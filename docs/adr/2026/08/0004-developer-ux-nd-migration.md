# ADR 0004 — `developer-ux-nd` Ownership Migration

Status: Accepted

Date: 2026-08-21

Governing decisions:
[ADR 0001](0001-nix-darwin-core-subflake-extension-contract-and-owner-driven-migration.md),
[ADR 0002](0002-domain-ownership-application-claims-and-migration-sequencing.md),
and the matched-cut reference in
[ADR 0003](0003-entertainment-nd-migration.md).

Migration owner: `operator`

## Context

ADR 0002 assigns Applite, Bruno, DBeaver, Zenmap, GIMP, Audacity, and Petdex
to `developer-ux-nd`. The tracked core footprint is broader than those package
list entries: it includes two Petdex package definitions and their overlay and
flake outputs, a GIMP configuration activation, seven application claims,
mixed tests, six coverage-manifest rows, and Petdex-specific state migration
embedded inside core's otherwise generic Codex state activation. The seventh
claim is the `chatgpt` cask's `ChatGPT.app`, whose bundle identifier is
`com.openai.codex`; it is existing core footprint in `modules/codex.nix`, not a
new feature.

OpenAI's current desktop product combines Chat and Work with Codex in the
ChatGPT desktop application. The operator assigns that indivisible application
to `developer-ux-nd` because Codex is the developer-facing reason for its
workstation installation. The sibling `codex` cask remains the core-owned
Codex CLI. The old standalone `codex-app` cask is deprecated in favor of
`chatgpt` and is not part of this migration.

The operator also assigns the Google Antigravity command center, Google
Antigravity IDE, and Anthropic Claude desktop GUI to `developer-ux-nd`. Those
three applications are new desired declarative footprint, not declarations
currently migrating out of core. Core keeps the distinct `antigravity-cli`
cask and its `agy` command, the Codex CLI, and Claude Code. Mixing the three new
GUI applications into the overlap would make semantic additions look like
parity differences. Adding them temporarily to core would also violate ADR
0001's `NO NEW PROJECT-SPECIFIC OPTIONAL FOOTPRINT IN CORE` rule. The ownership
transfer and the new destination-only feature therefore have separate selected
boundaries.

The entertainment migration has already reached `INTEGRATED` without host
activation. Developer UX must preserve that state, reach its own integrated
boundary without activation, and then participate in one later jointly
authorized activation whose evidence is attributed by domain.

## Decision

### 1. Ownership, destination, and publication class

The operator is accountable for the migration. The destination is:

```text
repository:            git+ssh://git@github.com/Knowledge-Forge-AI/developer-ux-nd.git
publication class:     PUBLIC-CAPABLE
composition registry:  developer-ux
deterministic checkout: developer-ux-nd
```

The repository does not exist yet. This decision neither creates it nor
authorizes publication, registration, lock movement, installation, or host
activation.

`developer-ux-nd` owns human-facing development product and UX footprint. Core
keeps generic toolchains and autonomous-agent primitives. Applied to the AI
application families, that means:

```text
developer-ux-nd:  chatgpt cask, ChatGPT.app/current Codex GUI
                  claude cask, Claude.app
                  antigravity cask, Antigravity.app
                  antigravity-ide cask, Antigravity IDE.app
core:             codex cask and Codex CLI
                  pkgs.claude-code and Claude Code CLI
                  antigravity-cli cask and agy command
excluded:         deprecated codex-app cask and Codex.app
```

The `antigravity-ide` cask also exposes `antigravity-ide` and `agy-ide`
launchers from its application bundle. They remain destination-owned adjuncts
and do not replace or become authority for core's `agy` from
`antigravity-cli`.

### 2. Exact existing footprint to migrate

The existing runtime footprint moving from core is:

