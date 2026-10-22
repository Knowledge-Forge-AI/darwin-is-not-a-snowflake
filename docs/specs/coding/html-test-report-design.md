# HTML Test Report Design

Date: 2026-06-24
Project: nix-darwin
Status: approved design

## Purpose

The nix-darwin test runner should generate a unified HTML report that feels familiar to a Java/Spring developer used to Gradle/JUnit/Jacoco reports, while staying readable on a laptop screen and reflecting this repository's multi-language test stack.

The report is generated only when the user passes `--report`. Report generation must run the full scoped suite rather than rendering partial stale data. Non-report test runs should continue to fail fast.

## Information Architecture

The report homepage has two stacked trees rooted at `nix-darwin`:

1. Coverage tree: organized by source file path.
2. Test result tree: organized by test file path.

Files that are not under test are hidden from both trees. The homepage summarizes unit, integration, and all-scope totals without forcing the user through flat framework-specific tables.

The report borrows Gradle/Jacoco's information structure, not Gradle's default visual style. The design should be quiet, dense, and readable, with clear status colors and simple tables.

## Coverage Tree

The coverage tree uses source paths as the primary navigation structure. Rows represent directories or files under `nix-darwin`. Directory rows aggregate metrics from tested descendants. File rows link to drilldown pages where the available tooling supports details.

Tracked metrics:

- Python: statement and branch coverage from coverage.py.
- Ruby: statement and branch coverage from SimpleCov.
- Bash: statement coverage from kcov.
- Awk, AppleScript, and Zsh: declared statement-target coverage.
- Nix: declared source-target coverage.

Nix does not have line or branch coverage tooling in this project. The report must include Nix coverage using the existing source-target TSV artifacts rather than omitting Nix from the HTML report.

Metric cells should show `n/a` when a metric does not apply to a language or file. Missing applicable coverage data is an error condition for report generation, not an `n/a`.

## Coverage Gates

The report must render coverage-gate results in addition to raw coverage totals. Gates are language-specific and metric-specific; the report must not hide a weak language inside a multi-language aggregate.

Gate severities:

- `pass`: the metric meets the configured threshold.
- `warn`: a subset misses the configured threshold while the command-level hard aggregate passes.
- `fail`: a hard aggregate gate misses the configured threshold.

Initial hard threshold: 85 percent for every tracked aggregate metric.

Command semantics:

- `nix-unit-test`: fail when any unit aggregate language-specific tracked metric is below threshold.
- `nix-int-test`: fail when any integration aggregate language-specific tracked metric is below threshold.
- `nix-test`: fail when any combined host-safe aggregate language-specific tracked metric is below threshold; warn for weaker unit-only, integration-only, folder, or file subsets.

Emergency override usage must be visible in both terminal and report output when report artifacts are generated. The runner must print a loud warning and continue only after the user explicitly enters `yes`, case-insensitively; the default response exits with a non-zero status.

## Source Eligibility

The coverage tree should eventually be backed by a language-aware source inventory rather than only by files that appear in coverage artifacts. Eligible source files are production files inside the project coverage contract after explicit exclusions for tests, generated files, static assets, fixtures, vendored code, and deliberately trivial wrappers.

Eligible source files missing from coverage data should begin as warnings while the inventory matures. Once the inventory is trusted, missing applicable coverage data should become a report-generation error or gate failure.

## Test Result Tree

The test result tree uses test paths as the primary navigation structure. Rows represent directories, test files, suites, and individual test cases where the framework output supports that level of detail.

Each row reports total, passed, failed, and skipped counts. Test file rows link to a test-file drilldown page. Individual test-case rows link to focused case details when stdout, stderr, failure text, or stack traces are available.

Supported inputs:

- Pytest JUnit XML.
- Bats JUnit XML for Bash, Awk, and AppleScript tests.
- Ruby Minitest JSON result output.
- Nix unit/int output parsed from the existing status lines.
- Zunit TAP output for Zsh tests.

## Drilldown Pages

The report uses separate drilldown pages instead of a persistent detail pane. This keeps the homepage usable on laptop screens and gives detailed content enough room.

Coverage drilldown pages include:

- Breadcrumbs from `nix-darwin` to the selected source file.
- Metric chips for the file's applicable metrics.
- Line-level coverage when available from the underlying tool.
- Source-target detail for Nix and other declared-target languages where line coverage is not available.
- Links to upstream framework HTML reports when they provide better native detail.

Test drilldown pages include:

- Breadcrumbs from `nix-darwin` to the selected test file or test case.
- Scope, language, status, duration, and suite/module metadata.
- stdout and stderr sections.
- Failure output and stack traces for failed tests.
- A clear empty state for passing tests with no captured console output.

## Layout Rules

The report must prevent text spillage and table distortion:

- Metric and status columns use fixed widths.
- Path and code columns use `minmax(0, 1fr)` or equivalent constraints.
- Long paths are ellipsized in tree rows and expose the full path through title/tooltip behavior.
- Long source, stdout, stderr, and stack-trace lines scroll horizontally inside their own content cells or blocks.
- Status labels are clipped or ellipsized within their own cells; they must never overlap neighboring text.
- Tables must remain readable at laptop widths without requiring page-level horizontal scrolling except for intentionally scrollable code/output blocks.

## Renderer Architecture

The current flat report renderer should evolve into a small pipeline:

1. Discover raw artifacts under `.test-reports/<scope>/latest`.
2. Normalize framework-specific test and coverage data into internal records.
3. Build a source-path coverage tree.
4. Evaluate coverage gates and source-eligibility warnings.
5. Build a test-path result tree.
6. Render the homepage.
7. Render coverage drilldown pages.
8. Render test result drilldown pages.
9. Copy or link native framework detail reports where useful.

The renderer should keep parsing, tree construction, metric aggregation, and HTML rendering in separate functions or modules. This keeps future additions like Java, TypeScript, or Go reports from turning the renderer into one large conditional pile.

## Error Handling

Report generation should fail with actionable messages when expected artifacts are missing, malformed, or internally inconsistent.

Examples:

- `--report` was requested but a scoped suite did not run.
- A language has tests but no expected coverage artifact.
- A coverage artifact references a source file outside `nix-darwin`.
- A test result references a path that cannot be mapped into the test tree.

Non-applicable metrics should render as `n/a`; missing applicable metrics should fail the report.

## Testing

The report generator should have focused unit tests for:

- Parsing each supported artifact format.
- Aggregating coverage metrics by source tree.
- Aggregating test results by test tree.
- Rendering HTML with escaped text.
- Preventing long paths, status labels, source lines, and console output from spilling outside their containers.
- Including Nix source-target coverage in the HTML model and rendered output.
- Rendering coverage-gate failures, warnings, and emergency override state.
- Rendering source-eligibility warnings for files that should be covered but are missing from coverage artifacts.

Integration tests should run the report generator against a representative fixture report directory and assert that the homepage and drilldown pages are produced with stable links.

Any new Python code for the report renderer must maintain at least 85% statement and branch coverage.
