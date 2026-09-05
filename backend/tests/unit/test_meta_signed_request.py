# =============================================================================
# Stratum AI - Meta signed_request verification tests
# =============================================================================
"""
Unit tests for ``app.services.meta.signed_request``.

This module is the whole authentication of both Meta App Review callbacks, so
the tests are adversarial: every way a caller could get an unsigned or
wrongly-signed payload accepted is asserted to fail, and the constant-time
comparison is asserted to actually happen rather than assumed.
"""

import ast
import base64
import hashlib
import hmac
import inspect
import json
import re
import time
from typing import Any
from unittest import mock

import pytest

from app.services.meta import signed_request as sr
from app.services.meta.signed_request import SignedRequestError, parse_signed_request

pytestmark = pytest.mark.unit

APP_SECRET = "meta-app-secret-do-not-log"
OTHER_SECRET = "some-other-apps-secret"
META_USER_ID = "1234567890"

#: Distinguishes "caller passed nothing" from "caller passed JSON null".
_DEFAULT = object()


# =============================================================================
# Helpers - build a signed_request exactly the way Meta does
# =============================================================================


def _b64url(raw: bytes) -> str:
    """base64url-encode without padding, as Meta does."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _make_signed_request(
    payload: Any = _DEFAULT,
    secret: str = APP_SECRET,
    *,
    signature_override: bytes | None = None,
) -> str:
    """
    Build ``base64url(sig) + "." + base64url(payload)`` the way Meta does.

    Args:
        payload: Object to serialise as the payload (defaults to a valid one)
        secret: Key the HMAC is computed with
        signature_override: Raw signature bytes to use instead of the real one

    Returns:
        A complete signed_request string
    """
    if payload is _DEFAULT:
        payload = {
            "algorithm": "HMAC-SHA256",
            "issued_at": int(time.time()),
            "user_id": META_USER_ID,
        }
    encoded_payload = _b64url(json.dumps(payload).encode("utf-8"))
    signature = (
        signature_override
        or hmac.new(
            secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256
        ).digest()
    )
    return f"{_b64url(signature)}.{encoded_payload}"


# =============================================================================
# The happy path
# =============================================================================


def test_correctly_signed_request_is_accepted() -> None:
    """A request signed with the app secret returns its decoded payload."""
    signed = _make_signed_request(
        {
            "algorithm": "HMAC-SHA256",
            "issued_at": 1789000000,
            "user_id": META_USER_ID,
        }
    )

    payload = parse_signed_request(signed, APP_SECRET, now=1789000000)

    assert payload["user_id"] == META_USER_ID
    assert payload["algorithm"] == "HMAC-SHA256"
    assert payload["issued_at"] == 1789000000


def test_signature_covers_the_raw_encoded_payload_not_the_decoded_json() -> None:
    """
    The HMAC is over the base64url text, exactly as Meta's reference does.

    Signing the re-serialised JSON instead is the classic implementation bug;
    it must not verify.
    """
    body = {"algorithm": "HMAC-SHA256", "user_id": META_USER_ID}
    encoded_payload = _b64url(json.dumps(body).encode("utf-8"))

    wrong = hmac.new(
        APP_SECRET.encode(), json.dumps(body).encode("utf-8"), hashlib.sha256
    ).digest()
    with pytest.raises(SignedRequestError):
        parse_signed_request(f"{_b64url(wrong)}.{encoded_payload}", APP_SECRET)

    right = hmac.new(
        APP_SECRET.encode(), encoded_payload.encode("ascii"), hashlib.sha256
    ).digest()
    assert parse_signed_request(f"{_b64url(right)}.{encoded_payload}", APP_SECRET)[
        "user_id"
    ]


def test_missing_base64_padding_is_handled() -> None:
    """Meta strips '=' padding; every payload length must still decode."""
    for filler in range(1, 12):
        request = _make_signed_request(
            {"algorithm": "HMAC-SHA256", "user_id": "x" * filler}
        )
        assert parse_signed_request(request, APP_SECRET)["user_id"] == "x" * filler


# =============================================================================
# Rejections
# =============================================================================


def test_tampered_payload_is_rejected() -> None:
    """Changing the payload after signing invalidates the signature."""
    signature, _, encoded_payload = _make_signed_request().partition(".")
    tampered = _b64url(
        json.dumps({"algorithm": "HMAC-SHA256", "user_id": "999999"}).encode("utf-8")
    )
    assert tampered != encoded_payload

    with pytest.raises(SignedRequestError):
        parse_signed_request(f"{signature}.{tampered}", APP_SECRET)


def test_request_signed_with_the_wrong_secret_is_rejected() -> None:
    """Another app's secret must never verify against ours."""
    with pytest.raises(SignedRequestError):
        parse_signed_request(_make_signed_request(secret=OTHER_SECRET), APP_SECRET)


