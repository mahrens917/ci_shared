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


def test_get_commit_config_prefers_repo(monkeypatch, tmp_path):
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


def test_get_commit_config_falls_back_to_shared(monkeypatch, tmp_path):
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


@patch("ci_tools.scripts.generate_commit_message.request_commit_message")
def test_request_with_chunking_reraises_chunk_exception(mock_request):
    """Exception during chunk processing is re-raised."""
    mock_request.side_effect = Exception("API failure")
    with pytest.raises(Exception, match="API failure"):
        _request_with_chunking(
            chunks=["diff --git a/a b/a\n+1"],
            model="gpt-5-codex",
            reasoning_effort="medium",
            detailed=False,
        )


@patch("ci_tools.scripts.generate_commit_message.request_commit_message")
def test_request_with_chunking_reraises_synthesis_exception(mock_request):
    """Exception during final synthesis is re-raised."""
    mock_request.side_effect = [
        ("Chunk1 summary", []),
        Exception("Synthesis failed"),
    ]
    with pytest.raises(Exception, match="Synthesis failed"):
        _request_with_chunking(
            chunks=["diff --git a/a b/a\n+1"],
            model="gpt-5-codex",
            reasoning_effort="medium",
            detailed=False,
        )


@patch("ci_tools.scripts.generate_commit_message.get_commit_config")
def test_main_missing_model(mock_get_config, capsys):
    """Test main exits with error when model not specified anywhere."""
    mock_get_config.return_value = {
        "reasoning": "medium",
        "chunk_line_limit": 1000,
        "max_chunks": 5,
    }
    result = main([])
    assert result == 1
    captured = capsys.readouterr()
    assert "Model must be specified" in captured.err


@patch("ci_tools.scripts.generate_commit_message.get_commit_config")
def test_main_missing_reasoning(mock_get_config, capsys):
    """Test main exits with error when reasoning not specified anywhere."""
    mock_get_config.return_value = {
        "model": "gpt-5-codex",
        "chunk_line_limit": 1000,
        "max_chunks": 5,
    }
    result = main([])
    assert result == 1
    captured = capsys.readouterr()
    assert "Reasoning must be specified" in captured.err


def test_split_diff_sections_empty_input():
    """Empty or whitespace-only diff returns empty list."""
    assert _split_diff_sections("") == []
    assert _split_diff_sections("   \n\n   ") == []


def test_chunk_by_sections_empty_returns_empty():
    """Empty sections list returns empty chunks."""
    assert _chunk_by_sections([], max_lines=10, max_chunks=5) == []


def test_chunk_by_sections_single_chunk_when_max_is_one():
    """Returns single combined chunk when max_chunks is 1."""
    sections = ["diff --git a/a b/a\n+1", "diff --git a/b b/b\n+2"]
    chunks = _chunk_by_sections(sections, max_lines=1, max_chunks=1)
    assert len(chunks) == 1
    assert "+1" in chunks[0] and "+2" in chunks[0]


def test_chunk_by_lines_empty_returns_empty():
    """Empty diff returns empty list."""
    assert _chunk_by_lines("", chunk_count=3) == []


def test_chunk_diff_returns_original_when_small():
    """Small diff that fits under limits returns as single chunk."""
    diff_text = "diff --git a/a b/a\n+1\n+2"
    chunks = _chunk_diff(diff_text, max_chunk_lines=100, max_chunks=5)
    assert chunks == [diff_text]


def test_chunk_diff_returns_original_when_max_lines_zero():
    """When max_chunk_lines is 0, return original diff."""
    diff_text = "diff --git a/a b/a\n" + "\n".join(f"+line{i}" for i in range(50))
    chunks = _chunk_diff(diff_text, max_chunk_lines=0, max_chunks=5)
    assert chunks == [diff_text]


def test_chunk_diff_returns_original_when_max_chunks_one():
    """When max_chunks is 1, return original diff."""
    diff_text = "diff --git a/a b/a\n" + "\n".join(f"+line{i}" for i in range(50))
    chunks = _chunk_diff(diff_text, max_chunk_lines=10, max_chunks=1)
    assert chunks == [diff_text]


def test_chunk_diff_falls_back_to_line_chunking():
    """Test chunking falls back to line-based when sections produce one chunk."""
    # Create a single large section that exceeds max_lines
    diff_text = "diff --git a/a b/a\n" + "\n".join(f"+line{i}" for i in range(20))
    chunks = _chunk_diff(diff_text, max_chunk_lines=5, max_chunks=10)
    assert len(chunks) > 1


