# `developer-ux-nd` Migration Inventory

Filled from the live tracked core tree and read-only host/provider evidence on
2026-08-21. Structure and rules come from the
[owner migration template](owner-migration-template.md); normative decisions
come from
[ADR 0001](../../adr/2026/08/0001-nix-darwin-core-subflake-extension-contract-and-owner-driven-migration.md),
[ADR 0002](../../adr/2026/08/0002-domain-ownership-application-claims-and-migration-sequencing.md),
and the matched-cut reference in
[ADR 0003](../../adr/2026/08/0003-entertainment-nd-migration.md).
The migration-specific decision is
[ADR 0004](../../adr/2026/08/0004-developer-ux-nd-migration.md).

This record keeps three sets distinct:

1. existing core footprint that must migrate at parity;
2. the Google Antigravity command center, Google Antigravity IDE, and Claude
   desktop, which are new destination-only footprint added only after the
   matched ownership cut; and
3. adjacent/core primitives and unmanaged bundles deliberately retained or
   unclaimed.

No runtime declaration, lock, installed application, or active host state was
changed while producing this inventory and parity evidence. The original
DEVUX5 evidence phase changed only this migration record and added the three
State B parity artifacts under `evidence/developer-ux-nd/`. The remediated
State C rerun changes only this record and adds two separately named artifacts
without rewriting the rejected State B evidence.

## Header

```text
module:                 developer-ux-nd
publication class:      PUBLIC-CAPABLE
owning composition:     the public-capable composition
owner:                  operator
status:                 PARITY
governing ADR:          docs/adr/2026/08/0004-developer-ux-nd-migration.md
activation required:    YES
```

PUBLIC-CAPABLE is supported by the inventoried footprint: it uses public
`nixpkgs`, public Homebrew casks, and public Petdex source artifacts and reads no
non-public input, secret, or operator-specific control-plane key. Publication
capability is not publication readiness.

---

## 1. Pre-cut inventory

Paths and line references describe the live tracked source on 2026-08-21. The
future removal is verified against symbols and semantics as well as these line
locations, because unrelated changes may shift line numbers before the cut.

### 1.1 Flake inputs

`none` dedicated to this domain.

All ordinary applications come from core's binding `nixpkgs` or existing
Homebrew integration. The Petdex derivations use `fetchurl` inside their package
definitions; neither has a dedicated flake input or lock node.

### 1.2 Packages and overlay attributes

| Item | Current source | Declaring surface |
|---|---|---|
| `petdex` | `nix-darwin/packages/petdex-cli/default.nix` | package definition; installs the wrapped CLI and its JavaScript payload |
| `petdex-desktop` | `nix-darwin/packages/petdex-desktop/default.nix` | package definition; installs `Petdex.app`, desktop binary, and sidecar payload |
| `pkgs.petdex` | `nix-darwin/flake.nix:102` | overlay `callPackage ./packages/petdex-cli` |
| `pkgs.petdex-desktop` | `nix-darwin/flake.nix:103` | overlay `callPackage ./packages/petdex-desktop` |
| `packages.<system>.petdex` | `nix-darwin/flake.nix:373` | package output |
| `packages.<system>.petdex-desktop` | `nix-darwin/flake.nix:374` | package output |

No compatibility alias exists for either Petdex package. Applite and GIMP are
Homebrew casks; Audacity, Bruno, DBeaver, and Zenmap are direct `nixpkgs`
packages and have no core package definition, overlay attribute, or package
output to move.

### 1.3 Darwin modules

| Current source | Owned slice | Cut disposition |
|---|---|---|
| `nix-darwin/modules/brewfile-stragglers.nix:29` | cask `applite` | remove one line; retain the mixed module |
| `nix-darwin/modules/desktop-apps.nix:5-8` | `pkgs.audacity`, `pkgs.bruno`, `pkgs.zenmap` | remove three entries; retain `pkgs.qbittorrent` and the mixed module |
| `nix-darwin/modules/desktop-apps.nix:18` | cask `gimp` | remove one line; retain other casks, brew, and MAS declarations |
| `nix-darwin/modules/developer-tooling.nix:5` | `pkgs.dbeaver-bin` | remove one entry; retain Liquibase and core agent-useful primitives |
| `nix-darwin/modules/codex.nix:6-7` | `pkgs.petdex`, `pkgs.petdex-desktop` | remove two entries; retain Claude Code |
| `nix-darwin/modules/codex.nix:12` | cask `chatgpt` | remove one existing core cask; retain `antigravity-cli` and `codex` |
| `nix-darwin/modules/gimp.nix` | complete managed GIMP help activation | move the module behavior; remove its core import |
| `nix-darwin/modules/codex-state.nix:5-6,27-40,118-176` | Petdex derivation bindings, state roots, migration, compatibility link, and installed links | split out Petdex behavior; retain Codex docs and Serena behavior |
| `nix-darwin/flake.nix:304` | import of `modules/gimp.nix` | remove at cut |

The module files other than `modules/gimp.nix` are mixed and are split, not
moved. The Petdex range includes names embedded in shared `install -d` and
`chown` commands at lines 32-39; those commands must be separated so no Petdex
path remains in core.

### 1.4 Options

`none`. The footprint declares no option namespace and makes no assignment to a
domain option elsewhere.

### 1.5 Host facts

`none`. No core host-table key is read only by this domain. (The user identity
`userName` required by GIMP and Petdex activation slices is not a host-table
key; in core it was supplied via curried module imports `{ userName }`, and in
the destination's `darwinModules.default` it is resolved generically from
standard nix-darwin module options such as `config.system.primaryUser` and
`config.users.users.<name>.home` without domain-specific bootstrap or
control-plane coupling).

### 1.6 Application claims

Seven existing claims migrate; see section 4.1. Their source records are the
Applite, ChatGPT, GIMP, Audacity, Bruno, DBeaver, and Petdex rows in
`nix-darwin/lib/core-application-inventory.nix`. Zenmap currently contributes
no macOS application bundle and therefore has no claim. The three post-cut AI
GUI claims are recorded separately in section 4.2.

### 1.7 Configuration

| Item | Current source | Consumer |
|---|---|---|
| managed GIMP online-help setting | `nix-darwin/config/gimp/gimprc-help-online` | `modules/gimp.nix` |

This tracked template moves with the GIMP module. There is no Petdex tracked
configuration file and no machine-local developer-ux configuration input.

### 1.8 `bin/`, `libexec/`, `tools/`

`none`. Searches of all three executable/helper trees found no command or helper
owned by these products and no Petdex consumer. The Petdex JavaScript, desktop
binary, and sidecar are package payloads installed or linked by the activation
slice recorded in 1.12; they are not separate tracked helper-tree sources.

### 1.9 Tests, fixtures, and support

