"""Unit tests for unused_module_guard module."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import write_module
from ci_tools.scripts import unused_module_guard
from ci_tools.scripts.duplicate_detection import (
    SUSPICIOUS_PATTERNS,
    duplicate_reason,
    find_suspicious_duplicates,
)
from ci_tools.scripts.import_analysis import (
    ImportCollector,
    collect_all_imports,
    collect_all_imports_with_parent,
    get_module_name,
)
from ci_tools.scripts.import_checking import module_is_imported


def test_import_collector_simple_import():
    """Test ImportCollector with simple import."""
    source = "import foo"
    tree = ast.parse(source)
    collector = ImportCollector()
    collector.visit(tree)

    assert "foo" in collector.imports


def test_import_collector_dotted_import():
    """Test ImportCollector with dotted import."""
    source = "import foo.bar.baz"
    tree = ast.parse(source)
    collector = ImportCollector()
    collector.visit(tree)

    assert "foo" in collector.imports
    assert "foo.bar" in collector.imports
    assert "foo.bar.baz" in collector.imports


def test_import_collector_from_import():
    """Test ImportCollector with from import."""
    source = "from foo.bar import baz"
    tree = ast.parse(source)
    collector = ImportCollector()
    collector.visit(tree)

    assert "foo" in collector.imports
    assert "foo.bar" in collector.imports


def test_import_collector_strips_src_prefix():
    """Test ImportCollector strips src. prefix."""
    source = "import src.foo.bar"
    tree = ast.parse(source)
    collector = ImportCollector()
    collector.visit(tree)

    assert "foo" in collector.imports
    assert "foo.bar" in collector.imports
    assert "src.foo" not in collector.imports


def test_import_collector_from_import_strips_src():
    """Test ImportCollector strips src. prefix from from imports."""
    source = "from src.foo.bar import baz"
    tree = ast.parse(source)
    collector = ImportCollector()
    collector.visit(tree)

    assert "foo" in collector.imports
    assert "foo.bar" in collector.imports


def test_import_collector_multiple_imports():
    """Test ImportCollector with multiple imports."""
    source = textwrap.dedent(
        """
        import foo
        import bar.baz
        from qux import quux
        """
    ).strip()
    tree = ast.parse(source)
    collector = ImportCollector()
    collector.visit(tree)

    assert "foo" in collector.imports
    assert "bar" in collector.imports
    assert "bar.baz" in collector.imports
    assert "qux" in collector.imports


def test_import_collector_from_import_no_module():
    """Test ImportCollector with from import without module."""
    source = "from . import foo"
    tree = ast.parse(source)
    collector = ImportCollector()
    collector.visit(tree)

    # Should not crash
    assert isinstance(collector.imports, set)


def test_collect_all_imports(tmp_path: Path):
    """Test collecting all imports from directory."""
    root = tmp_path / "src"
    root.mkdir()

    write_module(
        root / "module1.py",
        """
        import foo
        from bar import baz
        """,
    )
    write_module(
        root / "module2.py",
        """
        import qux
        """,
    )

    imports = collect_all_imports(root)

    assert "foo" in imports
    assert "bar" in imports
    assert "qux" in imports


def test_collect_all_imports_skips_pycache(tmp_path: Path):
    """Test that __pycache__ is skipped."""
    root = tmp_path / "src"
    pycache = root / "__pycache__"
    root.mkdir()
    pycache.mkdir()

    write_module(root / "module.py", "import foo")
    (pycache / "module.pyc").write_bytes(b"compiled")

    imports = collect_all_imports(root)
    assert "foo" in imports


def test_collect_all_imports_handles_syntax_errors(tmp_path: Path):
    """Test that syntax errors are handled gracefully."""
    root = tmp_path / "src"
    root.mkdir()

    (root / "bad.py").write_text("import foo\nclass Bar:\n    def method(self\n")
    write_module(root / "good.py", "import baz")

    imports = collect_all_imports(root)
    assert "baz" in imports


def test_collect_all_imports_handles_unicode_errors(tmp_path: Path):
    """Test that unicode errors are handled gracefully."""
    root = tmp_path / "src"
    root.mkdir()

    (root / "bad.py").write_bytes(b"\xff\xfe\x00\x00")
    write_module(root / "good.py", "import baz")

    imports = collect_all_imports(root)
    assert "baz" in imports


def test_get_module_name_basic(tmp_path: Path):
    """Test getting module name from file path."""
    root = tmp_path / "src"
    root.mkdir()
    file_path = root / "module.py"

    module_name = get_module_name(file_path, root)
    assert module_name == "module"


def test_get_module_name_nested(tmp_path: Path):
    """Test getting module name from nested path."""
    root = tmp_path / "src"
    file_path = root / "foo" / "bar" / "baz.py"

    module_name = get_module_name(file_path, root)
    assert module_name == "foo.bar.baz"


def test_get_module_name_init(tmp_path: Path):
    """Test getting module name from __init__.py."""
    root = tmp_path / "src"
    file_path = root / "foo" / "__init__.py"

    module_name = get_module_name(file_path, root)
    assert module_name == "foo"


def test_get_module_name_root_init(tmp_path: Path):
    """Test getting module name from root __init__.py."""
    root = tmp_path / "src"
    file_path = root / "__init__.py"

    module_name = get_module_name(file_path, root)
    assert module_name == ""


def test_duplicate_reason_suspicious():
    """Test detecting suspicious duplicate patterns."""
    assert duplicate_reason("module_old") is not None
    assert duplicate_reason("module_backup") is not None
    assert duplicate_reason("module_refactored") is not None
    assert duplicate_reason("module_temp") is not None


def test_duplicate_reason_not_suspicious():
    """Test that normal names are not flagged."""
    assert duplicate_reason("module") is None
    assert duplicate_reason("normal_name") is None


def test_duplicate_reason_false_positive():
    """Test that false positives are not flagged."""
    assert duplicate_reason("max_temp") is None
    assert duplicate_reason("phase_2") is None


def test_find_suspicious_duplicates(tmp_path: Path):
    """Test finding suspicious duplicate files."""
    root = tmp_path / "src"
    root.mkdir()

    (root / "module.py").write_text("# normal")
    (root / "module_old.py").write_text("# old")
    (root / "module_backup.py").write_text("# backup")

    duplicates = find_suspicious_duplicates(root)

    assert len(duplicates) == 2
    paths = [str(d[0].name) for d in duplicates]
    assert "module_old.py" in paths
    assert "module_backup.py" in paths
    assert "module.py" not in paths


def test_find_suspicious_duplicates_skips_pycache(tmp_path: Path):
    """Test that __pycache__ is skipped."""
    root = tmp_path / "src"
    pycache = root / "__pycache__"
    root.mkdir()
    pycache.mkdir()

    (pycache / "module_old.pyc").write_bytes(b"compiled")

    duplicates = find_suspicious_duplicates(root)
    assert len(duplicates) == 0


def test_should_skip_file_pycache():
    """Test that __pycache__ files are skipped."""
    py_file = Path("/project/src/__pycache__/module.py")
    assert unused_module_guard.should_skip_file(py_file, []) is True


def test_should_skip_file_main():
    """Test that __main__.py is skipped."""
    py_file = Path("/project/src/__main__.py")
    assert unused_module_guard.should_skip_file(py_file, []) is True


def test_should_skip_file_exclude_pattern():
    """Test that exclude patterns work."""
    py_file = Path("/project/src/test_module.py")
    assert unused_module_guard.should_skip_file(py_file, ["test_"]) is True


def test_should_skip_file_normal():
    """Test that normal files are not skipped."""
    py_file = Path("/project/src/module.py")
    assert unused_module_guard.should_skip_file(py_file, []) is False


def test_module_is_imported_exact_match():
    """Test module_is_imported with exact match."""
    all_imports = {"foo", "bar.baz"}
    root = Path("src")
    assert module_is_imported("foo", "foo", all_imports, root) is True


def test_module_is_imported_stem_match():
    """Test module_is_imported with stem match."""
    all_imports = {"foo", "bar"}
    root = Path("src")
    assert module_is_imported("bar.baz", "baz", all_imports, root) is True


def test_module_is_imported_partial_match():
    """Test module_is_imported with partial match."""
    all_imports = {"foo.bar"}
    root = Path("src")
    assert module_is_imported("foo.bar.baz", "baz", all_imports, root) is True


def test_module_is_imported_no_match():
    """Test module_is_imported with no match."""
    all_imports = {"foo", "bar"}
    root = Path("src")
    assert module_is_imported("qux.quux", "quux", all_imports, root) is False


def test_module_is_imported_empty_name():
    """Test module_is_imported with empty name."""
    all_imports = {"foo"}
    root = Path("src")
    assert module_is_imported("", "file", all_imports, root) is True


def test_find_unused_modules(tmp_path: Path):
    """Test finding unused modules."""
    root = tmp_path / "src"
    root.mkdir()

    write_module(root / "used.py", "def foo(): pass")
    write_module(root / "unused.py", "def bar(): pass")
    write_module(
        root / "main.py",
        """
        import used
        """,
    )

    unused = unused_module_guard.find_unused_modules(root, exclude_patterns=["__init__.py"])

    # unused.py should be flagged as unused
    assert len(unused) >= 1
    unused_names = [str(u[0].name) for u in unused]
    assert "unused.py" in unused_names


def test_find_unused_modules_excludes_init(tmp_path: Path):
    """Test that __init__.py is excluded."""
    root = tmp_path / "src"
    root.mkdir()

    write_module(root / "__init__.py", "# init")

    unused = unused_module_guard.find_unused_modules(root, exclude_patterns=["__init__.py"])

    # __init__.py should not be in unused list
    unused_names = [str(u[0].name) for u in unused]
    assert "__init__.py" not in unused_names


def test_find_unused_modules_with_parent_imports(tmp_path: Path):
    """Test that imports from parent directory are considered."""
    parent = tmp_path
    root = parent / "src"
    root.mkdir()

    write_module(root / "module.py", "def foo(): pass")
    write_module(parent / "other.py", "from src import module")

    unused = unused_module_guard.find_unused_modules(root, exclude_patterns=["__init__.py"])

    # module.py should not be flagged as unused
    unused_names = [str(u[0].name) for u in unused]
    assert "module.py" not in unused_names


def test_main_no_unused_modules(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function with no unused modules."""
    root = tmp_path / "src"
    root.mkdir()

    write_module(root / "used.py", "def foo(): pass")
    write_module(
        root / "main.py",
        """
        import used
        """,
    )

    with patch("sys.argv", ["unused_module_guard.py", "--root", str(root)]):
        result = unused_module_guard.main()

    assert result == 0
    captured = capsys.readouterr()
    assert "No unused modules found" in captured.out


