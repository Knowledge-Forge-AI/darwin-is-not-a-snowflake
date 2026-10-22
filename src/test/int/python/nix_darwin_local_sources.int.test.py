# nix-darwin-test-source-target: libexec/nix_darwin_local/local_source.py
"""Integration tests for DINAS-SOURCES1 local Git source observation and materialization.

Validates:
1. Strict local observation without remote queries when remote is unavailable.
2. Rejection of tracked drift (staged and unstaged) while permitting untracked Serena / tool dirt.
3. Rejection of stale refs and detached unrelated HEADs.
4. Rejection of active Git operations (MERGE_HEAD, rebase, cherry-pick, etc.).
5. Immutable source materialization via Nix fetchTree --offline into /nix/store.
6. Rejection of source mutations after snapshot.
7. Portable lock offline evaluation where unreachable canonical URLs evaluate from store.
8. Enforced agreement of selected ref authority and recorded narHash (no invented hashes).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local.deployment_workflow import Workflow
from nix_darwin_local.local_source import (
    SourceCatalogEntry,
    check_git_operations,
    materialize_source,
    observe_source,
    realize_catalog_locked_nodes,
    validate_source_identity,
)
from nix_darwin_local.paths import ConfigError
from nix_darwin_local.source_catalog import SourceCatalog
from nix_darwin_local.source_catalog import SourceCatalogEntry as RealEntry

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("nix") is None,
    reason="required git or nix executable is unavailable",
)


def _run(cwd: Path, *argv: str) -> str:
    result = subprocess.run(
        list(argv),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_flake_repo(path: Path, *, branch: str = "main") -> str:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "git", "init", f"--initial-branch={branch}")
    _run(path, "git", "config", "user.name", "Local Source Fixture")
    _run(path, "git", "config", "user.email", "fixture@example.invalid")

    flake_content = '{ outputs = { self }: { greeting = "hello-dinas"; }; }\n'
    (path / "flake.nix").write_text(flake_content, encoding="utf-8")
    (path / "README.md").write_text("# Test Repo\n", encoding="utf-8")

    _run(path, "git", "add", "-A")
    _run(path, "git", "commit", "-m", "initial commit")
    return _run(path, "git", "rev-parse", "HEAD")


def test_observe_source_success(tmp_path: Path):
    repo = tmp_path / "test_repo"
    rev = _init_flake_repo(repo)
    index = repo / ".git/index"
    index_before = (index.read_bytes(), index.stat().st_mtime_ns)
    workflow = Workflow(repo, display_command_output=False)

    entry = SourceCatalogEntry(
        key="test-key",
        checkout=repo,
        repository="https://github.com/example/test-repo.git",
        release_ref="refs/heads/main",
    )

    identity = observe_source(workflow, entry, rev)
    assert identity.key == "test-key"
    assert identity.revision == rev
    assert identity.head_revision == rev
    assert identity.head_ref == "refs/heads/main"
    assert identity.release_ref == "refs/heads/main"
    assert identity.clean is True
    assert identity.operation_state == "clean"
    assert identity.as_dict()["key"] == "test-key"
    assert dict(identity)["revision"] == rev
    assert (index.read_bytes(), index.stat().st_mtime_ns) == index_before


def test_local_exact_source_authority_remote_unavailable(tmp_path: Path):
    repo = tmp_path / "offline_repo"
    rev = _init_flake_repo(repo)
    _run(repo, "git", "remote", "add", "origin", "https://unreachable.invalid/offline/repo.git")

    workflow = Workflow(repo, display_command_output=False)
    entry = SourceCatalogEntry(
        key="offline-entry",
        checkout=repo,
        repository="https://unreachable.invalid/offline/repo.git",
        release_ref="refs/heads/main",
    )

    identity = observe_source(
        workflow,
        entry,
        rev,
        expected_repository="https://unreachable.invalid/offline/repo.git",
        expected_ref="refs/heads/main",
    )
    assert identity.revision == rev

    mat = materialize_source(workflow, entry, rev)
    assert mat.revision == rev
    assert mat.store_path is not None and mat.store_path.startswith("/nix/store/")
    assert mat.nar_hash is not None and mat.nar_hash.startswith("sha256-")
    assert Path(mat.store_path).exists()


def test_observe_source_tracked_drift_unstaged_rejection(tmp_path: Path):
    repo = tmp_path / "drift_unstaged"
    rev = _init_flake_repo(repo)
    (repo / "README.md").write_text("# Modified Content\n", encoding="utf-8")

    workflow = Workflow(repo, display_command_output=False)
    entry = SourceCatalogEntry(
        key="drift-unstaged",
        checkout=repo,
        repository="https://github.com/example/test-repo.git",
        release_ref="refs/heads/main",
    )

    with pytest.raises(ConfigError, match="tracked drift"):
        observe_source(workflow, entry, rev, require_clean=True)
    identity = observe_source(workflow, entry, rev)
    assert identity.clean is False


def test_observe_source_tracked_drift_staged_rejection(tmp_path: Path):
    repo = tmp_path / "drift_staged"
    rev = _init_flake_repo(repo)
    (repo / "README.md").write_text("# Staged Content\n", encoding="utf-8")
    _run(repo, "git", "add", "README.md")

    workflow = Workflow(repo, display_command_output=False)
    entry = SourceCatalogEntry(
        key="drift-staged",
        checkout=repo,
        repository="https://github.com/example/test-repo.git",
        release_ref="refs/heads/main",
    )

    with pytest.raises(ConfigError, match="tracked drift"):
        observe_source(workflow, entry, rev, require_clean=True)
    identity = observe_source(workflow, entry, rev)
    assert identity.clean is False


def test_observe_source_untracked_and_ignored_serena_dirt_permitted(tmp_path: Path):
    repo = tmp_path / "serena_dirt"
    rev = _init_flake_repo(repo)

    serena_dir = repo / ".serena"
    serena_dir.mkdir(parents=True, exist_ok=True)
    (serena_dir / "cache.json").write_text('{"serena": "active"}\n', encoding="utf-8")

    bin_dir = repo / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    claude = bin_dir / "claude"
    claude.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    (repo / "untracked_notes.tmp").write_text("scratch\n", encoding="utf-8")

    workflow = Workflow(repo, display_command_output=False)
    entry = SourceCatalogEntry(
        key="serena-entry",
        checkout=repo,
        repository="https://github.com/example/test-repo.git",
        release_ref="refs/heads/main",
    )

    identity = observe_source(workflow, entry, rev)
    assert identity.revision == rev
    assert identity.clean is True


def test_observe_source_stale_ref_rejection(tmp_path: Path):
    repo = tmp_path / "stale_repo"
    rev1 = _init_flake_repo(repo)
    (repo / "README.md").write_text("# Update\n", encoding="utf-8")
    _run(repo, "git", "add", "-A")
    _run(repo, "git", "commit", "-m", "second commit")
    rev2 = _run(repo, "git", "rev-parse", "HEAD")
    assert rev1 != rev2

    workflow = Workflow(repo, display_command_output=False)
    entry = SourceCatalogEntry(
        key="stale-entry",
        checkout=repo,
        repository="https://github.com/example/test-repo.git",
        release_ref="refs/heads/main",
    )

    with pytest.raises(ConfigError, match="resolves to.*expected requested revision"):
        observe_source(workflow, entry, rev1, require_exact_head=True)
    identity = observe_source(workflow, entry, rev1)
    assert identity.revision == rev1
    assert identity.head_revision == rev2


def test_observe_source_detached_unrelated_head_rejection(tmp_path: Path):
    repo = tmp_path / "detached_repo"
    rev1 = _init_flake_repo(repo)
    (repo / "README.md").write_text("# Second\n", encoding="utf-8")
    _run(repo, "git", "add", "-A")
    _run(repo, "git", "commit", "-m", "second commit")
    rev2 = _run(repo, "git", "rev-parse", "HEAD")

    _run(repo, "git", "checkout", rev1)

    workflow = Workflow(repo, display_command_output=False)
    entry = SourceCatalogEntry(
        key="detached-entry",
        checkout=repo,
        repository="https://github.com/example/test-repo.git",
        release_ref="refs/heads/main",
    )

    with pytest.raises(ConfigError, match="detached.*unrelated"):
        observe_source(workflow, entry, rev2, require_exact_head=True)
    identity = observe_source(workflow, entry, rev2)
    assert identity.revision == rev2


def test_observe_source_detached_at_requested_commit_accepted(tmp_path: Path):
    repo = tmp_path / "detached_match_repo"
    rev = _init_flake_repo(repo)
    _run(repo, "git", "checkout", rev)

    workflow = Workflow(repo, display_command_output=False)
    entry = SourceCatalogEntry(
        key="detached-match-entry",
        checkout=repo,
        repository="https://github.com/example/test-repo.git",
        release_ref="refs/heads/main",
    )

    identity = observe_source(workflow, entry, rev)
    assert identity.revision == rev
    assert identity.head_revision == rev


def test_observe_source_active_git_operation_rejection(tmp_path: Path):
    repo = tmp_path / "op_repo"
    rev = _init_flake_repo(repo)

    git_dir = repo / ".git"
    (git_dir / "MERGE_HEAD").write_text(f"{rev}\n", encoding="utf-8")

    workflow = Workflow(repo, display_command_output=False)
    entry = SourceCatalogEntry(
        key="op-entry",
        checkout=repo,
        repository="https://github.com/example/test-repo.git",
        release_ref="refs/heads/main",
    )

    with pytest.raises(ConfigError, match="active Git operation"):
        observe_source(workflow, entry, rev)


def test_observe_source_expected_repository_and_ref_validation(tmp_path: Path):
    repo = tmp_path / "expected_repo"
    rev = _init_flake_repo(repo)
    workflow = Workflow(repo, display_command_output=False)

    entry = SourceCatalogEntry(
        key="val-entry",
        checkout=repo,
        repository="https://github.com/example/test-repo.git",
        release_ref="refs/heads/main",
    )

    with pytest.raises(ConfigError, match="repository URL"):
        observe_source(
            workflow,
            entry,
            rev,
            expected_repository="https://github.com/mismatched/test-repo.git",
        )

    with pytest.raises(ConfigError, match="release ref"):
        observe_source(
            workflow,
            entry,
            rev,
            expected_ref="refs/heads/feature",
        )


def test_observe_source_rejects_shorthand_catalog_ref(tmp_path: Path):
    repo = tmp_path / "shorthand_repo"
    rev = _init_flake_repo(repo)
    workflow = Workflow(repo, display_command_output=False)
    entry = SourceCatalogEntry(
        key="shorthand-entry",
        checkout=repo,
        repository="https://github.com/example/test-repo.git",
        release_ref="main",
    )

    with pytest.raises(ConfigError, match="invalid release ref"):
        observe_source(workflow, entry, rev)


def test_observe_source_rejects_nonordinary_git_checkout(tmp_path: Path):
    repo = tmp_path / "bare_repo"
    _run(tmp_path, "git", "init", "--bare", str(repo))
    workflow = Workflow(repo, display_command_output=False)
    entry = SourceCatalogEntry(
        key="bare-entry",
        checkout=repo,
        repository="https://github.com/example/test-repo.git",
        release_ref="refs/heads/main",
    )

    with pytest.raises(ConfigError, match="ordinary Git worktree"):
        observe_source(workflow, entry, "a" * 40)


@pytest.mark.parametrize("environment_name", [
    "GIT_REPLACE_REF_BASE",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
])
def test_observe_source_rejects_source_git_environment_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment_name: str,
):
    repo = tmp_path / "environment_repo"
    rev = _init_flake_repo(repo)
    monkeypatch.setenv(environment_name, "fixture-injection")
    workflow = Workflow(repo, display_command_output=False)
    entry = SourceCatalogEntry(
        key="environment-entry",
        checkout=repo,
        repository="https://github.com/example/test-repo.git",
        release_ref="refs/heads/main",
    )

    with pytest.raises(ConfigError, match="environment override"):
        observe_source(workflow, entry, rev)
    assert workflow.records == []


@pytest.mark.parametrize("index_flag", ["--assume-unchanged", "--skip-worktree"])
def test_observe_source_raw_tracked_drift_rejects_hidden_index_flags(
    tmp_path: Path, index_flag: str,
):
    repo = tmp_path / "hidden_drift_repo"
    rev = _init_flake_repo(repo)
    _run(repo, "git", "update-index", index_flag, "README.md")
    (repo / "README.md").write_text("# Hidden tracked drift\n", encoding="utf-8")
    workflow = Workflow(repo, display_command_output=False)
    entry = SourceCatalogEntry(
        key="hidden-drift-entry",
        checkout=repo,
        repository="https://github.com/example/test-repo.git",
        release_ref="refs/heads/main",
    )

    with pytest.raises(ConfigError, match="tracked drift"):
        observe_source(workflow, entry, rev, require_clean=True)


def test_observe_source_operation_probe_failure_is_not_treated_as_clean(tmp_path: Path):
    repo = tmp_path / "operation_probe_repo"
    rev = _init_flake_repo(repo)
    inner = Workflow(repo, display_command_output=False)

    class FailingOperationProbe:
        def command(self, label: str, argv: list[str], cwd: Path, **kwargs: Any) -> Any:
            if "Git op check" in label:
                return subprocess.CompletedProcess(argv, 23, "", "probe failed")
            return inner.command(label, argv, cwd, **kwargs)

        def complete_stdout(self, label: str) -> str:
            return inner.complete_stdout(label)

    entry = SourceCatalogEntry(
        key="operation-probe-entry",
        checkout=repo,
        repository="https://github.com/example/test-repo.git",
        release_ref="refs/heads/main",
    )

    with pytest.raises(ConfigError, match="Git operation probe failed"):
        observe_source(FailingOperationProbe(), entry, rev)


def test_check_git_operations_allows_expected_lock_worktree_drift(tmp_path: Path):
    repo = tmp_path / "dirty_lock_repo"
    _init_flake_repo(repo)
    (repo / "README.md").write_text("# lock candidate\n", encoding="utf-8")

    check_git_operations(Workflow(repo, display_command_output=False), repo, "dirty-lock")


@pytest.mark.parametrize("marker", ("index.lock", "HEAD.lock", "packed-refs.lock", "config.lock"))
def test_check_git_operations_rejects_git_lock_markers(tmp_path: Path, marker: str):
    repo = tmp_path / "locked_repo"
    _init_flake_repo(repo)
    (repo / ".git" / marker).write_text("active\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="active Git operation"):
        check_git_operations(Workflow(repo, display_command_output=False), repo, "locked")


def test_observe_source_final_ref_revalidation_detects_race(tmp_path: Path):
    repo = tmp_path / "race_repo"
    rev = _init_flake_repo(repo)
    inner = Workflow(repo, display_command_output=False)

    class RefRacingWorkflow:
        def command(self, label: str, argv: list[str], cwd: Path, **kwargs: Any) -> Any:
            result = inner.command(label, argv, cwd, **kwargs)
            if "Git replacement refs final" in label:
                _run(repo, "git", "commit", "--allow-empty", "-m", "race update")
            return result

        def complete_stdout(self, label: str) -> str:
            return inner.complete_stdout(label)

    entry = SourceCatalogEntry(
        key="race-entry",
        checkout=repo,
        repository="https://github.com/example/test-repo.git",
        release_ref="refs/heads/main",
    )

    with pytest.raises(ConfigError, match="resolves to.*expected requested revision"):
        observe_source(RefRacingWorkflow(), entry, rev, require_exact_head=True)

    # Under SNAPSHOT1 local commit authority, concurrent ref updates do not invalidate
    # observation of the exact selected commit when require_exact_head is False.
    observed = observe_source(RefRacingWorkflow(), entry, rev)
    assert observed.revision == rev


def test_materialize_source_immutable_snapshot(tmp_path: Path):
    repo = tmp_path / "mat_repo"
    rev = _init_flake_repo(repo)
    workflow = Workflow(repo, display_command_output=False)

    entry = SourceCatalogEntry(
        key="mat-entry",
        checkout=repo,
        repository="https://github.com/example/test-repo.git",
        release_ref="refs/heads/main",
    )

    result = materialize_source(workflow, entry, rev)
    assert result.store_path is not None
    assert result.store_path.startswith("/nix/store/")
    assert result.nar_hash is not None
    assert result.nar_hash.startswith("sha256-")
    assert (Path(result.store_path) / "flake.nix").is_file()


def test_materialize_source_core_style_subflake_snapshot(tmp_path: Path):
    repo = tmp_path / "core_with_subflake"
    repo.mkdir(parents=True, exist_ok=True)
    _run(repo, "git", "init", "--initial-branch=main")
    _run(repo, "git", "config", "user.name", "Local Source Fixture")
    _run(repo, "git", "config", "user.email", "fixture@example.invalid")
    subflake = repo / "nix-darwin"
    subflake.mkdir()
    (subflake / "flake.nix").write_text(
        '{ outputs = { self }: { greeting = "subflake"; }; }\n',
        encoding="utf-8",
    )
    _run(repo, "git", "add", "-A")
    _run(repo, "git", "commit", "-m", "core subflake")
    rev = _run(repo, "git", "rev-parse", "HEAD")
    workflow = Workflow(repo, display_command_output=False)
    entry = SourceCatalogEntry(
        key="core-subflake-entry",
        checkout=repo,
        repository="https://github.com/example/core.git",
        release_ref="refs/heads/main",
    )

    result = materialize_source(workflow, entry, rev)

    assert result.path == result.store_path
    assert result.path is not None and Path(result.path).is_dir()
    assert result.locked["rev"] == rev
    assert result.locked["narHash"] == result.nar_hash
    assert "path" not in result.locked
    assert (Path(result.path) / "nix-darwin/flake.nix").is_file()
    assert not (Path(result.path) / "flake.nix").exists()


def test_validate_source_identity_rejects_unknown_or_unmaterialized_fields(
    tmp_path: Path,
):
    repo = tmp_path / "identity_repo"
    rev = _init_flake_repo(repo)
    identity = observe_source(
        Workflow(repo, display_command_output=False),
        SourceCatalogEntry(
            key="identity-entry",
            checkout=repo,
            repository="https://github.com/example/identity.git",
            release_ref="refs/heads/main",
        ),
        rev,
    )
    document = identity.as_dict()
    document["unexpected"] = True

    with pytest.raises(ConfigError, match="identity fields"):
        validate_source_identity(document)

    document = identity.as_dict()
    document["store_path"] = "/nix/store/not-real"
    with pytest.raises(ConfigError, match="store_path and nar_hash"):
        validate_source_identity(document)


def test_materialize_source_rejects_mutation_after_snapshot(tmp_path: Path):
    repo = tmp_path / "mutate_repo"
    rev = _init_flake_repo(repo)

    class MutatingWorkflow:
        def __init__(self, inner: Workflow):
            self.inner = inner

        def command(self, label: str, argv: list[str], cwd: Path, **kwargs: Any) -> Any:
            result = self.inner.command(label, argv, cwd, **kwargs)
            if "materialize source" in label:
                (repo / "flake.nix").write_text("{ outputs = { self }: { hacked = true; }; }\n")
                _run(repo, "git", "add", "flake.nix")
                _run(repo, "git", "commit", "-m", "mutation after snapshot")
            return result

        def complete_stdout(self, label: str) -> str:
            return self.inner.complete_stdout(label)

    workflow = MutatingWorkflow(Workflow(repo, display_command_output=False))
    entry = SourceCatalogEntry(
        key="mutate-entry",
        checkout=repo,
        repository="https://github.com/example/test-repo.git",
        release_ref="refs/heads/main",
    )

    with pytest.raises(ConfigError, match="revalidation failed|source ref.*changed|points to"):
        materialize_source(workflow, entry, rev, require_exact_head=True)


def test_realize_catalog_locked_nodes_portable_lock_offline_evaluation(tmp_path: Path):
    src_repo = tmp_path / "src_repo"
    rev = _init_flake_repo(src_repo)
    canonical_url = "https://unreachable.invalid/source/repo.git"

    workflow = Workflow(src_repo, display_command_output=False)
    entry = SourceCatalogEntry(
        key="dep-source",
        checkout=src_repo,
        repository=canonical_url,
        release_ref="refs/heads/main",
    )

    pre_mat = materialize_source(workflow, entry, rev)
    nar_hash = pre_mat.nar_hash
    assert nar_hash is not None

    consumer_dir = tmp_path / "consumer"
    consumer_dir.mkdir(parents=True, exist_ok=True)
    consumer_flake = f'''{{
  inputs.dep.url = "git+{canonical_url}?ref=refs/heads/main&rev={rev}";
  outputs = {{ self, dep }}: {{ test = dep.greeting; }};
}}
'''
    (consumer_dir / "flake.nix").write_text(consumer_flake, encoding="utf-8")

    portable_lock = {
        "nodes": {
            "dep": {
                "locked": {
                    "type": "git",
                    "url": canonical_url,
                    "ref": "refs/heads/main",
                    "rev": rev,
                    "narHash": nar_hash,
                },
                "original": {
                    "type": "git",
                    "url": canonical_url,
                    "ref": "refs/heads/main",
                    "rev": rev,
                },
            },
            "root": {
                "inputs": {
                    "dep": "dep",
                }
            },
        },
        "root": "root",
        "version": 7,
    }
    lock_file = consumer_dir / "flake.lock"
    lock_file.write_text(json.dumps(portable_lock, indent=2), encoding="utf-8")

    realized = realize_catalog_locked_nodes(
        workflow,
        lock=lock_file,
        catalog={"dep-source": entry},
    )
    assert "dep" in realized
    assert realized["dep"].nar_hash == nar_hash
    assert realized["dep"].store_path == pre_mat.store_path

    eval_out = _run(
        consumer_dir,
        "nix", "eval", "--offline", "--no-write-lock-file", "--json", ".#test"
    )
    assert json.loads(eval_out) == "hello-dinas"

    implicit_main_lock = json.loads(json.dumps(portable_lock))
    implicit_main_lock["nodes"]["dep"]["locked"].pop("ref")
    implicit_realized = realize_catalog_locked_nodes(
        workflow,
        lock=implicit_main_lock,
        catalog={"dep-source": entry},
    )
    assert implicit_realized["dep"].revision == rev

    shorthand_lock = json.loads(json.dumps(portable_lock))
    shorthand_lock["nodes"]["dep"]["locked"]["ref"] = "main"
    shorthand_realized = realize_catalog_locked_nodes(
        workflow,
        lock=shorthand_lock,
        catalog={"dep-source": entry},
    )
    assert shorthand_realized["dep"].revision == rev

    for rejected_ref in ("refs/remotes/origin/main", "refs/tags/release"):
        rejected_lock = json.loads(json.dumps(portable_lock))
        rejected_lock["nodes"]["dep"]["locked"]["ref"] = rejected_ref
        with pytest.raises(ConfigError, match="invalid ref"):
            realize_catalog_locked_nodes(
                workflow,
                lock=rejected_lock,
                catalog={"dep-source": entry},
            )


def test_realize_catalog_locked_nodes_nar_hash_mismatch_rejection(tmp_path: Path):
    src_repo = tmp_path / "src_repo_nar"
    rev = _init_flake_repo(src_repo)
    canonical_url = "https://unreachable.invalid/source/repo.git"

    workflow = Workflow(src_repo, display_command_output=False)
    entry = SourceCatalogEntry(
        key="dep-source",
        checkout=src_repo,
        repository=canonical_url,
        release_ref="refs/heads/main",
    )

    bogus_nar_hash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

    portable_lock = {
        "nodes": {
            "dep": {
                "locked": {
                    "type": "git",
                    "url": canonical_url,
                    "ref": "refs/heads/main",
                    "rev": rev,
                    "narHash": bogus_nar_hash,
                }
            },
            "root": {"inputs": {"dep": "dep"}},
        },
        "root": "root",
        "version": 7,
    }

    with pytest.raises(ConfigError, match="recorded narHash.*does not agree"):
        realize_catalog_locked_nodes(
            workflow,
            lock=portable_lock,
            catalog=[entry],
        )


def test_realize_catalog_locked_nodes_ref_mismatch_rejection(tmp_path: Path):
    src_repo = tmp_path / "src_repo_ref"
    rev = _init_flake_repo(src_repo)
    canonical_url = "https://unreachable.invalid/source/repo.git"

    workflow = Workflow(src_repo, display_command_output=False)
    entry = SourceCatalogEntry(
        key="dep-source",
        checkout=src_repo,
        repository=canonical_url,
        release_ref="refs/heads/main",
    )

    portable_lock = {
        "nodes": {
            "dep": {
                "locked": {
                    "type": "git",
                    "url": canonical_url,
                    "ref": "refs/heads/develop",
                    "rev": rev,
                    "narHash": "sha256-fake",
                }
            },
            "root": {"inputs": {"dep": "dep"}},
        },
        "root": "root",
        "version": 7,
    }

    with pytest.raises(ConfigError, match="ref.*does not agree|ref authority"):
        realize_catalog_locked_nodes(
            workflow,
            lock=portable_lock,
            catalog=[entry],
        )


def test_observe_and_materialize_duck_typed_entry(tmp_path: Path):
    repo = tmp_path / "duck_repo"
    rev = _init_flake_repo(repo)
    workflow = Workflow(repo, display_command_output=False)

    # Dictionary duck-typed entry
    dict_entry = {
        "key": "duck-dict",
        "checkout": repo,
        "repository": "https://github.com/example/duck-dict.git",
        "release_ref": "refs/heads/main",
        "publication": {"remote": "origin", "ref": "refs/heads/main"},
    }
    identity = observe_source(workflow, dict_entry, rev)
    assert identity.key == "duck-dict"
    assert identity.publication == {"remote": "origin", "ref": "refs/heads/main"}

    # Custom object duck-typed entry
    class CustomEntry:
        def __init__(self):
            self.key = "duck-obj"
            self.checkout = repo
            self.repository = "https://github.com/example/duck-obj.git"
            self.release_ref = "refs/heads/main"
            self.publication = None

    identity2 = observe_source(workflow, CustomEntry(), rev)
    assert identity2.key == "duck-obj"


def test_realize_with_source_catalog_object_if_available(tmp_path: Path):
    repo = tmp_path / "catalog_repo"
    rev = _init_flake_repo(repo)
    canonical_url = "https://unreachable.invalid/cat/repo.git"
    workflow = Workflow(repo, display_command_output=False)

    entry = RealEntry(
        key="cat-dep",
        checkout=repo,
        repository=canonical_url,
        release_ref="refs/heads/main",
    )
    catalog = SourceCatalog(entries=(entry,))

    pre_mat = materialize_source(workflow, entry, rev)
    lock = {
        "nodes": {
            "cat-dep": {
                "locked": {
                    "type": "git",
                    "url": canonical_url,
                    "ref": "refs/heads/main",
                    "rev": rev,
                    "narHash": pre_mat.nar_hash,
                }
            },
            "root": {"inputs": {"cat-dep": "cat-dep"}},
        },
        "root": "root",
        "version": 7,
    }

    realized = realize_catalog_locked_nodes(workflow, lock=lock, catalog=catalog)
    assert "cat-dep" in realized
    assert realized["cat-dep"].nar_hash == pre_mat.nar_hash
