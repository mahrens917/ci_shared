"""Unit tests for structure_guard module."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

from tests.conftest import write_module
from ci_tools.scripts.structure_guard import StructureGuard


def test_parse_args_defaults():
    """Test argument parsing with defaults."""
    guard = StructureGuard()
    args = guard.parse_args([])
    assert args.root is None
    assert args.max_class_lines == 100
    assert args.exclude == []


def test_parse_args_custom_values():
    """Test argument parsing with custom values."""
    guard = StructureGuard()
    args = guard.parse_args(
        ["--root", "custom", "--max-class-lines", "50", "--exclude", "tests"]
    )
    assert args.root == [Path("custom")]
    assert args.max_class_lines == 50
    assert args.exclude == [Path("tests")]


def test_scan_file_within_limit(tmp_path: Path):
    """Test scanning a file with class within the line limit."""
    py_file = tmp_path / "small_class.py"
    class_lines = "\n".join(f"    x{i} = {i}" for i in range(20))
    content = f"class SmallClass:\n{class_lines}"
    write_module(py_file, content)

    guard = StructureGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace(max_class_lines=100)
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 0


def test_scan_file_exceeds_limit(tmp_path: Path):
    """Test scanning a file with class exceeding the line limit."""
    py_file = tmp_path / "large_class.py"
    class_lines = "\n".join(f"    x{i} = {i}" for i in range(110))
    content = f"class LargeClass:\n{class_lines}"
    write_module(py_file, content)

    guard = StructureGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace(max_class_lines=100)
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 1
    assert "LargeClass" in violations[0]
    assert "limit 100" in violations[0]


def test_scan_file_multiple_classes(tmp_path: Path):
    """Test scanning a file with multiple classes."""
    py_file = tmp_path / "multi.py"
    small_class = "\n".join(f"    x{i} = {i}" for i in range(20))
    large_class = "\n".join(f"    y{i} = {i}" for i in range(110))
    content = f"class SmallClass:\n{small_class}\n\nclass LargeClass:\n{large_class}"
    write_module(py_file, content)

    guard = StructureGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace(max_class_lines=100)
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 1
    assert "LargeClass" in violations[0]
    assert "SmallClass" not in violations[0]


def test_scan_file_exactly_at_limit(tmp_path: Path):
    """Test scanning a file with class exactly at the line limit."""
    py_file = tmp_path / "exact.py"
    class_lines = "\n".join(f"    x{i} = {i}" for i in range(98))
    content = f"class ExactClass:\n{class_lines}"
    write_module(py_file, content)

    guard = StructureGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace(max_class_lines=100)
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 0


def test_scan_file_no_classes(tmp_path: Path):
    """Test scanning a file with no classes."""
    py_file = tmp_path / "no_classes.py"
    write_module(
        py_file,
        """
        def function1():
            pass

        def function2():
            pass
        """,
    )

    guard = StructureGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace(max_class_lines=100)
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 0


def test_get_violations_header():
    """Test violations header message."""
    guard = StructureGuard()
    args = argparse.Namespace(max_class_lines=100)
    header = guard.get_violations_header(args)
    assert "Oversized classes" in header
    assert "100 lines" in header


@patch("sys.argv", ["structure_guard.py"])
def test_main_no_violations(tmp_path: Path, monkeypatch):
    """Test main entry point with no violations."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    write_module(
        src / "small.py",
        """
        class SmallClass:
            def method(self): pass
        """,
    )

    result = StructureGuard.main()
    assert result == 0


@patch("sys.argv", ["structure_guard.py", "--max-class-lines", "10"])
def test_main_with_violations(tmp_path: Path, monkeypatch):
    """Test main entry point with violations."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    class_lines = "\n".join(f"    x{i} = {i}" for i in range(20))
    content = f"class LargeClass:\n{class_lines}"
    write_module(src / "large.py", content)

    result = StructureGuard.main()
    assert result == 1
