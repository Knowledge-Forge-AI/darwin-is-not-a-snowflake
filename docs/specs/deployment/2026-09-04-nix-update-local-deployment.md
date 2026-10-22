# Attended Local nix-darwin Deployment (nix-update)

Status: Implementation complete.

## 1. Context and Objective

`nix-update` previously required an external deployment contract (`deployment-contract.json`)
and a pre-created plan directory (`prepare` / `apply` two-stage workflow), which broke the
ordinary attended one-command update workflow (`nix-update`) for local composition checkouts.

This specification restores `nix-update` as a first-class, attended, local-only command that:
1. Qualifies and checks the configured active nix-darwin top-level flake (`flake.active`).
2. Builds the exact target system derivation without host mutation.
3. Derives source authority from `flake.lock` and store NAR hashes without external contract files.
4. Evaluates module-declared deployment effects (`options.dinas.deploymentEffects`), executing
   read-only preflights and routing mutating transactions to the advanced workflow.
5. Displays a concise, deterministic deployment summary detailing changes, closure movement, and package diffs.
6. Solicits operator confirmation interactively via `/dev/tty` (no environment or flag bypass).
7. Executes exactly one `darwin-rebuild switch`, records journaled evidence, and verifies live system status.
8. Preserves the contract-driven and participant-supervised workflow under `nix-update advanced prepare` and `nix-update advanced apply`.
9. Preserves compatibility for `bin/nix-rebuild PLAN` by delegating to `nix-update advanced apply PLAN`.

## 2. Command Namespace & Hierarchy

- `nix-update` (no arguments) or `nix-update deploy [OPTIONS]`:
  Attended local deployment of the configured active flake (`flake.active` or `--flake`).
  - `--dry-run`: Evaluate, qualify, build, and summarize without host mutation.
  - `--plan-only --output DIR`: Evaluate, qualify, build, write `nix-update-local-plan-v1` review artifact, and stop.
  - `--plan PLAN`: Apply an optional pre-generated local deployment plan after verifying its integrity and matching target system.
- `nix-update build [OPTIONS]`:
  Run qualification checks and non-privileged build without host activation.
- `nix-update lock [OPTIONS]`:
  Perform isolated explicit lock maintenance (`locked`, `targeted`, or `all`).
- `nix-update promote {prepare,apply} [OPTIONS]`:
  Transactional composition lock publication without host activation.
- `nix-update optimize [OPTIONS]`:
  Nix garbage collection and store optimization.
- `nix-update advanced {prepare,apply} [OPTIONS]`:
  Exceptional contract-driven deployment operations:
  - `prepare --contract CONTRACT --output DIR [--preflight-only]`: Creates a v2 contract deployment plan.
  - `apply PLAN`: Applies one reviewed v2 contract deployment plan with exact typed digest confirmation.
- Legacy `nix-update prepare` and `nix-update apply`:
  Refused with exit code 78 and explicit migration guidance directing operators to `nix-update` (normal) or `nix-update advanced` (contract).

### Ordinary and exceptional boundaries

Plans are optional for ordinary local deployment. `--plan-only --output DIR`
is available when an owner-private review artifact is useful, but the ordinary
lane does not require a pre-created plan directory or an external contract.
Contracts are exceptional and remain under `nix-update advanced
prepare/apply` for imperative participants, external mutable state, or
participant-local rollback. Closure deltas are evidence for the operator's
summary and journal, not a manually maintained path allowlist.

Lock-pinned Git and pinned local `git+file` inputs derive authority from
`flake.lock` and their realized store identities; they need no duplicate source
contract. Predecessor authority is derived from the live/profile state observed
immediately before mutation using the numbered-identity rules below. Remote publication and Agent-Central are not
normal deployment authority or runtime dependencies.

## 3. Module Effects Interface

Target compositions declare deployment-time constraints via `options.dinas.deploymentEffects`:
- Schema: `"nix-darwin-deployment-effects-v1"`
- Categories:
  - `preflightParticipants`: Read-only checks run during qualification before building.
  - `preSwitchTransactions`: File-system or state mutations requiring transactional supervision.
  - `postSwitchReconciliation`: Post-activation effects requiring external coordination.
  - `externalMutableStateProbes`: Environment state observations.
  - `participantLocalRollback`: Rollback handlers.

### Absence Handling
When a target composition does not declare `config.dinas.deploymentEffects` (such as legacy or
currently pinned compositions prior to core promotion), evaluation evaluates `c.dinas.deploymentEffects or null`.
Null/absent effects are treated as empty with a clear note in the deployment summary:
`deployment effects: none declared (dinas.deploymentEffects absent in target composition)`.

### Mutating Effect Boundary
If `preSwitchTransactions`, `postSwitchReconciliation`, or `participantLocalRollback` are non-empty,
or if any participant is classified as mutating, `nix-update deploy` halts immediately with exit code 78:
`configuration declares mutating deployment effects; run 'nix-update advanced prepare/apply' for transactional deployment`.

