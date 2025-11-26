"""Fail the build when functions exceed configured line limits."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import List

from ci_tools.scripts.guard_common import (
    GuardRunner,
    count_ast_node_lines,
    iter_ast_nodes,
    parse_python_ast,
    relative_path,
)


class FunctionSizeGuard(GuardRunner):
    """Guard that detects oversized functions."""

    def __init__(self):
        super().__init__(
            name="function_size_guard",
            description="Detect oversized functions that should be refactored.",
            default_root=Path("src"),
        )

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        """Add function-specific arguments."""
        parser.add_argument(
            "--max-function-lines",
            type=int,
            default=80,
            help="Maximum allowed lines per function (default: 80).",
        )

    def _collect_violations(
        self, path: Path, tree: ast.AST, args: argparse.Namespace
    ) -> List[str]:
        """Collect function size violations from AST."""
        violations: List[str] = []
        for node in iter_ast_nodes(tree, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            )  # Type narrowing
            line_count = count_ast_node_lines(node)
            if line_count > args.max_function_lines:
                rel_path = relative_path(path, self.repo_root)
                violations.append(
                    f"{rel_path}::{node.name} (line {node.lineno}) contains {line_count} lines "
                    f"(limit {args.max_function_lines})"
                )
        return violations

    def scan_file(self, path: Path, args: argparse.Namespace) -> List[str]:
        """Scan a file for function size violations."""
        tree = parse_python_ast(path, raise_on_error=False)
        if tree is not None:
            return self._collect_violations(path, tree, args)
        return []

    def get_violations_header(self, args: argparse.Namespace) -> str:
        """Get the header for violations report."""
        return (
            f"Oversized functions detected. Refactor functions to stay within "
            f"{args.max_function_lines} lines:"
        )


if __name__ == "__main__":
    sys.exit(FunctionSizeGuard.main())
