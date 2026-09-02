# =============================================================================
# Stratum AI - OAuth Services
# =============================================================================
"""
OAuth service implementations for Meta ad platform integrations.
Supports Meta (Facebook, Instagram, WhatsApp).
"""

from app.services.oauth.base import AdAccountInfo, OAuthService, OAuthState, OAuthTokens
from app.services.oauth.factory import get_oauth_service
from app.services.oauth.meta import MetaOAuthService

__all__ = [
    "OAuthService",
    "OAuthState",
    "OAuthTokens",
    "AdAccountInfo",
    "MetaOAuthService",
    "get_oauth_service",
]
