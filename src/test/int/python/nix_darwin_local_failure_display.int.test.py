# nix-darwin-test-source-target: libexec/nix_darwin_local/local_deployment.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/deployment_workflow.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/launcher.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/command_diagnostics.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/report_diagnostics.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_qualification.py
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

CORE = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE / "libexec"))
from nix_darwin_local import deployment_workflow, launcher, local_deployment
from nix_darwin_local import promotion_qualification


@pytest.fixture
def isolated_deployment(tmp_path, monkeypatch):
    """Real launcher, projection lifetime and subprocess capture; fake host observations."""
    selected = tmp_path / "composition"
    selected.mkdir()
    (selected / "flake.nix").write_text("{}")
    (selected / "flake.lock").write_text("unchanged lock")
    config = tmp_path / ".local/nix-darwin/config"
    config.mkdir(parents=True)
    (config / "nix-darwin.toml").write_text(f'[flake]\nactive = "{selected}"\n')
    for module in (launcher, local_deployment):
        monkeypatch.setattr(module, "operator_home", lambda: tmp_path)
    monkeypatch.setattr("nix_darwin_local.config.operator_home", lambda: tmp_path)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: tmp_path / ".local/state/nix-update")
    for key in ("NIX_DARWIN_LOCAL", "NIX_DARWIN_ACTIVE_FLAKE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(launcher, "_darwin_config", lambda env: "fixture")
    monkeypatch.setattr(local_deployment, "_guard_processes", lambda *a, **kw: None)
    monkeypatch.setattr(local_deployment, "local_git_identity", lambda *a: {"clean": True})
    monkeypatch.setattr(local_deployment, "inspect_profile", lambda *a: {"relationship": "aligned"})
    monkeypatch.setattr(local_deployment, "derive_predecessor_generation", lambda *a: 1)
    monkeypatch.setattr(local_deployment, "evaluate_allowlist", lambda *a: [])
    monkeypatch.setattr(local_deployment, "render_scratch", lambda control, path, allow: SimpleNamespace(root=path))
    monkeypatch.setattr(local_deployment, "evaluate_deployment_effects", lambda *a: {"status": "absent"})
    monkeypatch.setattr(local_deployment, "derive_source_identities", lambda *a, **kw: {})

    def forbidden(*a, **kw):
        pytest.fail("failure reporting reached confirmation, activation, or later evaluation")

    for name in ("require_terminal_confirmation", "ensure_sudo_credentials", "closure_inventory"):
        monkeypatch.setattr(local_deployment, name, forbidden)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])
    return selected, fake_bin


@pytest.mark.parametrize("route", [[], ["deploy"]])
@pytest.mark.parametrize("operation,stage,exit_code", [
    ("selected-flake report qualification", 0, 7),
    ("exact projected flake check", 1, 8),
    ("exact Darwin target build", 2, 9),
])
def test_default_failure_display_uses_real_capture_and_survives_cleanup(
    isolated_deployment, tmp_path, route, operation, stage, exit_code, capsys,
):
    selected, fake_bin = isolated_deployment
    # A report hook retains the separate exact projected check; the no-hook
    # identical fallback is covered by the local qualification call-count test.
    (selected / "bin").mkdir()
    report_hook = selected / "bin/nix-test"
    report_hook.write_text(
        "#!/bin/sh\nexec nix flake check path:. --no-update-lock-file --no-write-lock-file\n"
    )
    report_hook.chmod(0o700)
    calls = tmp_path / "calls.jsonl"
    fake = fake_bin / "nix"
    fake.write_text(
        f"#!{sys.executable}\nimport json,sys,pathlib\n"
        f"p=pathlib.Path({str(calls)!r})\n"
        "n=len(p.read_text().splitlines()) if p.exists() else 0\n"
        "with p.open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        f"if n == {stage}:\n"
        " sys.stderr.write('warning: harmless\\n'*1500+'error: fixture failed near end; token=secret-value\\n')\n"
        f" sys.exit({exit_code})\n"
    )
    fake.chmod(0o700)
    rc = launcher.main(CORE, route)
    output = capsys.readouterr()
    assert rc == launcher.EX_CONFIG
    assert operation in output.err and f"exit {exit_code}" in output.err
    assert "fixture failed near end" in output.err
    assert "secret-value" not in output.err
    assert len(output.err) < 3000
    pointer = Path(output.err.split("private evidence: ")[-1].strip())
    assert pointer.is_file() and "fixture failed near end" in pointer.read_text()
    state = tmp_path / ".local/state/nix-update"
    assert not list(state.glob("nix-update-local-*"))
    commands = [json.loads(line) for line in calls.read_text().splitlines()]
    assert len(commands) == stage + 1
    assert all("--no-update-lock-file" in cmd and "--no-write-lock-file" in cmd for cmd in commands)
    assert (selected / "flake.lock").read_text() == "unchanged lock"


