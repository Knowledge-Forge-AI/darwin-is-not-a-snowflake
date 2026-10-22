# Operator-local Git source authority

Status: SOURCES1 implementation candidate; independent acceptance and operator
adoption are separate boundaries.

## Authority

The operator control TOML accepts `[sources.<key>]` records. Each record binds
one logical key, an exact ordinary Git checkout, a canonical portable repository
identity, and a normalized local `refs/heads/...` release ref. Publication is
optional. No hosting-provider API is involved.

```toml
[sources.core]
checkout = "$HOME/.local/nix-darwin/darwin-is-not-a-snowflake"
repository = "https://git.example.invalid/operator/dinas.git"
release_ref = "refs/heads/main"

[sources.public-composition]
checkout = "$HOME/src/composition-nd"
repository = "https://git.example.invalid/operator/composition-nd.git"
release_ref = "refs/heads/main"

[sources.private-composition]
checkout = "$HOME/src/composition-private-nd"
repository = "ssh://git@git.example.invalid/operator/composition-private-nd.git"
release_ref = "refs/heads/main"

# Optional, and independent from local source selection:
[sources.public-composition.publication]
remote = "origin"
ref = "refs/heads/main"
```

Use real canonical repository declarations from the portable composition contract
and lock in place of these examples. Catalog checkout paths identify repository roots. Core historically retained a
`nix-darwin` subflake selector in the portable lock (`?dir=nix-darwin`), which is
removed following ROOT1 product flattening to root.
The key `core` identifies the source stage of a core promotion campaign;
composition stages are matched by canonical repository identity.

Unknown fields, malformed refs, credential-bearing URLs, duplicate repository
identities and duplicate checkout paths are errors. Checkout paths use the
existing portable-path expansion and managed-root validation. They must exist
below the operator home and cannot be unsafe or ambiguous symlinks.

`promotion.checkouts` remains a compatibility mapping. Its keys and checkout
paths must be disjoint from catalog records. Adoption replaces each migrated
mapping with one catalog entry; it does not maintain two checkout authorities.
When a catalog is present, composition routing uses its exact repository matches
and ignores legacy checkout aliases and workspace-root defaults. Those defaults
remain available only for catalog-free compatibility operation.
No catalog is inferred from workspace contents, outboxes, or `~/ai` links.

## Local promotion and publication

With catalog authority configured, prepare observes the exact local release
commit, materializes its immutable source through Nix, qualifies the candidate,
and retains a versioned reviewed plan. Apply revalidates authority and produces
one `flake.lock`-only composition commit. It performs no push. Campaigns pass
that exact local commit to the next composition stage. Activation remains a
separate `nix-update deploy` transaction.

Local-authority plans use native locked-source qualification: Nix resolves the
retained portable graph and builds every contract-declared check and host.
The owner qualifier file remains bound for identity drift, but this versioned
protocol does not claim to execute its revision-URL candidate interface or
attest optional owner snapshot protocols. This is the explicit schema seam for
unpublished sources: a revision-only canonical URL can require a remote fetch
even when the lock's NAR-addressed source is already available locally.
Legacy remote mode continues to execute the registered-input owner qualifier,
including when a retained legacy plan is applied after catalog adoption. Plan
schema selects the qualification protocol; catalog presence does not change it.

Publication is explicit:

```text
nix-update promote publish --source private-composition --revision <40-hex> --dry-run
nix-update promote publish --source private-composition --revision <40-hex>
```

The dry run renders dependency order without network queries or mutation.
Publication requires optional metadata on the selected sources and publishes
dependencies before their consumers. It verifies configured Git destinations
and exact remote truth, uses ordinary fast-forward pushes, and preserves local
commits on rejection or indeterminate publication. Reconciliation never amends,
rebases, or rewrites the promoted commits. Missing metadata blocks publication
only.

Without a catalog, legacy remote-coupled native plans retain their existing
behavior, including candidate remote admission and apply-time publication.
Legacy plans and results retain their original schemas. Local plans/results use
new schemas and distinguish local commits, publication, and activation. The
compatibility path can be retired after deployed operators have adopted the
catalog and historical unconsumed remote plans have been explicitly disposed of.

## Immutable realization and portable locks

### Serial promotion with advanced release heads

