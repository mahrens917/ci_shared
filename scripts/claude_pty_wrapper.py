#!/usr/bin/env python3
"""Wrapper to run Claude CLI with a pseudo-terminal.

Bun (Claude's runtime) hangs when stdout is not a TTY on machines
lacking AVX support. This wrapper allocates a PTY so Claude runs
normally, then outputs the result to stdout.

Usage: python claude_pty_wrapper.py <prompt_file> <model>
"""

import os
import select
import subprocess
import sys

CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")


def run_claude_with_pty(prompt_file: str, model: str) -> int:
    with open(prompt_file) as f:
        prompt = f.read()

    master, slave = os.openpty()

    proc = subprocess.Popen(
        [CLAUDE_BIN, "-p", prompt, "--model", model, "--dangerously-skip-permissions"],
        stdout=slave,
        stderr=slave,
        stdin=slave,
    )
    os.close(slave)

    output = b""
    while True:
        ready, _, _ = select.select([master], [], [], 2.0)
        if ready:
            try:
                data = os.read(master, 8192)
                if data:
                    output += data
                else:
                    break
            except OSError:
                break
        if proc.poll() is not None:
            # Drain remaining output
            while True:
                ready, _, _ = select.select([master], [], [], 0.5)
                if ready:
                    try:
                        data = os.read(master, 8192)
                        if data:
                            output += data
                        else:
                            break
                    except OSError:
                        break
                else:
                    break
            break

    os.close(master)
    proc.wait()

    # Strip ANSI escape sequences and terminal control codes
    decoded = output.decode(errors="replace")
    # Filter out the Bun AVX warning and terminal control sequences
    lines = decoded.split("\n")
    filtered = []
    for line in lines:
        if "CPU lacks AVX support" in line or "bun-darwin-x64-baseline" in line:
            continue
        # Remove common terminal escape sequences
        clean = line
        for seq in ["\x1b[", "\x1b]", "\x07", "\r"]:
            while seq in clean:
                if seq == "\x1b[":
                    idx = clean.find(seq)
                    end = idx + 2
                    while end < len(clean) and clean[end] not in "ABCDEFGHJKSTfmnsulh":
                        end += 1
                    clean = clean[:idx] + clean[end + 1 :]
                elif seq == "\x1b]":
                    idx = clean.find(seq)
                    end = clean.find("\x07", idx)
                    if end == -1:
                        end = clean.find("\x1b\\", idx)
                    if end == -1:
                        clean = clean[:idx]
                    else:
                        clean = clean[:idx] + clean[end + 1 :]
                else:
                    clean = clean.replace(seq, "")
        if clean.strip():
            filtered.append(clean)

    sys.stdout.write("\n".join(filtered) + "\n")
    return proc.returncode


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <prompt_file> <model>", file=sys.stderr)
        sys.exit(1)
    sys.exit(run_claude_with_pty(sys.argv[1], sys.argv[2]))
