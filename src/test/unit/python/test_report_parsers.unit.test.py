from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest


def write_runner_files(report_dir: Path, *, execution: str | None = "fail\t73") -> None:
    runner = report_dir / "runner"
    output = runner / "001-flake-check" / "output.txt"
    output.parent.mkdir(parents=True)
    output.write_text("flake diagnostic\n", encoding="utf-8")
    (runner / "steps.tsv").write_text(
        "ordinal\tlabel\tstatus\texit_code\toutput_log\n"
        "1\tflake check\tfail\t73\trunner/001-flake-check/output.txt\n",
        encoding="utf-8",
    )
    (runner / "failures.tsv").write_text(
        "sequence\tlabel\texit_code\n1\tflake check\t73\n",
        encoding="utf-8",
    )
    if execution is not None:
        (runner / "execution.tsv").write_text(
            f"status\texit_code\n{execution}\n",
            encoding="utf-8",
        )


def test_parse_runner_report_supports_failure_legacy_and_incomplete(load_module, tmp_path: Path):
    parsers = load_module("tools/test/report/parsers.py")
    legacy = parsers.parse_runner_report(tmp_path / "legacy")
    assert legacy.execution.status == "unknown"
    assert legacy.execution.detail == "not recorded"

    report_dir = tmp_path / "failed"
    write_runner_files(report_dir)
    parsed = parsers.parse_runner_report(report_dir)
    assert parsed.execution.status == "fail"
    assert parsed.execution.exit_code == 73
    assert parsed.steps[0].output_log == Path("runner/001-flake-check/output.txt")
    assert parsed.failures[0].label == "flake check"

    incomplete_dir = tmp_path / "incomplete"
    write_runner_files(incomplete_dir, execution=None)
    incomplete = parsers.parse_runner_report(incomplete_dir)
    assert incomplete.execution.status == "unknown"
    assert incomplete.execution.detail == "incomplete"


