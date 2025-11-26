"""Wrapper for running Bandit in strict mode.

This script executes ``python -m bandit`` with the provided arguments while
ensuring that any warnings emitted by Bandit are treated as CI failures.  The
upstream CLI currently exits with zero even when it logs warnings such as
``nosec`` misconfigurations, so we fail explicitly when those appear.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from typing import Iterable, List, Sequence

WARNING_PATTERN = re.compile(r"^\[[^\]]+\]\s+WARNING\b")


def collect_warning_lines(streams: Iterable[str]) -> List[str]:
    """Return lines that match Bandit's WARNING log format."""
    warning_lines: List[str] = []
    for chunk in streams:
        if not chunk:
            continue
        for raw_line in chunk.splitlines():
            line = raw_line.strip()
            if WARNING_PATTERN.match(line):
                warning_lines.append(line)
    return warning_lines


def run_bandit(
    bandit_args: Sequence[str],
    *,
    module: str = "bandit",
    allow_warnings: bool = False,
) -> int:
    """Invoke Bandit and fail if warnings are emitted."""
    cmd = [sys.executable, "-m", module, *bandit_args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)

    warning_lines = collect_warning_lines((result.stdout, result.stderr))
    if result.returncode != 0:
        return result.returncode

    if warning_lines and not allow_warnings:
        sys.stderr.write("Bandit emitted warnings; treating as failure:\n")
        for line in warning_lines:
            sys.stderr.write(f"  {line}\n")
        return 1

    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser for the wrapper."""
    parser = argparse.ArgumentParser(
        description="Run Bandit while failing on warnings.",
        add_help=True,
    )
    parser.add_argument(
        "--allow-warnings",
        action="store_true",
        help="Do not fail the run when Bandit logs warnings.",
    )
    parser.add_argument(
        "--module",
        default="bandit",
        help="Python module to run (primarily for tests).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args, bandit_args = parser.parse_known_args(argv)
    if not bandit_args:
        parser.error("pass Bandit arguments after the wrapper options")
    return run_bandit(
        bandit_args,
        module=args.module,
        allow_warnings=args.allow_warnings,
    )


if __name__ == "__main__":
    sys.exit(main())
