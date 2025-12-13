#!/usr/bin/env python3
"""
Propagate ci_shared updates to consuming repositories.

After ci_shared is successfully pushed, this script copies the canonical CI files
into all consuming repositories (api, zeus, kalshi, aws, common, peak) and pushes the changes.
Repositories that are explicitly blocked (for example, personal chess/tictactoe
checkouts) are skipped automatically to avoid unintended pushes.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from ci_tools.ci_runtime.process import (
    get_commit_message,
    get_current_branch,
    run_command,
)
from ci_tools.utils.consumers import ConsumingRepo, load_consuming_repos

BLOCKED_CONSUMER_NAMES = frozenset({"chess", "tictactoe"})

CI_SHARED_ROOT = Path(__file__).resolve().parents[2]
CI_SHARED_CONFIG_PATH = CI_SHARED_ROOT / "ci_shared.config.json"
DEFAULT_CLAUDE_FALLBACK_MODEL = "claude-sonnet-4-20250514"


def _filter_blocked_consumers(
    consuming_repos: Iterable[ConsumingRepo],
) -> tuple[list[ConsumingRepo], list[ConsumingRepo]]:
    """Remove blocked repositories from the consuming repos list."""
    allowed: list[ConsumingRepo] = []
    blocked: list[ConsumingRepo] = []

    for repo in consuming_repos:
        if repo.name.lower() in BLOCKED_CONSUMER_NAMES:
            blocked.append(repo)
            continue
        allowed.append(repo)

    return allowed, blocked


def _validate_repo_state(repo_path: Path, repo_name: str) -> bool:
    """Check if repo exists and auto-commit any uncommitted changes."""
    if not repo_path.exists():
        print(f"⚠️  Repository not found: {repo_path}")
        return False

    result = run_command(
        ["git", "status", "--porcelain"],
        cwd=repo_path,
        check=False,
    )
    if result.stdout.strip():
        print(f"📝 {repo_name} has uncommitted changes, committing automatically...")

        # Stage all changes
        run_command(["git", "add", "-A"], cwd=repo_path, check=False)

        # Create commit message
        commit_msg = """Auto-commit before ci_shared update

🤖 Generated with the ci_shared commit message helper

Co-Authored-By: ci_shared-bot <noreply@ci_shared>"""

        # Commit changes
        commit_result = run_command(
            ["git", "commit", "-m", commit_msg],
            cwd=repo_path,
            check=False,
        )

        if commit_result.returncode != 0:
            print(f"⚠️  Failed to commit changes in {repo_name}")
            print(f"   Error: {commit_result.stderr}")
            return False

        print(f"✓ Successfully committed changes in {repo_name}")

    return True


def _sync_repo_configs(repo_path: Path, repo_name: str, source_root: Path) -> bool:
    """Copy shared configs into the consuming repo and stage changes."""
    sync_script = source_root / "scripts" / "sync_project_configs.py"
    if not sync_script.exists():
        print(f"⚠️  Sync script missing at {sync_script}")
        return False

    print("Syncing shared config files...")
    result = run_command(
        [sys.executable, str(sync_script), str(repo_path)],
        cwd=source_root,
        check=False,
    )
    if result.returncode != 0:
        print(f"⚠️  sync_project_configs failed for {repo_name}")
        return False

    print("Running tool_config_guard sync...")
    guard_result = run_command(
        [
            sys.executable,
            "-m",
            "ci_tools.scripts.tool_config_guard",
            "--repo-root",
            str(repo_path),
            "--sync",
        ],
        cwd=source_root,
        check=False,
    )
    if guard_result.returncode != 0:
        print(f"⚠️  tool_config_guard --sync failed for {repo_name}")
        return False

    run_command(["git", "add", "-A"], cwd=repo_path, check=True)
    status = run_command(
        ["git", "status", "--porcelain"],
        cwd=repo_path,
        check=False,
    )
    if not status.stdout.strip():
        print(f"✓ {repo_name} already up to date")
        return False
    return True


def _reinstall_ci_shared(repo_path: Path, repo_name: str, source_root: Path) -> bool:
    """Reinstall ci_shared package to update scripts in bin directory."""
    print(f"Reinstalling ci_shared in {repo_name}...")
    result = run_command(
        [sys.executable, "-m", "pip", "install", "-e", str(source_root)],
        cwd=repo_path,
        check=False,
    )
    if result.returncode != 0:
        print(f"⚠️  Failed to reinstall ci_shared in {repo_name}")
        print(f"   Error: {result.stderr}")
        return False

    print(f"✓ Successfully reinstalled ci_shared in {repo_name}")
    return True


def _load_commit_message_config() -> dict[str, Any]:
    """Load the commit_message section of ci_shared.config.json."""
    if not CI_SHARED_CONFIG_PATH.exists():
        return {}
    try:
        with CI_SHARED_CONFIG_PATH.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def _get_commit_message_fallback_model() -> str:
    """Return the configured fallback model when Codex isn't available."""
    config = _load_commit_message_config()
    commit_section = config.get("commit_message")
    if isinstance(commit_section, dict):
        fallback = commit_section.get("fallback_model")
        if isinstance(fallback, str) and fallback.strip():
            return fallback.strip()
    return DEFAULT_CLAUDE_FALLBACK_MODEL


