# HTML Test Report Tree Implementation Plan

> **For agentic workers:** Execute this accepted plan under repository policy. Use `composing-bounded-worker-assignments` only after delegation is independently authorized and selected; use `reviewing-and-verifying-repository-work` for evidence-backed disposition or a completion claim. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat nix-darwin HTML test report with a two-tree report homepage plus focused drilldown pages for source coverage and test results.

**Architecture:** Keep `tools/test/report/render.py` as the CLI entrypoint, but move parsing, tree aggregation, and page rendering into focused Python modules. HTML and CSS remain in external template/static files so Python does not embed another source language.

**Tech Stack:** Python 3 stdlib, pytest, coverage.py branch coverage, existing JUnit XML, Minitest JSON, Nix output, Zunit TAP, TSV coverage artifacts, and static HTML/CSS templates.

---

## File Structure

- Modify: `<repo>/tools/test/report/render.py`
  - Keep argument parsing and top-level orchestration.
  - Delegate parsing, tree building, and HTML generation to new modules.
- Create: `<repo>/tools/test/report/model.py`
  - Dataclasses for normalized test results, coverage records, tree nodes, and generated pages.
- Create: `<repo>/tools/test/report/parsers.py`
  - Parse framework artifacts into normalized model records.
- Create: `<repo>/tools/test/report/trees.py`
  - Build source coverage and test result trees rooted at the product root.
- Create: `<repo>/tools/test/report/pages.py`
  - Render homepage and drilldown pages from templates.
- Create: `<repo>/tools/test/report/static/report.css`
  - Report styles, including fixed metric/status columns and overflow rules.
- Create: `<repo>/tools/test/report/templates/base.html`
  - Shared HTML shell with stylesheet link and content slot.
- Create: `<repo>/tools/test/report/templates/index.html`
  - Homepage with coverage source tree and test file tree.
- Create: `<repo>/tools/test/report/templates/coverage-file.html`
  - Coverage drilldown template.
- Create: `<repo>/tools/test/report/templates/test-file.html`
  - Test file and test case drilldown template.
- Modify: `<repo>/src/test/unit/python/test_report_renderer.unit.test.py`
  - Keep CLI-level regression coverage.
- Create: `<repo>/src/test/unit/python/test_report_model.unit.test.py`
  - Model and serialization tests.
- Create: `<repo>/src/test/unit/python/test_report_parsers.unit.test.py`
  - Artifact parser tests.
- Create: `<repo>/src/test/unit/python/test_report_trees.unit.test.py`
  - Coverage/test tree aggregation tests.
- Create: `<repo>/src/test/unit/python/test_report_pages.unit.test.py`
  - HTML rendering and overflow-rule tests.

---

### Task 1: Add Normalized Report Model

**Files:**
- Create: `<repo>/tools/test/report/model.py`
- Create: `<repo>/src/test/unit/python/test_report_model.unit.test.py`

- [ ] **Step 1: Write failing model tests**

Add this test file:

```python
from __future__ import annotations

from pathlib import Path


def test_tree_node_aggregates_test_counts(load_module):
    model = load_module("tools/test/report/model.py", "model")
    root = model.TreeNode("nix-darwin", Path("nix-darwin"))
    child = root.child("src").child("test").child("unit").child("python")
    child.test_totals.total = 2
    child.test_totals.passed = 1
    child.test_totals.failed = 1

    root.recalculate_totals()

    assert root.test_totals.total == 2
    assert root.test_totals.passed == 1
    assert root.test_totals.failed == 1
    assert root.test_totals.skipped == 0


def test_metric_formats_na_and_percentages(load_module):
    model = load_module("tools/test/report/model.py", "model")

    assert model.CoverageMetric("statement", None, None).display == "n/a"
    assert model.CoverageMetric("branch", 88, 100).display == "88 / 100 (88%)"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd <repo>/nix-darwin
pytest src/test/unit/python/test_report_model.unit.test.py -q
```

Expected: FAIL because `tools/test/report/model.py` does not exist.

- [ ] **Step 3: Implement the model module**

Create `<repo>/tools/test/report/model.py`:

```python
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

    @property
    def totals(self) -> TestTotals:
        return TestTotals(
            total=1,
            passed=1 if self.status == "passed" else 0,
            failed=1 if self.status == "failed" else 0,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd <repo>/nix-darwin
pytest src/test/unit/python/test_report_model.unit.test.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd <repo>
git add nix-darwin/tools/test/report/model.py nix-darwin/src/test/unit/python/test_report_model.unit.test.py
git commit -m "test-report: add normalized report model"
```

