from __future__ import annotations

from pathlib import Path


def test_homepage_renders_two_rooted_trees_and_overflow_rules(load_module, tmp_path: Path):
    model = load_module("tools/test/report/model.py", "model")
    trees = load_module("tools/test/report/trees.py")
    pages = load_module("tools/test/report/pages.py")
    tests = [
        model.TestCaseResult(
            "unit",
            "python",
            Path("src/test/unit/python/test_report_renderer.unit.test.py"),
            "test_report_renderer",
            "test_escapes",
            "passed",
            0.1,
        ),
    ]
    coverage = [
        model.CoverageRecord(
            "all",
            "nix",
            Path("modules/kitty.nix"),
            (model.CoverageMetric("source-target", 1, 1),),
            "source-target",
        ),
    ]

    output_dir = tmp_path / "report"
    pages.render_report_homepage(
        output_dir,
        coverage_tree=trees.build_coverage_tree(coverage),
        test_tree=trees.build_test_tree(tests),
    )

    html = (output_dir / "index.html").read_text(encoding="utf-8")
    css = (output_dir / "static" / "report.css").read_text(encoding="utf-8")
    assert "Coverage: source tree" in html
    assert "Test results: test file tree" in html
    assert "Static host-safe report" in html
    assert "report-badges" in html
    assert "Overall run: UNKNOWN (not recorded)" in html
    assert "Parsed tests: 1/1 passed" in html
    assert 'src="static/report.js" defer' in html
    assert 'data-tree-id="coverage"' in html
    assert 'data-tree-id="tests"' in html
    assert "modules/kitty.nix" in html
    assert "src/test/unit/python/test_report_renderer.unit.test.py" in html
    assert "color-scheme: dark" in css
    assert "#0f1419" in css
    assert "status-badge" in css
    assert "minmax(0, 1fr)" in css
    assert "text-overflow: ellipsis" in css
    assert "overflow-x: auto" in css
    assert (output_dir / "static" / "report.js").is_file()


def test_homepage_renders_expandable_trees_with_linked_test_case_rows(load_module, tmp_path: Path):
    model = load_module("tools/test/report/model.py", "model")
    trees = load_module("tools/test/report/trees.py")
    pages = load_module("tools/test/report/pages.py")
    test_path = Path("src/test/unit/python/test_report_pages.unit.test.py")
    tests = [
        model.TestCaseResult(
            "unit",
            "python",
            test_path,
            "test_report_pages",
            "test_passes",
            "passed",
            0.1,
        ),
        model.TestCaseResult(
            "unit",
            "python",
            test_path,
            "test_report_pages",
            "test_fails",
            "failed",
            0.2,
            failure_message="boom",
        ),
    ]
    source_path = Path("modules/kitty.nix")
    coverage = [
        model.CoverageRecord(
            "all",
            "nix",
            source_path,
            (model.CoverageMetric("source-target", 1, 1),),
            "source-target",
        ),
    ]

    pages.render_report_homepage(
        tmp_path,
        coverage_tree=trees.build_coverage_tree(coverage),
        test_tree=trees.build_test_tree(tests),
        coverage_links={source_path: "coverage/modules__kitty_nix--all--nix--source-target--1.html"},
        test_links={test_path: "tests/src__test__unit__python__test_report_pages_unit_test_py.html"},
    )

    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    css = (tmp_path / "static" / "report.css").read_text(encoding="utf-8")
    js = (tmp_path / "static" / "report.js").read_text(encoding="utf-8")
    assert '<details class="tree-node">' in html
    assert '<details class="tree-node" open>' not in html
    assert '<summary class="test-grid row tree-summary">' in html
    assert '<span class="tree-toggle" aria-hidden="true"></span>' in html
    assert '<div class="tree-children">' in html
    assert 'class="test-grid row test-case-row"' in html
    assert "test_passes" in html
    assert "test_fails" in html
    assert 'href="tests/src__test__unit__python__test_report_pages_unit_test_py.html#case-1"' in html
    assert 'href="tests/src__test__unit__python__test_report_pages_unit_test_py.html#case-2"' in html
    assert "details[open] > summary .tree-toggle::before" in css
    assert "window.sessionStorage" in js
    assert "nix-darwin-test-report:tree-state" in js


