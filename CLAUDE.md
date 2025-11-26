# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is `codex-ci-tools`, a shared continuous-integration toolkit used by the Zeus and Kalshi repositories. The package bundles the Codex automation workflow (`ci_tools`) for automated CI repair loops.

## Installation & Setup

Install the package in editable mode from the consuming repository root:

```bash
python -m pip install -e ../ci_shared
```

This places the shared scripts on `PYTHONPATH`.

## Key Commands

### Running CI Automation

**Python interface:**
```bash
python -m ci_tools.ci --model gpt-5-codex --reasoning-effort high
```

This interface:
- Runs the CI command (defaults to `scripts/ci.sh` or `./ci.sh`)
- On failure, sends logs to Codex for a patch suggestion
- Applies patches and loops until CI passes or max iterations reached
- Generates commit messages when CI succeeds

**Common options:**
- `--command <cmd>`: Custom CI command (default: `./scripts/ci.sh`)
- `--max-iterations <n>`: Max fix attempts (default: 5)
- `--patch-approval-mode {prompt,auto}`: Control patch approval (default: prompt)
- `--dry-run`: Run CI once without invoking Codex
- `--auto-stage`: Run `git add -A` after CI passes
- `--commit-message`: Request commit message from Codex

### Running Individual Guard Scripts

Each guard script can be invoked directly:

```bash
python -m ci_tools.scripts.policy_guard --root src
python -m ci_tools.scripts.module_guard --root src --max-module-lines 600
python -m ci_tools.scripts.function_size_guard --root src --max-function-lines 150
python -m ci_tools.scripts.structure_guard --root src
python -m ci_tools.scripts.coverage_guard --min-coverage 80
python -m ci_tools.scripts.dependency_guard --root src
```

## Code Duplication Policy - CRITICAL

**ALWAYS search for existing implementations before creating new functions.**

### Before Writing Any New Function

1. **Search the codebase first**:
   ```bash
   # Search for similar function names
   grep -r "def function_name" src/

   # Search for similar functionality by keyword
   grep -r "keyword" src/ | grep "def "
   ```

2. **Check `src/` for common utilities**: Look for existing implementations before creating new functions

3. **Use the exploration agent**: When unsure if functionality exists:
   ```
   "Search the codebase for functions that process X"
   "Find all implementations of Y"
   ```

### When You Find Duplicate Functions

**Consolidate them immediately.** Do NOT add another duplicate.

1. Identify the most complete/tested implementation
2. Move it to an appropriate shared location if needed (e.g., `src/common/`, `src/utils/`)
3. Update duplicates to delegate to the canonical version
4. Add clear documentation about the delegation
5. Test that behavior is preserved

Example consolidation:
```python
# BEFORE: Duplicate implementation
def process_data(data):
    return data.strip().lower()

# AFTER: Delegate to canonical
from src.utils.string_utils import normalize_string

def process_data(data):
    """Delegates to canonical implementation in src.utils.string_utils."""
    return normalize_string(data)
```

### Leverage Shared Utilities

- **DO**: Create reusable utilities for common operations
- **DO**: Put shared functions in appropriate modules (`src/common/`, `src/utils/`, etc.)
- **DO**: Document and test shared utilities thoroughly
- **DON'T**: Duplicate logic across modules
- **DON'T**: Create module-specific versions of common utilities

### Why This Matters

Duplicate functions cause:
- **Behavioral drift**: Different parts of code using slightly different logic
- **Bug multiplication**: Same bug must be fixed in multiple places
- **Maintenance burden**: Changes must be made in multiple locations
- **Testing complexity**: Same logic tested multiple times
- **Code bloat**: Unnecessary increase in codebase size

