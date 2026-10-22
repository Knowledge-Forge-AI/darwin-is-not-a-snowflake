# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_workflow.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_plan.py
"""Unit tests for local-authority native promotion path (DINAS-SOURCES1).

Validates:
1. Strict versioned local native plan/apply path with zero network queries (no git ls-remote).
2. v4 plan schema (nix-update-promotion-plan-v4) with actual plan loader roundtrip validation.
3. v2 result schema (nix-update-promotion-result-v2) with LOCAL_PROMOTION_COMMITTED and LOCAL_COMMIT_CREATED.
4. Already-selected v4 flow with local_ref_unchanged in non_mutation.
5. Exact commit preservation across commit failures and recovery (no history rewriting, no git push).
6. Local candidate source observation and materialization via local_source.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib
import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

REVISION = "a" * 40
OLD_REVISION = "b" * 40


def sri_nar_hash(revision: str) -> str:
    """Return deterministic, syntactically valid SRI for the fake Nix runner."""

    digest = hashlib.sha256(revision.encode("ascii")).digest()
    return f"sha256-{base64.b64encode(digest).decode('ascii')}"


@pytest.fixture
def promotion_modules(repo_root: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    deployment_workflow = importlib.import_module("nix_darwin_local.deployment_workflow")
    promotion_plan = importlib.import_module("nix_darwin_local.promotion_plan")
    promotion_workflow = importlib.import_module("nix_darwin_local.promotion_workflow")
    source_catalog = importlib.import_module("nix_darwin_local.source_catalog")
    campaign_result = importlib.import_module("nix_darwin_local.promotion_campaign_result")
    paths = importlib.import_module("nix_darwin_local.paths")
    return deployment_workflow, promotion_plan, promotion_workflow, source_catalog, campaign_result, paths


def find_store_dir() -> str:
    store = Path("/nix/store")
    if store.is_dir():
        for p in store.iterdir():
            if p.is_dir():
                return str(p)
    return "/nix/store/00000000000000000000000000000000-dummy-store-dir"


def contract(composition_dir: Path) -> dict:
    return {
        "schema": "fixture-base-promotion-v1",
        "version": 1,
        "composition": {
            "name": "stage-a-composition",
            "repositoryUrl": composition_dir.as_uri(),
        },
        "system": "aarch64-darwin",
        "modules": [
            {
                "name": "domain-adapter",
                "input": "domain-adapter",
                "url": "https://example.invalid/domain-adapter",
                "checkout": "domain-adapter",
                "bootstrapStrategy": "none",
                "ownsMachineLocalConfiguration": False,
            }
        ],
        "promotableInputs": {
            "rule": "registered-direct-inputs-only",
            "names": ["domain-adapter"],
        },
        "qualification": {
            "entryPoint": "bin/qualify-promotion",
            "mode": "exact-candidate",
        },
        "checks": ["composition"],
        "hosts": ["StageAHost"],
        "packageSetIdentity": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-nixpkgs",
    }


def qualifier_result(contract_document: dict, revision: str) -> dict:
    repository_url = contract_document["modules"][0]["url"]
    return {
        "schema": "fixture-base-promotion-result-v1",
        "input": "domain-adapter",
        "registry": {"name": "domain-adapter"},
        "candidate": {
            "url": f"{repository_url}?rev={revision}",
            "requestedRev": revision,
            "resolvedRev": revision,
            "resolvedUrl": repository_url,
            "revMatched": True,
            "urlMatched": True,
        },
        "qualification": {
            "entryPoint": "bin/qualify-promotion",
            "mode": "exact-candidate",
            "timeoutSeconds": 300,
        },
        "commands": [],
        "checks": {"composition": "pass"},
        "hosts": {
            "StageAHost": {
                "status": "pass",
                "drvPath": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-stage-a.drv",
            }
        },
        "lock": {"preSha256": "a", "postSha256": "a", "byteIdentical": True},
        "tree": {"preSha256": "b", "postSha256": "b", "byteIdentical": True},
        "outcome": "pass",
        "failure": None,
    }


def write_lock(path: Path, revision: str, release_ref: str | None = None) -> None:
    original = {"type": "git", "url": "https://example.invalid/domain-adapter"}
    locked = {
        "type": "git",
        "url": "https://example.invalid/domain-adapter",
        "rev": revision,
        "narHash": sri_nar_hash(revision),
    }
    if release_ref is not None:
        short_ref = release_ref.removeprefix("refs/heads/")
        original["ref"] = short_ref
        locked["ref"] = short_ref
    path.write_text(
        json.dumps({
            "nodes": {
                "root": {"inputs": {"domain-adapter": "domain-adapter-node"}},
                "domain-adapter-node": {
                    "locked": locked,
                    "original": original,
                },
            },
            "root": "root",
            "version": 7,
        })
    )


def initialize_git_repository(path: Path, *, initial_lock_revision: str = OLD_REVISION) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "bin").mkdir(parents=True, exist_ok=True)
    qualifier = path / "bin/qualify-promotion"
    qualifier.write_text("#!/bin/sh\nexit 97\n")
    qualifier.chmod(0o755)
    (path / "flake.nix").write_text("{}\n")
    write_lock(path / "flake.lock", initial_lock_revision)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Nix Update Local Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "nix-update-local@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial fixture commit"], cwd=path, check=True, capture_output=True)


def initialize_adapter_repository(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    (path / "flake.nix").write_text("{}\n")
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Adapter Source Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "adapter-source@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "adapter commit 1"], cwd=path, check=True, capture_output=True)
    # create a second commit so HEAD revision is distinct
    (path / "version.txt").write_text("v2.0.0\n")
    subprocess.run(["git", "add", "version.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "adapter commit 2"], cwd=path, check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


class LocalPromotionRunner:
    def __init__(
        self,
        contract_document: dict,
        target_revision: str,
        store_dir: str,
        *,
        fail_candidate_qualifier: bool = False,
    ):
        self.contract = contract_document
        self.target_revision = target_revision
        self.store_dir = store_dir
        self.fail_candidate_qualifier = fail_candidate_qualifier
        self.calls: list[tuple[list[str], Path]] = []
        self.host_derivations = [
            "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-stage-a-system.drv"
        ]
        self.host_observation_count = 0

    def __call__(self, argv, cwd=None, **kwargs):
        argv = list(argv)
        cwd = Path(cwd) if cwd is not None else None
        self.calls.append((argv, cwd))
        if argv[0] == "git":
            return subprocess.run(argv, cwd=cwd, **kwargs)
        if argv[0].endswith("qualify-promotion"):
            if self.fail_candidate_qualifier and any("candidate" in str(arg) for arg in argv):
                return subprocess.CompletedProcess(argv, 1, "", "injected qualifier candidate failure")
            result = qualifier_result(self.contract, self.target_revision)
            return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")
        if argv[:3] == ["nix", "eval", "--json"]:
            if "--expr" in argv:
                expression = argv[argv.index("--expr") + 1]
                if "builtins.fetchTree" in expression:
                    return subprocess.CompletedProcess(
                        argv,
                        0,
                        json.dumps({
                            "rev": self.target_revision,
                            "ref": "refs/heads/main",
                            "narHash": sri_nar_hash(self.target_revision),
                            "path": self.store_dir,
                        }),
                        "",
                    )
            if "--argstr" in argv and "source_revision" in argv:
                idx = argv.index("source_revision")
                rev = argv[idx + 1] if idx + 1 < len(argv) else self.target_revision
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps({
                        "rev": rev,
                        "ref": "refs/heads/main",
                        "narHash": sri_nar_hash(rev),
                        "path": self.store_dir,
                    }),
                    "",
                )
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.contract), "")
        if argv[:3] == ["nix", "eval", "--raw"]:
            index = min(self.host_observation_count, len(self.host_derivations) - 1)
            derivation = self.host_derivations[index]
            self.host_observation_count += 1
            return subprocess.CompletedProcess(argv, 0, derivation, "")
        if argv[:3] == ["nix", "flake", "metadata"]:
            if "path:." in argv:
                lock = json.loads((cwd / "flake.lock").read_text())
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps({
                        "locks": {key: lock[key] for key in ("version", "root", "nodes")}
                    }),
                    "",
                )
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps({
                    "locked": {
                        "rev": self.target_revision,
                        "ref": "refs/heads/main",
                        "narHash": sri_nar_hash(self.target_revision),
                    },
                    "path": self.store_dir,
                }),
                "",
            )
        if argv[:3] == ["nix", "flake", "lock"]:
            write_lock(cwd / "flake.lock", self.target_revision, "refs/heads/main")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["nix", "build"]:
            if self.fail_candidate_qualifier:
                return subprocess.CompletedProcess(argv, 1, "", "injected qualifier candidate build failure")
            return subprocess.CompletedProcess(argv, 0, "/nix/store/stage-a-system\n", "")
        raise AssertionError(f"unexpected command: {argv}")


def local_promotion_fixture(promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch):
    (
        deployment_workflow,
        promotion_plan,
        promotion_workflow,
        source_catalog,
        campaign_result,
        paths,
    ) = promotion_modules

    selected = tmp_path / "composition"
    initialize_git_repository(selected)

    adapter_path = tmp_path / "adapter"
    adapter_rev = initialize_adapter_repository(adapter_path)

    contract_doc = contract(selected)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)

    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotion_workflow, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotion_workflow, "_guard_processes", lambda *_args: None)

    store_dir = find_store_dir()
    runner = LocalPromotionRunner(contract_doc, adapter_rev, store_dir)

    comp_entry = source_catalog.SourceCatalogEntry(
        key="composition",
        checkout=selected,
        repository=selected.as_uri(),
        release_ref="refs/heads/main",
        publication=None,
    )
    adapter_entry = source_catalog.SourceCatalogEntry(
        key="domain-adapter",
        checkout=adapter_path,
        repository="https://example.invalid/domain-adapter",
        release_ref="refs/heads/main",
        publication=None,
    )

    workflow = deployment_workflow.Workflow(
        repo_root, runner, sources=(comp_entry, adapter_entry)
    )

    return (
        workflow,
        selected,
        adapter_path,
        adapter_rev,
        contract_doc,
        state,
        runner,
        promotion_workflow,
        promotion_plan,
        campaign_result,
    )


def prepare_local_plan(promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch):
    """Prepare one real v4 artifact for production-loader tamper tests."""

    (
        workflow,
        selected,
        _adapter_path,
        adapter_rev,
        _contract_doc,
        _state,
        _runner,
        promotion_workflow,
        promotion_plan,
        _campaign_result,
    ) = local_promotion_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)
    output = tmp_path / "promotion-plan-loader-tamper"
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "domain-adapter",
        adapter_rev,
        output,
        300,
        allow_local_fixture=True,
    )
    assert artifact.document["schema"] == "nix-update-promotion-plan-v4"
    assert artifact.document["status"] == "READY_FOR_REVIEW"
    assert artifact.document["local_sources"]
    return artifact, promotion_plan


def tamper_plan_file(promotion_plan, artifact, defect: str, old_revision: str) -> dict:
    """Rewrite a real plan canonically with one authority defect and a valid digest."""

    document = copy.deepcopy(artifact.document)
    if defect == "missing-local-sources":
        document.pop("local_sources")
    elif defect == "unknown-local-sources-field":
        document["local_sources"][0]["source"]["unexpected"] = True
    elif defect == "mutated-local-ref":
        document["local_ref"]["ref"] = "refs/heads/feature"
    elif defect == "mutated-source-revision":
        document["local_sources"][0]["source"]["revision"] = old_revision
    elif defect == "composition-catalog-path":
        checkout = Path(document["composition_catalog"]["checkout"])
        document["composition_catalog"]["checkout"] = str(checkout.parent)
    elif defect == "composition-catalog-ref":
        document["composition_catalog"]["release_ref"] = "refs/heads/feature"
    elif defect == "composition-catalog-publication-remote":
        document["composition_catalog"]["publication"] = {
            "remote": "origin/other",
            "ref": "refs/heads/main",
        }
    elif defect == "repository-alias":
        document["repository"] = document.pop("source_authority")
    else:
        raise AssertionError(f"unknown plan defect: {defect}")

    document["plan_digest"] = ""
    document["plan_digest"] = promotion_plan.sha256_bytes(
        promotion_plan.canonical_bytes(document)
    )
    (artifact.directory / "plan.json").write_bytes(
        promotion_plan.canonical_bytes(document)
    )
    return document


def prepare_local_artifact(promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch, name: str):
    """Prepare a real local v4 artifact and return its fixture bindings."""
    fixture = local_promotion_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)
    (
        workflow,
        selected,
        _adapter_path,
        adapter_rev,
        _contract_doc,
        _state,
        _runner,
        promotion_workflow,
        _promotion_plan,
        _campaign_result,
    ) = fixture
    output = tmp_path / name
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "domain-adapter",
        adapter_rev,
        output,
        300,
        allow_local_fixture=True,
    )
    assert artifact.document["schema"] == "nix-update-promotion-plan-v4"
    assert artifact.document["status"] == "READY_FOR_REVIEW"
    return fixture, artifact


def apply_prepared_local_artifact(fixture, artifact):
    """Apply a prepared local artifact once and return its committed identity."""
    workflow, selected, _adapter_path, _adapter_rev, _contract_doc, _state, _runner, promotion_workflow, _promotion_plan, campaign_result = fixture
    status = promotion_workflow.apply_promotion(
        workflow,
        artifact.directory,
        confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )
    assert status == 0
    loaded = campaign_result.load_native_result(artifact.directory, artifact.digest)
    result = loaded["document"]
    assert result["terminal_state"] == "LOCAL_PROMOTION_COMMITTED"
    assert result["local_commit"]
    assert result["published_commit"] is None
    return result["local_commit"]


def test_local_result_write_failure_retains_local_commit_in_versioned_fallback(
    promotion_modules, tmp_path, monkeypatch,
):
    from types import SimpleNamespace
    workflow = promotion_modules[2]
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(workflow, "_private_state_root", lambda: state)
    def fail_write(*args):
        raise OSError("fixture result write failed")
    monkeypatch.setattr(workflow, "write_result", fail_write)
    result = dict(terminal_state="LOCAL_PROMOTION_COMMITTED", plan_digest="a" * 64,
                  local_commit="b" * 40, published_commit=None,
                  publication_state="unavailable", activation_state="not_performed")
    assert workflow._finish_result(tmp_path, result, SimpleNamespace(records=[]), 0) == 1
    fallback, = (state / "promotion-failure-results").glob("*.json")
    retained = json.loads(fallback.read_text())
    assert retained["schema"] == "nix-update-promotion-result-fallback-v2"
    assert retained["local_commit"] == "b" * 40
    assert retained["published_commit"] is None
    assert retained["publication_state"] == "unavailable"
    assert retained["activation_state"] == "not_performed"


def test_prepare_and_apply_promotion_local_roundtrip_succeeds_without_network(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    """Test full prepare -> load_plan -> apply_promotion local workflow.

    Verifies v3 plan schema, v2 result schema, real plan loader roundtrip,
    and asserts zero network calls (git ls-remote) and no git push.
    """
    (
        workflow,
        selected,
        adapter_path,
        adapter_rev,
        contract_doc,
        state,
        runner,
        promotion_workflow,
        promotion_plan,
        campaign_result,
    ) = local_promotion_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)

    output = tmp_path / "promotion-plan-local"

    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "domain-adapter",
        adapter_rev,
        output,
        300,
        allow_local_fixture=True,
    )

    assert artifact.document["schema"] == "nix-update-promotion-plan-v4"
    assert artifact.document["status"] == "READY_FOR_REVIEW"
    assert "remote_ref" not in artifact.document
    assert "publication" not in artifact.document
    assert artifact.document["local_ref"] == {
        "ref": "refs/heads/main",
        "revision": adapter_rev,
    }
    assert "source_authority" in artifact.document
    assert "composition_catalog" in artifact.document
    assert artifact.document["composition_catalog"]["key"] == "composition"
    assert artifact.document["composition_catalog"]["repository"] == selected.as_uri()
    assert artifact.document["confirmation"] == "APPLY NIX-UPDATE PROMOTION {PLAN_DIGEST}"
    assert artifact.document["local_sources"]
    binding = artifact.document["local_sources"][0]
    assert binding["schema"] == "nix-update-promotion-local-source-binding-v2"
    assert binding["role"] == "exact"
    assert binding["source"]["revision"] == adapter_rev
    assert binding["realization"]["revision"] == adapter_rev

    # 2. Real loader roundtrip validation (NO mocked loader!)
    loaded = promotion_plan.load_plan(output)
    assert loaded.digest == artifact.digest
    assert loaded.document == artifact.document

    retained_cand_lock = json.loads((artifact.directory / "retained-lock/flake.lock.candidate").read_text())
    assert set(retained_cand_lock.keys()) == {"nodes", "root", "version"}
    assert "byte_sha256" not in retained_cand_lock

    # 3. Apply promotion
    status = promotion_workflow.apply_promotion(
        workflow,
        output,
        confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )
    assert status == 0

    installed_lock = json.loads((selected / "flake.lock").read_text())
    assert set(installed_lock.keys()) == {"nodes", "root", "version"}
    assert "byte_sha256" not in installed_lock

    # 4. Result validation (v2 result schema and terminal state)
    result_path = output / "result.json"
    assert result_path.exists()
    result_doc = json.loads(result_path.read_text())
    assert result_doc["schema"] == "nix-update-promotion-result-v2"
    assert result_doc["terminal_state"] == "LOCAL_PROMOTION_COMMITTED"
    assert result_doc["local_commit"] is not None
    assert result_doc["published_commit"] is None
    assert result_doc["publication_state"] == "unavailable"
    assert result_doc["activation_state"] == "not_performed"
    assert result_doc["no_activation"] is True
    assert result_doc["failure"] is None

    # Validate against campaign result specification
    campaign_result.validate_native_result(result_doc, artifact.digest)

    # 5. Assert strict no-network invariant: git ls-remote is prohibited
    assert not any("ls-remote" in " ".join(call[0]) for call in runner.calls)

    # 6. Assert no git push
    assert not any(call[0][:2] == ["git", "push"] for call in runner.calls)


def test_apply_local_result_scopes_commands_after_large_prior_workflow_history(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    """Keep cumulative workflow history while bounding each result to its apply."""
    (
        workflow,
        selected,
        _adapter_path,
        adapter_rev,
        _contract_doc,
        _state,
        _runner,
        promotion_workflow,
        _promotion_plan,
        campaign_result,
    ) = local_promotion_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)
    deployment_workflow = promotion_modules[0]

    output = tmp_path / "promotion-plan-local-large-history"
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "domain-adapter",
        adapter_rev,
        output,
        300,
        allow_local_fixture=True,
    )

    prior_label = "previous campaign stage command"
    prior_count = 4097
    for _ in range(prior_count):
        workflow.records.append(
            deployment_workflow.CommandRecord(
                label=prior_label,
                argv=("git", "status"),
                cwd=str(selected),
                started_at="2026-09-08T00:00:00Z",
                finished_at="2026-09-08T00:00:01Z",
                exit_status=0,
                stdout={"summary": ""},
                stderr={"summary": ""},
            )
        )
    records_before_apply = len(workflow.records)
    assert records_before_apply >= prior_count

    status = promotion_workflow.apply_promotion(
        workflow,
        output,
        confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )
    assert status == 0
    assert len(workflow.records) > records_before_apply

    result = campaign_result.load_native_result(output, artifact.digest)
    result_doc = result["document"]
    commands = result_doc["commands"]
    assert commands
    assert len(commands) == len(workflow.records) - records_before_apply
    assert len(commands) < 4096
    assert all(command["label"] != prior_label for command in commands)
    assert result_doc["terminal_state"] == "LOCAL_PROMOTION_COMMITTED"
    assert result_doc["published_commit"] is None
    assert result_doc["publication_state"] == "unavailable"


@pytest.mark.parametrize(
    ("defect", "match"),
    [
        ("missing-local-sources", "promotion plan fields are invalid"),
        ("unknown-local-sources-field", "source identity fields are invalid"),
        ("mutated-local-ref", "local release-ref evidence is invalid"),
        ("mutated-source-revision", "(source identity head_revision differs from revision|selected owned or changed source requires exact release revision)"),
        ("composition-catalog-path", "composition catalog checkout/ref disagrees"),
        ("composition-catalog-ref", "composition catalog checkout/ref disagrees"),
        ("composition-catalog-publication-remote", "publication fields are invalid"),
        ("repository-alias", "promotion plan fields are invalid"),
    ],
)
def test_production_v3_plan_loaders_reject_local_authority_tampering(
    promotion_modules,
    repo_root: Path,
    tmp_path: Path,
    monkeypatch,
    defect: str,
    match: str,
):
    """Reject local authority defects through both production plan loaders."""

    artifact, promotion_plan = prepare_local_plan(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    document = tamper_plan_file(promotion_plan, artifact, defect, OLD_REVISION)

    with pytest.raises(promotion_plan.ConfigError, match=match):
        promotion_plan.validate_plan(document)
    with pytest.raises(promotion_plan.ConfigError, match=match):
        promotion_plan.load_plan(artifact.directory)


def test_already_selected_local_produces_v3_plan_with_local_ref_unchanged(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    """Test already-selected local promotion plan produces v3 schema with local_ref_unchanged."""
    (
        deployment_workflow,
        promotion_plan,
        promotion_workflow,
        source_catalog,
        campaign_result,
        paths,
    ) = promotion_modules

    selected = tmp_path / "composition-already-selected"
    adapter_path = tmp_path / "adapter-already-selected"
    adapter_rev = initialize_adapter_repository(adapter_path)
    # Initialize composition repo where flake.lock ALREADY has adapter_rev
    initialize_git_repository(selected, initial_lock_revision=adapter_rev)

    contract_doc = contract(selected)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)

    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotion_workflow, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotion_workflow, "_guard_processes", lambda *_args: None)

    store_dir = find_store_dir()
    runner = LocalPromotionRunner(contract_doc, adapter_rev, store_dir)

    comp_entry = source_catalog.SourceCatalogEntry(
        key="composition",
        checkout=selected,
        repository=selected.as_uri(),
        release_ref="refs/heads/main",
        publication=None,
    )
    adapter_entry = source_catalog.SourceCatalogEntry(
        key="domain-adapter",
        checkout=adapter_path,
        repository="https://example.invalid/domain-adapter",
        release_ref="refs/heads/main",
        publication=None,
    )

    workflow = deployment_workflow.Workflow(
        repo_root, runner, sources=(comp_entry, adapter_entry)
    )

    output = tmp_path / "promotion-plan-already-selected"
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "domain-adapter",
        adapter_rev,
        output,
        300,
        allow_local_fixture=True,
    )

    assert artifact.document["schema"] == "nix-update-promotion-plan-v4"
    assert artifact.document["status"] == "ALREADY_SELECTED"
    assert artifact.document["non_mutation"] == {
        "git_authority_unchanged": True,
        "lock_bytes_unchanged": True,
        "local_ref_unchanged": True,
    }
    assert "local_ref" in artifact.document
    assert "remote_ref" not in artifact.document

    # Real plan loader roundtrip
    loaded = promotion_plan.load_plan(output)
    assert loaded.digest == artifact.digest
    assert loaded.document == artifact.document

    # Assert no ls-remote
    assert not any("ls-remote" in " ".join(call[0]) for call in runner.calls)


def test_apply_local_post_commit_verification_failure_preserves_commit(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    """Test post-commit verification failure preserves commit with LOCAL_COMMIT_CREATED."""
    (
        workflow,
        selected,
        adapter_path,
        adapter_rev,
        contract_doc,
        state,
        runner,
        promotion_workflow,
        promotion_plan,
        campaign_result,
    ) = local_promotion_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)

    output = tmp_path / "promotion-plan-post-commit-fail"
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "domain-adapter",
        adapter_rev,
        output,
        300,
        allow_local_fixture=True,
    )

    # Monkeypatch _verify_local_committed_repository to simulate post-commit defect
    def fail_verification(*_args):
        raise promotion_workflow.ConfigError("injected post-commit verification defect")

    monkeypatch.setattr(promotion_workflow, "_verify_local_committed_repository", fail_verification)

    status = promotion_workflow.apply_promotion(
        workflow,
        output,
        confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )
    assert status == 1

    result_doc = json.loads((output / "result.json").read_text())
    assert result_doc["schema"] == "nix-update-promotion-result-v2"
    assert result_doc["terminal_state"] == "LOCAL_COMMIT_CREATED"
    assert result_doc["local_commit"] is not None
    assert "injected post-commit verification defect" in result_doc["failure"]

    # Verify commit exists on HEAD and was preserved
    head_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=selected, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert head_commit == result_doc["local_commit"]

    # Assert no git push
    assert not any(call[0][:2] == ["git", "push"] for call in runner.calls)


@pytest.mark.parametrize(
    ("identity_label", "replacement"),
    [
        ("promotion commit parent", "0" * 40),
        ("promotion commit tree", "0" * 40),
        ("promotion commit changed paths", "flake.lock\nunexpected.txt"),
    ],
)
def test_apply_local_commit_identity_mismatch_retains_created_commit(
    promotion_modules,
    repo_root: Path,
    tmp_path: Path,
    monkeypatch,
    identity_label: str,
    replacement: str,
):
    """A post-commit parent/tree/path observation failure preserves HEAD."""
    fixture, artifact = prepare_local_artifact(
        promotion_modules,
        repo_root,
        tmp_path,
        monkeypatch,
        f"promotion-plan-commit-identity-{identity_label.replace(' ', '-')}",
    )
    workflow, selected, _adapter_path, _adapter_rev, _contract_doc, _state, _runner, promotion_workflow, _promotion_plan, campaign_result = fixture
    original_git_value = promotion_workflow._git_value

    def altered_git_value(workflow_arg, selected_arg, label, command):
        value = original_git_value(workflow_arg, selected_arg, label, command)
        return replacement if label == identity_label else value

    monkeypatch.setattr(promotion_workflow, "_git_value", altered_git_value)
    status = promotion_workflow.apply_promotion(
        workflow,
        artifact.directory,
        confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )
    assert status == 1

    result = campaign_result.load_native_result(artifact.directory, artifact.digest)["document"]
    assert result["terminal_state"] == "LOCAL_COMMIT_CREATED"
    assert result["local_commit"]
    assert result["published_commit"] is None
    assert result["publication_state"] == "unavailable"
    assert "promotion commit parent, tree, or changed path set is invalid" in result["failure"]
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=selected,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == result["local_commit"]
    assert int(subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=selected,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()) == 2


@pytest.mark.parametrize("defect", ["parent", "paths", "candidate"])
def test_apply_local_consumed_commit_recovery_rejects_identity_drift(
    promotion_modules,
    repo_root: Path,
    tmp_path: Path,
    monkeypatch,
    defect: str,
):
    """Consumed local commits remain durable when recovery evidence drifts."""
    fixture, artifact = prepare_local_artifact(
        promotion_modules,
        repo_root,
        tmp_path,
        monkeypatch,
        f"promotion-plan-recovery-{defect}",
    )
    workflow, selected, _adapter_path, _adapter_rev, _contract_doc, _state, _runner, promotion_workflow, _promotion_plan, campaign_result = fixture
    committed = apply_prepared_local_artifact(fixture, artifact)

    if defect in {"parent", "paths"}:
        original_git_value = promotion_workflow._git_value
        replacement = "0" * 40 if defect == "parent" else "flake.lock\nunexpected.txt"

        def altered_git_value(workflow_arg, selected_arg, label, command):
            value = original_git_value(workflow_arg, selected_arg, label, command)
            if label == "promotion recovery commit parent" and defect == "parent":
                return replacement
            if label == "promotion recovery commit changed paths" and defect == "paths":
                return replacement
            return value

        monkeypatch.setattr(promotion_workflow, "_git_value", altered_git_value)
    else:
        original_complete_stdout = workflow.complete_stdout

        def altered_complete_stdout(label):
            value = original_complete_stdout(label)
            if label == "promotion recovery committed lock":
                return "tampered lock bytes"
            return value

        monkeypatch.setattr(workflow, "complete_stdout", altered_complete_stdout)

    status = promotion_workflow.apply_promotion(
        workflow,
        artifact.directory,
        confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )
    assert status == 1
    result = campaign_result.load_native_result(artifact.directory, artifact.digest)["document"]
    assert result["terminal_state"] == "LOCAL_COMMIT_CREATED"
    assert result["local_commit"] == committed
    assert result["published_commit"] is None
    assert result["publication_state"] == "unavailable"
    assert "consumed promotion commit does not match the reviewed candidate" in result["failure"]
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=selected,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == committed


def test_apply_local_pre_commit_failure_restores_exact_preimage(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    """Test pre-commit failure restores exact flake.lock preimage."""
    (
        deployment_workflow,
        promotion_plan,
        promotion_workflow,
        source_catalog,
        campaign_result,
        paths,
    ) = promotion_modules

    selected = tmp_path / "composition-pre-commit-fail"
    initialize_git_repository(selected)
    preimage_bytes = (selected / "flake.lock").read_bytes()

    adapter_path = tmp_path / "adapter-pre-commit-fail"
    adapter_rev = initialize_adapter_repository(adapter_path)

    contract_doc = contract(selected)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)

    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotion_workflow, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotion_workflow, "_guard_processes", lambda *_args: None)

    store_dir = find_store_dir()
    runner = LocalPromotionRunner(
        contract_doc, adapter_rev, store_dir, fail_candidate_qualifier=False
    )

    comp_entry = source_catalog.SourceCatalogEntry(
        key="composition",
        checkout=selected,
        repository=selected.as_uri(),
        release_ref="refs/heads/main",
        publication=None,
    )
    adapter_entry = source_catalog.SourceCatalogEntry(
        key="domain-adapter",
        checkout=adapter_path,
        repository="https://example.invalid/domain-adapter",
        release_ref="refs/heads/main",
        publication=None,
    )

    workflow = deployment_workflow.Workflow(
        repo_root, runner, sources=(comp_entry, adapter_entry)
    )

    output = tmp_path / "promotion-plan-pre-commit-fail"
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "domain-adapter",
        adapter_rev,
        output,
        300,
        allow_local_fixture=True,
    )

    # Now make the installed qualifier fail during apply
    runner.fail_candidate_qualifier = True

    status = promotion_workflow.apply_promotion(
        workflow,
        output,
        confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )
    assert status == 1

    result_doc = json.loads((output / "result.json").read_text())
    assert result_doc["terminal_state"] == "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED"

    # Preimage was restored
    assert (selected / "flake.lock").read_bytes() == preimage_bytes


@pytest.mark.parametrize("drift", ["qualifier", "impact"])
def test_apply_local_installed_candidate_drift_restores_exact_preimage(
    promotion_modules,
    repo_root: Path,
    tmp_path: Path,
    monkeypatch,
    drift: str,
):
    """Installed qualifier and host impact drift restore the lock preimage."""
    fixture, artifact = prepare_local_artifact(
        promotion_modules,
        repo_root,
        tmp_path,
        monkeypatch,
        f"promotion-plan-installed-{drift}-drift",
    )
    workflow, selected, _adapter_path, _adapter_rev, _contract_doc, _state, _runner, promotion_workflow, _promotion_plan, campaign_result = fixture
    preimage_bytes = (selected / "flake.lock").read_bytes()

    if drift == "qualifier":
        original_qualifier_identity = promotion_workflow._qualifier_identity
        qualifier_calls = 0

        def altered_qualifier_identity(tree, contract):
            nonlocal qualifier_calls
            qualifier_calls += 1
            identity = original_qualifier_identity(tree, contract)
            if qualifier_calls == 2:
                identity = copy.deepcopy(identity)
                identity["identity"]["sha256"] = "0" * 64
            return identity

        monkeypatch.setattr(
            promotion_workflow,
            "_qualifier_identity",
            altered_qualifier_identity,
        )
        expected_failure = "installed promotion qualifier identity drifted from the reviewed plan"
    else:
        original_host_derivations = promotion_workflow.qualification_host_derivations

        def altered_host_derivations(qualification, declared_hosts):
            hosts = original_host_derivations(qualification, declared_hosts)
            if hosts:
                hosts = dict(hosts)
                hosts[declared_hosts[0]] = "/nix/store/" + "b" * 32 + "-drifted.drv"
            return hosts

        monkeypatch.setattr(
            promotion_workflow,
            "qualification_host_derivations",
            altered_host_derivations,
        )
        expected_failure = "installed promotion host impact drifted from the reviewed plan"

    status = promotion_workflow.apply_promotion(
        workflow,
        artifact.directory,
        confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )
    assert status == 1
    result = campaign_result.load_native_result(artifact.directory, artifact.digest)["document"]
    assert result["terminal_state"] == "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED"
    assert result["local_commit"] is None
    assert result["published_commit"] is None
    assert expected_failure in result["failure"]
    assert (selected / "flake.lock").read_bytes() == preimage_bytes
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=selected,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == artifact.document["source_authority"]["commit"]


@pytest.mark.parametrize("drift", ["composition", "source"])
def test_apply_local_catalog_drift_blocks_before_consumption(
    promotion_modules,
    repo_root: Path,
    tmp_path: Path,
    monkeypatch,
    drift: str,
):
    """Immediate catalog drift blocks apply before lock mutation or consumption."""
    fixture, artifact = prepare_local_artifact(
        promotion_modules,
        repo_root,
        tmp_path,
        monkeypatch,
        f"promotion-plan-catalog-{drift}-drift",
    )
    workflow, selected, _adapter_path, _adapter_rev, _contract_doc, _state, _runner, promotion_workflow, _promotion_plan, campaign_result = fixture
    preimage_bytes = (selected / "flake.lock").read_bytes()
    composition_entry, source_entry = workflow.sources
    if drift == "composition":
        workflow.sources = (
            replace(composition_entry, release_ref="refs/heads/feature"),
            source_entry,
        )
    else:
        workflow.sources = (
            composition_entry,
            replace(source_entry, release_ref="refs/heads/feature"),
        )

    status = promotion_workflow.apply_promotion(
        workflow,
        artifact.directory,
        confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )
    assert status == 1
    result = campaign_result.load_native_result(artifact.directory, artifact.digest)["document"]
    assert result["terminal_state"] == "BLOCKED_BEFORE_MUTATION"
    assert result["local_commit"] is None
    assert result["published_commit"] is None
    expected_failure = "authority drifted" if drift == "composition" else "ref differs from catalog release ref"
    assert expected_failure in result["failure"]
    assert (selected / "flake.lock").read_bytes() == preimage_bytes
    assert not promotion_workflow._consumption_path(artifact.digest).exists()
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=selected,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == artifact.document["source_authority"]["commit"]


def test_apply_local_result_evidence_failure_preserves_committed_head(
    promotion_modules,
    repo_root: Path,
    tmp_path: Path,
    monkeypatch,
):
    """Failure writing the result retains the local commit in fallback evidence."""
    fixture, artifact = prepare_local_artifact(
        promotion_modules,
        repo_root,
        tmp_path,
        monkeypatch,
        "promotion-plan-result-evidence-failure",
    )
    workflow, selected, _adapter_path, _adapter_rev, _contract_doc, state, _runner, promotion_workflow, _promotion_plan, _campaign_result = fixture
    def fail_write_result(*_args, **_kwargs):
        raise OSError("injected result write failure")

    monkeypatch.setattr(promotion_workflow, "write_result", fail_write_result)

    status = promotion_workflow.apply_promotion(
        workflow,
        artifact.directory,
        confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )
    assert status == 1
    assert not (artifact.directory / "result.json").exists()
    fallback_files = list((state / "promotion-failure-results").glob("*.json"))
    assert len(fallback_files) == 1
    fallback = json.loads(fallback_files[0].read_text())
    assert fallback["schema"] == "nix-update-promotion-result-fallback-v2"
    assert fallback["terminal_state"] == "PROMOTION_RESULT_EVIDENCE_FAILURE"
    assert fallback["local_commit"]
    assert fallback["published_commit"] is None
    assert fallback["publication_state"] == "unavailable"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=selected,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == fallback["local_commit"]


def test_apply_local_recovery_of_consumed_uncommitted_restores_preimage(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    """Test recovery of interrupted uncommitted promotion restores preimage."""
    (
        workflow,
        selected,
        adapter_path,
        adapter_rev,
        contract_doc,
        state,
        runner,
        promotion_workflow,
        promotion_plan,
        campaign_result,
    ) = local_promotion_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)

    preimage_bytes = (selected / "flake.lock").read_bytes()
    output = tmp_path / "promotion-plan-recovery-uncommitted"
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "domain-adapter",
        adapter_rev,
        output,
        300,
        allow_local_fixture=True,
    )

    deployment_workflow, _, _, _, _, _ = promotion_modules
    candidate = deployment_workflow.load_retained_lock_candidate(
        artifact.directory / "retained-lock",
        artifact.document["source_authority"],
        artifact.document["entry_lock"]["sha256"],
        artifact.document["candidate_lock"]["sha256"],
        expected_closure_authorization=artifact.document["closure_authorization"],
        expected_evidence_sha256=artifact.document["lock_authorization_evidence"]["sha256"],
    )
    promotion_workflow._consume_authority(
        artifact.digest, artifact.document["source_authority"]
    )
    deployment_workflow.install_retained_lock(selected, candidate)
    assert (selected / "flake.lock").read_bytes() == candidate.candidate_bytes

    status = promotion_workflow.apply_promotion(
        workflow,
        output,
        confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )
    assert status == 1

    result_doc = json.loads((output / "result.json").read_text())
    assert result_doc["terminal_state"] == "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED"
    assert (selected / "flake.lock").read_bytes() == preimage_bytes


def test_apply_local_recovery_of_consumed_committed_reconciles_cleanly(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    """Test recovery of already-committed promotion reconciles to LOCAL_PROMOTION_COMMITTED."""
    (
        workflow,
        selected,
        adapter_path,
        adapter_rev,
        contract_doc,
        state,
        runner,
        promotion_workflow,
        promotion_plan,
        campaign_result,
    ) = local_promotion_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)

    output = tmp_path / "promotion-plan-recovery-committed"
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "domain-adapter",
        adapter_rev,
        output,
        300,
        allow_local_fixture=True,
    )

    # First apply creates the commit
    status_1 = promotion_workflow.apply_promotion(
        workflow,
        output,
        confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )
    assert status_1 == 0
    res_1 = json.loads((output / "result.json").read_text())
    committed = res_1["local_commit"]

    # Calling apply again with consumption marker present triggers recovery
    status_2 = promotion_workflow.apply_promotion(
        workflow,
        output,
        confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )
    assert status_2 == 0

    res_2 = json.loads((output / "result.json").read_text())
    assert res_2["terminal_state"] == "LOCAL_PROMOTION_COMMITTED"
    assert res_2["local_commit"] == committed
    assert not any("ls-remote" in " ".join(call[0]) for call in runner.calls)
    assert not any(call[0][:2] == ["git", "push"] for call in runner.calls)


def test_local_promotion_with_annotated_preimage_omits_diagnostic_field(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    (
        workflow,
        selected,
        adapter_path,
        adapter_rev,
        contract_doc,
        state,
        runner,
        promotion_workflow,
        promotion_plan,
        campaign_result,
    ) = local_promotion_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)

    # Annotate preimage flake.lock with historical byte_sha256
    lock_file = selected / "flake.lock"
    preimage_data = json.loads(lock_file.read_text())
    preimage_data["byte_sha256"] = "1" * 64
    lock_file.write_text(json.dumps(preimage_data))
    subprocess.run(["git", "add", "flake.lock"], cwd=selected, check=True)
    subprocess.run(["git", "commit", "-m", "annotate preimage flake.lock"], cwd=selected, check=True, capture_output=True)

    output = tmp_path / "promotion-plan-annotated-preimage"
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "domain-adapter",
        adapter_rev,
        output,
        300,
        allow_local_fixture=True,
    )

    # Preimage retained artifact retains the historical annotation
    retained_preimage = json.loads((output / "retained-lock/flake.lock.preimage").read_text())
    assert "byte_sha256" in retained_preimage

    # Candidate retained artifact omits the diagnostic field
    retained_candidate = json.loads((output / "retained-lock/flake.lock.candidate").read_text())
    assert set(retained_candidate.keys()) == {"nodes", "root", "version"}
    assert "byte_sha256" not in retained_candidate

    status = promotion_workflow.apply_promotion(
        workflow,
        output,
        confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )
    assert status == 0

    # Installed lock omits the diagnostic field
    installed = json.loads(lock_file.read_text())
    assert set(installed.keys()) == {"nodes", "root", "version"}
    assert "byte_sha256" not in installed


def test_production_lock_candidate_writer_rejects_unsupported_fields_emitted_by_nix(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    (
        workflow,
        selected,
        adapter_path,
        adapter_rev,
        contract_doc,
        state,
        runner,
        promotion_workflow,
        promotion_plan,
        campaign_result,
    ) = local_promotion_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)

    orig_call = runner.__call__

    def inject_unsupported(argv, cwd=None, **kwargs):
        res = orig_call(argv, cwd=cwd, **kwargs)
        if list(argv)[:3] == ["nix", "flake", "lock"]:
            lock_file = Path(cwd) / "flake.lock"
            data = json.loads(lock_file.read_text())
            data["future_nix_feature"] = "unsupported_data"
            lock_file.write_text(json.dumps(data))
        return res

    monkeypatch.setattr(workflow, "runner", inject_unsupported)

    output = tmp_path / "promotion-plan-unsupported"
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "domain-adapter",
        adapter_rev,
        output,
        300,
        allow_local_fixture=True,
    )
    assert artifact.document["status"] == "BLOCKED"
    assert any("unsupported field: future_nix_feature" in b for b in artifact.document["blockers"])


def test_local_promotion_preserves_byte_sha256_dependency_name(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    (
        workflow,
        selected,
        adapter_path,
        adapter_rev,
        contract_doc,
        state,
        runner,
        promotion_workflow,
        promotion_plan,
        campaign_result,
    ) = local_promotion_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)

    # Add a legitimate dependency named byte_sha256 to the preimage flake.lock
    lock_file = selected / "flake.lock"
    preimage_data = json.loads(lock_file.read_text())
    preimage_data["nodes"]["root"]["inputs"]["byte_sha256"] = "byte_sha256_node"
    preimage_data["nodes"]["byte_sha256_node"] = {
        "locked": {
            "type": "git",
            "url": "https://example.invalid/byte_sha256",
            "rev": "c" * 40,
            "narHash": "sha256-test=",
        },
        "original": {
            "type": "git",
            "url": "https://example.invalid/byte_sha256",
        },
    }
    lock_file.write_text(json.dumps(preimage_data))
    subprocess.run(["git", "add", "flake.lock"], cwd=selected, check=True)
    subprocess.run(["git", "commit", "-m", "add byte_sha256 input to flake.lock"], cwd=selected, check=True, capture_output=True)

    orig_call = runner.__call__

    def inject_byte_sha256_input(argv, cwd=None, **kwargs):
        res = orig_call(argv, cwd=cwd, **kwargs)
        if list(argv)[:3] == ["nix", "flake", "lock"]:
            cand_lock = Path(cwd) / "flake.lock"
            data = json.loads(cand_lock.read_text())
            data["nodes"]["root"]["inputs"]["byte_sha256"] = "byte_sha256_node"
            data["nodes"]["byte_sha256_node"] = {
                "locked": {
                    "type": "git",
                    "url": "https://example.invalid/byte_sha256",
                    "rev": "c" * 40,
                    "narHash": "sha256-test=",
                },
                "original": {
                    "type": "git",
                    "url": "https://example.invalid/byte_sha256",
                },
            }
            cand_lock.write_text(json.dumps(data))
        return res

    monkeypatch.setattr(workflow, "runner", inject_byte_sha256_input)

    output = tmp_path / "promotion-plan-byte-sha256-dep"
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "domain-adapter",
        adapter_rev,
        output,
        300,
        allow_local_fixture=True,
    )
    assert artifact.document["status"] == "READY_FOR_REVIEW"

    # Preimage and candidate retained artifacts preserve the byte_sha256 dependency input
    retained_preimage = json.loads((output / "retained-lock/flake.lock.preimage").read_text())
    assert retained_preimage["nodes"]["root"]["inputs"]["byte_sha256"] == "byte_sha256_node"
    assert "byte_sha256_node" in retained_preimage["nodes"]

    retained_candidate = json.loads((output / "retained-lock/flake.lock.candidate").read_text())
    assert retained_candidate["nodes"]["root"]["inputs"]["byte_sha256"] == "byte_sha256_node"
    assert "byte_sha256_node" in retained_candidate["nodes"]
    assert set(retained_candidate.keys()) == {"nodes", "root", "version"}
    assert "byte_sha256" not in retained_candidate

    status = promotion_workflow.apply_promotion(
        workflow,
        output,
        confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )
    assert status == 0

    # Installed lock preserves the dependency input and contains no diagnostic byte_sha256
    installed = json.loads(lock_file.read_text())
    assert installed["nodes"]["root"]["inputs"]["byte_sha256"] == "byte_sha256_node"
    assert "byte_sha256_node" in installed["nodes"]
    assert set(installed.keys()) == {"nodes", "root", "version"}
    assert "byte_sha256" not in installed
