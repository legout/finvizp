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

# Keys whose values are always considered sensitive. Canonical and camelCase forms.
_SENSITIVE_KEY = re.compile(
    r"(?i)(?:^|[_-])(?:"
    r"cookie|cookies|authorization|proxy|proxies|token|secret|password|passwd|pwd"
    r"|api[-_]?key|auth|credential|credentials|session"
    r")(?:$|[_-])"
)
_BODY_KEY = re.compile(r"(?i)(?:^|[_-])(?:body|response_body|content|html|payload)(?:$|[_-])")

# URL credential forms: scheme://user:pass@...
_URL_CREDENTIALS = re.compile(r"(?i)\b(https?|socks[45])://([^/\s:@]+):([^@\s/]+)@")
# Label-driven secret markers: a "label:" or "label=" whose name contains a
# credential/body/proxy word. Value is consumed in full (quoted or bare) —
# covers Authorization/X-Api-Key/X-Auth-Token/Set-Cookie headers with scheme
# words ("Bearer x", "Basic x"), response_body=..., HTTP_PROXY=...,
# proxy-url: ..., access_token=... in query strings, etc.
_SECRET_LABEL = re.compile(
    r"(?i)((?:^|[\s,{\"'=\[]|[?&])[a-z0-9_.-]*(?:"
    r"authorization|auth[-_]?token|api[-_]?key|apikey|access[-_]?token|credential"
    r"|cookie|session|password|passwd|pwd|secret|token"
    r"|body|payload|content|html"
    r")[a-z0-9_.-]*\s*\"?\s*[:=]\s*)"
    r"(?:(?:bearer|basic|digest|token)\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s&;,\"']+)"
)
# Proxy URLs are route-sensitive: consume the whole URL (host included) when
# the surrounding text names it as a proxy/route ("via proxy http://...",
# "proxy URL: http://...", "proxy-url: socks5://...", "HTTP_PROXY=...",
# '"proxy_url": "http://..."'). The optional quote handles JSON keys/values.
_PROXY_URL = re.compile(
    r"(?i)((?:^|[\s,{\"'=(\[])[a-z0-9_.-]*(?:proxy|proxies|route)[a-z0-9_.-]*\s*"
    r"(?:\s+[a-z0-9_.-]{1,10})?\s*\"?\s*[:=]?\s*\"?\s*)"
    r"((?:https?|socks[45])://[^\s'\"<>]+)"
)


def _redact_text(text: str) -> str:
    """Redact all credential/proxy/body markers inside one string."""
    text = _URL_CREDENTIALS.sub(lambda m: f"{m.group(1)}://[REDACTED]@", text)
    text = _SECRET_LABEL.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    return _PROXY_URL.sub(lambda m: f"{m.group(1)}{REDACTED}", text)


@dataclass(frozen=True, slots=True)
class FetchWarning:
    """Immutable typed warning with a stable code and safe context."""

    code: str
    message: str
    symbol: str | None = None
    endpoint: str | None = None

    def __post_init__(self) -> None:
        # Public records must never carry credentials/proxy/response text.
        for name in ("code", "message", "symbol", "endpoint"):
            value = getattr(self, name)
            if isinstance(value, str):
                object.__setattr__(self, name, redact_value(value))


@dataclass(frozen=True, slots=True)
class UnitError:
    """Immutable record of one value that failed typed normalization."""

    code: str
    message: str
    raw: str | None = None
    symbol: str | None = None
    field: str | None = None

    def __post_init__(self) -> None:
        # ``raw`` is hostile provider input; sanitize every string field.
        for name in ("code", "message", "raw", "symbol", "field"):
            value = getattr(self, name)
            if isinstance(value, str):
                object.__setattr__(self, name, redact_value(value))


def redact_value(value: Any) -> Any:
    """Recursively replace credential-bearing values, returning frozen structures."""
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            # camelCase ("responseBody", "accessToken") acts as a separator boundary for matching.
            match_text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key_text)
            if _SENSITIVE_KEY.search(match_text):
                clean[key_text] = REDACTED
            elif _BODY_KEY.search(match_text):
                clean[key_text] = "[BODY REDACTED]"
            else:
                clean[key_text] = redact_value(item)
        return MappingProxyType(clean)
    if isinstance(value, (list, tuple)):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(redact_value(item) for item in value)
    if isinstance(value, str):
        return _redact_text(value)
    return value


def format_context(context: Mapping[str, Any] | None) -> str:
    """Render a mapping as a single redacted ``key=value`` line."""

    def _format(item: Any) -> str:
        if isinstance(item, Mapping):
            return "{" + ", ".join(f"{k}={_format(v)}" for k, v in item.items()) + "}"
        if isinstance(item, (list, tuple)):
            inner = ", ".join(_format(v) for v in item)
            return f"[{inner}]" if isinstance(item, list) else f"({inner})"
        if isinstance(item, (set, frozenset)):
            return "{" + ", ".join(sorted(_format(v) for v in item)) + "}"
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
        super().__init__(redact_value(str(message)))

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
