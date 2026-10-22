from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from .deployment_plan import canonical_bytes, sha256_bytes
from .paths import ConfigError
from .promotion_contract import (
    HEX_REVISION,
    normalize_nix_repository_url,
    repository_identity_key,
    validate_release_ref,
)


DYNAMIC_LOCK_FIELDS = {"rev", "revCount", "lastModified", "narHash"}
LOCKED_FIELDS = DYNAMIC_LOCK_FIELDS | {"type", "url", "ref", "submodules"}
ORIGINAL_FIELDS = {"type", "url", "ref", "submodules"}
OWNED_CLOSURE_SCHEMA = "nix-update-promotion-owned-closure-v1"
AUTHORIZATION_FIELDS = {
    "schema",
    "selectedNode",
    "preimageOwnedNodes",
    "candidateOwnedNodes",
    "addedNodes",
    "removedNodes",
    "changedNodes",
    "outsideGraphDigest",
    "sharedFollowsDigest",
    "authorizationDigest",
}


def validate_selected_lock_node(
    lock: Mapping[str, Any],
    node_name: str,
    repository_url: str,
    revision: str,
    *,
    allow_file: bool = False,
    expected_ref: str = "refs/heads/main",
    allow_implicit_main: bool = True,
) -> dict[str, Any]:
    nodes = lock.get("nodes")
    if not isinstance(nodes, dict):
        raise ConfigError("promotion lock nodes are invalid")
    node = nodes.get(node_name)
    if not isinstance(node, dict):
        raise ConfigError("promotion selected lock node is invalid")
    original = _record(node.get("original"), "promotion selected lock original")
    locked = _record(node.get("locked"), "promotion selected lock resolution")
    if not set(original) <= ORIGINAL_FIELDS or "dir" in original:
        raise ConfigError("promotion selected lock original has an alternate or unsupported identity")
    if not set(locked) <= LOCKED_FIELDS or "dir" in locked:
        raise ConfigError("promotion selected lock resolution has an alternate or unsupported identity")
    if original.get("type") != "git" or locked.get("type") != "git":
        raise ConfigError("promotion selected lock node must retain Git source identity")
    expected_url = repository_identity_key(
        normalize_nix_repository_url(repository_url, allow_file=allow_file)
    )
    for value in (original.get("url"), locked.get("url")):
        if repository_identity_key(
            normalize_nix_repository_url(value, allow_file=allow_file)
        ) != expected_url:
            raise ConfigError("promotion selected lock node repository URL mismatched")
    expected_ref = validate_release_ref(expected_ref)
    for record in (original, locked):
        recorded_ref = record.get("ref")
        if allow_implicit_main:
            if expected_ref != "refs/heads/main":
                raise ConfigError("implicit promotion lock refs are limited to version-1 main")
            if recorded_ref not in (None, "main", expected_ref):
                raise ConfigError("promotion selected lock node uses a non-main reference")
            continue
        if not isinstance(recorded_ref, str):
            raise ConfigError("version-2 promotion lock nodes require explicit release refs")
        normalized_ref = (
            recorded_ref
            if recorded_ref.startswith("refs/")
            else f"refs/heads/{recorded_ref}"
        )
        if validate_release_ref(normalized_ref) != expected_ref:
            raise ConfigError("promotion selected lock node release ref mismatched")
    if not isinstance(revision, str) or HEX_REVISION.fullmatch(revision) is None:
        raise ConfigError("promotion selected lock revision is invalid")
    if locked.get("rev") != revision:
        raise ConfigError("promotion selected lock node does not select the requested revision")
    return dict(node)


