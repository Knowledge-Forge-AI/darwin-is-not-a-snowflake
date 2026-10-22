from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


def test_report_renderer_normalizes_nix_unit_output(load_module, tmp_path: Path):
    module = load_module("tools/test/report/render.py")
    report_dir = tmp_path / "report"
    nix_report_dir = report_dir / "unit" / "nix" / "flake.unit.test.nix"
    nix_report_dir.mkdir(parents=True)
    bash_kcov_dir = report_dir / "unit" / "bash" / "kcov"
    bash_kcov_dir.mkdir(parents=True)
    (bash_kcov_dir / "index.html").write_text("<html>bash coverage</html>\n", encoding="utf-8")
    awk_report_dir = report_dir / "unit" / "awk"
    awk_report_dir.mkdir(parents=True)
    (awk_report_dir / "statement-coverage.html").write_text("<html>awk coverage</html>\n", encoding="utf-8")
    applescript_report_dir = report_dir / "unit" / "applescript"
    applescript_report_dir.mkdir(parents=True)
    (applescript_report_dir / "statement-coverage.html").write_text(
        "<html>applescript coverage</html>\n",
        encoding="utf-8",
    )
    (nix_report_dir / "output.txt").write_text(
        "\n".join(
            [
                "warning: unknown setting 'allowed-users'",
                "❌ testFail",
                "/private/tmp/nix-123/expected.nix --- Nix",
                "1 true                      1 false",
                "",
                "😢 1/2 successful",
                "✅ testPass",
                "error: Tests failed",
                "",
            ],
        ),
        encoding="utf-8",
    )
    summary_file = report_dir / "summary.tsv"
    summary_file.write_text("scope\tlanguage\tmetric\tvalue\n", encoding="utf-8")

    model = module.summarize(module.discover_results(report_dir))
    (report_dir / "test-results.json").write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    (report_dir / "index.html").write_text(module.render_html(report_dir, model), encoding="utf-8")
    module.append_summary_metrics(summary_file, model)

    assert model["summary"] == {"total": 2, "passed": 1, "failed": 1, "skipped": 0}
    assert {
        "scope": "unit",
        "language": "nix",
        "module": "flake.unit.test.nix",
        "total": 2,
        "passed": 1,
        "failed": 1,
        "skipped": 0,
    } in model["suites"]
    tests = {(test["name"], test["status"]): test for test in model["tests"]}
    assert ("testPass", "passed") in tests
    assert ("testFail", "failed") in tests
    assert "/private/tmp/nix-123/expected.nix --- Nix" in tests[("testFail", "failed")]["failure"]
    assert "1 true                      1 false" in tests[("testFail", "failed")]["failure"]
    assert "Tests failed" not in tests[("testFail", "failed")]["failure"]
    assert "unit\tnix\ttests.total\t2" in summary_file.read_text(encoding="utf-8")
    html_report = (report_dir / "index.html").read_text(encoding="utf-8")
    assert "flake.unit.test.nix" in html_report
    assert "unit bash coverage" in html_report
    assert "unit awk coverage" in html_report
    assert "unit applescript coverage" in html_report


def test_report_renderer_uses_external_html_templates(load_module, repo_root: Path):
    module = load_module("tools/test/report/render.py")
    renderer_source = (repo_root / "tools/test/report/render.py").read_text(encoding="utf-8")

    assert (repo_root / "tools/test/report/index.html").is_file()
    assert (repo_root / "tools/test/report/suite-row.html").is_file()
    assert (repo_root / "tools/test/report/test-row.html").is_file()
    assert "<html" not in renderer_source
    assert "<style>" not in renderer_source
    assert module.parse_args(
        [
            "--report-dir",
            str(repo_root / ".test-reports/unit/latest"),
            "--summary-file",
            str(repo_root / ".test-reports/unit/latest/summary.tsv"),
        ],
    ).template_file == repo_root / "tools/test/report/index.html"


def test_report_renderer_rejects_legacy_template_overrides(load_module, repo_root: Path):
    module = load_module("tools/test/report/render.py")

    try:
        module.parse_args(
            [
                "--report-dir",
                str(repo_root / ".test-reports/unit/latest"),
                "--summary-file",
                str(repo_root / ".test-reports/unit/latest/summary.tsv"),
                "--template-file",
                str(repo_root / "custom.html"),
            ],
        )
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("legacy template override should fail parsing")