def test_parse_runner_report_rejects_escaping_output_path(load_module, tmp_path: Path):
    parsers = load_module("tools/test/report/parsers.py")
    report_dir = tmp_path / "report"
    write_runner_files(report_dir)
    steps = report_dir / "runner" / "steps.tsv"
    steps.write_text(
        "ordinal\tlabel\tstatus\texit_code\toutput_log\n1\tflake check\tfail\t73\t../secret\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="report-relative"):
        parsers.parse_runner_report(report_dir)


def test_parse_runner_report_supports_passing_execution(load_module, tmp_path: Path):
    parsers = load_module("tools/test/report/parsers.py")
    runner = tmp_path / "runner"
    output = runner / "001-patch-hygiene" / "output.txt"
    output.parent.mkdir(parents=True)
    output.write_text("clean\n", encoding="utf-8")
    (runner / "steps.tsv").write_text(
        "ordinal\tlabel\tstatus\texit_code\toutput_log\n"
        "1\tpatch hygiene\tpass\t0\trunner/001-patch-hygiene/output.txt\n",
        encoding="utf-8",
    )
    (runner / "failures.tsv").write_text("sequence\tlabel\texit_code\n", encoding="utf-8")
    (runner / "execution.tsv").write_text("status\texit_code\npass\t0\n", encoding="utf-8")

    parsed = parsers.parse_runner_report(tmp_path)
    assert parsed.execution.status == "pass"
    assert parsed.steps[0].status == "pass"
    assert parsed.failures == ()


def test_parse_runner_report_rejects_malformed_or_inconsistent_execution(load_module, tmp_path: Path):
    parsers = load_module("tools/test/report/parsers.py")

    execution_only = tmp_path / "execution-only" / "runner"
    execution_only.mkdir(parents=True)
    (execution_only / "execution.tsv").write_text("status\texit_code\npass\t0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="without runner steps"):
        parsers.parse_runner_report(execution_only.parent)

    missing_failures = tmp_path / "missing-failures"
    write_runner_files(missing_failures)
    (missing_failures / "runner" / "failures.tsv").unlink()
    with pytest.raises(ValueError, match="missing failures.tsv"):
        parsers.parse_runner_report(missing_failures)

    missing_attribution = tmp_path / "missing-attribution"
    write_runner_files(missing_attribution)
    (missing_attribution / "runner" / "failures.tsv").write_text(
        "sequence\tlabel\texit_code\n1\tunit/python/pytest\t73\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing failed runner-step attribution"):
        parsers.parse_runner_report(missing_attribution)

    pass_with_failure = tmp_path / "pass-with-failure"
    write_runner_files(pass_with_failure, execution="pass\t0")
    with pytest.raises(ValueError, match="passing execution has recorded failures"):
        parsers.parse_runner_report(pass_with_failure)

    missing_failure = tmp_path / "missing-failure"
    write_runner_files(missing_failure)
    runner = missing_failure / "runner"
    (runner / "steps.tsv").write_text(
        "ordinal\tlabel\tstatus\texit_code\toutput_log\n",
        encoding="utf-8",
    )
    (runner / "failures.tsv").write_text("sequence\tlabel\texit_code\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no attributed failure"):
        parsers.parse_runner_report(missing_failure)

    wrong_first_exit = tmp_path / "wrong-first-exit"
    write_runner_files(wrong_first_exit)
    (wrong_first_exit / "runner" / "failures.tsv").write_text(
        "sequence\tlabel\texit_code\n"
        "1\tunit/python/pytest\t7\n"
        "2\tflake check\t73\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not match the first failure"):
        parsers.parse_runner_report(wrong_first_exit)


def test_parse_coverage_override_is_narrow_and_rejects_duplicates(load_module, tmp_path: Path):
    parsers = load_module("tools/test/report/parsers.py")
    summary = tmp_path / "summary.tsv"
    summary.write_text(
        "scope\tlanguage\tmetric\tvalue\n"
        "all\tpython\tcoverage.gate.override\tused\n"
        "all\tcoverage\tcoverage.gate.override\tused\n",
        encoding="utf-8",
    )
    assert parsers.parse_coverage_override(summary, "all")
    summary.write_text(
        "all\tcoverage\tcoverage.gate.override\tused\n"
        "all\tcoverage\tcoverage.gate.override\tused\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        parsers.parse_coverage_override(summary, "all")
    summary.write_text("all\tcoverage\tcoverage.gate.override\tnope\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid coverage override row"):
        parsers.parse_coverage_override(summary, "all")


def test_parser_loads_sibling_model_when_generic_model_module_exists(load_module, monkeypatch):
    fake_model = types.ModuleType("model")
    fake_model.TestCaseResult = object
    monkeypatch.setitem(sys.modules, "model", fake_model)

    parsers = load_module("tools/test/report/parsers.py")

    assert parsers.TestCaseResult is not fake_model.TestCaseResult
    assert sys.modules["model"] is fake_model


def test_parse_pytest_junit_maps_classname_to_test_path(load_module, tmp_path: Path):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")
    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite>
  <testcase classname="src.test.unit.python.test_report_renderer.unit.test" name="test_html" time="0.1">
    <system-out>stdout text</system-out>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )

    results = parsers.parse_junit_file("unit", "python", junit)

    assert len(results) == 1
    assert results[0].test_path == Path("src/test/unit/python/test_report_renderer.unit.test.py")
    assert results[0].status == "passed"
    assert results[0].stdout == "stdout text"
    assert results[0].tested_stdout == "stdout text"


def test_parse_junit_strips_xml_forbidden_control_characters(load_module, tmp_path: Path):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")
    junit = tmp_path / "report.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite>
  <testcase classname="bell.unit.test.bats" name="rings bell" time="0.1">
    <system-out>before\u0007after</system-out>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )

    results = parsers.parse_junit_file("unit", "bash", junit)

    assert len(results) == 1
    assert results[0].stdout == "beforeafter"


def test_parse_pytest_junit_splits_capture_sections_into_diagnostic_channels(
    load_module,
    tmp_path: Path,
):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")
    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite>
  <testcase classname="src.test.unit.python.cloud_capture_sync.unit.test" name="test_sync" time="0.1">
    <system-out>--------------------------------- Captured Log ---------------------------------
log line

--------------------------------- Captured Out ---------------------------------
tool stdout
</system-out>
    <system-err>--------------------------------- Captured Err ---------------------------------
tool stderr
</system-err>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )

    results = parsers.parse_junit_file("unit", "python", junit)

    assert len(results) == 1
    assert results[0].assertion_stdout == "log line"
    assert results[0].assertion_stderr == ""
    assert results[0].stdout == "tool stdout"
    assert results[0].stderr == "tool stderr"
    assert results[0].tested_stdout == "tool stdout"
    assert results[0].tested_stderr == "tool stderr"


