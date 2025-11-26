"""Unit tests for ci_tools.ci_runtime.workflow module."""

from __future__ import annotations

import os
from unittest.mock import Mock, patch

import pytest

from ci_tools.ci_runtime.config import (
    resolve_model_choice,
    resolve_reasoning_choice,
)
from ci_tools.ci_runtime.workflow import (
    _derive_runtime_flags,
    configure_runtime,
    perform_dry_run,
    _collect_worktree_diffs,
    _worktree_is_clean,
    _stage_if_needed,
    _warn_missing_staged_changes,
    _maybe_request_commit_message,
    _maybe_push_or_notify,
    finalize_worktree,
    run_repair_iterations,
    parse_args,
    main,
)
from ci_tools.ci_runtime.models import (
    CiAbort,
    ModelSelectionAbort,
    PatchLifecycleAbort,
    ReasoningEffortAbort,
    RuntimeOptions,
)
from ci_tools.test_constants import get_constant

WORKFLOW_CONSTANTS = get_constant("workflow")


class TestResolveModelChoice:
    """Tests for _resolve_model_choice function."""

    def test_accepts_required_model(self):
        """Test accepting the required model."""
        result = resolve_model_choice("gpt-5-codex", validate=True)
        assert result == "gpt-5-codex"
        assert os.environ["OPENAI_MODEL"] == "gpt-5-codex"

    def test_rejects_unsupported_model(self):
        """Test rejecting unsupported model."""
        with pytest.raises(ModelSelectionAbort) as exc_info:
            resolve_model_choice("gpt-4", validate=True)
        assert "requires" in str(exc_info.value)
        assert "gpt-5-codex" in str(exc_info.value)

    def test_uses_env_var_when_no_arg(self):
        """Test using OPENAI_MODEL env var when no argument provided."""
        with patch.dict(os.environ, {"OPENAI_MODEL": "gpt-5-codex"}):
            result = resolve_model_choice(None, validate=True)
            assert result == "gpt-5-codex"

    def test_raises_when_no_model_provided(self):
        """Test raising exception when no model is provided."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ModelSelectionAbort):
                resolve_model_choice(None, validate=True)

    def test_rejects_env_var_with_wrong_model(self):
        """Test rejecting wrong model from environment variable."""
        with patch.dict(os.environ, {"OPENAI_MODEL": "gpt-3.5-turbo"}):
            with pytest.raises(ModelSelectionAbort):
                resolve_model_choice(None, validate=True)


class TestResolveReasoningChoice:
    """Tests for _resolve_reasoning_choice function."""

    def test_accepts_valid_low(self):
        """Test accepting 'low' reasoning effort."""
        result = resolve_reasoning_choice("low", validate=True)
        assert result == "low"
        assert os.environ["OPENAI_REASONING_EFFORT"] == "low"

    def test_accepts_valid_medium(self):
        """Test accepting 'medium' reasoning effort."""
        result = resolve_reasoning_choice("medium", validate=True)
        assert result == "medium"

    def test_accepts_valid_high(self):
        """Test accepting 'high' reasoning effort."""
        result = resolve_reasoning_choice("high", validate=True)
        assert result == "high"

    def test_rejects_invalid_choice(self):
        """Test rejecting invalid reasoning effort."""
        with pytest.raises(ReasoningEffortAbort) as exc_info:
            resolve_reasoning_choice("extreme", validate=True)
        assert "expected one of" in str(exc_info.value)

    def test_uses_env_var_when_no_arg(self):
        """Test using OPENAI_REASONING_EFFORT env var."""
        with patch.dict(os.environ, {"OPENAI_REASONING_EFFORT": "MEDIUM"}):
            result = resolve_reasoning_choice(None, validate=True)
            assert result == "medium"

    def test_raises_when_no_reasoning_provided(self):
        """Test raising exception when no reasoning effort is provided."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ReasoningEffortAbort):
                resolve_reasoning_choice(None, validate=True)

    def test_case_insensitive_env_var(self):
        """Test environment variable is case-insensitive."""
        with patch.dict(os.environ, {"OPENAI_REASONING_EFFORT": "LOW"}):
            result = resolve_reasoning_choice(None, validate=True)
            assert result == "low"


