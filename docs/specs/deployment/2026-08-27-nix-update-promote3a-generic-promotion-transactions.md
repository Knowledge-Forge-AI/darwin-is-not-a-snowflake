# Generic composition promotion transactions

> [!NOTE]
> **Historical Design Record**: This document records the original August 2026
> design for generic composition promotion transactions. The following
> normative assumptions in this spec have been superseded by subsequent DINAS
> architecture:
> - **Local Promotion and Decoupled Publication**: `nix-update promote apply`
>   produces a local `flake.lock`-only commit (`LOCAL_COMMITTED`) without
>   performing network pushes. Explicit dependency-ordered publication is handled
>   separately via `nix-update promote publish`. See
>   [Operator-local Git source authority](dinas-local-sources.md).
> - **Native Campaign Coordination**: Sequencing across composition layers is
>   owned natively by DINAS via `nix-update promote campaign run|resume|status`
>   rather than an external coordinator.
> - **Deployment Version Authority**: Deployment source selection is governed
>   by `[snapshot.sources]` in `nix-darwin.toml` (`SNAPSHOT2`), removing the
>   ref skew / composition lock convergence dependency for deployment readiness.
> - **Current Status**: See `docs/status/2026/09/18/00001-nix-darwin-promote4-reconcile-and-completion.md`.

Status: historical design record superseded by DINAS native campaign coordination,
operator-local source authority, and declarative source snapshots. No real
promotion or host activation is authorized by this document.

## Ownership and boundary

`nix-update promote` owns one selected composition's lock-only qualification,
reviewed plan, exact lock installation, commit, and fast-forward publication.
`nix-update prepare/apply` remains the separate host-deployment and activation
transaction. A higher-level coordinator may sequence successful promotion
transactions, but it does not generate or install locks, commit, push, or
activate. Core names no domain or non-public composition.

A selected composition exposes its native interface directly as
`lib.promotion`. Core accepts base and layered structural dialects. A layered
record additionally contains `requiredLowerPromotion`, `lowerPromotion`,
`combinedModules`, and `semantics`. The exact `schema` string is an opaque
safe identity bound to the plan; it does not select a hard-coded repository
protocol. Version 1 remains byte-compatible and implies `refs/heads/main` for
every promotable input. Version 2 adds exactly:

```nix
releaseChannels = {
  policy = "explicit-head-refs-v1";
  refs = {
    direct-input = "refs/heads/deploy";
  };
};
```

Version 1 rejects `releaseChannels`; version 2 requires it. Version-1 lock
records normalize an omitted ref, short `main`, or full `refs/heads/main` to
the same implied main channel; every other ref is rejected. The ref map's keys
exactly equal `promotableInputs.names`, and each value is a normalized full
`refs/heads/*` ref. Tags, detached revisions, remote-tracking and symbolic
refs, refspecs, wildcards, control characters, `HEAD`, `..`, `@{`, duplicate
separators, invalid leading/trailing components, and `.lock` suffixes are
rejected. A layered contract owns release refs for its own promotable inputs,
including the required lower-composition input; its embedded lower contract is
recursively validated at its own independent version.

Both dialects have exact top-level and nested field sets.
Layered validation recursively validates the lower contract, exact module
concatenation, uniqueness and disjointness, system/host/package-set agreement,
the exact promotable lower candidate, and explicit non-activation and lock
selection semantics.
An exact `local-only` module URL is accepted only for a module whose
`ownsMachineLocalConfiguration` flag is true. Such a module cannot be
promotable or resolved as a candidate repository. Multiple distinct
machine-local modules are valid; their names, inputs, and checkouts remain
unique, while the non-repository `local-only` sentinel does not participate in
repository-URL uniqueness across one contract or layered contracts.

For a direct input, the candidate URL comes from exactly one matching module. A
layered contract may instead resolve its required lower input to the embedded
lower composition repository URL. The selected resolution rule is plan bound.
Nix URL identity and Git transport identity are normalized separately:
`git+ssh` maps to `ssh`, `git+https` maps to `https`, and only the explicit
`git` SSH username is accepted. A terminal `.git` suffix is equivalent to
Nix's suffix-free resolved repository identity, while the exact declared URL
remains plan bound. Passwords, other usernames, query/fragment or
`dir` selectors, whitespace, and ambiguous forms are rejected. `file://` is
limited to explicitly marked isolated fixtures.

## Prepare

The operator runs:

