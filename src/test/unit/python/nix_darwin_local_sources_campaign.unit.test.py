# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_campaign.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_campaign_state.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_campaign_result.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/source_snapshot_status.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/config.py
from __future__ import annotations

import copy
import importlib
from pathlib import Path
import subprocess

import pytest


REVISION = "a" * 40
LOCAL_COMMIT = "b" * 40
LOCAL_COMMIT_2 = "c" * 40


@pytest.fixture
def campaign_modules(repo_root: Path, monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    campaign = importlib.import_module("nix_darwin_local.promotion_campaign")
    state = importlib.import_module("nix_darwin_local.promotion_campaign_state")
    result = importlib.import_module("nix_darwin_local.promotion_campaign_result")
    deployment = importlib.import_module("nix_darwin_local.deployment_workflow")
    config = importlib.import_module("nix_darwin_local.config")
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(campaign, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(deployment, "_private_state_root", lambda: state_root)
    return campaign, state, result, deployment, config, state_root


def _v2_result(result_mod, digest: str, *, terminal: str = "LOCAL_PROMOTION_COMMITTED", commit: str = LOCAL_COMMIT):
    return {
        "schema": result_mod.RESULT_SCHEMA,
        "plan_digest": digest,
        "no_activation": True,
        "terminal_state": terminal,
        "local_commit": commit,
        "published_commit": None,
        "publication_state": "unavailable",
        "activation_state": "not_performed",
        "started_at": "2026-09-08T00:00:00Z",
        "finished_at": "2026-09-08T00:00:01Z",
        "failure": None,
        "recovery": None,
        "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"},
        "commands": [],
        "result_path": "/private/result.json",
    }


def test_v2_campaign_roundtrip_and_legacy_campaign_readback(campaign_modules, tmp_path):
    _, state, _, deployment, _, _ = campaign_modules
    selected = tmp_path / "selected"
    selected.mkdir()
    stages = [{"name": "source", "kind": "core", "repository": selected, "input": "core", "revision": REVISION}]

    document = state.new_campaign(selected, "core", REVISION, stages)
    assert document["schema"] == "nix-update-promotion-campaign-v2"
    assert document["version"] == 2
    assert document["stages"][0]["local_commit"] is None
    assert document["stages"][0]["activation_state"] == "not_performed"

    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir(mode=0o700)
    state.persist_campaign(campaign_dir, document)
    loaded = state.load_campaign(campaign_dir)
    assert loaded == document

    legacy = copy.deepcopy(document)
    legacy["schema"] = state.LEGACY_CAMPAIGN_SCHEMA
    legacy["version"] = state.LEGACY_CAMPAIGN_VERSION
    legacy_stage = legacy["stages"][0]
    for key in ("local_commit", "publication_state", "activation_state"):
        del legacy_stage[key]
    legacy_dir = tmp_path / "legacy-campaign"
    legacy_dir.mkdir(mode=0o700)
    state.persist_campaign(legacy_dir, state.seal(legacy))
    assert state.load_campaign(legacy_dir)["schema"] == state.LEGACY_CAMPAIGN_SCHEMA

    legacy_local = copy.deepcopy(legacy)
    legacy_local["state"] = "LOCAL_COMPLETE_AWAITING_ACTIVATION"
    legacy_local["last_outcome"] = legacy_local["state"]
    legacy_local = state.seal(legacy_local)
    with pytest.raises(deployment.ConfigError, match="state or last outcome is invalid"):
        state.validate_campaign(legacy_local, raw=None, campaign=None)


def test_v2_result_roundtrip_and_legacy_result_readback(campaign_modules, tmp_path):
    _, _, result, deployment, _, _ = campaign_modules
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir(mode=0o700)
    digest = "d" * 64
    v2 = _v2_result(result, digest)
    result_path = plan_dir / "result.json"
    result_path.write_bytes(deployment.canonical_bytes(v2))
    result_path.chmod(0o600)
    loaded = result.load_native_result(plan_dir, digest)
    assert loaded["status"] == "LOCAL_COMMITTED"
    assert loaded["local_commit"] == LOCAL_COMMIT
    assert loaded["publication_state"] == "unavailable"
    assert loaded["activation_state"] == "not_performed"

    legacy = {
        "schema": result.LEGACY_RESULT_SCHEMA,
        "plan_digest": digest,
        "no_activation": True,
        "terminal_state": "PROMOTION_PUBLISHED_AWAITING_ACTIVATION",
        "published_commit": LOCAL_COMMIT,
        "failure": None,
        "commands": [],
        "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"},
    }
    result_path.write_bytes(deployment.canonical_bytes(legacy))
    assert result.load_native_result(plan_dir, digest)["status"] == "PUBLISHED"

    legacy_local = dict(legacy)
    legacy_local["terminal_state"] = "LOCAL_PROMOTION_COMMITTED"
    with pytest.raises(deployment.ConfigError, match="terminal state is invalid"):
        result.validate_native_result(legacy_local, digest)


