"""Fail the build when packages contain too many tiny modules.

Flags packages where >=50% of modules are under 40 significant lines,
catching the "one function per file" anti-pattern that fragments logic
across many small files.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

from ci_tools.scripts.guard_common import (
    GuardRunner,
    iter_python_files,
    relative_path,
)

_DEFAULT_MIN_LINES = 40
_DEFAULT_MAX_TINY_RATIO = 0.5
_DEFAULT_MIN_MODULES = 1


def _count_significant_lines(path: Path) -> int:
    """Count non-blank, non-comment lines in a Python file."""
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            count += 1
    return count


def _collect_package_modules(roots: List[Path]) -> Dict[Path, List[Path]]:
    """Group non-__init__.py modules by their parent directory."""
    packages: Dict[Path, List[Path]] = defaultdict(list)
    for py_file in iter_python_files(roots):
        if py_file.name == "__init__.py":
            continue
        packages[py_file.parent].append(py_file)
    return packages


def _find_fragmented_packages(
    roots: List[Path],
    *,
    min_lines: int,
    max_tiny_ratio: float,
    min_modules: int,
) -> List[Tuple[str, int, int]]:
    """Find packages where too many modules are tiny.

    Returns list of (package_path, total_modules, tiny_count) tuples.
    """
    packages = _collect_package_modules(roots)
    violations: List[Tuple[str, int, int]] = []

    for pkg_dir, modules in sorted(packages.items()):
        total = len(modules)
        if total < min_modules:
            continue
        tiny_count = sum(1 for m in modules if _count_significant_lines(m) < min_lines)
        ratio = tiny_count / total
        if ratio >= max_tiny_ratio:
            violations.append((str(pkg_dir), total, tiny_count))

    return violations


class FragmentationGuard(GuardRunner):
    """Guard that detects over-fragmented packages."""

    def __init__(self):
        super().__init__(
            name="fragmentation_guard",
            description="Detect packages with too many tiny modules.",
            default_root=Path("src"),
        )

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        """Add fragmentation-specific arguments."""
        parser.add_argument(
            "--min-lines",
            type=int,
            default=_DEFAULT_MIN_LINES,
            help="Threshold for 'tiny' module (significant lines).",
        )
        parser.add_argument(
            "--max-tiny-ratio",
            type=float,
            default=_DEFAULT_MAX_TINY_RATIO,
            help="Max fraction of tiny modules allowed per package.",
        )
        parser.add_argument(
            "--min-modules",
            type=int,
            default=_DEFAULT_MIN_MODULES,
            help="Minimum modules in package to check.",
        )

    def scan_file(self, path: Path, args: argparse.Namespace) -> List[str]:
        """Not used — fragmentation checks packages, not individual files."""
        return []

    def run(self, argv=None) -> int:
        """Override run to do package-level analysis instead of file scanning."""
        args = self.parse_args(argv)
        roots = self._resolve_roots(args)
        if roots is None:
            return 1

        violations = _find_fragmented_packages(
            roots,
            min_lines=args.min_lines,
            max_tiny_ratio=args.max_tiny_ratio,
            min_modules=args.min_modules,
        )

        if violations:
            from ci_tools.scripts.guard_common import report_violations

            msgs: List[str] = []
            for pkg_path, total, tiny in violations:
                rel = relative_path(Path(pkg_path), self.repo_root)
                msgs.append(f"{rel} has {tiny}/{total} modules under {args.min_lines} significant lines")

            report_violations(msgs, self.get_violations_header(args))
            if footer := self.get_violations_footer(args):
                print(f"\n{footer}", file=sys.stderr)
            return 1

        return 0

    def get_violations_header(self, args: argparse.Namespace) -> str:
        """Get the header for violations report."""
        pct = int(args.max_tiny_ratio * 100)
        return f"Over-fragmented packages detected (>={pct}% tiny modules):"

    def get_violations_footer(self, args: argparse.Namespace) -> str:
        return "Fix: consolidate small modules into their parent module or a fewer, larger files."


if __name__ == "__main__":
    sys.exit(FragmentationGuard.main())
