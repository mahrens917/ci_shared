"""Unit tests for data_guard module."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import write_module
from ci_tools.scripts import data_guard
from ci_tools.scripts.guard_common import parse_python_ast, relative_path

EMPTY_ALLOWLIST = {
    "assignments": set(),
    "comparisons": set(),
    "dataframe": set(),
    "dataframe_paths": set(),
}

TESTS_EXCLUDED_ALLOWLIST = {
    "assignments": set(),
    "comparisons": set(),
    "dataframe": set(),
    "dataframe_paths": {"tests/**"},
}


def write_allowlist(path: Path, content: dict) -> None:
    """Helper to write a JSON allowlist file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2))


class TestAllowlistLoading:
    """Test allowlist loading functionality."""

    def test_load_allowlist_missing_file(self, tmp_path: Path) -> None:
        """Test loading allowlist when file doesn't exist."""
        with patch.object(data_guard, "ALLOWLIST_PATH", tmp_path / "missing.json"):
            result = data_guard.load_allowlist()
            assert result == {
                "assignments": set(),
                "comparisons": set(),
                "dataframe": set(),
                "dataframe_paths": set(),
            }

    def test_load_allowlist_valid_file(self, tmp_path: Path) -> None:
        """Test loading valid allowlist file."""
        allowlist_path = tmp_path / "allowlist.json"
        content = {
            "assignments": ["threshold_value", "max_retries"],
            "comparisons": ["timeout"],
            "dataframe": ["pd.DataFrame"],
        }
        write_allowlist(allowlist_path, content)

        with patch.object(data_guard, "ALLOWLIST_PATH", allowlist_path):
            result = data_guard.load_allowlist()
            assert result["assignments"] == {"threshold_value", "max_retries"}
            assert result["comparisons"] == {"timeout"}
            assert result["dataframe"] == {"pd.DataFrame"}

    def test_load_allowlist_invalid_json(self, tmp_path: Path) -> None:
        """Test loading allowlist with invalid JSON."""
        allowlist_path = tmp_path / "invalid.json"
        allowlist_path.write_text("{ invalid json")

        with patch.object(data_guard, "ALLOWLIST_PATH", allowlist_path):
            with pytest.raises(data_guard.DataGuardAllowlistError) as exc_info:
                data_guard.load_allowlist()
            assert "JSON parse error" in str(exc_info.value)

    def test_load_allowlist_coerces_types(self, tmp_path: Path) -> None:
        """Test that allowlist values are coerced to strings."""
        allowlist_path = tmp_path / "allowlist.json"
        content = {"assignments": [123, "string_value", True], "comparisons": []}
        write_allowlist(allowlist_path, content)

        with patch.object(data_guard, "ALLOWLIST_PATH", allowlist_path):
            result = data_guard.load_allowlist()
            assert result["assignments"] == {"123", "string_value", "True"}

    def test_allowlisted_checks_membership(self, tmp_path: Path) -> None:
        """Test allowlisted helper function."""
        allowlist_path = tmp_path / "allowlist.json"
        content = {"assignments": ["allowed_var"]}
        write_allowlist(allowlist_path, content)

        with patch.object(data_guard, "ALLOWLIST_PATH", allowlist_path):
            with patch.object(data_guard, "ALLOWLIST", data_guard.load_allowlist()):
                assert data_guard.allowlisted("allowed_var", "assignments")
                assert not data_guard.allowlisted("other_var", "assignments")
                assert not data_guard.allowlisted("allowed_var", "comparisons")


