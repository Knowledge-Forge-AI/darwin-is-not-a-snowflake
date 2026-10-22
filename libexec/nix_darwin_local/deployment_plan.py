from __future__ import annotations

import ast
import base64
import binascii
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from .paths import ConfigError
from .deployment_paths import (
    atomic_private_write,
    create_private_directory,
    exclusive_private_write,
    fsync_directory,
    require_private_directory as shared_require_private_directory,
    require_private_file as shared_require_private_file,
)


SCHEMA = "nix-update-deployment-plan-v2"
CONTRACT_SCHEMA = "nix-update-deployment-contract-v1"
PARTICIPANT_SCHEMA = "nix-update-participant-v1"
RESULT_SCHEMA = "nix-update-deployment-result-v1"
SECRET_KEYS = re.compile(
    r"(credential|password|secret|token|cookie|private_key|api[_-]?key|access[_-]?key(?:[_-]?id)?)", re.I
)
SECRET_VALUE = re.compile(
    r"(?i)(?P<label>authorization\s*:\s*[a-z][a-z0-9_-]*|x-api-key\s*:|[a-z0-9_-]*(?:credential|password|secret|token|cookie|private[_-]?key|api[_-]?key|access[_-]?key(?:[_-]?id)?)[a-z0-9_-]*\s*[:=])\s*(?P<value>[^\s,;]+)"
)
URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^/\s]+@)")
QUOTED_FIELD_VALUE = re.compile(
    r'(?P<label>(?P<key>(?P<key_quote>["\'])(?:\\.|(?!(?P=key_quote)|\\).)*(?P=key_quote))\s*:\s*)'
    r'(?P<value_quote>["\'])(?:\\.|(?!(?P=value_quote)|\\).)*(?P=value_quote)'
)
QUOTED_FIELD_PREFIX = re.compile(
    r'(?P<label>(?P<key>(?P<key_quote>["\'])(?:\\.|(?!(?P=key_quote)|\\).)*(?P=key_quote))\s*:\s*)'
    r'(?P<value_quote>["\'])(?:\\.|(?!(?P=value_quote)|\\).)*(?:\\\Z|\Z)'
)
HEX_SHA1 = re.compile(r"[0-9a-f]{40}")
HEX_SHA256 = re.compile(r"[0-9a-f]{64}")


def _is_generic_git_ssh_userinfo(match: re.Match[str]) -> bool:
    return match.group(1) in {"ssh://", "git+ssh://"} and match.group(2) == "git@"


def _redact_url_userinfo(match: re.Match[str]) -> str:
    if _is_generic_git_ssh_userinfo(match):
        return match.group(0)
    return f"{match.group(1)}[REDACTED]@"


def _has_credential_userinfo(value: str) -> bool:
    return any(not _is_generic_git_ssh_userinfo(match) for match in URL_USERINFO.finditer(value))


@dataclass(frozen=True)
class PlanArtifact:
    directory: Path
    document: dict[str, Any]
    digest: str


