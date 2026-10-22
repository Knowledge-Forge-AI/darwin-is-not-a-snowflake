# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_qualification.py
from __future__ import annotations

import importlib
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_failed_operation_has_bounded_redacted_cause_and_private_evidence(
    repo_root, tmp_path, monkeypatch, stream
):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    module = importlib.import_module("nix_darwin_local.promotion_qualification")
    deployment = importlib.import_module("nix_darwin_local.deployment_workflow")
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(deployment, "_private_state_root", lambda: state)
    diagnostic = "config/public.toml: unknown setting; token=secret-value\n" + "x" * 5000

    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 3, diagnostic if stream == "stdout" else "",
            diagnostic if stream == "stderr" else "",
        )

    workflow = deployment.Workflow(repo_root, runner, display_command_output=False)
    monkeypatch.setattr(module, "qualifier_identity", lambda *args: {"relative_path": "tools/qualify-promotion"})
    with pytest.raises(module.ConfigError) as failure:
        module.run_qualifier(workflow, tmp_path, "domain", "candidate", {}, None, label="candidate qualifier")
    message = str(failure.value)
    assert "config/public.toml: unknown setting" in message
    assert "tools/qualify-promotion (exit 3)" in message
    assert "secret-value" not in message
    assert "[REDACTED]" in message
    assert "private evidence:" in message
    assert len(message) < 2600
    evidence_path = Path(message.split("private evidence: ")[1].strip())
    assert evidence_path.name.endswith(f".{stream}")
    assert evidence_path.is_file()
    assert evidence_path.stat().st_size > 0
