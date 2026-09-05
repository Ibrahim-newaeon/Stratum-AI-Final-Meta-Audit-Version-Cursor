# =============================================================================
# Stratum AI - Meta App Review privacy callbacks
# =============================================================================
"""
The two callbacks Meta App Review requires before granting ``ads_read`` /
``ads_management``, plus the public status page the second one has to point at.

Endpoints (all three listed in ``TenantMiddleware.PUBLIC_ENDPOINTS``):

- ``POST /api/v1/meta/deauthorize`` - Deauthorize Callback. Meta calls it when
  a person removes the app from their Facebook settings, i.e. without ever
  touching Stratum. The connection is severed and its stored tokens are wiped.
- ``POST /api/v1/meta/data-deletion`` - Data Deletion Request Callback. Meta
  calls it when a person asks for their data to be deleted. It must answer with
  ``{"url": ..., "confirmation_code": ...}`` and nothing else.
- ``GET /api/v1/meta/data-deletion/status`` - the page ``url`` points at. It
  reports the status of one confirmation code in human-readable form.

Security
--------
These are Meta-to-server calls, so they carry no Authorization header and are
public by necessity. They are not unauthenticated: Meta signs each one with a
``signed_request`` - HMAC-SHA256 over the raw encoded payload, keyed with the
**app secret** - which ``app.services.meta.signed_request`` verifies in
constant time before a single row is touched. A request that fails
verification is rejected with 400 and changes nothing. The status endpoint is
authenticated by the confirmation code itself: 128 bits from ``secrets``, so it
is unguessable, and it discloses only the state of that one request.

The app secret is never logged, never stored on a row, and never appears in a
response or an exception message: ``SignedRequestError`` carries fixed
constants, and the failure paths below log the message only.

Retry behaviour
---------------
Meta retries any non-2xx. A Meta user that matches no connection is therefore
answered **200**, not 404 - an unknown user is not a failure, there is simply
nothing to sever, and retrying would never change that. Real failures (a
database error) still answer 5xx so Meta does retry.

Scope of the deletion
---------------------
The deletion callback erases what Stratum AI obtained **through Meta** for the
person Meta names: the platform connection(s) they authorised, the access
tokens stored against them, and the Meta user id that linked those connections
back to them. It deliberately does **not** anonymise the Stratum AI login that
authorised the connection. That account is an email/password account of the
tenant's own, usually an administrator's, it is not Meta-derived data, and
deactivating it from an unauthenticated third-party callback would lock a
paying tenant out of its own workspace with no undo and no notice. Account
erasure stays behind the authenticated ``POST /api/v1/gdpr/anonymize``, where
the person asking is the person whose account it is.
"""

import html
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import async_session_maker
from app.models import (
    AuditAction,
    AuditLog,
    DataDeletionStatus,
    MetaDataDeletionRequest,
)
from app.models.campaign_builder import (
    AdPlatform,
    ConnectionStatus,
    TenantPlatformConnection,
)
from app.services.meta.signed_request import SignedRequestError, parse_signed_request

logger = get_logger(__name__)
router = APIRouter(prefix="/meta", tags=["Meta App Review Callbacks"])

# Keep in sync with TenantMiddleware.PUBLIC_ENDPOINTS ("/api/v1/meta" + each).
DEAUTHORIZE_PATH = "/deauthorize"
DATA_DELETION_PATH = "/data-deletion"
DATA_DELETION_STATUS_PATH = "/data-deletion/status"

#: Form field Meta POSTs the signed request in.
SIGNED_REQUEST_FIELD = "signed_request"

#: Machine-readable causes recorded on the audit row when Meta severs a
#: connection, and the operator-facing sentence each one puts on
#: ``TenantPlatformConnection.last_error``. That field is surfaced to the
#: customer by the platform-connection status response, so it has to name the
#: cause that actually applied rather than a single hardcoded one.
REASON_DEAUTHORIZE = "meta_deauthorize_callback"
REASON_DATA_DELETION = "meta_data_deletion_callback"

SEVER_REASON_MESSAGES: dict[str, str] = {
    REASON_DEAUTHORIZE: "Disconnected by the Meta user (deauthorize callback)",
    REASON_DATA_DELETION: ("Disconnected by the Meta user (data deletion request)"),
}
DEFAULT_SEVER_MESSAGE = "Disconnected by the Meta user"

