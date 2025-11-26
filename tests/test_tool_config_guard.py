"""Unit tests for tool_config_guard module."""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest


from ci_tools.scripts.tool_config_guard import (
    _compare_tool_section,
    _find_shared_config,
    _format_toml_list,
    _format_toml_value,
    _handle_config_mismatch,
    _print_toml_list,
    _print_toml_value,
    _print_tool_section,
    _validate_paths,
    compare_configs,
    extract_tool_config,
    format_toml_tool_section,
    load_toml,
    main,
    print_tool_config_diff,
    sync_configs,
)


def test_load_toml(tmp_path):
    """Test load_toml loads TOML file."""
    toml_file = tmp_path / "test.toml"
    toml_file.write_text("[tool.ruff]\nline-length = 100")
    result = load_toml(toml_file)
    assert "tool" in result
    assert result["tool"]["ruff"]["line-length"] == 100


def test_extract_tool_config():
    """Test extract_tool_config extracts tool section."""
    data = {
        "project": {"name": "test"},
        "tool": {"ruff": {"line-length": 100}},
    }
    result = extract_tool_config(data)
    assert "tool" in result
    assert "project" not in result


def test_extract_tool_config_no_tool():
    """Test extract_tool_config with no tool section."""
    data = {"project": {"name": "test"}}
    result = extract_tool_config(data)
    assert not result


def test_compare_configs_match():
    """Test compare_configs with matching configs."""
    shared = {"tool": {"ruff": {"line-length": 100}}}
    repo = {"tool": {"ruff": {"line-length": 100}}}
    matches, differences = compare_configs(shared, repo)
    assert matches is True
    assert not differences


def test_compare_configs_no_tool_in_shared():
    """Test compare_configs with no tool in shared config."""
    shared = {}
    repo = {"tool": {"ruff": {"line-length": 100}}}
    matches, differences = compare_configs(shared, repo)
    assert matches is True
    assert not differences


def test_compare_configs_no_tool_in_repo():
    """Test compare_configs with no tool in repo config."""
    shared = {"tool": {"ruff": {"line-length": 100}}}
    repo = {}
    matches, differences = compare_configs(shared, repo)
    assert matches is False
    assert len(differences) > 0


def test_compare_configs_missing_tool():
    """Test compare_configs with missing tool."""
    shared = {"tool": {"ruff": {"line-length": 100}, "bandit": {}}}
    repo = {"tool": {"ruff": {"line-length": 100}}}
    matches, differences = compare_configs(shared, repo)
    assert matches is False
    assert any("bandit" in diff for diff in differences)


def test_compare_configs_extra_tool_in_repo():
    """Test compare_configs allows extra tools in repo."""
    shared = {"tool": {"ruff": {"line-length": 100}}}
    repo = {"tool": {"ruff": {"line-length": 100}, "mypy": {}}}
    matches, _differences = compare_configs(shared, repo)
    assert matches is True


def test_compare_configs_value_mismatch():
    """Test compare_configs detects value mismatch."""
    shared = {"tool": {"ruff": {"line-length": 100}}}
    repo = {"tool": {"ruff": {"line-length": 80}}}
    matches, differences = compare_configs(shared, repo)
    assert matches is False
    assert len(differences) > 0


def test_compare_tool_section_dict_match():
    """Test _compare_tool_section with matching dicts."""
    shared = {"line-length": 100, "target-version": "py310"}
    repo = {"line-length": 100, "target-version": "py310"}
    differences = _compare_tool_section(shared, repo, "tool.ruff")
    assert not differences


def test_compare_tool_section_missing_key():
    """Test _compare_tool_section with missing key."""
    shared = {"line-length": 100, "target-version": "py310"}
    repo = {"line-length": 100}
    differences = _compare_tool_section(shared, repo, "tool.ruff")
    assert len(differences) > 0
    assert any("target-version" in diff for diff in differences)