@pytest.mark.parametrize(
    "algorithm",
    ["HMAC-SHA1", "none", "None", "", "MD5", "hmac-sha512"],
)
def test_non_hmac_sha256_algorithms_are_rejected(algorithm: str) -> None:
    """
    An attacker must not be able to downgrade the algorithm via the payload.

    The request below is signed *correctly* with the app secret, so only the
    algorithm check can reject it.
    """
    request = _make_signed_request({"algorithm": algorithm, "user_id": META_USER_ID})

    with pytest.raises(SignedRequestError) as exc_info:
        parse_signed_request(request, APP_SECRET)

    assert "algorithm" in str(exc_info.value)


def test_missing_algorithm_field_is_rejected() -> None:
    """A payload with no algorithm at all is not trusted either."""
    with pytest.raises(SignedRequestError):
        parse_signed_request(
            _make_signed_request({"user_id": META_USER_ID}), APP_SECRET
        )


def test_non_string_algorithm_is_rejected() -> None:
    """``algorithm`` must be a string, not a truthy object."""
    with pytest.raises(SignedRequestError):
        parse_signed_request(
            _make_signed_request({"algorithm": 256, "user_id": META_USER_ID}),
            APP_SECRET,
        )


@pytest.mark.parametrize(
    "value",
    [
        "not-base64!.also-not-base64!",
        "***.***",
        "aGVsbG8=.$$$$",
        "$$$$.aGVsbG8",
        "  .  ",
    ],
)
def test_malformed_base64_is_rejected(value: str) -> None:
    """Characters outside the base64url alphabet are refused, not silently dropped."""
    with pytest.raises(SignedRequestError):
        parse_signed_request(value, APP_SECRET)


@pytest.mark.parametrize(
    "value",
    ["", "nodothere", "abcdef", "a.b.c", "..", "."],
)
def test_missing_or_extra_separator_is_rejected(value: str) -> None:
    """Exactly one '.' separates the two halves; anything else is malformed."""
    with pytest.raises(SignedRequestError):
        parse_signed_request(value, APP_SECRET)


@pytest.mark.parametrize(
    "payload",
    [[1, 2, 3], "a string", 42, None, True],
)
def test_non_object_payload_is_rejected(payload: Any) -> None:
    """A payload that is valid JSON but not an object cannot be a signed request."""
    with pytest.raises(SignedRequestError) as exc_info:
        parse_signed_request(_make_signed_request(payload), APP_SECRET)

    assert "JSON object" in str(exc_info.value) or "algorithm" in str(exc_info.value)


def test_payload_that_is_not_json_is_rejected() -> None:
    """Correctly signed garbage is still garbage."""
    encoded_payload = _b64url(b"this is not json")
    signature = hmac.new(
        APP_SECRET.encode(), encoded_payload.encode("ascii"), hashlib.sha256
    ).digest()

    with pytest.raises(SignedRequestError) as exc_info:
        parse_signed_request(f"{_b64url(signature)}.{encoded_payload}", APP_SECRET)

    assert "JSON" in str(exc_info.value)


def test_truncated_signature_is_rejected() -> None:
    """A short signature must not compare equal to a full one."""
    real = hmac.new(
        APP_SECRET.encode(),
        _b64url(
            json.dumps({"algorithm": "HMAC-SHA256", "user_id": "1"}).encode()
        ).encode("ascii"),
        hashlib.sha256,
    ).digest()

    with pytest.raises(SignedRequestError):
        parse_signed_request(
            _make_signed_request(
                {"algorithm": "HMAC-SHA256", "user_id": "1"},
                signature_override=real[:16],
            ),
            APP_SECRET,
        )


