#!/usr/bin/env bash
# Validate all consuming repositories after pushing ci_shared config updates.
# Runs `scripts/ci.sh` in each consuming repo in parallel with live status reporting.
# If validation fails, automatically invokes LLM CLI (configured in xci.config.json) to fix issues, then exits.

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

set -euo pipefail


# Kill background jobs on Ctrl-C or termination
trap 'trap - INT TERM; kill 0; exit 130' INT TERM

# Fix Node.js DNS resolution issue (IPv6 causes "Invalid DNS result order" errors)
export NODE_OPTIONS="${NODE_OPTIONS:+${NODE_OPTIONS} }--dns-result-order=ipv4first"

# Safety backstop: absolute max wall-clock time for a single LLM invocation.
# The PTY wrapper handles idle detection internally (LLM_IDLE_TIMEOUT, default 1800s).
# This backstop is a last resort in case idle detection fails.
LLM_BACKSTOP_TIMEOUT=3600  # 60 min absolute ceiling
export LLM_IDLE_TIMEOUT=300  # 5 min idle timeout (PTY wrapper reads this)

# Timestamped log line
tlog() { echo "  $(date '+%H:%M:%S') $*"; }

# Run LLM CLI with retry on DNS errors (Bun doesn't respect NODE_OPTIONS)
# Args: repo_name repo_dir prompt_file output_log cli model
run_llm_with_dns_retry() {
    local repo_name="$1"
    local repo_dir="$2"
    local prompt_file="$3"
    local output_log="$4"
    local cli="$5"
    local model="$6"
    local max_attempts=3
    local attempt=1
    local delay=2

    cd "${repo_dir}" || return 1

    while [ ${attempt} -le ${max_attempts} ]; do
        # Run CLI and capture to temp file for DNS error detection
        local temp_output
        temp_output=$(mktemp)

        # Build and run CLI command based on which CLI we're using
        if [[ "${cli}" == "claude" ]]; then
            # Use PTY wrapper to avoid Bun hanging without a terminal (AVX issue)
            tlog "[${repo_name}] Running claude_pty_wrapper.py ..."
            timeout "${LLM_BACKSTOP_TIMEOUT}" python "${CI_SHARED_ROOT}/scripts/claude_pty_wrapper.py" "${prompt_file}" "${model}" > "${temp_output}" 2>&1 || true
        else
            tlog "[${repo_name}] Running claude -p - ..."
            claude -p - --model "${model}" < "${prompt_file}" > "${temp_output}" 2>&1 || true
        fi

        local output_size
        output_size=$(wc -c < "${temp_output}" 2>/dev/null || echo 0)
        tlog "[${repo_name}] Output: ${output_size} bytes"

        # Check for DNS error
        if grep -q "Invalid DNS result order" "${temp_output}"; then
            tlog "[${repo_name}] DNS error on attempt ${attempt}/${max_attempts}, waiting ${delay}s..."
            rm -f "${temp_output}"
            sleep ${delay}
            ((attempt++))
            delay=$((delay * 2))
        else
            # Success or non-DNS error - copy to output log
            cp "${temp_output}" "${output_log}"
            rm -f "${temp_output}"
            return 0
        fi
    done

    tlog "[${repo_name}] DNS errors persisted after ${max_attempts} attempts"
    return 1
}

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_ROOT}"
export CI_SHARED_ROOT="${PROJECT_ROOT}"

# Read LLM CLI configuration from xci.config.json
XCI_CONFIG="${PROJECT_ROOT}/xci.config.json"
if [[ ! -f "${XCI_CONFIG}" ]]; then
    echo "ERROR: xci.config.json not found at ${XCI_CONFIG}" >&2
    exit 1
fi
LLM_CLI=$(python -c "import json; print(json.load(open('${XCI_CONFIG}'))['cli'])")
LLM_MODEL=$(python -c "import json; print(json.load(open('${XCI_CONFIG}'))['model'])")

# Calculate parallelism: 50% of available cores, minimum 1
NUM_CORES=$(getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1)
PARALLEL_JOBS=$(( (NUM_CORES + 1) / 2 ))
[[ ${PARALLEL_JOBS} -lt 1 ]] && PARALLEL_JOBS=1

