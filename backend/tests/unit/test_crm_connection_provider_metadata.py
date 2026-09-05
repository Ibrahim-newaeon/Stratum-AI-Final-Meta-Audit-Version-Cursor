# =============================================================================
# Stratum AI - CRM Connection Provider Metadata Tests
# =============================================================================
"""
Unit tests for ``CRMConnection.provider_metadata`` and the CRM OAuth settings.

Salesforce and Pipedrive cannot address a tenant's account without a value the
provider only returns at OAuth time - Salesforce's ``instance_url``, Pipedrive's
``api_domain``. Both clients read and wrote that value on the connection, but
the column did not exist, so both raised ``AttributeError``; the Salesforce
client also had no ``settings.salesforce_client_id`` to authorise with.

What is pinned here:

* the column exists on ``crm_connections``, is JSONB and is nullable,
* a Salesforce request URL is built from the stored ``instance_url``,
* a connection with no ``instance_url`` makes no request at all,
* refreshing the instance URL replaces the JSONB value rather than mutating it,
  because a plain JSONB column does not track in-place edits,
* the four CRM OAuth settings exist, default to None and read from the
  environment,
* every Alembic revision id fits ``alembic_version.version_num``.
"""

import pathlib
from typing import Any, Self
from uuid import uuid4

import pytest
from sqlalchemy.dialects.postgresql import JSONB

import app.models  # noqa: F401  (registers every mapper so ORM statements compile)
from app.core.config import Settings
from app.models.crm import CRMConnection, CRMProvider
from app.services.crm import salesforce_client
from app.services.crm.salesforce_client import API_VERSION, SalesforceClient
from app.services.encryption import encrypt_token

pytestmark = pytest.mark.unit

TENANT = 1
MIGRATIONS = pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"

# Alembic's own ``alembic_version.version_num`` column. A longer revision id
# applies its DDL and then fails on the version stamp, leaving the migration
# rolled back and the operator staring at a StringDataRightTruncation.
VERSION_NUM_LENGTH = 32


# =============================================================================
# 1. The column
# =============================================================================


def test_provider_metadata_is_nullable_jsonb():
    """Provider metadata is optional JSON: a connection may have none yet."""
    column = CRMConnection.__table__.columns["provider_metadata"]

    assert isinstance(column.type, JSONB)
    assert column.nullable is True


def test_provider_metadata_is_distinct_from_the_raw_crm_record():
    """
    ``raw_properties`` stays what it is on contacts and deals: the raw record.

    The connection's metadata is about reaching the account, not about any one
    CRM object, which is why it does not reuse that name.
    """
    assert "raw_properties" not in CRMConnection.__table__.columns


# =============================================================================
# 2. The Salesforce instance URL
# =============================================================================


class _FakeResponse:
    """Minimal stand-in for an aiohttp response context manager."""

    status = 200

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def json(self) -> dict[str, Any]:
        return {"records": []}