Ordinary local-authority promotion v4 distinguishes selected-candidate authority
from realization of an unchanged unrelated lock node. The configured local
checkout and exact selected commit provide source authority. Tracked working-tree
dirt and branch/HEAD divergence in local checkouts do not block qualification or
promotion when committed bytes are selected explicitly. Dirty working-tree state
is recorded as a diagnostic rather than a gate.

An unrelated node outside that closure may retain locked commit L while its
configured release ref identifies H, provided L equals or is an ancestor of H.
The complete unrelated lock node must remain unchanged. Local checkout state
(whether dirty, advanced, or detached) does not gate source observation or
materialization. Repository and ref identity remain exact. Missing commits,
replacement refs, grafts, shallow history, active Git operations, and
ancestry-altering environment overrides fail closed.

The promotion binding records the observed source head at H separately from
the immutable realization at L. Nix materializes L, and its NAR hash must equal
the portable lock's recorded hash. Observation around realization detects
movement during prepare; apply revalidates the exact reviewed L, relationship,
and realization at the transaction boundaries. A further advance to H2 requires
a fresh plan, even when L remains its ancestor.

Release-ref advancement alone authorizes no lock movement. Two advanced module
releases can converge through serial one-input promotions in either order;
an advanced core ref can also support its unchanged historical lock selection.
Every promotion still changes only its selected owned lock closure.

New ordinary local plans use `nix-update-promotion-plan-v4` and
`nix-update-promotion-local-source-binding-v2`. Retained v2/v3 plans keep their
original strict interpretation, digests, and replay protections; they are not
migrated. Tool drift may require fresh preparation. Local result v2 is unchanged.

Deployment plans bind exact immutable commits. Unlike legacy promotion models,
deployment does not require catalogued sources to converge on current release
refs or clean working trees. `flake.lock` provides the default build snapshot,
and exact commits materialize immutably from local Git databases.

Local source observation excludes untracked and ignored operational metadata.
Missing commits and active Git operations fail closed. However, tracked product
drift, detached HEAD state, and HEAD/ref divergence in source checkouts do not
block selection or evaluation of committed objects. Executing promotion tool
bytes retain their separate source-closure binding.

Nix establishes the store source and NAR hash for the exact Git commit. Candidate
lock generation uses immutable content, imports its child graph through Nix,
and restores the canonical portable source declaration before the existing
narrow lock-transition authorization. Committed locks contain no checkout,
temporary path, home path, or local file URL. Build and deployment materialize
catalog-bound locked revisions before evaluation and revalidate their identities
at the existing transaction boundaries. Uncommitted working-tree bytes never
enter ordinary build or deployment.

The Darwin spike on Nix 2.31.5 established that exact-revision local Git
materialization reads committed content despite a tracked worktree edit. A lock
with the same Nix-derived NAR hash and an unreachable canonical HTTPS source
evaluated offline from the store. A second spike established whole-repository
`builtins.fetchTree` materialization for repositories with only a subflake:
the source path, revision, NAR hash, and timestamp are Nix-derived; `revCount`
is optional. Ordinary build and deployment work offline without remote access.

## Local commit authority and candidate projection

DINAS treats local checkouts and exact selected commits as source authority for
consumer laptop operations. Remote tracking refs, current branch pointers, and
unrelated worktree dirt are not prerequisites for building or deploying a laptop
configuration.

### Core invariants

1. **Exact commit authority**: Builds and deployments are anchored to immutable
   git commits, not ephemeral branch heads or working-tree dirt. Uncommitted
   working-tree bytes never enter ordinary builds or deployments.
2. **Ephemeral candidate projection**: When testing candidate or alternative
   source revisions, selections are ephemeral. The tool writes candidate revisions
   and NAR hashes into a temporary scratch `flake.lock` (`scratch_target / "flake.lock"`)
   rather than modifying the operator's checked-in `flake.lock`.
3. **Plan immutability & replay**: Deployment plans record a resolved `commit_map`
   mapping each catalog key to its exact commit hash. Plan apply revalidates the
   bound immutable source identity keys and the resolved `commit_map` rather than
   requiring clean or stationary working trees.
4. **Publication separation**: Publication remains fail-closed. Pushing or publishing
   a candidate release requires a clean worktree and an attached named branch;
   local build and deploy permissions do not relax publication safety.
