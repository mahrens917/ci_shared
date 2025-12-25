"""Shared constants and AST utilities for policy enforcement."""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Sequence

from ci_tools.scripts.guard_common import (
    detect_repo_root,
    iter_python_files,
    parse_python_ast,
    relative_path,
)

ROOT = detect_repo_root()
SCAN_DIRECTORIES: Sequence[Path] = (ROOT / "src", ROOT / "tests")
BANNED_KEYWORDS = (
    "legacy",
    "fallback",
    "default",
    "catch_all",
    "failover",
    "backup",
    "compat",
    "backwards",
    "deprecated",
    "legacy_mode",
    "old_api",
    "legacy_flag",
)
FLAGGED_TOKENS = ("TODO", "FIXME", "HACK", "WORKAROUND", "LEGACY", "DEPRECATED")
FUNCTION_LENGTH_THRESHOLD = 150
BROAD_EXCEPT_SUPPRESSION = "policy_guard: allow-broad-except"
SILENT_HANDLER_SUPPRESSION = "policy_guard: allow-silent-handler"
IMPORT_OUTSIDE_TOPLEVEL_SUPPRESSION = "policy_guard: allow-import-outside-toplevel"
UNUSED_IMPORT_SUPPRESSION = "policy_guard: allow-unused-import"
IMPORT_NOT_AT_TOP_SUPPRESSION = "policy_guard: allow-import-not-at-top"
SUPPRESSION_PATTERNS: tuple[str, ...] = (
    "# noqa",
    "pylint: disable",
    "policy_guard: allow-silent-handler",
    "policy_guard: allow-broad-except",
    "policy_guard: allow-import-outside-toplevel",
    "policy_guard: allow-unused-import",
    "policy_guard: allow-import-not-at-top",
)
# Tokens that are allowed (exempted from suppression violations)
# Only specific policy_guard tokens are allowed; generic suppressions like # noqa are banned
ALLOWED_SUPPRESSION_TOKENS: tuple[str, ...] = (
    "policy_guard: allow-silent-handler",
    "policy_guard: allow-broad-except",
    "policy_guard: allow-import-outside-toplevel",
    "policy_guard: allow-unused-import",
    "policy_guard: allow-import-not-at-top",
)
FORBIDDEN_SYNC_CALLS: tuple[str, ...] = (
    "time.sleep",
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.delete",
    "requests.request",
)
LEGACY_GUARD_TOKENS: tuple[str, ...] = ("legacy", "compat", "deprecated")
LEGACY_SUFFIXES: tuple[str, ...] = ("_legacy", "_compat", "_deprecated")
LEGACY_CONFIG_TOKENS: tuple[str, ...] = (
    "legacy",
    "compat",
    "deprecated",
    "legacy_mode",
    "old_api",
    "legacy_flag",
)
CONFIG_EXTENSIONS: tuple[str, ...] = (".json", ".toml", ".yaml", ".yml", ".ini")
BROAD_EXCEPTION_NAMES = {"Exception", "BaseException"}


@dataclass(frozen=True)
class FunctionEntry:
    """Represents a function with its location and size metrics."""

    path: Path
    name: str
    lineno: int
    length: int


@dataclass
class ModuleContext:
    """Represents a Python module with its AST and optional source text."""

    path: Path
    rel_path: str
    tree: ast.AST
    source: Optional[str] = None
    lines: Optional[List[str]] = None


