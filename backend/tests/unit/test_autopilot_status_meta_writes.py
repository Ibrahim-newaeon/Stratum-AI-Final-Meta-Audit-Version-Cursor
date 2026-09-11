"""Autopilot status exposes Meta write flags without enabling them."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from app.api.v1.endpoints.autopilot import get_autopilot_status
from app.schemas.response import APIResponse


@pytest.mark.asyncio
async def test_autopilot_status_reports_meta_writes_off_by_default():
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.tenant_id = 7

    summary = {"pending_approval": 0}
    service = MagicMock()
    service.get_action_summary = AsyncMock(return_value=summary)

    with (
        patch(
            "app.api.v1.endpoints.autopilot.get_tenant_features",
            AsyncMock(return_value={"autopilot_level": 1}),
        ),
        patch("app.api.v1.endpoints.autopilot.AutopilotService", return_value=service),
        patch(
            "app.features.flags.get_autopilot_caps",
            return_value={
                "max_daily_budget_change": 1,
                "max_budget_pct_change": 20,
                "max_actions_per_day": 10,
            },
        ),
    ):
        response = await get_autopilot_status(request, tenant_id=7, db=AsyncMock())

    assert isinstance(response, APIResponse)
    assert response.data["enabled"] is True  # plan level > 0
    assert response.data["execution_enabled"] is False
    assert response.data["execution_dry_run"] is True
    assert response.data["meta_writes_enabled"] is False