class TestDeriveRuntimeFlags:
    """Tests for _derive_runtime_flags function."""

    def test_automation_mode_enabled_for_ci_sh(self):
        """Test automation mode enabled when command is ci.sh."""
        args = Mock(auto_stage=False, commit_message=False)
        command_tokens = ["./scripts/ci.sh"]
        (
            automation_mode,
            command_env,
            auto_stage_enabled,
            commit_message_enabled,
            auto_push_enabled,
        ) = _derive_runtime_flags(args, command_tokens)
        assert automation_mode is True
        assert command_env == {"CI_AUTOMATION": "1"}
        assert auto_stage_enabled is True
        assert commit_message_enabled is True
        assert auto_push_enabled is True

    def test_automation_mode_disabled_for_other_commands(self):
        """Test automation mode disabled for non-ci.sh commands."""
        args = Mock(auto_stage=False, commit_message=False)
        command_tokens = ["pytest"]
        (
            automation_mode,
            command_env,
            auto_stage_enabled,
            commit_message_enabled,
            auto_push_enabled,
        ) = _derive_runtime_flags(args, command_tokens)
        assert automation_mode is False
        assert not command_env
        assert auto_stage_enabled is False
        assert commit_message_enabled is False
        assert auto_push_enabled is False

    def test_auto_stage_flag_overrides(self):
        """Test auto_stage flag overrides default."""
        args = Mock(auto_stage=True, commit_message=False)
        command_tokens = ["pytest"]
        (
            _automation_mode,
            _command_env,
            auto_stage_enabled,
            _commit_message_enabled,
            _auto_push_enabled,
        ) = _derive_runtime_flags(args, command_tokens)
        assert auto_stage_enabled is True

    def test_commit_message_flag_overrides(self):
        """Test commit_message flag overrides default."""
        args = Mock(auto_stage=False, commit_message=True)
        command_tokens = ["make", "test"]
        (
            _automation_mode,
            _command_env,
            _auto_stage_enabled,
            commit_message_enabled,
            _auto_push_enabled,
        ) = _derive_runtime_flags(args, command_tokens)
        assert commit_message_enabled is True

    def test_empty_command_tokens(self):
        """Test handling of empty command tokens."""
        args = Mock(auto_stage=False, commit_message=False)
        command_tokens = []
        (
            automation_mode,
            _command_env,
            _auto_stage_enabled,
            _commit_message_enabled,
            _auto_push_enabled,
        ) = _derive_runtime_flags(args, command_tokens)
        assert automation_mode is False


class TestConfigureRuntime:
    """Tests for configure_runtime function."""

    @patch("ci_tools.ci_runtime.workflow.load_env_settings")
    @patch("ci_tools.ci_runtime.config.resolve_model_choice")
    @patch("ci_tools.ci_runtime.config.resolve_reasoning_choice")
    def test_creates_runtime_options(self, mock_reasoning, mock_model, mock_load_env):
        """Test creating RuntimeOptions from parsed args."""
        mock_model.return_value = "gpt-5-codex"
        mock_reasoning.return_value = "high"

        args = Mock(
            command="./scripts/ci.sh",
            env_file="~/.env",
            model="gpt-5-codex",
            reasoning_effort="high",
            patch_approval_mode="prompt",
            auto_stage=False,
            commit_message=False,
        )

        options = configure_runtime(args)

        assert isinstance(options, RuntimeOptions)
        assert options.command_tokens == ["./scripts/ci.sh"]
        assert options.model_name == "gpt-5-codex"
        assert options.reasoning_effort == "high"
        assert options.patch_approval_mode == "prompt"
        mock_load_env.assert_called_once_with("~/.env")

    @patch("ci_tools.ci_runtime.workflow.load_env_settings")
    @patch("ci_tools.ci_runtime.config.resolve_model_choice")
    @patch("ci_tools.ci_runtime.config.resolve_reasoning_choice")
    def test_handles_automation_mode(self, mock_reasoning, mock_model, _mock_load_env):
        """Test automation mode flags are set correctly."""
        mock_model.return_value = "gpt-5-codex"
        mock_reasoning.return_value = "high"

        args = Mock(
            command="./scripts/ci.sh",
            env_file="~/.env",
            model=None,
            reasoning_effort=None,
            patch_approval_mode="auto",
            auto_stage=False,
            commit_message=False,
        )

        options = configure_runtime(args)

        assert options.automation_mode is True
        assert options.auto_stage_enabled is True
        assert options.commit_message_enabled is True
        assert options.auto_push_enabled is True

    @patch("ci_tools.ci_runtime.workflow.load_env_settings")
    @patch("ci_tools.ci_runtime.config.resolve_model_choice")
    @patch("ci_tools.ci_runtime.config.resolve_reasoning_choice")
    def test_parses_command_with_spaces(self, mock_reasoning, mock_model, _mock_load_env):
        """Test parsing command with spaces and arguments."""
        mock_model.return_value = "gpt-5-codex"
        mock_reasoning.return_value = "medium"

        args = Mock(
            command="pytest tests/ -v --cov=src",
            env_file="~/.env",
            model=None,
            reasoning_effort=None,
            patch_approval_mode="prompt",
            auto_stage=False,
            commit_message=False,
        )

        options = configure_runtime(args)

        assert options.command_tokens == ["pytest", "tests/", "-v", "--cov=src"]


