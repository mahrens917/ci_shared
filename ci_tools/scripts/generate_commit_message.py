#!/usr/bin/env python3
"""Helper invoked by ci.sh to request commit messages from Codex."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Iterator, Sequence, Tuple

from ci_tools.ci_runtime import gather_git_diff, request_commit_message
from ci_tools.ci_runtime.config import (
    CONFIG_CANDIDATES,
    resolve_model_choice,
    resolve_reasoning_choice,
)
from ci_tools.scripts.guard_common import detect_repo_root

CI_SHARED_ROOT = Path(__file__).resolve().parents[2]


def _config_search_roots() -> Iterator[Path]:
    """Yield the repository and shared ci_shared roots for config lookup."""
    repo_root = detect_repo_root()
    yield repo_root
    if CI_SHARED_ROOT != repo_root:
        yield CI_SHARED_ROOT


def _load_config_from_root(root: Path) -> dict[str, Any] | None:
    """Load the first available configuration file from a given root."""
    for candidate_name in CONFIG_CANDIDATES:
        candidate_path = root / candidate_name
        if not candidate_path.is_file():
            continue
        with candidate_path.open(encoding="utf-8") as handle:
            return json.load(handle)
    return None


def get_commit_config() -> dict[str, Any]:
    """Get the commit_message section from config."""
    for root in _config_search_roots():
        config = _load_config_from_root(root)
        if not config:
            continue
        section = config.get("commit_message")
        if isinstance(section, dict):
            return section
    raise KeyError("commit_message section required in ci_shared.config.json")


def _resolve_model_arg(cli_arg: str | None, config: dict[str, Any]) -> str | None:
    """Resolve model from CLI arg, env var, or config file."""
    if cli_arg:
        return cli_arg
    env_model = os.environ.get("CI_COMMIT_MODEL")
    if env_model:
        return env_model
    return config.get("model")


def _resolve_reasoning_arg(cli_arg: str | None, config: dict[str, Any]) -> str | None:
    """Resolve reasoning from CLI arg, env var, or config file."""
    if cli_arg:
        return cli_arg
    env_reasoning = os.environ.get("CI_COMMIT_REASONING")
    if env_reasoning:
        return env_reasoning
    return config.get("reasoning")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for commit message generation."""
    parser = argparse.ArgumentParser(description="Generate a commit message via Codex")
    parser.add_argument("--model", help="Model name to pass to Codex")
    parser.add_argument(
        "--reasoning",
        help="Reasoning effort to request (low/medium/high)",
    )
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="Request a body along with the subject (used for auto-push mode)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional file to write the commit summary/body (suppresses stdout).",
    )
    parser.set_defaults(model=None, reasoning=None, output=None)
    return parser.parse_args(argv)


def _read_staged_diff() -> str:
    return gather_git_diff(staged=True)


def _prepare_payload(summary: str, body_lines: list[str]) -> str:
    body = "\n".join(line.rstrip() for line in body_lines).strip()
    payload_lines = [summary.strip()]
    if body:
        payload_lines.append(body)
    return "\n".join(payload_lines)


def _print_payload_to_stdout(payload: str) -> int:
    """Print payload to stdout and return success code."""
    print(payload)
    return 0


def _write_payload(payload: str, output_path: Path | None) -> int | None:
    if not output_path:
        return _print_payload_to_stdout(payload)
    try:
        output_path.write_text(payload + "\n")
    except OSError as exc:
        print(f"Failed to write commit message to {output_path}: {exc}", file=sys.stderr)
        return 1
    return 0


def _get_config_int(config: dict[str, Any], key: str, env_name: str) -> int:
    """Get an integer value from env var or config file."""
    raw = os.environ.get(env_name)
    if raw is not None:
        return int(raw)
    if key in config:
        return int(config[key])
    raise ValueError(f"{env_name} env var or '{key}' in config file is required")


def _split_diff_sections(diff_text: str) -> list[str]:
    """Break a unified diff into sections starting at each `diff --git` line."""
    if not diff_text.strip():
        return []
    sections: list[list[str]] = []
    current: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("diff --git ") and current:
            sections.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(current)
    return ["\n".join(section).strip("\n") for section in sections if any(item.strip() for item in section)]


