"""Patch iteration helpers for the CI workflow."""

from __future__ import annotations

from typing import Optional, Set

from .codex import (
    extract_unified_diff,
    has_unified_diff_header,
    request_codex_patch,
)
from .models import (
    FailureContext,
    PatchApplyError,
    PatchAttemptState,
    PatchLifecycleAbort,
    PatchPrompt,
    RuntimeOptions,
)
from .patching import apply_patch, patch_looks_risky
from .process import gather_git_diff_limited, gather_git_status


def _obtain_patch_diff(*, options: RuntimeOptions, prompt: PatchPrompt) -> str:
    """Request a patch from Codex and return its diff text."""
    response = request_codex_patch(
        model=options.model_name,
        reasoning_effort=options.reasoning_effort,
        prompt=prompt,
    )
    if not response:
        raise PatchLifecycleAbort.missing_patch()
    diff_text = extract_unified_diff(response)
    if not diff_text:
        raise PatchLifecycleAbort.missing_patch()
    return diff_text


def _validate_patch_candidate(
    diff_text: str,
    *,
    seen_patches: Set[str],
    max_patch_lines: int,
) -> Optional[str]:
    """Return a validation error string when the diff should be rejected."""
    if diff_text in seen_patches:
        return "Duplicate patch received; provide an alternative diff."
    seen_patches.add(diff_text)
    if not has_unified_diff_header(diff_text):
        return "Patch missing unified diff headers (diff --git/---/+++ lines)."
    is_risky, reason = patch_looks_risky(diff_text, max_lines=max_patch_lines)
    if is_risky:
        if not reason:
            return "Patch failed safety checks."
        return reason
    return None


def _apply_patch_candidate(
    diff_text: str,
    *,
    state: PatchAttemptState,
) -> bool:
    """Apply the diff and update state, returning True on success."""
    try:
        apply_patch(diff_text)
    except PatchApplyError as exc:
        state.record_failure(str(exc), retryable=exc.retryable)
        return False
    except RuntimeError as exc:  # pragma: no cover - defensive
        state.record_failure(str(exc), retryable=False)
        return False
    state.last_error = None
    return True


def _should_apply_patch(
    *,
    approval_mode: str,
    attempt: int,
) -> bool:
    """Return True when the user (or automation) approves applying the patch."""
    if approval_mode == "auto":
        print(f"[codex] Auto-approving patch attempt {attempt}.")
        return True
    decision = (
        input(f"[prompt] Apply patch attempt {attempt}? [y]es/[n]o/[q]uit: ")
        .strip()
        .lower()
    )
    if decision in {"q", "quit"}:
        raise PatchLifecycleAbort.user_declined()
    return decision in {"y", "yes", ""}  # treat empty input as yes


def request_and_apply_patches(
    *,
    args,
    options: RuntimeOptions,
    failure_ctx: FailureContext,
    iteration: int,
    seen_patches: Set[str],
) -> None:
    """Iteratively request and apply patches until one succeeds or retries are exhausted."""
    state = PatchAttemptState(max_attempts=args.patch_retries + 1)
    while True:
        state.ensure_budget()
        print(f"[codex] Requesting patch attempt {state.patch_attempt}...")
        prompt = PatchPrompt(
            command=args.command,
            failure_context=failure_ctx,
            git_diff=gather_git_diff_limited(staged=False),
            git_status=gather_git_status(),
            iteration=iteration,
            patch_error=state.last_error,
            attempt=state.patch_attempt,
        )
        diff_text = _obtain_patch_diff(options=options, prompt=prompt)
        validation_error = _validate_patch_candidate(
            diff_text,
            seen_patches=seen_patches,
            max_patch_lines=args.max_patch_lines,
        )
        if validation_error:
            state.record_failure(validation_error, retryable=True)
            continue
        if not _should_apply_patch(
            approval_mode=options.patch_approval_mode,
            attempt=state.patch_attempt,
        ):
            state.record_failure("User declined to apply the patch.", retryable=True)
            continue
        if _apply_patch_candidate(diff_text, state=state):
            post_status = gather_git_status()
            if post_status:
                print("[info] git status after patch:")
                print(post_status)
            else:
                print("[info] Working tree is clean after applying patch.")
            return
        # Defensive check: ensure state was updated when apply fails
        if not state.last_error:
            state.record_failure("Patch application failed", retryable=False)


__all__ = ["request_and_apply_patches"]
