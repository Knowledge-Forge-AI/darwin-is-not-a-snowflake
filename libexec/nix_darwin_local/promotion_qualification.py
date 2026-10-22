from __future__ import annotations

import os
import shutil
import stat
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .command_diagnostics import failure_explanation
from .deployment_paths import exclusive_private_write
from .deployment_plan import canonical_bytes, file_identity, redact_text, sha256_bytes
from .deployment_workflow import Workflow, _executable_identity
from .paths import ConfigError
from .promotion_contract import (
    SAFE_IDENTITY,
    reject_promotion_secrets,
    strict_json_loads,
    validate_contract,
)
from .promotion_result import (
    DRV_PATH,
    MAX_NATIVE_RESULT_BYTES,
    validate_native_result,
)


CONTRACT_LIMIT = 128 * 1024


def evaluate_contract(
    workflow: Workflow, selected: Path, *, allow_file_urls: bool = False
) -> dict[str, Any]:
    label = "promotion contract evaluation"
    result = workflow.command(
        label,
        ["nix", "eval", "--json", "path:.#lib.promotion", "--no-update-lock-file", "--no-write-lock-file"],
        selected,
    )
    if result.returncode:
        raise ConfigError("unable to evaluate selected composition promotion contract")
    raw = workflow.complete_stdout(label)
    if len(raw.encode()) > CONTRACT_LIMIT:
        raise ConfigError("promotion contract result exceeds the bounded JSON limit")
    return validate_contract(
        strict_json_loads(raw, "promotion contract evaluation"),
        allow_file_urls=allow_file_urls,
    )


def qualifier_identity(selected: Path, contract: Mapping[str, Any]) -> dict[str, Any]:
    relative = contract["qualification"]["entryPoint"]
    root = selected.resolve(strict=True)
    candidate = selected / relative
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise ConfigError("promotion qualifier entry point is outside the selected repository") from error
    metadata = candidate.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or not os.access(candidate, os.X_OK)
    ):
        raise ConfigError("promotion qualifier must be an owned executable single-link regular file")
    identity = file_identity(candidate)
    identity["path"] = relative
    return {"relative_path": relative, "identity": identity}


def _literal_host_selector(host: str, suffix: str) -> str:
    if not isinstance(host, str) or SAFE_IDENTITY.fullmatch(host) is None:
        raise ConfigError("promotion host identity is invalid")
    return f'path:.#darwinConfigurations."{host}".{suffix}'


def run_qualifier(
    workflow: Workflow,
    composition: Path,
    input_name: str,
    candidate_ref: str,
    contract: Mapping[str, Any],
    timeout_seconds: int | None,
    *,
    label: str,
    allow_file_urls: bool = False,
    expected_identity: Mapping[str, Any] | None = None,
    is_local: bool = False,
) -> dict[str, Any]:
    qualifier = qualifier_identity(composition, contract)
    if input_name == "core" or is_local:
        from .promotion_core_qualification import qualify_core

        if timeout_seconds is not None and (
            type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600
        ):
            raise ConfigError("promotion qualifier timeout must be between 1 and 3600 seconds")
        document = qualify_core(
            workflow, composition, candidate_ref, contract, allow_file_urls=allow_file_urls,
            **({"input_name": input_name} if input_name != "core" else {}),
        )
        validate_qualifier_result(
            document, input_name, candidate_ref, contract,
            allow_file_urls=allow_file_urls, expected_identity=expected_identity,
        )
        return document
    argv = [
        str(composition / qualifier["relative_path"]),
        "--composition", str(composition),
        "--input", input_name,
        "--candidate", candidate_ref,
        "--json",
    ]
    if timeout_seconds is not None:
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
            raise ConfigError("promotion qualifier timeout must be between 1 and 3600 seconds")
        argv.extend(["--timeout-seconds", str(timeout_seconds)])
    result = workflow.command(label, argv, composition)
    if result.returncode:
        raise ConfigError(_failure_explanation(workflow, label, qualifier["relative_path"], result.returncode))
    raw = workflow.complete_stdout(label)
    if len(raw.encode()) > MAX_NATIVE_RESULT_BYTES:
        raise ConfigError("promotion qualifier result exceeds the 10 MiB machine-JSON limit")
    try:
        document = strict_json_loads(raw, "promotion qualifier")
        validate_qualifier_result(
            document,
            input_name,
            candidate_ref,
            contract,
            allow_file_urls=allow_file_urls,
            expected_identity=expected_identity,
        )
    except ConfigError as error:
        raise ConfigError(
            f"{redact_text(str(error))}; "
            + _failure_explanation(workflow, label, qualifier["relative_path"], result.returncode)
        ) from error
    return document


def _failure_explanation(workflow: Workflow, label: str, entry_point: str, status: int) -> str:
    return failure_explanation(workflow, label, status, entry_point=entry_point)


