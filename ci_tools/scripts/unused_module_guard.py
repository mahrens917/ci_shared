#!/usr/bin/env python3
"""
Unused Module Guard - Detects Python modules that are never imported.

This guard identifies:
1. Python files that are never imported anywhere in the codebase
2. Suspicious duplicate files (_refactored, _slim, _old, etc.)
3. Test files without corresponding source files

Whitelist support:
- Use --whitelist to specify a file with known false positives
- Default whitelist location: .unused_module_guard_whitelist
- Format: One relative path per line, # for comments

Usage:
    python -m ci_tools.scripts.unused_module_guard --root src [--strict] [--whitelist PATH]

Exit codes:
    0: No unused modules found
    1: Unused modules detected
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Set, Tuple

from ci_tools.scripts.cli_detection import is_cli_entry_point
from ci_tools.scripts.duplicate_detection import find_suspicious_duplicates
from ci_tools.scripts.guard_common import iter_python_files
from ci_tools.scripts.import_analysis import (
    collect_all_imports_with_parent,
    get_module_name,
)
from ci_tools.scripts.import_checking import module_is_imported


def should_skip_file(py_file: Path, exclude_patterns: List[str]) -> bool:
    """Check if a file should be skipped during unused module detection."""
    if "__pycache__" in str(py_file):
        return True
    if any(pattern in str(py_file) for pattern in exclude_patterns):
        return True
    if py_file.name in ("__main__.py", "main.py"):
        return True
    return is_cli_entry_point(py_file)


def find_unused_modules(
    root: Path, exclude_patterns: Optional[List[str]] = None
) -> List[Tuple[Path, str]]:
    """
    Find Python modules that are never imported.

    Args:
        root: Root directory to search
        exclude_patterns: Patterns to exclude (e.g., ['__init__.py', 'test_'])

    Returns:
        List of (file_path, reason) tuples for unused modules
    """
    if exclude_patterns is None:
        exclude_patterns = []
    exclude_patterns = list(exclude_patterns)
    all_imports = collect_all_imports_with_parent(root)
    unused: List[Tuple[Path, str]] = []

    for py_file in iter_python_files(root):
        if should_skip_file(py_file, exclude_patterns):
            continue

        module_name = get_module_name(py_file, root)
        if module_is_imported(module_name, py_file.stem, all_imports, root):
            continue

        unused.append((py_file, f"Never imported (module: {module_name})"))

    return unused


def load_whitelist(whitelist_path: Path) -> Set[str]:
    """
    Load module paths from whitelist file.

    Args:
        whitelist_path: Path to whitelist file

    Returns:
        Set of module paths to ignore (relative to root)
    """
    if not whitelist_path.exists():
        return set()

    whitelist: Set[str] = set()
    try:
        with open(whitelist_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or not line:
                    continue
                whitelist.add(line)
    except (IOError, OSError) as exc:
        print(f"Warning: Could not read whitelist file: {exc}", file=sys.stderr)

    return whitelist


def apply_whitelist_filtering(
    unused: List[Tuple[Path, str]], whitelist_path: Path, root: Path
) -> List[Tuple[Path, str]]:
    """Apply whitelist filtering to unused modules and return filtered list."""
    whitelist = load_whitelist(whitelist_path)
    if not whitelist:
        return unused

    original_count = len(unused)
    filtered = [
        (file_path, reason)
        for file_path, reason in unused
        if str(file_path.relative_to(root)) not in whitelist
    ]
    filtered_count = original_count - len(filtered)
    if filtered_count > 0:
        print(f"(Filtered {filtered_count} whitelisted module(s))")
    return filtered


def report_results(
    unused: List[Tuple[Path, str]],
    duplicates: List[Tuple[Path, str]],
    root: Path,
    strict: bool,
) -> bool:
    """Report unused modules and duplicates. Returns True if issues were found."""
    issues_found = False

    if unused:
        print("\n❌ Unused modules detected (never imported):")
        for file_path, reason in sorted(unused):
            print(f"  - {file_path.relative_to(root)}: {reason}")
        issues_found = True

    if duplicates:
        print("\n⚠️  Suspicious duplicate files detected:")
        for file_path, reason in sorted(duplicates):
            print(f"  - {file_path.relative_to(root)}: {reason}")
        if strict:
            issues_found = True

    if not issues_found:
        print("✅ No unused modules found")
    else:
        print(
            "\nTip: Remove unused files or add them to .gitignore if they're work-in-progress"
        )

    return issues_found


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Detect unused Python modules",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root",
        type=Path,
        help="Root directory to check (initial: src)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Enable strict mode (fail on suspicious duplicates too)",
    )
    parser.add_argument(
        "--exclude",
        nargs="+",
        help="Patterns to exclude from unused checks",
    )
    parser.add_argument(
        "--whitelist",
        type=Path,
        help=(
            "Path to whitelist file with known false positives "
            "(default: .unused_module_guard_whitelist)"
        ),
        default=Path(".unused_module_guard_whitelist"),
    )
    parser.set_defaults(root=Path("src"), exclude=["__init__.py", "conftest.py"])

    args = parser.parse_args()

    if not args.root.exists():
        print(f"Error: Root directory '{args.root}' does not exist", file=sys.stderr)
        return 1

    print(f"Checking for unused modules in {args.root}...")

    unused = find_unused_modules(args.root, args.exclude)
    unused = apply_whitelist_filtering(unused, args.whitelist, args.root)
    duplicates = find_suspicious_duplicates(args.root)
    duplicates = apply_whitelist_filtering(duplicates, args.whitelist, args.root)
    issues_found = report_results(unused, duplicates, args.root, args.strict)

    if issues_found:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
