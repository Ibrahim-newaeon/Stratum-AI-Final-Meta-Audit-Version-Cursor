# =============================================================================
# Stratum AI - Measurement & Verification Endpoints (GA4 + GTM)
# =============================================================================
"""
Measurement & Verification integrations.

- **Google Analytics 4**: read-only, independent revenue/conversion baseline
  pulled through the GA4 Data API with a service account
  (scope ``https://www.googleapis.com/auth/analytics.readonly``). Used for
  attribution variance, EMQ, signal health and the Trust Gate.
- **Google Tag Manager**: tag deployment (web container for the Meta Pixel +
  Stratum snippet; server-side tagging endpoint for the Meta Conversions API
  and the CDP ``sgtm`` source).

These are measurement integrations, never ad platforms. Stratum AI reads and
acts on Meta channels only.

Secrets (service-account JSON, GTM preview header) are stored encrypted with
``app.services.encryption`` and are never returned, logged, or written to a
CDP source config.

Every route requires an authenticated user (JWT bearer token). The tenant is
always taken from the authenticated user, never from the unvalidated
``X-Tenant-ID`` header. Saving, testing, syncing, verifying or removing a
configuration additionally requires an admin/manager role (the same rule as
tenant settings).
"""

import enum
from datetime import UTC, date, datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CurrentUser, get_current_user, require_role
from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import hash_pii_for_lookup
from app.db.session import get_async_session
from app.models import UserRole
from app.models.cdp import CDPSource
from app.models.measurement import (
    MeasurementStatus,
    TenantGA4Integration,
    TenantGTMIntegration,
)
from app.schemas.measurement import (
    GA4BaselineResponse,
    GA4ConfigRequest,
    GA4ConfigResponse,
    GA4DailyPoint,
    GA4StatusResponse,
    GA4SyncRequest,
    GA4SyncResponse,
    GA4TestRequest,
    GA4TestResponse,
    GTMConfigRequest,
    GTMConfigResponse,
    GTMSnippetsResponse,
    GTMStatusResponse,
    GTMVerifyResponse,
    MeasurementConnectionStatus,
    MeasurementStatusResponse,
)
from app.schemas.response import APIResponse
from app.services.encryption import decrypt_token, encrypt_token
from app.services.measurement.ga4_client import (
    GA4_READONLY_SCOPE,
    GA4AuthError,
    GA4ClientError,
    GA4DataClient,
)
from app.services.measurement.ga4_ingestion import (
    get_ga4_baseline,
    load_ga4_client_for_tenant,
    sync_ga4_for_tenant,
)
from app.services.measurement.gtm_service import (
    build_snippets,
    ensure_sgtm_source,
    validate_container_id,
    validate_server_container_url,
    verify_containers,
)

router = APIRouter(prefix="/integrations/measurement", tags=["Measurement & Verification"])
logger = get_logger(__name__)

DEFAULT_BASELINE_DAYS = 30
MAX_BASELINE_RANGE_DAYS = 366
FINGERPRINT_LENGTH = 16

# Roles allowed to change measurement settings (mirrors tenant settings: admin/manager).
MEASUREMENT_WRITE_ROLES: tuple[UserRole, ...] = (
    UserRole.SUPERADMIN,
    UserRole.ADMIN,
    UserRole.MANAGER,
)
# Dependency: authenticated user with a write role (403 otherwise, 401 when anonymous).
require_measurement_writer = require_role(*MEASUREMENT_WRITE_ROLES)


# =============================================================================
# Helpers
# =============================================================================


