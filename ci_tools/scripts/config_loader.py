"""Shared JSON configuration loading helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ConfigLoadError(Exception):
    """Raised when configuration loading fails."""


def load_json_config(
    repo_root: Path,
    candidates: tuple[str, ...],
) -> dict[str, Any]:
    """Load JSON configuration from the first available candidate file.

    Raises:
        ConfigLoadError: If a config file exists but cannot be parsed.
        FileNotFoundError: If no candidate config files exist.
    """
    for candidate_name in candidates:
        candidate_path = repo_root / candidate_name
        if not candidate_path.is_file():
            continue
        try:
            with candidate_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as exc:
            msg = f"Failed to parse {candidate_path}: {exc}"
            raise ConfigLoadError(msg) from exc
        if not isinstance(data, dict):
            msg = f"Expected dict in {candidate_path}, got {type(data).__name__}"
            raise ConfigLoadError(msg)
        return data
    searched = ", ".join(candidates)
    msg = f"No config file found in {repo_root}; searched: {searched}"
    raise FileNotFoundError(msg)