```sh
nix-update promote prepare \
  --flake /absolute/composition-checkout \
  --input direct-input \
  --revision 0123456789abcdef0123456789abcdef01234567 \
  --output /absolute/new-owner-private-directory
```

The optional qualifier timeout is rejected by argument parsing unless it is in
`1..3600`. Under the global transaction lock, prepare requires clean
branch/upstream/remote authority, 0/0 parity, a clean index and complete
worktree, no active Git operation, and a new owner-only output outside every
reviewed repository. It evaluates `lib.promotion` without lock writes and
validates the qualifier as a current-user-owned executable single-link regular
file inside the selected repository.

The fixed qualifier invocation is:

```text
<entryPoint>
--composition <selected-root>
--input <input-name>
--candidate <exact-candidate-ref>
--json
```

The accepted native JSON result binds its opaque schema and optional version,
pass outcome, exact input and candidate, requested/resolved revision, canonical
resolved URL, and positive lock/tree identity proofs. Optional contract
compatibility and snapshot proofs must pass. Direct and nested check structures
are supported: every leaf must pass, with exactly one map matching the declared
check set. Hosts must exactly match the declared set, pass, and carry one
derivation identity. Complete accepted native results remain owner-only up to
10 MiB; plans contain only bounded digest-bound summaries.

If more than one nested map exactly matches the declared check-name set, the
result remains ambiguous and fails closed. No generic discriminator is proven
by the version-1 native contract, so resolving that ambiguity is deferred to a
future contract version rather than guessed from repository-specific shape.

Every authorization-relevant Nix observation uses one canonical promotion
snapshot identity:

```text
reviewed repository commit and tree
+ selected flake path relative to the repository root
+ exact lock SHA-256 and byte count
+ git-archive-exact-tree-bound-lock-v1 materialization policy
```

Those values derive from existing plan-v2 repository, flake-file, lock, and
tool-closure bindings; the exact-key schema gains no redundant field. A
temporary absolute CWD is execution evidence only. The fixed `snapshot` leaf
comes from the reviewed Git-tree archive, excludes `.git`, untracked, and
ignored content, rejects unsafe paths and metadata, receives the exact bound
lock bytes, and is removed on every exit.

Prepare requires exactly one `git ls-remote <transport-url>
<plan-bound-release-ref>` result equal to the requested revision. It never
consults the remote default branch and never advances a release branch. It
materializes the reviewed commit in one private temporary root and runs
exactly:

```text
nix flake update <input>
```

The selected direct lock node binds the contract, flake declaration, release
ref, repository, and requested revision. For version 1, absent lock refs or the
existing full `refs/heads/main` representation remain accepted. Version 2
requires both `original.ref` and `locked.ref`; installed Nix writes an explicit
`deploy` branch as short `"deploy"`, so that fixture-proven spelling and the
equivalent full `"refs/heads/deploy"` spelling normalize to the declared ref.
Version 2 never accepts an omitted ref, including for an explicitly declared
`refs/heads/main`.

The retained candidate must select the requested revision, plan-bound release
ref, and canonical URL.
Promotion resolves the selected root node through the exact lock input-path
resolver. Its owned closure is that node plus every node reachable through
string-valued input edges. List-valued follows are resolved and bound as shared
references but do not expand ownership. Reverse string-edge indexes prove that
the closure is exclusive: only one exact root edge may enter the selected node,
and no other closure node may have an incoming edge from outside the closure.

The lock root, version, root node, and every node outside the union of the
preimage and candidate closures remain exact. Added nodes belong only to the
candidate closure, removed nodes only to the preimage closure, and changed
nodes only to their union. The selected root keeps its original repository,
release reference, and flake-root selector. Common closure follows retain both
their exact path and resolved target. An added owned node may follow an
unchanged outside shared node, but may not capture another owned or changed
node. Cycles, unresolved references, shared children, outside capture, and
noncanonical graphs fail closed.

Every added or changed owned source must carry a Nix-generated immutable
content identity. Credential-bearing URLs, unresolved indirect sources,
mutable paths, non-store locked path sources, and unsafe selectors are
rejected. Hashed immutable remote file/archive URLs may retain ordinary query
strings. The complete native preimage and candidate lock bytes remain retained
evidence. A second reviewed tree receives the exact retained candidate bytes.
The same qualifier, every declared check, and every declared host system are
realized without lock writes; exact host derivations are recorded. Contract,
qualifier, check, and host evaluation for entry and candidate locks all occur
in canonical snapshots. Literal host selection uses
`darwinConfigurations."<host>".system[.drvPath]` after closed host validation,
so a dotted host cannot resolve as a nested attribute path. Candidate
qualification still builds every declared host, then uses that observer to
capture the exact candidate derivation identities.
Candidate/archive temporary roots are removed on success and failure.