# Create persistent logs directory with timestamp to separate runs
LOGS_DIR="${PROJECT_ROOT}/logs/validate_consumers_$(date +%Y%m%d_%H%M%S)"
export LOGS_DIR
mkdir -p "${LOGS_DIR}"

# Load consuming repos (ci_shared first, then consumers from config)
CONSUMER_DIRS=("${PROJECT_ROOT}")
if CONSUMER_OUTPUT=$(python "${PROJECT_ROOT}/scripts/list_consumers.py" "${PROJECT_ROOT}" 2>&1); then
    mapfile -t EXTRA_DIRS <<< "${CONSUMER_OUTPUT}"
    CONSUMER_DIRS+=("${EXTRA_DIRS[@]}")
else
    echo "Failed to load consuming repositories: ${CONSUMER_OUTPUT}" >&2
    exit 1
fi

if [ "${#CONSUMER_DIRS[@]}" -eq 0 ]; then
    echo "No repositories to validate."
    exit 0
fi

# Global arrays for tracking results (populated by run_validation)
declare -a pass_repos
declare -a skip_repos
declare -a fail_repos
declare -a timeout_repos
declare -a missing_repos
pass_count=0
skip_count=0
fail_count=0
timeout_count=0
missing_count=0

# Run CI for a single repo
run_repo_wrapper() {
    local repo_dir="$1"
    local logs_dir="$2"
    local repo_name="$3"
    local log_file="${logs_dir}/${repo_name}.log"
    local status_file="${logs_dir}/${repo_name}.status"

    echo "  [TESTING] ${repo_name}..."

    if [ ! -d "${repo_dir}" ]; then
        echo "MISSING" > "${status_file}"
        echo "  [MISSING] ${repo_name}"
        return 2
    fi

    if ! cd "${repo_dir}"; then
        echo "MISSING" > "${status_file}"
        echo "  [MISSING] ${repo_name}"
        return 2
    fi

    # Run CI with 30-minute timeout per repo
    if timeout 1800 bash scripts/ci.sh > "${log_file}" 2>&1; then
        # Check if CI was skipped (no changes since last run)
        if grep -q "^SKIPPED:" "${log_file}"; then
            echo "SKIP" > "${status_file}"
            echo "  [SKIP] ${repo_name} (no changes)"
        else
            echo "PASS" > "${status_file}"
            echo "  [PASS] ${repo_name} ✓"
        fi
        return 0
    else
        exit_code=$?
        if [ ${exit_code} -eq 124 ]; then
            echo "TIMEOUT" > "${status_file}"
            echo "  [TIMEOUT] ${repo_name} (CI hung after 30 min)"
        else
            echo "FAIL" > "${status_file}"
            echo "  [FAIL] ${repo_name} ✗"
        fi
        return 1
    fi
}

