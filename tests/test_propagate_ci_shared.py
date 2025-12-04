"""Unit tests for propagate_ci_shared module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ci_tools.ci_runtime.models import CommandResult
from ci_tools.ci_runtime.process import get_commit_message, run_command
from ci_tools.scripts.propagate_ci_shared import (
    _commit_and_push_update,
    _filter_blocked_consumers,
    _print_summary,
    _process_repositories,
    _request_commit_message,
    _sync_repo_configs,
    _validate_repo_state,
    main,
    update_submodule_in_repo,
)
from ci_tools.utils.consumers import ConsumingRepo


def test_run_command_success():
    """Test run_command with successful command."""
    result = run_command(["echo", "test"], cwd=None)
    assert result.returncode == 0
    assert "test" in result.stdout


def test_run_command_failure_no_check():
    """Test run_command with failed command and check=False."""
    result = run_command(["false"], cwd=None, check=False)
    assert result.returncode != 0


def test_run_command_failure_with_check():
    """Test run_command raises exception with check=True."""
    with pytest.raises(subprocess.CalledProcessError):
        run_command(["false"], cwd=None, check=True)


def test_get_commit_message():
    """Test get_commit_message returns commit message."""
    with patch("ci_tools.ci_runtime.process.run_command") as mock_run:
        mock_run.return_value = CommandResult(
            returncode=0,
            stdout="Fix: Update CI tooling",
            stderr="",
        )
        result = get_commit_message(cwd=Path("/tmp"))
        assert result == "Fix: Update CI tooling"


def test_validate_repo_state_missing_repo(tmp_path):
    """Test _validate_repo_state with missing repository."""
    missing_repo = tmp_path / "missing"
    result = _validate_repo_state(missing_repo, "missing")
    assert result is False


def test_validate_repo_state_existing_repo(tmp_path):
    """Test _validate_repo_state succeeds when repo exists."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    with patch("ci_tools.scripts.propagate_ci_shared.run_command") as mock_run:
        mock_run.return_value = CommandResult(
            returncode=0, stdout="", stderr=""
        )
        result = _validate_repo_state(repo_path, "repo")
        assert result is True


def test_validate_repo_state_uncommitted_changes(tmp_path):
    """Test _validate_repo_state auto-commits uncommitted changes."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    with patch("ci_tools.scripts.propagate_ci_shared.run_command") as mock_run:
        mock_run.side_effect = [
            # git status --porcelain (shows uncommitted changes)
            CommandResult(
                returncode=0,
                stdout=" M file.py\n",
                stderr="",
            ),
            # git add -A
            CommandResult(
                returncode=0,
                stdout="",
                stderr="",
            ),
            # git commit
            CommandResult(
                returncode=0,
                stdout="",
                stderr="",
            ),
        ]
        result = _validate_repo_state(repo_path, "repo")
        assert result is True
        assert mock_run.call_count == 3


def test_validate_repo_state_commit_failure(tmp_path):
    """Test _validate_repo_state handles commit failure."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    with patch("ci_tools.scripts.propagate_ci_shared.run_command") as mock_run:
        mock_run.side_effect = [
            # git status --porcelain (shows uncommitted changes)
            CommandResult(
                returncode=0,
                stdout=" M file.py\n",
                stderr="",
            ),
            # git add -A
            CommandResult(
                returncode=0,
                stdout="",
                stderr="",
            ),
            # git commit (fails)
            CommandResult(
                returncode=1,
                stdout="",
                stderr="commit failed",
            ),
        ]
        result = _validate_repo_state(repo_path, "repo")
        assert result is False


