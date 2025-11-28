#!/usr/bin/env bash
# Shared CI shell helper used by multiple repositories.
set -euo pipefail

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
CI_SHARED_ROOT="${CI_SHARED_ROOT:-${HOME}/ci_shared}"
if [[ ! -d "${CI_SHARED_ROOT}" ]]; then
  echo "Shared CI root not found at ${CI_SHARED_ROOT}. Set CI_SHARED_ROOT to your ci_shared checkout." >&2
  exit 1
fi
if [[ ":${PYTHONPATH:-}:" != *":${CI_SHARED_ROOT}:"* ]]; then
  export PYTHONPATH="${CI_SHARED_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
fi
export PYTHONDONTWRITEBYTECODE=1
GIT_REMOTE="${GIT_REMOTE:-origin}"

COMMIT_MESSAGE="${1-}"

cd "${PROJECT_ROOT}"

# Ensure test extras are available (pytest-xdist, pytest-cov, etc.).
python - <<'PY'
import importlib
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path.cwd()
required = ["pytest_cov", "xdist", "ruff", "codespell_lib"]
missing = []
for module in required:
    try:
        importlib.import_module(module)
    except ModuleNotFoundError:
        missing.append(module)

if missing:
    requirements = PROJECT_ROOT / "scripts" / "requirements.txt"
    if not requirements.exists():
        print(f"Cannot locate {requirements}; install the missing packages manually.", file=sys.stderr)
        sys.exit(1)
    cmd = [sys.executable, "-m", "pip", "install", "-r", str(requirements)]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode != 0:
        print("Failed to install test dependencies:\n" + result.stdout)
        sys.exit(result.returncode)
    else:
        print(result.stdout)
PY

echo "Syncing pyproject.toml tool configuration with shared template..."
if ! python -m ci_tools.scripts.tool_config_guard --repo-root "${PROJECT_ROOT}" --sync; then
  echo "tool_config_guard --sync failed; aborting." >&2
  exit 1
fi

if ! python -c "import packaging" >/dev/null 2>&1; then
  VENDOR_PATH=$(python - <<'PY'
import ci_tools
from pathlib import Path
print((Path(ci_tools.__file__).resolve().parent / 'vendor').as_posix())
PY
  )
  if [[ -d "${VENDOR_PATH}" ]]; then
    export PYTHONPATH="${VENDOR_PATH}${PYTHONPATH:+:${PYTHONPATH}}"
    echo "Activated lightweight packaging shim for CI tooling." >&2
  else
    echo "[warning] ci_tools vendor shim not found at ${VENDOR_PATH}." >&2
  fi
fi

echo "Running make check..."
if ! make -k check; then
  echo "make check failed; aborting commit and push." >&2
  exit 1
fi

if [[ -n "${CI_AUTOMATION:-}" ]]; then
  echo "CI automation mode active; skipping git staging and commit."
  exit 0
fi

echo "Staging repository changes..."
git add -A

if git diff --cached --quiet; then
  echo "No staged changes detected; nothing to commit." >&2
  exit 0
fi

COMMIT_BODY=""
if [ -z "${COMMIT_MESSAGE}" ]; then
  # Prefer Claude CLI if available, fall back to Codex
  if command -v claude >/dev/null 2>&1; then
    CLI_NAME="Claude"
    export CI_CLI_TYPE=claude
  elif command -v codex >/dev/null 2>&1; then
    CLI_NAME="Codex"
    export CI_CLI_TYPE=codex
  else
    CLI_NAME=""
  fi

  if [ -n "${CLI_NAME}" ]; then
    if [ "${CI_CLI_TYPE}" = "claude" ]; then
      export CI_COMMIT_MODEL="${CI_COMMIT_MODEL:-claude-haiku-4-5-20251001}"
    else
      export CI_COMMIT_MODEL="${CI_COMMIT_MODEL:-gpt-5.1-codex-mini}"
    fi
    export CI_COMMIT_REASONING="${CI_COMMIT_REASONING:-medium}"

    echo "Requesting commit message from ${CLI_NAME} (model: ${CI_COMMIT_MODEL})..."
    COMMIT_OUTPUT_FILE="$(mktemp)"
    if python -m ci_tools.scripts.generate_commit_message --output "${COMMIT_OUTPUT_FILE}"; then
      COMMIT_MESSAGE="$(head -n 1 "${COMMIT_OUTPUT_FILE}")"
      COMMIT_BODY="$(tail -n +2 "${COMMIT_OUTPUT_FILE}")"
      if [ -n "${COMMIT_MESSAGE//[[:space:]]/}" ]; then
        echo "Using ${CLI_NAME} commit summary: ${COMMIT_MESSAGE}"
      else
        echo "${CLI_NAME} returned an empty commit summary; falling back to manual entry." >&2
      fi
    else
      echo "${CLI_NAME} commit message generation failed; falling back to manual entry." >&2
    fi
    rm -f "${COMMIT_OUTPUT_FILE}"
  fi

  if [ -z "${COMMIT_MESSAGE//[[:space:]]/}" ]; then
    read -r -p "Enter commit message: " COMMIT_MESSAGE
    if [ -z "${COMMIT_MESSAGE//[[:space:]]/}" ]; then
      echo "Commit message is required; aborting." >&2
      exit 1
    fi
  fi
