# nix-darwin Test Layout

Tests live under `src/test/{unit,int}/{language}`. The directory names describe
the language under test, not the framework used to execute the tests.

Initial runners:
- `bin/nix-test` runs the default suite.
- `bin/nix-unit-test` runs unit lanes.
- `bin/nix-int-test` runs integration lanes.

Test-level policy and coverage-gate semantics are defined in
`docs/specs/coding/test-strategy-and-coverage-gates.md`.

`coverage-manifest.tsv` is the explicit ownership manifest for source files that
the current unit or integration suites claim should appear in coverage data.
Each row declares `scope`, `language`, and `source_path`. During report
generation, declared files missing from the matching scoped coverage artifacts
produce advisory coverage-gate warnings.

At minimum, every executable source file under `bin/` and `tools/` must have
both `unit` and `int` rows. Module-owned executable guest/admin scripts move
with their owning module repository and its test policy rather than remaining
as core coverage targets.

By default, runners fail fast. Passing `--report` switches to full-suite
collection for the selected scope: every lane that can run is executed, the
final exit status is non-zero if any lane failed, and detailed output is written
under `.test-reports/<scope>/latest`.

Report mode also records load-bearing runner and qualification steps such as
patch hygiene, flake checks, shell syntax, and an optional Darwin system build.
The overall report status comes from the finalized suite execution artifact,
not from parsed test-case totals. This means a failed qualification remains
visible even when every framework test case passed. Plain mode remains
fail-fast.

Planned coverage gates are language-specific. `nix-unit-test` and
`nix-int-test` fail on their scoped aggregate metrics, while `nix-test` fails on
combined host-safe aggregate metrics and warns for weaker unit, integration,
folder, or file subsets. Any emergency gate override must print a loud warning
and continue only after the user explicitly enters `yes`.

Report output currently includes:
- `index.html`: overall execution status, runner-step diagnostics, unified
  Python/Ruby/Bats/Zsh/Nix test results, and links to coverage reports.
- `test-results.json`: normalized execution, runner-step, failure-order,
  Python/Ruby/Bats/Zsh/Nix, coverage-gate, and override state.
- `runner/steps.tsv`: ordered runner steps with status, exit code, and a stable
  report-relative output path.
- `runner/failures.tsv`: the ordered cross-run failure sequence.
- `runner/execution.tsv`: the authoritative finalized overall status and exit
  code. A legacy report without this artifact is rendered as `UNKNOWN`.
- `runner/<step>/output.txt` and runner-step HTML pages: exact combined command
  output and an HTML-escaped homepage-linked view.
- `summary.tsv`: console-summary source data including Python/Ruby statement and
  branch coverage where available, Bash statement coverage via kcov for unit and
  combined host-safe runs, Bash integration source-target coverage for
  entrypoint ownership, awk/AppleScript source-statement coverage from
  Bats-targeted files, declared Zsh bootstrap source-statement coverage from
  zunit-targeted files, declared Nix source-target coverage, manifest coverage
  warnings, plus Python/Ruby/Bats/Zsh/Nix test totals.
- `<scope>/bash/kcov/`: Bash kcov HTML and Cobertura-style coverage output.
- `<scope>/{awk,applescript}/statement-coverage.*`: statement coverage details
  for source files referenced by Bats tests.
- `<scope>/zsh/statement-coverage.*`: statement coverage details for bootstrap
  zsh source files referenced by zunit tests.
- `<scope>/nix/source-target-coverage.tsv`: Nix source files marked `covered`
  or `missed` by declared source-target coverage.

Language runner defaults:
- `bash`: Bats test files (`*.bats`).
- `awk`: Bats test files (`*.bats`).
- `python`: pytest files discovered by `pytest.ini` (`*.unit.test.py`, `*.int.test.py`).
- `zsh`: zunit test files (`*.zunit`) for bootstrap-sensitive checks.
- `ruby`: Ruby scripts using Minitest.
- `applescript`: Bats tests for AppleScript source plus optional compile-only
  `*.applescript` fixtures checked with `osacompile`.
- `nix`: nix-unit suites (`*.nix`) using `lib.debug.runTests`-compatible `expr`/`expected` test attributes.

Bash statement coverage is collected by Homebrew `kcov` during `--report` runs.
The runner excludes tests and test infrastructure, then uses an exact include
list for discovered Bash source files under `bin`, `scripts`, `shell`,
`libexec`, and bootstrap tools. kcov's Cobertura output contains a shell
`branch-rate`, but local probing showed it can report 100% branch coverage even
when an unvisited `else` body remains uncovered, so the runner intentionally
does not publish Bash branch coverage.

Bash integration tests also declare source-target ownership in test-file
headers. This is the integration gate because those tests mainly exercise
user-facing entrypoints and host-safe mocks that cross into Python, Ruby, Nix,
Zsh, and external command boundaries. The integration kcov report is still
emitted as debugging context, but it is not a coverage gate.

Awk and AppleScript do not have native statement/branch tracers in this suite.
Their Bats lanes report source-statement coverage by counting nonblank,
noncomment statement lines in discovered `libexec` source files. A file counts
as covered when the scoped Bats tests reference it through `$repo_dir/...`; this
keeps the metric concrete without pretending to have runtime branch tracing.

Zsh tests use zunit. The current zsh statement coverage lane is intentionally
scoped to bootstrap zsh source files under `tools/bootstrap`, because those are
the scripts that must run before the Nix-managed shell environment exists. A
file counts as covered when scoped zunit tests declare it with
`# nix-darwin-test-source-target: ...`. Zsh branch coverage is not reported
until we have a trustworthy branch tracer.

Nix does not currently have statement or branch coverage in this suite. Nix
tests declare source-target coverage with comments at the top of test files:

```nix
# nix-darwin-test-source-target: flake.nix
# nix-darwin-test-source-target: modules/kitty.nix
```

The report runner compares these declarations against non-test `*.nix` source
files and reports scoped `source-target coverage` metrics.

Tests live in this `src/test` tree; `scripts/test-all.sh` remains as a
compatibility wrapper for the default runner.