def test_build_chunk_summary_diff_empty_summary():
    """Empty summary still produces valid placeholder output."""
    result = _build_chunk_summary_diff([("", []), ("", [])])
    assert "chunk_1" in result
    assert "chunk_2" in result


def test_build_chunk_summary_diff_all_empty_returns_placeholder():
    """When all summaries and bodies empty, returns placeholder."""
    # Create truly empty input that produces no content
    result = _build_chunk_summary_diff([])
    assert result == "+ chunk summary unavailable"


def test_get_commit_config_merges_shared_and_repo(monkeypatch, tmp_path):
    """Test that shared and repo configs are merged properly."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    repo_config = repo_root / "ci_shared.config.json"
    _write_config(repo_config, {"commit_message": {"model": "repo-model", "extra_key": "repo"}})

    shared_root = tmp_path / "shared"
    shared_root.mkdir()
    shared_config = shared_root / "ci_shared.config.json"
    _write_config(
        shared_config,
        {"commit_message": {"model": "shared-model", "reasoning": "high"}},
    )

    monkeypatch.setattr(commit_message_module, "CI_SHARED_ROOT", shared_root)
    monkeypatch.setattr(commit_message_module, "detect_repo_root", lambda: repo_root)

    config = commit_message_module.get_commit_config()
    # repo overrides shared for model
    assert config["model"] == "repo-model"
    # shared provides reasoning
    assert config["reasoning"] == "high"
    # repo provides extra_key
    assert config["extra_key"] == "repo"


def test_get_commit_config_raises_when_missing(monkeypatch, tmp_path):
    """Test get_commit_config raises KeyError when no commit_message found."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_config(repo_root / "ci_shared.config.json", {"other_section": {}})

    shared_root = tmp_path / "shared"
    shared_root.mkdir()
    _write_config(shared_root / "ci_shared.config.json", {"other_section": {}})

    monkeypatch.setattr(commit_message_module, "CI_SHARED_ROOT", shared_root)
    monkeypatch.setattr(commit_message_module, "detect_repo_root", lambda: repo_root)

    with pytest.raises(KeyError, match="commit_message section required"):
        commit_message_module.get_commit_config()


def test_config_search_roots_yields_both_when_different(monkeypatch, tmp_path):
    """Test _config_search_roots yields both repo and shared roots."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    shared_root = tmp_path / "shared"
    shared_root.mkdir()

    monkeypatch.setattr(commit_message_module, "CI_SHARED_ROOT", shared_root)
    monkeypatch.setattr(commit_message_module, "detect_repo_root", lambda: repo_root)

    roots = list(commit_message_module._config_search_roots())
    assert repo_root in roots
    assert shared_root in roots


def test_config_search_roots_yields_one_when_same(monkeypatch, tmp_path):
    """Test _config_search_roots yields single root when repo == shared."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    monkeypatch.setattr(commit_message_module, "CI_SHARED_ROOT", repo_root)
    monkeypatch.setattr(commit_message_module, "detect_repo_root", lambda: repo_root)

    roots = list(commit_message_module._config_search_roots())
    assert roots == [repo_root]


def test_load_config_from_root_returns_none_when_no_config(tmp_path):
    """Test _load_config_from_root returns None when no config file exists."""
    result = commit_message_module._load_config_from_root(tmp_path)
    assert result is None


def test_load_config_from_root_loads_first_candidate(tmp_path):
    """Test _load_config_from_root loads the first matching candidate."""
    config_path = tmp_path / "ci_shared.config.json"
    _write_config(config_path, {"key": "value"})
    result = commit_message_module._load_config_from_root(tmp_path)
    assert result == {"key": "value"}


def test_resolve_model_arg_cli_takes_precedence():
    """CLI arg takes precedence over env and config."""
    from ci_tools.scripts.generate_commit_message import _resolve_model_arg

    result = _resolve_model_arg("cli-model", {"model": "config-model"})
    assert result == "cli-model"


def test_resolve_model_arg_returns_config_when_no_cli_or_env(monkeypatch):
    """Config value used when no CLI arg or env var."""
    from ci_tools.scripts.generate_commit_message import _resolve_model_arg

    monkeypatch.delenv("CI_COMMIT_MODEL", raising=False)
    result = _resolve_model_arg(None, {"model": "config-model"})
    assert result == "config-model"


