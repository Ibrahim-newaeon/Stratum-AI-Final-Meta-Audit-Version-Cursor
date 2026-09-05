# =============================================================================
# Stratum AI - Platform CAPI Connectors
# =============================================================================
"""
Server-side Conversion API connectors for ad platforms.
Handles authentication, event formatting, and API calls.
Production-ready with retry logic, circuit breakers, and rate limiting.
"""

import asyncio
import hashlib
import statistics
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger

from .event_mapper import AIEventMapper
from .pii_hasher import PIIHasher

logger = get_logger(__name__)


# =============================================================================
# Circuit Breaker Implementation
# =============================================================================


class CircuitState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreaker:
    """
    Circuit breaker for API resilience.
    Prevents cascading failures by stopping requests to failing services.
    """

    failure_threshold: int = 5
    recovery_timeout: int = 60  # seconds
    half_open_max_calls: int = 3

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: Optional[float] = None
    half_open_calls: int = 0

    def can_execute(self) -> bool:
        """Check if request can proceed."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            # Check if recovery timeout has passed
            if (
                self.last_failure_time
                and (time.time() - self.last_failure_time) > self.recovery_timeout
            ):
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
                return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            return self.half_open_calls < self.half_open_max_calls

        return False

    def record_success(self):
        """Record successful call."""
        if self.state == CircuitState.HALF_OPEN:
            self.half_open_calls += 1
            if self.half_open_calls >= self.half_open_max_calls:
                # Service recovered
                self.state = CircuitState.CLOSED
                self.failure_count = 0
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0

    def record_failure(self):
        """Record failed call."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            # Failed during recovery test
            self.state = CircuitState.OPEN
        elif self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN


# =============================================================================
# Rate Limiter Implementation
# =============================================================================


@dataclass
class RateLimiter:
    """
    Token bucket rate limiter for API calls.
    """

    max_tokens: int = 100
    refill_rate: float = 10.0  # tokens per second

    tokens: float = field(default=100.0)
    last_refill: float = field(default_factory=time.time)

    def acquire(self, tokens: int = 1) -> bool:
        """Try to acquire tokens. Returns True if successful."""
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    async def wait_for_token(self, tokens: int = 1):
        """Wait until tokens are available."""
        while not self.acquire(tokens):
            await asyncio.sleep(0.1)

    def _refill(self):
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now


# =============================================================================
# Event Delivery Log for EMQ Measurement
# =============================================================================


@dataclass
class EventDeliveryLog:
    """
    One CAPI delivery attempt, as an in-request value.

    Passed to :meth:`ConnectorHealthMonitor.check_health` by whoever already
    holds the attempts it wants judged. It is deliberately *not* accumulated in
    a module-level list any more: that list lived in whichever process appended
    to it, so the Celery worker computing signal health always saw it empty, it
    was capped at 10000 entries, it was lost on restart, and - worst - it was
    not scoped by tenant at all. Delivery attempts are now persisted to
    ``capi_delivery_logs`` through
    :class:`app.services.capi.delivery_logger.DeliveryLogger`, which is the one
    source signal health and EMQ read from.
    """

    event_id: str
    platform: str
    event_name: str
    timestamp: datetime
    success: bool
    latency_ms: float
    error_message: Optional[str] = None
    request_id: Optional[str] = None
    retry_count: int = 0