# Run validation across all repos and populate result arrays
run_validation() {
    local iteration_label="${1:-}"

    if [ -n "${iteration_label}" ]; then
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "${iteration_label}"
        echo ""
    fi

    echo "Running CI in ${#CONSUMER_DIRS[@]} repositories (${PARALLEL_JOBS} at a time)..."
    echo ""

    # Clear previous status files
    for repo_dir in "${CONSUMER_DIRS[@]}"; do
        repo_name=$(basename "${repo_dir}")
        rm -f "${LOGS_DIR}/${repo_name}.status"
    done

    # Run repositories in parallel, respecting PARALLEL_JOBS limit
    local job_pids=()
    local job_names=()

    for repo_dir in "${CONSUMER_DIRS[@]}"; do
        repo_name=$(basename "${repo_dir}")

        # Wait if we have max jobs running
        while [ "${#job_pids[@]}" -ge "${PARALLEL_JOBS}" ]; do
            # Remove completed jobs from tracking
            local new_pids=()
            local new_names=()
            for i in "${!job_pids[@]}"; do
                if kill -0 "${job_pids[$i]}" 2>/dev/null; then
                    new_pids+=("${job_pids[$i]}")
                    new_names+=("${job_names[$i]}")
                fi
            done
            job_pids=("${new_pids[@]}")
            job_names=("${new_names[@]}")

            if [ "${#job_pids[@]}" -ge "${PARALLEL_JOBS}" ]; then
                sleep 0.05
            fi
        done

        # Start new job
        run_repo_wrapper "${repo_dir}" "${LOGS_DIR}" "${repo_name}" &
        new_pid=$!
        echo "  Started ${repo_name} as PID ${new_pid}"
        job_pids+=("${new_pid}")
        job_names+=("${repo_name}")
    done

    # Wait for all remaining jobs
    echo "Waiting for ${#job_pids[@]} jobs: ${job_names[*]}"
    for i in "${!job_pids[@]}"; do
        pid="${job_pids[$i]}"
        name="${job_names[$i]}"
        echo "  Waiting for ${name} (PID ${pid})..."
        wait "$pid" 2>/dev/null || true
        echo "  ${name} done."
    done
    echo "All jobs completed."

    # Reset counters and arrays
    pass_repos=()
    skip_repos=()
    fail_repos=()
    timeout_repos=()
    missing_repos=()
    pass_count=0
    skip_count=0
    fail_count=0
    timeout_count=0
    missing_count=0

    # Collect results
    for repo_dir in "${CONSUMER_DIRS[@]}"; do
        repo_name=$(basename "${repo_dir}")
        status_file="${LOGS_DIR}/${repo_name}.status"

        if [ ! -f "${status_file}" ]; then
            missing_repos+=("${repo_name}")
            ((missing_count++)) || true
            continue
        fi

        status=$(cat "${status_file}" 2>/dev/null || echo "UNKNOWN")
        case "${status}" in
            PASS)
                pass_repos+=("${repo_name}")
                ((pass_count++)) || true
                ;;
            SKIP)
                skip_repos+=("${repo_name}")
                ((skip_count++)) || true
                ;;
            FAIL)
                fail_repos+=("${repo_name}")
                ((fail_count++)) || true
                ;;
            TIMEOUT)
                timeout_repos+=("${repo_name}")
                ((timeout_count++)) || true
                ;;
            MISSING)
                missing_repos+=("${repo_name}")
                ((missing_count++)) || true
                ;;
        esac
    done
}

# Get full path for a repo by name
get_repo_dir() {
    local repo_name="$1"
    for repo_dir in "${CONSUMER_DIRS[@]}"; do
        if [ "$(basename "${repo_dir}")" = "${repo_name}" ]; then
            echo "${repo_dir}"
            return 0
        fi
    done
    return 1
}

# Monitor LLM output log sizes for running jobs (runs in background)
# Args: interval_seconds log_suffix repo_names...
monitor_llm_progress() {
    local interval="$1"
    local log_suffix="$2"
    local logs_dir="$3"
    shift 3
    local repos=("$@")

    while true; do
        sleep "${interval}"
        local parts=()
        for repo in "${repos[@]}"; do
            # Skip repos that have finished
            [ -f "${logs_dir}/${repo}.llm_done" ] && continue
            local log_file="${logs_dir}/${repo}.${log_suffix}"
            if [ -f "${log_file}" ]; then
                local size
                size=$(wc -c < "${log_file}" 2>/dev/null || echo 0)
                if [ "${size}" -ge 1048576 ]; then
                    parts+=("${repo}: $((size / 1048576))MB")
                elif [ "${size}" -ge 1024 ]; then
                    parts+=("${repo}: $((size / 1024))KB")
                else
                    parts+=("${repo}: ${size}B")
                fi
            fi
        done
        if [ "${#parts[@]}" -gt 0 ]; then
            local joined=""
            for i in "${!parts[@]}"; do
                if [ "$i" -gt 0 ]; then
                    joined+=" | "
                fi
                joined+="${parts[$i]}"
            done
            echo "  [progress] ${joined}"
        fi
    done
}

