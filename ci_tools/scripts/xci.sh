#!/usr/bin/env bash
# Automate the "run ci.sh, capture failure, ask LLM for a patch, retry" loop.
# Supports both Codex (gpt-5-codex) and Claude (claude-sonnet-4.5) CLIs.
set -euo pipefail

DEFAULT_MAX_ATTEMPTS=5
DEFAULT_TAIL_LINES=200
DEFAULT_CODEX_CLI=claude
DEFAULT_MODEL=claude-sonnet-4.5
DEFAULT_REASONING_EFFORT=medium
DEFAULT_LOG_FILE=.xci.log
DEFAULT_ARCHIVE_DIR=.xci/archive
DEFAULT_TMP_DIR=.xci/tmp
DEFAULT_CLI_TYPE=claude

# Load configuration overrides from JSON to avoid exporting env vars.
CONFIG_PATH=${XCI_CONFIG:-xci.config.json}
if [[ -f "${CONFIG_PATH}" ]]; then
  while IFS= read -r line; do
    [[ -n "${line}" ]] && eval "${line}"
  done < <(python - "${CONFIG_PATH}" <<'PY'
import json
import shlex
import sys

path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except OSError:
    sys.exit(0)


def emit_int(var_name, value):
    if value is None:
        return
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise SystemExit(f"Invalid integer for {var_name}: {value!r}")
    print(f"{var_name}={number}")


def emit_str(var_name, value):
    if value is None:
        return
    text = str(value)
    print(f"{var_name}={shlex.quote(text)}")

emit_int("CFG_MAX_ATTEMPTS", data.get("max_attempts"))
emit_int("CFG_TAIL_LINES", data.get("log_tail"))
emit_str("CFG_CODEX_CLI", data.get("codex_cli"))
emit_str("CFG_MODEL", data.get("model"))
emit_str("CFG_REASONING_EFFORT", data.get("reasoning_effort"))
emit_str("CFG_LOG_FILE", data.get("log_file"))
emit_str("CFG_ARCHIVE_DIR", data.get("archive_dir"))
emit_str("CFG_TMP_DIR", data.get("tmp_dir"))
emit_str("CFG_CLI_TYPE", data.get("cli_type"))
PY
)
else
  echo "[xci] Config file '${CONFIG_PATH}' not found; using defaults."
fi

MAX_ATTEMPTS=${XCI_MAX_ATTEMPTS:-${CFG_MAX_ATTEMPTS:-$DEFAULT_MAX_ATTEMPTS}}
TAIL_LINES=${XCI_LOG_TAIL:-${CFG_TAIL_LINES:-$DEFAULT_TAIL_LINES}}
CODEX_CLI=${XCI_CLI:-${CFG_CODEX_CLI:-$DEFAULT_CODEX_CLI}}
MODEL=${XCI_MODEL:-${CFG_MODEL:-$DEFAULT_MODEL}}
REASONING_EFFORT=${XCI_REASONING_EFFORT:-${CFG_REASONING_EFFORT:-$DEFAULT_REASONING_EFFORT}}
LOG_FILE=${XCI_LOG_FILE:-${CFG_LOG_FILE:-$DEFAULT_LOG_FILE}}
ARCHIVE_DIR=${XCI_ARCHIVE_DIR:-${CFG_ARCHIVE_DIR:-$DEFAULT_ARCHIVE_DIR}}
TMP_DIR=${XCI_TMP_DIR:-${CFG_TMP_DIR:-$DEFAULT_TMP_DIR}}
CLI_TYPE=${XCI_CLI_TYPE:-${CFG_CLI_TYPE:-$DEFAULT_CLI_TYPE}}

# Auto-detect CLI type from executable name if set to "auto"
if [[ "${CLI_TYPE}" == "auto" ]]; then
  CLI_BASENAME=$(basename "${CODEX_CLI}")
  if [[ "${CLI_BASENAME}" == "claude" ]]; then
    CLI_TYPE="claude"
  else
    CLI_TYPE="codex"
  fi
