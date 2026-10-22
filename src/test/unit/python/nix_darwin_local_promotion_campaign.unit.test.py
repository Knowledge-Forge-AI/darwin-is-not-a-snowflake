# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_campaign.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_campaign_state.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_campaign_result.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_topology.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_core.py
from __future__ import annotations

import copy
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REVISION = "a" * 40
OLD_REVISION = "b" * 40
THIRD_REVISION = "c" * 40


@pytest.fixture
def campaign_modules(repo_root: Path, monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    campaign = importlib.import_module("nix_darwin_local.promotion_campaign")
    state = importlib.import_module("nix_darwin_local.promotion_campaign_state")
    result = importlib.import_module("nix_darwin_local.promotion_campaign_result")
    topology = importlib.import_module("nix_darwin_local.promotion_topology")
    core = importlib.import_module("nix_darwin_local.promotion_core")
    config = importlib.import_module("nix_darwin_local.config")
    deployment = importlib.import_module("nix_darwin_local.deployment_workflow")
    workflow = importlib.import_module("nix_darwin_local.promotion_workflow")
    state_root = tmp_path / "private-state"
    state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    monkeypatch.setattr(workflow, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(deployment, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(campaign, "_private_state_root", lambda: state_root)
    return campaign, state, result, topology, core, config, deployment, state_root


def _contract(remote: Path, name: str = "stage-a-composition", input_name: str = "domain-adapter") -> dict:
    return {
        "schema": "fixture-base-promotion-v1",
        "version": 1,
        "composition": {
            "name": name,
            "repositoryUrl": remote.as_uri(),
        },
        "system": "aarch64-darwin",
        "modules": [
            {
                "name": input_name,
                "input": input_name,
                "url": f"https://example.invalid/{input_name}",
                "checkout": input_name,
                "bootstrapStrategy": "none",
                "ownsMachineLocalConfiguration": False,
            }
        ],
        "promotableInputs": {
            "rule": "registered-direct-inputs-only",
            "names": [input_name],
        },
        "qualification": {
            "entryPoint": "bin/qualify-promotion",
            "mode": "exact-candidate",
        },
        "checks": ["composition"],
        "hosts": ["StageAHost"],
        "packageSetIdentity": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-nixpkgs",
    }


def _layered_contract(
    remote: Path,
    lower: dict,
    name: str = "stage-private-composition",
    input_name: str = "private-adapter",
) -> dict:
    return {
        "schema": f"{name}-v1",
        "version": 1,
        "composition": {
            "name": name,
            "repositoryUrl": remote.as_uri(),
        },
        "system": lower["system"],
        "modules": [
            {
                "name": input_name,
                "input": input_name,
                "url": f"https://example.invalid/{input_name}",
                "checkout": input_name,
                "bootstrapStrategy": "none",
                "ownsMachineLocalConfiguration": False,
            }
        ],
        "promotableInputs": {
            "rule": "registered-direct-lower-composition-only",
            "names": [input_name],
        },
        "qualification": {
            "entryPoint": "bin/qualify-promotion",
            "mode": "exact-candidate",
        },
        "checks": ["private-checks"],
        "hosts": lower["hosts"],
        "packageSetIdentity": lower["packageSetIdentity"],
        "requiredLowerPromotion": {
            "schema": lower["schema"],
            "version": lower["version"],
            "candidateInput": input_name,
        },
        "lowerPromotion": lower,
        "combinedModules": lower["modules"] + [
            {
                "name": input_name,
                "input": input_name,
                "url": f"https://example.invalid/{input_name}",
                "checkout": input_name,
                "bootstrapStrategy": "none",
                "ownsMachineLocalConfiguration": False,
            }
        ],
        "semantics": {"nonActivation": True, "requiresExplicitLockSelection": True},
    }


def _make_git_repo(path: Path) -> tuple[Path, str]:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Fixture User"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "user@example.invalid"], cwd=path, check=True)
    (path / "flake.nix").write_text("{ description = \"test\"; }\n", encoding="utf-8")
    (path / "flake.lock").write_text("{\"version\": 7, \"nodes\": {}}\n", encoding="utf-8")
    subprocess.run(["git", "add", "flake.nix", "flake.lock"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()
    return path, commit


def _make_stage_plan_dir(camp_dir: Path, stage_name: str, attempt: int = 1) -> Path:
    stages = camp_dir / "stages"
    stages.mkdir(mode=0o700, exist_ok=True)
    os.chmod(stages, 0o700)
    stage = stages / stage_name
    stage.mkdir(mode=0o700, exist_ok=True)
    os.chmod(stage, 0o700)
    attempt_dir = stage / f"attempt-{attempt}"
    attempt_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(attempt_dir, 0o700)
    return attempt_dir


def test_campaign_state_creation_sealing_and_validation(campaign_modules, tmp_path):
    _, state_mod, _, _, _, _, _, _ = campaign_modules
    selected = tmp_path / "selected"
    selected.mkdir()
    stages = [
        {"name": "stage-1", "kind": "composition", "repository": selected, "input": "adapter", "revision": REVISION}
    ]
    doc = state_mod.new_campaign(selected, "adapter", REVISION, stages)
    assert doc["state"] == "NEW"
    assert doc["input"] == "adapter"
    assert doc["revision"] == REVISION
    assert doc["campaign_digest"] == state_mod.campaign_digest(doc)

    # Duplicate stage names rejected
    dup_stages = [
        {"name": "stage-1", "kind": "composition", "repository": selected, "input": "adapter", "revision": REVISION},
        {"name": "stage-1", "kind": "composition", "repository": selected, "input": "adapter", "revision": REVISION},
    ]
    with pytest.raises(state_mod.ConfigError, match="duplicate"):
        state_mod.new_campaign(selected, "adapter", REVISION, dup_stages)

    # Tampered digest rejected
    tampered = copy.deepcopy(doc)
    tampered["state"] = "BLOCKED"
    with pytest.raises(state_mod.ConfigError, match="digest"):
        state_mod.validate_campaign(tampered, raw=None, campaign=None)


def test_campaign_state_append_attempt_enforces_limits_and_published_commit(campaign_modules, tmp_path):
    _, state_mod, _, _, _, _, _, _ = campaign_modules
    stage = {
        "name": "stage-1",
        "kind": "composition",
        "repository": os.fspath(tmp_path),
        "input": "inp",
        "revision": REVISION,
        "status": "NEW",
        "attempts": [],
        "current_plan": None,
        "published_commit": None,
        "impact": None,
        "failure": None,
    }
    plan = {
        "path": tmp_path / "plan",
        "digest": "0" * 64,
        "status": "READY_FOR_REVIEW",
        "published_commit": REVISION,
    }
    updated = state_mod.append_attempt(stage, plan)
    assert len(updated["attempts"]) == 1
    assert updated["attempts"][0]["number"] == 1
    assert updated["attempts"][0]["published_commit"] == REVISION
    assert updated["published_commit"] == REVISION

    # Fill attempts up to MAX_ATTEMPTS and verify it raises on overflow
    cur = stage
    for i in range(state_mod.MAX_ATTEMPTS):
        p = dict(plan)
        p["path"] = tmp_path / f"plan-{i}"
        cur = state_mod.append_attempt(cur, p)
    assert len(cur["attempts"]) == state_mod.MAX_ATTEMPTS
    with pytest.raises(state_mod.ConfigError, match="exceeded maximum"):
        state_mod.append_attempt(cur, plan)


def test_campaign_indexing_and_matching_with_spaces_and_ambiguity(campaign_modules, tmp_path):
    _, state_mod, _, _, _, _, _, _ = campaign_modules
    state_root = tmp_path / "state root with spaces"
    state_root.mkdir(mode=0o700, parents=True)
    selected = tmp_path / "flake with spaces"
    selected.mkdir()
    campaign1 = tmp_path / "campaign 1"
    campaign1.mkdir(mode=0o700)
    campaign2 = tmp_path / "campaign 2"
    campaign2.mkdir(mode=0o700)

    # Initial empty matches
    matches = state_mod.matching_campaigns(state_root, selected=selected, input_name="inp", revision=REVISION)
    assert matches == []

    # Update index for campaign 1
    state_mod.update_index(
        state_root,
        campaign=campaign1,
        selected=selected,
        input_name="inp",
        revision=REVISION,
        state="NEW",
    )
    matches = state_mod.matching_campaigns(state_root, selected=selected, input_name="inp", revision=REVISION)
    assert matches == [campaign1]

    # Add ambiguous campaign 2
    state_mod.update_index(
        state_root,
        campaign=campaign2,
        selected=selected,
        input_name="inp",
        revision=REVISION,
        state="BLOCKED",
    )
    matches = state_mod.matching_campaigns(state_root, selected=selected, input_name="inp", revision=REVISION)
    assert set(matches) == {campaign1, campaign2}


def test_status_campaign_renders_state_and_handles_historical(campaign_modules, tmp_path, capsys):
    campaign_mod, state_mod, _, _, _, _, deployment, state_root = campaign_modules
    campaign_dir = tmp_path / "campaign-dir"
    campaign_dir.mkdir(mode=0o700)
    selected = tmp_path / "selected"
    selected.mkdir()

    stages = [
        {"name": "stage-1", "kind": "composition", "repository": selected, "input": "inp", "revision": REVISION}
    ]
    doc = state_mod.new_campaign(selected, "inp", REVISION, stages)
    state_mod.persist_campaign(campaign_dir, doc)
    state_mod.update_index(
        state_root,
        campaign=campaign_dir,
        selected=selected,
        input_name="inp",
        revision=REVISION,
        state="NEW",
    )

    # Status via explicit campaign
    code = campaign_mod.status_campaign(None, campaign=campaign_dir)
    assert code == 0
    out = capsys.readouterr().out
    assert "nix-update: promotion campaign" in out
    assert "state: NEW" in out

    # Status via historical journal format
    historical_dir = tmp_path / "hist-dir"
    historical_dir.mkdir(mode=0o700)
    hist_json = {
        "schema": "old-campaign-schema",
        "version": 1,
        "state": "COMPLETE",
        "input": "inp",
        "revision": REVISION,
        "updated_at": "2026-09-01T00:00:00Z",
    }
    (historical_dir / "campaign.json").write_text(json.dumps(hist_json), encoding="utf-8")
    (historical_dir / "campaign.json").chmod(0o600)
    code = campaign_mod.status_campaign(None, campaign=historical_dir)
    assert code == 0
    out = capsys.readouterr().out
    assert "historical campaign journal" in out
    assert "old-campaign-schema" in out


def test_campaign_dry_run_non_mutation_and_pending_downstream(campaign_modules, tmp_path, monkeypatch, capsys):
    campaign_mod, state_mod, _, topology_mod, _, config_mod, deployment, state_root = campaign_modules

    repo, commit0 = _make_git_repo(tmp_path / "repo1")
    repo2, commit2 = _make_git_repo(tmp_path / "repo2")

    cfg = config_mod.ControlConfig(workspace_root=tmp_path)
    workflow = deployment.Workflow(repo)

    stages = [
        {"name": "stage-1", "kind": "composition", "repository": repo, "input": "inp", "revision": REVISION},
        {"name": "stage-2", "kind": "composition", "repository": repo2, "input": "stage-1", "revision": None},
    ]
    monkeypatch.setattr(campaign_mod, "resolve_campaign_stages", lambda *args, **kwargs: stages)

    class MockArtifact:
        def __init__(self, d):
            self.directory = d
            self.digest = "1" * 64
            self.document = {"status": "READY_FOR_REVIEW"}

    monkeypatch.setattr(campaign_mod, "_prepare_native", lambda wf, r, inp, rev, out, cfg, stg: MockArtifact(out))

    code = campaign_mod.run_campaign(workflow, repo, cfg, "inp", REVISION, dry_run=True)
    assert code == 0
    out = capsys.readouterr().out
    assert "prepared (dry-run)" in out

    indexed = state_mod.matching_campaigns(state_root, selected=repo, input_name="inp", revision=REVISION)
    assert len(indexed) == 1
    doc = state_mod.load_campaign(indexed[0])
    assert doc["state"] == "DRY_RUN"
    assert doc["stages"][0]["status"] == "DRY_RUN"
    # Stage 2 stayed NEW and revision remains None (pending) without inventing commit IDs!
    assert doc["stages"][1]["status"] == "NEW"
    assert doc["stages"][1]["revision"] is None

    # Resume on dry run says evidence only
    code = campaign_mod.resume_campaign(workflow, repo, cfg, campaign=indexed[0])
    assert code == 0
    assert "dry-run campaign contains evidence only" in capsys.readouterr().out


def test_campaign_no_controlling_terminal_awaits_confirmation(campaign_modules, tmp_path, monkeypatch, capsys):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules

    repo, commit0 = _make_git_repo(tmp_path / "repo")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo)

    class MockArtifact:
        def __init__(self, d):
            self.directory = d
            self.digest = "2" * 64
            self.document = {"status": "READY_FOR_REVIEW"}

    monkeypatch.setattr(campaign_mod, "_prepare_native", lambda wf, r, inp, rev, out, cfg, stg: MockArtifact(out))
    monkeypatch.setattr(campaign_mod, "_has_controlling_terminal", lambda: False)

    code = campaign_mod.run_campaign(workflow, repo, cfg, "inp", REVISION)
    assert code == 0
    out = capsys.readouterr().out
    assert "awaiting native confirmation" in out

    indexed = state_mod.matching_campaigns(state_root, selected=repo, input_name="inp", revision=REVISION)
    doc = state_mod.load_campaign(indexed[0])
    assert doc["state"] == "AWAITING_APPLY"
    assert doc["stages"][0]["status"] == "READY_FOR_REVIEW"


def test_campaign_controlling_terminal_unavailable_during_apply_pauses_at_awaiting_apply(campaign_modules, tmp_path, monkeypatch, capsys):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules

    repo, commit0 = _make_git_repo(tmp_path / "repo")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo)

    class MockArtifact:
        def __init__(self, d):
            self.directory = d
            self.digest = "2" * 64
            self.document = {"status": "READY_FOR_REVIEW"}

    monkeypatch.setattr(campaign_mod, "_prepare_native", lambda wf, r, inp, rev, out, cfg, stg: MockArtifact(out))
    monkeypatch.setattr(campaign_mod, "load_plan", lambda p: MockArtifact(p))
    monkeypatch.setattr(campaign_mod, "_has_controlling_terminal", lambda: True)

    def fail_apply_no_tty(wf, plan, confirm=None):
        raise deployment.ConfigError("promotion confirmation requires a controlling terminal")

    monkeypatch.setattr(campaign_mod, "native_apply_promotion", fail_apply_no_tty)

    code = campaign_mod.run_campaign(workflow, repo, cfg, "inp", REVISION)
    assert code == 0
    out = capsys.readouterr().out
    assert "awaiting native confirmation" in out
    assert "to resume, run 'nix-update promote campaign resume" in out

    indexed = state_mod.matching_campaigns(state_root, selected=repo, input_name="inp", revision=REVISION)
    doc = state_mod.load_campaign(indexed[0])
    assert doc["state"] == "AWAITING_APPLY"
    assert doc["stages"][0]["status"] == "READY_FOR_REVIEW"
    assert doc["stages"][0]["failure"] is None
    assert doc["blocker"] is None
    reasons = [h["reason"] for h in doc["history"]]
    assert "awaiting native apply confirmation" in reasons
    assert "terminal unavailable for confirmation; returned to prepared boundary" in reasons
    assert "native apply confirmation accepted" not in reasons


def test_campaign_confirmation_refusal_blocks_and_resumes(campaign_modules, tmp_path, monkeypatch, capsys):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules

    repo, commit0 = _make_git_repo(tmp_path / "repo")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo)

    class MockArtifact:
        def __init__(self, d):
            self.directory = d
            self.digest = "3" * 64
            self.document = {"status": "READY_FOR_REVIEW"}

    monkeypatch.setattr(campaign_mod, "_prepare_native", lambda wf, r, inp, rev, out, cfg, stg: MockArtifact(out))
    monkeypatch.setattr(campaign_mod, "load_plan", lambda p: MockArtifact(p))
    monkeypatch.setattr(campaign_mod, "_has_controlling_terminal", lambda: True)

    # First attempt: confirmation refused
    def fail_apply(wf, plan, confirm=None):
        raise deployment.ConfigError("confirmation phrase mismatch")

    monkeypatch.setattr(campaign_mod, "native_apply_promotion", fail_apply)

    code = campaign_mod.run_campaign(workflow, repo, cfg, "inp", REVISION)
    assert code == 1
    assert "campaign blocked" in capsys.readouterr().err

    indexed = state_mod.matching_campaigns(state_root, selected=repo, input_name="inp", revision=REVISION)
    doc = state_mod.load_campaign(indexed[0])
    assert doc["state"] == "BLOCKED"
    assert doc["blocker"]["failure"]["operation"] == "promotion confirmation"

    # Second attempt: successful resume
    published_commit = "d" * 40
    def ok_apply(wf, plan, confirm=None):
        return 0

    def mock_result(plan, digest):
        return {
            "path": os.fspath(plan / "result.json"),
            "digest": "4" * 64,
            "terminal_state": "PROMOTION_PUBLISHED_AWAITING_ACTIVATION",
            "published_commit": published_commit,
            "status": "PUBLISHED",
            "failure": None,
            "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"},
        }

    monkeypatch.setattr(campaign_mod, "native_apply_promotion", ok_apply)
    monkeypatch.setattr(campaign_mod, "load_native_result", mock_result)

    code = campaign_mod.resume_campaign(workflow, repo, cfg, campaign=indexed[0])
    assert code == 0
    doc = state_mod.load_campaign(indexed[0])
    assert doc["state"] == "COMPLETE_NO_ACTIVATION"
    assert doc["stages"][0]["status"] == "PUBLISHED"
    assert doc["stages"][0]["published_commit"] == published_commit


def test_campaign_resume_from_retained_confirmation_blocked_state(campaign_modules, tmp_path, monkeypatch, capsys):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules

    repo, commit0 = _make_git_repo(tmp_path / "repo")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo)

    campaign_dir = tmp_path / "camp_retained_blocked"
    campaign_dir.mkdir(mode=0o700)
    stages_dir = campaign_dir / "stages"
    stages_dir.mkdir(mode=0o700)
    stages_dir.chmod(0o700)
    s1_dir = stages_dir / "s1"
    s1_dir.mkdir(mode=0o700)
    s1_dir.chmod(0o700)
    plan_dir = s1_dir / "attempt-1"
    plan_dir.mkdir(mode=0o700)
    plan_dir.chmod(0o700)

    class MockArtifact:
        def __init__(self, d):
            self.directory = d
            self.digest = "67d7" + "a" * 60
            self.document = {"status": "READY_FOR_REVIEW", "repository": repo}

    (plan_dir / "plan.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(campaign_mod, "load_plan", lambda p: MockArtifact(p))

    stages = [{
        "name": "s1",
        "kind": "composition",
        "repository": os.fspath(repo),
        "input": "inp",
        "revision": REVISION,
        "status": "BLOCKED",
        "current_plan": os.fspath(plan_dir),
        "published_commit": None,
        "impact": None,
        "attempts": [{
            "number": 1,
            "plan_path": os.fspath(plan_dir),
            "plan_digest": "67d7" + "a" * 60,
            "plan_status": "READY_FOR_REVIEW",
            "result_path": None,
            "result_digest": None,
            "terminal_state": None,
            "published_commit": None,
            "failure": None,
        }],
        "failure": {
            "message": "promotion confirmation requires a controlling terminal",
            "operation": "promotion confirmation",
            "evidence": None,
        },
    }]
    doc = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc["stages"] = stages
    doc["state"] = "BLOCKED"
    doc["last_outcome"] = "BLOCKED"
    doc["blocker"] = {"stage": "s1", "failure": stages[0]["failure"]}
    state_mod.persist_campaign(campaign_dir, doc)
    state_mod.update_index(state_root, campaign=campaign_dir, selected=repo, input_name="inp", revision=REVISION, state="BLOCKED")

    # Render status shows exact resume guidance
    rendered = state_mod.render_status(doc, campaign_dir)
    assert "guidance: run 'nix-update promote campaign resume" in rendered

    # When resumed without controlling terminal, unblocks to AWAITING_APPLY and exits 0
    monkeypatch.setattr(campaign_mod, "_has_controlling_terminal", lambda: False)
    code = campaign_mod.resume_campaign(workflow, repo, cfg, campaign=campaign_dir)
    assert code == 0
    resumed_doc = state_mod.load_campaign(campaign_dir)
    assert resumed_doc["state"] == "AWAITING_APPLY"
    assert resumed_doc["stages"][0]["status"] == "READY_FOR_REVIEW"


def test_campaign_already_selected_no_empty_commit(campaign_modules, tmp_path, monkeypatch, capsys):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules

    repo, commit0 = _make_git_repo(tmp_path / "repo")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo)

    class MockArtifact:
        def __init__(self, d):
            self.directory = d
            self.digest = "5" * 64
            self.document = {
                "status": "ALREADY_SELECTED",
                "repository": {"commit": commit0},
                "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"},
            }

    monkeypatch.setattr(campaign_mod, "_prepare_native", lambda wf, r, inp, rev, out, cfg, stg: MockArtifact(out))

    code = campaign_mod.run_campaign(workflow, repo, cfg, "inp", commit0)
    assert code == 0
    out = capsys.readouterr().out
    assert "already selected; no empty commit" in out

    indexed = state_mod.matching_campaigns(state_root, selected=repo, input_name="inp", revision=commit0)
    doc = state_mod.load_campaign(indexed[0])
    assert doc["state"] == "AWAITING_DEPLOYMENT_RECONCILIATION"
    assert doc["stages"][0]["status"] == "ALREADY_SELECTED"
    assert doc["stages"][0]["published_commit"] == commit0


