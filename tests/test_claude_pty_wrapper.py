"""Tests for scripts/claude_pty_wrapper.py."""

from __future__ import annotations

import os
import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from scripts.claude_pty_wrapper import (
    _check_idle_and_exit,
    _emit_heartbeat,
    _handle_ready_fd,
    _process_pty_data,
    _read_pty_chunk,
    cleanup_process,
    diag,
    drain_remaining_output,
    has_visible_content,
    load_prompt,
    log_env_vars,
    main,
    read_loop,
    sanitize_prompt,
    spawn_claude,
    terminate_child,
)


class TestSanitizePrompt:
    def test_removes_ansi_escapes(self):
        assert sanitize_prompt("\x1b[31mhello\x1b[0m") == "hello"

    def test_removes_null_bytes(self):
        assert sanitize_prompt("a\x00b") == "ab"

    def test_keeps_newlines_and_tabs(self):
        assert sanitize_prompt("a\nb\tc") == "a\nb\tc"

    def test_removes_control_characters(self):
        assert sanitize_prompt("a\x01b\x02c") == "abc"

    def test_keeps_printable_ascii(self):
        text = "Hello, World! 123"
        assert sanitize_prompt(text) == text

    def test_keeps_unicode(self):
        assert sanitize_prompt("café") == "café"


class TestHasVisibleContent:
    def test_empty_bytes(self):
        assert has_visible_content(b"") is False

    def test_only_whitespace(self):
        assert has_visible_content(b"   \n\t  ") is False

    def test_ansi_only(self):
        assert has_visible_content(b"\x1b[31m\x1b[0m") is False

    def test_substantial_text(self):
        assert has_visible_content(b"Hello World from Claude") is True

    def test_short_text(self):
        assert has_visible_content(b"Hi") is False

    def test_exactly_threshold(self):
        assert has_visible_content(b"0123456789") is True


class TestDiag:
    def test_writes_to_stderr_when_enabled(self, monkeypatch, capsys):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", True)
        diag("test message")
        captured = capsys.readouterr()
        assert "test message" in captured.err

    def test_silent_when_disabled(self, monkeypatch, capsys):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        diag("test message")
        captured = capsys.readouterr()
        assert captured.err == ""


class TestLogEnvVars:
    def test_masks_api_keys(self, monkeypatch, capsys):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", True)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "testkeytestkeytestkey")
        log_env_vars()
        captured = capsys.readouterr()
        assert "test..." in captured.err
        assert "testkeytestkeytestkey" not in captured.err

    def test_shows_non_key_vars(self, monkeypatch, capsys):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", True)
        monkeypatch.setenv("NODE_OPTIONS", "--max-old-space-size=4096")
        log_env_vars()
        captured = capsys.readouterr()
        assert "--max-old-space-size=4096" in captured.err