class TestPerformDryRun:
    """Tests for perform_dry_run function."""

    @patch("ci_tools.ci_runtime.workflow.run_command")
    def test_executes_command_when_dry_run_enabled(self, mock_run):
        """Test executing CI command once in dry-run mode."""
        mock_run.return_value = Mock(returncode=0)
        args = Mock(dry_run=True, command="./scripts/ci.sh")
        options = Mock(command_tokens=["./scripts/ci.sh"], command_env={})

        result = perform_dry_run(args, options)

        assert result == 0
        mock_run.assert_called_once_with(["./scripts/ci.sh"], live=True, env={})

    @patch("ci_tools.ci_runtime.workflow.run_command")
    def test_returns_failure_code(self, mock_run):
        """Test returning failure exit code in dry-run mode."""
        mock_run.return_value = Mock(returncode=1)
        args = Mock(dry_run=True, command="pytest")
        options = Mock(command_tokens=["pytest"], command_env={})

        result = perform_dry_run(args, options)

        assert result == 1

    def test_returns_none_when_dry_run_disabled(self):
        """Test returning None when dry-run is disabled."""
        args = Mock(dry_run=False)
        options = Mock()

        result = perform_dry_run(args, options)

        assert result is None


# pylint: disable=too-few-public-methods
class TestCollectWorktreeDiffs:
    """Tests for _collect_worktree_diffs function."""

    @patch("ci_tools.ci_runtime.workflow.gather_git_diff_limited")
    def test_collects_unstaged_and_staged_diffs(self, mock_gather):
        """Test collecting both unstaged and staged diffs."""
        mock_gather.side_effect = ["unstaged content", "staged content"]

        unstaged, staged = _collect_worktree_diffs()

        assert unstaged == "unstaged content"
        assert staged == "staged content"
        assert mock_gather.call_count == WORKFLOW_CONSTANTS["diff_call_count"]
        mock_gather.assert_any_call(staged=False)
        mock_gather.assert_any_call(staged=True)


class TestWorktreeIsClean:
    """Tests for _worktree_is_clean function."""

    def test_clean_when_no_diffs(self):
        """Test worktree is clean with no diffs."""
        assert _worktree_is_clean("", "") is True

    def test_not_clean_with_unstaged_diff(self):
        """Test worktree not clean with unstaged changes."""
        assert _worktree_is_clean("diff content", "") is False

    def test_not_clean_with_staged_diff(self):
        """Test worktree not clean with staged changes."""
        assert _worktree_is_clean("", "diff content") is False

    def test_not_clean_with_both_diffs(self):
        """Test worktree not clean with both types of changes."""
        assert _worktree_is_clean("unstaged", "staged") is False


class TestStageIfNeeded:
    """Tests for _stage_if_needed function."""

    @patch("ci_tools.ci_runtime.workflow.run_command")
    @patch("ci_tools.ci_runtime.workflow.gather_git_diff_limited")
    def test_stages_all_changes_when_enabled(self, mock_gather, mock_run):
        """Test staging all changes when auto-stage is enabled."""
        mock_gather.return_value = "new staged diff"
        options = Mock(auto_stage_enabled=True)

        result = _stage_if_needed(options, "old staged diff")

        assert result == "new staged diff"
        mock_run.assert_called_once_with(["git", "add", "-A"], check=True)

    def test_returns_existing_staged_diff_when_disabled(self):
        """Test returning existing diff when auto-stage is disabled."""
        options = Mock(auto_stage_enabled=False)

        result = _stage_if_needed(options, "existing diff")

        assert result == "existing diff"

