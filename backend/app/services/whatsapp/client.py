# =============================================================================
# Stratum AI - WhatsApp Sync Client (Worker Tasks)
# =============================================================================
"""
Synchronous WhatsApp Business API client for Celery worker tasks.

The async client in ``app.services.whatsapp_client`` serves the API layer;
this thin synchronous variant is used from Celery tasks where an event loop
is not available. Credentials come from tenant-agnostic app settings.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger

# Re-export the canonical error type so both clients raise the same exception
from app.services.whatsapp_client import WhatsAppAPIError

logger = get_logger(__name__)

__all__ = ["WhatsAppAPIError", "WhatsAppClient"]


class WhatsAppClient:
    """Synchronous WhatsApp Business (Meta Graph API) client."""

    def __init__(
        self,
        tenant_id: Optional[int] = None,
        phone_number_id: Optional[str] = None,
        access_token: Optional[str] = None,
        api_version: Optional[str] = None,
    ):
        """
        Initialize the client.

        Args:
            tenant_id: Tenant on whose behalf messages are sent (for logging).
            phone_number_id: WhatsApp Business Phone Number ID override.
            access_token: Meta Graph API access token override.
            api_version: Graph API version override.
        """
        self.tenant_id = tenant_id
        self.phone_number_id = phone_number_id or getattr(
            settings, "whatsapp_phone_number_id", None
        )
        self.access_token = access_token or getattr(settings, "whatsapp_access_token", None)
        self.api_version = api_version or getattr(settings, "whatsapp_api_version", "v18.0")
        self.base_url = f"https://graph.facebook.com/{self.api_version}"

    def _ensure_configured(self) -> None:
        if not self.phone_number_id or not self.access_token:
            raise WhatsAppAPIError("WhatsApp API is not configured for this environment")

    def _post_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST a message payload to the Graph API and normalize the response."""
        self._ensure_configured()
        url = f"{self.base_url}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=30.0)
        except httpx.HTTPError as e:
            raise WhatsAppAPIError(f"WhatsApp API request failed: {e}") from e

        data: dict[str, Any] = {}
        try:
            data = response.json()
        except ValueError:
            pass

        if response.status_code >= 400:
            error = (data or {}).get("error", {})
            raise WhatsAppAPIError(
                error.get("message", f"WhatsApp API error (HTTP {response.status_code})")
            )

        messages = data.get("messages") or [{}]
        return {
            "message_id": messages[0].get("id"),
            "raw": data,
        }

    def send_template_message(
        self,
        to: str,
        template: str,
        language: str = "en",
        components: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Send a pre-approved template message; returns {'message_id': ...}."""
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": language},
            },
        }
        if components:
            payload["template"]["components"] = components

        logger.info(
            "whatsapp_template_send",
            tenant_id=self.tenant_id,
            template=template,
            language=language,
        )
        return self._post_message(payload)

    def send_text_message(self, to: str, body: str) -> dict[str, Any]:
        """Send a plain text message (within the 24-hour session window)."""
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body},
        }
        return self._post_message(payload)
