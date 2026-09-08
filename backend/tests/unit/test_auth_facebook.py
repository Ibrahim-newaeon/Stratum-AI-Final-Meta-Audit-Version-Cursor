# =============================================================================
# Stratum AI - "Log in with Facebook" endpoint tests
# =============================================================================
"""
Unit tests for ``app.api.v1.endpoints.auth_facebook``.

No network and no database: the router is mounted on a minimal FastAPI app,
``get_async_session`` is overridden with an in-memory fake, and Graph
verification is stubbed - ``test_facebook_login_client`` already covers the real
verifier adversarially, so these tests are about what the endpoint *decides*
once an identity is established.

The decisions that matter, and are each asserted here:

- an unverifiable token never reaches account resolution at all;
- a matching email address does **not** silently adopt an existing password
  account (the account-takeover boundary);
- a provisioned account cannot be signed into with a password;
- MFA is not bypassed by signing in with Facebook;
- the authenticated link routes are not public.
"""

from types import SimpleNamespace
from typing import Any, Self
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import auth_facebook
from app.core.config import settings
from app.core.security import hash_pii_for_lookup, verify_password
from app.db.session import get_async_session
from app.middleware.tenant import is_public_endpoint
from app.models import SocialProvider, Tenant, User, UserSocialIdentity
from app.services.meta.login_client import FacebookLoginError, FacebookProfile

pytestmark = pytest.mark.unit

APP_ID = "1510739363341814"
APP_SECRET = "meta-app-secret-do-not-log"
ASID = "10215241773831025"
EMAIL = "person@example.com"
TENANT_ID = 42
USER_ID = 7
TOKEN = "EAAG-short-lived-user-token"

CONFIG_URL = "/api/v1/auth/facebook/config"
LOGIN_URL = "/api/v1/auth/facebook"
LINK_URL = "/api/v1/auth/facebook/link"


# =============================================================================
# Fakes
# =============================================================================
class _Result:
    """Minimal stand-in for a SQLAlchemy ``Result``."""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows or []

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> "_Result":
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return list(self._rows)


class FakeSession:
    """
    In-memory ``AsyncSession`` stand-in that answers by queried entity.

    Every ``select()`` in the endpoint targets exactly one of
    ``UserSocialIdentity``, ``User`` or ``Tenant``, so dispatching on the entity
    is enough to drive all the branches without a database.
    """

    def __init__(
        self,
        identities: list[Any] | None = None,
        users: list[Any] | None = None,
        tenants: list[Any] | None = None,
    ) -> None:
        self.identities = identities or []
        self.users = users or []
        self.tenants = tenants or []
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.commits = 0

    async def execute(self, statement: Any) -> _Result:
        entity = statement.column_descriptions[0]["entity"]
        if entity is UserSocialIdentity:
            return _Result(self.identities)
        if entity is User:
            return _Result(self.users)
        if entity is Tenant:
            return _Result(self.tenants)
        raise AssertionError(f"unexpected entity {entity}")

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    async def flush(self) -> None:
        # Stand in for the identity the database would assign.
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = USER_ID if isinstance(obj, User) else TENANT_ID

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False


def make_user(
    *,
    user_id: int = USER_ID,
    email: str = EMAIL,
    has_usable_password: bool = True,
    is_active: bool = True,
) -> User:
    """A persisted-looking ``User`` with the fields the endpoint reads."""
    user = User(
        tenant_id=TENANT_ID,
        email=_encrypt(email),
        email_hash=hash_pii_for_lookup(email),
        password_hash="$2b$12$" + "x" * 53,
        has_usable_password=has_usable_password,
        role=__import__("app.models", fromlist=["UserRole"]).UserRole.ADMIN,
        is_active=is_active,
        is_verified=True,
    )
    user.id = user_id
    return user


def _encrypt(value: str) -> str:
    """Encrypt PII the same way the endpoints do."""
    from app.core.security import encrypt_pii

    return encrypt_pii(value)


def make_identity(user_id: int = USER_ID) -> UserSocialIdentity:
    """A stored Facebook link for :data:`ASID`."""
    identity = UserSocialIdentity(
        user_id=user_id,
        tenant_id=TENANT_ID,
        provider=SocialProvider.FACEBOOK,
        provider_user_id=ASID,
        granted_scopes=["public_profile", "email"],
    )
    identity.id = 1
    return identity


