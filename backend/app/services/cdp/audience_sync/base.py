# =============================================================================
# Stratum AI - Audience Sync Base Types
# =============================================================================
"""
Base classes and data types for audience sync connectors.

Stratum AI syncs CDP segments only to Meta (Facebook, Instagram, WhatsApp)
Custom Audiences. Connectors implement the BaseAudienceConnector interface.
"""

import enum
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog


class IdentifierType(str, enum.Enum):
    """Types of user identifiers used for audience matching."""

    EMAIL = "email"
    PHONE = "phone"
    MOBILE_ADVERTISER_ID = "mobile_advertiser_id"
    EXTERNAL_ID = "external_id"


@dataclass
class UserIdentifier:
    """A single (optionally pre-hashed) user identifier."""

    identifier_type: IdentifierType
    hashed_value: Optional[str] = None
    raw_value: Optional[str] = None

    def get_hashed(self) -> Optional[str]:
        """Return the SHA256 hash of the identifier (hashing raw value if needed)."""
        if self.hashed_value:
            return self.hashed_value
        if self.raw_value:
            normalized = self.raw_value.strip().lower()
            return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return None


@dataclass
class AudienceUser:
    """A user (CDP profile) to include in a platform audience."""

    profile_id: str
    identifiers: list[UserIdentifier] = field(default_factory=list)


@dataclass
class AudienceConfig:
    """Configuration for creating a platform audience."""

    name: str
    description: Optional[str] = None
    customer_file_source: Optional[str] = None


@dataclass
class AudienceSyncResult:
    """Result of an audience sync operation against a platform."""

    success: bool
    operation: str = "update"
    platform_audience_id: Optional[str] = None
    platform_audience_name: Optional[str] = None

    # Counts
    users_sent: int = 0
    users_added: int = 0
    users_removed: int = 0
    users_failed: int = 0

    # Size / matching (as reported by the platform, when available)
    audience_size: Optional[int] = None
    matched_size: Optional[int] = None
    match_rate: Optional[float] = None

    # Timing
    duration_ms: Optional[int] = None

    # Errors / raw response
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    error_details: Optional[dict[str, Any]] = None
    platform_response: Optional[dict[str, Any]] = None


class BaseAudienceConnector(ABC):
    """Abstract base class for platform audience connectors."""

    PLATFORM_NAME = "base"
    BATCH_SIZE = 10000

    def __init__(self, access_token: str, ad_account_id: str, **kwargs: Any) -> None:
        self.access_token = access_token
        self.ad_account_id = ad_account_id
        self.logger = structlog.get_logger().bind(
            connector=self.PLATFORM_NAME, ad_account_id=ad_account_id
        )

    @abstractmethod
    async def create_audience(
        self, config: AudienceConfig, users: list[AudienceUser]
    ) -> AudienceSyncResult:
        """Create a new platform audience and add the given users."""

    @abstractmethod
    async def add_users(
        self, audience_id: str, users: list[AudienceUser]
    ) -> AudienceSyncResult:
        """Add users to an existing platform audience."""

    @abstractmethod
    async def remove_users(
        self, audience_id: str, users: list[AudienceUser]
    ) -> AudienceSyncResult:
        """Remove users from an existing platform audience."""

    @abstractmethod
    async def replace_audience(
        self, audience_id: str, users: list[AudienceUser]
    ) -> AudienceSyncResult:
        """Replace the full membership of a platform audience."""

    @abstractmethod
    async def delete_audience(self, audience_id: str) -> AudienceSyncResult:
        """Delete a platform audience."""


__all__ = [
    "IdentifierType",
    "UserIdentifier",
    "AudienceUser",
    "AudienceConfig",
    "AudienceSyncResult",
    "BaseAudienceConnector",
]
