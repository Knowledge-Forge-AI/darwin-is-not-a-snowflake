"""Negative safety contracts for held per-profile Proton VPN availability."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]
KIT = ROOT / "delivery/browser-ownership-r3r2"
spec = importlib.util.spec_from_file_location("proton_delivery", KIT / "verify-delivery.py")
VERIFY = importlib.util.module_from_spec(spec)
spec.loader.exec_module(VERIFY)


class ProtonAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads((ROOT / "config/browser/proton-vpn-r3r2.json").read_text())
        # Unit seam only; connected evaluation of the real owner patch is separate.
        self.floorp = {"extensionOwner": "browser-ux", "geckoId": VERIFY.GECKO_ID,
                       "install_url": VERIFY.AMO_URL, "automaticInstallation": False,
                       "automaticSignIn": False, "autoConnectDefault": False,
                       "status": "ATTENDED_OR_UNSUPPORTED"}

    def test_source_availability_is_held(self):
        self.assertEqual(VERIFY.validate_catalog(self.catalog, self.floorp), [])

    def test_slug_and_chromium_id_cannot_stand_in_for_gecko_identity(self):
        for wrong in ("proton-vpn-firefox-extension", VERIFY.CHROMIUM_ID, "vpn@proton.me", ""):
            with self.subTest(wrong=wrong):
                candidate = copy.deepcopy(self.catalog)
                candidate["extensions"]["gecko"]["id"] = wrong
                self.assertIn("INCORRECT_GECKO_ID", VERIFY.validate_catalog(candidate, self.floorp))

    def test_wrong_store_identity_and_update_route_refused(self):
        for key, value, error in (("id", "a" * 32, "INCORRECT_CHROMIUM_ID"),
                                  ("updateUrl", "https://example.invalid/update", "INCORRECT_CHROMIUM_UPDATE_URL")):
            candidate = copy.deepcopy(self.catalog)
            candidate["extensions"]["chromium"][key] = value
            self.assertIn(error, VERIFY.validate_catalog(candidate, self.floorp))

    def test_floorp_cannot_acquire_second_core_owner(self):
        self.catalog["roles"]["floorp"] = copy.deepcopy(self.catalog["roles"]["librewolf"])
        self.assertIn("CORE_ROLE_SCOPE_OR_DUPLICATE_OWNER", VERIFY.validate_catalog(self.catalog, self.floorp))
        self.floorp["extensionOwner"] = "core"
        self.assertIn("FLOORP_ID_OR_OWNER", VERIFY.validate_catalog(self.catalog, self.floorp))

    def test_every_context_refuses_auto_install_login_connect_or_profile_mutation(self):
        for role in self.catalog["roles"]:
            for flag in ("automaticInstallation", "automaticSignIn", "autoConnectDefault", "profileMutation"):
                with self.subTest(role=role, flag=flag):
                    candidate = copy.deepcopy(self.catalog)
                    candidate["roles"][role][flag] = True
                    self.assertIn("AUTOMATIC_EFFECT:" + role, VERIFY.validate_catalog(candidate, self.floorp))

    def test_policy_and_storage_effects_refused(self):
        for flag in ("profileStorageWrites", "proxyPolicyWrites"):
            candidate = copy.deepcopy(self.catalog)
            candidate[flag] = True
            self.assertIn("PROFILE_OR_PROXY_EFFECT", VERIFY.validate_catalog(candidate, self.floorp))
        self.catalog["roles"]["brave-daily"]["deliveredPolicies"] = {"ExtensionInstallForcelist": [VERIFY.CHROMIUM_ID]}
        self.assertIn("GLOBAL_POLICY:brave-daily", VERIFY.validate_catalog(self.catalog, self.floorp))

    def test_automation_keeps_extension_free_default(self):
        self.catalog["roles"]["brave-automation"]["defaultInstalledExtensions"] = [VERIFY.CHROMIUM_ID]
        self.assertIn("AUTOMATION_DEFAULT_CHANGED", VERIFY.validate_catalog(self.catalog, self.floorp))

    def test_ephemeral_automation_cannot_claim_manual_availability(self):
        self.catalog["roles"]["brave-automation"]["status"] = "MANUAL_OFFICIAL_STORE_AVAILABILITY"
        self.assertIn("AUTOMATION_AVAILABILITY_UNQUALIFIED",
                      VERIFY.validate_catalog(self.catalog, self.floorp))

    def test_automation_cannot_claim_a_persistent_install_target(self):
        self.catalog["roles"]["brave-automation"]["persistentInstallTarget"] = True
        self.assertIn("AUTOMATION_AVAILABILITY_UNQUALIFIED",
                      VERIFY.validate_catalog(self.catalog, self.floorp))

    def test_different_extensions_still_require_one_owner_per_browser(self):
        roles = {"roles": [{"id": "brave-daily", "appOwner": "core"}]}
        owner = {"braveDaily": {"extensions": {"different-extension": {}}}}
        self.assertEqual(VERIFY.browser_ownership_conflicts(roles, owner), ["brave-daily"])
        self.assertIn("UNDECLARED_BROWSER_OWNER_CONFLICT:brave-daily",
                      VERIFY.validate_browser_ownership(roles, owner, []))

    def test_conflict_detection_reads_convenience_owner_without_proton_overlap(self):
        roles = {"roles": [{"id": "librewolf", "appOwner": "core",
                            "convenienceOwner": "browser-ux"}]}
        self.assertEqual(VERIFY.browser_ownership_conflicts(roles, {}), ["librewolf"])

    def test_known_owner_conflicts_are_held_rather_than_qualified(self):
        roles = json.loads((ROOT / "config/browser/roles-r3.json").read_text())
        conflicts = VERIFY.browser_ownership_conflicts(roles, {})
        self.assertEqual(conflicts, ["brave-daily", "librewolf", "orion"])
        self.assertEqual(VERIFY.validate_browser_ownership(roles, {}, conflicts), [])
        self.assertIn("STALE_BROWSER_OWNER_HOLD:floorp",
                      VERIFY.validate_browser_ownership(roles, {}, conflicts + ["floorp"]))

    def test_floorp_suite_belongs_to_its_installer(self):
        roles = {"roles": [{"id": "floorp", "appOwner": "browser-ux"}]}
        owner = {"floorp": {"extensions": {VERIFY.GECKO_ID: {}}}}
        self.assertEqual(VERIFY.browser_ownership_conflicts(roles, owner), [])

    def test_wrapper_and_orion_cannot_claim_effectiveness(self):
        self.catalog["roles"]["brave-youtube"]["status"] = "ACTIVE"
        self.catalog["roles"]["orion"]["status"] = "PROTECTED"
        errors = VERIFY.validate_catalog(self.catalog, self.floorp)
        self.assertIn("WRAPPER_INHERITANCE_UNQUALIFIED", errors)
        self.assertIn("ORION_TUNNEL_UNQUALIFIED", errors)

    def test_floorp_wrong_id_and_automatic_delivery_refused(self):
        for key, value in (("geckoId", "proton-vpn-firefox-extension"),
                           ("automaticInstallation", True), ("autoConnectDefault", True),
                           ("install_url", "https://example.invalid/vpn.xpi")):
            with self.subTest(key=key):
                floorp = dict(self.floorp, **{key: value})
                self.assertTrue(VERIFY.validate_catalog(self.catalog, floorp))

    def test_source_drift_and_unsafe_inventory_paths_refuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = {"path": "missing.json", "after_sha256": "a" * 64}
            self.assertEqual(VERIFY.check_paths(root, [record], "after_sha256"),
                             ["SOURCE_DRIFT:missing.json"])
            for path in ("../outside", "/outside"):
                record["path"] = path
                self.assertEqual(VERIFY.check_paths(root, [record], "after_sha256"),
                                 ["UNSAFE_BINDING_PATH"])


if __name__ == "__main__":
    unittest.main()
