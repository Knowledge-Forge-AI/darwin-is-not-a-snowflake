"""Disposable-HOME fixtures for the real Kitty programs; native tools are stand-ins."""
import getpass
import hashlib
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(os.environ["KITTY_STOCK_TEST_TMP"]).resolve()
LOGS = Path(os.environ["KITTY_STOCK_TEST_LOGS"]).resolve()
BASE = "07e8a04d54d4376b577d9bd2aca481a8a4fe1c6a"


def fingerprint(root):
    result = {}
    if not root.exists() and not root.is_symlink():
        return result
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        data = os.readlink(path) if path.is_symlink() else (hashlib.sha256(path.read_bytes()).hexdigest() if stat.S_ISREG(info.st_mode) else "directory")
        result[str(path.relative_to(root))] = (info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns, data)
    return result


class KittyFixture(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(parents=True, exist_ok=True)
        LOGS.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TMP, prefix="maintenance-")
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.source = self.root / "kitty.app"
        for folder in ["MacOS", "Resources", "Frameworks"]:
            (self.source / "Contents" / folder).mkdir(parents=True)
        # Real already-installed Mach-O slices: the sign stub changes bytes while
        # LC_UUID remains stable. No compiler, package acquisition or app launch.
        shutil.copyfile("/bin/echo", self.source / "Contents/MacOS/kitty")
        (self.source / "Contents/MacOS/kitten").write_text("kitten")
        (self.source / "Contents/Resources/kitty.icns").write_text("icon")
        (self.source / "Contents/Resources/terminfo").write_text("resources-v1")
        self.set_version("1")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.calls = self.root / "calls"
        self.calls.touch()
        self.make_tools()
        self.font = self.root / "font.ttf"
        self.font.touch()
        self.stock_conf = self.root / "stock.conf"
        self.stock_conf.write_text("include kitty.conf\nshell /fixture/stock-launch\n")
        self.stock_ls = self.root / "stock.cmdline"
        self.stock_ls.write_text("--config stock-bash.conf\n")
        self.env = {"HOME": str(self.home), "TMPDIR": str(self.root), "TMP": str(self.root), "TEMP": str(self.root), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "FIXTURE_ROOT": str(self.root)}
        self.kitty = self.home / ".config/kitty"
        self.state = self.kitty / ".dinas-kitty-state"
        self.apps = self.home / "Applications"
        self.log_number = 0

    def tearDown(self):
        self.temp.cleanup()

    def set_version(self, value):
        (self.source / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": value, "CFBundleVersion": value}))

    def make_tools(self):
        body = r'''
#!/bin/bash
set -e
name=${0##*/}
printf '%s %s\n' "$name" "$*" >> "$FIXTURE_ROOT/calls"
[[ ! -e $FIXTURE_ROOT/fail-$name ]] || exit 7
case $name in
  magick) [[ $1 != identify ]] || printf '128 128\n' ;;
  iconutil)
    kind=$2
    while [[ $# -gt 0 ]]; do if [[ $1 == -o ]]; then output=$2; break; fi; shift; done
    if [[ $kind == iconset ]]; then mkdir -p "$output"; printf png > "$output/icon_128x128.png"
    else printf icns > "$output"; fi ;;
  codesign)
    if [[ $1 == --verify ]]; then [[ ! -e $FIXTURE_ROOT/fail-verify ]]; exit; fi
    [[ ! -e $FIXTURE_ROOT/fail-sign ]] || exit 8
    app=$4
    mkdir -p "$app/Contents/_CodeSignature"
    printf seal > "$app/Contents/_CodeSignature/CodeResources"
    for exe in "$app"/Contents/MacOS/kitty-*; do printf signed >> "$exe"; done
    [[ ! -e $FIXTURE_ROOT/kill-sign ]] || kill -KILL "$PPID"
    ;;
  lsregister) [[ ! -e $FIXTURE_ROOT/kill-register ]] || kill -KILL "$PPID" ;;
esac
exit 0
'''
        for name in ["iconutil", "magick", "codesign", "xattr", "lsregister"]:
            path = self.bin / name
            path.write_text(body.lstrip())
            path.chmod(0o755)

    def writer_args(self):
        cfg = ROOT / "config/kitty"
        return [str(ROOT / "modules/user-home-writer-common.sh"), getpass.getuser(), str(self.home), str(self.source),
                *[str(cfg / name) for name in ["kitty.conf", "launcher.conf.template", "launcher-bash.conf.fragment", "launcher.session.template", "launcher-info.plist.template"]],
                str(self.bin / "magick"), str(self.font), str(self.bin / "iconutil"), str(self.bin / "xattr"), str(self.bin / "codesign"), str(self.bin / "lsregister"),
                "com.operator.kitty.zsh", "com.operator.kitty.bash"]

    def execute(self, argv, expected=0):
        result = subprocess.run([str(x) for x in argv], env=self.env, capture_output=True, text=True, timeout=30)
        self.log_number += 1
        log = f"exit={result.returncode}\n{result.stdout}\n{result.stderr}"
        log = log.replace(str(self.root), "<fixture>").replace(str(ROOT), "<repo>").replace(getpass.getuser(), "<user>")
        (LOGS / f"{self.id().split('.')[-1]}-{self.log_number}.log").write_text(log)
        if expected is not None:
            self.assertEqual(result.returncode, expected, log)
        return result

    def writer(self, check=False, expected=0, script=None, args=None):
        return self.execute(["/bin/bash", script or ROOT / "modules/kitty-app-writer.sh", *(["--check"] if check else []), *(args or self.writer_args())], expected)

    def stock(self, mode="enable", check=False, expected=0):
        args = [mode, ROOT / "modules/user-home-writer-common.sh", getpass.getuser(), self.home]
        if mode == "enable":
            args.extend([self.stock_conf, self.stock_ls])
        return self.execute(["/bin/bash", ROOT / "modules/kitty-stock-activate.sh", *(["--check"] if check else []), *args], expected)

    def variants(self):
        return {name: fingerprint(path) for name, path in [("apps", self.apps), ("bash", self.kitty / "kitty-bash"), ("zsh", self.kitty / "kitty-zsh")]}

    def assert_no_mutating_calls(self):
        calls = self.calls.read_text()
        self.assertNotIn("--sign", calls)
        for name in ["iconutil", "magick", "xattr", "lsregister"]:
            self.assertNotIn(name, calls)

    def stock_receipt(self, name="stock-bash.conf"):
        return self.state / f"stock-{name}.receipt"