| Test surface | Current coverage and future split |
|---|---|
| `nix-darwin/src/test/int/nix/darwin-configuration.int.test.nix` | Source-target headers name `modules/gimp.nix` and both Petdex package definitions; package assertions cover `petdex`, `petdex-desktop`, Bruno, DBeaver, Audacity, and Zenmap; cask assertions cover `applite`, `gimp`, and retained casks; activation assertions cover every GIMP and Petdex state/link marker. Move domain assertions and retain the mixed core assertions. |
| `nix-darwin/src/test/int/nix/application-claims.int.test.nix` | Generic completeness evaluates `config.system.build.applications`; the current claim count includes the seven moving claims; the removed-declaration mutation probe uses `gimp`; the command-line exclusion contract asserts `zenmapIsCoreOwned` and `zenmapHasNoClaim`, `antigravity-cli` and `codex` are non-application casks, and `antigravityHasNoClaim` (line 192). At cut, reduce the then-live core count by seven, repoint the `gimp` mutation probe to a surviving cask, move exact destination claim checks including ChatGPT/current Codex desktop, remove `zenmapIsCoreOwned` and `zenmapHasNoClaim` from core while keeping equivalent Zenmap bundle completeness proved in the destination, and retain both CLI-only cask exclusions and `antigravityHasNoClaim` in core (mechanically enforcing that post-CUT GUI additions are not added to core). |
| `nix-darwin/src/test/unit/nix/flake.unit.test.nix` | Source-target headers name `modules/gimp.nix` and both Petdex packages; `testFlakeExposesPetdexPackages` covers both overlay/package-output pairs; the developer-tooling test includes `dbeaver-bin`. Split domain assertions and re-check every retained source-target header. |
| `nix-darwin/src/test/unit/bash/codex-state.unit.test.bats` | Lines 50-67 assert the Petdex leaf, legacy migration/link, copied CLI JavaScript, desktop binary link, and sidecar link inside a mixed Codex/Serena test. Move the Petdex assertions and retain the Codex/Serena assertions. |

No dedicated fixture or support harness was found outside these mixed tests. The
destination must add standalone module, package, claim, activation, and
package-set checks; removing core assertions without equivalent destination
coverage is not a split.

### 1.10 Coverage rows

All six current rows move with their sources at the core cut:

| Lane | Source | Current row |
|---|---|---|
| integration Nix | `modules/gimp.nix` | `src/test/coverage-manifest.tsv:16` |
| integration Nix | `packages/petdex-cli/default.nix` | `src/test/coverage-manifest.tsv:36` |
| integration Nix | `packages/petdex-desktop/default.nix` | `src/test/coverage-manifest.tsv:37` |
| unit Nix | `modules/gimp.nix` | `src/test/coverage-manifest.tsv:117` |
| unit Nix | `packages/petdex-cli/default.nix` | `src/test/coverage-manifest.tsv:137` |
| unit Nix | `packages/petdex-desktop/default.nix` | `src/test/coverage-manifest.tsv:138` |

Rows for mixed files such as `flake.nix`, `modules/desktop-apps.nix`,
`modules/developer-tooling.nix`, `modules/codex.nix`, and
`modules/codex-state.nix` remain because those sources remain in core. The
integration row for mixed `lib/core-application-inventory.nix` also remains;
the seven claim rows are removed from that source while retained core claims
continue to be tested there.

### 1.11 Docs, ADRs, and history

| Document/evidence | Disposition |
|---|---|
| ADR 0002 | stays in core as the normative framework ownership matrix and application-claim contract |
| ADR 0004 and this inventory | stay in core through `COMPLETE` as the decision and checklist against which core removal is verified |
| `entertainment-nd-inventory.md` | stays with the entertainment migration; its Audacity/Zenmap/GIMP references document retained neighbors and its matched-cut evidence is the choreography reference |
| entertainment parity JSON artifacts | stay as immutable entertainment evidence even though their system snapshots include then-current developer-ux packages and casks |
| `docs/deployments/agent-security2-fix1-r1-control-plane-hardening-exit.md` | stays as mixed historical core evidence; its incidental full-flake excerpt includes the two Petdex overlay lines but it is not Petdex guidance or a live ownership declaration |
| composition-maintainer skill | stays in core; its Applite statement is framework claim doctrine, not product guidance |

No existing Petdex, GIMP, or other developer-ux-specific guide was found that
must be relocated. Historical references are not runtime stubs and are not
deleted to manufacture an absence search.

### 1.12 Activation effects

| Effect | Current source | Required destination behavior |
|---|---|---|
| GIMP help configuration | `modules/gimp.nix:8-41` | idempotently maintain the marked block in `gimprc`, preserving other content and ownership |
| Petdex state setup | `modules/codex-state.nix:27-40` | define and create the app, bin, sidecar, and legacy paths with correct ownership |
| Petdex legacy migration | `modules/codex-state.nix:118-166` | move existing `~/.petdex` content safely, preserve conflicts with timestamped backups, and maintain the compatibility link |
| Petdex payload links | `modules/codex-state.nix:168-176` | copy the CLI JavaScript and link the desktop binary and sidecar into the persisted layout |

The destination keeps `~/.codex/apps/petdex` for parity and idempotently creates
both `~/.codex/apps` and its owned leaf. It must not rely on core activation
ordering. Core retains only its generic Codex docs and Serena activation
behavior after the split.

### 1.13 launchd agents and daemons

`none`. No label, arguments, schedule, working directory, environment, log
path, or log-rotation entry belongs to this footprint.

### 1.14 State roots

| Root/path | Owner and behavior |
|---|---|
| `~/Library/Application Support/GIMP/3.2/gimprc` | destination-managed marked configuration block; the file may also contain user content |
| `~/.codex/apps/petdex` | Petdex-owned state/product root retained in place for parity |
| `~/.codex/apps/petdex/bin` | persisted CLI JavaScript and desktop binary link |
| `~/.codex/apps/petdex/sidecar` | persisted sidecar link |
| `~/.petdex` | compatibility path pointing to `~/.codex/apps/petdex`, with migration/backup behavior for pre-existing state |

The parent `~/.codex/apps` remains a generic core path but is also created
idempotently by the destination before it creates the Petdex leaf. Shared
existence of an idempotently created parent is not shared ownership of the
Petdex leaf or its contents.

### 1.15 Skills

`none`. No tracked skill is owned by these products and no tracked skill
consumes Petdex.

### 1.16 Shell contributions

`none`. No static fragment, `PATH` addition, exported variable, or shell
initialization is contributed by this footprint.

### 1.17 Allowlist keys

`none`. The footprint reads no machine-local control-plane key.

### 1.18 System profile entries

The complete moving system-package set is:

```text
pkgs.audacity
pkgs.bruno
pkgs.dbeaver-bin
pkgs.zenmap
pkgs.petdex
pkgs.petdex-desktop
```

Dropping any entry rather than moving it is a parity failure. Applite and GIMP
are Homebrew casks, not system-profile entries. ChatGPT/current Codex desktop
is likewise a Homebrew cask rather than a system-profile entry.

### 1.19 Evaluation-time reads

