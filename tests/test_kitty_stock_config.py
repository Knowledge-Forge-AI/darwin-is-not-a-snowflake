"""Stock configuration seams; the resolver is a model, not Kitty's parser."""
import hashlib
import json
import os
import re
import shlex
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(os.environ["KITTY_STOCK_TEST_LOGS"]).parent
BASE = "07e8a04d54d4376b577d9bd2aca481a8a4fe1c6a"


def effective(text, includes):
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(" ")
        if key in ("include", "globinclude"):
            if value in includes:
                result.update(effective(includes[value], includes))
            elif key == "include":
                raise AssertionError("missing required include")
        else:
            result[key] = value
    return result


class ConfigTests(unittest.TestCase):
    def test_default_off_single_transition_and_split_owners(self):
        source = (ROOT / "modules/kitty.nix").read_text()
        self.assertIn('enable = lib.mkEnableOption', source)
        self.assertNotIn('default = true', source)
        self.assertIn('lib.optionals stock.enable [ stockShare ]', source)
        self.assertIn('${./kitty-stock-maintain.sh}', source)
        self.assertIn('${./kitty-app-writer.sh}', source)
        self.assertIn('stockMode = if stock.enable then "enable" else "disable";', source)
        body = re.search(r"system\.activationScripts\.postActivation\.text = ''(.*?)\n\s*'';", source, re.S)[1]
        self.assertEqual(body.count('/usr/bin/sudo -n -H -u "$target_user" --'), 1)
        writer = (ROOT / 'modules/kitty-app-writer.sh').read_text()
        for line in ['create_kitty_launcher "Kitty Zsh" "$zsh_bundle_id" "kitty-zsh" "/bin/zsh -l" "Z"',
                     'create_kitty_launcher "Kitty Bash" "$bash_bundle_id" "kitty-bash" "/usr/bin/login -fp $target_user /run/current-system/sw/bin/bash" "B"']:
            self.assertIn(line, writer)

    def test_variants_byte_and_effective_parity(self):
        paths = ["config/kitty/kitty.conf", "config/kitty/launcher.conf.template", "config/kitty/launcher-bash.conf.fragment", "config/kitty/launcher.session.template", "config/kitty/launcher-info.plist.template"]
        for path in paths:
            self.assertEqual((ROOT/path).read_bytes(), subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT), path)
        base = (ROOT / "config/kitty/kitty.conf").read_text()
        fragment = (ROOT / "config/kitty/launcher-bash.conf.fragment").read_text()
        template = (ROOT / "config/kitty/launcher.conf.template").read_text()
        includes = {"/Users/fixture/.config/kitty/kitty.conf": base, "kitty.conf": base}
        variants = {}
        for name, argv in [("bash", "/usr/bin/login -fp fixture /run/current-system/sw/bin/bash"), ("zsh", "/bin/zsh -l")]:
            rendered = template.replace("@USER_NAME@", "fixture").replace("@SHELL_COMMAND@", argv)
            if name == "bash":
                rendered += fragment
            resolved = effective(rendered, includes)
            self.assertEqual(resolved["shell"], argv)
            self.assertEqual(resolved["startup_session"], "launcher.session")
            self.assertNotIn("env", resolved)
            variants[name] = {"effective": resolved, "argv": shlex.split(argv), "bundleId": "com.operator.kitty." + name, "configDirectory": "~/.config/kitty/kitty-" + name, "goldenSourceParity": True}
        stock_stub = (ROOT/"config/kitty/stock-bash.conf").read_text()
        generated = (ROOT/"config/kitty/stock-kitty.conf.template").read_text().replace("@BASH_FRAGMENT@", fragment).replace("@LAUNCH@", "/fixture/share/launch")
        includes["/run/current-system/sw/share/kitty-stock-bash/kitty-stock.conf"] = generated
        stock = effective(stock_stub, includes)
        bash_appearance = variants["bash"]["effective"].copy()
        bash_appearance.pop("shell"); bash_appearance.pop("startup_session")
        stock_appearance = stock.copy(); stock_appearance.pop("shell")
        self.assertEqual(stock_appearance, bash_appearance)
        self.assertEqual(stock["shell"], "/fixture/share/launch")
        del includes["/run/current-system/sw/share/kitty-stock-bash/kitty-stock.conf"]
        self.assertEqual(effective(stock_stub, includes), effective(base, includes))
        OUTPUT.mkdir(parents=True, exist_ok=True)
        (OUTPUT / "config-parity-evidence.json").write_text(json.dumps({"method": "bounded last-wins include resolver model; not native Kitty parser", "variants": variants, "stock": stock, "rollbackModelEqualsNeutralConfig": True}, indent=2)+"\n")

    def test_all_shell_placeholders_wired_and_no_global_entry(self):
        module = (ROOT / "modules/kitty.nix").read_text()
        tokens = set()
        for path in (ROOT/"config/kitty").glob("stock-bash-*"):
            tokens.update(re.findall(r"@[A-Z_]+@", path.read_text()))
        for token in tokens:
            self.assertIn('"'+token+'"', module, token)
        self.assertNotIn("BASH_ENV", module)
        for path in ["shell/bashrc", "shell/bash_profile"]:
            text = (ROOT/path).read_text()
            self.assertNotIn("stock", text)
            self.assertNotIn("ble.sh", text)
        ls = (ROOT/"config/kitty/stock-launch-services-cmdline.template").read_text().replace("@STOCK_CONFIG@", shlex.quote("/fixture home/.config/kitty/stock-bash.conf"))
        self.assertEqual(shlex.split(ls, comments=True), ["--config", "/fixture home/.config/kitty/stock-bash.conf"])

    def test_combined_rollout_is_explicit_without_runtime_browser_coupling(self):
        source = (ROOT / "modules/kitty.nix").read_text()
        self.assertNotIn("rolesR3", source)
        self.assertNotIn("assertion = stock.enable", source)
        self.assertIn("needsCombinedBrowserBatch = true", source)
        self.assertIn("system.build.kittyStockBash", source)
        note = (ROOT / "docs/browser-rollout-kitty-stock-bash.md").read_text()
        self.assertIn("dinas.kittyStockBash.enable = kittyStockBashSelected;", note)
        self.assertNotIn("kittyStockBashSelected &&", note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
