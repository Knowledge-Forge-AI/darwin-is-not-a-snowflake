# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_campaign.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_campaign_state.py
"""Integration tests exercising production campaign confirmation via real PTYs.

Mutation boundary notice:
Real git mutations, network publication, lock installation, and host activation
are faked at the apply execution boundary (fake_apply simulates post-confirmation
publication writing a valid result.json), while testing the real campaign state
machine, PTY terminal confirmation reader, and coordinator lifecycle.
"""

from __future__ import annotations

import json
import os
import pty
import select
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local import promotion_campaign as campaign_mod
from nix_darwin_local import promotion_campaign_state as state_mod

REVISION = "0123456789abcdef0123456789abcdef01234567"


def _setup_campaign_fixture(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch=main"],
        cwd=repo,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    (repo / "flake.nix").write_text("{ outputs = { self }: { }; }", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=repo,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()

    state_root = tmp_path / "state_root"
    state_root.mkdir(mode=0o700)

    camp_dir = tmp_path / "camp"
    camp_dir.mkdir(mode=0o700)
    stage_dir = camp_dir / "stages" / "composition-nd" / "attempt-1"
    stage_dir.mkdir(parents=True, mode=0o700)

    tool_identity = {"digest": "a" * 64, "name": "tool", "version": "1"}
    plan_digest = "1" * 64
    plan_doc = {
        "status": "READY_FOR_REVIEW",
        "digest": plan_digest,
        "tool": tool_identity,
        "repository": {"commit": commit},
        "input": "inp",
        "revision": REVISION,
        "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"},
    }
    (stage_dir / "plan.json").write_text(json.dumps(plan_doc), encoding="utf-8")
    (stage_dir / "plan.md").write_text(f"Plan {plan_digest}", encoding="utf-8")

    stages = [
        {
            "name": "composition-nd",
            "kind": "composition",
            "repository": os.fspath(repo),
            "input": "inp",
            "revision": REVISION,
            "status": "READY_FOR_REVIEW",
            "current_plan": os.fspath(stage_dir),
            "published_commit": None,
            "impact": None,
            "failure": None,
            "attempts": [
                {
                    "number": 1,
                    "plan_path": os.fspath(stage_dir),
                    "plan_digest": plan_digest,
                    "plan_status": "READY_FOR_REVIEW",
                    "result_path": None,
                    "result_digest": None,
                    "terminal_state": None,
                    "published_commit": None,
                    "failure": None,
                }
            ],
        }
    ]

    doc = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc["stages"] = stages
    doc = state_mod.transition(doc, "AWAITING_APPLY", "stage prepared")
    state_mod.persist_campaign(camp_dir, doc)
    campaign_mod._index(doc, camp_dir, state_root / "promotion-campaigns")

    return repo, commit, state_root, camp_dir, stage_dir, plan_digest, plan_doc, tool_identity


def _run_campaign_subprocess(
    *,
    camp_dir: Path,
    repo: Path,
    state_root: Path,
    plan_digest: str,
    commit: str,
    tool: dict[str, Any],
    use_pty: bool = True,
    redirect_stdin_to_null: bool = False,
    response_bytes: bytes | None = None,
) -> tuple[int, str]:
    child_script = f"""
import os, sys
sys.path.insert(0, {str(CORE_ROOT / "libexec")!r})
from pathlib import Path
from nix_darwin_local import promotion_campaign as campaign_mod
from nix_darwin_local import promotion_workflow
from nix_darwin_local import deployment_workflow
from nix_darwin_local.promotion_plan import canonical_bytes
from nix_darwin_local import config as config_mod

if {redirect_stdin_to_null}:
    devnull = os.open("/dev/null", os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)

state_root = Path({str(state_root)!r})
camp_dir = Path({str(camp_dir)!r})
repo = Path({str(repo)!r})
plan_digest = {plan_digest!r}
published_commit = {commit!r}
tool = {tool!r}

campaign_mod._private_state_root = lambda: state_root
deployment_workflow._private_state_root = lambda: state_root
promotion_workflow._private_state_root = lambda: state_root
campaign_mod._promotion_tool_identity = lambda core_dir: tool

class MockArtifact:
    def __init__(self, d):
        self.directory = d
        self.digest = plan_digest
        self.document = {{
            "status": "READY_FOR_REVIEW",
            "digest": plan_digest,
            "tool": tool,
            "repository": {{"commit": published_commit}},
            "input": "inp",
            "revision": {REVISION!r},
            "impact": {{"classification": "HOST_DERIVATIONS_IDENTICAL"}},
        }}

campaign_mod.load_plan = lambda p: MockArtifact(p)

def fake_apply(workflow, plan_path, *, confirm=None):
    expected = f"APPLY NIX-UPDATE PROMOTION {{plan_digest}}"
    prompt = f"Type '{{expected}}' to continue: "
    try:
        response = (confirm or input)(prompt)
    except EOFError as error:
        raise deployment_workflow.ConfigError("promotion confirmation was not supplied") from error
    if response != expected:
        raise deployment_workflow.ConfigError("promotion confirmation text did not match the reviewed plan digest")
    result_doc = {{
        "schema": "nix-update-promotion-result-v1",
        "plan_digest": plan_digest,
        "no_activation": True,
        "terminal_state": "PROMOTION_PUBLISHED_AWAITING_ACTIVATION",
        "published_commit": published_commit,
        "failure": None,
        "recovery": None,
        "impact": {{"classification": "HOST_DERIVATIONS_IDENTICAL"}},
    }}
    raw = canonical_bytes(result_doc)
    result_path = plan_path / "result.json"
    result_path.write_bytes(raw)
    result_path.chmod(0o600)
    return 0

campaign_mod.native_apply_promotion = fake_apply

cfg = config_mod.ControlConfig()
workflow = deployment_workflow.Workflow(repo)
code = campaign_mod.resume_campaign(workflow, repo, cfg, campaign=camp_dir)
os._exit(code)
"""

    child_env = {**os.environ, "HOME": str(camp_dir.parent)}

    if use_pty:
        master, slave = pty.openpty()
        proc = subprocess.Popen(
            [sys.executable, "-c", child_script],
            preexec_fn=lambda: os.login_tty(slave),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            env=child_env,
        )
        os.close(slave)

        captured = ""
        prompt_expected = f"Type 'APPLY NIX-UPDATE PROMOTION {plan_digest}' to continue: "
        deadline = time.monotonic() + 5.0
        try:
            while time.monotonic() < deadline:
                r, _, _ = select.select([master], [], [], 0.2)
                if r:
                    try:
                        chunk = os.read(master, 1024)
                    except OSError:
                        break
                    if not chunk:
                        break
                    captured += chunk.decode("utf-8", errors="replace")
                    if prompt_expected in captured:
                        break

            if response_bytes is not None:
                os.write(master, response_bytes)
            while proc.poll() is None and time.monotonic() < deadline + 3.0:
                r, _, _ = select.select([master], [], [], 0.2)
                if r:
                    try:
                        chunk = os.read(master, 1024)
                    except OSError:
                        break
                    if not chunk:
                        break
                    captured += chunk.decode("utf-8", errors="replace")
        finally:
            try:
                os.close(master)
            except OSError:
                pass
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=5.0)
            except Exception:
                pass

        return proc.returncode, captured
    else:
        proc = subprocess.Popen(
            [sys.executable, "-c", child_script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=child_env,
        )
        try:
            stdout, stderr = proc.communicate(timeout=5.0)
            return proc.returncode, stdout + stderr
        finally:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=5.0)
            except Exception:
                pass


