# Subflake Migration Guide

Audience: a team that owns a software project which currently has Nix-darwin
integration code living in this repository, and now owns moving it out.

Normative decision:
[ADR 0001](../adr/2026/08/0001-nix-darwin-core-subflake-extension-contract-and-owner-driven-migration.md).
Where this guide and that ADR disagree, the ADR wins.

## 0. What you are being asked to do, and what you are not

You are being asked to move your project's Nix-darwin integration into a
repository your team owns, register it with a composition, and remove the copy
that lives here.

You are not being asked to change what your integration does. A migration that
also changes behaviour cannot be proved, because the parity check has nothing to
compare against. Change behaviour before or after, in its own change, never
during.

You are not being asked to migrate anyone else's footprint, and no other team is
waiting on you. Migrations are independent and unordered.

The framework team owns the mechanism described here. It does not own your
migration.

## 1. Inventory what you own

Before anything moves, enumerate every surface in this repository that belongs
to your project. The list is longer than most teams expect, and an incomplete
inventory is the usual cause of a broken cut.

- **Flake inputs** — every input your integration reads, including inputs pinned
  separately for a single rendered artifact.
- **Packages** — package definitions and their `callPackage` wiring, including
  cross-platform variants built for a non-host system.
- **Overlay entries** — every attribute your project adds to the package set,
  including compatibility aliases pointing at another attribute.
- **Package outputs** — entries in `packages.<system>` naming your project.
- **Darwin modules** — module files and their import wiring.
- **Options** — option namespaces your modules declare, plus any assignment to
  them made outside your modules.
- **Host facts** — keys in the host table that only your integration reads.
- **Configuration** — files under the configuration tree, including templates,
  policy documents, and container build context.
- **Shell state** — static shell fragments, `PATH` additions, exported
  variables, and shell initialisation injected from a module.
- **Helpers** — activation helpers and scripts under the executable trees.
- **Scheduled agents and log rotation** — user agents, their labels, and the log
  rotation entries that accompany them.
- **State and system paths** — state roots, configuration roots, report roots,
  and any path outside a store path that your integration creates or writes.
- **Tests** — unit, integration, and Nix tests, plus their fixtures, plus the
  coverage manifest rows that map your sources to test lanes.
- **Documentation** — current and historical, including deployment and exit
  reports that describe your project.
- **System profile entries** — anything of yours installed into the system
  package list. These are the ones that break the core-only build when an input
  is removed without moving its consumer.

Two checks catch most omissions. Search the tracked tree for your project's
identifiers in every spelling used — hyphenated, camel-cased, underscored,
upper-cased, and as a path component. Then search for anything that reads your
sources at evaluation time; a module that reads a file out of an input while the
module is being evaluated couples this repository to your repository far more
tightly than a build-time dependency does, and it is easy to miss because
nothing in the module's shape announces it.

Record the inventory. It is the checklist your removal commit is verified
against.

## 2. Choose a publication class

Every surface you inventoried is one of:

- **PUBLIC-CAPABLE** — safe to appear in a repository that may become public. It
  names no non-public repository, URL, credential, private path, or
  operator-designated private identity, and it requires no non-public source to
  build.
- **NON-PUBLIC** — anything else.

Judge by what is true today, not by what might be decided later. A repository
that is private today is non-public for this purpose unless its owner has
already recorded an explicit publication decision.

The class of the *whole* module is the class of its most restricted required
dependency. A module is not public-capable because most of it could be: if it
cannot build without a non-public source, it is non-public. Splitting a module
to raise its class is legitimate only when the resulting public part has no
non-public dependency at all — including package metadata that names a
non-public repository. Do not split merely to keep more capability in a public
layer.

- PUBLIC-CAPABLE modules register in `composition-nd`.
- NON-PUBLIC modules register in the operator-designated non-public composition.
  Ask the operator; its identity is not recorded in this repository.

If your project is non-public today and you expect it to be published later,
migrate it as non-public now and reclassify later. Reclassifying is cheap.
Un-disclosing is not possible.

## 3. Create your `<project>-nd` repository

Implement the exports your module actually has. The normative table is in
[ADR 0001](../adr/2026/08/0001-nix-darwin-core-subflake-extension-contract-and-owner-driven-migration.md);
this is the working summary:

```text
darwinModules.default          the module a composition registers
darwinModules.<name>           named modules, when you publish more than one
overlays.default               if you own packages
packages.<system>.*            build targets for your own CI
apps.<system>.*                operator entry points, when you have any
checks.<system>.*              your tests
configAllowlist                if you read machine-local control state
packageSetIdentity             if you have a nixpkgs input
shellContributions             if you contribute shell state
```

Omit what you do not have. Do not add an empty output to complete the list.

