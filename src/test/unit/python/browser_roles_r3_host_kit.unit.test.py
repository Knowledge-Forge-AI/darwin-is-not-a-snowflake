# nix-darwin-test-source-target: delivery/browser-ownership-r3/HOST-READONLY-QUALIFICATION/validate.py
# nix-darwin-test-source-target: delivery/browser-ownership-r3/HOST-READONLY-QUALIFICATION/profile_isolation.py
# nix-darwin-test-source-target: delivery/browser-ownership-r3/HOST-READONLY-QUALIFICATION/diagnose.py
"""Passive kit refuses incomplete readback and tests topology without profile reads."""
import copy
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest

KIT = Path(__file__).resolve().parents[4] / "delivery/browser-ownership-r3/HOST-READONLY-QUALIFICATION"


def module(name):
    spec = importlib.util.spec_from_file_location(name, KIT / f"{name}.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


VALIDATOR = module("validate")
ISOLATION = module("profile_isolation")
DIAGNOSTIC = module("diagnose")


class PassiveKitTests(unittest.TestCase):
    def setUp(self):
        self.requirements = {"role-test": {"methods": ["attended-effective-readback"]}}
        self.binding = {"sourceCandidateDigest": "a" * 64}
        self.evidence = {
            "schema": "browser-roles-r3-host-readback-v1", "sourceCandidateDigest": "a" * 64,
            "observations": {"role-test": {"status": "PASS", "method": "attended-effective-readback",
                             "observedAt": "2026-01-01T00:00:00Z", "evidenceSha256": "b" * 64}},
        }

    def test_complete_structure_requires_independent_disposition(self):
        self.assertEqual(VALIDATOR.validate(self.evidence, self.binding, self.requirements), [])

    def test_binding_and_missing_observation_fail_closed(self):
        self.evidence["sourceCandidateDigest"] = "c" * 64
        self.evidence["observations"] = {}
        result = VALIDATOR.validate(self.evidence, self.binding, self.requirements)
        self.assertIn("SOURCE_BINDING_MISMATCH", result)
        self.assertIn("role-test", result)

    def test_pending_unsupported_or_non_effective_method_holds(self):
        for change in ({"status": "PENDING"}, {"status": "ATTENDED_OR_UNSUPPORTED"},
                       {"method": "writer-exit-zero"}, {"evidenceSha256": "not-a-digest"},
                       {"observedAt": "2099-01-01T00:00:00Z"}, {"observedAt": "2026-01-01"}):
            row = copy.deepcopy(self.evidence)
            row["observations"]["role-test"].update(change)
            with self.subTest(change=change):
                self.assertIn("role-test", VALIDATOR.validate(row, self.binding, self.requirements))

    def test_malformed_observations_do_not_pass(self):
        for value in (None, [], "PASS", True):
            row = dict(self.evidence, observations=value)
            self.assertIn("OBSERVATIONS_OBJECT_REQUIRED", VALIDATOR.validate(row, self.binding, self.requirements))

    def test_shipped_template_is_held_and_covers_every_role(self):
        evidence = json.loads((KIT / "evidence-template.json").read_text())
        requirements = json.loads((KIT / "REQUIREMENTS.json").read_text())
        binding = json.loads((KIT / "CANDIDATE-BINDING.json").read_text())
        self.assertTrue(VALIDATOR.validate(evidence, binding, requirements))
        self.assertEqual({item["role"] for item in requirements.values() if item["role"] != "cross-role"},
                         {"brave-automation", "safari", "librewolf", "brave-daily", "orion", "floorp", "brave-youtube"})

    def test_topology_rejects_nested_and_symlink_alias(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = [root / name for name in ("automation", "daily", "youtube")]
            for path in paths:
                path.mkdir()
            self.assertTrue(ISOLATION.check(paths))
            nested = paths[1] / "nested"
            nested.mkdir()
            self.assertFalse(ISOLATION.check([paths[0], paths[1], nested]))
            link = root / "alias"
            link.symlink_to(paths[1])
            self.assertFalse(ISOLATION.check([paths[0], paths[1], link]))

    def test_metadata_never_claims_extension_effectiveness(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "App.app"
            (app / "Contents").mkdir(parents=True)
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "test.app", "CFBundleShortVersionString": "1"}))
            result = DIAGNOSTIC.app_metadata(app, "test.app")
            self.assertEqual(result["status"], "IDENTITY_MATCH")
            self.assertEqual(result["effectiveReadiness"], "UNQUALIFIED")
            self.assertEqual(DIAGNOSTIC.app_metadata(app, "other.app")["status"], "IDENTITY_MISMATCH")
            unverified = DIAGNOSTIC.app_metadata(app, None)
            self.assertEqual(unverified["status"], "IDENTITY_UNATTESTED")
            self.assertEqual(unverified["observedBundleIdentifier"], "test.app")
            self.assertEqual(unverified["effectiveReadiness"], "UNQUALIFIED")


if __name__ == "__main__":
    unittest.main()
