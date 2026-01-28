#!/usr/bin/env bash
# Validate all consuming repositories after pushing ci_shared config updates.
# Runs `scripts/ci.sh` in each consuming repo in parallel with live status reporting.

set -euo pipefail

# Kill any existing instances and orphaned processes from previous runs
cleanup_previous_runs() {
    local my_pid=$$
    local pids

    pids=$(pgrep -f "validate_consumers.sh" 2>/dev/null || true)
    for pid in $pids; do
        if [ "$pid" != "$my_pid" ] && [ "$pid" != "$PPID" ]; then
            kill -9 "$pid" 2>/dev/null && echo "Killed previous validate_consumers.sh (PID $pid)" || true
        fi
    done

    pkill -9 -f "scripts/ci\.sh" 2>/dev/null && echo "Killed orphaned ci.sh processes" || true
    pkill -9 -f "pytest.*--cov" 2>/dev/null && echo "Killed orphaned pytest processes" || true
    pkill -9 -f "pyright.*src" 2>/dev/null && echo "Killed orphaned pyright processes" || true
    pkill -9 -f "pylint.*src" 2>/dev/null && echo "Killed orphaned pylint processes" || true
    pkill -9 -f "ruff check" 2>/dev/null && echo "Killed orphaned ruff processes" || true
}

cleanup_previous_runs

trap 'kill $(jobs -p) 2>/dev/null; exit 130' INT TERM

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_ROOT}"
export CI_SHARED_ROOT="${PROJECT_ROOT}"

# Read LLM CLI configuration
XCI_CONFIG="${PROJECT_ROOT}/xci.config.json"
if [[ -f "${XCI_CONFIG}" ]]; then
    LLM_CLI=$(python -c "import json; print(json.load(open('${XCI_CONFIG}'))['codex_cli'])")
    LLM_MODEL=$(python -c "import json; print(json.load(open('${XCI_CONFIG}'))['model'])")
else
    echo "Warning: xci.config.json not found"
    LLM_CLI="claude"
    LLM_MODEL="sonnet"
fi

# Parallelism: 50% of cores
NUM_CORES=$(sysctl -n hw.ncpu 2>/dev/null || echo 4)
PARALLEL_JOBS=$(( (NUM_CORES + 1) / 2 ))

# Logs directory
LOGS_DIR="${PROJECT_ROOT}/logs/validate_consumers_$(date +%Y%m%d_%H%M%S)"
mkdir -p "${LOGS_DIR}"

echo "Logs: ${LOGS_DIR}"
echo "LLM: ${LLM_CLI} ${LLM_MODEL}"
echo "Parallelism: ${PARALLEL_JOBS} jobs (${NUM_CORES} cores)"
echo ""

# Load consuming repos
CONSUMER_DIRS=("${PROJECT_ROOT}")
if CONSUMER_OUTPUT=$(python "${PROJECT_ROOT}/scripts/list_consumers.py" "${PROJECT_ROOT}" 2>&1); then
    mapfile -t EXTRA_DIRS <<< "${CONSUMER_OUTPUT}"
    CONSUMER_DIRS+=("${EXTRA_DIRS[@]}")
fi

echo "Repos: ${#CONSUMER_DIRS[@]}"
echo ""

# Result tracking
declare -a pass_repos skip_repos fail_repos
pass_count=0 skip_count=0 fail_count=0

# Run CI for one repo
run_repo() {
    local repo_dir="$1"
    local repo_name="$2"
    local log_file="${LOGS_DIR}/${repo_name}.log"
    local status_file="${LOGS_DIR}/${repo_name}.status"

    echo "  [TESTING] ${repo_name}..."

    if [ ! -d "${repo_dir}" ]; then
        echo "MISSING" > "${status_file}"
        echo "  [MISSING] ${repo_name}"
        return
    fi

    cd "${repo_dir}" || return

    if bash scripts/ci.sh > "${log_file}" 2>&1; then
        if grep -q "^SKIPPED:" "${log_file}"; then
            echo "SKIP" > "${status_file}"
            echo "  [SKIP] ${repo_name}"
        else
            echo "PASS" > "${status_file}"
            echo "  [PASS] ${repo_name}"
        fi
    else
        echo "FAIL" > "${status_file}"
        echo "  [FAIL] ${repo_name}"
    fi
}

# Config sync
echo "=== CONFIG SYNC ==="
if [ -f "${PROJECT_ROOT}/scripts/sync_project_configs.py" ]; then
    python "${PROJECT_ROOT}/scripts/sync_project_configs.py" "${CONSUMER_DIRS[@]}" || echo "Config sync had issues"