# pylint: disable=too-few-public-methods

class TestWarnMissingStagedChanges:
    """Tests for _warn_missing_staged_changes function."""

    def test_prints_warning_to_stderr(self, capsys):
        """Test warning is printed to stderr."""
        _warn_missing_staged_changes()

        captured = capsys.readouterr()
        assert "No staged changes detected" in captured.err
        assert "Stage files before requesting a commit message" in captured.err


class TestMaybeRequestCommitMessage:
    """Tests for _maybe_request_commit_message function."""

    @patch("ci_tools.ci_runtime.workflow.request_commit_message")
    def test_requests_commit_message_when_enabled(self, mock_request):
        """Test requesting commit message when enabled."""
        mock_request.return_value = ("feat: add feature", ["Details here"])
        options = Mock(
            commit_message_enabled=True,
            model_name="gpt-5-codex",
            reasoning_effort="high",
            auto_push_enabled=False,
        )

        summary, body = _maybe_request_commit_message(options, "staged diff", "extra context")

        assert summary == "feat: add feature"
        assert body == ["Details here"]
        mock_request.assert_called_once_with(
            model="gpt-5-codex",
            reasoning_effort="high",
            staged_diff="staged diff",
            extra_context="extra context",
            detailed=False,
        )

    def test_returns_none_when_disabled(self):
        """Test returning None when commit message is disabled."""
        options = Mock(commit_message_enabled=False)

        summary, body = _maybe_request_commit_message(options, "diff", "")

        assert summary is None
        assert not body

    @patch("ci_tools.ci_runtime.workflow.request_commit_message")
    def test_detailed_mode_for_auto_push(self, mock_request):
        """Test detailed mode enabled for auto-push."""
        mock_request.return_value = ("commit", [])
        options = Mock(
            commit_message_enabled=True,
            model_name="gpt-5-codex",
            reasoning_effort="high",
            auto_push_enabled=True,
        )

        _maybe_request_commit_message(options, "diff", "")

        mock_request.assert_called_once()
        assert mock_request.call_args[1]["detailed"] is True


class TestMaybePushOrNotify:
    """Tests for _maybe_push_or_notify function."""

    @patch("ci_tools.ci_runtime.workflow.commit_and_push")
    def test_commits_and_pushes_when_auto_push_enabled(self, mock_commit):
        """Test committing and pushing when auto-push is enabled."""
        options = Mock(auto_push_enabled=True)

        _maybe_push_or_notify(options, "commit summary", ["body line"])

        mock_commit.assert_called_once_with("commit summary", ["body line"], push=True)

    def test_raises_when_summary_is_none_with_auto_push(self):
        """Test raises ValueError when summary is None with auto-push enabled."""
        options = Mock(auto_push_enabled=True)

        with pytest.raises(ValueError, match="Commit summary is required"):
            _maybe_push_or_notify(options, None, [])

    def test_prints_notification_when_auto_push_disabled(self, capsys):
        """Test printing notification when auto-push is disabled."""
        options = Mock(auto_push_enabled=False)

        _maybe_push_or_notify(options, "summary", ["body"])

        captured = capsys.readouterr()
        assert "Commit message ready" in captured.out

    def test_no_notification_when_no_summary(self, capsys):
        """Test no notification when summary is None."""
        options = Mock(auto_push_enabled=False)

        _maybe_push_or_notify(options, None, [])

        captured = capsys.readouterr()
        assert captured.out == ""


