# =============================================================================
# Stratum AI - Fabricated Ad Metrics Guard
# =============================================================================
"""
Regression tests for the "production fabricates ad metrics" defect.

``use_mock_ad_data`` defaulted to True and USE_MOCK_AD_DATA was not set on the
deployed api or worker services, so the sync tasks were configured to write
``MockAdNetwork.generate_time_series(...)`` output straight into CampaignMetric
for real tenants. It stayed latent only because the worker consumed no queues
and the sync task therefore never ran - fixing that would have activated it.

Two things are pinned here:

1. The flag defaults to off and is rejected outright when APP_ENV=production.
2. With the flag off the sync tasks never fabricate. ``app/workers/tasks/sync.py``
   now pulls read-only Meta Marketing API insights instead; the legacy, shadowed
   ``app/workers/tasks.py`` still skips. Either way, a tenant without a usable
   Meta credential gets no rows and no claim of success - not a bare commit that
   publishes "sync_complete" for metrics that were never touched.
"""

import functools
import importlib.util
import pathlib
import re
from typing import ClassVar

import pytest
import yaml
from pydantic import ValidationError

from app.core.config import Settings, settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

# Values that satisfy every other production check, so USE_MOCK_AD_DATA is the
# only thing left that can make the configuration invalid.
PRODUCTION_BASELINE = {
    "app_env": "production",
    "secret_key": "Xq7pR2mJ4vL9wT6yB3nC8kF5hD1gS0aZ",
    "jwt_secret_key": "Mn4bV7cX2zQ9wE6rT1yU8iO5pA3sD0fG",
    "pii_encryption_key": "Zl6kJ9hG3fD8sA1qW7eR4tY2uI5oP0nM",
    "database_url": "postgresql+asyncpg://stratum:Kx9mQ2wL7vB4@db:5432/stratum_ai",
    "database_url_sync": "postgresql://stratum:Kx9mQ2wL7vB4@db:5432/stratum_ai",
}


class TestMockAdDataSetting:
    """The flag is off by default and cannot be turned on in production."""

    def test_defaults_to_off(self):
        """A container that sets nothing must not fabricate metrics."""
        assert Settings.model_fields["use_mock_ad_data"].default is False

    def test_production_baseline_is_otherwise_valid(self):
        """Guards the negative test below: only the flag makes it fail."""
        assert Settings(**PRODUCTION_BASELINE, use_mock_ad_data=False).is_production

    def test_rejected_in_production(self):
        """APP_ENV=production plus USE_MOCK_AD_DATA=true must not start."""
        with pytest.raises(ValidationError) as exc_info:
            Settings(**PRODUCTION_BASELINE, use_mock_ad_data=True)

        assert "USE_MOCK_AD_DATA must be false in production" in str(exc_info.value)

    def test_allowed_outside_production(self):
        """Local development and the demo seed keep working."""
        for env in ("development", "staging"):
            config = Settings(
                **{**PRODUCTION_BASELINE, "app_env": env}, use_mock_ad_data=True
            )
            assert config.use_mock_ad_data is True


