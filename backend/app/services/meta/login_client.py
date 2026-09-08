# =============================================================================
# Stratum AI - Facebook Login token verification (read-only)
# =============================================================================
"""
Server-side verification of the access token the Facebook JS SDK hands the SPA.

READ-ONLY BY CONSTRUCTION: like ``insights_client``, the only HTTP verb this
module ever issues is GET. It reads two Graph endpoints and writes nothing to
Meta. The permissions involved are ``public_profile`` and ``email`` - it never
requests, needs or uses ``ads_read`` or ``ads_management``, and it is entirely
separate from the ad-account OAuth flow in ``app.services.oauth.meta``.

Why the browser's word is not enough
------------------------------------
``FB.login()`` gives the page an ``authResponse`` with an ``accessToken`` and a
``userID``. Neither may be trusted as an assertion of identity: they arrive over
a request the caller fully controls, so a caller can post **any** string as the
user id, and can post a *real* access token that was minted for a **different
Facebook app** where they are the developer. Meta's own guidance ("Confirming
Identity" in *Manually Build a Login Flow*) is that a token received this way
"needs to be verified... an API call to an inspection endpoint that will
indicate who the token was generated for and by which app", from a server,
because it needs the app secret.

So :func:`verify_access_token` calls::

    GET /{version}/debug_token?input_token=<the browser's token>

authenticated with the **app access token** (``<app id>|<app secret>``), and
refuses the sign-in unless every one of these holds:

- ``data.is_valid`` is true;
- ``data.app_id`` equals our own configured app id - this is the check that
  defeats the token-substitution attack above, and skipping it makes the whole
  flow worthless;
- ``data.type`` is ``USER`` - a Page or app token identifies no person;
- ``data.error`` is absent;
- ``data.expires_at``/``data.data_access_expires_at``, when non-zero, are in
  the future. (Zero means "does not expire", which is legitimate for long-lived
  tokens, so zero is not treated as 1970.)
- ``data.user_id`` is present.

Only then does :func:`fetch_profile` read the person's profile::

    GET /{version}/me?fields=id,name,email

and the id it returns is required to match the id ``debug_token`` reported. That
second comparison closes the gap where a token is swapped between the two calls.

The identifier this yields is the **app-scoped user id (ASID)**: stable for this
app, meaningless outside it, and the same id Meta's Deauthorize and Data
Deletion callbacks carry. It is what ``user_social_identity`` stores.

appsecret_proof
---------------
Every profile call carries ``appsecret_proof`` - HMAC-SHA256 of the access token
keyed with the app secret - so a leaked user token alone cannot be replayed
against this app from somewhere else. The app dashboard currently does not
*require* it; sending it anyway costs nothing, and means turning "Require app
secret" on later does not break this flow.

Secret and token safety
-----------------------
The app secret is never placed in a URL, a query string, a log line or an
exception message: the app access token goes in an ``Authorization: Bearer``
header, and :meth:`_redact` scrubs both the secret and the user token out of any
message built from a Graph response. ``FacebookLoginError`` messages are short,
fixed strings; the Graph error body is logged at debug level after redaction and
is never returned to the caller.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from typing import Any, Self

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "FacebookLoginError",
    "FacebookLoginNotConfigured",
    "FacebookProfile",
    "FacebookTokenInfo",
    "MetaLoginClient",
]

GRAPH_API_BASE_URL = "https://graph.facebook.com"

#: Profile fields read after a token verifies. Deliberately minimal: the id we
#: must have, plus the name and email used to provision or match an account.
#: No picture, birthday, friends, likes or anything else - Stratum has no use
#: for them and every extra field is data it would then have to protect.
PROFILE_FIELDS: tuple[str, ...] = ("id", "name", "email")

#: Token types that identify a *person*. A PAGE or APP token authenticates
#: something that cannot log in to Stratum.
USER_TOKEN_TYPE = "USER"

#: Default per-request timeout. Sign-in is interactive, so it is far shorter
#: than the batch insights pull's 30s: a person is waiting on this call.
DEFAULT_TIMEOUT_SECONDS = 10.0


class FacebookLoginError(Exception):
    """
    A Facebook sign-in could not be verified.

    Carries a short machine-readable ``reason`` for logging and metrics. The
    message is a fixed string and never contains the app secret, the user's
    access token or a Graph error body.
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


class FacebookLoginNotConfigured(FacebookLoginError):
    """Facebook Login is switched off, or the app id/secret is missing."""

    def __init__(self) -> None:
        super().__init__(
            "not_configured", "Facebook Login is not configured on this deployment"
        )


