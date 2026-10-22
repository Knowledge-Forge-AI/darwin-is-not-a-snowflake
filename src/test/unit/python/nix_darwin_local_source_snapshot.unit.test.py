# nix-darwin-test-source-target: libexec/nix_darwin_local/config.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/local_source.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/local_deployment.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/local_deployment_plan.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/source_snapshot_status.py
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local import (
    config,
    deployment_workflow,
    launcher,
    local_deployment,
    local_deployment_plan,
    local_source,
    source_snapshot_status,
)
from nix_darwin_local.paths import ConfigError
from nix_darwin_local.source_catalog import SourceCatalogEntry


REVISION_A = "a" * 40
REVISION_B = "b" * 40
REVISION_C = "c" * 40
NAR_HASH_A = "sha256-" + "A" * 43 + "="
NAR_HASH_B = "sha256-" + "B" * 43 + "="


@pytest.fixture
def private_state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_root = tmp_path / "private-state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)
    return state_root


def init_git_repo(path: Path) -> str:
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


def make_commit(path: Path, msg: str = "update") -> str:
    readme = path / "README.md"
    readme.write_text(readme.read_text() + f"{msg}\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, check=True, stdout=subprocess.PIPE, text=True)
    return res.stdout.strip()


def test_selected_source_at_configured_revision_passes(tmp_path: Path):
    """When configured snapshot is active and selected source matches snapshot revision, validation succeeds."""
    home = tmp_path / "home"
    home.mkdir()
    selected_repo = home / "selected_flake"
    rev_sel = init_git_repo(selected_repo)
    dep_repo = home / "dep_flake"
    rev_dep = init_git_repo(dep_repo)

    catalog = (
        SourceCatalogEntry("selected-src", selected_repo, "ssh://git@example/sel.git", "refs/heads/main"),
        SourceCatalogEntry("dep-src", dep_repo, "ssh://git@example/dep.git", "refs/heads/main"),
    )
    snap = config.SourceSnapshot((("dep-src", rev_dep), ("selected-src", rev_sel)))
    cfg = config.ControlConfig(sources=catalog, snapshot=snap)
    effective = config.resolve_effective_source_revisions(cfg, {})

    entry = local_source.match_selected_source(catalog, selected_repo)
    assert entry is not None
    assert entry.key == "selected-src"
    assert effective[entry.key] == rev_sel


def test_selected_source_head_differs_fails_before_qualification(tmp_path: Path, private_state_root: Path):
    """When configured snapshot is active and selected source checkout commit differs from snapshot, fails closed."""
    home = tmp_path / "home"
    home.mkdir()
    selected_repo = home / "selected_flake"
    rev_initial = init_git_repo(selected_repo)
    rev_second = make_commit(selected_repo, "second commit")

    catalog = (
        SourceCatalogEntry("selected-src", selected_repo, "ssh://git@example/sel.git", "refs/heads/main"),
    )
    # Snapshot expects initial commit, but repo HEAD is at second commit
    snap = config.SourceSnapshot((("selected-src", rev_initial),))
    cfg = config.ControlConfig(sources=catalog, snapshot=snap)
    effective = config.resolve_effective_source_revisions(cfg, {})

    control_dir = tmp_path / "control"
    control_dir.mkdir()
    wf = deployment_workflow.Workflow(selected_repo, sources=catalog, display_command_output=False)

    with pytest.raises(ConfigError, match="selected source 'selected-src' commit .* does not match expected snapshot revision"):
        deployment_workflow.build_only(wf, selected_repo, "TestHost", control_dir, [], source_revisions=effective)


def test_selected_flake_outside_catalog_in_snapshot_mode_fails_closed(tmp_path: Path, private_state_root: Path):
    """When snapshot mode is active and selected flake does not match any configured source, fails closed."""
    home = tmp_path / "home"
    home.mkdir()
    other_flake = home / "other_flake"
    init_git_repo(other_flake)

    configured_repo = home / "configured_flake"
    rev_conf = init_git_repo(configured_repo)

    catalog = (
        SourceCatalogEntry("configured-src", configured_repo, "ssh://git@example/conf.git", "refs/heads/main"),
    )
    snap = config.SourceSnapshot((("configured-src", rev_conf),))
    cfg = config.ControlConfig(sources=catalog, snapshot=snap)
    effective = config.resolve_effective_source_revisions(cfg, {})

    control_dir = tmp_path / "control"
    control_dir.mkdir()
    wf = deployment_workflow.Workflow(other_flake, sources=catalog, display_command_output=False)

    with pytest.raises(ConfigError, match="does not match any configured source checkout in snapshot mode"):
        deployment_workflow.build_only(wf, other_flake, "TestHost", control_dir, [], source_revisions=effective)


def test_selected_source_key_excluded_from_lock_backed_projection(tmp_path: Path, monkeypatch, private_state_root: Path):
    """Selected source is not sent through project_candidate_lock as if it were a lock-backed node."""
    home = tmp_path / "home"
    home.mkdir()
    selected_repo = home / "selected"
    rev_sel = init_git_repo(selected_repo)
    dep_repo = home / "dep"
    rev_dep = init_git_repo(dep_repo)

    catalog = (
        SourceCatalogEntry("selected-src", selected_repo, "ssh://git@example/sel.git", "refs/heads/main"),
        SourceCatalogEntry("dep-src", dep_repo, "ssh://git@example/dep.git", "refs/heads/main"),
    )
    snap = config.SourceSnapshot((("dep-src", rev_dep), ("selected-src", rev_sel)))
    cfg = config.ControlConfig(sources=catalog, snapshot=snap)
    effective = config.resolve_effective_source_revisions(cfg, {})

    projected_revisions: dict[str, str] = {}

    class ProjectedSuccessfully(Exception):
        pass

    def mock_project_candidate_lock(wf, target_dir, source_revs):
        projected_revisions.update(source_revs)
        raise ProjectedSuccessfully()

    monkeypatch.setattr(local_source, "project_candidate_lock", mock_project_candidate_lock)

    control_dir = tmp_path / "control"
    control_dir.mkdir()
    wf = deployment_workflow.Workflow(selected_repo, sources=catalog, display_command_output=False)

    with pytest.raises(ProjectedSuccessfully):
        deployment_workflow.build_only(wf, selected_repo, "TestHost", control_dir, [], source_revisions=effective)

    # dep-src is projected, but selected-src MUST NOT be projected
    assert "dep-src" in projected_revisions
    assert "selected-src" not in projected_revisions


def test_dependency_head_differs_from_snapshot_desired_commit_exists_locally_materializes(tmp_path: Path, private_state_root: Path):
    """Dependency checkout HEAD may differ from snapshot if desired commit exists locally."""
    dep_repo = tmp_path / "dep"
    rev_initial = init_git_repo(dep_repo)
    rev_second = make_commit(dep_repo, "second commit")

    entry = SourceCatalogEntry("dep", dep_repo, "https://example.invalid/dep.git", "refs/heads/main")
    wf = deployment_workflow.Workflow(tmp_path, sources=(entry,), display_command_output=False)

    # HEAD is at rev_second, but we request rev_initial
    mat = local_source.materialize_source(wf, entry, rev_initial)
    assert mat.revision == rev_initial
    # Check that dep_repo HEAD was NOT mutated
    curr_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=dep_repo, check=True, capture_output=True, text=True).stdout.strip()
    assert curr_head == rev_second


