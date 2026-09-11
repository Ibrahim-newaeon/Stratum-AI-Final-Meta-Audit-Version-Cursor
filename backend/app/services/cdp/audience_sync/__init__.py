# =============================================================================
# Stratum AI - CDP Audience Sync Module
# =============================================================================
"""
Audience sync services for pushing CDP segments to Meta ad platforms.

Supported Platforms:
- Meta (Facebook, Instagram, WhatsApp) Custom Audiences
"""

from .base import AudienceSyncResult, BaseAudienceConnector
from .meta_connector import MetaAudienceConnector
from .service import AudienceSyncService, list_due_auto_sync_audiences

__all__ = [
    "AudienceSyncService",
    "BaseAudienceConnector",
    "AudienceSyncResult",
    "MetaAudienceConnector",
    "list_due_auto_sync_audiences",
]