class ConnectionStatus(str, Enum):
    """Platform connection status."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    PENDING = "pending"


@dataclass
class CAPIResponse:
    """Response from CAPI request."""

    success: bool
    events_received: int
    events_processed: int
    errors: list[dict[str, Any]]
    platform: str
    request_id: Optional[str] = None


@dataclass
class ConnectionResult:
    """Result of connection test."""

    status: ConnectionStatus
    platform: str
    message: str
    details: Optional[dict[str, Any]] = None


# The user_data keys that actually bear on Meta's event match quality. An
# event carrying only a client IP and a user agent is not "identified" in any
# sense Meta will match on, so it must not count toward identifier coverage:
# that coverage is 30% of the EMQ component, and counting IP-only events as
# fully identified let a tenant sending no email, phone or external id score
# 100% "hashed customer identifier coverage".
MATCH_QUALITY_IDENTIFIERS: frozenset[str] = frozenset(
    {
        "em",
        "email",
        "ph",
        "phone",
        "external_id",
        "fbc",
        "fbp",
        "lead_id",
        "subscription_id",
        "madid",
    }
)


def _match_quality_hash(user_data: dict[str, Any] | None) -> Optional[str]:
    """
    Hash the match-quality identifiers on an event, or return None.

    The stored column is documented as "SHA256 of user identifiers" and is read
    two ways: as presence (identifier coverage, which feeds EMQ) and as a
    correlation key. It used to hash the *field names* - ``sha256("em|ph")`` -
    so every event with the same key set produced a byte-identical digest that
    correlated nothing, under a comment claiming it hashed the identifiers. It
    was also non-null for any non-empty ``user_data``, including the IP/user
    agent pair the landing-page path sends on every event.

    Hashing the values keeps the column PII-free (SHA-256 is one-way) while
    making it mean what its name says, and returning None when no genuine
    identifier was sent keeps coverage honest.

    Args:
        user_data: The event's raw user data, or None.

    Returns:
        A hex digest, or None when the event carried no match-quality identifier.
    """
    if not user_data:
        return None
    identifiers = {
        str(key): user_data[key]
        for key in user_data
        if str(key).lower() in MATCH_QUALITY_IDENTIFIERS
        and user_data[key] not in (None, "")
    }
    if not identifiers:
        return None
    payload = "|".join(f"{key}={identifiers[key]}" for key in sorted(identifiers))
    return hashlib.sha256(payload.encode()).hexdigest()


class BaseCAPIConnector(ABC):
    """
    Base class for CAPI connectors.
    Includes retry logic, circuit breaker, and rate limiting.
    """

    PLATFORM_NAME: str = "base"
    MAX_RETRIES: int = 3
    RETRY_DELAYS: list[float] = [1.0, 2.0, 4.0]  # Exponential backoff

    def __init__(self, tenant_id: int | None = None):
        """
        Initialise the connector.

        Args:
            tenant_id: Tenant these events belong to. Delivery attempts can
                only be persisted - and therefore can only feed signal health -
                when it is known, because ``capi_delivery_logs`` is scoped by
                tenant and an unattributed row would be a cross-tenant read
                waiting to happen.
        """
        self.tenant_id = tenant_id
        self.hasher = PIIHasher()
        self.mapper = AIEventMapper()
        self._credentials: dict[str, str] = {}
        self._connected = False
        self._circuit_breaker = CircuitBreaker()
        self._rate_limiter = RateLimiter()

    async def _record_dropped(
        self,
        events: list[dict[str, Any]],
        response: "CAPIResponse",
        status_value: str,
    ) -> None:
        """
        Record events that were dropped before any send was attempted.

        Args:
            events: The events that never reached the platform.
            response: The failure response returned to the caller.
            status_value: ``DeliveryStatus`` value describing the drop.
        """
        for event in events:
            await self._record_delivery(
                event, response, latency_ms=0.0, retry=0, status_value=status_value
            )
        await self._flush_delivery_log()

    async def _flush_delivery_log(self) -> None:
        """
        Persist whatever the delivery logger has buffered.

        The logger batches writes and only flushes them from inside a later
        ``log_delivery`` call, so a quiet tenant's rows could sit in process
        memory indefinitely and be lost on restart. Flushing once per send
        keeps the batch (all of a burst goes in one transaction) while making
        the durability boundary the send itself.
        """
        try:
            from app.services.capi.delivery_logger import get_delivery_logger

            await get_delivery_logger().flush()
        except Exception as exc:  # noqa: BLE001 - never fail a send on logging
            logger.error(
                "capi_delivery_log_flush_failed",
                platform=self.PLATFORM_NAME,
                error=str(exc),
            )

    async def _record_delivery(
        self,
        event: dict[str, Any],
        response: "CAPIResponse",
        latency_ms: float,
        retry: int,
        status_value: str | None = None,
    ) -> None:
        """
        Persist one delivery attempt to ``capi_delivery_logs``.

        This is the only record of CAPI delivery that survives the process, and
        it is what ``app.services.signal_health`` reads to score EMQ and event
        loss. Failing to write it must never fail the send, so errors are
        logged and swallowed.

        Args:
            event: The event payload that was sent.
            response: The platform's response for this attempt.
            latency_ms: How long the attempt took.
            retry: Zero-based retry index of this attempt.
            status_value: Optional ``DeliveryStatus`` value overriding the
                success/failure derived from ``response`` - used for events
                dropped before a send was attempted.
        """
        if self.tenant_id is None:
            # Without a tenant the attempt cannot be attributed, and an
            # unattributed row would either leak across tenants or silently
            # inflate somebody's score. Say so rather than inventing an owner.
            logger.warning(
                "capi_delivery_not_recorded_without_tenant",
                platform=self.PLATFORM_NAME,
                event_name=event.get("event_name", "unknown"),
            )
            return

        try:
            from app.services.capi.delivery_logger import (
                DeliveryStatus,
                get_delivery_logger,
            )

            user_data = event.get("user_data") or {}
            await get_delivery_logger().log_delivery(
                tenant_id=self.tenant_id,
                platform=self.PLATFORM_NAME,
                event_name=event.get("event_name", "unknown"),
                status=(
                    DeliveryStatus(status_value)
                    if status_value is not None
                    else (
                        DeliveryStatus.SUCCESS
                        if response.success
                        else DeliveryStatus.FAILED
                    )
                ),
                latency_ms=latency_ms,
                event_id=event.get("event_id"),
                retry_count=retry,
                error_message=(
                    response.errors[0].get("message") if response.errors else None
                ),
                request_id=response.request_id,
                # SHA-256 of the match-quality identifiers actually sent, so
                # coverage can be measured and identical identifier sets
                # correlated, without storing any PII.
                user_data_hash=_match_quality_hash(user_data),
            )
        except Exception as exc:  # noqa: BLE001 - never fail a send on logging
            logger.error(
                "capi_delivery_log_failed",
                platform=self.PLATFORM_NAME,
                error=str(exc),
            )

    @abstractmethod
    async def connect(self, credentials: dict[str, str]) -> ConnectionResult:
        """Establish connection with platform credentials."""
        pass

    @abstractmethod
    async def _send_events_impl(self, events: list[dict[str, Any]]) -> CAPIResponse:
        """Internal implementation of send_events. Override in subclasses."""
        pass

    @abstractmethod
    async def test_connection(self) -> ConnectionResult:
        """Test the current connection."""
        pass

    async def send_events(self, events: list[dict[str, Any]]) -> CAPIResponse:
        """
        Send conversion events with retry logic, circuit breaker, and rate limiting.
        """
        if not self._connected:
            response = CAPIResponse(
                success=False,
                events_received=len(events),
                events_processed=0,
                errors=[{"message": "Not connected"}],
                platform=self.PLATFORM_NAME,
            )
            # A dropped event is a lost conversion, not a non-event. Returning
            # here without a row used to make these invisible to signal health,
            # which computes event loss as a share of *recorded* attempts - so a
            # disconnected connector reported 0% loss.
            await self._record_dropped(events, response, "failed")
            return response

        # Check circuit breaker
        if not self._circuit_breaker.can_execute():
            response = CAPIResponse(
                success=False,
                events_received=len(events),
                events_processed=0,
                errors=[{"message": "Circuit breaker open - service temporarily unavailable"}],
                platform=self.PLATFORM_NAME,
            )
            # Same reasoning, and worse in practice: with a 60s recovery
            # timeout a sustained outage recorded five failed batches and then
            # nothing at all, so the measured success rate recovered while the
            # platform was still rejecting everything.
            await self._record_dropped(events, response, "circuit_open")
            return response

        # Rate limiting
        await self._rate_limiter.wait_for_token(len(events))

        # Retry logic with exponential backoff
        last_error = None
        recorded_any = False
        for retry in range(self.MAX_RETRIES):
            start_time = time.time()
            try:
                response = await self._send_events_impl(events)

                # Persist the delivery attempt. capi_delivery_logs is the one
                # event-delivery source signal health reads, so this is what
                # makes EMQ and event loss measurable rather than guessed.
                latency_ms = (time.time() - start_time) * 1000
                for event in events:
                    await self._record_delivery(event, response, latency_ms, retry)
                recorded_any = True

                if response.success:
                    self._circuit_breaker.record_success()
                    # Persist immediately. The logger batches, and the batch is
                    # only flushed from inside a *later* log_delivery call, so
                    # without this the tail of every burst sat in process memory
                    # until more traffic arrived - or was lost on restart. That
                    # gap read as "no CAPI events were delivered", pushing a
                    # low-volume tenant into insufficient_data and BLOCK.
                    await self._flush_delivery_log()
                    return response
                else:
                    last_error = response.errors
                    # Don't retry on client errors (4xx)
                    if any(
                        "invalid" in str(e).lower() or "missing" in str(e).lower()
                        for e in response.errors
                    ):
                        break

            except Exception as e:
                last_error = [{"message": str(e)}]
                latency_ms = (time.time() - start_time) * 1000
                logger.warning(
                    f"{self.PLATFORM_NAME} CAPI retry {retry + 1}/{self.MAX_RETRIES}: {e}"
                )

            # Wait before retry (exponential backoff)
            if retry < self.MAX_RETRIES - 1:
                await asyncio.sleep(self.RETRY_DELAYS[retry])

        # All retries failed
        self._circuit_breaker.record_failure()
        # Retries that raised never reached _record_delivery, so nothing was
        # written for them; record the exhausted attempt so the loss is visible.
        response = CAPIResponse(
            success=False,
            events_received=len(events),
            events_processed=0,
            errors=last_error or [{"message": "Unknown error after retries"}],
            platform=self.PLATFORM_NAME,
        )
        if not recorded_any:
            await self._record_dropped(events, response, "failed")
        await self._flush_delivery_log()
        return response

    def format_user_data(self, user_data: dict[str, Any]) -> dict[str, Any]:
        """Format and hash user data for the platform."""
        return self.hasher.hash_data(user_data)

    def map_event(self, event_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Map custom event to platform event."""
        mapping = self.mapper.map_event(event_name, params)
        return {
            "event_name": mapping.platform_events.get(self.PLATFORM_NAME),
            "parameters": mapping.parameters,
        }

    def get_circuit_state(self) -> dict[str, Any]:
        """Get current circuit breaker state."""
        return {
            "state": self._circuit_breaker.state.value,
            "failure_count": self._circuit_breaker.failure_count,
            "last_failure": self._circuit_breaker.last_failure_time,
        }


