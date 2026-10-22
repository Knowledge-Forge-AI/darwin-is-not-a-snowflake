"""Exact source binding checks only; no browser, policy, Git or runtime mutation."""
import argparse
import hashlib
import json
from pathlib import Path


def check_paths(root, records, field):
    errors = []
    for record in records:
        relative = Path(record["path"])
        if relative.is_absolute() or ".." in relative.parts:
            errors.append("UNSAFE_BINDING_PATH")
            continue
        path = root / relative
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if digest != record[field]:
            errors.append("SOURCE_DRIFT:" + record["path"])
    return errors


def load_ledger(kit):
    return json.loads((kit / "SOURCE-BINDINGS.json").read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--owner-candidate", type=Path, required=True)
    args = parser.parse_args()
    kit = args.core / "delivery/browser-ownership-r3r3"
    ledger = load_ledger(kit)
    owner = json.loads((kit / "browser-ux-manifest.json").read_text())
    errors = check_paths(args.core, ledger["records"], "after_sha256")
    errors += check_paths(args.owner_candidate, owner["paths"], "after_sha256")
    errors += check_paths(args.owner_candidate, owner["unchangedDependencies"], "sha256")
    print(json.dumps({"status": "FAIL" if errors else "PASS", "errors": errors,
                      "selected": False, "nativeGraphQualified": False}))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
