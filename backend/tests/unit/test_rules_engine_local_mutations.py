"""RulesEngine honors the portal local-mutations kill-switch (LOCAL_ONLY)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.base_models import RuleAction, RuleStatus
from app.models import CampaignStatus
from app.services.rules_engine import RulesEngine
from app.services.rules_meta_policy import EXECUTION_SCOPE_LOCAL_DB


@pytest.mark.asyncio
async def test_rules_engine_skips_pause_when_local_mutations_disabled(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.rules_local_campaign_mutations_enabled",
        False,
    )
    engine = RulesEngine(db=AsyncMock(), tenant_id=1)
    rule = SimpleNamespace(
        id=1,
        action_type=RuleAction.PAUSE_CAMPAIGN,
        action_config={},
        status=RuleStatus.ACTIVE,
    )
    campaign = SimpleNamespace(
        id=9,
        name="C",
        status=CampaignStatus.ACTIVE,
        labels=[],
        daily_budget_cents=10_000,
    )

    result = await engine._execute_action(rule, campaign)

    assert campaign.status == CampaignStatus.ACTIVE
    assert result["skipped"] is True
    assert result["reason"] == "local_campaign_mutations_disabled"
    assert result["execution_scope"] == EXECUTION_SCOPE_LOCAL_DB
    assert result["meta_write"] is False


@pytest.mark.asyncio
async def test_rules_engine_pauses_when_local_mutations_enabled(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.rules_local_campaign_mutations_enabled",
        True,
    )
    engine = RulesEngine(db=AsyncMock(), tenant_id=1)
    rule = SimpleNamespace(
        id=1,
        action_type=RuleAction.PAUSE_CAMPAIGN,
        action_config={},
        status=RuleStatus.ACTIVE,
    )
    campaign = SimpleNamespace(
        id=9,
        name="C",
        status=CampaignStatus.ACTIVE,
        labels=[],
        daily_budget_cents=10_000,
    )

    result = await engine._execute_action(rule, campaign)

    assert campaign.status == CampaignStatus.PAUSED
    assert result["success"] is True
    assert result["local_only"] is True
    assert result["execution_scope"] == EXECUTION_SCOPE_LOCAL_DB
    assert result["meta_write"] is False
