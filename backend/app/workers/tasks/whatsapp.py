# =============================================================================
# Stratum AI - WhatsApp Messaging Tasks
# =============================================================================
"""
Background tasks for WhatsApp Business API messaging.
"""

from datetime import UTC, datetime, timedelta
from typing import Optional

from celery import shared_task
from celery.utils.log import get_task_logger
from sqlalchemy import func, select

from app.core.config import settings
from app.db.session import SyncSessionLocal
from app.models import (
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppMessageStatus,
    WhatsAppTemplate,
)

logger = get_task_logger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def send_whatsapp_message(
    self,
    tenant_id: int,
    template_name: str,
    to_number: str,
    variables: Optional[dict] = None,
    media_url: Optional[str] = None,
):
    """
    Send a WhatsApp message using approved template.

    Args:
        tenant_id: Tenant ID for isolation
        template_name: Name of approved template
        to_number: Recipient phone number (E.164 format)
        variables: Template variable substitutions
        media_url: Optional media attachment URL
    """
    logger.info(f"Sending WhatsApp to {to_number} for tenant {tenant_id}")

    with SyncSessionLocal() as db:
        # Get template
        template = db.execute(
            select(WhatsAppTemplate).where(
                WhatsAppTemplate.tenant_id == tenant_id,
                WhatsAppTemplate.name == template_name,
                WhatsAppTemplate.status == "approved",
            )
        ).scalar_one_or_none()

        if not template:
            logger.error(f"Template {template_name} not found or not approved")
            return {"status": "template_not_found"}

        # Get or create contact
        contact = db.execute(
            select(WhatsAppContact).where(
                WhatsAppContact.tenant_id == tenant_id,
                WhatsAppContact.phone_number == to_number,
            )
        ).scalar_one_or_none()

        if not contact:
            contact = WhatsAppContact(
                tenant_id=tenant_id,
                phone_number=to_number,
            )
            db.add(contact)
            db.flush()

        # Create message record
        message = WhatsAppMessage(
            tenant_id=tenant_id,
            contact_id=contact.id,
            template_id=template.id,
            direction="outbound",
            status=WhatsAppMessageStatus.PENDING,
            content=template.body,
            variables=variables or {},
            media_url=media_url,
        )
        db.add(message)
        db.flush()

        try:
            # Send via WhatsApp Business API
            from app.services.whatsapp.client import WhatsAppClient

            client = WhatsAppClient(tenant_id)
            result = client.send_template_message(
                to=to_number,
                template=template_name,
                language=template.language,
                components=_build_template_components(template, variables, media_url),
            )

            message.external_id = result.get("message_id")
            message.status = WhatsAppMessageStatus.SENT
            message.sent_at = datetime.now(UTC)

            db.commit()

            logger.info(f"WhatsApp message sent: {message.external_id}")
            return {
                "status": "sent",
                "message_id": message.external_id,
            }

        except Exception as e:
            message.status = WhatsAppMessageStatus.FAILED
            message.error_message = str(e)
            db.commit()
            logger.error(f"WhatsApp send failed: {e}")
            raise


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
            send_whatsapp_message.delay(
                tenant_id=message.tenant_id,
                template_name=message.template.name if message.template else None,
                to_number=message.contact.phone_number if message.contact else None,
                variables=message.variables,
                media_url=message.media_url,
            )
            # Take the message out of the pending set. Nothing else does: this
            # task dispatches by tenant/template/number rather than by message
            # id, so the row was never updated and the same message was
            # re-queued on every single beat tick, forever.
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

    if media_url and template.header_type in ("image", "video", "document"):
        components.append(
            {
                "type": "header",
                "parameters": [{"type": template.header_type, "url": media_url}],
            }
        )

    if variables:
        body_params = [{"type": "text", "text": str(v)} for v in variables.values()]
        if body_params:
            components.append({"type": "body", "parameters": body_params})

    return components
