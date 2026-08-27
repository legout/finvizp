# Publishing finvizp

`finvizp` uses explicit version tags and PyPI trusted publishing. No long-lived
PyPI token belongs in GitHub secrets.

## Sibling-project precedent

- `fastreq` publishes from `.github/workflows/publish.yml` on `v*` tags, but its
  current workflow uses a repository `PYPI_API_TOKEN` secret.
- `yfin` has CI and documentation workflows but no checked-in PyPI publisher.
- `finvizp` keeps the explicit tag gate from `fastreq` and replaces its token
  with GitHub-to-PyPI OIDC.

## One-time GitHub setup

The repository must have an environment named `pypi`. The publish job targets
that environment. Environment protection can be strengthened with required
reviewers before the first release.

## One-time PyPI setup

Before the first release, register a pending trusted publisher at
<https://pypi.org/manage/account/publishing/> with:

| Field | Value |
|---|---|
| PyPI project name | `finvizp` |
| GitHub owner | `legout` |
| GitHub repository | `finvizp` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

PyPI account login and two-factor authentication are intentionally not automated
or stored in this repository. Registering the pending publisher reserves the
project-to-workflow trust relationship; it does not upload a release.

## Release procedure

1. Implement and review the intended release scope.
2. Update `project.version` in `pyproject.toml` and `__version__` in
   `src/finvizp/__init__.py` to the same version.
3. Update release documentation and run:

   ```bash
   uv lock --check
   uv run pytest -q
   uv run ruff format --check .
   uv run ruff check .
   uv run ty check src
   uv run python scripts/check_docs.py
   uv build
   uvx --from twine twine check dist/*
   ```

4. Merge the reviewed release commit to `main` and verify CI succeeds.
5. Create and push an annotated tag matching the package version exactly:

   ```bash
   git tag -a vX.Y.Z -m "Release X.Y.Z"
   git push origin vX.Y.Z
   ```

6. The publish workflow repeats tests and validation, exchanges GitHub's OIDC
   identity for a short-lived PyPI token, uploads the distributions, and creates
   the matching GitHub release.
7. Verify the PyPI project, wheel and source distribution metadata, clean-environment
   installation, import version, and GitHub release before announcing success.

## Safety properties

- A branch push cannot publish.
- A tag that does not match both version declarations fails before upload.
- PyPI credentials are not stored in the repository or GitHub secrets.
- Failed tests, lint, types, documentation, build, or package metadata stop the
  release.
- The workflow never uses `skip-existing`; duplicate or conflicting releases
  fail visibly.
