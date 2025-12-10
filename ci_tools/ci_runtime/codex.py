"""Codex/Claude CLI interaction helpers."""

from __future__ import annotations

import os
import re
import subprocess
import textwrap
import threading
from typing import Optional

from .config import RISKY_PATTERNS
from .models import CodexCliError, PatchPrompt
from .process import log_codex_interaction, stream_pipe


def _detect_cli_type(model: str) -> str:
    """Detect which CLI to use based on environment or model name."""
    cli_type_env = os.environ.get("CI_CLI_TYPE")
    if cli_type_env:
        cli_type = cli_type_env.lower()
        if cli_type in ("claude", "codex"):
            return cli_type
    if model.startswith("claude"):
        return "claude"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    raise ValueError("CI_CLI_TYPE must be set to 'claude' or 'codex', or ANTHROPIC_API_KEY must be set")


def build_codex_command(model: str, reasoning_effort: Optional[str]) -> list[str]:
    """Return the CLI invocation for the codex or claude binary."""
    cli_type = _detect_cli_type(model)
    if cli_type == "claude":
        command = ["claude", "-p", "-"]
        # Only pass --model if a Claude model is explicitly requested
        # Skip passing non-Claude models (e.g., gpt-5-codex) - Claude CLI uses its default
        if model and model.startswith("claude"):
            command.insert(1, "--model")
            command.insert(2, model)
        return command
    command = ["codex", "exec", "--model", model, "-"]
    if reasoning_effort:
        command.insert(-1, "-c")
        command.insert(-1, f"model_reasoning_effort={reasoning_effort}")
    return command


def _feed_prompt(process: subprocess.Popen[str], prompt: str) -> None:
    """Send the prompt to the Codex subprocess and close stdin.

    BrokenPipeError is re-raised after closing stdin to signal the subprocess
    terminated before consuming the input.
    """
    if not process.stdin:
        return
    try:
        process.stdin.write(prompt)
    except BrokenPipeError:  # pragma: no cover - defensive
        process.stdin.close()
        raise
    finally:
        process.stdin.close()


def _stream_output(process: subprocess.Popen[str]) -> tuple[list[str], list[str]]:
    """Read stdout and stderr from the Codex subprocess concurrently.

    Delegates to the canonical stream_pipe implementation in process.py.
    """
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    threads: list[threading.Thread] = []
    if process.stdout:
        threads.append(threading.Thread(target=stream_pipe, args=(process.stdout, stdout_lines), daemon=True))
        threads[-1].start()
    if process.stderr:
        threads.append(threading.Thread(target=stream_pipe, args=(process.stderr, stderr_lines), daemon=True))
        threads[-1].start()

    for thread in threads:
        thread.join()
    return stdout_lines, stderr_lines


def invoke_codex(
    prompt: str,
    *,
    model: str,
    description: str,
    reasoning_effort: Optional[str],
) -> str:
    """Execute the Codex CLI and return the assistant's response text."""
    command = build_codex_command(model, reasoning_effort)
    with subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    ) as process:
        feeder = threading.Thread(target=_feed_prompt, args=(process, prompt), daemon=True)
        feeder.start()
        feeder.join()
        stdout_lines, stderr_lines = _stream_output(process)
        returncode = process.wait()
    stdout = "".join(stdout_lines).strip()
    stderr = "".join(stderr_lines).strip()

    log_codex_interaction(description, prompt, stdout or stderr)

    if returncode != 0:
        error_details = stderr or stdout
        raise CodexCliError.exit_status(returncode=returncode, output=error_details)

    if stdout.startswith("assistant:"):
        stdout = stdout.partition("\n")[2].strip()
    return stdout or stderr


def truncate_error(error: Optional[str], limit: int = 2000) -> str:
    """Shorten an error message for inclusion in Codex prompts."""
    if not error:
        return "(none)"
    text = error.strip()
    if len(text) > limit:
        return text[:limit] + "...(truncated)"
    return text


def extract_unified_diff(response_text: str) -> Optional[str]:
    """Return the first diff block extracted from a Codex response."""
    if not response_text:
        return None
    if response_text.strip().upper() == "NOOP":
        return None
    code_blocks = re.findall(r"```(?:diff)?\s*(.*?)```", response_text, flags=re.DOTALL)
    if code_blocks:
        for block in code_blocks:
            text = block.strip()
            if text.startswith(("diff", "---", "Index:", "From ")):
                return text
        return code_blocks[0].strip()
    return response_text


def has_unified_diff_header(diff_text: str) -> bool:
    """Return True if the text contains the expected unified diff headers."""
    return bool(re.search(r"^(diff --git|--- |\+\+\+ )", diff_text, re.MULTILINE))


def _format_git_status(status: str) -> str:
    """Format git status for display."""
    if status:
        return status
    return "(clean)"


def _format_summary(summary: str) -> str:
    """Format failure summary for display."""
    if summary:
        return summary
    return "(not detected)"


def _format_diff(diff: str, placeholder: str) -> str:
    """Format diff for display."""
    if diff:
        return diff
    return placeholder


def request_codex_patch(
    *,
    model: str,
    reasoning_effort: str,
    prompt: PatchPrompt,
) -> str:
    """Ask Codex for a patch diff based on the supplied failure context."""
    git_status_display = _format_git_status(prompt.git_status)
    summary_display = _format_summary(prompt.failure_context.summary)
    focused_diff_display = _format_diff(prompt.failure_context.focused_diff, "/* no focused diff */")
    git_diff_display = _format_diff(prompt.git_diff, "/* no diff */")

    prompt_text = textwrap.dedent(
        f"""\
        You are currently iterating on automated CI repairs.

        Context:
        - CI command: `{prompt.command}`
        - Iteration: {prompt.iteration}
        - Patch attempt: {prompt.attempt}
        - Git status:
        {git_status_display}

        Failure summary:
        {summary_display}

        Focused diff for implicated files:
        ```diff
        {focused_diff_display}
        ```

        Current diff (unstaged working tree):
        ```diff
        {git_diff_display}
        ```

        Latest CI failure log (tail):
        ```
        {prompt.failure_context.log_excerpt}
        ```

        Previous patch apply error:
        {truncate_error(prompt.patch_error)}

        Instructions:
        - Respond ONLY with a unified diff (include `diff --git`, `---`, and `+++` lines) that can be applied with `patch -p1`.
        - Avoid large-scale refactors; keep the change tightly scoped to resolve the failure.
        - If no code change is appropriate, reply with `NOOP`.
        - Do not modify automation scaffolding (ci.py, ci_tools/*, scripts/ci.sh).
        """
    )
    return invoke_codex(
        prompt_text,
        model=model,
        description="patch suggestion",
        reasoning_effort=reasoning_effort,
    )


def truncate_diff_summary(diff_text: str, line_limit: int) -> tuple[bool, Optional[str]]:
    """Return whether a diff exceeds the allowed change budget."""
    changed_lines = sum(1 for line in diff_text.splitlines() if line.startswith(("+", "-")))
    if changed_lines > line_limit:
        return (
            True,
            f"Patch has {changed_lines} changed lines which exceeds the limit of {line_limit}.",
        )
    return False, None


def risky_pattern_in_diff(diff_text: str) -> Optional[str]:
    """Return the first risky pattern matched within the diff text."""
    for pattern in RISKY_PATTERNS:
        if pattern.search(diff_text):
            return pattern.pattern
    return None


__all__ = [
    "build_codex_command",
    "invoke_codex",
    "request_codex_patch",
    "truncate_error",
    "extract_unified_diff",
    "has_unified_diff_header",
    "truncate_diff_summary",
    "risky_pattern_in_diff",
]
