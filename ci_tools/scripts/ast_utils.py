"""AST helper utilities for guard scripts.

These helpers centralize AST-related logic used across the guard suite to
avoid duplication between modules.
"""

from __future__ import annotations

import ast
from typing import Iterable


def count_ast_node_lines(node: ast.AST) -> int:
    """Count lines spanned by an AST node.

    Args:
        node: AST node (function, class, etc.)

    Returns:
        Number of lines spanned by the node, or 0 if end_lineno is not available
    """
    if not hasattr(node, "lineno") or not hasattr(node, "end_lineno"):
        return 0
    lineno = getattr(node, "lineno", None)
    end_lineno = getattr(node, "end_lineno", None)
    if lineno is None or end_lineno is None:
        return 0
    return end_lineno - lineno + 1


def get_class_line_span(node: ast.ClassDef) -> tuple[int, int]:
    """Get the start and end line numbers of a class definition.

    Args:
        node: ClassDef AST node

    Returns:
        Tuple of (start_line, end_line)
    """
    start = node.lineno
    end = node.end_lineno
    if end is None:
        # Walk all child nodes to find maximum line number
        end = start
        for inner in ast.walk(node):
            inner_end = getattr(inner, "end_lineno", None)
            if inner_end is not None and inner_end > end:
                end = inner_end
    return start, end


def count_class_methods(node: ast.ClassDef) -> tuple[int, int]:
    """Count public and total methods in a class.

    Excludes:
        - Dunder methods (__init__, __str__, etc.)
        - Properties (@property decorated methods)

    Args:
        node: ClassDef AST node

    Returns:
        Tuple of (public_method_count, total_method_count)
    """
    public_count = 0
    total_count = 0

    for item in node.body:
        if not isinstance(item, ast.FunctionDef):
            continue

        # Skip dunder methods and name-mangled methods
        if item.name.startswith("__"):
            continue

        # Skip properties (they're data access, not behavior)
        is_property = any(
            isinstance(dec, ast.Name) and dec.id == "property"
            for dec in item.decorator_list
        )
        if is_property:
            continue

        total_count += 1

        # Count public methods (not starting with _)
        if not item.name.startswith("_"):
            public_count += 1

    return public_count, total_count


def iter_ast_nodes(
    tree: ast.AST, node_types: type[ast.AST] | tuple[type[ast.AST], ...]
) -> Iterable[ast.AST]:
    """Iterate over AST nodes of specified types.

    Args:
        tree: AST tree to walk
        node_types: Single node type or tuple of node types to filter for

    Yields:
        AST nodes matching the specified types
    """
    for node in ast.walk(tree):
        if isinstance(node, node_types):
            yield node


def count_significant_lines(tree: ast.AST) -> int:
    """Count significant (non-empty, non-comment) lines in an AST.

    This provides a more accurate module size metric than raw line counts.

    Args:
        tree: Parsed AST tree

    Returns:
        Number of lines that contain executable code or definitions
    """
    # Collect all line numbers that contain AST nodes
    lines_with_code = set()
    for node in ast.walk(tree):
        lineno = getattr(node, "lineno", None)
        if lineno is not None:
            lines_with_code.add(lineno)
        end_lineno = getattr(node, "end_lineno", None)
        if end_lineno is not None and lineno is not None:
            # Add all lines in the span
            for line_num in range(lineno, end_lineno + 1):
                lines_with_code.add(line_num)
    return len(lines_with_code)
