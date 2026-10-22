from __future__ import annotations

import json
import sys
from pathlib import Path


def test_report_renderer_main_writes_full_suite_fixture_drilldowns(
    load_module,
    monkeypatch,
    tmp_path: Path,
):
    render = load_module("tools/test/report/render.py")
    report_dir = tmp_path / "report"
    summary_file = report_dir / "summary.tsv"

    python_dir = report_dir / "unit" / "python"
    python_dir.mkdir(parents=True)
    (python_dir / "coverage.xml").write_text(
        '<coverage line-rate="0.90" branch-rate="0.90"></coverage>\n',
        encoding="utf-8",
    )
    (python_dir / "junit.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite>
  <testcase classname="src.test.unit.python.test_report_renderer.unit.test" name="test_html_report" time="0.1">
    <system-out>python stdout</system-out>
  </testcase>
  <testcase classname="src.test.unit.python.test_report_pages.unit.test" name="test_page_failure" time="0.2">
    <failure message="python boom">python stack trace</failure>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )
    (python_dir / "output.txt").write_text(
        "\n".join(
            [
                "============================= test session starts ==============================",
                "collected 2 items",
                "src/test/unit/python/test_report_renderer.unit.test.py .",
                "src/test/unit/python/test_report_pages.unit.test.py F",
                "",
                "================================ tests coverage ================================",
                "Name                                      Stmts   Miss Branch BrPart  Cover",
                "tools/test/report/render.py                 42      2     10      1    92%",
                "Coverage HTML written to dir .test-reports/unit/latest/unit/python/html",
                "Coverage XML written to file .test-reports/unit/latest/unit/python/coverage.xml",
                "========================= 1 failed, 1 passed in 0.42s ==========================",
            ],
        ),
        encoding="utf-8",
    )

    bats_dir = report_dir / "unit" / "bash" / "shell-config.unit.test.bats"
    bats_dir.mkdir(parents=True)
    (bats_dir / "report.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<testsuite>
  <testcase classname="shell-config.unit.test.bats" name="loads shell config" time="0.2" />
  <testcase classname="shell-config.unit.test.bats" name="skips optional shell fixture" time="0">
    <skipped message="fixture unavailable">optional shell fixture</skipped>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )
    (bats_dir / "output.txt").write_text(
        "1..2\nok 1 loads shell config\nok 2 skips optional shell fixture # skip fixture unavailable\n",
        encoding="utf-8",
    )

    ruby_dir = report_dir / "unit" / "ruby"
    ruby_dir.mkdir(parents=True)
    (ruby_dir / "results.json").write_text(
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
                        "stdout": "ruby stdout",
                        "stderr": "",
                        "failures": [],
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    (ruby_dir / "output.txt").write_text(
        "\n".join(
            [
                "ruby runner transcript",
                "1 runs, 1 assertions, 0 failures, 0 errors, 0 skips",
                "Coverage report generated for Unit Tests to .test-reports/unit/latest/unit/ruby.",
                "Line Coverage: 98.86% (87 / 88)",
            ],
        ),
        encoding="utf-8",
    )

    nix_dir = report_dir / "unit" / "nix" / "flake.unit.test.nix"
    nix_dir.mkdir(parents=True)
    (nix_dir / "output.txt").write_text(
        "\n".join(
            [
                "warning: unknown setting 'allowed-users'",
                "\u2705 testPass",
                "\u274c testFail",
                "expected true but got false",
                "error: Tests failed",
                "",
            ],
        ),
        encoding="utf-8",
    )

    zunit_dir = report_dir / "unit" / "zsh" / "bootstrap-macos.unit.zunit"
    zunit_dir.mkdir(parents=True)
    (zunit_dir / "output.tap").write_text(
        "\n".join(
            [
                "TAP version 13",
                "ok 1 - detects known config",
                "not ok 2 - Failure: rejects unknown option",
                "  ---",
                "  message: expected failure detail",
                "  ...",
                "ok 3 - # SKIP deferred video fixture",
                "1..3",
                "",
            ],
        ),
        encoding="utf-8",
    )

    nix_coverage = report_dir / "all" / "nix" / "source-target-coverage.tsv"
    nix_coverage.parent.mkdir(parents=True)
    nix_coverage.write_text(
        "target\tstatus\n"
        "flake.nix\tcovered\n"
        "modules/kitty.nix\tcovered\n"
        "modules/missing.nix\tuncovered\n",
        encoding="utf-8",
    )

    awk_coverage = report_dir / "unit" / "awk" / "statement-coverage.tsv"
    awk_coverage.parent.mkdir(parents=True)
    awk_coverage.write_text(
        "target\tstatus\tcovered_statements\ttotal_statements\n"
        "libexec/proton-vpn/first-field.awk\tcovered\t8\t8\n"
        "libexec/proton-vpn/missing.awk\tuncovered\t0\t3\n",
        encoding="utf-8",
    )

    applescript_coverage = report_dir / "unit" / "applescript" / "statement-coverage.tsv"
    applescript_coverage.parent.mkdir(parents=True)
    applescript_coverage.write_text(
        "target\tstatus\tcovered_statements\ttotal_statements\n"
        "libexec/brave-youtube/display-notification.applescript\tcovered\t4\t5\n",
        encoding="utf-8",
    )

    zsh_coverage = report_dir / "unit" / "zsh" / "statement-coverage.tsv"
    zsh_coverage.write_text(
        "target\tstatus\tcovered_statements\ttotal_statements\n"
        "shell/zshrc\tcovered\t12\t12\n",
        encoding="utf-8",
    )

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

    assert render.main() == 0

    assert (report_dir / "index.html").is_file()
    assert (report_dir / "test-results.json").is_file()
    assert len(list((report_dir / "coverage").glob("*.html"))) >= 2
    assert len(list((report_dir / "tests").glob("*.html"))) >= 4
    homepage = (report_dir / "index.html").read_text(encoding="utf-8")
    assert "modules/kitty.nix" in homepage
    assert "src/test/unit/bash/shell-config.unit.test.bats" in homepage
    assert 'href="tests/unit--python.html"' in homepage
    assert 'href="coverage/unit--python.html"' in homepage
    assert 'href="tests/unit--bash.html"' in homepage
    assert "source-target" in homepage

    summary = json.loads((report_dir / "test-results.json").read_text(encoding="utf-8"))
    assert summary["summary"] == {"total": 10, "passed": 5, "failed": 3, "skipped": 2}
    assert summary["trees"]["coverage"]["records"] == 8
    assert summary["trees"]["tests"] == {
        "root": "nix-darwin",
        "total": 10,
        "passed": 5,
        "failed": 3,
        "skipped": 2,
    }
    assert summary["execution"] == {"status": "unknown", "exit_code": 0, "detail": "not recorded"}
    assert summary["runner_steps"] == []

    coverage_pages = {
        page.name: page.read_text(encoding="utf-8")
        for page in (report_dir / "coverage").glob("*.html")
    }
    assert "modules__kitty_nix--all--nix--source-target--2.html" in coverage_pages
    assert "libexec__proton-vpn__missing_awk--unit--awk--statement-target--5.html" in coverage_pages
    assert "0 / 3 (0%)" in coverage_pages[
        "libexec__proton-vpn__missing_awk--unit--awk--statement-target--5.html"
    ]
    assert "display-notification.applescript" in "\n".join(coverage_pages.values())

    test_pages = {
        page.name: page.read_text(encoding="utf-8")
        for page in (report_dir / "tests").glob("*.html")
    }
    assert "src__test__unit__python__test_report_renderer_unit_test_py.html" in test_pages
    assert "test_html_report" in test_pages[
        "src__test__unit__python__test_report_renderer_unit_test_py.html"
    ]
    assert "python stdout" in test_pages[
        "src__test__unit__python__test_report_renderer_unit_test_py.html"
    ]
    assert "test session starts" not in test_pages[
        "src__test__unit__python__test_report_renderer_unit_test_py.html"
    ]
    assert "src__test__unit__python__test_report_pages_unit_test_py.html" in test_pages
    assert "test_page_failure" in test_pages[
        "src__test__unit__python__test_report_pages_unit_test_py.html"
    ]
    assert "python boom" in test_pages[
        "src__test__unit__python__test_report_pages_unit_test_py.html"
    ]
    assert "python stack trace" in test_pages[
        "src__test__unit__python__test_report_pages_unit_test_py.html"
    ]
    assert "src__test__unit__bash__shell-config_unit_test_bats.html" in test_pages
    assert "skips optional shell fixture" in test_pages[
        "src__test__unit__bash__shell-config_unit_test_bats.html"
    ]
    assert "optional shell fixture" in test_pages[
        "src__test__unit__bash__shell-config_unit_test_bats.html"
    ]
    assert "Runner transcript" in test_pages[
        "src__test__unit__bash__shell-config_unit_test_bats.html"
    ]
    assert "unit--bash.html" in test_pages
    assert "shell-config.unit.test.bats" in test_pages["unit--bash.html"]
    assert "ok 1 loads shell config" in test_pages["unit--bash.html"]
    assert "src__test__unit__zsh__bootstrap-macos_unit_zunit.html" in test_pages
    assert "expected failure detail" in test_pages[
        "src__test__unit__zsh__bootstrap-macos_unit_zunit.html"
    ]
    assert "src__test__unit__nix__flake_unit_test_nix.html" in test_pages
    assert "testFail" in test_pages[
        "src__test__unit__nix__flake_unit_test_nix.html"
    ]
    assert "expected true but got false" in test_pages[
        "src__test__unit__nix__flake_unit_test_nix.html"
    ]
    assert "warning: unknown setting" in test_pages[
        "src__test__unit__nix__flake_unit_test_nix.html"
    ]
    assert "warning: unknown setting" not in test_pages[
        "src__test__unit__nix__flake_unit_test_nix.html"
    ].split("Failure / traceback", 1)[1]
    assert "unit--nix.html" in test_pages
    assert "flake.unit.test.nix" in test_pages["unit--nix.html"]
    assert "warning: unknown setting" in test_pages["unit--nix.html"]
    assert "expected true but got false" in test_pages["unit--nix.html"]
    assert "src__test__unit__ruby__arq_list_wildcard_excludes_unit_test_rb.html" in test_pages
    assert "ruby stdout" in test_pages[
        "src__test__unit__ruby__arq_list_wildcard_excludes_unit_test_rb.html"
    ]
    assert "unit--ruby.html" in test_pages
    assert "ruby runner transcript" in test_pages["unit--ruby.html"]
    assert "1 runs, 1 assertions" in test_pages["unit--ruby.html"]
    assert "Line Coverage: 98.86%" not in test_pages["unit--ruby.html"]
    assert "unit--ruby.html" in coverage_pages
    assert "Line Coverage: 98.86%" in coverage_pages["unit--ruby.html"]
    assert "1 runs, 1 assertions" not in coverage_pages["unit--ruby.html"]
    assert "unit--python.html" in test_pages
    assert "collected 2 items" in test_pages["unit--python.html"]
    assert "1 failed, 1 passed in 0.42s" in test_pages["unit--python.html"]
    assert "Name                                      Stmts" not in test_pages["unit--python.html"]
    assert "unit--python.html" in coverage_pages
    assert "Name                                      Stmts" in coverage_pages["unit--python.html"]
    assert "collected 2 items" not in coverage_pages["unit--python.html"]

    summary_text = summary_file.read_text(encoding="utf-8")
    assert "unit\tzsh\ttests.failed\t1" in summary_text
    assert "unit\tbash\ttests.skipped\t1" in summary_text
    assert "all\tpython\tcoverage.gate.failures\t0" in summary_text
    assert "all\tnix\tcoverage.gate.failures\t1" in summary_text
    assert "all\tnix\tcoverage.gate.warnings\t0" in summary_text
    assert "Coverage gates" in homepage
