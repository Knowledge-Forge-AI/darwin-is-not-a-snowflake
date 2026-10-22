"""Read-only inactive R3R2 two-host Nix readback; no build, browser or activation."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selected-flake", type=Path, required=True)
    parser.add_argument("--composition", type=Path, required=True)
    parser.add_argument("--browser-ux", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    kit = Path(__file__).resolve().parent
    core = kit.parents[1]
    spec = importlib.util.spec_from_file_location("binding", kit / "verify-delivery.py")
    binding = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(binding)
    ledger = binding.load_ledger(kit)
    owner = json.loads((kit / "browser-ux-manifest.json").read_text())
    composition = json.loads((core / "delivery/browser-ownership-r3r1/composition-manifest.json").read_text())

    def drift():
        return (binding.check_paths(core, ledger["records"], "after_sha256")
                + binding.check_paths(args.browser_ux, owner["paths"], "after_sha256")
                + binding.check_paths(args.browser_ux, owner["unchangedDependencies"], "sha256")
                + binding.check_paths(args.composition, composition["paths"], "after_sha256"))

    if drift():
        print(json.dumps({"status": "SOURCE_DRIFT_HELD", "errors": drift()}))
        return 1
    common = ["rtk", "proxy", "nix", "eval", "--offline", "--no-update-lock-file",
              "--no-write-lock-file", "--impure", "--json"]
    overrides = ["--override-input", "composition-nd", f"path:{args.composition.resolve()}",
                 "--override-input", "composition-nd/core", f"path:{core}",
                 "--override-input", "composition-nd/browser-ux_dinas", f"path:{args.browser_ux.resolve()}"]
    expressions = {
        "full-composed-two-host-graph": ("darwinConfigurations",
            f"configurations: import {json.dumps(str(kit / 'readback.nix'))} {{ inherit configurations; }}"),
        "full-composed-two-host-claims": ("applicationClaimsFor",
            'claims: { target = claims "example-primary"; predecessor = claims "example-workstation"; }'),
    }

    def sanitize(value):
        if isinstance(value, dict):
            return {k: sanitize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [sanitize(v) for v in value]
        if isinstance(value, str):
            for path in (args.selected_flake.resolve(), args.composition.resolve(),
                         args.browser_ux.resolve(), core, Path.home()):
                value = value.replace(str(path), "<source-root>")
            value = re.sub(r"/Users/[^/\s]+", "<operator-home>", value)
            return re.sub(r"/private/tmp/[^\s'\"]+", "<task-scratch>", value)
        return value

    records = []
    for name, (attribute, expression) in expressions.items():
        try:
            result = subprocess.run(common + [f"path:{args.selected_flake.resolve()}#{attribute}",
                                              "--apply", expression] + overrides,
                                    capture_output=True, text=True, timeout=60)
            stderr = re.sub(r"\x1b\[[0-9;]*m", "", result.stderr)
            denied = "daemon-socket" in stderr and any(s in stderr for s in
                                                       ("Operation not permitted", "Permission denied"))
            status = "PASS" if result.returncode == 0 else "ENVIRONMENT_DENIED" if denied else "FAIL"
            value = json.loads(result.stdout) if result.returncode == 0 else None
            if status == "PASS" and name.endswith("graph"):
                for host in ("target", "predecessor"):
                    v = value[host]
                    if not (v["fullToplevelEvaluated"] and not v["rolesR3"] and not v["coreOwnership"]
                            and not v["deploymentHeld"] and not v["failedAssertions"] and not v["guardViolations"]
                            and v["chromeEnabled"] and v["firefoxEnabled"] and not v["automationExposed"]
                            and v["protonVpnIntent"] is None):
                        status = "FAIL_READBACK_CONTRACT"
            records.append({"check": name, "status": status, "exitCode": result.returncode,
                            "boundedStderr": sanitize(stderr)[:2400], "readback": sanitize(value)})
        except subprocess.TimeoutExpired:
            records.append({"check": name, "status": "ENVIRONMENT_TIMEOUT", "timeoutSeconds": 60})
    errors = drift()
    success = not errors and all(r["status"] == "PASS" for r in records)
    args.output.write_text(json.dumps({"schema": "browser-r3r2-native-result-v1",
        "sourceCandidateDigest": ledger["sourceCandidateDigest"], "records": records,
        "sourceDriftAfterReadback": errors, "activationPerformed": False, "nativeSuccess": success,
        "activeExtensionQualification": "PENDING_ATTENDED"}, indent=2) + "\n")
    print(json.dumps({"records": [{"check": r["check"], "status": r["status"]} for r in records],
                      "nativeSuccess": success}))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
