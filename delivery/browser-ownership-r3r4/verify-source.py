"""Verify bound source and cumulative owner postimages; no host/browser writes."""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def check_paths(root, records, field):
    errors = []
    for record in records:
        relative = Path(record["path"])
        if relative.is_absolute() or ".." in relative.parts:
            errors.append("UNSAFE_BINDING_PATH")
        elif digest(root / relative) != record[field]:
            errors.append("SOURCE_DRIFT:" + record["path"])
    return errors


def load_ledger(kit):
    return json.loads((kit / "SOURCE-BINDINGS.json").read_text())


def core_errors(core):
    kit = core / "delivery/browser-ownership-r3r4"
    ledger = load_ledger(kit)
    encoded = json.dumps(ledger["records"], sort_keys=True, separators=(",", ":")).encode()
    errors = check_paths(core, ledger["records"], "after_sha256")
    if hashlib.sha256(encoded).hexdigest() != ledger["sourceCandidateDigest"]:
        errors.append("SOURCE_LEDGER_DIGEST_MISMATCH")
    for line in (kit / "SHA256SUMS").read_text().splitlines():
        expected, relative = line.split("  ", 1)
        errors += check_paths(kit, [{"path": relative, "sha256": expected}], "sha256")
    expected = (kit / "SHA256SUMS.sha256").read_text().split()[0]
    if digest(kit / "SHA256SUMS") != expected:
        errors.append("DELIVERY_MANIFEST_DIGEST_MISMATCH")
    return errors


def owner_errors(root, manifest, field="after_sha256"):
    return (check_paths(root, manifest["paths"], field)
            + check_paths(root, manifest.get("unchangedDependencies", []), "sha256"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--owner-candidate", type=Path, required=True)
    parser.add_argument("--composition-candidate", type=Path, required=True)
    args = parser.parse_args()
    kit = args.core / "delivery/browser-ownership-r3r4"
    errors = core_errors(args.core)
    for label, root in (("browser-ux", args.owner_candidate), ("composition", args.composition_candidate)):
        errors += owner_errors(root, json.loads((kit / (label + "-manifest.json")).read_text()))
    print(json.dumps({"status": "FAIL" if errors else "PASS", "errors": errors,
                      "selected": False, "nativeGraphQualified": False}))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
