"""Unit tests for ci_tools.ci_runtime.codex module."""

from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch

import pytest

from ci_tools.ci_runtime.codex import (
    build_codex_command,
    invoke_codex,
    truncate_error,
    extract_unified_diff,
    has_unified_diff_header,
    request_codex_patch,
    truncate_diff_summary,
    risky_pattern_in_diff,
    _feed_prompt,
    _stream_output,
)
from ci_tools.ci_runtime.models import (
    CodexCliError,
    PatchPrompt,
    FailureContext,
)


class TestBuildCodexCommand:
    """Tests for build_codex_command function."""

    @patch.dict("os.environ", {"CI_CLI_TYPE": "codex"})
    def test_basic_command_without_reasoning_effort(self):
        """Test command building without reasoning effort."""
        result = build_codex_command("gpt-5-codex", None)
        assert result == ["codex", "exec", "--model", "gpt-5-codex", "-"]

    @patch.dict("os.environ", {"CI_CLI_TYPE": "codex"})
    def test_command_with_reasoning_effort(self):
        """Test command building with reasoning effort."""
        result = build_codex_command("gpt-5-codex", "high")
        assert result == [
            "codex",
            "exec",
            "--model",
            "gpt-5-codex",
            "-c",
            "model_reasoning_effort=high",
            "-",
        ]

    @patch.dict("os.environ", {"CI_CLI_TYPE": "codex"})
    def test_command_with_low_reasoning_effort(self):
        """Test command building with low reasoning effort."""
        result = build_codex_command("gpt-5-codex", "low")
        assert result == [
            "codex",
            "exec",
            "--model",
            "gpt-5-codex",
            "-c",
            "model_reasoning_effort=low",
            "-",
        ]

    @patch.dict("os.environ", {"CI_CLI_TYPE": "codex"})
    def test_command_with_medium_reasoning_effort(self):
        """Test command building with medium reasoning effort."""
        result = build_codex_command("gpt-5-codex", "medium")
        assert result == [
            "codex",
            "exec",
            "--model",
            "gpt-5-codex",
            "-c",
            "model_reasoning_effort=medium",
            "-",
        ]


class TestFeedPrompt:
    """Tests for _feed_prompt helper function."""

    def test_writes_prompt_and_closes_stdin(self):
        """Test that prompt is written to stdin and stream is closed."""
        mock_process = Mock()
        mock_process.stdin = Mock()

        _feed_prompt(mock_process, "test prompt")

        mock_process.stdin.write.assert_called_once_with("test prompt")
        mock_process.stdin.close.assert_called_once()

    def test_handles_broken_pipe_error(self):
        """Test that BrokenPipeError is re-raised after closing stdin."""
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_process.stdin.write.side_effect = BrokenPipeError()

        # Should re-raise BrokenPipeError after closing stdin
        with pytest.raises(BrokenPipeError):
            _feed_prompt(mock_process, "test prompt")

        # stdin should still be closed even when error occurs
        mock_process.stdin.close.assert_called()

    def test_handles_none_stdin(self):
        """Test handling when stdin is None."""
        mock_process = Mock()
        mock_process.stdin = None

        # Should not raise
        _feed_prompt(mock_process, "test prompt")


