"""Configuration helpers and constants for the CI runtime."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable, Optional

from ci_tools.ci_runtime.models import ModelSelectionAbort, ReasoningEffortAbort
from ci_tools.scripts.config_loader import load_json_config
from ci_tools.scripts.guard_common import detect_repo_root

CONFIG_CANDIDATES = ("ci_shared.config.json", ".ci_shared.config.json")
DEFAULT_PROTECTED_PATH_PREFIXES: tuple[str, ...] = (
    "ci.py",
    "ci_tools/",
    "scripts/ci.sh",
    "Makefile",
)
RISKY_PATTERNS = (
    re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE),
    re.compile(r"rm\s+-rf"),
    re.compile(r"subprocess\.run\([^)]*['\"]rm['\"]"),
)
REQUIRED_MODEL = "gpt-5-codex"
REASONING_EFFORT_CHOICES: tuple[str, ...] = ("low", "medium", "high")
DEFAULT_REASONING_EFFORT = "high"

_EMPTY_CONFIG: dict[str, Any] = {}


def load_repo_config(repo_root: Path) -> dict[str, Any]:
    """Load shared CI configuration when available.

    Delegates to the shared load_json_config implementation in config_loader.
    Returns empty dict if no config file exists; raises on parse errors.
    """
    try:
        return load_json_config(repo_root, CONFIG_CANDIDATES)
    except FileNotFoundError:
        return _EMPTY_CONFIG


def _coerce_repo_context(config: dict[str, Any], initial: str) -> str:
    raw = config.get("repo_context")
    if isinstance(raw, str):
        return raw
    return initial


def _coerce_protected_prefixes(
    config: dict[str, Any],
    initial: Iterable[str],
) -> tuple[str, ...]:
    raw = config.get("protected_path_prefixes")
    if isinstance(raw, (list, tuple, set)):
        return tuple(str(item) for item in raw)
    return tuple(initial)


def _coerce_coverage_threshold(config: dict[str, Any], initial: float) -> float:
    raw = config.get("coverage_threshold")
    if isinstance(raw, (int, float, str)):
        try:
            return float(raw)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return initial
    return initial


def resolve_model_choice(
    model_arg: Optional[str] = None, *, validate: bool = True
) -> str:
    """Resolve the Codex model to use.

    Args:
        model_arg: Model specified via CLI argument
        validate: If True, raise exception if model doesn't match REQUIRED_MODEL

    Returns:
        Resolved model name

    Raises:
        ValueError: If validate=True and model doesn't match REQUIRED_MODEL
        ModelSelectionAbort: If no model is provided via argument or environment
    """
    candidate = model_arg
    if not candidate:
        candidate = os.environ.get("OPENAI_MODEL")
    if not candidate:
        raise ModelSelectionAbort.unsupported_model(
            received="(none)", required=REQUIRED_MODEL
        )
    if validate and candidate != REQUIRED_MODEL:
        raise ModelSelectionAbort.unsupported_model(
            received=candidate, required=REQUIRED_MODEL
        )
    os.environ["OPENAI_MODEL"] = candidate
    return candidate


def resolve_reasoning_choice(
    reasoning_arg: Optional[str] = None, *, validate: bool = True
) -> str:
    """Resolve the reasoning effort level to use.

    Args:
        reasoning_arg: Reasoning effort specified via CLI argument
        validate: If True, raise exception if choice is not valid

    Returns:
        Resolved reasoning effort (low/medium/high)

    Raises:
        ValueError: If validate=True and choice is not in REASONING_EFFORT_CHOICES
        ReasoningEffortAbort: If no reasoning effort is provided via argument or environment
    """
    candidate = reasoning_arg
    if not candidate:
        env_reasoning = os.environ.get("OPENAI_REASONING_EFFORT")
        if env_reasoning:
            candidate = env_reasoning.lower()
    if not candidate:
        raise ReasoningEffortAbort.unsupported_choice(
            received="(none)", allowed=REASONING_EFFORT_CHOICES
        )
    if validate and candidate not in REASONING_EFFORT_CHOICES:
        raise ReasoningEffortAbort.unsupported_choice(
            received=candidate, allowed=REASONING_EFFORT_CHOICES
        )
    os.environ["OPENAI_REASONING_EFFORT"] = candidate
    return candidate


REPO_ROOT = detect_repo_root()
REPO_CONFIG = load_repo_config(REPO_ROOT)
DEFAULT_REPO_CONTEXT = (
    "You are assisting with continuous integration fixes for this repository.\n"
    "Repository facts:\n"
    "- Python 3.10+ project using PEP 8 conventions and four-space indentation.\n"
    "- Source lives under src/, tests mirror that structure under tests/.\n"
    "- Avoid committing secrets, install dependencies via scripts/requirements.txt when needed,\n"
    "  and prefer focused edits rather than sweeping rewrites.\n"
    "When CI fails, respond with a unified diff (a/ b/ prefixes) that can be applied with\n"
    "`patch -p1`. Keep the patch minimal, and mention any follow-up steps if the fix\n"
    "requires manual verification."
)
REPO_CONTEXT = _coerce_repo_context(REPO_CONFIG, DEFAULT_REPO_CONTEXT)
PROTECTED_PATH_PREFIXES = _coerce_protected_prefixes(
    REPO_CONFIG, DEFAULT_PROTECTED_PATH_PREFIXES
)
COVERAGE_THRESHOLD = _coerce_coverage_threshold(REPO_CONFIG, 80.0)


__all__ = [
    "CONFIG_CANDIDATES",
    "DEFAULT_PROTECTED_PATH_PREFIXES",
    "RISKY_PATTERNS",
    "REQUIRED_MODEL",
    "REASONING_EFFORT_CHOICES",
    "DEFAULT_REASONING_EFFORT",
    "detect_repo_root",
    "load_repo_config",
    "resolve_model_choice",
    "resolve_reasoning_choice",
    "REPO_ROOT",
    "REPO_CONFIG",
    "REPO_CONTEXT",
    "PROTECTED_PATH_PREFIXES",
    "COVERAGE_THRESHOLD",
]