def test_campaign_upstream_success_downstream_failure_and_resume(campaign_modules, tmp_path, monkeypatch, capsys):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules

    repo1, commit1 = _make_git_repo(tmp_path / "repo1")
    repo2, commit2 = _make_git_repo(tmp_path / "repo2")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo1)

    stages = [
        {"name": "stage-1", "kind": "composition", "repository": repo1, "input": "inp", "revision": REVISION},
        {"name": "stage-2", "kind": "composition", "repository": repo2, "input": "stage-1", "revision": None},
    ]
    monkeypatch.setattr(campaign_mod, "resolve_campaign_stages", lambda *args, **kwargs: stages)
    monkeypatch.setattr(campaign_mod, "_has_controlling_terminal", lambda: True)

    stage1_pub = "1" * 40
    stage2_pub = "2" * 40
    step = 0

    def mock_prepare(wf, r, inp, rev, out, cfg, stg):
        class MockArtifact:
            def __init__(self, d):
                self.directory = d
                self.digest = "6" * 64
                self.document = {"status": "READY_FOR_REVIEW"}
        return MockArtifact(out)

    monkeypatch.setattr(campaign_mod, "_prepare_native", mock_prepare)
    monkeypatch.setattr(campaign_mod, "load_plan", lambda p: mock_prepare(None, None, None, None, p, None, None))

    def mock_apply(wf, plan, confirm=None):
        nonlocal step
        step += 1
        if step == 1:
            # Stage 1 succeeds
            return 0
        if step == 2:
            # Stage 2 fails initially
            raise deployment.ConfigError("stage 2 apply failed")
        # Step 3: resumed stage 2 succeeds
        return 0

    def mock_load_result(plan, digest):
        nonlocal step
        return {
            "path": os.fspath(plan / "result.json"),
            "digest": "7" * 64,
            "terminal_state": "PROMOTION_PUBLISHED_AWAITING_ACTIVATION",
            "published_commit": stage1_pub if step == 1 else stage2_pub,
            "status": "PUBLISHED",
            "failure": None,
            "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"},
        }

    monkeypatch.setattr(campaign_mod, "native_apply_promotion", mock_apply)
    monkeypatch.setattr(campaign_mod, "load_native_result", mock_load_result)

    code = campaign_mod.run_campaign(workflow, repo1, cfg, "inp", REVISION)
    assert code == 1

    indexed = state_mod.matching_campaigns(state_root, selected=repo1, input_name="inp", revision=REVISION)
    doc = state_mod.load_campaign(indexed[0])
    assert doc["stages"][0]["status"] == "PUBLISHED"
    assert doc["stages"][0]["published_commit"] == stage1_pub
    assert doc["stages"][1]["status"] == "BLOCKED"
    assert doc["stages"][1]["revision"] == stage1_pub

    # Resume resumes stage 2 directly without re-applying stage 1
    code = campaign_mod.resume_campaign(workflow, repo1, cfg, campaign=indexed[0])
    assert code == 0
    doc = state_mod.load_campaign(indexed[0])
    assert doc["state"] == "COMPLETE_NO_ACTIVATION"
    assert doc["stages"][0]["status"] == "PUBLISHED"
    assert doc["stages"][1]["status"] == "PUBLISHED"
    assert doc["stages"][1]["published_commit"] == stage2_pub


def test_core_route_stage_resolution_and_preparation(campaign_modules, tmp_path, monkeypatch):
    campaign_mod, state_mod, _, topology_mod, core_mod, config_mod, deployment, state_root = campaign_modules

    core_dir, core_commit = _make_git_repo(tmp_path / "core")
    # The observational stage must bind real tracked tool bytes, never a placeholder identity.
    (core_dir / "bin").mkdir()
    (core_dir / "bin/nix-update").write_text("#!/bin/sh\nexit 0\n")
    (core_dir / "libexec/nix_darwin_local").mkdir(parents=True)
    (core_dir / "libexec/nix_darwin_local/__init__.py").write_text("# fixture tool\n")
    subprocess.run(["git", "add", "bin", "libexec"], cwd=core_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "fixture tool identity"], cwd=core_dir, check=True, capture_output=True)
    public_dir, pub_commit = _make_git_repo(tmp_path / "public")
    private_dir, priv_commit = _make_git_repo(tmp_path / "private")

    public_contract = _contract(tmp_path / "public-remote.git", name="composition-nd", input_name="core")
    private_contract = _layered_contract(
        tmp_path / "private-remote.git", public_contract, name="composition-private-nd", input_name="composition-nd"
    )

    (private_dir / "promotion-contract.json").write_text(json.dumps(private_contract), encoding="utf-8")
    cfg = config_mod.ControlConfig(
        workspace_root=tmp_path,
        dinas_root=core_dir,
        promotion_checkouts=(("composition-nd", public_dir),),
    )
    workflow = deployment.Workflow(core_dir)

    stages = topology_mod.resolve_campaign_stages(
        private_dir, cfg, "core", core_commit, workflow=workflow, core_dir=core_dir
    )
    assert stages is not None
    assert len(stages) == 3
    assert stages[0]["kind"] == "core"
    assert stages[0]["name"] == "core"
    assert stages[0]["repository"] == core_dir.resolve()
    assert stages[0]["revision"] == core_commit

    assert stages[1]["kind"] == "composition"
    assert stages[1]["name"] == "composition-nd"
    assert stages[1]["repository"] == public_dir.resolve()
    assert stages[1]["input"] == "core"

    assert stages[2]["kind"] == "composition"
    assert stages[2]["name"] == "composition-private-nd"
    assert stages[2]["repository"] == private_dir.resolve()
    assert stages[2]["input"] == "composition-nd"

    # Test prepare_campaign_stage on Stage 0 (core stage)
    out0 = tmp_path / "out0"
    art0 = topology_mod.prepare_campaign_stage(workflow, core_dir, "core", core_commit, out0, cfg, stages[0])
    assert art0.document["status"] == "ALREADY_SELECTED"
    assert art0.document["repository"]["commit"] == core_commit


def test_failure_from_records_matches_evidence_stream(campaign_modules, tmp_path):
    _, _, result_mod, _, _, _, deployment, _ = campaign_modules

    class MockRecord:
        def __init__(self, exit_status, label, stdout, stderr):
            self.exit_status = exit_status
            self.label = label
            self.stdout = stdout
            self.stderr = stderr

    class MockWorkflow:
        def __init__(self, records):
            self.records = records

    # Stderr has summary
    rec_stderr = MockRecord(
        1, "cmd-stderr",
        {"path": "/path/to/cmd.stdout", "summary": ""},
        {"path": "/path/to/cmd.stderr", "summary": "stderr failure explanation"},
    )
    fail1 = result_mod.failure_from_records(MockWorkflow([rec_stderr]), "operation failed")
    assert "stderr failure explanation" in fail1["message"]
    assert fail1["evidence"] == "/path/to/cmd.stderr"

    # Stdout has summary, stderr summary is empty
    rec_stdout = MockRecord(
        1, "cmd-stdout",
        {"path": "/path/to/cmd.stdout", "summary": "stdout failure explanation"},
        {"path": "/path/to/cmd.stderr", "summary": ""},
    )
    fail2 = result_mod.failure_from_records(MockWorkflow([rec_stdout]), "operation failed")
    assert "stdout failure explanation" in fail2["message"]
    assert fail2["evidence"] == "/path/to/cmd.stdout"


def test_result_validation_and_loading(campaign_modules, tmp_path):
    campaign_mod, state_mod, result_mod, _, _, _, deployment, _ = campaign_modules

    plan_dir = tmp_path / "plan"
    plan_dir.mkdir(mode=0o700)
    digest = "a" * 64
    commit = "c" * 40

    valid_doc = {
        "schema": "nix-update-promotion-result-v1",
        "plan_digest": digest,
        "no_activation": True,
        "terminal_state": "PROMOTION_PUBLISHED_AWAITING_ACTIVATION",
        "published_commit": commit,
        "failure": None,
        "commands": [],
        "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"},
    }
    raw = deployment.canonical_bytes(valid_doc)
    result_path = plan_dir / "result.json"
    result_path.write_bytes(raw)
    result_path.chmod(0o600)

    # Valid load
    res = result_mod.load_native_result(plan_dir, digest, expected_path=result_path)
    assert res["status"] == "PUBLISHED"
    assert res["terminal_state"] == "PROMOTION_PUBLISHED_AWAITING_ACTIVATION"
    assert res["published_commit"] == commit
    assert res["failure"] is None

    # Invalid digest parameter
    with pytest.raises(deployment.ConfigError, match="digest is invalid"):
        result_mod.load_native_result(plan_dir, "short")

    # Mismatched expected path
    with pytest.raises(deployment.ConfigError, match="not bound to its plan"):
        result_mod.load_native_result(plan_dir, digest, expected_path=plan_dir / "other.json")

    # Non-canonical bytes
    result_path.write_text(json.dumps(valid_doc, indent=2), encoding="utf-8")
    with pytest.raises(deployment.ConfigError, match="not canonical"):
        result_mod.load_native_result(plan_dir, digest)

    # Re-write canonical
    result_path.write_bytes(raw)

    # Defect validations
    def check_doc_defect(defect_doc, match):
        with pytest.raises(deployment.ConfigError, match=match):
            result_mod.validate_native_result(defect_doc, digest)

    check_doc_defect("not a dict", "must be a JSON object")
    check_doc_defect({**valid_doc, "schema": "wrong"}, "schema is invalid")
    check_doc_defect({**valid_doc, "plan_digest": "b" * 64}, "plan digest does not match")
    check_doc_defect({**valid_doc, "no_activation": False}, "forbid activation")
    check_doc_defect({**valid_doc, "terminal_state": "UNKNOWN_STATE"}, "terminal state is invalid")
    check_doc_defect({**valid_doc, "published_commit": "bad-commit"}, "published commit is invalid")
    check_doc_defect({**valid_doc, "published_commit": None}, "publication state has no commit identity")
    check_doc_defect({**valid_doc, "failure": 123}, "failure is invalid")
    check_doc_defect({**valid_doc, "failure": "x" * 9000}, "failure exceeds its size limit")


