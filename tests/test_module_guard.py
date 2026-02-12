"""Unit tests for module_guard module."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

from tests.conftest import write_module
from ci_tools.scripts.module_guard import ModuleGuard


def test_parse_args_defaults():
    """Test argument parsing with defaults."""
    guard = ModuleGuard()
    args = guard.parse_args([])
    assert args.root is None
    assert args.max_module_lines == 600
    assert args.exclude == []


def test_parse_args_custom_values():
    """Test argument parsing with custom values."""
    guard = ModuleGuard()
    args = guard.parse_args(
        ["--root", "custom", "--max-module-lines", "400", "--exclude", "tests"]
    )
    assert args.root == [Path("custom")]
    assert args.max_module_lines == 400
    assert args.exclude == [Path("tests")]


def test_scan_file_within_limit(tmp_path: Path):
    """Test scanning a file within the line limit."""
    py_file = tmp_path / "small.py"
    lines = "\n".join(f"x{i} = {i}" for i in range(50))
    write_module(py_file, lines)

    guard = ModuleGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace(max_module_lines=600)
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 0


def test_scan_file_exceeds_limit(tmp_path: Path):
    """Test scanning a file exceeding the line limit."""
    py_file = tmp_path / "large.py"
    lines = "\n".join(f"x{i} = {i}" for i in range(700))
    write_module(py_file, lines)

    guard = ModuleGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace(max_module_lines=600)
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 1
    assert "large.py" in violations[0]
    assert "700 lines" in violations[0]
    assert "limit 600" in violations[0]


def test_scan_file_exactly_at_limit(tmp_path: Path):
    """Test scanning a file exactly at the line limit."""
    py_file = tmp_path / "exact.py"
    lines = "\n".join(f"x{i} = {i}" for i in range(600))
    write_module(py_file, lines)

    guard = ModuleGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace(max_module_lines=600)
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 0


def test_scan_file_syntax_error(tmp_path: Path):
    """Test scanning a file with syntax error."""
    py_file = tmp_path / "bad.py"
    py_file.write_text("def foo(", encoding="utf-8")

    guard = ModuleGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace(max_module_lines=600)

    try:
        guard.scan_file(py_file, args)
        assert False, "Expected RuntimeError"
    except RuntimeError as exc:
        assert "failed to read" in str(exc)


def test_get_violations_header():
    """Test violations header message."""
    guard = ModuleGuard()
    args = argparse.Namespace(max_module_lines=600)
    header = guard.get_violations_header(args)
    assert "Oversized modules" in header
    assert "600 lines" in header


@patch("sys.argv", ["module_guard.py"])
def test_main_no_violations(tmp_path: Path, monkeypatch):
    """Test main entry point with no violations."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    lines = "\n".join(f"x{i} = {i}" for i in range(50))
    write_module(src / "small.py", lines)

    result = ModuleGuard.main()
    assert result == 0


@patch("sys.argv", ["module_guard.py", "--max-module-lines", "50"])
def test_main_with_violations(tmp_path: Path, monkeypatch):
    """Test main entry point with violations."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    lines = "\n".join(f"x{i} = {i}" for i in range(100))
    write_module(src / "large.py", lines)

    result = ModuleGuard.main()
    assert result == 1