`none` of an input or external repository. `modules/gimp.nix` binds the tracked
path `../config/gimp/gimprc-help-online` and interpolates its store path into
activation; that internal configuration dependency is inventoried in 1.7 and
does not perform `builtins.readFile` or couple evaluation to another checkout.
The user account parameter (`userName`) historically passed via curried module
arguments in core (`{ userName }: { ... }:`) is resolved in the destination's
`darwinModules.default` generically from standard nix-darwin module options
(such as `config.system.primaryUser` and `config.users.users.<name>.home`)
without external or out-of-band evaluation reads. The Petdex derivations fetch
their source at build time.

---

## 2. Destination module

```text
repository:             git+ssh://git@github.com/Knowledge-Forge-AI/developer-ux-nd.git
independent qualification commit:
                        14e95ce3cc156292b3ff79df3e83570cd6c8dcea
temporary-handoff commit:
                        417aff05dbb3330dc961d75115145983020423e6
rejected overlap state: the temporary-handoff commit above; retained evidence
State C overlap commit: fdbec5e2a47a1e2f55a3e5219a46845d5f902742
State C disposition:    activation-dormant temporary-handoff overlap;
                        independently requalified and selected
exports:                darwinModules.default, overlays.default,
                        packages.<system>.petdex,
                        packages.<system>.petdex-desktop,
                        applicationClaims, packageSetIdentity,
                        checks.<system>.*
exports omitted and why:
  apps                   no operator entry point in the inventoried footprint
  configAllowlist        reads no machine-local control state
  shellContributions     contributes no shell state
nixpkgs follows core:    YES
packageSetIdentity:      EXPORTED
post-CUT AI GUI runtime: ABSENT
```

The independently qualified destination contains only the existing migrated
footprint in section 1, including `chatgpt`. It exports the six system packages,
the three casks, the exact seven application claims, the GIMP managed
configuration, and the complete Petdex package/output/state contract. Commit
`417aff05dbb3330dc961d75115145983020423e6` subsequently added the seven exact
temporary ADR-0004 handoffs without changing those declarations. Its registered
overlap is preserved as the candidate that exposed duplicate GIMP and Petdex
writers; it is not the accepted parity state. Neither destination commit
contains the three post-cut GUI additions. The standalone qualification covers:

- exact system-package and Homebrew-cask deltas;
- both Petdex package builds and output wiring;
- the seven exact ordinary application claims with no handoffs for standalone
  qualification;
- evaluated proof that the selected Zenmap package contributes no `.app`;
- exact GIMP configuration and Petdex activation/state contracts (including
  user-generic resolution);
- package-set identity and `nixpkgs` following; and
- absence of unowned neighbors, deprecated `codex-app`, and destination runtime
  declarations for `antigravity`, `antigravity-ide`, or `claude` at this stage.

The handoff commit amends the destination's standalone claim checks in the same
commit, so the accepted checks continue to describe the exported claims exactly.

State C destination `fdbec5e2a47a1e2f55a3e5219a46845d5f902742` is the
transient, source-authoritative overlap. It retains every package, cask,
claim/handoff, Petdex overlay/package output, GIMP template, activation module
source, and focused test above, but `darwinModules.default` does not import the
GIMP or Petdex activation modules. Core therefore remains the sole selected
writer during overlap. Its 11 declared flake checks passed, including evaluated
zero default writer contribution, direct GIMP and Petdex behavior, and shell
syntax. Preserved independent qualification at
`14e95ce3cc156292b3ff79df3e83570cd6c8dcea` remains implementation evidence.
No `darwinModules.full` export, machine-local flag, allowlist key,
`moduleConfiguration`, or new bootstrap/registry mechanism was introduced.

---

## 3. Composition registration

```text
registry entry:             developer-ux in the public-capable composition
canonical repo URL:         git+ssh://git@github.com/Knowledge-Forge-AI/developer-ux-nd.git
deterministic checkout:     developer-ux-nd
configuration initializer:  none
bootstrap strategy:         none
registration revision:      a87c03c2a36fb544868aa1f6c95581ec383338db
destination input revision: 417aff05dbb3330dc961d75115145983020423e6
registration disposition:   claim-valid; parity-rejected duplicate-writer overlap
remediated registration:    da32ea392a6a8c3af5f4a97a61cab308bac37ebd
State C destination input:  fdbec5e2a47a1e2f55a3e5219a46845d5f902742
remediated disposition:     claim-valid, activation-dormant, parity-qualified;
                            only developer-ux-nd advanced
overlay position:           explicit; after the pre-existing registered inputs
overlay attributes:         petdex, petdex-desktop
allowlist keys added:       none
moduleConfiguration:        none
public aggregate:           VALID; 47 raw claims; exactly seven authorized
                            core -> developer-ux overlaps
```

The GIMP template is tracked inside the destination and Petdex paths are fixed
module behavior. User-scoped activation paths resolve generically through
standard nix-darwin options (`config.system.primaryUser`), so neither requires a
machine-local configuration file or domain initializer.
`bootstrap.strategy = none` is appropriate. Registration occurred after
standalone qualification and after the seven temporary handoffs in section 4.1
existed. The registered owner supplies no config, allowlist, or
`moduleConfiguration`, and its overlay participates in the composed package set.
The shared validator accepts exactly the seven authorized overlaps and no eighth
duplicate.

That historical registration is State B in ADR 0004's amendment: it proved the
claim-handoff and registration mechanics but selected destination activation
alongside the retained core writers. It was never activated and may be safely
superseded without altering its evidence. State C re-registration must advance
only the destination input to the independently requalified activation-dormant
handoff revision. The public composition was otherwise required to remain
fixed; the selected top-level composition would then advance only its
public-composition selection.

Public State C selection `da32ea392a6a8c3af5f4a97a61cab308bac37ebd`
completed that exact transition. Its lock adds destination
`fdbec5e2a47a1e2f55a3e5219a46845d5f902742` and changes no common core,
entertainment, VS Code, APGR, nixpkgs, nix-darwin, unstable-package, or
local-configuration revision. The registry retains `bootstrap.strategy = none`
and adds only owner `developer-ux`. The public aggregate validates at 47 raw
claims with exactly seven authorized `core -> developer-ux` pairs.

### 3.1 Selected top-level overlap support

The selected non-public top-level composition at
`60653021a4249af76acb0dd45d12901b887ce30b` consumes public registration
`a87c03c2a36fb544868aa1f6c95581ec383338db`. Its first 47 claims are an exact
prefix equal to the public aggregate; the complete aggregate validates at 50
claims: 47 public, zero from the first private owner, and three unique claims
from the security owner. The only duplicate identities are the same seven
developer-UX handoffs.
None of the three post-CUT AI GUI claims is present.
Its canonical 17-test repository suite and flake check pass, and both exposed
Darwin systems build with `--no-link --no-update-lock-file`. This is supporting
evidence for the rejected State B overlap selection only, not parity acceptance,
CUT, integration, or activation evidence. At that point, the future State C
top-level selection required fresh evidence after the public composition
selected the remediated destination revision.