fi
echo ""

# Run validation
echo "=== VALIDATION ==="
job_pids=()

for repo_dir in "${CONSUMER_DIRS[@]}"; do
    repo_name=$(basename "${repo_dir}")

    # Wait if at max parallel jobs
    while [ "${#job_pids[@]}" -ge "${PARALLEL_JOBS}" ]; do
        new_pids=()
        for pid in "${job_pids[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                new_pids+=("$pid")
            fi
        done
        job_pids=("${new_pids[@]}")
        [ "${#job_pids[@]}" -ge "${PARALLEL_JOBS}" ] && sleep 0.1
    done

    run_repo "${repo_dir}" "${repo_name}" &
    job_pids+=($!)
done

echo "Waiting for jobs to finish..."
for pid in "${job_pids[@]}"; do
    wait "$pid" 2>/dev/null || true
done
echo "All jobs done."
echo ""

# Collect results
echo "=== RESULTS ==="
for repo_dir in "${CONSUMER_DIRS[@]}"; do
    repo_name=$(basename "${repo_dir}")
    status_file="${LOGS_DIR}/${repo_name}.status"
    status=$(cat "${status_file}" 2>/dev/null || echo "UNKNOWN")

    case "${status}" in
        PASS) pass_repos+=("${repo_name}"); ((pass_count++)) || true ;;
        SKIP) skip_repos+=("${repo_name}"); ((skip_count++)) || true ;;
        FAIL) fail_repos+=("${repo_name}"); ((fail_count++)) || true ;;
    esac
done

for r in "${pass_repos[@]:-}"; do [ -n "$r" ] && echo "  PASS: $r"; done
for r in "${skip_repos[@]:-}"; do [ -n "$r" ] && echo "  SKIP: $r"; done
for r in "${fail_repos[@]:-}"; do [ -n "$r" ] && echo "  FAIL: $r"; done

total=$((pass_count + skip_count + fail_count))
echo ""
echo "Summary: ${pass_count}/${total} passed, ${skip_count} skipped, ${fail_count} failed"
echo ""

# Auto-fix if failures
if [ "${fail_count}" -gt 0 ]; then
    echo "=== AUTO-FIX ==="
    for repo_name in "${fail_repos[@]}"; do
        echo ""
        echo "--- Fixing: ${repo_name} ---"

        repo_dir=""
        for d in "${CONSUMER_DIRS[@]}"; do
            [ "$(basename "$d")" = "${repo_name}" ] && repo_dir="$d" && break
        done

        [ -z "${repo_dir}" ] && echo "  Directory not found, skipping" && continue

        log_file="${LOGS_DIR}/${repo_name}.log"
        [ ! -f "${log_file}" ] && echo "  No log file, skipping" && continue

        # Extract errors
        log_content=$(grep -iE "(error|fail|exception|traceback|FAILED|warning:|fatal)" "${log_file}" 2>/dev/null | head -300 || tail -100 "${log_file}")

        prompt_file=$(mktemp)
        cat > "${prompt_file}" << EOF
Fix all CI errors. The CI log output is below.
Do not ask questions - just fix the code.

Rules:
- Do NOT modify CI config, Makefiles, or pyproject.toml
- Do NOT add noqa, pylint:disable, type:ignore comments
- Fix the actual code issues directly

=== CI LOG ===
${log_content}
=== END LOG ===
EOF

        echo "Prompt size: $(wc -c < "${prompt_file}") bytes"
        echo "Invoking ${LLM_CLI}..."

        cd "${repo_dir}"
        llm_log="${LOGS_DIR}/${repo_name}.llm.log"

        if [[ "${LLM_CLI}" == "claude" ]]; then
            python "${CI_SHARED_ROOT}/scripts/claude_pty_wrapper.py" "${prompt_file}" "${LLM_MODEL}" 2>&1 | tee "${llm_log}" || echo "LLM failed"
        else
            codex exec "$(cat "${prompt_file}")" -m "${LLM_MODEL}" --dangerously-bypass-approvals-and-sandbox 2>&1 | tee "${llm_log}" || echo "LLM failed"
        fi

        rm -f "${prompt_file}"
        echo "--- Done: ${repo_name} ---"
    done
    echo ""
    echo "Auto-fix complete. Re-run to validate fixes."
    exit 1
fi

echo "All repos passed!"
exit 0