fi

# Set default model based on CLI type if not explicitly configured
if [[ "${CLI_TYPE}" == "claude" ]] && [[ "${MODEL}" == "gpt-5-codex" ]]; then
  MODEL="claude-sonnet-4.5"
fi

mkdir -p "${ARCHIVE_DIR}"
if ! find "${ARCHIVE_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null; then
  true
fi
echo "[xci] Archiving LLM exchanges under ${ARCHIVE_DIR}"

mkdir -p "${TMP_DIR}"
if ! find "${TMP_DIR}" -mindepth 1 -maxdepth 1 -exec rm -f {} + 2>/dev/null; then
  true
fi

if ! command -v "${CODEX_CLI}" >/dev/null 2>&1; then
  echo "[xci] ERROR: CLI '${CODEX_CLI}' not found in PATH." >&2
  if [[ "${CLI_TYPE}" == "claude" ]]; then
    echo "[xci] Install from: https://github.com/anthropics/anthropic-cli" >&2
  else
    echo "[xci] Install from: https://github.com/anthropics/anthropic-cli" >&2
  fi
  exit 2
fi

# Handle --help and --version flags
if [[ $# -eq 1 ]]; then
  case "$1" in
    --help|-h|help)
      cat <<'EOF'
xci.sh - Automated CI repair loop via Claude (or Codex)

Usage: xci.sh [ci-command...]

Runs CI command in a loop, capturing failures and requesting patches from Claude
until CI passes or maximum attempts are reached. Archives all exchanges.

Default: Claude (claude-sonnet-4.5) - Configure via xci.config.json

Arguments:
  [ci-command...]  Command to execute (default: auto-detect ./ci.sh or scripts/ci.sh)

Configuration File (xci.config.json):
  All settings are configured via xci.config.json (copy from xci.config.json.example)

  Default (Claude):
  {
    "max_attempts": 5,
    "log_tail": 200,
    "codex_cli": "claude",
    "model": "claude-sonnet-4.5",
    "reasoning_effort": "medium",
    "cli_type": "claude"
  }

  For Codex instead:
  {
    "codex_cli": "codex",
    "model": "gpt-5-codex",
    "cli_type": "codex"
  }

Configuration Options:
  max_attempts       Maximum fix attempts (default: 5)
  log_tail           Log lines to send to LLM (default: 200)
  codex_cli          CLI executable: "claude" or "codex" (default: claude)
  model              Model: claude-sonnet-4.5 or gpt-5-codex (default: claude-sonnet-4.5)
  reasoning_effort   Reasoning: low, medium, high (default: medium)
  cli_type           CLI type: claude or codex (default: claude)
  log_file           Log file path (default: .xci.log)
  archive_dir        Archive directory (default: .xci/archive)
  tmp_dir            Temp directory (default: .xci/tmp)

Examples:
  # Using Claude (default)
  cp xci.config.json.example xci.config.json
  xci.sh                    # Auto-detect and run CI script with claude
  xci.sh ./scripts/ci.sh    # Explicit CI command with claude

  # Switching to Codex
  Edit xci.config.json:
    "codex_cli": "codex"
    "model": "gpt-5-codex"
    "cli_type": "codex"
  xci.sh

Documentation:
  See docs/automation.md for detailed usage and examples.

Note: For more advanced features (--dry-run, --patch-approval-mode, etc.),
      use the Python interface: python -m ci_tools --help
EOF
      exit 0
      ;;
    --version|-v|version)
      echo "xci.sh version 0.1.0 (codex-ci-tools)"
      exit 0
      ;;
  esac
fi

# Track start time for stats
START_TIME=$(date +%s)