def test_campaign_uses_local_commit_for_downstream_and_completion(campaign_modules, tmp_path, monkeypatch):
    campaign, state, _, deployment, config, state_root = campaign_modules
    selected = tmp_path / "selected"
    selected.mkdir()
    lower = tmp_path / "lower"
    lower.mkdir()
    cfg = config.ControlConfig(sources=(object(),))
    workflow = deployment.Workflow(selected)
    stages = [
        {"name": "source", "kind": "core", "repository": selected, "input": "core", "revision": REVISION},
        {"name": "lower", "kind": "composition", "repository": lower, "input": "core", "revision": None},
    ]
    monkeypatch.setattr(campaign, "resolve_campaign_stages", lambda *args, **kwargs: stages)

    class Artifact:
        def __init__(self, directory):
            self.directory = directory
            self.digest = "e" * 64
            self.document = {"status": "READY_FOR_REVIEW"}

    monkeypatch.setattr(campaign, "_prepare_native", lambda *args: Artifact(args[4]))
    monkeypatch.setattr(campaign, "load_plan", lambda path: Artifact(path))
    monkeypatch.setattr(campaign, "_has_controlling_terminal", lambda: True)
    calls = []

    def apply(*args, **kwargs):
        calls.append(1)
        return 0

    monkeypatch.setattr(campaign, "native_apply_promotion", apply)

    def load_result(plan, digest):
        commit = LOCAL_COMMIT if len(calls) == 1 else LOCAL_COMMIT_2
        return {
            "terminal_state": "LOCAL_PROMOTION_COMMITTED",
            "local_commit": commit,
            "published_commit": None,
            "publication_state": "unavailable",
            "activation_state": "not_performed",
            "status": "LOCAL_COMMITTED",
            "failure": None,
            "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"},
        }

    monkeypatch.setattr(campaign, "load_native_result", load_result)
    assert campaign.run_campaign(workflow, selected, cfg, "core", REVISION) == 0
    assert workflow.sources == cfg.sources
    document = state.load_campaign(state.matching_campaigns(state_root, selected=selected, input_name="core", revision=REVISION)[0])
    assert [stage["status"] for stage in document["stages"]] == ["LOCAL_COMMITTED", "LOCAL_COMMITTED"]
    assert document["stages"][0]["local_commit"] == LOCAL_COMMIT
    assert document["stages"][1]["revision"] == LOCAL_COMMIT
    assert document["stages"][1]["local_commit"] == LOCAL_COMMIT_2
    assert all(stage["published_commit"] is None for stage in document["stages"])
    assert document["state"] == "LOCAL_COMPLETE_AWAITING_ACTIVATION"


def test_already_selected_local_receipt_uses_local_commit(campaign_modules, tmp_path, monkeypatch):
    campaign, state, _, deployment, config, _ = campaign_modules
    selected = tmp_path / "selected"
    selected.mkdir()
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir(mode=0o700)
    cfg = config.ControlConfig(sources=(object(),))
    workflow = deployment.Workflow(selected, sources=cfg.sources)
    document = state.new_campaign(
        selected,
        "core",
        REVISION,
        [{"name": "source", "kind": "core", "repository": selected, "input": "core", "revision": REVISION}],
    )

    class Artifact:
        directory = tmp_path / "plan"
        digest = "1" * 64
        document = {
            "status": "ALREADY_SELECTED",
            "source_authority": {"commit": LOCAL_COMMIT},
            "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"},
        }

    monkeypatch.setattr(campaign, "_prepare_native", lambda *args: Artifact())
    updated = campaign._prepare_stage(workflow, selected, cfg, campaign_dir, document, document["stages"][0])
    stage = updated["stages"][0]
    assert stage["status"] == "LOCAL_COMMITTED"
    assert stage["local_commit"] == LOCAL_COMMIT
    assert stage["published_commit"] is None
    assert stage["publication_state"] == "unavailable"