#: A repeat deletion request for the same Meta user inside this window reuses
#: the request already on file instead of filing another. Meta may redeliver,
#: and a person may click twice; neither should mint a new confirmation code or
#: re-run an erasure that has already happened.
DELETION_DEDUPE_WINDOW = timedelta(hours=24)

#: Hosts that are never a usable public origin for the URL handed to Meta.
_LOOPBACK_HOSTS: frozenset[str] = frozenset(
    {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
)

#: Identical response for every code we do not hold, so the endpoint can never
#: be used to discover which confirmation codes exist.
UNKNOWN_CODE_DETAIL = "No deletion request matches that confirmation code."


# =============================================================================
# Response models
# =============================================================================


class DeauthorizeAck(BaseModel):
    """Acknowledgement returned to Meta's deauthorize callback."""

    status: str = Field(
        ...,
        description=(
            "disconnected = at least one connection was severed, "
            "no_connection = the Meta user matched nothing we hold"
        ),
    )
    connections_cleared: int = Field(..., ge=0)


class DataDeletionAck(BaseModel):
    """
    The exact body Meta requires from the Data Deletion Request Callback.

    Meta specifies ``{ url: '<url>', confirmation_code: '<code>' }``. Both keys
    are mandatory and no others are permitted, so this model has exactly two
    fields and the endpoint returns it verbatim.
    """

    url: str
    confirmation_code: str


class DataDeletionStatusResponse(BaseModel):
    """
    Machine-readable form of the public status page.

    Carries nothing about the person: no Meta user id, no tenant, no counters -
    only the code the caller already had and what happened to it.
    """

    confirmation_code: str
    status: str
    requested_at: datetime
    completed_at: datetime | None = None
    message: str


# =============================================================================
# Request parsing
# =============================================================================


async def read_signed_request(request: Request) -> str:
    """
    Pull the ``signed_request`` field out of Meta's POST body.

    **Form-encoded only.** Meta documents
    ``application/x-www-form-urlencoded`` and Starlette's form parser also
    accepts the ``multipart/form-data`` some app configurations send, which
    covers every shape Meta produces.

    A JSON body is deliberately *not* accepted. ``AuditMiddleware`` json-parses
    the body of every POST and records it on a 2xx, so a JSON branch here would
    persist the verbatim ``signed_request`` - a credential - into the audit
    trail. A form body is never parsed by that middleware, so the field cannot
    reach it. (``signed_request`` is redacted there as well; both belong,
    because neither should be the only thing standing in the way.)

    Args:
        request: The incoming callback request

    Returns:
        The raw ``signed_request`` string

    Raises:
        HTTPException: 400 when the field is absent or not a string
    """
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001 - an unparsable body is simply a 400
        form = None

    if form is not None:
        value = form.get(SIGNED_REQUEST_FIELD)
        if isinstance(value, str) and value:
            return value

    logger.warning("meta_callback_missing_signed_request", path=request.url.path)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Missing {SIGNED_REQUEST_FIELD}",
    )


async def verified_meta_user_id(request: Request) -> str:
    """
    Verify the callback's ``signed_request`` and return the Meta user id in it.

    Every mutation below happens only after this returns, so an unsigned or
    wrongly signed call can never reach the database.

    Args:
        request: The incoming callback request

    Returns:
        Meta's app-scoped user id (ASID) as a string

    Raises:
        HTTPException: 503 when no app secret is configured, 400 when the
            signed request is missing, malformed, wrongly signed or carries no
            usable ``user_id``. No message contains secret material.
    """
    app_secret = settings.meta_app_secret
    if not app_secret:
        logger.warning("meta_callback_app_secret_missing", path=request.url.path)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Meta app secret is not configured",
        )

    signed_request = await read_signed_request(request)

    try:
        payload = parse_signed_request(signed_request, app_secret)
    except SignedRequestError as exc:
        # SignedRequestError messages are fixed constants; nothing derived from
        # the secret, the signature or the payload reaches this log line.
        logger.warning(
            "meta_callback_invalid_signed_request",
            path=request.url.path,
            reason=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signed_request",
        ) from exc

    raw_user_id = payload.get("user_id")
    if isinstance(raw_user_id, bool) or not isinstance(raw_user_id, (str, int)):
        raw_user_id = None
    meta_user_id = str(raw_user_id).strip() if raw_user_id is not None else ""
    if not meta_user_id:
        logger.warning(
            "meta_callback_signed_request_without_user", path=request.url.path
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="signed_request carries no user_id",
        )

    return meta_user_id


