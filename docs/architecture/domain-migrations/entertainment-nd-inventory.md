# `entertainment-nd` Migration Inventory

Filled in initially from the tracked tree at `3539b2d` and from the live host
on 2026-08-17, then advanced through independent destination qualification,
overlap registration and canonical `PARITY`, matched-cut qualification, the
selected public `CUT`, and selected private `INTEGRATED` qualification.
Structure and rules come from the
[owner migration template](owner-migration-template.md); normative decisions come
from [ADR 0001](../../adr/2026/08/0001-nix-darwin-core-subflake-extension-contract-and-owner-driven-migration.md)
and [ADR 0002](../../adr/2026/08/0002-domain-ownership-application-claims-and-migration-sequencing.md).
The migration-specific decision is
[ADR 0003](../../adr/2026/08/0003-entertainment-nd-migration.md).

The destination repository qualified independently and later gained four
temporary handoffs for overlap registration and parity. The matched
claim-free-core and handoff-free-destination candidates then qualified
together. Public composition revision
`fac9adb7b8fbb89ef063e44c3b7ff58ff3baf055` selected both halves and is the
`CUT` boundary. Selected non-public top-level revision
`21c6bd6700e8f6195165d0471cf40ccba8a544aa` advanced to that public cut and
passed the public-plus-non-public gates, establishing `INTEGRATED`. Activation
is still required and unauthorized, so the migration is not `COMPLETE`.

## Header

```text
module:                 entertainment-nd
publication class:      PUBLIC-CAPABLE
owning composition:     the public-capable composition
owner:                  operator
status:                 INTEGRATED
governing ADR:          docs/adr/2026/08/0003-entertainment-nd-migration.md
activation required:    YES
```

Publication class is PUBLIC-CAPABLE because the destination has no non-public
required dependency and is selected by the public-capable composition. Nothing
in the migrated surface reads an operator identity, a private input, or
machine-local secret state.

---

## 1. Pre-cut inventory

This section preserves the source inventory used to qualify parity and the
ownership cut. Paths and line references describe the pre-cut source state;
sections 6 and 7 record their current disposition.

### 1.1 Flake inputs

`none`. At inventory time, every item below came from core's existing `nixpkgs`
or from Homebrew taps already enabled for unrelated reasons. The migration
added no input to the destination and removed none from core.

### 1.2 Packages and overlay attributes

| Item | Pre-cut source path | Declaring line |
|---|---|---|
| `pkgs.yt-dlp` | `nix-darwin/flake.nix` | 221, inside the `environment.systemPackages` list |

No overlay attribute, no `callPackage` wiring, no `packages.<system>` entry, and
no compatibility alias exists for any item in this domain. `yt-dlp` is consumed
straight from `nixpkgs`.

This is the one entry that breaks a naive cut: `yt-dlp` is a **system profile
entry**, so removing it from core without the destination module declaring it
changes core's built system package set.

### 1.3 Darwin modules

`none` dedicated to this domain. Before the cut, the applications were declared
inside
`nix-darwin/modules/desktop-apps.nix`, a **mixed** module that also declares
office, storage, personal, and developer-adjacent applications. The module is
imported at `nix-darwin/flake.nix:303`.

The module file was therefore **split**, not moved. Core keeps
`modules/desktop-apps.nix` and its import for the applications this migration
does not claim.

### 1.4 Options

`none`. This domain declares no option namespace and makes no assignment outside
the module list.

### 1.5 Host facts

`none`. No key in core's host table is read only by this domain.

### 1.6 Application claims

See section 4.

### 1.7 Configuration

`none`. No file under a configuration tree, no template, no policy document, and
no container build context belongs to this domain.

### 1.8 `bin/`, `libexec/`, `tools/`

`none`. No user-facing command and no helper implementation in core invokes any
of these applications or `yt-dlp`.

### 1.9 Tests