def test_pty_exact_phrase_acceptance(tmp_path):
    repo, commit, state_root, camp_dir, stage_dir, plan_digest, plan_doc, tool = _setup_campaign_fixture(tmp_path)
    expected = f"APPLY NIX-UPDATE PROMOTION {plan_digest}"
    returncode, output = _run_campaign_subprocess(
        camp_dir=camp_dir,
        repo=repo,
        state_root=state_root,
        plan_digest=plan_digest,
        commit=commit,
        tool=tool,
        use_pty=True,
        response_bytes=f"{expected}\n".encode("utf-8"),
    )
    assert returncode == 0
    assert expected in output

    doc = state_mod.load_campaign(camp_dir)
    assert doc["stages"][0]["status"] == "PUBLISHED"
    assert doc["stages"][0]["published_commit"] == commit


def test_pty_wrong_phrase_refusal(tmp_path):
    repo, commit, state_root, camp_dir, stage_dir, plan_digest, plan_doc, tool = _setup_campaign_fixture(tmp_path)
    returncode, output = _run_campaign_subprocess(
        camp_dir=camp_dir,
        repo=repo,
        state_root=state_root,
        plan_digest=plan_digest,
        commit=commit,
        tool=tool,
        use_pty=True,
        response_bytes=b"WRONG CONFIRMATION PHRASE\n",
    )
    assert returncode == 1

    doc = state_mod.load_campaign(camp_dir)
    assert doc["state"] == "BLOCKED"
    assert doc["stages"][0]["status"] == "BLOCKED"
    assert "promotion confirmation text did not match the reviewed plan digest" in doc["blocker"]["failure"]["message"]


