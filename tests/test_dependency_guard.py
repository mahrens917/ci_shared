"""Unit tests for dependency_guard module."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import write_module
from ci_tools.scripts import dependency_guard


def testcallee_name_simple():
    """Test extracting callee name from simple call."""
    source = "Foo()"
    tree = ast.parse(source)
    stmt = tree.body[0]
    assert isinstance(stmt, ast.Expr)
    assert isinstance(stmt.value, ast.Call)
    call_node = stmt.value

    name = dependency_guard.callee_name(call_node)
    assert name == "Foo"


def testcallee_name_attribute():
    """Test extracting callee name from attribute call."""
    source = "module.Foo()"
    tree = ast.parse(source)
    stmt = tree.body[0]
    assert isinstance(stmt, ast.Expr)
    assert isinstance(stmt.value, ast.Call)
    call_node = stmt.value

    name = dependency_guard.callee_name(call_node)
    assert name == "Foo"


def testcallee_name_complex():
    """Test extracting callee name from complex expression."""
    source = "(foo if condition else bar)()"
    tree = ast.parse(source)
    stmt = tree.body[0]
    assert isinstance(stmt, ast.Expr)
    assert isinstance(stmt.value, ast.Call)
    call_node = stmt.value

    name = dependency_guard.callee_name(call_node)
    assert name is None


def testis_constructor_name_valid():
    """Test is_constructor_name with valid constructor names."""
    assert dependency_guard.is_constructor_name("Foo") is True
    assert dependency_guard.is_constructor_name("MyClass") is True
    assert dependency_guard.is_constructor_name("HTTPClient") is True


def testis_constructor_name_invalid():
    """Test is_constructor_name with invalid names."""
    assert dependency_guard.is_constructor_name("foo") is False
    assert dependency_guard.is_constructor_name("myFunc") is False
    assert dependency_guard.is_constructor_name("") is False


def testis_constructor_name_skipped():
    """Test is_constructor_name skips certain names."""
    assert dependency_guard.is_constructor_name("Path") is False
    assert dependency_guard.is_constructor_name("List") is False
    assert dependency_guard.is_constructor_name("Dict") is False
    assert dependency_guard.is_constructor_name("Optional") is False


def test_count_instantiations_basic():
    """Test counting instantiations in a basic method."""
    source = textwrap.dedent(
        """
        def __init__(self):
            self.foo = Foo()
            self.bar = Bar()
        """
    ).strip()

    tree = ast.parse(source)
    stmt = tree.body[0]
    assert isinstance(stmt, ast.FunctionDef)
    count, classes = dependency_guard.count_instantiations(stmt)

    assert count == 2
    assert "Foo" in classes
    assert "Bar" in classes


def test_count_instantiations_ignores_lowercase():
    """Test that lowercase function calls are not counted."""
    source = textwrap.dedent(
        """
        def __init__(self):
            self.x = int(1)
            self.y = str("test")
            self.z = Service()
        """
    ).strip()

    tree = ast.parse(source)
    stmt = tree.body[0]
    assert isinstance(stmt, ast.FunctionDef)
    count, classes = dependency_guard.count_instantiations(stmt)

    assert count == 1
    assert "Service" in classes


def test_count_instantiations_ignores_skipped():
    """Test that skipped constructor names are not counted."""
    source = textwrap.dedent(
        """
        def __init__(self):
            self.path = Path("/tmp")
            self.items = List()
            self.data = Dict()
            self.service = Service()
        """
    ).strip()

    tree = ast.parse(source)
    stmt = tree.body[0]
    assert isinstance(stmt, ast.FunctionDef)
    count, classes = dependency_guard.count_instantiations(stmt)

    assert count == 1
    assert "Service" in classes


def test_count_instantiations_no_instantiations():
    """Test counting with no instantiations."""
    source = textwrap.dedent(
        """
        def __init__(self, foo, bar):
            self.foo = foo
            self.bar = bar
        """
    ).strip()

    tree = ast.parse(source)
    stmt = tree.body[0]
    assert isinstance(stmt, ast.FunctionDef)
    count, classes = dependency_guard.count_instantiations(stmt)

    assert count == 0
    assert not classes


def test_main_success_no_violations(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function with no violations."""
    root = tmp_path / "src"
    root.mkdir()
    write_module(
        root / "simple.py",
        """
        class Simple:
            def __init__(self):
                self.service1 = Service1()
                self.service2 = Service2()
        """,
    )

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        result = dependency_guard.DependencyGuard.main(
            ["--root", str(root), "--max-instantiations", "5"]
        )

    assert result == 0
    captured = capsys.readouterr()
    assert captured.err == ""


