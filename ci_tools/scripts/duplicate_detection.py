"""Detection of suspicious duplicate file patterns."""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ci_tools.scripts.guard_common import iter_python_files

SUSPICIOUS_PATTERNS: Tuple[str, ...] = (
    "_refactored",
    "_slim",
    "_optimized",
    "_old",
    "_backup",
    "_copy",
    "_new",
    "_temp",
    "_v2",
    "_2",
    "_module_references",
)

FALSE_POSITIVE_RULES: Dict[str, Tuple[str, ...]] = {
    "_temp": ("temperature", "max_temp", "cleanup_temp_artifacts"),
    "_2": ("phase_2", "_v2"),
    "_v2": ("migrate_v2", "migration_state_v2"),
    "_backup": ("aws_backup",),
}


def is_false_positive_for_pattern(stem: str, pattern: str) -> bool:
    """Check if a stem matches false positive rules for a specific pattern."""
    if pattern in FALSE_POSITIVE_RULES:
        for marker in FALSE_POSITIVE_RULES[pattern]:
            if marker in stem:
                return True
    return False


def duplicate_reason(stem: str) -> Optional[str]:
    """Check if a stem contains suspicious patterns, accounting for false positives."""
    for pattern in SUSPICIOUS_PATTERNS:
        if pattern in stem:
            if not is_false_positive_for_pattern(stem, pattern):
                return f"Suspicious duplicate pattern '{pattern}' in filename"
    return None


def find_suspicious_duplicates(root: Path) -> List[Tuple[Path, str]]:
    """
    Find files with suspicious naming patterns that suggest duplicates.

    Args:
        root: Root directory to search

    Returns:
        List of (file_path, reason) tuples
    """
    duplicates: List[Tuple[Path, str]] = []

    for py_file in iter_python_files(root):
        reason = duplicate_reason(py_file.stem)
        if reason:
            duplicates.append((py_file, reason))

    return duplicates