def test_compare_tool_section_extra_key_in_repo():
    """Test _compare_tool_section allows extra keys in repo."""
    shared = {"line-length": 100}
    repo = {"line-length": 100, "extra-key": "value"}
    differences = _compare_tool_section(shared, repo, "tool.ruff")
    assert not differences


def test_compare_tool_section_nested_dict():
    """Test _compare_tool_section with nested dicts."""
    shared = {"lint": {"select": ["E", "F"]}}
    repo = {"lint": {"select": ["E", "F"]}}
    differences = _compare_tool_section(shared, repo, "tool.ruff")
    assert not differences


def test_compare_tool_section_value_mismatch():
    """Test _compare_tool_section with value mismatch."""
    shared = {"line-length": 100}
    repo = {"line-length": 80}
    differences = _compare_tool_section(shared, repo, "tool.ruff")
    assert len(differences) > 0


def test_format_toml_list_empty():
    """Test _format_toml_list with empty list."""
    result = _format_toml_list("key", [], "")
    assert result == ["key = []"]


def test_format_toml_list_strings():
    """Test _format_toml_list with string list."""
    result = _format_toml_list("select", ["E", "F"], "")
    assert "select = [" in result[0]
    assert any('"E"' in line for line in result)
    assert any('"F"' in line for line in result)


def test_format_toml_value_string():
    """Test _format_toml_value with string."""
    result = _format_toml_value("name", "test", "")
    assert result == ['name = "test"']


def test_format_toml_value_bool():
    """Test _format_toml_value with boolean."""
    result = _format_toml_value("enabled", True, "")
    assert result == ["enabled = true"]
    result = _format_toml_value("enabled", False, "")
    assert result == ["enabled = false"]


def test_format_toml_value_number():
    """Test _format_toml_value with number."""
    result = _format_toml_value("count", 42, "")
    assert result == ["count = 42"]


def test_format_toml_value_list():
    """Test _format_toml_value with list."""
    result = _format_toml_value("items", ["a", "b"], "")
    assert len(result) > 0


def test_format_toml_value_dict():
    """Test _format_toml_value with dict returns empty."""
    result = _format_toml_value("section", {}, "")
    assert not result


def test_format_toml_tool_section():
    """Test format_toml_tool_section formats config."""
    data = {"line-length": 100, "enabled": True}
    result = format_toml_tool_section(data)
    assert "line-length = 100" in result
    assert "enabled = true" in result


def test_format_toml_tool_section_quotes_special_keys():
    """Keys requiring quoting should be quoted."""
    data = {"tests/**": ["TRY002"]}
    result = format_toml_tool_section(data)
    assert '"tests/**"' in result


def test_print_toml_value_string(capsys):
    """Test _print_toml_value with string."""
    _print_toml_value("name", "test")
    captured = capsys.readouterr()
    assert 'name = "test"' in captured.out


def test_print_toml_value_bool(capsys):
    """Test _print_toml_value with boolean."""
    _print_toml_value("enabled", True)
    captured = capsys.readouterr()
    assert "enabled = true" in captured.out


def test_print_toml_value_list(capsys):
    """Test _print_toml_value with list."""
    _print_toml_value("items", ["a", "b"])
    captured = capsys.readouterr()
    assert "items = [" in captured.out


def test_print_toml_list_strings(capsys):
    """Test _print_toml_list with string list."""
    _print_toml_list("select", ["E", "F"])
    captured = capsys.readouterr()
    assert "select = [" in captured.out
    assert '"E"' in captured.out


def test_print_tool_section(capsys):
    """Test _print_tool_section prints tool config."""
    config = {"line-length": 100, "lint": {"select": ["E"]}}
    _print_tool_section("ruff", config)
    captured = capsys.readouterr()
    assert "[tool.ruff]" in captured.out
    assert "line-length = 100" in captured.out