| Test | Path | Lines |
|---|---|---|
| `yt-dlp` in the system package set | `nix-darwin/src/test/int/nix/darwin-configuration.int.test.nix` | 292, inside `mediaAndNetworkTools` (287–294) |
| `discord` cask | same file | 322 |
| `macgamestore` cask | same file | 332 |
| `openemu` cask | same file | 335 |
| `vlc` cask | same file | 337 |
| Core application claim count (`39`) | `nix-darwin/src/test/int/nix/application-claims.int.test.nix` | 83 |
| Removed-declaration test sentinel (`vlc`) | same file | 98, 104 |
| Entertainment handoff targets test | same file | 176–224 (`testEntertainmentMigrationTargets...`) |
| `yt-dlp` core ownership and claim exclusion | same file | 235–236, 246–247 (inside `testCommandLineOnlyPackages...`) |

The assertions in `darwin-configuration.int.test.nix` live in **mixed** test
attributes. `mediaAndNetworkTools` also asserts `audacity`, `nmap`,
`qbittorrent`, `rclone`, and `zenmap`; the cask list at 318–338 asserts
twenty-one casks in total, of which seventeen are owned by other domains. Per
the template's core-removal rule these attributes are **split**: core retains
the assertions for what core still declares, and the destination module asserts
its own. Relocating either whole attribute would silently drop core's coverage
of the packages that stay.

`nix-darwin/src/test/int/nix/application-claims.int.test.nix` also contained
mixed and domain-specific test assertions:
- `testEntertainmentMigrationTargetsAreClaimedAndReadyToServeAsHandoffSources`
  was dedicated to validating that core's four entertainment claims were ready
  for destination handoffs;
- the core claim count assertion (`count = 39`) validated the total claim set
  including the four entertainment claims;
- `detectsRemovedDeclaration` used `vlc` as the sentinel cask to test that
  removing a declared cask without removing its claim fails;
- `testCommandLineOnlyPackagesAreExcludedFromApplicationClaims` asserted both
  that `yt-dlp` was owned by core and that it had no application claim.

`nix-darwin/src/test/unit/nix/flake.unit.test.nix` carries a
`nix-darwin-test-source-target: modules/desktop-apps.nix` header (line 9) but
asserts nothing about any cask, so it needs no split — only a re-check that its
target header is still accurate after the source is split.

### 1.10 Coverage rows

| Row | `src/test/coverage-manifest.tsv` |
|---|---|
| `int nix modules/desktop-apps.nix` | line 13 |
| `unit nix modules/desktop-apps.nix` | line 114 |

Neither row is removed by this migration, because `modules/desktop-apps.nix`
survives the split. If a later migration empties that file, whoever removes it
removes both rows in the same commit.

`flake.nix` rows are likewise unaffected: `yt-dlp` is one line in a file core
keeps.

### 1.11 Docs, ADRs, history

`none` to move. A search of `docs/` finds no current or historical document that
materially describes this domain; the only matches are in ADR 0002, which is a
framework decision and stays in core.

### 1.12 Activation effects

`none` of this domain's own. There is no `system.activationScripts` fragment for
these applications.

The applications are installed by `nix-darwin`'s Homebrew integration during
activation, which is core's generic machinery and not a footprint this domain
owns. That is an activation *dependency*, recorded in section 8, not an
activation fragment to move.

### 1.13 launchd agents and daemons

`none`. No label, schedule, working directory, environment, log path, or log
rotation entry belongs to this domain.

### 1.14 State roots

`none` declared. These applications create their own state under the user's
`Library` at runtime; core declares no state root, configuration root, report
root, or log root for them, and the destination module should not begin
declaring one as part of a migration whose claim is parity.

### 1.15 Skills

`none`. No skill in `docs/contrib/skills/` is owned by this domain.

### 1.16 Shell contributions

`none`. No static fragment, `PATH` addition, exported variable, or shell
initialisation is injected for this domain.

### 1.17 Allowlist keys

`none`. This domain reads no machine-local control-plane key.

### 1.18 System profile entries

`pkgs.yt-dlp` — the single entry, already recorded in 1.2. This is the item that
makes the removal commit and the destination registration a matched pair rather
than two independent changes.

### 1.19 Evaluation-time reads

`none`. No module in this domain reads a file out of an input while being
evaluated.

---

## 2. Destination module