class TestStreamOutput:
    """Tests for _stream_output helper function."""

    def test_streams_stdout_and_stderr(self):
        """Test that stdout and stderr are streamed correctly."""
        mock_process = Mock()
        mock_stdout = Mock()
        mock_stderr = Mock()
        mock_stdout.readline.side_effect = ["line1\n", "line2\n", ""]
        mock_stderr.readline.side_effect = ["error1\n", ""]
        mock_stdout.close = Mock()
        mock_stderr.close = Mock()
        mock_process.stdout = mock_stdout
        mock_process.stderr = mock_stderr

        stdout_lines, stderr_lines = _stream_output(mock_process)

        assert stdout_lines == ["line1\n", "line2\n"]
        assert stderr_lines == ["error1\n"]

    def test_handles_stdout_only(self):
        """Test streaming when only stdout exists."""
        mock_process = Mock()
        mock_stdout = Mock()
        mock_stdout.readline.side_effect = ["output\n", ""]
        mock_stdout.close = Mock()
        mock_process.stdout = mock_stdout
        mock_process.stderr = None

        stdout_lines, stderr_lines = _stream_output(mock_process)

        assert stdout_lines == ["output\n"]
        assert not stderr_lines

    def test_handles_stderr_only(self):
        """Test streaming when only stderr exists."""
        mock_process = Mock()
        mock_stderr = Mock()
        mock_stderr.readline.side_effect = ["error\n", ""]
        mock_stderr.close = Mock()
        mock_process.stdout = None
        mock_process.stderr = mock_stderr

        stdout_lines, stderr_lines = _stream_output(mock_process)

        assert not stdout_lines
        assert stderr_lines == ["error\n"]

    def test_handles_no_streams(self):
        """Test when neither stdout nor stderr exist."""
        mock_process = Mock()
        mock_process.stdout = None
        mock_process.stderr = None

        stdout_lines, stderr_lines = _stream_output(mock_process)

        assert not stdout_lines
        assert not stderr_lines


class TestInvokeCodex:
    """Tests for invoke_codex function."""

    @patch.dict("os.environ", {"CI_CLI_TYPE": "codex"})
    @patch("ci_tools.ci_runtime.codex.log_codex_interaction")
    @patch("subprocess.Popen")
    def test_successful_invocation(self, mock_popen, mock_log):
        """Test successful Codex CLI invocation."""
        mock_process = MagicMock()
        mock_process.wait.return_value = 0
        mock_process.stdout = Mock()
        mock_process.stderr = Mock()
        mock_process.stdout.readline.side_effect = ["assistant:\n", "response text\n", ""]
        mock_process.stderr.readline.return_value = ""
        mock_process.stdout.close = Mock()
        mock_process.stderr.close = Mock()
        mock_popen.return_value.__enter__ = Mock(return_value=mock_process)
        mock_popen.return_value.__exit__ = Mock(return_value=False)

        result = invoke_codex(
            "test prompt",
            model="gpt-5-codex",
            description="test",
            reasoning_effort="high",
        )

        assert result == "response text"
        mock_log.assert_called_once()

    @patch.dict("os.environ", {"CI_CLI_TYPE": "codex"})
    @patch("ci_tools.ci_runtime.codex.log_codex_interaction")
    @patch("subprocess.Popen")
    def test_invocation_without_assistant_prefix(self, mock_popen, _mock_log):
        """Test invocation when response doesn't have assistant prefix."""
        mock_process = MagicMock()
        mock_process.wait.return_value = 0
        mock_process.stdout = Mock()
        mock_process.stderr = Mock()
        mock_process.stdout.readline.side_effect = ["direct response\n", ""]
        mock_process.stderr.readline.return_value = ""
        mock_process.stdout.close = Mock()
        mock_process.stderr.close = Mock()
        mock_popen.return_value.__enter__ = Mock(return_value=mock_process)
        mock_popen.return_value.__exit__ = Mock(return_value=False)

        result = invoke_codex(
            "test prompt",
            model="gpt-5-codex",
            description="test",
            reasoning_effort=None,
        )

        assert result == "direct response"

    @patch.dict("os.environ", {"CI_CLI_TYPE": "codex"})
    @patch("ci_tools.ci_runtime.codex.log_codex_interaction")
    @patch("subprocess.Popen")
    def test_invocation_with_error(self, mock_popen, _mock_log):
        """Test invocation when Codex CLI returns error."""
        mock_process = MagicMock()
        mock_process.wait.return_value = 1
        mock_process.stdout = Mock()
        mock_process.stderr = Mock()
        mock_process.stdout.readline.return_value = ""
        mock_process.stderr.readline.side_effect = ["error occurred\n", ""]
        mock_process.stdout.close = Mock()
        mock_process.stderr.close = Mock()
        mock_popen.return_value.__enter__ = Mock(return_value=mock_process)
        mock_popen.return_value.__exit__ = Mock(return_value=False)

        with pytest.raises(CodexCliError) as exc_info:
            invoke_codex(
                "test prompt",
                model="gpt-5-codex",
                description="test",
                reasoning_effort="low",
            )

        assert "exit status 1" in str(exc_info.value)

    @patch.dict("os.environ", {"CI_CLI_TYPE": "codex"})
    @patch("ci_tools.ci_runtime.codex.log_codex_interaction")
    @patch("subprocess.Popen")
    def test_invocation_returns_stderr_when_no_stdout(self, mock_popen, _mock_log):
        """Test that stderr is returned when stdout is empty."""
        mock_process = MagicMock()
        mock_process.wait.return_value = 0
        mock_process.stdout = Mock()
        mock_process.stderr = Mock()
        mock_process.stdout.readline.return_value = ""
        mock_process.stderr.readline.side_effect = ["stderr output\n", ""]
        mock_process.stdout.close = Mock()
        mock_process.stderr.close = Mock()
        mock_popen.return_value.__enter__ = Mock(return_value=mock_process)
        mock_popen.return_value.__exit__ = Mock(return_value=False)

        result = invoke_codex(
            "test prompt",
            model="gpt-5-codex",
            description="test",
            reasoning_effort="medium",
        )

        assert result == "stderr output"