def test_report_renderer_parses_junit_ruby_and_empty_coverage(load_module, tmp_path: Path):
    module = load_module("tools/test/report/render.py")
    report_dir = tmp_path / "report"
    python_report_dir = report_dir / "unit" / "python"
    bash_report_dir = report_dir / "unit" / "bash" / "shell.unit.test.bats"
    ruby_report_dir = report_dir / "unit" / "ruby"
    python_report_dir.mkdir(parents=True)
    bash_report_dir.mkdir(parents=True)
    ruby_report_dir.mkdir(parents=True)
    (python_report_dir / "junit.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite>
  <testcase classname="python.module" name="test_pass" time="0.1">
    <system-out>hello &amp; goodbye</system-out>
  </testcase>
  <testcase classname="python.module" name="test_fail" time="0.2">
    <failure message="boom">stack trace</failure>
    <system-err>stderr text</system-err>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )
    (bash_report_dir / "report.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite>
  <testcase classname="shell.unit.test.bats" name="test_skip">
    <skipped message="not today">skip detail</skipped>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )
    (ruby_report_dir / "results.json").write_text(
        json.dumps(
            {
                "tests": [
                    {
                        "classname": "RubyThing",
                        "name": "test_ruby_failure",
                        "status": "failed",
                        "time": 0.3,
                        "stdout": "ruby stdout",
                        "stderr": "ruby stderr",
                        "failures": [{"message": "ruby boom", "backtrace": ["line 1", "line 2"]}],
                    },
                ],
            },
        ),
        encoding="utf-8",
    )

    model = module.summarize(module.discover_results(report_dir))
    rendered = module.render_html(report_dir, model)

    assert model["summary"] == {"total": 4, "passed": 1, "failed": 2, "skipped": 1}
    assert any(test["language"] == "python" and test["stdout"] == "hello & goodbye" for test in model["tests"])
    assert any(test["language"] == "bash" and test["status"] == "skipped" for test in model["tests"])
    assert any(test["language"] == "ruby" and "line 2" in test["failure"] for test in model["tests"])
    assert "No coverage reports were generated" in rendered
    assert "hello &amp; goodbye" in rendered
    assert "ruby boom" in rendered


def test_report_renderer_prefers_pytest_json_report_over_junit(load_module, tmp_path: Path):
    module = load_module("tools/test/report/render.py")
    python_report_dir = tmp_path / "report" / "unit" / "python"
    python_report_dir.mkdir(parents=True)
    (python_report_dir / "junit.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite>
  <testcase classname="stale.junit" name="test_stale" time="0.1">
    <system-out>stale junit stdout</system-out>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )
    (python_report_dir / "report.json").write_text(
        json.dumps(
            {
                "tests": [
                    {
                        "nodeid": "src/test/unit/python/fresh.unit.test.py::test_fresh",
                        "lineno": 2,
                        "outcome": "passed",
                        "setup": {"duration": 0.001, "outcome": "passed"},
                        "call": {"duration": 0.002, "outcome": "passed", "stdout": "fresh stdout\n"},
                        "teardown": {"duration": 0.001, "outcome": "passed"},
                    },
                ],
            },
        ),
        encoding="utf-8",
    )

    results = module.discover_results(tmp_path / "report")

    assert len(results) == 1
    assert results[0]["name"] == "test_fresh"
    assert results[0]["stdout"] == "fresh stdout"
    assert results[0]["report_phases"] == [
        {"phase": "setup", "outcome": "passed", "duration": "0.001s"},
        {"phase": "call", "outcome": "passed", "duration": "0.002s"},
        {"phase": "teardown", "outcome": "passed", "duration": "0.001s"},
    ]


def test_report_renderer_warns_for_manifest_source_missing_from_native_coverage(load_module, tmp_path: Path):
    module = load_module("tools/test/report/render.py")
    repo_root = tmp_path / "repo"
    report_dir = repo_root / ".test-reports" / "unit" / "latest"
    python_report_dir = report_dir / "unit" / "python"
    source_dir = repo_root / "tools" / "test" / "report"
    manifest_file = repo_root / "src" / "test" / "coverage-manifest.tsv"
    python_report_dir.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    manifest_file.parent.mkdir(parents=True)
    (source_dir / "covered.py").write_text("print('covered')\n", encoding="utf-8")
    (source_dir / "missing.py").write_text("print('missing')\n", encoding="utf-8")
    manifest_file.write_text(
        "\n".join(
            [
                "scope\tlanguage\tsource_path",
                "unit\tpython\ttools/test/report/covered.py",
                "unit\tpython\ttools/test/report/missing.py",
                "",
            ],
        ),
        encoding="utf-8",
    )
    (python_report_dir / "coverage.xml").write_text(
        f"""<?xml version="1.0" ?>
<coverage line-rate="0.9" branch-rate="0.9">
  <sources>
    <source>{source_dir}</source>
  </sources>
  <packages>
    <package name=".">
      <classes>
        <class name="covered.py" filename="covered.py" line-rate="1" branch-rate="1">
          <lines><line number="1" hits="1"/></lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
""",
        encoding="utf-8",
    )

    report_model = module.build_report_model(report_dir, repo_root=repo_root)

    warnings = {
        (result.scope, result.language, result.metric, result.source_path): result
        for result in report_model["coverage_gate_report"].warnings
    }
    assert ("unit", "python", "manifest", Path("tools/test/report/missing.py")) in warnings
    assert ("unit", "python", "manifest", Path("tools/test/report/covered.py")) not in warnings


