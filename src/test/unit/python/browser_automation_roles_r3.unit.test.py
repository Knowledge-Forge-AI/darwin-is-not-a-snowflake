# nix-darwin-test-source-target: libexec/browser-automation/launch.py
"""Exercise scratch isolation and argument guards without launching a browser."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location("browser_automation_r3", ROOT / "libexec/browser-automation/launch.py")
ADAPTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTER)


class AutomationRoleTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.binary = self.root / "fake-browser"
        self.binary.touch()
        self.profile = self.root / "automation"
        self.profile.mkdir()

    def prepare(self, *arguments):
        return ADAPTER.prepare(str(self.binary), [f"--user-data-dir={self.profile}", *arguments])

    def test_fresh_profile_has_no_save_autosignin_passkeys_or_autofill(self):
        command = self.prepare("--remote-debugging-pipe", "--enable-automation", "about:blank")
        preferences = json.loads((self.profile / "Default/Preferences").read_text())
        self.assertFalse(preferences["credentials_enable_service"])
        self.assertFalse(preferences["credentials_enable_autosignin"])
        self.assertFalse(preferences["credentials_enable_passkeys"])
        self.assertEqual(preferences["autofill"], {"profile_enabled": False, "credit_card_enabled": False})
        self.assertIn("--disable-sync", command)
        self.assertIn("--remote-debugging-pipe", command)
        self.assertNotIn("--disable-extensions", command)  # Do not disable native Shields.
        self.assertEqual((self.profile / "Default/Preferences").stat().st_mode & 0o777, 0o600)

    def test_ephemeral_debug_port_is_loopback(self):
        self.assertIn("--remote-debugging-address=127.0.0.1", self.prepare("--remote-debugging-port=0"))

    def test_nonempty_profile_refused_without_touching_contents(self):
        sentinel = self.profile / "personal-data"
        sentinel.write_bytes(b"unchanged")
        with self.assertRaisesRegex(ValueError, "NOT_EMPTY"):
            self.prepare()
        self.assertEqual(sentinel.read_bytes(), b"unchanged")
        self.assertFalse((self.profile / "Default").exists())

    def test_missing_or_duplicate_data_root_refused(self):
        for arguments in ([], [f"--user-data-dir={self.profile}"] * 2):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, "ONE_FRESH"):
                ADAPTER.prepare(str(self.binary), arguments)

    def test_symlink_profile_refused(self):
        link = self.root / "link"
        link.symlink_to(self.profile)
        with self.assertRaisesRegex(ValueError, "DIRECTORY_INVALID"):
            ADAPTER.prepare(str(self.binary), [f"--user-data-dir={link}"])
        self.assertEqual(list(self.profile.iterdir()), [])

    def test_personal_root_refused_even_when_tmpdir_points_there(self):
        with mock.patch.object(ADAPTER.tempfile, "gettempdir", return_value="/Users/fixture/Library/Browser"), \
                self.assertRaisesRegex(ValueError, "NATIVE_TEMPORARY_ROOT"):
            self.prepare()
        self.assertEqual(list(self.profile.iterdir()), [])

    def test_unsafe_attach_extensions_and_security_flags_refused(self):
        flags = [flag for flag in ADAPTER.FORBIDDEN if flag not in ADAPTER.OMIT_FRAMEWORK_DEFAULTS]
        for flag in (*flags, "--no-sandbox=true", "--disable-extensions=all", "--remote-debugging-port=9222", "--remote-debugging-port", "--enable-features=UnqualifiedFeature"):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                self.prepare(flag)
            self.assertEqual(list(self.profile.iterdir()), [])

    def test_framework_defaults_retain_native_shields_and_devtools_features(self):
        command = self.prepare("--no-sandbox", "--disable-extensions", "--disable-component-update",
                               "--disable-component-extensions-with-background-pages", "--password-store=basic",
                               "--disable-features=Translate,HttpsUpgrades", "--enable-features=CDPScreenshotNewSurface,PdfOopif",
                               "--remote-debugging-pipe", "--use-mock-keychain")
        self.assertFalse(any(argument in command for argument in ADAPTER.OMIT_FRAMEWORK_DEFAULTS))
        self.assertIn("--disable-features=Translate", command)
        self.assertIn("--enable-features=CDPScreenshotNewSurface,PdfOopif", command)
        self.assertIn("--remote-debugging-pipe", command)
        self.assertIn("--use-mock-keychain", command)

    def test_existing_personal_directory_outside_tmp_refused(self):
        with self.assertRaisesRegex(ValueError, "OUTSIDE_TEMPORARY_ROOT"):
            ADAPTER.prepare(str(self.binary), [f"--user-data-dir={ROOT}"])

    def test_main_errors_are_sanitized_and_never_execute(self):
        with mock.patch.object(ADAPTER.sys, "argv", ["adapter", "/missing/private-path"]), \
                mock.patch.object(ADAPTER.os, "execv") as execute, \
                mock.patch.object(ADAPTER.sys, "stderr") as stderr:
            self.assertEqual(ADAPTER.main(), 78)
            execute.assert_not_called()
            self.assertNotIn("private-path", str(stderr.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
