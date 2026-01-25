#!/usr/bin/env bash
# Test Claude CLI exactly as validate_consumers.sh does.
# This should catch the same issues that CI encounters.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CI_SHARED_ROOT="$(dirname "${SCRIPT_DIR}")"
CLAUDE_BIN="${HOME}/.local/bin/claude"

echo "=== Claude CLI Test (matching CI flow) ==="
echo ""

# 0. Check for zombie Claude processes (common cause of hangs)
echo "0. Checking for competing Claude processes..."
CLAUDE_COUNT=$(ps aux | grep -c "[c]laude.*dangerously-skip-permissions" 2>/dev/null || echo 0)
if [[ ${CLAUDE_COUNT} -gt 2 ]]; then
    echo "   WARNING: ${CLAUDE_COUNT} Claude processes running!"
    echo "   This can cause timeouts and hangs."
    echo ""
    echo "   Run this to kill them:"
    echo "   pkill -9 -f 'claude.*dangerously-skip-permissions'"
    echo ""
    read -p "   Kill them now? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        pkill -9 -f "claude.*dangerously-skip-permissions" || true
        sleep 1
        echo "   Killed. Continuing..."
    else
        echo "   Continuing anyway (may timeout)..."
    fi
else
    echo "   OK: ${CLAUDE_COUNT} processes (acceptable)"
fi
echo ""

# 1. Check if claude binary exists
echo "1. Checking claude binary..."
if [[ -x "${CLAUDE_BIN}" ]]; then
    echo "   OK: ${CLAUDE_BIN}"
else
    echo "   FAIL: ${CLAUDE_BIN} not found"
    exit 1
fi

# 2. Check version
echo ""
echo "2. Checking version..."
"${CLAUDE_BIN}" --version 2>&1 || true
echo ""

# 3. Find a real log file to use (from most recent validate run)
echo "3. Finding real CI log file..."
LOGS_DIR=$(ls -dt "${CI_SHARED_ROOT}"/logs/validate_consumers_* 2>/dev/null | head -1)
if [[ -z "${LOGS_DIR}" ]]; then
    echo "   No validate_consumers logs found, creating synthetic test"
    LOG_FILE=""
else
    # Find a .log file that has actual content
    LOG_FILE=$(find "${LOGS_DIR}" -name "*.log" -size +1k ! -name "*.llm_output.log" | head -1)
    if [[ -n "${LOG_FILE}" ]]; then
        echo "   Using: ${LOG_FILE}"
        echo "   Size: $(wc -c < "${LOG_FILE}") bytes"
    fi
fi

# 4. Create prompt EXACTLY like validate_consumers.sh does
echo ""
echo "4. Creating prompt (same as validate_consumers.sh)..."

prompt_file=$(mktemp)

if [[ -n "${LOG_FILE}" ]]; then
    # Exact same filtering as validate_consumers.sh lines 291-305
    filtered_log=$(grep -v -E '^\s*(src|tests)/[^ ]+\s+[0-9]+\s+[0-9]+\s+[0-9]+%|PASSED' "${LOG_FILE}" || true)
    head_part=$(printf '%s\n' "${filtered_log}" | head -50 || true)
    tail_part=$(printf '%s\n' "${filtered_log}" | tail -300 || true)
    errors="${head_part}

[... middle of log omitted ...]

${tail_part}"
else
    errors="tests/unit/test_example.py:42: example error for testing"
fi

# Exact same prompt creation as validate_consumers.sh lines 310-321
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

echo "   Prompt file: ${prompt_file}"
echo "   Prompt size: $(wc -c < "${prompt_file}") bytes"

# 5. Test EXACTLY like run_llm_with_dns_retry does (line 36)
echo ""
echo "5. Running PTY wrapper (matching CI exactly)..."
echo "   cd to: ${CI_SHARED_ROOT}"
echo "   Command: timeout 300 python \"\${CI_SHARED_ROOT}/scripts/claude_pty_wrapper.py\" \"\${prompt_file}\" sonnet > temp 2>&1"

temp_output=$(mktemp)
cd "${CI_SHARED_ROOT}"

# This is EXACTLY line 36 of validate_consumers.sh
START=$(date +%s)
timeout 60 python "${CI_SHARED_ROOT}/scripts/claude_pty_wrapper.py" "${prompt_file}" sonnet > "${temp_output}" 2>&1 || true
ELAPSED=$(($(date +%s) - START))

output_size=$(wc -c < "${temp_output}" 2>/dev/null || echo 0)
echo ""
echo "   Time: ${ELAPSED}s"
echo "   Output size: ${output_size} bytes"

# 6. Check results
echo ""
echo "6. Results:"
if [[ ${output_size} -lt 200 ]]; then
    echo "   FAIL: Only ${output_size} bytes (probably just AVX warning)"
    echo ""
    echo "   --- Full output ---"
    cat "${temp_output}"
    echo "   --- End output ---"
    rm -f "${prompt_file}" "${temp_output}"
    exit 1
elif [[ ${ELAPSED} -ge 60 ]]; then
    echo "   FAIL: Timed out after ${ELAPSED}s"
    rm -f "${prompt_file}" "${temp_output}"
    exit 1
else
    echo "   OK: Got ${output_size} bytes in ${ELAPSED}s"
    echo ""
    echo "   --- First 20 lines ---"
    head -20 "${temp_output}"
    echo "   --- End preview ---"
fi

rm -f "${prompt_file}" "${temp_output}"
echo ""
echo "=== Test passed ==="