def test_result_commands_validation(campaign_modules, tmp_path):
    _, _, result_mod, _, _, _, deployment, _ = campaign_modules

    def check_cmd_defect(cmds, match):
        with pytest.raises(deployment.ConfigError, match=match):
            result_mod._validate_commands(cmds)

    check_cmd_defect("not a list", "command evidence is invalid")
    check_cmd_defect(["not a dict"], "command record is invalid")
    check_cmd_defect([{"label": 123}], "command label is invalid")
    check_cmd_defect([{"label": "ok", "exit_status": "zero"}], "command status is invalid")
    check_cmd_defect([{"label": "ok", "exit_status": 0, "stdout": "not dict"}], "command evidence is invalid")
    check_cmd_defect([{"label": "ok", "exit_status": 0, "stdout": {"path": "relative/path"}}], "evidence path is invalid")
    check_cmd_defect([{"label": "ok", "exit_status": 0, "stdout": {"summary": 123}}], "evidence summary is invalid")

    # Command evidence extraction
    plan_path = tmp_path / "plan/result.json"
    cmd_fail = {
        "label": "git push",
        "exit_status": 1,
        "stderr": {"path": "/path/err", "summary": "push rejected"},
        "stdout": {"path": "/path/out", "summary": ""},
    }
    label, ev = result_mod._first_failed_command([cmd_fail], plan_path)
    assert label == "git push"
    assert ev == Path("/path/err")

    # Failure from result with failure text vs without
    doc_fail = {
        "terminal_state": "COMMITTED_NOT_PUBLISHED",
        "failure": "push failed",
        "commands": [cmd_fail],
    }
    res_fail = result_mod.failure_from_result(doc_fail, plan_path)
    assert res_fail["message"] == "push failed"
    assert res_fail["operation"] == "git push"
    assert res_fail["evidence"] == "/path/err"

    doc_no_fail_text = {
        "terminal_state": "COMMITTED_NOT_PUBLISHED",
        "failure": None,
        "commands": [cmd_fail],
    }
    res_no_fail = result_mod.failure_from_result(doc_no_fail_text, plan_path)
    assert "COMMITTED_NOT_PUBLISHED" in res_no_fail["message"]
    assert res_no_fail["operation"] == "git push"


def test_campaign_state_validations(campaign_modules, tmp_path):
    _, state_mod, _, _, _, _, deployment, state_root = campaign_modules

    selected = tmp_path / "selected"
    selected.mkdir()
    stages = [{"name": "s1", "kind": "composition", "repository": selected, "input": "inp", "revision": REVISION}]
    doc = state_mod.new_campaign(selected, "inp", REVISION, stages)

    def sign(d):
        unsigned = dict(d)
        unsigned["campaign_digest"] = ""
        digest = deployment.sha256_bytes(deployment.canonical_bytes(unsigned))
        unsigned["campaign_digest"] = digest
        return unsigned

    def check_state_defect(d, match):
        with pytest.raises(deployment.ConfigError, match=match):
            state_mod.validate_campaign(d, raw=None, campaign=None)

    # Missing/extra fields
    bad_doc = dict(doc)
    del bad_doc["stages"]
    check_state_defect(bad_doc, "journal fields are invalid")

    check_state_defect({**doc, "schema": "wrong"}, "schema or version is invalid")
    check_state_defect({**doc, "campaign_digest": "0" * 64}, "campaign digest does not match")
    check_state_defect(sign({**doc, "activationPerformed": True}), "contains an activation claim")
    check_state_defect(sign({**doc, "dry_run": "yes"}), "dry-run marker is invalid")
    check_state_defect(sign({**doc, "selected": "not/abs"}), "selected path is invalid")
    check_state_defect(sign({**doc, "current_stage": -1}), "current stage is invalid")
    check_state_defect(sign({**doc, "current_stage": 5}), "current stage is out of range")
    check_state_defect(sign({**doc, "stages": []}), "stages are invalid")

    # Duplicate stage names
    dup_stages = doc["stages"] + doc["stages"]
    check_state_defect(sign({**doc, "stages": dup_stages}), "stage names are not unique")

    # Stage validator defects
    bad_stage1 = {**doc["stages"][0], "kind": 123}
    check_state_defect(sign({**doc, "stages": [bad_stage1]}), "stage kind is invalid")
    bad_stage2 = {**doc["stages"][0], "status": "INVALID_STATUS"}
    check_state_defect(sign({**doc, "stages": [bad_stage2]}), "stage status is invalid")
    bad_stage3 = {**doc["stages"][0], "repository": "not/abs"}
    check_state_defect(sign({**doc, "stages": [bad_stage3]}), "stage repository path is invalid")

    # Blocker and history validator defects
    check_state_defect(sign({**doc, "blocker": {"stage": 123}}), "blocker is invalid")
    check_state_defect(sign({**doc, "history": [{"state": "INVALID"}]}), "history entry is invalid")
    check_state_defect(sign({**doc, "final_impact": "not dict"}), "final impact is invalid")


def test_campaign_cli_branches(campaign_modules, tmp_path, capsys, monkeypatch):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules

    repo, _ = _make_git_repo(tmp_path / "repo")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo)

    # status_campaign with no args
    assert campaign_mod.status_campaign(None, None) == 1
    assert "status requires" in capsys.readouterr().err

    # status_campaign with nonexistent campaign
    assert campaign_mod.status_campaign(None, campaign=tmp_path / "nonexistent") == 1
    assert "unable to read campaign" in capsys.readouterr().err

    # status_campaign with selected flake having 0 matches
    assert campaign_mod.status_campaign(repo) == 1
    assert "no matching promotion campaign is indexed" in capsys.readouterr().err

    # resume_campaign with selected flake having 0 matches
    assert campaign_mod.resume_campaign(workflow, repo, cfg) == 1
    assert "no matching promotion campaign is indexed" in capsys.readouterr().err

    # Create two campaigns for repo to test multiple matches branch
    stages = [{"name": "s1", "kind": "composition", "repository": repo, "input": "inp", "revision": REVISION}]
    c1 = tmp_path / "c1"
    c1.mkdir(mode=0o700)
    c2 = tmp_path / "c2"
    c2.mkdir(mode=0o700)
    doc1 = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc2 = state_mod.new_campaign(repo, "inp", REVISION, stages)
    state_mod.persist_campaign(c1, doc1)
    state_mod.persist_campaign(c2, doc2)
    state_mod.update_index(state_root, campaign=c1, selected=repo, input_name="inp", revision=REVISION, state="NEW")
    state_mod.update_index(state_root, campaign=c2, selected=repo, input_name="inp", revision=REVISION, state="NEW")

    # status_campaign with multiple matches
    assert campaign_mod.status_campaign(repo) == 1
    assert "multiple matching promotion campaigns" in capsys.readouterr().err

    # resume_campaign with multiple matches
    assert campaign_mod.resume_campaign(workflow, repo, cfg) == 1
    assert "multiple matching promotion campaigns" in capsys.readouterr().err

    # run_campaign with multiple existing matches
    assert campaign_mod.run_campaign(workflow, repo, cfg, "inp", REVISION) == 1
    assert "multiple matching promotion campaigns" in capsys.readouterr().err

    # resume_campaign with mismatched selected
    other_repo, _ = _make_git_repo(tmp_path / "other")
    with pytest.raises(deployment.ConfigError, match="selected flake does not match"):
        campaign_mod.resume_campaign(workflow, other_repo, cfg, campaign=c1)

    # resume_campaign with dry-run campaign
    doc_dry = state_mod.new_campaign(repo, "inp", REVISION, stages, dry_run=True)
    c_dry = tmp_path / "c_dry"
    c_dry.mkdir(mode=0o700)
    state_mod.persist_campaign(c_dry, doc_dry)
    assert campaign_mod.resume_campaign(workflow, repo, cfg, campaign=c_dry) == 0
    assert "dry-run campaign contains evidence only" in capsys.readouterr().out

    # resume_campaign with completed campaign
    doc_done = state_mod.transition(doc1, "COMPLETE_NO_ACTIVATION", "done")
    c_done = tmp_path / "c_done"
    c_done.mkdir(mode=0o700)
    state_mod.persist_campaign(c_done, doc_done)
    assert campaign_mod.resume_campaign(workflow, repo, cfg, campaign=c_done) == 0
    assert "COMPLETE_NO_ACTIVATION" in capsys.readouterr().out

    # status_campaign with mismatched selected
    assert campaign_mod.status_campaign(other_repo, campaign=c1) == 1
    assert "selected flake does not match" in capsys.readouterr().err


def test_campaign_additional_branches(campaign_modules, tmp_path, capsys):
    campaign_mod, state_mod, result_mod, topology_mod, core_mod, config_mod, deployment, state_root = campaign_modules

    repo, _ = _make_git_repo(tmp_path / "repo")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo)

    # 1. run_campaign with existing campaign that has corrupt journal
    c_corrupt = tmp_path / "c_corrupt"
    c_corrupt.mkdir(mode=0o700)
    (c_corrupt / "campaign.json").write_text("not json", encoding="utf-8")
    (c_corrupt / "campaign.json").chmod(0o600)
    state_mod.update_index(state_root, campaign=c_corrupt, selected=repo, input_name="inp", revision=REVISION, state="NEW")
    assert campaign_mod.run_campaign(workflow, repo, cfg, "inp", REVISION) == 1
    assert "multiple matching promotion campaigns" in capsys.readouterr().err

    # Clear index to test next case
    state_mod._index_path(state_root).unlink(missing_ok=True)

    # 2. Existing campaign with mismatched dry-run flag
    stages = [{"name": "s1", "kind": "composition", "repository": repo, "input": "inp", "revision": REVISION}]
    c_dry = tmp_path / "c_dry_run"
    c_dry.mkdir(mode=0o700)
    doc_dry = state_mod.new_campaign(repo, "inp", REVISION, stages, dry_run=True)
    state_mod.persist_campaign(c_dry, doc_dry)
    state_mod.update_index(state_root, campaign=c_dry, selected=repo, input_name="inp", revision=REVISION, state="NEW")
    with pytest.raises(deployment.ConfigError, match="dry-run mode does not match"):
        campaign_mod.run_campaign(workflow, repo, cfg, "inp", REVISION, dry_run=False)

    # 3. Existing campaign with different input or revision in journal
    state_mod._index_path(state_root).unlink(missing_ok=True)
    c_diff = tmp_path / "c_diff"
    c_diff.mkdir(mode=0o700)
    doc_diff = state_mod.new_campaign(repo, "different_inp", REVISION, stages)
    state_mod.persist_campaign(c_diff, doc_diff)
    state_mod.update_index(state_root, campaign=c_diff, selected=repo, input_name="inp", revision=REVISION, state="NEW")
    assert campaign_mod.run_campaign(workflow, repo, cfg, "inp", REVISION) == 1

    # 4. read_index defects
    idx_file = state_mod._index_path(state_root)
    idx_file.write_text("not json", encoding="utf-8")
    idx_file.chmod(0o600)
    with pytest.raises(deployment.ConfigError, match="index is invalid"):
        state_mod.read_index(state_root)
    idx_file.unlink(missing_ok=True)

    # 5. _retry_same_plan
    stage_retry1 = {"current_plan": "/path/plan", "failure": {"operation": "promotion confirmation"}}
    assert campaign_mod._retry_same_plan(stage_retry1) is True
    stage_retry2 = {"current_plan": "/path/plan", "attempts": [{"terminal_state": "COMMITTED_NOT_PUBLISHED"}]}
    assert campaign_mod._retry_same_plan(stage_retry2) is True
    stage_no_retry = {"current_plan": None}
    assert campaign_mod._retry_same_plan(stage_no_retry) is False

    # 6. _complete with HOST_DERIVATIONS_CHANGED vs not
    doc_comp = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc_comp["stages"][0]["impact"] = {"classification": "HOST_DERIVATIONS_CHANGED"}
    completed1 = campaign_mod._complete(doc_comp)
    assert completed1["state"] == "AWAITING_ACTIVATION"

    doc_comp["stages"][0]["impact"] = {"classification": "HOST_DERIVATIONS_IDENTICAL"}
    completed2 = campaign_mod._complete(doc_comp)
    assert completed2["state"] == "COMPLETE_NO_ACTIVATION"

    # 7. _next_stage revision forwarding
    stage_two = [
        {"name": "s1", "kind": "composition", "repository": repo, "input": "inp", "revision": REVISION},
        {"name": "s2", "kind": "composition", "repository": repo, "input": "inp2", "revision": None},
    ]
    doc_two = state_mod.new_campaign(repo, "inp", REVISION, stage_two)
    doc_two["stages"][0]["published_commit"] = "1" * 40
    advanced = campaign_mod._next_stage(doc_two, 0)
    assert advanced["current_stage"] == 1
    assert advanced["stages"][1]["revision"] == "1" * 40


