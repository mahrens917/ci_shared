"""Fail the build when Python modules exceed configured line limits."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

from ci_tools.scripts.guard_common import (
    GuardRunner,
    count_significant_lines,
    parse_python_ast,
    relative_path,
)


class ModuleGuard(GuardRunner):
    """Guard that detects oversized Python modules."""

    def __init__(self):
        super().__init__(
            name="module_guard",
            description="Detect oversized Python modules that need refactoring.",
            default_root=Path("src"),
        )

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        """Add module-specific arguments."""
        parser.add_argument(
            "--max-module-lines",
            type=int,
            default=600,
            help="Maximum allowed number of lines per module (file).",
        )

    def scan_file(self, path: Path, args: argparse.Namespace) -> List[str]:
        """Scan a file for module size violations."""
        try:
            tree = parse_python_ast(path, raise_on_error=True)
        except RuntimeError as exc:
            # Re-raise with consistent error message format
            raise RuntimeError(
                str(exc).replace("failed to parse", "failed to read")
            ) from exc.__cause__

        assert (
            tree is not None
        )  # parse_python_ast raises on error when raise_on_error=True
        line_count = count_significant_lines(tree)
        if line_count > args.max_module_lines:
            rel_path = relative_path(path, self.repo_root)
            return [
                f"{rel_path} contains {line_count} lines "
                f"(limit {args.max_module_lines})"
            ]
        return []

    def get_violations_header(self, args: argparse.Namespace) -> str:
        """Get the header for violations report."""
        return (
            "Oversized modules detected. Refactor the following files "
            f"to stay within {args.max_module_lines} lines:"
        )


if __name__ == "__main__":
    sys.exit(ModuleGuard.main())