class _FakeSession:
    """Records the URL every request is sent to."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        self.urls.append(url)
        return _FakeResponse()


def make_client(provider_metadata: dict[str, Any] | None) -> tuple[SalesforceClient, _FakeSession]:
    """A Salesforce client whose connection carries ``provider_metadata``."""
    connection = CRMConnection(
        id=uuid4(),
        tenant_id=TENANT,
        provider=CRMProvider.SALESFORCE,
        provider_metadata=provider_metadata,
    )

    client = SalesforceClient(db=None, tenant_id=TENANT)  # type: ignore[arg-type]
    session = _FakeSession()
    client._session = session  # type: ignore[assignment]

    async def _access_token() -> str:
        return "token-123"

    async def _connection() -> CRMConnection:
        return connection

    client._get_access_token = _access_token  # type: ignore[method-assign]
    client._get_connection = _connection  # type: ignore[method-assign]

    return client, session


@pytest.mark.asyncio
async def test_request_url_is_built_from_the_stored_instance_url():
    """The instance URL Salesforce returned at OAuth time addresses the org."""
    client, session = make_client({"instance_url": "https://acme.my.salesforce.com"})

    result = await client._make_request("GET", "/query")

    assert result == {"records": []}
    assert session.urls == [f"https://acme.my.salesforce.com/services/data/{API_VERSION}/query"]


@pytest.mark.asyncio
async def test_no_instance_url_means_no_request():
    """
    A connection predating the column carries NULL and cannot be addressed.

    It must fail closed rather than guess a Salesforce host.
    """
    for metadata in (None, {}, {"is_sandbox": False}):
        client, session = make_client(metadata)

        assert await client._make_request("GET", "/query") is None
        assert session.urls == []


class _FakeTokenResponse:
    """Stand-in for the aiohttp response to the token-refresh POST."""

    status = 200

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def json(self) -> dict[str, Any]:
        return {
            "access_token": "refreshed-token",
            "issued_at": "1772668800000",
            "instance_url": "https://acme-new.my.salesforce.com",
        }


class _FakeTokenSession:
    """Stand-in for ``aiohttp.ClientSession()`` in the refresh path."""

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    def post(self, url: str, **kwargs: Any) -> _FakeTokenResponse:
        return _FakeTokenResponse()


class _CommitOnlySession:
    """Enough of an AsyncSession for the refresh path."""

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_refreshing_the_instance_url_replaces_the_json_value(monkeypatch):
    """
    ``provider_metadata`` is plain JSONB, so it does not track in-place edits.

    The refresh path must assign a new dict; mutating the loaded one would
    leave the row unchanged and the client still pointed at the stale host.
    Everything already in the metadata has to survive the replacement.
    """
    monkeypatch.setattr(salesforce_client.aiohttp, "ClientSession", _FakeTokenSession)

    original = {"instance_url": "https://acme-old.my.salesforce.com", "is_sandbox": False}
    connection = CRMConnection(
        id=uuid4(),
        tenant_id=TENANT,
        provider=CRMProvider.SALESFORCE,
        provider_metadata=original,
        refresh_token_enc=encrypt_token("refresh-token"),
    )

    client = SalesforceClient(db=_CommitOnlySession(), tenant_id=TENANT)  # type: ignore[arg-type]

    async def _connection() -> CRMConnection:
        return connection

    client._get_connection = _connection  # type: ignore[method-assign]

    assert await client._refresh_token() is True

    # A different object, so SQLAlchemy sees the attribute as changed.
    assert connection.provider_metadata is not original
    assert original == {"instance_url": "https://acme-old.my.salesforce.com", "is_sandbox": False}
    assert connection.provider_metadata == {
        "instance_url": "https://acme-new.my.salesforce.com",
        "is_sandbox": False,
    }


# =============================================================================
# 3. The OAuth settings
# =============================================================================

CRM_OAUTH_SETTINGS = (
    "hubspot_client_id",
    "hubspot_client_secret",
    "salesforce_client_id",
    "salesforce_client_secret",
    "pipedrive_client_id",
    "pipedrive_client_secret",
    "zoho_client_id",
    "zoho_client_secret",
)


@pytest.mark.parametrize("name", CRM_OAUTH_SETTINGS)
def test_crm_oauth_settings_default_to_none(name: str):
    """A deployment that configures no CRM app must still start."""
    assert name in Settings.model_fields
    assert Settings.model_fields[name].default is None


@pytest.mark.parametrize(
    "name",
    ["salesforce_client_id", "salesforce_client_secret", "pipedrive_client_id", "pipedrive_client_secret"],
)
def test_crm_oauth_settings_are_read_from_the_environment(monkeypatch, name: str):
    """The operator supplies these as environment variables, as in .env.example."""
    monkeypatch.setenv(name.upper(), f"value-for-{name}")

    assert getattr(Settings(_env_file=None), name) == f"value-for-{name}"


# =============================================================================
# 4. Migration guard
# =============================================================================


def revision_ids() -> list[str]:
    """Every revision id declared under migrations/versions."""
    ids = []
    for path in sorted(MIGRATIONS.glob("*.py")):
        for line in path.read_text().splitlines():
            if line.startswith("revision = "):
                ids.append(line.split("=", 1)[1].strip().strip('"').strip("'"))
                break
    return ids


def test_every_revision_id_fits_the_alembic_version_column():
    """
    A revision id longer than 32 characters cannot be stamped.

    The upgrade runs, then dies on ``UPDATE alembic_version``, and the whole
    step rolls back - so the length is a hard constraint, not a style rule.
    """
    too_long = [rev for rev in revision_ids() if len(rev) > VERSION_NUM_LENGTH]

    assert revision_ids(), "no revisions found"
    assert too_long == []


def test_the_provider_metadata_revision_is_chained_to_a_single_head():
    """One linear history, with this revision as the only head."""
    down_revisions = set()
    revisions = set()
    for path in sorted(MIGRATIONS.glob("*.py")):
        for line in path.read_text().splitlines():
            if line.startswith("revision = "):
                revisions.add(line.split("=", 1)[1].strip().strip('"'))
            elif line.startswith("down_revision = ") and "None" not in line:
                down_revisions.add(line.split("=", 1)[1].strip().strip('"'))

    assert "0005_crm_provider_metadata" in revisions
    assert revisions - down_revisions == {"0005_crm_provider_metadata"}
