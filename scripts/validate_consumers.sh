#!/usr/bin/env bash
# Validate all consuming repositories after pushing ci_shared config updates.
# Runs `scripts/ci.sh` in each consuming repo in parallel with live status reporting.
# If validation fails, automatically invokes Claude (opus) to fix issues, then exits.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_ROOT}"
export CI_SHARED_ROOT="${PROJECT_ROOT}"

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
declare -a fail_repos
declare -a missing_repos
pass_count=0
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
        echo "PASS" > "${status_file}"
        echo "  [PASS] ${repo_name} ✓"
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
    fail_repos=()
    missing_repos=()
    pass_count=0
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

        # Extract actual errors, filtering out coverage table noise
        # Coverage lines look like: "src/foo.py    100    10    90%"
        local errors
        local filtered_log
        # grep -v returns exit code 1 if no lines selected; protect with || true
        filtered_log=$(grep -v -E '^\s*(src|tests)/[^ ]+\s+[0-9]+\s+[0-9]+\s+[0-9]+%' "${log_file}" || true)
        # Take first 50 lines (early failures) + last 300 filtered lines
        local head_part
        head_part=$(echo "${filtered_log}" | head -50)
        local tail_part
        tail_part=$(echo "${filtered_log}" | tail -300)
        errors="${head_part}

[... middle of log omitted ...]

${tail_part}"

        # Create a temp file with the prompt to avoid escaping issues
        local prompt_file
        prompt_file=$(mktemp)
        cat > "${prompt_file}" << 'PROMPT_EOF'
Resolve all issues identified in the CI errors below. Fix the code directly.

Rules:
- Do NOT modify CI config, Makefiles, or pyproject.toml
- Do NOT add noqa, pylint:disable, type:ignore, or similar bypass comments
- Do NOT add fallbacks or backwards-compatibility shims
- Focus on fixing the actual code issues

PROMPT_EOF
        echo "Errors:" >> "${prompt_file}"
        echo "${errors}" >> "${prompt_file}"

        # Display the prompt being sent to Claude
        echo ""
        echo "━━━ CLAUDE INPUT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        cat "${prompt_file}"
        echo "━━━ END INPUT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        echo "━━━ CLAUDE OUTPUT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

        # Run Claude in the repo directory (script -q forces TTY for streaming output)
        local claude_output_log="${LOGS_DIR}/${repo_name}.claude_output.log"
        # Protect subshell so failures don't exit the main script
        (
            cd "${repo_dir}" || exit 1
            script -q "${claude_output_log}" claude -p "$(cat "${prompt_file}")" --model opus --dangerously-skip-permissions 2>&1 || true
        ) || echo "  [WARN] Claude invocation failed for ${repo_name}"
        echo "━━━ END OUTPUT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

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

    for repo in "${fail_repos[@]}"; do
        echo "  ✗ ${repo}"
    done

    for repo in "${missing_repos[@]}"; do
        echo "  ? ${repo}"
    done

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    local total=$((pass_count + fail_count + missing_count))
    echo "Summary: ${pass_count}/${total} passed"
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

echo "Validating ${PARALLEL_JOBS} consuming repos in parallel (${NUM_CORES} cores available)"
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

# Auto-fix failed repos (single pass, then exit)
if [ "${fail_count}" -gt 0 ]; then
    attempt_auto_fixes
    echo ""
    echo "Auto-fix complete. Re-run to verify fixes."
    exit 1
fi

exit 0
