# DINAS ROOT1 attended continuation

## Completed retirement — current authority

The checkout relocation, repository-URL transition, and filesystem retirement
are complete. The canonical checkout is
`$HOME/.local/nix-darwin/darwin-is-not-a-snowflake`; its remote is
`git@github.com:Knowledge-Forge-AI/darwin-is-not-a-snowflake.git`. `$HOME/.flakes` has been
retired and must not be recreated as active compatibility state.
`~/.local/nbin` is an ordinary managed namespace directory; all root symlinks
are rejected. The obsolete legacy-target guard and residue ignore rules have
been removed. `dinas-root-cutover` is retired and refuses operational use.

The remainder of this document preserves the historical procedure and its
then-current authority boundaries. Its compatibility topology, pending rename,
and resume commands are not current instructions. Preserve rollback archives
and journals; any recovery from historical material requires separate authority.

## Source candidate and authority

The product is `darwin-is-not-a-snowflake` (DINAS). Product files now live at the
Git root. The canonical active checkout is
`$HOME/.local/nix-darwin/darwin-is-not-a-snowflake`.

This source phase prepares code, disposable migration fixtures, checks and
no-link builds. The dispatcher owns independent review and source publication.
The exact accepted post-flatten commit must be supplied by that acceptance
record; a source-producing stage cannot report a commit it has not created.
The canonical remote is `git@github.com:Knowledge-Forge-AI/darwin-is-not-a-snowflake.git`.
The operator must complete and attest the GitHub rename before using that URL.
After the rename, GitHub redirects ordinary Git traffic from `lair001/.flakes`;
local origin and catalog entries may intentionally retain that spelling until
attended cutover. Do not create or reuse a repository at `lair001/.flakes` afterward.

No real checkout relocation, source-catalog edit, composition mutation, host
activation or garbage collection is part of the source phase. Stop after source
acceptance for the separately authorized attended continuation below.

## Matched composition transition

Preserve the repository transport and release ref. The public composition's
portable core declaration changes exactly from:

```nix
core.url = "git+ssh://git@github.com/Knowledge-Forge-AI/.flakes.git?dir=nix-darwin";
```

to:

```nix
core.url = "git+ssh://git@github.com/Knowledge-Forge-AI/.flakes.git";
```

Historical compositions retain `dir=nix-darwin` and their pre-flatten revisions;
those Git objects remain evaluable. New compositions select the accepted
post-flatten revision at the repository root. Do not apply the old selector to
a post-flatten revision or remove it from a historical revision.

Prepare the matched source declaration and lock transaction in the owning public
composition, then promote that exact accepted composition revision through its
private parent using SOURCES1. Use the catalog's local checkout and exact release
ref for unpublished qualification. Do not use a remote query as admission
for that local source. Do not mutate either real composition during source review.

Nix must generate the candidate lock. The structural authorization helper
`authorize_core_lock_transition(..., allow_layout_transition=True)` accepts only
historical `nix-darwin` to root, emitting
`nix-update-core-layout-transition-v1`. Its allowed delta is:

- The same direct `core` node and logical Git repository identity.
- Removal of `dir` from both original and locked source records.
- The requested exact revision and Nix-generated `narHash`/`lastModified` fields.
- Identical child nodes, input/follows topology, root inputs and unrelated nodes.

Ordinary native promotion remains unable to authorize source-declaration edits.
The structural helper validates supplied lock documents; it is not a new CLI
that edits or publishes composition sources. The attended owner transaction must
bind the declaration edit together with the Nix-generated lock and this explicit
authorization. Never hand-edit hashes or broaden the authorized node set to
conceal a dependency change. The private-parent delta is its registered public
composition node and the exact owned closure permitted by its native contract.

ROOT1 does not introduce local paths into portable composition locks. The
accepted core flake itself has a pre-existing absolute local `repo-map-src`
input. Replacing that source requires its approved portable source identity;
retaining it does not establish a fully portable core lock.

### Repository identity migration

Following the ROOT1 layout cut, migrating the public composition's remote repository
identity from:

```nix
core.url = "git+ssh://git@github.com/Knowledge-Forge-AI/.flakes.git";
```

to:

```nix
core.url = "git+ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git";
```

is a separate one-composition transaction authorized by
`authorize_core_lock_transition(..., allow_repository_transition=True)`.
This emits `nix-update-core-repository-transition-v1`, which digest-binds both
the preimage (`ssh://git@github.com/Knowledge-Forge-AI/.flakes.git`) and destination
(`ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git`) repository URLs,
requiring root layout on both sides, identical node topology, and the requested
exact revision. Because `local_source_binding.validate_local_sources` compares
the locked URL to `[sources.core].repository`, the catalog identity cut
must precede the composition lock URL migration.

