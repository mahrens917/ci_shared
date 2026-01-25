#!/usr/bin/env python3
"""Wrapper to run Claude CLI with a pseudo-terminal.

Bun (Claude's runtime) hangs when stdout is not a TTY on machines
lacking AVX support. This wrapper uses the `script` command to
provide a PTY environment.

Usage: python claude_pty_wrapper.py <prompt_file> <model>
"""

import os
import shlex
import subprocess
import sys

CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")


def run_claude_with_pty(prompt_file: str, model: str) -> int:
    # Use `script` command to provide PTY. On macOS: script -q /dev/null command
    # The inner bash -c handles the pipe from cat to claude
    inner_cmd = f"cat {shlex.quote(prompt_file)} | {shlex.quote(CLAUDE_BIN)} --model {shlex.quote(model)} --dangerously-skip-permissions"
    cmd = ["script", "-q", "/dev/null", "bash", "-c", inner_cmd]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=280)
    except subprocess.TimeoutExpired as e:
        print(f"[TIMEOUT] Claude CLI timed out after 280s", file=sys.stderr)
        if e.stdout:
            print(f"[TIMEOUT] Partial stdout: {e.stdout[:500]}", file=sys.stderr)
        if e.stderr:
            print(f"[TIMEOUT] Partial stderr: {e.stderr[:500]}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[ERROR] Failed to run Claude CLI: {e}", file=sys.stderr)
        return 1

    # Debug: show raw output sizes
    print(
        f"[DEBUG] stdout={len(result.stdout)} bytes, " f"stderr={len(result.stderr)} bytes, " f"returncode={result.returncode}",
        file=sys.stderr,
    )

    # Filter out Bun AVX warnings and empty lines
    lines = result.stdout.split("\n")
    filtered = []
    for line in lines:
        if "CPU lacks AVX support" in line or "bun-darwin-x64-baseline" in line:
            continue
        if line.strip():
            filtered.append(line)

    if filtered:
        print("\n".join(filtered))

    if result.stderr:
        # Also filter stderr
        err_lines = result.stderr.split("\n")
        for line in err_lines:
            if "CPU lacks AVX support" in line or "bun-darwin-x64-baseline" in line:
                continue
            if line.strip():
                print(line, file=sys.stderr)

    return result.returncode


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <prompt_file> <model>", file=sys.stderr)
        sys.exit(1)
    sys.exit(run_claude_with_pty(sys.argv[1], sys.argv[2]))
