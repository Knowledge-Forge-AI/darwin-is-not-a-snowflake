# ADR 0001 — Nix-Darwin Core, Subflake Extension Contract, and Owner-Driven Migration

Status: Accepted

Date: 2026-08-10

Supersedes: none. Supersedes in part the constructor signature recorded in
`vscode-nd` ADR 0001 §1 — see "The layer extension contract" below.

## Context

This repository began as one flake that configured one workstation. Every
capability the operator wanted — editors, language managers, browsers, backup
tooling, and the Nix-side integration of several independent software projects —
was added directly to it. That was the right shape while there was one
consumer.

It no longer is. The repository is now three things at once: the bootstrap and
recovery authority for a fresh machine, a reusable configuration framework, and
the accidental host of Nix integration code belonging to projects that are
developed, versioned, reviewed, and owned elsewhere. Those three roles have
incompatible requirements. Bootstrap wants a small, portable, independently
buildable flake. The framework wants stable published interfaces. The
project integrations want to move at the speed of the projects they serve, and
several of them carry source identities that must not appear in a
publication-capable repository at all.

A prior architecture phase inventoried the tracked tree exhaustively and found
that the coupling is not superficial. Some optional integrations are read at
*evaluation* time, not merely build time, so this repository currently cannot be
evaluated without fetching repositories that not every consumer can access. At
least one tool installed into the system profile is supplied by an optional
integration's overlay entry, so deleting an input would break the core-only
state rather than clean it. The problem is ownership, not configuration.

That phase drew the correct technical conclusions and one incorrect
organizational one: it assigned the migration of *every* project's footprint to
the team that owns this repository's framework. That does not scale and it is
not that team's knowledge. A team that did not write an integration cannot
credibly prove parity for it, cannot judge which of its behaviours are load
bearing, and cannot own the residual risk of removing it.

This decision keeps the technical conclusions and replaces the ownership model.

## Decision

### Core is a framework and a bootstrap authority, not a host for project code

This repository — "core" — remains a valid top-level flake on its own. It builds
without any composition layer, and it is what a fresh or recovering machine
uses.

Core owns generic composition mechanism: the configuration constructor, the host
table, the overlay composition seam, the machine-local control plane and its
pure evaluation projection, the selected-flake launcher, the generated-shell
seam, bootstrap, and the tests that hold those contracts. It also owns a
publication-safe base workstation capability — the shell, language managers,
terminal, browsers, host security, and the managed toolchains — because those
are properties of the machine rather than of any one project.

Core does not own the Nix integration of independent projects.

### No new project-specific optional footprint in core

This is the forward rule, and it is unconditional:

```text
NO NEW PROJECT-SPECIFIC OPTIONAL FOOTPRINT IN CORE
```

Allowed as ordinary core work: generic composition interfaces, generic
validation, generic launcher and configuration infrastructure, generic bootstrap
and recovery, the generic shell composition seam, publication-safe base
workstation capability, and tests of those contracts.

Not allowed without its own superseding ADR: a new project-specific package,
flake input, Darwin module, scheduled agent, configuration file, shell fragment,
source pin, or state path.

The rule is written this way because the alternative — "add it to core for now
and clean it up later" — is precisely how the current situation was produced.
Every existing footprint was once a reasonable exception.

A rule with no owner and no check is a slogan, and this repository already
contains dozens of grandfathered examples that make new coupling look normal to
a reviewer. The rule therefore carries an enforcement obligation, delivered by
the framework team as part of the implementation slices:

- **A tracked core surface inventory** enumerating core-owned flake inputs,
  package outputs, overlay attributes, module imports, static shell fragments,
  scheduled agents, configuration roots, and state roots.
- **A check that fails on an unclassified addition** to any of those families.
  The check is structural — it compares declared surfaces against actual ones —
  and it does not need to understand what a new surface *means* to notice that
  nobody classified it.
- **An exception record naming its superseding ADR.** An addition is classified
  by pointing at the decision that allows it. "No ADR" is the failure mode the
  check exists to catch.

