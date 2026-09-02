# =============================================================================
# Stratum AI - MFA (TOTP) Service
# =============================================================================
"""
TOTP-based two-factor authentication service.

Stores an encrypted TOTP secret and hashed backup codes on the User record.
Implements verification with failed-attempt lockout (5 attempts / 15 min).
"""

from __future__ import annotations

import base64
import hashlib
import io
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.base_models import User
from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import decrypt_pii, encrypt_pii

logger = get_logger(__name__)

try:
    import pyotp
except ImportError:  # pragma: no cover
    pyotp = None  # type: ignore[assignment]

__all__ = ["MFAService", "check_mfa_required", "is_user_locked"]

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
BACKUP_CODE_COUNT = 10


@dataclass
class MFAStatus:
    """MFA status for a user."""

    enabled: bool
    verified_at: Optional[datetime]
    backup_codes_remaining: int
    is_locked: bool
    lockout_until: Optional[datetime]


@dataclass
class MFASetupData:
    """Data returned when initiating MFA setup."""

    secret: str
    provisioning_uri: str
    qr_code_base64: str


def _hash_code(code: str) -> str:
    """Hash a backup code for storage."""
    return hashlib.sha256(code.replace("-", "").upper().encode()).hexdigest()


def _generate_backup_codes() -> list[str]:
    """Generate human-friendly backup codes (XXXX-XXXX)."""
    codes = []
    for _ in range(BACKUP_CODE_COUNT):
        raw = secrets.token_hex(4).upper()
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes


class MFAService:
    """Manages TOTP setup, verification, and backup codes for users."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _get_user(self, user_id: int) -> User:
        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise ValueError(f"User {user_id} not found")
        return user

    # -------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------

    async def get_mfa_status(self, user_id: int) -> MFAStatus:
        """Get MFA status for a user."""
        user = await self._get_user(user_id)
        backup = (user.backup_codes or {}).get("codes", [])
        locked, lockout_until = _lockout_state(user)
        return MFAStatus(
            enabled=bool(user.totp_enabled),
            verified_at=user.totp_verified_at,
            backup_codes_remaining=len(backup),
            is_locked=locked,
            lockout_until=lockout_until,
        )

    # -------------------------------------------------------------------
    # Setup / enable / disable
    # -------------------------------------------------------------------

    async def initiate_setup(self, user_id: int, email: str) -> MFASetupData:
        """Generate a TOTP secret and provisioning QR code for setup."""
        if pyotp is None:
            raise ValueError("MFA is not available: pyotp is not installed")

        user = await self._get_user(user_id)
        secret = pyotp.random_base32()

        user.totp_secret = encrypt_pii(secret)
        user.totp_enabled = False
        user.totp_verified_at = None
        await self.db.flush()

        issuer = getattr(settings, "app_name", "Stratum AI")
        provisioning_uri = pyotp.totp.TOTP(secret).provisioning_uri(
            name=email, issuer_name=issuer
        )

        return MFASetupData(
            secret=secret,
            provisioning_uri=provisioning_uri,
            qr_code_base64=_qr_code_base64(provisioning_uri),
        )

    async def verify_and_enable(self, user_id: int, code: str) -> tuple[bool, list[str]]:
        """Verify a TOTP code and enable MFA; returns (success, backup_codes)."""
        user = await self._get_user(user_id)
        if not user.totp_secret:
            raise ValueError("MFA setup has not been initiated")

        if not self._verify_totp(user, code):
            return False, []

        backup_codes = _generate_backup_codes()
        user.totp_enabled = True
        user.totp_verified_at = datetime.now(UTC)
        user.backup_codes = {"codes": [_hash_code(c) for c in backup_codes]}
        user.failed_totp_attempts = 0
        user.totp_lockout_until = None
        await self.db.commit()

        return True, backup_codes

    async def disable(self, user_id: int, code: str) -> bool:
        """Disable MFA after verifying a TOTP or backup code."""
        user = await self._get_user(user_id)
        if not user.totp_enabled:
            raise ValueError("MFA is not enabled")

        valid = self._verify_totp(user, code) or self._consume_backup_code(user, code)
        if not valid:
            return False

        user.totp_secret = None
        user.totp_enabled = False
        user.totp_verified_at = None
        user.backup_codes = None
        user.failed_totp_attempts = 0
        user.totp_lockout_until = None
        await self.db.commit()
        return True

    async def regenerate_backup_codes(
        self, user_id: int, code: str
    ) -> tuple[bool, list[str]]:
        """Regenerate backup codes after verifying a TOTP code (not a backup code)."""
        user = await self._get_user(user_id)
        if not user.totp_enabled:
            raise ValueError("MFA is not enabled")

        if not self._verify_totp(user, code):
            return False, []

        backup_codes = _generate_backup_codes()
        user.backup_codes = {"codes": [_hash_code(c) for c in backup_codes]}
        await self.db.commit()
        return True, backup_codes

    # -------------------------------------------------------------------
    # Login-time verification
    # -------------------------------------------------------------------

    async def verify_code(self, user_id: int, code: str) -> tuple[bool, str]:
        """
        Verify a TOTP or backup code during login.

        Returns (valid, message). Tracks failed attempts and applies lockout.
        """
        try:
            user = await self._get_user(user_id)
        except ValueError as e:
            return False, str(e)

        if not user.totp_enabled or not user.totp_secret:
            return False, "MFA is not enabled for this user"

        locked, _ = _lockout_state(user)
        if locked:
            return False, "Account is temporarily locked due to failed attempts"

        if self._verify_totp(user, code) or self._consume_backup_code(user, code):
            user.failed_totp_attempts = 0
            user.totp_lockout_until = None
            await self.db.commit()
            return True, "Code verified"

        user.failed_totp_attempts = (user.failed_totp_attempts or 0) + 1
        if user.failed_totp_attempts >= MAX_FAILED_ATTEMPTS:
            user.totp_lockout_until = datetime.now(UTC) + timedelta(minutes=LOCKOUT_MINUTES)
            logger.warning("mfa_lockout_applied", user_id=user_id)
        await self.db.commit()
        return False, "Invalid verification code"

    # -------------------------------------------------------------------
    # Internals
    # -------------------------------------------------------------------

    def _verify_totp(self, user: User, code: str) -> bool:
        """Check a code against the user's TOTP secret."""
        if pyotp is None or not user.totp_secret:
            return False
        try:
            secret = decrypt_pii(user.totp_secret)
            return bool(pyotp.TOTP(secret).verify(code.strip(), valid_window=1))
        except Exception as e:
            logger.warning("mfa_totp_verify_error", error=str(e))
            return False

    def _consume_backup_code(self, user: User, code: str) -> bool:
        """Check a backup code and remove it from the stored set if valid."""
        stored = (user.backup_codes or {}).get("codes", [])
        code_hash = _hash_code(code)
        if code_hash not in stored:
            return False
        user.backup_codes = {"codes": [c for c in stored if c != code_hash]}
        return True


def _lockout_state(user: User) -> tuple[bool, Optional[datetime]]:
    """Determine whether the user is currently locked out."""
    lockout_until = user.totp_lockout_until
    if lockout_until is None:
        return False, None
    if lockout_until.tzinfo is None:
        lockout_until = lockout_until.replace(tzinfo=UTC)
    if lockout_until > datetime.now(UTC):
        return True, lockout_until
    return False, None


def _qr_code_base64(provisioning_uri: str) -> str:
    """Render the provisioning URI as a base64-encoded PNG QR code."""
    try:
        import qrcode

        img = qrcode.make(provisioning_uri)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode()
    except Exception as e:  # pragma: no cover - qrcode optional
        logger.warning("mfa_qr_generation_failed", error=str(e))
        return ""


async def check_mfa_required(db: AsyncSession, user_id: int) -> bool:
    """Check whether MFA is enabled (and thus required) for a user."""
    result = await db.execute(select(User.totp_enabled).where(User.id == user_id))
    enabled = result.scalar_one_or_none()
    return bool(enabled)


async def is_user_locked(
    db: AsyncSession, user_id: int
) -> tuple[bool, Optional[datetime]]:
    """Check whether a user is locked out from MFA attempts."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return False, None
    return _lockout_state(user)