def test_report_renderer_enriches_tests_with_source_excerpt_and_execution_log(load_module, tmp_path: Path):
    module = load_module("tools/test/report/render.py")
    repo_root = tmp_path / "repo"
    report_dir = repo_root / ".test-reports" / "unit" / "latest"
    python_test = repo_root / "src" / "test" / "unit" / "python" / "example.unit.test.py"
    ruby_test = repo_root / "src" / "test" / "unit" / "ruby" / "example.unit.test.rb"
    python_report_dir = report_dir / "unit" / "python"
    ruby_report_dir = report_dir / "unit" / "ruby"
    python_test.parent.mkdir(parents=True)
    ruby_test.parent.mkdir(parents=True)
    python_report_dir.mkdir(parents=True)
    ruby_report_dir.mkdir(parents=True)
    python_test.write_text(
        "\n".join(
            [
                "def helper():",
                "    return 0",
                "",
                "def test_python_detail():",
                "    result = helper()",
                "    assert result == 0",
                "",
            ],
        ),
        encoding="utf-8",
    )
    ruby_test.write_text(
        "\n".join(
            [
                "class ExampleTest < Minitest::Test",
                "  def test_ruby_detail",
                "    stdout, stderr, status = run_command",
                "    assert_equal 0, status",
                "  end",
                "end",
                "",
            ],
        ),
        encoding="utf-8",
    )
    (python_report_dir / "junit.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite>
  <testcase classname="src.test.unit.python.example.unit.test" name="test_python_detail" time="0.1" />
</testsuite>
""",
        encoding="utf-8",
    )
    (python_report_dir / "output.txt").write_text("pytest transcript\n", encoding="utf-8")
    (ruby_report_dir / "results.json").write_text(
        json.dumps(
            {
                "tests": [
                    {
                        "classname": "ExampleTest",
                        "name": "test_ruby_detail",
                        "file": str(ruby_test),
                        "line": 2,
                        "status": "passed",
                        "time": 0.01,
                        "stdout": "",
                        "stderr": "",
                        "failures": [],
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    (ruby_report_dir / "output.txt").write_text("ruby transcript\n", encoding="utf-8")

    model = module.build_report_model(report_dir, repo_root=repo_root)

    tests = {(test.language, test.name): test for test in model["tests"]}
    assert "def test_python_detail():" in tests[("python", "test_python_detail")].source_excerpt
    assert "assert result == 0" in tests[("python", "test_python_detail")].source_excerpt
    assert tests[("python", "test_python_detail")].execution_log is None
    assert "def test_ruby_detail" in tests[("ruby", "test_ruby_detail")].source_excerpt
    assert "assert_equal 0, status" in tests[("ruby", "test_ruby_detail")].source_excerpt
    assert tests[("ruby", "test_ruby_detail")].source_line == 2
    assert tests[("ruby", "test_ruby_detail")].execution_log == ruby_report_dir / "output.txt"


def test_report_renderer_discovers_python_run_overview_and_links_language_directory(
    load_module,
    tmp_path: Path,
):
    module = load_module("tools/test/report/render.py")
    report_dir = tmp_path / "report"
    python_report_dir = report_dir / "unit" / "python"
    python_report_dir.mkdir(parents=True)
    (python_report_dir / "junit.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite>
  <testcase classname="src.test.unit.python.example.unit.test" name="test_example" time="0.1" />
</testsuite>
""",
        encoding="utf-8",
    )
    (python_report_dir / "output.txt").write_text(
        "\n".join(
            [
                "============================= test session starts ==============================",
                "collected 1 item",
                "src/test/unit/python/example.unit.test.py .                         [100%]",
                "",
                "================================ tests coverage ================================",
                "Name                                      Stmts   Miss Branch BrPart  Cover",
                "tools/test/report/render.py                 42      2     10      1    92%",
                "Coverage XML written to file .test-reports/unit/latest/unit/python/coverage.xml",
                "============================== 1 passed in 0.42s ===============================",
            ],
        ),
        encoding="utf-8",
    )

    model = module.build_report_model(report_dir)

    assert len(model["run_overviews"]) == 1
    overview = model["run_overviews"][0]
    assert overview.scope == "unit"
    assert overview.language == "python"
    assert "collected 1 item" in overview.test_output
    assert "1 passed in 0.42s" in overview.test_output
    assert "tests coverage" not in overview.test_output
    assert "Name                                      Stmts" in overview.coverage_output
    assert "1 passed in 0.42s" not in overview.coverage_output
    assert model["test_links"][Path("src/test/unit/python")] == "tests/unit--python.html"


