from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Sequence
from urllib.parse import parse_qsl, urlsplit

from .paths import ConfigError
from .deployment_workflow import Workflow


SCHEMA = "nix-darwin-deployment-sources-v1"
ACCEPTED_VCS_TYPES = frozenset({"git", "github", "gitlab", "sourcehut", "hg"})
ACCEPTED_ARCHIVE_TYPES = frozenset({"tarball"})
_SRI_SHA256 = re.compile(r"sha256-[A-Za-z0-9+/]{43}=")
_CREDENTIAL_QUERY_KEYS = frozenset({
    "access_token", "token", "api_key", "apikey", "key", "auth", "authorization",
    "password", "passwd", "secret", "signature", "sig", "credential",
    "x_amz_credential", "x_amz_security_token", "x_amz_signature",
    "x_goog_credential", "x_goog_signature",
})


def _collect_store_paths(data: Any, results: set[str]) -> None:
    if isinstance(data, dict):
        if "path" in data and isinstance(data["path"], str) and data["path"].startswith("/nix/store/"):
            results.add(data["path"])
        for value in data.values():
            _collect_store_paths(value, results)
    elif isinstance(data, list):
        for item in data:
            _collect_store_paths(item, results)


def _validate_hashed_tarball(name: str, locked: dict[str, Any]) -> None:
    """Require an HTTPS tarball pinned by a well-formed locked NAR hash."""
    url = locked.get("url")
    url_error = ConfigError(f"hash-pinned tarball input {name} must have a nonempty HTTPS URL")
    if (
        not isinstance(url, str)
        or not url
        or "#" in url
        or any(char.isspace() or not char.isprintable() for char in url)
    ):
        raise url_error
    try:
        parsed = urlsplit(url)
        parsed.port  # raises ValueError for a malformed port
    except ValueError as error:
        raise url_error from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise url_error
    # Source URLs are retained in plans. Do not accept signed URLs or common
    # credential query fields into that durable evidence, even with a NAR pin.
    query_keys = {key.lower().replace("-", "_") for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    if query_keys & _CREDENTIAL_QUERY_KEYS:
        raise ConfigError(f"hash-pinned tarball input {name} must not contain credential query fields")
    nar_hash = locked.get("narHash")
    if not isinstance(nar_hash, str) or _SRI_SHA256.fullmatch(nar_hash) is None:
        raise ConfigError(f"hash-pinned tarball input {name} must have a valid sha256 locked narHash")
    rev = locked.get("rev")
    if rev is not None and (not isinstance(rev, str) or re.fullmatch(r"[0-9a-f]{40}", rev) is None):
        raise ConfigError(f"hash-pinned tarball input {name} has an invalid locked revision")


def _archive_bindings(nodes: dict[str, Any], archive: Any, root: str) -> dict[str, set[str]]:
    """Bind direct lock edges to archive edges; follows edges share those nodes."""
    bindings: dict[str, set[str]] = {}

    def visit(node_name: str, tree: Any, ancestors: frozenset[str]) -> None:
        if node_name in ancestors:
            raise ConfigError("cyclic direct source graph in flake.lock")
        node = nodes.get(node_name)
        if not isinstance(node, dict) or not isinstance(tree, dict):
            raise ConfigError("source archive does not match the locked input graph")
        path = tree.get("path")
        if isinstance(path, str) and path.startswith("/nix/store/"):
            bindings.setdefault(node_name, set()).add(path)
        inputs = node.get("inputs", {})
        children = tree.get("inputs", {})
        if not isinstance(inputs, dict) or not isinstance(children, dict):
            raise ConfigError("source archive has an invalid input mapping")
        for name, child in children.items():
            reference = inputs.get(name)
            if not isinstance(reference, str):
                raise ConfigError(f"source archive input {name} has no direct locked binding")
            visit(reference, child, ancestors | {node_name})

    visit(root, archive, frozenset())
    return bindings


def derive_source_identities(
    workflow: Workflow,
    evaluated_flake: Path,
    overrides: Sequence[str],
    *,
    selected_flake_identity: Path | str | None = None,
) -> dict[str, Any]:
    for index, argument in enumerate(overrides):
        if argument == "--override-input" and index + 1 < len(overrides):
            name = overrides[index + 1]
            if name != "localConfig":
                raise ConfigError(
                    f"ordinary deployment cannot bind flake.overrides input {name}; "
                    "pin the source in flake.lock or use 'nix-update advanced prepare/apply'"
                )
    lock_path = evaluated_flake / "flake.lock"
    if not lock_path.is_file():
        raise ConfigError(f"evaluated flake has no flake.lock: {lock_path}")
    try:
        lock_document = json.loads(lock_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ConfigError(f"flake.lock is not valid JSON: {lock_path}") from error

    if not isinstance(lock_document, dict):
        raise ConfigError("flake.lock must contain an object")
    nodes = lock_document.get("nodes", {})
    if not isinstance(nodes, dict):
        raise ConfigError(f"flake.lock has invalid nodes mapping: {lock_path}")

    label_archive = "locked source tree realization"
    archive_command = [
        "nix",
        "flake",
        "archive",
        "--json",
        "--dry-run",
        "--no-update-lock-file",
        "--no-write-lock-file",
        *overrides,
    ]
    archive_result = workflow.command(label_archive, archive_command, evaluated_flake)
    if archive_result.returncode:
        raise ConfigError("unable to resolve source tree with nix flake archive")
    raw_archive = workflow.complete_stdout(label_archive)
    try:
        archive_data = json.loads(raw_archive)
    except json.JSONDecodeError as error:
        raise ConfigError("nix flake archive emitted invalid JSON") from error

    store_paths: set[str] = set()
    _collect_store_paths(archive_data, store_paths)

    path_info_map: dict[str, dict[str, Any]] = {}
    if store_paths:
        label_info = "source store path inspection"
        sorted_paths = sorted(store_paths)
        info_command = ["nix", "path-info", "--json", *sorted_paths]
        info_result = workflow.command(label_info, info_command, evaluated_flake)
        if info_result.returncode:
            raise ConfigError("unable to inspect source store paths with nix path-info")
        raw_info = workflow.complete_stdout(label_info)
        try:
            path_info_map = json.loads(raw_info)
        except json.JSONDecodeError as error:
            raise ConfigError("nix path-info emitted invalid JSON") from error

    if not isinstance(path_info_map, dict):
        raise ConfigError("nix path-info must emit a store path mapping")
    bindings = None
    root_name = lock_document.get("root", "root")
    root_inputs = nodes.get(root_name, {}).get("inputs", {})
    projection_node = root_inputs.get("localConfig")

    verified_sources: list[dict[str, Any]] = []
    for name, node in sorted(nodes.items()):
        if name == root_name or name == projection_node:
            continue
        if not isinstance(node, dict):
            raise ConfigError(f"flake.lock node {name} is not an object")
        locked = node.get("locked")
        if not isinstance(locked, dict):
            raise ConfigError(f"flake input {name} is unpinned or missing locked metadata")

        input_type = locked.get("type", "")
        expected_nar = locked.get("narHash")
        if not expected_nar:
            raise ConfigError(f"flake input {name} has no locked narHash")

        if input_type in ACCEPTED_VCS_TYPES:
            rev = locked.get("rev")
            if not isinstance(rev, str) or re.fullmatch(r"[0-9a-f]{40}", rev) is None:
                raise ConfigError(
                    f"pinned VCS input {name} must have an exact 40-hex locked revision"
                )
        elif input_type == "path":
            pass
        elif input_type in ACCEPTED_ARCHIVE_TYPES:
            _validate_hashed_tarball(name, locked)
        else:
            raise ConfigError(f"flake input {name} has unsupported type: {input_type}")

        if bindings is None:
            bindings = _archive_bindings(nodes, archive_data, root_name)
        bound_paths = bindings.get(name, set())
        if len(bound_paths) != 1:
            raise ConfigError(f"source input {name} has no unique archive graph binding")
        store_path = next(iter(bound_paths))
        info = path_info_map.get(store_path, {})
        if not isinstance(info, dict) or info.get("narHash") != expected_nar:
            raise ConfigError(
                f"source NAR hash for input {name} not resolved in nix store: expected {expected_nar}"
            )

        verified_sources.append({
            "name": name,
            "type": input_type,
            "revision": locked.get("rev"),
            "narHash": expected_nar,
            "store_path": store_path,
            "url": locked.get("url") or locked.get("path") or f"{locked.get("owner")}/{locked.get("repo")}",
        })

    evidence_flake = (
        Path(selected_flake_identity)
        if selected_flake_identity is not None
        else evaluated_flake
    )
    return {
        "schema": SCHEMA,
        "selected_flake": str(evidence_flake.resolve(strict=False)),
        "sources": verified_sources,
        "verified_count": len(verified_sources),
    }
