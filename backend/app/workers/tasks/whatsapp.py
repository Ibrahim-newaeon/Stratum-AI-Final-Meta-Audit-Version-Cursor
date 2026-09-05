# =============================================================================
# Stratum AI - WhatsApp Messaging Tasks
# =============================================================================
"""
Background tasks for WhatsApp Business API messaging.

Every send starts from a ``WhatsAppMessage`` row the API layer has already
committed, and the task is handed only its id. That row is the single record of
the send: the task resolves the contact and template from it, calls the Graph
API, and writes the outcome back. ``wamid`` - set only once Meta has accepted
the message - is the idempotency key, so neither a beat re-dispatch nor a
Celery retry can send the same row twice.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from celery import shared_task
from celery.utils.log import get_task_logger
from sqlalchemy import func, select

from app.core.config import settings
from app.db.session import SyncSessionLocal
from app.models import (
    WhatsAppMessage,
    WhatsAppMessageStatus,
    WhatsAppOptInStatus,
    WhatsAppTemplate,
    WhatsAppTemplateStatus,
)
from app.services.whatsapp_client import WhatsAppAPIError

logger = get_task_logger(__name__)


def _refuse(db, message: WhatsAppMessage, reason: str) -> dict[str, Any]:
    """Record why a message will not be sent and take it out of flight."""
    message.status = WhatsAppMessageStatus.FAILED
    message.error_message = reason
    db.commit()
    logger.error(f"WhatsApp message {message.id} not sent: {reason}")
    return {"status": "failed", "message_id": message.id, "reason": reason}


@shared_task(
    bind=True,
    autoretry_for=(WhatsAppAPIError,),
    retry_backoff=True,
    max_retries=3,
)
def send_whatsapp_message(self, message_id: int, tenant_id: int) -> dict[str, Any]:
    """
    Send one already-persisted WhatsApp message.

    The row carries everything the send needs, so the task never creates a
    second one. A row that already has a ``wamid`` is left alone.

    Args:
        message_id: ``WhatsAppMessage`` row to send.
        tenant_id: Tenant the row must belong to; a row outside it is not found.

    Returns:
        A status dict: ``sent``, ``already_sent``, ``failed`` or ``not_found``.
    """
    with SyncSessionLocal() as db:
        message = db.execute(
            select(WhatsAppMessage).where(
                WhatsAppMessage.id == message_id,
                WhatsAppMessage.tenant_id == tenant_id,
            )
        ).scalar_one_or_none()

        if message is None:
            logger.error(f"WhatsApp message {message_id} not found for tenant {tenant_id}")
            return {"status": "not_found", "message_id": message_id}

        # Meta has already accepted this row. A re-dispatch from beat, or a
        # retry after a failure later in this task, must not send it again.
        if message.wamid:
            return {
                "status": "already_sent",
                "message_id": message.id,
                "wamid": message.wamid,
            }

        contact = message.contact
        if contact is None:
            return _refuse(db, message, "contact no longer exists")
        # Opt-in is re-checked here, not just at the API layer: a broadcast can
        # sit in the queue long enough for the contact to opt out.
        if contact.opt_in_status != WhatsAppOptInStatus.OPTED_IN:
            return _refuse(db, message, "contact has not opted in")

        template = message.template
        if template is None and message.template_name:
            template = db.execute(
                select(WhatsAppTemplate).where(
                    WhatsAppTemplate.tenant_id == tenant_id,
                    WhatsAppTemplate.name == message.template_name,
                    WhatsAppTemplate.status == WhatsAppTemplateStatus.APPROVED,
                )
            ).scalar_one_or_none()

        if message.message_type == "template" and template is None:
            return _refuse(
                db,
                message,
                f"template {message.template_name!r} not found or not approved",
            )

        from app.services.whatsapp.client import WhatsAppClient

        client = WhatsAppClient(tenant_id)
        try:
            if template is not None:
                result = client.send_template_message(
                    to=contact.phone_number,
                    template=template.name,
                    language=template.language,
                    components=_build_template_components(
                        template, message.template_variables, message.media_url
                    ),
                )
            else:
                result = client.send_text_message(
                    to=contact.phone_number,
                    body=message.content or "",
                )
        except Exception as e:
            message.retry_count += 1
            _refuse(db, message, str(e))
            raise

        message.wamid = result.get("message_id")
        message.status = WhatsAppMessageStatus.SENT
        message.sent_at = datetime.now(UTC)
        db.commit()

        logger.info(f"WhatsApp message {message.id} sent: {message.wamid}")
        return {"status": "sent", "message_id": message.id, "wamid": message.wamid}


@shared_task
def send_whatsapp_broadcast(message_ids: list[int], tenant_id: int) -> dict[str, Any]:
    """
    Queue one send task per message row of a broadcast.

    The API layer has already committed one ``WhatsAppMessage`` per opted-in
    recipient, so the request pays for a single enqueue however large the
    audience is, and the fan-out happens on the worker.

    Args:
        message_ids: Rows created for this broadcast.
        tenant_id: Tenant the rows belong to.

    Returns:
        How many sends were queued.
    """
    for message_id in message_ids:
        send_whatsapp_message.delay(message_id=message_id, tenant_id=tenant_id)

    logger.info(f"Broadcast queued {len(message_ids)} messages for tenant {tenant_id}")
    return {"queued": len(message_ids)}


@shared_task
def process_scheduled_whatsapp_messages():
    """
    Process scheduled WhatsApp messages.
    Scheduled every minute by Celery beat.
    """
    logger.info("Processing scheduled WhatsApp messages")

    with SyncSessionLocal() as db:
        now = datetime.now(UTC)
        # Lower bound on how overdue a message may be. The worker consumed no
        # queues until the queue-list fix, so a real tenant's backlog could
        # stretch back to deployment; without this the first worker start would
        # send all of it at once.
        oldest = now - timedelta(hours=settings.scheduled_dispatch_max_age_hours)

        # Get pending scheduled messages
        messages = (
            db.execute(
                select(WhatsAppMessage).where(
                    WhatsAppMessage.status == WhatsAppMessageStatus.PENDING,
                    WhatsAppMessage.scheduled_at <= now,
                    WhatsAppMessage.scheduled_at >= oldest,
                    WhatsAppMessage.scheduled_at.isnot(None),
                )
            )
            .scalars()
            .all()
        )

        stale_count = (
            db.execute(
                select(func.count())
                .select_from(WhatsAppMessage)
                .where(
                    WhatsAppMessage.status == WhatsAppMessageStatus.PENDING,
                    WhatsAppMessage.scheduled_at < oldest,
                )
            ).scalar()
            or 0
        )

        task_count = 0
        for message in messages:
            send_whatsapp_message.delay(message_id=message.id, tenant_id=message.tenant_id)
            # Take the message out of the pending set before the beat ticks
            # again a minute from now, or the same row is re-queued on every
            # tick until the send task gets to it. This is optimistic: the send
            # task writes the real outcome, and refuses to send a row twice
            # because it checks wamid rather than status.
            message.status = WhatsAppMessageStatus.SENT
            message.sent_at = now
            task_count += 1

        db.commit()

    if stale_count:
        logger.warning(
            f"Skipped {stale_count} scheduled WhatsApp message(s) more than "
            f"{settings.scheduled_dispatch_max_age_hours}h overdue; review them manually"
        )
    logger.info(f"Queued {task_count} scheduled WhatsApp messages")
    return {"queued": task_count, "skipped_stale": stale_count}


def _build_template_components(
    template: WhatsAppTemplate,
    variables: Optional[dict],
    media_url: Optional[str],
) -> list:
    """Build WhatsApp template components from variables."""
    components = []

    # header_type is stored as free text and the model documents it uppercase
    # ("IMAGE", "VIDEO", ...), so normalize once and use that, rather than
    # lower-casing an Optional[str] a second time inside the branch.
    header_type = (template.header_type or "").lower()
    if media_url and header_type in ("image", "video", "document"):
        components.append(
            {
                "type": "header",
                "parameters": [{"type": header_type, "url": media_url}],
            }
        )

    if variables:
        body_params = [{"type": "text", "text": str(v)} for v in variables.values()]
        if body_params:
            components.append({"type": "body", "parameters": body_params})

    return components