**Everything a composition consumes is a flake output.** Your `config/`,
`shell/`, `tests/` and `docs/` directories are internal layout — a composition
may not read them, because reading them would be the filesystem discovery the
architecture forbids. If a composition needs it, it is an output.

Requirements:

- **Follow core's `nixpkgs`.** Do not pin your own. Export
  `packageSetIdentity` as `{ schema, revision, outPath }` from your resolved
  input, so the composition can assert every layer resolved the same package
  set; a forgotten `follows` must fail a check rather than silently instantiate
  a second one. A module with no `nixpkgs` input exports no identity and is
  exempt.
- **Declare your own configuration allowlist,** keyed under your module name.
  Only keys you name are projected from the machine-local control plane into
  evaluation. Never allowlist a key holding a secret or a private identity:
  anything reachable from a flake input is copied into the store, which is
  world-readable and effectively permanent. Repository visibility does not
  protect it.
- **Contribute shell state as `shellContributions`,** a list of
  `{ name, order, text, exports ? [] }`, not as a static fragment in core.
  Declare the environment variables you set in `exports`, so two modules
  claiming the same variable fail at evaluation instead of silently resolving by
  load order.
- **Own your state paths.** Declare them in your module rather than assuming a
  path core creates.
- **Do not declare your own publication class.** Registry placement is the
  classification authority: your module is public-capable because a
  public-capable composition registers it.

For development, use a local checkout override through the machine-local
override map. Do not point a tracked input at a placeholder URL and rely on a
local override to make it resolve — the tracked flake must be valid for anyone
who can legitimately fetch its inputs, and a fake canonical URL produces a flake
that is invalid without private local state.

## 4. Register with the owning composition

Registration is explicit: a tracked flake input and a tracked registry entry in
the composition. Nothing is discovered by scanning.

The composition aggregates your module, your overlay in a declared position, your
configuration allowlist, your shell contributions, your package-set identity
assertion, and your checks. It does not contain your implementation.

Register only after your repository qualifies on its own. A composition that
registers an unfinished module makes every other team's build a function of your
work in progress.

The registry entry also declares the canonical repository URL, deterministic
checkout name, and one constrained configuration initializer. Use a reviewed
repo-relative tool with fixed argv or a registered default-file copy; do not put
shell command strings in registry data. Follow both the lower `nixpkgs` and the
lower `nix-darwin` input when the module carries the latter only for standalone
checks, so composition does not create a second framework universe.

## 5. Prove parity before you cut

This is the step teams skip, and it is the reason cuts fail.

Prove parity **while this repository still provides your integration**. Build the
composition with your module registered, build the current configuration, and
compare. Comparing against a system you have already dismantled proves nothing.

Produce a **parity artifact** from each configuration and compare the two.
"Looks the same" is not a result anyone else can check, and two teams working
from prose alone will use two different definitions of parity.

Evaluate from both the baseline and the candidate, and compare:

- the normalized system package set;
- Homebrew state — taps, casks, formulae;
- full scheduled-agent definitions: labels, program arguments, schedules,
  working directories, environment, process type, and standard output and error
  paths;
- log rotation entries;
- activation script text;
- the generated shell manifest and each fragment's content;
- option definitions, assertions, and effective values in your namespace;
- owner and mode contracts for any path you install outside the store;
- declared state roots and configuration roots.

**Do not claim identical store paths.** Moving a source changes derivation
inputs even when the observable configuration is identical, so exact closure
identity is the wrong test and chasing it leads to undocumented normalization.
Enumerate the differences you expect — source paths and the store paths derived
from them — before you run the comparison. Any difference outside that list
blocks the claim until you explain it. Do not add it to the expected list
afterwards.

What build parity still cannot show, at all: services actually loading, system
directories actually installing, generated fragments actually publishing, state
roots actually taking ownership, and anything about upgrade or rollback on a
live machine. If your integration depends on any of those, record each as an
explicit residual-risk entry and request separately authorized activation
validation. Record the gap; do not quietly widen the claim to cover it.

## 6. Remove your footprint from core

Only now. In your removal change:

- Remove every surface from your inventory — inputs, packages, overlay entries,
  package outputs, modules, options, host-table keys, configuration, shell
  fragments, helpers, scheduled agents, log rotation entries, tests, and
  fixtures.
- **Remove test sources and their coverage manifest rows in the same commit.**
  The manifest maps sources to owning test lanes. A row whose source is gone
  raises an advisory coverage warning rather than failing the suite — so this
  will not stop you, and a stale row can survive unnoticed for a long time.
  The hard gate is the aggregate coverage threshold per language and metric, and
  that is the one your removal can break: taking out your sources and your tests
  shifts the aggregate, and if it drops below the threshold the failure lands on
  whoever runs the suite next. Run the full suite after removal and read the
  aggregate, not just the warnings.