def authorize_owned_lock_transition(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    node_name: str,
    repository_url: str,
    revision: str,
    *,
    allow_file: bool = False,
    expected_ref: str = "refs/heads/main",
    allow_implicit_main: bool = True,
) -> dict[str, Any]:
    """Authorize one selected input's exact, exclusive string-edge closure."""
    before_nodes, before_root = _validated_graph(before, "preimage")
    after_nodes, after_root = _validated_graph(after, "candidate")
    if before_root != after_root or before.get("version") != after.get("version"):
        raise ConfigError("promotion lock root or version changed")
    if before_nodes[before_root] != after_nodes[after_root]:
        raise ConfigError("promotion lock root node or direct input mapping changed")
    if node_name == before_root or node_name not in before_nodes or node_name not in after_nodes:
        raise ConfigError("promotion selected lock node identity is invalid")

    before_owned = _owned_closure(before_nodes, node_name, "preimage")
    after_owned = _owned_closure(after_nodes, node_name, "candidate")
    _prove_exclusive(before_nodes, before_root, node_name, before_owned, "preimage")
    _prove_exclusive(after_nodes, after_root, node_name, after_owned, "candidate")

    union = before_owned | after_owned
    before_names = set(before_nodes)
    after_names = set(after_nodes)
    added = after_names - before_names
    removed = before_names - after_names
    changed = {
        name for name in before_names & after_names if before_nodes[name] != after_nodes[name]
    }
    if not added <= after_owned or not removed <= before_owned or not changed <= union:
        raise ConfigError("promotion lock changed nodes outside the selected owned closure")
    before_outside = before_names - union
    after_outside = after_names - union
    if before_outside != after_outside or any(
        before_nodes[name] != after_nodes[name] for name in before_outside
    ):
        raise ConfigError("promotion lock outside graph changed")

    before_selected = _record(before_nodes[node_name], "promotion lock preimage selected node")
    after_selected = _record(after_nodes[node_name], "promotion lock candidate selected node")
    before_static = {
        key: value for key, value in before_selected.items() if key not in {"inputs", "locked"}
    }
    after_static = {
        key: value for key, value in after_selected.items() if key not in {"inputs", "locked"}
    }
    if before_static != after_static:
        raise ConfigError("promotion selected root declaration or original identity changed")
    before_locked = _record(before_selected.get("locked"), "promotion selected preimage resolution")
    after_locked = _record(after_selected.get("locked"), "promotion selected candidate resolution")
    if any(
        before_locked.get(key) != after_locked.get(key) and key not in DYNAMIC_LOCK_FIELDS
        for key in set(before_locked) | set(after_locked)
    ):
        raise ConfigError("promotion selected root repository, ref, or selector changed")
    before_revision = before_locked.get("rev")
    if not isinstance(before_revision, str):
        raise ConfigError("promotion selected preimage revision is invalid")
    validate_selected_lock_node(
        before,
        node_name,
        repository_url,
        before_revision,
        allow_file=allow_file,
        expected_ref=expected_ref,
        allow_implicit_main=allow_implicit_main,
    )
    validate_selected_lock_node(
        after,
        node_name,
        repository_url,
        revision,
        allow_file=allow_file,
        expected_ref=expected_ref,
        allow_implicit_main=allow_implicit_main,
    )

    common_owned = before_owned & after_owned
    before_common_follows = _follows_records(before_nodes, before_root, common_owned)
    after_common_follows = _follows_records(after_nodes, after_root, common_owned)
    if before_common_follows != after_common_follows:
        raise ConfigError("promotion changed a common owned node follows edge or resolved target")
    candidate_follows = _follows_records(after_nodes, after_root, after_owned)
    for record in candidate_follows:
        if record["source"] in added:
            target = record["resolvedNode"]
            if target in union or target not in before_outside:
                raise ConfigError("promotion added owned node follows an owned or changed target")

    for name in sorted((added | changed) & union & after_names):
        _validate_owned_source(
            after_nodes[name], name, selected=name == node_name, allow_file=allow_file
        )

    outside_material = {
        "root": before_root,
        "version": before["version"],
        "nodes": {name: before_nodes[name] for name in sorted(before_outside)},
    }
    follows_material = {
        "preimage": _follows_records(before_nodes, before_root, before_owned),
        "candidate": candidate_follows,
    }
    record: dict[str, Any] = {
        "schema": OWNED_CLOSURE_SCHEMA,
        "selectedNode": node_name,
        "preimageOwnedNodes": sorted(before_owned),
        "candidateOwnedNodes": sorted(after_owned),
        "addedNodes": sorted(added),
        "removedNodes": sorted(removed),
        "changedNodes": sorted(changed),
        "outsideGraphDigest": sha256_bytes(canonical_bytes(outside_material)),
        "sharedFollowsDigest": sha256_bytes(canonical_bytes(follows_material)),
        "authorizationDigest": "",
    }
    record["authorizationDigest"] = _authorization_digest(record)
    validate_owned_closure_authorization(record)
    return record


