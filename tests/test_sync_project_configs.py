"""Tests for scripts/sync_project_configs.py."""

from __future__ import annotations

from pathlib import Path
from typing import List

from scripts.sync_project_configs import (
    SyncResult,
    compute_digest,
    copy_with_backup,
    main,
    parse_args,
    print_summary,
    resolve_subdirs,
    sync_file,
    sync_project,
    sync_proxy_files,
    sync_target_root,
)


def _write_config(tmp_path: Path, data: str) -> None:
    (tmp_path / "ci_shared.config.json").write_text(data, encoding="utf-8")


def test_parse_args_minimal():
    args = parse_args(["/some/path"])
    assert args.projects == ["/some/path"]
    assert args.dry_run is False
    assert args.backup_suffix == ""


def test_parse_args_dry_run():
    args = parse_args(["--dry-run", "/a"])
    assert args.dry_run is True


def test_parse_args_files():
    args = parse_args(["--file", "a.toml", "--file", "b.toml", "/proj"])
    assert args.files == ["a.toml", "b.toml"]


def test_parse_args_subdirs():
    args = parse_args(["--subdir", "extra", "/proj"])
    assert args.subdirs == ["extra"]


def test_parse_args_skip_default_subdirs():
    args = parse_args(["--skip-default-subdirs", "/proj"])
    assert args.skip_default_subdirs is True


def test_compute_digest(tmp_path: Path):
    f = tmp_path / "test.txt"
    f.write_text("hello", encoding="utf-8")
    digest = compute_digest(f)
    assert isinstance(digest, str)
    assert len(digest) == 64  # sha256 hex


def test_compute_digest_deterministic(tmp_path: Path):
    f = tmp_path / "test.txt"
    f.write_text("deterministic content", encoding="utf-8")
    assert compute_digest(f) == compute_digest(f)


def test_copy_with_backup_no_suffix(tmp_path: Path):
    src = tmp_path / "src.txt"
    dest = tmp_path / "dest.txt"
    src.write_text("new", encoding="utf-8")
    dest.write_text("old", encoding="utf-8")
    copy_with_backup(src, dest, "")
    assert dest.read_text(encoding="utf-8") == "new"
    assert not (tmp_path / "dest.txt.bak").exists()


def test_copy_with_backup_with_suffix(tmp_path: Path):
    src = tmp_path / "src.txt"
    dest = tmp_path / "dest.txt"
    src.write_text("new", encoding="utf-8")
    dest.write_text("old", encoding="utf-8")
    copy_with_backup(src, dest, ".bak")
    assert dest.read_text(encoding="utf-8") == "new"
    assert (tmp_path / "dest.txt.bak").read_text(encoding="utf-8") == "old"


def test_sync_file_source_missing(tmp_path: Path):
    result = sync_file(tmp_path, tmp_path, tmp_path / "missing.txt", tmp_path / "dest.txt", False, "")
    assert result.action == "skipped"
    assert "source file missing" in result.message


def test_sync_file_creates_new(tmp_path: Path):
    src = tmp_path / "src.txt"
    src.write_text("content", encoding="utf-8")
    dest = tmp_path / "out" / "dest.txt"
    result = sync_file(tmp_path, tmp_path, src, dest, False, "")
    assert result.action == "created"
    assert dest.read_text(encoding="utf-8") == "content"


def test_sync_file_creates_dry_run(tmp_path: Path):
    src = tmp_path / "src.txt"
    src.write_text("content", encoding="utf-8")
    dest = tmp_path / "out" / "dest.txt"
    result = sync_file(tmp_path, tmp_path, src, dest, True, "")
    assert result.action == "create"
    assert not dest.exists()


def test_sync_file_up_to_date(tmp_path: Path):
    src = tmp_path / "src.txt"
    dest = tmp_path / "dest.txt"
    src.write_text("same", encoding="utf-8")
    dest.write_text("same", encoding="utf-8")
    result = sync_file(tmp_path, tmp_path, src, dest, False, "")
    assert result.action == "up-to-date"