def _chunk_by_sections(
    sections: Sequence[str],
    max_lines: int,
    max_chunks: int,
) -> list[str]:
    """Group diff sections into chunks respecting the max line budget."""
    if not sections:
        return []
    if max_chunks <= 1:
        return ["\n\n".join(sections)]

    def should_start_new_chunk(
        has_current: bool,
        current_lines: int,
        section_lines: int,
        chunk_count: int,
    ) -> bool:
        if not has_current or max_lines <= 0:
            return False
        if chunk_count >= max_chunks - 1:
            return False
        return current_lines + section_lines > max_lines

    chunks: list[list[str]] = []
    current: list[str] = []
    current_lines = 0
    for section in sections:
        section_lines = section.count("\n") + 1
        if should_start_new_chunk(bool(current), current_lines, section_lines, len(chunks)):
            chunks.append(current)
            current = []
            current_lines = 0
        current.append(section)
        current_lines += section_lines
    if current:
        chunks.append(current)
    return ["\n\n".join(chunk).strip("\n") for chunk in chunks if any(line.strip() for line in chunk)]


def _chunk_by_lines(diff_text: str, chunk_count: int) -> list[str]:
    """Chunk by raw line count when section-based grouping produces too few chunks."""
    lines = diff_text.splitlines()
    if not lines:
        return []
    chunk_count = max(1, chunk_count)
    chunk_size = max(1, math.ceil(len(lines) / chunk_count))
    chunks: list[str] = []
    for start in range(0, len(lines), chunk_size):
        chunk_lines = lines[start : start + chunk_size]
        if chunk_lines:
            chunks.append("\n".join(chunk_lines).strip("\n"))
    if not chunks:
        return [diff_text]
    return chunks


def _chunk_diff(
    diff_text: str,
    max_chunk_lines: int,
    max_chunks: int,
) -> list[str]:
    """Split the staged diff into manageable chunks for Codex."""
    sanitized_max_lines = max(0, max_chunk_lines)
    sanitized_max_chunks = max(1, max_chunks)
    total_lines = diff_text.count("\n") + 1
    if sanitized_max_lines == 0 or sanitized_max_chunks == 1 or total_lines <= sanitized_max_lines:
        return [diff_text]

    sections = _split_diff_sections(diff_text)
    if not sections:
        sections = [diff_text]

    chunks = _chunk_by_sections(sections, sanitized_max_lines, sanitized_max_chunks)
    if len(chunks) == 1 and total_lines > sanitized_max_lines:
        chunk_target = min(
            sanitized_max_chunks,
            max(2, math.ceil(total_lines / sanitized_max_lines)),
        )
        chunks = _chunk_by_lines(diff_text, chunk_target)
    if not chunks:
        return [diff_text]
    return chunks


def _build_chunk_summary_diff(
    chunk_results: Sequence[Tuple[str, Sequence[str]]],
) -> str:
    """Create a synthetic diff from chunk-level summaries for the final Codex pass."""
    lines: list[str] = []
    for index, (summary, body_lines) in enumerate(chunk_results, start=1):
        lines.append(f"diff --git a/chunk_{index} b/chunk_{index}")
        lines.append(f"--- a/chunk_{index}")
        lines.append(f"+++ b/chunk_{index}")
        summary_text = summary.strip()
        if summary_text:
            lines.append(f"+ chunk {index} summary: {summary_text}")
        for entry in body_lines:
            entry_text = entry.strip()
            if entry_text:
                lines.append(f"+ {entry_text}")
        lines.append("")
    synthesized = "\n".join(lines).strip()
    if synthesized:
        return synthesized
    return "+ chunk summary unavailable"


