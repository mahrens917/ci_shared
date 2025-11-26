"""High-level orchestration for policy enforcement routines."""

from __future__ import annotations

import sys

from .policy_rules import (
    PolicyViolation,
    _check_backward_compat,
    _check_boolean_fallbacks,
    _check_broad_excepts,
    _check_bytecode_artifacts,
    _check_conditional_literals,
    _check_duplicate_functions,
    _check_flagged_tokens,
    _check_function_lengths,
    _check_generic_raises,
    _check_keyword_policy,
    _check_legacy_artifacts,
    _check_literal_fallbacks,
    _check_silent_handlers,
    _check_suppressions,
    _check_sync_calls,
    purge_bytecode_artifacts,
)


def main() -> int:
    """Run all policy checks and return exit code (0 if no violations, 1 otherwise)."""
    purge_bytecode_artifacts()
    _check_keyword_policy()
    _check_flagged_tokens()
    _check_function_lengths()
    _check_broad_excepts()
    _check_silent_handlers()
    _check_generic_raises()
    _check_literal_fallbacks()
    _check_boolean_fallbacks()
    _check_conditional_literals()
    _check_backward_compat()
    _check_legacy_artifacts()
    _check_sync_calls()
    _check_suppressions()
    _check_duplicate_functions()
    _check_bytecode_artifacts()
    return 0


__all__ = ["PolicyViolation", "main", "purge_bytecode_artifacts"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PolicyViolation as err:
        print(err, file=sys.stderr)
        sys.exit(1)
