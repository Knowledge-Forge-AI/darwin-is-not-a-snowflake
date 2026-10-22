"""Attended presence, fresh package identity and preservation negatives; no browser."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[4]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PRESENCE = load("presence", "libexec/browser-extension-delivery/readback.py")
IDENTITY = load("identity", "delivery/browser-ownership-r3r3/identity-readback.py")


class PresenceTests(unittest.TestCase):
    def setUp(self):
        self.observation = {
            "role": "brave-daily", "id": PRESENCE.CHROMIUM, "semanticOwner": "core",
            "version": "observed-version", "attended": True, "profileConfirmed": True,
            "officialStore": True, "enabled": True, "policiesUnchanged": True,
            "wallpaperPreserved": True, "credentialsPreserved": True,
            "hardeningPreserved": True, "dnsUnchanged": True,
            "signedInDuringCanary": False, "connectionAttempted": False, "connected": False,
            "autoConnect": "UNOBSERVABLE_LOGGED_OUT",
            "existingBefore": {"cnt": "enabled", "keepassxc": "enabled"},
            "existingAfter": {"cnt": "enabled", "keepassxc": "enabled"},
            "forcedBefore": ["cnt", "keepassxc"], "forcedAfter": ["cnt", "keepassxc"],
            "extensionBaselineComplete": True, "forcedPolicyBaselineComplete": True,
            "installationAction": "ADDED_THIS_SESSION",
        }

    def identity_evidence(self):
        return {"status": "FRESH_PACKAGE_IDENTITY_OBSERVED_SIGNING_PENDING",
                "observedAtUtc": datetime.now(timezone.utc).isoformat(),
                "xpi": {"guid": PRESENCE.GECKO, "version": "observed-version",
                        "sha256": "a" * 64, "signatureContainerPresent": True}}

    def test_empty_baselines_need_explicit_complete_observation(self):
        row = dict(self.observation, existingBefore={}, existingAfter={}, forcedBefore=[], forcedAfter=[])
        self.assertTrue(PRESENCE.validate(row))
        row.update(emptyExtensionBaselineConfirmed=True, emptyForcedBaselineConfirmed=True)
        self.assertEqual(PRESENCE.validate(row), [])
        for flag in ("extensionBaselineComplete", "forcedPolicyBaselineComplete"):
            self.assertTrue(PRESENCE.validate(dict(row, **{flag: False})))

    def test_existing_proton_cannot_be_claimed_added(self):
        row = copy.deepcopy(self.observation)
        row["existingBefore"][PRESENCE.CHROMIUM] = "enabled"
        row["existingAfter"][PRESENCE.CHROMIUM] = "enabled"
        self.assertIn("INSTALLATION_ACTION_MISMATCH", PRESENCE.validate(row))
        row["installationAction"] = "OBSERVED_EXISTING"
        self.assertEqual(PRESENCE.validate(row), [])

    def test_presence_is_separate_from_connection(self):
        self.assertEqual(PRESENCE.validate(self.observation), [])

    def test_cli_binds_receipt_and_does_not_claim_existing_installation(self):
        row = dict(self.observation, role="librewolf", id=PRESENCE.GECKO,
                   browserSignatureValid=True, identitySha256="a" * 64)
        row["existingBefore"] = {PRESENCE.GECKO: "enabled"}
        row["existingAfter"] = dict(row["existingBefore"])
        row["installationAction"] = "OBSERVED_EXISTING"
        with tempfile.TemporaryDirectory() as directory:
            observation = Path(directory) / "observation.json"
            receipt = Path(directory) / "receipt.json"
            observation.write_text(json.dumps(row))
            receipt.write_text(json.dumps(self.identity_evidence()))
            for flags, expected in (([], 1), (["--identity-evidence", str(receipt)], 0)):
                output = io.StringIO()
                with patch("sys.argv", ["readback", str(observation), *flags]), patch("sys.stdout", output):
                    self.assertEqual(PRESENCE.main(), expected)
                result = json.loads(output.getvalue())
                self.assertFalse(result["installationPerformed"])
                self.assertFalse(result["protected"])
                self.assertFalse(result["connectionQualified"])

    def test_malformed_observation_refused(self):
        for row in (None, [], "invalid"):
            self.assertEqual(PRESENCE.validate(row), ["MALFORMED_OBSERVATION"])

    def test_auto_install_login_connect_and_other_roles_refused(self):
        for key in ("attended", "profileConfirmed", "officialStore"):
            row = dict(self.observation, **{key: False})
            self.assertTrue(PRESENCE.validate(row))
        for key in ("signedInDuringCanary", "connectionAttempted", "connected"):
            row = dict(self.observation, **{key: True})
            self.assertTrue(PRESENCE.validate(row))
        for role in ("brave-automation", "brave-youtube", "orion", "chrome", "safari"):
            self.assertEqual(PRESENCE.validate(dict(self.observation, role=role)),
                             ["ROLE_INSTALLATION_HELD_OR_NOT_APPLICABLE"])

    def test_missing_version_or_wrong_id_owner_refused(self):
        for key, value in (("version", None), ("version", ""), ("id", "other"),
                           ("semanticOwner", "browser-ux"), ("autoConnect", "ON")):
            self.assertTrue(PRESENCE.validate(dict(self.observation, **{key: value})))

    def test_existing_extension_and_forced_policy_changes_refused(self):
        for key, value in (("existingAfter", {"cnt": "enabled"}),
                           ("existingAfter", {"cnt": "disabled", "keepassxc": "enabled"}),
                           ("forcedAfter", ["cnt"]), ("forcedBefore", None)):
            self.assertTrue(PRESENCE.validate(dict(self.observation, **{key: value})))
        for key in ("policiesUnchanged", "wallpaperPreserved", "credentialsPreserved",
                    "hardeningPreserved", "dnsUnchanged"):
            self.assertTrue(PRESENCE.validate(dict(self.observation, **{key: False})))

    def test_gecko_needs_current_identity_and_browser_signature(self):
        for role, owner in (("librewolf", "core"), ("floorp", "browser-ux")):
            row = dict(self.observation, role=role, id=PRESENCE.GECKO, semanticOwner=owner)
            self.assertIn("FRESH_AMO_XPI_IDENTITY_REQUIRED", PRESENCE.validate(row))
            row.update(freshAmoXpiIdentity=True, browserSignatureValid=True, identitySha256="a" * 64)
            self.assertIn("FRESH_AMO_XPI_IDENTITY_REQUIRED", PRESENCE.validate(row))
            self.assertEqual(PRESENCE.validate(row, self.identity_evidence()), [])
            row["browserSignatureValid"] = False
            self.assertIn("BROWSER_SIGNATURE_ATTESTATION_REQUIRED", PRESENCE.validate(row, self.identity_evidence()))

    def test_gecko_version_receipt_and_freshness_must_match(self):
        row = dict(self.observation, role="librewolf", id=PRESENCE.GECKO,
                   browserSignatureValid=True, identitySha256="a" * 64)
        for field, value in (("guid", "wrong"), ("version", "newer-version"),
                             ("sha256", "b" * 64), ("sha256", "invalid"),
                             ("signatureContainerPresent", False)):
            evidence = self.identity_evidence(); evidence["xpi"][field] = value
            self.assertTrue(PRESENCE.validate(row, evidence))
        for timestamp in (None, "invalid", "2026-01-01T00:00:00",
                          (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
                          (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()):
            evidence = self.identity_evidence(); evidence["observedAtUtc"] = timestamp
            self.assertTrue(PRESENCE.validate(row, evidence))


class IdentityTests(unittest.TestCase):
    def package(self, guid="vpn@proton.ch", version="1.0", signed=True):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"version": version,
                             "browser_specific_settings": {"gecko": {"id": guid}}}))
            if signed:
                for name in ("mozilla.rsa", "mozilla.sf", "manifest.mf"):
                    archive.writestr("META-INF/" + name, "synthetic container; not a signature")
        data = output.getvalue()
        amo = {"guid": "vpn@proton.ch", "current_version": {"version": "1.0",
               "file": {"hash": "sha256:" + hashlib.sha256(data).hexdigest()}}}
        return amo, data

    def test_hash_guid_and_version_consistency_does_not_attest_signing(self):
        amo, data = self.package()
        result = IDENTITY.xpi_identity(amo, data)
        self.assertFalse(result["cryptographicSignatureVerified"])
        self.assertEqual(result["browserSignatureVerification"], "REQUIRED_ON_ATTENDED_INSTALL")

    def test_slug_wrong_guid_hash_version_and_unsigned_package_refused(self):
        amo, data = self.package()
        for field, value in (("guid", "proton-vpn-firefox-extension"),):
            bad = copy.deepcopy(amo); bad[field] = value
            with self.assertRaises(ValueError):
                IDENTITY.xpi_identity(bad, data)
        bad = copy.deepcopy(amo); bad["current_version"]["file"]["hash"] = "sha256:wrong"
        with self.assertRaises(ValueError):
            IDENTITY.xpi_identity(bad, data)
        for args in (("wrong-guid", "1.0", True), ("vpn@proton.ch", "2.0", True),
                     ("vpn@proton.ch", "1.0", False)):
            amo, data = self.package(*args)
            with self.assertRaises(ValueError):
                IDENTITY.xpi_identity(amo, data)

    def test_https_and_official_origin_required(self):
        for url in ("http://addons.mozilla.org/file", "https://example.invalid/xpi",
                    "https://addons.mozilla.org@evil.invalid/file"):
            with self.assertRaises(ValueError):
                IDENTITY.official_https(url)
        IDENTITY.official_https(IDENTITY.AMO)


if __name__ == "__main__":
    unittest.main()
