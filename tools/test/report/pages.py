from __future__ import annotations

import html
import re
import shutil
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = SCRIPT_DIR / "templates"
STATIC_DIR = SCRIPT_DIR / "static"
MODEL_MODULE_NAME = "_nix_darwin_test_report_model"


def load_sibling_model():
    if MODEL_MODULE_NAME in sys.modules:
        return sys.modules[MODEL_MODULE_NAME]

    model_path = SCRIPT_DIR / "model.py"
    spec = spec_from_file_location(MODEL_MODULE_NAME, model_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load test report model from {model_path}")
    model = module_from_spec(spec)
    sys.modules[MODEL_MODULE_NAME] = model
    spec.loader.exec_module(model)
    return model


MODEL = load_sibling_model()
CoverageMetric = MODEL.CoverageMetric
CoverageGateResult = MODEL.CoverageGateResult
CoverageRecord = MODEL.CoverageRecord
LanguageRunOverview = MODEL.LanguageRunOverview
TestCaseResult = MODEL.TestCaseResult
TestTotals = MODEL.TestTotals
TreeNode = MODEL.TreeNode
ExecutionStatus = MODEL.ExecutionStatus
RunnerReport = MODEL.RunnerReport
RunnerStepResult = MODEL.RunnerStepResult
TEMPLATE_TOKEN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


def template_path(name: str) -> Path:
    return TEMPLATE_DIR / name


def render_template(path: Path, values: dict[str, str]) -> str:
    template = path.read_text(encoding="utf-8")

    def replace_token(match: re.Match[str]) -> str:
        key = match.group(1)
        return values.get(key, match.group(0))

    return TEMPLATE_TOKEN.sub(replace_token, template)


def ensure_static_assets(output_dir: Path) -> None:
    static_output_dir = output_dir / "static"
    static_output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(STATIC_DIR / "report.css", static_output_dir / "report.css")
    shutil.copyfile(STATIC_DIR / "report.js", static_output_dir / "report.js")


def _display_path(path: Path) -> str:
    return path.as_posix()


def slug_for_path(path: Path) -> str:
    return "__".join(path.parts).replace(".", "_")


def _safe_filename_part(value: str, fallback: str) -> str:
    safe = FILENAME_UNSAFE.sub("_", value).strip("_")
    return safe or fallback


def _safe_path_slug(path: Path) -> str:
    slug = slug_for_path(path).lstrip("/\\")
    return _safe_filename_part(slug, "root")


def coverage_page_filename(record: CoverageRecord, ordinal: int) -> str:
    parts = [
        _safe_path_slug(record.source_path),
        _safe_filename_part(record.scope, "scope"),
        _safe_filename_part(record.language, "language"),
        _safe_filename_part(record.detail_kind, "detail"),
        str(ordinal),
    ]
    return f"{'--'.join(parts)}.html"


def test_page_filename(test_path: Path, collision_index: int = 1) -> str:
    slug = _safe_path_slug(test_path)
    if collision_index == 1:
        return f"{slug}.html"
    return f"{slug}--{collision_index}.html"


def run_overview_page_filename(overview: LanguageRunOverview) -> str:
    return (
        f"{_safe_filename_part(overview.scope, 'scope')}"
        f"--{_safe_filename_part(overview.language, 'language')}.html"
    )


def _test_run_overview_href(overview: LanguageRunOverview) -> str:
    return f"tests/{run_overview_page_filename(overview)}"


def _coverage_run_overview_href(overview: LanguageRunOverview) -> str:
    return f"coverage/{run_overview_page_filename(overview)}"


def _indent_style(depth: int) -> str:
    return f"--depth: {depth}"


def _coverage_metrics(records: list[CoverageRecord]) -> tuple[str, str, str, str]:
    if not records:
        return ("", "", "", "")
    record = records[0]
    primary_metric = _primary_metric(record.metrics, record.detail_kind)
    metric_display = primary_metric.display if primary_metric is not None else ""
    return (record.scope, record.language, record.detail_kind, metric_display)


def _primary_metric(metrics: tuple[CoverageMetric, ...], detail_kind: str) -> CoverageMetric | None:
    for metric in metrics:
        if metric.name == detail_kind:
            return metric
    return metrics[0] if metrics else None


def _path_cell(
    display: str,
    depth: int,
    href: str | None,
    *,
    expandable: bool = False,
    extra_class: str = "",
) -> str:
    toggle_class = "tree-toggle" if expandable else "tree-toggle tree-toggle-spacer"
    toggle = f'<span class="{toggle_class}" aria-hidden="true"></span>'
    path_text = html.escape(display)
    if href is not None:
        path_text = f'<a class="tree-link" href="{html.escape(href, quote=True)}">{path_text}</a>'
    classes = "cell path-cell"
    if extra_class:
        classes = f"{classes} {extra_class}"
    return (
        f'  <div class="{html.escape(classes, quote=True)}" style="{html.escape(_indent_style(depth), quote=True)}"'
        f' title="{html.escape(display, quote=True)}">'
        f"{toggle}{path_text}</div>"
    )


def _linked_path_cell(node: TreeNode, depth: int, href: str | None, *, expandable: bool = False) -> str:
    path_display = _display_path(node.path)
    return _path_cell(path_display, depth, href, expandable=expandable)


def _row(grid_class: str, cells: list[str], *, summary: bool = False, extra_class: str = "") -> str:
    tag = "summary" if summary else "div"
    classes = f"{grid_class} row"
    if summary:
        classes = f"{classes} tree-summary"
    if extra_class:
        classes = f"{classes} {extra_class}"
    return "\n".join(
        [
            f'<{tag} class="{html.escape(classes, quote=True)}">',
            *cells,
            f"</{tag}>",
        ],
    )


def _details(summary_row: str, child_rows: list[str]) -> str:
    children = "\n".join(child_rows)
    return "\n".join(
        [
            '<details class="tree-node">',
            summary_row,
            '<div class="tree-children">',
            children,
            "</div>",
            "</details>",
        ],
    )


def _tree_relative_path(root: TreeNode, node: TreeNode) -> Path:
    try:
        return node.path.relative_to(root.path)
    except ValueError:
        return node.path


def _coverage_href(links: dict[object, str], path: Path, occurrence: int) -> str | None:
    return links.get((path, occurrence)) or (links.get(path) if occurrence == 1 else None)


def _coverage_row(
    node: TreeNode,
    depth: int,
    href: str | None = None,
    record: CoverageRecord | None = None,
    *,
    summary: bool = False,
    expandable: bool = False,
) -> str:
    coverage_records = [record] if record is not None else node.coverage_records
    scope, language, detail_kind, metric = _coverage_metrics(coverage_records)
    return _row(
        "tree-grid",
        [
            _linked_path_cell(node, depth, href, expandable=expandable),
            f'  <div class="cell metric-cell">{html.escape(scope)}</div>',
            f'  <div class="cell metric-cell">{html.escape(language)}</div>',
            f'  <div class="cell metric-cell">{html.escape(metric)}</div>',
            f'  <div class="cell metric-cell">{html.escape(detail_kind)}</div>',
        ],
        summary=summary,
    )


def render_coverage_tree_rows(root: TreeNode, links: dict[object, str] | None = None) -> str:
    rows: list[str] = []
    links = links or {}

    def visit(node: TreeNode, depth: int) -> str:
        relative_path = _tree_relative_path(root, node)
        if node.children:
            child_rows: list[str] = []
            for occurrence, record in enumerate(node.coverage_records[1:], start=2):
                child_rows.append(_coverage_row(node, depth + 1, _coverage_href(links, relative_path, occurrence), record))
            for child in sorted(node.children.values(), key=lambda item: item.name):
                child_rows.append(visit(child, depth + 1))
            return _details(
                _coverage_row(
                    node,
                    depth,
                    _coverage_href(links, relative_path, 1),
                    node.coverage_records[0] if node.coverage_records else None,
                    summary=True,
                    expandable=True,
                ),
                child_rows,
            )

        if len(node.coverage_records) > 1:
            leaf_rows = []
            for occurrence, record in enumerate(node.coverage_records, start=1):
                leaf_rows.append(_coverage_row(node, depth, _coverage_href(links, relative_path, occurrence), record))
            return "\n".join(leaf_rows)
        return _coverage_row(node, depth, _coverage_href(links, relative_path, 1))

    rows.append(visit(root, 0))
    return "\n".join(rows)


def _status(totals: TestTotals) -> str:
    if totals.failed:
        return "failed"
    if totals.skipped and totals.skipped == totals.total:
        return "skipped"
    if totals.total:
        return "passed"
    return ""


def _test_row(
    node: TreeNode,
    depth: int,
    href: str | None = None,
    *,
    summary: bool = False,
    expandable: bool = False,
) -> str:
    totals = node.test_totals
    status = _status(totals)
    return _row(
        "test-grid",
        [
            _linked_path_cell(node, depth, href, expandable=expandable),
            f'  <div class="cell metric-cell">{totals.total}</div>',
            f'  <div class="cell metric-cell">{totals.passed}</div>',
            f'  <div class="cell metric-cell">{totals.failed}</div>',
            f'  <div class="cell metric-cell">{totals.skipped}</div>',
            f'  <div class="cell status-cell status-{html.escape(status)}">{html.escape(status)}</div>',
        ],
        summary=summary,
    )


def _test_case_href(base_href: str | None, index: int) -> str | None:
    if base_href is None:
        return None
    return f"{base_href}#case-{index}"


def _test_case_row(test: TestCaseResult, depth: int, href: str | None) -> str:
    totals = test.totals
    status = _status(totals)
    label = test.name or test.module or "unnamed test case"
    return _row(
        "test-grid",
        [
            _path_cell(label, depth, href, extra_class="test-case-path"),
            f'  <div class="cell metric-cell">{totals.total}</div>',
            f'  <div class="cell metric-cell">{totals.passed}</div>',
            f'  <div class="cell metric-cell">{totals.failed}</div>',
            f'  <div class="cell metric-cell">{totals.skipped}</div>',
            f'  <div class="cell status-cell status-{html.escape(status)}">{html.escape(status)}</div>',
        ],
        extra_class="test-case-row",
    )


def render_test_tree_rows(root: TreeNode, links: dict[Path, str] | None = None) -> str:
    rows: list[str] = []
    links = links or {}

    def visit(node: TreeNode, depth: int) -> str:
        relative_path = _tree_relative_path(root, node)
        href = links.get(relative_path)
        if node.children:
            child_rows = [visit(child, depth + 1) for child in sorted(node.children.values(), key=lambda item: item.name)]
            return _details(
                _test_row(node, depth, href, summary=True, expandable=True),
                child_rows,
            )

        if node.tests:
            case_rows = [
                _test_case_row(test, depth + 1, _test_case_href(href, index))
                for index, test in enumerate(node.tests, start=1)
            ]
            return _details(
                _test_row(node, depth, href, summary=True, expandable=True),
                case_rows,
            )

        return _test_row(node, depth, href)

    rows.append(visit(root, 0))
    return "\n".join(rows)


def _metric_chips(record: CoverageRecord) -> str:
    chips: list[str] = []
    for metric in record.metrics:
        metric_label = f"{metric.name}: {metric.display}"
        chips.append(
            (
                '<span class="metric-chip metric-cell"'
                f' title="{html.escape(metric_label, quote=True)}">'
                f"{html.escape(metric_label)}</span>"
            ),
        )
    return "\n".join(chips)


def _detail_rows(record: CoverageRecord) -> str:
    rows: list[str] = []
    for index, detail in enumerate(record.detail_rows, start=1):
        row_cells: list[str] = []
        for key, value in detail.items():
            row_cells.append(
                "\n".join(
                    [
                        '<div class="detail-field">',
                        f'  <div class="metric-cell">{html.escape(str(key))}</div>',
                        f'  <pre class="code-block">{html.escape(str(value))}</pre>',
                        "</div>",
                    ],
                ),
            )
        rows.append(
            "\n".join(
                [
                    '<section class="tree-section detail-row">',
                    f'  <div class="section-heading"><h2>Detail row {index}</h2></div>',
                    *row_cells,
                    "</section>",
                ],
            ),
        )
    if rows:
        return "\n".join(rows)
    return '<p class="code-block">No detail rows.</p>'


def _native_report_link(output_dir: Path, record: CoverageRecord) -> str:
    if record.native_report is None:
        return ""
    try:
        report_path = record.native_report.relative_to(output_dir)
    except ValueError:
        return ""

    href = (Path("..") / report_path).as_posix()
    label = report_path.as_posix()
    return "\n".join(
        [
            '<section class="tree-section">',
            '  <div class="section-heading"><h2>Native coverage report</h2></div>',
            (
                '  <div class="cell path-cell">'
                f'<a class="tree-link" href="{html.escape(href, quote=True)}">'
                f"{html.escape(label)}</a></div>"
            ),
            "</section>",
        ],
    )


def _breadcrumbs(path: Path) -> str:
    crumbs = ["coverage", *_display_path(path).split("/")]
    return " / ".join(html.escape(crumb) for crumb in crumbs if crumb)


def _test_breadcrumbs(path: Path) -> str:
    crumbs = ["tests", *_display_path(path).split("/")]
    return " / ".join(html.escape(crumb) for crumb in crumbs if crumb)


def _run_overview_breadcrumbs(kind: str, overview: LanguageRunOverview) -> str:
    return " / ".join(
        html.escape(crumb)
        for crumb in (kind, overview.scope, overview.language)
    )


def _totals_for_tests(tests: list[TestCaseResult]) -> TestTotals:
    totals = TestTotals()
    for test in tests:
        totals.add(test.totals)
    return totals


def _has_meaningful_output(value: str) -> bool:
    for line in value.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.strip("- ") in {
            "Captured Log",
            "Captured Out",
            "Captured Err",
        }:
            continue
        return True
    return False


def _diagnostic_field(label: str, value: str) -> str:
    return "\n".join(
        [
            '<div class="detail-field">',
            f'  <div class="metric-cell">{html.escape(label)}</div>',
            f'  <pre class="code-block">{html.escape(value)}</pre>',
            "</div>",
        ],
    )


def _diagnostic_group(
    title: str,
    output_fields: list[tuple[str, str, bool]],
    empty_message: str,
) -> str:
    rows = [
        _diagnostic_field(label, value)
        for label, value, needs_meaningful_output in output_fields
        if value and (not needs_meaningful_output or _has_meaningful_output(value))
    ]
    if rows:
        content = "\n".join(rows)
        open_attr = " open"
    else:
        content = "\n".join(
            [
                '<p class="empty-diagnostic">',
                html.escape(empty_message),
                "</p>",
            ],
        )
        open_attr = ""
    return "\n".join(
        [
            f'<details class="diagnostic-details diagnostic-channel"{open_attr}>',
            f"  <summary>{html.escape(title)}</summary>",
            content,
            "</details>",
        ],
    )


def _captured_stdout(test: TestCaseResult) -> str:
    return test.stdout or test.tested_stdout


def _captured_stderr(test: TestCaseResult) -> str:
    return test.stderr or test.tested_stderr


def _captured_failure(test: TestCaseResult) -> str:
    return test.assertion_failure or test.failure or test.tested_failure


def _has_per_test_captured_output(test: TestCaseResult) -> bool:
    return any(
        (
            test.failure_message,
            test.assertion_stdout,
            _captured_stdout(test),
            _captured_stderr(test),
            _captured_failure(test),
        ),
    )


def _captured_output_section(test: TestCaseResult) -> str:
    return _diagnostic_group(
        "Per-test captured output",
        [
            ("Failure message", test.failure_message, False),
            ("Captured log", test.assertion_stdout, True),
            ("Captured stdout", _captured_stdout(test), True),
            ("Captured stderr", _captured_stderr(test), True),
            ("Failure / traceback", _captured_failure(test), False),
        ],
        "No per-test stdout, stderr, log, or failure output was captured for this test case.",
    )


def _report_phase_grid(phases: tuple[dict[str, str], ...]) -> str:
    rows: list[str] = []
    for phase in phases:
        label = phase.get("phase", "")
        outcome = phase.get("outcome", "")
        duration = phase.get("duration", "")
        message = phase.get("message", "")
        phase_label = f"{label or 'phase'}: {outcome or 'unknown'}"
        if duration:
            phase_label = f"{phase_label} ({duration})"
        rows.append(
            "\n".join(
                [
                    '<div class="phase-row">',
                    f'  <div class="metric-cell">{html.escape(phase_label)}</div>',
                    f'  <pre class="code-block phase-message">{html.escape(message)}</pre>',
                    "</div>",
                ],
            ),
        )
    return "\n".join(rows)


def _pytest_phase_section(test: TestCaseResult) -> str:
    if not test.report_phases:
        return ""
    return "\n".join(
        [
            '<details class="diagnostic-details diagnostic-channel" open>',
            "  <summary>Pytest report phases</summary>",
            _report_phase_grid(test.report_phases),
            "</details>",
        ],
    )


def _source_excerpt_section(test: TestCaseResult) -> str:
    if not test.source_excerpt:
        return ""
    return "\n".join(
        [
            '<div class="detail-field">',
            '  <div class="metric-cell">Source excerpt</div>',
            f'  <pre class="code-block source-excerpt">{html.escape(test.source_excerpt)}</pre>',
            "</div>",
        ],
    )


def _execution_log_section(
    output_dir: Path,
    execution_log: Path,
    *,
    title: str = "Execution log",
    open_by_default: bool = True,
) -> str:
    if not execution_log.is_file():
        return ""
    try:
        relative_log = Path("..") / execution_log.relative_to(output_dir)
    except ValueError:
        return ""
    log_text = execution_log.read_text(encoding="utf-8", errors="replace")
    open_attr = " open" if open_by_default else ""
    return "\n".join(
        [
            f'<details class="diagnostic-details execution-log-details"{open_attr}>',
            f"  <summary>{html.escape(title)}</summary>",
            (
                '  <p class="metric-cell diagnostic-link">'
                f'<a class="tree-link" href="{html.escape(relative_log.as_posix(), quote=True)}">'
                f"{html.escape(execution_log.name)}</a></p>"
            ),
            f'  <pre class="code-block execution-log">{html.escape(log_text)}</pre>',
            "</details>",
        ],
    )


def _diagnostic_sections(output_dir: Path, test: TestCaseResult) -> str:
    sections = [
        _source_excerpt_section(test),
        _captured_output_section(test),
        _pytest_phase_section(test),
    ]
    if test.case_output_log is not None:
        sections.append(
            _execution_log_section(
                output_dir,
                test.case_output_log,
                title="Per-test Bats log",
                open_by_default=not _has_per_test_captured_output(test),
            ),
        )
    return "\n".join(section for section in sections if section)


def _test_file_execution_log_sections(output_dir: Path, tests: list[TestCaseResult]) -> str:
    logs: list[Path] = []
    for test in tests:
        if test.language == "python":
            continue
        if test.execution_log is not None and test.execution_log not in logs:
            logs.append(test.execution_log)
    return "\n".join(
        _execution_log_section(
            output_dir,
            log,
            title="Runner transcript",
            open_by_default=False,
        )
        for log in logs
    )


def _test_case_sections(output_dir: Path, tests: list[TestCaseResult]) -> str:
    sections: list[str] = []
    for index, test in enumerate(tests, start=1):
        test_title = test.name or f"Test case {index}"
        sections.append(
            "\n".join(
                [
                    f'<section class="tree-section" id="case-{index}">',
                    "  <div class=\"section-heading\">",
                    f"    <h2>{html.escape(test_title)}</h2>",
                    "  </div>",
                    "  <div class=\"test-grid row\">",
                    f"    <div class=\"cell path-cell\">{html.escape(test.module)}</div>",
                    f"    <div class=\"cell metric-cell\">{html.escape(test.name)}</div>",
                    f"    <div class=\"cell metric-cell\">{html.escape(test.status)}</div>",
                    f"    <div class=\"cell metric-cell\">{html.escape(f'{test.time:.3f}s')}</div>",
                    f"    <div class=\"cell metric-cell\">{html.escape(test.scope)}</div>",
                    f"    <div class=\"cell metric-cell\">{html.escape(test.language)}</div>",
                    "  </div>",
                    _diagnostic_sections(output_dir, test),
                    "</section>",
                ],
            ),
        )
    return "\n".join(sections)


def _group_tests_by_path(tests: list[TestCaseResult]) -> dict[Path, list[TestCaseResult]]:
    grouped: dict[Path, list[TestCaseResult]] = {}
    for test in tests:
        grouped.setdefault(test.test_path, []).append(test)
    return grouped


def test_page_filenames(test_paths: list[Path]) -> dict[Path, str]:
    slug_counts: dict[str, int] = {}
    filenames: dict[Path, str] = {}
    for test_path in test_paths:
        slug = _safe_path_slug(test_path)
        slug_counts[slug] = slug_counts.get(slug, 0) + 1
        filenames[test_path] = test_page_filename(test_path, slug_counts[slug])
    return filenames


def render_coverage_drilldown_pages(output_dir: Path, records: list[CoverageRecord]) -> None:
    ensure_static_assets(output_dir)
    coverage_dir = output_dir / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)

    for ordinal, record in enumerate(records, start=1):
        source_path = _display_path(record.source_path)
        html_output = render_template(
            template_path("coverage-file.html"),
            {
                "title": f"Coverage: {html.escape(source_path)}",
                "breadcrumbs": _breadcrumbs(record.source_path),
                "source_path": html.escape(source_path),
                "source_path_attr": html.escape(source_path, quote=True),
                "scope": html.escape(record.scope),
                "language": html.escape(record.language),
                "detail_kind": html.escape(record.detail_kind),
                "metric_chips": _metric_chips(record),
                "native_report": _native_report_link(output_dir, record),
                "detail_rows": _detail_rows(record),
            },
        )
        (coverage_dir / coverage_page_filename(record, ordinal)).write_text(html_output, encoding="utf-8")