def test_homepage_report_badges_show_failed_tests_and_failed_gates(load_module, tmp_path: Path):
    model = load_module("tools/test/report/model.py", "model")
    trees = load_module("tools/test/report/trees.py")
    pages = load_module("tools/test/report/pages.py")
    tests = [
        model.TestCaseResult(
            "unit",
            "python",
            Path("src/test/unit/python/example.unit.test.py"),
            "example",
            "test_failure",
            "failed",
            0.1,
        ),
    ]
    gates = [
        model.CoverageGateResult(
            kind="hard",
            severity="fail",
            scope="unit",
            language="python",
            metric="statement",
            covered=50,
            total=100,
            threshold_percent=85.0,
        ),
    ]

    pages.render_report_homepage(
        tmp_path,
        coverage_tree=trees.build_coverage_tree([]),
        test_tree=trees.build_test_tree(tests),
        coverage_gates=gates,
    )

    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "Parsed tests: 0/1 passed" in html
    assert "Coverage gates: 1 failed" in html
    assert "status-badge status-failed" in html


def test_homepage_runner_failure_is_load_bearing_and_output_is_escaped(load_module, tmp_path: Path):
    model = load_module("tools/test/report/model.py", "model")
    trees = load_module("tools/test/report/trees.py")
    pages = load_module("tools/test/report/pages.py")
    output_log = Path("runner/001-flake-check/output.txt")
    (tmp_path / output_log).parent.mkdir(parents=True)
    (tmp_path / output_log).write_text("error: <bad & diagnostic>\n", encoding="utf-8")
    runner = model.RunnerReport(
        model.ExecutionStatus("fail", 73),
        (model.RunnerStepResult(1, "flake <check>", "fail", 73, output_log),),
        (model.ExecutionFailure(1, "flake <check>", 73),),
    )

    pages.render_runner_step_pages(tmp_path, runner)
    pages.render_report_homepage(
        tmp_path,
        coverage_tree=trees.build_coverage_tree([]),
        test_tree=trees.build_test_tree([]),
        runner_report=runner,
        coverage_override=True,
    )

    homepage = (tmp_path / "index.html").read_text(encoding="utf-8")
    drilldown = (tmp_path / "runner" / "001-flake_check.html").read_text(encoding="utf-8")
    assert "Overall run: FAIL" in homepage
    assert "Parsed tests: none" in homepage
    assert "flake &lt;check&gt;" in homepage
    assert 'href="runner/001-flake_check.html"' in homepage
    assert "Coverage override: used" in homepage
    assert "operator accepted a coverage-gate override" in homepage
    assert "error: &lt;bad &amp; diagnostic&gt;" in drilldown
    assert "error: <bad & diagnostic>" not in drilldown


def test_homepage_links_python_run_overviews_without_opening_trees(
    load_module,
    tmp_path: Path,
):
    model = load_module("tools/test/report/model.py", "model")
    trees = load_module("tools/test/report/trees.py")
    pages = load_module("tools/test/report/pages.py")
    test_path = Path("src/test/unit/python/test_report_pages.unit.test.py")
    overview = model.LanguageRunOverview(
        scope="unit",
        language="python",
        output_log=tmp_path / "unit" / "python" / "output.txt",
        test_output="pytest progress",
        coverage_output="coverage table",
    )
    tests = [
        model.TestCaseResult(
            "unit",
            "python",
            test_path,
            "test_report_pages",
            "test_passes",
            "passed",
            0.1,
        ),
    ]

    pages.render_report_homepage(
        tmp_path,
        coverage_tree=trees.build_coverage_tree([]),
        test_tree=trees.build_test_tree(tests),
        test_links={
            Path("src/test/unit/python"): "tests/unit--python.html",
            test_path: "tests/src__test__unit__python__test_report_pages_unit_test_py.html",
        },
        run_overviews=[overview],
    )

    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "Suite overviews" in html
    assert 'href="tests/unit--python.html"' in html
    assert 'href="coverage/unit--python.html"' in html
    assert '<details class="tree-node" open>' not in html


def test_homepage_renders_coverage_gate_results(load_module, tmp_path: Path):
    model = load_module("tools/test/report/model.py", "model")
    trees = load_module("tools/test/report/trees.py")
    pages = load_module("tools/test/report/pages.py")
    gates = [
        model.CoverageGateResult(
            kind="hard",
            severity="pass",
            scope="all",
            language="python",
            metric="statement",
            covered=90,
            total=100,
            threshold_percent=85.0,
        ),
        model.CoverageGateResult(
            kind="warning",
            severity="warn",
            scope="unit",
            language="python",
            metric="branch",
            covered=80,
            total=100,
            threshold_percent=85.0,
        ),
    ]

    pages.render_report_homepage(
        tmp_path,
        coverage_tree=trees.build_coverage_tree([]),
        test_tree=trees.build_test_tree([]),
        coverage_gates=gates,
    )

    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    css = (tmp_path / "static" / "report.css").read_text(encoding="utf-8")
    assert "Coverage gates" in html
    assert "all" in html
    assert "python" in html
    assert "statement" in html
    assert "90 / 100 (90.00%)" in html
    assert "unit" in html
    assert "branch" in html
    assert "80 / 100 (80.00%)" in html
    assert "status-warn" in html
    assert "Coverage gates: 1 warning" in html
    assert ".gate-grid" in css


