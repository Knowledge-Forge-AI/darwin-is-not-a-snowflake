# Core browser roles, R3

Core selects `programs.browserBaseline.stockApplications = true` for
example-primary independently of the advanced R3 transition. This adds the
Homebrew casks `brave-browser`, `librewolf` and `orion`, plus a thin `bin/brave`
CLI adapter targeting `/Applications/Brave Browser.app`. Immutable signed
`pkgs.brave` remains solely the source for the retained Brave YouTube wrapper.
Stock Brave and Brave YouTube carry independent claims with no Brave suite. Core's
unparameterized application claims describe this target; `applicationClaimsFor`
projects each host. The inventory's generic selector defaults to false and its
explicit disabled selection preserves the baseline. example-workstation retains its
previous apps. Direct browser module consumers also default to no acquisition.
Declarations and claims establish intended acquisition, not observed installation.
Explicit `stockApplications = true` applies to module and inventory consumers on
any host; the core constructor owns the target-only selection and asserts it.
See the [stock application runtime repair](../../docs/stock-app-runtime-repair.md)
for the current provider decision. [ADR 0003](../../docs/adr/2026/10/0003-additive-stock-browser-acquisition.md)
preserves the additive selector decision superseding the older two-host proposal;
its stock Brave provider and suite statements are superseded by the runtime repair.

The advanced target-host candidate retains the contract in `roles-r3.json`
through `programs.browserBaseline.rolesR3`. `browserRolesR3Selected = false`
and `corePolicyOwnership = false` preserve both hosts' policy and activation
baseline. Acquisition emits no R3 automation, extension or credential/profile
transition. Legacy selectors still imply their previous acquisition behavior.
R3 remains
mechanically held: a failed assertion, the browser policy activation fragment and all R3 attended writer
helpers stop deployment, and `browser-automation` exits 78 before any effect.
A subsequent reviewed source release is required; there is no option bypass.
The assertion and pre-effect guards apply when either R3 selector is active.
Inactive unrelated deployments carry the unchanged browser baseline without a
browser assertion failure. Direct module consumers default to inactive; explicit
R2B ownership remains rejected. Selection and eventual guard release each require
reviewed source changes and native qualification. The composition amendment and
fresh source evidence are in `delivery/browser-ownership-r3r1/`; original R3
delivery evidence remains immutable.

Brave automation uses the same binary as daily Brave, with fresh temporary data
physically disjoint from daily and Brave YouTube roots. The held automation adapter
seeds supported Chromium credential-saving preferences only in new empty scratch
profiles, disables sync, rejects profile reuse/extension injection, and restricts
CDP to pipe or an ephemeral loopback port. This source adapter has fixture evidence;
actual Brave/MCP compatibility and absence of inherited injected extensions remain
unqualified. DevTools, localhost, downloads and browser security retain support.
The adapter drops Playwright's exact `--no-sandbox` default and keeps Brave's
native sandbox enabled. This argument compatibility has fixture evidence only.

Core owns native Shields and the daily Apple Passwords/search/privacy intent.
Browser UX provides only a per-profile convenience catalog and attended Widevine
opt-in. No global Brave forced-extension policy is emitted. LibreWolf keeps stock
hardening and bundled uBO; Browser UX supplies only Clear New Tab convenience.
Official Apple extension listings establish identity, not compatibility with forks
or Apple helper caller validation. Preserve native credentials until password,
TOTP and passkey proof covers any suppression change.

The `safari` block of `core-ownership.json` is core's current Safari desired state:
- native DuckDuckGo search, and the listed protections;
- Wipr 2 Blocklists 1–4 and Wipr Extra all enabled;
- Wipr Extra additionally allowed in private browsing and set to Allow for Other
  Websites.

The stock-application target exposes it as `system.build.browserBaseline.safariIntent`.
Other hosts and inactive consumers keep `null`. Delivery is attended Safari UI with
effective readback, including permissions. Standalone enforcement needs supervised
device management and is unsupported. No invented Apple preference keys, extension
approval, configuration profile, TCC writer or password-store migration is emitted.
Orion's cask and bundle identity come from the Homebrew API. Its uBO, content-blocker
coordination, Tracking Prevention and each Chrome-family convenience extension need
actual supported UI and compatibility qualification. Do not import passwords into
Orion's separate Keychain. Unsupported native choices stay ATTENDED_OR_UNSUPPORTED.

