# Core Proton VPN attended installation

Status: source prepared, runtime inactive. This procedure is held until a
separate approved source release and attended installation authorization.
Core owns Proton VPN on Brave daily and LibreWolf. Browser UX owns convenience
extensions on those browsers and the whole Floorp suite. Extension ownership
is per role and ID; it does not give another repository a policy writer.

Use the existing installed application and the intended existing daily profile.
The operation below installs into that profile only. Repeat in another profile
only with separate authorization. Do not use automation, private windows,
YouTube, Orion, or legacy Chrome as the installation target.

1. Before adding anything, record the browser version, an anonymous local
   profile label, existing extension IDs and enabled states, forced-policy IDs,
   and whether Clear New Tab wallpaper and password-manager behavior work.
   Keep paths, screenshots, credentials and raw profile data local. Record
   policy values before and after locally; report only equality. Inspect
   `brave://policy` or `about:policies`. If installation is blocked by policy,
   stop; do not remove restrictions or overwrite `ExtensionSettings`.
   If Proton is already present, skip all Add actions and observe it without
   reinstalling. Record `OBSERVED_EXISTING`; otherwise use `ADDED_THIS_SESSION`
   only after the actual Add action. Confirm both inventories are complete;
   explicitly confirm an empty extension or forced-policy inventory if observed.
2. For Brave daily, open the official
   [Proton VPN Web Store listing](https://chromewebstore.google.com/detail/proton-vpn-fast-secure/jplgfhpmjnbigmhklmmbgecoobifkmpa)
   in that profile. Check Proton as publisher and ID
   `jplgfhpmjnbigmhklmmbgecoobifkmpa`. Click **Add to Brave** (the label may say
   **Add to Chrome**), inspect permissions, and confirm **Add extension**.
   This is the installer action; merely opening the listing installs nothing.
   Keep the official Web Store update route
   `https://clients2.google.com/service/update2/crx`; no unpacked build,
   external manifest, pinned downgrade, or alternate update server.
3. For LibreWolf, first run the R3R3 HTTPS identity kit as an ordinary user.
   Require fresh AMO `guid=vpn@proton.ch`, current XPI manifest ID/version and
   AMO SHA256 agreement. The immutable upstream builder is corroboration only.
   Then open the official
   [AMO listing](https://addons.mozilla.org/en-US/firefox/addon/proton-vpn-firefox-extension/)
   in the intended LibreWolf profile. Check Proton AG and click **Add to
   Firefox**, inspect permissions, and approve **Add**. Let the browser enforce
   signing; inspect `about:support`/add-on details for the installed ID,
   version, active state and valid signing. If unavailable or incompatible,
   retain the held result. Never turn off signing, load a temporary add-on,
   modify `extensions.json`, remove bundled uBO, or inject Firefox policies.
4. Do not log in or connect. Keep private-window permission at its existing
   default. Inspect any auto-connect control without enabling it. If the
   logged-out UI cannot attest auto-connect, record `UNOBSERVABLE_LOGGED_OUT`;
   connection readiness stays pending. Preserve existing Proton settings if
   already installed; never reset them silently.
5. In `brave://extensions` (Details) or `about:addons` plus `about:support`,
   read the exact ID, version and enabled state. Compare the existing extension
   and forced-policy inventory, wallpaper, credentials, Shields/hardening,
   policy values and DNS preferences with the baseline. Submit only the
   sanitized R3R3 presence observation to the readback validator. A successful
   result means observed installed presence, not a protected tunnel.
   For LibreWolf, pass the identity kit JSON as `--identity-evidence` and copy
   its XPI SHA256 into `identitySha256`. The observed installed version must
   match that receipt. Refresh identity during the session (within 24 hours).
   If AMO updates between readback and Add, refresh the receipt and compare
   again; do not downgrade or reinstall. The receipt links the observed version
   to the fetched package; it is not independent inspection of installed bytes.
6. Stop on any removed/disabled existing extension, changed forced policy,
   wallpaper or credential regression. Do not delete profiles or extension
   storage to recover. If this session added Proton, an attended disable of
   that one new add-on is the bounded rollback; uninstall/data deletion needs
   a separate preservation decision. If Proton was already present, do not
   reinstall, downgrade, reset or claim this procedure installed it.

Proton documents Brave and LibreWolf support, browser-only traffic coverage,
and Firefox DNS caveats in its
[installation guide](https://protonvpn.com/support/browser-extensions).
No `network.trr.mode` or other DoH setting is changed here. The later connection
canary must characterize normal/private DNS, DoH, proxy exceptions, WebRTC,
IPv4/IPv6, and interaction with an already active system VPN. Keep public IPs
and resolver addresses local; record only whether routes changed as expected.

Brave policy audit: Chromium `ExtensionSettings` can override other extension
policies, including force lists. A platform/user policy is not an isolation
boundary for one chosen daily profile. Its effective precedence and propagation
must be observed in every affected profile before policy adoption. This phase
uses attended installation and emits no Proton managed settings. See
[Google's policy reference](https://support.google.com/chrome/a/answer/9867568)
and [Brave's policy guide](https://support.brave.com/hc/en-us/articles/360039248271-Group-Policy).
Firefox `allowed` permits an install but does not perform one;
`normal_installed` and `force_installed` install automatically, so neither is
selected here. See [Mozilla's reference](https://mozilla.github.io/policy-templates/#extensionsettings).
