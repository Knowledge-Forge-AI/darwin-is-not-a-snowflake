# `browser-ux_dinas` Migration Roadmap

Normative decisions:
[ADR 0001](../../adr/2026/08/0001-nix-darwin-core-subflake-extension-contract-and-owner-driven-migration.md)
and amended
[ADR 0002](../../adr/2026/08/0002-domain-ownership-application-claims-and-migration-sequencing.md).
Choreography precedent:
[ADR 0004](../../adr/2026/08/0004-developer-ux-nd-migration.md) section 5 and
amendment 5A (single-writer overlap activation strategy).
Migration inventory:
[browser-ux_dinas-inventory.md](browser-ux_dinas-inventory.md).
Actionable protocol:
[subflake migration guide](../subflake-migration-guide.md) and
[domain change deployment](../../ops/domain-change-deployment.md).

This document establishes the executable migration plan for extracting the Browser UX
domain from `darwin-is-not-a-snowflake` core into the standalone destination
repository `browser-ux_dinas`.

---

## 1. Governance and architectural principles

### 1.1 Core baseline vs destination overlay

- **Core keeps:** Google Chrome and Firefox binary/cask installation authority,
  their application claims, a deliberately small core-only browser baseline for
  agent VMs, frontend development, cross-browser validation, and
  bootstrap/recovery, and a generic declarative policy composition/rendering seam.
- **`browser-ux_dinas` owns:** Chrome managed policy values, Firefox policy values,
  external extension policy, camera/microphone policy and allowlists,
  password-manager/autofill policy, KeePassXC browser-side native messaging,
  Clear New Tab, Brave YouTube, video-session power support, the Shared Images
  workflow, browser-specific activation helpers, and browser UX tests/docs/skills.

### 1.2 Root policy files: mechanically single-writer seam (Option A)

To eliminate conflicting root-level writers, order-dependent writes, or "last
writer wins" behavior during overlap:

1. Core owns a generic option and rendering seam in `modules/browsers.nix`
   (`programs.browserBaseline`).
2. Core's seam is the **sole mechanical writer** of:
   - `/Library/Managed Preferences/com.google.Chrome.plist`
   - `/Library/Application Support/Google/Chrome/External Extensions/<id>.json`
   - `/Library/Google/Chrome/NativeMessagingHosts/org.keepassxc.keepassxc_browser.json`
   - `/Library/Application Support/Mozilla/NativeMessagingHosts/org.keepassxc.keepassxc_browser.json`
   - `/Applications/Firefox.app/Contents/Resources/distribution/policies.json` (bundle mode)
   - `/Library/Preferences/org.mozilla.firefox.plist` (macOS preferences mode; mutually exclusive with bundle mode)
3. `browser-ux_dinas` contributes structured values into `programs.browserBaseline.*`
   and never writes these paths directly. A destination-local evaluated check
   proves `darwinModules.default` contributes zero activation text naming any of
   these paths.
4. **Duplicate and priority rejection is mechanical, not prose.** Seam leaf values
   accept exactly one definition per key. A seam guard rejects any seam definition
   carrying a non-default override priority (`mkForce`, `mkOverride`, `mkDefault`,
   `mkOptionDefault`). BUX2 must ship negative core checks proving that (a) two
   modules defining the same seam key fail evaluation and (b) an `mkForce` or
   `mkOverride` definition on a seam key fails evaluation.
5. **Legacy defaults lifecycle.** Until the cut, core renders its current host
   values from `config/browser/*` behind
   `programs.browserBaseline.legacyPolicyDefaults.enable`, declared with the
   option-declaration `default = true` (not a `mkDefault` definition). A seam
   assertion rejects `legacyPolicyDefaults.enable = true` together with any
   non-empty destination contribution, so legacy and destination values can never
   merge. The destination sets it to `false` from BUX3 through BUX5. At the
   matched cut, the core candidate deletes the option **and** `config/browser/*`,
   and the destination cut candidate deletes its `legacyPolicyDefaults.enable =
   false` assignment in the same candidate revision. After the cut, a core-only
   build renders the empty bare baseline: no Chrome managed plist, no external
   extension JSON, no NativeMessagingHosts manifests, no Firefox `policies.json`.
   Host-specific values never remain in core.

### 1.3 User-home and sudoers writers: activation-dormant overlap (ADR 0004 5A)

The seam in 1.2 covers only root policy files. Brave YouTube
(`$HOME/Applications/Brave YouTube.app` plus its user-data/theme roots and legacy
YouTube app cleanup) and video-session (`/private/etc/sudoers.d/video_session_pmset`
plus the `amphetamine_PowerProtect` cleanup) are separate activation writers.
The binding invariant is:

> At every valid selected migration state, exactly one owner executes each
> migrating Brave YouTube and video-session activation side effect.

