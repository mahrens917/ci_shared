#!/usr/bin/env bash
# Validate all consuming repositories after pushing ci_shared config updates.
# Runs `scripts/ci.sh` in each consuming repo in parallel with live status reporting.
# If validation fails, automatically invokes LLM CLI (configured in xci.config.json) to fix issues, then exits.

set -euo pipefail

# Kill background jobs on Ctrl-C or termination
trap 'kill $(jobs -p) 2>/dev/null; exit 130' INT TERM

# ============================================================================
# Diagnostic logging functions
# ============================================================================
DIAG_LOG=""
SCRIPT_START_TIME=$(date +%s%3N 2>/dev/null || date +%s)

diag_init() {
    local logs_dir="$1"
    DIAG_LOG="${logs_dir}/diagnostics.log"
    echo "=== DIAGNOSTIC LOG ===" > "${DIAG_LOG}"
    echo "Started: $(date '+%Y-%m-%d %H:%M:%S')" >> "${DIAG_LOG}"
    echo "" >> "${DIAG_LOG}"
}

diag() {
    local msg="$1"
    local now
    now=$(date +%s%3N 2>/dev/null || date +%s)
    local elapsed=$((now - SCRIPT_START_TIME))
    local timestamp
    timestamp=$(date '+%H:%M:%S')
    echo "[${timestamp}] [+${elapsed}ms] ${msg}" | tee -a "${DIAG_LOG}" >&2
}

diag_env() {
    echo "" >> "${DIAG_LOG}"
    echo "=== ENVIRONMENT ===" >> "${DIAG_LOG}"
    echo "USER: ${USER:-unknown}" >> "${DIAG_LOG}"
    echo "SHELL: ${SHELL:-unknown}" >> "${DIAG_LOG}"
    echo "PWD: ${PWD}" >> "${DIAG_LOG}"
    echo "" >> "${DIAG_LOG}"

    echo "=== LLM-RELATED ENV VARS ===" >> "${DIAG_LOG}"
    env | grep -iE "^(ANTHROPIC|CLAUDE|LLM|OPENAI|NODE_)" >> "${DIAG_LOG}" 2>/dev/null || echo "(none found)" >> "${DIAG_LOG}"
    echo "" >> "${DIAG_LOG}"

    echo "=== CLAUDE CLI INFO ===" >> "${DIAG_LOG}"
    if command -v claude &>/dev/null; then
        echo "claude path: $(which claude)" >> "${DIAG_LOG}"
        claude --version >> "${DIAG_LOG}" 2>&1 || echo "version check failed" >> "${DIAG_LOG}"
    else
        echo "claude: not found in PATH" >> "${DIAG_LOG}"
    fi
    echo "" >> "${DIAG_LOG}"
}

diag_section() {
    local section="$1"
    echo "" >> "${DIAG_LOG}"
    echo "=== ${section} ===" >> "${DIAG_LOG}"
    diag ">>> ${section}"
}

# ============================================================================
# Main script
# ============================================================================

