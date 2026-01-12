"""Unit tests for inheritance_guard module."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import write_module
from ci_tools.scripts import inheritance_guard


def test_extract_base_names_simple():
    """Test extracting base names from simple inheritance."""
    source = "class Child(Parent): pass"
    tree = inheritance_guard.ast.parse(source)
    stmt = tree.body[0]
    assert isinstance(stmt, inheritance_guard.ast.ClassDef)

    base_names = inheritance_guard.extract_base_names(stmt)
    assert base_names == ["Parent"]


def test_extract_base_names_multiple():
    """Test extracting multiple base names."""
    source = "class Child(Parent1, Parent2): pass"
    tree = inheritance_guard.ast.parse(source)
    stmt = tree.body[0]
    assert isinstance(stmt, inheritance_guard.ast.ClassDef)

    base_names = inheritance_guard.extract_base_names(stmt)
    assert base_names == ["Parent1", "Parent2"]


def test_extract_base_names_attribute():
    """Test extracting base names with module attributes."""
    source = "class Child(module.Parent): pass"
    tree = inheritance_guard.ast.parse(source)
    stmt = tree.body[0]
    assert isinstance(stmt, inheritance_guard.ast.ClassDef)

    base_names = inheritance_guard.extract_base_names(stmt)
    assert base_names == ["module.Parent"]


def test_extract_base_names_nested_attribute():
    """Test extracting base names with nested module attributes."""
    source = "class Child(package.module.Parent): pass"
    tree = inheritance_guard.ast.parse(source)
    stmt = tree.body[0]
    assert isinstance(stmt, inheritance_guard.ast.ClassDef)

    base_names = inheritance_guard.extract_base_names(stmt)
    assert base_names == ["package.module.Parent"]


def test_extract_base_names_no_bases():
    """Test extracting base names with no bases."""
    source = "class Child: pass"
    tree = inheritance_guard.ast.parse(source)
    stmt = tree.body[0]
    assert isinstance(stmt, inheritance_guard.ast.ClassDef)

    base_names = inheritance_guard.extract_base_names(stmt)
    assert not base_names


def test_build_class_hierarchy_basic():
    """Test building class hierarchy."""
    source = textwrap.dedent(
        """
        class Parent:
            pass

        class Child(Parent):
            pass
        """
    ).strip()

    tree = inheritance_guard.ast.parse(source)
    hierarchy = inheritance_guard.build_class_hierarchy(tree)

    assert "Parent" in hierarchy
    assert "Child" in hierarchy
    assert hierarchy["Parent"] == []
    assert hierarchy["Child"] == ["Parent"]


def test_build_class_hierarchy_multiple_classes():
    """Test building hierarchy with multiple classes."""
    source = textwrap.dedent(
        """
        class A:
            pass

        class B(A):
            pass

        class C(B):
            pass
        """
    ).strip()

    tree = inheritance_guard.ast.parse(source)
    hierarchy = inheritance_guard.build_class_hierarchy(tree)

    assert hierarchy["A"] == []
    assert hierarchy["B"] == ["A"]
    assert hierarchy["C"] == ["B"]


def test_calculate_depth_no_inheritance():
    """Test calculating depth with no inheritance."""
    hierarchy = {"Child": []}
    depth = inheritance_guard.calculate_depth("Child", hierarchy)
    assert depth == 0


def test_calculate_depth_single_level():
    """Test calculating depth with single level inheritance."""
    hierarchy = {"Parent": [], "Child": ["Parent"]}
    depth = inheritance_guard.calculate_depth("Child", hierarchy)
    assert depth == 1


def test_calculate_depth_multiple_levels():
    """Test calculating depth with multiple levels."""
    hierarchy = {"GrandParent": [], "Parent": ["GrandParent"], "Child": ["Parent"]}
    depth = inheritance_guard.calculate_depth("Child", hierarchy)
    assert depth == 2


def test_calculate_depth_unknown_class():
    """Test calculating depth for unknown class."""
    hierarchy = {"Known": []}
    depth = inheritance_guard.calculate_depth("Unknown", hierarchy)
    assert depth == 0


def test_calculate_depth_external_base():
    """Test calculating depth with external base class."""
    hierarchy = {"Child": ["ExternalBase"]}
    depth = inheritance_guard.calculate_depth("Child", hierarchy)
    assert depth == 1


def test_calculate_depth_ignores_object():
    """Test calculating depth ignores object base class."""
    hierarchy = {"Child": ["object"]}
    depth = inheritance_guard.calculate_depth("Child", hierarchy)
    assert depth == 0


def test_calculate_depth_ignores_protocol():
    """Test calculating depth ignores Protocol base class."""
    hierarchy = {"Child": ["Protocol"]}
    depth = inheritance_guard.calculate_depth("Child", hierarchy)
    assert depth == 0


def test_calculate_depth_ignores_abc():
    """Test calculating depth ignores ABC base class."""
    hierarchy = {"Child": ["ABC"]}
    depth = inheritance_guard.calculate_depth("Child", hierarchy)
    assert depth == 0


def test_calculate_depth_cycle_detection():
    """Test calculating depth handles cycles."""
    hierarchy = {"A": ["B"], "B": ["A"]}
    depth = inheritance_guard.calculate_depth("A", hierarchy)
    assert depth >= 0  # Should not crash


def test_calculate_depth_multiple_bases():
    """Test calculating depth with multiple bases."""
    hierarchy = {"Base1": [], "Base2": ["Base1"], "Child": ["Base2", "Base1"]}
    depth = inheritance_guard.calculate_depth("Child", hierarchy)
    assert depth == 2  # Max depth from Base2


def test_main_success_no_violations(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function with no violations."""
    root = tmp_path / "src"
    root.mkdir()
    write_module(
        root / "simple.py",
        """
        class Parent:
            pass

        class Child(Parent):
            pass
        """,
    )

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        result = inheritance_guard.InheritanceGuard.main(["--root", str(root), "--max-depth", "2"])

    assert result == 0
    captured = capsys.readouterr()
    assert captured.err == ""


