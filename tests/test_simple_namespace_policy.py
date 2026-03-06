"""Unit tests for SimpleNamespace policy check."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import write_module

from ci_tools.scripts.policy_collectors_ast import collect_simple_namespace_usage


@pytest.fixture
def policy_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up a temporary policy context root for testing."""
    monkeypatch.setattr("ci_tools.scripts.policy_context.ROOT", tmp_path)
    return tmp_path


def test_detects_simple_namespace(policy_root: Path):
    """SimpleNamespace() call in src/ should be flagged."""
    src = policy_root / "src" / "app"
    src.mkdir(parents=True)
    write_module(
        src / "stubs.py",
        """
        from types import SimpleNamespace

        stub = SimpleNamespace(name="test", value=0)
        """,
    )
    results = collect_simple_namespace_usage()
    assert len(results) == 1
    assert results[0][1] == 3  # line number


def test_detects_qualified_simple_namespace(policy_root: Path):
    """types.SimpleNamespace() should also be flagged."""
    src = policy_root / "src" / "app"
    src.mkdir(parents=True)
    write_module(
        src / "qualified.py",
        """
        import types

        stub = types.SimpleNamespace(x=1)
        """,
    )
    results = collect_simple_namespace_usage()
    assert len(results) == 1


def test_ignores_code_without_simple_namespace(policy_root: Path):
    """Regular code without SimpleNamespace should not be flagged."""
    src = policy_root / "src" / "app"
    src.mkdir(parents=True)
    write_module(
        src / "clean.py",
        """
        class Config:
            def __init__(self, name):
                self.name = name
        """,
    )
    results = collect_simple_namespace_usage()
    assert len(results) == 0


def test_ignores_init_files(policy_root: Path):
    """__init__.py files should be skipped by the collector."""
    src = policy_root / "src" / "app"
    src.mkdir(parents=True)
    write_module(
        src / "__init__.py",
        """
        from types import SimpleNamespace
        stub = SimpleNamespace()
        """,
    )
    results = collect_simple_namespace_usage()
    assert len(results) == 0


def test_detects_multiple_usages(policy_root: Path):
    """Multiple SimpleNamespace calls should all be flagged."""
    src = policy_root / "src" / "app"
    src.mkdir(parents=True)
    write_module(
        src / "multi.py",
        """
        from types import SimpleNamespace

        a = SimpleNamespace(x=1)
        b = SimpleNamespace(y=2)
        """,
    )
    results = collect_simple_namespace_usage()
    assert len(results) == 2
