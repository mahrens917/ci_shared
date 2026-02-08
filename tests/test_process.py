"""Unit tests for ci_tools.ci_runtime.process module."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, Mock, patch

import pytest

from ci_tools.ci_runtime.process import (
    _run_command_buffered,
    _run_command_streaming,
    stream_pipe,
    run_command,
    tail_text,
    gather_git_diff,
    gather_git_status,
    gather_file_diff,
    log_codex_interaction,
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
