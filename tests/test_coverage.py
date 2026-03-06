"""Unit tests for ci_tools.ci_runtime.coverage module."""

from __future__ import annotations


from ci_tools.ci_runtime.coverage import (
    _find_coverage_table,
    _parse_coverage_entries,
    extract_coverage_deficits,
)
from ci_tools.ci_runtime.models import CoverageCheckResult, CoverageDeficit
from tests.test_constants import get_constant

COVERAGE_CONSTANTS = get_constant("coverage")


class TestFindCoverageTable:
    """Tests for _find_coverage_table helper function."""

    def test_finds_coverage_table_with_header(self):
        """Test finds coverage table when header is present."""
        lines = [
            "Running tests...",
            "Name                 Stmts   Miss  Cover",
            "-----------------------------------------",
            "src/module.py          100     20    80%",
            "",
            "TOTAL                  100     20    80%",
        ]
        table = _find_coverage_table(lines)
        assert table is not None
        assert len(table) > 1
        table_list = list(table)  # Convert to list to make pylint happy
        assert "Name" in table_list[0]
        assert "Cover" in table_list[0]

    def test_returns_none_when_no_header_found(self):
        """Test returns None when no coverage header found."""
        lines = ["Just some output", "No coverage table here"]
        table = _find_coverage_table(lines)
        assert table is None

    def test_includes_all_rows_until_blank_line(self):
        """Test includes all table rows until blank line."""
        lines = [
            "Name                 Stmts   Cover",
            "-----------------------------------",
            "file1.py                10    50%",
            "file2.py                20    75%",
            "",
            "Other output",
        ]
        table = _find_coverage_table(lines)
        assert table is not None
        assert len(table) == COVERAGE_CONSTANTS["table_row_count"]
        table_list = list(table)  # Convert to list to make pylint happy
        assert "file1.py" in table_list[2]
        assert "file2.py" in table_list[3]

    def test_handles_table_at_end_of_output(self):
        """Test handles coverage table at end of output."""
        lines = [
            "Previous output",
            "Name       Cover",
            "file.py      80%",
        ]
        table = _find_coverage_table(lines)
        assert table is not None
        assert len(table) == COVERAGE_CONSTANTS["minimal_table_length"]

    def test_returns_none_for_header_only(self):
        """Test returns None when only header exists."""
        lines = ["Name                 Stmts   Cover"]
        table = _find_coverage_table(lines)
        assert table is None  # len(table) must be > 1

    def test_handles_whitespace_in_header(self):
        """Test handles extra whitespace in header line."""
        lines = [
            "  Name                 Stmts   Cover  ",
            "file.py                  10    50%",
            "",
        ]
        table = _find_coverage_table(lines)
        assert table is not None
        table_list = list(table)
        assert "Name" in table_list[0]

    def test_finds_first_matching_header(self):
        """Test finds first matching coverage header."""
        lines = [
            "First Name section",
            "Name                 Cover",
            "file1.py               50%",
            "",
            "Second Name section",
            "Name                 Cover",
            "file2.py               60%",
        ]
        table = _find_coverage_table(lines)
        assert table is not None
        assert "file1.py" in "".join(table)
        # Should stop at first blank line after first header


