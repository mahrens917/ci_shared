"""Utility helpers to load shared magic values for tests."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def _load_constants() -> dict[str, Any]:
    """Load test constants from the JSON configuration file."""
    constants_path = Path(__file__).resolve().parents[1] / "tests" / "test_constants.json"
    with constants_path.open(encoding="utf-8") as handle:
        return json.load(handle)


def get_constant(*keys: str) -> Any:
    """Return a constant value by walking the nested JSON keys."""
    value: Any = _load_constants()
    for key in keys:
        value = value[key]
    return value