class TestTruncateError:
    """Tests for truncate_error function."""

    def test_returns_placeholder_for_none(self):
        """Test that None error returns placeholder."""
        assert truncate_error(None) == "(none)"

    def test_returns_placeholder_for_empty_string(self):
        """Test that empty string returns placeholder."""
        assert truncate_error("") == "(none)"
        # Whitespace-only strings get stripped to empty string
        assert truncate_error("   ") == ""

    def test_returns_short_error_unchanged(self):
        """Test that short errors are returned as-is."""
        error = "This is a short error message"
        assert truncate_error(error) == error

    def test_truncates_long_error(self):
        """Test that long errors are truncated."""
        error = "x" * 3000
        result = truncate_error(error, limit=2000)
        assert len(result) == 2000 + len("...(truncated)")
        assert result.endswith("...(truncated)")

    def test_custom_limit(self):
        """Test truncation with custom limit."""
        error = "x" * 200
        result = truncate_error(error, limit=100)
        assert result == "x" * 100 + "...(truncated)"

    def test_strips_whitespace(self):
        """Test that whitespace is stripped before checking length."""
        error = "  error message  "
        assert truncate_error(error) == "error message"


class TestExtractUnifiedDiff:
    """Tests for extract_unified_diff function."""

    def test_returns_none_for_empty_response(self):
        """Test that empty response returns None."""
        assert extract_unified_diff("") is None

    def test_returns_none_for_noop(self):
        """Test that NOOP response returns None."""
        assert extract_unified_diff("NOOP") is None
        assert extract_unified_diff("noop") is None
        assert extract_unified_diff("  NOOP  ") is None

    def test_extracts_diff_block_with_marker(self):
        """Test extraction of diff block with ```diff marker."""
        response = """
Here's the fix:
```diff
diff --git a/file.py b/file.py
--- a/file.py
+++ b/file.py
@@ -1,3 +1,3 @@
-old line
+new line
```
"""
        result = extract_unified_diff(response)
        assert result is not None
        assert result.startswith("diff --git")

    def test_extracts_diff_block_without_marker(self):
        """Test extraction of diff block without language marker."""
        response = """
```
diff --git a/file.py b/file.py
--- a/file.py
+++ b/file.py
```
"""
        result = extract_unified_diff(response)
        assert result is not None
        assert "diff --git" in result

    def test_extracts_first_diff_from_multiple_blocks(self):
        """Test that first diff block is extracted when multiple exist."""
        response = """
```
Some text
```
```diff
--- a/file.py
+++ b/file.py
@@ -1 +1 @@
-old
+new
```
"""
        result = extract_unified_diff(response)
        assert result is not None
        assert "--- a/file.py" in result

    def test_extracts_diff_starting_with_index(self):
        """Test extraction of diff starting with Index:."""
        response = """
```
Index: file.py
===================================================================
--- file.py
+++ file.py
```
"""
        result = extract_unified_diff(response)
        assert result is not None
        assert result.startswith("Index:")

    def test_returns_first_code_block_if_no_diff_markers(self):
        """Test that first code block is returned if no diff markers found."""
        response = """
```
some code
without diff markers
```
"""
        result = extract_unified_diff(response)
        assert result is not None
        assert "some code" in result

    def test_returns_response_text_if_no_code_blocks(self):
        """Test that raw response is returned if no code blocks exist."""
        response = "diff --git a/file.py b/file.py"
        result = extract_unified_diff(response)
        assert result == response