### 3.2 State C selected top-level support

Selected top-level revision
`4222fc927c4848c8d2b59b21135e66eecbdfa2f9` consumes public State C
`da32ea392a6a8c3af5f4a97a61cab308bac37ebd` and destination
`fdbec5e2a47a1e2f55a3e5219a46845d5f902742`. Its complete aggregate validates
at 50 raw claims: the exact 47-claim public prefix, zero agent-security claims,
and three singleton security claims. The only duplicates are the same seven
authorized developer-UX handoffs; all three post-CUT GUI identities remain
absent.

The canonical 17-test suite and `nix flake check --no-update-lock-file` pass.
Both exposed private systems build with `--no-link --no-update-lock-file`.
Evaluated activation projection for both hosts contains zero destination GIMP
or Petdex definitions and exactly one core GIMP writer and one core Petdex
writer. This is supporting selected-top-level evidence for State C; it is not
CUT, destination-writer semantic equivalence, integration, activation, or
deployment evidence.

---

## 4. Application claims

### 4.1 Existing claims that migrate

| Application | `id` | `artifact` | `provider` | `package` | Current core declaration |
|---|---|---|---|---|---|
| Applite | `bundle:dev.aerolite.Applite` | `Applite.app` | `homebrew-cask` | `applite` | `modules/brewfile-stragglers.nix:29` |
| Bruno | `bundle:com.usebruno.app` | `Bruno.app` | `nix` | `bruno` | `modules/desktop-apps.nix:6` |
| DBeaver | `bundle:org.jkiss.dbeaver.core.product` | `dbeaver.app` | `nix` | `dbeaver-bin` | `modules/developer-tooling.nix:5` |
| GIMP | `bundle:org.gimp.gimp` | `GIMP.app` | `homebrew-cask` | `gimp` | `modules/desktop-apps.nix:18` |
| Audacity | `bundle:org.audacityteam.audacity` | `Audacity.app` | `nix` | `audacity` | `modules/desktop-apps.nix:5` |
| Petdex | `bundle:run.crafter.petdex-desktop` | `Petdex.app` | `nix` | `petdex-desktop` | `modules/codex.nix:7` |
| ChatGPT/current Codex desktop | `bundle:com.openai.codex` | `ChatGPT.app` | `homebrew-cask` | `chatgpt` | `modules/codex.nix:12` |

On 2026-08-21, read-only `Info.plist` inspection returned each exact identifier
from its installed artifact: Applite, GIMP, and ChatGPT under `/Applications`,
and the four Nix applications under `/Applications/Nix Apps`. The provider and
package fields come from core's tracked declaration/inventory. No installed
`Zenmap.app` was present, consistent with the evaluated completeness contract
that reads the selected system application output and reports no Zenmap
artifact.

#### Handoff plan

Every row above temporarily carries:

```nix
handoff = {
  from = "core";
  adr = "docs/adr/2026/08/0004-developer-ux-nd-migration.md";
};
```

The handoffs were added before registration and the destination's standalone
claim checks were amended in the same commit, so those checks continue to
describe the exported claims exactly.

| `id` | `from` | Added | Removed |
|---|---|---|---|
| `bundle:dev.aerolite.Applite` | `core` | `417aff05dbb3330dc961d75115145983020423e6` | pending |
| `bundle:com.usebruno.app` | `core` | `417aff05dbb3330dc961d75115145983020423e6` | pending |
| `bundle:org.jkiss.dbeaver.core.product` | `core` | `417aff05dbb3330dc961d75115145983020423e6` | pending |
| `bundle:org.gimp.gimp` | `core` | `417aff05dbb3330dc961d75115145983020423e6` | pending |
| `bundle:org.audacityteam.audacity` | `core` | `417aff05dbb3330dc961d75115145983020423e6` | pending |
| `bundle:run.crafter.petdex-desktop` | `core` | `417aff05dbb3330dc961d75115145983020423e6` | pending |
| `bundle:com.openai.codex` | `core` | `417aff05dbb3330dc961d75115145983020423e6` | pending |

Zenmap has no claim/handoff. The Petdex CLI has no separate claim because it is
command-line-only.

### 4.2 Post-CUT destination-only AI GUI claims

These are new destination-only footprint and are excluded from section 4.1 and
parity:

```nix
{
  id = "bundle:com.google.antigravity";
  artifact = "Antigravity.app";
  provider = "homebrew-cask";
  package = "antigravity";
}
{
  id = "bundle:com.google.antigravity-ide";
  artifact = "Antigravity IDE.app";
  provider = "homebrew-cask";
  package = "antigravity-ide";
}
{
  id = "bundle:com.anthropic.claudefordesktop";
  artifact = "Claude.app";
  provider = "homebrew-cask";
  package = "claude";
}
```

Current Homebrew metadata records the exact artifacts above. It uses
`com.google.antigravity` and `com.google.antigravity-ide` as the respective
quit identities, and the `antigravity-ide` cask additionally exposes
application-bundled `antigravity-ide` and `agy-ide` binaries. Neither
Antigravity bundle is installed, so both Google identities remain provisional
pending live `Info.plist` reads after first installation. The installed
unmanaged `Claude.app` reports `com.anthropic.claudefordesktop`, matching the
`claude` cask's primary quit identity. All three claims have no handoff, and no
version is pinned.

### 4.3 Read-only host/provider adoption audit

The audit used exact `brew list --cask --versions` queries for all requested
tokens and direct reads of installed `Info.plist` files. It did not install,
uninstall, upgrade, reinstall, adopt, or activate anything.

| Token/surface | Homebrew registration on 2026-08-21 | Bundle readback | Boundary |
|---|---|---|---|
| `antigravity` | not registered | `Antigravity.app` absent | post-CUT addition; `com.google.antigravity` provisional |
| `antigravity-ide` | not registered | `Antigravity IDE.app` absent | post-CUT addition; `com.google.antigravity-ide` provisional |
| `antigravity-cli` | registered, `1.1.17,5084709148033024` | no application bundle | retained core `agy` provider |
| `claude` | not registered | `Claude.app` present; `com.anthropic.claudefordesktop` | unmanaged pre-existing bundle; not yet declarative |
| `chatgpt` | registered, `1.2026.153,1780942906` | `ChatGPT.app` present; `com.openai.codex` | existing core footprint covered by migration parity |
| `codex-app` | not registered; provider marks it deprecated/discontinued with `chatgpt` as replacement | `Codex.app` absent | excluded; must not be added |
| `codex` | registered, `0.147.0` | no application bundle | retained core Codex CLI provider |
| adjacent ChatGPT Classic | no matching installed cask receipt | `ChatGPT Classic.app` present; `com.openai.chat` | distinct unmanaged bundle, deliberately unclaimed and untouched |

