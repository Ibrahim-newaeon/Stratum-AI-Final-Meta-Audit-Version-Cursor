# =============================================================================
# Stratum AI - Paddle Billing Service
# =============================================================================
"""
Paddle Billing integration for subscription billing and payment processing.

Paddle is the merchant of record: checkout happens client-side (Paddle.js
overlay), Paddle owns dunning/retries, and this backend only

- maps subscription tiers <-> Paddle price ids,
- talks to the Paddle REST API through a thin, typed ``httpx.AsyncClient``
  wrapper (``PaddleClient``) - no ``paddle_billing`` SDK dependency,
- verifies webhook notification signatures (``Paddle-Signature`` header),
- converts Paddle entity payloads into plain dataclasses, and
- keeps the ``Tenant`` billing columns in sync with Paddle.

Nothing in this module performs network I/O or raises at import time: the
API client is created lazily and only fails (``PaddleNotConfiguredError``)
when the first request is attempted without ``PADDLE_API_KEY``.

Paddle API reference used here (``Paddle-Version: 1``):
- ``GET/POST /customers``, ``GET /customers/{id}``
- ``GET /subscriptions``, ``GET/PATCH /subscriptions/{id}``,
  ``POST /subscriptions/{id}/cancel``
- ``GET /transactions``, ``GET /transactions/{id}``,
  ``GET /transactions/{id}/invoice``
- ``POST /customers/{id}/portal-sessions``
- ``GET /prices``, ``POST /prices``, ``GET /products``, ``POST /products``
  (catalogue bootstrap, ``scripts_paddle_bootstrap.py``)
- ``GET /notification-settings``, ``POST /notification-settings``
  (webhook destination bootstrap; entities carry ``endpoint_secret_key``,
  which is never logged)
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

import httpx
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.tiers import SubscriptionTier

logger = get_logger(__name__)


# =============================================================================
# Constants
# =============================================================================

PADDLE_API_VERSION = "1"
PADDLE_SANDBOX_API_BASE_URL = "https://sandbox-api.paddle.com"
PADDLE_PRODUCTION_API_BASE_URL = "https://api.paddle.com"
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_SIGNATURE_TOLERANCE_SECONDS = 300
MAX_TRANSACTIONS_PER_PAGE = 100
# Upper bound on pages followed by ``PaddleClient._get_all`` (guards against a
# broken ``meta.pagination.next`` loop; the catalogue is a handful of entities).
MAX_LIST_PAGES = 50

# Transaction statuses that represent a real charge (shown as "invoices").
TRANSACTION_STATUSES_FOR_HISTORY = ("completed", "billed", "past_due", "paid")


# =============================================================================
# Exceptions
# =============================================================================


class PaddleError(Exception):
    """
    Error returned by (or while talking to) the Paddle API.

    Attributes:
        status_code: HTTP status of the Paddle response (502 when unreachable).
        code: Paddle ``error.code`` (e.g. ``customer_already_exists``) or None.
        detail: Human-readable error detail.
    """

    def __init__(self, status_code: int, code: Optional[str], detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail

    def __str__(self) -> str:
        """Render as ``<status> <code>: <detail>`` for logs."""
        code = f" {self.code}" if self.code else ""
        return f"Paddle API error {self.status_code}{code}: {self.detail}"


class PaddleNotConfiguredError(PaddleError):
    """Raised when a Paddle operation is attempted without ``PADDLE_API_KEY``."""

    def __init__(
        self,
        detail: str = "Paddle Billing is not configured (PADDLE_API_KEY is missing)",
    ) -> None:
        super().__init__(status_code=503, code="paddle_not_configured", detail=detail)


class PaddleSignatureError(Exception):
    """Raised when a webhook ``Paddle-Signature`` header is missing, malformed, stale or wrong."""


# =============================================================================
# Data Models
# =============================================================================


class SubscriptionState(str, Enum):
    """Paddle subscription status values persisted in ``Tenant.subscription_status``."""

    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    PAUSED = "paused"
    CANCELED = "canceled"


@dataclass
class PaddleCustomer:
    """Paddle customer entity (``ctm_...``)."""

    id: str
    email: str
    name: Optional[str]
    custom_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaddleSubscription:
    """Paddle subscription entity (``sub_...``) reduced to what the platform needs."""

    id: str
    customer_id: str
    status: SubscriptionState
    tier: Optional[SubscriptionTier]
    price_id: Optional[str]
    current_period_start: Optional[datetime]
    current_period_end: Optional[datetime]
    next_billed_at: Optional[datetime]
    cancel_at_period_end: bool
    canceled_at: Optional[datetime]
    paused_at: Optional[datetime]
    trial_end: Optional[datetime]
    custom_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaddleTransaction:
    """Paddle transaction entity (``txn_...``); amounts are integer minor units."""

    id: str
    invoice_number: Optional[str]
    status: str
    subscription_id: Optional[str]
    customer_id: Optional[str]
    total_minor: int
    currency_code: str
    billed_at: Optional[datetime]
    created_at: Optional[datetime]
    custom_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PortalSession:
    """Customer portal links returned by ``POST /customers/{id}/portal-sessions``."""

    overview_url: str
    cancel_url: Optional[str]
    update_payment_method_url: Optional[str]


# =============================================================================
# Configuration helpers
# =============================================================================


def is_configured() -> bool:
    """Return True when a Paddle API key is present (API calls are possible)."""
    return bool(settings.paddle_api_key)


def _api_base_url(environment: Optional[str]) -> str:
    """Resolve the REST base URL for a Paddle environment (``sandbox`` | ``production``)."""
    return (
        PADDLE_PRODUCTION_API_BASE_URL
        if (environment or "sandbox") == "production"
        else PADDLE_SANDBOX_API_BASE_URL
    )


def get_price_id_for_tier(tier: SubscriptionTier) -> Optional[str]:
    """Return the configured Paddle price id (``pri_...``) for a subscription tier."""
    mapping: dict[SubscriptionTier, Optional[str]] = {
        SubscriptionTier.STARTER: settings.paddle_starter_price_id,
        SubscriptionTier.PROFESSIONAL: settings.paddle_professional_price_id,
        SubscriptionTier.ENTERPRISE: settings.paddle_enterprise_price_id,
    }
    return mapping.get(tier) or None


def get_tier_for_price_id(price_id: Optional[str]) -> Optional[SubscriptionTier]:
    """
    Return the subscription tier configured for a Paddle price id.

    Returns None for an empty/unknown price id - callers must never default
    to a tier, otherwise an unknown price would silently change a tenant plan.
    """
    if not price_id:
        return None
    for tier in (
        SubscriptionTier.STARTER,
        SubscriptionTier.PROFESSIONAL,
        SubscriptionTier.ENTERPRISE,
    ):
        configured = get_price_id_for_tier(tier)
        if configured and configured == price_id:
            return tier
    return None


# =============================================================================
# Payload parsing
# =============================================================================


def parse_paddle_datetime(value: Optional[str]) -> Optional[datetime]:
    """
    Parse a Paddle ISO-8601 timestamp (``2024-05-01T12:00:00.123456Z``) to a tz-aware UTC datetime.

    Returns None for ``None`` / empty strings, and (with a warning) for values
    that cannot be parsed so a malformed field never breaks webhook handling.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        logger.warning("paddle_datetime_unparseable", value=value)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _as_dict(value: Any) -> dict[str, Any]:
    """Return ``value`` when it is a dict, otherwise an empty dict."""
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    """Return ``value`` when it is a list, otherwise an empty list."""
    return value if isinstance(value, list) else []


