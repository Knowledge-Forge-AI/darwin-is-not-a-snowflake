"""Prepare exact owner source copies in empty scratch; no live owner mutation."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--browser-ux", type=Path, required=True)
    parser.add_argument("--composition", type=Path, required=True)
    args = parser.parse_args()
    core = Path(__file__).resolve().parents[2]
    kit = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("binding", kit / "verify-delivery.py")
    binding = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(binding)
    ledger = binding.load_ledger(kit)
    if binding.check_paths(core, ledger["records"], "after_sha256"):
        raise RuntimeError("CORE_SOURCE_DRIFT_HELD")
    args.scratch.mkdir(parents=True, exist_ok=True)
    if any(args.scratch.iterdir()):
        raise RuntimeError("EMPTY_SCRATCH_REQUIRED")
    for label, source, manifest_path, patch in (
        ("browser-ux", args.browser_ux, kit / "browser-ux-manifest.json", kit / "browser-ux.patch"),
        ("composition", args.composition, core / "delivery/browser-ownership-r3r1/composition-manifest.json",
         core / "delivery/browser-ownership-r3r1/composition.patch"),
    ):
        manifest = json.loads(manifest_path.read_text())
        if binding.check_paths(source, manifest["paths"], "before_sha256"):
            raise RuntimeError("OWNER_PREIMAGE_DRIFT_HELD:" + label)
        dependencies = manifest.get("unchangedDependencies", [])
        if binding.check_paths(source, dependencies, "sha256"):
            raise RuntimeError("OWNER_DEPENDENCY_DRIFT_HELD:" + label)
        destination = args.scratch / label
        destination.mkdir()
        paths = subprocess.run(["rtk", "proxy", "git", "-C", str(source), "ls-files",
                                "-co", "--exclude-standard", "-z"],
                               capture_output=True, check=True, timeout=30).stdout.decode().split("\0")
        for relative in sorted(set(filter(None, paths))):
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError("UNSAFE_SOURCE_PATH")
            file = source / path
            if file.is_file():
                target = destination / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, target)
        for flags in (["--check"], []):
            subprocess.run(["rtk", "proxy", "git", "-C", str(destination), "apply"] + flags + [str(patch)],
                           capture_output=True, check=True, timeout=30)
        if binding.check_paths(destination, manifest["paths"], "after_sha256"):
            raise RuntimeError("OWNER_POSTIMAGE_MISMATCH:" + label)
    print(json.dumps({"status": "PREPARED_NOT_SELECTED", "liveOwnerModified": False,
                      "patchesAppliedOnlyInScratch": True, "nativeGraphQualified": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
