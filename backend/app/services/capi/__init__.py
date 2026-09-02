# =============================================================================
# Stratum AI - Conversion API (CAPI) Service
# =============================================================================
"""
Server-side Conversion API integration for streaming first-party data
to Meta ad platforms (Facebook, Instagram, WhatsApp).

Features:
- No-code platform connection via API tokens
- AI-powered event mapping
- Automatic PII hashing (SHA256)
- Event Match Quality scoring
- Data gap analysis and recommendations
"""

from .capi_service import CAPIService
from .data_quality import DataQualityAnalyzer
from .event_mapper import AIEventMapper
from .pii_hasher import PIIHasher
from .platform_connectors import MetaCAPIConnector

__all__ = [
    "CAPIService",
    "AIEventMapper",
    "PIIHasher",
    "DataQualityAnalyzer",
    "MetaCAPIConnector",
]