def test_desired_commit_missing_from_local_checkout_fails_closed_no_remote_fallback(tmp_path: Path, private_state_root: Path):
    """Desired commit missing from local checkout fails closed with no remote fallback."""
    dep_repo = tmp_path / "dep"
    init_git_repo(dep_repo)

    entry = SourceCatalogEntry("dep", dep_repo, "https://example.invalid/dep.git", "refs/heads/main")
    wf = deployment_workflow.Workflow(tmp_path, sources=(entry,), display_command_output=False)

    missing_rev = "0" * 40
    with pytest.raises(local_source.SourceError, match="does not contain requested revision"):
        local_source.materialize_source(wf, entry, missing_rev)


def test_plan_v3_validation_and_canonical_bytes():
    """Plan document schema nix-update-local-plan-v3 validates strict fields and rejects malformed content."""
    doc: dict[str, Any] = {
        "schema": "nix-update-local-plan-v3",
        "selected_flake": "/path/to/flake",
        "host": "TestHost",
        "git_authority": {"commit": REVISION_A, "tree": REVISION_B},
        "target_system": "/nix/store/test-system",
        "predecessor_generation": 202,
        "profile_snapshot": {"relationship": "aligned"},
        "sources": {"verified_count": 1},
        "effects": {"status": "absent"},
        "preflight_results": [],
        "closure_delta": {},
        "created_at": "2026-09-17T17:00:00Z",
        "local_sources": [],
        "lock": {"nodes": {}, "root": "root", "version": 7},
        "source_snapshot": {
            "active": True,
            "configured_revisions": {"core": REVISION_A},
            "effective_revisions": {"core": REVISION_A},
            "provenance": {"core": "snapshot"},
            "selected_source": {"key": "core", "revision": REVISION_A},
            "lock_backed_revisions": {},
        },
    }
    local_deployment_plan.validate_local_plan_document(doc)

    # Missing field inside source_snapshot fails closed
    bad_doc = json.loads(json.dumps(doc))
    del bad_doc["source_snapshot"]["active"]
    with pytest.raises(ConfigError, match="missing or unknown fields"):
        local_deployment_plan.validate_local_plan_document(bad_doc)

    # Unknown field in plan fails closed
    bad_doc2 = json.loads(json.dumps(doc))
    bad_doc2["extra_field"] = "bad"
    with pytest.raises(ConfigError, match="missing or unknown fields"):
        local_deployment_plan.validate_local_plan_document(bad_doc2)


