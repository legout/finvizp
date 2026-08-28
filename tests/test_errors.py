"""Contract tests for the finvizp error hierarchy and safe context redaction."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pyarrow as pa
import pytest

from finvizp.errors import (
    REDACTED,
    FetchWarning,
    FinvizBatchError,
    FinvizBlockedError,
    FinvizDataError,
    FinvizEntitlementError,
    FinvizError,
    FinvizNotFoundError,
    FinvizParseError,
    FinvizPartialError,
    FinvizQueryError,
    FinvizRateLimitError,
    FinvizTransportError,
    UnitError,
    format_context,
    redact_value,
)
from finvizp.results import AccessTier, FetchResult, ResultMetadata, ResultStatus

SECRET = "super-secret-value-1234"

HIERARCHY = [
    FinvizTransportError,
    FinvizRateLimitError,
    FinvizBlockedError,
    FinvizEntitlementError,
    FinvizNotFoundError,
    FinvizQueryError,
    FinvizPartialError,
    FinvizBatchError,
    FinvizParseError,
    FinvizDataError,
]


def _meta(**overrides: object) -> ResultMetadata:
    defaults: dict[str, object] = {
        "endpoint": "quote",
        "status": ResultStatus.COMPLETE,
        "access_tier": AccessTier.PUBLIC,
        "fetched_at": datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return ResultMetadata(**defaults)  # type: ignore[arg-type]


@pytest.mark.parametrize("cls", HIERARCHY)
def test_public_error_hierarchy(cls: type[FinvizError]) -> None:
    assert issubclass(cls, FinvizError)
    assert issubclass(FinvizError, Exception)
    assert len(set(HIERARCHY)) == 10


def test_error_context_is_immutable() -> None:
    error = FinvizQueryError("bad symbol", context={"t": "BRK.B"})
    with pytest.raises(TypeError):
        error.context["t"] = "MSFT"  # type: ignore[index]


def test_error_context_attribute_is_redacted_and_frozen() -> None:
    error = FinvizQueryError(
        "bad symbol",
        context={"cookies": SECRET, "nested": {"token": SECRET}, "t": "BRK.B"},
    )
    assert error.context["cookies"] == REDACTED
    assert error.context["nested"]["token"] == REDACTED
    assert error.context["t"] == "BRK.B"
    with pytest.raises(TypeError):
        error.context["extra"] = 1  # type: ignore[index]
    with pytest.raises(TypeError):
        error.context["nested"]["extra"] = 1  # type: ignore[index]


def test_error_str_includes_redacted_context() -> None:
    error = FinvizQueryError("bad symbol", context={"t": "BRK.B", "session_cookie": SECRET})
    rendered = str(error)
    assert "bad symbol" in rendered
    assert "BRK.B" in rendered
    assert SECRET not in rendered


def test_partial_error_carries_immutable_partial_result() -> None:
    metadata = _meta(
        status=ResultStatus.PARTIAL,
        requested_units=2,
        succeeded_units=1,
        failed_units=1,
    )
    partial = FetchResult(
        data=pa.table({"symbol": ["AAPL"]}),
        metadata=metadata,
    )
    error = FinvizPartialError("one unit failed", partial_result=partial)
    assert isinstance(error, FinvizError)
    assert error.partial_result is partial
    with pytest.raises(dataclasses.FrozenInstanceError):
        partial.data = None  # type: ignore[misc]


def test_format_context_redacts_nested_sensitive_keys() -> None:
    context = {
        "symbol": "AAPL",
        "cookies": {"session": SECRET},
        "headers": {"Authorization": f"Bearer {SECRET}", "Accept": "text/html"},
        "proxy": "http://user:pass@proxy.example:8080",
        "proxy_url": "socks5://user:pass@proxy.example:1080",
        "query": {"t": "AAPL", "api_key": SECRET},
        "response_body": "<html>secret page</html>",
        "nested": {"deep": [{"token": SECRET}]},
    }
    rendered = format_context(context)
    assert "AAPL" in rendered
    assert "text/html" in rendered
    assert SECRET not in rendered
    assert "user:pass" not in rendered
    assert "proxy.example:8080" not in rendered
    assert "proxy.example:1080" not in rendered
    assert "secret page" not in rendered


def test_error_message_is_redacted_before_storage() -> None:
    error = FinvizQueryError(
        f"request failed: https://finviz.com/api/quote.ashx?access_token={SECRET}&t=AAPL"
    )
    rendered = str(error)
    assert SECRET not in rendered
    assert "access_token=[REDACTED]" in rendered
    assert all(SECRET not in str(arg) for arg in error.args)


def test_error_context_scrubs_access_token_query_strings() -> None:
    error = FinvizQueryError(
        "bad request",
        context={"note": f"GET https://example.invalid/?access_token={SECRET}&t=AAPL"},
    )
    assert SECRET not in error.context["note"]
    assert "access_token=[REDACTED]" in error.context["note"]
    assert SECRET not in str(error)


def test_error_context_freezes_sets_and_redacts_elements() -> None:
    tags = {f"https://x.invalid/?token={SECRET}", "plain"}
    error = FinvizQueryError("bad", context={"tags": tags})
    stored = error.context["tags"]
    assert isinstance(stored, frozenset)
    assert stored == frozenset({"https://x.invalid/?token=[REDACTED]", "plain"})
    tags.add("mutated")
    assert "mutated" not in stored
    with pytest.raises(AttributeError):
        stored.add("nope")  # type: ignore[attr-defined]


def test_format_context_renders_frozensets() -> None:
    rendered = format_context({"tags": frozenset({"a", "b"})})
    assert "a" in rendered
    assert "b" in rendered
    assert "frozenset" not in rendered


def test_redact_value_scrubs_url_credentials_in_strings() -> None:
    redacted = redact_value({"note": "request to http://user:pass@host/path failed"})
    rendered = format_context(redacted)
    assert "user:pass" not in rendered


def test_redact_value_scrubs_query_secrets_in_strings() -> None:
    redacted = redact_value({"note": f"GET /api/statement.ashx?token={SECRET}&t=AAPL"})
    rendered = format_context(redacted)
    assert "AAPL" in rendered
    assert SECRET not in rendered
    assert "token=[REDACTED]" in rendered


def test_redact_value_keeps_safe_values() -> None:
    redacted = redact_value({"symbol": "AAPL", "counts": [1, 2]})
    rendered = format_context(redacted)
    assert "AAPL" in rendered


def test_error_message_scrubs_authorization_header_and_proxy_values() -> None:
    error = FinvizQueryError(
        f"request failed: proxy=http://route.example:9 Authorization: Bearer {SECRET}"
    )
    rendered = str(error)
    assert SECRET not in rendered
    assert "route.example" not in rendered
    assert "[REDACTED]" in rendered
    assert all(SECRET not in str(arg) for arg in error.args)


def test_error_message_redacts_basic_and_proxy_authorization_headers() -> None:
    for text in (
        f"upstream rejected Proxy-Authorization: Basic {SECRET}",
        f"auth failed with Authorization: Basic {SECRET}",
    ):
        error = FinvizQueryError(text)
        assert SECRET not in str(error)
        assert all(SECRET not in str(arg) for arg in error.args)


def test_error_message_redacts_proxy_url_in_prose() -> None:
    error = FinvizQueryError(f"route failed via proxy http://user:{SECRET}@proxy.example:9")
    assert SECRET not in str(error)
    assert "proxy.example" not in str(error)
    assert all(SECRET not in str(arg) for arg in error.args)


def test_error_message_redacts_quoted_query_secret_values() -> None:
    for text in (
        f'request token="{SECRET}" rejected',
        f"request token='{SECRET}' rejected",
        f"cookies={SECRET} expired",
    ):
        error = FinvizQueryError(text)
        assert SECRET not in str(error)
        assert all(SECRET not in str(arg) for arg in error.args)


def test_error_context_recognizes_camel_case_sensitive_keys() -> None:
    error = FinvizQueryError(
        "bad request",
        context={"responseBody": SECRET, "accessToken": SECRET, "proxyUrl": SECRET},
    )
    assert error.context["responseBody"] == "[BODY REDACTED]"
    assert error.context["accessToken"] == REDACTED
    assert error.context["proxyUrl"] == REDACTED
    assert SECRET not in str(error)


def test_warning_and_unit_error_public_fields_are_redacted() -> None:
    warning = FetchWarning(code="extra_field", message=f"upstream Authorization: Bearer {SECRET}")
    unit_error = UnitError(code="unit_convert", message="bad unit", raw=f"cookies={SECRET}")
    assert SECRET not in warning.message
    assert SECRET not in unit_error.message
    assert SECRET not in (unit_error.raw or "")
    meta = _meta(warnings=(warning,), unit_errors=(unit_error,))
    assert all(SECRET not in str(item) for item in meta.warnings)
    assert all(SECRET not in str(item) for item in meta.unit_errors)


def test_error_message_redacts_header_values_after_equals_sign() -> None:
    """Round 6: header names may directly follow ``=`` and arbitrary cookie names."""
    error = FinvizError(f"upstream=Authorization: Bearer {SECRET}")
    assert SECRET not in str(error.args[0])
    cookie_error = FinvizError(f"Set-Cookie: sid={SECRET}; Path=/")
    assert SECRET not in str(cookie_error)
    assert SECRET not in cookie_error.args[0]


def test_error_message_redacts_labelled_proxy_urls() -> None:
    error = FinvizError(f"proxy URL: http://user:{SECRET}@proxy-gw.example.invalid:8080")
    text = str(error)
    assert SECRET not in text
    assert "proxy-gw.example.invalid" not in text
    assert SECRET not in error.args[0]


def test_warning_and_unit_error_records_are_frozen() -> None:
    warning = FetchWarning(code="extra_field", message="unknown label", symbol="AAPL")
    unit_error = UnitError(code="unit_convert", message="bad unit", raw="1.2x", symbol="AAPL")
    assert warning.code == "extra_field"
    assert unit_error.raw == "1.2x"
    with pytest.raises(dataclasses.FrozenInstanceError):
        warning.code = "other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        unit_error.raw = "9.9"  # type: ignore[misc]
