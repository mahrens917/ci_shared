"""Unit tests for ci_tools.ci_runtime.models module."""

from __future__ import annotations

import subprocess

import pytest

from ci_tools.ci_runtime.models import (
    CiAbort,
    CiError,
    CodexCliError,
    CommandConfig,
    CommandResult,
    CommitMessageError,
    CoverageCheckResult,
    CoverageDeficit,
    FailureContext,
    GitCommandAbort,
    ModelConfig,
    ModelSelectionAbort,
    PatchApplyError,
    PatchAttemptState,
    PatchLifecycleAbort,
    PatchPrompt,
    ReasoningEffortAbort,
    RepositoryStateAbort,
    RuntimeOptions,
    WorkflowConfig,
)


class TestCiError:
    """Tests for CiError base exception."""

    def test_default_message_no_detail(self):
        """Test CiError with no detail uses default message."""
        error = CiError()
        assert str(error) == "CI automation failure"
        assert error.detail is None

    def test_with_detail(self):
        """Test CiError with detail appends it to message."""
        error = CiError(detail="something went wrong")
        assert str(error) == "CI automation failure: something went wrong"
        assert error.detail == "something went wrong"

    def test_is_runtime_error(self):
        """Test CiError is a RuntimeError subclass."""
        error = CiError()
        assert isinstance(error, RuntimeError)


class TestCodexCliError:
    """Tests for CodexCliError exception."""

    def test_exit_status_with_output(self):
        """Test factory method with exit status and output."""
        error = CodexCliError.exit_status(returncode=127, output="command not found")
        assert "exit status 127" in str(error)
        assert "command not found" in str(error)

    def test_exit_status_without_output(self):
        """Test factory method with no output."""
        error = CodexCliError.exit_status(returncode=1, output=None)
        assert "exit status 1" in str(error)
        assert "(no output)" in str(error)

    def test_exit_status_empty_output(self):
        """Test factory method with empty string output."""
        error = CodexCliError.exit_status(returncode=2, output="   ")
        assert "exit status 2" in str(error)
        assert "(no output)" in str(error)


# pylint: disable=too-few-public-methods
class TestCommitMessageError:
    """Tests for CommitMessageError exception."""

    def test_empty_response_factory(self):
        """Test empty_response factory method."""
        error = CommitMessageError.empty_response()
        assert "Commit message response was empty" in str(error)
        assert error.detail is None


class TestCiAbort:
    """Tests for CiAbort base exception."""

    def test_default_exit_code(self):
        """Test CiAbort defaults to exit code 1."""
        abort = CiAbort()
        assert abort.exit_code == 1
        assert abort.code == 1

    def test_custom_exit_code(self):
        """Test CiAbort with custom exit code."""
        abort = CiAbort(code=42)
        assert abort.exit_code == 42
        assert abort.code == 42

    def test_with_detail(self):
        """Test CiAbort with detail message."""
        abort = CiAbort(detail="user cancelled")
        assert "user cancelled" in str(abort)
        assert abort.detail == "user cancelled"

    def test_is_system_exit(self):
        """Test CiAbort is a SystemExit subclass."""
        abort = CiAbort()
        assert isinstance(abort, SystemExit)