class FunctionNormalizer(ast.NodeTransformer):
    """Normalizes function ASTs for structural comparison by replacing names and constants."""

    def visit_Name(self, node: ast.Name) -> ast.AST:  # pragma: no cover - trivial
        """Normalize variable names to 'var'."""
        ctx = node.ctx.__class__()
        new_node = ast.Name(id="var", ctx=ctx)
        return ast.copy_location(new_node, node)

    def visit_arg(self, node: ast.arg) -> ast.AST:  # pragma: no cover - trivial
        """Normalize argument names to 'arg'."""
        annotation = self.visit(node.annotation) if node.annotation else None
        new_node = ast.arg(arg="arg", annotation=annotation)
        return ast.copy_location(new_node, node)

    def visit_Constant(self, node: ast.Constant) -> ast.AST:  # pragma: no cover
        if isinstance(node.value, (int, float, complex, str, bytes, bool)):
            new_node = ast.Constant(value="CONST")
            return ast.copy_location(new_node, node)
        return self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> ast.AST:
        """Normalize exception variable names to 'exc'."""
        if node.type:
            normalized_type = self.visit(node.type)
        else:
            normalized_type = None
        if node.name:
            normalized_name = "exc"
        else:
            normalized_name = None
        new_node = ast.ExceptHandler(
            type=normalized_type,
            name=normalized_name,
            body=[self.visit(stmt) for stmt in node.body],
        )
        return ast.copy_location(new_node, node)


def normalize_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Normalize a function AST for structural comparison."""
    clone = deepcopy(node)
    if (
        clone.body
        and isinstance(clone.body[0], ast.Expr)
        and isinstance(clone.body[0].value, ast.Constant)
        and isinstance(clone.body[0].value.value, str)
    ):
        clone.body = clone.body[1:]
    clone.decorator_list = []
    clone.returns = None
    if hasattr(clone, "args") and clone.args:
        for arg in clone.args.args + clone.args.posonlyargs + clone.args.kwonlyargs:
            arg.annotation = None
        if clone.args.kwarg:
            clone.args.kwarg.annotation = None
        if clone.args.vararg:
            clone.args.vararg.annotation = None
    normalizer = FunctionNormalizer()
    normalizer.visit(clone)
    return ast.dump(clone, annotate_fields=False, include_attributes=False)


def _determine_default_bases() -> Sequence[Path]:
    """Determine the default base directories for scanning."""
    src_dir = ROOT / "src"
    tests_dir = ROOT / "tests"
    if src_dir.exists() or tests_dir.exists():
        return (src_dir, tests_dir)
    return (ROOT,)


def _load_module_text(path: Path, include_source: bool, include_lines: bool) -> tuple[Optional[str], Optional[List[str]]]:
    """Load source text and lines if requested."""
    if not (include_source or include_lines):
        return None, None

    try:
        text = path.read_text()
    except UnicodeDecodeError:
        return None, None

    source = text if include_source else None
    lines = text.splitlines() if include_lines else None
    return source, lines


def iter_module_contexts(
    bases: Sequence[Path] | None = None,
    *,
    include_source: bool = False,
    include_lines: bool = False,
) -> Iterator[ModuleContext]:
    """Iterate over Python modules in the specified bases, yielding ModuleContext objects."""
    if bases is None:
        bases = _determine_default_bases()

    for path in iter_python_files(bases):
        tree = parse_python_ast(path, raise_on_error=False)
        if tree is None:
            continue

        rel_path = str(relative_path(path, ROOT, as_string=True))
        source, lines = _load_module_text(path, include_source, include_lines)

        if source is None and lines is None and (include_source or include_lines):
            continue

        yield ModuleContext(
            path=path,
            rel_path=rel_path,
            tree=tree,
            source=source,
            lines=lines,
        )


def get_call_qualname(node: ast.AST) -> str | None:
    """Extract the qualified name from a Call node (e.g., 'module.function')."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = get_call_qualname(node.value)
        if base is None:
            return None
        return f"{base}.{node.attr}"
    return None


def _sequence_element_has_literal(elt: ast.AST) -> bool:
    if isinstance(elt, ast.Constant):
        return isinstance(elt.value, (int, float, str))
    if isinstance(elt, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
        return contains_literal_dataset(elt)
    return False


def contains_literal_dataset(node: ast.AST) -> bool:
    """Check if a node contains literal data (numbers, strings) in collections."""
    if isinstance(node, ast.Dict):
        return any(contains_literal_dataset(value) for value in node.values)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return any(_sequence_element_has_literal(elt) for elt in node.elts)
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str))