5. **Identical build and switch authority**: The exact scratch flake used to
   qualify and build the target is the flake authority used for the mutating
   Darwin switch (`darwin-rebuild switch --flake path:{scratch_target}#{host}`).
   Canonical `selected` remains authoritative for operator selection, Git
   commit/tree/index/worktree drift checks, matching catalog keys, and user-facing
   evidence. But the switch mutator consumes `scratch_target`, ensuring build and
   switch authority never diverge when effective source snapshots differ from the
   checked-in flake lock. Scratch lock bytes and Git authority are revalidated
   immediately before host mutation.
6. **Durable plan source authority & scratch path separation**: Deployment plan
   evidence must be durable across scratch directory lifetimes. The evaluation flake
   (the isolated scratch directory `scratch_target` used for lock projection, nix
   qualification, building, and switching) is kept strictly distinct from the durable
   logical selected flake identity (the canonical operator-selected flake path, e.g.
   `~/.local/nix-darwin/composition-private_dinas`).
   Deployment plan evidence records the canonical selected path in both `plan.selected_flake`
   and `sources.selected_flake`. Nix derivation commands run against the evaluated scratch
   flake, but `sources.selected_flake` binds to the canonical operator-selected flake path.
   This prevents ephemeral scratch pathnames (`.../nix-update-local-XXXXXXXX/target`) from
   leaking into persisted plan evidence, ensuring plan replay (`nix-update deploy --plan <dir>`)
   in an independent scratch transaction successfully verifies source authority without false-positive
   source drift rejections.

### CLI interface

`nix-update build` and `nix-update deploy` support repeatable `--source-revision`
arguments:

```text
nix-update build --source-revision KEY=REV [--source-revision KEY2=REV2]
nix-update deploy --source-revision KEY=REV [--source-revision KEY2=REV2]
```

Where `KEY` matches a catalog key representing a dependency lock node (such as `core`)
and `REV` is a 40-character hexadecimal commit hash present in that checkout's git
object database. `--source-revision` is an ephemeral catalog lock-source revision
override that projects into dependency lock nodes; the selected top-level flake
repository itself is not a lock node and is separately snapshotted by exact local Git
authority.

When `--source-revision` is provided:
- The candidate commit is verified to exist and resolve to a commit object in the
  local checkout.
- The commit is materialized into the Nix store via `materialize_source`, deriving
  its exact store path and NAR hash.
- The matching node in `flake.lock` is updated in the isolated execution workspace.
- The deployment plan records the resolved `commit_map` in schema
  `nix-update-local-plan-v2` (when no source catalog is configured) or
  `nix-update-local-plan-v3` (when a source catalog is configured).

## Declarative source snapshot (`[snapshot.sources]`)

To avoid passing dozens of ephemeral `--source-revision` CLI flags on every build
or deployment, DINAS provides declarative source snapshot tables in the operator
control TOML (`~/.local/nix-darwin/config/nix-darwin.toml`).

### Catalog vs Snapshot distinction

- **Catalog (`[sources.<key>]`)**: Defines the known universe of operator source
  checkouts, canonical repository identities, release refs, and publication paths.
  The catalog is long-lived and changes infrequently (e.g. when onboarding a new
  repository).
- **Snapshot (`[snapshot.sources]`)**: Defines a complete, pinned set of exact Git
  commit revisions (40-hex hashes) for all catalog sources. It captures the exact
  revisions to be used for ordinary builds and deployments.

```toml
[sources.core]
checkout = "$HOME/.local/nix-darwin/darwin-is-not-a-snowflake"
repository = "https://git.example.invalid/operator/dinas.git"
release_ref = "refs/heads/main"

[sources.public-composition]
checkout = "$HOME/src/composition-nd"
repository = "https://git.example.invalid/operator/composition-nd.git"
release_ref = "refs/heads/main"

[sources.private-composition]
checkout = "$HOME/src/composition-private-nd"
repository = "ssh://git@git.example.invalid/operator/composition-private-nd.git"
release_ref = "refs/heads/main"

# Declarative snapshot pinned for deployment:
[snapshot.sources]
core = "43b6f165d56c65b8b32a90b87b3f5dd9ec45df00"
public-composition = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
private-composition = "f0e1d2c3b4a5968778695041322314059687ba98"
```

### Strict completeness rule

When `[snapshot]` is configured:
1. `snapshot.sources` must match the configured `[sources]` catalog keys **exactly**.
2. **Fail closed on missing keys**: If a catalog source key is missing from `snapshot.sources`,
   configuration loading fails with `ConfigError`.
