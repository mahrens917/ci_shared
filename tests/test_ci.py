"""Unit tests for ci_tools.ci_runtime module."""

from __future__ import annotations

# Import all exports from ci_runtime to verify they're accessible
from ci_tools.ci_runtime import (
    PatchPrompt,
    apply_patch,
    build_codex_command,
    build_failure_context,
    commit_and_push,
    configure_runtime,
    extract_coverage_deficits,
    extract_unified_diff,
    finalize_worktree,
    gather_file_diff,
    gather_git_diff,
    gather_git_status,
    has_unified_diff_header,
    invoke_codex,
    log_codex_interaction,
    main,
    patch_looks_risky,
    perform_dry_run,
    request_and_apply_patches,
    request_codex_patch,
    request_commit_message,
    run_command,
    run_repair_iterations,
    tail_text,
    truncate_diff_summary,
    truncate_error,
)
from ci_tools.ci_runtime.messaging import (
    request_commit_message as msg_request,
)
from ci_tools.ci_runtime.models import PatchPrompt as models_prompt
from ci_tools.ci_runtime.patching import apply_patch as patching_apply
from ci_tools.ci_runtime.process import run_command as process_run
from ci_tools.ci_runtime.workflow import (
    configure_runtime as workflow_cfg,
    main as workflow_main,
)
from ci_tools.ci_runtime import workflow, process, patching, messaging
import ci_tools.ci_runtime as ci_module


class TestCiRuntimeExports:
    """Tests verifying the ci_runtime package exports."""

    def test_all_exports_accessible(self):
        """Test that all exported functions are accessible from ci_runtime."""
        # Verify main entry point is callable
        assert callable(main)

        # Verify runtime configuration functions
        assert callable(configure_runtime)
        assert callable(perform_dry_run)
        assert callable(run_repair_iterations)
        assert callable(finalize_worktree)

        # Verify command execution functions
        assert callable(run_command)
        assert callable(tail_text)

        # Verify git helpers
        assert callable(gather_git_diff)
        assert callable(gather_git_status)
        assert callable(gather_file_diff)

        # Verify Codex interaction functions
        assert callable(invoke_codex)
        assert callable(build_codex_command)
        assert callable(request_codex_patch)
        assert callable(request_commit_message)
        assert callable(log_codex_interaction)

        # Verify patch functions
        assert callable(apply_patch)
        assert callable(patch_looks_risky)
        assert callable(request_and_apply_patches)
        assert callable(extract_unified_diff)
        assert callable(has_unified_diff_header)

        # Verify failure handling
        assert callable(build_failure_context)
        assert callable(extract_coverage_deficits)
        assert callable(truncate_error)
        assert callable(truncate_diff_summary)

        # Verify commit/push functions
        assert callable(commit_and_push)

        # Verify data models are accessible
        assert PatchPrompt is not None

    def test_main_is_reexported_from_workflow(self):
        """Test that main function is correctly re-exported."""
        assert main is workflow_main

    def test_configure_runtime_is_reexported(self):
        """Test that configure_runtime is correctly re-exported."""
        assert configure_runtime is workflow_cfg

    def test_run_command_is_reexported(self):
        """Test that run_command is correctly re-exported."""
        assert run_command is process_run

    def test_apply_patch_is_reexported(self):
        """Test that apply_patch is correctly re-exported."""
        assert apply_patch is patching_apply

    def test_request_commit_message_is_reexported(self):
        """Test that request_commit_message is correctly re-exported."""
        assert request_commit_message is msg_request

    def test_patch_prompt_model_is_reexported(self):
        """Test that PatchPrompt model is correctly re-exported."""
        assert PatchPrompt is models_prompt


class TestCiModuleStructure:
    """Tests for ci_runtime module structure and organization."""

    def test_module_has_all_attribute(self):
        """Test that ci_runtime defines __all__ for explicit exports."""
        assert hasattr(ci_module, "__all__")
        assert isinstance(ci_module.__all__, list)
        assert len(ci_module.__all__) > 0

    def test_all_items_in_all_are_exported(self):
        """Test that every item in __all__ is actually exported."""
        for name in ci_module.__all__:
            assert hasattr(ci_module, name), f"{name} in __all__ but not exported"

    def test_main_entry_point_in_all(self):
        """Test that main entry point is in __all__."""
        assert "main" in ci_module.__all__

    def test_core_workflow_functions_in_all(self):
        """Test that core workflow functions are in __all__."""
        core_functions = [
            "configure_runtime",
            "perform_dry_run",
            "run_repair_iterations",
            "finalize_worktree",
        ]
        for func in core_functions:
            assert func in ci_module.__all__

    def test_command_execution_functions_in_all(self):
        """Test that command execution functions are in __all__."""
        command_functions = ["run_command", "tail_text"]
        for func in command_functions:
            assert func in ci_module.__all__

    def test_git_functions_in_all(self):
        """Test that git helper functions are in __all__."""
        git_functions = ["gather_git_diff", "gather_git_status", "gather_file_diff"]
        for func in git_functions:
            assert func in ci_module.__all__

    def test_codex_functions_in_all(self):
        """Test that Codex interaction functions are in __all__."""
        codex_functions = [
            "invoke_codex",
            "build_codex_command",
            "request_codex_patch",
            "request_commit_message",
            "log_codex_interaction",
        ]
        for func in codex_functions:
            assert func in ci_module.__all__

    def test_patch_functions_in_all(self):
        """Test that patch handling functions are in __all__."""
        patch_functions = [
            "apply_patch",
            "patch_looks_risky",
            "request_and_apply_patches",
            "extract_unified_diff",
            "has_unified_diff_header",
        ]
        for func in patch_functions:
            assert func in ci_module.__all__

    def test_models_in_all(self):
        """Test that data models are in __all__."""
        assert "PatchPrompt" in ci_module.__all__


class TestCiIntegration:
    """Integration tests for the ci_runtime package."""

    def test_can_import_from_ci_module_directly(self):
        """Test that functions can be imported directly from ci_tools.ci_runtime."""
        # Verify imports already at module level work
        assert main is not None
        assert configure_runtime is not None
        assert run_command is not None

    def test_ci_module_provides_same_interface_as_runtime(self):
        """Test that ci_runtime provides consistent interface with submodules."""
        # Verify workflow functions
        assert ci_module.main is workflow.main
        assert ci_module.configure_runtime is workflow.configure_runtime

        # Verify process functions
        assert ci_module.run_command is process.run_command
        assert ci_module.tail_text is process.tail_text

        # Verify patching functions
        assert ci_module.apply_patch is patching.apply_patch
        assert ci_module.patch_looks_risky is patching.patch_looks_risky

        # Verify messaging functions
        assert ci_module.request_commit_message is messaging.request_commit_message

    def test_ci_module_docstring_exists(self):
        """Test that ci_runtime has a module docstring."""
        assert ci_module.__doc__ is not None
        assert len(ci_module.__doc__.strip()) > 0