def pagination_after_cursor(next_url: Any) -> Optional[str]:
    """
    Extract the ``after`` cursor from a ``meta.pagination.next`` URL.

    Paddle returns ``next`` as a full URL such as
    ``https://api.paddle.com/prices?after=pri_01...``; the cursor is the value
    of its ``after`` query parameter. Returns None for anything else.
    """
    if not isinstance(next_url, str) or not next_url:
        return None
    values = parse_qs(urlsplit(next_url).query).get("after")
    return values[0] if values and values[0] else None


def _to_minor_units(value: Any) -> int:
    """Convert a Paddle minor-unit amount (string like ``"4900"`` or int) to int."""
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            logger.warning("paddle_amount_unparseable", value=value)
            return 0


def _subscription_state(raw_status: Any, subscription_id: Optional[str]) -> SubscriptionState:
    """Map a Paddle status string to ``SubscriptionState`` (unknown -> ACTIVE with a warning)."""
    try:
        return SubscriptionState(str(raw_status))
    except ValueError:
        logger.warning(
            "paddle_unknown_subscription_status",
            subscription_id=subscription_id,
            status=raw_status,
        )
        return SubscriptionState.ACTIVE


def customer_from_payload(data: dict[str, Any]) -> PaddleCustomer:
    """Build a ``PaddleCustomer`` from a Paddle customer entity payload."""
    return PaddleCustomer(
        id=str(data.get("id", "")),
        email=str(data.get("email") or ""),
        name=data.get("name"),
        custom_data=_as_dict(data.get("custom_data")),
    )


