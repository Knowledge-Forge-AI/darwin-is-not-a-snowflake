# nix-darwin-test-source-target: libexec/nix_darwin_local/local_source.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/local_source_binding.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/repository_identity.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/deployment_workflow.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/local_deployment.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/launcher.py
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local import (
    config,
    deployment_workflow,
    launcher,
    local_deployment,
    local_source,
    local_source_binding,
    repository_identity,
)
from nix_darwin_local.launcher import parse_source_revisions
from nix_darwin_local.deployment_plan import normalized_lock, serialize_lock_document
from nix_darwin_local.local_source import (
    LocalSourceIdentity,
    SourceCatalogEntry,
    SourceError,
    coerce_source_catalog,
    materialize_source,
    observe_source,
    project_candidate_lock,
    repository_urls_match,
)
from nix_darwin_local.local_source_binding import (
    capture_local_sources,
    revalidate_local_sources,
    validate_local_sources,
)
from nix_darwin_local.paths import ConfigError
from nix_darwin_local.promotion_locked_source import capture_promotion_local_sources
from nix_darwin_local.promotion_qualification import promotion_tool_identity
from nix_darwin_local.promotion_source import validate_source_receipt
from nix_darwin_local.repository_identity import local_git_identity, publication_git_identity

REVISION_A = "a" * 40
REVISION_B = "b" * 40
REVISION_C = "c" * 40
NAR_HASH_A = "sha256-" + "A" * 43 + "="
NAR_HASH_B = "sha256-" + "B" * 43 + "="
STORE_PATH_A = "/nix/store/" + "a" * 32 + "-leaf"
STORE_PATH_B = "/nix/store/" + "b" * 32 + "-leaf"