def _run_overview_raw_log_link(output_dir: Path, output_log: Path | None) -> str:
    if output_log is None:
        return ""
    try:
        relative_log = Path("..") / output_log.relative_to(output_dir)
    except ValueError:
        return ""
    return (
        '<a class="tree-link"'
        f' href="{html.escape(relative_log.as_posix(), quote=True)}">'
        f"{html.escape(output_log.relative_to(output_dir).as_posix())}</a>"
    )


def _run_overview_output_section(
    output_dir: Path,
    *,
    title: str,
    output_text: str,
    output_log: Path | None,
) -> str:
    raw_log_link = _run_overview_raw_log_link(output_dir, output_log)
    raw_link = ""
    if raw_log_link:
        raw_link = f'<p class="metric-cell diagnostic-link">{raw_log_link}</p>'
    return "\n".join(
        [
            '<section class="tree-section">',
            '  <div class="section-heading">',
            f"    <h2>{html.escape(title)}</h2>",
            "  </div>",
            raw_link,
            f'  <pre class="code-block run-overview-output">{html.escape(output_text)}</pre>',
            "</section>",
        ],
    )


def _render_run_overview_page(
    output_dir: Path,
    overview: LanguageRunOverview,
    *,
    title_prefix: str,
    output_title: str,
    output_text: str,
    breadcrumbs_kind: str,
) -> str:
    title = f"{title_prefix}: {overview.scope}/{overview.language}"
    if overview.sections:
        output_sections = "\n".join(
            _run_overview_output_section(
                output_dir,
                title=section.title,
                output_text=section.output,
                output_log=section.output_log,
            )
            for section in overview.sections
        )
        raw_log_link = "See per-file sections below."
    else:
        output_sections = _run_overview_output_section(
            output_dir,
            title=output_title,
            output_text=output_text,
            output_log=overview.output_log,
        )
        raw_log_link = _run_overview_raw_log_link(output_dir, overview.output_log)
    return render_template(
        template_path("run-overview.html"),
        {
            "title": html.escape(title),
            "breadcrumbs": _run_overview_breadcrumbs(breadcrumbs_kind, overview),
            "heading": html.escape(title),
            "scope": html.escape(overview.scope),
            "language": html.escape(overview.language),
            "raw_log_link": raw_log_link,
            "output_sections": output_sections,
        },
    )