def validate_owned_closure_authorization(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != AUTHORIZATION_FIELDS:
        raise ConfigError("promotion owned-closure authorization fields are invalid")
    if value.get("schema") != OWNED_CLOSURE_SCHEMA:
        raise ConfigError("promotion owned-closure authorization schema is invalid")
    if not isinstance(value.get("selectedNode"), str) or not value["selectedNode"]:
        raise ConfigError("promotion owned-closure selected node is invalid")
    for field in (
        "preimageOwnedNodes",
        "candidateOwnedNodes",
        "addedNodes",
        "removedNodes",
        "changedNodes",
    ):
        identities = value.get(field)
        if (
            not isinstance(identities, list)
            or any(not isinstance(item, str) or not item for item in identities)
            or identities != sorted(identities)
            or len(identities) != len(set(identities))
        ):
            raise ConfigError("promotion owned-closure node identities are not canonical")
    for field in ("outsideGraphDigest", "sharedFollowsDigest", "authorizationDigest"):
        digest = value.get(field)
        if not isinstance(digest, str) or len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise ConfigError("promotion owned-closure digest is invalid")
    if value["authorizationDigest"] != _authorization_digest(value):
        raise ConfigError("promotion owned-closure authorization digest is invalid")
    return dict(value)


def _authorization_digest(value: Mapping[str, Any]) -> str:
    material = dict(value)
    material["authorizationDigest"] = ""
    return sha256_bytes(canonical_bytes(material))


def _validated_graph(
    lock: Mapping[str, Any], label: str
) -> tuple[Mapping[str, Any], str]:
    nodes = lock.get("nodes")
    root = lock.get("root")
    version = lock.get("version")
    if (
        not isinstance(nodes, dict)
        or not isinstance(root, str)
        or not root
        or root not in nodes
        or not isinstance(version, int)
        or isinstance(version, bool)
    ):
        raise ConfigError(f"promotion {label} lock root, version, or nodes are invalid")
    for name, value in nodes.items():
        if not isinstance(name, str) or not name or not isinstance(value, dict):
            raise ConfigError(f"promotion {label} lock node identity is invalid")
        if "inputs" not in value:
            continue
        inputs = value.get("inputs")
        if not isinstance(inputs, dict):
            raise ConfigError(f"promotion {label} lock node inputs mapping is malformed")
        for input_name, reference in inputs.items():
            if not isinstance(input_name, str) or not input_name:
                raise ConfigError(f"promotion {label} lock input identity is invalid")
            if isinstance(reference, str):
                if not reference or reference not in nodes:
                    raise ConfigError(f"promotion {label} lock string input is unresolved")
            elif isinstance(reference, list):
                if not reference or any(not isinstance(item, str) or not item for item in reference):
                    raise ConfigError(f"promotion {label} lock follows path is malformed")
            else:
                raise ConfigError(f"promotion {label} lock input reference is malformed")
    _reject_string_cycles(nodes, label)
    for name, value in nodes.items():
        inputs = value.get("inputs")
        if isinstance(inputs, dict):
            for reference in inputs.values():
                if isinstance(reference, list):
                    _resolve_input_path(nodes, root, root, reference, set())
    return nodes, root


def _reject_string_cycles(nodes: Mapping[str, Any], label: str) -> None:
    complete: set[str] = set()
    active: set[str] = set()

    def visit(name: str) -> None:
        if name in active:
            raise ConfigError(f"promotion {label} lock string-edge graph is cyclic")
        if name in complete:
            return
        active.add(name)
        inputs = nodes[name].get("inputs")
        if isinstance(inputs, dict):
            for reference in inputs.values():
                if isinstance(reference, str):
                    visit(reference)
        active.remove(name)
        complete.add(name)

    for name in nodes:
        visit(name)


def _owned_closure(
    nodes: Mapping[str, Any], selected: str, label: str
) -> set[str]:
    owned: set[str] = set()
    active: set[str] = set()

    def visit(name: str) -> None:
        if name in active:
            raise ConfigError(f"promotion {label} owned closure is cyclic")
        if name in owned:
            return
        active.add(name)
        inputs = nodes[name].get("inputs")
        if isinstance(inputs, dict):
            for reference in inputs.values():
                if isinstance(reference, str):
                    visit(reference)
        active.remove(name)
        owned.add(name)

    visit(selected)
    return owned


def selected_owned_closure(
    lock: Mapping[str, Any],
    node_name: str,
    label: str = "lock",
) -> set[str]:
    """Return the exact, string-edge closure owned by selected node."""
    nodes, root = _validated_graph(lock, label)
    if node_name not in nodes or node_name == root:
        raise ConfigError(f"promotion {label} selected node {node_name!r} not in lock")
    owned = _owned_closure(nodes, node_name, label)
    _prove_exclusive(nodes, root, node_name, owned, label)
    return owned


def _reverse_string_edges(nodes: Mapping[str, Any]) -> dict[str, list[tuple[str, str]]]:
    incoming = {name: [] for name in nodes}
    for source, node in nodes.items():
        inputs = node.get("inputs")
        if isinstance(inputs, dict):
            for input_name, reference in inputs.items():
                if isinstance(reference, str):
                    incoming[reference].append((source, input_name))
    for edges in incoming.values():
        edges.sort()
    return incoming


def _prove_exclusive(
    nodes: Mapping[str, Any],
    root: str,
    selected: str,
    owned: set[str],
    label: str,
) -> None:
    incoming = _reverse_string_edges(nodes)
    selected_root_edges = [edge for edge in incoming[selected] if edge[0] == root]
    if len(selected_root_edges) != 1:
        raise ConfigError(f"promotion {label} selected node root resolution is ambiguous")
    for name in owned:
        outside = [edge for edge in incoming[name] if edge[0] not in owned]
        permitted = selected_root_edges if name == selected else []
        if outside != permitted:
            raise ConfigError(f"promotion {label} owned closure has an outside string-edge owner")


def _resolve_input_path(
    nodes: Mapping[str, Any],
    root: str,
    current: str,
    segments: Sequence[str],
    seen: set[tuple[str, tuple[str, ...]]],
    depth: int = 0,
) -> str:
    key = (current, tuple(segments))
    if depth > 32 or key in seen or not segments:
        raise ConfigError("promotion lock follows path is cyclic or empty")
    seen.add(key)
    inputs = nodes[current].get("inputs")
    reference = inputs.get(segments[0]) if isinstance(inputs, dict) else None
    remaining = list(segments[1:])
    if isinstance(reference, list):
        return _resolve_input_path(
            nodes, root, root, [*reference, *remaining], seen, depth + 1
        )
    if not isinstance(reference, str) or reference not in nodes:
        raise ConfigError("promotion lock follows path is unresolved")
    if not remaining:
        return reference
    return _resolve_input_path(nodes, root, reference, remaining, seen, depth + 1)


def _follows_records(
    nodes: Mapping[str, Any], root: str, sources: set[str]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for source in sorted(sources):
        inputs = nodes[source].get("inputs")
        if isinstance(inputs, dict):
            for input_name, reference in sorted(inputs.items()):
                if isinstance(reference, list):
                    records.append(
                        {
                            "source": source,
                            "input": input_name,
                            "path": list(reference),
                            "resolvedNode": _resolve_input_path(
                                nodes, root, root, reference, set()
                            ),
                        }
                    )
    return records


def _validate_owned_source(
    value: Any, name: str, *, selected: bool, allow_file: bool
) -> None:
    node = _record(value, f"promotion owned node {name}")
    original = _record(node.get("original"), f"promotion owned node {name} original")
    locked = _record(node.get("locked"), f"promotion owned node {name} locked")
    if selected and ("dir" in original or "dir" in locked):
        raise ConfigError("promotion selected root has an alternate dir selector")
    if not selected and ("dir" in original or "dir" in locked):
        original_dir = original.get("dir")
        locked_dir = locked.get("dir")
        if original_dir != locked_dir or not _safe_relative_dir(original_dir):
            raise ConfigError("promotion owned node has an unsafe dir selector")
    original_type = original.get("type")
    locked_type = locked.get("type")
    if not isinstance(original_type, str) or not isinstance(locked_type, str):
        raise ConfigError("promotion owned source type is invalid")
    if original_type == "indirect":
        identifier = original.get("id")
        if not isinstance(identifier, str) or not identifier or any(char.isspace() for char in identifier):
            raise ConfigError("promotion owned indirect source identity is malformed")
    elif original_type != locked_type:
        raise ConfigError("promotion owned source original and locked types disagree")
    if locked_type == "indirect":
        raise ConfigError("promotion owned indirect source identity is unresolved")
    _require_content_hash(locked)
    if locked_type == "git":
        for record in (original, locked):
            normalize_nix_repository_url(record.get("url"), allow_file=allow_file)
        revision = locked.get("rev")
        if not isinstance(revision, str) or HEX_REVISION.fullmatch(revision) is None:
            raise ConfigError("promotion owned Git source revision is invalid")
    elif locked_type in {"github", "gitlab", "sourcehut"}:
        revision = locked.get("rev")
        if (
            not isinstance(revision, str)
            or HEX_REVISION.fullmatch(revision) is None
            or any(
                not _safe_hosted_part(record.get("owner"))
                or not _safe_hosted_part(record.get("repo"))
                for record in (original, locked)
                if record.get("type") != "indirect"
            )
        ):
            raise ConfigError("promotion owned hosted source identity is invalid")
    elif locked_type in {"file", "tarball"}:
        _validate_hashed_remote_url(original.get("url"))
        _validate_hashed_remote_url(locked.get("url"))
    elif locked_type == "path":
        path = locked.get("path")
        if (
            not isinstance(path, str)
            or not path.startswith("/nix/store/")
            or any(char.isspace() for char in path)
        ):
            raise ConfigError("promotion owned path source is not an immutable store path")
        original_path = original.get("path")
        if (
            isinstance(original_path, str)
            and original_path.startswith("/")
            and not original_path.startswith("/nix/store/")
        ):
            raise ConfigError("promotion owned path source uses a mutable absolute path")
    else:
        raise ConfigError("promotion owned source type is unsupported or not immutable")


def _require_content_hash(locked: Mapping[str, Any]) -> None:
    content_hash = locked.get("narHash", locked.get("hash"))
    if not isinstance(content_hash, str) or not content_hash or any(
        char.isspace() for char in content_hash
    ):
        raise ConfigError("promotion owned source lacks an immutable content identity")


def _validate_hashed_remote_url(value: Any) -> None:
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise ConfigError("promotion owned remote source URL is invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ConfigError("promotion owned remote source URL is unsafe")


def _safe_relative_dir(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and path.as_posix() == value
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _safe_hosted_part(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and all(char.isalnum() or char in "._-" for char in value)
    )


def _record(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be an object")
    return value