def test_report_renderer_discovers_ruby_run_overview_and_links_language_directory(
    load_module,
    tmp_path: Path,
):
    module = load_module("tools/test/report/render.py")
    report_dir = tmp_path / "report"
    ruby_report_dir = report_dir / "unit" / "ruby"
    ruby_report_dir.mkdir(parents=True)
    (ruby_report_dir / "results.json").write_text(
        json.dumps(
            {
                "tests": [
                    {
                        "classname": "ArqListWildcardExcludesTest",
                        "name": "test_yaml_output",
                        "file": "src/test/unit/ruby/arq_list_wildcard_excludes.unit.test.rb",
                        "line": 12,
                        "status": "passed",
                        "time": 0.3,
                        "stdout": "ruby stdout\n",
                        "stderr": "",
                        "failures": [],
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    (ruby_report_dir / "output.txt").write_text(
        "\n".join(
            [
                "ruby runner transcript",
                "1 runs, 1 assertions, 0 failures, 0 errors, 0 skips",
                "Coverage report generated for Unit Tests to .test-reports/unit/latest/unit/ruby.",
                "Line Coverage: 98.86% (87 / 88)",
                "Branch Coverage: 90.48% (19 / 21)",
            ],
        ),
        encoding="utf-8",
    )

    model = module.build_report_model(report_dir)

    assert len(model["run_overviews"]) == 1
    overview = model["run_overviews"][0]
    assert overview.scope == "unit"
    assert overview.language == "ruby"
    assert "ruby runner transcript" in overview.test_output
    assert "1 runs, 1 assertions" in overview.test_output
    assert "Line Coverage: 98.86%" not in overview.test_output
    assert "Line Coverage: 98.86%" in overview.coverage_output
    assert "1 runs, 1 assertions" not in overview.coverage_output
    assert model["test_links"][Path("src/test/unit/ruby")] == "tests/unit--ruby.html"


def test_report_renderer_discovers_bats_run_overview_and_links_language_directory(
    load_module,
    tmp_path: Path,
):
    module = load_module("tools/test/report/render.py")
    report_dir = tmp_path / "report"
    alpha_dir = report_dir / "unit" / "bash" / "alpha.unit.test.bats"
    beta_dir = report_dir / "unit" / "bash" / "beta.unit.test.bats"
    alpha_dir.mkdir(parents=True)
    beta_dir.mkdir(parents=True)
    for test_dir, name in ((alpha_dir, "alpha"), (beta_dir, "beta")):
        (test_dir / "report.xml").write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<testsuite>
  <testcase classname="{test_dir.name}" name="{name} passes" time="0.1" />
</testsuite>
""",
            encoding="utf-8",
        )
        (test_dir / "output.txt").write_text(f"1..1\nok 1 {name} passes\n", encoding="utf-8")

    model = module.build_report_model(report_dir)

    overviews = {
        (overview.scope, overview.language): overview
        for overview in model["run_overviews"]
    }
    overview = overviews[("unit", "bash")]
    assert overview.output_log is None
    assert overview.test_output == ""
    assert [section.title for section in overview.sections] == [
        "alpha.unit.test.bats",
        "beta.unit.test.bats",
    ]
    assert "ok 1 alpha passes" in overview.sections[0].output
    assert "ok 1 beta passes" in overview.sections[1].output
    assert model["test_links"][Path("src/test/unit/bash")] == "tests/unit--bash.html"


def test_report_renderer_discovers_nix_run_overview_and_links_language_directory(
    load_module,
    tmp_path: Path,
):
    module = load_module("tools/test/report/render.py")
    report_dir = tmp_path / "report"
    nix_report_dir = report_dir / "unit" / "nix" / "flake.unit.test.nix"
    nix_report_dir.mkdir(parents=True)
    (nix_report_dir / "output.txt").write_text(
        "warning: unknown setting 'allowed-users'\n✅ testPass\n🎉 1/1 successful\n",
        encoding="utf-8",
    )

    model = module.build_report_model(report_dir)

    overviews = {
        (overview.scope, overview.language): overview
        for overview in model["run_overviews"]
    }
    overview = overviews[("unit", "nix")]
    assert overview.output_log is None
    assert overview.test_output == ""
    assert [section.title for section in overview.sections] == ["flake.unit.test.nix"]
    assert "warning: unknown setting" in overview.sections[0].output
    assert "✅ testPass" in overview.sections[0].output
    assert model["test_links"][Path("src/test/unit/nix")] == "tests/unit--nix.html"


def test_report_renderer_extracts_shell_zsh_and_nix_diagnostics(load_module, tmp_path: Path):
    module = load_module("tools/test/report/render.py")
    model_module = load_module("tools/test/report/model.py", "model")
    repo_root = tmp_path / "repo"
    report_dir = repo_root / ".test-reports" / "unit" / "latest"
    bash_test = repo_root / "src" / "test" / "unit" / "bash" / "tool.unit.test.bats"
    zsh_test = repo_root / "src" / "test" / "unit" / "zsh" / "bootstrap.unit.zunit"
    nix_test = repo_root / "src" / "test" / "unit" / "nix" / "flake.unit.test.nix"
    bash_log = report_dir / "unit" / "bash" / bash_test.name / "output.txt"
    zsh_log = report_dir / "unit" / "zsh" / zsh_test.name / "output.tap"
    nix_log = report_dir / "unit" / "nix" / nix_test.name / "output.txt"
    for path in (bash_test, zsh_test, nix_test, bash_log, zsh_log, nix_log):
        path.parent.mkdir(parents=True, exist_ok=True)
    bash_test.write_text(
        "@test 'runs fake command' {\n  run fake-command\n  [ \"$status\" -eq 0 ]\n}\n",
        encoding="utf-8",
    )
    zsh_test.write_text(
        "@test 'bootstraps fake host' {\n  source ./bootstrap\n  assert \"$status\" equals 0\n}\n",
        encoding="utf-8",
    )
    nix_test.write_text(
        "testNixDetail = {\n  expr = true;\n  expected = true;\n};\n",
        encoding="utf-8",
    )
    bash_log.write_text("bats transcript\n", encoding="utf-8")
    zsh_log.write_text("zunit transcript\n", encoding="utf-8")
    nix_log.write_text("nix transcript\n", encoding="utf-8")
    tests = [
        model_module.TestCaseResult("unit", "bash", Path("src/test/unit/bash/tool.unit.test.bats"), "tool", "runs fake command", "passed", 0.1),
        model_module.TestCaseResult("unit", "zsh", Path("src/test/unit/zsh/bootstrap.unit.zunit"), "bootstrap", "bootstraps fake host", "passed", 0.1),
        model_module.TestCaseResult("unit", "nix", Path("src/test/unit/nix/flake.unit.test.nix"), "flake", "testNixDetail", "passed", 0.0),
    ]

    enriched = module.enrich_test_results(report_dir, repo_root, tests)

    by_language = {test.language: test for test in enriched}
    assert "run fake-command" in by_language["bash"].source_excerpt
    assert by_language["bash"].execution_log == bash_log
    assert "source ./bootstrap" in by_language["zsh"].source_excerpt
    assert by_language["zsh"].execution_log == zsh_log
    assert "testNixDetail" in by_language["nix"].source_excerpt
    assert by_language["nix"].execution_log == nix_log


def test_report_renderer_attaches_gathered_bats_case_output_log(load_module, tmp_path: Path):
    module = load_module("tools/test/report/render.py")
    model_module = load_module("tools/test/report/model.py", "model")
    report_dir = tmp_path / "report"
    repo_root = tmp_path / "repo"
    test_path = Path("src/test/unit/bash/tool.unit.test.bats")
    test_file = repo_root / test_path
    case_log = report_dir / "unit" / "bash" / test_path.name / "test-outputs" / "1-runs fake command.log"
    test_file.parent.mkdir(parents=True)
    case_log.parent.mkdir(parents=True)
    test_file.write_text(
        "@test 'runs fake command' {\n  run fake-command\n  [ \"$status\" -eq 0 ]\n}\n",
        encoding="utf-8",
    )
    case_log.write_text("fake stdout\nfake stderr\n", encoding="utf-8")
    tests = [
        model_module.TestCaseResult(
            "unit",
            "bash",
            test_path,
            "tool.unit.test.bats",
            "runs fake command",
            "passed",
            0.1,
        ),
    ]

    enriched = module.enrich_test_results(report_dir, repo_root, tests)

    assert enriched[0].case_output_log == case_log


def test_report_renderer_diagnostic_helpers_handle_fallbacks(load_module, tmp_path: Path):
    module = load_module("tools/test/report/render.py")
    model_module = load_module("tools/test/report/model.py", "model")
    repo_root = tmp_path / "repo"
    invalid_python = repo_root / "src" / "test" / "unit" / "python" / "broken.unit.test.py"
    missing_source = model_module.TestCaseResult(
        "unit",
        "python",
        Path("src/test/unit/python/missing.unit.test.py"),
        "missing",
        "test_missing",
        "passed",
        0.1,
    )
    invalid_python.parent.mkdir(parents=True)
    invalid_python.write_text(
        "def test_broken(\n    assert False\n",
        encoding="utf-8",
    )
    syntax_error_test = model_module.TestCaseResult(
        "unit",
        "python",
        Path("src/test/unit/python/broken.unit.test.py"),
        "broken",
        "test_broken",
        "passed",
        0.1,
    )
    unknown_language_test = model_module.TestCaseResult(
        "unit",
        "mystery",
        invalid_python,
        "broken",
        "test_broken",
        "passed",
        0.1,
        source_line=2,
    )

    assert module.infer_repo_root(repo_root / ".test-reports" / "unit" / "latest") == repo_root
    assert module.infer_repo_root(tmp_path / "plain-report") is None
    assert module.source_excerpt_for_test(repo_root, missing_source) == ""
    assert "assert False" in module.source_excerpt_for_test(repo_root, syntax_error_test)
    assert "assert False" in module.source_excerpt_for_test(repo_root, unknown_language_test)
    assert module.execution_log_for_test(tmp_path, unknown_language_test) is None
    assert module.enrich_test_result(tmp_path, None, unknown_language_test).source_excerpt == ""


def test_report_renderer_coverage_links_include_native_fallback_indexes(load_module, tmp_path: Path):
    module = load_module("tools/test/report/render.py")
    report_dir = tmp_path / "report"
    (report_dir / "unit" / "python").mkdir(parents=True)
    (report_dir / "unit" / "ruby").mkdir(parents=True)
    (report_dir / "unit" / "python" / "index.html").write_text("<html>python fallback</html>\n", encoding="utf-8")
    (report_dir / "unit" / "ruby" / "index.html").write_text("<html>ruby fallback</html>\n", encoding="utf-8")

    links = module.coverage_links(report_dir)

    assert ("unit", "python", "unit/python/index.html") in links
    assert ("unit", "ruby", "unit/ruby/index.html") in links


def test_report_renderer_normalizes_zunit_tap_output(load_module, tmp_path: Path):
    module = load_module("tools/test/report/render.py")
    report_dir = tmp_path / "report"
    zsh_report_dir = report_dir / "unit" / "zsh" / "bootstrap-macos.unit.zunit"
    zsh_report_dir.mkdir(parents=True)
    (zsh_report_dir / "output.tap").write_text(
        "\n".join(
            [
                "TAP version 13",
                "known config stdout",
                "ok 1 - detects known config",
                "not ok 2 - Failure: rejects unknown option",
                "  ---",
                "  message: expected failure detail",
                "  severity: fail",
                "  ...",
                "deferred fixture setup",
                "ok 3 - # SKIP deferred video fixture",
                "1..3",
                "",
            ],
        ),
        encoding="utf-8",
    )
    summary_file = report_dir / "summary.tsv"
    summary_file.write_text("scope\tlanguage\tmetric\tvalue\n", encoding="utf-8")

    model = module.summarize(module.discover_results(report_dir))
    module.append_summary_metrics(summary_file, model)
    rendered = module.render_html(report_dir, model)

    assert model["summary"] == {"total": 3, "passed": 1, "failed": 1, "skipped": 1}
    assert {
        "scope": "unit",
        "language": "zsh",
        "module": "bootstrap-macos.unit.zunit",
        "total": 3,
        "passed": 1,
        "failed": 1,
        "skipped": 1,
    } in model["suites"]
    tests = {(test["name"], test["status"]): test for test in model["tests"]}
    assert ("detects known config", "passed") in tests
    assert ("rejects unknown option", "failed") in tests
    assert ("deferred video fixture", "skipped") in tests
    assert tests[("detects known config", "passed")]["stdout"] == "known config stdout"
    assert tests[("deferred video fixture", "skipped")]["stdout"] == "deferred fixture setup"
    assert "expected failure detail" in tests[("rejects unknown option", "failed")]["failure"]
    assert "unit\tzsh\ttests.total\t3" in summary_file.read_text(encoding="utf-8")
    assert "bootstrap-macos.unit.zunit" in rendered


def test_report_renderer_main_writes_json_html_and_summary(load_module, monkeypatch, tmp_path: Path):
    module = load_module("tools/test/report/render.py")
    report_dir = tmp_path / "report"
    nix_report_dir = report_dir / "unit" / "nix" / "flake.unit.test.nix"
    nix_report_dir.mkdir(parents=True)
    (nix_report_dir / "output.txt").write_text("✅ testPass\n", encoding="utf-8")
    (report_dir / "unit" / "nix" / "source-target-coverage.tsv").write_text(
        "target\tstatus\nflake.nix\tcovered\nmodules/source-target.nix\tuncovered\n",
        encoding="utf-8",
    )
    bats_report_dir = report_dir / "unit" / "bash" / "shell.unit.test.bats"
    bats_report_dir.mkdir(parents=True)
    (bats_report_dir / "report.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite>
  <testcase classname="shell.unit.test.bats" name="loads shell config" time="0.2" />
</testsuite>
""",
        encoding="utf-8",
    )
    python_report_dir = report_dir / "unit" / "python"
    python_report_dir.mkdir(parents=True)
    (python_report_dir / "junit.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite>
  <testcase classname="src.test.unit.python.test_report_renderer.unit.test" name="test_html" time="0.1" />
</testsuite>
""",
        encoding="utf-8",
    )
    summary_file = report_dir / "summary.tsv"
    summary_file.write_text("scope\tlanguage\tmetric\tvalue\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "render.py",
            "--report-dir",
            str(report_dir),
            "--summary-file",
            str(summary_file),
        ],
    )

    assert module.main() == 0

    assert json.loads((report_dir / "test-results.json").read_text(encoding="utf-8"))["summary"]["passed"] == 3
    assert (report_dir / "index.html").is_file()
    assert any((report_dir / "coverage").glob("*.html"))
    assert any((report_dir / "tests").glob("*.html"))
    html_report = (report_dir / "index.html").read_text(encoding="utf-8")
    assert "Coverage: source tree" in html_report
    assert "Test results: test file tree" in html_report
    assert "source-target" in html_report
    coverage_pages = list((report_dir / "coverage").glob("*.html"))
    test_pages = sorted((report_dir / "tests").glob("*.html"))
    assert coverage_pages
    assert test_pages
    assert any(f'href="coverage/{page.name}"' in html_report for page in coverage_pages)
    assert any(f'href="tests/{page.name}"' in html_report for page in test_pages)
    coverage_drilldown = "\n".join(page.read_text(encoding="utf-8") for page in coverage_pages)
    test_drilldown = "\n".join(page.read_text(encoding="utf-8") for page in test_pages)
    assert "modules/source-target.nix" in coverage_drilldown
    assert "uncovered" in coverage_drilldown
    assert "loads shell config" in test_drilldown
    assert "test_html" in test_drilldown
    assert "unit\tnix\ttests.passed\t1" in summary_file.read_text(encoding="utf-8")


def test_report_renderer_two_phase_publish_preserves_execution_and_override(
    load_module,
    monkeypatch,
    tmp_path: Path,
):
    module = load_module("tools/test/report/render.py")
    report_dir = tmp_path / ".test-reports" / "all" / "latest"
    runner_dir = report_dir / "runner"
    output = runner_dir / "002-flake-check" / "output.txt"
    output.parent.mkdir(parents=True)
    output.write_text("flake failure token\n", encoding="utf-8")
    (runner_dir / "steps.tsv").write_text(
        "ordinal\tlabel\tstatus\texit_code\toutput_log\n"
        "1\tpatch hygiene\tpass\t0\trunner/001-patch-hygiene/output.txt\n"
        "2\tflake check\tfail\t73\trunner/002-flake-check/output.txt\n",
        encoding="utf-8",
    )
    first_output = runner_dir / "001-patch-hygiene" / "output.txt"
    first_output.parent.mkdir(parents=True)
    first_output.write_text("clean\n", encoding="utf-8")
    (runner_dir / "failures.tsv").write_text(
        "sequence\tlabel\texit_code\n1\tflake check\t73\n",
        encoding="utf-8",
    )
    summary_file = report_dir / "summary.tsv"
    summary_file.write_text("scope\tlanguage\tmetric\tvalue\n", encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "render.py",
            "--report-dir",
            str(report_dir),
            "--summary-file",
            str(summary_file),
            "--summary-only",
        ],
    )
    assert module.main() == 0
    assert not (report_dir / "index.html").exists()
    assert not (report_dir / "test-results.json").exists()

    with summary_file.open("a", encoding="utf-8") as summary:
        summary.write("all\tcoverage\tcoverage.gate.override\tused\n")
    (runner_dir / "execution.tsv").write_text("status\texit_code\nfail\t73\n", encoding="utf-8")
    summary_before = summary_file.read_text(encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [*sys.argv[:-1], "--render-only"])
    assert module.main() == 0
    assert summary_file.read_text(encoding="utf-8") == summary_before

    payload = json.loads((report_dir / "test-results.json").read_text(encoding="utf-8"))
    homepage = (report_dir / "index.html").read_text(encoding="utf-8")
    assert payload["execution"] == {"status": "fail", "exit_code": 73, "detail": "recorded"}
    assert payload["runner_steps"][1]["label"] == "flake check"
    assert payload["execution_failures"] == [
        {"sequence": 1, "label": "flake check", "exit_code": 73},
    ]
    assert payload["coverage_override"] is True
    assert "Overall run: FAIL" in homepage
    assert "flake check" in homepage
    assert "Coverage override: used" in homepage


def test_render_only_removes_stale_authoritative_files_before_model_failure(
    load_module,
    monkeypatch,
    tmp_path: Path,
):
    module = load_module("tools/test/report/render.py")
    report_dir = tmp_path / "report"
    runner = report_dir / "runner"
    runner.mkdir(parents=True)
    (runner / "execution.tsv").write_text("status\texit_code\npass\t0\n", encoding="utf-8")
    (report_dir / "index.html").write_text("stale pass", encoding="utf-8")
    (report_dir / "test-results.json").write_text('{"execution":{"status":"pass"}}\n', encoding="utf-8")
    summary_file = report_dir / "summary.tsv"
    summary_file.write_text("scope\tlanguage\tmetric\tvalue\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "render.py",
            "--report-dir",
            str(report_dir),
            "--summary-file",
            str(summary_file),
            "--render-only",
        ],
    )

    with pytest.raises(ValueError, match="without runner steps"):
        module.main()
    assert not (report_dir / "index.html").exists()
    assert not (report_dir / "test-results.json").exists()


def test_report_renderer_homepage_links_duplicate_coverage_source_paths(load_module, monkeypatch, tmp_path: Path):
    module = load_module("tools/test/report/render.py")
    report_dir = tmp_path / "report"
    python_report_dir = report_dir / "unit" / "python"
    ruby_report_dir = report_dir / "unit" / "ruby"
    python_report_dir.mkdir(parents=True)
    ruby_report_dir.mkdir(parents=True)
    (python_report_dir / "coverage.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<coverage line-rate="0.75" branch-rate="0.50" />
""",
        encoding="utf-8",
    )
    (ruby_report_dir / ".last_run.json").write_text(
        json.dumps({"result": {"line": 90.0, "branch": 80.0}}),
        encoding="utf-8",
    )
    summary_file = report_dir / "summary.tsv"
    summary_file.write_text("scope\tlanguage\tmetric\tvalue\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "render.py",
            "--report-dir",
            str(report_dir),
            "--summary-file",
            str(summary_file),
        ],
    )

    assert module.main() == 0

    coverage_pages = sorted((report_dir / "coverage").glob("*.html"))
    assert [page.name for page in coverage_pages] == [
        "root--unit--python--native--1.html",
        "root--unit--ruby--native--2.html",
    ]
    html_report = (report_dir / "index.html").read_text(encoding="utf-8")
    assert 'href="coverage/root--unit--python--native--1.html"' in html_report
    assert 'href="coverage/root--unit--ruby--native--2.html"' in html_report
    assert coverage_pages[0].read_text(encoding="utf-8").count("python") >= 1
    assert "statement: 750 / 1000 (75%)" in coverage_pages[0].read_text(encoding="utf-8")
    assert coverage_pages[1].read_text(encoding="utf-8").count("ruby") >= 1
    assert "statement: 900 / 1000 (90%)" in coverage_pages[1].read_text(encoding="utf-8")