def build_app(session: FakeSession) -> FastAPI:
    """Mount the router with the fake session bound in."""
    app = FastAPI()
    app.include_router(auth_facebook.router, prefix="/api/v1/auth")

    async def _session_override():
        yield session

    app.dependency_overrides[get_async_session] = _session_override
    return app


def profile(email: str | None = EMAIL) -> FacebookProfile:
    """A verified Facebook identity for :data:`ASID`."""
    return FacebookProfile(
        user_id=ASID,
        name="Ibrahim Abd Rabo",
        email=email,
        granted_scopes=("public_profile", "email"),
    )


def patch_verification(result: Any):
    """Replace Graph verification with a fixed profile or exception."""

    async def _verified(_token: str) -> FacebookProfile:
        if isinstance(result, Exception):
            raise result
        return result

    return patch.object(auth_facebook, "_verified_profile", _verified)


def enabled_settings(**overrides):
    """Turn Facebook Login on for the duration of a test."""
    values = {
        "facebook_login_enabled": True,
        "meta_app_id": APP_ID,
        "meta_app_secret": APP_SECRET,
        "facebook_login_allow_signup": True,
        "facebook_login_auto_link_by_email": False,
    }
    values.update(overrides)
    return patch.multiple(settings, **values)


def no_mfa():
    """No account in these tests has a second factor unless it says so."""

    async def _no(_db, _user_id):
        return False

    return patch.object(auth_facebook, "check_mfa_required", _no)


# =============================================================================
# Route exposure
# =============================================================================
class TestRouteExposure:
    """Which of these routes may be reached without a session."""

    def test_signin_routes_are_public(self):
        assert is_public_endpoint(LOGIN_URL)
        assert is_public_endpoint(CONFIG_URL)

    def test_link_management_is_not_public(self):
        """Linking changes an existing account, so it needs that account."""
        assert not is_public_endpoint(LINK_URL)


# =============================================================================
# Config endpoint
# =============================================================================
class TestConfigEndpoint:
    """What the logged-out page is told."""

    def test_disabled_when_the_feature_is_off(self):
        session = FakeSession()
        with patch.multiple(settings, facebook_login_enabled=False):
            response = TestClient(build_app(session)).get(CONFIG_URL)
        assert response.status_code == 200
        assert response.json()["data"] == {
            "enabled": False,
            "app_id": None,
            "api_version": None,
            "config_id": None,
            "scopes": [],
        }

    def test_disabled_when_credentials_are_missing(self):
        """Enabled without an app secret is a misconfiguration, not a feature."""
        session = FakeSession()
        with patch.multiple(
            settings,
            facebook_login_enabled=True,
            meta_app_id=APP_ID,
            meta_app_secret=None,
        ):
            response = TestClient(build_app(session)).get(CONFIG_URL)
        assert response.json()["data"]["enabled"] is False

    def test_enabled_reports_public_values_only(self):
        session = FakeSession()
        with enabled_settings():
            response = TestClient(build_app(session)).get(CONFIG_URL)
        data = response.json()["data"]
        assert data["enabled"] is True
        assert data["app_id"] == APP_ID
        assert data["scopes"] == ["public_profile", "email"]
        # The one thing that must never ship to a browser.
        assert APP_SECRET not in response.text


