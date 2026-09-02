# =============================================================================
# Stratum AI - Embed Widget Service
# =============================================================================
"""
Widget configuration and domain-whitelist management for embeddable widgets.

Responsibilities:
- Widget CRUD scoped to a tenant, with tier-based limits and branding levels
- Domain whitelist management (domains must be whitelisted before tokens can
  be bound to them; see ``EmbedTokenService``)
- Embed code generation (iframe + script snippets)

Branding level is derived from the subscription tier:
- Starter: full Stratum branding
- Professional: minimal "Powered by Stratum" branding
- Enterprise: no branding (white-label, custom colours/logo allowed)
"""

import html
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.tiers import Feature, SubscriptionTier, get_tier_limit, has_feature
from app.models.embed_widgets import (
    BrandingLevel,
    EmbedDomainWhitelist,
    EmbedWidget,
    WidgetSize,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.schemas.embed_widgets import WidgetCreate, WidgetUpdate

# Preset widget dimensions in CSS pixels: size -> (width, height)
WIDGET_SIZE_PRESETS: dict[str, tuple[int, int]] = {
    WidgetSize.BADGE.value: (120, 40),
    WidgetSize.COMPACT.value: (200, 100),
    WidgetSize.STANDARD.value: (300, 200),
    WidgetSize.LARGE.value: (400, 300),
}

CUSTOM_BRANDING_FIELDS: tuple[str, ...] = (
    "custom_logo_url",
    "custom_accent_color",
    "custom_background_color",
    "custom_text_color",
)


def _enum_value(value: Any) -> Any:
    """Return ``.value`` for enum members, the raw value otherwise."""
    return getattr(value, "value", value)


def _model_to_dict(model: Any) -> dict[str, Any]:
    """Dump a Pydantic model (v2 ``model_dump`` or v1 ``dict``) without ``None`` values."""
    dump = getattr(model, "model_dump", None)
    if dump is None:
        dump = model.dict
    return dict(dump(exclude_none=True))


class EmbedWidgetService:
    """Service for managing embed widget configurations and the domain whitelist."""

    def __init__(self, db: Session):
        self.db = db

    # =========================================================================
    # Tier helpers
    # =========================================================================

    @staticmethod
    def branding_level_for_tier(tier: SubscriptionTier) -> BrandingLevel:
        """Map a subscription tier to the branding level applied to its widgets."""
        if has_feature(tier, Feature.EMBED_WIDGETS_WHITELABEL):
            return BrandingLevel.NONE
        if has_feature(tier, Feature.EMBED_WIDGETS_MINIMAL):
            return BrandingLevel.MINIMAL
        return BrandingLevel.FULL

    def _ensure_widget_quota(self, tenant_id: int, tier: SubscriptionTier) -> None:
        """Raise HTTP 400 when the tenant already has the maximum number of widgets."""
        max_widgets = get_tier_limit(tier, "max_embed_widgets")
        current = (
            self.db.query(EmbedWidget)
            .filter(EmbedWidget.tenant_id == tenant_id)
            .count()
        )
        if current >= max_widgets:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Maximum {max_widgets} embed widgets allowed for {tier.value} tier",
            )

    def _ensure_domain_quota(self, tenant_id: int, tier: SubscriptionTier) -> None:
        """Raise HTTP 400 when the tenant already has the maximum number of domains."""
        max_domains = get_tier_limit(tier, "max_embed_domains")
        current = (
            self.db.query(EmbedDomainWhitelist)
            .filter(
                EmbedDomainWhitelist.tenant_id == tenant_id,
                EmbedDomainWhitelist.is_active.is_(True),
            )
            .count()
        )
        if current >= max_domains:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Maximum {max_domains} whitelisted domains allowed for {tier.value} tier",
            )

    @staticmethod
    def _custom_branding_values(custom_branding: Any) -> dict[str, Optional[str]]:
        """Extract the non-null custom branding fields from a ``WidgetCustomBranding``."""
        if custom_branding is None:
            return {}
        values = _model_to_dict(custom_branding)
        return {key: values[key] for key in CUSTOM_BRANDING_FIELDS if key in values}

    def _apply_custom_branding(
        self,
        widget: EmbedWidget,
        custom_branding: Any,
        tier: SubscriptionTier,
    ) -> None:
        """Apply custom branding (Enterprise / white-label only)."""
        values = self._custom_branding_values(custom_branding)
        if not values:
            return
        if not has_feature(tier, Feature.EMBED_WIDGETS_WHITELABEL):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Custom branding is only available on the white-label (Enterprise) tier",
            )
        for key, value in values.items():
            setattr(widget, key, value)

    @staticmethod
    def _data_scope_dict(data_scope: Any) -> dict[str, Any]:
        """Serialize a ``WidgetDataScope`` (or ``None``) to a JSON-compatible dict."""
        if data_scope is None:
            return {}
        return _model_to_dict(data_scope)

    # =========================================================================
    # Widget CRUD
    # =========================================================================

    def create_widget(
        self,
        tenant_id: int,
        data: "WidgetCreate",
        tier: SubscriptionTier,
    ) -> EmbedWidget:
        """
        Create a widget for the tenant.

        Enforces the tier widget quota, sets the branding level from the tier
        and only accepts custom branding on the white-label tier.

        Raises:
            HTTPException: 400 when the quota is exceeded, 403 for disallowed branding
        """
        self._ensure_widget_quota(tenant_id, tier)

        widget = EmbedWidget(
            tenant_id=tenant_id,
            name=data.name,
            description=data.description,
            widget_type=_enum_value(data.widget_type),
            widget_size=_enum_value(data.widget_size),
            custom_width=data.custom_width,
            custom_height=data.custom_height,
            branding_level=self.branding_level_for_tier(tier).value,
            data_scope=self._data_scope_dict(data.data_scope),
            refresh_interval_seconds=data.refresh_interval_seconds,
        )
        self._apply_custom_branding(widget, data.custom_branding, tier)

        self.db.add(widget)
        self.db.commit()
        self.db.refresh(widget)
        return widget

    def list_widgets(
        self,
        tenant_id: int,
        widget_type: Optional[Any] = None,
        is_active: Optional[bool] = None,
    ) -> list[EmbedWidget]:
        """List the tenant's widgets, optionally filtered by type and active state."""
        query = self.db.query(EmbedWidget).filter(EmbedWidget.tenant_id == tenant_id)
        if widget_type is not None:
            query = query.filter(EmbedWidget.widget_type == _enum_value(widget_type))
        if is_active is not None:
            query = query.filter(EmbedWidget.is_active == is_active)
        return query.order_by(EmbedWidget.created_at.desc()).all()

    def get_widget(self, tenant_id: int, widget_id: UUID) -> Optional[EmbedWidget]:
        """Fetch a single widget belonging to the tenant (``None`` when not found)."""
        return (
            self.db.query(EmbedWidget)
            .filter(
                EmbedWidget.id == widget_id,
                EmbedWidget.tenant_id == tenant_id,
            )
            .first()
        )

    def update_widget(
        self,
        tenant_id: int,
        widget_id: UUID,
        data: "WidgetUpdate",
        tier: SubscriptionTier,
    ) -> EmbedWidget:
        """
        Update a widget's configuration.

        The branding level is re-derived from the current tier so a downgrade
        restores branding. Custom branding is only accepted on the white-label tier.

        Raises:
            HTTPException: 404 when the widget does not exist for the tenant
        """
        widget = self.get_widget(tenant_id, widget_id)
        if not widget:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")

        if data.name is not None:
            widget.name = data.name
        if data.description is not None:
            widget.description = data.description
        if data.widget_size is not None:
            widget.widget_size = _enum_value(data.widget_size)
        if data.custom_width is not None:
            widget.custom_width = data.custom_width
        if data.custom_height is not None:
            widget.custom_height = data.custom_height
        if data.data_scope is not None:
            widget.data_scope = self._data_scope_dict(data.data_scope)
        if data.refresh_interval_seconds is not None:
            widget.refresh_interval_seconds = data.refresh_interval_seconds
        if data.is_active is not None:
            widget.is_active = data.is_active

        if widget.widget_size == WidgetSize.CUSTOM.value and (
            widget.custom_width is None or widget.custom_height is None
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Custom dimensions required when widget_size is 'custom'",
            )

        widget.branding_level = self.branding_level_for_tier(tier).value
        self._apply_custom_branding(widget, data.custom_branding, tier)

        self.db.commit()
        self.db.refresh(widget)
        return widget

    def delete_widget(self, tenant_id: int, widget_id: UUID) -> None:
        """Delete a widget (tokens cascade via the ORM relationship)."""
        widget = self.get_widget(tenant_id, widget_id)
        if not widget:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")

        self.db.delete(widget)
        self.db.commit()

    # =========================================================================
    # Domain whitelist
    # =========================================================================

    def add_domain_to_whitelist(
        self,
        tenant_id: int,
        domain_pattern: str,
        description: Optional[str],
        tier: SubscriptionTier,
    ) -> EmbedDomainWhitelist:
        """
        Add (or re-activate) a whitelisted domain pattern for the tenant.

        Raises:
            HTTPException: 400 when the domain quota is exceeded or the pattern exists
        """
        pattern = domain_pattern.strip().lower()

        existing = (
            self.db.query(EmbedDomainWhitelist)
            .filter(
                EmbedDomainWhitelist.tenant_id == tenant_id,
                EmbedDomainWhitelist.domain_pattern == pattern,
            )
            .first()
        )
        if existing is not None:
            if existing.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Domain '{pattern}' is already whitelisted",
                )
            self._ensure_domain_quota(tenant_id, tier)
            existing.is_active = True
            if description is not None:
                existing.description = description
            self.db.commit()
            self.db.refresh(existing)
            return existing

        self._ensure_domain_quota(tenant_id, tier)

        domain = EmbedDomainWhitelist(
            tenant_id=tenant_id,
            domain_pattern=pattern,
            description=description,
        )
        self.db.add(domain)
        self.db.commit()
        self.db.refresh(domain)
        return domain

    def list_whitelisted_domains(self, tenant_id: int) -> list[EmbedDomainWhitelist]:
        """List the tenant's active whitelisted domains."""
        return (
            self.db.query(EmbedDomainWhitelist)
            .filter(
                EmbedDomainWhitelist.tenant_id == tenant_id,
                EmbedDomainWhitelist.is_active.is_(True),
            )
            .order_by(EmbedDomainWhitelist.created_at.desc())
            .all()
        )

    def remove_domain_from_whitelist(self, tenant_id: int, domain_id: UUID) -> None:
        """Remove a whitelisted domain."""
        domain = (
            self.db.query(EmbedDomainWhitelist)
            .filter(
                EmbedDomainWhitelist.id == domain_id,
                EmbedDomainWhitelist.tenant_id == tenant_id,
            )
            .first()
        )
        if not domain:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found")

        self.db.delete(domain)
        self.db.commit()

    # =========================================================================
    # Embed code
    # =========================================================================

    @staticmethod
    def widget_dimensions(widget: EmbedWidget) -> tuple[int, int]:
        """Resolve the rendered (width, height) for a widget from its size preset."""
        if widget.widget_size == WidgetSize.CUSTOM.value:
            fallback = WIDGET_SIZE_PRESETS[WidgetSize.STANDARD.value]
            return (
                int(widget.custom_width or fallback[0]),
                int(widget.custom_height or fallback[1]),
            )
        return WIDGET_SIZE_PRESETS.get(
            widget.widget_size, WIDGET_SIZE_PRESETS[WidgetSize.STANDARD.value]
        )

    def generate_embed_code(
        self,
        widget: EmbedWidget,
        token: str,
        base_url: str,
    ) -> dict[str, str]:
        """
        Build iframe and script embed snippets for a widget.

        Args:
            widget: Widget to embed
            token: Embed token (or a placeholder such as ``{YOUR_TOKEN}``)
            base_url: Public base URL of the Stratum app

        Returns:
            Dict with ``iframe_code``, ``script_code``, ``preview_url`` and
            ``documentation_url``.
        """
        base = base_url.rstrip("/")
        width, height = self.widget_dimensions(widget)
        widget_id = str(widget.id)
        safe_token = html.escape(token, quote=True)
        safe_title = html.escape(widget.name or "Stratum widget", quote=True)

        preview_url = f"{base}/embed/v1/widget/{widget_id}?token={safe_token}"
        iframe_code = (
            f'<iframe src="{preview_url}" width="{width}" height="{height}" '
            f'title="{safe_title}" frameborder="0" scrolling="no" loading="lazy" '
            'sandbox="allow-scripts allow-same-origin"></iframe>'
        )
        script_code = (
            f'<div data-stratum-widget="{widget_id}" data-stratum-token="{safe_token}" '
            f'data-stratum-width="{width}" data-stratum-height="{height}"></div>\n'
            f'<script async src="{base}/embed/v1/widget.js"></script>'
        )

        return {
            "iframe_code": iframe_code,
            "script_code": script_code,
            "preview_url": preview_url,
            "documentation_url": f"{base}/docs/embed-widgets",
        }
