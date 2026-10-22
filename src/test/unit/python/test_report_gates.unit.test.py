from __future__ import annotations

from pathlib import Path


def _record(model, scope: str, language: str, *metrics):
    return model.CoverageRecord(
        scope=scope,
        language=language,
        source_path=Path("."),
        metrics=tuple(model.CoverageMetric(name, covered, total) for name, covered, total in metrics),
        detail_kind="native",
    )


def test_all_scope_gate_passes_combined_aggregate_and_warns_for_weak_subscope(load_module):
    model = load_module("tools/test/report/model.py", "model")
    gates = load_module("tools/test/report/gates.py")
    records = [
        _record(model, "unit", "python", ("statement", 90, 100), ("branch", 80, 100)),
        _record(model, "int", "python", ("statement", 90, 100), ("branch", 90, 100)),
        _record(model, "unit", "ruby", ("statement", 95, 100), ("branch", 95, 100)),
    ]

    report = gates.evaluate_coverage_gates(records, command_scope="all")

    hard = {
        (result.scope, result.language, result.metric): result
        for result in report.hard_results
    }
    assert hard[("all", "python", "statement")].severity == "pass"
    assert hard[("all", "python", "branch")].severity == "pass"
    assert hard[("all", "python", "branch")].covered == 170
    assert hard[("all", "python", "branch")].total == 200
    assert hard[("all", "ruby", "branch")].severity == "pass"
    assert report.failures == ()

    warnings = {
        (result.scope, result.language, result.metric): result
        for result in report.warnings
    }
    assert warnings[("unit", "python", "branch")].severity == "warn"
    assert warnings[("unit", "python", "branch")].display == "80 / 100 (80.00%)"


def test_scoped_gate_fails_when_scoped_aggregate_misses_threshold(load_module):
    model = load_module("tools/test/report/model.py", "model")
    gates = load_module("tools/test/report/gates.py")
    records = [
        _record(model, "unit", "python", ("statement", 90, 100), ("branch", 80, 100)),
        _record(model, "int", "python", ("statement", 100, 100), ("branch", 100, 100)),
    ]

    report = gates.evaluate_coverage_gates(records, command_scope="unit")

    failures = {
        (result.scope, result.language, result.metric): result
        for result in report.failures
    }
    assert failures[("unit", "python", "branch")].severity == "fail"
    assert failures[("unit", "python", "branch")].threshold_percent == 85.0
    assert failures[("unit", "python", "branch")].display == "80 / 100 (80.00%)"


def test_all_scope_prefers_existing_all_record_over_double_counting_subscopes(load_module):
    model = load_module("tools/test/report/model.py", "model")
    gates = load_module("tools/test/report/gates.py")
    records = [
        _record(model, "unit", "nix", ("source-target", 1, 2)),
        _record(model, "int", "nix", ("source-target", 2, 2)),
        _record(model, "all", "nix", ("source-target", 3, 4)),
    ]

    report = gates.evaluate_coverage_gates(records, command_scope="all")

    hard = {
        (result.scope, result.language, result.metric): result
        for result in report.hard_results
    }
    assert hard[("all", "nix", "source-target")].covered == 3
    assert hard[("all", "nix", "source-target")].total == 4
    assert hard[("all", "nix", "source-target")].severity == "fail"


def test_manifest_declared_source_without_coverage_becomes_warning(load_module):
    model = load_module("tools/test/report/model.py", "model")
    gates = load_module("tools/test/report/gates.py")
    records = [
        model.CoverageRecord(
            scope="unit",
            language="bash",
            source_path=Path("."),
            metrics=(model.CoverageMetric("statement", 90, 100),),
            detail_kind="native",
            covered_source_paths=(Path("bin/covered-tool"),),
        ),
    ]
    manifest_entries = [
        model.CoverageManifestEntry(
            scope="unit",
            language="bash",
            source_path=Path("bin/covered-tool"),
        ),
        model.CoverageManifestEntry(
            scope="unit",
            language="bash",
            source_path=Path("bin/missing-tool"),
        ),
    ]

    report = gates.evaluate_coverage_gates(
        records,
        command_scope="unit",
        manifest_entries=manifest_entries,
    )

    warnings = {
        (result.scope, result.language, result.metric, result.source_path): result
        for result in report.warnings
    }
    assert warnings[("unit", "bash", "manifest", Path("bin/missing-tool"))].severity == "warn"
    assert warnings[("unit", "bash", "manifest", Path("bin/missing-tool"))].display == "missing coverage"
    assert ("unit", "bash", "manifest", Path("bin/covered-tool")) not in warnings


def test_integration_bash_statement_coverage_is_not_a_gate(load_module):
    model = load_module("tools/test/report/model.py", "model")
    gates = load_module("tools/test/report/gates.py")
    records = [
        _record(model, "int", "bash", ("statement", 10, 100), ("source-target", 1, 1)),
    ]

    int_report = gates.evaluate_coverage_gates(records, command_scope="int")
    int_hard = {
        (result.scope, result.language, result.metric): result
        for result in int_report.hard_results
    }
    assert ("int", "bash", "statement") not in int_hard
    assert int_hard[("int", "bash", "source-target")].severity == "pass"
    assert int_report.warnings == ()

    all_report = gates.evaluate_coverage_gates(records, command_scope="all")
    all_hard = {
        (result.scope, result.language, result.metric): result
        for result in all_report.hard_results
    }
    assert ("all", "bash", "statement") not in all_hard
    assert all_hard[("all", "bash", "source-target")].severity == "pass"
    assert all_report.warnings == ()
