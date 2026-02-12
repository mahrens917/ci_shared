"""Tests for scripts/list_consumers.py."""

from __future__ import annotations

from pathlib import Path

from scripts.list_consumers import main


def _write_config(tmp_path: Path, data: str) -> None:
    (tmp_path / "ci_shared.config.json").write_text(data, encoding="utf-8")


def test_main_prints_repo_paths(tmp_path: Path, capsys):
    _write_config(tmp_path, '{"consuming_repositories": [{"name": "alpha", "path": "/tmp/alpha"}]}')
    result = main([str(tmp_path)])
    assert result == 0
    captured = capsys.readouterr()
    assert "/tmp/alpha" in captured.out


def test_main_no_args_uses_ci_shared_root(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("scripts.list_consumers.CI_SHARED_ROOT", tmp_path)
    _write_config(tmp_path, '{"consuming_repositories": [{"name": "beta", "path": "/tmp/beta"}]}')
    result = main([])
    assert result == 0
