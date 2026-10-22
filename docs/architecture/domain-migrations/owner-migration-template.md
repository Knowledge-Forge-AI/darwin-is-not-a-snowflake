# Owner Migration Template

Copy this file to `docs/architecture/domain-migrations/<module>-inventory.md`,
fill it in from **live source**, and keep it updated as the migration runs. It is
the checklist your removal commit is verified against.

Normative decisions:
[ADR 0001](../../adr/2026/08/0001-nix-darwin-core-subflake-extension-contract-and-owner-driven-migration.md)
and
[ADR 0002](../../adr/2026/08/0002-domain-ownership-application-claims-and-migration-sequencing.md).
The actionable prose protocol is the
[subflake migration guide](../subflake-migration-guide.md). Where any of them
disagree with this template, they win.

This template exists because prose alone lets two owners use two different
definitions of "done". Every section below is either filled in or explicitly
marked `none` with a reason. A blank section is an incomplete inventory, and an
incomplete inventory is the usual cause of a broken cut.

---

## Header

```text
module:                 <name>-nd
publication class:      PUBLIC-CAPABLE | NON-PUBLIC
owning composition:     <the composition that registers it>
owner:                  <team or person accountable for this migration>
status:                 INVENTORY | BUILDING | PARITY | CUT | INTEGRATED | COMPLETE
governing ADR:          <path to this migration's own decision record>
activation required:    YES | NO   (see "Activation requirement" below)
```

Publication class is chosen from **today's** facts, not from what might be
decided later. The class of the whole module is the class of its most restricted
required dependency. Migrating as non-public and reclassifying later is cheap;
un-disclosing is impossible.

---

## 1. Inventory

Enumerate every surface this module owns in core. Each row records where it is
now, so the removal commit can be checked against this list rather than against
memory.

For each family: list every item with its current path and, where it is a line
in a larger file, the declaring symbol or line. Mark a family `none` explicitly
rather than omitting it.

| Family | What to look for |
|---|---|
| Flake inputs | every input the integration reads, including inputs pinned separately for one rendered artifact |
| Packages and overlay attributes | package definitions, `callPackage` wiring, every overlay attribute including compatibility aliases, and `packages.<system>` entries |
| Darwin modules | module files and their import wiring in the core module list |
| Options | option namespaces declared, plus any assignment made outside those modules |
| Host facts | keys in core's host table only this integration reads |
| Application claims | every macOS application this module will install, with canonical identity — see section 4 |
| Configuration | files under the configuration tree, including templates, policy documents, and container build context |
| `bin/`, `libexec/`, `tools/` | user-facing commands and helper implementations |
| Tests | unit, integration, and Nix tests, plus fixtures and support harnesses |
| Coverage rows | every `src/test/coverage-manifest.tsv` row naming a source that moves |
| Docs, ADRs, history | current and historical documents, including deployment and exit reports that materially describe this domain |
| Activation effects | every `system.activationScripts` fragment, and what it installs, owns, or deletes |
| launchd agents and daemons | labels, arguments, schedules, working directories, environment, log paths, and log rotation entries |
| State roots | state, configuration, report, and log roots outside the store that this integration creates or writes |
| Skills | skills owned by this domain and where they will be exposed afterwards |
| Shell contributions | static fragments, `PATH` additions, exported variables, shell initialisation injected from a module |
| Allowlist keys | machine-local control-plane keys this module reads |
| System profile entries | anything of this module's in `environment.systemPackages` — these are what break the core-only build when an input is removed without moving its consumer |

Two searches catch most omissions:

1. Search the tracked tree for every spelling of the domain's identifiers —
   hyphenated, camel-cased, underscored, upper-cased, and as a path component.
2. Search for anything that reads these sources **at evaluation time**. A module
   that reads a file out of an input while being evaluated couples core to that
   input far more tightly than a build-time dependency does, and nothing in the
   module's shape announces it.

---

## 2. Destination module

```text
repository:             <URL or "to be created">
exports implemented:    darwinModules.default, overlays.default, ...
exports deliberately omitted and why:
nixpkgs follows core:   YES | NO   (NO requires an explanation)
packageSetIdentity:     exported | exempt (no nixpkgs input)
```

Implement only the exports the module actually has. Everything a composition
consumes is a flake output; `config/`, `shell/`, `tests/`, and `docs/` are
internal layout a composition may not read.

Do not declare a publication class as an output. Registry placement is the
classification authority.

---

## 3. Composition registration

```text
registry entry:         <composition, registry name>
canonical repo URL:
deterministic checkout name:
configuration initializer:  <reviewed repo-relative tool with fixed argv, or a
                             registered default-file copy — never a shell
                             command string in registry data>
overlay position:       <declared position in the composition's ordered list>
allowlist keys added:   <namespaced under the module name>
```

Register only after the destination repository qualifies on its own. A
composition that registers an unfinished module makes every other owner's build
a function of work in progress.

---

## 4. Application claims

One row per macOS application this module will install. Identity is
`bundle:<bundle identifier>` — or `mas:<id>` when the application is Mac App
Store only and its bundle identifier is not knowable without installing it.
Never a display name.

| Application | `id` | `provider` | `package` | Currently declared in core at |
|---|---|---|---|---|
| | | | | |

Command-line-only packages are excluded from claims.

### Handoff plan

For each application above, the parity window has core and this module both
declaring it. Record the handoff that authorizes the overlap:

```nix
handoff = { from = "core"; adr = "<path to this migration's ADR>"; };
```

| `id` | `from` | Added in commit | Removed in commit |
|---|---|---|---|
| | | | |

The handoff must be deleted in the **same** migration that removes the source
footprint. Once the source claim is gone the handoff no longer validates, so
leaving it behind is an evaluation failure rather than a stale annotation — that
is the mechanism, not an accident. Never widen a handoff into a wildcard or a
mutual arrangement.

---

## 5. Parity

Parity is proved **while core still provides the footprint**. Comparing against
a system already dismantled proves nothing.

Produce a canonical parity artifact from the baseline configuration and from the
candidate configuration, and compare. At minimum both cover:

```text
normalized system package set
Homebrew state — taps, casks, formulae, MAS apps
full launchd definitions: labels, arguments, schedules, working directories,
  environment, process type, stdout and stderr paths
log rotation entries
activation script text
generated shell manifest and each fragment's content
option definitions, assertions, and effective values in this namespace
owner and mode contracts for paths installed outside the store
declared state roots and configuration roots
```

### Expected differences, enumerated in advance

| Difference | Why it is expected |
|---|---|
| | |

Do not claim identical store paths. Relocating a source changes derivation
inputs even when observable configuration is identical. Any difference outside
the table above blocks the parity claim until it is explained — it is not added
to the table afterwards.

```text
baseline artifact:   <path>
candidate artifact:  <path>
result:              MATCH | MATCH WITH ENUMERATED DIFFERENCES | BLOCKED
```

For a migration spanning multiple Git repositories, candidate commits may be
qualified separately on non-default, non-selected branches. The ownership-cut
boundary is the top-level selected composition that consumes all matched halves
together: it must never select a half-cut pairing. Default branches must not be
left deliberately mismatched across long-running or asynchronous phases.

---

## 6. Core removal

Everything in section 1, removed. Specifically:

```text
[ ] inputs, packages, overlay attributes, package outputs removed
[ ] module files and their import wiring removed
[ ] options, host-table keys, allowlist keys removed
[ ] configuration, helpers, bin/libexec entries removed
[ ] launchd agents/daemons and log rotation entries removed
[ ] activation fragments removed
[ ] tests and fixtures moved; mixed test files SPLIT, not relocated
[ ] coverage manifest rows removed in the SAME commit as their sources
[ ] documentation and history moved, not deleted
[ ] core's application claims for these ids removed, and the handoff blocks
    deleted at the same selected ownership-cut boundary
[ ] no stub, alias, or retained host-table key left behind
[ ] no identifier of this domain remains in core's tracked tree
```

A stub defeats the verification. An assignment to an option whose declaration is
gone is an evaluation error, and that error is what makes the removal provable.

Then prove:

```text
[ ] core evaluates and builds with this checkout PHYSICALLY ABSENT — not merely
    unreferenced, actually not on disk
[ ] core's checks pass
[ ] aggregate coverage per language and metric is above threshold after removal
    (the stale-row warning is advisory; the aggregate is the hard gate)
[ ] the composition builds with the module registered
```

---

## 7. Integrate

Other owners are changing the same tree concurrently. Two individually green
branches can produce a combined state neither owner built.

Rebase onto the current accepted heads of core and the owning composition and
re-run the full affected gates on that integrated state:

```text
[ ] registry names unique across every registered module
[ ] overlay order is the declared one, with no unintended attribute collision
[ ] allowlist keys do not collide
[ ] package-set identity equal across every layer
[ ] application claims unique, with only authorized handoffs outstanding
[ ] split test files still union — no assertion lost between two branches
[ ] aggregate coverage above threshold on the MERGED tree
[ ] core builds with this checkout absent
[ ] the selected composition builds
[ ] composition lock advanced with an input-scoped update; lock delta inspected
    and no unrelated owner input drifted
```

If the integrated state fails, it belongs to whoever is landing.

---

## 8. Activation requirement and residual risk

Parity is build-only unless activation is separately authorized. Build parity
cannot show services actually loading, directories actually installing,
fragments actually publishing, state roots actually taking ownership, or
anything about upgrade and rollback on a live machine.

```text
activation required:   YES | NO
justification:
authorization:         <who authorized it, when — or "not yet requested">
```

### Residual risks

| Risk | Why build parity cannot cover it | Mitigation or acceptance |
|---|---|---|
| | | |

A migration with a recorded, bounded gap is a finished migration. One with an
unrecorded gap is a defect that surfaces on somebody else's machine. Record the
gap; do not widen the claim to cover what was not proved.

---

## 9. History and docs disposition

```text
documents moved to the destination repository:
documents moved to private archival evidence:
documents deliberately left in core, and why:
```

"Historical" does not imply publication-safe. A historical document describing a
non-public integration belongs with that integration or in private archival
evidence. Nothing is deleted to clean the tree; it is moved.

---

## 10. Evidence

```text
[ ] decision record: destination, publication class, what was deliberately not
    migrated, residual activation risk
[ ] status or exit record: what was proved and what was not
[ ] Git evidence for each commit in each repository changed
[ ] operational record of the qualification runs
[ ] Review A before the ownership cut
[ ] Review B against the frozen integrated candidate
[ ] supplemental review if a material correction landed after Review B
[ ] publication-readiness handoff recorded where the program tracks it
```