The canonical `nix-update-promotion-impact-v1` record binds exact baseline and
candidate maps, a sorted changed-host list, and one derived classification:

```text
HOST_DERIVATIONS_IDENTICAL -> NO_HOST_CHANGE
HOST_DERIVATIONS_CHANGED   -> HOST_CHANGE_REVIEW_REQUIRED
```

`activationPerformed` is always false. Host-set drift is invalid evidence,
not a change classification. The record is neutral: an intermediate
composition's result says nothing about completion or activation of a
multi-stage campaign.

A `READY_FOR_REVIEW` `nix-update-promotion-plan-v2` plan binds complete canonical
contract bytes and digest; contract/qualifier identities; input resolution;
canonical Nix and transport URLs; revision and release-ref proof; exact lock
preimage/candidate bytes and normalized graphs; the canonical owned-closure
authorization, outside-graph and shared-follows digests, and retained lock
authorization evidence; native result artifacts and summaries; locked
qualification evidence; baseline observation commands and host map; candidate
host map; canonical impact record and digest; publication/recovery policy; and confirmation
authority. Version-1 plans are structurally inapplicable after the closure
semantics change. Tool identity covers `bin/nix-update`, the
complete current-user-owned non-symlink single-link regular-file source closure
under `libexec/nix_darwin_local/`, and exact external Git and Nix executables.

`BLOCKED` and `ALREADY_SELECTED` are structurally inapplicable to apply. An
already-selected disposition still performs the full native qualifier, declared
check and host realization, exact lock and release-ref validation, identity
revalidation, identical baseline/candidate host maps, and complete
selected-repository non-mutation proof, retaining digest-bound evidence.

## Apply and publication

### Ordinary operator-local promotion v4

With operator-local catalog authority, new ordinary promotions use
`nix-update-promotion-plan-v4`. The selected candidate and its owned lock closure
remain subject to exact configured repository/ref and requested-revision checks.
The strict set is derived from the lock graph even for `ALREADY_SELECTED`;
equal preimage/candidate bytes never make the selected source unrelated.

Only complete unchanged nodes outside that closure may use the versioned
`unchanged-lock` binding role. A
`nix-update-promotion-local-source-binding-v2` record distinguishes the strict
unmaterialized source observation at current release head H from realization of
exact locked revision L, store path, and NAR hash. L must equal or be an ancestor
of H in history without replacement refs, grafts, or shallow boundaries. The
configured checkout HEAD must be attached to its release ref or detached at H;
tracked product drift and active Git operations remain prohibited.

The canonical plan digest binds both identities. Revalidation recomputes role
eligibility from the authorized graphs and requires the exact reviewed H and
exact L/NAR realization before installation and at commit-authority boundaries.
Additional ref advances require fresh preparation. Divergence, repository/ref
mismatch, and an attempt to route the selected candidate through `unchanged-lock`
fail closed. Source observation state never expands the one-input lock theorem.

