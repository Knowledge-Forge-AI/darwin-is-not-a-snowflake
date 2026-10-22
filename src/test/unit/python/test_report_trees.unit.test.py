from __future__ import annotations

import sys
import types
from pathlib import Path


def test_build_test_tree_aggregates_by_test_path(load_module):
    model = load_module("tools/test/report/model.py", "model")
    trees = load_module("tools/test/report/trees.py")
    tests = [
        model.TestCaseResult(
            "unit",
            "python",
            Path("src/test/unit/python/a.unit.test.py"),
            "a",
            "test_pass",
            "passed",
            0.1,
        ),
        model.TestCaseResult(
            "unit",
            "python",
            Path("src/test/unit/python/a.unit.test.py"),
            "a",
            "test_fail",
            "failed",
            0.2,
        ),
        model.TestCaseResult(
            "unit",
            "bash",
            Path("src/test/unit/bash/b.unit.test.bats"),
            "b",
            "test_skip",
            "skipped",
            0.0,
        ),
    ]

    root = trees.build_test_tree(tests)
    python_leaf = (
        root.children["src"]
        .children["test"]
        .children["unit"]
        .children["python"]
        .children["a.unit.test.py"]
    )
    bash_leaf = (
        root.children["src"]
        .children["test"]
        .children["unit"]
        .children["bash"]
        .children["b.unit.test.bats"]
    )

    assert root.path == Path("nix-darwin")
    assert root.test_totals.total == 3
    assert root.test_totals.passed == 1
    assert root.test_totals.failed == 1
    assert root.test_totals.skipped == 1
    assert python_leaf.tests == tests[:2]
    assert bash_leaf.tests == tests[2:]


def test_build_coverage_tree_hides_untested_files(load_module):
    model = load_module("tools/test/report/model.py", "model")
    trees = load_module("tools/test/report/trees.py")
    records = [
        model.CoverageRecord(
            scope="all",
            language="nix",
            source_path=Path("modules/kitty.nix"),
            metrics=(model.CoverageMetric("source-target", 1, 1),),
            detail_kind="source-target",
        ),
    ]

    root = trees.build_coverage_tree(records)

    assert "modules" in root.children
    assert "kitty.nix" in root.children["modules"].children
    assert "untested.nix" not in root.children["modules"].children
    assert root.children["modules"].children["kitty.nix"].coverage_records == records


def test_trees_use_report_model_when_generic_model_module_exists(load_module, monkeypatch, tmp_path: Path):
    fake_model = types.ModuleType("model")
    fake_model.TestCaseResult = object
    fake_model.CoverageRecord = object
    monkeypatch.setitem(sys.modules, "model", fake_model)
    monkeypatch.delitem(sys.modules, "_nix_darwin_test_report_model", raising=False)

    parsers = load_module("tools/test/report/parsers.py")
    trees = load_module("tools/test/report/trees.py")

    assert parsers.TestCaseResult is trees.TestCaseResult
    assert parsers.CoverageRecord is trees.CoverageRecord
    assert trees.TestCaseResult is not fake_model.TestCaseResult
    assert trees.CoverageRecord is not fake_model.CoverageRecord
    assert sys.modules["model"] is fake_model

    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite>
  <testcase classname="src.test.unit.python.a.unit.test" name="test_pass" time="0.1" />
</testsuite>
""",
        encoding="utf-8",
    )
    coverage = tmp_path / "source-target-coverage.tsv"
    coverage.write_text("target\tstatus\nmodules/kitty.nix\tcovered\n", encoding="utf-8")

    tests = parsers.parse_junit_file("unit", "python", junit)
    records = parsers.parse_declared_target_coverage("all", "nix", coverage, metric_name="source-target")

    test_root = trees.build_test_tree(tests)
    coverage_root = trees.build_coverage_tree(records)

    assert (
        test_root.children["src"]
        .children["test"]
        .children["unit"]
        .children["python"]
        .children["a.unit.test.py"]
        .tests
        == tests
    )
    assert coverage_root.children["modules"].children["kitty.nix"].coverage_records == records