def test_prepare_and_apply_stage_defects(campaign_modules, tmp_path, monkeypatch):
    campaign_mod, state_mod, result_mod, _, _, config_mod, deployment, state_root = campaign_modules

    repo, _ = _make_git_repo(tmp_path / "repo")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo)

    campaign_dir = tmp_path / "camp"
    campaign_dir.mkdir(mode=0o700)

    stages = [{"name": "s1", "kind": "composition", "repository": repo, "input": "inp", "revision": REVISION}]
    doc = state_mod.new_campaign(repo, "inp", REVISION, stages)
    state_mod.persist_campaign(campaign_dir, doc)

    # 1. _prepare_stage: downstream stage with no published parent revision
    stg_no_rev = {"name": "s2", "kind": "composition", "repository": repo, "input": "inp2", "revision": None}
    doc_two = state_mod.new_campaign(repo, "inp", REVISION, [stages[0], stg_no_rev])
    doc_two["current_stage"] = 1
    blocked = campaign_mod._prepare_stage(workflow, repo, cfg, campaign_dir, doc_two, stg_no_rev)
    assert blocked["state"] == "BLOCKED"
    assert "no published parent revision" in blocked["blocker"]["failure"]["message"]

    # 2. _prepare_stage: native prepare raises ConfigError
    monkeypatch.setattr(
        campaign_mod,
        "_prepare_native",
        lambda *args: (_ for _ in ()).throw(deployment.ConfigError("prepare failed error")),
    )
    doc_prep_fail = state_mod.new_campaign(repo, "inp", REVISION, stages)
    blocked2 = campaign_mod._prepare_stage(workflow, repo, cfg, campaign_dir, doc_prep_fail, doc_prep_fail["stages"][0])
    assert blocked2["state"] == "BLOCKED"
    assert "prepare failed error" in blocked2["blocker"]["failure"]["message"]

    # 3. _prepare_stage: artifact status == BLOCKED
    class BlockedArtifact:
        directory = tmp_path / "art_blocked"
        digest = "a" * 64
        document = {"status": "BLOCKED", "blockers": ["manual blocker reason"]}

    BlockedArtifact.directory.mkdir(mode=0o700)
    monkeypatch.setattr(campaign_mod, "_prepare_native", lambda *args: BlockedArtifact())
    doc_art_blocked = state_mod.new_campaign(repo, "inp", REVISION, stages)
    blocked3 = campaign_mod._prepare_stage(workflow, repo, cfg, campaign_dir, doc_art_blocked, doc_art_blocked["stages"][0])
    assert blocked3["state"] == "BLOCKED"
    assert "manual blocker reason" in blocked3["blocker"]["failure"]["message"]

    # 4. _prepare_stage: artifact status == ALREADY_SELECTED but commit is None
    class AlreadyNoCommitArtifact:
        directory = tmp_path / "art_no_commit"
        digest = "b" * 64
        document = {"status": "ALREADY_SELECTED", "repository": {}}

    AlreadyNoCommitArtifact.directory.mkdir(mode=0o700)
    monkeypatch.setattr(campaign_mod, "_prepare_native", lambda *args: AlreadyNoCommitArtifact())
    doc_no_commit = state_mod.new_campaign(repo, "inp", REVISION, stages)
    blocked4 = campaign_mod._prepare_stage(workflow, repo, cfg, campaign_dir, doc_no_commit, doc_no_commit["stages"][0])
    assert blocked4["state"] == "BLOCKED"
    assert "no selected commit identity" in blocked4["blocker"]["failure"]["message"]

    # 5. _prepare_stage: artifact status unrecognized
    class UnrecognizedArtifact:
        directory = tmp_path / "art_unrecognized"
        digest = "c" * 64
        document = {"status": "SOMETHING_ELSE"}

    UnrecognizedArtifact.directory.mkdir(mode=0o700)
    monkeypatch.setattr(campaign_mod, "_prepare_native", lambda *args: UnrecognizedArtifact())
    doc_unrecognized = state_mod.new_campaign(repo, "inp", REVISION, stages)
    blocked5 = campaign_mod._prepare_stage(workflow, repo, cfg, campaign_dir, doc_unrecognized, doc_unrecognized["stages"][0])
    assert blocked5["state"] == "BLOCKED"
    assert "unrecognized plan status" in blocked5["blocker"]["failure"]["message"]

    # 6. _apply_stage: awaiting stage has no native plan
    doc_no_plan = state_mod.new_campaign(repo, "inp", REVISION, stages)
    blocked6 = campaign_mod._apply_stage(workflow, campaign_dir, doc_no_plan, doc_no_plan["stages"][0])
    assert blocked6["state"] == "BLOCKED"
    assert "has no native plan" in blocked6["blocker"]["failure"]["message"]

    # 7. _apply_stage: load_plan raises ConfigError
    doc_bad_plan = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc_bad_plan["stages"][0]["current_plan"] = os.fspath(tmp_path / "nonexistent_plan")
    blocked7 = campaign_mod._apply_stage(workflow, campaign_dir, doc_bad_plan, doc_bad_plan["stages"][0])
    assert blocked7["state"] == "BLOCKED"

    # 8. _apply_stage: artifact status != READY_FOR_REVIEW
    class NotReadyArtifact:
        directory = tmp_path / "art_not_ready"
        digest = "d" * 64
        document = {"status": "NEW"}

    doc_not_ready = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc_not_ready["stages"][0]["current_plan"] = os.fspath(tmp_path / "art_not_ready")
    monkeypatch.setattr(campaign_mod, "load_plan", lambda *args: NotReadyArtifact())
    blocked8 = campaign_mod._apply_stage(workflow, campaign_dir, doc_not_ready, doc_not_ready["stages"][0])
    assert blocked8["state"] == "BLOCKED"
    assert "no longer review-ready" in blocked8["blocker"]["failure"]["message"]

    # 9. _safe_stage_name, _ensure_stage_root, _stage_mapping
    assert campaign_mod._safe_stage_name("stage/with/slashes") == "stage_with_slashes"
    assert campaign_mod._safe_stage_name("stage name with spaces") == "stage_name_with_spaces"
    root = campaign_mod._ensure_stage_root(campaign_dir, campaign_dir / "stages/stage1")
    assert root.is_dir()

    mapped = campaign_mod._stage_mapping(
        campaign_mod.CampaignStage("stg", repo, "inp", revision=REVISION, kind="composition")
    )
    assert mapped["name"] == "stg"
    assert mapped["kind"] == "composition"


def test_evidence_and_failure_helpers(campaign_modules, tmp_path):
    _, _, result_mod, _, _, _, deployment, _ = campaign_modules

    fallback = Path("/fallback")

    # _command_evidence: stdout summary
    cmd1 = {"stdout": {"summary": "stdout err", "path": "/path/stdout"}}
    assert result_mod._command_evidence(cmd1, fallback) == Path("/path/stdout")

    # _command_evidence: no summary, path present
    cmd2 = {"stderr": {"summary": "", "path": "/path/stderr"}}
    assert result_mod._command_evidence(cmd2, fallback) == Path("/path/stderr")

    # _command_evidence: invalid path or newline in path
    cmd3 = {"stderr": {"summary": "", "path": "/path/with\nnewline"}}
    assert result_mod._command_evidence(cmd3, fallback) == fallback

    # _record_evidence
    rec1 = {"stdout": {"summary": "out", "path": "/path/stdout"}}
    assert result_mod._record_evidence(rec1) == Path("/path/stdout")

    rec2 = {"stderr": {"summary": "", "path": "/path/stderr"}}
    assert result_mod._record_evidence(rec2) == Path("/path/stderr")

    assert result_mod._record_evidence({"stderr": "not dict"}) is None

    # _failure empty message and operation
    f = result_mod._failure("", "", Path("/path/ev"))
    assert f["message"] == "native promotion failed"
    assert f["operation"] == "native promotion"
    assert f["evidence"] == "/path/ev"

    # _normal_path defects
    with pytest.raises(deployment.ConfigError, match="must be absolute"):
        result_mod._normal_path(Path("relative"), "test")
    with pytest.raises(deployment.ConfigError, match="must be normalized"):
        result_mod._normal_path(Path("/a/b/../c"), "test")


def test_apply_stage_terminal_state_branches(campaign_modules, tmp_path, monkeypatch):
    campaign_mod, state_mod, result_mod, _, _, config_mod, deployment, state_root = campaign_modules

    repo, _ = _make_git_repo(tmp_path / "repo")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo)

    campaign_dir = tmp_path / "camp_term"
    campaign_dir.mkdir(mode=0o700)

    stages = [{"name": "s1", "kind": "composition", "repository": repo, "input": "inp", "revision": REVISION}]
    doc = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc["stages"][0].update(state_mod.append_attempt(doc["stages"][0], {"path": os.fspath(tmp_path / "plan"), "digest": "1" * 64, "status": "READY_FOR_REVIEW"}))
    state_mod.persist_campaign(campaign_dir, doc)

    class MockArtifact:
        digest = "1" * 64
        document = {"status": "READY_FOR_REVIEW"}

    monkeypatch.setattr(campaign_mod, "load_plan", lambda *args: MockArtifact())
    monkeypatch.setattr(campaign_mod, "native_apply_promotion", lambda *args, **kwargs: 0)

    # 1. load_native_result raises ConfigError
    def fail_load_result(*args, **kwargs):
        raise deployment.ConfigError("result corrupt")
    monkeypatch.setattr(campaign_mod, "load_native_result", fail_load_result)

    blocked1 = campaign_mod._apply_stage(workflow, campaign_dir, doc, doc["stages"][0])
    assert blocked1["state"] == "BLOCKED"
    assert "result corrupt" in blocked1["blocker"]["failure"]["message"]

    # 2. terminal_state in RETRYABLE_PUBLICATION_STATES -> retains current_plan
    retryable_res = {
        "path": "/path/res.json",
        "digest": "2" * 64,
        "terminal_state": "COMMITTED_NOT_PUBLISHED",
        "published_commit": "c" * 40,
        "failure": {"message": "unpushed", "operation": "git push", "evidence": None},
        "impact": None,
    }
    monkeypatch.setattr(campaign_mod, "load_native_result", lambda *args, **kwargs: retryable_res)
    blocked2 = campaign_mod._apply_stage(workflow, campaign_dir, doc, doc["stages"][0])
    assert blocked2["state"] == "BLOCKED"
    assert blocked2["stages"][0]["current_plan"] == os.fspath(tmp_path / "plan")

    # 3. terminal_state in non-retryable states -> clears current_plan
    non_retryable_res = {
        "path": "/path/res.json",
        "digest": "3" * 64,
        "terminal_state": "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED",
        "published_commit": None,
        "failure": {"message": "failed pre pub", "operation": "build", "evidence": None},
        "impact": None,
    }
    monkeypatch.setattr(campaign_mod, "load_native_result", lambda *args, **kwargs: non_retryable_res)
    blocked3 = campaign_mod._apply_stage(workflow, campaign_dir, doc, doc["stages"][0])
    assert blocked3["state"] == "BLOCKED"
    assert blocked3["stages"][0]["current_plan"] is None


def test_campaign_stage_resolution_branches(campaign_modules, tmp_path, monkeypatch):
    campaign_mod, _, _, topology_mod, _, config_mod, deployment, _ = campaign_modules

    repo = tmp_path / "repo"
    repo.mkdir()

    # 1. Custom callable on config
    class CustomObj:
        def __init__(self, stages):
            self.stages = stages

    stage_item = {"name": "custom", "kind": "composition", "repository": repo, "input": "inp", "revision": REVISION}
    class MockConfig1:
        campaign_stages = staticmethod(lambda *args, **kwargs: CustomObj([stage_item]))

    resolved = campaign_mod.resolve_campaign_stages(repo, MockConfig1(), "inp", REVISION)
    assert len(resolved) == 1
    assert resolved[0]["name"] == "custom"

    # 2. Resolver returns empty list -> raises ConfigError
    class MockConfigEmpty:
        campaign_stages = staticmethod(lambda *args, **kwargs: [])
    with pytest.raises(deployment.ConfigError, match="returned no stages"):
        campaign_mod.resolve_campaign_stages(repo, MockConfigEmpty(), "inp", REVISION)

    # 3. Resolver returns too many stages -> raises ConfigError
    class MockConfigTooMany:
        campaign_stages = staticmethod(lambda *args, **kwargs: [stage_item] * 33)
    with pytest.raises(deployment.ConfigError, match="too many stages"):
        campaign_mod.resolve_campaign_stages(repo, MockConfigTooMany(), "inp", REVISION)

    # 4. Resolver returns None -> fall back to default single stage
    class MockConfigNone:
        campaign_stages = staticmethod(lambda *args, **kwargs: None)
    # Also patch topology to return None
    monkeypatch.setattr(topology_mod, "resolve_campaign_stages", lambda *args, **kwargs: None)
    fallback = campaign_mod.resolve_campaign_stages(repo, MockConfigNone(), "inp", REVISION)
    assert len(fallback) == 1
    assert fallback[0].name == "selected"


def test_campaign_blocked_non_retryable_transitions_to_preparing(campaign_modules, tmp_path, monkeypatch):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules

    repo, _ = _make_git_repo(tmp_path / "repo_retry")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo)

    campaign_dir = tmp_path / "camp_retry_advance"
    campaign_dir.mkdir(mode=0o700)

    stages = [{"name": "s1", "kind": "composition", "repository": repo, "input": "inp", "revision": REVISION}]
    doc = state_mod.new_campaign(repo, "inp", REVISION, stages)
    # Stage is BLOCKED with a non-retryable attempt
    doc["stages"][0].update(state_mod.append_attempt(doc["stages"][0], {"path": os.fspath(tmp_path / "plan"), "digest": "1" * 64, "status": "READY_FOR_REVIEW"}))
    doc["stages"][0].update(state_mod.update_attempt(doc["stages"][0], {
        "path": os.fspath(tmp_path / "res"),
        "digest": "2" * 64,
        "terminal_state": "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED",
        "status": "BLOCKED",
        "failure": {"message": "non-retryable failure", "operation": "build", "evidence": None},
    }))
    doc["state"] = "BLOCKED"
    doc["last_outcome"] = "BLOCKED"
    doc["blocker"] = {"stage": "s1", "failure": doc["stages"][0]["failure"]}
    state_mod.persist_campaign(campaign_dir, doc)

    # When advance runs on this campaign, _retry_same_plan returns False,
    # so stage resets to NEW and campaign transitions to PREPARING
    mock_prepare_called = False
    def mock_prepare(*args, **kwargs):
        nonlocal mock_prepare_called
        mock_prepare_called = True
        return None

    monkeypatch.setattr(campaign_mod, "_prepare_stage", mock_prepare)
    # Mock controlling terminal to avoid asking confirmation
    monkeypatch.setattr(campaign_mod, "_has_controlling_terminal", lambda: False)

    code = campaign_mod.resume_campaign(workflow, repo, cfg, campaign=campaign_dir)
    assert mock_prepare_called


def test_historical_status_and_path_validation(campaign_modules, tmp_path, capsys):
    campaign_mod, state_mod, _, _, _, _, deployment, _ = campaign_modules

    # 1. Historical journal valid
    hist_dir = tmp_path / "hist_valid"
    hist_dir.mkdir(mode=0o700)
    journal_data = {
        "schema": "nix-update-promotion-campaign-journal-v1",
        "version": 1,
        "state": "COMPLETE_NO_ACTIVATION",
        "input": "inp",
        "revision": REVISION,
        "updated_at": "2026-09-05T00:00:00Z",
    }
    journal_file = hist_dir / campaign_mod.JOURNAL_NAME
    journal_file.write_text(json.dumps(journal_data), encoding="utf-8")
    journal_file.chmod(0o600)

    assert campaign_mod._render_historical(hist_dir)
    out = capsys.readouterr().out
    assert "historical campaign journal" in out
    assert "COMPLETE_NO_ACTIVATION" in out

    # 2. Historical journal not a mapping
    hist_not_map = tmp_path / "hist_not_map"
    hist_not_map.mkdir(mode=0o700)
    jf = hist_not_map / campaign_mod.JOURNAL_NAME
    jf.write_text("[1, 2, 3]", encoding="utf-8")
    jf.chmod(0o600)
    assert not campaign_mod._render_historical(hist_not_map)

    # 3. Path validation
    with pytest.raises(deployment.ConfigError, match="must be an exact normalized"):
        campaign_mod._absolute_path(Path("/a/b/../b"), "test")

    # 4. Identity validation
    with pytest.raises(deployment.ConfigError, match="is invalid"):
        campaign_mod._validate_identity("", "test")
    with pytest.raises(deployment.ConfigError, match="is invalid"):
        campaign_mod._validate_identity("name with spaces", "test")
    with pytest.raises(deployment.ConfigError, match="is invalid"):
        campaign_mod._validate_identity("x" * 129, "test")