Core declares no Homebrew cask adoption argument. Homebrew exposes an explicit
`--adopt` mode only for identical existing artifacts, so this record does not
claim that an ordinary activation will safely adopt `Claude.app`. Before the
joint activation, an operator-approved non-destructive disposition must bind
the existing artifact and user state; activation stops if the planned provider
action could overwrite, remove, or reject it. The currently registered
`ChatGPT.app` still receives before/after provider and state checks because its
installation origin was independent of this ownership decision.

---

## 5. Parity

Status: `BLOCKED`. Canonical parity ran while core still owned every section 1
declaration. The evidence phase is complete, but the migration remains
`BUILDING` because rejected State B changes GIMP and Petdex activation behavior.

The clean historical boundary discovered from public-composition history is:

```text
baseline:           fac9adb7b8fbb89ef063e44c3b7ff58ff3baf055
overlap candidate:  a87c03c2a36fb544868aa1f6c95581ec383338db
common core:        332afc0b5dd6c7eeef272628f3feea2ffa0747a8
destination:        absent from baseline; 417aff05dbb3330dc961d75115145983020423e6 in overlap
hosts:              example-workstation, example-primary
```

Both revisions retain the same core, entertainment, VS Code, APGR, `nixpkgs`,
`nixpkgs-unstable`, nix-darwin, local-config content/topology, and pre-existing
registry order. The candidate's only new owner input is `developer-ux-nd`; no
unrelated runtime owner revision advanced. Subsequent core development differs
from the common pinned core `332afc0b5dd6c7eeef272628f3feea2ffa0747a8` only
through unrelated retained-core Antigravity CLI declarations and
governance/migration documentation. No moving package, cask, claim, GIMP, or
Petdex contract drifted.

Parity is proved while core still provides every section 1 declaration. The
baseline and overlap artifacts cover:

```text
normalized system package set
Homebrew taps, brews, casks, MAS apps, and rendered declarations
the complete application-claim aggregate and handoff validation
activation text, including GIMP and Petdex behavior
option leaves, assertions, and effective values
owner and mode contracts for GIMP and Petdex paths
declared state/configuration roots
launchd and log rotation projections, including the empty domain result
generated shell state, including the empty domain result
```

Observed surface results for both hosts are:

| Surface | Result | Evidence summary |
|---|---|---|
| normalized system packages | `MATCH` | 135 -> 141 raw declarations; the six moving packages are 1 -> 2, while all normalized derivation identities match |
| Homebrew effective state | `MATCH` | taps, brews, normalized casks, and MAS apps match; raw rendered Brewfile text differs by the three duplicate cask declarations (`applite`, `gimp`, `chatgpt` 1 -> 2 raw declarations) which normalize to equal effective state |
| application-claim payloads | `MATCH` | all seven destination payloads match core after removing owner/handoff metadata |
| application-claim ownership | `EXPECTED DIFFERENCE` | 40 -> 47 raw claims; exactly seven authorized pairs, no eighth duplicate; entertainment remains singleton and handoff-free |
| Petdex overlay/package outputs | `MATCH` | both derivation identities, versions, package source hashes, and the two-attribute destination overlay match the core-selected outputs |
| GIMP managed configuration | `DIFFERENT` | the same path, template, markers, preservation, and ownership contract now has two sequential writers with different implementation text |
| Petdex state/outside-store behavior | `DIFFERENT` | the path/leaf/backup/link/owner/mode contract is unchanged, but two different migration/link writers now execute |
| activation | `DIFFERENT` | definition count 44 -> 46; destination Petdex and GIMP blocks precede the retained core GIMP and combined Codex/Petdex blocks; each relevant side-effect count is 1 -> 2 |
| launchd, log rotation, shell, options/assertions | `MATCH` | complete canonical projections match |
| state/config roots and other outside-store contracts | `MATCH` | no new root or unrelated path/owner/mode contract appears |

The different `system-applications` store paths have the same NAR hash. The
different derived `system-path/share/terminfo` sources also have the same NAR
hash. GIMP template bytes and both Petdex package expressions have equal
SHA-256 hashes across source and destination, and the selected Petdex
derivation paths are identical. Those facts support the pre-enumerated
source/store-path normalization; they do not authorize duplicate activation.
No synthetic scratch-home rehearsal was performed because the raw additional
writers and divergent implementation text already decide the frozen parity
contract.

The exact blockers are the second GIMP managed-configuration writer and the
second Petdex migration/link writer. The destination variants use generic
primary-user resolution, shell escaping, and—in Petdex—the independently
established parent hierarchy, while the core variants remain active. The
candidate therefore changes sequence and side-effect count even though the
target paths coincide. This is not an expected difference.

### 5.1 Single-writer remediation and parity rerun

ADR 0004 now requires exactly one selected writer for each migrating side
effect in every valid state. The planned State C destination overlap keeps all
six system packages, all three casks, both Petdex package/overlay outputs, all
seven handoffs, the GIMP template, both activation module source files, and
their focused full-behavior tests. Its `darwinModules.default` omits the GIMP
and Petdex activation imports so core remains the sole selected writer for the
GIMP managed-help block and Petdex state migration, compatibility path, payload
copies, and links.

The State C implementation is independently requalified before selection.
Tests import the dormant module source files directly and continue to prove the
full intended destination behavior; no additional public module export is
needed. The public composition then advances only `developer-ux-nd` while core
and every unrelated owner stay fixed, and the selected top-level composition
advances only its public-composition selection.

The three existing DEVUX5 artifacts are immutable historical evidence for the
rejected State B candidate and retain their original names and `BLOCKED`
result. A remediated rerun uses these non-destructive names:

```text
remediated candidate:   parity-remediated-overlap.json
remediated comparison:  parity-remediated-comparison.json
conditional baseline:   parity-remediated-baseline.json
```

The canonical preference is to reuse `parity-baseline.json` and its clean
pre-registration public revision by selecting State C against the identical
common core and unrelated input set. Reuse is valid only when those inputs are
identical to the frozen baseline. If that clean boundary cannot be preserved,
the rerun adds `parity-remediated-baseline.json` from a clean pre-registration
selection with exactly the same core and unrelated inputs as its remediated
candidate. It must never compare unequal core/input boundaries or overwrite
the original artifact.

For both exposed hosts, the remediated comparison must prove the existing
package, cask, claim, Petdex-derivation, launchd, log-rotation, shell, option,
root, and outside-store contracts under the already frozen normalization. It
must also prove GIMP writers `1 -> 1`, Petdex writers `1 -> 1`, activation
definitions `44 -> 44` when the original common-core boundary is reused, and
no unrelated activation change. No duplicate-activation category is added to
expected differences. Status may advance to `PARITY` only after those checks
pass; advancing to `PARITY` at this rerun proves non-activation footprint
neutrality and that core remains sole writer without duplicate activation,
while semantic equivalence between the destination and core activation
implementations is evaluated at the subsequent matched-cut semantic gate.
Planning this rerun does not complete parity.

### Expected differences, enumerated in advance