class TestShippedComposeFilesStart:
    """No shipped compose file may combine production with mock data.

    ``use_mock_ad_data=True`` is now a hard startup failure in production, so a
    compose file that defaults ``APP_ENV`` to production while defaulting
    ``USE_MOCK_AD_DATA`` to true would not boot at all. The starter and
    professional editions did exactly that.
    """

    COMPOSE_FILES: ClassVar[list[pathlib.Path]] = [
        REPO_ROOT / "docker-compose.yml",
        REPO_ROOT / "docker-compose.prod.yml",
        REPO_ROOT / "editions/starter/docker-compose.yml",
        REPO_ROOT / "editions/professional/docker-compose.yml",
        REPO_ROOT / "editions/enterprise/docker-compose.yml",
    ]

    @staticmethod
    def _default_of(value: str) -> str:
        """Resolve ``${VAR:-default}`` to its default, or return it unchanged.

        Args:
            value: The right-hand side of a compose ``KEY=VALUE`` entry.

        Returns:
            The value an operator gets when they set nothing.
        """
        match = re.fullmatch(r"\$\{[A-Z_]+:-([^}]*)\}", value.strip())
        return match.group(1) if match else value.strip()

    @pytest.mark.parametrize(
        "path", COMPOSE_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT))
    )
    def test_no_service_defaults_to_production_with_mock_data(self, path):
        """Every service must be startable in its default configuration."""
        assert path.exists(), f"{path} is missing"
        compose = yaml.safe_load(path.read_text())

        for name, service in (compose.get("services") or {}).items():
            env = service.get("environment") or []
            if not isinstance(env, list):
                env = [f"{k}={v}" for k, v in env.items()]

            values = {}
            for item in env:
                if "=" in item:
                    key, _, raw = item.partition("=")
                    values[key.strip()] = self._default_of(raw)

            if "USE_MOCK_AD_DATA" not in values:
                # Nothing set means the code default (False), which is safe.
                continue

            app_env = values.get("APP_ENV", "")
            mock = values["USE_MOCK_AD_DATA"].lower() == "true"
            assert not (app_env == "production" and mock), (
                f"{path.relative_to(REPO_ROOT)} service '{name}' defaults to "
                f"APP_ENV=production with USE_MOCK_AD_DATA=true; the settings "
                f"validator rejects that, so the service cannot start"
            )


# =============================================================================
# Sync task behaviour with the flag off
# =============================================================================


class _FakeResult:
    """Stand-in for a SQLAlchemy ``Result``."""

    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        """Return the single ORM object, or None."""
        return self._obj

    def scalars(self):
        """Return a scalar view (nothing on this path returns a list)."""
        return self

    def first(self):
        """Return the single ORM object, or None."""
        return self._obj


class FakeSyncSession:
    """Synchronous session stand-in that records every write.

    Only ``select(Campaign)`` resolves. Every other lookup - the tenant's Meta
    platform connection, its enabled ad accounts - comes back empty, which is
    exactly the state of a tenant that has never connected Meta.
    """

    def __init__(self, campaign):
        self.campaign = campaign
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    @staticmethod
    def _selects_campaign(statement) -> bool:
        """Report whether the statement selects the Campaign entity itself."""
        for description in statement.column_descriptions:
            entity = description.get("entity")
            if entity is None or description.get("expr") is not entity:
                continue
            if getattr(entity, "__name__", "") == "Campaign":
                return True
        return False

    def execute(self, statement):
        """Return the campaign for a campaign lookup, nothing for anything else."""
        return _FakeResult(self.campaign if self._selects_campaign(statement) else None)

    def add(self, obj):
        """Record an attempted insert."""
        self.added.append(obj)

    def commit(self):
        """Record an attempted commit."""
        self.commits += 1

    def rollback(self):
        """Record a rollback."""
        self.rollbacks += 1


class FakeCampaign:
    """Just enough campaign for the refusal paths."""

    id = 42
    tenant_id = 1
    name = "Test Campaign"
    external_id = "ext-42"
    account_id = "act_123456789"
    currency = "USD"
    start_date = None
    last_synced_at = None
    sync_error = None


LEGACY_TASKS_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "app" / "workers" / "tasks.py"
)