def render_test_run_overview_pages(output_dir: Path, overviews: list[LanguageRunOverview]) -> None:
    ensure_static_assets(output_dir)
    tests_dir = output_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    for overview in overviews:
        html_output = _render_run_overview_page(
            output_dir,
            overview,
            title_prefix="Test Results",
            output_title="Runner summary",
            output_text=overview.test_output,
            breadcrumbs_kind="tests",
        )
        (tests_dir / run_overview_page_filename(overview)).write_text(html_output, encoding="utf-8")


def render_coverage_run_overview_pages(output_dir: Path, overviews: list[LanguageRunOverview]) -> None:
    ensure_static_assets(output_dir)
    coverage_dir = output_dir / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)

    for overview in overviews:
        if not overview.coverage_output:
            continue
        html_output = _render_run_overview_page(
            output_dir,
            overview,
            title_prefix="Coverage",
            output_title="Coverage runner output",
            output_text=overview.coverage_output,
            breadcrumbs_kind="coverage",
        )
        (coverage_dir / run_overview_page_filename(overview)).write_text(html_output, encoding="utf-8")


def render_test_drilldown_pages(output_dir: Path, tests: list[TestCaseResult]) -> None:
    ensure_static_assets(output_dir)
    tests_dir = output_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    grouped_tests = _group_tests_by_path(tests)
    filenames = test_page_filenames(list(grouped_tests))
    for test_path, path_tests in grouped_tests.items():
        path_display = _display_path(test_path)
        totals = _totals_for_tests(path_tests)
        html_output = render_template(
            template_path("test-file.html"),
            {
                "title": f"Tests: {html.escape(path_display)}",
                "breadcrumbs": _test_breadcrumbs(test_path),
                "test_path": html.escape(path_display),
                "test_path_attr": html.escape(path_display, quote=True),
                "total": html.escape(str(totals.total)),
                "passed": html.escape(str(totals.passed)),
                "failed": html.escape(str(totals.failed)),
                "skipped": html.escape(str(totals.skipped)),
                "status": html.escape(_status(totals)),
                "test_cases": "\n".join(
                    section
                    for section in [
                        _test_file_execution_log_sections(output_dir, path_tests),
                        _test_case_sections(output_dir, path_tests),
                    ]
                    if section
                ),
            },
        )
        (tests_dir / filenames[test_path]).write_text(html_output, encoding="utf-8")


