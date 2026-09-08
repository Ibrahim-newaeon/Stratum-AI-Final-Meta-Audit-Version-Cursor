# =============================================================================
# Stratum AI - Facebook Login token verification tests
# =============================================================================
"""
Unit tests for ``app.services.meta.login_client``.

This module is the entire authentication of "Log in with Facebook": the browser
hands over a string, and these checks are the only thing standing between that
string and a Stratum session. So the tests are adversarial - every way a caller
could get an unverified, foreign or stale token accepted is asserted to fail,
and the read-only contract is asserted rather than assumed.

No network and no database: HTTP goes through ``httpx.MockTransport``.
"""

import hashlib
import hmac
import time

import httpx
import pytest

from app.services.meta.login_client import (
    FacebookLoginError,
    FacebookLoginNotConfigured,
    MetaLoginClient,
)

pytestmark = pytest.mark.unit

APP_ID = "1510739363341814"
APP_SECRET = "meta-app-secret-do-not-log"
OTHER_APP_ID = "9999999999999999"
USER_ID = "10215241773831025"
USER_TOKEN = "EAAG-short-lived-user-token"


def debug_payload(**overrides) -> dict:
    """Build a ``debug_token`` body that passes every check, then override it."""
    data = {
        "app_id": APP_ID,
        "type": "USER",
        "application": "Stratum Connect",
        "is_valid": True,
        "issued_at": int(time.time()) - 60,
        # 0 is Meta's "does not expire" sentinel; a real short-lived token
        # carries a future timestamp.
        "expires_at": int(time.time()) + 3600,
        "data_access_expires_at": int(time.time()) + 86400,
        "scopes": ["public_profile", "email"],
        "user_id": USER_ID,
    }
    data.update(overrides)
    return {"data": data}


def profile_payload(**overrides) -> dict:
    """Build a ``/me`` body for the same person."""
    body = {"id": USER_ID, "name": "Ibrahim Abd Rabo", "email": "person@example.com"}
    body.update(overrides)
    return body


