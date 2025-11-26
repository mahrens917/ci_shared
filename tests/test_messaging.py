"""Unit tests for ci_tools.ci_runtime.messaging module."""

from __future__ import annotations

import subprocess
from unittest.mock import Mock, patch

import pytest

from ci_tools.ci_runtime.messaging import (
    request_commit_message,
    commit_and_push,
)
from ci_tools.ci_runtime.models import (
    CommitMessageError,
    GitCommandAbort,
)


class TestRequestCommitMessage:
    """Tests for request_commit_message function."""

    @patch("ci_tools.ci_runtime.messaging.invoke_codex")
    def test_simple_commit_message(self, mock_invoke):
        """Test generating a simple single-line commit message."""
        mock_invoke.return_value = "Fixed authentication bug"

        summary, body_lines = request_commit_message(
            model="gpt-5-codex",
            reasoning_effort="high",
            staged_diff="diff content",
            extra_context="",
            detailed=False,
        )

        assert summary == "Fixed authentication bug"
        assert not body_lines
        mock_invoke.assert_called_once()

    @patch("ci_tools.ci_runtime.messaging.invoke_codex")
    def test_detailed_commit_message(self, mock_invoke):
        """Test generating a detailed commit message with body."""
        response = """Updated user authentication system

- Added JWT token validation
- Implemented password hashing with bcrypt
- Updated login endpoint to return refresh tokens"""
        mock_invoke.return_value = response

        summary, body_lines = request_commit_message(
            model="gpt-5-codex",
            reasoning_effort="medium",
            staged_diff="diff content",
            extra_context="",
            detailed=True,
        )

        assert summary == "Updated user authentication system"
        # Leading blank lines are removed, so first body line is content
        assert len(body_lines) >= 3
        assert "JWT token" in "\n".join(body_lines)

    @patch("ci_tools.ci_runtime.messaging.invoke_codex")
    def test_commit_message_with_extra_context(self, mock_invoke):
        """Test commit message generation with extra context."""
        mock_invoke.return_value = "Added new feature"

        request_commit_message(
            model="gpt-5-codex",
            reasoning_effort="low",
            staged_diff="diff content",
            extra_context="This fixes issue #123",
            detailed=False,
        )

        call_args = mock_invoke.call_args
        prompt = call_args[0][0]
        assert "This fixes issue #123" in prompt

    @patch("ci_tools.ci_runtime.messaging.invoke_codex")
    def test_commit_message_includes_model_config(self, mock_invoke):
        """Test that prompt includes model configuration."""
        mock_invoke.return_value = "Fixed bug"

        request_commit_message(
            model="gpt-5-codex",
            reasoning_effort="high",
            staged_diff="diff",
            extra_context="",
            detailed=False,
        )

        call_args = mock_invoke.call_args
        prompt = call_args[0][0]
        assert "gpt-5-codex" in prompt
        assert "high" in prompt

    @patch("ci_tools.ci_runtime.messaging.invoke_codex")
    def test_commit_message_with_medium_reasoning_effort(self, mock_invoke):
        """Test commit message with medium reasoning effort."""
        mock_invoke.return_value = "Fixed bug"

        request_commit_message(
            model="gpt-5-codex",
            reasoning_effort="medium",
            staged_diff="diff",
            extra_context="",
            detailed=False,
        )

        call_args = mock_invoke.call_args
        prompt = call_args[0][0]
        assert "medium" in prompt

    @patch("ci_tools.ci_runtime.messaging.invoke_codex")
    def test_empty_response_raises_error(self, mock_invoke):
        """Test that empty response raises CommitMessageError."""
        mock_invoke.return_value = ""

        with pytest.raises(CommitMessageError):
            request_commit_message(
                model="gpt-5-codex",
                reasoning_effort="high",
                staged_diff="diff",
                extra_context="",
                detailed=False,
            )

    @patch("ci_tools.ci_runtime.messaging.invoke_codex")
    def test_whitespace_only_response_raises_error(self, mock_invoke):
        """Test that whitespace-only response raises error."""
        mock_invoke.return_value = "   \n  \n  "

        with pytest.raises(CommitMessageError):
            request_commit_message(
                model="gpt-5-codex",
                reasoning_effort="high",
                staged_diff="diff",
                extra_context="",
                detailed=False,
            )

    @patch("ci_tools.ci_runtime.messaging.invoke_codex")
    def test_strips_trailing_whitespace_from_lines(self, mock_invoke):
        """Test that trailing whitespace is stripped from lines."""
        response = "Fixed bug  \n  \n- Detail line   \n- Another detail  "
        mock_invoke.return_value = response

        summary, body_lines = request_commit_message(
            model="gpt-5-codex",
            reasoning_effort="high",
            staged_diff="diff",
            extra_context="",
            detailed=True,
        )

        assert summary == "Fixed bug"
        assert all(not line.endswith(" ") for line in body_lines if line)

    @patch("ci_tools.ci_runtime.messaging.invoke_codex")
    def test_removes_leading_blank_lines_from_body(self, mock_invoke):
        """Test that leading blank lines are removed from body."""
        response = "Summary\n\n\n\n- First bullet"
        mock_invoke.return_value = response

        summary, body_lines = request_commit_message(
            model="gpt-5-codex",
            reasoning_effort="high",
            staged_diff="diff",
            extra_context="",
            detailed=True,
        )

        assert summary == "Summary"
        # All leading blank lines are removed
        assert body_lines[0] == "- First bullet"
        assert "- First bullet" in body_lines

    @patch("ci_tools.ci_runtime.messaging.invoke_codex")
    def test_detailed_prompt_includes_instructions(self, mock_invoke):
        """Test that detailed prompt includes specific instructions."""
        mock_invoke.return_value = "Summary"

        request_commit_message(
            model="gpt-5-codex",
            reasoning_effort="high",
            staged_diff="diff",
            extra_context="",
            detailed=True,
        )

        call_args = mock_invoke.call_args
        prompt = call_args[0][0]
        assert "bullet" in prompt.lower()
        assert "72" in prompt  # character limit

    @patch("ci_tools.ci_runtime.messaging.invoke_codex")
    def test_simple_prompt_avoids_shell_commands(self, mock_invoke):
        """Test that prompt warns against running shell commands."""
        mock_invoke.return_value = "Summary"

        request_commit_message(
            model="gpt-5-codex",
            reasoning_effort="high",
            staged_diff="diff",
            extra_context="",
            detailed=False,
        )

        call_args = mock_invoke.call_args
        prompt = call_args[0][0]
        assert "diff --git" in prompt  # Warning about not using this command

    @patch("ci_tools.ci_runtime.messaging.invoke_codex")
    def test_passes_staged_diff_to_prompt(self, mock_invoke):
        """Test that staged diff is included in prompt."""
        mock_invoke.return_value = "Summary"
        diff = "--- a/file.py\n+++ b/file.py"

        request_commit_message(
            model="gpt-5-codex",
            reasoning_effort="high",
            staged_diff=diff,
            extra_context="",
            detailed=False,
        )

        call_args = mock_invoke.call_args
        prompt = call_args[0][0]
        assert diff in prompt

    @patch("ci_tools.ci_runtime.messaging.invoke_codex")
    def test_handles_empty_staged_diff(self, mock_invoke):
        """Test handling of empty staged diff."""
        mock_invoke.return_value = "Summary"

        request_commit_message(
            model="gpt-5-codex",
            reasoning_effort="high",
            staged_diff="",
            extra_context="",
            detailed=False,
        )

        call_args = mock_invoke.call_args
        prompt = call_args[0][0]
        assert "no staged diff" in prompt


