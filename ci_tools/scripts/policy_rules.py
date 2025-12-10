"""Policy enforcement helpers built on top of collectors."""

from __future__ import annotations

from typing import List, Tuple

from .policy_collectors_ast import (
    collect_backward_compat_blocks,
    collect_bool_fallbacks,
    collect_broad_excepts,
    collect_bytecode_artifacts,
    collect_conditional_literal_returns,
    collect_duplicate_functions,
    collect_forbidden_sync_calls,
    collect_generic_raises,
    collect_literal_fallbacks,
    collect_long_functions,
    collect_silent_handlers,
    purge_bytecode_artifacts,
)
from .policy_collectors_text import (
    collect_flagged_tokens,
    collect_legacy_configs,
    collect_legacy_modules,
    collect_suppressions,
    scan_keywords,
)
from .policy_context import (
    FUNCTION_LENGTH_THRESHOLD,
    FunctionEntry,
)


class PolicyViolation(Exception):
    """Raised when the policy guard detects a violation."""


def enforce_occurrences(discovered: List[Tuple[str, int]], message: str) -> None:
    """Raise PolicyViolation if any occurrences are found."""
    if not discovered:
        return
    violations = [f"{path}:{lineno} -> {message}" for path, lineno in discovered]
    raise PolicyViolation("Policy violations detected:\n" + "\n".join(sorted(violations)))


def enforce_duplicate_functions(duplicates: List[List[FunctionEntry]]) -> None:
    """Raise PolicyViolation if duplicate function implementations are found."""
    if not duplicates:
        return
    messages: List[str] = []
    for group in duplicates:
        details = ", ".join(
            f"{entry.path}:{entry.lineno} ({entry.name})" for entry in sorted(group, key=lambda item: (str(item.path), item.lineno))
        )
        messages.append(f"Duplicate function implementations detected: {details}")
    raise PolicyViolation("Duplicate helper policy violations detected:\n" + "\n".join(messages))


def _check_keyword_policy() -> None:
    keyword_hits = scan_keywords()
    for keyword, files in keyword_hits.items():
        for path, lines in files.items():
            messages = [f"{path}:{lineno} -> keyword '{keyword}'" for lineno in lines]
            if messages:
                raise PolicyViolation("Banned keyword policy violations detected:\n" + "\n".join(sorted(messages)))


def _check_flagged_tokens() -> None:
    flagged_tokens = collect_flagged_tokens()
    if not flagged_tokens:
        return
    details = "\n".join(f"{path}:{lineno} -> flagged token '{token}' detected" for path, lineno, token in sorted(flagged_tokens))
    raise PolicyViolation("Flagged annotations detected:\n" + details)


def _check_function_lengths() -> None:
    long_functions = list(collect_long_functions(FUNCTION_LENGTH_THRESHOLD))
    enforce_function_lengths(long_functions)


def enforce_function_lengths(found: List[FunctionEntry], threshold: int = FUNCTION_LENGTH_THRESHOLD) -> None:
    """Raise PolicyViolation if any functions exceed the length threshold."""
    if not found:
        return
    violations = [
        f"{entry.path}:{entry.lineno} -> function '{entry.name}' " f"length {entry.length} exceeds {threshold}" for entry in found
    ]
    raise PolicyViolation("Function length policy violations detected:\n" + "\n".join(sorted(violations)))


def _check_broad_excepts() -> None:
    broad_excepts = collect_broad_excepts()
    enforce_occurrences(broad_excepts, "broad exception handler")


def _check_silent_handlers() -> None:
    silent_handlers = collect_silent_handlers()
    if not silent_handlers:
        return
    details = "\n".join(f"{path}:{lineno} -> {reason}" for path, lineno, reason in sorted(silent_handlers))
    raise PolicyViolation("Silent exception handler detected:\n" + details)


def _check_generic_raises() -> None:
    generic_raises = collect_generic_raises()
    enforce_occurrences(generic_raises, "generic Exception raise")


def _check_literal_fallbacks() -> None:
    literal_fallbacks = collect_literal_fallbacks()
    if not literal_fallbacks:
        return
    details = "\n".join(f"{path}:{lineno} -> {reason}" for path, lineno, reason in sorted(literal_fallbacks))
    raise PolicyViolation("Fallback default usage detected:\n" + details)


def _check_boolean_fallbacks() -> None:
    bool_fallbacks = collect_bool_fallbacks()
    enforce_occurrences(bool_fallbacks, "literal fallback via boolean 'or'")


def _check_conditional_literals() -> None:
    conditional_literals = collect_conditional_literal_returns()
    enforce_occurrences(conditional_literals, "literal return inside None guard")


def _check_backward_compat() -> None:
    backward_compat = collect_backward_compat_blocks()
    if not backward_compat:
        return
    details = "\n".join(f"{path}:{lineno} -> {reason}" for path, lineno, reason in sorted(backward_compat))
    raise PolicyViolation("Backward compatibility code detected:\n" + details)


def _check_legacy_artifacts() -> None:
    legacy_modules = collect_legacy_modules()
    if legacy_modules:
        details = "\n".join(f"{path}:{lineno} -> {reason}" for path, lineno, reason in sorted(legacy_modules))
        raise PolicyViolation("Legacy module detected:\n" + details)

    legacy_configs = collect_legacy_configs()
    if legacy_configs:
        details = "\n".join(f"{path}:{lineno} -> {reason}" for path, lineno, reason in sorted(legacy_configs))
        raise PolicyViolation("Legacy toggle detected in config:\n" + details)


def _check_sync_calls() -> None:
    forbidden_calls = collect_forbidden_sync_calls()
    if not forbidden_calls:
        return
    details = "\n".join(f"{path}:{lineno} -> {reason}" for path, lineno, reason in sorted(forbidden_calls))
    raise PolicyViolation("Synchronous call policy violations detected:\n" + details)


def _check_suppressions() -> None:
    suppressions = collect_suppressions()
    if not suppressions:
        return
    details = "\n".join(f"{path}:{lineno} -> suppression token '{token}' detected" for path, lineno, token in sorted(suppressions))
    raise PolicyViolation("Suppression policy violations detected:\n" + details)


def _check_duplicate_functions() -> None:
    duplicates = collect_duplicate_functions()
    enforce_duplicate_functions(duplicates)


def _check_bytecode_artifacts() -> None:
    offenders = collect_bytecode_artifacts()
    if offenders:
        raise PolicyViolation("Bytecode artifacts detected:\n" + "\n".join(offenders))


__all__ = [
    "PolicyViolation",
    "purge_bytecode_artifacts",
    "_check_keyword_policy",
    "_check_flagged_tokens",
    "_check_function_lengths",
    "_check_broad_excepts",
    "_check_silent_handlers",
    "_check_generic_raises",
    "_check_literal_fallbacks",
    "_check_boolean_fallbacks",
    "_check_conditional_literals",
    "_check_backward_compat",
    "_check_legacy_artifacts",
    "_check_sync_calls",
    "_check_suppressions",
    "_check_duplicate_functions",
    "_check_bytecode_artifacts",
]