def test_sync_file_updates(tmp_path: Path):
    src = tmp_path / "src.txt"
    dest = tmp_path / "dest.txt"
    src.write_text("new version", encoding="utf-8")
    dest.write_text("old version", encoding="utf-8")
    result = sync_file(tmp_path, tmp_path, src, dest, False, "")
    assert result.action == "updated"
    assert dest.read_text(encoding="utf-8") == "new version"


def test_sync_file_update_dry_run(tmp_path: Path):
    src = tmp_path / "src.txt"
    dest = tmp_path / "dest.txt"
    src.write_text("new version", encoding="utf-8")
    dest.write_text("old version", encoding="utf-8")
    result = sync_file(tmp_path, tmp_path, src, dest, True, "")
    assert result.action == "update"
    assert dest.read_text(encoding="utf-8") == "old version"


def test_sync_target_root(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    (source / "a.toml").write_text("data", encoding="utf-8")
    results = sync_target_root(target, target, source, ["a.toml"], False, "")
    assert len(results) == 1
    assert results[0].action == "created"


def test_sync_proxy_files(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "ci_tools_proxy").mkdir()
    (source / "ci_tools_proxy" / "__init__.py").write_text("proxy", encoding="utf-8")
    (source / "scripts_proxy").mkdir()
    (source / "scripts_proxy" / "ci.sh").write_text("#!/bin/bash", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    results = sync_proxy_files(project, source, False, "")
    assert len(results) == 2


def test_resolve_subdirs_defaults():
    args = parse_args(["/proj"])
    subdirs = resolve_subdirs(args)
    assert "ci_shared" in subdirs


def test_resolve_subdirs_skip_defaults():
    args = parse_args(["--skip-default-subdirs", "/proj"])
    subdirs = resolve_subdirs(args)
    assert subdirs == []


def test_resolve_subdirs_with_custom():
    args = parse_args(["--subdir", "extra", "/proj"])
    subdirs = resolve_subdirs(args)
    assert "ci_shared" in subdirs
    assert "extra" in subdirs


def test_sync_project(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.toml").write_text("data", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    results = sync_project(project, source, ["a.toml"], [], False, "")
    created = [r for r in results if r.action == "created"]
    assert len(created) >= 1


def test_sync_project_with_subdir(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.toml").write_text("data", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "sub").mkdir()
    results = sync_project(project, source, ["a.toml"], ["sub"], False, "")
    assert len(results) >= 2


def test_print_summary(capsys):
    results: List[SyncResult] = [
        SyncResult(Path("/proj"), Path("/proj"), Path("/proj/a.toml"), "created"),
        SyncResult(Path("/proj"), Path("/proj"), Path("/proj/b.toml"), "up-to-date", "already there"),
    ]
    print_summary(results)
    captured = capsys.readouterr()
    assert "created" in captured.out
    assert "up-to-date" in captured.out


def test_print_summary_with_subdir(capsys):
    proj = Path("/proj")
    target = Path("/proj/sub")
    results = [SyncResult(proj, target, target / "a.toml", "updated")]
    print_summary(results)
    captured = capsys.readouterr()
    assert "proj/sub" in captured.out


def test_main_missing_source_root(tmp_path: Path):
    result = main(["--source-root", str(tmp_path / "nonexistent"), "/proj"])
    assert result == 2


def test_main_with_explicit_projects(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    (source / ".gitleaks.toml").write_text("# gitleaks", encoding="utf-8")
    result = main(["--source-root", str(source), "--file", ".gitleaks.toml", str(project)])
    assert result == 0


def test_main_project_missing(tmp_path: Path):
    result = main(["--source-root", str(tmp_path), str(tmp_path / "nonexistent")])
    assert result == 0


def test_main_syncs_project(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / ".gitleaks.toml").write_text("# gitleaks", encoding="utf-8")
    (source / "ci_shared.mk").write_text("# makefile", encoding="utf-8")
    (source / "shared-tool-config.toml").write_text("# config", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    result = main(["--source-root", str(source), str(project)])
    assert result == 0
    assert (project / ".gitleaks.toml").exists()
