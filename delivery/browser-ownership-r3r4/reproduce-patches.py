"""Reproduce cumulative owner bytes from historical patches plus R3R4 deltas."""
import argparse
import difflib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

KIT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("binding", KIT / "verify-source.py")
BINDING = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BINDING)


def reproduce(scratch, destination, label):
    manifest = json.loads((KIT / (label + "-manifest.json")).read_text())
    base = scratch / (label + "-base")
    if BINDING.owner_errors(base, manifest, "before_sha256"):
        raise RuntimeError("PREIMAGE_DRIFT_HELD:" + label)
    candidate = destination / label
    shutil.copytree(scratch / (label + "-r3r3"), candidate, symlinks=True)
    for flags in (["--check"], []):
        subprocess.run(["rtk", "proxy", "git", "-C", str(candidate), "apply"] + flags
                       + [str(KIT / (label + "-R3R3-TO-R3R4.patch"))],
                       capture_output=True, check=True, timeout=30)
    if BINDING.owner_errors(candidate, manifest):
        raise RuntimeError("REPRODUCED_POSTIMAGE_MISMATCH:" + label)
    chunks = []
    for row in manifest["paths"]:
        relative = row["path"]
        before, after = base / relative, candidate / relative
        chunks.extend(difflib.unified_diff(
            before.read_text().splitlines(keepends=True) if before.is_file() else [],
            after.read_text().splitlines(keepends=True) if after.is_file() else [],
            fromfile="a/" + relative if before.is_file() else "/dev/null",
            tofile="b/" + relative if after.is_file() else "/dev/null"))
    output = destination / (label + ".patch")
    output.write_text("".join(chunks))
    if BINDING.digest(output) != manifest["patchSha256"]:
        raise RuntimeError("CUMULATIVE_REPRODUCTION_MISMATCH:" + label)
    return {"owner": label, "status": "PASS", "patchSha256": BINDING.digest(output),
            "postimagePaths": len(manifest["paths"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if BINDING.core_errors(KIT.parents[1]):
        raise RuntimeError("CORE_SOURCE_DRIFT_HELD")
    destination = args.scratch / "reproduced"
    destination.mkdir()
    records = [reproduce(args.scratch, destination, label) for label in ("browser-ux", "composition")]
    args.output.write_text(json.dumps({"status": "PASS", "records": records,
        "liveOwnersModified": False, "indexModified": False}, indent=2) + "\n")
    print(json.dumps({"status": "PASS", "owners": len(records)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
