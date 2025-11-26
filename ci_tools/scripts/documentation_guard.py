#!/usr/bin/env python3
"""Fail the build when required documentation is missing.

Binary decision: Either we have the docs or we don't. No thresholds, no warnings.

Auto-Discovery:
    - Base docs: README.md, CLAUDE.md always required
    - Module docs: Every directory in src/ gets a README.md
    - Architecture docs: docs/architecture/*.md files that exist are validated
    - Domain docs: docs/domains/*/ directories that exist require README.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, List, Tuple


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for documentation guard."""
    parser = argparse.ArgumentParser(
        description="Verify required documentation exists. FAIL on missing required docs."
    )
    parser.add_argument(
        "--root",
        type=Path,
        help="Repository root directory (initial: current directory).",
    )
    parser.set_defaults(root=Path("."))
    return parser.parse_args()


def check_single_directory(
    base_dir: Path,
    readme_path: str | None,
    has_content_check: Callable[[Path], bool] | None,
) -> List[str]:
    """Check if a single directory requires a README."""
    if has_content_check and not has_content_check(base_dir):
        return []
    return [readme_path] if readme_path else []


def should_skip_directory(item: Path) -> bool:
    """Check if a directory should be skipped during scanning."""
    return not item.is_dir() or item.name.startswith("_") or item.name == ".git"


def scan_subdirectories(
    base_dir: Path,
    path_prefix: str,
    has_content_check: Callable[[Path], bool] | None,
) -> List[str]:
    """Scan subdirectories and collect README requirements."""
    required = []
    for item in base_dir.iterdir():
        if should_skip_directory(item):
            continue
        if has_content_check and not has_content_check(item):
            continue
        readme = f"{path_prefix}/{item.name}/README.md"
        required.append(readme)
    return required


def discover_readme_requirements(
    base_dir: Path,
    readme_path: str | None = None,
    path_prefix: str | None = None,
    has_content_check: Callable[[Path], bool] | None = None,
    scan_subdirs: bool = False,
) -> List[str]:
    """Generic helper to discover README.md requirements.

    Args:
        base_dir: Directory to check or scan
        readme_path: Path for single README requirement (used when scan_subdirs=False)
        path_prefix: Prefix for generated paths when scanning subdirs (e.g., "src", "docs/domains")
        has_content_check: Optional callable to check if dir/subdir has relevant content
        scan_subdirs: If True, scan subdirectories; if False, check single directory

    Returns:
        List of required README.md paths
    """
    if not base_dir.exists():
        return []

    if not scan_subdirs:
        return check_single_directory(base_dir, readme_path, has_content_check)

    assert path_prefix is not None  # scan_subdirs requires path_prefix
    return scan_subdirectories(base_dir, path_prefix, has_content_check)


def discover_src_modules(root: Path) -> List[str]:
    """Auto-discover all top-level modules in src/ that need README.md files.

    Returns paths like: src/collect_data/README.md, src/modeling/README.md
    """
    return discover_readme_requirements(
        root / "src",
        path_prefix="src",
        has_content_check=lambda d: len(list(d.rglob("*.py"))) > 0,
        scan_subdirs=True,
    )


def discover_architecture_docs(root: Path) -> List[str]:
    """Auto-discover architecture docs in docs/architecture/.

    If docs/architecture/ exists and has .md files, require docs/architecture/README.md
    """
    return discover_readme_requirements(
        root / "docs" / "architecture",
        readme_path="docs/architecture/README.md",
        has_content_check=lambda d: len(list(d.glob("*.md"))) > 0,
        scan_subdirs=False,
    )


def discover_domain_docs(root: Path) -> List[str]:
    """Auto-discover domain docs in docs/domains/*/.

    Each subdirectory in docs/domains/ needs a README.md
    """
    return discover_readme_requirements(
        root / "docs" / "domains",
        path_prefix="docs/domains",
        scan_subdirs=True,
    )


def discover_operations_docs(root: Path) -> List[str]:
    """Auto-discover operations docs in docs/operations/.

    If docs/operations/ exists, it needs a README.md
    """
    return discover_readme_requirements(
        root / "docs" / "operations",
        readme_path="docs/operations/README.md",
        scan_subdirs=False,
    )


def discover_reference_docs(root: Path) -> List[str]:
    """Auto-discover reference docs in docs/reference/*/.

    Each subdirectory in docs/reference/ needs a README.md
    """
    return discover_readme_requirements(
        root / "docs" / "reference",
        path_prefix="docs/reference",
        scan_subdirs=True,
    )