# =============================================================================
# Shared severing logic
# =============================================================================


async def load_connections_for_meta_user(
    db: AsyncSession, meta_user_id: str
) -> list[TenantPlatformConnection]:
    """
    Find every Meta connection authorised by one Meta user.

    The mapping is ``TenantPlatformConnection.platform_user_id``, written at
    OAuth time from ``GET /me``. One person can connect several tenants, so this
    returns a list. A connection made before that column existed carries NULL
    and will not match - see the migration notes.

    Args:
        db: Open async session
        meta_user_id: App-scoped user id from the verified signed request

    Returns:
        Matching connections, possibly empty
    """
    result = await db.execute(
        select(TenantPlatformConnection).where(
            TenantPlatformConnection.platform == AdPlatform.META.value,
            TenantPlatformConnection.platform_user_id == meta_user_id,
        )
    )
    return list(result.scalars().all())


def sever_connection(
    db: AsyncSession,
    connection: TenantPlatformConnection,
    *,
    reason: str,
    forget_platform_user_id: bool = False,
    audit_context: dict[str, Any] | None = None,
) -> None:
    """
    Mark one connection disconnected, wipe its stored tokens and audit it.

    Clearing the ciphertext matters as much as the status: a disconnected row
    that still holds an encrypted token has not honoured the person's decision.

    ``last_error`` is derived from ``reason``, because that column is returned
    to the customer by the platform-connection status response - telling
    someone who asked for their data to be deleted that they "deauthorised the
    app" would simply be wrong.

    Args:
        db: Open async session (not committed here)
        connection: The connection to sever
        reason: Machine-readable cause recorded on the audit row and mapped to
            the operator-facing sentence stored on ``last_error``
        forget_platform_user_id: Also drop the stored Meta user id. True for a
            deletion request, where that id is itself Meta-derived personal
            data; false for a deauthorize, which asks to disconnect, not to
            erase.
        audit_context: Extra non-personal fields merged into the audit row
    """
    connection.status = ConnectionStatus.DISCONNECTED.value
    connection.access_token_encrypted = None
    connection.refresh_token_encrypted = None
    connection.token_ref = None
    connection.token_expires_at = None
    connection.last_error = SEVER_REASON_MESSAGES.get(reason, DEFAULT_SEVER_MESSAGE)

    if forget_platform_user_id:
        connection.platform_user_id = None

    new_value: dict[str, Any] = {
        "status": ConnectionStatus.DISCONNECTED.value,
        "tokens_cleared": True,
        "platform_user_id_cleared": forget_platform_user_id,
        "reason": reason,
    }
    if audit_context:
        new_value.update(audit_context)

    db.add(
        AuditLog(
            tenant_id=connection.tenant_id,
            user_id=None,  # Meta acted, not a Stratum user
            action=AuditAction.UPDATE,
            resource_type="tenant_platform_connection",
            resource_id=str(connection.id),
            new_value=new_value,
        )
    )


# =============================================================================
# Deauthorize Callback
# =============================================================================


@router.post(DEAUTHORIZE_PATH, response_model=DeauthorizeAck)
async def meta_deauthorize(request: Request) -> DeauthorizeAck:
    """
    Handle Meta's Deauthorize Callback: the person removed the app.

    Verifies the ``signed_request``, then severs every Meta connection that the
    named Meta user authorised - status ``disconnected``, stored tokens wiped,
    one audit row per connection.

    A Meta user that matches nothing answers 200 with ``no_connection``: it is
    the normal case for anyone who connected before the app stored the Meta user
    id, and Meta would otherwise retry a call that can never succeed.

    Args:
        request: The callback request carrying ``signed_request``

    Returns:
        What was severed

    Raises:
        HTTPException: 503 without an app secret, 400 for a bad signed request,
            500 (so Meta retries) when the database write fails
    """
    meta_user_id = await verified_meta_user_id(request)

    async with async_session_maker() as db:
        try:
            connections = await load_connections_for_meta_user(db, meta_user_id)

            for connection in connections:
                sever_connection(db, connection, reason=REASON_DEAUTHORIZE)

            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.exception("meta_deauthorize_failed", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Deauthorize handler failed; Meta will retry",
            ) from exc

    if not connections:
        # Not an error: nothing is held for this person, so nothing to sever.
        logger.info("meta_deauthorize_no_connection")
        return DeauthorizeAck(status="no_connection", connections_cleared=0)

    logger.info("meta_deauthorize_completed", connections_cleared=len(connections))
    return DeauthorizeAck(status="disconnected", connections_cleared=len(connections))


