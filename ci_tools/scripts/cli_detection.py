#!/usr/bin/env python3
"""
CLI Entry Point Detection - Identifies Python CLI entry points.

This module detects files meant to be executed directly (e.g., python -m module)
rather than imported. It recognizes two common patterns:

1. Module-level main() with if __name__ == "__main__"
2. Class-based ClassName.main() called from if __name__ == "__main__"
"""

import ast
from pathlib import Path

from ci_tools.scripts.guard_common import iter_ast_nodes, parse_python_ast


def is_main_guard_node(node: ast.If) -> bool:
    """Check if an If node is a '__name__ == "__main__"' guard."""
    if not isinstance(node.test, ast.Compare):
        return False
    if not isinstance(node.test.left, ast.Name):
        return False
    if node.test.left.id != "__name__":
        return False
    if len(node.test.comparators) != 1:
        return False
    comparator = node.test.comparators[0]
    return isinstance(comparator, ast.Constant) and comparator.value == "__main__"


def has_main_function(tree: ast.AST) -> bool:
    """Check if AST contains a main() function definition."""
    return any(isinstance(node, ast.FunctionDef) and node.name == "main" for node in iter_ast_nodes(tree, ast.FunctionDef))


def has_main_guard(tree: ast.AST) -> bool:
    """Check if AST contains if __name__ == '__main__' pattern."""
    return any(isinstance(node, ast.If) and is_main_guard_node(node) for node in iter_ast_nodes(tree, ast.If))


def calls_class_main(node: ast.AST) -> bool:
    """
    Check if a node calls ClassName.main() pattern.

    Handles both direct calls and sys.exit(ClassName.main()) patterns.
    """
    if isinstance(node, ast.Expr):
        node = node.value

    if not isinstance(node, ast.Call):
        return False

    # Check for sys.exit(ClassName.main()) pattern
    if isinstance(node.func, ast.Attribute) and node.func.attr == "exit":
        if node.args and isinstance(node.args[0], ast.Call):
            call_node = node.args[0]
            if isinstance(call_node.func, ast.Attribute):
                return call_node.func.attr == "main"

    # Check for direct ClassName.main() pattern
    if isinstance(node.func, ast.Attribute):
        return node.func.attr == "main"

    return False


def has_class_main_call(tree: ast.AST) -> bool:
    """
    Check if __main__ guard calls ClassName.main().

    Returns True if the file has a __main__ guard that calls a class method main().
    """
    for node in iter_ast_nodes(tree, ast.If):
        if not isinstance(node, ast.If):
            continue
        if not is_main_guard_node(node):
            continue
        # Check if any statement in the if body calls *.main()
        for stmt in node.body:
            if calls_class_main(stmt):
                return True
    return False


def is_cli_entry_point(py_file: Path) -> bool:
    """
    Check if a file is a CLI entry point.

    Recognizes two patterns:
    1. Module-level main() with if __name__ == "__main__"
    2. Class-based ClassName.main() called from if __name__ == "__main__"

    CLI entry points are meant to be executed directly (e.g., python -m module)
    rather than imported, so they don't need to appear in import statements.
    """
    return _check_cli_entry_point_patterns(py_file)


def _check_cli_entry_point_patterns(py_file: Path) -> bool:
    """Check if file matches CLI entry point patterns."""
    tree = parse_python_ast(py_file, raise_on_error=False)
    if not tree:
        return False

    # Pattern 1: Module-level main() function
    if has_main_guard(tree) and has_main_function(tree):
        return True

    # Pattern 2: Class-based ClassName.main() call
    if has_class_main_call(tree):
        return True

    return False