| State | Destination state | Selected writer | Disposition |
|---|---|---|---|
| A: standalone destination | full Brave YouTube and video-session modules imported, no handoff, not registered | destination (standalone checks only) | implementation-qualification evidence |
| B: rejected overlap | handoff registered **and** activation modules imported while core still writes | core **and** destination | **INVALID**; never registered or selected |
| C: dormant handoff overlap (BUX4-BUX5) | claim with handoff; module files, tests, and templates present; `darwinModules.default` does **not** import the two activation modules | core alone | selected transient overlap; parity runs here |
| D: matched cut (BUX6) | destination cut candidate removes the handoff **and** re-enables both activation imports in the same candidate revision | destination alone | selected only as the new/new pair |

During State C, focused destination checks import the retained module files
directly and exercise their full intended cut-time behavior, and a destination
evaluated check proves `darwinModules.default` contributes zero Brave YouTube or
video-session activation fragments. The claim validator is not a writer-presence
validator. A malformed destination that removed its handoff but left the writers
dormant would pair with new core without a claim error while supplying zero
writers. It is therefore not a qualified cut candidate. The matched-cut gate must
independently prove exactly one destination-owned writer for each side effect.

### 1.4 Atomic application claim handoff

The Brave YouTube claim keeps the current identity and shape exactly:

```nix
{
  id = "bundle:com.operator.brave-youtube";
  artifact = "Brave YouTube.app";
  provider = "nix";
  package = "brave";
  surface = "system.activationScripts";
  declaration = "modules/brave-youtube.nix";
  # overlap only (BUX4-BUX5):
  handoff = {
    from = "core";
    adr = "docs/adr/2026/08/0002-domain-ownership-application-claims-and-migration-sequencing.md";
  };
}
```

`artifact` is required by ADR 0002 section 3.2.1. `surface` and `declaration`
mirror the current core `generated` inventory record, where `declaration` names
the destination-relative module. The transition matrix is:

| State | Core claim | Destination claim | Result |
|---|---|---|---|
| Baseline (BUX0-BUX3) | present | absent | VALID |
| Overlap (BUX4-BUX5, State C) | present | present with handoff | VALID |
| Reject A: old core + destination cut candidate | present | present, no handoff | REJECT (`duplicate-claim` / claim collision) |
| Reject B: new core + overlap destination | absent | present with handoff | REJECT (`singleton-handoff`) |
| Reject C: no claim | absent | absent | REJECT (unclaimed application) |
| Post-cut (BUX6, State D) | absent | present, no handoff | VALID |

Neither half-cut is independently deployable. The core claim removal and the
destination handoff removal become effective only when **one** selected
composition revision consumes both qualified halves (section BUX7).

### 1.5 Command namespace

- The destination packages all 10 Browser UX commands with the `$out/bin` and
  `$out/libexec` sibling layout. `brave-youtube-session` resolves helpers via
  `$(script_dir)/../libexec/brave-youtube/*`.
- `dinas.commandNamespaces` exports a whole `${package}/${binSubdir}`
  (`modules/dinas/command-namespaces.nix:17-57`) and has no per-command
  suppression. `bin/dinas-reconcile:95-109` reports collisions only as warnings.
- **Decision:** the destination does **not** register
  `dinas.commandNamespaces.browser-ux` in any selected composition before the cut.
  During BUX5 the command package is built and exercised only through destination
  `checks.<system>`. Optional operator trials may use `dinas.devNamespaces`, but
  never in a selected composition. The destination cut candidate adds
  `dinas.commandNamespaces.browser-ux` in the same revision as handoff removal and
  writer re-enablement, and the core cut candidate removes the 10 names and three
  libexec subtrees from `packages/dinas-commands`.
- **Cut gate:** `dinas-reconcile --check` for the new/new pair reports zero
  collision warnings and zero blockers. The core `dinas` namespace contains none
  of the 10 names.

### 1.6 KeePassXC integration enablement

- The public option `config."browser-ux".configuration.keepassxc.enable` defaults
  to `true`. It contributes both Chrome and Firefox NativeMessagingHosts
  manifests to the seam, targeting the stable upstream native-host path. It
  imports no application owner and no private module.
- The registry uses `bootstrap.strategy = "copy-default"` with the destination's
  `config/default/browser-ux.toml` as initializer. Its current defaults are
  `schema = 1`, `[browser-ux.keepassxc] enable = true`, and
  `[browser-ux.extensions] default-on-opt-out = []`.
- The single live toggle file is the operator's machine-local
  `$HOME/.local/nix-darwin/config/browser-ux.toml`, not the operator control
  `nix-darwin.toml`. Updating the shipped initializer does not overwrite an
  existing machine-local opt-out. Explicit `[browser-ux.keepassxc] enable = false`
  opts out of the native bridge only.

| Configuration (Chrome and Firefox) | Native bridge | KeePassXC-Browser extension |
|---|---|---|
| Default / `enable = true` | Present | `required`, managed/enabled (`force_installed`) |
| Explicit `enable = false`, no extension opt-out | Absent | `default-on`, managed/enabled (`force_installed`) |
| Explicit false plus browser-qualified extension opt-out | Absent | `allowed` for that browser only |

