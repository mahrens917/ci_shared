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
trap 'kill $(jobs -p) 2>/dev/null; exit 130' INT TERM

# Fix Node.js DNS resolution issue (IPv6 causes "Invalid DNS result order" errors)
export NODE_OPTIONS="${NODE_OPTIONS:+${NODE_OPTIONS} }--dns-result-order=ipv4first"

# Safety backstop: absolute max wall-clock time for a single LLM invocation.
# The PTY wrapper handles idle detection internally (LLM_IDLE_TIMEOUT, default 300s).
# This backstop is a last resort in case idle detection fails.
LLM_BACKSTOP_TIMEOUT=1200  # 20 min absolute ceiling

# Run LLM CLI with retry on DNS errors (Bun doesn't respect NODE_OPTIONS)
# Args: repo_dir prompt_file output_log cli model
run_llm_with_dns_retry() {
    local repo_dir="$1"
    local prompt_file="$2"
    local output_log="$3"
    local cli="$4"
    local model="$5"
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
            echo "  Running: python claude_pty_wrapper.py ..."
            timeout "${LLM_BACKSTOP_TIMEOUT}" python "${CI_SHARED_ROOT}/scripts/claude_pty_wrapper.py" "${prompt_file}" "${model}" 2>&1 | tee "${temp_output}" || true
        else
            echo "  Running: codex exec ..."
            codex exec "$(cat "${prompt_file}")" -m "${model}" --dangerously-bypass-approvals-and-sandbox 2>&1 | tee "${temp_output}" || true
        fi

        local output_size
        output_size=$(wc -c < "${temp_output}" 2>/dev/null || echo 0)
        echo "  [DEBUG] ${cli} output: ${output_size} bytes"

        # Check for DNS error
        if grep -q "Invalid DNS result order" "${temp_output}"; then
            echo "  [RETRY] DNS error on attempt ${attempt}/${max_attempts}, waiting ${delay}s..."
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

    echo "  [ERROR] DNS errors persisted after ${max_attempts} attempts"
    return 1
}

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_ROOT}"
export CI_SHARED_ROOT="${PROJECT_ROOT}"

# Read LLM CLI configuration from xci.config.json
XCI_CONFIG="${PROJECT_ROOT}/xci.config.json"
if [[ -f "${XCI_CONFIG}" ]]; then
    LLM_CLI=$(python -c "import json; print(json.load(open('${XCI_CONFIG}'))['codex_cli'])")
    LLM_MODEL=$(python -c "import json; print(json.load(open('${XCI_CONFIG}'))['model'])")
else
    echo "Warning: xci.config.json not found, using defaults" >&2
    LLM_CLI="claude"
    LLM_MODEL="sonnet"
fi

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

    # Run CI with 10-minute timeout per repo
    if timeout 600 bash scripts/ci.sh > "${log_file}" 2>&1; then
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
            echo "  [TIMEOUT] ${repo_name} (CI hung after 10 min)"
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

# Attempt to auto-fix failed repos using Claude
attempt_auto_fixes() {
    echo ""
    echo "============================================"
    echo "AUTO-FIX: ${#fail_repos[@]} failed repo(s)"
    echo "============================================"
    echo ""

    for repo_name in "${fail_repos[@]}"; do
        local repo_dir
        repo_dir=$(get_repo_dir "${repo_name}")

        if [ -z "${repo_dir}" ] || [ ! -d "${repo_dir}" ]; then
            echo "  [SKIP] ${repo_name} - directory not found"
            continue
        fi

        local log_file="${LOGS_DIR}/${repo_name}.log"
        if [ ! -f "${log_file}" ]; then
            echo "  [SKIP] ${repo_name} - no log file"
            continue
        fi

        echo "  [FIXING] ${repo_name}..."

        # Filter out progress/noise lines from the log
        local errors
        errors=$(grep -v -E 'PASSED|^\s*\.\.\.|^\s*(src|tests)/[^ ]+\s+[0-9]+\s+[0-9]+\s+[0-9]+%|\[\s*[0-9]+%\]|^tests/.*::' "${log_file}" || true)

        # Create a temp file with the prompt to avoid escaping issues
        local prompt_file
        prompt_file=$(mktemp)
        cat > "${prompt_file}" << 'PROMPT_EOF'
Implement fixes for all CI errors below. Write the code changes directly to disk. Do NOT plan, do NOT ask for confirmation, do NOT use the plan skill. Edit the files immediately.

Rules:
- Do NOT modify CI config, Makefiles, or pyproject.toml
- Do NOT add noqa, pylint:disable, type:ignore, or similar bypass comments
- Do NOT add fallbacks or backwards-compatibility shims
- Focus on fixing the actual code issues

PROMPT_EOF
        echo "Errors:" >> "${prompt_file}"
        echo "${errors}" >> "${prompt_file}"

        # Display the prompt being sent to the LLM
        echo ""
        echo "━━━ LLM INPUT (${LLM_CLI} ${LLM_MODEL}) ━━━━━━━━━━━━━━━━━━━━━━"
        cat "${prompt_file}"
        echo "━━━ END INPUT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        local start_time
        start_time=$(date +%s)
        echo "━━━ LLM OUTPUT (started $(date '+%H:%M:%S')) ━━━━━━━━━━━━━━━━━━"

        # Run LLM CLI with timeout (5 minutes max), retry on DNS errors
        local llm_output_log="${LOGS_DIR}/${repo_name}.llm_output.log"
        # Protect subshell so failures don't exit the main script
        (
            run_llm_with_dns_retry "${repo_dir}" "${prompt_file}" "${llm_output_log}" "${LLM_CLI}" "${LLM_MODEL}" || true
        ) || echo "  [WARN] ${LLM_CLI} invocation failed for ${repo_name}"
        local elapsed=$(( $(date +%s) - start_time ))
        echo "━━━ END LLM OUTPUT (${elapsed}s elapsed) ━━━━━━━━━━━━━━━━━━━━━"

        rm -f "${prompt_file}"
        echo "  [DONE] ${repo_name}"
    done
}

