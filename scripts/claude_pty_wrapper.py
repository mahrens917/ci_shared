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

EXPECTED_ARGC = 3
MIN_KEY_DISPLAY_LEN = 10
MIN_PRINTABLE_CHARS = 10
MIN_RAW_BYTES_HEURISTIC = 20
PRINTABLE_LOW = 32
ASCII_HIGH = 127
HEARTBEAT_INTERVAL = 5
TERM_WAIT_SECONDS = 5
KILL_WAIT_SECONDS = 3
TIMEOUT_EXIT_CODE = 124


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
    text = "".join(c for c in text if c in {"\n", "\t"} or (PRINTABLE_LOW <= ord(c) < ASCII_HIGH) or ord(c) > ASCII_HIGH)
    return text


def log_env_vars() -> None:
    """Log relevant environment variables, masking API keys."""
    for key in ["ANTHROPIC_API_KEY", "NODE_OPTIONS", "CLAUDE_BASH_NO_LOGIN"]:
        val = os.environ.get(key)
        if val:
            if "KEY" in key and len(val) > MIN_KEY_DISPLAY_LEN:
                val = val[:4] + "..." + val[-4:]
            diag(f"ENV {key}={val}")


def load_prompt(prompt_file: str) -> str:
    """Load and sanitize the prompt from a file, truncating if needed."""
    with open(prompt_file, encoding="utf-8", errors="replace") as f:
        prompt = f.read()
    diag(f"prompt loaded: {len(prompt)} chars")
    prompt = sanitize_prompt(prompt)
    if len(prompt) > MAX_PROMPT_SIZE:
        prompt = prompt[:MAX_PROMPT_SIZE] + "\n\n[... truncated ...]"
        diag(f"prompt truncated to {MAX_PROMPT_SIZE} chars")
    return prompt


def has_visible_content(raw: bytes) -> bool:
    """Check if data contains substantial visible content (not just control chars)."""
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return len(raw) > MIN_RAW_BYTES_HEURISTIC
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
    printable = sum(1 for c in text if c.isprintable() and not c.isspace())
    return printable >= MIN_PRINTABLE_CHARS


def drain_remaining_output(master_fd: int) -> None:
    """Drain any remaining output from the PTY after process exits."""
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


