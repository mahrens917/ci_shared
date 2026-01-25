#!/usr/bin/env python3
"""Run Claude CLI with PTY to prevent Bun AVX hang.

Usage: python claude_pty_wrapper.py <prompt_file> <model>

Uses pty.spawn with a custom read function to capture output while
passing the prompt via --print argument. Sanitizes input to remove
null bytes and ANSI escape sequences that break command line parsing.
"""

import os
import pty
import re
import sys

CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")

# Maximum prompt size (characters) to pass via command line
MAX_PROMPT_SIZE = 100000


def sanitize_prompt(text: str) -> str:
    """Remove null bytes, ANSI escapes, and other control characters."""
    # Remove ANSI escape sequences
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
    # Remove null bytes
    text = text.replace("\x00", "")
    # Remove other control characters except newline and tab
    text = "".join(c for c in text if c == "\n" or c == "\t" or (32 <= ord(c) < 127) or ord(c) > 127)
    return text


def main() -> int:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <prompt_file> <model>", file=sys.stderr)
        return 1

    prompt_file = sys.argv[1]
    model = sys.argv[2]

    with open(prompt_file, encoding="utf-8", errors="replace") as f:
        prompt = f.read()

    # Sanitize the prompt
    prompt = sanitize_prompt(prompt)

    # Truncate very large prompts
    if len(prompt) > MAX_PROMPT_SIZE:
        prompt = prompt[:MAX_PROMPT_SIZE] + "\n\n[... truncated ...]"

    def read_fn(fd: int) -> bytes:
        """Custom read function that captures and forwards output."""
        data = os.read(fd, 4096)
        if data:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        return data

    # Use pty.spawn which handles the PTY setup properly
    return pty.spawn(
        [CLAUDE_BIN, "--print", prompt, "--model", model, "--dangerously-skip-permissions"],
        read_fn,
    )


if __name__ == "__main__":
    sys.exit(main())