def test_plan_replay_fails_closed_when_snapshot_intent_changed(tmp_path: Path, monkeypatch, private_state_root: Path):
    """--plan replay fails closed if TOML snapshot revisions changed between plan and replay."""
    monkeypatch.setattr("nix_darwin_local.local_deployment.operator_home", lambda: tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    selected_repo = home / "selected"
    rev_sel = init_git_repo(selected_repo)
    dep_repo = home / "dep"
    rev_dep1 = init_git_repo(dep_repo)
    rev_dep2 = make_commit(dep_repo, "update")

    catalog = (
        SourceCatalogEntry("selected-src", selected_repo, "ssh://git@example/sel.git", "refs/heads/main"),
        SourceCatalogEntry("dep-src", dep_repo, "ssh://git@example/dep.git", "refs/heads/main"),
    )

    # Create plan with rev_dep1
    snap_original = config.SourceSnapshot((("dep-src", rev_dep1), ("selected-src", rev_sel)))
    cfg_original = config.ControlConfig(sources=catalog, snapshot=snap_original)
    eff_original = config.resolve_effective_source_revisions(cfg_original, {})

    plan_dir = tmp_path / "plan_out"
    plan_dir.mkdir(mode=0o700)
    plan_doc = {
        "schema": "nix-update-local-plan-v3",
        "selected_flake": str(selected_repo.resolve()),
        "host": "TestHost",
        "git_authority": {"commit": rev_sel},
        "target_system": "/nix/store/dummy-system",
        "predecessor_generation": 202,
        "profile_snapshot": {"relationship": "aligned"},
        "sources": {"verified_count": 1},
        "effects": {"status": "absent"},
        "preflight_results": [],
        "closure_delta": {},
        "created_at": "2026-09-17T17:00:00Z",
        "local_sources": [],
        "lock": {"nodes": {}, "root": "root", "version": 7},
        "source_snapshot": {
            "active": True,
            "configured_revisions": {"dep-src": rev_dep1, "selected-src": rev_sel},
            "effective_revisions": {"dep-src": rev_dep1, "selected-src": rev_sel},
            "provenance": {"dep-src": "snapshot", "selected-src": "snapshot"},
            "selected_source": {"key": "selected-src", "revision": rev_sel},
            "lock_backed_revisions": {"dep-src": rev_dep1},
        },
    }
    local_deployment_plan.write_local_plan(plan_dir, plan_doc, "# Plan\n")

    # Replay with modified snapshot (rev_dep2)
    snap_modified = config.SourceSnapshot((("dep-src", rev_dep2), ("selected-src", rev_sel)))
    cfg_modified = config.ControlConfig(sources=catalog, snapshot=snap_modified)
    eff_modified = config.resolve_effective_source_revisions(cfg_modified, {})

    control_dir = tmp_path / "control"
    control_dir.mkdir()
    wf = deployment_workflow.Workflow(selected_repo, sources=catalog, display_command_output=False)

    with pytest.raises(ConfigError, match="configured snapshot revisions changed since reviewed local plan was created"):
        local_deployment.deploy_local(
            wf,
            selected_repo,
            "TestHost",
            control_dir,
            [],
            "1",
            "1",
            plan_path=plan_dir,
            source_revisions=eff_modified,
        )


def test_plan_replay_fails_closed_when_cli_override_changed(tmp_path: Path, monkeypatch, private_state_root: Path):
    """--plan replay fails closed if CLI override changed."""
    monkeypatch.setattr("nix_darwin_local.local_deployment.operator_home", lambda: tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    selected_repo = home / "selected"
    rev_sel = init_git_repo(selected_repo)
    dep_repo = home / "dep"
    rev_dep1 = init_git_repo(dep_repo)
    rev_override1 = make_commit(dep_repo, "override1")
    rev_override2 = make_commit(dep_repo, "override2")

    catalog = (
        SourceCatalogEntry("selected-src", selected_repo, "ssh://git@example/sel.git", "refs/heads/main"),
        SourceCatalogEntry("dep-src", dep_repo, "ssh://git@example/dep.git", "refs/heads/main"),
    )
    snap = config.SourceSnapshot((("dep-src", rev_dep1), ("selected-src", rev_sel)))
    cfg = config.ControlConfig(sources=catalog, snapshot=snap)

    plan_dir = tmp_path / "plan_out"
    plan_dir.mkdir(mode=0o700)
    plan_doc = {
        "schema": "nix-update-local-plan-v3",
        "selected_flake": str(selected_repo.resolve()),
        "host": "TestHost",
        "git_authority": {"commit": rev_sel},
        "target_system": "/nix/store/dummy-system",
        "predecessor_generation": 202,
        "profile_snapshot": {"relationship": "aligned"},
        "sources": {"verified_count": 1},
        "effects": {"status": "absent"},
        "preflight_results": [],
        "closure_delta": {},
        "created_at": "2026-09-17T17:00:00Z",
        "local_sources": [],
        "lock": {"nodes": {}, "root": "root", "version": 7},
        "source_snapshot": {
            "active": True,
            "configured_revisions": {"dep-src": rev_dep1, "selected-src": rev_sel},
            "effective_revisions": {"dep-src": rev_override1, "selected-src": rev_sel},
            "provenance": {"dep-src": "cli", "selected-src": "snapshot"},
            "selected_source": {"key": "selected-src", "revision": rev_sel},
            "lock_backed_revisions": {"dep-src": rev_override1},
        },
    }
    local_deployment_plan.write_local_plan(plan_dir, plan_doc, "# Plan\n")

    eff_changed_override = config.resolve_effective_source_revisions(cfg, {"dep-src": rev_override2})

    control_dir = tmp_path / "control"
    control_dir.mkdir()
    wf = deployment_workflow.Workflow(selected_repo, sources=catalog, display_command_output=False)

    with pytest.raises(ConfigError, match="effective source revisions changed since reviewed local plan was created"):
        local_deployment.deploy_local(
            wf,
            selected_repo,
            "TestHost",
            control_dir,
            [],
            "1",
            "1",
            plan_path=plan_dir,
            source_revisions=eff_changed_override,
        )


def test_plan_replay_fails_closed_when_selected_source_revision_drifted(tmp_path: Path, monkeypatch, private_state_root: Path):
    """--plan replay fails closed if selected source authority drifted."""
    monkeypatch.setattr("nix_darwin_local.local_deployment.operator_home", lambda: tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    selected_repo = home / "selected"
    rev_sel1 = init_git_repo(selected_repo)
    rev_sel2 = make_commit(selected_repo, "second commit")

    catalog = (
        SourceCatalogEntry("selected-src", selected_repo, "ssh://git@example/sel.git", "refs/heads/main"),
    )
    cfg = config.ControlConfig(sources=catalog, snapshot=None)
    eff = config.resolve_effective_source_revisions(cfg, {})

    plan_dir = tmp_path / "plan_out"
    plan_dir.mkdir(mode=0o700)
    plan_doc = {
        "schema": "nix-update-local-plan-v3",
        "selected_flake": str(selected_repo.resolve()),
        "host": "TestHost",
        "git_authority": {"commit": rev_sel2},
        "target_system": "/nix/store/dummy-system",
        "predecessor_generation": 202,
        "profile_snapshot": {"relationship": "aligned"},
        "sources": {"verified_count": 0},
        "effects": {"status": "absent"},
        "preflight_results": [],
        "closure_delta": {},
        "created_at": "2026-09-17T17:00:00Z",
        "local_sources": [],
        "lock": {"nodes": {}, "root": "root", "version": 7},
        "source_snapshot": {
            "active": False,
            "configured_revisions": None,
            "effective_revisions": {},
            "provenance": {},
            "selected_source": {"key": "selected-src", "revision": rev_sel1},
            "lock_backed_revisions": {},
        },
    }
    local_deployment_plan.write_local_plan(plan_dir, plan_doc, "# Plan\n")

    control_dir = tmp_path / "control"
    control_dir.mkdir()
    wf = deployment_workflow.Workflow(selected_repo, sources=catalog, display_command_output=False)

    with pytest.raises(ConfigError, match="selected source authority changed since reviewed local plan was created"):
        local_deployment.deploy_local(
            wf,
            selected_repo,
            "TestHost",
            control_dir,
            [],
            "1",
            "1",
            plan_path=plan_dir,
            source_revisions=eff,
        )


def test_legacy_plan_v2_replays_without_snapshot_and_blocks_with_snapshot(tmp_path: Path, monkeypatch, private_state_root: Path):
    """Legacy plan v2 replays when snapshot is inactive, and fails closed when snapshot is active."""
    monkeypatch.setattr("nix_darwin_local.local_deployment.operator_home", lambda: tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    selected_repo = home / "selected"
    rev_sel = init_git_repo(selected_repo)

    catalog = (
        SourceCatalogEntry("selected-src", selected_repo, "ssh://git@example/sel.git", "refs/heads/main"),
    )

    plan_dir = tmp_path / "plan_out"
    plan_dir.mkdir(mode=0o700)
    plan_doc = {
        "schema": "nix-update-local-plan-v2",
        "selected_flake": str(selected_repo.resolve()),
        "host": "TestHost",
        "git_authority": {"commit": rev_sel},
        "target_system": "/nix/store/dummy-system",
        "predecessor_generation": 202,
        "profile_snapshot": {"relationship": "aligned"},
        "sources": {"verified_count": 0},
        "effects": {"status": "absent"},
        "preflight_results": [],
        "closure_delta": {},
        "created_at": "2026-09-17T17:00:00Z",
        "local_sources": [],
        "lock": {"nodes": {}, "root": "root", "version": 7},
    }
    local_deployment_plan.write_local_plan(plan_dir, plan_doc, "# Plan\n")

    # When snapshot is active on replay, fail closed
    snap = config.SourceSnapshot((("selected-src", rev_sel),))
    cfg_active = config.ControlConfig(sources=catalog, snapshot=snap)
    eff_active = config.resolve_effective_source_revisions(cfg_active, {})

    control_dir = tmp_path / "control"
    control_dir.mkdir()
    wf = deployment_workflow.Workflow(selected_repo, sources=catalog, display_command_output=False)

    with pytest.raises(ConfigError, match="configured snapshot intent changed since reviewed local plan was created"):
        local_deployment.deploy_local(
            wf,
            selected_repo,
            "TestHost",
            control_dir,
            [],
            "1",
            "1",
            plan_path=plan_dir,
            source_revisions=eff_active,
        )


def test_snapshot_status_command(tmp_path: Path, capsys):
    """nix-update snapshot status reports completeness, expected vs observed HEADs, and active/selected flake."""
    home = tmp_path / "home"
    home.mkdir()
    core_repo = home / "core"
    rev_core = init_git_repo(core_repo)
    comp_repo = home / "comp"
    rev_comp = init_git_repo(comp_repo)

    config_path = home / "nix-darwin.toml"
    config_path.write_text(
        f"""
[flake]
active = "{comp_repo}"

[sources.core]
checkout = "{core_repo}"
repository = "ssh://git@example/core.git"
release_ref = "refs/heads/main"

[sources.comp]
checkout = "{comp_repo}"
repository = "ssh://git@example/comp.git"
release_ref = "refs/heads/main"

[snapshot.sources]
core = "{rev_core}"
comp = "{rev_comp}"
"""
    )

    code = source_snapshot_status.show_snapshot_status(
        core_repo, None, config_path, {}, home
    )
    assert code == 0
    captured = capsys.readouterr().out
    assert "configured: yes" in captured
    assert "complete: yes (2 of 2 sources configured)" in captured
    assert "selected flake source: comp" in captured
    assert f"core: expected={rev_core}, head={rev_core} (match)" in captured
    assert f"comp: expected={rev_comp}, head={rev_comp} (match)" in captured


def setup_deploy_local_mocks(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("nix_darwin_local.local_deployment.operator_home", lambda: tmp_path)
    monkeypatch.setattr("nix_darwin_local.local_deployment._guard_processes", lambda *a, **kw: None)
    profile = {
        "relationship": "aligned",
        "selected": {"generation_number": 1, "target": "/nix/store/t1", "link": "/nix/var/nix/profiles/system-1-link", "generation_path": "/nix/var/nix/profiles/system-1-link"},
        "live": {"generation_number": None, "target": "/nix/store/t1"},
        "retained": [{"generation": 1, "target": "/nix/store/t1", "path": "/nix/var/nix/profiles/system-1-link"}],
    }
    monkeypatch.setattr("nix_darwin_local.local_deployment.inspect_profile", lambda *a: profile)
    monkeypatch.setattr("nix_darwin_local.local_deployment.evaluate_allowlist", lambda *a: [])
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir(exist_ok=True)
    monkeypatch.setattr("nix_darwin_local.local_deployment.render_scratch", lambda *a: SimpleNamespace(root=scratch_root))
    monkeypatch.setattr("nix_darwin_local.local_deployment.evaluate_deployment_effects", lambda *a: {"status": "absent"})
    monkeypatch.setattr("nix_darwin_local.local_deployment.derive_source_identities", lambda *a, **kw: {"verified_count": 1, "sources": []})
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.closure_inventory",
        lambda *a: [{"path": "/nix/store/dummy-target-system"}],
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.current_live_closure",
        lambda *a: [{"path": "/nix/store/dummy-live-system"}],
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_source_binding.capture_local_sources",
        lambda *a: [],
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_source_binding.revalidate_local_sources",
        lambda *a: None,
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment._qualification_command",
        lambda *a: None,
    )
    def mock_materialize(workflow, selected, git_identity, scratch_target):
        scratch_target.mkdir(parents=True, exist_ok=True)
        (scratch_target / "flake.lock").write_text('{"nodes":{"root":{}},"root":"root","version":7}\n')
        (scratch_target / "flake.nix").write_text('{ outputs = { self }: {}; }\n')

    monkeypatch.setattr(
        "nix_darwin_local.deployment_workflow._materialize_git_tree",
        mock_materialize,
    )
    def project_fixture(workflow, target, revisions):
        nodes = {"root": {"inputs": {key: key for key in revisions}}}
        catalog = {entry.key: entry for entry in workflow.sources}
        for key, rev in revisions.items():
            nodes[key] = {"locked": {"type": "git", "url": catalog[key].repository,
                                      "rev": rev, "narHash": "sha256-fixture="}}
        (target / "flake.lock").write_text(json.dumps({"version": 7, "root": "root", "nodes": nodes}))
    monkeypatch.setattr(local_source, "project_candidate_lock", project_fixture)



def test_plan_production_records_v3_source_snapshot_evidence_and_positive_replay(tmp_path: Path, monkeypatch, private_state_root: Path):
    """deploy_local with --plan-only writes v3 plan with snapshot evidence, and unchanged replay succeeds."""
    setup_deploy_local_mocks(monkeypatch, tmp_path)

    home = tmp_path / "home"
    home.mkdir()
    selected_repo = home / "selected"
    rev_sel = init_git_repo(selected_repo)
    dep_repo = home / "dep"
    rev_dep = init_git_repo(dep_repo)
    rev_dep_override = make_commit(dep_repo, "override")

    catalog = (
        SourceCatalogEntry("selected-src", selected_repo, "ssh://git@example/sel.git", "refs/heads/main"),
        SourceCatalogEntry("dep-src", dep_repo, "ssh://git@example/dep.git", "refs/heads/main"),
    )
    snap = config.SourceSnapshot((("dep-src", rev_dep), ("selected-src", rev_sel)))
    cfg = config.ControlConfig(sources=catalog, snapshot=snap)
    # One CLI override on dep-src
    eff = config.resolve_effective_source_revisions(cfg, {"dep-src": rev_dep_override})

    wf = deployment_workflow.Workflow(selected_repo, sources=catalog, display_command_output=False)
    real_command = wf.command
    def mock_command(label, argv, cwd=None):
        if label.startswith("Git "):
            return real_command(label, argv, cwd)
        if label == "exact Darwin target build":
            return SimpleNamespace(returncode=0, stdout="/nix/store/dummy-target-system\n")
        return SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(wf, "command", mock_command)

    real_complete_stdout = wf.complete_stdout
    def mock_complete_stdout(label):
        if label.startswith("Git "):
            return real_complete_stdout(label)
        return ""
    monkeypatch.setattr(wf, "complete_stdout", mock_complete_stdout)

    control_dir = tmp_path / "control"
    control_dir.mkdir()
    plan_out = tmp_path / "produced_plan"

    # Step 1: Produce the plan
    rc_plan = local_deployment.deploy_local(
        wf,
        selected_repo,
        "TestHost",
        control_dir,
        [],
        "1",
        "1",
        plan_only=True,
        plan_output=plan_out,
        source_revisions=eff,
    )
    assert rc_plan == 0
    assert (plan_out / "plan.json").is_file()

    # Verify plan document structure and contents
    loaded = local_deployment_plan.load_local_plan(plan_out)
    doc = loaded.document
    assert doc["schema"] == "nix-update-local-plan-v3"
    ss = doc["source_snapshot"]
    assert ss["active"] is True
    assert ss["configured_revisions"] == {"dep-src": rev_dep, "selected-src": rev_sel}
    assert ss["effective_revisions"] == {"dep-src": rev_dep_override, "selected-src": rev_sel}
    assert ss["provenance"] == {"dep-src": "cli", "selected-src": "snapshot"}
    assert ss["selected_source"] == {"key": "selected-src", "revision": rev_sel}
    assert ss["lock_backed_revisions"] == {"dep-src": rev_dep_override}

    # A saved lock cannot contradict its otherwise unchanged v3 pin evidence.
    import copy
    for bad_lock in (None, copy.deepcopy(doc["lock"])):
        altered = copy.deepcopy(doc)
        if bad_lock is not None:
            bad_lock["nodes"]["dep-src"]["locked"]["rev"] = rev_dep
        altered["lock"] = bad_lock
        with monkeypatch.context() as scoped:
            scoped.setattr(local_deployment, "load_local_plan", lambda *a: SimpleNamespace(document=altered))
            with pytest.raises(ConfigError, match="requested revision|no matching node"):
                local_deployment.deploy_local(wf, selected_repo, "TestHost", control_dir, [], "1", "1",
                    plan_path=plan_out, source_revisions=eff, dry_run=True)

    # Step 2: Positive replay with unchanged snapshot and effective revisions
    rc_replay = local_deployment.deploy_local(
        wf,
        selected_repo,
        "TestHost",
        control_dir,
        [],
        "1",
        "1",
        plan_path=plan_out,
        dry_run=True,
        source_revisions=eff,
    )
    assert rc_replay == 0


def test_legacy_cli_selected_source_handling_and_projection(tmp_path: Path, monkeypatch, private_state_root: Path):
    """When no snapshot is configured, CLI revisions for selected source are validated and lock-backed are projected."""
    setup_deploy_local_mocks(monkeypatch, tmp_path)

    home = tmp_path / "home"
    home.mkdir()
    selected_repo = home / "selected"
    rev_sel = init_git_repo(selected_repo)
    dep_repo = home / "dep"
    rev_dep = init_git_repo(dep_repo)

    catalog = (
        SourceCatalogEntry("selected-src", selected_repo, "ssh://git@example/sel.git", "refs/heads/main"),
        SourceCatalogEntry("dep-src", dep_repo, "ssh://git@example/dep.git", "refs/heads/main"),
    )
    # No snapshot configured
    cfg = config.ControlConfig(sources=catalog, snapshot=None)

    projected_revisions: dict[str, str] = {}

    def capture_project(workflow, target, revisions):
        projected_revisions.update(revisions)

    monkeypatch.setattr(
        "nix_darwin_local.local_source.project_candidate_lock",
        capture_project,
    )

    wf = deployment_workflow.Workflow(selected_repo, sources=catalog, display_command_output=False)
    real_command = wf.command
    def mock_command(label, argv, cwd=None):
        if label.startswith("Git "):
            return real_command(label, argv, cwd)
        if label == "exact Darwin target build":
            return SimpleNamespace(returncode=0, stdout="/nix/store/dummy-target-system\n")
        return SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(wf, "command", mock_command)

    real_complete_stdout = wf.complete_stdout
    def mock_complete_stdout(label):
        if label.startswith("Git "):
            return real_complete_stdout(label)
        return ""
    monkeypatch.setattr(wf, "complete_stdout", mock_complete_stdout)
    control_dir = tmp_path / "control"
    control_dir.mkdir()

    # Case 1: CLI revision matches selected repo commit, plus lock-backed dependency revision
    eff1 = config.resolve_effective_source_revisions(
        cfg, {"selected-src": rev_sel, "dep-src": rev_dep}
    )
    projected_revisions.clear()
    rc1 = local_deployment.deploy_local(
        wf,
        selected_repo,
        "TestHost",
        control_dir,
        [],
        "1",
        "1",
        dry_run=True,
        source_revisions=eff1,
    )
    assert rc1 == 0
    # Selected source was excluded from lock projection, while dep-src was included
    assert projected_revisions == {"dep-src": rev_dep}

    # Case 2: CLI revision for selected source mismatches local commit -> fails closed
    bogus_rev = "f" * 40
    eff2 = config.resolve_effective_source_revisions(
        cfg, {"selected-src": bogus_rev, "dep-src": rev_dep}
    )
    with pytest.raises(ConfigError, match="selected source 'selected-src' commit .* does not match expected revision"):
        local_deployment.deploy_local(
            wf,
            selected_repo,
            "TestHost",
            control_dir,
            [],
            "1",
            "1",
            dry_run=True,
            source_revisions=eff2,
        )


def test_snapshot_status_launcher_dispatch(tmp_path: Path, monkeypatch, capsys):
    """launcher dispatch correctly routes 'snapshot status' subcommand."""
    home = tmp_path / "home"
    home.mkdir()
    core_repo = home / "core"
    rev_core = init_git_repo(core_repo)
    comp_repo = home / "comp"
    rev_comp = init_git_repo(comp_repo)

    config_dir = home / "config"
    config_dir.mkdir()
    config_path = config_dir / "nix-darwin.toml"
    config_path.write_text(
        f"""
[flake]
active = "{comp_repo}"

[sources.core]
checkout = "{core_repo}"
repository = "ssh://git@example/core.git"
release_ref = "refs/heads/main"

[sources.comp]
checkout = "{comp_repo}"
repository = "ssh://git@example/comp.git"
release_ref = "refs/heads/main"

[snapshot.sources]
core = "{rev_core}"
comp = "{rev_comp}"
"""
    )
    parser = launcher._parser()
    args = parser.parse_args(["snapshot", "status", "--flake", str(comp_repo)])

    monkeypatch.setattr(launcher, "operator_home", lambda: home)
    env = {"NIX_DARWIN_LOCAL": str(home)}
    rc = launcher._run(core_repo, args, env)
    assert rc == 0
    captured = capsys.readouterr().out
    assert "configured: yes" in captured
    assert "complete: yes (2 of 2 sources configured)" in captured
    assert "selected flake source: comp" in captured
