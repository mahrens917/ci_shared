"""Unit tests for generate_commit_message module."""

from __future__ import annotations

import json

from pathlib import Path
from unittest.mock import patch

import pytest

from ci_tools.scripts import generate_commit_message as commit_message_module
from ci_tools.scripts.generate_commit_message import (
    _build_chunk_summary_diff,
    _chunk_by_lines,
    _chunk_by_sections,
    _chunk_diff,
    _get_config_int,
    _prepare_payload,
    _read_staged_diff,
    _request_with_chunking,
    _split_diff_sections,
    _write_payload,
    main,
    parse_args,
)


def test_parse_args_default():
    """Test parse_args with default arguments."""
    args = parse_args([])
    assert args.model is None
    assert args.reasoning is None
    assert args.detailed is False
    assert args.output is None


def test_parse_args_with_model():
    """Test parse_args with model specified."""
    args = parse_args(["--model", "gpt-5-codex"])
    assert args.model == "gpt-5-codex"


def test_parse_args_with_reasoning():
    """Test parse_args with reasoning specified."""
    args = parse_args(["--reasoning", "high"])
    assert args.reasoning == "high"


def test_parse_args_with_detailed():
    """Test parse_args with detailed flag."""
    args = parse_args(["--detailed"])
    assert args.detailed is True


def test_parse_args_with_output():
    """Test parse_args with output file specified."""
    args = parse_args(["--output", "/tmp/commit.txt"])
    assert args.output == Path("/tmp/commit.txt")


def _write_config(path: Path, data: dict[str, object]) -> None:
    """Persist JSON data for config files used in tests."""
    path.write_text(json.dumps(data), encoding="utf-8")


def testget_commit_config_prefers_repo(monkeypatch, tmp_path):
    """Repo root config should win when commit_message is present."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    repo_config = repo_root / "ci_shared.config.json"
    _write_config(repo_config, {"commit_message": {"model": "repo"}})

    shared_root = tmp_path / "shared"
    shared_root.mkdir()
    shared_config = shared_root / "ci_shared.config.json"
    _write_config(shared_config, {"commit_message": {"model": "shared"}})

    monkeypatch.setattr(commit_message_module, "CI_SHARED_ROOT", shared_root)
    monkeypatch.setattr(
        commit_message_module, "detect_repo_root", lambda: repo_root
    )

    assert commit_message_module.get_commit_config()["model"] == "repo"


def testget_commit_config_falls_back_to_shared(monkeypatch, tmp_path):
    """Fallback to shared config when repo config lacks commit_message."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_config(repo_root / "ci_shared.config.json", {"repo_context": "local"})

    shared_root = tmp_path / "shared"
    shared_root.mkdir()
    _write_config(
        shared_root / "ci_shared.config.json",
        {"commit_message": {"reasoning": "medium"}},
    )

    monkeypatch.setattr(commit_message_module, "CI_SHARED_ROOT", shared_root)
    monkeypatch.setattr(
        commit_message_module, "detect_repo_root", lambda: repo_root
    )

    assert (
        commit_message_module.get_commit_config()["reasoning"]
        == "medium"
    )


@patch("ci_tools.scripts.generate_commit_message.gather_git_diff")
def test_read_staged_diff(mock_gather_git_diff):
    """Test _read_staged_diff calls gather_git_diff."""
    mock_gather_git_diff.return_value = "diff content"
    result = _read_staged_diff()
    assert result == "diff content"
    mock_gather_git_diff.assert_called_once_with(staged=True)


def test_prepare_payload_summary_only():
    """Test _prepare_payload with summary only."""
    result = _prepare_payload("Fix bug", [])
    assert result == "Fix bug"


def test_prepare_payload_with_body():
    """Test _prepare_payload with summary and body."""
    result = _prepare_payload("Fix bug", ["Details here", "More details"])
    assert result == "Fix bug\nDetails here\nMore details"


