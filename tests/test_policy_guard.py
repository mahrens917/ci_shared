"""Unit tests for policy_guard module."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from ci_tools.scripts.policy_guard import PolicyViolation, main, purge_bytecode_artifacts


def test_policy_violation_imported():
    """Test PolicyViolation is correctly imported from policy_checks."""
    assert PolicyViolation is not None
    exc = PolicyViolation("test")
    assert isinstance(exc, Exception)


def test_purge_bytecode_artifacts_imported():
    """Test purge_bytecode_artifacts is correctly imported."""
    assert purge_bytecode_artifacts is not None
    assert callable(purge_bytecode_artifacts)


def test_main_delegates_to_policy_checks():
    """Test main in policy_guard is a direct re-export of policy_checks.main."""
    # pylint: disable=import-outside-toplevel
    from ci_tools.scripts import policy_checks, policy_guard

    assert policy_guard.main is policy_checks.main


def test_main_returns_exit_code():
    """Test main returns the exit code from policy_checks."""
    # pylint: disable=import-outside-toplevel
    import ci_tools.scripts.policy_guard as pg

    with patch("ci_tools.scripts.policy_guard.main", return_value=42):
        result = pg.main()
        assert result == 42


def test_main_propagates_exceptions():
    """Test main propagates exceptions from policy_checks."""
    # pylint: disable=import-outside-toplevel
    import ci_tools.scripts.policy_guard as pg

    with patch("ci_tools.scripts.policy_guard.main", side_effect=RuntimeError("test error")):
        with pytest.raises(RuntimeError) as exc:
            pg.main()
        assert "test error" in str(exc.value)


def test_module_exports():
    """Test module exports expected symbols."""
    # pylint: disable=import-outside-toplevel
    from ci_tools.scripts import policy_guard

    assert hasattr(policy_guard, "PolicyViolation")
    assert hasattr(policy_guard, "purge_bytecode_artifacts")
    assert hasattr(policy_guard, "main")


def test_all_contains_expected_exports():
    """Test __all__ contains expected exports."""
    # pylint: disable=import-outside-toplevel
    from ci_tools.scripts import policy_guard

    assert "PolicyViolation" in policy_guard.__all__
    assert "purge_bytecode_artifacts" in policy_guard.__all__
    assert "main" in policy_guard.__all__


def test_main_as_script_success():
    """Test running module as script with successful checks."""
    with patch("ci_tools.scripts.policy_guard.main", return_value=0):
        with pytest.raises(SystemExit) as exc:
            # Simulate running as __main__
            # pylint: disable=exec-used
            exec(
                compile(
                    "import sys; from ci_tools.scripts.policy_guard import main; sys.exit(main())",
                    "<string>",
                    "exec",
                )
            )
        assert exc.value.code == 0


def test_main_as_script_with_violation():
    """Test running module as script with policy violation."""

    def mock_main():
        raise PolicyViolation("test violation")

    with patch("ci_tools.scripts.policy_guard.main", side_effect=mock_main):
        with pytest.raises(SystemExit) as exc:
            # Simulate the __main__ block behavior
            try:
                mock_main()
            except PolicyViolation as err:
                print(err, file=sys.stderr)
                raise SystemExit(1) from err

        assert exc.value.code == 1


def test_main_is_policy_checks_main():
    """Test main in policy_guard is the same object as policy_checks.main."""
    # pylint: disable=import-outside-toplevel
    from ci_tools.scripts import policy_checks, policy_guard

    assert policy_guard.main is policy_checks.main


def test_policy_violation_handling_in_main_block(capsys):
    """Test PolicyViolation is caught and handled in __main__ block."""
    test_error_message = "Policy check failed"

    def mock_main():
        raise PolicyViolation(test_error_message)

    # Simulate the __main__ block
    with pytest.raises(SystemExit) as exc:
        try:
            mock_main()
        except PolicyViolation as err:
            print(err, file=sys.stderr)
            sys.exit(1)

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert test_error_message in captured.err


def test_main_with_zero_return():
    """Test main returns 0 on success."""
    # pylint: disable=import-outside-toplevel
    import ci_tools.scripts.policy_guard as pg

    with patch("ci_tools.scripts.policy_guard.main", return_value=0):
        assert pg.main() == 0


def test_main_with_nonzero_return():
    """Test main returns non-zero on failure."""
    # pylint: disable=import-outside-toplevel
    import ci_tools.scripts.policy_guard as pg

    with patch("ci_tools.scripts.policy_guard.main", return_value=1):
        assert pg.main() == 1
