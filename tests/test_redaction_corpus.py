"""Adversarial redaction corpus — the executable definition of safe error text.

Consolidates every secret shape found in review rounds 1-9. Each shape must be
absent from every documented carrier: ``FinvizError.args``, ``str(error)``,
stored ``context``, ``FetchWarning.message`` and ``UnitError.raw``. New secret
encodings outside these shape classes belong on the m05-hardening card, not
here; encodings INSIDE these classes must never regress.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from finvizp.errors import FetchWarning, FinvizError, FinvizQueryError, UnitError
from finvizp.results import AccessTier, ResultMetadata, ResultStatus

SECRET = "super-secret-value-1234"
PROXY_HOST = "proxy-gw.example.invalid"

# One entry per secret SHAPE (round found in parens). Each is a string that
# embeds SECRET (and, for proxy routes, PROXY_HOST) in that encoding.
TEXT_SHAPES = [
    # --- auth headers -------------------------------------------------------
    "Authorization: Bearer " + SECRET,  # R1/R4
    "upstream=Authorization: Bearer " + SECRET,  # R6
    "Authorization: Basic " + SECRET,  # R5
    "Proxy-Authorization: Basic " + SECRET,  # R5
    "X-Api-Key: " + SECRET,  # R7
    "X-Auth-Token: " + SECRET,  # R7
    'Authorization: Digest username="u", realm="r", nonce="abc", '
    'uri="/q", response="' + SECRET + '", opaque="z"',  # R9
    # --- cookies ------------------------------------------------------------
    "Set-Cookie: sid=" + SECRET + "; Path=/",  # R6
    "Cookie: session=opaque; theme=" + SECRET + "; tz=utc",  # R9
    "cookies=" + SECRET + " expired",  # R5
    # --- query/env secret assignments ---------------------------------------
    "GET /q?access_token=" + SECRET + "&t=AAPL",  # R3
    'request token="' + SECRET + '" rejected',  # R5
    "request token='" + SECRET + "' rejected",  # R5
    '{"error": "auth failed", "access_token": "' + SECRET + '"}',  # R8
    '{"access_token": "he said \\" hi ' + SECRET + '"}',  # R9
    # --- response bodies ----------------------------------------------------
    "response_body=" + SECRET + " parse halted",  # R7
    "response_body=prefix " + SECRET + " trailing parse halted",  # R9
    # --- proxy URLs (route + host are the secret) ----------------------------
    "via proxy http://user:" + SECRET + "@" + PROXY_HOST + ":9",  # R5
    "proxy URL: http://user:pw@" + PROXY_HOST + ":8080",  # R6
    "HTTP_PROXY=http://gate.example.invalid:8080/" + SECRET,  # R7
    "proxy-url: http://route.example.invalid/" + SECRET,  # R7
    '{"proxy_url": "http://' + PROXY_HOST + ":8080/" + SECRET + '"}',  # R8
]

# Context mapping keys that must redact their value wholesale (round found).
KEY_CASES = [
    ({"cookies": SECRET}, "cookies", "[REDACTED]"),  # R1
    ({"nested": {"token": SECRET}}, ("nested", "token"), "[REDACTED]"),  # R1
    ({"session_cookie": SECRET}, "session_cookie", "[REDACTED]"),  # R1
    ({"responseBody": SECRET}, "responseBody", "[BODY REDACTED]"),  # R4
    ({"accessToken": SECRET}, "accessToken", "[REDACTED]"),  # R4
    ({"proxyUrl": SECRET}, "proxyUrl", "[REDACTED]"),  # R4
]

# Text that must stay PUBLIC despite containing credential vocabulary.
SAFE_TEXTS = [
    "invalid token format",
    "session expired for AAPL",
    "GET /q?token=[REDACTED]&t=AAPL",  # neighbouring query params survive
]


def _carriers(text: str) -> list[str]:
    """Render ``text`` through every documented public carrier."""
    warning = FetchWarning(code="leak", message=text)
    unit_error = UnitError(code="unit", message="bad", raw=text)
    meta = ResultMetadata(
        endpoint="quote",
        status=ResultStatus.PARTIAL,
        access_tier=AccessTier.PUBLIC,
        fetched_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
        requested_units=2,
        succeeded_units=1,
        failed_units=1,
        warnings=(warning,),
        unit_errors=(unit_error,),
    )
    message_error = FinvizError(text)
    context_error = FinvizQueryError("bad", context={"note": text})
    return [
        message_error.args[0],
        str(message_error),
        context_error.context["note"],
        str(context_error),
        warning.message,
        unit_error.raw or "",
        str(meta.warnings[0]),
        str(meta.unit_errors[0]),
    ]


@pytest.mark.parametrize("shape", TEXT_SHAPES, ids=lambda s: s[:28])
def test_corpus_shape_never_reaches_a_carrier(shape: str) -> None:
    for carrier in _carriers(shape):
        assert SECRET not in carrier, f"leak in carrier: {carrier!r}"
        assert PROXY_HOST not in carrier, f"route leak in carrier: {carrier!r}"
        assert "[REDACTED]" in carrier


@pytest.mark.parametrize("context,path,expected", KEY_CASES)
def test_corpus_sensitive_keys_redact_in_context(
    context: dict, path: object, expected: str
) -> None:
    error = FinvizError("bad", context=context)
    stored = error.context
    for key in path if isinstance(path, tuple) else (path,):
        stored = stored[key]  # type: ignore[index]
    assert stored == expected
    assert SECRET not in str(error)


@pytest.mark.parametrize("text", SAFE_TEXTS)
def test_corpus_keeps_safe_text_public(text: str) -> None:
    assert text in str(FinvizError(text))
