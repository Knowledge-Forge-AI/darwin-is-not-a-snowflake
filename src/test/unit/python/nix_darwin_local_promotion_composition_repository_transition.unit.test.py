# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_composition.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_workflow.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_plan.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_binding.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/deployment_workflow.py
"""Unit tests for the composition repository-identity transition promotion path.

Validates:
1. Strict schema and validity constraints: input 'composition-nd', release ref 'refs/heads/main',
   historical preimage URL ssh://git@github.com/Knowledge-Forge-AI/composition-nd.git, canonical candidate
   URL ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git.
2. Negative scenarios: non-composition input, non-main ref, missing flag, mismatched URLs,
   tampered declarations, unauthorized contract field drift, combining core and composition transitions.
3. Dual declaration-and-lock artifact generation under retained-lock/ (flake.nix.candidate,
   flake.lock.candidate, delta.json with declaration metadata).
4. Plan validation under nix-update-promotion-composition-repository-transition-plan-v1.
5. Atomic dual-file installation, commit creation with changed_paths=["flake.nix", "flake.lock"],
   clean status reconciliation, and replay prevention.
6. Install failure rollback with journaled preimage restoration for both declaration and lock.
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

REVISION = "c" * 40
OLD_REVISION = "d" * 40

PREIMAGE_URL = "ssh://git@github.com/Knowledge-Forge-AI/composition-nd.git"
CANDIDATE_URL = "ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git"
PREIMAGE_DECLARATION_URL = "git+ssh://git@github.com/Knowledge-Forge-AI/composition-nd.git"
CANDIDATE_DECLARATION_URL = "git+ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git"


def sri_nar_hash(revision: str) -> str:
    digest = hashlib.sha256(revision.encode("ascii")).digest()
    return f"sha256-{base64.b64encode(digest).decode('ascii')}"


@pytest.fixture
def promotion_modules(repo_root: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    deployment_workflow = importlib.import_module("nix_darwin_local.deployment_workflow")
    promotion_plan = importlib.import_module("nix_darwin_local.promotion_plan")
    promotion_workflow = importlib.import_module("nix_darwin_local.promotion_workflow")
    promotion_composition = importlib.import_module("nix_darwin_local.promotion_composition")
    source_catalog = importlib.import_module("nix_darwin_local.source_catalog")
    paths = importlib.import_module("nix_darwin_local.paths")
    return (
        deployment_workflow,
        promotion_plan,
        promotion_workflow,
        promotion_composition,
        source_catalog,
        paths,
    )


def layered_private_parent_contract(composition_dir: Path, comp_repo_url: str = PREIMAGE_URL) -> dict:
    lower = {
        "schema": "fixture-lower-promotion-v1",
        "version": 1,
        "composition": {
            "name": "composition-nd",
            "repositoryUrl": comp_repo_url,
        },
        "system": "aarch64-darwin",
        "modules": [
            {
                "name": "base-module",
                "input": "base-module",
                "url": "https://example.invalid/base-module",
                "checkout": "base-module",
                "bootstrapStrategy": "none",
                "ownsMachineLocalConfiguration": False,
            }
        ],
        "promotableInputs": {
            "rule": "direct-module",
            "names": ["base-module"],
        },
        "qualification": {
            "entryPoint": "bin/qualify-promotion",
            "mode": "exact-candidate",
        },
        "checks": ["composition"],
        "hosts": ["StageAHost"],
        "packageSetIdentity": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-nixpkgs",
    }
    local_modules = [
        {
            "name": "private-module",
            "input": "private-module",
            "url": "https://example.invalid/private-module",
            "checkout": "private-module",
            "bootstrapStrategy": "none",
            "ownsMachineLocalConfiguration": False,
        }
    ]
    return {
        "schema": "fixture-private-promotion-v1",
        "version": 1,
        "composition": {
            "name": "composition-private-nd",
            "repositoryUrl": composition_dir.as_uri(),
        },
        "system": lower["system"],
        "modules": local_modules,
        "promotableInputs": {
            "rule": "layered-private-parent",
            "names": ["composition-nd"],
        },
        "qualification": {
            "entryPoint": "bin/qualify-promotion",
            "mode": "exact-candidate",
        },
        "checks": ["composition"],
        "hosts": lower["hosts"],
        "packageSetIdentity": lower["packageSetIdentity"],
        "requiredLowerPromotion": {
            "schema": lower["schema"],
            "version": lower["version"],
            "candidateInput": "composition-nd",
        },
        "lowerPromotion": lower,
        "combinedModules": list(lower["modules"]) + list(local_modules),
        "semantics": {"nonActivation": True, "requiresExplicitLockSelection": True},
    }


def direct_composition_contract(composition_dir: Path, comp_repo_url: str = PREIMAGE_URL) -> dict:
    return {
        "schema": "fixture-base-promotion-v1",
        "version": 1,
        "composition": {
            "name": "darwin-is-not-a-snowflake",
            "repositoryUrl": composition_dir.as_uri(),
        },
        "system": "aarch64-darwin",
        "modules": [
            {
                "name": "composition-nd",
                "input": "composition-nd",
                "url": comp_repo_url,
                "checkout": "composition-nd",
                "bootstrapStrategy": "none",
                "ownsMachineLocalConfiguration": False,
            }
        ],
        "promotableInputs": {
            "rule": "direct-module",
            "names": ["composition-nd"],
        },
        "qualification": {
            "entryPoint": "bin/qualify-promotion",
            "mode": "exact-candidate",
        },
        "checks": ["composition"],
        "hosts": ["StageAHost"],
        "packageSetIdentity": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-nixpkgs",
    }


def write_comp_flake_nix(path: Path, url: str = PREIMAGE_DECLARATION_URL) -> None:
    path.write_text(
        f'{{\n  description = "Private composition flake";\n  # Reference to composition-nd repository\n  inputs.composition-nd.url = "{url}";\n  outputs = {{ ... }}: {{}};\n}}\n'
    )


def write_comp_flake_lock(path: Path, revision: str, lock_url: str = PREIMAGE_URL) -> None:
    path.write_text(
        json.dumps({
            "nodes": {
                "root": {"inputs": {"composition-nd": "comp-node"}},
                "comp-node": {
                    "locked": {
                        "type": "git",
                        "url": lock_url,
                        "rev": revision,
                        "narHash": sri_nar_hash(revision),
                        "ref": "refs/heads/main",
                    },
                    "original": {
                        "type": "git",
                        "url": lock_url,
                    },
                },
            },
            "root": "root",
            "version": 7,
        })
    )


def initialize_comp_parent(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "bin").mkdir(parents=True, exist_ok=True)
    qualifier = path / "bin/qualify-promotion"
    qualifier.write_text("#!/bin/sh\nexit 97\n")
    qualifier.chmod(0o755)
    write_comp_flake_nix(path / "flake.nix", PREIMAGE_DECLARATION_URL)
    write_comp_flake_lock(path / "flake.lock", OLD_REVISION, PREIMAGE_URL)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Composition Transition Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "comp-transition@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial parent composition commit"], cwd=path, check=True, capture_output=True)


def initialize_comp_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    (path / "flake.nix").write_text("{}\n")
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Comp Repo Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "comp-repo@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "comp commit 1"], cwd=path, check=True, capture_output=True)
    (path / "version.txt").write_text("v1.0.0\n")
    subprocess.run(["git", "add", "version.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "comp commit 2"], cwd=path, check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


class CompositionTransitionRunner:
    def __init__(self, entry_contract_doc: dict, cand_contract_doc: dict, target_revision: str, store_dir: str):
        self.entry_contract = entry_contract_doc
        self.cand_contract = cand_contract_doc
        self.target_revision = target_revision
        self.store_dir = store_dir
        self.calls: list[tuple[list[str], Path]] = []
        self.host_derivations = [
            "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-stage-a-system.drv"
        ]

    def __call__(self, argv, cwd=None, **kwargs):
        argv = list(argv)
        cwd = Path(cwd) if cwd is not None else None
        self.calls.append((argv, cwd))
        if argv[0] == "git":
            return subprocess.run(argv, cwd=cwd, **kwargs)
        if argv[0].endswith("qualify-promotion"):
            result = {
                "schema": "fixture-base-promotion-result-v1",
                "input": "composition-nd",
                "registry": {"name": "composition-nd"},
                "candidate": {
                    "url": f"{CANDIDATE_URL}?rev={self.target_revision}",
                    "requestedRev": self.target_revision,
                    "resolvedRev": self.target_revision,
                    "resolvedUrl": CANDIDATE_URL,
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
            # Differentiate whether evaluating entry tree or candidate tree:
            # Check cwd's flake.nix or candidate contract
            if cwd and (cwd / "flake.nix").exists():
                text = (cwd / "flake.nix").read_text()
                if CANDIDATE_DECLARATION_URL in text or CANDIDATE_URL in text:
                    return subprocess.CompletedProcess(argv, 0, json.dumps(self.cand_contract), "")
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.entry_contract), "")
        if argv[:3] == ["nix", "eval", "--raw"]:
            return subprocess.CompletedProcess(argv, 0, self.host_derivations[0], "")
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
            write_comp_flake_lock(cwd / "flake.lock", self.target_revision, CANDIDATE_URL)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["nix", "build"]:
            return subprocess.CompletedProcess(argv, 0, "/nix/store/stage-a-system\n", "")
        raise AssertionError(f"unexpected command: {argv}")


def comp_transition_fixture(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch, *, layered: bool = True
):
    (
        deployment_workflow,
        promotion_plan,
        promotion_workflow,
        promotion_composition,
        source_catalog,
        paths,
    ) = promotion_modules

    selected = tmp_path / "parent-composition"
    initialize_comp_parent(selected)

    comp_path = tmp_path / "composition_dinas"
    comp_rev = initialize_comp_repo(comp_path)

    if layered:
        entry_contract_doc = layered_private_parent_contract(selected, PREIMAGE_URL)
        cand_contract_doc = layered_private_parent_contract(selected, CANDIDATE_DECLARATION_URL)
    else:
        entry_contract_doc = direct_composition_contract(selected, PREIMAGE_URL)
        cand_contract_doc = direct_composition_contract(selected, CANDIDATE_URL)

    state = tmp_path / "state"
    state.mkdir(mode=0o700)

    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotion_workflow, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotion_workflow, "_guard_processes", lambda *_args: None)

    # Model only the fake fetchTree result; never inspect an arbitrary host store entry.
    store_fixture = tmp_path / "store-source"
    store_fixture.mkdir()
    store_dir = "/nix/store/00000000000000000000000000000000-composition-fixture"
    real_stat = Path.stat
    monkeypatch.setattr(
        Path, "stat",
        lambda path, **kwargs: real_stat(
            store_fixture if str(path) == store_dir else path, **kwargs
        ),
    )
    runner = CompositionTransitionRunner(
        entry_contract_doc, cand_contract_doc, comp_rev, store_dir
    )

    comp_parent_entry = source_catalog.SourceCatalogEntry(
        key="composition",
        checkout=selected,
        repository=selected.as_uri(),
        release_ref="refs/heads/main",
        publication=None,
    )
    # The operator catalog entry for key composition-nd points to local checkout composition_dinas and candidate URL
    comp_target_entry = source_catalog.SourceCatalogEntry(
        key="composition-nd",
        checkout=comp_path,
        repository=CANDIDATE_URL,
        release_ref="refs/heads/main",
        publication=None,
    )

    workflow = deployment_workflow.Workflow(
        repo_root, runner, sources=(comp_parent_entry, comp_target_entry)
    )

    return (
        workflow,
        selected,
        comp_path,
        comp_rev,
        entry_contract_doc,
        cand_contract_doc,
        state,
        runner,
        promotion_workflow,
        promotion_plan,
        promotion_composition,
        paths,
    )


def test_composition_repository_transition_prepare_and_apply_layered_success(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    (
        workflow,
        selected,
        comp_path,
        comp_rev,
        entry_contract_doc,
        cand_contract_doc,
        state,
        runner,
        promotion_workflow,
        promotion_plan,
        promotion_composition,
        paths,
    ) = comp_transition_fixture(promotion_modules, repo_root, tmp_path, monkeypatch, layered=True)

    output = tmp_path / "output-plan"
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "composition-nd",
        comp_rev,
        output,
        300,
        allow_local_fixture=True,
        allow_composition_repository_transition=True,
    )

    doc = artifact.document
    assert doc["schema"] == promotion_plan.PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1
    assert doc["status"] == "READY_FOR_REVIEW", f"Status: {doc.get('status')}, blockers: {doc.get('blockers')}"
    assert doc["input"] == "composition-nd"
    assert doc["canonical_repository_url"] == CANDIDATE_URL
    assert doc["closure_authorization"]["schema"] == promotion_composition.COMPOSITION_AUTHORIZATION_SCHEMA
    assert doc["closure_authorization"]["repositoryUrl"] == CANDIDATE_URL
    assert doc["closure_authorization"]["previousRepositoryUrl"] == PREIMAGE_URL
    assert doc["commit"]["changed_paths"] == ["flake.nix", "flake.lock"]

    # Candidate contract is bound and updated
    assert "candidate_contract" in doc
    assert doc["candidate_contract"]["lowerPromotion"]["composition"]["repositoryUrl"] == CANDIDATE_DECLARATION_URL
    assert doc["contract"]["lowerPromotion"]["composition"]["repositoryUrl"] == PREIMAGE_URL

    # Retained artifacts
    retained_dir = artifact.directory / "retained-lock"
    assert (retained_dir / "flake.nix.candidate").exists()
    assert (retained_dir / "flake.nix.preimage").exists()
    assert (retained_dir / "flake.lock.candidate").exists()
    assert (retained_dir / "flake.lock.preimage").exists()
    cand_lock = json.loads((retained_dir / "flake.lock.candidate").read_text())
    assert set(cand_lock.keys()) == {"nodes", "root", "version"}
    assert "byte_sha256" not in cand_lock

    delta = json.loads((retained_dir / "delta.json").read_text())
    assert delta["authorization"]["kind"] == "composition-repository-transition"
    assert delta["composition_repository_transition"] is True
    assert "candidate_flake_nix_sha256" in delta
    assert "preimage_flake_nix_sha256" in delta

    # Candidate declaration contains candidate URL and removes preimage URL
    candidate_nix_text = (retained_dir / "flake.nix.candidate").read_text()
    assert CANDIDATE_DECLARATION_URL in candidate_nix_text
    assert PREIMAGE_DECLARATION_URL not in candidate_nix_text

    # Apply promotion
    confirm_fn = lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}"
    result_code = promotion_workflow.apply_promotion(
        workflow, artifact.directory, confirm=confirm_fn
    )
    assert result_code == 0

    # Verify post-apply state in selected composition
    installed_nix = (selected / "flake.nix").read_text()
    assert CANDIDATE_DECLARATION_URL in installed_nix
    assert PREIMAGE_DECLARATION_URL not in installed_nix

    installed_lock = json.loads((selected / "flake.lock").read_text())
    assert set(installed_lock.keys()) == {"nodes", "root", "version"}
    assert "byte_sha256" not in installed_lock
    assert installed_lock["nodes"]["comp-node"]["locked"]["url"] == CANDIDATE_URL
    assert installed_lock["nodes"]["comp-node"]["locked"]["rev"] == comp_rev

    # Verify replay reconciles consumed commit cleanly
    replay_code = promotion_workflow.apply_promotion(
        workflow, artifact.directory, confirm=confirm_fn
    )
    assert replay_code == 0


def test_composition_repository_transition_prepare_and_apply_direct_success(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    (
        workflow,
        selected,
        comp_path,
        comp_rev,
        entry_contract_doc,
        cand_contract_doc,
        state,
        runner,
        promotion_workflow,
        promotion_plan,
        promotion_composition,
        paths,
    ) = comp_transition_fixture(promotion_modules, repo_root, tmp_path, monkeypatch, layered=False)

    output = tmp_path / "output-plan-direct"
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "composition-nd",
        comp_rev,
        output,
        300,
        allow_local_fixture=True,
        allow_composition_repository_transition=True,
    )

    doc = artifact.document
    assert doc["schema"] == promotion_plan.PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1
    assert doc["status"] == "READY_FOR_REVIEW"
    assert doc["candidate_contract"]["modules"][0]["url"] == CANDIDATE_URL

    confirm_fn = lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}"
    result_code = promotion_workflow.apply_promotion(
        workflow, artifact.directory, confirm=confirm_fn
    )
    assert result_code == 0

    installed_lock = json.loads((selected / "flake.lock").read_text())
    assert set(installed_lock.keys()) == {"nodes", "root", "version"}
    assert "byte_sha256" not in installed_lock


def test_composition_repository_transition_rejects_non_composition_input(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    (
        workflow,
        selected,
        comp_path,
        comp_rev,
        *_,
        promotion_workflow,
        promotion_plan,
        _,
        paths,
    ) = comp_transition_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)

    output = tmp_path / "output-plan-invalid-input"
    with pytest.raises(paths.ConfigError, match="valid only for input composition-nd"):
        promotion_workflow.prepare_promotion(
            workflow,
            selected,
            "core",
            comp_rev,
            output,
            300,
            allow_local_fixture=True,
            allow_composition_repository_transition=True,
        )


def test_composition_repository_transition_rejects_combining_core_and_comp(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    (
        workflow,
        selected,
        comp_path,
        comp_rev,
        *_,
        promotion_workflow,
        promotion_plan,
        _,
        paths,
    ) = comp_transition_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)

    output = tmp_path / "output-plan-combined"
    with pytest.raises(paths.ConfigError, match="cannot be combined"):
        promotion_workflow.prepare_promotion(
            workflow,
            selected,
            "composition-nd",
            comp_rev,
            output,
            300,
            allow_local_fixture=True,
            allow_core_repository_transition=True,
            allow_composition_repository_transition=True,
        )


def test_composition_repository_transition_rejects_non_main_ref(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    (
        workflow,
        selected,
        comp_path,
        comp_rev,
        entry_contract_doc,
        cand_contract_doc,
        state,
        runner,
        promotion_workflow,
        promotion_plan,
        _,
        paths,
    ) = comp_transition_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)

    # Change lock node to non-main ref
    lock_data = json.loads((selected / "flake.lock").read_text())
    lock_data["nodes"]["comp-node"]["locked"]["ref"] = "refs/heads/feature"
    (selected / "flake.lock").write_text(json.dumps(lock_data))
    subprocess.run(["git", "add", "flake.lock"], cwd=selected, check=True)
    subprocess.run(["git", "commit", "-m", "feature ref lock"], cwd=selected, check=True)

    output = tmp_path / "output-plan-feature-ref"
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "composition-nd",
        comp_rev,
        output,
        300,
        allow_local_fixture=True,
        allow_composition_repository_transition=True,
    )
    assert artifact.document["status"] == "BLOCKED"
    assert any("non-main reference" in b or "main release ref" in b for b in artifact.document["blockers"])


def test_composition_repository_transition_rejects_missing_flag(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    (
        workflow,
        selected,
        comp_path,
        comp_rev,
        *_,
        promotion_workflow,
        promotion_plan,
        _,
        paths,
    ) = comp_transition_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)

    output = tmp_path / "output-plan-missing-flag"
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "composition-nd",
        comp_rev,
        output,
        300,
        allow_local_fixture=True,
        allow_composition_repository_transition=False,
    )
    assert artifact.document["status"] == "BLOCKED"
    assert any("does not match expected" in b for b in artifact.document["blockers"])


def test_composition_repository_transition_rejects_old_candidate_catalog(
    promotion_modules, repo_root, tmp_path, monkeypatch
):
    fixture = comp_transition_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)
    workflow, selected, _, revision = fixture[:4]
    workflow.sources = tuple(
        replace(entry, repository=PREIMAGE_URL) if entry.key == "composition-nd" else entry
        for entry in workflow.sources
    )
    artifact = fixture[8].prepare_promotion(
        workflow, selected, "composition-nd", revision, tmp_path / "old-catalog", 300,
        allow_local_fixture=True, allow_composition_repository_transition=True,
    )
    assert artifact.document["status"] == "BLOCKED"
    assert any("must point to canonical composition_dinas repository" in blocker
               for blocker in artifact.document["blockers"])
    assert not any(call[:3] == ["nix", "flake", "lock"] for call, _ in fixture[7].calls)


@pytest.mark.parametrize("side", ["preimage", "candidate"])
@pytest.mark.parametrize("field", ["original", "locked"])
def test_composition_repository_transition_rejects_wrong_owner(
    promotion_modules, tmp_path, side, field
):
    pre_path, cand_path = tmp_path / "before", tmp_path / "after"
    write_comp_flake_lock(pre_path, OLD_REVISION, PREIMAGE_URL)
    write_comp_flake_lock(cand_path, REVISION, CANDIDATE_URL)
    before, after = json.loads(pre_path.read_text()), json.loads(cand_path.read_text())
    target = before if side == "preimage" else after
    target["nodes"]["comp-node"][field]["url"] = target["nodes"]["comp-node"][field]["url"].replace(
        "/Knowledge-Forge-AI/", "/other-owner/"
    )
    with pytest.raises(promotion_modules[-1].ConfigError, match="authorizes only the exact"):
        promotion_modules[3].authorize_composition_repository_transition(
            before, after, "comp-node", REVISION
        )


def test_composition_retained_plan_tampering_and_cross_replay(
    promotion_modules, repo_root, tmp_path, monkeypatch
):
    fixture = comp_transition_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)
    workflow, selected, _, revision = fixture[:4]
    promotion_workflow, plan, composition, paths = fixture[8:]
    artifact = promotion_workflow.prepare_promotion(
        workflow, selected, "composition-nd", revision, tmp_path / "tamper-plan", 300,
        allow_local_fixture=True, allow_composition_repository_transition=True,
    )
    assert artifact.document["status"] == "READY_FOR_REVIEW"
    binding = importlib.import_module("nix_darwin_local.promotion_binding")
    core = importlib.import_module("nix_darwin_local.promotion_core")
    before = artifact.document["entry_lock"]["normalized"]
    after = artifact.document["candidate_lock"]["normalized"]

    # Ordinary selected-owned promotion cannot authorize this URL migration.
    with pytest.raises(paths.ConfigError):
        binding.authorize_transition(
            before, after, "comp-node", PREIMAGE_URL, revision,
            input_name="composition-nd", allow_file=False,
            expected_ref="refs/heads/main", allow_implicit_main=True,
        )

    core_before, core_after = copy.deepcopy(before), copy.deepcopy(after)
    for lock, url in (
        (core_before, core.CORE_REPOSITORY_TRANSITION_PREIMAGE_URL),
        (core_after, core.CORE_REPOSITORY_TRANSITION_CANDIDATE_URL),
    ):
        lock["nodes"]["root"]["inputs"] = {"core": "comp-node"}
        for field in ("original", "locked"):
            lock["nodes"]["comp-node"][field]["url"] = url
    core_record = core.authorize_core_lock_transition(
        core_before, core_after, revision,
        repository_url=core.CORE_REPOSITORY_TRANSITION_PREIMAGE_URL,
        allow_repository_transition=True,
    )
    assert binding.validate_authorization(core_record) == core_record
    # Valid evidence from either transition is rejected by the other plan schema.
    for schema, record, message in (
        (plan.PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1, core_record,
         "composition repository transition plan requires"),
        (plan.PLAN_SCHEMA_CORE_REPOSITORY_TRANSITION_V1,
         artifact.document["closure_authorization"], "core repository transition plan requires"),
    ):
        replay = copy.deepcopy(artifact.document)
        replay["schema"], replay["closure_authorization"] = schema, record
        with pytest.raises(paths.ConfigError, match=message):
            plan._validate_ready_artifacts(replay, replay["contract"])

    original_plan = (artifact.directory / "plan.json").read_bytes()
    delta_path = artifact.directory / artifact.document["lock_authorization_evidence"]["path"]
    original_delta = delta_path.read_bytes()
    preimage = {name: (selected / name).read_bytes() for name in ("flake.nix", "flake.lock")}

    # Re-seal public digests so rejection proves semantic validation, not stale hashing.
    for mutation in (
        "ordinary-schema", "core-schema", "false-flag", "core-flag", "core-record",
        "binding-repository", "binding-spelling", "binding-rule", "binding-module", "binding-extra",
        "binding-missing", "binding-not-object", "candidate-selector",
    ):
        document = copy.deepcopy(artifact.document)
        delta = json.loads(original_delta)
        if mutation == "ordinary-schema":
            document["schema"] = plan.PLAN_SCHEMA_V3
        elif mutation == "core-schema":
            document["schema"] = plan.PLAN_SCHEMA_CORE_REPOSITORY_TRANSITION_V1
        elif mutation == "false-flag":
            delta["composition_repository_transition"] = False
        elif mutation == "core-flag":
            del delta["composition_repository_transition"]
            delta["core_repository_transition"] = True
        elif mutation in {"binding-repository", "binding-spelling"}:
            wrong_url = (
                CANDIDATE_URL.replace("/Knowledge-Forge-AI/", "/other-owner/")
                if mutation == "binding-repository" else CANDIDATE_URL.removesuffix(".git")
            )
            document["input_resolution"]["repository_url"] = wrong_url
            document["canonical_repository_url"] = wrong_url
            document["canonical_transport_url"] = wrong_url
            document["candidate_ref"] = f"{wrong_url}?rev={revision}"
        elif mutation in {"binding-rule", "binding-module"}:
            document["input_resolution"][mutation.removeprefix("binding-")] = "different"
        elif mutation == "binding-extra":
            document["input_resolution"]["extra"] = True
        elif mutation == "binding-missing":
            del document["input_resolution"]["repository_url"]
        elif mutation == "binding-not-object":
            document["input_resolution"] = None
        elif mutation == "candidate-selector":
            document["candidate_ref"] += "&dir=other"
        else:
            delta["authorization"] = {"kind": "core-repository-transition", "record": core_record}
        delta_bytes = plan.canonical_bytes(delta)
        delta_path.write_bytes(delta_bytes)
        document["lock_authorization_evidence"]["sha256"] = hashlib.sha256(delta_bytes).hexdigest()
        document["plan_digest"] = ""
        document["plan_digest"] = hashlib.sha256(plan.canonical_bytes(document)).hexdigest()
        (artifact.directory / "plan.json").write_bytes(plan.canonical_bytes(document))
        if mutation == "binding-spelling":
            with pytest.raises(paths.ConfigError, match="native input URL resolution drifted"):
                plan._validate_native_context(document)
        with pytest.raises(paths.ConfigError):
            plan.load_plan(artifact.directory)
        with pytest.raises(paths.ConfigError):
            promotion_workflow.apply_promotion(
                workflow, artifact.directory,
                confirm=lambda _: pytest.fail("tampered plan reached confirmation"),
            )
        assert {name: (selected / name).read_bytes() for name in preimage} == preimage
    delta_path.write_bytes(original_delta)
    (artifact.directory / "plan.json").write_bytes(original_plan)
    assert plan.load_plan(artifact.directory).digest == artifact.digest


def test_composition_repository_transition_rejects_candidate_contract_drift(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    (
        workflow,
        selected,
        comp_path,
        comp_rev,
        entry_contract_doc,
        cand_contract_doc,
        state,
        runner,
        promotion_workflow,
        promotion_plan,
        _,
        paths,
    ) = comp_transition_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)

    # Corrupt candidate contract by adding a check
    runner.cand_contract = copy.deepcopy(cand_contract_doc)
    runner.cand_contract["checks"].append("unauthorized_check")

    output = tmp_path / "output-plan-drift"
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "composition-nd",
        comp_rev,
        output,
        300,
        allow_local_fixture=True,
        allow_composition_repository_transition=True,
    )
    assert artifact.document["status"] == "BLOCKED"
    assert any("contract drifted" in b or "contract fields drifted" in b for b in artifact.document["blockers"])


def test_composition_repository_transition_rollback_on_install_failure(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    (
        workflow,
        selected,
        comp_path,
        comp_rev,
        *_,
        promotion_workflow,
        promotion_plan,
        _,
        paths,
    ) = comp_transition_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)

    output = tmp_path / "output-plan-rollback"
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "composition-nd",
        comp_rev,
        output,
        300,
        allow_local_fixture=True,
        allow_composition_repository_transition=True,
    )
    assert artifact.document["status"] == "READY_FOR_REVIEW"

    # Preimage contents
    orig_nix = (selected / "flake.nix").read_bytes()
    orig_lock = (selected / "flake.lock").read_bytes()

    # Trigger failure during qualify step after install
    monkeypatch.setattr(
        promotion_workflow,
        "_qualify_locked_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(paths.ConfigError("simulated post-install qualification error")),
    )

    confirm_fn = lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}"
    result_code = promotion_workflow.apply_promotion(
        workflow, artifact.directory, confirm=confirm_fn
    )
    assert result_code == 1

    # Both flake.nix and flake.lock must be restored to original preimage
    assert (selected / "flake.nix").read_bytes() == orig_nix
    assert (selected / "flake.lock").read_bytes() == orig_lock

    result_json = json.loads((artifact.directory / "result.json").read_text())
    assert result_json["terminal_state"] == "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED"


def test_validate_composition_repository_authorization_branches(promotion_modules):
    promotion_composition = promotion_modules[3]
    paths = promotion_modules[-1]
    validate_auth = promotion_composition.validate_composition_repository_authorization

    with pytest.raises(paths.ConfigError, match="must be an object"):
        validate_auth("not-a-dict")

    base_auth = {
        "schema": promotion_composition.COMPOSITION_AUTHORIZATION_SCHEMA,
        "selectedInput": "composition-nd",
        "selectedNode": "comp-node",
        "repositoryUrl": promotion_composition.COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL,
        "previousRepositoryUrl": promotion_composition.COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_URL,
        "requestedRevision": "0123456789abcdef0123456789abcdef01234567",
        "preimageOwnedNodes": ["comp-node"],
        "candidateOwnedNodes": ["comp-node"],
        "addedNodes": [],
        "removedNodes": [],
        "changedNodes": ["comp-node"],
        "outsideGraphDigest": "a" * 64,
        "sharedFollowsDigest": "b" * 64,
        "authorizationDigest": "",
    }
    base_auth["authorizationDigest"] = promotion_composition._authorization_digest(base_auth)

    # Valid base auth passes
    assert validate_auth(base_auth) == base_auth

    # Missing fields / extra fields
    tampered = dict(base_auth)
    del tampered["schema"]
    with pytest.raises(paths.ConfigError, match="fields are invalid"):
        validate_auth(tampered)

    tampered = dict(base_auth, extra="field")
    with pytest.raises(paths.ConfigError, match="fields are invalid"):
        validate_auth(tampered)

    # Schema invalid
    tampered = dict(base_auth, schema="wrong-schema")
    with pytest.raises(paths.ConfigError, match="schema is invalid"):
        validate_auth(tampered)

    # Selected input invalid
    tampered = dict(base_auth, selectedInput="wrong-input")
    with pytest.raises(paths.ConfigError, match="must be composition-nd"):
        validate_auth(tampered)

    # Selected node invalid
    for bad_node in (123, ""):
        tampered = dict(base_auth, selectedNode=bad_node)
        with pytest.raises(paths.ConfigError, match="selected node identity is invalid"):
            validate_auth(tampered)

    # Destination URL invalid
    tampered = dict(base_auth, repositoryUrl="ssh://git@github.com/Knowledge-Forge-AI/wrong.git")
    with pytest.raises(paths.ConfigError, match="destination repository URL evidence is invalid"):
        validate_auth(tampered)

    # Preimage URL invalid
    tampered = dict(base_auth, previousRepositoryUrl="ssh://git@github.com/Knowledge-Forge-AI/wrong.git")
    with pytest.raises(paths.ConfigError, match="preimage repository URL evidence is invalid"):
        validate_auth(tampered)

    # Revision invalid
    for bad_rev in ("not-hex", "short", 123):
        tampered = dict(base_auth, requestedRevision=bad_rev)
        with pytest.raises(paths.ConfigError, match="requested revision evidence is invalid"):
            validate_auth(tampered)

    # Node identities not canonical
    for field in ("preimageOwnedNodes", "candidateOwnedNodes", "addedNodes", "removedNodes", "changedNodes"):
        tampered = dict(base_auth, **{field: "not-a-list"})
        with pytest.raises(paths.ConfigError, match="node identity evidence is not canonical"):
            validate_auth(tampered)
        tampered = dict(base_auth, **{field: [123]})
        with pytest.raises(paths.ConfigError, match="node identity evidence is not canonical"):
            validate_auth(tampered)
        tampered = dict(base_auth, **{field: ["b", "a"]})
        with pytest.raises(paths.ConfigError, match="node identity evidence is not canonical"):
            validate_auth(tampered)
        tampered = dict(base_auth, **{field: ["a", "a"]})
        with pytest.raises(paths.ConfigError, match="node identity evidence is not canonical"):
            validate_auth(tampered)

    # Selected node not in changedNodes
    tampered = dict(base_auth, changedNodes=["other-node"])
    with pytest.raises(paths.ConfigError, match="changed-node evidence is invalid"):
        validate_auth(tampered)

    # Digest invalid
    for field in ("outsideGraphDigest", "sharedFollowsDigest", "authorizationDigest"):
        tampered = dict(base_auth, **{field: "short"})
        with pytest.raises(paths.ConfigError, match="digest is invalid"):
            validate_auth(tampered)
        tampered = dict(base_auth, **{field: "g" * 64})
        with pytest.raises(paths.ConfigError, match="digest is invalid"):
            validate_auth(tampered)

    # Digest mismatch
    tampered = dict(base_auth, authorizationDigest="0" * 64)
    with pytest.raises(paths.ConfigError, match="does not match content"):
        validate_auth(tampered)


def test_authorize_composition_repository_transition_branches(promotion_modules):
    promotion_composition = promotion_modules[3]
    paths = promotion_modules[-1]
    authorize = promotion_composition.authorize_composition_repository_transition

    rev1 = "1111111111111111111111111111111111111111"
    rev2 = "2222222222222222222222222222222222222222"

    def make_lock(rev, url, *, dir_val=None, type_val="git", root_input="composition-nd", node_name="comp-node", extra_nodes=None):
        orig = {"type": type_val, "url": url}
        if dir_val:
            orig["dir"] = dir_val
        locked = {"type": type_val, "url": url, "rev": rev, "narHash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}
        if dir_val:
            locked["dir"] = dir_val
        nodes = {
            "root": {"inputs": {root_input: node_name}},
            node_name: {"original": orig, "locked": locked},
        }
        if extra_nodes:
            nodes.update(extra_nodes)
        return {"version": 7, "root": "root", "nodes": nodes}

    pre_url = promotion_composition.COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_URL
    cand_url = promotion_composition.COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL

    pre = make_lock(rev1, pre_url)
    cand = make_lock(rev2, cand_url)

    # Valid call succeeds
    res = authorize(pre, cand, "comp-node", rev2)
    assert res["schema"] == promotion_composition.COMPOSITION_AUTHORIZATION_SCHEMA

    # Invalid revision
    with pytest.raises(paths.ConfigError, match="revision is invalid"):
        authorize(pre, cand, "comp-node", "invalid-rev")

    # Version mismatch
    bad_cand = copy.deepcopy(cand)
    bad_cand["version"] = 8
    with pytest.raises(paths.ConfigError, match="root or version changed"):
        authorize(pre, bad_cand, "comp-node", rev2)

    # Root mismatch
    bad_cand = copy.deepcopy(cand)
    bad_cand["root"] = "other"
    bad_cand["nodes"]["other"] = bad_cand["nodes"]["root"]
    with pytest.raises(paths.ConfigError, match="root or version changed"):
        authorize(pre, bad_cand, "comp-node", rev2)

    # Root node changed
    bad_cand = copy.deepcopy(cand)
    bad_cand["nodes"]["root"]["inputs"]["extra"] = "comp-node"
    with pytest.raises(paths.ConfigError, match="root node or direct input mapping changed"):
        authorize(pre, bad_cand, "comp-node", rev2)

    # Missing composition-nd in root inputs
    bad_pre = make_lock(rev1, pre_url, root_input="other-input")
    bad_cand = make_lock(rev2, cand_url, root_input="other-input")
    with pytest.raises(paths.ConfigError, match="requires direct root input composition-nd"):
        authorize(bad_pre, bad_cand, "comp-node", rev2)

    # Selected node mismatch
    bad_cand = make_lock(rev2, cand_url, node_name="comp-other")
    bad_cand["nodes"]["root"]["inputs"]["composition-nd"] = "comp-other"
    with pytest.raises(paths.ConfigError, match="root node or direct input mapping changed"):
        authorize(pre, bad_cand, "comp-node", rev2)

    # Node name not in before/after
    with pytest.raises(paths.ConfigError, match="selected lock node identity is invalid"):
        authorize(pre, cand, "nonexistent", rev2)

    # Node name is root
    with pytest.raises(paths.ConfigError, match="selected lock node identity is invalid"):
        authorize(pre, cand, "root", rev2)

    # Node name argument does not match root input
    extra = {"other": {"original": {"type": "git", "url": "https://example.invalid"}, "locked": {"type": "git", "url": "https://example.invalid", "rev": rev1}}}
    pre_with_extra = make_lock(rev1, pre_url, extra_nodes=extra)
    cand_with_extra = make_lock(rev2, cand_url, extra_nodes=extra)
    with pytest.raises(paths.ConfigError, match="composition repository transition selected node mismatch"):
        authorize(pre_with_extra, cand_with_extra, "other", rev2)

    # Outside graph changed
    extra1 = {"unrelated": {"original": {"type": "git", "url": "https://example.invalid/1"}, "locked": {"type": "git", "url": "https://example.invalid/1", "rev": rev1}}}
    extra2 = {"unrelated": {"original": {"type": "git", "url": "https://example.invalid/2"}, "locked": {"type": "git", "url": "https://example.invalid/2", "rev": rev1}}}
    bad_pre = make_lock(rev1, pre_url, extra_nodes=extra1)
    bad_cand = make_lock(rev2, cand_url, extra_nodes=extra2)
    with pytest.raises(paths.ConfigError, match="changed nodes outside the selected owned closure"):
        authorize(bad_pre, bad_cand, "comp-node", rev2)

    # Static fields check
    bad_cand = copy.deepcopy(cand)
    bad_cand["nodes"]["comp-node"]["flake"] = False
    with pytest.raises(paths.ConfigError, match="selected root declaration changed"):
        authorize(pre, bad_cand, "comp-node", rev2)

    # Original dir selector
    bad_cand = make_lock(rev2, cand_url, dir_val="subdir")
    with pytest.raises(paths.ConfigError, match="does not accept directory selector"):
        authorize(pre, bad_cand, "comp-node", rev2)

    # Original type != git
    bad_cand = make_lock(rev2, cand_url, type_val="tarball")
    with pytest.raises(paths.ConfigError, match="requires Git source type"):
        authorize(pre, bad_cand, "comp-node", rev2)

    # Original URL wrong
    bad_cand = make_lock(rev2, "ssh://git@github.com/Knowledge-Forge-AI/other.git")
    with pytest.raises(paths.ConfigError, match="authorizes only the exact"):
        authorize(pre, bad_cand, "comp-node", rev2)

    # Locked dir selector
    bad_cand = copy.deepcopy(cand)
    bad_cand["nodes"]["comp-node"]["locked"]["dir"] = "sub"
    with pytest.raises(paths.ConfigError, match="does not accept directory selector"):
        authorize(pre, bad_cand, "comp-node", rev2)

    # Locked type != git
    bad_cand = copy.deepcopy(cand)
    bad_cand["nodes"]["comp-node"]["locked"]["type"] = "tarball"
    with pytest.raises(paths.ConfigError, match="requires Git locked type"):
        authorize(pre, bad_cand, "comp-node", rev2)

    # Locked URL wrong
    bad_cand = copy.deepcopy(cand)
    bad_cand["nodes"]["comp-node"]["locked"]["url"] = "ssh://git@github.com/Knowledge-Forge-AI/other.git"
    with pytest.raises(paths.ConfigError, match="authorizes only the exact"):
        authorize(pre, bad_cand, "comp-node", rev2)

    # Candidate rev mismatch
    with pytest.raises(paths.ConfigError, match="candidate does not select requested revision"):
        authorize(pre, cand, "comp-node", "3333333333333333333333333333333333333333")

    # Preimage rev invalid
    bad_pre = copy.deepcopy(pre)
    bad_pre["nodes"]["comp-node"]["locked"]["rev"] = "invalid-rev"
    with pytest.raises(paths.ConfigError, match="preimage revision is invalid"):
        authorize(bad_pre, cand, "comp-node", rev2)


def test_validate_composition_transition_contract_branches(tmp_path, promotion_modules, monkeypatch):
    promotion_composition = promotion_modules[3]
    paths = promotion_modules[-1]
    validate_contract = promotion_composition.validate_composition_transition_contract

    pre_url = promotion_composition.COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_URL
    cand_url = promotion_composition.COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL

    layered_entry = layered_private_parent_contract(tmp_path / "parent", pre_url)
    layered_cand = layered_private_parent_contract(tmp_path / "parent", cand_url)
    direct_entry = direct_composition_contract(tmp_path / "direct", pre_url)
    direct_cand = direct_composition_contract(tmp_path / "direct", cand_url)

    # Both dialects accept identity-equivalent Nix URL spelling, without other drift.
    for entry, candidate in (
        (direct_entry, direct_composition_contract(tmp_path / "direct", "git+" + cand_url)),
        (layered_entry, layered_private_parent_contract(tmp_path / "parent", "git+" + cand_url)),
    ):
        validate_contract(entry, candidate, allow_file=True)

    # Dialect mismatch
    with pytest.raises(paths.ConfigError, match="contract dialect changed"):
        validate_contract(layered_entry, direct_cand, allow_file=True)

    # Entry repo url not preimage
    bad_entry = direct_composition_contract(tmp_path / "direct", "ssh://git@github.com/Knowledge-Forge-AI/other.git")
    with pytest.raises(paths.ConfigError, match="does not resolve composition-nd to the preimage repository"):
        validate_contract(bad_entry, direct_cand, allow_file=True)

    # Candidate repo url not candidate
    bad_cand = direct_composition_contract(tmp_path / "direct", "ssh://git@github.com/Knowledge-Forge-AI/other.git")
    with pytest.raises(paths.ConfigError, match="does not resolve composition-nd to the candidate repository"):
        validate_contract(direct_entry, bad_cand, allow_file=True)

    # Monkeypatch validate_contract and resolve_contract_input for structural drift checks
    monkeypatch.setattr(promotion_composition, "validate_contract", lambda *args, **kwargs: None)

    def mock_resolve(contract, input_name):
        for mod in contract.get("modules", []):
            if mod.get("input") == input_name:
                return {"repository_url": mod["url"]}
        lower = contract.get("lowerPromotion", {})
        if lower.get("composition"):
            return {"repository_url": lower["composition"]["repositoryUrl"]}
        return {"repository_url": pre_url}

    monkeypatch.setattr(promotion_composition, "resolve_contract_input", mock_resolve)

    # Layered: requiredLowerPromotion changed
    bad_cand = copy.deepcopy(layered_cand)
    bad_cand["requiredLowerPromotion"]["version"] = 99
    with pytest.raises(paths.ConfigError, match="changed requiredLowerPromotion"):
        validate_contract(layered_entry, bad_cand, allow_file=True)

    # Layered: candidateInput != composition-nd
    bad_entry = copy.deepcopy(layered_entry)
    bad_entry["requiredLowerPromotion"]["candidateInput"] = "other-input"
    bad_cand = copy.deepcopy(layered_cand)
    bad_cand["requiredLowerPromotion"]["candidateInput"] = "other-input"
    with pytest.raises(paths.ConfigError, match="candidateInput must be composition-nd"):
        validate_contract(bad_entry, bad_cand, allow_file=True)

    # Layered: lowerPromotion drifted
    bad_cand = copy.deepcopy(layered_cand)
    bad_cand["lowerPromotion"]["composition"]["name"] = "changed-name"
    with pytest.raises(paths.ConfigError, match="lower promotion contract drifted"):
        validate_contract(layered_entry, bad_cand, allow_file=True)

    # Layered: outer contract drifted
    bad_cand = copy.deepcopy(layered_cand)
    bad_cand["composition"]["name"] = "changed-name"
    with pytest.raises(paths.ConfigError, match="candidate promotion contract drifted"):
        validate_contract(layered_entry, bad_cand, allow_file=True)

    # Direct: module drifted
    bad_cand = copy.deepcopy(direct_cand)
    bad_cand["modules"][0]["name"] = "changed-module-name"
    with pytest.raises(paths.ConfigError, match="candidate promotion contract drifted beyond the authorized module URL transition"):
        validate_contract(direct_entry, bad_cand, allow_file=True)