---

### Task 2: Extract Artifact Parsers

**Files:**
- Create: `<repo>/tools/test/report/parsers.py`
- Create: `<repo>/src/test/unit/python/test_report_parsers.unit.test.py`
- Modify: `<repo>/tools/test/report/render.py`

- [ ] **Step 1: Write failing parser tests**

Add parser tests covering one representative artifact per format:

```python
from __future__ import annotations

import json
from pathlib import Path


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
                        "file": "<repo>/src/test/unit/ruby/arq_list_wildcard_excludes.unit.test.rb",
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd <repo>/nix-darwin
pytest src/test/unit/python/test_report_parsers.unit.test.py -q
```

Expected: FAIL because `parsers.py` does not exist.

- [ ] **Step 3: Implement parser module**

Move parsing logic from `render.py` into `parsers.py`, returning `model.TestCaseResult` instead of dictionaries. Include path normalization helpers:

```python
def python_classname_to_path(classname: str) -> Path:
    return Path(*classname.split(".")).with_suffix(".py")


def test_path_from_report(scope: str, language: str, artifact_file: Path) -> Path:
    if language in {"bash", "awk", "applescript"}:
        return Path("src/test") / scope / language / artifact_file.parent.name
    if language == "nix":
        return Path("src/test") / scope / "nix" / artifact_file.parent.name
    if language == "zsh":
        return Path("src/test") / scope / "zsh" / artifact_file.parent.name
    return Path("src/test") / scope / language / artifact_file.name
```

Preserve the existing functions' behavior for statuses, stdout, stderr, failure text, and failure messages.

- [ ] **Step 4: Update `render.py` to import parser functions**

Change `discover_results()` in `render.py` to call the new parser module. Keep the public `discover_results(report_dir)` function available so existing tests continue to import it.

- [ ] **Step 5: Run parser and renderer tests**

Run:

```bash
cd <repo>/nix-darwin
pytest src/test/unit/python/test_report_parsers.unit.test.py src/test/unit/python/test_report_renderer.unit.test.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd <repo>
git add nix-darwin/tools/test/report/parsers.py nix-darwin/tools/test/report/render.py nix-darwin/src/test/unit/python/test_report_parsers.unit.test.py nix-darwin/src/test/unit/python/test_report_renderer.unit.test.py
git commit -m "test-report: extract artifact parsers"
```

---

### Task 3: Parse Coverage Artifacts Including Nix

**Files:**
- Modify: `<repo>/tools/test/report/parsers.py`
- Modify: `<repo>/src/test/unit/python/test_report_parsers.unit.test.py`

- [ ] **Step 1: Add failing coverage parser tests**

Append:

```python
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
    assert records[0].metrics[0].name == "statement"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd <repo>/nix-darwin
pytest src/test/unit/python/test_report_parsers.unit.test.py -q
```

Expected: FAIL because the coverage parser functions do not exist.

- [ ] **Step 3: Implement coverage parsing**

Add functions:

```python
def parse_declared_target_coverage(
    scope: str,
    language: str,
    coverage_file: Path,
    *,
    metric_name: str,
) -> list[CoverageRecord]:
    records: list[CoverageRecord] = []
    for row in read_tsv(coverage_file):
        target = Path(row["target"])
        covered = 1 if row["status"] == "covered" else 0
        records.append(
            CoverageRecord(
                scope=scope,
                language=language,
                source_path=target,
                metrics=(CoverageMetric(metric_name, covered, 1),),
                detail_kind=metric_name,
                detail_rows=(dict(row),),
            ),
        )
    return records


def parse_declared_statement_coverage(scope: str, language: str, coverage_file: Path) -> list[CoverageRecord]:
    records: list[CoverageRecord] = []
    for row in read_tsv(coverage_file):
        records.append(
            CoverageRecord(
                scope=scope,
                language=language,
                source_path=Path(row["target"]),
                metrics=(CoverageMetric("statement", int(row["covered_statements"]), int(row["total_statements"])),),
                detail_kind="statement-target",
                detail_rows=(dict(row),),
            ),
        )
    return records
```

Also add `discover_coverage(report_dir: Path) -> list[CoverageRecord]` that finds:

- `*/nix/source-target-coverage.tsv`
- `*/awk/statement-coverage.tsv`
- `*/applescript/statement-coverage.tsv`
- `*/zsh/statement-coverage.tsv`
- `*/python/coverage.xml`
- `*/ruby/.last_run.json`
- `*/bash/kcov/kcov-merged/coverage.json` when present

Python, Ruby, and Bash can initially include aggregate metric records plus native report links; detailed line parsing can remain native-link based in this slice.

- [ ] **Step 4: Run coverage parser tests**

Run:

```bash
cd <repo>/nix-darwin
pytest src/test/unit/python/test_report_parsers.unit.test.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd <repo>
git add nix-darwin/tools/test/report/parsers.py nix-darwin/src/test/unit/python/test_report_parsers.unit.test.py
git commit -m "test-report: parse normalized coverage artifacts"
```

---

### Task 4: Build Coverage and Test Trees

**Files:**
- Create: `<repo>/tools/test/report/trees.py`
- Create: `<repo>/src/test/unit/python/test_report_trees.unit.test.py`

- [ ] **Step 1: Write failing tree tests**

Add:

```python
from __future__ import annotations

from pathlib import Path


def test_build_test_tree_aggregates_by_test_path(load_module):
    model = load_module("tools/test/report/model.py", "model")
    trees = load_module("tools/test/report/trees.py")
    tests = [
        model.TestCaseResult("unit", "python", Path("src/test/unit/python/a.unit.test.py"), "a", "test_pass", "passed", 0.1),
        model.TestCaseResult("unit", "python", Path("src/test/unit/python/a.unit.test.py"), "a", "test_fail", "failed", 0.2),
        model.TestCaseResult("unit", "bash", Path("src/test/unit/bash/b.unit.test.bats"), "b", "test_skip", "skipped", 0.0),
    ]

    root = trees.build_test_tree(tests)

    assert root.path == Path("nix-darwin")
    assert root.test_totals.total == 3
    assert root.test_totals.passed == 1
    assert root.test_totals.failed == 1
    assert root.test_totals.skipped == 1


def test_build_coverage_tree_hides_untested_files(load_module):
    model = load_module("tools/test/report/model.py", "model")
    trees = load_module("tools/test/report/trees.py")
    records = [
        model.CoverageRecord(
            scope="all",
            language="nix",
            source_path=Path("modules/kitty.nix"),
            metrics=(model.CoverageMetric("source-target", 1, 1),),
            detail_kind="source-target",
        ),
    ]

    root = trees.build_coverage_tree(records)

    assert "modules" in root.children
    assert "kitty.nix" in root.children["modules"].children
    assert "untested.nix" not in root.children["modules"].children
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd <repo>/nix-darwin
pytest src/test/unit/python/test_report_trees.unit.test.py -q
```

Expected: FAIL because `trees.py` does not exist.

- [ ] **Step 3: Implement tree builders**

Create tree builder functions:

```python
from __future__ import annotations

from pathlib import Path

from model import CoverageRecord, TestCaseResult, TreeNode


def _insert_path(root: TreeNode, path: Path) -> TreeNode:
    node = root
    for part in path.parts:
        node = node.child(part)
    return node


def build_test_tree(tests: list[TestCaseResult]) -> TreeNode:
    root = TreeNode("nix-darwin", Path("nix-darwin"))
    for test in tests:
        node = _insert_path(root, test.test_path)
        node.tests.append(test)
    root.recalculate_totals()
    return root


def build_coverage_tree(records: list[CoverageRecord]) -> TreeNode:
    root = TreeNode("nix-darwin", Path("nix-darwin"))
    for record in records:
        node = _insert_path(root, record.source_path)
        node.coverage_records.append(record)
    return root
```

Adjust imports to work with the repo's `load_module` helper. If direct imports fail during tests, use package-relative import fallback:

```python
try:
    from .model import CoverageRecord, TestCaseResult, TreeNode
except ImportError:
    from model import CoverageRecord, TestCaseResult, TreeNode
```

- [ ] **Step 4: Run tree tests**

Run:

```bash
cd <repo>/nix-darwin
pytest src/test/unit/python/test_report_trees.unit.test.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd <repo>
git add nix-darwin/tools/test/report/trees.py nix-darwin/src/test/unit/python/test_report_trees.unit.test.py
git commit -m "test-report: build source and test result trees"
```

---

### Task 5: Render Homepage from Trees

