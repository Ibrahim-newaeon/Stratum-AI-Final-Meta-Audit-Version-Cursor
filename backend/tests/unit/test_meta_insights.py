# =============================================================================
# Stratum AI - Meta Insights Ingestion Tests (read-only)
# =============================================================================
"""
Unit tests for ``app.services.meta`` and the real path of
``app.workers.tasks.sync.sync_campaign_data``.

No network, no database: HTTP goes through ``httpx.MockTransport`` and the
session is an in-memory fake that really stores CampaignMetric rows, so
idempotency can be asserted rather than mimed.

What is pinned here:

* a realistic multi-day payload maps to exact CampaignMetric values,
* pagination is followed and the page cap stops a runaway cursor loop,
* re-ingesting a day updates instead of duplicating,
* code 190 raises MetaTokenError and the task disconnects the connection,
* a throttling code raises MetaRateLimitError and writes no partial data,
* a missing/expired/unusable credential writes nothing and reports no success,
* conversions and revenue come from the *configured* action types only,
* zero-decimal currencies convert correctly,
* the access token never reaches a log record or an exception message.
"""

import json
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Optional, Self

import httpx
import pytest
import structlog

import app.models  # noqa: F401  (registers every mapper so ORM statements compile)
from app.core.config import settings
from app.models import (
    Campaign,
    CampaignMetric,
    ConnectionStatus,
    TenantAdAccount,
    TenantPlatformConnection,
)
from app.services.encryption import encrypt_token
from app.services.meta import insights_ingestion
from app.services.meta.insights_client import (
    INSIGHTS_FIELDS,
    RATE_LIMIT_ERROR_CODES,
    TOKEN_ERROR_CODES,
    VIDEO_VIEW_ACTION_TYPE,
    MetaAPIError,
    MetaInsightsClient,
    MetaInsightsTruncatedError,
    MetaRateLimitError,
    MetaTokenError,
    first_present_action_type,
    row_from_payload,
    sum_action_values,
    sum_one_action_type,
)
from app.services.meta.insights_ingestion import (
    ZERO_DECIMAL_CURRENCIES,
    MetaCredentials,
    MetaCredentialsError,
    fetch_campaign_insight_rows,
    ingest_campaign_insights,
    insights_window,
    metric_values_from_row,
    minor_unit_exponent,
    resolve_meta_credentials,
    to_hundredths,
)
from app.workers.tasks import sync as sync_task

pytestmark = pytest.mark.unit

TOKEN = "EAAG_super_secret_meta_system_user_token_do_not_leak"
AD_ACCOUNT = "act_1234567890"
CAMPAIGN_EXTERNAL_ID = "23847562910380123"
TENANT_ID = 7
CAMPAIGN_ID = 42

PURCHASE = "offsite_conversion.fb_pixel_purchase"
OMNI_PURCHASE = "omni_purchase"
VIDEO_VIEW = "video_view"


# =============================================================================
# Payload builders
# =============================================================================