def test_report_only_child_error_is_retained_before_cleanup(isolated_deployment, tmp_path, capsys):
    selected, _ = isolated_deployment
    (selected / "bin").mkdir()
    fake = selected / "bin/nix-test"
    fake.write_text(
        f"#!{sys.executable}\nimport pathlib,sys\n"
        "root=pathlib.Path(sys.argv[sys.argv.index('--report-root')+1])/'all/latest'\n"
        "runner=root/'runner'; (runner/'002-flake-check').mkdir(parents=True)\n"
        "(runner/'steps.tsv').write_text('ordinal\\tlabel\\tstatus\\texit_code\\toutput_log\\n1\\tpatch hygiene\\tpass\\t0\\trunner/001-patch/output.txt\\n2\\tflake check\\tfail\\t4\\trunner/002-flake-check/output.txt\\n')\n"
        "(runner/'failures.tsv').write_text('sequence\\tlabel\\texit_code\\n1\\tflake check\\t4\\n')\n"
        "(runner/'execution.tsv').write_text('status\\texit_code\\nfail\\t4\\n')\n"
        "(runner/'002-flake-check/output.txt').write_text('warning: preliminary\\n'*2000+'error: report-specific assertion failed\\n')\n"
        "print('report qualification failed; report retained in temporary report root')\n"
        "sys.exit(4)\n"
    )
    fake.chmod(0o700)
    assert launcher.main(CORE, []) == launcher.EX_CONFIG
    output = capsys.readouterr()
    assert "selected-flake report qualification" in output.err
    assert "flake check" in output.err and "exit 4" in output.err
    assert "report-specific assertion failed" in output.err
    pointer = Path(output.err.split("private evidence: ")[-1].strip())
    assert pointer.is_file() and "report-specific assertion failed" in pointer.read_text()
    assert not list((tmp_path / ".local/state/nix-update").glob("nix-update-local-*"))


@pytest.mark.parametrize("stream", ["stderr", "stdout"])
def test_native_qualifier_process_displays_recorded_child_failure(
    isolated_deployment, tmp_path, stream,
):
    selected, _ = isolated_deployment
    (selected / "tools").mkdir()
    qualifier = selected / "tools/qualify-promotion"
    assertion = 'error: string \'"ac6ec7467f069f24a5bee76839670c2ba31a139b"\' is not equal to string \'"unavailable"\''

    def command(argv, exit_code=0, detail="metadata success"):
        def preview(text):
            return {"bytes": len(text.encode()), "sha256": "0" * 64, "truncated": False, "preview": text}
        return {"argv": argv, "exitCode": exit_code, "status": "fail" if exit_code else "pass",
                "timedOut": False, "timeoutSeconds": 300, "cleanupDisposition": "complete",
                "stdout": preview(detail if stream == "stdout" or not exit_code else ""),
                "stderr": preview(detail if stream == "stderr" and exit_code else "")}

    document = {
        "schema": "composition-nd-promotion-result-v1", "input": "developer-ux",
        "candidate": {}, "registry": None,
        "qualification": {"entryPoint": "tools/qualify-promotion", "mode": "exact-candidate", "timeoutSeconds": 300},
        "commands": [command(["nix", "flake", "metadata"], detail="metadata success " * 1000) for _ in range(5)] + [
            command(["nix", "build", "--no-link", "--override-input", "developer-ux",
                     "git+https://example.invalid/ux?rev=" + "a" * 40,
                     "path:.#checks.aarch64-darwin.registered-input-checks"], 1, assertion),
        ],
        "checks": {"application-claims": "pass", "contracts": "pass", "lock-provenance": "pass",
                   "registered-input-checks": "fail", "tests": "not-run"},
        "hosts": {}, "lock": {"byteIdentical": True}, "tree": {"byteIdentical": True},
        "outcome": "fail", "failure": {"category": "command-failure", "message": "public check failed",
                                          "details": {"check": "registered-input-checks", "exitCode": 1}},
    }
    qualifier.write_text(f"#!{sys.executable}\nimport sys\nprint({json.dumps(document)!r})\nsys.exit(5)\n")
    qualifier.chmod(0o700)
    workflow = deployment_workflow.Workflow(CORE, display_command_output=False)
    with pytest.raises(promotion_qualification.ConfigError) as failure:
        promotion_qualification.run_qualifier(
            workflow, selected, "developer-ux", "fixture", {"qualification": {"entryPoint": "tools/qualify-promotion"}},
            None, label="candidate qualifier",
        )
    message = str(failure.value)
    assert "exit 5" in message and "exit 1" in message
    assert "first recorded" in message.lower() and "registered-input-checks (fail)" in message
    assert "ac6ec7467f069f24a5bee76839670c2ba31a139b" in message and "unavailable" in message
    assert "metadata success" not in message and "tests: not-run" not in message
    assert len(message) < 2600 and len(workflow.records) == 1
    pointer = Path(message.split("private evidence: ")[-1].strip())
    assert pointer.name.endswith(".stdout") and json.loads(pointer.read_text())["outcome"] == "fail"


@pytest.mark.parametrize("status", [0, 6])
def test_native_policy_result_without_commands_is_explained(isolated_deployment, tmp_path, status):
    selected, _ = isolated_deployment
    (selected / "tools").mkdir()
    qualifier = selected / "tools/qualify-promotion"
    document = {"schema": "composition-nd-promotion-result-v1", "outcome": "fail",
                "failure": {"message": "contract policy rejected"}, "checks": {"contracts": "fail"}}
    qualifier.write_text(f"#!{sys.executable}\nimport sys\nprint({json.dumps(document)!r})\nsys.exit({status})\n")
    qualifier.chmod(0o700)
    workflow = deployment_workflow.Workflow(CORE, display_command_output=False)
    with pytest.raises(promotion_qualification.ConfigError) as failure:
        promotion_qualification.run_qualifier(workflow, selected, "fixture", "fixture",
            {"qualification": {"entryPoint": "tools/qualify-promotion"}}, None, label="candidate qualifier")
    message = str(failure.value)
    assert f"exit {status}" in message and "contracts (fail)" in message
    assert "contract policy rejected" in message and len(workflow.records) == 1
    if status == 0:
        assert "candidate qualifier result rejected" in message
    pointer = Path(message.split("private evidence: ")[-1].strip())
    assert pointer.is_file() and json.loads(pointer.read_text()) == document
