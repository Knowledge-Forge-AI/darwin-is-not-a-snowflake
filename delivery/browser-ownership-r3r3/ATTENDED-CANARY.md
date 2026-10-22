# Later attended presence and connection handoff

No browser canary ran in this phase. Separate approved source release and
attended installation authority are required before following the core
`config/browser/proton-vpn-install.md` or the patched Browser UX
`delivery/proton-vpn-floorp.md` installer procedure. The resulting observation
is limited to the existing selected profile. Other profiles do not inherit a
successful observation automatically.

Use `presence-template.json`, record sanitized observations after installation,
and run core's validator:

```sh
rtk proxy env PYTHONDONTWRITEBYTECODE=1 python3 libexec/browser-extension-delivery/readback.py \
  "$LOCAL_SANITIZED_OBSERVATION"
```

For LibreWolf/Floorp append `--identity-evidence "$LOCAL_GECKO_IDENTITY_JSON"`.
The receipt must come from `identity-readback.py` during this session (within
24 hours), match the installed version, GUID and `identitySha256`, and contain
the package signing container. A bare `freshAmoXpiIdentity=true` is insufficient.
If the store version changes, refresh the receipt and repeat comparison without
downgrading or reinstalling. Browser valid-signature readback remains required;
receipt matching does not independently attest installed package bytes.

The template starts incomplete and fails. Record only IDs, enabled states,
browser/add-on versions and comparison outcomes; no username, profile path,
password, token, account ID, IP, resolver address or raw policy/profile payload.
`existingBefore` and `existingAfter` must retain all observed prior extension
IDs/states, including CNT and any KeePassXC extension that was present.
`forcedBefore` and `forcedAfter` must match. Keep policy values local and report
only equality. Set `extensionBaselineComplete` and `forcedPolicyBaselineComplete`
only after complete readback. Empty inventories additionally require the
corresponding `emptyExtensionBaselineConfirmed` or `emptyForcedBaselineConfirmed`.
Use `OBSERVED_EXISTING` for `installationAction` if Proton was present before;
otherwise use `ADDED_THIS_SESSION` after installation. Existing presence never
means this procedure installed it. Confirm wallpaper, credential behavior, hardening and DNS
preservation. Gecko additionally requires fresh AMO/XPI identity and browser
valid-signature readback. Existing signed add-on presence should be observed
without reinstallation. A passing operator observation is presence evidence,
not independent inspection or protected-tunnel qualification.

Login and connection require a later separate canary. At that time verify
auto-connect remains off before any account action; do not retain credentials.
Characterize public IPv4/IPv6 route changes, DNS/DoH and WebRTC leaks in normal
and separately authorized private windows, proxy bypasses and interaction with
an already active native VPN. Record qualitative results only. Do not blanket
override `network.trr.mode`. A badge alone never establishes protection.

Brave YouTube remains WRAPPER_PROJECTION_HELD. A later separately authorized
scratch prototype must use a disposable copy, retain theme/CNT and the strict
extension allowlist, prove extension/tunnel APIs, and observe short streaming,
wallpaper and battery behavior. It must not mutate the live profile, relax flags
globally or copy daily/automation profiles. If the extension cannot operate
with the retained flags, stop and retain the hold.

Orion remains REQUESTED_COMPATIBILITY_HELD. No new API evidence was established;
therefore no new scratch canary is proposed now. Only new supported proxy API
evidence can justify one later attended scratch test. Automation uses no VPN
extension by design; a custom test context is an optional separately approved
task. Safari, Tor, Mullvad and Vivaldi remain outside this slice.
