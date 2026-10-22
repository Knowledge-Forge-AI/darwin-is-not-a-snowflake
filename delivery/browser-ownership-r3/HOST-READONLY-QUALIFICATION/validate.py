#!/usr/bin/env python3
"""Validate sanitized, candidate-bound observation structure, never deployment."""
import argparse
import datetime
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent


def valid_observation(row, requirement):
    if not isinstance(row, dict) or row.get("status") != "PASS":
        return False
    if row.get("method") not in requirement["methods"]:
        return False
    if re.fullmatch(r"[0-9a-f]{64}", str(row.get("evidenceSha256"))) is None:
        return False
    try:
        stamp = datetime.datetime.fromisoformat(row["observedAt"].replace("Z", "+00:00"))
        return stamp.tzinfo is not None and stamp <= datetime.datetime.now(datetime.timezone.utc)
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def validate(evidence, binding, requirements):
    if not isinstance(evidence, dict):
        return ["EVIDENCE_OBJECT_REQUIRED"]
    problems = []
    if evidence.get("schema") != "browser-roles-r3-host-readback-v1":
        problems.append("SCHEMA_MISMATCH")
    if evidence.get("sourceCandidateDigest") != binding["sourceCandidateDigest"]:
        problems.append("SOURCE_BINDING_MISMATCH")
    observations = evidence.get("observations")
    if not isinstance(observations, dict):
        return problems + ["OBSERVATIONS_OBJECT_REQUIRED"]
    if set(observations) != set(requirements):
        problems.append("INCOMPLETE_OR_UNKNOWN_REQUIREMENTS")
    for name, requirement in requirements.items():
        if not valid_observation(observations.get(name), requirement):
            problems.append(name)
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    try:
        evidence = json.loads(args.evidence.read_text())
        binding = json.loads((HERE / "CANDIDATE-BINDING.json").read_text())
        requirements = json.loads((HERE / "REQUIREMENTS.json").read_text())
        problems = validate(evidence, binding, requirements)
    except (OSError, ValueError, TypeError, KeyError):
        problems = ["UNREADABLE_EVIDENCE_OR_KIT"]
    print(json.dumps({"status": "HOST_QUALIFICATION_HELD" if problems else "READBACK_STRUCTURE_COMPLETE_MANAGER_DISPOSITION_REQUIRED",
                      "pending": problems, "deploymentAuthorized": False,
                      "independentEvidenceReviewRequired": True}, indent=2))
    return 78 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