| Difference | Why expected |
|---|---|
| source and derived store paths | relocating tracked sources changes derivation inputs without changing the observable contract |
| raw package/cask multiplicity during overlap | core and destination both declare the footprint; normalized effective sets must remain equal |
| seven destination claim owners and handoff records | the overlap mechanism adds metadata and an authorized second claimant; ids, artifacts, providers, and packages must match core exactly |
| ordering within merged package/cask declarations | composition may assemble equal sets in a different order; no activation or effective-set difference is allowed |

`antigravity`, `antigravity-ide`, and `claude`, their application artifacts,
and their three claim ids must be absent from both parity artifacts. Their
presence is contamination, not an expected difference. `chatgpt` and
`bundle:com.openai.codex` must be present on both sides as migrated core
footprint.

```text
baseline artifact:    nix-darwin/docs/architecture/domain-migrations/evidence/developer-ux-nd/parity-baseline.json
candidate artifact:   nix-darwin/docs/architecture/domain-migrations/evidence/developer-ux-nd/parity-overlap.json
comparison artifact:  nix-darwin/docs/architecture/domain-migrations/evidence/developer-ux-nd/parity-comparison.json
result:               BLOCKED
next boundary:        implement and independently qualify the activation-dormant
                      temporary-handoff destination revision without changing
                      claims, packages, casks, overlays, or post-CUT GUI scope
```

### 5.2 Remediated State C parity rerun

Status: `MATCH_WITH_ENUMERATED_DIFFERENCES`. The canonical rerun compares the
same clean pre-registration baseline with the selected activation-dormant State
C public overlap:

```text
baseline public:        fac9adb7b8fbb89ef063e44c3b7ff58ff3baf055
State C public:         da32ea392a6a8c3af5f4a97a61cab308bac37ebd
common core:            332afc0b5dd6c7eeef272628f3feea2ffa0747a8
State C destination:    fdbec5e2a47a1e2f55a3e5219a46845d5f902742
selected private:       4222fc927c4848c8d2b59b21135e66eecbdfa2f9
hosts:                  example-workstation, example-primary
baseline disposition:   REUSED; no remediated baseline artifact created
```

Baseline reuse is valid at the lock-node boundary: core, entertainment, VS
Code, APGR, nixpkgs, nixpkgs-unstable, nix-darwin, local-config content and
topology, and the pre-existing registry owners/order are identical. State C
adds only the destination input and `developer-ux` registry row. The reused
`parity-baseline.json` SHA-256 is
`679d6252e610b429680b7ee4cc05cb74a8df1740701be1c03a11050206335c6d`.

The new evidence is:

```text
candidate artifact:   nix-darwin/docs/architecture/domain-migrations/evidence/developer-ux-nd/parity-remediated-overlap.json
comparison artifact:  nix-darwin/docs/architecture/domain-migrations/evidence/developer-ux-nd/parity-remediated-comparison.json
result:               MATCH_WITH_ENUMERATED_DIFFERENCES
```

Before and after the rerun, the original State B artifacts remained
byte-identical: `parity-baseline.json` SHA-256 `679d6252e610b429680b7ee4cc05cb74a8df1740701be1c03a11050206335c6d`,
`parity-overlap.json` SHA-256 `281051b1b7f64fd680a40f1280109ea39a58c11792fe36883859a46d6bcb9fec`,
and `parity-comparison.json` SHA-256
`5b5793779417a5a71292447e781aec0b6a0e239c659727af00009239de72c886`.

Observed results are the same on both hosts:

| Surface | Result | Evidence summary |
|---|---|---|
| normalized system packages | `MATCH` | 135 -> 141 raw declarations; each of the six moving packages is 1 -> 2 while normalized derivations, including both Petdex derivations, remain equal |
| Homebrew effective state | `MATCH` | taps, brews, normalized casks, MAS apps, and entertainment singletons match; `applite`, `gimp`, and `chatgpt` are 1 -> 2 raw declarations |
| application claims | `EXPECTED DIFFERENCE` | 40 -> 47 raw public claims; exactly seven matching payload pairs and seven exact handoffs; no eighth duplicate, post-CUT identity, or deprecated `codex-app` |
| Petdex overlay/package outputs | `MATCH` | overlay remains exactly `petdex` and `petdex-desktop`; derivations, versions, source hashes, and expression hashes match core selections |
| GIMP managed configuration | `MATCH` | target, markers, template, preservation, owner/mode contract, and selected writer count are equal; core is sole writer, 1 -> 1 |
| Petdex state/outside-store | `MATCH` | roots, leaves, migration/backup, copy/link, owner/mode, and surrounding Codex-state contracts match; core is sole writer, 1 -> 1 |
| activation | `MATCH` | 44 -> 44 definitions; ordered definition/dependency projection, relevant sequence, and complete activation match after only the frozen source/derived-store normalization; destination contributes zero GIMP/Petdex definitions |
| launchd | `MATCH` | complete system and user agent/daemon projections match |
| log rotation | `MATCH` | complete effective declarations match |
| shell state | `MATCH` | initializers, variables, PATH contributions, and shell contributions match |
| options/assertions | `MATCH` | option leaves, values, and assertions match; no destination option namespace appears |
| roots/outside-store contracts | `MATCH` | no new selected root, writer, path, owner, or mode contract appears |

The activation normalization retains command order and multiplicity. It replaces
only the already authorized equivalent `system-applications`, `terminfo`,
rendered Brewfile, and derived `activate-*` store paths. Exactly 3 of 26
activation entries differ (`applications` by store path, `homebrew` by the 3
frozen duplicate cask declarations in raw Brewfile, and `script` by embedded
derived `activate-*` paths), all falling strictly within the two frozen
expected-difference categories. The two Brewfiles differ only by the three
frozen duplicate cask declarations; no command was removed, sorted, or waived as
idempotent. Normalized `postActivation` text (`5d4295fad767ad4f05ae7213dffb90b9995b3ca43fc44fdc2fcb5d62dd24b02b`)
and the relevant writer sequence (`core:modules/gimp.nix`, `core:modules/codex-state.nix`)
are byte-identical.

Both scratch public revisions pass their revision-appropriate 24-test suites,
`nix flake check --no-update-lock-file`, and both exposed `.system` builds with
`--no-link --no-update-lock-file`. Destination State C passes all 11 declared
flake checks, including direct GIMP/Petdex behavior and zero default writer
contribution. Selected private State C passes its 17-test suite, flake check,
and both private `.system` builds; its aggregate is exactly 47 public + 0
agent-security + 3 security claims, and each private host has zero destination
definitions plus one core GIMP and one core Petdex writer. Nothing was
activated.

This rerun proves neutral overlap and sole core writer authority. It does not
prove semantic equivalence of the dormant destination writers; that remains the
matched-cut gate.

---

## 6. Core removal and matched cut

Not started. The future core candidate checklist is:

```text
[ ] remove applite from modules/brewfile-stragglers.nix
[ ] remove audacity, bruno, and zenmap from modules/desktop-apps.nix
[ ] remove gimp from modules/desktop-apps.nix
[ ] remove dbeaver-bin from modules/developer-tooling.nix
[ ] remove petdex and petdex-desktop from modules/codex.nix
[ ] remove chatgpt from modules/codex.nix while retaining antigravity-cli and codex
[ ] remove modules/gimp.nix, its flake import, and config/gimp/gimprc-help-online
[ ] remove both Petdex package definitions, overlay attributes, and package outputs
[ ] split Petdex behavior out of modules/codex-state.nix without changing the
    retained Codex/Serena behavior
[ ] remove the seven source application claims
[ ] split all mixed tests; repoint the gimp stale-claim mutation sentinel; remove
    zenmapIsCoreOwned and zenmapHasNoClaim assertions from core
[ ] move all six coverage-manifest rows with their sources
[ ] leave no Petdex stub, alias, state writer, or reverse dependency in core
[ ] preserve antigravity-cli/agy, codex CLI, claude-code, and both core
    non-application-cask records
[ ] do not add antigravity, antigravity-ide, claude, or codex-app to core
```

The destination cut candidate makes two coordinated changes in one candidate
revision: it removes the seven temporary handoffs and re-enables/imports the
retained GIMP and Petdex activation modules in `darwinModules.default`. The core
candidate simultaneously removes the source writers and all other moving
footprint above. A clean scratch composition must prove:

```text
new core + new destination cut candidate:
  VALID claims; exactly one GIMP writer and one Petdex writer, destination-owned
old core + new destination cut candidate:
  INVALID, seven duplicate-claim errors; may have two writers but is rejected
  before selection or activation
new core + remediated overlap destination:
  INVALID, seven singleton-handoff errors; has zero migrating writers but is
  rejected before selection or activation
```

The claim validator is binding for the two invalid half-cuts but cannot detect a
zero-writer implementation mistake. A malformed destination that removed
handoffs without re-enabling its dormant writers could pair with new core
without claim errors. It therefore fails the independent cut gate: a qualified
destination cut candidate must contain both coordinated changes, and the
selected new/new pair must prove exactly one destination-owned writer for each
side effect. No fallback or stub makes an invalid state runnable. Only the
public composition revision that selects both qualified halves is `CUT`.

Cut qualification compares the parity-qualified State C pair, where core is
sole writer, with the new/new pair, where destination is sole writer. For both
exposed hosts, the binding semantic projection proves:

- GIMP resolves the same primary user/home, targets the same
  `~/Library/Application Support/GIMP/3.2/gimprc`, uses the same managed
  markers and template bytes, preserves surrounding content, preserves
  ownership and mode behavior, has one writer before and after, and changes no
  unrelated activation;
- Petdex retains `~/.codex/apps/petdex`, `bin`, `sidecar`, the `~/.petdex`
  compatibility behavior, conflict-backup semantics, copied CLI JavaScript,
  desktop and sidecar links, ownership and modes, has no dependency on core
  activation ordering, has one writer before and after, and changes no
  unrelated state root; and
- structural/textual differences are accepted only when semantic projection or
  a safe scratch-home rehearsal proves the observable contract equal without
  touching the live user profile.

---

## 7. Integrate and post-CUT feature

The selected sequence is:

```text
BUILDING
  -> standalone destination with existing footprint only
  -> handoffs and registration
  -> canonical parity evidence: BLOCKED on duplicate GIMP/Petdex writers
  -> destination activation-dormant handoff overlap implemented and independently qualified
  -> public composition advances only developer-ux-nd; selected top-level
     composition advances only that public selection
  -> canonical parity rerun with core as sole overlap writer
  -> PARITY only after the rerun matches the frozen contract
  -> destination cut candidate atomically removes handoffs and re-enables both
     writers while the matched core candidate removes both source writers
  -> semantic writer-handoff qualification and selected public CUT
  -> destination-only Antigravity command-center, Antigravity IDE, and Claude
     GUI feature with no handoffs
  -> public composition advances to the post-cut destination
  -> non-public top-level composition advances once and passes integrated gates
  -> INTEGRATED, without activation
```

Integrated gates include registry and checkout uniqueness, overlay order,
package-set equality, zero stale handoffs, unique application ids/artifacts,
split-test union, core coverage, core building with the destination physically
absent, public and selected non-public builds, and input-scoped lock movement
with no unrelated owner drift.

Entertainment is already `INTEGRATED` and remains selected but unactivated.
Developer UX does not alter entertainment runtime state while reaching its own
integrated boundary.

---

## 8. Activation requirement and residual risk

```text
activation required:   YES
justification:         Homebrew installation, live bundle identity, GIMP user
                       configuration, Petdex state migration/links, and the new
                       Antigravity/Claude GUIs cannot be proved by build parity
authorization:         NOT AUTHORIZED
```

One later explicit operator authorization activates the selected private
composition once for both the entertainment and developer-ux migrations. The
report attributes evidence by domain rather than treating one green host as an
undifferentiated result.

### Residual risks

| Risk | Why build parity cannot cover it | Required mitigation/evidence |
|---|---|---|
| Homebrew removes or reinstalls Applite or GIMP when declaration ownership changes | activation-only provider behavior | record installed provider/state before and after joint activation |
| a provider update changes an installed bundle identity | claims validate declarations, not disk contents | re-read every migrated GUI bundle before and after activation |
| GIMP managed help content or ownership changes | user-profile file mutation occurs only during activation | inspect the marked block, preserved user content, owner, and mode |
| Petdex legacy migration or links fail on an existing profile | build evidence cannot exercise live conflicts, backups, deep links, or ownership | verify `~/.petdex`, the canonical leaf, CLI JS, desktop link, sidecar link, product launch, and deep-link flows |
| destination accidentally depends on core-created `~/.codex/apps` | module ordering and pre-existing state can hide the dependency | qualify idempotent parent/leaf creation and verify with the core Petdex slice absent |
| Zenmap begins shipping a selected `.app` | package outputs can change with `nixpkgs` | rerun application completeness at standalone, parity, cut, and integrated gates; add a claim only if an artifact exists |
| an Antigravity identity differs from cask metadata | both GUIs are absent now and metadata is not an installed `Info.plist` | verify both application artifacts and `CFBundleIdentifier` values on first install and post-activation |
| Homebrew rejects or overwrites unmanaged `Claude.app` | no adoption mechanism is selected and build evidence cannot exercise the existing destination artifact | bind an operator-approved non-destructive pre-activation disposition; capture bundle and user-state evidence before and after; stop on mismatch or destructive action |
| the independently installed ChatGPT/current Codex desktop changes provider or state unexpectedly | its current Homebrew receipt does not prove future ownership-transfer behavior | capture receipt, bundle identity, application state, and relevant user-state checks before and after activation |
| unmanaged `ChatGPT Classic.app` is removed by adjacent cleanup | it is distinct and deliberately outside the declarative claim set | prove `com.openai.chat` remains unclaimed and unchanged |
| an IDE-bundled launcher displaces a core CLI | package and link order is an activation/PATH property | prove core `agy`, Codex CLI, and Claude Code remain authoritative; separately identify destination `agy-ide` and `antigravity-ide` |
| joint activation obscures which migration caused a change | both unactivated domains become live in one switch | use separate entertainment and developer-ux checklists and result sections in one report |