class TestHasUnifiedDiffHeader:
    """Tests for has_unified_diff_header function."""

    def test_detects_diff_git_header(self):
        """Test detection of diff --git header."""
        diff = "diff --git a/file.py b/file.py"
        assert has_unified_diff_header(diff) is True

    def test_detects_minus_line_header(self):
        """Test detection of --- header."""
        diff = "--- a/file.py"
        assert has_unified_diff_header(diff) is True

    def test_detects_plus_line_header(self):
        """Test detection of +++ header."""
        diff = "+++ b/file.py"
        assert has_unified_diff_header(diff) is True

    def test_returns_false_for_no_headers(self):
        """Test that False is returned when no headers present."""
        diff = "just some text\nwithout headers"
        assert has_unified_diff_header(diff) is False

    def test_detects_header_in_middle_of_text(self):
        """Test detection of header in middle of text."""
        diff = "some text\n--- a/file.py\nmore text"
        assert has_unified_diff_header(diff) is True


class TestRequestCodexPatch:
    """Tests for request_codex_patch function."""

    @patch("ci_tools.ci_runtime.codex.invoke_codex")
    def test_builds_prompt_with_all_context(self, mock_invoke):
        """Test that prompt is built with all context fields."""
        mock_invoke.return_value = "diff response"

        failure_context = FailureContext(
            log_excerpt="test failed",
            summary="failure summary",
            implicated_files=["file.py"],
            focused_diff="--- a/file.py",
            coverage_report=None,
        )
        prompt = PatchPrompt(
            command="make test",
            failure_context=failure_context,
            git_diff="working tree diff",
            git_status="M file.py",
            iteration=1,
            patch_error="previous error",
            attempt=1,
        )

        result = request_codex_patch(
            model="gpt-5-codex",
            reasoning_effort="high",
            prompt=prompt,
        )

        assert result == "diff response"
        call_args = mock_invoke.call_args
        prompt_text = call_args[0][0]
        assert "make test" in prompt_text
        assert "Iteration: 1" in prompt_text
        assert "Patch attempt: 1" in prompt_text
        assert "M file.py" in prompt_text
        assert "failure summary" in prompt_text
        assert "test failed" in prompt_text
        assert "previous error" in prompt_text

    @patch("ci_tools.ci_runtime.codex.invoke_codex")
    def test_handles_empty_git_status(self, mock_invoke):
        """Test handling of empty git_status."""
        mock_invoke.return_value = "diff response"

        failure_context = FailureContext(
            log_excerpt="error",
            summary="summary",
            implicated_files=[],
            focused_diff="",
            coverage_report=None,
        )
        prompt = PatchPrompt(
            command="test",
            failure_context=failure_context,
            git_diff="",
            git_status="",
            iteration=1,
            patch_error=None,
            attempt=1,
        )

        request_codex_patch(
            model="gpt-5-codex",
            reasoning_effort="low",
            prompt=prompt,
        )

        call_args = mock_invoke.call_args
        prompt_text = call_args[0][0]
        assert "(clean)" in prompt_text

    @patch("ci_tools.ci_runtime.codex.invoke_codex")
    def test_truncates_patch_error(self, mock_invoke):
        """Test that patch error is truncated in prompt."""
        mock_invoke.return_value = "diff response"

        long_error = "x" * 3000
        failure_context = FailureContext(
            log_excerpt="error",
            summary="summary",
            implicated_files=[],
            focused_diff="",
            coverage_report=None,
        )
        prompt = PatchPrompt(
            command="test",
            failure_context=failure_context,
            git_diff="",
            git_status="",
            iteration=1,
            patch_error=long_error,
            attempt=1,
        )

        request_codex_patch(
            model="gpt-5-codex",
            reasoning_effort="medium",
            prompt=prompt,
        )

        call_args = mock_invoke.call_args
        prompt_text = call_args[0][0]
        # Prompt should contain truncated error
        assert "...(truncated)" in prompt_text