class TestASTUtilities:
    """Test AST utility functions."""

    def test_parse_ast_valid_file(self, tmp_path: Path) -> None:
        """Test parsing valid Python file."""
        target = tmp_path / "valid.py"
        write_module(target, "def foo(): pass")

        tree = parse_python_ast(target, raise_on_error=False)
        assert tree is not None
        assert isinstance(tree, ast.Module)

    def test_parse_ast_invalid_syntax(self, tmp_path: Path) -> None:
        """Test parsing file with syntax errors."""
        target = tmp_path / "invalid.py"
        target.write_text("def foo(")

        tree = parse_python_ast(target, raise_on_error=False)
        assert tree is None

    def test_extract_target_names_simple(self) -> None:
        """Test extracting names from simple assignment."""
        code = "x = 10"
        tree = ast.parse(code)
        stmt = tree.body[0]
        assert isinstance(stmt, ast.Assign)
        names = list(data_guard.extract_target_names(stmt.targets[0]))
        assert names == ["x"]

    def test_extract_target_names_tuple(self) -> None:
        """Test extracting names from tuple unpacking."""
        code = "x, y = 1, 2"
        tree = ast.parse(code)
        stmt = tree.body[0]
        assert isinstance(stmt, ast.Assign)
        names = list(data_guard.extract_target_names(stmt.targets[0]))
        assert set(names) == {"x", "y"}

    def test_extract_target_names_attribute(self) -> None:
        """Test extracting names from attribute assignment."""
        code = "obj.attr = 10"
        tree = ast.parse(code)
        stmt = tree.body[0]
        assert isinstance(stmt, ast.Assign)
        names = list(data_guard.extract_target_names(stmt.targets[0]))
        assert names == ["attr"]

    def test_is_all_caps_identifier(self) -> None:
        """Test constant identifier detection."""
        assert data_guard.is_all_caps_identifier("MAX_RETRY")
        assert data_guard.is_all_caps_identifier("TIMEOUT")
        assert not data_guard.is_all_caps_identifier("max_retry")
        assert not data_guard.is_all_caps_identifier("MaxRetry")
        assert not data_guard.is_all_caps_identifier("")
        assert not data_guard.is_all_caps_identifier("123")

    def test_is_numeric_constant(self) -> None:
        """Test numeric constant detection."""
        code = "x = 42"
        tree = ast.parse(code)
        stmt = tree.body[0]
        assert isinstance(stmt, ast.Assign)
        assert data_guard.is_numeric_constant(stmt.value)

        code = "x = 'string'"
        tree = ast.parse(code)
        stmt2 = tree.body[0]
        assert isinstance(stmt2, ast.Assign)
        assert not data_guard.is_numeric_constant(stmt2.value)

    def test_literal_value_repr(self) -> None:
        """Test literal value representation."""
        code = "x = 42"
        tree = ast.parse(code)
        stmt = tree.body[0]
        assert isinstance(stmt, ast.Assign)
        assert data_guard.literal_value_repr(stmt.value) == "42"

        code = "x = 'string'"
        tree = ast.parse(code)
        stmt2 = tree.body[0]
        assert isinstance(stmt2, ast.Assign)
        assert data_guard.literal_value_repr(stmt2.value) == "'string'"

        assert "None" in data_guard.literal_value_repr(None)