def test_parse_pytest_junit_discards_empty_capture_section_wrappers(
    load_module,
    tmp_path: Path,
):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")
    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite>
  <testcase classname="src.test.unit.python.test_report_pages.unit.test" name="test_clean" time="0.1">
    <system-out>--------------------------------- Captured Log ---------------------------------

--------------------------------- Captured Out ---------------------------------

</system-out>
    <system-err>--------------------------------- Captured Err ---------------------------------

</system-err>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )

    results = parsers.parse_junit_file("unit", "python", junit)

    assert len(results) == 1
    assert results[0].assertion_stdout == ""
    assert results[0].stdout == ""
    assert results[0].stderr == ""
    assert results[0].tested_stdout == ""
    assert results[0].tested_stderr == ""


def test_parse_pytest_json_report_associates_output_with_test_case(load_module, tmp_path: Path):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "tests": [
                    {
                        "nodeid": "src/test/unit/python/example.unit.test.py::test_json_report_output",
                        "lineno": 4,
                        "outcome": "passed",
                        "setup": {"duration": 0.001, "outcome": "passed"},
                        "call": {
                            "duration": 0.012,
                            "outcome": "passed",
                            "stdout": "unit stdout\n",
                            "stderr": "unit stderr\n",
                            "log": "log line\n",
                        },
                        "teardown": {"duration": 0.002, "outcome": "passed"},
                    },
                ],
            },
        ),
        encoding="utf-8",
    )

    results = parsers.parse_pytest_json_report_file("unit", report)

    assert len(results) == 1
    result = results[0]
    assert result.test_path == Path("src/test/unit/python/example.unit.test.py")
    assert result.module == "src.test.unit.python.example.unit.test"
    assert result.name == "test_json_report_output"
    assert result.status == "passed"
    assert result.source_line == 5
    assert result.stdout == "unit stdout"
    assert result.stderr == "unit stderr"
    assert result.assertion_stdout == "log line"
    assert result.report_phases == (
        {"phase": "setup", "outcome": "passed", "duration": "0.001s"},
        {"phase": "call", "outcome": "passed", "duration": "0.012s"},
        {"phase": "teardown", "outcome": "passed", "duration": "0.002s"},
    )


def test_parse_pytest_json_report_preserves_failure_longrepr(load_module, tmp_path: Path):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "tests": [
                    {
                        "nodeid": "src/test/unit/python/example.unit.test.py::test_failure_detail",
                        "lineno": 6,
                        "outcome": "failed",
                        "setup": {"duration": 0.001, "outcome": "passed"},
                        "call": {
                            "duration": 0.003,
                            "outcome": "failed",
                            "crash": {
                                "path": "src/test/unit/python/example.unit.test.py",
                                "lineno": 7,
                                "message": "AssertionError: expected useful detail",
                            },
                            "traceback": [
                                {
                                    "path": "src/test/unit/python/example.unit.test.py",
                                    "lineno": 7,
                                    "message": "AssertionError",
                                },
                            ],
                            "stdout": "before failure\n",
                            "longrepr": "assert 1 == 2\nE assert 1 == 2",
                        },
                    },
                ],
            },
        ),
        encoding="utf-8",
    )

    results = parsers.parse_pytest_json_report_file("unit", report)

    assert len(results) == 1
    result = results[0]
    assert result.status == "failed"
    assert result.failure_message == "AssertionError: expected useful detail"
    assert "assert 1 == 2" in result.failure
    assert result.stdout == "before failure"
    assert result.report_phases[-1] == {
        "phase": "call",
        "outcome": "failed",
        "duration": "0.003s",
        "message": "AssertionError: expected useful detail",
    }