| Product | Current declaration | Provider surface |
|---|---|---|
| Applite | `modules/brewfile-stragglers.nix` | Homebrew cask `applite` |
| Bruno | `modules/desktop-apps.nix` | Nix package `pkgs.bruno` |
| DBeaver | `modules/developer-tooling.nix` | Nix package `pkgs.dbeaver-bin` |
| Zenmap | `modules/desktop-apps.nix` | Nix package `pkgs.zenmap` |
| GIMP | `modules/desktop-apps.nix`, `modules/gimp.nix`, `config/gimp/gimprc-help-online` | Homebrew cask `gimp` plus managed user configuration |
| Audacity | `modules/desktop-apps.nix` | Nix package `pkgs.audacity` |
| Petdex | `packages/petdex-cli`, `packages/petdex-desktop`, `flake.nix`, `modules/codex.nix`, and the Petdex portion of `modules/codex-state.nix` | Nix packages `pkgs.petdex` and `pkgs.petdex-desktop` plus product state activation |
| ChatGPT/current Codex desktop | `modules/codex.nix` | Homebrew cask `chatgpt` |

The seven current application claims are:

| Application | Identity | Artifact | Provider | Package |
|---|---|---|---|---|
| Applite | `bundle:dev.aerolite.Applite` | `Applite.app` | `homebrew-cask` | `applite` |
| Bruno | `bundle:com.usebruno.app` | `Bruno.app` | `nix` | `bruno` |
| DBeaver | `bundle:org.jkiss.dbeaver.core.product` | `dbeaver.app` | `nix` | `dbeaver-bin` |
| GIMP | `bundle:org.gimp.gimp` | `GIMP.app` | `homebrew-cask` | `gimp` |
| Audacity | `bundle:org.audacityteam.audacity` | `Audacity.app` | `nix` | `audacity` |
| Petdex | `bundle:run.crafter.petdex-desktop` | `Petdex.app` | `nix` | `petdex-desktop` |
| ChatGPT/current Codex desktop | `bundle:com.openai.codex` | `ChatGPT.app` | `homebrew-cask` | `chatgpt` |

The identifiers were re-read from the installed bundles on 2026-08-21 and
match core's tracked inventory. Zenmap is a GUI frontend, but the selected
`pkgs.zenmap` output currently contributes no macOS `.app` bundle. It therefore
has no application claim. Standalone destination qualification must preserve
that bundle-based result; if the selected package later begins producing an
application bundle, qualification stops until an exact claim is added.

No adjacent declaration in the mixed source modules transfers by proximity.
The detailed inventory names every retained neighbor.

### 3. Petdex is one product slice

Petdex is not split into a core CLI dependency and a destination GUI merely
because both currently sit beside Codex configuration. Tracked-source searches
found no core command, helper, skill, MCP integration, or autonomous-agent
workflow that consumes `petdex`, `petdex-desktop`, their copied JavaScript, the
desktop binary link, or the sidecar link. They are one human-facing companion
product.

The destination therefore takes:

- both package definitions;
- overlay attributes and `packages.<system>` outputs for `petdex` and
  `petdex-desktop`;
- both system-profile entries;
- the `Petdex.app` claim;
- the existing `~/.petdex` migration and compatibility link;
- the persisted CLI JavaScript, desktop binary link, and sidecar link; and
- Petdex-specific tests and coverage ownership.

Core's mixed `modules/codex-state.nix` is split. Core retains the Codex docs and
Serena state behavior; the destination owns the Petdex behavior. To preserve
migration parity, Petdex state remains at `~/.codex/apps/petdex`, with
`~/.petdex` pointing to it. The destination must idempotently create both the
generic parent and its owned leaf before use. It must not depend on core's
activation ordering or on core having created `~/.codex/apps` first. Keeping the
path avoids a behavioral state relocation during an ownership migration while
still making the Petdex leaf and all writes to it destination-owned.

This decision creates no dependency from core back into `developer-ux-nd`.

### 4. Destination contract and configuration-less bootstrap

The destination is expected to implement only the outputs its live footprint
requires:

```text
darwinModules.default
overlays.default
packages.<system>.petdex
packages.<system>.petdex-desktop
applicationClaims
packageSetIdentity
checks.<system>.*
```

It follows the composition's `nixpkgs`; its package-set identity is binding.
It owns no machine-local control-plane key, configuration initializer,
`configAllowlist`, shell contribution, launchd service, or operator tool.

The tracked GIMP help template is internal module configuration, and the fixed
Petdex path contract is module behavior. Both moving activation slices (GIMP
help setup in `modules/gimp.nix` and Petdex state in `modules/codex-state.nix`)
require the target user account identity (`userName`) to resolve home paths and
file ownership (`/Users/${userName}`). In core, these modules were curried
functions (`{ userName }: { ... }:`). The destination exports a standard
`darwinModules.default` without curried import parameters, resolving the target
user generically from standard nix-darwin options (`config.system.primaryUser`
and `config.users.users.<name>.home`) supplied by the host environment. Because
this uses standard module configuration options rather than machine-local
control-plane keys or domain initializers, it introduces no operator-specific
coupling and registration uses the already-qualified configuration-less form:

```json
"bootstrap": { "strategy": "none" }
```

The destination must still qualify standalone before registration. At minimum,
qualification builds both Petdex packages, evaluates the exact module delta and
claims, proves Zenmap's selected output has no application bundle, checks
package-set identity, exercises GIMP and Petdex activation text and state-path
contracts (including user-generic resolution), and proves the destination
contains only the existing migrated footprint, including `chatgpt` and
excluding `antigravity`, `antigravity-ide`, and `claude`.

### 5. Overlap, parity, and the matched ownership cut

Before registration, each of the seven destination claims in section 2 gains:

```nix
handoff = {
  from = "core";
  adr = "docs/adr/2026/08/0004-developer-ux-nd-migration.md";
};
```

The handoff change must amend the destination's standalone claim checks in the
same commit so those checks continue to describe the exported claims exactly.

Zenmap has no claim and therefore no handoff. Petdex's CLI package has no
separate claim; its handoff is the `Petdex.app` claim above.

Parity is proved while core still supplies the complete existing footprint.
The canonical baseline and overlap artifacts must cover normalized system
packages, Homebrew state, application claims, activation text, the GIMP
configuration contract, the Petdex state and compatibility paths, owner/mode
contracts, options and assertions, launchd and log rotation even when empty,
shell state, and declared configuration/state roots. Store paths and source
paths may differ when enumerated in advance; a runtime application, package,
configuration, or state-path difference is not parity.

The Antigravity command center, Antigravity IDE, and Claude desktop are absent
from both parity sides. Their absence is an invariant, not an expected
difference.

The ownership cut follows ADR 0003's proven choreography:

1. Prepare separate core and destination candidates on non-default staging
   branches.
2. Core removes exactly the existing footprint in section 2, including mixed
   test assertions and the seven source claims. The destination cut candidate
   atomically makes two coordinated changes: removing the seven temporary
   handoff blocks and re-enabling both the GIMP and Petdex activation module
   imports in `darwinModules.default` (refined in Section 5A).
3. A clean scratch composition proves the matched new/new pair passes.
4. Old core plus the handoff-free destination must reject with seven
   `duplicate-claim` errors.
5. Claim-free core plus the old handoff-bearing destination must reject with
   seven `singleton-handoff` errors.
6. Only the selected public composition revision that consumes both qualified
   new halves is `CUT`. Candidate commits and default-branch ref updates are
   preparation, not the cut.

No selected composition may consume one half of the cut. No compatibility stub,
permanent handoff, or duplicate owner remains afterward.

### 5A. Amendment: single-writer overlap activation strategy