class TestAssignmentViolations:
    """Test sensitive assignment detection."""

    def test_should_flag_assignment_sensitive_name(self) -> None:
        """Test flagging assignment with sensitive name."""
        code = "threshold = 100"
        tree = ast.parse(code)
        stmt = tree.body[0]
        assert isinstance(stmt, ast.Assign)
        names = list(data_guard.extract_target_names(stmt.targets[0]))

        assert data_guard.should_flag_assignment(names, stmt.value)

    def test_should_flag_assignment_constant_ignored(self) -> None:
        """Test that all-caps constants are not flagged."""
        code = "MAX_THRESHOLD = 100"
        tree = ast.parse(code)
        stmt = tree.body[0]
        assert isinstance(stmt, ast.Assign)
        names = list(data_guard.extract_target_names(stmt.targets[0]))

        assert not data_guard.should_flag_assignment(names, stmt.value)

    def test_should_flag_assignment_allowed_literals(self) -> None:
        """Test that 0, 1, -1 are not flagged."""
        for value in [0, 1, -1]:
            code = f"threshold = {value}"
            tree = ast.parse(code)
            stmt = tree.body[0]
            assert isinstance(stmt, ast.Assign)
            names = list(data_guard.extract_target_names(stmt.targets[0]))
            assert not data_guard.should_flag_assignment(names, stmt.value)

    def test_contains_sensitive_token(self) -> None:
        """Test sensitive token detection."""
        assert data_guard.contains_sensitive_token(["threshold"])
        assert data_guard.contains_sensitive_token(["max_value"])
        assert data_guard.contains_sensitive_token(["retry_count"])
        assert not data_guard.contains_sensitive_token(["regular_var"])

    def test_assignment_violation_from_node_simple(self, tmp_path: Path) -> None:
        """Test creating violation from simple assignment."""
        code = "threshold = 100"
        tree = ast.parse(code)
        stmt = tree.body[0]
        assert isinstance(stmt, ast.Assign)

        with patch.object(
            data_guard,
            "ALLOWLIST",
            EMPTY_ALLOWLIST,
        ):
            violation = data_guard.assignment_violation_from_node(tmp_path / "test.py", stmt)
            assert violation is not None
            assert "literal assignment" in violation.message
            assert "threshold" in violation.message

    def test_assignment_violation_from_node_annotated(self, tmp_path: Path) -> None:
        """Test creating violation from annotated assignment."""
        code = "max_retries: int = 5"
        tree = ast.parse(code)
        stmt = tree.body[0]
        assert isinstance(stmt, ast.AnnAssign)

        with patch.object(
            data_guard,
            "ALLOWLIST",
            EMPTY_ALLOWLIST,
        ):
            violation = data_guard.assignment_violation_from_node(tmp_path / "test.py", stmt)
            assert violation is not None
            assert "annotated literal assignment" in violation.message
            assert "max_retries" in violation.message

    def test_collect_sensitive_assignments(self, tmp_path: Path) -> None:
        """Test collecting all sensitive assignments."""
        target = tmp_path / "src" / "test.py"
        target.parent.mkdir(parents=True)
        write_module(
            target,
            """
            threshold = 100
            MAX_THRESHOLD = 200
            timeout: int = 30
            regular_var = 50
            """,
        )

        with patch.object(data_guard, "SCAN_DIRECTORIES", (tmp_path / "src",)):
            with patch.object(data_guard, "ROOT", tmp_path):
                with patch.object(
                    data_guard,
                    "ALLOWLIST",
                    EMPTY_ALLOWLIST,
                ):
                    violations = data_guard.collect_sensitive_assignments()
                    assert len(violations) >= 2  # threshold and timeout
                    messages = [v.message for v in violations]
                    assert any("threshold" in msg for msg in messages)
                    assert any("timeout" in msg for msg in messages)


