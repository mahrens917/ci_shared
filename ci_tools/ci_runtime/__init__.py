"""Public API for the Claude CI runtime package."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import claude_cli as _claude_cli
from . import coverage as _coverage
from . import failures as _failures
from . import messaging as _messaging
from . import patch_cycle as _patch_cycle
from . import patching as _patching
from . import process as _process
from . import workflow as _workflow
from .models import PatchPrompt

if TYPE_CHECKING:  # pragma: no cover - type checking helper block
    build_claude_command = _claude_cli.build_claude_command
    extract_unified_diff = _claude_cli.extract_unified_diff
    has_unified_diff_header = _claude_cli.has_unified_diff_header
    invoke_claude = _claude_cli.invoke_claude
    request_claude_patch = _claude_cli.request_claude_patch
    risky_pattern_in_diff = _claude_cli.risky_pattern_in_diff
    truncate_diff_summary = _claude_cli.truncate_diff_summary
    truncate_error = _claude_cli.truncate_error
    extract_coverage_deficits = _coverage.extract_coverage_deficits
    build_failure_context = _failures.build_failure_context
    commit_and_push = _messaging.commit_and_push
    request_commit_message = _messaging.request_commit_message
    request_and_apply_patches = _patch_cycle.request_and_apply_patches
    apply_patch = _patching.apply_patch
    patch_looks_risky = _patching.patch_looks_risky
    gather_file_diff = _process.gather_file_diff
    gather_git_diff = _process.gather_git_diff
    gather_git_diff_limited = _process.gather_git_diff_limited
    gather_git_status = _process.gather_git_status
    log_cli_interaction = _process.log_cli_interaction
    run_command = _process.run_command
    tail_text = _process.tail_text
    configure_runtime = _workflow.configure_runtime
    finalize_worktree = _workflow.finalize_worktree
    main = _workflow.main
    perform_dry_run = _workflow.perform_dry_run
    run_repair_iterations = _workflow.run_repair_iterations

_MODULE_EXPORTS = [
    (_claude_cli, _claude_cli.__all__),
    (_coverage, _coverage.__all__),
    (_failures, _failures.__all__),
    (_messaging, _messaging.__all__),
    (_patch_cycle, _patch_cycle.__all__),
    (_patching, _patching.__all__),
    (_process, _process.__all__),
    (_workflow, _workflow.__all__),
]

# Static __all__ definition required for pyright analysis
__all__ = [
    # ---- models ----
    "PatchPrompt",
    # ---- claude_cli module exports ----
    "build_claude_command",
    "extract_unified_diff",
    "has_unified_diff_header",
    "invoke_claude",
    "request_claude_patch",
    "risky_pattern_in_diff",
    "truncate_diff_summary",
    "truncate_error",
    # ---- coverage ----
    "extract_coverage_deficits",
    # ---- failures ----
    "build_failure_context",
    # ---- messaging ----
    "commit_and_push",
    "request_commit_message",
    # ---- patch_cycle ----
    "request_and_apply_patches",
    # ---- patching ----
    "apply_patch",
    "patch_looks_risky",
    # ---- process ----
    "gather_file_diff",
    "gather_git_diff",
    "gather_git_diff_limited",
    "gather_git_status",
    "log_cli_interaction",
    "run_command",
    "tail_text",
    # ---- workflow ----
    "configure_runtime",
    "finalize_worktree",
    "main",
    "perform_dry_run",
    "run_repair_iterations",
]

for module, exports in _MODULE_EXPORTS:
    for name in exports:
        globals()[name] = getattr(module, name)

globals()["PatchPrompt"] = PatchPrompt
