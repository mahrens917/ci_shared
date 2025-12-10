"""AST visitor classes for policy enforcement."""

from __future__ import annotations

import ast
from typing import List, Tuple

from .policy_context import (
    BROAD_EXCEPT_SUPPRESSION,
    BROAD_EXCEPTION_NAMES,
    FORBIDDEN_SYNC_CALLS,
    LEGACY_GUARD_TOKENS,
    LEGACY_SUFFIXES,
    SILENT_HANDLER_SUPPRESSION,
    ModuleContext,
    classify_handler,
    get_call_qualname,
    handler_contains_suppression,
    is_literal_none_guard,
    is_non_none_literal,
)

MIN_GETATTR_ARGS_WITH_DEFAULT = 3
MIN_SETDEFAULT_ARGS = 2


def _safe_get_qualname(func: ast.AST) -> str:
    """Get call qualname, returning empty string if None."""
    result = get_call_qualname(func)
    if result:
        return result
    return ""


def _resolve_default_argument(
    call: ast.Call,
    *,
    positional_index: int,
    keyword_names: set[str],
) -> ast.AST | None:
    """Resolve a function call argument from positional or keyword form."""
    if len(call.args) > positional_index:
        return call.args[positional_index]
    for keyword in call.keywords:
        if keyword.arg in keyword_names:
            return keyword.value
    return None


def _handler_is_suppressed(
    handler: ast.ExceptHandler,
    ctx: ModuleContext,
    suppression_token: str,
) -> bool:
    """Check if an exception handler contains a suppression comment."""
    if ctx.lines:
        lines = ctx.lines
    else:
        lines = []
    return handler_contains_suppression(handler, lines, suppression_token)


def _handler_catches_broad(handler: ast.ExceptHandler) -> bool:
    """Check if an exception handler catches broad exception types."""
    if not handler.type:
        # Bare except clause catches all exceptions
        return True
    if isinstance(handler.type, ast.Name):
        return handler.type.id in BROAD_EXCEPTION_NAMES
    if isinstance(handler.type, ast.Tuple):
        return any(isinstance(elt, ast.Name) and elt.id in BROAD_EXCEPTION_NAMES for elt in handler.type.elts)
    return False


class BroadExceptVisitor(ast.NodeVisitor):
    """AST visitor to detect broad exception handlers."""

    def __init__(self, ctx: ModuleContext, records: List[Tuple[str, int]]) -> None:
        self.ctx = ctx
        self.records = records

    def visit_Try(self, node: ast.Try) -> None:
        """Check Try nodes for broad exception handlers."""
        for handler in node.handlers:
            if not _handler_catches_broad(handler):
                continue
            if _handler_is_suppressed(handler, self.ctx, BROAD_EXCEPT_SUPPRESSION):
                continue
            self.records.append((self.ctx.rel_path, handler.lineno))
        self.generic_visit(node)


class SilentHandlerVisitor(ast.NodeVisitor):
    """AST visitor to detect silent exception handlers."""

    def __init__(self, ctx: ModuleContext, records: List[Tuple[str, int, str]]) -> None:
        self.ctx = ctx
        self.records = records

    def visit_Try(self, node: ast.Try) -> None:
        """Check Try nodes for silent exception handlers."""
        for handler in node.handlers:
            reason = classify_handler(handler)
            if reason is None:
                continue
            if _handler_is_suppressed(handler, self.ctx, SILENT_HANDLER_SUPPRESSION):
                continue
            self.records.append((self.ctx.rel_path, handler.lineno, reason))
        self.generic_visit(node)


class GenericRaiseVisitor(ast.NodeVisitor):
    """AST visitor to detect generic exception raises."""

    def __init__(self, rel_path: str, records: List[Tuple[str, int]]) -> None:
        self.rel_path = rel_path
        self.records = records

    def visit_Raise(self, node: ast.Raise) -> None:
        """Check Raise nodes for generic exception types."""
        exc = node.exc
        if exc is None:
            return
        if isinstance(exc, ast.Name) and exc.id in BROAD_EXCEPTION_NAMES:
            self.records.append((self.rel_path, node.lineno))
        elif isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name) and exc.func.id in BROAD_EXCEPTION_NAMES:
            self.records.append((self.rel_path, node.lineno))
        self.generic_visit(node)