def test_split_pytest_runner_output_moves_coverage_to_coverage_overview(load_module):
    parsers = load_module("tools/test/report/parsers.py")
    raw_output = "\n".join(
        [
            "============================= test session starts ==============================",
            "collected 2 items",
            "",
            "src/test/unit/python/example.unit.test.py ..                         [100%]",
            "",
            "================================ tests coverage ================================",
            "Name                                      Stmts   Miss Branch BrPart  Cover",
            "--------------------------------------------------------------------------",
            "tools/test/report/render.py                 42      2     10      1    92%",
            "Coverage HTML written to dir .test-reports/unit/latest/unit/python/html",
            "Coverage XML written to file .test-reports/unit/latest/unit/python/coverage.xml",
            "============================== 2 passed in 0.42s ===============================",
        ],
    )

    test_output, coverage_output = parsers.split_pytest_runner_output(raw_output)

    assert "test session starts" in test_output
    assert "collected 2 items" in test_output
    assert "2 passed in 0.42s" in test_output
    assert "tests coverage" not in test_output
    assert "Name                                      Stmts" in coverage_output
    assert "Coverage XML written" in coverage_output
    assert "2 passed in 0.42s" not in coverage_output


def test_split_pytest_runner_output_keeps_output_without_coverage_as_test_output(load_module):
    parsers = load_module("tools/test/report/parsers.py")
    raw_output = "collected 1 item\nexample.py . [100%]\n1 passed in 0.01s\n"

    test_output, coverage_output = parsers.split_pytest_runner_output(raw_output)

    assert test_output == raw_output
    assert coverage_output == ""


def test_split_ruby_runner_output_moves_simplecov_footer_to_coverage_overview(load_module):
    parsers = load_module("tools/test/report/parsers.py")
    raw_output = "\n".join(
        [
            "Run options: --seed 23035",
            "",
            "# Running:",
            "",
            "...........",
            "",
            "Finished in 0.007061s, 1557.8530 runs/s, 9488.7410 assertions/s.",
            "",
            "11 runs, 67 assertions, 0 failures, 0 errors, 0 skips",
            "Coverage report generated for Unit Tests to .test-reports/unit/latest/unit/ruby.",
            "Line Coverage: 98.86% (87 / 88)",
            "Branch Coverage: 90.48% (19 / 21)",
        ],
    )

    test_output, coverage_output = parsers.split_ruby_runner_output(raw_output)

    assert "Run options" in test_output
    assert "11 runs, 67 assertions" in test_output
    assert "Coverage report generated" not in test_output
    assert "Coverage report generated for Unit Tests" in coverage_output
    assert "Line Coverage: 98.86%" in coverage_output
    assert "11 runs, 67 assertions" not in coverage_output


def test_split_ruby_runner_output_keeps_output_without_coverage_as_test_output(load_module):
    parsers = load_module("tools/test/report/parsers.py")
    raw_output = "Run options: --seed 1\n.\n1 runs, 1 assertions, 0 failures, 0 errors, 0 skips\n"

    test_output, coverage_output = parsers.split_ruby_runner_output(raw_output)

    assert test_output == raw_output
    assert coverage_output == ""


def test_parse_junit_preserves_suite_level_console_output(load_module, tmp_path: Path):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")
    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="ruby-like-suite">
    <testcase classname="src.test.unit.python.output_capture.unit.test" name="test_from_suite_output" time="0.1" />
    <system-out>suite stdout line 1
suite stdout line 2</system-out>
    <system-err>suite stderr line</system-err>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )

    results = parsers.parse_junit_file("unit", "python", junit)

    assert len(results) == 1
    assert results[0].stdout == "suite stdout line 1\nsuite stdout line 2"
    assert results[0].stderr == "suite stderr line"
    assert results[0].tested_stdout == "suite stdout line 1\nsuite stdout line 2"
    assert results[0].tested_stderr == "suite stderr line"


def test_parse_bats_junit_uses_report_directory_name(load_module, tmp_path: Path):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")
    report = tmp_path / "unit" / "bash" / "shell-config.unit.test.bats" / "report.xml"
    report.parent.mkdir(parents=True)
    report.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite>
  <testcase classname="shell-config.unit.test.bats" name="loads shell config" time="0.2" />
