#!/usr/bin/env python3
"""Entry point that delegates to the policy checks module."""

from __future__ import annotations

import sys

from .policy_checks import (
    purge_bytecode_artifacts,  # Re-exported for external consumers
)
from .policy_checks import (
    PolicyViolation,
    main,
)

__all__ = ["PolicyViolation", "purge_bytecode_artifacts", "main"]

if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main())
    except PolicyViolation as err:
        print(err, file=sys.stderr)
        sys.exit(1)