def test_main_detects_unused_modules(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function detects unused modules."""
    root = tmp_path / "src"
    root.mkdir()

    write_module(root / "unused.py", "def bar(): pass")

    with patch("sys.argv", ["unused_module_guard.py", "--root", str(root)]):
        result = unused_module_guard.main()

    assert result == 1
    captured = capsys.readouterr()
    assert "Unused modules detected" in captured.out
    assert "unused.py" in captured.out


def test_main_detects_suspicious_duplicates(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function detects suspicious duplicates."""
    root = tmp_path / "src"
    root.mkdir()

    write_module(root / "module.py", "def foo(): pass")
    write_module(root / "module_old.py", "def foo_old(): pass")
    write_module(
        root / "main.py",
        """
        import module
        import module_old
        """,
    )

    with patch("sys.argv", ["unused_module_guard.py", "--root", str(root)]):
        result = unused_module_guard.main()

    # Should pass in non-strict mode
    assert result == 0
    captured = capsys.readouterr()
    assert "Suspicious duplicate" in captured.out


def test_main_strict_mode_fails_on_duplicates(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function in strict mode fails on duplicates."""
    root = tmp_path / "src"
    root.mkdir()

    write_module(root / "module.py", "def foo(): pass")
    write_module(root / "module_old.py", "def foo_old(): pass")
    write_module(
        root / "main.py",
        """
        import module
        import module_old
        """,
    )

    with patch("sys.argv", ["unused_module_guard.py", "--root", str(root), "--strict"]):
        result = unused_module_guard.main()

    assert result == 1
    captured = capsys.readouterr()
    assert "Suspicious duplicate" in captured.out


def test_main_root_does_not_exist(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test main function when root does not exist."""
    missing = tmp_path / "missing"

    with patch("sys.argv", ["unused_module_guard.py", "--root", str(missing)]):
        result = unused_module_guard.main()

    assert result == 1
    captured = capsys.readouterr()
    assert "does not exist" in captured.err


def test_main_custom_exclude_patterns(tmp_path: Path):
    """Test main function with custom exclude patterns."""
    root = tmp_path / "src"
    root.mkdir()

    write_module(root / "test_module.py", "def test(): pass")
    write_module(root / "module.py", "def foo(): pass")

    with patch(
        "sys.argv",
        ["unused_module_guard.py", "--root", str(root), "--exclude", "test_", "module.py"],
    ):
        result = unused_module_guard.main()

    # Both should be excluded
    assert result == 0


def test_find_unused_modules_nested_packages(tmp_path: Path):
    """Test finding unused modules in nested packages."""
    root = tmp_path / "src"
    pkg = root / "package"
    root.mkdir()
    pkg.mkdir()

    write_module(pkg / "__init__.py", "")
    write_module(pkg / "used.py", "def foo(): pass")
    write_module(pkg / "unused.py", "def bar(): pass")
    write_module(
        root / "main.py",
        """
        from package import used
        """,
    )

    unused = unused_module_guard.find_unused_modules(root, exclude_patterns=["__init__.py"])

    unused_names = [str(u[0].name) for u in unused]
    assert "unused.py" in unused_names
    assert "used.py" not in unused_names


def test_import_collector_aliased_import():
    """Test ImportCollector with aliased imports."""
    source = "import foo.bar as fb"
    tree = ast.parse(source)
    collector = ImportCollector()
    collector.visit(tree)

    # Should still track the original module name
    assert "foo" in collector.imports
    assert "foo.bar" in collector.imports


def test_suspicious_patterns_coverage():
    """Test coverage of all suspicious patterns."""
    for pattern in SUSPICIOUS_PATTERNS:
        filename = f"module{pattern}"
        reason = duplicate_reason(filename)
        assert reason is not None, f"Pattern {pattern} should be detected"


def test_collect_all_imports_empty_directory(tmp_path: Path):
    """Test collecting imports from empty directory."""
    root = tmp_path / "src"
    root.mkdir()

    imports = collect_all_imports(root)
    assert len(imports) == 0


def test_find_suspicious_duplicates_empty_directory(tmp_path: Path):
    """Test finding duplicates in empty directory."""
    root = tmp_path / "src"
    root.mkdir()

    duplicates = find_suspicious_duplicates(root)
    assert len(duplicates) == 0


def test_main_prints_tip(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test that main prints helpful tip on violations."""
    root = tmp_path / "src"
    root.mkdir()

    write_module(root / "unused.py", "def bar(): pass")

    with patch("sys.argv", ["unused_module_guard.py", "--root", str(root)]):
        result = unused_module_guard.main()

    assert result == 1
    captured = capsys.readouterr()
    assert "Tip:" in captured.out
    assert ".gitignore" in captured.out


def test_collect_all_imports_with_parent(tmp_path: Path):
    """Test _collect_all_imports_with_parent includes parent directory."""
    parent = tmp_path
    root = parent / "src"
    root.mkdir()

    write_module(root / "child.py", "import foo")
    write_module(parent / "parent.py", "import bar")

    imports = collect_all_imports_with_parent(root)

    assert "foo" in imports
    assert "bar" in imports


def test_module_is_imported_with_partial_paths():
    """Test module matching with various partial paths."""
    all_imports = {"foo.bar.baz"}
    root = Path("src")

    # Should match any partial path
    assert module_is_imported("foo.bar.baz.qux", "qux", all_imports, root) is True
    assert module_is_imported("foo.bar", "bar", all_imports, root) is True


def test_load_whitelist_empty(tmp_path: Path):
    """Test loading whitelist when file does not exist."""
    whitelist_path = tmp_path / "nonexistent_whitelist"
    whitelist = unused_module_guard.load_whitelist(whitelist_path)
    assert len(whitelist) == 0


def test_load_whitelist_with_entries(tmp_path: Path):
    """Test loading whitelist with entries."""
    whitelist_path = tmp_path / ".whitelist"
    whitelist_path.write_text("ci_tools/scripts/old_script.py\nci_tools/legacy.py\n# comment\n\n")
    whitelist = unused_module_guard.load_whitelist(whitelist_path)
    assert "ci_tools/scripts/old_script.py" in whitelist
    assert "ci_tools/legacy.py" in whitelist
    assert len(whitelist) == 2


def test_apply_whitelist_filtering(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test applying whitelist filtering to unused modules."""
    root = tmp_path / "src"
    root.mkdir()

    unused = [
        (root / "module1.py", "Never imported"),
        (root / "module2.py", "Never imported"),
        (root / "module3.py", "Never imported"),
    ]

    whitelist_path = tmp_path / ".whitelist"
    whitelist_path.write_text("module1.py\nmodule3.py\n")

    filtered = unused_module_guard.apply_whitelist_filtering(unused, whitelist_path, root)

    assert len(filtered) == 1
    assert filtered[0][0].name == "module2.py"

    captured = capsys.readouterr()
    assert "Filtered 2 whitelisted module(s)" in captured.out


def test_apply_whitelist_filtering_no_whitelist(tmp_path: Path):
    """Test apply_whitelist_filtering when whitelist does not exist."""
    root = tmp_path / "src"
    root.mkdir()

    unused = [(root / "module1.py", "Never imported")]
    whitelist_path = tmp_path / "nonexistent"

    filtered = unused_module_guard.apply_whitelist_filtering(unused, whitelist_path, root)

    assert len(filtered) == 1
    assert filtered[0][0].name == "module1.py"


def test_whitelist_filters_duplicates(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test that whitelist filtering applies to suspicious duplicates."""
    root = tmp_path / "src"
    root.mkdir()

    write_module(root / "module.py", "def foo(): pass")
    write_module(root / "module_backup.py", "def foo_backup(): pass")
    write_module(root / "module_old.py", "def foo_old(): pass")
    write_module(
        root / "main.py",
        """
        import module
        import module_backup
        import module_old
        """,
    )

    whitelist_path = tmp_path / ".whitelist"
    whitelist_path.write_text("module_backup.py\n")

    with patch(
        "sys.argv",
        [
            "unused_module_guard.py",
            "--root",
            str(root),
            "--whitelist",
            str(whitelist_path),
            "--strict",
        ],
    ):
        result = unused_module_guard.main()

    captured = capsys.readouterr()
    assert "module_old.py" in captured.out
    assert "module_backup.py" not in captured.out
    assert "Filtered 1 whitelisted module(s)" in captured.out
    assert result == 1


def test_whitelist_filters_all_duplicates(tmp_path: Path, capsys: pytest.CaptureFixture):
    """Test that whitelist can filter all suspicious duplicates."""
    root = tmp_path / "src"
    root.mkdir()

    write_module(root / "module.py", "def foo(): pass")
    write_module(root / "module_backup.py", "def foo_backup(): pass")
    write_module(
        root / "main.py",
        """
        import module
        import module_backup
        """,
    )

    whitelist_path = tmp_path / ".whitelist"
    whitelist_path.write_text("module_backup.py\n")

    with patch(
        "sys.argv",
        [
            "unused_module_guard.py",
            "--root",
            str(root),
            "--whitelist",
            str(whitelist_path),
            "--strict",
        ],
    ):
        result = unused_module_guard.main()

    captured = capsys.readouterr()
    assert "No unused modules found" in captured.out
    assert "module_backup.py" not in captured.out
    assert "Filtered 1 whitelisted module(s)" in captured.out
    assert result == 0
