#!/usr/bin/env python3
"""Entry point that delegates to the policy checks module."""

from __future__ import annotations

import sys

from .policy_checks import (
    purge_bytecode_artifacts,  # Re-exported for external consumers
)
from .policy_checks import (
    PolicyViolation,
)
from .policy_checks import main as _run_policy_checks

__all__ = ["PolicyViolation", "purge_bytecode_artifacts", "main"]


def main() -> int:  # pragma: no cover - thin wrapper
    """Run policy checks and return exit code."""
    return _run_policy_checks()


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main())
    except PolicyViolation as err:
        print(err, file=sys.stderr)
        sys.exit(1)