def _request_with_chunking(
    *,
    chunks: Sequence[str],
    model: str,
    reasoning_effort: str,
    detailed: bool,
) -> tuple[str, list[str]]:
    """Run multiple Codex requests across diff chunks and synthesize the final message."""
    total_chunks = len(chunks)
    approx_lines = sum(chunk.count("\n") + 1 for chunk in chunks) // max(total_chunks, 1)
    print(
        ("[ci_shared] Large staged diff detected; splitting into " f"{total_chunks} Codex prompts (~{approx_lines} lines each)."),
        file=sys.stderr,
    )
    chunk_summaries: list[tuple[str, list[str]]] = []

    for index, chunk in enumerate(chunks, start=1):
        chunk_lines = chunk.count("\n") + 1
        print(
            f"[ci_shared] Requesting commit summary for chunk {index}/{total_chunks} ({chunk_lines} lines)...",
            file=sys.stderr,
        )
        extra_context = (
            f"This prompt contains chunk {index}/{total_chunks} of the staged diff. Summarize only the changes visible in this chunk."
        )
        try:
            summary, body_lines = request_commit_message(
                model=model,
                reasoning_effort=reasoning_effort,
                staged_diff=chunk,
                extra_context=extra_context,
                detailed=False,
            )
        except Exception as exc:
            print(
                f"[ci_shared] Chunk {index}/{total_chunks} FAILED: {exc}",
                file=sys.stderr,
            )
            raise
        print(
            f"[ci_shared] Chunk {index}/{total_chunks} succeeded.",
            file=sys.stderr,
        )
        chunk_summaries.append(
            (
                summary.strip(),
                [line.strip() for line in body_lines if line.strip()],
            )
        )

    synthesized_diff = _build_chunk_summary_diff(chunk_summaries)
    aggregate_context = (
        "The diff shown above is a synthesized summary constructed from multiple chunks "
        "of the original staged changes. Produce a single cohesive commit message that "
        "covers the entire change-set."
    )
    print(
        f"[ci_shared] Requesting final synthesis from {total_chunks} chunk summaries...",
        file=sys.stderr,
    )
    try:
        result = request_commit_message(
            model=model,
            reasoning_effort=reasoning_effort,
            staged_diff=synthesized_diff,
            extra_context=aggregate_context,
            detailed=detailed,
        )
    except Exception as exc:
        print(
            f"[ci_shared] Final synthesis FAILED: {exc}",
            file=sys.stderr,
        )
        raise
    print("[ci_shared] Final synthesis succeeded.", file=sys.stderr)
    return result


def main(argv: list[str] | None = None) -> int:
    """Main entry point for commit message generation."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    commit_config = get_commit_config()

    model_arg = _resolve_model_arg(args.model, commit_config)
    if not model_arg:
        print(
            "Model must be specified via --model, CI_COMMIT_MODEL env var, or commit_message.model in ci_shared.config.json",
            file=sys.stderr,
        )
        return 1

    reasoning_arg = _resolve_reasoning_arg(args.reasoning, commit_config)
    if not reasoning_arg:
        print(
            "Reasoning must be specified via --reasoning, CI_COMMIT_REASONING env var, "
            "or commit_message.reasoning in ci_shared.config.json",
            file=sys.stderr,
        )
        return 1

    model = resolve_model_choice(model_arg, validate=False)
    reasoning = resolve_reasoning_choice(reasoning_arg, validate=False)

    staged_diff = _read_staged_diff()
    if not staged_diff.strip():
        print("No staged diff available for commit message generation.", file=sys.stderr)
        return 1

    max_chunk_lines = _get_config_int(commit_config, "chunk_line_limit", "CI_CODEX_COMMIT_CHUNK_LINE_LIMIT")
    max_chunks = _get_config_int(commit_config, "max_chunks", "CI_CODEX_COMMIT_MAX_CHUNKS")
    chunks = _chunk_diff(staged_diff, max_chunk_lines, max_chunks)

    summary, body_lines = _generate_commit_message(
        chunks=chunks,
        staged_diff=staged_diff,
        model=model,
        reasoning=reasoning,
        detailed=args.detailed,
    )

    summary = summary.strip()
    if not summary:
        print("Codex commit message response was empty.", file=sys.stderr)
        return 1

    payload = _prepare_payload(summary, body_lines)
    result = _write_payload(payload, args.output)
    if result is not None:
        return result
    return 0


def _generate_commit_message(
    *,
    chunks: list[str],
    staged_diff: str,
    model: str,
    reasoning: str,
    detailed: bool,
) -> tuple[str, list[str]]:
    """Generate commit message, using chunking if needed."""
    if len(chunks) == 1:
        return request_commit_message(
            model=model,
            reasoning_effort=reasoning,
            staged_diff=staged_diff,
            extra_context="",
            detailed=detailed,
        )
    return _request_with_chunking(
        chunks=chunks,
        model=model,
        reasoning_effort=reasoning,
        detailed=detailed,
    )


if __name__ == "__main__":
    raise SystemExit(main())
