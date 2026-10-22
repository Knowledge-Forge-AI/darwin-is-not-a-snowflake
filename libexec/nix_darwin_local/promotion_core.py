"""Validation helpers for the small, explicit core promotion route.

The normal promotion contract intentionally accepts only registered domain
inputs.  The core input is a different binding: it is the direct public
composition input at the root or the historical ``nix-darwin`` selector. This
module keeps that binding separate from :mod:`promotion_lock`'s registered
domain validator while reusing its lock graph validation primitives.

Core promotion is deliberately narrow.  A candidate may move the requested
core revision and the content fields that Nix derives for that revision.  The
core node declaration (except an explicitly authorized layout cut), its input/follows topology, every child node in its
owned closure, and all nodes outside that closure must remain unchanged.  A
broader core release is reported as unsupported so the coordinator can retain
the evidence and stop for a separately authorized migration.
"""

from __future__ import annotations

from typing import Any, Mapping

from .deployment_plan import canonical_bytes, sha256_bytes
from .paths import ConfigError
from .promotion_contract import HEX_REVISION, normalize_nix_repository_url, repository_identity_key
from .promotion_lock import (
    DYNAMIC_LOCK_FIELDS,
    _owned_closure,
    _record,
    _validated_graph,
)


CORE_INPUT = "core"
CORE_SUBFLAKE = "nix-darwin"
CORE_AUTHORIZATION_SCHEMA = "nix-update-core-lock-transition-v1"
CORE_LAYOUT_AUTHORIZATION_SCHEMA = "nix-update-core-layout-transition-v1"
CORE_REPOSITORY_AUTHORIZATION_SCHEMA = "nix-update-core-repository-transition-v1"
CORE_REPOSITORY_TRANSITION_PREIMAGE_URL = "ssh://git@github.com/Knowledge-Forge-AI/.flakes.git"
CORE_REPOSITORY_TRANSITION_CANDIDATE_URL = (
    "ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git"
)
CORE_REPOSITORY_TRANSITION_PREIMAGE_KEY = repository_identity_key(
    CORE_REPOSITORY_TRANSITION_PREIMAGE_URL
)
CORE_REPOSITORY_TRANSITION_CANDIDATE_KEY = repository_identity_key(
    CORE_REPOSITORY_TRANSITION_CANDIDATE_URL
)
CORE_AUTHORIZATION_FIELDS = frozenset(
    {
        "schema",
        "selectedInput",
        "selectedNode",
        "repositoryUrl",
        "subflake",
        "requestedRevision",
        "preimageOwnedNodes",
        "candidateOwnedNodes",
        "changedNodes",
        "outsideGraphDigest",
        "childGraphDigest",
        "authorizationDigest",
    }
)

# ``dir`` is intentionally accepted for this route even though it is rejected
# by validate_selected_lock_node for registered module inputs.
_CORE_ORIGINAL_FIELDS = frozenset({"type", "url", "ref", "submodules", "dir"})
_CORE_LOCKED_FIELDS = frozenset(
    {"type", "url", "ref", "submodules", "dir", *DYNAMIC_LOCK_FIELDS}
)


def resolve_core_node(
    lock: Mapping[str, Any], *, input_name: str = CORE_INPUT
) -> str:
    """Resolve the direct public ``core`` input and return its node identity.

    The first core route intentionally accepts only the direct root input.
    A nested path such as ``composition-nd/core`` belongs to a later matched
    lower-layer transaction and must be carried by that composition's normal
    promotion result.
    """

    if input_name != CORE_INPUT:
        raise ConfigError("core promotion selected input must be exactly core")
    nodes, root = _validated_graph(lock, "core promotion")
    root_node = _record(nodes[root], "core promotion root node")
    inputs = root_node.get("inputs")
    if not isinstance(inputs, dict) or input_name not in inputs:
        raise ConfigError("core promotion requires a direct root core input")
    reference = inputs[input_name]
    if not isinstance(reference, str) or reference not in nodes:
        raise ConfigError("core promotion direct core input is unresolved")
    return reference


def core_input_revision(lock: Mapping[str, Any], *, input_name: str = CORE_INPUT) -> str:
    """Return the selected core revision after validating its source shape."""

    node_name = resolve_core_node(lock, input_name=input_name)
    node = _record(lock["nodes"][node_name], "core promotion selected node")
    locked = _record(node.get("locked"), "core promotion selected resolution")
    revision = locked.get("rev")
    if not isinstance(revision, str) or HEX_REVISION.fullmatch(revision) is None:
        raise ConfigError("core promotion selected revision is invalid")
    return revision