def insight_payload(
    day: str,
    *,
    campaign_id: str = CAMPAIGN_EXTERNAL_ID,
    impressions: str = "10000",
    clicks: str = "250",
    spend: str = "123.45",
    conversions: str = "12",
    revenue: str = "678.90",
    currency: str = "USD",
    extra_actions: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    """
    Build one realistic Ads Insights row exactly as Meta returns it.

    Every numeric field is a string, and actions / action_values are
    AdsActionStats objects, matching the documented response shape.
    """
    actions = [
        {"action_type": "link_click", "value": clicks},
        {"action_type": "landing_page_view", "value": "180"},
        # 3-second video views: the documented action type CampaignMetric
        # .video_views is named after, NOT a watch-depth threshold.
        {"action_type": VIDEO_VIEW, "value": "900"},
        {"action_type": PURCHASE, "value": conversions, "7d_click": conversions},
    ]
    if extra_actions:
        actions.extend(extra_actions)

    return {
        "campaign_id": campaign_id,
        "campaign_name": "Prospecting | Broad | US",
        "impressions": impressions,
        "clicks": clicks,
        "spend": spend,
        "ctr": "2.5",
        "cpc": "0.4938",
        "cpm": "12.345",
        "account_currency": currency,
        "actions": actions,
        "action_values": [
            {"action_type": "link_click", "value": "0"},
            {"action_type": PURCHASE, "value": revenue, "7d_click": revenue},
        ],
        # Watch-depth thresholds. Only p100 is requested (video_completions);
        # p25 is deliberately smaller than video_view so a regression that
        # reads views from the wrong field is visible.
        "video_p25_watched_actions": [{"action_type": VIDEO_VIEW, "value": "700"}],
        "video_p100_watched_actions": [{"action_type": VIDEO_VIEW, "value": "150"}],
        "date_start": day,
        "date_stop": day,
    }


def json_response(
    payloads: list[dict[str, Any]], *, next_cursor: Optional[str] = None
) -> httpx.Response:
    """Wrap insight payloads in Meta's ``{"data": ..., "paging": ...}`` envelope."""
    body: dict[str, Any] = {"data": payloads}
    cursors = {"before": "BEFORE_CURSOR", "after": next_cursor or "AFTER_CURSOR"}
    body["paging"] = {"cursors": cursors}
    if next_cursor:
        # Meta returns "next" only when another page exists; "cursors" is
        # always present, which is exactly the trap this pins.
        body["paging"]["next"] = f"https://graph.facebook.com/next?after={next_cursor}"
    return httpx.Response(200, json=body)


def error_response(
    status: int,
    code: int,
    *,
    subcode: Optional[int] = None,
    message: str = "Something went wrong",
    headers: Optional[dict[str, str]] = None,
) -> httpx.Response:
    """Build a Graph API error envelope response."""
    error: dict[str, Any] = {
        "message": message,
        "type": "OAuthException",
        "code": code,
        "fbtrace_id": "AbCdEfGhIjK",
    }
    if subcode is not None:
        error["error_subcode"] = subcode
    return httpx.Response(status, json={"error": error}, headers=headers or {})


# =============================================================================
# Fake synchronous session
# =============================================================================


class _ScalarResult:
    """Stand-in for a SQLAlchemy ``ScalarResult``."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        """Return every row."""
        return list(self._rows)

    def first(self) -> Optional[Any]:
        """Return the first row, or None."""
        return self._rows[0] if self._rows else None


class _Result:
    """Stand-in for a SQLAlchemy ``Result``."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Optional[Any]:
        """Return the single row, or None."""
        if len(self._rows) > 1:
            raise AssertionError("scalar_one_or_none() got more than one row")
        return self._rows[0] if self._rows else None

    def scalars(self) -> _ScalarResult:
        """Return a scalar result view."""
        return _ScalarResult(self._rows)

    def one_or_none(self) -> Optional[Any]:
        """Return the single row tuple, or None."""
        return self._rows[0] if self._rows else None


def _selected_entity(statement: Any) -> Optional[type]:
    """
    Return the ORM entity a ``select()`` targets, or None for an aggregate.

    ``select(func.sum(CampaignMetric.impressions))`` still reports
    ``entity=CampaignMetric``, so the entity only counts when the selected
    expression *is* the entity itself (``select(CampaignMetric)``).
    """
    for description in statement.column_descriptions:
        entity = description.get("entity")
        if entity is not None and description.get("expr") is entity:
            return entity
    return None


class FakeSession:
    """
    In-memory stand-in for ``SyncSessionLocal()``.

    CampaignMetric rows are really stored and really looked up by
    ``(campaign_id, date)``, so the idempotency test exercises the upsert
    instead of trusting it.

    Crucially it reproduces ``autoflush=False`` (which is how
    ``SyncSessionLocal`` is actually configured, see ``app/db/session.py``):
    a row handed to ``add()`` lands in ``_pending`` and is **invisible to
    every query** until ``flush()`` (or ``commit()``) moves it into
    ``metrics``. A fake that made ``add()`` immediately queryable would hide
    the real bug this pins - the campaign aggregate SUM running before the
    window it is meant to include.
    """

    def __init__(
        self,
        *,
        campaign: Optional[Campaign] = None,
        connection: Optional[TenantPlatformConnection] = None,
        ad_account: Optional[TenantAdAccount] = None,
    ) -> None:
        self.campaign = campaign
        self.connection = connection
        self.ad_account = ad_account
        self.metrics: list[CampaignMetric] = []
        self._pending: list[CampaignMetric] = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0

    # -- context manager ---------------------------------------------------

    def __call__(self) -> Self:
        """Allow the instance itself to stand in for ``SyncSessionLocal``."""
        return self

    def __enter__(self) -> Self:
        """Enter the ``with SyncSessionLocal() as db`` block."""
        return self

    def __exit__(self, *exc_info: object) -> bool:
        """Leave the block without swallowing exceptions."""
        return False

    # -- session API -------------------------------------------------------

    def execute(self, statement: Any) -> _Result:
        """Answer the handful of queries the ingestion path issues."""
        entity = _selected_entity(statement)
        params = statement.compile().params

        if entity is Campaign:
            return _Result([self.campaign] if self.campaign is not None else [])
        if entity is TenantPlatformConnection:
            return _Result([self.connection] if self.connection is not None else [])
        if entity is TenantAdAccount:
            return _Result([self.ad_account] if self.ad_account is not None else [])
        if entity is CampaignMetric:
            campaign_id = params.get("campaign_id_1")
            wanted = params.get("date_1")
            matches = [
                metric
                for metric in self.metrics
                if metric.campaign_id == campaign_id and metric.date == wanted
            ]
            return _Result(matches)

        # No entity => the aggregate roll-up over CampaignMetric.
        rows = [m for m in self.metrics if m.campaign_id == params.get("campaign_id_1")]
        return _Result(
            [
                (
                    sum(m.impressions or 0 for m in rows),
                    sum(m.clicks or 0 for m in rows),
                    sum(m.conversions or 0 for m in rows),
                    sum(m.spend_cents or 0 for m in rows),
                    sum(m.revenue_cents or 0 for m in rows),
                )
            ]
        )

    def add(self, obj: Any) -> None:
        """Buffer an insert; it stays invisible to queries until flush()."""
        if isinstance(obj, CampaignMetric):
            self._pending.append(obj)

    def flush(self) -> None:
        """Make pending inserts visible to subsequent queries."""
        self.flushes += 1
        self.metrics.extend(self._pending)
        self._pending.clear()

    def commit(self) -> None:
        """Record a commit (a real session flushes on commit)."""
        self.flush()
        self.commits += 1

    def rollback(self) -> None:
        """Discard anything still pending, as a real rollback would."""
        self._pending.clear()
        self.rollbacks += 1


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def meta_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the Meta insights settings so tests do not depend on the .env."""
    monkeypatch.setattr(settings, "meta_graph_api_version", "v23.0")
    monkeypatch.setattr(settings, "meta_insights_lookback_days", 3)
    monkeypatch.setattr(settings, "meta_insights_request_timeout_seconds", 5.0)
    monkeypatch.setattr(settings, "meta_insights_max_pages", 25)
    monkeypatch.setattr(
        settings, "meta_conversion_action_types", f"{PURCHASE},purchase"
    )
    monkeypatch.setattr(settings, "use_mock_ad_data", False)


@pytest.fixture
def campaign() -> Campaign:
    """A Meta campaign with no sync history yet."""
    return Campaign(
        id=CAMPAIGN_ID,
        tenant_id=TENANT_ID,
        platform="meta",
        external_id=CAMPAIGN_EXTERNAL_ID,
        account_id=AD_ACCOUNT,
        name="Prospecting | Broad | US",
        currency="USD",
        last_synced_at=None,
        sync_error=None,
    )


@pytest.fixture
def connection() -> TenantPlatformConnection:
    """A healthy, connected Meta platform connection holding an encrypted token."""
    return TenantPlatformConnection(
        tenant_id=TENANT_ID,
        platform="meta",
        status=ConnectionStatus.CONNECTED.value,
        access_token_encrypted=encrypt_token(TOKEN),
        token_expires_at=datetime.now(UTC) + timedelta(days=30),
        error_count=0,
    )


@pytest.fixture
def credentials() -> MetaCredentials:
    """Resolved credentials pointing at the fixture ad account."""
    return MetaCredentials(
        access_token=TOKEN,
        ad_account_id=AD_ACCOUNT,
        currency="USD",
        connection_id=None,
    )


# =============================================================================
# Client: request shape
# =============================================================================


class TestRequestShape:
    """The client must send the documented read-only insights request."""

    def test_get_only_with_documented_parameters(self, meta_settings):
        """level=campaign, time_increment=1, JSON time_range, field list, GET."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return json_response([insight_payload("2026-08-31")])

        credentials = MetaCredentials(TOKEN, AD_ACCOUNT, "USD", None)
        fetch_campaign_insight_rows(
            credentials,
            date(2026, 8, 29),
            date(2026, 8, 31),
            transport=httpx.MockTransport(handler),
        )

        assert len(seen) == 1
        request = seen[0]
        assert request.method == "GET"
        assert request.url.path == f"/v23.0/{AD_ACCOUNT}/insights"

        params = request.url.params
        assert params["level"] == "campaign"
        assert params["time_increment"] == "1"
        assert json.loads(params["time_range"]) == {
            "since": "2026-08-29",
            "until": "2026-08-31",
        }
        for field_name in INSIGHTS_FIELDS:
            assert field_name in params["fields"]

    def test_token_travels_in_the_authorization_header_not_the_url(self, meta_settings):
        """A token in the query string leaks into every access log on the path."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return json_response([])

        credentials = MetaCredentials(TOKEN, AD_ACCOUNT, "USD", None)
        fetch_campaign_insight_rows(
            credentials,
            date(2026, 8, 29),
            date(2026, 8, 31),
            transport=httpx.MockTransport(handler),
        )

        request = seen[0]
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in str(request.url)
        assert "access_token" not in request.url.params

    def test_campaign_filter_is_applied_server_side(self, meta_settings):
        """A per-campaign task must not download the tenant's whole ad account."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return json_response([insight_payload("2026-08-29")])

        credentials = MetaCredentials(TOKEN, AD_ACCOUNT, "USD", None)
        fetch_campaign_insight_rows(
            credentials,
            date(2026, 8, 29),
            date(2026, 8, 29),
            external_campaign_id=CAMPAIGN_EXTERNAL_ID,
            transport=httpx.MockTransport(handler),
        )

        assert json.loads(seen[0].url.params["filtering"]) == [
            {
                "field": "campaign.id",
                "operator": "IN",
                "value": [CAMPAIGN_EXTERNAL_ID],
            }
        ]

    def test_rows_for_another_campaign_are_dropped_locally(self, meta_settings):
        """Belt and braces behind the server-side filter."""

        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(
                [
                    insight_payload("2026-08-29"),
                    insight_payload("2026-08-29", campaign_id="99999999999"),
                ]
            )

        credentials = MetaCredentials(TOKEN, AD_ACCOUNT, "USD", None)
        rows = fetch_campaign_insight_rows(
            credentials,
            date(2026, 8, 29),
            date(2026, 8, 29),
            external_campaign_id=CAMPAIGN_EXTERNAL_ID,
            transport=httpx.MockTransport(handler),
        )

        assert [row.campaign_id for row in rows] == [CAMPAIGN_EXTERNAL_ID]

    def test_no_filter_when_no_campaign_is_named(self, meta_settings):
        """An account-wide pull must not carry an empty filter."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return json_response([])

        credentials = MetaCredentials(TOKEN, AD_ACCOUNT, "USD", None)
        fetch_campaign_insight_rows(
            credentials,
            date(2026, 8, 29),
            date(2026, 8, 29),
            transport=httpx.MockTransport(handler),
        )

        assert "filtering" not in seen[0].url.params

    def test_bare_account_id_gets_the_act_prefix(self, meta_settings):
        """Callers may pass ``1234`` or ``act_1234``."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return json_response([])

        credentials = MetaCredentials(TOKEN, "1234567890", "USD", None)
        fetch_campaign_insight_rows(
            credentials,
            date(2026, 8, 29),
            date(2026, 8, 31),
            transport=httpx.MockTransport(handler),
        )

        assert seen[0].url.path == "/v23.0/act_1234567890/insights"


# =============================================================================
# Client: parsing
# =============================================================================


class TestRowParsing:
    """Meta returns strings; the row must carry typed, exact values."""

    def test_multi_day_payload_parses_exactly(self, meta_settings):
        """Three days in, three typed rows out, money as Decimal."""
        days = ["2026-08-29", "2026-08-30", "2026-08-31"]

        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(
                [
                    insight_payload(days[0], spend="123.45", revenue="678.90"),
                    insight_payload(days[1], spend="99.99", revenue="0"),
                    insight_payload(days[2], spend="0.01", revenue="1000.005"),
                ]
            )

        credentials = MetaCredentials(TOKEN, AD_ACCOUNT, "USD", None)
        rows = fetch_campaign_insight_rows(
            credentials,
            date(2026, 8, 29),
            date(2026, 8, 31),
            transport=httpx.MockTransport(handler),
        )

        assert [row.date for row in rows] == [
            date(2026, 8, 29),
            date(2026, 8, 30),
            date(2026, 8, 31),
        ]
        assert rows[0].impressions == 10000
        assert rows[0].clicks == 250
        assert rows[0].spend == Decimal("123.45")
        assert rows[0].conversions == 12
        assert rows[0].conversion_value == Decimal("678.90")
        assert rows[0].video_views == 900
        assert rows[0].video_completions == 150
        assert rows[0].currency == "USD"
        assert rows[0].campaign_id == CAMPAIGN_EXTERNAL_ID
        assert isinstance(rows[0].spend, Decimal)

    def test_row_without_campaign_id_or_date_is_dropped(self, meta_settings):
        """A row that cannot key a CampaignMetric must not become one."""
        assert row_from_payload({"impressions": "10"}, [PURCHASE]) is None
        assert (
            row_from_payload(
                {"campaign_id": "1", "impressions": "10"}, [PURCHASE]
            )
            is None
        )

    def test_missing_video_fields_stay_none(self, meta_settings):
        """Absent video breakdowns are unknown, not zero."""
        payload = insight_payload("2026-08-29")
        payload["actions"] = [
            action
            for action in payload["actions"]
            if action["action_type"] != VIDEO_VIEW
        ]
        del payload["video_p100_watched_actions"]

        row = row_from_payload(payload, [PURCHASE])

        assert row is not None
        assert row.video_views is None
        assert row.video_completions is None

    def test_video_views_come_from_the_video_view_action_type(self, meta_settings):
        """
        video_views is 3-second views, not the 25%-watched threshold.

        Meta documents ``video_view`` as "3-Second Video Views" and
        ``video_p25_watched_actions`` as a watch-depth count, which is a
        different and always smaller number. Reading the wrong one made every
        video figure on the dashboard silently low.
        """
        payload = insight_payload("2026-08-29")
        assert payload["video_p25_watched_actions"][0]["value"] == "700"

        row = row_from_payload(payload, [PURCHASE])

        assert row is not None
        assert row.video_views == 900
        assert row.video_completions == 150

    def test_watch_depth_fields_are_not_requested_beyond_p100(self):
        """Only the p100 threshold is pulled; p25/p50/p75 feed nothing."""
        assert "video_p100_watched_actions" in INSIGHTS_FIELDS
        assert "video_p25_watched_actions" not in INSIGHTS_FIELDS
        assert "video_p50_watched_actions" not in INSIGHTS_FIELDS
        assert "video_p75_watched_actions" not in INSIGHTS_FIELDS
        assert VIDEO_VIEW_ACTION_TYPE == VIDEO_VIEW


# =============================================================================
# Client: pagination
# =============================================================================


class TestPagination:
    """paging.next drives the loop; the page cap ends a runaway."""

    def test_follows_pages_until_next_is_absent(self, meta_settings):
        """Three pages, the last without ``next``, all rows collected once."""
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            after = request.url.params.get("after")
            if after is None:
                return json_response(
                    [insight_payload("2026-08-29")], next_cursor="CURSOR_1"
                )
            if after == "CURSOR_1":
                return json_response(
                    [insight_payload("2026-08-30")], next_cursor="CURSOR_2"
                )
            return json_response([insight_payload("2026-08-31")])

        credentials = MetaCredentials(TOKEN, AD_ACCOUNT, "USD", None)
        rows = fetch_campaign_insight_rows(
            credentials,
            date(2026, 8, 29),
            date(2026, 8, 31),
            transport=httpx.MockTransport(handler),
        )

        assert len(requests) == 3
        assert requests[1].url.params["after"] == "CURSOR_1"
        assert requests[2].url.params["after"] == "CURSOR_2"
        assert len(rows) == 3

    def test_cursors_without_next_do_not_loop(self, meta_settings):
        """Meta returns cursors on the final page too - that must not re-request."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return json_response([insight_payload("2026-08-29")])

        credentials = MetaCredentials(TOKEN, AD_ACCOUNT, "USD", None)
        rows = fetch_campaign_insight_rows(
            credentials,
            date(2026, 8, 29),
            date(2026, 8, 29),
            transport=httpx.MockTransport(handler),
        )

        assert calls["n"] == 1
        assert len(rows) == 1

    def test_page_cap_stops_a_runaway(self, meta_settings, monkeypatch):
        """
        A server that always says "there is more" must not spin forever.

        And it must not quietly hand back the pages it did get: a truncated
        window is incomplete data, so it raises instead of returning a short
        list that the caller would report as a fresh, successful sync.
        """
        monkeypatch.setattr(settings, "meta_insights_max_pages", 4)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return json_response(
                [insight_payload("2026-08-29"), insight_payload("2026-08-30")],
                next_cursor=f"CURSOR_{calls['n']}",
            )

        credentials = MetaCredentials(TOKEN, AD_ACCOUNT, "USD", None)
        with pytest.raises(MetaInsightsTruncatedError) as excinfo:
            fetch_campaign_insight_rows(
                credentials,
                date(2026, 8, 29),
                date(2026, 8, 30),
                transport=httpx.MockTransport(handler),
            )

        assert calls["n"] == 4
        assert excinfo.value.max_pages == 4
        assert excinfo.value.rows == 8
        assert TOKEN not in str(excinfo.value)

    def test_truncation_is_not_a_meta_api_error(self):
        """
        Truncation must not be caught by the generic API-error retry branch.

        Retrying a page-capped pull truncates identically, so it deliberately
        sits outside the MetaAPIError hierarchy that Celery retries.
        """
        assert not issubclass(MetaInsightsTruncatedError, MetaAPIError)


# =============================================================================
# Client: errors
# =============================================================================


class TestErrorMapping:
    """Graph API error codes must become actionable, distinct exceptions."""

    def _fetch(self, response: httpx.Response, credentials: MetaCredentials) -> None:
        """Run one fetch against a canned response."""
        fetch_campaign_insight_rows(
            credentials,
            date(2026, 8, 29),
            date(2026, 8, 29),
            transport=httpx.MockTransport(lambda request: response),
        )

    def test_code_190_raises_meta_token_error(self, meta_settings, credentials):
        """An expired/invalid token must be distinguishable from any other failure."""
        with pytest.raises(MetaTokenError) as exc_info:
            self._fetch(
                error_response(
                    401,
                    190,
                    subcode=463,
                    message="Error validating access token: Session has expired",
                ),
                credentials,
            )

        error = exc_info.value
        assert error.code == 190
        assert error.subcode == 463
        assert error.status_code == 401
        assert "Session has expired" in error.message

    @pytest.mark.parametrize("code", [4, 17, 32, 613, 80004])
    def test_rate_limit_codes_raise_meta_rate_limit_error(
        self, meta_settings, credentials, code
    ):
        """Throttling must be retried with a back-off, not treated as a hard failure."""
        with pytest.raises(MetaRateLimitError) as exc_info:
            self._fetch(
                error_response(400, code, message="User request limit reached"),
                credentials,
            )

        assert exc_info.value.code == code
        assert exc_info.value.retry_after_seconds > 0

    def test_retry_after_read_from_business_use_case_header(
        self, meta_settings, credentials
    ):
        """estimated_time_to_regain_access is in minutes and must be honoured."""
        header = json.dumps(
            {
                "51234": [
                    {
                        "type": "ads_insights",
                        "call_count": 100,
                        "estimated_time_to_regain_access": 12,
                    }
                ]
            }
        )
        with pytest.raises(MetaRateLimitError) as exc_info:
            self._fetch(
                error_response(
                    400,
                    4,
                    headers={"X-Business-Use-Case-Usage": header},
                ),
                credentials,
            )

        assert exc_info.value.retry_after_seconds == 12 * 60

    def test_other_codes_raise_plain_meta_api_error(self, meta_settings, credentials):
        """An unknown failure must not masquerade as a token or rate-limit problem."""
        with pytest.raises(MetaAPIError) as exc_info:
            self._fetch(error_response(500, 2, message="Temporary issue"), credentials)

        assert not isinstance(exc_info.value, (MetaTokenError, MetaRateLimitError))
        assert exc_info.value.code == 2

    def test_error_envelope_inside_a_200_is_still_an_error(
        self, meta_settings, credentials
    ):
        """Meta occasionally answers 200 with an error body."""
        response = httpx.Response(
            200, json={"error": {"message": "nope", "code": 190}}
        )
        with pytest.raises(MetaTokenError):
            self._fetch(response, credentials)

    def test_transport_failure_becomes_a_502(self, meta_settings, credentials):
        """A connection error must not escape as a raw httpx exception."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(MetaAPIError) as exc_info:
            fetch_campaign_insight_rows(
                credentials,
                date(2026, 8, 29),
                date(2026, 8, 29),
                transport=httpx.MockTransport(handler),
            )

        assert exc_info.value.status_code == 502

    def test_error_code_sets_are_disjoint(self):
        """A code must not be both "dead token" and "back off"."""
        assert not (TOKEN_ERROR_CODES & RATE_LIMIT_ERROR_CODES)


# =============================================================================
# Configurable conversion action types
# =============================================================================


class TestConversionActionTypes:
    """Which action_type counts as a conversion is configuration, not code."""

    def test_configured_types_are_used_and_others_ignored(self, meta_settings):
        """link_click and landing_page_view must never be counted as conversions."""
        payload = insight_payload("2026-08-29", conversions="12", revenue="678.90")
        row = row_from_payload(payload, [PURCHASE, "purchase"])

        assert row is not None
        # 250 link_clicks and 180 landing_page_views are in the payload.
        assert row.conversions == 12
        assert row.conversion_value == Decimal("678.90")

    def test_changing_the_setting_changes_what_counts(self, meta_settings, monkeypatch):
        """Reconfiguring the action type must change the mapping, not the code."""
        monkeypatch.setattr(settings, "meta_conversion_action_types", "lead")
        payload = insight_payload("2026-08-29")
        payload["actions"].append({"action_type": "lead", "value": "31"})
        payload["action_values"].append({"action_type": "lead", "value": "42.50"})

        def handler(request: httpx.Request) -> httpx.Response:
            return json_response([payload])

        credentials = MetaCredentials(TOKEN, AD_ACCOUNT, "USD", None)
        rows = fetch_campaign_insight_rows(
            credentials,
            date(2026, 8, 29),
            date(2026, 8, 29),
            transport=httpx.MockTransport(handler),
        )

        assert rows[0].conversions == 31
        assert rows[0].conversion_value == Decimal("42.50")

    def test_first_configured_type_wins_no_double_counting(self, meta_settings):
        """purchase and offsite_conversion.* describe the same conversion."""
        payload = insight_payload("2026-08-29", conversions="12")
        payload["actions"].append({"action_type": "purchase", "value": "12"})

        row = row_from_payload(payload, [PURCHASE, "purchase"])

        assert row is not None
        assert row.conversions == 12  # not 24

    def test_absent_action_type_yields_zero(self, meta_settings):
        """No configured type present means no conversions, not a crash."""
        assert sum_action_values([{"action_type": "link_click", "value": "5"}], ["lead"]) is None

        payload = insight_payload("2026-08-29")
        payload["actions"] = [{"action_type": "link_click", "value": "5"}]
        payload["action_values"] = []
        row = row_from_payload(payload, [PURCHASE])

        assert row is not None
        assert row.conversions == 0
        assert row.conversion_value == Decimal(0)

    def test_settings_list_property_splits_and_strips(self):
        """The CSV setting follows the house cors_origins_list pattern."""
        assert settings.meta_conversion_action_types_list == [
            PURCHASE,
            OMNI_PURCHASE,
            "purchase",
        ]

    def test_default_includes_a_documented_grouped_purchase_type(self):
        """
        The default must contain an action type Meta actually reports.

        A bare ``purchase`` is not in Meta's documented action_type enum, so
        an account whose purchases are app, on-Facebook or offline (no web
        pixel purchase event) would match nothing and have conversions and
        revenue written as a confident zero. ``omni_purchase`` is the
        documented grouped type that covers those.
        """
        default = type(settings).model_fields["meta_conversion_action_types"].default
        types = [item.strip() for item in default.split(",")]
        assert types[0] == PURCHASE, "the web pixel purchase must still win"
        assert OMNI_PURCHASE in types

    def test_app_only_account_is_not_silently_zero(self, meta_settings, monkeypatch):
        """An account reporting only omni_purchase still gets real numbers."""
        monkeypatch.setattr(
            settings,
            "meta_conversion_action_types",
            f"{PURCHASE},{OMNI_PURCHASE},purchase",
        )
        payload = insight_payload("2026-08-29")
        payload["actions"] = [
            {"action_type": "link_click", "value": "250"},
            {"action_type": OMNI_PURCHASE, "value": "9"},
        ]
        payload["action_values"] = [
            {"action_type": OMNI_PURCHASE, "value": "412.50"},
        ]

        row = row_from_payload(
            payload, settings.meta_conversion_action_types_list
        )

        assert row is not None
        assert row.conversions == 9
        assert row.conversion_value == Decimal("412.50")


# =============================================================================
# Money
# =============================================================================


class TestMoneyConversion:
    """Money is Decimal all the way to the integer column."""

    @pytest.mark.parametrize(
        "amount,currency,expected",
        [
            ("123.45", "USD", 12345),
            ("0.01", "USD", 1),
            ("1000.005", "USD", 100001),  # ROUND_HALF_UP, not banker's rounding
            ("0", "USD", 0),
            ("99.994", "EUR", 9999),
            ("99.995", "EUR", 10000),
        ],
    )
    def test_two_decimal_currencies_are_exact_to_the_cent(
        self, amount, currency, expected
    ):
        assert to_hundredths(Decimal(amount), currency) == expected

    @pytest.mark.parametrize(
        "amount,currency,expected",
        [
            ("1234", "JPY", 123400),
            ("1500", "KRW", 150000),
            ("25000", "VND", 2500000),
            ("1234.4", "JPY", 123400),  # a yen has no sub-unit
            ("1234.6", "JPY", 123500),
        ],
    )
    def test_zero_decimal_currencies_are_not_blindly_multiplied(
        self, amount, currency, expected
    ):
        """
        The column means "major unit x 100" for every currency because every
        reader divides by 100 with no currency awareness. Storing true ISO-4217
        minor units for JPY would render yen amounts 100x too small.
        """
        assert to_hundredths(Decimal(amount), currency) == expected

    def test_three_decimal_currency_quantises_at_its_own_precision(self):
        """BHD has 3 decimals; the column can only hold 2, so it rounds once."""
        assert to_hundredths(Decimal("1.234"), "BHD") == 123
        assert to_hundredths(Decimal("1.236"), "BHD") == 124

    def test_minor_unit_exponents(self):
        assert minor_unit_exponent("JPY") == 0
        assert minor_unit_exponent("jpy") == 0
        assert minor_unit_exponent("USD") == 2
        assert minor_unit_exponent("KWD") == 3
        assert minor_unit_exponent(None) == 2
        assert minor_unit_exponent("ZZZ") == 2

    def test_zero_decimal_set_is_consistent(self):
        assert "JPY" in ZERO_DECIMAL_CURRENCIES
        assert "USD" not in ZERO_DECIMAL_CURRENCIES

    def test_no_float_anywhere_on_the_money_path(self):
        """A float would reintroduce the rounding drift the cents columns exist to avoid."""
        row = row_from_payload(
            insight_payload("2026-08-29", spend="0.1", revenue="0.2"), [PURCHASE]
        )
        assert row is not None
        assert row.spend + row.conversion_value == Decimal("0.3")


# =============================================================================
# Mapping onto CampaignMetric
# =============================================================================


class TestMetricMapping:
    """A row must land on the right columns with exact values."""

    def test_row_maps_to_campaign_metric_values(self, meta_settings):
        row = row_from_payload(
            insight_payload("2026-08-29", spend="123.45", revenue="678.90"), [PURCHASE]
        )
        assert row is not None

        values = metric_values_from_row(row)

        assert values == {
            "date": date(2026, 8, 29),
            "impressions": 10000,
            "clicks": 250,
            "conversions": 12,
            "spend_cents": 12345,
            "revenue_cents": 67890,
            "video_views": 900,
            "video_completions": 150,
        }

    def test_row_currency_wins_over_the_fallback(self, meta_settings):
        """Meta's account_currency is authoritative for that account."""
        row = row_from_payload(
            insight_payload("2026-08-29", spend="1234", currency="JPY"), [PURCHASE]
        )
        assert row is not None

        assert metric_values_from_row(row, "USD")["spend_cents"] == 123400

    def test_fallback_currency_used_when_meta_omits_it(self, meta_settings):
        payload = insight_payload("2026-08-29", spend="1234")
        del payload["account_currency"]
        row = row_from_payload(payload, [PURCHASE])
        assert row is not None

        assert metric_values_from_row(row, "JPY")["spend_cents"] == 123400


# =============================================================================
# Persistence and idempotency
# =============================================================================


class TestIngestionPersistence:
    """The upsert must be idempotent on (campaign_id, date)."""

    def _rows(self, days: list[str], **kwargs: Any) -> list[Any]:
        """Parse insight payloads for the given days."""
        return [
            row_from_payload(insight_payload(day, **kwargs), [PURCHASE])
            for day in days
        ]

    def test_first_ingest_inserts_one_row_per_day(self, meta_settings, campaign):
        session = FakeSession(campaign=campaign)
        rows = self._rows(["2026-08-29", "2026-08-30", "2026-08-31"])

        result = ingest_campaign_insights(
            session,
            TENANT_ID,
            campaign,
            rows,
            date(2026, 8, 29),
            date(2026, 8, 31),
            "USD",
        )

        assert result.inserted == 3
        assert result.updated == 0
        assert len(session.metrics) == 3
        assert {m.date for m in session.metrics} == {
            date(2026, 8, 29),
            date(2026, 8, 30),
            date(2026, 8, 31),
        }
        assert all(m.tenant_id == TENANT_ID for m in session.metrics)
        assert all(m.campaign_id == CAMPAIGN_ID for m in session.metrics)

    def test_reingesting_the_same_day_updates_instead_of_duplicating(
        self, meta_settings, campaign
    ):
        """Meta restates conversions; a second pull must correct, not double."""
        session = FakeSession(campaign=campaign)
        window = (date(2026, 8, 29), date(2026, 8, 31))
        days = ["2026-08-29", "2026-08-30", "2026-08-31"]

        ingest_campaign_insights(
            session, TENANT_ID, campaign, self._rows(days), *window, "USD"
        )
        assert len(session.metrics) == 3

        restated = self._rows(days, conversions="20", revenue="999.99")
        result = ingest_campaign_insights(
            session, TENANT_ID, campaign, restated, *window, "USD"
        )

        assert result.inserted == 0
        assert result.updated == 3
        assert len(session.metrics) == 3  # no duplicates
        assert all(m.conversions == 20 for m in session.metrics)
        assert all(m.revenue_cents == 99999 for m in session.metrics)

    def test_campaign_aggregates_and_sync_state_are_refreshed(
        self, meta_settings, campaign
    ):
        session = FakeSession(campaign=campaign)
        rows = self._rows(["2026-08-29", "2026-08-30"])

        ingest_campaign_insights(
            session, TENANT_ID, campaign, rows, date(2026, 8, 29), date(2026, 8, 30), "USD"
        )

        assert campaign.impressions == 20000
        assert campaign.clicks == 500
        assert campaign.conversions == 24
        assert campaign.total_spend_cents == 24690
        assert campaign.revenue_cents == 135780
        assert campaign.last_synced_at is not None
        assert campaign.sync_error is None
        assert campaign.roas == pytest.approx(135780 / 24690)

    def test_unique_constraint_backs_the_upsert(self):
        """The select-then-write relies on the DB constraint as its backstop."""
        names = {
            constraint.name
            for constraint in CampaignMetric.__table__.constraints
            if getattr(constraint, "name", None)
        }
        assert "uq_campaign_metric_date" in names


# =============================================================================
# Credential resolution
# =============================================================================


class TestCredentialResolution:
    """A missing or unusable credential must be an explicit, named refusal."""

    def test_resolves_a_healthy_connection(self, meta_settings, campaign, connection):
        session = FakeSession(campaign=campaign, connection=connection)

        credentials = resolve_meta_credentials(session, TENANT_ID, campaign)

        assert credentials.access_token == TOKEN
        assert credentials.ad_account_id == AD_ACCOUNT
        assert credentials.currency == "USD"

    def test_enum_status_is_accepted_not_just_its_string_value(
        self, meta_settings, campaign, connection
    ):
        """
        ``oauth.py`` assigns ``ConnectionStatus.CONNECTED`` itself to a String
        column, so a connection still live in the session carries the enum.
        ``str()`` of it is "ConnectionStatus.CONNECTED", which must not be
        mistaken for "not connected" right after a tenant completes OAuth.
        """
        connection.status = ConnectionStatus.CONNECTED  # the member, not .value
        session = FakeSession(campaign=campaign, connection=connection)

        credentials = resolve_meta_credentials(session, TENANT_ID, campaign)

        assert credentials.access_token == TOKEN

    def test_enum_status_disconnected_is_still_refused(
        self, meta_settings, campaign, connection
    ):
        """The enum path must not accidentally accept everything either."""
        connection.status = ConnectionStatus.DISCONNECTED
        session = FakeSession(campaign=campaign, connection=connection)

        with pytest.raises(MetaCredentialsError) as exc_info:
            resolve_meta_credentials(session, TENANT_ID, campaign)

        assert exc_info.value.reason == "connection_not_connected"

    def test_no_connection(self, meta_settings, campaign):
        session = FakeSession(campaign=campaign, connection=None)

        with pytest.raises(MetaCredentialsError) as exc_info:
            resolve_meta_credentials(session, TENANT_ID, campaign)

        assert exc_info.value.reason == "no_meta_connection"

    @pytest.mark.parametrize(
        "status",
        [
            ConnectionStatus.DISCONNECTED.value,
            ConnectionStatus.EXPIRED.value,
            ConnectionStatus.ERROR.value,
        ],
    )
    def test_connection_not_connected(self, meta_settings, campaign, connection, status):
        connection.status = status
        session = FakeSession(campaign=campaign, connection=connection)

        with pytest.raises(MetaCredentialsError) as exc_info:
            resolve_meta_credentials(session, TENANT_ID, campaign)

        assert exc_info.value.reason == "connection_not_connected"

    def test_expired_token(self, meta_settings, campaign, connection):
        connection.token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        session = FakeSession(campaign=campaign, connection=connection)

        with pytest.raises(MetaCredentialsError) as exc_info:
            resolve_meta_credentials(session, TENANT_ID, campaign)

        assert exc_info.value.reason == "token_expired"

    def test_missing_token(self, meta_settings, campaign, connection):
        connection.access_token_encrypted = None
        session = FakeSession(campaign=campaign, connection=connection)

        with pytest.raises(MetaCredentialsError) as exc_info:
            resolve_meta_credentials(session, TENANT_ID, campaign)

        assert exc_info.value.reason == "token_missing"

    def test_no_ad_account(self, meta_settings, campaign, connection):
        campaign.account_id = ""
        session = FakeSession(campaign=campaign, connection=connection, ad_account=None)

        with pytest.raises(MetaCredentialsError) as exc_info:
            resolve_meta_credentials(session, TENANT_ID, campaign)

        assert exc_info.value.reason == "no_ad_account"

    def test_falls_back_to_the_enabled_tenant_ad_account(
        self, meta_settings, campaign, connection
    ):
        campaign.account_id = ""
        campaign.currency = ""
        ad_account = TenantAdAccount(
            tenant_id=TENANT_ID,
            platform="meta",
            platform_account_id="act_9998887776",
            name="Main",
            currency="JPY",
            is_enabled=True,
        )
        session = FakeSession(
            campaign=campaign, connection=connection, ad_account=ad_account
        )

        credentials = resolve_meta_credentials(session, TENANT_ID, campaign)

        assert credentials.ad_account_id == "act_9998887776"
        assert credentials.currency == "JPY"

    def test_repr_never_shows_the_token(self, credentials):
        assert TOKEN not in repr(credentials)
        assert "***" in repr(credentials)


# =============================================================================
# Celery task wiring
# =============================================================================


def run_task(
    monkeypatch: pytest.MonkeyPatch,
    session: FakeSession,
    fetcher: Any,
    published: list[Any],
    retries: Optional[list[Any]] = None,
) -> Any:
    """
    Run ``sync_campaign_data`` against the fake session and a stubbed fetcher.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        session: The fake session the task should use.
        fetcher: Stand-in for ``fetch_campaign_insight_rows``.
        published: List that records ``publish_event`` calls.
        retries: When given, records ``self.retry`` calls and raises Retry.

    Returns:
        The task's return value.
    """
    monkeypatch.setattr(sync_task, "SyncSessionLocal", session)
    monkeypatch.setattr(sync_task, "fetch_campaign_insight_rows", fetcher)
    monkeypatch.setattr(
        sync_task, "publish_event", lambda *a, **kw: published.append((a, kw))
    )

    if retries is not None:

        def fake_retry(*args: Any, **kwargs: Any) -> Exception:
            """Record the retry request and return the exception to raise."""
            retries.append(kwargs)
            return RuntimeError("retry requested")

        monkeypatch.setattr(sync_task.sync_campaign_data, "retry", fake_retry)

    return sync_task.sync_campaign_data(TENANT_ID, CAMPAIGN_ID)


class TestSyncTaskHappyPath:
    """The real path must write real rows and report an honest success."""

    def test_writes_metrics_and_reports_success(
        self, meta_settings, campaign, connection, monkeypatch
    ):
        session = FakeSession(campaign=campaign, connection=connection)
        published: list[Any] = []
        rows = [
            row_from_payload(insight_payload(day), [PURCHASE])
            for day in ("2026-08-29", "2026-08-30")
        ]

        result = run_task(
            monkeypatch, session, lambda *a, **kw: rows, published
        )

        assert result["status"] == "success"
        assert result["source"] == "meta_insights"
        assert result["rows_fetched"] == 2
        assert result["inserted"] == 2
        assert len(session.metrics) == 2
        assert session.metrics[0].spend_cents == 12345
        assert campaign.last_synced_at is not None
        assert campaign.sync_error is None
        assert len(published) == 1

    def test_window_comes_from_config(self, meta_settings, campaign, connection, monkeypatch):
        """META_INSIGHTS_LOOKBACK_DAYS must drive the request, not a literal."""
        monkeypatch.setattr(settings, "meta_insights_lookback_days", 5)
        seen: dict[str, Any] = {}

        def fetcher(credentials, since, until, **kwargs):
            """Record the window the task asked for."""
            seen["since"] = since
            seen["until"] = until
            return []

        run_task(monkeypatch, FakeSession(campaign=campaign, connection=connection), fetcher, [])

        assert (seen["until"] - seen["since"]).days == 4  # 5 days inclusive

    def test_lookback_window_is_inclusive(self):
        since, until = insights_window(date(2026, 9, 4), lookback_days=7)
        assert since == date(2026, 8, 29)
        assert until == date(2026, 9, 4)


class TestSyncTaskRefusals:
    """No credential means no data and no claim of success."""

    @pytest.mark.parametrize(
        "mutate,expected_reason",
        [
            (lambda conn: None, "no_meta_connection"),
            (
                lambda conn: setattr(conn, "status", ConnectionStatus.EXPIRED.value),
                "connection_not_connected",
            ),
            (
                lambda conn: setattr(
                    conn, "token_expires_at", datetime.now(UTC) - timedelta(hours=1)
                ),
                "token_expired",
            ),
            (
                lambda conn: setattr(conn, "access_token_encrypted", None),
                "token_missing",
            ),
        ],
    )
    def test_refusals_write_nothing_and_report_no_success(
        self, meta_settings, campaign, connection, monkeypatch, mutate, expected_reason
    ):
        use_connection = connection
        if expected_reason == "no_meta_connection":
            use_connection = None
        else:
            mutate(connection)

        session = FakeSession(campaign=campaign, connection=use_connection)
        published: list[Any] = []

        def fetcher(*args: Any, **kwargs: Any) -> list[Any]:
            """The task must never reach the network without a credential."""
            raise AssertionError("no HTTP call may be made without a credential")

        result = run_task(monkeypatch, session, fetcher, published)

        assert result["status"] == "skipped"
        assert result["reason"] == expected_reason
        assert session.metrics == []
        assert campaign.last_synced_at is None
        assert campaign.sync_error
        assert published == []

    def test_no_ad_account_refuses(self, meta_settings, campaign, connection, monkeypatch):
        campaign.account_id = ""
        session = FakeSession(campaign=campaign, connection=connection, ad_account=None)

        result = run_task(
            monkeypatch,
            session,
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no call")),
            [],
        )

        assert result["status"] == "skipped"
        assert result["reason"] == "no_ad_account"
        assert session.metrics == []
        assert campaign.last_synced_at is None

    def test_missing_campaign_is_not_found(self, meta_settings, monkeypatch):
        session = FakeSession(campaign=None)

        result = run_task(monkeypatch, session, lambda *a, **kw: [], [])

        assert result["status"] == "not_found"
        assert session.metrics == []


class TestSyncTaskErrorHandling:
    """Token and throttling failures need distinct, correct reactions."""

    def test_token_error_disconnects_the_connection(
        self, meta_settings, campaign, connection, monkeypatch
    ):
        session = FakeSession(campaign=campaign, connection=connection)
        published: list[Any] = []

        def fetcher(*args: Any, **kwargs: Any) -> list[Any]:
            """Reject the token exactly as Meta code 190 does."""
            raise MetaTokenError(401, 190, "Session has expired", subcode=463)

        result = run_task(monkeypatch, session, fetcher, published)

        assert result["status"] == "failed"
        assert result["reason"] == "token_rejected"
        assert connection.status == ConnectionStatus.DISCONNECTED.value
        assert connection.error_count == 1
        assert connection.last_error
        assert session.metrics == []
        assert campaign.last_synced_at is None
        assert published == []

    def test_rate_limit_retries_and_writes_no_partial_data(
        self, meta_settings, campaign, connection, monkeypatch
    ):
        session = FakeSession(campaign=campaign, connection=connection)
        retries: list[Any] = []
        published: list[Any] = []

        def fetcher(*args: Any, **kwargs: Any) -> list[Any]:
            """Throttle the request the way codes 4/17/613 do."""
            raise MetaRateLimitError(
                400, 17, "User request limit reached", retry_after_seconds=600
            )

        with pytest.raises(RuntimeError):
            run_task(monkeypatch, session, fetcher, published, retries=retries)

        assert retries and retries[0]["countdown"] == 600
        assert session.metrics == []
        assert campaign.last_synced_at is None
        assert campaign.sync_error and "rate limit" in campaign.sync_error.lower()
        assert connection.status == ConnectionStatus.CONNECTED.value
        assert published == []

    def test_generic_api_error_is_recorded_and_reraised(
        self, meta_settings, campaign, connection, monkeypatch
    ):
        """Celery's own backoff handles the unknown case; nothing is written."""
        session = FakeSession(campaign=campaign, connection=connection)

        def fetcher(*args: Any, **kwargs: Any) -> list[Any]:
            """Fail the way an unclassified Graph API error does."""
            raise MetaAPIError(500, 2, "Temporary issue due to downtime")

        with pytest.raises(MetaAPIError):
            run_task(monkeypatch, session, fetcher, [])

        assert session.metrics == []
        assert campaign.last_synced_at is None
        assert campaign.sync_error


# =============================================================================
# Token leakage
# =============================================================================


class TestTokenNeverLeaks:
    """The access token must not reach a log record or an exception message."""

    def test_not_in_logs_or_exception_on_a_token_error(self, meta_settings, credentials):
        with (
            structlog.testing.capture_logs() as captured,
            pytest.raises(MetaTokenError) as exc_info,
        ):
                fetch_campaign_insight_rows(
                    credentials,
                    date(2026, 8, 29),
                    date(2026, 8, 29),
                    transport=httpx.MockTransport(
                        lambda request: error_response(
                            401, 190, message="Error validating access token"
                        )
                    ),
                )

        assert TOKEN not in str(exc_info.value)
        assert TOKEN not in repr(exc_info.value)
        assert TOKEN not in exc_info.value.message
        assert TOKEN not in json.dumps(captured, default=str)

    def test_not_in_logs_on_a_successful_pull(self, meta_settings, credentials):
        with structlog.testing.capture_logs() as captured:
            fetch_campaign_insight_rows(
                credentials,
                date(2026, 8, 29),
                date(2026, 8, 29),
                transport=httpx.MockTransport(
                    lambda request: json_response([insight_payload("2026-08-29")])
                ),
            )

        assert TOKEN not in json.dumps(captured, default=str)

    def test_not_in_logs_on_a_transport_failure(self, meta_settings, credentials):
        """The URL can appear in a transport error - the token must not be in it."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"failed connecting to {request.url}")

        with (
            structlog.testing.capture_logs() as captured,
            pytest.raises(MetaAPIError) as exc_info,
        ):
                fetch_campaign_insight_rows(
                    credentials,
                    date(2026, 8, 29),
                    date(2026, 8, 29),
                    transport=httpx.MockTransport(handler),
                )

        assert TOKEN not in str(exc_info.value)
        assert TOKEN not in json.dumps(captured, default=str)

    def test_meta_echoing_the_token_back_is_redacted(self, meta_settings, credentials):
        """Belt and braces: even if Meta echoed the token, it must not propagate."""
        with pytest.raises(MetaTokenError) as exc_info:
            fetch_campaign_insight_rows(
                credentials,
                date(2026, 8, 29),
                date(2026, 8, 29),
                transport=httpx.MockTransport(
                    lambda request: error_response(
                        401, 190, message=f"Invalid token {TOKEN}"
                    )
                ),
            )

        assert TOKEN not in str(exc_info.value)
        assert "***REDACTED***" in str(exc_info.value)

    def test_not_in_task_logs(
        self, meta_settings, campaign, connection, monkeypatch, caplog
    ):
        """The Celery task logs through stdlib logging - check that too."""
        session = FakeSession(campaign=campaign, connection=connection)

        def fetcher(*args: Any, **kwargs: Any) -> list[Any]:
            """Reject the token so every error branch logs."""
            raise MetaTokenError(401, 190, "Session has expired")

        with caplog.at_level(logging.DEBUG):
            run_task(monkeypatch, session, fetcher, [])

        for record in caplog.records:
            assert TOKEN not in record.getMessage()
        assert TOKEN not in (connection.last_error or "")


# =============================================================================
# Read-only guarantee
# =============================================================================


class TestReadOnly:
    """The client must be incapable of mutating anything on Meta."""

    def test_only_get_is_ever_issued(self, meta_settings, credentials):
        methods: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            methods.append(request.method)
            return json_response([insight_payload("2026-08-29")], next_cursor="C1") if len(
                methods
            ) == 1 else json_response([insight_payload("2026-08-30")])

        fetch_campaign_insight_rows(
            credentials,
            date(2026, 8, 29),
            date(2026, 8, 30),
            transport=httpx.MockTransport(handler),
        )

        assert methods == ["GET", "GET"]

    def test_client_exposes_no_write_method(self):
        """No post/put/patch/delete helper may exist on the insights client."""
        for forbidden in ("post", "put", "patch", "delete", "create", "update"):
            assert not hasattr(MetaInsightsClient, forbidden)

    def test_ingestion_module_never_imports_the_action_executor(self):
        """The autopilot write path must stay unwired."""
        source = insights_ingestion.__file__
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        assert "apply_actions_queue" not in text


# =============================================================================
# Regressions found in review
# =============================================================================


class TestBlankExternalIdIsRefused:
    """
    A campaign with no external_id must never inherit the whole ad account.

    ``Campaign.external_id`` is NOT NULL but empty strings get through, and a
    blank id used to disable *both* the server-side ``filtering`` and the local
    re-filter. Every campaign's rows in the account were then upserted onto the
    same ``(campaign_id, date)`` key - real money belonging to someone else,
    reported as a success.
    """

    def test_resolution_refuses_a_blank_external_id(
        self, meta_settings, campaign, connection
    ):
        """Credential resolution treats it as a configuration refusal."""
        campaign.external_id = "   "
        session = FakeSession(campaign=campaign, connection=connection)

        with pytest.raises(MetaCredentialsError) as excinfo:
            resolve_meta_credentials(session, TENANT_ID, campaign)

        assert excinfo.value.reason == "no_external_id"

    def test_fetch_refuses_a_blank_filter_id_rather_than_pulling_everything(
        self, meta_settings
    ):
        """A blank (not absent) filter id is a bug, not "no filter"."""
        called = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            called["n"] += 1
            return json_response([insight_payload("2026-08-29")])

        credentials = MetaCredentials(TOKEN, AD_ACCOUNT, "USD", None)
        with pytest.raises(MetaCredentialsError) as excinfo:
            fetch_campaign_insight_rows(
                credentials,
                date(2026, 8, 29),
                date(2026, 8, 29),
                external_campaign_id="",
                transport=httpx.MockTransport(handler),
            )

        assert excinfo.value.reason == "no_external_id"
        assert called["n"] == 0, "no request may be issued with a blank filter"

    def test_task_writes_nothing_and_leaves_freshness_alone(
        self, meta_settings, campaign, connection, monkeypatch
    ):
        """End to end: no rows, no success, no advanced last_synced_at."""
        campaign.external_id = ""
        session = FakeSession(campaign=campaign, connection=connection)
        published: list[Any] = []

        def fetcher(*args: Any, **kwargs: Any) -> list[Any]:
            raise AssertionError("no HTTP call may be made without an external_id")

        result = run_task(monkeypatch, session, fetcher, published)

        assert result["status"] == "skipped"
        assert result["reason"] == "no_external_id"
        assert session.metrics == []
        assert campaign.last_synced_at is None
        assert campaign.sync_error
        assert published == []

    def test_schema_rejects_a_blank_external_id(self):
        """The API cannot create a campaign that this path must then refuse."""
        from pydantic import ValidationError

        from app.base_schemas import CampaignCreate

        with pytest.raises(ValidationError):
            CampaignCreate(
                name="Blank",
                platform="meta",
                external_id="",
                account_id=AD_ACCOUNT,
            )


class TestConversionCountAndValueShareOneActionType:
    """
    The count and the revenue must come from the *same* action type.

    Meta drops a type from ``action_values`` when no value was attributed to
    it, so resolving each array independently could pair a pixel-purchase
    count with an omni-purchase value - two different populations, and an AOV
    that reconciles against nothing in Ads Manager.
    """

    def test_divergent_arrays_do_not_borrow_another_types_value(
        self, meta_settings
    ):
        """actions has both types, action_values only the second one."""
        payload = insight_payload("2026-08-29")
        payload["actions"] = [
            {"action_type": PURCHASE, "value": "10"},
            {"action_type": "purchase", "value": "12"},
            {"action_type": "link_click", "value": "500"},
        ]
        payload["action_values"] = [
            {"action_type": "purchase", "value": "1200.50"},
            {"action_type": "link_click", "value": "0"},
        ]

        row = row_from_payload(payload, [PURCHASE, "purchase"])

        assert row is not None
        # PURCHASE wins in `actions`, and `action_values` has no PURCHASE
        # entry, so revenue is zero - never the other type's 1200.50.
        assert row.conversions == 10
        assert row.conversion_value == Decimal(0)

    def test_the_winning_type_is_read_from_both_arrays(self, meta_settings):
        """When the winning type is in both arrays, both come from it."""
        payload = insight_payload("2026-08-29")
        payload["actions"] = [
            {"action_type": PURCHASE, "value": "10"},
            {"action_type": "purchase", "value": "12"},
        ]
        payload["action_values"] = [
            {"action_type": PURCHASE, "value": "500.00"},
            {"action_type": "purchase", "value": "1200.50"},
        ]

        row = row_from_payload(payload, [PURCHASE, "purchase"])

        assert row is not None
        assert row.conversions == 10
        assert row.conversion_value == Decimal("500.00")

    def test_missing_value_is_logged_with_the_action_type(self, meta_settings):
        """The operator gets told which type had a count but no value."""
        payload = insight_payload("2026-08-29")
        payload["action_values"] = [{"action_type": "link_click", "value": "0"}]

        with structlog.testing.capture_logs() as logs:
            row = row_from_payload(payload, [PURCHASE])

        assert row is not None
        assert row.conversion_value == Decimal(0)
        events = [entry for entry in logs if entry.get("event") ==
                  "meta_insight_conversion_value_missing"]
        assert events and events[0]["action_type"] == PURCHASE

    def test_helpers_separate_resolution_from_summing(self, meta_settings):
        """The two halves are independently usable and consistent."""
        stats = [
            {"action_type": "purchase", "value": "3"},
            {"action_type": PURCHASE, "value": "7"},
            {"action_type": PURCHASE, "value": "1"},
        ]

        assert first_present_action_type(stats, [PURCHASE, "purchase"]) == PURCHASE
        assert sum_one_action_type(stats, PURCHASE) == Decimal(8)
        assert sum_one_action_type(stats, "omni_purchase") is None
        assert sum_one_action_type(stats, None) is None
        assert sum_action_values(stats, [PURCHASE, "purchase"]) == Decimal(8)


class TestEmptyWindowDoesNotClaimFreshness:
    """
    Zero rows is not a successful sync.

    It is indistinguishable from a campaign deleted on Meta or an external_id
    that belongs to another account, and Freshness is 25% of signal health
    (docs/architecture/trust-engine.md). Advancing ``last_synced_at`` would let
    the Trust Gate read HEALTHY on data nobody has seen.
    """

    def test_ingest_leaves_last_synced_at_and_records_why(
        self, meta_settings, campaign
    ):
        campaign.last_synced_at = datetime(2026, 1, 1, tzinfo=UTC)
        campaign.sync_error = "Tenant 7 has no Meta platform connection"
        session = FakeSession(campaign=campaign)

        result = ingest_campaign_insights(
            session,
            TENANT_ID,
            campaign,
            [],
            date(2026, 8, 29),
            date(2026, 8, 31),
            ad_account_id=AD_ACCOUNT,
        )

        assert result.rows_fetched == 0
        assert result.marked_fresh is False
        assert result.status == "no_rows"
        assert campaign.last_synced_at == datetime(2026, 1, 1, tzinfo=UTC)
        assert campaign.sync_error
        assert CAMPAIGN_EXTERNAL_ID in campaign.sync_error
        assert AD_ACCOUNT in campaign.sync_error
        assert session.metrics == []

    def test_task_reports_no_rows_not_success(
        self, meta_settings, campaign, connection, monkeypatch
    ):
        session = FakeSession(campaign=campaign, connection=connection)
        published: list[Any] = []

        result = run_task(monkeypatch, session, lambda *a, **kw: [], published)

        assert result["status"] == "no_rows"
        assert result["reason"] == "no_insight_rows"
        assert campaign.last_synced_at is None
        assert campaign.sync_error
        assert session.metrics == []
        assert published == [], "no sync_complete event for a window with no data"

    def test_a_window_with_rows_still_clears_the_error(
        self, meta_settings, campaign, connection, monkeypatch
    ):
        """The honest path must not become a stuck error either."""
        campaign.sync_error = "an older failure"
        session = FakeSession(campaign=campaign, connection=connection)
        rows = [
            row_from_payload(insight_payload("2026-08-29"), [PURCHASE]),
        ]

        result = run_task(monkeypatch, session, lambda *a, **kw: rows, [])

        assert result["status"] == "success"
        assert campaign.sync_error is None
        assert campaign.last_synced_at is not None


class TestTruncatedPullIsAFailure:
    """A page-capped pull is incomplete data, not a short successful one."""

    def test_task_records_it_and_does_not_retry_or_claim_freshness(
        self, meta_settings, campaign, connection, monkeypatch
    ):
        session = FakeSession(campaign=campaign, connection=connection)
        published: list[Any] = []
        retries: list[Any] = []

        def fetcher(*args: Any, **kwargs: Any) -> list[Any]:
            raise MetaInsightsTruncatedError(AD_ACCOUNT, 25, 12500)

        result = run_task(monkeypatch, session, fetcher, published, retries)

        assert result["status"] == "failed"
        assert result["reason"] == "insights_truncated"
        assert session.metrics == []
        assert campaign.last_synced_at is None
        assert campaign.sync_error and "25-page cap" in campaign.sync_error
        assert retries == [], "retrying truncates identically"
        assert published == []


class TestHttpThrottlingWithoutAGraphErrorCode:
    """
    An edge/proxy throttle answers 429 with no Graph error envelope.

    Classifying on ``error.code`` alone made that a generic MetaAPIError, which
    Celery retried on ~1s exponential backoff straight back into the throttle.
    """

    def test_plain_429_is_a_rate_limit_error(self, meta_settings, credentials):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="Too Many Requests")

        with pytest.raises(MetaRateLimitError) as excinfo:
            fetch_campaign_insight_rows(
                credentials,
                date(2026, 8, 29),
                date(2026, 8, 29),
                external_campaign_id=CAMPAIGN_EXTERNAL_ID,
                transport=httpx.MockTransport(handler),
            )

        assert excinfo.value.status_code == 429
        assert TOKEN not in str(excinfo.value)

    def test_retry_after_header_seeds_the_backoff(self, meta_settings, credentials):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429, json={"error": {"message": "slow down"}},
                headers={"Retry-After": "90"},
            )

        with pytest.raises(MetaRateLimitError) as excinfo:
            fetch_campaign_insight_rows(
                credentials,
                date(2026, 8, 29),
                date(2026, 8, 29),
                external_campaign_id=CAMPAIGN_EXTERNAL_ID,
                transport=httpx.MockTransport(handler),
            )

        assert excinfo.value.retry_after_seconds == 90

    def test_business_use_case_header_still_wins_over_retry_after(
        self, meta_settings, credentials
    ):
        """Meta's own estimate is the better hint when both are present."""
        usage = json.dumps(
            {"business_1": [{"estimated_time_to_regain_access": 5}]}
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return error_response(
                429,
                4,
                headers={
                    "X-Business-Use-Case-Usage": usage,
                    "Retry-After": "90",
                },
            )

        with pytest.raises(MetaRateLimitError) as excinfo:
            fetch_campaign_insight_rows(
                credentials,
                date(2026, 8, 29),
                date(2026, 8, 29),
                external_campaign_id=CAMPAIGN_EXTERNAL_ID,
                transport=httpx.MockTransport(handler),
            )

        assert excinfo.value.retry_after_seconds == 300  # 5 minutes

    def test_unparseable_retry_after_falls_back_to_the_default(
        self, meta_settings, credentials
    ):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429, json={"error": {"message": "slow down"}},
                headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"},
            )

        with pytest.raises(MetaRateLimitError) as excinfo:
            fetch_campaign_insight_rows(
                credentials,
                date(2026, 8, 29),
                date(2026, 8, 29),
                external_campaign_id=CAMPAIGN_EXTERNAL_ID,
                transport=httpx.MockTransport(handler),
            )

        assert excinfo.value.retry_after_seconds == 300


class TestOneGraphApiVersionConstant:
    """
    ``META_GRAPH_API_VERSION`` must actually move every Meta caller.

    The insights client, the Conversions API connector, the WhatsApp Cloud
    connector, offline conversions, CDP audience sync and the OAuth flow used
    to carry three different hardcoded versions (two of them already expired),
    so setting the env var moved one of them and silently left the rest.
    """

    def test_every_meta_caller_reads_the_same_setting(self, monkeypatch):
        from app.services.capi.platform_connectors import (
            MetaCAPIConnector,
            WhatsAppCAPIConnector,
        )
        from app.services.cdp.audience_sync.meta_connector import (
            MetaAudienceConnector,
        )
        from app.services.meta.insights_client import graph_api_version
        from app.services.oauth.meta import MetaOAuthService
        from app.services.offline_conversion_service import MetaOfflineUploader

        monkeypatch.setattr(settings, "meta_graph_api_version", "v99.0")
        monkeypatch.setattr(settings, "meta_api_version", None)

        assert graph_api_version() == "v99.0"
        assert MetaCAPIConnector().API_VERSION == "v99.0"
        assert WhatsAppCAPIConnector().API_VERSION == "v99.0"
        assert MetaOfflineUploader().API_VERSION == "v99.0"
        assert MetaAudienceConnector(TOKEN, AD_ACCOUNT).API_VERSION == "v99.0"
        assert MetaOAuthService().api_version == "v99.0"

    def test_no_hardcoded_graph_version_survives_in_the_meta_callers(self):
        """A literal version string would break the single-knob guarantee."""
        import re
        from pathlib import Path

        sources = [
            "app/services/meta/insights_client.py",
            "app/services/capi/platform_connectors.py",
            "app/services/cdp/audience_sync/meta_connector.py",
            "app/services/offline_conversion_service.py",
            "app/services/oauth/meta.py",
        ]
        pattern = re.compile(r'API_VERSION\s*=\s*[\'"]v\d')
        for source in sources:
            text = Path(source).read_text()
            assert not pattern.search(text), f"hardcoded Graph version in {source}"

    def test_deprecated_override_still_works_for_oauth(self, monkeypatch):
        """An existing META_API_VERSION env var is honoured, not ignored."""
        from app.services.oauth.meta import MetaOAuthService

        monkeypatch.setattr(settings, "meta_graph_api_version", "v23.0")
        monkeypatch.setattr(settings, "meta_api_version", "v21.0")

        assert settings.meta_oauth_api_version == "v21.0"
        assert MetaOAuthService().api_version == "v21.0"


class TestZeroDecimalCurrencyHeadroom:
    """
    The x100 scaling needs 64-bit money columns.

    int4 caps at 2,147,483,647 hundredths = 21,474,836 major units: ~US$21M in
    USD, but only ~US$16k in KRW and ~US$860 in VND. A zero-decimal advertiser
    at ordinary spend used to hit "integer out of range" and abort the whole
    window - permanently, on every subsequent sync.
    """

    INT32_MAX = 2_147_483_647

    def test_money_columns_are_64_bit(self):
        from sqlalchemy import BigInteger

        from app.models import Campaign as CampaignModel

        for column in ("spend_cents", "revenue_cents"):
            assert isinstance(
                CampaignMetric.__table__.c[column].type, BigInteger
            ), f"campaign_metrics.{column} must be BigInteger"

        for column in (
            "total_spend_cents",
            "revenue_cents",
            "daily_budget_cents",
            "lifetime_budget_cents",
            "cpc_cents",
            "cpm_cents",
            "cpa_cents",
        ):
            assert isinstance(
                CampaignModel.__table__.c[column].type, BigInteger
            ), f"campaigns.{column} must be BigInteger"

    def test_a_realistic_vnd_campaign_exceeds_int32(self, meta_settings, campaign):
        """~US$1,200 of VND spend is already past the old ceiling."""
        campaign.currency = "VND"
        session = FakeSession(campaign=campaign)
        rows = [
            row_from_payload(
                insight_payload(
                    "2026-08-29",
                    spend="30000000",
                    revenue="90000000",
                    currency="VND",
                ),
                [PURCHASE],
            )
        ]

        ingest_campaign_insights(
            session,
            TENANT_ID,
            campaign,
            rows,
            date(2026, 8, 29),
            date(2026, 8, 29),
            fallback_currency="VND",
        )

        assert session.metrics[0].spend_cents == 3_000_000_000
        assert session.metrics[0].spend_cents > self.INT32_MAX
        assert campaign.total_spend_cents == 3_000_000_000

    def test_jpy_still_renders_at_the_right_magnitude(self):
        """Hundredths-of-major-unit, not ISO-4217 minor units."""
        yen = Decimal("1234.00")
        assert to_hundredths(yen, "JPY") == 123400
        assert to_hundredths(yen, "JPY") / 100 == 1234

    def test_the_migration_covers_exactly_the_widened_columns(self):
        """The 0002 migration and the models must not drift apart."""
        import importlib.util
        from pathlib import Path

        from sqlalchemy import BigInteger

        from app.db.base import Base

        path = Path(
            "migrations/versions/20260904_000000_0002_widen_money_columns.py"
        )
        spec = importlib.util.spec_from_file_location("_widen_money", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert module.down_revision == "0001_baseline"

        for table_name, column_name, nullable in module.MONEY_COLUMNS:
            table = Base.metadata.tables[table_name]
            column = table.c[column_name]
            assert isinstance(column.type, BigInteger), (
                f"{table_name}.{column_name} is in the migration but is not "
                f"BigInteger in the model"
            )
            assert column.nullable is nullable, (
                f"{table_name}.{column_name} nullability disagrees with the "
                f"migration's existing_nullable"
            )

        migrated = {(t, c) for t, c, _ in module.MONEY_COLUMNS}
        for table_name in ("campaigns", "campaign_metrics"):
            table = Base.metadata.tables[table_name]
            for column in table.c:
                if column.name.endswith("_cents"):
                    assert (table_name, column.name) in migrated, (
                        f"{table_name}.{column.name} is a money column the "
                        f"migration does not widen"
                    )
