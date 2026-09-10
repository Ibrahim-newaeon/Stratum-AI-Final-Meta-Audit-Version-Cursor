# =============================================================================
# Stratum AI - Meta Campaign Discovery Client (read-only)
# =============================================================================
"""
Thin ``httpx.AsyncClient`` over ``GET /act_<id>/campaigns``.

READ-ONLY BY CONSTRUCTION: the only HTTP verb this module ever issues is GET.
It discovers campaign metadata so Stratum can upsert local ``Campaign`` rows;
it never creates, updates, pauses or deletes anything on Meta. Permission
required: ``ads_read`` (same as insights). Autopilot writes stay in
``write_client.py``.

Contract (Marketing API Ad Account → Campaigns edge):

    GET https://graph.facebook.com/<version>/act_<id>/campaigns
        fields=id,name,status,effective_status,objective,start_time,stop_time,
               updated_time,daily_budget,lifetime_budget
        limit=<page size>

Pagination follows ``paging.next`` via ``paging.cursors.after``, matching the
insights client. Hitting the page cap while Meta still has pages raises
``MetaCampaignsTruncatedError`` so callers cannot treat a partial catalogue as
complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Self, Sequence

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.services.meta.insights_client import (
    DEFAULT_RATE_LIMIT_BACKOFF_SECONDS,
    GRAPH_API_BASE_URL,
    RATE_LIMIT_ERROR_CODES,
    TOKEN_ERROR_CODES,
    TOKEN_ERROR_SUBCODES,
    MetaAPIError,
    MetaRateLimitError,
    MetaTokenError,
    _as_dict,
    _as_list,
    graph_api_version,
)

logger = get_logger(__name__)

#: Fields requested for each campaign node.
CAMPAIGN_DISCOVERY_FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "status",
    "effective_status",
    "objective",
    "start_time",
    "stop_time",
    "updated_time",
    "daily_budget",
    "lifetime_budget",
)

#: Rows requested per page.
CAMPAIGNS_PAGE_LIMIT = 100


class MetaCampaignsTruncatedError(Exception):
    """
    Raised when the pagination page cap was hit while Meta still had pages.

    Deliberately NOT a ``MetaAPIError``: nothing is wrong with Meta and a
    retry would truncate identically. Callers must not upsert a partial
    catalogue as a successful full discovery for that ad account.
    """

    def __init__(self, ad_account_id: str, max_pages: int, rows: int) -> None:
        self.ad_account_id = ad_account_id
        self.max_pages = max_pages
        self.rows = rows
        super().__init__(
            f"Meta campaigns pull for {ad_account_id} truncated after "
            f"{max_pages} pages ({rows} campaigns fetched); raise "
            f"META_INSIGHTS_MAX_PAGES or narrow the account"
        )


@dataclass(frozen=True)
class MetaCampaignRow:
    """
    One campaign node from the Ad Account campaigns edge.

    Budgets are kept as Meta's raw API-unit strings (or None). They are NOT
    converted into ``*_cents`` here: Meta offset units differ from this
    schema's hundredths-of-major by 100x for zero-decimal currencies, and
    discovery must not invent wrong money.
    """

    external_id: str
    name: str
    status: Optional[str]
    effective_status: Optional[str]
    objective: Optional[str]
    start_time: Optional[str]
    stop_time: Optional[str]
    updated_time: Optional[str]
    daily_budget: Optional[str]
    lifetime_budget: Optional[str]
    raw: dict[str, Any]


def row_from_campaign_payload(payload: dict[str, Any]) -> Optional[MetaCampaignRow]:
    """
    Parse one campaigns-edge object into a ``MetaCampaignRow``.

    Returns:
        The row, or ``None`` when ``id`` is missing/blank (unusable for upsert).
    """
    external_id = str(payload.get("id") or "").strip()
    if not external_id:
        return None

    name = str(payload.get("name") or "").strip() or f"Campaign {external_id}"

    def _opt_str(key: str) -> Optional[str]:
        value = payload.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    return MetaCampaignRow(
        external_id=external_id,
        name=name,
        status=_opt_str("status"),
        effective_status=_opt_str("effective_status"),
        objective=_opt_str("objective"),
        start_time=_opt_str("start_time"),
        stop_time=_opt_str("stop_time"),
        updated_time=_opt_str("updated_time"),
        daily_budget=_opt_str("daily_budget"),
        lifetime_budget=_opt_str("lifetime_budget"),
        raw=dict(payload),
    )


class MetaCampaignDiscoveryClient:
    """
    Read-only ``httpx.AsyncClient`` wrapper over the Ad Account campaigns edge.

    Args:
        access_token: Meta user or system-user token with ``ads_read``.
            Sent as ``Authorization: Bearer`` - never in the query string.
        api_version: Graph API version (defaults to
            ``settings.meta_graph_api_version``).
        timeout: Per-request timeout in seconds.
        max_pages: Page cap for the pagination loop.
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
        self._transport = transport
        self._http: Optional[httpx.AsyncClient] = None

    @property
    def api_version(self) -> str:
        """Graph API version this client talks to."""
        return self._api_version

    @property
    def base_url(self) -> str:
        """Versioned Graph API base URL."""
        return f"{GRAPH_API_BASE_URL}/{self._api_version}"

    def _get_http(self) -> httpx.AsyncClient:
        """Lazily build the underlying ``httpx.AsyncClient``."""
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

    def _redact(self, text: str) -> str:
        """Remove the access token from ``text`` if it somehow appears."""
        if self._access_token and self._access_token in text:
            return text.replace(self._access_token, "***REDACTED***")
        return text

    @staticmethod
    def _retry_after_from_headers(headers: httpx.Headers) -> int:
        """Read the back-off hint out of the response headers."""
        import json

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
        """Translate a Graph API error envelope into the right exception type."""
        error = _as_dict(_as_dict(body).get("error"))
        raw_code = error.get("code")
        code: Optional[int]
        try:
            code = int(raw_code) if raw_code is not None else None
        except (TypeError, ValueError):
            code = None

        raw_subcode = error.get("error_subcode")
        subcode: Optional[int]
        try:
            subcode = int(raw_subcode) if raw_subcode is not None else None
        except (TypeError, ValueError):
            subcode = None

        message = self._redact(
            str(error.get("message") or f"Meta HTTP {status_code}")
        )
        fbtrace_id = error.get("fbtrace_id")
        fbtrace = str(fbtrace_id) if fbtrace_id else None

        if code in TOKEN_ERROR_CODES or (
            subcode is not None and subcode in TOKEN_ERROR_SUBCODES
        ):
            return MetaTokenError(
                status_code, code, message, subcode=subcode, fbtrace_id=fbtrace
            )

        if status_code == 429 or code in RATE_LIMIT_ERROR_CODES:
            return MetaRateLimitError(
                status_code,
                code,
                message,
                subcode=subcode,
                fbtrace_id=fbtrace,
                retry_after_seconds=self._retry_after_from_headers(headers),
            )

        return MetaAPIError(
            status_code, code, message, subcode=subcode, fbtrace_id=fbtrace
        )

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """Issue one GET and return the parsed JSON body."""
        http = self._get_http()
        try:
            response = await http.get(path, params=params)
        except httpx.HTTPError as exc:
            detail = self._redact(f"Meta Graph API unreachable: {exc}")
            logger.error("meta_campaigns_request_failed", path=path, error=detail)
            raise MetaAPIError(502, None, detail) from exc

        try:
            body = response.json()
        except ValueError:
            body = None

        if response.status_code < 200 or response.status_code >= 300:
            error = self._error_from_body(response.status_code, body, response.headers)
            logger.warning(
                "meta_campaigns_api_error",
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

        if _as_dict(body).get("error"):
            error = self._error_from_body(response.status_code, body, response.headers)
            logger.warning(
                "meta_campaigns_api_error_in_200",
                path=path,
                code=error.code,
                fbtrace_id=error.fbtrace_id,
            )
            raise error

        return _as_dict(body)

    async def list_campaigns(self, ad_account_id: str) -> list[MetaCampaignRow]:
        """
        List campaigns for one ad account (``GET /act_<id>/campaigns``).

        Args:
            ad_account_id: Ad account id, with or without the ``act_`` prefix.

        Returns:
            Parsed campaign rows in Meta's order.

        Raises:
            ValueError: ``ad_account_id`` is empty.
            MetaTokenError / MetaRateLimitError / MetaAPIError: Graph failures.
            MetaCampaignsTruncatedError: page cap hit with more pages available.
        """
        account = str(ad_account_id or "").strip()
        if not account:
            raise ValueError("ad_account_id is required")
        if not account.startswith("act_"):
            account = f"act_{account}"

        params: dict[str, Any] = {
            "fields": ",".join(CAMPAIGN_DISCOVERY_FIELDS),
            "limit": CAMPAIGNS_PAGE_LIMIT,
        }

        rows: list[MetaCampaignRow] = []
        path = f"/{account}/campaigns"
        after: Optional[str] = None
        pages = 0

        while pages < self._max_pages:
            page_params = dict(params)
            if after:
                page_params["after"] = after

            body = await self._get(path, page_params)
            pages += 1

            for payload in _as_list(body.get("data")):
                row = row_from_campaign_payload(_as_dict(payload))
                if row is not None:
                    rows.append(row)

            paging = _as_dict(body.get("paging"))
            if not paging.get("next"):
                break
            after = _as_dict(paging.get("cursors")).get("after")
            if not after:
                break
        else:
            logger.error(
                "meta_campaigns_page_cap_reached",
                ad_account_id=account,
                max_pages=self._max_pages,
                rows=len(rows),
            )
            raise MetaCampaignsTruncatedError(account, self._max_pages, len(rows))

        logger.info(
            "meta_campaigns_fetched",
            ad_account_id=account,
            pages=pages,
            rows=len(rows),
        )
        return rows