class TestFinalizeWorktree:
    """Tests for finalize_worktree function."""

    @patch("ci_tools.ci_runtime.workflow._collect_worktree_diffs")
    def test_returns_zero_when_worktree_clean(self, mock_collect):
        """Test returning 0 when worktree is clean."""
        mock_collect.return_value = ("", "")
        args = Mock()
        options = Mock()

        result = finalize_worktree(args, options)

        assert result == 0

    @patch("ci_tools.ci_runtime.workflow._collect_worktree_diffs")
    @patch("ci_tools.ci_runtime.workflow._stage_if_needed")
    def test_warns_when_no_staged_changes_after_staging(self, mock_stage, mock_collect):
        """Test warning when no staged changes after staging."""
        mock_collect.return_value = ("unstaged", "")
        mock_stage.return_value = ""
        args = Mock(commit_extra_context="")
        options = Mock(auto_stage_enabled=True, commit_message_enabled=False)

        result = finalize_worktree(args, options)

        assert result == 0

    @patch("ci_tools.ci_runtime.workflow._collect_worktree_diffs")
    @patch("ci_tools.ci_runtime.workflow._stage_if_needed")
    @patch("ci_tools.ci_runtime.workflow._maybe_request_commit_message")
    @patch("ci_tools.ci_runtime.workflow._maybe_push_or_notify")
    def test_complete_workflow_with_staged_changes(
        self, mock_push, mock_request, mock_stage, mock_collect
    ):
        """Test complete finalization workflow with staged changes."""
        mock_collect.return_value = ("", "staged diff")
        mock_stage.return_value = "staged diff"
        mock_request.return_value = ("summary", ["body"])
        args = Mock(commit_extra_context="context")
        options = Mock(
            auto_stage_enabled=False,
            commit_message_enabled=True,
            model_name="gpt-5-codex",
            reasoning_effort="high",
            auto_push_enabled=False,
        )

        result = finalize_worktree(args, options)

        assert result == 0
        mock_request.assert_called_once()
        mock_push.assert_called_once_with(options, "summary", ["body"])


class TestRunRepairIterations:
    """Tests for run_repair_iterations function."""

    @patch("ci_tools.ci_runtime.workflow.run_command")
    @patch("ci_tools.ci_runtime.workflow.extract_coverage_deficits")
    def test_succeeds_on_first_iteration(self, mock_coverage, mock_run):
        """Test successful CI on first iteration."""
        mock_run.return_value = Mock(returncode=0, ok=True, combined_output="")
        mock_coverage.return_value = None
        args = Mock(command="pytest", max_iterations=5)
        options = Mock(command_tokens=["pytest"], command_env={})

        run_repair_iterations(args, options)

        assert mock_run.call_count == 1

    @patch("ci_tools.ci_runtime.workflow.run_command")
    @patch("ci_tools.ci_runtime.workflow.extract_coverage_deficits")
    @patch("ci_tools.ci_runtime.workflow.build_failure_context")
    @patch("ci_tools.ci_runtime.workflow.request_and_apply_patches")
    def test_iterates_until_success(self, mock_patches, mock_failure, mock_coverage, mock_run):
        """Test iterating until CI succeeds."""
        # First two iterations fail, third succeeds
        mock_run.side_effect = [
            Mock(returncode=1, ok=False, combined_output="fail1"),
            Mock(returncode=1, ok=False, combined_output="fail2"),
            Mock(returncode=0, ok=True, combined_output="success"),
        ]
        mock_coverage.return_value = None
        mock_failure.return_value = Mock()
        args = Mock(command="pytest", max_iterations=5)
        options = Mock(command_tokens=["pytest"], command_env={})

        run_repair_iterations(args, options)

        assert mock_run.call_count == WORKFLOW_CONSTANTS["repair_iterations_runs"]
        assert mock_patches.call_count == WORKFLOW_CONSTANTS["repair_iterations_patch_calls"]

    @patch("ci_tools.ci_runtime.workflow.run_command")
    @patch("ci_tools.ci_runtime.workflow.extract_coverage_deficits")
    @patch("ci_tools.ci_runtime.workflow.build_failure_context")
    @patch("ci_tools.ci_runtime.workflow.request_and_apply_patches")
    def test_raises_when_max_iterations_exceeded(
        self, _mock_patches, mock_failure, mock_coverage, mock_run
    ):
        """Test raising exception when max iterations exceeded."""
        mock_run.return_value = Mock(returncode=1, ok=False, combined_output="fail")
        mock_coverage.return_value = None
        mock_failure.return_value = Mock()
        args = Mock(command="pytest", max_iterations=2, log_tail=200)
        options = Mock(command_tokens=["pytest"], command_env={})

        with pytest.raises(PatchLifecycleAbort) as exc_info:
            run_repair_iterations(args, options)

        assert "unable to obtain a valid patch" in str(exc_info.value)

    @patch("ci_tools.ci_runtime.workflow.run_command")
    @patch("ci_tools.ci_runtime.workflow.extract_coverage_deficits")
    @patch("ci_tools.ci_runtime.workflow.build_failure_context")
    @patch("ci_tools.ci_runtime.workflow.request_and_apply_patches")
    def test_handles_coverage_deficits(self, mock_patches, mock_failure, mock_coverage, mock_run):
        """Test handling coverage deficits even when CI passes."""
        mock_run.side_effect = [
            Mock(returncode=0, ok=True, combined_output="coverage: 70%"),
            Mock(returncode=0, ok=True, combined_output="coverage: 82%"),
        ]
        mock_coverage.side_effect = [Mock(deficits=[Mock()]), None]
        mock_failure.return_value = Mock()
        args = Mock(command="pytest", max_iterations=5)
        options = Mock(command_tokens=["pytest"], command_env={})

        run_repair_iterations(args, options)

        assert mock_run.call_count == WORKFLOW_CONSTANTS["retry_run_call_count"]
        assert mock_patches.call_count == 1

    @patch("ci_tools.ci_runtime.workflow.run_command")
    @patch("ci_tools.ci_runtime.workflow.extract_coverage_deficits")
    @patch("ci_tools.ci_runtime.workflow.build_failure_context")
    @patch("ci_tools.ci_runtime.workflow.request_and_apply_patches")
    def test_passes_correct_iteration_number(
        self, mock_patches, mock_failure, mock_coverage, mock_run
    ):
        """Test correct iteration number passed to patch function."""
        mock_run.side_effect = [
            Mock(returncode=1, ok=False, combined_output="fail"),
            Mock(returncode=0, ok=True, combined_output="success"),
        ]
        mock_coverage.return_value = None
        mock_failure.return_value = Mock()
        args = Mock(command="pytest", max_iterations=5)
        options = Mock(command_tokens=["pytest"], command_env={})

        run_repair_iterations(args, options)

        # Check iteration numbers in calls
        calls = mock_patches.call_args_list
        assert calls[0][1]["iteration"] == 1