def test_prepare_payload_strips_whitespace():
    """Test _prepare_payload strips trailing whitespace and leading/trailing body whitespace."""
    result = _prepare_payload("  Fix bug  ", ["  Details  ", "  More  "])
    # Summary is stripped, trailing whitespace is removed from each line,
    # then the whole body is stripped (removing leading spaces from first line only)
    assert result == "Fix bug\nDetails\n  More"


def test_prepare_payload_empty_body_lines():
    """Test _prepare_payload handles empty body lines."""
    result = _prepare_payload("Fix bug", ["", "  ", ""])
    assert result == "Fix bug"


def test_write_payload_to_stdout(capsys):
    """Test _write_payload writes to stdout when output_path is None."""
    result = _write_payload("Test commit message", None)
    assert result == 0
    captured = capsys.readouterr()
    assert captured.out == "Test commit message\n"


def test_write_payload_to_file(tmp_path):
    """Test _write_payload writes to file when output_path specified."""
    output_file = tmp_path / "commit.txt"
    result = _write_payload("Test commit message", output_file)
    assert result == 0
    assert output_file.read_text() == "Test commit message\n"


def test_write_payload_file_error(tmp_path):
    """Test _write_payload handles OSError."""
    # Try to write to a directory (will cause OSError)
    output_dir = tmp_path / "subdir"
    output_dir.mkdir()
    result = _write_payload("Test commit message", output_dir)
    assert result == 1


@patch("ci_tools.scripts.generate_commit_message.get_commit_config")
@patch("ci_tools.scripts.generate_commit_message.gather_git_diff")
@patch("ci_tools.scripts.generate_commit_message.request_commit_message")
@patch("ci_tools.scripts.generate_commit_message.resolve_model_choice")
@patch("ci_tools.scripts.generate_commit_message.resolve_reasoning_choice")
def test_main_success(
    mock_resolve_reasoning,
    mock_resolve_model,
    mock_request_commit,
    mock_gather_diff,
    mock_get_config,
    capsys,
):
    """Test main with successful commit message generation."""
    mock_get_config.return_value = {
        "model": "gpt-5-codex",
        "reasoning": "medium",
        "chunk_line_limit": 1000,
        "max_chunks": 5,
    }
    mock_gather_diff.return_value = "diff --git a/file.py b/file.py"
    mock_resolve_model.return_value = "gpt-5-codex"
    mock_resolve_reasoning.return_value = "medium"
    mock_request_commit.return_value = ("Fix bug", [])

    result = main([])
    assert result == 0
    captured = capsys.readouterr()
    assert "Fix bug" in captured.out


@patch("ci_tools.scripts.generate_commit_message.get_commit_config")
@patch("ci_tools.scripts.generate_commit_message.gather_git_diff")
def test_main_no_staged_diff(mock_gather_diff, mock_get_config):
    """Test main exits with error when no staged diff."""
    mock_get_config.return_value = {
        "model": "gpt-5-codex",
        "reasoning": "medium",
        "chunk_line_limit": 1000,
        "max_chunks": 5,
    }
    mock_gather_diff.return_value = ""
    result = main([])
    assert result == 1


@patch("ci_tools.scripts.generate_commit_message.get_commit_config")
@patch("ci_tools.scripts.generate_commit_message.gather_git_diff")
@patch("ci_tools.scripts.generate_commit_message.request_commit_message")
@patch("ci_tools.scripts.generate_commit_message.resolve_model_choice")
@patch("ci_tools.scripts.generate_commit_message.resolve_reasoning_choice")
def test_main_empty_summary(
    mock_resolve_reasoning,
    mock_resolve_model,
    mock_request_commit,
    mock_gather_diff,
    mock_get_config,
):
    """Test main exits with error when commit message is empty."""
    mock_get_config.return_value = {
        "model": "gpt-5-codex",
        "reasoning": "medium",
        "chunk_line_limit": 1000,
        "max_chunks": 5,
    }
    mock_gather_diff.return_value = "diff --git a/file.py b/file.py"
    mock_resolve_model.return_value = "gpt-5-codex"
    mock_resolve_reasoning.return_value = "medium"
    mock_request_commit.return_value = ("", [])

    result = main([])
    assert result == 1