```text
repository:             git+ssh://git@github.com/Knowledge-Forge-AI/entertainment-nd.git
qualified commit:       7401bbf2b23fe327a013147766370f00a374c440
handoffs added commit:  7fa89024de2c7f5bc8b15176019a225027557817
handoff-bearing head:   ee5c4e27f74b04045114ac3e80b1f508741ba750
handoffs removed commit: f1c78a7332dbd758b0ff5ea2296fd37d86d47fba
exports implemented:    darwinModules.default, applicationClaims,
                        packageSetIdentity, four standalone checks
exports deliberately omitted and why:
  overlays.default      the module owns no package; it consumes yt-dlp from
                        its binding nixpkgs package set
  packages / apps       no module-local build target, no operator entry point
  configAllowlist       reads no machine-local control state
  shellContributions    contributes no shell state
nixpkgs binding:        standalone qualification pins the accepted package set;
                        composition registration must bind it to core
packageSetIdentity:     exported
```

The qualified module adds exactly `pkgs.yt-dlp` to
`environment.systemPackages` and exactly the casks `discord`, `macgamestore`,
`openemu`, and `vlc`. Its four standalone checks cover the module delta,
application-claim contract, package-set contract, and inert generated
applications.

At the independently qualified commit, the four destination application claims
were ordinary owner-less claims with no `handoff` or `suite`. Commit
`7fa89024de2c7f5bc8b15176019a225027557817` added the four ADR 0003 handoffs.
The later handoff-bearing revision
`ee5c4e27f74b04045114ac3e80b1f508741ba750`, which both evaluated compositions
consumed during overlap, removed the obsolete per-claim `schema` field and its
corresponding legacy assertion while retaining all four handoffs. Independent
qualification remains distinct from the composition and parity evidence below.
Commit
`f1c78a7332dbd758b0ff5ea2296fd37d86d47fba` removed only the temporary
handoffs from the claims and retained the destination runtime declarations,
claim identities, package-set identity, and lock. The selected public cut
consumes that handoff-free destination together with the claim-free core
revision recorded in section 6.

---

## 3. Composition registration

```text
registry entry:         entertainment
registration commit:    520e51ac381de5cecb0e238d33dc5fe82d286058
canonical repo URL:     git+ssh://git@github.com/Knowledge-Forge-AI/entertainment-nd.git
deterministic checkout name: entertainment-nd
configuration initializer:  none
bootstrap strategy:     none
overlay position:       none required — the module exports no overlay
allowlist keys added:   none
moduleConfiguration:    none
```

The destination owns no configuration file, `configAllowlist`, initializer,
shell state, or overlay. ADR 0003 therefore requires a configuration-less
registry/bootstrap form rather than a fake entertainment configuration surface.
That form must preserve existing `repo-tool` and `copy-default` behavior. A
no-config entry clones and validates its checkout, creates no config file,
requires no `configAllowlist`, and contributes no `moduleConfiguration`; it
still participates in application claims, package-set identity, module import,
registry uniqueness, and bootstrap validation.

Registration exists at
`520e51ac381de5cecb0e238d33dc5fe82d286058`. The entry uses
`bootstrap.strategy = none`; it has no entertainment config file, initializer,
allowlist key, or `moduleConfiguration` surface. The same accepted change added
the configuration-less bootstrap support while preserving the existing
configured strategies. The candidate's same-revision pre/post registration
probe is therefore used alongside the historical parent/candidate comparison in
section 5 to isolate entertainment from that registry refactor.

---

## 4. Application claims

| Application | `id` | `artifact` | `provider` | `package` | Pre-cut source declaration |
|---|---|---|---|---|---|
| Discord | `bundle:com.hnc.Discord` | `Discord.app` | `homebrew-cask` | `discord` | `modules/desktop-apps.nix:17` |
| MacGameStore | `bundle:com.macgamestore.Client2` | `MacGameStore.app` | `homebrew-cask` | `macgamestore` | `modules/desktop-apps.nix:23` |
| OpenEmu | `bundle:org.openemu.OpenEmu` | `OpenEmu.app` | `homebrew-cask` | `openemu` | `modules/desktop-apps.nix:26` |
| VLC | `bundle:org.videolan.vlc` | `VLC.app` | `homebrew-cask` | `vlc` | `modules/desktop-apps.nix:27` |

