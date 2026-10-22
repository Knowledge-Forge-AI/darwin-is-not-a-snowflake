# Transactional `nix-update` Plan/Apply Design

Status: candidate implementation for manual review; no live deployment has
been performed.

## Safety boundary

`nix-update` separates four authorities:

1. `lock` performs explicit lock maintenance from an exact clean reviewed Git
   tree in an isolated transaction checkout.
2. `build` runs report qualification and a locked non-privileged build.
3. `prepare` collects blockers, builds the exact target, and writes an immutable
   owner-only deployment plan without publishing `eval/current`.
4. `apply` requalifies and consumes one reviewed plan after exact typed
   confirmation.

No-argument update-and-switch and `--switch-only` are rejected. A broad lock
update is available only as explicit `lock --mode all`; it cannot switch.
`--build-only` is a deprecated alias for `build`. `nix-rebuild PLAN` delegates
only to `nix-update apply PLAN`. Predecessor retention is mandatory rather than
an optional `--preserve-predecessor` mode. Store optimization and garbage
collection are never part of apply; `nix-update optimize` is a separate
operator action.

## Plan lifecycle and serialization

The machine-readable plan schema is
`nix-update-deployment-plan-v2` (v1 plans are explicitly rejected as incompatible with local repository authority). JSON uses UTF-8-compatible ASCII, sorted keys,
compact separators, and exactly one trailing newline. The digest is SHA-256 of
the canonical document with `plan_digest` set to the empty string. Apply
rejects noncanonical bytes, a digest mismatch, blockers, or a preflight-only
artifact.

Plan/result directories are mode `0700`; plan, Markdown, marker, and result
files are mode `0600`. Apply rejects ownership, mode, topology, or hard-link
drift. Secret-bearing key and value families, including credentials embedded in
remote URLs, are rejected from canonical evidence. Child stdout and stderr are
streamed to validated owner-only files with an 8 MiB retained-byte ceiling,
full-stream SHA-256 and size, and explicit overflow disposition. Plans and
results retain only those identities plus redacted 8 KiB summaries. Phase
labels flush before child output.

The plan records:

- tool/schema identity, creation time, status, digest, and confirmation shape;
- exact launcher/core Python bytes, resolved Git/Nix/sudo/rebuild executable
  identities, operator/host/platform identity, and hashes of private
  environment values;
- selected flake, host, local Git commit/tree/branch/status/operation_state/index_sha256/flake_nix/flake_lock/ignored_status_sha256/ignored_status_count (local-first; no remote URL, upstream ref, parity, or ls-remote required);
- exact lock bytes hash and normalized node graph;
- machine-control contract identity and private scratch projection inventory;
- source snapshot, VCS, NAR, override, and participant identities;
- report qualification and exact target-build records;
- target output and recursive closure inventory;
- selected profile, live system, retained generations, and closure delta;
- declared external state, typed probes, controls, participants, and rollback
  policy.

Prepare status `READY_FOR_REVIEW` means only that no discovered blocker remains.
It does not authorize apply. `--preflight-only` artifacts are structurally
inapplicable even when otherwise ready.

## Lock discipline

`nix-update lock` requires a Git repository with a clean worktree, clean index,
no untracked or conflicted paths, no merge/rebase/cherry-pick/revert/bisect/
sequencer state, an exact branch/upstream/remote, `0/0` parity, and a canonical
remote branch at the same commit. It records commit, tree, branch, upstream,
remote, parity, index digest, and exact `flake.nix`/`flake.lock` identities.
The candidate is materialized with `git archive` from that reviewed commit,
never by copying possibly dirty filesystem bytes. The candidate lock is parsed
into a sorted normalized node graph and compared with the original.
Targeted and locked modes reject movement outside the exact authorized nodes,
including node metadata such as path and timestamp fields. Root or lock-version
movement is represented as `$lock-metadata` and also requires exact authority.

Targeted input paths are structurally resolved through root aliases and
`follows` references to actual normalized lock nodes before authorization. The
candidate lock bytes and semantic delta are retained as mode-`0600` evidence.
`--install` is a separate explicit choice. Before replacement, the
implementation writes and fsyncs a durable `rename-pending` journal, revalidates
the complete Git authority and lock preimage, writes an exclusive same-directory
temporary, fsyncs it, atomically replaces the lock, and fsyncs the directory.
Any failure before completed installation restores the exact preimage and
records `preimage-restored`. The Git index is never rewritten. Broad update is
never an implicit compatibility behavior.
The authoritative and candidate `flake.nix` and `flake.lock` must each be
single-link regular files; symlinked or hard-linked authority files block before
the isolated command runs.