@patch("ci_tools.scripts.generate_commit_message.get_commit_config")
@patch("ci_tools.scripts.generate_commit_message.gather_git_diff")
@patch("ci_tools.scripts.generate_commit_message.request_commit_message")
@patch("ci_tools.scripts.generate_commit_message.resolve_model_choice")
@patch("ci_tools.scripts.generate_commit_message.resolve_reasoning_choice")
def test_main_codex_exception(
    mock_resolve_reasoning,
    mock_resolve_model,
    mock_request_commit,
    mock_gather_diff,
    mock_get_config,
):
    """Test main propagates Codex exceptions."""
    mock_get_config.return_value = {
        "model": "gpt-5-codex",
        "reasoning": "medium",
        "chunk_line_limit": 1000,
        "max_chunks": 5,
    }
    mock_gather_diff.return_value = "diff --git a/file.py b/file.py"
    mock_resolve_model.return_value = "gpt-5-codex"
    mock_resolve_reasoning.return_value = "medium"
    mock_request_commit.side_effect = Exception("Codex failed")

    with pytest.raises(Exception, match="Codex failed"):
        main([])


@patch("ci_tools.scripts.generate_commit_message.get_commit_config")
@patch("ci_tools.scripts.generate_commit_message.gather_git_diff")
@patch("ci_tools.scripts.generate_commit_message.request_commit_message")
@patch("ci_tools.scripts.generate_commit_message.resolve_model_choice")
@patch("ci_tools.scripts.generate_commit_message.resolve_reasoning_choice")
def test_main_with_detailed_flag(
    mock_resolve_reasoning,
    mock_resolve_model,
    mock_request_commit,
    mock_gather_diff,
    mock_get_config,
    capsys,
):
    """Test main with detailed flag includes body."""
    mock_get_config.return_value = {
        "model": "gpt-5-codex",
        "reasoning": "medium",
        "chunk_line_limit": 1000,
        "max_chunks": 5,
    }
    mock_gather_diff.return_value = "diff --git a/file.py b/file.py"
    mock_resolve_model.return_value = "gpt-5-codex"
    mock_resolve_reasoning.return_value = "medium"
    mock_request_commit.return_value = ("Fix bug", ["Detailed explanation"])

    result = main(["--detailed"])
    assert result == 0
    captured = capsys.readouterr()
    assert "Fix bug" in captured.out
    assert "Detailed explanation" in captured.out


@patch("ci_tools.scripts.generate_commit_message.get_commit_config")
@patch("ci_tools.scripts.generate_commit_message.gather_git_diff")
@patch("ci_tools.scripts.generate_commit_message.request_commit_message")
@patch("ci_tools.scripts.generate_commit_message.resolve_model_choice")
@patch("ci_tools.scripts.generate_commit_message.resolve_reasoning_choice")
def test_main_with_output_file(
    mock_resolve_reasoning,
    mock_resolve_model,
    mock_request_commit,
    mock_gather_diff,
    mock_get_config,
    tmp_path,
):
    """Test main writes to output file when specified."""
    mock_get_config.return_value = {
        "model": "gpt-5-codex",
        "reasoning": "medium",
        "chunk_line_limit": 1000,
        "max_chunks": 5,
    }
    output_file = tmp_path / "commit.txt"
    mock_gather_diff.return_value = "diff --git a/file.py b/file.py"
    mock_resolve_model.return_value = "gpt-5-codex"
    mock_resolve_reasoning.return_value = "medium"
    mock_request_commit.return_value = ("Fix bug", [])

    result = main(["--output", str(output_file)])
    assert result == 0
    assert output_file.exists()
    assert "Fix bug" in output_file.read_text()


