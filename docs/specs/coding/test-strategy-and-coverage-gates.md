# Test Strategy And Coverage Gates

Status: draft

## Purpose

This flake uses a multi-language test suite. The suite should be safe to run on
the host during normal development while still leaving room for future
high-realism tests in disposable macOS environments.

The default test commands must answer two questions:

1. Does the host-safe suite pass?
2. Are the source files we claim to test covered well enough to trust
   refactoring and updates?

## Test Levels

### Unit

Unit tests isolate one implementation unit at a time. They use the most mocking
of any host-safe test level.

Unit tests should:

- Invoke implementation code programmatically where the language makes that
  practical.
- Mock calls into this project's other implementation files.
- Mock host-mutating operations such as installs, file moves, activation, and
  destructive cleanup.
- Mock calls to external services and other machines.
- Assert behavior, output, errors, and calls to fakes rather than mutating the
  real host.

`nix-unit-test` runs this level.

### Integration

Integration tests exercise the interfaces between this project's components and
the host-facing command surface while remaining safe to run on the real host.

Integration tests should:

- Invoke user-facing commands, wrappers, and tools where possible.
- Use realistic filesystem fixtures and shell environments.
- Mock only operations that would mutate the host or call external systems.
- Exercise zsh entrypoints thoroughly when bootstrap behavior matters.
- Use bash integration checks mainly to verify invocation, argument passing, and
  compatibility when zsh already carries the deeper shell coverage.
- Exercise publication workflows only against temporary repositories and local
  bare remotes. Promotion tests may install and commit a fixture `flake.lock`,
  but must never push to a configured real remote or invoke host activation.
- Promotion release-channel integration uses installed Nix with disposable
  work repositories and local bare remotes. It must prove the declared head ref
  beats a different main, inspect Nix-written lock refs, run production prepare,
  block contract/flake ref mismatch, and prove checkout, remote, and temporary
  tree non-mutation.
- Canonical-promotion integration runs a complete source-dependent local
  prepare/apply/commit/fast-forward-push transaction against a disposable bare
  remote. It proves symmetric host maps, ignored-byte exclusion, location
  independence, meaningful changed impact, literal dotted hosts, version-1
  main representations, exhaustive cleanup, and no activation or release-ref
  mutation.
- Promotion impact tests cover exact-equality and changed derivation maps,
  canonical changed-host sorting, host-set and malformed-evidence rejection,
  apply-time baseline drift, post-install candidate drift with exact preimage
  restoration, already-selected equality, result binding, and structural
  no-activation.

`nix-int-test` runs this level.

`nix-test` runs the combined host-safe suite: unit plus integration.

### Smoke

Smoke tests are planned but not part of the default suite. They may mutate a
disposable macOS environment prepared with most expected dependencies already
installed. Their purpose is to validate that the bootstrap and update flows work
in a live environment at a sanity-check level.

Smoke tests should be standalone and should not be hidden behind `nix-test
--full`.

### System

System tests are planned but not part of the default suite. They may mutate a
fresh disposable macOS environment with only the expected manual prerequisites.
Their purpose is to validate the complete fresh-machine setup flow.

System tests are expected to be slow and rare.

## Coverage Metrics

Coverage gates apply per language and per metric. Do not combine unrelated
languages into one hard pass/fail number.

Tracked metrics:

- Python: statement and branch coverage.
- Ruby: statement and branch coverage.
- Bash: statement coverage for unit and combined host-safe runs; declared
  source-target coverage for integration entrypoint ownership.
- Awk: declared statement-target coverage.
- AppleScript: declared statement-target coverage.
- Zsh: declared statement-target coverage.
- Nix: declared source-target coverage.

The minimum for every tracked aggregate metric is 85 percent.

## Gate Semantics

Coverage gate results have three severities:

- `pass`: the metric meets the threshold.
- `warn`: a subset misses the threshold, but the command's hard aggregate gate
  passes.
- `fail`: a hard aggregate gate misses the threshold.

Hard gates:

- `nix-unit-test` fails when any unit aggregate language-specific tracked metric
  is below 85 percent.
- `nix-int-test` fails when any integration aggregate language-specific tracked
  metric is below 85 percent. Bash integration uses declared source-target
  coverage as its gate because those tests intentionally cross shell,
  interpreter, and host-interface boundaries where kcov statement coverage is
  not a useful quality signal.
- `nix-test` fails when any combined host-safe aggregate language-specific
  tracked metric is below 85 percent.

Warnings:

- `nix-test` warns when unit-only, integration-only, folder, or file subsets
  miss the threshold while the combined host-safe aggregate still passes.
- `nix-unit-test` and `nix-int-test` warn for folder and file subsets below the
  threshold after their aggregate gates pass.

The HTML report must show both failures and warnings. The terminal summary may
stay compact, but it must make gate failures and override usage obvious.

## Emergency Override

The suite may provide an emergency coverage-gate override for exceptional
maintenance cases, such as a needed `nix-update` while gate implementation is
being repaired.

The override must not be silent. When the override is set, the runner must:

1. Print a loud warning that coverage gates are being skipped.
2. Prompt the user for confirmation.
3. Default to exiting with a non-zero status.
4. Continue only when the user explicitly enters `yes`, case-insensitively.

The override exists to prevent accidental lockout, not to normalize skipping
tests.

`nix-update build` and `nix-update prepare` must run the host-safe
`nix-test --report` suite and then an exact-scratch-projection flake check before
a target is accepted. Lock mutation is a separate isolated `nix-update lock` operation;
switching consumes an immutable reviewed plan. Deployment qualification has no
production environment-variable test-command or test-skip override. The test
runner's coverage override remains limited to its documented runner boundary
and cannot confer deployment authority.

Deployment fault tests use isolated Git repositories, local bare remotes,
profile symlinks, control trees, participant executables, and command stubs.
They must prove virtual postimage builds without prepare-time control mutation,
fresh target-aware participant attestations, exact generation-link mappings,
dirty/sequencer Git rejection, transactional lock restoration, digest-keyed
replay consumption, fail-closed owner-only paths, and bounded command evidence.
No deployment test may select the real system profile or publish the real
machine-local evaluation pointer.

## Source Eligibility

Coverage should gate eligible source files, not merely files that happened to
appear in a coverage artifact.

The runner should build or consume a source inventory by language, then exclude
only files that are explicitly outside the coverage contract. Typical exclusions
include:

- Tests and test support.
- Generated files and report artifacts.
- Static assets and fixtures.
- Vendored code.
- Trivial compatibility wrappers when their behavior is covered through the
  implementation they invoke.

Files that are eligible but absent from coverage data should begin as warnings
while the inventory matures. Once the inventory is trusted, missing applicable
coverage data should become a report-generation error or gate failure.

When inference is not enough, use an explicit coverage ownership manifest rather
than relying on fragile naming conventions.

The host-safe ownership manifest lives at `src/test/coverage-manifest.tsv`.
Rows declare:

- `scope`: `unit` or `int`.
- `language`: the implementation language whose coverage artifact should
  contain the source file.
- `source_path`: repo-relative path to the source file.

The `all` scope is derived from unit and integration reports and should not be
declared directly unless a future test lane produces a source file that only has
combined-suite coverage.

Manifest entries are advisory during the initial rollout. A declared source file
that does not appear in the matching coverage artifact produces a coverage-gate
warning. This catches files falling out of coverage without immediately turning
the still-maturing manifest into a hard lockout.

## Reporting

`--report` must run the full scoped suite and render fresh artifacts.

Reports should include:

- Aggregate gate status for the command that generated the report.
- Language-specific metric gates.
- Unit/integration subset warnings when relevant.
- Folder and file warnings where the underlying coverage data supports them.
- Links from gate rows into the corresponding coverage tree or source file
  drilldown.

Non-report test runs should continue to fail fast. Gate enforcement may still
run in non-report mode when the required coverage summaries are available, but
HTML-only detail must not be required for the user to understand why a command
failed.
