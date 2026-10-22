"""Validation and diagnostic helpers for native promotion results."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from .deployment_paths import require_private_directory, require_private_file
from .deployment_plan import canonical_bytes, redact_text, sha256_bytes
from .paths import ConfigError
from .promotion_contract import reject_promotion_secrets, strict_json_loads


LEGACY_RESULT_SCHEMA = "nix-update-promotion-result-v1"
RESULT_SCHEMA = "nix-update-promotion-result-v2"
MAX_RESULT_BYTES = 10 * 1024 * 1024
MAX_FAILURE_BYTES = 2048
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")

LEGACY_SUCCESS_STATES = frozenset({"PROMOTION_PUBLISHED_AWAITING_ACTIVATION"})
LOCAL_SUCCESS_STATES = frozenset({"LOCAL_PROMOTION_COMMITTED"})
LOCAL_COMMIT_CREATED_STATES = frozenset({"LOCAL_COMMIT_CREATED"})
SUCCESS_STATES = LEGACY_SUCCESS_STATES | LOCAL_SUCCESS_STATES
RETRYABLE_PUBLICATION_STATES = frozenset(
    {"COMMITTED_NOT_PUBLISHED", "PUBLICATION_INDETERMINATE"}
)
REPREPARE_STATES = frozenset(
    {
        "BLOCKED_BEFORE_MUTATION",
        "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED",
        "PROMOTION_RESULT_EVIDENCE_FAILURE",
        "PROMOTION_PUBLISHED_REPOSITORY_STATE_FAILURE",
        "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORATION_UNPROVEN",
    }
)
LEGACY_TERMINAL_STATES = (
    LEGACY_SUCCESS_STATES | RETRYABLE_PUBLICATION_STATES | REPREPARE_STATES
)
TERMINAL_STATES = (
    SUCCESS_STATES
    | LOCAL_COMMIT_CREATED_STATES
    | RETRYABLE_PUBLICATION_STATES
    | REPREPARE_STATES
)
V2_RESULT_KEYS = frozenset(
    {
        "schema",
        "plan_digest",
        "no_activation",
        "terminal_state",
        "local_commit",
        "published_commit",
        "publication_state",
        "activation_state",
        "started_at",
        "finished_at",
        "failure",
        "recovery",
        "impact",
        "commands",
        "result_path",
    }
)
PUBLICATION_STATES = frozenset(
    {"pending", "unavailable", "published", "indeterminate", "rejected"}
)


def load_native_result(
    plan_directory: Path,
    plan_digest: str,
    *,
    expected_path: Path | None = None,
) -> dict[str, Any]:
    """Load and validate the result written by ``apply_promotion``.

    The coordinator treats the result file as evidence. A successful process
    exit without this file, or with a malformed file, is a blocked operation.
    """

    plan_directory = _normal_directory(plan_directory, "promotion plan directory")
    if not isinstance(plan_digest, str) or HEX64.fullmatch(plan_digest) is None:
        raise ConfigError("promotion plan digest is invalid")
    result_path = plan_directory / "result.json"
    if expected_path is not None:
        expected_path = _normal_path(expected_path, "promotion result path")
        if expected_path != result_path:
            raise ConfigError("promotion result path is not bound to its plan")
    require_private_file(result_path, "promotion result")
    raw = result_path.read_bytes()
    if len(raw) > MAX_RESULT_BYTES:
        raise ConfigError("promotion result exceeds its size limit")
    try:
        document = strict_json_loads(raw.decode("utf-8"), "promotion result")
    except (UnicodeDecodeError, ConfigError) as error:
        raise ConfigError("promotion result is not valid JSON") from error
    if raw != canonical_bytes(document):
        raise ConfigError("promotion result bytes are not canonical")
    validate_native_result(document, plan_digest)
    result = _record(document, result_path, raw)
    return result


def validate_native_result(document: Any, plan_digest: str) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ConfigError("promotion result must be a JSON object")
    schema = document.get("schema")
    if schema not in {LEGACY_RESULT_SCHEMA, RESULT_SCHEMA}:
        raise ConfigError("promotion result schema is invalid")
    if schema == RESULT_SCHEMA:
        return _validate_v2_result(document, plan_digest)
    return _validate_v1_result(document, plan_digest)


def _validate_v1_result(document: Mapping[str, Any], plan_digest: str) -> dict[str, Any]:
    if document.get("plan_digest") != plan_digest:
        raise ConfigError("promotion result plan digest does not match the prepared plan")
    if document.get("no_activation") is not True:
        raise ConfigError("promotion result must explicitly forbid activation")
    state = document.get("terminal_state")
    if not isinstance(state, str) or state not in LEGACY_TERMINAL_STATES:
        raise ConfigError("promotion result terminal state is invalid")
    published = document.get("published_commit")
    if published is not None and (
        not isinstance(published, str) or HEX40.fullmatch(published) is None
    ):
        raise ConfigError("promotion result published commit is invalid")
    if state in SUCCESS_STATES | RETRYABLE_PUBLICATION_STATES and published is None:
        raise ConfigError("promotion result publication state has no commit identity")
    failure = document.get("failure")
    if failure is not None and not isinstance(failure, str):
        raise ConfigError("promotion result failure is invalid")
    if isinstance(failure, str) and len(failure) > MAX_FAILURE_BYTES * 4:
        raise ConfigError("promotion result failure exceeds its size limit")
    commands = document.get("commands")
    if commands is not None:
        _validate_commands(commands)
    reject_promotion_secrets(document)
    return dict(document)


def _validate_v2_result(document: Mapping[str, Any], plan_digest: str) -> dict[str, Any]:
    if set(document) != V2_RESULT_KEYS:
        raise ConfigError("promotion result v2 fields are invalid")
    if document.get("plan_digest") != plan_digest:
        raise ConfigError("promotion result plan digest does not match the prepared plan")
    if document.get("no_activation") is not True:
        raise ConfigError("promotion result must explicitly forbid activation")
    state = document.get("terminal_state")
    if not isinstance(state, str) or state not in TERMINAL_STATES:
        raise ConfigError("promotion result terminal state is invalid")
    local_commit = document.get("local_commit")
    if local_commit is not None and (
        not isinstance(local_commit, str) or HEX40.fullmatch(local_commit) is None
    ):
        raise ConfigError("promotion result local commit is invalid")
    if state in LOCAL_SUCCESS_STATES | LOCAL_COMMIT_CREATED_STATES and local_commit is None:
        raise ConfigError("local promotion result has no commit identity")
    published = document.get("published_commit")
    if published is not None and (
        not isinstance(published, str) or HEX40.fullmatch(published) is None
    ):
        raise ConfigError("promotion result published commit is invalid")
    publication_state = document.get("publication_state")
    if publication_state not in PUBLICATION_STATES:
        raise ConfigError("promotion result publication state is invalid")
    if state in LOCAL_SUCCESS_STATES:
        if publication_state not in {"pending", "unavailable"}:
            raise ConfigError("local promotion result publication state is invalid")
        if published is not None:
            raise ConfigError("local promotion result claims publication")
        if document.get("failure") is not None:
            raise ConfigError("local promotion success contains failure details")
    elif state == "LOCAL_COMMIT_CREATED":
        if publication_state == "published":
            raise ConfigError("local commit-created result claims publication")
    elif state in LEGACY_SUCCESS_STATES:
        if publication_state != "published" or published is None:
            raise ConfigError("published promotion result lacks publication identity")
    if document.get("activation_state") != "not_performed":
        raise ConfigError("promotion result contains an activation claim")
    failure = document.get("failure")
    if failure is not None and not isinstance(failure, str):
        raise ConfigError("promotion result failure is invalid")
    if isinstance(failure, str) and len(failure) > MAX_FAILURE_BYTES * 4:
        raise ConfigError("promotion result failure exceeds its size limit")
    _validate_commands(document.get("commands"))
    reject_promotion_secrets(document)
    return dict(document)


def _record(document: Mapping[str, Any], path: Path, raw: bytes) -> dict[str, Any]:
    state = str(document["terminal_state"])
    failure = failure_from_result(document, path)
    local = state in LOCAL_SUCCESS_STATES
    return {
        "path": os.fspath(path),
        "digest": sha256_bytes(raw),
        "plan_digest": document["plan_digest"],
        "terminal_state": state,
        "local_commit": document.get("local_commit"),
        "published_commit": document.get("published_commit"),
        "publication_state": document.get("publication_state"),
        "activation_state": document.get("activation_state", "not_performed"),
        "status": "LOCAL_COMMITTED" if local else ("PUBLISHED" if state in LEGACY_SUCCESS_STATES else "BLOCKED"),
        "failure": failure,
        "impact": document.get("impact"),
        "document": dict(document),
    }


def failure_from_result(document: Mapping[str, Any], result_path: Path) -> dict[str, str] | None:
    failure_text = document.get("failure")
    if isinstance(failure_text, str) and failure_text:
        operation, evidence = _first_failed_command(document.get("commands"), result_path)
        return _failure(failure_text, operation, evidence)
    if document.get("terminal_state") in SUCCESS_STATES:
        return None
    operation, evidence = _first_failed_command(document.get("commands"), result_path)
    return _failure(
        f"native promotion ended in {document.get('terminal_state', 'unknown')}",
        operation,
        evidence,
    )


def failure_from_records(workflow: Any, cause: str, *, since_index: int = 0) -> dict[str, str]:
    """Extract the first failed command while retaining its private evidence path."""

    records = list(getattr(workflow, "records", ()) or ())
    if since_index > 0:
        records = records[since_index:]
    first = None
    for record in records:
        status = _record_value(record, "exit_status")
        if isinstance(status, int) and status != 0:
            first = record
            break
    if first is None:
        return _failure(cause, "native promotion", None)
    operation = _record_value(first, "label") or "native promotion"
    evidence = _record_evidence(first)
    summary = _record_summary(first)
    message = cause
    if summary:
        message = f"{cause}: {summary}"
    return _failure(message, str(operation), evidence)


def _first_failed_command(commands: Any, fallback: Path) -> tuple[str, Path | None]:
    if isinstance(commands, list):
        for item in commands:
            if not isinstance(item, dict):
                continue
            status = item.get("exit_status")
            if isinstance(status, int) and status != 0:
                return str(item.get("label") or "native promotion"), _command_evidence(item, fallback)
    return "native promotion", fallback


def _validate_commands(commands: Any) -> None:
    if not isinstance(commands, list) or len(commands) > 4096:
        raise ConfigError("promotion result command evidence is invalid")
    for item in commands:
        if not isinstance(item, dict):
            raise ConfigError("promotion result command record is invalid")
        if not isinstance(item.get("label"), str) or len(item["label"]) > 512:
            raise ConfigError("promotion result command label is invalid")
        if not isinstance(item.get("exit_status"), int):
            raise ConfigError("promotion result command status is invalid")
        for stream in ("stdout", "stderr"):
            value = item.get(stream)
            if value is not None and not isinstance(value, dict):
                raise ConfigError("promotion result command evidence is invalid")
            if isinstance(value, dict):
                path = value.get("path")
                if path is not None and (
                    not isinstance(path, str) or len(path) > 4096 or not Path(path).is_absolute()
                ):
                    raise ConfigError("promotion result evidence path is invalid")
                summary = value.get("summary")
                if summary is not None and (not isinstance(summary, str) or len(summary) > 65536):
                    raise ConfigError("promotion result evidence summary is invalid")


def _command_evidence(item: Mapping[str, Any], fallback: Path) -> Path | None:
    stderr = item.get("stderr") if isinstance(item.get("stderr"), Mapping) else None
    stdout = item.get("stdout") if isinstance(item.get("stdout"), Mapping) else None
    if stderr and stderr.get("summary") and isinstance(stderr.get("path"), str):
        path = Path(stderr["path"])
        if path.is_absolute() and "\n" not in stderr["path"]:
            return path
    if stdout and stdout.get("summary") and isinstance(stdout.get("path"), str):
        path = Path(stdout["path"])
        if path.is_absolute() and "\n" not in stdout["path"]:
            return path
    for stream in ("stderr", "stdout"):
        value = item.get(stream)
        if isinstance(value, dict) and isinstance(value.get("path"), str):
            path = Path(value["path"])
            if path.is_absolute() and "\n" not in value["path"]:
                return path
    return fallback


def _record_evidence(record: Any) -> Path | None:
    stderr = _record_value(record, "stderr")
    stdout = _record_value(record, "stdout")
    if isinstance(stderr, Mapping) and stderr.get("summary") and isinstance(stderr.get("path"), str):
        path = Path(stderr["path"])
        if path.is_absolute() and "\n" not in stderr["path"]:
            return path
    if isinstance(stdout, Mapping) and stdout.get("summary") and isinstance(stdout.get("path"), str):
        path = Path(stdout["path"])
        if path.is_absolute() and "\n" not in stdout["path"]:
            return path
    for stream in ("stderr", "stdout"):
        value = _record_value(record, stream)
        if isinstance(value, Mapping) and isinstance(value.get("path"), str):
            path = Path(value["path"])
            if path.is_absolute() and "\n" not in value["path"]:
                return path
    return None


def _record_summary(record: Any) -> str:
    stderr = _record_value(record, "stderr")
    if isinstance(stderr, Mapping) and isinstance(stderr.get("summary"), str) and stderr["summary"]:
        return stderr["summary"]
    stdout = _record_value(record, "stdout")
    if isinstance(stdout, Mapping) and isinstance(stdout.get("summary"), str) and stdout["summary"]:
        return stdout["summary"]
    return ""


def _record_value(record: Any, name: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(name)
    return getattr(record, name, None)


def _failure(message: str, operation: str, evidence: Path | None) -> dict[str, str]:
    safe_message = redact_text(str(message)).replace("\x00", "")[:MAX_FAILURE_BYTES]
    safe_operation = redact_text(str(operation)).replace("\x00", "")[:256]
    if not safe_message:
        safe_message = "native promotion failed"
    if not safe_operation:
        safe_operation = "native promotion"
    result = {"message": safe_message, "operation": safe_operation, "evidence": None}
    if evidence is not None:
        path = os.fspath(evidence)
        if Path(path).is_absolute() and "\n" not in path and "\x00" not in path:
            result["evidence"] = path[:4096]
    return result


def _normal_directory(path: Path, label: str) -> Path:
    path = _normal_path(path, label)
    require_private_directory(path, label)
    return path


def _normal_path(path: Path, label: str) -> Path:
    path = Path(path)
    if not path.is_absolute():
        raise ConfigError(f"{label} must be absolute")
    normalized = Path(os.path.normpath(os.fspath(path)))
    if normalized != path:
        raise ConfigError(f"{label} must be normalized")
    return path