@pytest.mark.parametrize("secret", ["", None])
def test_missing_app_secret_is_rejected(secret: Any) -> None:
    """Without a configured secret nothing can be verified, so nothing is trusted."""
    with pytest.raises(SignedRequestError) as exc_info:
        parse_signed_request(_make_signed_request(), secret)

    assert "secret" in str(exc_info.value)


@pytest.mark.parametrize("value", [None, b"bytes", 123, []])
def test_non_string_signed_request_is_rejected(value: Any) -> None:
    """A non-string input is refused rather than coerced."""
    with pytest.raises(SignedRequestError):
        parse_signed_request(value, APP_SECRET)


# =============================================================================
# Constant-time comparison
# =============================================================================


def test_signature_comparison_uses_hmac_compare_digest() -> None:
    """The signature check must go through ``hmac.compare_digest``."""
    with mock.patch.object(sr.hmac, "compare_digest", wraps=hmac.compare_digest) as spy:
        parse_signed_request(_make_signed_request(), APP_SECRET)

    assert spy.call_count == 1, "the signature was not compared in constant time"


def test_a_failing_comparison_also_goes_through_compare_digest() -> None:
    """The rejection path is constant time too, not a short-circuit."""
    with mock.patch.object(
        sr.hmac, "compare_digest", wraps=hmac.compare_digest
    ) as spy, pytest.raises(SignedRequestError):
        parse_signed_request(_make_signed_request(secret=OTHER_SECRET), APP_SECRET)

    assert spy.call_count == 1


def test_no_equality_comparison_on_signature_material_in_the_source() -> None:
    """
    No ``==`` / ``!=`` compares signature bytes anywhere in the module.

    Asserted on the parsed AST rather than on behaviour, because a byte-by-byte
    comparison reintroduced in a future refactor would still pass every test
    above while leaking the expected signature through response timing.
    """
    tree = ast.parse(inspect.getsource(sr))

    offenders = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops)
        and re.search(r"signature|sig\b|digest", ast.unparse(node), re.IGNORECASE)
    ]

    assert offenders == [], f"signature compared with ==/!=: {offenders}"


