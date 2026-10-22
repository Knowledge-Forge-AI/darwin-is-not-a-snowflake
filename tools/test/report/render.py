#!/usr/bin/env python3
"""Render a unified Python/Ruby/Bats test result report."""

from __future__ import annotations

import argparse
import ast
import html
import json
import os
import re
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pages
import parsers
import trees
import gates

TestCaseResult = parsers.TestCaseResult
LanguageRunOverview = parsers.MODEL.LanguageRunOverview
RunOverviewSection = parsers.MODEL.RunOverviewSection
CoverageGateReport = parsers.MODEL.CoverageGateReport

if TYPE_CHECKING:
    from model import CoverageRecord
    from model import CoverageGateReport
    from model import LanguageRunOverview
    from model import RunOverviewSection
    from model import TestCaseResult


class DeprecatedTemplateOverrideAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[str] | None,
        option_string: str | None = None,
    ) -> None:
        parser.error(f"{option_string} is no longer supported by the tree-based report renderer")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--summary-file", required=True, type=Path)
    parser.add_argument("--repo-root", type=Path)
    phase = parser.add_mutually_exclusive_group()
    phase.add_argument("--summary-only", action="store_true")
    phase.add_argument("--render-only", action="store_true")
    parser.add_argument(
        "--template-file",
        action=DeprecatedTemplateOverrideAction,
        default=SCRIPT_DIR / "index.html",
    )
    parser.add_argument(
        "--suite-row-template-file",
        action=DeprecatedTemplateOverrideAction,
        default=SCRIPT_DIR / "suite-row.html",
    )
    parser.add_argument(
        "--test-row-template-file",
        action=DeprecatedTemplateOverrideAction,
        default=SCRIPT_DIR / "test-row.html",
    )
    parser.add_argument(
        "--coverage-item-template-file",
        action=DeprecatedTemplateOverrideAction,
        default=SCRIPT_DIR / "coverage-item.html",
    )
    parser.add_argument(
        "--coverage-empty-template-file",
        action=DeprecatedTemplateOverrideAction,
        default=SCRIPT_DIR / "coverage-empty.html",
    )
    parser.add_argument(
        "--test-output-template-file",
        action=DeprecatedTemplateOverrideAction,
        default=SCRIPT_DIR / "test-output.html",
    )
    parser.add_argument(
        "--test-output-section-template-file",
        action=DeprecatedTemplateOverrideAction,
        default=SCRIPT_DIR / "test-output-section.html",
    )
    return parser.parse_args(argv)


def test_result_to_dict(result: TestCaseResult) -> dict[str, Any]:
    return {
        "scope": result.scope,
        "language": result.language,
        "test_path": result.test_path.as_posix(),
        "module": result.module,
        "classname": result.module,
        "name": result.name,
        "status": result.status,
        "time": result.time,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "failure": result.failure,
        "failure_message": result.failure_message,
        "source_line": result.source_line,
        "source_excerpt": result.source_excerpt,
        "execution_log": "" if result.execution_log is None else result.execution_log.as_posix(),
        "case_output_log": "" if result.case_output_log is None else result.case_output_log.as_posix(),
        "assertion_stdout": result.assertion_stdout,
        "assertion_stderr": result.assertion_stderr,
        "assertion_failure": result.assertion_failure,
        "tested_stdout": result.tested_stdout,
        "tested_stderr": result.tested_stderr,
        "tested_failure": result.tested_failure,
        "report_phases": list(result.report_phases),
    }


def discover_results(report_dir: Path) -> list[dict[str, Any]]:
    return [test_result_to_dict(result) for result in discover_typed_results(report_dir)]