# Fix a single failed repo (designed to run in a subshell)
fix_repo_worker() {
    local repo_name="$1"
    local repo_dir="$2"
    local logs_dir="$3"
    local cli="$4"
    local model="$5"

    local log_file="${logs_dir}/${repo_name}.log"
    local llm_output_log="${logs_dir}/${repo_name}.llm_output.log"
    local prompt_file="${logs_dir}/${repo_name}.llm_prompt.txt"

    if [ -z "${repo_dir}" ] || [ ! -d "${repo_dir}" ]; then
        tlog "[SKIP] ${repo_name} - directory not found"
        return 0
    fi

    if [ ! -f "${log_file}" ]; then
        tlog "[SKIP] ${repo_name} - no log file"
        return 0
    fi

    tlog "[FIXING] ${repo_name}..."
    local start_time
    start_time=$(date +%s)

    local errors
    errors=$(grep -v -E 'PASSED|^\s*\.\.\.|^\s*(src|tests)/[^ ]+\s+[0-9]+\s+[0-9]+\s+[0-9]+%|\[\s*[0-9]+%\]|^tests/.*::' "${log_file}" || true)

    cat > "${prompt_file}" << 'PROMPT_EOF'
Implement fixes for all CI errors below. Write the code changes directly to disk. Do NOT plan, do NOT ask for confirmation, do NOT use the plan skill. Edit the files immediately.

You have full permission to create, modify, and delete files. If a file needs to be
removed, use the Bash tool to run rm. Do NOT ask for approval -- all tool calls are
pre-authorized.

Rules:
- Do NOT modify CI config, Makefiles, or pyproject.toml
- Do NOT add noqa, pylint:disable, type:ignore, or similar bypass comments
- Do NOT add fallbacks or backwards-compatibility shims
- Focus on fixing the actual code issues
- Verify that your fixes do not introduce new violations

CI Limits (all enforced, cannot be changed):
- Functions: max 80 lines, max 7 arguments (ruff PLR0913)
- Classes: max 150 lines, max 15 public / 30 total methods
- Modules: max 600 lines
- Cyclomatic complexity: max 10, cognitive: max 15
- Max branches: 10, max statements: 50
- Inheritance depth: max 2
- When reducing complexity, bundle parameters in a dataclass or existing object
  instead of adding individual arguments

PROMPT_EOF
    echo "Errors:" >> "${prompt_file}"
    echo "${errors}" >> "${prompt_file}"

    # Append previous attempt context if available
    if [ -n "${PREV_LOGS_DIR}" ]; then
        local prev_log="${PREV_LOGS_DIR}/${repo_name}.llm_output.log"
        if [ -f "${prev_log}" ]; then
            local prev_size
            prev_size=$(wc -c < "${prev_log}" 2>/dev/null || echo 0)
            if [ "${prev_size}" -gt 500 ]; then
                echo "" >> "${prompt_file}"
                echo "=== PREVIOUS FIX ATTEMPT (failed - do NOT repeat the same approach) ===" >> "${prompt_file}"
                tail -c 10240 "${prev_log}" >> "${prompt_file}"
                echo "" >> "${prompt_file}"
                echo "=== END PREVIOUS ATTEMPT ===" >> "${prompt_file}"
            fi
        fi
    fi

    (
        run_llm_with_dns_retry "${repo_name}" "${repo_dir}" "${prompt_file}" "${llm_output_log}" "${cli}" "${model}" || true
    ) || tlog "[WARN] ${cli} invocation failed for ${repo_name}"

    # Detect non-start (idle timeout with no useful output)
    if grep -q "idle timeout.*exceeded" "${llm_output_log}" 2>/dev/null; then
        tlog "[NON-START] ${repo_name} - LLM idle timeout with no output"
        touch "${logs_dir}/${repo_name}.llm_nonstart"
    fi

    local elapsed=$(( $(date +%s) - start_time ))
    touch "${logs_dir}/${repo_name}.llm_done"
    tlog "[DONE] ${repo_name} (${elapsed}s)"
}

