"""Fail the build when code uses delegation anti-patterns.

Detects:
1. Module-scope setattr on classes (binding methods externally)
2. Single-method wrapper classes (trivial delegation)
3. Pass-through functions (direct argument forwarding)
4. Empty helper packages (*_helpers/ with only __init__.py)
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import List, Set

from ci_tools.scripts.guard_common import (
    GuardRunner,
    iter_python_files,
    parse_python_ast,
    relative_path,
)

_MIN_DELEGATION_METHODS = 2

DUNDER_METHODS = frozenset(
    {
        "__init__",
        "__post_init__",
        "__repr__",
        "__str__",
        "__eq__",
        "__ne__",
        "__lt__",
        "__le__",
        "__gt__",
        "__ge__",
        "__hash__",
        "__bool__",
        "__len__",
        "__iter__",
        "__next__",
        "__enter__",
        "__exit__",
        "__call__",
        "__getattr__",
        "__setattr__",
        "__delattr__",
        "__getitem__",
        "__setitem__",
        "__delitem__",
        "__contains__",
        "__add__",
        "__sub__",
        "__mul__",
        "__truediv__",
        "__floordiv__",
        "__mod__",
        "__pow__",
        "__and__",
        "__or__",
        "__xor__",
        "__neg__",
        "__pos__",
        "__abs__",
        "__invert__",
        "__del__",
        "__new__",
    }
)


def _has_dataclass_decorator(class_node: ast.ClassDef) -> bool:
    """Check if a class has a @dataclass decorator."""
    for decorator in class_node.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id == "dataclass":
            return True
        if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name) and decorator.func.id == "dataclass":
            return True
        if isinstance(decorator, ast.Attribute) and decorator.attr == "dataclass":
            return True
    return False


def _body_without_docstring(body: List[ast.stmt]) -> List[ast.stmt]:
    """Return function body with a leading docstring stripped, if present."""
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        return body[1:]
    return body


def _is_single_return_call(body: List[ast.stmt]) -> bool:
    """Check if a function body is a single return statement containing a call.

    Handles both sync and async delegation: `return f()` and `return await f()`.
    Leading docstrings are ignored so documented pass-throughs are still caught.
    """
    stmts = _body_without_docstring(body)
    if len(stmts) != 1:
        return False
    stmt = stmts[0]
    if not isinstance(stmt, ast.Return):
        return False
    value = stmt.value
    if isinstance(value, ast.Await):
        value = value.value
    return isinstance(value, ast.Call)


def _get_param_names(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> List[str]:
    """Extract parameter names from a function definition, excluding 'self' and 'cls'.

    Includes both positional args and keyword-only args (after *).
    """
    names: List[str] = []
    for arg in func_node.args.args:
        if arg.arg not in ("self", "cls"):
            names.append(arg.arg)
    for arg in func_node.args.kwonlyargs:
        names.append(arg.arg)
    return names


def _extract_positional_names(call: ast.Call) -> List[str] | None:
    """Return positional arg names from a call, or None if any arg is not a plain Name."""
    names: List[str] = []
    for arg in call.args:
        if not isinstance(arg, ast.Name):
            return None
        names.append(arg.id)
    return names


def _keywords_match_params(keywords: List[ast.keyword], param_names: List[str]) -> bool:
    """Check that all keyword args are param→param forwarding and present in param_names."""
    for kw in keywords:
        if kw.arg is None:
            return False
        if not isinstance(kw.value, ast.Name) or kw.value.id != kw.arg:
            return False
        if kw.arg not in param_names:
            return False
    return True


def _call_forwards_params(call: ast.Call, param_names: List[str]) -> bool:
    """Check if a call forwards the exact same args as the function's parameters.

    Returns False if the call adds keyword arguments not present in the function's params,
    since that indicates the function is specializing behavior rather than delegating.
    """
    call_arg_names = _extract_positional_names(call)
    if call_arg_names is None:
        return False
    if call_arg_names != param_names[: len(call_arg_names)]:
        return False
    if not _keywords_match_params(call.keywords, param_names):
        return False
    forwarded = set(call_arg_names) | {kw.arg for kw in call.keywords if kw.arg is not None}
    return forwarded == set(param_names)


def _check_module_scope_setattr(tree: ast.Module) -> List[int]:
    """Detect setattr(ClassName, "method_name", func) at module level."""
    violations: List[int] = []
    for node in tree.body:
        if not isinstance(node, ast.Expr):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not (isinstance(call.func, ast.Name) and call.func.id == "setattr"):
            continue
        if len(call.args) >= 1 and isinstance(call.args[0], ast.Name):
            violations.append(node.lineno)
    return violations


def _get_non_dunder_methods(class_node: ast.ClassDef) -> List[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return all non-dunder methods from a class body."""
    return [
        item for item in class_node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name not in DUNDER_METHODS
    ]


