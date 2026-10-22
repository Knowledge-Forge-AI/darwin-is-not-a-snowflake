from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .paths import ConfigError
from .deployment_workflow import Workflow


SCHEMA = "nix-darwin-deployment-effects-v1"


def evaluate_deployment_effects(
    workflow: Workflow,
    selected: Path,
    host: str,
    overrides: Sequence[str],
) -> dict[str, Any]:
    label = "module deployment effects evaluation"
    command = [
        "nix",
        "eval",
        "--json",
        "--apply",
        "c: c.dinas.deploymentEffects or null",
        f"path:.#darwinConfigurations.{host}.config",
        "--no-update-lock-file",
        "--no-write-lock-file",
        *overrides,
    ]
    result = workflow.command(label, command, selected)
    if result.returncode:
        raise ConfigError(f"module deployment effects evaluation failed for host {host}")
    raw_output = workflow.complete_stdout(label).strip()
    if not raw_output or raw_output == "null":
        return {
            "schema": SCHEMA,
            "status": "absent",
            "preflightParticipants": [],
            "preSwitchTransactions": [],
            "postSwitchReconciliation": [],
            "externalMutableStateProbes": [],
            "participantLocalRollback": [],
        }
    try:
        data = json.loads(raw_output)
    except json.JSONDecodeError as error:
        raise ConfigError("module deployment effects evaluation emitted invalid JSON") from error

    if not isinstance(data, dict):
        raise ConfigError("module deployment effects must evaluate to a dictionary")

    normalized = {
        "schema": str(data.get("schema", SCHEMA)),
        "status": "declared",
        "preflightParticipants": list(data.get("preflightParticipants", [])),
        "preSwitchTransactions": list(data.get("preSwitchTransactions", [])),
        "postSwitchReconciliation": list(data.get("postSwitchReconciliation", [])),
        "externalMutableStateProbes": list(data.get("externalMutableStateProbes", [])),
        "participantLocalRollback": list(data.get("participantLocalRollback", [])),
    }
    return normalized


def classify_deployment_effects(effects: Mapping[str, Any]) -> tuple[bool, list[str]]:
    mutating_reasons: list[str] = []

    for item in effects.get("preSwitchTransactions", []):
        name = item.get("name", "unnamed")
        mutating_reasons.append(f"preSwitchTransactions declares mutating transaction: {name}")

    for item in effects.get("postSwitchReconciliation", []):
        name = item.get("name", "unnamed")
        mutating_reasons.append(f"postSwitchReconciliation declares mutating effect: {name}")

    for item in effects.get("participantLocalRollback", []):
        name = item.get("name", "unnamed")
        mutating_reasons.append(f"participantLocalRollback declares rollback authority: {name}")

    for item in effects.get("preflightParticipants", []):
        name = item.get("name", "unnamed")
        if not item.get("readOnly", True) or item.get("classification") == "mutation":
            mutating_reasons.append(f"preflightParticipants declares mutating participant: {name}")

    for item in effects.get("externalMutableStateProbes", []):
        name = item.get("name", "unnamed")
        mutating_reasons.append(f"externalMutableStateProbes requires advanced external-state authority: {name}")

    return (len(mutating_reasons) == 0, mutating_reasons)


def run_read_only_preflights(
    effects: Mapping[str, Any],
    workflow: Workflow,
    selected: Path,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    preflights = effects.get("preflightParticipants", [])
    for preflight in preflights:
        name = preflight.get("name", "unnamed")
        executable = preflight.get("executable")
        if not executable:
            raise ConfigError(f"preflight participant {name} has no executable")
        args = [str(arg) for arg in preflight.get("arguments", [])]
        cmd = [str(executable), *args]
        label = f"module preflight {name}"
        timeout = preflight.get("timeout", 60)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise ConfigError(f"preflight participant {name} must declare a positive timeout")
        if preflight.get("protocol", "command") != "command" or preflight.get("kind", "command") != "command":
            raise ConfigError(f"preflight participant {name} requires an unsupported protocol; use advanced mode")
        completed = workflow.command(label, cmd, selected, timeout=timeout)
        if completed.returncode:
            raise ConfigError(f"module preflight {name} failed with status {completed.returncode}")
        results.append({
            "name": name,
            "owner": preflight.get("owner", ""),
            "status": "passed",
            "exit_code": completed.returncode,
        })
    return results
