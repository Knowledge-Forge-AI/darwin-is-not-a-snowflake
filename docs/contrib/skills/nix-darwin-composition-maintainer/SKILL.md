---
name: nix-darwin-composition-maintainer
description: Maintain the nix-darwin core framework and its composition layers across the multi-repository owner-driven architecture. Use when work touches core's composition seams, the *-nd module output contract, composition registries, overlay ordering, config allowlists, application claims, package-set identity, owner migrations, or core's test and coverage infrastructure.
---

# Nix-Darwin Composition Maintainer

## Overview

This is the **framework** skill. It owns the ecosystem layer: the seams,
contracts, registries, and migration mechanics that let independently owned
`*-nd` modules compose into one system.

It is not a repository-specific refactoring guide. The nix-darwin ecosystem is
several repositories with separate owners, and the single most common way to
break it is to edit the wrong one.

Normative decisions, in precedence order:

- `docs/adr/2026/08/0002-domain-ownership-application-claims-and-migration-sequencing.md`
- `docs/adr/2026/08/0001-nix-darwin-core-subflake-extension-contract-and-owner-driven-migration.md`
- `docs/architecture/subflake-migration-guide.md`
- `docs/architecture/domain-migrations/owner-migration-template.md`

Where this skill and an ADR disagree, the ADR wins.

## 1. Determine the owning repository before editing

Do this first, every time, before proposing any change.

```text
Is the change about a seam, a registry, a contract, or generic machinery?
  -> core

Is it about how modules are selected, ordered, or aggregated?
  -> the composition that registers them

Is it about what one domain's applications or behaviour do?
  -> that domain's *-nd repository
```

If the answer is a domain module and that module does not exist yet, the change
does **not** become a core change. It becomes a blocked change with a named
destination, or it waits for the migration. "Add it to core for now" is the
mechanism that produced the migration program.

If the owning repository is genuinely unclear, say so and stop. Guessing costs
more than asking.

## 2. `NO NEW PROJECT-SPECIFIC OPTIONAL FOOTPRINT IN CORE`

Unconditional, from ADR 0001.

Allowed in core as ordinary work: generic composition interfaces, generic
validation, generic launcher and configuration infrastructure, generic bootstrap
and recovery, the generic shell composition seam, agent-useful developer
primitives, generic test and coverage infrastructure.

Not allowed without a superseding ADR: a new project-specific or
domain-specific package, flake input, Darwin module, scheduled agent,
configuration file, shell fragment, source pin, or state path.

The rule cannot be automated end to end. A check can notice that nobody
classified an addition; it cannot judge whether a surface is generic or
domain-specific in disguise. That judgement is the framework maintainer's, and
it is recorded as a disposition on the change.

Core's tree contains dozens of grandfathered violations. They are precedent for
nothing.

## 3. The `*-nd` output contract

Everything a composition consumes is a **flake output**. A composition may not
read a module's directories; `config/`, `shell/`, `tests/`, and `docs/` are
internal layout.

```text
darwinModules.default          the module a composition registers
darwinModules.<name>           named modules, when more than one is published
overlays.default               if the module owns packages
packages.<system>.*            module-local build targets, not aggregated
apps.<system>.*                operator entry points, not aggregated
checks.<system>.*              the module's own tests
configAllowlist                if it reads machine-local control state
packageSetIdentity             if it has a nixpkgs input
shellContributions             if it contributes shell state
applicationClaims              if it installs macOS applications (ADR 0002)
```

Omit what the module does not have. Do not add a ceremonial empty output.

Publication class is never a module output. Registry placement is the sole
classification authority.

## 4. Composition invariants to preserve

When touching a composition or core's constructor, these are the properties that
must survive:

- **One `darwinSystem` call**, in core. Every layer delegates once to the layer
  below with `extraModules` and `extraOverlays` prepended. No layer copies
  another layer's registry, module list, or host table.
- **Explicit registration.** Tracked flake input plus tracked registry entry. No
  directory scanning, globbing, or probing for a checkout — discovery by
  filesystem makes the system a function of what happens to be on disk, and a
  public layer that searches for optional neighbours has already disclosed that
  neighbours exist.
- **Registered module names unique** across the whole composed set; a duplicate
  is an evaluation error, not a silent override.
- **Overlay order is the declared order**, outward from core. Later overlays may
  read earlier ones; earlier ones can never see later ones. Verify ordering
  whenever an overlay reads its predecessor.
- **Allowlist keys namespaced by module name**; a duplicate key across layers is
  an evaluation error. Never allowlist a key holding a secret or a private
  identity — anything reachable from a flake input is copied into the
  world-readable store.
- **Package-set identity equal across every layer**, asserted by the selected
  top-level flake. Every module follows core's `nixpkgs`; a forgotten `follows`
  must fail a check rather than silently instantiate a second package set.
- **Shell state through `shellContributions`**, ordered by `order` then `name`,
  with declared `exports`. Registering a module never adds a static fragment to
  core.
