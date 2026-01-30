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
import time

CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")
MAX_PROMPT_SIZE = 100000
IDLE_TIMEOUT_SECONDS = int(os.environ.get("LLM_IDLE_TIMEOUT", "1800"))  # 30 min
DIAG_ENABLED = os.environ.get("VALIDATE_DIAG", "1") == "1"
START_TIME = time.time()


def diag(msg: str) -> None:
    """Write diagnostic message to stderr."""
    if DIAG_ENABLED:
        elapsed = int((time.time() - START_TIME) * 1000)
        sys.stderr.write(f"[PTY +{elapsed}ms] {msg}\n")
        sys.stderr.flush()


def sanitize_prompt(text: str) -> str:
    """Remove null bytes, ANSI escapes, and other control characters."""
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
    text = text.replace("\x00", "")
    text = "".join(c for c in text if c == "\n" or c == "\t" or (32 <= ord(c) < 127) or ord(c) > 127)
    return text


def main() -> int:
    diag("claude_pty_wrapper starting")

    if len(sys.argv) != 3:
        sys.stderr.write(f"Usage: {sys.argv[0]} <prompt_file> <model>\n")
        return 1

    prompt_file = sys.argv[1]
    model = sys.argv[2]

    diag(f"prompt_file={prompt_file}, model={model}")
    diag(f"CLAUDE_BIN={CLAUDE_BIN}, exists={os.path.exists(CLAUDE_BIN)}")

    # Log relevant env vars
    for key in ["ANTHROPIC_API_KEY", "NODE_OPTIONS", "CLAUDE_BASH_NO_LOGIN"]:
        val = os.environ.get(key)
        if val:
            # Mask API keys
            if "KEY" in key and len(val) > 10:
                val = val[:4] + "..." + val[-4:]
            diag(f"ENV {key}={val}")

    with open(prompt_file, encoding="utf-8", errors="replace") as f:
        prompt = f.read()

    diag(f"prompt loaded: {len(prompt)} chars")

    prompt = sanitize_prompt(prompt)

    if len(prompt) > MAX_PROMPT_SIZE:
        prompt = prompt[:MAX_PROMPT_SIZE] + "\n\n[... truncated ...]"
        diag(f"prompt truncated to {MAX_PROMPT_SIZE} chars")

    # Create PTY
    diag("creating PTY")
    master_fd, slave_fd = pty.openpty()
    diag("PTY created")

    # Track if we should keep running
    running = True
    timed_out = False

    def signal_handler(_signum, _frame):
        nonlocal running
        running = False

    # Handle SIGTERM (from timeout) gracefully
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    diag(f"idle timeout: {IDLE_TIMEOUT_SECONDS}s")

    try:
        cmd = [CLAUDE_BIN, "--print", prompt, "--model", model, "--dangerously-skip-permissions", "--no-session-persistence"]
        diag(f"spawning: {CLAUDE_BIN} --print <prompt> --model {model} --dangerously-skip-permissions --no-session-persistence")

        # Create clean environment without ANTHROPIC_API_KEY to prevent Claude CLI
        # from prompting about rejected API keys (which causes hangs in non-interactive mode)
        clean_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        if "ANTHROPIC_API_KEY" in os.environ:
            diag("Removed ANTHROPIC_API_KEY from subprocess environment")

        proc = subprocess.Popen(
            cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            env=clean_env,
        )
        diag(f"process spawned, PID={proc.pid}")
        os.close(slave_fd)
        slave_fd = -1

        first_output = True
        total_bytes = 0
        last_visible = time.time()  # last time we saw substantial output
        last_heartbeat = time.time()  # last time we printed a dot
        heartbeat_interval = 5  # Print dot every 5 seconds

        def has_visible_content(raw: bytes) -> bool:
            """Check if data contains substantial visible content (not just control chars)."""
            # Decode and strip ANSI escape sequences
            try:
                text = raw.decode("utf-8", errors="replace")
            except Exception:
                return len(raw) > 20  # If decode fails, use size heuristic
            text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
            # Count printable non-whitespace characters
            printable = sum(1 for c in text if c.isprintable() and not c.isspace())
            return printable >= 10

        # Read output until process exits or we're killed
        while running:
            # Check if there's data to read
            ready, _, _ = select.select([master_fd], [], [], 5.0)
            if ready:
                try:
                    data = os.read(master_fd, 4096)
                    if data:
                        if first_output:
                            diag("first output received from Claude")
                            first_output = False
                        total_bytes += len(data)
                        sys.stdout.buffer.write(data)
                        sys.stdout.buffer.flush()
                        if has_visible_content(data):
                            last_visible = time.time()
                    else:
                        diag("EOF received")
                        break  # EOF
                except OSError as e:
                    diag(f"OSError reading: {e}")
                    break
            else:
                # No data ready - print heartbeat if enough time passed
                now = time.time()
                if now - last_heartbeat >= heartbeat_interval:
                    sys.stderr.write(".")
                    sys.stderr.flush()
                    last_heartbeat = now

            # Check idle timeout (no visible output for too long)
            idle_seconds = time.time() - last_visible
            if idle_seconds >= IDLE_TIMEOUT_SECONDS:
                diag(
                    f"idle timeout ({IDLE_TIMEOUT_SECONDS}s) exceeded (no visible output for {int(idle_seconds)}s), terminating child PID={proc.pid}"
                )
                timed_out = True
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    diag("child did not exit after SIGTERM, sending SIGKILL")
                    proc.kill()
                    proc.wait(timeout=3)
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

        if timed_out:
            elapsed = int(time.time() - START_TIME)
            diag(f"idle timeout after {elapsed}s total runtime, total_bytes={total_bytes}")
            return 124  # match timeout(1) exit code convention
        diag(f"process exited, returncode={proc.returncode}, total_bytes={total_bytes}")
        return proc.returncode if proc.returncode is not None else 1

    except Exception as e:
        diag(f"Exception: {e}")
        sys.stderr.write(f"Error: {e}\n")
        return 1

    finally:
        # Kill child process if still running
        if "proc" in dir() and proc.poll() is None:
            diag(f"cleaning up child PID={proc.pid}")
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
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