def test_pty_eof_interruption(tmp_path):
    repo, commit, state_root, camp_dir, stage_dir, plan_digest, plan_doc, tool = _setup_campaign_fixture(tmp_path)
    returncode, output = _run_campaign_subprocess(
        camp_dir=camp_dir,
        repo=repo,
        state_root=state_root,
        plan_digest=plan_digest,
        commit=commit,
        tool=tool,
        use_pty=True,
        response_bytes=b"\x04",
    )
    assert returncode == 1

    doc = state_mod.load_campaign(camp_dir)
    assert doc["state"] == "BLOCKED"
    assert doc["stages"][0]["status"] == "BLOCKED"
    assert "promotion confirmation was not supplied" in doc["blocker"]["failure"]["message"]


def test_pty_stdin_redirection_reads_from_controlling_terminal(tmp_path):
    repo, commit, state_root, camp_dir, stage_dir, plan_digest, plan_doc, tool = _setup_campaign_fixture(tmp_path)
    expected = f"APPLY NIX-UPDATE PROMOTION {plan_digest}"
    returncode, output = _run_campaign_subprocess(
        camp_dir=camp_dir,
        repo=repo,
        state_root=state_root,
        plan_digest=plan_digest,
        commit=commit,
        tool=tool,
        use_pty=True,
        redirect_stdin_to_null=True,
        response_bytes=f"{expected}\n".encode("utf-8"),
    )
    assert returncode == 0

    doc = state_mod.load_campaign(camp_dir)
    assert doc["stages"][0]["status"] == "PUBLISHED"


def test_no_controlling_terminal_pauses_at_awaiting_apply(tmp_path):
    repo, commit, state_root, camp_dir, stage_dir, plan_digest, plan_doc, tool = _setup_campaign_fixture(tmp_path)
    returncode, output = _run_campaign_subprocess(
        camp_dir=camp_dir,
        repo=repo,
        state_root=state_root,
        plan_digest=plan_digest,
        commit=commit,
        tool=tool,
        use_pty=False,
    )
    assert returncode == 0

    doc = state_mod.load_campaign(camp_dir)
    assert doc["state"] == "AWAITING_APPLY"
    assert doc["stages"][0]["status"] == "READY_FOR_REVIEW"
    assert doc["blocker"] is None
    assert "awaiting native confirmation" in output
    assert f"APPLY NIX-UPDATE PROMOTION {plan_digest}" in output