**Keep it DRY (Don't Repeat Yourself).**

## Architecture

### Core Modules

**`ci_tools/ci.py`** (1400+ lines)
- Main automation loop that orchestrates CI fixes
- Calls CI command, captures failures, and requests patches from Codex
- Implements safety guards (risky pattern detection, protected paths)
- Handles coverage deficit detection and targeted file diffs
- Model requirement: `gpt-5-codex` with configurable reasoning effort

Key workflow stages:
1. **Preflight**: Validate model, reasoning effort, repository state
2. **Iteration loop**: Run CI → capture failure → request patch → apply → retry
3. **Coverage handling**: Special logic for coverage deficits below threshold
4. **Commit phase**: Auto-stage changes and generate commit messages

**`ci_tools/scripts/ci.sh`** (shared CI script)
- Primary CI entry point used by consuming repositories (Zeus, Kalshi) and ci_shared itself
- Ensures test dependencies are installed (pytest-cov, ruff, codespell, etc.)
- Runs `make check` to execute all guards
- In non-automation mode: stages changes, requests commit message from Codex, commits and pushes
- In CI_AUTOMATION mode: runs checks only, skips git operations

**`scripts/ci.sh`** (local wrapper)
- Thin delegation wrapper that invokes `ci_tools/scripts/ci.sh`
- Ensures ci_shared uses the exact same CI flow it provides to consuming repositories
- Supports dogfooding: we test the actual script that Zeus/Kalshi use

### Guard Scripts

The toolkit includes specialized guard scripts that enforce code quality policies:

**Code Quality Guards:**
- **`policy_guard.py`**: Enforces code policies (banned keywords, oversized functions, fail-fast violations, broad exception handlers)
- **`module_guard.py`**: Detects oversized Python modules that need refactoring (default: 600 lines)
- **`function_size_guard.py`**: Detects oversized functions (default: 150 lines)
- **`structure_guard.py`**: Enforces structural constraints (directory depth, class structure)
- **`coverage_guard.py`**: Ensures test coverage meets threshold (default: 80%)
- **`dependency_guard.py`**: Validates dependency usage and imports
- **`method_count_guard.py`**: Limits methods per class
- **`inheritance_guard.py`**: Enforces inheritance depth limits
- **`data_guard.py`**: Validates data handling patterns
- **`documentation_guard.py`**: Ensures documentation standards
- **`complexity_guard.py`**: Enforces cyclomatic and cognitive complexity limits
- **`tool_config_guard.py`**: Validates tool configurations match shared standard across repositories

**Security Guards:**
- **`gitleaks`**: Scans for hardcoded secrets (API keys, tokens, passwords) - external Go binary
- **`bandit`**: Python security linter (SQL injection, shell injection, etc.)
- **`safety`**: Dependency vulnerability scanner (checks PyPI against CVE database)

## CI Rules & Guard Contract
`ci_tools/scripts/ci.sh` (invoked by `make check`) is the canonical CI entry point. It executes the steps below in order; Claude must never propose patches that attempt to skip, suppress, or reorder them.

### Formatting, Naming, and Layout
- Python 3.10+ only, four-space indentation, `snake_case` for modules/functions, `PascalCase` for classes; public APIs must remain backward compatible for downstream consumers.
- Keep `FORMAT_TARGETS=ci_tools scripts`; always run `isort --profile black $(FORMAT_TARGETS)` and `black $(FORMAT_TARGETS)` (tests are pulled in via the Makefile targets).
- Tests belong under `tests/test_<module>.py` with `test_` prefixes. Shared pytest defaults (`-q --tb=short PYTHONPATH=["."]`) are defined in `shared-tool-config.toml`.

### Static Analysis Pipeline (strict order)
1. `codespell` skipping `.git`, `artifacts`, `trash`, `models`, `logs`, `htmlcov`, `*.json`, `*.csv`; extend `ci_tools/config/codespell_ignore_words.txt` for repo-specific vocabulary.
2. `vulture $(FORMAT_TARGETS) --min-confidence 80`.
3. `deptry --config pyproject.toml .`.
4. `gitleaks` scans `ci_tools`, `ci_tools_proxy`, `scripts`, `tests`, `docs`, `shared-tool-config.toml`, `pyproject.toml`, `Makefile`, `README.md`, `SECURITY.md`, etc.
5. `python -m ci_tools.scripts.bandit_wrapper -c pyproject.toml -r $(FORMAT_TARGETS) -q --exclude $(BANDIT_EXCLUDE)`.
6. `python -m safety scan --json --cache tail` (automation skips it; local development must run it).
7. `ruff check --target-version=py310 --fix $(FORMAT_TARGETS) tests` (TRY, C90, PLR rule sets).
8. `pyright --warnings ci_tools`.
9. `pylint -j 7 ci_tools` using Ruff’s strict profile (max args 7, branches 10, statements 50).

### Tests, Coverage, and Bytecode
- `pytest -n 7 tests/ --cov=ci_tools --cov-fail-under=80` with fixtures from `tests/conftest.py`.
- `python -m ci_tools.scripts.coverage_guard --threshold 80 --data-file .coverage` enforces the same coverage floor.
- `python -m compileall ci_tools tests scripts` runs last to surface syntax errors without executing business logic.

### Guard Thresholds
- `structure_guard --root ci_tools`: classes ≤100 LOC.
- `complexity_guard --root ci_tools --max-cyclomatic 10 --max-cognitive 15`.
- `module_guard --root ci_tools --max-module-lines 400`.
- `function_size_guard --root ci_tools --max-function-lines 80`.
- `inheritance_guard --max-depth 2`; `method_count_guard` (≤15 public / ≤25 total methods).
- `dependency_guard --max-instantiations 5` inside `__init__` / `__post_init__`.
- `unused_module_guard --strict` blocks orphans and suspicious suffixes (`_refactored`, `_slim`, `_optimized`, `_old`, `_backup`, `_copy`, `_new`, `_temp`, `_v2`, `_2`).
- `policy_guard`, `data_guard`, and `documentation_guard` use the defaults described in `ci_tools/config/*`; suppressions beyond the guard-specific tokens do not exist.

### Policy Guard Hot Buttons
- Banned keywords/tokens: `legacy`, `fallback`, `default`, `catch_all`, `failover`, `backup`, `compat`, `backwards`, `deprecated`, `legacy_mode`, `old_api`, `legacy_flag`, plus TODO/FIXME/HACK/WORKAROUND/LEGACY/DEPRECATED anywhere in source or comments.
- Only two suppression tokens exist: `policy_guard: allow-broad-except` and `policy_guard: allow-silent-handler`. `# noqa` and `pylint: disable` are rejected.
- Exception handling must not use bare `except` or catch `Exception/BaseException`; handlers must re-raise and cannot log-and-suppress. Raising `Exception/BaseException` is also forbidden.
- Literal fallbacks/defaults are banned in `.get`, `.setdefault`, `getattr`, `os.getenv`, ternaries, or `if x is None` blocks when the fallback is a literal. Inside `ci_tools`, synchronous calls (`time.sleep`, `subprocess.run/call/check_call/check_output`, `requests.*`) are blocked entirely.
- Functions ≥80 lines fail `function_size_guard`. The policy guard still checks for ≥150-line functions and duplicate ≥6-line functions. `.pyc` / `__pycache__` artifacts and directories/files named `_legacy`, `_compat`, `_deprecated`, etc., also fail.

### Data Guard Expectations
- Assigning or comparing numeric literals (except -1/0/1) to variables whose names include `threshold`, `limit`, `timeout`, `default`, `max`, `min`, `retry`, `window`, `size`, `count`, etc., violates the guard unless allowlisted via `config/data_guard_allowlist.json`.
- Creating pandas/numpy objects with literal datasets is blocked unless allowlisted under `["dataframe"]`. Only UPPER_SNAKE_CASE constants are implicitly exempt.

### Documentation Guard Requirements
- `README.md` and `CLAUDE.md` must always exist; `docs/README.md` is required because `docs/` exists.
- Packages containing `.py` files (e.g., `ci_tools/`, `ci_tools_proxy/`) each need their own `README.md`.
- Additional required READMEs: `docs/architecture/`, every `docs/domains/*/`, `docs/reference/*/`, and `docs/operations/` directories once they contain Markdown.

### Miscellaneous Enforcement
- `gitleaks` rejects secrets; whitelist safe tokens through `.gitleaks.toml` or the shared config files.
- The Makefile wipes `*.pyc` and `__pycache__` under `ci_tools`, `ci_tools_proxy`, `scripts`, `docs`, and `tests` every run—never rely on bytecode.
- Guards (`policy_guard`, `data_guard`, `structure_guard`, `complexity_guard`, `module_guard`, `function_size_guard`, `inheritance_guard`, `method_count_guard`, `dependency_guard`, `unused_module_guard --strict`, `documentation_guard`) run *before* pytest; fix guard failures first.
- Repository and automation metadata belongs solely in `ci_shared.config.json`; never clone secrets or protected paths elsewhere.

### Configuration

**Repository-Specific Configuration**

Repository-specific configuration is supplied via `ci_shared.config.json` at the repository root:

```json
{
  "repo_context": "Custom repository description...",
  "protected_path_prefixes": ["ci.py", "ci_tools/", "scripts/ci.sh", "Makefile"],
  "coverage_threshold": 80.0
}
```

**Shared Tool Configuration**

To ensure consistent CI behavior across all repositories (zeus, kalshi, aws, ci_shared), tool configurations (ruff, bandit, pytest, coverage, etc.) are standardized in `shared-tool-config.toml`.

**For consuming repositories (zeus, kalshi, aws):**

1. **Initial setup**: Copy tool configurations from `shared-tool-config.toml` to your repository's `pyproject.toml`
   ```bash
   # From your repository root
   python -m ci_tools.scripts.tool_config_guard --sync
   ```

2. **Validation**: Check if your tool configs match the shared standard
   ```bash
   python -m ci_tools.scripts.tool_config_guard
   ```

3. **Keep in sync**: When `shared-tool-config.toml` is updated, run the sync command again

**Important notes:**
- Only `[tool.*]` sections are standardized (ruff, bandit, pytest, coverage, deptry)
- Project metadata (`[project]`, `[build-system]`) remains repository-specific
- Dependencies remain repository-specific
- The guard validates consistency but requires manual copying to avoid file corruption risks

### Vendored Dependencies

The package includes a lightweight `packaging` shim under `ci_tools/vendor/` to avoid external dependencies. This provides version parsing and specifier utilities for guard scripts.

## Key Design Principles

1. **Protected Infrastructure**: Patches cannot modify CI tooling itself (`ci_tools/`, `scripts/ci.sh`, `Makefile`, `ci.py`)
2. **No Workarounds**: The automation refuses to add baseline files, exemption comments (`# noqa`, `policy_guard: allow-*`), or `--exclude` arguments
3. **Fix Code, Not Tests**: The workflow is designed to fix underlying code issues, not bypass quality checks
4. **Safety Guards**: Multiple heuristics prevent dangerous patches (risky patterns, protected paths, line count limits)
5. **Model Enforcement**: Requires `gpt-5-codex` with configurable reasoning effort (low/medium/high)

## CI Pipeline Compliance - CRITICAL

When CI checks fail, you MUST fix the underlying code issues. This is non-negotiable.

**NEVER:**
- Add ignore statements (`# noqa`, `# pylint: disable`, `# type: ignore`, etc.)
- Add suppression comments (`policy_guard: allow-*`, etc.)
- Modify CI pipeline configuration to skip or weaken tests
- Modify guard scripts to relax thresholds
- Add entries to ignore lists or allowlists to bypass checks
- Disable or comment out failing tests

**ALWAYS:**
- Change the code to fix the actual issue
- Refactor to meet complexity/size limits
- Fix type errors by correcting types
- Resolve linting issues by improving code quality
- Ask the user for guidance if the fix approach is unclear

**Examples:**
- ❌ `# pylint: disable=too-many-arguments` → ✅ Refactor to use a config object
- ❌ `# type: ignore` → ✅ Fix the type annotation
- ❌ Adding to `.gitleaks.toml` → ✅ Remove the hardcoded credential
- ❌ `policy_guard: allow-broad-except` → ✅ Catch specific exceptions
- ❌ Lowering coverage threshold → ✅ Write tests to increase coverage

If you're unsure how to fix a CI failure, ask the user for direction before proceeding.

## Error Handling

The `ci.py` module defines a hierarchy of typed exceptions:

- **`CiError`**: Base class for CI automation runtime failures
  - `CodexCliError`: Codex CLI invocation failures
  - `CommitMessageError`: Empty commit message responses
  - `PatchApplyError`: Patch application failures (retryable vs non-retryable)

- **`CiAbort`**: Base class for deliberate workflow exits
  - `GitCommandAbort`: Git operation failures
  - `RepositoryStateAbort`: Invalid repository state (e.g., detached HEAD)
  - `ModelSelectionAbort`: Unsupported model configuration
  - `ReasoningEffortAbort`: Invalid reasoning effort value
  - `PatchLifecycleAbort`: Patch workflow cannot continue

## Patch Application Strategy

The workflow uses a multi-stage patch application approach:

1. Try `git apply --check` (preferred)
2. If that fails, check if already applied with `git apply --check --reverse`
3. Fall back to `patch -p1` with dry run validation
4. Apply patch with safety guards enabled

## Coverage Deficit Handling

When CI passes but coverage falls below threshold:
1. Parse pytest coverage table from output
2. Extract modules below threshold
3. Generate focused failure summary for Codex
4. Request patches that add/expand tests for those modules

## Logs & Archiving

- **`logs/codex_ci.log`**: Appended log of all Codex interactions (prompt + response)

## Development Workflow

When working on this codebase:

1. The automation scripts are designed to be used by consuming repositories (Zeus, Kalshi), not run directly here
2. Changes to guard scripts should maintain backward compatibility with existing consumers
3. Protected paths must never be modified by automated patches
4. All guard scripts follow a similar CLI pattern: `--root`, exclusions, thresholds
5. Python 3.10+ required (uses `is_relative_to` and other modern APIs)

## Auto-Propagation of Updates

When you successfully run CI and push changes from the ci_shared repository, the updates are **automatically propagated** to every repository listed under `consuming_repositories` in `ci_shared.config.json` (api, zeus, kalshi, aws by default).

### How It Works

After `scripts/ci.sh` successfully pushes ci_shared changes:

1. **`propagate_ci_shared.py`** automatically runs
2. For each consuming repo declared in the config file:
   - **Automatically commits any uncommitted changes** (if present)
   - Copies the canonical shared files (`ci_shared.mk`, `shared-tool-config.toml`, etc.) into the repo via `scripts/sync_project_configs.py`
   - Runs `tool_config_guard --sync` inside that repo
   - Creates a commit describing the sync and pushes it to the remote

### What Gets Auto-Updated

- ✅ `ci_shared.mk` - CI pipeline logic
- ✅ All guard scripts (`ci_tools/scripts/*.py`)
- ✅ CI runner (`ci_tools/scripts/ci.sh`)
- ✅ Shared tool config (`shared-tool-config.toml`)
- ✅ All other ci_shared files

### What Stays Repo-Specific

Each consuming repo maintains its own:
- `pyproject.toml` - Synced via `tool_config_guard.py`, but separate files
- `Makefile` - Repo-specific variables
- `ci_shared.config.json` - Repo-specific settings

### Manual Propagation

If auto-propagation fails or you need to update manually:

```bash
# From any consuming repo (api, zeus, kalshi, aws)
cd ../zeus  # or api / kalshi / aws
python ../ci_shared/scripts/sync_project_configs.py .
python -m ci_tools.scripts.tool_config_guard --repo-root . --sync
git add -A
git commit -m "Sync shared CI files"
git push
```

### Skipping Auto-Propagation

Auto-propagation is skipped if:
- The consuming repo path cannot be located
- The sync script or tool_config_guard fails inside the consuming repo
- Auto-commit of existing changes fails (rare)