def _invoke_commit_message_generator(
    repo_path: Path,
    *,
    env_overrides: dict[str, str] | None,
    label: str,
) -> tuple[str, list[str]] | None:
    """Run the commit message generator and parse the response."""
    print(f"Requesting {label} commit message...")
    result = run_command(
        [
            sys.executable,
            "-m",
            "ci_tools.scripts.generate_commit_message",
            "--detailed",
        ],
        cwd=repo_path,
        check=False,
        env=env_overrides,
    )
    if result.returncode != 0:
        print(f"⚠️  {label} commit message generation failed", file=sys.stderr)
        if result.stderr:
            print(f"   Error: {result.stderr.strip()}", file=sys.stderr)
        return None

    lines = [line.rstrip("\n") for line in result.stdout.splitlines()]
    if not lines:
        print("⚠️  Commit message generator returned no content", file=sys.stderr)
        return None

    summary = lines[0].strip()
    if not summary:
        print("⚠️  Commit summary was empty", file=sys.stderr)
        return None

    body_lines = [line.rstrip() for line in lines[1:]]
    return summary, body_lines


def _request_commit_message(repo_path: Path, ci_shared_commit_msg: str) -> tuple[str, list[str]] | None:
    """Generate a commit message, trying Codex first then falling back to Claude."""
    commit_message = _invoke_commit_message_generator(repo_path, env_overrides=None, label="Codex")
    if commit_message is None:
        fallback_model = _get_commit_message_fallback_model()
        print(
            "⚠️  Codex commit message generation failed; falling back to Claude...",
            file=sys.stderr,
        )
        fallback_env = {
            "CI_COMMIT_MODEL": fallback_model,
            "CI_CLI_TYPE": "claude",
        }
        commit_message = _invoke_commit_message_generator(repo_path, env_overrides=fallback_env, label="Claude")
        if commit_message is None:
            print("⚠️  Commit message generation failed after fallback", file=sys.stderr)
            return None

    summary, body_lines = commit_message
    body_lines.append(f"- Latest ci_shared change: {ci_shared_commit_msg}")
    return summary, body_lines


def _commit_and_push_update(repo_path: Path, repo_name: str, ci_shared_commit_msg: str) -> bool:
    """Stage, commit, and push the synced ci_shared files."""
    commit_message = _request_commit_message(repo_path, ci_shared_commit_msg)
    if commit_message is None:
        return False
    summary, body_lines = commit_message
    commit_args = ["git", "commit", "-m", summary]
    body_text = "\n".join(body_lines).strip()
    if body_text:
        commit_args.extend(["-m", body_text])

    result = run_command(
        commit_args,
        cwd=repo_path,
        check=False,
    )
    if result.returncode != 0:
        print(f"⚠️  Failed to commit shared CI updates in {repo_name}")
        return False

    print(f"✓ Committed shared CI updates in {repo_name}")

    # Get current branch name using shared utility
    try:
        current_branch = get_current_branch(cwd=repo_path)
    except subprocess.CalledProcessError:
        print(f"⚠️  Failed to determine current branch in {repo_name}")
        print(f"   Run 'cd {repo_path} && git push' to push manually")
        return False

    # Push with --set-upstream to handle branches without upstream configured
    result = run_command(
        ["git", "push", "--set-upstream", "origin", current_branch],
        cwd=repo_path,
        check=False,
    )
    if result.returncode != 0:
        print(f"⚠️  Failed to push shared CI updates in {repo_name}")
        print(f"   Run 'cd {repo_path} && git push' to push manually")
        return False

    print(f"✓ Pushed shared CI updates to {repo_name}")
    return True


