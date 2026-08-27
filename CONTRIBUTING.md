# Contributing

`finvizp` is currently in its design and pre-implementation phase. Changes
should preserve the approved foundation specification and the frozen public
surface inventory.

## Development setup

```bash
uv sync --all-groups
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run ty check src
uv run python scripts/check_docs.py
uv build
```

## Expectations

- Use test-driven changes for implementation work.
- Keep endpoint parsing pure and separate from transport and Arrow normalization.
- Prefer same-tier complete first-party structured responses, but do not replace
  coherent public HTML with incomplete or Elite-only JSON.
- Keep live tests bounded and opt-in.
- Never commit cookies, credentials, proxy URLs, authenticated bodies, raw HAR
  files, or unsanitized provider fixtures.
- Update the capability manifest, schema registry, documentation, and tests
  together when adding a public capability.

Implementation work should be based on a reviewed plan and submitted through a
focused pull request.
