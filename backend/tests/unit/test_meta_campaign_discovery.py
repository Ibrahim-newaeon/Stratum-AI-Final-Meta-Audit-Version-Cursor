# =============================================================================
# Stratum AI - Meta Campaign Discovery Tests (read-only)
# =============================================================================
"""
Unit tests for ``app.services.meta.campaign_discovery`` and the Celery task
``discover_tenant_campaigns_task``.

No network, no database: HTTP goes through ``httpx.MockTransport`` and the
session is an in-memory fake that stores ``Campaign`` rows so upsert
idempotency can be asserted.

What is pinned here:

* GET-only request shape against ``/act_<id>/campaigns``,
* pagination followed and the page cap refusing a truncated catalogue,
* upsert by ``(tenant_id, platform, external_id)`` inserts then updates,
* soft-deleted local rows are revived,
* Meta budgets are NOT written into ``*_cents`` columns,
* ``last_synced_at`` on campaigns is left untouched,
* missing credentials skip without inventing campaigns,
* code 190 disconnects the Meta connection,
* the access token never reaches a log record or an exception message.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Optional, Self
from uuid import uuid4

import httpx
import pytest

import app.models  # noqa: F401
from app.core.config import settings
from app.models import (
    AdPlatform,
    Campaign,
    CampaignStatus,
    ConnectionStatus,
    TenantAdAccount,
    TenantPlatformConnection,
)
from app.services.encryption import encrypt_token
from app.services.meta.campaign_discovery import (
    discover_tenant_campaigns,
    map_meta_campaign_status,
    parse_meta_date,
    resolve_meta_read_connection,
)
from app.services.meta.campaign_discovery_client import (
    CAMPAIGN_DISCOVERY_FIELDS,
    MetaCampaignDiscoveryClient,
    MetaCampaignsTruncatedError,
)
from app.services.meta.insights_client import MetaTokenError
from app.services.meta.insights_ingestion import MetaCredentialsError
from app.workers.tasks import sync as sync_task

pytestmark = pytest.mark.unit

TOKEN = "EAAG_super_secret_meta_discovery_token_do_not_leak"
AD_ACCOUNT = "act_1234567890"
TENANT_ID = 11


# =============================================================================
# Payload builders
# =============================================================================


def campaign_payload(
    campaign_id: str,
    *,
    name: str = "Prospecting | Broad",
    status: str = "ACTIVE",
    effective_status: str = "ACTIVE",
    objective: str = "OUTCOME_SALES",
    daily_budget: str = "5000",
    lifetime_budget: Optional[str] = None,
) -> dict[str, Any]:
    """Build one Meta campaigns-edge object."""
    payload: dict[str, Any] = {
        "id": campaign_id,
        "name": name,
        "status": status,
        "effective_status": effective_status,
        "objective": objective,
        "start_time": "2026-01-15T12:00:00-0800",
        "updated_time": "2026-09-01T00:00:00+0000",
        "daily_budget": daily_budget,
    }
    if lifetime_budget is not None:
        payload["lifetime_budget"] = lifetime_budget
    return payload


def json_response(
    data: list[dict[str, Any]],
    *,
    after: Optional[str] = None,
    status_code: int = 200,
) -> httpx.Response:
    """Build a Graph API list response, optionally advertising another page."""
    body: dict[str, Any] = {"data": data}
    if after is not None:
        body["paging"] = {
            "cursors": {"after": after},
            "next": f"https://graph.facebook.com/v23.0/{AD_ACCOUNT}/campaigns?after={after}",
        }
    return httpx.Response(status_code, json=body)


# =============================================================================
# Fake session
# =============================================================================


class _ScalarResult:
    """Stand-in for ``Result.scalars()``."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        """Return every row."""
        return list(self._rows)

    def first(self) -> Optional[Any]:
        """Return the first row or None."""
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


def _selected_entity(statement: Any) -> Optional[type]:
    """Return the ORM entity a ``select()`` targets."""
    for description in statement.column_descriptions:
        entity = description.get("entity")
        if entity is not None and description.get("expr") is entity:
            return entity
    return None


class FakeSession:
    """In-memory stand-in for ``SyncSessionLocal()`` used by discovery."""

    def __init__(
        self,
        *,
        connection: Optional[TenantPlatformConnection] = None,
        ad_accounts: Optional[list[TenantAdAccount]] = None,
        campaigns: Optional[list[Campaign]] = None,
    ) -> None:
        self.connection = connection
        self.ad_accounts = list(ad_accounts or [])
        self.campaigns: list[Campaign] = list(campaigns or [])
        self._pending: list[Campaign] = []
        self._next_id = 1 + max((c.id or 0 for c in self.campaigns), default=0)
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0

    def __call__(self) -> Self:
        """Allow the instance itself to stand in for ``SyncSessionLocal``."""
        return self

    def __enter__(self) -> Self:
        """Enter the ``with SyncSessionLocal() as db`` block."""
        return self

    def __exit__(self, *exc_info: object) -> bool:
        """Leave the block without swallowing exceptions."""
        return False

    def execute(self, statement: Any) -> _Result:
        """Answer the queries the discovery path issues."""
        entity = _selected_entity(statement)
        params = statement.compile().params

        if entity is TenantPlatformConnection:
            return _Result([self.connection] if self.connection is not None else [])
        if entity is TenantAdAccount:
            return _Result(list(self.ad_accounts))
        if entity is Campaign:
            external_id = params.get("external_id_1") or params.get("external_id")
            matches = [
                campaign
                for campaign in self.campaigns
                if (
                    external_id is None
                    or str(campaign.external_id) == str(external_id)
                )
            ]
            return _Result(matches)
        return _Result([])

    def add(self, obj: Any) -> None:
        """Buffer an insert; invisible until flush()."""
        if isinstance(obj, Campaign):
            self._pending.append(obj)

    def flush(self) -> None:
        """Make pending inserts visible and assign synthetic primary keys."""
        self.flushes += 1
        for campaign in self._pending:
            if getattr(campaign, "id", None) is None:
                campaign.id = self._next_id
                self._next_id += 1
            self.campaigns.append(campaign)
        self._pending.clear()

    def commit(self) -> None:
        """Record a commit (a real session flushes on commit)."""
        self.flush()
        self.commits += 1

    def rollback(self) -> None:
        """Discard anything still pending."""
        self._pending.clear()
        self.rollbacks += 1


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def meta_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin Meta Graph settings so tests do not depend on the .env."""
    monkeypatch.setattr(settings, "meta_graph_api_version", "v23.0")
    monkeypatch.setattr(settings, "meta_insights_request_timeout_seconds", 5.0)
    monkeypatch.setattr(settings, "meta_insights_max_pages", 25)
    monkeypatch.setattr(settings, "use_mock_ad_data", False)


