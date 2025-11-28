# ci_shared: Working Rules

- Purpose: canonical CI/guard toolkit consumed by Zeus/Kalshi/API/AWS. Code in `ci_tools/`, repo-specific helpers in `scripts/`, shared configs in `ci_shared.mk` and `shared-tool-config.toml`, tests in `tests/`, docs in `docs/`.
- Install: `python -m pip install -e .` so guard scripts and automation are importable.

## Core Commands
- `make check` runs `ci_tools/scripts/ci.sh`, which syncs tool configs, ensures test deps, then executes the guard + test pipeline.
- `python -m ci_tools.ci --model gpt-5-codex` runs the automation loop (use `--command "<cmd>"` to override CI).
- `python scripts/sync_project_configs.py <consumer...>` pushes updated shared assets into downstream repos.

## Code Hygiene
- Avoid introducing fallbacks, duplicate code, backward-compat breaks, fail-fast violations, or dead code; when you spot existing issues, call them out and fix them.
- Prefer config JSON files over new environment variables; only use ENV when absolutely required and document it.

## Duplicate Code Policy
- Search `ci_tools/` before adding helpers (`rg "def <name>" ci_tools`). Consolidate shared logic into `ci_tools/utils/` or `ci_tools/common/`, update callers to import the canonical function, and document the delegation.

## CI Contract (from `ci_tools/scripts/ci.sh`)
- Order: `codespell` → `vulture` → `deptry` → `gitleaks` → `bandit_wrapper` → `safety scan` (skipped with `CI_AUTOMATION`) → `ruff --fix` → `pyright --warnings` → `pylint` → `pytest` → `coverage_guard` → `compileall`.
- Limits: classes ≤100 lines; functions ≤80; modules ≤400; cyclomatic ≤10 / cognitive ≤15; inheritance depth ≤2; ≤15 public / 25 total methods; ≤5 instantiations in `__init__`/`__post_init__`; `unused_module_guard --strict`; documentation guard requires README/CLAUDE/docs hierarchy.
- Policy guard: bans `legacy`, `fallback`, `default`, `catch_all`, `failover`, `backup`, `compat`, `backwards`, `deprecated`, `legacy_mode`, `old_api`, `legacy_flag`, TODO/FIXME/HACK/WORKAROUND; no broad/empty exception handlers; no literal fallbacks in `.get`/`setdefault`/ternaries/`os.getenv`/`if x is None`; blocks `time.sleep`, `subprocess.*`, and `requests.*` inside `ci_tools`.
- Prep: `tool_config_guard --sync` runs first; PYTHONPATH includes `ci_shared`; packaging shim activates if `packaging` is missing.

## Non-Negotiables
- Fix root causes—never bypass checks (`# noqa`, `# pylint: disable`, `# type: ignore`, `policy_guard: allow-*`, or relaxed thresholds are prohibited).
- Keep secrets and generated artifacts out of the repo; use `.gitleaks.toml`/`ci_tools/config/*` for sanctioned patterns.
- Maintain required docs (`README.md`, `CLAUDE.md`, `docs/README.md`, per-package READMEs) and avoid reverting user edits you didn’t make.
