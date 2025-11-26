"""Tests for consuming repository helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from ci_tools.utils.consumers import MissingConsumersConfigError, load_consuming_repos


def write_config(tmp_path: Path, data: str) -> None:
    """Helper to write a test configuration file."""
    (tmp_path / "ci_shared.config.json").write_text(data, encoding="utf-8")


def test_load_from_config_with_paths(tmp_path: Path):
    """Test loading consuming repositories from config with explicit paths."""
    write_config(
        tmp_path,
        """
{"consuming_repositories": [
    {"name": "api", "path": "../api"},
    {"name": "custom", "path": "/opt/custom"}
]}
""",
    )
    repos = load_consuming_repos(tmp_path)
    assert len(repos) == 2
    assert repos[0].name == "api"
    assert repos[0].path == (tmp_path.parent / "api").resolve()
    assert repos[1].name == "custom"
    assert repos[1].path == Path("/opt/custom").resolve()


def test_load_from_config_with_strings(tmp_path: Path):
    """Test loading consuming repositories from config with string names."""
    write_config(
        tmp_path,
        """
{"consuming_repositories": ["alpha", "beta"]}
""",
    )
    repos = load_consuming_repos(tmp_path)
    assert [repo.name for repo in repos] == ["alpha", "beta"]
    assert repos[0].path == (tmp_path.parent / "alpha").resolve()


def test_load_from_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test that environment variable overrides the config file."""
    write_config(
        tmp_path,
        """
{"consuming_repositories": ["alpha"]}
""",
    )
    env_path = tmp_path / "custom"
    env_path.mkdir()
    monkeypatch.setenv("CI_SHARED_PROJECTS", str(env_path))
    repos = load_consuming_repos(tmp_path)
    assert len(repos) == 1
    assert repos[0].name == "custom"
    assert repos[0].path == env_path.resolve()


def test_raises_when_config_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test that an error is raised when no config source provides repositories."""
    monkeypatch.delenv("CI_SHARED_PROJECTS", raising=False)
    with pytest.raises(MissingConsumersConfigError) as exc_info:
        load_consuming_repos(tmp_path)
    assert "No consuming repositories configured" in str(exc_info.value)
