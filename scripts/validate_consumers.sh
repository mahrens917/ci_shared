#!/usr/bin/env bash
# Validate all consuming repositories after pushing ci_shared config updates.
# Runs `scripts/ci.sh` in each consuming repo in parallel with live status reporting.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_ROOT}"

# Calculate parallelism: 50% of available cores, minimum 1
NUM_CORES=$(getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1)
PARALLEL_JOBS=$(( (NUM_CORES + 1) / 2 ))
[[ ${PARALLEL_JOBS} -lt 1 ]] && PARALLEL_JOBS=1

echo "Validating ${PARALLEL_JOBS} consuming repos in parallel (${NUM_CORES} cores available)"
echo ""

# Create temp directory for logs
LOGS_DIR="$(mktemp -d)"
export LOGS_DIR
# Don't cleanup yet - subshells may still be writing to logs
cleanup_logs() {
    sleep 1  # Give subshells time to finish writing
    rm -rf "${LOGS_DIR}"
}
trap "cleanup_logs" EXIT

# Load consuming repos
CONSUMER_DIRS=()
CONSUMER_TMP="$(mktemp)"
if python - "${PROJECT_ROOT}" "${CONSUMER_TMP}" <<'PY'; then
import sys
from pathlib import Path
from ci_tools.utils.consumers import load_consuming_repos

repo_root = Path(sys.argv[1]).resolve()
output_file = Path(sys.argv[2])
try:
    repos = load_consuming_repos(repo_root)
    with output_file.open("w", encoding="utf-8") as handle:
        for repo in repos:
            handle.write(f"{repo.path}\n")
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
PY
    mapfile -t CONSUMER_DIRS < "${CONSUMER_TMP}" 2>/dev/null || CONSUMER_DIRS=()
else
    echo "Failed to load consuming repositories" >&2
    rm -f "${CONSUMER_TMP}"
    exit 1
fi
rm -f "${CONSUMER_TMP}"

if [ "${#CONSUMER_DIRS[@]}" -eq 0 ]; then
    echo "No consuming repositories configured."
    exit 0
fi

echo "Pushing config to consuming repositories..."

# Push config to all repos (reuse sync logic from ci.sh)
if [ -f "${PROJECT_ROOT}/scripts/sync_project_configs.py" ]; then
    if ! python "${PROJECT_ROOT}/scripts/sync_project_configs.py" "${CONSUMER_DIRS[@]}"; then
        echo "⚠️  Config sync encountered issues (see above)" >&2
    fi
fi

echo ""
echo "Running CI in ${#CONSUMER_DIRS[@]} repositories (${PARALLEL_JOBS} at a time)..."
echo ""

# Run all repos in parallel using GNU parallel or fallback to custom approach
run_repo_wrapper() {
    local repo_dir="$1"
    local logs_dir="$2"
    local repo_name="$3"
    local log_file="${logs_dir}/${repo_name}.log"

    if [ ! -d "${repo_dir}" ]; then
        {
            echo "  ${repo_name}: MISSING"
        } | tee /dev/stderr
        return 2
    fi

    cd "${repo_dir}"
    if bash scripts/ci.sh 2>&1 | tee "${log_file}"; then
        {
            echo "  ${repo_name}: PASS ✓"
        } | tee /dev/stderr > /dev/null
        return 0
    else
        {
            echo "  ${repo_name}: FAIL ✗"
        } | tee /dev/stderr > /dev/null
        return 1
    fi
}

# Run repositories in parallel, respecting PARALLEL_JOBS limit
job_pids=()
job_names=()

for repo_dir in "${CONSUMER_DIRS[@]}"; do
    repo_name=$(basename "${repo_dir}")

    # Wait if we have max jobs running
    while [ "${#job_pids[@]}" -ge "${PARALLEL_JOBS}" ]; do
        # Remove completed jobs from tracking
        new_pids=()
        new_names=()
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

    # Start new job - print output immediately
    echo "  ${repo_name}: WORKING..." >&2
    run_repo_wrapper "${repo_dir}" "${LOGS_DIR}" "${repo_name}" &

    job_pids+=($!)
    job_names+=("${repo_name}")
done

# Wait for all remaining jobs
for pid in "${job_pids[@]}"; do
    wait "$pid" 2>/dev/null || true
done

# Collect and display final results
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Results:"
echo ""

pass_count=0
fail_count=0
missing_count=0

for repo_dir in "${CONSUMER_DIRS[@]}"; do
    repo_name=$(basename "${repo_dir}")
    log_file="${LOGS_DIR}/${repo_name}.log"

    if [ ! -f "${log_file}" ]; then
        echo "  ? ${repo_name}: UNKNOWN"
        continue
    fi

    # Check if script completed successfully (last line should be "Done.")
    if tail -1 "${log_file}" 2>/dev/null | grep -q "^Done\.$"; then
        echo "  ✓ ${repo_name}: PASS"
        ((pass_count++))
    elif grep -q "scripts/ci.sh: Shared CI script not found" "${log_file}" 2>/dev/null; then
        echo "  ? ${repo_name}: MISSING"
        ((missing_count++))
    else
        # Check for errors in the last lines
        if tail -50 "${log_file}" 2>/dev/null | grep -qiE "^(error|failed|Error|FAILED|exit code)"; then
            echo "  ✗ ${repo_name}: FAIL"
            ((fail_count++))
        else
            echo "  ✓ ${repo_name}: PASS"
            ((pass_count++))
        fi
    fi
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
total=$((pass_count + fail_count + missing_count))
echo "Summary: ${pass_count}/${total} passed"
if [ "${fail_count}" -gt 0 ]; then
    echo "         ${fail_count} failed"
fi
if [ "${missing_count}" -gt 0 ]; then
    echo "         ${missing_count} missing"
fi

# Show log file paths for failed repos (for debugging)
if [ "${fail_count}" -gt 0 ]; then
    echo ""
    echo "Failed repo logs:"
    for repo_dir in "${CONSUMER_DIRS[@]}"; do
        repo_name=$(basename "${repo_dir}")
        log_file="${LOGS_DIR}/${repo_name}.log"
        if [ -f "${log_file}" ] && ! tail -1 "${log_file}" 2>/dev/null | grep -q "^Done\.$"; then
            echo "  ${repo_name}: ${log_file}"
        fi
    done
fi

# Exit with non-zero if any repos failed
[ "${fail_count}" -eq 0 ] && exit 0 || exit 1