# =============================================================================
# Data Deletion Request Callback
# =============================================================================


def _is_usable_public_origin(base: str) -> bool:
    """
    Report whether a configured origin can be handed to an outside caller.

    ``settings.oauth_redirect_base_url`` defaults to ``http://localhost:8000``,
    so "unset" and "set to something useless" look the same from here. A
    loopback origin in the URL returned to Meta would send the person - and the
    App Review reviewer - to a dead link, so it counts as absent.

    Args:
        base: The configured origin, already stripped

    Returns:
        True when the origin has a host that resolves for someone else
    """
    if not base:
        return False
    try:
        parsed = urlparse(base)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if not host or not parsed.scheme:
        return False
    return host not in _LOOPBACK_HOSTS and not host.endswith(".localhost")


def build_status_url(request: Request, confirmation_code: str) -> str:
    """
    Build the status URL handed back to Meta for one confirmation code.

    Prefers ``settings.oauth_redirect_base_url`` - the public origin of this
    API, which the operator also sets for Meta's OAuth redirect. When that is
    unset or still points at loopback, the origin of the request Meta just made
    is used instead (the same fallback ``measurement.py`` uses), because a
    request that actually arrived from Meta proves the host Meta can reach.
    Returning a ``http://localhost:8000`` link would be worse than either.

    Args:
        request: The callback request, used for the origin fallback
        confirmation_code: The code the request was filed under

    Returns:
        Absolute URL of the public status page
    """
    base = (settings.oauth_redirect_base_url or "").strip().rstrip("/")
    if not _is_usable_public_origin(base):
        base = f"{request.url.scheme}://{request.url.netloc}"
    path = f"{settings.api_v1_prefix}{router.prefix}{DATA_DELETION_STATUS_PATH}"
    return f"{base}{path}?code={confirmation_code}"


async def erase_meta_user(
    db: AsyncSession, meta_user_id: str, confirmation_code: str
) -> list[TenantPlatformConnection]:
    """
    Erase what Meta gave us about one Meta user.

    That is exactly three things, all of them on the connection row: the
    authorisation itself (set to ``disconnected``), the access tokens stored
    against it, and the app-scoped Meta user id that linked it back to the
    person. Each severance is audited under the confirmation code.

    What this does **not** do is anonymise the Stratum AI account that
    authorised the connection. See the module docstring: that account is not
    Meta-derived data, disabling it has no undo, and the person clicking in
    Facebook is not told their colleague's - or their own - workspace login
    would be deactivated. ``POST /api/v1/gdpr/anonymize`` remains the path for
    erasing an account, and it authenticates the person first.

    Nothing is committed here; the caller owns the transaction.

    Args:
        db: Open async session
        meta_user_id: App-scoped Meta user id
        confirmation_code: Recorded on each audit row for traceability

    Returns:
        The connections that were severed
    """
    connections = await load_connections_for_meta_user(db, meta_user_id)

    for connection in connections:
        sever_connection(
            db,
            connection,
            reason=REASON_DATA_DELETION,
            forget_platform_user_id=True,
            audit_context={"confirmation_code": confirmation_code},
        )

    return connections