## Path inputs and exact source identity

A Git commit, Git archive byte stream, and Nix path NAR are independent
identities. A path-input lock's `narHash` is validated with Nix path semantics;
it is not compared with the SHA-256 of `git archive`. Version 1 accepts only
sources whose exact root is already immutable in `/nix/store`:

- `store-path` for an already realized immutable Nix store source;
- `immutable-snapshot` for an operator-reviewed snapshot first materialized in
  the Nix store and then bound by exact store path and NAR.

Every exact source names the flake input it owns and the exact normalized
`flake.lock` node that input resolves to. The node's `locked.narHash` must equal
the declared and observed NAR; aliases and `follows` paths must terminate at
that same node. Prepare either verifies a
matching configured `--override-input` or adds the exact `path:` locator itself;
an undeclared configured override, duplicate input, or path disagreement blocks.
The resulting override vector is the one used for allowlist evaluation, checks,
build, revalidation, and switch.

Mutable or reconstructive `path:` authority is not silently accepted. If the
exact source that produced a locked NAR is unavailable, prepare records an
actionable blocker. It never substitutes local HEAD, an archive, a timestamp,
or a newly rotated NAR.
Git commit and locator provenance may accompany the operator handoff, but version
1 does not treat a mutable Git checkout as apply authority.

## Scratch projection

Prepare first validates every typed control transaction against the current
control root, copies that root into private scratch, and overlays only reviewed
postimages. It then evaluates `configAllowlist` using no-update/no-write lock
flags and renders allowlisted TOML values from that virtual post-transaction
tree. Files are exclusively created at `0600`; directories are `0700`; every
name and SHA-256 contributes to the recorded projection digest.
Unknown TOML files, unknown or missing allowlisted keys, malformed TOML,
control-file projection, and unsupported values fail closed.

The same retained projection is used for report/check qualification, both host
builds, target closure capture, apply target revalidation, optional
`localConfig` publication, and the single switch. Neither prepare nor build
publishes machine-local `eval/current`. Apply uses
the exact retained scratch path bound in the plan and independently rechecks
its owner-only topology, complete file inventory, bytes, and digest. Publication of a canonical
machine-local generation is reserved for a separately declared and reviewed
transaction; it is not an incidental qualification side effect.

## Typed control transactions

Each `control_transactions` entry binds one lexically normalized absolute path
under an explicit reviewed control root, its complete lexical ancestor chain,
regular-file topology, owner/group, link count, preimage bytes and SHA-256,
postimage bytes and SHA-256, pre/post modes, metadata policy, authorization
phase, and restoration rule.
Transaction paths are unique and non-overlapping so journals and restoration
ownership cannot collide. Canonical base64, lower-case 64-hex hashes, exact
field types, safe modes, valid TOML postimages where applicable, complete
current prestate, and exact virtual poststate are pure prepare-time gates. The
plan records actual current and projected postimage identities.

After confirmation, the transaction first writes and fsyncs a durable
`rename-pending` marker. It then writes an exclusive same-directory temporary,
fsyncs the file, applies the reviewed metadata, atomically renames it, fsyncs
the directory, and advances the marker. Pre-switch failure restores the exact
preimage and metadata and advances the journal to `preimage-restored`. Restore
first proves the installed postimage's regular-file topology, owner/group, mode,
link count, and bytes; concurrent drift blocks rather than being overwritten.
Core records each transaction as restoration-owned before invoking its atomic
installer, so a failure after rename but before the final journal/fsync still
enters exact recovery. Version 1 supports only `preserve-owner-group`; unknown
or unimplemented metadata policies are rejected. Operator-controlled xattrs,
ACL entries, or nonzero flags block. macOS kernel-managed provenance is
explicitly recorded but outside transaction ownership. An already-current
postimage produces an effective no-op contract derived from that postimage and
sets restoration false, so stale old bytes cannot be restored.
Immediately before `sudo`, apply durably sets
`switch_invocation_started=true`. From that point onward core performs no
automatic control restoration and no second system mutation.

The acceptance fixture uses a generic TOML control file with the APGR-shaped
transition from a regular, single-link `0644` preimage containing two agents to
`agents = []` at `0600`. Project names and paths are not embedded in core.

## State probes and volatile entries