def update_submodule_in_repo(
    repo_path: Path,
    ci_shared_commit_msg: str,
    *,
    display_name: str | None = None,
    source_root: Path,
) -> bool:
    """
    Apply the latest ci_shared files to a consuming repository.

    Returns:
        True if update was successful, False if skipped or failed
    """
    repo_name = display_name or repo_path.name
    print(f"\n{'='*70}")
    print(f"Updating ci_shared assets in {repo_name}...")
    print(f"{'='*70}")

    if not _validate_repo_state(repo_path, repo_name):
        return False

    has_changes = _sync_repo_configs(repo_path, repo_name, source_root)

    # Reinstall ci_shared to update scripts
    _reinstall_ci_shared(repo_path, repo_name, source_root)

    if not has_changes:
        return False

    return _commit_and_push_update(repo_path, repo_name, ci_shared_commit_msg)


def _process_repositories(
    consuming_repos: Iterable[ConsumingRepo],
    commit_msg: str,
    source_root: Path,
) -> tuple[list[str], list[str], list[str]]:
    """Process all consuming repositories and return results."""
    updated = []
    skipped = []
    failed = []

    for repo in consuming_repos:
        repo_path = repo.path
        repo_name = repo.name
        success = update_submodule_in_repo(
            repo_path,
            commit_msg,
            display_name=repo_name,
            source_root=source_root,
        )
        if success:
            updated.append(repo_name)
        else:
            skipped.append(repo_name)

    return updated, skipped, failed


def _print_summary(updated: list[str], skipped: list[str], failed: list[str]) -> None:
    """Print propagation summary."""
    print("\n" + "=" * 70)
    print("Propagation Summary")
    print("=" * 70)

    if updated:
        print(f"✅ Updated and pushed: {', '.join(updated)}")
    if skipped:
        print(f"⊘  Skipped: {', '.join(skipped)}")
    if failed:
        print(f"❌ Failed: {', '.join(failed)}")

    print()


def main() -> int:
    """Main entry point."""
    # Verify we're in ci_shared repository
    cwd = Path.cwd()

    # Check if this is the ci_shared repo
    if not (cwd / "ci_tools").exists() or not (cwd / "ci_shared.mk").exists():
        print("⚠️  Not running from ci_shared repository, skipping propagation")
        return 0

    print("\n" + "=" * 70)
    print("Propagating ci_shared updates to consuming repositories")
    print("=" * 70)

    # Get the latest commit message using shared utility
    try:
        commit_msg = get_commit_message(cwd=cwd)
        print(f"\nLatest ci_shared commit: {commit_msg}")
    except subprocess.CalledProcessError:
        print("⚠️  Failed to get latest commit message", file=sys.stderr)
        return 1

    consuming_repos = load_consuming_repos(repo_root=cwd)
    if not consuming_repos:
        print("⚠️  No consuming repositories configured; skipping propagation")
        return 0

    filtered_repos, blocked_repos = _filter_blocked_consumers(consuming_repos)
    if blocked_repos:
        print("\nSkipping blocked repositories: " + ", ".join(f"{repo.name} ({repo.path})" for repo in blocked_repos))

    if not filtered_repos:
        print("⚠️  No consuming repositories available after filtering; skipping propagation")
        return 0

    print("\nConsuming repos: " + ", ".join(f"{repo.name} ({repo.path})" for repo in filtered_repos))

    updated, skipped, failed = _process_repositories(filtered_repos, commit_msg, cwd)

    _print_summary(updated, skipped, failed)

    if failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
