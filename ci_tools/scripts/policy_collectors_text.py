"""File-based policy collectors (token scanning, legacy config detection)."""

from __future__ import annotations

import io
import tokenize
from typing import Dict, List, Set, Tuple

from ci_tools.scripts.guard_common import relative_path

from .policy_context import (
    BANNED_KEYWORDS,
    CONFIG_EXTENSIONS,
    FLAGGED_TOKENS,
    LEGACY_CONFIG_TOKENS,
    LEGACY_SUFFIXES,
    ROOT,
    SUPPRESSION_PATTERNS,
    iter_module_contexts,
)

# Paths to skip during policy collection
_SKIP_PATH_PREFIXES = ("scripts/", "ci_runtime/", "vendor/")

# Pre-computed legacy patterns (forbidden suffixes, directory parts, and prefixes)
_FORBIDDEN_SUFFIXES = tuple(f"{suffix}.py" for suffix in LEGACY_SUFFIXES)
_DIR_TOKENS = tuple(token.strip("_") for token in LEGACY_SUFFIXES)
_FORBIDDEN_PARTS = tuple(f"/{token}/" for token in _DIR_TOKENS) + tuple(
    f"\\{token}\\" for token in _DIR_TOKENS
)
_FORBIDDEN_PREFIXES = tuple(f"{token}/" for token in _DIR_TOKENS) + tuple(
    f"{token}\\" for token in _DIR_TOKENS
)
_LEGACY_PATTERNS = (_FORBIDDEN_SUFFIXES, _FORBIDDEN_PARTS, _FORBIDDEN_PREFIXES)


def _should_skip_path(rel_path: str) -> bool:
    """Check if a path should be skipped during policy collection."""
    return rel_path.startswith(_SKIP_PATH_PREFIXES)


def _keyword_token_lines(
    source: str,
    keyword_lookup: Dict[str, str],
) -> Dict[str, Set[int]]:
    hits: Dict[str, Set[int]] = {}
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    except tokenize.TokenError:
        return {}
    for token in tokens:
        if token.type != tokenize.NAME:
            continue
        keyword = keyword_lookup.get(token.string.lower())
        if keyword:
            hits.setdefault(keyword, set()).add(token.start[0])
    return hits


def scan_keywords() -> Dict[str, Dict[str, List[int]]]:
    """Scan for banned keywords in source files."""
    found: Dict[str, Dict[str, List[int]]] = {kw: {} for kw in BANNED_KEYWORDS}
    keyword_lookup = {kw.lower(): kw for kw in BANNED_KEYWORDS}

    for ctx in iter_module_contexts(include_source=True):
        if _should_skip_path(ctx.rel_path):
            continue
        if ctx.source:
            source = ctx.source
        else:
            source = ""
        keyword_hits = _keyword_token_lines(source, keyword_lookup)
        for keyword, lines in keyword_hits.items():
            if lines:
                if keyword not in found:
                    found[keyword] = {}
                found[keyword][ctx.rel_path] = sorted(lines)
    return found


def _collect_line_tokens(token_list: List[str]) -> List[Tuple[str, int, str]]:
    """Helper to collect tokens found in source lines.

    Args:
        token_list: List of tokens to search for in each line

    Returns:
        List of (rel_path, lineno, token) tuples for each match
    """
    records: List[Tuple[str, int, str]] = []
    for ctx in iter_module_contexts(include_source=True):
        if ctx.source is None:
            continue
        if _should_skip_path(ctx.rel_path):
            continue
        for lineno, line in enumerate(ctx.source.splitlines(), start=1):
            for token in token_list:
                if token in line:
                    records.append((ctx.rel_path, lineno, token))
    return records


def collect_flagged_tokens() -> List[Tuple[str, int, str]]:
    """Collect flagged comment tokens (TODO, FIXME, HACK, etc.)."""
    return _collect_line_tokens(list(FLAGGED_TOKENS))


def collect_suppressions() -> List[Tuple[str, int, str]]:
    """Collect suppression comment patterns (# noqa, pylint: disable)."""
    return _collect_line_tokens(list(SUPPRESSION_PATTERNS))


def _has_legacy_pattern(
    lowered_path: str,
    patterns: Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]],
) -> bool:
    """Check if a lowercased path contains any legacy patterns."""
    forbidden_suffixes, forbidden_parts, forbidden_prefixes = patterns
    if any(suffix in lowered_path for suffix in forbidden_suffixes):
        return True
    if any(part in lowered_path for part in forbidden_parts):
        return True
    if any(lowered_path.startswith(prefix) for prefix in forbidden_prefixes):
        return True
    return False


def collect_legacy_modules() -> List[Tuple[str, int, str]]:
    """Collect legacy/deprecated module patterns."""
    records: List[Tuple[str, int, str]] = []
    for ctx in iter_module_contexts():
        if _should_skip_path(ctx.rel_path):
            continue
        lowered = ctx.rel_path.lower()
        if _has_legacy_pattern(lowered, _LEGACY_PATTERNS):
            records.append((ctx.rel_path, 1, "legacy module path"))
    return records


def collect_legacy_configs() -> List[Tuple[str, int, str]]:
    """Collect legacy configuration patterns."""
    records: List[Tuple[str, int, str]] = []
    config_root = ROOT / "config"
    if not config_root.exists():
        return records
    for path in config_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in CONFIG_EXTENSIONS:
            continue
        try:
            lines = path.read_text().splitlines()
        except UnicodeDecodeError:
            continue
        rel_path_str = str(relative_path(path, as_string=True))
        for lineno, line in enumerate(lines, start=1):
            lower = line.lower()
            if any(token in lower for token in LEGACY_CONFIG_TOKENS):
                records.append((rel_path_str, lineno, "legacy toggle in config"))
    return records


__all__ = [
    "scan_keywords",
    "collect_flagged_tokens",
    "collect_suppressions",
    "collect_legacy_modules",
    "collect_legacy_configs",
]