class TestComparisonViolations:
    """Test numeric comparison detection."""

    def test_should_flag_comparison_sensitive_name(self) -> None:
        """Test flagging comparison with sensitive name."""
        with patch.object(
            data_guard,
            "ALLOWLIST",
            EMPTY_ALLOWLIST,
        ):
            assert data_guard.should_flag_comparison(["threshold"])
            assert not data_guard.should_flag_comparison(["THRESHOLD"])
            assert not data_guard.should_flag_comparison([])

    def test_literal_comparators(self) -> None:
        """Test extracting literal comparators."""
        code = "if threshold > 100: pass"
        tree = ast.parse(code)
        stmt = tree.body[0]
        assert isinstance(stmt, ast.If)
        assert isinstance(stmt.test, ast.Compare)
        compare = stmt.test

        literals = data_guard.literal_comparators(compare)
        assert len(literals) == 1
        assert literals[0].value == 100

    def test_literal_comparators_allowed_values(self) -> None:
        """Test that 0, 1, -1 comparators are not flagged."""
        for value in [0, 1, -1]:
            code = f"if threshold > {value}: pass"
            tree = ast.parse(code)
            stmt = tree.body[0]
            assert isinstance(stmt, ast.If)
            assert isinstance(stmt.test, ast.Compare)
            compare = stmt.test

            literals = data_guard.literal_comparators(compare)
            assert len(literals) == 0

    def test_comparison_targets(self) -> None:
        """Test extracting comparison targets."""
        code = "if threshold > 100: pass"
        tree = ast.parse(code)
        stmt = tree.body[0]
        assert isinstance(stmt, ast.If)
        assert isinstance(stmt.test, ast.Compare)
        compare = stmt.test

        targets = data_guard.comparison_targets(compare)
        assert targets == ["threshold"]

    def test_format_comparison_message(self) -> None:
        """Test formatting comparison violation message."""
        code = "if threshold > 100: pass"
        tree = ast.parse(code)
        stmt = tree.body[0]
        assert isinstance(stmt, ast.If)
        assert isinstance(stmt.test, ast.Compare)
        compare = stmt.test

        assert len(compare.comparators) > 0
        assert isinstance(compare.comparators[0], ast.Constant)
        literals = [compare.comparators[0]]
        message = data_guard.format_comparison_message(literals, ["threshold"])
        assert "comparison against literal" in message
        assert "100" in message
        assert "threshold" in message

    def test_collect_numeric_comparisons(self, tmp_path: Path) -> None:
        """Test collecting all numeric comparisons."""
        target = tmp_path / "src" / "test.py"
        target.parent.mkdir(parents=True)
        write_module(
            target,
            """
            def check(threshold):
                if threshold > 100:
                    return True
                if MAX_THRESHOLD < 200:
                    return False
                return threshold == 50
            """,
        )

        with patch.object(data_guard, "SCAN_DIRECTORIES", (tmp_path / "src",)):
            with patch.object(data_guard, "ROOT", tmp_path):
                with patch.object(
                    data_guard,
                    "ALLOWLIST",
                    EMPTY_ALLOWLIST,
                ):
                    violations = data_guard.collect_numeric_comparisons()
                    assert len(violations) >= 1
                    assert any("threshold" in v.message for v in violations)


class TestDataframeLiterals:
    """Test DataFrame literal detection."""

    def test_contains_literal_dataset_list(self) -> None:
        """Test detecting literal datasets in lists."""
        code = "[1, 2, 3]"
        tree = ast.parse(code)
        stmt = tree.body[0]
        assert isinstance(stmt, ast.Expr)
        assert data_guard.contains_literal_dataset(stmt.value)

    def test_contains_literal_dataset_dict(self) -> None:
        """Test detecting literal datasets in dicts."""
        code = "{'a': 1, 'b': 2}"
        tree = ast.parse(code)
        stmt = tree.body[0]
        assert isinstance(stmt, ast.Expr)
        assert data_guard.contains_literal_dataset(stmt.value)

    def test_contains_literal_dataset_nested(self) -> None:
        """Test detecting literal datasets in nested structures."""
        code = "[[1, 2], [3, 4]]"
        tree = ast.parse(code)
        stmt = tree.body[0]
        assert isinstance(stmt, ast.Expr)
        assert data_guard.contains_literal_dataset(stmt.value)

    def test_contains_literal_dataset_empty(self) -> None:
        """Test that empty containers don't count as literal datasets."""
        code = "[]"
        tree = ast.parse(code)
        stmt = tree.body[0]
        assert isinstance(stmt, ast.Expr)
        assert not data_guard.contains_literal_dataset(stmt.value)

    def test_get_call_qualname(self) -> None:
        """Test extracting qualified names from calls."""
        code = "pd.DataFrame()"
        tree = ast.parse(code)
        stmt = tree.body[0]
        assert isinstance(stmt, ast.Expr)
        assert isinstance(stmt.value, ast.Call)
        call = stmt.value

        qualname = data_guard.get_call_qualname(call.func)
        assert qualname == "pd.DataFrame"

    def test_get_call_qualname_simple(self) -> None:
        """Test extracting simple names from calls."""
        code = "DataFrame()"
        tree = ast.parse(code)
        stmt = tree.body[0]
        assert isinstance(stmt, ast.Expr)
        assert isinstance(stmt.value, ast.Call)
        call = stmt.value

        qualname = data_guard.get_call_qualname(call.func)
        assert qualname == "DataFrame"

    def test_call_contains_literal_arguments(self) -> None:
        """Test detecting literal arguments in calls."""
        code = "pd.DataFrame([1, 2, 3])"
        tree = ast.parse(code)
        stmt = tree.body[0]
        assert isinstance(stmt, ast.Expr)
        assert isinstance(stmt.value, ast.Call)
        call = stmt.value

        assert data_guard.call_contains_literal_arguments(call)

    def test_collect_dataframe_literals(self, tmp_path: Path) -> None:
        """Test collecting DataFrame calls with literal data."""
        target = tmp_path / "src" / "test.py"
        target.parent.mkdir(parents=True)
        write_module(
            target,
            """
            import pandas as pd

            def create_df():
                df1 = pd.DataFrame([1, 2, 3])
                df2 = pd.DataFrame(data)
                return df1, df2
            """,
        )

        with patch.object(data_guard, "SCAN_DIRECTORIES", (tmp_path / "src",)):
            with patch.object(data_guard, "ROOT", tmp_path):
                with patch.object(
                    data_guard,
                    "ALLOWLIST",
                    EMPTY_ALLOWLIST,
                ):
                    violations = data_guard.collect_dataframe_literals()
                    assert len(violations) >= 1
                    assert any("pd.DataFrame" in v.message for v in violations)


