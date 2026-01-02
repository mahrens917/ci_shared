# codex-ci-tools

Shared continuous-integration toolkit used across Zeus, Kalshi, AWS, API, and TicTacToe repositories. This package provides:
- **Automated CI repair loops** powered by Codex that fix failing checks
- **Comprehensive guard suite** enforcing code quality, security, and complexity standards
- **Shared CI pipeline** with consistent tooling across all consuming repositories
- **Intelligent commit message generation** with automatic diff chunking for large changes

## What Is This Repository For?

`ci_shared` standardizes continuous integration across multiple projects by providing:

1. **Automated Fix Loops**: When CI fails, Codex analyzes the errors and generates patches to fix them
2. **Quality Guards**: Enforces strict standards for code complexity, structure, documentation, and security
3. **Unified Pipeline**: Ensures all consuming repositories use identical linter/formatter/test configurations
4. **Auto-Propagation**: Updates to `ci_shared` automatically sync to consuming repositories

## Installation

### For Consuming Repositories (Zeus, Kalshi, AWS, API, TicTacToe)

From your repository root:

```bash
# Install ci_shared in editable mode
python -m pip install -e ../ci_shared

# Sync shared tool configurations
python -m ci_tools.scripts.tool_config_guard --sync
```

This makes `ci_tools` scripts available on your `PYTHONPATH`.

### Requirements

- Python 3.10+
- Git repository
- Codex CLI (`codex` command) for AI-powered features
- Optional: `gitleaks` for secret scanning

## How to Use

### Option 1: Modern Python Interface (Recommended)

Run the full CI pipeline with automated fixes:

```bash
# Basic usage - runs CI and fixes failures automatically
python -m ci_tools.ci --model gpt-5-codex --reasoning-effort high

# Dry run - just run CI once without Codex
python -m ci_tools.ci --dry-run

# Custom CI command
python -m ci_tools.ci --command "pytest tests/" --max-iterations 10

# Auto-stage and commit when CI passes
python -m ci_tools.ci --auto-stage --commit-message
```

**Common Options:**
- `--model`: Codex model to use (default: `gpt-5-codex`)
- `--reasoning-effort`: `low`, `medium`, or `high` (default: `high`)
- `--max-iterations`: Max fix attempts (default: 5)
- `--patch-approval-mode`: `prompt` (ask before applying) or `auto`
- `--auto-stage`: Run `git add -A` after CI passes
- `--commit-message`: Request commit message from Codex

### Option 2: Shared CI Script

Run the complete CI pipeline manually:

```bash
# From consuming repository
./scripts/ci.sh

# Or with custom commit message
./scripts/ci.sh "Add feature X"
```

The `ci.sh` script:
1. **Runs all CI checks** (linters, formatters, guards, tests, security scans)
2. **Continues through failures** - shows all errors, not just the first one
3. **Auto-generates commit messages** for large diffs using chunking
4. **Commits and pushes** if all checks pass
5. **Auto-propagates** ci_shared updates to consuming repos

### Option 3: Manual Makefile Targets

For local development:

```bash
make check    # Run full CI suite (all checks run to completion)
make format   # Auto-format code (black, isort)
make lint     # Run linters only
make test     # Run tests with coverage
make policy   # Run policy guards only
```

## Features

### Automated CI Repair Loop
- Runs CI command and captures failures
- Sends error logs to Codex for patch generation
- Applies patches with safety validation
- Loops until CI passes or max iterations reached
- Handles coverage deficits with targeted test generation

### Intelligent Commit Message Generation
- **Automatic diff chunking** for large changes (>6000 lines)
- Splits diffs into manageable chunks (max 4 by default)
- Summarizes each chunk separately
- Synthesizes final cohesive commit message
- Avoids context window overflow

**Environment Variables:**
```bash
CI_CODEX_COMMIT_CHUNK_LINE_LIMIT=6000  # Lines per chunk
CI_CODEX_COMMIT_MAX_CHUNKS=4           # Max chunks
CI_COMMIT_MODEL=gpt-5-codex            # Model override
CI_COMMIT_REASONING=high               # Reasoning effort
```

