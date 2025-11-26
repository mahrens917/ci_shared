"""CI automation workflow orchestration."""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path
from typing import Iterable, Optional, Set

from .config import (
    DEFAULT_REASONING_EFFORT,
    REASONING_EFFORT_CHOICES,
    REQUIRED_MODEL,
    resolve_model_choice,
    resolve_reasoning_choice,
)
from .coverage import extract_coverage_deficits
from .environment import load_env_settings
from .failures import build_failure_context
from .messaging import commit_and_push, request_commit_message
from .models import (
    CiAbort,
    CommandConfig,
    ModelConfig,
    PatchLifecycleAbort,
    RuntimeOptions,
    WorkflowConfig,
)
from .patch_cycle import request_and_apply_patches
from .process import gather_git_diff_limited, run_command


def _derive_runtime_flags(
    args: argparse.Namespace, command_tokens: list[str]
) -> tuple[bool, dict[str, str], bool, bool, bool]:
    """Derive automation flags based on CLI args and the requested command."""
    if command_tokens:
        command_basename = Path(command_tokens[0]).name
    else:
        command_basename = ""
    automation_mode = command_basename == "ci.sh"
    command_env = {"CI_AUTOMATION": "1"} if automation_mode else {}
    auto_stage_enabled = args.auto_stage or automation_mode
    commit_message_enabled = args.commit_message or automation_mode
    auto_push_enabled = automation_mode
    return (
        automation_mode,
        command_env,
        auto_stage_enabled,
        commit_message_enabled,
        auto_push_enabled,
    )


def configure_runtime(args: argparse.Namespace) -> RuntimeOptions:
    """Convert parsed CLI arguments into the runtime options dataclass."""
    load_env_settings(args.env_file)
    command_tokens = shlex.split(args.command)
    model_name = resolve_model_choice(args.model, validate=True)
    reasoning_effort = resolve_reasoning_choice(args.reasoning_effort, validate=True)
    (
        automation_mode,
        command_env,
        auto_stage_enabled,
        commit_message_enabled,
        auto_push_enabled,
    ) = _derive_runtime_flags(args, command_tokens)

    return RuntimeOptions(
        command=CommandConfig(tokens=command_tokens, env=command_env),
        workflow=WorkflowConfig(
            patch_approval_mode=args.patch_approval_mode,
            automation_mode=automation_mode,
            auto_stage_enabled=auto_stage_enabled,
            commit_message_enabled=commit_message_enabled,
            auto_push_enabled=auto_push_enabled,
        ),
        model=ModelConfig(name=model_name, reasoning_effort=reasoning_effort),
    )


def perform_dry_run(args: argparse.Namespace, options: RuntimeOptions) -> Optional[int]:
    """Run the CI command once when --dry-run is supplied."""
    if not args.dry_run:
        return None
    print("[info] Dry run: executing CI command once without invoking Codex.")
    result = run_command(options.command_tokens, live=True, env=options.command_env)
    return result.returncode


def _collect_worktree_diffs() -> tuple[str, str]:
    """Return both unstaged and staged git diffs."""
    return gather_git_diff_limited(staged=False), gather_git_diff_limited(staged=True)


def _worktree_is_clean(unstaged_diff: str, staged_diff: str) -> bool:
    """Return True when there are no staged or unstaged changes."""
    return not unstaged_diff and not staged_diff


def _stage_if_needed(options: RuntimeOptions, staged_diff: str) -> str:
    """Stage all changes when auto-stage is enabled and return the staged diff."""
    if not options.auto_stage_enabled:
        return staged_diff
    print("[info] Staging all changes (`git add -A`).")
    run_command(["git", "add", "-A"], check=True)
    return gather_git_diff_limited(staged=True)


def _warn_missing_staged_changes() -> None:
    """Warn when a commit message was requested without staged changes."""
    print(
        "[warn] No staged changes detected. Stage files before requesting a commit message.",
        file=sys.stderr,
    )


def _maybe_request_commit_message(
    options: RuntimeOptions,
    staged_diff: str,
    extra_context: str,
) -> tuple[Optional[str], list[str]]:
    """Request a commit message from Codex when the mode is enabled."""
    if not options.commit_message_enabled:
        return None, []
    summary, body_lines = request_commit_message(
        model=options.model_name,
        reasoning_effort=options.reasoning_effort,
        staged_diff=staged_diff,
        extra_context=extra_context,
        detailed=options.auto_push_enabled,
    )
    preview_lines: list[str] = [summary]
    if body_lines:
        preview_lines.append("")
        preview_lines.extend(body_lines)
    print("[info] Suggested commit message:")
    for line in preview_lines:
        if line:
            print(f"    {line}")
        else:
            print("")
    return summary, body_lines