def init_git_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    (path / "flake.nix").write_text("{ outputs = { self }: {}; }\n")
    (path / "flake.lock").write_text(json.dumps({"nodes": {"root": {}}, "root": "root", "version": 7}))
    (path / "README.md").write_text("initial content\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
    return rev


def make_mock_workflow(tmp_path: Path, git_responses: dict[str, Any] | None = None) -> Any:
    class MockWorkflow:
        def __init__(self):
            self.core_dir = tmp_path
            self.records = []
            self.outputs = {}
            self.sources = ()

        def command(self, label: str, argv: list[str], cwd: Path):
            self.records.append((label, list(argv), Path(cwd)))
            if git_responses and label in git_responses:
                res = git_responses[label]
                self.outputs[label] = res.stdout if hasattr(res, "stdout") else ""
                return res
            res = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.outputs[label] = res.stdout
            return res

        def complete_stdout(self, label: str) -> str:
            if label in self.outputs:
                return self.outputs[label]
            for rec_label, _argv, _cwd in reversed(self.records):
                if rec_label == label:
                    return self.outputs.get(rec_label, "")
            return ""

    return MockWorkflow()


# Scenario 1: Dirty unrelated leaf
def test_scenario_1_dirty_unrelated_leaf(tmp_path: Path):
    leaf_dir = tmp_path / "leaf"
    leaf_rev = init_git_repo(leaf_dir)

    # Advance HEAD H > L
    (leaf_dir / "committed_in_h.txt").write_text("commit H content\n")
    subprocess.run(["git", "add", "committed_in_h.txt"], cwd=leaf_dir, check=True)
    subprocess.run(["git", "commit", "-m", "advance to H"], cwd=leaf_dir, check=True, stdout=subprocess.PIPE)
    rev_h = subprocess.run(["git", "rev-parse", "HEAD"], cwd=leaf_dir, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
    assert rev_h != leaf_rev

    # Make the working tree dirty by modifying tracked file and adding untracked file
    (leaf_dir / "unrelated_dirty.txt").write_text("uncommitted worktree dirt\n")
    (leaf_dir / "README.md").write_text("modified uncommitted content\n")

    workflow = make_mock_workflow(tmp_path)
    entry = SourceCatalogEntry(
        key="leaf",
        checkout=leaf_dir,
        repository="https://example.invalid/leaf.git",
        release_ref="refs/heads/main",
    )

    # observe_source should succeed and accurately record clean=False
    identity = observe_source(workflow, entry, leaf_rev)
    assert identity.revision == leaf_rev
    assert identity.head_revision == rev_h
    assert identity.clean is False

    # Extract git tree at leaf_rev and prove dirty bytes + commit H are demonstrably excluded
    scratch_target = tmp_path / "scratch_target"
    deployment_workflow._materialize_git_tree(
        workflow, leaf_dir, {"commit": leaf_rev, "repository_root": str(leaf_dir)}, scratch_target
    )
    assert not (scratch_target / "unrelated_dirty.txt").exists()
    assert not (scratch_target / "committed_in_h.txt").exists()
    assert (scratch_target / "README.md").read_text() == "initial content\n"

    # Validation and revalidation should succeed despite clean=False
    lock = {
        "nodes": {
            "leaf": {
                "locked": {
                    "type": "git",
                    "url": "https://example.invalid/leaf.git",
                    "rev": leaf_rev,
                    "narHash": NAR_HASH_A,
                }
            }
        }
    }
    identity_dict = identity.as_dict()
    identity_dict["store_path"] = STORE_PATH_A
    identity_dict["nar_hash"] = NAR_HASH_A
    record = {"node": "leaf", "source": identity_dict}

    validate_local_sources([record], lock)


# Scenario 2: Dirty selected checkout, committed candidate
def test_scenario_2_dirty_selected_checkout_committed_candidate(tmp_path: Path):
    selected = tmp_path / "selected"
    rev1 = init_git_repo(selected)

    # Commit candidate configuration
    (selected / "flake.nix").write_text('{ outputs = { self }: { candidate = "committed"; }; }\n')
    (selected / "flake.lock").write_text(json.dumps({"nodes": {"root": {}}, "root": "root", "version": 7}))
    subprocess.run(["git", "add", "flake.nix", "flake.lock"], cwd=selected, check=True)
    subprocess.run(["git", "commit", "-m", "candidate commit"], cwd=selected, check=True, stdout=subprocess.PIPE)
    candidate_rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=selected, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()

    # Now dirty the selected worktree with uncommitted changes
    (selected / "flake.nix").write_text('{ outputs = { self }: { candidate = "DIRTY_UNCOMMITTED"; }; }\n')
    (selected / "uncommitted_file.txt").write_text("should never enter build\n")

    workflow = make_mock_workflow(tmp_path)

    # local_git_identity with require_clean=False succeeds and captures committed state
    identity = local_git_identity(workflow, selected, False)
    assert identity["commit"] == candidate_rev
    assert identity["status"] != ""  # Has dirty status

    # When materialized into scratch, the committed tree is extracted, omitting uncommitted changes
    scratch_target = tmp_path / "scratch_target"
    deployment_workflow._materialize_git_tree(workflow, selected, identity, scratch_target)

    # Verify uncommitted file is not in scratch and flake.nix has committed content
    assert not (scratch_target / "uncommitted_file.txt").exists()
    assert 'candidate = "committed"' in (scratch_target / "flake.nix").read_text()
    assert "DIRTY_UNCOMMITTED" not in (scratch_target / "flake.nix").read_text()


# Scenario 3: No remote configured
def test_scenario_3_no_remote_configured(tmp_path: Path):
    repo = tmp_path / "noremote"
    rev = init_git_repo(repo)

    # Verify repo has no remotes
    remotes = subprocess.run(["git", "remote"], cwd=repo, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
    assert remotes == ""

    workflow = make_mock_workflow(tmp_path)
    entry = SourceCatalogEntry(key="noremote", checkout=repo, repository="https://example.invalid/noremote.git", release_ref="refs/heads/main")
    identity = observe_source(workflow, entry, rev)
    assert identity.revision == rev
    local = local_git_identity(workflow, repo, False)
    assert local["commit"] == rev

    # Assert no remote contact (fetch, ls-remote) occurred
    assert all("fetch" not in cmd and "ls-remote" not in cmd for _, cmd, _ in workflow.records)


# Scenario 4: Remote unreachable
def test_scenario_4_remote_unreachable(tmp_path: Path):
    repo = tmp_path / "unreachableremote"
    rev = init_git_repo(repo)

    # Add an unreachable remote
    subprocess.run(["git", "remote", "add", "origin", "https://127.0.0.1:9/unreachable.git"], cwd=repo, check=True)

    workflow = make_mock_workflow(tmp_path)
    entry = SourceCatalogEntry(key="unreachable", checkout=repo, repository="https://example.invalid/unreachable.git", release_ref="refs/heads/main")
    identity = observe_source(workflow, entry, rev)
    assert identity.revision == rev
    local = local_git_identity(workflow, repo, False)
    assert local["commit"] == rev

    # Assert no network contact attempt occurred
    assert all("fetch" not in cmd and "ls-remote" not in cmd for _, cmd, _ in workflow.records)


# Scenario 5: HEAD/ref disagreement
def test_scenario_5_head_ref_disagreement(tmp_path: Path, monkeypatch):
    leaf_dir = tmp_path / "leaf"
    rev_a = init_git_repo(leaf_dir)

    # Create a feature branch with commit B
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=leaf_dir, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (leaf_dir / "feature.txt").write_text("feature commit\n")
    subprocess.run(["git", "add", "feature.txt"], cwd=leaf_dir, check=True)
    subprocess.run(["git", "commit", "-m", "feature"], cwd=leaf_dir, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    rev_b = subprocess.run(["git", "rev-parse", "HEAD"], cwd=leaf_dir, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()

    # Switch checkout HEAD back to main (rev_a)
    subprocess.run(["git", "checkout", "main"], cwd=leaf_dir, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    target_dir = tmp_path / "target"
    target_dir.mkdir()
    initial_lock = {
        "nodes": {
            "root": {"inputs": {"leaf": "leaf"}},
            "leaf": {
                "locked": {
                    "type": "git",
                    "url": "https://example.invalid/leaf.git",
                    "rev": rev_a,
                    "narHash": NAR_HASH_A,
                }
            },
        },
        "root": "root",
        "version": 7,
    }
    (target_dir / "flake.lock").write_text(json.dumps(initial_lock))

    workflow = make_mock_workflow(tmp_path)
    entry = SourceCatalogEntry(
        key="leaf",
        checkout=leaf_dir,
        repository="https://example.invalid/leaf.git",
        release_ref="refs/heads/main",
    )
    workflow.sources = (entry,)

    # Mock materialize_source to return rev_b
    def mock_materialize(wf, cat_entry, rev, **kwargs):
        return LocalSourceIdentity(
            key="leaf",
            checkout=str(leaf_dir),
            repository="https://example.invalid/leaf.git",
            release_ref="refs/heads/main",
            revision=rev,
            head_ref="refs/heads/main",
            head_revision=rev_a,
            clean=True,
            operation_state="clean",
            store_path=STORE_PATH_B,
            nar_hash=NAR_HASH_B,
        )

    monkeypatch.setattr(local_source, "materialize_source", mock_materialize)

    # Project candidate lock requesting rev_b even though checkout HEAD is at rev_a
    project_candidate_lock(workflow, target_dir, {"leaf": rev_b})

    updated_lock = json.loads((target_dir / "flake.lock").read_text())
    assert updated_lock["nodes"]["leaf"]["locked"]["rev"] == rev_b
    assert updated_lock["nodes"]["leaf"]["locked"]["narHash"] == NAR_HASH_B


# Scenario 6: Missing selected commit
def test_scenario_6_missing_selected_commit(tmp_path: Path):
    leaf_dir = tmp_path / "leaf"
    init_git_repo(leaf_dir)

    workflow = make_mock_workflow(tmp_path)
    entry = SourceCatalogEntry(
        key="leaf",
        checkout=leaf_dir,
        repository="https://example.invalid/leaf.git",
        release_ref="refs/heads/main",
    )

    nonexistent_rev = "0123456789abcdef0123456789abcdef01234567"
    with pytest.raises(SourceError, match="does not contain requested revision"):
        observe_source(workflow, entry, nonexistent_rev)

    # Assert no fetch or remote call occurred
    assert all("fetch" not in cmd and "ls-remote" not in cmd for _, cmd, _ in workflow.records)


# Scenario 7: Multiple independent dirty leaves
def test_scenario_7_multiple_independent_dirty_leaves(tmp_path: Path):
    leaf1 = tmp_path / "leaf1"
    rev1 = init_git_repo(leaf1)
    (leaf1 / "README.md").write_text("modified dirt 1\n")

    leaf2 = tmp_path / "leaf2"
    rev2 = init_git_repo(leaf2)
    (leaf2 / "README.md").write_text("modified dirt 2\n")

    workflow = make_mock_workflow(tmp_path)
    entry1 = SourceCatalogEntry(key="leaf1", checkout=leaf1, repository="https://example.invalid/leaf1.git", release_ref="refs/heads/main")
    entry2 = SourceCatalogEntry(key="leaf2", checkout=leaf2, repository="https://example.invalid/leaf2.git", release_ref="refs/heads/main")

    id1 = observe_source(workflow, entry1, rev1)
    id2 = observe_source(workflow, entry2, rev2)

    assert id1.clean is False
    assert id2.clean is False

    lock = {
        "nodes": {
            "leaf1": {"locked": {"type": "git", "url": entry1.repository, "rev": rev1, "narHash": NAR_HASH_A}},
            "leaf2": {"locked": {"type": "git", "url": entry2.repository, "rev": rev2, "narHash": NAR_HASH_B}},
        }
    }
    rec1 = {"node": "leaf1", "source": {**id1.as_dict(), "store_path": STORE_PATH_A, "nar_hash": NAR_HASH_A}}
    rec2 = {"node": "leaf2", "source": {**id2.as_dict(), "store_path": STORE_PATH_B, "nar_hash": NAR_HASH_B}}

    validate_local_sources([rec1, rec2], lock)


# Scenario 8: Plan replay
def test_scenario_8_plan_replay(tmp_path: Path):
    leaf_dir = tmp_path / "leaf"
    rev = init_git_repo(leaf_dir)

    entry = SourceCatalogEntry(
        key="leaf",
        checkout=leaf_dir,
        repository="https://example.invalid/leaf.git",
        release_ref="refs/heads/main",
    )
    workflow = make_mock_workflow(tmp_path)
    workflow.sources = (entry,)

    mat = materialize_source(workflow, entry, rev)

    lock = {
        "nodes": {
            "leaf": {
                "locked": {
                    "type": "git",
                    "url": "https://example.invalid/leaf.git",
                    "rev": rev,
                    "narHash": mat.nar_hash,
                }
            }
        }
    }

    identity = LocalSourceIdentity(
        key="leaf",
        checkout=str(leaf_dir),
        repository="https://example.invalid/leaf.git",
        release_ref="refs/heads/main",
        revision=rev,
        head_ref="refs/heads/main",
        head_revision=rev,
        clean=True,
        operation_state="clean",
        store_path=mat.store_path,
        nar_hash=mat.nar_hash,
    )
    reviewed_records = [{"node": "leaf", "source": identity.as_dict()}]

    # Validate reviewed records
    validate_local_sources(reviewed_records, lock)

    # Mutate leaf checkout after plan creation: advance HEAD and add dirt
    (leaf_dir / "advance.txt").write_text("advance\n")
    subprocess.run(["git", "add", "advance.txt"], cwd=leaf_dir, check=True)
    subprocess.run(["git", "commit", "-m", "advance"], cwd=leaf_dir, check=True, stdout=subprocess.PIPE)
    (leaf_dir / "README.md").write_text("uncommitted dirt\n")

    # Revalidation against the reviewed records succeeds because bound immutable keys match
    revalidate_local_sources(workflow, lock, reviewed_records)

    # Replacement or removal of commit L fails closed
    missing_commit_records = [{"node": "leaf", "source": {**identity.as_dict(), "revision": "f" * 40}}]
    with pytest.raises((SourceError, ConfigError)):
        revalidate_local_sources(workflow, lock, missing_commit_records)


# Scenario 9: Publication separation
def test_scenario_9_publication_separation(tmp_path: Path):
    selected = tmp_path / "selected"
    rev = init_git_repo(selected)

    workflow = make_mock_workflow(tmp_path)

    # 1. Normal dirty worktree should be allowed by local_git_identity(require_clean=False)
    (selected / "README.md").write_text("modified dirt\n")
    local = local_git_identity(workflow, selected, False)
    assert local["status"] != ""

    # 2. publication_git_identity MUST reject dirty worktree
    with pytest.raises(ConfigError, match="not clean"):
        publication_git_identity(workflow, selected, local)

    # 3. Clean worktree but detached HEAD must also be rejected by publication_git_identity
    subprocess.run(["git", "checkout", "README.md"], cwd=selected, check=True)
    subprocess.run(["git", "checkout", "--detach", rev], cwd=selected, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    detached_local = local_git_identity(workflow, selected, False)
    assert detached_local["branch"] == "HEAD"

    with pytest.raises(ConfigError, match="detached HEAD"):
        publication_git_identity(workflow, selected, detached_local)


# Scenario 10: Current serializer incident
def test_scenario_10_current_serializer_incident(tmp_path: Path):
    """Simulate promotion with unrelated leaf (devux) having tracked dirt and detached HEAD."""
    core_dir = tmp_path / "core"
    core_rev = init_git_repo(core_dir)

    devux_dir = tmp_path / "devux"
    devux_rev_base = init_git_repo(devux_dir)

    # advance devux to a feature branch with uncommitted tracked dirt
    (devux_dir / "feature.py").write_text("# devux feature\n")
    subprocess.run(["git", "add", "feature.py"], cwd=devux_dir, check=True)
    subprocess.run(["git", "commit", "-m", "devux feature"], cwd=devux_dir, check=True, stdout=subprocess.PIPE)
    devux_feature_rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=devux_dir, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
    subprocess.run(["git", "checkout", "--detach", devux_feature_rev], cwd=devux_dir, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (devux_dir / "feature.py").write_text("# uncommitted tracked dirt in devux\n")

    workflow = make_mock_workflow(tmp_path)
    core_mat = materialize_source(workflow, SourceCatalogEntry("core", core_dir, "https://example.invalid/core.git", "refs/heads/main"), core_rev)
    devux_mat = materialize_source(workflow, SourceCatalogEntry("devux", devux_dir, "https://example.invalid/devux.git", "refs/heads/main"), devux_rev_base)

    core_entry = SourceCatalogEntry("core", core_dir, "https://example.invalid/core.git", "refs/heads/main")
    devux_entry = SourceCatalogEntry("devux", devux_dir, "https://example.invalid/devux.git", "refs/heads/main")
    workflow.sources = (core_entry, devux_entry)

    candidate_lock = {
        "nodes": {
            "root": {"inputs": {"core": "core", "devux": "devux"}},
            "core": {
                "locked": {
                    "type": "git",
                    "url": "https://example.invalid/core.git",
                    "rev": core_rev,
                    "narHash": core_mat.nar_hash,
                    "ref": "main",
                }
            },
            "devux": {
                "locked": {
                    "type": "git",
                    "url": "https://example.invalid/devux.git",
                    "rev": devux_rev_base,
                    "narHash": devux_mat.nar_hash,
                    "ref": "main",
                }
            },
        },
        "root": "root",
        "version": 7,
    }

    # capture_promotion_local_sources should succeed and NOT be blocked by devux's dirt or detached HEAD
    records = capture_promotion_local_sources(
        workflow,
        candidate_lock,
        preimage_lock=candidate_lock,
        selected_node="core",
    )
    assert len(records) == 2
    devux_record = next(r for r in records if r["node"] == "devux")
    assert devux_record["role"] == "unchanged-lock"
    # devux clean is truthfully recorded as False without gating promotion
    assert devux_record["source"]["clean"] is False


def test_source_receipt_permits_clean_false():
    """Verify validate_source_receipt accepts clean=False for local authority."""
    document = {
        "schema": "nix-update-core-source-receipt-v2",
        "status": "ALREADY_SELECTED",
        "input": "core",
        "revision": "a" * 40,
        "impact": None,
        "no_activation": True,
        "source_authority": {
            "key": "core",
            "checkout": "/tmp/core",
            "repository": "https://example.invalid/core.git",
            "release_ref": "refs/heads/main",
            "revision": "a" * 40,
            "head_ref": "refs/heads/main",
            "head_revision": "a" * 40,
            "clean": False,
            "operation_state": "clean",
            "store_path": None,
            "nar_hash": None,
            "publication": None,
        },
        "repository": {
            "commit": "a" * 40,
            "head_commit": "a" * 40,
            "repository_root": "/tmp/core",
        },
        "execution_repository": {
            "commit": "a" * 40,
            "repository_root": "/tmp/core",
        },
        "tool": promotion_tool_identity(CORE_ROOT),
        "receipt_digest": "",
    }
    validate_source_receipt(document, allow_unsigned=True)


def test_build_only_preserves_checked_in_flake_lock(tmp_path: Path):
    """Ephemeral candidate projection never mutates the checked-in flake.lock."""
    repo = tmp_path / "repo"
    rev = init_git_repo(repo)
    lock_doc = {
        "nodes": {
            "root": {"inputs": {"core": "core"}},
            "core": {
                "locked": {
                    "type": "git",
                    "url": "https://example.invalid/core.git",
                    "rev": rev,
                    "narHash": "sha256-" + "A" * 43 + "=",
                    "ref": "main",
                }
            },
        },
        "root": "root",
        "version": 7,
    }
    (repo / "README.md").write_text("new leaf revision\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "advance leaf"], cwd=repo, check=True, stdout=subprocess.PIPE)
    rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
    (repo / "flake.lock").write_text(json.dumps(lock_doc))
    subprocess.run(["git", "add", "flake.lock"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add core node"], cwd=repo, check=True, stdout=subprocess.PIPE)
    # Project the earlier leaf commit, not the later composition that contains it.
    original_lock = (repo / "flake.lock").read_bytes()

    workflow = make_mock_workflow(tmp_path)
    entry = SourceCatalogEntry("core", repo, "https://example.invalid/core.git", "refs/heads/main")
    workflow.sources = (entry,)

    scratch_target = tmp_path / "scratch" / "target"
    scratch_target.mkdir(parents=True)
    (scratch_target / "flake.lock").write_bytes(original_lock)

    project_candidate_lock(workflow, scratch_target, {"core": rev})
    assert (repo / "flake.lock").read_bytes() == original_lock


def test_advanced_prepare_rejects_source_revision(tmp_path: Path):
    """advanced prepare fails closed when --source-revision is passed."""
    flake_dir = tmp_path / "flake"
    flake_dir.mkdir()
    (flake_dir / "flake.nix").write_text("{ outputs = { self }: {}; }\n")
    args = argparse.Namespace(
        command="advanced",
        advanced_command="prepare",
        source_revision=["core=" + "a" * 40],
        flake=str(flake_dir),
        contract=tmp_path / "contract.json",
        output=tmp_path / "out",
        preflight_only=False,
        test_jobs=None,
        build_jobs=None,
        allow_core_override=False,
    )
    with pytest.raises(ConfigError, match="--source-revision is not supported"):
        launcher._run(tmp_path, args, {})


def test_local_git_identity_require_attached_branch_independent(tmp_path: Path):
    """Test independent control of require_attached_branch and require_clean."""
    repo = tmp_path / "test_repo"
    rev = init_git_repo(repo)

    # Detach HEAD
    subprocess.run(["git", "checkout", "--detach", rev], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    workflow = make_mock_workflow(tmp_path)
    # With require_attached_branch=True, detached HEAD must fail even if require_clean=False
    with pytest.raises(ConfigError, match="detached HEAD"):
        local_git_identity(workflow, repo, require_clean=False, require_attached_branch=True)

    # With require_attached_branch=False, detached HEAD is accepted
    identity = local_git_identity(workflow, repo, require_clean=False, require_attached_branch=False)
    assert identity["branch"] == "HEAD"


def test_parse_source_revisions_real_config_collection(tmp_path: Path):
    """parse_source_revisions must accept the actual collection shape returned by load_control_config."""
    home = tmp_path / "home"
    home.mkdir(parents=True)
    core_checkout = home / "core"
    leaf_checkout = home / "leaf"
    init_git_repo(core_checkout)
    init_git_repo(leaf_checkout)

    config_path = home / "control.toml"
    config_path.write_text(
        f"""
[sources.core]
checkout = "{core_checkout}"
repository = "https://example.invalid/core.git"
release_ref = "refs/heads/main"

[sources.leaf]
checkout = "{leaf_checkout}"
repository = "https://example.invalid/leaf.git"
release_ref = "refs/heads/main"
"""
    )
    loaded = config.load_control_config(config_path, operator_home=home)
    assert isinstance(loaded.sources, tuple)
    assert len(loaded.sources) == 2
    assert all(isinstance(e, SourceCatalogEntry) for e in loaded.sources)

    # Valid configured keys with 40-hex revisions
    parsed = parse_source_revisions([f"leaf={REVISION_A}", f"core={REVISION_B}"], loaded.sources)
    assert parsed == {"leaf": REVISION_A, "core": REVISION_B}

    # Unknown key fails closed
    with pytest.raises(ConfigError, match="unknown source key in --source-revision: 'missing'"):
        parse_source_revisions([f"missing={REVISION_A}"], loaded.sources)

    # Duplicate key fails closed
    with pytest.raises(ConfigError, match="duplicate --source-revision for key: 'leaf'"):
        parse_source_revisions([f"leaf={REVISION_A}", f"leaf={REVISION_B}"], loaded.sources)

    # Malformed format (no '=')
    with pytest.raises(ConfigError, match="invalid --source-revision format: 'leaf', expected KEY=REV"):
        parse_source_revisions(["leaf"], loaded.sources)

    # Malformed revision (not 40-hex)
    with pytest.raises(ConfigError, match="expected KEY=40-hex-commit"):
        parse_source_revisions(["leaf=not-a-hash"], loaded.sources)

    # Empty catalog fails closed
    with pytest.raises(ConfigError, match="--source-revision requires configured \\[sources\\] catalog"):
        parse_source_revisions([f"leaf={REVISION_A}"], ())


def test_project_candidate_lock_real_config_collection(tmp_path: Path, monkeypatch):
    """project_candidate_lock must accept the actual collection shape from ControlConfig.sources."""
    home = tmp_path / "home"
    home.mkdir(parents=True)
    leaf_dir = home / "leaf"
    other_dir = home / "other"
    init_git_repo(leaf_dir)
    init_git_repo(other_dir)

    config_path = home / "control.toml"
    config_path.write_text(
        f"""
[sources.leaf]
checkout = "{leaf_dir}"
repository = "https://example.invalid/leaf.git"
release_ref = "refs/heads/main"

[sources.other]
checkout = "{other_dir}"
repository = "https://example.invalid/other.git"
release_ref = "refs/heads/main"
"""
    )
    loaded = config.load_control_config(config_path, operator_home=home)
    assert isinstance(loaded.sources, tuple)

    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)
    initial_lock = {
        "nodes": {
            "root": {"inputs": {"leaf": "leaf", "untouched": "untouched"}},
            "leaf": {
                "locked": {
                    "type": "git",
                    "url": "https://example.invalid/leaf.git",
                    "rev": REVISION_A,
                    "narHash": NAR_HASH_A,
                }
            },
            "untouched": {
                "locked": {
                    "type": "git",
                    "url": "https://example.invalid/untouched.git",
                    "rev": REVISION_C,
                    "narHash": "sha256-" + "C" * 43 + "=",
                }
            },
        },
        "root": "root",
        "version": 7,
    }
    (target_dir / "flake.lock").write_text(json.dumps(initial_lock))

    workflow = make_mock_workflow(tmp_path)
    workflow.sources = loaded.sources  # actual tuple of SourceCatalogEntry

    materialize_calls = []

    def mock_materialize(wf, cat_entry, rev, **kwargs):
        materialize_calls.append((cat_entry, rev))
        return LocalSourceIdentity(
            key=cat_entry.key,
            checkout=str(cat_entry.checkout),
            repository=cat_entry.repository,
            release_ref=cat_entry.release_ref,
            revision=rev,
            head_ref="refs/heads/main",
            head_revision=REVISION_A,
            clean=True,
            operation_state="clean",
            store_path=None,
            nar_hash=NAR_HASH_B,
        )

    monkeypatch.setattr(local_source, "materialize_source", mock_materialize)

    project_candidate_lock(workflow, target_dir, {"leaf": REVISION_B})

    # Proves materialize_source receives the resolved catalog entry
    assert len(materialize_calls) == 1
    passed_entry, passed_rev = materialize_calls[0]
    assert passed_entry.key == "leaf"
    assert passed_entry.repository == "https://example.invalid/leaf.git"
    assert passed_rev == REVISION_B

    # Proves only selected node changed, untouched node did not
    updated_lock = json.loads((target_dir / "flake.lock").read_text())
    assert updated_lock["nodes"]["leaf"]["locked"]["rev"] == REVISION_B
    assert updated_lock["nodes"]["leaf"]["locked"]["narHash"] == NAR_HASH_B
    assert updated_lock["nodes"]["untouched"]["locked"]["rev"] == REVISION_C

    # Proves unknown source key fails closed
    with pytest.raises(ConfigError, match="source revision key 'unknown' not found in catalog"):
        project_candidate_lock(workflow, target_dir, {"unknown": REVISION_B})

    # Proves configured catalog source with no matching lock node fails closed
    with pytest.raises(ConfigError, match="candidate source 'other' has no matching node in flake.lock"):
        project_candidate_lock(workflow, target_dir, {"other": REVISION_B})


def test_selected_source_non_node_fails_closed(tmp_path: Path):
    """A configured source representing the selected composition is not a lock node and must fail closed."""
    home = tmp_path / "home"
    home.mkdir(parents=True)
    comp_checkout = home / "composition-private"
    init_git_repo(comp_checkout)

    config_path = home / "control.toml"
    config_path.write_text(
        f"""
[flake]
active = "{comp_checkout}"

[sources.composition-private-nd]
checkout = "{comp_checkout}"
repository = "ssh://git@github.com/example/composition-private.git"
release_ref = "refs/heads/main"
"""
    )
    loaded = config.load_control_config(config_path, operator_home=home)

    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)
    # The lock of the top-level flake does NOT contain composition-private-nd under nodes
    lock_doc = {
        "nodes": {
            "root": {"inputs": {"core": "core"}},
            "core": {
                "locked": {
                    "type": "git",
                    "url": "ssh://git@github.com/example/core.git",
                    "rev": REVISION_A,
                    "narHash": NAR_HASH_A,
                }
            },
        },
        "root": "root",
        "version": 7,
    }
    (target_dir / "flake.lock").write_text(json.dumps(lock_doc))

    workflow = make_mock_workflow(tmp_path)
    workflow.sources = loaded.sources

    # Passing composition-private-nd must fail explicitly, never silently pass
    with pytest.raises(ConfigError, match="candidate source 'composition-private-nd' has no matching node in flake.lock"):
        project_candidate_lock(workflow, target_dir, {"composition-private-nd": REVISION_B})


def test_real_cli_build_and_deploy_path_regression(tmp_path: Path, monkeypatch):
    """Launcher build and deploy paths accept valid configured keys and pass projected source revisions."""
    home = tmp_path / "home"
    home.mkdir(parents=True)
    core_checkout = home / "core"
    leaf_checkout = home / "leaf"
    comp_checkout = home / "composition"
    init_git_repo(core_checkout)
    init_git_repo(leaf_checkout)
    init_git_repo(comp_checkout)

    local_root = home / ".local/nix-darwin"
    config_dir = local_root / "config"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "nix-darwin.toml"
    config_path.write_text(
        f"""
[flake]
active = "{comp_checkout}"

[sources.core]
checkout = "{core_checkout}"
repository = "ssh://git@github.com/example/core.git"
release_ref = "refs/heads/main"

[sources.leaf]
checkout = "{leaf_checkout}"
repository = "ssh://git@github.com/example/leaf.git"
release_ref = "refs/heads/main"
"""
    )

    build_calls = []
    deploy_calls = []

    def mock_build_only(wf, flake_path, host, cdir, overrides, tj, bj, source_revisions):
        build_calls.append((flake_path, source_revisions))
        return 0

    def mock_deploy_local(wf, flake_path, host, cdir, overrides, tj, bj, **kwargs):
        deploy_calls.append((flake_path, kwargs.get("source_revisions")))
        return 0

    monkeypatch.setattr(launcher, "build_only", mock_build_only)
    monkeypatch.setattr(launcher, "deploy_local", mock_deploy_local)
    monkeypatch.setattr(launcher, "operator_home", lambda: home)
    monkeypatch.setattr(launcher, "_darwin_config", lambda env: "dummy-host")

    args_build = argparse.Namespace(
        command="build",
        flake=None,
        allow_core_override=False,
        test_jobs=None,
        build_jobs=None,
        source_revision=[f"leaf={REVISION_B}"],
        timing=False,
    )
    code = launcher._run(core_checkout, args_build, {})
    assert code == 0
    assert len(build_calls) == 1
    assert build_calls[0][1] == {"leaf": REVISION_B}

    args_deploy = argparse.Namespace(
        command="deploy",
        flake=None,
        allow_core_override=False,
        test_jobs=None,
        build_jobs=None,
        source_revision=[f"leaf={REVISION_B}", f"core={REVISION_A}"],
        timing=False,
        dry_run=True,
        plan_only=False,
        output=None,
        plan=None,
    )
    code = launcher._run(core_checkout, args_deploy, {})
    assert code == 0
    assert len(deploy_calls) == 1
    assert deploy_calls[0][1] == {"leaf": REVISION_B, "core": REVISION_A}

    # Proves unknown key fails closed before build/deploy
    args_bad = argparse.Namespace(
        command="build",
        flake=None,
        allow_core_override=False,
        test_jobs=None,
        build_jobs=None,
        source_revision=[f"unknown={REVISION_B}"],
        timing=False,
    )
    with pytest.raises(ConfigError, match="unknown source key in --source-revision: 'unknown'"):
        launcher._run(core_checkout, args_bad, {})


def test_project_candidate_lock_excludes_result_only_fetchtree_metadata(tmp_path: Path, monkeypatch):
    """1. Realistic fetchTree result metadata (shortRev, lastModifiedDate, etc.) must not leak into projected lock."""
    leaf_dir = tmp_path / "leaf"
    init_git_repo(leaf_dir)
    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)

    initial_lock = {
        "nodes": {
            "root": {"inputs": {"leaf": "leaf"}},
            "leaf": {
                "locked": {
                    "type": "git",
                    "url": "https://example.invalid/leaf.git",
                    "ref": "refs/heads/main",
                    "rev": REVISION_A,
                    "narHash": NAR_HASH_A,
                }
            },
        },
        "root": "root",
        "version": 7,
    }
    (target_dir / "flake.lock").write_text(json.dumps(initial_lock))

    workflow = make_mock_workflow(tmp_path)
    entry = SourceCatalogEntry(
        key="leaf",
        checkout=leaf_dir,
        repository="https://example.invalid/leaf.git",
        release_ref="refs/heads/main",
    )
    workflow.sources = (entry,)

    def mock_materialize(wf, cat_entry, rev, **kwargs):
        return LocalSourceIdentity(
            key="leaf",
            checkout=str(leaf_dir),
            repository=cat_entry.repository,
            release_ref=cat_entry.release_ref,
            revision=rev,
            head_ref="refs/heads/main",
            head_revision=REVISION_A,
            clean=True,
            operation_state="clean",
            store_path=STORE_PATH_B,
            nar_hash=NAR_HASH_B,
            locked_metadata={
                "lastModified": 1789335288,
                "lastModifiedDate": "20260913213448",
                "narHash": NAR_HASH_B,
                "path": STORE_PATH_B,
                "outPath": STORE_PATH_B,
                "rev": rev,
                "shortRev": rev[:7],
                "submodules": False,
            },
        )

    monkeypatch.setattr(local_source, "materialize_source", mock_materialize)

    project_candidate_lock(workflow, target_dir, {"leaf": REVISION_B})

    updated_lock = json.loads((target_dir / "flake.lock").read_text())
    leaf_locked = updated_lock["nodes"]["leaf"]["locked"]

    assert "shortRev" not in leaf_locked
    assert "lastModifiedDate" not in leaf_locked
    assert "path" not in leaf_locked
    assert "outPath" not in leaf_locked
    assert "submodules" not in leaf_locked
    assert leaf_locked["type"] == "git"
    assert leaf_locked["url"] == "https://example.invalid/leaf.git"
    assert leaf_locked["ref"] == "refs/heads/main"
    assert leaf_locked["rev"] == REVISION_B
    assert leaf_locked["narHash"] == NAR_HASH_B
    assert leaf_locked["lastModified"] == 1789335288


def test_project_candidate_lock_revision_metadata_refresh(tmp_path: Path, monkeypatch):
    """2. When revision changes, old rev, narHash, lastModified, and revCount must be refreshed/purged."""
    leaf_dir = tmp_path / "leaf"
    init_git_repo(leaf_dir)
    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)

    initial_lock = {
        "nodes": {
            "root": {"inputs": {"leaf": "leaf"}},
            "leaf": {
                "locked": {
                    "type": "git",
                    "url": "https://example.invalid/leaf.git",
                    "ref": "refs/heads/main",
                    "rev": REVISION_A,
                    "narHash": NAR_HASH_A,
                    "lastModified": 1000,
                    "revCount": 5,
                }
            },
        },
        "root": "root",
        "version": 7,
    }
    (target_dir / "flake.lock").write_text(json.dumps(initial_lock))

    workflow = make_mock_workflow(tmp_path)
    entry = SourceCatalogEntry(
        key="leaf",
        checkout=leaf_dir,
        repository="https://example.invalid/leaf.git",
        release_ref="refs/heads/main",
    )
    workflow.sources = (entry,)

    # Case A: Materializer does not provide revCount
    def mock_materialize_no_rc(wf, cat_entry, rev, **kwargs):
        return LocalSourceIdentity(
            key="leaf",
            checkout=str(leaf_dir),
            repository=cat_entry.repository,
            release_ref=cat_entry.release_ref,
            revision=rev,
            head_ref="refs/heads/main",
            head_revision=REVISION_A,
            clean=True,
            operation_state="clean",
            store_path=None,
            nar_hash=NAR_HASH_B,
            locked_metadata={
                "lastModified": 2000,
                "narHash": NAR_HASH_B,
                "rev": rev,
            },
        )

    monkeypatch.setattr(local_source, "materialize_source", mock_materialize_no_rc)
    project_candidate_lock(workflow, target_dir, {"leaf": REVISION_B})

    updated_lock = json.loads((target_dir / "flake.lock").read_text())
    leaf_locked = updated_lock["nodes"]["leaf"]["locked"]
    assert leaf_locked["rev"] == REVISION_B
    assert leaf_locked["narHash"] == NAR_HASH_B
    assert leaf_locked["lastModified"] == 2000
    assert "revCount" not in leaf_locked  # Stale revCount must be removed

    # Case B: Materializer provides a valid revCount
    def mock_materialize_with_rc(wf, cat_entry, rev, **kwargs):
        return LocalSourceIdentity(
            key="leaf",
            checkout=str(leaf_dir),
            repository=cat_entry.repository,
            release_ref=cat_entry.release_ref,
            revision=rev,
            head_ref="refs/heads/main",
            head_revision=REVISION_A,
            clean=True,
            operation_state="clean",
            store_path=None,
            nar_hash=NAR_HASH_B,
            locked_metadata={
                "lastModified": 3000,
                "revCount": 6,
                "narHash": NAR_HASH_B,
                "rev": rev,
            },
        )

    monkeypatch.setattr(local_source, "materialize_source", mock_materialize_with_rc)
    project_candidate_lock(workflow, target_dir, {"leaf": REVISION_B})

    updated_lock2 = json.loads((target_dir / "flake.lock").read_text())
    leaf_locked2 = updated_lock2["nodes"]["leaf"]["locked"]
    assert leaf_locked2["lastModified"] == 3000
    assert leaf_locked2["revCount"] == 6


def test_project_candidate_lock_preserves_existing_fetcher_options(tmp_path: Path, monkeypatch):
    """3. Existing fetcher options like submodules = true must be preserved and not overwritten by fetchTree result default."""
    leaf_dir = tmp_path / "leaf"
    init_git_repo(leaf_dir)
    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)

    initial_lock = {
        "nodes": {
            "root": {"inputs": {"leaf": "leaf"}},
            "leaf": {
                "locked": {
                    "type": "git",
                    "url": "https://example.invalid/leaf.git",
                    "ref": "refs/heads/main",
                    "submodules": True,
                    "dir": "sub-flake",
                    "rev": REVISION_A,
                    "narHash": NAR_HASH_A,
                }
            },
        },
        "root": "root",
        "version": 7,
    }
    (target_dir / "flake.lock").write_text(json.dumps(initial_lock))

    workflow = make_mock_workflow(tmp_path)
    entry = SourceCatalogEntry(
        key="leaf",
        checkout=leaf_dir,
        repository="https://example.invalid/leaf.git",
        release_ref="refs/heads/main",
    )
    workflow.sources = (entry,)

    def mock_materialize(wf, cat_entry, rev, **kwargs):
        return LocalSourceIdentity(
            key="leaf",
            checkout=str(leaf_dir),
            repository=cat_entry.repository,
            release_ref=cat_entry.release_ref,
            revision=rev,
            head_ref="refs/heads/main",
            head_revision=REVISION_A,
            clean=True,
            operation_state="clean",
            store_path=None,
            nar_hash=NAR_HASH_B,
            locked_metadata={
                "lastModified": 1789335288,
                "narHash": NAR_HASH_B,
                "rev": rev,
                "submodules": False,  # fetchTree result default must NOT overwrite existing submodules=True
            },
        )

    monkeypatch.setattr(local_source, "materialize_source", mock_materialize)
    project_candidate_lock(workflow, target_dir, {"leaf": REVISION_B})

    updated_lock = json.loads((target_dir / "flake.lock").read_text())
    leaf_locked = updated_lock["nodes"]["leaf"]["locked"]
    assert leaf_locked["submodules"] is True  # preserved from existing accepted lock
    assert leaf_locked["dir"] == "sub-flake"
    assert leaf_locked["rev"] == REVISION_B


def test_project_candidate_lock_noop_idempotence_and_byte_stability(tmp_path: Path, monkeypatch):
    """4. An override requesting the exact revision already locked with matching narHash is stable and byte-identical."""
    leaf_dir = tmp_path / "leaf"
    init_git_repo(leaf_dir)
    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)

    initial_lock = {
        "nodes": {
            "root": {"inputs": {"leaf": "leaf"}},
            "leaf": {
                "locked": {
                    "lastModified": 1788645520,
                    "narHash": NAR_HASH_A,
                    "ref": "refs/heads/main",
                    "rev": REVISION_A,
                    "revCount": 3,
                    "type": "git",
                    "url": "https://example.invalid/leaf.git",
                }
            },
        },
        "root": "root",
        "version": 7,
    }
    canonical_preimage = serialize_lock_document(initial_lock)
    (target_dir / "flake.lock").write_bytes(canonical_preimage)

    workflow = make_mock_workflow(tmp_path)
    entry = SourceCatalogEntry(
        key="leaf",
        checkout=leaf_dir,
        repository="https://example.invalid/leaf.git",
        release_ref="refs/heads/main",
    )
    workflow.sources = (entry,)

    def mock_materialize(wf, cat_entry, rev, **kwargs):
        return LocalSourceIdentity(
            key="leaf",
            checkout=str(leaf_dir),
            repository=cat_entry.repository,
            release_ref=cat_entry.release_ref,
            revision=rev,
            head_ref="refs/heads/main",
            head_revision=REVISION_A,
            clean=True,
            operation_state="clean",
            store_path=None,
            nar_hash=NAR_HASH_A,
            locked_metadata={
                "lastModified": 1788645520,
                "lastModifiedDate": "20260905215840",
                "narHash": NAR_HASH_A,
                "rev": rev,
                "shortRev": rev[:7],
                "submodules": False,
            },
        )

    monkeypatch.setattr(local_source, "materialize_source", mock_materialize)

    # First projection
    project_candidate_lock(workflow, target_dir, {"leaf": REVISION_A})
    post_bytes_1 = (target_dir / "flake.lock").read_bytes()
    assert post_bytes_1 == canonical_preimage

    # Second projection (idempotent)
    project_candidate_lock(workflow, target_dir, {"leaf": REVISION_A})
    post_bytes_2 = (target_dir / "flake.lock").read_bytes()
    assert post_bytes_2 == canonical_preimage

    doc = json.loads(post_bytes_2.decode())
    assert doc["nodes"]["leaf"]["locked"]["revCount"] == 3
    assert "shortRev" not in doc["nodes"]["leaf"]["locked"]
    assert "lastModifiedDate" not in doc["nodes"]["leaf"]["locked"]


def test_real_nix_acceptance_projected_lock_regression(tmp_path: Path, monkeypatch):
    """5. Real Nix commands fail when result-only fields leak into locked nodes and pass with fixed projection."""
    resolved_root = tmp_path.resolve()
    leaf_dir = resolved_root / "leaf"
    leaf_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=leaf_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=leaf_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=leaf_dir, check=True)
    (leaf_dir / "flake.nix").write_text('{ description = "leaf"; outputs = { self }: { x = 1; }; }\n')
    subprocess.run(["git", "add", "."], cwd=leaf_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init leaf"], cwd=leaf_dir, check=True, capture_output=True)
    leaf_rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=leaf_dir, check=True, capture_output=True, text=True).stdout.strip()

    # Capture real Nix fetchTree metadata from local git checkout
    expr = f'''let
  s = builtins.fetchTree {{
    type = "git";
    url = "file://{leaf_dir}";
    rev = "{leaf_rev}";
  }};
in (builtins.removeAttrs s [ "outPath" ]) // {{ path = s.outPath; }}'''
    eval_proc = subprocess.run(
        ["nix", "eval", "--json", "--impure", "--offline", "--expr", expr],
        check=True, capture_output=True, text=True,
    )
    mat_meta = json.loads(eval_proc.stdout)
    nar_hash = mat_meta["narHash"]
    last_mod = mat_meta["lastModified"]

    # Target flake
    target_dir = resolved_root / "target"
    target_dir.mkdir(parents=True)
    (target_dir / "flake.nix").write_text(f'''{{
  description = "target";
  inputs.leaf.url = "git+file://{leaf_dir}?ref=main";
  outputs = {{ self, leaf }}: {{ result = leaf.x; }};
}}\n''')

    # Initial valid lock
    initial_lock = {
        "nodes": {
            "root": {"inputs": {"leaf": "leaf"}},
            "leaf": {
                "locked": {
                    "type": "git",
                    "url": f"file://{leaf_dir}",
                    "ref": "main",
                    "rev": leaf_rev,
                    "narHash": nar_hash,
                    "lastModified": last_mod,
                },
                "original": {
                    "type": "git",
                    "url": f"file://{leaf_dir}",
                    "ref": "main",
                },
            },
        },
        "root": "root",
        "version": 7,
    }
    (target_dir / "flake.lock").write_text(json.dumps(initial_lock))

    # Baseline 1: prove that injecting shortRev causes real Nix to fail
    leaky_lock = json.loads(json.dumps(initial_lock))
    leaky_lock["nodes"]["leaf"]["locked"]["shortRev"] = leaf_rev[:7]
    (target_dir / "flake.lock").write_text(json.dumps(leaky_lock))

    res_leaky = subprocess.run(
        ["nix", "flake", "metadata", "--no-update-lock-file", "--offline", f"path:{target_dir}"],
        capture_output=True, text=True,
    )
    assert res_leaky.returncode != 0
    assert "not supported by scheme 'git'" in res_leaky.stderr
    assert "shortRev" in res_leaky.stderr

    # Baseline 2: prove that injecting lastModifiedDate causes real Nix to fail
    leaky_date_lock = json.loads(json.dumps(initial_lock))
    leaky_date_lock["nodes"]["leaf"]["locked"]["lastModifiedDate"] = "20260913213448"
    (target_dir / "flake.lock").write_text(json.dumps(leaky_date_lock))

    res_leaky_date = subprocess.run(
        ["nix", "flake", "metadata", "--no-update-lock-file", "--offline", f"path:{target_dir}"],
        capture_output=True, text=True,
    )
    assert res_leaky_date.returncode != 0
    assert "not supported by scheme 'git'" in res_leaky_date.stderr
    assert "lastModifiedDate" in res_leaky_date.stderr

    # Now project using project_candidate_lock with production-shaped fetchTree metadata
    workflow = make_mock_workflow(resolved_root)
    entry = SourceCatalogEntry(
        key="leaf",
        checkout=leaf_dir,
        repository=f"file://{leaf_dir}",
        release_ref="refs/heads/main",
    )
    workflow.sources = (entry,)

    def mock_materialize(wf, cat_entry, rev, **kwargs):
        return LocalSourceIdentity(
            key="leaf",
            checkout=str(leaf_dir),
            repository=cat_entry.repository,
            release_ref=cat_entry.release_ref,
            revision=rev,
            head_ref="refs/heads/main",
            head_revision=leaf_rev,
            clean=True,
            operation_state="clean",
            store_path=mat_meta["path"],
            nar_hash=nar_hash,
            locked_metadata=mat_meta,  # Real Nix fetchTree metadata containing shortRev, submodules, etc.
        )

    monkeypatch.setattr(local_source, "materialize_source", mock_materialize)

    project_candidate_lock(workflow, target_dir, {"leaf": leaf_rev})

    # Real Nix command against projected lock must now succeed
    res_fixed = subprocess.run(
        ["nix", "flake", "metadata", "--no-update-lock-file", "--offline", f"path:{target_dir}"],
        capture_output=True, text=True,
    )
    assert res_fixed.returncode == 0, f"Real Nix rejected projected lock: {res_fixed.stderr}"

    # Verify real evaluation succeeds
    eval_check = subprocess.run(
        ["nix", "eval", "--json", "--offline", f"path:{target_dir}#result"],
        capture_output=True, text=True,
    )
    assert eval_check.returncode == 0
    assert json.loads(eval_check.stdout) == 1

    # Now create a second commit in leaf to exercise the revision-changed purge-and-refresh branch under real Nix
    (leaf_dir / "flake.nix").write_text('{ description = "leaf"; outputs = { self }: { x = 42; }; }\n')
    subprocess.run(["git", "add", "."], cwd=leaf_dir, check=True)
    subprocess.run(["git", "commit", "-m", "update leaf commit 2"], cwd=leaf_dir, check=True, capture_output=True)
    leaf_rev_2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=leaf_dir, check=True, capture_output=True, text=True).stdout.strip()
    assert leaf_rev_2 != leaf_rev

    expr_2 = f'''let
  s = builtins.fetchTree {{
    type = "git";
    url = "file://{leaf_dir}";
    rev = "{leaf_rev_2}";
  }};
in (builtins.removeAttrs s [ "outPath" ]) // {{ path = s.outPath; }}'''
    eval_proc_2 = subprocess.run(
        ["nix", "eval", "--json", "--impure", "--offline", "--expr", expr_2],
        check=True, capture_output=True, text=True,
    )
    mat_meta_2 = json.loads(eval_proc_2.stdout)
    nar_hash_2 = mat_meta_2["narHash"]

    # Inject a stale revCount into current lock to ensure revision change purges it
    current_lock = json.loads((target_dir / "flake.lock").read_text())
    current_lock["nodes"]["leaf"]["locked"]["revCount"] = 999
    (target_dir / "flake.lock").write_text(json.dumps(current_lock))

    def mock_materialize_2(wf, cat_entry, rev, **kwargs):
        if rev == leaf_rev_2:
            return LocalSourceIdentity(
                key="leaf",
                checkout=str(leaf_dir),
                repository=cat_entry.repository,
                release_ref=cat_entry.release_ref,
                revision=rev,
                head_ref="refs/heads/main",
                head_revision=leaf_rev_2,
                clean=True,
                operation_state="clean",
                store_path=mat_meta_2["path"],
                nar_hash=nar_hash_2,
                locked_metadata=mat_meta_2,
            )
        return mock_materialize(wf, cat_entry, rev, **kwargs)

    monkeypatch.setattr(local_source, "materialize_source", mock_materialize_2)

    project_candidate_lock(workflow, target_dir, {"leaf": leaf_rev_2})

    # Real Nix flake metadata and eval against the revision-changed projected lock
    res_changed = subprocess.run(
        ["nix", "flake", "metadata", "--no-update-lock-file", "--offline", f"path:{target_dir}"],
        capture_output=True, text=True,
    )
    assert res_changed.returncode == 0, f"Real Nix rejected updated projected lock: {res_changed.stderr}"

    eval_changed = subprocess.run(
        ["nix", "eval", "--json", "--offline", f"path:{target_dir}#result"],
        capture_output=True, text=True,
    )
    assert eval_changed.returncode == 0
    assert json.loads(eval_changed.stdout) == 42

    updated_doc = json.loads((target_dir / "flake.lock").read_text())
    up_locked = updated_doc["nodes"]["leaf"]["locked"]
    assert up_locked["rev"] == leaf_rev_2
    assert up_locked["narHash"] == nar_hash_2
    assert "revCount" not in up_locked
    assert "shortRev" not in up_locked
    assert "lastModifiedDate" not in up_locked


def test_real_build_and_deploy_projection_path_uses_projector(tmp_path: Path, monkeypatch):
    """6. Build and deploy paths pass source revisions through project_candidate_lock, producing clean candidate locks."""
    home = tmp_path / "home"
    home.mkdir(parents=True)
    core_checkout = home / "core"
    leaf_checkout = home / "leaf"
    comp_checkout = home / "composition"
    init_git_repo(core_checkout)
    init_git_repo(leaf_checkout)
    init_git_repo(comp_checkout)

    initial_lock = {
        "nodes": {
            "root": {"inputs": {"leaf": "leaf"}},
            "leaf": {
                "locked": {
                    "type": "git",
                    "url": "https://example.invalid/leaf.git",
                    "ref": "refs/heads/main",
                    "rev": REVISION_A,
                    "narHash": NAR_HASH_A,
                },
                "original": {
                    "type": "git",
                    "url": "https://example.invalid/leaf.git",
                    "ref": "refs/heads/main",
                },
            },
        },
        "root": "root",
        "version": 7,
    }
    (comp_checkout / "flake.lock").write_text(json.dumps(initial_lock))
    subprocess.run(["git", "add", "flake.lock"], cwd=comp_checkout, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "update lock"], cwd=comp_checkout, check=True, capture_output=True)

    local_root = home / ".local/nix-darwin"
    config_dir = local_root / "config"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "nix-darwin.toml"
    config_path.write_text(
        f"""
[flake]
active = "{comp_checkout}"

[sources.core]
checkout = "{core_checkout}"
repository = "ssh://git@github.com/example/core.git"
release_ref = "refs/heads/main"

[sources.leaf]
checkout = "{leaf_checkout}"
repository = "https://example.invalid/leaf.git"
release_ref = "refs/heads/main"
"""
    )

    projected_locks = []
    real_project_lock = local_source.project_candidate_lock

    def tracking_project_lock(wf, target_dir, source_revisions):
        real_project_lock(wf, target_dir, source_revisions)
        lock_doc = json.loads((target_dir / "flake.lock").read_text())
        projected_locks.append((dict(source_revisions), lock_doc))

    monkeypatch.setattr(local_source, "project_candidate_lock", tracking_project_lock)

    def mock_materialize(wf, cat_entry, rev, **kwargs):
        return LocalSourceIdentity(
            key=cat_entry.key,
            checkout=str(cat_entry.checkout),
            repository=cat_entry.repository,
            release_ref=cat_entry.release_ref,
            revision=rev,
            head_ref="refs/heads/main",
            head_revision=REVISION_A,
            clean=True,
            operation_state="clean",
            store_path=STORE_PATH_B,
            nar_hash=NAR_HASH_B,
            locked_metadata={
                "lastModified": 1789335288,
                "lastModifiedDate": "20260913213448",
                "narHash": NAR_HASH_B,
                "rev": rev,
                "shortRev": rev[:7],
                "submodules": False,
            },
        )

    monkeypatch.setattr(local_source, "materialize_source", mock_materialize)
    monkeypatch.setattr(launcher, "operator_home", lambda: home)
    monkeypatch.setattr(launcher, "_darwin_config", lambda env: "dummy-host")

    class FakeResult:
        returncode = 0
        stdout = b""
        stderr = b""

    class DummyLock:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(deployment_workflow, "DeploymentLock", DummyLock)
    monkeypatch.setattr(local_deployment, "DeploymentLock", DummyLock)
    monkeypatch.setattr(deployment_workflow, "evaluate_allowlist", lambda wf, target, ov: ["leaf"])
    from types import SimpleNamespace
    monkeypatch.setattr(deployment_workflow, "render_scratch", lambda cdir, target, al: SimpleNamespace(root=target))
    state_root = tmp_path / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    # Stubs for deploy_local path
    monkeypatch.setattr(local_deployment, "_guard_processes", lambda *a, **kw: None)
    monkeypatch.setattr(local_deployment, "local_git_identity", lambda *a: {"commit": "1" * 40, "clean": True})
    monkeypatch.setattr(local_deployment, "inspect_profile", lambda *a: {
        "relationship": "aligned",
        "selected": {"generation_number": 1, "target": "/nix/store/t1", "link": "/nix/var/nix/profiles/system-1-link", "generation_path": "/nix/var/nix/profiles/system-1-link"},
        "live": {"generation_number": None, "target": "/nix/store/t1"},
        "retained": [{"generation": 1, "target": "/nix/store/t1", "path": "/nix/var/nix/profiles/system-1-link"}],
    })
    monkeypatch.setattr(local_deployment, "evaluate_allowlist", lambda *a: ["leaf"])
    monkeypatch.setattr(local_deployment, "render_scratch", lambda cdir, target, al: SimpleNamespace(root=target))
    monkeypatch.setattr(local_deployment, "evaluate_deployment_effects", lambda *a: {"status": "absent"})
    monkeypatch.setattr(local_deployment, "classify_deployment_effects", lambda *a: (True, []))
    monkeypatch.setattr(local_deployment, "derive_source_identities", lambda *a, **kw: {"verified_count": 1, "sources": []})
    monkeypatch.setattr(local_deployment, "closure_inventory", lambda *a: [{"path": "/nix/store/dummy-target-system"}])
    monkeypatch.setattr(local_deployment, "current_live_closure", lambda *a: [{"path": "/nix/store/t1"}])

    original_workflow_cls = deployment_workflow.Workflow
    class FakeWorkflow(original_workflow_cls):
        def command(self, label, *args, **kwargs):
            if label == "exact Darwin target build":
                return SimpleNamespace(returncode=0, stdout="/nix/store/dummy-target-system\n", stderr="")
            if not label.startswith("Git"):
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return super().command(label, *args, **kwargs)

        def complete_stdout(self, label: str) -> str:
            if not label.startswith("Git"):
                return "diff closures line\n"
            return super().complete_stdout(label)

    monkeypatch.setattr(deployment_workflow, "Workflow", FakeWorkflow)
    monkeypatch.setattr(launcher, "Workflow", FakeWorkflow)

    args_build = argparse.Namespace(
        command="build",
        flake=None,
        allow_core_override=False,
        test_jobs=None,
        build_jobs=None,
        source_revision=[f"leaf={REVISION_B}"],
        timing=False,
    )

    code = launcher._run(core_checkout, args_build, {})
    assert code == 0
    assert len(projected_locks) == 1
    passed_revs, candidate_lock = projected_locks[0]
    assert passed_revs == {"leaf": REVISION_B}
    leaf_locked = candidate_lock["nodes"]["leaf"]["locked"]
    assert leaf_locked["rev"] == REVISION_B
    assert leaf_locked["narHash"] == NAR_HASH_B
    assert "shortRev" not in leaf_locked
    assert "lastModifiedDate" not in leaf_locked
    assert "submodules" not in leaf_locked

    args_deploy = argparse.Namespace(
        command="deploy",
        flake=None,
        allow_core_override=False,
        test_jobs=None,
        build_jobs=None,
        source_revision=[f"leaf={REVISION_B}"],
        timing=False,
        dry_run=True,
        plan_only=False,
        output=None,
        plan=None,
    )
    code_deploy = launcher._run(core_checkout, args_deploy, {})
    assert code_deploy == 0
    assert len(projected_locks) == 2
    passed_revs_d, candidate_lock_d = projected_locks[1]
    assert passed_revs_d == {"leaf": REVISION_B}
    leaf_locked_d = candidate_lock_d["nodes"]["leaf"]["locked"]
    assert leaf_locked_d["rev"] == REVISION_B
    assert leaf_locked_d["narHash"] == NAR_HASH_B
    assert "shortRev" not in leaf_locked_d
    assert "lastModifiedDate" not in leaf_locked_d
    assert "submodules" not in leaf_locked_d


def test_project_candidate_lock_preserves_owned_path_and_multi_scheme_children(tmp_path: Path, monkeypatch):
    """Owned child records copied from committed flake.lock retain scheme attributes byte-for-byte."""
    resolved_root = tmp_path.resolve()
    core_dir = resolved_root / "core"
    init_git_repo(core_dir)
    target_dir = resolved_root / "target"
    target_dir.mkdir(parents=True)

    direct_path_locked = {
        "type": "path",
        "path": "./config/default",
    }
    live_path_locked = {
        "type": "path",
        "path": "/Users/example/.local/share/repo-map",
        "narHash": "sha256-3rpMGnFMUgBGRH7ndODxwROowz92XUxjnrO4Kp/q2Bk=",
        "lastModified": 1786904022,
    }
    github_child_locked = {
        "type": "github",
        "owner": "nix-darwin",
        "repo": "nix-darwin",
        "rev": "ebec37af18215214173c98cf6356d0aca24a2585",
        "narHash": "sha256-Utk0zd8STPsUJPyjabhzPc5BpPodLTXrwkpXBHYnpeg=",
        "lastModified": 1772129556,
    }

    core_cand_lock = {
        "nodes": {
            "root": {
                "inputs": {
                    "directPath": "directPath",
                    "repoMap": "repoMap",
                    "nixDarwin": "nixDarwin",
                }
            },
            "directPath": {
                "locked": dict(direct_path_locked),
                "original": {"path": "./config/default", "type": "path"},
            },
            "repoMap": {
                "locked": dict(live_path_locked),
                "original": {"path": "/Users/example/.local/share/repo-map", "type": "path"},
            },
            "nixDarwin": {
                "locked": dict(github_child_locked),
                "original": {"owner": "nix-darwin", "repo": "nix-darwin", "type": "github"},
            },
        },
        "root": "root",
        "version": 7,
    }
    cand_store_path = resolved_root / "core-store-path"
    cand_store_path.mkdir(parents=True)
    (cand_store_path / "flake.lock").write_text(json.dumps(core_cand_lock))

    initial_composition_lock = {
        "nodes": {
            "root": {"inputs": {"core": "core"}},
            "core": {
                "inputs": {
                    "directPath": "directPath",
                    "repoMap": "repoMap",
                    "nixDarwin": "nixDarwin",
                },
                "locked": {
                    "type": "git",
                    "url": "https://example.invalid/core.git",
                    "ref": "refs/heads/main",
                    "rev": REVISION_A,
                    "narHash": NAR_HASH_A,
                },
            },
            "directPath": {
                "locked": dict(direct_path_locked),
            },
            "repoMap": {
                "locked": dict(live_path_locked),
            },
            "nixDarwin": {
                "locked": dict(github_child_locked),
            },
        },
        "root": "root",
        "version": 7,
    }
    (target_dir / "flake.lock").write_text(json.dumps(initial_composition_lock))

    workflow = make_mock_workflow(resolved_root)
    entry = SourceCatalogEntry(
        key="core",
        checkout=core_dir,
        repository="https://example.invalid/core.git",
        release_ref="refs/heads/main",
    )
    workflow.sources = (entry,)

    def mock_materialize(wf, cat_entry, rev, **kwargs):
        return LocalSourceIdentity(
            key="core",
            checkout=str(core_dir),
            repository=cat_entry.repository,
            release_ref=cat_entry.release_ref,
            revision=rev,
            head_ref="refs/heads/main",
            head_revision=REVISION_A,
            clean=True,
            operation_state="clean",
            store_path=str(cand_store_path),
            nar_hash=NAR_HASH_B,
            locked_metadata={
                "lastModified": 1789335288,
                "lastModifiedDate": "20260913213448",
                "narHash": NAR_HASH_B,
                "path": str(cand_store_path),
                "outPath": str(cand_store_path),
                "rev": rev,
                "shortRev": rev[:7],
                "submodules": False,
            },
        )

    monkeypatch.setattr(local_source, "materialize_source", mock_materialize)

    project_candidate_lock(workflow, target_dir, {"core": REVISION_B})

    updated_lock = json.loads((target_dir / "flake.lock").read_text())

    # 1. Selected Git node is sanitized: no fetchTree result-only keys leak
    core_locked = updated_lock["nodes"]["core"]["locked"]
    assert core_locked["rev"] == REVISION_B
    assert core_locked["narHash"] == NAR_HASH_B
    assert "shortRev" not in core_locked
    assert "lastModifiedDate" not in core_locked
    assert "path" not in core_locked
    assert "outPath" not in core_locked

    # 2. Direct path child retains 'path' byte-for-byte
    assert updated_lock["nodes"]["directPath"]["locked"] == direct_path_locked

    # 3. Live-shaped absolute path child retains 'path' and all attributes byte-for-byte
    assert updated_lock["nodes"]["repoMap"]["locked"] == live_path_locked

    # 4. Non-Git non-path child (github) retains all attributes byte-for-byte
    assert updated_lock["nodes"]["nixDarwin"]["locked"] == github_child_locked


def test_real_nix_acceptance_owned_path_lock_regression(tmp_path: Path):
    """Real Nix acceptance test: removing path causes Nix error; fixed projection passes."""
    resolved_root = tmp_path.resolve()

    # 1. Path dependency fixture
    path_dep = resolved_root / "path_dep"
    path_dep.mkdir()
    (path_dep / "flake.nix").write_text('{ description = "path-dep"; outputs = { self }: { value = 100; }; }\n')

    # 2. Overridden Git source owning the path dependency
    leaf_dir = resolved_root / "leaf"
    leaf_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=leaf_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=leaf_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=leaf_dir, check=True)

    (leaf_dir / "flake.nix").write_text(f"""{{
  description = "leaf";
  inputs.pathDep.url = "path:{path_dep}";
  outputs = {{ self, pathDep }}: {{
    x = pathDep.value + 42;
  }};
}}
""")
    subprocess.run(["git", "add", "."], cwd=leaf_dir, check=True)
    subprocess.run(["nix", "flake", "lock"], cwd=leaf_dir, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=leaf_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init leaf"], cwd=leaf_dir, check=True, capture_output=True)
    leaf_rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=leaf_dir, check=True, capture_output=True, text=True).stdout.strip()

    # 3. Target flake importing the Git source
    target_dir = resolved_root / "target"
    target_dir.mkdir()
    (target_dir / "flake.nix").write_text(f"""{{
  description = "target";
  inputs.leaf.url = "git+file://{leaf_dir}?ref=main";
  outputs = {{ self, leaf }}: {{
    result = leaf.x;
  }};
}}
""")
    subprocess.run(["nix", "flake", "lock"], cwd=target_dir, check=True, capture_output=True)

    # Pre-fix baseline: prove that removing 'path' from the path child causes real Nix to fail
    pre_fix_bad_lock = json.loads((target_dir / "flake.lock").read_text())
    pre_fix_bad_lock["nodes"]["pathDep"]["locked"].pop("path", None)
    (target_dir / "flake.lock").write_text(json.dumps(pre_fix_bad_lock))

    res_bad = subprocess.run(
        ["nix", "eval", "--json", "--offline", f"path:{target_dir}#result"],
        capture_output=True,
        text=True,
    )
    assert res_bad.returncode != 0
    assert "input attribute 'path' is missing" in res_bad.stderr or (
        "path" in res_bad.stderr and "missing" in res_bad.stderr
    )

    # Fixed projection restores the exact lock record
    entry = SourceCatalogEntry(
        key="leaf",
        checkout=leaf_dir,
        repository=f"file://{leaf_dir}",
        release_ref="refs/heads/main",
    )
    wf = deployment_workflow.Workflow(resolved_root, sources=(entry,), display_command_output=False)
    project_candidate_lock(wf, target_dir, {"leaf": leaf_rev})

    # Assert path child retained 'path' in projected lock
    projected_doc = json.loads((target_dir / "flake.lock").read_text())
    assert "path" in projected_doc["nodes"]["pathDep"]["locked"]
    assert projected_doc["nodes"]["pathDep"]["locked"]["path"] == str(path_dep)

    # Real offline Nix parse/evaluation succeeds
    res_meta = subprocess.run(
        ["nix", "flake", "metadata", "--no-update-lock-file", "--offline", f"path:{target_dir}"],
        capture_output=True,
        text=True,
    )
    assert res_meta.returncode == 0, f"nix flake metadata failed: {res_meta.stderr}"

    res_eval = subprocess.run(
        ["nix", "eval", "--json", "--offline", f"path:{target_dir}#result"],
        capture_output=True,
        text=True,
    )
    assert res_eval.returncode == 0, f"nix eval failed: {res_eval.stderr}"
    assert json.loads(res_eval.stdout) == 142

    # 4. Changed-revision regression: update leaf to a second commit
    (leaf_dir / "flake.nix").write_text(f"""{{
  description = "leaf";
  inputs.pathDep.url = "path:{path_dep}";
  outputs = {{ self, pathDep }}: {{
    x = pathDep.value + 99;
  }};
}}
""")
    subprocess.run(["git", "add", "."], cwd=leaf_dir, check=True)
    subprocess.run(["nix", "flake", "lock"], cwd=leaf_dir, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=leaf_dir, check=True)
    subprocess.run(["git", "commit", "-m", "update leaf commit 2"], cwd=leaf_dir, check=True, capture_output=True)
    leaf_rev_2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=leaf_dir, check=True, capture_output=True, text=True).stdout.strip()
    assert leaf_rev_2 != leaf_rev

    project_candidate_lock(wf, target_dir, {"leaf": leaf_rev_2})

    res_eval_2 = subprocess.run(
        ["nix", "eval", "--json", "--offline", f"path:{target_dir}#result"],
        capture_output=True,
        text=True,
    )
    assert res_eval_2.returncode == 0, f"nix eval on updated revision failed: {res_eval_2.stderr}"
    assert json.loads(res_eval_2.stdout) == 199


def test_project_candidate_lock_private_full_graph_semantics_and_canonical_bytes(tmp_path: Path):
    """Preserve the installed private lock graph with canonical output and real Nix evaluation."""
    import shutil
    private_root = Path("/Users/operator/.local/nix-darwin/composition-private_dinas")
    config_file = Path("/Users/operator/.local/nix-darwin/config/nix-darwin.toml")
    if not private_root.is_dir() or not (private_root / "flake.lock").is_file() or not config_file.is_file():
        pytest.skip("canonical private composition or config not present in environment")

    orig_lock_bytes = (private_root / "flake.lock").read_bytes()
    orig_lock_doc = json.loads(orig_lock_bytes)
    assert set(orig_lock_doc) == {"nodes", "root", "version"}
    nodes = orig_lock_doc.get("nodes", {})

    cfg = config.load_control_config(config_file)
    catalog = coerce_source_catalog(cfg.sources)

    # Derive source revisions from the canonical private lock itself to test true no-op idempotency
    sources_revs: dict[str, str] = {}
    for key, entry in catalog.by_key.items():
        if key == "composition-private-nd":
            continue
        matching_nodes = []
        for node_name, node_data in nodes.items():
            locked = node_data.get("locked")
            if not isinstance(locked, Mapping) or locked.get("type") != "git":
                continue
            locked_url = locked.get("url")
            if locked_url and repository_urls_match(entry.repository, locked_url, allow_file=True):
                matching_nodes.append(node_name)
        if not matching_nodes and key in nodes:
            matching_nodes = [key]
        if len(matching_nodes) == 1:
            target_node = matching_nodes[0]
            locked_info = nodes[target_node].get("locked", {})
            rev = locked_info.get("rev")
            if rev:
                sources_revs[key] = rev

    # Skip gracefully if host environment checkouts are missing or do not contain the revisions
    for key, rev in sources_revs.items():
        entry = catalog[key]
        if not entry.checkout.is_dir():
            pytest.skip(f"source checkout for {key} not present at {entry.checkout}")
        res = subprocess.run(
            ["git", "cat-file", "-e", f"{rev}^{{commit}}"],
            cwd=entry.checkout,
            capture_output=True,
        )
        if res.returncode != 0:
            pytest.skip(f"source checkout {key} does not contain revision {rev}")

    wf = deployment_workflow.Workflow(CORE_ROOT, sources=cfg.sources, display_command_output=False)

    target_dir = tmp_path / "private_comp"
    shutil.copytree(private_root, target_dir, ignore=shutil.ignore_patterns(".git"))

    project_candidate_lock(wf, target_dir, sources_revs)

    projected_bytes = (target_dir / "flake.lock").read_bytes()
    assert json.loads(projected_bytes) == orig_lock_doc
    assert projected_bytes == serialize_lock_document(orig_lock_doc)

    # Record campaign provenance: when executed against the original baseline, verify expected properties
    expected_campaign_sha = "9c82c0299abf3063084044455974549565c96e4d05f3d7454e1eda3a7097cf45"
    if hashlib.sha256(orig_lock_bytes).hexdigest() == expected_campaign_sha:
        assert len(sources_revs) == 8

    res_eval = subprocess.run(
        ["nix", "eval", "--json", "path:.#configAllowlist", "--no-update-lock-file", "--no-write-lock-file", "--offline"],
        cwd=target_dir,
        capture_output=True,
        text=True,
    )
    assert res_eval.returncode == 0, f"configAllowlist evaluation failed: {res_eval.stderr}"
    allowlist = json.loads(res_eval.stdout)
    assert isinstance(allowlist, dict)


def test_project_candidate_lock_canonicalizes_noncanonical_git_leaf(tmp_path: Path):
    """A real materialized revision rewrites formatting without changing graph data."""
    root = tmp_path.resolve()
    leaf = root / "leaf"
    revision = init_git_repo(leaf)
    target = root / "target"
    target.mkdir()
    (target / "flake.nix").write_text(
        f'{{ inputs.leaf.url = "git+file://{leaf}?ref=main"; '
        'outputs = { self, leaf }: {}; }\n'
    )
    subprocess.run(["nix", "flake", "lock"], cwd=target, check=True, capture_output=True)
    lock = target / "flake.lock"
    original = json.loads(lock.read_bytes())
    noncanonical = (json.dumps(original, indent=4) + "\n\n").encode()
    canonical = serialize_lock_document(original)
    assert noncanonical != canonical
    lock.write_bytes(noncanonical)
    entry = SourceCatalogEntry(
        key="leaf", checkout=leaf, repository=f"file://{leaf}",
        release_ref="refs/heads/main",
    )
    workflow = deployment_workflow.Workflow(root, sources=(entry,), display_command_output=False)

    project_candidate_lock(workflow, target, {"leaf": revision})

    assert json.loads(lock.read_bytes()) == original
    assert lock.read_bytes() == canonical


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ("aligned", None),
        ("detached-exact", None),
        ("missing-release-ref", "release ref .* does not exist"),
        ("release-ref-moved", "release ref .* expected requested revision"),
        ("other-head-ref", "HEAD ref .* does not match requested ref"),
        ("detached-other", "HEAD is detached at unrelated commit"),
    ],
)
def test_source_exact_head_authority_checks_real_git_refs(tmp_path, state, message):
    checkout = tmp_path / "leaf"
    revision = init_git_repo(checkout)

    def git(*args):
        return subprocess.run(["git", *args], cwd=checkout, check=True, capture_output=True, text=True)

    if state in {"detached-exact", "missing-release-ref", "detached-other"}:
        git("checkout", "--detach", revision)
    if state == "missing-release-ref":
        git("branch", "-D", "main")
    elif state == "other-head-ref":
        git("checkout", "-b", "other")
    elif state in {"release-ref-moved", "detached-other"}:
        (checkout / "README.md").write_text("second revision\n")
        git("add", "README.md")
        git("commit", "-m", "second")
    before = git("rev-parse", "HEAD").stdout.strip()
    entry = SourceCatalogEntry(
        key="leaf", checkout=checkout, repository=checkout.as_uri(),
        release_ref="refs/heads/main",
    )
    workflow = make_mock_workflow(tmp_path)
    if message:
        with pytest.raises(SourceError, match=message):
            observe_source(workflow, entry, revision, require_exact_head=True)
    else:
        observed = observe_source(workflow, entry, revision, require_exact_head=True)
        assert observed.revision == observed.head_revision == revision
    assert git("rev-parse", "HEAD").stdout.strip() == before
    assert git("status", "--porcelain").stdout == ""