class TestTruncateDiffSummary:
    """Tests for truncate_diff_summary function."""

    def test_returns_false_for_small_diff(self):
        """Test that small diffs are not flagged."""
        diff = "+line1\n-line2\n line3"
        exceeded, message = truncate_diff_summary(diff, line_limit=10)
        assert exceeded is False
        assert message is None

    def test_returns_true_for_large_diff(self):
        """Test that large diffs are flagged."""
        diff = "\n".join(["+line" for _ in range(100)])
        exceeded, message = truncate_diff_summary(diff, line_limit=50)
        assert exceeded is True
        assert message is not None
        assert "100" in message
        assert "50" in message

    def test_counts_only_changed_lines(self):
        """Test that only +/- lines are counted."""
        diff = """
diff --git a/file.py b/file.py
--- a/file.py
+++ b/file.py
@@ -1,3 +1,3 @@
 context line
-removed line
+added line
 more context
"""
        exceeded, _message = truncate_diff_summary(diff, line_limit=10)
        assert exceeded is False

    def test_exact_limit_boundary(self):
        """Test behavior at exact limit boundary."""
        diff = "\n".join(["+line" for _ in range(10)])
        exceeded, _message = truncate_diff_summary(diff, line_limit=10)
        assert exceeded is False

        diff = "\n".join(["+line" for _ in range(11)])
        exceeded, _message = truncate_diff_summary(diff, line_limit=10)
        assert exceeded is True


class TestRiskyPatternInDiff:
    """Tests for risky_pattern_in_diff function."""

    def test_detects_drop_table(self):
        """Test detection of DROP TABLE pattern."""
        diff = "+  DROP TABLE users;"
        result = risky_pattern_in_diff(diff)
        assert result is not None
        assert "DROP" in result

    def test_detects_rm_rf(self):
        """Test detection of rm -rf pattern."""
        diff = "+  rm -rf /important/directory"
        result = risky_pattern_in_diff(diff)
        assert result is not None
        assert "rm" in result

    def test_detects_subprocess_rm(self):
        """Test detection of subprocess.run with rm."""
        diff = '+  subprocess.run(["rm", "-rf", path])'
        result = risky_pattern_in_diff(diff)
        assert result is not None
        assert "subprocess" in result or "rm" in result

    def test_returns_none_for_safe_diff(self):
        """Test that safe diffs return None."""
        diff = """
+def safe_function():
+    return "safe"
-old_line = value
+new_line = value
"""
        result = risky_pattern_in_diff(diff)
        assert result is None

    def test_case_insensitive_detection(self):
        """Test that detection is case insensitive."""
        diff = "+  drop table Users;"
        result = risky_pattern_in_diff(diff)
        assert result is not None

    def test_returns_first_match(self):
        """Test that first matched pattern is returned."""
        diff = """
+  DROP TABLE users;
+  rm -rf /
"""
        result = risky_pattern_in_diff(diff)
        # Should return the first pattern matched
        assert result is not None
