"""Core data models and exception types for the CI runtime."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Iterable, Optional

from .._messages import format_default_message


def _normalize_output(*sources: Optional[str]) -> str:
    """Combine and strip multiple output sources into a single string.

    Returns the first non-empty stripped string from the provided sources,
    or an empty string if all sources are None or empty.
    """
    for source in sources:
        if source is not None:
            stripped = source.strip()
            if stripped:
                return stripped
    return ""


def _normalize_output_with_placeholder(*sources: Optional[str]) -> str:
    """Combine output sources and return a placeholder if all are empty."""
    result = _normalize_output(*sources)
    if not result:
        return "(no output)"
    return result


def display_value(value: Optional[str], placeholder: str) -> str:
    """Return the value if non-empty, otherwise the placeholder.

    This is the canonical way to display optional values without using
    the banned `value or placeholder` pattern.
    """
    if value:
        return value
    return placeholder


class CiError(RuntimeError):
    """Base class for CI automation runtime failures."""

    default_message = "CI automation failure"

    def __init__(self, *, detail: Optional[str] = None) -> None:
        """Initialise the exception with an optional detail string."""
        self.detail = detail
        message = format_default_message(self.default_message, detail)
        super().__init__(message)


class CodexCliError(CiError):
    """Raised when invoking the Codex CLI returns a non-zero status."""

    default_message = "Codex CLI command failed"

    @classmethod
    def exit_status(cls, *, returncode: int, output: Optional[str]) -> "CodexCliError":
        """Build an error containing the CLI exit status and captured output."""
        normalized = _normalize_output_with_placeholder(output)
        detail = f"exit status {returncode} ({normalized})"
        return cls(detail=detail)


class CommitMessageError(CiError):
    """Raised when commit message generation fails."""

    default_message = "Commit message generation failed"

    @classmethod
    def empty_response(cls) -> "CommitMessageError":
        """Return an error signalling the Codex response was blank."""
        return cls(detail="Commit message response was empty")

    @classmethod
    def invalid_response(cls, *, reason: str) -> "CommitMessageError":
        """Return an error signalling the Codex response violated format expectations."""
        return cls(detail=reason)


class CiAbort(SystemExit):
    """Base class for deliberate CI workflow exits."""

    default_message = "CI automation aborted"

    def __init__(self, *, detail: Optional[str] = None, code: int = 1) -> None:
        """Initialise the abort with a user-facing detail string."""
        self.detail = detail
        self.exit_code = code
        message = format_default_message(self.default_message, detail)
        super().__init__(message)
        self.code = code


class GitCommandAbort(CiAbort):
    """Raised when git operations fail during CI automation."""

    default_message = "Git command failed"

    @classmethod
    def commit_failed(cls, exc: subprocess.CalledProcessError) -> "GitCommandAbort":
        """Return an error capturing a failed git commit invocation."""
        output = _normalize_output(exc.stderr, exc.output)
        detail = f"'git commit' exited with status {exc.returncode}"
        if output:
            detail = f"{detail}; {output}"
        return cls(detail=detail)

    @classmethod
    def push_failed(cls, exc: subprocess.CalledProcessError) -> "GitCommandAbort":
        """Return an error capturing a failed git push invocation."""
        output = _normalize_output(exc.stderr, exc.output)
        detail = f"'git push' exited with status {exc.returncode}"
        if output:
            detail = f"{detail}; {output}"
        return cls(detail=detail)

    @classmethod
    def missing_remote(cls) -> "GitCommandAbort":
        """Return an error when GIT_REMOTE environment variable is not set."""
        return cls(detail="GIT_REMOTE environment variable is required")


class RepositoryStateAbort(CiAbort):
    """Raised when the repository is not in a valid state for CI automation."""

    default_message = "Repository state invalid"

    @classmethod
    def detached_head(cls) -> "RepositoryStateAbort":
        """Factory raised when running outside a branch (detached HEAD)."""
        return cls(
            detail="detached HEAD detected; checkout a branch before running ci.py"
        )


class ModelSelectionAbort(CiAbort):
    """Raised when an unsupported model is provided to the CI workflow."""

    default_message = "Unsupported model configuration"

    @classmethod
    def unsupported_model(
        cls, *, received: str, required: str
    ) -> "ModelSelectionAbort":
        """Factory when a CLI caller passes an unsupported model."""
        return cls(detail=f"requires `{required}` but received `{received}`")


class ReasoningEffortAbort(CiAbort):
    """Raised when an unsupported reasoning effort value is supplied."""

    default_message = "Unsupported reasoning effort"

    @classmethod
    def unsupported_choice(
        cls, *, received: str, allowed: Iterable[str]
    ) -> "ReasoningEffortAbort":
        """Factory when the reasoning effort flag is not recognised."""
        choices = ", ".join(allowed)
        return cls(detail=f"expected one of {choices}; received `{received}`")


class PatchLifecycleAbort(CiAbort):
    """Raised when the automated patch workflow cannot continue."""

    default_message = "Patch workflow aborted"

    @classmethod
    def attempts_exhausted(cls) -> "PatchLifecycleAbort":
        """Factory when Codex could not produce a valid patch in time."""
        return cls(detail="unable to obtain a valid patch after multiple attempts")

    @classmethod
    def missing_patch(cls) -> "PatchLifecycleAbort":
        """Factory when Codex responded without a usable patch diff."""
        return cls(detail="Codex returned an empty or NOOP patch response")

    @classmethod
    def user_declined(cls) -> "PatchLifecycleAbort":
        """Factory when the user stops automation during a patch cycle."""
        return cls(detail="user declined CI automation")

    @classmethod
    def retries_exhausted(cls) -> "PatchLifecycleAbort":
        """Factory when patch retries were exhausted after repeated failures."""
        return cls(
            detail="Codex patches failed after exhausting retries; manual review required"
        )


@dataclass
class CommandResult:
    """Captured output from a completed subprocess invocation."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """Return True when the process exited successfully."""
        return self.returncode == 0

    @property
    def combined_output(self) -> str:
        """Return stdout and stderr concatenated together."""
        return f"{self.stdout}{self.stderr}"


@dataclass
class CommandConfig:
    """Command execution configuration."""

    tokens: list[str]
    env: dict[str, str]


@dataclass
class WorkflowConfig:
    """Workflow behavior configuration."""

    patch_approval_mode: str
    automation_mode: bool
    auto_stage_enabled: bool
    commit_message_enabled: bool
    auto_push_enabled: bool


@dataclass
class ModelConfig:
    """Model configuration."""

    name: str
    reasoning_effort: str


@dataclass
class RuntimeOptions:
    """Configuration flags governing how the CI workflow runs."""

    command: CommandConfig
    workflow: WorkflowConfig
    model: ModelConfig

    @property
    def command_tokens(self) -> list[str]:
        """Command tokens for execution."""
        return self.command.tokens

    @property
    def command_env(self) -> dict[str, str]:
        """Command environment variables."""
        return self.command.env

    @property
    def patch_approval_mode(self) -> str:
        """Patch approval mode."""
        return self.workflow.patch_approval_mode

    @property
    def automation_mode(self) -> bool:
        """Whether automation mode is enabled."""
        return self.workflow.automation_mode

    @property
    def auto_stage_enabled(self) -> bool:
        """Whether auto staging is enabled."""
        return self.workflow.auto_stage_enabled

    @property
    def commit_message_enabled(self) -> bool:
        """Whether commit message generation is enabled."""
        return self.workflow.commit_message_enabled

    @property
    def auto_push_enabled(self) -> bool:
        """Whether auto push is enabled."""
        return self.workflow.auto_push_enabled

    @property
    def model_name(self) -> str:
        """Model name."""
        return self.model.name

    @property
    def reasoning_effort(self) -> str:
        """Reasoning effort level."""
        return self.model.reasoning_effort


@dataclass
class FailureContext:
    """Summary of the most recent CI failure provided to Codex."""

    log_excerpt: str
    summary: str
    implicated_files: list[str]
    focused_diff: str
    coverage_report: Optional["CoverageCheckResult"]


@dataclass
class PatchAttemptState:
    """Track Codex patch attempts and retry budget."""

    max_attempts: int
    patch_attempt: int = 1
    extra_retry_budget: int = 3
    last_error: Optional[str] = None

    def ensure_budget(self) -> None:
        """Abort when the patch attempt counter exceeds the allowed budget."""
        if self.patch_attempt > self.max_attempts:
            raise PatchLifecycleAbort.attempts_exhausted()

    def record_failure(self, message: str, *, retryable: bool) -> None:
        """Record a patch failure and expand the budget when retries remain."""
        self.last_error = message
        if self.patch_attempt >= self.max_attempts:
            if retryable and self.extra_retry_budget > 0:
                self.extra_retry_budget -= 1
                self.max_attempts += 1
            else:
                raise PatchLifecycleAbort.retries_exhausted()
        self.patch_attempt += 1


class PatchApplyError(CiError):
    """Raised when git or patch apply steps fail."""

    default_message = "Patch application failed"

    def __init__(self, *, detail: Optional[str] = None, retryable: bool = True) -> None:
        super().__init__(detail=detail)
        self.retryable = retryable

    @classmethod
    def git_apply_failed(cls, *, output: str) -> "PatchApplyError":
        """Factory when `git apply` fails to dry-run or apply the diff."""
        normalized = _normalize_output_with_placeholder(output)
        detail = f"`git apply` failed: {normalized}"
        return cls(detail=detail, retryable=True)

    @classmethod
    def preflight_failed(
        cls, *, check_output: str, dry_output: str
    ) -> "PatchApplyError":
        """Factory when both git and patch dry-runs are unable to apply."""
        check_display = display_value(_normalize_output(check_output), "(none)")
        dry_display = display_value(_normalize_output(dry_output), "(none)")
        detail = (
            "Patch dry-run failed.\n"
            f"git apply --check output:\n{check_display}\n\n"
            f"patch --dry-run output:\n{dry_display}"
        )
        return cls(detail=detail, retryable=True)

    @classmethod
    def patch_exit(cls, *, returncode: int, output: str) -> "PatchApplyError":
        """Factory when the POSIX `patch` utility exits with a non-zero code."""
        normalized = _normalize_output_with_placeholder(output)
        detail = f"`patch` exited with status {returncode}: {normalized}"
        return cls(detail=detail, retryable=True)


@dataclass
class CoverageDeficit:
    """Coverage percentage for a single module below the configured threshold."""

    path: str
    coverage: float


@dataclass
class CoverageCheckResult:
    """Aggregate report returned by the coverage guard."""

    table_text: str
    deficits: list[CoverageDeficit]
    threshold: float


@dataclass
class PatchPrompt:
    """Contextual information sent to Codex when requesting a patch."""

    command: str
    failure_context: FailureContext
    git_diff: str
    git_status: str
    iteration: int
    patch_error: Optional[str]
    attempt: int


__all__ = [
    "display_value",
    "CiError",
    "CodexCliError",
    "CommitMessageError",
    "CiAbort",
    "GitCommandAbort",
    "RepositoryStateAbort",
    "ModelSelectionAbort",
    "ReasoningEffortAbort",
    "PatchLifecycleAbort",
    "PatchApplyError",
    "CommandResult",
    "CommandConfig",
    "WorkflowConfig",
    "ModelConfig",
    "RuntimeOptions",
    "FailureContext",
    "PatchAttemptState",
    "CoverageDeficit",
    "CoverageCheckResult",
    "PatchPrompt",
]
