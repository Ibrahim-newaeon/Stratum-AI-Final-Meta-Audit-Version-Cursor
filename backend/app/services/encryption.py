# =============================================================================
# Stratum AI - Token Encryption Service
# =============================================================================
"""
Symmetric encryption helpers for third-party OAuth/API tokens.

Wraps the Fernet-based PII encryption implemented in ``app.core.security``
so integration clients (e.g. CRM connectors) can encrypt credentials at rest.
"""

from typing import Optional

from app.core.security import decrypt_pii, encrypt_pii

__all__ = ["decrypt_token", "encrypt_token"]


def encrypt_token(token: str) -> str:
    """Encrypt a credential/token for storage at rest."""
    if token is None:
        raise ValueError("Cannot encrypt None token")
    return encrypt_pii(token)


def decrypt_token(encrypted_token: Optional[str]) -> str:
    """Decrypt a credential/token previously encrypted with encrypt_token."""
    if not encrypted_token:
        raise ValueError("Cannot decrypt empty token")
    return decrypt_pii(encrypted_token)