What the check cannot do is judge whether a surface is genuinely generic or
project-specific in disguise. That is a review judgement, owned by the framework
team, recorded as a disposition on the change. Where classification cannot be
automated, the requirement is a durable review checklist with a named owner —
not a claim of mechanical enforcement that the mechanism does not deliver.

Until that inventory and check exist, this rule is review protocol only, and
should be described that way.

### Existing footprints are grandfathered, and their owners migrate them

The footprints already in the tracked tree remain until their owning teams move
them. They are not deleted by the framework team, not rewritten by it, and not
blocked on it.

Each owner team owns the whole of its own migration: the inventory, the
destination repository, the implementation move, the parity proof, the removal
from core, and its own decision and status records. The framework team owns the
mechanism those teams use, and nothing else.

This has a consequence worth stating plainly: core will not be
publication-ready until the last owner team finishes. That is accepted. A
grandfathered footprint blocks publication readiness; it does not block any
other team's migration, and it does not block the framework team from
completing its own.

### The `*-nd` module repository contract

A migrated integration lives in its own repository named `<project>-nd`. Every
output a composition consumes is a **flake output**, because a composition may
not read a module's directories — that would be the filesystem discovery this
decision forbids. Directories such as `config/`, `shell/`, `tests/` and `docs/`
are internal layout, consumed only by the module's own expressions.

| Output | Required | Shape | Merge / collision |
|---|---|---|---|
| `darwinModules.default` | yes | Darwin module | module system merge; duplicate option definition is an evaluation error |
| `darwinModules.<name>` | when >1 module | Darwin module | as above |
| `overlays.default` | if it owns packages | overlay `final: prev: {…}` | applied in the composition's declared order; later overlay wins on attribute collision |
| `packages.<system>.*` | no | derivations | module-local; not aggregated |
| `apps.<system>.*` | no | flake apps | module-local; not aggregated |
| `checks.<system>.*` | no | derivations | aggregated under the module's name |
| `configAllowlist` | if it reads control state | attrset of dotted key → declaration | keys are namespaced by module name; a duplicate key across layers is an evaluation error |
| `packageSetIdentity` | if it has a `nixpkgs` input | `{ schema, revision, outPath }` | all three fields must be equal across every layer |
| `shellContributions` | if it contributes shell state | list of `{ name, order, text, exports ? [] }` | ordered by `order` then `name`; duplicate `name` or duplicate entry in `exports` is an evaluation error |

`packageSetIdentity.revision` is the resolved `nixpkgs` revision and
`outPath` its store path, both taken from the module's own resolved input. A
module with no `nixpkgs` input exports no identity and is exempt from the
equality check — it cannot disagree about a package set it never resolved.

`shellContributions` exists because a directory is not a flake output. Each
entry renders one numbered fragment into the generated-shell generation, in a
total order the composition can compute without looking at any filesystem.
`exports` optionally declares the environment variables the fragment sets, so
two modules claiming the same variable fail at evaluation rather than by
last-writer-wins at shell load.

Publication class is **not** a module output. Registry placement is the sole
classification authority: a module is public-capable because a public-capable
composition registers it. A self-declared class would be an unverifiable claim
by the party least able to be objective about it, and a public repository
carrying a `NON-PUBLIC` marker is itself an oddity.

Do not add empty ceremonial outputs. A module with no packages exports no
overlay; a module with no operator commands exports no apps. An output that
exists only to satisfy a checklist is noise later readers must disprove.

The contract is deliberately small. It is not a plugin framework, there is no
lifecycle to implement, and there are no hooks to register.

### The layer extension contract

`vscode-nd` ADR 0001 §1 recorded core's constructor as
`lib.mkDarwinConfiguration { hostName, hostConfig, extraModules }`, with each
layer re-exporting a constructor of the same shape. That shape is retained and
**extended**; this section supersedes the signature, not the delegation model.

Core exports:

```text
darwinModules.core
overlays.default                       core-owned packages only
lib.hosts
lib.packageSetIdentity
lib.mkDarwinConfiguration {
  hostName,
  hostConfig    ? lib.hosts.${hostName},
  extraModules  ? [ ],
  extraOverlays ? [ ]
}
darwinConfigurations.<host>            core-only configurations
configAllowlist                        core-owned keys
```

Every composition layer exports a constructor of that same extended shape. It
does not invoke `darwinSystem`; it prepends its own registered modules and
overlays to what its caller passed and delegates **once** to the layer below:

```text
composition.lib.mkDarwinConfiguration { hostName, hostConfig, extraModules, extraOverlays }
  = lower.lib.mkDarwinConfiguration {
      inherit hostName hostConfig;
      extraModules  = ownRegisteredModules  ++ extraModules;
      extraOverlays = ownRegisteredOverlays ++ extraOverlays;
    }
```

`nix-darwin.lib.darwinSystem` is invoked exactly once, in core. No layer copies
the module list, the host table, or another layer's registry.

This yields one total order, outward from core:

```text
core overlay
  -> public composition's registered overlays, in its declared order
    -> non-public composition's registered overlays, in its declared order
      -> whatever that layer's caller passed
```

Later overlays may read earlier ones; earlier overlays can never see later ones.
That is the property that makes the direction of dependency real rather than
conventional.

Additional rules the layers must hold:

- **Registered module names are unique across the whole composed set.** A
  duplicate name is an evaluation error, not a silent override — otherwise an
  upper layer could shadow a lower layer's module without either declaring it.
- **Allowlist keys are namespaced by module name** and a duplicate key is an
  evaluation error.
- **Package-set identity is asserted by the selected top-level flake,** across
  core, every composition layer, and every registered module.
- **Checks are owned where they are written.** A module's checks stay in the
  module's flake; a composition exports only checks about composition —
  registry uniqueness, overlay ordering, allowlist collisions, package-set
  equality, and shell-fragment collisions. A composition does not re-export a
  module's checks as its own, because that makes the module's health
  indistinguishable from the composition's.

An upper layer therefore needs nothing from a lower layer except this
constructor and these outputs. It never reaches into an input's internals, never
takes core as a second parallel input, and never reimplements a registry.

### Compositions aggregate; they do not implement

A composition is a top-level flake that selects modules and hands them to core's
constructor. It aggregates Darwin modules, ordered overlays, configuration
allowlists, package-set identity checks, generated shell contributions, host and
module configuration, and checks. It contains no module implementation of
substance.

Registration is explicit: tracked flake inputs and a tracked registry.
Compositions never discover modules by scanning sibling directories, globbing a
path, or probing for a checkout. Discovery-by-filesystem makes the composed
system a function of what happens to be on disk, which is untestable, and it
makes concealment impossible — a public layer that searches for optional
neighbours has already disclosed that neighbours exist.

Two composition layers are defined. The public-capable composition,
`composition-nd`, requires core, is a complete top-level flake, and registers
`PUBLIC-CAPABLE` modules; `vscode-nd` is its first. It has no non-public
dependency of any kind. A generic higher-level composition may in turn require
`composition-nd` and register modules an operator has designated non-public;
core and `composition-nd` do not name it, depend on it, search for it, or
document its identity, and the operator installs it manually.

Exactly one top-level flake is selected per rebuild. These layers are a
dependency chain, not an activation stack: selecting the higher one does not
activate the lower ones separately.

### Extension seams: overlays, host facts, and module configuration

Core exports `overlays.default` containing core-owned packages only. The
constructor accepts an explicit, ordered `extraOverlays` list, applied after
core's own overlay, and the composition passes module overlays in a declared
deterministic order.

The parameter is explicit on purpose. A composition could append to the
platform's overlay list option instead and rely on list merging — but the
resulting order would be a function of module evaluation order rather than of
anything either layer states, and overlay order is load-bearing whenever an
overlay reads its predecessor. The platform does not specify that option's
ordering semantics, so relying on them would be building on undocumented
behaviour.

One package set stays binding. Every module flake and every composition follows
core's `nixpkgs`; each module publishes the package-set identity it resolved
against; the selected top-level flake asserts those identities are equal. A
forgotten `follows` then fails a check instead of silently instantiating a
second package set. No module instantiates its own.