Extension opt-outs are recorded in
`config."browser-ux".configuration.extensions."default-on-opt-out"`, corresponding
to `[browser-ux.extensions] default-on-opt-out` in TOML. Opting out of a
KeePassXC extension while the bridge is enabled is rejected. Bridge disabled
therefore does not mean extension absent; `allowed` permits user control rather
than requiring removal.

The historical BUX3 parity prerequisite was the operator's change from BUX1's
neutral false state to `[browser-ux.keepassxc] enable = true`. This host retains
that setting, so missing manifests remain a parity failure here. An explicitly
opted-out host instead expects both manifests to be absent.

### 1.7 Release channel contract (Model B)

- **Channel selection:** Model B. `main` is integrated development; `deploy` is the
  reviewed release ref. This matches the current DINAS precedent for a domain with
  privileged/user-home writers and operator-only workflows: `developer-ux_dinas`
  is registered with `releaseRef = "refs/heads/deploy"` while simpler domains use
  `refs/heads/main`.
- **Enforcing contracts (all three must agree; any mismatch is invalid):**
  1. Composition registry entry (`composition_dinas/registry.json`, schema
     `nix-darwin-composition-registry-v2`):
     `"releaseRef": "refs/heads/deploy"`. `composition_dinas/lib/promotion.nix`
     derives `releaseChannels.refs.browser-ux_dinas` from it (policy
     `explicit-head-refs-v1`). No separate promotion contract file exists.
  2. Composition flake input for `browser-ux_dinas` tracks `ref=deploy`. A
     mismatch between the promotion contract and the input is BLOCKED
     (`src/test/int/python/nix_darwin_local_promotion.int.test.py:472-505`).
  3. Operator source catalog: `[sources.browser-ux_dinas]` with
     `release_ref = "refs/heads/deploy"`.
- **Destination `releaseCandidate`:** the destination exports destination-local
  metadata under schema `browser-ux-release-candidate-v1`, modeled on
  `developer-ux-release-candidate-v1`. It declares
  `intendedChannel = "refs/heads/deploy"`, the `hostGeneration` partition, and
  `operatorOnlyOperations` entries with `authorizedByRecord = false`. This
  metadata is self-asserted and tested only by destination checks. Neither core
  nor `composition_dinas` consumes it, and it is not itself an enforcement
  boundary. The enforcing controls are: (a) a destination evaluated check proving
  that no activation fragment invokes any operator-only command, and (b) the
  operator-only commands running only on explicit operator invocation.
- Promotion of `deploy` is a reviewed fast-forward from `main`. No candidate is
  committed directly to `deploy` without first existing on `main`.

### 1.8 Explicit exclusions

The migration explicitly does **NOT**:
1. Move Chrome binary, package, or application claim from core;
2. Move Firefox binary, cask, or application claim from core;
3. Move KeePassXC application ownership into Browser UX, or depend on a private
   security module;
4. Transfer generic `rclone` ownership to `browser-ux_dinas`;
5. Make browser activation depend on storage module activation;
6. Move office web-app wrappers into Browser UX;
7. Force Safari policy symmetry;
8. Add third-party AI/control extensions for Chrome/Firefox symmetry;
9. Place Shared Images image files or database into the Nix store;
10. Execute destructive CNT or Shared Images actions during activation;
11. Redesign or rename `Brave YouTube.app` or its claim identity during ownership
    migration;
12. Create, register, or activate the destination repository during the BUX0
    architecture phase (BUX1 has since created it and registered it dormantly;
    activation remains gated to BUX8).

### 1.9 Managed extension durability doctrine

Browser UX owns the host-specific Chrome and Firefox extension policy roster.

The migration establishes explicit semantic classes rather than treating all extension rows alike:
- **`required`**: Workstation baseline extension whose enabled and installed state is enforced by policy; browser replacement, profile recreation, or browser updates must not leave it disabled.
- **`default-on`**: Browser UX enforces installed/enabled state with `force_installed` unless the operator records a browser-qualified `default-on-opt-out`, which renders that extension `allowed`. BUX3B2B implements this mechanism on Browser UX `main`; browser UI disables alone are not recorded opt-outs.
- **`allowed`**: User or ad-hoc extension outside Browser UX enablement authority.

**Operator baseline expectation:** Managed workstation extensions are enabled by default and browser updates do not silently disable them.

**Design invariants:**
1. BUX3B2B classifies the managed roster into `required` versus `default-on` on Browser UX `main`. Both render `force_installed` unless a valid `default-on` opt-out renders `allowed`; the semantic distinction is whether opt-out is permitted.
2. Where Chrome or Firefox policy semantics cannot distinguish an update-caused disablement from an intentional operator disable, the design must choose explicitly between:
   - enforcement (`required`), or
   - recorded operator intent via a separate state seam (`default-on`).
3. No heuristic profile mutation during ordinary activation.
4. BUX3 parity and mechanical tests must assert update and replacement durability:
   - Policy survives or reappears after browser replacement;
   - Required extensions are installed and enabled;
   - Expected extension IDs and policy modes are mechanically asserted;
   - Unmanaged and ad-hoc extensions are untouched.