</testsuite>
""",
        encoding="utf-8",
    )

    results = parsers.parse_junit_file("unit", "bash", report)

    assert results[0].test_path == Path("src/test/unit/bash/shell-config.unit.test.bats")
    assert results[0].name == "loads shell config"


def test_parse_ruby_results_uses_recorded_source_file(load_module, tmp_path: Path):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")
    result_file = tmp_path / "results.json"
    result_file.write_text(
        json.dumps(
            {
                "tests": [
                    {
                        "classname": "ArqListWildcardExcludesTest",
                        "name": "test_yaml_output",
                        "file": "/Users/operator/.flakes/nix-darwin/src/test/unit/ruby/arq_list_wildcard_excludes.unit.test.rb",
                        "line": 12,
                        "status": "passed",
                        "time": 0.3,
                        "stdout": "",
                        "stderr": "",
                        "failures": [],
                    },
                ],
            },
        ),
        encoding="utf-8",
    )

    results = parsers.parse_ruby_file("unit", result_file)

    assert results[0].test_path == Path("src/test/unit/ruby/arq_list_wildcard_excludes.unit.test.rb")
    assert results[0].status == "passed"
    assert results[0].source_line == 12
    assert results[0].tested_stdout == ""


def test_parse_ruby_results_preserves_per_case_diagnostics(load_module, tmp_path: Path):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")
    result_file = tmp_path / "results.json"
    result_file.write_text(
        json.dumps(
            {
                "tests": [
                    {
                        "classname": "ArqListWildcardExcludesTest",
                        "name": "test_yaml_output",
                        "file": "src/test/unit/ruby/arq_list_wildcard_excludes.unit.test.rb",
                        "line": 12,
                        "status": "failed",
                        "time": 0.3,
                        "stdout": "ruby stdout\n",
                        "stderr": "ruby stderr\n",
                        "failures": [
                            {
                                "class": "Minitest::Assertion",
                                "message": "Expected YAML output",
                                "backtrace": [
                                    "src/test/unit/ruby/arq_list_wildcard_excludes.unit.test.rb:12:in `test_yaml_output'",
                                ],
                            },
                        ],
                    },
                ],
            },
        ),
        encoding="utf-8",
    )

    results = parsers.parse_ruby_file("unit", result_file)

    assert results[0].failure_message == "Expected YAML output"
    assert "Minitest::Assertion: Expected YAML output" in results[0].failure
    assert "arq_list_wildcard_excludes.unit.test.rb:12" in results[0].assertion_failure
    assert results[0].stdout == "ruby stdout\n"
    assert results[0].stderr == "ruby stderr\n"
    assert results[0].assertion_stdout == "ruby stdout\n"
    assert results[0].assertion_stderr == "ruby stderr\n"
    assert results[0].tested_stdout == "ruby stdout\n"
    assert results[0].tested_stderr == "ruby stderr\n"


def test_source_path_from_recorded_file_keeps_relative_ruby_path(load_module):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")

    test_path = parsers.source_path_from_recorded_file(
        "src/test/unit/ruby/relative.unit.test.rb",
        Path("src/test/unit/ruby/results.json"),
    )

    assert test_path == Path("src/test/unit/ruby/relative.unit.test.rb")


def test_parse_nix_output_file_normalizes_statuses_and_test_path(load_module, tmp_path: Path):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")
    output_file = tmp_path / "unit" / "nix" / "flake.unit.test.nix" / "output.txt"
    output_file.parent.mkdir(parents=True)
    output_file.write_text(
        "\n".join(
            [
                "warning: unknown setting 'allowed-users'",
                "\x1b[31m❌ testFail\x1b[0m",
                "\x1b[1m/private/tmp/nix-123/expected.nix\x1b[0m --- Nix",
                "1 { alpha = 1; }     1 { alpha = 2; }",
                "",
                "😢 1/2 successful",
                "✅ testPass",
                "error: Tests failed",
            ],
        ),
        encoding="utf-8",
    )

    results = parsers.parse_nix_output_file("unit", output_file)

    assert [result.test_path for result in results] == [
        Path("src/test/unit/nix/flake.unit.test.nix"),
        Path("src/test/unit/nix/flake.unit.test.nix"),
    ]
    assert [(result.name, result.status) for result in results] == [
        ("testFail", "failed"),
        ("testPass", "passed"),
    ]
    assert results[0].failure_message == "/private/tmp/nix-123/expected.nix --- Nix"
    assert "/private/tmp/nix-123/expected.nix --- Nix" in results[0].failure
    assert "1 { alpha = 1; }     1 { alpha = 2; }" in results[0].assertion_failure
    assert "warning: unknown setting" not in results[0].failure
    assert "😢 1/2 successful" not in results[0].failure
    assert "✅ testPass" not in results[0].failure
    assert "error: Tests failed" not in results[0].failure
    assert results[1].stdout == ""


def test_parse_zunit_tap_file_normalizes_skip_failure_and_test_path(load_module, tmp_path: Path):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")
    tap_file = tmp_path / "unit" / "zsh" / "bootstrap-macos.unit.zunit" / "output.tap"
    tap_file.parent.mkdir(parents=True)
    tap_file.write_text(
        "\n".join(
            [
                "TAP version 13",
                "not ok 1 - Failure: rejects unknown option",
                "  ---",
                "  message: expected failure detail",
                "  ...",
                "deferred fixture setup",
                "ok 2 - # SKIP deferred video fixture",
                "accepted stdout",
                "ok 3 - accepts known option",
                "1..3",
                "",
            ],
        ),
        encoding="utf-8",
    )

    results = parsers.parse_zunit_tap_file("unit", tap_file)

    assert [result.test_path for result in results] == [
        Path("src/test/unit/zsh/bootstrap-macos.unit.zunit"),
        Path("src/test/unit/zsh/bootstrap-macos.unit.zunit"),
        Path("src/test/unit/zsh/bootstrap-macos.unit.zunit"),
    ]
    assert [(result.name, result.status) for result in results] == [
        ("rejects unknown option", "failed"),
        ("deferred video fixture", "skipped"),
        ("accepts known option", "passed"),
    ]
    assert results[0].failure_message == "expected failure detail"
    assert "expected failure detail" in results[0].failure
    assert results[1].stdout == "deferred fixture setup"
    assert results[2].stdout == "accepted stdout"


def test_parse_nix_source_target_coverage(load_module, tmp_path: Path):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")
    coverage = tmp_path / "all" / "nix" / "source-target-coverage.tsv"
    coverage.parent.mkdir(parents=True)
    coverage.write_text(
        "target\tstatus\nflake.nix\tcovered\nmodules/kitty.nix\tcovered\nmodules/missing.nix\tuncovered\n",
        encoding="utf-8",
    )

    records = parsers.parse_declared_target_coverage("all", "nix", coverage, metric_name="source-target")

    by_path = {record.source_path: record for record in records}
    assert by_path[Path("flake.nix")].metrics[0].display == "1 / 1 (100%)"
    assert by_path[Path("modules/missing.nix")].metrics[0].display == "0 / 1 (0%)"
    assert by_path[Path("modules/kitty.nix")].detail_kind == "source-target"


def test_parse_statement_target_coverage(load_module, tmp_path: Path):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")
    coverage = tmp_path / "unit" / "zsh" / "statement-coverage.tsv"
    coverage.parent.mkdir(parents=True)
    coverage.write_text(
        "target\tstatus\tcovered_statements\ttotal_statements\n"
        "tools/bootstrap/bootstrap-macos\tcovered\t12\t12\n"
        "shell/zshrc\tuncovered\t0\t4\n",
        encoding="utf-8",
    )

    records = parsers.parse_declared_statement_coverage("unit", "zsh", coverage)

    assert {record.source_path for record in records} == {
        Path("tools/bootstrap/bootstrap-macos"),
        Path("shell/zshrc"),
    }
    by_path = {record.source_path: record for record in records}
    assert by_path[Path("tools/bootstrap/bootstrap-macos")].metrics[0].name == "statement"
    assert by_path[Path("tools/bootstrap/bootstrap-macos")].metrics[0].covered == 12
    assert by_path[Path("tools/bootstrap/bootstrap-macos")].metrics[0].total == 12
    assert by_path[Path("tools/bootstrap/bootstrap-macos")].metrics[0].display == "12 / 12 (100%)"
    assert by_path[Path("shell/zshrc")].metrics[0].covered == 0
    assert by_path[Path("shell/zshrc")].metrics[0].total == 4
    assert by_path[Path("shell/zshrc")].metrics[0].display == "0 / 4 (0%)"


def test_discover_coverage_finds_normalized_and_native_artifacts(load_module, tmp_path: Path):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")
    report_dir = tmp_path / "latest"

    nix_coverage = report_dir / "all" / "nix" / "source-target-coverage.tsv"
    nix_coverage.parent.mkdir(parents=True)
    nix_coverage.write_text("target\tstatus\nflake.nix\tcovered\n", encoding="utf-8")

    zsh_coverage = report_dir / "unit" / "zsh" / "statement-coverage.tsv"
    zsh_coverage.parent.mkdir(parents=True)
    zsh_coverage.write_text(
        "target\tstatus\tcovered_statements\ttotal_statements\nshell/zshrc\tcovered\t3\t4\n",
        encoding="utf-8",
    )

    python_coverage = report_dir / "unit" / "python" / "coverage.xml"
    python_coverage.parent.mkdir(parents=True)
    python_coverage.write_text('<coverage line-rate="0.75" branch-rate="0.5"></coverage>\n', encoding="utf-8")
    python_html = report_dir / "unit" / "python" / "html" / "index.html"
    python_html.parent.mkdir(parents=True)
    python_html.write_text("<html>python coverage</html>\n", encoding="utf-8")

    ruby_coverage = report_dir / "unit" / "ruby" / ".last_run.json"
    ruby_coverage.parent.mkdir(parents=True)
    ruby_coverage.write_text('{"result":{"line":88.8,"branch":66.7}}\n', encoding="utf-8")
    ruby_html = report_dir / "unit" / "ruby" / "index.html"
    ruby_html.write_text("<html>ruby coverage</html>\n", encoding="utf-8")

    bash_coverage = report_dir / "unit" / "bash" / "kcov" / "kcov-merged" / "coverage.json"
    bash_coverage.parent.mkdir(parents=True)
    bash_coverage.write_text('{"percent_covered":"12.50","covered_lines":1,"total_lines":8}\n', encoding="utf-8")
    bash_html = report_dir / "unit" / "bash" / "kcov" / "index.html"
    bash_html.write_text("<html>bash coverage</html>\n", encoding="utf-8")

    records = parsers.discover_coverage(report_dir)

    keys = {(record.scope, record.language, record.detail_kind) for record in records}
    assert ("all", "nix", "source-target") in keys
    assert ("unit", "zsh", "statement-target") in keys
    assert ("unit", "python", "native") in keys
    assert ("unit", "ruby", "native") in keys
    assert ("unit", "bash", "native") in keys
    assert {record.native_report for record in records if record.detail_kind == "native"} == {
        python_html,
        ruby_html,
        bash_html,
    }


def test_declared_target_coverage_rejects_missing_headers(load_module, tmp_path: Path):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")
    coverage = tmp_path / "source-target-coverage.tsv"
    coverage.write_text("target\nflake.nix\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"source-target-coverage\.tsv.*missing required header.*status"):
        parsers.parse_declared_target_coverage("all", "nix", coverage, metric_name="source-target")


def test_declared_target_coverage_rejects_unknown_status(load_module, tmp_path: Path):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")
    coverage = tmp_path / "source-target-coverage.tsv"
    coverage.write_text("target\tstatus\nflake.nix\tpartial\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"source-target-coverage\.tsv.*row 2.*unknown status.*partial"):
        parsers.parse_declared_target_coverage("all", "nix", coverage, metric_name="source-target")


def test_statement_coverage_rejects_non_integer_counts(load_module, tmp_path: Path):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")
    coverage = tmp_path / "statement-coverage.tsv"
    coverage.write_text(
        "target\tstatus\tcovered_statements\ttotal_statements\nshell/zshrc\tcovered\tmany\t4\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"statement-coverage\.tsv.*row 2.*covered_statements.*many"):
        parsers.parse_declared_statement_coverage("unit", "zsh", coverage)


def test_statement_coverage_rejects_short_rows(load_module, tmp_path: Path):
    load_module("tools/test/report/model.py", "model")
    parsers = load_module("tools/test/report/parsers.py")
    coverage = tmp_path / "statement-coverage.tsv"
    coverage.write_text(
        "target\tstatus\tcovered_statements\ttotal_statements\nshell/zshrc\tcovered\t3\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"statement-coverage\.tsv.*row 2.*missing value.*total_statements"):
        parsers.parse_declared_statement_coverage("unit", "zsh", coverage)
