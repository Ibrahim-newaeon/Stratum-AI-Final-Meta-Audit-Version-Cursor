# =============================================================================
# Stratum AI - CDP → Meta CAPI fan-out
# =============================================================================
"""Forward trusted CDP ingest events to tenant-scoped Conversions API.

EMQ / signal-health scores are measured from ``capi_delivery_logs``. CDP
ingest alone only stores ``CDPEvent`` rows; this module is the missing bridge
that sends accepted events through ``CAPIService`` with ``tenant_id`` so
delivery attempts are attributed correctly.

Fails soft: CAPI errors are logged and never fail CDP ingest.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.schemas.cdp import EventInput
from app.services.capi.capi_service import CAPIService

logger = get_logger(__name__)

# CDP identifier types → Meta CAPI user_data keys (values hashed by CAPIService).
_IDENTIFIER_MAP = {
    "email": "em",
    "phone": "ph",
    "external_id": "external_id",
    "fbp": "fbp",
    "fbc": "fbc",
}


def advertising_consent_allows(event: EventInput) -> bool:
    """Return True when fan-out is allowed under consent rules.

    - No consent object: allow (trusted server-side ingest such as sGTM).
    - Consent present: require explicit ``advertising=True`` (fail closed).
    """
    if event.consent is None:
        return True
    return event.consent.advertising is True


def cdp_event_to_capi_payload(event: EventInput) -> dict[str, Any]:
    """Map one CDP ``EventInput`` into the dict ``CAPIService.stream_events`` expects."""
    user_data: dict[str, Any] = {}
    for ident in event.identifiers or []:
        key = _IDENTIFIER_MAP.get(ident.type or "")
        if key and ident.value:
            user_data[key] = ident.value

    event_time = event.event_time
    if isinstance(event_time, datetime):
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=UTC)
        ts = int(event_time.timestamp())
    else:
        ts = int(datetime.now(UTC).timestamp())

    page_url = event.context.page_url if event.context is not None else None

    return {
        "event_name": event.event_name,
        "user_data": user_data,
        "parameters": dict(event.properties or {}),
        "event_time": ts,
        "event_source_url": page_url,
        "event_id": event.idempotency_key,
    }


async def fanout_cdp_events_to_capi(
    db: AsyncSession,
    *,
    tenant_id: int,
    events: list[EventInput],
    platforms: list[str] | None = None,
) -> dict[str, Any]:
    """Send consenting CDP events to Meta CAPI for ``tenant_id``.

    Hydrates durable ``tenant_capi_credentials`` into the process cache first.
    Returns a small result summary; never raises to the caller.
    """
    summary: dict[str, Any] = {
        "attempted": 0,
        "skipped_consent": 0,
        "skipped_no_credentials": 0,
        "sent": 0,
        "error": None,
    }

    try:
        payloads: list[dict[str, Any]] = []
        for event in events:
            if not advertising_consent_allows(event):
                summary["skipped_consent"] += 1
                continue
            payloads.append(cdp_event_to_capi_payload(event))

        summary["attempted"] = len(payloads)
        if not payloads:
            return summary

        service = CAPIService(tenant_id=tenant_id)
        loaded = await service.ensure_loaded_from_db(db)
        targets = platforms or ["meta"]
        connected = [p for p in targets if p in service.connectors]
        if not connected:
            summary["skipped_no_credentials"] = len(payloads)
            logger.info(
                "cdp_capi_fanout_skipped_no_credentials",
                tenant_id=tenant_id,
                events=len(payloads),
                hydrated=loaded,
            )
            return summary

        result = await service.stream_events(payloads, platforms=connected)
        summary["sent"] = result.total_events
        summary["platforms_sent"] = result.platforms_sent
        summary["failed_platforms"] = result.failed_platforms
        logger.info(
            "cdp_capi_fanout_complete",
            tenant_id=tenant_id,
            events=len(payloads),
            platforms=connected,
            failed=result.failed_platforms,
        )
        return summary
    except Exception as exc:
        summary["error"] = str(exc)
        logger.exception(
            "cdp_capi_fanout_failed",
            tenant_id=tenant_id,
            error=str(exc),
        )
        return summary