- **Checks are owned where they are written.** A composition exports checks
  about composition only; it does not re-export a module's checks as its own.

## 5. Application claims

Per ADR 0002 §3. A module that installs a macOS application declares a claim:

```nix
applicationClaims = [
  { id = "bundle:org.mozilla.firefox"; provider = "homebrew-cask"; package = "firefox"; }
];
```

- Identity is `bundle:<bundle identifier>`, or `mas:<id>` when Mac App Store
  only. Never a display name. A cask token is provider metadata, not identity —
  keying on it makes cross-manager collisions invisible.
- `provider` is a closed set: `nix`, `homebrew-cask`, `mas`. Adding one requires
  an ADR. Applite is **not** a deployment authority; installing through it
  neither creates nor satisfies a claim.
- Command-line-only packages are excluded; they already collide visibly.
- The owner name is attached by the composition, never supplied by the module.
- The selected top-level composition is the binding validation point, because it
  is the only layer that can see the complete set.
- A duplicate `id` is an evaluation error unless one authorized migration
  handoff permits exactly that pair.

**Handoffs.** During a migration's parity window, the destination claim carries
`handoff = { from = "<source registry name>"; adr = "<tracked path>"; }`. It is
valid only when the named source actually claims the same `id`, exactly two
claims exist, the source declares no handoff of its own, and `adr` is non-empty.
Removing the source footprint invalidates the handoff, so the `handoff` block
must be deleted in the same migration — the exception cannot be left behind.

Never widen a handoff into a wildcard or a mutual arrangement. If a real case
cannot be expressed, that is an ADR, not a workaround.

## 6. Owner-driven migration mechanics

The framework owns the mechanism; owners own their migrations. When assisting a
migration, drive the sequence from
`docs/architecture/domain-migrations/owner-migration-template.md` and hold these
gates:

1. Inventory the **whole** footprint, including evaluation-time reads and system
   profile entries.
2. Destination module implementing the applicable `*-nd` exports.
3. Explicit registration in the owning composition.
4. **Parity proved while core still provides the footprint** — a canonical
   parity artifact from both configurations, with expected differences
   enumerated in advance. Never claim identical store paths.
5. Explicit application-claim handoff for each application in the overlap.
6. Core removal, with coverage manifest rows moved in the same commit as their
   sources. Split mixed test files; do not relocate them. Leave no stub, alias,
   or retained host-table key.
7. Core builds with the destination checkout **physically absent**.
8. The composition builds.
9. **Rebase onto accepted heads and re-run the full affected gates on the
   integrated state.** Two individually green migrations can produce a combined
   state neither owner built.
10. Live activation only where separately authorized.

Build parity cannot show services loading, directories installing, fragments
publishing, state roots taking ownership, or anything about upgrade and
rollback. Record those as residual risk. A migration with a recorded, bounded
gap is finished; one with an unrecorded gap is a defect on someone else's
machine.

## 7. Public-capable is not publication-ready

- **Public-capable** — registered in a public-capable composition; no non-public
  required dependency.
- **Publication-ready** — every owner has finished, and the publication gates
  pass: no non-public identity in a publication-capable tracked head, no
  operator or machine identity in a repository about to become public, and a
  published history containing no prohibited disclosure.

A valid top-level composition is not a publication certificate. Do not describe
one as the other.

Core's disclosure boundary applies to everything written here: documents in this
repository may name it, the public-capable composition, and generic
public-capable `*-nd` modules. They must not name an operator-designated
non-public repository, module, Git URL, flake input, checkout path, option name,
or configuration identity. Refer to those by role.

## 8. Working discipline

- Read task-local files first. Use `rg` and targeted reads; do not ingest the
  deployment archive.
- Leave unrelated dirty worktree changes alone. Never reset, restore, stash,
  clean, or rewrite operator work.
- One programming language per source file. Keep user-facing commands in `bin/`
  and implementation in `libexec/`, `tools/<domain>/`, or structured test
  folders.
- Add or update tests with each behavioural change, and keep the coverage
  manifest row beside its source.
- Verify with the narrowest useful command first, then widen.

## 9. Verification lanes

Selected-flake build and check lanes, in widening order:

```sh
nix-unit-test
nix-int-test
nix-test
nix-test --report
nix flake check --no-update-lock-file
```

- `nix-update build` and `nix-update prepare` run `nix-test --report` before any
  deployment authority. Prepare validates a virtual post-transaction control
  tree first; the exact retained projection must feed checks, every host build,
  target/closure capture, apply revalidation, publication, and switch. The real
  control root remains unchanged until digest-bound apply authority. Report
  output belongs in a new owner-only directory under private scratch, and every
  Nix qualification command carries both no-update and no-write lock flags so
  the reviewed source tree and lock bytes remain unchanged.
