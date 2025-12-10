"""Failure diagnosis helpers used by the CI workflow."""

from __future__ import annotations

import sys
from typing import Iterable, Optional

from .heuristics import (
    detect_attribute_error,
    detect_missing_symbol_error,
    summarize_failure,
)
from .models import CiAbort, CommandResult, CoverageCheckResult, FailureContext
from .process import gather_file_diff, tail_text


def _gather_focused_diff(implicated_files: Iterable[str]) -> str:
    """Return the per-file git diff for files implicated by the failure."""
    blocks: list[str] = []
    for rel_path in implicated_files:
        diff = gather_file_diff(rel_path)
        if diff:
            blocks.append(diff)
    return "\n\n".join(blocks)


def _render_coverage_context(report: CoverageCheckResult) -> tuple[str, str, list[str]]:
    """Derive coverage summary text and implicated file list."""
    deficits = [f"- {item.path}: {item.coverage:.1f}%" for item in report.deficits]
    intro = "Coverage guard triggered: add or expand tests so each listed module " f"meets the {report.threshold:.0f}% threshold."
    header = f"Coverage deficits detected (threshold {report.threshold:.0f}%):"
    summary = "\n".join([intro, *deficits])
    log_excerpt = "\n".join(
        [
            intro,
            "",
            header,
            *deficits,
            "",
            report.table_text,
        ]
    )
    return summary, log_excerpt, [item.path for item in report.deficits]


def build_failure_context(
    args,
    result: CommandResult,
    coverage_report: Optional[CoverageCheckResult],
) -> FailureContext:
    """Compile the information Codex needs to produce a follow-up patch."""
    if coverage_report is not None:
        summary, log_excerpt, implicated = _render_coverage_context(coverage_report)
        print(
            "[coverage] Coverage below "
            f"{coverage_report.threshold:.0f}% detected for: "
            + ", ".join(f"{item.path} ({item.coverage:.1f}%)" for item in coverage_report.deficits)
        )
        print("[loop] Consulting Codex for additional tests to lift coverage.")
    else:
        log_excerpt = tail_text(result.combined_output, args.log_tail)
        summary, implicated = summarize_failure(log_excerpt)
        missing_symbol_hint = detect_missing_symbol_error(log_excerpt)
        if missing_symbol_hint:
            print(f"[guard] {missing_symbol_hint}", file=sys.stderr)
            print(
                "[guard] Resolve the missing symbol or adjust the import before rerunning ci.py.",
                file=sys.stderr,
            )
            raise CiAbort(detail="Manual intervention required")
        attribute_error_hint = detect_attribute_error(log_excerpt)
        if attribute_error_hint:
            print(f"[guard] {attribute_error_hint}", file=sys.stderr)
            raise CiAbort(detail="Manual intervention required")
        print(f"[loop] CI failed with exit code {result.returncode}. Consulting Codex...")
    focused_diff = _gather_focused_diff(implicated)
    return FailureContext(
        log_excerpt=log_excerpt,
        summary=summary,
        implicated_files=list(implicated),
        focused_diff=focused_diff,
        coverage_report=coverage_report,
    )


__all__ = ["build_failure_context"]