def authorize_core_lock_transition(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    revision: str,
    *,
    repository_url: str | None = None,
    input_name: str = CORE_INPUT,
    expected_subflake: str | None = None,
    allow_file: bool = False,
    allow_layout_transition: bool = False,
    allow_repository_transition: bool = False,
) -> dict[str, Any]:
    """Authorize the bounded first core lock transition.

    ``before`` and ``after`` are the normalized lock documents already
    produced by DINAS's isolated lock primitive.  The function does not read
    or mutate a repository.  It returns a digest-bound authorization record
    suitable for a campaign stage or native plan.
    """

    if allow_layout_transition and allow_repository_transition:
        raise ConfigError("core promotion cannot combine layout and repository transitions")
    if input_name != CORE_INPUT:
        raise ConfigError("core promotion selected input must be exactly core")
    if expected_subflake is None:
        selected = _record(before["nodes"][resolve_core_node(before)], "core promotion selected node")
        expected_subflake = _record(selected.get("original"), "core promotion original source").get("dir", "")
    if allow_repository_transition and expected_subflake != "":
        raise ConfigError("core repository transition requires the root layout preimage")
    if expected_subflake not in ("", CORE_SUBFLAKE):
        raise ConfigError("core promotion subflake selector is invalid")
    if not isinstance(revision, str) or HEX_REVISION.fullmatch(revision) is None:
        raise ConfigError("core promotion revision is invalid")

    before_nodes, before_root = _validated_graph(before, "core promotion preimage")
    after_nodes, after_root = _validated_graph(after, "core promotion candidate")
    if before_root != after_root or before.get("version") != after.get("version"):
        raise ConfigError("core promotion lock root or version changed")

    before_root_node = _record(before_nodes[before_root], "core promotion preimage root")
    after_root_node = _record(after_nodes[after_root], "core promotion candidate root")
    before_inputs = before_root_node.get("inputs")
    after_inputs = after_root_node.get("inputs")
    if not isinstance(before_inputs, dict) or not isinstance(after_inputs, dict):
        raise ConfigError("core promotion lock root inputs are invalid")
    if before_inputs != after_inputs:
        raise ConfigError("core promotion changed the root input topology")

    before_node_name = resolve_core_node(before, input_name=input_name)
    after_node_name = resolve_core_node(after, input_name=input_name)
    if before_node_name != after_node_name:
        raise ConfigError("core promotion selected root node name changed")

    # The core node's string-edge closure is useful evidence.  The first route
    # still requires all nodes in that closure except the selected node to be
    # exact-equal, which catches a changed child lock or topology.
    before_owned = _owned_closure(before_nodes, before_node_name, "core promotion preimage")
    after_owned = _owned_closure(after_nodes, after_node_name, "core promotion candidate")
    if before_owned != after_owned:
        raise ConfigError(
            "core promotion requires unchanged core child inputs and lock topology; "
            "broader core release is unsupported"
        )

    before_names = set(before_nodes)
    after_names = set(after_nodes)
    if before_names != after_names:
        raise ConfigError(
            "core promotion requires unchanged core child inputs and lock topology; "
            "broader core release is unsupported"
        )
    changed = {
        name for name in before_names if before_nodes[name] != after_nodes[name]
    }
    if changed != {before_node_name}:
        raise ConfigError(
            "core promotion requires the public core node to move alone; "
            "broader core release is unsupported"
        )

    before_node = _record(before_nodes[before_node_name], "core promotion preimage node")
    after_node = _record(after_nodes[after_node_name], "core promotion candidate node")
    target_subflake = "" if allow_layout_transition else expected_subflake
    if allow_layout_transition and expected_subflake != CORE_SUBFLAKE:
        raise ConfigError("core layout transition requires the historical preimage")
    before_original = _validate_core_source(
        before_node, "core promotion preimage", expected_subflake=expected_subflake,
        allow_file=allow_file,
    )
    after_original = _validate_core_source(
        after_node, "core promotion candidate", expected_subflake=target_subflake,
        allow_file=allow_file,
    )
    if allow_repository_transition:
        before_key = repository_identity_key(
            normalize_nix_repository_url(before_original["url"], allow_file=allow_file)
        )
        after_key = repository_identity_key(
            normalize_nix_repository_url(after_original["url"], allow_file=allow_file)
        )
        if (
            before_key != CORE_REPOSITORY_TRANSITION_PREIMAGE_KEY
            or after_key != CORE_REPOSITORY_TRANSITION_CANDIDATE_KEY
        ):
            raise ConfigError(
                "core repository transition authorizes only the exact "
                ".flakes to darwin-is-not-a-snowflake transition"
            )

    def declaration(node: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(node)
        if allow_layout_transition:
            result.pop("dir", None)
        if allow_repository_transition:
            result.pop("url", None)
        return result

    if declaration(before_original) != declaration(after_original) or declaration(before_node["original"]) != declaration(after_node["original"]):
        raise ConfigError("core promotion changed the core source declaration")
    if {key: value for key, value in before_node.items() if key not in {"locked", "original"}} != {
        key: value for key, value in after_node.items() if key not in {"locked", "original"}
    }:
        raise ConfigError("core promotion changed the core node declaration or topology")
    if repository_url is not None:
        expected_url = repository_identity_key(
            normalize_nix_repository_url(repository_url, allow_file=allow_file)
        )
        if repository_identity_key(
            normalize_nix_repository_url(before_original["url"], allow_file=allow_file)
        ) != expected_url:
            raise ConfigError("core promotion core repository URL mismatched")

    before_inputs = before_node.get("inputs", {})
    after_inputs = after_node.get("inputs", {})
    if before_inputs != after_inputs:
        raise ConfigError(
            "core promotion requires unchanged core child inputs and lock topology; "
            "broader core release is unsupported"
        )

    before_locked = _record(before_node.get("locked"), "core promotion preimage resolution")
    after_locked = _record(after_node.get("locked"), "core promotion candidate resolution")
    ignored_lock_fields = DYNAMIC_LOCK_FIELDS | (
        {"dir"} if allow_layout_transition else set()
    ) | (
        {"url"} if allow_repository_transition else set()
    )
    if any(
        before_locked.get(key) != after_locked.get(key)
        for key in (set(before_locked) | set(after_locked)) - ignored_lock_fields
    ):
        raise ConfigError("core promotion changed the core repository selector or source identity")
    if after_locked.get("rev") != revision:
        raise ConfigError("core promotion candidate does not select the requested revision")
    _validate_revision_and_hash(before_locked, "core promotion preimage resolution")
    _validate_revision_and_hash(after_locked, "core promotion candidate resolution")

    # The child digest makes the bounded topology rule explicit in retained
    # evidence.  The outside digest is useful during apply revalidation.
    outside_material = {
        "root": before_root,
        "version": before.get("version"),
        "nodes": {name: before_nodes[name] for name in sorted(before_names - before_owned)},
    }
    child_material = {
        "nodes": {
            name: before_nodes[name]
            for name in sorted(before_owned - {before_node_name})
        },
        "rootInputs": before_root_node.get("inputs"),
        "coreInputs": before_inputs,
    }
    if allow_repository_transition:
        schema = CORE_REPOSITORY_AUTHORIZATION_SCHEMA
    elif allow_layout_transition:
        schema = CORE_LAYOUT_AUTHORIZATION_SCHEMA
    else:
        schema = CORE_AUTHORIZATION_SCHEMA

    record: dict[str, Any] = {
        "schema": schema,
        "selectedInput": input_name,
        "selectedNode": before_node_name,
        "repositoryUrl": (
            CORE_REPOSITORY_TRANSITION_CANDIDATE_URL
            if allow_repository_transition
            else str(before_original["url"])
        ),
        "subflake": target_subflake,
        "requestedRevision": revision,
        "preimageOwnedNodes": sorted(before_owned),
        "candidateOwnedNodes": sorted(after_owned),
        "changedNodes": [before_node_name],
        "outsideGraphDigest": sha256_bytes(canonical_bytes(outside_material)),
        "childGraphDigest": sha256_bytes(canonical_bytes(child_material)),
        "authorizationDigest": "",
    }
    if allow_layout_transition:
        record["previousSubflake"] = expected_subflake
    if allow_repository_transition:
        record["previousRepositoryUrl"] = CORE_REPOSITORY_TRANSITION_PREIMAGE_URL
    record["authorizationDigest"] = _authorization_digest(record)
    validate_core_lock_authorization(record)
    return record


def validate_core_lock_authorization(value: Any) -> dict[str, Any]:
    """Validate the shape and digest of a retained core authorization record."""

    if not isinstance(value, dict):
        raise ConfigError("core promotion authorization fields are invalid")
    schema = value.get("schema")
    layout = schema == CORE_LAYOUT_AUTHORIZATION_SCHEMA
    repo_trans = schema == CORE_REPOSITORY_AUTHORIZATION_SCHEMA
    if layout:
        fields = CORE_AUTHORIZATION_FIELDS | {"previousSubflake"}
    elif repo_trans:
        fields = CORE_AUTHORIZATION_FIELDS | {"previousRepositoryUrl"}
    else:
        fields = CORE_AUTHORIZATION_FIELDS

    if set(value) != fields:
        raise ConfigError("core promotion authorization fields are invalid")
    if schema not in (
        CORE_AUTHORIZATION_SCHEMA,
        CORE_LAYOUT_AUTHORIZATION_SCHEMA,
        CORE_REPOSITORY_AUTHORIZATION_SCHEMA,
    ):
        raise ConfigError("core promotion authorization schema is invalid")
    if value.get("selectedInput") != CORE_INPUT or not isinstance(value.get("selectedNode"), str):
        raise ConfigError("core promotion selected input identity is invalid")
    if not isinstance(value.get("repositoryUrl"), str) or not value["repositoryUrl"]:
        raise ConfigError("core promotion repository URL evidence is invalid")
    if value.get("subflake") not in ("", CORE_SUBFLAKE):
        raise ConfigError("core promotion subflake evidence is invalid")
    if layout and (value.get("previousSubflake") != CORE_SUBFLAKE or value["subflake"] != ""):
        raise ConfigError("core layout transition evidence is invalid")
    if repo_trans:
        if value.get("repositoryUrl") != CORE_REPOSITORY_TRANSITION_CANDIDATE_URL:
            raise ConfigError("core repository transition destination URL evidence is invalid")
        if value.get("previousRepositoryUrl") != CORE_REPOSITORY_TRANSITION_PREIMAGE_URL:
            raise ConfigError("core repository transition preimage URL evidence is invalid")
        if value.get("subflake") != "":
            raise ConfigError("core repository transition subflake evidence is invalid")
    if not isinstance(value.get("requestedRevision"), str) or HEX_REVISION.fullmatch(
        value["requestedRevision"]
    ) is None:
        raise ConfigError("core promotion requested revision evidence is invalid")
    for field in ("preimageOwnedNodes", "candidateOwnedNodes", "changedNodes"):
        identities = value.get(field)
        if (
            not isinstance(identities, list)
            or any(not isinstance(item, str) or not item for item in identities)
            or identities != sorted(identities)
            or len(identities) != len(set(identities))
        ):
            raise ConfigError("core promotion node identity evidence is not canonical")
    if value["changedNodes"] != [value["selectedNode"]]:
        raise ConfigError("core promotion changed-node evidence is invalid")
    for field in ("outsideGraphDigest", "childGraphDigest", "authorizationDigest"):
        digest = value.get(field)
        if not isinstance(digest, str) or len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise ConfigError("core promotion authorization digest is invalid")
    if value["authorizationDigest"] != _authorization_digest(value):
        raise ConfigError("core promotion authorization digest does not match content")
    return dict(value)


def _validate_core_source(
    node: Mapping[str, Any],
    label: str,
    *,
    expected_subflake: str,
    allow_file: bool,
) -> dict[str, Any]:
    original = _record(node.get("original"), f"{label} original source")
    locked = _record(node.get("locked"), f"{label} locked source")
    if expected_subflake not in ("", CORE_SUBFLAKE):
        raise ConfigError(f"{label} unsupported core layout")
    if expected_subflake == "" and ("dir" in original or "dir" in locked):
        raise ConfigError(f"{label} root source must omit the directory selector")
    if not set(original) <= _CORE_ORIGINAL_FIELDS or original.get("dir", "") != expected_subflake:
        raise ConfigError(f"{label} source declaration must select {expected_subflake}")
    if not set(locked) <= _CORE_LOCKED_FIELDS or locked.get("dir", "") != expected_subflake:
        raise ConfigError(f"{label} source resolution must select {expected_subflake}")
    if original.get("type") != "git" or locked.get("type") != "git":
        raise ConfigError(f"{label} source must retain Git identity")
    original_url = normalize_nix_repository_url(original.get("url"), allow_file=allow_file)
    locked_url = normalize_nix_repository_url(locked.get("url"), allow_file=allow_file)
    if repository_identity_key(original_url) != repository_identity_key(locked_url):
        raise ConfigError(f"{label} source original and locked URLs disagree")
    _validate_main_ref(original, f"{label} original source")
    _validate_main_ref(locked, f"{label} locked source")
    return {"type": "git", "url": original_url, "dir": expected_subflake}


def _validate_main_ref(record: Mapping[str, Any], label: str) -> None:
    value = record.get("ref")
    if value not in (None, "main", "refs/heads/main"):
        raise ConfigError(f"{label} uses a non-main release ref")


def _validate_revision_and_hash(record: Mapping[str, Any], label: str) -> None:
    revision = record.get("rev")
    if not isinstance(revision, str) or HEX_REVISION.fullmatch(revision) is None:
        raise ConfigError(f"{label} revision is invalid")
    content_hash = record.get("narHash", record.get("hash"))
    if not isinstance(content_hash, str) or not content_hash or any(
        char.isspace() for char in content_hash
    ):
        raise ConfigError(f"{label} lacks an immutable content identity")


def _authorization_digest(value: Mapping[str, Any]) -> str:
    material = dict(value)
    material["authorizationDigest"] = ""
    return sha256_bytes(canonical_bytes(material))
