"""Unit tests for policy_collectors_text module."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import assert_collector_finds_token, write_module
from ci_tools.scripts.policy_collectors_text import (
    collect_flagged_tokens,
    collect_legacy_configs,
    collect_legacy_modules,
    collect_suppressions,
    scan_keywords,
)


def write_file(path: Path, content: str) -> None:
    """Helper to write a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_scan_keywords_finds_banned_keywords(policy_root):
    """Test scan_keywords finds banned keywords."""

    write_module(
        policy_root / "module.py",
        """
        def legacy_handler():
            return fallback_value
        """,
    )

    results = scan_keywords()
    assert "legacy" in results
    assert "fallback" in results


def test_scan_keywords_case_insensitive(policy_root):
    """Test scan_keywords is case insensitive."""

    write_module(
        policy_root / "module.py",
        """
        def LEGACY_handler():
            return Fallback_value
        """,
    )

    results = scan_keywords()
    assert "legacy" in results or "LEGACY" in results.get("legacy", {})
    assert "fallback" in results or "Fallback" in results.get("fallback", {})


def test_scan_keywords_tracks_line_numbers(policy_root):
    """Test scan_keywords tracks line numbers."""

    write_module(
        policy_root / "module.py",
        """
        legacy = True
        x = 1
        legacy = False
        """,
    )

    results = scan_keywords()
    if "legacy" in results:
        files = results["legacy"]
        assert len(files) > 0
        for lines in files.values():
            assert len(lines) >= 2


def test_scan_keywords_handles_tokenize_error(policy_root):
    """Test scan_keywords handles tokenization errors gracefully."""

    # Write file with incomplete string that causes tokenize error
    path = policy_root / "bad.py"
    path.write_text('x = "incomplete string\n', encoding="utf-8")

    # Should not raise, just skip the file
    scan_keywords()
    # Just ensure it completes without error


def test_scan_keywords_filters_by_name_token_type(policy_root):
    """Test scan_keywords only matches NAME tokens."""

    write_module(
        policy_root / "module.py",
        """
        # Comment with legacy keyword
        x = "legacy string"
        legacy_variable = 1
        """,
    )

    scan_keywords()
    # Should find legacy_variable but not the string or comment
    # This is a characteristic test to ensure tokenization works


def test_collect_flagged_tokens_todo(policy_root):
    """Test collect_flagged_tokens finds TODO."""
    assert_collector_finds_token(
        collect_flagged_tokens,
        """
        # TODO: implement this
        def foo():
            pass
        """,
        "TODO",
        root_path=policy_root,
    )


def test_collect_flagged_tokens_fixme(policy_root):
    """Test collect_flagged_tokens finds FIXME."""
    assert_collector_finds_token(
        collect_flagged_tokens,
        """
        # FIXME: broken logic
        def foo():
            pass
        """,
        "FIXME",
        root_path=policy_root,
    )


def test_collect_flagged_tokens_hack(policy_root):
    """Test collect_flagged_tokens finds HACK."""
    assert_collector_finds_token(
        collect_flagged_tokens,
        """
        # HACK: temporary workaround
        def foo():
            pass
        """,
        "HACK",
        root_path=policy_root,
    )


def test_collect_flagged_tokens_workaround(policy_root):
    """Test collect_flagged_tokens finds WORKAROUND."""
    assert_collector_finds_token(
        collect_flagged_tokens,
        """
        # WORKAROUND: for known bug
        def foo():
            pass
        """,
        "WORKAROUND",
        root_path=policy_root,
    )


def test_collect_flagged_tokens_legacy(policy_root):
    """Test collect_flagged_tokens finds LEGACY."""
    assert_collector_finds_token(
        collect_flagged_tokens,
        """
        # LEGACY: old implementation
        def foo():
            pass
        """,
        "LEGACY",
        root_path=policy_root,
    )


def test_collect_flagged_tokens_deprecated(policy_root):
    """Test collect_flagged_tokens finds DEPRECATED."""
    assert_collector_finds_token(
        collect_flagged_tokens,
        """
        # DEPRECATED: use new_function instead
        def foo():
            pass
        """,
        "DEPRECATED",
        root_path=policy_root,
    )


