"""Validation helpers for the explicit public composition repository transition route.

This module provides the fail-closed authorization and validation logic for the
exact physical public-composition repository rename from `composition-nd` to
`composition_dinas`, while preserving the logical Nix input key `composition-nd`.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from .deployment_plan import canonical_bytes, sha256_bytes
from .paths import ConfigError
from .promotion_contract import (
    HEX_REVISION,
    contract_dialect,
    normalize_nix_repository_url,
    repository_identity_key,
    resolve_contract_input,
    validate_contract,
    validate_release_ref,
)
from .promotion_lock import (
    DYNAMIC_LOCK_FIELDS,
    _follows_records,
    _owned_closure,
    _prove_exclusive,
    _record,
    _validate_owned_source,
    _validated_graph,
    validate_selected_lock_node,
)


COMPOSITION_INPUT = "composition-nd"
COMPOSITION_AUTHORIZATION_SCHEMA = "nix-update-promotion-owned-repository-transition-v1"
COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_URL = "ssh://git@github.com/Knowledge-Forge-AI/composition-nd.git"
COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL = "ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git"
COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_KEY = repository_identity_key(
    COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_URL
)
COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_KEY = repository_identity_key(
    COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL
)

COMPOSITION_AUTHORIZATION_FIELDS = frozenset(
    {
        "schema",
        "selectedInput",
        "selectedNode",
        "repositoryUrl",
        "previousRepositoryUrl",
        "requestedRevision",
        "preimageOwnedNodes",
        "candidateOwnedNodes",
        "addedNodes",
        "removedNodes",
        "changedNodes",
        "outsideGraphDigest",
        "sharedFollowsDigest",
        "authorizationDigest",
    }
)


def authorize_composition_repository_transition(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    node_name: str,
    revision: str,
    *,
    input_name: str = COMPOSITION_INPUT,
    allow_file: bool = False,
    expected_ref: str = "refs/heads/main",
    allow_implicit_main: bool = True,
) -> dict[str, Any]:
    """Authorize the exact physical public-composition repository transition."""
    if input_name != COMPOSITION_INPUT:
        raise ConfigError(
            f"composition repository transition is valid only for input {COMPOSITION_INPUT}"
        )
    normalized_ref = validate_release_ref(expected_ref)
    if normalized_ref != "refs/heads/main":
        raise ConfigError("composition repository transition requires main release ref")
    if not isinstance(revision, str) or HEX_REVISION.fullmatch(revision) is None:
        raise ConfigError("composition repository transition revision is invalid")

    before_nodes, before_root = _validated_graph(before, "composition transition preimage")
    after_nodes, after_root = _validated_graph(after, "composition transition candidate")
    if before_root != after_root or before.get("version") != after.get("version"):
        raise ConfigError("promotion lock root or version changed")

    before_root_node = _record(before_nodes[before_root], "preimage root node")
    after_root_node = _record(after_nodes[after_root], "candidate root node")
    if before_root_node != after_root_node:
        raise ConfigError("promotion lock root node or direct input mapping changed")

    if node_name == before_root or node_name not in before_nodes or node_name not in after_nodes:
        raise ConfigError("promotion selected lock node identity is invalid")

    root_inputs = before_root_node.get("inputs")
    if not isinstance(root_inputs, dict) or COMPOSITION_INPUT not in root_inputs:
        raise ConfigError(
            f"composition repository transition requires direct root input {COMPOSITION_INPUT}"
        )
    if root_inputs[COMPOSITION_INPUT] != node_name:
        raise ConfigError("composition repository transition selected node mismatch")

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
        raise ConfigError(
            "promotion lock changed nodes outside the selected owned closure"
        )
    before_outside = before_names - union
    after_outside = after_names - union
    if before_outside != after_outside or any(
        before_nodes[name] != after_nodes[name] for name in before_outside
    ):
        raise ConfigError("promotion lock outside graph changed")

    before_selected = _record(before_nodes[node_name], "preimage selected node")
    after_selected = _record(after_nodes[node_name], "candidate selected node")

    # Static fields check
    if {
        k: v
        for k, v in before_selected.items()
        if k not in {"inputs", "locked", "original"}
    } != {
        k: v
        for k, v in after_selected.items()
        if k not in {"inputs", "locked", "original"}
    }:
        raise ConfigError("promotion selected root declaration changed")

    before_orig = _record(before_selected.get("original"), "preimage original")
    after_orig = _record(after_selected.get("original"), "candidate original")
    if "dir" in before_orig or "dir" in after_orig:
        raise ConfigError("composition repository transition does not accept directory selector")
    if before_orig.get("type") != "git" or after_orig.get("type") != "git":
        raise ConfigError("composition repository transition requires Git source type")

    before_orig_url = normalize_nix_repository_url(before_orig.get("url"), allow_file=allow_file)
    after_orig_url = normalize_nix_repository_url(after_orig.get("url"), allow_file=allow_file)
    if (
        repository_identity_key(before_orig_url)
        != COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_KEY
        or repository_identity_key(after_orig_url)
        != COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_KEY
    ):
        raise ConfigError(
            "composition repository transition authorizes only the exact "
            "composition-nd to composition_dinas transition"
        )

    if {k: v for k, v in before_orig.items() if k != "url"} != {
        k: v for k, v in after_orig.items() if k != "url"
    }:
        raise ConfigError("composition repository transition changed original static fields")

    before_locked = _record(before_selected.get("locked"), "preimage locked")
    after_locked = _record(after_selected.get("locked"), "candidate locked")
    if "dir" in before_locked or "dir" in after_locked:
        raise ConfigError("composition repository transition does not accept directory selector")
    if before_locked.get("type") != "git" or after_locked.get("type") != "git":
        raise ConfigError("composition repository transition requires Git locked type")

    before_locked_url = normalize_nix_repository_url(before_locked.get("url"), allow_file=allow_file)
    after_locked_url = normalize_nix_repository_url(after_locked.get("url"), allow_file=allow_file)
    if (
        repository_identity_key(before_locked_url)
        != COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_KEY
        or repository_identity_key(after_locked_url)
        != COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_KEY
    ):
        raise ConfigError(
            "composition repository transition authorizes only the exact "
            "composition-nd to composition_dinas transition"
        )

    if any(
        before_locked.get(k) != after_locked.get(k)
        for k in (set(before_locked) | set(after_locked)) - (DYNAMIC_LOCK_FIELDS | {"url"})
    ):
        raise ConfigError("composition repository transition changed static locked fields")

    if after_locked.get("rev") != revision:
        raise ConfigError("candidate does not select requested revision")

    before_rev = before_locked.get("rev")
    if not isinstance(before_rev, str) or HEX_REVISION.fullmatch(before_rev) is None:
        raise ConfigError("preimage revision is invalid")

    validate_selected_lock_node(
        before,
        node_name,
        COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_URL,
        before_rev,
        allow_file=allow_file,
        expected_ref=expected_ref,
        allow_implicit_main=allow_implicit_main,
    )
    validate_selected_lock_node(
        after,
        node_name,
        COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL,
        revision,
        allow_file=allow_file,
        expected_ref=expected_ref,
        allow_implicit_main=allow_implicit_main,
    )

    common_owned = before_owned & after_owned
    before_common_follows = _follows_records(before_nodes, before_root, common_owned)
    after_common_follows = _follows_records(after_nodes, after_root, common_owned)
    if before_common_follows != after_common_follows:
        raise ConfigError(
            "promotion changed a common owned node follows edge or resolved target"
        )

    candidate_follows = _follows_records(after_nodes, after_root, after_owned)
    for rec in candidate_follows:
        if rec["source"] in added:
            target = rec["resolvedNode"]
            if target in union or target not in before_outside:
                raise ConfigError(
                    "promotion added owned node follows an owned or changed target"
                )

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
        "schema": COMPOSITION_AUTHORIZATION_SCHEMA,
        "selectedInput": COMPOSITION_INPUT,
        "selectedNode": node_name,
        "repositoryUrl": COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL,
        "previousRepositoryUrl": COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_URL,
        "requestedRevision": revision,
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
    validate_composition_repository_authorization(record)
    return record


def validate_composition_repository_authorization(value: Any) -> dict[str, Any]:
    """Validate retained composition repository transition authorization record."""
    if not isinstance(value, dict):
        raise ConfigError("composition repository transition authorization must be an object")
    if set(value) != COMPOSITION_AUTHORIZATION_FIELDS:
        raise ConfigError(
            "composition repository transition authorization fields are invalid"
        )
    if value.get("schema") != COMPOSITION_AUTHORIZATION_SCHEMA:
        raise ConfigError(
            "composition repository transition authorization schema is invalid"
        )
    if value.get("selectedInput") != COMPOSITION_INPUT:
        raise ConfigError("composition transition selected input must be composition-nd")
    if not isinstance(value.get("selectedNode"), str) or not value["selectedNode"]:
        raise ConfigError("composition transition selected node identity is invalid")
    if value.get("repositoryUrl") != COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL:
        raise ConfigError(
            "composition transition destination repository URL evidence is invalid"
        )
    if (
        value.get("previousRepositoryUrl")
        != COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_URL
    ):
        raise ConfigError(
            "composition transition preimage repository URL evidence is invalid"
        )
    if (
        not isinstance(value.get("requestedRevision"), str)
        or HEX_REVISION.fullmatch(value["requestedRevision"]) is None
    ):
        raise ConfigError("composition transition requested revision evidence is invalid")

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
            raise ConfigError(
                "composition transition node identity evidence is not canonical"
            )

    if value["selectedNode"] not in value["changedNodes"]:
        raise ConfigError("composition transition changed-node evidence is invalid")

    for field in (
        "outsideGraphDigest",
        "sharedFollowsDigest",
        "authorizationDigest",
    ):
        digest = value.get(field)
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ConfigError("composition transition authorization digest is invalid")

    if value["authorizationDigest"] != _authorization_digest(value):
        raise ConfigError(
            "composition transition authorization digest does not match content"
        )
    return dict(value)


def _authorization_digest(value: Mapping[str, Any]) -> str:
    material = dict(value)
    material["authorizationDigest"] = ""
    return sha256_bytes(canonical_bytes(material))


def validate_composition_transition_contract(
    entry_contract: Mapping[str, Any],
    candidate_contract: Mapping[str, Any],
    *,
    allow_file: bool = False,
) -> None:
    """Validate that candidate contract changes are strictly limited to the authorized rename."""
    validate_contract(entry_contract, allow_file_urls=allow_file)
    validate_contract(candidate_contract, allow_file_urls=allow_file)

    dialect = contract_dialect(entry_contract)
    cand_dialect = contract_dialect(candidate_contract)
    if dialect != cand_dialect:
        raise ConfigError("composition transition contract dialect changed")

    entry_res = resolve_contract_input(entry_contract, COMPOSITION_INPUT)
    cand_res = resolve_contract_input(candidate_contract, COMPOSITION_INPUT)

    if (
        repository_identity_key(
            normalize_nix_repository_url(entry_res["repository_url"], allow_file=allow_file)
        )
        != COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_KEY
    ):
        raise ConfigError(
            "entry promotion contract does not resolve composition-nd to the preimage repository"
        )
    if (
        repository_identity_key(
            normalize_nix_repository_url(cand_res["repository_url"], allow_file=allow_file)
        )
        != COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_KEY
    ):
        raise ConfigError(
            "candidate promotion contract does not resolve composition-nd to the candidate repository"
        )

    if dialect == "layered":
        entry_req = entry_contract["requiredLowerPromotion"]
        cand_req = candidate_contract["requiredLowerPromotion"]
        if entry_req != cand_req:
            raise ConfigError("composition transition changed requiredLowerPromotion")
        if entry_req["candidateInput"] != COMPOSITION_INPUT:
            raise ConfigError("layered requiredLowerPromotion candidateInput must be composition-nd")

        entry_lower = entry_contract["lowerPromotion"]
        cand_lower = candidate_contract["lowerPromotion"]
        entry_lower_copy = copy.deepcopy(entry_lower)
        cand_lower_copy = copy.deepcopy(cand_lower)
        entry_lower_copy["composition"]["repositoryUrl"] = cand_lower_copy["composition"]["repositoryUrl"]
        if canonical_bytes(entry_lower_copy) != canonical_bytes(cand_lower_copy):
            raise ConfigError("lower promotion contract drifted beyond the authorized repository URL transition")

        entry_copy = copy.deepcopy(entry_contract)
        cand_copy = copy.deepcopy(candidate_contract)
        entry_copy["lowerPromotion"] = cand_copy["lowerPromotion"]
        if canonical_bytes(entry_copy) != canonical_bytes(cand_copy):
            raise ConfigError("candidate promotion contract drifted beyond the authorized lower promotion transition")
    else:
        # direct contract
        entry_copy = copy.deepcopy(entry_contract)
        cand_copy = copy.deepcopy(candidate_contract)
        for mod in entry_copy.get("modules", []):
            if mod.get("input") == COMPOSITION_INPUT:
                mod["url"] = cand_res["repository_url"]
        if canonical_bytes(entry_copy) != canonical_bytes(cand_copy):
            raise ConfigError("candidate promotion contract drifted beyond the authorized module URL transition")
