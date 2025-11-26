"""Import analysis utilities for detecting module imports."""

import ast
from pathlib import Path
from typing import Optional, Set

from ci_tools.scripts.guard_common import iter_python_files, parse_python_ast


def get_module_name(file_path: Path, root: Path) -> str:
    """
    Convert file path to Python module name.

    Args:
        file_path: Path to Python file
        root: Root directory

    Returns:
        Module name (e.g., 'common.redis_protocol.store')
    """
    relative = file_path.relative_to(root)
    parts = list(relative.parts)

    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]

    if parts[-1] == "__init__":
        parts = parts[:-1]

    if parts:
        return ".".join(parts)
    return ""


class ImportCollector(ast.NodeVisitor):
    """Collects all import statements from a Python file."""

    def __init__(self, file_path: Optional[Path] = None, root: Optional[Path] = None):
        self.imports: Set[str] = set()
        self.file_path = file_path
        self.root = root

    def visit_Import(self, node: ast.Import) -> None:
        """Handle 'import foo' statements."""
        for alias in node.names:
            module = alias.name
            if module.startswith("src."):
                module = module[4:]
            parts = module.split(".")
            for i in range(len(parts)):
                self.imports.add(".".join(parts[: i + 1]))
        self.generic_visit(node)

    def _compute_base_module(self, parts: list[str], level: int) -> str:
        """Compute base module from parts and import level."""
        if level > 1:
            base_parts = parts[: -level + 1]
        else:
            base_parts = parts[:-1]
        return ".".join(base_parts)

    def _resolve_relative_import(self, node: ast.ImportFrom) -> None:
        """Handle relative imports like 'from . import X'."""
        if not (self.file_path and self.root):
            return

        file_module = get_module_name(self.file_path, self.root)
        if not file_module:
            return

        parts = file_module.split(".")
        if node.level > len(parts):
            return

        base_module = self._compute_base_module(parts, node.level)
        self._add_relative_imports(node.names, base_module)

    def _add_relative_imports(self, names: list, base_module: str) -> None:
        """Add relative imports to the imports set."""
        for alias in names:
            if alias.name == "*":
                continue
            if base_module:
                self.imports.add(f"{base_module}.{alias.name}")
            else:
                self.imports.add(alias.name)

    def _resolve_absolute_import(self, module: str, names: list) -> None:
        """Handle absolute imports like 'from foo import bar'."""
        if module.startswith("src."):
            module = module[4:]

        parts = module.split(".")
        for i in range(len(parts)):
            self.imports.add(".".join(parts[: i + 1]))

        for alias in names:
            if alias.name != "*":
                self.imports.add(f"{module}.{alias.name}")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Handle 'from foo import bar' statements."""
        if node.module is None and node.level > 0:
            self._resolve_relative_import(node)
        elif node.module:
            self._resolve_absolute_import(node.module, node.names)

        self.generic_visit(node)


def collect_all_imports(root: Path) -> Set[str]:
    """
    Collect all imported module names from all Python files.

    Args:
        root: Root directory to search

    Returns:
        Set of all imported module names
    """
    all_imports: Set[str] = set()

    for py_file in iter_python_files(root):
        tree = parse_python_ast(py_file, raise_on_error=False)
        if tree is None:
            continue

        collector = ImportCollector(file_path=py_file, root=root)
        collector.visit(tree)
        all_imports.update(collector.imports)

    return all_imports


def collect_all_imports_with_parent(root: Path) -> Set[str]:
    """Collect all imports from root and parent directory."""
    imports = collect_all_imports(root)
    parent = root.parent
    if parent.exists():
        imports.update(collect_all_imports(parent))
    return imports