def test_compare_digest_is_actually_called_in_the_source() -> None:
    """``hmac.compare_digest`` appears as a call, not merely as a comment."""
    tree = ast.parse(inspect.getsource(sr))

    calls = [
        ast.unparse(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and "compare_digest" in ast.unparse(node.func)
    ]

    assert calls == ["hmac.compare_digest"], calls


# =============================================================================
# The app secret never leaks
# =============================================================================


@pytest.mark.parametrize(
    "bad_request",
    [
        "",
        "no-dot",
        "!!!.???",
        _make_signed_request(secret=OTHER_SECRET),
        _make_signed_request({"algorithm": "none", "user_id": "1"}),
        _make_signed_request([1, 2, 3]),
    ],
)
def test_error_messages_never_contain_the_app_secret(bad_request: str) -> None:
    """A public endpoint logs these messages, so they must be secret-free."""
    with pytest.raises(SignedRequestError) as exc_info:
        parse_signed_request(bad_request, APP_SECRET)

    message = str(exc_info.value)
    assert APP_SECRET not in message
    assert bad_request not in message or bad_request == ""
    # And no fragment of the secret either.
    assert "meta-app-secret" not in message


# =============================================================================
# Freshness - a verified request must also be recent
# =============================================================================


def _fresh_payload(**overrides: Any) -> dict[str, Any]:
    """A valid payload at a fixed instant, with fields overridden per test."""
    payload: dict[str, Any] = {
        "algorithm": "HMAC-SHA256",
        "issued_at": _NOW,
        "user_id": META_USER_ID,
    }
    payload.update(overrides)
    return payload


#: Fixed reference instant, so these tests never depend on the wall clock.
_NOW = 1789000000


def test_a_request_inside_the_window_is_accepted() -> None:
    """The tolerance is real, not zero: a few seconds of skew is fine."""
    signed = _make_signed_request(_fresh_payload(issued_at=_NOW - 60))

    assert parse_signed_request(signed, APP_SECRET, now=_NOW)["user_id"] == META_USER_ID


@pytest.mark.parametrize("age", [sr.MAX_AGE_SECONDS + 1, 3600, 86_400, 31_536_000])
def test_a_stale_request_is_rejected(age: int) -> None:
    """
    Replay is bounded. Without this a captured or self-minted signed_request
    would work forever, since it carries no nonce and nothing consumes it.
    """
    signed = _make_signed_request(_fresh_payload(issued_at=_NOW - age))

    with pytest.raises(SignedRequestError, match="tolerance window"):
        parse_signed_request(signed, APP_SECRET, now=_NOW)


def test_a_far_future_request_is_rejected() -> None:
    """The window is symmetric, so post-dating buys an attacker nothing."""
    signed = _make_signed_request(_fresh_payload(issued_at=_NOW + 86_400))

    with pytest.raises(SignedRequestError, match="tolerance window"):
        parse_signed_request(signed, APP_SECRET, now=_NOW)


def test_an_expired_request_is_rejected() -> None:
    """A payload whose own deadline has passed is not usable."""
    signed = _make_signed_request(_fresh_payload(expires=_NOW - 86_400))

    with pytest.raises(SignedRequestError, match="expired"):
        parse_signed_request(signed, APP_SECRET, now=_NOW)


def test_expires_zero_means_no_expiry() -> None:
    """Meta uses ``expires: 0`` for "does not expire"; it is not the epoch."""
    signed = _make_signed_request(_fresh_payload(expires=0))

    assert parse_signed_request(signed, APP_SECRET, now=_NOW)["expires"] == 0


def test_a_future_expiry_is_accepted() -> None:
    """A live deadline passes."""
    signed = _make_signed_request(_fresh_payload(expires=_NOW + 3600))

    assert parse_signed_request(signed, APP_SECRET, now=_NOW)["user_id"] == META_USER_ID


def test_a_payload_without_issued_at_still_verifies() -> None:
    """
    The check is enforced only when the field is present.

    ``issued_at`` is inside the signed payload, so a caller cannot strip it to
    escape the window - only Meta could omit it, and a Meta payload that does
    must not be rejected.
    """
    signed = _make_signed_request({"algorithm": "HMAC-SHA256", "user_id": META_USER_ID})

    assert parse_signed_request(signed, APP_SECRET, now=_NOW)["user_id"] == META_USER_ID


@pytest.mark.parametrize("value", ["not-a-number", True, [], {}, None])
def test_an_unusable_issued_at_is_rejected(value: Any) -> None:
    """A present-but-nonsense timestamp is not a Meta payload; do not trust it."""
    signed = _make_signed_request(_fresh_payload(issued_at=value))

    if value is None:
        # An explicit null reads as absent, which is the documented lenient case.
        assert parse_signed_request(signed, APP_SECRET, now=_NOW)
        return

    with pytest.raises(SignedRequestError):
        parse_signed_request(signed, APP_SECRET, now=_NOW)


def test_freshness_is_checked_only_after_the_signature() -> None:
    """
    The clock must never be consulted on content this app did not sign.

    A stale *and* wrongly signed request must fail on the signature, so the
    rejection reason cannot become an oracle for whether a forged payload's
    timestamp was in range.
    """
    signed = _make_signed_request(
        _fresh_payload(issued_at=_NOW - 86_400), secret=OTHER_SECRET
    )

    with pytest.raises(SignedRequestError, match="signature does not match"):
        parse_signed_request(signed, APP_SECRET, now=_NOW)


def test_the_default_window_matches_the_house_webhook_tolerance() -> None:
    """The Paddle webhook bounds replay the same way; both stay in step."""
    from app.services.paddle_service import DEFAULT_SIGNATURE_TOLERANCE_SECONDS

    assert sr.MAX_AGE_SECONDS == DEFAULT_SIGNATURE_TOLERANCE_SECONDS
