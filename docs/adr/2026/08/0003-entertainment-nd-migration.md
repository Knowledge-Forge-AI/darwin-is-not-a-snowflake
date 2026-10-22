# ADR 0003 — `entertainment-nd` Ownership Migration

Status: Accepted

Date: 2026-08-20

Amended: 2026-08-20 — clarified the cross-repository ownership-cut
choreography and the selected-composition migration boundary.

Governing decisions: [ADR 0001](0001-nix-darwin-core-subflake-extension-contract-and-owner-driven-migration.md)
and [ADR 0002](0002-domain-ownership-application-claims-and-migration-sequencing.md).

Migration owner: `operator`

## Context

ADR 0001 assigns each grandfathered project or domain footprint to its owner
and requires a migration-specific decision before the ownership cut. ADR 0002
assigns Discord, MacGameStore, OpenEmu, VLC, and `yt-dlp` to
`entertainment-nd`, defines binding whole-set application-claim validation, and
permits a temporary duplicate claim only through an explicit handoff.

The destination has now qualified independently at commit
`7401bbf2b23fe327a013147766370f00a374c440`. That commit deliberately carries
ordinary owner-less, handoff-free application claims. This was correct for
standalone qualification: the repository was not registered in a composition,
and no composition or core change occurred in that qualification phase.

It is not, however, the claim shape that can be registered while core still
claims the same four applications. `composition-nd` now validates the binding
whole set of claims. Bare registration would present two owners for each
identity and would be rejected unless the destination claims first identify
the temporary transfer from `core`.

The destination also owns no machine-local configuration. The current
composition registry and bootstrap paths assume that every registered module
has a configuration file. Inventing a configuration surface merely to satisfy
that assumption would give the composition ownership the destination does not
have.

## Decision

### 1. Ownership and durable identities

The `operator` is accountable for this migration.

The destination and its composition identities are:

```text
repository:              git+ssh://git@github.com/Knowledge-Forge-AI/entertainment-nd.git
qualified commit:        7401bbf2b23fe327a013147766370f00a374c440
composition registry:    entertainment
deterministic checkout:  entertainment-nd
```

The qualified destination exports `darwinModules.default`,
`applicationClaims`, `packageSetIdentity`, and four standalone checks. It owns
no overlay, package output, app output, option namespace, machine-local
configuration, `configAllowlist`, configuration initializer, shell state,
launchd declaration, state root, activation script, or local tool.

Standalone qualification proved this exact destination delta:

- the only system-package addition is `pkgs.yt-dlp`;
- the only Homebrew-cask additions are `discord`, `macgamestore`, `openemu`,
  and `vlc`;
- the four claims below are ordinary owner-less claims with no handoff; and
- the destination's standalone checks passed.

These facts establish the destination boundary only. They do not establish
composition parity, core removal, activation, installation, or live
bundle-identity behavior.

### 2. Exact migration footprint

The entire system and Homebrew footprint moving from core is:

```text
system package:  pkgs.yt-dlp
Homebrew casks:  discord, macgamestore, openemu, vlc
```

The application identities and artifacts are:

| Application | Identity | Artifact | Provider package |
|---|---|---|---|
| Discord | `bundle:com.hnc.Discord` | `Discord.app` | `discord` |
| MacGameStore | `bundle:com.macgamestore.Client2` | `MacGameStore.app` | `macgamestore` |
| OpenEmu | `bundle:org.openemu.OpenEmu` | `OpenEmu.app` | `openemu` |
| VLC | `bundle:org.videolan.vlc` | `VLC.app` | `vlc` |

All four use provider `homebrew-cask`. `yt-dlp` is command-line only and is not
an application claim.

No adjacent package or application is part of this migration. There will be no
compatibility stub, alias, or permanent duplicate ownership.

### 3. Temporary application-claim handoffs

Before registration, each of the four destination claims will gain this
handoff:

```nix
handoff = {
  from = "core";
  adr = "docs/adr/2026/08/0003-entertainment-nd-migration.md";
};
```

The four records are temporary overlap authority only. They authorize the
parallel-ownership window needed to register the destination and prove parity
while core still supplies the footprint. They do not make duplicate ownership
permanent, and they become invalid as soon as the corresponding core claims
are removed.

The handoff change must amend the destination's standalone claim checks in the
same commit so those checks continue to describe the exported claims exactly.

The ownership cut is one matched migration boundary. Its invariant is:

> No selected composition may ever consume only one half of the ownership cut.

The matched boundary must remove:

- `pkgs.yt-dlp` from core's system packages;
- the four casks from core's declarations;
- the affected assertions from core's mixed tests;
- the four source application claims from core; and
- all four handoff blocks from the destination claims.

It must not leave a compatibility stub or a second permanent owner.

Git cannot atomically commit two independent repositories, and "matched" does
not require such a cross-repository transaction. Core and destination
candidates may be prepared, qualified, and published separately on non-default
staging branches. The binding boundary is the selected public composition
revision that consumes both qualified halves together. Publishing one half
directly to its default branch and leaving the other half for a later
asynchronous phase is not an accepted choreography.