def _maybe_push_or_notify(
    options: RuntimeOptions,
    summary: Optional[str],
    body_lines: list[str],
) -> None:
    """Push automatically or prompt the user to commit manually."""
    if options.auto_push_enabled:
        if not summary:
            raise ValueError("Commit summary is required for auto-push")
        commit_and_push(summary, body_lines, push=True)
        return
    if summary is not None:
        print("[info] Commit message ready; run `git commit` manually if desired.")


def finalize_worktree(args: argparse.Namespace, options: RuntimeOptions) -> int:
    """Stage, commit, and optionally push once CI passes."""
    unstaged_diff, staged_diff = _collect_worktree_diffs()
    if _worktree_is_clean(unstaged_diff, staged_diff):
        print("[info] Working tree clean. Nothing to stage or commit.")
        return 0

    staged_diff = _stage_if_needed(options, staged_diff)
    if not staged_diff:
        _warn_missing_staged_changes()
        return 0

    summary, body_lines = _maybe_request_commit_message(
        options, staged_diff, args.commit_extra_context
    )
    _maybe_push_or_notify(options, summary, body_lines)
    return 0


def run_repair_iterations(args: argparse.Namespace, options: RuntimeOptions) -> None:
    """Loop CI execution and Codex interactions until the command succeeds."""
    seen_patches: Set[str] = set()
    for iteration in range(1, args.max_iterations + 1):
        print(f"[loop] Iteration {iteration} — running `{args.command}`")
        result = run_command(options.command_tokens, live=True, env=options.command_env)
        coverage_report = (
            extract_coverage_deficits(result.combined_output) if result.ok else None
        )
        if result.ok and coverage_report is None:
            print(f"[loop] CI command succeeded on iteration {iteration}.")
            return
        failure_ctx = build_failure_context(args, result, coverage_report)
        request_and_apply_patches(
            args=args,
            options=options,
            failure_ctx=failure_ctx,
            iteration=iteration,
            seen_patches=seen_patches,
        )
    raise PatchLifecycleAbort.attempts_exhausted()


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments for the workflow CLI."""
    parser = argparse.ArgumentParser(
        description="Automate CI fixes via LLM (Codex or Claude)."
    )
    parser.add_argument(
        "--command",
        help="Command to run for CI (initial: ./scripts/ci.sh)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        help="Maximum LLM-assisted fix attempts (initial: 5)",
    )
    parser.add_argument(
        "--log-tail",
        type=int,
        help="Number of log lines from the failure to send to LLM (initial: 200)",
    )
    parser.add_argument(
        "--model",
        help=f"LLM model name (required: {REQUIRED_MODEL})",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=REASONING_EFFORT_CHOICES,
        help=f"Reasoning effort hint for LLM (initial: {DEFAULT_REASONING_EFFORT})",
    )
    parser.add_argument(
        "--max-patch-lines",
        type=int,
        help="Abort if LLM suggests touching more than this many lines (initial: 1500)",
    )
    parser.add_argument(
        "--patch-approval-mode",
        choices=("prompt", "auto"),
        help="Control whether patch application requires approval (initial: prompt)",
    )
    parser.add_argument(
        "--auto-stage",
        action="store_true",
        help="After CI passes, run `git add -A` before asking for a commit message.",
    )
    parser.add_argument(
        "--commit-message",
        action="store_true",
        help="Request a commit message from LLM after CI succeeds.",
    )
    parser.add_argument(
        "--commit-extra-context",
        help="Additional instructions for the commit message prompt.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the CI command once and exit without contacting LLM.",
    )
    parser.add_argument(
        "--env-file",
        help="Path to dotenv file for LLM CLI environment initial values (initial: ~/.env)",
    )
    parser.add_argument(
        "--patch-retries",
        type=int,
        help="Number of additional patch attempts when apply fails (initial: 1)",
    )
    parser.set_defaults(
        command="./scripts/ci.sh",
        max_iterations=5,
        log_tail=200,
        max_patch_lines=1500,
        patch_approval_mode="prompt",
        commit_extra_context="",
        env_file="~/.env",
        patch_retries=1,
    )
    parsed_args = list(argv) if argv is not None else None
    return parser.parse_args(parsed_args)


def main(argv: Optional[Iterable[str]] = None) -> int:
    """Entry point for running the CI automation workflow."""
    args = parse_args(argv)
    try:
        options = configure_runtime(args)
        dry_run_exit = perform_dry_run(args, options)
        if dry_run_exit is not None:
            return dry_run_exit
        run_repair_iterations(args, options)
    except KeyboardInterrupt:
        print("\n[info] Received Ctrl-C. Aborting ci.py cleanly.")
        return 130
    except CiAbort as exc:
        if exc.detail:
            print(f"[error] {exc.detail}", file=sys.stderr)
        return exc.exit_code
    return finalize_worktree(args, options)


__all__ = [
    "main",
    "configure_runtime",
    "perform_dry_run",
    "run_repair_iterations",
    "finalize_worktree",
]
