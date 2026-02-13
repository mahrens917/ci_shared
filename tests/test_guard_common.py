"""Unit tests for guard_common module shared utilities."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from ci_tools.scripts.guard_common import (
    GuardRunner,
    count_ast_node_lines,
    count_class_methods,
    create_guard_parser,
    get_class_line_span,
    is_excluded,
    iter_python_files,
    parse_python_ast,
)


class TestIterPythonFiles:
    """Tests for iter_python_files utility function."""

    def test_single_file(self, tmp_path: Path):
        """Test iter_python_files with a single file."""
        py_file = tmp_path / "test.py"
        py_file.write_text("# test")

        files = list(iter_python_files(py_file))
        assert len(files) == 1
        assert files[0] == py_file

    def test_non_python_file(self, tmp_path: Path):
        """Test iter_python_files with a non-Python file."""
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("# test")

        files = list(iter_python_files(txt_file))
        assert len(files) == 0

    def test_directory(self, tmp_path: Path):
        """Test iter_python_files with a directory."""
        (tmp_path / "file1.py").write_text("# file1")
        (tmp_path / "file2.py").write_text("# file2")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "file3.py").write_text("# file3")

        files = list(iter_python_files(tmp_path))
        assert len(files) == 3

    def test_empty_directory(self, tmp_path: Path):
        """Test iter_python_files with empty directory."""
        files = list(iter_python_files(tmp_path))
        assert len(files) == 0


class TestIsExcluded:
    """Tests for is_excluded utility function."""

    def test_basic(self):
        """Test basic exclusion logic."""
        path = Path("/project/src/module.py").resolve()
        exclusions = [Path("/project/src").resolve()]
        assert is_excluded(path, exclusions) is True

    def test_no_match(self):
        """Test exclusion with no match."""
        path = Path("/project/src/module.py").resolve()
        exclusions = [Path("/project/tests").resolve()]
        assert is_excluded(path, exclusions) is False

    def test_handles_attribute_error(self):
        """Test is_excluded handles AttributeError correctly."""
        path = Path("/project/src/module.py")
        exclusions = [Path("/other/path")]
        result = is_excluded(path, exclusions)
        assert result is False

    def test_multiple_exclusions(self):
        """Test exclusion with multiple patterns."""
        path = Path("/project/tests/test_module.py").resolve()
        exclusions = [
            Path("/project/vendor").resolve(),
            Path("/project/tests").resolve(),
        ]
        assert is_excluded(path, exclusions) is True

    def test_partial_match(self):
        """Test exclusion handles partial matches correctly."""
        path = Path("/project/src_other/module.py").resolve()
        exclusions = [Path("/project/src").resolve()]
        result = is_excluded(path, exclusions)
        assert result is False


class TestCountAstNodeLines:
    """Tests for count_ast_node_lines utility function."""

    def test_basic_function(self):
        """Test counting lines for basic function."""
        source = textwrap.dedent(
            """
            def foo():
                x = 1
                return x
            """
        ).strip()
        tree = ast.parse(source)
        func = tree.body[0]
        assert count_ast_node_lines(func) == 3

    def test_no_end_lineno(self):
        """Test count_ast_node_lines with no end_lineno."""
        source = textwrap.dedent(
            """
            def foo():
                pass
            """
        ).strip()
        tree = ast.parse(source)
        func = tree.body[0]
        func.end_lineno = None
        assert count_ast_node_lines(func) == 0


class TestCountClassMethods:  # pylint: disable=too-few-public-methods
    """Tests for count_class_methods utility function."""

    def test_basic_class(self):
        """Test counting methods in basic class."""
        source = textwrap.dedent(
            """
            class Foo:
                def public_method(self):
                    pass
                def _private_method(self):
                    pass
            """
        ).strip()
        tree = ast.parse(source)
        cls = tree.body[0]
        assert isinstance(cls, ast.ClassDef)
        public, total = count_class_methods(cls)
        assert public == 1
        assert total == 2


class TestGetClassLineSpan:  # pylint: disable=too-few-public-methods
    """Tests for get_class_line_span utility function."""

    def test_basic_class(self):
        """Test class_line_span with basic class."""
        source = textwrap.dedent(
            """
            class Foo:
                def method(self):
                    pass
            """
        ).strip()
        tree = ast.parse(source)
        cls = tree.body[0]
        assert isinstance(cls, ast.ClassDef)
        start, end = get_class_line_span(cls)
        assert start == 1
        assert end == 3


class TestParsePythonAst:
    """Tests for parse_python_ast utility function."""

    def test_valid_source(self, tmp_path: Path):
        """Test parsing valid Python source."""
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1")
        tree = parse_python_ast(py_file)
        assert tree is not None
        assert isinstance(tree, ast.Module)
        assert len(tree.body) == 1

    def test_syntax_error(self, tmp_path: Path):
        """Test parsing invalid Python source."""
        py_file = tmp_path / "test.py"
        py_file.write_text("def foo(")
        with pytest.raises(RuntimeError):
            parse_python_ast(py_file)


class TestCreateGuardParserMultiRoot:
    """Tests for multi-root support in create_guard_parser."""

    def test_default_root_is_none(self):
        """Test that --root defaults to None when not specified."""
        parser = create_guard_parser("test guard")
        args = parser.parse_args([])
        assert args.root is None

    def test_single_root(self):
        """Test passing a single --root."""
        parser = create_guard_parser("test guard")
        args = parser.parse_args(["--root", "src"])
        assert args.root == [Path("src")]

    def test_multiple_roots(self):
        """Test passing multiple --root flags."""
        parser = create_guard_parser("test guard")
        args = parser.parse_args(["--root", "src", "--root", "scripts"])
        assert args.root == [Path("src"), Path("scripts")]


class TestGuardRunnerMultiRoot:
    """Tests for multi-root support in GuardRunner."""

    def test_run_scans_multiple_roots(self, tmp_path: Path):
        """Test that GuardRunner.run scans files from multiple roots."""
        src = tmp_path / "src"
        scripts = tmp_path / "scripts"
        src.mkdir()
        scripts.mkdir()
        (src / "a.py").write_text("x = 1\n")
        (scripts / "b.py").write_text("y = 2\n")

        scanned_files: list[Path] = []

        class _Recorder(GuardRunner):
            def __init__(self):
                super().__init__(name="recorder", description="test", default_root=Path("src"))

            def setup_parser(self, parser):
                pass

            def scan_file(self, path, args):
                scanned_files.append(path)
                return []

            def get_violations_header(self, args):
                return ""

        guard = _Recorder()
        result = guard.run(["--root", str(src), "--root", str(scripts)])
        assert result == 0
        basenames = sorted(p.name for p in scanned_files)
        assert basenames == ["a.py", "b.py"]

    def test_run_uses_default_root_when_none_given(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test that GuardRunner falls back to default_root."""
        monkeypatch.chdir(tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.py").write_text("x = 1\n")

        scanned_files: list[Path] = []

        class _Recorder(GuardRunner):
            def __init__(self):
                super().__init__(name="recorder", description="test", default_root=Path("src"))

            def setup_parser(self, parser):
                pass

            def scan_file(self, path, args):
                scanned_files.append(path)
                return []

            def get_violations_header(self, args):
                return ""

        guard = _Recorder()
        result = guard.run([])
        assert result == 0
        assert len(scanned_files) == 1
        assert scanned_files[0].name == "a.py"

    def test_exclude_resolves_relative_to_each_root(self, tmp_path: Path):
        """Test that --exclude resolves relative to each root, not CWD."""
        src = tmp_path / "src"
        scripts = tmp_path / "scripts"
        src.mkdir()
        scripts.mkdir()
        # File to keep in each root
        (src / "keep.py").write_text("x = 1\n")
        (scripts / "keep.py").write_text("y = 2\n")
        # File to exclude — same relative name under both roots
        (src / "generated.py").write_text("z = 3\n")
        (scripts / "generated.py").write_text("w = 4\n")

        scanned_files: list[Path] = []

        class _Recorder(GuardRunner):
            def __init__(self):
                super().__init__(name="recorder", description="test", default_root=Path("src"))

            def setup_parser(self, parser):
                pass

            def scan_file(self, path, args):
                scanned_files.append(path)
                return []

            def get_violations_header(self, args):
                return ""

        guard = _Recorder()
        result = guard.run([
            "--root", str(src),
            "--root", str(scripts),
            "--exclude", "generated.py",
        ])
        assert result == 0
        basenames = sorted(p.name for p in scanned_files)
        assert basenames == ["keep.py", "keep.py"]
