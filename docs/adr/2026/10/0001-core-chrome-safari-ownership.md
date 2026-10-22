# Core ownership of development Chrome and hardened Safari

Status: amended after the dispatcher-owned post-work review; acceptance and
deployment held. Native composed-graph qualification is blocked. No selection,
activation, installed-policy or extension-effectiveness acceptance is implied.

## Decision and scope

Core defines and writes normal Google Chrome policy, retains `pkgs.google-chrome`,
and owns its required uBlock Origin Lite record. Browser UX owns Vivaldi/Floorp
features and contributes their data to core's existing root writer. It contributes
no Chrome/Safari policy or extension requirements, including through a family
projection. Core's default ownership guard rejects these definitions and duplicate
writers; generic contribution fixtures explicitly opt out. The current host also
asserts ownership cannot be disabled. This shared baseline covers both configured
hosts, example-primary and example-workstation, as did the retained Chrome/Firefox
baseline. Both gain stock Brave/LibreWolf. Public composition's
`mkDarwinConfigurationWith` calls `core.lib.mkDarwinConfiguration`, including
`mkConfiguration` and its ownership assertion. A direct generic module import
retains the explicit opt-out seam for other consumers.

The duplicate activation-writer scan is bounded to literal activation fragments
and recognized verbs/domains; it is a diagnostic, not an exhaustive ownership
proof. Aggregate provenance comes from the activation option declaration rather
than `modulesPath`, and aggregate text is never forced. Inactive `mkIf` contents
are skipped. Computed commands, launchd and `environment.etc` still require
composed-source inspection. Native composed-graph evaluation remains a hard gate.

Core owns Safari privacy intent, native DuckDuckGo normal/private search, and the
existing purchased Wipr 2 app's five-extension qualification contract. No supported
declarative API for these user settings was established in this phase. Core emits
no undocumented Safari preferences or fake extension/TCC approvals. An attended
Apple UI walkthrough and independent readback implement that boundary. Existing
stronger protections, Keychain, approvals, bookmarks, history and profiles persist.

Core installs stock Nix Brave, sharing `pkgs.brave` with the existing Brave-YouTube
launcher, and Homebrew `librewolf`. The two Brave bundle claims deliberately share
one package as a suite. No policy or extension injection is added for either stock
browser. Safari and Wipr have explicit desired-state ownership, without a second
acquisition declaration. Firefox retirement remains held.

The former R1 snapshot plan and both superseded R2 proposals remain historical
evidence. They confer no selection or activation authority. External owner edits
are exact unapplied handoffs; no live external worktree or source pin is changed.

## Chrome policy semantics and evidence

Installed public app metadata observed Chrome 149.0.7827.201 and Safari 27.0.1.
Wipr 2.41's installed extension metadata names are Blocklist 1, Blocklist 2,
Blocklist 3, Blocklist 4 and Wipr Extra. These observations establish metadata,
not enabled state, effective policies or GUI permission status.