def make_client(handler, **kwargs) -> MetaLoginClient:
    """Build a client whose HTTP goes to ``handler`` instead of the network."""
    return MetaLoginClient(
        app_id=APP_ID,
        app_secret=APP_SECRET,
        api_version="v23.0",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def routing_handler(
    debug_body=None, profile_body=None, debug_status=200, profile_status=200
):
    """Answer ``/debug_token`` and ``/me`` from the supplied bodies."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/debug_token"):
            return httpx.Response(debug_status, json=debug_body or debug_payload())
        if request.url.path.endswith("/me"):
            return httpx.Response(
                profile_status, json=profile_body or profile_payload()
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    return handler


# =============================================================================
# Configuration
# =============================================================================
class TestConfiguration:
    """Refusing to run without credentials is part of failing closed."""

    def test_missing_app_secret_refuses_to_build(self):
        with pytest.raises(FacebookLoginNotConfigured):
            MetaLoginClient(app_id=APP_ID, app_secret="")

    def test_missing_app_id_refuses_to_build(self):
        with pytest.raises(FacebookLoginNotConfigured):
            MetaLoginClient(app_id="   ", app_secret=APP_SECRET)


# =============================================================================
# Token verification
# =============================================================================
class TestVerifyAccessToken:
    """Every rejection path through ``debug_token``."""

    @pytest.mark.asyncio
    async def test_valid_token_is_accepted(self):
        client = make_client(routing_handler())
        info = await client.verify_access_token(USER_TOKEN)
        assert info.user_id == USER_ID
        assert info.app_id == APP_ID
        assert info.scopes == ("public_profile", "email")
        await client.aclose()

    @pytest.mark.asyncio
    async def test_empty_token_is_rejected_without_a_request(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request should be made for an empty token")

        client = make_client(handler)
        with pytest.raises(FacebookLoginError) as exc:
            await client.verify_access_token("   ")
        assert exc.value.reason == "missing_token"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_token_from_another_app_is_rejected(self):
        """The token-substitution attack: real token, wrong app."""
        client = make_client(
            routing_handler(debug_body=debug_payload(app_id=OTHER_APP_ID))
        )
        with pytest.raises(FacebookLoginError) as exc:
            await client.verify_access_token(USER_TOKEN)
        assert exc.value.reason == "wrong_app"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_app_id_compares_across_int_and_string(self):
        """Meta has returned ``app_id`` as a number; that must still match."""
        client = make_client(
            routing_handler(debug_body=debug_payload(app_id=int(APP_ID)))
        )
        info = await client.verify_access_token(USER_TOKEN)
        assert info.user_id == USER_ID
        await client.aclose()

    @pytest.mark.asyncio
    async def test_invalid_token_is_rejected(self):
        client = make_client(routing_handler(debug_body=debug_payload(is_valid=False)))
        with pytest.raises(FacebookLoginError) as exc:
            await client.verify_access_token(USER_TOKEN)
        assert exc.value.reason == "token_invalid"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_per_token_error_object_is_rejected_despite_http_200(self):
        body = debug_payload()
        body["data"]["error"] = {"code": 190, "message": "Session has expired"}
        client = make_client(routing_handler(debug_body=body))
        with pytest.raises(FacebookLoginError) as exc:
            await client.verify_access_token(USER_TOKEN)
        assert exc.value.reason == "token_invalid"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_page_token_is_rejected(self):
        client = make_client(routing_handler(debug_body=debug_payload(type="PAGE")))
        with pytest.raises(FacebookLoginError) as exc:
            await client.verify_access_token(USER_TOKEN)
        assert exc.value.reason == "not_a_user_token"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_expired_token_is_rejected(self):
        client = make_client(
            routing_handler(debug_body=debug_payload(expires_at=int(time.time()) - 1))
        )
        with pytest.raises(FacebookLoginError) as exc:
            await client.verify_access_token(USER_TOKEN)
        assert exc.value.reason == "token_expired"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_expired_data_access_is_rejected(self):
        client = make_client(
            routing_handler(
                debug_body=debug_payload(data_access_expires_at=int(time.time()) - 1)
            )
        )
        with pytest.raises(FacebookLoginError) as exc:
            await client.verify_access_token(USER_TOKEN)
        assert exc.value.reason == "token_expired"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_zero_expiry_means_never_expires_not_1970(self):
        client = make_client(
            routing_handler(
                debug_body=debug_payload(expires_at=0, data_access_expires_at=0)
            )
        )
        info = await client.verify_access_token(USER_TOKEN)
        assert info.expires_at == 0
        await client.aclose()

    @pytest.mark.asyncio
    async def test_missing_user_id_is_rejected(self):
        client = make_client(routing_handler(debug_body=debug_payload(user_id="")))
        with pytest.raises(FacebookLoginError) as exc:
            await client.verify_access_token(USER_TOKEN)
        assert exc.value.reason == "no_user_id"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_http_error_is_rejected(self):
        client = make_client(
            routing_handler(
                debug_status=400,
                debug_body={"error": {"code": 190, "message": "Invalid OAuth token"}},
            )
        )
        with pytest.raises(FacebookLoginError) as exc:
            await client.verify_access_token(USER_TOKEN)
        assert exc.value.reason == "token_inspection_failed"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_transport_failure_is_distinguishable_from_a_bad_token(self):
        """A retryable outage must not read as "this person is an impostor"."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        client = make_client(handler)
        with pytest.raises(FacebookLoginError) as exc:
            await client.verify_access_token(USER_TOKEN)
        assert exc.value.reason == "graph_unreachable"
        await client.aclose()


# =============================================================================
# Request shape
# =============================================================================
class TestRequestShape:
    """What actually goes over the wire."""

    @pytest.mark.asyncio
    async def test_only_get_is_ever_issued(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.method)
            return routing_handler()(request)

        client = make_client(handler)
        await client.verify_and_fetch_profile(USER_TOKEN)
        assert seen == ["GET", "GET"]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_app_secret_never_appears_in_a_url(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return routing_handler()(request)

        client = make_client(handler)
        await client.verify_and_fetch_profile(USER_TOKEN)
        for request in seen:
            assert APP_SECRET not in str(request.url)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_debug_token_is_authenticated_with_the_app_access_token(self):
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/debug_token"):
                seen["auth"] = request.headers.get("authorization", "")
                seen["input_token"] = request.url.params.get("input_token", "")
            return routing_handler()(request)

        client = make_client(handler)
        await client.verify_access_token(USER_TOKEN)
        assert seen["auth"] == f"Bearer {APP_ID}|{APP_SECRET}"
        assert seen["input_token"] == USER_TOKEN
        await client.aclose()

    @pytest.mark.asyncio
    async def test_profile_call_carries_a_correct_appsecret_proof(self):
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/me"):
                seen["proof"] = request.url.params.get("appsecret_proof", "")
                seen["fields"] = request.url.params.get("fields", "")
                seen["auth"] = request.headers.get("authorization", "")
            return routing_handler()(request)

        client = make_client(handler)
        await client.verify_and_fetch_profile(USER_TOKEN)

        expected = hmac.new(
            APP_SECRET.encode(), USER_TOKEN.encode(), hashlib.sha256
        ).hexdigest()
        assert seen["proof"] == expected
        assert seen["auth"] == f"Bearer {USER_TOKEN}"
        # Minimal profile: nothing beyond what provisioning an account needs.
        assert seen["fields"] == "id,name,email"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_api_version_is_in_the_path(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return routing_handler()(request)

        client = make_client(handler)
        await client.verify_and_fetch_profile(USER_TOKEN)
        assert all(path.startswith("/v23.0/") for path in seen)
        await client.aclose()


# =============================================================================
# Profile
# =============================================================================
class TestFetchProfile:
    """The second call, and the identity check that binds it to the first."""

    @pytest.mark.asyncio
    async def test_profile_is_returned_for_the_verified_identity(self):
        client = make_client(routing_handler())
        profile = await client.verify_and_fetch_profile(USER_TOKEN)
        assert profile.user_id == USER_ID
        assert profile.name == "Ibrahim Abd Rabo"
        assert profile.email == "person@example.com"
        assert profile.granted_scopes == ("public_profile", "email")
        await client.aclose()

    @pytest.mark.asyncio
    async def test_id_mismatch_between_the_two_calls_is_rejected(self):
        client = make_client(
            routing_handler(profile_body=profile_payload(id="777777777"))
        )
        with pytest.raises(FacebookLoginError) as exc:
            await client.verify_and_fetch_profile(USER_TOKEN)
        assert exc.value.reason == "identity_mismatch"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_missing_email_is_none_not_an_error(self):
        """Declining the email permission is normal, not a failure."""
        body = profile_payload()
        del body["email"]
        client = make_client(routing_handler(profile_body=body))
        profile = await client.verify_and_fetch_profile(USER_TOKEN)
        assert profile.email is None
        assert profile.user_id == USER_ID
        await client.aclose()

    @pytest.mark.asyncio
    async def test_email_is_lowercased(self):
        client = make_client(
            routing_handler(profile_body=profile_payload(email="Person@Example.COM"))
        )
        profile = await client.verify_and_fetch_profile(USER_TOKEN)
        assert profile.email == "person@example.com"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_blank_name_is_none(self):
        client = make_client(routing_handler(profile_body=profile_payload(name="   ")))
        profile = await client.verify_and_fetch_profile(USER_TOKEN)
        assert profile.name is None
        await client.aclose()


# =============================================================================
# Secret hygiene
# =============================================================================
class TestRedaction:
    """Nothing derived from a Graph response may carry a secret into a log."""

    def test_redact_removes_secret_and_token(self):
        client = MetaLoginClient(app_id=APP_ID, app_secret=APP_SECRET)
        text = f"secret={APP_SECRET} app={APP_ID}|{APP_SECRET} token={USER_TOKEN}"
        redacted = client._redact(text, USER_TOKEN)
        assert APP_SECRET not in redacted
        assert USER_TOKEN not in redacted

    def test_error_messages_are_fixed_strings(self):
        """No error message may be built from a Graph body."""
        error = FacebookLoginError("token_invalid", "Facebook rejected the sign-in")
        assert APP_SECRET not in str(error)
        assert str(error) == "Facebook rejected the sign-in"


# =============================================================================
# Facebook Login for Business code exchange
# =============================================================================
AUTH_CODE = "AQD-single-use-authorization-code"


def code_routing_handler(
    token_body=None,
    token_status=200,
    debug_body=None,
    profile_body=None,
    seen: list | None = None,
):
    """Answer ``/oauth/access_token`` as well as the two verification calls."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path.endswith("/oauth/access_token"):
            return httpx.Response(
                token_status, json=token_body or {"access_token": USER_TOKEN}
            )
        return routing_handler(debug_body=debug_body, profile_body=profile_body)(
            request
        )

    return handler


class TestExchangeCodeForToken:
    """The code flow is how the token arrives, never a reason to trust it more."""

    @pytest.mark.asyncio
    async def test_exchange_returns_the_access_token(self):
        client = make_client(code_routing_handler())
        assert await client.exchange_code_for_token(AUTH_CODE) == USER_TOKEN
        await client.aclose()

    @pytest.mark.asyncio
    async def test_exchange_sends_meta_the_parameters_it_requires(self):
        seen: list[httpx.Request] = []
        client = make_client(code_routing_handler(seen=seen))
        await client.exchange_code_for_token(AUTH_CODE)

        request = seen[0]
        assert request.method == "GET"
        assert request.url.params["client_id"] == APP_ID
        assert request.url.params["client_secret"] == APP_SECRET
        assert request.url.params["code"] == AUTH_CODE
        # Empty on purpose: a JS SDK code has no redirect, and Meta rejects the
        # exchange when one is supplied.
        assert request.url.params["redirect_uri"] == ""
        await client.aclose()

    @pytest.mark.asyncio
    async def test_empty_code_is_refused_before_any_request(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request should be made for an empty code")

        client = make_client(handler)
        with pytest.raises(FacebookLoginError) as exc:
            await client.exchange_code_for_token("   ")
        assert exc.value.reason == "missing_code"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_a_rejected_code_fails_closed(self):
        client = make_client(
            code_routing_handler(
                token_status=400, token_body={"error": {"message": "code expired"}}
            )
        )
        with pytest.raises(FacebookLoginError) as exc:
            await client.exchange_code_for_token(AUTH_CODE)
        assert exc.value.reason == "code_exchange_failed"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_a_200_without_a_token_fails_closed(self):
        client = make_client(code_routing_handler(token_body={"machine_id": "abc"}))
        with pytest.raises(FacebookLoginError) as exc:
            await client.exchange_code_for_token(AUTH_CODE)
        assert exc.value.reason == "code_exchange_failed"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_code_flow_still_runs_every_verification_check(self):
        """A foreign app id must fail even when the code exchange succeeded."""
        client = make_client(
            code_routing_handler(debug_body=debug_payload(app_id=OTHER_APP_ID))
        )
        with pytest.raises(FacebookLoginError) as exc:
            await client.verify_and_fetch_profile_from_code(AUTH_CODE)
        assert exc.value.reason == "wrong_app"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_code_flow_yields_the_same_verified_profile(self):
        client = make_client(code_routing_handler())
        profile = await client.verify_and_fetch_profile_from_code(AUTH_CODE)
        assert profile.user_id == USER_ID
        assert profile.email == "person@example.com"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_exchange_issues_only_get_requests(self):
        """The read-only contract holds for the new endpoint too."""
        seen: list[httpx.Request] = []
        client = make_client(code_routing_handler(seen=seen))
        await client.verify_and_fetch_profile_from_code(AUTH_CODE)
        assert [request.method for request in seen] == ["GET", "GET", "GET"]
        await client.aclose()
