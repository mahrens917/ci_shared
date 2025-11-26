"""Environment loading utilities for the CI runtime."""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str) -> dict[str, str]:
    """Parse a simple KEY=VALUE dotenv file into a dictionary."""
    env_path = Path(path).expanduser()
    if not env_path.is_file():
        return {}
    content = env_path.read_text(encoding="utf-8")
    result: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def load_env_settings(env_path: str) -> None:
    """Populate os.environ with values from a dotenv-style file.

    Only sets values that are not already present in the environment.
    """
    env_values = load_env_file(env_path)
    for key, value in env_values.items():
        if key not in os.environ:
            os.environ[key] = value


__all__ = ["load_env_file", "load_env_settings"]