## 4. Source Authority Derivation

Without an external deployment contract, source authority is derived directly from `flake.lock`:
1. `flake.lock` is read and validated.
2. `nix flake archive --json --dry-run --no-update-lock-file --no-write-lock-file` realizes source store paths with local overrides.
3. `nix path-info --json` inspects all realized source store paths to obtain NAR hashes.
4. Each lock node (excluding root and `localConfig`) is validated:
   - Locked VCS inputs (`git`, `github`, `gitlab`, `sourcehut`, `hg`): must have 40-hex `rev` and valid `narHash`.
   - Locked path inputs (e.g. `repo-map-src`): must have locked `narHash` matching the Nix store NAR hash.
   - Unpinned mutable paths or missing `narHash`: rejected immediately.
   - `localConfig`: explicitly excluded as the reserved projection override.

## 5. Profile & Predecessor State

Predecessor generations are derived safely:
- An explicit numbered live link takes precedence for either supported relationship: that exact positive integer generation must be retained with the live target. The selected generation does not replace it.
- `aligned` means live and selected targets are equal, even if their numbered generations differ. For a direct live store link, the selected positive numbered generation is the canonical predecessor representative. Its number, generation path, and exact live target must match a retained entry. Multiple retained aliases of that target are supported; no newest-generation heuristic is used.
- An aligned direct live link with no valid numbered selected identity is refused, even when exactly one retained entry matches its target.
- `split-retained`: live target differs from selected target. For a direct live store link, exactly one retained generation must match the live target store path, and its number must be a positive integer. Multiple live-target aliases remain ambiguous because the selected profile identifies a different target.
- Ambiguous or unrecognized states: refused immediately.

## 6. Confirmation & Privilege Safety

- Interactive confirmation: Operator is prompted on `/dev/tty` to type `DEPLOY`.
  - Non-interactive environments (no `/dev/tty`) refuse activation safely.
  - No environment variable or command-line flag exists to bypass confirmation in production.
  - Standard input/output to `/dev/tty` uses separate read and write file descriptors to support macOS/Python 3.13 stream semantics.
- Privilege elevation: `sudo -v` credentials are validated interactively against `/dev/tty` prior to invoking DEVNULL-isolated privileged commands.
- Host activation: Exactly one `sudo darwin-rebuild switch` is executed. No profile mutations occur if confirmation is refused or during dry-run/plan-only modes.

## 7. Qualification and Build Diagnostics

Ordinary `nix-update` and `nix-update deploy` print qualification, projected
flake-check, and target-build failures on stderr without an extra flag. The
message distinguishes the outer process exit from the first recorded failed
child command or check, when the captured format supplies one. Native promotion
qualification uses the same failure selection, including stderr/stdout previews
inside qualifier JSON. A policy failure without a failed child is reported as
an outcome; recorded order does not establish a uniquely causal root error.

The bounded, redacted excerpt favors useful error context and the end of text
logs. Truncated or unavailable detail is identified explicitly. The printed
`private evidence` path points to verified command evidence, or a compact
diagnostic retained from the temporary core test report. It survives temporary
projection/report cleanup. Full report trees are not retained by this feature.
Raw evidence stays owner-private; terminal redaction covers known credential
forms and is not a general secret detector.

Each diagnostic uses at most 1,400 characters of sanitized detail, with a
separately bounded operation and evidence pointer (under 2,500 characters
total). Stream reads retain the existing 10 MiB limit per stream. Core report
extraction reads at most 1 MiB per runner TSV and 10 MiB of the selected runner
output, then retains only the compact diagnostic. Unknown report formats use
the captured command streams instead.

Diagnostics do not rerun commands, relax qualification, change lock options,
or authorize activation. Existing exit codes and machine-readable stdout
conventions remain in force. A corrected source launcher can be used directly;
publishing the source or building `dinasCommands` does not update the installed
command namespace or require a host redeployment.

Structured child fields are producer-owned. The supported composition result
shape was checked against `composition-nd/python/promotion_qualifier.py`
(`CommandResult.to_dict` and `_run_command_helper`): `argv`, `exitCode`,
`status`, `timedOut`, `timeoutSeconds`, `cleanupDisposition`, optional
`failureReason`, and stream `preview`/`truncated` fields. Core fixtures use
sanitized examples of this shape without importing or executing the composition.
A result may omit `commands` for policy-only diagnostics; success authorization
still uses the unchanged native result validator.

Failed checks and policy detail accompany child errors, and nonempty outer
stderr remains available with its own evidence pointer. An exit-zero result
that fails validation is described as a rejected result. A cleanup-only record
marked successful is considered only after recorded process/status failures.
Report-derived excerpts carry their own availability/truncation information,
not annotations inherited from replaced command streams. Live Workflow
summaries of nonzero commands also strip terminal controls and known credential
forms before presentation truncation.
