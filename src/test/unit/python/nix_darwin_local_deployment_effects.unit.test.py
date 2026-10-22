# nix-darwin-test-source-target: libexec/nix_darwin_local/deployment_effects.py
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local.deployment_effects import (
    evaluate_deployment_effects,
    classify_deployment_effects,
    run_read_only_preflights,
)
from nix_darwin_local.paths import ConfigError


class MockWorkflow:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.calls = []
        self.command_kwargs = []

    def command(self, label, argv, cwd, **kwargs):
        self.calls.append((label, list(argv), cwd))
        self.command_kwargs.append(kwargs)
        return SimpleNamespace(returncode=self.returncode)

    def complete_stdout(self, label):
        return self.stdout


def test_evaluate_deployment_effects_absent(tmp_path: Path):
    workflow = MockWorkflow(stdout="null\n", returncode=0)
    result = evaluate_deployment_effects(workflow, tmp_path, "Host", [])
    assert result["status"] == "absent"
    assert result["preflightParticipants"] == []
    assert result["preSwitchTransactions"] == []


def test_evaluate_deployment_effects_declared(tmp_path: Path):
    effects = {
        "schema": "nix-darwin-deployment-effects-v1",
        "preflightParticipants": [{"name": "p1", "executable": "/bin/true"}],
        "preSwitchTransactions": [],
        "postSwitchReconciliation": [],
        "externalMutableStateProbes": [],
        "participantLocalRollback": [],
    }
    workflow = MockWorkflow(stdout=json.dumps(effects), returncode=0)
    result = evaluate_deployment_effects(workflow, tmp_path, "Host", ["--override-input", "a", "path:/b"])
    assert result["status"] == "declared"
    assert len(result["preflightParticipants"]) == 1
    assert result["preflightParticipants"][0]["name"] == "p1"
    assert "--override-input" in workflow.calls[0][1]


def test_evaluate_deployment_effects_eval_failure(tmp_path: Path):
    workflow = MockWorkflow(stdout="", returncode=1)
    with pytest.raises(ConfigError, match="evaluation failed for host"):
        evaluate_deployment_effects(workflow, tmp_path, "Host", [])


def test_evaluate_deployment_effects_invalid_json(tmp_path: Path):
    workflow = MockWorkflow(stdout="not json", returncode=0)
    with pytest.raises(ConfigError, match="emitted invalid JSON"):
        evaluate_deployment_effects(workflow, tmp_path, "Host", [])


def test_evaluate_deployment_effects_not_dict(tmp_path: Path):
    workflow = MockWorkflow(stdout="[1, 2, 3]", returncode=0)
    with pytest.raises(ConfigError, match="must evaluate to a dictionary"):
        evaluate_deployment_effects(workflow, tmp_path, "Host", [])


def test_classify_deployment_effects_safe():
    safe_effects = {
        "status": "declared",
        "preflightParticipants": [{"name": "chk", "readOnly": True, "classification": "preflight"}],
        "preSwitchTransactions": [],
        "postSwitchReconciliation": [],
        "externalMutableStateProbes": [],
        "participantLocalRollback": [],
    }
    is_safe, reasons = classify_deployment_effects(safe_effects)
    assert is_safe is True
    assert reasons == []


def test_classify_deployment_effects_mutating_reasons():
    mutating = {
        "status": "declared",
        "preflightParticipants": [{"name": "mut_chk", "readOnly": False}],
        "preSwitchTransactions": [{"name": "tx1"}],
        "postSwitchReconciliation": [{"name": "post1"}],
        "externalMutableStateProbes": [{"name": "probe1", "classification": "mutation"}],
        "participantLocalRollback": [{"name": "rb1"}],
    }
    is_safe, reasons = classify_deployment_effects(mutating)
    assert is_safe is False
    assert len(reasons) == 5
    assert any("preSwitchTransactions" in r for r in reasons)
    assert any("postSwitchReconciliation" in r for r in reasons)
    assert any("participantLocalRollback" in r for r in reasons)
    assert any("preflightParticipants" in r for r in reasons)
    assert any("externalMutableStateProbes" in r for r in reasons)


def test_classify_deployment_effects_routes_read_only_external_probe_to_advanced():
    is_safe, reasons = classify_deployment_effects(
        {"externalMutableStateProbes": [{"name": "read-only-probe", "readOnly": True}]}
    )
    assert is_safe is False
    assert reasons == [
        "externalMutableStateProbes requires advanced external-state authority: read-only-probe"
    ]


def test_run_read_only_preflights_success(tmp_path: Path):
    effects = {
        "preflightParticipants": [
            {"name": "p1", "owner": "test", "executable": "/bin/echo", "arguments": ["hello"]},
        ]
    }
    workflow = MockWorkflow(returncode=0)
    results = run_read_only_preflights(effects, workflow, tmp_path)
    assert len(results) == 1
    assert results[0]["name"] == "p1"
    assert results[0]["status"] == "passed"
    assert results[0]["exit_code"] == 0
    assert workflow.calls[0][1] == ["/bin/echo", "hello"]
    assert workflow.command_kwargs == [{"timeout": 60}]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("kind", "shell", "requires an unsupported protocol"),
        ("protocol", "json", "requires an unsupported protocol"),
        ("timeout", 0, "must declare a positive timeout"),
        ("timeout", -1, "must declare a positive timeout"),
        ("timeout", True, "must declare a positive timeout"),
    ],
)
def test_run_read_only_preflights_rejects_unsupported_contract(
    tmp_path: Path, field, value, message
):
    preflight = {"name": "invalid", "executable": "/bin/true", field: value}
    workflow = MockWorkflow()
    with pytest.raises(ConfigError, match=message):
        run_read_only_preflights({"preflightParticipants": [preflight]}, workflow, tmp_path)
    assert workflow.calls == []


def test_run_read_only_preflights_passes_declared_timeout(tmp_path: Path):
    workflow = MockWorkflow()
    results = run_read_only_preflights(
        {
            "preflightParticipants": [
                {
                    "name": "bounded",
                    "executable": "/bin/true",
                    "kind": "command",
                    "protocol": "command",
                    "timeout": 17,
                }
            ]
        },
        workflow,
        tmp_path,
    )
    assert results[0]["status"] == "passed"
    assert workflow.command_kwargs == [{"timeout": 17}]


def test_run_read_only_preflights_missing_executable(tmp_path: Path):
    effects = {
        "preflightParticipants": [{"name": "no_exe"}]
    }
    workflow = MockWorkflow()
    with pytest.raises(ConfigError, match="has no executable"):
        run_read_only_preflights(effects, workflow, tmp_path)


def test_run_read_only_preflights_failure(tmp_path: Path):
    effects = {
        "preflightParticipants": [{"name": "fail_chk", "executable": "/bin/false"}]
    }
    workflow = MockWorkflow(returncode=1)
    with pytest.raises(ConfigError, match="failed with status 1"):
        run_read_only_preflights(effects, workflow, tmp_path)