3. **Fail closed on unknown keys**: If `snapshot.sources` contains a key that does not exist
   in `[sources]`, configuration loading fails with `ConfigError`.
4. **Strict commit format**: Every revision value must be a 40-character hexadecimal
   SHA-1 commit hash (normalized to lowercase).
5. **Exact key matching**: Source keys in `snapshot.sources` must match catalog keys exactly;
   there is no implicit hyphen/underscore substitution or alias mapping.
6. **No remote fallback**: All declared snapshot revisions must exist in local Git
   checkouts. If any revision is missing from the local object database, materialization
   fails immediately and locally; DINAS never initiates remote git fetches or fallback.

### Snapshot absence preserves legacy behavior

When the `[snapshot]` table is absent:
- Current behavior is preserved completely: existing configurations and tests remain valid.
- Normal builds and deployments continue to resolve dependencies from HEAD-derived local checkout state.
- Ephemeral CLI `--source-revision KEY=REV` overrides project into lock-backed dependency nodes as before.
- If `--source-revision` targets the selected top-level flake, its commit is validated against the observed checkout commit and excluded from candidate lock projection (preventing invalid lock node projection).

### CLI override precedence

Operators can supply one or more `--source-revision KEY=REV` flags alongside an active
declarative snapshot.
- CLI overrides take precedence over the configured snapshot for the specified key(s).
- Unspecified keys continue to resolve to their declarative snapshot revisions.
- The provenance of each effective revision (`"snapshot"` vs `"cli"`) is recorded in
  the deployment plan.
- The operator is notified of the effective resolution during workflow execution:
  `nix-update: source snapshot: 9 configured exact revisions (1 CLI override)`

### Selected top-level source handling

The selected flake repository (e.g. `composition-private-nd`) is matched in the
configured source catalog:
1. **Verification before qualification**: Before any candidate projection or qualification
   begins, the selected repository's local Git commit must match its declared snapshot
   revision (or CLI override). If the local checkout commit differs, the command fails
   closed immediately.
2. **Exclusion from candidate projection**: The selected flake is the root of the local
   dependency graph rather than a node in its own `flake.lock`. Its revision is validated
   directly against local Git authority and excluded from `lock_backed_revisions` passed
   to `project_candidate_lock()`. Only lock-backed dependency sources are projected into
   the candidate `flake.lock`.

### Candidate lock precedence

Every effective lock-backed source revision is explicit authority. Projection
resolves all explicit sources against the original candidate lock and protects
those nodes and their dependency subtrees from ancestor lock propagation. Each
explicit source supplies its own immutable Git metadata and committed dependency
records. Unpinned owned children retain complete scheme-specific locked records;
input edges establish correspondence even when Nix renamed an inner node.
Composition-level follows overrides remain unchanged.

Projection is independent of source mapping order. Ambiguous repository matches,
incompatible authorities for a shared node, unprojected outside owners, and
unprovable dependency topology fail before writing the candidate lock. Changed
input topology requires a separately regenerated composition lock. A final
structural check verifies every explicit revision and its materialized metadata.
Saved deployment locks must also contain every effective exact revision before
source realization or qualification.

### Scope of snapshot authority

Declarative source snapshots apply to standard build and deployment pathways:
- `nix-update build`
- `nix-update deploy`
- `nix-update deploy --dry-run`
- `nix-update deploy --plan-only`
- `nix-update deploy --plan <dir>` (replay validation)

Note: `nix-update advanced prepare` operates on explicit deployment contract inputs and does not consume `[snapshot.sources]`. Promotion commands maintain explicit input/revision authority and remain decoupled from the deployment snapshot.

### Plan schema `nix-update-local-plan-v3` & replay invariants

When a source catalog is active, deployment plans record full snapshot evidence under
schema `nix-update-local-plan-v3`:
- `source_snapshot.active`: Boolean indicating whether snapshot mode is active.
- `source_snapshot.configured_revisions`: Mapping of configured snapshot revisions.
- `source_snapshot.effective_revisions`: Mapping of effective revisions.
- `source_snapshot.provenance`: Per-key origin (`"snapshot"` or `"cli"`).
- `source_snapshot.selected_source`: Key and revision of the selected top-level flake.
- `source_snapshot.lock_backed_revisions`: Revisions projected into `flake.lock`.

