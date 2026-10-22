> R3 supersession: the target-host role/physical-owner contract is now
> [MANAGER-REVIEW.md](../../../delivery/browser-ownership-r3/MANAGER-REVIEW.md).
> Existing inventory and R1/R2B evidence below are retained. Chrome/CNT data and
> the Brave YouTube core writer remain held continuity; no retirement or source
> selection is authorized by this inventory.

# `browser-ux_dinas` Migration Inventory

Filled from the live tracked core tree and read-only host/provider evidence on
2026-09-24. Structure and rules follow the
[owner migration template](owner-migration-template.md); normative decisions come
from
[ADR 0001](../../adr/2026/08/0001-nix-darwin-core-subflake-extension-contract-and-owner-driven-migration.md)
and amended
[ADR 0002](../../adr/2026/08/0002-domain-ownership-application-claims-and-migration-sequencing.md).
The actionable protocol is the
[subflake migration guide](../subflake-migration-guide.md).

BUX0 architecture refresh and BUX1 destination bootstrap / dormant composition
registration are complete. This inventory is updated for the **BUX2 PREPARATION**
phase. No runtime modules, packages, commands, configurations, locks, or host states
are modified in this documentation update.

## Header

```text
module:                 browser-ux_dinas
publication class:      PUBLIC-CAPABLE
owning composition:     the public-capable composition
owner:                  operator
status:                 BUILDING
governing ADR:          docs/adr/2026/08/0002-domain-ownership-application-claims-and-migration-sequencing.md
activation required:    YES
```

**Host activation note:** BUX1 required no host activation because local deployment
`--plan-only` review proved `+0 / -0` closure movement targeting the live generation-208
system. Host activation remains `YES` in this inventory because subsequent cut and
activation milestones involve privileged root writers and user-home app bundle generation.

Publication class is **PUBLIC-CAPABLE**: the destination footprint consumes only
public `nixpkgs`, public Homebrew casks, and public extension/policy declarations.
It reads no private input, operator secret, or non-public security state.
Publication capability is not publication readiness: publication requires stripping
private paths, operator continuation-document references, and private archive
descriptions from the Shared Images workflow skill before public release.

---

## 1. Pre-cut inventory

Paths and line references describe the live tracked source at entry commit
`9c0d37bc5733e32b7c928843c1d71dac1b71065c` (tree `dfe1347a9002a9a6bf76bce2cafdef62ea42fb99`).

Current ownership supersession (R3): core retains stock `pkgs.brave` and the
existing Brave-YouTube launcher backed by that same package. Historical MOVE and
removal entries below confer no removal authority. Core also owns Chrome policy,
Safari privacy/DDG/Wipr intent, stock LibreWolf and Orion; Browser UX owns Floorp
and held convenience intent. See
[the R3 ownership ADR](../../adr/2026/10/0002-browser-role-ownership-r3.md).
The earlier October ADR 0001 is retained superseded R2B evidence.

### 1.1 Flake inputs and package dependencies

| Dependency | Current source / location | Classification | Rationale |
|---|---|---|---|
| `pkgs.brave` | Historical: `modules/brave-youtube.nix:27`; R2B: also `modules/browsers.nix` | **STAY CORE (R2B)** | One package backs stock Brave and the retained Brave-YouTube launcher; the earlier MOVE decision is superseded. |
| `pkgs.google-chrome` | `modules/browsers.nix:12` | **STAY CORE** | Retained in core's `environment.systemPackages` for the core-only browser baseline. |
| Homebrew cask `firefox` | `modules/browsers.nix:16` | **STAY CORE** | Retained in core's `homebrew.casks` for the core-only browser baseline. |
| `pkgs.rclone` | `flake.nix:175` | **STAY CORE** | Owned by storage domain; destination uses direct package closure `${pkgs.rclone}` without module dependency. |
| `pkgs.python3` + `playwright` | `flake.nix:22-33,180-183` (`mkManagedPython`) | **SPLIT** | Core retains managed Python; destination provides its own hermetic Python + Playwright package closure for `cnt-wallpaper-gallery`. |
| `pkgs.bash` | `modules/brave-youtube.nix:24` | **STAY CORE** | Standard nixpkgs shell dependency. |

No dedicated flake input exists in core for Browser UX.

### 1.2 Packages, commands, and overlay attributes

Current core `packages/dinas-commands/default.nix` packages 34 user-facing commands
and performs wholesale copying of all `libexec/*` subtrees into the package output
(`packages/dinas-commands/default.nix:64-65`).

#### Command inventory (Arithmetic: 34 total - 10 Browser UX = 24 remaining core)

| Command | Current source | Ownership | Destination surface |
|---|---|---|---|
| `brave-youtube-cdp` | `bin/brave-youtube-cdp` | **MOVE** | `browser-ux_dinas` command package |
| `brave-youtube-session` | `bin/brave-youtube-session` | **MOVE** | `browser-ux_dinas` command package |
| `clear-new-tab-sync` | `bin/clear-new-tab-sync` | **MOVE** | `browser-ux_dinas` command package |
| `cnt-wallpaper-gallery` | `bin/cnt-wallpaper-gallery` | **MOVE** | `browser-ux_dinas` command package |
| `shared-images-db-audit` | `bin/shared-images-db-audit` | **MOVE** | `browser-ux_dinas` command package |
| `shared-images-promote-original` | `bin/shared-images-promote-original` | **MOVE** | `browser-ux_dinas` command package |
| `shared-images-rclone-sync` | `bin/shared-images-rclone-sync` | **MOVE** | `browser-ux_dinas` command package |
| `shared-images-refresh-artist-links` | `bin/shared-images-refresh-artist-links` | **MOVE** | `browser-ux_dinas` command package |
| `shared-images-rehash` | `bin/shared-images-rehash` | **MOVE** | `browser-ux_dinas` command package |
| `shared-images-workflow-layout` | `bin/shared-images-workflow-layout` | **MOVE** | `browser-ux_dinas` command package |
| *24 remaining commands* | `bin/{arq-*,codex-*,dinas-*,nix-*,...}` | **STAY CORE** | Retained in `packages/dinas-commands/default.nix` |

#### Wholesale `libexec` packaging subtrees

| Directory | Current source | Disposition at cut |
|---|---|---|
| `libexec/brave-youtube/` | 10 helper files | **MOVE** to `browser-ux_dinas`; prune from core packaging |
| `libexec/clear-new-tab/` | 1 helper file | **MOVE** to `browser-ux_dinas`; prune from core packaging |
| `libexec/shared-images/` | 6 helper files | **MOVE** to `browser-ux_dinas`; prune from core packaging |

#### Destination command packaging layout and namespace surface

1. **Packaging layout:** `brave-youtube-session` resolves its helper implementations
   using `$(script_dir)/../libexec/brave-youtube/*`. Therefore, the destination package
   must preserve the exact `$out/bin` and `$out/libexec` sibling structure.
2. **Command namespace decision:** The destination-owned DINAS command namespace
   `dinas.commandNamespaces.browser-ux = { package = <browser-ux commands>; binSubdir = "bin"; };`
   is the destination command surface. No browser-owned ordinary
   `environment.systemPackages` contribution is needed to preserve the command
   names.
3. **Collision prevention during overlap:** `dinas.commandNamespaces` exports a
   whole `${package}/${binSubdir}` (`modules/dinas/command-namespaces.nix:17-57`)
   with no per-command suppression, and `bin/dinas-reconcile:95-109` reports
   collisions only as warnings. The destination therefore does **not** register
   the namespace in any selected composition before the cut. During `BUX5` the
   command package is built and tested only through destination
   `checks.<system>`. `dinas.devNamespaces` may be used for operator trials, but
   never in a selected composition. The destination cut candidate registers the
   namespace in the same revision as handoff removal. The cut gate requires
   `dinas-reconcile --check` on the new/new pair to report zero collision warnings.
   After the cut, core's `dinas` namespace contains none of the 10 names, so no
   duplicate core wrapper remains.

### 1.3 Darwin modules

| Module file | Current core lines | Owned slice | Cut disposition |
|---|---|---|---|
| `modules/browsers.nix` | 1–64 | Split into core baseline seam and host-policy overlay | **SPLIT** (core keeps installation & generic seam; overlay moves) |
| `modules/brave-youtube.nix` | 1–36 | Entire module (Brave YouTube activation) | **MOVE** to `browser-ux_dinas`; remove import from `flake.nix` |
| `modules/brave-youtube-app-writer.sh` | 1–173 | Entire activation helper script | **MOVE** to `browser-ux_dinas` |
| `modules/video-session.nix` | 1–20 | Entire module (power controls for video sessions) | **MOVE** to `browser-ux_dinas`; remove import from `flake.nix` |