This amendment records an execution refinement discovered after the original
decision and does not rewrite the earlier qualification or parity plan as if
the issue had been known in advance. The DEVUX5 canonical comparison between
public baseline `fac9adb7b8fbb89ef063e44c3b7ff58ff3baf055` and registered
overlap `a87c03c2a36fb544868aa1f6c95581ec383338db`, with common core
`332afc0b5dd6c7eeef272628f3feea2ffa0747a8` and destination
`417aff05dbb3330dc961d75115145983020423e6`, was also selected by the
top-level overlap at `60653021a4249af76acb0dd45d12901b887ce30b` and returned
`BLOCKED`. Packages, effective Homebrew state, seven claim payloads, Petdex
derivations, and the
other projected contracts matched, but the selected overlap changed both the
GIMP and Petdex writer counts from one to two and activation definitions from
44 to 46. No activation occurred from that state.

The migration therefore adopts this binding invariant:

> At every valid selected migration state, exactly one owner executes each
> migrating GIMP or Petdex activation side effect.

The invariant is implemented by a source-authoritative overlap, with four
causally distinct states:

| State | Destination state | Selected writer authority | Disposition |
|---|---|---|---|
| A — independently qualified destination | `14e95ce3cc156292b3ff79df3e83570cd6c8dcea` contains and imports the full GIMP and Petdex activation implementations without handoffs | destination when qualified independently | retained implementation-qualification evidence |
| B — rejected registered overlap | destination `417aff05dbb3330dc961d75115145983020423e6` carries all seven handoffs and imports both activation modules; public `a87c03c2a36fb544868aa1f6c95581ec383338db` also retains the core writers | core and destination both write | valid claim-handoff registration, but not parity-qualified; immutable DEVUX5 evidence records the defect |
| C — remediated handoff overlap | all six packages, all three casks, both Petdex package/overlay outputs, all seven exact handoffs, the GIMP template, and both activation module source files remain; `darwinModules.default` does not import the two activation modules | core alone writes | planned transient overlap for a canonical parity rerun |
| D — matched ownership cut | the destination cut candidate removes all seven handoffs and re-enables both activation imports in the same candidate revision; the core cut candidate removes the corresponding declarations, claims, sources, writers, tests, and coverage | destination alone writes | selected new-core plus new-destination public revision is the `CUT` boundary |

State A continues to qualify the full intended destination implementation.
During State C, focused destination checks import the retained GIMP and Petdex
module files directly and exercise their complete intended cut-time behavior,
including generic primary-user resolution. A destination-local evaluated test
further proves that `darwinModules.default` contributes zero GIMP or Petdex
activation fragments during State C. This direct test coverage and evaluated
check, together with the preserved State A evidence, refines section 4's
qualification rule and section 5 step 2 for the dormant overlap state. It does
not require an extra public flake export such as `darwinModules.full`, and it
does not permit the implementations or their focused tests to be weakened
merely because `darwinModules.default` temporarily omits them.

State C is deliberately temporary and destination-local. It adds no
machine-local configuration key, `configAllowlist`, `moduleConfiguration`,
registry/bootstrap variant, composition special case, generalized optional
module framework, core reverse dependency, fallback writer, or public gating
option. Core remains the sole selected writer for the GIMP managed-help block
and for Petdex migration, compatibility link, payload copies, and links.

The whole-set claim validator remains the binding fail-closed control for the
two legitimate half-cut pairings:

| Invalid pairing | Required result |
|---|---|
| old core plus destination cut candidate | reject with seven `duplicate-claim` errors because the destination handoffs are gone while the source claims remain; the pair may also contain two writers, but validation rejects it before selection or activation |
| new core plus remediated overlap destination | reject with seven `singleton-handoff` errors because the source claims are gone while the destination handoffs remain; it has no migrating writer, but validation rejects it before selection or activation |

The validator is not a writer-presence validator. A malformed destination that
both removed its handoffs and left its activation imports dormant could pair
with new core without a claim error while supplying zero migrating writers.
That state is not a qualified cut candidate. The destination cut candidate is
defined atomically by both handoff removal and writer re-enablement, and the
matched-cut gate must independently prove exactly one destination-owned GIMP
writer and exactly one destination-owned Petdex writer before public selection.
No fallback or stub makes an invalid or malformed half-state runnable.