@patch.dict(
    "os.environ",
    {
        "CI_COMMIT_MODEL": "gpt-5-codex",
        "CI_COMMIT_REASONING": "medium",
    },
)
@patch("ci_tools.scripts.generate_commit_message.get_commit_config")
@patch("ci_tools.scripts.generate_commit_message.gather_git_diff")
@patch("ci_tools.scripts.generate_commit_message.request_commit_message")
@patch("ci_tools.scripts.generate_commit_message.resolve_model_choice")
@patch("ci_tools.scripts.generate_commit_message.resolve_reasoning_choice")
def test_main_uses_env_var_for_model(
    mock_resolve_reasoning,
    mock_resolve_model,
    mock_request_commit,
    mock_gather_diff,
    mock_get_config,
):
    """Test main uses CI_COMMIT_MODEL env var."""
    mock_get_config.return_value = {
        "model": "config-model",
        "reasoning": "low",
        "chunk_line_limit": 1000,
        "max_chunks": 5,
    }
    mock_gather_diff.return_value = "diff --git a/file.py b/file.py"
    mock_resolve_model.return_value = "gpt-5-codex"
    mock_resolve_reasoning.return_value = "medium"
    mock_request_commit.return_value = ("Fix bug", [])

    result = main([])
    assert result == 0
    # Env var takes precedence over config
    mock_resolve_model.assert_called_once_with("gpt-5-codex", validate=False)


@patch.dict(
    "os.environ",
    {
        "CI_COMMIT_MODEL": "gpt-5-codex",
        "CI_COMMIT_REASONING": "high",
    },
)
@patch("ci_tools.scripts.generate_commit_message.get_commit_config")
@patch("ci_tools.scripts.generate_commit_message.gather_git_diff")
@patch("ci_tools.scripts.generate_commit_message.request_commit_message")
@patch("ci_tools.scripts.generate_commit_message.resolve_model_choice")
@patch("ci_tools.scripts.generate_commit_message.resolve_reasoning_choice")
def test_main_uses_env_var_for_reasoning(
    mock_resolve_reasoning,
    mock_resolve_model,
    mock_request_commit,
    mock_gather_diff,
    mock_get_config,
):
    """Test main uses CI_COMMIT_REASONING env var."""
    mock_get_config.return_value = {
        "model": "config-model",
        "reasoning": "low",
        "chunk_line_limit": 1000,
        "max_chunks": 5,
    }
    mock_gather_diff.return_value = "diff --git a/file.py b/file.py"
    mock_resolve_model.return_value = "gpt-5-codex"
    mock_resolve_reasoning.return_value = "high"
    mock_request_commit.return_value = ("Fix bug", [])

    result = main([])
    assert result == 0
    # Env var takes precedence over config
    mock_resolve_reasoning.assert_called_once_with("high", validate=False)


@patch("ci_tools.scripts.generate_commit_message.get_commit_config")
@patch("ci_tools.scripts.generate_commit_message.gather_git_diff")
@patch("ci_tools.scripts.generate_commit_message.request_commit_message")
@patch("ci_tools.scripts.generate_commit_message.resolve_model_choice")
@patch("ci_tools.scripts.generate_commit_message.resolve_reasoning_choice")
def test_main_uses_config_file_values(
    mock_resolve_reasoning,
    mock_resolve_model,
    mock_request_commit,
    mock_gather_diff,
    mock_get_config,
):
    """Test main uses config file values when no env vars or CLI args."""
    mock_get_config.return_value = {
        "model": "claude-sonnet-4-20250514",
        "reasoning": "medium",
        "chunk_line_limit": 6000,
        "max_chunks": 4,
    }
    mock_gather_diff.return_value = "diff --git a/file.py b/file.py"
    mock_resolve_model.return_value = "claude-sonnet-4-20250514"
    mock_resolve_reasoning.return_value = "medium"
    mock_request_commit.return_value = ("Fix bug", [])

    result = main([])
    assert result == 0
    mock_resolve_model.assert_called_once_with("claude-sonnet-4-20250514", validate=False)
    mock_resolve_reasoning.assert_called_once_with("medium", validate=False)


@patch.dict("os.environ", {}, clear=True)
def test_get_config_int_raises_when_missing():
    """Raises ValueError when env var and config key missing."""
    with pytest.raises(ValueError, match="NON_EXISTENT env var or 'missing_key' in config file"):
        _get_config_int({}, "missing_key", "NON_EXISTENT")