# Fix Node.js DNS resolution issue (IPv6 causes "Invalid DNS result order" errors)
export NODE_OPTIONS="${NODE_OPTIONS:+${NODE_OPTIONS} }--dns-result-order=ipv4first"

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

    diag "run_llm_with_dns_retry: repo=${repo_dir}, cli=${cli}, model=${model}"

    cd "${repo_dir}" || return 1

    while [ ${attempt} -le ${max_attempts} ]; do
        # Run CLI and capture to temp file for DNS error detection
        local temp_output
        temp_output=$(mktemp)
        local cmd_start
        cmd_start=$(date +%s%3N 2>/dev/null || date +%s)

        diag "LLM attempt ${attempt}/${max_attempts} starting..."

        # Build and run CLI command based on which CLI we're using
        if [[ "${cli}" == "claude" ]]; then
            # Use PTY wrapper to prevent Bun AVX hang when stdout is not a TTY
            diag "Invoking: timeout 300 python claude_pty_wrapper.py <prompt> ${model}"
            diag "ANTHROPIC_API_KEY set: $([ -n \"${ANTHROPIC_API_KEY:-}\" ] && echo 'yes' || echo 'no')"
            diag "LLM_PROVIDER_KEY set: $([ -n \"${LLM_PROVIDER_KEY:-}\" ] && echo 'yes' || echo 'no')"

            # Capture stderr separately for diagnostics
            local stderr_file
            stderr_file=$(mktemp)
            timeout 300 python "${CI_SHARED_ROOT}/scripts/claude_pty_wrapper.py" "${prompt_file}" "${model}" > "${temp_output}" 2>"${stderr_file}" || true

            local cmd_end
            cmd_end=$(date +%s%3N 2>/dev/null || date +%s)
            local cmd_elapsed=$((cmd_end - cmd_start))
            diag "LLM command completed in ${cmd_elapsed}ms"

            # Log stderr if non-empty
            if [ -s "${stderr_file}" ]; then
                diag "LLM stderr output:"
                cat "${stderr_file}" >> "${DIAG_LOG}"
                cat "${stderr_file}" >&2
            fi
            rm -f "${stderr_file}"
        else
            diag "Invoking: timeout 300 codex exec ... -m ${model}"
            timeout 300 codex exec "$(cat "${prompt_file}")" -m "${model}" --dangerously-bypass-approvals-and-sandbox > "${temp_output}" 2>&1 || true

            local cmd_end
            cmd_end=$(date +%s%3N 2>/dev/null || date +%s)
            local cmd_elapsed=$((cmd_end - cmd_start))
            diag "LLM command completed in ${cmd_elapsed}ms"
        fi

        local output_size
        output_size=$(wc -c < "${temp_output}" 2>/dev/null || echo 0)
        diag "LLM output size: ${output_size} bytes"

        # Check for specific error patterns
        if grep -q "Invalid DNS result order" "${temp_output}"; then
            diag "DNS error detected, will retry"
            echo "  [RETRY] DNS error on attempt ${attempt}/${max_attempts}, waiting ${delay}s..."
            rm -f "${temp_output}"
            sleep ${delay}
            ((attempt++))
            delay=$((delay * 2))
        elif grep -q "Pre-flight check" "${temp_output}"; then
            diag "Pre-flight check message detected in output"
            cat "${temp_output}" >> "${DIAG_LOG}"
            cat "${temp_output}" | tee "${output_log}"
            rm -f "${temp_output}"
            return 0
        else
            # Success or non-DNS error - output and exit
            diag "LLM completed successfully"
            cat "${temp_output}" | tee "${output_log}"
            rm -f "${temp_output}"
            return 0
        fi
    done

    diag "ERROR: DNS errors persisted after ${max_attempts} attempts"
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
declare -a missing_repos
pass_count=0
skip_count=0
fail_count=0
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

    if bash scripts/ci.sh > "${log_file}" 2>&1; then
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
        echo "FAIL" > "${status_file}"
        echo "  [FAIL] ${repo_name} ✗"
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
        job_pids+=($!)
        job_names+=("${repo_name}")
    done

    # Wait for all remaining jobs
    for pid in "${job_pids[@]}"; do
        wait "$pid" 2>/dev/null || true
    done

    # Reset counters and arrays
    pass_repos=()
    skip_repos=()
    fail_repos=()
    missing_repos=()
    pass_count=0
    skip_count=0
    fail_count=0
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
    echo "Attempting auto-fix for ${#fail_repos[@]} failed repo(s)..."
    echo ""

    diag "attempt_auto_fixes: ${#fail_repos[@]} repos to fix"

    for repo_name in "${fail_repos[@]}"; do
        diag "Processing repo: ${repo_name}"
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

        # Create a temp file with the prompt - tell Claude to read the log file itself
        local prompt_file
        prompt_file=$(mktemp)
        cat > "${prompt_file}" << PROMPT_EOF
Implement fixes for all CI errors. The CI log is at: ${log_file}
Read that file to understand what failed, then fix the code.