def test_homepage_links_leaf_rows_when_link_maps_are_provided(load_module, tmp_path: Path):
    model = load_module("tools/test/report/model.py", "model")
    trees = load_module("tools/test/report/trees.py")
    pages = load_module("tools/test/report/pages.py")
    test_path = Path("src/test/unit/python/test_report_renderer.unit.test.py")
    source_path = Path("modules/kitty.nix")
    tests = [
        model.TestCaseResult(
            "unit",
            "python",
            test_path,
            "test_report_renderer",
            "test_links",
            "passed",
            0.1,
        ),
    ]
    coverage = [
        model.CoverageRecord(
            "all",
            "nix",
            source_path,
            (model.CoverageMetric("source-target", 1, 1),),
            "source-target",
        ),
    ]

    pages.render_report_homepage(
        tmp_path,
        coverage_tree=trees.build_coverage_tree(coverage),
        test_tree=trees.build_test_tree(tests),
        coverage_links={source_path: "coverage/modules__kitty_nix--all--nix--source-target--1.html"},
        test_links={test_path: "tests/src__test__unit__python__test_report_renderer_unit_test_py.html"},
    )

    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert (
        'href="coverage/modules__kitty_nix--all--nix--source-target--1.html"'
        in html
    )
    assert (
        'href="tests/src__test__unit__python__test_report_renderer_unit_test_py.html"'
        in html
    )
    assert '<a class="tree-link"' in html


def test_homepage_renders_duplicate_coverage_records_as_distinct_linked_rows(load_module, tmp_path: Path):
    model = load_module("tools/test/report/model.py", "model")
    trees = load_module("tools/test/report/trees.py")
    pages = load_module("tools/test/report/pages.py")
    source_path = Path(".")
    coverage = [
        model.CoverageRecord(
            "unit",
            "python",
            source_path,
            (model.CoverageMetric("statement", 750, 1000),),
            "native",
        ),
        model.CoverageRecord(
            "unit",
            "ruby",
            source_path,
            (model.CoverageMetric("statement", 900, 1000),),
            "native",
        ),
    ]

    pages.render_report_homepage(
        tmp_path,
        coverage_tree=trees.build_coverage_tree(coverage),
        test_tree=trees.build_test_tree([]),
        coverage_links={
            (source_path, 1): "coverage/root--unit--python--native--1.html",
            (source_path, 2): "coverage/root--unit--ruby--native--2.html",
        },
    )

    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'href="coverage/root--unit--python--native--1.html"' in html
    assert 'href="coverage/root--unit--ruby--native--2.html"' in html
    assert "python" in html
    assert "ruby" in html
    assert "750 / 1000 (75%)" in html
    assert "900 / 1000 (90%)" in html


def test_homepage_escapes_dynamic_tree_text_without_replacing_placeholder_like_values(
    load_module,
    tmp_path: Path,
):
    model = load_module("tools/test/report/model.py", "model")
    trees = load_module("tools/test/report/trees.py")
    pages = load_module("tools/test/report/pages.py")
    tests = [
        model.TestCaseResult(
            "unit<html>",
            "python&pytest",
            Path("src/test/{{ coverage_tree }} <renderer&escape>.unit.test.py"),
            "test_report_renderer",
            "test_escapes",
            "passed",
            0.1,
        ),
    ]
    coverage = [
        model.CoverageRecord(
            "all<html>",
            "nix&lang",
            Path("modules/{{ test_tree }} <kitty&co>.nix"),
            (
                model.CoverageMetric(
                    "source-target {{ coverage_tree }} <detail&kind>",
                    1,
                    1,
                ),
            ),
            "source-target {{ coverage_tree }} <detail&kind>",
        ),
    ]

    output_dir = tmp_path / "report"
    pages.render_report_homepage(
        output_dir,
        coverage_tree=trees.build_coverage_tree(coverage),
        test_tree=trees.build_test_tree(tests),
    )

    rendered = (output_dir / "index.html").read_text(encoding="utf-8")
    assert "modules/{{ test_tree }} &lt;kitty&amp;co&gt;.nix" in rendered
    assert "src/test/{{ coverage_tree }} &lt;renderer&amp;escape&gt;.unit.test.py" in rendered
    assert "all&lt;html&gt;" in rendered
    assert "nix&amp;lang" in rendered
    assert "source-target {{ coverage_tree }} &lt;detail&amp;kind&gt;" in rendered
    assert "all<html>" not in rendered
    assert "nix&lang" not in rendered
    assert "<kitty&co>" not in rendered
    assert "<renderer&escape>" not in rendered
    assert "modules/<div class=\"test-grid row\">" not in rendered