if [[ $# -gt 0 ]]; then
  CI_COMMAND=("$@")
else
  if [[ -x "./ci.sh" ]]; then
    CI_COMMAND=(./ci.sh)
  elif [[ -x "scripts/ci.sh" ]]; then
    CI_COMMAND=(scripts/ci.sh)
  elif [[ -x "scripts/dev/ci.sh" ]]; then
    CI_COMMAND=(scripts/dev/ci.sh)
  else
    echo "[xci] ERROR: Unable to locate an executable ci.sh in the current directory." >&2
    echo "[xci] Searched: ./ci.sh, scripts/ci.sh, scripts/dev/ci.sh" >&2
    echo "[xci] Provide a command explicitly: xci.sh <command>" >&2
    echo "[xci] Example: xci.sh ./scripts/dev/ci.sh" >&2
    echo "[xci] Run 'xci.sh --help' for more information." >&2
    exit 2
  fi
fi

# Helper to create temp files in our local tmp directory
mktmp() {
  mktemp "${TMP_DIR}/xci.XXXXXX"
}

# Helper to limit diff size for Codex prompt to prevent context window overflow
limit_diff_size() {
  local diff_text="$1"
  local max_chars=50000
  local max_lines=1000

  local char_count=${#diff_text}
  local line_count
  line_count=$(echo "$diff_text" | wc -l | tr -d ' ')

  if (( char_count > max_chars )) || (( line_count > max_lines )); then
    cat <<EOF
[Diff too large: ${char_count} chars, ${line_count} lines]

Summary (git diff --stat):
$(git diff --stat 2>/dev/null || true)

Note: Full diff exceeded limits (${max_chars} chars or ${max_lines} lines).
The focused changes from the CI failure output above show which files need attention.
EOF
  else
    echo "$diff_text"
  fi
}

# Helper to invoke the appropriate LLM CLI
invoke_llm() {
  local prompt_file="$1"
  local output_file="$2"

  if [[ "${CLI_TYPE}" == "claude" ]]; then
    # Claude CLI: simple invocation with --print flag
    "${CODEX_CLI}" -p <"${prompt_file}" >"${output_file}"
  else
    # Codex CLI: uses exec subcommand with model and reasoning effort
    if [[ -n "${REASONING_EFFORT}" ]]; then
      "${CODEX_CLI}" exec --model "${MODEL}" -c "model_reasoning_effort=${REASONING_EFFORT}" - <"${prompt_file}" >"${output_file}"
    else
      "${CODEX_CLI}" exec --model "${MODEL}" - <"${prompt_file}" >"${output_file}"
    fi
  fi
}

attempt=1
while true; do
  echo "[xci] Attempt ${attempt}: ${CI_COMMAND[*]}"

  set +e
  "${CI_COMMAND[@]}" 2>&1 | tee "${LOG_FILE}"
  ci_status=${PIPESTATUS[0]}
  set -e

  if [[ ${ci_status} -eq 0 ]]; then
    echo "[xci] CI passed on attempt ${attempt}."
    status_after_ci=$(git status --short 2>/dev/null || true)
    if [[ -n "${status_after_ci}" ]]; then
      diff_after_ci=$(limit_diff_size "$(git diff 2>/dev/null || true)")
      timestamp=$(date +"%Y%m%dT%H%M%S")
      commit_prefix="${ARCHIVE_DIR}/commit_${timestamp}"
      commit_prompt=$(mktmp)
      cat >"${commit_prompt}" <<EOF_COMMIT
You are preparing an imperative, single-line git commit message (<=72 characters)
for the current working tree. Provide only the commit summary line; do not
include additional commentary unless a short body is absolutely necessary.

Repository status (git status --short):
${status_after_ci}

Diff (git diff):
```diff
${diff_after_ci}
```
Use the provided diff for context. Do not run shell commands such as `diff --git`.
EOF_COMMIT

      cp "${commit_prompt}" "${commit_prefix}_prompt.txt"

      commit_response=$(mktmp)
      set +e
      invoke_llm "${commit_prompt}" "${commit_response}"
      commit_status=$?
      set -e

      if [[ ${commit_status} -ne 0 ]]; then
        echo "[xci] LLM commit message request failed (exit ${commit_status}); skipping suggestion." >&2
      else
        cp "${commit_response}" "${commit_prefix}_response.txt"
        commit_message_file=$(mktmp)
        if python - "${commit_response}" "${commit_message_file}" <<'PY'
import pathlib
import sys

response_path = pathlib.Path(sys.argv[1])
out_path = pathlib.Path(sys.argv[2])

text = response_path.read_text().strip()
if text.startswith("assistant:"):
    text = text.partition("\n")[2]

text = text.strip()
if text:
    out_path.write_text(text)
else:
    out_path.write_text("")
PY
        then
          commit_message=$(head -n 1 "${commit_message_file}" | tr -d '\r')
          if [[ -n "${commit_message}" ]]; then
            echo "[xci] Suggested commit message:"
            echo "  ${commit_message}"
            cp "${commit_message_file}" "${commit_prefix}_message.txt"
          else
            echo "[xci] Codex response did not contain a commit summary."
          fi
        else
          echo "[xci] Failed to parse Codex commit response; see ${commit_prefix}_response.txt." >&2
        fi
      fi
    else
      echo "[xci] Working tree clean; skipping commit message request."
    fi

    # Calculate run statistics
    END_TIME=$(date +%s)
    ELAPSED_TIME=$((END_TIME - START_TIME))
    PATCHES_APPLIED=$((attempt - 1))

    echo ""
    echo "========================================"
    echo "[xci] ✓ SUCCESS: CI passed!"
    echo "========================================"
    echo "Run Statistics:"
    echo "  • Total attempts: ${attempt}"
    echo "  • Patches applied: ${PATCHES_APPLIED}"
    echo "  • Elapsed time: ${ELAPSED_TIME}s"
    echo "  • CI command: ${CI_COMMAND[*]}"
    echo "========================================"
    break
  fi

  if (( attempt >= MAX_ATTEMPTS )); then
    echo "" >&2
    echo "========================================"  >&2
    echo "[xci] ✗ FAILED: Maximum attempts (${MAX_ATTEMPTS}) reached" >&2
    echo "========================================"  >&2
    echo "CI is still failing after ${MAX_ATTEMPTS} automated fix attempts." >&2
    echo "" >&2
    echo "Common reasons for persistent failures:" >&2
    echo "  • Violations are too numerous/complex (${MAX_ATTEMPTS} patches insufficient)" >&2
    echo "  • Large classes/functions need architectural refactoring" >&2
    echo "  • Issues require design decisions beyond automated fixes" >&2
    echo "  • Structural problems need manual intervention" >&2
    echo "" >&2
    echo "Next steps:" >&2
    echo "  1. Review CI output above to understand the failures" >&2
    echo "  2. Check .xci/archive/ for Codex's attempted solutions" >&2
    echo "  3. Address the root architectural issues manually" >&2
    echo "  4. Consider breaking down large components" >&2
    echo "========================================"  >&2
    exit 1
  fi

  echo "[xci] CI failed (exit ${ci_status}); preparing Codex prompt..."

  log_tail=$(tail -n "${TAIL_LINES}" "${LOG_FILE}" 2>/dev/null || true)
  git_status=$(git status --short 2>/dev/null || true)
  git_diff=$(limit_diff_size "$(git diff 2>/dev/null || true)")

  prompt_file=$(mktmp)
  cat >"${prompt_file}" <<EOF_PROMPT
You are assisting with automated CI repairs for the repository at $(pwd).

Run details:
- Attempt: ${attempt}
- CI command: ${CI_COMMAND[*]}

Git status:
${git_status:-<clean>}

Current diff:
\`\`\`diff
${git_diff:-/* no diff */}
\`\`\`

Most recent CI log tail:
\`\`\`
${log_tail}
\`\`\`

STRICT REQUIREMENTS - YOU MUST FOLLOW THESE RULES:
1. Fix the UNDERLYING CODE ISSUES, not the tests or CI checks
2. NEVER add --baseline arguments to any guard scripts in Makefiles or CI configurations
3. NEVER create baseline files (e.g., module_guard_baseline.txt, function_size_guard_baseline.txt)
4. NEVER add exemption comments like "policy_guard: allow-*", "# noqa", or "pylint: disable"
5. NEVER add --exclude arguments to guard scripts to bypass checks
6. NEVER modify guard scripts themselves (policy_guard.py, module_guard.py, structure_guard.py, function_size_guard.py, etc.)
7. NEVER modify CI configuration files (Makefile, ci.sh, xci.sh, etc.)
8. If a module/class/function is too large, REFACTOR it into smaller pieces
9. If there's a policy violation, FIX the code to comply with the policy

Your job is to fix the code quality issues, not to bypass the quality checks.

Please respond with a unified diff (starting with \`diff --git\`) that fixes the failure.
If no change is needed, respond with NOOP.
EOF_PROMPT

  response_file=$(mktmp)
  set +e
  invoke_llm "${prompt_file}" "${response_file}"
  llm_status=$?
  set -e

  if [[ ${llm_status} -ne 0 ]]; then
    echo "" >&2
    echo "========================================"  >&2
    echo "[xci] ✗ FAILED: LLM CLI error (exit ${llm_status})" >&2
    echo "========================================"  >&2
    exit 3
  fi

  timestamp=$(date +"%Y%m%dT%H%M%S")
  archive_prefix="${ARCHIVE_DIR}/attempt${attempt}_${timestamp}"
  cp "${prompt_file}" "${archive_prefix}_prompt.txt"
  cp "${response_file}" "${archive_prefix}_response.txt"

  if grep -qi '^NOOP$' "${response_file}"; then
    echo "" >&2
    echo "========================================"  >&2
    echo "[xci] ✗ FAILED: Codex returned NOOP (no fix suggested)" >&2
    echo "Response saved at: ${archive_prefix}_response.txt" >&2
    echo "========================================"  >&2
    exit 4
  fi

  patch_file=$(mktmp)
  extract_result=$(mktmp)
  if ! python - "${response_file}" "${patch_file}" "${extract_result}" <<'PY'
import pathlib
import sys

response_path = pathlib.Path(sys.argv[1])
patch_path = pathlib.Path(sys.argv[2])
result_path = pathlib.Path(sys.argv[3])

text = response_path.read_text()
if text.startswith("assistant:"):
    text = text.partition("\n")[2]

text = text.strip()

# Check if response is empty or just whitespace
if not text:
    result_path.write_text("EMPTY_RESPONSE")
    sys.exit(1)

# Check if response explains it can't fix the issue
if any(phrase in text.lower() for phrase in [
    "cannot automatically",
    "too complex",
    "requires manual",
    "architectural decision",
    "beyond automated",
    "cannot be automatically",
]):
    result_path.write_text("REQUIRES_MANUAL")
    # Still try to extract any partial response
    marker = "diff --git "
    idx = text.find(marker)
    if idx == -1:
        sys.exit(1)
    patch_path.write_text(text[idx:])
    sys.exit(0)

marker = "diff --git "
idx = text.find(marker)
if idx == -1:
    result_path.write_text("NO_DIFF")
    sys.exit(1)

patch_path.write_text(text[idx:])
result_path.write_text("SUCCESS")
PY
  then
    extract_status=$(cat "${extract_result}" 2>/dev/null || echo "UNKNOWN")

    case "${extract_status}" in
      EMPTY_RESPONSE)
        echo "" >&2
        echo "========================================"  >&2
        echo "[xci] ✗ FAILED: Codex returned empty response" >&2
        echo "========================================"  >&2
        echo "This usually means the task is too complex for automated fixes." >&2
        echo "" >&2
        echo "Review the CI failures and consider:" >&2
        echo "  • Breaking large classes/functions into smaller pieces" >&2
        echo "  • Refactoring complex code manually" >&2
        echo "  • Addressing architectural issues" >&2
        echo "" >&2
        echo "Prompt saved at: ${archive_prefix}_prompt.txt" >&2
        echo "Response saved at: ${archive_prefix}_response.txt" >&2
        echo "========================================"  >&2
        exit 5
        ;;
      REQUIRES_MANUAL)
        echo "" >&2
        echo "========================================"  >&2
        echo "[xci] ✗ FAILED: Changes require manual intervention" >&2
        echo "========================================"  >&2
        echo "Codex indicated this issue cannot be fixed automatically." >&2
        echo "" >&2
        echo "Common reasons:" >&2
        echo "  • Classes/functions too large (need architectural refactoring)" >&2
        echo "  • Complex policy violations (require design decisions)" >&2
        echo "  • Structural issues (need breaking changes)" >&2
        echo "" >&2
        echo "See Codex explanation at: ${archive_prefix}_response.txt" >&2
        echo "========================================"  >&2
        exit 6
        ;;
      NO_DIFF|*)
        if (( attempt >= MAX_ATTEMPTS - 1 )); then
          echo "" >&2
          echo "========================================"  >&2
          echo "[xci] ✗ FAILED: Unable to extract fixes from Codex" >&2
          echo "========================================"  >&2
          echo "After ${attempt} attempts, Codex has not provided usable patches." >&2
          echo "" >&2
          echo "This typically indicates:" >&2
          echo "  • The violations are too numerous/complex for automated fixing" >&2
          echo "  • The codebase needs manual architectural improvements" >&2
          echo "  • The CI failures require design decisions" >&2
          echo "" >&2
          echo "Latest prompt: ${archive_prefix}_prompt.txt" >&2
          echo "Latest response: ${archive_prefix}_response.txt" >&2
          echo "========================================"  >&2
          exit 7
        else
          echo "[xci] Unable to extract diff from Codex response; will retry. (Response saved at ${archive_prefix}_response.txt)" >&2
          ((attempt+=1))
          continue
        fi
        ;;
    esac
  fi

  cp "${patch_file}" "${archive_prefix}_patch.diff"

  # Validate patch doesn't modify protected CI infrastructure
  FORBIDDEN_PATHS="ci_tools/|scripts/ci\.sh|Makefile|xci\.sh|/ci\.py"
  forbidden_files=$(grep "^diff --git" "${patch_file}" | grep -E "${FORBIDDEN_PATHS}" || true)
  if [[ -n "${forbidden_files}" ]]; then
    echo "" >&2
    echo "========================================"  >&2
    echo "[xci] ✗ REJECTED: Patch modifies protected CI infrastructure" >&2
    echo "========================================"  >&2
    echo "Forbidden files detected:" >&2
    echo "${forbidden_files}" >&2
    echo "" >&2
    echo "Only application code should be modified, not CI tools." >&2
    echo "The following paths are protected:" >&2
    echo "  - ci_tools/" >&2
    echo "  - scripts/ci.sh" >&2
    echo "  - Makefile" >&2
    echo "  - xci.sh" >&2
    echo "  - ci.py" >&2
    echo "========================================"  >&2
    ((attempt+=1))
    continue
  fi

  if git apply --check --whitespace=nowarn "${patch_file}" 2>/dev/null; then
    git apply --allow-empty --whitespace=nowarn "${patch_file}"
    echo "[xci] Applied patch from Codex (see ${patch_file})."
  elif git apply --check --reverse --whitespace=nowarn "${patch_file}" 2>/dev/null; then
    echo "[xci] Patch already applied; rerunning CI with existing changes."
    ((attempt+=1))
    continue
  else
    echo "[xci] Patch failed dry-run; will retry with fresh Codex request. (Response saved at ${archive_prefix}_response.txt)" >&2
    ((attempt+=1))
    continue
  fi

  ((attempt+=1))
done
