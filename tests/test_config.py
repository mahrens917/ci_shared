"""Unit tests for ci_tools.ci_runtime.config module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ci_tools.ci_runtime.config import (
    CONFIG_CANDIDATES,
    COVERAGE_THRESHOLD,
    DEFAULT_PROTECTED_PATH_PREFIXES,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_REPO_CONTEXT,
    PROTECTED_PATH_PREFIXES,
    REASONING_EFFORT_CHOICES,
    REPO_CONFIG,
    REPO_CONTEXT,
    REPO_ROOT,
    _coerce_coverage_threshold,
    _coerce_protected_prefixes,
    _coerce_repo_context,
    REQUIRED_MODEL,
    RISKY_PATTERNS,
    detect_repo_root,
    load_repo_config,
)
from ci_tools.scripts.config_loader import ConfigLoadError
from ci_tools.test_constants import get_constant

COVERAGE_VALUES = get_constant("config", "coverage_thresholds")


class TestConstants:
    """Tests for module-level constants."""

    def test_config_candidates(self):
        """Test CONFIG_CANDIDATES contains expected filenames."""
        assert "ci_shared.config.json" in CONFIG_CANDIDATES
        assert ".ci_shared.config.json" in CONFIG_CANDIDATES

    def test_default_protected_path_prefixes(self):
        """Test DEFAULT_PROTECTED_PATH_PREFIXES contains expected paths."""
        assert "ci.py" in DEFAULT_PROTECTED_PATH_PREFIXES
        assert "ci_tools/" in DEFAULT_PROTECTED_PATH_PREFIXES
        assert "scripts/ci.sh" in DEFAULT_PROTECTED_PATH_PREFIXES
        assert "Makefile" in DEFAULT_PROTECTED_PATH_PREFIXES

    def test_risky_patterns_is_tuple_of_regex(self):
        """Test RISKY_PATTERNS contains compiled regex patterns."""
        assert isinstance(RISKY_PATTERNS, tuple)
        assert len(RISKY_PATTERNS) > 0
        # Test that patterns can match expected dangerous strings
        dangerous_sql = "DROP TABLE users"
        dangerous_rm = "rm -rf /"
        dangerous_subprocess = "subprocess.run(['rm', '-rf'])"

        matched = False
        for pattern in RISKY_PATTERNS:
            if (
                pattern.search(dangerous_sql)
                or pattern.search(dangerous_rm)
                or pattern.search(dangerous_subprocess)
            ):
                matched = True
                break
        assert matched, "RISKY_PATTERNS should match dangerous patterns"

    def test_required_model(self):
        """Test REQUIRED_MODEL is set."""
        assert REQUIRED_MODEL == "gpt-5-codex"

    def test_reasoning_effort_choices(self):
        """Test REASONING_EFFORT_CHOICES contains expected values."""
        assert "low" in REASONING_EFFORT_CHOICES
        assert "medium" in REASONING_EFFORT_CHOICES
        assert "high" in REASONING_EFFORT_CHOICES

    def test_default_reasoning_effort(self):
        """Test DEFAULT_REASONING_EFFORT is a valid choice."""
        assert DEFAULT_REASONING_EFFORT in REASONING_EFFORT_CHOICES


class TestDetectRepoRoot:
    """Tests for detect_repo_root function."""

    def test_detect_repo_root_finds_git_dir(self, tmp_path):
        """Test detect_repo_root finds .git directory."""
        repo = tmp_path / "project"
        repo.mkdir()
        (repo / ".git").mkdir()

        with patch("pathlib.Path.cwd", return_value=repo):
            result = detect_repo_root()
            assert result == repo

    def test_detect_repo_root_walks_up_to_find_git(self, tmp_path):
        """Test detect_repo_root walks up directory tree."""
        repo = tmp_path / "project"
        repo.mkdir()
        (repo / ".git").mkdir()
        nested = repo / "src" / "module"
        nested.mkdir(parents=True)

        with patch("pathlib.Path.cwd", return_value=nested):
            result = detect_repo_root()
            assert result == repo

    def test_detect_repo_root_returns_cwd_when_no_git(self, tmp_path):
        """Test detect_repo_root returns cwd when no .git found."""
        no_git_dir = tmp_path / "no_git"
        no_git_dir.mkdir()

        with patch("pathlib.Path.cwd", return_value=no_git_dir):
            result = detect_repo_root()
            assert result == no_git_dir.resolve()


class TestLoadRepoConfig:
    """Tests for load_repo_config function."""

    def test_load_repo_config_first_candidate(self, tmp_path):
        """Test loading config from first candidate file."""
        config_file = tmp_path / "ci_shared.config.json"
        config_data = {"repo_context": "test context", "coverage_threshold": 85.0}
        config_file.write_text(json.dumps(config_data))

        result = load_repo_config(tmp_path)
        assert result == config_data

    def test_load_repo_config_second_candidate(self, tmp_path):
        """Test loading config from second candidate file."""
        config_file = tmp_path / ".ci_shared.config.json"
        config_data = {"protected_path_prefixes": ["custom/"]}
        config_file.write_text(json.dumps(config_data))

        result = load_repo_config(tmp_path)
        assert result == config_data

    def test_load_repo_config_prefers_first_candidate(self, tmp_path):
        """Test that first candidate is preferred over second."""
        config1 = tmp_path / "ci_shared.config.json"
        config1.write_text(json.dumps({"key": "from_first"}))

        config2 = tmp_path / ".ci_shared.config.json"
        config2.write_text(json.dumps({"key": "from_second"}))

        result = load_repo_config(tmp_path)
        assert result["key"] == "from_first"

    def test_load_repo_config_no_file_returns_empty(self, tmp_path):
        """Test loading returns empty dict when no config file exists."""
        result = load_repo_config(tmp_path)
        assert not result

    def test_load_repo_config_invalid_json(self, tmp_path):
        """Test loading raises ConfigLoadError for invalid JSON."""
        config_file = tmp_path / "ci_shared.config.json"
        config_file.write_text("{ invalid json }")

        with pytest.raises(ConfigLoadError) as exc_info:
            load_repo_config(tmp_path)
        assert "Failed to parse" in str(exc_info.value)

    def test_load_repo_config_non_dict_raises_error(self, tmp_path):
        """Test loading raises ConfigLoadError when JSON is not a dict."""
        config_file = tmp_path / "ci_shared.config.json"
        config_file.write_text(json.dumps(["not", "a", "dict"]))

        with pytest.raises(ConfigLoadError) as exc_info:
            load_repo_config(tmp_path)
        assert "Expected dict" in str(exc_info.value)

    def test_load_repo_config_empty_json(self, tmp_path):
        """Test loading empty JSON object."""
        config_file = tmp_path / "ci_shared.config.json"
        config_file.write_text(json.dumps({}))

        result = load_repo_config(tmp_path)
        assert not result

    def test_load_repo_config_complex_structure(self, tmp_path):
        """Test loading config with nested structure."""
        config_file = tmp_path / "ci_shared.config.json"
        config_data = {
            "repo_context": "Complex context",
            "coverage_threshold": 90.0,
            "protected_path_prefixes": ["ci/", "scripts/"],
            "nested": {"key": "value"},
        }
        config_file.write_text(json.dumps(config_data))

        result = load_repo_config(tmp_path)
        assert result == config_data


class TestCoercionFunctions:
    """Tests for internal coercion functions."""

    def test_coerce_repo_context_with_string(self):
        """Test _coerce_repo_context with string value."""

        config = {"repo_context": "Custom context"}
        result = _coerce_repo_context(config, "default")
        assert result == "Custom context"

    def test_coerce_repo_context_missing_uses_default(self):
        """Test _coerce_repo_context uses default when key missing."""

        config = {}
        result = _coerce_repo_context(config, "default context")
        assert result == "default context"

    def test_coerce_repo_context_wrong_type_uses_default(self):
        """Test _coerce_repo_context uses default for non-string value."""

        config = {"repo_context": 123}
        result = _coerce_repo_context(config, "default")
        assert result == "default"

    def test_coerce_protected_prefixes_with_list(self):
        """Test _coerce_protected_prefixes with list value."""

        config = {"protected_path_prefixes": ["path1/", "path2/"]}
        result = _coerce_protected_prefixes(config, ["default/"])
        assert result == ("path1/", "path2/")

    def test_coerce_protected_prefixes_with_tuple(self):
        """Test _coerce_protected_prefixes with tuple value."""

        config = {"protected_path_prefixes": ("path1/", "path2/")}
        result = _coerce_protected_prefixes(config, ["default/"])
        assert result == ("path1/", "path2/")

    def test_coerce_protected_prefixes_with_set(self):
        """Test _coerce_protected_prefixes with set value."""

        config = {"protected_path_prefixes": {"path1/", "path2/"}}
        result = _coerce_protected_prefixes(config, ["default/"])
        assert isinstance(result, tuple)
        assert set(result) == {"path1/", "path2/"}

    def test_coerce_protected_prefixes_missing_uses_default(self):
        """Test _coerce_protected_prefixes uses default when key missing."""

        config = {}
        result = _coerce_protected_prefixes(config, ["default/"])
        assert result == ("default/",)

    def test_coerce_protected_prefixes_wrong_type_uses_default(self):
        """Test _coerce_protected_prefixes uses default for wrong type."""

        config = {"protected_path_prefixes": "not a list"}
        result = _coerce_protected_prefixes(config, ["default/"])
        assert result == ("default/",)

    def test_coerce_coverage_threshold_with_float(self):
        """Test _coerce_coverage_threshold with float value."""

        config = {"coverage_threshold": COVERAGE_VALUES["float_example"]}
        result = _coerce_coverage_threshold(config, COVERAGE_VALUES["default"])
        assert result == COVERAGE_VALUES["float_example"]

    def test_coerce_coverage_threshold_with_int(self):
        """Test _coerce_coverage_threshold with int value."""

        config = {"coverage_threshold": COVERAGE_VALUES["int_example"]}
        result = _coerce_coverage_threshold(config, COVERAGE_VALUES["default"])
        assert result == COVERAGE_VALUES["int_example"]

    def test_coerce_coverage_threshold_with_string(self):
        """Test _coerce_coverage_threshold with string value."""

        config = {"coverage_threshold": str(COVERAGE_VALUES["string_example"])}
        result = _coerce_coverage_threshold(config, COVERAGE_VALUES["default"])
        assert result == COVERAGE_VALUES["string_example"]

    def test_coerce_coverage_threshold_missing_uses_default(self):
        """Test _coerce_coverage_threshold uses default when key missing."""

        config = {}
        result = _coerce_coverage_threshold(config, COVERAGE_VALUES["default"])
        assert result == COVERAGE_VALUES["default"]

    def test_coerce_coverage_threshold_invalid_string_uses_default(self):
        """Test _coerce_coverage_threshold uses default for invalid string."""

        config = {"coverage_threshold": "not-a-number"}
        result = _coerce_coverage_threshold(config, COVERAGE_VALUES["default"])
        assert result == COVERAGE_VALUES["default"]


class TestModuleLevelInitialization:
    """Tests for module-level variables initialized at import."""

    def test_repo_root_is_path(self):
        """Test REPO_ROOT is a Path object."""
        assert isinstance(REPO_ROOT, Path)

    def test_repo_config_is_dict(self):
        """Test REPO_CONFIG is a dict."""
        assert isinstance(REPO_CONFIG, dict)

    def test_repo_context_is_string(self):
        """Test REPO_CONTEXT is a string."""
        assert isinstance(REPO_CONTEXT, str)
        # Should be either the default or loaded from config
        assert len(REPO_CONTEXT) > 0

    def test_protected_path_prefixes_is_tuple(self):
        """Test PROTECTED_PATH_PREFIXES is a tuple."""
        assert isinstance(PROTECTED_PATH_PREFIXES, tuple)
        # Should contain at least the default paths
        assert len(PROTECTED_PATH_PREFIXES) > 0

    def test_coverage_threshold_is_float(self):
        """Test COVERAGE_THRESHOLD is a float."""
        assert isinstance(COVERAGE_THRESHOLD, float)
        assert COVERAGE_THRESHOLD > 0.0
        assert COVERAGE_THRESHOLD <= COVERAGE_VALUES["max"]

    def test_default_repo_context_contains_key_info(self):
        """Test DEFAULT_REPO_CONTEXT contains expected guidance."""
        assert "Python 3.10+" in DEFAULT_REPO_CONTEXT
        assert "src/" in DEFAULT_REPO_CONTEXT
        assert "tests/" in DEFAULT_REPO_CONTEXT
        assert "unified diff" in DEFAULT_REPO_CONTEXT


class TestRiskyPatterns:
    """Tests for RISKY_PATTERNS regex patterns."""

    def test_risky_pattern_drop_table(self):
        """Test RISKY_PATTERNS detects DROP TABLE statements."""
        test_cases = [
            "DROP TABLE users",
            "drop table accounts",
            "DROP  TABLE  products",
        ]
        for test_case in test_cases:
            matched = any(pattern.search(test_case) for pattern in RISKY_PATTERNS)
            assert matched, f"Should match: {test_case}"

    def test_risky_pattern_rm_rf(self):
        """Test RISKY_PATTERNS detects rm -rf commands."""
        test_cases = [
            "rm -rf /",
            "rm -rf ./directory",
            "rm -rf *",
        ]
        for test_case in test_cases:
            matched = any(pattern.search(test_case) for pattern in RISKY_PATTERNS)
            assert matched, f"Should match: {test_case}"

    def test_risky_pattern_subprocess_rm(self):
        """Test RISKY_PATTERNS detects subprocess.run with rm."""
        test_cases = [
            "subprocess.run(['rm', '-rf'])",
            'subprocess.run(["rm", "file"])',
            "subprocess.run('rm -rf', shell=True)",
        ]
        for test_case in test_cases:
            matched = any(pattern.search(test_case) for pattern in RISKY_PATTERNS)
            assert matched, f"Should match: {test_case}"

    def test_risky_pattern_safe_commands(self):
        """Test RISKY_PATTERNS doesn't match safe commands."""
        safe_cases = [
            "git rm file.txt",
            "remove_file('test.txt')",
            "SELECT * FROM users",
            "subprocess.run(['ls', '-l'])",
        ]
        for safe_case in safe_cases:
            matched = any(pattern.search(safe_case) for pattern in RISKY_PATTERNS)
            # These should not match the risky patterns
            assert not matched, f"Should not match: {safe_case}"
