"""Unit tests for ci_tools.ci_runtime.process module."""

from __future__ import annotations

import subprocess
import threading
from unittest.mock import MagicMock, Mock, patch

import pytest

from ci_tools.ci_runtime.process import (
    _create_pipe_thread,
    _handle_streaming_timeout,
    _run_command_buffered,
    _run_command_streaming,
    gather_file_diff,
    gather_git_diff,
    gather_git_diff_limited,
    gather_git_status,
    get_commit_message,
    get_current_branch,
    log_codex_interaction,
    run_command,
    stream_pipe,
    tail_text,
)
from ci_tools.ci_runtime.models import CommandResult


class TestRunCommandBuffered:
    """Tests for _run_command_buffered function."""

    def test_success_returns_command_result(self):
        """Test successful command execution returns CommandResult."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout="output text",
                stderr="",
            )
            result = _run_command_buffered(
                ["echo", "test"],
                check=False,
                env={},
                cwd=None,
                timeout=None,
            )
            assert isinstance(result, CommandResult)
            assert result.returncode == 0
            assert result.stdout == "output text"
            assert result.stderr == ""

    def test_failure_with_check_false_returns_result(self):
        """Test failed command with check=False returns result."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=1,
                stdout="",
                stderr="error message",
            )
            result = _run_command_buffered(
                ["false"],
                check=False,
                env={},
                cwd=None,
                timeout=None,
            )
            assert result.returncode == 1
            assert result.stderr == "error message"

    def test_failure_with_check_true_raises_exception(self):
        """Test failed command with check=True raises CalledProcessError."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=127,
                stdout="",
                stderr="command not found",
                args=["nonexistent"],
            )
            with pytest.raises(subprocess.CalledProcessError) as exc_info:
                _run_command_buffered(
                    ["nonexistent"],
                    check=True,
                    env={},
                    cwd=None,
                    timeout=None,
                )
            assert exc_info.value.returncode == 127

    def test_captures_both_stdout_and_stderr(self):
        """Test that both stdout and stderr are captured."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout="standard output",
                stderr="standard error",
            )
            result = _run_command_buffered(
                ["cmd"],
                check=False,
                env={"KEY": "value"},
                cwd=None,
                timeout=None,
            )
            assert result.stdout == "standard output"
            assert result.stderr == "standard error"


class TestStreamPipe:
    """Tests for stream_pipe helper function."""

    def test_collects_and_forwards_lines(self):
        """Test that lines are collected and forwarded to target."""
        mock_pipe = MagicMock()
        mock_pipe.readline.side_effect = ["line1\n", "line2\n", ""]
        collector: list[str] = []
        target = MagicMock()

        stream_pipe(mock_pipe, collector, target)

        assert collector == ["line1\n", "line2\n"]
        assert target.write.call_count == 2
        target.write.assert_any_call("line1\n")
        target.write.assert_any_call("line2\n")
        target.flush.assert_called()

    def test_closes_pipe_after_processing(self):
        """Test that pipe is closed after processing."""
        mock_pipe = MagicMock()
        mock_pipe.readline.side_effect = [""]
        collector: list[str] = []
        target = MagicMock()

        stream_pipe(mock_pipe, collector, target)

        mock_pipe.close.assert_called_once()


class TestRunCommandStreaming:
    """Tests for _run_command_streaming function."""

    def test_success_streams_and_captures_output(self):
        """Test successful streaming command captures output."""
        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_process.stdout.readline.side_effect = ["out1\n", ""]
            mock_process.stderr.readline.side_effect = ["err1\n", ""]
            mock_process.wait.return_value = 0
            mock_popen.return_value.__enter__.return_value = mock_process

            result = _run_command_streaming(
                ["echo", "test"],
                check=False,
                env={},
                cwd=None,
                timeout=None,
            )

            assert result.returncode == 0
            assert "out1\n" in result.stdout
            assert "err1\n" in result.stderr

    def test_failure_with_check_true_raises_exception(self):
        """Test streaming command failure with check=True raises."""
        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_process.stdout.readline.side_effect = [""]
            mock_process.stderr.readline.side_effect = ["error\n", ""]
            mock_process.wait.return_value = 1
            mock_process.args = ["false"]
            mock_popen.return_value.__enter__.return_value = mock_process

            with pytest.raises(subprocess.CalledProcessError) as exc_info:
                _run_command_streaming(
                    ["false"],
                    check=True,
                    env={},
                    cwd=None,
                    timeout=None,
                )
            assert exc_info.value.returncode == 1

    def test_handles_none_pipes(self):
        """Test handles when stdout or stderr are None."""
        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.stdout = None
            mock_process.stderr = None
            mock_process.wait.return_value = 0
            mock_popen.return_value.__enter__.return_value = mock_process

            result = _run_command_streaming(
                ["cmd"],
                check=False,
                env={},
                cwd=None,
                timeout=None,
            )

            assert result.returncode == 0
            assert result.stdout == ""
            assert result.stderr == ""