class TestCommitAndPush:
    """Tests for commit_and_push function."""

    @patch("ci_tools.ci_runtime.messaging.run_command")
    def test_commit_without_push(self, mock_run):
        """Test creating commit without pushing."""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        commit_and_push("Summary line", [], push=False)

        # Should only call git commit once
        assert mock_run.call_count == 1
        call_args = mock_run.call_args_list[0][0][0]
        assert call_args[0] == "git"
        assert call_args[1] == "commit"
        assert "Summary line" in call_args

    @patch("ci_tools.ci_runtime.messaging.run_command")
    def test_commit_with_body(self, mock_run):
        """Test creating commit with body text."""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        body_lines = ["", "- Detail 1", "- Detail 2"]
        commit_and_push("Summary", body_lines, push=False)

        call_args = mock_run.call_args_list[0][0][0]
        assert "Summary" in call_args
        # Body should be included
        assert "-m" in call_args
        body_index = call_args.index("-m")
        # Find the second -m (for body)
        second_m_index = call_args.index("-m", body_index + 1)
        body_text = call_args[second_m_index + 1]
        assert "Detail 1" in body_text
        assert "Detail 2" in body_text

    @patch("ci_tools.ci_runtime.messaging.run_command")
    def test_commit_without_body(self, mock_run):
        """Test creating commit without body text."""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        commit_and_push("Summary", [], push=False)

        call_args = mock_run.call_args_list[0][0][0]
        # Should only have one -m flag
        m_count = call_args.count("-m")
        assert m_count == 1

    @patch("ci_tools.ci_runtime.messaging.run_command")
    def test_commit_with_empty_body_lines(self, mock_run):
        """Test that empty body lines are handled correctly."""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        body_lines = ["", "  ", ""]
        commit_and_push("Summary", body_lines, push=False)

        call_args = mock_run.call_args_list[0][0][0]
        # Should only have one -m since body is whitespace only
        m_count = call_args.count("-m")
        assert m_count == 1

    @patch("ci_tools.ci_runtime.messaging.run_command")
    @patch.dict("os.environ", {"GIT_REMOTE": "origin"})
    def test_commit_and_push_to_origin(self, mock_run):
        """Test committing and pushing to origin."""
        mock_run.return_value = Mock(returncode=0, stdout="main\n", stderr="")

        commit_and_push("Summary", [], push=True)

        assert mock_run.call_count == 3
        # First call: git commit
        assert mock_run.call_args_list[0][0][0][1] == "commit"
        # Second call: git rev-parse to get branch
        assert "rev-parse" in mock_run.call_args_list[1][0][0]
        # Third call: git push
        push_args = mock_run.call_args_list[2][0][0]
        assert push_args[0] == "git"
        assert push_args[1] == "push"
        assert push_args[2] == "origin"
        assert push_args[3] == "main"

    @patch("ci_tools.ci_runtime.messaging.run_command")
    @patch.dict("os.environ", {"GIT_REMOTE": "upstream"})
    def test_commit_and_push_to_custom_remote(self, mock_run):
        """Test pushing to custom remote from environment."""
        mock_run.return_value = Mock(returncode=0, stdout="feature\n", stderr="")

        commit_and_push("Summary", [], push=True)

        # Check push command uses custom remote
        push_args = mock_run.call_args_list[2][0][0]
        assert "upstream" in push_args
        assert "feature" in push_args

    @patch("ci_tools.ci_runtime.messaging.run_command")
    def test_commit_failure_raises_abort(self, mock_run):
        """Test that commit failure raises GitCommandAbort."""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, ["git", "commit"], output="", stderr="commit failed"
        )

        with pytest.raises(GitCommandAbort) as exc_info:
            commit_and_push("Summary", [], push=False)

        assert "commit" in str(exc_info.value).lower()

    @patch("ci_tools.ci_runtime.messaging.run_command")
    @patch.dict("os.environ", {"GIT_REMOTE": "origin"})
    def test_push_failure_raises_abort(self, mock_run):
        """Test that push failure raises GitCommandAbort."""
        # First call (commit) succeeds, second (rev-parse) succeeds, third (push) fails
        mock_run.side_effect = [
            Mock(returncode=0, stdout="", stderr=""),  # commit success
            Mock(returncode=0, stdout="main\n", stderr=""),  # branch name
            subprocess.CalledProcessError(1, ["git", "push"], output="", stderr="push failed"),
        ]

        with pytest.raises(GitCommandAbort) as exc_info:
            commit_and_push("Summary", [], push=True)

        assert "push" in str(exc_info.value).lower()

    @patch("ci_tools.ci_runtime.messaging.run_command")
    @patch.dict("os.environ", {"GIT_REMOTE": "origin"})
    def test_prints_info_messages(self, mock_run, capsys):
        """Test that informational messages are printed."""
        mock_run.return_value = Mock(returncode=0, stdout="main\n", stderr="")

        commit_and_push("Summary", [], push=True)

        captured = capsys.readouterr()
        assert "Creating commit" in captured.out
        assert "Pushing" in captured.out

    @patch("ci_tools.ci_runtime.messaging.run_command")
    def test_commit_uses_check_true(self, mock_run):
        """Test that commit command uses check=True."""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        commit_and_push("Summary", [], push=False)

        # Check that check=True was passed
        kwargs = mock_run.call_args_list[0][1]
        assert kwargs.get("check") is True

    @patch("ci_tools.ci_runtime.messaging.run_command")
    def test_commit_uses_live_output(self, mock_run):
        """Test that commit command uses live=True for output."""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        commit_and_push("Summary", [], push=False)

        # Check that live=True was passed
        kwargs = mock_run.call_args_list[0][1]
        assert kwargs.get("live") is True

    @patch("ci_tools.ci_runtime.messaging.run_command")
    @patch.dict("os.environ", {"GIT_REMOTE": "origin"})
    def test_push_uses_live_output(self, mock_run):
        """Test that push command uses live=True for output."""
        mock_run.return_value = Mock(returncode=0, stdout="main\n", stderr="")

        commit_and_push("Summary", [], push=True)

        # Check that push command uses live=True
        kwargs = mock_run.call_args_list[2][1]
        assert kwargs.get("live") is True

    @patch("ci_tools.ci_runtime.messaging.run_command")
    @patch.dict("os.environ", {"GIT_REMOTE": "origin"})
    def test_branch_detection_strips_whitespace(self, mock_run):
        """Test that branch name is stripped of whitespace."""
        mock_run.side_effect = [
            Mock(returncode=0, stdout="", stderr=""),  # commit
            Mock(returncode=0, stdout="  feature-branch  \n", stderr=""),  # branch
            Mock(returncode=0, stdout="", stderr=""),  # push
        ]

        commit_and_push("Summary", [], push=True)

        # Check that branch name was stripped
        push_args = mock_run.call_args_list[2][0][0]
        assert "feature-branch" in push_args
        # Verify no whitespace in branch name
        branch_arg = push_args[3]
        assert branch_arg == "feature-branch"

    @patch("ci_tools.ci_runtime.messaging.run_command")
    def test_body_with_multiple_paragraphs(self, mock_run):
        """Test commit body with multiple paragraphs."""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        body_lines = [
            "",
            "First paragraph with details.",
            "",
            "Second paragraph with more info.",
            "- Bullet point",
        ]
        commit_and_push("Summary", body_lines, push=False)

        call_args = mock_run.call_args_list[0][0][0]
        # Find body text
        m_indices = [i for i, x in enumerate(call_args) if x == "-m"]
        assert len(m_indices) == 2
        body_text = call_args[m_indices[1] + 1]
        assert "First paragraph" in body_text
        assert "Second paragraph" in body_text
        assert "Bullet point" in body_text


