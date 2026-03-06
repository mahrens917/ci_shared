"""Unit tests for fragmentation_guard module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests.conftest import write_module

from ci_tools.scripts.fragmentation_guard import FragmentationGuard


def test_parse_args_defaults():
    guard = FragmentationGuard()
    args = guard.parse_args([])
    assert args.root is None
    assert args.min_lines == 30
    assert args.max_tiny_ratio == 0.5
    assert args.min_modules == 3


def test_parse_args_custom_values():
    guard = FragmentationGuard()
    args = guard.parse_args(
        [
            "--root", "custom",
            "--min-lines", "20",
            "--max-tiny-ratio", "0.6",
            "--min-modules", "5",
        ]
    )
    assert args.root == [Path("custom")]
    assert args.min_lines == 20
    assert args.max_tiny_ratio == 0.6
    assert args.min_modules == 5


def _make_tiny_module(path: Path) -> None:
    """Create a module with very few significant lines (3), tiny at threshold 30 but not at 3."""
    write_module(
        path,
        """
        def tiny():
            x = 1
            return x
        """,
    )


def _make_large_module(path: Path, line_count: int = 40) -> None:
    """Create a module with many significant lines."""
    lines = [f"x_{i} = {i}" for i in range(line_count)]
    write_module(path, "\n".join(lines))


def test_detects_fragmented_package(tmp_path: Path):
    """Package with 4 modules, 3 tiny -> 75% tiny, exceeds 50% threshold."""
    pkg = tmp_path / "src" / "mypackage"
    pkg.mkdir(parents=True)
    write_module(pkg / "__init__.py", "")
    _make_tiny_module(pkg / "a.py")
    _make_tiny_module(pkg / "b.py")
    _make_tiny_module(pkg / "c.py")
    _make_large_module(pkg / "d.py")

    guard = FragmentationGuard()
    guard.repo_root = tmp_path
    result = guard.run(["--root", str(tmp_path / "src")])
    assert result == 1


def test_passes_healthy_package(tmp_path: Path):
    """Package with 4 modules, 1 tiny -> 25% tiny, under 50% threshold."""
    pkg = tmp_path / "src" / "mypackage"
    pkg.mkdir(parents=True)
    write_module(pkg / "__init__.py", "")
    _make_tiny_module(pkg / "a.py")
    _make_large_module(pkg / "b.py")
    _make_large_module(pkg / "c.py")
    _make_large_module(pkg / "d.py")

    guard = FragmentationGuard()
    guard.repo_root = tmp_path
    result = guard.run(["--root", str(tmp_path / "src")])
    assert result == 0


def test_skips_package_below_min_modules(tmp_path: Path):
    """Package with only 2 modules is below min_modules=3 threshold."""
    pkg = tmp_path / "src" / "small"
    pkg.mkdir(parents=True)
    write_module(pkg / "__init__.py", "")
    _make_tiny_module(pkg / "a.py")
    _make_tiny_module(pkg / "b.py")

    guard = FragmentationGuard()
    guard.repo_root = tmp_path
    result = guard.run(["--root", str(tmp_path / "src")])
    assert result == 0


def test_exactly_at_threshold(tmp_path: Path):
    """Package with exactly 50% tiny modules should pass (not >50%)."""
    pkg = tmp_path / "src" / "balanced"
    pkg.mkdir(parents=True)
    write_module(pkg / "__init__.py", "")
    _make_tiny_module(pkg / "a.py")
    _make_tiny_module(pkg / "b.py")
    _make_large_module(pkg / "c.py")
    _make_large_module(pkg / "d.py")

    guard = FragmentationGuard()
    guard.repo_root = tmp_path
    result = guard.run(["--root", str(tmp_path / "src")])
    assert result == 0


def test_all_large_modules(tmp_path: Path):
    """Package with no tiny modules should pass."""
    pkg = tmp_path / "src" / "solid"
    pkg.mkdir(parents=True)
    write_module(pkg / "__init__.py", "")
    _make_large_module(pkg / "a.py")
    _make_large_module(pkg / "b.py")
    _make_large_module(pkg / "c.py")

    guard = FragmentationGuard()
    guard.repo_root = tmp_path
    result = guard.run(["--root", str(tmp_path / "src")])
    assert result == 0


def test_custom_min_lines_threshold(tmp_path: Path):
    """Custom --min-lines changes what counts as 'tiny'."""
    pkg = tmp_path / "src" / "mypackage"
    pkg.mkdir(parents=True)
    write_module(pkg / "__init__.py", "")
    # These modules have ~5 significant lines each (tiny at 30, not tiny at 3)
    _make_tiny_module(pkg / "a.py")
    _make_tiny_module(pkg / "b.py")
    _make_tiny_module(pkg / "c.py")

    guard = FragmentationGuard()
    guard.repo_root = tmp_path
    # With min-lines=3, these aren't tiny anymore
    result = guard.run(["--root", str(tmp_path / "src"), "--min-lines", "3"])
    assert result == 0


def test_ignores_init_files(tmp_path: Path):
    """__init__.py files should not be counted as modules."""
    pkg = tmp_path / "src" / "mypackage"
    pkg.mkdir(parents=True)
    write_module(pkg / "__init__.py", "")
    _make_large_module(pkg / "a.py")
    _make_large_module(pkg / "b.py")
    _make_large_module(pkg / "c.py")

    guard = FragmentationGuard()
    guard.repo_root = tmp_path
    result = guard.run(["--root", str(tmp_path / "src")])
    assert result == 0


def test_multiple_packages_one_fragmented(tmp_path: Path):
    """Only the fragmented package should be flagged."""
    good_pkg = tmp_path / "src" / "good"
    good_pkg.mkdir(parents=True)
    write_module(good_pkg / "__init__.py", "")
    _make_large_module(good_pkg / "a.py")
    _make_large_module(good_pkg / "b.py")
    _make_large_module(good_pkg / "c.py")

    bad_pkg = tmp_path / "src" / "bad"
    bad_pkg.mkdir(parents=True)
    write_module(bad_pkg / "__init__.py", "")
    _make_tiny_module(bad_pkg / "a.py")
    _make_tiny_module(bad_pkg / "b.py")
    _make_tiny_module(bad_pkg / "c.py")

    guard = FragmentationGuard()
    guard.repo_root = tmp_path
    result = guard.run(["--root", str(tmp_path / "src")])
    assert result == 1


# ── CLI integration ───────────────────────────────────────────────────


@patch("sys.argv", ["fragmentation_guard.py"])
def test_main_no_violations(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "src"
    pkg = src / "mypackage"
    pkg.mkdir(parents=True)
    write_module(pkg / "__init__.py", "")
    _make_large_module(pkg / "a.py")
    _make_large_module(pkg / "b.py")
    _make_large_module(pkg / "c.py")

    result = FragmentationGuard.main()
    assert result == 0


@patch("sys.argv", ["fragmentation_guard.py"])
def test_main_with_violations(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "src"
    pkg = src / "mypackage"
    pkg.mkdir(parents=True)
    write_module(pkg / "__init__.py", "")
    _make_tiny_module(pkg / "a.py")
    _make_tiny_module(pkg / "b.py")
    _make_tiny_module(pkg / "c.py")

    result = FragmentationGuard.main()
    assert result == 1
