#!/usr/bin/env python3
"""
Guard against hard-coded thresholds, synthetic datasets, and literal fallbacks.
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple, TypeGuard

from ci_tools.scripts.guard_common import (
    iter_ast_nodes,
    iter_python_files,
    parse_python_ast,
    relative_path,
)
from ci_tools.scripts.policy_context import (
    ROOT,
    SCAN_DIRECTORIES,
    contains_literal_dataset,
    get_call_qualname,
)

ALLOWLIST_PATH = ROOT / "config" / "data_guard_allowlist.json"

SENSITIVE_NAME_TOKENS: Tuple[str, ...] = (
    "threshold",
    "limit",
    "timeout",
    "default",
    "max",
    "min",
    "retry",
    "window",
    "size",
    "count",
)
ALLOWED_NUMERIC_LITERALS = {0, 1, -1}
DATAFRAME_CALLS = {
    "pandas.DataFrame",
    "pd.DataFrame",
    "DataFrame",
    "numpy.array",
    "np.array",
    "numpy.asarray",
    "np.asarray",
}


# DataGuard script uses heuristics; prefer false positives to silent drift.


class DataGuardAllowlistError(RuntimeError):
    """Raised when the allowlist payload cannot be loaded."""

    default_message = "Unable to load data guard allowlist"

    def __init__(self, *, detail: str) -> None:
        super().__init__(f"{self.default_message}: {detail}")


def load_allowlist() -> Dict[str, set[str]]:
    """Load the data guard allowlist from config file."""
    if not ALLOWLIST_PATH.exists():
        return {"assignments": set(), "comparisons": set(), "dataframe": set()}
    try:
        payload = json.loads(ALLOWLIST_PATH.read_text())
    except json.JSONDecodeError as exc:
        raise DataGuardAllowlistError(
            detail=f"JSON parse error at {ALLOWLIST_PATH}: {exc}"
        ) from exc

    def _coerce_group(key: str) -> set[str]:
        values = payload.get(key)
        if values is None:
            return set()
        return {str(item) for item in values}

    return {
        "assignments": _coerce_group("assignments"),
        "comparisons": _coerce_group("comparisons"),
        "dataframe": _coerce_group("dataframe"),
    }


ALLOWLIST = load_allowlist()


def allowlisted(name: str, category: str) -> bool:
    """Check if a name is allowlisted in a given category."""
    if category not in ALLOWLIST:
        return False
    return name in ALLOWLIST[category]


class DataGuardViolation(Exception):
    """Raised when the data guard detects a violation."""


@dataclass(frozen=True)
class Violation:
    """Represents a data guard policy violation."""

    path: Path
    lineno: int
    message: str


def extract_target_names(target: ast.AST) -> Iterable[str]:
    """Extract variable names from assignment targets."""
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, ast.Tuple):
        for elt in target.elts:
            yield from extract_target_names(elt)
    elif isinstance(target, ast.Attribute):
        yield target.attr


def is_all_caps_identifier(name: str) -> bool:
    """Check if name is an all-caps identifier (constant)."""
    stripped = name.strip()
    return (
        bool(stripped)
        and stripped.upper() == stripped
        and any(ch.isalpha() for ch in stripped)
    )


def is_numeric_constant(node: ast.AST | None) -> TypeGuard[ast.Constant]:
    """Check if a node is a numeric constant."""
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float))


def literal_value_repr(node: ast.AST | None) -> str:
    """Get string representation of a literal value."""
    if isinstance(node, ast.Constant):
        return repr(node.value)
    if node is not None:
        return ast.dump(node)
    return "None"


def should_flag_assignment(target_names: Iterable[str], value: ast.AST | None) -> bool:
    """Determine if an assignment should be flagged as a violation."""
    names = [name for name in target_names if name]
    if not names:
        return False
    if all(is_all_caps_identifier(name) for name in names):
        return False
    if any(allowlisted(name, "assignments") for name in names):
        return False
    if not (value and is_numeric_constant(value)):
        return False
    return value.value not in ALLOWED_NUMERIC_LITERALS


def should_flag_comparison(names: Iterable[str]) -> bool:
    """Check if a comparison should be flagged as a violation."""
    identifiers = [name for name in names if name]
    if not identifiers:
        return False
    if all(is_all_caps_identifier(name) for name in identifiers):
        return False
    return not any(allowlisted(name, "comparisons") for name in identifiers)


def flatten_assignment_targets(targets: Iterable[ast.AST]) -> list[str]:
    """Extract all target names from assignment targets."""
    names: list[str] = []
    for target in targets:
        names.extend(extract_target_names(target))
    return names


def contains_sensitive_token(names: Iterable[str]) -> bool:
    """Check if any name contains a sensitive token."""
    lowered = [name.lower() for name in names]
    return any(
        token in candidate for candidate in lowered for token in SENSITIVE_NAME_TOKENS
    )


def build_assignment_violation(
    path: Path,
    *,
    target_names: list[str],
    value: ast.AST | None,
    lineno: int,
    prefix: str,
) -> Optional[Violation]:
    """Build a violation for a sensitive assignment if applicable."""
    if not target_names or not contains_sensitive_token(target_names):
        return None
    if not should_flag_assignment(target_names, value):
        return None
    message = (
        f"{prefix} {literal_value_repr(value)} for {', '.join(sorted(target_names))}"
    )
    return Violation(path=path, lineno=lineno, message=message)


def assignment_violation_from_node(path: Path, node: ast.AST) -> Optional[Violation]:
    """Extract assignment violation from an AST node if applicable."""
    if isinstance(node, ast.Assign):
        names = flatten_assignment_targets(node.targets)
        return build_assignment_violation(
            path,
            target_names=names,
            value=node.value,
            lineno=node.lineno,
            prefix="literal assignment",
        )
    if isinstance(node, ast.AnnAssign):
        names = list(extract_target_names(node.target))
        return build_assignment_violation(
            path,
            target_names=names,
            value=node.value,
            lineno=node.lineno,
            prefix="annotated literal assignment",
        )
    return None


def iter_sensitive_assignment_violations(
    path: Path, tree: ast.AST
) -> Iterator[Violation]:
    """Iterate over all assignment violations in a file."""
    for node in iter_ast_nodes(tree, (ast.Assign, ast.AnnAssign)):
        violation = assignment_violation_from_node(path, node)
        if violation:
            yield violation


def collect_violations_from_iterator(
    iterator_func: Callable[[Path, ast.AST], Iterator[Violation]],
) -> List[Violation]:
    """Generic collector that applies an iterator function to all Python files."""
    violations: List[Violation] = []
    for path in iter_python_files(SCAN_DIRECTORIES):
        tree = parse_python_ast(path, raise_on_error=False)
        if tree is None:
            continue
        violations.extend(iterator_func(path, tree))
    return violations


def collect_sensitive_assignments() -> List[Violation]:
    """Collect violations for assignments with sensitive data patterns."""
    return collect_violations_from_iterator(iter_sensitive_assignment_violations)


def call_contains_literal_arguments(node: ast.Call) -> bool:
    """Check if a call contains literal dataset arguments."""
    arguments = list(node.args) + [kw.value for kw in node.keywords]
    return any(contains_literal_dataset(arg) for arg in arguments)


def iter_dataframe_literal_violations(path: Path, tree: ast.AST) -> Iterator[Violation]:
    """Iterate over DataFrame literal violations in a file."""
    for node in iter_ast_nodes(tree, ast.Call):
        assert isinstance(node, ast.Call)  # Type narrowing for pyright
        qualname = get_call_qualname(node.func)
        if not qualname or qualname not in DATAFRAME_CALLS:
            continue
        if allowlisted(qualname, "dataframe"):
            continue
        if call_contains_literal_arguments(node):
            yield Violation(
                path=path,
                lineno=node.lineno,
                message=f"literal dataset passed to {qualname}",
            )


def collect_dataframe_literals() -> List[Violation]:
    """Collect violations for DataFrame operations with literal values."""
    return collect_violations_from_iterator(iter_dataframe_literal_violations)


def literal_comparators(node: ast.Compare) -> list[ast.Constant]:
    """Extract literal comparators from a comparison node."""
    return [
        comp
        for comp in node.comparators
        if is_numeric_constant(comp) and comp.value not in ALLOWED_NUMERIC_LITERALS
    ]


def comparison_targets(node: ast.Compare) -> list[str]:
    """Extract comparison targets from a comparison node."""
    if isinstance(node.left, ast.Name):
        return [node.left.id]
    return []


def format_comparison_message(
    comparator_literals: list[ast.Constant],
    left_names: list[str],
) -> str:
    """Format a comparison violation message."""
    literal_repr = ", ".join(literal_value_repr(comp) for comp in comparator_literals)
    return (
        "comparison against literal "
        + literal_repr
        + f" for {', '.join(sorted(left_names))}"
    )


def iter_numeric_comparison_violations(
    path: Path, tree: ast.AST
) -> Iterator[Violation]:
    """Iterate over numeric comparison violations in a file."""
    for node in iter_ast_nodes(tree, ast.Compare):
        assert isinstance(node, ast.Compare)  # Type narrowing for pyright
        comparator_literals = literal_comparators(node)
        if not comparator_literals:
            continue
        left_names = comparison_targets(node)
        if not left_names or not contains_sensitive_token(left_names):
            continue
        if not should_flag_comparison(left_names):
            continue
        yield Violation(
            path=path,
            lineno=node.lineno,
            message=format_comparison_message(comparator_literals, left_names),
        )


def collect_numeric_comparisons() -> List[Violation]:
    """Collect violations for numeric comparisons with literal values."""
    return collect_violations_from_iterator(iter_numeric_comparison_violations)


def collect_all_violations() -> List[Violation]:
    """Collect all data guard violations."""
    violations: List[Violation] = []
    violations.extend(collect_sensitive_assignments())
    violations.extend(collect_dataframe_literals())
    violations.extend(collect_numeric_comparisons())
    return violations


def main() -> int:
    """Main entry point for data guard."""
    violations = sorted(
        collect_all_violations(),
        key=lambda item: (
            relative_path(item.path, as_string=True),
            item.lineno,
            item.message,
        ),
    )
    if violations:
        details = "\n".join(
            f"{relative_path(v.path, as_string=True)}:{v.lineno} -> {v.message}"
            for v in violations
        )
        raise DataGuardViolation("Data integrity violations detected:\n" + details)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DataGuardViolation as err:
        print(err, file=sys.stderr)
        sys.exit(1)
