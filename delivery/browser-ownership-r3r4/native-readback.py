"""Read-only inactive R3R4 native graph/claims; no build, browser or activation."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess

KIT = Path(__file__).resolve().parent
CORE = KIT.parents[1]
SPEC = importlib.util.spec_from_file_location("binding", KIT / "verify-source.py")
BINDING = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BINDING)


def source_errors(args):
    errors = BINDING.core_errors(CORE)
    configuration = json.loads((KIT / "CONFIGURATION-BINDING.json").read_text())
    if BINDING.digest(args.browser_config) != configuration["browserConfigurationSha256"]:
        errors.append("BROWSER_CONFIGURATION_DRIFT_HELD")
    for label, root in (("browser-ux", args.browser_ux), ("composition", args.composition)):
        manifest = json.loads((KIT / (label + "-manifest.json")).read_text())
        errors += BINDING.owner_errors(root, manifest)
    return errors


def sanitize(value, args):
    if isinstance(value, dict):
        return {k: sanitize(v, args) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v, args) for v in value]
    if isinstance(value, str):
        for path in (args.selected_flake.resolve(), args.composition.resolve(),
                     args.browser_ux.resolve(), CORE, Path.home()):
            value = value.replace(str(path), "<source-root>")
        value = re.sub(r"/Users/[^/\s]+", "<operator-home>", value)
        return re.sub(r"/private/tmp/[^\s'\"]+", "<task-scratch>", value)
    return value


def inactive_contract(value):
    return all(v["fullToplevelEvaluated"] and not v["rolesR3"] and not v["coreOwnership"]
               and not v["deploymentHeld"] and not v["failedAssertions"] and not v["guardViolations"]
               and v["chromeEnabled"] and v["firefoxEnabled"] and not v["automationExposed"]
               and v["protonVpnHistorical"] is None and v["protonVpnDelivery"] is None
               for v in (value["target"], value["predecessor"]))


def evaluate(args, name, query):
    attribute, expression = query
    command = ["rtk", "proxy", "nix", "eval", "--offline", "--no-update-lock-file",
               "--no-write-lock-file", "--impure", "--json",
               f"path:{args.selected_flake.resolve()}#{attribute}", "--apply", expression,
               "--override-input", "composition-nd", f"path:{args.composition.resolve()}",
               "--override-input", "composition-nd/core", f"path:{CORE}",
               "--override-input", "composition-nd/browser-ux_dinas", f"path:{args.browser_ux.resolve()}"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return {"check": name, "status": "ENVIRONMENT_TIMEOUT", "timeoutSeconds": 60}
    stderr = re.sub(r"\x1b\[[0-9;]*m", "", result.stderr)
    denied = "daemon-socket" in stderr and any(s in stderr for s in ("Operation not permitted", "Permission denied"))
    status = "PASS" if result.returncode == 0 else "ENVIRONMENT_DENIED" if denied else "FAIL"
    value = json.loads(result.stdout) if result.returncode == 0 else None
    if status == "PASS" and name.endswith("graph") and not inactive_contract(value):
        status = "FAIL_READBACK_CONTRACT"
    if status == "PASS":
        baseline = json.loads((KIT / "BASELINE-NATIVE.json").read_text())
        compared = {k: v for k, v in value.items() if k != "schema"}
        encoded = json.dumps(compared, sort_keys=True, separators=(",", ":")).encode()
        if hashlib.sha256(encoded).hexdigest() != baseline["records"][name]:
            status = "FAIL_INACTIVE_BASELINE_DRIFT"
    return {"check": name, "status": status, "exitCode": result.returncode,
            "boundedStderr": sanitize(stderr, args)[:2400], "readback": sanitize(value, args)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selected-flake", type=Path, required=True)
    parser.add_argument("--composition", type=Path, required=True)
    parser.add_argument("--browser-ux", type=Path, required=True)
    parser.add_argument("--browser-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    errors = source_errors(args)
    if errors:
        print(json.dumps({"status": "SOURCE_DRIFT_HELD", "errors": errors}))
        return 1
    queries = {
        "full-composed-two-host-graph": ("darwinConfigurations",
            f"configurations: import {json.dumps(str(KIT / 'readback.nix'))} {{ inherit configurations; }}"),
        "full-composed-two-host-claims": ("applicationClaimsFor",
            'claims: { target = claims "example-primary"; predecessor = claims "example-workstation"; }'),
    }
    records = [evaluate(args, name, query) for name, query in queries.items()]
    errors = source_errors(args)
    success = not errors and all(r["status"] == "PASS" for r in records)
    args.output.write_text(json.dumps({"schema": "browser-r3r4-native-result-v1",
        "sourceCandidateDigest": BINDING.load_ledger(KIT)["sourceCandidateDigest"], "records": records,
        "sourceDriftAfterReadback": errors, "activationPerformed": False, "nativeSuccess": success,
        "activeR3Qualification": "SOURCE_FIXTURE_ONLY_HELD", "browserEffectiveness": "PENDING_ATTENDED"}, indent=2) + "\n")
    print(json.dumps({"records": [{"check": r["check"], "status": r["status"]} for r in records], "nativeSuccess": success}))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
