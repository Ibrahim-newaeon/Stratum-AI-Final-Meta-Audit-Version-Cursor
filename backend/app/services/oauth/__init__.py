# =============================================================================
# Stratum AI - OAuth Services
# =============================================================================
"""
OAuth service implementations for Meta ad platform integrations.
Supports Meta (Facebook, Instagram, WhatsApp).
"""

from app.services.oauth.ad_account_sync import sync_connection_ad_accounts, upsert_ad_accounts
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
    "upsert_ad_accounts",
    "sync_connection_ad_accounts",
]
