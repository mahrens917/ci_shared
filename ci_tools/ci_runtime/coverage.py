"""Coverage report parsing helpers."""

from __future__ import annotations

from typing import Optional

from .config import COVERAGE_THRESHOLD
from .models import CoverageCheckResult, CoverageDeficit

# Coverage table format: Name Stmts Miss Cover
MIN_COVERAGE_ROW_TOKENS = 4


def _find_coverage_table(lines: list[str]) -> Optional[list[str]]:
    """Return the lines that compose the coverage table in the pytest report."""
    header_index: Optional[int] = None
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("Name") and "Cover" in stripped:
            header_index = idx
            break
    if header_index is None:
        return None
    table: list[str] = [lines[header_index]]
    for line in lines[header_index + 1 :]:
        table.append(line)
        if not line.strip():
            break
    return table if len(table) > 1 else None


def _parse_coverage_entries(
    rows: list[str],
    threshold: float,
) -> list[CoverageDeficit]:
    """Parse coverage table rows and collect deficits below the given threshold."""
    deficits: list[CoverageDeficit] = []
    for row in rows:
        stripped = row.strip()
        if not stripped or stripped.startswith("-"):
            continue
        tokens = row.split()
        if len(tokens) < MIN_COVERAGE_ROW_TOKENS:
            continue
        cover_token = tokens[-1]
        if not cover_token.endswith("%"):
            continue
        try:
            coverage = float(cover_token[:-1])
        except ValueError:
            continue
        path_token = " ".join(tokens[:-3]).strip()
        if not path_token or path_token.upper() == "TOTAL":
            continue
        if coverage < threshold:
            deficits.append(CoverageDeficit(path=path_token, coverage=coverage))
    return deficits


def extract_coverage_deficits(output: str, *, threshold: float = COVERAGE_THRESHOLD) -> Optional[CoverageCheckResult]:
    """Extract modules that fall below the coverage threshold from pytest output."""
    if not output:
        return None
    table = _find_coverage_table(output.splitlines())
    if table is None:
        return None
    table_lines = list(table)
    deficits = _parse_coverage_entries(table_lines[2:], threshold)
    if not deficits:
        return None
    table_text = "\n".join(table_lines).strip()
    return CoverageCheckResult(table_text=table_text, deficits=deficits, threshold=threshold)


__all__ = ["extract_coverage_deficits"]
