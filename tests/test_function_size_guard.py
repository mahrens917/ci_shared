"""Unit tests for function_size_guard module."""

from __future__ import annotations

import argparse
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import write_module
from ci_tools.scripts import function_size_guard
from ci_tools.scripts.guard_common import count_ast_node_lines


def test_parse_args_defaults():
    """Test argument parsing with defaults."""
    guard = function_size_guard.FunctionSizeGuard()
    args = guard.parse_args([])
    assert args.root is None
    assert args.max_function_lines == 80
    assert args.exclude == []


def test_parse_args_custom_values():
    """Test argument parsing with custom values."""
    guard = function_size_guard.FunctionSizeGuard()
    args = guard.parse_args(
        ["--root", "custom", "--max-function-lines", "50", "--exclude", "tests"]
    )
    assert args.root == [Path("custom")]
    assert args.max_function_lines == 50
    assert args.exclude == [Path("tests")]


def test_count_function_lines_basic():
    """Test counting lines for basic function."""
    source = textwrap.dedent(
        """
        def foo():
            x = 1
            return x
        """
    ).strip()

    tree = function_size_guard.ast.parse(source)
    func_node = tree.body[0]
    count = count_ast_node_lines(func_node)
    assert count == 3


def test_count_function_lines_no_end_lineno():
    """Test counting lines when end_lineno is None."""
    source = "def foo(): pass"
    tree = function_size_guard.ast.parse(source)
    func_node = tree.body[0]

    # Simulate missing end_lineno
    if hasattr(func_node, "end_lineno"):
        original_end = func_node.end_lineno
        func_node.end_lineno = None
        count = count_ast_node_lines(func_node)
        assert count == 0
        func_node.end_lineno = original_end


def test_count_function_lines_async_function():
    """Test counting lines for async function."""
    source = textwrap.dedent(
        """
        async def async_foo():
            x = 1
            await something()
            return x
        """
    ).strip()

    tree = function_size_guard.ast.parse(source)
    func_node = tree.body[0]
    count = count_ast_node_lines(func_node)
    assert count == 4


def test_scan_file_within_limit(tmp_path: Path):
    """Test scanning a file within the line limit."""
    py_file = tmp_path / "small.py"
    write_module(
        py_file,
        """
        def small_function():
            return 1
        """,
    )

    guard = function_size_guard.FunctionSizeGuard()
    args = argparse.Namespace(max_function_lines=10)
    guard.repo_root = tmp_path
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 0


def test_scan_file_exceeds_limit(tmp_path: Path):
    """Test scanning a file that exceeds the limit."""
    py_file = tmp_path / "large.py"
    lines = "\n".join([f"    line_{i} = {i}" for i in range(20)])
    content = f"def large_function():\n{lines}"
    py_file.write_text(content)

    guard = function_size_guard.FunctionSizeGuard()
    args = argparse.Namespace(max_function_lines=10)
    guard.repo_root = tmp_path
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 1
    assert "large_function" in violations[0]
    assert "(line 1)" in violations[0]


def test_scan_file_multiple_functions(tmp_path: Path):
    """Test scanning a file with multiple functions."""
    py_file = tmp_path / "multi.py"
    write_module(
        py_file,
        """
        def small_function():
            return 1

        def large_function():
            x = 1
            y = 2
            z = 3
            a = 4
            b = 5
            c = 6
            d = 7
            e = 8
            f = 9
            g = 10
            return x + y + z + a + b + c + d + e + f + g
        """,
    )

    guard = function_size_guard.FunctionSizeGuard()
    args = argparse.Namespace(max_function_lines=5)
    guard.repo_root = tmp_path
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 1
    assert "large_function" in violations[0]


def test_scan_file_syntax_error(tmp_path: Path):
    """Test scan_file with syntax error."""
    py_file = tmp_path / "bad.py"
    py_file.write_text("def foo(\n")  # Missing closing paren

    guard = function_size_guard.FunctionSizeGuard()
    args = argparse.Namespace(max_function_lines=10)
    guard.repo_root = tmp_path
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 0


def test_scan_file_unicode_decode_error(tmp_path: Path):
    """Test scan_file with Unicode decode error."""
    py_file = tmp_path / "bad_encoding.py"
    py_file.write_bytes(b"\xff\xfe\x00\x00")  # Invalid UTF-8

    guard = function_size_guard.FunctionSizeGuard()
    args = argparse.Namespace(max_function_lines=10)
    guard.repo_root = tmp_path
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 0


def test_scan_file_async_functions(tmp_path: Path):
    """Test scanning file with async functions."""
    py_file = tmp_path / "async_funcs.py"
    lines = "\n".join([f"    line_{i} = {i}" for i in range(15)])
    content = f"async def large_async():\n{lines}"
    py_file.write_text(content)

    guard = function_size_guard.FunctionSizeGuard()
    args = argparse.Namespace(max_function_lines=10)
    guard.repo_root = tmp_path
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 1
    assert "large_async" in violations[0]


