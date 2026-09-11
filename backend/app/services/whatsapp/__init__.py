# =============================================================================
# Stratum AI - WhatsApp Services Package
# =============================================================================
"""WhatsApp Business API service package (Meta Graph API)."""

from app.services.whatsapp.client import WhatsAppAPIError, WhatsAppClient
from app.services.whatsapp.credentials_store import (
    ResolvedWhatsAppCredentials,
    WhatsAppNotConfiguredError,
    deactivate_credentials,
    public_status,
    resolve_credentials,
    resolve_credentials_sync,
    upsert_credentials,
)

__all__ = [
    "WhatsAppAPIError",
    "WhatsAppClient",
    "ResolvedWhatsAppCredentials",
    "WhatsAppNotConfiguredError",
    "deactivate_credentials",
    "public_status",
    "resolve_credentials",
    "resolve_credentials_sync",
    "upsert_credentials",
]