5. Firefox extension policy uses `macos-preferences`, whose live proof of effect passed in BUX3B2A (section 1.10). BUX3B2B extension durability implementation remains unselected and unactivated; delivery proof alone is not live proof of the newer extension candidate.

### 1.10 Firefox policy delivery durability doctrine

The historical BUX2 delivery wrote `/Applications/Firefox.app/Contents/Resources/distribution/policies.json` inside a core-owned Homebrew application bundle. BUX3B2A passed live proof of effect for bundle-external `macos-preferences` delivery, avoiding loss of the policy file on bundle replacement.

**Design invariants:**
1. **Preserve parity-first migration:** BUX2 implements the behavior-preserving seam in `modules/browsers.nix` writing `distribution/policies.json` in-bundle without changing effective delivery mechanics.
2. **BUX3 investigation and gate:** BUX3 establishes a generic core seam (`programs.browserBaseline.firefox.policyDelivery` supporting `bundle` and `macos-preferences`, writing `/Library/Preferences/org.mozilla.firefox.plist`). BUX3B1 implements the core seam; BUX3B2A has passed the `about:policies` proof of effect using `macos-preferences`; BUX3B2B selection remains pending.
3. **Proof of effect:** Evaluation of any bundle-external mechanism requires proof of *effect* (e.g. verified policy enforcement via `about:policies` in Firefox), not merely file or plist presence, since non-MDM managed-preference delivery on macOS must be verified.
4. **Architectural single-writer ownership:** Any new delivery path or post-update reconciler must remain within the core generic seam or an explicitly declared single writer, added to the section 1.2 sole-writer path list and tested by the destination zero-root-path activation check.
5. **Reconciliation fallback:** If a bundle-external mechanism cannot preserve required semantics, retain the in-bundle writer only with an explicit post-update reconciliation mechanism (owned by core, such as a launchd agent or daemon) that repairs policy loss without requiring an unrelated Darwin rebuild.

### 1.11 Clear New Tab durable authority doctrine

Browser profile and extension IndexedDB state must not remain the sole canonical authority for Clear New Tab (CNT) state.

BUX5 must establish a Browser UX-owned durable representation sufficient to reconstruct at least:
- Collection membership;
- Collection ordering;
- Relevant CNT preferences and settings;
- Stable image identities and source mapping.

**Design invariants:**
1. **Durability against update/reinstall:** Chrome or Firefox updates or extension reinstalls must not silently destroy the operator's collection without recoverable authoritative state.
2. **Strict non-activation decoupling:** Normal nix-darwin host activation must **NEVER** clear, rebuild, or import the CNT collection.
3. **Operator-only operations:** Existing clear, import, and rebuild operations (`cnt-wallpaper-gallery clear`, `load`, Playwright import) remain strictly operator-only.
4. **State preservation during recovery:** Recovery must preserve displaced live state before replacement.
5. **Idempotent and guarded recovery:** Recovery must be idempotent and require proof that state is missing/reset or explicit operator authorization.
6. **Brave YouTube is a derived consumer:** Brave YouTube is a derived consumer of canonical Browser UX CNT state, never a second authority (superseding historical notes in `config/browser/README.md`).
7. **Storage boundary:** Shared Images content/database and CNT runtime databases remain outside the Nix store.
8. **Native export/backup preference:** Native CNT backup and export semantics should be preferred over direct browser database synthesis wherever sufficiently reliable.
9. **Engine format distinction:** Firefox and Chromium storage formats remain distinct; never copy Chromium IndexedDB directly into Firefox.
10. **Destructive command isolation:** The current `cnt-wallpaper-gallery clear` capability must remain explicitly destructive and operator-only, and must never be invoked by browser update reconciliation.

### 1.12 Local authority doctrine

In all roadmap phases, governance, promotion, and deployment distinguish:
- **Local DINAS Git/source/snapshot authority** (canonical); versus
- **Remote publication/readback** (distribution and readback only).

Local DINAS Git checkouts, source catalogs (`dinas-local-sources`), and adopted snapshot pins represent local canonical authority. Remote publication to remotes (e.g. GitHub) may be required for distribution or backup, but remote state does not override a reviewed local DINAS source state. If a remote differs unexpectedly during publication, publication stops without automated or forced reset or rebase of local authority.

---

## 2. Milestone execution roadmap

```text
BUX0 architecture refresh (docs-only)
  -> BUX1 destination bootstrap + dormant composition registration
  -> BUX2 core generic browser-baseline seam (sole root writer)
  -> BUX3 browser-policy overlap + parity
  -> BUX4 Brave YouTube + video-session (State C dormant handoff overlap)
  -> BUX5 Clear New Tab + Shared Images + skills + command package (unregistered)
  -> BUX6 matched cut candidates + invalid-half-cut + writer-count gates
  -> BUX7 source publication + promotion campaign selecting the new/new pair
     + snapshot + deploy --plan-only review
  -> BUX8 attended deployment + live canary + +0/-0 READY closeout
```

