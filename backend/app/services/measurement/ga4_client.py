# =============================================================================
# Stratum AI - GA4 Data API Client (read-only)
# =============================================================================
"""
Read-only Google Analytics 4 Data API client.

GA4 is a *measurement* integration: it provides an independent revenue and
conversion baseline for attribution variance, EMQ and the Trust Gate. It is
never an ad channel - Stratum AI only reads GA4 reports with a service
account limited to ``https://www.googleapis.com/auth/analytics.readonly``.

Google libraries are imported lazily so the module imports even when
``google-analytics-data`` is not installed; a ``GA4ClientError`` is raised at
call time instead.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# Read-only scope - the only scope Stratum ever requests for GA4.
GA4_READONLY_SCOPE: str = "https://www.googleapis.com/auth/analytics.readonly"

# GA4 Data API pagination limit (max 100k per request; 10k keeps responses small)
GA4_PAGE_LIMIT: int = 10_000

DAILY_DIMENSIONS: tuple[str, ...] = (
    "date",
    "sessionSource",
    "sessionMedium",
    "sessionCampaignName",
)
DAILY_METRICS: tuple[str, ...] = ("sessions", "keyEvents", "purchaseRevenue", "totalRevenue")
CONVERSION_DIMENSIONS: tuple[str, ...] = DAILY_DIMENSIONS + ("eventName",)
CONVERSION_METRICS: tuple[str, ...] = ("eventCount",)
TEST_METRICS: tuple[str, ...] = ("sessions", "keyEvents", "purchaseRevenue")

NOT_SET = "(not set)"


# =============================================================================
# Exceptions
# =============================================================================


class GA4ClientError(Exception):
    """Base error for GA4 Data API failures."""


class GA4NotConfiguredError(GA4ClientError):
    """Raised when GA4 is not configured for a tenant."""


class GA4AuthError(GA4ClientError):
    """Raised when the service account cannot authenticate or lacks access."""


# =============================================================================
# Data classes
# =============================================================================


@dataclass
class GA4DailyRow:
    """One GA4 row: date x source/medium/campaign with sessions/conversions/revenue."""

    date: date
    utm_source: str
    utm_medium: str
    utm_campaign: str
    sessions: int
    conversions: int
    revenue: float
    total_revenue: float
    currency: str | None = None


@dataclass
class GA4TestResult:
    """Result of a connection test against the GA4 property."""

    success: bool
    message: str
    property_id: str
    sessions_last_7d: int | None = None
    conversions_last_7d: int | None = None
    revenue_last_7d: float | None = None


# =============================================================================
# Helpers
# =============================================================================


def normalize_property_id(property_id: str) -> str:
    """Return the numeric GA4 property id (accepts ``properties/123`` or ``123``)."""
    value = (property_id or "").strip()
    if value.lower().startswith("properties/"):
        value = value[len("properties/") :]
    return value.strip()


def _to_int(value: Any) -> int:
    """Parse a GA4 metric value into an int (metric strings may be floats)."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    """Parse a GA4 metric value into a float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_ga4_date(value: str) -> date:
    """Parse a GA4 ``date`` dimension value (``YYYYMMDD``)."""
    value = (value or "").strip()
    if len(value) == 8 and value.isdigit():
        return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
    return date.fromisoformat(value)


def response_to_dicts(response: Any) -> list[dict[str, str]]:
    """
    Flatten a ``RunReportResponse``-like object into ``{header_name: value}`` dicts.

    Works with the real protobuf response and with simple fakes exposing
    ``dimension_headers``/``metric_headers`` (``.name``) and ``rows`` with
    ``dimension_values``/``metric_values`` (``.value``).
    """
    dim_names = [h.name for h in getattr(response, "dimension_headers", []) or []]
    metric_names = [h.name for h in getattr(response, "metric_headers", []) or []]
    rows: list[dict[str, str]] = []
    for row in getattr(response, "rows", []) or []:
        record: dict[str, str] = {}
        for name, dim in zip(dim_names, getattr(row, "dimension_values", []) or [], strict=False):
            record[name] = getattr(dim, "value", "") or ""
        for name, met in zip(metric_names, getattr(row, "metric_values", []) or [], strict=False):
            record[name] = getattr(met, "value", "") or ""
        rows.append(record)
    return rows


def _response_currency(response: Any) -> str | None:
    """Extract the property currency code from a response, when present."""
    metadata = getattr(response, "metadata", None)
    code = getattr(metadata, "currency_code", None) if metadata is not None else None
    return code or None