Each bundle identifier was read from the installed application on the live host
on 2026-08-17, not inferred from a cask token or a display name.

`yt-dlp` is command-line only and is therefore **excluded** from claims, per ADR
0002 §3.1. Excluded from claims is not excluded from the migration — it is still
the system profile entry in 1.18, and it is still the item most likely to break
core's build if the two commits are separated.

### Handoff history

```nix
handoff = {
  from = "core";
  adr = "docs/adr/2026/08/0003-entertainment-nd-migration.md";
};
```

| `id` | `from` | Added in commit | Removed in commit |
|---|---|---|---|
| `bundle:com.hnc.Discord` | `core` | `7fa89024de2c7f5bc8b15176019a225027557817` | `f1c78a7332dbd758b0ff5ea2296fd37d86d47fba` |
| `bundle:com.macgamestore.Client2` | `core` | `7fa89024de2c7f5bc8b15176019a225027557817` | `f1c78a7332dbd758b0ff5ea2296fd37d86d47fba` |
| `bundle:org.openemu.OpenEmu` | `core` | `7fa89024de2c7f5bc8b15176019a225027557817` | `f1c78a7332dbd758b0ff5ea2296fd37d86d47fba` |
| `bundle:org.videolan.vlc` | `core` | `7fa89024de2c7f5bc8b15176019a225027557817` | `f1c78a7332dbd758b0ff5ea2296fd37d86d47fba` |

All four handoffs were temporary overlap authority. They were added before
registration and removed at `f1c78a7332dbd758b0ff5ea2296fd37d86d47fba`.
The destination's exact-claim checks were amended with the handoff addition and
then repaired for the current schema at `ee5c4e2`. The public cut selected that
handoff-free destination together with core revision `332afc0b...`, which had
removed the four source claims and runtime declarations.

---

## 5. Parity

Parity is proved while core still declares all four casks and `yt-dlp`.
BASELINE is the first parent of the accepted registration commit,
`770af521eedf8fc1d3ea51048542a9d42fea6627`; OVERLAP CANDIDATE is the
registration commit, `520e51ac381de5cecb0e238d33dc5fe82d286058`.
After removing only the new entertainment lock node and root reference, their
remaining lock graphs match exactly: core, nixpkgs, nix-darwin, and both
pre-existing registered modules retain the same revisions.

Both exposed configurations, `example-workstation` and `example-primary`, were
compared across normalized package derivation identities; normalized Homebrew
taps, brews, casks, MAS apps, and Brewfile declarations; complete effective
launchd and log-rotation projections; activation text and ordering; shell
state; option leaves and assertions; and outside-store/state/config contracts.
Every required semantic surface is `MATCH`.

The overlap mechanics are explicit and non-semantic: raw `yt-dlp` multiplicity
is one to two; each of the four casks and its Brewfile line is one to two; the
normalized sets remain equal. The candidate commit's own
`preEntertainmentProbe`/`compositionProbe` pair reproduces these deltas at one
revision for one exposed host while every semantic surface, including
activation, remains `MATCH`. The two distinct `system-applications` paths used
by that activation comparison have the same NAR hash. The state- and
configuration-root result is derived rather than directly projected: section
1.14 declares neither root, while activation, option-leaf paths, and
outside-store contracts all match. The comparison artifact records this basis
and its projection limitation explicitly.

### Expected differences, enumerated in advance

| Difference | Why it is expected |
|---|---|
| store path of the built system | relocating any source changes derivation inputs even when observable configuration is identical |
| ordering within `homebrew.casks` | the composed list is assembled from two modules instead of one; the effective set must be equal, the order need not be |
| store path of `system-applications` | registration changes its derived path; the same-revision probe proves the before and after paths have an identical NAR hash |

Anything outside this table blocks the parity claim. In particular, a difference
in the normalized system package set means `yt-dlp` was dropped rather than
moved, and that is a failure, not a row to add here afterwards.

```text
baseline artifact:   nix-darwin/docs/architecture/domain-migrations/evidence/entertainment-nd/parity-baseline.json
candidate artifact:  nix-darwin/docs/architecture/domain-migrations/evidence/entertainment-nd/parity-overlap.json
comparison artifact: nix-darwin/docs/architecture/domain-migrations/evidence/entertainment-nd/parity-comparison.json
result:              MATCH WITH ENUMERATED DIFFERENCES
```

