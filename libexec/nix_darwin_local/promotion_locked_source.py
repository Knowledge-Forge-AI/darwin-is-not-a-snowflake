"""Promotion-only bindings between reviewed release heads and exact lock sources."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .local_source import (
    _normalize_locked_ref, coerce_catalog_entry, materialize_source,
    realize_historical_source, validate_source_identity,
)
from .paths import ConfigError
from .promotion_contract import HEX_REVISION, repository_urls_match
from .promotion_lock import selected_owned_closure

PROMOTION_LOCAL_SOURCE_BINDING_SCHEMA_V2 = "nix-update-promotion-local-source-binding-v2"
_STORE_PATH = re.compile(r"/nix/store/[0-9a-z]{32}-[^/]+")
_NAR_HASH = re.compile(r"sha256-[A-Za-z0-9+/]{43}=")


def _strict_nodes(candidate, preimage, selected_node):
    if not isinstance(preimage, Mapping):
        raise ConfigError("promotion source bindings require an explicit preimage lock")
    return (selected_owned_closure(candidate, selected_node)
            | selected_owned_closure(preimage, selected_node))


def _catalog_entries(sources):
    if hasattr(sources, "entries"):
        sources = sources.entries
    elif isinstance(sources, Mapping):
        sources = sources.values()
    return [coerce_catalog_entry(entry) for entry in sources]


def _matching_entry(entries, name, locked):
    matches = [entry for entry in entries
               if repository_urls_match(entry.repository, locked.get("url"), allow_file=True)]
    if len(matches) > 1:
        exact = [entry for entry in matches if entry.key == name]
        if len(exact) != 1:
            raise ConfigError(f"ambiguous catalog entries for locked node {name}")
        return exact[0]
    return matches[0] if matches else None


def _locked_ref(declaration):
    if not isinstance(declaration, Mapping):
        raise ConfigError("local source lock declaration is invalid")
    ref = declaration.get("ref")
    return "refs/heads/main" if ref is None else _normalize_locked_ref(ref)


def _validate_realization(realization, source, node):
    if not isinstance(realization, dict) or set(realization) != {"revision", "store_path", "nar_hash"}:
        raise ConfigError("local source realization fields are invalid")
    patterns = {"revision": HEX_REVISION, "store_path": _STORE_PATH, "nar_hash": _NAR_HASH}
    for field, pattern in patterns.items():
        if not isinstance(realization[field], str) or pattern.fullmatch(realization[field]) is None:
            raise ConfigError(f"local source realization {field} is invalid")
    locked = node.get("locked")
    if not isinstance(locked, Mapping) or locked.get("type") != "git":
        raise ConfigError("local source binding must identify a Git lock node")
    if (locked.get("rev") != realization["revision"]
            or locked.get("narHash") != realization["nar_hash"]
            or not repository_urls_match(locked.get("url"), source["repository"], allow_file=True)):
        raise ConfigError("local source realization differs from the portable lock")
    if any(_locked_ref(part) != source["release_ref"]
           for part in (locked, node.get("original", {}))):
        raise ConfigError("local source release ref differs from the portable lock")


def _role(name, source, realization, strict, candidate, preimage):
    if source["revision"] == realization["revision"]:
        return "exact"
    if name in strict or candidate["nodes"][name] != preimage["nodes"].get(name):
        raise ConfigError("selected owned or changed source requires exact release revision")
    return "unchanged-lock"


def _capture_record(workflow, entry, name, node, historical):
    locked = node["locked"]
    if _locked_ref(locked) != entry.release_ref:
        raise ConfigError(f"locked node {name} ref differs from catalog release ref")
    if historical:
        observed, realization = realize_historical_source(
            workflow, entry, locked.get("rev"), expected_repository=locked.get("url"),
            expected_ref=entry.release_ref, expected_nar_hash=locked.get("narHash"),
        )
        source = observed.as_dict()
    else:
        # Selected and owned sources use the original strict materializer.
        observed = materialize_source(
            workflow, entry, locked.get("rev"),
            expected_repository=locked.get("url"), expected_ref=entry.release_ref,
        )
        source = observed.as_dict()
        realization = {"revision": observed.revision,
                       "store_path": source.pop("store_path"), "nar_hash": source.pop("nar_hash")}
        source.update(store_path=None, nar_hash=None)
    return {"schema": PROMOTION_LOCAL_SOURCE_BINDING_SCHEMA_V2,
            "node": name, "source": source, "realization": realization}


def capture_promotion_local_sources(
    workflow: Any, candidate_lock: Mapping[str, Any], *,
    preimage_lock: Mapping[str, Any], selected_node: str,
) -> list[dict[str, Any]]:
    """Capture only closure-derived permissions, never a caller-supplied role."""
    strict = _strict_nodes(candidate_lock, preimage_lock, selected_node)
    entries = _catalog_entries(getattr(workflow, "sources", ()))
    records = []
    for name, node in sorted(candidate_lock["nodes"].items()):
        locked = node.get("locked")
        if not isinstance(locked, Mapping) or locked.get("type") != "git":
            continue
        entry = _matching_entry(entries, name, locked)
        if entry is None:
            continue
        historical = name not in strict and node == preimage_lock["nodes"].get(name)
        record = _capture_record(workflow, entry, name, node, historical)
        record["role"] = _role(name, record["source"], record["realization"],
                               strict, candidate_lock, preimage_lock)
        records.append(record)
    validate_promotion_local_sources(records, candidate_lock,
                                    preimage_lock=preimage_lock, selected_node=selected_node)
    return records


def validate_promotion_local_sources(
    records: Any, candidate_lock: Mapping[str, Any], *,
    preimage_lock: Mapping[str, Any], selected_node: str,
) -> None:
    """Validate truthful versioned records and independently derive their roles."""
    if not isinstance(records, list):
        raise ConfigError("local source bindings must be a list")
    strict = _strict_nodes(candidate_lock, preimage_lock, selected_node)
    seen = set()
    fields = {"schema", "node", "role", "source", "realization"}
    for record in records:
        if not isinstance(record, dict) or set(record) != fields:
            raise ConfigError("local source binding fields are invalid")
        if record["schema"] != PROMOTION_LOCAL_SOURCE_BINDING_SCHEMA_V2:
            raise ConfigError("local source binding schema is invalid")
        name = record["node"]
        if not isinstance(name, str) or name in seen or name not in candidate_lock["nodes"]:
            raise ConfigError("local source binding node is invalid or duplicated")
        seen.add(name)
        source = record["source"]
        validate_source_identity(source, materialized=False)
        if source["store_path"] is not None or source["nar_hash"] is not None:
            raise ConfigError("local source observation must be unmaterialized")
        _validate_realization(record["realization"], source, candidate_lock["nodes"][name])
        expected_role = _role(name, source, record["realization"], strict, candidate_lock, preimage_lock)
        if record["role"] != expected_role:
            raise ConfigError("local source binding role disagrees with its authorized lock closure")
    if selected_node not in seen:
        raise ConfigError("promotion local source bindings omit the selected candidate")


def revalidate_promotion_local_sources(
    workflow: Any, candidate_lock: Mapping[str, Any], records: Any, *,
    preimage_lock: Mapping[str, Any], selected_node: str,
) -> None:
    """Recapture all authority and exact realizations, including before commit."""
    validate_promotion_local_sources(records, candidate_lock,
                                    preimage_lock=preimage_lock, selected_node=selected_node)
    fresh_records = capture_promotion_local_sources(workflow, candidate_lock,
                                           preimage_lock=preimage_lock, selected_node=selected_node)
    if len(fresh_records) != len(records):
        raise ConfigError("catalog local source authority drifted from reviewed bindings; fresh plan required")
    bound_keys = {"key", "checkout", "repository", "release_ref", "revision"}
    for fresh, reviewed in zip(fresh_records, records):
        if fresh["node"] != reviewed["node"] or fresh["role"] != reviewed["role"] or fresh["realization"] != reviewed["realization"]:
            raise ConfigError("catalog local source authority drifted from reviewed bindings; fresh plan required")
        fresh_src = {k: v for k, v in fresh["source"].items() if k in bound_keys}
        reviewed_src = {k: v for k, v in reviewed["source"].items() if k in bound_keys}
        if fresh_src != reviewed_src:
            raise ConfigError("catalog local source authority drifted from reviewed bindings; fresh plan required")


def render_promotion_local_sources(records):
    """Render reviewed head and historical realization as separate identities."""
    lines = ["## Catalog source realizations", ""]
    for record in records:
        lines.extend([
            f"- Node `{record['node']}`; role `{record['role']}`.",
            f"  Observed release head H: `{record['source']['revision']}`.",
            f"  Exact locked realization L: `{record['realization']['revision']}`.",
            f"  Locked NAR: `{record['realization']['nar_hash']}`.",
        ])
    return lines + ["", "Release-head observation does not authorize unrelated lock movement.", ""]