def test_campaign_state_index_and_allocate_branches(campaign_modules, tmp_path, monkeypatch):
    campaign_mod, state_mod, _, _, _, _, deployment, state_root = campaign_modules

    # 1. allocate_campaign with explicit output already existing
    exist_out = tmp_path / "exist_out"
    exist_out.mkdir()
    with pytest.raises(deployment.ConfigError, match="already exists"):
        state_mod.allocate_campaign(state_root, output=exist_out)

    # allocate_campaign with explicit output valid
    valid_out = tmp_path / "valid_out"
    created = state_mod.allocate_campaign(state_root, output=valid_out)
    assert created == valid_out.resolve()

    # 2. allocate_campaign retry collision exhaust
    def mock_create_dir(path, label, **kwargs):
        raise deployment.ConfigError("already exists")

    monkeypatch.setattr(state_mod, "create_private_directory", mock_create_dir)
    with pytest.raises(deployment.ConfigError, match="unable to allocate a unique campaign directory"):
        state_mod.allocate_campaign(state_root)

    # 3. read_index defects
    idx_path = state_mod._index_path(state_root)

    # Over size limit
    idx_path.write_bytes(b"x" * (state_mod.MAX_INDEX_BYTES + 1))
    idx_path.chmod(0o600)
    with pytest.raises(deployment.ConfigError, match="exceeds its size limit"):
        state_mod.read_index(state_root)

    # Invalid JSON
    idx_path.write_bytes(b"not json")
    with pytest.raises(deployment.ConfigError, match="index is invalid"):
        state_mod.read_index(state_root)

    # Wrong schema
    idx_path.write_text(json.dumps({"schema": "bad", "version": 1, "entries": []}))
    with pytest.raises(deployment.ConfigError, match="schema is invalid"):
        state_mod.read_index(state_root)

    # Entries not list
    idx_path.write_text(json.dumps({"schema": state_mod.INDEX_SCHEMA, "version": 1, "entries": "not list"}))
    with pytest.raises(deployment.ConfigError, match="entries are invalid"):
        state_mod.read_index(state_root)

    # Entry invalid keys or path
    bad_entry = {"campaign": "/rel/path", "selected": "/sel", "input": "i", "revision": "r", "state": "s", "updated_at": "t"}
    idx_path.write_text(json.dumps({"schema": state_mod.INDEX_SCHEMA, "version": 1, "entries": [bad_entry]}))
    # Unnormalized path in entry
    bad_norm = dict(bad_entry, campaign="/a/b/../c")
    idx_path.write_text(json.dumps({"schema": state_mod.INDEX_SCHEMA, "version": 1, "entries": [bad_norm]}))
    with pytest.raises(deployment.ConfigError, match="path is invalid"):
        state_mod.read_index(state_root)

    # Clean up index so subsequent calls start fresh
    idx_path.unlink(missing_ok=True)

    # 4. matching_campaigns filters
    c1 = tmp_path / "c1"
    c2 = tmp_path / "c2"
    s1 = tmp_path / "s1"
    s2 = tmp_path / "s2"
    state_mod.update_index(state_root, campaign=c1, selected=s1, input_name="inp1", revision="rev1", state="COMPLETE")
    state_mod.update_index(state_root, campaign=c2, selected=s2, input_name="inp2", revision="rev2", state="COMPLETE")

    assert state_mod.matching_campaigns(state_root, selected=s1) == [c1.resolve()]
    assert state_mod.matching_campaigns(state_root, input_name="inp2") == [c2.resolve()]
    assert state_mod.matching_campaigns(state_root, revision="rev1") == [c1.resolve()]
    assert state_mod.matching_campaigns(state_root, selected=s1, input_name="mismatch") == []

    # 5. render_status details
    doc = {
        "state": "BLOCKED",
        "input": "inp1",
        "revision": "rev1",
        "selected": os.fspath(s1),
        "campaign_digest": "d" * 64,
        "current_stage": 0,
        "stages": [
            {
                "name": "stg1",
                "status": "BLOCKED",
                "input": "inp1",
                "revision": "rev1",
                "published_commit": "p" * 40,
                "failure": {"message": "stage failed", "evidence": "/ev/stage"},
            }
        ],
        "blocker": {"stage": "stg1", "failure": {"message": "campaign blocker", "evidence": "/ev/blocker"}},
        "final_impact": {"classification": "HOST_DERIVATIONS_IDENTICAL", "activationSignal": "NOT_REQUIRED"},
    }
    rendered = state_mod.render_status(doc, c1)
    assert "published commit: pppp" in rendered
    assert "failure: stage failed" in rendered
    assert "evidence: /ev/stage" in rendered
    assert "blocked stage: stg1" in rendered
    assert "cause: campaign blocker" in rendered
    assert "evidence: /ev/blocker" in rendered
    assert "final impact: HOST_DERIVATIONS_IDENTICAL" in rendered


def test_dry_run_and_normal_run_lookup_and_disambiguation(campaign_modules, tmp_path, monkeypatch, capsys):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules

    repo, commit0 = _make_git_repo(tmp_path / "repo_disambig")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo)

    class MockArtifact:
        def __init__(self, d):
            self.directory = d
            self.digest = "d" * 64
            self.document = {"status": "READY_FOR_REVIEW", "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"}}

    monkeypatch.setattr(campaign_mod, "_prepare_native", lambda wf, r, inp, rev, out, cfg, stg: MockArtifact(out))
    monkeypatch.setattr(campaign_mod, "_has_controlling_terminal", lambda: False)

    # 1. Dry run then normal run in sequence without directory management
    code_dry = campaign_mod.run_campaign(workflow, repo, cfg, "inp", REVISION, dry_run=True)
    assert code_dry == 0
    out = capsys.readouterr().out
    assert "prepared (dry-run)" in out

    # Normal run does not fail with "dry-run mode does not match", uses separate state
    code_norm = campaign_mod.run_campaign(workflow, repo, cfg, "inp", REVISION, dry_run=False)
    assert code_norm == 0
    out = capsys.readouterr().out
    assert "awaiting native confirmation" in out

    # Both campaigns exist in index with distinct dry_run markers
    dry_matches = state_mod.matching_campaigns(state_root, selected=repo, input_name="inp", revision=REVISION, dry_run=True)
    norm_matches = state_mod.matching_campaigns(state_root, selected=repo, input_name="inp", revision=REVISION, dry_run=False)
    assert len(dry_matches) == 1
    assert len(norm_matches) == 1
    assert dry_matches[0] != norm_matches[0]

    doc_dry = state_mod.load_campaign(dry_matches[0])
    doc_norm = state_mod.load_campaign(norm_matches[0])
    assert doc_dry["state"] == "DRY_RUN"
    assert doc_dry["dry_run"] is True
    assert doc_norm["state"] == "AWAITING_APPLY"
    assert doc_norm["dry_run"] is False

    # 2. Repeated dry-run calls reuse the dry-run campaign and output status
    code_dry2 = campaign_mod.run_campaign(workflow, repo, cfg, "inp", REVISION, dry_run=True)
    assert code_dry2 == 0
    out = capsys.readouterr().out
    assert "state: DRY_RUN" in out

    # 3. Retained historical dry-runs alongside one resumable campaign
    # resume_campaign without --campaign picks the one normal campaign without ambiguity!
    code_resume = campaign_mod.resume_campaign(workflow, repo, cfg)
    assert code_resume == 0
    out = capsys.readouterr().out
    assert "awaiting native confirmation" in out
    assert str(norm_matches[0]) in out

    # status_campaign without --campaign picks the normal campaign without ambiguity!
    code_status = campaign_mod.status_campaign(repo)
    assert code_status == 0
    out = capsys.readouterr().out
    assert f"campaign: {norm_matches[0]}" in out
    assert "state: AWAITING_APPLY" in out

    # 4. Normal run -> dry-run sequence
    repo2, commit2 = _make_git_repo(tmp_path / "repo_normal_first")
    workflow2 = deployment.Workflow(repo2)
    assert campaign_mod.run_campaign(workflow2, repo2, cfg, "inp", REVISION, dry_run=False) == 0
    assert campaign_mod.run_campaign(workflow2, repo2, cfg, "inp", REVISION, dry_run=True) == 0
    assert len(state_mod.matching_campaigns(state_root, selected=repo2, dry_run=False)) == 1
    assert len(state_mod.matching_campaigns(state_root, selected=repo2, dry_run=True)) == 1

    # 5. Genuine multiple operational campaigns still require an explicit choice
    repo3, commit3 = _make_git_repo(tmp_path / "repo_ambig")
    workflow3 = deployment.Workflow(repo3)
    c_op1 = tmp_path / "c_op1"
    c_op2 = tmp_path / "c_op2"
    c_op1.mkdir(mode=0o700)
    c_op2.mkdir(mode=0o700)
    stages = [{"name": "s1", "kind": "composition", "repository": repo3, "input": "inp", "revision": REVISION}]
    doc_op1 = state_mod.new_campaign(repo3, "inp", REVISION, stages, dry_run=False)
    doc_op2 = state_mod.new_campaign(repo3, "inp", REVISION, stages, dry_run=False)
    state_mod.persist_campaign(c_op1, doc_op1)
    state_mod.persist_campaign(c_op2, doc_op2)
    state_mod.update_index(state_root, campaign=c_op1, selected=repo3, input_name="inp", revision=REVISION, state="NEW", dry_run=False)
    state_mod.update_index(state_root, campaign=c_op2, selected=repo3, input_name="inp", revision=REVISION, state="NEW", dry_run=False)

    assert campaign_mod.run_campaign(workflow3, repo3, cfg, "inp", REVISION, dry_run=False) == 1
    assert "multiple matching promotion campaigns" in capsys.readouterr().err
    assert campaign_mod.resume_campaign(workflow3, repo3, cfg) == 1
    assert "multiple matching promotion campaigns" in capsys.readouterr().err
    assert campaign_mod.status_campaign(repo3) == 1
    assert "multiple matching promotion campaigns" in capsys.readouterr().err


def test_interruption_recovery_before_native_mutation_starts(campaign_modules, tmp_path, monkeypatch, capsys):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules

    repo, commit0 = _make_git_repo(tmp_path / "repo_interrupted_pre")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo)

    camp_dir = tmp_path / "camp_interrupted_pre"
    camp_dir.mkdir(mode=0o700)
    plan_dir = _make_stage_plan_dir(camp_dir, "stage-1", 1)

    plan_digest = "1" * 64
    class MockArtifact:
        def __init__(self, d):
            self.directory = d
            self.digest = plan_digest
            self.document = {"status": "READY_FOR_REVIEW", "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"}}

    stages = [
        {
            "name": "stage-1",
            "kind": "composition",
            "repository": os.fspath(repo),
            "input": "inp",
            "revision": REVISION,
            "status": "APPLYING",
            "current_plan": os.fspath(plan_dir),
            "attempts": [
                {
                    "number": 1,
                    "plan_path": os.fspath(plan_dir),
                    "plan_digest": plan_digest,
                    "plan_status": "READY_FOR_REVIEW",
                    "result_path": None,
                    "result_digest": None,
                    "terminal_state": None,
                    "published_commit": None,
                    "failure": None,
                }
            ],
            "published_commit": None,
            "impact": None,
            "failure": None,
        }
    ]
    doc = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc["stages"] = stages
    doc = state_mod.transition(doc, "AWAITING_APPLY", "stage applying")
    state_mod.persist_campaign(camp_dir, doc)
    state_mod.update_index(state_root, campaign=camp_dir, selected=repo, input_name="inp", revision=REVISION, state="AWAITING_APPLY")

    monkeypatch.setattr(campaign_mod, "load_plan", lambda p: MockArtifact(p))
    monkeypatch.setattr(campaign_mod, "_has_controlling_terminal", lambda: False)

    # Resume outside controlling terminal: returns to prepared boundary and awaits confirmation
    code = campaign_mod.resume_campaign(workflow, repo, cfg, campaign=camp_dir)
    assert code == 0
    out = capsys.readouterr().out
    assert "awaiting native confirmation" in out

    reloaded = state_mod.load_campaign(camp_dir)
    assert reloaded["stages"][0]["status"] == "READY_FOR_REVIEW"
    assert reloaded["state"] == "AWAITING_APPLY"
    assert reloaded["blocker"] is None

    # Now simulate controlling terminal confirmation and apply
    pub_commit = "9" * 40
    monkeypatch.setattr(campaign_mod, "_has_controlling_terminal", lambda: True)
    monkeypatch.setattr(campaign_mod, "native_apply_promotion", lambda wf, p, confirm=None: 0)
    monkeypatch.setattr(
        campaign_mod,
        "load_native_result",
        lambda p, d: {
            "terminal_state": "PROMOTION_PUBLISHED_AWAITING_ACTIVATION",
            "published_commit": pub_commit,
            "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"},
        },
    )

    code2 = campaign_mod.resume_campaign(workflow, repo, cfg, campaign=camp_dir)
    assert code2 == 0
    doc2 = state_mod.load_campaign(camp_dir)
    assert doc2["state"] == "COMPLETE_NO_ACTIVATION"
    assert doc2["stages"][0]["status"] == "PUBLISHED"
    assert doc2["stages"][0]["published_commit"] == pub_commit


def test_interruption_recovery_native_success_interrupted_campaign_update(campaign_modules, tmp_path, monkeypatch, capsys):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules

    repo, commit0 = _make_git_repo(tmp_path / "repo_interrupted_success")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo)

    camp_dir = tmp_path / "camp_interrupted_success"
    camp_dir.mkdir(mode=0o700)
    plan_dir = _make_stage_plan_dir(camp_dir, "stage-1", 1)
    (plan_dir / "result.json").write_text("{}", encoding="utf-8")

    plan_digest = "2" * 64
    pub_commit = "8" * 40

    class MockArtifact:
        def __init__(self, d):
            self.directory = d
            self.digest = plan_digest
            self.document = {"status": "READY_FOR_REVIEW", "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"}}

    stages = [
        {
            "name": "stage-1",
            "kind": "composition",
            "repository": os.fspath(repo),
            "input": "inp",
            "revision": REVISION,
            "status": "APPLYING",
            "current_plan": os.fspath(plan_dir),
            "attempts": [
                {
                    "number": 1,
                    "plan_path": os.fspath(plan_dir),
                    "plan_digest": plan_digest,
                    "plan_status": "READY_FOR_REVIEW",
                    "result_path": None,
                    "result_digest": None,
                    "terminal_state": None,
                    "published_commit": None,
                    "failure": None,
                }
            ],
            "published_commit": None,
            "impact": None,
            "failure": None,
        }
    ]
    doc = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc["stages"] = stages
    doc = state_mod.transition(doc, "AWAITING_APPLY", "stage applying")
    state_mod.persist_campaign(camp_dir, doc)

    monkeypatch.setattr(campaign_mod, "load_plan", lambda p: MockArtifact(p))
    # native_apply_promotion must NOT be called because result is already complete!
    def forbid_apply(*args, **kwargs):
        raise AssertionError("native_apply_promotion should not be called when native result already exists")

    monkeypatch.setattr(campaign_mod, "native_apply_promotion", forbid_apply)
    monkeypatch.setattr(
        campaign_mod,
        "load_native_result",
        lambda p, d: {
            "terminal_state": "PROMOTION_PUBLISHED_AWAITING_ACTIVATION",
            "published_commit": pub_commit,
            "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"},
        },
    )

    code = campaign_mod.resume_campaign(workflow, repo, cfg, campaign=camp_dir)
    assert code == 0
    out = capsys.readouterr().out
    assert f"published {pub_commit}" in out

    doc_done = state_mod.load_campaign(camp_dir)
    assert doc_done["state"] == "COMPLETE_NO_ACTIVATION"
    assert doc_done["stages"][0]["status"] == "PUBLISHED"
    assert doc_done["stages"][0]["published_commit"] == pub_commit


