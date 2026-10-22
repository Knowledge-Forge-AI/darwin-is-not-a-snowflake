"""Owner-private state for the attended composition promotion coordinator.

The native promotion transaction remains the authority for repository changes.
This module only records which native artifacts have been prepared or consumed
so a later invocation can continue from the last durable boundary.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .deployment_paths import (
    atomic_private_write,
    create_private_directory,
    ensure_private_root,
    require_private_directory,
    require_private_file,
    validate_ancestor_chain,
)
from .deployment_plan import canonical_bytes, redact_text, sha256_bytes
from .paths import ConfigError
from .promotion_contract import reject_promotion_secrets, strict_json_loads


LEGACY_CAMPAIGN_SCHEMA = "nix-update-promotion-campaign-v1"
LEGACY_CAMPAIGN_VERSION = 1
CAMPAIGN_SCHEMA = "nix-update-promotion-campaign-v2"
CAMPAIGN_VERSION = 2
JOURNAL_NAME = "campaign.json"
INDEX_NAME = "campaign-index.json"
MAX_JOURNAL_BYTES = 2 * 1024 * 1024
MAX_INDEX_BYTES = 512 * 1024
MAX_HISTORY = 256
MAX_ATTEMPTS = 64
MAX_TEXT = 4096
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")

STATES = frozenset(
    {
        "NEW",
        "PREPARING",
        "AWAITING_APPLY",
        "BLOCKED",
        "COMPLETE_NO_ACTIVATION",
        "AWAITING_ACTIVATION",
        "AWAITING_DEPLOYMENT_RECONCILIATION",
        "LOCAL_COMPLETE_AWAITING_ACTIVATION",
        "DRY_RUN",
    }
)
LEGACY_STATES = frozenset(
    {
        "NEW",
        "PREPARING",
        "AWAITING_APPLY",
        "BLOCKED",
        "COMPLETE_NO_ACTIVATION",
        "AWAITING_ACTIVATION",
        "AWAITING_DEPLOYMENT_RECONCILIATION",
        "DRY_RUN",
    }
)
STAGE_STATUSES = frozenset(
    {
        "NEW",
        "PREPARING",
        "READY_FOR_REVIEW",
        "ALREADY_SELECTED",
        "APPLYING",
        "PUBLISHED",
        "LOCAL_COMMITTED",
        "BLOCKED",
        "DRY_RUN",
    }
)
LEGACY_STAGE_STATUSES = frozenset(
    {
        "NEW",
        "PREPARING",
        "READY_FOR_REVIEW",
        "ALREADY_SELECTED",
        "APPLYING",
        "PUBLISHED",
        "BLOCKED",
        "DRY_RUN",
    }
)
JOURNAL_KEYS = frozenset(
    {
        "schema",
        "version",
        "campaign_digest",
        "state",
        "last_outcome",
        "activationPerformed",
        "created_at",
        "updated_at",
        "input",
        "revision",
        "selected",
        "dry_run",
        "stages",
        "current_stage",
        "final_impact",
        "blocker",
        "history",
    }
)
LEGACY_STAGE_KEYS = frozenset(
    {
        "name",
        "kind",
        "repository",
        "input",
        "revision",
        "status",
        "attempts",
        "current_plan",
        "published_commit",
        "impact",
        "failure",
    }
)
STAGE_KEYS = LEGACY_STAGE_KEYS | frozenset(
    {
        "local_commit",
        "publication_state",
        "activation_state",
    }
)
LEGACY_ATTEMPT_KEYS = frozenset(
    {
        "number",
        "plan_path",
        "plan_digest",
        "plan_status",
        "result_path",
        "result_digest",
        "terminal_state",
        "published_commit",
        "failure",
    }
)
ATTEMPT_KEYS = LEGACY_ATTEMPT_KEYS | frozenset(
    {
        "local_commit",
        "publication_state",
        "activation_state",
    }
)
PUBLICATION_STATES = frozenset(
    {"pending", "unavailable", "published", "indeterminate", "rejected"}
)
INDEX_SCHEMA = "nix-update-promotion-campaign-index-v1"
INDEX_REQUIRED_KEYS = frozenset(
    {"campaign", "selected", "input", "revision", "state", "updated_at"}
)
INDEX_ENTRY_KEYS = frozenset(
    {"campaign", "selected", "input", "revision", "state", "dry_run", "updated_at"}
)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def normal_path(path: Path, label: str) -> Path:
    if not isinstance(path, Path):
        path = Path(path)
    if not path.is_absolute():
        path = path.absolute()
    normalized = Path(os.path.normpath(os.fspath(path)))
    if normalized != path:
        raise ConfigError(f"{label} must be an exact normalized absolute path: {path}")
    return path


def _text(value: Any, label: str, *, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ConfigError(f"{label} is invalid")
    return value


def _optional_text(value: Any, label: str, *, limit: int = MAX_TEXT) -> str | None:
    if value is None:
        return None
    return _text(value, label, limit=limit)


def _stage_template(
    name: str,
    kind: str,
    repository: Path,
    input_name: str,
    revision: str | None,
) -> dict[str, Any]:
    return {
        "name": _text(name, "campaign stage name", limit=128),
        "kind": _text(kind, "campaign stage kind", limit=64),
        "repository": os.fspath(normal_path(repository, "campaign stage repository")),
        "input": _text(input_name, "campaign stage input", limit=128),
        "revision": revision,
        "status": "NEW",
        "attempts": [],
        "current_plan": None,
        "local_commit": None,
        "published_commit": None,
        "publication_state": None,
        "activation_state": "not_performed",
        "impact": None,
        "failure": None,
    }


def new_campaign(
    selected: Path,
    input_name: str,
    revision: str,
    stages: Iterable[Mapping[str, Any]],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    selected = normal_path(selected, "selected flake")
    _text(input_name, "campaign input", limit=128)
    _text(revision, "campaign revision", limit=128)
    normalized: list[dict[str, Any]] = []
    for raw in stages:
        if not isinstance(raw, Mapping):
            raise ConfigError("campaign topology stage is invalid")
        name = raw.get("name", raw.get("stage", "stage"))
        kind = raw.get("kind", raw.get("role", "composition"))
        repository = raw.get("repository", raw.get("path"))
        stage_input = raw.get("input", raw.get("input_name", input_name))
        stage_revision = raw.get("revision", revision if not normalized else None)
        if repository is None:
            raise ConfigError(f"campaign stage {name!r} has no repository")
        normalized.append(
            _stage_template(
                str(name), str(kind), Path(repository), str(stage_input),
                None if stage_revision is None else str(stage_revision),
            )
        )
    if not normalized:
        raise ConfigError("promotion campaign topology contains no stages")
    if len({stage["name"] for stage in normalized}) != len(normalized):
        raise ConfigError("promotion campaign topology contains duplicate stages")
    created = now()
    document = {
        "schema": CAMPAIGN_SCHEMA,
        "version": CAMPAIGN_VERSION,
        "campaign_digest": "",
        "state": "NEW",
        "last_outcome": "NEW",
        "activationPerformed": False,
        "created_at": created,
        "updated_at": created,
        "input": input_name,
        "revision": revision,
        "selected": os.fspath(selected),
        "dry_run": bool(dry_run),
        "stages": normalized,
        "current_stage": 0,
        "final_impact": None,
        "blocker": None,
        "history": [{"at": created, "from": None, "to": "NEW", "reason": "campaign created"}],
    }
    document = seal(document)
    validate_campaign(document, raw=None, campaign=None)
    return document


def seal(document: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(document)
    value["campaign_digest"] = ""
    digest = sha256_bytes(canonical_bytes(value))
    value["campaign_digest"] = digest
    return value


def campaign_digest(document: Mapping[str, Any]) -> str:
    recorded = document.get("campaign_digest")
    if not isinstance(recorded, str) or HEX64.fullmatch(recorded) is None:
        raise ConfigError("campaign digest is invalid")
    unsigned = dict(document)
    unsigned["campaign_digest"] = ""
    expected = sha256_bytes(canonical_bytes(unsigned))
    if expected != recorded:
        raise ConfigError("campaign digest does not match canonical content")
    return recorded


def persist_campaign(campaign: Path, document: Mapping[str, Any]) -> dict[str, Any]:
    campaign = normal_path(campaign, "campaign directory")
    require_private_directory(campaign, "campaign directory")
    value = _normalize_v2_legacy_shapes(document)
    value = seal(value)
    validate_campaign(value, raw=None, campaign=campaign)
    atomic_private_write(campaign / JOURNAL_NAME, canonical_bytes(value))
    return value


def load_campaign(campaign: Path) -> dict[str, Any]:
    campaign = normal_path(campaign, "campaign directory")
    require_private_directory(campaign, "campaign directory")
    path = campaign / JOURNAL_NAME
    require_private_file(path, "campaign journal")
    raw = path.read_bytes()
    if len(raw) > MAX_JOURNAL_BYTES:
        raise ConfigError("campaign journal exceeds its size limit")
    try:
        value = strict_json_loads(raw.decode("utf-8"), "campaign journal")
    except (UnicodeDecodeError, ConfigError) as error:
        raise ConfigError("campaign journal is not valid canonical JSON") from error
    validate_campaign(value, raw=raw, campaign=campaign)
    return value


def validate_campaign(
    value: Any,
    *,
    raw: bytes | None,
    campaign: Path | None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != JOURNAL_KEYS:
        raise ConfigError("campaign journal fields are invalid")
    schema = value.get("schema")
    version = value.get("version")
    legacy = schema == LEGACY_CAMPAIGN_SCHEMA and version == LEGACY_CAMPAIGN_VERSION
    current = schema == CAMPAIGN_SCHEMA and version == CAMPAIGN_VERSION
    if not legacy and not current:
        raise ConfigError("campaign journal schema or version is invalid")
    if raw is not None and raw != canonical_bytes(value):
        raise ConfigError("campaign journal bytes are not canonical")
    campaign_digest(value)
    valid_states = LEGACY_STATES if legacy else STATES
    if value.get("state") not in valid_states or value.get("last_outcome") != value["state"]:
        raise ConfigError("campaign state or last outcome is invalid")
    if value.get("activationPerformed") is not False:
        raise ConfigError("campaign journal contains an activation claim")
    if type(value.get("dry_run")) is not bool:
        raise ConfigError("campaign dry-run marker is invalid")
    selected = _text(value.get("selected"), "campaign selected path", limit=4096)
    if not Path(selected).is_absolute() or Path(os.path.normpath(selected)) != Path(selected):
        raise ConfigError("campaign selected path is invalid")
    _text(value.get("input"), "campaign input", limit=128)
    _text(value.get("revision"), "campaign revision", limit=128)
    if type(value.get("current_stage")) is not int or value["current_stage"] < 0:
        raise ConfigError("campaign current stage is invalid")
    stages = value.get("stages")
    if not isinstance(stages, list) or not stages or len(stages) > 32:
        raise ConfigError("campaign stages are invalid")
    if value["current_stage"] > len(stages):
        raise ConfigError("campaign current stage is out of range")
    names: set[str] = set()
    for stage in stages:
        _validate_stage(stage, campaign, legacy=legacy)
        if stage["name"] in names:
            raise ConfigError("campaign stage names are not unique")
        names.add(stage["name"])
    _validate_blocker(value.get("blocker"))
    _validate_history(value.get("history"))
    if value.get("final_impact") is not None and not isinstance(value["final_impact"], dict):
        raise ConfigError("campaign final impact is invalid")
    reject_promotion_secrets(value)
    return dict(value)


def _normalize_v2_legacy_shapes(document: Mapping[str, Any]) -> dict[str, Any]:
    """Add v2 fields for callers that still construct v1-shaped stage values.

    The production v2 validator remains strict. This narrow write-boundary
    adapter keeps existing API callers that copy a legacy stage dictionary
    from losing their campaign when the journal is persisted.
    """

    value = dict(document)
    if value.get("schema") != CAMPAIGN_SCHEMA or value.get("version") != CAMPAIGN_VERSION:
        return value
    for stage in value.get("stages", []):
        if set(stage) == LEGACY_STAGE_KEYS:
            stage.update(
                local_commit=None,
                publication_state=None,
                activation_state="not_performed",
            )
        for attempt in stage.get("attempts", []):
            if set(attempt) == LEGACY_ATTEMPT_KEYS:
                attempt.update(
                    local_commit=None,
                    publication_state=None,
                    activation_state="not_performed",
                )
    return value


def _validate_stage(value: Any, campaign: Path | None, *, legacy: bool = False) -> None:
    expected_keys = LEGACY_STAGE_KEYS if legacy else STAGE_KEYS
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ConfigError("campaign stage fields are invalid")
    _text(value.get("name"), "campaign stage name", limit=128)
    _text(value.get("kind"), "campaign stage kind", limit=64)
    repository = _text(value.get("repository"), "campaign stage repository", limit=4096)
    path = Path(repository)
    if not path.is_absolute() or Path(os.path.normpath(repository)) != path:
        raise ConfigError("campaign stage repository path is invalid")
    _text(value.get("input"), "campaign stage input", limit=128)
    revision = value.get("revision")
    if revision is not None:
        _text(revision, "campaign stage revision", limit=128)
    valid_statuses = LEGACY_STAGE_STATUSES if legacy else STAGE_STATUSES
    if value.get("status") not in valid_statuses:
        raise ConfigError("campaign stage status is invalid")
    attempts = value.get("attempts")
    if not isinstance(attempts, list) or len(attempts) > MAX_ATTEMPTS:
        raise ConfigError("campaign stage attempts are invalid")
    for expected, attempt in enumerate(attempts, 1):
        attempt_keys = LEGACY_ATTEMPT_KEYS if legacy else ATTEMPT_KEYS
        if not isinstance(attempt, dict) or set(attempt) != attempt_keys:
            raise ConfigError("campaign stage attempt fields are invalid")
        if attempt.get("number") != expected:
            raise ConfigError("campaign stage attempt sequence is invalid")
        _validate_path_or_none(attempt.get("plan_path"), "campaign plan path")
        _optional_hex(attempt.get("plan_digest"), "campaign plan digest", HEX64)
        _optional_text(attempt.get("plan_status"), "campaign plan status", limit=64)
        _validate_path_or_none(attempt.get("result_path"), "campaign result path")
        _optional_hex(attempt.get("result_digest"), "campaign result digest", HEX64)
        _optional_text(attempt.get("terminal_state"), "campaign terminal state", limit=128)
        if not legacy:
            _optional_hex(attempt.get("local_commit"), "campaign local commit", HEX40)
            publication_state = attempt.get("publication_state")
            if publication_state is not None and publication_state not in PUBLICATION_STATES:
                raise ConfigError("campaign publication state is invalid")
            if attempt.get("activation_state") != "not_performed":
                raise ConfigError("campaign attempt contains an activation claim")
        _optional_hex(attempt.get("published_commit"), "campaign published commit", HEX40)
        _validate_failure(attempt.get("failure"))
    _validate_path_or_none(value.get("current_plan"), "campaign current plan")
    if not legacy:
        _optional_hex(value.get("local_commit"), "campaign local commit", HEX40)
        publication_state = value.get("publication_state")
        if publication_state is not None and publication_state not in PUBLICATION_STATES:
            raise ConfigError("campaign publication state is invalid")
        if value.get("activation_state") != "not_performed":
            raise ConfigError("campaign stage contains an activation claim")
        if value.get("status") == "LOCAL_COMMITTED" and value.get("local_commit") is None:
            raise ConfigError("local campaign stage has no local commit identity")
    _optional_hex(value.get("published_commit"), "campaign published commit", HEX40)
    if value.get("impact") is not None and not isinstance(value["impact"], dict):
        raise ConfigError("campaign stage impact is invalid")
    _validate_failure(value.get("failure"))
    if campaign is not None:
        for field in ("current_plan",):
            path_value = value[field]
            if path_value is not None and not Path(path_value).is_absolute():
                raise ConfigError(f"campaign {field} must be absolute")


def _validate_path_or_none(value: Any, label: str) -> None:
    if value is None:
        return
    text = _text(value, label, limit=4096)
    path = Path(text)
    if not path.is_absolute() or Path(os.path.normpath(text)) != path:
        raise ConfigError(f"{label} is invalid")


def _optional_hex(value: Any, label: str, pattern: re.Pattern[str]) -> None:
    if value is not None and (not isinstance(value, str) or pattern.fullmatch(value) is None):
        raise ConfigError(f"{label} is invalid")


def _validate_failure(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {"message", "operation", "evidence"}:
        raise ConfigError("campaign failure details are invalid")
    _text(value.get("message"), "campaign failure message", limit=2048)
    _text(value.get("operation"), "campaign failure operation", limit=256)
    _validate_path_or_none(value.get("evidence"), "campaign failure evidence")


def _validate_blocker(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {"stage", "failure"}:
        raise ConfigError("campaign blocker is invalid")
    _text(value.get("stage"), "campaign blocked stage", limit=128)
    _validate_failure(value.get("failure"))


def _validate_history(value: Any) -> None:
    if not isinstance(value, list) or len(value) > MAX_HISTORY:
        raise ConfigError("campaign history is invalid")
    for item in value:
        if not isinstance(item, dict) or set(item) != {"at", "from", "to", "reason"}:
            raise ConfigError("campaign history entry is invalid")
        _text(item.get("at"), "campaign history timestamp", limit=64)
        if item.get("from") is not None:
            _text(item["from"], "campaign history source state", limit=64)
        _text(item.get("to"), "campaign history target state", limit=64)
        _text(item.get("reason"), "campaign history reason", limit=MAX_TEXT)


def transition(
    document: Mapping[str, Any],
    state: str,
    reason: str,
    *,
    blocker: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if state not in STATES:
        raise ConfigError(f"campaign state is invalid: {state}")
    value = dict(document)
    previous = value.get("state")
    value["state"] = state
    value["last_outcome"] = state
    value["updated_at"] = now()
    value["blocker"] = None if blocker is None else dict(blocker)
    history = list(value.get("history", []))
    history.append({"at": value["updated_at"], "from": previous, "to": state, "reason": redact_text(str(reason))[:MAX_TEXT]})
    value["history"] = history[-MAX_HISTORY:]
    return value


def append_attempt(stage: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(stage)
    v2 = "local_commit" in value or "publication_state" in value or "activation_state" in value
    attempts = [dict(item) for item in value.get("attempts", [])]
    if len(attempts) >= MAX_ATTEMPTS:
        raise ConfigError(f"campaign stage attempts exceeded maximum ({MAX_ATTEMPTS})")
    number = len(attempts) + 1
    attempt = {
        "number": number,
        "plan_path": os.fspath(plan["path"]),
        "plan_digest": plan.get("digest"),
        "plan_status": plan.get("status"),
        "result_path": None,
        "result_digest": None,
        "terminal_state": None,
        "published_commit": plan.get("published_commit"),
        "failure": None,
    }
    if v2:
        attempt.update(
            local_commit=plan.get("local_commit"),
            publication_state=plan.get("publication_state"),
            activation_state=plan.get("activation_state", "not_performed"),
        )
    attempts.append(attempt)
    value["attempts"] = attempts
    value["current_plan"] = os.fspath(plan["path"])
    value["status"] = str(plan.get("status", "PREPARING"))
    if attempt["published_commit"]:
        value["published_commit"] = attempt["published_commit"]
    if v2 and attempt["local_commit"]:
        value["local_commit"] = attempt["local_commit"]
    if v2 and attempt["publication_state"] is not None:
        value["publication_state"] = attempt["publication_state"]
    value["failure"] = None
    return value


def update_attempt(stage: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(stage)
    v2 = "local_commit" in value or "publication_state" in value or "activation_state" in value
    attempts = [dict(item) for item in value.get("attempts", [])]
    if not attempts:
        raise ConfigError("campaign stage has no prepared attempt")
    attempt = attempts[-1]
    attempt["result_path"] = result.get("path")
    attempt["result_digest"] = result.get("digest")
    attempt["terminal_state"] = result.get("terminal_state")
    published_commit = result.get("published_commit")
    local_publication = result.get("publication_state")
    if v2 and local_publication in {"pending", "unavailable"}:
        # A local result may carry the commit again for diagnostic context;
        # it is not a publication claim until publication_state says so.
        published_commit = None
    attempt["published_commit"] = published_commit
    attempt["failure"] = result.get("failure")
    if v2:
        attempt["local_commit"] = result.get("local_commit")
        attempt["publication_state"] = result.get("publication_state")
        attempt["activation_state"] = result.get("activation_state", "not_performed")
    value["attempts"] = attempts
    value["current_plan"] = None
    value["status"] = str(result.get("status", "BLOCKED"))
    value["published_commit"] = published_commit
    if v2:
        value["local_commit"] = result.get("local_commit")
        value["publication_state"] = result.get("publication_state")
        value["activation_state"] = result.get("activation_state", "not_performed")
    value["impact"] = result.get("impact")
    value["failure"] = result.get("failure")
    return value


def allocate_campaign(
    state_root: Path,
    output: Path | None = None,
) -> Path:
    state_root = ensure_private_root(normal_path(state_root, "nix-update state root"), "nix-update state root")
    if output is not None:
        target = normal_path(output, "campaign output")
        validate_ancestor_chain(target.parent, owner_only=True)
        if target.exists() or target.is_symlink():
            raise ConfigError(f"campaign output already exists: {target}")
        return create_private_directory(target, "campaign directory")
    parent = ensure_private_root(state_root / "promotion-campaigns", "promotion campaign root")
    for _ in range(32):
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        candidate = parent / f"campaign-{stamp}-{os.urandom(6).hex()}"
        try:
            return create_private_directory(candidate, "campaign directory", parent=parent)
        except ConfigError as error:
            if "already exists" not in str(error):
                raise
    raise ConfigError("unable to allocate a unique campaign directory")


def _index_path(state_root: Path) -> Path:
    return ensure_private_root(normal_path(state_root, "nix-update state root"), "nix-update state root") / INDEX_NAME


def read_index(state_root: Path) -> list[dict[str, Any]]:
    path = _index_path(state_root)
    if not path.exists() and not path.is_symlink():
        return []
    require_private_file(path, "promotion campaign index")
    raw = path.read_bytes()
    if len(raw) > MAX_INDEX_BYTES:
        raise ConfigError("promotion campaign index exceeds its size limit")
    try:
        value = strict_json_loads(raw.decode("utf-8"), "promotion campaign index")
    except (UnicodeDecodeError, ConfigError) as error:
        raise ConfigError("promotion campaign index is invalid") from error
    if set(value) != {"schema", "version", "entries"} or value.get("schema") != INDEX_SCHEMA or value.get("version") != 1:
        raise ConfigError("promotion campaign index schema is invalid")
    entries = value.get("entries")
    if not isinstance(entries, list) or len(entries) > 256:
        raise ConfigError("promotion campaign index entries are invalid")
    results: list[dict[str, Any]] = []
    for entry in entries:
        _validate_index_entry(entry)
        item = dict(entry)
        if "dry_run" not in item:
            item["dry_run"] = item.get("state") == "DRY_RUN"
        results.append(item)
    return results


def _validate_index_entry(value: Any) -> None:
    if not isinstance(value, dict) or not (INDEX_REQUIRED_KEYS <= set(value) <= INDEX_ENTRY_KEYS):
        raise ConfigError("promotion campaign index entry is invalid")
    for field in ("campaign", "selected"):
        text = _text(value.get(field), f"promotion index {field}", limit=4096)
        path = Path(text)
        if not path.is_absolute() or Path(os.path.normpath(text)) != path:
            raise ConfigError(f"promotion index {field} path is invalid")
    _text(value.get("input"), "promotion index input", limit=128)
    _text(value.get("revision"), "promotion index revision", limit=128)
    _text(value.get("state"), "promotion index state", limit=64)
    if "dry_run" in value and type(value["dry_run"]) is not bool:
        raise ConfigError("promotion index dry_run is invalid")
    _text(value.get("updated_at"), "promotion index timestamp", limit=64)


def update_index(
    state_root: Path,
    *,
    campaign: Path,
    selected: Path,
    input_name: str,
    revision: str,
    state: str,
    dry_run: bool = False,
) -> None:
    root = ensure_private_root(normal_path(state_root, "nix-update state root"), "nix-update state root")
    entries = read_index(root)
    record = {
        "campaign": os.fspath(normal_path(campaign, "campaign directory")),
        "selected": os.fspath(normal_path(selected, "selected flake")),
        "input": _text(input_name, "campaign input", limit=128),
        "revision": _text(revision, "campaign revision", limit=128),
        "state": _text(state, "campaign state", limit=64),
        "dry_run": bool(dry_run),
        "updated_at": now(),
    }
    entries = [entry for entry in entries if entry["campaign"] != record["campaign"]]
    entries.append(record)
    entries.sort(key=lambda item: (item["updated_at"], item["campaign"]))
    entries = entries[-256:]
    document = {"schema": INDEX_SCHEMA, "version": 1, "entries": entries}
    atomic_private_write(_index_path(root), canonical_bytes(document))


def matching_campaigns(
    state_root: Path,
    *,
    selected: Path | None = None,
    input_name: str | None = None,
    revision: str | None = None,
    dry_run: bool | None = None,
) -> list[Path]:
    entries = read_index(state_root)
    selected_text = None if selected is None else os.fspath(normal_path(selected, "selected flake"))
    matches: list[Path] = []
    for entry in entries:
        if selected_text is not None and entry["selected"] != selected_text:
            continue
        if input_name is not None and entry["input"] != input_name:
            continue
        if revision is not None and entry["revision"] != revision:
            continue
        entry_dry_run = entry.get("dry_run", entry.get("state") == "DRY_RUN")
        if dry_run is not None and bool(entry_dry_run) != bool(dry_run):
            continue
        matches.append(Path(entry["campaign"]))
    return matches


def render_status(document: Mapping[str, Any], campaign: Path) -> str:
    lines = [
        "nix-update: promotion campaign",
        f"campaign: {campaign}",
        f"state: {document['state']}",
        f"input: {document['input']}",
        f"revision: {document['revision']}",
        f"selected: {document['selected']}",
        f"campaign digest: {document['campaign_digest']}",
        "activation performed: false",
    ]
    for index, stage in enumerate(document["stages"]):
        suffix = " (current)" if index == document["current_stage"] else ""
        lines.append(
            f"stage {stage['name']}{suffix}: {stage['status']} "
            f"input={stage['input']} revision={stage['revision'] or 'pending'}"
        )
        if stage.get("local_commit"):
            lines.append(f"  local commit: {stage['local_commit']}")
        if stage.get("published_commit"):
            lines.append(f"  published commit: {stage['published_commit']}")
        if stage.get("publication_state") is not None:
            lines.append(f"  publication state: {stage['publication_state']}")
        if stage.get("activation_state"):
            lines.append(f"  activation state: {stage['activation_state']}")
        if stage.get("failure"):
            failure = stage["failure"]
            lines.append(f"  failure: {failure['message']}")
            if failure.get("evidence"):
                lines.append(f"  evidence: {failure['evidence']}")
    if document.get("blocker"):
        failure = document["blocker"]["failure"]
        lines.append(f"blocked stage: {document['blocker']['stage']}")
        lines.append(f"cause: {failure['message']}")
        if failure.get("evidence"):
            lines.append(f"evidence: {failure['evidence']}")
    if document.get("final_impact"):
        impact = document["final_impact"]
        lines.append(f"final impact: {impact.get('classification', 'unknown')}")
        lines.append(f"activation signal: {impact.get('activationSignal', 'unknown')}")
    if document.get("state") == "AWAITING_DEPLOYMENT_RECONCILIATION":
        lines.append("guidance: run 'nix-update --dry-run' or 'nix-update' to observe or reconcile live host deployment")
    elif document.get("state") == "AWAITING_ACTIVATION":
        lines.append("guidance: run ordinary 'nix-update' to activate the new host configuration")
    elif document.get("state") == "COMPLETE_NO_ACTIVATION":
        lines.append("guidance: no additional host change from this promotion; run 'nix-update --dry-run' to inspect live host state")
    elif document.get("state") == "LOCAL_COMPLETE_AWAITING_ACTIVATION":
        lines.append(
            "guidance: local promotion commits are qualified; publish explicitly if desired, "
            "adopt into '[snapshot.sources]' (or pass '--source-revision'), then run ordinary 'nix-update' to activate"
        )
    elif document.get("state") == "AWAITING_APPLY":
        lines.append(f"guidance: run 'nix-update promote campaign resume --campaign {campaign}' in an interactive terminal to supply attended confirmation")
    elif document.get("state") == "BLOCKED":
        blocker = document.get("blocker")
        failure = blocker.get("failure", {}) if isinstance(blocker, Mapping) else {}
        if "confirmation" in str(failure.get("operation", "")).casefold():
            lines.append(f"guidance: run 'nix-update promote campaign resume --campaign {campaign}' in an interactive terminal to supply attended confirmation")
    return "\n".join(lines) + "\n"
