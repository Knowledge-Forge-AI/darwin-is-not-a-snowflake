from __future__ import annotations

import sys
from collections import defaultdict
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
MODEL_MODULE_NAME = "_nix_darwin_test_report_model"
HOST_SAFE_SUBSCOPES = ("unit", "int")


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
CoverageGateReport = MODEL.CoverageGateReport
CoverageGateResult = MODEL.CoverageGateResult
CoverageManifestEntry = MODEL.CoverageManifestEntry
CoverageRecord = MODEL.CoverageRecord
NON_GATED_SCOPE_METRICS = frozenset({
    ("int", "bash", "statement"),
})


def _coverage_key(scope: str, language: str, metric: str) -> tuple[str, str, str]:
    return (scope, language, metric)


def _metric_counts(records: list[CoverageRecord]) -> dict[tuple[str, str, str], tuple[int, int]]:
    counts: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])
    for record in records:
        for metric in record.metrics:
            if metric.covered is None or metric.total is None:
                continue
            if metric.total <= 0:
                continue
            bucket = counts[_coverage_key(record.scope, record.language, metric.name)]
            bucket[0] += metric.covered
            bucket[1] += metric.total
    return {key: (value[0], value[1]) for key, value in counts.items()}


def _severity(covered: int, total: int, threshold_percent: float, *, warning: bool = False) -> str:
    percent = covered / total * 100 if total else 0.0
    if percent >= threshold_percent:
        return "pass"
    return "warn" if warning else "fail"


def _result(
    *,
    kind: str,
    scope: str,
    language: str,
    metric: str,
    covered: int,
    total: int,
    threshold_percent: float,
    source_path: Path | None = None,
) -> CoverageGateResult:
    return CoverageGateResult(
        kind=kind,
        severity=_severity(covered, total, threshold_percent, warning=kind == "warning"),
        scope=scope,
        language=language,
        metric=metric,
        covered=covered,
        total=total,
        threshold_percent=threshold_percent,
        source_path=source_path,
    )


def _hard_counts_for_all(
    counts: dict[tuple[str, str, str], tuple[int, int]],
) -> dict[tuple[str, str, str], tuple[int, int]]:
    hard_counts: dict[tuple[str, str, str], tuple[int, int]] = {}
    all_keys = {key for key in counts if key[0] == "all"}
    for _scope, language, metric in sorted(all_keys):
        hard_counts[_coverage_key("all", language, metric)] = counts[_coverage_key("all", language, metric)]

    subscoped_keys = {
        (language, metric)
        for scope, language, metric in counts
        if (
            scope in HOST_SAFE_SUBSCOPES
            and _coverage_key(scope, language, metric) not in NON_GATED_SCOPE_METRICS
            and _coverage_key("all", language, metric) not in counts
        )
    }
    for language, metric in sorted(subscoped_keys):
        covered = 0
        total = 0
        for scope in HOST_SAFE_SUBSCOPES:
            scope_counts = counts.get(_coverage_key(scope, language, metric))
            if scope_counts is None:
                continue
            covered += scope_counts[0]
            total += scope_counts[1]
        if total:
            hard_counts[_coverage_key("all", language, metric)] = (covered, total)
    return hard_counts


def _hard_counts_for_scope(
    counts: dict[tuple[str, str, str], tuple[int, int]],
    command_scope: str,
) -> dict[tuple[str, str, str], tuple[int, int]]:
    if command_scope == "all":
        return _hard_counts_for_all(counts)
    return {
        key: value
        for key, value in counts.items()
        if key[0] == command_scope and key not in NON_GATED_SCOPE_METRICS
    }


def _warning_results(
    counts: dict[tuple[str, str, str], tuple[int, int]],
    *,
    command_scope: str,
    threshold_percent: float,
) -> list[CoverageGateResult]:
    if command_scope != "all":
        return []

    warnings: list[CoverageGateResult] = []
    for scope, language, metric in sorted(counts):
        if scope not in HOST_SAFE_SUBSCOPES:
            continue
        if _coverage_key(scope, language, metric) in NON_GATED_SCOPE_METRICS:
            continue
        covered, total = counts[_coverage_key(scope, language, metric)]
        result = _result(
            kind="warning",
            scope=scope,
            language=language,
            metric=metric,
            covered=covered,
            total=total,
            threshold_percent=threshold_percent,
        )
        if result.severity == "warn":
            warnings.append(result)
    return warnings


def _record_covered_paths(record: CoverageRecord) -> tuple[Path, ...]:
    if record.covered_source_paths:
        return record.covered_source_paths
    if record.source_path == Path("."):
        return ()
    return (record.source_path,)


def _covered_source_paths_by_scope_language(
    records: list[CoverageRecord],
) -> dict[tuple[str, str], set[Path]]:
    covered: dict[tuple[str, str], set[Path]] = defaultdict(set)
    for record in records:
        bucket = covered[(record.scope, record.language)]
        bucket.update(_record_covered_paths(record))
    return covered


def _manifest_entry_applies(entry: CoverageManifestEntry, command_scope: str) -> bool:
    if command_scope == "all":
        return entry.scope in {"all", *HOST_SAFE_SUBSCOPES}
    return entry.scope == command_scope


def _covered_paths_for_manifest_entry(
    covered_paths: dict[tuple[str, str], set[Path]],
    entry: CoverageManifestEntry,
    command_scope: str,
) -> set[Path]:
    if command_scope == "all" and entry.scope == "all":
        paths: set[Path] = set()
        for scope in ("all", *HOST_SAFE_SUBSCOPES):
            paths.update(covered_paths.get((scope, entry.language), set()))
        return paths
    return covered_paths.get((entry.scope, entry.language), set())


def _manifest_warnings(
    records: list[CoverageRecord],
    manifest_entries: list[CoverageManifestEntry],
    *,
    command_scope: str,
) -> list[CoverageGateResult]:
    covered_paths = _covered_source_paths_by_scope_language(records)
    warnings: list[CoverageGateResult] = []
    for entry in manifest_entries:
        if not _manifest_entry_applies(entry, command_scope):
            continue
        if entry.source_path in _covered_paths_for_manifest_entry(covered_paths, entry, command_scope):
            continue
        warnings.append(
            CoverageGateResult(
                kind="warning",
                severity="warn",
                scope=entry.scope,
                language=entry.language,
                metric="manifest",
                covered=0,
                total=1,
                threshold_percent=100.0,
                source_path=entry.source_path,
            ),
        )
    return warnings


def evaluate_coverage_gates(
    records: list[CoverageRecord],
    *,
    command_scope: str,
    threshold_percent: float = 85.0,
    manifest_entries: list[CoverageManifestEntry] | None = None,
) -> CoverageGateReport:
    counts = _metric_counts(records)
    hard_results = [
        _result(
            kind="hard",
            scope=scope,
            language=language,
            metric=metric,
            covered=covered,
            total=total,
            threshold_percent=threshold_percent,
        )
        for (scope, language, metric), (covered, total) in sorted(
            _hard_counts_for_scope(counts, command_scope).items(),
        )
    ]
    warnings = _warning_results(
        counts,
        command_scope=command_scope,
        threshold_percent=threshold_percent,
    )
    warnings.extend(
        _manifest_warnings(
            records,
            manifest_entries or [],
            command_scope=command_scope,
        ),
    )
    return CoverageGateReport(
        command_scope=command_scope,
        threshold_percent=threshold_percent,
        hard_results=tuple(hard_results),
        warnings=tuple(warnings),
    )