### 4. Configuration-less composition bootstrap

`entertainment-nd` intentionally owns no configuration file,
`configAllowlist`, configuration initializer, shell state, or overlay.
Registration therefore requires an extension to the composition registry
contract, not a fake entertainment configuration surface.

The registry will gain a configuration-less bootstrap form named:

```json
"bootstrap": { "strategy": "none" }
```

The implementation must preserve the existing `repo-tool` and `copy-default`
behavior unchanged. An entry using strategy `none` must:

- clone or otherwise establish and validate its deterministic checkout;
- create no configuration file;
- require no `configAllowlist`;
- contribute no `moduleConfiguration` entry;
- still participate in registry-name and checkout uniqueness;
- still supply its Darwin module import;
- still participate in `applicationClaims` aggregation and validation;
- still participate in `packageSetIdentity` validation; and
- still participate in bootstrap validation.

This ADR decides that contract requirement. It does not implement the registry,
bootstrap, or registration change.

### 5. Executable migration sequence

The accepted sequence from the qualified destination is:

```text
migration ADR tracked
  -> destination claims gain four temporary handoff records from core
  -> composition-nd gains configuration-less registry support and registers
     entertainment-nd
  -> baseline/candidate parity while core still provides the footprint
  -> prepare and qualify separate core and destination cut candidates on
     non-default staging branches
  -> qualify both candidates together in a clean scratch composition and prove
     both half-cut pairings reject
  -> fast-forward both repository default branches in one bounded operator
     action while the selected composition remains on the old valid pair
  -> composition-nd resolves both new default revisions in one input-scoped
     lock operation; this selected revision is the ownership-cut boundary
  -> the selected non-public top-level composition advances to that public cut
     and completes the integrated gates
  -> separately authorized activation
```

Bare registration cannot precede the handoffs under the binding claim gate.
The earlier inventory wording that treated registration and removal as a naive
pair is superseded by this sequence.

The core staging candidate branches from the current accepted core default
branch (`main`). It removes only `pkgs.yt-dlp`, the four casks, the four source
claims, and their assertions in mixed test files, then qualifies standalone and
is published without advancing the default branch. The destination staging
candidate likewise branches from its current accepted default branch (`main`).
It removes only the four temporary handoff blocks, retaining `yt-dlp`, the
casks, claim ids, artifacts, providers, packages, and all runtime behavior; it
also qualifies standalone and is published without advancing the default
branch.

Before either candidate becomes selected, a clean scratch composition must
substitute both staging candidates together. The matched pair must pass binding
whole-set application-claim validation. The two half-cut controls must fail:
old core plus the handoff-free destination is a `duplicate-claim`, while the
claim-free core candidate plus the old handoff-bearing destination is a
`singleton-handoff`. These expected failures are the reason neither half may be
selected separately.

Only after both staging candidates and the matched scratch composition are
green may an operator fast-forward both repository default branches to the
already-qualified commits in one bounded action and window. That publication
uses no force push, merge commit, rebase, or additional source mutation. The
network ref updates are necessarily ordered, but their order is not the
migration boundary: `composition-nd` remains lock-pinned to the old valid pair
until both updates complete. The bounded window remains under the same
operator's control until both ref updates succeed; it must not be released as a
half-cut state for a later asynchronous agent phase.

The selected public cut then advances `composition-nd` with one input-scoped
lock operation that resolves both new default-branch revisions together. Its
binding whole-set application-claim validation must pass. That resulting
selected public composition, not either candidate commit or default-branch ref
update, is the ownership-cut boundary; only then may migration status advance
from `PARITY` to `CUT`. The selected non-public top-level composition advances
afterward to that public cut and must pass the complete public-plus-non-public
claim validation, selected Darwin builds, and the remaining integration gates
before status advances under the migration template.

### 6. Activation boundary

Activation remains required because the four applications are installed by
Homebrew during activation and build evidence cannot prove live installation or
bundle identity. This ADR does not authorize activation, switching, or any live
host mutation. Activation requires a later, separate authorization after the
ownership cut and integrated gates.

## Consequences

- The migration advances from `INVENTORY` to `BUILDING`.
- The destination handoff commit is the next implementation boundary; it makes
  no composition or core change.
- Registration depends on configuration-less bootstrap support in
  `composition-nd`.
- Parity is measured during an explicitly authorized, validator-accepted
  overlap window.
- Core and destination return to unique ownership at the matched cut.
- No runtime Nix behavior changes as a consequence of accepting this record.

## Migration record

The tracked checklist and evidence state are maintained in the
[`entertainment-nd` migration inventory](../../../architecture/domain-migrations/entertainment-nd-inventory.md).
Execution follows the
[owner migration template](../../../architecture/domain-migrations/owner-migration-template.md)
and the
[subflake migration guide](../../../architecture/subflake-migration-guide.md),
subject to the sequencing correction in this ADR.