def test_coverage_drilldown_renders_nix_source_targets(load_module, tmp_path: Path):
    model = load_module("tools/test/report/model.py", "model")
    pages = load_module("tools/test/report/pages.py")
    record = model.CoverageRecord(
        scope="all",
        language="nix",
        source_path=Path("modules/kitty.nix"),
        metrics=(model.CoverageMetric("source-target", 1, 1),),
        detail_kind="source-target",
        detail_rows=({"target": "modules/kitty.nix", "status": "covered"},),
    )

    pages.render_coverage_drilldown_pages(tmp_path, [record])

    generated = list((tmp_path / "coverage").glob("*.html"))
    assert len(generated) == 1
    assert generated[0].name == "modules__kitty_nix--all--nix--source-target--1.html"
    html = generated[0].read_text(encoding="utf-8")
    assert 'href="../index.html"' in html
    assert "modules/kitty.nix" in html
    assert "source-target" in html
    assert "covered" in html
    assert "code-block" in html


def test_coverage_drilldown_escapes_dynamic_detail_metric_and_path_text(
    load_module,
    tmp_path: Path,
):
    model = load_module("tools/test/report/model.py", "model")
    pages = load_module("tools/test/report/pages.py")

    class UnsafeMetric:
        name = 'line <metric&"name">'
        display = '5 < 7 & "quoted"'

    record = model.CoverageRecord(
        scope="all<section>",
        language="nix&lang",
        source_path=Path('modules/<kitty&"co">.nix'),
        metrics=(UnsafeMetric(),),
        detail_kind='source-target "detail"&<kind>',
        detail_rows=({'target<key>': 'modules/<kitty&"co">.nix'},),
    )

    pages.render_coverage_drilldown_pages(tmp_path, [record])

    generated = list((tmp_path / "coverage").glob("*.html"))
    assert len(generated) == 1
    html = generated[0].read_text(encoding="utf-8")
    assert (
        "<title>Coverage: modules/&lt;kitty&amp;&quot;co&quot;&gt;.nix</title>"
        in html
    )
    assert "coverage / modules / &lt;kitty&amp;&quot;co&quot;&gt;.nix" in html
    assert "all&lt;section&gt;" in html
    assert "nix&amp;lang" in html
    assert "modules/&lt;kitty&amp;&quot;co&quot;&gt;.nix" in html
    assert 'title="modules/&lt;kitty&amp;&quot;co&quot;&gt;.nix"' in html
    assert "source-target &quot;detail&quot;&amp;&lt;kind&gt;" in html
    assert (
        "line &lt;metric&amp;&quot;name&quot;&gt;: 5 &lt; 7 &amp; &quot;quoted&quot;"
        in html
    )
    assert "target&lt;key&gt;" in html
    assert "<kitty&" not in html
    assert "<metric&" not in html
    assert "<section>" not in html


def test_coverage_drilldown_filenames_disambiguate_duplicate_source_paths(
    load_module,
    tmp_path: Path,
):
    model = load_module("tools/test/report/model.py", "model")
    pages = load_module("tools/test/report/pages.py")
    records = [
        model.CoverageRecord(
            "all",
            "python",
            Path("."),
            (model.CoverageMetric("line", 1, 2),),
            "line",
            ({"marker": "python row"},),
        ),
        model.CoverageRecord(
            "all",
            "ruby",
            Path("."),
            (model.CoverageMetric("line", 2, 3),),
            "line",
            ({"marker": "ruby row"},),
        ),
    ]

    pages.render_coverage_drilldown_pages(tmp_path, records)

    coverage_dir = tmp_path / "coverage"
    generated = sorted(coverage_dir.glob("*.html"))
    assert [path.name for path in generated] == [
        "root--all--python--line--1.html",
        "root--all--ruby--line--2.html",
    ]
    assert all(path.parent == coverage_dir for path in generated)
    assert "python row" in generated[0].read_text(encoding="utf-8")
    assert "ruby row" in generated[1].read_text(encoding="utf-8")


