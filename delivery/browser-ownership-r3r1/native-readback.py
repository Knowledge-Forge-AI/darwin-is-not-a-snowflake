"""Ordinary-user Nix evaluation only; no build, activation, or lock write."""
import argparse
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
    core = Path(__file__).resolve().parents[2]
    kit = Path(__file__).resolve().parent
    common = ["nix", "eval", "--offline", "--no-update-lock-file", "--no-write-lock-file", "--impure", "--json"]
    overrides = ["--override-input", "composition-nd", f"path:{args.composition.resolve()}",
                 "--override-input", "composition-nd/core", f"path:{core}",
                 "--override-input", "composition-nd/browser-ux_dinas", f"path:{args.browser_ux.resolve()}"]
    expressions = {
        "full-composed-two-host-graph": ("darwinConfigurations", f"configurations: import {json.dumps(str(kit / 'readback.nix'))} {{ inherit configurations; }}"),
        "full-composed-two-host-claims": ("applicationClaimsFor", 'claims: { target = claims "example-primary"; predecessor = claims "example-workstation"; }'),
    }
    records = []
    for name, (attribute, expression) in expressions.items():
        command = common + [f"path:{args.selected_flake.resolve()}#{attribute}", "--apply", expression] + overrides
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            stderr = re.sub(r"\x1b\[[0-9;]*m", "", result.stderr)
            denied = "daemon-socket" in stderr and ("Operation not permitted" in stderr or "Permission denied" in stderr)
            status = "PASS" if result.returncode == 0 else "ENVIRONMENT_DENIED" if denied else "FAIL"
            value = json.loads(result.stdout) if result.returncode == 0 else None
            if status == "PASS" and name.endswith("graph"):
                for host in ("target", "predecessor"):
                    v = value[host]
                    if not (v["fullToplevelEvaluated"] and not v["rolesR3"] and not v["coreOwnership"]
                            and not v["deploymentHeld"] and not v["failedAssertions"] and not v["guardViolations"]
                            and v["chromeEnabled"] and v["firefoxEnabled"] and not v["automationExposed"]):
                        status = "FAIL_READBACK_CONTRACT"
            def sanitize(value):
                if isinstance(value, dict):
                    return {k: sanitize(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [sanitize(v) for v in value]
                if isinstance(value, str):
                    return value.replace(str(Path.home()), "<operator-home>")
                return value

            value = sanitize(value)
            for path, label in ((str(args.selected_flake.resolve()), "<selected-flake>"),
                                (str(args.composition.resolve()), "<composition-candidate>"),
                                (str(args.browser_ux.resolve()), "<browser-ux-candidate>"),
                                (str(core), "<core>"), (str(Path.home()), "<operator-home>")):
                stderr = stderr.replace(path, label)
            stderr = re.sub(r"/private/tmp/[^\s'\"]+", "<task-scratch>", stderr)
            records.append({"check": name, "status": status, "exitCode": result.returncode,
                            "boundedStderr": stderr[:3500], "readback": value})
        except subprocess.TimeoutExpired:
            records.append({"check": name, "status": "ENVIRONMENT_TIMEOUT", "timeoutSeconds": 60})
    args.output.write_text(json.dumps({"records": records, "activationPerformed": False,
                                      "nativeSuccess": all(r["status"] == "PASS" for r in records)}, indent=2) + "\n")
    print(json.dumps({"records": [{"check": r["check"], "status": r["status"]} for r in records]}))
    return 0 if all(r["status"] == "PASS" for r in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
