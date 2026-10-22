from __future__ import annotations

import csv
import json
import re
import sys
import xml.etree.ElementTree as ET
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

MODEL_MODULE_NAME = "_nix_darwin_test_report_model"


def load_sibling_model():
    if MODEL_MODULE_NAME in sys.modules:
        return sys.modules[MODEL_MODULE_NAME]

    model_path = Path(__file__).resolve().with_name("model.py")
    spec = spec_from_file_location(MODEL_MODULE_NAME, model_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load test report model from {model_path}")
    model = module_from_spec(spec)
    sys.modules[MODEL_MODULE_NAME] = model
    spec.loader.exec_module(model)
    return model


MODEL = load_sibling_model()
TestCaseResult = MODEL.TestCaseResult
CoverageMetric = MODEL.CoverageMetric
CoverageManifestEntry = MODEL.CoverageManifestEntry
CoverageRecord = MODEL.CoverageRecord
ExecutionFailure = MODEL.ExecutionFailure
ExecutionStatus = MODEL.ExecutionStatus
RunnerReport = MODEL.RunnerReport
RunnerStepResult = MODEL.RunnerStepResult


ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
XML_FORBIDDEN_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
NIX_STATUS_PREFIXES = {
    "\u2705 ": "passed",
    "\u274c ": "failed",
}
NIX_SUMMARY_PATTERN = re.compile(r"^[\U0001f389\U0001f622]\s+\d+/\d+\s+successful$")
NIX_FINAL_ERROR_PATTERN = re.compile(r"^error:\s+Tests failed$")
TAP_RESULT_PATTERN = re.compile(r"^(not )?ok\s+\d+\s+-\s+(.*)$")
NOT_COVERED_STATUSES = {"missed", "uncovered"}
PYTEST_CAPTURE_SECTION_PATTERN = re.compile(r"^-+\s+(Captured (?:Log|Out|Err))\s+-+$")
PYTEST_COVERAGE_HEADING_PATTERN = re.compile(r"^=+\s+tests coverage\s+=+$")
PYTEST_FINAL_SUMMARY_PATTERN = re.compile(
    r"^=+\s+.*\b(?:passed|failed|error|errors|skipped|xfailed|xpassed|warnings?)\b.*\bin\s+[\d.]+s\s+=+$",
)
RUBY_COVERAGE_START_PATTERN = re.compile(r"^Coverage report generated for\b")


def text_or_empty(element: ET.Element | None) -> str:
    if element is None or element.text is None:
        return ""
    return element.text


def parse_xml_file(xml_file: Path) -> ET.Element:
    raw_xml = xml_file.read_text(encoding="utf-8", errors="replace")
    return ET.fromstring(XML_FORBIDDEN_CONTROL_CHARS.sub("", raw_xml))


def first_text(*elements: ET.Element | None) -> str:
    for element in elements:
        text = text_or_empty(element)
        if text:
            return text
    return ""


def trim_capture_text(value: str) -> str:
    return value.strip("\n")


def split_pytest_capture_sections(value: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current_section = ""
    found_section = False

    for line in value.splitlines():
        match = PYTEST_CAPTURE_SECTION_PATTERN.match(line)
        if match is not None:
            current_section = match.group(1)
            sections.setdefault(current_section, [])
            found_section = True
            continue
        if current_section:
            sections[current_section].append(line)

    if not found_section:
        return {}
    return {name: trim_capture_text("\n".join(lines)) for name, lines in sections.items()}


def split_pytest_runner_output(raw_output: str) -> tuple[str, str]:
    lines = raw_output.splitlines(keepends=True)
    coverage_start = None
    for index, line in enumerate(lines):
        if PYTEST_COVERAGE_HEADING_PATTERN.match(line.rstrip("\n")):
            coverage_start = index
            break

    if coverage_start is None:
        return raw_output, ""

    final_summary_index = None
    for index in range(len(lines) - 1, coverage_start, -1):
        if PYTEST_FINAL_SUMMARY_PATTERN.match(lines[index].rstrip("\n")):
            final_summary_index = index
            break

    test_lines = list(lines[:coverage_start])
    coverage_lines = list(lines[coverage_start:])
    if final_summary_index is not None:
        summary_line = lines[final_summary_index]
        coverage_lines = lines[coverage_start:final_summary_index] + lines[final_summary_index + 1 :]
        if test_lines and test_lines[-1].strip():
            test_lines.append("\n")
        test_lines.append(summary_line)

    return "".join(test_lines), "".join(coverage_lines)


def split_ruby_runner_output(raw_output: str) -> tuple[str, str]:
    lines = raw_output.splitlines(keepends=True)
    coverage_start = None
    for index, line in enumerate(lines):
        if RUBY_COVERAGE_START_PATTERN.match(line.rstrip("\n")):
            coverage_start = index
            break

    if coverage_start is None:
        return raw_output, ""

    return "".join(lines[:coverage_start]), "".join(lines[coverage_start:])


def iter_validated_tsv_rows(tsv_file: Path, required_headers: tuple[str, ...]):
    with tsv_file.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fieldnames = reader.fieldnames or []
        missing_headers = [header for header in required_headers if header not in fieldnames]
        if missing_headers:
            raise ValueError(f"{tsv_file}: missing required header(s): {', '.join(missing_headers)}")

        for row_number, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f"{tsv_file}: row {row_number}: unexpected extra value(s)")
            for header in required_headers:
                if row.get(header) in (None, ""):
                    raise ValueError(f"{tsv_file}: row {row_number}: missing value for {header}")
            yield row_number, row


def _positive_int(tsv_file: Path, row_number: int, field: str, value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{tsv_file}: row {row_number}: invalid {field}: {value}") from error
    if parsed <= 0:
        raise ValueError(f"{tsv_file}: row {row_number}: {field} must be positive")
    return parsed


def _exit_code(tsv_file: Path, row_number: int, value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{tsv_file}: row {row_number}: invalid exit_code: {value}") from error
    if not 0 <= parsed <= 255:
        raise ValueError(f"{tsv_file}: row {row_number}: exit_code must be between 0 and 255")
    return parsed


def _report_output_path(report_dir: Path, tsv_file: Path, row_number: int, value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{tsv_file}: row {row_number}: output_log must be report-relative")
    resolved_report = report_dir.resolve()
    resolved_output = (report_dir / path).resolve()
    if resolved_output != resolved_report and resolved_report not in resolved_output.parents:
        raise ValueError(f"{tsv_file}: row {row_number}: output_log escapes the report")
    if not resolved_output.is_file():
        raise ValueError(f"{tsv_file}: row {row_number}: output_log does not exist: {value}")
    return path


def parse_runner_report(report_dir: Path) -> RunnerReport:
    runner_dir = report_dir / "runner"
    steps_file = runner_dir / "steps.tsv"
    failures_file = runner_dir / "failures.tsv"
    execution_file = runner_dir / "execution.tsv"
    steps_present = steps_file.is_file()
    execution_present = execution_file.is_file()

    if not steps_present and not execution_present:
        return RunnerReport(ExecutionStatus("unknown", 0, "not recorded"))
    if execution_present and not steps_present:
        raise ValueError(f"{runner_dir}: execution status exists without runner steps")
    if not failures_file.is_file():
        raise ValueError(f"{runner_dir}: missing failures.tsv")

    steps: list[RunnerStepResult] = []
    for row_number, row in iter_validated_tsv_rows(
        steps_file,
        ("ordinal", "label", "status", "exit_code", "output_log"),
    ):
        ordinal = _positive_int(steps_file, row_number, "ordinal", row["ordinal"])
        if ordinal != len(steps) + 1:
            raise ValueError(f"{steps_file}: row {row_number}: ordinals must be consecutive")
        status = row["status"]
        if status not in ("pass", "fail"):
            raise ValueError(f"{steps_file}: row {row_number}: invalid status: {status}")
        exit_code = _exit_code(steps_file, row_number, row["exit_code"])
        if (status == "pass") != (exit_code == 0):
            raise ValueError(f"{steps_file}: row {row_number}: status and exit_code disagree")
        steps.append(
            RunnerStepResult(
                ordinal,
                row["label"],
                status,
                exit_code,
                _report_output_path(report_dir, steps_file, row_number, row["output_log"]),
            ),
        )

    failures: list[ExecutionFailure] = []
    for row_number, row in iter_validated_tsv_rows(
        failures_file,
        ("sequence", "label", "exit_code"),
    ):
        sequence = _positive_int(failures_file, row_number, "sequence", row["sequence"])
        if sequence != len(failures) + 1:
            raise ValueError(f"{failures_file}: row {row_number}: sequences must be consecutive")
        exit_code = _exit_code(failures_file, row_number, row["exit_code"])
        if exit_code == 0:
            raise ValueError(f"{failures_file}: row {row_number}: failure exit_code must be nonzero")
        failures.append(ExecutionFailure(sequence, row["label"], exit_code))

    if not execution_present:
        return RunnerReport(
            ExecutionStatus("unknown", 0, "incomplete"),
            tuple(steps),
            tuple(failures),
        )

    execution_rows = list(iter_validated_tsv_rows(execution_file, ("status", "exit_code")))
    if len(execution_rows) != 1:
        raise ValueError(f"{execution_file}: expected exactly one status row")
    row_number, row = execution_rows[0]
    status = row["status"]
    if status not in ("pass", "fail"):
        raise ValueError(f"{execution_file}: row {row_number}: invalid status: {status}")
    exit_code = _exit_code(execution_file, row_number, row["exit_code"])
    if (status == "pass") != (exit_code == 0):
        raise ValueError(f"{execution_file}: row {row_number}: status and exit_code disagree")
    failed_steps = {(step.label, step.exit_code) for step in steps if step.status == "fail"}
    recorded_failures = {(failure.label, failure.exit_code) for failure in failures}
    if not failed_steps.issubset(recorded_failures):
        raise ValueError(f"{failures_file}: missing failed runner-step attribution")
    if status == "pass" and failures:
        raise ValueError(f"{execution_file}: passing execution has recorded failures")
    if status == "fail":
        if not failures:
            raise ValueError(f"{execution_file}: failed execution has no attributed failure")
        if failures[0].exit_code != exit_code:
            raise ValueError(f"{execution_file}: exit_code does not match the first failure")
    return RunnerReport(ExecutionStatus(status, exit_code), tuple(steps), tuple(failures))


def parse_coverage_override(summary_file: Path, command_scope: str) -> bool:
    if not summary_file.is_file():
        return False
    matches = 0
    for row_number, line in enumerate(summary_file.read_text(encoding="utf-8").splitlines(), start=1):
        fields = line.split("\t")
        if len(fields) < 3 or fields[:3] != [command_scope, "coverage", "coverage.gate.override"]:
            continue
        if len(fields) != 4 or fields[3] != "used":
            raise ValueError(f"{summary_file}: row {row_number}: invalid coverage override row")
        matches += 1
    if matches > 1:
        raise ValueError(f"{summary_file}: duplicate coverage override rows")
    return matches == 1


def percent_metric(name: str, percent: float):
    basis = 1000
    return CoverageMetric(name, round(percent / 100 * basis), basis)


def rate_metric(name: str, rate: str):
    return percent_metric(name, float(rate) * 100)


def coverage_status_value(tsv_file: Path, row_number: int, status: str) -> int:
    if status == "covered":
        return 1
    if status in NOT_COVERED_STATUSES:
        return 0
    raise ValueError(f"{tsv_file}: row {row_number}: unknown status {status!r}")


def int_tsv_field(tsv_file: Path, row_number: int, row: dict[str, str], field_name: str) -> int:
    try:
        return int(row[field_name])
    except ValueError as error:
        raise ValueError(f"{tsv_file}: row {row_number}: invalid integer for {field_name}: {row[field_name]!r}") from error


def _relative_to_repo(path: Path, repo_root: Path | None) -> Path:
    if repo_root is None:
        return path
    try:
        return path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return path


def _unique_paths(paths: list[Path]) -> tuple[Path, ...]:
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return tuple(unique)


def parse_declared_target_coverage(
    scope: str,
    language: str,
    coverage_file: Path,
    *,
    metric_name: str,
) -> list[CoverageRecord]:
    records: list[CoverageRecord] = []
    for row_number, row in iter_validated_tsv_rows(coverage_file, ("target", "status")):
        target = Path(row["target"])
        covered = coverage_status_value(coverage_file, row_number, row["status"])
        records.append(
            CoverageRecord(
                scope=scope,
                language=language,
                source_path=target,
                metrics=(CoverageMetric(metric_name, covered, 1),),
                detail_kind=metric_name,
                detail_rows=(dict(row),),
                covered_source_paths=(target,),
            ),
        )
    return records


def parse_declared_statement_coverage(scope: str, language: str, coverage_file: Path) -> list[CoverageRecord]:
    records: list[CoverageRecord] = []
    required_headers = ("target", "status", "covered_statements", "total_statements")
    for row_number, row in iter_validated_tsv_rows(coverage_file, required_headers):
        coverage_status_value(coverage_file, row_number, row["status"])
        covered_statements = int_tsv_field(coverage_file, row_number, row, "covered_statements")
        total_statements = int_tsv_field(coverage_file, row_number, row, "total_statements")
        records.append(
            CoverageRecord(
                scope=scope,
                language=language,
                source_path=Path(row["target"]),
                metrics=(CoverageMetric("statement", covered_statements, total_statements),),
                detail_kind="statement-target",
                detail_rows=(dict(row),),
                covered_source_paths=(Path(row["target"]),),
            ),
        )
    return records


def python_covered_source_paths(root: ET.Element, repo_root: Path | None) -> tuple[Path, ...]:
    source_roots = [
        Path(source.text)
        for source in root.findall("./sources/source")
        if source.text
    ]
    covered_paths: list[Path] = []
    for class_element in root.findall(".//class"):
        filename = class_element.attrib.get("filename")
        if not filename:
            continue
        filename_path = Path(filename)
        candidates = [filename_path]
        if not filename_path.is_absolute():
            candidates = [source_root / filename_path for source_root in source_roots]
        existing_candidate = next((candidate for candidate in candidates if candidate.is_file()), None)
        candidate = existing_candidate or candidates[0]
        covered_paths.append(_relative_to_repo(candidate, repo_root))
    return _unique_paths(covered_paths)


def parse_python_coverage(scope: str, coverage_file: Path, *, repo_root: Path | None = None) -> CoverageRecord:
    root = ET.parse(coverage_file).getroot()
    metrics = [rate_metric("statement", root.attrib["line-rate"])]
    branch_rate = root.attrib.get("branch-rate")
    if branch_rate:
        metrics.append(rate_metric("branch", branch_rate))
    native_report = coverage_file.parent / "html" / "index.html"
    return CoverageRecord(
        scope=scope,
        language="python",
        source_path=Path("."),
        metrics=tuple(metrics),
        detail_kind="native",
        native_report=native_report if native_report.is_file() else coverage_file,
        covered_source_paths=python_covered_source_paths(root, repo_root),
    )


def ruby_covered_source_paths(coverage_file: Path, repo_root: Path | None) -> tuple[Path, ...]:
    resultset_file = coverage_file.parent / ".resultset.json"
    if not resultset_file.is_file():
        return ()
    payload = json.loads(resultset_file.read_text(encoding="utf-8"))
    covered_paths: list[Path] = []
    for suite in payload.values():
        coverage = suite.get("coverage", {})
        for source_path in coverage:
            covered_paths.append(_relative_to_repo(Path(source_path), repo_root))
    return _unique_paths(covered_paths)


def parse_ruby_coverage(scope: str, coverage_file: Path, *, repo_root: Path | None = None) -> CoverageRecord:
    payload = json.loads(coverage_file.read_text(encoding="utf-8"))
    result = payload.get("result", payload)
    metrics = []
    if "line" in result:
        metrics.append(percent_metric("statement", float(result["line"])))
    elif "covered_percent" in result:
        metrics.append(percent_metric("statement", float(result["covered_percent"])))
    if "branch" in result:
        metrics.append(percent_metric("branch", float(result["branch"])))
    native_report = coverage_file.parent / "index.html"
    return CoverageRecord(
        scope=scope,
        language="ruby",
        source_path=Path("."),
        metrics=tuple(metrics),
        detail_kind="native",
        native_report=native_report if native_report.is_file() else coverage_file,
        covered_source_paths=ruby_covered_source_paths(coverage_file, repo_root),
    )


def bash_covered_source_paths(payload: dict[str, Any], repo_root: Path | None) -> tuple[Path, ...]:
    covered_paths: list[Path] = []
    for file_entry in payload.get("files", []):
        source_path = file_entry.get("file")
        if source_path:
            covered_paths.append(_relative_to_repo(Path(source_path), repo_root))
    return _unique_paths(covered_paths)


def parse_bash_kcov_coverage(scope: str, coverage_file: Path, *, repo_root: Path | None = None) -> CoverageRecord:
    payload = json.loads(coverage_file.read_text(encoding="utf-8"))
    if "covered_lines" in payload and "total_lines" in payload:
        metric = CoverageMetric("statement", int(payload["covered_lines"]), int(payload["total_lines"]))
    else:
        metric = percent_metric("statement", float(payload["percent_covered"]))
    native_report = coverage_file.parent.parent / "index.html"
    return CoverageRecord(
        scope=scope,
        language="bash",
        source_path=Path("."),
        metrics=(metric,),
        detail_kind="native",
        native_report=native_report if native_report.is_file() else coverage_file,
        covered_source_paths=bash_covered_source_paths(payload, repo_root),
    )


def scope_from_report_path(report_dir: Path, coverage_file: Path) -> str:
    return coverage_file.relative_to(report_dir).parts[0]


def parse_coverage_manifest(manifest_file: Path) -> list[CoverageManifestEntry]:
    entries: list[CoverageManifestEntry] = []
    for _row_number, row in iter_validated_tsv_rows(manifest_file, ("scope", "language", "source_path")):
        entries.append(
            CoverageManifestEntry(
                scope=row["scope"],
                language=row["language"],
                source_path=Path(row["source_path"]),
                note=row.get("note") or "",
            ),
        )
    return entries


def discover_coverage_manifest(repo_root: Path | None) -> list[CoverageManifestEntry]:
    if repo_root is None:
        return []
    manifest_file = repo_root / "src/test/coverage-manifest.tsv"
    if not manifest_file.is_file():
        return []
    return parse_coverage_manifest(manifest_file)


def discover_coverage(report_dir: Path, *, repo_root: Path | None = None) -> list[CoverageRecord]:
    records: list[CoverageRecord] = []

    for language in ("bash", "nix", "python", "ruby"):
        for coverage_file in sorted(report_dir.glob(f"*/{language}/source-target-coverage.tsv")):
            records.extend(
                parse_declared_target_coverage(
                    scope_from_report_path(report_dir, coverage_file),
                    language,
                    coverage_file,
                    metric_name="source-target",
                ),
            )

    for language in ("awk", "applescript", "zsh"):
        for coverage_file in sorted(report_dir.glob(f"*/{language}/statement-coverage.tsv")):
            records.extend(
                parse_declared_statement_coverage(
                    scope_from_report_path(report_dir, coverage_file),
                    language,
                    coverage_file,
                ),
            )

    for coverage_file in sorted(report_dir.glob("*/python/coverage.xml")):
        records.append(
            parse_python_coverage(
                scope_from_report_path(report_dir, coverage_file),
                coverage_file,
                repo_root=repo_root,
            ),
        )

    for coverage_file in sorted(report_dir.glob("*/ruby/.last_run.json")):
        records.append(
            parse_ruby_coverage(
                scope_from_report_path(report_dir, coverage_file),
                coverage_file,
                repo_root=repo_root,
            ),
        )

    for coverage_file in sorted(report_dir.glob("*/bash/kcov/kcov-merged/coverage.json")):
        records.append(
            parse_bash_kcov_coverage(
                scope_from_report_path(report_dir, coverage_file),
                coverage_file,
                repo_root=repo_root,
            ),
        )

    return records


def python_classname_to_path(classname: str) -> Path:
    parts = classname.split(".")
    if (
        len(parts) >= 6
        and parts[:2] == ["src", "test"]
        and parts[3] == "python"
        and parts[-1] == "test"
        and parts[-2] in {"unit", "int"}
    ):
        return Path("src/test") / parts[2] / "python" / f"{'.'.join(parts[4:])}.py"
    return Path(*parts).with_suffix(".py")


def test_path_from_report(scope: str, language: str, artifact_file: Path) -> Path:
    if language in {"bash", "awk", "applescript"}:
        return Path("src/test") / scope / language / artifact_file.parent.name
    if language == "nix":
        return Path("src/test") / scope / "nix" / artifact_file.parent.name
    if language == "zsh":
        return Path("src/test") / scope / "zsh" / artifact_file.parent.name
    return Path("src/test") / scope / language / artifact_file.name


def source_path_from_recorded_file(recorded_file: str, fallback: Path) -> Path:
    if not recorded_file:
        return fallback

    path = Path(recorded_file)
    parts = path.parts
    if "src" in parts:
        return Path(*parts[parts.index("src") :])
    return path


def parse_junit_file(scope: str, language: str, junit_file: Path) -> list[TestCaseResult]:
    root = parse_xml_file(junit_file)
    results: list[TestCaseResult] = []
    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
    if not suites:
        suites = [root]

    for suite in suites:
        suite_stdout = suite.find("system-out")
        suite_stderr = suite.find("system-err")
        for case in suite.findall("testcase"):
            failure = case.find("failure")
            error = case.find("error")
            skipped = case.find("skipped")
            status = "passed"
            detail_element = None
            if skipped is not None:
                status = "skipped"
                detail_element = skipped
            elif failure is not None:
                status = "failed"
                detail_element = failure
            elif error is not None:
                status = "failed"
                detail_element = error

            classname = case.attrib.get("classname", "")
            test_path = (
                python_classname_to_path(classname)
                if language == "python" and classname
                else test_path_from_report(scope, language, junit_file)
            )
            raw_stdout = first_text(case.find("system-out"), suite_stdout)
            raw_stderr = first_text(case.find("system-err"), suite_stderr)
            stdout_sections = split_pytest_capture_sections(raw_stdout)
            stderr_sections = split_pytest_capture_sections(raw_stderr)
            assertion_stdout = stdout_sections.get("Captured Log", "")
            assertion_stderr = ""
            tested_stdout = stdout_sections.get("Captured Out", "")
            tested_stderr = stderr_sections.get("Captured Err", "")
            if not stdout_sections:
                tested_stdout = raw_stdout
            if not stderr_sections:
                tested_stderr = raw_stderr
            failure_text = "" if detail_element is None else text_or_empty(detail_element)
            failure_message = "" if detail_element is None else detail_element.attrib.get("message", "")
            results.append(
                TestCaseResult(
                    scope=scope,
                    language=language,
                    test_path=test_path,
                    module=classname or suite.attrib.get("name", "") or junit_file.stem,
                    name=case.attrib.get("name", ""),
                    status=status,
                    time=float(case.attrib.get("time", "0") or 0),
                    stdout=tested_stdout,
                    stderr=tested_stderr,
                    failure=failure_text,
                    failure_message=failure_message,
                    assertion_stdout=assertion_stdout,
                    assertion_stderr=assertion_stderr,
                    assertion_failure=failure_text,
                    tested_stdout=tested_stdout,
                    tested_stderr=tested_stderr,
                ),
            )
    return results


def _json_report_longrepr_text(phase: dict) -> str:
    longrepr = phase.get("longrepr")
    if longrepr:
        return str(longrepr)
    traceback = phase.get("traceback")
    if traceback:
        return json.dumps(traceback, indent=2, sort_keys=True)
    crash = phase.get("crash")
    if crash:
        return json.dumps(crash, indent=2, sort_keys=True)
    return ""


def _json_report_failure_message(phase: dict) -> str:
    crash = phase.get("crash")
    if isinstance(crash, dict) and crash.get("message"):
        return str(crash["message"])
    failure_text = _json_report_longrepr_text(phase)
    for line in failure_text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _json_report_test_path(nodeid: str) -> Path:
    recorded_file = nodeid.split("::", 1)[0]
    return source_path_from_recorded_file(recorded_file, Path(recorded_file or "pytest-json-report"))


def _json_report_test_name(nodeid: str) -> str:
    return nodeid.rsplit("::", 1)[-1]


def _module_from_test_path(test_path: Path) -> str:
    return test_path.with_suffix("").as_posix().replace("/", ".")


def _join_chunks(chunks: list[str]) -> str:
    return "\n\n".join(chunk.strip("\n") for chunk in chunks if chunk.strip("\n"))


def _phase_duration(phase: dict) -> float:
    return float(phase.get("duration") or 0.0)


def _phase_record(phase_name: str, phase: dict) -> dict[str, str]:
    outcome = str(phase.get("outcome", ""))
    duration = _phase_duration(phase)
    record = {
        "phase": phase_name,
        "outcome": outcome,
        "duration": f"{duration:.3f}s",
    }
    failure_message = _json_report_failure_message(phase)
    if failure_message:
        record["message"] = failure_message
    return record


def _phase_outcomes(test: dict) -> list[str]:
    outcomes: list[str] = []
    for phase_name in ("setup", "call", "teardown"):
        phase = test.get(phase_name)
        if isinstance(phase, dict) and phase.get("outcome"):
            outcomes.append(str(phase["outcome"]))
    return outcomes


def _json_report_status(test: dict) -> str:
    outcome = str(test.get("outcome", ""))
    if outcome:
        return outcome
    outcomes = _phase_outcomes(test)
    if "failed" in outcomes:
        return "failed"
    if "skipped" in outcomes:
        return "skipped"
    return "passed"


def parse_pytest_json_report_file(scope: str, report_file: Path) -> list[TestCaseResult]:
    payload = json.loads(report_file.read_text(encoding="utf-8"))
    results: list[TestCaseResult] = []

    for test in payload.get("tests", []):
        nodeid = str(test.get("nodeid", ""))
        if not nodeid:
            continue
        test_path = _json_report_test_path(nodeid)
        phases: list[dict[str, str]] = []
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        log_chunks: list[str] = []
        failure_chunks: list[str] = []
        failure_messages: list[str] = []
        duration = 0.0

        for phase_name in ("setup", "call", "teardown"):
            phase = test.get(phase_name)
            if not isinstance(phase, dict):
                continue
            duration += _phase_duration(phase)
            phases.append(_phase_record(phase_name, phase))
            for key, chunks in (
                ("stdout", stdout_chunks),
                ("stderr", stderr_chunks),
                ("log", log_chunks),
            ):
                value = phase.get(key)
                if value:
                    chunks.append(str(value))
            failure_text = _json_report_longrepr_text(phase)
            failure_message = _json_report_failure_message(phase)
            if failure_text:
                failure_chunks.append(failure_text)
            if failure_message:
                failure_messages.append(failure_message)

        failure = _join_chunks(failure_chunks)
        stdout = _join_chunks(stdout_chunks)
        stderr = _join_chunks(stderr_chunks)
        log = _join_chunks(log_chunks)
        results.append(
            TestCaseResult(
                scope=scope,
                language="python",
                test_path=test_path,
                module=_module_from_test_path(test_path),
                name=_json_report_test_name(nodeid),
                status=_json_report_status(test),
                time=duration,
                stdout=stdout,
                stderr=stderr,
                failure=failure,
                failure_message=failure_messages[0] if failure_messages else "",
                source_line=int(test["lineno"]) + 1 if isinstance(test.get("lineno"), int) else None,
                assertion_stdout=log,
                assertion_failure=failure,
                tested_stdout=stdout,
                tested_stderr=stderr,
                report_phases=tuple(phases),
            ),
        )

    return results


def _ruby_failure_text(failure: dict[str, Any]) -> str:
    failure_class = failure.get("class", "")
    message = failure.get("message", "")
    header = ": ".join(part for part in (failure_class, message) if part)
    chunks = [header, *failure.get("backtrace", [])]
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def parse_ruby_file(scope: str, result_file: Path) -> list[TestCaseResult]:
    payload = json.loads(result_file.read_text(encoding="utf-8"))
    results: list[TestCaseResult] = []
    for test in payload.get("tests", []):
        failures = test.get("failures") or []
        failure_text = "\n\n".join(_ruby_failure_text(failure) for failure in failures)
        stdout = test.get("stdout", "")
        stderr = test.get("stderr", "")
        failure_message = failures[0].get("message", "") if failures else ""
        results.append(
            TestCaseResult(
                scope=scope,
                language="ruby",
                test_path=source_path_from_recorded_file(
                    test.get("file", ""),
                    test_path_from_report(scope, "ruby", result_file),
                ),
                module=test.get("classname", ""),
                name=test.get("name", ""),
                status=test.get("status", "passed"),
                time=float(test.get("time") or 0),
                stdout=stdout,
                stderr=stderr,
                failure=failure_text,
                failure_message=failure_message,
                source_line=test.get("line"),
                assertion_stdout=stdout,
                assertion_stderr=stderr,
                assertion_failure=failure_text,
                tested_stdout=stdout,
                tested_stderr=stderr,
            ),
        )
    return results


def parse_nix_output_file(scope: str, output_file: Path) -> list[TestCaseResult]:
    raw_output = output_file.read_text(encoding="utf-8")
    clean_output = ANSI_PATTERN.sub("", raw_output)
    lines = clean_output.splitlines()
    module = output_file.parent.name
    test_path = test_path_from_report(scope, "nix", output_file)
    results: list[TestCaseResult] = []
    index = 0

    def status_line(line: str) -> tuple[str, str] | None:
        for prefix, status in NIX_STATUS_PREFIXES.items():
            if line.startswith(prefix):
                return status, line.removeprefix(prefix).strip()
        return None

    def first_meaningful_line(value: str) -> str:
        for detail_line in value.splitlines():
            stripped = detail_line.strip()
            if stripped:
                return stripped
        return "nix-unit test failed"

    while index < len(lines):
        parsed_status = status_line(lines[index])
        if parsed_status is None:
            index += 1
            continue

        status, name = parsed_status
        failure = ""
        failure_message = ""
        next_index = index + 1

        if status == "failed":
            detail_lines: list[str] = []
            while next_index < len(lines):
                next_line = lines[next_index]
                if status_line(next_line) is not None:
                    break
                if NIX_SUMMARY_PATTERN.match(next_line) or NIX_FINAL_ERROR_PATTERN.match(next_line):
                    break
                detail_lines.append(next_line)
                next_index += 1
            failure = "\n".join(detail_lines).strip()
            failure_message = first_meaningful_line(failure)

        results.append(
            TestCaseResult(
                scope=scope,
                language="nix",
                test_path=test_path,
                module=module,
                name=name,
                status=status,
                time=0.0,
                stdout="",
                stderr="",
                failure=failure,
                failure_message=failure_message,
                assertion_failure=failure,
            ),
        )
        index = next_index

    return results


def parse_zunit_tap_file(scope: str, tap_file: Path) -> list[TestCaseResult]:
    raw_output = tap_file.read_text(encoding="utf-8")
    lines = raw_output.splitlines()
    module = tap_file.parent.name
    test_path = test_path_from_report(scope, "zsh", tap_file)
    results: list[TestCaseResult] = []
    pending_stdout: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        match = TAP_RESULT_PATTERN.match(line)
        if match is None:
            if line.startswith("TAP version") or re.match(r"^\d+\.\.\d+$", line) or line == "":
                index += 1
                continue
            pending_stdout.append(line)
            index += 1
            continue

        is_failure = match.group(1) is not None
        name = match.group(2).strip()
        status = "failed" if is_failure else "passed"
        stdout = "\n".join(pending_stdout).strip("\n")
        pending_stdout = []
        failure = ""
        failure_message = ""
        if name.startswith("Failure: "):
            name = name.removeprefix("Failure: ").strip()
        elif name.startswith("Error: "):
            name = name.removeprefix("Error: ").strip()

        if name.startswith("# SKIP "):
            status = "skipped"
            name = name.removeprefix("# SKIP ").strip()

        if status == "failed":
            detail_lines = []
            detail_index = index + 1
            in_zunit_detail_block = detail_index < len(lines) and lines[detail_index].strip() == "---"
            while detail_index < len(lines):
                detail_line = lines[detail_index]
                if TAP_RESULT_PATTERN.match(detail_line) or re.match(r"^\d+\.\.\d+$", detail_line):
                    break
                detail_lines.append(detail_line)
                detail_index += 1
                if in_zunit_detail_block and detail_line.strip() == "...":
                    break
            failure = "\n".join(detail_lines).strip() or raw_output
            message_match = re.search(r"^[ \t]*message:[ \t]*(.*)$", failure, flags=re.MULTILINE)
            failure_message = message_match.group(1).strip() if message_match else "zunit test failed"
            index = detail_index
        else:
            index += 1

        results.append(
            TestCaseResult(
                scope=scope,
                language="zsh",
                test_path=test_path,
                module=module,
                name=name,
                status=status,
                time=0.0,
                stdout=stdout,
                stderr="",
                failure=failure,
                failure_message=failure_message,
                assertion_failure=failure,
            ),
        )

    return results