class TestGitCommandAbort:
    """Tests for GitCommandAbort exception."""

    def test_commit_failed_with_stderr(self):
        """Test commit_failed factory with stderr output."""
        exc = subprocess.CalledProcessError(
            returncode=1,
            cmd=["git", "commit"],
            stderr="fatal: not a git repository",
        )
        abort = GitCommandAbort.commit_failed(exc)
        assert "git commit" in str(abort)
        assert "status 1" in str(abort)
        assert "fatal: not a git repository" in str(abort)

    def test_commit_failed_with_output(self):
        """Test commit_failed factory with output instead of stderr."""
        exc = subprocess.CalledProcessError(
            returncode=1,
            cmd=["git", "commit"],
            output="nothing to commit",
        )
        abort = GitCommandAbort.commit_failed(exc)
        assert "nothing to commit" in str(abort)

    def test_commit_failed_no_output(self):
        """Test commit_failed factory with no output."""
        exc = subprocess.CalledProcessError(returncode=1, cmd=["git", "commit"])
        abort = GitCommandAbort.commit_failed(exc)
        assert "git commit" in str(abort)
        assert "status 1" in str(abort)

    def test_push_failed_with_stderr(self):
        """Test push_failed factory with stderr."""
        exc = subprocess.CalledProcessError(
            returncode=128,
            cmd=["git", "push"],
            stderr="error: failed to push",
        )
        abort = GitCommandAbort.push_failed(exc)
        assert "git push" in str(abort)
        assert "status 128" in str(abort)
        assert "failed to push" in str(abort)

# pylint: disable=too-few-public-methods

class TestRepositoryStateAbort:
    """Tests for RepositoryStateAbort exception."""

    def test_detached_head_factory(self):
        """Test detached_head factory method."""
        abort = RepositoryStateAbort.detached_head()
        assert "detached HEAD detected" in str(abort)
        assert "checkout a branch" in str(abort)
# pylint: disable=too-few-public-methods


class TestModelSelectionAbort:
    """Tests for ModelSelectionAbort exception."""

    def test_unsupported_model_factory(self):
        """Test unsupported_model factory method."""
        abort = ModelSelectionAbort.unsupported_model(received="gpt-4", required="gpt-5-codex")
        assert "gpt-5-codex" in str(abort)
# pylint: disable=too-few-public-methods
        assert "gpt-4" in str(abort)


class TestReasoningEffortAbort:
    """Tests for ReasoningEffortAbort exception."""

    def test_unsupported_choice_factory(self):
        """Test unsupported_choice factory method."""
        abort = ReasoningEffortAbort.unsupported_choice(
            received="extreme", allowed=["low", "medium", "high"]
        )
        assert "extreme" in str(abort)
        assert "low, medium, high" in str(abort)


class TestPatchLifecycleAbort:
    """Tests for PatchLifecycleAbort exception."""

    def test_attempts_exhausted(self):
        """Test attempts_exhausted factory method."""
        abort = PatchLifecycleAbort.attempts_exhausted()
        assert "unable to obtain a valid patch" in str(abort)

    def test_missing_patch(self):
        """Test missing_patch factory method."""
        abort = PatchLifecycleAbort.missing_patch()
        assert "empty or NOOP patch" in str(abort)

    def test_user_declined(self):
        """Test user_declined factory method."""
        abort = PatchLifecycleAbort.user_declined()
        assert "user declined" in str(abort)

    def test_retries_exhausted(self):
        """Test retries_exhausted factory method."""
        abort = PatchLifecycleAbort.retries_exhausted()
        assert "exhausting retries" in str(abort)
        assert "manual review required" in str(abort)


class TestCommandResult:
    """Tests for CommandResult dataclass."""

    def test_ok_property_success(self):
        """Test ok property returns True for zero exit code."""
        result = CommandResult(returncode=0, stdout="success", stderr="")
        assert result.ok is True

    def test_ok_property_failure(self):
        """Test ok property returns False for non-zero exit code."""
        result = CommandResult(returncode=1, stdout="", stderr="error")
        assert result.ok is False

    def test_combined_output(self):
        """Test combined_output concatenates stdout and stderr."""
        result = CommandResult(returncode=0, stdout="hello\n", stderr="world\n")
        assert result.combined_output == "hello\nworld\n"

    def test_combined_output_empty(self):
        """Test combined_output with empty streams."""
# pylint: disable=too-few-public-methods
        result = CommandResult(returncode=0, stdout="", stderr="")
        assert result.combined_output == ""


