"""Integration tests for xci.sh bash wrapper."""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_xci_help_flag():
    """Test xci.sh --help displays usage information."""
    xci_path = Path(__file__).parent.parent / "ci_tools" / "scripts" / "xci.sh"
    assert xci_path.exists(), f"xci.sh not found at {xci_path}"

    result = subprocess.run(
        [str(xci_path), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, f"--help failed with exit code {result.returncode}"
    assert "xci.sh - Automated CI repair loop" in result.stdout
    assert "Usage: xci.sh [ci-command...]" in result.stdout
    assert "Configuration Options:" in result.stdout
    assert "max_attempts" in result.stdout
    assert "Configuration File (xci.config.json):" in result.stdout


def test_xci_short_help_flag():
    """Test xci.sh -h displays usage information."""
    xci_path = Path(__file__).parent.parent / "ci_tools" / "scripts" / "xci.sh"
    result = subprocess.run(
        [str(xci_path), "-h"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "xci.sh - Automated CI repair loop" in result.stdout


def test_xci_help_word():
    """Test xci.sh help displays usage information."""
    xci_path = Path(__file__).parent.parent / "ci_tools" / "scripts" / "xci.sh"
    result = subprocess.run(
        [str(xci_path), "help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "xci.sh - Automated CI repair loop" in result.stdout


def test_xci_version_flag():
    """Test xci.sh --version displays version information."""
    xci_path = Path(__file__).parent.parent / "ci_tools" / "scripts" / "xci.sh"
    result = subprocess.run(
        [str(xci_path), "--version"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, f"--version failed with exit code {result.returncode}"
    assert "xci.sh version" in result.stdout
    assert "codex-ci-tools" in result.stdout


def test_xci_short_version_flag():
    """Test xci.sh -v displays version information."""
    xci_path = Path(__file__).parent.parent / "ci_tools" / "scripts" / "xci.sh"
    result = subprocess.run(
        [str(xci_path), "-v"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "xci.sh version" in result.stdout


def test_xci_version_word():
    """Test xci.sh version displays version information."""
    xci_path = Path(__file__).parent.parent / "ci_tools" / "scripts" / "xci.sh"
    result = subprocess.run(
        [str(xci_path), "version"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "xci.sh version" in result.stdout


def test_xci_error_message_when_no_ci_found(tmp_path):
    """Test xci.sh shows helpful error when CI script not found."""
    xci_path = Path(__file__).parent.parent / "ci_tools" / "scripts" / "xci.sh"

    result = subprocess.run(
        [str(xci_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(tmp_path),
    )

    assert result.returncode == 2
    assert "ERROR: Unable to locate an executable ci.sh" in result.stderr
    assert "Searched: ./ci.sh, scripts/ci.sh, scripts/dev/ci.sh" in result.stderr
    assert "xci.sh --help" in result.stderr


def test_xci_creates_archive_directory(tmp_path):
    """Test xci.sh creates .xci/archive directory on startup."""
    xci_path = Path(__file__).parent.parent / "ci_tools" / "scripts" / "xci.sh"

    subprocess.run(
        [str(xci_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(tmp_path),
    )

    archive_dir = tmp_path / ".xci" / "archive"
    assert archive_dir.exists(), ".xci/archive directory should be created"
    assert archive_dir.is_dir(), ".xci/archive should be a directory"