def test_interruption_recovery_local_commit_needs_publication(campaign_modules, tmp_path, monkeypatch, capsys):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules

    repo, commit0 = _make_git_repo(tmp_path / "repo_interrupted_consumed")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo)

    camp_dir = tmp_path / "camp_interrupted_consumed"
    camp_dir.mkdir(mode=0o700)
    plan_dir = _make_stage_plan_dir(camp_dir, "stage-1", 1)

    plan_digest = "3" * 64
    pub_commit = "7" * 40

    # Place consumed marker on disk to simulate consumed authority
    consumed_dir = state_root / "promotion-consumed"
    consumed_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker = consumed_dir / f"{plan_digest}.json"
    marker.write_text("{}", encoding="utf-8")

    class MockArtifact:
        def __init__(self, d):
            self.directory = d
            self.digest = plan_digest
            self.document = {"status": "READY_FOR_REVIEW", "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"}}

    stages = [
        {
            "name": "stage-1",
            "kind": "composition",
            "repository": os.fspath(repo),
            "input": "inp",
            "revision": REVISION,
            "status": "APPLYING",
            "current_plan": os.fspath(plan_dir),
            "attempts": [
                {
                    "number": 1,
                    "plan_path": os.fspath(plan_dir),
                    "plan_digest": plan_digest,
                    "plan_status": "READY_FOR_REVIEW",
                    "result_path": None,
                    "result_digest": None,
                    "terminal_state": None,
                    "published_commit": None,
                    "failure": None,
                }
            ],
            "published_commit": None,
            "impact": None,
            "failure": None,
        }
    ]
    doc = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc["stages"] = stages
    doc = state_mod.transition(doc, "AWAITING_APPLY", "stage applying")
    state_mod.persist_campaign(camp_dir, doc)

    monkeypatch.setattr(campaign_mod, "load_plan", lambda p: MockArtifact(p))

    # 1. Non-controlling terminal: preserves stage and awaits confirmation
    monkeypatch.setattr(campaign_mod, "_has_controlling_terminal", lambda: False)
    code = campaign_mod.resume_campaign(workflow, repo, cfg, campaign=camp_dir)
    assert code == 0
    out = capsys.readouterr().out
    assert "awaiting native confirmation" in out

    doc_pre = state_mod.load_campaign(camp_dir)
    assert doc_pre["stages"][0]["status"] == "APPLYING"

    # 2. Controlling terminal: invokes native apply which recovers publication without a second commit
    apply_called = []
    def mock_native_recovery(wf, p, confirm=None):
        apply_called.append(True)
        return 0

    monkeypatch.setattr(campaign_mod, "_has_controlling_terminal", lambda: True)
    monkeypatch.setattr(campaign_mod, "native_apply_promotion", mock_native_recovery)
    monkeypatch.setattr(
        campaign_mod,
        "load_native_result",
        lambda p, d: {
            "terminal_state": "PROMOTION_PUBLISHED_AWAITING_ACTIVATION",
            "published_commit": pub_commit,
            "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"},
        },
    )

    code2 = campaign_mod.resume_campaign(workflow, repo, cfg, campaign=camp_dir)
    assert code2 == 0
    assert len(apply_called) == 1
    doc_post = state_mod.load_campaign(camp_dir)
    assert doc_post["state"] == "COMPLETE_NO_ACTIVATION"
    assert doc_post["stages"][0]["status"] == "PUBLISHED"
    assert doc_post["stages"][0]["published_commit"] == pub_commit


def test_interruption_recovery_public_stage_completed_private_stage_interrupted(campaign_modules, tmp_path, monkeypatch, capsys):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules

    repo1, commit1 = _make_git_repo(tmp_path / "repo_public")
    repo2, commit2 = _make_git_repo(tmp_path / "repo_private")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo1)

    camp_dir = tmp_path / "camp_multi_stage_interrupted"
    camp_dir.mkdir(mode=0o700)
    plan2_dir = _make_stage_plan_dir(camp_dir, "private-stage", 1)
    (plan2_dir / "result.json").write_text("{}", encoding="utf-8")

    plan2_digest = "5" * 64
    pub1_commit = "1" * 40
    pub2_commit = "2" * 40

    class MockArtifact2:
        def __init__(self, d):
            self.directory = d
            self.digest = plan2_digest
            self.document = {"status": "READY_FOR_REVIEW", "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"}}

    stages = [
        {
            "name": "public-stage",
            "kind": "composition",
            "repository": os.fspath(repo1),
            "input": "core",
            "revision": REVISION,
            "status": "PUBLISHED",
            "current_plan": None,
            "attempts": [
                {
                    "number": 1,
                    "plan_path": "/path/public/plan",
                    "plan_digest": "a" * 64,
                    "plan_status": "READY_FOR_REVIEW",
                    "result_path": "/path/public/result.json",
                    "result_digest": "b" * 64,
                    "terminal_state": "PROMOTION_PUBLISHED_AWAITING_ACTIVATION",
                    "published_commit": pub1_commit,
                    "failure": None,
                }
            ],
            "published_commit": pub1_commit,
            "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"},
            "failure": None,
        },
        {
            "name": "private-stage",
            "kind": "composition",
            "repository": os.fspath(repo2),
            "input": "public-stage",
            "revision": pub1_commit,
            "status": "APPLYING",
            "current_plan": os.fspath(plan2_dir),
            "attempts": [
                {
                    "number": 1,
                    "plan_path": os.fspath(plan2_dir),
                    "plan_digest": plan2_digest,
                    "plan_status": "READY_FOR_REVIEW",
                    "result_path": None,
                    "result_digest": None,
                    "terminal_state": None,
                    "published_commit": None,
                    "failure": None,
                }
            ],
            "published_commit": None,
            "impact": None,
            "failure": None,
        },
    ]

    doc = state_mod.new_campaign(repo1, "core", REVISION, stages)
    doc["stages"] = stages
    doc["current_stage"] = 1
    doc = state_mod.transition(doc, "AWAITING_APPLY", "stage applying")
    state_mod.persist_campaign(camp_dir, doc)

    monkeypatch.setattr(campaign_mod, "load_plan", lambda p: MockArtifact2(p))
    monkeypatch.setattr(
        campaign_mod,
        "load_native_result",
        lambda p, d: {
            "terminal_state": "PROMOTION_PUBLISHED_AWAITING_ACTIVATION",
            "published_commit": pub2_commit,
            "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"},
        },
    )

    code = campaign_mod.resume_campaign(workflow, repo1, cfg, campaign=camp_dir)
    assert code == 0

    doc_done = state_mod.load_campaign(camp_dir)
    assert doc_done["state"] == "COMPLETE_NO_ACTIVATION"
    # Stage 0 was preserved untouched
    assert doc_done["stages"][0]["status"] == "PUBLISHED"
    assert doc_done["stages"][0]["published_commit"] == pub1_commit
    # Stage 1 was adopted and published
    assert doc_done["stages"][1]["status"] == "PUBLISHED"
    assert doc_done["stages"][1]["published_commit"] == pub2_commit


def test_selection_completion_distinct_from_deployment_state(campaign_modules, tmp_path):
    campaign_mod, state_mod, _, _, _, _, _, _ = campaign_modules
    repo, _ = _make_git_repo(tmp_path / "repo_selection_state")

    stages = [{"name": "s1", "kind": "composition", "repository": repo, "input": "inp", "revision": REVISION}]

    # 1. ALREADY_SELECTED with identical host derivations -> AWAITING_DEPLOYMENT_RECONCILIATION
    doc1 = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc1["stages"][0]["status"] = "ALREADY_SELECTED"
    doc1["stages"][0]["impact"] = {"classification": "HOST_DERIVATIONS_IDENTICAL"}
    res1 = campaign_mod._complete(doc1)
    assert res1["state"] == "AWAITING_DEPLOYMENT_RECONCILIATION"
    rendered1 = state_mod.render_status(res1, tmp_path / "c1")
    assert "guidance: run 'nix-update --dry-run' or 'nix-update' to observe or reconcile live host deployment" in rendered1

    # 2. ALREADY_SELECTED with changed derivations -> AWAITING_DEPLOYMENT_RECONCILIATION
    doc2 = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc2["stages"][0]["status"] = "ALREADY_SELECTED"
    doc2["stages"][0]["impact"] = {"classification": "HOST_DERIVATIONS_CHANGED"}
    res2 = campaign_mod._complete(doc2)
    assert res2["state"] == "AWAITING_DEPLOYMENT_RECONCILIATION"

    # 3. ALREADY_SELECTED with missing impact -> AWAITING_DEPLOYMENT_RECONCILIATION
    doc3 = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc3["stages"][0]["status"] = "ALREADY_SELECTED"
    doc3["stages"][0]["impact"] = None
    res3 = campaign_mod._complete(doc3)
    assert res3["state"] == "AWAITING_DEPLOYMENT_RECONCILIATION"

    # 4. PUBLISHED with HOST_DERIVATIONS_CHANGED -> AWAITING_ACTIVATION
    doc4 = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc4["stages"][0]["status"] = "PUBLISHED"
    doc4["stages"][0]["impact"] = {"classification": "HOST_DERIVATIONS_CHANGED"}
    res4 = campaign_mod._complete(doc4)
    assert res4["state"] == "AWAITING_ACTIVATION"
    rendered4 = state_mod.render_status(res4, tmp_path / "c4")
    assert "guidance: run ordinary 'nix-update' to activate the new host configuration" in rendered4

    # 5. PUBLISHED with HOST_DERIVATIONS_IDENTICAL -> COMPLETE_NO_ACTIVATION
    doc5 = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc5["stages"][0]["status"] = "PUBLISHED"
    doc5["stages"][0]["impact"] = {"classification": "HOST_DERIVATIONS_IDENTICAL"}
    res5 = campaign_mod._complete(doc5)
    assert res5["state"] == "COMPLETE_NO_ACTIVATION"
    rendered5 = state_mod.render_status(res5, tmp_path / "c5")
    assert "guidance: no additional host change from this promotion; run 'nix-update --dry-run' to inspect live host state" in rendered5

    # 6. PUBLISHED with missing impact -> AWAITING_DEPLOYMENT_RECONCILIATION
    doc6 = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc6["stages"][0]["status"] = "PUBLISHED"
    doc6["stages"][0]["impact"] = None
    res6 = campaign_mod._complete(doc6)
    assert res6["state"] == "AWAITING_DEPLOYMENT_RECONCILIATION"

    # 7. PUBLISHED with unrecognized impact classification -> AWAITING_DEPLOYMENT_RECONCILIATION
    doc7 = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc7["stages"][0]["status"] = "PUBLISHED"
    doc7["stages"][0]["impact"] = {"classification": "UNKNOWN_SIGNAL"}
    res7 = campaign_mod._complete(doc7)
    assert res7["state"] == "AWAITING_DEPLOYMENT_RECONCILIATION"

    # 8. LOCAL_COMMITTED -> LOCAL_COMPLETE_AWAITING_ACTIVATION
    doc8 = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc8["stages"][0]["status"] = "LOCAL_COMMITTED"
    doc8["stages"][0]["local_commit"] = "a" * 40
    doc8["stages"][0]["impact"] = {"classification": "HOST_DERIVATIONS_CHANGED"}
    res8 = campaign_mod._complete(doc8)
    assert res8["state"] == "LOCAL_COMPLETE_AWAITING_ACTIVATION"
    rendered8 = state_mod.render_status(res8, tmp_path / "c8")
    assert (
        "guidance: local promotion commits are qualified; publish explicitly if desired, "
        "adopt into '[snapshot.sources]' (or pass '--source-revision'), then run ordinary 'nix-update' to activate"
    ) in rendered8


def test_interrupted_applying_stage_error_diagnostics(campaign_modules, tmp_path, monkeypatch):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules
    repo, _ = _make_git_repo(tmp_path / "repo_diagnostics")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo)

    camp_dir = tmp_path / "camp_diag"
    camp_dir.mkdir(mode=0o700)
    plan_dir = _make_stage_plan_dir(camp_dir, "s1", 1)

    stages = [
        {
            "name": "s1",
            "kind": "composition",
            "repository": os.fspath(repo),
            "input": "inp",
            "revision": REVISION,
            "status": "APPLYING",
            "current_plan": os.fspath(plan_dir),
            "attempts": [
                {
                    "number": 1,
                    "plan_path": os.fspath(plan_dir),
                    "plan_digest": "d" * 64,
                    "plan_status": "READY_FOR_REVIEW",
                    "result_path": None,
                    "result_digest": None,
                    "terminal_state": None,
                    "published_commit": None,
                    "failure": None,
                }
            ],
            "published_commit": None,
            "impact": None,
            "failure": None,
        }
    ]
    doc = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc["stages"] = stages
    doc = state_mod.transition(doc, "AWAITING_APPLY", "stage applying")
    state_mod.persist_campaign(camp_dir, doc)

    # 1. load_plan failure produces actionable blocker diagnostic
    monkeypatch.setattr(campaign_mod, "load_plan", lambda p: (_ for _ in ()).throw(deployment.ConfigError("corrupted plan bytes")))
    assert campaign_mod.resume_campaign(workflow, repo, cfg, campaign=camp_dir) == 1
    doc_err1 = state_mod.load_campaign(camp_dir)
    assert doc_err1["state"] == "BLOCKED"
    assert "corrupted plan bytes" in doc_err1["blocker"]["failure"]["message"]

    # 2. Corrupted result.json produces actionable blocker diagnostic
    (plan_dir / "result.json").write_text("corrupted json", encoding="utf-8")
    class MockArtifact:
        def __init__(self, d):
            self.directory = d
            self.digest = "d" * 64
            self.document = {"status": "READY_FOR_REVIEW"}

    monkeypatch.setattr(campaign_mod, "load_plan", lambda p: MockArtifact(p))
    doc_reset = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc_reset["stages"] = stages
    doc_reset = state_mod.transition(doc_reset, "AWAITING_APPLY", "reset")
    state_mod.persist_campaign(camp_dir, doc_reset)

    assert campaign_mod.resume_campaign(workflow, repo, cfg, campaign=camp_dir) == 1
    doc_err2 = state_mod.load_campaign(camp_dir)
    assert doc_err2["state"] == "BLOCKED"
    assert "native result is invalid" in doc_err2["blocker"]["failure"]["message"]

    # 3. Non-retryable failure result (BLOCKED_BEFORE_MUTATION) transitions to BLOCKED, then PREPARING on resume
    monkeypatch.setattr(
        campaign_mod,
        "load_native_result",
        lambda p, d: {
            "terminal_state": "BLOCKED_BEFORE_MUTATION",
            "failure": {"message": "lock contention", "operation": "apply", "evidence": None},
        },
    )
    doc_reset2 = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc_reset2["stages"] = stages
    doc_reset2 = state_mod.transition(doc_reset2, "AWAITING_APPLY", "reset")
    state_mod.persist_campaign(camp_dir, doc_reset2)

    assert campaign_mod.resume_campaign(workflow, repo, cfg, campaign=camp_dir) == 1
    doc_err3 = state_mod.load_campaign(camp_dir)
    assert doc_err3["state"] == "BLOCKED"
    assert doc_err3["stages"][0]["current_plan"] is None

    # Resuming non-retryable blocker transitions to PREPARING to prepare a fresh plan
    monkeypatch.setattr(campaign_mod, "_prepare_native", lambda wf, r, inp, rev, out, cfg, stg: MockArtifact(out))
    monkeypatch.setattr(campaign_mod, "_has_controlling_terminal", lambda: False)
    assert campaign_mod.resume_campaign(workflow, repo, cfg, campaign=camp_dir) == 0
    doc_prep = state_mod.load_campaign(camp_dir)
    assert doc_prep["state"] == "AWAITING_APPLY"
    assert doc_prep["stages"][0]["status"] == "READY_FOR_REVIEW"

    # 4. Retryable publication failure (COMMITTED_NOT_PUBLISHED) preserves current_plan
    doc_reset3 = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc_reset3["stages"] = stages
    doc_reset3 = state_mod.transition(doc_reset3, "AWAITING_APPLY", "reset")
    state_mod.persist_campaign(camp_dir, doc_reset3)
    monkeypatch.setattr(
        campaign_mod,
        "load_native_result",
        lambda p, d: {
            "terminal_state": "COMMITTED_NOT_PUBLISHED",
            "failure": {"message": "remote push rejected", "operation": "git push", "evidence": None},
            "published_commit": "c" * 40,
        },
    )
    assert campaign_mod.resume_campaign(workflow, repo, cfg, campaign=camp_dir) == 1
    doc_retry = state_mod.load_campaign(camp_dir)
    assert doc_retry["state"] == "BLOCKED"
    assert doc_retry["stages"][0]["current_plan"] == os.fspath(plan_dir)


