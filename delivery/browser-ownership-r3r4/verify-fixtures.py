"""Source-only R3R4 fixtures, parses and pre-effect guards; never a native graph."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

KIT = Path(__file__).resolve().parent
CORE = KIT.parents[1]


def run(command, env):
    result = subprocess.run(["rtk", "proxy"] + command, capture_output=True,
                            text=True, timeout=60, env=env)
    if result.returncode:
        raise RuntimeError("CHECK_FAILED:" + command[0] + ":" + result.stderr[-2000:])
    return result.stdout


def all_true(value):
    if isinstance(value, dict):
        return all(all_true(item) for item in value.values())
    return value is True


def expression(scratch, lib_path):
    bindings = {"repoRoot": str(CORE), "previousRoot": str(scratch / "previous-core"),
                "ownerRoot": str(scratch / "browser-ux-r3r4"),
                "previousOwnerRoot": str(scratch / "browser-ux-r3r3"),
                "compositionRoot": str(scratch / "composition-r3r4"),
                "previousCompositionRoot": str(scratch / "composition-r3r3"),
                "configurationFile": str(scratch / "configuration.json"), "libPath": str(lib_path)}
    attrs = " ".join(key + " = /. + " + json.dumps(value) + ";" for key, value in bindings.items())
    return "import " + json.dumps(str(KIT / "source-fixture.nix")) + " { " + attrs + " }"


def verify_guards(expr, scratch, env):
    proxy = shutil.which("rtk", path=env["PATH"])
    if proxy is None:
        raise RuntimeError("RTK_REQUIRED")
    scripts = json.loads(run(["nix-instantiate", "--eval", "--strict", "--json", "--expr",
                              expr.replace(" }", " emitScripts = true; }", 1)], env))
    directory = scratch / "guards"
    directory.mkdir(exist_ok=True)
    results = []
    for name, text in scripts.items():
        script = directory / (name + ".sh")
        script.write_text(text)
        result = subprocess.run([proxy, "proxy", "/bin/bash", "--noprofile", "--norc", str(script)],
                                capture_output=True, text=True, timeout=10,
                                env={"PATH": "/fixture/no-effects", "HOME": "/fixture/no-profile"})
        if result.returncode != 78:
            raise RuntimeError("PRE_EFFECT_GUARD_FAILED:" + name)
        results.append({"script": name, "exitCode": result.returncode})
    return results


def verify_contracts(args, env):
    expr = expression(args.scratch, args.lib_path)
    connected = json.loads(run(["nix-instantiate", "--eval", "--strict", "--json", "--expr", expr], env))
    if not all_true(connected["checks"]):
        raise RuntimeError("CONNECTED_SOURCE_CONTRACT_FAILED")
    lib = json.dumps(str(args.lib_path))
    core_tests = "let libPath = /. + " + lib + "; lib = import libPath; in lib.runTests (import "
    core_tests += json.dumps(str(CORE / "src/test/unit/nix/core-browser-ownership.unit.test.nix"))
    core_tests += " { repoRoot = /. + " + json.dumps(str(CORE)) + '; system = "aarch64-darwin"; inherit libPath; })'
    if json.loads(run(["nix-instantiate", "--eval", "--strict", "--json", "--expr", core_tests], env)) != []:
        raise RuntimeError("CORE_OWNERSHIP_TEST_FAILED")
    for label, test in (("browser-ux-r3r4", "roles-r3.nix"), ("composition-r3r4", "browser-role-r3.nix")):
        subject = args.scratch / label / "tests" / test
        run(["nix-instantiate", "--eval", "--strict", "--json", "--expr",
             "import " + json.dumps(str(subject)) + " { lib = import (/. + " + lib + "); }"], env)
    for test in ("roles-r3.test.py", "delivery-rollout.test.py"):
        run(["python3", str(args.scratch / "browser-ux-r3r4/tests" / test)], env)
    return connected


def verify_syntax(args, env):
    parses = []
    for label in ("browser-ux", "composition"):
        manifest = json.loads((KIT / (label + "-manifest.json")).read_text())
        for row in manifest["paths"]:
            path = args.scratch / (label + "-r3r4") / row["path"]
            if path.suffix == ".nix":
                run(["nix-instantiate", "--parse", str(path)], env)
                parses.append(label + ":" + row["path"])
            elif path.suffix == ".py":
                ast.parse(path.read_text(), filename=row["path"])
    for path in KIT.iterdir():
        if path.suffix == ".py":
            ast.parse(path.read_text(), filename=path.name)
        elif path.suffix == ".nix":
            run(["nix-instantiate", "--parse", str(path)], env)
    for relative in ("modules/browsers.nix", "src/test/unit/nix/core-browser-ownership.unit.test.nix"):
        run(["nix-instantiate", "--parse", str(CORE / relative)], env)
    return parses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--lib-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    configuration = json.loads((args.scratch / "configuration.json").read_text())
    encoded = json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
    expected = json.loads((KIT / "CONFIGURATION-BINDING.json").read_text())["canonicalConfigurationSha256"]
    if hashlib.sha256(encoded).hexdigest() != expected:
        raise RuntimeError("BROWSER_CONFIGURATION_DRIFT_HELD")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(args.scratch))
    connected = verify_contracts(args, env)
    parses = verify_syntax(args, env)
    guards = verify_guards(expression(args.scratch, args.lib_path), args.scratch, env)
    args.output.write_text(json.dumps({"status": "PASS", "connectedSource": connected["checks"],
        "coreOwnershipTests": "PASS", "ownerAndCompositionNixContracts": "PASS",
        "ownerPythonHoldAndRolloutTests": "PASS", "nixParses": parses,
        "pythonSyntax": "PASS", "preEffectGuards": guards, "nativeGraphQualified": False}, indent=2) + "\n")
    print(json.dumps({"status": "PASS", "nativeGraphQualified": False, "guards": len(guards)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