# Attempt to auto-fix failed repos using Claude (parallel)
attempt_auto_fixes() {
    echo ""
    echo "============================================"
    echo "AUTO-FIX: ${#fail_repos[@]} failed repo(s) (${PARALLEL_JOBS} parallel)"
    echo "============================================"
    echo ""

    # Start progress monitor
    monitor_llm_progress 10 "llm_output.log" "${LOGS_DIR}" "${fail_repos[@]}" &
    local monitor_pid=$!

    local job_pids=()
    local job_names=()

    for repo_name in "${fail_repos[@]}"; do
        local repo_dir
        repo_dir=$(get_repo_dir "${repo_name}")

        # Wait if we have max jobs running
        while [ "${#job_pids[@]}" -ge "${PARALLEL_JOBS}" ]; do
            local new_pids=()
            local new_names=()
            for i in "${!job_pids[@]}"; do
                if kill -0 "${job_pids[$i]}" 2>/dev/null; then
                    new_pids+=("${job_pids[$i]}")
                    new_names+=("${job_names[$i]}")
                fi
            done
            job_pids=("${new_pids[@]}")
            job_names=("${new_names[@]}")
            if [ "${#job_pids[@]}" -ge "${PARALLEL_JOBS}" ]; then
                sleep 0.05
            fi
        done

        fix_repo_worker "${repo_name}" "${repo_dir}" "${LOGS_DIR}" "${LLM_CLI}" "${LLM_MODEL}" &
        job_pids+=("$!")
        job_names+=("${repo_name}")
    done

    # Wait for all remaining jobs
    for i in "${!job_pids[@]}"; do
        wait "${job_pids[$i]}" 2>/dev/null || true
    done

    # Stop progress monitor
    kill "${monitor_pid}" 2>/dev/null || true
    wait "${monitor_pid}" 2>/dev/null || true
}

# Fix a single timeout repo (designed to run in a subshell)
fix_timeout_worker() {
    local repo_name="$1"
    local repo_dir="$2"
    local logs_dir="$3"
    local cli="$4"
    local model="$5"

    local llm_output_log="${logs_dir}/${repo_name}.llm_timeout_fix.log"
    local prompt_file="${logs_dir}/${repo_name}.llm_timeout_prompt.txt"

    if [ -z "${repo_dir}" ] || [ ! -d "${repo_dir}" ]; then
        tlog "[SKIP] ${repo_name} - directory not found"
        return 0
    fi

    tlog "[FIXING HANG] ${repo_name}..."
    local start_time
    start_time=$(date +%s)

    local log_file="${logs_dir}/${repo_name}.log"
    local log_content=""
    if [ -f "${log_file}" ]; then
        log_content=$(grep -v -E 'PASSED|^\s*\.\.\.|^\s*(src|tests)/[^ ]+\s+[0-9]+\s+[0-9]+\s+[0-9]+%|\[\s*[0-9]+%\]|^tests/.*::' "${log_file}" || true)
    fi

    cat > "${prompt_file}" << 'PROMPT_EOF'
CRITICAL: This repository's CI/tests are HANGING (timing out after 30 minutes). Fix the hang issue FIRST.

Common causes of CI hangs:
1. Asyncio event loops not being closed properly in test fixtures
2. Redis/database connections not being cleaned up after tests
3. Background threads/processes started by tests that don't exit
4. pytest fixtures with scope="session" that hang during teardown
5. Mocks that intercept asyncio.Event.wait() or similar blocking calls incorrectly

Focus on:
- tests/conftest.py - look for session-scoped fixtures and cleanup code
- Any fixture that creates event loops, Redis connections, or spawns processes
- Ensure all async resources are properly closed with timeouts

The partial CI log (before timeout) is below. Look for clues about what was running when it hung.

Write the code changes directly to disk. Do NOT plan, do NOT ask for confirmation. Edit the files immediately.

You have full permission to create, modify, and delete files. If a file needs to be
removed, use the Bash tool to run rm. Do NOT ask for approval -- all tool calls are
pre-authorized.

Rules:
- Do NOT modify CI config, Makefiles, or pyproject.toml
- Focus on fixing the HANG issue in test fixtures/conftest
- Add timeouts to cleanup code if needed
- Ensure event loops and connections are forcibly closed
- Verify that your fixes do not introduce new violations

CI Limits (all enforced, cannot be changed):
- Functions: max 80 lines, max 7 arguments (ruff PLR0913)
- Classes: max 150 lines, max 15 public / 30 total methods
- Modules: max 600 lines
- Cyclomatic complexity: max 10, cognitive: max 15
- Max branches: 10, max statements: 50
- Inheritance depth: max 2
- When reducing complexity, bundle parameters in a dataclass or existing object
  instead of adding individual arguments

PROMPT_EOF
    echo "=== PARTIAL CI LOG (before timeout) ===" >> "${prompt_file}"
    echo "${log_content}" >> "${prompt_file}"
    echo "=== END LOG ===" >> "${prompt_file}"

    # Append previous attempt context if available
    if [ -n "${PREV_LOGS_DIR}" ]; then
        local prev_log="${PREV_LOGS_DIR}/${repo_name}.llm_timeout_fix.log"
        if [ -f "${prev_log}" ]; then
            local prev_size
            prev_size=$(wc -c < "${prev_log}" 2>/dev/null || echo 0)
            if [ "${prev_size}" -gt 500 ]; then
                echo "" >> "${prompt_file}"
                echo "=== PREVIOUS FIX ATTEMPT (failed - do NOT repeat the same approach) ===" >> "${prompt_file}"
                tail -c 10240 "${prev_log}" >> "${prompt_file}"
                echo "" >> "${prompt_file}"
                echo "=== END PREVIOUS ATTEMPT ===" >> "${prompt_file}"
            fi
        fi
    fi

    (
        run_llm_with_dns_retry "${repo_name}" "${repo_dir}" "${prompt_file}" "${llm_output_log}" "${cli}" "${model}" || true
    ) || tlog "[WARN] ${cli} invocation failed for ${repo_name}"

    # Detect non-start (idle timeout with no useful output)
    if grep -q "idle timeout.*exceeded" "${llm_output_log}" 2>/dev/null; then
        tlog "[NON-START] ${repo_name} - LLM idle timeout with no output"
        touch "${logs_dir}/${repo_name}.llm_nonstart"
    fi

    local elapsed=$(( $(date +%s) - start_time ))
    touch "${logs_dir}/${repo_name}.llm_done"
    tlog "[DONE] ${repo_name} (${elapsed}s)"
}