class MetaCAPIConnector(BaseCAPIConnector):
    """
    Meta (Facebook) Conversion API connector.

    Required credentials:
    - pixel_id: Facebook Pixel ID
    - access_token: System user access token
    """

    PLATFORM_NAME = "meta"
    BASE_URL = "https://graph.facebook.com"
    # Sent with the validation event so Meta routes it to Events Manager's
    # "Test events" tab instead of recording it as real traffic.
    TEST_EVENT_CODE = "STRATUM_CONNECTION_TEST"

    @property
    def API_VERSION(self) -> str:
        """Graph API version, from config so one knob moves every Meta caller."""
        return settings.meta_graph_api_version

    def __init__(self, tenant_id: int | None = None):
        """Initialise the Meta connector for one tenant."""
        super().__init__(tenant_id=tenant_id)
        self.pixel_id: Optional[str] = None
        self.access_token: Optional[str] = None

    async def connect(self, credentials: dict[str, str]) -> ConnectionResult:
        """Connect to Meta CAPI with credentials."""
        self.pixel_id = credentials.get("pixel_id")
        self.access_token = credentials.get("access_token")

        if not self.pixel_id or not self.access_token:
            return ConnectionResult(
                status=ConnectionStatus.ERROR,
                platform=self.PLATFORM_NAME,
                message="Missing pixel_id or access_token",
            )

        # Test the connection
        return await self.test_connection()

    async def test_connection(self) -> ConnectionResult:
        """Test connection to Meta CAPI."""
        if not self.pixel_id or not self.access_token:
            return ConnectionResult(
                status=ConnectionStatus.DISCONNECTED,
                platform=self.PLATFORM_NAME,
                message="Not configured",
            )

        try:
            async with httpx.AsyncClient() as client:
                # 1) Reading the pixel node needs ads_read/ads_management on the
                #    pixel. Tokens generated in Events Manager for the Conversions
                #    API often only carry event-sending permission, so a
                #    "Missing Permission" here is not a failure by itself.
                url = f"{self.BASE_URL}/{self.API_VERSION}/{self.pixel_id}"
                response = await client.get(
                    url,
                    params={"access_token": self.access_token, "fields": "id,name"},
                    timeout=10.0,
                )
                if response.status_code == 200:
                    data = response.json()
                    self._connected = True
                    return ConnectionResult(
                        status=ConnectionStatus.CONNECTED,
                        platform=self.PLATFORM_NAME,
                        message="Successfully connected to Meta CAPI",
                        details={"pixel_name": data.get("name")},
                    )

                read_error = response.json().get("error", {})
                if read_error.get("code") == 190:  # invalid/expired token: no point retrying
                    return ConnectionResult(
                        status=ConnectionStatus.ERROR,
                        platform=self.PLATFORM_NAME,
                        message=read_error.get("message", "Invalid access token"),
                    )

                # 2) Validate the way the token will actually be used: send one
                #    event flagged with a test_event_code (Meta's recommended check).
                events_url = f"{self.BASE_URL}/{self.API_VERSION}/{self.pixel_id}/events"
                probe = await client.post(
                    events_url,
                    json={
                        "data": [
                            {
                                "event_name": "PageView",
                                "event_time": int(datetime.now(UTC).timestamp()),
                                "action_source": "website",
                                "event_source_url": "https://stratumai.app/connection-test",
                                "user_data": {"client_user_agent": "StratumAI-ConnectionTest/1.0"},
                            }
                        ],
                        "test_event_code": self.TEST_EVENT_CODE,
                        "access_token": self.access_token,
                    },
                    timeout=15.0,
                )
                if probe.status_code == 200:
                    self._connected = True
                    return ConnectionResult(
                        status=ConnectionStatus.CONNECTED,
                        platform=self.PLATFORM_NAME,
                        message=(
                            "Connected to Meta CAPI (event-sending token; pixel details "
                            "need ads_read on the pixel)"
                        ),
                        details={"test_event_code": self.TEST_EVENT_CODE},
                    )

                probe_error = probe.json().get("error", {})
                message = probe_error.get("message") or read_error.get("message") or "Connection failed"
                if "permission" in message.lower():
                    message += (
                        " — generate the token in Events Manager > Data sources > your pixel > "
                        "Settings > Conversions API (or use a System User with the pixel assigned "
                        "and the ads_management permission)."
                    )
                return ConnectionResult(
                    status=ConnectionStatus.ERROR,
                    platform=self.PLATFORM_NAME,
                    message=message,
                )

        except Exception as e:
            logger.error(f"Meta CAPI connection error: {e}")
            return ConnectionResult(
                status=ConnectionStatus.ERROR,
                platform=self.PLATFORM_NAME,
                message=str(e),
            )

    async def _send_events_impl(self, events: list[dict[str, Any]]) -> CAPIResponse:
        """Send conversion events to Meta CAPI."""
        # Format events for Meta CAPI
        formatted_events = []
        for event in events:
            formatted = self._format_event(event)
            formatted_events.append(formatted)

        try:
            async with httpx.AsyncClient() as client:
                url = f"{self.BASE_URL}/{self.API_VERSION}/{self.pixel_id}/events"
                payload = {
                    "data": formatted_events,
                    "access_token": self.access_token,
                }

                response = await client.post(url, json=payload, timeout=30.0)
                result = response.json()

                if response.status_code == 200:
                    return CAPIResponse(
                        success=True,
                        events_received=result.get("events_received", len(events)),
                        events_processed=result.get("events_received", len(events)),
                        errors=[],
                        platform=self.PLATFORM_NAME,
                        request_id=result.get("fbtrace_id"),
                    )
                else:
                    error = result.get("error", {})
                    return CAPIResponse(
                        success=False,
                        events_received=len(events),
                        events_processed=0,
                        errors=[error],
                        platform=self.PLATFORM_NAME,
                    )

        except Exception as e:
            logger.error(f"Meta CAPI send error: {e}")
            return CAPIResponse(
                success=False,
                events_received=len(events),
                events_processed=0,
                errors=[{"message": str(e)}],
                platform=self.PLATFORM_NAME,
            )

    def _format_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Format event for Meta CAPI."""
        # Map event name
        event_name = event.get("event_name", event.get("name", "CustomEvent"))
        mapping = self.mapper.map_event(event_name, event.get("parameters", {}))

        # Hash user data
        user_data = event.get("user_data", {})
        hashed_user_data = self.format_user_data(user_data)

        # Build Meta event format
        formatted = {
            "event_name": mapping.platform_events.get("meta", event_name),
            "event_time": event.get("event_time", int(time.time())),
            "action_source": event.get("action_source", "website"),
            "user_data": hashed_user_data,
        }

        # Add custom data (parameters)
        if mapping.parameters:
            formatted["custom_data"] = mapping.parameters

        # Add event_source_url if available
        if event.get("event_source_url"):
            formatted["event_source_url"] = event["event_source_url"]

        # Add event_id for deduplication
        if event.get("event_id"):
            formatted["event_id"] = event["event_id"]

        return formatted


class WhatsAppCAPIConnector(BaseCAPIConnector):
    """
    WhatsApp Business Cloud API connector.

    Required credentials:
    - phone_number_id: WhatsApp Business phone number ID
    - business_account_id: WhatsApp Business Account ID
    - access_token: Meta Graph API access token
    - webhook_verify_token: Custom token for webhook verification
    """

    PLATFORM_NAME = "whatsapp"
    BASE_URL = "https://graph.facebook.com"

    @property
    def API_VERSION(self) -> str:
        """Graph API version, from config (WhatsApp Cloud shares Graph versioning)."""
        return settings.meta_graph_api_version

    def __init__(self, tenant_id: int | None = None):
        """Initialise the WhatsApp connector for one tenant."""
        super().__init__(tenant_id=tenant_id)
        self.phone_number_id: Optional[str] = None
        self.business_account_id: Optional[str] = None
        self.access_token: Optional[str] = None
        self.webhook_verify_token: Optional[str] = None

    async def connect(self, credentials: dict[str, str]) -> ConnectionResult:
        """Connect to WhatsApp Business Cloud API."""
        self.phone_number_id = credentials.get("phone_number_id")
        self.business_account_id = credentials.get("business_account_id")
        self.access_token = credentials.get("access_token")
        self.webhook_verify_token = credentials.get("webhook_verify_token")

        if not self.phone_number_id or not self.access_token:
            return ConnectionResult(
                status=ConnectionStatus.ERROR,
                platform=self.PLATFORM_NAME,
                message="Missing phone_number_id or access_token",
            )

        return await self.test_connection()

    async def test_connection(self) -> ConnectionResult:
        """Test connection to WhatsApp Business API."""
        if not self.phone_number_id or not self.access_token:
            return ConnectionResult(
                status=ConnectionStatus.DISCONNECTED,
                platform=self.PLATFORM_NAME,
                message="Not configured",
            )

        try:
            async with httpx.AsyncClient() as client:
                # Test with a phone number info request
                url = f"{self.BASE_URL}/{self.API_VERSION}/{self.phone_number_id}"
                response = await client.get(
                    url,
                    params={"access_token": self.access_token},
                    timeout=10.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    self._connected = True
                    return ConnectionResult(
                        status=ConnectionStatus.CONNECTED,
                        platform=self.PLATFORM_NAME,
                        message="Successfully connected to WhatsApp Business API",
                        details={
                            "display_phone_number": data.get("display_phone_number"),
                            "verified_name": data.get("verified_name"),
                        },
                    )
                else:
                    error = response.json().get("error", {})
                    return ConnectionResult(
                        status=ConnectionStatus.ERROR,
                        platform=self.PLATFORM_NAME,
                        message=error.get("message", "Connection failed"),
                    )

        except Exception as e:
            logger.error(f"WhatsApp API connection error: {e}")
            return ConnectionResult(
                status=ConnectionStatus.ERROR,
                platform=self.PLATFORM_NAME,
                message=str(e),
            )

    async def _send_events_impl(self, events: list[dict[str, Any]]) -> CAPIResponse:
        """
        Send messages/events via WhatsApp Business API.

        Note: WhatsApp is primarily a messaging platform, not a conversion tracking platform.
        This connector allows sending template messages for marketing/notification purposes.
        """
        processed = 0
        errors = []

        for event in events:
            try:
                result = await self._send_message(event)
                if result:
                    processed += 1
                else:
                    errors.append({"message": f"Failed to send event: {event.get('event_name')}"})
            except Exception as e:
                errors.append({"message": str(e)})

        return CAPIResponse(
            success=len(errors) == 0,
            events_received=len(events),
            events_processed=processed,
            errors=errors,
            platform=self.PLATFORM_NAME,
        )

    async def _send_message(self, event: dict[str, Any]) -> bool:
        """Send a WhatsApp message based on event data."""
        user_data = event.get("user_data", {})
        phone = user_data.get("phone") or user_data.get("ph")

        if not phone:
            return False

        # Clean phone number (remove + and spaces)
        phone = phone.replace("+", "").replace(" ", "").replace("-", "")

        try:
            async with httpx.AsyncClient() as client:
                url = f"{self.BASE_URL}/{self.API_VERSION}/{self.phone_number_id}/messages"
                headers = {
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                }

                # Build message payload
                template_name = event.get("parameters", {}).get("template_name", "hello_world")
                language = event.get("parameters", {}).get("language", "en")

                payload = {
                    "messaging_product": "whatsapp",
                    "to": phone,
                    "type": "template",
                    "template": {
                        "name": template_name,
                        "language": {"code": language},
                    },
                }

                # Add template components if provided
                components = event.get("parameters", {}).get("components")
                if components:
                    payload["template"]["components"] = components

                response = await client.post(url, json=payload, headers=headers, timeout=30.0)

                if response.status_code == 200:
                    return True
                else:
                    logger.error(f"WhatsApp send error: {response.json()}")
                    return False

        except Exception as e:
            logger.error(f"WhatsApp message send error: {e}")
            return False

    def _format_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Format event for WhatsApp API."""
        return {
            "event_name": event.get("event_name"),
            "event_time": event.get("event_time", int(time.time())),
            "user_data": event.get("user_data", {}),
            "parameters": event.get("parameters", {}),
        }