Rules:
- Do NOT modify CI config, Makefiles, or pyproject.toml
- Do NOT add noqa, pylint:disable, type:ignore, or similar bypass comments
- Do NOT add fallbacks or backwards-compatibility shims
- Focus on fixing the actual code issues
PROMPT_EOF

        # Display the prompt being sent to the LLM
        echo ""
        echo "━━━ LLM INPUT (${LLM_CLI} ${LLM_MODEL}) ━━━━━━━━━━━━━━━━━━━━━━"
        cat "${prompt_file}"
        echo "━━━ END INPUT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        local start_time
        start_time=$(date +%s)
        echo "━━━ LLM OUTPUT (started $(date '+%H:%M:%S')) ━━━━━━━━━━━━━━━━━━"

        diag "LLM invocation starting for ${repo_name}"

        # Run LLM CLI with timeout (5 minutes max), retry on DNS errors
        local llm_output_log="${LOGS_DIR}/${repo_name}.llm_output.log"
        # Protect subshell so failures don't exit the main script
        (
            run_llm_with_dns_retry "${repo_dir}" "${prompt_file}" "${llm_output_log}" "${LLM_CLI}" "${LLM_MODEL}" || true
        ) || echo "  [WARN] ${LLM_CLI} invocation failed for ${repo_name}"
        local elapsed=$(( $(date +%s) - start_time ))
        echo "━━━ END LLM OUTPUT (${elapsed}s elapsed) ━━━━━━━━━━━━━━━━━━━━━"

        diag "LLM invocation completed for ${repo_name} in ${elapsed}s"

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

    for repo in "${missing_repos[@]}"; do
        echo "  ? ${repo}"
    done

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    local total=$((pass_count + skip_count + fail_count + missing_count))
    echo "Summary: ${pass_count}/${total} passed, ${skip_count} skipped"
    if [ "${fail_count}" -gt 0 ]; then
        echo "         ${fail_count} failed"
    fi
    if [ "${missing_count}" -gt 0 ]; then
        echo "         ${missing_count} missing"
    fi

    if [ "${fail_count}" -gt 0 ]; then
        echo ""
        echo "Failed repo logs:"
        local logs_relative
        logs_relative=$(echo "${LOGS_DIR}" | sed "s|${PROJECT_ROOT}/||")
        for repo in "${fail_repos[@]}"; do
            echo "  ${repo}: ${logs_relative}/${repo}.log"
        done
    fi
}

# ============================================================================
# Main execution
# ============================================================================

# Initialize diagnostic logging early (before LOGS_DIR is created, use temp)
TEMP_DIAG_LOG=$(mktemp)
DIAG_LOG="${TEMP_DIAG_LOG}"

diag "Script starting"
diag "PROJECT_ROOT: ${PROJECT_ROOT}"
diag "LLM_CLI: ${LLM_CLI}, LLM_MODEL: ${LLM_MODEL}"
diag "PARALLEL_JOBS: ${PARALLEL_JOBS}, NUM_CORES: ${NUM_CORES}"

echo "Validating ${PARALLEL_JOBS} consuming repos in parallel (${NUM_CORES} cores available)"
echo ""

# Now initialize proper diagnostic log in LOGS_DIR
diag_init "${LOGS_DIR}"
# Copy temp log contents
cat "${TEMP_DIAG_LOG}" >> "${DIAG_LOG}"
rm -f "${TEMP_DIAG_LOG}"

diag_env
diag_section "CONFIG SYNC"

echo "Pushing config to consuming repositories..."

# Push config to all repos
if [ -f "${PROJECT_ROOT}/scripts/sync_project_configs.py" ]; then
    if ! python "${PROJECT_ROOT}/scripts/sync_project_configs.py" "${CONSUMER_DIRS[@]}"; then
        echo "⚠️  Config sync encountered issues (see above)" >&2
        diag "Config sync failed"
    else
        diag "Config sync completed"
    fi
fi

diag_section "VALIDATION RUN"

# Initial validation run
run_validation

display_results

# Auto-fix failed repos (single pass, then exit)
if [ "${fail_count}" -gt 0 ]; then
    diag_section "AUTO-FIX PHASE"
    diag "Starting auto-fix for ${fail_count} failed repo(s): ${fail_repos[*]}"
    attempt_auto_fixes
    display_results

    diag_section "FINAL STATUS"
    diag "Script completed with failures"
    echo ""
    echo "Diagnostic log: ${DIAG_LOG}"
    exit 1
fi

diag_section "FINAL STATUS"
diag "Script completed successfully"
echo ""
echo "Diagnostic log: ${DIAG_LOG}"
exit 0
