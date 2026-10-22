# Deploy a domain change

This guide is for an attended change through the split public/private
nix-darwin composition. Resolve the selected checkout from current operator
configuration. For advanced transactions, resolve contract and plan paths afresh.

## Ordinary local deployment

For the configured active top-level flake, start with the non-mutating review
and then run the attended command:

```bash
nix-update --dry-run
nix-update
```

`nix-update --dry-run` qualifies, builds, summarizes, and runs module-owned
read-only preflight checks without confirmation, privilege, profile mutation,
or switching. Plain `nix-update` repeats the current-truth checks, reads the
operator's `DEPLOY` confirmation from `/dev/tty`, and invokes at most one
switch. Plans are optional for this ordinary lane; contracts are reserved for
exceptional participant or external-state transactions under
`nix-update advanced prepare/apply`.

## Choose the owning path first

For a ready published input, the campaign command manages the composition
handoffs and retains its own owner-private state:

```bash
nix-update promote campaign run --input DOMAIN --revision EXACT_40_HEX_REVISION --dry-run
nix-update promote campaign run --input DOMAIN --revision EXACT_40_HEX_REVISION
nix-update promote campaign status
nix-update promote campaign resume
```

The top-level flake defaults to `flake.active`; use `--flake TOP` to select it
explicitly. `--output DIRECTORY` is an optional state-location convenience.
`status` and `resume` accept `--campaign DIRECTORY`; otherwise a maintained
index resolves the matching campaign. Ambiguous matches are listed for explicit
selection. State paths and downstream commit IDs are handled by the command.

Each actual repository mutation retains the native digest-bound confirmation.
Without a controlling terminal, the command prepares the next operation and
reports that it is awaiting confirmation. Dry-run retains qualification
evidence but never installs locks, stages, commits, pushes, or activates. A
downstream revision that needs a future public commit remains pending.

Campaign completion concerns source selection. Even an already-selected input
or identical final derivations do not prove that the live host is current.
After selecting the published implementation and desired composition inputs,
the separate host commands remain `nix-update --dry-run` and `nix-update`.

| Change class | Path |
| --- | --- |
| Agent-Central roster only | Agent-Central commit; no flake promotion |
| Public domain input | domain -> promotion campaign (public -> private) -> [publish] -> snapshot adoption -> deploy |
| Private remote input | current private contract/targeted lock path -> deploy |
| Private local-only `security-nd` input | targeted private lock -> deploy |
| Core `darwin-is-not-a-snowflake` | core promotion campaign (public -> private) -> [publish] -> snapshot adoption -> deploy |
| No input/lock change | `nix-update --dry-run` -> `nix-update` |

The commands have separate authority:

- `nix-update` (or `nix-update deploy [--dry-run] [--plan-only]`) performs
  standard attended local deployment without requiring an external deployment
  contract. It qualifies, builds, inspects, summarizes, confirms via `/dev/tty`,
  and switches once.
- `nix-update promote prepare/apply` qualifies and commits one composition's
  reviewed `flake.lock` change locally (`LOCAL_COMMITTED`). Under operator-local
  source authority, it does not push to a remote and never activates the host
  (retained legacy pre-catalog plans also publish).
- `nix-update promote campaign run|resume|status` coordinates multi-stage
  composition promotion across configured routes, passing exact local commits to
  downstream compositions with attended confirmation at each stage.
- `nix-update promote publish --source <key> --revision <commit>` explicitly
  publishes promoted commits in dependency order via fast-forward pushes to
  configured Git remotes.
- `nix-update snapshot status` inspects alignment between local source checkouts
  and the canonical deployment source pins configured under `[snapshot.sources]`
  in `nix-darwin.toml`.
- `nix-update lock` performs one isolated explicit `locked`, `targeted`, or
  `all` lock-candidate transaction. `targeted` requires `--input`; additional
  `--allow-node` values are explicit operator authority, not guessed closure.
- `nix-update build` qualifies and builds the selected closure without host
  activation.
- `nix-update advanced prepare` consumes an owner-private deployment contract and
  creates one immutable host plan under `nix-update-deployment-plan-v2`. It validates
  clean local Git authority without requiring upstream remotes or network access.
  `--preflight-only` does not create an activatable substitute.
