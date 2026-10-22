from __future__ import annotations

import re
from typing import Any, Mapping

from .deployment_plan import canonical_bytes, sha256_bytes
from .paths import ConfigError
from .promotion_contract import (
    HEX_REVISION,
    SAFE_IDENTITY,
    normalize_nix_repository_url,
    reject_promotion_secrets,
    repository_identity_key,
)


MAX_NATIVE_RESULT_BYTES = 10 * 1024 * 1024
DRV_PATH = re.compile(r"/nix/store/[A-Za-z0-9+._?=-]+\.drv")
IMPACT_SCHEMA = "nix-update-promotion-impact-v1"
IMPACT_FIELDS = {
    "schema",
    "baselineHosts",
    "candidateHosts",
    "changedHosts",
    "classification",
    "activationSignal",
    "activationPerformed",
}


def compute_host_impact(
    baseline_hosts: Mapping[str, str],
    candidate_hosts: Mapping[str, str],
    declared_hosts: list[str],
) -> dict[str, Any]:
    baseline = _validate_derivation_map(
        baseline_hosts, declared_hosts, "promotion impact baseline hosts"
    )
    candidate = _validate_derivation_map(
        candidate_hosts, declared_hosts, "promotion impact candidate hosts"
    )
    changed = sorted(
        host for host in declared_hosts if baseline[host] != candidate[host]
    )
    identical = not changed
    return {
        "schema": IMPACT_SCHEMA,
        "baselineHosts": baseline,
        "candidateHosts": candidate,
        "changedHosts": changed,
        "classification": (
            "HOST_DERIVATIONS_IDENTICAL"
            if identical
            else "HOST_DERIVATIONS_CHANGED"
        ),
        "activationSignal": (
            "NO_HOST_CHANGE" if identical else "HOST_CHANGE_REVIEW_REQUIRED"
        ),
        "activationPerformed": False,
    }


def validate_impact_record(
    record: Any, declared_hosts: list[str]
) -> dict[str, Any]:
    if not isinstance(record, dict) or set(record) != IMPACT_FIELDS:
        raise ConfigError("promotion impact record fields are invalid")
    if record.get("schema") != IMPACT_SCHEMA:
        raise ConfigError("promotion impact record schema is invalid")
    expected = compute_host_impact(
        record.get("baselineHosts"),
        record.get("candidateHosts"),
        declared_hosts,
    )
    if record != expected:
        raise ConfigError("promotion impact record classification or host evidence is invalid")
    return dict(record)


def _validate_derivation_map(
    value: Any, declared_hosts: list[str], label: str
) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != set(declared_hosts):
        raise ConfigError(f"{label} must exactly match the declared host set")
    if any(
        not isinstance(value[host], str) or DRV_PATH.fullmatch(value[host]) is None
        for host in declared_hosts
    ):
        raise ConfigError(f"{label} contains an invalid derivation identity")
    return {host: value[host] for host in declared_hosts}


def qualification_host_derivations(
    qualification: Any, declared_hosts: list[str]
) -> dict[str, str]:
    if not isinstance(qualification, dict):
        raise ConfigError("promotion locked qualification evidence is invalid")
    hosts = qualification.get("hosts")
    if not isinstance(hosts, list):
        raise ConfigError("promotion locked host evidence is invalid")
    names = [item.get("name") for item in hosts if isinstance(item, dict)]
    if (
        len(names) != len(hosts)
        or names != declared_hosts
        or len(set(names)) != len(names)
    ):
        raise ConfigError("promotion locked host evidence is missing, duplicated, or reordered")
    derivations = {
        item["name"]: item.get("derivation")
        for item in hosts
        if isinstance(item, dict)
    }
    return _validate_derivation_map(
        derivations, declared_hosts, "promotion locked host evidence"
    )