def discover_typed_results(report_dir: Path) -> list[TestCaseResult]:
    results: list[TestCaseResult] = []
    for scope_dir in sorted(path for path in report_dir.iterdir() if path.is_dir()):
        scope = scope_dir.name
        python_json_report = scope_dir / "python" / "report.json"
        python_junit = scope_dir / "python" / "junit.xml"
        if python_json_report.is_file():
            results.extend(parsers.parse_pytest_json_report_file(scope, python_json_report))
        elif python_junit.is_file():
            results.extend(parsers.parse_junit_file(scope, "python", python_junit))

        for language in ("bash", "awk", "applescript", "nix"):
            for bats_junit in sorted((scope_dir / language).glob("*/report.xml")):
                results.extend(parsers.parse_junit_file(scope, language, bats_junit))

        for nix_output in sorted((scope_dir / "nix").glob("*/output.txt")):
            results.extend(parsers.parse_nix_output_file(scope, nix_output))

        for zunit_tap in sorted((scope_dir / "zsh").glob("*/output.tap")):
            results.extend(parsers.parse_zunit_tap_file(scope, zunit_tap))

        ruby_results = scope_dir / "ruby" / "results.json"
        if ruby_results.is_file():
            results.extend(parsers.parse_ruby_file(scope, ruby_results))
    return results


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {"total": len(results), "passed": 0, "failed": 0, "skipped": 0}
    by_suite: dict[tuple[str, str, str], dict[str, Any]] = {}
    for result in results:
        status = result["status"]
        if status == "skipped":
            summary["skipped"] += 1
        elif status == "passed":
            summary["passed"] += 1
        else:
            summary["failed"] += 1

        key = (result["scope"], result["language"], result["module"])
        suite = by_suite.setdefault(
            key,
            {
                "scope": result["scope"],
                "language": result["language"],
                "module": result["module"],
                "total": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0,
            },
        )
        suite["total"] += 1
        suite["skipped" if status == "skipped" else "passed" if status == "passed" else "failed"] += 1

    return {"summary": summary, "suites": list(by_suite.values()), "tests": results}


def build_coverage_link_map(records: list[CoverageRecord]) -> dict[tuple[Path, int], str]:
    occurrence_by_path: dict[Path, int] = {}
    links: dict[tuple[Path, int], str] = {}
    for ordinal, record in enumerate(records, start=1):
        occurrence_by_path[record.source_path] = occurrence_by_path.get(record.source_path, 0) + 1
        links[(record.source_path, occurrence_by_path[record.source_path])] = (
            f"coverage/{pages.coverage_page_filename(record, ordinal)}"
        )
    return links


def discover_run_overviews(report_dir: Path) -> list[LanguageRunOverview]:
    overviews: list[LanguageRunOverview] = []
    for scope_dir in sorted(path for path in report_dir.iterdir() if path.is_dir()):
        python_output = scope_dir / "python" / "output.txt"
        if not python_output.is_file():
            pass
        else:
            test_output, coverage_output = parsers.split_pytest_runner_output(
                python_output.read_text(encoding="utf-8", errors="replace"),
            )
            overviews.append(
                LanguageRunOverview(
                    scope=scope_dir.name,
                    language="python",
                    output_log=python_output,
                    test_output=test_output,
                    coverage_output=coverage_output,
                ),
            )

        ruby_output = scope_dir / "ruby" / "output.txt"
        if ruby_output.is_file():
            test_output, coverage_output = parsers.split_ruby_runner_output(
                ruby_output.read_text(encoding="utf-8", errors="replace"),
            )
            overviews.append(
                LanguageRunOverview(
                    scope=scope_dir.name,
                    language="ruby",
                    output_log=ruby_output,
                    test_output=test_output,
                    coverage_output=coverage_output,
                ),
            )

        for language in ("bash", "awk", "applescript", "nix"):
            sections: list[RunOverviewSection] = []
            for bats_output in sorted((scope_dir / language).glob("*/output.txt")):
                sections.append(
                    RunOverviewSection(
                        title=bats_output.parent.name,
                        output_log=bats_output,
                        output=bats_output.read_text(encoding="utf-8", errors="replace"),
                    ),
                )
            if sections:
                overviews.append(
                    LanguageRunOverview(
                        scope=scope_dir.name,
                        language=language,
                        sections=tuple(sections),
                    ),
                )
    return overviews