def _run_overview_links(overviews: list[LanguageRunOverview] | None) -> str:
    if not overviews:
        return ""
    rows: list[str] = []
    for overview in overviews:
        label = f"{overview.scope}/{overview.language}"
        coverage_link = ""
        if overview.coverage_output:
            coverage_link = (
                f'<a class="tree-link" href="{html.escape(_coverage_run_overview_href(overview), quote=True)}">'
                "coverage</a>"
            )
        rows.append(
            "\n".join(
                [
                    '<div class="overview-link-row">',
                    f'  <div class="cell path-cell">{html.escape(label)}</div>',
                    (
                        '  <div class="cell metric-cell">'
                        f'<a class="tree-link" href="{html.escape(_test_run_overview_href(overview), quote=True)}">'
                        "test results</a></div>"
                    ),
                    f'  <div class="cell metric-cell">{coverage_link}</div>',
                    "</div>",
                ],
            ),
        )
    return "\n".join(
        [
            '<section class="tree-section overview-section" aria-labelledby="overview-heading">',
            '  <div class="section-heading">',
            '    <h2 id="overview-heading">Suite overviews</h2>',
            "  </div>",
            '  <div class="overview-link-grid grid-header">',
            '    <div class="cell path-cell">Lane</div>',
            '    <div class="cell metric-cell">Tests</div>',
            '    <div class="cell metric-cell">Coverage</div>',
            "  </div>",
            *rows,
            "</section>",
        ],
    )


