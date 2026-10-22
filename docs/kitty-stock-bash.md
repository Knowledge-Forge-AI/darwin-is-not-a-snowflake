# Stock Kitty enhanced Bash source candidate

Status: source candidate selecting enhanced stock Kitty only on `example-primary`; the module default and `example-workstation` remain off. Independent dispatcher review and later manager acceptance are pending. Deployment is unauthorized. Offline fixture evidence does not qualify native Kitty, LaunchServices, login accounting, signatures or TCC/FDA.

The stock vendor app keeps its identity, bytes, signature, ownership and resources. Only its user configuration selection changes: `macos-launch-services-cmdline` selects `stock-bash.conf`, which includes the neutral shared config and an optional generation share. Kitty Bash and Kitty Zsh retain their bundle IDs, names, launch commands, plist, title, cwd/window/session semantics, resources, icon recipe and shared configuration. No shared shell loader, prompt, banner or Fastfetch code changes.

## Ownership and maintenance

One target-user privilege transition runs `kitty-stock-maintain.sh`. It always calls the seventeen-argument variant writer, then calls the independent stock adapter in enable or disable mode. There is no rebuilding fallback. Baseline variant-maintenance failures still return nonzero to the existing fatal activation boundary. Optional stock failures print `required combined rollout remains incomplete` and allow subsequent activation sections to run. This diagnostic must prevent acceptance of the combined rollout even when the enclosing activation exits zero.

Variant receipts live under `~/.config/kitty/.dinas-kitty-state/variant-kitty-{bash,zsh}.receipt`. They record a version-bound functional-input digest, cask version, observed signed executable/icon/seal hashes, independently observed signature verification, last managed config/session hashes and complete/pending-registration state. Identity excludes stock settings, banner policy, common-helper bytes, font changes and rendering/tool paths. An icon-recipe revision is needed to intentionally change already-generated icons. Necessary cask-version/executable/icon/resource-name/plist changes trigger normal maintenance in either mode.

Valid unchanged bundles/configs/sessions are no-ops: no copy, recreation, touch, signing, icon work or LaunchServices registration; inode and mtime remain unchanged. Read-only signature verification and hashing still occur. Config updates compare bytes first, replace exact old managed content without claiming a backup, and preserve user conflicts using the existing `.before-nix-darwin` convention. Shared config linking retains its existing helper semantics.

Baseline migration does not infer a valid native signature from source-byte equality. It checks expected structure, plist and links, then compares source and copied executable LC_UUID sets across all Mach-O slices (or exact bytes where equal). This is provenance evidence, not tamper-proof authentication. The receipt binds the current cask version/source digest and observed signed bytes; signature verification is independently recorded, including `unverified`. There is no ctime heuristic. If different signed bytes cannot be related to the current source (including unavailable `otool`), the bundle cannot be adopted: normal staged maintenance rebuilds from the current cask instead. This includes a baseline bundle left behind by a manual cask upgrade with unchanged resource names. The previous app remains until publication. Corrupt/absent variant receipts can be re-adopted only through the provenance checks; symlinked receipts refuse. The old stock activation stamp is retained but grants no ownership.

Two intentional baseline-maintenance refusals remain fatal to activation: a previously verified unchanged bundle whose signature now fails verification, and a variant-directory `macos-launch-services-cmdline` that can override the managed launch selection. Preflight preserves all files and prevents stock installation in these states. For a signature regression, inspect the bundle/receipt and actual verification diagnostics before a separately authorized repair; do not erase the receipt to bypass the check or silently re-sign it. Restore the known-good bytes or qualify a deliberate rebuild from the trusted current cask. For the selection conflict, inspect and preserve the user file, then move it aside only with its owner's authorization and rerun read-only preflight. These failures are explicit baseline safety boundaries, not optional enhancement failures. They are covered by preservation and activation-boundary fixtures.

## Recovery and disable

Rebuilds use exact hidden transaction directories beside the final apps, on the same filesystem, with no `.app` suffix. The owner marker is prepared in a temporary file and published by rename. An empty owned transaction directory is a recoverable creation/cleanup state; nonempty unmarked directories refuse. Build/icon/sign work happens before replacing an existing app. A failed build/sign leaves the old bundle and receipt intact; a retry handles the retained transaction. After the first rename, retry restores the previous correctly identified bundle if the final path is absent. After publishing a candidate, matching candidate hashes permit receipt publication and registration retry. Pending registration retries do not copy or sign the candidate. Completed/aborted transactions are renamed to the exact `.cleanup` path before deletion, keeping the owner until all payloads are removed. Retry can finish partial cleanup without interpreting a partly deleted old bundle as publication state. Unknown/conflicting transaction contents are retained and refused. No private-directory sweep occurs. Run maintenance serially; concurrent manual invocations are not a supported interface.

Quarantine removal now targets only the staged copied executable, rendered icon and generated plist. Recursive removal through cask resource/framework links is avoided; the stock vendor app is untouched. Extended-attribute behavior, native launch/registration and any effect on signature/TCC qualification remain unverified offline.