**Files:**
- Create: `<repo>/tools/test/report/pages.py`
- Create: `<repo>/tools/test/report/static/report.css`
- Create: `<repo>/tools/test/report/templates/base.html`
- Create: `<repo>/tools/test/report/templates/index.html`
- Create: `<repo>/src/test/unit/python/test_report_pages.unit.test.py`

- [ ] **Step 1: Write failing homepage rendering tests**

Add:

```python
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
    assert "modules/kitty.nix" in html
    assert "src/test/unit/python/test_report_renderer.unit.test.py" in html
    assert "minmax(0, 1fr)" in css
    assert "text-overflow: ellipsis" in css
    assert "overflow-x: auto" in css
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd <repo>/nix-darwin
pytest src/test/unit/python/test_report_pages.unit.test.py -q
```

Expected: FAIL because `pages.py` and templates do not exist.

- [ ] **Step 3: Add external templates and CSS**

Create `static/report.css` with these required rules:

```css
:root {
  color-scheme: light;
  --bg: #f8fafc;
  --panel: #ffffff;
  --border: #d8dee9;
  --text: #1f2933;
  --muted: #66788a;
  --pass: #166534;
  --fail: #991b1b;
  --skip: #92400e;
  --link: #075985;
}

body {
  margin: 1.5rem;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  line-height: 1.4;
}

.report-shell {
  max-width: 1180px;
  margin: 0 auto;
}

.tree-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 6rem 6rem 6rem 7rem;
  width: 100%;
}

.test-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 4rem 4rem 4rem 4rem 7rem;
  width: 100%;
}

.cell {
  min-width: 0;
  padding: 0.45rem 0.55rem;
  border-bottom: 1px solid var(--border);
}

.path-cell {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-cell,
.metric-cell {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.code-block {
  overflow-x: auto;
  white-space: pre;
}
```

Create templates with placeholders only; do not embed CSS in Python. `base.html` should link `static/report.css`. `index.html` should contain `{{ coverage_tree }}` and `{{ test_tree }}` placeholders.

- [ ] **Step 4: Implement homepage rendering**

In `pages.py`, implement:

```python
def render_report_homepage(output_dir: Path, *, coverage_tree: TreeNode, test_tree: TreeNode) -> None:
    ensure_static_assets(output_dir)
    body = render_template(
        template_path("index.html"),
        {
            "coverage_tree": render_coverage_tree_rows(coverage_tree),
            "test_tree": render_test_tree_rows(test_tree),
        },
    )
    html = render_template(template_path("base.html"), {"title": "nix-darwin Test Report", "body": body})
    (output_dir / "index.html").write_text(html, encoding="utf-8")
```

All row renderers must escape text with `html.escape()`.

- [ ] **Step 5: Run page tests**

Run:

```bash
cd <repo>/nix-darwin
pytest src/test/unit/python/test_report_pages.unit.test.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd <repo>
git add nix-darwin/tools/test/report/pages.py nix-darwin/tools/test/report/static/report.css nix-darwin/tools/test/report/templates nix-darwin/src/test/unit/python/test_report_pages.unit.test.py
git commit -m "test-report: render tree-based homepage"
```

---

### Task 6: Render Coverage Drilldown Pages

**Files:**
- Modify: `<repo>/tools/test/report/pages.py`
- Create: `<repo>/tools/test/report/templates/coverage-file.html`
- Modify: `<repo>/src/test/unit/python/test_report_pages.unit.test.py`

- [ ] **Step 1: Add failing coverage drilldown tests**

Append:

```python
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
    html = generated[0].read_text(encoding="utf-8")
    assert "modules/kitty.nix" in html
    assert "source-target" in html
    assert "covered" in html
    assert "code-block" in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd <repo>/nix-darwin
pytest src/test/unit/python/test_report_pages.unit.test.py::test_coverage_drilldown_renders_nix_source_targets -q
```

Expected: FAIL because `render_coverage_drilldown_pages()` does not exist.

- [ ] **Step 3: Implement coverage drilldowns**

Add a deterministic slug helper:

```python
def slug_for_path(path: Path) -> str:
    return "__".join(path.parts).replace(".", "_")
```

Render files under `coverage/<slug>.html`. The page must show breadcrumbs, metric chips, detail rows, and a link back to `../index.html`.

- [ ] **Step 4: Run page tests**

Run:

```bash
cd <repo>/nix-darwin
pytest src/test/unit/python/test_report_pages.unit.test.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd <repo>
git add nix-darwin/tools/test/report/pages.py nix-darwin/tools/test/report/templates/coverage-file.html nix-darwin/src/test/unit/python/test_report_pages.unit.test.py
git commit -m "test-report: render coverage drilldown pages"
```

---

### Task 7: Render Test Drilldown Pages

**Files:**
- Modify: `<repo>/tools/test/report/pages.py`
- Create: `<repo>/tools/test/report/templates/test-file.html`
- Modify: `<repo>/src/test/unit/python/test_report_pages.unit.test.py`

- [ ] **Step 1: Add failing test drilldown tests**

Append:

```python
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
    )

    pages.render_test_drilldown_pages(tmp_path, [test])

    generated = list((tmp_path / "tests").glob("*.html"))
    assert len(generated) == 1
    html = generated[0].read_text(encoding="utf-8")
    assert "test_failure_output" in html
    assert "&lt;stdout&gt;" in html
    assert "&lt;stderr&gt;" in html
    assert "&lt;stack trace&gt;" in html
    assert "code-block" in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd <repo>/nix-darwin
pytest src/test/unit/python/test_report_pages.unit.test.py::test_test_drilldown_escapes_console_output_and_failures -q
```

Expected: FAIL because `render_test_drilldown_pages()` does not exist.

- [ ] **Step 3: Implement test drilldowns**

Render files under `tests/<slug>.html`, grouped by `TestCaseResult.test_path`. The page must show per-file totals and a section for each test case. Passing tests without output should show `No captured output.` instead of an empty block.

- [ ] **Step 4: Run page tests**

Run:

```bash
cd <repo>/nix-darwin
pytest src/test/unit/python/test_report_pages.unit.test.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd <repo>
git add nix-darwin/tools/test/report/pages.py nix-darwin/tools/test/report/templates/test-file.html nix-darwin/src/test/unit/python/test_report_pages.unit.test.py
git commit -m "test-report: render test result drilldown pages"
```

---

### Task 8: Wire the CLI to the New Pipeline

**Files:**
- Modify: `<repo>/tools/test/report/render.py`
- Modify: `<repo>/src/test/unit/python/test_report_renderer.unit.test.py`

- [ ] **Step 1: Add failing CLI integration assertions**

Extend `test_report_renderer_main_writes_json_html_and_summary` to create:

- a Nix `source-target-coverage.tsv`
- a Bats `report.xml`
- a Python `junit.xml`

Then assert:

```python
assert (report_dir / "index.html").is_file()
assert any((report_dir / "coverage").glob("*.html"))
assert any((report_dir / "tests").glob("*.html"))
html_report = (report_dir / "index.html").read_text(encoding="utf-8")
assert "Coverage: source tree" in html_report
assert "Test results: test file tree" in html_report
assert "source-target" in html_report
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd <repo>/nix-darwin
pytest src/test/unit/python/test_report_renderer.unit.test.py::test_report_renderer_main_writes_json_html_and_summary -q
```

Expected: FAIL because the CLI still renders only the flat report.

- [ ] **Step 3: Implement CLI orchestration**

Update `render.py`:

```python
def build_report_model(report_dir: Path) -> dict[str, Any]:
    tests = parsers.discover_results(report_dir)
    coverage = parsers.discover_coverage(report_dir)
    coverage_tree = trees.build_coverage_tree(coverage)
    test_tree = trees.build_test_tree(tests)
    return {
        "tests": tests,
        "coverage": coverage,
        "coverage_tree": coverage_tree,
        "test_tree": test_tree,
        "legacy_summary": summarize([test_to_legacy_dict(test) for test in tests]),
    }
```

Keep `test-results.json` stable enough for existing users by serializing a legacy-compatible summary plus tree metadata. Then call:

```python
pages.render_report_homepage(report_dir, coverage_tree=model["coverage_tree"], test_tree=model["test_tree"])
pages.render_coverage_drilldown_pages(report_dir, model["coverage"])
pages.render_test_drilldown_pages(report_dir, model["tests"])
```

- [ ] **Step 4: Run renderer tests**

Run:

```bash
cd <repo>/nix-darwin
pytest src/test/unit/python/test_report_renderer.unit.test.py src/test/unit/python/test_report_pages.unit.test.py src/test/unit/python/test_report_parsers.unit.test.py src/test/unit/python/test_report_trees.unit.test.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd <repo>
git add nix-darwin/tools/test/report nix-darwin/src/test/unit/python/test_report_renderer.unit.test.py
git commit -m "test-report: wire tree renderer into report CLI"
```