def build_test_link_map(
    tests: list[TestCaseResult],
    run_overviews: list[LanguageRunOverview] | None = None,
) -> dict[Path, str]:
    unique_test_paths: list[Path] = []
    for test in tests:
        if test.test_path not in unique_test_paths:
            unique_test_paths.append(test.test_path)
    links = {
        test_path: f"tests/{filename}"
        for test_path, filename in pages.test_page_filenames(unique_test_paths).items()
    }
    for overview in run_overviews or []:
        links[Path("src/test") / overview.scope / overview.language] = (
            f"tests/{pages.run_overview_page_filename(overview)}"
        )
    return links


def infer_repo_root(report_dir: Path) -> Path | None:
    resolved = report_dir.resolve()
    parts = resolved.parts
    if len(parts) >= 3 and parts[-3] == ".test-reports":
        return Path(*parts[:-3])
    return None


def infer_command_scope(report_dir: Path) -> str:
    parts = report_dir.resolve().parts
    if len(parts) >= 2 and parts[-1] == "latest" and parts[-2] in {"all", "unit", "int"}:
        return parts[-2]
    return "all"


def execution_log_for_test(report_dir: Path, test: TestCaseResult) -> Path | None:
    language_dir = report_dir / test.scope / test.language
    if test.language == "python":
        return None
    if test.language == "ruby":
        log_path = language_dir / "output.txt"
    elif test.language in {"bash", "awk", "applescript"}:
        log_path = language_dir / test.test_path.name / "output.txt"
    elif test.language == "zsh":
        log_path = language_dir / test.test_path.name / "output.tap"
    elif test.language == "nix":
        log_path = language_dir / test.test_path.name / "output.txt"
    else:
        return None
    return log_path if log_path.is_file() else None


def case_output_log_for_test(report_dir: Path, test: TestCaseResult, case_index: int) -> Path | None:
    if test.language not in {"bash", "awk", "applescript"}:
        return None

    gather_dir = report_dir / test.scope / test.language / test.test_path.name / "test-outputs"
    if not gather_dir.is_dir():
        return None

    exact_log = gather_dir / f"{case_index}-{test.name}.log"
    if exact_log.is_file():
        return exact_log

    matches = sorted(gather_dir.glob(f"{case_index}-*.log"))
    if matches:
        return matches[0]
    return None


def source_file_for_test(repo_root: Path, test: TestCaseResult) -> Path | None:
    source_path = test.test_path
    if source_path.is_absolute():
        candidate = source_path
    else:
        candidate = repo_root / source_path
    return candidate if candidate.is_file() else None


def _lines_to_excerpt(lines: list[str], start_index: int, end_index: int) -> str:
    start = max(0, start_index)
    end = min(len(lines), end_index)
    return "\n".join(lines[start:end]).rstrip() + "\n"


def _extract_python_excerpt(source_file: Path, test_name: str) -> str:
    source = source_file.read_text(encoding="utf-8", errors="replace")
    name = test_name.split("[", 1)[0]
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""

    lines = source.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        if node.name != name:
            continue
        decorator_lines = [decorator.lineno for decorator in node.decorator_list]
        start_line = min([node.lineno, *decorator_lines]) if decorator_lines else node.lineno
        end_line = getattr(node, "end_lineno", node.lineno)
        return _lines_to_excerpt(lines, start_line - 1, end_line)
    return ""


def _extract_ruby_excerpt(source_file: Path, test_name: str, source_line: int | None) -> str:
    lines = source_file.read_text(encoding="utf-8", errors="replace").splitlines()
    start_index = None
    method_pattern = re.compile(rf"^\s*def\s+{re.escape(test_name)}\b")
    for index, line in enumerate(lines):
        if method_pattern.search(line):
            start_index = index
            break
    if start_index is None and source_line:
        start_index = max(0, source_line - 1)
    if start_index is None:
        return ""

    for end_index in range(start_index + 1, len(lines)):
        if re.match(r"^\s*end\s*$", lines[end_index]):
            return _lines_to_excerpt(lines, start_index, end_index + 1)
    return _lines_to_excerpt(lines, start_index, min(len(lines), start_index + 12))