@pytest.mark.parametrize("order", [("child", "parent"), ("parent", "child")])
def test_explicit_nested_pin_precedence(tmp_path, monkeypatch, order):
    """A nested adapter B supplies its own dependency, regardless of parent A."""
    from types import SimpleNamespace
    import itertools
    parent, child = "composition-nd", "agentic-praxis-grimoire_dinas"
    def locked(name, rev=REVISION_A):
        return {"type": "git", "url": f"https://example.invalid/{name}.git",
                "rev": rev, "narHash": NAR_HASH_A}
    initial = {"version": 7, "root": "root", "nodes": {
        "root": {"inputs": {"parent": parent}},
        parent: {"locked": locked(parent), "inputs": {"adapter": child, "loose": "loose"}},
        child: {"locked": locked(child), "inputs": {"dependency": "dep"}},
        "dep": {"locked": locked("dep")},
        "loose": {"locked": {"type": "path", "path": "./old"}},
    }}
    parent_doc = {"version": 7, "root": "root", "nodes": {
        "root": {"inputs": {"adapter": child, "loose": "renamed"}},
        child: {"locked": locked(child), "inputs": {"dependency": "dep"}},
        "dep": {"locked": locked("dep")},
        "renamed": {"locked": {"type": "path", "path": "./committed"}},
    }}
    child_doc = {"version": 7, "root": "root", "nodes": {
        "root": {"inputs": {"dependency": "renamed-dep"}},
        "renamed-dep": {"locked": locked("dep", REVISION_C)},
    }}
    mats = {}
    for name, doc in [(parent, parent_doc), (child, child_doc), ("dep", {"nodes": {"root": {}}, "root": "root", "version": 7})]:
        store = tmp_path / name
        store.mkdir()
        (store / "flake.lock").write_text(json.dumps(doc))
        mats[name] = store
    workflow = make_mock_workflow(tmp_path)
    workflow.sources = tuple(SourceCatalogEntry(key=name, checkout=mats[name],
        repository=f"https://example.invalid/{name}.git", release_ref="refs/heads/main") for name in mats)
    calls = []
    def materialize(wf, entry, rev):
        calls.append(entry.key)
        return SimpleNamespace(revision=rev, nar_hash=NAR_HASH_B, store_path=str(mats[entry.key]),
            locked_metadata={"rev": rev, "narHash": NAR_HASH_B, "lastModified": 42, "revCount": 7})
    monkeypatch.setattr(local_source, "materialize_source", materialize)
    keys = {"parent": parent, "child": child}
    orders = [tuple(keys[k] for k in order), tuple(keys[k] for k in reversed(order))]
    outputs = []
    target = tmp_path / "target"
    target.mkdir()
    for permutation in orders:
        (target / "flake.lock").write_text(json.dumps(initial))
        calls.clear()
        project_candidate_lock(workflow, target, dict.fromkeys(permutation, REVISION_B))
        assert sorted(calls) == sorted([parent, child])
        outputs.append((target / "flake.lock").read_bytes())
        nodes = json.loads(outputs[-1])["nodes"]
        assert nodes[child]["locked"] == {**locked(child, REVISION_B), "narHash": NAR_HASH_B,
                                         "lastModified": 42, "revCount": 7}
        assert nodes["dep"]["locked"] == locked("dep", REVISION_C)
        assert nodes["loose"]["locked"] == {"type": "path", "path": "./committed"}
    assert outputs[0] == outputs[1]
    # All six three-level explicit permutations share one canonical result.
    outputs.clear()
    for permutation in itertools.permutations(mats):
        (target / "flake.lock").write_text(json.dumps(initial))
        project_candidate_lock(workflow, target, dict.fromkeys(permutation, REVISION_B))
        outputs.append((target / "flake.lock").read_bytes())
        assert json.loads(outputs[-1])["nodes"]["dep"]["locked"]["rev"] == REVISION_B
    assert len(set(outputs)) == 1


