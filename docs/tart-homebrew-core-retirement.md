# Core Tart Homebrew ownership retirement

## Status and scope

This is the five-path source-only integration onto Core
`710db9af4a2b0adc1042cf9e7055c543609e0402`. The reviewed source was accepted by the parent after fresh native verification,
following one dispatcher-owned independent review and bounded amendments.
Source selection, activation and Tart runtime acceptance remain **OPEN**. No
production Tart ownership cutover is claimed.

The resulting change owns exactly these paths:

- `flake.nix`: remove only `localOverlay.tart`.
- `packages/tart-projected/default.nix`: delete the custom application
  projection launcher and disposable bundle version-check implementation.
- `src/test/unit/nix/flake.unit.test.nix`: replace the projection test with
  no-override and installation-ownership checks; remove the deleted source's
  header association.
- `src/test/coverage-manifest.tsv`: remove exactly the two obsolete unit and
  integration associations for that package.
- `docs/tart-homebrew-core-retirement.md`: record the integrated source,
  verification limits and parent handoff.

The frozen original four-path retirement and the accepted two-file advisory
follow-up were authenticated against their supplied SHA-256 bindings. Only
their relevant changes were merged. Other files from the advisory checkout
were not adopted.

Core's signed Brave overlay, RTK overlay, Kitty/style settings, inactive R3
selection, source constructor and existing assertions are preserved. Other
owners' files, operator state, retained evidence and existing untracked files
are outside this change. The synthetic `mkPackage "tart"` unfree-policy fixture
remains; independent policy and VM ownership tests are unchanged. Existing
store artifacts, application copies and rollback generations are retained.

## Resulting ownership checks

Each configured Core overlay is invoked with the same fixed predecessor and a
package-call recorder. The checks reject any declared `tart` attribute,
including an identity override, and separately check upstream pass-through.
Brave and RTK package paths are compared with the resolved flake's `outPath`,
so copied source and checkout roots can differ. This records overlay
declarations; it does not prove the final package-set fixed point.

Every Core host must have no Tart system-profile package, Homebrew formula or
cask (including tap-qualified names), application claim, or Core package
output. Claim checks use the shared application-claims normalization contract:
they reject case variants and tap-qualified package names whose basename is
`tart`, and independently reject the normalized artifact `tart.app`. Ordinary
upstream Nixpkgs `legacyPackages.tart` availability is distinct from installation
ownership; a cheap `pkgs ? tart` assertion preserves that availability without
forcing the derivation.

## Review disposition and evidence boundary

The final in-run disposition is **amend**. Both advisory findings are accepted:
the claim predicate uses the existing exported `normalize` helper, which reuses
the internal package and artifact normalizers, and the upstream availability
assertion is explicit. No application-claims library interface changed.

The informational finding that this document is untracked is retained for the
parent: include it explicitly in the five-path product manifest. The resulting
bytes are uncommitted working-tree state and must be bound separately from the
entry HEAD/tree identity. The single independent review covered the producer's
candidate; the revisor checked the amendments below. No second independent
review followed these amendments.

## Fresh focused verification

- The edited flake and unit file parse with installed Nix 2.31.5.
- Local fixture evaluation imports the real flake outputs and constructor
  overlay functions with a replacement Darwin constructor and package-call
  recorder. Same-root and distinct copied-source-root checks pass. Replacing
  the resolved-flake comparisons with checkout-root comparisons reproduces
  the distinct-root Brave/RTK failure while the same-root case still passes.
- Both injected identity and replacement Tart overlays fail the declaration
  ownership check; only replacement fails upstream pass-through. Brave and RTK
  recorder checks remain successful in both injections.
- Tart-free and unrelated application-claim fixtures pass. Twelve injected
  ownership cases are rejected: a system package, bare and tap-qualified
  formulas, bare and tap-qualified casks, six application claims (bare,
  tap-qualified, mixed-case bare and tap-qualified packages, plus normal and
  mixed-case Tart artifacts), and a package output. A missing upstream Tart
  attribute separately fails the availability assertion. The pre-amendment
  fixture reproduced acceptance of all five non-exact claim variants; the
  amended predicate rejects them. The retained synthetic unfree-policy
  assertion passes. These fixtures do not evaluate actual native host
  configurations or establish real package-set availability.
