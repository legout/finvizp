"""Fixture hygiene guard (Card 0.5-B, step 4).

Structural fixtures are hand-built scrubbed samples, but the hygiene contract
is enforced, not assumed: every committed fixture must reject cookies,
authorization/session/account data, credential-carrying proxy URLs, tracking
identifiers, oversized irrelevant content, and unscrubbed secrets. Clean
fixtures pass; a future fixture refreshed from a live capture cannot leak
these through.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
# text/JSON/HTML fixtures plus their generator scripts; generated on demand by
# the *_build.py helpers, which this scan also covers.
MAX_FIXTURE_BYTES = 256 * 1024

# Substrings that must never appear in a committed fixture (case-insensitive):
# cookies, auth headers, session/account identifiers, tracking params, secrets.
FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "set-cookie",
    "cookie:",
    "document.cookie",
    "jsessionid",
    "phpsessid",
    "cf_clearance",
    "__cf_bm",
    "authorization:",
    "bearer ",
    "x-api-key",
    "api_key=",
    "apikey=",
    "password",
    "sessionid=",
    "accountid=",
    "portfolio_id=",
    "gclid=",
    "fbclid=",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "BEGIN RSA PRIVATE KEY",
    "BEGIN OPENSSH PRIVATE KEY",
)
# Credentials in the userinfo position: scheme://user:pass@host (userinfo
# precedes any path/query separator, so the provider's ``t=@eurusd`` chart
# ticker syntax never matches).
_URL_CREDENTIALS = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/?#]+@", re.IGNORECASE)
# Proxy URLs must never appear unsanitized (credentials or raw provider config).
_PROXY_SCHEMES = re.compile(r"\b(?:https?|socks[45])://[^\s/?#'\"<>]*@", re.IGNORECASE)


def _fixture_files() -> list[Path]:
    files = [
        path
        for path in FIXTURES.rglob("*")
        if path.is_file()
        and path.name != "__init__.py"
        and not path.name.endswith(".pyc")
        and "__pycache__" not in path.parts
    ]
    assert files, "fixture tree missing"
    return files


def test_fixtures_stay_within_size_budget() -> None:
    """Oversized captures are rejected: fixtures are structural, not archives."""
    for path in _fixture_files():
        assert path.stat().st_size <= MAX_FIXTURE_BYTES, path


@pytest.mark.parametrize("path", _fixture_files(), ids=lambda p: str(p.relative_to(FIXTURES)))
def test_fixture_is_free_of_session_account_and_tracking_data(path: Path) -> None:
    text = path.read_text("utf-8", errors="ignore")
    lowered = text.lower()
    for needle in FORBIDDEN_SUBSTRINGS:
        assert needle not in lowered, f"{path.relative_to(FIXTURES)}: contains {needle!r}"
    assert not _URL_CREDENTIALS.search(text), f"{path.relative_to(FIXTURES)}: URL credentials"
    assert not _PROXY_SCHEMES.search(text), f"{path.relative_to(FIXTURES)}: unsanitized proxy URL"


def test_manifest_referenced_fixtures_exist_and_are_exercised() -> None:
    """Every manifest fixture path exists and at least one test names its file."""
    import json

    caps = json.loads(Path("src/finvizp/capabilities.json").read_text("utf-8"))
    test_corpus = "\n".join(
        path.read_text("utf-8", errors="ignore")
        for path in (Path(__file__).parent).glob("test_*.py")
    )
    for cap in caps["capabilities"]:
        fixture = cap.get("fixture")
        if not fixture:
            continue
        path = Path(fixture)
        assert path.exists(), f"{cap['id']}: missing fixture {fixture}"
        assert path.name in test_corpus, (
            f"{cap['id']}: fixture {fixture} is never exercised by any test"
        )