Core's host table contains only facts core itself understands — machine identity
and the build-user identifiers. Module-specific enable flags and configuration
never become permanent core host-table keys, because a key in core's table is a
switch any layer can reach, and core cannot validate a value whose meaning lives
somewhere else. Upper layers configure modules through `extraModules` or an
accepted generic equivalent: the public composition owns public module
configuration, and a non-public composition owns non-public module
configuration.

Core validates host objects against a closed key set and fails evaluation on an
unknown key. Within its scope — the host table specifically — this makes "no
hidden default silently enables an optional module" mechanical rather than
conventional. It says nothing about inputs, packages, imports, shell fragments,
or state paths, which are covered by the surface inventory described above.

### Tests, configuration, shell state, and documentation move with ownership

Tests that assert a module's behaviour belong to the module. Tests that assert
composition belong to core. A file that asserts both is split, not relocated,
and a module's test must not remain in core as the only proof that the module
works.

Core's coverage manifest maps source files to owning test lanes. A manifest row
whose source is absent from the coverage data raises an advisory warning rather
than failing the suite; the hard gate is the aggregate coverage threshold per
language and metric. Both matter during a migration, and they fail differently:
a stale row is visible but tolerated, while removing a module's sources and
tests shifts the aggregate and can push a language below its threshold — a hard
failure in someone else's unrelated change. A source file and its manifest row
therefore move in the same commit, and the removing team checks the aggregate
rather than assuming the warning is the only signal.

A module's configuration files, activation helpers, and state-path contracts
move with it. Its shell state enters through the generated-shell seam rather
than as a static core fragment. Core keeps its own capability fragments; the
composition mechanism contributes a closed set of its own. The invariant is that
**registering a module never adds a fragment to core** — not that core has few
fragments.

Documentation follows the same rule, including historical documentation.
"Historical" does not imply publication-safe. A historical document that
describes a migrated integration belongs to that integration's repository or to
private archival evidence. Nothing is deleted to clean the tree; it is moved.

### Selection remains the launcher's job

The machine-local root stays `$NIX_DARWIN_LOCAL`, defaulting to
`~/.local/nix-darwin`, with its control file at
`$NIX_DARWIN_LOCAL/config/nix-darwin.toml`. Selection precedence is an explicit
`--flake` argument, then the validated control file's active selection, then
core when the configuration intentionally selects nothing. The environment value
is diagnostic, never authoritative, and a configured selection that cannot be
resolved fails closed rather than falling back.

Compositions do not add alternate selection systems. One mechanism selects one
top-level flake.

### Bootstrap knows core and the public composition, and stops

Core's bootstrap installs Nix and Nix-managed Git, establishes
`$NIX_DARWIN_LOCAL` with its configuration directory and default, may clone
`composition-nd`, and stops. It does not run the composition's bootstrap, does
not activate, and has no knowledge of any non-public composition.

Each composition owns its own explicit, idempotent bootstrap: it installs the
module checkouts it has registered, creates missing module configuration without
overwriting existing configuration, renders its evaluation and shell
contributions, validates package-set identity, validates the composition, and
stops short of activation absent separate authorization. No step scans a
directory or discovers anything.

### Migration readiness and publication readiness are different milestones

Three milestones, deliberately independent:

**Architecture ready** — core's composition interfaces exist, the composition
repositories exist, the normative decision records exist, the module contract
exists, and the selection mechanism exists. Owner teams can start.

**Owner migration complete**, per team — that team's module is extracted,
registered in the composition matching its publication class, proved at parity,
and removed from core, with its own evidence. One team reaching this does not
require any other team to.

**Publication ready**, for the ecosystem — every remaining footprint that
violates the publication policy has been migrated by its owner, and the
publication gates pass: no non-public identity in any publication-capable
tracked head, no operator or machine identity in a repository about to become
public, and a published history that contains no prohibited disclosure. This is
a shared obligation, not the framework team's alone.