File probes bind type, owner, group, mode, link count, size, bytes, and SHA-256.
Every tree probe separately binds its lexical root path, root kind, owner,
group, mode, link count, symlink text when applicable, and resolved target.
Its content identity separately binds every relative entry, descendant symlink
topology, regular-file content, and the complete declared volatile policy. The
canonical complete digest binds both identities. Two aliases may share a content
digest, but they never share a root-topology identity. A volatile entry requires
an exact normalized safe relative path, kind, owner, group, mode, producer, and
content SHA-256. There is no ignore glob.

The marker acceptance fixture proves that aliases exposing the same skills tree
classify the declared marker content consistently while preserving independent
root identities. Alias retargeting, changed symlink text, root replacement or
metadata drift, and any undeclared descendant change the complete identity;
wrong marker content, type, owner, or mode blocks immediately.

## Concurrency and process gate

Prepare, lock, and apply share a nonblocking owner-only advisory lock. The
process gate obtains separate PID/PPID-framed command and accounting-command
projections, then joins accounting identity only when both PID and PPID match.
PID and PPID are the only whitespace-delimited prefix fields in either
projection, so an accounting command containing spaces cannot shift executable
or argument identity. Failure to reconstruct argv retains the structured
accounting identity. The gate blocks actual lock/update, apply,
activation/rollback, and profile-generation mutation commands at every
recognized executable path, including other store outputs, current-system
paths, temporary or older installations, Bash/sh/Python interpreter targets,
and nested `sudo`/`env` wrappers. Exact paths identify the current ancestry and
declared participant adapters; they never whitelist a competing mutation.
Wrapper parsing is operand-aware for installed short and long forms, accepts
long equals and separated values, honors `--`, processes environment
assignments, distinguishes bare sudo `-h` help from `-h host`, and never
promotes an option operand to the wrapped executable. Unknown or truncated
wrapper option sequences are typed fail-closed matches, so option-table drift
cannot silently remove a structured wrapper record from the process gate. Nix
command-boundary parsing covers installed
help-declared global, logging, and configuration-setting arities plus
`--option name value`. An unknown leading Nix option is a typed fail-closed
match; terminal `--help` and `--version` forms remain non-mutating. Failure to
reconstruct arguments is classified as a resolved command, a mechanically
terminal non-mutating invocation, an ambiguous sensitive wrapper or command
position, or an innocent invocation. Direct sensitive accounting identities
and ambiguous or identity-free records fail closed with typed reasons,
including parse-error records whose argv field is absent or non-iterable.

Interpreter parsing uses installed invocation arity rather than discarding every
leading option. It consumes Bash/sh `-O`, `-o`, `+O`, and `+o` operands, Python
`--check-hash-based-pycs`, `-W`, and `-X` operands, documented zero-operand
forms and option termination. Unsupported, attached-only-invalid, and truncated
forms remain ambiguous and blocking. Shell and Python `-c` source stays
terminal at the interpreter boundary and is never searched for mutation text.
The gate does not substring-match prompt text, basename lookalikes, or ordinary
services. Launcher and interpreter families beyond `sudo`, `env`, Bash/sh, and
Python are outside this gate's current structured classification contract.

## Exact target and profile split

Prepare runs the selected flake's exact regular executable `nix-test --report`
when that path exists; a present symlink, non-file, or non-executable hook is a
blocker rather than an implicit fallback. When the selected source has no hook,
the first qualification is a locked `nix flake check`. Prepare then runs a
second exact-projection `nix flake check` and every declared host build. Report
artifacts are directed into a new owner-only directory under retained private
scratch, never into the selected source. Every Nix qualification command uses
both no-update and no-write lock flags. The active host output uses
`--no-update-lock-file`, `--no-write-lock-file`, exact
overrides, and the scratch projection. It inventories the recursive closure and
the complete live-to-target delta. Unapproved movement blocks.

Apply repeats target build and closure validation before requesting authority,
then repeats mutable identity checks after confirmation. All target builds and
closure enumerations finish before the final attestation for each privileged
transition. That final sequence is fixed: exact mutable-state revalidation,
structured process gate, a newly written target-aware context, read-only
participant preflight, durable transition marker, and the one reviewed
privileged command. After the final participant attestation, only bounded durable
marker/evidence writes occur before privilege; there is no build, closure walk,
network operation, sleep, or unrelated child process. A reviewed localConfig
publication changes the expected pointer from the recorded preimage to the exact
projection generation for every subsequent check; it is never compared to the
obsolete pre-publication pointer. The process gate and participant preflight are
checked again immediately before each transition marker and privilege. After switching, repository, source,
lock, projection, control, participant, probe, and external identities are
revalidated before participant mutation and again before success. The selected system
profile, `/run/current-system`, and retained generation links are modeled
independently:

- aligned is accepted;
- split-retained requires a reviewed `realign-selected-profile` policy and
  exact safe generation;
- any other relationship blocks.

Realignment uses exact `sudo nix-env --profile ... --switch-generation`, never
`ln`. It
must change only the selected pointer, leave the live target unchanged, and
retain the reviewed safe and failed generations. Post-switch verification
requires selected and live targets to equal the exact reviewed output, permits
exactly one higher-numbered target generation, and preserves every reviewed
generation path, symlink text, resolved target, number, kind, owner, group,
mode, and link count. Renaming a generation or repointing a link blocks even if
the same store target remains reachable elsewhere.
Apply durably consumes one pre-switch mutation authority before realignment or
any other mutation. A failure before switch invocation restores the original
selected generation through the same Nix-managed interface and verifies the
full reviewed profile/live/retained snapshot. Any retained pre-switch marker
blocks replay of that plan, even when bounded restoration succeeded. Replay is
checked once before qualification and again while holding the mutation lock,
immediately before durable authority consumption.
A digest-keyed consumed-authority record under the private transaction state
root backs plan-local markers, so copying plan bytes cannot restore authority.

## Confirmation, switch, and result truth

The required input is exactly:

`APPLY NIX-UPDATE PLAN <64-hex-plan-digest>`

There is no production flag or environment variable that bypasses this text.
Immediately after the final state/process/participant attestation, apply writes
the durable switch marker and then invokes one direct `sudo darwin-rebuild switch` with the exact selected
flake, host, overrides, and no-lock-write arguments.
`HOMEBREW_NO_AUTO_UPDATE=1` is present for that child.

A nonzero child status is `POST_INVOCATION_FAILURE`; it is not evidence that no
activation occurred. Core performs no automatic rollback, control restoration,
second switch, GC, or optimization. `SUCCESS` is emitted only after profile,
control, probe, external-state, participant, and final-state verification.
Every terminal path writes structured private result evidence.

## Deployment participants

Optional participants use `nix-update-participant-v1`. Each declaration binds
an exact absolute regular executable, SHA-256, target-closure or reviewed-
operator-artifact provenance, closed phase list, mutation authority, and
independently safe rollback authority. Every declaration also carries an
integer timeout from 1 through 300 seconds. A timeout is a typed blocker.
Supported phases are:

- read-only `preflight`;
- `pre-switch` preparation;
- `post-switch-reconcile`;
- `post-switch-verify`;
- bounded participant-local `rollback`.

Core passes only a protocol token, closed phase token, and exact private context
path as separate argv. It never accepts shell text, an arbitrary command list,
or `eval`. Stdout must be one canonical strict JSON object and LF. Duplicate,
unknown, missing, noncanonical, or trailing data fails. Status/exit pairs are
`ok/0`, `blocked/20`, and `failed/21`.

Prepare builds the exact target and closure before participant preflight. It
writes one canonical owner-only context binding protocol/schema, plan-draft
identity, repository commit/tree/lock, host and operator environment, exact
target/closure, overrides and projection digest, source identities, control
pre/post identities, profile/live state, probes/external state, and the
participant declaration digest. Each participant echoes the context digest,
which is retained with an attestation digest. Apply reruns read-only preflight
against a newly written equivalent context before confirmation and again
immediately before each privileged transition. Prepare-time observations never
authorize apply. Rollback contexts are atomic owner-only files binding their
parent context, declaration, and opaque handle.

Participant names are unique. Target-closure provenance must equal the reviewed
target and the executable's store root must occur in its recorded closure.
Operator-artifact provenance must equal `sha256:<declared-sha256>` and bind an
exact reviewed private root and executable path. Every lexical directory from
that root to the file is a current-user-owned non-symlink exact-`0700`
directory; the source is an owner-only executable single-link regular file with
exact device, inode, metadata, size, and SHA-256. Prepare captures the reviewed
bytes through no-follow descriptor traversal, installs a SHA-named owner-only
copy under the private transaction state root, and records both source and frozen
identities. An operator artifact is a bounded shebang program naming one exact
absolute root-owned, non-writable interpreter and at most one absolute,
independently validated interpreter dispatch executable; relative `env`/`PATH`
dispatch is rejected. Immediately before spawn, runtime opens and revalidates the frozen
copy, retains that descriptor through process creation, and makes the validated
interpreter read the reviewed bytes through `/dev/fd`. Replacing the frozen
pathname after attestation therefore cannot select replacement bytes.
Target-closure provenance remains a distinct immutable-store contract and may
execute an immutable store binary directly. Read-only preflight may observe a declaration that has mutation
authority for a later phase; it cannot itself emit mutation state or a rollback
handle.

