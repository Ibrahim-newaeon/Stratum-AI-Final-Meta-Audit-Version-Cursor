# =============================================================================
# Stratum AI - Meta signed_request verification
# =============================================================================
"""
Verification of Meta's ``signed_request``, shared by both App Review callbacks.

Meta authenticates its server-to-server privacy callbacks - the **Deauthorize
Callback** and the **Data Deletion Request Callback** - by POSTing a single
form field named ``signed_request``. There is no header signature and no
bearer token: this string *is* the credential, so parsing it correctly is the
whole of the authentication.

Wire format (verified against Meta's own reference implementation on
https://developers.facebook.com/docs/development/create-an-app/app-dashboard/data-deletion-callback/)::

    signed_request := base64url(signature) "." base64url(utf-8 json payload)

and the reference PHP is::

    list($encoded_sig, $payload) = explode('.', $signed_request, 2);
    $sig  = base64_url_decode($encoded_sig);
    $data = json_decode(base64_url_decode($payload), true);
    $expected_sig = hash_hmac('sha256', $payload, $secret, $raw = true);

Three details in that snippet are load-bearing and easy to get wrong:

1. The HMAC is computed over the **raw, still-base64url-encoded payload
   string**, not over the decoded JSON. Re-serialising the parsed JSON and
   signing that will never match.
2. The key is the **app secret**, not the app id and not an access token.
3. Meta strips base64 ``=`` padding, so both halves must be re-padded to a
   multiple of four before decoding.

The decoded payload is a JSON object carrying at least ``algorithm``
(``"HMAC-SHA256"``), ``issued_at`` and ``user_id`` - the app-scoped id (ASID)
of the person who deauthorised the app or asked for their data to be deleted.

Security properties this module guarantees:

- The signature is compared with :func:`hmac.compare_digest`, so a wrong
  signature cannot be recovered byte-by-byte from response timing. There is no
  ``==`` comparison of signature material anywhere in this file.
- Any ``algorithm`` other than ``HMAC-SHA256`` is rejected outright. An
  attacker must not be able to downgrade the callback to a weaker (or absent)
  algorithm by choosing the payload.
- A payload that decodes to something other than a JSON **object** is rejected,
  so a caller can never receive a list/int/string where it expects a mapping.
- :class:`SignedRequestError` messages are fixed constants. They never contain
  the app secret, the received signature, the expected signature or the
  payload, because these callbacks are public and their errors are logged.
- A verified request is additionally required to be **fresh**. A signed request
  is not a Meta-only secret - any person who has authorised the app can obtain
  one for their own ASID from the JS SDK - and without a time bound one
  captured or self-minted string would replay forever. ``issued_at`` must sit
  within :data:`MAX_AGE_SECONDS` of now (the same tolerance
  ``app.services.paddle_service`` applies to the Paddle webhook timestamp) and
  a non-zero ``expires`` must not be in the past.

  The window is deliberately symmetric, so a clock ahead of ours is tolerated
  as well. It is enforced only when the field is present: the field is signed,
  so a caller cannot strip it to escape the check, and a hypothetical Meta
  payload without one still verifies. The trade-off is stated plainly: if Meta
  ever retried a callback more than :data:`MAX_AGE_SECONDS` later with the
  *same* string rather than a freshly signed one, that retry is rejected. The
  deletion callback is idempotent per Meta user for exactly this reason, so the
  first delivery is what counts.
"""

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from typing import Any, Final

__all__ = ["MAX_AGE_SECONDS", "SignedRequestError", "parse_signed_request"]

#: The only signature algorithm Meta uses, and the only one accepted here.
EXPECTED_ALGORITHM: Final[str] = "HMAC-SHA256"

#: How far ``issued_at`` may sit from now, in either direction. Mirrors
#: ``paddle_service.DEFAULT_SIGNATURE_TOLERANCE_SECONDS`` so both public
#: webhook paths bound replay the same way.
MAX_AGE_SECONDS: Final[int] = 300

#: base64url alphabet (RFC 4648 section 5), padding excluded - Meta omits it.
_BASE64URL_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]+$")


class SignedRequestError(Exception):
    """
    A ``signed_request`` was absent, malformed, or not signed by this app.

    Raised for every rejection reason so a caller cannot accidentally treat an
    unverified payload as trusted. The message is always a fixed constant and
    never echoes secret or attacker-supplied material.
    """


def _decode_base64url(segment: str) -> bytes:
    """
    Decode one padding-less base64url segment of a ``signed_request``.

    Meta omits the ``=`` padding, so the segment is re-padded to a multiple of
    four before decoding. Characters outside the base64url alphabet are
    rejected explicitly, because :func:`base64.urlsafe_b64decode` silently
    discards them and would otherwise accept a mangled segment.

    Args:
        segment: One dot-separated half of the signed request

    Returns:
        The decoded bytes

    Raises:
        SignedRequestError: The segment is empty or not valid base64url
    """
    if not segment or not _BASE64URL_RE.match(segment):
        raise SignedRequestError("signed_request is not valid base64url")

    padded = segment + "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError) as exc:
        raise SignedRequestError("signed_request is not valid base64url") from exc


