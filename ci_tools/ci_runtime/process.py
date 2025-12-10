"""Process and git helpers for CI runtime."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Iterable, Optional

from .models import CommandResult


def _run_command_buffered(
    args: list[str],
    *,
    check: bool,
    env: dict[str, str],
    cwd: Optional[Path],
) -> CommandResult:
    """Run a subprocess and capture its output without streaming."""
    process = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(cwd) if cwd else None,
        check=False,
    )
    if check and process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode,
            process.args,
            output=process.stdout,
            stderr=process.stderr,
        )
    return CommandResult(
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
    )


def stream_pipe(pipe, collector: list[str], target=None) -> None:
    """Collect text from a pipe, optionally forwarding to a stream.

    This consolidates duplicate stream processing logic used across
    process.py and codex.py modules.

    Args:
        pipe: File-like object to read from
        collector: List to append lines to
        target: Optional stream to forward lines to (e.g., sys.stdout)
    """
    try:
        for line in iter(pipe.readline, ""):
            collector.append(line)
            if target is not None:
                target.write(line)
                target.flush()
    finally:
        pipe.close()


def _run_command_streaming(
    args: list[str],
    *,
    check: bool,
    env: dict[str, str],
    cwd: Optional[Path],
) -> CommandResult:
    """Stream stdout/stderr live while accumulating the full text."""
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    with subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
        cwd=str(cwd) if cwd else None,
    ) as process:
        threads: list[threading.Thread] = []

        if process.stdout:
            threads.append(
                threading.Thread(
                    target=stream_pipe,
                    args=(process.stdout, stdout_lines, sys.stdout),
                    daemon=True,
                )
            )
        if process.stderr:
            threads.append(
                threading.Thread(
                    target=stream_pipe,
                    args=(process.stderr, stderr_lines, sys.stderr),
                    daemon=True,
                )
            )

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        returncode = process.wait()
    stdout_text = "".join(stdout_lines)
    stderr_text = "".join(stderr_lines)

    if check and returncode != 0:
        raise subprocess.CalledProcessError(returncode, process.args, output=stdout_text, stderr=stderr_text)

    return CommandResult(
        returncode=returncode,
        stdout=stdout_text,
        stderr=stderr_text,
    )


def run_command(
    args: Iterable[str],
    *,
    check: bool = False,
    live: bool = False,
    env: Optional[dict[str, str]] = None,
    cwd: Optional[Path] = None,
) -> CommandResult:
    """Run a command, optionally streaming output while capturing it.

    Args:
        args: Command and arguments to execute
        check: If True, raise CalledProcessError on non-zero exit
        live: If True, stream output to stdout/stderr while capturing
        env: Additional environment variables to merge with os.environ
        cwd: Working directory for the command (defaults to current directory)

    Returns:
        CommandResult with returncode, stdout, and stderr
    """
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    command_args = list(args)
    runner = _run_command_streaming if live else _run_command_buffered
    return runner(
        command_args,
        check=check,
        env=merged_env,
        cwd=cwd,
    )


def tail_text(text: str, lines: int) -> str:
    """Return the last *lines* lines from the provided multiline string."""
    return "\n".join(text.splitlines()[-lines:])


def _build_git_diff_args(staged: bool) -> list[str]:
    """Build git diff command arguments."""
    if staged:
        return ["git", "diff", "--cached"]
    return ["git", "diff"]


def gather_git_diff(*, staged: bool = False) -> str:
    """Return the git diff for staged or unstaged changes."""
    args = _build_git_diff_args(staged)
    result = run_command(args)
    return result.stdout


def gather_git_diff_limited(
    *,
    staged: bool = False,
    max_chars: int = 50000,
    max_lines: int = 1000,
) -> str:
    """Return git diff with size limits to prevent context window overflow.

    When diff exceeds limits, falls back to git diff --stat summary.

    Args:
        staged: If True, return staged changes (--cached)
        max_chars: Maximum characters before falling back to summary
        max_lines: Maximum lines before falling back to summary

    Returns:
        Git diff text, or a summary if too large
    """
    full_diff = gather_git_diff(staged=staged)

    char_count = len(full_diff)
    line_count = full_diff.count("\n")

    if char_count <= max_chars and line_count <= max_lines:
        return full_diff

    stat_args = ["git", "diff", "--stat"]
    if staged:
        stat_args.insert(2, "--cached")

    stat_result = run_command(stat_args)

    return (
        f"[Diff too large: {char_count:,} chars, {line_count:,} lines]\n\n"
        f"Summary (git diff --stat):\n{stat_result.stdout}\n\n"
        f"Note: Full diff exceeds limits ({max_chars:,} chars or {max_lines:,} lines).\n"
        f"The focused diff above shows changes to files implicated in this failure.\n"
        f"Review the CI error output to identify which files need attention."
    )


def gather_git_status() -> str:
    """Return a short git status suitable for prompt summaries."""
    result = run_command(["git", "status", "--short"])
    return result.stdout.strip()


def gather_file_diff(path: str) -> str:
    """Return the diff for a single path relative to HEAD."""
    result = run_command(["git", "diff", path])
    return result.stdout


def get_current_branch(cwd: Optional[Path] = None) -> str:
    """Get the current git branch name.

    Args:
        cwd: Working directory (defaults to current directory)

    Returns:
        Current branch name

    Raises:
        subprocess.CalledProcessError: If git command fails
    """
    result = run_command(["git", "branch", "--show-current"], check=True, cwd=cwd)
    return result.stdout.strip()


def get_commit_message(ref: str = "HEAD", cwd: Optional[Path] = None) -> str:
    """Get commit message for a given git reference.

    Args:
        ref: Git reference (commit hash, branch name, HEAD, etc.)
        cwd: Working directory (defaults to current directory)

    Returns:
        Commit message subject line

    Raises:
        subprocess.CalledProcessError: If git command fails
    """
    result = run_command(["git", "log", "-1", "--pretty=format:%s", ref], check=True, cwd=cwd)
    return result.stdout.strip()


def log_codex_interaction(kind: str, prompt: str, response: str) -> None:
    """Append the interaction to logs/codex_ci.log for later auditing."""
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "codex_ci.log"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"--- {kind} ---\n")
        handle.write("Prompt:\n")
        handle.write(prompt.strip() + "\n")
        handle.write("Response:\n")
        handle.write(response.strip() + "\n\n")


__all__ = [
    "run_command",
    "tail_text",
    "gather_git_diff",
    "gather_git_diff_limited",
    "gather_git_status",
    "gather_file_diff",
    "get_current_branch",
    "get_commit_message",
    "log_codex_interaction",
]
