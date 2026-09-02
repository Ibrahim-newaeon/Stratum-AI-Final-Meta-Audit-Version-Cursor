# =============================================================================
# Stratum AI - OAuth Service Factory
# =============================================================================
"""
Factory for platform OAuth services.

Stratum AI acts only on Meta channels (Facebook, Instagram, WhatsApp),
so the only supported OAuth provider is Meta.
"""

from app.services.oauth.base import OAuthService
from app.services.oauth.meta import MetaOAuthService

_SERVICES: dict[str, type[OAuthService]] = {
    "meta": MetaOAuthService,
    # Facebook/Instagram/WhatsApp ad accounts all authenticate through Meta.
    "facebook": MetaOAuthService,
    "instagram": MetaOAuthService,
    "whatsapp": MetaOAuthService,
}


def get_oauth_service(platform: str) -> OAuthService:
    """
    Get the OAuth service for a platform.

    Args:
        platform: Platform identifier (meta, facebook, instagram, whatsapp)

    Returns:
        Initialized OAuth service instance

    Raises:
        ValueError: If the platform is not a supported Meta channel
    """
    service_cls = _SERVICES.get(platform.lower())
    if service_cls is None:
        raise ValueError(
            f"Unsupported OAuth platform '{platform}'. "
            "Stratum AI acts only on Meta channels (Facebook, Instagram, WhatsApp)."
        )
    return service_cls()