- `nix-update advanced apply` consumes one reviewed v2 plan and activates the host only
  after the operator types its exact digest-bound confirmation. It revalidates
  local Git authority against the reviewed plan. Legacy v1 plans are rejected.

Do not hand-edit `flake.lock`, run broad `nix flake update`, call
`darwin-rebuild switch`, replay a consumed plan, or bypass confirmation. A
promotion digest and a deployment digest are different authorities.

## Release channels and host impact

`main` is not necessarily a promotable input's release channel. A version-1
`lib.promotion` contract continues to imply `refs/heads/main`. A version-2
contract explicitly maps every direct promotable input to one reviewed
`refs/heads/*` release ref, and the selected flake input must follow the same
branch. Operators do not pass a ref to `nix-update promote`.

Release-branch selection does not replace exact lock pinning. Prepare resolves
the contract-declared ref to the requested 40-hex revision, proves the flake and
lock follow that ref, and retains exact candidate lock bytes. Promotion never
creates, advances, or rewrites the release branch.

Each promotion plan also records neutral host-impact evidence:

- `HOST_DERIVATIONS_IDENTICAL` / `NO_HOST_CHANGE` means the selected
  composition's declared Darwin derivations are exact-map identical.
- `HOST_DERIVATIONS_CHANGED` / `HOST_CHANGE_REVIEW_REQUIRED` means at least
  one declared derivation differs.

Neither classification activates a host. In a multi-stage campaign, only the
final composition result may be interpreted as `COMPLETE_NO_ACTIVATION` or
`AWAITING_ACTIVATION`. The latter requires a separate reviewed top-level
deployment; promotion never calls it
automatically.

Contract, qualifier, check, and host-impact evidence is evaluated from private
canonical snapshots of the reviewed Git tree with the exact applicable lock
bytes. Active-checkout `.git`, ignored, or untracked bytes are not promotion
authority. Temporary snapshot paths are execution evidence only and are
deleted. A literal dotted host name is one attribute identifier, not a nested
path. Version-1 lock evidence may spell its implied main ref as absent, `main`,
or `refs/heads/main`; no other ref is equivalent.

## Normal attended deployment tail

Use the strict operator configuration to select the active top-level flake.
First build and inspect the current candidate without mutation, then run the
same workflow with its one terminal-only confirmation:

```bash
set -euo pipefail
NIX_UPDATE="$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/nix-update"
# Use the canonical checkout; the former compatibility location is retired.
"$NIX_UPDATE" --dry-run
"$NIX_UPDATE"
```

The dry run performs source, profile, qualification, build, closure, and
module-owned read-only preflight checks, prints the same concise summary, and
stops before confirmation, privilege, profile mutation, or switching. Plain
`nix-update` asks the operator to type `DEPLOY` on `/dev/tty`, revalidates the
current Git, executable, process, and live-generation authority, and invokes at
most one switch.

Plans are optional for ordinary deployment. To retain the same current-truth
artifact for agent or human review without activating the host:

```bash
set -euo pipefail
umask 077
NIX_UPDATE="$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/nix-update"
LOCAL_PLAN=/ABSOLUTE/OWNER-PRIVATE/nix-update-local-plan
"$NIX_UPDATE" --plan-only --output "$LOCAL_PLAN"
```

Contracts are exceptional. Closure deltas are evidence, not path allowlists;
owner-private command evidence retains the complete closure inventories, and
the journal records the machine closure delta. The terminal summary groups
meaningful package changes. Lock-pinned Git and
`git+file` sources derive authority from `flake.lock` and their realized NARs,
so they need no duplicate source contract. Raw mutable path sources without
locked NAR authority stop. Rollback authority comes from the actual live
generation observed immediately before mutation, never from a historical
generation number. Git remote publication and Agent-Central are not deployment
authority and are not runtime dependencies of normal deployment.

## Advanced and legacy contract deployment