def test_validate_repo_state_clean(tmp_path):
    """Test _validate_repo_state with clean repository."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    with patch("ci_tools.scripts.propagate_ci_shared.run_command") as mock_run:
        mock_run.return_value = CommandResult(
            returncode=0,
            stdout="",
            stderr="",
        )
        result = _validate_repo_state(repo_path, "repo")
        assert result is True

@patch("ci_tools.scripts.propagate_ci_shared.run_command")
def test_request_commit_message_success(mock_run):
    """Test _request_commit_message succeeds when Codex returns content."""
    repo_path = Path("/tmp/repo")
    mock_run.return_value = CommandResult(
        returncode=0,
        stdout="Fix bug\n- Updated logic\n",
        stderr="",
    )

    summary, body_lines = _request_commit_message(repo_path, "abc123")
    assert summary == "Fix bug"
    assert body_lines[-1] == "- Latest ci_shared change: abc123"
    assert mock_run.call_count == 1
    assert mock_run.call_args.kwargs["env"] is None


@patch("ci_tools.scripts.propagate_ci_shared.run_command")
def test_request_commit_message_fallback_to_claude(mock_run):
    """Test fallback to Claude when the Codex attempt fails."""
    repo_path = Path("/tmp/repo")
    mock_run.side_effect = [
        CommandResult(returncode=1, stdout="", stderr="codex missing"),
        CommandResult(returncode=0, stdout="Add docs\n- Document change\n", stderr=""),
    ]

    summary, body_lines = _request_commit_message(repo_path, "ci456")
    assert summary == "Add docs"
    assert body_lines[-1] == "- Latest ci_shared change: ci456"
    assert mock_run.call_count == 2
    fallback_env = mock_run.call_args_list[1][1]["env"]
    assert fallback_env["CI_CLI_TYPE"] == "claude"
    assert fallback_env["CI_COMMIT_MODEL"] == "claude-sonnet-4-20250514"

def test_sync_repo_configs_no_changes(tmp_path):
    """Test _sync_repo_configs reports no updates when status clean."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    source_root = tmp_path / "ci_shared"
    (source_root / "scripts").mkdir(parents=True)
    (source_root / "scripts" / "sync_project_configs.py").write_text("print('ok')")

    with patch("ci_tools.scripts.propagate_ci_shared.run_command") as mock_run:
        mock_run.side_effect = [
            CommandResult(returncode=0, stdout="", stderr=""),
            CommandResult(returncode=0, stdout="", stderr=""),
            CommandResult(returncode=0, stdout="", stderr=""),
            CommandResult(returncode=0, stdout="", stderr=""),
        ]
        result = _sync_repo_configs(repo_path, "repo", source_root)
        assert result is False


