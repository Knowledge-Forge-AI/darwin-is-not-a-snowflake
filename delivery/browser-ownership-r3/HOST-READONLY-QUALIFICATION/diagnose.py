#!/usr/bin/env python3
"""Passive ordinary-user app metadata and system-policy inventory.

No app launch, Nix invocation, package operation or profile-content read occurs.
Metadata cannot establish enabled extensions, compatibility or effective policy.
"""
import hashlib
import json
from pathlib import Path
import plistlib
import sys

APPS = {
    "brave": ("/Applications/Nix Apps/Brave Browser.app", "com.brave.Browser"),
    "orion": ("/Applications/Orion.app", "com.kagi.kagimacOS"),
    "librewolf": ("/Applications/LibreWolf.app", "io.gitlab.librewolf-community"),
    "floorp": ("/Applications/Floorp.app", None),
    "safari": ("/Applications/Safari.app", "com.apple.Safari"),
    "wipr": ("/Applications/Wipr 2.app", None),
    "brave-youtube": (str(Path.home() / "Applications/Brave YouTube.app"), "com.operator.brave-youtube"),
}
POLICIES = {
    "chrome-managed": "/Library/Managed Preferences/com.google.Chrome.plist",
    "chrome-recommended": "/Library/Preferences/com.google.Chrome.plist",
    "brave-managed": "/Library/Managed Preferences/com.brave.Browser.plist",
    "brave-recommended": "/Library/Preferences/com.brave.Browser.plist",
}


def app_metadata(path, expected):
    try:
        raw = (Path(path) / "Contents/Info.plist").read_bytes()
        info = plistlib.loads(raw)
        if not isinstance(info, dict):
            return {"status": "UNREADABLE"}
        actual = info.get("CFBundleIdentifier")
        return {
            "status": ("IDENTITY_UNATTESTED" if expected is None else
                       "IDENTITY_MATCH" if actual == expected else "IDENTITY_MISMATCH"),
            "observedBundleIdentifier": actual,
            "metadataSha256": hashlib.sha256(raw).hexdigest(),
            "version": info.get("CFBundleShortVersionString"),
            "effectiveReadiness": "UNQUALIFIED",
        }
    except (OSError, ValueError, plistlib.InvalidFileException):
        return {"status": "ABSENT_OR_UNREADABLE", "effectiveReadiness": "UNQUALIFIED"}


def policy_metadata(path):
    target = Path(path)
    if target.is_symlink():
        return {"status": "SYMLINK_PROVENANCE_REQUIRES_QUALIFICATION"}
    try:
        raw = target.read_bytes()
        info = plistlib.loads(raw)
        return {"status": "PRESENT_REQUIRES_EFFECTIVE_READBACK", "sha256": hashlib.sha256(raw).hexdigest(),
                "topLevelKeyCount": len(info) if isinstance(info, dict) else None}
    except (OSError, ValueError, plistlib.InvalidFileException):
        return {"status": "ABSENT_OR_UNREADABLE"}


def main():
    if sys.platform != "darwin":
        print(json.dumps({"status": "NATIVE_MAC_REQUIRED"}))
        return 78
    report = {"status": "METADATA_ONLY_EFFECTIVENESS_UNQUALIFIED",
              "apps": {name: app_metadata(*record) for name, record in APPS.items()},
              "systemPolicy": {name: policy_metadata(path) for name, path in POLICIES.items()},
              "personalProfilesRead": False, "appsLaunched": False, "hostMutation": False}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
