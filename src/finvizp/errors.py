"""Typed public error hierarchy with safe, redacted context rendering."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from finvizp.results import FetchResult

REDACTED = "[REDACTED]"

# Keys whose values are always considered sensitive.
_SENSITIVE_KEY = re.compile(
    r"(?i)(?:^|[_-])(?:"
    r"cookie|cookies|authorization|proxy|proxies|token|secret|password|passwd|pwd"
    r"|api[-_]?key|auth|credential|credentials|session"
    r")(?:$|[_-])"
)
_BODY_KEY = re.compile(r"(?i)(?:^|[_-])(?:body|response_body|content|html|payload)(?:$|[_-])")

# URL credential forms: scheme://user:pass@... and query secrets ?a=...&token=...
_URL_CREDENTIALS = re.compile(r"(?i)\b(https?|socks[45])://([^/\s:@]+):([^@\s/]+)@")
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:token|api[-_]?key|apikey|key|secret|password|sig|signature|session|auth)[=])"
    r"([^&\s'\"]+)"
)


@dataclass(frozen=True, slots=True)
class FetchWarning:
    """Immutable typed warning with a stable code and safe context."""

    code: str
    message: str
    symbol: str | None = None
    endpoint: str | None = None


@dataclass(frozen=True, slots=True)
class UnitError:
    """Immutable record of one value that failed typed normalization."""

    code: str
    message: str
    raw: str | None = None
    symbol: str | None = None
    field: str | None = None


def redact_value(value: Any) -> Any:
    """Recursively replace credential-bearing values, returning frozen structures."""
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _SENSITIVE_KEY.search(key_text):
                clean[key_text] = REDACTED
            elif _BODY_KEY.search(key_text):
                clean[key_text] = "[BODY REDACTED]"
            else:
                clean[key_text] = redact_value(item)
        return MappingProxyType(clean)
    if isinstance(value, (list, tuple)):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, str):
        value = _URL_CREDENTIALS.sub(
            lambda m: f"{m.group(1)}://[REDACTED]@",
            value,
        )
        value = _QUERY_SECRET.sub(lambda m: f"{m.group(1)}{REDACTED}", value)
        return value
    return value


def format_context(context: Mapping[str, Any] | None) -> str:
    """Render a mapping as a single redacted ``key=value`` line."""

    def _format(item: Any) -> str:
        if isinstance(item, Mapping):
            return "{" + ", ".join(f"{k}={_format(v)}" for k, v in item.items()) + "}"
        if isinstance(item, (list, tuple)):
            inner = ", ".join(_format(v) for v in item)
            return f"[{inner}]" if isinstance(item, list) else f"({inner})"
        return str(item)

    safe = redact_value(dict(context or {}))
    return " ".join(f"{key}={_format(value)}" for key, value in safe.items())


class FinvizError(Exception):
    """Base class for every public finvizp failure."""

    def __init__(
        self,
        message: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        self.context: Mapping[str, Any] = redact_value(dict(context or {}))
        super().__init__(message)

    def __str__(self) -> str:
        rendered = format_context(self.context)
        base = super().__str__()
        return f"{base} [{rendered}]" if rendered else base


class FinvizTransportError(FinvizError): ...


class FinvizRateLimitError(FinvizError): ...


class FinvizBlockedError(FinvizError): ...


class FinvizEntitlementError(FinvizError): ...


class FinvizNotFoundError(FinvizError, LookupError): ...


class FinvizQueryError(FinvizError, ValueError): ...


class FinvizPartialError(FinvizError):
    """Raised in strict mode when at least one unit failed; carries the partial result."""

    def __init__(self, message: str, *, partial_result: FetchResult) -> None:
        self.partial_result: FetchResult = partial_result
        super().__init__(message, context={"endpoint": partial_result.metadata.endpoint})


class FinvizBatchError(FinvizError): ...


class FinvizParseError(FinvizError): ...


class FinvizDataError(FinvizError): ...