# pylint: disable=too-few-public-methods
class TestCommitMessageErrorFactory:
    """Tests for CommitMessageError factory methods."""

    def test_empty_response_factory(self):
        """Test empty_response factory method."""
        error = CommitMessageError.empty_response()
        assert isinstance(error, CommitMessageError)
        assert "empty" in str(error).lower()


class TestGitCommandAbortFactories:
    """Tests for GitCommandAbort factory methods."""

    def test_commit_failed_factory(self):
        """Test commit_failed factory method."""
        exc = subprocess.CalledProcessError(
            1, ["git", "commit"], output="", stderr="nothing to commit"
        )
        error = GitCommandAbort.commit_failed(exc)
        assert isinstance(error, GitCommandAbort)
        assert "commit" in str(error).lower()
        assert "status 1" in str(error)

    def test_push_failed_factory(self):
        """Test push_failed factory method."""
        exc = subprocess.CalledProcessError(
            128, ["git", "push"], output="", stderr="failed to push"
        )
        error = GitCommandAbort.push_failed(exc)
        assert isinstance(error, GitCommandAbort)
        assert "push" in str(error).lower()
        assert "status 128" in str(error)

    def test_commit_failed_includes_stderr(self):
        """Test that stderr is included in commit error."""
        exc = subprocess.CalledProcessError(
            1, ["git", "commit"], output="", stderr="pre-commit hook failed"
        )
        error = GitCommandAbort.commit_failed(exc)
        assert "pre-commit hook failed" in str(error)

    def test_push_failed_includes_stderr(self):
        """Test that stderr is included in push error."""
        exc = subprocess.CalledProcessError(
            1, ["git", "push"], output="", stderr="authentication failed"
        )
        error = GitCommandAbort.push_failed(exc)
        assert "authentication failed" in str(error)