def subscription_from_payload(data: dict[str, Any]) -> PaddleSubscription:
    """
    Build a ``PaddleSubscription`` from a Paddle subscription entity payload.

    Works for both API responses (``data``) and webhook ``data`` objects.
    The tier is resolved from ``items[0].price.id``; an unknown price id yields
    ``tier=None`` (never a default tier).
    """
    subscription_id = str(data.get("id", ""))
    items = _as_list(data.get("items"))
    first_item = _as_dict(items[0]) if items else {}
    price = _as_dict(first_item.get("price"))
    price_id: Optional[str] = price.get("id") or first_item.get("price_id") or None
    billing_period = _as_dict(data.get("current_billing_period"))
    scheduled_change = _as_dict(data.get("scheduled_change"))
    trial_dates = _as_dict(first_item.get("trial_dates"))

    return PaddleSubscription(
        id=subscription_id,
        customer_id=str(data.get("customer_id") or ""),
        status=_subscription_state(data.get("status"), subscription_id),
        tier=get_tier_for_price_id(price_id),
        price_id=price_id,
        current_period_start=parse_paddle_datetime(billing_period.get("starts_at")),
        current_period_end=parse_paddle_datetime(billing_period.get("ends_at")),
        next_billed_at=parse_paddle_datetime(data.get("next_billed_at")),
        cancel_at_period_end=scheduled_change.get("action") == "cancel",
        canceled_at=parse_paddle_datetime(data.get("canceled_at")),
        paused_at=parse_paddle_datetime(data.get("paused_at")),
        trial_end=parse_paddle_datetime(trial_dates.get("ends_at")),
        custom_data=_as_dict(data.get("custom_data")),
    )


def transaction_from_payload(data: dict[str, Any]) -> PaddleTransaction:
    """Build a ``PaddleTransaction`` from a Paddle transaction entity payload."""
    details = _as_dict(data.get("details"))
    totals = _as_dict(details.get("totals"))
    total_raw = totals.get("grand_total")
    if total_raw in (None, ""):
        total_raw = totals.get("total")

    return PaddleTransaction(
        id=str(data.get("id", "")),
        invoice_number=data.get("invoice_number") or None,
        status=str(data.get("status") or ""),
        subscription_id=data.get("subscription_id") or None,
        customer_id=data.get("customer_id") or None,
        total_minor=_to_minor_units(total_raw),
        currency_code=str(data.get("currency_code") or totals.get("currency_code") or ""),
        billed_at=parse_paddle_datetime(data.get("billed_at")),
        created_at=parse_paddle_datetime(data.get("created_at")),
        custom_data=_as_dict(data.get("custom_data")),
    )


# =============================================================================
# Webhook signature verification
# =============================================================================


def parse_signature_header(header: str) -> tuple[int, list[str]]:
    """
    Parse a ``Paddle-Signature`` header value (``ts=<unix>;h1=<hex>[;h1=<hex>...]``).

    Returns:
        ``(timestamp, [h1, ...])``

    Raises:
        PaddleSignatureError: when the header is empty, malformed, has no
            integer ``ts`` or carries no ``h1`` signature.
    """
    if not header or not header.strip():
        raise PaddleSignatureError("Missing Paddle-Signature header")

    timestamp: Optional[int] = None
    signatures: list[str] = []
    for raw_part in header.split(";"):
        part = raw_part.strip()
        if not part:
            continue
        key, sep, value = part.partition("=")
        key = key.strip()
        value = value.strip()
        if not sep or not key or not value:
            raise PaddleSignatureError("Malformed Paddle-Signature header")
        if key == "ts":
            if timestamp is not None:
                raise PaddleSignatureError("Malformed Paddle-Signature header (duplicate ts)")
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise PaddleSignatureError("Malformed Paddle-Signature timestamp") from exc
        elif key == "h1":
            signatures.append(value.lower())
        # Unknown keys (future signature versions) are ignored.

    if timestamp is None:
        raise PaddleSignatureError("Paddle-Signature header has no ts")
    if not signatures:
        raise PaddleSignatureError("Paddle-Signature header has no h1 signature")
    return timestamp, signatures


def compute_webhook_signature(raw_body: bytes, timestamp: int, secret: str) -> str:
    """Return the hex HMAC-SHA256 of ``"<ts>:<raw body>"`` keyed with the endpoint secret."""
    signed_payload = f"{timestamp}:".encode() + raw_body
    return hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()


def verify_webhook_signature(
    raw_body: bytes,
    signature_header: Optional[str],
    secret: str,
    tolerance_seconds: int = DEFAULT_SIGNATURE_TOLERANCE_SECONDS,
    now: Optional[int] = None,
) -> None:
    """
    Verify a Paddle webhook notification against its ``Paddle-Signature`` header.

    Must be called on the *raw* request body before any JSON parsing.

    Args:
        raw_body: Exact request body bytes.
        signature_header: Value of the ``Paddle-Signature`` header (may be None).
        secret: Notification endpoint secret key (``pdl_ntfset_...``).
        tolerance_seconds: Maximum accepted clock skew / replay window.
        now: Unix time to compare against (defaults to the current time).

    Raises:
        PaddleSignatureError: missing/malformed header, stale timestamp or
            no ``h1`` value matching the computed HMAC.
    """
    if not secret:
        raise PaddleSignatureError("Webhook secret is not configured")
    if signature_header is None:
        raise PaddleSignatureError("Missing Paddle-Signature header")

    timestamp, signatures = parse_signature_header(signature_header)

    current = int(time.time()) if now is None else int(now)
    if abs(current - timestamp) > tolerance_seconds:
        raise PaddleSignatureError("Paddle-Signature timestamp is outside the tolerance window")

    expected = compute_webhook_signature(raw_body, timestamp, secret)
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise PaddleSignatureError("Paddle-Signature does not match the request body")