- Lock maintenance is a separate explicit `nix-update lock` transaction. In a
  repository it requires clean local Git authority, materializes the
  reviewed commit tree, resolves input aliases/follows to normalized lock nodes,
  and journals any requested install. It operates entirely local-first without
  requiring remote connectivity. Never qualify a lock candidate built from
  dirty filesystem bytes.
- One-composition promotion uses `nix-update promote prepare`, manual review,
  then `nix-update promote apply`. The selected composition owns the generic
  `lib.promotion` contract and fixed-argv qualifier. Core retains and installs
  the exact authorized lock candidate, requalifies it, and commits only
  `flake.lock` locally. The terminal `LOCAL_COMMITTED` stage status (or
  `LOCAL_COMPLETE_AWAITING_ACTIVATION` for a completed campaign) creates no remote push and grants no
  activation authority. Publication is handled explicitly via `nix-update promote publish`.
  Multi-stage promotion campaigns are natively sequenced by DINAS using
  `nix-update promote campaign run|resume|status`.
- A structural version-1 promotion contract implies `refs/heads/main`.
  Version 2 declares `releaseChannels.policy = "explicit-head-refs-v1"` and an
  exact `releaseChannels.refs` map for every direct promotable input. The
  selected flake declaration and both lock ref records must follow the declared
  branch; the operator supplies only an exact revision. Promotion verifies but
  never advances that branch.
- Promotion host impact is neutral derivation-identity evidence. Exact baseline
  and candidate host maps derive either `HOST_DERIVATIONS_IDENTICAL` or
  `HOST_DERIVATIONS_CHANGED`; `activationPerformed` remains false. Only a
  campaign may interpret the final stage, and activation remains a
  separate reviewed `nix-update deploy` (or `nix-update`) transaction.
  Deployment source version authority is governed by `[snapshot.sources]` in
  `nix-darwin.toml`, completely decoupled from composition lock convergence.
  Adopting promoted source commits into `[snapshot.sources]` uses the bounded
  two-phase transaction `nix-update snapshot prepare` (extracting pins from completed
  campaigns or explicit `--pin` arguments) and `nix-update snapshot apply <plan>`.
  Prepare validates candidate pins against local Git ancestry on configured release
  branches, generates the candidate postimage, and records a SHA-256 integrity digest.
  Apply acquires the deployment transaction lock, requires attended TTY confirmation,
  generates an atomic timestamped backup under `~/.local/state/nix-update/snapshot-backups/`,
  replaces `nix-darwin.toml` atomically (preserving permissions and ownership), and
  consumes plan authority. Rollback of Nix Darwin host generations remains an
  operational symlink switch decoupled from the declarative snapshot table.
- Every promotion contract, qualifier, check, and host observation is evaluated
  in a fresh canonical private snapshot: exact reviewed Git tree, exact
  flake-relative path, exact bound lock bytes, and the closed Git-archive
  materialization policy. The active checkout owns Git, lock installation,
  staging, commit, and publication only; ambient ignored bytes never become Nix
  authorization evidence. Temporary CWDs are execution evidence, not identity.
- Target-aware participants run only after target and closure capture. Their
  canonical context and timeout are plan-bound; apply completes every target
  build and closure walk first, then reruns exact mutable-state validation, the
  structured process gate, and a fresh read-only preflight immediately before
  each transition marker and privileged command. No nontrivial child operation
  may occur after that final participant attestation.
- Competing Nix, profile, or Darwin mutation is blocked by operation at every
  executable path, including alternate store/current-system paths and wrapped
  invocations. Exact tool paths never whitelist competing mutation.
- Reviewed operator participants bind a private lexical root and are frozen by
  SHA-256 under private transaction state during prepare. Operator artifacts
  are bounded shebang programs with no relative `env`/`PATH` dispatch; apply holds the revalidated frozen descriptor
  through spawn and invokes its validated absolute interpreter on `/dev/fd`.
  Tree probes bind
  independent lexical root topology as well as descendant content. Mutating
  post-switch reconcile uses phase-bound participant-local handles and reverse
  rollback; incomplete and indeterminate restoration are distinct terminals.
- `nix-rebuild PLAN` stays a managed `bin/` compatibility command delegating to
  `nix-update apply PLAN`, not a shell alias. No-argument update-and-switch and
  `nix-update --switch-only` are rejected.
- `nix-opt` prunes system generations down to a retained rollback window and
  never runs a destructive `nix-collect-garbage -d`. Use `nix-opt --dry-run` to
  inspect the retain and delete sets without mutating anything.
- Activation is **separately authorized**, always. Building and checking are not
  activation, and a green build is not a switched host.
- Do not mutate `flake.lock` unless a source change genuinely requires it, and
  say why.

## 10. Domain guidance does not live here

Browser policy, storage workflows, office applications, entertainment
applications, and security posture each belong to their owning repository, with
their own skills and their own documentation.

If a request needs domain knowledge, the answer is to find the owning repository
— not to add the knowledge to this skill. A framework skill that accumulates
domain instructions becomes the monolith this architecture replaced.