During `--plan` replay (`nix-update deploy --plan <dir>`):
- Replay operates in a fresh, independent scratch workspace.
- Replay fails closed if snapshot intent changed (e.g. plan was v2/un-snapshotted but
  replay environment has active snapshot, or vice-versa).
- Replay fails closed if configured snapshot revisions, effective revisions, selected
  source authority, revision provenance, or lock-backed revisions drifted between plan
  creation and deployment switch.
- Source authority revalidation (`sources == loaded_doc.get("sources")`) confirms
  that all locked input revisions, NAR hashes, store paths, URLs, and canonical
  git identities are byte-for-byte identical to the reviewed plan. Because
  `sources.selected_flake` durable identity binds to the canonical operator-selected
  path (`selected_flake_identity`) rather than ephemeral scratch directory paths,
  valid replays across independent scratch lifetimes succeed without false source-authority drift.
- Replay switches the freshly evaluated scratch target, preserving identical build
  and switch authority.

### Desired operator UX

With a complete `[snapshot.sources]` table in `nix-darwin.toml`, the operator runs:

```text
nix-update build --flake ~/.local/nix-darwin/composition-private_dinas
nix-update deploy --flake ~/.local/nix-darwin/composition-private_dinas
```

with **zero** `--source-revision` CLI flags required.

Operators can inspect the effective status at any time using the read-only command:

```text
nix-update snapshot status
```

## Declarative snapshot adoption transaction (`nix-update snapshot prepare|apply|status`)

`SNAPSHOT3` introduces a bounded, reviewable, two-phase transaction for adopting
promoted source revisions into the operator's canonical `[snapshot.sources]`
table in `$HOME/.local/nix-darwin/config/nix-darwin.toml`, replacing manual file
editing with native DINAS control primitives.

### Operator workflow

```sh
# 1. Prepare an adoption plan from a completed promotion campaign (or explicit pins)
nix-update snapshot prepare [--campaign <campaign-dir>] [--pin <key>=<commit> ...]

# 2. Inspect the proposed adoption plan before applying
nix-update snapshot status --plan ~/.local/state/nix-update/snapshot-adoption-plans/plan-...

# 3. Apply the reviewed adoption plan (attended TTY confirmation required)
nix-update snapshot apply ~/.local/state/nix-update/snapshot-adoption-plans/plan-...

# 4. Verify that live repository HEADs now align with declarative snapshot pins
nix-update snapshot status
```

### Transaction contract and invariants

1. **Source Pin Authority**:
   - Pins are extracted either from a completed promotion campaign journal
     (`campaign.json` v2) or explicit `--pin <key>=<commit>` CLI options.
     The `--campaign` and `--pin` options are strictly mutually exclusive.
   - When no `--campaign` or `--pin` is supplied, `prepare` automatically detects
     the unambiguous single matching indexed campaign for the target flake from
     `~/.local/state/nix-update/promotion-campaigns/` (or requires `--campaign <PATH>`
     if multiple campaigns exist).
   - Campaigns must be in an authoritative terminal state (`LOCAL_COMPLETE_AWAITING_ACTIVATION`,
     `COMPLETE_NO_ACTIVATION`, `AWAITING_ACTIVATION`, or `AWAITING_DEPLOYMENT_RECONCILIATION`)
     to be consumed.

2. **Local Ancestry Verification**:
   - Every proposed snapshot pin must resolve to an existing local Git commit in its
     cataloged source repository.
   - Crucially, every proposed pin must be an ancestor of the source's configured
     release ref (`release_ref`), verified via `git merge-base --is-ancestor`.
     Pins that have diverged from the release branch fail closed.

3. **Catalog Integrity and Incomplete Snapshot Allowance**:
   - Proposed pins must belong to configured `[sources]` catalog keys; unknown keys
     fail closed.
   - `prepare` and `status` invoke configuration parsing with `allow_incomplete_snapshot=True`,
     preventing deadlock when catalog keys are introduced before being snapshotted.
   - When proposed pins match the current `[snapshot.sources]`, `prepare` records an empty
     `changed_pins` mapping; upon `apply`, the configuration is verified and confirmed
     to already match the candidate postimage without modifying config bytes.