class TestPathExclusion:
    """Test path-based exclusion functionality."""

    def test_path_excluded_matches_glob(self, tmp_path: Path) -> None:
        """Test that path_excluded correctly matches glob patterns."""
        with patch.object(
            data_guard,
            "ALLOWLIST",
            TESTS_EXCLUDED_ALLOWLIST,
        ):
            test_file = tmp_path / "tests" / "test_module.py"
            assert data_guard.path_excluded(test_file, tmp_path)

    def test_path_excluded_no_match(self, tmp_path: Path) -> None:
        """Test that path_excluded returns False for non-matching paths."""
        with patch.object(
            data_guard,
            "ALLOWLIST",
            TESTS_EXCLUDED_ALLOWLIST,
        ):
            src_file = tmp_path / "src" / "module.py"
            assert not data_guard.path_excluded(src_file, tmp_path)

    def test_path_excluded_empty_patterns(self, tmp_path: Path) -> None:
        """Test that path_excluded returns False when no patterns defined."""
        with patch.object(
            data_guard,
            "ALLOWLIST",
            EMPTY_ALLOWLIST,
        ):
            test_file = tmp_path / "tests" / "test_module.py"
            assert not data_guard.path_excluded(test_file, tmp_path)

    def test_dataframe_literals_excluded_in_tests(self, tmp_path: Path) -> None:
        """Test that DataFrame literals in test files are excluded."""
        target = tmp_path / "tests" / "test_module.py"
        target.parent.mkdir(parents=True)
        write_module(
            target,
            """
            import pandas as pd
            df = pd.DataFrame([1, 2, 3])
            """,
        )

        with patch.object(data_guard, "SCAN_DIRECTORIES", (tmp_path / "tests",)):
            with patch.object(data_guard, "ROOT", tmp_path):
                with patch.object(
                    data_guard,
                    "ALLOWLIST",
                    TESTS_EXCLUDED_ALLOWLIST,
                ):
                    violations = data_guard.collect_dataframe_literals()
                    assert len(violations) == 0


