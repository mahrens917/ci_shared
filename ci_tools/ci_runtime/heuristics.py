"""Log analysis helpers for CI failure diagnosis."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from .config import REPO_ROOT
from .process import tail_text

IMPORT_ERROR_PATTERN = re.compile(
    r"ImportError: cannot import name '([^']+)' from '([^']+)'"
)
ATTRIBUTE_ERROR_PATTERN = re.compile(
    r"AttributeError:\s+(?:'[^']+'\s+object\s+has\s+no\s+attribute\s+'([^']+)')"
)


def detect_missing_symbol_error(log_excerpt: str) -> Optional[str]:
    """Return a guidance string when an ImportError indicates a missing symbol."""
    match = IMPORT_ERROR_PATTERN.search(log_excerpt)
    if not match:
        return None
    missing, module = match.groups()
    return (
        f"ImportError detected: missing symbol `{missing}` in module `{module}`.\n"
        "Investigate the import paths or ensure the symbol exists before rerunning ci.py."
    )


def detect_attribute_error(log_excerpt: str) -> Optional[str]:
    """Return a guidance string for AttributeError messages referencing repo files."""
    match = ATTRIBUTE_ERROR_PATTERN.search(log_excerpt)
    if not match:
        return None
    attribute = match.group(1)
    frame_match = re.findall(r'File "([^"]+)", line \d+, in[^\n]+', log_excerpt)
    candidate_file: Optional[Path] = None
    for frame in reversed(frame_match):
        frame_path = Path(frame)
        try:
            resolved = frame_path.resolve()
        except OSError:
            continue
        try:
            relative = resolved.relative_to(REPO_ROOT)
        except ValueError:
            continue
        candidate_file = relative
        break
    if candidate_file is None:
        return None
    return (
        f"AttributeError detected: missing attribute `{attribute}` in `{candidate_file}`.\n"
        "Review the failing attribute manually before retrying ci.py."
    )


def summarize_failure(log_excerpt: str) -> tuple[str, List[str]]:
    """Summarize failing file locations detected in the log excerpt."""
    lines = log_excerpt.splitlines()
    pyright_matches: List[Tuple[str, str]] = []
    for line in lines:
        if "pyright" in line and ":" in line:
            continue
        match = re.search(r"/Users/[^:]+/(.+?):(\d+)", line)
        if match:
            relative_path, lineno = match.groups()
            pyright_matches.append((relative_path, lineno))
    if pyright_matches:
        unique_files: dict[str, str] = {}
        for rel_path, lineno in pyright_matches:
            if rel_path not in unique_files:
                unique_files[rel_path] = lineno
        summary_lines = [
            "pyright reported type errors:",
            *[f"- {path}:{lineno}" for path, lineno in unique_files.items()],
        ]
        return "\n".join(summary_lines), list(unique_files.keys())
    return "", []


__all__ = [
    "detect_missing_symbol_error",
    "detect_attribute_error",
    "summarize_failure",
    "tail_text",
]