def canonical_bytes(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def redact_text(value: str) -> str:
    def replace_secret(match: re.Match[str]) -> str:
        label = match.group("label")
        if label.lower().startswith("authorization"):
            scheme = label.rsplit(":", 1)[-1].strip()
            return f"Authorization: {scheme} [REDACTED]"
        return f"{label.rstrip(':= ')}=[REDACTED]"

    def replace_quoted_field(match: re.Match[str]) -> str:
        key_literal = match.group("key")
        try:
            key = json.loads(key_literal) if key_literal.startswith('"') else ast.literal_eval(key_literal)
        except (SyntaxError, ValueError, json.JSONDecodeError):
            return match.group(0)
        if not isinstance(key, str) or (key.casefold() != "authorization" and SECRET_KEYS.search(key) is None):
            return match.group(0)
        quote = match.group("value_quote")
        return f'{match.group("label")}{quote}[REDACTED]{quote}'

    redacted = QUOTED_FIELD_VALUE.sub(replace_quoted_field, value)
    redacted = QUOTED_FIELD_PREFIX.sub(replace_quoted_field, redacted)
    redacted = SECRET_VALUE.sub(replace_secret, redacted)
    redacted = URL_USERINFO.sub(_redact_url_userinfo, redacted)
    if "-----BEGIN" in redacted and "PRIVATE KEY-----" in redacted:
        return "[REDACTED PRIVATE KEY OUTPUT]\n"
    return redacted


def file_identity(path: Path) -> dict[str, Any]:
    metadata = path.lstat()
    identity: dict[str, Any] = {
        "path": str(path.absolute()),
        "mode": stat.S_IMODE(metadata.st_mode),
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "kind": _kind(metadata.st_mode),
        "link_count": metadata.st_nlink,
    }
    if stat.S_ISREG(metadata.st_mode):
        raw = path.read_bytes()
        identity.update({"sha256": sha256_bytes(raw), "size": len(raw)})
    elif stat.S_ISLNK(metadata.st_mode):
        target = os.readlink(path)
        identity.update({"target": target, "target_identity": str(path.resolve(strict=False))})
    return identity


def tree_identity(root: Path, volatile: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    lexical_root = str(root)
    if (
        not root.is_absolute()
        or os.path.normpath(lexical_root) != lexical_root
        or not root.exists()
        or not root.is_dir()
    ):
        raise ConfigError(f"state tree is missing or not a directory: {root}")
    root_metadata = root.lstat()
    root_kind = _kind(root_metadata.st_mode)
    if root_kind not in {"directory", "symlink"}:
        raise ConfigError(f"state tree root has an unsupported kind: {root}")
    root_identity: dict[str, Any] = {
        "path": lexical_root,
        "kind": root_kind,
        "mode": stat.S_IMODE(root_metadata.st_mode),
        "uid": root_metadata.st_uid,
        "gid": root_metadata.st_gid,
        "link_count": root_metadata.st_nlink,
        "resolved_target": str(root.resolve(strict=True)),
    }
    if root_kind == "symlink":
        root_identity["target"] = os.readlink(root)
    policy_items = [dict(item) for item in volatile]
    policies: dict[str, dict[str, Any]] = {}
    for policy in policy_items:
        _validate_volatile_policy(policy)
        policy_path = policy["path"]
        if policy_path in policies:
            raise ConfigError(f"volatile entry path must be unique: {policy_path}")
        policies[policy_path] = policy
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        entry_kind = _kind(metadata.st_mode)
        if entry_kind not in {"regular", "directory", "symlink"}:
            raise ConfigError(f"state tree entry has an unsupported kind: {relative}")
        entry: dict[str, Any] = {
            "path": relative,
            "kind": entry_kind,
            "mode": stat.S_IMODE(metadata.st_mode),
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
            "link_count": metadata.st_nlink,
        }
        if path.is_symlink():
            entry["target"] = os.readlink(path)
            entry["target_identity"] = str(path.resolve(strict=False))
        elif path.is_file():
            entry["sha256"] = sha256_bytes(path.read_bytes())
            entry["size"] = metadata.st_size
        policy = policies.get(relative)
        if policy is not None:
            seen.add(relative)
            _validate_volatile(entry, policy)
            entry["volatile"] = {
                "producer": policy["producer"],
                "content_policy": policy["content_policy"],
            }
        entries.append(entry)
    missing = sorted(set(policies) - seen)
    if missing:
        raise ConfigError(f"declared volatile entry is missing: {missing[0]}")
    normalized_policies = [policies[path] for path in sorted(policies)]
    content_material = {"entries": entries, "volatile": normalized_policies}
    content_identity = {
        **content_material,
        "digest": sha256_bytes(canonical_bytes(content_material)),
    }
    complete_material = {"root_identity": root_identity, "content_identity": content_identity}
    return {
        **complete_material,
        "digest": sha256_bytes(canonical_bytes(complete_material)),
    }


def normalized_lock(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConfigError(f"unable to read flake lock: {path}") from error
    if not isinstance(document, dict) or not isinstance(document.get("nodes"), dict):
        raise ConfigError("flake.lock must contain a nodes mapping")
    nodes: dict[str, Any] = {}
    for name, node in sorted(document["nodes"].items()):
        if not isinstance(name, str) or not isinstance(node, dict):
            raise ConfigError("flake.lock contains an invalid node")
        nodes[name] = _normalized_json(node)
    return {
        "version": document.get("version"),
        "root": document.get("root"),
        "nodes": nodes,
        "byte_sha256": sha256_bytes(raw),
    }


def read_lock_document(path: Path) -> dict[str, Any]:
    """Read and decode a flake.lock document without normalized snapshot mutations."""
    try:
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConfigError(f"unable to read flake lock: {path}") from error
    if not isinstance(document, dict):
        raise ConfigError("flake.lock must contain an object")
    return document


def serialize_lock_document(document: Mapping[str, Any]) -> bytes:
    """Serialize a standard Nix flake.lock document to canonical JSON bytes.

    Accepts a lock document mapping and outputs canonical bytes containing only
    the standard Nix document fields ('nodes', 'root', 'version').

    Deliberate asymmetry:
    - Top-level 'byte_sha256' is accepted on input and omitted from output to
      tolerate historical annotated locks and diagnostic snapshots without
      persisting the diagnostic hash into portable flake.lock files.
    - Relocated diagnostic metadata on node mappings or inside source locators
      ('locked' and 'original') is strictly rejected via
      `_contains_diagnostic_metadata` to guarantee that diagnostic metadata is
      never hidden within node entries. Legitimate dependency/input names
      (inside 'inputs') are graph data and are preserved.
    - Unsupported top-level fields are rejected to prevent lossy filtering of
      future or extended Nix lock fields.
    """
    if not isinstance(document, Mapping):
        raise ConfigError("flake.lock must contain an object")
    for key in document:
        if not isinstance(key, str):
            raise ConfigError("flake.lock document keys must be strings")
    nodes_mapping = document.get("nodes")
    if not isinstance(nodes_mapping, Mapping):
        raise ConfigError("flake.lock must contain a nodes mapping")
    for name in nodes_mapping:
        if not isinstance(name, str):
            raise ConfigError("flake.lock contains an invalid node")
    if "root" not in document:
        raise ConfigError("flake.lock must contain a root node reference")
    root = document["root"]
    if not isinstance(root, str) or not root:
        raise ConfigError("flake.lock must contain a root node reference")
    if "version" not in document:
        raise ConfigError("flake.lock must contain an integer version")
    version = document["version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise ConfigError("flake.lock must contain an integer version")
    unsupported = sorted(set(document) - {"nodes", "root", "version", "byte_sha256"})
    if unsupported:
        raise ConfigError(f"flake.lock contains unsupported field: {unsupported[0]}")
    nodes: dict[str, Any] = {}
    for name, node in sorted(nodes_mapping.items()):
        if not isinstance(node, Mapping):
            raise ConfigError("flake.lock contains an invalid node")
        if _contains_diagnostic_metadata(node):
            raise ConfigError("flake.lock node contains invalid diagnostic metadata")
        nodes[name] = _normalized_json(node)
    lock_doc = {
        "nodes": nodes,
        "root": root,
        "version": version,
    }
    return canonical_bytes(lock_doc)


def _contains_diagnostic_metadata(node: Mapping[str, Any]) -> bool:
    """Check whether a lock node contains relocated diagnostic metadata.

    Preserves a narrow, structurally justified rejection for relocated diagnostic
    annotations where the supported Nix flake.lock schema unambiguously
    distinguishes metadata from graph data:
    - Direct node attributes: 'byte_sha256' directly on a node mapping is rejected.
    - Source locators: 'byte_sha256' recursively within 'locked' or 'original'
      locators (scanning nested mappings and lists) is rejected.

    Legitimate dependency/input names in 'inputs' (and arbitrary nested extension
    objects) represent graph data and must not be reserved.
    """
    if not isinstance(node, Mapping):
        return False
    if "byte_sha256" in node:
        return True
    for locator_key in ("locked", "original"):
        locator = node.get(locator_key)
        if _locator_contains_diagnostic_metadata(locator):
            return True
    return False


def _locator_contains_diagnostic_metadata(value: Any) -> bool:
    if isinstance(value, Mapping):
        if "byte_sha256" in value:
            return True
        return any(_locator_contains_diagnostic_metadata(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_locator_contains_diagnostic_metadata(item) for item in value)
    return False


def lock_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    left = before["nodes"]
    right = after["nodes"]
    changes: list[dict[str, Any]] = []
    for name in sorted(set(left) | set(right)):
        if left.get(name) != right.get(name):
            changes.append({"node": name, "before": left.get(name), "after": right.get(name)})
    if before.get("root") != after.get("root") or before.get("version") != after.get("version"):
        changes.append(
            {
                "node": "$lock-metadata",
                "before": {"root": before.get("root"), "version": before.get("version")},
                "after": {"root": after.get("root"), "version": after.get("version")},
            }
        )
    return changes


def reject_unapproved_lock_delta(changes: Iterable[Mapping[str, Any]], allowed: Iterable[str]) -> None:
    approved = set(allowed)
    unexpected = sorted(str(change["node"]) for change in changes if change["node"] not in approved)
    if unexpected:
        raise ConfigError(f"lock update moved an unauthorized node: {unexpected[0]}")


def load_contract(path: Path) -> dict[str, Any]:
    document = _read_json_object(path, "deployment contract")
    allowed = {
        "schema",
        "additional_hosts",
        "allowed_lock_nodes",
        "allowed_closure_delta",
        "external_state",
        "profile_policy",
        "sources",
        "state_probes",
        "control_transactions",
        "participants",
        "publish_local_config",
        "rollback_policy",
        "control_root",
    }
    _reject_unknown(document, allowed, "deployment contract")
    if document.get("schema") != CONTRACT_SCHEMA:
        raise ConfigError(f"deployment contract schema must be {CONTRACT_SCHEMA}")
    _reject_secrets(document)
    for key in ("additional_hosts", "allowed_lock_nodes", "allowed_closure_delta", "external_state", "sources", "state_probes", "control_transactions", "participants"):
        if not isinstance(document.get(key, []), list):
            raise ConfigError(f"deployment contract {key} must be a list")
    if not isinstance(document.get("publish_local_config", False), bool):
        raise ConfigError("deployment contract publish_local_config must be a boolean")
    _validate_sources(document.get("sources", []))
    for key in ("additional_hosts", "allowed_lock_nodes", "allowed_closure_delta"):
        if any(not isinstance(item, str) for item in document.get(key, [])):
            raise ConfigError(f"deployment contract {key} entries must be strings")
    _validate_probes(document.get("state_probes", []), "state probe")
    _validate_probes(document.get("external_state", []), "external state")
    state_names = {item["name"] for item in document.get("state_probes", [])}
    external_names = {item["name"] for item in document.get("external_state", [])}
    if state_names & external_names:
        raise ConfigError("state probe and external state names must be globally unique")
    _validate_profile_policy(document.get("profile_policy"))
    control_root = document.get("control_root")
    if document.get("control_transactions"):
        if not isinstance(control_root, str) or not Path(control_root).is_absolute():
            raise ConfigError("deployment contract control_root must be absolute when transactions exist")
    elif control_root is not None and (not isinstance(control_root, str) or not Path(control_root).is_absolute()):
        raise ConfigError("deployment contract control_root must be absolute")
    _validate_transactions(document.get("control_transactions", []), control_root)
    _validate_participants(document.get("participants", []))
    rollback_policy = document.get("rollback_policy")
    if rollback_policy is not None:
        if rollback_policy != {"pre_switch": "restore-all", "post_switch": "participant-local-reverse"}:
            raise ConfigError("rollback_policy must be the closed restore-all/participant-local-reverse policy")
    return document


def write_plan(directory: Path, document: dict[str, Any], markdown: str) -> PlanArtifact:
    if not directory.is_absolute():
        raise ConfigError("deployment plan directory must be absolute")
    if document.get("schema") not in {SCHEMA, "nix-update-deployment-plan-v3"}:
        raise ConfigError(f"plan schema must be {SCHEMA} or nix-update-deployment-plan-v3")
    _reject_secrets(document)
    validate_plan_document(document)
    if directory.exists() or directory.is_symlink():
        shared_require_private_directory(directory, "deployment plan directory", empty=True)
    else:
        create_private_directory(directory, "deployment plan directory")
    unsigned = dict(document)
    unsigned["plan_digest"] = ""
    digest = sha256_bytes(canonical_bytes(unsigned))
    final = dict(document)
    final["plan_digest"] = digest
    exclusive_private_write(directory / "plan.json", canonical_bytes(final))
    exclusive_private_write(directory / "plan.md", markdown.replace("{PLAN_DIGEST}", digest).encode("utf-8"))
    fsync_directory(directory)
    return PlanArtifact(directory, final, digest)


def load_plan(path: Path) -> PlanArtifact:
    if not path.is_absolute():
        raise ConfigError("deployment plan path must be absolute")
    directory = path if path.is_dir() else path.parent
    json_path = path / "plan.json" if path.is_dir() else path
    _require_private_directory(directory, "deployment plan directory")
    _require_private_file(json_path, "deployment plan")
    markdown_path = directory / "plan.md"
    if markdown_path.exists() or markdown_path.is_symlink():
        _require_private_file(markdown_path, "deployment plan companion")
    raw = json_path.read_bytes()
    document = _read_json_object(json_path, "deployment plan")
    if raw != canonical_bytes(document):
        raise ConfigError("deployment plan bytes are not canonical")
    if document.get("schema") == "nix-update-deployment-plan-v1":
        raise ConfigError(
            "deployment plan schema nix-update-deployment-plan-v1 is incompatible with local repository authority; run nix-update prepare to create a v2 plan"
        )
    if document.get("schema") not in {SCHEMA, "nix-update-deployment-plan-v3"}:
        raise ConfigError(f"deployment plan schema must be {SCHEMA} or nix-update-deployment-plan-v3")
    recorded = document.get("plan_digest")
    if not isinstance(recorded, str) or len(recorded) != 64:
        raise ConfigError("deployment plan digest is missing or invalid")
    unsigned = dict(document)
    unsigned["plan_digest"] = ""
    if sha256_bytes(canonical_bytes(unsigned)) != recorded:
        raise ConfigError("deployment plan digest does not match canonical content")
    _reject_secrets(document)
    validate_plan_document(document)
    return PlanArtifact(directory, document, recorded)


def write_result(directory: Path, document: dict[str, Any]) -> Path:
    if not directory.is_absolute():
        raise ConfigError("deployment result directory must be absolute")
    final = dict(document)
    final["schema"] = RESULT_SCHEMA
    _reject_secrets(final)
    if directory.exists() or directory.is_symlink():
        shared_require_private_directory(directory, "deployment result directory")
    else:
        create_private_directory(directory, "deployment result directory")
    path = directory / "result.json"
    _validate_result_document(final, path)
    atomic_private_write(path, canonical_bytes(final))
    return path


def _validate_result_document(document: Mapping[str, Any], path: Path) -> None:
    allowed = {
        "schema", "plan_digest", "started_at", "finished_at", "terminal_state",
        "switch_invocation_started", "events", "failure", "consumed_authority",
        "pre_switch_marker", "profile_realignment_marker", "switch_marker", "switch", "commands", "participants",
        "result_path", "restoration_failures",
        "participant_restoration", "participant_restoration_failures",
        "operator_disposition_commands",
        "command_evidence_policy",
    }
    required = {
        "schema", "plan_digest", "started_at", "finished_at", "terminal_state",
        "switch_invocation_started", "events", "commands", "participants", "result_path",
    }
    terminals = {
        "FAILED_PRE_SWITCH", "FAILED_PRE_SWITCH_RESTORE_INCOMPLETE",
        "LOCK_RELEASE_FAILURE", "POST_INVOCATION_FAILURE", "REPLAY_BLOCKED", "SUCCESS",
        "POST_SWITCH_RESTORED", "POST_SWITCH_RESTORE_INCOMPLETE",
        "POST_SWITCH_PARTICIPANT_INDETERMINATE",
    }
    if set(document) - allowed or not required <= set(document):
        raise ConfigError("deployment result has missing or unknown fields")
    if (
        document.get("schema") != RESULT_SCHEMA
        or not isinstance(document.get("plan_digest"), str)
        or HEX_SHA256.fullmatch(document["plan_digest"]) is None
        or document.get("terminal_state") not in terminals
        or not isinstance(document.get("started_at"), str)
        or not isinstance(document.get("finished_at"), str)
        or not isinstance(document.get("switch_invocation_started"), bool)
        or not isinstance(document.get("events"), list)
        or not isinstance(document.get("commands"), list)
        or not isinstance(document.get("participants"), list)
        or document.get("result_path") != str(path)
    ):
        raise ConfigError("deployment result field or path identity is invalid")
    result_policy = document.get("command_evidence_policy", {})
    if result_policy and (
        not isinstance(result_policy, dict)
        or set(result_policy) != {"raw_limit_bytes", "summary_limit_bytes", "root"}
        or any(
            not isinstance(result_policy[key], int) or isinstance(result_policy[key], bool)
            for key in ("raw_limit_bytes", "summary_limit_bytes")
        )
        or not 0 < result_policy["summary_limit_bytes"] <= result_policy["raw_limit_bytes"]
        or not isinstance(result_policy["root"], str)
        or not Path(result_policy["root"]).is_absolute()
    ):
        raise ConfigError("deployment result command evidence policy is invalid")
    if document["commands"] and not result_policy:
        raise ConfigError("deployment result commands require a bound evidence policy")
    _validate_plan_checks(document["commands"], result_policy)
    _validate_plan_participant_results(document["participants"])
    if "switch" in document:
        if not result_policy:
            raise ConfigError("deployment result switch requires a bound evidence policy")
        _validate_plan_checks([document["switch"]], result_policy)


def _validate_sources(items: list[Any]) -> None:
    allowed = {"name", "input", "lock_node", "kind", "path", "nar_hash", "store_path", "locator"}
    names: set[str] = set()
    inputs: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ConfigError("each source must be an object")
        _reject_unknown(item, allowed, "source")
        if not isinstance(item.get("name"), str) or not item["name"] or item["name"] in names:
            raise ConfigError("source name must be unique and nonempty")
        names.add(item["name"])
        if item.get("kind") not in {"store-path", "immutable-snapshot"}:
            raise ConfigError("source kind must be store-path or immutable-snapshot")
        if (
            not isinstance(item.get("input"), str)
            or not item["input"]
            or item["input"] == "localConfig"
            or item["input"] in inputs
        ):
            raise ConfigError("source input must uniquely name a non-localConfig flake input")
        inputs.add(item["input"])
        if not isinstance(item.get("lock_node"), str) or not item["lock_node"]:
            raise ConfigError("source lock_node must name one exact flake.lock node")
        if not isinstance(item.get("nar_hash"), str) or not item["nar_hash"].startswith("sha256-"):
            raise ConfigError("source nar_hash must be an SRI SHA-256")
        source_path = item.get("store_path") if item["kind"] == "store-path" else item.get("path")
        if not isinstance(source_path, str) or not Path(source_path).is_absolute():
            raise ConfigError("exact source path is required")
        if not _is_nix_store_path(Path(source_path)):
            raise ConfigError("exact source must be an immutable /nix/store path")


def _validate_transactions(items: list[Any], control_root: str | None = None) -> None:
    required = {
        "path", "safe_ancestors", "preimage_sha256", "preimage_bytes_b64", "postimage_sha256",
        "postimage_bytes_b64", "pre_kind", "pre_uid", "pre_gid", "pre_mode", "pre_link_count",
        "post_mode", "metadata_policy", "authorization", "restore_before_switch",
    }
    paths: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != required:
            raise ConfigError("control transaction has missing or unknown keys")
        path_value = item.get("path")
        if not isinstance(path_value, str) or not Path(path_value).is_absolute() or str(Path(path_value)) != path_value:
            raise ConfigError("control transaction path must be absolute and lexically normalized")
        path = Path(path_value)
        if control_root is not None:
            root = Path(control_root)
            try:
                path.relative_to(root)
            except ValueError as error:
                raise ConfigError("control transaction path is outside control_root") from error
            if path == root:
                raise ConfigError("control transaction cannot replace control_root")
            expected_ancestors: list[str] = []
            current = root
            expected_ancestors.append(str(current))
            for part in path.parent.relative_to(root).parts:
                current /= part
                expected_ancestors.append(str(current))
            if item.get("safe_ancestors") != expected_ancestors:
                raise ConfigError("control transaction safe_ancestors must be the complete lexical chain")
        elif not isinstance(item.get("safe_ancestors"), list) or any(
            not isinstance(value, str) or not Path(value).is_absolute() for value in item["safe_ancestors"]
        ):
            raise ConfigError("control transaction safe_ancestors must be absolute paths")
        for field in ("preimage_sha256", "postimage_sha256"):
            if not isinstance(item.get(field), str) or HEX_SHA256.fullmatch(item[field]) is None:
                raise ConfigError(f"control transaction {field} must be lower-case 64-hex")
        for field in ("preimage_bytes_b64", "postimage_bytes_b64"):
            if not isinstance(item.get(field), str):
                raise ConfigError(f"control transaction {field} must be canonical base64")
            try:
                decoded = base64.b64decode(item[field], validate=True)
            except (ValueError, binascii.Error) as error:
                raise ConfigError(f"control transaction {field} must be canonical base64") from error
            if base64.b64encode(decoded).decode() != item[field]:
                raise ConfigError(f"control transaction {field} must be canonical base64")
        preimage = base64.b64decode(item["preimage_bytes_b64"], validate=True)
        postimage = base64.b64decode(item["postimage_bytes_b64"], validate=True)
        if sha256_bytes(preimage) != item["preimage_sha256"] or sha256_bytes(postimage) != item["postimage_sha256"]:
            raise ConfigError("control transaction content hash does not match decoded bytes")
        if item.get("pre_kind") != "regular":
            raise ConfigError("control transaction pre_kind must be regular")
        for field in ("pre_uid", "pre_gid", "pre_mode", "pre_link_count", "post_mode"):
            if not isinstance(item.get(field), int) or isinstance(item[field], bool) or item[field] < 0:
                raise ConfigError(f"control transaction {field} must be a nonnegative integer")
        if item["pre_link_count"] != 1:
            raise ConfigError("control transaction pre_link_count must be one")
        if item["pre_mode"] > 0o7777 or item["post_mode"] > 0o7777:
            raise ConfigError("control transaction mode is invalid")
        if item["pre_mode"] & 0o022 or item["post_mode"] & 0o077:
            raise ConfigError("control transaction mode is unsafe")
        if item["authorization"] != "after-confirmation-before-switch":
            raise ConfigError("control transaction authorization phase is invalid")
        if item["metadata_policy"] != {
            "owner_group": "preserve",
            "xattrs": "require-absent",
            "acl": "require-absent",
            "flags": "require-zero",
        }:
            raise ConfigError("control transaction metadata_policy is unsupported")
        if not isinstance(item.get("restore_before_switch"), bool):
            raise ConfigError("control transaction restore_before_switch must be a boolean")
        if path_value in paths:
            raise ConfigError("control transaction path must be unique")
        for other in paths:
            if path in Path(other).parents or Path(other) in path.parents:
                raise ConfigError("control transaction destinations must not overlap")
        paths.add(path_value)


def _validate_participants(items: list[Any], *, require_frozen: bool = False) -> None:
    allowed = {
        "name", "protocol", "path", "sha256", "phases", "mutation_authority", "rollback_authority", "provenance",
        "timeout_seconds",
    }
    phase_names = {"preflight", "pre-switch", "post-switch-reconcile", "post-switch-verify", "rollback"}
    names: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ConfigError("each participant must be an object")
        _reject_unknown(item, allowed, "participant")
        if not isinstance(item.get("name"), str) or not item["name"] or item["name"] in names:
            raise ConfigError("participant name must be unique and nonempty")
        names.add(item["name"])
        if item.get("protocol") != PARTICIPANT_SCHEMA:
            raise ConfigError(f"participant protocol must be {PARTICIPANT_SCHEMA}")
        if not _is_normalized_absolute_path(item.get("path")):
            raise ConfigError("participant path must be an exact normalized absolute path")
        if not isinstance(item.get("sha256"), str) or HEX_SHA256.fullmatch(item["sha256"]) is None:
            raise ConfigError("participant sha256 must be a hex SHA-256")
        if (
            not isinstance(item.get("timeout_seconds"), int)
            or isinstance(item["timeout_seconds"], bool)
            or not 1 <= item["timeout_seconds"] <= 300
        ):
            raise ConfigError("participant timeout_seconds must be between 1 and 300")
        provenance = item.get("provenance")
        if not isinstance(provenance, dict) or provenance.get("kind") not in {"target-closure", "operator-artifact"}:
            raise ConfigError("participant provenance must bind a target closure or reviewed operator artifact")
        if provenance["kind"] == "target-closure":
            if (
                set(provenance) != {"kind", "identity"}
                or not isinstance(provenance.get("identity"), str)
                or not _is_nix_store_path(Path(provenance["identity"]))
            ):
                raise ConfigError("target-closure participant provenance must name an exact /nix/store output")
        else:
            contract_fields = {"kind", "identity", "root"}
            frozen_fields = contract_fields | {
                "source_path", "source_identity", "frozen_root", "frozen_path", "frozen_identity",
            }
            if (
                set(provenance) != (frozen_fields if require_frozen else contract_fields)
                or provenance.get("identity") != f"sha256:{item['sha256']}"
                or not _is_normalized_absolute_path(provenance.get("root"))
            ):
                raise ConfigError("operator participant provenance must bind its private root and SHA-256")
            if require_frozen:
                if (
                    not _is_normalized_absolute_path(provenance.get("source_path"))
                    or not _is_normalized_absolute_path(provenance.get("frozen_root"))
                    or not _is_normalized_absolute_path(provenance.get("frozen_path"))
                    or item["path"] != provenance["frozen_path"]
                ):
                    raise ConfigError("operator participant frozen paths are invalid")
                _require_lexical_child(provenance["source_path"], provenance["root"], "operator participant source")
                _require_lexical_child(provenance["frozen_path"], provenance["frozen_root"], "operator participant frozen copy")
                _validate_participant_artifact_identity(
                    provenance["source_identity"], provenance["source_path"], item["sha256"]
                )
                _validate_participant_artifact_identity(
                    provenance["frozen_identity"], provenance["frozen_path"], item["sha256"]
                )
            else:
                _require_lexical_child(item["path"], provenance["root"], "operator participant executable")
        phases = item.get("phases")
        if not isinstance(phases, list) or not phases or not set(phases) <= phase_names:
            raise ConfigError("participant phases are invalid")
        for authority in ("mutation_authority", "rollback_authority"):
            if not isinstance(item.get(authority), list) or any(not isinstance(value, str) for value in item[authority]):
                raise ConfigError(f"participant {authority} must be a list of strings")
        if item["mutation_authority"] and set(phases) <= {"preflight", "post-switch-verify", "rollback"}:
            raise ConfigError("read-only participant phases require an empty mutation authority")
        if set(phases) & {"pre-switch", "post-switch-reconcile"} and item["mutation_authority"]:
            if "rollback" not in phases or not item["rollback_authority"]:
                raise ConfigError("mutating participant must declare bounded rollback")
        if item["rollback_authority"] and (
            not item["mutation_authority"]
            or "rollback" not in phases
            or not set(phases) & {"pre-switch", "post-switch-reconcile"}
            or not set(item["rollback_authority"]) <= set(item["mutation_authority"])
        ):
            raise ConfigError("participant rollback authority must be bounded by mutation authority")


def _validate_probes(items: list[Any], label: str) -> None:
    allowed = {"name", "kind", "path", "volatile"}
    names: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ConfigError(f"each {label} must be an object")
        _reject_unknown(item, allowed, label)
        if not isinstance(item.get("name"), str) or not item["name"]:
            raise ConfigError(f"{label} name must be a nonempty string")
        if item["name"] in names:
            raise ConfigError(f"{label} name must be unique")
        names.add(item["name"])
        if item.get("kind") not in {"file", "tree"}:
            raise ConfigError(f"{label} kind must be file or tree")
        if not isinstance(item.get("path"), str) or not Path(item["path"]).is_absolute():
            raise ConfigError(f"{label} path must be absolute")
        volatile = item.get("volatile", [])
        if not isinstance(volatile, list) or (volatile and item["kind"] != "tree"):
            raise ConfigError(f"{label} volatile policy is invalid")
        volatile_paths: set[str] = set()
        for policy in volatile:
            _validate_volatile_policy(policy)
            if policy["path"] in volatile_paths:
                raise ConfigError(f"{label} volatile paths must be unique")
            volatile_paths.add(policy["path"])


def _validate_volatile_policy(policy: Any) -> None:
    required = {"path", "kind", "uid", "gid", "mode", "producer", "content_policy"}
    if not isinstance(policy, dict) or set(policy) != required:
        raise ConfigError("volatile entry policy has missing or unknown keys")
    _validate_relative_tree_path(policy["path"], "volatile entry")
    if policy["kind"] != "regular":
        raise ConfigError("volatile entry kind must be regular")
    for field in ("uid", "gid", "mode"):
        if not _is_nonnegative_int(policy[field]):
            raise ConfigError(f"volatile entry {field} must be a nonnegative integer")
    if policy["mode"] > 0o7777:
        raise ConfigError("volatile entry mode is invalid")
    if not isinstance(policy["producer"], str) or not policy["producer"]:
        raise ConfigError("volatile entry producer must be a nonempty string")
    content_policy = policy["content_policy"]
    if (
        not isinstance(content_policy, dict)
        or set(content_policy) != {"sha256"}
        or not isinstance(content_policy["sha256"], str)
        or HEX_SHA256.fullmatch(content_policy["sha256"]) is None
    ):
        raise ConfigError("volatile content policy must bind an exact sha256")


def _validate_profile_policy(value: Any) -> None:
    if not isinstance(value, dict):
        raise ConfigError("deployment contract profile_policy must be an object")
    allowed = {"profile", "live", "generation_glob", "action", "safe_generation"}
    _reject_unknown(value, allowed, "profile_policy")
    for key in ("profile", "live", "generation_glob"):
        if not isinstance(value.get(key), str) or not Path(value[key]).is_absolute():
            raise ConfigError(f"profile_policy {key} must be an absolute path")
    if value.get("action", "none") not in {"none", "realign-selected-profile"}:
        raise ConfigError("profile_policy action is invalid")
    if value.get("action") == "realign-selected-profile" and not isinstance(value.get("safe_generation"), int):
        raise ConfigError("profile_policy safe_generation is required for realignment")


def _validate_volatile(entry: Mapping[str, Any], policy: Mapping[str, Any]) -> None:
    _validate_volatile_policy(policy)
    if entry.get("path") != policy["path"]:
        raise ConfigError("volatile entry policy path does not match the bound entry")
    for field in ("kind", "uid", "gid", "mode"):
        if entry[field] != policy[field]:
            raise ConfigError(f"volatile entry {entry['path']} has unexpected {field}")
    content_policy = policy["content_policy"]
    if entry.get("sha256") != content_policy["sha256"]:
        raise ConfigError(f"volatile entry {entry['path']} has unexpected content")


def validate_plan_document(document: Mapping[str, Any]) -> None:
    required = {
        "schema", "tool", "environment", "plan_digest", "created_at", "status", "preflight_only", "blockers",
        "selected_flake", "lock", "allowed_lock_nodes", "control_snapshot", "machine_control_snapshot",
        "control_projection", "evaluation_root", "evaluation_current", "projection", "input_snapshots", "overrides",
        "checks", "target", "host_candidates", "live", "closure_delta", "external_state", "state_probes",
        "control_transactions", "participants", "participant_context", "participant_preflight", "profile_policy",
        "publish_local_config", "rollback_policy", "confirmation", "observations", "mutable_state_comparison",
        "command_evidence_policy",
    }
    local_schema = document.get("schema") == "nix-update-deployment-plan-v3"
    if local_schema:
        required.add("local_sources")
        from .local_source_binding import validate_local_sources
        validate_local_sources(document.get("local_sources"), document.get("lock", {}))
    if document.get("schema") == "nix-update-deployment-plan-v1":
        raise ConfigError(
            "deployment plan schema nix-update-deployment-plan-v1 is incompatible with local repository authority; run nix-update prepare to create a v2 plan"
        )
    if set(document) != required:
        raise ConfigError("deployment plan has missing or unknown top-level fields")
    if document.get("schema") != SCHEMA and not local_schema:
        raise ConfigError(f"deployment plan schema must be {SCHEMA}")
    if document.get("status") not in {"READY_FOR_REVIEW", "BLOCKED"}:
        raise ConfigError("deployment plan status is invalid")
    if not isinstance(document.get("plan_digest"), str):
        raise ConfigError("deployment plan plan_digest must be a string")
    if not isinstance(document.get("created_at"), str) or not document["created_at"]:
        raise ConfigError("deployment plan created_at must be a string")
    if not isinstance(document.get("preflight_only"), bool):
        raise ConfigError("deployment plan preflight_only must be a boolean")
    if not isinstance(document.get("blockers"), list) or any(not isinstance(item, str) for item in document["blockers"]):
        raise ConfigError("deployment plan blockers must be strings")
    for key in (
        "tool", "environment", "selected_flake", "lock", "control_snapshot", "machine_control_snapshot",
        "control_projection", "evaluation_current", "target", "live", "closure_delta", "participant_context",
        "profile_policy", "rollback_policy", "confirmation", "observations", "mutable_state_comparison",
        "command_evidence_policy",
    ):
        if not isinstance(document.get(key), dict):
            raise ConfigError(f"deployment plan {key} must be an object")
    for key in (
        "allowed_lock_nodes", "input_snapshots", "overrides", "checks", "host_candidates", "external_state",
        "state_probes", "control_transactions", "participants", "participant_preflight",
    ):
        if not isinstance(document.get(key), list):
            raise ConfigError(f"deployment plan {key} must be a list")
    if any(not isinstance(item, str) for item in document["overrides"]):
        raise ConfigError("deployment plan overrides must be strings")
    if not isinstance(document.get("evaluation_root"), str) or not Path(document["evaluation_root"]).is_absolute():
        raise ConfigError("deployment plan evaluation_root must be absolute")
    if not isinstance(document.get("projection"), dict):
        raise ConfigError("deployment plan projection must be an object")
    if not isinstance(document.get("publish_local_config"), bool):
        raise ConfigError("deployment plan publish_local_config must be a boolean")
    if document["rollback_policy"] != {"pre_switch": "restore-all", "post_switch": "participant-local-reverse"}:
        raise ConfigError("deployment plan rollback_policy is invalid")
    _validate_profile_policy(document["profile_policy"])
    control_root = document["control_projection"].get("control_root")
    if document["control_transactions"] and (
        not isinstance(control_root, str) or not Path(control_root).is_absolute()
    ):
        raise ConfigError("deployment plan control projection must bind an absolute control_root")
    _validate_transactions(document["control_transactions"], control_root)
    _validate_participants(document["participants"], require_frozen=True)
    _validate_plan_selected_flake(document["selected_flake"], document["status"] == "READY_FOR_REVIEW")
    _validate_plan_target(document["target"])
    _validate_plan_projection(document["projection"], document["status"] == "READY_FOR_REVIEW")
    _validate_plan_control_projection(document["control_projection"], document["control_transactions"])
    _validate_plan_source_identities(document["input_snapshots"])
    if document["status"] == "READY_FOR_REVIEW":
        _validate_plan_semantic_bindings(document)
    _validate_plan_participant_context(document["participant_context"], document["status"] == "READY_FOR_REVIEW")
    policy = document["command_evidence_policy"]
    if document["machine_control_snapshot"] or document["status"] == "READY_FOR_REVIEW":
        _validate_tree_identity(document["machine_control_snapshot"], "machine control")
    _validate_plan_probe_identities(document["state_probes"], "state probe")
    _validate_plan_probe_identities(document["external_state"], "external state")
    if {item["name"] for item in document["state_probes"]} & {item["name"] for item in document["external_state"]}:
        raise ConfigError("deployment plan state probe and external state names must be globally unique")
    if not isinstance(document["mutable_state_comparison"], dict) or any(
        not isinstance(key, str) or not isinstance(value, bool)
        for key, value in document["mutable_state_comparison"].items()
    ):
        raise ConfigError("deployment plan mutable_state_comparison must contain booleans")
    if policy and (
        set(policy) != {"raw_limit_bytes", "summary_limit_bytes", "root"}
        or not isinstance(policy["raw_limit_bytes"], int)
        or isinstance(policy["raw_limit_bytes"], bool)
        or policy["raw_limit_bytes"] <= 0
        or not isinstance(policy["summary_limit_bytes"], int)
        or isinstance(policy["summary_limit_bytes"], bool)
        or not 0 < policy["summary_limit_bytes"] <= policy["raw_limit_bytes"]
        or not isinstance(policy["root"], str)
        or not Path(policy["root"]).is_absolute()
    ):
        raise ConfigError("deployment plan command_evidence_policy is invalid")
    _validate_plan_checks(document["checks"], policy)
    confirmation = document["confirmation"]
    if confirmation and (
        set(confirmation) != {"template"}
        or not isinstance(confirmation["template"], str)
        or confirmation["template"] != "APPLY NIX-UPDATE PLAN {plan_digest}"
    ):
        raise ConfigError("deployment plan confirmation contract is invalid")
    if document["status"] == "READY_FOR_REVIEW":
        if any(not isinstance(item, str) for item in document["allowed_lock_nodes"]):
            raise ConfigError("ready deployment plan allowed_lock_nodes must contain strings")
        _validate_ready_live_and_hosts(document)
        _validate_plan_participant_results(document["participant_preflight"])
        if document["blockers"]:
            raise ConfigError("ready deployment plan cannot contain blockers")
        if not isinstance(document["target"].get("derivation_output"), str) or not document["target"]["derivation_output"]:
            raise ConfigError("ready deployment plan must bind an exact target")
        if not isinstance(document["participant_context"].get("digest"), str) or HEX_SHA256.fullmatch(document["participant_context"]["digest"]) is None:
            raise ConfigError("ready deployment plan must bind a participant context digest")
        if not policy or not confirmation:
            raise ConfigError("ready deployment plan must bind evidence and confirmation policy")
        context_document = document["participant_context"]["document"]
        expected_context_bindings = {
            "selected_repository": document["selected_flake"]["repository"],
            "selected_flake": document["selected_flake"]["path"],
            "flake_lock": document["lock"],
            "host": document["selected_flake"]["host"],
            "environment": document["environment"],
            "target": {"output": document["target"]["derivation_output"], "closure": document["target"]["closure"]},
            "overrides": document["overrides"],
            "projection": document["projection"],
            "sources": document["input_snapshots"],
            "control_projection": document["control_projection"],
            "profile": document["live"].get("profile"),
            "state_probes": document["state_probes"],
            "external_state": document["external_state"],
            "participant_declarations": document["participants"],
        }
        for key, expected_value in expected_context_bindings.items():
            if context_document.get(key) != expected_value:
                raise ConfigError(f"ready deployment plan participant context does not bind {key}")


def _validate_ready_live_and_hosts(document: Mapping[str, Any]) -> None:
    evaluation = document["evaluation_current"]
    if (
        set(evaluation) != {"exists", "link"}
        or not isinstance(evaluation["exists"], bool)
        or not isinstance(evaluation["link"], (str, type(None)))
    ):
        raise ConfigError("ready deployment plan evaluation_current is invalid")
    live = document["live"]
    if set(live) != {"profile", "closure"} or not isinstance(live["profile"], dict) or not isinstance(live["closure"], list):
        raise ConfigError("ready deployment plan live identity is invalid")
    delta = document["closure_delta"]
    if set(delta) != {"added", "removed", "unapproved"} or any(
        not isinstance(delta[key], list) or any(not isinstance(item, str) for item in delta[key])
        for key in ("added", "removed", "unapproved")
    ):
        raise ConfigError("ready deployment plan closure_delta is invalid")
    for item in document["host_candidates"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"host", "output", "closure"}
            or not isinstance(item["host"], str)
            or not item["host"]
            or not isinstance(item["output"], str)
            or not isinstance(item["closure"], list)
        ):
            raise ConfigError("ready deployment plan host candidate is invalid")


def _validate_plan_participant_results(items: list[Any]) -> None:
    fields = {
        "name", "phase", "status", "blockers", "observations", "rollback_handle",
        "context_digest", "attestation_digest", "stdout", "stderr", "failure_kind", "timeout_seconds",
        "mutation_state",
    }
    for item in items:
        if not isinstance(item, dict) or set(item) != fields:
            raise ConfigError("deployment plan participant result has missing or unknown fields")
        if (
            not isinstance(item["name"], str)
            or not item["name"]
            or not isinstance(item["phase"], str)
            or item["status"] not in {"ok", "blocked", "failed"}
            or not isinstance(item["blockers"], (list, tuple))
            or any(not isinstance(value, str) for value in item["blockers"])
            or not isinstance(item["observations"], dict)
            or not isinstance(item["rollback_handle"], (str, type(None)))
            or not isinstance(item["context_digest"], str)
            or HEX_SHA256.fullmatch(item["context_digest"]) is None
            or not isinstance(item["attestation_digest"], str)
            or HEX_SHA256.fullmatch(item["attestation_digest"]) is None
            or not isinstance(item["stdout"], str)
            or not isinstance(item["stderr"], str)
            or not isinstance(item["failure_kind"], (str, type(None)))
            or not isinstance(item["timeout_seconds"], (int, type(None)))
            or isinstance(item["timeout_seconds"], bool)
            or item["mutation_state"] not in {None, "unchanged", "mutated", "partial", "indeterminate"}
        ):
            raise ConfigError("deployment plan participant result field type is invalid")


LOCAL_REPOSITORY_FIELDS = {
    "repository_root",
    "commit",
    "tree",
    "branch",
    "status",
    "operation_state",
    "index_sha256",
    "flake_nix",
    "flake_lock",
    "ignored_status_sha256",
    "ignored_status_count",
}


def _validate_plan_local_repository(value: Any, selected_path: str) -> None:
    if not isinstance(value, dict) or set(value) != LOCAL_REPOSITORY_FIELDS:
        raise ConfigError("deployment plan selected_flake repository has missing or unknown fields")
    root_str = value.get("repository_root")
    if not isinstance(root_str, str) or not Path(root_str).is_absolute():
        raise ConfigError("deployment plan selected_flake repository repository_root must be an absolute path")
    repo_root = Path(root_str)
    try:
        Path(selected_path).relative_to(repo_root)
    except ValueError:
        raise ConfigError("deployment plan selected_flake repository repository_root does not contain selected flake")

    for field in ("commit", "tree"):
        raw_val = value.get(field)
        if not isinstance(raw_val, str) or HEX_SHA1.fullmatch(raw_val) is None:
            raise ConfigError(f"deployment plan selected_flake repository {field} must be a 40-character hex SHA-1")

    branch = value.get("branch")
    if not isinstance(branch, str) or not branch or branch == "HEAD" or branch.strip() != branch:
        raise ConfigError("deployment plan selected_flake repository branch must be a non-empty branch name and not HEAD")

    if value.get("status") != "":
        raise ConfigError("deployment plan selected_flake repository status must be clean")

    if value.get("operation_state") != "clean":
        raise ConfigError("deployment plan selected_flake repository operation_state must be clean")

    for field in ("index_sha256", "ignored_status_sha256"):
        raw_val = value.get(field)
        if not isinstance(raw_val, str) or HEX_SHA256.fullmatch(raw_val) is None:
            raise ConfigError(f"deployment plan selected_flake repository {field} must be a 64-character hex SHA-256")

    count_str = value.get("ignored_status_count")
    if not isinstance(count_str, str) or not count_str.isdigit():
        raise ConfigError("deployment plan selected_flake repository ignored_status_count must be a digit string")

    for filename in ("flake_nix", "flake_lock"):
        expected_name = filename.replace("_", ".")
        _validate_plan_repository_file_identity(value.get(filename), expected_name, repo_root)


def _validate_plan_repository_file_identity(raw_json: Any, expected_filename: str, repo_root: Path) -> None:
    if not isinstance(raw_json, str):
        raise ConfigError(f"deployment plan selected_flake repository {expected_filename} must be a JSON string")
    try:
        identity = json.loads(raw_json)
    except json.JSONDecodeError as error:
        raise ConfigError(f"deployment plan selected_flake repository {expected_filename} JSON is invalid") from error
    if not isinstance(identity, dict):
        raise ConfigError(f"deployment plan selected_flake repository {expected_filename} must be a JSON object")
    required_keys = {"path", "mode", "uid", "gid", "kind", "link_count", "sha256", "size"}
    if set(identity) != required_keys:
        raise ConfigError(f"deployment plan selected_flake repository {expected_filename} has invalid identity fields")
    file_path_str = identity.get("path")
    if not isinstance(file_path_str, str) or not Path(file_path_str).is_absolute():
        raise ConfigError(f"deployment plan selected_flake repository {expected_filename} path must be an absolute path")
    file_path = Path(file_path_str)
    if file_path.name != expected_filename:
        raise ConfigError(f"deployment plan selected_flake repository {expected_filename} filename mismatch")
    try:
        file_path.relative_to(repo_root)
    except ValueError:
        raise ConfigError(f"deployment plan selected_flake repository {expected_filename} must be inside repository root")
    if identity.get("kind") != "regular":
        raise ConfigError(f"deployment plan selected_flake repository {expected_filename} must be a regular file")
    if identity.get("link_count") != 1:
        raise ConfigError(f"deployment plan selected_flake repository {expected_filename} must have a single link")
    sha_val = identity.get("sha256")
    if not isinstance(sha_val, str) or HEX_SHA256.fullmatch(sha_val) is None:
        raise ConfigError(f"deployment plan selected_flake repository {expected_filename} sha256 must be 64-character hex")
    for int_field in ("mode", "uid", "gid", "size"):
        val = identity.get(int_field)
        if not isinstance(val, int) or isinstance(val, bool) or val < 0:
            raise ConfigError(f"deployment plan selected_flake repository {expected_filename} {int_field} must be a nonnegative integer")


def _validate_plan_selected_flake(value: Mapping[str, Any], is_ready: bool = True) -> None:
    if set(value) != {"path", "host", "repository"}:
        raise ConfigError("deployment plan selected_flake has missing or unknown fields")
    if not isinstance(value["path"], str) or not Path(value["path"]).is_absolute():
        raise ConfigError("deployment plan selected_flake path must be absolute")
    if not isinstance(value["host"], str) or not value["host"]:
        raise ConfigError("deployment plan selected_flake host must be nonempty")
    if not isinstance(value["repository"], dict):
        raise ConfigError("deployment plan selected_flake repository must be an object")
    if is_ready or value["repository"]:
        _validate_plan_local_repository(value["repository"], value["path"])


def _validate_plan_target(value: Mapping[str, Any]) -> None:
    if set(value) != {"derivation_output", "closure"}:
        raise ConfigError("deployment plan target has missing or unknown fields")
    if not isinstance(value["derivation_output"], str):
        raise ConfigError("deployment plan target output must be a string")
    if not isinstance(value["closure"], list):
        raise ConfigError("deployment plan target closure must be a list")
    for item in value["closure"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "narHash", "narSize"}
            or not isinstance(item["path"], str)
            or not isinstance(item["narHash"], (str, type(None)))
            or not isinstance(item["narSize"], (int, type(None)))
            or isinstance(item["narSize"], bool)
        ):
            raise ConfigError("deployment plan target closure entry is invalid")


def _validate_plan_projection(value: Mapping[str, Any], required: bool) -> None:
    if not value and not required:
        return
    if set(value) != {"root", "files", "digest", "published"}:
        raise ConfigError("deployment plan projection has missing or unknown fields")
    if (
        not isinstance(value["root"], str)
        or not Path(value["root"]).is_absolute()
        or not isinstance(value["files"], list)
        or any(
            not isinstance(item, (list, tuple))
            or len(item) != 2
            or any(not isinstance(part, str) for part in item)
            for item in value["files"]
        )
        or not isinstance(value["digest"], str)
        or not value["digest"]
        or not isinstance(value["published"], bool)
    ):
        raise ConfigError("deployment plan projection field type is invalid")


def _validate_plan_control_projection(
    value: Mapping[str, Any], transactions: Sequence[Mapping[str, Any]]
) -> None:
    if not value and not transactions:
        return
    fields = {
        "control_root", "current_tree", "virtual_root", "projected_tree", "transactions",
        "effective_transactions", "digest",
    }
    if set(value) != fields:
        raise ConfigError("deployment plan control_projection has missing or unknown fields")
    if (
        not isinstance(value["control_root"], str) or not Path(value["control_root"]).is_absolute()
        or not isinstance(value["virtual_root"], str) or not Path(value["virtual_root"]).is_absolute()
        or not isinstance(value["transactions"], list)
        or value["effective_transactions"] != list(transactions)
        or not isinstance(value["digest"], str) or HEX_SHA256.fullmatch(value["digest"]) is None
    ):
        raise ConfigError("deployment plan control_projection field type is invalid")
    _validate_tree_identity(value["current_tree"], "control current")
    _validate_tree_identity(value["projected_tree"], "control projected")
    expected = sha256_bytes(canonical_bytes({
        "projected_tree": value["projected_tree"], "transactions": value["transactions"],
    }))
    if value["digest"] != expected:
        raise ConfigError("deployment plan control_projection digest is invalid")


def _validate_plan_semantic_bindings(document: Mapping[str, Any]) -> None:
    overrides = document["overrides"]
    if len(overrides) % 3:
        raise ConfigError("deployment plan overrides must be exact override-input triples")
    configured: dict[str, str] = {}
    for index in range(0, len(overrides), 3):
        if overrides[index] != "--override-input" or not isinstance(overrides[index + 1], str):
            raise ConfigError("deployment plan overrides must be exact override-input triples")
        name = overrides[index + 1]
        locator = overrides[index + 2]
        if name in configured or not isinstance(locator, str) or not locator.startswith("path:"):
            raise ConfigError("deployment plan override input is duplicated or non-path")
        configured[name] = locator[5:]
    expected = {item["input"]: item["path"] for item in document["input_snapshots"]}
    projection = document["projection"]
    if projection:
        expected["localConfig"] = projection["root"]
    if configured != expected:
        raise ConfigError("deployment plan overrides do not bind exact sources and projection")

    control = document["control_projection"]
    if not control:
        return
    if (
        control["current_tree"]["root_identity"]["path"] != control["control_root"]
        or control["projected_tree"]["root_identity"]["path"] != control["virtual_root"]
        or len(control["transactions"]) != len(document["control_transactions"])
    ):
        raise ConfigError("deployment plan control projection roots or transaction count are inconsistent")
    for identity, specification in zip(control["transactions"], document["control_transactions"]):
        preimage = base64.b64decode(specification["preimage_bytes_b64"], validate=True)
        postimage = base64.b64decode(specification["postimage_bytes_b64"], validate=True)
        expected_pre = {
            "kind": specification["pre_kind"], "uid": specification["pre_uid"],
            "gid": specification["pre_gid"], "mode": specification["pre_mode"],
            "link_count": specification["pre_link_count"], "size": len(preimage),
            "sha256": specification["preimage_sha256"],
        }
        expected_post = {
            "kind": "regular", "uid": specification["pre_uid"],
            "gid": specification["pre_gid"], "mode": specification["post_mode"],
            "link_count": 1, "size": len(postimage),
            "sha256": specification["postimage_sha256"],
        }
        if (
            not isinstance(identity, dict)
            or identity.get("path") != specification["path"]
            or identity.get("effective_preimage") != expected_pre
            or identity.get("projected_postimage") != expected_post
            or identity.get("projected_postimage_actual") != expected_post
            or identity.get("metadata_policy") != specification["metadata_policy"]
        ):
            raise ConfigError("deployment plan control transaction identity is inconsistent")


def _validate_plan_source_identities(items: Sequence[Any]) -> None:
    names: set[str] = set()
    for item in items:
        required = {"name", "input", "lock_node", "kind", "path", "nar_hash"}
        if (
            not isinstance(item, dict) or not required <= set(item)
            or not set(item) <= required | {"locator"}
            or any(not isinstance(item[key], str) or not item[key] for key in required)
            or not Path(item["path"]).is_absolute()
            or item["name"] in names
        ):
            raise ConfigError("deployment plan source identity is invalid")
        names.add(item["name"])


def _validate_plan_participant_context(value: Mapping[str, Any], required: bool) -> None:
    if not value and not required:
        return
    if set(value) != {"path", "digest", "document"}:
        raise ConfigError("deployment plan participant_context has missing or unknown fields")
    if (
        not isinstance(value["path"], str)
        or not Path(value["path"]).is_absolute()
        or not isinstance(value["digest"], str)
        or HEX_SHA256.fullmatch(value["digest"]) is None
        or not isinstance(value["document"], dict)
    ):
        raise ConfigError("deployment plan participant_context field type is invalid")
    if sha256_bytes(canonical_bytes(value["document"])) != value["digest"]:
        raise ConfigError("deployment plan participant_context digest does not match its document")
    document = value["document"]
    fields = {
        "schema", "protocol", "stage", "plan_digest", "selected_repository", "selected_flake",
        "flake_lock", "host", "environment", "target", "overrides", "overrides_digest",
        "projection", "sources", "control_projection", "profile", "state_probes", "external_state",
        "participant_declarations", "participant_declaration_digest", "plan_draft_identity",
    }
    if set(document) != fields:
        raise ConfigError("deployment plan participant context document has missing or unknown fields")
    if (
        document["schema"] != "nix-update-participant-context-v1"
        or document["protocol"] != PARTICIPANT_SCHEMA
        or not isinstance(document["stage"], str)
        or not document["stage"]
        or not isinstance(document["plan_digest"], str)
        or not isinstance(document["selected_repository"], dict)
        or not isinstance(document["selected_flake"], str)
        or not Path(document["selected_flake"]).is_absolute()
        or not isinstance(document["flake_lock"], dict)
        or not isinstance(document["host"], str)
        or not document["host"]
        or not isinstance(document["environment"], dict)
        or not isinstance(document["target"], dict)
        or set(document["target"]) != {"output", "closure"}
        or not isinstance(document["target"]["output"], str)
        or not isinstance(document["target"]["closure"], list)
        or any(not isinstance(document[key], list) for key in ("overrides", "sources", "state_probes", "external_state", "participant_declarations"))
        or not isinstance(document["projection"], dict)
        or not isinstance(document["control_projection"], dict)
        or not isinstance(document["profile"], dict)
    ):
        raise ConfigError("deployment plan participant context document field type is invalid")
    expected_overrides = sha256_bytes(canonical_bytes({"overrides": document["overrides"]}))
    expected_declarations = sha256_bytes(canonical_bytes({"participants": document["participant_declarations"]}))
    unsigned = dict(document)
    recorded_draft = unsigned.pop("plan_draft_identity")
    if (
        document["overrides_digest"] != expected_overrides
        or document["participant_declaration_digest"] != expected_declarations
        or not isinstance(recorded_draft, str)
        or recorded_draft != sha256_bytes(canonical_bytes(unsigned))
    ):
        raise ConfigError("deployment plan participant context identity is invalid")


def _validate_plan_checks(items: list[Any], policy: Mapping[str, Any]) -> None:
    for item in items:
        if not isinstance(item, dict):
            raise ConfigError("deployment plan check must be an object")
        required = {"label", "argv", "cwd", "started_at", "finished_at", "exit_status", "stdout", "stderr"}
        if set(item) != required:
            raise ConfigError("deployment plan check has missing or unknown fields")
        if (
            not isinstance(item["label"], str)
            or not isinstance(item["argv"], (list, tuple))
            or any(not isinstance(value, str) for value in item["argv"])
            or not isinstance(item["cwd"], str)
            or not Path(item["cwd"]).is_absolute()
            or not isinstance(item["started_at"], str)
            or not isinstance(item["finished_at"], str)
            or not isinstance(item["exit_status"], int)
            or isinstance(item["exit_status"], bool)
        ):
            raise ConfigError("deployment plan check field type is invalid")
        for stream in (item["stdout"], item["stderr"]):
            if (
                not isinstance(stream, dict)
                or set(stream) != {"path", "sha256", "retained_sha256", "size", "retained_bytes", "overflow", "limit", "summary"}
                or not isinstance(stream["path"], str)
                or not Path(stream["path"]).is_absolute()
                or not isinstance(stream["sha256"], str)
                or HEX_SHA256.fullmatch(stream["sha256"]) is None
                or not isinstance(stream["retained_sha256"], str)
                or HEX_SHA256.fullmatch(stream["retained_sha256"]) is None
                or any(not isinstance(stream[key], int) or isinstance(stream[key], bool) for key in ("size", "retained_bytes", "limit"))
                or not isinstance(stream["overflow"], bool)
                or not isinstance(stream["summary"], str)
            ):
                raise ConfigError("deployment plan command stream evidence is invalid")
            path = Path(stream["path"])
            shared_require_private_file(path, "deployment command evidence")
            raw = path.read_bytes()
            if (
                stream["limit"] <= 0
                or stream["size"] < 0
                or stream["retained_bytes"] != min(stream["size"], stream["limit"])
                or stream["overflow"] != (stream["size"] > stream["limit"])
                or len(raw) != stream["retained_bytes"]
                or sha256_bytes(raw) != stream["retained_sha256"]
                or not stream["overflow"] and stream["retained_sha256"] != stream["sha256"]
                or len(stream["summary"].encode()) > 8192
            ):
                raise ConfigError("deployment plan command evidence identity is invalid")
            if policy:
                root = Path(str(policy["root"]))
                try:
                    path.relative_to(root)
                except ValueError as error:
                    raise ConfigError("deployment plan command evidence is outside its reviewed root") from error
                if (
                    stream["limit"] != policy["raw_limit_bytes"]
                    or len(stream["summary"].encode()) > policy["summary_limit_bytes"]
                ):
                    raise ConfigError("deployment plan command evidence identity is invalid")
            summary_limit = policy.get("summary_limit_bytes", 8192) if policy else 8192
            expected_summary = redacted_summary(raw, summary_limit)
            if stream["summary"] != expected_summary:
                raise ConfigError("deployment plan command evidence summary is not bound to retained bytes")


def _validate_plan_probe_identities(items: list[Any], label: str) -> None:
    names: set[str] = set()
    for item in items:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "kind", "policy", "identity"}
            or not isinstance(item["name"], str)
            or not item["name"]
            or item["name"] in names
            or item["kind"] not in {"file", "tree"}
            or not isinstance(item["policy"], dict)
            or not isinstance(item["identity"], dict)
        ):
            raise ConfigError(f"deployment plan {label} identity is invalid")
        _validate_probes([item["policy"]], label)
        if item["policy"]["name"] != item["name"] or item["policy"]["kind"] != item["kind"]:
            raise ConfigError(f"deployment plan {label} policy identity is invalid")
        if item["kind"] == "tree":
            _validate_tree_identity(item["identity"], label)
            if item["identity"]["root_identity"]["path"] != item["policy"]["path"]:
                raise ConfigError(f"deployment plan {label} root does not equal its probe path")
        else:
            _validate_file_identity(item["identity"], label)
            if item["identity"]["path"] != item["policy"]["path"]:
                raise ConfigError(f"deployment plan {label} file does not equal its probe path")
        names.add(item["name"])


def _validate_file_identity(value: Any, label: str) -> None:
    if not isinstance(value, dict):
        raise ConfigError(f"deployment plan {label} file identity is invalid")
    required = {"path", "mode", "uid", "gid", "kind", "link_count"}
    if not required <= set(value) or not set(value) <= required | {"sha256", "size", "target", "target_identity"}:
        raise ConfigError(f"deployment plan {label} file identity is invalid")
    if (
        not isinstance(value["path"], str) or not Path(value["path"]).is_absolute()
        or value["kind"] not in {"regular", "directory", "symlink", "other"}
        or any(not isinstance(value[key], int) or isinstance(value[key], bool) for key in ("mode", "uid", "gid", "link_count"))
    ):
        raise ConfigError(f"deployment plan {label} file identity is invalid")


def _validate_tree_identity(value: Any, label: str) -> None:
    if not isinstance(value, dict) or set(value) != {"root_identity", "content_identity", "digest"}:
        raise ConfigError(f"deployment plan {label} tree identity is invalid")
    root = value["root_identity"]
    content = value["content_identity"]
    if not isinstance(root, dict) or not isinstance(content, dict):
        raise ConfigError(f"deployment plan {label} tree identity is invalid")
    root_common = {"path", "kind", "mode", "uid", "gid", "link_count", "resolved_target"}
    root_expected = root_common | ({"target"} if root.get("kind") == "symlink" else set())
    if (
        set(root) != root_expected
        or root.get("kind") not in {"directory", "symlink"}
        or not _is_normalized_absolute_path(root.get("path"))
        or not _is_normalized_absolute_path(root.get("resolved_target"))
        or any(not _is_nonnegative_int(root.get(field)) for field in ("mode", "uid", "gid", "link_count"))
        or root["mode"] > 0o7777
        or root["link_count"] < 1
        or (root["kind"] == "symlink" and (not isinstance(root.get("target"), str) or not root["target"]))
    ):
        raise ConfigError(f"deployment plan {label} tree root identity is invalid")
    if set(content) != {"entries", "volatile", "digest"}:
        raise ConfigError(f"deployment plan {label} tree content identity is invalid")
    entries = content.get("entries")
    volatile = content.get("volatile")
    if not isinstance(entries, list) or not isinstance(volatile, list):
        raise ConfigError(f"deployment plan {label} tree content identity is invalid")
    paths: list[str] = []
    volatile_by_path: dict[str, Mapping[str, Any]] = {}
    for policy in volatile:
        _validate_volatile_policy(policy)
        if policy["path"] in volatile_by_path:
            raise ConfigError(f"deployment plan {label} volatile path is duplicated")
        volatile_by_path[policy["path"]] = policy
    seen_volatile: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ConfigError(f"deployment plan {label} tree entry is invalid")
        common = {"path", "kind", "mode", "uid", "gid", "link_count"}
        kind = entry.get("kind")
        specific = {
            "regular": {"sha256", "size"},
            "directory": set(),
            "symlink": {"target", "target_identity"},
        }.get(kind)
        if specific is None:
            raise ConfigError(f"deployment plan {label} tree entry kind is invalid")
        expected = common | specific | ({"volatile"} if "volatile" in entry else set())
        if set(entry) != expected:
            raise ConfigError(f"deployment plan {label} tree entry fields are invalid")
        _validate_relative_tree_path(entry.get("path"), f"deployment plan {label} tree entry")
        if (
            any(not _is_nonnegative_int(entry.get(field)) for field in ("mode", "uid", "gid", "link_count"))
            or entry["mode"] > 0o7777
            or entry["link_count"] < 1
        ):
            raise ConfigError(f"deployment plan {label} tree entry metadata is invalid")
        if kind == "regular" and (
            not isinstance(entry["sha256"], str)
            or HEX_SHA256.fullmatch(entry["sha256"]) is None
            or not _is_nonnegative_int(entry["size"])
        ):
            raise ConfigError(f"deployment plan {label} regular tree entry is invalid")
        if kind == "symlink" and (
            not isinstance(entry["target"], str)
            or not entry["target"]
            or not _is_normalized_absolute_path(entry["target_identity"])
        ):
            raise ConfigError(f"deployment plan {label} symlink tree entry is invalid")
        if "volatile" in entry:
            annotation = entry["volatile"]
            policy = volatile_by_path.get(entry["path"])
            if (
                policy is None
                or not isinstance(annotation, dict)
                or set(annotation) != {"producer", "content_policy"}
                or annotation != {"producer": policy["producer"], "content_policy": policy["content_policy"]}
            ):
                raise ConfigError(f"deployment plan {label} volatile tree entry is invalid")
            seen_volatile.add(entry["path"])
        paths.append(entry["path"])
    if paths != sorted(paths) or len(paths) != len(set(paths)) or seen_volatile != set(volatile_by_path):
        raise ConfigError(f"deployment plan {label} tree entry paths are invalid")
    content_material = {"entries": entries, "volatile": volatile}
    if (
        not isinstance(content.get("digest"), str)
        or HEX_SHA256.fullmatch(content["digest"]) is None
        or content["digest"] != sha256_bytes(canonical_bytes(content_material))
    ):
        raise ConfigError(f"deployment plan {label} tree content digest is invalid")
    complete_material = {"root_identity": root, "content_identity": content}
    if (
        not isinstance(value.get("digest"), str)
        or HEX_SHA256.fullmatch(value["digest"]) is None
        or value["digest"] != sha256_bytes(canonical_bytes(complete_material))
    ):
        raise ConfigError(f"deployment plan {label} tree digest is invalid")


def _exclusive_write(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConfigError(f"unable to read {label}: {path}") from error
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a JSON object")
    return value


def redacted_summary(raw: bytes, limit: int) -> str:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ConfigError("redacted summary limit must be a positive integer")
    safe = redact_text(raw.decode("utf-8", errors="replace")).encode("utf-8")[:limit]
    return safe.decode("utf-8", errors="ignore")


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigError(f"unknown {label} key: {unknown[0]}")


def _reject_secrets(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if SECRET_KEYS.search(str(key)):
                raise ConfigError(f"secret-bearing key is forbidden in evidence: {path}.{key}")
            _reject_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secrets(child, f"{path}[{index}]")
    elif isinstance(value, str):
        if _has_credential_userinfo(value):
            raise ConfigError(f"credential-bearing URL is forbidden in evidence: {path}")
        if (
            redact_text(value) != value
            or SECRET_VALUE.search(value)
            or ("-----BEGIN" in value and "PRIVATE KEY-----" in value)
        ):
            raise ConfigError(f"secret-bearing value is forbidden in evidence: {path}")


def reject_secrets(value: Any) -> None:
    _reject_secrets(value)


def _require_private_directory(path: Path, label: str) -> None:
    shared_require_private_directory(path, label)


def _require_private_file(path: Path, label: str) -> None:
    shared_require_private_file(path, label)


def _normalized_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalized_json(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalized_json(item) for item in value]
    return value


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_normalized_absolute_path(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and Path(value).is_absolute()
        and os.path.normpath(value) == value
    )


def _validate_relative_tree_path(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ConfigError(f"{label} path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in {"", ".", ".."} for part in path.parts):
        raise ConfigError(f"{label} path is not a normalized safe relative path")


def _require_lexical_child(path_value: str, root_value: str, label: str) -> None:
    path = Path(path_value)
    root = Path(root_value)
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ConfigError(f"{label} escapes its reviewed root") from error
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ConfigError(f"{label} must be a normalized descendant of its reviewed root")


def _validate_participant_artifact_identity(value: Any, path: str, digest: str) -> None:
    fields = {"path", "device", "inode", "uid", "gid", "mode", "link_count", "size", "sha256"}
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("path") != path
        or value.get("sha256") != digest
        or any(
            not _is_nonnegative_int(value.get(field))
            for field in ("device", "inode", "uid", "gid", "mode", "link_count", "size")
        )
        or value["mode"] > 0o7777
        or value["mode"] & 0o077
        or value["mode"] & 0o111 == 0
        or value["link_count"] != 1
    ):
        raise ConfigError("operator participant artifact identity is invalid")


def _kind(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "regular"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "other"


def _is_nix_store_path(path: Path) -> bool:
    return path != Path("/nix/store") and Path("/nix/store") in path.parents


def _fsync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
