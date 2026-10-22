"""Reproduce scoped source checks in an empty scratch directory, without host effects."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile


def run(command, diagnostics=None):
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode:
        if diagnostics:
            diagnostics.write_text(result.stderr)
        raise RuntimeError(f"{command[0]} failed")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--browser-ux", type=Path, required=True)
    parser.add_argument("--composition", type=Path, required=True)
    parser.add_argument("--configuration", type=Path)
    args = parser.parse_args()
    scratch = args.scratch.resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    if any(scratch.iterdir()):
        raise RuntimeError("Use an empty task scratch directory")
    core = Path(__file__).resolve().parents[2]
    kit = Path(__file__).resolve().parent
    original = core / "delivery/browser-ownership-r3"
    records = []
    for name, source, patch in (("browser-ux", args.browser_ux, original / "browser-ux.patch"),
                                ("composition", args.composition, kit / "composition.patch")):
        destination = scratch / name
        destination.mkdir()
        paths = run(["git", "-C", str(source), "ls-files", "-co", "--exclude-standard", "-z"]).split("\0")
        for relative in set(filter(None, paths)):
            file = source / relative
            if file.is_file():
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, target)
        run(["git", "-C", str(destination), "apply", "--check", str(patch)])
        run(["git", "-C", str(destination), "apply", str(patch)])
    baseline = scratch / "baseline-core"
    paths = ["modules/browsers.nix", "modules/desktop-apps.nix", "lib/core-application-inventory.nix", "lib/application-claims.nix"]
    paths += run(["git", "-C", str(core), "ls-tree", "-r", "--name-only", "HEAD", "config/browser/"]).splitlines()
    for relative in paths:
        target = baseline / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(run(["git", "-C", str(core), "show", "HEAD:" + relative]))
    before = scratch / "before-core"
    shutil.copytree(baseline, before)
    with zipfile.ZipFile(kit / "preserved/r3-before-repair.zip") as archive:
        # This package contains only the explicit product preimage inventory.
        archive.extractall(before)
    for record in json.loads((original / "CANDIDATE-SOURCE.json").read_text())["records"]:
        if record["owner"] != "core":
            continue
        existing = before / record["path"]
        if existing.exists() and hashlib.sha256(existing.read_bytes()).hexdigest() == record["sha256"]:
            continue
        data = (core / record["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise RuntimeError("Retained R3 preimage is unavailable")
        target = before / record["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for label, source in (("current", core), ("before", before)):
        text = (source / "flake.nix").read_text()
        start = text.index("      programs.browserBaseline.corePolicyOwnership =")
        end = text.index("      networking.hostName =", start)
        literal = re.search(r"^    browserRolesR3Selected = (true|false);$", text, re.M)
        selection = literal.group(0) if literal else ""
        (scratch / f"{label}-constructor.nix").write_text(
            "hostName: { config, ... }: let\n" + selection + "\nin {\n" + text[start:end] + "}\n")
    params = {"repoRoot": str(core), "baselineRoot": str(baseline),
              "browserUxRoot": str(scratch / "browser-ux"), "liveBrowserUxRoot": str(args.browser_ux.resolve()),
              "compositionRoot": str(scratch / "composition"), "liveCompositionRoot": str(args.composition.resolve()),
              "constructorFile": str(scratch / "current-constructor.nix"), "libPath": str(args.library.resolve())}

    def evaluate(values):
        command = ["nix-instantiate", "--eval", "--strict", "--json", str(kit / "source-fixture.nix")]
        for key, value in values.items():
            command += ["--arg", key, value]
        return json.loads(run(command, scratch / "evaluation.stderr"))

    cases = [("default-controls", params)]
    if args.configuration:
        import tomllib
        control = scratch / "browser-controls.json"
        control.write_text(json.dumps(tomllib.loads(args.configuration.read_text())))
        cases.append(("actual-control-projection", {**params, "configurationFile": str(control)}))
    for label, values in cases:
        value = evaluate(values)
        checks = list(value["activeChecks"].values()) + [v for host in value["checks"].values() for v in host.values()]
        for result in value["inventoryChecks"].values():
            checks.extend(result.values() if isinstance(result, dict) else [result])
        if not all(checks):
            raise RuntimeError("Connected source contract failed")
        records.append({"check": label, "status": "PASS", "checks": len(checks), "readback": value})
    old = evaluate({**params, "repoRoot": str(before), "constructorFile": str(scratch / "before-constructor.nix")})
    if old["checks"]["target"]["assertionsPass"] or old["checks"]["target"]["inactive"]:
        raise RuntimeError("Prior hold regression was not reproduced")
    records.append({"check": "prior-live-source-defect-reproduced", "status": "PASS"})
    for name, text in evaluate({**params, "emitScripts": "true"}).items():
        file = scratch / f"{name}.sh"
        file.write_text(text)
        run(["bash", "-n", str(file)])
        if "exit 78" not in text:
            raise RuntimeError("Missing active guard")
        prefix = text.split("exit 78", 1)[0] + "exit 78\n"
        if any(verb in prefix for verb in ("install -", "rm -", "defaults write", "exec ")):
            raise RuntimeError("Effect precedes guard")
        sentinel = scratch / f"{name}-effect"
        for flags in ({}, {"R3_QUALIFIED": "1", "BROWSER_R3_RELEASE": "1", "NIX_BROWSER_R3_SELECTED": "1"}):
            result = subprocess.run(["bash", "-c", prefix + 'touch "$1"\n', "guard", str(sentinel)],
                                    capture_output=True, env={**os.environ, **flags}, timeout=5)
            if result.returncode != 78 or sentinel.exists():
                raise RuntimeError("Active guard was bypassed")
        records.append({"check": "pre-effect-" + name, "status": "PASS", "exitCode": 78,
                        "environmentVariants": 2, "sentinelCreated": False})
    (scratch / "source-results.json").write_text(json.dumps({"records": records, "nativeGraphQualified": False}, indent=2) + "\n")
    print(json.dumps({"status": "PASS", "records": len(records), "nativeGraphQualified": False}))


if __name__ == "__main__":
    main()
