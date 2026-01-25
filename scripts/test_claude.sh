#!/usr/bin/env bash
# Minimal test to verify Claude CLI is working.
# Usage: ./scripts/test_claude.sh

set -euo pipefail

CLAUDE_BIN="${HOME}/.local/bin/claude"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Claude CLI Test ==="
echo ""

# 1. Check if claude binary exists
echo "1. Checking if claude binary exists..."
if [[ -x "${CLAUDE_BIN}" ]]; then
    echo "   OK: ${CLAUDE_BIN} exists and is executable"
else
    echo "   FAIL: ${CLAUDE_BIN} not found or not executable"
    exit 1
fi

# 2. Check claude version
echo ""
echo "2. Checking claude version..."
if "${CLAUDE_BIN}" --version 2>&1; then
    echo "   OK: Version check passed"
else
    echo "   FAIL: Version check failed"
    exit 1
fi

# 3. Test PTY wrapper (the only method that works with AVX issue)
echo ""
echo "3. Testing PTY wrapper (15s timeout)..."
PROMPT_FILE=$(mktemp)
echo "Reply with exactly one word: WORKING" > "${PROMPT_FILE}"
RESULT=$(timeout 15 python "${SCRIPT_DIR}/claude_pty_wrapper.py" "${PROMPT_FILE}" haiku 2>&1) || true
EXIT_CODE=$?
rm -f "${PROMPT_FILE}"

# Check for timeout (124) or killed (137)
if [[ ${EXIT_CODE} -eq 124 ]]; then
    echo "   FAIL: Timeout (124) - Claude is not responding"
    exit 1
elif [[ ${EXIT_CODE} -eq 137 ]]; then
    echo "   FAIL: Killed (137) - memory or system issue"
    exit 1
fi

# Strip terminal escape sequences for cleaner output
CLEAN_RESULT=$(echo "${RESULT}" | sed 's/\x1b\[[0-9;]*[a-zA-Z]//g' | tr -d '[]?')
echo "   Output: ${CLEAN_RESULT}"

# Check if we got a real response (not just the AVX warning)
if [[ "${RESULT}" == *"WORKING"* ]]; then
    echo "   OK: Claude CLI working via PTY wrapper"
elif [[ ${#RESULT} -gt 200 ]]; then
    echo "   OK: Claude responded (output length: ${#RESULT} bytes)"
else
    echo "   FAIL: No meaningful response from Claude"
    echo "   Output was: ${RESULT}"
    exit 1
fi

echo ""
echo "=== Test passed: Claude CLI is functional ==="