def _extract_braced_test_excerpt(source_file: Path, test_name: str) -> str:
    lines = source_file.read_text(encoding="utf-8", errors="replace").splitlines()
    start_index = None
    quoted_name = re.escape(test_name)
    pattern = re.compile(rf"^\s*@test\s+(['\"]){quoted_name}\1")
    for index, line in enumerate(lines):
        if pattern.search(line):
            start_index = index
            break
    if start_index is None:
        return ""

    brace_balance = 0
    for end_index in range(start_index, len(lines)):
        brace_balance += lines[end_index].count("{") - lines[end_index].count("}")
        if end_index > start_index and brace_balance <= 0:
            return _lines_to_excerpt(lines, start_index, end_index + 1)
    return _lines_to_excerpt(lines, start_index, min(len(lines), start_index + 20))


def _extract_context_excerpt(source_file: Path, needle: str, source_line: int | None = None) -> str:
    lines = source_file.read_text(encoding="utf-8", errors="replace").splitlines()
    if source_line:
        index = max(0, source_line - 1)
        return _lines_to_excerpt(lines, index, min(len(lines), index + 12))
    for index, line in enumerate(lines):
        if needle and needle in line:
            return _lines_to_excerpt(lines, max(0, index - 3), min(len(lines), index + 9))
    return ""


def source_excerpt_for_test(repo_root: Path, test: TestCaseResult) -> str:
    source_file = source_file_for_test(repo_root, test)
    if source_file is None:
        return ""
    if test.language == "python":
        excerpt = _extract_python_excerpt(source_file, test.name)
    elif test.language == "ruby":
        excerpt = _extract_ruby_excerpt(source_file, test.name, test.source_line)
    elif test.language in {"bash", "awk", "applescript", "zsh"}:
        excerpt = _extract_braced_test_excerpt(source_file, test.name)
    else:
        excerpt = _extract_context_excerpt(source_file, test.name, test.source_line)
    return excerpt or _extract_context_excerpt(source_file, test.name, test.source_line)


def enrich_test_result(
    report_dir: Path,
    repo_root: Path | None,
    test: TestCaseResult,
    *,
    case_index: int = 1,
) -> TestCaseResult:
    if repo_root is None:
        source_excerpt = ""
    else:
        source_excerpt = source_excerpt_for_test(repo_root, test)
    return TestCaseResult(
        scope=test.scope,
        language=test.language,
        test_path=test.test_path,
        module=test.module,
        name=test.name,
        status=test.status,
        time=test.time,
        stdout=test.stdout,
        stderr=test.stderr,
        failure=test.failure,
        failure_message=test.failure_message,
        source_line=test.source_line,
        source_excerpt=source_excerpt,
        execution_log=execution_log_for_test(report_dir, test),
        case_output_log=case_output_log_for_test(report_dir, test, case_index),
        assertion_stdout=test.assertion_stdout,
        assertion_stderr=test.assertion_stderr,
        assertion_failure=test.assertion_failure,
        tested_stdout=test.tested_stdout,
        tested_stderr=test.tested_stderr,
        tested_failure=test.tested_failure,
        report_phases=test.report_phases,
    )


def enrich_test_results(report_dir: Path, repo_root: Path | None, tests: list[TestCaseResult]) -> list[TestCaseResult]:
    case_indexes: dict[tuple[str, str, Path], int] = defaultdict(int)
    enriched: list[TestCaseResult] = []
    for test in tests:
        key = (test.scope, test.language, test.test_path)
        case_indexes[key] += 1
        enriched.append(
            enrich_test_result(
                report_dir,
                repo_root,
                test,
                case_index=case_indexes[key],
            ),
        )
    return enriched


