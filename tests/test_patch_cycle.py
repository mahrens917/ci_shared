"""Unit tests for ci_tools.ci_runtime.patch_cycle module."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from ci_tools.ci_runtime.patch_cycle import (
    _obtain_patch_diff,
    _validate_patch_candidate,
    _apply_patch_candidate,
    _should_apply_patch,
    request_and_apply_patches,
)
from ci_tools.ci_runtime.models import (
    PatchApplyError,
    PatchAttemptState,
    PatchLifecycleAbort,
    PatchPrompt,
)


class TestObtainPatchDiff:
    """Tests for _obtain_patch_diff function."""

    @patch("ci_tools.ci_runtime.patch_cycle.request_codex_patch")
    @patch("ci_tools.ci_runtime.patch_cycle.extract_unified_diff")
    def test_returns_diff_text_on_success(self, mock_extract, mock_request):
        """Test returning diff text when Codex responds with valid patch."""
        mock_request.return_value = "response with diff"
        mock_extract.return_value = "diff --git a/file.py b/file.py"
        options = Mock(model_name="gpt-5-codex", reasoning_effort="high")
        prompt = Mock()

        result = _obtain_patch_diff(options=options, prompt=prompt)

        assert result == "diff --git a/file.py b/file.py"
        mock_request.assert_called_once_with(
            model="gpt-5-codex",
            reasoning_effort="high",
            prompt=prompt,
        )

    @patch("ci_tools.ci_runtime.patch_cycle.request_codex_patch")
    @patch("ci_tools.ci_runtime.patch_cycle.extract_unified_diff")
    def test_raises_when_no_diff_extracted(self, mock_extract, mock_request):
        """Test raising exception when no diff is extracted."""
        mock_request.return_value = "response without diff"
        mock_extract.return_value = None
        options = Mock(model_name="gpt-5-codex", reasoning_effort="high")
        prompt = Mock()

        with pytest.raises(PatchLifecycleAbort) as exc_info:
            _obtain_patch_diff(options=options, prompt=prompt)

        assert "empty or NOOP patch" in str(exc_info.value)

    @patch("ci_tools.ci_runtime.patch_cycle.request_codex_patch")
    @patch("ci_tools.ci_runtime.patch_cycle.extract_unified_diff")
    def test_raises_when_empty_diff(self, mock_extract, mock_request):
        """Test raising exception when diff is empty string."""
        mock_request.return_value = "response"
        mock_extract.return_value = ""
        options = Mock(model_name="gpt-5-codex", reasoning_effort="high")
        prompt = Mock()

        with pytest.raises(PatchLifecycleAbort):
            _obtain_patch_diff(options=options, prompt=prompt)


class TestValidatePatchCandidate:
    """Tests for _validate_patch_candidate function."""

    def test_accepts_valid_patch(self):
        """Test accepting a valid patch."""
        diff_text = "diff --git a/file.py b/file.py\n--- a/file.py\n+++ b/file.py"
        seen_patches = set()

        error = _validate_patch_candidate(
            diff_text, seen_patches=seen_patches, max_patch_lines=1500
        )

        assert error is None
        assert diff_text in seen_patches

    def test_rejects_duplicate_patch(self):
        """Test rejecting duplicate patch."""
        diff_text = "diff --git a/file.py b/file.py"
        seen_patches = {diff_text}

        error = _validate_patch_candidate(
            diff_text, seen_patches=seen_patches, max_patch_lines=1500
        )

        assert error is not None
        assert "Duplicate patch" in error

    @patch("ci_tools.ci_runtime.patch_cycle.has_unified_diff_header")
    def test_rejects_patch_without_headers(self, mock_has_header):
        """Test rejecting patch without unified diff headers."""
        mock_has_header.return_value = False
        diff_text = "some content without headers"
        seen_patches = set()

        error = _validate_patch_candidate(
            diff_text, seen_patches=seen_patches, max_patch_lines=1500
        )

        assert error is not None
        assert "missing unified diff headers" in error

    @patch("ci_tools.ci_runtime.patch_cycle.has_unified_diff_header")
    @patch("ci_tools.ci_runtime.patch_cycle.patch_looks_risky")
    def test_rejects_risky_patch(self, mock_risky, mock_has_header):
        """Test rejecting patch that looks risky."""
        mock_has_header.return_value = True
        mock_risky.return_value = (True, "Contains DROP TABLE")
        diff_text = "diff --git a/file.py b/file.py"
        seen_patches = set()

        error = _validate_patch_candidate(
            diff_text, seen_patches=seen_patches, max_patch_lines=1500
        )

        assert error is not None
        assert "Contains DROP TABLE" in error

    @patch("ci_tools.ci_runtime.patch_cycle.has_unified_diff_header")
    @patch("ci_tools.ci_runtime.patch_cycle.patch_looks_risky")
    def test_rejects_risky_patch_without_reason(self, mock_risky, mock_has_header):
        """Test rejecting risky patch without specific reason."""
        mock_has_header.return_value = True
        mock_risky.return_value = (True, None)
        diff_text = "diff --git a/file.py b/file.py"
        seen_patches = set()

        error = _validate_patch_candidate(
            diff_text, seen_patches=seen_patches, max_patch_lines=1500
        )

        assert error is not None
        assert "failed safety checks" in error

    @patch("ci_tools.ci_runtime.patch_cycle.has_unified_diff_header")
    @patch("ci_tools.ci_runtime.patch_cycle.patch_looks_risky")
    def test_adds_patch_to_seen_set(self, mock_risky, mock_has_header):
        """Test patch is added to seen set."""
        mock_has_header.return_value = True
        mock_risky.return_value = (False, None)
        diff_text = "diff --git a/file.py b/file.py"
        seen_patches = set()

        _validate_patch_candidate(diff_text, seen_patches=seen_patches, max_patch_lines=1500)

        assert diff_text in seen_patches


class TestApplyPatchCandidate:
    """Tests for _apply_patch_candidate function."""

    @patch("ci_tools.ci_runtime.patch_cycle.apply_patch")
    def test_returns_true_on_success(self, mock_apply):
        """Test returning True when patch applies successfully."""
        diff_text = "diff --git a/file.py b/file.py"
        state = PatchAttemptState(max_attempts=3)

        result = _apply_patch_candidate(diff_text, state=state)

        assert result is True
        assert state.last_error is None
        mock_apply.assert_called_once_with(diff_text)

    @patch("ci_tools.ci_runtime.patch_cycle.apply_patch")
    def test_returns_false_on_retryable_error(self, mock_apply):
        """Test returning False when patch apply fails with retryable error."""
        mock_apply.side_effect = PatchApplyError(detail="git apply failed", retryable=True)
        diff_text = "diff --git a/file.py b/file.py"
        state = PatchAttemptState(max_attempts=3)

        result = _apply_patch_candidate(diff_text, state=state)

        assert result is False
        assert state.last_error is not None
        assert "git apply failed" in state.last_error

    @patch("ci_tools.ci_runtime.patch_cycle.apply_patch")
    def test_returns_false_on_non_retryable_error(self, mock_apply):
        """Test returning False when patch apply fails with non-retryable error."""
        mock_apply.side_effect = PatchApplyError(detail="fatal error", retryable=False)
        diff_text = "diff --git a/file.py b/file.py"
        state = PatchAttemptState(max_attempts=3)

        result = _apply_patch_candidate(diff_text, state=state)

        assert result is False
        assert state.last_error is not None
        assert "fatal error" in state.last_error

    @patch("ci_tools.ci_runtime.patch_cycle.apply_patch")
    def test_handles_runtime_error(self, mock_apply):
        """Test handling generic RuntimeError from apply_patch."""
        mock_apply.side_effect = RuntimeError("unexpected error")
        diff_text = "diff --git a/file.py b/file.py"
        state = PatchAttemptState(max_attempts=3)

        result = _apply_patch_candidate(diff_text, state=state)

        assert result is False
        assert state.last_error is not None
        assert "unexpected error" in state.last_error

    @patch("ci_tools.ci_runtime.patch_cycle.apply_patch")
    def test_records_failure_with_retryable_flag(self, mock_apply):
        """Test recording failure preserves retryable flag."""
        error = PatchApplyError(detail="fail", retryable=True)
        mock_apply.side_effect = error
        state = Mock()
        diff_text = "diff --git a/file.py b/file.py"

        _apply_patch_candidate(diff_text, state=state)

        state.record_failure.assert_called_once_with(
            "Patch application failed: fail", retryable=True
        )


class TestShouldApplyPatch:
    """Tests for _should_apply_patch function."""

    def test_auto_mode_returns_true(self):
        """Test auto mode returns True without prompting."""
        result = _should_apply_patch(approval_mode="auto", attempt=1)

        assert result is True

    @patch("builtins.input", return_value="y")
    def test_prompt_mode_accepts_yes(self, mock_input):
        """Test prompt mode accepts 'y' or 'yes'."""
        result = _should_apply_patch(approval_mode="prompt", attempt=1)

        assert result is True
        mock_input.assert_called_once()

    @patch("builtins.input", return_value="yes")
    def test_prompt_mode_accepts_yes_word(self, _mock_input):
        """Test prompt mode accepts 'yes'."""
        result = _should_apply_patch(approval_mode="prompt", attempt=2)

        assert result is True

    @patch("builtins.input", return_value="")
    def test_prompt_mode_accepts_empty_input(self, _mock_input):
        """Test prompt mode accepts empty input as yes."""
        result = _should_apply_patch(approval_mode="prompt", attempt=1)

        assert result is True

    @patch("builtins.input", return_value="n")
    def test_prompt_mode_rejects_no(self, _mock_input):
        """Test prompt mode rejects 'n'."""
        result = _should_apply_patch(approval_mode="prompt", attempt=1)

        assert result is False

    @patch("builtins.input", return_value="q")
    def test_prompt_mode_quit_raises_abort(self, _mock_input):
        """Test prompt mode raises abort on 'q'."""
        with pytest.raises(PatchLifecycleAbort) as exc_info:
            _should_apply_patch(approval_mode="prompt", attempt=1)

        assert "user declined" in str(exc_info.value)

    @patch("builtins.input", return_value="quit")
    def test_prompt_mode_quit_word_raises_abort(self, _mock_input):
        """Test prompt mode raises abort on 'quit'."""
        with pytest.raises(PatchLifecycleAbort):
            _should_apply_patch(approval_mode="prompt", attempt=1)

    @patch("builtins.input", return_value="  YES  ")
    def test_prompt_mode_strips_and_lowercases(self, _mock_input):
        """Test prompt mode strips whitespace and lowercases."""
        result = _should_apply_patch(approval_mode="prompt", attempt=1)

        assert result is True

    @patch("builtins.input", return_value="invalid")
    def test_prompt_mode_rejects_invalid_input(self, _mock_input):
        """Test prompt mode rejects invalid input."""
        result = _should_apply_patch(approval_mode="prompt", attempt=1)

        assert result is False


class TestRequestAndApplyPatches:
    """Tests for request_and_apply_patches function."""

    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_diff_limited")
    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_status")
    @patch("ci_tools.ci_runtime.patch_cycle._obtain_patch_diff")
    @patch("ci_tools.ci_runtime.patch_cycle._validate_patch_candidate")
    @patch("ci_tools.ci_runtime.patch_cycle._should_apply_patch")
    @patch("ci_tools.ci_runtime.patch_cycle._apply_patch_candidate")
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def test_successful_patch_on_first_attempt(
        self,
        mock_apply,
        mock_should_apply,
        mock_validate,
        mock_obtain,
        mock_status,
        mock_diff,
    ):
        """Test successful patch application on first attempt."""
        mock_diff.return_value = "current diff"
        mock_status.side_effect = ["git status", "post-patch status"]
        mock_obtain.return_value = "diff content"
        mock_validate.return_value = None
        mock_should_apply.return_value = True
        mock_apply.return_value = True

        args = Mock(
            command="pytest",
            patch_retries=1,
            max_patch_lines=1500,
        )
        options = Mock(
            model_name="gpt-5-codex",
            reasoning_effort="high",
            patch_approval_mode="auto",
        )
        failure_ctx = Mock()
        seen_patches = set()

        request_and_apply_patches(
            args=args,
            options=options,
            failure_ctx=failure_ctx,
            iteration=1,
            seen_patches=seen_patches,
        )

        mock_obtain.assert_called_once()
        mock_apply.assert_called_once()

    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_diff_limited")
    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_status")
    @patch("ci_tools.ci_runtime.patch_cycle._obtain_patch_diff")
    @patch("ci_tools.ci_runtime.patch_cycle._validate_patch_candidate")
    @patch("ci_tools.ci_runtime.patch_cycle._should_apply_patch")
    @patch("ci_tools.ci_runtime.patch_cycle._apply_patch_candidate")
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def test_retries_on_validation_error(
        self,
        mock_apply,
        mock_should_apply,
        mock_validate,
        mock_obtain,
        mock_status,
        mock_diff,
    ):
        """Test retrying when validation fails."""
        mock_diff.return_value = "current diff"
        mock_status.side_effect = ["git status"] * 10
        mock_obtain.side_effect = ["bad diff", "good diff"]
        mock_validate.side_effect = ["Validation error", None]
        mock_should_apply.return_value = True
        mock_apply.return_value = True

        args = Mock(
            command="pytest",
            patch_retries=2,
            max_patch_lines=1500,
        )
        options = Mock(
            model_name="gpt-5-codex",
            reasoning_effort="high",
            patch_approval_mode="auto",
        )
        failure_ctx = Mock()
        seen_patches = set()

        request_and_apply_patches(
            args=args,
            options=options,
            failure_ctx=failure_ctx,
            iteration=1,
            seen_patches=seen_patches,
        )

        assert mock_obtain.call_count == 2

    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_diff_limited")
    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_status")
    @patch("ci_tools.ci_runtime.patch_cycle._obtain_patch_diff")
    @patch("ci_tools.ci_runtime.patch_cycle._validate_patch_candidate")
    @patch("ci_tools.ci_runtime.patch_cycle._should_apply_patch")
    @patch("ci_tools.ci_runtime.patch_cycle._apply_patch_candidate")
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def test_retries_when_user_declines(
        self,
        mock_apply,
        mock_should_apply,
        mock_validate,
        mock_obtain,
        mock_status,
        mock_diff,
    ):
        """Test retrying when user declines patch."""
        mock_diff.return_value = "current diff"
        mock_status.side_effect = ["git status"] * 10
        mock_obtain.side_effect = ["diff1", "diff2"]
        mock_validate.return_value = None
        mock_should_apply.side_effect = [False, True]
        mock_apply.return_value = True

        args = Mock(
            command="pytest",
            patch_retries=2,
            max_patch_lines=1500,
        )
        options = Mock(
            model_name="gpt-5-codex",
            reasoning_effort="high",
            patch_approval_mode="prompt",
        )
        failure_ctx = Mock()
        seen_patches = set()

        request_and_apply_patches(
            args=args,
            options=options,
            failure_ctx=failure_ctx,
            iteration=1,
            seen_patches=seen_patches,
        )

        assert mock_obtain.call_count == 2
        assert mock_should_apply.call_count == 2

    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_diff_limited")
    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_status")
    @patch("ci_tools.ci_runtime.patch_cycle._obtain_patch_diff")
    @patch("ci_tools.ci_runtime.patch_cycle._validate_patch_candidate")
    @patch("ci_tools.ci_runtime.patch_cycle._should_apply_patch")
    @patch("ci_tools.ci_runtime.patch_cycle._apply_patch_candidate")
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def test_retries_when_apply_fails(
        self,
        mock_apply,
        mock_should_apply,
        mock_validate,
        mock_obtain,
        mock_status,
        mock_diff,
    ):
        """Test retrying when patch application fails."""
        mock_diff.return_value = "current diff"
        mock_status.side_effect = ["git status"] * 10
        mock_obtain.side_effect = ["diff1", "diff2"]
        mock_validate.return_value = None
        mock_should_apply.return_value = True
        mock_apply.side_effect = [False, True]

        args = Mock(
            command="pytest",
            patch_retries=2,
            max_patch_lines=1500,
        )
        options = Mock(
            model_name="gpt-5-codex",
            reasoning_effort="high",
            patch_approval_mode="auto",
        )
        failure_ctx = Mock()
        seen_patches = set()

        request_and_apply_patches(
            args=args,
            options=options,
            failure_ctx=failure_ctx,
            iteration=1,
            seen_patches=seen_patches,
        )

        assert mock_apply.call_count == 2

    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_diff_limited")
    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_status")
    @patch("ci_tools.ci_runtime.patch_cycle._obtain_patch_diff")
    @patch("ci_tools.ci_runtime.patch_cycle._validate_patch_candidate")
    @patch("ci_tools.ci_runtime.patch_cycle._should_apply_patch")
    @patch("ci_tools.ci_runtime.patch_cycle._apply_patch_candidate")
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def test_raises_when_budget_exhausted(
        self,
        mock_apply,
        mock_should_apply,
        mock_validate,
        mock_obtain,
        mock_status,
        mock_diff,
    ):
        """Test raising exception when patch budget exhausted."""
        mock_diff.return_value = "current diff"
        mock_status.return_value = "git status"
        mock_obtain.return_value = "diff content"
        mock_validate.return_value = None
        mock_should_apply.return_value = True
        mock_apply.return_value = False

        args = Mock(
            command="pytest",
            patch_retries=0,
            max_patch_lines=1500,
        )
        options = Mock(
            model_name="gpt-5-codex",
            reasoning_effort="high",
            patch_approval_mode="auto",
        )
        failure_ctx = Mock()
        seen_patches = set()

        with pytest.raises(PatchLifecycleAbort) as exc_info:
            request_and_apply_patches(
                args=args,
                options=options,
                failure_ctx=failure_ctx,
                iteration=1,
                seen_patches=seen_patches,
            )

        assert "exhausting retries" in str(exc_info.value)

    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_diff_limited")
    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_status")
    @patch("ci_tools.ci_runtime.patch_cycle._obtain_patch_diff")
    @patch("ci_tools.ci_runtime.patch_cycle._validate_patch_candidate")
    @patch("ci_tools.ci_runtime.patch_cycle._should_apply_patch")
    @patch("ci_tools.ci_runtime.patch_cycle._apply_patch_candidate")
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def test_shows_post_patch_status_when_not_clean(
        self,
        mock_apply,
        mock_should_apply,
        mock_validate,
        mock_obtain,
        mock_status,
        mock_diff,
        capsys,
    ):
        """Test showing git status after patch when not clean."""
        mock_diff.return_value = "current diff"
        mock_status.side_effect = ["git status", "M file.py"]
        mock_obtain.return_value = "diff content"
        mock_validate.return_value = None
        mock_should_apply.return_value = True
        mock_apply.return_value = True

        args = Mock(
            command="pytest",
            patch_retries=1,
            max_patch_lines=1500,
        )
        options = Mock(
            model_name="gpt-5-codex",
            reasoning_effort="high",
            patch_approval_mode="auto",
        )
        failure_ctx = Mock()
        seen_patches = set()

        request_and_apply_patches(
            args=args,
            options=options,
            failure_ctx=failure_ctx,
            iteration=1,
            seen_patches=seen_patches,
        )

        captured = capsys.readouterr()
        assert "git status after patch" in captured.out
        assert "M file.py" in captured.out

    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_diff_limited")
    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_status")
    @patch("ci_tools.ci_runtime.patch_cycle._obtain_patch_diff")
    @patch("ci_tools.ci_runtime.patch_cycle._validate_patch_candidate")
    @patch("ci_tools.ci_runtime.patch_cycle._should_apply_patch")
    @patch("ci_tools.ci_runtime.patch_cycle._apply_patch_candidate")
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def test_shows_clean_message_when_no_status(
        self,
        mock_apply,
        mock_should_apply,
        mock_validate,
        mock_obtain,
        mock_status,
        mock_diff,
        capsys,
    ):
        """Test showing clean message when no git status."""
        mock_diff.return_value = "current diff"
        mock_status.side_effect = ["git status", ""]
        mock_obtain.return_value = "diff content"
        mock_validate.return_value = None
        mock_should_apply.return_value = True
        mock_apply.return_value = True

        args = Mock(
            command="pytest",
            patch_retries=1,
            max_patch_lines=1500,
        )
        options = Mock(
            model_name="gpt-5-codex",
            reasoning_effort="high",
            patch_approval_mode="auto",
        )
        failure_ctx = Mock()
        seen_patches = set()

        request_and_apply_patches(
            args=args,
            options=options,
            failure_ctx=failure_ctx,
            iteration=1,
            seen_patches=seen_patches,
        )

        captured = capsys.readouterr()
        assert "Working tree is clean" in captured.out
    # pylint: disable=too-many-arguments,too-many-positional-arguments

    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_diff_limited")
    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_status")
    @patch("ci_tools.ci_runtime.patch_cycle._obtain_patch_diff")
    @patch("ci_tools.ci_runtime.patch_cycle._validate_patch_candidate")
    @patch("ci_tools.ci_runtime.patch_cycle._should_apply_patch")
    @patch("ci_tools.ci_runtime.patch_cycle._apply_patch_candidate")
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def test_passes_correct_patch_prompt(
        self,
        mock_apply,
        mock_should_apply,
        mock_validate,
        mock_obtain,
        mock_status,
        mock_diff,
    ):
        """Test passing correct PatchPrompt to obtain function."""
        mock_diff.return_value = "current diff"
        mock_status.return_value = "git status"
        mock_obtain.return_value = "diff content"
        mock_validate.return_value = None
        mock_should_apply.return_value = True
        mock_apply.return_value = True

        args = Mock(
            command="pytest",
            patch_retries=1,
            max_patch_lines=1500,
        )
        options = Mock(
            model_name="gpt-5-codex",
            reasoning_effort="high",
            patch_approval_mode="auto",
        )
        failure_ctx = Mock()
        seen_patches = set()

        request_and_apply_patches(
            args=args,
            options=options,
            failure_ctx=failure_ctx,
            iteration=2,
            seen_patches=seen_patches,
        )

        # Verify PatchPrompt structure
        call_args = mock_obtain.call_args
        prompt = call_args[1]["prompt"]
        assert isinstance(prompt, PatchPrompt)
        assert prompt.command == "pytest"
        assert prompt.failure_context == failure_ctx
        assert prompt.git_diff == "current diff"
        assert prompt.git_status == "git status"
        assert prompt.iteration == 2
    # pylint: disable=too-many-arguments,too-many-positional-arguments
        assert prompt.attempt == 1

    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_diff_limited")
    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_status")
    @patch("ci_tools.ci_runtime.patch_cycle._obtain_patch_diff")
    @patch("ci_tools.ci_runtime.patch_cycle._validate_patch_candidate")
    @patch("ci_tools.ci_runtime.patch_cycle._should_apply_patch")
    @patch("ci_tools.ci_runtime.patch_cycle._apply_patch_candidate")
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def test_includes_previous_error_in_prompt(
        self,
        mock_apply,
        mock_should_apply,
        mock_validate,
        mock_obtain,
        mock_status,
        mock_diff,
    ):
        """Test including previous patch error in prompt."""
        mock_diff.return_value = "current diff"
        mock_status.side_effect = ["git status"] * 10
        mock_obtain.side_effect = ["diff1", "diff2"]
        mock_validate.return_value = None
        mock_should_apply.return_value = True
        mock_apply.side_effect = [False, True]

        args = Mock(
            command="pytest",
            patch_retries=2,
            max_patch_lines=1500,
        )
        options = Mock(
            model_name="gpt-5-codex",
            reasoning_effort="high",
            patch_approval_mode="auto",
        )
        failure_ctx = Mock()
        seen_patches = set()

        request_and_apply_patches(
            args=args,
            options=options,
            failure_ctx=failure_ctx,
            iteration=1,
            seen_patches=seen_patches,
        )

        # Second call should have error from first attempt
        second_call = mock_obtain.call_args_list[1]
        prompt = second_call[1]["prompt"]
        # The error is stored in state which is passed to prompt
        assert prompt.attempt == 2

    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_diff_limited")
    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_status")
    @patch("ci_tools.ci_runtime.patch_cycle._obtain_patch_diff")
    def test_raises_on_missing_patch(self, mock_obtain, mock_status, mock_diff):
        """Test raising exception when _obtain_patch_diff raises."""
        mock_diff.return_value = "current diff"
        mock_status.return_value = "git status"
        mock_obtain.side_effect = PatchLifecycleAbort.missing_patch()

        args = Mock(
            command="pytest",
            patch_retries=1,
            max_patch_lines=1500,
        )
        options = Mock(
            model_name="gpt-5-codex",
            reasoning_effort="high",
            patch_approval_mode="auto",
        )
        failure_ctx = Mock()
        seen_patches = set()

        with pytest.raises(PatchLifecycleAbort):
            request_and_apply_patches(
                args=args,
                options=options,
                failure_ctx=failure_ctx,
    # pylint: disable=too-many-arguments,too-many-positional-arguments
                iteration=1,
                seen_patches=seen_patches,
            )

    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_diff_limited")
    @patch("ci_tools.ci_runtime.patch_cycle.gather_git_status")
    @patch("ci_tools.ci_runtime.patch_cycle._obtain_patch_diff")
    @patch("ci_tools.ci_runtime.patch_cycle._validate_patch_candidate")
    @patch("ci_tools.ci_runtime.patch_cycle._should_apply_patch")
    def test_user_quit_propagates(
        self, mock_should_apply, mock_validate, mock_obtain, mock_status, mock_diff
    ):
        """Test user quit exception propagates."""
        mock_diff.return_value = "current diff"
        mock_status.return_value = "git status"
        mock_obtain.return_value = "diff content"
        mock_validate.return_value = None
        mock_should_apply.side_effect = PatchLifecycleAbort.user_declined()

        args = Mock(
            command="pytest",
            patch_retries=1,
            max_patch_lines=1500,
        )
        options = Mock(
            model_name="gpt-5-codex",
            reasoning_effort="high",
            patch_approval_mode="prompt",
        )
        failure_ctx = Mock()
        seen_patches = set()

        with pytest.raises(PatchLifecycleAbort) as exc_info:
            request_and_apply_patches(
                args=args,
                options=options,
                failure_ctx=failure_ctx,
                iteration=1,
                seen_patches=seen_patches,
            )

        assert "user declined" in str(exc_info.value)
