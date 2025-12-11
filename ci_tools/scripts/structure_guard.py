"""Fail the build when Python classes exceed configured line limits."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import List

from ci_tools.scripts.guard_common import (
    GuardRunner,
    get_class_line_span,
    parse_python_ast,
    relative_path,
)


class StructureGuard(GuardRunner):
    """Guard that detects oversized Python classes."""

    def __init__(self):
        super().__init__(
            name="structure_guard",
            description="Detect oversized Python classes that need refactoring.",
            default_root=Path("src"),
        )

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        """Add structure-specific arguments."""
        parser.add_argument(
            "--max-class-lines",
            type=int,
            default=100,
            help="Maximum allowed number of lines per class definition.",
        )

    def scan_file(self, path: Path, args: argparse.Namespace) -> List[str]:
        """Scan a file for class size violations."""
        tree = parse_python_ast(path)
        assert tree is not None  # parse_python_ast raises on error by default
        assert isinstance(tree, ast.Module)  # Type narrowing for tree.body access

        source_lines = path.read_text().splitlines()
        violations: List[str] = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                start, end = get_class_line_span(node)
                # Count only non-blank lines (ignore blank lines and comment-only lines)
                non_blank_count = 0
                for line_idx in range(start - 1, end):  # Convert 1-based to 0-based
                    if line_idx < len(source_lines):
                        line = source_lines[line_idx].strip()
                        # Count line if it's not blank and not a comment-only line
                        if line and not line.startswith("#"):
                            non_blank_count += 1

                if non_blank_count > args.max_class_lines:
                    rel_path = relative_path(path, self.repo_root)
                    violations.append(f"{rel_path}:{start} class {node.name} spans {non_blank_count} significant lines " f"(limit {args.max_class_lines})")
        return violations

    def get_violations_header(self, args: argparse.Namespace) -> str:
        """Get the header for violations report."""
        return "Oversized classes detected. Refactor the following definitions " f"to stay within {args.max_class_lines} lines:"


if __name__ == "__main__":
    sys.exit(StructureGuard.main())