# Attempt to fix timeout (hanging) repos using Claude
attempt_fix_timeouts() {
    echo ""
    echo "============================================"
    echo "FIX TIMEOUTS: ${#timeout_repos[@]} hung repo(s)"
    echo "============================================"
    echo ""

    for repo_name in "${timeout_repos[@]}"; do
        local repo_dir
        repo_dir=$(get_repo_dir "${repo_name}")

        if [ -z "${repo_dir}" ] || [ ! -d "${repo_dir}" ]; then
            echo "  [SKIP] ${repo_name} - directory not found"
            continue
        fi

        echo "  [FIXING HANG] ${repo_name}..."

        local log_file="${LOGS_DIR}/${repo_name}.log"
        local log_content=""
        if [ -f "${log_file}" ]; then
            # Filter out progress/noise lines from the log
            log_content=$(grep -v -E 'PASSED|^\s*\.\.\.|^\s*(src|tests)/[^ ]+\s+[0-9]+\s+[0-9]+\s+[0-9]+%|\[\s*[0-9]+%\]|^tests/.*::' "${log_file}" || true)
        fi

        # Create a temp file with the prompt focused on fixing hangs
        local prompt_file
        prompt_file=$(mktemp)
        cat > "${prompt_file}" << 'PROMPT_EOF'
CRITICAL: This repository's CI/tests are HANGING (timing out after 10 minutes). Fix the hang issue FIRST.

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

Rules:
- Do NOT modify CI config, Makefiles, or pyproject.toml
- Focus on fixing the HANG issue in test fixtures/conftest
- Add timeouts to cleanup code if needed
- Ensure event loops and connections are forcibly closed

PROMPT_EOF
        echo "=== PARTIAL CI LOG (before timeout) ===" >> "${prompt_file}"
        echo "${log_content}" >> "${prompt_file}"
        echo "=== END LOG ===" >> "${prompt_file}"

        # Display the prompt being sent to the LLM
        echo ""
        echo "━━━ LLM INPUT (${LLM_CLI} ${LLM_MODEL}) ━━━━━━━━━━━━━━━━━━━━━━"
        cat "${prompt_file}"
        echo "━━━ END INPUT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        local start_time
        start_time=$(date +%s)
        echo "━━━ LLM OUTPUT (started $(date '+%H:%M:%S')) ━━━━━━━━━━━━━━━━━━"

        # Run LLM CLI
        local llm_output_log="${LOGS_DIR}/${repo_name}.llm_timeout_fix.log"
        (
            run_llm_with_dns_retry "${repo_dir}" "${prompt_file}" "${llm_output_log}" "${LLM_CLI}" "${LLM_MODEL}" || true
        ) || echo "  [WARN] ${LLM_CLI} invocation failed for ${repo_name}"
        local elapsed=$(( $(date +%s) - start_time ))
        echo "━━━ END LLM OUTPUT (${elapsed}s elapsed) ━━━━━━━━━━━━━━━━━━━━━"

        rm -f "${prompt_file}"
        echo "  [DONE] ${repo_name}"
    done
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

echo "Validating ${#CONSUMER_DIRS[@]} repos in parallel (${PARALLEL_JOBS} jobs, ${NUM_CORES} cores)"
echo "Logs: ${LOGS_DIR}"
echo ""

echo "Pushing config to consuming repositories..."

# Push config to all repos
if [ -f "${PROJECT_ROOT}/scripts/sync_project_configs.py" ]; then
    if ! python "${PROJECT_ROOT}/scripts/sync_project_configs.py" "${CONSUMER_DIRS[@]}"; then
        echo "⚠️  Config sync encountered issues (see above)" >&2
    fi
fi

# Initial validation run
run_validation

display_results

# Fix timeout (hanging) repos FIRST - these are higher priority
if [ "${timeout_count}" -gt 0 ]; then
    attempt_fix_timeouts
fi

# Then fix failed repos
if [ "${fail_count}" -gt 0 ]; then
    attempt_auto_fixes
fi

# Show final results if there were any problems
if [ "${timeout_count}" -gt 0 ] || [ "${fail_count}" -gt 0 ]; then
    display_results
    exit 1
fi

exit 0
