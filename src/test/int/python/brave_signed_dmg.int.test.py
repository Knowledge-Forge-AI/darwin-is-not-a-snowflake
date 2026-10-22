# nix-darwin-test-source-target: packages/brave-signed-dmg/default.nix
"""Read-only signing qualification of an explicitly supplied candidate store app.

Set DINAS_BRAVE_SIGNING_APP to the realized package's Applications/Brave Browser.app.
This never launches the browser or touches a profile. A skip is unqualified.
"""
import os
from pathlib import Path
import plistlib
import subprocess
import sys

import pytest


def test_candidate_preserves_brave_developer_signature_and_gatekeeper_acceptance():
    candidate = os.environ.get("DINAS_BRAVE_SIGNING_APP")
    if sys.platform != "darwin" or not candidate:
        pytest.skip("NATIVE_BRAVE_SIGNING_UNQUALIFIED: supply a realized Darwin candidate")
    app = Path(candidate).resolve(strict=True)
    assert app.parts[:3] == ("/", "nix", "store")
    assert app.parts[4:] == ("Applications", "Brave Browser.app")
    with (app / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    assert info["CFBundleIdentifier"] == "com.brave.Browser"
    assert info["CFBundleShortVersionString"] == "149.1.91.180"
    commands = [
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
        ["/usr/bin/codesign", "-dv", "--verbose=4", str(app)],
        ["/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose=4", str(app)],
    ]
    results = [subprocess.run(command, capture_output=True, text=True, timeout=60)
               for command in commands]
    assert all(result.returncode == 0 for result in results), [
        (result.returncode, result.stdout, result.stderr) for result in results
    ]
    details = results[1].stdout + results[1].stderr
    assert "Identifier=com.brave.Browser" in details.splitlines()
    assert "TeamIdentifier=KL8N8XSYF4" in details.splitlines()
    assert "Signature=adhoc" not in details
