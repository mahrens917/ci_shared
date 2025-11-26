#!/usr/bin/env python3
"""Enforce per-file coverage thresholds using coverage.py data."""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

from coverage import Coverage
from coverage.exceptions import CoverageException, NoDataError, NoSource

from ci_tools.scripts.guard_common import detect_repo_root

ROOT = detect_repo_root()


@dataclass(frozen=True)
class CoverageResult:
    """Represents coverage results for a single file."""

    path: Path
    statements: int
    missing: int

    @property
    def percent(self) -> float:
        """Calculate coverage percentage."""
        if self.statements == 0:
            return 100.0
        covered = self.statements - self.missing
        return (covered / self.statements) * 100.0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse command-line arguments for coverage guard."""
    parser = argparse.ArgumentParser(
        description="Fail when any measured file falls below the coverage threshold."
    )
    parser.add_argument(
        "--threshold",
        type=float,
        required=True,
        help="Required per-file coverage percentage.",
    )
    parser.add_argument(
        "--data-file",
        required=True,
        help="Coverage data file path.",
    )
    parser.add_argument(
        "--include",
        action="append",
        help="Relative path prefixes to check (repeatable). Initial: repo root.",
    )
    parser.set_defaults(
        include=[],
    )
    return parser.parse_args(argv)


def resolve_data_file(candidate: str) -> Path:
    """Resolve the coverage data file path."""
    path = Path(candidate)
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def find_config_file() -> str | None:
    """Find the coverage configuration file (pyproject.toml or .coveragerc)."""
    pyproject = ROOT / "pyproject.toml"
    if pyproject.exists():
        return str(pyproject)
    coveragerc = ROOT / ".coveragerc"
    if coveragerc.exists():
        return str(coveragerc)
    return None


def normalize_prefixes(prefixes: Iterable[str]) -> List[Path]:
    """Normalize file path prefixes to absolute paths."""
    paths: List[Path] = []
    for prefix in prefixes:
        candidate = (ROOT / prefix).resolve()
        paths.append(candidate)
    return paths


def should_include(path: Path, prefixes: Sequence[Path]) -> bool:
    """Check if a path should be included based on prefixes."""
    try:
        path.relative_to(ROOT)
    except ValueError:
        return False
    if not prefixes:
        return True
    return any(
        path == prefix or str(path).startswith(str(prefix) + os.sep)
        for prefix in prefixes
    )


def _matches_omit_pattern(path_str: str, patterns: Sequence[str]) -> bool:
    """Check if path matches any omit pattern."""
    for pattern in patterns:
        if fnmatch.fnmatch(path_str, pattern):
            return True
    return False


def is_omitted(path: Path, cov: Coverage) -> bool:
    """Check if a file should be omitted based on coverage config omit patterns."""
    patterns = cov.config.report_omit
    if patterns is not None:
        return _matches_omit_pattern(str(path), patterns)
    return False


def collect_results(cov: Coverage, prefixes: Sequence[Path]) -> List[CoverageResult]:
    """Collect coverage results for files matching the given prefixes."""
    try:
        cov.load()
    except NoDataError as exc:
        msg = f"coverage_guard: no data found ({exc})"
        raise SystemExit(msg) from exc
    data = cov.get_data()
    results: List[CoverageResult] = []
    for filename in sorted(data.measured_files()):
        file_path = Path(filename).resolve()
        if not should_include(file_path, prefixes):
            continue
        if is_omitted(file_path, cov):
            continue
        try:
            _, statements, _, missing, _ = cov.analysis2(str(file_path))
        except NoSource:
            continue
        total_statements = len(statements)
        missing_count = len(missing)
        results.append(
            CoverageResult(
                path=file_path,
                statements=total_statements,
                missing=missing_count,
            )
        )
    return results


def _create_coverage_instance(data_file: Path, config_file: str | None) -> Coverage:
    """Create a Coverage instance with appropriate config handling.

    The Coverage class expects config_file to be FilePath or bool (True means
    auto-detect). We use True when no explicit config is found.
    """
    if config_file is not None:
        return Coverage(data_file=str(data_file), config_file=config_file)
    return Coverage(data_file=str(data_file), config_file=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Main entry point for coverage guard."""
    args = parse_args(argv or sys.argv[1:])
    data_file = resolve_data_file(args.data_file)
    if not data_file.exists():
        print(
            f"coverage_guard: coverage data file not found: {data_file}",
            file=sys.stderr,
        )
        return 1
    config_file = find_config_file()
    cov = _create_coverage_instance(data_file, config_file)
    prefixes = normalize_prefixes(args.include)
    try:
        results = collect_results(cov, prefixes)
    except CoverageException as exc:
        print(f"coverage_guard: failed to load coverage data: {exc}", file=sys.stderr)
        return 1
    threshold = float(args.threshold)
    failures = [
        result
        for result in results
        if result.statements > 0 and result.percent + 1e-9 < threshold
    ]
    if failures:
        print(
            "coverage_guard: per-file coverage below threshold " f"({threshold:.2f}%):",
            file=sys.stderr,
        )
        for result in failures:
            rel_path = result.path.relative_to(ROOT)
            covered = result.statements - result.missing
            print(
                f"  {rel_path.as_posix()}: {result.percent:.2f}% "
                f"({covered}/{result.statements} lines covered)",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