def test_print_tool_config_diff(capsys):
    """Test print_tool_config_diff prints expected config."""
    shared = {"tool": {"ruff": {"line-length": 100}}}
    repo = {}
    print_tool_config_diff(shared, repo)
    captured = capsys.readouterr()
    assert "[tool.ruff]" in captured.out
    assert "line-length = 100" in captured.out


def test_print_tool_config_diff_no_tool(capsys):
    """Test print_tool_config_diff with no tool section."""
    shared = {}
    repo = {}
    print_tool_config_diff(shared, repo)
    _captured = capsys.readouterr()
    # Should not crash


def test_sync_configs(tmp_path):
    """Test sync_configs overwrites tool sections."""
    shared_config = tmp_path / "shared.toml"
    shared_config.write_text("[tool.ruff]\nline-length = 100")
    repo_pyproject = tmp_path / "pyproject.toml"
    repo_pyproject.write_text(
        '[project]\nname = "test"\n\n'
        '[tool.old]\nvalue = "remove me"\n\n'
        '[tool.deptry]\nexclude = ["tests"]\n'
    )

    result = sync_configs(shared_config, repo_pyproject)
    assert result is True
    updated = repo_pyproject.read_text()
    assert "[project]" in updated
    assert "[tool.ruff]" in updated
    assert "line-length = 100" in updated
    assert "[tool.old]" in updated
    assert "[tool.deptry]" in updated
    assert 'exclude = ["tests"]' in updated


def test_sync_configs_missing_shared_config_raises(tmp_path):
    """Test sync_configs raises when shared config is missing."""
    shared_config = tmp_path / "missing.toml"
    repo_pyproject = tmp_path / "pyproject.toml"
    repo_pyproject.write_text('[project]\nname = "test"')

    with pytest.raises(FileNotFoundError):
        sync_configs(shared_config, repo_pyproject)


def test_find_shared_config_explicit():
    """Test _find_shared_config with explicit path."""
    explicit_path = Path("/explicit/shared-tool-config.toml")
    result = _find_shared_config(Path("/repo"), explicit_path)
    assert result == explicit_path


def test_find_shared_config_relative_to_script():
    """Test _find_shared_config finds config relative to script."""
    with patch("pathlib.Path.exists") as mock_exists:
        mock_exists.return_value = True
        result = _find_shared_config(Path("/repo"), None)
        assert "shared-tool-config.toml" in str(result)


def test_find_shared_config_sibling():
    """Test _find_shared_config tries sibling directory."""
    with patch("pathlib.Path.exists") as mock_exists:
        mock_exists.return_value = False
        result = _find_shared_config(Path("/repo"), None)
        assert "ci_shared" in str(result)


def test_validate_paths_missing_shared_config(tmp_path):
    """Test _validate_paths with missing shared config."""
    shared_config = tmp_path / "missing.toml"
    repo_pyproject = tmp_path / "pyproject.toml"
    repo_pyproject.touch()

    result = _validate_paths(shared_config, repo_pyproject)
    assert result == 2


def test_validate_paths_missing_pyproject(tmp_path):
    """Test _validate_paths with missing pyproject.toml."""
    shared_config = tmp_path / "shared.toml"
    shared_config.touch()
    repo_pyproject = tmp_path / "missing.toml"

    result = _validate_paths(shared_config, repo_pyproject)
    assert result == 2


def test_validate_paths_success(tmp_path):
    """Test _validate_paths with valid paths."""
    shared_config = tmp_path / "shared.toml"
    shared_config.touch()
    repo_pyproject = tmp_path / "pyproject.toml"
    repo_pyproject.touch()

    result = _validate_paths(shared_config, repo_pyproject)
    assert result is None


