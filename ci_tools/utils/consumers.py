"""Helpers for resolving consuming repositories that share ci_shared."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

from ci_tools.ci_runtime.config import CONFIG_CANDIDATES
from ci_tools.scripts.config_loader import ConfigLoadError, load_json_config


class MissingConsumersConfigError(ConfigLoadError):
    """Raised when no consuming repositories configuration is found."""

    def __init__(self, repo_root: Path) -> None:
        super().__init__(
            f"No consuming repositories configured. "
            f"Set CI_SHARED_PROJECTS env var or add 'consuming_repositories' "
            f"to config file in {repo_root}"
        )


@dataclass(frozen=True)
class ConsumingRepo:
    """Represents a repository that should receive ci_shared updates."""

    name: str
    path: Path


def _load_config(repo_root: Path) -> dict | None:
    """Load configuration from repo root.

    Delegates to canonical load_json_config implementation in config_loader.
    Returns None if no config file exists; raises on parse errors.
    """
    try:
        return load_json_config(repo_root, CONFIG_CANDIDATES)
    except FileNotFoundError:
        return None
    except ConfigLoadError:
        raise


def _coerce_repo_entry(
    repo_root: Path,
    *,
    name: str,
    raw_path: str | None,
) -> ConsumingRepo:
    if raw_path:
        resolved = Path(raw_path).expanduser()
        if not resolved.is_absolute():
            resolved = (repo_root / raw_path).resolve()
    else:
        resolved = (repo_root.parent / name).resolve()
    return ConsumingRepo(name=name, path=resolved)


def _load_from_config(repo_root: Path, config: dict) -> List[ConsumingRepo]:
    raw_entries = config.get("consuming_repositories")
    if not isinstance(raw_entries, Sequence):
        return []

    repos: list[ConsumingRepo] = []
    for entry in raw_entries:
        if isinstance(entry, str):
            repos.append(_coerce_repo_entry(repo_root, name=entry, raw_path=None))
            continue
        if isinstance(entry, dict):
            name = entry.get("name")
            path_value = entry.get("path")
            if isinstance(name, str):
                repos.append(
                    _coerce_repo_entry(
                        repo_root,
                        name=name,
                        raw_path=path_value if isinstance(path_value, str) else None,
                    )
                )
    return repos


def _load_from_env(repo_root: Path, env_value: str) -> List[ConsumingRepo]:
    repos: list[ConsumingRepo] = []
    for token in shlex.split(env_value):
        path = Path(token).expanduser()
        name = path.name
        if not path.is_absolute():
            path = (repo_root / token).resolve()
        repos.append(ConsumingRepo(name=name, path=path))
    return repos


def load_consuming_repos(repo_root: Path | None = None) -> List[ConsumingRepo]:
    """Resolve consuming repositories from config or environment.

    Raises:
        MissingConsumersConfigError: When no configuration source provides
            consuming repositories.
    """
    repo_root = repo_root.resolve() if repo_root else Path.cwd().resolve()
    env_value = os.environ.get("CI_SHARED_PROJECTS")
    if isinstance(env_value, str):
        env_override = env_value.strip()
    else:
        env_override = ""
    if env_override:
        env_repos = _load_from_env(repo_root, env_override)
        if env_repos:
            return env_repos

    config = _load_config(repo_root)
    if config:
        config_repos = _load_from_config(repo_root, config)
        if config_repos:
            return config_repos

    raise MissingConsumersConfigError(repo_root)


__all__ = [
    "ConsumingRepo",
    "MissingConsumersConfigError",
    "load_consuming_repos",
    "CONFIG_CANDIDATES",
]
