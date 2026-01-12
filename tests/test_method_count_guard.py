"""Unit tests for method_count_guard module."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

from tests.conftest import write_module
from ci_tools.scripts.method_count_guard import MethodCountGuard


def test_parse_args_defaults():
    """Test argument parsing with defaults."""
    guard = MethodCountGuard()
    args = guard.parse_args([])
    assert args.root == Path("src")
    assert args.max_public_methods == 15
    assert args.max_total_methods == 25
    assert args.exclude == []


def test_parse_args_custom_values():
    """Test argument parsing with custom values."""
    guard = MethodCountGuard()
    args = guard.parse_args(
        [
            "--root",
            "custom",
            "--max-public-methods",
            "10",
            "--max-total-methods",
            "20",
            "--exclude",
            "tests",
        ]
    )
    assert args.root == Path("custom")
    assert args.max_public_methods == 10
    assert args.max_total_methods == 20
    assert args.exclude == [Path("tests")]


def test_scan_file_within_limits(tmp_path: Path):
    """Test scanning a file with class within method limits."""
    py_file = tmp_path / "small_class.py"
    write_module(
        py_file,
        """
        class SmallClass:
            def method1(self):
                pass
            def method2(self):
                pass
            def _private1(self):
                pass
        """,
    )

    guard = MethodCountGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace(max_public_methods=15, max_total_methods=25)
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 0


def test_scan_file_exceeds_public_limit(tmp_path: Path):
    """Test scanning a file with class exceeding public method limit."""
    py_file = tmp_path / "large_class.py"
    methods = "\n".join(f"    def method{i}(self): pass" for i in range(20))
    content = f"class LargeClass:\n{methods}"
    write_module(py_file, content)

    guard = MethodCountGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace(max_public_methods=15, max_total_methods=25)
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 1
    assert "LargeClass" in violations[0]
    assert "20 public methods" in violations[0]


def test_scan_file_exceeds_total_limit(tmp_path: Path):
    """Test scanning a file with class exceeding total method limit."""
    py_file = tmp_path / "large_class.py"
    public_methods = "\n".join(f"    def method{i}(self): pass" for i in range(10))
    private_methods = "\n".join(f"    def _private{i}(self): pass" for i in range(20))
    content = f"class LargeClass:\n{public_methods}\n{private_methods}"
    write_module(py_file, content)

    guard = MethodCountGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace(max_public_methods=15, max_total_methods=25)
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 1
    assert "LargeClass" in violations[0]
    assert "30 total methods" in violations[0]


def test_scan_file_exceeds_both_limits(tmp_path: Path):
    """Test scanning a file with class exceeding both limits."""
    py_file = tmp_path / "huge_class.py"
    public_methods = "\n".join(f"    def method{i}(self): pass" for i in range(20))
    private_methods = "\n".join(f"    def _private{i}(self): pass" for i in range(10))
    content = f"class HugeClass:\n{public_methods}\n{private_methods}"
    write_module(py_file, content)

    guard = MethodCountGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace(max_public_methods=15, max_total_methods=25)
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 1
    assert "HugeClass" in violations[0]
    assert "20 public methods" in violations[0]
    assert "30 total methods" in violations[0]


def test_scan_file_multiple_classes(tmp_path: Path):
    """Test scanning a file with multiple classes."""
    py_file = tmp_path / "multi.py"
    write_module(
        py_file,
        """
        class GoodClass:
            def method1(self): pass

        class BadClass:
            def method1(self): pass
            def method2(self): pass
            def method3(self): pass
            def method4(self): pass
            def method5(self): pass
            def method6(self): pass
            def method7(self): pass
            def method8(self): pass
            def method9(self): pass
            def method10(self): pass
            def method11(self): pass
            def method12(self): pass
            def method13(self): pass
            def method14(self): pass
            def method15(self): pass
            def method16(self): pass
        """,
    )

    guard = MethodCountGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace(max_public_methods=15, max_total_methods=25)
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 1
    assert "BadClass" in violations[0]


def test_get_violations_header():
    """Test violations header message."""
    guard = MethodCountGuard()
    args = argparse.Namespace()
    header = guard.get_violations_header(args)
    assert "too many methods" in header
    assert "multi-concern" in header


def test_get_violations_footer():
    """Test violations footer message."""
    guard = MethodCountGuard()
    args = argparse.Namespace()
    footer = guard.get_violations_footer(args)
    assert footer is not None
    assert "service" in footer


@patch("sys.argv", ["method_count_guard.py"])
def test_main_no_violations(tmp_path: Path, monkeypatch):
    """Test main entry point with no violations."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    write_module(
        src / "good.py",
        """
        class GoodClass:
            def method(self): pass
        """,
    )

    result = MethodCountGuard.main()
    assert result == 0


@patch("sys.argv", ["method_count_guard.py", "--max-public-methods", "2"])
def test_main_with_violations(tmp_path: Path, monkeypatch):
    """Test main entry point with violations."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    write_module(
        src / "bad.py",
        """
        class BadClass:
            def method1(self): pass
            def method2(self): pass
            def method3(self): pass
        """,
    )

    result = MethodCountGuard.main()
    assert result == 1