def _coverage_gate_row(result: CoverageGateResult) -> str:
    severity_label = "pass" if result.severity == "pass" else result.severity
    source_path = "" if result.source_path is None else result.source_path.as_posix()
    return "\n".join(
        [
            '<div class="gate-grid row">',
            f'  <div class="cell metric-cell">{html.escape(result.kind)}</div>',
            f'  <div class="cell metric-cell">{html.escape(result.scope)}</div>',
            f'  <div class="cell metric-cell">{html.escape(result.language)}</div>',
            f'  <div class="cell metric-cell">{html.escape(result.metric)}</div>',
            f'  <div class="cell path-cell" title="{html.escape(source_path, quote=True)}">{html.escape(source_path)}</div>',
            f'  <div class="cell metric-cell">{html.escape(result.display)}</div>',
            f'  <div class="cell metric-cell">{result.threshold_percent:.0f}%</div>',
            f'  <div class="cell status-cell status-{html.escape(result.severity)}">{html.escape(severity_label)}</div>',
            "</div>",
        ],
    )


def _coverage_gate_section(gate_results: list[CoverageGateResult] | None) -> str:
    if not gate_results:
        return ""
    return "\n".join(
        [
            '<section class="tree-section gate-section" aria-labelledby="coverage-gates-heading">',
            '  <div class="section-heading">',
            '    <h2 id="coverage-gates-heading">Coverage gates</h2>',
            "  </div>",
            '  <div class="gate-grid grid-header">',
            '    <div class="cell metric-cell">Kind</div>',
            '    <div class="cell metric-cell">Scope</div>',
            '    <div class="cell metric-cell">Lang</div>',
            '    <div class="cell metric-cell">Metric</div>',
            '    <div class="cell path-cell">Source</div>',
            '    <div class="cell metric-cell">Result</div>',
            '    <div class="cell metric-cell">Min</div>',
            '    <div class="cell status-cell">Status</div>',
            "  </div>",
            *(_coverage_gate_row(result) for result in gate_results),
            "</section>",
        ],
    )