# Attempt to fix timeout (hanging) repos using Claude (parallel)
attempt_fix_timeouts() {
    echo ""
    echo "============================================"
    echo "FIX TIMEOUTS: ${#timeout_repos[@]} hung repo(s) (${PARALLEL_JOBS} parallel)"
    echo "============================================"
    echo ""

    # Start progress monitor
    monitor_llm_progress 10 "llm_timeout_fix.log" "${LOGS_DIR}" "${timeout_repos[@]}" &
    local monitor_pid=$!

    local job_pids=()
    local job_names=()

    for repo_name in "${timeout_repos[@]}"; do
        local repo_dir
        repo_dir=$(get_repo_dir "${repo_name}")

        # Wait if we have max jobs running
        while [ "${#job_pids[@]}" -ge "${PARALLEL_JOBS}" ]; do
            local new_pids=()
            local new_names=()
            for i in "${!job_pids[@]}"; do
                if kill -0 "${job_pids[$i]}" 2>/dev/null; then
                    new_pids+=("${job_pids[$i]}")
                    new_names+=("${job_names[$i]}")
                fi
            done
            job_pids=("${new_pids[@]}")
            job_names=("${new_names[@]}")
            if [ "${#job_pids[@]}" -ge "${PARALLEL_JOBS}" ]; then
                sleep 0.05
            fi
        done

        fix_timeout_worker "${repo_name}" "${repo_dir}" "${LOGS_DIR}" "${LLM_CLI}" "${LLM_MODEL}" &
        job_pids+=("$!")
        job_names+=("${repo_name}")
    done

    # Wait for all remaining jobs
    for i in "${!job_pids[@]}"; do
        wait "${job_pids[$i]}" 2>/dev/null || true
    done

    # Stop progress monitor
    kill "${monitor_pid}" 2>/dev/null || true
    wait "${monitor_pid}" 2>/dev/null || true
}

# Display results summary
display_results() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Results Summary:"
    echo ""

    for repo in "${pass_repos[@]}"; do
        echo "  ✓ ${repo}"
    done

    for repo in "${skip_repos[@]}"; do
        echo "  ○ ${repo} (skipped)"
    done

    for repo in "${fail_repos[@]}"; do
        echo "  ✗ ${repo}"
    done

    for repo in "${timeout_repos[@]}"; do
        echo "  ⏱ ${repo} (hung)"
    done

    for repo in "${missing_repos[@]}"; do
        echo "  ? ${repo}"
    done

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    local total=$((pass_count + skip_count + fail_count + timeout_count + missing_count))
    echo "Summary: ${pass_count}/${total} passed, ${skip_count} skipped"
    if [ "${fail_count}" -gt 0 ]; then
        echo "         ${fail_count} failed"
    fi
    if [ "${timeout_count}" -gt 0 ]; then
        echo "         ${timeout_count} timed out (hung)"
    fi
    if [ "${missing_count}" -gt 0 ]; then
        echo "         ${missing_count} missing"
    fi

    if [ "${fail_count}" -gt 0 ] || [ "${timeout_count}" -gt 0 ]; then
        echo ""
        echo "Problem repo logs:"
        local logs_relative
        logs_relative=$(echo "${LOGS_DIR}" | sed "s|${PROJECT_ROOT}/||")
        for repo in "${fail_repos[@]}"; do
            echo "  ${repo}: ${logs_relative}/${repo}.log"
        done
        for repo in "${timeout_repos[@]}"; do
            echo "  ${repo}: ${logs_relative}/${repo}.log (TIMEOUT)"
        done
    fi
}