The frozen doctrine order applies: exact inventory, destination bootstrap,
dormant public composition registration, core-old + destination overlap, exact
parity evidence, invalid-half-cut tests, matched core ownership cut, destination
qualification, source publication, DINAS promotion campaign, composition
publication where required, snapshot prepare/review/apply, `deploy --plan-only`
review, attended deployment, live canary, post-deploy `+0/-0` READY proof.

Each milestone before BUX7 is independently reversible by not selecting it.
No milestone is coupled to office or storage migrations.

---

### BUX0: Architecture refresh (COMPLETE)

- **Objective:** Refresh and freeze the Browser UX architecture against current
  DINAS core without modifying runtime code.
- **Actions:** Amend ADR 0002, and author this roadmap and the inventory.
- **Verification:** `git diff --check`, and a changed-path check proving the diff
  contains documentation paths only.
- **Exit criteria:** Dispatcher review and publication of the documentation. Complete.

---

### BUX1: Destination bootstrap and dormant composition registration (COMPLETE)

- **Objective:** Create `browser-ux_dinas` with `main` and `deploy`, and register it
  dormantly in the public composition.
- **Prerequisites:** BUX0 published.
- **Closed state and accepted facts:**
  - **Destination bootstrap:** `browser-ux_dinas` created and qualified on `main` and `deploy`; accepted selected revision `fd800c52d149867efa6bf7052c350ab505b6fd78`.
  - **Public composition adoption:** `composition_dinas` accepted revision `c5d3f635dec5638f9f6ac291628f253bec6f1a8c`.
  - **Private composition adoption:** `composition-private_dinas` accepted revision `c48932f65c6fc95b0be3ec8a5673bfb572667989`.
  - **Operator source catalog:** Contains `[sources.browser-ux_dinas]` with `checkout = "$HOME/.local/nix-darwin/browser-ux_dinas"` and `release_ref = "refs/heads/deploy"`.
  - **Machine-local Browser UX config** (`$HOME/.local/nix-darwin/config/browser-ux.toml`, section 1.6): Configured with neutral state:
    ```toml
    [browser-ux]
    schema = 1

    [browser-ux.keepassxc]
    enable = false
    ```
    This neutral state is the correct dormant BUX1 state. The destination allowlist key is frozen at BUX1 as `keepassxc.enable`. The operator later set `enable = true` in this same machine-local file for BUX3 parity. BUX3 subsequently changed the product default to true (section 1.6); this does not rewrite the BUX1 bootstrap history.
  - **Adopted snapshot pins:** Adopted `[snapshot.sources]` is complete with 10 configured catalog sources, pinning Browser UX, public composition, and private composition at the accepted revisions above.
  - **Deployment plan review:** A local deployment `--plan-only` review resolved:
    - Target system: exactly the currently live generation-208 system;
    - Closure movement: `+0 / -0`;
    - Deployment effects: one read-only `dinas-reconcile` preflight, no pre-switch transaction, no post-switch reconciliation, no external mutable-state probe.
  - **Host activation intentionally unnecessary:** Because the target system is identical to live generation 208 (`+0 / -0`), **BUX1 requires no host activation**. Attended no-op deployment was not required.
- **Exit criteria:** Standalone qualification, composition evaluation with dormant registration, and zero effective host change verified. Complete.

---

### BUX2: Core generic browser-baseline seam

- **Objective:** Implement `programs.browserBaseline` in core `modules/browsers.nix`
  as the sole root-policy writer while preserving current effective files and behavior.
- **Prerequisites:** BUX1 complete.
- **Scope & invariants:**
  - Strictly behavior-preserving seam: core generic browser-baseline seam, sole root writer, current effective policy unchanged.
  - Do not implement new durability mechanics here; ensure the seam structure (`attrsOf anything` in `managedPreferences` and `policies`) can support structured extension policy without embedding destination-specific extension identities or class-aware option types into core.
  - Core's seam is the sole root writer of Chrome plist, external extension JSON, NativeMessagingHosts manifests, and Firefox `policies.json`.
- **Implementation:** The option tree, renderers, duplicate/priority guard, and
  negative checks from 1.2. Also absent-contribution cleanup: unlink uncontributed
  NativeMessagingHosts symlinks, and remove managed files when their contribution
  is empty. The Firefox cross-owner write keeps its current in-bundle ordering and presence
  semantics (inventory 1.12). The legacy cleanup actions follow inventory 1.12.
- **Parity:** Chrome plist semantic equality via
  `plutil -convert json -r -o - | jq -S .`. Byte identity for the external
  extension JSON, Firefox `policies.json`, and both manifests.
- **Exit criteria:** Core builds standalone. The rendered files match current
  files byte-for-byte / semantically.

---

### BUX3: Browser-policy overlap, durability freeze, and parity

- **Objective:** Move policy values into the destination, contributed through the
  seam, and freeze extension and Firefox delivery durability mechanisms.