Future entertainment checks: `yt-dlp` on `PATH`; Discord, MacGameStore, OpenEmu,
and VLC; live identities/providers; unexpected Homebrew removal/reinstall.

Future developer-ux checks: Applite, Bruno, DBeaver, Zenmap if it then supplies
an app bundle, GIMP, Audacity, all moved Petdex product/state/deep-link surfaces,
ChatGPT/current Codex desktop, both Antigravity GUIs, Claude desktop, and all
live identities/providers. Retained-core checks prove continued `agy`
availability from `antigravity-cli`, continued Codex CLI and Claude Code
availability, and no authority transfer to the IDE-bundled launchers. Adoption
evidence must show the independently installed GUI state survived without
destructive loss.

Neither record advances to `COMPLETE` without its own supported post-activation
evidence.

---

## 9. History and docs disposition

```text
documents moved to destination:       none; destination owns its implementation
                                      and qualification docs in its repository
documents moved to private evidence:  none; this is PUBLIC-CAPABLE footprint
documents deliberately left in core:
  ADR 0002 — normative framework ownership/claims decision
  ADR 0004 and this inventory — migration decision and core-removal checklist
  entertainment migration record/evidence — independent domain evidence and
    the matched-cut reference
  mixed historical deployment evidence — retained for its original core phase
```

At `COMPLETE`, reassess whether any implementation-specific material created in
later phases belongs in the destination. Framework decisions and the core
removal record remain where their contracts and evidence are verified.

---

## 10. Evidence

```text
[x] ADR 0002 minimally records the expanded human-facing AI GUI and retained CLI ownership
[x] migration decision record: docs/adr/2026/08/0004-developer-ux-nd-migration.md
[x] complete live tracked-source inventory across all template families
[x] current installed identities re-read for the seven existing GUI claims
[x] current Homebrew, Google, Anthropic, and OpenAI product metadata checked without installation
[x] read-only host audit records unmanaged Claude and ChatGPT Classic distinctly
[x] both Antigravity claim identities marked provisional until installed readback
[x] Petdex consumer search found no core autonomous-agent dependency
[x] Petdex state-root and compatibility-path ownership decision recorded
[x] Zenmap no-bundle claim tied to evaluated completeness and live artifact absence
[x] destination repository created and independently qualified at 14e95ce3cc156292b3ff79df3e83570cd6c8dcea
[x] seven temporary handoffs added at 417aff05dbb3330dc961d75115145983020423e6
[x] public registration selected at a87c03c2a36fb544868aa1f6c95581ec383338db
[x] selected non-public top-level overlap verified at 60653021a4249af76acb0dd45d12901b887ce30b
[x] canonical parity attempt completed against the clean pre-registration baseline
[x] original baseline/overlap/comparison artifacts retained byte-for-byte; result BLOCKED
[x] activation-dormant temporary-handoff destination overlap implemented and independently qualified at fdbec5e2a47a1e2f55a3e5219a46845d5f902742
[x] public and selected top-level compositions advanced only to State C at da32ea392a6a8c3af5f4a97a61cab308bac37ebd and 4222fc927c4848c8d2b59b21135e66eecbdfa2f9
[x] canonical parity rerun retained under the two non-destructive remediated artifact names
[x] rerun proves one core GIMP writer and one core Petdex writer on both hosts; result MATCH_WITH_ENUMERATED_DIFFERENCES; status PARITY
[ ] matched candidates and both invalid half-cut controls qualified
[ ] matched cut semantic writer handoff qualified for GIMP and Petdex on both exposed hosts
[ ] selected public CUT
[ ] post-CUT Antigravity command-center, Antigravity IDE, and Claude GUI feature independently qualified
[ ] public and selected non-public compositions advanced to final integrated state
[ ] integrated gates and two-review evidence retained for each future phase
[ ] joint activation explicitly authorized and completed
[ ] domain-attributed post-activation evidence supports COMPLETE
[ ] publication-readiness handoff recorded where the program tracks it
```

Current status is `PARITY`. The original State B comparison remains `BLOCKED`
and immutable; State C destination qualification, public/private selection, and
the separately named remediated parity rerun now prove core-only overlap writer
counts and neutral effective behavior. The matched cut, cut-time destination
writer equivalence, post-CUT GUI feature, integration, installation, and
activation remain pending.

---

## Appendix A: Adjacent items deliberately not claimed

| Item | Current source | Disposition |
|---|---|---|
| `pkgs.qbittorrent` | `modules/desktop-apps.nix` | unassigned by ADR 0002; remains core pending its own ownership decision |
| `homebrew.brews = [ "mas" ]` and unrelated casks/MAS apps | `modules/desktop-apps.nix` | mixed neighbors owned by core or other planned domains; not developer-ux footprint |
| `pkgs.liquibase`, `pkgs.nil`, `pkgs.nixd`, `pkgs.nixfmt-rfc-style`, `pkgs.ripgrep`, `pkgs.shellcheck`, `pkgs.shfmt` | `modules/developer-tooling.nix` | retained core tooling and agent-useful primitives |
| Corepack, fonts, fuse tap/brew, kcov, macFUSE | `modules/brewfile-stragglers.nix` | unrelated mixed-module declarations |
| `pkgs.claude-code`, casks `codex`, `antigravity-cli` | `modules/codex.nix` | retained core terminal/agent primitives; `antigravity-cli` installs `agy`, while `codex` installs the Codex CLI |
| Codex docs and Serena state behavior | `modules/codex-state.nix` | generic/core agent state retained after the Petdex split |
| `ChatGPT Classic.app`, `com.openai.chat` | unmanaged host bundle | deliberately unclaimed and untouched; distinct from the migrating `ChatGPT.app`, `com.openai.codex` |
| deprecated `codex-app` / `Codex.app` | not declared in core; not installed | excluded in favor of current `chatgpt`; must not be added |

## Appendix B: Post-CUT new footprint

The three destination-only GUI additions are recorded here so they cannot be
mistaken for missing migration rows:

```text
timing:       after selected CUT
owner:        developer-ux-nd
provider:     homebrew-cask
handoff:      none; core never declaratively owned these GUIs
parity role:  excluded from baseline and overlap

cask:         antigravity
artifact:     Antigravity.app
identity:     bundle:com.google.antigravity (provisional until installed readback)

cask:         antigravity-ide
artifact:     Antigravity IDE.app
identity:     bundle:com.google.antigravity-ide (provisional until installed readback)
adjunct CLIs: antigravity-ide and agy-ide; never authority for core agy

cask:         claude
artifact:     Claude.app
identity:     bundle:com.anthropic.claudefordesktop (live-read from unmanaged bundle)
adoption:     unresolved activation risk; requires operator-approved disposition
```