# ============================================================================
# Main execution
# ============================================================================

# Parse flags
FIX_ONLY=false
FORCE=false
for arg in "$@"; do
    case "${arg}" in
        --fix-only)
            FIX_ONLY=true
            ;;
        --force)
            FORCE=true
            ;;
    esac
done

if [ "${FORCE}" = true ]; then
    export CI_FORCE=1
fi

# --fix-only: skip CI, load results from most recent logs, run one fix pass
if [ "${FIX_ONLY}" = true ]; then
    LATEST_LOGS=$(find "${PROJECT_ROOT}/logs" -maxdepth 1 -type d -name "validate_consumers_*" | sort | tail -1)
    if [ -z "${LATEST_LOGS}" ]; then
        echo "ERROR: No previous logs found in ${PROJECT_ROOT}/logs/" >&2
        exit 1
    fi
    LOGS_DIR="${LATEST_LOGS}"
    export LOGS_DIR
    echo "Fix-only mode: using logs from ${LOGS_DIR}"
    echo ""

    # Populate fail/timeout arrays from existing status files
    fail_repos=()
    timeout_repos=()
    fail_count=0
    timeout_count=0

    for repo_dir in "${CONSUMER_DIRS[@]}"; do
        repo_name=$(basename "${repo_dir}")
        status_file="${LOGS_DIR}/${repo_name}.status"
        [ ! -f "${status_file}" ] && continue
        status=$(cat "${status_file}" 2>/dev/null)
        case "${status}" in
            FAIL)
                fail_repos+=("${repo_name}")
                ((fail_count++)) || true
                ;;
            TIMEOUT)
                timeout_repos+=("${repo_name}")
                ((timeout_count++)) || true
                ;;
        esac
    done

    if [ "${fail_count}" -eq 0 ] && [ "${timeout_count}" -eq 0 ]; then
        echo "No failed or timed-out repos in ${LOGS_DIR}. Nothing to fix."
        exit 0
    fi

    echo "Found ${fail_count} failed, ${timeout_count} timed-out repos"

    if [ "${#timeout_repos[@]}" -gt 0 ]; then
        attempt_fix_timeouts
    fi
    if [ "${#fail_repos[@]}" -gt 0 ]; then
        attempt_auto_fixes
    fi

    echo ""
    echo "Fix-only pass complete. Re-run without --fix-only to validate."
    exit 0
fi

echo "Validating ${#CONSUMER_DIRS[@]} repos in parallel (${PARALLEL_JOBS} jobs, ${NUM_CORES} cores)"
echo "Logs: ${LOGS_DIR}"
echo ""


echo "Pushing config to consuming repositories..."

# Push config to all repos (once, before loop)
if [ -f "${PROJECT_ROOT}/scripts/sync_project_configs.py" ]; then
    if ! python "${PROJECT_ROOT}/scripts/sync_project_configs.py" "${CONSUMER_DIRS[@]}"; then
        echo "⚠️  Config sync encountered issues (see above)" >&2
    fi
fi

# Consecutive LLM fix attempt tracking per repo
declare -A repo_fix_counts
MAX_FIX_ATTEMPTS=5
LOOP_SLEEP=300
PREV_LOGS_DIR=""
export PREV_LOGS_DIR

loop_iteration=0

