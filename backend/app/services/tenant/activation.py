# =============================================================================
# Stratum AI - Meta activation status
# =============================================================================
"""Compute tenant Meta integration progress for the activation hub."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audience_sync import AudienceSyncCredential
from app.models.campaign_builder import ConnectionStatus, TenantPlatformConnection
from app.models.capi_credentials import TenantCAPICredential
from app.services.capi.credentials_store import public_connected_platforms
from app.services.whatsapp.credentials_store import public_status as whatsapp_public_status

FRONTEND_ACTIVATION_PATH = "/dashboard/activation"
FRONTEND_CONNECT_PATH = "/dashboard/campaigns/connect"
FRONTEND_CAPI_PATH = "/dashboard/capi-setup"
FRONTEND_AUDIENCE_SYNC_PATH = "/dashboard/cdp/audience-sync"
FRONTEND_WHATSAPP_PATH = "/dashboard/whatsapp"


@dataclass(frozen=True)
class ActivationStepDef:
    id: str
    title: str
    description: str
    required: bool
    action_path: str


ACTIVATION_STEPS: tuple[ActivationStepDef, ...] = (
    ActivationStepDef(
        id="meta_oauth",
        title="Connect Meta Ads (OAuth)",
        description=(
            "Authorize Stratum to read campaigns, insights, and ad accounts via "
            "Facebook Login. This is separate from CAPI and Custom Audiences."
        ),
        required=True,
        action_path=FRONTEND_CONNECT_PATH,
    ),
    ActivationStepDef(
        id="capi_meta",
        title="Conversions API (Pixel + CAPI token)",
        description=(
            "Send server-side conversion events to Meta. Use your Pixel ID and a "
            "CAPI-capable access token — not the same token as Custom Audiences."
        ),
        required=True,
        action_path=FRONTEND_CAPI_PATH,
    ),
    ActivationStepDef(
        id="marketing_api_token",
        title="Marketing API System User token",
        description=(
            "Required for Custom Audiences, audience sync, and other Marketing API "
            "writes. Create a System User in Meta Business Settings with "
            "ads_management on your ad account (act_…)."
        ),
        required=True,
        action_path=FRONTEND_ACTIVATION_PATH,
    ),
    ActivationStepDef(
        id="whatsapp_messaging",
        title="WhatsApp messaging credentials",
        description=(
            "Optional Module G credentials for inbox, templates, and broadcasts. "
            "Distinct from CAPI WhatsApp fields on the CAPI Setup page."
        ),
        required=False,
        action_path=FRONTEND_WHATSAPP_PATH,
    ),
)


async def _meta_oauth_connected(db: AsyncSession, tenant_id: int) -> bool:
    result = await db.execute(
        select(func.count())
        .select_from(TenantPlatformConnection)
        .where(
            and_(
                TenantPlatformConnection.tenant_id == tenant_id,
                TenantPlatformConnection.status == ConnectionStatus.CONNECTED,
            )
        )
    )
    return (result.scalar() or 0) > 0


async def _capi_meta_connected(db: AsyncSession, tenant_id: int) -> bool:
    connected = await public_connected_platforms(db, tenant_id)
    meta = connected.get("meta") or {}
    return bool(meta.get("connected"))


async def _marketing_api_token_configured(db: AsyncSession, tenant_id: int) -> bool:
    result = await db.execute(
        select(func.count())
        .select_from(AudienceSyncCredential)
        .where(
            and_(
                AudienceSyncCredential.tenant_id == tenant_id,
                AudienceSyncCredential.is_active == True,  # noqa: E712
                AudienceSyncCredential.platform == "meta",
                AudienceSyncCredential.access_token_encrypted.isnot(None),
            )
        )
    )
    count = result.scalar() or 0
    if count > 0:
        return True
    # Legacy plaintext during cutover
    legacy = await db.execute(
        select(func.count())
        .select_from(AudienceSyncCredential)
        .where(
            and_(
                AudienceSyncCredential.tenant_id == tenant_id,
                AudienceSyncCredential.is_active == True,  # noqa: E712
                AudienceSyncCredential.platform == "meta",
                AudienceSyncCredential.access_token.isnot(None),
            )
        )
    )
    return (legacy.scalar() or 0) > 0


async def _whatsapp_messaging_connected(db: AsyncSession, tenant_id: int) -> bool:
    status = await whatsapp_public_status(db, tenant_id)
    return bool(status.get("connected"))


async def compute_activation_status(db: AsyncSession, tenant_id: int) -> dict[str, Any]:
    """Return activation checklist with completion flags for the tenant."""
    checks = {
        "meta_oauth": await _meta_oauth_connected(db, tenant_id),
        "capi_meta": await _capi_meta_connected(db, tenant_id),
        "marketing_api_token": await _marketing_api_token_configured(db, tenant_id),
        "whatsapp_messaging": await _whatsapp_messaging_connected(db, tenant_id),
    }

    steps: list[dict[str, Any]] = []
    required_total = 0
    required_done = 0

    for step_def in ACTIVATION_STEPS:
        complete = checks[step_def.id]
        if step_def.required:
            required_total += 1
            if complete:
                required_done += 1
        steps.append(
            {
                "id": step_def.id,
                "title": step_def.title,
                "description": step_def.description,
                "required": step_def.required,
                "complete": complete,
                "action_path": step_def.action_path,
            }
        )

    all_required_complete = required_done == required_total and required_total > 0
    optional_done = sum(1 for s in ACTIVATION_STEPS if not s.required and checks[s.id])
    optional_total = sum(1 for s in ACTIVATION_STEPS if not s.required)
    fully_integrated = all_required_complete and (
        optional_total == 0 or optional_done == optional_total
    )

    progress_percent = int(round((required_done / required_total) * 100)) if required_total else 100

    return {
        "steps": steps,
        "required_complete": all_required_complete,
        "fully_integrated": fully_integrated,
        "progress_percent": progress_percent,
        "required_done": required_done,
        "required_total": required_total,
    }