def test_resolve_reasoning_arg_cli_takes_precedence():
    """CLI arg takes precedence over env and config."""
    from ci_tools.scripts.generate_commit_message import _resolve_reasoning_arg

    result = _resolve_reasoning_arg("cli-reasoning", {"reasoning": "config-reasoning"})
    assert result == "cli-reasoning"


def test_resolve_reasoning_arg_returns_config_when_no_cli_or_env(monkeypatch):
    """Config value used when no CLI arg or env var."""
    from ci_tools.scripts.generate_commit_message import _resolve_reasoning_arg

    monkeypatch.delenv("CI_COMMIT_REASONING", raising=False)
    result = _resolve_reasoning_arg(None, {"reasoning": "config-reasoning"})
    assert result == "config-reasoning"


def test_get_commit_config_skips_missing_config_file(monkeypatch, tmp_path):
    """Test get_commit_config skips roots without config files."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    # No config file in repo_root

    shared_root = tmp_path / "shared"
    shared_root.mkdir()
    _write_config(
        shared_root / "ci_shared.config.json",
        {"commit_message": {"model": "shared-model"}},
    )

    monkeypatch.setattr(commit_message_module, "CI_SHARED_ROOT", shared_root)
    monkeypatch.setattr(commit_message_module, "detect_repo_root", lambda: repo_root)

    # Should skip repo_root (no config) and use shared_root
    config = commit_message_module.get_commit_config()
    assert config["model"] == "shared-model"


def test_chunk_diff_non_standard_diff_format():
    """Test _chunk_diff handles text without diff --git headers."""
    # Text that doesn't have "diff --git" headers - like a raw patch or malformed diff
    non_standard_diff = "\n".join([f"+line{i}" for i in range(20)])
    chunks = _chunk_diff(non_standard_diff, max_chunk_lines=5, max_chunks=10)
    # Should fall back to line-based chunking
    assert len(chunks) > 1
    combined = "\n".join(chunks)
    assert "+line0" in combined
    assert "+line19" in combined


def test_chunk_diff_whitespace_only_diff():
    """Test _chunk_diff handles whitespace-only diff that passes the line count check."""
    # Whitespace-only diff with multiple "lines" - edge case
    whitespace_diff = "   \n   \n   \n   \n   "
    chunks = _chunk_diff(whitespace_diff, max_chunk_lines=2, max_chunks=10)
    # Should return the original whitespace diff as a single chunk
    assert chunks == [whitespace_diff]


@patch("ci_tools.scripts.generate_commit_message.get_commit_config")
@patch("ci_tools.scripts.generate_commit_message.gather_git_diff")
@patch("ci_tools.scripts.generate_commit_message.request_commit_message")
@patch("ci_tools.scripts.generate_commit_message.resolve_model_choice")
@patch("ci_tools.scripts.generate_commit_message.resolve_reasoning_choice")
def test_main_with_multiple_chunks(
    mock_resolve_reasoning,
    mock_resolve_model,
    mock_request_commit,
    mock_gather_diff,
    mock_get_config,
    capsys,
):
    """Test main uses chunking when diff exceeds limits."""
    mock_get_config.return_value = {
        "model": "gpt-5-codex",
        "reasoning": "medium",
        "chunk_line_limit": 5,
        "max_chunks": 10,
    }
    # Create a diff large enough to be chunked (10 sections, 3 lines each = 30 lines)
    large_diff = "\n".join(f"diff --git a/file{i}.py b/file{i}.py\n+line{i}" for i in range(10))
    mock_gather_diff.return_value = large_diff
    mock_resolve_model.return_value = "gpt-5-codex"
    mock_resolve_reasoning.return_value = "medium"
    # Return for each of 5 chunks + final synthesis (6 calls total)
    mock_request_commit.side_effect = [
        ("Chunk1 summary", []),
        ("Chunk2 summary", []),
        ("Chunk3 summary", []),
        ("Chunk4 summary", []),
        ("Chunk5 summary", []),
        ("Final summary", ["body"]),
    ]

    result = main([])
    assert result == 0
    captured = capsys.readouterr()
    assert "Final summary" in captured.out
    assert "Large staged diff detected" in captured.err
