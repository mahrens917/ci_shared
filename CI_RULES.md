# ci.sh Pass Rules

Everything below is enforced by `ci_tools/scripts/ci.sh` (which just runs `make check`). Treat it as the contract for automation agents and humans—violations here will fail CI before your PR lands.

---

## 1. Formatting, Naming, and Test Layout
- Python 3.10+, four-space indentation, PEP 8 naming (`snake_case` for modules/functions, `PascalCase` for classes). Keep public APIs backward compatible because consuming repos import from `ci_tools`.
- Formatters *must* be clean: `isort --profile black` then `black` over `FORMAT_TARGETS` (defaults to `src tests`).
- Test modules live in `tests/` and follow `test_<module>.py`; every pytest test function/class starts with `test_`.
- Shared pytest defaults (see `shared-tool-config.toml`): `-q --tb=short` and `PYTHONPATH=["."]`.

---

## 2. Static Analysis Pipeline (runs in this order)

| Tool | Rule Set / Important Flags |
|------|----------------------------|
| `codespell` | Skips `.git`, `artifacts`, `trash`, `models`, `logs`, `htmlcov`, `*.json`, `*.csv`; add repo-specific words to `ci_tools/config/codespell_ignore_words.txt`. |
| `vulture` | `vulture $(FORMAT_TARGETS) --min-confidence 80` → no unused code ≥80% confidence. |
| `deptry` | `deptry --config pyproject.toml .` → dependencies declared & used consistently. |
| `gitleaks` | Scans `src`, `tests`, `scripts`, `docs`, `ci_tools`, `shared-tool-config.toml`, etc. Never commit secrets—whitelist safe strings in `.gitleaks.toml` or `ci_tools/config/*`. |
| `bandit` | `python -m ci_tools.scripts.bandit_wrapper -c pyproject.toml -r src tests --exclude artifacts,trash,models,logs`; skip list in `shared-tool-config.toml`. |
| `pip-audit` | Runs outside CI_AUTOMATION: `python -m pip_audit`. |
| `ruff` | `ruff check --target-version=py310 --fix src tests` with rulesets `TRY`, `C90` (McCabe ≤10), `PLR` (refactor hints). |
| `pyright` | `pyright --warnings src` — treat warnings as failures. |
| `pylint` | `pylint -j 7 src` plus any repo-specific arguments. Max args 7, branches 10, statements 50 (from Ruff’s Pylint profile). |

---

## 3. Testing & Coverage
- Command: `pytest -n 7 tests/ --cov=ci_tools --cov-fail-under=80` (threshold pulled from `ci_shared.mk`).
- `python -m ci_tools.scripts.coverage_guard --threshold 80 --data-file .coverage` ensures the recorded coverage meets the same floor.
- `python -m compileall src tests` runs last to catch syntax errors without executing code.

---

## 4. Guard Thresholds You Must Respect

| Guard | What It Enforces | ci.sh Threshold / Flags |
|-------|------------------|-------------------------|
| `policy_guard` | See §5 for the full policy list (keywords, TODOs, exception rules, etc.). | defaults |
| `data_guard` | Blocks literal thresholds/datasets (details in §6). | defaults + `config/data_guard_allowlist.json` |
| `structure_guard` | Each class ≤ **100** lines. | `--root src` |
| `complexity_guard` | Per-function cyclomatic ≤ **10**, cognitive ≤ **15**. | `--root src --max-cyclomatic 10 --max-cognitive 15` |
| `module_guard` | Python modules ≤ **400** significant lines. | `--max-module-lines 400` |
| `function_size_guard` | Functions in `src` ≤ **80** lines (stricter than the policy guard’s legacy 150-line limit). | `--max-function-lines 80` |
| `inheritance_guard` | Inheritance depth (class → parent → grandparent) ≤ **2** real ancestors. | `--max-depth 2` |
| `method_count_guard` | Per class: ≤ **15** public methods, ≤ **25** total methods. | defaults |
| `dependency_guard` | `__init__`/`__post_init__` may instantiate ≤ **5** new objects (constructor calls with capitalized names). | `--max-instantiations 5` |
| `unused_module_guard` | No orphan modules and, in `--strict` mode, no suspicious duplicates. Patterns like `_refactored`, `_slim`, `_optimized`, `_old`, `_backup`, `_copy`, `_new`, `_temp`, `_v2`, `_2` fail unless removed. | `--strict` |
| `documentation_guard` | README.md hygiene (see §7). | defaults |

If you exceed any limit, refactor before running CI; suppressions are not allowed.

---

## 5. Policy Guard Rules (ci_tools/scripts/policy_guard.py)