def translate_google_exception(exc: Exception) -> GA4ClientError:
    """
    Map a Google API / auth exception onto the GA4 error hierarchy with a
    human-readable message. Unknown exceptions become ``GA4ClientError``.
    """
    if isinstance(exc, GA4ClientError):
        return exc

    name = type(exc).__name__
    try:  # pragma: no cover - depends on optional package
        from google.api_core import exceptions as gexc
    except ImportError:  # pragma: no cover
        gexc = None

    if gexc is not None:
        if isinstance(exc, gexc.PermissionDenied):
            return GA4AuthError(
                "The service account does not have access to this GA4 property. "
                "Grant it the Viewer role on the property (Admin > Property access management)."
            )
        if isinstance(exc, gexc.Unauthenticated):
            return GA4AuthError(
                "GA4 authentication failed. Check that the service-account JSON is valid "
                "and the key has not been revoked."
            )
        if isinstance(exc, gexc.NotFound):
            return GA4ClientError(
                "GA4 property not found. Check the numeric property ID (Admin > Property settings)."
            )
        if isinstance(exc, gexc.InvalidArgument):
            return GA4ClientError(f"GA4 rejected the report request: {exc}")
        if isinstance(exc, gexc.ResourceExhausted):
            return GA4ClientError("GA4 Data API quota exhausted. Try again later.")
        if isinstance(exc, gexc.GoogleAPICallError):
            return GA4ClientError(f"GA4 Data API error ({name}): {exc}")

    try:  # pragma: no cover - depends on optional package
        from google.auth import exceptions as auth_exc

        if isinstance(exc, auth_exc.GoogleAuthError):
            return GA4AuthError(f"GA4 authentication failed: {exc}")
    except ImportError:  # pragma: no cover
        pass

    if isinstance(exc, ValueError):
        return GA4AuthError(f"Invalid GA4 service-account credentials: {exc}")

    return GA4ClientError(f"GA4 request failed ({name}): {exc}")


def _transient_exception_types() -> tuple[type[BaseException], ...]:
    """Google exceptions that are safe to retry with backoff."""
    try:
        from google.api_core import exceptions as gexc
    except ImportError:  # pragma: no cover
        return ()
    return (gexc.ResourceExhausted, gexc.ServiceUnavailable, gexc.DeadlineExceeded)


# =============================================================================
# Client
# =============================================================================


