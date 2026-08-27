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


def test_warning_and_unit_error_records_are_frozen() -> None:
    warning = FetchWarning(code="extra_field", message="unknown label", symbol="AAPL")
    unit_error = UnitError(code="unit_convert", message="bad unit", raw="1.2x", symbol="AAPL")
    assert warning.code == "extra_field"
    assert unit_error.raw == "1.2x"
    with pytest.raises(dataclasses.FrozenInstanceError):
        warning.code = "other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        unit_error.raw = "9.9"  # type: ignore[misc]