def test_coverage_drilldown_links_native_report_inside_report_dir(load_module, tmp_path: Path):
    model = load_module("tools/test/report/model.py", "model")
    pages = load_module("tools/test/report/pages.py")
    native_report = tmp_path / "unit" / "python" / "html" / "index.html"
    native_report.parent.mkdir(parents=True)
    native_report.write_text("<html>native python coverage</html>\n", encoding="utf-8")
    record = model.CoverageRecord(
        "unit",
        "python",
        Path("."),
        (model.CoverageMetric("statement", 9, 10),),
        "native",
        native_report=native_report,
    )

    pages.render_coverage_drilldown_pages(tmp_path, [record])

    generated = list((tmp_path / "coverage").glob("*.html"))
    assert len(generated) == 1
    html = generated[0].read_text(encoding="utf-8")
    assert "Native coverage report" in html
    assert 'href="../unit/python/html/index.html"' in html
    assert "unit/python/html/index.html" in html


def test_coverage_drilldown_omits_native_report_outside_report_dir(load_module, tmp_path: Path):
    model = load_module("tools/test/report/model.py", "model")
    pages = load_module("tools/test/report/pages.py")
    outside_report = tmp_path.parent / "outside-coverage.html"
    record = model.CoverageRecord(
        "unit",
        "python",
        Path("."),
        (model.CoverageMetric("statement", 9, 10),),
        "native",
        native_report=outside_report,
    )

    pages.render_coverage_drilldown_pages(tmp_path, [record])

    generated = list((tmp_path / "coverage").glob("*.html"))
    assert len(generated) == 1
    html = generated[0].read_text(encoding="utf-8")
    assert "Native coverage report" not in html
    assert "outside-coverage.html" not in html


def test_coverage_page_filename_keeps_absolute_paths_under_coverage(load_module):
    model = load_module("tools/test/report/model.py", "model")
    pages = load_module("tools/test/report/pages.py")
    record = model.CoverageRecord(
        "all",
        "nix",
        Path("/tmp/a.nix"),
        (model.CoverageMetric("source-target", 1, 1),),
        "source-target",
    )

    filename = pages.coverage_page_filename(record, 1)

    assert filename == "tmp__a_nix--all--nix--source-target--1.html"
    assert not Path(filename).is_absolute()
    assert Path(filename).parts == (filename,)


def test_coverage_drilldown_absolute_source_paths_stay_under_coverage(
    load_module,
    tmp_path: Path,
):
    model = load_module("tools/test/report/model.py", "model")
    pages = load_module("tools/test/report/pages.py")
    record = model.CoverageRecord(
        "all",
        "nix",
        Path("/tmp/a.nix"),
        (model.CoverageMetric("source-target", 1, 1),),
        "source-target",
    )

    pages.render_coverage_drilldown_pages(tmp_path, [record])

    coverage_dir = tmp_path / "coverage"
    generated = list(coverage_dir.glob("*.html"))
    assert len(generated) == 1
    assert generated[0].parent == coverage_dir
    assert generated[0].name == "tmp__a_nix--all--nix--source-target--1.html"


def test_test_drilldown_escapes_console_output_and_failures(load_module, tmp_path: Path):
    model = load_module("tools/test/report/model.py", "model")
    pages = load_module("tools/test/report/pages.py")
    test = model.TestCaseResult(
        scope="unit",
        language="python",
        test_path=Path("src/test/unit/python/test_report_renderer.unit.test.py"),
        module="src.test.unit.python.test_report_renderer.unit.test",
        name="test_failure_output",
        status="failed",
        time=0.2,
        stdout="<stdout>",
        stderr="<stderr>",
        failure="<stack trace>",
        failure_message="boom",
        report_phases=(
            {"phase": "setup", "outcome": "passed", "duration": "0.001s"},
            {"phase": "call", "outcome": "failed", "duration": "0.020s", "message": "boom"},
        ),
    )

    pages.render_test_drilldown_pages(tmp_path, [test])

    generated = list((tmp_path / "tests").glob("*.html"))
    assert len(generated) == 1
    assert generated[0].name == "src__test__unit__python__test_report_renderer_unit_test_py.html"
    html = generated[0].read_text(encoding="utf-8")
    assert 'id="case-1"' in html
    assert "test_failure_output" in html
    assert "Per-test captured output" in html
    assert "Captured stdout" in html
    assert "Captured stderr" in html
    assert "Failure / traceback" in html
    assert "Pytest report phases" in html
    assert "setup: passed (0.001s)" in html
    assert "call: failed (0.020s)" in html
    assert "&lt;stdout&gt;" in html
    assert "&lt;stderr&gt;" in html
    assert "&lt;stack trace&gt;" in html
    assert "code-block" in html