def _require_tenant_id(current_user: CurrentUser) -> int:
    """
    Resolve the tenant from the authenticated user.

    The tenant is never read from request state / the ``X-Tenant-ID`` header:
    that value is unvalidated when no JWT is present, so it must not be used to
    scope reads or writes of GA4/GTM configuration.
    """
    tenant_id = getattr(current_user, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return int(tenant_id)


def _status_value(value: Any) -> MeasurementConnectionStatus:
    """Normalize a ``MeasurementStatus`` member (or raw string) to the API literal."""
    raw = str(value.value) if isinstance(value, enum.Enum) else str(value or "")
    if raw == "connected":
        return "connected"
    if raw == "error":
        return "error"
    return "disconnected"


def _connected_status() -> Any:
    return getattr(MeasurementStatus, "CONNECTED", "connected")


def _error_status() -> Any:
    return getattr(MeasurementStatus, "ERROR", "error")


def _disconnected_status() -> Any:
    return getattr(MeasurementStatus, "DISCONNECTED", "disconnected")


def _iso(value: Optional[date]) -> Optional[str]:
    """Serialize a date to ISO-8601 or ``None``."""
    return value.isoformat() if value else None


async def _get_ga4_row(db: AsyncSession, tenant_id: int) -> Optional[TenantGA4Integration]:
    """Fetch the tenant's GA4 integration row, if any."""
    result = await db.execute(
        select(TenantGA4Integration).where(TenantGA4Integration.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def _get_gtm_row(db: AsyncSession, tenant_id: int) -> Optional[TenantGTMIntegration]:
    """Fetch the tenant's GTM integration row, if any."""
    result = await db.execute(
        select(TenantGTMIntegration).where(TenantGTMIntegration.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def _get_linked_source(
    db: AsyncSession, tenant_id: int, source_id: Optional[UUID]
) -> Optional[CDPSource]:
    """Fetch the CDP source linked to a GTM integration (tenant-scoped)."""
    if not source_id:
        return None
    result = await db.execute(
        select(CDPSource).where(
            CDPSource.id == source_id,
            CDPSource.tenant_id == tenant_id,
        )
    )
    return result.scalar_one_or_none()


def _ga4_config_response(row: TenantGA4Integration) -> GA4ConfigResponse:
    """Build the public GA4 config view (no secrets)."""
    return GA4ConfigResponse(
        property_id=row.property_id,
        measurement_id=row.measurement_id,
        service_account_email=row.service_account_email,
        service_account_fingerprint=row.service_account_fingerprint,
        has_credentials=bool(row.service_account_json_encrypted),
        conversion_event_names=list(row.conversion_event_names or ["purchase"]),
        status=_status_value(row.status),
        is_active=bool(row.is_active),
        last_verified_at=row.last_verified_at,
        last_verify_success=row.last_verify_success,
        last_verify_message=row.last_verify_message,
        last_sync_at=row.last_sync_at,
        last_sync_rows=row.last_sync_rows,
        last_error=row.last_error,
        scope=GA4_READONLY_SCOPE,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _ga4_status_response(row: Optional[TenantGA4Integration]) -> GA4StatusResponse:
    """Build the compact GA4 status view."""
    if row is None:
        return GA4StatusResponse()
    return GA4StatusResponse(
        configured=True,
        status=_status_value(row.status),
        property_id=row.property_id,
        last_sync_at=row.last_sync_at,
        last_verified_at=row.last_verified_at,
        last_error=row.last_error,
    )


def _gtm_config_response(
    row: TenantGTMIntegration, source: Optional[CDPSource]
) -> GTMConfigResponse:
    """Build the public GTM config view (no preview header value)."""
    return GTMConfigResponse(
        web_container_id=row.web_container_id,
        server_container_url=row.server_container_url,
        server_container_id=row.server_container_id,
        has_preview_header=bool(row.preview_header_encrypted),
        meta_pixel_id=row.meta_pixel_id,
        deploy_meta_pixel=bool(row.deploy_meta_pixel),
        deploy_meta_capi=bool(row.deploy_meta_capi),
        deploy_stratum_snippet=bool(row.deploy_stratum_snippet),
        status=_status_value(row.status),
        is_active=bool(row.is_active),
        cdp_source_id=str(row.cdp_source_id) if row.cdp_source_id else None,
        cdp_source_key=str(source.source_key) if source and source.source_key else None,
        last_verified_at=row.last_verified_at,
        last_verify_success=row.last_verify_success,
        last_verify_message=row.last_verify_message,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _gtm_status_response(row: Optional[TenantGTMIntegration]) -> GTMStatusResponse:
    """Build the compact GTM status view."""
    if row is None:
        return GTMStatusResponse()
    return GTMStatusResponse(
        configured=True,
        status=_status_value(row.status),
        web_container_id=row.web_container_id,
        server_container_url=row.server_container_url,
        last_verified_at=row.last_verified_at,
    )


def _stratum_ingest_url(request: Request) -> str:
    """Absolute URL of the key-only CDP ingest endpoint used by server-side GTM."""
    base = (settings.oauth_redirect_base_url or "").strip().rstrip("/")
    if not base:
        base = f"{request.url.scheme}://{request.url.netloc}"
    return f"{base}{settings.api_v1_prefix}/cdp/ingest"


def _build_client_from_json(property_id: str, service_account_json: str) -> GA4DataClient:
    """Parse service-account JSON into a client; raise HTTP 400 on invalid input."""
    try:
        return GA4DataClient.from_service_account_json(property_id, service_account_json)
    except (GA4ClientError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid service account JSON: {exc}",
        ) from exc


# =============================================================================
# Combined status
# =============================================================================


@router.get("/status", response_model=APIResponse[MeasurementStatusResponse])
async def get_measurement_status(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
) -> APIResponse[MeasurementStatusResponse]:
    """Status of both Measurement & Verification integrations (GA4 + GTM)."""
    tenant_id = _require_tenant_id(current_user)
    ga4_row = await _get_ga4_row(db, tenant_id)
    gtm_row = await _get_gtm_row(db, tenant_id)
    return APIResponse(
        success=True,
        data=MeasurementStatusResponse(
            ga4=_ga4_status_response(ga4_row),
            gtm=_gtm_status_response(gtm_row),
        ),
    )


# =============================================================================
# GA4 - read-only baseline
# =============================================================================


@router.get("/ga4", response_model=APIResponse[Optional[GA4ConfigResponse]])
async def get_ga4_config(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
) -> APIResponse[Optional[GA4ConfigResponse]]:
    """Get the tenant's GA4 read-only baseline configuration (``data`` is null when unset)."""
    tenant_id = _require_tenant_id(current_user)
    row = await _get_ga4_row(db, tenant_id)
    if row is None:
        return APIResponse(success=True, data=None)
    return APIResponse(success=True, data=_ga4_config_response(row))


@router.put("/ga4", response_model=APIResponse[GA4ConfigResponse])
async def save_ga4_config(
    body: GA4ConfigRequest,
    current_user: CurrentUser = Depends(require_measurement_writer),
    db: AsyncSession = Depends(get_async_session),
) -> APIResponse[GA4ConfigResponse]:
    """
    Create or update the GA4 configuration (one row per tenant).

    When ``service_account_json`` is provided it is parsed to extract the
    service-account email, encrypted at rest and fingerprinted. When omitted the
    stored credentials are kept (400 if none are stored yet).
    """
    tenant_id = _require_tenant_id(current_user)
    row = await _get_ga4_row(db, tenant_id)

    encrypted_json: Optional[str] = None
    service_account_email: Optional[str] = None
    fingerprint: Optional[str] = None

    if body.service_account_json:
        client = _build_client_from_json(body.property_id, body.service_account_json)
        service_account_email = client.service_account_email
        encrypted_json = encrypt_token(body.service_account_json)
        fingerprint = hash_pii_for_lookup(body.service_account_json)[:FINGERPRINT_LENGTH]
    elif row is None or not row.service_account_json_encrypted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="service_account_json is required when no credentials are stored",
        )

    created = row is None
    if row is None:
        row = TenantGA4Integration(
            tenant_id=tenant_id,
            property_id=body.property_id,
            status=_disconnected_status(),
        )
        db.add(row)

    credentials_changed = bool(encrypted_json) and fingerprint != row.service_account_fingerprint
    property_changed = row.property_id != body.property_id

    row.property_id = body.property_id
    row.measurement_id = body.measurement_id
    row.conversion_event_names = list(body.conversion_event_names)
    row.is_active = body.is_active

    if encrypted_json:
        row.service_account_json_encrypted = encrypted_json
        row.service_account_email = service_account_email
        row.service_account_fingerprint = fingerprint

    if created or credentials_changed or property_changed:
        # New credentials / property must be re-verified before they count as connected.
        row.status = _disconnected_status()
        row.last_verified_at = None
        row.last_verify_success = None
        row.last_verify_message = None
        row.last_error = None

    await db.commit()
    await db.refresh(row)

    logger.info(
        "ga4_config_saved",
        tenant_id=tenant_id,
        property_id=row.property_id,
        credentials_updated=bool(encrypted_json),
    )

    return APIResponse(
        success=True,
        data=_ga4_config_response(row),
        message="GA4 configuration saved (read-only measurement baseline)",
    )


@router.post("/ga4/test-connection", response_model=APIResponse[GA4TestResponse])
async def test_ga4_connection(
    body: Optional[GA4TestRequest] = None,
    current_user: CurrentUser = Depends(require_measurement_writer),
    db: AsyncSession = Depends(get_async_session),
) -> APIResponse[GA4TestResponse]:
    """
    Test the GA4 Data API connection (read-only report).

    Uses credentials from the request body when provided, otherwise the stored
    ones. Never fails with 500 on GA4 client errors: returns ``success=false``.
    """
    tenant_id = _require_tenant_id(current_user)
    row = await _get_ga4_row(db, tenant_id)

    body_property_id = body.property_id if body else None
    body_json = body.service_account_json if body else None
    property_id = body_property_id or (row.property_id if row else None)

    if not property_id:
        return APIResponse(
            success=True,
            data=GA4TestResponse(
                success=False,
                message="GA4 is not configured. Provide a property ID and service account JSON.",
                property_id=None,
            ),
        )

    client: Optional[GA4DataClient] = None
    failure_message: Optional[str] = None
    try:
        if body_json:
            client = GA4DataClient.from_service_account_json(property_id, body_json)
        elif row and row.service_account_json_encrypted:
            if body_property_id and body_property_id != row.property_id:
                stored_json = decrypt_token(row.service_account_json_encrypted)
                client = GA4DataClient.from_service_account_json(property_id, stored_json)
            else:
                client = await load_ga4_client_for_tenant(db, tenant_id)
        if client is None:
            failure_message = "No service account credentials are stored for GA4."
    except (GA4ClientError, ValueError, TypeError, KeyError) as exc:
        failure_message = f"Invalid service account credentials: {exc}"
    except Exception:  # pragma: no cover - defensive; never leak secrets
        logger.exception("ga4_credentials_load_failed", tenant_id=tenant_id)
        failure_message = "Stored GA4 credentials could not be loaded."

    if client is None:
        response = GA4TestResponse(
            success=False,
            message=failure_message or "GA4 credentials unavailable.",
            property_id=property_id,
        )
    else:
        try:
            result = await client.test_connection()
            response = GA4TestResponse(
                success=bool(result.success),
                message=result.message,
                property_id=result.property_id or property_id,
                sessions_last_7d=result.sessions_last_7d,
                conversions_last_7d=result.conversions_last_7d,
                revenue_last_7d=result.revenue_last_7d,
            )
        except GA4AuthError as exc:
            response = GA4TestResponse(
                success=False,
                message=f"GA4 authentication failed: {exc}",
                property_id=property_id,
            )
        except GA4ClientError as exc:
            response = GA4TestResponse(
                success=False,
                message=f"GA4 connection failed: {exc}",
                property_id=property_id,
            )
        except Exception as exc:
            logger.exception("ga4_test_connection_error", tenant_id=tenant_id)
            response = GA4TestResponse(
                success=False,
                message=f"GA4 connection failed: {exc.__class__.__name__}",
                property_id=property_id,
            )

    # Persist verification outcome when it applies to the stored configuration.
    tested_stored_config = row is not None and not body_json and property_id == row.property_id
    if row is not None and tested_stored_config:
        now = datetime.now(UTC)
        row.last_verified_at = now
        row.last_verify_success = response.success
        row.last_verify_message = response.message[:2000] if response.message else None
        row.status = _connected_status() if response.success else _error_status()
        row.last_error = None if response.success else response.message
        await db.commit()

    logger.info(
        "ga4_test_connection",
        tenant_id=tenant_id,
        property_id=property_id,
        success=response.success,
    )
    return APIResponse(success=True, data=response)


@router.post("/ga4/sync", response_model=APIResponse[GA4SyncResponse])
async def sync_ga4(
    body: Optional[GA4SyncRequest] = None,
    current_user: CurrentUser = Depends(require_measurement_writer),
    db: AsyncSession = Depends(get_async_session),
) -> APIResponse[GA4SyncResponse]:
    """
    Run the GA4 daily baseline sync inline for this tenant.

    Returns ``configured=false, success=false`` with HTTP 200 when GA4 is not set up.
    """
    tenant_id = _require_tenant_id(current_user)
    lookback_days = body.lookback_days if body else None
    backfill = bool(body.backfill) if body else False

    try:
        result = await sync_ga4_for_tenant(
            db,
            tenant_id,
            lookback_days=lookback_days,
            backfill=backfill,
        )
        response = GA4SyncResponse(
            configured=result.configured,
            success=result.success,
            rows_upserted=result.rows_upserted,
            start_date=_iso(result.start_date),
            end_date=_iso(result.end_date),
            message=result.message,
        )
    except GA4ClientError as exc:
        response = GA4SyncResponse(
            configured=True,
            success=False,
            rows_upserted=0,
            message=f"GA4 sync failed: {exc}",
        )
    except Exception as exc:
        logger.exception("ga4_sync_error", tenant_id=tenant_id)
        response = GA4SyncResponse(
            configured=True,
            success=False,
            rows_upserted=0,
            message=f"GA4 sync failed: {exc.__class__.__name__}",
        )

    return APIResponse(success=True, data=response)


@router.get("/ga4/baseline", response_model=APIResponse[GA4BaselineResponse])
async def get_ga4_baseline_summary(
    start_date: Optional[date] = Query(None, description="Inclusive start (default: 30 days ago)"),
    end_date: Optional[date] = Query(None, description="Inclusive end (default: today, UTC)"),
    meta_only: bool = Query(False, description="Only sessions classified as Meta traffic"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
) -> APIResponse[GA4BaselineResponse]:
    """GA4 baseline (sessions / conversions / revenue) for a date range; defaults to last 30 days."""
    tenant_id = _require_tenant_id(current_user)

    resolved_end = end_date or datetime.now(UTC).date()
    resolved_start = start_date or (resolved_end - timedelta(days=DEFAULT_BASELINE_DAYS - 1))
    if resolved_start > resolved_end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date must be on or before end_date",
        )
    if (resolved_end - resolved_start).days > MAX_BASELINE_RANGE_DAYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Date range must be at most {MAX_BASELINE_RANGE_DAYS} days",
        )

    summary = await get_ga4_baseline(
        db,
        tenant_id,
        start_date=resolved_start,
        end_date=resolved_end,
        meta_only=meta_only,
    )

    daily = [
        GA4DailyPoint(
            date=str(point.get("date")),
            sessions=int(point.get("sessions") or 0),
            conversions=int(point.get("conversions") or 0),
            revenue=float(point.get("revenue") or 0.0),
        )
        for point in (summary.daily or [])
    ]

    return APIResponse(
        success=True,
        data=GA4BaselineResponse(
            start_date=summary.start_date.isoformat(),
            end_date=summary.end_date.isoformat(),
            meta_only=summary.meta_only,
            sessions=summary.sessions,
            conversions=summary.conversions,
            revenue=summary.revenue,
            total_revenue=summary.total_revenue,
            days_with_data=summary.days_with_data,
            last_date=_iso(summary.last_date),
            daily=daily,
        ),
    )


@router.delete("/ga4", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_ga4(
    current_user: CurrentUser = Depends(require_measurement_writer),
    db: AsyncSession = Depends(get_async_session),
) -> None:
    """Remove the GA4 configuration and its encrypted credentials."""
    tenant_id = _require_tenant_id(current_user)
    row = await _get_ga4_row(db, tenant_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="GA4 integration not configured",
        )
    await db.delete(row)
    await db.commit()
    logger.info("ga4_disconnected", tenant_id=tenant_id)


# =============================================================================
# GTM - tag deployment
# =============================================================================


@router.get("/gtm", response_model=APIResponse[Optional[GTMConfigResponse]])
async def get_gtm_config(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
) -> APIResponse[Optional[GTMConfigResponse]]:
    """Get the tenant's GTM tag-deployment configuration (``data`` is null when unset)."""
    tenant_id = _require_tenant_id(current_user)
    row = await _get_gtm_row(db, tenant_id)
    if row is None:
        return APIResponse(success=True, data=None)
    source = await _get_linked_source(db, tenant_id, row.cdp_source_id)
    return APIResponse(success=True, data=_gtm_config_response(row, source))


@router.put("/gtm", response_model=APIResponse[GTMConfigResponse])
async def save_gtm_config(
    body: GTMConfigRequest,
    current_user: CurrentUser = Depends(require_measurement_writer),
    db: AsyncSession = Depends(get_async_session),
) -> APIResponse[GTMConfigResponse]:
    """
    Create or update the GTM configuration (one row per tenant).

    Validates container IDs / the server-side tagging URL, encrypts the optional
    preview header, and links (or creates) the CDP ``sgtm`` source.
    """
    tenant_id = _require_tenant_id(current_user)

    try:
        web_container_id = (
            validate_container_id(body.web_container_id) if body.web_container_id else None
        )
        server_container_id = (
            validate_container_id(body.server_container_id) if body.server_container_id else None
        )
        server_container_url = (
            validate_server_container_url(body.server_container_url)
            if body.server_container_url
            else None
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    row = await _get_gtm_row(db, tenant_id)
    created = row is None
    if row is None:
        row = TenantGTMIntegration(tenant_id=tenant_id, status=_disconnected_status())
        db.add(row)

    containers_changed = (
        row.web_container_id != web_container_id
        or row.server_container_url != server_container_url
    )

    row.web_container_id = web_container_id
    row.server_container_url = server_container_url
    row.server_container_id = server_container_id
    row.meta_pixel_id = body.meta_pixel_id
    row.deploy_meta_pixel = body.deploy_meta_pixel
    row.deploy_meta_capi = body.deploy_meta_capi
    row.deploy_stratum_snippet = body.deploy_stratum_snippet
    row.is_active = body.is_active

    if body.preview_header is not None:
        # Empty string clears the stored header; None keeps it.
        row.preview_header_encrypted = (
            encrypt_token(body.preview_header) if body.preview_header else None
        )

    if created or containers_changed:
        row.status = _disconnected_status()
        row.last_verified_at = None
        row.last_verify_success = None
        row.last_verify_message = None
        row.last_error = None

    source = await ensure_sgtm_source(
        db,
        tenant_id,
        web_container_id=web_container_id,
        server_container_url=server_container_url,
    )
    row.cdp_source_id = source.id

    await db.commit()
    await db.refresh(row)

    logger.info(
        "gtm_config_saved",
        tenant_id=tenant_id,
        web_container_id=web_container_id,
        has_server_container=bool(server_container_url),
        cdp_source_id=str(source.id),
    )

    return APIResponse(
        success=True,
        data=_gtm_config_response(row, source),
        message="GTM configuration saved (tag deployment)",
    )


@router.post("/gtm/verify", response_model=APIResponse[GTMVerifyResponse])
async def verify_gtm(
    current_user: CurrentUser = Depends(require_measurement_writer),
    db: AsyncSession = Depends(get_async_session),
) -> APIResponse[GTMVerifyResponse]:
    """Verify that the configured web / server containers are reachable."""
    tenant_id = _require_tenant_id(current_user)
    row = await _get_gtm_row(db, tenant_id)
    now = datetime.now(UTC)

    if row is None:
        return APIResponse(
            success=True,
            data=GTMVerifyResponse(
                success=False,
                message="GTM is not configured.",
                web_container_ok=None,
                server_container_ok=None,
                checked_at=now.isoformat(),
            ),
        )

    timeout_seconds = float(getattr(settings, "gtm_verify_timeout_seconds", 10.0) or 10.0)
    try:
        result = await verify_containers(
            row.web_container_id,
            row.server_container_url,
            timeout_seconds=timeout_seconds,
        )
        response = GTMVerifyResponse(
            success=bool(result.success),
            message=result.message,
            web_container_ok=result.web_container_ok,
            server_container_ok=result.server_container_ok,
            checked_at=(result.checked_at or now).isoformat(),
        )
    except Exception as exc:
        logger.exception("gtm_verify_error", tenant_id=tenant_id)
        response = GTMVerifyResponse(
            success=False,
            message=f"Container verification failed: {exc.__class__.__name__}",
            web_container_ok=None,
            server_container_ok=None,
            checked_at=now.isoformat(),
        )

    row.last_verified_at = now
    row.last_verify_success = response.success
    row.last_verify_message = response.message[:2000] if response.message else None
    row.status = _connected_status() if response.success else _error_status()
    row.last_error = None if response.success else response.message
    await db.commit()

    return APIResponse(success=True, data=response)


@router.get("/gtm/snippets", response_model=APIResponse[GTMSnippetsResponse])
async def get_gtm_snippets(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
) -> APIResponse[GTMSnippetsResponse]:
    """Ready-to-paste GTM web snippets plus the server-side tagging (sGTM) configuration."""
    tenant_id = _require_tenant_id(current_user)
    row = await _get_gtm_row(db, tenant_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="GTM integration not configured",
        )

    source = await _get_linked_source(db, tenant_id, row.cdp_source_id)
    snippets = build_snippets(
        web_container_id=row.web_container_id,
        server_container_url=row.server_container_url,
        stratum_ingest_url=_stratum_ingest_url(request),
        source_key=str(source.source_key) if source and source.source_key else None,
        meta_pixel_id=row.meta_pixel_id,
    )

    return APIResponse(
        success=True,
        data=GTMSnippetsResponse(
            head_snippet=snippets.head_snippet,
            body_snippet=snippets.body_snippet,
            stratum_snippet=snippets.stratum_snippet,
            sgtm_config=dict(snippets.sgtm_config or {}),
        ),
    )


@router.delete("/gtm", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_gtm(
    current_user: CurrentUser = Depends(require_measurement_writer),
    db: AsyncSession = Depends(get_async_session),
) -> None:
    """Remove the GTM configuration (the linked CDP source is kept for event history)."""
    tenant_id = _require_tenant_id(current_user)
    row = await _get_gtm_row(db, tenant_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="GTM integration not configured",
        )
    await db.delete(row)
    await db.commit()
    logger.info("gtm_disconnected", tenant_id=tenant_id)


# =============================================================================
# Disconnect both
# =============================================================================


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_measurement(
    current_user: CurrentUser = Depends(require_measurement_writer),
    db: AsyncSession = Depends(get_async_session),
) -> None:
    """Remove both the GA4 and GTM configurations for the tenant."""
    tenant_id = _require_tenant_id(current_user)
    ga4_row = await _get_ga4_row(db, tenant_id)
    gtm_row = await _get_gtm_row(db, tenant_id)

    if ga4_row is not None:
        await db.delete(ga4_row)
    if gtm_row is not None:
        await db.delete(gtm_row)
    await db.commit()

    logger.info(
        "measurement_disconnected",
        tenant_id=tenant_id,
        ga4_removed=ga4_row is not None,
        gtm_removed=gtm_row is not None,
    )