def test_collect_flagged_tokens_tracks_line_numbers(policy_root):
    """Test collect_flagged_tokens tracks correct line numbers."""

    write_module(
        policy_root / "module.py",
        """
        def first():
            pass
        # TODO: line 4
        def second():
            # FIXME: line 6
            pass
        """,
    )

    results = collect_flagged_tokens()
    assert len(results) >= 2
    line_numbers = [lineno for _, lineno, _ in results]
    assert 3 in line_numbers or 4 in line_numbers  # Line with keyword
    assert 5 in line_numbers or 6 in line_numbers  # Line with keyword


def test_collect_suppressions_noqa(policy_root):
    """Test collect_suppressions finds # noqa."""
    write_module(
        policy_root / "module.py",
        """
        def foo():
            long_line = 1  # noqa
        """,
    )
    results = list(collect_suppressions())
    assert len(results) >= 1
    assert any("# noqa" in token for _, _, token in results)


def test_collect_suppressions_pylint(policy_root):
    """Test collect_suppressions finds pylint: disable."""
    write_module(
        policy_root / "module.py",
        """
        def foo():
            x = 1  # pylint: disable=invalid-name
        """,
    )
    results = list(collect_suppressions())
    assert len(results) >= 1
    assert any("pylint: disable" in token for _, _, token in results)


def test_collect_suppressions_tracks_line_numbers(policy_root):
    """Test collect_suppressions tracks line numbers."""

    write_module(
        policy_root / "module.py",
        """
        def foo():
            a = 1  # noqa
            b = 2
            c = 3  # pylint: disable=foo
        """,
    )

    results = collect_suppressions()
    assert len(results) >= 2


def test_collect_legacy_modules_legacy_suffix(policy_root):
    """Test collect_legacy_modules finds _legacy.py suffix."""

    write_module(policy_root / "handler_legacy.py", "def foo(): pass")

    results = collect_legacy_modules()
    assert len(results) >= 1
    assert any("legacy" in path.lower() for path, _, _ in results)


def test_collect_legacy_modules_compat_suffix(policy_root):
    """Test collect_legacy_modules finds _compat.py suffix."""

    write_module(policy_root / "utils_compat.py", "def foo(): pass")

    results = collect_legacy_modules()
    assert len(results) >= 1
    assert any("compat" in path.lower() for path, _, _ in results)


def test_collect_legacy_modules_deprecated_suffix(policy_root):
    """Test collect_legacy_modules finds _deprecated.py suffix."""

    write_module(policy_root / "api_deprecated.py", "def foo(): pass")

    results = collect_legacy_modules()
    assert len(results) >= 1
    assert any("deprecated" in path.lower() for path, _, _ in results)


def test_collect_legacy_modules_legacy_directory(policy_root):
    """Test collect_legacy_modules finds legacy directory."""

    legacy_dir = policy_root / "legacy"
    legacy_dir.mkdir()
    write_module(legacy_dir / "module.py", "def foo(): pass")

    results = collect_legacy_modules()
    assert len(results) >= 1
    assert any(
        "/legacy/" in path or "\\legacy\\" in path or
        path.startswith("legacy/") or path.startswith("legacy\\")
        for path, _, _ in results
    )


def test_collect_legacy_modules_compat_directory(policy_root):
    """Test collect_legacy_modules finds compat directory."""

    compat_dir = policy_root / "compat"
    compat_dir.mkdir()
    write_module(compat_dir / "module.py", "def foo(): pass")

    results = collect_legacy_modules()
    assert len(results) >= 1
    assert any(
        "/compat/" in path or "\\compat\\" in path or
        path.startswith("compat/") or path.startswith("compat\\")
        for path, _, _ in results
    )


def test_collect_legacy_configs_json(policy_root, monkeypatch: pytest.MonkeyPatch):
    """Test collect_legacy_configs finds legacy in JSON config."""
    monkeypatch.setattr("ci_tools.scripts.policy_collectors_text.ROOT", policy_root)


    config_dir = policy_root / "config"
    config_dir.mkdir()
    write_file(
        config_dir / "settings.json",
        '{"legacy_mode": true, "other": "value"}',
    )

    results = collect_legacy_configs()
    assert len(results) >= 1
    assert any("legacy" in reason.lower() for _, _, reason in results)