def test_test_drilldown_renders_clean_per_test_captured_output(
    load_module,
    tmp_path: Path,
):
    model = load_module("tools/test/report/model.py", "model")
    pages = load_module("tools/test/report/pages.py")
    test = model.TestCaseResult(
        scope="unit",
        language="bash",
        test_path=Path("src/test/unit/bash/tool.unit.test.bats"),
        module="tool.unit.test.bats",
        name="reports separate channels",
        status="failed",
        time=0.2,
        failure_message="assertion failed",
        assertion_stdout="captured log",
        stdout="captured stdout",
        stderr="captured stderr",
        failure="assertion stack",
    )

    pages.render_test_drilldown_pages(tmp_path, [test])

    html = (tmp_path / "tests" / "src__test__unit__bash__tool_unit_test_bats.html").read_text(encoding="utf-8")
    assert "Per-test captured output" in html
    assert "Failure message" in html
    assert "assertion failed" in html
    assert "Captured log" in html
    assert "captured log" in html
    assert "Captured stdout" in html
    assert "captured stdout" in html
    assert "Captured stderr" in html
    assert "captured stderr" in html
    assert "Failure / traceback" in html
    assert "assertion stack" in html


def test_test_drilldown_renders_source_excerpt(load_module, tmp_path: Path):
    model = load_module("tools/test/report/model.py", "model")
    pages = load_module("tools/test/report/pages.py")
    test = model.TestCaseResult(
        scope="unit",
        language="python",
        test_path=Path("src/test/unit/python/example.unit.test.py"),
        module="src.test.unit.python.example.unit.test",
        name="test_example",
        status="passed",
        time=0.1,
        source_excerpt="def test_example():\n    assert run_tool() == 0\n",
    )

    pages.render_test_drilldown_pages(tmp_path, [test])

    html = (tmp_path / "tests" / "src__test__unit__python__example_unit_test_py.html").read_text(encoding="utf-8")
    assert "Source excerpt" in html
    assert "def test_example():" in html
    assert "assert run_tool() == 0" in html
    assert "Per-test captured output" in html
    assert "Runner transcript" not in html


def test_python_test_drilldown_omits_suite_runner_transcript(load_module, tmp_path: Path):
    model = load_module("tools/test/report/model.py", "model")
    pages = load_module("tools/test/report/pages.py")
    execution_log = tmp_path / "unit" / "python" / "output.txt"
    execution_log.parent.mkdir(parents=True)
    execution_log.write_text("pytest full suite transcript\n", encoding="utf-8")
    test = model.TestCaseResult(
        scope="unit",
        language="python",
        test_path=Path("src/test/unit/python/example.unit.test.py"),
        module="src.test.unit.python.example.unit.test",
        name="test_example",
        status="passed",
        time=0.1,
        execution_log=execution_log,
    )

    pages.render_test_drilldown_pages(tmp_path, [test])

    html = (tmp_path / "tests" / "src__test__unit__python__example_unit_test_py.html").read_text(encoding="utf-8")
    assert "Runner transcript" not in html
    assert "pytest full suite transcript" not in html
    assert "No per-test stdout, stderr, log, or failure output was captured for this test case." in html


def test_run_overview_pages_render_split_pytest_runner_transcript(load_module, tmp_path: Path):
    model = load_module("tools/test/report/model.py", "model")
    pages = load_module("tools/test/report/pages.py")
    output_log = tmp_path / "unit" / "python" / "output.txt"
    output_log.parent.mkdir(parents=True)
    output_log.write_text("full raw pytest output\n", encoding="utf-8")
    overview = model.LanguageRunOverview(
        scope="unit",
        language="python",
        output_log=output_log,
        test_output="collected 174 items\n174 passed in 32.44s",
        coverage_output="Name Stmts Miss Cover\nrender.py 10 0 100%",
    )

    pages.render_test_run_overview_pages(tmp_path, [overview])
    pages.render_coverage_run_overview_pages(tmp_path, [overview])

    test_html = (tmp_path / "tests" / "unit--python.html").read_text(encoding="utf-8")
    coverage_html = (tmp_path / "coverage" / "unit--python.html").read_text(encoding="utf-8")
    assert "Test Results: unit/python" in test_html
    assert "collected 174 items" in test_html
    assert "174 passed in 32.44s" in test_html
    assert "Name Stmts Miss Cover" not in test_html
    assert 'href="../unit/python/output.txt"' in test_html
    assert "Coverage: unit/python" in coverage_html
    assert "Name Stmts Miss Cover" in coverage_html
    assert "collected 174 items" not in coverage_html
    assert 'href="../unit/python/output.txt"' in coverage_html


