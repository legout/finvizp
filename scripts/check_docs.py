"""Validate local Markdown links and repository documentation hygiene."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_FILES = [*sorted(ROOT.glob("*.md")), *sorted((ROOT / "docs").rglob("*.md"))]
LINK_PATTERN = re.compile(r"\[[^]]+\]\(([^)]+)\)")
PLACEHOLDER_PATTERN = re.compile(r"\b(?:TBD|TODO|PLACEHOLDER)\b", re.IGNORECASE)
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "#")


def validate_file(path: Path) -> list[str]:
    """Return documentation errors for one Markdown file."""
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    relative = path.relative_to(ROOT)

    for line_number, line in enumerate(text.splitlines(), start=1):
        if line.rstrip() != line:
            errors.append(f"{relative}:{line_number}: trailing whitespace")

    if PLACEHOLDER_PATTERN.search(text):
        errors.append(f"{relative}: unresolved placeholder")

    for target in LINK_PATTERN.findall(text):
        if target.startswith(EXTERNAL_PREFIXES):
            continue
        local_target = target.split("#", maxsplit=1)[0]
        if local_target and not (path.parent / local_target).resolve().exists():
            errors.append(f"{relative}: broken link {target}")

    return errors


def main() -> int:
    """Validate all repository Markdown files."""
    errors = [error for path in MARKDOWN_FILES for error in validate_file(path)]
    if errors:
        print("\n".join(errors))
        return 1

    line_count = sum(len(path.read_text(encoding="utf-8").splitlines()) for path in MARKDOWN_FILES)
    print(f"Documentation check passed: {len(MARKDOWN_FILES)} files, {line_count} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