**Overlap writer rule:** The destination copies of `brave-youtube.nix`,
`brave-youtube-app-writer.sh`, and `video-session.nix` are present but **not
imported** by `darwinModules.default` during `BUX4`–`BUX5` (ADR 0004 5A State C;
see roadmap 1.3). Core remains the sole writer of the Brave YouTube bundle and
the video-session sudoers. The destination cut candidate re-enables both imports
in the same revision that removes the claim handoff. The matched-cut gate proves
exactly one destination-owned writer per side effect.
| `modules/user-home-writer-common.sh` | 1–123 | Shared helper for user-home app writers | **STAY CORE**; exact copy vendored into destination |
| `flake.nix` | 251 (`browsers.nix`, stays), 254–255 | Imports of `modules/video-session.nix` and `modules/brave-youtube.nix` | **SPLIT**: remove lines 254–255 at matched cut; keep 251 |

#### `user-home-writer-common.sh` ownership decision

`modules/user-home-writer-common.sh` is shared across multiple core modules
(`pyenv.nix`, `kitty.nix`, `arq.nix`, `sdkman.nix`, `oh-my-zsh.nix`, `shell.nix`,
`nvm.nix`, `rbenv.nix`, `fastfetch.nix`).
- **Options:** (a) core exposes a stable generic helper output that the
  destination consumes; (b) the destination vendors an exact copy.
- **Trade-off:** (a) avoids duplication but creates a destination build-time
  dependency on a core helper contract. Every core helper change would become a
  cross-owner interface change, and the destination could not qualify standalone
  against its own sources. (b) duplicates about 120 lines but keeps the
  destination self-contained. This follows `developer-ux_dinas`, whose writers
  do not consume core helper outputs.
- **Architecture decision: (b) vendor.** At `BUX4` the destination adds
  `modules/user-home-writer-common.sh` byte-identical to core. At entry, core's
  copy has sha256
  `bc8de7573905c5ae5fa4aac581685fddfab074e77a70fe9d61c8203d02c843a0`
  (recomputed at closeout). That hash is **provenance evidence** recorded with the
  `BUX4` parity artifact, not a permanent pin.
- **Drift control:** During overlap (`BUX4`–`BUX6`), the parity and cut semantic
  gates compare the vendored copy against the selected core copy. Divergence
  before the cut is a parity failure. After the cut, the copy is destination-owned
  and may diverge only through reviewed destination changes. There is no
  composition-level special case and no permanent cross-repository drift test.

### 1.4 Options and generic seam contract

Core currently declares no options for browser policy.

#### Option A: Core generic browser-baseline seam (Sole Mechanical Writer)

To eliminate any risk of conflicting root writers or order-dependent writes,
core provides a generic option/render seam in `modules/browsers.nix`:

```nix
programs.browserBaseline = {
  enable = lib.mkEnableOption "core browser baseline and generic policy seam";
  legacyPolicyDefaults = {
    enable = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Enable core legacy browser policy defaults until destination overlay takes over";
    };
  };
  chrome = {
    managedPreferences = lib.mkOption {
      type = lib.types.attrsOf lib.types.anything;
      default = {};
      description = "Structured Chrome managed preferences rendered to /Library/Managed Preferences/com.google.Chrome.plist";
    };
    externalExtensions = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = {};
      description = "Map of extension ID to update URL rendered to /Library/Application Support/Google/Chrome/External Extensions/<id>.json";
    };
    nativeMessagingHosts = lib.mkOption {
      type = lib.types.attrsOf lib.types.path;
      default = {};
      description = "Map of manifest name to JSON path symlinked in /Library/Google/Chrome/NativeMessagingHosts";
    };
  };
  firefox = {
    policies = lib.mkOption {
      type = lib.types.attrsOf lib.types.anything;
      default = {};
      description = "Structured Firefox policies rendered to distribution/policies.json";
    };
    nativeMessagingHosts = lib.mkOption {
      type = lib.types.attrsOf lib.types.path;
      default = {};
      description = "Map of manifest name to JSON path symlinked in /Library/Application Support/Mozilla/NativeMessagingHosts";
    };
  };
};
```

#### Seam merge, override, and single-writer rules

1. **Option types reject conflicting duplicates:** Seam leaf values accept exactly
   one definition per key. Two modules defining the same key fail evaluation.
2. **Priority overrides rejected mechanically:** A seam guard rejects any seam
   *definition* carrying a non-default priority (`mkForce`, `mkOverride`,
   `mkDefault`, `mkOptionDefault`). This prevents "last writer wins" at the
   option-evaluation level. `BUX2` ships negative core checks proving that a
   duplicate key and an `mkForce`/`mkOverride` definition each fail. The
   `legacyPolicyDefaults.enable` *declaration* default (`default = true`, shown
   above) is an option-declaration default, not a priority definition.
2a. **Legacy defaults lifecycle:** While `legacyPolicyDefaults.enable` is true, core
   renders its current values from `config/browser/*`. A seam assertion rejects
   legacy defaults together with any non-empty destination contribution. The
   destination sets the flag `false` from `BUX3` to `BUX5`. At the matched cut,
   the core candidate deletes both the option and `config/browser/*`, and the
   destination cut candidate deletes its assignment. A post-cut core-only build
   renders the empty bare baseline, and no host-specific value remains in core.
3. **Mechanical single writer:** Core's `modules/browsers.nix` is the **only**
   Nix module in the entire system that writes to:
   - `/Library/Managed Preferences/com.google.Chrome.plist`
   - `/Library/Application Support/Google/Chrome/External Extensions/`
   - `/Library/Google/Chrome/NativeMessagingHosts/`
   - `/Library/Application Support/Mozilla/NativeMessagingHosts/`
   - `/Applications/Firefox.app/Contents/Resources/distribution/policies.json`
4. **Destination role:** `browser-ux_dinas` contributes structured attribute sets
   into `programs.browserBaseline.*`.
5. **Absent-contribution and removal semantics:** When an extension, policy, or
   manifest is not contributed (e.g. on destination rollback or core-only bootstrap):
   - Uncontributed NativeMessagingHosts symlinks are actively removed by the seam
     activation script to prevent dangling Nix store pointers upon garbage collection.
   - When external extensions are empty, managed extension JSON files are cleaned up.
   - When Chrome managed preferences are empty, the managed plist is removed.
6. **Extension policy modes and durability classes:** Current extension policy modes
   (`normal_installed`, `force_installed`) and extension IDs are migration inputs (section 1.19),
   not necessarily final enablement classes. The seam option structure (`attrsOf anything`
   in `managedPreferences` and `policies`) carries `ExtensionSettings` and
   `ExtensionInstallForcelist` without requiring class-aware option types in BUX2.
   BUX3 will resolve the mapping of existing extensions into `required`, `default-on`,
   and `allowed` durability classes (roadmap 1.9) and address how the
   one-definition-per-leaf-key guard applies to the composite `ExtensionSettings` dictionary
   when multiple modules or roster entries contribute.

### 1.5 Host facts

`none`. No key in core's host table is read by this domain. Target user and home
directories are resolved generically from `config.system.primaryUser` and
`config.users.users.${primaryUser}.home`.

### 1.6 Application claims

| Application | Canonical ID | Provider | Package | Current core location | Migration disposition |
|---|---|---|---|---|---|
| Google Chrome | `bundle:com.google.Chrome` | `nix` | `google-chrome` | `lib/core-application-inventory.nix:86` | **STAY CORE** |
| Firefox | `bundle:org.mozilla.firefox` | `homebrew-cask` | `firefox` | `lib/core-application-inventory.nix:62` | **STAY CORE** |
| Brave YouTube | `bundle:com.operator.brave-youtube` | `nix` | `brave` | `lib/core-application-inventory.nix:92` | **MOVE** to `browser-ux_dinas` |

### 1.7 Configuration files and templates

| Configuration path | Declaring surface | Target / Consumer | Cut disposition |
|---|---|---|---|
| `config/browser/chrome-policy.plist` | `modules/browsers.nix:4` | Chrome managed preferences | **SPLIT/MOVE** (values migrate to destination; file retired in core) |
| `config/browser/chrome-clear-new-tab-external-extension.json` | `modules/browsers.nix:5` | Chrome external extension | **MOVE** to `browser-ux_dinas` |
| `config/browser/firefox-policies.json` | `modules/browsers.nix:6` | Firefox distribution policy | **MOVE** to `browser-ux_dinas` |
| `config/browser/keepassxc-chrome-native-messaging.json` | `modules/browsers.nix:7` | Chrome KeePassXC NMH | **MOVE** to `browser-ux_dinas` |
| `config/browser/keepassxc-firefox-native-messaging.json` | `modules/browsers.nix:8` | Firefox KeePassXC NMH | **MOVE** to `browser-ux_dinas` |
| `config/brave-youtube/launcher.sh.template` | `modules/brave-youtube.nix:12` | Brave YouTube App bundle launcher | **MOVE** to `browser-ux_dinas` |
| `config/brave-youtube/theme-manifest.json` | `modules/brave-youtube.nix:13` | Brave YouTube custom theme manifest | **MOVE** to `browser-ux_dinas` |
| `config/power/video-session-pmset.sudoers` | `modules/video-session.nix:4` | `/private/etc/sudoers.d/video_session_pmset` | **MOVE** to `browser-ux_dinas` |