### Ownership-cut staging

The completed matched-cut qualification used these staging candidates:

```text
core candidate:         332afc0b5dd6c7eeef272628f3feea2ffa0747a8
destination candidate:  f1c78a7332dbd758b0ff5ea2296fd37d86d47fba
matched new/new result: VALID, 40 claims, zero validation errors
new core + old destination: INVALID, four singleton-handoff errors
old core + new destination: INVALID, four duplicate-claim errors
```

Each invalid half-cut contained exactly the four entertainment identities and
no unrelated validation rule. The same qualification proved the matched
new/new runtime semantically equivalent to the overlap runtime after duplicate
multiplicity was collapsed: `yt-dlp` and each of the four casks remained once,
with no unrelated runtime-surface change.

After qualification, both source defaults were fast-forwarded in one bounded
operator action while the selected composition remained lock-pinned to the old
valid pair. Those default-branch ref updates published the qualified halves;
they were not the cut. Public composition revision
`fac9adb7b8fbb89ef063e44c3b7ff58ff3baf055` then advanced both inputs together
and is the actual selected `CUT` boundary.

---

## 6. Core removal

Completed at core commit
`332afc0b5dd6c7eeef272628f3feea2ffa0747a8`:

```text
[x] flake.nix — pkgs.yt-dlp removed from environment.systemPackages
[x] modules/desktop-apps.nix — the four cask lines removed; the file and its
    import retained in flake.nix for the applications that stay
[x] darwin-configuration.int.test.nix — "yt-dlp" removed from
    mediaAndNetworkTools; ytDlpAbsent assertion added; the other five entries
    retained
[x] darwin-configuration.int.test.nix — "discord", "macgamestore", "openemu",
    "vlc" removed from the cask list; the other seventeen retained
[x] application-claims.int.test.nix — testEntertainmentMigrationTargets...
    removed; core claim count adjusted (39 -> 35); detectsRemovedDeclaration
    sentinel fixture switched from "vlc" to "gimp"; ytDlpIsCoreOwned and
    ytDlpHasNoClaim assertions removed
[x] core's application claims for the four ids removed, and the four destination
    handoff blocks deleted in the same matched migration boundary
[x] no stub, alias, or retained host-table key left behind
[x] no live/runtime ownership declaration for this domain remains in core
```

The removal split the mixed files rather than moving them.
`modules/desktop-apps.nix`, the mixed integration-test attributes in
`darwin-configuration.int.test.nix` and `application-claims.int.test.nix`,
unrelated neighboring package/cask declarations, and their assertions survive.
No coverage-manifest row was removed because `flake.nix` and
`modules/desktop-apps.nix` survive. No option, host-table key, allowlist key,
launchd entry, or activation fragment was removed because section 1 records
none.

Current tracked-tree inspection confirms that core no longer declares
`pkgs.yt-dlp`, any of the four entertainment casks, or any of the four bundle
claims. It also confirms that neighboring declarations remain and that no
compatibility stub was introduced. The final checklist line is deliberately
about live/runtime ownership: ADRs, this migration record, validator fixtures,
and the command-line-claim exclusion comment intentionally retain historical
references to the domain and `yt-dlp`.

Qualification evidence:

```text
[x] core evaluates and builds with the entertainment-nd checkout physically
    absent
[x] core's checks pass
[x] aggregate coverage per language and metric is above threshold after removal
[x] the public-capable composition builds with the module registered
```

The core candidate phase archive records a clean tracked-file export with no
destination checkout or composition sibling, a passing flake check, and both
exposed Darwin builds. Its authoritative candidate-worktree run records focused
tests, `nix-int-test --jobs 4`, `nix-test --report --jobs 4` including all hard
coverage gates, `nix flake check --no-update-lock-file`, and both builds. The
selected-public-cut archive records the public canonical tests, flake check,
and both selected Darwin builds at `fac9adb7...`; section 10 identifies both
archives and their reviews.

---

## 7. Integrate

