"""Kill after real write/directory/cleanup boundaries, then resume original source.

Instrumentation substitutes command paths only in a disposable source copy.
The wrapper executes the original command and kills its parent after boundary N.
No product branches or recovery logic are replaced.
"""
import shutil
import json
import unittest
from kitty_stock_fixtures import ROOT, LOGS, KittyFixture


class InterruptionTests(KittyFixture):
    def instrument(self, name):
        wrapper = self.bin / "boundary"
        wrapper.write_text(r'''#!/bin/bash
set -e
command=$1; shift
"$command" "$@"
count=0
[[ ! -f $FIXTURE_ROOT/boundary-count ]] || read -r count < "$FIXTURE_ROOT/boundary-count"
count=$((count + 1))
printf '%s\n' "$count" > "$FIXTURE_ROOT/boundary-count"
printf '%s %s\n' "$command" "$*" >> "$FIXTURE_ROOT/boundaries"
if [[ ${STOP_AT-} == "$count" ]]; then kill -KILL "$PPID"; fi
''')
        wrapper.chmod(0o755)
        source = (ROOT / "modules" / name).read_text()
        commands = ["/bin/mv", "/bin/cp", "/usr/bin/touch"]
        if name == "kitty-app-writer.sh":
            commands += ["/bin/mkdir", "/bin/rm", "/bin/rmdir"]
            source = source.replace("  printf 'dinas-kitty-transaction-v1", f'  "{wrapper}" /usr/bin/printf \'dinas-kitty-transaction-v1')
        for command in commands:
            source = source.replace(command + " ", f'"{wrapper}" {command} ')
        path = self.root / name
        path.write_text(source)
        return path

    def reset_count(self):
        (self.root / "boundary-count").write_text("0\n")

    def restore(self, snapshot):
        shutil.rmtree(self.home)
        shutil.copytree(snapshot, self.home, symlinks=True)
        self.reset_count()

    def test_variant_upgrade_each_write_and_cleanup_boundary(self):
        self.writer()
        snapshot = self.root / "baseline-home"
        shutil.copytree(self.home, snapshot, symlinks=True)
        template = self.root / "launcher.conf.template"
        template.write_text((ROOT / "config/kitty/launcher.conf.template").read_text() + "# new managed template\n")
        args = self.writer_args()
        args[5] = str(template)
        self.set_version("2")
        script = self.instrument("kitty-app-writer.sh")
        self.reset_count()
        self.writer(script=script, args=args)
        boundaries = int((self.root / "boundary-count").read_text())
        self.assertGreaterEqual(boundaries, 14)
        events = (self.root / "boundaries").read_text().replace(str(self.root), "<fixture>").splitlines()
        (LOGS / "variant-write-boundaries.json").write_text(json.dumps({"boundaries": boundaries, "events": events[-boundaries:]}, indent=2) + "\n")
        for number in range(1, boundaries + 1):
            with self.subTest(boundary=number):
                self.restore(snapshot)
                self.env["STOP_AT"] = str(number)
                self.assertEqual(self.writer(script=script, args=args, expected=None).returncode, -9)
                del self.env["STOP_AT"]
                self.writer(args=args)
                self.assertEqual(self.writer(args=args).stdout.count("NOOP"), 2)
                self.assertFalse(list(self.apps.glob(".kitty-stock-maintain-*")))
                self.assertFalse(list(self.kitty.glob("kitty-*/kitty.conf.before-nix-darwin*")))

    def test_partial_retired_payload_cleanup_recovers(self):
        self.writer()
        cleanup = self.apps / ".kitty-stock-maintain-kitty-zsh.cleanup"
        cleanup.mkdir()
        (cleanup / "owner").write_text("dinas-kitty-transaction-v1\n")
        (cleanup / "previous/Contents").mkdir(parents=True)
        (cleanup / "previous/Contents/partially-removed").write_text("old payload")
        self.calls.write_text("")
        self.writer()
        self.assertFalse(cleanup.exists())
        self.assert_no_mutating_calls()

    def test_stock_enable_and_disable_each_copy_rename_boundary(self):
        self.writer()
        for name in ["stock-bash.conf", "macos-launch-services-cmdline"]:
            (self.kitty / name).write_text("original " + name)
        script = self.instrument("kitty-stock-activate.sh")
        for mode in ["enable", "disable"]:
            snapshot = self.root / (mode + "-home")
            shutil.copytree(self.home, snapshot, symlinks=True)
            args = [mode, *self.writer_args()[:3]]
            if mode == "enable":
                args.extend([str(self.stock_conf), str(self.stock_ls)])
            self.reset_count()
            self.execute(["/bin/bash", script, *args])
            boundaries = int((self.root / "boundary-count").read_text())
            self.assertGreaterEqual(boundaries, 4)
            events = (self.root / "boundaries").read_text().replace(str(self.root), "<fixture>").splitlines()
            (LOGS / ("stock-" + mode + "-boundaries.json")).write_text(json.dumps({"boundaries": boundaries, "events": events[-boundaries:]}, indent=2) + "\n")
            for number in range(1, boundaries + 1):
                with self.subTest(mode=mode, boundary=number):
                    self.restore(snapshot)
                    self.env["STOP_AT"] = str(number)
                    self.assertEqual(self.execute(["/bin/bash", script, *args], expected=None).returncode, -9)
                    del self.env["STOP_AT"]
                    self.stock(mode)
                    self.stock("disable")
                    for name in ["stock-bash.conf", "macos-launch-services-cmdline"]:
                        self.assertEqual((self.kitty / name).read_text(), "original " + name)
            self.stock()  # Supply the enabled preimage for the disable matrix.


if __name__ == "__main__":
    unittest.main(verbosity=2)