- **Prerequisites:** BUX2 in the selected core. The machine-local
  `$HOME/.local/nix-darwin/config/browser-ux.toml` enables KeePassXC integration (`keepassxc.enable = true`, section 1.6).
- **Destination:** `darwinModules.default` contributes the Chrome managed
  preferences, Firefox policies, Clear New Tab external extension, and (through
  `config."browser-ux".configuration.keepassxc.enable`) both manifests. It sets
  `programs.browserBaseline.legacyPolicyDefaults.enable = false`.
- **Registration:** The public composition advances only `browser-ux_dinas`; core
  and every other owner stay fixed.
- **Expanded exit criteria & durability gates:**
  - **Extension roster & classes:** Implemented on Browser UX `main` in BUX3B2B (section 1.9); selection and live canary remain pending.
  - **Mechanical tests:** Mechanical tests assert expected extension IDs and policy modes; required extensions are update-durable and survive browser replacement; unmanaged/ad-hoc extensions are untouched.
  - **Default-on design:** Implemented as `force_installed` plus a recorded browser-qualified `default-on-opt-out` rendering `allowed`, without heuristic profile mutation during ordinary activation.
  - **Option guard resolution:** Explicit resolution for how the seam leaf guard (one definition per key) applies to the single `ExtensionSettings` map when multiple extensions are contributed.
  - **Firefox policy delivery durability gate:** BUX3B2A passed live proof of effect for `macos-preferences` (accepted evidence below). Preserve the section 1.10 fallback if required semantics cannot be maintained.
  - **Parity:** Seam output byte-identical to BUX2 render before any delivery-mechanism modernization. Duplicate/priority guard passes. Destination emits zero root-path activation text.
- **Accepted BUX3B2A live evidence (prior evidence, not a new observation):**
  Live system
  `/nix/store/9k9m0j8p1z0l2pfi98mzixfwj508zlnc-darwin-system-25.11.ebec37a`.
  Firefox policy delivery was `macos-preferences`.
  `/Library/Preferences/org.mozilla.firefox.plist` was present, owned by
  `root:wheel`, mode `0644`; generated and installed bytes matched. Legacy
  Firefox.app `distribution/policies.json` was absent. Firefox reported managed
  status, `about:policies` showed the expected Browser UX policies with no
  surfaced policy errors, Enhanced Tracking Protection was visibly Strict,
  DNS over HTTPS was visibly Off, and all six managed Firefox extensions were
  present and enabled.
- **Current selection status:** Extension durability classes are implemented on
  Browser UX `main`, together with the later KeePassXC bridge default-on
  correction. The corrected destination candidate must still cross reviewed
  `deploy` advancement, composition promotion, snapshot adoption, and live canary
  before BUX3 selection is complete and BUX4 may start. BUX3B2B remains
  unselected and unactivated; the BUX3B2A evidence is not live extension-durability
  proof for BUX3B2B.
- **Reversibility:** Re-selecting the prior destination revision restores core
  legacy defaults.

---

### BUX4: Brave YouTube and video-session (State C)

- **Objective:** Move the Brave YouTube product family and video-session into the
  destination under a claim handoff, with core remaining the sole writer.
- **Prerequisites:** BUX3 selected.
- **Destination:**
  - Add `brave-youtube.nix`, `brave-youtube-app-writer.sh`, `config/brave-youtube/*`,
    `video-session.nix`, and the `video-session-pmset.sudoers` template.
    `darwinModules.default` does **not** import the two activation modules
    (State C).
  - Vendor `user-home-writer-common.sh` byte-identical to core
    (inventory 1.3 decision).
  - Add the claim with handoff (1.4). Destination claim checks are amended in the
    same commit.
  - Add focused checks that import the dormant modules directly. Add an evaluated
    check proving zero Brave YouTube or video-session fragments in
    `darwinModules.default`.
- **Parity (State C):** Canonical baseline vs overlap artifacts cover packages,
  claims, activation text, writer counts (exactly one core writer per side
  effect), and sudoers bytes.
- **Exit criteria:** Overlap composition validates claims. Writer counts are
  unchanged from baseline.

---

### BUX5: Clear New Tab, Shared Images, skills, and command package

- **Objective:** Move the remaining product sources and skills into the
  destination, establish the durable Clear New Tab authority model, and package commands (unregistered).
- **Prerequisites:** BUX4 selected.
- **Destination:**
  - Move the `clear-new-tab-sync`, `cnt-wallpaper-gallery`, and `shared-images-*`
    sources and `libexec/{clear-new-tab,shared-images}`.
  - `cnt-wallpaper-gallery` runs under a destination-owned
    `pkgs.python3.withPackages (ps: [ ps.playwright ])` closure. Its Chrome
    executable authority becomes an explicit override or the core-owned Chrome
    path passed by the destination package. It must not depend on the mutable
    `/run/current-system` search order (inventory 1.8).
  - Shared Images commands use `${pkgs.rclone}/bin/rclone` as the default and
    retain the bounded `RCLONE` fixture override for tests.
  - Skills: the Shared Images skill is sanitized (inventory 1.15). The
    browser-hardening skill moves with its `agents/openai.yaml` display metadata.
  - The command package with all 10 commands is built and tested in `checks`;
    `dinas.commandNamespaces.browser-ux` is not registered (1.5).
