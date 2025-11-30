"""AST-based collectors for policy enforcement."""

from __future__ import annotations

import ast
import shutil
from collections import defaultdict
from typing import Dict, Iterable, Iterator, List, Tuple

from ci_tools.scripts.guard_common import relative_path

from .policy_context import (
    ROOT,
    FunctionEntry,
    ModuleContext,
    iter_module_contexts,
    normalize_function,
)
from .policy_visitors import (
    BoolFallbackVisitor,
    BroadExceptVisitor,
    ConditionalLiteralVisitor,
    GenericRaiseVisitor,
    LegacyVisitor,
    LiteralFallbackVisitor,
    SilentHandlerVisitor,
    SyncCallVisitor,
)

# Paths to skip during policy collection
# Tests that verify guards and policy enforcement should be excluded because they
# intentionally contain banned patterns for testing purposes
_POLICY_TEST_SKIP_PREFIXES = ("vendor/", "tests/test_policy")


def _should_skip_test_path(rel_path: str) -> bool:
    """Check if a test path should be skipped from policy checks.

    All test files are skipped because they can legitimately use patterns
    that are disallowed in production code for testing purposes.
    """
    if rel_path.startswith(_POLICY_TEST_SKIP_PREFIXES):
        return True
    if rel_path.startswith("tests/"):
        return True
    return False


def iter_non_init_modules(*args, **kwargs) -> Iterator[ModuleContext]:
    """Iterate over module contexts, filtering out __init__.py files."""
    for ctx in iter_module_contexts(*args, **kwargs):
        if ctx.path.name != "__init__.py":
            yield ctx


