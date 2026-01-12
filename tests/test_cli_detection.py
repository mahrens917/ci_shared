"""Unit tests for cli_detection module."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import write_module
from ci_tools.scripts.cli_detection import (
    has_class_main_call,
    has_main_function,
    has_main_guard,
    is_cli_entry_point,
)
from ci_tools.scripts.guard_common import parse_python_ast


def test_has_main_function_present(tmp_path: Path):
    """Test has_main_function detects main() function."""
    py_file = tmp_path / "script.py"
    write_module(
        py_file,
        """
        def main():
            pass
        """,
    )
    tree = parse_python_ast(py_file)
    assert tree is not None
    assert has_main_function(tree)


def test_has_main_function_absent(tmp_path: Path):
    """Test has_main_function returns False when no main()."""
    py_file = tmp_path / "module.py"
    write_module(
        py_file,
        """
        def foo():
            pass
        """,
    )
    tree = parse_python_ast(py_file)
    assert tree is not None
    assert not has_main_function(tree)


def test_has_main_guard_present(tmp_path: Path):
    """Test has_main_guard detects if __name__ == '__main__'."""
    py_file = tmp_path / "script.py"
    write_module(
        py_file,
        """
        if __name__ == '__main__':
            main()
        """,
    )
    tree = parse_python_ast(py_file)
    assert tree is not None
    assert has_main_guard(tree)


def test_has_main_guard_absent(tmp_path: Path):
    """Test has_main_guard returns False when no guard."""
    py_file = tmp_path / "module.py"
    write_module(
        py_file,
        """
        def foo():
            pass
        """,
    )
    tree = parse_python_ast(py_file)
    assert tree is not None
    assert not has_main_guard(tree)


def test_has_class_main_call_with_sys_exit(tmp_path: Path):
    """Test has_class_main_call detects sys.exit(ClassName.main())."""
    py_file = tmp_path / "guard.py"
    write_module(
        py_file,
        """
        import sys

        class MyGuard:
            @staticmethod
            def main():
                return 0

        if __name__ == '__main__':
            sys.exit(MyGuard.main())
        """,
    )
    tree = parse_python_ast(py_file)
    assert tree is not None
    assert has_class_main_call(tree)


def test_has_class_main_call_without_sys_exit(tmp_path: Path):
    """Test has_class_main_call detects direct ClassName.main()."""
    py_file = tmp_path / "guard.py"
    write_module(
        py_file,
        """
        class MyGuard:
            @staticmethod
            def main():
                return 0

        if __name__ == '__main__':
            MyGuard.main()
        """,
    )
    tree = parse_python_ast(py_file)
    assert tree is not None
    assert has_class_main_call(tree)


def test_has_class_main_call_absent(tmp_path: Path):
    """Test has_class_main_call returns False when no class main call."""
    py_file = tmp_path / "module.py"
    write_module(
        py_file,
        """
        if __name__ == '__main__':
            print('hello')
        """,
    )
    tree = parse_python_ast(py_file)
    assert tree is not None
    assert not has_class_main_call(tree)


def test_is_cli_entry_point_module_level_main(tmp_path: Path):
    """Test is_cli_entry_point detects module-level main pattern."""
    py_file = tmp_path / "script.py"
    write_module(
        py_file,
        """
        def main():
            return 0

        if __name__ == '__main__':
            main()
        """,
    )
    assert is_cli_entry_point(py_file)


def test_is_cli_entry_point_class_based_main(tmp_path: Path):
    """Test is_cli_entry_point detects class-based main pattern."""
    py_file = tmp_path / "guard.py"
    write_module(
        py_file,
        """
        import sys

        class Guard:
            @staticmethod
            def main():
                return 0

        if __name__ == '__main__':
            sys.exit(Guard.main())
        """,
    )
    assert is_cli_entry_point(py_file)


def test_is_cli_entry_point_regular_module(tmp_path: Path):
    """Test is_cli_entry_point returns False for regular module."""
    py_file = tmp_path / "module.py"
    write_module(
        py_file,
        """
        def foo():
            pass

        def bar():
            pass
        """,
    )
    assert not is_cli_entry_point(py_file)


def test_is_cli_entry_point_main_without_guard(tmp_path: Path):
    """Test is_cli_entry_point returns False when main() but no guard."""
    py_file = tmp_path / "module.py"
    write_module(
        py_file,
        """
        def main():
            pass
        """,
    )
    assert not is_cli_entry_point(py_file)


def test_is_cli_entry_point_guard_without_main(tmp_path: Path):
    """Test is_cli_entry_point returns False when guard but no main()."""
    py_file = tmp_path / "module.py"
    write_module(
        py_file,
        """
        if __name__ == '__main__':
            print('hello')
        """,
    )
    assert not is_cli_entry_point(py_file)


def test_is_cli_entry_point_invalid_file(tmp_path: Path):
    """Test is_cli_entry_point handles syntax errors gracefully."""
    py_file = tmp_path / "bad.py"
    py_file.write_text('def broken(\n', encoding='utf-8')
    assert not is_cli_entry_point(py_file)
