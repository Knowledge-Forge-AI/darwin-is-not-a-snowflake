"""Validate sanitized attended observations; never read a browser or install/connect."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

CHROMIUM = "jplgfhpmjnbigmhklmmbgecoobifkmpa"
GECKO = "vpn@proton.ch"
ROLES = {"brave-daily": (CHROMIUM, "core"), "librewolf": (GECKO, "core"),
         "floorp": (GECKO, "browser-ux")}


def validate_identity(observation, evidence):
    if not isinstance(evidence, dict) or evidence.get("status") != "FRESH_PACKAGE_IDENTITY_OBSERVED_SIGNING_PENDING":
        return ["FRESH_AMO_XPI_IDENTITY_REQUIRED"]
    package = evidence.get("xpi")
    if not isinstance(package, dict):
        return ["FRESH_AMO_XPI_IDENTITY_REQUIRED"]
    errors = []
    digest = package.get("sha256")
    if (package.get("guid") != GECKO or package.get("version") != observation.get("version")
            or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)
            or digest != observation.get("identitySha256")
            or package.get("signatureContainerPresent") is not True):
        errors.append("AMO_XPI_VERSION_OR_RECEIPT_MISMATCH")
    try:
        timestamp = datetime.fromisoformat(evidence["observedAtUtc"])
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()
        if not -300 <= age <= 86400:
            errors.append("AMO_XPI_IDENTITY_STALE_REFRESH_REQUIRED")
    except (KeyError, TypeError, ValueError):
        errors.append("AMO_XPI_IDENTITY_TIMESTAMP_REQUIRED")
    return errors


def validate_baselines(observation, identity):
    errors = []
    before, after = observation.get("existingBefore"), observation.get("existingAfter")
    if (observation.get("extensionBaselineComplete") is not True
            or not isinstance(before, dict) or not isinstance(after, dict)):
        errors.append("EXTENSION_BASELINE_REQUIRED")
    else:
        if not before and observation.get("emptyExtensionBaselineConfirmed") is not True:
            errors.append("EMPTY_EXTENSION_BASELINE_UNCONFIRMED")
        if any(after.get(key) != state for key, state in before.items()):
            errors.append("EXISTING_EXTENSION_REMOVED_OR_CHANGED")
        action = "OBSERVED_EXISTING" if identity in before else "ADDED_THIS_SESSION"
        if observation.get("installationAction") != action:
            errors.append("INSTALLATION_ACTION_MISMATCH")
    forced_before, forced_after = observation.get("forcedBefore"), observation.get("forcedAfter")
    if (observation.get("forcedPolicyBaselineComplete") is not True
            or not isinstance(forced_before, list) or forced_before != forced_after):
        errors.append("FORCED_EXTENSION_POLICY_CHANGED_OR_UNOBSERVED")
    elif not forced_before and observation.get("emptyForcedBaselineConfirmed") is not True:
        errors.append("EMPTY_FORCED_BASELINE_UNCONFIRMED")
    return errors


def validate(observation, identity_evidence=None):
    if not isinstance(observation, dict):
        return ["MALFORMED_OBSERVATION"]
    errors = []
    role = observation.get("role")
    if role not in ROLES:
        return ["ROLE_INSTALLATION_HELD_OR_NOT_APPLICABLE"]
    identity, owner = ROLES[role]
    if observation.get("id") != identity or observation.get("semanticOwner") != owner:
        errors.append("IDENTITY_OR_OWNER_MISMATCH")
    if not isinstance(observation.get("version"), str) or not observation["version"].strip():
        errors.append("INSTALLED_VERSION_REQUIRED")
    for flag in ("attended", "profileConfirmed", "officialStore", "enabled",
                 "policiesUnchanged", "wallpaperPreserved", "credentialsPreserved",
                 "hardeningPreserved", "dnsUnchanged"):
        if observation.get(flag) is not True:
            errors.append("OBSERVATION_REQUIRED:" + flag)
    for flag in ("signedInDuringCanary", "connectionAttempted", "connected"):
        if observation.get(flag) is not False:
            errors.append("UNAUTHORIZED_LOGIN_OR_CONNECTION:" + flag)
    if observation.get("autoConnect") not in ("OFF", "UNOBSERVABLE_LOGGED_OUT"):
        errors.append("AUTO_CONNECT_NOT_HELD")
    errors += validate_baselines(observation, identity)
    if role != "brave-daily":
        errors += validate_identity(observation, identity_evidence)
        if observation.get("browserSignatureValid") is not True:
            errors.append("BROWSER_SIGNATURE_ATTESTATION_REQUIRED")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observation", type=Path)
    parser.add_argument("--identity-evidence", type=Path,
                        help="Fresh identity-readback.py JSON receipt, required for Gecko roles")
    args = parser.parse_args()
    observation = json.loads(args.observation.read_text())
    evidence = json.loads(args.identity_evidence.read_text()) if args.identity_evidence else None
    errors = validate(observation, evidence)
    print(json.dumps({"status": "FAIL" if errors else "ATTENDED_PRESENCE_OBSERVED",
                      "errors": errors, "protected": False, "connectionQualified": False,
                      "installationPerformed": not errors and observation.get("installationAction") == "ADDED_THIS_SESSION",
                      "evidenceAuthority": "operator observations, not independent runtime inspection"}))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