def terminate_child(proc: subprocess.Popen) -> None:
    """Send SIGTERM then SIGKILL if needed."""
    proc.terminate()
    try:
        proc.wait(timeout=TERM_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        diag("child did not exit after SIGTERM, sending SIGKILL")
        proc.kill()
        proc.wait(timeout=KILL_WAIT_SECONDS)


def _read_pty_chunk(master_fd: int) -> bytes | None:
    """Read a chunk from the PTY. Returns data, empty bytes for EOF, or None for error."""
    try:
        data = os.read(master_fd, 4096)
    except OSError as e:
        diag(f"OSError reading: {e}")
        return None
    else:
        return data


def _emit_heartbeat(last_heartbeat: float) -> float:
    """Print a heartbeat dot if enough time has passed. Returns updated timestamp."""
    now = time.time()
    if now - last_heartbeat >= HEARTBEAT_INTERVAL:
        sys.stderr.write(".")
        sys.stderr.flush()
        return now
    return last_heartbeat


def _process_pty_data(data: bytes, total_bytes: int, first_output: bool) -> tuple[int, bool]:
    """Write received PTY data to stdout, tracking byte counts. Returns (total_bytes, first_output)."""
    if first_output:
        diag("first output received from Claude")
        first_output = False
    total_bytes += len(data)
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()
    return total_bytes, first_output


def _check_idle_and_exit(last_visible: float, proc: subprocess.Popen) -> bool:
    """Return True if idle timeout exceeded, terminating the child."""
    idle_seconds = time.time() - last_visible
    if idle_seconds < IDLE_TIMEOUT_SECONDS:
        return False
    diag(f"idle timeout ({IDLE_TIMEOUT_SECONDS}s) exceeded (no visible output for {int(idle_seconds)}s), terminating child PID={proc.pid}")
    terminate_child(proc)
    return True


def _handle_ready_fd(master_fd: int, total_bytes: int, first_output: bool) -> tuple[int, bool, bool]:
    """Handle data when the PTY fd is ready. Returns (total_bytes, first_output, should_continue)."""
    data = _read_pty_chunk(master_fd)
    if not data:
        if data is not None:
            diag("EOF received")
        return total_bytes, first_output, False
    total_bytes, first_output = _process_pty_data(data, total_bytes, first_output)
    visible = has_visible_content(data)
    return total_bytes, first_output, not visible


def read_loop(master_fd: int, proc: subprocess.Popen, running_flag: list[bool]) -> tuple[int, bool]:
    """Read PTY output until process exits, signal, or idle timeout. Returns (total_bytes, timed_out)."""
    first_output = True
    total_bytes = 0
    last_visible = time.time()
    last_heartbeat = time.time()

    while running_flag[0]:
        ready, _, _ = select.select([master_fd], [], [], 5.0)
        if ready:
            total_bytes, first_output, keep_ts = _handle_ready_fd(master_fd, total_bytes, first_output)
            if not keep_ts:
                last_visible = time.time()
        else:
            last_heartbeat = _emit_heartbeat(last_heartbeat)

        if _check_idle_and_exit(last_visible, proc):
            return total_bytes, True

        if proc.poll() is not None:
            drain_remaining_output(master_fd)
            break

    return total_bytes, False


def cleanup_process(proc: subprocess.Popen, master_fd: int, slave_fd: int) -> None:
    """Clean up child process and file descriptors."""
    if proc.poll() is None:
        diag(f"cleaning up child PID={proc.pid}")
        proc.terminate()
        try:
            proc.wait(timeout=KILL_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
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


def spawn_claude(prompt: str, model: str, slave_fd: int) -> subprocess.Popen:
    """Spawn the Claude CLI process attached to the given PTY slave."""
    cmd = [CLAUDE_BIN, "--print", prompt, "--model", model, "--dangerously-skip-permissions", "--no-session-persistence"]
    diag(f"spawning: {CLAUDE_BIN} --print <prompt> --model {model} --dangerously-skip-permissions --no-session-persistence")
    proc = subprocess.Popen(cmd, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True)
    diag(f"process spawned, PID={proc.pid}")
    return proc


def main() -> int:
    diag("claude_pty_wrapper starting")

    if len(sys.argv) != EXPECTED_ARGC:
        sys.stderr.write(f"Usage: {sys.argv[0]} <prompt_file> <model>\n")
        return 1

    prompt_file = sys.argv[1]
    model = sys.argv[2]
    diag(f"prompt_file={prompt_file}, model={model}")
    diag(f"CLAUDE_BIN={CLAUDE_BIN}, exists={os.path.exists(CLAUDE_BIN)}")
    log_env_vars()

    prompt = load_prompt(prompt_file)

    diag("creating PTY")
    master_fd, slave_fd = pty.openpty()
    diag("PTY created")

    running_flag = [True]

    def signal_handler(_signum, _frame):
        running_flag[0] = False

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    diag(f"idle timeout: {IDLE_TIMEOUT_SECONDS}s")

    proc = None
    try:
        proc = spawn_claude(prompt, model, slave_fd)
        os.close(slave_fd)
        slave_fd = -1

        total_bytes, timed_out = read_loop(master_fd, proc, running_flag)

        if timed_out:
            elapsed = int(time.time() - START_TIME)
            diag(f"idle timeout after {elapsed}s total runtime, total_bytes={total_bytes}")
            return TIMEOUT_EXIT_CODE
        diag(f"process exited, returncode={proc.returncode}, total_bytes={total_bytes}")
    except Exception as e:
        diag(f"Exception: {e}")
        sys.stderr.write(f"Error: {e}\n")
        return 1
    else:
        return proc.returncode if proc.returncode is not None else 1
    finally:
        if proc is not None:
            cleanup_process(proc, master_fd, slave_fd)
        else:
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