class TestRunCommand:
    """Tests for run_command public API."""

    def test_buffered_mode_by_default(self):
        """Test that buffered mode is used by default."""
        with patch("ci_tools.ci_runtime.process._run_command_buffered") as mock_buffered:
            mock_buffered.return_value = CommandResult(0, "out", "err")
            result = run_command(["echo", "test"])
            assert result.returncode == 0
            mock_buffered.assert_called_once()

    def test_streaming_mode_when_live_true(self):
        """Test that streaming mode is used when live=True."""
        with patch("ci_tools.ci_runtime.process._run_command_streaming") as mock_streaming:
            mock_streaming.return_value = CommandResult(0, "out", "err")
            result = run_command(["echo", "test"], live=True)
            assert result.returncode == 0
            mock_streaming.assert_called_once()

    def test_merges_environment_variables(self):
        """Test that custom env vars are merged with os.environ."""
        with patch("ci_tools.ci_runtime.process._run_command_buffered") as mock_buffered:
            with patch("os.environ", {"EXISTING": "value"}):
                mock_buffered.return_value = CommandResult(0, "", "")
                run_command(["cmd"], env={"NEW": "var"})
                call_args = mock_buffered.call_args
                env = call_args[1]["env"]
                assert "EXISTING" in env
                assert env["NEW"] == "var"

    def test_converts_iterable_to_list(self):
        """Test that iterable args are converted to list."""
        with patch("ci_tools.ci_runtime.process._run_command_buffered") as mock_buffered:
            mock_buffered.return_value = CommandResult(0, "", "")
            run_command(("echo", "test"))
            call_args = mock_buffered.call_args
            assert isinstance(call_args[0][0], list)

    def test_check_parameter_passed_through(self):
        """Test that check parameter is passed to runner."""
        with patch("ci_tools.ci_runtime.process._run_command_buffered") as mock_buffered:
            mock_buffered.return_value = CommandResult(0, "", "")
            run_command(["cmd"], check=True)
            assert mock_buffered.call_args[1]["check"] is True


class TestTailText:
    """Tests for tail_text helper function."""

    def test_returns_last_n_lines(self):
        """Test returns last N lines from text."""
        text = "line1\nline2\nline3\nline4\nline5"
        result = tail_text(text, 3)
        assert result == "line3\nline4\nline5"

    def test_returns_all_lines_when_n_exceeds_count(self):
        """Test returns all lines when N exceeds line count."""
        text = "line1\nline2"
        result = tail_text(text, 10)
        assert result == "line1\nline2"

    def test_handles_empty_string(self):
        """Test handles empty string."""
        result = tail_text("", 5)
        assert result == ""

    def test_handles_single_line(self):
        """Test handles single line correctly."""
        result = tail_text("oneline", 1)
        assert result == "oneline"

    def test_returns_all_when_zero_lines(self):
        """Test returns all lines when requesting zero lines (edge case)."""
        result = tail_text("line1\nline2", 0)
        # Python list slicing [-0:] returns the entire list
        assert result == "line1\nline2"


class TestGatherGitDiff:
    """Tests for gather_git_diff function."""

    def test_gathers_unstaged_diff_by_default(self):
        """Test gathers unstaged changes by default."""
        with patch("ci_tools.ci_runtime.process.run_command") as mock_run:
            mock_run.return_value = CommandResult(0, "diff content", "")
            result = gather_git_diff()
            assert result == "diff content"
            mock_run.assert_called_once_with(["git", "diff"])

    def test_gathers_staged_diff_when_specified(self):
        """Test gathers staged changes when staged=True."""
        with patch("ci_tools.ci_runtime.process.run_command") as mock_run:
            mock_run.return_value = CommandResult(0, "staged diff", "")
            result = gather_git_diff(staged=True)
            assert result == "staged diff"
            mock_run.assert_called_once_with(["git", "diff", "--cached"])

    def test_returns_empty_when_no_diff(self):
        """Test returns empty string when no changes."""
        with patch("ci_tools.ci_runtime.process.run_command") as mock_run:
            mock_run.return_value = CommandResult(0, "", "")
            result = gather_git_diff()
            assert result == ""


