# =============================================================================
# Stratum AI - Meta Ads Insights API Client (read-only)
# =============================================================================
"""
Thin async client for the Meta Marketing API *Ads Insights* endpoint.

READ-ONLY BY CONSTRUCTION: the only HTTP verb this module ever issues is GET,
against ``/{version}/act_<ad account id>/insights``. It never creates, edits,
pauses or deletes a campaign, ad set, ad or audience. The permission required
is ``ads_read``.

Contract implemented here (verified against Meta's documentation):

- ``GET https://graph.facebook.com/<version>/act_<id>/insights``
- ``level=campaign`` (enum: ``account`` | ``campaign`` | ``adset`` | ``ad``)
- ``time_increment=1`` - one row per campaign per day (enum ``all_days`` /
  ``monthly`` / integer 1-90)
- ``time_range={"since": "YYYY-MM-DD", "until": "YYYY-MM-DD"}`` (JSON encoded)
- ``fields=`` comma-separated metric list (see ``INSIGHTS_FIELDS``)
- Response: ``{"data": [...], "paging": {"cursors": {"before", "after"},
  "next": "<url>"}}``. ``paging.cursors`` is present even on the final page,
  so pagination continues only while ``paging.next`` is present.
- Every numeric metric (``impressions``, ``clicks``, ``spend``, ``ctr``,
  ``cpc``, ``cpm``, ``campaign_id``) comes back as a *string*. ``spend`` and
  ``action_values[].value`` are decimal strings in the ad account currency,
  so they are parsed with ``Decimal`` and never with ``float``.
- ``actions``, ``action_values`` and ``video_p{25,50,75,100}_watched_actions``
  are lists of ``AdsActionStats`` objects: ``{"action_type": "...",
  "value": "125", "1d_click": "...", ...}``.

Errors follow the Graph API envelope ``{"error": {"message", "type", "code",
"error_subcode", "fbtrace_id"}}``. Code 190 (plus subcodes 463/467) means the
token is invalid or expired; codes 4 / 17 / 32 / 341 / 613 and the 80000-range
business-use-case codes mean throttling. Those are raised as distinct
exceptions so the caller can disconnect the connection or back off instead of
hammering.

Pagination stops on ``paging.next`` being absent. If the ``max_pages`` cap is
reached while Meta still advertises another page, the pull is *incomplete* and
``MetaInsightsTruncatedError`` is raised rather than a short list returned - a
truncated window must never be mistaken for a successful sync.

TOKEN SAFETY: the access token is sent in the ``Authorization: Bearer``
header, never as a query parameter, so it cannot leak into a URL, a
``paging.next`` link, a log line or an exception message. Every message this
module produces is additionally passed through ``_redact`` as a backstop.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Self

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Constants
# =============================================================================

GRAPH_API_BASE_URL = "https://graph.facebook.com"

#: Ads Insights fields requested for a campaign-level, day-by-day pull.
#: Mirrors the field list the original (never importable) facebook_business
#: adapter intended, minus everything that is not used downstream.
INSIGHTS_FIELDS: tuple[str, ...] = (
    "campaign_id",
    "campaign_name",
    "impressions",
    "clicks",
    "spend",
    "ctr",
    "cpc",
    "cpm",
    "actions",
    "action_values",
    # video_views comes from the "video_view" action_type inside `actions`
    # ("3-Second Video Views"), NOT from video_p25_watched_actions ("times
    # your video played at 25% of its length") - those are different, and
    # always smaller, numbers. Only the p100 list is requested, for
    # video_completions.
    "video_p100_watched_actions",
    "account_currency",
    "date_start",
    "date_stop",
)

#: Graph API error codes meaning "this access token cannot be used again".
TOKEN_ERROR_CODES: frozenset[int] = frozenset({102, 190})

#: Error subcodes under code 190 that specifically mean expired/invalidated.
TOKEN_ERROR_SUBCODES: frozenset[int] = frozenset({458, 460, 463, 467, 492})

#: Graph API error codes meaning "you are being throttled, back off".
RATE_LIMIT_ERROR_CODES: frozenset[int] = frozenset(
    {4, 17, 32, 341, 613, *range(80000, 80015)}
)

#: Meta's documented action_type for a 3-second video view. This is the
#: number ``CampaignMetric.video_views`` is named after; the
#: ``video_p*_watched_actions`` lists are watch-depth thresholds instead.
VIDEO_VIEW_ACTION_TYPE = "video_view"

#: Rows requested per page. Meta caps insights pages well below this; asking
#: for a large page simply means fewer round trips.
INSIGHTS_PAGE_LIMIT = 500

#: Fallback back-off when Meta does not tell us how long to wait.
DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = 300


def graph_api_version() -> str:
    """Return the configured Graph API version (e.g. ``v23.0``)."""
    return settings.meta_graph_api_version


# =============================================================================
# Exceptions
# =============================================================================


class MetaAPIError(Exception):
    """
    Error returned by (or while talking to) the Meta Graph API.

    Attributes:
        status_code: HTTP status of the Meta response (502 when unreachable).
        code: Graph API ``error.code`` (e.g. 190), or None.
        subcode: Graph API ``error.error_subcode``, or None.
        message: Human-readable message, already redacted.
        fbtrace_id: Meta's support identifier, useful in a bug report.
    """

    def __init__(
        self,
        status_code: int,
        code: Optional[int],
        message: str,
        *,
        subcode: Optional[int] = None,
        fbtrace_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.subcode = subcode
        self.message = message
        self.fbtrace_id = fbtrace_id

    def __str__(self) -> str:
        """Render as ``Meta API error <status> (code=..., subcode=...): <message>``."""
        parts = [f"Meta API error {self.status_code}"]
        detail = ", ".join(
            part
            for part in (
                f"code={self.code}" if self.code is not None else "",
                f"subcode={self.subcode}" if self.subcode is not None else "",
            )
            if part
        )
        if detail:
            parts.append(f"({detail})")
        return f"{' '.join(parts)}: {self.message}"


class MetaInsightsTruncatedError(Exception):
    """
    Raised when the pagination page cap was hit while Meta still had pages.

    Deliberately NOT a ``MetaAPIError``: nothing is wrong with Meta and a
    retry would truncate identically, so the caller must record the failure
    and leave freshness degraded rather than retry. The window is incomplete,
    so reporting it as a successful sync would put a partial number in front
    of the trust gate.

    Attributes:
        ad_account_id: Account whose pull was truncated.
        max_pages: The cap that was reached (``META_INSIGHTS_MAX_PAGES``).
        rows: Rows collected before the cap stopped the loop.
    """

    def __init__(self, ad_account_id: str, max_pages: int, rows: int) -> None:
        super().__init__(
            f"Meta insights pull for {ad_account_id} stopped at the "
            f"{max_pages}-page cap with {rows} rows and more pages pending; "
            f"raise META_INSIGHTS_MAX_PAGES or narrow the window"
        )
        self.ad_account_id = ad_account_id
        self.max_pages = max_pages
        self.rows = rows


class MetaTokenError(MetaAPIError):
    """
    Raised when Meta rejects the access token (code 190, or 102).

    The caller must mark the tenant's platform connection disconnected and
    stop retrying: a retry with the same token can only fail again.
    """


class MetaRateLimitError(MetaAPIError):
    """
    Raised when Meta throttles the request (codes 4 / 17 / 32 / 341 / 613 / 80000+).

    Attributes:
        retry_after_seconds: How long to wait before retrying, taken from
            ``X-Business-Use-Case-Usage.estimated_time_to_regain_access``
            when Meta supplies it, otherwise a conservative default.
    """

    def __init__(
        self,
        status_code: int,
        code: Optional[int],
        message: str,
        *,
        subcode: Optional[int] = None,
        fbtrace_id: Optional[str] = None,
        retry_after_seconds: int = DEFAULT_RATE_LIMIT_BACKOFF_SECONDS,
    ) -> None:
        super().__init__(
            status_code, code, message, subcode=subcode, fbtrace_id=fbtrace_id
        )
        self.retry_after_seconds = retry_after_seconds


# =============================================================================
# Parsed row
# =============================================================================


@dataclass(frozen=True)
class MetaInsightRow:
    """
    One Ads Insights row: a single campaign on a single day.

    Money is ``Decimal`` in the ad account's currency - never ``float`` - and
    is converted to integer minor units only at the persistence boundary.

    Attributes:
        campaign_id: Meta campaign id (``Campaign.external_id``).
        date: The day this row covers (``date_start``; identical to
            ``date_stop`` because ``time_increment=1``).
        impressions: Impressions served.
        clicks: Clicks (all clicks, matching Meta's ``clicks`` field).
        spend: Amount spent, in the ad account currency.
        conversions: Count for the winning conversion action type - the first
            configured type present in ``actions``.
        conversion_value: Revenue for that *same* action type read out of
            ``action_values``, in the ad account currency. Zero when Meta
            reported no value for it.
        video_views: 3-second video views (the ``video_view`` action type).
        video_completions: ``video_p100_watched_actions`` total.
        currency: ``account_currency`` (ISO 4217), when Meta returned it.
        campaign_name: Campaign name as Meta reports it, when requested.
        raw_actions: Untouched ``actions`` list, kept for debugging a
            mis-mapped action type. Never logged.
        raw_action_values: Untouched ``action_values`` list. Never logged.
    """

    campaign_id: str
    date: date
    impressions: int
    clicks: int
    spend: Decimal
    conversions: int
    conversion_value: Decimal
    video_views: Optional[int] = None
    video_completions: Optional[int] = None
    currency: Optional[str] = None
    campaign_name: Optional[str] = None
    raw_actions: tuple[dict[str, Any], ...] = field(default=())
    raw_action_values: tuple[dict[str, Any], ...] = field(default=())


# =============================================================================
# Parsing helpers
# =============================================================================


def _as_dict(value: Any) -> dict[str, Any]:
    """Return ``value`` when it is a dict, otherwise an empty dict."""
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    """
    Return ``value`` as a list when it is a list or tuple, else an empty list.

    Tuples matter: ``MetaInsightRow`` stores the raw action arrays as tuples
    (the dataclass is frozen), and ``sum_action_values`` is re-run over them.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _to_int(value: Any, *, default: int = 0) -> int:
    """
    Parse a Meta numeric string (``"1234"``) into an int.

    Meta returns every count as a string; a malformed value is logged at debug
    level (never with the raw payload) and falls back to ``default``.
    """
    if value is None or value == "":
        return default
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        logger.debug("meta_insight_int_unparseable", field_type=type(value).__name__)
        return default


def _to_decimal(value: Any, *, default: Decimal = Decimal(0)) -> Decimal:
    """
    Parse a Meta decimal string (``"12.34"``) into a ``Decimal``.

    Money must never round-trip through ``float``: Meta reports ``spend`` and
    ``action_values[].value`` as decimal strings in the account currency.
    """
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        logger.debug("meta_insight_decimal_unparseable", field_type=type(value).__name__)
        return default


def _parse_date(value: Any) -> Optional[date]:
    """Parse Meta's ``YYYY-MM-DD`` ``date_start`` / ``date_stop`` string."""
    if not value:
        return None
    try:
        # DTZ007 is not applicable: Meta sends a bare calendar day with no
        # time or zone, and .date() discards the naive datetime immediately.
        return datetime.strptime(str(value), "%Y-%m-%d").date()  # noqa: DTZ007
    except ValueError:
        logger.warning("meta_insight_date_unparseable")
        return None


def first_present_action_type(
    stats: Iterable[Any], action_types: Sequence[str]
) -> Optional[str]:
    """
    Return the first configured action type that appears in an action list.

    Action types are tried in the configured order and the first one present
    wins - Meta reports overlapping types (``omni_purchase`` groups the pixel,
    app, on-Facebook and offline purchases that are also reported
    individually), so summing across types would double count.

    Args:
        stats: A Meta ``actions`` / ``action_values`` list.
        action_types: Ordered action types to look for.

    Returns:
        The winning action type, or None when none of them is present.
    """
    present = {
        _as_dict(item).get("action_type")
        for item in _as_list(stats)
    }
    for action_type in action_types:
        if action_type in present:
            return action_type
    return None


def sum_one_action_type(
    stats: Iterable[Any], action_type: Optional[str]
) -> Optional[Decimal]:
    """
    Total the ``value`` of every AdsActionStats entry with exactly this type.

    Args:
        stats: A Meta ``actions`` / ``action_values`` list.
        action_type: The single action type to total, or None.

    Returns:
        The total, or None when ``action_type`` is None or absent from
        ``stats``. None is meaningfully different from ``Decimal(0)``: Meta
        drops an entry entirely when nothing was attributed to it, and the
        caller must not silently substitute another type's number.
    """
    if not action_type:
        return None
    matching = [
        entry
        for entry in (_as_dict(item) for item in _as_list(stats))
        if entry.get("action_type") == action_type
    ]
    if not matching:
        return None
    return sum((_to_decimal(entry.get("value")) for entry in matching), Decimal(0))


def sum_action_values(
    stats: Iterable[Any], action_types: Sequence[str]
) -> Optional[Decimal]:
    """
    Total one action list for the first configured action type present in it.

    Convenience wrapper over ``first_present_action_type`` +
    ``sum_one_action_type``. Do **not** call it twice to read a count out of
    ``actions`` and a value out of ``action_values``: the two lists can settle
    on different types (Meta omits a type from ``action_values`` when no value
    was attributed to it), which would pair one type's count with another
    type's revenue. ``row_from_payload`` resolves the type once instead.

    Args:
        stats: A Meta ``actions`` / ``action_values`` list.
        action_types: Ordered action types to look for.

    Returns:
        The total for the first matching action type, or None when no
        configured type is present.
    """
    return sum_one_action_type(
        stats, first_present_action_type(stats, action_types)
    )


def _sum_video_actions(stats: Any) -> Optional[int]:
    """Total a ``video_p*_watched_actions`` list, or None when absent."""
    entries = _as_list(stats)
    if not entries:
        return None
    return sum(_to_int(_as_dict(entry).get("value")) for entry in entries)


def _video_views_from_actions(actions: Iterable[Any]) -> Optional[int]:
    """
    Read 3-second video views out of the ``actions`` list.

    Meta documents ``video_view`` ("3-Second Video Views") as a first-class
    action type. ``video_p25_watched_actions`` is a *different*, always
    smaller, watch-depth metric, so ``CampaignMetric.video_views`` is fed from
    ``video_view`` and nothing else.

    Args:
        actions: The row's parsed ``actions`` list.

    Returns:
        The total, or None when the row reports no video views at all.
    """
    total = sum_one_action_type(actions, VIDEO_VIEW_ACTION_TYPE)
    return int(total) if total is not None else None


def row_from_payload(
    payload: dict[str, Any], conversion_action_types: Sequence[str]
) -> Optional[MetaInsightRow]:
    """
    Build a ``MetaInsightRow`` from one raw Ads Insights object.

    Args:
        payload: A single element of the response ``data`` array.
        conversion_action_types: Ordered action types counted as conversions.

    Returns:
        The parsed row, or None when the payload carries no campaign id or no
        parseable ``date_start`` (both are required to key a CampaignMetric).
    """
    campaign_id = str(payload.get("campaign_id") or "").strip()
    row_date = _parse_date(payload.get("date_start"))
    if not campaign_id or row_date is None:
        logger.warning(
            "meta_insight_row_skipped",
            has_campaign_id=bool(campaign_id),
            has_date=row_date is not None,
        )
        return None

    actions = tuple(_as_dict(item) for item in _as_list(payload.get("actions")))
    action_values = tuple(
        _as_dict(item) for item in _as_list(payload.get("action_values"))
    )

    # Resolve the conversion action type ONCE, against `actions`, then read
    # `action_values` for that same type. Reading each list independently can
    # pair the count of one type with the revenue of another, because Meta
    # drops a type from `action_values` when no value was attributed to it.
    conversion_action_type = first_present_action_type(
        actions, conversion_action_types
    )
    conversion_count = sum_one_action_type(actions, conversion_action_type)
    conversion_total = sum_one_action_type(action_values, conversion_action_type)

    if conversion_action_type is not None and conversion_total is None:
        # The count is real but Meta reported no value for that same type.
        # Report zero revenue rather than borrowing another type's number.
        logger.warning(
            "meta_insight_conversion_value_missing",
            campaign_id=campaign_id,
            date=row_date.isoformat(),
            action_type=conversion_action_type,
        )

    return MetaInsightRow(
        campaign_id=campaign_id,
        date=row_date,
        impressions=_to_int(payload.get("impressions")),
        clicks=_to_int(payload.get("clicks")),
        spend=_to_decimal(payload.get("spend")),
        conversions=int(conversion_count) if conversion_count is not None else 0,
        conversion_value=conversion_total
        if conversion_total is not None
        else Decimal(0),
        video_views=_video_views_from_actions(actions),
        video_completions=_sum_video_actions(payload.get("video_p100_watched_actions")),
        currency=(str(payload.get("account_currency")) or None)
        if payload.get("account_currency")
        else None,
        campaign_name=payload.get("campaign_name") or None,
        raw_actions=actions,
        raw_action_values=action_values,
    )


# =============================================================================
# Client
# =============================================================================


class MetaInsightsClient:
    """
    Read-only ``httpx.AsyncClient`` wrapper over the Ads Insights endpoint.

    Args:
        access_token: A Meta user or system-user token with ``ads_read``.
            Sent as ``Authorization: Bearer`` - never in the query string.
        api_version: Graph API version (defaults to
            ``settings.meta_graph_api_version``).
        timeout: Per-request timeout in seconds (defaults to
            ``settings.meta_insights_request_timeout_seconds``).
        max_pages: Page cap for the pagination loop (defaults to
            ``settings.meta_insights_max_pages``).
        conversion_action_types: Ordered action types counted as conversions
            (defaults to ``settings.meta_conversion_action_types_list``).
        transport: Optional ``httpx`` transport (tests use
            ``httpx.MockTransport``).
    """

    def __init__(
        self,
        access_token: str,
        *,
        api_version: Optional[str] = None,
        timeout: Optional[float] = None,
        max_pages: Optional[int] = None,
        conversion_action_types: Optional[Sequence[str]] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        if not access_token:
            raise ValueError("access_token is required")
        self._access_token = access_token
        self._api_version = api_version or graph_api_version()
        self._timeout = (
            timeout
            if timeout is not None
            else settings.meta_insights_request_timeout_seconds
        )
        self._max_pages = (
            max_pages if max_pages is not None else settings.meta_insights_max_pages
        )
        self._conversion_action_types = list(
            conversion_action_types
            if conversion_action_types is not None
            else settings.meta_conversion_action_types_list
        )
        self._transport = transport
        self._http: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------ setup

    @property
    def api_version(self) -> str:
        """Graph API version this client talks to."""
        return self._api_version

    @property
    def base_url(self) -> str:
        """Versioned Graph API base URL."""
        return f"{GRAPH_API_BASE_URL}/{self._api_version}"

    def _get_http(self) -> httpx.AsyncClient:
        """
        Lazily build the underlying ``httpx.AsyncClient``.

        One client is reused for the whole pagination loop so the TLS
        connection is kept alive between pages.
        """
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
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

    async def __aenter__(self) -> Self:
        """Enter an ``async with`` block."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close the HTTP client on leaving an ``async with`` block."""
        await self.aclose()

    # ------------------------------------------------------------ redaction

    def _redact(self, text: str) -> str:
        """
        Remove the access token from ``text`` if it somehow appears in it.

        The token is only ever sent as a header, so this is a backstop against
        Meta echoing a caller-supplied value back in an error message.
        """
        if self._access_token and self._access_token in text:
            return text.replace(self._access_token, "***REDACTED***")
        return text

    # -------------------------------------------------------------- errors

    @staticmethod
    def _retry_after_from_headers(headers: httpx.Headers) -> int:
        """
        Read the back-off hint out of the response headers.

        ``X-Business-Use-Case-Usage`` is Meta's own hint: it maps a business
        id to a list of usage objects carrying
        ``estimated_time_to_regain_access`` *in minutes*. When it is absent
        (an edge or proxy throttle, which answers plain HTTP 429), the
        standard ``Retry-After`` header is used instead. Falls back to
        ``DEFAULT_RATE_LIMIT_BACKOFF_SECONDS``.

        Args:
            headers: Response headers.

        Returns:
            Seconds to wait before retrying.
        """
        raw = headers.get("x-business-use-case-usage")
        if raw:
            try:
                usage = json.loads(raw)
            except (ValueError, TypeError):
                usage = None
            if usage is not None:
                minutes = 0
                for entries in _as_dict(usage).values():
                    for entry in _as_list(entries):
                        value = _as_dict(entry).get(
                            "estimated_time_to_regain_access"
                        )
                        try:
                            minutes = max(minutes, int(value))
                        except (TypeError, ValueError):
                            continue
                if minutes > 0:
                    return minutes * 60

        # RFC 9110 Retry-After: delta-seconds, or an HTTP-date we do not try
        # to parse (falling back to the default is the safe reading).
        retry_after = headers.get("retry-after")
        if retry_after:
            try:
                seconds = int(str(retry_after).strip())
            except (TypeError, ValueError):
                seconds = 0
            if seconds > 0:
                return seconds

        return DEFAULT_RATE_LIMIT_BACKOFF_SECONDS

    def _error_from_body(
        self, status_code: int, body: Any, headers: httpx.Headers
    ) -> MetaAPIError:
        """
        Translate a Graph API error envelope into the right exception type.

        Args:
            status_code: HTTP status of the response.
            body: Parsed JSON body (or None when it was not JSON).
            headers: Response headers, read for throttling hints.

        Returns:
            ``MetaTokenError`` for code 190/102, ``MetaRateLimitError`` for a
            throttling code *or* HTTP 429, otherwise ``MetaAPIError``.
        """
        error = _as_dict(_as_dict(body).get("error"))
        raw_code = error.get("code")
        code: Optional[int]
        try:
            code = int(raw_code) if raw_code is not None else None
        except (TypeError, ValueError):
            code = None

        raw_subcode = error.get("error_subcode")
        try:
            subcode: Optional[int] = int(raw_subcode) if raw_subcode is not None else None
        except (TypeError, ValueError):
            subcode = None

        message = self._redact(
            str(error.get("message") or f"Meta request failed with HTTP {status_code}")
        )
        fbtrace_id = error.get("fbtrace_id") or None

        if code in TOKEN_ERROR_CODES or (subcode in TOKEN_ERROR_SUBCODES and code == 190):
            return MetaTokenError(
                status_code, code, message, subcode=subcode, fbtrace_id=fbtrace_id
            )
        # Classify on the HTTP status too: an edge/proxy throttle answers 429
        # with no Graph error envelope at all, so `code` is None and this
        # would otherwise become a generic error that Celery retries after
        # ~1s against an endpoint that is already throttling us.
        if code in RATE_LIMIT_ERROR_CODES or status_code == 429:
            return MetaRateLimitError(
                status_code,
                code,
                message,
                subcode=subcode,
                fbtrace_id=fbtrace_id,
                retry_after_seconds=self._retry_after_from_headers(headers),
            )
        return MetaAPIError(
            status_code, code, message, subcode=subcode, fbtrace_id=fbtrace_id
        )

    # ------------------------------------------------------------- requests

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Issue one GET and return the parsed JSON body.

        This is the only request method in the module - the client is
        read-only by construction.

        Raises:
            MetaTokenError: the token was rejected (code 190 / 102).
            MetaRateLimitError: Meta is throttling (codes 4/17/32/341/613/80000+).
            MetaAPIError: any other non-2xx response, transport failure (502)
                or non-JSON body.
        """
        http = self._get_http()
        try:
            response = await http.get(path, params=params)
        except httpx.HTTPError as exc:
            # str(exc) can contain the request URL; the token is never in it
            # (header auth), and _redact is the belt-and-braces backstop.
            detail = self._redact(f"Meta Graph API unreachable: {exc}")
            logger.error("meta_insights_request_failed", path=path, error=detail)
            raise MetaAPIError(502, None, detail) from exc

        try:
            body = response.json()
        except ValueError:
            body = None

        if response.status_code < 200 or response.status_code >= 300:
            error = self._error_from_body(response.status_code, body, response.headers)
            logger.warning(
                "meta_insights_api_error",
                path=path,
                status_code=error.status_code,
                code=error.code,
                subcode=error.subcode,
                fbtrace_id=error.fbtrace_id,
                message=error.message,
            )
            raise error

        if body is None:
            raise MetaAPIError(502, None, "Meta returned a non-JSON body")

        # Meta occasionally answers 200 with an error envelope.
        if _as_dict(body).get("error"):
            error = self._error_from_body(response.status_code, body, response.headers)
            logger.warning(
                "meta_insights_api_error_in_200",
                path=path,
                code=error.code,
                fbtrace_id=error.fbtrace_id,
            )
            raise error

        return _as_dict(body)

    # --------------------------------------------------------------- public

    async def get_campaign_insights(
        self,
        ad_account_id: str,
        since: date,
        until: date,
        campaign_ids: Optional[Sequence[str]] = None,
    ) -> list[MetaInsightRow]:
        """
        Pull day-by-day, campaign-level insights for one ad account.

        Issues ``GET /act_<id>/insights`` with ``level=campaign`` and
        ``time_increment=1``, then follows ``paging.next`` (using
        ``paging.cursors.after``) until the data is exhausted or
        ``max_pages`` is reached.

        Args:
            ad_account_id: Ad account id, with or without the ``act_`` prefix.
            since: First day of the window (inclusive).
            until: Last day of the window (inclusive).
            campaign_ids: When given, Meta filters server-side to these
                campaign ids, so a per-campaign sync does not download the
                whole ad account.

        Returns:
            One ``MetaInsightRow`` per campaign per day, in Meta's order.

        Raises:
            ValueError: ``ad_account_id`` is empty or ``until`` precedes ``since``.
            MetaTokenError: the token was rejected.
            MetaRateLimitError: Meta is throttling.
            MetaInsightsTruncatedError: the ``max_pages`` cap was reached while
                Meta still had pages, so the window would be incomplete.
            MetaAPIError: any other API or transport failure.
        """
        account = str(ad_account_id or "").strip()
        if not account:
            raise ValueError("ad_account_id is required")
        if not account.startswith("act_"):
            account = f"act_{account}"
        if until < since:
            raise ValueError("until must be on or after since")

        params: dict[str, Any] = {
            "level": "campaign",
            "time_increment": 1,
            "time_range": json.dumps(
                {"since": since.isoformat(), "until": until.isoformat()}
            ),
            "fields": ",".join(INSIGHTS_FIELDS),
            "limit": INSIGHTS_PAGE_LIMIT,
        }

        if campaign_ids:
            # Narrow the response server-side. Meta's insights endpoint takes
            # entity ids as a filter rather than in the path.
            params["filtering"] = json.dumps(
                [
                    {
                        "field": "campaign.id",
                        "operator": "IN",
                        "value": [str(cid) for cid in campaign_ids],
                    }
                ]
            )

        rows: list[MetaInsightRow] = []
        path = f"/{account}/insights"
        after: Optional[str] = None
        pages = 0

        while pages < self._max_pages:
            page_params = dict(params)
            if after:
                page_params["after"] = after

            body = await self._get(path, page_params)
            pages += 1

            for payload in _as_list(body.get("data")):
                row = row_from_payload(
                    _as_dict(payload), self._conversion_action_types
                )
                if row is not None:
                    rows.append(row)

            paging = _as_dict(body.get("paging"))
            # paging.cursors is returned even on the final page, so "next"
            # is the only reliable "there is more" signal.
            if not paging.get("next"):
                break
            after = _as_dict(paging.get("cursors")).get("after")
            if not after:
                break
        else:
            # The loop ran out of pages allowed, not out of pages available:
            # `rows` is a silently truncated window. Raise rather than return
            # it, so the caller cannot report a partial pull as a fresh,
            # successful sync.
            logger.error(
                "meta_insights_page_cap_reached",
                ad_account_id=account,
                max_pages=self._max_pages,
                rows=len(rows),
            )
            raise MetaInsightsTruncatedError(account, self._max_pages, len(rows))

        logger.info(
            "meta_insights_fetched",
            ad_account_id=account,
            since=since.isoformat(),
            until=until.isoformat(),
            pages=pages,
            rows=len(rows),
        )
        return rows