4. **Plan Structure and Integrity Digest**:
   - Adoption plans are stored in an owner-private state root (`~/.local/state/nix-update/snapshot-adoption-plans/plan-{timestamp}-{digest[:16]}/`).
   - The plan directory contains:
     - `plan.json`: structured metadata adhering to schema `nix-update-snapshot-adoption-plan-v1`.
     - `preimage.toml`: exact pre-adoption configuration bytes.
     - `candidate.toml`: exact candidate postimage configuration file.
     - `plan.md`: human-readable plan summary and review guide.
   - `candidate_sha256` is recorded in `plan.json` and verified against `candidate.toml`
     immediately before application, preventing tampering between prepare and apply.

5. **Concurrency and Mutual Exclusion**:
   - `apply` acquires `DeploymentLock(state_root / "transaction.lock")` prior to validation
     and file replacement, preventing concurrent deployment, promotion, or adoption operations
     from corrupting configuration state.

6. **Attended TTY Confirmation**:
   - Applying a plan strictly requires explicit attended confirmation typed on `/dev/tty`:
     `APPLY NIX-UPDATE SNAPSHOT <digest>`.
   - Non-interactive or headless execution without a controlling terminal fails closed
     with `TerminalUnavailableError`.

7. **Atomic Replacement and Backup**:
   - Prior to modifying the target configuration, `apply` writes a timestamped backup copy
     and metadata under `~/.local/state/nix-update/snapshot-backups/nix-darwin-{timestamp}-{preimage_sha256[:16]}.toml`
     and `.json`.
   - The candidate file replaces the target atomically via `_atomic_replace`.
   - The target path must not be a symlink and must have `st_nlink == 1`; file permissions
     and ownership are preserved.

8. **Consumed Authority**:
   - Upon successful application, `apply` writes durable consumption authority to
     `~/.local/state/nix-update/snapshot-consumed/{plan_digest}.json` to mark the plan
     consumed, preventing replay attacks.

9. **Operational Decoupling and Historical Recovery**:
   - Snapshot adoption touches only `$HOME/.local/nix-darwin/config/nix-darwin.toml` and
     state root records. It never modifies Git repositories, switches Darwin profiles,
     triggers host activation, or modifies composition locks.
   - Rollback of a Darwin host generation (`nix-update rollback`) changes `/run/current-system`
     symlinks and does not touch `nix-darwin.toml`.
   - SNAPSHOT2 historical lesson: an older host generation may require its corresponding
     historical operator config when parser or source semantics differ. In that event,
     the operator config backed up in `snapshot-backups` can be restored alongside the
     rollback generation.

## Operator adoption handoff

1. After source acceptance, identify each checkout from its existing explicit
   operator mapping and each portable repository/ref from its contract and lock.
2. Prepare a complete catalog before adding the first `[sources.<key>]` record
   to the operator control TOML. That first record switches every new prepare
   and campaign to local authority: catalogue every promotable input, including
   registered public/private module inputs, core, and every composition in each
   chain. A partial catalog blocks uncatalogued inputs; it does not fall back to
   remote mode. Remove migrated `promotion.checkouts` entries. Do not copy this
   example over a full configuration or infer checkout paths from symlinks.
3. Keep publication subtables absent for a provider-free local workflow, or
   supply an existing configured remote name and normalized destination ref.
4. Ensure the selected local release refs resolve to the intended exact commits.
   Prepare and review a fresh local-authority plan before a separately authorized
   promotion. Historical plans and failed journals remain retained.

This source phase does not edit the real operator configuration, promote real
composition locks, activate the host, mutate local origin, or run the new
publication machinery against real repositories. Agent-Central activation and
the retained historical journals are untouched. NIX-ENV1 remains in core ancestry
and is not separately promoted here. Dispatcher publication of the accepted core
source is separate from module/composition publication. ROOT1 is the
structural milestone flattening product-tracked files to root, transitioning the
canonical checkout to `$HOME/.local/nix-darwin/darwin-is-not-a-snowflake` and
establishing the attended cutover path for downstream composition lock removal
of `?dir=nix-darwin` (see `docs/ops/dinas-root1-cutover.md`). DINAS-REMOTE1 adds
narrow core authorization for migrating the public composition remote identity
from `ssh://git@github.com/Knowledge-Forge-AI/.flakes.git` to
`ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git`.
Because `local_source_binding.validate_local_sources` compares the locked URL to
`[sources.core].repository`, the operator source-catalog identity cut must
precede the composition lock URL migration.