After State C is implemented and independently requalified, the public
composition advances only `developer-ux-nd` while core and every other owner
remain fixed, and the selected top-level composition advances only that public
selection. Canonical parity is then rerun against the same clean
pre-registration baseline. The existing `parity-baseline.json` may be reused
only when the remediated candidate retains the identical common core and all
unrelated inputs represented by that artifact. If repository movement makes
that boundary unavailable, the rerun must add
`parity-remediated-baseline.json` from a clean baseline with exactly the same
core and unrelated inputs as the remediated candidate; it must not compare
different core states or overwrite the historical baseline. The remediated
candidate and comparison are recorded separately as
`parity-remediated-overlap.json` and
`parity-remediated-comparison.json`.

The rerun must retain the frozen expected-difference categories from section 5
and prove, for both exposed hosts, one GIMP writer before and after, one Petdex
writer before and after, and no unrelated activation change. Duplicate
activation is not added to expected differences after observation. Note that
advancing to `PARITY` at this rerun proves that the non-activation footprint is
neutral and that core remains sole writer without duplicate activation;
semantic activation equivalence between the destination and core
implementations is evaluated at the subsequent matched-cut semantic gate. Only a
matching remediated comparison may advance the migration to `PARITY`; the three
original DEVUX5 artifacts remain the immutable evidence for rejected State B.

At matched-cut qualification, writer count alone is insufficient because the
destination implementations use generic primary-user resolution and differ
structurally from core. The parity-qualified State C pair, with core as sole
writer, must be compared with the new/new cut pair, with destination as sole
writer, for both exposed hosts. The binding semantic gate proves:

- GIMP resolves the same primary user and home, targets the same `gimprc`, uses
  the same managed markers and template bytes, preserves surrounding user
  content, preserves ownership and mode behavior, has exactly one writer on
  each side, and introduces no unrelated activation change;
- Petdex retains `~/.codex/apps/petdex`, its `bin` and `sidecar` leaves, the
  `~/.petdex` compatibility behavior, conflict-backup semantics, copied CLI
  JavaScript, desktop and sidecar links, ownership and modes, has no dependency
  on core activation ordering after cut, has exactly one writer on each side,
  and introduces no unrelated state-root change; and
- structural or textual differences are accepted only where the semantic
  projection or a safe scratch-home rehearsal proves the observable contract
  equal without touching the live user profile.

The following alternatives are rejected unless later evidence establishes a
strictly simpler mechanism with the same single-writer and fail-closed
properties:

1. Two allegedly idempotent writers change side-effect count and sequence and
   violate the retained frozen parity contract.
2. Adding duplicate activation to expected differences after DEVUX5 would
   weaken the proof post hoc.
3. Composition-specific activation gating would couple a generic composition
   to one migration's internal staging when destination-local staging suffices.
4. A machine-local flag or allowlist entry would contradict the accepted
   configuration-less contract and add operator state solely for choreography.
5. A permanent shared core implementation would undermine destination
   ownership and risk a reverse dependency or stub surviving the cut.
6. A generalized module-selection registry would add unnecessary architecture
   for two temporary side-effect slices.

This refinement does not change section 6's post-CUT Antigravity and Claude GUI
sequence, section 7's integration boundary, or the later joint
entertainment-plus-developer-UX activation plan.

### 6. Post-CUT AI GUI feature and host adoption boundary

Only after `CUT`, one separate destination-only feature adds three ordinary
Homebrew casks and claims directly to `developer-ux-nd`:

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

They have no `handoff`, because core never declaratively owned them. The
feature does not add deprecated `codex-app`, move `antigravity-cli`, replace
core's `agy`, move the Codex CLI, or move Claude Code.

Current provider metadata distinguishes the surfaces:

- the Homebrew [`antigravity` cask](https://formulae.brew.sh/cask/antigravity)
  installs `Antigravity.app` and uses `com.google.antigravity` as its quit
  identity; Google's
  [Antigravity 2.0 overview](https://antigravity.google/docs/overview)
  identifies it as a standalone command center independent of an IDE;
- the Homebrew
  [`antigravity-ide` cask](https://formulae.brew.sh/cask/antigravity-ide)
  installs `Antigravity IDE.app`, uses `com.google.antigravity-ide` as its quit
  identity, and exposes application-bundled `antigravity-ide` and `agy-ide`
  launchers; Google's
  [Antigravity IDE overview](https://antigravity.google/docs/ide/overview/)
  identifies it as the separate integrated development environment;
- the Homebrew [`claude` cask](https://formulae.brew.sh/cask/claude) installs
  `Claude.app` and uses `com.anthropic.claudefordesktop` as its primary quit
  identity;
- the Homebrew [`chatgpt` cask](https://formulae.brew.sh/cask/chatgpt) installs
  `ChatGPT.app`; official
  [OpenAI desktop documentation](https://learn.chatgpt.com/docs/app) describes
  choosing Chat, Work, or Codex in the combined current product; and
- the old Homebrew
  [`codex-app` cask](https://formulae.brew.sh/cask/codex-app) is deprecated and
  names `chatgpt` as its replacement.

The Antigravity applications are not installed on the host as of 2026-08-21.
Both Antigravity bundle identities above are therefore provisional provider
metadata, not live `Info.plist` evidence. First installation and the later
post-activation check must read `CFBundleIdentifier` from each installed bundle
before treating either claim as live-verified. Claude's identifier was read
from the installed bundle and is live-verified for this inventory date.

The read-only host audit found:

| Surface | Host artifact | Homebrew registration | Disposition |
|---|---|---|---|
| Antigravity command center | absent | `antigravity` not registered | post-CUT destination-only addition; provisional identity |
| Antigravity IDE | absent | `antigravity-ide` not registered | post-CUT destination-only addition; provisional identity |
| Claude desktop | `Claude.app`, `com.anthropic.claudefordesktop` | `claude` not registered | unmanaged pre-existing bundle; post-CUT declaration requires a pre-activation adoption disposition |
| ChatGPT/current Codex desktop | `ChatGPT.app`, `com.openai.codex` | `chatgpt` registered | existing core footprint; migrate with a normal handoff |
| legacy standalone Codex | absent | `codex-app` not registered | deprecated; do not add |
| ChatGPT Classic | `ChatGPT Classic.app`, `com.openai.chat` | no matching installed cask receipt | adjacent unmanaged bundle; deliberately unclaimed and not removed by this migration |
| Antigravity CLI | no application bundle | `antigravity-cli` registered | retained core primitive |
| Codex CLI | no application bundle | `codex` registered | retained core primitive |

No current core Homebrew configuration selects an adoption argument. Homebrew's
`--adopt` mode is explicit and applies only to identical destination artifacts;
ordinary activation must not be described as safely adopting the unmanaged
Claude bundle. Before the jointly authorized activation, the operator must
select and record a non-destructive disposition for that bundle and its user
state. If the chosen provider action would overwrite, remove, or fail on the
existing artifact, activation stops for explicit authorization rather than
guessing. `ChatGPT Classic.app` remains outside the selected declarative
footprint and must not be deleted as cleanup.

### 7. Public integration and joint activation

After the matched cut:

1. qualify the destination-only Antigravity command-center, Antigravity IDE,
   and Claude GUI feature;
2. advance the public composition to the post-cut destination revision that
   includes it;
3. advance the selected non-public top-level composition once to that final
   public state and run the integrated gates; and
4. mark the developer-ux migration `INTEGRATED`, without activating.

The entertainment migration remains `INTEGRATED` and unactivated throughout.
One later explicit operator authorization may activate the selected private
composition once, covering both domain migrations. The report must attribute
results separately.

Entertainment verification includes `yt-dlp` on `PATH`, Discord, MacGameStore,
OpenEmu, VLC, their live bundle identities/providers, and unexpected Homebrew
removal or reinstall behavior.

Developer UX verification includes Applite, Bruno, DBeaver, Zenmap if its
selected package then produces an application bundle, GIMP, Audacity, Petdex
product/state/deep-link surfaces, and ChatGPT/current Codex desktop as migrated
footprint. Post-cut verification includes `Antigravity.app`, `Antigravity
IDE.app`, and `Claude.app`; live reads of all three bundle identifiers; and
confirmation that the IDE-bundled launchers do not displace `agy` from core's
separate `antigravity-cli` cask. The Codex CLI and Claude Code must remain
core-supplied. The report must also prove that the pre-existing ChatGPT and
Claude applications were handled without destructive state loss and that
`ChatGPT Classic.app` remained outside this migration.

Neither migration advances from `INTEGRATED` to `COMPLETE` until its own
evidence supports that transition after the joint activation.

### 8. Residual activation risk

Build and parity evidence cannot prove:

- Homebrew's live install, uninstall, or reinstall behavior when Applite and
  GIMP change declaring modules;
- installed bundle identities after provider updates;
- GIMP's managed help block and file ownership in the live user profile;
- Petdex's legacy-state migration, compatibility link, copied JavaScript,
  desktop link, sidecar link, deep links, and ownership on an already-used
  profile;
- either provisional Antigravity bundle identity or first-install behavior;
- Homebrew's behavior at the unmanaged pre-existing `Claude.app` path, because
  no adoption mechanism is currently selected;
- the independently installed origin and provider transition behavior of the
  now-registered `ChatGPT.app`;
- preservation of unmanaged `ChatGPT Classic.app`, which is deliberately
  unclaimed;
- authority and `PATH` ordering among core `agy`, the destination's
  application-bundled `agy-ide` and `antigravity-ide`, the Codex CLI, and
  Claude Code; or
- upgrade and rollback behavior when both unactivated domains are selected
  together.

Those are activation evidence, not reasons to contaminate migration parity.
They remain outstanding until the separately authorized joint activation.

## Consequences

- The migration advances from `INVENTORY` to `BUILDING`.
- The next implementation boundary is creation and independent qualification of
  `developer-ux-nd` with only the existing migrated footprint.
- That original boundary is now complete; after the DEVUX5 `BLOCKED` result,
  the current next boundary is the activation-dormant temporary-handoff
  destination overlap defined by the section 5A amendment.
- Petdex moves as a coherent product slice; core retains no Petdex runtime stub
  or reverse dependency after the cut.
- Configuration-less bootstrap is appropriate despite the destination's
  tracked GIMP template and fixed Petdex state contract.
- Seven existing GUI claims receive temporary handoffs for overlap and lose them
  at the matched cut. Zenmap receives no claim while it supplies no app bundle.
- ChatGPT/current Codex desktop moves as existing core footprint; the deprecated
  `codex-app` cask is excluded.
- The Antigravity command center, Antigravity IDE, and Claude desktop are
  post-cut destination-only additions with no handoffs; all three retained
  terminal/agent primitives remain core-owned.
- The unmanaged Claude bundle is an unresolved activation risk, not a claim of
  safe provider adoption; `ChatGPT Classic.app` is deliberately unclaimed.
- Entertainment remains integrated and unactivated until the later joint
  activation boundary.
- Accepting this record changes no runtime Nix declaration, selected lock,
  installed application, or active host state.

## Migration record

The tracked checklist and evidence state are maintained in the
[`developer-ux-nd` migration inventory](../../../architecture/domain-migrations/developer-ux-nd-inventory.md).
Execution follows the
[owner migration template](../../../architecture/domain-migrations/owner-migration-template.md)
and the
[subflake migration guide](../../../architecture/subflake-migration-guide.md),
subject to the matched-cut and post-cut feature sequencing in this ADR.