class TestParseCoverageEntries:
    """Tests for _parse_coverage_entries helper function."""

    def test_parses_entries_below_threshold(self):
        """Test parses coverage entries below threshold."""
        rows = [
            "Name                 Stmts   Miss  Cover",
            "-----------------------------------------",
            "src/module.py          100     40    60%",
            "src/other.py           200     10    95%",
        ]
        deficits = _parse_coverage_entries(rows, threshold=COVERAGE_CONSTANTS["threshold"])
        assert len(deficits) == 1
        assert deficits[0].path == "src/module.py"
        assert deficits[0].coverage == COVERAGE_CONSTANTS["low_coverage_percent"]

    def test_ignores_entries_above_threshold(self):
        """Test ignores entries at or above threshold."""
        rows = [
            "module1.py    100     10    90%",
            "module2.py    100     20    80%",
        ]
        deficits = _parse_coverage_entries(rows, threshold=COVERAGE_CONSTANTS["threshold"])
        assert len(deficits) == 0

    def test_skips_separator_lines(self):
        """Test skips separator lines with dashes."""
        rows = [
            "Name                 Stmts   Miss  Cover",
            "-----------------------------------------",
            "file.py                100     50    50%",
        ]
        deficits = _parse_coverage_entries(rows, threshold=COVERAGE_CONSTANTS["threshold"])
        assert len(deficits) == 1
        assert deficits[0].path == "file.py"

    def test_skips_total_row(self):
        """Test skips TOTAL summary row."""
        rows = [
            "module.py          100     50    50%",
            "TOTAL              100     50    50%",
        ]
        deficits = _parse_coverage_entries(rows, threshold=COVERAGE_CONSTANTS["threshold"])
        assert len(deficits) == 1
        assert deficits[0].path == "module.py"

    def test_handles_paths_with_spaces(self):
        """Test handles file paths that contain spaces."""
        rows = [
            "src/my module.py       100     50    50%",
        ]
        deficits = _parse_coverage_entries(rows, threshold=COVERAGE_CONSTANTS["threshold"])
        assert len(deficits) == 1
        assert "my module.py" in deficits[0].path

    def test_parses_percentage_correctly(self):
        """Test parses coverage percentage correctly."""
        rows = [
            "file.py    10    5    45%",
        ]
        deficits = _parse_coverage_entries(rows, threshold=50.0)
        assert deficits[0].coverage == COVERAGE_CONSTANTS["parsed_percentage"]

    def test_skips_malformed_rows(self):
        """Test skips rows that don't have expected format."""
        rows = [
            "incomplete",
            "only two tokens",
            "file.py    100     50    50%",  # valid
        ]
        deficits = _parse_coverage_entries(rows, threshold=COVERAGE_CONSTANTS["threshold"])
        assert len(deficits) == 1
        assert deficits[0].path == "file.py"

    def test_skips_rows_without_percentage(self):
        """Test skips rows where coverage doesn't end with %."""
        rows = [
            "file.py    100     50    noPercent",
        ]
        deficits = _parse_coverage_entries(rows, threshold=COVERAGE_CONSTANTS["threshold"])
        assert len(deficits) == 0

    def test_handles_empty_row_list(self):
        """Test handles empty row list."""
        deficits = _parse_coverage_entries([], threshold=COVERAGE_CONSTANTS["threshold"])
        assert len(deficits) == 0

    def test_handles_blank_lines(self):
        """Test handles blank lines in rows."""
        rows = [
            "",
            "   ",
            "file.py    100    50    50%",
        ]
        deficits = _parse_coverage_entries(rows, threshold=COVERAGE_CONSTANTS["threshold"])
        assert len(deficits) == 1

    def test_parses_float_percentages(self):
        """Test parses float percentages correctly."""
        rows = [
            "file.py    100    27    72.5%",
        ]
        deficits = _parse_coverage_entries(rows, threshold=75.0)
        assert len(deficits) == 1
        assert deficits[0].coverage == 72.5

    def test_handles_value_error_in_parsing(self):
        """Test handles ValueError when parsing invalid percentage."""
        rows = [
            "file.py    100    50    abc%",
        ]
        deficits = _parse_coverage_entries(rows, threshold=COVERAGE_CONSTANTS["threshold"])
        assert len(deficits) == 0