Chrome retains its legacy forced policy, CNT external manifest and KeePassXC bridge
as continuity inputs. Do not retract them or uninstall Chrome, Clear New Tab or
Vivaldi. Apart from its current Safari block, the retained R2B `core-ownership.json`,
mirror plist and ADR describe the superseded compatibility proposal, not the
target-host R3 policy. Legacy payloads
remain immutable. Core's managed/recommended provenance check and cache invalidation
stay held pending native composed graph/readback; no unknown preferences are removed.

The retained capture policy prompts by default (`DefaultMediaStreamSetting = 3`)
and keeps the matching camera/microphone allowlists for existing meeting and
telehealth workflows. These browser exceptions still require macOS permission;
they do not grant TCC access. The exact origins remain in the continuity plist.
Clear New Tab stays force-installed and its external-extension manifest remains
delivered because Chrome's wallpaper import and Brave YouTube sync depend on its
bundle and stored IndexedDB. Removing forced policy can uninstall the extension
and lose storage; a policy-only rollback cannot restore that storage.

Legacy Firefox remains a Homebrew cask with bundle delivery of
`firefox-policies.json` into `Contents/Resources/distribution/policies.json`.
This preserves its established utility extensions and native-messaging bridge
without Nixpkgs Firefox policy wrapping, which can introduce local builds. This
legacy suite is not applied to LibreWolf or Floorp. Existing native managers and
credential data are retained until separately qualified Apple integration.

The existing Brave YouTube core app writer, artwork, profiles and CNT Chrome
IndexedDB wallpaper source remain unchanged. Browser UX is the destination owner,
but a lighter wrapper and physical cut require a separate nonoverlapping handoff.
The existing YouTube copy retains its immutable signed Nix Brave source. Stock
Brave is acquired separately through the stable Homebrew cask; the YouTube writer
and profile retain their established behavior. R3R4 retains Vivaldi installed, runnable and unchanged as a
visual reference outside the seven primary roles; Floorp remains the preferred
power-user browser. Vivaldi retirement requires explicit operator approval.
Chrome and Firefox retirement remains required and unfinished: preserve their
data and CNT dependencies, qualify replacement automation, then remove each app
through its owning provider without deleting browser data. Acquisition of the
three stock apps does not wait for that retirement or credential qualification.
Mail, Calendar, Feeds, OAuth, credentials, extensions, local data and preferences
remain available. Tor and Mullvad and all unrelated selections remain untouched.

The literal activation-fragment scan is a bounded diagnostic. It rejects known
core browser domains and custom-default writers, but cannot prove every computed
shell, launchd or environment.etc write. Complete native composed-source and
application-claim qualification remains required for both hosts.

See [R3 handoff](../../delivery/browser-ownership-r3/MANAGER-REVIEW.md),
[host qualification plan](../../delivery/browser-ownership-r3/HOST-QUALIFICATION-PLAN.md)
and [R3 ADR](../../docs/adr/2026/10/0002-browser-role-ownership-r3.md).

## R3R3 Proton VPN delivery

The current source-only successor is
[`proton-vpn-r3r3.nix`](proton-vpn-r3r3.nix), with the core-owned
[`attended installer procedure`](proton-vpn-install.md) and
[`R3R3 handoff`](../../delivery/browser-ownership-r3r3/README.md).
It merges per-ID core security and Browser UX convenience inputs into one
attended plan; it writes no Proton policy or profile data. Installation,
enabled state and tunnel protection remain unobserved. The R3R2 catalog and
its same-browser hold are retained historical evidence, superseded by R3R3's
per-role, per-ID ownership contract. R3 remains disabled and mechanically held.

The Vivaldi reference decision and cumulative owner handoff are recorded in
[R3R4 delivery](../../delivery/browser-ownership-r3r4/README.md). Historical R3R3
source and native receipts apply only to their original bytes.