def build_report_model(
    report_dir: Path,
    *,
    repo_root: Path | None = None,
    summary_file: Path | None = None,
) -> dict[str, Any]:
    if repo_root is None:
        repo_root = infer_repo_root(report_dir)
    tests = enrich_test_results(report_dir, repo_root, discover_typed_results(report_dir))
    coverage = parsers.discover_coverage(report_dir, repo_root=repo_root)
    coverage_manifest = parsers.discover_coverage_manifest(repo_root)
    run_overviews = discover_run_overviews(report_dir)
    coverage_gate_report = gates.evaluate_coverage_gates(
        coverage,
        command_scope=infer_command_scope(report_dir),
        manifest_entries=coverage_manifest,
    )
    coverage_tree = trees.build_coverage_tree(coverage)
    test_tree = trees.build_test_tree(tests)
    legacy_summary = summarize([test_result_to_dict(test) for test in tests])
    runner_report = parsers.parse_runner_report(report_dir)
    coverage_override = (
        False
        if summary_file is None
        else parsers.parse_coverage_override(summary_file, infer_command_scope(report_dir))
    )
    return {
        "tests": tests,
        "coverage": coverage,
        "coverage_manifest": coverage_manifest,
        "coverage_gate_report": coverage_gate_report,
        "run_overviews": run_overviews,
        "coverage_tree": coverage_tree,
        "test_tree": test_tree,
        "coverage_links": build_coverage_link_map(coverage),
        "test_links": build_test_link_map(tests, run_overviews),
        "legacy_summary": legacy_summary,
        "runner_report": runner_report,
        "coverage_override": coverage_override,
    }


def serialize_report_model(model: dict[str, Any]) -> dict[str, Any]:
    serialized = dict(model["legacy_summary"])
    serialized["trees"] = {
        "coverage": {
            "root": model["coverage_tree"].path.as_posix(),
            "records": len(model["coverage"]),
        },
        "tests": {
            "root": model["test_tree"].path.as_posix(),
            "total": model["test_tree"].test_totals.total,
            "passed": model["test_tree"].test_totals.passed,
            "failed": model["test_tree"].test_totals.failed,
            "skipped": model["test_tree"].test_totals.skipped,
        },
    }
    gate_report = model["coverage_gate_report"]
    serialized["coverage_gates"] = [
        {
            "kind": result.kind,
            "severity": result.severity,
            "scope": result.scope,
            "language": result.language,
            "metric": result.metric,
            "covered": result.covered,
            "total": result.total,
            "threshold_percent": result.threshold_percent,
            "display": result.display,
            "source_path": "" if result.source_path is None else result.source_path.as_posix(),
        }
        for result in gate_report.results
    ]
    runner_report = model["runner_report"]
    serialized["execution"] = {
        "status": runner_report.execution.status,
        "exit_code": runner_report.execution.exit_code,
        "detail": runner_report.execution.detail,
    }
    serialized["runner_steps"] = [
        {
            "ordinal": step.ordinal,
            "label": step.label,
            "status": step.status,
            "exit_code": step.exit_code,
            "output_log": step.output_log.as_posix(),
            "output_page": f"runner/{pages.runner_step_page_filename(step)}",
        }
        for step in runner_report.steps
    ]
    serialized["execution_failures"] = [
        {
            "sequence": failure.sequence,
            "label": failure.label,
            "exit_code": failure.exit_code,
        }
        for failure in runner_report.failures
    ]
    serialized["coverage_override"] = model["coverage_override"]
    return serialized


def append_summary_metrics(summary_file: Path, model: dict[str, Any]) -> None:
    grouped: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"total": 0, "passed": 0, "failed": 0, "skipped": 0})
    for test in model["tests"]:
        bucket = grouped[(test["scope"], test["language"])]
        bucket["total"] += 1
        bucket[test["status"] if test["status"] in ("passed", "skipped") else "failed"] += 1

    with summary_file.open("a", encoding="utf-8") as summary:
        for (scope, language), values in sorted(grouped.items()):
            for metric in ("total", "passed", "failed", "skipped"):
                summary.write(f"{scope}\t{language}\ttests.{metric}\t{values[metric]}\n")