# =============================================================================
# Advanced Platform Connector Features (P0 Enhancement)
# =============================================================================


@dataclass
class ConnectorHealthStatus:
    """Health status for a platform connector."""

    platform: str
    status: str  # healthy, degraded, unhealthy, unknown
    last_check: datetime
    # None when no delivery attempts were supplied to judge: unknown, which is
    # not the same as 100%.
    success_rate_1h: float | None
    avg_latency_ms: float
    circuit_state: str
    error_count_1h: int
    events_processed_1h: int
    issues: list[str]


@dataclass
class BatchOptimizationResult:
    """Result of batch optimization."""

    original_batch_size: int
    optimized_batch_size: int
    estimated_throughput_improvement: float
    recommendation: str


class ConnectorHealthMonitor:
    """
    Monitors health of all platform connectors.

    Provides:
    - Real-time health status for each connector
    - Automated alerting on degradation
    - Historical health tracking
    - Recommendations for improvement
    """

    def __init__(self):
        self._health_history: dict[str, list[tuple[datetime, ConnectorHealthStatus]]] = {}
        self._alert_callbacks: list[Any] = []
        self._last_alerts: dict[str, datetime] = {}
        self._alert_cooldown = timedelta(minutes=15)

    def check_health(
        self,
        connector: BaseCAPIConnector,
        delivery_logs: Optional[list[EventDeliveryLog]] = None,
    ) -> ConnectorHealthStatus:
        """
        Judge a connector from the delivery attempts the caller supplies.

        Args:
            connector: The connector whose circuit state to read.
            delivery_logs: Delivery attempts to judge. With none supplied the
                success rate is reported as unknown rather than as perfect.

        Returns:
            The health status, whose ``status`` may be ``unknown``.
        """
        platform = connector.PLATFORM_NAME
        now = datetime.now(UTC)

        # Delivery attempts must be supplied by the caller. They used to be
        # read from a process-local list here, which meant a fresh process saw
        # none and scored a flat 100% success - "no evidence" reported as
        # "perfect". With nothing to judge, this now reports unknown.
        delivery_logs = list(delivery_logs) if delivery_logs is not None else []

        # Calculate success rate
        total = len(delivery_logs)
        successes = sum(1 for log in delivery_logs if log.success)
        success_rate = (successes / total * 100) if total > 0 else None

        # Calculate average latency
        latencies = [log.latency_ms for log in delivery_logs if log.latency_ms > 0]
        avg_latency = statistics.mean(latencies) if latencies else 0.0

        # Count errors
        error_count = sum(1 for log in delivery_logs if not log.success)

        # Get circuit state
        circuit_info = connector.get_circuit_state()
        circuit_state = circuit_info.get("state", "unknown")

        # Identify issues
        issues = []
        status = "healthy"

        if circuit_state == "open":
            issues.append("Circuit breaker is OPEN - service unavailable")
            status = "unhealthy"
        elif circuit_state == "half_open":
            issues.append("Circuit breaker is recovering")
            status = "degraded"

        if success_rate is None:
            issues.append("No delivery attempts to judge - health is unknown")
            if status == "healthy":
                status = "unknown"
        else:
            if success_rate < 90:
                issues.append(f"Low success rate: {success_rate:.1f}%")
                status = "degraded" if status != "unhealthy" else status

            if success_rate < 70:
                status = "unhealthy"

        if avg_latency > 5000:
            issues.append(f"High latency: {avg_latency:.0f}ms")
            status = "degraded" if status == "healthy" else status

        if avg_latency > 10000:
            status = "unhealthy"

        if error_count > 50:
            issues.append(f"High error count: {error_count} errors in 1h")

        health = ConnectorHealthStatus(
            platform=platform,
            status=status,
            last_check=now,
            success_rate_1h=round(success_rate, 1) if success_rate is not None else None,
            avg_latency_ms=round(avg_latency, 1),
            circuit_state=circuit_state,
            error_count_1h=error_count,
            events_processed_1h=total,
            issues=issues,
        )

        # Record history
        if platform not in self._health_history:
            self._health_history[platform] = []
        self._health_history[platform].append((now, health))

        # Keep only last 24 hours
        cutoff = now - timedelta(hours=24)
        self._health_history[platform] = [
            (t, h) for t, h in self._health_history[platform] if t > cutoff
        ]

        # Trigger alerts if needed
        self._check_alerts(health)

        return health

    def _check_alerts(self, health: ConnectorHealthStatus):
        """Check if alerts should be triggered."""
        if health.status == "unhealthy":
            last_alert = self._last_alerts.get(health.platform)
            if last_alert is None or (datetime.now(UTC) - last_alert) > self._alert_cooldown:
                self._last_alerts[health.platform] = datetime.now(UTC)
                for callback in self._alert_callbacks:
                    try:
                        callback(health)
                    except Exception as e:
                        logger.error(f"Alert callback error: {e}")

    def register_alert_callback(self, callback: Any):
        """Register a callback for health alerts."""
        self._alert_callbacks.append(callback)

    def get_health_summary(
        self,
        connectors: list[BaseCAPIConnector],
    ) -> dict[str, Any]:
        """Get health summary for all connectors."""
        statuses = {}
        overall_status = "healthy"

        for connector in connectors:
            health = self.check_health(connector)
            statuses[connector.PLATFORM_NAME] = {
                "status": health.status,
                "success_rate": health.success_rate_1h,
                "avg_latency_ms": health.avg_latency_ms,
                "issues": health.issues,
            }

            if health.status == "unhealthy":
                overall_status = "unhealthy"
            elif health.status == "degraded" and overall_status == "healthy":
                overall_status = "degraded"

        return {
            "overall_status": overall_status,
            "checked_at": datetime.now(UTC).isoformat(),
            "platforms": statuses,
        }

    def get_health_history(
        self,
        platform: str,
        hours: int = 24,
    ) -> list[dict[str, Any]]:
        """Get health history for a platform."""
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        history = self._health_history.get(platform, [])

        return [
            {
                "timestamp": t.isoformat(),
                "status": h.status,
                "success_rate": h.success_rate_1h,
                "avg_latency_ms": h.avg_latency_ms,
                "error_count": h.error_count_1h,
            }
            for t, h in history
            if t > cutoff
        ]