### 1.8 Executables and runtime authority inventory

Every executable and helper script moving to `browser-ux_dinas` is audited for its
current runtime interpreter authority and target destination package closure:

| Script / Helper | Current Shebang / Invocation | Current Authority | Target Authority in Destination |
|---|---|---|---|
| `bin/brave-youtube-cdp` | `#!/usr/bin/env python3` | Mutable PATH | Hermetic wrapper using `${pkgs.python3}/bin/python3` |
| `bin/brave-youtube-session` | `#!/usr/bin/env bash` | Mutable PATH | Hermetic wrapper using `${pkgs.bash}/bin/bash` |
| `bin/clear-new-tab-sync` | `#!/usr/bin/env bash` + `/usr/bin/python3` (line 60) | Apple CLT python | Hermetic wrapper using `${pkgs.python3}/bin/python3` |
| `bin/cnt-wallpaper-gallery` | `#!/usr/bin/env python3` + `/run/current-system/sw/bin/python3` (line 696); Chrome executable search (lines 680–690): override, `/Applications/Nix Apps/Google Chrome.app`, `/Applications/Google Chrome.app`, `/run/current-system/Applications/Google Chrome.app` | Ambient live-system core managed Python with Playwright; mutable live-system/ordered-path search for core-owned Chrome | Destination-owned closure `pkgs.python3.withPackages (ps: [ ps.playwright ])`; Chrome is an explicit override or the core-owned Chrome path passed in by the destination package (cross-owner runtime dependency on core Chrome, recorded deliberately); no `/run/current-system` authority |
| `bin/shared-images-db-audit` | `#!/usr/bin/env python3` | Mutable PATH | Hermetic wrapper using `${pkgs.python3}/bin/python3` |
| `bin/shared-images-promote-original` | `#!/usr/bin/env python3` | Mutable PATH | Hermetic wrapper using `${pkgs.python3}/bin/python3` |
| `bin/shared-images-rclone-sync` | `#!/usr/bin/env bash` + `RCLONE="${RCLONE:-rclone}"` | Mutable PATH | Explicit `${pkgs.rclone}/bin/rclone` default with test override retained |
| `bin/shared-images-refresh-artist-links` | `#!/usr/bin/env python3` | Mutable PATH | Hermetic wrapper using `${pkgs.python3}/bin/python3` |
| `bin/shared-images-rehash` | `#!/usr/bin/env python3` | Mutable PATH | Hermetic wrapper using `${pkgs.python3}/bin/python3` |
| `bin/shared-images-workflow-layout` | `#!/usr/bin/env python3` | Mutable PATH | Hermetic wrapper using `${pkgs.python3}/bin/python3` |
| `libexec/brave-youtube/allocate-debug-port.py` | `#!/usr/bin/env python3` | Invoked by session script | Invoked by wrapped session script |
| `libexec/brave-youtube/display-notification.applescript` | AppleScript source | Invoked via `/usr/bin/osascript` | Invoked via `/usr/bin/osascript` |
| `libexec/brave-youtube/first-field.awk` | Awk source | Invoked via `awk -f` | Invoked via `awk -f` |
| `libexec/brave-youtube/generate-icon.py` | `#!/usr/bin/env python3` (stdlib only) | Invoked by app writer | Invoked by app writer with explicit Python |
| `libexec/brave-youtube/generate-theme-assets.py` | `#!/usr/bin/env python3` (stdlib only) | Invoked by app writer | Invoked by app writer with explicit Python |
| `libexec/brave-youtube/pmset-battery-percent.awk` | Awk source | Invoked via `awk -f` | Invoked via `awk -f` |
| `libexec/brave-youtube/pmset-power-source.awk` | Awk source | Invoked via `awk -f` | Invoked via `awk -f` |
| `libexec/brave-youtube/pmset-sleep-disabled.awk` | Awk source | Invoked via `awk -f` | Invoked via `awk -f` |
| `libexec/brave-youtube/profile-extension-audit.py` | `#!/usr/bin/env python3` | Invoked by session script | Invoked by wrapped session script |
| `libexec/brave-youtube/write-launch-plist.py` | `#!/usr/bin/env python3` | Invoked by session script | Invoked by wrapped session script |
| `libexec/clear-new-tab/latest-source-extension-dir.py` | `#!/usr/bin/env python3` | Invoked by sync script | Invoked by wrapped sync script |
| `libexec/shared-images/artist_links.py` | Python module (stdlib only) | Imported by commands | Imported by destination commands |
| `libexec/shared-images/db_audit.py` | Python module (stdlib only) | Imported by commands | Imported by destination commands |
| `libexec/shared-images/promote_original.py` | Python module (stdlib only) | Imported by commands | Imported by destination commands |
| `libexec/shared-images/rehash.py` | Python module (stdlib only) | Imported by commands | Imported by destination commands |
| `libexec/shared-images/shared_images_lib.py` | Python module (stdlib only) | Imported by commands | Imported by destination commands |
| `libexec/shared-images/workflow_layout.py` | Python module (stdlib only) | Imported by commands | Imported by destination commands |

**Runtime state vs. canonical durable authority:** `bin/clear-new-tab-sync` and
`bin/cnt-wallpaper-gallery` currently operate directly on Chrome's and Firefox's live
profile IndexedDB. That live browser profile state is observed mutable runtime state,
NOT future canonical authority. Although `config/browser/README.md:151-154` historically
stated that "the normal Chrome profile remains the source of truth for the wallpaper lineup",
that design is superseded by the Clear New Tab durable authority doctrine (roadmap 1.11).
BUX5 establishes a Browser UX-owned durable representation outside the Nix store and
outside browser profiles, and re-points `clear-new-tab-sync` to consume that canonical
durable state.

### 1.9 Test surfaces, fixtures, and support harnesses

There are exactly 30 test and support files in `src/test/` referencing the Browser UX
domain. Each is classified below:

| Test / Support Path | Scope / Coverage | Cut Disposition |
|---|---|---|
| `src/test/int/bash/bash-entrypoints.int.test.bats` | Asserts entrypoints for commands including domain commands | **SPLIT**: remove domain assertions; retain core commands |
| `src/test/int/bash/test-runner.int.test.bats` | Uses `display-notification.applescript` in fake-repo tests | **SPLIT**: replace with neutral core fixture |
| `src/test/int/nix/application-claims.int.test.nix` | Asserts claim count (28) and `com.operator.brave-youtube` | **SPLIT**: count 28 -> 27; remove Brave YouTube claim assertion; retain Chrome and Firefox claims |
| `src/test/int/nix/darwin-configuration.int.test.nix` | Asserts Brave YouTube, video-session, and browser policies | **SPLIT**: remove domain assertions; retain Chrome package and Firefox cask checks |
| `src/test/int/python/python_entrypoints.int.test.py` | Asserts Python CLI entrypoints for domain commands | **SPLIT**: remove domain command tests; retain core commands |
| `src/test/support/bash/brave-youtube-session-coverage-harness` | Bats coverage harness for `brave-youtube-session` | **MOVE** to `browser-ux_dinas` |
| `src/test/unit/applescript/display-notification.unit.test.bats` | Unit test for notification AppleScript | **MOVE** to destination; core adds neutral fixture test |
| `src/test/unit/awk/parser.unit.test.bats` | Parses all awk files including `first-field.awk` & `pmset-*.awk` | **SPLIT**: domain awk files move to destination |
| `src/test/unit/bash/bash-runtime-coverage.unit.test.bats` | Asserts bash coverage manifest entries | **SPLIT**: remove domain rows; retain core rows |
| `src/test/unit/bash/brave-youtube-config.unit.test.bats` | Unit test for Brave YouTube configuration | **MOVE** to `browser-ux_dinas` |
| `src/test/unit/bash/brave-youtube-session.unit.test.bats` | Unit test for Brave YouTube session script | **MOVE** to `browser-ux_dinas` |
| `src/test/unit/bash/browser-config.unit.test.bats` | Tests Chrome plist and Firefox policies | **SPLIT**: core tests generic seam; destination tests overlay values |
| `src/test/unit/bash/clear-new-tab-sync.unit.test.bats` | Unit test for Clear New Tab sync | **MOVE** to `browser-ux_dinas` |
| `src/test/unit/bash/repo-structure.unit.test.bats` | Enforces AppleScript lane (38–49, 56–66) and `shared_images_lib.py` + commands (207–247) | **SPLIT**: remove `shared_images_lib.py` and shared-images command checks; retain AppleScript lane check with neutral fixture |
| `src/test/unit/bash/security-config.unit.test.bats` | Asserts sudoers including `video-session-pmset.sudoers` | **SPLIT**: remove video-session checks; retain core sudoers |
| `src/test/unit/bash/user-home-app-writers.unit.test.bats` | Tests `brave-youtube-app-writer.sh` | **SPLIT**: remove Brave YouTube test; retain Kitty tests |
| `src/test/unit/bash/user-home-writers.unit.test.bats` | Tests `modules/user-home-writer-common.sh` | **STAY CORE**: tests shared common writer |
| `src/test/unit/nix/flake.unit.test.nix` | Tests imports of `brave-youtube.nix` and `video-session.nix` | **SPLIT**: remove domain module import assertions |
| `src/test/unit/python/brave_youtube_asset_generators.unit.test.py` | Unit tests for icon and theme asset generators | **MOVE** to `browser-ux_dinas` |
| `src/test/unit/python/brave_youtube_helpers.unit.test.py` | Unit tests for debug port and plist helpers | **MOVE** to `browser-ux_dinas` |
| `src/test/unit/python/clear_new_tab.unit.test.py` | Unit test for Clear New Tab extension helper | **MOVE** to `browser-ux_dinas` |
| `src/test/unit/python/python_entrypoints.unit.test.py` | Unit test for Python CLI entrypoints | **SPLIT**: remove domain command checks |
| `src/test/unit/python/shared_images_artist_links.unit.test.py` | Unit test for artist links helper | **MOVE** to `browser-ux_dinas` |
| `src/test/unit/python/shared_images_db_audit.unit.test.py` | Unit test for DB audit helper | **MOVE** to `browser-ux_dinas` |
| `src/test/unit/python/shared_images_lib.unit.test.py` | Unit test for shared images library | **MOVE** to `browser-ux_dinas` |
| `src/test/unit/python/shared_images_promote_original.unit.test.py` | Unit test for original promotion helper | **MOVE** to `browser-ux_dinas` |
| `src/test/unit/python/shared_images_rehash.unit.test.py` | Unit test for rehash helper | **MOVE** to `browser-ux_dinas` |
| `src/test/unit/python/shared_images_workflow_layout.unit.test.py` | Unit test for workflow layout helper | **MOVE** to `browser-ux_dinas` |
| `src/test/unit/python/test_report_integration.unit.test.py` | References `display-notification.applescript` at 165, 225 | **SPLIT**: update fixture reference to neutral core fixture |
| `src/test/unit/python/user_home_privilege_boundary.unit.test.py` | Asserts privilege category D for `brave-youtube.nix` and `video-session.nix` | **SPLIT**: remove domain module assertions |

