"""Commit message and git commit helpers."""

from __future__ import annotations

import os
import re
import subprocess
import textwrap
from typing import List

from .codex import invoke_codex
from .models import CommitMessageError, GitCommandAbort
from .process import run_command

COMMIT_SUMMARY_MAX_LENGTH = 90


def _format_staged_diff(staged_diff: str) -> str:
    """Format staged diff for display."""
    if staged_diff:
        return staged_diff
    return "/* no staged diff */"


def _commit_summary_issue(summary: str) -> str | None:
    """Return a description of any formatting issue detected in the commit summary."""
    trimmed = summary.strip()
    checks: list[tuple[bool, str]] = [
        (not trimmed, "Commit summary was blank."),
        (
            len(trimmed) > COMMIT_SUMMARY_MAX_LENGTH,
            f"Commit summary exceeded {COMMIT_SUMMARY_MAX_LENGTH} characters ({len(trimmed)}).",
        ),
        (
            ". " in trimmed,
            "Commit summary contained multiple sentences; use one concise line.",
        ),
        (
            trimmed.endswith((".", "!", "?")),
            "Commit summary must not end with punctuation.",
        ),
    ]

    if trimmed:
        lowered = trimmed.lower()
        disallowed_prefixes = (
            "now i ",
            "i ",
            "here is",
            "here's",
            "the diff",
            "this diff",
        )
        checks.append(
            (
                any(lowered.startswith(prefix) for prefix in disallowed_prefixes),
                "Commit summary used meta commentary instead of describing the change.",
            )
        )

        disallowed_phrases = (
            "your commit",
            "the diff shows",
        )
        commit_message_prompt = re.search(r"\b(?:the|this|your|our)\s+commit message\b", lowered)
        checks.append(
            (
                commit_message_prompt is not None or any(phrase in lowered for phrase in disallowed_phrases),
                "Commit summary referenced the prompt instead of the change.",
            )
        )

    for condition, message in checks:
        if condition:
            return message
    return None


def _build_commit_prompt(
    *,
    model: str,
    reasoning_effort: str,
    staged_diff: str,
    extra_context: str,
    detailed: bool,
    invalid_reason: str | None = None,
) -> str:
    """Construct the Codex prompt for commit message generation."""
    effort_display = reasoning_effort
    if detailed:
        instructions = textwrap.dedent(
            """\
            Produce a git commit message consisting of:
            - A concise subject line (≤72 characters)
              that summarizes what changed using past tense.
            - After a blank line, include ≤5 bullet points (each starting with "- ").
            - Each bullet should summarise the key changes using past tense verbs.
            Avoid trailing periods on the subject line.
            Rely on the diff provided below for context instead of running shell commands.
            Avoid invoking tools such as `diff --git`.
            """
        )
    else:
        instructions = textwrap.dedent(
            """\
            Provide a single-line commit message in past tense (no trailing punctuation).
            Use the diff shown above instead of running shell commands such as `diff --git`.
            Avoid prefatory phrases like "Here is your commit message"
            or commentary about the diff.
            """
        ).strip()

    retry_block = ""
    if invalid_reason:
        detail_hint = " and bullet list" if detailed else ""
        retry_block = textwrap.dedent(
            f"""\
            The previous response was rejected because it violated the commit message
            rules ({invalid_reason}).
            Retry with a concise commit message that follows the instructions above.
            Do not include apologies or meta commentary.
            Respond with only the commit subject{detail_hint}.
            """
        ).strip()

    extra_parts = [part for part in (extra_context.strip(), retry_block) if part]
    extra_block = "\n\n".join(extra_parts)

    diff_display = _format_staged_diff(staged_diff)
    prompt = textwrap.dedent(
        f"""\
        You write high-quality git commit messages.

        Model configuration:
        - Model: {model}
        - Reasoning effort: {effort_display}

        Diff for the staged changes:
        ```diff
        {diff_display}
        ```

        {instructions}
        """
    ).strip()

    if extra_block:
        prompt = f"{prompt}\n\n{extra_block}"
    return prompt


def _invoke_commit_prompt(
    prompt: str,
    *,
    model: str,
    reasoning_effort: str,
) -> tuple[str, List[str]]:
    """Call Codex with the provided prompt and parse the response."""
    response = invoke_codex(
        prompt,
        model=model,
        description="commit message suggestion",
        reasoning_effort=reasoning_effort,
    )
    lines = [line.rstrip() for line in response.strip().splitlines()]
    if not lines:
        raise CommitMessageError.empty_response()
    summary = lines[0].strip()
    body_lines = lines[1:]
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)
    return summary, body_lines


def request_commit_message(
    *,
    model: str,
    reasoning_effort: str,
    staged_diff: str,
    extra_context: str,
    detailed: bool = False,
) -> tuple[str, List[str]]:
    """Ask Codex to produce a commit message for the staged diff."""
    prompt = _build_commit_prompt(
        model=model,
        reasoning_effort=reasoning_effort,
        staged_diff=staged_diff,
        extra_context=extra_context,
        detailed=detailed,
    )
    summary, body_lines = _invoke_commit_prompt(prompt, model=model, reasoning_effort=reasoning_effort)
    validation_issue = _commit_summary_issue(summary)
    if not validation_issue:
        return summary, body_lines

    retry_prompt = _build_commit_prompt(
        model=model,
        reasoning_effort=reasoning_effort,
        staged_diff=staged_diff,
        extra_context=extra_context,
        detailed=detailed,
        invalid_reason=validation_issue,
    )
    summary, body_lines = _invoke_commit_prompt(retry_prompt, model=model, reasoning_effort=reasoning_effort)
    retry_issue = _commit_summary_issue(summary)
    if retry_issue:
        raise CommitMessageError.invalid_response(reason=retry_issue)
    return summary, body_lines


def commit_and_push(
    summary: str,
    body_lines: List[str],
    *,
    push: bool,
) -> None:
    """Create a commit locally and optionally push it to the configured remote."""
    print("[info] Creating commit...")
    commit_args = ["git", "commit", "-m", summary]
    body_text = "\n".join(body_lines).strip()
    if body_text:
        commit_args.extend(["-m", body_text])
    try:
        run_command(commit_args, check=True, live=True)
    except subprocess.CalledProcessError as exc:  # pragma: no cover - defensive
        raise GitCommandAbort.commit_failed(exc) from exc

    if not push:
        return

    remote_env = os.environ.get("GIT_REMOTE")
    if not remote_env:
        raise GitCommandAbort.missing_remote()
    branch_result = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], check=True)
    branch = branch_result.stdout.strip()
    print(f"[info] Pushing to {remote_env}/{branch}...")
    try:
        run_command(["git", "push", remote_env, branch], check=True, live=True)
    except subprocess.CalledProcessError as exc:  # pragma: no cover - defensive
        raise GitCommandAbort.push_failed(exc) from exc


__all__ = ["request_commit_message", "commit_and_push"]
