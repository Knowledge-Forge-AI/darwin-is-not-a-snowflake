"""Content/provenance and crash-state tests of the actual variant writer."""
import os
import shutil
import subprocess
import unittest
from kitty_stock_fixtures import BASE, ROOT, KittyFixture, fingerprint


class VariantTests(KittyFixture):
    def test_build_and_unchanged_input_noop(self):
        self.writer()
        before = self.variants()
        receipt = fingerprint(self.state)
        self.calls.write_text("")
        result = self.writer()
        self.assertEqual(result.stdout.count("NOOP"), 2)
        self.assertEqual(before, self.variants())
        self.assertEqual(receipt, fingerprint(self.state))
        self.assert_no_mutating_calls()

    def test_migrate_base_writer_signed_bytes_and_unverified_signature(self):
        base = subprocess.check_output(["git", "show", f"{BASE}:modules/kitty-app-writer.sh"], cwd=ROOT, text=True)
        # Exactly one temp-root substitution; original baseline logic otherwise.
        base = base.replace('/private/tmp/$executable_name.XXXXXX', str(self.root) + '/$executable_name.XXXXXX')
        script = self.root / "base-writer.sh"
        script.write_text(base)
        self.writer(script=script)
        before = self.variants()
        (self.root / "fail-verify").touch()
        self.calls.write_text("")
        self.assertEqual(self.writer().stdout.count("ADOPT"), 2)
        self.assertEqual(before, self.variants())
        self.assert_no_mutating_calls()
        self.calls.write_text("")
        self.writer()
        self.assert_no_mutating_calls()
        self.assertEqual(before, self.variants())

    def test_absent_corrupt_receipts_and_ctime_do_not_force_resign(self):
        self.writer()
        (self.state / "variant-kitty-zsh.receipt").unlink()
        (self.state / "variant-kitty-bash.receipt").write_text("corrupt\n")
        os.chmod(self.source / "Contents/MacOS/kitty", 0o700)
        before = self.variants()
        self.calls.write_text("")
        self.assertEqual(self.writer().stdout.count("ADOPT"), 2)
        self.assertEqual(before, self.variants())
        self.assert_no_mutating_calls()

    def test_unused_font_helper_and_tool_paths_do_not_churn(self):
        self.writer()
        args = self.writer_args()
        helper = self.root / "common.sh"
        helper.write_text((ROOT / "modules/user-home-writer-common.sh").read_text() + "\n# relocated\n")
        args[0] = str(helper)
        self.font.write_text("new font")
        tools = self.root / "other-tools"
        shutil.copytree(self.bin, tools)
        for index in [9, 11, 12, 13, 14]:
            args[index] = str(tools / self.writer_args()[index].split('/')[-1])
        before = self.variants()
        self.calls.write_text("")
        self.writer(args=args)
        self.assertEqual(before, self.variants())
        self.assert_no_mutating_calls()

    def test_cask_version_delta_requires_normal_maintenance(self):
        self.writer()
        self.set_version("2")
        (self.source / "Contents/Resources/new-data").write_text("v2")
        result = self.writer()
        self.assertEqual(result.stdout.count("REBUILD (version 2/2;"), 2)
        for exe, title in [("kitty-zsh", "Kitty Zsh"), ("kitty-bash", "Kitty Bash")]:
            app = self.apps / (title + ".app")
            self.assertTrue((app / "Contents/Resources/new-data").is_symlink())
            self.assertIn("com.operator." + exe.replace('-', '.'), (app / "Contents/Info.plist").read_text())
        self.calls.write_text("")
        self.writer()
        self.assert_no_mutating_calls()

    def test_baseline_cask_upgrade_without_receipt_rebuilds_current_code(self):
        base = subprocess.check_output(["git", "show", f"{BASE}:modules/kitty-app-writer.sh"], cwd=ROOT, text=True)
        script = self.root / "base-writer.sh"
        script.write_text(base.replace('/private/tmp/$executable_name.XXXXXX', str(self.root) + '/$executable_name.XXXXXX'))
        self.writer(script=script)
        self.state.mkdir()
        # Neither receipt proves the old executable: absent Zsh, corrupt Bash.
        (self.state / "variant-kitty-bash.receipt").write_text("corrupt\n")
        shutil.copyfile("/bin/cat", self.source / "Contents/MacOS/kitty")
        self.set_version("2")  # Resource names remain unchanged across upgrade.
        self.assertEqual(self.writer(check=True).stdout.count("REBUILD"), 2)
        result = self.writer()
        self.assertEqual(result.stdout.count("REBUILD"), 2)
        for name, title in [("kitty-zsh", "Kitty Zsh"), ("kitty-bash", "Kitty Bash")]:
            exe = self.apps / (title + ".app") / "Contents/MacOS" / name
            self.assertEqual(exe.read_bytes(), (self.source / "Contents/MacOS/kitty").read_bytes() + b"signed")
        self.calls.write_text("")
        self.writer()
        self.assert_no_mutating_calls()

    def test_missing_artifacts_repair(self):
        self.writer()
        for relative in ["Contents/Resources/kitty-zsh.icns", "Contents/MacOS/kitten", "Contents/Resources/terminfo"]:
            (self.apps / "Kitty Zsh.app" / relative).unlink()
            self.assertIn("kitty-zsh: REBUILD", self.writer().stdout)

    def test_empty_icon_is_drift_and_repaired(self):
        self.writer()
        icon = self.apps / "Kitty Zsh.app/Contents/Resources/kitty-zsh.icns"
        icon.write_bytes(b"")
        self.assertIn("kitty-zsh: REBUILD", self.writer().stdout)
        self.assertTrue(icon.read_bytes())

    def test_sign_failure_preserves_previous_then_recovers(self):
        self.writer()
        before = fingerprint(self.apps / "Kitty Zsh.app")
        self.set_version("2")
        (self.root / "fail-sign").touch()
        self.writer(expected=8)
        self.assertEqual(before, fingerprint(self.apps / "Kitty Zsh.app"))
        (self.root / "fail-sign").unlink()
        self.writer()
        self.assertFalse((self.apps / ".kitty-stock-maintain-kitty-zsh").exists())

    def test_signature_regression_is_explicit_and_preserved(self):
        self.writer()
        before = self.variants()
        (self.root / "fail-verify").touch()
        result = self.writer(expected=1)
        self.assertIn("signature regressed", result.stderr)
        self.assertEqual(before, self.variants())

    def test_launchservices_selection_conflict_refuses_before_mutation(self):
        self.writer()
        conflict = self.kitty / "kitty-bash/macos-launch-services-cmdline"
        conflict.write_text("user selection\n")
        before = fingerprint(self.home)
        for check in [True, False]:
            self.assertIn("LaunchServices selection conflict", self.writer(check=check, expected=1).stderr)
            self.assertEqual(before, fingerprint(self.home))
        conflict.rename(conflict.with_name("selection.saved"))
        self.calls.write_text("")
        self.writer()
        self.assert_no_mutating_calls()

    def test_empty_transaction_creation_and_retired_cleanup_recover(self):
        self.writer()
        transaction = self.apps / ".kitty-stock-maintain-kitty-zsh"
        for path in [transaction, transaction.with_name(transaction.name + ".cleanup")]:
            path.mkdir()
            before = fingerprint(self.home)
            self.writer(check=True)
            self.assertEqual(before, fingerprint(self.home))
            self.writer()
            self.assertFalse(path.exists())
        transaction.mkdir()
        (transaction / "owner").write_text("dinas-kitty-transaction-v1\n")
        self.set_version("2")
        (self.source / "Contents/Resources/new-data").write_text("v2")
        self.writer()  # Kill after marker publication, before creating next.
        self.assertFalse(transaction.exists())

    def test_unmarked_nonempty_cleanup_is_retained_without_mutation(self):
        self.writer()
        cleanup = self.apps / ".kitty-stock-maintain-kitty-zsh.cleanup"
        cleanup.mkdir()
        (cleanup / "previous").mkdir()
        before = fingerprint(self.home)
        self.writer(expected=1)
        self.assertEqual(before, fingerprint(self.home))

    def test_register_failure_retries_only_register(self):
        (self.root / "fail-lsregister").touch()
        self.writer(expected=7)
        before = fingerprint(self.apps / "Kitty Zsh.app")
        (self.root / "fail-lsregister").unlink()
        self.calls.write_text("")
        self.writer()
        self.assertEqual(before, fingerprint(self.apps / "Kitty Zsh.app"))
        # Bash is a first build; Zsh is register-only, once each.
        self.assertEqual(self.calls.read_text().count("codesign --force"), 1)
        self.assertEqual(self.calls.read_text().count("lsregister -f"), 2)

    def test_killed_after_sign_and_during_registration(self):
        for stop in ["kill-sign", "kill-register"]:
            with self.subTest(stop=stop):
                (self.root / stop).touch()
                result = self.writer(expected=None)
                self.assertEqual(result.returncode, -9)
                (self.root / stop).unlink()
                self.writer()
                self.calls.write_text("")
                self.writer()
                self.assert_no_mutating_calls()
                self.set_version(stop)

    def test_restore_previous_after_first_rename_interruption(self):
        self.writer()
        app = self.apps / "Kitty Zsh.app"
        transaction = self.apps / ".kitty-stock-maintain-kitty-zsh"
        transaction.mkdir()
        (transaction / "owner").write_text("dinas-kitty-transaction-v1\n")
        app.rename(transaction / "previous")
        (transaction / "next").mkdir()
        self.writer()
        self.assertTrue(app.is_dir())
        self.assertFalse(transaction.exists())

    def test_publish_receipt_after_second_rename_interruption(self):
        (self.root / "fail-lsregister").touch()
        self.writer(expected=7)
        transaction = self.apps / ".kitty-stock-maintain-kitty-zsh"
        record = self.state / "variant-kitty-zsh.receipt"
        record.rename(transaction / "receipt.next")
        self.writer(expected=7)
        self.assertTrue(record.is_file())
        (self.root / "fail-lsregister").unlink()
        self.writer()

    def test_preflight_all_inputs_before_mutation(self):
        (self.home / "Applications").symlink_to(self.root / "missing")
        before = fingerprint(self.home)
        self.writer(expected=1)
        self.assertEqual(before, fingerprint(self.home))

    def test_check_is_read_only_and_config_edits_are_preserved(self):
        self.writer(check=True)
        self.assertEqual(list(self.home.iterdir()), [])
        self.writer()
        cfg = self.kitty / "kitty-bash/kitty.conf"
        cfg.write_text("user-edit\n")
        self.writer()
        self.assertEqual(next(cfg.parent.glob("kitty.conf.before-nix-darwin*")).read_text(), "user-edit\n")
        before = fingerprint(self.home)
        self.writer(check=True)
        self.assertEqual(before, fingerprint(self.home))

    def test_wrong_identity_and_external_symlink_preserved(self):
        self.writer()
        app = self.apps / "Kitty Zsh.app"
        plist = app / "Contents/Info.plist"
        plist.write_text(plist.read_text().replace("com.operator.kitty.zsh", "com.example.foreign"))
        self.writer()
        self.assertTrue(list(self.apps.glob("Kitty Zsh.app.before-nix-darwin*")))
        shutil.rmtree(app)
        sentinel = self.root / "sentinel"
        sentinel.mkdir()
        (sentinel / "keep").write_text("keep")
        app.symlink_to(sentinel)
        before = fingerprint(sentinel)
        self.writer()
        self.assertEqual(before, fingerprint(sentinel))

    def test_preflight_second_variant_and_missing_source_inputs(self):
        self.writer()
        (self.state / "variant-kitty-bash.receipt").unlink()
        (self.state / "variant-kitty-bash.receipt").symlink_to(self.font)
        before = fingerprint(self.home)
        self.writer(expected=1)
        self.assertEqual(before, fingerprint(self.home))
        (self.state / "variant-kitty-bash.receipt").unlink()
        (self.source / "Contents/MacOS/kitten").unlink()
        before = fingerprint(self.home)
        self.writer(expected=1)
        self.assertEqual(before, fingerprint(self.home))

    def test_unknown_transaction_is_retained_without_mutation(self):
        self.writer()
        transaction = self.apps / ".kitty-stock-maintain-kitty-zsh"
        transaction.mkdir()
        (transaction / "owner").write_text("dinas-kitty-transaction-v1\n")
        (transaction / "private-unexpected").write_text("preserve")
        before = fingerprint(self.home)
        self.writer(expected=1)
        self.assertEqual(before, fingerprint(self.home))

    def test_noop_does_not_need_unused_rendering_tools(self):
        self.writer()
        self.font.unlink()
        for name in ["magick", "iconutil", "xattr", "lsregister"]:
            (self.bin / name).unlink()
        before = self.variants()
        self.writer()
        self.assertEqual(before, self.variants())

    def test_interrupted_state_conflict_preflights_before_shared_link_repair(self):
        self.writer()
        transaction = self.apps / ".kitty-stock-maintain-kitty-zsh"
        transaction.mkdir()
        (transaction / "owner").write_text("dinas-kitty-transaction-v1\n")
        (transaction / "receipt.next").write_text("corrupt\n")
        shared = self.kitty / "kitty.conf"
        shared.unlink()
        shared.symlink_to(self.font)
        before = fingerprint(self.home)
        self.writer(expected=1)
        self.assertEqual(before, fingerprint(self.home))



if __name__ == "__main__":
    unittest.main(verbosity=2)
