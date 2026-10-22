from __future__ import annotations

from pathlib import Path


def test_tree_node_aggregates_test_counts(load_module):
    model = load_module("tools/test/report/model.py", "model")
    root = model.TreeNode("nix-darwin", Path("nix-darwin"))
    child = root.child("src").child("test").child("unit").child("python")
    child.tests.extend(
        [
            model.TestCaseResult(
                scope="unit",
                language="python",
                test_path=Path("src/test/unit/python/example.unit.test.py"),
                module="example",
                name="test_passes",
                status="passed",
                time=0.01,
            ),
            model.TestCaseResult(
                scope="unit",
                language="python",
                test_path=Path("src/test/unit/python/example.unit.test.py"),
                module="example",
                name="test_fails",
                status="failed",
                time=0.02,
            ),
        ]
    )

    root.recalculate_totals()

    assert root.test_totals.total == 2
    assert root.test_totals.passed == 1
    assert root.test_totals.failed == 1
    assert root.test_totals.skipped == 0


def test_tree_node_recalculate_clears_stale_leaf_totals(load_module):
    model = load_module("tools/test/report/model.py", "model")
    node = model.TreeNode("python", Path("src/test/unit/python"))
    node.test_totals.total = 3
    node.test_totals.passed = 2
    node.test_totals.failed = 1

    node.recalculate_totals()

    assert node.test_totals.total == 0
    assert node.test_totals.passed == 0
    assert node.test_totals.failed == 0
    assert node.test_totals.skipped == 0


def test_non_passed_or_skipped_test_status_counts_as_failed(load_module):
    model = load_module("tools/test/report/model.py", "model")
    test = model.TestCaseResult(
        scope="unit",
        language="python",
        test_path=Path("src/test/unit/python/example.unit.test.py"),
        module="example",
        name="test_errors",
        status="error",
        time=0.03,
    )

    assert test.totals.total == 1
    assert test.totals.passed == 0
    assert test.totals.failed == 1
    assert test.totals.skipped == 0


def test_metric_formats_na_and_percentages(load_module):
    model = load_module("tools/test/report/model.py", "model")

    assert model.CoverageMetric("statement", None, None).display == "n/a"
    assert model.CoverageMetric("branch", 88, 100).display == "88 / 100 (88%)"
    assert model.CoverageMetric("branch", 0, 0).display == "0 / 0 (n/a)"


def test_language_run_overview_records_split_runner_output(load_module, tmp_path: Path):
    model = load_module("tools/test/report/model.py", "model")
    output_log = tmp_path / "unit" / "python" / "output.txt"

    overview = model.LanguageRunOverview(
        scope="unit",
        language="python",
        output_log=output_log,
        test_output="pytest progress",
        coverage_output="coverage table",
    )

    assert overview.scope == "unit"
    assert overview.language == "python"
    assert overview.output_log == output_log
    assert overview.test_output == "pytest progress"
    assert overview.coverage_output == "coverage table"


def test_language_run_overview_can_hold_multiple_output_sections(load_module, tmp_path: Path):
    model = load_module("tools/test/report/model.py", "model")
    alpha_log = tmp_path / "unit" / "bash" / "alpha.unit.test.bats" / "output.txt"
    beta_log = tmp_path / "unit" / "bash" / "beta.unit.test.bats" / "output.txt"

    overview = model.LanguageRunOverview(
        scope="unit",
        language="bash",
        sections=(
            model.RunOverviewSection("alpha.unit.test.bats", alpha_log, "ok 1 alpha"),
            model.RunOverviewSection("beta.unit.test.bats", beta_log, "ok 1 beta"),
        ),
    )

    assert overview.output_log is None
    assert overview.test_output == ""
    assert [section.title for section in overview.sections] == [
        "alpha.unit.test.bats",
        "beta.unit.test.bats",
    ]