@functools.cache
def _load_module(module_path: str):
    """
    Import one of the two sync implementations, once.

    ``app/workers/tasks.py`` is shadowed by the ``app/workers/tasks/`` package,
    so ``import app.workers.tasks`` never reaches it. It is loaded from its path
    here so its copy of the flag branch is covered too. The result is cached:
    re-executing the module would register its Celery tasks again and leave the
    task proxies bound to the first copy, past the monkeypatched session.
    """
    if module_path == "app.workers.tasks":
        spec = importlib.util.spec_from_file_location(
            "legacy_workers_tasks", LEGACY_TASKS_PATH
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return importlib.import_module(module_path)


def _run_sync_task(monkeypatch, module_path):
    """
    Run ``sync_campaign_data`` with the flag off against a recording session.

    Returns:
        ``(result, session, published)``.
    """
    module = _load_module(module_path)
    campaign = FakeCampaign()
    session = FakeSyncSession(campaign)
    published: list[tuple] = []

    monkeypatch.setattr(settings, "use_mock_ad_data", False)
    monkeypatch.setattr(module, "SyncSessionLocal", lambda: session)
    publisher = "publish_event" if module_path.endswith(".sync") else "_publish_event"
    monkeypatch.setattr(
        module, publisher, lambda *args, **kwargs: published.append((args, kwargs))
    )

    result = module.sync_campaign_data(campaign.tenant_id, campaign.id)
    return result, session, published


class TestLegacySyncSkipsWithoutMockData:
    """``app/workers/tasks.py`` has no real ingestion and must say so.

    It is shadowed by the ``app/workers/tasks/`` package and therefore never
    imported at runtime, so it was left on the skip branch rather than
    duplicating the Meta ingestion into dead code.
    """

    MODULE = "app.workers.tasks"

    def test_writes_no_campaign_metric_row(self, monkeypatch):
        """The whole point: not one fabricated metric reaches the database."""
        result, session, _ = _run_sync_task(monkeypatch, self.MODULE)

        assert session.added == []
        assert session.commits == 0
        assert result["status"] == "skipped"
        assert result["reason"] == "real_ad_platform_sync_unavailable"

    def test_does_not_claim_the_campaign_was_synced(self, monkeypatch):
        """
        The old code fell through to commit() and published "sync_complete"
        for a campaign whose metrics were never touched.
        """
        _, session, published = _run_sync_task(monkeypatch, self.MODULE)

        assert published == []
        assert session.campaign.last_synced_at is None

    def test_warns_so_the_skip_is_visible(self, monkeypatch, caplog):
        """A silent skip is almost as bad as a fabricated row."""
        import logging

        with caplog.at_level(logging.WARNING):
            _run_sync_task(monkeypatch, self.MODULE)

        assert any(
            "USE_MOCK_AD_DATA" in record.getMessage() for record in caplog.records
        ), "skipping the sync must be logged as a warning"


class TestRealSyncRefusesWithoutCredentials:
    """``app/workers/tasks/sync.py`` now pulls real Meta insights.

    With the mock flag off it goes to the Meta Marketing API instead of
    skipping - but a tenant with no Meta connection still must not produce a
    single row, and must not report success. Fabricating data and *pretending*
    to have synced are two separate defects; this pins the second one.
    """

    MODULE = "app.workers.tasks.sync"

    def test_writes_no_campaign_metric_row(self, monkeypatch):
        """No credential, no data - never an invented row."""
        result, session, _ = _run_sync_task(monkeypatch, self.MODULE)

        assert session.added == []
        assert result["status"] == "skipped"
        assert result["reason"] == "no_meta_connection"

    def test_does_not_claim_the_campaign_was_synced(self, monkeypatch):
        """last_synced_at must stay unset so freshness degrades honestly."""
        _, session, published = _run_sync_task(monkeypatch, self.MODULE)

        assert published == []
        assert session.campaign.last_synced_at is None

    def test_records_why_on_the_campaign(self, monkeypatch):
        """The operator has to be able to see why the campaign is stale."""
        _, session, _ = _run_sync_task(monkeypatch, self.MODULE)

        assert session.campaign.sync_error
        assert "Meta platform connection" in session.campaign.sync_error

    def test_warns_so_the_refusal_is_visible(self, monkeypatch, caplog):
        """A silent refusal is almost as bad as a fabricated row."""
        import logging

        with caplog.at_level(logging.WARNING):
            _run_sync_task(monkeypatch, self.MODULE)

        assert any(
            "Cannot sync campaign" in record.getMessage() for record in caplog.records
        ), "refusing the sync must be logged as a warning"