- **Expanded exit criteria & Clear New Tab durable authority gate:**
  - **Durable authority representation:** Establish a Browser UX-owned durable representation outside the Nix store and outside browser profiles sufficient to reconstruct collection membership, collection ordering, relevant preferences/settings, and stable image identities/source mapping.
  - **Migration completion requirement:** The migration cannot be declared complete if the only reconstructible CNT state is the browser's live IndexedDB/profile state.
  - **Format & recovery invariants:** Native CNT backup/export semantics preferred over direct browser DB synthesis; Firefox and Chromium formats remain distinct (never copy Chromium IndexedDB into Firefox).
  - **Activation decoupling:** Proof that normal host activation NEVER clears, rebuilds, or imports CNT. All recovery is idempotent, preserves displaced live state before replacement, and requires missing-state proof or explicit operator authorization.
  - **Brave YouTube derived consumer:** Re-point Brave YouTube sync (`clear-new-tab-sync`) to consume canonical Browser UX durable state rather than the live Chrome profile.
  - **Command package & runtimes:** The destination package builds with hermetic runtimes, and the selected composition is unchanged in commands and writers.

---

### BUX6: Matched cut candidates and cut gates

- **Objective:** Prepare and qualify both cut halves. Nothing is selected in this
  milestone.
- **Prerequisites:** BUX5 selected and parity-qualified.
- **Core cut candidate** (core `main` candidate branch):
  1. Remove the Brave YouTube claim from `lib/core-application-inventory.nix`.
  2. Remove the `brave-youtube.nix` and `video-session.nix` imports
     (`flake.nix:254-255`) and delete those modules,
     `brave-youtube-app-writer.sh`, `config/brave-youtube/`, and
     `config/power/video-session-pmset.sudoers`.
  3. Delete `config/browser/*` and the `legacyPolicyDefaults` option (1.2).
  4. Remove the 10 commands from `bin/` and `packages/dinas-commands`
     (34 -> 24), and remove the `libexec/{brave-youtube,clear-new-tab,shared-images}`
     subtrees.
  5. Remove the 42 coverage rows and retain the 2 `modules/browsers.nix` rows.
  6. Add the neutral AppleScript fixture and its test (inventory 1.9).
  7. Split the mixed tests (inventory 1.9).
  8. Remove the skill exposure: the `.claude/skills/{browser-hardening-policy,shared-images-workflow}`
     links, the `.agents/skills/nix-darwin/ops` link, the codex-tooling
     desired-state entry, and both skill directories (inventory 1.11/1.15).
- **Destination cut candidate** (destination `main`, then reviewed onto `deploy`),
  in **one** revision:
  1. Remove the `handoff` block.
  2. Import both activation modules into `darwinModules.default`.
  3. Register `dinas.commandNamespaces.browser-ux`.
  4. Remove the `legacyPolicyDefaults.enable = false` assignment.
- **Gates (clean scratch composition):**
  - New core + new destination: claims VALID; exactly one destination-owned writer
    each for the Brave YouTube bundle and the video-session sudoers; seam sole
    root writer; `dinas-reconcile --check` has zero collisions.
  - Old core + new destination: REJECT (`duplicate-claim`). Also rejected by the
    legacy/contribution seam assertion.
  - New core + overlap destination: REJECT (`singleton-handoff`). Also fails on
    the removed `legacyPolicyDefaults` option.
  - A destination with the handoff removed but writers dormant is not a qualified
    candidate (zero-writer gate).
  - Core evaluates and builds with the destination checkout physically absent.
  - Cut semantic projection: the State C pair (core writes) equals the new/new pair
    (destination writes) for bundle identity, sudoers bytes, policy files, and
    command set.
- **Exit criteria:** Both candidates qualified. The gate evidence is recorded.

---

### BUX7: Publication, promotion, snapshot, and deployment plan

- **Objective:** Select the new/new pair through maintained tooling and produce a
  reviewed deployment plan.
- **Prerequisites:** BUX6 gates pass.
- **Actions:**
  1. Source publication: the core cut to core `main`. The destination cut to
     destination `main`, then a reviewed fast-forward of `deploy`. Publication
     follows the section 1.12 local authority doctrine: the reviewed local DINAS
     source state is authoritative, and an unexpected remote difference stops
     publication rather than triggering a reset or rebase of local authority.
  2. Promotion: one composition candidate that advances **exactly** `core` and
     `browser-ux_dinas` together, qualified by `tools/qualify-promotion`
     (`exact-candidate` mode). The candidate is produced by maintained tooling;
     `nix-update lock` accepts a repeated `--input`. `flake.lock` is never
     hand-edited, and `nix flake update` is never used. No single-input
     campaign stage may select one half alone. If current campaign tooling cannot
     carry a two-input candidate through promotion, that is a BUX7 tooling blocker,
     not a license for a manual lock.
  3. Composition publication where required. The top-level/private composition
     advances once.
  4. Snapshot `prepare`/review/`apply` so that adopted snapshot pins select the
     new pair. `--source-revision` is not a permanent substitute for adopted
     snapshot authority.
  5. `nix-update deploy --plan-only` (or `nix-update --dry-run`). The review
     confirms that only the expected root paths, user-home bundle, and namespace
     links change.