def is_non_none_literal(node: ast.AST | None) -> bool:
    """Check if a node is a non-None literal constant, dict, or list."""
    if isinstance(node, ast.Constant):
        return node.value is not None
    if isinstance(node, (ast.Dict, ast.List, ast.Tuple, ast.Set)):
        return True
    return False


def is_logging_call(node: ast.AST) -> bool:
    """Check if a node is a logging call (e.g., logging.info, logging.error)."""
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        qualname = get_call_qualname(node.value.func)
        if qualname and qualname.startswith("logging."):
            return True
    return False


def handler_has_raise(handler: ast.ExceptHandler) -> bool:
    """Check if an exception handler contains a raise statement."""
    for stmt in handler.body:
        for inner in ast.walk(stmt):
            if isinstance(inner, ast.Raise):
                return True
    return False


def classify_handler(handler: ast.ExceptHandler) -> str | None:
    """Classify an exception handler as potentially problematic or None if acceptable."""
    if handler_has_raise(handler):
        return None
    if not handler.body:
        return "empty exception handler"
    for stmt in handler.body:
        if isinstance(stmt, (ast.Pass, ast.Continue, ast.Break)):
            return f"suppresses exception with {stmt.__class__.__name__.lower()}"
        if isinstance(stmt, ast.Return):
            if stmt.value is None or isinstance(stmt.value, ast.Constant):
                return "suppresses exception with literal return"
        if is_logging_call(stmt):
            return "logs exception without re-raising"
    return "exception handler without re-raise"


def is_literal_none_guard(test: ast.AST) -> bool:
    """Check if a test expression is a None comparison (e.g., 'x is None', 'x == None')."""
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and len(test.comparators) == 1:
        comparator = test.comparators[0]
        if isinstance(comparator, ast.Constant) and comparator.value is None:
            if isinstance(test.ops[0], (ast.Is, ast.Eq)):
                return True
    return False


def handler_contains_suppression(
    handler: ast.ExceptHandler,
    lines: Sequence[str],
    token: str,
) -> bool:
    """Check if an exception handler contains a suppression comment token."""
    if not lines:
        return False
    header_start = max(handler.lineno - 1, 0)
    if handler.body:
        header_end = handler.body[0].lineno - 1
    else:
        header_end = getattr(handler, "end_lineno", handler.lineno)
    header_end = max(header_end, header_start)
    header_end = min(header_end, len(lines) - 1)
    for idx in range(header_start, header_end + 1):
        if token in lines[idx]:
            return True
    return False


__all__ = [
    "ROOT",
    "SCAN_DIRECTORIES",
    "BANNED_KEYWORDS",
    "FLAGGED_TOKENS",
    "FUNCTION_LENGTH_THRESHOLD",
    "BROAD_EXCEPT_SUPPRESSION",
    "SILENT_HANDLER_SUPPRESSION",
    "IMPORT_OUTSIDE_TOPLEVEL_SUPPRESSION",
    "UNUSED_IMPORT_SUPPRESSION",
    "IMPORT_NOT_AT_TOP_SUPPRESSION",
    "SUPPRESSION_PATTERNS",
    "ALLOWED_SUPPRESSION_TOKENS",
    "FORBIDDEN_SYNC_CALLS",
    "LEGACY_GUARD_TOKENS",
    "LEGACY_SUFFIXES",
    "LEGACY_CONFIG_TOKENS",
    "CONFIG_EXTENSIONS",
    "BROAD_EXCEPTION_NAMES",
    "FunctionEntry",
    "ModuleContext",
    "FunctionNormalizer",
    "normalize_function",
    "iter_module_contexts",
    "get_call_qualname",
    "contains_literal_dataset",
    "is_non_none_literal",
    "is_logging_call",
    "handler_has_raise",
    "classify_handler",
    "is_literal_none_guard",
    "handler_contains_suppression",
]