---

### Task 9: Add Full-Suite Fixture Integration Test

**Files:**
- Create: `<repo>/src/test/unit/python/test_report_integration.unit.test.py`

- [ ] **Step 1: Write failing fixture integration test**

Create a fixture report directory in the test using tmp files. Include Python JUnit, Bats JUnit, Ruby JSON, Nix output, Zunit TAP, Nix source-target coverage, and statement-target coverage. Assert that `render.main()` writes homepage and drilldowns.

Use this core assertion block:

```python
assert (report_dir / "index.html").is_file()
assert (report_dir / "test-results.json").is_file()
assert len(list((report_dir / "coverage").glob("*.html"))) >= 2
assert len(list((report_dir / "tests").glob("*.html"))) >= 4
homepage = (report_dir / "index.html").read_text(encoding="utf-8")
assert "modules/kitty.nix" in homepage
assert "src/test/unit/bash/shell-config.unit.test.bats" in homepage
assert "source-target" in homepage
```

- [ ] **Step 2: Run test to verify it fails if prior tasks are incomplete**

Run:

```bash
cd <repo>/nix-darwin
pytest src/test/unit/python/test_report_integration.unit.test.py -q
```

Expected after Tasks 1-8: PASS. Expected before Task 8: FAIL because full rendering is not wired.

- [ ] **Step 3: Run full Python unit suite with coverage**

Run:

```bash
cd <repo>/nix-darwin
pytest src/test/unit/python --cov=bin --cov=libexec --cov=tools --cov-branch --cov-report=term-missing -q
```

Expected: PASS with new report code at or above 85% statement and branch coverage.

- [ ] **Step 4: Commit**

```bash
cd <repo>
git add nix-darwin/src/test/unit/python/test_report_integration.unit.test.py
git commit -m "test-report: add full fixture integration coverage"
```

---

### Task 10: Verify Full nix-darwin Report

**Files:**
- Modify only if verification reveals defects in files from Tasks 1-9.

- [ ] **Step 1: Run full report suite**

Run:

```bash
cd <repo>
nix-darwin/tools/test/run all --report
```

Expected:

- Command exits 0.
- Console summary still includes Python/Ruby branch coverage and Bash/Awk/AppleScript/Zsh/Nix coverage metrics.
- `nix-darwin/.test-reports/all/latest/index.html` exists.
- `nix-darwin/.test-reports/all/latest/coverage/*.html` exists.
- `nix-darwin/.test-reports/all/latest/tests/*.html` exists.

- [ ] **Step 2: Spot-check generated files**

Run:

```bash
cd <repo>
rg -n "Coverage: source tree|Test results: test file tree|source-target|modules/kitty.nix" nix-darwin/.test-reports/all/latest/index.html nix-darwin/.test-reports/all/latest/coverage nix-darwin/.test-reports/all/latest/tests
```

Expected: matches in homepage and drilldown pages.

- [ ] **Step 3: Check generated HTML for text-spillage rules**

Run:

```bash
cd <repo>
rg -n "minmax\\(0, 1fr\\)|text-overflow: ellipsis|overflow-x: auto|white-space: pre" nix-darwin/.test-reports/all/latest/static/report.css
```

Expected: all four rules are present.

- [ ] **Step 4: Run full test command used before commits**

Run:

```bash
cd <repo>
nix-test --full
```

Expected: PASS. If VPN-dependent network checks fail, rerun after taking VPN down or run the same scoped command that previously passed in this environment.

- [ ] **Step 5: Commit final verification fixes**

If no fixes were required, skip this commit. If fixes were required:

```bash
cd <repo>
git add nix-darwin/tools/test/report nix-darwin/src/test/unit/python
git commit -m "test-report: polish tree report generation"
```

---

## Self-Review Checklist

- The plan implements the approved homepage shape: coverage source tree over test result file tree.
- The plan includes Nix source-target coverage in the HTML model and rendered output.
- The plan uses separate drilldown pages, not a persistent detail pane.
- The plan keeps HTML/CSS out of Python source by using external templates and a stylesheet.
- The plan includes explicit overflow rules for fixed metric/status columns, ellipsized paths, and horizontally scrollable code/output blocks.
- The plan preserves the current `render.py` CLI entrypoint.
- The plan adds tests before implementation for each major slice.
- New Python report code must maintain at least 85% statement and branch coverage.