This transition is wired into the native promotion pipeline via the
`--allow-core-repository-transition` flag on `nix-update promote prepare`.
This is not a generic repository rename facility: only the exact historical
`.flakes` to `darwin-is-not-a-snowflake` transition is authorized. After the
REMOTE2 catalog cut, prepare the one-time migration with:

```sh
nix-update promote prepare --flake "$PUBLIC_COMPOSITION" --input core \
  --revision 2f2f0ec887a6342eca9e9d3f84d71816c66c1e12 \
  --allow-core-repository-transition --output "$PROMOTION_PLAN"
```

After public and private composition migration, the old GitHub redirect was
compatibility only. At that stage, the independent local `$HOME/.flakes`
compatibility paths remained in place; they have since been retired as recorded
in the completion notice above.

This flag acts as an explicit, one-time cutover gate that:
- Requires `--input core` and release ref `refs/heads/main`.
- Verifies catalog prerequisites (sources.core must identify `darwin-is-not-a-snowflake`).
- Operates independently of `$HOME/.flakes` checkout status.
- Migrates both `flake.nix` (core declaration URL) and `flake.lock` (core node URL) atomically.
- Records candidate artifacts (`flake.nix.candidate`, `flake.lock.candidate`, `delta.json`)
  under `retained-lock/`.
- Validates the resulting plan under schema `nix-update-promotion-core-repository-transition-plan-v1`.
- Executes dual-file atomic installation and rollback with preimage journal recovery upon `nix-update promote apply`.
In this schema, `repositoryUrl` is the destination and `previousRepositoryUrl` is the preimage; ordinary and layout records instead retain
the unchanged repository in `repositoryUrl`.

### Physical public-composition repository rename

Following the DINAS core repository rename, the physical public-composition repository
is renamed from `composition-nd` (`ssh://git@github.com/Knowledge-Forge-AI/composition-nd.git`) to
`composition_dinas` (`ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git`), while strictly
preserving the logical Nix input key `composition-nd`.

The physical canonical names after the attended cut are `composition_dinas` and
`composition-private_dinas`. The private checkout's physical rename does not need
parent-lock transition authority; the attended cut updates its active-flake pointer,
checkout path, origin, and catalog metadata. This core phase performs neither rename.
Any local aliases for the old checkout names are temporary compatibility aids and
must never become canonical source authority. GitHub redirects are compatibility
only; do not recreate the old repository names.

In the private parent composition (`composition-private_dinas`), the public composition's
declaration URL changes from:

```nix
inputs.composition-nd.url = "git+ssh://git@github.com/Knowledge-Forge-AI/composition-nd.git";
```

to:

```nix
inputs.composition-nd.url = "git+ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git";
```

The logical input name `composition-nd` is strictly retained across `flake.nix`, `flake.lock`
input bindings, and native promotion contract definitions.

This migration is authorized by DINAS native promotion via the explicit opt-in CLI flag
`--allow-composition-repository-transition` on `nix-update promote prepare`. It is not a
generic rename facility: only the exact physical URL rename `composition-nd` to
`composition_dinas` for logical input `composition-nd` is permitted.

Prerequisites:
- Before the attended cut, confirm the actual private declaration uses the supported
  single-line `[inputs.]composition-nd.url = "...";` assignment, with optional
  `git+` and `?ref=main`. Attribute-set declarations, additional query parameters,
  and selectors fail closed. Disposable fixtures qualify this
  declaration shape; the real parent transition remains an attended-cut proof.
- Operator catalog update: `sources.composition-nd` must point to the local checkout
  `composition_dinas` and candidate remote URL `ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git`.
- Release ref requirement: candidate and preimage must both use `refs/heads/main`.
- Private parent qualification: candidate qualification must succeed against the candidate
  `composition_dinas` repository.
- Complete fail-closed separation: `--allow-repository-transition` and
  `--allow-composition-repository-transition` cannot be combined in any promotion transaction.

Prepare invocation:

```sh
nix-update promote prepare --flake "$PRIVATE_COMPOSITION" --input composition-nd \
  --revision "$ACCEPTED_COMPOSITION_COMMIT" \
  --allow-composition-repository-transition --output "$PROMOTION_PLAN"
```

The transaction semantics:
- Authorization record: emitted under schema `nix-update-promotion-owned-repository-transition-v1`,
  binding preimage (`composition-nd`), destination (`composition_dinas`), exact revision,
  owned graph closures, and canonical SHA-256 digests.
- Plan document: validated under schema `nix-update-promotion-composition-repository-transition-plan-v1`.
- Retained lock candidate: records candidate declaration and lock artifacts (`flake.nix.candidate`,
  `flake.lock.candidate`, `delta.json`).
- Application and rollback: `nix-update promote apply` performs dual-file atomic installation
  of both `flake.nix` and `flake.lock` with journaled rollback to preimages on failure.

## Filesystem and catalog cut

