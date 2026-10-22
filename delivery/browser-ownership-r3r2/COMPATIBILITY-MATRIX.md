# Proton VPN compatibility and ownership

Status: `BUX_R3R2_PROTONVPN_COMPATIBILITY_HELD_RETAINED`.
Final revision: `PENDING_FINAL_INDEPENDENT_REVIEW`.
Availability is intent/contract metadata, with no new runtime capability: stores
already allowed manual installs. No browser
installation, login, connection, profile read/write or active protection is claimed.
All installed browser versions remain unobserved in this task.

| Browser/context | Extension owner | Source availability | Runtime qualification |
| --- | --- | --- | --- |
| Stock Brave daily | Core | Official Chromium ID and update route; per-profile manual add | Pending actual profile readback; preserve Shields and existing extensions |
| Stock Brave development/test/agent | Core | `AUTOMATION_AVAILABILITY_HELD`; same identity is reference metadata only | Launcher creates one fresh empty temporary data root per run and disables extensions; no persistent install target. Separate approved design required; preserve three disjoint roots and CDP contract |
| Brave YouTube | Core retained writer | Same Chromium definition, `WRAPPER_PROJECTION_HELD` | Separate held action; current launcher restricts extensions to theme/CNT |
| LibreWolf | Core | Official Gecko identity and signed AMO update route; per-profile manual add | Preserve bundled uBO, hardening and Apple Passwords; DNS/DoH compatibility pending |
| Floorp | Browser UX | One exact cumulative owner patch adds VPN to the existing held Gecko suite | No policies delivered; existing suite and Apple credential intent retained; attended readback pending |
| Orion | Core | `REQUESTED_COMPATIBILITY_HELD` / `PROXY_API_UNQUALIFIED`; remain absent | No proven tunnel; no installed/protected claim; existing system Proton VPN app remains a separate option |
| Safari, Tor, Mullvad, Vivaldi, legacy Chrome | Outside this extension revision | No addition | Existing source and state retained |

The owner column states the requested installer-owned destination, not achieved
single ownership of each browser's complete extension suite. Retained
`config/browser/roles-r3.json` names Browser UX as convenience owner for
Brave daily, LibreWolf and Orion. The cumulative Browser UX patch also defines
their extension suites. These are split browser-level definition owners even
though Proton itself has no duplicate ID owner. All three are
`HELD_RECONCILIATION_REQUIRED`: installer-owned reconciliation must preserve
the existing suites and storage before adoption. This additive revision retains
the earlier R3 source and exact owner patch; it does not migrate those suites.
The final checker detects the split and reports `singleOwnerQualified: false`.

Chromium ID: `jplgfhpmjnbigmhklmmbgecoobifkmpa`. Update route:
`https://clients2.google.com/service/update2/crx`. The [official Proton download
page](https://protonvpn.com/download-chrome-extension) links to the matching
[Chrome Web Store listing](https://chromewebstore.google.com/detail/proton-vpn-fast-secure/jplgfhpmjnbigmhklmmbgecoobifkmpa).

Gecko ID: `vpn@proton.ch`, AMO add-on number `2786243`. The retained Browser UX
catalog records authenticated AMO metadata matching the signed 1.3.6 XPI manifest
and its SHA256. The producer's unpinned corroboration is Proton's [Firefox manifest
builder](https://raw.githubusercontent.com/ProtonVPN/proton-vpn-browser-extension/main/webpack.config.js),
which writes `browser_specific_settings.gecko.id` with this value. The
[official Firefox download page](https://protonvpn.com/download-firefox-extension)
and [AMO listing](https://addons.mozilla.org/en-US/firefox/addon/proton-vpn-firefox-extension/)
identify the product. Revision readback of the public AMO listing establishes
neither its GUID nor a signed manifest. Fresh AMO API/XPI bytes and an immutable
upstream reference are unavailable in this provider. Identity adoption remains
held until fresh authenticated AMO GUID readback and commit/tag-bound builder
corroboration. Exact inherited evidence and this limitation are recorded in
`IDENTITY-EVIDENCE.json`. The slug is a locator, never the extension ID.
The mutable `latest.xpi` URL is an official update locator, not a pinned package
or identity proof; no XPI is fetched or installed by the candidate. Later attended
LibreWolf readback must observe signed add-on auto-update settings and storage
continuity without changing update preferences or forcing a version downgrade.

Proton describes the extension as a browser-only proxy using HTTPS. Native app
traffic is outside its coverage. Each active browser connection consumes a
connection against the account's plan. Split tunneling can limit which sites
use the proxy, and combining the separate system app with the extension changes
the route again. Proton reports Firefox DNS leak conditions and suggests DoH
configuration; this revision leaves DNS unchanged until attended route and
compatibility checks. Auto-connect is an attended requirement to keep off; the
catalog's false intent is not an enforced preference. No existing setting is
rewritten. See [Proton's browser extension guide](https://protonvpn.com/support/browser-extensions).

Brave YouTube source provenance: `modules/brave-youtube.nix` supplies the stock
Nix Brave app to `modules/brave-youtube-app-writer.sh`. The writer copies the app,
renames the binary to `Brave YouTube.bin`, installs a launcher and changes the
bundle identity. `bin/brave-youtube-session` chooses a dedicated data root and
passes `--disable-extensions-except` and `--load-extension` for theme/CNT.
Stock Brave profile extension inheritance is unsupported by this design.
Proposed later work is a separately approved per-wrapper availability/launch
projection retaining both flags' intended preservation behavior, with auto-connect
off and battery/video checks. Do not clone stock profiles, remove the extension
allowlist, or write extension databases under this revision. Installed wrapper
identity, flags and executable provenance still require later read-only observation.

Orion uses WebKit and its WebExtensions support is experimental. The [reported
Proton failure](https://orionfeedback.org/d/11515-proton-vpn-extension-does-not-connect)
includes inability to control network settings and unsupported APIs. [Kagi's
technical documentation](https://help.kagi.com/orion/misc/technical.html) does not
establish a working Proton tunnel. No canary is justified by the current evidence.
Permit at most one later attended scratch-profile canary only after documented
API capability or a relevant newer release: compare public IPv4/IPv6 and DNS routes
before and after a deliberately attended connection; record sanitized route
change outcomes, not addresses, credentials or a connected icon. If it fails,
keep the extension absent and use the separate existing system app as appropriate.