class TestLoadPrompt:
    def test_loads_and_sanitizes(self, tmp_path):
        f = tmp_path / "prompt.txt"
        f.write_text("Hello\x00World", encoding="utf-8")
        result = load_prompt(str(f))
        assert result == "HelloWorld"

    def test_truncates_long_prompt(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scripts.claude_pty_wrapper.MAX_PROMPT_SIZE", 10)
        f = tmp_path / "prompt.txt"
        f.write_text("a" * 100, encoding="utf-8")
        result = load_prompt(str(f))
        assert len(result) < 100
        assert "[... truncated ...]" in result


class TestEmitHeartbeat:
    def test_emits_when_interval_passed(self, capsys):
        old_time = time.time() - 10
        result = _emit_heartbeat(old_time)
        assert result > old_time
        captured = capsys.readouterr()
        assert "." in captured.err

    def test_skips_when_recent(self, capsys):
        recent = time.time()
        result = _emit_heartbeat(recent)
        assert result == recent
        captured = capsys.readouterr()
        assert captured.err == ""


class TestProcessPtyData:
    def test_writes_data_to_stdout(self, capsys, monkeypatch):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        total, first = _process_pty_data(b"hello", 0, True)
        assert total == 5
        assert first is False

    def test_accumulates_bytes(self, monkeypatch):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        total, first = _process_pty_data(b"abc", 10, False)
        assert total == 13
        assert first is False


class TestReadPtyChunk:
    def test_returns_data(self):
        r_fd, w_fd = os.pipe()
        os.write(w_fd, b"hello")
        os.close(w_fd)
        data = _read_pty_chunk(r_fd)
        assert data == b"hello"
        os.close(r_fd)

    def test_returns_none_on_error(self, monkeypatch):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        data = _read_pty_chunk(-1)
        assert data is None


class TestHandleReadyFd:
    def test_returns_data_and_updates(self, monkeypatch):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        r_fd, w_fd = os.pipe()
        os.write(w_fd, b"hello world from test")
        os.close(w_fd)
        total, first, keep_ts = _handle_ready_fd(r_fd, 0, True)
        assert total == 21
        assert first is False
        os.close(r_fd)

    def test_returns_false_continue_on_eof(self, monkeypatch):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        r_fd, w_fd = os.pipe()
        os.close(w_fd)
        total, first, keep_going = _handle_ready_fd(r_fd, 5, True)
        assert keep_going is False
        os.close(r_fd)


class TestCheckIdleAndExit:
    def test_no_timeout(self):
        result = _check_idle_and_exit(time.time(), MagicMock())
        assert result is False

    def test_timeout_triggers(self, monkeypatch):
        monkeypatch.setattr("scripts.claude_pty_wrapper.IDLE_TIMEOUT_SECONDS", 0)
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        proc = MagicMock()
        result = _check_idle_and_exit(0.0, proc)
        assert result is True
        proc.terminate.assert_called_once()


class TestTerminateChild:
    def test_terminate_success(self):
        proc = MagicMock()
        terminate_child(proc)
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once()

    def test_terminate_then_kill(self, monkeypatch):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        proc = MagicMock()
        proc.wait.side_effect = [subprocess.TimeoutExpired("cmd", 5), None]
        terminate_child(proc)
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()


class TestCleanupProcess:
    def test_terminates_running_process(self):
        proc = MagicMock()
        proc.poll.return_value = None
        r_fd, w_fd = os.pipe()
        os.close(w_fd)
        cleanup_process(proc, r_fd, -1)
        proc.terminate.assert_called_once()

    def test_skips_terminated_process(self):
        proc = MagicMock()
        proc.poll.return_value = 0
        r_fd, w_fd = os.pipe()
        os.close(w_fd)
        cleanup_process(proc, r_fd, -1)
        proc.terminate.assert_not_called()


class TestDrainRemainingOutput:
    def test_drains_data(self):
        r_fd, w_fd = os.pipe()
        os.write(w_fd, b"remaining data")
        os.close(w_fd)
        drain_remaining_output(r_fd)
        os.close(r_fd)

    def test_handles_empty(self):
        r_fd, w_fd = os.pipe()
        os.close(w_fd)
        drain_remaining_output(r_fd)
        os.close(r_fd)


class TestSpawnClaude:
    def test_spawn_returns_popen(self, monkeypatch):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        mock_popen = MagicMock(spec=subprocess.Popen)
        mock_popen.pid = 12345
        with patch("scripts.claude_pty_wrapper.subprocess.Popen", return_value=mock_popen):
            proc = spawn_claude("prompt", "model", 1)
        assert proc.pid == 12345


class TestMain:
    def test_wrong_argc(self, monkeypatch, capsys):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        monkeypatch.setattr("sys.argv", ["wrapper"])
        result = main()
        assert result == 1
        captured = capsys.readouterr()
        assert "Usage" in captured.err

    def test_wrong_argc_too_many(self, monkeypatch, capsys):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        monkeypatch.setattr("sys.argv", ["wrapper", "a", "b", "c"])
        result = main()
        assert result == 1

    def test_main_success_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("test prompt", encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["wrapper", str(prompt_file), "test-model"])
        mock_proc = MagicMock()
        mock_proc.pid = 999
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0
        with (
            patch("scripts.claude_pty_wrapper.spawn_claude", return_value=mock_proc),
            patch("scripts.claude_pty_wrapper.read_loop", return_value=(100, False)),
            patch("scripts.claude_pty_wrapper.cleanup_process"),
        ):
            result = main()
        assert result == 0

    def test_main_timeout_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("test prompt", encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["wrapper", str(prompt_file), "test-model"])
        mock_proc = MagicMock()
        mock_proc.pid = 999
        mock_proc.poll.return_value = 0
        with (
            patch("scripts.claude_pty_wrapper.spawn_claude", return_value=mock_proc),
            patch("scripts.claude_pty_wrapper.read_loop", return_value=(50, True)),
            patch("scripts.claude_pty_wrapper.cleanup_process"),
        ):
            result = main()
        assert result == 124

    def test_main_exception_path(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("test prompt", encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["wrapper", str(prompt_file), "test-model"])
        with (
            patch("scripts.claude_pty_wrapper.spawn_claude", side_effect=RuntimeError("test error")),
            patch("scripts.claude_pty_wrapper.cleanup_process"),
        ):
            result = main()
        assert result == 1
        captured = capsys.readouterr()
        assert "test error" in captured.err


class TestReadLoop:
    def test_running_flag_stops_loop(self, monkeypatch):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        proc = MagicMock()
        proc.poll.return_value = None
        total, timed_out = read_loop(0, proc, [False])
        assert timed_out is False

    def test_process_exits_after_select(self, monkeypatch):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        proc = MagicMock()
        proc.poll.return_value = 0
        with patch("scripts.claude_pty_wrapper.select.select", return_value=([], [], [])):
            total, timed_out = read_loop(0, proc, [True])
        assert timed_out is False

    def test_reads_data_then_process_exits(self, monkeypatch):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        proc = MagicMock()
        poll_results = [None, 0]
        proc.poll.side_effect = lambda: poll_results.pop(0) if poll_results else 0

        with (
            patch("scripts.claude_pty_wrapper.select.select", return_value=([0], [], [])),
            patch("scripts.claude_pty_wrapper._handle_ready_fd", return_value=(20, False, True)),
            patch("scripts.claude_pty_wrapper.drain_remaining_output"),
        ):
            total, timed_out = read_loop(0, proc, [True])
        assert total == 20
        assert timed_out is False

    def test_idle_timeout_triggers(self, monkeypatch):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        monkeypatch.setattr("scripts.claude_pty_wrapper.IDLE_TIMEOUT_SECONDS", 0)
        proc = MagicMock()
        proc.poll.return_value = None
        with (
            patch("scripts.claude_pty_wrapper.select.select", return_value=([0], [], [])),
            patch("scripts.claude_pty_wrapper._handle_ready_fd", return_value=(5, False, True)),
        ):
            total, timed_out = read_loop(0, proc, [True])
        assert timed_out is True
        proc.terminate.assert_called()

    def test_heartbeat_on_no_data(self, monkeypatch, capsys):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        proc = MagicMock()
        call_count = [0]

        def mock_poll():
            call_count[0] += 1
            if call_count[0] >= 2:
                return 0
            return None

        proc.poll = mock_poll
        with patch("scripts.claude_pty_wrapper.select.select", return_value=([], [], [])):
            total, timed_out = read_loop(0, proc, [True])
        assert timed_out is False


class TestHandleReadyFdError:
    def test_returns_false_on_read_error(self, monkeypatch):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        total, first, keep_ts = _handle_ready_fd(-1, 5, True)
        assert keep_ts is False
        assert total == 5


class TestCleanupProcessExtended:
    def test_cleanup_with_timeout_expired(self, monkeypatch):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 3)
        r_fd, w_fd = os.pipe()
        os.close(w_fd)
        cleanup_process(proc, r_fd, -1)
        proc.kill.assert_called_once()

    def test_cleanup_closes_slave_fd(self, monkeypatch):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        proc = MagicMock()
        proc.poll.return_value = 0
        r_fd, w_fd = os.pipe()
        cleanup_process(proc, r_fd, w_fd)

    def test_cleanup_handles_bad_master_fd(self, monkeypatch):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", False)
        proc = MagicMock()
        proc.poll.return_value = 0
        cleanup_process(proc, -1, -1)


class TestLogEnvVarsExtended:
    def test_short_key_not_masked(self, monkeypatch, capsys):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", True)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "short")
        log_env_vars()
        captured = capsys.readouterr()
        assert "short" in captured.err

    def test_missing_vars_silent(self, monkeypatch, capsys):
        monkeypatch.setattr("scripts.claude_pty_wrapper.DIAG_ENABLED", True)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("NODE_OPTIONS", raising=False)
        monkeypatch.delenv("CLAUDE_BASH_NO_LOGIN", raising=False)
        log_env_vars()
        captured = capsys.readouterr()
        assert "ENV" not in captured.err