class GA4DataClient:
    """
    Thin async wrapper over ``BetaAnalyticsDataClient`` restricted to
    ``runReport`` with the read-only scope.

    Args:
        property_id: GA4 property id (``123456789`` or ``properties/123456789``)
        service_account_info: parsed service-account JSON (dict)
        timeout_seconds: per-request timeout
    """

    def __init__(
        self,
        property_id: str,
        service_account_info: dict[str, Any],
        timeout_seconds: float = 30.0,
    ) -> None:
        self.property_id: str = normalize_property_id(property_id)
        if not self.property_id:
            raise GA4NotConfiguredError("GA4 property ID is required")
        self._info: dict[str, Any] = dict(service_account_info or {})
        self.timeout_seconds: float = float(timeout_seconds)
        self._client: Any = None  # BetaAnalyticsDataClient, built lazily

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_service_account_json(
        cls,
        property_id: str,
        service_account_json: str,
        timeout_seconds: float = 30.0,
    ) -> GA4DataClient:
        """
        Build a client from the raw service-account JSON string.

        Raises:
            GA4AuthError: when the JSON is malformed or is not a service account key.
        """
        if not service_account_json or not service_account_json.strip():
            raise GA4AuthError("Service account JSON is empty")
        try:
            info = json.loads(service_account_json)
        except (TypeError, ValueError) as exc:
            raise GA4AuthError("Service account JSON is not valid JSON") from exc
        if not isinstance(info, dict):
            raise GA4AuthError("Service account JSON must be a JSON object")
        if info.get("type") != "service_account":
            raise GA4AuthError("Service account JSON must have type='service_account'")
        if not info.get("client_email"):
            raise GA4AuthError("Service account JSON is missing client_email")
        if not info.get("private_key"):
            raise GA4AuthError("Service account JSON is missing private_key")
        return cls(property_id, info, timeout_seconds=timeout_seconds)

    @property
    def service_account_email(self) -> str:
        """Service account e-mail (safe to display; never the private key)."""
        return str(self._info.get("client_email", "") or "")

    @property
    def property_path(self) -> str:
        """GA4 Data API resource name for the property."""
        return f"properties/{self.property_id}"

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Lazily build the synchronous ``BetaAnalyticsDataClient`` (read-only scope)."""
        if self._client is not None:
            return self._client
        try:
            from google.analytics.data_v1beta import BetaAnalyticsDataClient
            from google.oauth2.service_account import Credentials
        except ImportError as exc:
            raise GA4ClientError(
                "google-analytics-data not installed (pip install google-analytics-data google-auth)"
            ) from exc
        try:
            credentials = Credentials.from_service_account_info(
                self._info, scopes=[GA4_READONLY_SCOPE]
            )
        except Exception as exc:  # malformed key etc. -> GA4AuthError
            raise translate_google_exception(exc) from exc
        self._client = BetaAnalyticsDataClient(credentials=credentials)
        return self._client

    def _build_request(
        self,
        *,
        dimensions: Iterable[str],
        metrics: Iterable[str],
        start_date: str,
        end_date: str,
        limit: int,
        offset: int,
        event_names: list[str] | None = None,
    ) -> Any:
        """Build a ``RunReportRequest`` (google types imported lazily)."""
        try:
            from google.analytics.data_v1beta.types import (
                DateRange,
                Dimension,
                Filter,
                FilterExpression,
                Metric,
                RunReportRequest,
            )
        except ImportError as exc:
            raise GA4ClientError(
                "google-analytics-data not installed (pip install google-analytics-data google-auth)"
            ) from exc

        kwargs: dict[str, Any] = {
            "property": self.property_path,
            "dimensions": [Dimension(name=d) for d in dimensions],
            "metrics": [Metric(name=m) for m in metrics],
            "date_ranges": [DateRange(start_date=start_date, end_date=end_date)],
            "limit": limit,
            "offset": offset,
            "keep_empty_rows": False,
        }
        if event_names:
            kwargs["dimension_filter"] = FilterExpression(
                filter=Filter(
                    field_name="eventName",
                    in_list_filter=Filter.InListFilter(values=list(event_names)),
                )
            )
        return RunReportRequest(**kwargs)

    def _run_report_sync(
        self,
        *,
        dimensions: Iterable[str],
        metrics: Iterable[str],
        start_date: str,
        end_date: str,
        limit: int = GA4_PAGE_LIMIT,
        offset: int = 0,
        event_names: list[str] | None = None,
    ) -> Any:
        """Execute one ``runReport`` call with retries on transient errors."""
        from tenacity import (
            Retrying,
            retry_if_exception_type,
            stop_after_attempt,
            wait_exponential,
        )

        client = self._get_client()
        request = self._build_request(
            dimensions=dimensions,
            metrics=metrics,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
            event_names=event_names,
        )
        transient = _transient_exception_types()
        retryer = Retrying(
            retry=retry_if_exception_type(transient) if transient else (lambda _: False),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=True,
        )
        try:
            for attempt in retryer:
                with attempt:
                    return client.run_report(request, timeout=self.timeout_seconds)
        except Exception as exc:  # every Google error maps to GA4ClientError
            raise translate_google_exception(exc) from exc
        return None  # pragma: no cover - unreachable (reraise=True)

    async def _run_report(self, **kwargs: Any) -> Any:
        """Async wrapper: runs the blocking report call in a worker thread."""
        return await asyncio.to_thread(self._run_report_sync, **kwargs)

    async def _run_paginated(
        self,
        *,
        dimensions: Iterable[str],
        metrics: Iterable[str],
        start_date: str,
        end_date: str,
        event_names: list[str] | None = None,
    ) -> tuple[list[dict[str, str]], str | None]:
        """Run a report and page through all rows (limit/offset)."""
        dims = tuple(dimensions)
        mets = tuple(metrics)
        all_rows: list[dict[str, str]] = []
        currency: str | None = None
        offset = 0
        while True:
            response = await self._run_report(
                dimensions=dims,
                metrics=mets,
                start_date=start_date,
                end_date=end_date,
                limit=GA4_PAGE_LIMIT,
                offset=offset,
                event_names=event_names,
            )
            currency = currency or _response_currency(response)
            page = response_to_dicts(response)
            all_rows.extend(page)
            row_count = _to_int(getattr(response, "row_count", 0) or 0)
            offset += len(page)
            if not page or offset >= row_count or len(page) < GA4_PAGE_LIMIT:
                break
        return all_rows, currency

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def test_connection(self) -> GA4TestResult:
        """
        Verify read access with a 7-day totals report.

        Never raises for known GA4/auth errors - they are reported in the result.
        """
        try:
            response = await self._run_report(
                dimensions=(),
                metrics=TEST_METRICS,
                start_date="7daysAgo",
                end_date="yesterday",
                limit=1,
                offset=0,
            )
        except GA4ClientError as exc:
            logger.warning(
                "ga4_test_connection_failed",
                property_id=self.property_id,
                error=str(exc),
            )
            return GA4TestResult(success=False, message=str(exc), property_id=self.property_id)

        rows = response_to_dicts(response)
        totals = rows[0] if rows else {}
        sessions = _to_int(totals.get("sessions", 0))
        conversions = _to_int(totals.get("keyEvents", 0))
        revenue = round(_to_float(totals.get("purchaseRevenue", 0.0)), 2)
        return GA4TestResult(
            success=True,
            message=(
                f"Read-only access verified for property {self.property_id} "
                f"({sessions} sessions in the last 7 days)."
            ),
            property_id=self.property_id,
            sessions_last_7d=sessions,
            conversions_last_7d=conversions,
            revenue_last_7d=revenue,
        )

    async def run_daily_report(
        self,
        start_date: date,
        end_date: date,
        conversion_events: list[str] | None = None,
    ) -> list[GA4DailyRow]:
        """
        Pull sessions / conversions / purchase revenue per
        date x sessionSource x sessionMedium x sessionCampaignName.

        When ``conversion_events`` is given, conversions are taken from a
        second report (``eventCount`` filtered by ``eventName IN events``);
        otherwise GA4 ``keyEvents`` is used.

        Raises:
            GA4ClientError / GA4AuthError on API failures.
        """
        if end_date < start_date:
            raise GA4ClientError("end_date must be on or after start_date")

        start = start_date.isoformat()
        end = end_date.isoformat()

        base_rows, currency = await self._run_paginated(
            dimensions=DAILY_DIMENSIONS,
            metrics=DAILY_METRICS,
            start_date=start,
            end_date=end,
        )

        merged: dict[tuple[date, str, str, str], GA4DailyRow] = {}
        for record in base_rows:
            key = self._row_key(record)
            if key is None:
                continue
            existing = merged.get(key)
            sessions = _to_int(record.get("sessions", 0))
            key_events = _to_int(record.get("keyEvents", 0))
            revenue = _to_float(record.get("purchaseRevenue", 0.0))
            total_revenue = _to_float(record.get("totalRevenue", 0.0))
            if existing is None:
                merged[key] = GA4DailyRow(
                    date=key[0],
                    utm_source=key[1],
                    utm_medium=key[2],
                    utm_campaign=key[3],
                    sessions=sessions,
                    conversions=key_events,
                    revenue=revenue,
                    total_revenue=total_revenue,
                    currency=currency,
                )
            else:
                existing.sessions += sessions
                existing.conversions += key_events
                existing.revenue += revenue
                existing.total_revenue += total_revenue

        events = [e.strip() for e in (conversion_events or []) if e and e.strip()]
        if events:
            conv_rows, _ = await self._run_paginated(
                dimensions=CONVERSION_DIMENSIONS,
                metrics=CONVERSION_METRICS,
                start_date=start,
                end_date=end,
                event_names=events,
            )
            counts: dict[tuple[date, str, str, str], int] = {}
            for record in conv_rows:
                key = self._row_key(record)
                if key is None:
                    continue
                counts[key] = counts.get(key, 0) + _to_int(record.get("eventCount", 0))
            for key, row in merged.items():
                row.conversions = counts.get(key, 0)
            for key, count in counts.items():
                if key not in merged:
                    merged[key] = GA4DailyRow(
                        date=key[0],
                        utm_source=key[1],
                        utm_medium=key[2],
                        utm_campaign=key[3],
                        sessions=0,
                        conversions=count,
                        revenue=0.0,
                        total_revenue=0.0,
                        currency=currency,
                    )

        rows = sorted(
            merged.values(), key=lambda r: (r.date, r.utm_source, r.utm_medium, r.utm_campaign)
        )
        for row in rows:
            row.revenue = round(row.revenue, 4)
            row.total_revenue = round(row.total_revenue, 4)
        logger.info(
            "ga4_daily_report_complete",
            property_id=self.property_id,
            start_date=start,
            end_date=end,
            rows=len(rows),
        )
        return rows

    @staticmethod
    def _row_key(record: dict[str, str]) -> tuple[date, str, str, str] | None:
        """Build the (date, source, medium, campaign) key for a flattened row."""
        try:
            row_date = _parse_ga4_date(record.get("date", ""))
        except ValueError:
            return None
        source = (record.get("sessionSource") or NOT_SET)[:255]
        medium = (record.get("sessionMedium") or NOT_SET)[:255]
        campaign = (record.get("sessionCampaignName") or NOT_SET)[:255]
        return (row_date, source, medium, campaign)


__all__ = [
    "GA4_READONLY_SCOPE",
    "GA4AuthError",
    "GA4ClientError",
    "GA4DailyRow",
    "GA4DataClient",
    "GA4NotConfiguredError",
    "GA4TestResult",
    "normalize_property_id",
    "response_to_dicts",
    "translate_google_exception",
]
