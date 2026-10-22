"""Read-only source binding and Proton availability contract checks."""
import argparse
import hashlib
import json
from pathlib import Path


CHROMIUM_ID = "jplgfhpmjnbigmhklmmbgecoobifkmpa"
GECKO_ID = "vpn@proton.ch"
UPDATE_URL = "https://clients2.google.com/service/update2/crx"
AMO_URL = "https://addons.mozilla.org/firefox/downloads/latest/proton-vpn-firefox-extension/latest.xpi"
CORE_ROLES = {"brave-daily", "brave-automation", "brave-youtube", "librewolf", "orion"}
OWNER_ROLE_KEYS = {"brave-daily": "braveDaily", "brave-automation": "braveAutomation",
                   "librewolf": "librewolf", "orion": "orion", "floorp": "floorp"}


def load_ledger(kit):
    """Keep the reviewed producer ledger immutable while binding revised bytes."""
    revision = kit / "REVISION-BINDINGS.json"
    return json.loads((revision if revision.is_file() else kit / "SOURCE-BINDINGS.json").read_text())


def browser_ownership_conflicts(role_manifest, owner_intents):
    """Detect split definition ownership even when extension IDs do not overlap."""
    conflicts = set()
    for role in role_manifest["roles"]:
        installer = role["appOwner"]
        convenience = role.get("convenienceOwner")
        if installer == "core" and convenience == "browser-ux":
            conflicts.add(role["id"])
        suite = owner_intents.get(OWNER_ROLE_KEYS.get(role["id"], ""), {}).get("extensions", {})
        if suite and installer != "browser-ux":
            conflicts.add(role["id"])
    return sorted(conflicts)


def validate_browser_ownership(role_manifest, owner_intents, held_conflicts):
    conflicts = set(browser_ownership_conflicts(role_manifest, owner_intents))
    held = set(held_conflicts)
    return (["UNDECLARED_BROWSER_OWNER_CONFLICT:" + role for role in sorted(conflicts - held)]
            + ["STALE_BROWSER_OWNER_HOLD:" + role for role in sorted(held - conflicts)])


def validate_automation(automation):
    errors = []
    if automation.get("defaultInstalledExtensions") != [] or automation.get("ordinaryTestsUseVpn") is not False:
        errors.append("AUTOMATION_DEFAULT_CHANGED")
    if (automation.get("status") != "AUTOMATION_AVAILABILITY_HELD"
            or automation.get("persistentInstallTarget") is not False
            or automation.get("extensionsEnabled") is not False):
        errors.append("AUTOMATION_AVAILABILITY_UNQUALIFIED")
    return errors


def validate_catalog(catalog, floorp):
    """Reject unsafe intent without reading a browser, profile or credential."""
    errors = []
    extensions = catalog.get("extensions", {})
    if extensions.get("chromium", {}).get("id") != CHROMIUM_ID:
        errors.append("INCORRECT_CHROMIUM_ID")
    if extensions.get("chromium", {}).get("updateUrl") != UPDATE_URL:
        errors.append("INCORRECT_CHROMIUM_UPDATE_URL")
    if extensions.get("gecko", {}).get("id") != GECKO_ID:
        errors.append("INCORRECT_GECKO_ID")
    if extensions.get("gecko", {}).get("installUrl") != AMO_URL:
        errors.append("INCORRECT_AMO_ROUTE")
    roles = catalog.get("roles", {})
    if set(roles) != CORE_ROLES:
        errors.append("CORE_ROLE_SCOPE_OR_DUPLICATE_OWNER")
    if catalog.get("selected") is not False or catalog.get("deployment") != "HELD_SOURCE_RELEASE_REQUIRED":
        errors.append("SOURCE_SELECTION_OR_HOLD_CHANGED")
    if catalog.get("profileStorageWrites") is not False or catalog.get("proxyPolicyWrites") is not False:
        errors.append("PROFILE_OR_PROXY_EFFECT")
    for role, row in roles.items():
        if row.get("extensionOwner") != "core":
            errors.append("DUPLICATE_OR_INCORRECT_OWNER:" + role)
        if any(row.get(flag) is not False for flag in
               ("automaticInstallation", "automaticSignIn", "autoConnectDefault", "profileMutation")):
            errors.append("AUTOMATIC_EFFECT:" + role)
        if row.get("deliveredPolicies") != {}:
            errors.append("GLOBAL_POLICY:" + role)
    errors += validate_automation(roles.get("brave-automation", {}))
    if roles.get("brave-youtube", {}).get("status") != "WRAPPER_PROJECTION_HELD":
        errors.append("WRAPPER_INHERITANCE_UNQUALIFIED")
    orion = roles.get("orion", {})
    if (orion.get("status"), orion.get("capability"), orion.get("installed")) != (
            "REQUESTED_COMPATIBILITY_HELD", "PROXY_API_UNQUALIFIED", False):
        errors.append("ORION_TUNNEL_UNQUALIFIED")
    if floorp.get("extensionOwner") != "browser-ux" or floorp.get("geckoId") != GECKO_ID:
        errors.append("FLOORP_ID_OR_OWNER")
    if floorp.get("install_url") != AMO_URL:
        errors.append("FLOORP_AMO_ROUTE")
    if any(floorp.get(flag) is not False for flag in
           ("automaticInstallation", "automaticSignIn", "autoConnectDefault")):
        errors.append("FLOORP_AUTOMATIC_EFFECT")
    if floorp.get("status") != "ATTENDED_OR_UNSUPPORTED":
        errors.append("FLOORP_EFFECTIVENESS_UNQUALIFIED")
    return errors


def check_paths(root, records, field):
    errors = []
    for record in records:
        relative = Path(record["path"])
        if relative.is_absolute() or ".." in relative.parts:
            errors.append("UNSAFE_BINDING_PATH")
            continue
        path = root / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual != record[field]:
            errors.append("SOURCE_DRIFT:" + record["path"])
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--owner-candidate", type=Path, required=True)
    parser.add_argument("--floorp-readback", type=Path, required=True,
                        help="JSON from Nix evaluation of the exact patched Floorp intent")
    parser.add_argument("--owner-intents", type=Path, required=True,
                        help="JSON of all browser role intents evaluated from the exact owner patch")
    args = parser.parse_args()
    kit = args.core / "delivery/browser-ownership-r3r2"
    ledger = load_ledger(kit)
    owner = json.loads((kit / "browser-ux-manifest.json").read_text())
    errors = check_paths(args.core, ledger["records"], "after_sha256")
    errors += check_paths(args.owner_candidate, owner["paths"], "after_sha256")
    errors += check_paths(args.owner_candidate, owner["unchangedDependencies"], "sha256")
    catalog = json.loads((args.core / "config/browser/proton-vpn-r3r2.json").read_text())
    roles = json.loads((args.core / "config/browser/roles-r3.json").read_text())
    intents = json.loads(args.owner_intents.read_text())
    conflicts = browser_ownership_conflicts(roles, intents)
    errors += validate_browser_ownership(roles, intents, catalog["heldBrowserOwnerConflicts"])
    errors += validate_catalog(catalog, json.loads(args.floorp_readback.read_text())[GECKO_ID])
    print(json.dumps({"status": "FAIL" if errors else "PASS", "errors": errors,
                      "heldBrowserOwnerConflicts": conflicts, "singleOwnerQualified": not conflicts,
                      "qualification": "COMPATIBILITY_HELD" if conflicts else "SOURCE_ONLY",
                      "nativeGraphQualified": False, "profileReadOrWrite": False}))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