def test_main_success_no_violations(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function with no violations."""
    root = tmp_path / "src"
    root.mkdir()
    write_module(
        root / "small.py",
        """
        def small_function():
            return 1
        """,
    )

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        result = function_size_guard.FunctionSizeGuard.main(
            ["--root", str(root), "--max-function-lines", "10"]
        )

    assert result == 0
    captured = capsys.readouterr()
    assert captured.err == ""


def test_main_detects_violations(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function detects violations."""
    root = tmp_path / "src"
    root.mkdir()
    py_file = root / "large.py"

    lines = "\n".join([f"    line_{i} = {i}" for i in range(20)])
    content = f"def large_function():\n{lines}"
    py_file.write_text(content)

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        result = function_size_guard.FunctionSizeGuard.main(
            ["--root", str(root), "--max-function-lines", "10"]
        )

    assert result == 1
    captured = capsys.readouterr()
    assert "Oversized functions detected" in captured.err
    assert "large_function" in captured.err


def test_main_respects_exclusions(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function respects exclusion patterns."""
    root = tmp_path / "src"
    excluded = root / "excluded"
    root.mkdir()
    excluded.mkdir(parents=True)

    large_func = "def large():\n" + "\n".join([f"    line_{i} = {i}" for i in range(20)])
    (root / "included.py").write_text(large_func)
    (excluded / "excluded.py").write_text(large_func)

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        result = function_size_guard.FunctionSizeGuard.main(
            ["--root", str(root), "--max-function-lines", "10", "--exclude", str(excluded)]
        )

    assert result == 1
    captured = capsys.readouterr()
    assert "included.py" in captured.err
    assert "excluded.py" not in captured.err


def test_main_handles_multiple_violations(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function handles multiple violations."""
    root = tmp_path / "src"
    root.mkdir()

    large_func = "def large():\n" + "\n".join([f"    line_{i} = {i}" for i in range(20)])
    (root / "file1.py").write_text(large_func)
    (root / "file2.py").write_text(large_func)

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        result = function_size_guard.FunctionSizeGuard.main(
            ["--root", str(root), "--max-function-lines", "10"]
        )

    assert result == 1
    captured = capsys.readouterr()
    assert "file1.py" in captured.err
    assert "file2.py" in captured.err


def test_main_prints_violations_sorted(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function prints violations in sorted order."""
    root = tmp_path / "src"
    root.mkdir()

    large_func = "def large():\n" + "\n".join([f"    line_{i} = {i}" for i in range(20)])
    (root / "zebra.py").write_text(large_func)
    (root / "alpha.py").write_text(large_func)

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        result = function_size_guard.FunctionSizeGuard.main(
            ["--root", str(root), "--max-function-lines", "10"]
        )

    assert result == 1
    captured = capsys.readouterr()
    err_lines = [
        line for line in captured.err.split("\n") if "alpha.py" in line or "zebra.py" in line
    ]
    assert len(err_lines) == 2
    assert "alpha.py" in err_lines[0]
    assert "zebra.py" in err_lines[1]


def test_main_traverse_error(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function handles traversal errors."""
    missing = tmp_path / "missing"

    result = function_size_guard.FunctionSizeGuard.main(["--root", str(missing)])
    assert result == 1
    captured = capsys.readouterr()
    assert "failed to traverse" in captured.err


def test_scan_file_no_functions(tmp_path: Path):
    """Test scanning a file with no functions."""
    py_file = tmp_path / "no_funcs.py"
    write_module(
        py_file,
        """
        x = 1
        y = 2
        """,
    )

    guard = function_size_guard.FunctionSizeGuard()
    args = argparse.Namespace(max_function_lines=10)
    guard.repo_root = tmp_path
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 0


def test_scan_file_nested_functions(tmp_path: Path):
    """Test scanning file with nested functions."""
    py_file = tmp_path / "nested.py"
    lines = "\n".join([f"        inner_line_{i} = {i}" for i in range(15)])
    content = f"def outer():\n    def inner():\n{lines}\n    return inner"
    py_file.write_text(content)

    guard = function_size_guard.FunctionSizeGuard()
    args = argparse.Namespace(max_function_lines=10)
    guard.repo_root = tmp_path
    violations = guard.scan_file(py_file, args)
    # Should detect the large inner function
    assert len(violations) >= 1


def test_scan_file_methods_in_class(tmp_path: Path):
    """Test scanning methods inside classes."""
    py_file = tmp_path / "methods.py"
    lines = "\n".join([f"        line_{i} = {i}" for i in range(20)])
    content = f"class Foo:\n    def large_method(self):\n{lines}"
    py_file.write_text(content)

    guard = function_size_guard.FunctionSizeGuard()
    args = argparse.Namespace(max_function_lines=10)
    guard.repo_root = tmp_path
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 1
    assert "large_method" in violations[0]


def test_main_handles_relative_paths(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function handles relative paths correctly."""
    root = tmp_path / "src"
    root.mkdir()

    large_func = "def large():\n" + "\n".join([f"    line_{i} = {i}" for i in range(20)])
    (root / "module.py").write_text(large_func)

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        result = function_size_guard.FunctionSizeGuard.main(
            ["--root", str(root), "--max-function-lines", "10"]
        )

    assert result == 1
    captured = capsys.readouterr()
    assert "module.py" in captured.err
    assert "large" in captured.err




def test_count_function_lines_single_line():
    """Test counting lines for single-line function."""
    source = "def foo(): return 1"
    tree = function_size_guard.ast.parse(source)
    func_node = tree.body[0]
    count = count_ast_node_lines(func_node)
    assert count == 1


def test_scan_file_with_decorators(tmp_path: Path):
    """Test scanning functions with decorators."""
    py_file = tmp_path / "decorated.py"
    lines = "\n".join([f"    line_{i} = {i}" for i in range(15)])
    content = f"@decorator\ndef decorated_func():\n{lines}"
    py_file.write_text(content)

    guard = function_size_guard.FunctionSizeGuard()
    args = argparse.Namespace(max_function_lines=10)
    guard.repo_root = tmp_path
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 1
    assert "decorated_func" in violations[0]
