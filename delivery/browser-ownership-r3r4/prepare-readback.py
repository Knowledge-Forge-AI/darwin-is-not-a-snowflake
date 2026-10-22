"""Copy exact local sources and apply cumulative patches only in empty scratch."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tomllib

KIT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("binding", KIT / "verify-source.py")
BINDING = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BINDING)


def copy_source(source, destination):
    destination.mkdir()
    paths = subprocess.run(["rtk", "proxy", "git", "-C", str(source), "ls-files",
                            "-co", "--exclude-standard", "-z"],
                           capture_output=True, check=True, timeout=30).stdout.decode().split("\0")
    for relative in sorted(set(filter(None, paths))):
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError("UNSAFE_SOURCE_PATH")
        file = source / path
        target = destination / path
        if file.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(file.readlink())
        elif file.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, target)


def apply_patch(destination, patch, reverse=False):
    for flags in (["--check"], []):
        subprocess.run(["rtk", "proxy", "git", "-C", str(destination), "apply"]
                       + (["--reverse"] if reverse else []) + flags + [str(patch)],
                       capture_output=True, check=True, timeout=30)


def prepare_owner(source, scratch, label, prior_kit):
    manifest = json.loads((KIT / (label + "-manifest.json")).read_text())
    if BINDING.owner_errors(source, manifest, "before_sha256"):
        raise RuntimeError("OWNER_PREIMAGE_DRIFT_HELD:" + label)
    if BINDING.digest(KIT / (label + ".patch")) != manifest["patchSha256"]:
        raise RuntimeError("OWNER_PATCH_DIGEST_MISMATCH:" + label)
    copy_source(source, scratch / (label + "-base"))
    for suffix, patch in (("r3r4", KIT / (label + ".patch")),
                          ("r3r3", prior_kit / (label + ".patch"))):
        destination = scratch / (label + "-" + suffix)
        shutil.copytree(scratch / (label + "-base"), destination)
        apply_patch(destination, patch)
    if BINDING.owner_errors(scratch / (label + "-r3r4"), manifest):
        raise RuntimeError("OWNER_POSTIMAGE_MISMATCH:" + label)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--browser-ux", type=Path, required=True)
    parser.add_argument("--composition", type=Path, required=True)
    parser.add_argument("--browser-config", type=Path, required=True)
    args = parser.parse_args()
    core = KIT.parents[1]
    if BINDING.core_errors(core):
        raise RuntimeError("CORE_SOURCE_DRIFT_HELD")
    expected = json.loads((KIT / "CONFIGURATION-BINDING.json").read_text())["browserConfigurationSha256"]
    if BINDING.digest(args.browser_config) != expected:
        raise RuntimeError("BROWSER_CONFIGURATION_DRIFT_HELD")
    args.scratch.mkdir(parents=True, exist_ok=True)
    if any(args.scratch.iterdir()):
        raise RuntimeError("EMPTY_SCRATCH_REQUIRED")
    prepare_owner(args.browser_ux, args.scratch, "browser-ux", core / "delivery/browser-ownership-r3r3")
    prepare_owner(args.composition, args.scratch, "composition", core / "delivery/browser-ownership-r3r1")
    copy_source(core, args.scratch / "previous-core")
    apply_patch(args.scratch / "previous-core", KIT / "CORE-SOURCE-DIFF.patch", reverse=True)
    config = tomllib.loads(args.browser_config.read_text())
    (args.scratch / "configuration.json").write_text(json.dumps(config, indent=2) + "\n")
    print(json.dumps({"status": "PREPARED_NOT_SELECTED", "liveOwnersModified": False,
                      "patchesAppliedOnlyInScratch": True, "nativeGraphQualified": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
