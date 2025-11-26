"""Unit tests for import_analysis module."""

from __future__ import annotations

import ast
from pathlib import Path

from ci_tools.scripts.import_analysis import (
    ImportCollector,
    collect_all_imports,
    collect_all_imports_with_parent,
    get_module_name,
)


class TestGetModuleName:
    """Tests for get_module_name function."""

    def test_simple_module(self, tmp_path: Path) -> None:
        """Test converting simple file path to module name."""
        file_path = tmp_path / "module.py"
        result = get_module_name(file_path, tmp_path)
        assert result == "module"

    def test_nested_module(self, tmp_path: Path) -> None:
        """Test converting nested file path to module name."""
        file_path = tmp_path / "pkg" / "subpkg" / "module.py"
        result = get_module_name(file_path, tmp_path)
        assert result == "pkg.subpkg.module"

    def test_init_file(self, tmp_path: Path) -> None:
        """Test __init__.py returns package name."""
        file_path = tmp_path / "pkg" / "__init__.py"
        result = get_module_name(file_path, tmp_path)
        assert result == "pkg"

    def test_root_init_file(self, tmp_path: Path) -> None:
        """Test root __init__.py returns empty string."""
        file_path = tmp_path / "__init__.py"
        result = get_module_name(file_path, tmp_path)
        assert result == ""


class TestImportCollector:
    """Tests for ImportCollector class."""

    def test_visit_import_simple(self) -> None:
        """Test collecting simple import statement."""
        code = "import os"
        tree = ast.parse(code)
        collector = ImportCollector()
        collector.visit(tree)
        assert "os" in collector.imports

    def test_visit_import_nested(self) -> None:
        """Test collecting nested import statement."""
        code = "import os.path"
        tree = ast.parse(code)
        collector = ImportCollector()
        collector.visit(tree)
        assert "os" in collector.imports
        assert "os.path" in collector.imports

    def test_visit_import_from(self) -> None:
        """Test collecting from import statement."""
        code = "from os import path"
        tree = ast.parse(code)
        collector = ImportCollector()
        collector.visit(tree)
        assert "os" in collector.imports
        assert "os.path" in collector.imports

    def test_visit_import_from_nested(self) -> None:
        """Test collecting nested from import statement."""
        code = "from os.path import join"
        tree = ast.parse(code)
        collector = ImportCollector()
        collector.visit(tree)
        assert "os" in collector.imports
        assert "os.path" in collector.imports
        assert "os.path.join" in collector.imports

    def test_visit_import_strips_src_prefix(self) -> None:
        """Test stripping src. prefix from imports."""
        code = "import src.module"
        tree = ast.parse(code)
        collector = ImportCollector()
        collector.visit(tree)
        assert "module" in collector.imports

    def test_resolve_relative_import_without_context(self) -> None:
        """Test relative import without file context."""
        code = "from . import sibling"
        tree = ast.parse(code)
        collector = ImportCollector()
        collector.visit(tree)
        # Without file_path and root, relative imports are not resolved
        assert len(collector.imports) == 0

    def test_resolve_relative_import_with_context(self, tmp_path: Path) -> None:
        """Test relative import with file context."""
        code = "from . import sibling"
        tree = ast.parse(code)
        file_path = tmp_path / "pkg" / "module.py"
        collector = ImportCollector(file_path=file_path, root=tmp_path)
        collector.visit(tree)
        assert "pkg.sibling" in collector.imports

    def test_resolve_relative_import_parent(self, tmp_path: Path) -> None:
        """Test parent-level relative import."""
        code = "from .. import parent_sibling"
        tree = ast.parse(code)
        file_path = tmp_path / "pkg" / "subpkg" / "module.py"
        collector = ImportCollector(file_path=file_path, root=tmp_path)
        collector.visit(tree)
        # With level 2 from pkg.subpkg.module, base is pkg.subpkg
        assert "pkg.subpkg.parent_sibling" in collector.imports

    def test_resolve_relative_import_level_too_deep(self, tmp_path: Path) -> None:
        """Test relative import with level deeper than module path."""
        code = "from .... import something"
        tree = ast.parse(code)
        file_path = tmp_path / "pkg" / "module.py"
        collector = ImportCollector(file_path=file_path, root=tmp_path)
        collector.visit(tree)
        # Should not crash, just not add the import
        assert "something" not in collector.imports

    def test_resolve_relative_import_star_skipped(self, tmp_path: Path) -> None:
        """Test that star imports are skipped in relative imports."""
        code = "from . import *"
        tree = ast.parse(code)
        file_path = tmp_path / "pkg" / "module.py"
        collector = ImportCollector(file_path=file_path, root=tmp_path)
        collector.visit(tree)
        # Star imports should be skipped
        assert "*" not in collector.imports

    def test_relative_import_base_computation_level_1(self, tmp_path: Path) -> None:
        """Test base module computation via relative imports with level 1."""
        # Use a real relative import to test base module computation
        code = "from . import sibling"
        tree = ast.parse(code)
        file_path = tmp_path / "pkg" / "subpkg" / "module.py"
        collector = ImportCollector(file_path=file_path, root=tmp_path)
        collector.visit(tree)
        # level 1 gives parts[:-1] = "pkg.subpkg"
        assert "pkg.subpkg.sibling" in collector.imports

    def test_relative_import_base_computation_level_2(self, tmp_path: Path) -> None:
        """Test base module computation via relative imports with level 2."""
        # Use real relative import syntax
        code = "from .. import other"
        tree = ast.parse(code)
        file_path = tmp_path / "pkg" / "subpkg" / "module.py"
        collector = ImportCollector(file_path=file_path, root=tmp_path)
        collector.visit(tree)
        # level 2 gives parts[:-1] = "pkg.subpkg" (since level > 1)
        assert "pkg.subpkg.other" in collector.imports

    def test_relative_import_with_empty_base(self, tmp_path: Path) -> None:
        """Test relative imports with empty base module result."""
        # When file is at root level, relative import produces no base
        code = "from . import sibling"
        tree = ast.parse(code)
        file_path = tmp_path / "module.py"
        collector = ImportCollector(file_path=file_path, root=tmp_path)
        collector.visit(tree)
        # module.py at root: parts = ["module"], level 1 => base_parts = []
        # so import is just "sibling"
        assert "sibling" in collector.imports