- **Split mixed test files; do not relocate them.** A file that asserts both
  composition behaviour and your module's behaviour has a core half and a yours
  half. Move your half, leave core's, and delete no assertion silently.
- **Remove literal identifiers of yours from remaining core fixtures.** A test
  that asserts "core contains no optional module" must read its needle list from
  private configuration, not embed it. A public file listing private identities
  in order to search for them is itself the disclosure.
- **Leave no shim.** No option stub, no host-table key retained "for
  compatibility", no alias. An assignment to an option whose declaration is gone
  is an evaluation error, not a silent no-op — which is what makes the removal
  verifiable, and why a stub defeats it.
- **Move your documentation,** including historical documents describing your
  project, to your repository or to private archival evidence. Do not delete
  history to clean the tree.
- **Touch nothing belonging to another project.** If you find another team's
  footprint while working, record it and tell them.

Then prove:

- core evaluates and builds with your checkout physically absent — not merely
  unreferenced, actually not on disk;
- core's checks pass;
- the composition still builds with your module registered;
- no identifier of yours remains in core's tracked tree.

"Physically absent" is the meaningful test. An integration can appear removed and
still be reachable through a lock entry, a transitively inherited input, or a
path that happens to exist on the machine doing the verification.

### Then integrate, because you are not the only team migrating

Everything above proves your migration against the core you started from. Other
teams are changing the same tree at the same time, and two individually green
branches can produce a combined state neither of you built.

Before you claim completion, rebase onto the current accepted heads of core and
your composition and re-run the full affected gates on that integrated state:

- registry names are unique across every registered module;
- overlay order is the declared one, with no unintended attribute collision;
- allowlist keys do not collide;
- package-set identity is equal across every layer;
- split test files still union — your half plus core's half covers what the
  original file covered, with no assertion lost between two branches;
- the aggregate coverage figure is above threshold on the merged tree, not just
  on yours;
- core builds with your checkout absent;
- the selected composition builds.

Advance the composition lock to the accepted owner head with an input-scoped
update. Inspect the lock delta: the intended module and lower framework may
move, but unrelated owner inputs must not drift. When a non-public composition
is involved, qualify core, the public composition, and that top-level
composition independently; each is a complete selected flake, not one stage of
an activation stack.

If the integrated state fails, it is yours to fix — you are the one landing.

## 7. Produce your evidence

Your team's records, in your repository:

- a decision record covering the destination, the publication class, anything
  you deliberately did not migrate, and residual activation risk;
- a status or exit record stating what was proved and what was not;
- Git evidence for each commit in each repository you changed;
- an operational record of the qualification runs.

Use an independent Review A before the ownership cut and Review B against the
frozen integrated candidate. A material correction after Review B requires a
supplemental review before lock handoff.

State residual risk explicitly. A migration with a recorded, bounded gap is a
finished migration. One with an unrecorded gap is a defect that surfaces on
somebody else's machine.

## 8. Handoff to publication readiness

Your migration removes your project's contribution to the publication blocker
set. It does not make this repository publishable — that requires every owner
team to finish, plus separate gates covering non-public identities in tracked
heads, operator and machine identity in repositories about to become public, and
the history actually published.

When you finish, say so where the program tracks it, so the remaining blocker
set is accurate. Until then your footprint is grandfathered: it blocks
publication readiness and nothing else.

## Quick checklist

```text
[ ] inventory complete, including evaluation-time reads and system profile entries
[ ] publication class chosen from today's facts
[ ] <project>-nd created, exports implemented, no ceremonial outputs
[ ] everything a composition consumes is a flake output, not a directory
[ ] follows core's nixpkgs; exports packageSetIdentity
[ ] configAllowlist namespaced; no secret or private identity allowlisted
[ ] shell state exported as shellContributions with declared exports
[ ] registered explicitly in the owning composition
[ ] constrained bootstrap metadata recorded; no shell command evaluation
[ ] nixpkgs and standalone nix-darwin inputs follow the lower framework
[ ] parity artifact produced from BOTH configurations, WHILE core still provides the module
[ ] expected differences enumerated in advance; no unexplained difference remains
[ ] activation-only, upgrade, and rollback gaps recorded as residual risk
[ ] core footprint removed; coverage manifest rows in the same commit
[ ] mixed test files split, not relocated
[ ] no option stub, host key, or alias left behind
[ ] documentation moved, nothing deleted
[ ] core builds with your checkout physically absent
[ ] composition builds
[ ] rebased onto accepted heads; full gates re-run on the integrated state
[ ] composition lock advanced with no unrelated input drift
[ ] Review A and frozen-candidate Review B complete
[ ] aggregate coverage above threshold on the merged tree
[ ] decision, status, Git, and operational evidence produced
[ ] publication-readiness handoff recorded
```