class TestRuntimeOptions:
    """Tests for RuntimeOptions dataclass."""

    def test_creation_with_all_fields(self):
        """Test RuntimeOptions with all fields."""
        options = RuntimeOptions(
            command=CommandConfig(tokens=["make", "test"], env={"FOO": "bar"}),
            workflow=WorkflowConfig(
                patch_approval_mode="prompt",
                automation_mode=True,
                auto_stage_enabled=False,
                commit_message_enabled=True,
                auto_push_enabled=False,
            ),
            model=ModelConfig(name="gpt-5-codex", reasoning_effort="high"),
        )
        assert options.command_tokens == ["make", "test"]
        assert options.command_env == {"FOO": "bar"}
        assert options.patch_approval_mode == "prompt"
        assert options.automation_mode is True
        assert options.auto_stage_enabled is False
        assert options.commit_message_enabled is True
        assert options.auto_push_enabled is False
        assert options.model_name == "gpt-5-codex"
        assert options.reasoning_effort == "high"


class TestFailureContext:
    """Tests for FailureContext dataclass."""

    def test_creation_without_coverage_report(self):
        """Test FailureContext creation without coverage report."""
        context = FailureContext(
            log_excerpt="Error: test failed",
            summary="Tests failed in module X",
            implicated_files=["src/module.py"],
            focused_diff="diff --git...",
            coverage_report=None,
        )
        assert context.log_excerpt == "Error: test failed"
        assert context.summary == "Tests failed in module X"
        assert context.implicated_files == ["src/module.py"]
        assert context.focused_diff == "diff --git..."
        assert context.coverage_report is None

    def test_creation_with_coverage_report(self):
        """Test FailureContext with coverage report."""
        coverage = CoverageCheckResult(table_text="coverage table", deficits=[], threshold=80.0)
        context = FailureContext(
            log_excerpt="",
            summary="",
            implicated_files=[],
            focused_diff="",
            coverage_report=coverage,
        )
        assert context.coverage_report is not None
        assert context.coverage_report.threshold == 80.0


class TestPatchAttemptState:
    """Tests for PatchAttemptState dataclass."""

    def test_initial_state(self):
        """Test initial state of PatchAttemptState."""
        state = PatchAttemptState(max_attempts=5)
        assert state.max_attempts == 5
        assert state.patch_attempt == 1
        assert state.extra_retry_budget == 3
        assert state.last_error is None

    def test_ensure_budget_success(self):
        """Test ensure_budget when within budget."""
        state = PatchAttemptState(max_attempts=5, patch_attempt=3)
        state.ensure_budget()  # Should not raise

    def test_ensure_budget_exhausted(self):
        """Test ensure_budget raises when budget exceeded."""
        state = PatchAttemptState(max_attempts=5, patch_attempt=6)
        with pytest.raises(PatchLifecycleAbort) as exc_info:
            state.ensure_budget()
        assert "unable to obtain a valid patch" in str(exc_info.value)

    def test_record_failure_increments_attempt(self):
        """Test record_failure increments patch_attempt."""
        state = PatchAttemptState(max_attempts=5)
        state.record_failure("test error", retryable=True)
        assert state.patch_attempt == 2
        assert state.last_error == "test error"

    def test_record_failure_uses_retry_budget(self):
        """Test record_failure uses extra retry budget when at max."""
        state = PatchAttemptState(max_attempts=3, patch_attempt=3)
        state.record_failure("error 1", retryable=True)
        assert state.max_attempts == 4
        assert state.extra_retry_budget == 2
        assert state.patch_attempt == 4

    def test_record_failure_exhausts_retries(self):
        """Test record_failure raises when retries exhausted."""
        state = PatchAttemptState(max_attempts=3, patch_attempt=3, extra_retry_budget=0)
        with pytest.raises(PatchLifecycleAbort) as exc_info:
            state.record_failure("final error", retryable=True)
        assert "exhausting retries" in str(exc_info.value)

    def test_record_failure_non_retryable(self):
        """Test record_failure with non-retryable error."""
        state = PatchAttemptState(max_attempts=3, patch_attempt=3)
        with pytest.raises(PatchLifecycleAbort) as exc_info:
            state.record_failure("fatal error", retryable=False)
        assert "exhausting retries" in str(exc_info.value)