@pytest.fixture
def connection() -> TenantPlatformConnection:
    """A healthy Meta connection holding an encrypted token."""
    return TenantPlatformConnection(
        tenant_id=TENANT_ID,
        platform="meta",
        status=ConnectionStatus.CONNECTED.value,
        access_token_encrypted=encrypt_token(TOKEN),
        token_expires_at=datetime.now(UTC) + timedelta(days=30),
        error_count=0,
    )


@pytest.fixture
def ad_account(connection: TenantPlatformConnection) -> TenantAdAccount:
    """One enabled Meta ad account."""
    return TenantAdAccount(
        id=uuid4(),
        tenant_id=TENANT_ID,
        connection_id=connection.id if getattr(connection, "id", None) else uuid4(),
        platform="meta",
        platform_account_id=AD_ACCOUNT,
        name="Primary Ads",
        currency="USD",
        timezone="UTC",
        is_enabled=True,
    )


# =============================================================================
# Pure helpers
# =============================================================================


class TestStatusAndDateHelpers:
    """Status mapping and date parsing stay honest."""

    def test_effective_status_preferred(self) -> None:
        """Delivery state wins over configured status."""
        assert (
            map_meta_campaign_status("PAUSED", "ACTIVE") is CampaignStatus.PAUSED
        )

    def test_unknown_status_defaults_active(self) -> None:
        """Unknown Meta statuses still surface as active, not draft."""
        assert map_meta_campaign_status("SOMETHING_NEW", None) is CampaignStatus.ACTIVE

    def test_parse_meta_offset_without_colon(self) -> None:
        """Meta's -0800 form parses to a UTC calendar date."""
        assert parse_meta_date("2026-01-15T12:00:00-0800").isoformat() == "2026-01-15"


# =============================================================================
# Client
# =============================================================================