@patch.dict("os.environ", {"TEST_VALUE": "123"}, clear=True)
def test_get_config_int_prefers_env_var():
    """Env var takes precedence over config value."""
    assert _get_config_int({"chunk_line_limit": 999}, "chunk_line_limit", "TEST_VALUE") == 123


@patch.dict("os.environ", {}, clear=True)
def test_get_config_int_uses_config():
    """Falls back to config value when env var missing."""
    assert _get_config_int({"chunk_line_limit": 6000}, "chunk_line_limit", "MISSING_ENV") == 6000


@patch.dict("os.environ", {"TEST_VALUE": "not-a-number"}, clear=True)
def test_get_config_int_raises_on_invalid():
    """Raises ValueError on invalid numbers."""
    with pytest.raises(ValueError):
        _get_config_int({}, "key", "TEST_VALUE")


def test_split_diff_sections_breaks_on_headers():
    """Split diff into sections per file."""
    diff_text = (
        "diff --git a/a.txt b/a.txt\n+1\n"
        "diff --git a/b.txt b/b.txt\n+2\n+3\n"
        "diff --git a/c.txt b/c.txt\n@@ -1 +1 @@\n-foo\n+bar\n"
    )
    sections = _split_diff_sections(diff_text)
    assert len(sections) == 3
    assert sections[0].startswith("diff --git a/a.txt")
    assert "+2" in sections[1]
    assert "a/c.txt" in sections[2]


def test_chunk_by_sections_respects_limits():
    """Chunk sections based on line budget."""
    sections = [
        "diff --git a/a b/a\n+1",
        "diff --git a/b b/b\n+2\n+3",
        "diff --git a/c b/c\n+4",
    ]
    chunks = _chunk_by_sections(sections, max_lines=2, max_chunks=3)
    assert len(chunks) == 3
    assert "+1" in chunks[0]
    assert "+2" in chunks[1]


def test_chunk_by_lines_evenly_distributes():
    """Chunk diff by raw line count when needed."""
    diff_text = "\n".join(f"+line {i}" for i in range(12))
    chunks = _chunk_by_lines(diff_text, chunk_count=4)
    assert len(chunks) == 4
    assert chunks[0].startswith("+line 0")
    assert chunks[-1].endswith("+line 11")


def test_chunk_diff_handles_large_diffs():
    """Chunk diff using section boundaries and fall back to lines."""
    diff_text = "\n".join(f"diff --git a/file{i}.py b/file{i}.py\n+line {i}\n" for i in range(5))
    chunks = _chunk_diff(diff_text, max_chunk_lines=3, max_chunks=4)
    assert len(chunks) > 1
    combined = "\n\n".join(chunks)
    for i in range(5):
        assert f"file{i}.py" in combined


def test_build_chunk_summary_diff_formats_output():
    """Synthesized diff includes chunk headers and summary text."""
    summary_diff = _build_chunk_summary_diff([("Added feature", ["- detail a", "- detail b"])])
    assert "diff --git a/chunk_1 b/chunk_1" in summary_diff
    assert "+ chunk 1 summary: Added feature" in summary_diff
    assert "- detail a" in summary_diff


@patch("ci_tools.scripts.generate_commit_message.request_commit_message")
def test_request_with_chunking_combines_results(mock_request):
    """Ensure request_with_chunking summarizes chunks then finalizes."""
    mock_request.side_effect = [
        ("Chunk1 summary", ["detail 1"]),
        ("Chunk2 summary", []),
        ("Final summary", ["Final body"]),
    ]
    summary, body = _request_with_chunking(
        chunks=["diff --git a/a b/a\n+1", "diff --git a/b b/b\n+2"],
        model="gpt-5-codex",
        reasoning_effort="medium",
        detailed=True,
    )
    assert summary == "Final summary"
    assert body == ["Final body"]
    assert mock_request.call_count == 3
    first_call_kwargs = mock_request.call_args_list[0].kwargs
    assert "chunk 1/2" in first_call_kwargs["extra_context"]
    final_call_kwargs = mock_request.call_args_list[-1].kwargs
    assert "synthesized summary" in final_call_kwargs["extra_context"]
