# Later ordinary-user read-only R3R2 readback

Status: modified R3R2 native graph `PENDING`. The provider attempt returned
`ENVIRONMENT_DENIED` for both full graph and claims. `PRIOR-NATIVE-R3R1.json`
carries the operator-supplied successful old-candidate proof separately.

Use the reviewed core source root as `CORE_ROOT`, the current external owners
as `BROWSER_UX_ROOT` and `COMPOSITION_ROOT`, and the real selected private flake
as `SELECTED_FLAKE`. Set `TASK_SCRATCH` to a new empty run under the configured
scratch root. These are source roots, never browser profile roots.

```sh
rtk proxy python3 -B "$CORE_ROOT/delivery/browser-ownership-r3r2/prepare-readback.py" \
  --scratch "$TASK_SCRATCH" --browser-ux "$BROWSER_UX_ROOT" \
  --composition "$COMPOSITION_ROOT"

rtk proxy python3 -B "$CORE_ROOT/delivery/browser-ownership-r3r2/native-readback.py" \
  --selected-flake "$SELECTED_FLAKE" \
  --composition "$TASK_SCRATCH/composition" \
  --browser-ux "$TASK_SCRATCH/browser-ux" \
  --output "$TASK_SCRATCH/r3r2-native-readback.json"
```

Preparation copies owner source to scratch and applies the ONE cumulative R3R2
Browser UX patch plus the retained R3R1 composition patch there. It checks exact
preimages, dependencies and postimages. Apply neither patch to a live owner.
Do not apply the original R3 Browser UX patch in addition to its replacement.
Neither script stages, commits, builds, activates, launches a browser or changes
the snapshot/lock. Evaluation uses offline/no-update/no-write-lock flags.
Native input routes retain the selected `composition-nd` contract.

The native result is bound to `REVISION-BINDINGS.json` before and after evaluation;
the original producer `SOURCE-BINDINGS.json` remains immutable lineage.
Both hosts must fully evaluate while R3/core ownership remain false, assertions
and writer guards pass, Chrome/Firefox continuity persists, automation is absent,
and the new VPN intent is null in the inactive graph. Complete application claims
are read separately. Graph evaluation does not prove any browser extension works.
Retain sanitized output privately for dispatcher/manager disposition; a readback
pass does not release the active source guard.

Later attended extension checks require separate authorization. Check the actual
binary, version, bundle and three disjoint Brave data roots without exporting
profile content. Observe effective profile policy and extension identities;
verify auto-connect is off before representative MCP/tests or playback. Observe
Shields, uBO, Apple Passwords and CNT wallpaper continuity. For a deliberate VPN
compatibility check, compare public-IP and DNS route changes in the exact context,
including Firefox DNS/DoH and split-tunneling conditions; record only outcomes.
No automatic connection, login, proxy policy, DNS policy, CDP exposure or profile
database manipulation is supplied by this kit. Orion's conditional single canary
and Brave YouTube's separate held action are defined in `COMPATIBILITY-MATRIX.md`.

The automation launcher has no persistent extension target and disables
extensions. Do not try to install Proton in that ephemeral context or invent a
fourth persistent Brave root. Automation availability requires a separate approved
design. Resolve the retained Brave daily/LibreWolf/Orion split definition ownership
before owner adoption; source checks disclose it and do not qualify single ownership.

Before any Gecko identity adoption, obtain fresh TLS-authenticated AMO metadata
and an immutable upstream reference. This read-only source metadata check has
not passed in this provider and performs no installation:

```sh
rtk proxy python3 -B - <<'PY'
import json
import urllib.request
def read_json(url):
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.load(response)
amo = read_json("https://addons.mozilla.org/api/v5/addons/addon/proton-vpn-firefox-extension/")
assert amo["guid"] == "vpn@proton.ch", "AMO_GUID_MISMATCH_HELD"
commit = read_json("https://api.github.com/repos/ProtonVPN/proton-vpn-browser-extension/commits/main")["sha"]
url = "https://raw.githubusercontent.com/ProtonVPN/proton-vpn-browser-extension/" + commit + "/webpack.config.js"
print(json.dumps({"guid": amo["guid"], "upstreamCommit": commit, "immutableBuilderUrl": url}))
PY
```

Inspect the printed immutable builder URL for the Gecko manifest property and
bind the reference/content hash before adoption. A resolved commit alone is not
corroboration. The `latest.xpi` locator intentionally follows signed AMO updates;
it is not a package pin. During a separately authorized attended LibreWolf check,
observe the add-on's version, signing/update route, global and per-add-on automatic
update settings, and preservation of existing extension storage. Do not turn off
updates, install a pinned downgrade, rewrite DNS preferences or export profile data.