def test_campaign_test_state_isolation_and_sentinel_untouched(campaign_modules, tmp_path, monkeypatch):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, fixture_state_root = campaign_modules

    # Create a sentinel operator state root that must NEVER be touched by tests
    sentinel_home = tmp_path / "sentinel_operator_home"
    sentinel_state = sentinel_home / ".local/state/nix-update"
    sentinel_state.mkdir(mode=0o700, parents=True)
    sentinel_index = sentinel_state / "campaign-index.json"
    sentinel_data = {
        "schema": "nix-update-promotion-campaign-index-v1",
        "version": 1,
        "entries": [
            {
                "campaign": "/sentinel/campaign-1",
                "input": "sentinel-input",
                "revision": "s" * 40,
                "selected": "/sentinel/composition",
                "state": "BLOCKED",
                "updated_at": "2026-09-06T00:00:00Z",
            }
        ],
    }
    sentinel_index.write_text(json.dumps(sentinel_data), encoding="utf-8")
    sentinel_index.chmod(0o600)
    sentinel_bytes_before = sentinel_index.read_bytes()
    sentinel_mtime_before = sentinel_index.stat().st_mtime_ns

    # Run campaign operations in fixture state
    repo, commit0 = _make_git_repo(tmp_path / "iso_repo")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo)

    class MockArtifact:
        def __init__(self, d):
            self.directory = d
            self.digest = "7" * 64
            self.document = {"status": "READY_FOR_REVIEW"}

    monkeypatch.setattr(campaign_mod, "_prepare_native", lambda wf, r, inp, rev, out, cfg, stg: MockArtifact(out))
    monkeypatch.setattr(campaign_mod, "_has_controlling_terminal", lambda: False)

    code = campaign_mod.run_campaign(workflow, repo, cfg, "inp", REVISION)
    assert code == 0

    # Test campaigns appear ONLY in fixture state root
    fixture_index = fixture_state_root / "campaign-index.json"
    assert fixture_index.exists()
    fixture_entries = json.loads(fixture_index.read_text(encoding="utf-8"))["entries"]
    assert len(fixture_entries) == 1
    assert fixture_entries[0]["input"] == "inp"
    assert fixture_entries[0]["campaign"].startswith(str(fixture_state_root))

    # Sentinel index is completely untouched
    assert sentinel_index.read_bytes() == sentinel_bytes_before
    assert sentinel_index.stat().st_mtime_ns == sentinel_mtime_before


def test_campaign_multi_topology_selection_isolation(campaign_modules, tmp_path):
    _, state_mod, _, _, _, _, _, state_root = campaign_modules

    repo_public = tmp_path / "composition-nd"
    repo_private = tmp_path / "composition-private-nd"
    repo_test = tmp_path / "pytest-repo"
    c_public = tmp_path / "camp-public"
    c_private = tmp_path / "camp-private"
    c_test = tmp_path / "camp-test"

    state_mod.update_index(state_root, campaign=c_public, selected=repo_public, input_name="apgr", revision="r1", state="COMPLETE")
    state_mod.update_index(state_root, campaign=c_private, selected=repo_private, input_name="apgr", revision="r1", state="BLOCKED")
    state_mod.update_index(state_root, campaign=c_test, selected=repo_test, input_name="inp", revision="r2", state="AWAITING_APPLY")

    # Querying for private composition selects ONLY the private campaign
    matches = state_mod.matching_campaigns(state_root, selected=repo_private)
    assert matches == [c_private.resolve()]

    matches_public = state_mod.matching_campaigns(state_root, selected=repo_public, input_name="apgr")
    assert matches_public == [c_public.resolve()]

    # Other topologies do not match
    assert state_mod.matching_campaigns(state_root, selected=repo_private, input_name="inp") == []


def test_tty_confirmation_production_reader_real_pty(campaign_modules):
    campaign_mod, _, _, _, _, _, _, _ = campaign_modules
    import pty, select

    pid, fd = pty.fork()
    if pid == 0:
        try:
            phrase = campaign_mod._tty_confirmation("Type confirmation phrase: ")
            os.write(1, f"CONFIRMED:{phrase}\n".encode("utf-8"))
        except Exception as exc:
            os.write(1, f"ERR:{type(exc).__name__}:{exc}\n".encode("utf-8"))
        os._exit(0)

    try:
        prompt = b""
        while b": " not in prompt:
            r, _, _ = select.select([fd], [], [], 3.0)
            assert r, "Timed out waiting for confirmation prompt"
            chunk = os.read(fd, 1024)
            if not chunk:
                break
            prompt += chunk
        assert b"Type confirmation phrase: " in prompt

        synthetic_phrase = "APPLY NIX-UPDATE PROMOTION synthetic-test-digest-12345"
        os.write(fd, f"{synthetic_phrase}\n".encode("utf-8"))

        out = b""
        while True:
            r, _, _ = select.select([fd], [], [], 3.0)
            if not r:
                break
            try:
                chunk = os.read(fd, 1024)
                if not chunk:
                    break
                out += chunk
            except OSError:
                break
        _, status = os.waitpid(pid, 0)
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
        assert f"CONFIRMED:{synthetic_phrase}".encode("utf-8") in out
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def test_tty_confirmation_stdin_redirection_and_isolation(campaign_modules):
    campaign_mod, _, _, _, _, _, _, _ = campaign_modules
    import pty, select

    pipe_r, pipe_w = os.pipe()
    os.write(pipe_w, b"SPOOFED_STDIN_CONFIRMATION\n")
    os.close(pipe_w)

    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.dup2(pipe_r, 0)
            os.close(pipe_r)
            phrase = campaign_mod._tty_confirmation("Confirmation: ")
            os.write(1, f"RECEIVED:{phrase}\n".encode("utf-8"))
        except Exception as exc:
            os.write(1, f"ERR:{type(exc).__name__}:{exc}\n".encode("utf-8"))
        os._exit(0)

    try:
        os.close(pipe_r)
        prompt = b""
        while b": " not in prompt:
            r, _, _ = select.select([fd], [], [], 3.0)
            assert r, "Timed out waiting for prompt"
            chunk = os.read(fd, 1024)
            if not chunk:
                break
            prompt += chunk

        synthetic_phrase = "SYNTHETIC_TTY_ANSWER_8888"
        os.write(fd, f"{synthetic_phrase}\n".encode("utf-8"))

        out = b""
        while True:
            r, _, _ = select.select([fd], [], [], 3.0)
            if not r:
                break
            try:
                chunk = os.read(fd, 1024)
                if not chunk:
                    break
                out += chunk
            except OSError:
                break
        _, status = os.waitpid(pid, 0)
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
        decoded = out.decode("utf-8", errors="replace")
        assert f"RECEIVED:{synthetic_phrase}" in decoded
        assert "SPOOFED_STDIN_CONFIRMATION" not in decoded
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def test_tty_confirmation_eof_raises_eoferror(campaign_modules):
    campaign_mod, _, _, _, _, _, _, _ = campaign_modules
    import pty, select

    pid, fd = pty.fork()
    if pid == 0:
        try:
            campaign_mod._tty_confirmation("Prompt: ")
            os.write(1, b"UNEXPECTED_SUCCESS\n")
        except EOFError:
            os.write(1, b"CAUGHT_EOF\n")
        except Exception as exc:
            os.write(1, f"OTHER_ERR:{type(exc).__name__}\n".encode("utf-8"))
        os._exit(0)

    try:
        prompt = b""
        while b": " not in prompt:
            r, _, _ = select.select([fd], [], [], 3.0)
            assert r, "Timed out waiting for prompt"
            chunk = os.read(fd, 1024)
            if not chunk:
                break
            prompt += chunk

        # Send EOF (\x04 / Ctrl-D)
        os.write(fd, b"\x04")

        out = b""
        while True:
            r, _, _ = select.select([fd], [], [], 3.0)
            if not r:
                break
            try:
                chunk = os.read(fd, 1024)
                if not chunk:
                    break
                out += chunk
            except OSError:
                break
        _, status = os.waitpid(pid, 0)
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
        assert b"CAUGHT_EOF" in out
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def test_tty_confirmation_no_controlling_terminal_raises_terminal_unavailable(campaign_modules, repo_root):
    campaign_mod, _, _, _, _, _, _, _ = campaign_modules
    child_code = f'''
import sys
sys.path.insert(0, "{repo_root / 'libexec'}")
from nix_darwin_local import promotion_campaign
try:
    promotion_campaign._tty_confirmation("Prompt: ")
    print("UNEXPECTED")
except promotion_campaign.TerminalUnavailableError:
    print("CAUGHT_TERMINAL_UNAVAILABLE")
except Exception as e:
    print(f"OTHER_ERR:{{type(e).__name__}}:{{e}}")
'''
    proc = subprocess.run(
        [sys.executable, "-c", child_code],
        env={**os.environ, "PYTHONPATH": str(repo_root / "libexec")},
        capture_output=True,
        text=True,
        start_new_session=True,
        timeout=5,
    )
    assert "CAUGHT_TERMINAL_UNAVAILABLE" in proc.stdout, f"Output: {proc.stdout}, stderr: {proc.stderr}"


def test_tty_confirmation_unrelated_errors_not_relabeled_as_no_tty(campaign_modules):
    campaign_mod, _, _, _, _, _, deployment, _ = campaign_modules
    import errno, io

    unsupported_err = deployment.ConfigError("promotion confirmation requires a controlling terminal")
    unsupported_err.__cause__ = io.UnsupportedOperation("File or stream is not seekable.")
    assert not campaign_mod._is_terminal_unavailable(unsupported_err)

    unicode_err = deployment.ConfigError("promotion confirmation requires a controlling terminal")
    unicode_err.__cause__ = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    assert not campaign_mod._is_terminal_unavailable(unicode_err)

    mismatch_err = deployment.ConfigError("promotion confirmation text did not match the reviewed plan digest")
    assert not campaign_mod._is_terminal_unavailable(mismatch_err)

    eof_err = deployment.ConfigError("promotion confirmation was not supplied")
    assert not campaign_mod._is_terminal_unavailable(eof_err)

    badf_err = OSError(errno.EBADF, "Bad file descriptor")
    assert not campaign_mod._is_terminal_unavailable(badf_err)

    enotty_err = OSError(errno.ENOTTY, "Inappropriate ioctl for device")
    assert campaign_mod._is_terminal_unavailable(enotty_err)

    avail_err = campaign_mod.TerminalUnavailableError("promotion confirmation requires a controlling terminal")
    assert campaign_mod._is_terminal_unavailable(avail_err)


def test_stale_tool_renewal_on_resume_appends_attempt_and_updates_digest(campaign_modules, repo_root, tmp_path, monkeypatch, capsys):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules
    import shutil
    qualification = importlib.import_module("nix_darwin_local.promotion_qualification")

    fixture_core = tmp_path / "fixture_core"
    (fixture_core / "bin").mkdir(parents=True)
    shutil.copy2(repo_root / "bin/nix-update", fixture_core / "bin/nix-update")
    shutil.copytree(
        repo_root / "libexec/nix_darwin_local",
        fixture_core / "libexec/nix_darwin_local",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    repo, commit0 = _make_git_repo(tmp_path / "repo")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(fixture_core)

    camp_dir = tmp_path / "camp_stale_tool"
    camp_dir.mkdir(mode=0o700)

    attempt1_dir = _make_stage_plan_dir(camp_dir, "composition-nd", 1)
    tool_a = qualification.promotion_tool_identity(fixture_core)

    plan1_digest = "1" * 64
    plan1_doc = {
        "status": "READY_FOR_REVIEW",
        "digest": plan1_digest,
        "tool": tool_a,
        "repository": {"commit": commit0},
        "input": "inp",
        "revision": REVISION,
        "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"},
    }
    (attempt1_dir / "plan.json").write_text(json.dumps(plan1_doc), encoding="utf-8")
    (attempt1_dir / "plan.md").write_text("plan 1", encoding="utf-8")

    stages = [
        {
            "name": "composition-nd",
            "kind": "composition",
            "repository": os.fspath(repo),
            "input": "inp",
            "revision": REVISION,
            "status": "READY_FOR_REVIEW",
            "current_plan": os.fspath(attempt1_dir),
            "attempts": [
                {
                    "number": 1,
                    "plan_path": os.fspath(attempt1_dir),
                    "plan_digest": plan1_digest,
                    "plan_status": "READY_FOR_REVIEW",
                    "result_path": None,
                    "result_digest": None,
                    "terminal_state": None,
                    "published_commit": None,
                    "failure": None,
                }
            ],
            "published_commit": None,
            "impact": None,
            "failure": None,
        }
    ]
    doc = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc["stages"] = stages
    doc = state_mod.transition(doc, "AWAITING_APPLY", "stage prepared under tool A")
    state_mod.persist_campaign(camp_dir, doc)
    campaign_mod._index(doc, camp_dir, state_root)

    bound_file = fixture_core / "libexec/nix_darwin_local/promotion_campaign.py"
    bound_file.write_text(bound_file.read_text(encoding="utf-8") + "\n# tool drift B\n", encoding="utf-8")
    tool_b = qualification.promotion_tool_identity(fixture_core)
    assert tool_b["digest"] != tool_a["digest"]

    plan2_digest = "2" * 64
    class MockArtifact2:
        def __init__(self, d):
            self.directory = d
            self.digest = plan2_digest
            self.document = {
                "status": "READY_FOR_REVIEW",
                "digest": plan2_digest,
                "tool": tool_b,
                "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"},
            }

    prepare_called = []
    def mock_prepare(wf, r, inp, rev, out, cfg, stg):
        prepare_called.append(out)
        return MockArtifact2(out)

    class MockArtifact1:
        def __init__(self, d):
            self.directory = d
            self.digest = plan1_digest
            self.document = plan1_doc

    monkeypatch.setattr(campaign_mod, "_prepare_native", mock_prepare)
    monkeypatch.setattr(campaign_mod, "load_plan", lambda p: MockArtifact2(p) if "attempt-2" in str(p) else MockArtifact1(p))
    monkeypatch.setattr(campaign_mod, "_has_controlling_terminal", lambda: False)

    code = campaign_mod.resume_campaign(workflow, repo, cfg, campaign=camp_dir)
    assert code == 0

    assert len(prepare_called) == 1
    assert "attempt-2" in os.fspath(prepare_called[0])

    assert (attempt1_dir / "plan.json").read_text(encoding="utf-8") == json.dumps(plan1_doc)

    doc_resumed = state_mod.load_campaign(camp_dir)
    stage_resumed = doc_resumed["stages"][0]
    assert len(stage_resumed["attempts"]) == 2
    assert stage_resumed["attempts"][0]["number"] == 1
    assert stage_resumed["attempts"][0]["plan_digest"] == plan1_digest
    assert stage_resumed["attempts"][1]["number"] == 2
    assert stage_resumed["attempts"][1]["plan_digest"] == plan2_digest
    assert stage_resumed["current_plan"] == os.fspath(prepare_called[0])

    out = capsys.readouterr().out
    assert f"APPLY NIX-UPDATE PROMOTION {plan2_digest}" in out
    assert f"APPLY NIX-UPDATE PROMOTION {plan1_digest}" not in out
    assert "plan tool identity drifted; refreshing native preparation" in out