def test_run_overview_pages_render_multiple_bats_transcripts(load_module, tmp_path: Path):
    model = load_module("tools/test/report/model.py", "model")
    pages = load_module("tools/test/report/pages.py")
    alpha_log = tmp_path / "unit" / "bash" / "alpha.unit.test.bats" / "output.txt"
    beta_log = tmp_path / "unit" / "bash" / "beta.unit.test.bats" / "output.txt"
    alpha_log.parent.mkdir(parents=True)
    beta_log.parent.mkdir(parents=True)
    alpha_log.write_text("1..1\nok 1 alpha\n", encoding="utf-8")
    beta_log.write_text("1..1\nok 1 beta\n", encoding="utf-8")
    overview = model.LanguageRunOverview(
        scope="unit",
        language="bash",
        sections=(
            model.RunOverviewSection("alpha.unit.test.bats", alpha_log, "1..1\nok 1 alpha\n"),
            model.RunOverviewSection("beta.unit.test.bats", beta_log, "1..1\nok 1 beta\n"),
        ),
    )

    pages.render_test_run_overview_pages(tmp_path, [overview])

    test_html = (tmp_path / "tests" / "unit--bash.html").read_text(encoding="utf-8")
    assert "Test Results: unit/bash" in test_html
    assert "alpha.unit.test.bats" in test_html
    assert "beta.unit.test.bats" in test_html
    assert "ok 1 alpha" in test_html
    assert "ok 1 beta" in test_html
    assert 'href="../unit/bash/alpha.unit.test.bats/output.txt"' in test_html
    assert 'href="../unit/bash/beta.unit.test.bats/output.txt"' in test_html


def test_test_drilldown_uses_runner_transcript_as_per_case_fallback(
    load_module,
    tmp_path: Path,
):
    model = load_module("tools/test/report/model.py", "model")
    pages = load_module("tools/test/report/pages.py")
    execution_log = tmp_path / "unit" / "bash" / "tool.unit.test.bats" / "output.txt"
    execution_log.parent.mkdir(parents=True)
    execution_log.write_text("bats full transcript\nrun fake-command\nstatus=0\n", encoding="utf-8")
    test = model.TestCaseResult(
        "unit",
        "bash",
        Path("src/test/unit/bash/tool.unit.test.bats"),
        "tool.unit.test.bats",
        "runs fake command",
        "passed",
        0.1,
        execution_log=execution_log,
    )

    pages.render_test_drilldown_pages(tmp_path, [test])

    html = (tmp_path / "tests" / "src__test__unit__bash__tool_unit_test_bats.html").read_text(encoding="utf-8")
    assert "runs fake command" in html
    assert "Runner transcript" in html
    assert "bats full transcript" in html
    assert "run fake-command" in html
    assert "No per-test stdout, stderr, log, or failure output was captured for this test case." in html


def test_test_drilldown_renders_gathered_bats_case_log(
    load_module,
    tmp_path: Path,
):
    model = load_module("tools/test/report/model.py", "model")
    pages = load_module("tools/test/report/pages.py")
    case_output_log = tmp_path / "unit" / "bash" / "tool.unit.test.bats" / "test-outputs" / "1-runs fake command.log"
    case_output_log.parent.mkdir(parents=True)
    case_output_log.write_text("fake stdout\nfake stderr\n", encoding="utf-8")
    test = model.TestCaseResult(
        "unit",
        "bash",
        Path("src/test/unit/bash/tool.unit.test.bats"),
        "tool.unit.test.bats",
        "runs fake command",
        "passed",
        0.1,
        case_output_log=case_output_log,
    )

    pages.render_test_drilldown_pages(tmp_path, [test])

    html = (tmp_path / "tests" / "src__test__unit__bash__tool_unit_test_bats.html").read_text(encoding="utf-8")
    assert "Per-test Bats log" in html
    assert "fake stdout" in html
    assert "fake stderr" in html
    assert 'href="../unit/bash/tool.unit.test.bats/test-outputs/1-runs fake command.log"' in html