A valid top-level composition is therefore **not** a publication certificate.
During the migration program every layer transitively inherits whatever
grandfathered footprints core still carries. That is expected and must not be
described as publication readiness.

### Evidence required before a footprint leaves core

An owner team removes its footprint from core only after it can show, for its
own module:

1. an inventory of every surface it owns in core — inputs, packages, modules,
   configuration, shell state, helpers, tests, documentation, activation
   effects, and state paths;
2. the destination repository implementing the applicable `*-nd` exports;
3. registration in the owning composition;
4. **parity proved while core still provides the module**, so the comparison is
   against a working system rather than against a memory of one;
5. core building after removal, with the coverage manifest updated in the same
   commit;
6. the composition building after removal;
7. **the completed migration rebased onto the accepted heads of core and its
   composition, with the full affected gates re-run on that integrated state**;
8. its own decision, status, and Git evidence.

Requirement 7 exists because migrations run concurrently. Two migrations that
are each green in isolation can produce a combined state neither team built:
registry names that collide, an overlay order nobody declared, two halves of a
split test file that no longer union, an aggregate coverage figure below its
threshold, or a core that builds for each team separately and for neither
together. Same-commit movement of a source and its manifest row is necessary and
is not sufficient — it is a property of one commit, and this is a property of a
merge. The integrated re-run covers registry uniqueness, overlay ordering,
allowlist collisions, package-set equality, mixed-test ownership, core with the
removed checkout absent, and the selected composition.

Parity is proved build-only unless activation is separately authorized, and it
compares a **canonical parity artifact** rather than an ad-hoc impression of
sameness. Two teams using different notions of parity can both satisfy prose;
they cannot both satisfy a named artifact with a defined comparison.

The artifact is evaluated from the baseline configuration and from the candidate
configuration and covers, at minimum: the normalized system package set;
Homebrew state; full scheduled-agent definitions including labels, arguments,
schedules, working directories, environment and log paths; log rotation entries;
activation script text; the generated shell manifest and fragment content; option
definitions, assertions and effective values; owner and mode contracts for
installed paths; and declared state roots.

Exact store-path identity is not required and should not be claimed: relocating
a source changes derivation inputs even when observable configuration is
identical. Expected differences — source paths, store paths derived from them —
are enumerated in advance. An unexplained difference blocks the parity claim; it
is not normalized away after the fact.

Build parity still cannot show services actually loading, directories actually
installing, fragments actually publishing, state roots actually taking
ownership, or anything about upgrade and rollback on a live machine. Those are
recorded as explicit residual-risk entries requiring separately authorized
activation validation. A team that cannot prove parity records the gap; it does
not downgrade the claim to fit what it could prove.

## Consequences

Core stops growing. New project integrations go to their own repositories from
the start, which is cheaper than the migration this decision exists to organize.

Core does not become publication-ready when the framework team finishes. It
becomes publication-ready when the last owner team finishes. Publication
readiness is now visibly a shared, tracked obligation rather than an assumption.

Each owner team takes on a repository lifecycle it did not have: its own flake,
tests, and release cadence. That cost is real. It is accepted because the
alternative is a repository nobody can publish, nobody can fully evaluate, and
no single team can safely change.

Teams migrate independently and in any order. There is no global barrier, no
sequencing negotiation, and no team blocked on another's schedule. The cost is
that core carries a mixed tree — some migrated, some not — for as long as the
program runs, and the tree alone will not tell a reader which is which. The
migration guide and each team's status records are where that is tracked.

The core-only state is deliberately smaller than the operator's full
workstation, and it loses capability as each migration lands. That is the
intended direction: those capabilities are restored by the composition that owns
them.

Several mechanism claims here — overlay ordering under the explicit parameter,
closed host-key validation, and package-set identity equality — rest on platform
documentation and on reading tracked artifacts rather than on local evaluation,
because the authoring environment could not reach the build daemon. Each is the
first gate of the implementation slice that depends on it.

## References

- [Subflake migration guide](../../../architecture/subflake-migration-guide.md)