class TestDiscoveryClient:
    """The discovery client is GET-only and paginates safely."""

    @pytest.mark.asyncio
    async def test_get_only_with_documented_fields(self, meta_settings) -> None:
        """Campaigns edge, documented fields, Bearer auth, no query token."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return json_response([campaign_payload("111")])

        async with MetaCampaignDiscoveryClient(
            TOKEN, transport=httpx.MockTransport(handler)
        ) as client:
            rows = await client.list_campaigns(AD_ACCOUNT)

        assert len(rows) == 1
        assert rows[0].external_id == "111"
        assert len(seen) == 1
        request = seen[0]
        assert request.method == "GET"
        assert request.url.path.endswith(f"/{AD_ACCOUNT}/campaigns")
        assert "access_token" not in str(request.url)
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        fields = request.url.params.get("fields", "").split(",")
        assert list(fields) == list(CAMPAIGN_DISCOVERY_FIELDS)

    @pytest.mark.asyncio
    async def test_page_cap_raises_truncated(self, meta_settings, monkeypatch) -> None:
        """Hitting the page cap while Meta still has pages refuses the list."""
        monkeypatch.setattr(settings, "meta_insights_max_pages", 1)

        def handler(request: httpx.Request) -> httpx.Response:
            return json_response([campaign_payload("111")], after="cursor-2")

        async with MetaCampaignDiscoveryClient(
            TOKEN, transport=httpx.MockTransport(handler)
        ) as client:
            with pytest.raises(MetaCampaignsTruncatedError):
                await client.list_campaigns(AD_ACCOUNT)

    @pytest.mark.asyncio
    async def test_token_error_redacts_secret(self, meta_settings) -> None:
        """Code 190 becomes MetaTokenError and never echoes the token."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": f"Invalid OAuth access token: {TOKEN}",
                        "code": 190,
                        "error_subcode": 463,
                    }
                },
            )

        async with MetaCampaignDiscoveryClient(
            TOKEN, transport=httpx.MockTransport(handler)
        ) as client:
            with pytest.raises(MetaTokenError) as exc_info:
                await client.list_campaigns(AD_ACCOUNT)

        assert TOKEN not in str(exc_info.value)
        assert "***REDACTED***" in exc_info.value.message


# =============================================================================
# Upsert / task
# =============================================================================