@dataclass(frozen=True)
class FacebookTokenInfo:
    """The subset of ``debug_token``'s payload this flow acts on."""

    user_id: str
    app_id: str
    is_valid: bool
    token_type: str
    expires_at: int = 0
    data_access_expires_at: int = 0
    scopes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class FacebookProfile:
    """A verified Facebook identity.

    Attributes:
        user_id: App-scoped user id (ASID) - confirmed by both Graph calls.
        name: Display name, or None when Meta withheld it.
        email: Verified email, or None. **Often None in practice** - the person
            may have declined the ``email`` permission, may have registered
            with a phone number only, or the app may still be on Standard
            Access for ``email``. Callers must handle None rather than assume.
        granted_scopes: Permissions ``debug_token`` reported as granted.
    """

    user_id: str
    name: str | None
    email: str | None
    granted_scopes: tuple[str, ...] = field(default_factory=tuple)


def _as_dict(value: Any) -> dict[str, Any]:
    """Return ``value`` when it is a mapping, otherwise an empty dict."""
    return value if isinstance(value, dict) else {}


def _to_int(value: Any) -> int:
    """Coerce a Graph numeric (often a string) to int, defaulting to 0."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class MetaLoginClient:
    """
    Read-only Graph client that turns a browser access token into an identity.

    Args:
        app_id: Meta app id (defaults to ``settings.meta_app_id``).
        app_secret: Meta app secret (defaults to ``settings.meta_app_secret``).
        api_version: Graph API version (defaults to
            ``settings.meta_graph_api_version``).
        timeout: Per-request timeout in seconds.
        transport: Optional ``httpx`` transport (tests use
            ``httpx.MockTransport``).

    Raises:
        FacebookLoginNotConfigured: When the app id or secret is missing.
    """

    def __init__(
        self,
        *,
        app_id: str | None = None,
        app_secret: str | None = None,
        api_version: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        resolved_app_id = (app_id if app_id is not None else settings.meta_app_id) or ""
        resolved_secret = (
            app_secret if app_secret is not None else settings.meta_app_secret
        ) or ""
        if not resolved_app_id.strip() or not resolved_secret.strip():
            raise FacebookLoginNotConfigured()

        self._app_id = resolved_app_id.strip()
        self._app_secret = resolved_secret.strip()
        self._api_version = api_version or settings.meta_graph_api_version
        self._timeout = timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS
        self._transport = transport
        self._http: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ setup

    @property
    def app_id(self) -> str:
        """The Meta app id this client verifies tokens against."""
        return self._app_id

    @property
    def base_url(self) -> str:
        """Versioned Graph API base URL."""
        return f"{GRAPH_API_BASE_URL}/{self._api_version}"

    @property
    def _app_access_token(self) -> str:
        """The app access token, ``<app id>|<app secret>``. Never logged."""
        return f"{self._app_id}|{self._app_secret}"

    def _get_http(self) -> httpx.AsyncClient:
        """Lazily build the underlying client, reused across both Graph calls."""
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Accept": "application/json"},
                timeout=self._timeout,
                transport=self._transport,
            )
        return self._http

    async def aclose(self) -> None:
        """Close the underlying HTTP client (safe when never opened)."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self) -> Self:
        """Enter an ``async with`` block."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close the HTTP client on leaving an ``async with`` block."""
        await self.aclose()

    # -------------------------------------------------------------- internals

    def _redact(self, text: str, *user_token: str) -> str:
        """
        Strip the app secret and any user token out of ``text``.

        Applied to everything derived from a Graph response before it reaches a
        log, because Meta echoes caller-supplied values back in error messages.
        """
        for secret in (self._app_secret, self._app_access_token, *user_token):
            if secret and secret in text:
                text = text.replace(secret, "***REDACTED***")
        return text

    def appsecret_proof(self, access_token: str) -> str:
        """
        HMAC-SHA256 of ``access_token`` keyed with the app secret, hex encoded.

        Args:
            access_token: The user access token the call will carry.

        Returns:
            The ``appsecret_proof`` value Meta expects alongside that token.
        """
        return hmac.new(
            self._app_secret.encode("utf-8"),
            access_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def _get(
        self,
        path: str,
        params: dict[str, str],
        *,
        bearer: str,
        user_token: str,
        reason: str,
    ) -> dict[str, Any]:
        """
        Issue one authenticated GET and return its JSON object.

        Args:
            path: Graph path relative to the versioned base URL.
            params: Query parameters (never the app secret).
            bearer: Token for the ``Authorization`` header.
            user_token: Token to scrub from any log line.
            reason: Machine-readable reason attached to a raised error.

        Returns:
            The decoded JSON object.

        Raises:
            FacebookLoginError: On a transport failure, a non-2xx response or a
                body that is not a JSON object.
        """
        client = self._get_http()
        try:
            response = await client.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {bearer}"},
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "facebook_login_graph_unreachable",
                path=path,
                error=self._redact(str(exc), user_token),
            )
            raise FacebookLoginError(
                "graph_unreachable", "Could not reach Facebook to verify the sign-in"
            ) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = None

        if response.status_code >= 400 or not isinstance(payload, dict):
            error = _as_dict(_as_dict(payload).get("error"))
            logger.warning(
                "facebook_login_graph_error",
                path=path,
                status_code=response.status_code,
                # Meta's own code/subcode - useful for triage, never secret.
                error_code=error.get("code"),
                error_subcode=error.get("error_subcode"),
                message=self._redact(str(error.get("message", "")), user_token),
            )
            raise FacebookLoginError(reason, "Facebook rejected the sign-in")

        return payload

    # ------------------------------------------------------------------- API

    async def verify_access_token(self, access_token: str) -> FacebookTokenInfo:
        """
        Inspect a browser-supplied access token and prove it belongs to this app.

        Args:
            access_token: The ``authResponse.accessToken`` the JS SDK produced.

        Returns:
            The verified token metadata, including the app-scoped user id.

        Raises:
            FacebookLoginError: When the token is empty, invalid, expired,
                issued to a different app, or not a user token.
        """
        token = (access_token or "").strip()
        if not token:
            raise FacebookLoginError(
                "missing_token", "No Facebook access token supplied"
            )

        payload = await self._get(
            "/debug_token",
            {"input_token": token},
            bearer=self._app_access_token,
            user_token=token,
            reason="token_inspection_failed",
        )
        data = _as_dict(payload.get("data"))

        # A per-token error object means Meta itself is telling us the token is
        # unusable, even when the HTTP status was 200.
        if data.get("error"):
            raise FacebookLoginError("token_invalid", "Facebook rejected the sign-in")

        if not bool(data.get("is_valid")):
            raise FacebookLoginError("token_invalid", "Facebook rejected the sign-in")

        # THE check. A valid token from someone else's app must not sign anyone
        # in here; str() on both sides because Meta has returned app_id as both
        # a number and a string over the years.
        if str(data.get("app_id", "")) != self._app_id:
            logger.warning(
                "facebook_login_wrong_app", reported_app_id=str(data.get("app_id", ""))
            )
            raise FacebookLoginError("wrong_app", "Facebook rejected the sign-in")

        token_type = str(data.get("type", "")).upper()
        if token_type != USER_TOKEN_TYPE:
            raise FacebookLoginError(
                "not_a_user_token", "Facebook rejected the sign-in"
            )

        user_id = str(data.get("user_id", "")).strip()
        if not user_id:
            raise FacebookLoginError("no_user_id", "Facebook rejected the sign-in")

        now = int(time.time())
        expires_at = _to_int(data.get("expires_at"))
        data_access_expires_at = _to_int(data.get("data_access_expires_at"))
        # 0 is Meta's "never expires" sentinel for long-lived tokens, so only a
        # positive timestamp in the past means expired.
        if 0 < expires_at <= now or 0 < data_access_expires_at <= now:
            raise FacebookLoginError(
                "token_expired", "The Facebook sign-in has expired"
            )

        scopes = tuple(
            str(scope) for scope in data.get("scopes", []) if isinstance(scope, str)
        )

        return FacebookTokenInfo(
            user_id=user_id,
            app_id=self._app_id,
            is_valid=True,
            token_type=token_type,
            expires_at=expires_at,
            data_access_expires_at=data_access_expires_at,
            scopes=scopes,
        )

    async def fetch_profile(
        self, access_token: str, token_info: FacebookTokenInfo
    ) -> FacebookProfile:
        """
        Read the signed-in person's minimal profile.

        Args:
            access_token: The same token that was just verified.
            token_info: The result of :meth:`verify_access_token`.

        Returns:
            The verified profile.

        Raises:
            FacebookLoginError: When Graph refuses the call, or the id it
                returns differs from the one ``debug_token`` reported.
        """
        token = (access_token or "").strip()
        payload = await self._get(
            "/me",
            {
                "fields": ",".join(PROFILE_FIELDS),
                "appsecret_proof": self.appsecret_proof(token),
            },
            bearer=token,
            user_token=token,
            reason="profile_fetch_failed",
        )

        profile_id = str(payload.get("id", "")).strip()
        # Belt and braces against a token swapped between the two calls: the
        # identity we act on must be the identity we verified.
        if not profile_id or profile_id != token_info.user_id:
            logger.warning("facebook_login_identity_mismatch")
            raise FacebookLoginError(
                "identity_mismatch", "Facebook rejected the sign-in"
            )

        name = payload.get("name")
        email = payload.get("email")

        return FacebookProfile(
            user_id=profile_id,
            name=str(name).strip() if isinstance(name, str) and name.strip() else None,
            email=(
                str(email).strip().lower()
                if isinstance(email, str) and email.strip()
                else None
            ),
            granted_scopes=token_info.scopes,
        )

    async def verify_and_fetch_profile(self, access_token: str) -> FacebookProfile:
        """
        Run both Graph calls and return the identity they agree on.

        Args:
            access_token: The ``authResponse.accessToken`` from the JS SDK.

        Returns:
            The verified profile.

        Raises:
            FacebookLoginError: When any check fails.
        """
        token_info = await self.verify_access_token(access_token)
        return await self.fetch_profile(access_token, token_info)
