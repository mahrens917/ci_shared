#!/usr/bin/env python3
"""Fail the build when classes have too many methods.

High method counts often indicate Single Responsibility Principle violations where
a class is handling multiple concerns. Consider extracting service objects.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import List, Optional

from ci_tools.scripts.guard_common import (
    GuardRunner,
    count_class_methods,
    iter_ast_nodes,
    parse_python_ast,
    relative_path,
)


class MethodCountGuard(GuardRunner):
    """Guard that detects classes with excessive method counts."""

    def __init__(self):
        super().__init__(
            name="method_count_guard",
            description="Detect classes with excessive method counts (multi-concern indicator).",
            default_root=Path("src"),
        )

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        """Add method-count-specific arguments."""
        parser.add_argument(
            "--max-public-methods",
            type=int,
            default=15,
            help="Maximum allowed public methods per class (default: 15).",
        )
        parser.add_argument(
            "--max-total-methods",
            type=int,
            default=25,
            help="Maximum allowed total methods (public + private) per class (default: 25).",
        )

    def _check_class_methods(
        self, path: Path, node: ast.ClassDef, args: argparse.Namespace
    ) -> Optional[str]:
        """Check class method counts and build violation message if exceeded."""
        pub, tot = count_class_methods(node)
        if pub <= args.max_public_methods and tot <= args.max_total_methods:
            return None
        rel_path = relative_path(path, self.repo_root)
        parts: List[str] = []
        if pub > args.max_public_methods:
            parts.append(f"{pub} public methods (limit {args.max_public_methods})")
        if tot > args.max_total_methods:
            parts.append(f"{tot} total methods (limit {args.max_total_methods})")
        return f"{rel_path}:{node.lineno} class {node.name} has {', '.join(parts)}"

    def scan_file(self, path: Path, args: argparse.Namespace) -> List[str]:
        """Scan a file for method count violations."""
        tree = parse_python_ast(path)
        assert tree is not None  # parse_python_ast raises on error by default
        violations: List[str] = []
        for node in iter_ast_nodes(tree, ast.ClassDef):
            assert isinstance(node, ast.ClassDef)  # Type narrowing for pyright
            if violation := self._check_class_methods(path, node, args):
                violations.append(violation)
        return violations

    def get_violations_header(self, args: argparse.Namespace) -> str:
        """Get the header for violations report."""
        return (
            "Classes with too many methods detected (multi-concern indicator). "
            "Consider extracting service objects or using composition:"
        )

    def get_violations_footer(self, args: argparse.Namespace) -> Optional[str]:
        """Get the footer tip for violations report."""
        return "Tip: Extract groups of related methods into separate service classes"


if __name__ == "__main__":
    sys.exit(MethodCountGuard.main())
