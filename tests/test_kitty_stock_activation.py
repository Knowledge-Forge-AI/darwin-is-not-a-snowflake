"""Preservation and interruption fixtures for the real stock adapter/orchestrator."""
import shutil
import hashlib
import re
import shlex
import unittest
from kitty_stock_fixtures import ROOT, KittyFixture, fingerprint


class StockTests(KittyFixture):
    def setUp(self):
        super().setUp()
        self.writer()
        self.calls.write_text("")

    def test_enable_reenable_disable_redisable_and_enable(self):
        before = self.variants()
        self.assertEqual(self.stock(check=True).stdout.strip(), "PENDING")
        self.stock()
        full = fingerprint(self.home)
        self.stock()
        self.assertEqual(full, fingerprint(self.home))
        self.assertEqual(self.stock(check=True).stdout.strip(), "INSTALLED")
        self.stock("disable")
        full = fingerprint(self.home)
        self.stock("disable")
        self.assertEqual(full, fingerprint(self.home))
        self.assertFalse((self.kitty / "stock-bash.conf").exists())
        self.stock()
        self.assertEqual(before, self.variants())
        self.assert_no_mutating_calls()

    def test_preimage_restored_backup_kept_and_cycles_do_not_duplicate(self):
        dest = self.kitty / "stock-bash.conf"
        dest.write_text("original\n")
        self.stock()
        backups = sorted(self.kitty.glob("*.before-nix-darwin*"))
        self.stock("disable")
        self.assertEqual(dest.read_text(), "original\n")
        self.assertEqual(backups[0].read_text(), "original\n")
        self.stock()
        self.stock("disable")
        self.assertEqual(backups, sorted(self.kitty.glob("*.before-nix-darwin*")))

    def test_user_edits_preserved_on_enable_and_relinquished_on_disable(self):
        self.stock()
        dest = self.kitty / "stock-bash.conf"
        dest.write_text("user-one\n")
        self.stock()
        self.assertEqual(next(self.kitty.glob("stock-bash.conf.before-nix-darwin*")).read_text(), "user-one\n")
        dest.write_text("user-two\n")
        self.stock("disable")
        self.assertEqual(dest.read_text(), "user-two\n")
        before = fingerprint(self.home)
        self.stock("disable")
        self.assertEqual(before, fingerprint(self.home))

    def test_managed_update_does_not_claim_backup(self):
        self.stock()
        self.stock_conf.write_text("new managed content\n")
        self.stock()
        self.assertEqual(list(self.kitty.glob("*.before-nix-darwin*")), [])
        self.assertEqual((self.kitty / "stock-bash.conf").read_text(), "new managed content\n")

    def test_missing_stamp_disable_preserves_unowned_and_identical_adoption(self):
        dest = self.kitty / "stock-bash.conf"
        dest.write_bytes(self.stock_conf.read_bytes())
        before = fingerprint(self.home)
        self.stock("disable")
        self.assertEqual(before, fingerprint(self.home))
        self.stock()
        self.stock("disable")
        self.assertEqual(dest.read_bytes(), self.stock_conf.read_bytes())

    def test_corrupt_symlink_receipt_refuses_before_other_file_changes(self):
        self.stock()
        record = self.stock_receipt("macos-launch-services-cmdline")
        record.write_text("bad\n")
        for mode in ["enable", "disable"]:
            before = fingerprint(self.home)
            self.stock(mode, expected=1)
            self.assertEqual(before, fingerprint(self.home))
        record.unlink()
        record.symlink_to(self.stock_conf)
        self.stock(expected=1)
        self.assertTrue(record.is_symlink())

    def test_symlink_preimage_and_directory_conflict(self):
        dest = self.kitty / "stock-bash.conf"
        dest.symlink_to(self.stock_conf)
        self.stock()
        self.stock("disable")
        self.assertTrue(dest.is_symlink())
        self.assertTrue(next(self.kitty.glob("stock-bash.conf.before-nix-darwin*")).is_symlink())
        dest.unlink()
        dest.mkdir()
        before = fingerprint(self.home)
        self.stock(expected=1)
        self.assertEqual(before, fingerprint(self.home))

    def test_edited_backup_disable_retains_owned_file(self):
        (self.kitty / "stock-bash.conf").write_text("original")
        self.stock()
        next(self.kitty.glob("stock-bash.conf.before-nix-darwin*")).write_text("edited backup")
        self.stock("disable", expected=1)
        self.assertEqual((self.kitty / "stock-bash.conf").read_bytes(), self.stock_conf.read_bytes())

    def test_prepared_enable_before_and_after_preimage_move(self):
        dest = self.kitty / "stock-bash.conf"
        dest.write_text("original")
        self.stock()
        record = self.stock_receipt()
        backup = next(self.kitty.glob("stock-bash.conf.before-nix-darwin*"))
        record.write_text(record.read_text().replace("state=active", "state=prepared"))
        dest.unlink()
        self.stock()
        self.assertEqual(dest.read_bytes(), self.stock_conf.read_bytes())
        self.stock("disable")
        record.write_text(record.read_text().replace("state=disabled", "state=prepared"))
        backup.unlink()
        self.stock()
        self.assertEqual(backup.read_text(), "original")

    def test_disable_interruption_after_remove_or_restore(self):
        dest = self.kitty / "stock-bash.conf"
        dest.write_text("original")
        self.stock()
        dest.unlink()
        self.stock("disable")
        self.assertEqual(dest.read_text(), "original")
        record = self.stock_receipt()
        record.write_text(record.read_text().replace("state=disabled", "state=active"))
        before = fingerprint(dest)
        self.stock("disable")
        self.assertEqual(before, fingerprint(dest))

    def test_legacy_stamp_is_not_adoption_authority(self):
        legacy = self.kitty / ".kitty-stock-activation-stamp"
        legacy.write_text("legacy")
        self.stock()
        self.stock("disable")
        self.assertEqual(legacy.read_text(), "legacy")

    def test_orchestrator_stock_failure_continues_writer_failure_aborts(self):
        self.stock_receipt().write_text("bad\n")
        args = [ROOT / "modules/kitty-app-writer.sh", ROOT / "modules/kitty-stock-activate.sh", "enable", self.stock_conf, self.stock_ls, *self.writer_args()]
        result = self.execute(["/bin/bash", ROOT / "modules/kitty-stock-maintain.sh", *args])
        self.assertIn("required combined rollout remains incomplete", result.stderr)
        wrapper = self.root / "activation.sh"
        module = (ROOT / "modules/kitty.nix").read_text()
        body = re.search(r"system\.activationScripts\.postActivation\.text = ''(.*?)\n\s*'';", module, re.S)[1]
        # Execute the real module's generated body with only the privilege
        # transition removed and Nix placeholders replaced by fixture inputs.
        body = body.replace('/usr/bin/sudo -n -H -u "$target_user" --', '')
        replacements = {
            "lib.escapeShellArg primaryUser": shlex.quote(self.writer_args()[1]),
            "lib.escapeShellArg userHome": shlex.quote(str(self.home)),
            "pkgs.bash": "/", "./kitty-stock-maintain.sh": str(ROOT / "modules/kitty-stock-maintain.sh"),
            "./kitty-app-writer.sh": str(ROOT / "modules/kitty-app-writer.sh"),
            "./kitty-stock-activate.sh": str(ROOT / "modules/kitty-stock-activate.sh"),
            "stockMode": "enable", "stockActivationArgs": shlex.join([str(self.stock_conf), str(self.stock_ls)]),
            "./user-home-writer-common.sh": self.writer_args()[0],
            "kittyConfig": self.writer_args()[4], "kittyLauncherConfigTemplate": self.writer_args()[5],
            "kittyLauncherBashConfigFragment": self.writer_args()[6], "kittyLauncherSessionTemplate": self.writer_args()[7],
            "kittyLauncherPlistTemplate": self.writer_args()[8], "pkgs.imagemagick": str(self.bin.parent),
        }
        for key, value in replacements.items():
            body = body.replace("${" + key + "}", value)
        body = body.replace("//bin/bash", "/bin/bash").replace("/Applications/kitty.app", shlex.quote(str(self.source)))
        body = re.sub(r"\$\{pkgs.nerd-fonts.jetbrains-mono\}/\S+", shlex.quote(str(self.font)), body)
        body = body.replace(str(self.bin.parent) + "/bin/magick", str(self.bin / "magick"))
        for name in ["iconutil", "xattr", "codesign"]:
            body = body.replace("/usr/bin/" + name, str(self.bin / name))
        body = body.replace("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister", str(self.bin / "lsregister"))
        self.assertNotIn("${", body)
        wrapper.write_text(body + '\nprintf later > "$HOME/later"\n')
        self.execute(["/bin/bash", wrapper])
        self.assertTrue((self.home / "later").exists())
        (self.home / "later").unlink()
        for failure in [self.root / "fail-verify", self.kitty / "kitty-bash/macos-launch-services-cmdline"]:
            failure.write_text("fixture conflict\n")
            before = fingerprint(self.home)
            self.execute(["/bin/bash", wrapper], expected=1)
            self.assertEqual(before, fingerprint(self.home))
            self.assertFalse((self.home / "later").exists())
            failure.unlink()
        for record in self.state.glob("variant-*.receipt"):
            record.unlink()
        shutil.copyfile("/bin/cat", self.source / "Contents/MacOS/kitty")
        self.set_version("2")
        result = self.execute(["/bin/bash", wrapper])
        self.assertIn("baseline provenance differs", result.stdout)
        self.assertTrue((self.home / "later").exists())
        (self.home / "later").unlink()
        (self.source / "Contents/MacOS/kitty").unlink()
        self.execute(["/bin/bash", wrapper], expected=1)
        self.assertFalse((self.home / "later").exists())

    def test_interrupted_managed_update_recognizes_previous_hash(self):
        self.stock()
        record = self.stock_receipt()
        old = hashlib.sha256(self.stock_conf.read_bytes()).hexdigest()
        self.stock_conf.write_text("managed-v2\n")
        new = hashlib.sha256(self.stock_conf.read_bytes()).hexdigest()
        record.write_text(record.read_text().replace("state=active", "state=prepared")
                          .replace("installed=" + old, "installed=" + new)
                          .replace("previous=absent", "previous=" + old))
        self.stock()
        self.assertEqual((self.kitty / "stock-bash.conf").read_bytes(), self.stock_conf.read_bytes())
        self.assertFalse(list(self.kitty.glob("*.before-nix-darwin*")))

    def test_missing_owned_artifact_repairs_without_variants_or_backup(self):
        self.stock()
        before = self.variants()
        (self.kitty / "macos-launch-services-cmdline").unlink()
        self.stock()
        self.assertEqual(before, self.variants())
        self.assertFalse(list(self.kitty.glob("*.before-nix-darwin*")))

    def test_disable_preflights_both_preimages(self):
        (self.kitty / "stock-bash.conf").write_text("original")
        self.stock()
        next(self.kitty.glob("*.before-nix-darwin*")).unlink()
        before = fingerprint(self.home)
        self.stock("disable", expected=1)
        self.assertEqual(before, fingerprint(self.home))

    def test_adopted_identical_preimage_survives_managed_update(self):
        dest = self.kitty / "stock-bash.conf"
        original = self.stock_conf.read_bytes()
        dest.write_bytes(original)
        self.stock()
        self.stock_conf.write_text("updated managed content\n")
        self.stock()
        self.stock("disable")
        self.assertEqual(dest.read_bytes(), original)
        self.assertEqual(next(self.kitty.glob("*.before-nix-darwin*")).read_bytes(), original)

    def test_full_orchestrator_toggle_and_stock_changes_keep_variants_noop(self):
        before = self.variants()
        for mode in ["enable", "enable", "disable", "disable", "enable"]:
            args = [ROOT / "modules/kitty-app-writer.sh", ROOT / "modules/kitty-stock-activate.sh", mode,
                    self.stock_conf, self.stock_ls, *self.writer_args()]
            self.execute(["/bin/bash", ROOT / "modules/kitty-stock-maintain.sh", *args])
            self.assertEqual(before, self.variants())
        self.stock_conf.write_text("changed stock setting\n")
        args = [ROOT / "modules/kitty-app-writer.sh", ROOT / "modules/kitty-stock-activate.sh", "enable",
                self.stock_conf, self.stock_ls, *self.writer_args()]
        self.execute(["/bin/bash", ROOT / "modules/kitty-stock-maintain.sh", *args])
        self.assertEqual(before, self.variants())
        self.assert_no_mutating_calls()



if __name__ == "__main__":
    unittest.main(verbosity=2)