#### AppleScript test lane disposition

- `libexec/brave-youtube/display-notification.applescript` is currently the sole
  `.applescript` file in core.
- `src/test/unit/bash/repo-structure.unit.test.bats:44` requires `src/test/unit/applescript`
  to exist.
- Lines 56–66 prune `src/test` and require every non-test `.applescript` file to have
  a unit test.
- **Architecture decision:** Retain the generic AppleScript test lane in core.
  Introduce a neutral core-owned product fixture `libexec/core/sample-notification.applescript`
  with unit test `src/test/unit/applescript/sample-notification.unit.test.bats` and
  manifest row in `coverage-manifest.tsv`. This preserves core's multi-language
  test-runner validation capability without depending on the migrated Brave YouTube
  product.

### 1.10 Coverage manifest accounting (`src/test/coverage-manifest.tsv`)

- **Total rows matching domain paths:** exactly **44 rows**.
- **Rows to be REMOVED at cut (`BUX6`):** exactly **42 rows** (corresponding to all
  moving `bin/`, `libexec/`, `modules/brave-youtube.nix`, `modules/video-session.nix`,
  and `modules/brave-youtube-app-writer.sh` files).
- **Rows RETAINED in core (SPLIT residue):** exactly **2 rows**:
  - `src/test/coverage-manifest.tsv:12` (`int nix modules/browsers.nix`)
  - `src/test/coverage-manifest.tsv:159` (`unit nix modules/browsers.nix`)
  `modules/browsers.nix` stays in core to declare the Google Chrome package, Firefox
  cask, and the generic policy composition seam.

### 1.11 Documentation, ADRs, and skills

| Document / Asset | Current location | Disposition |
|---|---|---|
| Domain ownership and sequencing | `docs/adr/2026/08/0002-domain-ownership-application-claims-and-migration-sequencing.md` | **AMEND** (rename `browser-ux-nd` -> `browser-ux_dinas`, record core baseline, rclone boundary, KeePassXC split) |
| Shared Images workflow skill | `docs/ops/skills/shared-images-workflow/SKILL.md` | **MOVE** to `browser-ux_dinas`; sanitize private paths & remove continuation-doc dependency |
| Browser hardening skill | `docs/contrib/skills/browser-hardening-policy/**` (including `agents/openai.yaml`, which is display/interface metadata only, not an exposure mechanism) | **MOVE** to `browser-ux_dinas` |
| Tracked Claude skill link | `.claude/skills/shared-images-workflow` -> `../../docs/ops/skills/shared-images-workflow` | **MOVE/REMOVE** from core at cut; replaced by project-local archive links (1.15) |
| Tracked Claude skill link | `.claude/skills/browser-hardening-policy` -> `../../docs/contrib/skills/browser-hardening-policy` | **MOVE/REMOVE** from core at cut; destination-checkout project-local link replaces it (1.15) |
| Tracked `.agents` skill root | `.agents/skills/nix-darwin/ops` -> `../../../docs/ops/skills` | **SPLIT**: `docs/ops/skills` contains only `shared-images-workflow`; remove the link at cut unless another ops skill exists then |
| Tracked `.agents` skill root | `.agents/skills/nix-darwin/contrib` -> `../../../docs/contrib/skills` | **STAY CORE** (still exposes `nix-darwin-composition-maintainer`); loses `browser-hardening-policy` when the directory moves |
| Codex tooling desired state | `config/codex-tooling/codex-tooling-desired-state.json:174-176` (`path = skills/flakes/nix-darwin/shared-images-workflow/SKILL.md`, a stale `.flakes`-era future-source path that does not match the live source) | **SPLIT**: remove the `shared-images-workflow` entry at cut `BUX6`; do not repoint it (project-local discovery replaces global exposure) |

### 1.12 Activation effects and privilege boundaries

#### Root/system writes (executed during `postActivation` by root)

1. `/Library/Managed Preferences/com.google.Chrome.plist` (mode `0644`, owner `root:wheel`)
2. `/Library/Application Support/Google/Chrome/External Extensions/felphkbfjadmcejnibcmcncimlappdde.json` (mode `0644`, owner `root:wheel`)
3. `/Library/Google/Chrome/NativeMessagingHosts/org.keepassxc.keepassxc_browser.json` (symlink, owner `root:wheel`)
4. `/Library/Application Support/Mozilla/NativeMessagingHosts/org.keepassxc.keepassxc_browser.json` (symlink, owner `root:wheel`)
5. `/Applications/Firefox.app/Contents/Resources/distribution/policies.json` (mode `0644`, owner `root:wheel`)
6. `/private/etc/sudoers.d/video_session_pmset` (mode `0440`, owner `root:wheel`, validated via `visudo -cf`).
   Scope: host-wide `%admin ALL=(ALL) NOPASSWD: PMSET_VIDEO_SESSION`, where
   `PMSET_VIDEO_SESSION` is exactly `/usr/bin/pmset -a disablesleep 1` and
   `/usr/bin/pmset -a disablesleep 0`. The sole runtime consumer is
   `bin/brave-youtube-session`, and no non-browser consumer exists. Video-session
   therefore **MOVES** with Brave YouTube; no generic core power seam is warranted.

#### Firefox policy: deliberate cross-owner write into a core-owned bundle

The BUX2 parity baseline wrote `policies.json` inside `/Applications/Firefox.app`,
whose binary and claim stay core-owned. The following bundle-mode semantics are
retained as migration history and fallback requirements; BUX3B2A subsequently
passed live proof for `macos-preferences`, outside the bundle:

- **Writer:** Only the core seam performs the physical write. The destination
  supplies values only, so the write never depends on destination activation
  ordering.
- **Ordering:** The seam write runs in `postActivation`, after nix-darwin's
  Homebrew cask step installs or upgrades Firefox in the same activation.
- **Presence:** Current behavior silently skips (`echo`) when `Firefox.app` is
  absent. The future seam must keep activation non-fatal but emit a loud warning
  whenever Firefox policy values are contributed and the bundle is absent. The
  `BUX8` canary, and every later deployment verification, fails if contributed
  policies are not present byte-identically. A skip is therefore visible drift,
  never silent permanent drift.
- **Cask upgrades and durability gap:** Homebrew cask upgrades outside activation replace the bundle
  and drop `policies.json`. BUX3B2A addressed this gap with bundle-external
  `macos-preferences`; bundle-mode fallback still requires explicit post-update
  reconciliation (section 8).