Use `nix-update advanced prepare/apply` only when module-owned exceptional
metadata declares imperative participants, pre-switch transactions,
post-switch reconciliation, external mutable-state probes, or participant-local
rollback. The compatibility workflow preserves the historical contract and
digest-bound plan evidence; it is not required or presented as the ordinary
deployment path.

```bash
set -euo pipefail
umask 077
NIX_UPDATE="$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/nix-update"
SELECTED_PRIVATE=/ABSOLUTE/PATH/TO/composition-private-nd
DEPLOYMENT_CONTRACT=/ABSOLUTE/OWNER-PRIVATE/nix-update-deployment-contract.json
DEPLOY_OUTPUT=/ABSOLUTE/OWNER-PRIVATE/nix-update-advanced-plan
"$NIX_UPDATE" advanced prepare --flake "$SELECTED_PRIVATE" \
  --contract "$DEPLOYMENT_CONTRACT" --output "$DEPLOY_OUTPUT"
"$NIX_UPDATE" advanced apply "$DEPLOY_OUTPUT"
```

The advanced apply confirmation remains:

```text
APPLY NIX-UPDATE PLAN <exact-64-hex-plan-digest>
```

Never pipe, synthesize, or replay that confirmation. A stale, missing, or
synthesized contract is a stop, and a consumed plan is never reusable.

## Example: add Proton Pass to local-only `security-nd`