# =============================================================================
# API Client
# =============================================================================


class PaddleClient:
    """
    Thin async client for the Paddle Billing REST API.

    Requests are authenticated with ``Authorization: Bearer <API key>`` and
    pinned to ``Paddle-Version: 1``. Every non-2xx response is raised as a
    ``PaddleError`` carrying Paddle's ``error.code`` / ``error.detail``.

    Args:
        api_key: Paddle API key (defaults to ``settings.paddle_api_key``).
        environment: ``sandbox`` | ``production`` (defaults to settings).
        timeout: Per-request timeout in seconds.
        transport: Optional ``httpx`` transport (tests use ``httpx.MockTransport``).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        environment: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._api_key: Optional[str] = api_key if api_key is not None else settings.paddle_api_key
        self._environment: str = environment or settings.paddle_environment
        self._timeout: float = timeout
        self._transport: Optional[httpx.AsyncBaseTransport] = transport
        self._http: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------ setup

    @property
    def environment(self) -> str:
        """Paddle environment this client talks to (``sandbox`` | ``production``)."""
        return self._environment

    @property
    def base_url(self) -> str:
        """REST base URL derived from the environment."""
        return _api_base_url(self._environment)

    def is_configured(self) -> bool:
        """Return True when this client has an API key."""
        return bool(self._api_key)

    def _get_http(self) -> httpx.AsyncClient:
        """Lazily build the underlying ``httpx.AsyncClient``; raise when not configured."""
        if not self._api_key:
            raise PaddleNotConfiguredError()
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Paddle-Version": PADDLE_API_VERSION,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=self._timeout,
                transport=self._transport,
            )
        return self._http

    async def aclose(self) -> None:
        """Close the underlying HTTP client (safe to call when never opened)."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # --------------------------------------------------------------- requests

    @staticmethod
    def _error_from_response(response: httpx.Response) -> PaddleError:
        """Translate a non-2xx Paddle response into a ``PaddleError``."""
        code: Optional[str] = None
        detail = f"Paddle request failed with HTTP {response.status_code}"
        try:
            body = response.json()
        except ValueError:
            body = None
        error = _as_dict(_as_dict(body).get("error"))
        if error:
            code = error.get("code") or None
            detail = str(error.get("detail") or error.get("type") or detail)
            nested = _as_list(error.get("errors"))
            if nested:
                fields = "; ".join(
                    f"{_as_dict(item).get('field', '?')}: {_as_dict(item).get('message', '')}"
                    for item in nested
                )
                detail = f"{detail} ({fields})"
        return PaddleError(status_code=response.status_code, code=code, detail=detail)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Send one request and return the parsed JSON body (``{"data": ..., "meta": ...}``).

        Raises:
            PaddleNotConfiguredError: no API key.
            PaddleError: transport failure (502) or non-2xx Paddle response.
        """
        http = self._get_http()
        try:
            response = await http.request(method, path, params=params, json=json_body)
        except httpx.HTTPError as exc:
            logger.error("paddle_request_failed", method=method, path=path, error=str(exc))
            raise PaddleError(
                status_code=502, code="paddle_unreachable", detail=f"Paddle API unreachable: {exc}"
            ) from exc

        if response.status_code < 200 or response.status_code >= 300:
            error = self._error_from_response(response)
            logger.warning(
                "paddle_api_error",
                method=method,
                path=path,
                status_code=error.status_code,
                code=error.code,
                detail=error.detail,
            )
            raise error

        if not response.content:
            return {}
        try:
            body = response.json()
        except ValueError as exc:
            raise PaddleError(
                status_code=502, code="paddle_invalid_response", detail="Paddle returned non-JSON body"
            ) from exc
        return _as_dict(body)

    async def _get_data(
        self, path: str, params: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """GET ``path`` and return the ``data`` object."""
        body = await self._request("GET", path, params=params)
        return _as_dict(body.get("data"))

    async def _get_list(
        self, path: str, params: Optional[dict[str, Any]] = None
    ) -> list[dict[str, Any]]:
        """GET ``path`` and return the ``data`` list (first page only)."""
        body = await self._request("GET", path, params=params)
        return [_as_dict(item) for item in _as_list(body.get("data"))]

    async def _get_all(
        self, path: str, params: Optional[dict[str, Any]] = None
    ) -> list[dict[str, Any]]:
        """
        GET ``path`` and return the ``data`` items of every page.

        Follows ``meta.pagination`` (``has_more`` + the ``after`` cursor taken
        from ``next``) for at most ``MAX_LIST_PAGES`` pages and stops on a
        repeated cursor so a malformed ``next`` can never loop forever.
        """
        query: dict[str, Any] = dict(params or {})
        items: list[dict[str, Any]] = []
        seen_cursors: set[str] = set()
        for _ in range(MAX_LIST_PAGES):
            body = await self._request("GET", path, params=query)
            items.extend(_as_dict(item) for item in _as_list(body.get("data")))
            pagination = _as_dict(_as_dict(body.get("meta")).get("pagination"))
            if not pagination.get("has_more"):
                break
            after = pagination_after_cursor(pagination.get("next"))
            if not after or after in seen_cursors:
                break
            seen_cursors.add(after)
            query["after"] = after
        return items

    # -------------------------------------------------------------- customers

    async def create_customer(
        self, email: str, name: Optional[str], tenant_id: int
    ) -> PaddleCustomer:
        """
        Create a Paddle customer for a tenant (``POST /customers``).

        Paddle enforces unique emails; on ``409 customer_already_exists`` the
        existing active customer with that email is looked up and returned.
        """
        payload: dict[str, Any] = {
            "email": email,
            "custom_data": {"tenant_id": str(tenant_id)},
        }
        if name:
            payload["name"] = name

        try:
            body = await self._request("POST", "/customers", json_body=payload)
        except PaddleError as exc:
            if exc.status_code != 409 or exc.code != "customer_already_exists":
                raise
            logger.info("paddle_customer_exists_lookup", tenant_id=tenant_id)
            existing = await self._get_list(
                "/customers", params={"email": email, "status": "active"}
            )
            if not existing:
                raise
            customer = customer_from_payload(existing[0])
            logger.info(
                "paddle_customer_reused", customer_id=customer.id, tenant_id=tenant_id
            )
            return customer

        customer = customer_from_payload(_as_dict(body.get("data")))
        logger.info("paddle_customer_created", customer_id=customer.id, tenant_id=tenant_id)
        return customer

    async def get_customer(self, customer_id: str) -> Optional[PaddleCustomer]:
        """Fetch a customer (``GET /customers/{id}``); returns None on 404."""
        try:
            data = await self._get_data(f"/customers/{customer_id}")
        except PaddleError as exc:
            if exc.status_code == 404:
                return None
            raise
        return customer_from_payload(data)

    # ---------------------------------------------------------- subscriptions

    async def get_subscription(self, subscription_id: str) -> PaddleSubscription:
        """Fetch a subscription (``GET /subscriptions/{id}``)."""
        data = await self._get_data(f"/subscriptions/{subscription_id}")
        return subscription_from_payload(data)

    async def list_subscriptions(
        self, customer_id: str, statuses: Optional[list[str]] = None
    ) -> list[PaddleSubscription]:
        """
        List a customer's subscriptions (``GET /subscriptions``), newest first.

        Args:
            customer_id: Paddle customer id (``ctm_...``).
            statuses: Optional status filter (e.g. ``["active", "trialing", "past_due"]``).
        """
        params: dict[str, Any] = {"customer_id": customer_id, "per_page": 10}
        if statuses:
            params["status"] = ",".join(statuses)
        items = await self._get_list("/subscriptions", params=params)
        return [subscription_from_payload(item) for item in items]

    async def update_subscription_tier(
        self,
        subscription_id: str,
        new_tier: SubscriptionTier,
        prorate: bool = True,
    ) -> PaddleSubscription:
        """
        Swap the subscription's single item to the price of ``new_tier`` (``PATCH /subscriptions/{id}``).

        Args:
            subscription_id: Paddle subscription id (``sub_...``).
            new_tier: Target tier; must have a configured price id.
            prorate: ``prorated_immediately`` when True, else ``full_next_billing_period``.

        Raises:
            PaddleNotConfiguredError: when the tier has no configured price id.
        """
        price_id = get_price_id_for_tier(new_tier)
        if not price_id:
            raise PaddleNotConfiguredError(
                f"No Paddle price id configured for tier '{new_tier.value}'"
            )
        payload = {
            "items": [{"price_id": price_id, "quantity": 1}],
            "proration_billing_mode": (
                "prorated_immediately" if prorate else "full_next_billing_period"
            ),
            "custom_data": {"tier": new_tier.value},
        }
        body = await self._request(
            "PATCH", f"/subscriptions/{subscription_id}", json_body=payload
        )
        subscription = subscription_from_payload(_as_dict(body.get("data")))
        logger.info(
            "paddle_subscription_tier_updated",
            subscription_id=subscription_id,
            new_tier=new_tier.value,
            prorate=prorate,
        )
        return subscription

    async def cancel_subscription(
        self, subscription_id: str, at_period_end: bool = True
    ) -> PaddleSubscription:
        """
        Cancel a subscription (``POST /subscriptions/{id}/cancel``).

        Args:
            at_period_end: ``next_billing_period`` (scheduled change) when True,
                ``immediately`` otherwise.
        """
        payload = {
            "effective_from": "next_billing_period" if at_period_end else "immediately"
        }
        body = await self._request(
            "POST", f"/subscriptions/{subscription_id}/cancel", json_body=payload
        )
        subscription = subscription_from_payload(_as_dict(body.get("data")))
        logger.info(
            "paddle_subscription_canceled",
            subscription_id=subscription_id,
            at_period_end=at_period_end,
        )
        return subscription

    async def reactivate_subscription(self, subscription_id: str) -> PaddleSubscription:
        """Remove a scheduled cancellation (``PATCH /subscriptions/{id}`` with ``scheduled_change: null``)."""
        body = await self._request(
            "PATCH", f"/subscriptions/{subscription_id}", json_body={"scheduled_change": None}
        )
        subscription = subscription_from_payload(_as_dict(body.get("data")))
        logger.info("paddle_subscription_reactivated", subscription_id=subscription_id)
        return subscription

    # ----------------------------------------------------------- transactions

    async def list_transactions(
        self,
        customer_id: str,
        subscription_id: Optional[str] = None,
        limit: int = 10,
    ) -> list[PaddleTransaction]:
        """
        List billed/paid transactions for a customer (``GET /transactions``), newest first.

        Args:
            customer_id: Paddle customer id.
            subscription_id: Optional subscription filter.
            limit: Page size (capped at 100).
        """
        per_page = max(1, min(int(limit), MAX_TRANSACTIONS_PER_PAGE))
        params: dict[str, Any] = {
            "customer_id": customer_id,
            "status": ",".join(TRANSACTION_STATUSES_FOR_HISTORY),
            "per_page": per_page,
            "order_by": "created_at[DESC]",
        }
        if subscription_id:
            params["subscription_id"] = subscription_id
        items = await self._get_list("/transactions", params=params)
        return [transaction_from_payload(item) for item in items]

    async def get_transaction(self, transaction_id: str) -> PaddleTransaction:
        """Fetch a transaction (``GET /transactions/{id}``)."""
        data = await self._get_data(f"/transactions/{transaction_id}")
        return transaction_from_payload(data)

    async def get_transaction_invoice_url(self, transaction_id: str) -> str:
        """Return the invoice PDF URL for a transaction (``GET /transactions/{id}/invoice``)."""
        data = await self._get_data(f"/transactions/{transaction_id}/invoice")
        url = data.get("url")
        if not url:
            raise PaddleError(
                status_code=502,
                code="invoice_url_missing",
                detail=f"Paddle returned no invoice url for transaction {transaction_id}",
            )
        return str(url)

    # ----------------------------------------------------------------- portal

    async def create_portal_session(
        self, customer_id: str, subscription_ids: Optional[list[str]] = None
    ) -> PortalSession:
        """
        Create a customer portal session (``POST /customers/{id}/portal-sessions``).

        Returns the general overview URL plus, for the first requested
        subscription, the deep links to cancel / update the payment method.
        """
        payload: dict[str, Any] = {}
        if subscription_ids:
            payload["subscription_ids"] = list(subscription_ids)
        body = await self._request(
            "POST", f"/customers/{customer_id}/portal-sessions", json_body=payload
        )
        data = _as_dict(body.get("data"))
        urls = _as_dict(data.get("urls"))
        general = _as_dict(urls.get("general"))
        subscriptions = [_as_dict(item) for item in _as_list(urls.get("subscriptions"))]
        first = subscriptions[0] if subscriptions else {}
        overview_url = str(general.get("overview") or "")
        if not overview_url:
            raise PaddleError(
                status_code=502,
                code="portal_url_missing",
                detail="Paddle portal session response has no overview url",
            )
        logger.info("paddle_portal_session_created", customer_id=customer_id)
        return PortalSession(
            overview_url=overview_url,
            cancel_url=first.get("cancel_subscription") or None,
            update_payment_method_url=first.get("update_subscription_payment_method") or None,
        )

    # -------------------------------------------------------------- catalogue

    async def list_products(self, status: str = "active") -> list[dict[str, Any]]:
        """List products (``GET /products?status=...``) as raw Paddle entities, every page."""
        return await self._get_all("/products", params={"status": status})

    async def create_product(
        self,
        name: str,
        tax_category: str,
        description: Optional[str],
        custom_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Create a product (``POST /products``) and return the raw entity (``pro_...``).

        Args:
            name: Product name shown at checkout and on invoices.
            tax_category: Paddle tax category (``standard``, ``saas``, ...).
            description: Optional description (max 2048 characters).
            custom_data: Your own key-value data stored on the product.
        """
        payload: dict[str, Any] = {"name": name, "tax_category": tax_category}
        if description:
            payload["description"] = description
        if custom_data:
            payload["custom_data"] = custom_data
        body = await self._request("POST", "/products", json_body=payload)
        product = _as_dict(body.get("data"))
        logger.info("paddle_product_created", product_id=product.get("id"), name=name)
        return product

    async def list_prices(
        self, product_id: Optional[str] = None, status: str = "active"
    ) -> list[dict[str, Any]]:
        """
        List prices (``GET /prices?status=...[&product_id=...]``) as raw Paddle entities, every page.

        Args:
            product_id: Optional product filter (``pro_...``).
            status: ``active`` (default) or ``archived``.
        """
        params: dict[str, Any] = {"status": status}
        if product_id:
            params["product_id"] = product_id
        return await self._get_all("/prices", params=params)

    async def create_price(
        self,
        product_id: str,
        description: str,
        amount_minor: str,
        currency_code: str,
        billing_interval: str,
        billing_frequency: int,
        quantity_minimum: int = 1,
        quantity_maximum: int = 1,
        custom_data: Optional[dict[str, Any]] = None,
        name: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Create a recurring price (``POST /prices``) and return the raw entity (``pri_...``).

        Args:
            product_id: Owning product (``pro_...``).
            description: Internal description (not shown to customers).
            amount_minor: Amount in the currency's minor unit as a string (``"49900"`` = 499.00).
            currency_code: ISO 4217 code (``USD``).
            billing_interval: ``day`` | ``week`` | ``month`` | ``year``.
            billing_frequency: Number of intervals per billing cycle (>= 1).
            quantity_minimum: Minimum quantity per checkout.
            quantity_maximum: Maximum quantity per checkout.
            custom_data: Your own key-value data stored on the price.
            name: Optional customer-facing name.
        """
        payload: dict[str, Any] = {
            "product_id": product_id,
            "description": description,
            "unit_price": {"amount": amount_minor, "currency_code": currency_code},
            "billing_cycle": {"interval": billing_interval, "frequency": billing_frequency},
            "quantity": {"minimum": quantity_minimum, "maximum": quantity_maximum},
        }
        if name:
            payload["name"] = name
        if custom_data:
            payload["custom_data"] = custom_data
        body = await self._request("POST", "/prices", json_body=payload)
        price = _as_dict(body.get("data"))
        logger.info(
            "paddle_price_created",
            price_id=price.get("id"),
            product_id=product_id,
            amount_minor=amount_minor,
            currency_code=currency_code,
        )
        return price

    # -------------------------------------------------- notification settings

    async def list_notification_settings(self) -> list[dict[str, Any]]:
        """
        List notification destinations (``GET /notification-settings``), every page.

        Every entity carries ``endpoint_secret_key`` (``pdl_ntfset_...``);
        callers must never log, print or persist it outside a secret store.
        """
        return await self._get_all("/notification-settings")

    async def create_notification_setting(
        self,
        description: str,
        destination: str,
        subscribed_events: list[str],
        destination_type: str = "url",
        include_sensitive_fields: bool = False,
        api_version: int = int(PADDLE_API_VERSION),
    ) -> dict[str, Any]:
        """
        Create a notification destination (``POST /notification-settings``).

        Returns the raw entity (``ntfset_...``) including ``endpoint_secret_key``,
        the secret that verifies ``Paddle-Signature`` on webhook deliveries.

        Args:
            description: Label shown in the Paddle dashboard.
            destination: Webhook URL (``type='url'``) or email address (``type='email'``).
            subscribed_events: Event type names (``subscription.created``, ...).
            destination_type: ``url`` (default) or ``email``.
            include_sensitive_fields: Whether Paddle sends potentially sensitive fields.
            api_version: Paddle API version the event payloads conform to.
        """
        payload: dict[str, Any] = {
            "description": description,
            "destination": destination,
            "type": destination_type,
            "subscribed_events": list(subscribed_events),
            "include_sensitive_fields": include_sensitive_fields,
            "api_version": api_version,
        }
        body = await self._request("POST", "/notification-settings", json_body=payload)
        setting = _as_dict(body.get("data"))
        logger.info(
            "paddle_notification_setting_created",
            notification_setting_id=setting.get("id"),
            destination=destination,
        )
        return setting


# =============================================================================
# Client singleton
# =============================================================================

_client: Optional[PaddleClient] = None


def get_paddle_client() -> PaddleClient:
    """
    Return the process-wide ``PaddleClient`` (created lazily).

    Construction never raises; ``PaddleNotConfiguredError`` surfaces on the
    first request when ``PADDLE_API_KEY`` is missing.
    """
    global _client
    if _client is None:
        _client = PaddleClient()
    return _client


def reset_paddle_client() -> None:
    """Drop the cached client so the next ``get_paddle_client()`` re-reads settings."""
    global _client
    _client = None


# =============================================================================
# Tenant sync helpers
# =============================================================================


def compute_tenant_billing_updates(
    subscription: PaddleSubscription, now: Optional[datetime] = None
) -> dict[str, Any]:
    """
    Compute the ``Tenant`` column updates implied by a Paddle subscription.

    Rules (see shared contract "Tenant sync"):
    - always: ``paddle_subscription_id``, ``subscription_status``, ``current_period_end``
    - ACTIVE / TRIALING / PAST_DUE: ``plan = tier`` (only when the price id is
      known; unknown -> warning, plan untouched) and
      ``plan_expires_at = current_period_end``
    - PAUSED: plan untouched, ``plan_expires_at = current_period_end`` if present
    - CANCELED: ``plan = 'free'``, ``plan_expires_at = canceled_at or current_period_end or now``

    ``paddle_customer_id`` is handled by the caller (set only when empty).
    """
    updates: dict[str, Any] = {
        "paddle_subscription_id": subscription.id,
        "subscription_status": subscription.status.value,
        "current_period_end": subscription.current_period_end,
    }

    if subscription.status in (
        SubscriptionState.ACTIVE,
        SubscriptionState.TRIALING,
        SubscriptionState.PAST_DUE,
    ):
        if subscription.tier is not None:
            updates["plan"] = subscription.tier.value
        else:
            logger.warning(
                "paddle_unknown_price_id_plan_untouched",
                subscription_id=subscription.id,
                price_id=subscription.price_id,
            )
        updates["plan_expires_at"] = subscription.current_period_end
    elif subscription.status == SubscriptionState.PAUSED:
        if subscription.current_period_end is not None:
            updates["plan_expires_at"] = subscription.current_period_end
    elif subscription.status == SubscriptionState.CANCELED:
        updates["plan"] = "free"
        updates["plan_expires_at"] = (
            subscription.canceled_at
            or subscription.current_period_end
            or (now or datetime.now(UTC))
        )

    return updates


async def sync_tenant_subscription(
    db: AsyncSession, tenant_id: int, subscription: PaddleSubscription
) -> None:
    """
    Persist a Paddle subscription onto the tenant row (plan, expiry, Paddle ids).

    Links ``paddle_customer_id`` only when the tenant has none yet. Commits.
    """
    from app.base_models import Tenant

    values = compute_tenant_billing_updates(subscription)
    if subscription.customer_id:
        # Keep an existing link; fill it in only when NULL / empty.
        values["paddle_customer_id"] = func.coalesce(
            func.nullif(Tenant.paddle_customer_id, ""), subscription.customer_id
        )

    await db.execute(update(Tenant).where(Tenant.id == tenant_id).values(**values))
    await db.commit()

    expires = values.get("plan_expires_at")
    logger.info(
        "tenant_subscription_synced",
        tenant_id=tenant_id,
        subscription_id=subscription.id,
        status=subscription.status.value,
        plan=values.get("plan"),
        plan_expires_at=expires.isoformat() if isinstance(expires, datetime) else None,
    )


async def sync_tenant_paddle_customer(
    db: AsyncSession, tenant_id: int, customer_id: str
) -> None:
    """Store the Paddle customer id (``ctm_...``) on the tenant. Commits."""
    from app.base_models import Tenant

    await db.execute(
        update(Tenant).where(Tenant.id == tenant_id).values(paddle_customer_id=customer_id)
    )
    await db.commit()

    logger.info("tenant_paddle_customer_synced", tenant_id=tenant_id, customer_id=customer_id)


async def clear_tenant_subscription(
    db: AsyncSession, tenant_id: int, ended_at: Optional[datetime]
) -> None:
    """
    Downgrade a tenant to the free plan after a subscription ended. Commits.

    Sets ``plan='free'``, ``plan_expires_at = ended_at or now`` and
    ``subscription_status='canceled'``; the Paddle ids are kept for audit.
    """
    from app.base_models import Tenant

    ended = ended_at or datetime.now(UTC)
    await db.execute(
        update(Tenant)
        .where(Tenant.id == tenant_id)
        .values(
            plan="free",
            plan_expires_at=ended,
            subscription_status=SubscriptionState.CANCELED.value,
        )
    )
    await db.commit()

    logger.info(
        "tenant_subscription_cleared", tenant_id=tenant_id, plan_expires_at=ended.isoformat()
    )
