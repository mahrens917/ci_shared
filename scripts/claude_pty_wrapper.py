#!/usr/bin/env python3
"""Run Claude CLI with PTY to prevent Bun AVX hang.

Usage: python claude_pty_wrapper.py <prompt_file> <model>

Uses subprocess with PTY to run Claude, capturing all output.
Handles signals properly to ensure output is captured even on timeout.
"""

import os
import pty
import re
import select
import signal
import subprocess
import sys

CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")
MAX_PROMPT_SIZE = 100000


def sanitize_prompt(text: str) -> str:
    """Remove null bytes, ANSI escapes, and other control characters."""
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
    text = text.replace("\x00", "")
    text = "".join(c for c in text if c == "\n" or c == "\t" or (32 <= ord(c) < 127) or ord(c) > 127)
    return text


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write(f"Usage: {sys.argv[0]} <prompt_file> <model>\n")
        return 1

    prompt_file = sys.argv[1]
    model = sys.argv[2]

    with open(prompt_file, encoding="utf-8", errors="replace") as f:
        prompt = f.read()

    prompt = sanitize_prompt(prompt)

    if len(prompt) > MAX_PROMPT_SIZE:
        prompt = prompt[:MAX_PROMPT_SIZE] + "\n\n[... truncated ...]"

    # Create PTY
    master_fd, slave_fd = pty.openpty()

    # Track if we should keep running
    running = True

    def signal_handler(_signum, _frame):
        nonlocal running
        running = False

    # Handle SIGTERM (from timeout) gracefully
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        proc = subprocess.Popen(
            [CLAUDE_BIN, "--print", prompt, "--model", model, "--dangerously-skip-permissions"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
        os.close(slave_fd)
        slave_fd = -1

        # Read output until process exits or we're killed
        while running:
            # Check if there's data to read
            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if ready:
                try:
                    data = os.read(master_fd, 4096)
                    if data:
                        sys.stdout.buffer.write(data)
                        sys.stdout.buffer.flush()
                    else:
                        break  # EOF
                except OSError:
                    break

            # Check if process finished
            if proc.poll() is not None:
                # Drain remaining output
                while True:
                    ready, _, _ = select.select([master_fd], [], [], 0.05)
                    if not ready:
                        break
                    try:
                        data = os.read(master_fd, 4096)
                        if data:
                            sys.stdout.buffer.write(data)
                            sys.stdout.buffer.flush()
                        else:
                            break
                    except OSError:
                        break
                break

        return proc.returncode if proc.returncode is not None else 1

    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1

    finally:
        # Ensure output is flushed
        sys.stdout.buffer.flush()
        try:
            os.close(master_fd)
        except OSError:
            pass
        if slave_fd >= 0:
            try:
                os.close(slave_fd)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