class LiteralFallbackVisitor(ast.NodeVisitor):
    """AST visitor to detect literal fallback values in function calls."""

    def __init__(self, rel_path: str, records: List[Tuple[str, int, str]]) -> None:
        self.rel_path = rel_path
        self.records = records

    def visit_Call(self, node: ast.Call) -> None:
        """Check Call nodes for literal fallbacks."""
        self._check_get_method(node)
        self._check_getattr(node)
        self._check_os_getenv(node)
        self._check_setdefault(node)
        self.generic_visit(node)

    def _check_get_method(self, node: ast.Call) -> None:
        """Check for literal fallback in .get() method calls."""
        qualname = _safe_get_qualname(node.func)
        if not qualname.endswith(".get"):
            return
        default_arg = _resolve_default_argument(node, positional_index=1, keyword_names={"default", "fallback"})
        self._maybe_record(node, default_arg, f"{qualname} literal fallback")

    def _check_getattr(self, node: ast.Call) -> None:
        qualname = _safe_get_qualname(node.func)
        if qualname == "getattr" and len(node.args) >= MIN_GETATTR_ARGS_WITH_DEFAULT:
            self._maybe_record(node, node.args[2], "getattr literal fallback")

    def _check_os_getenv(self, node: ast.Call) -> None:
        qualname = _safe_get_qualname(node.func)
        if qualname not in {"os.getenv", "os.environ.get"}:
            return
        default_arg = _resolve_default_argument(node, positional_index=1, keyword_names={"default"})
        self._maybe_record(node, default_arg, f"{qualname} literal fallback")

    def _check_setdefault(self, node: ast.Call) -> None:
        qualname = _safe_get_qualname(node.func)
        if qualname.endswith(".setdefault") and len(node.args) >= MIN_SETDEFAULT_ARGS:
            self._maybe_record(node, node.args[1], f"{qualname} literal fallback")

    def _maybe_record(self, node: ast.Call, default_arg: ast.AST | None, message: str) -> None:
        """Record a violation if the default argument is a non-None literal."""
        if is_non_none_literal(default_arg):
            self.records.append((self.rel_path, node.lineno, message))


class BoolFallbackVisitor(ast.NodeVisitor):
    """AST visitor to detect boolean fallback patterns."""

    def __init__(self, rel_path: str, records: List[Tuple[str, int]]) -> None:
        self.rel_path = rel_path
        self.records = records

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        """Check BoolOp nodes for literal fallbacks in 'or' expressions."""
        if isinstance(node.op, ast.Or):
            if any(is_non_none_literal(value) for value in node.values[1:]):
                self.records.append((self.rel_path, node.lineno))
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        """Check IfExp nodes for literal values in ternary expressions."""
        if is_non_none_literal(node.body) or is_non_none_literal(node.orelse):
            self.records.append((self.rel_path, node.lineno))
        self.generic_visit(node)


class ConditionalLiteralVisitor(ast.NodeVisitor):
    """AST visitor to detect literal returns inside None guards."""

    def __init__(self, rel_path: str, records: List[Tuple[str, int]]) -> None:
        self.rel_path = rel_path
        self.records = records

    def visit_If(self, node: ast.If) -> None:
        """Check If nodes for literal returns in None guards."""
        if is_literal_none_guard(node.test):
            for stmt in node.body:
                if isinstance(stmt, ast.Return) and is_non_none_literal(stmt.value):
                    self.records.append((self.rel_path, stmt.lineno))
        self.generic_visit(node)


class LegacyVisitor(ast.NodeVisitor):
    """AST visitor to detect legacy/backward compatibility code patterns."""

    def __init__(self, ctx: ModuleContext, records: List[Tuple[str, int, str]]) -> None:
        self.ctx = ctx
        self.records = records

    def visit_If(self, node: ast.If) -> None:
        """Check If nodes for legacy conditional guards."""
        if self.ctx.source is None:
            return
        source_segment = ast.get_source_segment(self.ctx.source, node)
        if source_segment:
            segment = source_segment
        else:
            segment = ""
        lowered = segment.lower()
        if any(token in lowered for token in LEGACY_GUARD_TOKENS):
            self.records.append((self.ctx.rel_path, node.lineno, "conditional legacy guard"))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Check Attribute nodes for legacy suffixes."""
        attr_name = node.attr.lower()
        if attr_name.endswith(LEGACY_SUFFIXES):
            self.records.append((self.ctx.rel_path, node.lineno, "legacy attribute access"))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """Check Name nodes for legacy symbols."""
        name_id = node.id.lower()
        if name_id.endswith(LEGACY_SUFFIXES):
            self.records.append(
                (
                    self.ctx.rel_path,
                    node.lineno,
                    "legacy symbol reference",
                )
            )


class SyncCallVisitor(ast.NodeVisitor):
    """AST visitor to detect forbidden synchronous calls."""

    def __init__(self, rel_path: str, records: List[Tuple[str, int, str]]) -> None:
        self.rel_path = rel_path
        self.records = records

    def visit_Call(self, node: ast.Call) -> None:
        """Check Call nodes for forbidden synchronous calls."""
        qualname = get_call_qualname(node.func)
        if not qualname:
            self.generic_visit(node)
            return
        for pattern in FORBIDDEN_SYNC_CALLS:
            if qualname == pattern or qualname.startswith(f"{pattern}."):
                self.records.append(
                    (
                        self.rel_path,
                        node.lineno,
                        f"forbidden synchronous call '{qualname}'",
                    )
                )
                break
        self.generic_visit(node)