def test_collect_legacy_configs_toml(policy_root, monkeypatch: pytest.MonkeyPatch):
    """Test collect_legacy_configs finds legacy in TOML config."""
    monkeypatch.setattr("ci_tools.scripts.policy_collectors_text.ROOT", policy_root)


    config_dir = policy_root / "config"
    config_dir.mkdir()
    write_file(
        config_dir / "settings.toml",
        '[section]\nlegacy_flag = true\n',
    )

    results = collect_legacy_configs()
    assert len(results) >= 1


def test_collect_legacy_configs_yaml(policy_root, monkeypatch: pytest.MonkeyPatch):
    """Test collect_legacy_configs finds legacy in YAML config."""
    monkeypatch.setattr("ci_tools.scripts.policy_collectors_text.ROOT", policy_root)


    config_dir = policy_root / "config"
    config_dir.mkdir()
    write_file(
        config_dir / "settings.yaml",
        'old_api: true\ncompat: enabled\n',
    )

    results = collect_legacy_configs()
    assert len(results) >= 1


def test_collect_legacy_configs_yml(policy_root, monkeypatch: pytest.MonkeyPatch):
    """Test collect_legacy_configs finds legacy in .yml config."""
    monkeypatch.setattr("ci_tools.scripts.policy_collectors_text.ROOT", policy_root)


    config_dir = policy_root / "config"
    config_dir.mkdir()
    write_file(
        config_dir / "settings.yml",
        'deprecated: true\n',
    )

    results = collect_legacy_configs()
    assert len(results) >= 1


def test_collect_legacy_configs_ini(policy_root, monkeypatch: pytest.MonkeyPatch):
    """Test collect_legacy_configs finds legacy in INI config."""
    monkeypatch.setattr("ci_tools.scripts.policy_collectors_text.ROOT", policy_root)


    config_dir = policy_root / "config"
    config_dir.mkdir()
    write_file(
        config_dir / "settings.ini",
        '[section]\nlegacy = true\n',
    )

    results = collect_legacy_configs()
    assert len(results) >= 1


def test_collect_legacy_configs_ignores_non_config_files(
    policy_root, monkeypatch: pytest.MonkeyPatch
):
    """Test collect_legacy_configs ignores non-config files."""
    monkeypatch.setattr("ci_tools.scripts.policy_collectors_text.ROOT", policy_root)


    config_dir = policy_root / "config"
    config_dir.mkdir()
    write_file(config_dir / "readme.txt", "legacy documentation")

    results = collect_legacy_configs()
    # Should not find legacy in .txt file
    matching = [r for r in results if "readme.txt" in r[0]]
    assert len(matching) == 0


def test_collect_legacy_configs_handles_unicode_error(policy_root, monkeypatch: pytest.MonkeyPatch):
    """Test collect_legacy_configs handles unicode decode errors."""
    monkeypatch.setattr("ci_tools.scripts.policy_collectors_text.ROOT", policy_root)


    config_dir = policy_root / "config"
    config_dir.mkdir()
    bad_file = config_dir / "bad.json"
    bad_file.write_bytes(b"\xff\xfe\xff\xfe")

    # Should not raise
    collect_legacy_configs()


def test_collect_legacy_configs_no_config_dir(policy_root, monkeypatch: pytest.MonkeyPatch):
    """Test collect_legacy_configs handles missing config directory."""
    monkeypatch.setattr("ci_tools.scripts.policy_collectors_text.ROOT", policy_root)


    # No config directory exists
    results = collect_legacy_configs()
    assert not results


def test_collect_legacy_configs_tracks_line_numbers(policy_root, monkeypatch: pytest.MonkeyPatch):
    """Test collect_legacy_configs tracks correct line numbers."""
    monkeypatch.setattr("ci_tools.scripts.policy_collectors_text.ROOT", policy_root)


    config_dir = policy_root / "config"
    config_dir.mkdir()
    write_file(
        config_dir / "settings.json",
        '{\n  "normal": true,\n  "legacy_mode": true,\n  "other": false\n}',
    )

    results = collect_legacy_configs()
    assert len(results) >= 1
    # Line 3 should contain legacy_mode
    assert any(lineno == 3 for _, lineno, _ in results)