- **Exit criteria:** The plan is reviewed and approved.

---

### BUX8: Attended deployment, live canary, and `+0/-0` closeout

- **Prerequisites:** The BUX7 plan is approved.
- **Execution:**
  1. Attended `nix-update` (or `nix-update deploy`). Never call
     `darwin-rebuild switch` directly.
  2. Root files: the Chrome managed plist matches. The CNT external-extension JSON
     is present. Both KeePassXC manifests resolve because this host keeps
     `keepassxc.enable = true`; an opted-out host skips resolution checks and
     verifies both manifests are absent. Firefox `policies.json` (or verified bundle-external mechanism) is
     present and matches. `video_session_pmset` passes `visudo -cf`.
  3. User-home: `$HOME/Applications/Brave YouTube.app` has the unchanged bundle
     identity.
  4. Expanded read-only canaries:
     - `brave-youtube-session status`, `clear-new-tab-sync status`, and command resolution of all 10 names from the `browser-ux` namespace;
     - Expected managed-extension enablement and policy status;
     - Firefox policy presence and effectiveness (`about:policies`) after final activation;
     - Clear New Tab durable-state fingerprint/backup availability, and proof that activation did not clear, rebuild, or import CNT;
     - Optionally, `shared-images-db-audit` (read-only) when the archive is present.
  5. Post-deploy `+0/-0` READY proof: no drift between the selected and live
     generation.
- **Operator-only workflows are not canaries.** No `clear-new-tab-sync brave-youtube`,
  Playwright import, promotion/rehash/layout, or rclone push/pull runs as part of
  deployment.

---

## 3. Rollback, contingency, and residual risks

### 3.1 Rollback and contingency matrix

| Failure mode | Detection point | Response |
|---|---|---|
| Seam render or guard failure | BUX2/BUX3 checks | Do not select; fix the core seam |
| Claim collision or singleton handoff | BUX6 scratch gates | Expected for half-cuts; never select a half |
| Zero or two writers | BUX4 parity / BUX6 writer gate | Not a qualified candidate; fix the destination revision |
| Command collision warning | BUX6 `dinas-reconcile --check` | Block the cut until zero collisions |
| Firefox bundle replaced by a cask upgrade | Canary / next activation | BUX3B2A passed live proof for bundle-external `macos-preferences`; verify continued policy effectiveness. Bundle-mode rollback retains the reconciliation requirement (section 1.10 / inventory 1.12) |
| Host activation failure | BUX8 | Re-select the previous composition/snapshot and redeploy through `nix-update`; the nix-darwin generation rollback is the recovery path of last resort |

### 3.2 Migration-time residual risks and mitigations

The inventory document ([browser-ux_dinas-inventory.md](browser-ux_dinas-inventory.md#8-activation-requirement-and-residual-risk), section 8) maintains canonical risk accounting. Key durability-related migration-time risks include:

1. **Firefox policy gap after out-of-band cask upgrade:**
   - *Risk:* Homebrew cask upgrades replace `/Applications/Firefox.app` and wipe in-bundle `policies.json` between system activations.
   - *Mitigation:* BUX3B2A passed live proof of effect for bundle-external `macos-preferences`. Continue policy-effectiveness canaries; any bundle-mode fallback still requires explicit post-update reconciliation without a Darwin rebuild.
2. **Managed extension update disablement:**
   - *Risk:* Browser updates or profile resets silently disable managed extensions.
   - *Mitigation:* BUX3B2B implements the section 1.9 classes using `force_installed` and recorded opt-outs on Browser UX `main`. The corrected candidate still requires deploy advancement, promotion, snapshot adoption, and live canary; BUX3B2A Firefox delivery proof does not establish BUX3B2B live durability.
3. **Clear New Tab collection state loss or reset:**
   - *Risk:* Browser update, profile reset, or extension reinstallation wipes the live IndexedDB collection.
   - *Mitigation:* BUX5 establishes a Browser UX-owned durable representation outside browser profiles and outside the Nix store sufficient to reconstruct membership, ordering, settings, and image mapping.
4. **Destructive CNT recovery accidentally coupled to host activation:**
   - *Risk:* Automated activation logic could invoke destructive clear or import commands, clobbering live operator state.
   - *Mitigation:* Architectural invariant strictly decoupling activation from CNT operations. Normal activation NEVER clears, rebuilds, or imports CNT. Recovery preserves displaced live state and requires missing-state proof or explicit operator authorization.
