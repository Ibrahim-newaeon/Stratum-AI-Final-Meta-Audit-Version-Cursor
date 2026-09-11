"""Email verification is required only when SMTP can send the message."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.auth.deps import get_current_verified_user
from app.core.config import settings

pytestmark = pytest.mark.unit


async def test_unverified_user_may_connect_when_smtp_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "smtp_user", None)
    monkeypatch.setattr(settings, "smtp_password", None)
    user = SimpleNamespace(is_verified=False)
    assert await get_current_verified_user(user) is user


async def test_placeholder_smtp_password_does_not_enforce_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "smtp_user", "info@stratumai.app")
    monkeypatch.setattr(settings, "smtp_password", "YOUR_SMTP_PASSWORD_HERE")
    user = SimpleNamespace(is_verified=False)
    assert await get_current_verified_user(user) is user


async def test_unverified_user_is_blocked_when_smtp_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "smtp_user", "mail@example.com")
    monkeypatch.setattr(settings, "smtp_password", "real-smtp-secret")
    user = SimpleNamespace(is_verified=False)
    with pytest.raises(HTTPException) as exc:
        await get_current_verified_user(user)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Email verification required"


async def test_verified_user_passes_when_smtp_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "smtp_user", "mail@example.com")
    monkeypatch.setattr(settings, "smtp_password", "real-smtp-secret")
    user = SimpleNamespace(is_verified=True)
    assert await get_current_verified_user(user) is user
