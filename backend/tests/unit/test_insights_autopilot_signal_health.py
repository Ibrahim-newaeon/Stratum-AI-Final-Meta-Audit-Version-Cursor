"""Insights autopilot helper must fail closed on missing signal health (DEF-004)."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints import insights as insights_mod


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "health",
    [
        {"status": "no_data", "automation_blocked": True},
        {"status": "insufficient_data"},
        {"status": "degraded"},
        {"status": "critical"},
        {"status": "healthy", "automation_blocked": True},
    ],
)
async def test_autopilot_blocked_when_signal_health_unknown_or_bad(monkeypatch, health):
    class _Svc:
        def __init__(self, db):
            self._db = db

        async def get_signal_health(self, tenant_id, target_date):
            return health

    monkeypatch.setattr(insights_mod, "SignalHealthService", _Svc)
    result = await insights_mod.check_signal_health_for_autopilot(
        AsyncMock(), tenant_id=1, target_date=date(2026, 1, 1)
    )
    assert result["blocked"] is True
    assert result["reason"]


@pytest.mark.asyncio
async def test_autopilot_not_blocked_when_healthy(monkeypatch):
    class _Svc:
        def __init__(self, db):
            self._db = db

        async def get_signal_health(self, tenant_id, target_date):
            return {"status": "healthy", "automation_blocked": False}

    monkeypatch.setattr(insights_mod, "SignalHealthService", _Svc)
    result = await insights_mod.check_signal_health_for_autopilot(
        AsyncMock(), tenant_id=1, target_date=date(2026, 1, 1)
    )
    assert result["blocked"] is False
    assert result["status"] == "healthy"