def append_coverage_gate_summary_metrics(summary_file: Path, gate_report: CoverageGateReport) -> None:
    buckets: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"failures": 0, "warnings": 0})
    for result in gate_report.hard_results:
        if result.severity == "fail":
            buckets[(result.scope, result.language)]["failures"] += 1
        else:
            buckets[(result.scope, result.language)]["failures"] += 0
    for result in gate_report.warnings:
        buckets[(result.scope, result.language)]["warnings"] += 1

    with summary_file.open("a", encoding="utf-8") as summary:
        for (scope, language), values in sorted(buckets.items()):
            summary.write(f"{scope}\t{language}\tcoverage.gate.failures\t{values['failures']}\n")
            summary.write(f"{scope}\t{language}\tcoverage.gate.warnings\t{values['warnings']}\n")
        for result in gate_report.results:
            source_suffix = "" if result.source_path is None else f" {result.source_path.as_posix()}"
            summary.write(
                (
                    f"{result.scope}\t{result.language}\t"
                    f"coverage.gate.{result.kind}.{result.metric}\t"
                    f"{result.severity} {result.display}{source_suffix} threshold {result.threshold_percent:.0f}%\n"
                ),
            )


def relative_link(report_dir: Path, target: Path) -> str:
    return html.escape(str(target.relative_to(report_dir)))


def coverage_links(report_dir: Path) -> list[tuple[str, str, str]]:
    links: list[tuple[str, str, str]] = []
    for scope_dir in sorted(path for path in report_dir.iterdir() if path.is_dir()):
        bash_kcov = scope_dir / "bash" / "kcov" / "index.html"
        if bash_kcov.is_file():
            links.append((scope_dir.name, "bash", relative_link(report_dir, bash_kcov)))

        for language in ("awk", "applescript", "zsh"):
            statement_coverage = scope_dir / language / "statement-coverage.html"
            if statement_coverage.is_file():
                links.append((scope_dir.name, language, relative_link(report_dir, statement_coverage)))

        for language in ("python", "ruby"):
            html_index = scope_dir / language / "html" / "index.html"
            if not html_index.is_file():
                html_index = scope_dir / language / "index.html"
            if html_index.is_file():
                links.append((scope_dir.name, language, relative_link(report_dir, html_index)))
    return links


def replace_placeholders(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{ {key} }}}}", value)
    return rendered


def template_path(name: str) -> Path:
    return SCRIPT_DIR / name


def render_suite_row(row_template: str, suite: dict[str, Any]) -> str:
    return replace_placeholders(
        row_template,
        {
            "scope": html.escape(suite["scope"]),
            "language": html.escape(suite["language"]),
            "module": html.escape(suite["module"]),
            "total": str(suite["total"]),
            "passed": str(suite["passed"]),
            "failed": str(suite["failed"]),
            "skipped": str(suite["skipped"]),
        },
    )


def render_coverage_items(report_dir: Path, item_template: str, empty_template: str) -> str:
    items = [
        replace_placeholders(
            item_template,
            {
                "href": href,
                "label": html.escape(f"{scope} {language} coverage"),
            },
        )
        for scope, language, href in coverage_links(report_dir)
    ]
    return "\n".join(items) if items else empty_template


def render_test_output(test: dict[str, Any], output_template: str, section_template: str) -> str:
    sections = []
    for label, key in (("stdout", "stdout"), ("stderr", "stderr"), ("failure", "failure")):
        if test.get(key):
            sections.append(
                replace_placeholders(
                    section_template,
                    {
                        "label": html.escape(label),
                        "content": html.escape(test[key]),
                    },
                ),
            )
    if not sections:
        return ""
    return replace_placeholders(output_template, {"sections": "".join(sections)})


def render_test_row(
    test: dict[str, Any],
    row_template: str,
    *,
    output_template: str,
    output_section_template: str,
) -> str:
    status = test["status"]
    return replace_placeholders(
        row_template,
        {
            "status_class": html.escape(status),
            "status": html.escape(status),
            "scope": html.escape(test["scope"]),
            "language": html.escape(test["language"]),
            "module": html.escape(test["module"]),
            "name": html.escape(test["name"]),
            "output": render_test_output(test, output_template, output_section_template),
        },
    )