class BatchOptimizer:
    """
    Optimizes event batching for maximum throughput.

    Analyzes historical performance to recommend optimal batch sizes
    for each platform.
    """

    # Platform-specific default batch sizes
    DEFAULT_BATCH_SIZES = {
        "meta": 1000,
        "whatsapp": 100,
    }

    # Platform rate limits (events per minute)
    RATE_LIMITS = {
        "meta": 1000,
        "whatsapp": 80,
    }

    def __init__(self):
        self._performance_history: dict[str, list[dict[str, Any]]] = {}

    def record_batch_performance(
        self,
        platform: str,
        batch_size: int,
        success: bool,
        latency_ms: float,
        events_processed: int,
    ):
        """Record batch performance for optimization."""
        if platform not in self._performance_history:
            self._performance_history[platform] = []

        self._performance_history[platform].append(
            {
                "timestamp": datetime.now(UTC),
                "batch_size": batch_size,
                "success": success,
                "latency_ms": latency_ms,
                "events_processed": events_processed,
                "throughput": events_processed / (latency_ms / 1000) if latency_ms > 0 else 0,
            }
        )

        # Keep last 1000 records
        if len(self._performance_history[platform]) > 1000:
            self._performance_history[platform] = self._performance_history[platform][-1000:]

    def optimize_batch_size(
        self,
        platform: str,
        current_batch_size: int,
    ) -> BatchOptimizationResult:
        """Calculate optimal batch size for a platform."""
        history = self._performance_history.get(platform, [])

        if len(history) < 10:
            # Not enough data - use defaults
            default = self.DEFAULT_BATCH_SIZES.get(platform, 500)
            return BatchOptimizationResult(
                original_batch_size=current_batch_size,
                optimized_batch_size=default,
                estimated_throughput_improvement=0,
                recommendation=f"Using default batch size for {platform}. Collect more data for optimization.",
            )

        # Group by batch size ranges and calculate average throughput
        size_performance: dict[int, list[float]] = {}
        for record in history:
            if record["success"]:
                size_bucket = (record["batch_size"] // 100) * 100
                if size_bucket not in size_performance:
                    size_performance[size_bucket] = []
                size_performance[size_bucket].append(record["throughput"])

        if not size_performance:
            default = self.DEFAULT_BATCH_SIZES.get(platform, 500)
            return BatchOptimizationResult(
                original_batch_size=current_batch_size,
                optimized_batch_size=default,
                estimated_throughput_improvement=0,
                recommendation="No successful batches recorded. Check connector health.",
            )

        # Find optimal batch size
        avg_throughputs = {
            size: statistics.mean(throughputs)
            for size, throughputs in size_performance.items()
            if len(throughputs) >= 3
        }

        if not avg_throughputs:
            default = self.DEFAULT_BATCH_SIZES.get(platform, 500)
            return BatchOptimizationResult(
                original_batch_size=current_batch_size,
                optimized_batch_size=default,
                estimated_throughput_improvement=0,
                recommendation="Insufficient data per batch size. Continue collecting metrics.",
            )

        optimal_size = max(avg_throughputs, key=avg_throughputs.get)
        optimal_throughput = avg_throughputs[optimal_size]

        # Calculate improvement
        current_bucket = (current_batch_size // 100) * 100
        current_throughput = avg_throughputs.get(current_bucket, optimal_throughput * 0.8)
        improvement = (
            ((optimal_throughput - current_throughput) / current_throughput * 100)
            if current_throughput > 0
            else 0
        )

        # Apply rate limit constraints
        rate_limit = self.RATE_LIMITS.get(platform, 500)
        max_batch = int(rate_limit * 0.8)  # 80% of rate limit
        optimal_size = min(optimal_size, max_batch)

        recommendation = f"Optimal batch size: {optimal_size} events. "
        if optimal_size > current_batch_size:
            recommendation += "Increase batch size for better throughput."
        elif optimal_size < current_batch_size:
            recommendation += "Decrease batch size to reduce errors."
        else:
            recommendation += "Current batch size is optimal."

        return BatchOptimizationResult(
            original_batch_size=current_batch_size,
            optimized_batch_size=optimal_size,
            estimated_throughput_improvement=round(improvement, 1),
            recommendation=recommendation,
        )


class ConnectionPool:
    """
    Manages a pool of HTTP connections for high-throughput scenarios.

    Features:
    - Pre-warmed connections
    - Connection reuse
    - Automatic reconnection
    - Load balancing across connections
    """

    def __init__(self, max_connections: int = 10, timeout: float = 30.0):
        self.max_connections = max_connections
        self.timeout = timeout
        self._clients: dict[str, list[httpx.AsyncClient]] = {}
        self._client_index: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def get_client(self, platform: str) -> httpx.AsyncClient:
        """Get a client from the pool for a platform."""
        async with self._lock:
            if platform not in self._clients:
                self._clients[platform] = []
                self._client_index[platform] = 0

                # Create initial connections
                for _ in range(min(3, self.max_connections)):
                    client = httpx.AsyncClient(
                        timeout=self.timeout,
                        limits=httpx.Limits(max_connections=100),
                    )
                    self._clients[platform].append(client)

            # Round-robin selection
            clients = self._clients[platform]
            index = self._client_index[platform]
            client = clients[index % len(clients)]
            self._client_index[platform] = (index + 1) % len(clients)

            return client

    async def scale_up(self, platform: str):
        """Add more connections to the pool."""
        async with self._lock:
            if platform not in self._clients:
                self._clients[platform] = []

            if len(self._clients[platform]) < self.max_connections:
                client = httpx.AsyncClient(
                    timeout=self.timeout,
                    limits=httpx.Limits(max_connections=100),
                )
                self._clients[platform].append(client)
                logger.info(
                    f"Scaled up connection pool for {platform} to {len(self._clients[platform])}"
                )

    async def scale_down(self, platform: str):
        """Remove connections from the pool."""
        async with self._lock:
            if platform in self._clients and len(self._clients[platform]) > 1:
                client = self._clients[platform].pop()
                await client.aclose()
                logger.info(
                    f"Scaled down connection pool for {platform} to {len(self._clients[platform])}"
                )

    async def close_all(self):
        """Close all connections in the pool."""
        async with self._lock:
            for platform, clients in self._clients.items():
                for client in clients:
                    await client.aclose()
            self._clients.clear()
            self._client_index.clear()

    def get_pool_stats(self) -> dict[str, Any]:
        """Get statistics about the connection pool."""
        return {
            platform: {
                "connections": len(clients),
                "max_connections": self.max_connections,
            }
            for platform, clients in self._clients.items()
        }


class EventDeduplicator:
    """
    Deduplicates events before sending to prevent duplicate conversions.

    Uses event_id and user data to identify duplicates.
    """

    def __init__(self, ttl_hours: int = 24, max_size: int = 100000):
        self._seen_events: dict[str, datetime] = {}
        self._ttl = timedelta(hours=ttl_hours)
        self._max_size = max_size
        self._lock = threading.RLock()

    def is_duplicate(self, event: dict[str, Any]) -> bool:
        """Check if an event is a duplicate."""
        event_key = self._generate_key(event)

        with self._lock:
            self._cleanup_expired()

            if event_key in self._seen_events:
                return True

            self._seen_events[event_key] = datetime.now(UTC)
            return False

    def _generate_key(self, event: dict[str, Any]) -> str:
        """Generate a unique key for an event."""
        # Use event_id if available
        event_id = event.get("event_id")
        if event_id:
            return f"{event.get('platform', '')}:{event_id}"

        # Generate from event content
        user_data = event.get("user_data", {})
        components = [
            event.get("event_name", ""),
            str(event.get("event_time", "")),
            user_data.get("em", ""),
            user_data.get("ph", ""),
            str(event.get("parameters", {}).get("value", "")),
        ]

        content = "|".join(components)
        return hashlib.md5(content.encode()).hexdigest()  # noqa: S324 - deduplication fingerprint

    def _cleanup_expired(self):
        """Remove expired entries."""
        now = datetime.now(UTC)
        expired = [
            key for key, timestamp in self._seen_events.items() if now - timestamp > self._ttl
        ]

        for key in expired:
            del self._seen_events[key]

        # If still too large, remove oldest
        if len(self._seen_events) > self._max_size:
            sorted_events = sorted(self._seen_events.items(), key=lambda x: x[1])
            to_remove = len(self._seen_events) - self._max_size
            for key, _ in sorted_events[:to_remove]:
                del self._seen_events[key]

    def get_stats(self) -> dict[str, Any]:
        """Get deduplication statistics."""
        with self._lock:
            return {
                "tracked_events": len(self._seen_events),
                "max_size": self._max_size,
                "ttl_hours": self._ttl.total_seconds() / 3600,
            }


# Singleton instances for P0 enhancements
connector_health_monitor = ConnectorHealthMonitor()
batch_optimizer = BatchOptimizer()
connection_pool = ConnectionPool()
event_deduplicator = EventDeduplicator()