def test_sync_repo_configs_with_changes(tmp_path):
    """Test _sync_repo_configs reports updates when status dirty."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    source_root = tmp_path / "ci_shared"
    (source_root / "scripts").mkdir(parents=True)
    (source_root / "scripts" / "sync_project_configs.py").write_text("print('ok')")

    with patch("ci_tools.scripts.propagate_ci_shared.run_command") as mock_run:
        mock_run.side_effect = [
            CommandResult(returncode=0, stdout="", stderr=""),
            CommandResult(returncode=0, stdout="", stderr=""),
            CommandResult(returncode=0, stdout="", stderr=""),
            CommandResult(returncode=0, stdout=" M file\n", stderr=""),
        ]
        result = _sync_repo_configs(repo_path, "repo", source_root)
        assert result is True


def test_commit_and_push_update_commit_failure(tmp_path):
    """Test _commit_and_push_update with commit failure."""
    repo_path = tmp_path / "repo"

    with patch("ci_tools.scripts.propagate_ci_shared.run_command") as mock_run:
        mock_run.side_effect = [
            CommandResult(returncode=0, stdout="Subject\n- body", stderr=""),
            CommandResult(returncode=1, stdout="", stderr="error")
        ]
        result = _commit_and_push_update(repo_path, "repo", "Test commit")
        assert result is False


def test_commit_and_push_update_branch_detection_failure(tmp_path):
    """Test _commit_and_push_update with branch detection failure."""
    repo_path = tmp_path / "repo"

    with patch("ci_tools.scripts.propagate_ci_shared.run_command") as mock_run:
        with patch("ci_tools.scripts.propagate_ci_shared.get_current_branch") as mock_branch:
            mock_run.side_effect = [
                CommandResult(returncode=0, stdout="Subject\n- body", stderr=""),
                CommandResult(returncode=0, stdout="", stderr=""),
            ]
            mock_branch.side_effect = subprocess.CalledProcessError(1, "git")
            result = _commit_and_push_update(repo_path, "repo", "Test commit")
            assert result is False


def test_commit_and_push_update_push_failure(tmp_path):
    """Test _commit_and_push_update with push failure."""
    repo_path = tmp_path / "repo"

    with patch("ci_tools.scripts.propagate_ci_shared.run_command") as mock_run:
        with patch("ci_tools.scripts.propagate_ci_shared.get_current_branch") as mock_branch:
            mock_branch.return_value = "main"
            mock_run.side_effect = [
                CommandResult(returncode=0, stdout="Subject\n- body", stderr=""),
                CommandResult(returncode=0, stdout="", stderr=""),
                CommandResult(returncode=1, stdout="", stderr="error"),
            ]
            result = _commit_and_push_update(repo_path, "repo", "Test commit")
            assert result is False


def test_commit_and_push_update_success(tmp_path):
    """Test _commit_and_push_update with successful commit and push."""
    repo_path = tmp_path / "repo"

    with patch("ci_tools.scripts.propagate_ci_shared.run_command") as mock_run:
        with patch("ci_tools.scripts.propagate_ci_shared.get_current_branch") as mock_branch:
            mock_branch.return_value = "main"
            mock_run.side_effect = [
                CommandResult(returncode=0, stdout="Subject\n- body", stderr=""),
                CommandResult(returncode=0, stdout="", stderr=""),
                CommandResult(returncode=0, stdout="", stderr=""),
            ]
            result = _commit_and_push_update(repo_path, "repo", "Test commit")
            assert result is True


def test_commit_and_push_update_generation_failure(tmp_path):
    """Test _commit_and_push_update fails fast when commit message generation fails."""
    repo_path = tmp_path / "repo"

    with patch("ci_tools.scripts.propagate_ci_shared.run_command") as mock_run:
        mock_run.return_value = CommandResult(returncode=1, stdout="", stderr="error")
        result = _commit_and_push_update(repo_path, "repo", "Test commit")
        assert result is False
        assert mock_run.call_count == 2
        fallback_env = mock_run.call_args_list[1][1]["env"]
        assert fallback_env["CI_CLI_TYPE"] == "claude"
        assert fallback_env["CI_COMMIT_MODEL"] == "claude-sonnet-4-20250514"


def test_update_submodule_in_repo_invalid_state(tmp_path):
    """Test update_submodule_in_repo with invalid state."""
    repo_path = tmp_path / "repo"
    with patch("ci_tools.scripts.propagate_ci_shared._validate_repo_state") as mock:
        mock.return_value = False
        result = update_submodule_in_repo(repo_path, "Test commit", source_root=tmp_path)
        assert result is False


def test_update_submodule_in_repo_no_changes(tmp_path):
    """Test update_submodule_in_repo with no changes."""
    repo_path = tmp_path / "repo"
    with patch("ci_tools.scripts.propagate_ci_shared._validate_repo_state") as mock1:
        with patch("ci_tools.scripts.propagate_ci_shared._sync_repo_configs") as mock2:
            with patch("ci_tools.scripts.propagate_ci_shared._reinstall_ci_shared") as mock3:
                mock1.return_value = True
                mock2.return_value = False
                mock3.return_value = True
                result = update_submodule_in_repo(repo_path, "Test commit", source_root=tmp_path)
                assert result is False


def test_update_submodule_in_repo_success(tmp_path):
    """Test update_submodule_in_repo with successful update."""
    repo_path = tmp_path / "repo"
    with patch("ci_tools.scripts.propagate_ci_shared._validate_repo_state") as mock1:
        with patch("ci_tools.scripts.propagate_ci_shared._sync_repo_configs") as mock2:
            with patch("ci_tools.scripts.propagate_ci_shared._commit_and_push_update") as mock3:
                with patch("ci_tools.scripts.propagate_ci_shared._reinstall_ci_shared") as mock4:
                    mock1.return_value = True
                    mock2.return_value = True
                    mock3.return_value = True
                    mock4.return_value = True
                    result = update_submodule_in_repo(
                        repo_path, "Test commit", source_root=tmp_path
                    )
                    assert result is True


def test_process_repositories(tmp_path):
    """Test _process_repositories processes all repos."""
    repos = [
        ConsumingRepo("zeus", tmp_path / "zeus"),
        ConsumingRepo("kalshi", tmp_path / "kalshi"),
        ConsumingRepo("aws", tmp_path / "aws"),
    ]
    with patch("ci_tools.scripts.propagate_ci_shared.update_submodule_in_repo") as mock_update:
        mock_update.side_effect = [True, False, True]
        updated, skipped, failed = _process_repositories(repos, "Test commit", tmp_path)
        assert updated == ["zeus", "aws"]
        assert skipped == ["kalshi"]
        assert not failed


def test_filter_blocked_consumers(tmp_path):
    """Test that blocked consumer names are filtered out."""
    repos = [
        ConsumingRepo("api", tmp_path / "api"),
        ConsumingRepo("chess", tmp_path / "chess"),
        ConsumingRepo("tictactoe", tmp_path / "tictactoe"),
    ]
    allowed, blocked = _filter_blocked_consumers(repos)
    assert [repo.name for repo in allowed] == ["api"]
    assert [repo.name for repo in blocked] == ["chess", "tictactoe"]


def test_print_summary_all_types(capsys):
    """Test _print_summary prints all status types."""
    _print_summary(["zeus"], ["kalshi"], ["aws"])
    captured = capsys.readouterr()
    assert "zeus" in captured.out
    assert "kalshi" in captured.out
    assert "aws" in captured.out


def test_print_summary_empty_lists(capsys):
    """Test _print_summary with empty lists."""
    _print_summary([], [], [])
    captured = capsys.readouterr()
    assert "Summary" in captured.out


def test_main_not_in_ci_shared(tmp_path, monkeypatch):
    """Test main returns early when not in ci_shared."""
    monkeypatch.chdir(tmp_path)
    result = main()
    assert result == 0


def test_main_commit_message_failure(tmp_path, monkeypatch):
    """Test main handles commit message failure."""
    repo_dir = tmp_path / "ci_shared"
    repo_dir.mkdir()
    (repo_dir / "ci_tools").mkdir()
    (repo_dir / "ci_shared.mk").touch()
    monkeypatch.chdir(repo_dir)

    with patch("ci_tools.scripts.propagate_ci_shared.get_commit_message") as mock:
        mock.side_effect = subprocess.CalledProcessError(1, "git")
        result = main()
        assert result == 1


def test_main_success_with_updates(tmp_path, monkeypatch):
    """Test main successfully propagates updates."""
    repo_dir = tmp_path / "ci_shared"
    repo_dir.mkdir()
    (repo_dir / "ci_tools").mkdir()
    (repo_dir / "ci_shared.mk").touch()
    monkeypatch.chdir(repo_dir)

    with patch("ci_tools.scripts.propagate_ci_shared.get_commit_message") as mock1:
        with patch("ci_tools.scripts.propagate_ci_shared.load_consuming_repos") as mock_load:
            with patch("ci_tools.scripts.propagate_ci_shared._process_repositories") as mock2:
                mock1.return_value = "Test commit"
                mock_load.return_value = [ConsumingRepo("api", tmp_path / "api")]
                mock2.return_value = (["zeus"], ["kalshi"], [])
                result = main()
                assert result == 0


def test_main_with_failures(tmp_path, monkeypatch):
    """Test main returns error code when there are failures."""
    repo_dir = tmp_path / "ci_shared"
    repo_dir.mkdir()
    (repo_dir / "ci_tools").mkdir()
    (repo_dir / "ci_shared.mk").touch()
    monkeypatch.chdir(repo_dir)

    with patch("ci_tools.scripts.propagate_ci_shared.get_commit_message") as mock1:
        with patch("ci_tools.scripts.propagate_ci_shared.load_consuming_repos") as mock_load:
            with patch("ci_tools.scripts.propagate_ci_shared._process_repositories") as mock2:
                mock1.return_value = "Test commit"
                mock_load.return_value = [ConsumingRepo("api", tmp_path / "api")]
                mock2.return_value = ([], [], ["aws"])
                result = main()
    assert result == 1


def test_main_skips_blocked_consumers(tmp_path, monkeypatch, capsys):
    """Test main filters out blocked consuming repositories."""
    repo_dir = tmp_path / "ci_shared"
    repo_dir.mkdir()
    (repo_dir / "ci_tools").mkdir()
    (repo_dir / "ci_shared.mk").touch()
    monkeypatch.chdir(repo_dir)

    with patch(
        "ci_tools.scripts.propagate_ci_shared.get_commit_message"
    ) as mock_commit:
        with patch(
            "ci_tools.scripts.propagate_ci_shared.load_consuming_repos"
        ) as mock_load:
            with patch(
                "ci_tools.scripts.propagate_ci_shared._process_repositories"
            ) as mock_process:
                mock_commit.return_value = "Test commit"
                mock_load.return_value = [
                    ConsumingRepo("api", tmp_path / "api"),
                    ConsumingRepo("chess", tmp_path / "chess"),
                ]
                mock_process.return_value = ([], [], [])
                result = main()

    captured = capsys.readouterr()
    assert result == 0
    assert "Skipping blocked repositories" in captured.out
    assert "chess" in captured.out
    mock_process.assert_called_once()
    passed_repos = mock_process.call_args.args[0]
    assert [repo.name for repo in passed_repos] == ["api"]


def test_main_only_blocked_consumers(tmp_path, monkeypatch, capsys):
    """Test main exits early when every consuming repo is blocked."""
    repo_dir = tmp_path / "ci_shared"
    repo_dir.mkdir()
    (repo_dir / "ci_tools").mkdir()
    (repo_dir / "ci_shared.mk").touch()
    monkeypatch.chdir(repo_dir)

    with patch(
        "ci_tools.scripts.propagate_ci_shared.get_commit_message"
    ) as mock_commit:
        with patch(
            "ci_tools.scripts.propagate_ci_shared.load_consuming_repos"
        ) as mock_load:
            with patch(
                "ci_tools.scripts.propagate_ci_shared._process_repositories"
            ) as mock_process:
                mock_commit.return_value = "Test commit"
                mock_load.return_value = [
                    ConsumingRepo("chess", tmp_path / "chess"),
                    ConsumingRepo("tictactoe", tmp_path / "tictactoe"),
                ]
                result = main()

    captured = capsys.readouterr()
    assert result == 0
    assert "Skipping blocked repositories" in captured.out
    assert "No consuming repositories available after filtering" in captured.out
    mock_process.assert_not_called()