class TestGatherGitStatus:
    """Tests for gather_git_status function."""

    def test_returns_short_status(self):
        """Test returns short git status."""
        with patch("ci_tools.ci_runtime.process.run_command") as mock_run:
            mock_run.return_value = CommandResult(0, " M file.py\n A new.py\n", "")
            result = gather_git_status()
            assert result == "M file.py\n A new.py"
            mock_run.assert_called_once_with(["git", "status", "--short"])

    def test_strips_whitespace(self):
        """Test strips leading and trailing whitespace."""
        with patch("ci_tools.ci_runtime.process.run_command") as mock_run:
            mock_run.return_value = CommandResult(0, "  status  \n", "")
            result = gather_git_status()
            assert result == "status"

    def test_returns_empty_on_clean_repo(self):
        """Test returns empty string on clean repository."""
        with patch("ci_tools.ci_runtime.process.run_command") as mock_run:
            mock_run.return_value = CommandResult(0, "", "")
            result = gather_git_status()
            assert result == ""


class TestGatherFileDiff:
    """Tests for gather_file_diff function."""

    def test_gathers_diff_for_single_file(self):
        """Test gathers diff for specified file."""
        with patch("ci_tools.ci_runtime.process.run_command") as mock_run:
            mock_run.return_value = CommandResult(0, "diff for file.py", "")
            result = gather_file_diff("src/module.py")
            assert result == "diff for file.py"
            mock_run.assert_called_once_with(["git", "diff", "src/module.py"])

    def test_returns_empty_for_unchanged_file(self):
        """Test returns empty string for unchanged file."""
        with patch("ci_tools.ci_runtime.process.run_command") as mock_run:
            mock_run.return_value = CommandResult(0, "", "")
            result = gather_file_diff("unchanged.py")
            assert result == ""

    def test_accepts_relative_paths(self):
        """Test accepts relative file paths."""
        with patch("ci_tools.ci_runtime.process.run_command") as mock_run:
            mock_run.return_value = CommandResult(0, "diff", "")
            gather_file_diff("relative/path/file.py")
            mock_run.assert_called_once()


class TestLogCodexInteraction:
    """Tests for log_codex_interaction function."""

    @staticmethod
    def _fake_file(tmp_path):
        """Create a fake module path so logs go under tmp_path."""
        fake_dir = tmp_path / "ci_tools" / "ci_runtime"
        fake_dir.mkdir(parents=True)
        fake_file = fake_dir / "process.py"
        fake_file.touch()
        return str(fake_file)

    def test_creates_log_directory(self, tmp_path, monkeypatch):
        """Test creates logs directory if it doesn't exist."""
        monkeypatch.setattr("ci_tools.ci_runtime.process.__file__", self._fake_file(tmp_path))
        log_codex_interaction("test", "prompt text", "response text")
        assert (tmp_path / "logs").is_dir()

    def test_appends_interaction_to_log(self, tmp_path, monkeypatch):
        """Test appends interaction to log file."""
        monkeypatch.setattr("ci_tools.ci_runtime.process.__file__", self._fake_file(tmp_path))
        log_path = tmp_path / "logs" / "codex_ci.log"

        log_codex_interaction("patch request", "fix this", "here's the patch")

        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8")
        assert "--- patch request ---" in content
        assert "Prompt:" in content
        assert "fix this" in content
        assert "Response:" in content
        assert "here's the patch" in content

    def test_strips_whitespace_from_content(self, tmp_path, monkeypatch):
        """Test strips leading/trailing whitespace from logged content."""
        monkeypatch.setattr("ci_tools.ci_runtime.process.__file__", self._fake_file(tmp_path))
        log_path = tmp_path / "logs" / "codex_ci.log"

        log_codex_interaction("test", "  prompt  \n", "  response  \n")

        content = log_path.read_text(encoding="utf-8")
        assert "prompt  \n" not in content
        assert "prompt\n" in content

    def test_multiple_interactions_appended(self, tmp_path, monkeypatch):
        """Test multiple interactions are appended to same file."""
        monkeypatch.setattr("ci_tools.ci_runtime.process.__file__", self._fake_file(tmp_path))
        log_path = tmp_path / "logs" / "codex_ci.log"

        log_codex_interaction("first", "prompt1", "response1")
        log_codex_interaction("second", "prompt2", "response2")

        content = log_path.read_text(encoding="utf-8")
        assert "--- first ---" in content
        assert "--- second ---" in content
        assert content.count("Prompt:") == 2
        assert content.count("Response:") == 2

    def test_handles_empty_strings(self, tmp_path, monkeypatch):
        """Test handles empty prompt or response strings."""
        monkeypatch.setattr("ci_tools.ci_runtime.process.__file__", self._fake_file(tmp_path))
        log_path = tmp_path / "logs" / "codex_ci.log"

        log_codex_interaction("empty", "", "")

        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8")
        assert "--- empty ---" in content