def test_handle_config_mismatch_sync_mode(tmp_path, capsys):
    """Test _handle_config_mismatch in sync mode."""
    shared_config = tmp_path / "shared.toml"
    shared_config.write_text("[tool.ruff]\nline-length = 100")
    repo_pyproject = tmp_path / "pyproject.toml"
    repo_pyproject.write_text('[project]\nname = "test"')

    differences = ["Missing tool configuration: [tool.ruff]"]
    result = _handle_config_mismatch(repo_pyproject, differences, True, shared_config)
    assert result == 0
    updated = repo_pyproject.read_text()
    assert "[tool.ruff]" in updated
    assert "line-length = 100" in updated
    output = capsys.readouterr().out
    assert "Updated [tool.*]" in output


def test_handle_config_mismatch_validation_mode(tmp_path):
    """Test _handle_config_mismatch in validation mode."""
    shared_config = tmp_path / "shared.toml"
    repo_pyproject = tmp_path / "pyproject.toml"
    repo_pyproject.write_text('[project]\nname = "test"')

    differences = ["Missing tool configuration: [tool.ruff]"]
    result = _handle_config_mismatch(repo_pyproject, differences, False, shared_config)
    assert result == 1


def test_main_success(tmp_path):
    """Test main with matching configs."""
    shared_config = tmp_path / "shared-tool-config.toml"
    shared_config.write_text("[tool.ruff]\nline-length = 100")
    repo_pyproject = tmp_path / "pyproject.toml"
    repo_pyproject.write_text("[tool.ruff]\nline-length = 100")

    with patch("ci_tools.scripts.tool_config_guard._find_shared_config") as mock:
        mock.return_value = shared_config
        with patch("sys.argv", ["tool_config_guard.py", "--repo-root", str(tmp_path)]):
            result = main()
            assert result == 0


def test_main_config_mismatch(tmp_path):
    """Test main with mismatched configs."""
    shared_config = tmp_path / "shared-tool-config.toml"
    shared_config.write_text("[tool.ruff]\nline-length = 100")
    repo_pyproject = tmp_path / "pyproject.toml"
    repo_pyproject.write_text('[project]\nname = "test"')

    with patch("ci_tools.scripts.tool_config_guard._find_shared_config") as mock:
        mock.return_value = shared_config
        with patch("sys.argv", ["tool_config_guard.py", "--repo-root", str(tmp_path)]):
            result = main()
            assert result == 1


def test_main_missing_shared_config(tmp_path):
    """Test main with missing shared config."""
    shared_config = tmp_path / "missing.toml"
    repo_pyproject = tmp_path / "pyproject.toml"
    repo_pyproject.touch()

    with patch("ci_tools.scripts.tool_config_guard._find_shared_config") as mock:
        mock.return_value = shared_config
        with patch("sys.argv", ["tool_config_guard.py", "--repo-root", str(tmp_path)]):
            result = main()
            assert result == 2


def test_main_toml_load_error(tmp_path):
    """Test main handles TOML load errors."""
    shared_config = tmp_path / "shared-tool-config.toml"
    shared_config.write_text("invalid toml [[[")
    repo_pyproject = tmp_path / "pyproject.toml"
    repo_pyproject.touch()

    with patch("ci_tools.scripts.tool_config_guard._find_shared_config") as mock:
        mock.return_value = shared_config
        with patch("sys.argv", ["tool_config_guard.py", "--repo-root", str(tmp_path)]):
            with pytest.raises(tomllib.TOMLDecodeError):
                main()


def test_main_sync_mode(tmp_path):
    """Test main in sync mode."""
    shared_config = tmp_path / "shared-tool-config.toml"
    shared_config.write_text("[tool.ruff]\nline-length = 100")
    repo_pyproject = tmp_path / "pyproject.toml"
    repo_pyproject.write_text('[project]\nname = "test"\n')

    with patch("ci_tools.scripts.tool_config_guard._find_shared_config") as mock:
        mock.return_value = shared_config
        with patch(
            "sys.argv",
            ["tool_config_guard.py", "--repo-root", str(tmp_path), "--sync"],
        ):
            result = main()
            assert result == 0
    updated = repo_pyproject.read_text()
    assert "[tool.ruff]" in updated