def test_same_tool_reuse_without_reprepare(campaign_modules, tmp_path, monkeypatch, capsys):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules

    repo, commit0 = _make_git_repo(tmp_path / "repo")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo)

    camp_dir = tmp_path / "camp_same_tool"
    camp_dir.mkdir(mode=0o700)

    attempt1_dir = _make_stage_plan_dir(camp_dir, "composition-nd", 1)
    tool_a = {"digest": "a" * 64, "name": "tool", "version": "1"}

    plan1_digest = "1" * 64
    plan1_doc = {
        "status": "READY_FOR_REVIEW",
        "digest": plan1_digest,
        "tool": tool_a,
        "repository": {"commit": commit0},
        "input": "inp",
        "revision": REVISION,
        "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"},
    }
    (attempt1_dir / "plan.json").write_text(json.dumps(plan1_doc), encoding="utf-8")
    (attempt1_dir / "plan.md").write_text("plan 1", encoding="utf-8")

    stages = [
        {
            "name": "composition-nd",
            "kind": "composition",
            "repository": os.fspath(repo),
            "input": "inp",
            "revision": REVISION,
            "status": "READY_FOR_REVIEW",
            "current_plan": os.fspath(attempt1_dir),
            "attempts": [
                {
                    "number": 1,
                    "plan_path": os.fspath(attempt1_dir),
                    "plan_digest": plan1_digest,
                    "plan_status": "READY_FOR_REVIEW",
                    "result_path": None,
                    "result_digest": None,
                    "terminal_state": None,
                    "published_commit": None,
                    "failure": None,
                }
            ],
            "published_commit": None,
            "impact": None,
            "failure": None,
        }
    ]
    doc = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc["stages"] = stages
    doc = state_mod.transition(doc, "AWAITING_APPLY", "stage prepared under tool A")
    state_mod.persist_campaign(camp_dir, doc)
    campaign_mod._index(doc, camp_dir, state_root)

    monkeypatch.setattr(campaign_mod, "_promotion_tool_identity", lambda core_dir: tool_a)

    class MockArtifact1:
        def __init__(self, d):
            self.directory = d
            self.digest = plan1_digest
            self.document = plan1_doc

    monkeypatch.setattr(campaign_mod, "load_plan", lambda p: MockArtifact1(p))

    prepare_called = []
    monkeypatch.setattr(campaign_mod, "_prepare_native", lambda *args, **kwargs: prepare_called.append(True))
    monkeypatch.setattr(campaign_mod, "_has_controlling_terminal", lambda: False)

    code = campaign_mod.resume_campaign(workflow, repo, cfg, campaign=camp_dir)
    assert code == 0
    assert len(prepare_called) == 0

    doc_resumed = state_mod.load_campaign(camp_dir)
    assert len(doc_resumed["stages"][0]["attempts"]) == 1
    assert doc_resumed["stages"][0]["current_plan"] == os.fspath(attempt1_dir)
    out = capsys.readouterr().out
    assert f"APPLY NIX-UPDATE PROMOTION {plan1_digest}" in out


def test_consumed_authority_preserves_native_recovery_despite_tool_change(campaign_modules, tmp_path, monkeypatch):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules

    repo, commit0 = _make_git_repo(tmp_path / "repo")
    workflow = deployment.Workflow(repo)

    camp_dir = tmp_path / "camp_consumed_tool_change"
    camp_dir.mkdir(mode=0o700)

    attempt1_dir = _make_stage_plan_dir(camp_dir, "composition-nd", 1)
    tool_a = {"digest": "a" * 64}
    tool_b = {"digest": "b" * 64}

    plan1_digest = "1" * 64
    plan1_doc = {
        "status": "READY_FOR_REVIEW",
        "digest": plan1_digest,
        "tool": tool_a,
        "repository": {"commit": commit0},
        "input": "inp",
        "revision": REVISION,
    }
    (attempt1_dir / "plan.json").write_text(json.dumps(plan1_doc), encoding="utf-8")

    stages = [
        {
            "name": "composition-nd",
            "kind": "composition",
            "repository": os.fspath(repo),
            "input": "inp",
            "revision": REVISION,
            "status": "READY_FOR_REVIEW",
            "current_plan": os.fspath(attempt1_dir),
            "attempts": [
                {
                    "number": 1,
                    "plan_path": os.fspath(attempt1_dir),
                    "plan_digest": plan1_digest,
                    "plan_status": "READY_FOR_REVIEW",
                    "result_path": None,
                    "result_digest": None,
                    "terminal_state": None,
                    "published_commit": None,
                    "failure": None,
                }
            ],
            "published_commit": None,
            "impact": None,
            "failure": None,
        }
    ]
    doc = state_mod.new_campaign(repo, "inp", REVISION, stages)
    doc["stages"] = stages
    state_mod.persist_campaign(camp_dir, doc)

    consumption_file = state_root / "promotion-consumed" / f"{plan1_digest}.json"
    consumption_file.parent.mkdir(parents=True, exist_ok=True)
    consumption_file.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(campaign_mod, "_promotion_tool_identity", lambda core_dir: tool_b)
    assert not campaign_mod._stage_plan_tool_is_stale(workflow, doc["stages"][0])


def test_result_json_precedence_preserves_completed_attempt_despite_tool_change(campaign_modules, tmp_path, monkeypatch):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules

    repo, commit0 = _make_git_repo(tmp_path / "repo")
    workflow = deployment.Workflow(repo)

    camp_dir = tmp_path / "camp_result_json"
    camp_dir.mkdir(mode=0o700)
    attempt1_dir = _make_stage_plan_dir(camp_dir, "composition-nd", 1)
    (attempt1_dir / "plan.json").write_text(json.dumps({"status": "READY_FOR_REVIEW", "tool": {"digest": "a" * 64}}), encoding="utf-8")
    (attempt1_dir / "result.json").write_text("{}", encoding="utf-8")

    stages = [
        {
            "name": "composition-nd",
            "kind": "composition",
            "repository": os.fspath(repo),
            "input": "inp",
            "revision": REVISION,
            "status": "READY_FOR_REVIEW",
            "current_plan": os.fspath(attempt1_dir),
            "attempts": [{"number": 1, "plan_path": os.fspath(attempt1_dir)}],
        }
    ]
    doc = state_mod.new_campaign(repo, "inp", REVISION, stages)

    monkeypatch.setattr(campaign_mod, "_promotion_tool_identity", lambda core_dir: {"digest": "b" * 64})
    assert not campaign_mod._stage_plan_tool_is_stale(workflow, doc["stages"][0])


def test_retryable_publication_state_preserves_native_recovery_despite_tool_change(campaign_modules, tmp_path, monkeypatch):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules

    repo, commit0 = _make_git_repo(tmp_path / "repo")
    workflow = deployment.Workflow(repo)

    camp_dir = tmp_path / "camp_retryable"
    camp_dir.mkdir(mode=0o700)
    attempt1_dir = _make_stage_plan_dir(camp_dir, "composition-nd", 1)
    (attempt1_dir / "plan.json").write_text(json.dumps({"status": "READY_FOR_REVIEW", "tool": {"digest": "a" * 64}}), encoding="utf-8")

    stages = [
        {
            "name": "composition-nd",
            "kind": "composition",
            "repository": os.fspath(repo),
            "input": "inp",
            "revision": REVISION,
            "status": "BLOCKED",
            "current_plan": os.fspath(attempt1_dir),
            "attempts": [{"number": 1, "plan_path": os.fspath(attempt1_dir), "terminal_state": "COMMITTED_NOT_PUBLISHED"}],
        }
    ]
    doc = state_mod.new_campaign(repo, "inp", REVISION, stages)

    monkeypatch.setattr(campaign_mod, "_promotion_tool_identity", lambda core_dir: {"digest": "b" * 64})
    assert not campaign_mod._stage_plan_tool_is_stale(workflow, doc["stages"][0])


def test_downstream_stage_preservation_and_refresh_pending_only(campaign_modules, tmp_path, monkeypatch, capsys):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules

    repo1, commit1 = _make_git_repo(tmp_path / "repo_pub")
    repo2, commit2 = _make_git_repo(tmp_path / "repo_priv")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo1)

    camp_dir = tmp_path / "camp_downstream"
    camp_dir.mkdir(mode=0o700)
    plan1_dir = _make_stage_plan_dir(camp_dir, "stage-1", 1)
    plan2_dir = _make_stage_plan_dir(camp_dir, "stage-2", 1)

    tool_a = {"digest": "a" * 64}
    tool_b = {"digest": "b" * 64}

    (plan1_dir / "plan.json").write_text(json.dumps({"status": "READY_FOR_REVIEW", "tool": tool_a}), encoding="utf-8")
    (plan2_dir / "plan.json").write_text(json.dumps({"status": "READY_FOR_REVIEW", "tool": tool_a, "input": "stage-1", "revision": commit1, "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"}}), encoding="utf-8")

    stages = [
        {
            "name": "stage-1",
            "kind": "composition",
            "repository": os.fspath(repo1),
            "input": "inp",
            "revision": REVISION,
            "status": "PUBLISHED",
            "current_plan": None,
            "published_commit": commit1,
            "impact": None,
            "failure": None,
            "attempts": [
                {
                    "number": 1,
                    "plan_path": os.fspath(plan1_dir),
                    "plan_digest": "1" * 64,
                    "plan_status": "READY_FOR_REVIEW",
                    "result_path": None,
                    "result_digest": None,
                    "terminal_state": "PROMOTION_PUBLISHED_AWAITING_ACTIVATION",
                    "published_commit": commit1,
                    "failure": None,
                }
            ],
        },
        {
            "name": "stage-2",
            "kind": "composition",
            "repository": os.fspath(repo2),
            "input": "stage-1",
            "revision": commit1,
            "status": "READY_FOR_REVIEW",
            "current_plan": os.fspath(plan2_dir),
            "published_commit": None,
            "impact": None,
            "failure": None,
            "attempts": [
                {
                    "number": 1,
                    "plan_path": os.fspath(plan2_dir),
                    "plan_digest": "2" * 64,
                    "plan_status": "READY_FOR_REVIEW",
                    "result_path": None,
                    "result_digest": None,
                    "terminal_state": None,
                    "published_commit": None,
                    "failure": None,
                }
            ],
        },
    ]
    doc = state_mod.new_campaign(repo2, "inp", REVISION, stages)
    doc["stages"] = stages
    doc["current_stage"] = 1
    doc = state_mod.transition(doc, "AWAITING_APPLY", "stage 2 awaits confirmation")
    state_mod.persist_campaign(camp_dir, doc)

    monkeypatch.setattr(campaign_mod, "_promotion_tool_identity", lambda core_dir: tool_b)

    stage2_new_digest = "3" * 64
    prepare_called = []
    class MockArtifactStage2:
        def __init__(self, d):
            self.directory = d
            self.digest = stage2_new_digest
            self.document = {"status": "READY_FOR_REVIEW", "digest": stage2_new_digest, "tool": tool_b, "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"}}

    class MockArtifactStage2Old:
        def __init__(self, d):
            self.directory = d
            self.digest = "2" * 64
            self.document = {"status": "READY_FOR_REVIEW", "digest": "2" * 64, "tool": tool_a, "input": "stage-1", "revision": commit1, "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"}}

    monkeypatch.setattr(campaign_mod, "_prepare_native", lambda wf, r, inp, rev, out, cfg, stg: prepare_called.append(out) or MockArtifactStage2(out))
    monkeypatch.setattr(campaign_mod, "load_plan", lambda p: MockArtifactStage2(p) if "attempt-2" in str(p) else MockArtifactStage2Old(p))
    monkeypatch.setattr(campaign_mod, "_has_controlling_terminal", lambda: False)

    code = campaign_mod.resume_campaign(workflow, repo2, cfg, campaign=camp_dir)
    assert code == 0

    doc_after = state_mod.load_campaign(camp_dir)
    assert doc_after["stages"][0]["status"] == "PUBLISHED"
    assert doc_after["stages"][0]["published_commit"] == commit1
    assert len(doc_after["stages"][0]["attempts"]) == 1

    assert len(doc_after["stages"][1]["attempts"]) == 2
    assert doc_after["stages"][1]["attempts"][1]["plan_digest"] == stage2_new_digest


def test_fresh_prepare_failure_blocks_before_mutation(campaign_modules, tmp_path, monkeypatch, capsys):
    campaign_mod, state_mod, _, _, _, config_mod, deployment, state_root = campaign_modules

    repo, commit0 = _make_git_repo(tmp_path / "repo")
    cfg = config_mod.ControlConfig()
    workflow = deployment.Workflow(repo)

    camp_dir = tmp_path / "camp_prepare_fail"
    camp_dir.mkdir(mode=0o700)
    attempt1_dir = _make_stage_plan_dir(camp_dir, "composition-nd", 1)
    tool_a = {"digest": "a" * 64}
    tool_b = {"digest": "b" * 64}

    (attempt1_dir / "plan.json").write_text(json.dumps({"status": "READY_FOR_REVIEW", "tool": tool_a, "digest": "1" * 64}), encoding="utf-8")

    stages = [
        {
            "name": "composition-nd",
            "kind": "composition",
            "repository": os.fspath(repo),
            "input": "inp",
            "revision": REVISION,
            "status": "READY_FOR_REVIEW",
            "current_plan": os.fspath(attempt1_dir),
            "published_commit": None,
            "impact": None,
            "failure": None,
            "attempts": [
                {
                    "number": 1,
                    "plan_path": os.fspath(attempt1_dir),
                    "plan_digest": "1" * 64,
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
    state_mod.persist_campaign(camp_dir, doc)

    monkeypatch.setattr(campaign_mod, "_promotion_tool_identity", lambda core_dir: tool_b)

    class MockArtifact1:
        def __init__(self, d):
            self.directory = d
            self.digest = "1" * 64
            self.document = {"status": "READY_FOR_REVIEW", "tool": tool_a, "digest": "1" * 64}

    monkeypatch.setattr(campaign_mod, "load_plan", lambda p: MockArtifact1(p))

    def failing_prepare(*args, **kwargs):
        raise deployment.ConfigError("fresh native qualification failure")

    monkeypatch.setattr(campaign_mod, "_prepare_native", failing_prepare)
    apply_called = []
    monkeypatch.setattr(campaign_mod, "native_apply_promotion", lambda *args, **kwargs: apply_called.append(True))

    code = campaign_mod.resume_campaign(workflow, repo, cfg, campaign=camp_dir)
    assert code == 1
    assert len(apply_called) == 0

    doc_after = state_mod.load_campaign(camp_dir)
    assert doc_after["state"] == "BLOCKED"
    assert "fresh native qualification failure" in doc_after["blocker"]["failure"]["message"]