class TestBufferedTimeout:
    """Tests for _run_command_buffered timeout handling."""

    def test_timeout_with_check_false_returns_result(self):
        """Test timeout without check returns CommandResult."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(["cmd"], 5)
            result = _run_command_buffered(
                ["cmd"],
                check=False,
                env={},
                cwd=None,
                timeout=5.0,
            )
            assert result.returncode == 1
            assert result.stdout == ""
            assert "timed out" in result.stderr

    def test_timeout_with_check_true_raises(self):
        """Test timeout with check raises CalledProcessError."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(["cmd"], 5)
            with pytest.raises(subprocess.CalledProcessError):
                _run_command_buffered(
                    ["cmd"],
                    check=True,
                    env={},
                    cwd=None,
                    timeout=5.0,
                )

    def test_timeout_decodes_bytes_stdout(self):
        """Test timeout with bytes stdout decodes correctly."""
        with patch("subprocess.run") as mock_run:
            exc = subprocess.TimeoutExpired(["cmd"], 5)
            exc.stdout = b"partial output"
            mock_run.side_effect = exc
            result = _run_command_buffered(
                ["cmd"],
                check=False,
                env={},
                cwd=None,
                timeout=5.0,
            )
            assert result.stdout == "partial output"

    def test_timeout_handles_str_stdout(self):
        """Test timeout with str stdout passes through."""
        with patch("subprocess.run") as mock_run:
            exc = subprocess.TimeoutExpired(["cmd"], 5)
            exc.stdout = "string output"
            mock_run.side_effect = exc
            result = _run_command_buffered(
                ["cmd"],
                check=False,
                env={},
                cwd=None,
                timeout=5.0,
            )
            assert result.stdout == "string output"


class TestCreatePipeThread:
    """Tests for _create_pipe_thread helper."""

    def test_creates_daemon_thread(self):
        """Test returns a daemon thread targeting stream_pipe."""
        mock_pipe = MagicMock()
        collector: list[str] = []
        thread = _create_pipe_thread(mock_pipe, collector, None)
        assert isinstance(thread, threading.Thread)
        assert thread.daemon is True


class TestHandleStreamingTimeout:
    """Tests for _handle_streaming_timeout helper."""

    def test_returns_result_without_check(self):
        """Test returns CommandResult when check is False."""
        mock_process = MagicMock()
        result = _handle_streaming_timeout(
            mock_process, ["cmd"], ["partial\n"], 5.0, check=False,
        )
        assert result.returncode == 1
        assert result.stdout == "partial\n"
        assert "timed out" in result.stderr
        mock_process.kill.assert_called_once()

    def test_raises_with_check(self):
        """Test raises CalledProcessError when check is True."""
        mock_process = MagicMock()
        with pytest.raises(subprocess.CalledProcessError):
            _handle_streaming_timeout(
                mock_process, ["cmd"], [], 5.0, check=True,
            )


class TestStreamPipeNoTarget:
    """Tests for stream_pipe when target is None."""

    def test_collects_lines_without_forwarding(self):
        """Test lines are collected but not forwarded when target is None."""
        mock_pipe = MagicMock()
        mock_pipe.readline.side_effect = ["line1\n", ""]
        collector: list[str] = []
        stream_pipe(mock_pipe, collector, None)
        assert collector == ["line1\n"]
        mock_pipe.close.assert_called_once()


