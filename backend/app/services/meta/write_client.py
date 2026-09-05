# =============================================================================
# Stratum AI - Meta Marketing API Write Client
# =============================================================================
"""
Thin async client for Meta Marketing API **writes**.

This is the only module in the codebase that may change something on Meta.
``insights_client`` remains read-only by construction; this one issues exactly
two kinds of request, against the node itself:

- ``GET  https://graph.facebook.com/<version>/<entity id>?fields=...`` - read
  the entity's CURRENT state, so a before/after value is *measured* rather
  than assumed.
- ``POST https://graph.facebook.com/<version>/<entity id>`` - apply an update.

Contract verified against Meta's documentation (2026-09):

- **Campaign** (``/docs/marketing-api/reference/ad-campaign-group/``): update is
  ``POST /{campaign_id}``; ``status``, ``daily_budget`` and ``lifetime_budget``
  are updatable. ``status`` accepts ``ACTIVE`` / ``PAUSED`` / ``DELETED`` /
  ``ARCHIVED`` on update ("Only ACTIVE and PAUSED are valid during creation").
- **Ad set** (``/docs/marketing-api/reference/ad-campaign/``): update is
  ``POST /{ad_set_id}``; ``status``, ``daily_budget``, ``lifetime_budget`` and
  ``bid_amount`` are updatable.
- **Ad** (``/docs/marketing-api/reference/adgroup/``): update is
  ``POST /{ad_id}``; ``status`` is updatable. An ad has **no** ``daily_budget``,
  and ``bid_amount`` on an ad is refused by Meta: "We no longer allow setting
  the bid_amount value on an ad. Please set bid_amount for the ad set."

This module deliberately narrows Meta's contract:

- Only ``ACTIVE`` and ``PAUSED`` may ever be written. ``DELETED`` and
  ``ARCHIVED`` are destructive and are not reachable from autopilot.
- Only the fields in ``WRITABLE_FIELDS`` may be written, per entity type. A
  change naming anything else is rejected locally, before any request.

MONEY. Meta budget and bid fields are integers in the ad account's **API
units**, which are the currency's minor units for most currencies and the
*basic* unit for the offset-1 currencies. Meta's own wording: "The bid amount's
unit is cents for currencies like USD, EUR, and the basic unit for currencies
like JPY, KRW." ``meta_currency_offset`` below is the single documented place
where that conversion happens - see its docstring for why this table is Meta's
and not ISO 4217's.

RETRY SAFETY. A read may be retried freely. A write may **never** be blindly
retried: if the request left this process, Meta may have applied it even
though we never saw the response. A timeout or a connection reset during an
update therefore raises :class:`MetaWriteAmbiguousError` - deliberately not a
``MetaAPIError``, so no generic "retry on Meta error" handler can pick it up -
and the caller must reconcile by re-reading the entity.

TOKEN SAFETY. The access token is sent in the ``Authorization: Bearer`` header,
never in a query string or a form body, so it cannot reach a URL, a log line or
an exception message. Every message produced here additionally passes through
``_redact`` as a backstop.

The permission these calls require is ``ads_management`` (``ads_read`` is not
enough), which is subject to Meta App Review.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from typing import Any, Self

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.services.meta.insights_client import (
    GRAPH_API_BASE_URL,
    RATE_LIMIT_ERROR_CODES,
    TOKEN_ERROR_CODES,
    TOKEN_ERROR_SUBCODES,
    MetaAPIError,
    MetaRateLimitError,
    MetaTokenError,
    _as_dict,
    graph_api_version,
)

logger = get_logger(__name__)


# =============================================================================
# Entities and writable fields
# =============================================================================


class MetaEntityType(str, Enum):
    """
    The Meta nodes autopilot may read or write.

    ``AD_ACCOUNT`` is read-only here: it exists solely so the account currency
    can be read from Meta before any money is converted.
    """

    CAMPAIGN = "campaign"
    ADSET = "adset"
    AD = "ad"
    AD_ACCOUNT = "ad_account"


#: Entity types this client will accept an update for. The ad account is not
#: among them - nothing in autopilot edits an account.
WRITABLE_ENTITY_TYPES: frozenset[MetaEntityType] = frozenset(
    {MetaEntityType.CAMPAIGN, MetaEntityType.ADSET, MetaEntityType.AD}
)

#: Fields this client will write, per entity type. Anything not listed is
#: rejected locally: the point of an allowlist is that a bug elsewhere cannot
#: turn into an unexpected mutation on a live ad account.
WRITABLE_FIELDS: dict[MetaEntityType, frozenset[str]] = {
    MetaEntityType.CAMPAIGN: frozenset({"status", "daily_budget", "lifetime_budget"}),
    # bid_amount lives on the ad set; Meta refuses it on an ad.
    MetaEntityType.ADSET: frozenset(
        {"status", "daily_budget", "lifetime_budget", "bid_amount"}
    ),
    MetaEntityType.AD: frozenset({"status"}),
}

#: Integer money/bid fields. Values for these are Meta API units and are
#: produced only by :func:`major_to_meta_minor`.
MONEY_FIELDS: frozenset[str] = frozenset(
    {"daily_budget", "lifetime_budget", "bid_amount"}
)

#: Status values autopilot may write. Meta also accepts DELETED and ARCHIVED
#: on update; both are destructive and intentionally unreachable from here.
WRITABLE_STATUSES: frozenset[str] = frozenset({"ACTIVE", "PAUSED"})

#: Fields read back to establish before/after state, per entity type.
#: ``effective_status`` is read for context only - it reflects parent state and
#: is never written; ``status`` is the field autopilot sets and verifies.
DEFAULT_READ_FIELDS: dict[MetaEntityType, tuple[str, ...]] = {
    MetaEntityType.CAMPAIGN: (
        "id",
        "name",
        "status",
        "effective_status",
        "daily_budget",
        "lifetime_budget",
        "account_id",
    ),
    MetaEntityType.ADSET: (
        "id",
        "name",
        "status",
        "effective_status",
        "daily_budget",
        "lifetime_budget",
        "bid_amount",
        "campaign_id",
        "account_id",
    ),
    MetaEntityType.AD: (
        "id",
        "name",
        "status",
        "effective_status",
        "adset_id",
        "campaign_id",
        "account_id",
    ),
    MetaEntityType.AD_ACCOUNT: ("id", "currency", "account_status"),
}


# =============================================================================
# Money: the one place major units become Meta API units
# =============================================================================

#: Meta's own ad-account currency offsets (``/docs/marketing-api/currencies/``).
#:
#: This is deliberately **not** the ISO 4217 minor-unit table that
#: ``insights_ingestion.CURRENCY_MINOR_UNIT_EXPONENT`` uses, and the two must
#: not be merged. They disagree, and on the write path the disagreement is
#: money:
#:
#: * HUF, IDR, TWD, COP and CRC are two-decimal in ISO 4217 but **offset 1** at
#:   Meta. Converting a HUF budget with the ISO table would send Meta a number
#:   100x too large.
#: * BHD, JOD, KWD, OMR, TND are three-decimal in ISO 4217 but **offset 100** at
#:   Meta ("No currencies have an offset of 1000"). The ISO table would send a
#:   number 10x too large.
#:
#: The read path is unaffected: insights returns ``spend`` as a decimal string
#: in major units, so it never multiplies by an offset.
META_CURRENCY_OFFSET: dict[str, int] = {
    # Offset 1 - the amount IS the basic unit, there is no minor unit.
    "CLP": 1,
    "COP": 1,
    "CRC": 1,
    "HUF": 1,
    "IDR": 1,
    "ISK": 1,
    "JPY": 1,
    "KRW": 1,
    "PYG": 1,
    "TWD": 1,
    "VND": 1,
    # Offset 100 - the amount is hundredths of the basic unit.
    "AED": 100,
    "ARS": 100,
    "AUD": 100,
    "BDT": 100,
    "BGN": 100,
    "BHD": 100,
    "BOB": 100,
    "BRL": 100,
    "CAD": 100,
    "CHF": 100,
    "CNY": 100,
    "CZK": 100,
    "DKK": 100,
    "DZD": 100,
    "EGP": 100,
    "EUR": 100,
    "GBP": 100,
    "GTQ": 100,
    "HKD": 100,
    "HNL": 100,
    "ILS": 100,
    "INR": 100,
    "JOD": 100,
    "KES": 100,
    "MOP": 100,
    "MXN": 100,
    "MYR": 100,
    "NGN": 100,
    "NIO": 100,
    "NOK": 100,
    "NZD": 100,
    "PEN": 100,
    "PHP": 100,
    "PKR": 100,
    "PLN": 100,
    "QAR": 100,
    "RON": 100,
    "RSD": 100,
    "RUB": 100,
    "SAR": 100,
    "SEK": 100,
    "SGD": 100,
    "THB": 100,
    "TRY": 100,
    "UAH": 100,
    "USD": 100,
    "UYU": 100,
    "VES": 100,
    "ZAR": 100,
}

#: Currencies with no minor unit at Meta, for callers and tests.
META_ZERO_DECIMAL_CURRENCIES: frozenset[str] = frozenset(
    code for code, offset in META_CURRENCY_OFFSET.items() if offset == 1
)


class UnsupportedCurrencyError(Exception):
    """
    Raised when an amount cannot be converted because the currency is unknown.

    Meta publishes 104 ad-account currencies and this table carries the ones
    that could be transcribed from that page. A currency that is absent is a
    currency whose offset we do not know, and guessing 100 would be a 100x
    error for an offset-1 currency. Refusing is the only safe answer: the
    action is recorded as refused with this reason, no request is sent, and an
    operator adds the code to ``META_CURRENCY_OFFSET`` once verified.

    Attributes:
        currency: The unrecognised currency code, as supplied.
    """

    def __init__(self, currency: str | None) -> None:
        super().__init__(
            f"Meta currency offset for {currency!r} is not known to Stratum; "
            "refusing to convert a budget rather than guess the unit"
        )
        self.currency = currency


def meta_currency_offset(currency: str | None) -> int:
    """
    Return Meta's API-unit offset for an ad account currency.

    Args:
        currency: ISO 4217 code of the ad account (case-insensitive).

    Returns:
        1 for a currency whose API unit is the basic unit (JPY, KRW, HUF,
        TWD, ...), 100 for a currency whose API unit is hundredths.

    Raises:
        UnsupportedCurrencyError: the code is missing or not in Meta's table.
    """
    if not currency:
        raise UnsupportedCurrencyError(currency)
    offset = META_CURRENCY_OFFSET.get(str(currency).strip().upper())
    if offset is None:
        raise UnsupportedCurrencyError(currency)
    return offset


def major_to_meta_minor(amount: Decimal, currency: str | None) -> int:
    """
    Convert an amount in the account's major unit into Meta API units.

    This is the boundary conversion, and the only one. ``Decimal(100)`` USD
    becomes ``10000``; ``Decimal(1000)`` JPY becomes ``1000``.

    The amount is first quantised at the currency's real precision, so a
    zero-decimal currency can never acquire a fraction that is then silently
    truncated.

    Args:
        amount: Money in the ad account's major unit (never a ``float``).
        currency: ISO 4217 code of that amount.

    Returns:
        A non-negative integer in Meta's API units for that currency.

    Raises:
        UnsupportedCurrencyError: the currency's offset is unknown.
        TypeError: the amount is not a Decimal.
        ValueError: the amount is negative or not finite.
    """
    if not isinstance(amount, Decimal):
        raise TypeError("amount must be a Decimal, never a float")
    if not amount.is_finite():
        raise ValueError("amount must be finite")
    if amount < 0:
        raise ValueError("amount must not be negative")
    offset = meta_currency_offset(currency)
    # Quantise at the currency's real precision first (JPY has none), then
    # scale. Doing it the other way round would round 1000.4 JPY to 1000.4
    # API units and truncate.
    places = 0 if offset == 1 else 2
    at_precision = amount.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return int((at_precision * offset).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def meta_minor_to_major(value: Any, currency: str | None) -> Decimal:
    """
    Convert a Meta API-unit integer back into the account's major unit.

    Meta returns budgets as *strings*, so the value is parsed with ``Decimal``.

    Args:
        value: The API-unit value as returned by Meta (string or int).
        currency: ISO 4217 code of the ad account.

    Returns:
        The amount in major units, e.g. ``Decimal("100.00")`` for a USD
        ``daily_budget`` of ``"10000"``.

    Raises:
        UnsupportedCurrencyError: the currency's offset is unknown.
        ValueError: the value is not a usable integer amount.
    """
    offset = meta_currency_offset(currency)
    try:
        as_decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Meta returned a non-numeric money value: {value!r}") from exc
    return as_decimal / Decimal(offset)


def hundredths_to_meta_minor(cents: int, currency: str | None) -> int:
    """
    Convert one of this schema's ``*_cents`` values into Meta API units.

    The ``*_cents`` columns hold hundredths of the **major** unit for every
    currency (see CLAUDE.md), which is not the same convention as Meta's. For
    JPY the two differ by exactly 100x: ``daily_budget_cents = 100000`` means
    JPY 1000.00, which Meta wants as ``1000``, not ``100000``.

    Args:
        cents: Hundredths of the major unit.
        currency: ISO 4217 code of the ad account.

    Returns:
        The same amount in Meta API units.

    Raises:
        UnsupportedCurrencyError: the currency's offset is unknown.
    """
    return major_to_meta_minor(Decimal(int(cents)) / Decimal(100), currency)


# =============================================================================
# Errors
# =============================================================================

#: Graph API error codes that mean "this request is wrong and will be wrong
#: again". Retrying is pointless and, on a write, dangerous. Code 100 is
#: "Invalid parameter", 200 "Permission error", 294 "requires extended
#: permission", 3 "unknown method", 2635 "deprecated endpoint".
NON_RETRYABLE_ERROR_CODES: frozenset[int] = frozenset({3, 100, 200, 294, 2635})

#: Marketing-API subcodes worth naming because autopilot can actually trigger
#: them. 1487901: daily budget below the account minimum. 1885621: the ad set
#: and campaign budget cannot both be set (Advantage campaign budget is on).
NON_RETRYABLE_ERROR_SUBCODES: frozenset[int] = frozenset({1487901, 1885621})


class MetaWriteValidationError(MetaAPIError):
    """
    Meta rejected the update as invalid, and will reject it identically again.

    Distinct from :class:`MetaAPIError` so a caller can record the action as
    failed instead of scheduling a retry. Meta's own ``is_transient: false``
    flag, the codes in :data:`NON_RETRYABLE_ERROR_CODES` and the subcodes in
    :data:`NON_RETRYABLE_ERROR_SUBCODES` all land here.
    """


class MetaWriteAmbiguousError(Exception):
    """
    A write left this process but its outcome is unknown.

    Raised for a timeout, a connection reset or any transport failure during
    an update. Meta may or may not have applied the change, so this is
    **never** retried: the caller reconciles by re-reading the entity and
    comparing it against the intended state.

    Deliberately not a ``MetaAPIError`` - a handler that retries "Meta errors"
    must not be able to catch this one by accident.

    Attributes:
        entity_type: The node whose state is now unknown.
        entity_id: Its Meta id.
        detail: Redacted description of the transport failure.
    """

    def __init__(self, entity_type: str, entity_id: str, detail: str) -> None:
        super().__init__(
            f"Meta update to {entity_type} {entity_id} did not return a result "
            f"({detail}); the change may or may not have been applied and must "
            "be reconciled by re-reading the entity, never retried"
        )
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.detail = detail


# =============================================================================
# Client
# =============================================================================


class MetaWriteClient:
    """
    Async ``httpx`` wrapper over the Meta Marketing API write endpoints.

    Reads the current state of a campaign, ad set or ad and applies narrowly
    allowlisted updates to it. Requires ``ads_management``.

    Args:
        access_token: Meta user or system-user token with ``ads_management``.
            Sent as ``Authorization: Bearer`` - never in a URL or a form body.
        api_version: Graph API version (defaults to
            ``settings.meta_graph_api_version``).
        timeout: Per-request timeout in seconds (defaults to
            ``settings.meta_write_request_timeout_seconds``).
        transport: Optional ``httpx`` transport (tests use
            ``httpx.MockTransport``).
    """

    def __init__(
        self,
        access_token: str,
        *,
        api_version: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not access_token:
            raise ValueError("access_token is required")
        self._access_token = access_token
        self._api_version = api_version or graph_api_version()
        self._timeout = (
            timeout
            if timeout is not None
            else settings.meta_write_request_timeout_seconds
        )
        self._transport = transport
        self._http: httpx.AsyncClient | None = None

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

        No transport-level retries are configured, and none may be: httpx
        would replay a POST whose response never arrived, which is precisely
        the double-apply this module exists to prevent.
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

    # ------------------------------------------------------------- redaction

    def _redact(self, text: str) -> str:
        """
        Remove the access token from ``text`` if it somehow appears in it.

        The token only ever travels in a header, so this is a backstop against
        Meta echoing a caller-supplied value back in an error message.
        """
        if self._access_token and self._access_token in text:
            return text.replace(self._access_token, "***REDACTED***")
        return text

    # ---------------------------------------------------------------- errors

    def _error_from_body(
        self, status_code: int, body: Any, headers: httpx.Headers
    ) -> MetaAPIError:
        """
        Translate a Graph API error envelope into the right exception type.

        Classification order matters. A token error is a token error whatever
        else the envelope says; a throttle must not be mistaken for a
        validation failure (it is retryable, later); and everything Meta flags
        ``is_transient: false``, or that carries a code/subcode we know to be
        permanent, becomes a :class:`MetaWriteValidationError` so no caller
        retries it.

        Args:
            status_code: HTTP status of the response.
            body: Parsed JSON body (or None when it was not JSON).
            headers: Response headers, read for throttling hints.

        Returns:
            The most specific exception the envelope supports.
        """
        error = _as_dict(_as_dict(body).get("error"))

        def _int_or_none(value: Any) -> int | None:
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        code = _int_or_none(error.get("code"))
        subcode = _int_or_none(error.get("error_subcode"))
        message = self._redact(
            str(error.get("message") or f"Meta request failed with HTTP {status_code}")
        )
        # error_user_msg is the operator-facing text Meta writes for Marketing
        # API rejections; it is usually the only useful part.
        user_message = error.get("error_user_msg")
        if user_message:
            message = f"{message} ({self._redact(str(user_message))})"
        fbtrace_id = error.get("fbtrace_id") or None
        is_transient = error.get("is_transient")

        if code in TOKEN_ERROR_CODES or (subcode in TOKEN_ERROR_SUBCODES and code == 190):
            return MetaTokenError(
                status_code, code, message, subcode=subcode, fbtrace_id=fbtrace_id
            )
        if code in RATE_LIMIT_ERROR_CODES or status_code == 429:
            return MetaRateLimitError(
                status_code,
                code,
                message,
                subcode=subcode,
                fbtrace_id=fbtrace_id,
                retry_after_seconds=self._retry_after_seconds(headers),
            )
        if (
            code in NON_RETRYABLE_ERROR_CODES
            or subcode in NON_RETRYABLE_ERROR_SUBCODES
            or is_transient is False
            or 400 <= status_code < 500
        ):
            return MetaWriteValidationError(
                status_code, code, message, subcode=subcode, fbtrace_id=fbtrace_id
            )
        return MetaAPIError(
            status_code, code, message, subcode=subcode, fbtrace_id=fbtrace_id
        )

    @staticmethod
    def _retry_after_seconds(headers: httpx.Headers) -> int:
        """
        Read the throttling back-off hint out of the response headers.

        Delegates to the read client's parser so the two agree on Meta's
        ``X-Business-Use-Case-Usage`` (minutes) and ``Retry-After`` (seconds)
        semantics rather than each inventing their own.

        Args:
            headers: Response headers.

        Returns:
            Seconds to wait before the caller may talk to Meta again.
        """
        from app.services.meta.insights_client import MetaInsightsClient

        return MetaInsightsClient._retry_after_from_headers(headers)

    def _check_response(
        self,
        path: str,
        response: httpx.Response,
        event: str,
        *,
        write_target: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Validate a Meta response and return its parsed body.

        ``write_target`` marks the call as a **write**, which changes how two
        indeterminate answers are classified. On a read either one is an
        ordinary error, because a read cannot have changed anything; on a
        write the request has already been accepted or rejected somewhere out
        of sight, and recording "it did not happen" would be a guess:

        * **2xx with a body that will not parse.** Meta accepted the request
          and then said something this client cannot read. Whether the change
          landed is unknown.
        * **5xx that Meta did not itself mark permanent.** A gateway or
          backend failure can occur before or after the mutation is applied.
          A 4xx, a throttle, a token rejection and anything carrying
          ``is_transient: false`` are excluded: each of those is Meta stating
          it did not apply the change.

        Both become :class:`MetaWriteAmbiguousError`, so the caller reconciles
        by re-reading instead of recording a definite failure or retrying.

        Args:
            path: Request path, for the log record.
            response: The httpx response.
            event: Structured-log event name.
            write_target: ``(entity_type, entity_id)`` when this response is
                for a write; None for a read.

        Returns:
            The parsed JSON object.

        Raises:
            MetaWriteAmbiguousError: a write whose outcome cannot be
                established from the response.
            MetaTokenError, MetaRateLimitError, MetaWriteValidationError,
            MetaAPIError: per :meth:`_error_from_body`.
        """
        try:
            body = response.json()
        except ValueError:
            body = None

        if response.status_code < 200 or response.status_code >= 300:
            error = self._error_from_body(response.status_code, body, response.headers)
            logger.warning(
                event,
                path=path,
                status_code=error.status_code,
                code=error.code,
                subcode=error.subcode,
                fbtrace_id=error.fbtrace_id,
                # Deliberately not `message=`: structlog's stdlib renderer
                # passes extra keys through to LogRecord, where `message` is
                # reserved and raises "Attempt to overwrite 'message'".
                error_message=error.message,
            )
            if (
                write_target is not None
                and error.status_code >= 500
                and not isinstance(
                    error, MetaTokenError | MetaRateLimitError | MetaWriteValidationError
                )
            ):
                raise MetaWriteAmbiguousError(
                    write_target[0],
                    write_target[1],
                    f"Meta answered HTTP {error.status_code} ({error.message}), which "
                    "it did not mark permanent, so whether the update was applied "
                    "before the failure is unknown.",
                ) from error
            raise error

        if body is None:
            if write_target is not None:
                raise MetaWriteAmbiguousError(
                    write_target[0],
                    write_target[1],
                    f"Meta accepted the update with HTTP {response.status_code} but "
                    "returned a body this client cannot parse, so whether it was "
                    "applied is unknown.",
                )
            raise MetaAPIError(502, None, "Meta returned a non-JSON body")

        # Meta occasionally answers 200 with an error envelope.
        if _as_dict(body).get("error"):
            error = self._error_from_body(response.status_code, body, response.headers)
            logger.warning(event, path=path, code=error.code, fbtrace_id=error.fbtrace_id)
            raise error

        return _as_dict(body)

    # ----------------------------------------------------------------- reads

    async def get_entity(
        self,
        entity_type: MetaEntityType,
        entity_id: str,
        fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """
        Read an entity's current state from Meta.

        Every action's ``before_value`` comes from here. An action whose
        current state cannot be read must be refused, never executed on an
        assumed value: that assumption is exactly what the simulator this
        replaces used to write into the audit log.

        Args:
            entity_type: Which node is being read.
            entity_id: Meta id of the node. For an ad account this is the
                ``act_<id>`` form.
            fields: Field list to request; defaults to
                :data:`DEFAULT_READ_FIELDS` for the entity type.

        Returns:
            The parsed entity as Meta returned it. Money and numeric fields
            are strings, as Meta sends them.

        Raises:
            ValueError: ``entity_id`` is empty.
            MetaTokenError: the token was rejected.
            MetaRateLimitError: Meta is throttling.
            MetaWriteValidationError: Meta rejected the read as invalid
                (unknown id, missing permission).
            MetaAPIError: any other non-2xx, transport failure or non-JSON body.
        """
        identifier = str(entity_id or "").strip()
        if not identifier:
            raise ValueError("entity_id is required")

        requested = tuple(fields) if fields else DEFAULT_READ_FIELDS[entity_type]
        path = f"/{identifier}"
        http = self._get_http()
        try:
            response = await http.get(path, params={"fields": ",".join(requested)})
        except httpx.HTTPError as exc:
            # A read is side-effect free, so a transport failure is an
            # ordinary error rather than an ambiguous outcome.
            detail = self._redact(f"Meta Graph API unreachable: {exc}")
            logger.error("meta_write_read_failed", path=path, error=detail)
            raise MetaAPIError(502, None, detail) from exc

        return self._check_response(path, response, "meta_write_read_api_error")

    # ---------------------------------------------------------------- writes

    def _validate_changes(
        self, entity_type: MetaEntityType, changes: Mapping[str, Any]
    ) -> dict[str, str]:
        """
        Check a change set against the allowlists and render it for the wire.

        Args:
            entity_type: Node being updated.
            changes: Field -> value mapping.

        Returns:
            The same changes as form values (Meta takes everything as strings).

        Raises:
            ValueError: the entity type is not writable, the change set is
                empty, a field is not writable for that entity type, a status
                is outside :data:`WRITABLE_STATUSES`, or a money field is not
                a non-negative integer already in Meta API units.
        """
        if entity_type not in WRITABLE_ENTITY_TYPES:
            raise ValueError(f"{entity_type.value} is not a writable entity type")
        if not changes:
            raise ValueError("changes must not be empty")

        allowed = WRITABLE_FIELDS[entity_type]
        rendered: dict[str, str] = {}
        for field, value in changes.items():
            if field not in allowed:
                raise ValueError(
                    f"{field!r} is not writable on a {entity_type.value}; "
                    f"allowed: {sorted(allowed)}"
                )
            if field == "status":
                status = str(value).upper()
                if status not in WRITABLE_STATUSES:
                    raise ValueError(
                        f"status {status!r} is not writable by autopilot; "
                        f"allowed: {sorted(WRITABLE_STATUSES)}"
                    )
                rendered[field] = status
                continue
            if field in MONEY_FIELDS:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError(
                        f"{field} must be an int in Meta API units "
                        "(use major_to_meta_minor); "
                        f"got {type(value).__name__}"
                    )
                if value < 0:
                    raise ValueError(f"{field} must not be negative")
                rendered[field] = str(value)
                continue
            rendered[field] = str(value)
        return rendered

    async def update_entity(
        self,
        entity_type: MetaEntityType,
        entity_id: str,
        changes: Mapping[str, Any],
    ) -> dict[str, Any]:
        """
        Apply an update to a campaign, ad set or ad.

        Issues a single ``POST /{entity_id}`` with the change set as form
        fields. It is never retried here and must never be retried by a
        caller: see :class:`MetaWriteAmbiguousError`.

        Args:
            entity_type: Node being updated (campaign, adset or ad).
            entity_id: Meta id of the node.
            changes: Allowlisted fields to set. Money fields must already be
                integers in Meta API units - convert with
                :func:`major_to_meta_minor` and nowhere else.

        Returns:
            Meta's response body, typically ``{"success": true}``.

        Raises:
            ValueError: the change set failed local validation (nothing sent).
            MetaWriteAmbiguousError: the outcome cannot be established - the
                request left this process and no response arrived, Meta
                answered a transient 5xx, or it answered 2xx with a body this
                client cannot parse. Reconcile by re-reading, do not retry.
            MetaTokenError: the token was rejected (nothing was applied).
            MetaRateLimitError: Meta is throttling (nothing was applied).
            MetaWriteValidationError: Meta rejected the change permanently.
            MetaAPIError: any other error Meta stated definitively.
        """
        identifier = str(entity_id or "").strip()
        if not identifier:
            raise ValueError("entity_id is required")
        payload = self._validate_changes(entity_type, changes)

        path = f"/{identifier}"
        http = self._get_http()
        # Nothing about the token is logged; the fields and the entity are.
        logger.info(
            "meta_write_update",
            entity_type=entity_type.value,
            entity_id=identifier,
            fields=sorted(payload),
        )
        try:
            response = await http.post(path, data=payload)
        except httpx.HTTPError as exc:
            detail = self._redact(f"{type(exc).__name__}: {exc}")
            logger.error(
                "meta_write_update_ambiguous",
                entity_type=entity_type.value,
                entity_id=identifier,
                error=detail,
            )
            raise MetaWriteAmbiguousError(entity_type.value, identifier, detail) from exc

        return self._check_response(
            path,
            response,
            "meta_write_update_api_error",
            write_target=(entity_type.value, identifier),
        )