### Banned Keywords & Tokens
- Keywords (`BANNED_KEYWORDS`): `legacy`, `fallback`, `default`, `catch_all`, `failover`, `backup`, `compat`, `backwards`, `deprecated`, `legacy_mode`, `old_api`, `legacy_flag`.
- Flagged tokens anywhere in source/comments: `TODO`, `FIXME`, `HACK`, `WORKAROUND`, `LEGACY`, `DEPRECATED`.
- Suppressions: `# noqa` and `pylint: disable` are prohibited unless the guard-specific suppression tokens are used (only `policy_guard: allow-broad-except` and `policy_guard: allow-silent-handler` exist).

### Exception Handling
- No bare `except:` or handlers catching `Exception`/`BaseException` unless explicitly suppressed.
- Handlers must re-raise; the guard flags empty bodies, `pass/continue/break`, literal `return`, or “log-and-suppress” patterns.
- Raising `Exception`/`BaseException` (directly or via constructor) is blocked.

### Function Length & Duplicates
- Legacy policy still checks for functions ≥150 lines; `function_size_guard` will trip first at 80.
- Duplicate functions (structurally identical, ≥6 lines, across different files) fail the build.

### Literal Fallbacks & Defaults
- Disallowed: literal defaults in `.get()`, `.setdefault()`, `getattr`, `os.getenv`/`os.environ.get`, or boolean/ternary fallbacks like `value or "fallback"` when the fallback is a literal.
- Returning literals from `if x is None` guards is forbidden—return something computed instead.

### Forbidden Sync Calls
- The following synchronous calls are banned inside `src`: `time.sleep`, `subprocess.run/call/check_call/check_output`, and every variant of `requests.*` (`get`, `post`, `put`, `delete`, `request`).

### Legacy Code Indicators
- File paths or directories containing `_legacy`, `_compat`, `_deprecated`, `legacy/`, `compat/`, etc., fail (`collect_legacy_modules`).
- Config files (`config/*.json|.toml|.yaml|.yml|.ini`) cannot contain tokens like `legacy`, `compat`, `deprecated`, `legacy_mode`, `old_api`, `legacy_flag`.
- Guard also detects backward-compatibility blocks (legacy feature toggles) via AST heuristics.

### TODO Hygiene & Bytecode
- Any `.pyc` or `__pycache__` files cause a failure (the guard purges them automatically but treats persistent ones as violations).
- TODO/FIXME/HACK/WORKAROUND strings are forbidden—convert them into issues and link those instead.

---

## 6. Data Guard Rules (ci_tools/scripts/data_guard.py)

| Pattern | Why It Fails | Allowlist Hook |
|---------|--------------|----------------|
| Assigning numeric literals (anything except -1, 0, 1) to variables whose names include `threshold`, `limit`, `timeout`, `default`, `max`, `min`, `retry`, `window`, `size`, `count` | Hard-coded thresholds must be configurable. | `config/data_guard_allowlist.json["assignments"]` |
| Comparing those sensitive names directly to numeric literals | Same reason as assignments (no inline magic numbers). | `["comparisons"]` allowlist |
| Creating pandas/numpy data structures (`pd.DataFrame`, `pandas.DataFrame`, `np.array`, `numpy.asarray`, etc.) with literal datasets | Forces fixtures/config-driven data. | `["dataframe"]` allowlist |

Variables written entirely in UPPER_SNAKE_CASE (constants) are exempt. Keep the allowlist small, reviewed, and committed alongside changes.

---

## 7. Documentation Guard Requirements
- Always keep: `README.md`. Once added, `CLAUDE.md` is mandatory forever.
- If `docs/` exists, `docs/README.md` must exist.
- Every top-level `src/<package>/` that contains `.py` files needs its own `src/<package>/README.md`.
- If the following directories exist, they must contain READMEs:
  - `docs/architecture/` → `docs/architecture/README.md` (required when any `.md` lives there)
  - Every `docs/domains/*/`, `docs/reference/*/`, and `docs/operations/`

---

## 8. Miscellaneous CI Expectations
- **Secrets**: `gitleaks` scans `src`, `tests`, `scripts`, `docs`, `ci_tools`, `ci_tools_proxy`, `shared-tool-config.toml`, `pyproject.toml`, `Makefile`, `README.md`, `SECURITY.md`, etc. Do not add secrets—extend `.gitleaks.toml` or the shared ignore list if a string is safe.
- **Cleanup**: The Makefile deletes all `*.pyc` and `__pycache__` under `src`, `tests`, `scripts`, `docs`, `ci_tools`, `ci_tools_proxy` every run—don’t rely on bytecode artifacts.
- **Safety Net**: `python -m ci_tools.scripts.policy_guard`, `data_guard`, `structure_guard`, `complexity_guard`, `module_guard`, `function_size_guard`, `inheritance_guard`, `method_count_guard`, `dependency_guard`, `unused_module_guard --strict`, and `documentation_guard` all run *before* any tests—fix guard failures first to save time.
- **Automation Context**: Only `ci_shared.config.json` may contain repo metadata for agents; never duplicate secrets/protected paths elsewhere.

