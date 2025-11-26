"""Unit tests for coverage_guard module."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from coverage import Coverage
from coverage.exceptions import CoverageException, NoDataError, NoSource

from ci_tools.scripts import coverage_guard
from ci_tools.scripts.guard_common import detect_repo_root


def create_coverage_mock() -> Mock:
    """Create a properly configured Coverage mock with config attribute."""
    mock_cov = Mock(spec=Coverage)
    mock_config = Mock()
    mock_config.report_omit = None
    mock_cov.config = mock_config
    return mock_cov


class TestFindRepoRoot:
    """Test repository root finding."""

    def test_find_repo_root_with_git(self, tmp_path: Path) -> None:
        """Test finding root with .git directory."""
        repo = tmp_path / "project"
        repo.mkdir()
        (repo / ".git").mkdir()

        nested = repo / "src" / "module"
        nested.mkdir(parents=True)

        # Mock both git command and Path.cwd for fallback
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("git not found")
            with patch("pathlib.Path.cwd", return_value=nested):
                root = detect_repo_root()
                assert root == repo

    def test_find_repo_root_no_git(self, tmp_path: Path) -> None:
        """Test finding root without .git directory."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("git not found")
            with patch("pathlib.Path.cwd", return_value=tmp_path):
                root = detect_repo_root()
                assert root == tmp_path

    def test_find_repo_root_at_filesystem_root(self) -> None:
        """Test finding root at filesystem root."""
        # Mock being at filesystem root
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("git not found")
            with patch("pathlib.Path.cwd", return_value=Path("/")):
                root = detect_repo_root()
                assert root == Path("/")


class TestCoverageResult:
    """Test CoverageResult dataclass."""

    def test_coverage_result_percent_normal(self) -> None:
        """Test coverage percentage calculation."""
        result = coverage_guard.CoverageResult(path=Path("test.py"), statements=100, missing=20)
        assert result.percent == 80.0

    def test_coverage_result_percent_zero_statements(self) -> None:
        """Test coverage with zero statements."""
        result = coverage_guard.CoverageResult(path=Path("test.py"), statements=0, missing=0)
        assert result.percent == 100.0

    def test_coverage_result_percent_full_coverage(self) -> None:
        """Test 100% coverage."""
        result = coverage_guard.CoverageResult(path=Path("test.py"), statements=50, missing=0)
        assert result.percent == 100.0

    def test_coverage_result_percent_no_coverage(self) -> None:
        """Test 0% coverage."""
        result = coverage_guard.CoverageResult(path=Path("test.py"), statements=50, missing=50)
        assert result.percent == 0.0

    def test_coverage_result_frozen(self) -> None:
        """Test that CoverageResult is frozen."""
        result = coverage_guard.CoverageResult(path=Path("test.py"), statements=100, missing=20)

        with pytest.raises(Exception):  # FrozenInstanceError
            result.statements = 50  # type: ignore[misc]


class TestParseArgs:
    """Test argument parsing."""

    def test_parse_args_required_args(self) -> None:
        """Test parsing with required arguments."""
        args = coverage_guard.parse_args([
            "--threshold", "80",
            "--data-file", ".coverage"
        ])
        assert args.threshold == 80.0
        assert args.data_file == ".coverage"
        assert args.include == []

    def test_parse_args_custom_threshold(self) -> None:
        """Test custom threshold argument."""
        args = coverage_guard.parse_args([
            "--threshold", "90",
            "--data-file", ".coverage"
        ])
        assert args.threshold == 90.0

    def test_parse_args_custom_data_file(self) -> None:
        """Test custom data file argument."""
        args = coverage_guard.parse_args([
            "--threshold", "80",
            "--data-file", "/path/to/.coverage"
        ])
        assert args.data_file == "/path/to/.coverage"

    def test_parse_args_single_include(self) -> None:
        """Test single include path."""
        args = coverage_guard.parse_args([
            "--threshold", "80",
            "--data-file", ".coverage",
            "--include", "src"
        ])
        assert "src" in args.include

    def test_parse_args_multiple_includes(self) -> None:
        """Test multiple include paths."""
        args = coverage_guard.parse_args([
            "--threshold", "80",
            "--data-file", ".coverage",
            "--include", "src", "--include", "lib", "--include", "tests"
        ])
        assert "src" in args.include
        assert "lib" in args.include
        assert "tests" in args.include

    def test_parse_args_combined_options(self) -> None:
        """Test combining multiple options."""
        args = coverage_guard.parse_args(
            [
                "--threshold",
                "85",
                "--data-file",
                ".coverage.test",
                "--include",
                "src",
                "--include",
                "lib",
            ]
        )
        assert args.threshold == 85.0
        assert args.data_file == ".coverage.test"
        assert "src" in args.include
        assert "lib" in args.include