def get_base_requirements(root: Path) -> List[str]:
    """Base documentation that's always required."""
    required = [
        "README.md",  # Project README always required
    ]

    # CLAUDE.md is required if it exists (once created, must be maintained)
    claude_md = root / "CLAUDE.md"
    if claude_md.exists():
        required.append("CLAUDE.md")

    # docs/README.md is required if docs/ directory exists
    docs_dir = root / "docs"
    if docs_dir.exists():
        required.append("docs/README.md")

    return required


def discover_all_requirements(root: Path) -> Tuple[List[str], dict]:
    """Discover all documentation requirements automatically.

    Returns: (required_docs, discovery_info)
    """
    required = []
    info = {}

    # Base requirements
    base = get_base_requirements(root)
    required.extend(base)
    info["base"] = base

    # Module READMEs
    modules = discover_src_modules(root)
    required.extend(modules)
    info["modules"] = modules

    # Architecture docs
    arch = discover_architecture_docs(root)
    required.extend(arch)
    info["architecture"] = arch

    # Domain docs
    domains = discover_domain_docs(root)
    required.extend(domains)
    info["domains"] = domains

    # Operations docs
    ops = discover_operations_docs(root)
    required.extend(ops)
    info["operations"] = ops

    # Reference docs
    ref = discover_reference_docs(root)
    required.extend(ref)
    info["reference"] = ref

    return required, info


def check_required_docs(root: Path, required: List[str]) -> List[str]:
    """Return list of missing required documentation files."""
    missing: List[str] = []
    for doc_path in required:
        full_path = root / doc_path
        if not full_path.exists():
            missing.append(doc_path)

    return missing


CATEGORY_KEYS = [
    ("Base", "base"),
    ("Modules", "modules"),
    ("Architecture", "architecture"),
    ("Domains", "domains"),
    ("Operations", "operations"),
    ("Reference", "reference"),
]


def group_missing_docs(
    missing: List[str], discovery_info: dict
) -> dict[str, List[str]]:
    """Group missing documentation files by category."""
    grouped: dict[str, List[str]] = {label: [] for label, _ in CATEGORY_KEYS}
    for doc in missing:
        for label, key in CATEGORY_KEYS:
            category_docs = discovery_info.get(key)
            if category_docs is not None and doc in category_docs:
                grouped[label].append(doc)
                break
    return grouped


def print_failure_report(
    grouped: dict[str, List[str]],
) -> None:
    """Print detailed failure report for missing documentation."""
    print("Documentation Guard: FAILED", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("❌ Missing required documentation:", file=sys.stderr)
    print("", file=sys.stderr)
    for category, docs in grouped.items():
        if not docs:
            continue
        print(f"  {category}:", file=sys.stderr)
        for doc in docs:
            print(f"    • {doc}", file=sys.stderr)
        print("", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("Build FAILED: Create the missing documentation files.", file=sys.stderr)
    print("", file=sys.stderr)
    print("Requirements auto-discovered from repository structure:", file=sys.stderr)
    print(
        "  • Base: README.md, CLAUDE.md (if exists), docs/README.md",
        file=sys.stderr,
    )
    print("  • Modules: Every directory in src/ with Python files", file=sys.stderr)
    print(
        "  • Architecture: docs/architecture/README.md (if architecture docs exist)",
        file=sys.stderr,
    )
    print("  • Domains: Every subdirectory in docs/domains/", file=sys.stderr)
    print(
        "  • Operations: docs/operations/README.md (if operations dir exists)",
        file=sys.stderr,
    )
    print("  • Reference: Every subdirectory in docs/reference/", file=sys.stderr)


def print_success(total_docs: int) -> None:
    """Print success message for documentation guard."""
    print("✅ documentation_guard: All required documentation present", file=sys.stderr)
    print(f"   ({total_docs} docs verified)", file=sys.stderr)


def main() -> int:
    """Main entry point for documentation guard."""
    args = parse_args()
    root = args.root.resolve()

    if not root.exists():
        print(f"documentation_guard: root path does not exist: {root}", file=sys.stderr)
        return 1

    # Auto-discover all documentation requirements
    required_docs, discovery_info = discover_all_requirements(root)

    # Check for missing docs
    missing = check_required_docs(root, required_docs)

    if missing:
        grouped = group_missing_docs(missing, discovery_info)
        print_failure_report(grouped)
        return 1

    print_success(len(required_docs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