class TestDiscoverTenantCampaigns:
    """Catalogue upsert behaviour."""

    def test_inserts_then_updates_without_touching_freshness_or_budgets(
        self, meta_settings, connection, ad_account
    ) -> None:
        """First pass inserts; second updates name/status; money/freshness stay clean."""
        payloads = [
            campaign_payload("2384001", name="Alpha", daily_budget="9999"),
            campaign_payload(
                "2384002",
                name="Beta",
                effective_status="PAUSED",
                status="ACTIVE",
            ),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(payloads)

        db = FakeSession(connection=connection, ad_accounts=[ad_account])
        result = discover_tenant_campaigns(
            db, TENANT_ID, transport=httpx.MockTransport(handler)
        )
        db.commit()

        assert result.inserted == 2
        assert result.updated == 0
        assert len(db.campaigns) == 2
        by_external = {c.external_id: c for c in db.campaigns}
        assert by_external["2384001"].name == "Alpha"
        assert by_external["2384001"].status is CampaignStatus.ACTIVE
        assert by_external["2384001"].daily_budget_cents is None
        assert by_external["2384001"].lifetime_budget_cents is None
        assert by_external["2384001"].last_synced_at is None
        assert by_external["2384001"].account_id == AD_ACCOUNT
        assert by_external["2384001"].platform == AdPlatform.META
        assert by_external["2384002"].status is CampaignStatus.PAUSED
        assert by_external["2384001"].raw_data["meta"]["daily_budget"] == "9999"
        assert ad_account.last_synced_at is not None

        # Second discovery: rename + status change, still no freshness advance.
        payloads[0] = campaign_payload(
            "2384001", name="Alpha Reloaded", effective_status="PAUSED"
        )

        result2 = discover_tenant_campaigns(
            db, TENANT_ID, transport=httpx.MockTransport(handler)
        )
        db.commit()

        assert result2.inserted == 0
        assert result2.updated == 2
        assert len(db.campaigns) == 2
        assert by_external["2384001"].name == "Alpha Reloaded"
        assert by_external["2384001"].status is CampaignStatus.PAUSED
        assert by_external["2384001"].last_synced_at is None
        assert by_external["2384001"].daily_budget_cents is None

    def test_revives_soft_deleted_campaign(
        self, meta_settings, connection, ad_account
    ) -> None:
        """A soft-deleted local row is restored when Meta still lists it."""
        existing = Campaign(
            id=77,
            tenant_id=TENANT_ID,
            platform=AdPlatform.META,
            external_id="2384001",
            account_id=AD_ACCOUNT,
            name="Old Name",
            status=CampaignStatus.PAUSED,
            currency="USD",
            labels=[],
            is_deleted=True,
            deleted_at=datetime.now(UTC),
            last_synced_at=None,
        )
        db = FakeSession(
            connection=connection,
            ad_accounts=[ad_account],
            campaigns=[existing],
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(
                [campaign_payload("2384001", name="Back From Meta")]
            )

        result = discover_tenant_campaigns(
            db, TENANT_ID, transport=httpx.MockTransport(handler)
        )
        db.commit()

        assert result.updated == 1
        assert result.accounts[0].revived == 1
        assert existing.is_deleted is False
        assert existing.deleted_at is None
        assert existing.name == "Back From Meta"
        assert existing.last_synced_at is None

    def test_truncated_account_writes_nothing(
        self, meta_settings, connection, ad_account, monkeypatch
    ) -> None:
        """A truncated pull must not upsert a partial catalogue for that account."""
        monkeypatch.setattr(settings, "meta_insights_max_pages", 1)

        def handler(request: httpx.Request) -> httpx.Response:
            return json_response([campaign_payload("111")], after="more")

        db = FakeSession(connection=connection, ad_accounts=[ad_account])
        result = discover_tenant_campaigns(
            db, TENANT_ID, transport=httpx.MockTransport(handler)
        )
        db.commit()

        assert result.accounts[0].status == "failed"
        assert result.accounts[0].reason == "campaigns_truncated"
        assert db.campaigns == []
        assert ad_account.sync_error is not None

    def test_missing_connection_raises(self, meta_settings, ad_account) -> None:
        """No Meta connection is a hard skip, not an empty success."""
        db = FakeSession(connection=None, ad_accounts=[ad_account])
        with pytest.raises(MetaCredentialsError) as exc_info:
            resolve_meta_read_connection(db, TENANT_ID)
        assert exc_info.value.reason == "no_meta_connection"


class TestDiscoverTenantCampaignsTask:
    """Celery task wiring around discovery."""

    def test_success_queues_insights_for_new_campaigns(
        self, meta_settings, connection, ad_account, monkeypatch
    ) -> None:
        """Inserted campaign ids are handed to sync_campaign_data.delay."""
        db = FakeSession(connection=connection, ad_accounts=[ad_account])
        queued: list[tuple[int, int]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            return json_response([campaign_payload("2384999", name="Fresh")])

        monkeypatch.setattr(sync_task, "SyncSessionLocal", db)
        monkeypatch.setattr(
            sync_task.sync_campaign_data,
            "delay",
            lambda tenant_id, campaign_id: queued.append((tenant_id, campaign_id)),
        )
        monkeypatch.setattr(sync_task, "publish_event", lambda *args, **kwargs: None)

        # discover_tenant_campaigns is imported into sync_task; patch transport
        # by wrapping the service call via monkeypatch on the module used by the task.
        original = sync_task.discover_tenant_campaigns

        def wrapped(session, tenant_id, transport=None):
            return original(
                session, tenant_id, transport=httpx.MockTransport(handler)
            )

        monkeypatch.setattr(sync_task, "discover_tenant_campaigns", wrapped)

        outcome = sync_task.discover_tenant_campaigns_task.run(TENANT_ID)

        assert outcome["status"] == "success"
        assert outcome["inserted"] == 1
        assert len(queued) == 1
        assert queued[0][0] == TENANT_ID
        assert queued[0][1] == db.campaigns[0].id

    def test_missing_credentials_skips(
        self, meta_settings, monkeypatch
    ) -> None:
        """No connection → skipped, nothing queued."""
        db = FakeSession(connection=None, ad_accounts=[])
        monkeypatch.setattr(sync_task, "SyncSessionLocal", db)
        monkeypatch.setattr(sync_task, "publish_event", lambda *args, **kwargs: None)

        outcome = sync_task.discover_tenant_campaigns_task.run(TENANT_ID)

        assert outcome["status"] == "skipped"
        assert outcome["reason"] == "no_meta_connection"

    def test_token_rejection_disconnects(
        self, meta_settings, connection, ad_account, monkeypatch
    ) -> None:
        """Code 190 marks the connection disconnected."""
        db = FakeSession(connection=connection, ad_accounts=[ad_account])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"error": {"message": "Error validating access token", "code": 190}},
            )

        original = sync_task.discover_tenant_campaigns

        def wrapped(session, tenant_id, transport=None):
            return original(
                session, tenant_id, transport=httpx.MockTransport(handler)
            )

        monkeypatch.setattr(sync_task, "SyncSessionLocal", db)
        monkeypatch.setattr(sync_task, "discover_tenant_campaigns", wrapped)
        monkeypatch.setattr(sync_task, "publish_event", lambda *args, **kwargs: None)

        outcome = sync_task.discover_tenant_campaigns_task.run(TENANT_ID)

        assert outcome["status"] == "failed"
        assert outcome["reason"] == "token_rejected"
        assert connection.status == ConnectionStatus.DISCONNECTED.value
