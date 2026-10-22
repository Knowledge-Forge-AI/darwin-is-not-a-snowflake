# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_source.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_topology.py
"""Integration tests for observational core source stage and truthful source receipt.

Validates:
1. Observational source receipt permits untracked dirt (.serena, bin/claude, etc.)
2. Detects tracked engine drift (modifications to tracked tool files)
3. Proves requested lowercase 40-hex revision exists as a commit (allows revision older than HEAD)
4. Strict canonical roundtrip and digest validation (fails on non-canonical bytes or tampered digest)
5. Integration with prepare_campaign_stage delegation for core stages
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local.deployment_workflow import Workflow
from nix_darwin_local.paths import ConfigError
from nix_darwin_local.promotion_source import (
    SOURCE_RECEIPT_SCHEMA,
    SourceReceipt,
    load_source_receipt,
    prepare_core_source,
    validate_source_receipt,
    write_source_receipt,
)
from nix_darwin_local.promotion_topology import prepare_campaign_stage

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("nix") is None,
    reason="required git or nix executable is unavailable",
)


def _run(cwd: Path, *argv: str) -> str:
    result = subprocess.run(
        list(argv),
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def _init_core_repo(path: Path) -> str:
    """Initialize a real Git repository with minimal valid core tool files."""
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "git", "init", "--initial-branch=main")
    _run(path, "git", "config", "user.name", "Source Test Fixture")
    _run(path, "git", "config", "user.email", "source-fixture@example.invalid")

    # flake.nix
    (path / "flake.nix").write_text("{ outputs = { self }: { }; }\n", encoding="utf-8")

    # bin/nix-update (executable)
    bin_dir = path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    nix_update = bin_dir / "nix-update"
    nix_update.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    nix_update.chmod(0o755)

    # libexec/nix_darwin_local tool files
    libexec = path / "libexec/nix_darwin_local"
    libexec.mkdir(parents=True, exist_ok=True)
    (libexec / "__init__.py").write_text('"""test package"""\n', encoding="utf-8")
    (libexec / "paths.py").write_text('"""test paths"""\n', encoding="utf-8")

    _run(path, "git", "add", "-A")
    _run(path, "git", "commit", "-m", "initial core tool files")
    return _run(path, "git", "rev-parse", "HEAD")


def test_source_receipt_dirt_permits_untracked_files(tmp_path: Path):
    """Untracked dirt (.serena, bin/claude, etc.) does not prevent observational receipt."""
    repo = tmp_path / "core_repo"
    commit_1 = _init_core_repo(repo)

    # Introduce untracked dirt: .serena, bin/claude, and arbitrary untracked scratch
    serena_dir = repo / ".serena"
    serena_dir.mkdir(parents=True, exist_ok=True)
    (serena_dir / "state.json").write_text('{"untracked": true}\n', encoding="utf-8")

    claude_bin = repo / "bin/claude"
    claude_bin.write_text("#!/bin/sh\necho claude\n", encoding="utf-8")
    claude_bin.chmod(0o755)

    scratch_file = repo / "scratch_dirt.txt"
    scratch_file.write_text("random dirt\n", encoding="utf-8")

    workflow = Workflow(repo, display_command_output=False)
    out_dir = tmp_path / "out_dirt"
    cfg = SimpleNamespace()

    receipt = prepare_core_source(
        workflow,
        repo,
        "core",
        commit_1,
        out_dir,
        cfg,
        {"kind": "core"},
    )

    assert isinstance(receipt, SourceReceipt)
    assert receipt.document["status"] == "ALREADY_SELECTED"
    assert receipt.document["revision"] == commit_1
    assert receipt.document["repository"]["commit"] == commit_1
    assert receipt.document["repository"]["head_commit"] == commit_1
    assert receipt.document["impact"] is None
    assert receipt.document["no_activation"] is True
    assert receipt.document["schema"] == SOURCE_RECEIPT_SCHEMA
    assert receipt.document["schema"] != "nix-update-promotion-plan-v2"
    assert isinstance(receipt.document["tool"], dict)
    assert receipt.digest == receipt.document["receipt_digest"]

    # Verify untracked dirt remained untouched
    assert (serena_dir / "state.json").is_file()
    assert claude_bin.is_file()
    assert scratch_file.is_file()


def test_source_receipt_detects_tracked_tool_drift(tmp_path: Path):
    """Modifying tracked tool files triggers tracked engine drift detection."""
    repo = tmp_path / "drift_repo"
    commit_1 = _init_core_repo(repo)

    workflow = Workflow(repo, display_command_output=False)
    out_dir = tmp_path / "out_drift"
    cfg = SimpleNamespace()

    # Modify a tracked file inside libexec/nix_darwin_local
    paths_file = repo / "libexec/nix_darwin_local/paths.py"
    paths_file.write_text('"""drifted paths content"""\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="tracked tool drift detected"):
        prepare_core_source(
            workflow,
            repo,
            "core",
            commit_1,
            out_dir,
            cfg,
            {"kind": "core"},
        )

    # Revert the libexec drift
    _run(repo, "git", "checkout", "HEAD", "--", "libexec/nix_darwin_local/paths.py")

    # Now modify bin/nix-update
    nix_update = repo / "bin/nix-update"
    nix_update.write_text("#!/bin/sh\n# drifted nix-update\nexit 1\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="tracked tool drift detected"):
        prepare_core_source(
            workflow,
            repo,
            "core",
            commit_1,
            out_dir,
            cfg,
            {"kind": "core"},
        )

    # Revert bin/nix-update
    _run(repo, "git", "checkout", "HEAD", "--", "bin/nix-update")

    # Modifying a non-tool tracked file (e.g. flake.nix) does not trigger tool drift
    (repo / "flake.nix").write_text("{ outputs = { self }: { modified = true; }; }\n", encoding="utf-8")
    clean_receipt = prepare_core_source(
        workflow,
        repo,
        "core",
        commit_1,
        out_dir,
        cfg,
        {"kind": "core"},
    )
    assert clean_receipt.document["status"] == "ALREADY_SELECTED"


def test_source_receipt_allows_requested_revision_older_than_head(tmp_path: Path):
    """Requested revision may be older than execution HEAD."""
    repo = tmp_path / "older_repo"
    commit_1 = _init_core_repo(repo)

    # Create a second commit (newer execution HEAD, e.g. bugfix or tool update)
    (repo / "CHANGELOG.md").write_text("# Changelog\nFix applied.\n", encoding="utf-8")
    _run(repo, "git", "add", "CHANGELOG.md")
    _run(repo, "git", "commit", "-m", "update tools/changelog")
    commit_2 = _run(repo, "git", "rev-parse", "HEAD")

    assert commit_1 != commit_2

    workflow = Workflow(repo, display_command_output=False)
    out_dir = tmp_path / "out_older"
    cfg = SimpleNamespace()

    receipt = prepare_core_source(
        workflow,
        repo,
        "core",
        commit_1,
        out_dir,
        cfg,
        {"kind": "core"},
    )

    assert receipt.document["status"] == "ALREADY_SELECTED"
    assert receipt.document["revision"] == commit_1
    assert receipt.document["repository"]["commit"] == commit_1
    assert receipt.document["repository"]["head_commit"] == commit_2


def test_source_receipt_strict_roundtrip_and_digest_validation(tmp_path: Path):
    """Source receipt writer and loader enforce canonical bytes, digest match, and truthful schema."""
    repo = tmp_path / "roundtrip_repo"
    commit_1 = _init_core_repo(repo)

    workflow = Workflow(repo, display_command_output=False)
    out_dir = tmp_path / "out_roundtrip"
    cfg = SimpleNamespace()

    receipt = prepare_core_source(
        workflow,
        repo,
        "core",
        commit_1,
        out_dir,
        cfg,
        {"kind": "core"},
    )

    # Load from directory
    loaded_dir = load_source_receipt(out_dir)
    assert loaded_dir.digest == receipt.digest
    assert loaded_dir.document == receipt.document

    # Load from file directly
    receipt_file = out_dir / "receipt.json"
    assert receipt_file.is_file()
    loaded_file = load_source_receipt(receipt_file)
    assert loaded_file.digest == receipt.digest
    assert loaded_file.document == receipt.document

    # Tamper with document content without updating digest (write with canonical bytes)
    from nix_darwin_local.deployment_plan import canonical_bytes
    tampered_doc = copy.deepcopy(receipt.document)
    tampered_doc["revision"] = "b" * 40
    tampered_doc["repository"]["commit"] = "b" * 40
    receipt_file.write_bytes(canonical_bytes(tampered_doc))

    with pytest.raises(ConfigError, match="digest does not match canonical content"):
        load_source_receipt(out_dir)

    # Tamper with digest
    tampered_doc_2 = copy.deepcopy(receipt.document)
    tampered_doc_2["receipt_digest"] = "f" * 64
    receipt_file.write_bytes(canonical_bytes(tampered_doc_2))

    with pytest.raises(ConfigError, match="digest does not match canonical content"):
        load_source_receipt(out_dir)

    # Non-canonical formatting (added spaces/newlines)
    non_canonical = json.dumps(receipt.document, indent=2).encode("utf-8")
    receipt_file.write_bytes(non_canonical)

    with pytest.raises(ConfigError, match="bytes are not canonical"):
        load_source_receipt(out_dir)

    # Test schema rejection: native promotion plan schema is forbidden
    forbidden_doc = copy.deepcopy(receipt.document)
    forbidden_doc["schema"] = "nix-update-promotion-plan-v2"
    with pytest.raises(ConfigError, match="schema must not be a native promotion plan schema"):
        validate_source_receipt(forbidden_doc)

    # Test impact rejection: impact must be null
    impact_doc = copy.deepcopy(receipt.document)
    impact_doc["impact"] = {"classification": "HOST_DERIVATIONS_IDENTICAL"}
    with pytest.raises(ConfigError, match="impact must be null"):
        validate_source_receipt(impact_doc)


def test_source_receipt_git_verification_defects(tmp_path: Path):
    """Proving revision fails when format is invalid, commit does not exist, or object is not a commit."""
    repo = tmp_path / "defects_repo"
    commit_1 = _init_core_repo(repo)

    workflow = Workflow(repo, display_command_output=False)
    out_dir = tmp_path / "out_defects"
    cfg = SimpleNamespace()

    # 1. Invalid revision format (not 40-hex lowercase)
    with pytest.raises(ConfigError, match="40-character lowercase hex"):
        prepare_core_source(workflow, repo, "core", "not-a-hash", out_dir, cfg, {"kind": "core"})

    with pytest.raises(ConfigError, match="40-character lowercase hex"):
        prepare_core_source(workflow, repo, "core", "A" * 40, out_dir, cfg, {"kind": "core"})

    # 2. Non-existent 40-hex revision
    missing_rev = "0" * 40
    with pytest.raises(ConfigError, match="does not contain requested revision"):
        prepare_core_source(workflow, repo, "core", missing_rev, out_dir, cfg, {"kind": "core"})

    # 3. Valid Git object that is a tree, not a commit
    tree_hash = _run(repo, "git", "rev-parse", "HEAD^{tree}")
    with pytest.raises(ConfigError, match="is a tree, not a commit"):
        prepare_core_source(workflow, repo, "core", tree_hash, out_dir, cfg, {"kind": "core"})


def test_prepare_campaign_stage_core_integration(tmp_path: Path):
    """prepare_campaign_stage delegates kind=core to promotion_source producing a truthful receipt."""
    repo = tmp_path / "campaign_core_repo"
    commit_1 = _init_core_repo(repo)

    workflow = Workflow(repo, display_command_output=False)
    out_dir = tmp_path / "out_campaign_stage"
    cfg = SimpleNamespace(allow_local_fixture=True)

    stage = {
        "name": "core",
        "kind": "core",
        "repository": repo,
        "input": "core",
        "revision": commit_1,
    }

    artifact = prepare_campaign_stage(
        workflow,
        repo,
        "core",
        commit_1,
        out_dir,
        cfg,
        stage,
    )

    assert isinstance(artifact, SourceReceipt)
    assert artifact.document["status"] == "ALREADY_SELECTED"
    assert artifact.document["schema"] == SOURCE_RECEIPT_SCHEMA
    assert artifact.document["revision"] == commit_1
    assert artifact.document["impact"] is None
    assert artifact.document["no_activation"] is True

    loaded = load_source_receipt(artifact.directory)
    assert loaded.digest == artifact.digest
    assert loaded.document == artifact.document


@pytest.mark.parametrize("index_flag", ["--assume-unchanged", "--skip-worktree"])
def test_source_tool_bytes_cannot_hide_behind_index_flags(tmp_path, index_flag):
    repo = tmp_path / "source"
    revision = _init_core_repo(repo)
    relative = "libexec/nix_darwin_local/paths.py"
    _run(repo, "git", "update-index", index_flag, relative)
    (repo / relative).write_text("# concealed tracked drift\n")
    with pytest.raises(ConfigError, match="tracked tool drift"):
        prepare_core_source(Workflow(repo), repo, "core", revision, tmp_path / "receipt", None, {"kind": "core"})


@pytest.mark.parametrize("defect", ["extra", "missing", "tool", "input", "artifact"])
def test_source_receipt_rejects_unknown_or_missing_authority(tmp_path, defect):
    from nix_darwin_local.deployment_plan import canonical_bytes, sha256_bytes
    repo = tmp_path / "source"
    revision = _init_core_repo(repo)
    artifact = prepare_core_source(Workflow(repo), repo, "core", revision, tmp_path / "receipt", None, {"kind": "core"})
    document = copy.deepcopy(artifact.document)
    if defect == "extra":
        document["trusted"] = True
    elif defect == "missing":
        del document["execution_repository"]
    elif defect == "tool":
        document["tool"]["files"] = []
    elif defect == "input":
        document["input"] = "other"
    else:
        (artifact.directory / "plan.json").write_text("{}")
    document["receipt_digest"] = ""
    document["receipt_digest"] = sha256_bytes(canonical_bytes(document))
    (artifact.directory / "receipt.json").write_bytes(canonical_bytes(document))
    with pytest.raises(ConfigError):
        load_source_receipt(artifact.directory)


def test_source_receipt_binds_execution_tool_not_selected_checkout(tmp_path):
    selected, execution = tmp_path / "selected", tmp_path / "execution"
    revision = _init_core_repo(selected)
    _init_core_repo(execution)
    (execution / "libexec/nix_darwin_local/paths.py").write_text("# actual running tool drift\n")
    with pytest.raises(ConfigError, match="tracked tool drift"):
        prepare_core_source(Workflow(execution), selected, "core", revision, tmp_path / "receipt", None, {"kind": "core"})