The public and private selected boundaries are distinct:

```text
public CUT:         composition-nd
                    fac9adb7b8fbb89ef063e44c3b7ff58ff3baf055
private INTEGRATED: composition-private-nd
                    21c6bd6700e8f6195165d0471cf40ccba8a544aa
```

At the public cut, binding whole-set validation accepted 40 claims: 35 from
core, one from `vscode-nd`, and four singleton claims owned by `entertainment`.
No duplicate identity or handoff metadata remained. The public canonical tests,
flake check, and both Darwin builds passed. Normalized runtime comparison was
equivalent to the overlap state after collapsing duplicate multiplicity:
`yt-dlp` and each entertainment cask remained exactly once, and no unrelated
runtime surface changed.

The private selected revision advanced only its public `composition-nd` root
input. Its resolved lock graph followed the public cut's new core and
destination nodes, while unrelated owner inputs did not drift. The complete
private aggregate contains the public 40-claim list as a strict prefix plus the
three singleton `security-nd` claims. Whole-aggregate validation, all 17
canonical private tests, `nix flake check`, and both private Darwin builds
passed.

Template integration gates reconciled against the selected states:

```text
[x] registry names are unique across public and private registered modules
[x] overlay order is preserved; entertainment exports no overlay
[x] allowlist keys do not collide; entertainment contributes no allowlist key
[x] package-set identity is equal across every layer
[x] application claims are unique with zero entertainment handoffs outstanding
[x] mixed test files retain the union of the surviving core assertions
[x] aggregate coverage gates passed on the post-removal core tree
[x] core built with the destination checkout physically absent
[x] the selected public composition built both exposed Darwin systems
[x] the selected private composition built both exposed Darwin systems
[x] public and private lock updates were input-scoped with no unrelated owner
    input drift
```

The public selected lock pins core `332afc0b...` and destination `f1c78a7...`
together. Live evaluation of that selected checkout reproduces 40 claims, four
singleton entertainment owners, zero duplicates, and zero handoffs. The
private selected lock pins public composition `fac9adb7...`; live evaluation
reproduces 43 claims, the same four singleton entertainment owners, the three
singleton security owners, zero duplicates, and zero handoffs. The phase
archives identified in section 10 retain the build, runtime-projection, lock,
and two-review evidence for these gates.

---

## 8. Activation requirement and residual risk

```text
activation required:   YES
justification:         all four applications are installed by Homebrew during
                       activation. A build proves the cask is declared in the
                       composed configuration; it cannot prove the cask
                       installs, that the installed bundle identifier matches
                       the claim, or that moving the declaration between modules
                       does not cause Homebrew to uninstall and reinstall the
                       application and lose its local state.
authorization:         NOT AUTHORIZED
```

The former cut-stage risks are resolved for this selected cut. Public runtime
projection and both selected builds prove that `yt-dlp` remained present once,
and the integrated core/public/private qualification proves that the concurrent
mixed-file edits retained their required assertions and passed the merged
gates. They remain recorded as cut evidence in sections 6 and 7 rather than as
future mitigations.

### Residual risks

| Risk | Why build parity cannot cover it | Mitigation or acceptance |
|---|---|---|
| Homebrew uninstalls and reinstalls a cask when its declaring module changes | activation-only behaviour; the built configuration is identical either way | verify on a non-primary machine, or accept and record that application-local state may be reset |
| an installed bundle identifier differs from the claim | claims are validated against declared data, never against what is on disk | identifiers in section 4 were read from the live host; re-read before activation and verify again after activation, since a vendor can change one in an update |

---

## 9. History and docs disposition

```text
documents moved to the destination repository:  none — section 1.11 found none
documents moved to private archival evidence:   none
documents deliberately left in core, and why:
  ADR 0002 — a framework decision about ownership and claims, not domain
  guidance. It names this domain as an example and stays in core.
  This inventory — the template's own instruction is that the inventory is the
  checklist the removal commit is verified against, so it lives where the
  removal happens until the migration is COMPLETE.
```

---

## 10. Evidence

