from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TestTotals:
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0

    def add(self, other: "TestTotals") -> None:
        self.total += other.total
        self.passed += other.passed
        self.failed += other.failed
        self.skipped += other.skipped


@dataclass(frozen=True)
class CoverageMetric:
    name: str
    covered: int | None
    total: int | None

    @property
    def display(self) -> str:
        if self.covered is None or self.total is None:
            return "n/a"
        if self.total == 0:
            return "0 / 0 (n/a)"
        percent = self.covered / self.total * 100
        return f"{self.covered} / {self.total} ({percent:.0f}%)"


@dataclass(frozen=True)
class TestCaseResult:
    scope: str
    language: str
    test_path: Path
    module: str
    name: str
    status: str
    time: float
    stdout: str = ""
    stderr: str = ""
    failure: str = ""
    failure_message: str = ""
    source_line: int | None = None
    source_excerpt: str = ""
    execution_log: Path | None = None
    case_output_log: Path | None = None
    assertion_stdout: str = ""
    assertion_stderr: str = ""
    assertion_failure: str = ""
    tested_stdout: str = ""
    tested_stderr: str = ""
    tested_failure: str = ""
    report_phases: tuple[dict[str, str], ...] = ()

    @property
    def totals(self) -> TestTotals:
        return TestTotals(
            total=1,
            passed=1 if self.status == "passed" else 0,
            failed=1 if self.status not in ("passed", "skipped") else 0,
            skipped=1 if self.status == "skipped" else 0,
        )


@dataclass(frozen=True)
class CoverageRecord:
    scope: str
    language: str
    source_path: Path
    metrics: tuple[CoverageMetric, ...]
    detail_kind: str
    detail_rows: tuple[dict[str, str], ...] = ()
    native_report: Path | None = None
    covered_source_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class CoverageManifestEntry:
    scope: str
    language: str
    source_path: Path
    note: str = ""


@dataclass(frozen=True)
class CoverageGateResult:
    kind: str
    severity: str
    scope: str
    language: str
    metric: str
    covered: int
    total: int
    threshold_percent: float
    source_path: Path | None = None

    @property
    def percent(self) -> float:
        if self.total == 0:
            return 0.0
        return self.covered / self.total * 100

    @property
    def display(self) -> str:
        if self.metric == "manifest":
            return "covered" if self.covered else "missing coverage"
        return f"{self.covered} / {self.total} ({self.percent:.2f}%)"


@dataclass(frozen=True)
class CoverageGateReport:
    command_scope: str
    threshold_percent: float
    hard_results: tuple[CoverageGateResult, ...] = ()
    warnings: tuple[CoverageGateResult, ...] = ()

    @property
    def failures(self) -> tuple[CoverageGateResult, ...]:
        return tuple(result for result in self.hard_results if result.severity == "fail")

    @property
    def results(self) -> tuple[CoverageGateResult, ...]:
        return (*self.hard_results, *self.warnings)


@dataclass(frozen=True)
class RunOverviewSection:
    title: str
    output_log: Path
    output: str


@dataclass(frozen=True)
class LanguageRunOverview:
    scope: str
    language: str
    output_log: Path | None = None
    test_output: str = ""
    coverage_output: str = ""
    sections: tuple[RunOverviewSection, ...] = ()


@dataclass(frozen=True)
class RunnerStepResult:
    ordinal: int
    label: str
    status: str
    exit_code: int
    output_log: Path


@dataclass(frozen=True)
class ExecutionFailure:
    sequence: int
    label: str
    exit_code: int


@dataclass(frozen=True)
class ExecutionStatus:
    status: str
    exit_code: int
    detail: str = "recorded"


@dataclass(frozen=True)
class RunnerReport:
    execution: ExecutionStatus
    steps: tuple[RunnerStepResult, ...] = ()
    failures: tuple[ExecutionFailure, ...] = ()


@dataclass
class TreeNode:
    name: str
    path: Path
    children: dict[str, "TreeNode"] = field(default_factory=dict)
    test_totals: TestTotals = field(default_factory=TestTotals)
    coverage_records: list[CoverageRecord] = field(default_factory=list)
    tests: list[TestCaseResult] = field(default_factory=list)

    def child(self, name: str) -> "TreeNode":
        path = self.path / name
        if name not in self.children:
            self.children[name] = TreeNode(name=name, path=path)
        return self.children[name]

    def recalculate_totals(self) -> None:
        for child in self.children.values():
            child.recalculate_totals()
        self.test_totals = TestTotals()
        for test in self.tests:
            self.test_totals.add(test.totals)
        for child in self.children.values():
            self.test_totals.add(child.test_totals)
