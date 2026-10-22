# Local deployment nix-env invocation correction

Status: amended after independent work review; final revision qualification passed.
Publication remains dispatcher-owned; no second independent review follows revision.

## Scope

Local selected-profile realignment now invokes the recorded `nix-env` invocation
path. Resolving that symlink for invocation selected the underlying `nix` multicall
mode, which rejected `--profile`. The resolved executable identity remains bound
and revalidated before privilege and again after authentication. Revalidation
rejects changed paths, targets, bytes, and missing or non-executable endpoints;
the command uses the original reviewed path. Transactional apply/recovery,
`sudo`, and `darwin-rebuild` invocation behavior is unchanged.

Nonzero realignment now uses the existing bounded, redacted command failure
explanation, including exit status and a private evidence pointer. The journal
retains its existing 2,048-character exception-detail limit. The existing formatter
now reserves space for the complete safe evidence pointer before bounding detail,
including near-limit paths. No retry, restoration,
or switch follows a realignment failure. Successful realignment still requires
the selected generation and target to match the reviewed predecessor and both
the live observation and retained generations to remain unchanged.

Both local deployment and `build_only` suppress only the identical fallback
projected flake-check argv already executed in the same selected directory. A source-owned `nix-test
--report` hook still receives all projection options and retains the separate
exact projected check: arbitrary hooks do not prove equivalent suites, source
references, or lock semantics. Report, coverage, module preflight, and target
build gates remain intact.

## Independent review disposition

Disposition: amend. Findings 1 and 2 were reproduced and corrected: the remaining
`build_only` fallback duplicate is removed, and long evidence pointers survive
the journal limit. Finding 3 is declined as optional refactoring: command layout
is unchanged and exact argv regressions guard the current contract. Transactional
apply/recovery construction remains unchanged. Finding 4 is addressed by fresh
revision qualification, distinguished below from producer-reported history.
Finding 5 is accepted: unrelated `bin/claude` and Serena state are excluded from
the scoped product and preserved. Prior review-stage Serena cache deltas are
operational metadata, not source changes. No further independent review was
requested or performed.

The Python changes are narrow corrections in existing large workflow modules;
they add no independent responsibility or executable framework. Decomposition
and command-construction refactoring remain outside this hotfix.

## Verification

The Darwin subprocess fixture uses `bin/nix-env -> nix`, actual executable
identity binding and command evidence capture, and disposable profile links.
The endpoint rejects `--profile` when invoked as `nix`. It never runs real sudo
or a Darwin switch. Cases cover successful realignment, bounded/redacted command
failure, and endpoint retargeting, removal, or executable-permission loss before
privilege. Unit cases also reject live, retention, selected-target, and selected-
generation drift and assert that aligned profiles skip realignment.

Producer-reported historical failing-first evidence (not re-executed during
revision): after correcting an initial fixture allowlist shape,
both multicall cases failed against the old invocation (2 failed, 1 deselected,
exit 1). The dedupe regression separately produced 1 failed, 1 passed,
65 deselected (exit 1), proving two identical fallback calls. Subsequent focused
runs passed: 84 changed-boundary tests and a separate 450 identity, transactional,
and diagnostic tests (each exit 0). These are overlapping development evidence,
not additions to the complete checkpoint count.

Final revision qualification supersedes the producer's earlier 2,054-test
checkpoint. The new `build_only` call-count and long-path realignment regressions
first failed against the bound producer candidate: **2 failed, 5 passed,
408 deselected; exit 1**. After amendment, focused pytest over deployment state,
local deployment, command diagnostics, normal deployment integration, and
failure-display integration passed: **519 passed, no failures or skips; exit 0**.
These development runs overlap the complete checkpoint and are not additive.

The complete checkpoint ran under the curated environment with
`NIX_DARWIN_TEST_SOURCE=worktree`, using `bin/nix-test --report --jobs 4` and a
private report root: **2,055 passed, 0 failed, 0 skipped; exit 0**. This includes
the Darwin provider-free multicall fixture, both qualification paths, transactional
compatibility, executable identity, profile invariants, and bounded/redacted
failure display. The long-path fixture verifies that the retained evidence pointer
still names a readable file after journaling. Patch hygiene, shell syntax, and
all seven flake checks passed. Flake checking used
`--no-update-lock-file --no-write-lock-file`.

All 12 hard coverage gates passed without an override. Combined Python statement
and branch gates reported 91.60% and 85.30%; Bash statements 86.92%; Ruby statements
and branches 100.00% and 90.50%; Nix source targets 97.50%. Remaining hard metrics
reported 100%. Two non-fatal manifest warnings remain: unit `bin/nix-darwin-config`
and integration `bin/nix-update` coverage entries. No coverage policy changed.

`nix build --no-link --no-update-lock-file --no-write-lock-file
path:.#dinasCommands path:.#nixDarwinManagedPython` exited 0. The commands package
was rebuilt; managed Python was reused from cache. The flake check rebuilt its
Nix unit-test derivation; the other six check outputs were reused. No host-system
build or activation was performed. Generated reports, command evidence, and build
logs remain outside the source candidate. No second independent review follows
these revisions; staging, commit creation, and publication remain dispatcher-owned.

## Deployment and retention

Agent-Central DINAS live activation was already successful before this phase.
This is a permanent CLI/core correction, not another activation. No live profile,
host activation, composition promotion, controller relocation, or link retargeting
was performed. Composition locks are intentionally not promoted. Old failed
deployment journals and historical blocked promotion campaigns remain retained.

After source acceptance, DINAS may proceed to DINAS-SOURCES1. This correction can
ride the next authorized core promotion; it grants no standalone redeployment
authority. Rollback of this source change belongs to a separately authorized
source operation and does not imply replaying any historical deployment.