class TestPatchApplyError:
    """Tests for PatchApplyError exception."""

    def test_git_apply_failed_factory(self):
        """Test git_apply_failed factory method."""
        error = PatchApplyError.git_apply_failed(output="error: patch failed")
        assert "git apply" in str(error)
        assert "patch failed" in str(error)
        assert error.retryable is True

    def test_git_apply_failed_no_output(self):
        """Test git_apply_failed with no output."""
        error = PatchApplyError.git_apply_failed(output="")
        assert "(no output)" in str(error)

    def test_preflight_failed_factory(self):
        """Test preflight_failed factory method."""
        error = PatchApplyError.preflight_failed(
            check_output="git check failed", dry_output="patch dry run failed"
        )
        assert "Patch dry-run failed" in str(error)
        assert "git check failed" in str(error)
        assert "patch dry run failed" in str(error)
        assert error.retryable is True

    def test_patch_exit_factory(self):
        """Test patch_exit factory method."""
        error = PatchApplyError.patch_exit(returncode=1, output="malformed patch")
        assert "exited with status 1" in str(error)
        assert "malformed patch" in str(error)
        assert error.retryable is True

    def test_custom_retryable_flag(self):
# pylint: disable=too-few-public-methods
        """Test PatchApplyError with custom retryable flag."""
        error = PatchApplyError(detail="fatal", retryable=False)
        assert error.retryable is False


class TestCoverageDeficit:
    """Tests for CoverageDeficit dataclass."""

    def test_creation(self):
        """Test CoverageDeficit creation."""
        deficit = CoverageDeficit(path="src/module.py", coverage=65.5)
        assert deficit.path == "src/module.py"
        assert deficit.coverage == 65.5


class TestCoverageCheckResult:
    """Tests for CoverageCheckResult dataclass."""

    def test_creation_with_deficits(self):
        """Test CoverageCheckResult with deficits."""
        deficits = [
            CoverageDeficit(path="src/a.py", coverage=70.0),
            CoverageDeficit(path="src/b.py", coverage=75.5),
        ]
        result = CoverageCheckResult(
            table_text="Name | Stmts | Miss | Cover\n",
            deficits=deficits,
            threshold=80.0,
        )
        assert result.table_text == "Name | Stmts | Miss | Cover\n"
        assert len(result.deficits) == 2
        assert result.threshold == 80.0

    def test_creation_no_deficits(self):
        """Test CoverageCheckResult with no deficits."""
        result = CoverageCheckResult(table_text="", deficits=[], threshold=80.0)
        assert not result.deficits


class TestPatchPrompt:
    """Tests for PatchPrompt dataclass."""

    def test_creation_with_all_fields(self):
        """Test PatchPrompt with all fields."""
        context = FailureContext(
            log_excerpt="error log",
            summary="summary",
            implicated_files=["file.py"],
            focused_diff="diff",
            coverage_report=None,
        )
        prompt = PatchPrompt(
            command="make test",
            failure_context=context,
            git_diff="diff output",
            git_status="M file.py",
            iteration=2,
            patch_error="previous patch failed",
            attempt=3,
        )
        assert prompt.command == "make test"
        assert prompt.failure_context == context
        assert prompt.git_diff == "diff output"
        assert prompt.git_status == "M file.py"
        assert prompt.iteration == 2
        assert prompt.patch_error == "previous patch failed"
        assert prompt.attempt == 3

    def test_creation_no_patch_error(self):
        """Test PatchPrompt without patch error."""
        context = FailureContext(
            log_excerpt="",
            summary="",
            implicated_files=[],
            focused_diff="",
            coverage_report=None,
        )
        prompt = PatchPrompt(
            command="pytest",
            failure_context=context,
            git_diff="",
            git_status="",
            iteration=1,
            patch_error=None,
            attempt=1,
        )
        assert prompt.patch_error is None