- **Bundle-external alternative and BUX3 durability gate:** BUX2 preserved the in-bundle writer
  as a behavior-preserving seam. BUX3 introduces an explicit gate for a bundle-external
  macOS-supported enterprise-policy delivery mechanism (preference domain `org.mozilla.firefox`).
  BUX3B1 implemented the generic core delivery seam (`programs.browserBaseline.firefox.policyDelivery`
  writing `/Library/Preferences/org.mozilla.firefox.plist` root:wheel 0644).
  BUX3B2A adoption passed the live proof-of-effect requirement; the accepted
  evidence is recorded in the roadmap BUX3 status. The governing requirements remain:
  1. Proof of *effect* (e.g. policy enforcement verified in `about:policies` in Firefox), not merely
     file/plist presence, since non-MDM managed-preference delivery on macOS must be verified;
  2. Any new delivery path or post-update reconciler remains within the core generic seam or an explicitly
     declared single writer, added to the roadmap 1.2 sole-writer path list and to the destination zero-root-path
     activation check;
  3. If a bundle-external mechanism cannot preserve required semantics, retain the in-bundle writer only with an
     explicit post-update reconciler (owned by core, e.g. launchd agent/daemon) that repairs policy loss without
     requiring an unrelated Darwin rebuild.

#### User-home writes (executed by target user via `sudo -n -H -u "$target_user"`)

1. `$HOME/Applications/Brave YouTube.app` (generated application bundle)
2. `$HOME/Library/Application Support/Brave YouTube/` (user data directory)
3. `$HOME/Library/Application Support/Brave YouTube/brave-youtube-theme/` (theme assets)

#### Legacy cleanup behavior disposition

| Cleanup action | Current location | Future disposition |
|---|---|---|
| `defaults delete` for stale `PasswordManagerEnabled`, `AutofillCreditCardEnabled`, `AutofillAddressEnabled` | `modules/browsers.nix:24-29` | **ONE-TIME MIGRATION CLEANUP / RETIRE** (keys do not exist in Chrome plist; only Firefox policies) |
| `killall cfprefsd` | `modules/browsers.nix:30` | **STAY CORE** (owned by core seam plist writer) |
| `recommendedChromePolicy` cleanup (`/Library/Preferences/com.google.Chrome.plist`) | `modules/browsers.nix:32-35` | **ONE-TIME MIGRATION CLEANUP** |
| `amphetamine_PowerProtect` sudoers removal | `modules/video-session.nix:17` | **ONE-TIME MIGRATION CLEANUP** |
| Legacy YouTube apps cleanup (`YouTube Chrome.app`, `YouTube Brave.app`, shortcut ID `agimnkijcaahngcdmfeangaknmldooml`) | `modules/brave-youtube-app-writer.sh:82-97` | **MOVE** to destination with app writer |

### 1.13 launchd agents and daemons

`none` in current system activation.
`brave-youtube-session daemon` dynamically creates a user-level launch agent
`$HOME/Library/LaunchAgents/local.brave-youtube-session.plist` during runtime session
execution. This is operator session state, not system activation.

*Durability note (BUX3):* If BUX3 adopts an automated post-update reconciler for Firefox
out-of-band updates without Darwin rebuilds, an agent or daemon (likely launchd-managed
in core) may be introduced to monitor and restore `policies.json`.

### 1.14 State roots

- **User-home state:**
  - `$HOME/Applications/Brave YouTube.app`
  - `$HOME/Library/Application Support/Brave YouTube/`
  - `$HOME/.local/state/brave-youtube-session/`
  - `$HOME/Pictures/shared-images/` (external operator content archive)
  - Observed mutable Clear New Tab runtime state (NOT canonical authority). These
    are the roots current code reads or replaces (`bin/clear-new-tab-sync:100-109`,
    `bin/cnt-wallpaper-gallery:412-426`); BUX5 preserve-displaced-state recovery
    must cover every one of them. Chrome paths are relative to
    `$HOME/Library/Application Support/Google/Chrome/`; Firefox paths to
    `$HOME/Library/Application Support/Firefox/`.
    - Chrome `Default/IndexedDB/chrome-extension_felphkbfjadmcejnibcmcncimlappdde_0.indexeddb.leveldb/`
    - Chrome `Default/IndexedDB/chrome-extension_felphkbfjadmcejnibcmcncimlappdde_0.indexeddb.blob/`
    - Chrome `Default/Local Extension Settings/felphkbfjadmcejnibcmcncimlappdde/`
    - Firefox `Profiles/<profile>/storage/default/moz-extension+++<uuid>/`
    - Firefox `Profiles/<profile>/storage/default/moz-extension+++<uuid>^userContextId=4294967295/`
      (`<uuid>` is the profile-local extension UUID resolved at runtime)
    - Brave YouTube's copies of the Chrome roots under
      `$HOME/Library/Application Support/Brave YouTube/Default/` (derived consumer
      state written by `clear-new-tab-sync brave-youtube`)
  - *Durable Clear New Tab authority root (BUX5):* BUX5 establishes a Browser UX-owned durable representation outside browser profiles and outside the Nix store (with native backup/export preference) to reconstruct collection membership, ordering, settings, and stable image mapping.
- **Root/system state:**
  - `/Library/Managed Preferences/`
  - `/Library/Application Support/Google/Chrome/External Extensions/`
  - `/Library/Google/Chrome/NativeMessagingHosts/`
  - `/Library/Application Support/Mozilla/NativeMessagingHosts/`
  - `/Applications/Firefox.app/Contents/Resources/distribution/`
  - `/private/etc/sudoers.d/video_session_pmset`

### 1.15 Skills

1. `shared-images-workflow`: Moves to `browser-ux_dinas`. Project-local skill links
   at `$HOME/Pictures/shared-images/.agents/skills/shared-images-workflow` and
   `$HOME/Pictures/shared-images/.claude/skills/shared-images-workflow` are established
   via an operator action (e.g. `shared-images-workflow-layout` or setup tool) when
   the archive exists. It must not be written by unconditional host activation.
   Publication blockers that must be removed before the skill is canonical in the
   public-capable destination: the absolute `/Users/operator/Pictures/shared-images/...`
   paths (`SKILL.md` lines 3, 18–23 and following), and the required read of the
   private continuation document
   `/Users/operator/ai/agent-central/common/usr/docs/shared-images/cont/shared-images-continuation-2026-06-14.md`
   (line 14). Replace them with `$HOME`-relative paths and self-contained guidance.
2. `browser-hardening-policy`: Moves to `browser-ux_dinas`. Current exposure is the
   tracked `.claude/skills/browser-hardening-policy` link plus the
   `.agents/skills/nix-darwin/contrib` directory link. It has no codex-tooling
   desired-state entry. Future discovery is project-local in the destination
   checkout, through the destination's own tracked `.agents/skills` and
   `.claude/skills` links. It is not globally exposed from core. Its
   `agents/openai.yaml` moves with it as display metadata.

### 1.16 Shell contributions

`none`.

### 1.17 Allowlist keys

`none` in core.

### 1.18 System profile entries

- `pkgs.google-chrome` in `environment.systemPackages`: **STAY CORE**.
- Homebrew cask `firefox`: **STAY CORE**.
- `pkgs.rclone` in `environment.systemPackages`: **STAY CORE** (storage domain).

### 1.19 Historical core extension policy modes and IDs (BUX3 migration inputs)

