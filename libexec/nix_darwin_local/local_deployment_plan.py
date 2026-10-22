from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .paths import ConfigError
from .deployment_paths import (
    atomic_private_write,
    exclusive_private_write,
    fsync_directory,
    require_private_directory,
    require_private_file,
)
from .deployment_plan import canonical_bytes, reject_secrets, sha256_bytes


SCHEMA = "nix-update-local-plan-v1"


@dataclass(frozen=True)
class LocalPlanArtifact:
    directory: Path
    document: dict[str, Any]
    markdown: str
    digest: str


def validate_local_plan_document(document: Mapping[str, Any]) -> None:
    schema = document.get("schema")
    if schema not in {SCHEMA, "nix-update-local-plan-v2", "nix-update-local-plan-v3"}:
        raise ConfigError(f"invalid local plan schema: expected {SCHEMA}, got {schema}")
    if schema == "nix-update-local-plan-v2":
        from .local_source_binding import validate_local_sources
        valid_fields = {
            "schema", "selected_flake", "host", "git_authority", "target_system",
            "predecessor_generation", "profile_snapshot", "sources", "effects",
            "preflight_results", "closure_delta", "created_at", "local_sources", "lock",
            "commit_map",
        }
        required_fields = {
            "schema", "selected_flake", "host", "git_authority", "target_system",
            "predecessor_generation", "profile_snapshot", "sources", "effects",
            "preflight_results", "closure_delta", "created_at", "local_sources", "lock",
        }
        if not (set(document) <= valid_fields and required_fields <= set(document)):
            raise ConfigError("local plan v2 has missing or unknown fields")
        validate_local_sources(document["local_sources"], document["lock"])
        if "commit_map" in document:
            if not isinstance(document["commit_map"], Mapping):
                raise ConfigError("local plan v2 commit_map must be a mapping")
            for k, v in document["commit_map"].items():
                if not isinstance(k, str) or not isinstance(v, str):
                    raise ConfigError("local plan v2 commit_map has invalid entries")
    elif schema == "nix-update-local-plan-v3":
        from .local_source_binding import validate_local_sources
        from .promotion_contract import HEX_REVISION
        valid_fields = {
            "schema", "selected_flake", "host", "git_authority", "target_system",
            "predecessor_generation", "profile_snapshot", "sources", "effects",
            "preflight_results", "closure_delta", "created_at", "local_sources", "lock",
            "commit_map", "source_snapshot",
        }
        required_fields = {
            "schema", "selected_flake", "host", "git_authority", "target_system",
            "predecessor_generation", "profile_snapshot", "sources", "effects",
            "preflight_results", "closure_delta", "created_at", "local_sources", "lock",
            "source_snapshot",
        }
        if not (set(document) <= valid_fields and required_fields <= set(document)):
            raise ConfigError("local plan v3 has missing or unknown fields")
        validate_local_sources(document["local_sources"], document["lock"])
        if "commit_map" in document:
            if not isinstance(document["commit_map"], Mapping):
                raise ConfigError("local plan v3 commit_map must be a mapping")
            for k, v in document["commit_map"].items():
                if not isinstance(k, str) or not isinstance(v, str):
                    raise ConfigError("local plan v3 commit_map has invalid entries")
        ss = document["source_snapshot"]
        if not isinstance(ss, Mapping):
            raise ConfigError("local plan v3 source_snapshot must be a mapping")
        ss_valid = {
            "active", "configured_revisions", "effective_revisions",
            "provenance", "selected_source", "lock_backed_revisions",
        }
        if set(ss) != ss_valid:
            raise ConfigError("local plan v3 source_snapshot has missing or unknown fields")
        if not isinstance(ss["active"], bool):
            raise ConfigError("local plan v3 source_snapshot.active must be a boolean")
        if ss["active"]:
            if not isinstance(ss["configured_revisions"], Mapping):
                raise ConfigError("local plan v3 source_snapshot.configured_revisions must be a mapping when active")
            for k, v in ss["configured_revisions"].items():
                if not isinstance(k, str) or not isinstance(v, str) or HEX_REVISION.fullmatch(v) is None:
                    raise ConfigError("local plan v3 source_snapshot.configured_revisions has invalid revision")
        elif ss["configured_revisions"] is not None and not isinstance(ss["configured_revisions"], Mapping):
            raise ConfigError("local plan v3 source_snapshot.configured_revisions must be None or a mapping")
        for field in ("effective_revisions", "provenance", "lock_backed_revisions"):
            if not isinstance(ss[field], Mapping):
                raise ConfigError(f"local plan v3 source_snapshot.{field} must be a mapping")
        for k, v in ss["effective_revisions"].items():
            if not isinstance(k, str) or not isinstance(v, str) or HEX_REVISION.fullmatch(v) is None:
                raise ConfigError("local plan v3 source_snapshot.effective_revisions has invalid revision")
        for k, v in ss["lock_backed_revisions"].items():
            if not isinstance(k, str) or not isinstance(v, str) or HEX_REVISION.fullmatch(v) is None:
                raise ConfigError("local plan v3 source_snapshot.lock_backed_revisions has invalid revision")
        for k, v in ss["provenance"].items():
            if not isinstance(k, str) or v not in {"snapshot", "cli"}:
                raise ConfigError("local plan v3 source_snapshot.provenance has invalid provenance")
        sel_src = ss["selected_source"]
        if sel_src is not None:
            if not isinstance(sel_src, Mapping) or set(sel_src) != {"key", "revision"}:
                raise ConfigError("local plan v3 source_snapshot.selected_source must have 'key' and 'revision'")
            if not isinstance(sel_src["key"], str) or not isinstance(sel_src["revision"], str) or HEX_REVISION.fullmatch(sel_src["revision"]) is None:
                raise ConfigError("local plan v3 source_snapshot.selected_source has invalid key or revision")
    for key in ("selected_flake", "host", "target_system", "predecessor_generation", "git_authority"):
        if key not in document:
            raise ConfigError(f"missing required key in local plan: {key}")
    reject_secrets(document)


def write_local_plan(directory: Path, document: dict[str, Any], markdown: str) -> LocalPlanArtifact:
    validate_local_plan_document(document)
    reject_secrets(markdown)
    require_private_directory(directory, "local plan output directory")

    raw_bytes = canonical_bytes(document)
    digest = sha256_bytes(raw_bytes)
    plan_json = directory / "plan.json"
    plan_md = directory / "plan.md"

    exclusive_private_write(plan_json, raw_bytes)
    exclusive_private_write(plan_md, markdown.encode("utf-8"))
    fsync_directory(directory)

    return LocalPlanArtifact(directory=directory, document=document, markdown=markdown, digest=digest)


def load_local_plan(path: Path) -> LocalPlanArtifact:
    if path.is_file():
        plan_json = path
        directory = path.parent
    else:
        directory = path
        plan_json = directory / "plan.json"

    require_private_directory(directory, "local plan directory")
    require_private_file(plan_json, "local plan file")

    raw_bytes = plan_json.read_bytes()
    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ConfigError(f"unable to decode local plan: {plan_json}") from error

    validate_local_plan_document(document)
    if document["schema"] in {"nix-update-local-plan-v2", "nix-update-local-plan-v3"} and raw_bytes != canonical_bytes(document):
        raise ConfigError(f"local plan {document['schema'][-2:]} bytes must be canonical")
    digest = sha256_bytes(canonical_bytes(document))

    plan_md = directory / "plan.md"
    markdown = plan_md.read_text(encoding="utf-8") if plan_md.is_file() else ""

    return LocalPlanArtifact(directory=directory, document=document, markdown=markdown, digest=digest)