def _status_badge(label: str, value: str, status: str) -> str:
    text = f"{label}: {value}"
    return (
        f'<span class="status-badge status-{html.escape(status, quote=True)}" '
        f'title="{html.escape(text, quote=True)}">'
        f"{html.escape(text)}</span>"
    )


def runner_step_page_filename(step: RunnerStepResult) -> str:
    label = _safe_filename_part(step.label, "step")
    return f"{step.ordinal:03d}-{label}.html"


def render_runner_step_pages(output_dir: Path, runner_report: RunnerReport) -> None:
    runner_dir = output_dir / "runner"
    runner_dir.mkdir(parents=True, exist_ok=True)
    for step in runner_report.steps:
        source = output_dir / step.output_log
        output = source.read_text(encoding="utf-8", errors="replace")
        title = f"Runner step {step.ordinal}: {step.label}"
        body = "\n".join(
            [
                '<main class="drilldown-page">',
                f"  <h1>{html.escape(title)}</h1>",
                f'  <p class="status-{html.escape(step.status, quote=True)}">Status: {html.escape(step.status.upper())}; exit code: {step.exit_code}</p>',
                f"  <pre>{html.escape(output)}</pre>",
                "</main>",
            ],
        )
        document = "\n".join(
            [
                "<!doctype html>",
                '<html lang="en">',
                "<head>",
                '  <meta charset="utf-8">',
                f"  <title>{html.escape(title)}</title>",
                '  <link rel="stylesheet" href="../static/report.css">',
                "</head>",
                "<body>",
                body,
                "</body>",
                "</html>",
            ],
        )
        (runner_dir / runner_step_page_filename(step)).write_text(document, encoding="utf-8")


