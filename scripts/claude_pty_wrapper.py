#!/usr/bin/env python3
"""Run Claude CLI with PTY to prevent Bun AVX hang.

Usage: python claude_pty_wrapper.py <prompt_file> <model>
"""

import os
import pty
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

    return pty.spawn([CLAUDE_BIN, "-p", prompt, "--model", model, "--dangerously-skip-permissions"])


if __name__ == "__main__":
    sys.exit(main())