def _check_single_method_wrappers(tree: ast.Module) -> List[tuple[int, str]]:
    """Detect classes with exactly 1 non-dunder method whose body delegates."""
    violations: List[tuple[int, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if _has_dataclass_decorator(node):
            continue
        methods = _get_non_dunder_methods(node)
        if len(methods) == 1 and _is_single_return_call(methods[0].body):
            violations.append((node.lineno, node.name))
    return violations


def _get_callee_name(func: ast.AST) -> str:
    """Return the attribute or name of the callee from a Call's func node."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _has_decorator(method: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    """Return True if the method has a decorator with the given name."""
    return any(
        (isinstance(d, ast.Name) and d.id == name) or (isinstance(d, ast.Attribute) and d.attr == name) for d in method.decorator_list
    )


def _is_factory_method(method: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if this method is a named constructor or factory — not delegation.

    Covers:
    - ``@classmethod`` where body is ``return cls(...)``
    - ``@staticmethod`` (no ``self``/``cls`` — cannot delegate to a sub-object)
    """
    if _has_decorator(method, "staticmethod"):
        return True
    if not _has_decorator(method, "classmethod"):
        return False
    stmts = _body_without_docstring(method.body)
    if len(stmts) != 1 or not isinstance(stmts[0], ast.Return):
        return False
    value = stmts[0].value
    if isinstance(value, ast.Await):
        value = value.value
    return isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "cls"


def _check_all_method_delegation(tree: ast.Module) -> List[tuple[int, str]]:
    """Detect non-dataclasses where every non-dunder method is a single delegation call.

    Catches coordinator classes with 2+ methods that all forward to sub-objects,
    which the single-method wrapper check misses. Named-constructor classmethods
    (``return cls(...)``) and ``@staticmethod`` methods are excluded since they
    cannot delegate to a sub-object.
    """
    violations: List[tuple[int, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if _has_dataclass_decorator(node):
            continue
        methods = _get_non_dunder_methods(node)
        # Exclude factory methods — they cannot delegate to self/cls sub-objects
        delegation_methods = [m for m in methods if not _is_factory_method(m)]
        if len(delegation_methods) < _MIN_DELEGATION_METHODS:  # single-method case already handled
            continue
        if all(_is_single_return_call(m.body) for m in delegation_methods):
            violations.append((node.lineno, node.name))
    return violations


def _check_passthrough_functions(tree: ast.Module) -> List[tuple[int, str]]:
    """Detect module-level functions that just forward args to another function."""
    violations: List[tuple[int, str]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_single_return_call(node.body):
            continue
        param_names = _get_param_names(node)
        return_stmt = _body_without_docstring(node.body)[0]
        value = return_stmt.value  # type: ignore[union-attr]
        if isinstance(value, ast.Await):
            value = value.value
        if not isinstance(value, ast.Call):
            continue
        if _call_forwards_params(value, param_names) and _get_callee_name(value.func) != node.name:
            violations.append((node.lineno, node.name))
    return violations


def _is_empty_helper_package(directory: Path) -> bool:
    """Return True if directory is a *_helpers package containing only __init__.py."""
    if not directory.name.endswith("_helpers"):
        return False
    if not (directory / "__init__.py").exists():
        return False
    if any(f.name != "__init__.py" for f in directory.glob("*.py")):
        return False
    return not any(d.is_dir() and (d / "__init__.py").exists() for d in directory.iterdir())


def _find_empty_helper_packages(roots: List[Path]) -> List[tuple[str, str]]:
    """Detect *_helpers/ directories that contain only __init__.py."""
    seen_dirs: Set[Path] = set()
    for root in roots:
        for py_file in iter_python_files(root):
            seen_dirs.add(py_file.parent)
    return [(str(d), d.name) for d in seen_dirs if _is_empty_helper_package(d)]


class DelegationGuard(GuardRunner):
    """Guard that detects delegation anti-patterns."""

    def __init__(self):
        super().__init__(
            name="delegation_guard",
            description="Detect delegation anti-patterns that obscure code.",
            default_root=Path("src"),
        )
        self._empty_helper_violations: List[str] = []

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        """No additional arguments needed."""

    def scan_file(self, path: Path, args: argparse.Namespace) -> List[str]:
        """Scan a file for delegation anti-patterns."""
        tree = parse_python_ast(path)
        assert tree is not None
        assert isinstance(tree, ast.Module)

        violations: List[str] = []
        rel = relative_path(path, self.repo_root)

        for lineno in _check_module_scope_setattr(tree):
            violations.append(f"{rel}:{lineno} module-scope setattr binds method to class externally")

        for lineno, class_name in _check_single_method_wrappers(tree):
            violations.append(f"{rel}:{lineno} class {class_name} is a single-method wrapper that delegates to another function")

        for lineno, class_name in _check_all_method_delegation(tree):
            violations.append(f"{rel}:{lineno} class {class_name} is a full delegation class where every method forwards to a sub-object")

        for lineno, func_name in _check_passthrough_functions(tree):
            violations.append(f"{rel}:{lineno} function {func_name} is a pass-through that forwards all arguments")

        return violations

    def run(self, argv=None) -> int:
        """Override run to also check empty helper packages."""
        args = self.parse_args(argv)
        roots = self._resolve_roots(args)
        if roots is None:
            return 1

        # Run standard file-level scanning
        result = super().run(argv)

        # Check empty helper packages
        empty_helpers = _find_empty_helper_packages(roots)
        if empty_helpers:
            from ci_tools.scripts.guard_common import report_violations

            msgs = [f"{path} is an empty helper package (contains only __init__.py)" for path, _name in empty_helpers]
            report_violations(msgs, "Empty helper packages detected:")
            return 1

        return result

    def get_violations_header(self, args: argparse.Namespace) -> str:
        """Get the header for violations report."""
        return "Delegation anti-patterns detected. Inline the logic directly:"

    def get_violations_footer(self, args: argparse.Namespace) -> str:
        return "Fix: define methods in class bodies, inline single-use wrappers, " "and remove empty *_helpers/ packages."


if __name__ == "__main__":
    sys.exit(DelegationGuard.main())
