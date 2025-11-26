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

## Verify the Installation
1. `python -m ci_tools.ci --dry-run --command "echo ok"` – ensures CLI wiring
2. `make shared-checks` – validates the guard scripts can be imported and run

If any command fails, see the [Development Guide](development.md) for debugging
tips and dependency notes.