def _runner_section(runner_report: RunnerReport | None) -> str:
    if runner_report is None or not runner_report.steps:
        return ""
    rows = []
    for step in runner_report.steps:
        href = f"runner/{runner_step_page_filename(step)}"
        rows.append(
            "\n".join(
                [
                    '<div class="runner-grid row">',
                    f'  <div class="cell metric-cell">{step.ordinal}</div>',
                    f'  <div class="cell path-cell"><a href="{html.escape(href, quote=True)}">{html.escape(step.label)}</a></div>',
                    f'  <div class="cell status-cell status-{html.escape(step.status, quote=True)}">{html.escape(step.status.upper())}</div>',
                    f'  <div class="cell metric-cell">{step.exit_code}</div>',
                    "</div>",
                ],
            ),
        )
    failure_rows = "".join(
        f"<li>{failure.sequence}. {html.escape(failure.label)} (exit {failure.exit_code})</li>"
        for failure in runner_report.failures
    )
    failures = "" if not failure_rows else f'<div class="runner-failures"><h3>Failure order</h3><ol>{failure_rows}</ol></div>'
    return "\n".join(
        [
            '<section class="tree-section" aria-labelledby="runner-heading">',
            '  <div class="section-heading"><h2 id="runner-heading">Runner / qualification steps</h2></div>',
            '  <div class="runner-grid grid-header">',
            '    <div class="cell metric-cell">#</div>',
            '    <div class="cell path-cell">Step</div>',
            '    <div class="cell status-cell">Status</div>',
            '    <div class="cell metric-cell">Exit</div>',
            "  </div>",
            *rows,
            failures,
            "</section>",
        ],
    )