- The actual-source `flake.unit.test.nix` native run was repeated after the
  amendments with installed `nix-unit` and the checkout path as `repoRoot`.
  Evaluation was blocked by sandbox denial of the Nix daemon socket: the runner reported
  0/38 successful, with daemon/store access errors. This is an infrastructure
  limitation, not a fresh native pass or evidence of assertion failure. No
  host build, activation or input update was performed.
- All eight existing tests in `coverage_manifest.unit.test.py` and
  `test_report_gates.unit.test.py` pass. The existing manifest parser reads
  296 entries before and 294 after, with no duplicates. Every surviving byte,
  header and row order is identical.
- The unmodified runner's `discover_nix_source_targets` and
  `declared_nix_source_targets` functions and existing `evaluate_coverage_gates`
  evaluator were exercised directly. Every scope has zero invalid header
  declarations. The only removed source and header association are Tart's.
- Byte comparisons against the entry source confirm that the flake removes
  only the Tart overlay binding and all 36 non-Tart unit assertions remain
  identical, including the Brave/RTK, constructor and Kitty/style checks.
  The affected native file still declares 38 tests. This count is separate
  from the blocked native execution result.

The 85% source-target policy is unchanged. Before counts reconstruct the
deleted source and its exact old header association from the entry state.

| Inventory | Scope | Before | After | Aggregate gate before/after |
| --- | --- | --- | --- | --- |
| Current source-bound candidate | Unit | 44/53 (83.0189%) | 43/52 (82.6923%) | fail / fail |
| Current source-bound candidate | Integration | 34/53 (64.1509%) | 34/52 (65.3846%) | fail / fail |
| Current source-bound candidate | Combined | 47/53 (88.6792%) | 46/52 (88.4615%) | pass / pass |
| Current raw workspace | Unit | 44/57 (77.1930%) | 43/56 (76.7857%) | fail / fail |
| Current raw workspace | Integration | 34/57 (59.6491%) | 34/56 (60.7143%) | fail / fail |
| Current raw workspace | Combined | 47/57 (82.4561%) | 46/56 (82.1429%) | fail / fail |

The source-bound comparison intersects discovered targets with tracked source;
it is diagnostic arithmetic, not a runner exclusion. The unmodified raw runner
also discovers four retained untracked/ignored Nix evidence sources here. Those
sources remain intact. The prior follow-up's raw inventory contained 38 extra
sources and reported 43/90, 34/90 and 46/90 after retirement; that result belongs
to its historical workspace and is not relabeled as current verification.

Removing the covered Tart source reduces the unit/combined numerator and
denominator by one; integration never declared that source. Deleting its two
manifest rows creates no additional coverage delta. The unrelated unit
manifest advisory for `packages/dinas-commands/default.nix` and the weak
unit/integration subset warnings remain. A combined source-bound run treats
those subset deficits as warnings when its combined aggregate passes. The
raw workspace aggregate still fails. No inventory, exclusion, runner,
threshold, dependency or lockfile policy was changed. These calculations are
not full-suite execution.

### Parent actual-source native qualification

The parent verified the exact five-path candidate manifest
`d786a08c6baa9ce37968fd221e0ccbf08597c1414ecc943375f200175abb34b8`
and ran the real Nix unit lane from a clean Core710db9a archive plus those paths.
All **38/38** assertions passed with the checkout path as `repoRoot`; all eight
manifest and gate tests passed. The primary app assertions remain included.
The retained native log and source bindings are recorded in
`CORE-TART-INTEGRATE2-PARENT-NATIVE.json`. The worker sandbox denial above remains
history and is not an assertion failure. These source tests establish no host
activation or runtime acceptance. This document-only parent update does not
change the tested executable source.

The supported reproduction command follows; `TART_TEST_SCRATCH` denotes
parent-owned scratch.
The installed `nix-unit` does not accept Nix CLI lock flags, so this direct
evaluation lane uses the existing locked `builtins.getFlake` call and requires
separate lock-byte preservation verification. Any subsequent flake
qualification must disable both lock updates and lock-file writes.