@pytest.mark.parametrize("defect", ["ambiguous", "conflict", "revision", "metadata", "missing", "unknown", "wrong-repository"])
def test_explicit_projection_fails_without_writing(tmp_path, monkeypatch, defect):
    from types import SimpleNamespace
    workflow = make_mock_workflow(tmp_path)
    entry = SourceCatalogEntry(key="leaf", checkout=tmp_path, repository="https://example.invalid/leaf.git",
                               release_ref="refs/heads/main")
    workflow.sources = (entry,)
    node = {"locked": {"type": "git", "url": entry.repository, "rev": REVISION_A, "narHash": NAR_HASH_A}}
    doc = {"nodes": {"root": {"inputs": {"leaf": "leaf"}}, "leaf": node}, "root": "root", "version": 7}
    revisions = {"leaf": REVISION_B}
    if defect == "wrong-repository":
        node["locked"]["url"] = "https://example.invalid/unrelated.git"
    if defect == "ambiguous":
        doc["nodes"]["duplicate"] = node
    if defect == "conflict":
        workflow.sources += (SourceCatalogEntry(key="alias", checkout=tmp_path, repository=entry.repository,
                                                release_ref="refs/heads/main"),)
        revisions["alias"] = REVISION_C
    if defect == "missing":
        del doc["nodes"]["leaf"]
    if defect == "unknown":
        revisions = {"unknown": REVISION_B}
    monkeypatch.setattr(local_source, "materialize_source", lambda *a: SimpleNamespace(
        revision=REVISION_B, nar_hash=NAR_HASH_B, store_path=None))
    if defect in {"revision", "metadata"}:
        real_copy = local_source.copy_serialized_locked_record
        def drift(record):
            result = real_copy(record)
            result["rev" if defect == "revision" else "narHash"] = REVISION_C if defect == "revision" else NAR_HASH_A
            return result
        monkeypatch.setattr(local_source, "copy_serialized_locked_record", drift)
    path = tmp_path / "flake.lock"
    before = json.dumps(doc).encode()
    path.write_bytes(before)
    with pytest.raises(ConfigError):
        project_candidate_lock(workflow, tmp_path, revisions)
    assert path.read_bytes() == before