class TestExtractCoverageDeficits:
    """Tests for extract_coverage_deficits public API."""

    def test_extracts_deficits_from_pytest_output(self):
        """Test extracts coverage deficits from pytest output."""
        output = """
========== test session starts ===========
platform linux -- Python 3.10.0

----------- coverage: platform linux, python 3.10.0 -----------
Name                 Stmts   Miss  Cover
-----------------------------------------
src/module1.py         100     40    60%
src/module2.py         200     10    95%
-----------------------------------------
TOTAL                  300     50    83%
"""
        result = extract_coverage_deficits(output, threshold=80.0)
        assert result is not None
        assert len(result.deficits) == 1
        assert result.deficits[0].path == "src/module1.py"
        assert result.deficits[0].coverage == 60.0
        assert result.threshold == 80.0

    def test_returns_none_when_no_table_found(self):
        """Test returns None when no coverage table found."""
        output = "No coverage information in this output"
        result = extract_coverage_deficits(output, threshold=80.0)
        assert result is None

    def test_returns_none_for_empty_output(self):
        """Test returns None for empty output string."""
        result = extract_coverage_deficits("", threshold=80.0)
        assert result is None

    def test_returns_none_when_no_deficits(self):
        """Test returns None when all modules meet threshold."""
        output = """
Name                 Stmts   Miss  Cover
-----------------------------------------
src/module.py          100      5    95%
TOTAL                  100      5    95%
"""
        result = extract_coverage_deficits(output, threshold=80.0)
        assert result is None

    def test_uses_explicit_threshold(self):
        """Test uses explicit threshold parameter."""
        output = """
Name                 Stmts   Miss  Cover
-----------------------------------------
module.py              100     25    75%
"""
        result = extract_coverage_deficits(output, threshold=80.0)
        assert result is not None
        assert result.threshold == 80.0

    def test_includes_table_text_in_result(self):
        """Test includes original table text in result."""
        output = """
Name                 Stmts   Miss  Cover
-----------------------------------------
module.py              100     50    50%

TOTAL                  100     50    50%
"""
        result = extract_coverage_deficits(output, threshold=80.0)
        assert result is not None
        assert "Name" in result.table_text
        assert "module.py" in result.table_text

    def test_handles_multiple_deficits(self):
        """Test handles multiple modules with coverage deficits."""
        output = """
Name                 Stmts   Miss  Cover
-----------------------------------------
src/low1.py            100     70    30%
src/low2.py            100     60    40%
src/high.py            100     10    90%
"""
        result = extract_coverage_deficits(output, threshold=80.0)
        assert result is not None
        assert len(result.deficits) == 2
        paths = [d.path for d in result.deficits]
        assert "src/low1.py" in paths
        assert "src/low2.py" in paths
        assert "src/high.py" not in paths

    def test_preserves_coverage_values(self):
        """Test preserves exact coverage values."""
        output = """
Name                 Stmts   Miss  Cover
-----------------------------------------
module.py              100     32    67.8%
"""
        result = extract_coverage_deficits(output, threshold=70.0)
        assert result is not None
        assert result.deficits[0].coverage == 67.8

    def test_handles_complex_pytest_output(self):
        """Test handles complex pytest output with test results."""
        output = """
collected 50 items

tests/test_module.py::test_function PASSED      [ 10%]
tests/test_other.py::test_other FAILED          [ 20%]

---------- coverage: platform darwin -----------
Name                      Stmts   Miss  Cover
----------------------------------------------
ci_tools/module.py          150     75    50%
ci_tools/other.py           100     10    90%
----------------------------------------------
TOTAL                       250     85    66%

========== 49 passed, 1 failed in 5.23s ==========
"""
        result = extract_coverage_deficits(output, threshold=80.0)
        assert result is not None
        assert len(result.deficits) == 1
        assert result.deficits[0].path == "ci_tools/module.py"

    def test_uses_custom_threshold(self):
        """Test uses custom threshold value."""
        output = """
Name                 Stmts   Miss  Cover
-----------------------------------------
file.py                100     15    85%
"""
        result_high = extract_coverage_deficits(output, threshold=90.0)
        assert result_high is not None
        assert len(result_high.deficits) == 1

        result_low = extract_coverage_deficits(output, threshold=80.0)
        assert result_low is None

    def test_strips_table_text(self):
        """Test strips whitespace from table text."""
        output = """

Name                 Stmts   Miss  Cover
-----------------------------------------
file.py                100     50    50%


"""
        result = extract_coverage_deficits(output, threshold=80.0)
        assert result is not None
        assert result.table_text.startswith("Name")
        assert not result.table_text.startswith("\n")


class TestCoverageCheckResult:
    """Tests for CoverageCheckResult dataclass."""

    def test_dataclass_initialization(self):
        """Test CoverageCheckResult can be initialized."""
        result = CoverageCheckResult(
            table_text="table",
            deficits=[CoverageDeficit("file.py", 60.0)],
            threshold=80.0,
        )
        assert result.table_text == "table"
        assert len(result.deficits) == 1
        assert result.threshold == 80.0

    def test_stores_multiple_deficits(self):
        """Test can store multiple deficits."""
        deficits = [
            CoverageDeficit("file1.py", 50.0),
            CoverageDeficit("file2.py", 60.0),
            CoverageDeficit("file3.py", 70.0),
        ]
        result = CoverageCheckResult(table_text="table", deficits=deficits, threshold=80.0)
        assert len(result.deficits) == 3
        assert result.deficits[0].coverage == 50.0
        assert result.deficits[2].coverage == 70.0

    def test_empty_deficits_list(self):
        """Test can have empty deficits list."""
        result = CoverageCheckResult(table_text="table", deficits=[], threshold=80.0)
        assert len(result.deficits) == 0


class TestCoverageDeficit:
    """Tests for CoverageDeficit dataclass."""

    def test_dataclass_initialization(self):
        """Test CoverageDeficit can be initialized."""
        deficit = CoverageDeficit(path="module.py", coverage=55.5)
        assert deficit.path == "module.py"
        assert deficit.coverage == 55.5

    def test_stores_integer_coverage(self):
        """Test can store integer coverage values."""
        deficit = CoverageDeficit(path="file.py", coverage=75.0)
        assert deficit.coverage == 75.0

    def test_stores_float_coverage(self):
        """Test can store float coverage values."""
        deficit = CoverageDeficit(path="file.py", coverage=72.35)
        assert deficit.coverage == 72.35
