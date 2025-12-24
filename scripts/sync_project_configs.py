#!/usr/bin/env python3
"""Sync shared CI config files into one or more project directories.

Typical usage:
    python scripts/sync_project_configs.py ~/zeus ~/aws ~/kalshi ~/common ~/peak

The script copies a curated set of config files from the local ci_shared repo
into each target project. Files are only overwritten when the source content
differs, making the sync idempotent. Pass --dry-run to see what would change
without modifying any files.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from ci_tools.utils.consumers import load_consuming_repos

# Files we keep in sync across repos by default
DEFAULT_FILES = [
    ".gitleaks.toml",
    "ci_shared.mk",
    "shared-tool-config.toml",
]

PROXY_MAPPINGS = {
    "ci_tools_proxy/__init__.py": Path("ci_tools") / "__init__.py",
    "scripts_proxy/ci.sh": Path("scripts") / "ci.sh",
}

DEFAULT_SUBDIRS = ["ci_shared"]


@dataclass
class SyncResult:
    project: Path
    target_root: Path
    file: Path
    action: str
    message: str = ""


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copy shared CI configs into multiple project directories.")
    parser.add_argument(
        "projects",
        nargs="*",
        help=(
            "Absolute or relative paths to project directories that need updates. "
            "If omitted, repositories from ci_shared.config.json are used."
        ),
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Directory that contains the canonical config files (default: this repo).",
    )
    parser.add_argument(
        "--file",
        dest="files",
        action="append",
        default=None,
        help=("Specific relative file to sync. Can be supplied multiple times. " "Defaults to a curated list if omitted."),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the planned actions without copying any files.",
    )
    parser.add_argument(
        "--backup-suffix",
        default="",
        help=(
            "Optional suffix to append when writing backups of overwritten files " "(e.g. '.bak'). Empty string disables backups (default)."
        ),
    )
    parser.add_argument(
        "--subdir",
        dest="subdirs",
        action="append",
        default=None,
        help=(
            "Relative subdirectory inside each project that should also be updated "
            "(default: auto-detect 'ci_shared' if it exists). Repeat for multiples."
        ),
    )
    parser.add_argument(
        "--skip-default-subdirs",
        action="store_true",
        help="Disable the automatic inclusion of the 'ci_shared' subdirectory.",
    )
    return parser.parse_args(argv)


def compute_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def copy_with_backup(src: Path, dest: Path, backup_suffix: str) -> None:
    if dest.exists() and backup_suffix:
        backup_path = dest.with_name(dest.name + backup_suffix)
        shutil.copy2(dest, backup_path)
    shutil.copy2(src, dest)


def sync_file(
    project: Path,
    target_root: Path,
    src: Path,
    dest: Path,
    dry_run: bool,
    backup_suffix: str,
) -> SyncResult:
    if not src.exists():
        return SyncResult(project, target_root, dest, "skipped", "source file missing")

    if not dest.exists():
        if dry_run:
            return SyncResult(project, target_root, dest, "create", "dry-run")

        dest.parent.mkdir(parents=True, exist_ok=True)
        copy_with_backup(src, dest, backup_suffix)
        return SyncResult(project, target_root, dest, "created")

    src_hash = compute_digest(src)
    dest_hash = compute_digest(dest)
    if src_hash == dest_hash:
        return SyncResult(project, target_root, dest, "up-to-date")

    if dry_run:
        return SyncResult(project, target_root, dest, "update", "dry-run")

    copy_with_backup(src, dest, backup_suffix)
    return SyncResult(project, target_root, dest, "updated")


def sync_target_root(
    project_root: Path,
    target_root: Path,
    source_root: Path,
    files: List[str],
    dry_run: bool,
    backup_suffix: str,
) -> List[SyncResult]:
    results: List[SyncResult] = []
    for rel_path in files:
        src = source_root / rel_path
        dest = target_root / rel_path
        results.append(sync_file(project_root, target_root, src, dest, dry_run, backup_suffix))
    return results


def sync_proxy_files(
    project_root: Path,
    source_root: Path,
    dry_run: bool,
    backup_suffix: str,
) -> list[SyncResult]:
    """Copy special files (like the ci_tools proxy) into the project root."""
    results: list[SyncResult] = []
    for src_rel, dest_rel in PROXY_MAPPINGS.items():
        src = source_root / src_rel
        dest = project_root / dest_rel
        dest_parent = dest.parent
        if not dest_parent.exists() and not dry_run:
            dest_parent.mkdir(parents=True, exist_ok=True)
        results.append(sync_file(project_root, dest_parent, src, dest, dry_run, backup_suffix))
    return results


def main(argv: Iterable[str]) -> int:
    args = parse_args(argv)
    source_root = args.source_root.resolve()
    files = args.files or DEFAULT_FILES
    subdirs: List[str] = []
    if not args.skip_default_subdirs:
        subdirs.extend(DEFAULT_SUBDIRS)
    if args.subdirs:
        subdirs.extend(args.subdirs)

    if not source_root.exists():
        print(f"[error] Source root {source_root} does not exist", file=sys.stderr)
        return 2

    summary: List[SyncResult] = []
    projects = args.projects
    if not projects:
        projects = [repo.path for repo in load_consuming_repos(source_root)]

    if not projects:
        print("[warning] No consuming repositories configured; nothing to sync.")
        return 0

    for project in projects:
        project_root = Path(project).expanduser().resolve()
        if not project_root.exists():
            summary.append(SyncResult(project_root, project_root, Path("."), "skipped", "project missing"))
            continue

        summary.extend(
            sync_target_root(
                project_root,
                project_root,
                source_root,
                files,
                args.dry_run,
                args.backup_suffix,
            )
        )
        summary.extend(sync_proxy_files(project_root, source_root, args.dry_run, args.backup_suffix))

        for subdir in subdirs:
            target_root = project_root / subdir
            if not target_root.exists():
                continue
            summary.extend(
                sync_target_root(
                    project_root,
                    target_root,
                    source_root,
                    files,
                    args.dry_run,
                    args.backup_suffix,
                )
            )

    print("\nSync results:")
    for result in summary:
        try:
            rel_target = result.target_root.relative_to(result.project)
            project_label = result.project.name if rel_target == Path(".") else f"{result.project.name}/{rel_target}"
        except ValueError:
            project_label = result.target_root.as_posix()

        destination = result.file.relative_to(result.target_root)
        note = f" ({result.message})" if result.message else ""
        print(f"  [{project_label}] {destination}: {result.action}{note}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