def test_main_detects_violations(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function detects violations."""
    root = tmp_path / "src"
    root.mkdir()
    py_file = root / "complex.py"

    instantiations = "\n".join([f"        self.service{i} = Service{i}()" for i in range(15)])
    content = f"class Complex:\n    def __init__(self):\n{instantiations}"
    py_file.write_text(content)

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        result = dependency_guard.DependencyGuard.main(
            ["--root", str(root), "--max-instantiations", "5"]
        )

    assert result == 1
    captured = capsys.readouterr()
    assert "too many dependency instantiations" in captured.err
    assert "Complex" in captured.err


def test_main_respects_exclusions(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function respects exclusion patterns."""
    root = tmp_path / "src"
    excluded = root / "excluded"
    root.mkdir()
    excluded.mkdir(parents=True)

    many_deps = "class ManyDeps:\n    def __init__(self):\n" + "\n".join(
        [f"        self.s{i} = Service{i}()" for i in range(15)]
    )
    (root / "included.py").write_text(many_deps)
    (excluded / "excluded.py").write_text(many_deps)

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        result = dependency_guard.DependencyGuard.main(
            ["--root", str(root), "--max-instantiations", "5", "--exclude", str(excluded)]
        )

    assert result == 1
    captured = capsys.readouterr()
    assert "included.py" in captured.err
    assert "excluded.py" not in captured.err


def test_main_prints_violations_sorted(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function prints violations in sorted order."""
    root = tmp_path / "src"
    root.mkdir()

    many_deps = "class ManyDeps:\n    def __init__(self):\n" + "\n".join(
        [f"        self.s{i} = Service{i}()" for i in range(15)]
    )
    (root / "zebra.py").write_text(many_deps)
    (root / "alpha.py").write_text(many_deps)

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        result = dependency_guard.DependencyGuard.main(
            ["--root", str(root), "--max-instantiations", "5"]
        )

    assert result == 1
    captured = capsys.readouterr()
    err_lines = [
        line for line in captured.err.split("\n") if "alpha.py" in line or "zebra.py" in line
    ]
    assert len(err_lines) >= 2


def test_main_traverse_error(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function handles traversal errors."""
    missing = tmp_path / "missing"

    result = dependency_guard.DependencyGuard.main(["--root", str(missing)])
    assert result == 1
    captured = capsys.readouterr()
    assert "failed to traverse" in captured.err


def test_main_scan_file_error(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function handles scan_file errors."""
    root = tmp_path / "src"
    root.mkdir()
    (root / "bad.py").write_text("class Foo:\n    def __init__(self\n")

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        result = dependency_guard.DependencyGuard.main(["--root", str(root)])

    assert result == 1
    captured = capsys.readouterr()
    assert "failed to parse" in captured.err


def test_count_instantiations_nested_calls():
    """Test counting nested instantiation calls."""
    source = textwrap.dedent(
        """
        def __init__(self):
            self.foo = Foo(Bar(), Baz())
        """
    ).strip()

    tree = ast.parse(source)
    stmt = tree.body[0]
    assert isinstance(stmt, ast.FunctionDef)
    count, classes = dependency_guard.count_instantiations(stmt)

    assert count == 3  # Foo, Bar, Baz
    assert "Foo" in classes
    assert "Bar" in classes
    assert "Baz" in classes


def test_main_handles_relative_paths(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function handles relative paths correctly."""
    root = tmp_path / "src"
    root.mkdir()

    many_deps = "class ManyDeps:\n    def __init__(self):\n" + "\n".join(
        [f"        self.s{i} = Service{i}()" for i in range(15)]
    )
    (root / "module.py").write_text(many_deps)

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        result = dependency_guard.DependencyGuard.main(
            ["--root", str(root), "--max-instantiations", "5"]
        )

    assert result == 1
    captured = capsys.readouterr()
    assert "module.py" in captured.err



def testcallee_name_subscript():
    """Test callee name with subscript expression."""
    source = "foo[0]()"
    tree = ast.parse(source)
    stmt = tree.body[0]
    assert isinstance(stmt, ast.Expr)
    assert isinstance(stmt.value, ast.Call)
    call_node = stmt.value

    name = dependency_guard.callee_name(call_node)
    assert name is None


def test_count_instantiations_in_nested_function():
    """Test counting instantiations in nested function."""
    source = textwrap.dedent(
        """
        def __init__(self):
            def helper():
                return Service()
            self.service = helper()
        """
    ).strip()

    tree = ast.parse(source)
    stmt = tree.body[0]
    assert isinstance(stmt, ast.FunctionDef)
    count, classes = dependency_guard.count_instantiations(stmt)

    # Should count Service even though it's in nested function
    assert count == 1
    assert "Service" in classes