def test_test_drilldown_passing_test_without_output_shows_empty_message(
    load_module,
    tmp_path: Path,
):
    model = load_module("tools/test/report/model.py", "model")
    pages = load_module("tools/test/report/pages.py")
    test = model.TestCaseResult(
        "unit",
        "python",
        Path("src/test/unit/python/test_report_pages.unit.test.py"),
        "test_report_pages",
        "test_clean_pass",
        "passed",
        0.1,
    )

    pages.render_test_drilldown_pages(tmp_path, [test])

    generated = list((tmp_path / "tests").glob("*.html"))
    assert len(generated) == 1
    assert generated[0].name == "src__test__unit__python__test_report_pages_unit_test_py.html"
    html = generated[0].read_text(encoding="utf-8")
    assert "test_clean_pass" in html
    assert "No per-test stdout, stderr, log, or failure output was captured for this test case." in html


def test_test_drilldown_groups_multiple_cases_for_one_test_file(
    load_module,
    tmp_path: Path,
):
    model = load_module("tools/test/report/model.py", "model")
    pages = load_module("tools/test/report/pages.py")
    tests = [
        model.TestCaseResult(
            "unit",
            "python",
            Path("src/test/unit/python/test_report_pages.unit.test.py"),
            "test_report_pages",
            "test_passes",
            "passed",
            0.1,
        ),
        model.TestCaseResult(
            "unit",
            "python",
            Path("src/test/unit/python/test_report_pages.unit.test.py"),
            "test_report_pages",
            "test_fails",
            "failed",
            0.2,
            failure_message="boom",
        ),
    ]

    pages.render_test_drilldown_pages(tmp_path, tests)

    generated = list((tmp_path / "tests").glob("*.html"))
    assert len(generated) == 1
    html = generated[0].read_text(encoding="utf-8")
    assert "Total" in html
    assert "Passed" in html
    assert "Failed" in html
    assert "test_passes" in html
    assert "test_fails" in html
    assert "boom" in html


def test_test_drilldown_escapes_case_metadata_and_output(load_module, tmp_path: Path):
    model = load_module("tools/test/report/model.py", "model")
    pages = load_module("tools/test/report/pages.py")
    test = model.TestCaseResult(
        scope='unit<section>',
        language='python&"pytest"',
        test_path=Path('src/test/<report&"pages">.unit.test.py'),
        module='module.<unsafe&"value">',
        name='test_<name&"quoted">',
        status='failed<script>alert("x")</script>',
        time=0.3,
        stdout='stdout <tag&"value">',
        stderr='stderr <tag&"value">',
        failure='trace <tag&"value">',
        failure_message='message <tag&"value">',
    )

    pages.render_test_drilldown_pages(tmp_path, [test])

    generated = list((tmp_path / "tests").glob("*.html"))
    assert len(generated) == 1
    html = generated[0].read_text(encoding="utf-8")
    assert "unit&lt;section&gt;" in html
    assert "python&amp;&quot;pytest&quot;" in html
    assert "src/test/&lt;report&amp;&quot;pages&quot;&gt;.unit.test.py" in html
    assert "module.&lt;unsafe&amp;&quot;value&quot;&gt;" in html
    assert "test_&lt;name&amp;&quot;quoted&quot;&gt;" in html
    assert "failed&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html
    assert "stdout &lt;tag&amp;&quot;value&quot;&gt;" in html
    assert "stderr &lt;tag&amp;&quot;value&quot;&gt;" in html
    assert "trace &lt;tag&amp;&quot;value&quot;&gt;" in html
    assert "message &lt;tag&amp;&quot;value&quot;&gt;" in html
    assert "<section>" not in html
    assert "<script>" not in html
    assert "<tag&" not in html


def test_test_drilldown_absolute_and_weird_paths_stay_under_tests(
    load_module,
    tmp_path: Path,
):
    model = load_module("tools/test/report/model.py", "model")
    pages = load_module("tools/test/report/pages.py")
    tests = [
        model.TestCaseResult(
            "unit",
            "python",
            Path("/tmp/a.py"),
            "tmp.a",
            "test_absolute",
            "passed",
            0.1,
        ),
        model.TestCaseResult(
            "unit",
            "python",
            Path("../tmp/a.py"),
            "tmp.a",
            "test_parent",
            "passed",
            0.1,
        ),
    ]

    pages.render_test_drilldown_pages(tmp_path, tests)

    tests_dir = tmp_path / "tests"
    generated = sorted(tests_dir.glob("*.html"))
    assert len(generated) == 2
    assert all(path.parent == tests_dir for path in generated)
    assert {path.name for path in generated} == {
        "tmp__a_py.html",
        "tmp__a_py--2.html",
    }