async def find_recent_request(
    db: AsyncSession, meta_user_id: str, now: datetime
) -> MetaDataDeletionRequest | None:
    """
    Return the deletion request already on file for this Meta user, if any.

    Makes the callback idempotent. A ``signed_request`` is not a Meta-only
    secret - anyone who authorised the app can obtain one for their own ASID -
    so without this a caller could file an unbounded number of rows and re-run
    the erasure on every one. Meta's own redeliveries land here too, and get
    back the code they were already given rather than a second one.

    Failed requests are excluded on purpose: a retry after a failure should
    genuinely be retried, not answered with the code that did not work.

    Args:
        db: Open async session
        meta_user_id: App-scoped Meta user id from the verified request
        now: Current time, the window is measured back from here

    Returns:
        The most recent non-failed request inside
        :data:`DELETION_DEDUPE_WINDOW`, or None
    """
    result = await db.execute(
        select(MetaDataDeletionRequest)
        .where(
            MetaDataDeletionRequest.meta_user_id == meta_user_id,
            MetaDataDeletionRequest.status != DataDeletionStatus.FAILED.value,
            MetaDataDeletionRequest.requested_at >= now - DELETION_DEDUPE_WINDOW,
        )
        .order_by(MetaDataDeletionRequest.requested_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


@router.post(DATA_DELETION_PATH, response_model=DataDeletionAck)
async def meta_data_deletion(request: Request) -> DataDeletionAck:
    """
    Handle Meta's Data Deletion Request Callback.

    Verifies the ``signed_request``, files a durable
    :class:`~app.models.meta_privacy.MetaDataDeletionRequest` under a fresh
    unguessable confirmation code, runs the erasure, and answers with exactly
    the two keys Meta specifies: ``url`` and ``confirmation_code``.

    The record is committed whether the erasure succeeded or not, because Meta
    is being told the code resolves and the person must be able to look it up.
    A failed erasure is recorded as ``failed`` and surfaces on the status page
    rather than being hidden behind a success response.

    Args:
        request: The callback request carrying ``signed_request``

    Returns:
        ``{"url": ..., "confirmation_code": ...}``

    Raises:
        HTTPException: 503 without an app secret, 400 for a bad signed request,
            500 (so Meta retries) when the request cannot be recorded at all
    """
    meta_user_id = await verified_meta_user_id(request)

    now = datetime.now(UTC)

    async with async_session_maker() as db:
        # A redelivery, a double click, or a replay inside the signature window
        # must not mint a second code or re-run the erasure. The request already
        # on file is the answer.
        existing = await find_recent_request(db, meta_user_id, now)
        if existing is not None:
            logger.info(
                "meta_data_deletion_deduplicated",
                confirmation_code=existing.confirmation_code,
                request_status=existing.status,
            )
            return DataDeletionAck(
                url=build_status_url(request, existing.confirmation_code),
                confirmation_code=existing.confirmation_code,
            )

        # 128 bits from the CSPRNG: the status endpoint is public, so the code
        # is the only thing standing between a stranger and one request's
        # status.
        confirmation_code = secrets.token_hex(16)

        record = MetaDataDeletionRequest(
            confirmation_code=confirmation_code,
            meta_user_id=meta_user_id,
            status=DataDeletionStatus.PENDING.value,
            requested_at=now,
        )
        db.add(record)

        try:
            # Savepoint: a failed erasure must not take the request record with
            # it, or the code we just promised Meta would resolve nowhere.
            async with db.begin_nested():
                connections = await erase_meta_user(db, meta_user_id, confirmation_code)

            record.status = DataDeletionStatus.COMPLETED.value
            record.completed_at = datetime.now(UTC)
            record.connections_cleared = len(connections)
            record.tenant_id = connections[0].tenant_id if connections else None
        # Recorded on the row, not swallowed: the status page shows 'failed'.
        except Exception as exc:
            logger.exception("meta_data_deletion_erasure_failed", error=str(exc))
            record.status = DataDeletionStatus.FAILED.value
            # Class name only. An exception string can carry a query, a row or a
            # credential; this must never become a leak channel.
            record.last_error = (
                f"Erasure failed ({type(exc).__name__}); see server logs"
            )

        try:
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.exception("meta_data_deletion_record_failed", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Data deletion handler failed; Meta will retry",
            ) from exc

        recorded_status = record.status

    logger.info(
        "meta_data_deletion_received",
        confirmation_code=confirmation_code,
        request_status=recorded_status,
    )

    return DataDeletionAck(
        url=build_status_url(request, confirmation_code),
        confirmation_code=confirmation_code,
    )


# =============================================================================
# Public status page
# =============================================================================

#: What each status means, in the plain language Meta requires the page to use.
STATUS_MESSAGES: dict[str, str] = {
    DataDeletionStatus.PENDING.value: (
        "Your deletion request has been received and is being processed."
    ),
    DataDeletionStatus.COMPLETED.value: (
        "Your deletion request is complete. The Facebook or Instagram "
        "connection authorised with your Meta account has been disconnected, "
        "the access tokens Stratum AI stored for it have been erased, and the "
        "Meta user id that linked it to you has been removed."
    ),
    DataDeletionStatus.FAILED.value: (
        "Your deletion request was received but could not be completed "
        "automatically. It has been escalated to our team and will be completed "
        "manually. Please contact support if you need confirmation."
    ),
}

#: Meta requires the status page to give "a legitimate justification for any
#: refusal to delete". Holding nothing is that justification, and it is the
#: honest answer for every connection made before the Meta user id was stored -
#: describing those as erased would be a false statement to the person asking.
NOTHING_HELD_MESSAGE = (
    "Your deletion request is complete. Stratum AI holds no data associated "
    "with your Meta account - no connection, no access token and no Meta "
    "identifier were found for it - so there was nothing to delete."
)


def _status_message(record: MetaDataDeletionRequest) -> str:
    """
    Return the human-readable explanation for one stored request.

    A completed request that matched nothing gets its own wording. The generic
    completion sentence describes tokens being erased and a connection being
    severed; saying that when neither happened would be untrue, and "nothing
    was found" is precisely the justification Meta asks this page to give.

    Args:
        record: The stored deletion request

    Returns:
        One plain-language sentence about that request
    """
    request_status = record.status
    if (
        request_status == DataDeletionStatus.COMPLETED.value
        and not record.connections_cleared
    ):
        return NOTHING_HELD_MESSAGE

    return STATUS_MESSAGES.get(
        request_status,
        STATUS_MESSAGES[DataDeletionStatus.PENDING.value],
    )


def _status_page_html(title: str, body: str) -> str:
    """
    Render the minimal human-readable page Meta requires the status URL to show.

    Args:
        title: Page heading
        body: Already-escaped explanatory sentence(s)

    Returns:
        A complete HTML document
    """
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title></head>"
        '<body style="font-family:system-ui,sans-serif;max-width:38rem;margin:3rem auto;padding:0 1rem">'
        f"<h1>{html.escape(title)}</h1><p>{body}</p>"
        "</body></html>"
    )


def _wants_html(request: Request) -> bool:
    """Report whether the caller is a browser asking for a rendered page."""
    return "text/html" in request.headers.get("accept", "")


@router.get(DATA_DELETION_STATUS_PATH, response_model=DataDeletionStatusResponse)
async def meta_data_deletion_status(
    request: Request,
    code: str = Query(
        default="", description="Confirmation code from the deletion request"
    ),
) -> Any:
    """
    Report the status of one data deletion request.

    This is the page ``url`` in the callback response points at, so it must work
    for a logged-out person in a browser: it renders HTML when the caller asks
    for HTML and JSON otherwise.

    It discloses only the state of the one code presented - never a Meta user
    id, a tenant, an email or a count. Any code not on file (including an empty
    one) gets the same generic 404, so the endpoint cannot be used to find out
    which codes exist.

    Args:
        request: The incoming request (used for content negotiation)
        code: The confirmation code returned by the deletion callback

    Returns:
        The request's status, as HTML or JSON

    Raises:
        HTTPException: 404 for any code not on file
    """
    record: MetaDataDeletionRequest | None = None

    if code:
        async with async_session_maker() as db:
            result = await db.execute(
                select(MetaDataDeletionRequest).where(
                    MetaDataDeletionRequest.confirmation_code == code
                )
            )
            record = result.scalar_one_or_none()

    if record is None:
        if _wants_html(request):
            return HTMLResponse(
                _status_page_html(
                    "Deletion request not found",
                    html.escape(UNKNOWN_CODE_DETAIL),
                ),
                status_code=status.HTTP_404_NOT_FOUND,
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=UNKNOWN_CODE_DETAIL,
        )

    message = _status_message(record)

    if _wants_html(request):
        completed = (
            f" Completed on {record.completed_at.strftime('%Y-%m-%d')}."
            if record.completed_at
            else ""
        )
        return HTMLResponse(
            _status_page_html(
                "Data deletion request",
                f"{html.escape(message)}<br><br>"
                f"Confirmation code: <code>{html.escape(record.confirmation_code)}</code><br>"
                f"Status: {html.escape(record.status)}."
                f"{html.escape(completed)}",
            )
        )

    return DataDeletionStatusResponse(
        confirmation_code=record.confirmation_code,
        status=record.status,
        requested_at=record.requested_at,
        completed_at=record.completed_at,
        message=message,
    )