class TestParseArgs:
    """Tests for parse_args function."""

    def test_default_values(self):
        """Test default argument values."""
        args = parse_args([])

        assert args.command == "./scripts/ci.sh"
        assert args.max_iterations == WORKFLOW_CONSTANTS["default_max_iterations"]
        assert args.log_tail == WORKFLOW_CONSTANTS["default_log_tail"]
        assert args.patch_approval_mode == "prompt"
        assert args.dry_run is False
        assert args.auto_stage is False
        assert args.commit_message is False

    def test_custom_command(self):
        """Test custom command argument."""
        args = parse_args(["--command", "make test"])

        assert args.command == "make test"

    def test_max_iterations(self):
        """Test max-iterations argument."""
        args = parse_args(["--max-iterations", "10"])

        assert args.max_iterations == WORKFLOW_CONSTANTS["cli_max_iterations"]

    def test_log_tail(self):
        """Test log-tail argument."""
        args = parse_args(["--log-tail", "500"])

        assert args.log_tail == WORKFLOW_CONSTANTS["cli_log_tail"]

    def test_model_argument(self):
        """Test model argument."""
        args = parse_args(["--model", "gpt-5-codex"])

        assert args.model == "gpt-5-codex"

    def test_reasoning_effort_choices(self):
        """Test reasoning effort choices."""
        for choice in ["low", "medium", "high"]:
            args = parse_args(["--reasoning-effort", choice])
            assert args.reasoning_effort == choice

    def test_patch_approval_mode_choices(self):
        """Test patch approval mode choices."""
        for mode in ["prompt", "auto"]:
            args = parse_args(["--patch-approval-mode", mode])
            assert args.patch_approval_mode == mode

    def test_boolean_flags(self):
        """Test boolean flag arguments."""
        args = parse_args(["--dry-run", "--auto-stage", "--commit-message"])

        assert args.dry_run is True
        assert args.auto_stage is True
        assert args.commit_message is True

    def test_env_file_default(self):
        """Test env-file default value."""
        args = parse_args([])

        assert args.env_file == "~/.env"

    def test_env_file_custom(self):
        """Test custom env-file argument."""
        args = parse_args(["--env-file", "/custom/.env"])

        assert args.env_file == "/custom/.env"

    def test_commit_extra_context(self):
        """Test commit-extra-context argument."""
        args = parse_args(["--commit-extra-context", "Fix bug #123"])

        assert args.commit_extra_context == "Fix bug #123"

    def test_max_patch_lines(self):
        """Test max-patch-lines argument."""
        args = parse_args(["--max-patch-lines", "2000"])

        assert args.max_patch_lines == WORKFLOW_CONSTANTS["max_patch_lines"]

    def test_patch_retries(self):
        """Test patch-retries argument."""
        args = parse_args(["--patch-retries", "3"])

        assert args.patch_retries == WORKFLOW_CONSTANTS["patch_retries"]