Participant-local rollback may act only when both the declaration and a
phase-bound opaque handle permit it. A participant with `pre-switch` or
`post-switch-reconcile` mutation authority must declare the rollback phase and
nonempty rollback authority bounded by its mutation authority. A mutating
reconcile attests `unchanged`, `mutated`, or `partial`; mutated and partial
results require a handle, while unchanged results forbid one. Core verifies the
originating target-aware context, declaration digest, and result phase, then
rolls back successful or partial reconciliations in reverse order if a later
reconcile, participant verification, or core verification fails. Rollback can
touch only that declaration's participant-local authority and can never request
a system rollback, second switch, DOCS15 mutation, or unrelated participant
mutation. Malformed JSON, timeout, status/exit mismatch, or failure after
mutation without a valid handle is `POST_SWITCH_PARTICIPANT_INDETERMINATE`;
rollback failure is `POST_SWITCH_RESTORE_INCOMPLETE`; complete bounded rollback
is `POST_SWITCH_RESTORED`.

## Private paths, process gates, and result truth

State roots, plan/result directories, lock files, journals, contexts, evidence
files, and temporaries use shared path primitives. Existing objects are accepted
only with the expected current-user ownership, kind, exact mode, and link count;
every existing ancestor is checked without following symlinks. A reviewed parent
must itself be a current-user-owned non-symlink exact-`0700` directory and the
new child must be its exact normalized lexical child. Parent identity is checked
again through a no-follow directory descriptor before creation. Unsafe parents
are rejected before mutation and never repaired with `chmod`. New files use
exclusive no-follow opens where the platform supports them, and atomic
replacements include file and directory fsync.

The process gate matches executable and argv structure rather than prompt
substrings. It recognizes direct commands, `sudo`/`env`, Python-version
interpreters, and `bash`/`sh` script targets for apply, Darwin activation, flake
lock/update, and profile-generation mutation. A `python -c` prompt mentioning a
command remains inert, as does shell `-c` source. Unparseable `sudo` and `env`
records recover command position only through known option arity; unknown or
truncated options fail closed rather than disappearing from the match set.

The post-confirmation lock acquisition is inside terminal-result handling.
Lock races, restoration failures, and evidence-write failures preserve the best
available owner-only record. A result write failure falls back to the private
state root with terminal state `EVIDENCE_WRITE_FAILURE`; after switch invocation
the tool never claims that unchanged live state proves no activation occurred.

## Recovery and compatibility

Plans, pre-switch/switch markers, command records, participant results, control journals, and
terminal result files are the recovery surface. A retained switch marker means
operators must inspect actual selected/live state; rerunning apply is not an
automatic retry mechanism. Disposition commands are informational and do not
run themselves.
Publication-pointer restoration likewise requires `eval/current` still to equal
the exact generation published by this transaction; foreign drift is preserved
and reported rather than clobbered. The expected published link is recorded in
recovery state before publication, so a failure immediately after rename is
still recoverable; an unchanged preimage is an exact no-op recovery.

Compatibility changes are intentional:

| Prior interface | Candidate disposition |
| --- | --- |
| `nix-update` | rejected; choose an explicit subcommand |
| `nix-update --build-only` | deprecated alias for locked `build` |
| `nix-update --switch-only` | rejected; use reviewed prepare/apply |
| `nix-rebuild` | rejected without a plan; `nix-rebuild PLAN` applies it |
| `update_first` | remains parsed for configuration compatibility but no longer causes implicit update |
| broad `nix flake update` | only explicit `lock --mode all` |
| emergency test command/skip | removed from deployment production interface |
| post-switch `nix-opt` | removed; explicit separate `optimize` operation |

The PATH-facing source-link installation contract remains unchanged:
`~/.local/nbin` exposes the repository `bin/` directory. New implementation
modules are non-executable source under `libexec/nix_darwin_local`.