The following managed extension roster and install modes are historical BUX3
migration inputs, not the newer BUX3B2B durability candidate on Browser UX `main`.
The tracked policy sources are authoritative for these inputs:
`config/browser/chrome-policy.plist` (`ExtensionInstallForcelist` :67-70,
`ExtensionSettings` :72-151) and `config/browser/firefox-policies.json`
(`ExtensionSettings` :25-53). The tables below mirror them with line citations.
BUX3 expected-ID/mode assertions must be derived mechanically from these sources
(or from the destination's migrated copies after parity), never from this table.

#### Chrome managed extensions

| Extension ID | Name | Current Policy Mode | Current Delivery Surface (`chrome-policy.plist` line) |
|---|---|---|---|
| `felphkbfjadmcejnibcmcncimlappdde` | Clear New Tab | `force_installed` | `ExtensionInstallForcelist` (:69), `ExtensionSettings` (:88-94), and External Extensions JSON |
| `ddkjiahejlhfcafbddmgiahcphecmpfh` | uBlock Origin Lite | `normal_installed` | `ExtensionSettings` (:80) |
| `oboonakemofpalcgghocfoadofidjkkk` | KeePassXC-Browser | `normal_installed` | `ExtensionSettings` (:96) |
| `hlkenndednhfkekhgcdicdfddnkalmdm` | Cookie-Editor | `normal_installed` | `ExtensionSettings` (:104) |
| `hoombieeljmmljlkjmnheibnpciblicm` | Language Reactor | `normal_installed` | `ExtensionSettings` (:112) |
| `efaidnbmnnnibpcajpcglclefindmkaj` | Adobe Acrobat | `normal_installed` | `ExtensionSettings` (:120) |
| `lmjnegcaeklhafolokijcfjliaokphfk` | Video DownloadHelper | `normal_installed` | `ExtensionSettings` (:128) |
| `ckkdlimhmcjmikdlpkmbgfkaikojcbjk` | Markdown Viewer | `normal_installed` | `ExtensionSettings` (:136) |
| `efknglbfhoddmmfabeihlemgekhhnabb` | JSON Viewer | `normal_installed` | `ExtensionSettings` (:144) |
| `*` | Ad-hoc user extensions | `allowed` | `ExtensionSettings` (:74-78) |

#### Firefox managed extensions

| Extension ID | Name | Current Policy Mode | Current Delivery Surface (`firefox-policies.json` line) |
|---|---|---|---|
| `uBlock0@raymondhill.net` | uBlock Origin | `normal_installed` | `ExtensionSettings` (:29) |
| `keepassxc-browser@keepassxc.org` | KeePassXC-Browser | `normal_installed` | `ExtensionSettings` (:33) |
| `{c3c10168-4186-445c-9c5b-63f12b8e2c87}` | Cookie-Editor | `normal_installed` | `ExtensionSettings` (:37) |
| `markdown-viewer@outofindex.com` | Markdown Viewer | `normal_installed` | `ExtensionSettings` (:41) |
| `clear-new-tab@loftyshaky` | Clear New Tab | `normal_installed` | `ExtensionSettings` (:45) |
| `{b9db16a4-6edc-47ec-a1f4-b86292ed211d}` | Video DownloadHelper | `normal_installed` | `ExtensionSettings` (:49) |
| `*` | Ad-hoc user extensions | `allowed` | `ExtensionSettings` (:26-28) |

These historical Firefox rows used in-bundle `distribution/policies.json`
(section 1.12), vulnerable to bundle replacement. BUX3B2A subsequently passed
live proof of effect for `macos-preferences` delivery (roadmap 1.10). BUX3B2B
implements extension durability classes on Browser UX `main`, but remains
unselected and unactivated; the historical tables above are preserved unchanged.

#### Rationale revision and BUX2/BUX3 transition

1. **Revision of historical rationale:** `config/browser/README.md:16-19` historically
   stated that extensions were installed `normal_installed` so "they can be disabled
   by the user". In practice, browser updates and profile recreation silently disabled
   managed extensions. The new durability doctrine (roadmap 1.9) supersedes this:
   workstation baseline extensions are expected to remain enabled by default across
   browser updates.
2. **BUX2 generic seam:** The BUX2 generic seam options (`attrsOf anything`) accept
   `ExtensionSettings` and `ExtensionInstallForcelist` dictionaries directly, preserving
   current behavior without requiring class-aware option types in core.
3. **BUX3 classification & leaf guard:** BUX3B2B implements `required` and
   `default-on` classes on Browser UX `main`: both render `force_installed`, while
   a recorded browser-qualified `default-on-opt-out` renders an eligible extension
   `allowed`. The BUX3 gate still requires mechanical proof for the composite
   `ExtensionSettings` map under the seam leaf guard, plus selection and live
   canary of the corrected candidate.

---

## 2. Destination module contract

```text
repository:             browser-ux_dinas
checkout:               $HOME/.local/nix-darwin/browser-ux_dinas
composition registry:   browser-ux
source/input key:       browser-ux_dinas
nixpkgs follows core:   YES
packageSetIdentity:     exported
release channel:        Model B (main = integrated dev, deploy = reviewed release ref)
```

### 2.1 Flake outputs

The destination flake must export:
- `darwinModules.default`: The primary Nix-Darwin module providing `config."browser-ux".configuration` options
  and contributing to `programs.browserBaseline.*`.
- `applicationClaims`: `[ ]` from `BUX1`, required because the composition
  aggregates claims from every registered input and throws when the attribute is
  absent (`composition_dinas/flake.nix:158-170`). Exports
  `bundle:com.operator.brave-youtube` from `BUX4`.
- `packageSetIdentity`: Declared package set identity following core.
- `configAllowlist`: Allows `browser-ux.schema`, `browser-ux.keepassxc`, and
  `browser-ux.extensions`. These include the toggle frozen at BUX1 as
  `keepassxc.enable` and the BUX3B2B `extensions.default-on-opt-out` key, consumed through the registry `copy-default`
  bootstrap (`config/default/browser-ux.toml` -> `config/browser-ux.toml`).
  There is exactly one live toggle file: the operator's machine-local
  `$HOME/.local/nix-darwin/config/browser-ux.toml`, the registry-relative
  `copy-default` target, initialized from the destination checkout's
  `config/default/browser-ux.toml`. It is not the operator control TOML
  `$HOME/.local/nix-darwin/config/nix-darwin.toml`, which holds the source catalog
  and snapshot pins. The current shipped initializer on Browser UX `main` is:
  ```toml
  [browser-ux]
  schema = 1

  [browser-ux.keepassxc]
  enable = true

  [browser-ux.extensions]
  default-on-opt-out = []
  ```
  The public Nix option `config."browser-ux".configuration.keepassxc.enable`
  likewise defaults to true. Initializer changes do not overwrite existing
  machine-local configuration, including explicit operator opt-outs.

  At BUX1 closeout the original machine-local file contained the neutral state:
  ```toml
  [browser-ux]
  schema = 1

  [browser-ux.keepassxc]
  enable = false
  ```
  This `enable = false` setting represents the correct historical dormant BUX1
  state. The operator later set `enable = true` in that same file for BUX3 parity,
  ensuring NativeMessagingHosts manifests were rendered and compared. BUX3 later
  changed the product default to true; it does not change this bootstrap history.

  Current bridge and extension behavior for both Chrome and Firefox:

  | Configuration | Native bridge | KeePassXC-Browser extension |
  |---|---|---|
  | Default / `keepassxc.enable = true` | Present | `required`, managed/enabled (`force_installed`) |
  | Explicit `keepassxc.enable = false`, no extension opt-out | Absent | `default-on`, managed/enabled (`force_installed`) |
  | Explicit false plus browser-qualified `default-on-opt-out` | Absent | `allowed` for that browser only |

  Explicit false opts out of the bridge, not extension presence. Extension
  opt-out is rejected while the bridge is enabled; with the bridge disabled,
  `allowed` permits user control and does not require extension removal.
- `checks.<system>`: Comprehensive standalone test suite across unit, int, bash, python,
  and nix lanes.
- `releaseCandidate`: Destination-local metadata, schema
  `browser-ux-release-candidate-v1`, modeled on
  `developer-ux-release-candidate-v1`. It declares
  `intendedChannel = "refs/heads/deploy"`, the `hostGeneration` partition, and
  `operatorOnlyOperations` with `authorizedByRecord = false`. The metadata is
  self-asserted, tested only by destination checks, and consumed by neither core
  nor `composition_dinas`. Enforcement comes from a destination evaluated check
  proving that no activation fragment invokes an operator-only command.
- No overlay unless destination-owned packages require one. No top-level Darwin
  configuration, and no hidden composition or private dependency.

### 2.2 Release channel choice (Model B)

- **Selection:** Model B (`main` = integrated development, `deploy` = reviewed release ref).
- **Rationale:** The domain contains privileged activation writers, root-owned policy
  files, user-home app bundle generators, and sensitive operator-only workflows.
  Deployments must be gated on reviewed release refs. This matches current DINAS
  convention: `developer-ux_dinas` (similar writer/operator-only profile) is
  registered with `releaseRef = "refs/heads/deploy"`, while simpler domains use
  `refs/heads/main`.
- **Enforcing contracts (all must agree; any mismatch is invalid):**
  1. Composition registry entry `composition_dinas/registry.json` (schema
     `nix-darwin-composition-registry-v2`) with `"releaseRef": "refs/heads/deploy"`.
     `composition_dinas/lib/promotion.nix` derives
     `releaseChannels.refs.browser-ux_dinas` from it (policy
     `explicit-head-refs-v1`; validated by
     `libexec/nix_darwin_local/promotion_contract.py`).
  2. Composition flake input `browser-ux_dinas` tracking `ref=deploy`. A mismatch
     between the promotion contract and the input is BLOCKED per
     `src/test/int/python/nix_darwin_local_promotion.int.test.py:472-505`.
  3. Operator source catalog: `[sources.browser-ux_dinas].release_ref = "refs/heads/deploy"`.
- `deploy` advances only by a reviewed fast-forward from `main`.

---

## 3. Composition registration

```text
registry entry:             name = browser-ux, input = browser-ux_dinas
canonical repo URL:         git+ssh://git@github.com/Knowledge-Forge-AI/browser-ux_dinas.git
releaseRef:                 refs/heads/deploy
deterministic checkout:     browser-ux_dinas (registry-relative; resolves to
                            $HOME/.local/nix-darwin/browser-ux_dinas)
configuration initializer:  copy-default: config/default/browser-ux.toml -> config/browser-ux.toml
application claims wiring:  automatic; composition aggregates every registered
                            input's applicationClaims
overlay position:           none (packages contributed via modules / namespace)
allowlist keys added:       none
```

### Distinction between owner name and source key

- **Registry owner name (`browser-ux`):** The semantic domain identifier used in
  composition options (`config.dinas.commandNamespaces.browser-ux`).
- **Flake input / source key (`browser-ux_dinas`):** The exact Git repository and
  flake input key following DINAS naming conventions (`<domain>_dinas`).

---

## 4. Application claims and atomic transition matrix

### 4.1 Claim definitions

| Application | `id` | `artifact` | `provider` | `package` | `surface` | Declaring location |
|---|---|---|---|---|---|---|
| Brave YouTube | `bundle:com.operator.brave-youtube` | `Brave YouTube.app` | `nix` | `brave` | `system.activationScripts` | `modules/brave-youtube.nix` |

The destination claim reproduces the current core `generated` record
(`lib/core-application-inventory.nix:51-55,92`) field-for-field: `artifact` is
required by ADR 0002 section 3.2.1. Only the owner changes, plus the temporary
`handoff` during overlap.

### 4.2 Claim handoff transition matrix

The application claims validator (`lib/application-claims.nix`) enforces:
1. Every GUI application must have exactly one claim.
2. An overlap between two modules is permitted **only** when the destination claim
   declares a valid handoff from the source module (`handoff = { from = "core"; adr = "..."; }`).
3. Dual claims without handoff are **REJECTED** (`application claim collision`).
4. A handoff without a corresponding source claim is **REJECTED** (`singleton-handoff`).
5. Absence of a claim is **REJECTED** (`unclaimed application`).

```text
+-------------------+--------------------+------------------------+---------------------------------------+
| Milestone / State | Core Claim         | Destination Claim      | Evaluation Result                     |
+-------------------+--------------------+------------------------+---------------------------------------+
| Baseline (BUX0-3) | Present            | Absent                 | VALID (Core owns claim)               |
| Overlap (BUX4-5)  | Present            | Present (with handoff) | VALID (Authorized handoff overlap)    |
| REJECT A          | Present            | Present (no handoff)   | REJECTED (Collision)                  |
| REJECT B          | Absent (half-cut)  | Present (with handoff) | REJECTED (Singleton handoff)          |
| REJECT C          | Absent (half-cut)  | Absent                 | REJECTED (Unclaimed application)      |
| Post-Cut (BUX6)   | Absent             | Present (no handoff)   | VALID (Destination owns claim)        |
+-------------------+--------------------+------------------------+---------------------------------------+
```

### 4.3 Atomic cut mechanism

Because promotion campaigns advance one input at a time (`promotion_campaign.py:263-328`),
an intermediate lock state that updates only core or only `browser-ux_dinas` would
produce `REJECT B` or `REJECT A`.

- **Mechanism:** Following the entertainment and developer-ux precedent
  (`docs/architecture/domain-migrations/entertainment-nd-inventory.md:398-403`;
  ADR 0004 section 5 and 5A), only the selected composition revision that
  consumes **both** qualified new halves is `CUT`.
- **Destination cut candidate:** one revision that removes the handoff,
  re-enables the dormant Brave YouTube and video-session activation imports,
  registers `dinas.commandNamespaces.browser-ux`, and drops the
  `legacyPolicyDefaults` assignment. It lands on destination `main` and is then
  reviewed onto `deploy`.
- **Core cut candidate:** core `main`, carrying the section 6 checklist.
- **Gates (clean scratch composition):** new/new is VALID with exactly one
  destination-owned writer per migrating side effect and zero `dinas-reconcile`
  collisions. Old-core/new-destination is REJECTED (`duplicate-claim`).
  New-core/overlap-destination is REJECTED (`singleton-handoff`). A
  handoff-free destination with dormant writers is not a qualified candidate
  (zero-writer gate).
- **Selection:** Maintained tooling produces one composition candidate advancing
  exactly `core` and `browser-ux_dinas`, qualified by `tools/qualify-promotion`
  in `exact-candidate` mode. `nix-update lock` accepts a repeated `--input`.
  `flake.lock` is never hand-edited, and no single-input campaign stage may
  select one half. If campaign tooling cannot carry the two-input candidate, that
  is a tooling blocker, not permission for a manual lock.

---

## 5. Parity evidence and verification

Parity is proved while core still provides the footprint.

### 5.1 Parity targets

The Firefox in-bundle byte-identity target below is pre-modernization BUX2/BUX3
parity history. Current delivery uses the BUX3B2A-proved `macos-preferences` path;
retain the original target as evidence of parity before delivery modernization.

1. **Chrome Managed Preferences:**
   - `BUX2`: Semantic AST equality (converting XML/binary plist to normalized sorted
     JSON via `plutil -convert json -r -o -` and comparing) between static core file
     and rendered seam output.
   - `BUX3`: Byte identity between seam output in core and seam output with
     `browser-ux_dinas` contributions.
2. **Firefox Policies:**
   - Byte identity of `/Applications/Firefox.app/Contents/Resources/distribution/policies.json`.
3. **KeePassXC NativeMessagingHosts:**
   - Byte identity of Chrome and Firefox manifest JSON files because this host
     keeps `keepassxc.enable = true`. An explicitly opted-out host skips manifest
     byte comparisons and instead verifies both manifests are absent.
4. **Brave YouTube App Bundle:**
   - Exact identity of bundle structure, Info.plist keys, icon, and launcher script.
5. **Video-Session Sudoers:**
   - Byte identity of `/private/etc/sudoers.d/video_session_pmset`.

### 5.2 Expected differences

| Difference | Why it is expected |
|---|---|
| Nix store hash prefixes in scripts/wrappers | Derivations built in destination have different input hashes |
| Package provenance paths | Sourced from `browser-ux_dinas` checkout rather than core |

### 5.3 Durability gates (BUX3 and BUX5)

These gate definitions preserve the parity-before-modernization sequence from
section 5.1. BUX3B2A has passed live Firefox delivery proof of effect. Extension
durability classes and the KeePassXC bridge default-on correction exist on
Browser UX `main`; the corrected BUX3B2B candidate remains unselected and
unactivated, awaiting reviewed deploy advancement, composition promotion,
snapshot adoption, and live canary before BUX4 selection. BUX3 is not closed.

1. **BUX3 Extension Durability Gate:**
   - Managed extension roster classified into `required`, `default-on`, and `allowed` (roadmap 1.9).
   - Mechanical assertions verifying expected extension IDs and policy modes.
   - Durability asserted: required extensions remain installed and enabled across browser updates and binary replacement; ad-hoc extensions are untouched.
   - Implemented on Browser UX `main`: `force_installed` plus recorded browser-qualified opt-outs rendering `allowed`, without heuristic profile mutation; live BUX3B2B qualification remains pending.
   - Parity of existing browser policy proved before delivery modernization.
2. **BUX3 Firefox Enterprise Policy Delivery Durability Gate:**
   - BUX3B2A selected `macos-preferences` (preference domain `org.mozilla.firefox`) and passed live proof of effect; this is accepted prior evidence, not a new observation in this documentation phase.
   - Proof of *effect* required (e.g. policy verified in `about:policies`), not merely file presence.
   - If equivalent, prefer bundle-external delivery; if not, retain bundle writer with an explicit post-update reconciler (e.g. launchd agent/daemon in core) that restores `policies.json` without requiring a Darwin rebuild.
   - Any delivery path or post-update reconciler remains within the core generic seam or an explicitly declared single writer.
3. **BUX5 Clear New Tab Durable Authority Gate:**
   - Browser UX-owned durable representation outside the Nix store and outside browser profiles sufficient to reconstruct collection membership, ordering, settings, and stable image mapping.
   - The migration cannot be declared complete if the only reconstructible CNT state is the browser's live IndexedDB/profile state.
   - Strict decoupling: normal system activation NEVER clears, rebuilds, or imports CNT.
   - Recovery preserves displaced live state before replacement, is idempotent, and requires missing-state proof or explicit operator authorization.
   - Brave YouTube sync (`clear-new-tab-sync`) consumes canonical Browser UX durable state rather than the live Chrome profile.
   - Native backup/export semantics preferred over direct browser DB synthesis; Firefox and Chromium formats remain distinct.

---

## 6. Core removal checklist (`BUX6`)

- [ ] Superseded by R2B: retain `pkgs.brave` and `modules/brave-youtube.nix`; do not execute the earlier removal plan
- [ ] Remove `modules/video-session.nix` import from `flake.nix`
- [ ] Superseded by R2B: retain `modules/brave-youtube.nix` and `modules/brave-youtube-app-writer.sh`
- [ ] Delete `modules/video-session.nix` and `config/power/video-session-pmset.sudoers`
- [ ] Delete `config/brave-youtube/launcher.sh.template` and `theme-manifest.json`
- [ ] Delete `config/browser/chrome-clear-new-tab-external-extension.json`, `firefox-policies.json`, `keepassxc-*-native-messaging.json`
- [ ] Prune `libexec/brave-youtube/`, `libexec/clear-new-tab/`, `libexec/shared-images/` from core
- [ ] Prune 10 domain commands from `packages/dinas-commands/default.nix` (allowedCommands: 34 -> 24)
- [ ] Prune 10 domain command executables from `bin/`
- [ ] Delete 42 domain rows from `src/test/coverage-manifest.tsv`; retain 2 `modules/browsers.nix` rows
- [ ] Superseded by R2B: retain `com.operator.brave-youtube` in `lib/core-application-inventory.nix`
- [ ] Add neutral AppleScript fixture in core (`libexec/core/sample-notification.applescript`)
- [ ] Split 15+ mixed test files; relocate domain-specific unit tests
- [ ] Delete `config/browser/chrome-policy.plist` and remove the `programs.browserBaseline.legacyPolicyDefaults` option
- [ ] Remove `shared-images-workflow` entry from `config/codex-tooling/codex-tooling-desired-state.json`
- [ ] Delete `docs/ops/skills/shared-images-workflow/` and `docs/contrib/skills/browser-hardening-policy/` from core
- [ ] Remove tracked links `.claude/skills/shared-images-workflow`, `.claude/skills/browser-hardening-policy`, and `.agents/skills/nix-darwin/ops` (unless another ops skill exists)
- [ ] Verify core evaluates and builds with `browser-ux_dinas` checkout physically absent

---

## 7. Integration

- [x] BUX1 destination bootstrap and dormant composition registration complete (`browser-ux_dinas` revision `fd800c52d149867efa6bf7052c350ab505b6fd78`, public `c5d3f635dec5638f9f6ac291628f253bec6f1a8c`, private `c48932f65c6fc95b0be3ec8a5673bfb572667989`)
- [x] BUX1 operator source catalog (`refs/heads/deploy`) and neutral machine-local `config/browser-ux.toml` (`schema = 1`, `keepassxc.enable = false`) configured
- [x] BUX1 adopted 10-source `[snapshot.sources]` pins complete
- [x] BUX1 deployment `--plan-only` review proved `+0 / -0` closure movement targeting live generation 208; no host activation required
- [ ] Command namespace `browser-ux` registered only in the destination cut candidate; `dinas-reconcile --check` reports zero collisions
- [ ] Exactly one destination-owned writer each for the Brave YouTube bundle and video-session sudoers in the new/new pair
- [ ] One selected composition revision consumes both qualified cut halves (no hand-edited lock, no single-half selection)
- [ ] Adopted snapshot pins select the new pair; `--source-revision` is not a permanent substitute
- [ ] `nix-update deploy --plan-only` reviewed before attended `nix-update`
- [ ] Package set identity verified across composition layers
- [ ] Application claims validated with zero errors
- [ ] Core builds and passes all checks with destination physically absent

---

## 8. Activation requirement and residual risk

```text
activation required:    YES
justification:          Privileged root writers (sudoers, /Library policy files) and user-home app bundle generation for later cut and activation milestones. BUX1 required no host activation (+0 / -0 closure movement against live generation 208).
authorization:          Not yet requested (BUX8); BUX1 required no host activation
```

### Residual risks and mitigations

| Risk | Why build parity cannot cover it | Mitigation / Acceptance |
|---|---|---|
| Firefox policy gap after out-of-band cask upgrade | Bundle-mode policy is lost when Homebrew replaces `Firefox.app` | BUX3B2A passed live proof of effect for bundle-external `macos-preferences`. Continue policy-effectiveness canaries; bundle-mode fallback still requires explicit post-update reconciliation without Darwin rebuild. |
| Dangling NativeMessagingHosts | Store paths referenced by symlinks can be garbage collected if unreferenced | Seam actively unlinks uncontributed manifests on rollback or deactivation. |
| CNT browser authority | `cnt-wallpaper-gallery` drives the core-owned Google Chrome executable, not a Playwright-downloaded browser; today it finds Chrome by an ordered mutable-path search | Destination passes an explicit Chrome path/override; the cross-owner dependency on core Chrome is recorded, not hidden. Operator-only workflow; not exercised by activation. |
| Managed extension update disablement | Browser updates or profile resets can silently disable managed extensions | BUX3B2B implements `required`, `default-on`, and recorded opt-outs on Browser UX `main`. Corrected candidate selection and live canary remain pending; BUX3B2A delivery proof is not BUX3B2B live durability evidence. |
| Clear New Tab collection state loss or reset | Browser update, extension reinstall, or profile reset wipes IndexedDB collection | BUX5 establishes a Browser UX-owned durable representation outside browser profiles and outside the Nix store sufficient to reconstruct collection membership, ordering, settings, and stable image mapping. |
| Destructive CNT recovery accidentally coupled to host activation | Automated activation logic could invoke destructive clear or import commands, clobbering live operator state | Strict architectural decoupling: system activation NEVER clears, rebuilds, or imports CNT. All recovery preserves displaced live state and requires missing-state proof or explicit operator authorization. |

---

## 9. History and docs disposition

- `docs/adr/2026/08/0002-domain-ownership-application-claims-and-migration-sequencing.md`: Retained and amended in core.
- `docs/architecture/domain-migrations/browser-ux_dinas-inventory.md`: Retained in core.
- `docs/architecture/domain-migrations/browser-ux_dinas-roadmap.md`: Retained in core.
- `docs/ops/skills/shared-images-workflow/SKILL.md`: Moves to `browser-ux_dinas` (sanitized).
- `docs/contrib/skills/browser-hardening-policy/**`: Moves to `browser-ux_dinas`.

---

## 10. Evidence

Evidence will be recorded under `docs/architecture/domain-migrations/evidence/browser-ux_dinas/`
during milestones `BUX3` through `BUX8`.

---

## 11. Authority classes

| Authority Class | Operations Included | Execution Trigger | Authorization Boundary |
|---|---|---|---|
| **Ordinary Host Generation** | Chrome/Firefox policy rendering (core seam), NativeMessagingHosts symlinks, Brave YouTube app generation, video-session sudoers, `browser-ux` command namespace links. System activation NEVER clears, rebuilds, or imports Clear New Tab state; all recovery and modification operations are operator-authorized only. | Attended `nix-update` / `nix-update deploy` | Ordinary source promotion, snapshot adoption, and reviewed deployment plan |
| **Read-only operator diagnostics** | `brave-youtube-session status`, `clear-new-tab-sync status`, `shared-images-db-audit` (reads the archive DB; no write path in `libexec/shared-images/db_audit.py`) | Explicit operator invocation; usable as deployment canaries | No mutation authority required; still never run by activation |
| **Operator-Only Workflows** | Clear New Tab profile mutation (`clear-new-tab-sync brave-youtube` copies Chrome `Default` profile extension, IndexedDB, and Local Extension Settings state into the Brave YouTube user-data directory), Playwright CNT import (`cnt-wallpaper-gallery`), Shared Images DB/file mutation (`shared-images-promote-original`, `shared-images-rehash`, `shared-images-refresh-artist-links`, `shared-images-workflow-layout`), rclone push/pull (`shared-images-rclone-sync`), destructive profile cleanup, visual/content triage, project-local skill link installation | Explicit operator CLI invocation only | Separate operator authority; ordinary promotion or activation never authorizes them. `authorizedByRecord = false` in `releaseCandidate` records this but is self-asserted metadata; the enforcing check is that no activation fragment invokes these commands |

A `releaseCandidate` classification **is** warranted (section 2.1), matching the
`developer-ux_dinas` precedent for domains with operator-only workflows.

---

## 12. Explicit exclusions

The `browser-ux_dinas` migration explicitly does **NOT**:
1. Move Chrome binary, cask, or application claim from core;
2. Move Firefox binary, cask, or application claim from core;
3. Move KeePassXC application ownership or depend on private security modules;
4. Transfer generic `rclone` package ownership to `browser-ux_dinas`;
5. Make browser activation depend on storage module activation;
6. Move office web-app wrappers into Browser UX;
7. Force synthetic Safari policy symmetry;
8. Add third-party AI extensions for symmetry;
9. Place Shared Images image files or database into the Nix store;
10. Execute destructive CNT or Shared Images actions during activation;
11. Redesign or rename `Brave YouTube.app` during ownership migration;
12. Create, register, or activate destination checkout during the BUX0 architecture
    phase (BUX1 has since created it and registered it dormantly; activation remains
    gated to BUX8).