def _coverage_override_notice(coverage_override: bool) -> str:
    if not coverage_override:
        return ""
    return (
        '<section class="tree-section override-section" aria-labelledby="coverage-override-heading">'
        '<div class="section-heading"><h2 id="coverage-override-heading">Coverage gate override</h2></div>'
        '<p class="notice">The operator accepted a coverage-gate override for this run.</p>'
        "</section>"
    )


def _report_badges(
    test_tree: TreeNode,
    coverage_gates: list[CoverageGateResult] | None,
    execution: ExecutionStatus | None = None,
    coverage_override: bool = False,
) -> str:
    if execution is None:
        execution = ExecutionStatus("unknown", 0, "not recorded")
    overall_value = execution.status.upper()
    if execution.detail != "recorded":
        overall_value = f"{overall_value} ({execution.detail})"
    badges: list[str] = [_status_badge("Overall run", overall_value, execution.status)]
    totals = test_tree.test_totals
    if totals.total:
        if totals.failed:
            status = "failed"
        elif totals.skipped:
            status = "warn"
        else:
            status = "passed"
        badges.append(_status_badge("Parsed tests", f"{totals.passed}/{totals.total} passed", status))
    else:
        badges.append(_status_badge("Parsed tests", "none", "skipped"))
    gate_results = coverage_gates or []
    if gate_results:
        failures = sum(1 for result in gate_results if result.severity == "fail")
        warnings = sum(1 for result in gate_results if result.severity == "warn")
        if failures:
            badges.append(_status_badge("Coverage gates", f"{failures} failed", "failed"))
        elif warnings:
            badges.append(_status_badge("Coverage gates", f"{warnings} warning", "warn"))
        else:
            badges.append(_status_badge("Coverage gates", f"{len(gate_results)} passed", "passed"))
    else:
        badges.append(_status_badge("Coverage gates", "not reported", "skipped"))
    if coverage_override:
        badges.append(_status_badge("Coverage override", "used", "warn"))
    return "\n".join(badges)


def render_report_homepage(
    output_dir: Path,
    *,
    coverage_tree: TreeNode,
    test_tree: TreeNode,
    coverage_links: dict[Path, str] | None = None,
    test_links: dict[Path, str] | None = None,
    run_overviews: list[LanguageRunOverview] | None = None,
    coverage_gates: list[CoverageGateResult] | None = None,
    runner_report: RunnerReport | None = None,
    coverage_override: bool = False,
    destination: Path | None = None,
) -> None:
    ensure_static_assets(output_dir)
    body = render_template(
        template_path("index.html"),
        {
            "report_badges": _report_badges(
                test_tree,
                coverage_gates,
                None if runner_report is None else runner_report.execution,
                coverage_override,
            ),
            "overview_links": _run_overview_links(run_overviews),
            "coverage_override": _coverage_override_notice(coverage_override),
            "runner_steps": _runner_section(runner_report),
            "coverage_gates": _coverage_gate_section(coverage_gates),
            "coverage_tree": render_coverage_tree_rows(coverage_tree, coverage_links),
            "test_tree": render_test_tree_rows(test_tree, test_links),
        },
    )
    html_output = render_template(template_path("base.html"), {"title": "nix-darwin Test Report", "body": body})
    (destination or output_dir / "index.html").write_text(html_output, encoding="utf-8")
