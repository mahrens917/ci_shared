#!/usr/bin/env python3
"""Fail the build when classes instantiate too many dependencies.

High dependency counts in __init__ or __post_init__ indicate orchestrators
handling multiple concerns. Consider dependency injection or extracting coordinators.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import List, Optional

from ci_tools.scripts.guard_common import (
    GuardRunner,
    iter_ast_nodes,
    parse_python_ast,
    relative_path,
)

# Maximum classes to show in error message before truncating
MAX_CLASSES_TO_DISPLAY = 5

SKIPPED_CONSTRUCTOR_NAMES = {
    "Path",
    "Optional",
    "List",
    "Dict",
    "Set",
    "Tuple",
    "Any",
    "Union",
}


def callee_name(node: ast.Call) -> Optional[str]:
    """Extract the name of the called function or method."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def is_constructor_name(name: str) -> bool:
    """Check if a name looks like a constructor."""
    if not name:
        return False
    return name[0].isupper() and name not in SKIPPED_CONSTRUCTOR_NAMES


def count_instantiations(func_node: ast.FunctionDef) -> tuple[int, List[str]]:
    """Count object instantiations (calls that look like constructors)."""
    count = 0
    instantiated_classes: List[str] = []
    for node in iter_ast_nodes(func_node, ast.Call):
        assert isinstance(node, ast.Call)  # Type narrowing for pyright
        callee = callee_name(node)
        if callee and is_constructor_name(callee):
            count += 1
            instantiated_classes.append(callee)
    return count, instantiated_classes


class DependencyGuard(GuardRunner):
    """Guard that detects excessive dependency instantiation."""

    def __init__(self):
        super().__init__(
            name="dependency_guard",
            description="Detect classes with excessive dependency instantiation.",
            default_root=Path("src"),
        )

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        """Add dependency-specific arguments."""
        parser.add_argument(
            "--max-instantiations",
            type=int,
            default=8,
            help="Maximum allowed object instantiations in __init__/__post_init__ (default: 8).",
        )

    def _check_class_init(
        self, node: ast.ClassDef, path: Path, max_inst: int
    ) -> Optional[str]:
        """Check __init__/__post_init__ for excessive instantiations."""
        for item in node.body:
            if not isinstance(item, ast.FunctionDef) or item.name not in (
                "__init__",
                "__post_init__",
            ):
                continue
            count, instantiated = count_instantiations(item)
            if count > max_inst:
                rel_path = relative_path(path, self.repo_root)
                classes_str = ", ".join(instantiated[:MAX_CLASSES_TO_DISPLAY])
                if len(instantiated) > MAX_CLASSES_TO_DISPLAY:
                    remaining = len(instantiated) - MAX_CLASSES_TO_DISPLAY
                    classes_str += f", ... ({remaining} more)"
                return (
                    f"{rel_path}:{node.lineno} class {node.name} instantiates {count} dependencies "
                    f"(limit {max_inst}) - [{classes_str}]"
                )
        return None

    def scan_file(self, path: Path, args: argparse.Namespace) -> List[str]:
        """Scan a file for dependency instantiation violations."""
        tree = parse_python_ast(path)
        assert tree is not None  # parse_python_ast raises on error by default
        violations: List[str] = []
        for node in iter_ast_nodes(tree, ast.ClassDef):
            assert isinstance(node, ast.ClassDef)  # Type narrowing for pyright
            if violation := self._check_class_init(node, path, args.max_instantiations):
                violations.append(violation)
        return violations

    def get_violations_header(self, args: argparse.Namespace) -> str:
        """Get the header for violations report."""
        return (
            "Classes with too many dependency instantiations detected. "
            "Consider dependency injection or extracting coordinators:"
        )

    def get_violations_footer(self, args: argparse.Namespace) -> Optional[str]:
        """Get the footer tip for violations report."""
        return (
            "Tip: Pass dependencies via __init__ parameters "
            "instead of instantiating them internally"
        )


if __name__ == "__main__":
    sys.exit(DependencyGuard.main())