```sh
env-run -- env NIX_CONFIG='experimental-features = nix-command flakes' \
  nix-unit --impure --gc-roots-dir "$TART_TEST_SCRATCH/gc-roots" \
  --arg repoRoot "$PWD" --argstr system aarch64-darwin \
  src/test/unit/nix/flake.unit.test.nix
```

Keep the checkout-path `repoRoot` boundary used by `tools/test/run`; a
flake-check-only result cannot qualify the reviewed copied-source path defect.

## Historical bounded parent evidence

The parent-owned `TART-CORE-NATIVE-SOURCE.json` binds the original Core
`43948500da942f14ac1b11ffc5ab070e8b4b7514` archive plus four retirement paths
from checkpoint `987c746408f65794cc71dadea6448f26cb358c1c`. It records a real
native Nix unit **38/38 PASS**, with log SHA-256
`a9af74ad1dcb445db86658efe93a8b6a595a24c3eb3523e054eb886af06dcf9a`.
This predates the primary app repair and is evidence only for that four-path
source. It is not a native rerun of this five-path integration or runtime
acceptance.

`TART-ACTUAL-OPERATOR-PROJECTION-EVALUATION.json` binds the actual generated
local configuration plus only the three intended Tart flags, the same original
Core archive with retirement, and the accepted receiving VM/outgoing Security
sources. It records one receiver, one Tart formula, one immutable Softnet
Defaults entry and one grant; no Nix Tart system package or Fusion; and helper
SHA-256 `abe4e16efe04317901816b6a6c389a46390bae5333c08f5199567f66dc320cc3`
with no setuid/setgid or group/other write bits. Its native evaluation-result
SHA-256 is `22085c414748f1789d6b0a1ccbbc3bf9068de29f26be88fddde9a0d70e8c5ee2`.
This supersedes the older unbound default-projection evidence within that
historical evaluation scope. It did not change host control, build, deploy, or
qualify privilege/runtime behavior. It does not bind the current five paths.

## Current deployment context and parent handoff

The parent reports primary deployment complete at generation 217 through the
normal report/build/adopt/deploy flow. The user confirms a fresh stock Kitty
session is clean. Brave was updated natively from 1.97.57 to 1.97.59, passes strict signing and
Gatekeeper checks, and the user confirms it reports up to date. The earlier AppManagement failure
is historical recovery context and is no longer the current blocker.

The official Homebrew Tart 2.40.1 is already installed and pinned, according to
the parent. Immutable Softnet 0.22.2 is retained. Root policy, execution,
Security Apple RPC/privilege qualification, Tart RPC, DHCP and isolation remain
external acceptance work. This source checkpoint makes no fresh payload,
signature, entitlement, command-resolution or runtime claim.

Source-only integration and qualification are authorized now. The parent owns
source acceptance, source selection, normal C1/S1/V1 qualification/adoption,
composed native build/readback and activation. The final production ownership
cutover requires qualified composed privilege/runtime behavior, including the
SETENV restriction, exact command boundary, one installation/privilege owner,
the immutable Softnet rule, and coordinated retirement of the outgoing guard.
A separately identified, reversible qualification activation may be needed to
make the receiving owner's Softnet policy testable. That technical ordering
does not require runtime acceptance before all source preparation, and it
does not declare any remaining gate satisfied or invent a new human approval
requirement. Source selection, activation and runtime acceptance remain OPEN.

The parent must bind the resulting five-path manifest explicitly, preserve
the deployed app repair and all unrelated owner work, and complete actual host
readback against the intended composed source. The historical four-path
binding and 38/38 result cannot stand in for that qualification. This revised
candidate stops at the dispatcher-owned checkpoint; it starts no successor.

## Rollback and deferrals

Retain the previous reviewed source selection, Nix closure and system
generation as coordinated rollback inputs. Keep production source selection
unchanged until its existing acceptance conditions are met. After any
qualification or production activation, rollback belongs to the parent and
must restore coordinated ownership/source selection and verify native
readback; restoring this overlay alone does not establish runtime recovery.

This integration performs no live policy write, build, activation, VM launch,
network qualification, source-catalog mutation, garbage collection,
application/store deletion, profile/PATH mutation, package installation,
commit or push. Composed privilege and runtime behavior and final production cutover remain
deferred to their existing owners; the parent actual-source unit gate above
has passed.