The official Homebrew cask token is `proton-pass`. Homebrew reported version
`1.39.1`, artifact `Proton Pass.app`, and the preferences/saved-state identity
`me.proton.pass.electron` during discovery on 2026-08-29; those are provenance,
not pins. Recheck the [official cask page](https://formulae.brew.sh/cask/proton-pass)
and its linked cask metadata before the change, then confirm the installed
bundle's `CFBundleIdentifier` agrees.

`security-nd` is deliberately local-only. Its private registry entry has
`url: "local-only"`; it is neither a public `composition-nd` promotion input
nor a remote private promotion target.

1. Resolve the canonical local checkouts and prove the registry classification.

   ```bash
   set -euo pipefail
   SECURITY_ND=/ABSOLUTE/PATH/TO/security-nd
   PRIVATE_COMPOSITION=/ABSOLUTE/PATH/TO/composition-private-nd
   python3 - "$PRIVATE_COMPOSITION/registry.json" <<'PY'
   import json, pathlib, sys
   registry = json.loads(pathlib.Path(sys.argv[1]).read_text())
   matches = [m for m in registry["modules"] if m["input"] == "security-nd"]
   assert len(matches) == 1 and matches[0]["url"] == "local-only"
   print("security-nd: local-only")
   PY
   git -C "$SECURITY_ND" status --short --branch
   git -C "$PRIVATE_COMPOSITION" status --short --branch
   ```

2. In `security-nd`, add `"proton-pass"` to the existing
   `homebrew.casks` list in `modules/default.nix`. Add exactly this owner-less
   application claim in `flake.nix` after independently verifying its identity:

   ```nix
   {
     id = "bundle:me.proton.pass.electron";
     artifact = "Proton Pass.app";
     provider = "homebrew-cask";
     package = "proton-pass";
   }
   ```

   Advance the existing exact claim-count/set assertions and
   `docs/evidence/application-claims.md`. Record both current cask metadata and
   the installed bundle's `Contents/Info.plist` `CFBundleIdentifier`, following
   that evidence file's existing two-source convention. Do not put a Proton account,
   credential, login state, vault data, recovery material, or runtime secret in
   Nix, Git, a derivation, or the store.

3. Run the domain's focused and full checks. The domain's authority and source
   policy are local-only, so do not infer a publication step from incidental
   Git remote metadata.

   ```bash
   set -euo pipefail
   SECURITY_ND=/ABSOLUTE/PATH/TO/security-nd
   (cd "$SECURITY_ND" && python3 -m unittest tests.test_application_claims -v)
   (cd "$SECURITY_ND" && tests/run)
   (cd "$SECURITY_ND" && nix flake check \
     --no-update-lock-file --no-write-lock-file)
   (cd "$SECURITY_ND" && git diff --check)
   ```

4. Prepare and inspect a non-installing targeted candidate. The current
   `security-nd` lock node follows the shared private composition's nixpkgs and
   nix-darwin nodes, so the exact initial authorization is only
   `--input security-nd`; do not pre-authorize another node. If the transaction
   reports another changed node, stop and inspect the retained delta before
   deciding whether one exact `--allow-node NODE` is mechanically justified.

   ```bash
   set -euo pipefail
   umask 077
   NIX_UPDATE="$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/nix-update"
   PRIVATE_COMPOSITION=/ABSOLUTE/PATH/TO/composition-private-nd
   LOCK_REVIEW=$(mktemp -d "${TMPDIR:-/tmp}/security-nd-lock-review.XXXXXX")
   chmod 700 "$LOCK_REVIEW"
   "$NIX_UPDATE" lock --flake "$PRIVATE_COMPOSITION" --mode targeted \
     --input security-nd --output "$LOCK_REVIEW"
   python3 -m json.tool "$LOCK_REVIEW/result.json" >/dev/null
   cmp "$PRIVATE_COMPOSITION/flake.lock" "$LOCK_REVIEW/flake.lock.preimage"
   git -C "$PRIVATE_COMPOSITION" diff --exit-code -- flake.lock
   ```

   Inspect `result.json`, `flake.lock.preimage`, `flake.lock.candidate`, and the
   exact node delta. Without `--install`, the selected checkout is unchanged.
   Current main exposes no detached `lock apply` subcommand: installing is a
   fresh same-source transaction with explicit `--install`, not reuse of the
   review directory. If the reviewed transaction requires an exact additional
   owned node, discard that output, repeat the review with `--allow-node
   EXACT-OWNED-NODE`, and repeat that identical `--allow-node` argument in the
   fresh install transaction below. Never omit an authorization between review
   and install.

5. After review, run one fresh install transaction. Require the same clean Git
   identity, the same single-node authorization, and a fresh output directory.
   Inspect the resulting journal and prove the installed bytes equal its
   retained candidate.

   ```bash
   set -euo pipefail
   umask 077
   NIX_UPDATE="$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/nix-update"
   PRIVATE_COMPOSITION=/ABSOLUTE/PATH/TO/composition-private-nd
   LOCK_INSTALL=$(mktemp -d "${TMPDIR:-/tmp}/security-nd-lock-install.XXXXXX")
   chmod 700 "$LOCK_INSTALL"
   "$NIX_UPDATE" lock --flake "$PRIVATE_COMPOSITION" --mode targeted \
     --input security-nd --output "$LOCK_INSTALL" --install
   python3 -m json.tool "$LOCK_INSTALL/result.json" >/dev/null
   python3 -m json.tool "$LOCK_INSTALL/install-transaction.json" >/dev/null
   cmp "$PRIVATE_COMPOSITION/flake.lock" "$LOCK_INSTALL/flake.lock.candidate"
   git -C "$PRIVATE_COMPOSITION" diff --check -- flake.lock
   ```

   The shown install is valid only when the reviewed authorization remained
   exactly `--input security-nd`. If review authorized one additional node,
   the install command shape is instead:

   ```bash
   set -euo pipefail
   umask 077
   NIX_UPDATE="$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/nix-update"
   PRIVATE_COMPOSITION=/ABSOLUTE/PATH/TO/composition-private-nd
   LOCK_INSTALL=$(mktemp -d "${TMPDIR:-/tmp}/security-nd-lock-install-owned.XXXXXX")
   chmod 700 "$LOCK_INSTALL"
   "$NIX_UPDATE" lock --flake "$PRIVATE_COMPOSITION" --mode targeted \
     --input security-nd --allow-node EXACT-OWNED-NODE \
     --output "$LOCK_INSTALL" --install
   ```

   Review the actual delta. Commit and fast-forward push the private
   composition's `flake.lock` only when that repository's current policy and
   remote authority require it; the local `security-nd` source itself remains
   local-only. Then run the normal attended deployment tail above. If the
   selected module metadata declares exceptional effects, stop and use the
   advanced contract path instead.

6. After activation, verify one installed cask and one application claim.

   ```bash
   set -euo pipefail
   test "$(brew list --cask --versions proton-pass | wc -l | tr -d ' ')" = 1
   test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' \
     '/Applications/Proton Pass.app/Contents/Info.plist')" = \
     me.proton.pass.electron
   SECURITY_ND=/ABSOLUTE/PATH/TO/security-nd
   test "$(rg -n 'package = "proton-pass";' "$SECURITY_ND/flake.nix" | wc -l | tr -d ' ')" = 1
   ```

## Example: add `pypinfo` to public `developer-ux-nd`

PyPI reported `pypinfo` `23.0.0` during discovery on 2026-08-29. Recheck the
[official PyPI project](https://pypi.org/project/pypinfo/) and upstream
[authentication documentation](https://github.com/ofek/pypinfo/blob/master/README.md#installation)
at implementation time. Authentication is runtime operator state, not package
or deployment material.

1. Query the pinned nixpkgs package set before creating a package. This is an
   evaluation query only; it does not update a lock or activate a host.

   ```bash
   set -euo pipefail
   DEVELOPER_UX=/ABSOLUTE/PATH/TO/developer-ux-nd
   nix eval --no-update-lock-file --no-write-lock-file --raw --impure --expr \
     "let flake = builtins.getFlake \"path:$DEVELOPER_UX\"; \
       pkgs = flake.inputs.nixpkgs.legacyPackages.aarch64-darwin; \
      in if builtins.hasAttr \"pypinfo\" pkgs then pkgs.pypinfo.version else \"absent\""
   ```

   If the pinned set contains a suitable package, reuse it. If absent, add a
   `packages/pypinfo/default.nix` using the repository's existing
   `callPackage`/overlay conventions, the rechecked exact release, and an exact
   source hash. Declare its runtime Python dependencies, export it from
   `packages.aarch64-darwin`, and add it to `environment.systemPackages` in the
   Developer-UX module. Add package build, `pypinfo --version`, flake output,
   module, and selected-closure tests.

   Never commit a Google service-account JSON file or
   `GOOGLE_APPLICATION_CREDENTIALS`; never copy credential bytes into a Nix
   expression, derivation environment, test fixture, build log, or store path.

2. Run the domain's focused/package/flake/full tests, inspect the exact diff,
   create one domain commit, fetch `origin/main`, require normal fast-forward
   ancestry, and publish `developer-ux-nd/main` with an ordinary push. Record
   the exact lowercase 40-hex published commit as `DEVUX_COMMIT`.

3. Apply the public composition promotion. `developer-ux-nd` is a direct
   public registry input.

   ```bash
   set -euo pipefail
   umask 077
   NIX_UPDATE="$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/nix-update"
   PUBLIC_COMPOSITION=/ABSOLUTE/PATH/TO/composition-nd
   DEVUX_COMMIT=0123456789abcdef0123456789abcdef01234567
   PUBLIC_PLAN=$(mktemp -d "${TMPDIR:-/tmp}/developer-ux-public-promotion.XXXXXX")
   chmod 700 "$PUBLIC_PLAN"
   "$NIX_UPDATE" promote prepare --flake "$PUBLIC_COMPOSITION" \
     --input developer-ux-nd --revision "$DEVUX_COMMIT" --output "$PUBLIC_PLAN"
   python3 -m json.tool "$PUBLIC_PLAN/plan.json" >/dev/null
   sed -n '1,240p' "$PUBLIC_PLAN/plan.md"
   "$NIX_UPDATE" promote apply "$PUBLIC_PLAN"
   ```

   At the prompt, type the digest from that reviewed promotion plan:

   ```text
   APPLY NIX-UPDATE PROMOTION <exact-64-hex-plan-digest>
   ```

   Record the exact local public composition commit as `PUBLIC_COMMIT`.
   Promotion apply commits only the reviewed lock change locally (`LOCAL_COMMITTED`)
   and never activates the host or pushes to a remote.

4. Apply the separate private promotion. The private promotion contract has
   exactly one promotable lower input, `composition-nd`.

   ```bash
   set -euo pipefail
   umask 077
   NIX_UPDATE="$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/nix-update"
   PRIVATE_COMPOSITION=/ABSOLUTE/PATH/TO/composition-private-nd
   PUBLIC_COMMIT=0123456789abcdef0123456789abcdef01234567
   PRIVATE_PLAN=$(mktemp -d "${TMPDIR:-/tmp}/composition-private-promotion.XXXXXX")
   chmod 700 "$PRIVATE_PLAN"
   "$NIX_UPDATE" promote prepare --flake "$PRIVATE_COMPOSITION" \
     --input composition-nd --revision "$PUBLIC_COMMIT" --output "$PRIVATE_PLAN"
   python3 -m json.tool "$PRIVATE_PLAN/plan.json" >/dev/null
   sed -n '1,240p' "$PRIVATE_PLAN/plan.md"
   "$NIX_UPDATE" promote apply "$PRIVATE_PLAN"
   ```

   Type the private plan's own distinct confirmation:

   ```text
   APPLY NIX-UPDATE PROMOTION <exact-64-hex-plan-digest>
   ```

   Record the exact local private composition commit as `PRIVATE_COMMIT`.
   If remote publication is desired, publish explicitly with `nix-update promote publish`.
   To activate the promoted source set, adopt the promoted source commits into the
   canonical deployment snapshot using the bounded two-phase adoption transaction:

   ```bash
   # Prepare the adoption plan (auto-detects the single matching campaign or pass --campaign <dir>)
   nix-update snapshot prepare

   # Inspect the prepared adoption plan before applying
   nix-update snapshot status --plan ~/.local/state/nix-update/snapshot-adoption-plans/plan-...

   # Apply the reviewed adoption plan (attended confirmation required)
   nix-update snapshot apply ~/.local/state/nix-update/snapshot-adoption-plans/plan-...
   ```

   Snapshot adoption verifies local Git ancestry against configured release branches,
   holds the deployment transaction lock (`DeploymentLock`), generates an atomic timestamped
   backup under `~/.local/state/nix-update/snapshot-backups/`, updates `[snapshot.sources]` in
   `nix-darwin.toml` preserving file permissions and ownership, and consumes plan authority.
   Verify post-adoption alignment with `nix-update snapshot status`. Without snapshot adoption
   or an explicit `--source-revision`, deployment preserves the existing snapshot pins.

5. Run the normal attended deployment tail (`nix-update deploy`). If module metadata requires the
   advanced path, prepare and apply its separate contract instead. After
   activation, keep authentication outside Nix and do a non-secret readback:

   ```bash
   set -euo pipefail
   pypinfo --version
   pypinfo --help >/dev/null
   test -z "${GOOGLE_APPLICATION_CREDENTIALS:-}" || \
     test -r "$GOOGLE_APPLICATION_CREDENTIALS"
   ```

   Supply `pypinfo --auth /owner/private/service-account.json` or
   `GOOGLE_APPLICATION_CREDENTIALS` only at runtime under operator ownership.

## Recovery after a failed deployment

- A failed plan whose mutation authority was consumed is never replayed.
- Preserve its result, marker, journal, and participant evidence.
- Fix forward and create a fresh contract and fresh plan directory.
- Live and selected generations may temporarily differ. The fresh contract
  must truthfully represent that split.
- Do not manually realign a profile unless the canonical recovery contract
  explicitly instructs it.
- Retain the rollback generation until post-activation verification succeeds.
- **Snapshot adoption vs. Rollback**: Rolling back a system generation (`nix-update rollback`)
  switches generation symlinks; it does not mutate `nix-darwin.toml` or repository
  working trees. Snapshot adoption is a decoupled configuration transaction.
  Preserve SNAPSHOT2's lesson: an older host generation may require its corresponding
  historical operator config when parser or source semantics differ. If an operator rolls
  back a host deployment across semantic boundaries and wishes to realign the snapshot table
  with the earlier generation's source pins, they can either restore from the timestamped
  backup under `~/.local/state/nix-update/snapshot-backups/` or prepare and apply a snapshot
  adoption plan specifying `--pin <key>=<commit>`.

Promotion publication, lock installation, build qualification, plan creation,
snapshot adoption, and host activation are separate outcomes. Never describe one
as evidence that another happened.
