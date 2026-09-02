# =============================================================================
# Stratum AI - Adapter Registry
# =============================================================================
"""
Adapter registry for Meta advertising channels.

Maps a Platform enum value to its adapter implementation and caches
initialized adapter instances. Stratum AI acts only on Meta channels
(Facebook, Instagram, WhatsApp).
"""

import logging
from typing import Any

from app.stratum.adapters.base import BaseAdapter, ValidationError
from app.stratum.models import Platform

logger = logging.getLogger("stratum.adapters.registry")


class AdapterRegistry:
    """Registry of platform adapters (Meta channels only)."""

    _instances: dict[str, BaseAdapter] = {}

    @classmethod
    def _adapter_class(cls, platform: Platform) -> type[BaseAdapter]:
        """Resolve the adapter class for a platform (lazy imports)."""
        if platform == Platform.META:
            from app.stratum.adapters.meta_adapter import MetaAdapter

            return MetaAdapter
        if platform == Platform.WHATSAPP:
            from app.stratum.adapters.whatsapp_adapter import WhatsAppAdapter

            return WhatsAppAdapter
        raise ValidationError(
            f"Unsupported platform '{platform}'. "
            "Stratum AI acts only on Meta channels (Facebook, Instagram, WhatsApp)."
        )

    @classmethod
    def get_adapter(cls, platform: Platform, credentials: dict[str, Any]) -> BaseAdapter:
        """Get (or create) an adapter instance for a platform."""
        adapter_cls = cls._adapter_class(platform)
        cache_key = f"{platform.value}"
        if cache_key not in cls._instances:
            cls._instances[cache_key] = adapter_cls(credentials)
            logger.info(f"Created {adapter_cls.__name__} for platform '{platform.value}'")
        return cls._instances[cache_key]

    @classmethod
    def clear(cls) -> None:
        """Clear cached adapter instances (used in tests)."""
        cls._instances.clear()


async def get_adapter(platform: Platform, credentials: dict[str, Any]) -> BaseAdapter:
    """
    Async helper to get an initialized adapter for a Meta channel.

    Args:
        platform: Platform enum value (META or WHATSAPP)
        credentials: Platform credentials

    Returns:
        Initialized adapter instance
    """
    adapter = AdapterRegistry.get_adapter(platform, credentials)
    initialize = getattr(adapter, "initialize", None)
    if callable(initialize):
        result = initialize()
        if hasattr(result, "__await__"):
            await result
    return adapter