class TestMain:
    """Tests for main function."""

    @patch("ci_tools.ci_runtime.workflow.parse_args")
    @patch("ci_tools.ci_runtime.workflow.configure_runtime")
    @patch("ci_tools.ci_runtime.workflow.perform_dry_run")
    def test_dry_run_exits_early(self, mock_dry_run, mock_config, mock_parse):
        """Test dry-run mode exits early."""
        mock_parse.return_value = Mock()
        mock_config.return_value = Mock()
        mock_dry_run.return_value = 0

        result = main([])

        assert result == 0

    @patch("ci_tools.ci_runtime.workflow.parse_args")
    @patch("ci_tools.ci_runtime.workflow.configure_runtime")
    @patch("ci_tools.ci_runtime.workflow.perform_dry_run")
    @patch("ci_tools.ci_runtime.workflow.run_repair_iterations")
    @patch("ci_tools.ci_runtime.workflow.finalize_worktree")
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def test_successful_workflow(
        self, mock_finalize, mock_repair, mock_dry_run, mock_config, mock_parse
    ):
        """Test successful workflow execution."""
        mock_parse.return_value = Mock()
        mock_config.return_value = Mock()
        mock_dry_run.return_value = None
        mock_finalize.return_value = 0

        result = main([])

        assert result == 0
        mock_repair.assert_called_once()
        mock_finalize.assert_called_once()

    @patch("ci_tools.ci_runtime.workflow.parse_args")
    @patch("ci_tools.ci_runtime.workflow.configure_runtime")
    @patch("ci_tools.ci_runtime.workflow.perform_dry_run")
    @patch("ci_tools.ci_runtime.workflow.run_repair_iterations")
    def test_handles_keyboard_interrupt(self, mock_repair, mock_dry_run, mock_config, mock_parse):
        """Test handling Ctrl-C gracefully."""
        mock_parse.return_value = Mock()
        mock_config.return_value = Mock()
        mock_dry_run.return_value = None
        mock_repair.side_effect = KeyboardInterrupt()

        result = main([])

        assert result == WORKFLOW_CONSTANTS["main_failure_code"]

    @patch("ci_tools.ci_runtime.workflow.parse_args")
    @patch("ci_tools.ci_runtime.workflow.configure_runtime")
    @patch("ci_tools.ci_runtime.workflow.perform_dry_run")
    @patch("ci_tools.ci_runtime.workflow.run_repair_iterations")
    def test_handles_ci_abort(self, mock_repair, mock_dry_run, mock_config, mock_parse):
        """Test handling CiAbort exception."""
        mock_parse.return_value = Mock()
        mock_config.return_value = Mock()
        mock_dry_run.return_value = None
        mock_repair.side_effect = CiAbort(detail="test abort", code=42)

        result = main([])

        assert result == WORKFLOW_CONSTANTS["main_success_code"]

    @patch("ci_tools.ci_runtime.workflow.parse_args")
    @patch("ci_tools.ci_runtime.workflow.configure_runtime")
    def test_handles_model_selection_abort(self, mock_config, mock_parse):
        """Test handling model selection abort."""
        mock_parse.return_value = Mock()
        mock_config.side_effect = ModelSelectionAbort(detail="wrong model", code=1)

        result = main([])

        assert result == 1

    @patch("ci_tools.ci_runtime.workflow.parse_args")
    @patch("ci_tools.ci_runtime.workflow.configure_runtime")
    @patch("ci_tools.ci_runtime.workflow.perform_dry_run")
    @patch("ci_tools.ci_runtime.workflow.run_repair_iterations")
    @patch("ci_tools.ci_runtime.workflow.finalize_worktree")
    def test_passes_custom_argv(
        self, mock_finalize, _mock_repair, mock_dry_run, mock_config, mock_parse
    ):
        """Test passing custom argv to parse_args."""
        mock_parse.return_value = Mock()
        mock_config.return_value = Mock()
        mock_dry_run.return_value = None
        mock_finalize.return_value = 0

        main(["--command", "pytest"])

        mock_parse.assert_called_once_with(["--command", "pytest"])