# =============================================================================
# Sign-in
# =============================================================================
class TestFacebookSignIn:
    """Account resolution, in the order the module documents."""

    def test_unverifiable_token_is_rejected(self):
        session = FakeSession()
        error = FacebookLoginError("wrong_app", "Facebook rejected the sign-in")

        async def _raise(_token: str):
            raise auth_facebook.HTTPException(status_code=401, detail=str(error))

        with enabled_settings(), patch.object(
            auth_facebook, "_verified_profile", _raise
        ):
            response = TestClient(build_app(session)).post(
                LOGIN_URL, json={"access_token": TOKEN}
            )
        assert response.status_code == 401
        # Nothing was looked up, created or written.
        assert session.added == []
        assert session.commits == 0

    def test_known_identity_signs_in(self):
        user = make_user()
        session = FakeSession(identities=[make_identity()], users=[user])
        with enabled_settings(), patch_verification(profile()), no_mfa():
            response = TestClient(build_app(session)).post(
                LOGIN_URL, json={"access_token": TOKEN}
            )
        data = response.json()["data"]
        assert response.status_code == 200
        assert data["mfa_required"] is False
        assert data["access_token"]
        assert data["refresh_token"]

    def test_inactive_account_is_refused_not_reprovisioned(self):
        """A deactivated account must not quietly get a second workspace."""
        session = FakeSession(identities=[make_identity()], users=[])
        with enabled_settings(), patch_verification(profile()), no_mfa():
            response = TestClient(build_app(session)).post(
                LOGIN_URL, json={"access_token": TOKEN}
            )
        assert response.status_code == 401
        assert session.added == []

    def test_matching_email_does_not_take_over_a_password_account(self):
        """The account-takeover boundary, on by default."""
        session = FakeSession(identities=[], users=[make_user()])
        with enabled_settings(), patch_verification(profile()), no_mfa():
            response = TestClient(build_app(session)).post(
                LOGIN_URL, json={"access_token": TOKEN}
            )
        assert response.status_code == 409
        assert "account settings" in response.json()["detail"]
        # No link was created, so a second attempt is refused identically.
        assert session.added == []

    def test_matching_email_links_when_the_operator_opted_in(self):
        session = FakeSession(identities=[], users=[make_user()])
        with (
            enabled_settings(facebook_login_auto_link_by_email=True),
            patch_verification(profile()),
            no_mfa(),
        ):
            response = TestClient(build_app(session)).post(
                LOGIN_URL, json={"access_token": TOKEN}
            )
        assert response.status_code == 200
        identities = [o for o in session.added if isinstance(o, UserSocialIdentity)]
        assert len(identities) == 1
        assert identities[0].provider_user_id == ASID
        assert identities[0].user_id == USER_ID

    def test_unknown_identity_provisions_a_workspace(self):
        session = FakeSession(identities=[], users=[], tenants=[])
        with enabled_settings(), patch_verification(profile()), no_mfa():
            response = TestClient(build_app(session)).post(
                LOGIN_URL, json={"access_token": TOKEN}
            )
        assert response.status_code == 200

        tenants = [o for o in session.added if isinstance(o, Tenant)]
        users = [o for o in session.added if isinstance(o, User)]
        assert len(tenants) == 1
        assert len(users) == 1
        assert users[0].is_verified is True  # Meta supplied a confirmed email

    def test_a_provisioned_account_has_no_usable_password(self):
        """Its password hash must be real bcrypt, and match nothing."""
        session = FakeSession(identities=[], users=[], tenants=[])
        with enabled_settings(), patch_verification(profile()), no_mfa():
            TestClient(build_app(session)).post(LOGIN_URL, json={"access_token": TOKEN})

        user = next(o for o in session.added if isinstance(o, User))
        assert user.has_usable_password is False
        # A real hash, so POST /auth/login answers 401 rather than raising.
        assert user.password_hash.startswith("$2")
        for guess in ("", "password", "facebook", ASID, EMAIL):
            assert verify_password(guess, user.password_hash) is False

    def test_provisioning_without_an_email_leaves_the_account_unverified(self):
        """Declining the email permission must not fake a verified address."""
        session = FakeSession(identities=[], users=[], tenants=[])
        with enabled_settings(), patch_verification(profile(email=None)), no_mfa():
            response = TestClient(build_app(session)).post(
                LOGIN_URL, json={"access_token": TOKEN}
            )
        assert response.status_code == 200
        user = next(o for o in session.added if isinstance(o, User))
        assert user.is_verified is False
        # The placeholder hash is derived from the ASID, so it can never
        # collide with a real address or be typed into a login form.
        assert user.email_hash == hash_pii_for_lookup(f"facebook:{ASID}")

    def test_signup_can_be_disabled(self):
        session = FakeSession(identities=[], users=[], tenants=[])
        with (
            enabled_settings(facebook_login_allow_signup=False),
            patch_verification(profile()),
            no_mfa(),
        ):
            response = TestClient(build_app(session)).post(
                LOGIN_URL, json={"access_token": TOKEN}
            )
        assert response.status_code == 403
        assert not [o for o in session.added if isinstance(o, Tenant)]

    def test_feature_disabled_answers_503(self):
        session = FakeSession()
        with patch.multiple(settings, facebook_login_enabled=False):
            response = TestClient(build_app(session)).post(
                LOGIN_URL, json={"access_token": TOKEN}
            )
        assert response.status_code == 503

    def test_short_token_is_rejected_by_validation(self):
        session = FakeSession()
        with enabled_settings():
            response = TestClient(build_app(session)).post(
                LOGIN_URL, json={"access_token": "x"}
            )
        assert response.status_code == 422


