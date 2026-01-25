#!/usr/bin/env python3
"""Run Claude CLI with PTY to prevent Bun AVX hang.

Usage: python claude_pty_wrapper.py <prompt_file> <model>

This wrapper runs Claude in a PTY to work around the Bun AVX hang issue
that occurs when stdout is not a TTY. It properly captures and outputs
the response even when stdout is redirected.
"""

import os
import pty
import select
import subprocess
import sys

CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")


def main() -> int:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <prompt_file> <model>", file=sys.stderr)
        return 1

    prompt_file = sys.argv[1]
    model = sys.argv[2]

    with open(prompt_file) as f:
        prompt = f.read()

    # Create a pseudo-terminal
    master_fd, slave_fd = pty.openpty()

    try:
        # Run Claude with the PTY as stdin/stdout/stderr
        proc = subprocess.Popen(
            [CLAUDE_BIN, "-p", prompt, "--model", model, "--dangerously-skip-permissions"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )

        # Close slave in parent - child has it
        os.close(slave_fd)

        # Read from master and write to stdout
        output = []
        while True:
            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if ready:
                try:
                    data = os.read(master_fd, 4096)
                    if not data:
                        break
                    output.append(data)
                    # Write to stdout for real-time output
                    sys.stdout.buffer.write(data)
                    sys.stdout.buffer.flush()
                except OSError:
                    break

            # Check if process has finished
            if proc.poll() is not None:
                # Drain any remaining output
                while True:
                    ready, _, _ = select.select([master_fd], [], [], 0.1)
                    if not ready:
                        break
                    try:
                        data = os.read(master_fd, 4096)
                        if not data:
                            break
                        output.append(data)
                        sys.stdout.buffer.write(data)
                        sys.stdout.buffer.flush()
                    except OSError:
                        break
                break

        return proc.returncode if proc.returncode is not None else 1

    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