while true; do
    ((loop_iteration++)) || true

    # Fresh logs directory for each iteration after the first
    if [ "${loop_iteration}" -gt 1 ]; then
        PREV_LOGS_DIR="${LOGS_DIR}"
        LOGS_DIR="${PROJECT_ROOT}/logs/validate_consumers_$(date +%Y%m%d_%H%M%S)"
        export LOGS_DIR
        mkdir -p "${LOGS_DIR}"
    fi

    # Run validation across all repos
    if [ "${loop_iteration}" -eq 1 ]; then
        run_validation
    else
        run_validation "Loop iteration ${loop_iteration}"
    fi

    display_results

    # Reset fix counts for repos that passed or were skipped
    for repo in "${pass_repos[@]}"; do
        repo_fix_counts["${repo}"]=0
    done
    for repo in "${skip_repos[@]}"; do
        repo_fix_counts["${repo}"]=0
    done

    # All repos skipped — nothing left to validate, stop
    if [ "${skip_count}" -eq "${#CONSUMER_DIRS[@]}" ]; then
        echo ""
        echo "All repos skipped (no changes). Nothing to validate."
        exit 0
    fi

    # All green — sleep and re-check
    if [ "${fail_count}" -eq 0 ] && [ "${timeout_count}" -eq 0 ]; then
        echo ""
        echo "All repos green. Sleeping ${LOOP_SLEEP}s..."
        sleep "${LOOP_SLEEP}"
        continue
    fi

    # Determine which failing repos are still eligible for LLM fixes
    fixable_timeout_repos=()
    fixable_fail_repos=()
    exhausted_repos=()

    for repo in "${timeout_repos[@]}"; do
        count="${repo_fix_counts["${repo}"]:-0}"
        if [ "${count}" -lt "${MAX_FIX_ATTEMPTS}" ]; then
            fixable_timeout_repos+=("${repo}")
        else
            exhausted_repos+=("${repo}")
        fi
    done

    for repo in "${fail_repos[@]}"; do
        count="${repo_fix_counts["${repo}"]:-0}"
        if [ "${count}" -lt "${MAX_FIX_ATTEMPTS}" ]; then
            fixable_fail_repos+=("${repo}")
        else
            exhausted_repos+=("${repo}")
        fi
    done

    if [ "${#exhausted_repos[@]}" -gt 0 ]; then
        echo ""
        echo "Repos at max LLM fix attempts (${MAX_FIX_ATTEMPTS}):"
        for repo in "${exhausted_repos[@]}"; do
            echo "  ⊘ ${repo} (${repo_fix_counts["${repo}"]} attempts)"
        done
    fi

    # Swap in only fixable repos for fix functions
    saved_fail_repos=("${fail_repos[@]}")
    saved_timeout_repos=("${timeout_repos[@]}")
    timeout_repos=("${fixable_timeout_repos[@]}")
    fail_repos=("${fixable_fail_repos[@]}")

    # Fix timeout (hanging) repos first — higher priority
    if [ "${#timeout_repos[@]}" -gt 0 ]; then
        attempt_fix_timeouts
    fi

    # Then fix failed repos
    if [ "${#fail_repos[@]}" -gt 0 ]; then
        attempt_auto_fixes
    fi

    # Increment fix counts for repos we just attempted (skip non-starts)
    for repo in "${timeout_repos[@]}"; do
        if [ -f "${LOGS_DIR}/${repo}.llm_nonstart" ]; then
            tlog "[NON-START] ${repo} - not counting toward fix attempts"
        else
            repo_fix_counts["${repo}"]=$(( ${repo_fix_counts["${repo}"]:-0} + 1 ))
        fi
    done
    for repo in "${fail_repos[@]}"; do
        if [ -f "${LOGS_DIR}/${repo}.llm_nonstart" ]; then
            tlog "[NON-START] ${repo} - not counting toward fix attempts"
        else
            repo_fix_counts["${repo}"]=$(( ${repo_fix_counts["${repo}"]:-0} + 1 ))
        fi
    done

    # Restore original arrays
    fail_repos=("${saved_fail_repos[@]}")
    timeout_repos=("${saved_timeout_repos[@]}")

    # If no repos are fixable by LLM, exit — nothing more we can do
    if [ "${#fixable_fail_repos[@]}" -eq 0 ] && [ "${#fixable_timeout_repos[@]}" -eq 0 ]; then
        echo ""
        echo "All failing repos exhausted LLM fix attempts (${MAX_FIX_ATTEMPTS}). Exiting."
        exit 1
    fi
    continue
done
