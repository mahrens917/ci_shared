"""Tests for the strict Bandit wrapper."""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from ci_tools.ci_runtime import bandit_wrapper


def test_collect_warning_lines_filters_bandit_logs() -> None:
    """Test that collect_warning_lines filters Bandit warning messages."""
    logs = """
    [manager] WARNING Test in comment: invalid id
    [tester]    WARNING    nosec encountered (B324)
    This line should be ignored
    """
    warnings = bandit_wrapper.collect_warning_lines([logs])
    assert warnings == [
        "[manager] WARNING Test in comment: invalid id",
        "[tester]    WARNING    nosec encountered (B324)",
    ]


def test_run_bandit_returns_underlying_exit_code_when_non_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test that run_bandit returns underlying exit code on non-zero."""
    completed = subprocess.CompletedProcess(
        args=["bandit"],
        returncode=3,
        stdout="Run started...\n",
        stderr="[error] something bad\n",
    )
    with mock.patch("ci_tools.ci_runtime.bandit_wrapper.subprocess.run", return_value=completed):
        exit_code = bandit_wrapper.run_bandit(["-c", "pyproject.toml"])
    captured = capsys.readouterr()
    assert "Run started..." in captured.out
    assert "[error]" in captured.err
    assert exit_code == 3


def test_run_bandit_fails_when_warning_detected(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test that run_bandit fails when warning is detected."""
    completed = subprocess.CompletedProcess(
        args=["bandit"],
        returncode=0,
        stdout="[manager] WARNING invalid escape sequence '\\;'\n",
        stderr="",
    )
    with mock.patch("ci_tools.ci_runtime.bandit_wrapper.subprocess.run", return_value=completed):
        exit_code = bandit_wrapper.run_bandit(["-c", "pyproject.toml"])
    captured = capsys.readouterr()
    assert "Bandit emitted warnings" in captured.err
    assert exit_code == 1


def test_run_bandit_allows_warnings_when_flag_set(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test that run_bandit allows warnings when allow_warnings flag is set."""
    completed = subprocess.CompletedProcess(
        args=["bandit"],
        returncode=0,
        stdout="[tester] WARNING nosec encountered (B324)\n",
        stderr="",
    )
    with mock.patch("ci_tools.ci_runtime.bandit_wrapper.subprocess.run", return_value=completed):
        exit_code = bandit_wrapper.run_bandit(
            ["-c", "pyproject.toml"],
            allow_warnings=True,
        )
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert exit_code == 0


def test_build_parser_defines_custom_module_and_flag() -> None:
    """Test that build_parser defines custom module and allow_warnings flag."""
    parser = bandit_wrapper.build_parser()
    args = parser.parse_args(["--allow-warnings", "--module", "bandit_beta"])
    assert args.allow_warnings is True
    assert args.module == "bandit_beta"


def test_main_invokes_run_bandit_with_parsed_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that main invokes run_bandit with parsed arguments."""
    captured: dict[str, object] = {}

    def fake_run(
        bandit_args: list[str],
        *,
        module: str,
        allow_warnings: bool,
    ) -> int:
        captured["bandit_args"] = list(bandit_args)
        captured["module"] = module
        captured["allow_warnings"] = allow_warnings
        return 0

    monkeypatch.setattr(bandit_wrapper, "run_bandit", fake_run)
    exit_code = bandit_wrapper.main(["--module", "bandit_beta", "-c", "pyproject.toml"])

    assert exit_code == 0
    assert captured["bandit_args"] == ["-c", "pyproject.toml"]
    assert captured["module"] == "bandit_beta"
    assert captured["allow_warnings"] is False


def test_main_requires_bandit_arguments() -> None:
    """Test that main requires at least one bandit argument."""
    with pytest.raises(SystemExit) as excinfo:
        bandit_wrapper.main(["--allow-warnings"])
    assert excinfo.value.code == 2