def test_local_commit_created_is_retained_and_not_reapplied(campaign_modules, tmp_path, monkeypatch):
    campaign, state, _, deployment, config, state_root = campaign_modules
    selected = tmp_path / "selected"
    selected.mkdir()
    cfg = config.ControlConfig(sources=(object(),))
    workflow = deployment.Workflow(selected)
    calls = []

    class Artifact:
        def __init__(self, directory):
            self.directory = directory
            self.digest = "f" * 64
            self.document = {"status": "READY_FOR_REVIEW"}

    monkeypatch.setattr(campaign, "_prepare_native", lambda *args: Artifact(args[4]))
    monkeypatch.setattr(campaign, "load_plan", lambda path: Artifact(path))
    monkeypatch.setattr(campaign, "_has_controlling_terminal", lambda: True)
    monkeypatch.setattr(campaign, "native_apply_promotion", lambda *args, **kwargs: calls.append(1) or 1)
    monkeypatch.setattr(
        campaign,
        "load_native_result",
        lambda *args: {
            "terminal_state": "LOCAL_COMMIT_CREATED",
            "local_commit": LOCAL_COMMIT,
            "published_commit": None,
            "publication_state": "pending",
            "activation_state": "not_performed",
            "status": "BLOCKED",
            "failure": {"message": "publication unavailable", "operation": "promotion publication", "evidence": None},
            "impact": None,
        },
    )

    assert campaign.run_campaign(workflow, selected, cfg, "core", REVISION) == 1
    campaign_dir = state.matching_campaigns(state_root, selected=selected, input_name="core", revision=REVISION)[0]
    document = state.load_campaign(campaign_dir)
    assert document["state"] == "BLOCKED"
    assert document["stages"][0]["local_commit"] == LOCAL_COMMIT
    assert document["stages"][0]["current_plan"] is None
    assert len(document["stages"][0]["attempts"]) == 1
    assert document["stages"][0]["attempts"][0]["local_commit"] == LOCAL_COMMIT
    before_resume = copy.deepcopy(document)
    assert campaign.resume_campaign(workflow, selected, cfg, campaign=campaign_dir) == 1
    assert len(calls) == 1
    after_resume = state.load_campaign(campaign_dir)
    assert after_resume == before_resume
    assert after_resume["history"] == before_resume["history"]


def test_campaign_reconciles_local_result_without_reapplying(
    campaign_modules, tmp_path, monkeypatch
):
    """An interrupted apply adopts its durable local result exactly once."""
    campaign, state, result, deployment, config, state_root = campaign_modules
    selected = tmp_path / "selected"
    selected.mkdir(mode=0o700)
    cfg = config.ControlConfig(sources=(object(),))
    workflow = deployment.Workflow(selected)
    digest = "9" * 64
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir(mode=0o700)
    result_path = plan_dir / "result.json"
    result_path.write_bytes(deployment.canonical_bytes(_v2_result(result, digest)))
    result_path.chmod(0o600)

    class Artifact:
        def __init__(self, directory):
            self.directory = directory
            self.digest = digest
            self.document = {
                "status": "READY_FOR_REVIEW",
                "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"},
            }

    monkeypatch.setattr(campaign, "load_plan", lambda path: Artifact(path))

    def fail_reapply(*_args, **_kwargs):
        raise AssertionError("a retained result must be reconciled without native apply")

    monkeypatch.setattr(campaign, "native_apply_promotion", fail_reapply)
    document = state.new_campaign(
        selected,
        "core",
        REVISION,
        [{"name": "source", "kind": "core", "repository": selected, "input": "core", "revision": REVISION}],
    )
    stage = state.append_attempt(
        document["stages"][0],
        {"path": str(plan_dir), "digest": digest, "status": "READY_FOR_REVIEW"},
    )
    stage["status"] = "APPLYING"
    stage["current_plan"] = str(plan_dir)
    document["stages"] = [stage]
    document = state.transition(document, "AWAITING_APPLY", "native apply interrupted after result write")
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir(mode=0o700)
    state.persist_campaign(campaign_dir, document)

    assert campaign.resume_campaign(workflow, selected, cfg, campaign=campaign_dir) == 0
    loaded = state.load_campaign(campaign_dir)
    loaded_stage = loaded["stages"][0]
    assert loaded["state"] == "LOCAL_COMPLETE_AWAITING_ACTIVATION"
    assert loaded_stage["status"] == "LOCAL_COMMITTED"
    assert loaded_stage["local_commit"] == LOCAL_COMMIT
    assert loaded_stage["published_commit"] is None
    assert loaded_stage["publication_state"] == "unavailable"
    assert loaded_stage["activation_state"] == "not_performed"
    assert loaded_stage["current_plan"] is None
    assert loaded_stage["attempts"][0]["terminal_state"] == "LOCAL_PROMOTION_COMMITTED"
    assert loaded_stage["attempts"][0]["local_commit"] == LOCAL_COMMIT
    assert loaded_stage["attempts"][0]["published_commit"] is None
    assert loaded_stage["attempts"][0]["publication_state"] == "unavailable"
    assert loaded_stage["attempts"][0]["result_path"] == str(result_path)
    assert not state_root.joinpath("unexpected-reapply").exists()