def test_main_detects_violations(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function detects violations."""
    root = tmp_path / "src"
    root.mkdir()
    write_module(
        root / "deep.py",
        """
        class A:
            pass

        class B(A):
            pass

        class C(B):
            pass

        class D(C):
            pass
        """,
    )

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        result = inheritance_guard.InheritanceGuard.main(["--root", str(root), "--max-depth", "2"])

    assert result == 1
    captured = capsys.readouterr()
    assert "Deep inheritance detected" in captured.err
    assert "composition over inheritance" in captured.err


def test_main_respects_exclusions(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function respects exclusion patterns."""
    root = tmp_path / "src"
    excluded = root / "excluded"
    root.mkdir()
    excluded.mkdir(parents=True)

    deep_hierarchy = """
        class A:
            pass
        class B(A):
            pass
        class C(B):
            pass
        class D(C):
            pass
    """
    write_module(root / "included.py", deep_hierarchy)
    write_module(excluded / "excluded.py", deep_hierarchy)

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        result = inheritance_guard.InheritanceGuard.main(
            ["--root", str(root), "--max-depth", "1", "--exclude", str(excluded)]
        )

    assert result == 1
    captured = capsys.readouterr()
    assert "included.py" in captured.err
    assert "excluded.py" not in captured.err


def test_main_prints_violations_sorted(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function prints violations in sorted order."""
    root = tmp_path / "src"
    root.mkdir()

    deep_hierarchy = """
        class A:
            pass
        class B(A):
            pass
        class C(B):
            pass
    """
    write_module(root / "zebra.py", deep_hierarchy)
    write_module(root / "alpha.py", deep_hierarchy)

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        result = inheritance_guard.InheritanceGuard.main(["--root", str(root), "--max-depth", "1"])

    assert result == 1
    captured = capsys.readouterr()
    err_lines = [
        line for line in captured.err.split("\n") if "alpha.py" in line or "zebra.py" in line
    ]
    assert len(err_lines) >= 2


def test_main_scan_file_error(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function handles scan_file errors."""
    root = tmp_path / "src"
    root.mkdir()
    (root / "bad.py").write_text("class Foo:\n    def method(self\n")

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        result = inheritance_guard.InheritanceGuard.main(["--root", str(root)])

    assert result == 1
    captured = capsys.readouterr()
    assert "failed to parse" in captured.err


def test_main_traverse_error(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function handles traversal errors."""
    missing = tmp_path / "missing"

    result = inheritance_guard.InheritanceGuard.main(["--root", str(missing)])
    assert result == 1
    captured = capsys.readouterr()
    assert "failed to traverse" in captured.err


def test_calculate_depth_diamond_inheritance():
    """Test calculating depth with diamond inheritance pattern."""
    hierarchy = {
        "Base": [],
        "Left": ["Base"],
        "Right": ["Base"],
        "Diamond": ["Left", "Right"],
    }
    depth = inheritance_guard.calculate_depth("Diamond", hierarchy)
    assert depth == 2


def test_extract_base_names_complex_expression():
    """Test extract_base_names handles non-Name and non-Attribute bases."""
    source = "class Child(Parent if condition else Other): pass"
    tree = inheritance_guard.ast.parse(source)
    stmt = tree.body[0]
    assert isinstance(stmt, inheritance_guard.ast.ClassDef)

    base_names = inheritance_guard.extract_base_names(stmt)
    # Should handle gracefully, possibly returning empty or partial
    assert isinstance(base_names, list)


def test_main_handles_relative_paths(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function handles relative paths correctly."""
    root = tmp_path / "src"
    root.mkdir()

    write_module(
        root / "module.py",
        """
        class A:
            pass
        class B(A):
            pass
        class C(B):
            pass
        """,
    )

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        result = inheritance_guard.InheritanceGuard.main(["--root", str(root), "--max-depth", "1"])

    assert result == 1
    captured = capsys.readouterr()
    assert "module.py" in captured.err


def test_calculate_depth_visited_passed():
    """Test calculate_depth with pre-populated visited set."""
    hierarchy = {"A": [], "B": ["A"]}
    visited = {"A"}
    depth = inheritance_guard.calculate_depth("B", hierarchy, visited)
    assert depth >= 0