### Comprehensive Guard Suite
- **Code Quality**: complexity, module size, function length, structure
- **Security**: gitleaks (secrets), bandit (vulnerabilities), pip-audit (dependency CVEs)
- **Policy**: banned keywords, TODO markers, exception handling rules
- **Documentation**: enforces README files for packages and domains
- **Dependencies**: limits instantiations, validates imports

### Fail-Through CI Pipeline
All CI checks **run to completion** regardless of failures:
- Shows **all errors** in one run, not just the first
- Only proceeds with commit/push if **all checks pass**
- Clear progress indicators (`→ Running pytest...`)
- Final summary shows total failed checks

### Auto-Propagation
When you push changes to `ci_shared`:
1. Updates automatically sync to **all consuming repositories** listed in `ci_shared.config.json`
2. Shared files are copied (`ci_shared.mk`, `shared-tool-config.toml`, etc.)
3. Tool configurations are synced via `tool_config_guard`
4. Changes are committed and pushed to each consuming repo

## Configuration
- `ci_shared.config.json` supplies repository context, protected path prefixes,
  coverage thresholds, **and the `consuming_repositories` list** that drives
  config sync + propagation into API, Zeus, Kalshi, AWS, etc.
- Environment variables such as `OPENAI_MODEL`, `OPENAI_REASONING_EFFORT`, and
  `GIT_REMOTE` customize Codex behavior and push targets.

## Guard Suite
Key guard scripts live under `ci_tools/scripts/` and `scripts/`:
- `policy_guard.py` – enforces banned keywords, TODO markers, and fail-fast rules
- `module_guard.py`, `function_size_guard.py` – prevent oversize modules/functions
- `coverage_guard.py` – enforces per-file coverage thresholds
- `documentation_guard.py` – verifies that required docs exist
- `scripts/complexity_guard.py` – limits cyclomatic and cognitive complexity
- **Security**: gitleaks (secret detection), bandit (security linting), pip-audit (dependency CVEs)

See the [Guard Suite reference](docs/guard-suite.md) and [Security Guidelines](SECURITY.md) for details.

## Common Workflows

### Daily Development
```bash
# Make code changes
vim src/module.py

# Run CI locally to check your changes
make check

# If everything passes, commit and push
./scripts/ci.sh "Implement feature X"
```

### Fixing CI Failures Automatically
```bash
# Let Codex fix failures automatically
python -m ci_tools.ci --model gpt-5-codex --reasoning-effort high

# Or with automatic patch application
python -m ci_tools.ci --patch-approval-mode auto --max-iterations 10
```

### Working with Large Changes
When you have a large diff (many files changed), the commit message generator will automatically:
1. Split the diff into chunks (~6000 lines each)
2. Request a summary for each chunk
3. Synthesize a final cohesive commit message
4. Avoid context window errors

No special configuration needed - it happens automatically!

### Individual Guard Scripts
Run specific guards during development:

```bash
# Check function sizes
python -m ci_tools.scripts.function_size_guard --root src --max-function-lines 80

# Check complexity
python -m ci_tools.scripts.complexity_guard --root src --max-cyclomatic 10

# Check coverage
python -m ci_tools.scripts.coverage_guard --threshold 80
```

## Documentation
- [Getting Started](docs/getting-started.md)
- [Automation Workflow](docs/automation.md)
- [Guard Suite](docs/guard-suite.md)
- [Development Guide](docs/development.md)
- [Claude Guidance](CLAUDE.md)

For security practices, review [`SECURITY.md`](SECURITY.md).

## Troubleshooting

### CI fails on first check and stops
**Fixed!** As of the latest version, CI now runs **all checks to completion** and reports all failures at once.

### Commit message generation fails with "context window" error
**Fixed!** The commit message generator now automatically chunks large diffs and summarizes them incrementally.

### Tool configurations out of sync
```bash
# Sync from ci_shared to your repository
python -m ci_tools.scripts.tool_config_guard --sync
```

### Auto-propagation didn't run
Check that:
1. Your repository is listed in `ci_shared.config.json` under `consuming_repositories`
2. The repository path is correct (e.g., `../zeus`)
3. You have write permissions to the consuming repository
