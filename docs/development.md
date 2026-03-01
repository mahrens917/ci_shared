# Development Guide

This guide covers local development workflows, recommended tooling, and release
considerations for maintainers of claude-ci-tools.

## Repository Layout
- `ci_tools/` – Python package containing the automation loop, guard modules, and vendor shims
- `scripts/` – auxiliary guards that operate outside the package namespace
- `ci_shared.mk` – reusable Makefile fragment bundling the guard pipeline
- `config/` – shared configuration (e.g., codespell ignore list)
- `docs/` – human-facing documentation (you are here)

## Local Tooling
Install development dependencies in a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .[dev]
```

The `dev` extras currently include Jinja2 for templated guard output. Additional
utilities (radon, coverage, pytest) should be installed to exercise the guards:

```bash
python -m pip install radon pytest pytest-cov coverage pyright pylint ruff codespell deptry vulture
```

## Running Checks
- `make shared-checks` – executes the full guard and lint pipeline
- `python -m ci_tools.ci --dry-run --command "pytest"` – smoke-test the CLI loop
- `python -m pytest` – run unit tests (add tests under `tests/` in consuming repos)
- `python scripts/complexity_guard.py --root src` – spot-check complexity limits

## Coding Standards
- Python 3.10+ typing (use `list[str]` style annotations, avoid legacy `List`)
- Black + isort formatting; Ruff and pylint in the pipeline
- Keep guard scripts idempotent and deterministic; avoid non-hermetic filesystem deps
- Prefer ASCII-only source unless existing files rely on Unicode
- Document complex sections with succinct comments when intent is non-obvious

## Packaging and Release
1. Update version in `pyproject.toml`
2. Regenerate `claude_ci_tools.egg-info` via `python -m build` if packaging locally
3. Tag releases with `vX.Y.Z` and publish to the internal package index as needed

## Working with Vendored Dependencies
`ci_tools/vendor/` contains a lightweight `packaging` shim used when
`packaging` is absent in automation environments. Ensure the shim stays in sync
with the upstream API surface relied upon by the guards. When adding new
dependencies, prefer vendoring to guarantee deterministic behavior.

## Security Practices
- Review [`SECURITY.md`](../SECURITY.md) before pushing to avoid leaking secrets
- Ensure guards that parse repository files handle untrusted content defensively
- Prefer pathlib and safe file operations over shell pipelines inside guards

## Documentation Updates
- Add or modify guides in `docs/`; `documentation_guard.py` will enforce required files
- Keep `README.md` concise and link to detailed docs from there
- When introducing new guards or automation features, update:
  - [`docs/guard-suite.md`](guard-suite.md)
  - [`docs/automation.md`](automation.md)
  - Inline module docstrings for quick discoverability