```text
[x] migration decision record: docs/adr/2026/08/0003-entertainment-nd-migration.md
[x] destination qualification: 7401bbf2b23fe327a013147766370f00a374c440
[x] destination handoffs: 7fa89024de2c7f5bc8b15176019a225027557817,
    with handoff-bearing schema revision ee5c4e27f74b04045114ac3e80b1f508741ba750
[x] public registration/parity: 520e51ac381de5cecb0e238d33dc5fe82d286058
[x] canonical baseline, overlap, and comparison parity artifacts retained
[x] core cut: 332afc0b5dd6c7eeef272628f3feea2ffa0747a8
[x] destination cut: f1c78a7332dbd758b0ff5ea2296fd37d86d47fba
[x] matched-pair and both invalid half-cut controls qualified
[x] public CUT: fac9adb7b8fbb89ef063e44c3b7ff58ff3baf055
[x] private INTEGRATED: 21c6bd6700e8f6195165d0471cf40ccba8a544aa
[x] Git objects and selected lock relationships verified in all four repositories
[x] dispatcher operational records and two-review evidence retained
[ ] separate supplemental review of the core post-final-review absence guard:
    no such artifact exists; later selected public and private reviews verified
    the integrated runtime and ownership result
[ ] publication-readiness handoff: outstanding for COMPLETE; this program has
    no separate publication-readiness artifact at INTEGRATED
[ ] activation: required, separately authorized, and still outstanding
```

The dispatcher archives provide the operational and two-review records without
making this tracked document depend on an untracked scratch filename:

- `ND-ENT-PARITY--20260820T182225047826Z.zip` retains the canonical parity
  phase record and its two reviews.
- `ND-ENT-CORE-CANDIDATE--20260820T202914523544Z.zip` and
  `ND-ENT-DEST-CANDIDATE--20260820T202958491390Z.zip` each record two reviews,
  standalone candidate qualification, and the committed cut identity.
- `ND-ENT-MATCHED-QUAL--20260820T212542777671Z.zip` records the matched
  new/new pass, both exact half-cut rejection classes, and two reviews.
- `ND-ENT-SELECT-CUT--20260820T224643784485Z.zip` records two reviews, the
  input-scoped public lock selection, 24 canonical tests, flake check, both
  public Darwin builds, and normalized runtime equivalence.
- `ND-ENT-PRIVATE-SELECT-CUT--20260820T235431285031Z.zip` records two reviews,
  the input-scoped private selection, public-prefix and private-claim gates, 17
  canonical tests, flake check, and both private Darwin builds.

The tracked Git objects and current selected locks independently reproduce the
four source/destination/public/private boundary identities. Activation was not
performed by any of these phases and remains the next separately authorized
boundary. Publication readiness and `COMPLETE` are not claimed.

---

## Appendix: adjacent items deliberately not claimed

Recorded so the next owner does not have to re-derive why they are absent.

| Item | Where | Why not claimed here |
|---|---|---|
| `pkgs.qbittorrent` | `modules/desktop-apps.nix:7` | a general-purpose transfer client, not assigned to any domain in ADR 0002 §2. It needs an owner decision before it can be claimed, and guessing one here would put an unowned surface into a migration whose parity claim depends on the inventory being exact. |
| `pkgs.audacity` | `modules/desktop-apps.nix:5` | ADR 0002 §2 assigns it to the planned `developer-ux-nd`, not here. |
| `pkgs.imagemagick`, `pkgs.rclone` | `flake.nix` | general-purpose tooling with consumers elsewhere in core. |

### `yt-dlp` disposition

`yt-dlp` belongs to `entertainment-nd` on evidence, not on taxonomy. A search of
the pre-cut tracked tree found four live ownership and contract references in
core — the declaration at `flake.nix:221`, the package assertion at
`darwin-configuration.int.test.nix:292`, and the ownership/claim-exclusion
assertions at `application-claims.int.test.nix:235-236`. No `bin/` command,
`libexec/` helper, `tools/` script, test harness, or autonomous-agent
infrastructure invoked it. The cut removed those live core references, and the
current integration test now asserts that `yt-dlp` is absent from core
(`ytDlpAbsent`). Historical documentation, validator fixtures, and the
command-line-claim exclusion comment remain by design; none is a runtime
declaration or compatibility stub.