@pytest.mark.parametrize("dry_run", [False, True])
def test_plan_only_missing_output_precedes_source_and_state_work(tmp_path, monkeypatch, dry_run):
    def forbidden(*a, **kw):
        pytest.fail("expensive deployment boundary invoked")
    monkeypatch.setattr(local_deployment, "ensure_private_root", forbidden)
    monkeypatch.setattr(local_deployment, "local_git_identity", forbidden)
    monkeypatch.setattr(local_deployment, "inspect_profile", forbidden)
    monkeypatch.setattr(local_source, "materialize_source", forbidden)
    monkeypatch.setattr(config, "resolve_effective_source_revisions", lambda *a: (_ for _ in ()).throw(ConfigError("config sentinel")))
    message = "config sentinel" if dry_run else "--plan-only requires --output"
    with pytest.raises(ConfigError, match=message):
        local_deployment.deploy_local(make_mock_workflow(tmp_path), tmp_path, "host", tmp_path,
                                      [], "1", "1", plan_only=True, dry_run=dry_run)


@pytest.mark.parametrize("scheme", [
    {"type": "path", "path": "./nested", "parent": []},
    {"type": "github", "owner": "fixture", "repo": "dep", "rev": REVISION_C, "narHash": NAR_HASH_A},
    {"type": "gitlab", "owner": "fixture", "repo": "dep", "rev": REVISION_C, "narHash": NAR_HASH_A},
    {"type": "tarball", "url": "https://example.invalid/dep.tar.gz", "narHash": NAR_HASH_A},
])
@pytest.mark.parametrize("case", ["shared", "conflict", "outside", "topology", "missing-lock", "follows", "missing-node"])
def test_owned_projection_shared_authority_and_schemes(tmp_path, monkeypatch, scheme, case):
    from types import SimpleNamespace
    import copy
    workflow = make_mock_workflow(tmp_path)
    def locked(key):
        return {"type": "git", "url": f"https://example.invalid/{key}.git", "rev": REVISION_A, "narHash": NAR_HASH_A}
    nodes = {"root": {"inputs": {"one": "one", "two": "two"}},
             "one": {"locked": locked("one"), "inputs": {"dep": "shared"}},
             "two": {"locked": locked("two"), "inputs": {"dep": "shared"}},
             "shared": {"locked": {"type": "path", "path": "./old"}}}
    stores = {}
    for key in ["one", "two"]:
        store = tmp_path / key
        store.mkdir()
        stores[key] = store
        inner_nodes = {"root": {"inputs": {"dep": "renamed"}}, "renamed": {"locked": copy.deepcopy(scheme)}}
        if case == "conflict" and key == "two":
            inner_nodes["renamed"]["locked"] = {"type": "path", "path": "./conflicting"}
        if case == "topology":
            inner_nodes["root"]["inputs"]["extra"] = "renamed"
        if case == "missing-node":
            del inner_nodes["renamed"]
        if case != "missing-lock":
            (store / "flake.lock").write_text(json.dumps({"root": "root", "version": 7, "nodes": inner_nodes}))
    if case == "outside":
        nodes["root"]["inputs"]["external"] = "shared"
    if case == "follows":
        nodes["one"]["inputs"]["dep"] = ["two", "dep"]
    workflow.sources = tuple(SourceCatalogEntry(key=k, checkout=stores[k], repository=locked(k)["url"],
                                               release_ref="refs/heads/main") for k in stores)
    monkeypatch.setattr(local_source, "materialize_source", lambda wf, entry, rev: SimpleNamespace(
        revision=rev, nar_hash=NAR_HASH_B, store_path=str(stores[entry.key])))
    doc = {"root": "root", "version": 7, "nodes": nodes}
    before = json.dumps(doc).encode()
    path = tmp_path / "flake.lock"
    results = []
    for order in [("one", "two"), ("two", "one")]:
        path.write_bytes(before)
        if case not in {"shared", "follows"}:
            with pytest.raises(ConfigError):
                project_candidate_lock(workflow, tmp_path, dict.fromkeys(order, REVISION_B))
            assert path.read_bytes() == before
        else:
            project_candidate_lock(workflow, tmp_path, dict.fromkeys(order, REVISION_B))
            results.append(path.read_bytes())
            assert json.loads(results[-1])["nodes"]["shared"]["locked"] == scheme
    if results:
        assert results[0] == results[1]


@pytest.mark.parametrize("records", [
    ({"type": "git", "lastModified": 1}, {"type": "git", "lastModified": True}),
    ({"type": "git", "lastModified": 1}, {"type": "git", "lastModified": 1.0}),
])
def test_projection_conflict_compares_exact_json_records(records):
    """Python numeric equality must not make distinct lock bytes last-writer-wins."""
    for first, second in (records, tuple(reversed(records))):
        merged = {}
        local_source._merge_projected_record(merged, "shared", first)
        with pytest.raises(ConfigError, match="contradictory projected authority"):
            local_source._merge_projected_record(merged, "shared", second)
