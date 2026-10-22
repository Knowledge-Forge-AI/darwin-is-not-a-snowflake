"""Deployment bindings for catalog-derived immutable Git source realizations."""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any, Mapping

from .paths import ConfigError, validate_input_path, validate_portable_path
from .promotion_contract import HEX_REVISION, SAFE_IDENTITY, repository_urls_match, validate_release_ref


def capture_local_sources(workflow: Any, lock: Mapping[str, Any]) -> list[dict[str, Any]]:
    from .local_source import realize_catalog_locked_nodes

    if not getattr(workflow, "sources", ()):
        return []
    realized = realize_catalog_locked_nodes(workflow, lock, workflow.sources)
    records = [{"node": node, "source": dict(source)} for node, source in sorted(realized.items())]
    validate_local_sources(records, lock)
    return records


def validate_local_sources(records: Any, lock: Mapping[str, Any], *, strict: bool = False) -> None:
    if not isinstance(records, list):
        raise ConfigError("local source bindings must be a list")
    if not isinstance(lock, Mapping) or not isinstance(lock.get("nodes", {}), Mapping):
        raise ConfigError("local source lock graph must be an object")
    seen: set[str] = set()
    fields = {
        "key", "checkout", "repository", "release_ref", "revision", "head_ref",
        "head_revision", "clean", "operation_state", "store_path", "nar_hash", "publication",
    }
    for record in records:
        if not isinstance(record, dict) or set(record) != {"node", "source"}:
            raise ConfigError("local source binding fields are invalid")
        node, source = record["node"], record["source"]
        if not isinstance(node, str) or node in seen:
            raise ConfigError("local source binding node is invalid or duplicated")
        seen.add(node)
        if not isinstance(source, dict) or set(source) != fields:
            raise ConfigError("local source identity fields are invalid")
        if any(not isinstance(source[key], str) or not source[key] for key in fields - {"clean", "publication"}):
            raise ConfigError("local source identity has invalid field types")
        if (not isinstance(source["clean"], bool) or source["operation_state"] != "clean"
                or HEX_REVISION.fullmatch(source["revision"]) is None
                or (strict and (source["clean"] is not True or source["revision"] != source["head_revision"] or source["head_ref"] != source["release_ref"]))
                or not source["checkout"].startswith("/")
                or re.fullmatch(r"/nix/store/[0-9a-z]{32}-[^/]+", source["store_path"]) is None
                or re.fullmatch(r"sha256-[A-Za-z0-9+/]{43}=", source["nar_hash"]) is None):
            raise ConfigError("local source identity is not an exact immutable commit")
        validate_input_path(source["key"])
        validate_portable_path(source["checkout"])
        validate_release_ref(source["release_ref"])
        publication = source["publication"]
        if publication is not None:
            if not isinstance(publication, dict) or set(publication) != {"remote", "ref"}:
                raise ConfigError("local source publication metadata is invalid")
            if not isinstance(publication["remote"], str) or SAFE_IDENTITY.fullmatch(publication["remote"]) is None:
                raise ConfigError("local source publication remote is invalid")
            validate_release_ref(publication["ref"])
        locked_node = lock.get("nodes", {}).get(node, {})
        if not isinstance(locked_node, Mapping) or not isinstance(locked_node.get("locked", {}), Mapping):
            raise ConfigError("local source locked node is invalid")
        locked = locked_node.get("locked", {})
        if (locked.get("type") != "git" or locked.get("rev") != source["revision"]
                or locked.get("narHash") != source["nar_hash"]
                or not repository_urls_match(locked.get("url"), source["repository"])):
            raise ConfigError("local source realization differs from the portable lock")


def revalidate_local_sources(workflow: Any, lock: Mapping[str, Any], records: Any) -> None:
    validate_local_sources(records, lock)
    fresh_records = capture_local_sources(workflow, lock)
    if len(fresh_records) != len(records):
        raise ConfigError("catalog local source authority drifted from reviewed bindings")
    bound_keys = {"node", "key", "checkout", "repository", "revision", "store_path", "nar_hash"}
    for fresh, reviewed in zip(fresh_records, records):
        if fresh["node"] != reviewed["node"]:
            raise ConfigError("catalog local source authority drifted from reviewed bindings")
        fresh_src = {k: v for k, v in fresh["source"].items() if k in bound_keys}
        reviewed_src = {k: v for k, v in reviewed["source"].items() if k in bound_keys}
        if fresh_src != reviewed_src:
            raise ConfigError("catalog local source authority drifted from reviewed bindings")


def revalidate_composition_catalog(
    workflow: Any, record: Mapping[str, Any], revision: str, *, installed_lock: bool = False,
) -> None:
    from .local_source import check_git_operations, observe_source
    from .source_catalog import lookup_by_key

    entry = lookup_by_key(getattr(workflow, "sources", ()), record["key"])
    if entry is None:
        raise ConfigError("reviewed composition source catalog entry is unavailable")
    actual = asdict(entry)
    actual["checkout"] = str(entry.checkout.resolve())
    if actual != record:
        raise ConfigError("composition catalog authority drifted from the reviewed plan")
    if not installed_lock:
        observe_source(workflow, entry, revision, expected_repository=record["repository"], expected_ref=record["release_ref"])
        return
    # The reviewed lock is deliberately installed/staged at this boundary.
    # Its exact bytes/index are checked by promotion; operation state is still
    # required to remain idle immediately before committing that one path.
    check_git_operations(workflow, entry.checkout, entry.key)