class TestIterators:
    """Test file iteration utilities."""

    def test_iter_python_files(self, tmp_path: Path) -> None:
        """Test iterating Python files."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "test1.py").write_text("# test")
        (src / "test2.py").write_text("# test")
        (src / "data.txt").write_text("not python")

        files = list(data_guard.iter_python_files([src]))
        assert len(files) == 2
        assert all(f.suffix == ".py" for f in files)

    def test_iter_python_files_missing_directory(self, tmp_path: Path) -> None:
        """Test iterating with non-existent directory."""
        missing = tmp_path / "missing"
        files = list(data_guard.iter_python_files([missing]))
        assert len(files) == 0

    def test_normalize_path(self, tmp_path: Path) -> None:
        """Test path normalization."""
        path = tmp_path / "src" / "module.py"
        normalized = relative_path(path, tmp_path, as_string=True)
        assert normalized == "src/module.py"


class TestMainFunction:
    """Test main function and CLI behavior."""

    def test_violation_immutable(self, tmp_path: Path) -> None:
        """Test that Violation is frozen."""
        violation = data_guard.Violation(
            path=tmp_path / "test.py", lineno=42, message="test violation"
        )

        with pytest.raises(Exception):  # FrozenInstanceError
            setattr(violation, "lineno", 43)

    def test_main_no_violations(self, tmp_path: Path) -> None:
        """Test main function with no violations."""
        target = tmp_path / "src" / "clean.py"
        target.parent.mkdir(parents=True)
        write_module(
            target,
            """
            def clean_function():
                return 42
            """,
        )

        with patch.object(data_guard, "SCAN_DIRECTORIES", (tmp_path / "src",)):
            with patch.object(data_guard, "ROOT", tmp_path):
                with patch.object(
                    data_guard,
                    "ALLOWLIST",
                    EMPTY_ALLOWLIST,
                ):
                    result = data_guard.main()
                    assert result == 0

    def test_main_with_violations(
        self, tmp_path: Path    ) -> None:
        """Test main function with violations."""
        target = tmp_path / "src" / "violations.py"
        target.parent.mkdir(parents=True)
        write_module(
            target,
            """
            threshold = 100
            """,
        )

        with patch.object(data_guard, "SCAN_DIRECTORIES", (tmp_path / "src",)):
            with patch.object(data_guard, "ROOT", tmp_path):
                with patch.object(
                    data_guard,
                    "ALLOWLIST",
                    EMPTY_ALLOWLIST,
                ):
                    with pytest.raises(data_guard.DataGuardViolation) as exc_info:
                        data_guard.main()
                    assert "Data integrity violations detected" in str(exc_info.value)
                    assert "threshold" in str(exc_info.value)

    def test_main_script_entry_point(
        self, tmp_path: Path    ) -> None:
        """Test __main__ entry point."""
        target = tmp_path / "src" / "test.py"
        target.parent.mkdir(parents=True)
        write_module(target, "x = 1")

        # We can't easily test the __main__ entry point due to module-level code
        # Instead, test that main() can be called successfully
        with patch.object(data_guard, "SCAN_DIRECTORIES", (tmp_path / "src",)):
            with patch.object(data_guard, "ROOT", tmp_path):
                with patch.object(
                    data_guard,
                    "ALLOWLIST",
                    EMPTY_ALLOWLIST,
                ):
                    result = data_guard.main()
                    assert result == 0

    def test_collect_all_violations_comprehensive(self, tmp_path: Path) -> None:
        """Test collecting all types of violations."""
        target = tmp_path / "src" / "test.py"
        target.parent.mkdir(parents=True)
        write_module(
            target,
            """
            import pandas as pd

            # Assignment violation
            threshold = 100

            # Comparison violation
            def check(timeout):
                if timeout > 500:
                    return True
                return False

            # DataFrame literal violation
            def create_data():
                return pd.DataFrame([1, 2, 3])
            """,
        )

        with patch.object(data_guard, "SCAN_DIRECTORIES", (tmp_path / "src",)):
            with patch.object(data_guard, "ROOT", tmp_path):
                with patch.object(
                    data_guard,
                    "ALLOWLIST",
                    EMPTY_ALLOWLIST,
                ):
                    violations = data_guard.collect_all_violations()
                    assert len(violations) >= 3

                    # Check we got different types of violations
                    messages = [v.message for v in violations]
                    has_assignment = any("literal assignment" in msg for msg in messages)
                    has_comparison = any("comparison" in msg for msg in messages)
                    has_dataframe = any("DataFrame" in msg for msg in messages)

                    assert has_assignment or has_comparison or has_dataframe
