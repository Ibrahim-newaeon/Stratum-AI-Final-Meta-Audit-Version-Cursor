"""Unit tests for Meta activation status computation."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.tenant.activation import ACTIVATION_STEPS, compute_activation_status


@pytest.mark.unit
@pytest.mark.asyncio
async def test_activation_status_all_incomplete():
    db = AsyncMock()
    with (
        patch(
            "app.services.tenant.activation._meta_oauth_connected",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.services.tenant.activation._capi_meta_connected",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.services.tenant.activation._marketing_api_token_configured",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.services.tenant.activation._whatsapp_messaging_connected",
            new=AsyncMock(return_value=False),
        ),
    ):
        result = await compute_activation_status(db, tenant_id=1)

    assert result["required_complete"] is False
    assert result["fully_integrated"] is False
    assert result["required_done"] == 0
    assert result["required_total"] == 3
    assert len(result["steps"]) == len(ACTIVATION_STEPS)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_activation_status_required_complete():
    db = AsyncMock()
    with (
        patch(
            "app.services.tenant.activation._meta_oauth_connected",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.tenant.activation._capi_meta_connected",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.tenant.activation._marketing_api_token_configured",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.tenant.activation._whatsapp_messaging_connected",
            new=AsyncMock(return_value=False),
        ),
    ):
        result = await compute_activation_status(db, tenant_id=1)

    assert result["required_complete"] is True
    assert result["progress_percent"] == 100
    assert result["fully_integrated"] is False  # WhatsApp optional still open


@pytest.mark.unit
@pytest.mark.asyncio
async def test_activation_status_fully_integrated():
    db = AsyncMock()
    with (
        patch(
            "app.services.tenant.activation._meta_oauth_connected",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.tenant.activation._capi_meta_connected",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.tenant.activation._marketing_api_token_configured",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.tenant.activation._whatsapp_messaging_connected",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await compute_activation_status(db, tenant_id=1)

    assert result["required_complete"] is True
    assert result["fully_integrated"] is True