| Candidate key | Value and purpose | Primary reference |
| --- | --- | --- |
| UrlKeyedAnonymizedDataCollectionEnabled | false; disable URL-keyed collection, supported from desktop 69 | [Google policy](https://chromeenterprise.google/policies/#UrlKeyedAnonymizedDataCollectionEnabled), [Chromium template change](https://chromium.googlesource.com/chromium/src/+/ada682e3261848fa33bf63fa5dbac618c45881fa%5E%21/) |
| SearchSuggestEnabled | false; stop address-bar search suggestions, desktop 8 onward | [Google policy](https://chromeenterprise.google/policies/#SearchSuggestEnabled), [Chromium template](https://chromium.googlesource.com/chromium/src/+/801cfe88f8c4e699346ecb62e822739f623345bc/components/policy/resources/policy_templates.json) |
| NetworkPredictionOptions | 2; no speculative DNS, preconnection or prerendering, desktop 38 onward | [Google policy](https://chromeenterprise.google/policies/network-prediction-options/) |
| SafeBrowsingProtectionLevel | 1; standard protection, desktop 83 onward | [Google policy](https://chromeenterprise.google/policies/#SafeBrowsingProtectionLevel), [Chromium template](https://chromium.googlesource.com/chromium/src/+/9adf1d0da0289ca1538d9a71ccc5b6b5118add81/components/policy/resources/policy_templates.json) |
| HttpsOnlyMode | force_balanced_enabled; balanced HTTPS upgrades, value available from 129 | [Google policy](https://chromeenterprise.google/intl/nl_nl/policies/https-only-mode/) |
| ExtensionSettings | only uBOL force_installed from the Chrome Web Store; other extensions allowed | [Google Mac extension procedure](https://support.google.com/chrome/a/answer/9044425?hl=en), [Google extension settings](https://support.google.com/chrome/a/answer/7517624?hl=en) |

The version floor is 129 because of balanced HTTPS mode. Public policy references
establish intended syntax/semantics and macOS availability; they do not establish
standalone installed effectiveness. Each exact key must have readable value,
platform/machine/mandatory provenance and status OK in `chrome://policy`, with no
conflict or ignored/unsupported status. Any contrary installed result holds that
policy for omission/correction and re-review; plist byte equality cannot pass it.

MetricsReportingEnabled is omitted: [Google's macOS policy description](https://chromeenterprise.google/intl/zh_cn/policies/metrics-reporting-enabled/)
requires MDM, MCX or Chrome Enterprise Core enrollment. The UI intent is metrics
and crash reporting disabled, separately attended if change is needed.
PrivacySandboxAdTopicsEnabled, PrivacySandboxSiteEnabledAdsEnabled and
PrivacySandboxAdMeasurementEnabled are omitted because Google [deprecated their
APIs/policies from 144 and scheduled removal at 150](https://support.google.com/chrome/a/answer/10314655).
Existing ad settings are inspected and disabled where present through approved UI
changes, without claiming absent APIs are active enforcement.

uBOL [documents Optimal as its default since 2025.4.13.1188](https://github.com/uBlockOrigin/uBOL-home/wiki/Managed-settings).
Core uses built-in default lists and requires effective Optimal/site permission
readback. No custom list or managed-storage write is invented. Preserve extension
updates and test-required/user extensions. Camera/mic prompts, downloads, CDP,
WebSocket, certificate trust and cookies retain defaults; global third-party cookie
blocking is omitted pending SSO compatibility qualification.

## Safari and stock-browser qualification

Apple documents separate [normal/private native search pickers](https://support.apple.com/en-gb/guide/safari/sfria1042d31/mac),
[cross-site tracking prevention](https://support.apple.com/guide/safari/prevent-cross-site-tracking-sfri40732/mac)
and [Safari privacy settings](https://support.apple.com/guide/safari/change-privacy-settings-sfri35610/mac).
The core intent preserves Prevent Cross-Site Tracking, Hide IP Address from
trackers (or an already stronger choice), fraudulent website warning and other
enabled protections. Search must resolve directly to DuckDuckGo. Existing Google
AI Mode and ChatGPT bookmarks are verified without export of bookmark contents.
The [existing Wipr 2 purchase](https://apps.apple.com/us/app/wipr-2/id1662217862)
is preserved; all five checkboxes and Wipr Extra website permissions require UI
evidence. Metadata or Nix output cannot substitute for that evidence.

[Homebrew's official LibreWolf cask](https://formulae.brew.sh/cask/librewolf) is
`librewolf`. Later readback must prove stock startup and policy absence. Brave and
Brave-YouTube must launch with their respective identities and preserved profile.
There is no new duplicate Brave Homebrew cask.
The [Homebrew JSON API](https://formulae.brew.sh/api/cask/librewolf.json), generated
2026-10-10, reports `deprecated: false` and `disabled: false`. An older cached
GitHub source view contains a macOS disable directive. Source/web cache availability
cannot qualify acquisition on this host; live host cask and app readback are required.
A cask or Gatekeeper failure holds the check; no security bypass or alternate
packaging is authorized here.

## Verification, rollback and held gates

Synthetic evidence checks Nix module ownership, exact policy values, unique targets,
Safari intent, extensions, application claims, mirror plist semantics and fail-closed
host evidence. Rendering fixtures use cached Nix libraries and inert package
writers; they are explicitly distinguished from the locked composed host graph.
The Nix daemon and loopback bind are denied in the production sandbox. Full native
evaluation, browser/MCP smoke, policy status and Safari GUI readback remain held.

The SHA-bound attended kit contains checks/readback only. It first captures Safari
state read-only, separates any needed UI change behind operator approval, and then
captures independent readback. Normal Chrome uses a dedicated disposable profile
but still inherits forced extensions. A separate framework Chromium context must
prove absence of policy/extensions for unfiltered suite tests. No alternate normal
Chrome engine, runtime root or broker dependency is introduced.

Chrome can automatically uninstall an extension removed from its force-install
policy ([Chromium policy definition](https://github.com/chromium/chromium/blob/main/components/policy/resources/templates/policy_definitions/Extensions/ExtensionInstallForcelist.yaml)).
This includes legacy Clear New Tab and potentially previously forced Browser UX
convenience extensions; wildcard `allowed` is not preservation evidence. Capture
the actual forced and installed IDs before policy adoption, qualify extension and
storage preservation, and obtain the separate transition approval. Hold any
deployment that cannot preserve the installed extensions and their storage.
This is now a mechanical source hold: a failed Nix assertion prevents a deployable
configuration, and activation plus both aggregate and Chrome attended helpers exit
78 before their first effect. The hold has no approval option or checklist bypass;
release requires a subsequent reviewed source change. Generic seam consumers that
explicitly opt out retain compatibility; this host's core ownership assertion
prevents that opt-out. Vivaldi/Floorp scoped helpers remain available.

Core still packages `clear-new-tab-sync` and `cnt-wallpaper-gallery` in
`packages/dinas-commands/default.nix`. The former reads normal Chrome's Clear New
Tab extension and profile state as the source for Brave YouTube wallpapers; the
latter loads that extension's wallpaper state. No qualified BUX5 durable-state
replacement was demonstrated. Release must preserve that source and its IndexedDB
or qualify the explicit replacement, in addition to preserving the existing Brave
YouTube profile. A policy-only rollback cannot recover removed extension storage.
Retraction of the byte-matching legacy Chrome KeePassXC NMH can break a retained
KeePassXC-Browser unless per-user messaging works; qualify that before adoption.

The owned writer now invalidates the managed-preference cache in both Chrome lanes.
The core lane checks recommended-policy provenance before writing: an unfamiliar
or symlinked `/Library/Preferences/com.google.Chrome.plist` stops the writer rather
than leaving a silent mixed source or deleting unknown state. Only a byte-matching
legacy recommended plist is eligible for bounded removal and backup. These paths
remain unreachable under the deployment hold and need later host readback.
LibreWolf's host cask availability must be qualified before release; a web API
observation cannot establish whether Homebrew activation would succeed.

Rollback is limited to core's changed Chrome managed plist and byte-matching former
core external/NMH records, backed up together by the existing core attended writer.
The writer does not directly modify profiles, but restoring these records cannot
recover extension storage lost through Chrome's policy reconciliation. No profile
deletion or destructive transition is authorized. Restore any approved Safari
UI change from the before capture through attended UI only; no reset or extension
deletion. Source/snapshot planning follows independent acceptance and must preserve
all unrelated source selections. This source phase does not invoke the writer.

The Browser UX owner handoff updates current Vivaldi helper/test paths and its
source roster after the family rename. Its attended rollout selection and rollback
reject core Chrome/Safari scope before any effect; Firefox/Vivaldi/Floorp delivery
continues through core's writer. Historical archives are retained. Extended owner
checks report 194 passed and one unchanged Gecko `signon.` assertion failure,
reproduced against the live preimage. Native writer provenance remains a held gate.