def _as_epoch_seconds(value: Any) -> int | None:
    """
    Read one signed timestamp field as whole epoch seconds.

    Args:
        value: The raw ``issued_at`` / ``expires`` value from the payload

    Returns:
        The timestamp, or None when the field was absent

    Raises:
        SignedRequestError: The field is present but not a number. It is inside
            the signed payload, so an unusable value means the request is not
            shaped the way Meta shapes one and must not be trusted.
    """
    if value is None:
        return None
    # bool is an int subclass; ``issued_at: true`` is not a timestamp.
    if isinstance(value, bool):
        raise SignedRequestError("signed_request timestamp is not a number")
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError as exc:
            raise SignedRequestError(
                "signed_request timestamp is not a number"
            ) from exc
    raise SignedRequestError("signed_request timestamp is not a number")


def _require_fresh(
    payload: dict[str, Any], max_age_seconds: int, now: int | None
) -> None:
    """
    Reject a verified-but-stale payload, bounding how long one can be replayed.

    Runs only after the signature has been checked, so it never reasons about
    attacker-chosen content: an attacker cannot alter ``issued_at`` without
    invalidating the signature, and cannot remove it either.

    Args:
        payload: The already-verified payload
        max_age_seconds: Tolerance applied in both directions to ``issued_at``
        now: Epoch seconds to compare against; ``None`` uses the wall clock

    Raises:
        SignedRequestError: ``issued_at`` is outside the tolerance window, or a
            non-zero ``expires`` is already in the past. Neither message
            contains any payload content.
    """
    current = int(time.time()) if now is None else int(now)

    issued_at = _as_epoch_seconds(payload.get("issued_at"))
    if issued_at is not None and abs(current - issued_at) > max_age_seconds:
        raise SignedRequestError(
            "signed_request timestamp is outside the tolerance window"
        )

    # Meta uses expires=0 for "does not expire"; only a real deadline counts.
    expires = _as_epoch_seconds(payload.get("expires"))
    if expires is not None and expires > 0 and current - expires > max_age_seconds:
        raise SignedRequestError("signed_request has expired")


def parse_signed_request(
    signed_request: str,
    app_secret: str,
    *,
    max_age_seconds: int = MAX_AGE_SECONDS,
    now: int | None = None,
) -> dict[str, Any]:
    """
    Verify a Meta ``signed_request`` and return its decoded payload.

    Implements Meta's documented contract exactly: split on the single ``.``,
    base64url-decode both halves, require ``algorithm == "HMAC-SHA256"``, and
    compare the received signature in constant time against HMAC-SHA256 of the
    **raw encoded payload string** keyed with the app secret. A verified
    payload is then required to be fresh, so a captured or self-minted string
    cannot be replayed indefinitely.

    Args:
        signed_request: The ``signed_request`` form field POSTed by Meta
        app_secret: The Meta app secret (``settings.meta_app_secret``)
        max_age_seconds: How far ``issued_at`` may sit from now in either
            direction. Defaults to :data:`MAX_AGE_SECONDS`.
        now: Epoch seconds to treat as the current time. For tests; ``None``
            uses the wall clock.

    Returns:
        The verified payload as a dict, e.g.
        ``{"algorithm": "HMAC-SHA256", "issued_at": 1291840400,
        "user_id": "218471"}``

    Raises:
        SignedRequestError: The secret is missing, or the request is malformed,
            uses an unexpected algorithm, does not decode to a JSON object,
            carries a signature this app did not produce, or is stale. The
            message never contains the secret, the signature or the payload.
    """
    if not app_secret or not isinstance(app_secret, str):
        raise SignedRequestError("Meta app secret is not configured")

    if not signed_request or not isinstance(signed_request, str):
        raise SignedRequestError("signed_request is missing")

    # Exactly one separator. The base64url alphabet excludes ".", so a second
    # one means the value was tampered with or concatenated.
    if signed_request.count(".") != 1:
        raise SignedRequestError("signed_request is malformed")

    encoded_signature, encoded_payload = signed_request.split(".", 1)

    received_signature = _decode_base64url(encoded_signature)
    payload_bytes = _decode_base64url(encoded_payload)

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SignedRequestError("signed_request payload is not valid JSON") from exc

    if not isinstance(payload, dict):
        raise SignedRequestError("signed_request payload is not a JSON object")

    # Reject before verifying, and never trust the payload's own claim about
    # how it was signed: only HMAC-SHA256 is ever computed below.
    algorithm = payload.get("algorithm")
    if not isinstance(algorithm, str) or algorithm.upper() != EXPECTED_ALGORITHM:
        raise SignedRequestError("signed_request uses an unsupported algorithm")

    # The signature covers the raw encoded payload string, not the decoded JSON.
    expected_signature = hmac.new(
        app_secret.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()

    # Constant time: never "==" on signature material.
    if not hmac.compare_digest(expected_signature, received_signature):
        raise SignedRequestError("signed_request signature does not match")

    # Only now, on content this app provably signed, is the clock consulted.
    _require_fresh(payload, max_age_seconds, now)

    return payload