fi

echo "Creating commit..."
if [ -n "${COMMIT_BODY//[[:space:]]/}" ]; then
  git commit -m "${COMMIT_MESSAGE}" -m "${COMMIT_BODY}"
else
  git commit -m "${COMMIT_MESSAGE}"
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"

echo "Pushing to ${GIT_REMOTE}/${CURRENT_BRANCH}..."
git push "${GIT_REMOTE}" "${CURRENT_BRANCH}"

# Sync shared config files into consuming repositories when running inside ci_shared.
if [ -f "${PROJECT_ROOT}/scripts/sync_project_configs.py" ]; then
  echo ""
  echo "Syncing shared config files into consuming repositories..."

  CONSUMER_DIRS=()
  CONSUMER_TMP="$(mktemp)"
  if python - "${PROJECT_ROOT}" "${CONSUMER_TMP}" <<'PY'; then
import sys
from pathlib import Path
from ci_tools.utils.consumers import load_consuming_repos

repo_root = Path(sys.argv[1]).resolve()
output_file = Path(sys.argv[2])
repos = load_consuming_repos(repo_root)
with output_file.open("w", encoding="utf-8") as handle:
    for repo in repos:
        handle.write(f"{repo.path}\n")
PY
    mapfile -t CONSUMER_DIRS < "${CONSUMER_TMP}" 2>/dev/null || CONSUMER_DIRS=()
  else
    echo "⚠️  Failed to resolve consuming repositories; skipping sync." >&2
  fi
  rm -f "${CONSUMER_TMP}"

  if [ "${#CONSUMER_DIRS[@]}" -eq 0 ]; then
    echo "No consuming repositories configured; update ci_shared.config.json if needed."
  else
    if python "${PROJECT_ROOT}/scripts/sync_project_configs.py" "${CONSUMER_DIRS[@]}"; then
      echo "✓ Config sync complete"
    else
      echo "⚠️  Config sync encountered issues (see above)" >&2
    fi

    echo ""
    echo "Running shared tool-config sync in consuming repositories..."
    TOOL_SYNC_ERRORS=0
    for repo_dir in "${CONSUMER_DIRS[@]}"; do
      if [ ! -d "${repo_dir}" ]; then
        echo "  • Skipping missing repo: ${repo_dir}" >&2
        TOOL_SYNC_ERRORS=1
        continue
      fi

      echo "  • ${repo_dir}"
      if python -m ci_tools.scripts.tool_config_guard --repo-root "${repo_dir}" --sync; then
        echo "    ✓ tool configuration synced"
      else
        echo "    ⚠️  tool_config_guard failed for ${repo_dir}" >&2
        TOOL_SYNC_ERRORS=1
      fi
    done

    if [ "${TOOL_SYNC_ERRORS}" -ne 0 ]; then
      echo "⚠️  One or more repositories failed tool-config sync; review logs above." >&2
    else
      echo "✓ Tool-config sync complete across consuming repositories."
    fi
  fi

  echo ""
  echo "Propagating ci_shared updates into consuming repositories..."
  if python -m ci_tools.scripts.propagate_ci_shared; then
    echo "✓ Propagation complete"
  else
    echo "⚠️  Propagation encountered issues (see above)" >&2
  fi
fi

echo "Done."
exit 0
