"""Qualify an exact locked core using the composition's declared checks/hosts.

Owner entry points qualify registered module candidates and cannot accept a
subflake core. This native protocol records actual locked-tree Nix results; it
does not claim to have run that registered-module entry point.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .deployment_plan import canonical_bytes, normalized_lock, sha256_bytes, tree_identity
from .paths import ConfigError
from .promotion_contract import normalize_nix_repository_url, repository_identity_key, strict_json_loads
from .promotion_core import core_input_revision
from .promotion_binding import resolve_binding
from .promotion_result import MAX_NATIVE_RESULT_BYTES, _candidate_parts


def qualify_core(
    workflow: Any, tree: Path, candidate_ref: str, contract: Mapping[str, Any],
    *, allow_file_urls: bool = False,
    input_name: str = "core",
) -> dict[str, Any]:
    from .promotion_qualification import observe_host_derivations, _literal_host_selector

    start = len(workflow.records)
    before = tree_identity(tree)
    lock_bytes = (tree / "flake.lock").read_bytes()
    lock = normalized_lock(tree / "flake.lock")
    binding = resolve_binding(contract, input_name, lock, allow_file=allow_file_urls)
    if input_name == "core":
        revision = core_input_revision(lock)
    else:
        from .promotion_topology import resolve_lock_input
        revision = lock["nodes"][resolve_lock_input(lock, input_name)]["locked"]["rev"]
    requested_revision, candidate_url = _candidate_parts(candidate_ref)
    revision_matched = revision == requested_revision
    candidate_url = normalize_nix_repository_url(candidate_url, allow_file=allow_file_urls)
    binding_url = normalize_nix_repository_url(
        binding["repository_url"], allow_file=allow_file_urls
    )
    url_matched = repository_identity_key(candidate_url) == repository_identity_key(binding_url)
    if not revision_matched or not url_matched:
        raise ConfigError("core qualification must select the exact retained candidate revision")
    _verify_nix_resolution(workflow, tree, lock)
    checks = {}
    for check in contract["checks"]:
        label = f"core qualification check {check}"
        _build(workflow, tree, label, f'path:.#checks.{contract["system"]}.{check}')
        checks[check] = "pass"
    for host in contract["hosts"]:
        _build(workflow, tree, f"core qualification host {host}", _literal_host_selector(host, "system"))
    hosts = observe_host_derivations(
        workflow, tree, contract, label_prefix="core qualification host derivation"
    )["hosts"]
    after = tree_identity(tree)
    tree_identical = before == after
    lock_identical = (tree / "flake.lock").read_bytes() == lock_bytes
    if not tree_identical or not lock_identical:
        raise ConfigError("core qualification changed the exact candidate tree or lock")
    return {
        "schema": "nix-update-core-qualification-v1" if input_name == "core" else "nix-update-local-source-qualification-v1", "version": 1,
        "input": input_name, "outcome": "pass", "failure": None,
        "qualification": {
            "mode": "native-locked-core-checks-and-hosts" if input_name == "core" else "native-locked-local-source-checks-and-hosts",
            "ownerEntryPointRole": "identity-drift-check-only",
        },
        "candidate": {
            "url": candidate_ref, "requestedRev": requested_revision, "resolvedRev": revision,
            "resolvedUrl": binding["repository_url"],
            "revMatched": revision_matched, "urlMatched": url_matched,
        },
        "lock": {"sha256": sha256_bytes(lock_bytes), "byteIdentical": lock_identical},
        "tree": {"sha256": sha256_bytes(canonical_bytes(before)), "byteIdentical": tree_identical},
        "checks": checks,
        "hosts": {name: {"status": "pass", "drvPath": drv} for name, drv in hosts.items()},
        "commands": [asdict(record) for record in workflow.records[start:]],
    }


def _verify_nix_resolution(workflow: Any, tree: Path, lock: Mapping[str, Any]) -> None:
    label = "core qualification locked metadata"
    result = workflow.command(
        label, ["nix", "flake", "metadata", "--json", "path:.",
                "--no-update-lock-file", "--no-write-lock-file"], tree,
    )
    if result.returncode:
        raise ConfigError("core qualification could not resolve the locked candidate")
    raw = workflow.complete_stdout(label)
    if len(raw.encode()) > MAX_NATIVE_RESULT_BYTES:
        raise ConfigError("core qualification metadata exceeds the bounded JSON limit")
    metadata = strict_json_loads(raw, "core qualification metadata")
    expected_graph = {key: lock[key] for key in ("version", "root", "nodes")}
    if not isinstance(metadata, dict) or metadata.get("locks") != expected_graph:
        raise ConfigError("core qualification Nix lock graph differs from retained candidate")


def _build(workflow: Any, tree: Path, label: str, selector: str) -> None:
    result = workflow.command(
        label, ["nix", "build", "--no-link", "--print-out-paths", selector,
                "--no-update-lock-file", "--no-write-lock-file"], tree,
    )
    outputs = workflow.complete_stdout(label).splitlines()
    if result.returncode or not outputs or any(not item.startswith("/nix/store/") for item in outputs):
        raise ConfigError(f"{label} failed or returned invalid output identity")
