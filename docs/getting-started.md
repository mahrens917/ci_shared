# Getting Started

This guide walks through installing the shared tooling, configuring a consuming
repository, and running the automation loop for the first time.

## Prerequisites
- Python 3.10 or newer
- `pip` and `virtualenv` (recommended for isolated installs)
- Access to the [Codex CLI](https://github.com/kalshi-trading/codex-cli) with a
  valid `OPENAI_API_KEY`
- Git repository with a `scripts/ci.sh` file
  - In consuming repos: Should delegate to `ci_tools/scripts/ci.sh` (installed via this package)
  - The shared CI script handles check execution, commit generation, and push

## Install the Package
From the consuming repository root, install `codex-ci-tools` in editable mode so
that the Python package and helper scripts resolve correctly:

```bash
python -m pip install -e ../ci_shared
```

This exposes the `ci_tools` Python package on `PYTHONPATH`.

## Configure Repository Context (Optional)
Place a `ci_shared.config.json` at the repository root when you need custom
metadata for the automation loop **and to declare the consuming repositories that
should receive automatic syncs**:

```json
{
  "repo_context": "Brief description of the codebase and its CI quirks",
  "protected_path_prefixes": [
    "ci.py",
    "ci_tools/",
    "scripts/ci.sh",
    "Makefile"
  ],
  "consuming_repositories": [
    {"name": "api", "path": "../api"},
    {"name": "zeus", "path": "../zeus"},
    {"name": "kalshi", "path": "../kalshi"},
    {"name": "aws", "path": "../aws"}
  ],
  "coverage_threshold": 80.0
}
```

Values are read by `ci_tools.ci` to tighten safety rails and tailor coverage
messages, while the `consuming_repositories` list drives `scripts/ci.sh`
propagation so that running CI once from `ci_shared` updates every sibling repo.

## Quick Usage

### Running CI Directly
```bash
./scripts/ci.sh      # Runs full CI: checks → commit → push
CI_AUTOMATION=1 ./scripts/ci.sh  # Checks only, no git operations
```

The `scripts/ci.sh` wrapper delegates to `ci_tools/scripts/ci.sh`, which:
- Installs missing test dependencies
- Runs `make check` to execute all guards and tests
- Stages changes and generates Codex commit messages (unless `CI_AUTOMATION=1`)
- Commits and pushes to remote (interactive mode only)

### Python Automation Interface (for repair loops)
```bash
python -m ci_tools.ci --model gpt-5-codex --reasoning-effort high
```

- Runs the configured CI command (default: `./scripts/ci.sh` with `CI_AUTOMATION=1`)
- Streams logs to Codex when failures occur
- Applies Codex patches while enforcing protected path rules
- Generates a commit message when checks pass

## Integrate Shared Makefile Targets

Include `ci_shared.mk` inside your repository’s `Makefile` to adopt the shared
check pipeline:

```make
include ci_shared.mk

.PHONY: check
check: shared-checks
```

The `shared-checks` target runs formatters, static analyzers, the guard suite,
and pytest with coverage. Customize high-level knobs such as `FORMAT_TARGETS` or
`PYTEST_NODES` by overriding the variables before including the file.

### Scanning Additional Source Roots

By default, guards scan only `$(SHARED_SOURCE_ROOT)` (typically `src`). Repos
with standalone CLI scripts or other top-level Python directories can opt in to
guard coverage by setting `SHARED_EXTRA_SOURCE_ROOTS` before the include:

```make
SHARED_EXTRA_SOURCE_ROOTS = scripts
include ci_shared.mk

.PHONY: check
check: shared-checks
```

This extends the following tools to cover `scripts/` alongside `src/`:

- **Guards**: structure, complexity, module, function_size, method_count,
  inheritance, and dependency guards all scan every root listed in
  `SHARED_GUARD_ROOTS` (= `SHARED_SOURCE_ROOT` + `SHARED_EXTRA_SOURCE_ROOTS`).
- **Linters**: ruff, pyright, pylint, and compileall run against all roots.
- **Coverage**: pytest `--cov` flags are generated per root so coverage reports
  include the extra directories.

Guards that intentionally remain single-root:

| Guard | Reason |
| ----- | ------ |
| `unused_module_guard` | Scripts are standalone CLIs, not imported modules; scanning them would produce false positives. |
| `policy_guard` / `data_guard` | Target library code (`src`, `tests`); subprocess/sleep bans do not apply to CLI scripts. |
| `documentation_guard` | `--root` refers to the repo root (`.`), not a source root. |

#### Consumer Repos with `scripts/` Directories

The following repos have `scripts/` directories containing Python that should be
checked. Rollout order is smallest-first to minimize fix-up effort:

| Repo | Files | Approx. Lines |
| ---- | ----: | ------------: |
| tracker | 1 | 74 |
| weather | 3 | 348 |
| common | 2 | 616 |
| analytics | 6 | 3,215 |
| monitor | 57 | 13,655 |
| zeus | 60 | 27,154 |

For each repo: add `SHARED_EXTRA_SOURCE_ROOTS = scripts` to its `Makefile`,
run `make check`, and fix any violations before merging.

## Verify the Installation
1. `python -m ci_tools.ci --dry-run --command "echo ok"` – ensures CLI wiring
2. `make shared-checks` – validates the guard scripts can be imported and run

If any command fails, see the [Development Guide](development.md) for debugging
tips and dependency notes.