def render_html(
    report_dir: Path,
    model: dict[str, Any],
    *,
    template_file: Path | None = None,
    suite_row_template_file: Path | None = None,
    test_row_template_file: Path | None = None,
    coverage_item_template_file: Path | None = None,
    coverage_empty_template_file: Path | None = None,
    test_output_template_file: Path | None = None,
    test_output_section_template_file: Path | None = None,
) -> str:
    template = (template_file or template_path("index.html")).read_text(encoding="utf-8")
    suite_row_template = (suite_row_template_file or template_path("suite-row.html")).read_text(encoding="utf-8").rstrip("\n")
    test_row_template = (test_row_template_file or template_path("test-row.html")).read_text(encoding="utf-8").rstrip("\n")
    coverage_item_template = (coverage_item_template_file or template_path("coverage-item.html")).read_text(encoding="utf-8").rstrip("\n")
    coverage_empty_template = (coverage_empty_template_file or template_path("coverage-empty.html")).read_text(encoding="utf-8").rstrip("\n")
    test_output_template = (test_output_template_file or template_path("test-output.html")).read_text(encoding="utf-8").rstrip("\n")
    test_output_section_template = (
        test_output_section_template_file or template_path("test-output-section.html")
    ).read_text(encoding="utf-8").rstrip("\n")
    summary = model["summary"]
    suite_rows = "\n".join(
        render_suite_row(suite_row_template, suite) for suite in model["suites"]
    )
    test_rows = "\n".join(
        render_test_row(
            test,
            test_row_template,
            output_template=test_output_template,
            output_section_template=test_output_section_template,
        )
        for test in model["tests"]
    )
    return replace_placeholders(
        template,
        {
            "total": str(summary["total"]),
            "passed": str(summary["passed"]),
            "failed": str(summary["failed"]),
            "skipped": str(summary["skipped"]),
            "coverage_items": render_coverage_items(report_dir, coverage_item_template, coverage_empty_template),
            "suite_rows": suite_rows,
            "test_rows": test_rows,
        },
    )


def _remove_published_report(report_dir: Path) -> None:
    for path in (report_dir / "index.html", report_dir / "test-results.json"):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _render_report(report_dir: Path, model: dict[str, Any]) -> None:
    _remove_published_report(report_dir)
    pages.render_coverage_drilldown_pages(report_dir, model["coverage"])
    pages.render_coverage_run_overview_pages(report_dir, model["run_overviews"])
    pages.render_test_drilldown_pages(report_dir, model["tests"])
    pages.render_test_run_overview_pages(report_dir, model["run_overviews"])
    pages.render_runner_step_pages(report_dir, model["runner_report"])

    json_temp = report_dir / ".test-results.json.tmp"
    index_temp = report_dir / ".index.html.tmp"
    try:
        json_temp.write_text(
            json.dumps(serialize_report_model(model), indent=2) + "\n",
            encoding="utf-8",
        )
        pages.render_report_homepage(
            report_dir,
            coverage_tree=model["coverage_tree"],
            test_tree=model["test_tree"],
            coverage_links=model["coverage_links"],
            test_links=model["test_links"],
            run_overviews=model["run_overviews"],
            coverage_gates=list(model["coverage_gate_report"].results),
            runner_report=model["runner_report"],
            coverage_override=model["coverage_override"],
            destination=index_temp,
        )
        os.replace(json_temp, report_dir / "test-results.json")
        os.replace(index_temp, report_dir / "index.html")
    except Exception:
        _remove_published_report(report_dir)
        raise
    finally:
        for path in (json_temp, index_temp):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def main() -> int:
    args = parse_args()
    report_dir = args.report_dir
    if not args.summary_only:
        _remove_published_report(report_dir)
    model = build_report_model(
        report_dir,
        repo_root=args.repo_root,
        summary_file=None if args.summary_only else args.summary_file,
    )
    legacy_summary = model["legacy_summary"]
    if not args.render_only:
        append_summary_metrics(args.summary_file, legacy_summary)
        append_coverage_gate_summary_metrics(args.summary_file, model["coverage_gate_report"])
    if not args.summary_only:
        _render_report(report_dir, model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
