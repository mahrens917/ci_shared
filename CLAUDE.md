# ci_shared: Claude Guide

Shared CI toolkit providing guards, linters, and LLM-powered auto-fix loops for all repos (api, zeus, monitor, aws, common, peak, cfb, deribit, kalshi, tracker, weather, pdf). Code in `ci_tools/`, helpers in `scripts/`, configs in `ci_shared.mk` + `shared-tool-config.toml`, tests in `tests/`, docs in `docs/`.

## Quick Commands
- `make check` → runs `ci_tools/scripts/ci.sh` (syncs tool configs, ensures deps, then executes the guard + test pipeline).
- `python -m ci_tools.ci --model gpt-5-codex` → automation loop (override with `--command "<cmd>"` if needed).
- `python scripts/sync_project_configs.py <consumer...>` → push updated shared assets into consuming repos.

## Code Hygiene
- Avoid adding fallbacks, duplicate code, or backward-compatibility shims (backward compatibility is not required); call out and fix fail-fast gaps or dead code when encountered.
- Prefer config JSON files over new environment variables; only introduce ENV when necessary and document it.

## Duplicate Code Rule
- Search `ci_tools/` before adding helpers (`rg "def <name>" ci_tools`). Centralize shared logic in `ci_tools/utils/` or `ci_tools/common/`, update callers to import it, and document the delegation.

## CI Pipeline (exact order)
- `codespell` → `vulture` → `deptry` → `gitleaks` → `bandit_wrapper` → `pip-audit` (skipped with `CI_AUTOMATION`) → `ruff --fix` → `pyright --warnings` → `pylint` → `pytest` → `coverage_guard` → `compileall`.
- Limits: classes ≤100 lines; functions ≤80; modules ≤400; cyclomatic ≤10 / cognitive ≤15; inheritance depth ≤2; ≤15 public / 25 total methods; ≤5 instantiations in `__init__`/`__post_init__`; `unused_module_guard --strict`; documentation guard requires README/CLAUDE/docs hierarchy.
- Policy guard reminders: banned tokens (`legacy`, `fallback`, `default`, `catch_all`, `failover`, `backup`, `compat`, `backwards`, `deprecated`, `legacy_mode`, `old_api`, `legacy_flag`, TODO/FIXME/HACK/WORKAROUND), no broad/empty exception handlers, no literal fallbacks in `.get`/`setdefault`/ternaries/`os.getenv`/`if x is None`, and no `time.sleep`/`subprocess.*`/`requests.*` inside `ci_tools`.
- Prep: `tool_config_guard --sync` runs first; PYTHONPATH includes `ci_shared`; packaging shim activates if `packaging` is missing.

## Do/Don't
- Do fix the code—never bypass checks (`# noqa`, `# pylint: disable`, `# type: ignore`, `policy_guard: allow-*`, threshold changes are off-limits).
- Do keep secrets and generated artifacts out of git; use `.gitleaks.toml`/`ci_tools/config/*` for sanctioned patterns.
- Do keep required docs current (`README.md`, `CLAUDE.md`, `docs/README.md`, per-package READMEs) and avoid undoing user edits.