class TestGetCurrentBranch:
    """Tests for get_current_branch function."""

    def test_returns_branch_name(self):
        """Test returns stripped branch name."""
        with patch("ci_tools.ci_runtime.process.run_command") as mock_run:
            mock_run.return_value = CommandResult(0, "main\n", "")
            result = get_current_branch()
            assert result == "main"
            mock_run.assert_called_once_with(
                ["git", "branch", "--show-current"], check=True, cwd=None,
            )


class TestGetCommitMessage:
    """Tests for get_commit_message function."""

    def test_returns_commit_subject(self):
        """Test returns stripped commit message."""
        with patch("ci_tools.ci_runtime.process.run_command") as mock_run:
            mock_run.return_value = CommandResult(0, "Add feature\n", "")
            result = get_commit_message()
            assert result == "Add feature"
            mock_run.assert_called_once_with(
                ["git", "log", "-1", "--pretty=format:%s", "HEAD"],
                check=True,
                cwd=None,
            )

    def test_custom_ref(self):
        """Test with custom git reference."""
        with patch("ci_tools.ci_runtime.process.run_command") as mock_run:
            mock_run.return_value = CommandResult(0, "Fix bug", "")
            result = get_commit_message(ref="abc123")
            assert result == "Fix bug"
            call_args = mock_run.call_args[0][0]
            assert "abc123" in call_args


class TestGatherGitDiffLimited:
    """Tests for gather_git_diff_limited function."""

    def test_returns_full_diff_within_limits(self):
        """Test returns full diff when within size limits."""
        with patch("ci_tools.ci_runtime.process.gather_git_diff") as mock_diff:
            mock_diff.return_value = "small diff"
            result = gather_git_diff_limited()
            assert result == "small diff"

    def test_returns_summary_when_too_large(self):
        """Test returns stat summary when diff exceeds limits."""
        large_diff = "x" * 60000
        with patch("ci_tools.ci_runtime.process.gather_git_diff") as mock_diff:
            with patch("ci_tools.ci_runtime.process.run_command") as mock_run:
                mock_diff.return_value = large_diff
                mock_run.return_value = CommandResult(0, "3 files changed", "")
                result = gather_git_diff_limited(max_chars=100)
                assert "Diff too large" in result
                assert "3 files changed" in result

    def test_returns_summary_when_too_many_lines(self):
        """Test returns summary when line count exceeds limit."""
        many_lines = "\n".join(["line"] * 2000)
        with patch("ci_tools.ci_runtime.process.gather_git_diff") as mock_diff:
            with patch("ci_tools.ci_runtime.process.run_command") as mock_run:
                mock_diff.return_value = many_lines
                mock_run.return_value = CommandResult(0, "stat output", "")
                result = gather_git_diff_limited(max_lines=5)
                assert "Diff too large" in result

    def test_staged_flag_forwarded(self):
        """Test staged flag is forwarded to gather_git_diff and stat command."""
        large_diff = "x" * 60000
        with patch("ci_tools.ci_runtime.process.gather_git_diff") as mock_diff:
            with patch("ci_tools.ci_runtime.process.run_command") as mock_run:
                mock_diff.return_value = large_diff
                mock_run.return_value = CommandResult(0, "stat", "")
                gather_git_diff_limited(staged=True, max_chars=100)
                mock_diff.assert_called_once_with(staged=True)
                stat_args = mock_run.call_args[0][0]
                assert "--cached" in stat_args


class TestCommandResult:
    """Tests for CommandResult dataclass properties."""

    def test_ok_property_true_on_success(self):
        """Test ok property returns True when returncode is 0."""
        result = CommandResult(0, "output", "")
        assert result.ok is True

    def test_ok_property_false_on_failure(self):
        """Test ok property returns False when returncode is non-zero."""
        result = CommandResult(1, "", "error")
        assert result.ok is False

    def test_combined_output_merges_stdout_stderr(self):
        """Test combined_output concatenates stdout and stderr."""
        result = CommandResult(0, "stdout text", "stderr text")
        assert result.combined_output == "stdout textstderr text"

    def test_combined_output_with_empty_streams(self):
        """Test combined_output with empty stdout or stderr."""
        result = CommandResult(0, "", "")
        assert result.combined_output == ""