# =============================================================================
# MFA
# =============================================================================
class TestMfaIsNotBypassed:
    """Facebook is a first factor; it does not replace the second."""

    def test_mfa_account_gets_a_challenge_and_no_tokens(self):
        user = make_user()
        session = FakeSession(identities=[make_identity()], users=[user])

        async def _mfa_required(_db, _user_id):
            return True

        async def _not_locked(_db, _user_id):
            return False, None

        stored: dict[str, Any] = {}

        class _Redis:
            async def setex(self, key, ttl, value):
                stored["key"] = key
                stored["value"] = value

            async def close(self):
                pass

        async def _redis():
            return _Redis()

        with (
            enabled_settings(),
            patch_verification(profile()),
            patch.object(auth_facebook, "check_mfa_required", _mfa_required),
            patch.object(auth_facebook, "is_user_locked", _not_locked),
            patch.object(auth_facebook, "get_redis_client", _redis),
        ):
            response = TestClient(build_app(session)).post(
                LOGIN_URL, json={"access_token": TOKEN}
            )

        data = response.json()["data"]
        assert data["mfa_required"] is True
        assert data["mfa_session_token"]
        assert data["access_token"] is None
        assert data["refresh_token"] is None
        # The session is stored under the prefix POST /auth/login/mfa reads,
        # so the existing second-factor endpoint completes this sign-in.
        assert stored["key"].startswith(auth_facebook.MFA_SESSION_PREFIX)
        assert stored["value"].startswith(f"{USER_ID}:{TENANT_ID}:")

    def test_locked_out_account_is_refused(self):
        from datetime import UTC, datetime, timedelta

        user = make_user()
        session = FakeSession(identities=[make_identity()], users=[user])

        async def _mfa_required(_db, _user_id):
            return True

        async def _locked(_db, _user_id):
            return True, datetime.now(UTC) + timedelta(minutes=10)

        with (
            enabled_settings(),
            patch_verification(profile()),
            patch.object(auth_facebook, "check_mfa_required", _mfa_required),
            patch.object(auth_facebook, "is_user_locked", _locked),
        ):
            response = TestClient(build_app(session)).post(
                LOGIN_URL, json={"access_token": TOKEN}
            )
        assert response.status_code == 429


# =============================================================================
# Helpers
# =============================================================================
class TestHelpers:
    """Small pieces whose behaviour the flows depend on."""

    def test_unusable_password_hash_is_unique_per_account(self):
        first = auth_facebook._unusable_password_hash()
        second = auth_facebook._unusable_password_hash()
        assert first != second
        assert first.startswith("$2")

    @pytest.mark.asyncio
    async def test_slug_is_made_unique_against_existing_tenants(self):
        taken = SimpleNamespace(slug="acme")
        session = FakeSession(tenants=[taken])

        # Every lookup answers "taken", so the counter has to advance; capped
        # by swapping in an empty result after the first two probes.
        calls = {"n": 0}

        async def execute(statement):
            calls["n"] += 1
            return _Result([taken] if calls["n"] < 3 else [])

        session.execute = execute  # type: ignore[method-assign]
        slug = await auth_facebook._unique_tenant_slug(session, "Acme Corp")
        assert slug == "acme-corp-2"

    @pytest.mark.asyncio
    async def test_slug_falls_back_when_the_name_has_no_alphanumerics(self):
        session = FakeSession(tenants=[])
        assert await auth_facebook._unique_tenant_slug(session, "!!!") == "workspace"
