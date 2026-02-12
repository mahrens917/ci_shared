"""Unit tests for complexity_guard module."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from tests.conftest import write_module
from ci_tools.scripts import complexity_guard
from ci_tools.test_constants import get_constant


def test_calculate_cognitive_complexity_counts_nested_branches() -> None:
    """Test that cognitive complexity is calculated correctly for nested branches."""
    source = textwrap.dedent(
        """
        def sample(x: int) -> int:
            if x > 10:
                for value in range(x):
                    if value % 2 == 0:
                        return value
            return 0
        """
    )
    tree = complexity_guard.ast.parse(source)
    func = next(node for node in tree.body if isinstance(node, complexity_guard.ast.FunctionDef))
    score = complexity_guard.calculate_cognitive_complexity(func)
    assert score >= get_constant("complexity_guard", "min_score")


def test_check_file_complexity_detects_violation(tmp_path: Path) -> None:
    """Test that complexity violations are detected in files."""
    target = tmp_path / "violations.py"
    write_module(
        target,
        """
        def risky(value: int) -> int:
            if value > 0:
                if value > 1:
                    if value > 2:
                        return value
            return 0
        """,
    )
    results = complexity_guard.check_file_complexity(target, max_cyclomatic=1, max_cognitive=1)
    assert results, "Expected complexity violations to be reported"
    assert results[0].function_name == "risky"


def test_check_file_complexity_ignores_simple_function(tmp_path: Path) -> None:
    """Test that simple functions pass complexity checks."""
    target = tmp_path / "clean.py"
    write_module(
        target,
        """
        def clean() -> int:
            return 1
        """,
    )
    results = complexity_guard.check_file_complexity(target, max_cyclomatic=2, max_cognitive=2)
    assert not results


def run_main(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> int:
    """Helper to run the main function with specified arguments."""
    monkeypatch.setattr(sys, "argv", ["complexity_guard.py", *args])
    with pytest.raises(SystemExit) as exc:
        complexity_guard.main()
    return int(exc.value.code) if exc.value.code is not None else 0


def test_main_rejects_missing_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that main rejects a missing root directory."""
    missing = tmp_path / "missing"
    code = run_main(monkeypatch, ["--root", str(missing)])
    assert code == 1
    captured = capsys.readouterr()
    assert "does not exist" in captured.err


def test_main_reports_violation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that main reports complexity violations."""
    root = tmp_path / "pkg"
    root.mkdir()
    write_module(
        root / "bad.py",
        """
        def bad(value: int) -> int:
            if value > 0:
                if value > 1:
                    return value
            return 0
        """,
    )
    code = run_main(
        monkeypatch,
        ["--root", str(root), "--max-cyclomatic", "1", "--max-cognitive", "1"],
    )
    assert code == 1
    captured = capsys.readouterr()
    assert "bad.py" in captured.out


def test_main_succeeds_without_violations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that main succeeds when there are no violations."""
    root = tmp_path / "pkg"
    root.mkdir()
    write_module(
        root / "ok.py",
        """
        def ok() -> int:
            return 1
        """,
    )
    code = run_main(monkeypatch, ["--root", str(root)])
    assert code == 0
    captured = capsys.readouterr()
    assert "All functions meet complexity limits" in captured.out


def test_main_scans_multiple_roots(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that main scans files from multiple roots."""
    src = tmp_path / "src"
    scripts = tmp_path / "scripts"
    src.mkdir()
    scripts.mkdir()
    write_module(
        src / "ok.py",
        """
        def ok() -> int:
            return 1
        """,
    )
    write_module(
        scripts / "also_ok.py",
        """
        def also_ok() -> int:
            return 2
        """,
    )
    code = run_main(monkeypatch, ["--root", str(src), "--root", str(scripts)])
    assert code == 0
    captured = capsys.readouterr()
    assert "All functions meet complexity limits" in captured.out


def test_build_parser_root_default_is_none() -> None:
    """Test that build_parser --root defaults to None."""
    parser = complexity_guard.build_parser()
    args = parser.parse_args([])
    assert args.root is None


def test_build_parser_multiple_roots() -> None:
    """Test that build_parser accepts multiple --root flags."""
    parser = complexity_guard.build_parser()
    args = parser.parse_args(["--root", "src", "--root", "scripts"])
    assert args.root == [Path("src"), Path("scripts")]