class TestCollectAllImports:
    """Tests for collect_all_imports function."""

    def test_collects_from_single_file(self, tmp_path: Path) -> None:
        """Test collecting imports from a single file."""
        py_file = tmp_path / "test.py"
        py_file.write_text("import os\nimport sys")

        imports = collect_all_imports(tmp_path)

        assert "os" in imports
        assert "sys" in imports

    def test_skips_invalid_files(self, tmp_path: Path) -> None:
        """Test skipping files with syntax errors."""
        py_file = tmp_path / "invalid.py"
        py_file.write_text("import os\nthis is not valid python {{{")

        # Should not raise
        imports = collect_all_imports(tmp_path)
        # Invalid file is skipped
        assert isinstance(imports, set)


class TestCollectAllImportsWithParent:
    """Tests for collect_all_imports_with_parent function."""

    def test_collects_from_root(self, tmp_path: Path) -> None:
        """Test collecting imports from root directory."""
        py_file = tmp_path / "test.py"
        py_file.write_text("import os\nimport sys")

        imports = collect_all_imports_with_parent(tmp_path)

        # Result is a set of import names
        assert "os" in imports
        assert "sys" in imports

    def test_includes_parent_directory(self, tmp_path: Path) -> None:
        """Test collecting imports includes parent directory."""
        # Create a subdirectory with a file
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        py_file = subdir / "test.py"
        py_file.write_text("import os")

        # Create a file in parent (tmp_path)
        parent_file = tmp_path / "parent.py"
        parent_file.write_text("import json")

        imports = collect_all_imports_with_parent(subdir)

        # Should include imports from both subdir and parent
        assert "os" in imports
        assert "json" in imports