def validate_qualifier_result(
    document: Any,
    input_name: str,
    candidate_ref: str,
    contract: Mapping[str, Any],
    *,
    allow_file_urls: bool = False,
    expected_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return validate_native_result(
        document,
        input_name,
        candidate_ref,
        contract,
        allow_file_urls=allow_file_urls,
        expected_identity=expected_identity,
    )


def observe_host_derivations(
    workflow: Workflow,
    tree: Path,
    contract: Mapping[str, Any],
    *,
    label_prefix: str,
) -> dict[str, Any]:
    start = len(workflow.records)
    derivations: dict[str, str] = {}
    for host in contract["hosts"]:
        label = f"{label_prefix} {host}"
        command = [
            "nix",
            "eval",
            "--raw",
            _literal_host_selector(host, "system.drvPath"),
            "--no-update-lock-file",
            "--no-write-lock-file",
        ]
        child = workflow.command(label, command, tree)
        if child.returncode:
            raise ConfigError(f"unable to observe declared promotion host derivation: {host}")
        values = [
            line
            for line in workflow.complete_stdout(label).splitlines()
            if line
        ]
        if (
            len(values) != 1
            or DRV_PATH.fullmatch(values[0]) is None
        ):
            raise ConfigError(f"declared promotion host derivation is invalid: {host}")
        derivations[host] = values[0]
    return {
        "hosts": derivations,
        "commands": [asdict(record) for record in workflow.records[start:]],
    }


def qualify_locked_tree(
    workflow: Workflow,
    tree: Path,
    input_name: str,
    candidate_ref: str,
    contract: Mapping[str, Any],
    timeout_seconds: int | None,
    output: Path,
    *,
    retain: bool = True,
    allow_file_urls: bool = False,
    expected_result_identity: Mapping[str, Any] | None = None,
    is_local: bool = False,
) -> dict[str, Any]:
    start = len(workflow.records)
    result = run_qualifier(
        workflow, tree, input_name, candidate_ref, contract, timeout_seconds,
        label="locked composition qualifier",
        allow_file_urls=allow_file_urls,
        expected_identity=expected_result_identity,
        is_local=is_local,
    )
    result_summary = validate_qualifier_result(
        result,
        input_name,
        candidate_ref,
        contract,
        allow_file_urls=allow_file_urls,
        expected_identity=expected_result_identity,
    )
    qualifier_binding = None
    if retain:
        qualifier_binding = write_json_artifact(
            output, "locked-qualifier-result.json", result, summary=result_summary
        )
    check_results: list[dict[str, Any]] = []
    system = contract["system"]
    for check in contract["checks"]:
        label = f"locked composition check {check}"
        command = [
            "nix", "build", "--no-link", "--print-out-paths",
            f"path:.#checks.{system}.{check}", "--no-update-lock-file", "--no-write-lock-file",
        ]
        child = workflow.command(label, command, tree)
        if child.returncode:
            raise ConfigError(f"declared promotion check failed: {check}")
        check_results.append({"name": check, "outcome": "pass"})
    host_results: list[dict[str, Any]] = []
    host_outputs: dict[str, str] = {}
    for host in contract["hosts"]:
        label = f"locked composition host {host}"
        command = [
            "nix", "build", "--no-link", "--print-out-paths",
            _literal_host_selector(host, "system"), "--no-update-lock-file", "--no-write-lock-file",
        ]
        child = workflow.command(label, command, tree)
        if child.returncode:
            raise ConfigError(f"declared promotion host failed: {host}")
        outputs = [line for line in workflow.complete_stdout(label).splitlines() if line]
        if len(outputs) != 1 or not outputs[0].startswith("/nix/store/"):
            raise ConfigError(f"declared promotion host returned an invalid output identity: {host}")
        host_outputs[host] = outputs[0]
    observation = observe_host_derivations(
        workflow,
        tree,
        contract,
        label_prefix="locked composition host derivation",
    )
    for host in contract["hosts"]:
        host_results.append(
            {
                "name": host,
                "outcome": "pass",
                "output": host_outputs[host],
                "derivation": observation["hosts"][host],
            }
        )
    return {
        "qualifier": qualifier_binding,
        "checks": check_results,
        "hosts": host_results,
        "commands": [asdict(record) for record in workflow.records[start:]],
        "no_activation": True,
    }


def promotion_tool_identity(core_dir: Path) -> dict[str, Any]:
    source_root = core_dir / "libexec/nix_darwin_local"
    paths = [core_dir / "bin/nix-update"]
    for path in sorted(source_root.rglob("*")):
        if "__pycache__" in path.relative_to(source_root).parts:
            continue
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            continue
        paths.append(path)
    identities = [_source_identity(core_dir, path) for path in paths]
    external: dict[str, Any] = {}
    for name in ("git", "nix"):
        executable = shutil.which(name)
        if executable is None:
            raise ConfigError(f"required promotion executable is unavailable: {name}")
        external[name] = _executable_identity(Path(executable))
    material = {"files": identities, "external": external}
    return {
        "name": "nix-update-promote",
        "version": "1",
        **material,
        "digest": sha256_bytes(canonical_bytes(material)),
    }


def write_json_artifact(
    output: Path,
    name: str,
    document: Mapping[str, Any],
    *,
    summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    reject_promotion_secrets(document)
    raw = canonical_bytes(document)
    if len(raw) > MAX_NATIVE_RESULT_BYTES:
        raise ConfigError("promotion JSON evidence exceeds the 10 MiB artifact limit")
    path = output / name
    exclusive_private_write(path, raw)
    binding = {"path": name, "sha256": sha256_bytes(raw), "size": len(raw)}
    if summary is not None:
        binding["summary"] = dict(summary)
    return binding


def _source_identity(core_dir: Path, path: Path) -> dict[str, Any]:
    metadata = path.lstat()
    immutable_store_source = (
        path.resolve() == path
        and str(path).startswith("/nix/store/")
        and metadata.st_uid == 0
        and not metadata.st_mode & 0o222
    )
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or (not immutable_store_source and (
            metadata.st_uid != os.getuid() or metadata.st_nlink != 1
        ))
    ):
        raise ConfigError("promotion tool source must be an owned single-link regular file")
    identity = file_identity(path)
    identity["path"] = path.relative_to(core_dir).as_posix()
    return identity