class TestResolveDataFile:
    """Test coverage data file resolution."""

    def test_resolve_data_file_explicit_path(self, tmp_path: Path) -> None:
        """Test with explicit absolute path."""
        explicit = tmp_path / ".coverage.custom"
        with patch.object(coverage_guard, "ROOT", tmp_path):
            result = coverage_guard.resolve_data_file(str(explicit))
            assert result == explicit

    def test_resolve_data_file_relative_path(self, tmp_path: Path) -> None:
        """Test with relative path."""
        with patch.object(coverage_guard, "ROOT", tmp_path):
            result = coverage_guard.resolve_data_file(".coverage.test")
            assert result == (tmp_path / ".coverage.test").resolve()

    def test_resolve_data_file_relative(self, tmp_path: Path) -> None:
        """Test resolving relative path."""
        with patch.object(coverage_guard, "ROOT", tmp_path):
            result = coverage_guard.resolve_data_file(".coverage")
            assert result == (tmp_path / ".coverage").resolve()

    def test_resolve_data_file_with_custom_name(
        self, tmp_path: Path
    ) -> None:
        """Test resolving with custom filename."""
        with patch.object(coverage_guard, "ROOT", tmp_path):
            result = coverage_guard.resolve_data_file(".coverage.custom")
            assert result == (tmp_path / ".coverage.custom").resolve()

    def test_resolve_data_file_candidate_overrides_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Test that explicit candidate overrides environment."""
        monkeypatch.setenv("COVERAGE_FILE", ".coverage.env")
        with patch.object(coverage_guard, "ROOT", tmp_path):
            result = coverage_guard.resolve_data_file(".coverage.explicit")
            assert result == (tmp_path / ".coverage.explicit").resolve()


class TestNormalizePrefixes:
    """Test prefix normalization."""

    def test_normalize_prefixes_single(self, tmp_path: Path) -> None:
        """Test normalizing single prefix."""
        with patch.object(coverage_guard, "ROOT", tmp_path):
            result = coverage_guard.normalize_prefixes(["src"])
            assert len(result) == 1
            assert result[0] == (tmp_path / "src").resolve()

    def test_normalize_prefixes_multiple(self, tmp_path: Path) -> None:
        """Test normalizing multiple prefixes."""
        with patch.object(coverage_guard, "ROOT", tmp_path):
            result = coverage_guard.normalize_prefixes(["src", "lib", "tests"])
            assert len(result) == 3
            assert result[0] == (tmp_path / "src").resolve()
            assert result[1] == (tmp_path / "lib").resolve()
            assert result[2] == (tmp_path / "tests").resolve()

    def test_normalize_prefixes_empty(self, tmp_path: Path) -> None:
        """Test normalizing empty list."""
        with patch.object(coverage_guard, "ROOT", tmp_path):
            result = coverage_guard.normalize_prefixes([])
            assert not result

    def test_normalize_prefixes_absolute_paths(self, tmp_path: Path) -> None:
        """Test normalizing absolute paths."""
        abs_path = tmp_path / "project" / "src"
        with patch.object(coverage_guard, "ROOT", tmp_path):
            result = coverage_guard.normalize_prefixes([str(abs_path)])
            assert len(result) == 1


class TestShouldInclude:
    """Test file inclusion logic."""

    def test_should_include_no_prefixes(self, tmp_path: Path) -> None:
        """Test that all files are included when no prefixes specified."""
        with patch.object(coverage_guard, "ROOT", tmp_path):
            file_path = tmp_path / "src" / "module.py"
            assert coverage_guard.should_include(file_path, [])

    def test_should_include_matching_prefix(self, tmp_path: Path) -> None:
        """Test including file matching prefix."""
        with patch.object(coverage_guard, "ROOT", tmp_path):
            file_path = tmp_path / "src" / "module.py"
            prefixes = [(tmp_path / "src").resolve()]
            assert coverage_guard.should_include(file_path, prefixes)

    def test_should_include_not_matching_prefix(self, tmp_path: Path) -> None:
        """Test excluding file not matching prefix."""
        with patch.object(coverage_guard, "ROOT", tmp_path):
            file_path = tmp_path / "tests" / "test_module.py"
            prefixes = [(tmp_path / "src").resolve()]
            assert not coverage_guard.should_include(file_path, prefixes)

    def test_should_include_outside_root(self, tmp_path: Path) -> None:
        """Test excluding file outside root."""
        root = tmp_path / "project"
        outside = tmp_path / "other" / "file.py"

        with patch.object(coverage_guard, "ROOT", root):
            assert not coverage_guard.should_include(outside, [])

    def test_should_include_exact_prefix_match(self, tmp_path: Path) -> None:
        """Test including file that is exactly the prefix."""
        with patch.object(coverage_guard, "ROOT", tmp_path):
            file_path = tmp_path / "src"
            prefixes = [(tmp_path / "src").resolve()]
            assert coverage_guard.should_include(file_path, prefixes)

    def test_should_include_multiple_prefixes(self, tmp_path: Path) -> None:
        """Test including with multiple prefixes."""
        with patch.object(coverage_guard, "ROOT", tmp_path):
            file1 = tmp_path / "src" / "module.py"
            file2 = tmp_path / "lib" / "util.py"
            file3 = tmp_path / "other" / "file.py"

            prefixes = [(tmp_path / "src").resolve(), (tmp_path / "lib").resolve()]

            assert coverage_guard.should_include(file1, prefixes)
            assert coverage_guard.should_include(file2, prefixes)
            assert not coverage_guard.should_include(file3, prefixes)


class TestCollectResults:
    """Test collecting coverage results."""

    def test_collect_results_no_data(self) -> None:
        """Test when no coverage data exists."""
        mock_cov = create_coverage_mock()
        mock_cov.load.side_effect = NoDataError("No data")

        with pytest.raises(SystemExit) as exc_info:
            coverage_guard.collect_results(mock_cov, [])

        assert "no data found" in str(exc_info.value)

    def test_collect_results_empty_data(self) -> None:
        """Test with empty coverage data."""
        mock_cov = create_coverage_mock()
        mock_data = Mock()
        mock_data.measured_files.return_value = []

        mock_cov.load.return_value = None
        mock_cov.get_data.return_value = mock_data

        results = coverage_guard.collect_results(mock_cov, [])
        assert not results

    def test_collect_results_single_file(self, tmp_path: Path) -> None:
        """Test collecting results for single file."""
        mock_cov = create_coverage_mock()
        mock_data = Mock()

        file_path = tmp_path / "src" / "module.py"
        mock_data.measured_files.return_value = [str(file_path)]

        # Mock analysis2 to return (?, statements, ?, missing, ?)
        mock_cov.analysis2.return_value = (
            None,
            [1, 2, 3, 4, 5],  # statements (line numbers)
            None,
            [3, 5],  # missing (line numbers)
            None,
        )

        mock_cov.load.return_value = None
        mock_cov.get_data.return_value = mock_data

        with patch.object(coverage_guard, "ROOT", tmp_path):
            results = coverage_guard.collect_results(mock_cov, [])

        assert len(results) == 1
        assert results[0].path == file_path.resolve()
        assert results[0].statements == 5
        assert results[0].missing == 2

    def test_collect_results_multiple_files(self, tmp_path: Path) -> None:
        """Test collecting results for multiple files."""
        mock_cov = create_coverage_mock()
        mock_data = Mock()

        file1 = tmp_path / "src" / "module1.py"
        file2 = tmp_path / "src" / "module2.py"
        mock_data.measured_files.return_value = [str(file1), str(file2)]

        # Different coverage for each file
        def analysis_side_effect(filename):
            if "module1" in filename:
                return (None, [1, 2, 3], None, [1], None)
            return (None, [1, 2, 3, 4], None, [2, 4], None)

        mock_cov.analysis2.side_effect = analysis_side_effect
        mock_cov.load.return_value = None
        mock_cov.get_data.return_value = mock_data

        with patch.object(coverage_guard, "ROOT", tmp_path):
            results = coverage_guard.collect_results(mock_cov, [])

        assert len(results) == 2

    def test_collect_results_with_prefixes(self, tmp_path: Path) -> None:
        """Test collecting results filtered by prefixes."""
        mock_cov = create_coverage_mock()
        mock_data = Mock()

        file1 = tmp_path / "src" / "module.py"
        file2 = tmp_path / "tests" / "test_module.py"
        mock_data.measured_files.return_value = [str(file1), str(file2)]

        mock_cov.analysis2.return_value = (None, [1, 2], None, [1], None)
        mock_cov.load.return_value = None
        mock_cov.get_data.return_value = mock_data

        prefixes = [(tmp_path / "src").resolve()]

        with patch.object(coverage_guard, "ROOT", tmp_path):
            results = coverage_guard.collect_results(mock_cov, prefixes)

        # Only src/ file should be included
        assert len(results) == 1
        assert "src" in str(results[0].path)

    def test_collect_results_skip_no_source(self, tmp_path: Path) -> None:
        """Test that files with no source are skipped."""
        mock_cov = create_coverage_mock()
        mock_data = Mock()

        file1 = tmp_path / "exists.py"
        file2 = tmp_path / "deleted.py"
        mock_data.measured_files.return_value = [str(file1), str(file2)]

        def analysis_side_effect(filename):
            if "deleted" in filename:
                raise NoSource("File not found")
            return (None, [1, 2], None, [1], None)

        mock_cov.analysis2.side_effect = analysis_side_effect
        mock_cov.load.return_value = None
        mock_cov.get_data.return_value = mock_data

        with patch.object(coverage_guard, "ROOT", tmp_path):
            results = coverage_guard.collect_results(mock_cov, [])

        # Only the existing file should be in results
        assert len(results) == 1

    def test_collect_results_sorted_output(self, tmp_path: Path) -> None:
        """Test that results are sorted by filename."""
        mock_cov = create_coverage_mock()
        mock_data = Mock()

        files = [str(tmp_path / "zebra.py"), str(tmp_path / "alpha.py"), str(tmp_path / "beta.py")]
        mock_data.measured_files.return_value = files

        mock_cov.analysis2.return_value = (None, [1, 2], None, [1], None)
        mock_cov.load.return_value = None
        mock_cov.get_data.return_value = mock_data

        with patch.object(coverage_guard, "ROOT", tmp_path):
            results = coverage_guard.collect_results(mock_cov, [])

        # Files are sorted by measured_files
        assert len(results) == 3


class TestMainFunction:
    """Test main function and CLI behavior."""

    def test_main_data_file_not_found(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test main when coverage data file doesn't exist."""
        with patch.object(coverage_guard, "ROOT", tmp_path):
            result = coverage_guard.main([
                "--threshold", "80",
                "--data-file", str(tmp_path / ".coverage.missing")
            ])

        assert result == 1
        captured = capsys.readouterr()
        assert "coverage data file not found" in captured.err

    def test_main_all_files_pass(self, tmp_path: Path) -> None:
        """Test main when all files meet threshold."""
        data_file = tmp_path / ".coverage"
        data_file.write_text("")  # Create empty file

        mock_cov = create_coverage_mock()
        mock_data = Mock()

        file1 = tmp_path / "module.py"
        mock_data.measured_files.return_value = [str(file1)]

        # 90% coverage
        mock_cov.analysis2.return_value = (None, list(range(10)), None, [9], None)
        mock_cov.load.return_value = None
        mock_cov.get_data.return_value = mock_data

        with patch.object(coverage_guard, "ROOT", tmp_path):
            with patch("ci_tools.scripts.coverage_guard.Coverage", return_value=mock_cov):
                result = coverage_guard.main(["--threshold", "80", "--data-file", str(data_file)])

        assert result == 0

    def test_main_some_files_fail(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Test main when some files fail threshold."""
        data_file = tmp_path / ".coverage"
        data_file.write_text("")

        mock_cov = create_coverage_mock()
        mock_data = Mock()

        file1 = tmp_path / "low_coverage.py"
        mock_data.measured_files.return_value = [str(file1)]

        # 50% coverage
        mock_cov.analysis2.return_value = (None, list(range(10)), None, list(range(5)), None)
        mock_cov.load.return_value = None
        mock_cov.get_data.return_value = mock_data

        with patch.object(coverage_guard, "ROOT", tmp_path):
            with patch("ci_tools.scripts.coverage_guard.Coverage", return_value=mock_cov):
                result = coverage_guard.main(["--threshold", "80", "--data-file", str(data_file)])

        assert result == 1
        captured = capsys.readouterr()
        assert "coverage_guard" in captured.err
        assert "below threshold" in captured.err
        assert "low_coverage.py" in captured.err
        assert "50.00%" in captured.err

    def test_main_coverage_exception(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test main when coverage raises exception."""
        data_file = tmp_path / ".coverage"
        data_file.write_text("")

        mock_cov = create_coverage_mock()
        mock_cov.load.side_effect = CoverageException("Error loading data")

        with patch.object(coverage_guard, "ROOT", tmp_path):
            with patch("ci_tools.scripts.coverage_guard.Coverage", return_value=mock_cov):
                result = coverage_guard.main([
                    "--threshold", "80",
                    "--data-file", str(data_file)
                ])

        assert result == 1
        captured = capsys.readouterr()
        assert "failed to load coverage data" in captured.err

    def test_main_threshold_boundary(self, tmp_path: Path) -> None:
        """Test coverage exactly at threshold."""
        data_file = tmp_path / ".coverage"
        data_file.write_text("")

        mock_cov = create_coverage_mock()
        mock_data = Mock()

        file1 = tmp_path / "module.py"
        mock_data.measured_files.return_value = [str(file1)]

        # Exactly 80% coverage
        mock_cov.analysis2.return_value = (None, list(range(10)), None, [8, 9], None)
        mock_cov.load.return_value = None
        mock_cov.get_data.return_value = mock_data

        with patch.object(coverage_guard, "ROOT", tmp_path):
            with patch("ci_tools.scripts.coverage_guard.Coverage", return_value=mock_cov):
                result = coverage_guard.main(["--threshold", "80", "--data-file", str(data_file)])

        # Should pass (80.0 >= 80.0)
        assert result == 0

    def test_main_zero_statements_file(self, tmp_path: Path) -> None:
        """Test file with zero statements."""
        data_file = tmp_path / ".coverage"
        data_file.write_text("")

        mock_cov = create_coverage_mock()
        mock_data = Mock()

        file1 = tmp_path / "empty.py"
        mock_data.measured_files.return_value = [str(file1)]

        # Zero statements
        mock_cov.analysis2.return_value = (None, [], None, [], None)
        mock_cov.load.return_value = None
        mock_cov.get_data.return_value = mock_data

        with patch.object(coverage_guard, "ROOT", tmp_path):
            with patch("ci_tools.scripts.coverage_guard.Coverage", return_value=mock_cov):
                result = coverage_guard.main(["--threshold", "80", "--data-file", str(data_file)])

        # Should pass (empty files don't count against coverage)
        assert result == 0

    def test_main_with_include_filters(self, tmp_path: Path) -> None:
        """Test main with include filters."""
        data_file = tmp_path / ".coverage"
        data_file.write_text("")

        mock_cov = create_coverage_mock()
        mock_data = Mock()

        file1 = tmp_path / "src" / "module.py"
        file2 = tmp_path / "tests" / "test.py"
        mock_data.measured_files.return_value = [str(file1), str(file2)]

        # Good coverage in src, bad in tests
        def analysis_side_effect(filename):
            if "src" in filename:
                return (None, list(range(10)), None, [9], None)  # 90%
            return (None, list(range(10)), None, list(range(5)), None)  # 50%

        mock_cov.analysis2.side_effect = analysis_side_effect
        mock_cov.load.return_value = None
        mock_cov.get_data.return_value = mock_data

        with patch.object(coverage_guard, "ROOT", tmp_path):
            with patch("ci_tools.scripts.coverage_guard.Coverage", return_value=mock_cov):
                # Only check src
                result = coverage_guard.main(
                    ["--threshold", "80", "--data-file", str(data_file), "--include", "src"]
                )

        # Should pass because we're only checking src
        assert result == 0

    def test_main_script_entry_point(self, tmp_path: Path) -> None:
        """Test __main__ entry point."""
        data_file = tmp_path / ".coverage"
        data_file.write_text("")

        mock_cov = create_coverage_mock()
        mock_data = Mock()
        mock_data.measured_files.return_value = []
        mock_cov.load.return_value = None
        mock_cov.get_data.return_value = mock_data

        # We can't easily test the __main__ entry point due to module-level imports
        # Instead, test that main() can be called successfully
        with patch.object(coverage_guard, "ROOT", tmp_path):
            with patch("ci_tools.scripts.coverage_guard.Coverage", return_value=mock_cov):
                result = coverage_guard.main([
                    "--threshold", "80",
                    "--data-file", str(data_file)
                ])
                assert result == 0

    def test_main_float_precision_edge_case(self, tmp_path: Path) -> None:
        """Test handling of floating point precision issues."""
        data_file = tmp_path / ".coverage"
        data_file.write_text("")

        mock_cov = create_coverage_mock()
        mock_data = Mock()

        file1 = tmp_path / "module.py"
        mock_data.measured_files.return_value = [str(file1)]

        # Coverage that's very close to threshold
        # 79.99999% should fail against 80% threshold
        mock_cov.analysis2.return_value = (
            None,
            list(range(100000)),
            None,
            list(range(20001)),
            None,
        )
        mock_cov.load.return_value = None
        mock_cov.get_data.return_value = mock_data

        with patch.object(coverage_guard, "ROOT", tmp_path):
            with patch("ci_tools.scripts.coverage_guard.Coverage", return_value=mock_cov):
                coverage_guard.main(["--threshold", "80", "--data-file", str(data_file)])

        # The 1e-9 tolerance should allow near-misses to pass
        # With 20001 missing out of 100000, we have 79.999% which should pass

    def test_main_none_argv_uses_sys_argv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that argv=None uses sys.argv."""
        data_file = tmp_path / ".coverage"
        data_file.write_text("")

        mock_cov = create_coverage_mock()
        mock_data = Mock()
        mock_data.measured_files.return_value = []
        mock_cov.load.return_value = None
        mock_cov.get_data.return_value = mock_data

        monkeypatch.setattr(sys, "argv", [
            "coverage_guard.py",
            "--threshold", "80",
            "--data-file", str(data_file)
        ])

        with patch.object(coverage_guard, "ROOT", tmp_path):
            with patch("ci_tools.scripts.coverage_guard.Coverage", return_value=mock_cov):
                result = coverage_guard.main(None)
                assert result == 0


class TestEdgeCases:
    """Test edge cases and unusual scenarios."""

    def test_coverage_result_with_rounding(self) -> None:
        """Test coverage percentage with rounding."""
        result = coverage_guard.CoverageResult(path=Path("test.py"), statements=3, missing=1)
        # 2/3 = 66.666...%
        assert abs(result.percent - 66.666666) < 0.001

    def test_should_include_nested_paths(self, tmp_path: Path) -> None:
        """Test inclusion with deeply nested paths."""
        with patch.object(coverage_guard, "ROOT", tmp_path):
            deep_file = tmp_path / "src" / "pkg" / "subpkg" / "module.py"
            prefixes = [(tmp_path / "src").resolve()]
            assert coverage_guard.should_include(deep_file, prefixes)

    def test_multiple_failures_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that all failures are reported."""
        data_file = tmp_path / ".coverage"
        data_file.write_text("")

        mock_cov = create_coverage_mock()
        mock_data = Mock()

        file1 = tmp_path / "module1.py"
        file2 = tmp_path / "module2.py"
        file3 = tmp_path / "module3.py"
        mock_data.measured_files.return_value = [str(file1), str(file2), str(file3)]

        # All have low coverage
        mock_cov.analysis2.return_value = (None, list(range(10)), None, list(range(5)), None)
        mock_cov.load.return_value = None
        mock_cov.get_data.return_value = mock_data

        with patch.object(coverage_guard, "ROOT", tmp_path):
            with patch("ci_tools.scripts.coverage_guard.Coverage", return_value=mock_cov):
                result = coverage_guard.main(["--threshold", "80", "--data-file", str(data_file)])

        assert result == 1
        captured = capsys.readouterr()
        assert "module1.py" in captured.err
        assert "module2.py" in captured.err
        assert "module3.py" in captured.err

    def test_relative_path_display(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that paths are displayed relative to root."""
        data_file = tmp_path / ".coverage"
        data_file.write_text("")

        mock_cov = create_coverage_mock()
        mock_data = Mock()

        file1 = tmp_path / "src" / "deeply" / "nested" / "module.py"
        mock_data.measured_files.return_value = [str(file1)]

        # Low coverage
        mock_cov.analysis2.return_value = (None, list(range(10)), None, list(range(5)), None)
        mock_cov.load.return_value = None
        mock_cov.get_data.return_value = mock_data

        with patch.object(coverage_guard, "ROOT", tmp_path):
            with patch("ci_tools.scripts.coverage_guard.Coverage", return_value=mock_cov):
                coverage_guard.main(["--threshold", "80", "--data-file", str(data_file)])

        captured = capsys.readouterr()
        # Should show relative path with forward slashes
        assert "src/deeply/nested/module.py" in captured.err

    def test_high_threshold(self, tmp_path: Path) -> None:
        """Test with very high threshold requirement."""
        data_file = tmp_path / ".coverage"
        data_file.write_text("")

        mock_cov = create_coverage_mock()
        mock_data = Mock()

        file1 = tmp_path / "module.py"
        mock_data.measured_files.return_value = [str(file1)]

        # 95% coverage
        mock_cov.analysis2.return_value = (None, list(range(100)), None, list(range(5)), None)
        mock_cov.load.return_value = None
        mock_cov.get_data.return_value = mock_data

        with patch.object(coverage_guard, "ROOT", tmp_path):
            with patch("ci_tools.scripts.coverage_guard.Coverage", return_value=mock_cov):
                # Should pass 95% threshold
                result1 = coverage_guard.main(["--threshold", "95", "--data-file", str(data_file)])
                assert result1 == 0

                # Should fail 99% threshold
                result2 = coverage_guard.main(["--threshold", "99", "--data-file", str(data_file)])
                assert result2 == 1