After source acceptance, bind the exact accepted commit and inspect the tool's
read-only preflight from the source checkout:

```sh
./bin/dinas-root-cutover --accepted-commit "$ACCEPTED_CORE_COMMIT" --dry-run
./bin/dinas-root-cutover --accepted-commit "$ACCEPTED_CORE_COMMIT"
```

Add `--navigation-symlink /absolute/owner-selected/link` only for an intentionally
selected navigation link. Repeat the same arguments during recovery. The owner of any active Git MCP allowlist must also admit the canonical checkout in its separately authorized configuration; the legacy core copy of that transferred tooling config is not runtime authority. The tool
supports `--home` for disposable rehearsals. Do not run it as root.

Immediately before this cut, OLD is an ordinary checkout already containing the
accepted flattened commit; `nix-darwin/` may retain unrelated operational files.
Legacy Serena memories (`nix-darwin/.serena/`) and the legacy Claude runner symlink
(`nix-darwin/bin/claude`) are intentionally untracked and ignored operator material
at OLD. Following core publication of the corrected flattened tree, the operator
restores this separately preserved residue at OLD before retrying the cutover
dry-run or preflight. The tool verifies actual tracked bytes and executable bits
against Git blobs, including skip-worktree drift, and rejects active Git operations.
Registered linked worktrees or explicit `core.worktree` configuration require a
separate owner relocation before proceeding.

The resulting topology is:

```text
NEW = $HOME/.local/nix-darwin/darwin-is-not-a-snowflake  ordinary Git checkout
OLD = $HOME/.flakes                                  ordinary compatibility directory
OLD/nix-darwin -> NEW                                compatibility symlink
```

OLD must never itself become a symlink to NEW. New automation uses `dinas_root`
or NEW; old callers can use OLD/nix-darwin during the bounded compatibility window.

The atomic catalog edit is only:

```text
sources.core.checkout: "$HOME/.flakes"
                    -> "$HOME/.local/nix-darwin/darwin-is-not-a-snowflake"
```

Repository URL, release ref, publication metadata and unrelated TOML semantics
remain unchanged. The tool binds those semantics in its private journal and
revalidates the full updated control config before atomic replacement.

Tracked files and `.git` move to NEW. Untracked and ignored material stays in OLD;
an existing ordinary OLD/nix-darwin directory is renamed in place to
OLD/.root1-preserved-nix-darwin before the compatibility link is created.
This attended compatibility-directory cut preserves the untracked and ignored
legacy Serena and Claude operator residue in OLD/.root1-preserved-nix-darwin.
No source-tree backup, cache archive or deletion is used. Repository export rules
exclude Serena operational metadata and the transferred, unpackaged
`codex-tooling-self-heal` pointer from product Git archives while preserving
them in the checkout and Git history. The private journal at
`$HOME/.local/nix-darwin/root-cutover-journal.json` records the accepted commit,
phase progress, moved-file count and top-level names containing retained files
or symlinks. Empty directory remnants remain untouched and are omitted from that
material list; do not use OLD/bin as a post-cutover command path. Its sibling lock
serializes cutover writers.

Operational Serena memories (`.serena/memories/`) have been reconciled to the
root-level product layout and current `darwin-is-not-a-snowflake` repository identity.
Use this root-level handoff and README for current commands. When retiring the
compatibility window, also retire the exact legacy
nbin-target guard in `modules/dinas/command-namespaces.nix` and its corresponding
assertion, together with the `/nix-darwin/.serena/` and
`/nix-darwin/bin/claude` rules in `.gitignore`. These ignore rules protect the
restored residue only during the publication-to-cutover window at OLD.

## Recovery and deployment boundary

On interruption, keep both roots and the journal. Re-run the same command with
`--dry-run` to validate the split tracked tree, then without it to resume.
Recovery accepts exactly one `.git` directory and exactly one copy of every
accepted tracked file across OLD and NEW. It never deletes NEW. Catalog or
navigation drift, duplicate files, unexpected destinations and ambiguous links
stop for owner reconciliation; do not reset or replay a different commit.

After `.git` moves, OLD is not source-catalog authority. Do not point the catalog
back to OLD as a rollback shortcut. Keep NEW and recover forward. A rollback to
an older source requires a separately authorized real checkout of the historical
commit and its matching composition selector. Previously prepared deployment
plans must be discarded and prepared again after relocation; their execution
and mutable-state bindings belong to the prior location.

The filesystem cut invokes no Nix switch. After the matched composition migration
is accepted, ordinary `nix-update` still selects and qualifies the configured
active top-level composition. Perform its normal attended deployment with fresh
confirmation. Agent-Central remains a consumer, not runtime authority. Preserve
APGR, command namespaces, Developer UX and security choices. Leaf `_dinas` rename
waves remain deferred while the canonical remote repository identity becomes
`darwin-is-not-a-snowflake`.