def validate_native_result(
    document: Any,
    input_name: str,
    candidate_ref: str,
    contract: Mapping[str, Any],
    *,
    allow_file_urls: bool = False,
    expected_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ConfigError("promotion qualifier result must be a JSON object")
    schema = document.get("schema")
    if not isinstance(schema, str) or SAFE_IDENTITY.fullmatch(schema) is None:
        raise ConfigError("promotion qualifier result schema identity is invalid")
    version = document.get("version")
    if "version" in document and (type(version) is not int or version != 1):
        raise ConfigError("promotion qualifier result version is unsupported")
    identity = {"schema": schema, "version": version}
    if expected_identity is not None and identity != dict(expected_identity):
        raise ConfigError("promotion qualifier native result identity drifted")
    revision, repository_url = _candidate_parts(candidate_ref)
    if document.get("outcome") != "pass" or document.get("failure") is not None:
        raise ConfigError("promotion qualifier outcome or failure disposition is not accepted")
    if document.get("input") != input_name:
        raise ConfigError("promotion qualifier input identity mismatched")
    candidate = _mapping(document.get("candidate"), "promotion qualifier candidate")
    required_candidate = {
        "url", "requestedRev", "resolvedRev", "resolvedUrl", "revMatched", "urlMatched",
    }
    if not required_candidate <= set(candidate):
        raise ConfigError("promotion qualifier candidate evidence is incomplete")
    if (
        candidate["url"] != candidate_ref
        or candidate["requestedRev"] != revision
        or candidate["resolvedRev"] != revision
        or candidate["revMatched"] is not True
        or candidate["urlMatched"] is not True
    ):
        raise ConfigError("promotion qualifier candidate revision or match evidence mismatched")
    resolved_url = normalize_nix_repository_url(
        candidate["resolvedUrl"], allow_file=allow_file_urls
    )
    if repository_identity_key(resolved_url) != repository_identity_key(
        normalize_nix_repository_url(repository_url, allow_file=allow_file_urls)
    ):
        raise ConfigError("promotion qualifier candidate resolved URL mismatched")
    lock = _mapping(document.get("lock"), "promotion qualifier lock evidence")
    tree = _mapping(document.get("tree"), "promotion qualifier tree evidence")
    if lock.get("byteIdentical") is not True or tree.get("byteIdentical") is not True:
        raise ConfigError("promotion qualifier lock or tree byte identity proof failed")
    compatibility = document.get("contractCompatibility")
    if "contractCompatibility" in document and (
        not isinstance(compatibility, dict) or compatibility.get("status") != "pass"
    ):
        raise ConfigError("promotion qualifier contract compatibility did not pass")
    snapshots = document.get("snapshots")
    if "snapshots" in document and (
        not isinstance(snapshots, dict)
        or snapshots.get("valid") is not True
        or snapshots.get("byteIdentical") is not True
    ):
        raise ConfigError("promotion qualifier snapshot evidence did not pass")
    check_summary = _validate_checks(document.get("checks"), contract["checks"])
    host_summary = _validate_hosts(document.get("hosts"), contract["hosts"])
    reject_promotion_secrets(document)
    proofs = {
        "lock_byte_identical": lock["byteIdentical"],
        "tree_byte_identical": tree["byteIdentical"],
    }
    # Keep registered-input summaries compatible. Native core qualification
    # attests only evidence it measured; absent optional protocols are not proofs.
    native_local = schema == "nix-update-local-source-qualification-v1"
    if (input_name != "core" and not native_local) or compatibility is not None:
        proofs["contract_compatible"] = compatibility is None or compatibility.get("status") == "pass"
    if (input_name != "core" and not native_local) or snapshots is not None:
        proofs["snapshots_valid"] = snapshots is None or snapshots.get("valid") is True
        proofs["snapshots_byte_identical"] = snapshots is None or snapshots.get("byteIdentical") is True
    return {
        "identity": identity,
        "outcome": "pass",
        "input": input_name,
        "candidate": {
            "requested_revision": revision,
            "resolved_revision": revision,
            "resolved_repository_url": resolved_url,
        },
        "checks": check_summary,
        "hosts": host_summary,
        "proofs": proofs,
    }


def _validate_checks(value: Any, declared: Any) -> dict[str, Any]:
    checks = _mapping(value, "promotion qualifier checks")
    expected = set(declared)
    matches: list[tuple[list[str], Mapping[str, Any]]] = []
    leaf_count = 0

    def visit(node: Any, path: list[str]) -> None:
        nonlocal leaf_count
        if not isinstance(node, dict) or not node:
            raise ConfigError("promotion qualifier check evidence contains an empty or non-map structure")
        if set(node) == expected and all(isinstance(status, str) for status in node.values()):
            matches.append((path, node))
        for key, child in node.items():
            if not isinstance(key, str) or SAFE_IDENTITY.fullmatch(key) is None:
                raise ConfigError("promotion qualifier check identity is invalid")
            if isinstance(child, dict):
                visit(child, [*path, key])
            else:
                leaf_count += 1
                if child != "pass":
                    raise ConfigError("promotion qualifier contains a non-pass check leaf")

    visit(checks, [])
    if len(matches) != 1:
        raise ConfigError("promotion qualifier declared check map is absent or ambiguous")
    path, selected = matches[0]
    if set(selected) != expected or any(selected[name] != "pass" for name in expected):
        raise ConfigError("promotion qualifier declared checks are incomplete, unknown, or non-pass")
    return {
        "declared_count": len(expected),
        "leaf_count": leaf_count,
        "selected_path": path,
        "all_pass": True,
    }


def _validate_hosts(value: Any, declared: Any) -> dict[str, Any]:
    hosts = _mapping(value, "promotion qualifier hosts")
    if set(hosts) != set(declared):
        raise ConfigError("promotion qualifier host set is incomplete or unknown")
    derivations: dict[str, str] = {}
    for host in declared:
        record = _mapping(hosts[host], f"promotion qualifier host {host}")
        if set(record) == {"status", "drvPath"}:
            derivation = record["drvPath"]
        elif set(record) == {"status", "derivation"}:
            derivation = record["derivation"]
        else:
            raise ConfigError("promotion qualifier host evidence fields are invalid")
        if record["status"] != "pass" or not isinstance(derivation, str) or DRV_PATH.fullmatch(
            derivation
        ) is None:
            raise ConfigError("promotion qualifier host status or derivation identity is invalid")
        derivations[host] = derivation
    return {
        "count": len(hosts),
        "derivation_digest": sha256_bytes(canonical_bytes(derivations)),
        "all_pass": True,
    }


def _candidate_parts(candidate_ref: str) -> tuple[str, str]:
    if not isinstance(candidate_ref, str) or candidate_ref.count("?rev=") != 1:
        raise ConfigError("promotion candidate reference is invalid")
    repository_url, revision = candidate_ref.rsplit("?rev=", 1)
    if HEX_REVISION.fullmatch(revision) is None:
        raise ConfigError("promotion candidate reference revision is invalid")
    return revision, repository_url


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be an object")
    return value