A genuine cask upgrade can change the resources/frameworks targeted by the old bundles before maintenance runs. Rebuilding with the new executable restores that pairing while retaining identities and configuration. If it fails, the old copied executable is explicitly **not recovered**; restoring its bundle cannot restore already-replaced vendor resources. When the source is unchanged, the prior bundle can remain coherent. This candidate neither snapshots nor rolls back the vendor app. Signatures can change during necessary upgrades; retained TCC/FDA access is unqualified, never guaranteed. No stock vendor app editing/re-signing, ownership changes or TCC reset is permitted.

The stock adapter accepts `[--check] enable COMMON USER HOME CONFIG CMDLINE` or `[--check] disable COMMON USER HOME`. It owns only the two stock selection files and one receipt per file in the same state directory. Receipts record installed/previous managed hashes, first preimage type/hash/backup basename, and prepared/active/disabled/relinquished state. They are data, never sourced. Sources, parents, both records and necessary restore backups are preflighted before mutation. Corrupt or symlinked ownership receipts refuse without changing them.

Enable writes config before cmdline, journals intent before replacement, and installs by atomic rename. Exact previous managed content is replaced without a backup. User conflicts are preserved; directories/special files refuse in place. Identical unrecorded content is adopted without claiming ownership of its original bytes; before a later content update it receives a real preimage backup. Disable handles cmdline before config, removing/reverting only exact recorded managed content. Regular-file and symlink preimages are restored from validated copies; original backups are retained. User-edited targets are left in place with ownership relinquished. Disabled receipts remain as preimage history, so repeated disable and ordinary enable/disable cycles do not accumulate duplicate backups. Lost ownership records never authorize deletion. `ABSENT` means no active recorded ownership; unowned/adopted/user-edited files can remain and may still select stock behavior. Inspect those before claiming functional disable. Two files are ordered, not one atomic transaction; unexpected I/O failures require readback/retry. Unreferenced temporary files from killed writes are retained; there is no automatic sweep or backup pruning.

## Shell selection and host parity

The launch wrapper forwards explicit arguments through login to Bash. Without arguments, the post-login stage sets `ENV` for one Bash exec, loads the normal host login files, clears the private handoff, restores cwd, configures the stock layer and attaches ble.sh last. No global BASH_ENV/ENV/ZDOTDIR or default-shell change is installed. The host owns prompt, aliases, colours, title and `ff`.

The layer adapts the original VM donor's detached ble.sh, bash-completion, supported delayed fzf integration, zoxide `z`/`zi` with ordinary `cd`, `up`, larger history and append/import synchronization. `DINAS_PLAIN_BASH=1` skips ble/fzf/zoxide/direnv while retaining stock Readline/history conveniences. Missing/broken optional components fall back to Readline. Explicit/noninteractive commands, non-TTY streams, missing/dumb terminals and SSH contexts skip enhancements. An optional sourced package that itself calls `exit` cannot be recovered by its caller.

Nested Bash, language-manager shells, `nix develop` and `nix-shell` do not select the stock entry and inherit no interactive stock functions/hooks. Children may inherit appended PATH tool availability. Ordinary sessions may trim shared history to their own smaller limits. Manager decision **B1** explicitly preserves the host baseline's nested-Bash and explicit `.bashrc` re-source banners; the regression compares stock against unchanged Kitty Bash argv. Once-per-window VM banner parity is not claimed. No suppression marker or shadowed `ff` is introduced.

Direnv/nix-direnv remains opt-in and stock-only, default false, with project authorization still required. No Agent-Central, Agent-Security, JACA or broker dependency is introduced.

## Verification and rollout

Set `KITTY_STOCK_TEST_TMP` to the phase output/_tmp, `KITTY_STOCK_TEST_LOGS` to its durable logs directory, and route parent HOME/TMPDIR into _tmp. Run the four `tests/test_kitty_*` Python suites and Kitty Bats/privilege-boundary checks. Fixture tools log calls; signing changes executable bytes. The migration test executes base-writer logic with one documented temp-root substitution. PTY tests use real host rc files and a byte-level terminal stand-in; its driver propagates child exit status. Config resolution remains a bounded model, not Kitty's parser. These standalone tests are not automatically registered in repository CI.

No dependency, lock or runner-policy changes are added. Existing Nix package selections remain pinned by the existing core configuration. No Nix realization, cask operation, activation or app launch occurs in this phase. Real selected ble/fzf/zoxide APIs, GUI/rendering/keys, native login, running-process reuse, Finder windows/cwd, LaunchServices isolation, real-host no-op metadata/signatures and TCC responsibility still require qualification. See [the exact combined-rollout integration note](browser-rollout-kitty-stock-bash.md).

## Local stock Bash theme and completion policy