def _init_git_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    (path / "README.md").write_text("# Test\n")
    (path / "flake.nix").write_text("{ outputs = { self }: {}; }\n")
    (path / "flake.lock").write_text('{"nodes":{"root":{}},"root":"root","version":7}\n')
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, check=True, stdout=subprocess.PIPE, text=True)
    return res.stdout.strip()


def _make_commit(path: Path, msg: str = "update") -> str:
    readme = path / "README.md"
    readme.write_text(readme.read_text() + f"{msg}\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, check=True, stdout=subprocess.PIPE, text=True)
    return res.stdout.strip()


def test_campaign_completion_and_snapshot_adoption_interaction(
    campaign_modules, tmp_path, monkeypatch, capsys
):
    campaign, state, _, deployment, config_mod, state_root = campaign_modules
    from nix_darwin_local import source_snapshot_status

    home = tmp_path / "home"
    home.mkdir()

    core_repo = home / "core"
    public_repo = home / "public_comp"
    private_repo = home / "private_comp"
    dep_repo = home / "dep"

    rev_core_init = _init_git_repo(core_repo)
    rev_public_init = _init_git_repo(public_repo)
    rev_private_init = _init_git_repo(private_repo)
    rev_dep = _init_git_repo(dep_repo)

    config_path = home / "nix-darwin.toml"

    def write_config(rev_c: str, rev_pub: str, rev_priv: str):
        config_path.write_text(
            f"""
[flake]
active = "{private_repo}"

[sources.core]
checkout = "{core_repo}"
repository = "ssh://git@example/core.git"
release_ref = "refs/heads/main"

[sources.public-comp]
checkout = "{public_repo}"
repository = "ssh://git@example/public-comp.git"
release_ref = "refs/heads/main"

[sources.private-comp]
checkout = "{private_repo}"
repository = "ssh://git@example/private-comp.git"
release_ref = "refs/heads/main"

[sources.dep]
checkout = "{dep_repo}"
repository = "ssh://git@example/dep.git"
release_ref = "refs/heads/main"

[snapshot.sources]
core = "{rev_c}"
public-comp = "{rev_pub}"
private-comp = "{rev_priv}"
dep = "{rev_dep}"
"""
        )

    write_config(rev_core_init, rev_public_init, rev_private_init)

    # 1. Verify initial snapshot status reports complete match across all 4 sources
    code = source_snapshot_status.show_snapshot_status(
        core_repo, None, config_path, {}, home
    )
    assert code == 0
    captured = capsys.readouterr().out
    assert "configured: yes" in captured
    assert "complete: yes (4 of 4 sources configured)" in captured
    assert f"core: expected={rev_core_init}, head={rev_core_init} (match)" in captured
    assert f"public-comp: expected={rev_public_init}, head={rev_public_init} (match)" in captured
    assert f"private-comp: expected={rev_private_init}, head={rev_private_init} (match)" in captured
    assert f"dep: expected={rev_dep}, head={rev_dep} (match)" in captured

    # 2. Advance core repository commit
    rev_core_new = _make_commit(core_repo, "core feature commit")

    # Load control config
    loaded_cfg = config_mod.load_control_config(config_path, operator_home=home)
    workflow = deployment.Workflow(private_repo, sources=loaded_cfg.sources)

    # Set up multi-stage campaign: core -> public-comp -> private-comp
    stages = [
        {"name": "public", "kind": "composition", "repository": public_repo, "input": "core", "revision": rev_core_new},
        {"name": "private", "kind": "composition", "repository": private_repo, "input": "public-comp", "revision": None},
    ]
    monkeypatch.setattr(campaign, "resolve_campaign_stages", lambda *args, **kwargs: stages)

    class Artifact:
        def __init__(self, directory):
            self.directory = directory
            self.digest = "c" * 64
            self.document = {"status": "READY_FOR_REVIEW"}

    monkeypatch.setattr(campaign, "_prepare_native", lambda *args: Artifact(args[4]))
    monkeypatch.setattr(campaign, "load_plan", lambda path: Artifact(path))
    monkeypatch.setattr(campaign, "_has_controlling_terminal", lambda: True)

    apply_count = 0
    promoted_commits: list[str] = []

    def mock_apply(*args, **kwargs):
        nonlocal apply_count
        apply_count += 1
        if apply_count == 1:
            c = _make_commit(public_repo, "promotion commit in public-comp")
        else:
            c = _make_commit(private_repo, "promotion commit in private-comp")
        promoted_commits.append(c)
        return 0

    monkeypatch.setattr(campaign, "native_apply_promotion", mock_apply)

    def mock_load_result(plan, digest):
        current_commit = promoted_commits[-1]
        return {
            "terminal_state": "LOCAL_PROMOTION_COMMITTED",
            "local_commit": current_commit,
            "published_commit": None,
            "publication_state": "unavailable",
            "activation_state": "not_performed",
            "status": "LOCAL_COMMITTED",
            "failure": None,
            "impact": {"classification": "HOST_DERIVATIONS_IDENTICAL"},
        }

    monkeypatch.setattr(campaign, "load_native_result", mock_load_result)

    # Execute the multi-stage campaign to completion
    res = campaign.run_campaign(workflow, private_repo, loaded_cfg, "core", rev_core_new)
    assert res == 0
    assert len(promoted_commits) == 2
    rev_public_new, rev_private_new = promoted_commits

    # Campaign reached terminal LOCAL_COMPLETE_AWAITING_ACTIVATION
    campaign_file = state.matching_campaigns(
        state_root, selected=private_repo, input_name="core", revision=rev_core_new
    )[0]
    campaign_doc = state.load_campaign(campaign_file)
    assert campaign_doc["state"] == "LOCAL_COMPLETE_AWAITING_ACTIVATION"
    assert [s["status"] for s in campaign_doc["stages"]] == ["LOCAL_COMMITTED", "LOCAL_COMMITTED"]
    assert campaign_doc["stages"][0]["revision"] == rev_core_new
    assert campaign_doc["stages"][0]["local_commit"] == rev_public_new
    assert campaign_doc["stages"][1]["revision"] == rev_public_new
    assert campaign_doc["stages"][1]["local_commit"] == rev_private_new
    rendered = state.render_status(campaign_doc, campaign_file)
    assert (
        "guidance: local promotion commits are qualified; publish explicitly if desired, "
        "adopt into '[snapshot.sources]' (or pass '--source-revision'), then run ordinary 'nix-update' to activate"
    ) in rendered

    # 3. Verify show_snapshot_status reflects mismatch on all promoted sources, while unaffected source (dep) remains match
    code = source_snapshot_status.show_snapshot_status(
        core_repo, None, config_path, {}, home
    )
    assert code == 0
    captured = capsys.readouterr().out
    assert f"core: expected={rev_core_init}, head={rev_core_new} (mismatch)" in captured
    assert f"public-comp: expected={rev_public_init}, head={rev_public_new} (mismatch)" in captured
    assert f"private-comp: expected={rev_private_init}, head={rev_private_new} (mismatch)" in captured
    assert f"dep: expected={rev_dep}, head={rev_dep} (match)" in captured

    # 4. Operator adopts promoted commits into [snapshot.sources]
    write_config(rev_core_new, rev_public_new, rev_private_new)

    # 5. Verify snapshot status is now fully reconciled
    code = source_snapshot_status.show_snapshot_status(
        core_repo, None, config_path, {}, home
    )
    assert code == 0
    captured = capsys.readouterr().out
    assert f"core: expected={rev_core_new}, head={rev_core_new} (match)" in captured
    assert f"public-comp: expected={rev_public_new}, head={rev_public_new} (match)" in captured
    assert f"private-comp: expected={rev_private_new}, head={rev_private_new} (match)" in captured
    assert f"dep: expected={rev_dep}, head={rev_dep} (match)" in captured

    # 6. Verify deployment resolution consumes the exact snapshot pins without requiring composition-lock convergence
    reconciled_cfg = config_mod.load_control_config(config_path, operator_home=home)
    effective = config_mod.resolve_effective_source_revisions(reconciled_cfg, {})
    assert effective["core"] == rev_core_new
    assert effective["public-comp"] == rev_public_new
    assert effective["private-comp"] == rev_private_new
    assert effective["dep"] == rev_dep