This permits serial convergence of multiple advanced module release refs, in
either order, while preserving each unrelated historical lock selection. Core
may itself be an unrelated advanced source. Retained v2/v3 plans preserve their
original interpretation and replay policy without migration; result v2 is
unchanged. Deployment and both repository-transition schemas remain strict and
can still block on ref skew. An intermediate `READY_FOR_REVIEW` promotion is
therefore not a deployment-readiness claim. See
[operator-local source authority](dinas-local-sources.md#serial-promotion-with-advanced-release-heads).

### Transaction checks

Apply accepts only `APPLY NIX-UPDATE PROMOTION <64-hex-plan-digest>`. There is no
flag or environment bypass. It validates canonical owner-only plan and artifact
bytes and recomputes the exact owned-closure theorem before consuming one-shot
authority. It repeats the closure proof again immediately before staging, in
addition to Git, tool-closure, contract, qualifier, candidate, release-ref,
process, and replay checks. Before mutation it re-observes and matches the
baseline host map. It installs the exact retained lock through the journaled
atomic installer, repeats locked qualification in a canonical candidate-lock
snapshot while retaining exact selected-checkout lock assurance, and
matches candidate host identities and the derived impact record.

Immediately before staging and again immediately before commit, apply requires
the expected branch/upstream/parent and remote parent, clean index, a worktree
change set of exactly `flake.lock` for ordinary promotion (or exactly `flake.nix`
and `flake.lock` for either explicit repository transition), exact candidate bytes,
the unchanged contract or its separately authorized repository transition,
qualifier and tool identities, unchanged candidate release ref, unchanged
candidate host impact, and no competing mutation process. Pre-commit impact or
other authority drift restores and verifies the exact lock preimage and stops.
Only the plan's validated `changed_paths` may enter the index: `flake.lock` for
ordinary promotion, or `flake.nix` and `flake.lock` for either repository transition.
Apply creates one
ordinary commit and makes one ordinary fast-forward push. It never resets,
amends, rebases, retries an uncertain push, or erases a created commit.

Existing terminal publication and recovery states remain authoritative,
including `COMMITTED_NOT_PUBLISHED`, `PUBLICATION_INDETERMINATE`, exact-preimage
restoration outcomes, and replay prevention. Re-entry after authority
consumption reconciles existing evidence; it never starts a second mutation.

No promotion path invokes `sudo`, `darwin-rebuild`, host profiles, Homebrew,
local configuration publication, deployment `prepare/apply`, or activation.

## Attended repository transition extensions

Generic promotion rejects arbitrary changes to repository URLs or flake declarations.
Two narrow, non-generic transition extensions are provided for attended repository cutovers:

### 1. Core repository identity transition
- CLI flag: `--allow-core-repository-transition` on `nix-update promote prepare`.
- Target: Migrates `core` input declaration and lock node from `.flakes` to `darwin-is-not-a-snowflake`.
- Plan schema: `nix-update-promotion-core-repository-transition-plan-v1`.
- Authorization schema: `nix-update-core-repository-transition-v1`.
- Preimage URL: `ssh://git@github.com/Knowledge-Forge-AI/.flakes.git`.
- Candidate URL: `ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git`.

### 2. Physical public-composition repository transition
- CLI flag: `--allow-composition-repository-transition` on `nix-update promote prepare`.
- Target: Migrates the physical public-composition repository URL from `composition-nd` to `composition_dinas`,
  while strictly preserving the logical Nix input key `composition-nd`.
- Plan schema: `nix-update-promotion-composition-repository-transition-plan-v1`.
- Authorization schema: `nix-update-promotion-owned-repository-transition-v1`.
- Preimage URL: `ssh://git@github.com/Knowledge-Forge-AI/composition-nd.git`.
- Candidate URL: `ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git`.
- Semantics: Valid only for input `composition-nd` and release ref `refs/heads/main`. Requires
  candidate source catalog entry for `composition-nd` pointing to local checkout `composition_dinas`
  and candidate remote URL. Combining core and composition repository transitions in one promotion
  transaction is prohibited and fails closed. Retains both declaration and lock candidate artifacts
  under `retained-lock/` (`flake.nix.candidate`, `flake.lock.candidate`, `delta.json`) and executes
  journaled dual-file preimage restoration on failure.

## Campaign handoff

A later coordinator may interpret host impact only for its declared final
composition stage:

```text
HOST_DERIVATIONS_IDENTICAL -> COMPLETE_NO_ACTIVATION
HOST_DERIVATIONS_CHANGED   -> AWAITING_ACTIVATION
```

`AWAITING_ACTIVATION` points to the existing top-level `nix-update
prepare/apply` transaction; it does not invoke it. The campaign still stops
before host mutation. `AWAITING_OPERATOR_OPERATION` is reserved for a later
first-class operation-prerequisite design and is not implemented by promotion
or this handoff.

## Verification policy

Synthetic tests cover both native dialects and contract versions, exact release
maps, prohibited ref forms, lock/ref mismatches, impact equality and changes,
host-set and impact drift, result shapes, malformed and ambiguous structures,
URL safety, result bounds, publication and recovery faults, and the
no-activation boundary. Installed-Nix integration proves an explicit deploy
input beats a different newer main, writes Nix's real branch-bound lock
representation, runs the production prepare path, and blocks a contract/flake
ref mismatch without a lock-command monkeypatch or synthetic lock rewrite.
Operational qualification requires
separate base and layered prepare-only canaries using new external owner-only
output directories. Neither canary authorizes apply, commit, publication,
deployment preparation, or activation.