def collect_long_functions(threshold: int) -> Iterable[FunctionEntry]:
    """Collect functions that exceed the specified line length threshold."""
    src_root = ROOT / "src"
    for ctx in iter_non_init_modules((src_root,)):
        for node in ast.walk(ctx.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not (node.end_lineno and node.lineno):
                continue
            length = node.end_lineno - node.lineno + 1
            if length > threshold:
                yield FunctionEntry(
                    path=ctx.path,
                    name=node.name,
                    lineno=node.lineno,
                    length=length,
                )


def collect_broad_excepts() -> List[Tuple[str, int]]:
    """Collect exception handlers that catch broad exception types."""
    records: List[Tuple[str, int]] = []
    for ctx in iter_non_init_modules((ROOT / "src",), include_lines=True):
        BroadExceptVisitor(ctx, records).visit(ctx.tree)
    return records


def collect_silent_handlers() -> List[Tuple[str, int, str]]:
    """Collect exception handlers that silently swallow exceptions."""
    records: List[Tuple[str, int, str]] = []
    for ctx in iter_non_init_modules((ROOT / "src",), include_lines=True):
        SilentHandlerVisitor(ctx, records).visit(ctx.tree)
    return records


def collect_generic_raises() -> List[Tuple[str, int]]:
    """Collect raise statements that raise generic exception types."""
    records: List[Tuple[str, int]] = []
    for ctx in iter_non_init_modules((ROOT / "src",)):
        GenericRaiseVisitor(ctx.rel_path, records).visit(ctx.tree)
    return records


def collect_literal_fallbacks() -> List[Tuple[str, int, str]]:
    """Collect function calls that use literal fallback values."""
    records: List[Tuple[str, int, str]] = []
    for ctx in iter_module_contexts():
        if _should_skip_test_path(ctx.rel_path):
            continue
        LiteralFallbackVisitor(ctx.rel_path, records).visit(ctx.tree)
    return records


def collect_bool_fallbacks() -> List[Tuple[str, int]]:
    """Collect boolean 'or' expressions that use literal fallback values."""
    records: List[Tuple[str, int]] = []
    for ctx in iter_module_contexts():
        if _should_skip_test_path(ctx.rel_path):
            continue
        BoolFallbackVisitor(ctx.rel_path, records).visit(ctx.tree)
    return records


def collect_conditional_literal_returns() -> List[Tuple[str, int]]:
    """Collect return statements with literals inside None guards."""
    records: List[Tuple[str, int]] = []
    for ctx in iter_module_contexts():
        if _should_skip_test_path(ctx.rel_path):
            continue
        ConditionalLiteralVisitor(ctx.rel_path, records).visit(ctx.tree)
    return records


def collect_backward_compat_blocks() -> List[Tuple[str, int, str]]:
    """Collect backward compatibility code blocks.

    Note: scripts/ is excluded because policy enforcement code necessarily
    contains the banned keywords in its implementation. Tests that verify policy
    enforcement are excluded because they intentionally test banned patterns.
    """
    records: List[Tuple[str, int, str]] = []
    for ctx in iter_module_contexts(include_source=True):
        if ctx.rel_path.startswith("scripts/") or _should_skip_test_path(ctx.rel_path):
            continue
        LegacyVisitor(ctx, records).visit(ctx.tree)
    return records


def collect_forbidden_sync_calls() -> List[Tuple[str, int, str]]:
    """Collect forbidden synchronous function calls."""
    records: List[Tuple[str, int, str]] = []
    for ctx in iter_module_contexts((ROOT / "src",)):
        SyncCallVisitor(ctx.rel_path, records).visit(ctx.tree)
    return records


# Method names that are commonly interface implementations and should be excluded
# from duplicate detection. These methods are expected to have similar structure
# across different classes implementing the same interface.
INTERFACE_METHOD_NAMES = frozenset(
    {
        "__init__",
        "__post_init__",
        "setup_parser",
        "get_violations_header",
        "get_violations_footer",
    }
)


def _function_entries_from_context(
    ctx: ModuleContext,
    *,
    min_length: int,
) -> Iterator[Tuple[str, FunctionEntry]]:
    """Extract function entries from a module context for duplicate detection."""
    for node in ast.walk(ctx.tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not (node.end_lineno and node.lineno):
            continue
        # Skip common interface method implementations
        if node.name in INTERFACE_METHOD_NAMES:
            continue
        length = node.end_lineno - node.lineno + 1
        if length < min_length:
            continue
        key = normalize_function(node)
        entry = FunctionEntry(
            path=ctx.path,
            name=node.name,
            lineno=node.lineno,
            length=length,
        )
        yield key, entry


def collect_duplicate_functions(min_length: int = 6) -> List[List[FunctionEntry]]:
    """Collect groups of duplicate function implementations."""
    mapping: Dict[str, List[FunctionEntry]] = defaultdict(list)
    for ctx in iter_non_init_modules():
        if _should_skip_test_path(ctx.rel_path):
            continue
        for key, entry in _function_entries_from_context(ctx, min_length=min_length):
            mapping[key].append(entry)

    duplicates: List[List[FunctionEntry]] = []
    for entries in mapping.values():
        unique_paths = {relative_path(entry.path, as_string=True) for entry in entries}
        if len(entries) > 1 and len(unique_paths) > 1:
            duplicates.append(entries)
    return duplicates


def collect_bytecode_artifacts() -> List[str]:
    """Collect bytecode artifacts (.pyc files and __pycache__ directories)."""
    offenders: List[str] = []
    for path in ROOT.rglob("*.pyc"):
        rel_path = relative_path(path, as_string=True)
        assert isinstance(rel_path, str)
        offenders.append(rel_path)
    for path in ROOT.rglob("__pycache__"):
        rel_path = relative_path(path, as_string=True)
        assert isinstance(rel_path, str)
        offenders.append(rel_path)
    return sorted(set(offenders))


def purge_bytecode_artifacts() -> None:
    """Remove bytecode artifacts (.pyc files and __pycache__ directories)."""
    for path in ROOT.rglob("*.pyc"):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
    for path in ROOT.rglob("__pycache__"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)


__all__ = [
    "collect_long_functions",
    "collect_broad_excepts",
    "collect_silent_handlers",
    "collect_generic_raises",
    "collect_literal_fallbacks",
    "collect_bool_fallbacks",
    "collect_conditional_literal_returns",
    "collect_backward_compat_blocks",
    "collect_forbidden_sync_calls",
    "collect_duplicate_functions",
    "collect_bytecode_artifacts",
    "purge_bytecode_artifacts",
]
