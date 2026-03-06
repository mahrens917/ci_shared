"""Unit tests for delegation_guard module."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

from tests.conftest import write_module

from ci_tools.scripts.delegation_guard import DelegationGuard


def test_parse_args_defaults():
    guard = DelegationGuard()
    args = guard.parse_args([])
    assert args.root is None
    assert args.exclude == []


# ── Check 1: module-scope setattr ──────────────────────────────────────


def test_detects_module_scope_setattr(tmp_path: Path):
    py_file = tmp_path / "runtime.py"
    write_module(
        py_file,
        """
        class MyService:
            pass

        def _start(self):
            pass

        setattr(MyService, "start", _start)
        """,
    )
    guard = DelegationGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace()
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 1
    assert "module-scope setattr" in violations[0]


def test_ignores_setattr_inside_function(tmp_path: Path):
    py_file = tmp_path / "clean.py"
    write_module(
        py_file,
        """
        class MyClass:
            pass

        def configure(cls, name, func):
            setattr(cls, name, func)
        """,
    )
    guard = DelegationGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace()
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 0


def test_ignores_setattr_with_non_name_target(tmp_path: Path):
    py_file = tmp_path / "dynamic.py"
    write_module(
        py_file,
        """
        import sys
        setattr(sys.modules[__name__], "x", 1)
        """,
    )
    guard = DelegationGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace()
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 0


# ── Check 2: single-method wrapper classes ─────────────────────────────


def test_detects_single_method_wrapper(tmp_path: Path):
    py_file = tmp_path / "wrapper.py"
    write_module(
        py_file,
        """
        def do_work(data):
            return data

        class WorkWrapper:
            def execute(self, data):
                return do_work(data)
        """,
    )
    guard = DelegationGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace()
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 1
    assert "WorkWrapper" in violations[0]
    assert "single-method wrapper" in violations[0]


def test_ignores_dataclass(tmp_path: Path):
    py_file = tmp_path / "model.py"
    write_module(
        py_file,
        """
        from dataclasses import dataclass

        @dataclass
        class Config:
            def validate(self):
                return check_config(self)
        """,
    )
    guard = DelegationGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace()
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 0


def test_ignores_class_with_multiple_methods(tmp_path: Path):
    py_file = tmp_path / "service.py"
    write_module(
        py_file,
        """
        class Service:
            def start(self):
                return run_start()

            def stop(self):
                return run_stop()
        """,
    )
    guard = DelegationGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace()
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 0


def test_ignores_class_with_non_delegating_method(tmp_path: Path):
    py_file = tmp_path / "processor.py"
    write_module(
        py_file,
        """
        class Processor:
            def process(self, data):
                result = data * 2
                return result
        """,
    )
    guard = DelegationGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace()
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 0


def test_ignores_class_with_only_dunder_methods(tmp_path: Path):
    py_file = tmp_path / "container.py"
    write_module(
        py_file,
        """
        class Container:
            def __init__(self, items):
                self.items = items

            def __len__(self):
                return len(self.items)
        """,
    )
    guard = DelegationGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace()
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 0


# ── Check 3: pass-through functions ────────────────────────────────────


def test_detects_passthrough_function(tmp_path: Path):
    py_file = tmp_path / "forwarding.py"
    write_module(
        py_file,
        """
        def _impl(x, y):
            return x + y

        def add(x, y):
            return _impl(x, y)
        """,
    )
    guard = DelegationGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace()
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 1
    assert "add" in violations[0]
    assert "pass-through" in violations[0]


def test_ignores_function_with_different_args(tmp_path: Path):
    py_file = tmp_path / "transform.py"
    write_module(
        py_file,
        """
        def _impl(x, y, z):
            return x + y + z

        def add(x, y):
            return _impl(x, y, 0)
        """,
    )
    guard = DelegationGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace()
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 0


def test_ignores_function_with_multi_statement_body(tmp_path: Path):
    py_file = tmp_path / "validated.py"
    write_module(
        py_file,
        """
        def add(x, y):
            assert isinstance(x, int)
            return _impl(x, y)
        """,
    )
    guard = DelegationGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace()
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 0


def test_ignores_recursive_function(tmp_path: Path):
    py_file = tmp_path / "recursive.py"
    write_module(
        py_file,
        """
        def factorial(n):
            return factorial(n)
        """,
    )
    guard = DelegationGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace()
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 0


def test_detects_no_arg_passthrough(tmp_path: Path):
    py_file = tmp_path / "noargs.py"
    write_module(
        py_file,
        """
        def _get_data():
            return [1, 2, 3]

        def get_data():
            return _get_data()
        """,
    )
    guard = DelegationGuard()
    guard.repo_root = tmp_path
    args = argparse.Namespace()
    violations = guard.scan_file(py_file, args)
    assert len(violations) == 1
    assert "get_data" in violations[0]


# ── Check 4: empty helper packages ────────────────────────────────────


def test_detects_empty_helper_package(tmp_path: Path):
    helpers_dir = tmp_path / "src" / "app_helpers"
    helpers_dir.mkdir(parents=True)
    write_module(helpers_dir / "__init__.py", "")

    guard = DelegationGuard()
    guard.repo_root = tmp_path
    result = guard.run(["--root", str(tmp_path / "src")])
    assert result == 1


def test_ignores_helper_package_with_modules(tmp_path: Path):
    helpers_dir = tmp_path / "src" / "app_helpers"
    helpers_dir.mkdir(parents=True)
    write_module(helpers_dir / "__init__.py", "")
    write_module(
        helpers_dir / "cache.py",
        """
        def get_cache():
            return {}
        """,
    )

    guard = DelegationGuard()
    guard.repo_root = tmp_path
    result = guard.run(["--root", str(tmp_path / "src")])
    assert result == 0


def test_ignores_non_helper_empty_package(tmp_path: Path):
    pkg_dir = tmp_path / "src" / "utils"
    pkg_dir.mkdir(parents=True)
    write_module(pkg_dir / "__init__.py", "")

    guard = DelegationGuard()
    guard.repo_root = tmp_path
    result = guard.run(["--root", str(tmp_path / "src")])
    assert result == 0


# ── CLI integration ───────────────────────────────────────────────────


@patch("sys.argv", ["delegation_guard.py"])
def test_main_no_violations(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    write_module(
        src / "clean.py",
        """
        class Service:
            def start(self):
                self.running = True

            def stop(self):
                self.running = False
        """,
    )

    result = DelegationGuard.main()
    assert result == 0


@patch("sys.argv", ["delegation_guard.py"])
def test_main_with_violations(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    write_module(
        src / "bad.py",
        """
        class MyService:
            pass

        def _start(self):
            pass

        setattr(MyService, "start", _start)
        """,
    )

    result = DelegationGuard.main()
    assert result == 1