The stock Bash reads `~/.local/nix-darwin/config/kitty-bash.toml` with
`tomllib` through `tools/kitty-stock-style.py`. Six prompt roles,
five terminal fields, optional ANSI `color0..color15`, bounded menu geometry,
and a closed whitelist of ble.sh faces can be adjusted independently.
`[ble.faces]` supports the 65 faces provided by pinned ble.sh 0.4.0-devel3:
ghost auto-completion, menu-filter input, syntax, commands, filenames, variables
and regions. Selected/matched menu-item and description faces absent from this
pin are rejected; the completion module loads synchronously before its faces
are styled. Styles
are whitelisted `fg`/`bg` colors and graphic attributes, never sourced code. The committed fallback is `config/kitty/stock-bash-style.toml`.
`emit-shell` applies only to the stock Bash prompt (after host startup files),
not the existing Kitty Bash/Zsh or system login shell. BLE uses the
`align-nowrap` menu style and configurable `menu_align_min/max` widths to
render padded, left-justified columns. The selected instance starts the absolute Nix Bash executable with explicit
`--login --posix` and `SHELL` set before exec. Bash therefore initializes its own
`BASH` identity correctly before ble.sh spawns subprocesses; setting `SHELL` later
is insufficient. `$0` is now the absolute executable path, while login-shell rc
ordering and `login -fp` accounting remain. The reproduced `noprofile` and
missing-face errors are covered by real pinned-runtime PTY regressions. See
[the runtime repair and installed-readback boundary](stock-app-runtime-repair.md).

The explicit interface is `python3 -I -S -B tools/kitty-stock-style.py MODE`;
the script is data installed with mode `100644`, not a direct executable.
Selected stock startup invokes the pinned Python with those isolation flags.
Run `init-user` once to create the editable local
TOML. `render-kitty --apply` creates only the generated
`~/.config/kitty/stock-bash-colors.conf` fragment, included optionally from
the stock config. A final chosen palette can be promoted to the committed
fallback with `promote --commit`; this stages and commits only the stock-style
TOML and never pushes or activates. Promotion refuses an already staged index.
The stock share must be rebuilt and deployed in the separate authorized
combined browser/Kitty rollout. Editing the local TOML affects new selected
stock Bash sessions only after that production source has been adopted.

### Shared palette precedence

Installation validates the projected `kitty-bash.toml` and merges its declared
values over `config/kitty/stock-bash-style.toml`. The admitted absent-file default
has `schema = 1` and empty required tables, so it selects the committed sample.
The installed result is the fallback palette, exposed as
`/run/current-system/sw/share/kitty-stock-bash/style-default.toml`. This file is
data, not a terminal include. Required projection keys are `schema`, `palette`,
`terminal`, and `completion`; `ansi` and `ble.faces` are explicitly optional.

Both selected stock startup and the manual CLI then merge the current local
`~/.local/nix-darwin/config/kitty-bash.toml` over that installed fallback, key by
key, including individual faces and ANSI entries. Every present local file requires
`schema = 1` and the `[palette]`, `[terminal]`, and `[completion]` tables, matching
the projection and installation validator. Tables may be empty and their values
may be partial; `[ansi]` and `[ble.faces]` remain optional. Removing one
local key restores its installed value. Removing the entire local file restores
the entire installed fallback. A value captured by the projection remains in the
installed fallback until a later separately authorized installation changes it.
Removing such a local key therefore does not necessarily restore the committed
sample. Palette promotion remains an explicit later user choice.

The manual CLI discovers the active stock fallback before its source sample.
For a standalone store-file program, or to bind a particular generation, pass
`--default /nix/store/…-kitty-stock-style.toml` explicitly. Startup always passes
the generated store file explicitly. An explicit missing `--file` or `--default`
refuses; an absent implicit local file is normal. Invalid local data refuses
emission and rendering. Startup prints a refusal and leaves the original host
prompt in place; it does not silently select a different validated palette.
The stock emitter uses truecolor for every prompt role. Five sample roles match
the standard xterm 256-color values; the virtualenv role uses pure blue rather
than the host theme's palette-relative SGR 34. The host theme's low-color fallback
is not used by this stock style. Appearance on a terminal without truecolor
support remains unqualified.
Only the trusted installed-default alias is resolved automatically. User input
and output symlinks and symlinked parents refuse. Hardlinked local overrides and
write destinations refuse. Trusted read-only defaults allow hardlinks so Nix
store optimisation does not disable styling.

For example, install palette A, then edit local prompt/face/terminal values to B.
New selected shells use B immediately. The terminal still uses the previously
generated include until an explicit `render-kitty --apply` writes B. Removing
one prompt override returns that prompt role to A while the other B values
remain. Removing the local file returns shell values and render previews to A,
but the generated terminal include retains B until the next explicit render.
Startup never deletes or regenerates that include. Unmanaged files and path
collisions refuse; byte-identical managed renders preserve inode and mtime.

Use disposable fixture homes, explicit files/defaults and fixture repositories
for mutation tests. No host rendering, promotion, adoption or activation is part
of this source phase. The source provenance JSON records this new implementation
from surviving inputs; the unavailable earlier candidates confer no acceptance.
