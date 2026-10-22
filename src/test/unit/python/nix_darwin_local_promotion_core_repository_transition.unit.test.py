# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_workflow.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_plan.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_binding.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/deployment_workflow.py
"""Unit tests for the core repository-identity transition promotion path.

Validates:
1. Strict schema and validity constraints: input 'core', release ref 'refs/heads/main',
   historical preimage URL, canonical candidate URL, root layout on both sides.
2. Negative scenarios: non-core input, non-main ref, missing flag, mismatched URLs,
   tampered declarations, invalid changed_paths.
3. Dual declaration-and-lock artifact generation under retained-lock/ (flake.nix.candidate,
   flake.lock.candidate, delta.json with declaration metadata).
4. Plan validation under nix-update-promotion-core-repository-transition-plan-v1.
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

REVISION = "a" * 40
OLD_REVISION = "b" * 40

PREIMAGE_URL = "git+ssh://git@github.com/Knowledge-Forge-AI/.flakes.git"
CANDIDATE_URL = "git+ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git"
PREIMAGE_LOCK_URL = "ssh://git@github.com/Knowledge-Forge-AI/.flakes.git"
CANDIDATE_LOCK_URL = "ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git"


def sri_nar_hash(revision: str) -> str:
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


def core_contract(composition_dir: Path) -> dict:
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
                "name": "core",
                "input": "core",
                "url": PREIMAGE_URL,
                "checkout": "core",
                "bootstrapStrategy": "none",
                "ownsMachineLocalConfiguration": False,
            }
        ],
        "promotableInputs": {
            "rule": "direct-core-subflake",
            "names": ["core"],
        },
        "qualification": {
            "entryPoint": "bin/qualify-promotion",
            "mode": "exact-candidate",
        },
        "checks": ["composition"],
        "hosts": ["StageAHost"],
        "packageSetIdentity": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-nixpkgs",
    }


def write_core_flake_nix(path: Path, url: str = PREIMAGE_URL) -> None:
    path.write_text(
        f'{{\n  description = "Composition flake";\n  inputs.core.url = "{url}";\n  outputs = {{ ... }}: {{}};\n}}\n'
    )


def write_core_flake_lock(path: Path, revision: str, lock_url: str = PREIMAGE_LOCK_URL) -> None:
    path.write_text(
        json.dumps({
            "nodes": {
                "root": {"inputs": {"core": "core-node"}},
                "core-node": {
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


def initialize_core_composition(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "bin").mkdir(parents=True, exist_ok=True)
    qualifier = path / "bin/qualify-promotion"
    qualifier.write_text("#!/bin/sh\nexit 97\n")
    qualifier.chmod(0o755)
    write_core_flake_nix(path / "flake.nix", PREIMAGE_URL)
    write_core_flake_lock(path / "flake.lock", OLD_REVISION, PREIMAGE_LOCK_URL)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Core Transition Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "core-transition@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial core composition commit"], cwd=path, check=True, capture_output=True)


def initialize_core_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    (path / "flake.nix").write_text("{}\n")
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Core Repo Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "core-repo@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "core commit 1"], cwd=path, check=True, capture_output=True)
    (path / "version.txt").write_text("v2.0.0\n")
    subprocess.run(["git", "add", "version.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "core commit 2"], cwd=path, check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


class CoreTransitionRunner:
    def __init__(self, contract_document: dict, target_revision: str, store_dir: str):
        self.contract = contract_document
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
                "input": "core",
                "registry": {"name": "core"},
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
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.contract), "")
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
            write_core_flake_lock(cwd / "flake.lock", self.target_revision, CANDIDATE_LOCK_URL)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["nix", "build"]:
            return subprocess.CompletedProcess(argv, 0, "/nix/store/stage-a-system\n", "")
        raise AssertionError(f"unexpected command: {argv}")


def find_store_dir() -> str:
    store = Path("/nix/store")
    if store.is_dir():
        for p in store.iterdir():
            if p.is_dir():
                return str(p)
    return "/nix/store/00000000000000000000000000000000-dummy-store-dir"


def core_transition_fixture(promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch):
    (
        deployment_workflow,
        promotion_plan,
        promotion_workflow,
        source_catalog,
        campaign_result,
        paths,
    ) = promotion_modules

    selected = tmp_path / "composition"
    initialize_core_composition(selected)

    core_path = tmp_path / "core"
    core_rev = initialize_core_repo(core_path)

    contract_doc = core_contract(selected)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)

    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotion_workflow, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotion_workflow, "_guard_processes", lambda *_args: None)

    store_dir = find_store_dir()
    runner = CoreTransitionRunner(contract_doc, core_rev, store_dir)

    comp_entry = source_catalog.SourceCatalogEntry(
        key="composition",
        checkout=selected,
        repository=selected.as_uri(),
        release_ref="refs/heads/main",
        publication=None,
    )
    core_entry = source_catalog.SourceCatalogEntry(
        key="core",
        checkout=core_path,
        repository=CANDIDATE_URL,
        release_ref="refs/heads/main",
        publication=None,
    )

    workflow = deployment_workflow.Workflow(
        repo_root, runner, sources=(comp_entry, core_entry)
    )

    return (
        workflow,
        selected,
        core_path,
        core_rev,
        contract_doc,
        state,
        runner,
        promotion_workflow,
        promotion_plan,
        paths,
    )


def test_core_repository_transition_prepare_and_apply_success(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    (
        workflow,
        selected,
        core_path,
        core_rev,
        contract_doc,
        state,
        runner,
        promotion_workflow,
        promotion_plan,
        paths,
    ) = core_transition_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)

    output = tmp_path / "output-plan"
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "core",
        core_rev,
        output,
        300,
        allow_local_fixture=True,
        allow_core_repository_transition=True,
    )

    # Validate document schema and structure
    doc = artifact.document
    assert doc["schema"] == promotion_plan.PLAN_SCHEMA_CORE_REPOSITORY_TRANSITION_V1
    assert doc["status"] == "READY_FOR_REVIEW", f"Status is {doc.get('status')}, blockers: {doc.get('blockers')}"
    assert doc["input"] == "core"
    assert doc["canonical_repository_url"] == CANDIDATE_LOCK_URL
    assert doc["closure_authorization"]["schema"] == "nix-update-core-repository-transition-v1"
    assert doc["closure_authorization"]["repositoryUrl"] == CANDIDATE_LOCK_URL
    assert doc["closure_authorization"]["previousRepositoryUrl"] == PREIMAGE_LOCK_URL
    assert doc["commit"]["changed_paths"] == ["flake.nix", "flake.lock"]

    # Validate retained artifacts
    retained_dir = artifact.directory / "retained-lock"
    assert (retained_dir / "flake.nix.candidate").exists()
    assert (retained_dir / "flake.nix.preimage").exists()
    cand_lock = json.loads((retained_dir / "flake.lock.candidate").read_text())
    assert set(cand_lock.keys()) == {"nodes", "root", "version"}
    assert "byte_sha256" not in cand_lock
    assert (retained_dir / "flake.lock.preimage").exists()
    delta = json.loads((retained_dir / "delta.json").read_text())
    assert delta["authorization"]["kind"] == "core-repository-transition"
    assert delta["core_repository_transition"] is True
    assert "candidate_flake_nix_sha256" in delta

    # Candidate flake.nix should contain the new repository URL
    candidate_nix_text = (retained_dir / "flake.nix.candidate").read_text()
    assert CANDIDATE_URL in candidate_nix_text
    assert PREIMAGE_URL not in candidate_nix_text

    # Apply promotion
    confirm_fn = lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}"
    result_code = promotion_workflow.apply_promotion(
        workflow, artifact.directory, confirm=confirm_fn
    )
    assert result_code == 0

    # Verify post-apply state in selected composition
    installed_nix = (selected / "flake.nix").read_text()
    installed_lock = json.loads((selected / "flake.lock").read_text())
    assert CANDIDATE_URL in installed_nix
    assert set(installed_lock.keys()) == {"nodes", "root", "version"}
    assert "byte_sha256" not in installed_lock
    assert installed_lock["nodes"]["core-node"]["locked"]["url"] == CANDIDATE_LOCK_URL
    assert installed_lock["nodes"]["core-node"]["locked"]["rev"] == core_rev

    # Verify Git commit changed both paths
    commit_changed = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
        cwd=selected,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert set(commit_changed) == {"flake.nix", "flake.lock"}

    # Verify replay reconciles consumed commit cleanly
    replay_code = promotion_workflow.apply_promotion(
        workflow, artifact.directory, confirm=confirm_fn
    )
    assert replay_code == 0


def test_core_repository_transition_negative_constraints(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    (
        workflow,
        selected,
        core_path,
        core_rev,
        contract_doc,
        state,
        runner,
        promotion_workflow,
        promotion_plan,
        paths,
    ) = core_transition_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)

    # 1. Non-core input with allow_core_repository_transition -> rejected
    with pytest.raises(paths.ConfigError, match="valid only for input core"):
        promotion_workflow.prepare_promotion(
            workflow,
            selected,
            "non-core",
            core_rev,
            tmp_path / "out-1",
            300,
            allow_local_fixture=True,
            allow_core_repository_transition=True,
        )

    # 2. Transition without allow_core_repository_transition flag -> blocked
    art2 = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "core",
        core_rev,
        tmp_path / "out-2",
        300,
        allow_local_fixture=True,
        allow_core_repository_transition=False,
    )
    assert art2.document["status"] == "BLOCKED"
    assert any("does not match expected" in b or "core source declaration" in b for b in art2.document.get("blockers", []))

    # 3. Preimage URL mismatch (e.g. already migrated composition or arbitrary URL) -> blocked
    write_core_flake_nix(selected / "flake.nix", "git+ssh://git@github.com/Knowledge-Forge-AI/other.git")
    write_core_flake_lock(selected / "flake.lock", OLD_REVISION, "ssh://git@github.com/Knowledge-Forge-AI/other.git")
    subprocess.run(["git", "add", "-A"], cwd=selected, check=True)
    subprocess.run(["git", "commit", "-m", "mismatched URL commit"], cwd=selected, check=True, capture_output=True)
    art3 = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "core",
        core_rev,
        tmp_path / "out-3",
        300,
        allow_local_fixture=True,
        allow_core_repository_transition=True,
    )
    assert art3.document["status"] == "BLOCKED"
    assert any("preimage" in b or "historical" in b for b in art3.document.get("blockers", []))


def test_transition_requires_catalog_before_any_commands(
    promotion_modules, repo_root: Path, tmp_path: Path,
):
    deployment, _, promotion, _, _, paths = promotion_modules
    calls = []
    workflow = deployment.Workflow(repo_root, lambda *args, **kwargs: calls.append(args))
    with pytest.raises(paths.ConfigError, match="requires the canonical source catalog"):
        promotion.prepare_promotion(
            workflow, tmp_path, "core", REVISION, tmp_path / "plan",
            allow_core_repository_transition=True,
        )
    assert calls == []


@pytest.mark.parametrize("mismatch", ["old-catalog", "non-main-catalog"])
def test_transition_rejects_catalog_authority_mismatch(
    promotion_modules, repo_root, tmp_path, monkeypatch, mismatch,
):
    workflow, selected, _, revision, _, _, runner, promotion, _, _ = core_transition_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    workflow.sources = tuple(
        replace(entry, **({"repository": PREIMAGE_URL} if mismatch == "old-catalog"
                          else {"release_ref": "refs/heads/other"}))
        if entry.key == "core" else entry for entry in workflow.sources
    )
    artifact = promotion.prepare_promotion(
        workflow, selected, "core", revision, tmp_path / "mismatch-plan", 300,
        allow_local_fixture=True, allow_core_repository_transition=True,
    )
    assert artifact.document["status"] == "BLOCKED"
    assert not any(argv[:3] == ["nix", "flake", "lock"] for argv, _ in runner.calls)


@pytest.mark.parametrize("mutation", [
    "old-candidate", "wrong-new", "wrong-owner", "candidate-dir",
    "revision", "unrelated-node", "child-topology",
])
def test_native_transition_rejects_invalid_candidate(
    promotion_modules, repo_root, tmp_path, monkeypatch, mutation,
):
    workflow, selected, _, revision, _, _, runner, promotion, _, _ = core_transition_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )

    deployment = promotion_modules[0]
    prepare_candidate = deployment._prepare_lock_candidate

    def mutate_candidate(*args, **kwargs):
        candidate = prepare_candidate(*args, **kwargs)
        lock = copy.deepcopy(candidate.candidate)
        node = lock["nodes"]["core-node"]
        urls = {"old-candidate": PREIMAGE_LOCK_URL,
                "wrong-new": "ssh://git@github.com/Knowledge-Forge-AI/other.git",
                "wrong-owner": "ssh://git@github.com/other/darwin-is-not-a-snowflake.git"}
        if mutation in urls:
            for field in ("original", "locked"):
                node[field]["url"] = urls[mutation]
        elif mutation == "candidate-dir":
            for field in ("original", "locked"):
                node[field]["dir"] = "nix-darwin"
        elif mutation == "revision":
            node["locked"]["rev"] = OLD_REVISION
        elif mutation == "unrelated-node":
            lock["nodes"]["unrelated"] = copy.deepcopy(node)
        else:
            node["inputs"] = {"child": ["core"]}
        return replace(candidate, candidate=lock)

    # Inject at the completed candidate boundary, after restoration of the
    # temporary local-source locator. Exercise the real native authorization.
    monkeypatch.setattr(deployment, "_prepare_lock_candidate", mutate_candidate)
    artifact = promotion.prepare_promotion(
        workflow, selected, "core", revision, tmp_path / "invalid-candidate", 300,
        allow_local_fixture=True, allow_core_repository_transition=True,
    )
    assert artifact.document["status"] == "BLOCKED"


def test_core_repository_transition_plan_tampering_fails_closed(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    (
        workflow,
        selected,
        core_path,
        core_rev,
        contract_doc,
        state,
        runner,
        promotion_workflow,
        promotion_plan,
        paths,
    ) = core_transition_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)

    output = tmp_path / "tamper-plan"
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "core",
        core_rev,
        output,
        300,
        allow_local_fixture=True,
        allow_core_repository_transition=True,
    )

    # Tampering changed_paths to flake.lock only
    binding = importlib.import_module("nix_darwin_local.promotion_binding")
    before = artifact.document["entry_lock"]["normalized"]
    after = artifact.document["candidate_lock"]["normalized"]
    options = dict(input_name="core", allow_file=False,
                   expected_ref="refs/heads/main", allow_implicit_main=True,
                   allow_repository_transition=True)
    for override in (
        {"allow_repository_transition": False},
        {"allow_layout_transition": True},
        {"expected_ref": "refs/heads/other"},
        {"input_name": "domain-adapter"},
    ):
        with pytest.raises(paths.ConfigError):
            binding.authorize_transition(
                before, after, "core-node", PREIMAGE_LOCK_URL, core_rev,
                **(options | override),
            )
    with_dir = copy.deepcopy(before)
    for field in ("original", "locked"):
        with_dir["nodes"]["core-node"][field]["dir"] = "nix-darwin"
    with pytest.raises(paths.ConfigError):
        binding.authorize_transition(
            with_dir, after, "core-node", PREIMAGE_LOCK_URL, core_rev, **options,
        )
    for field in ("previousRepositoryUrl", "repositoryUrl"):
        tampered = copy.deepcopy(artifact.document["closure_authorization"])
        tampered[field] = "ssh://git@github.com/other/other.git"
        with pytest.raises(paths.ConfigError):
            binding.validate_authorization(tampered)
    for schema in (promotion_plan.PLAN_SCHEMA_V2, promotion_plan.PLAN_SCHEMA_V3):
        replay = copy.deepcopy(artifact.document)
        replay["schema"] = schema
        with pytest.raises(paths.ConfigError):
            promotion_plan.validate_plan(replay)

    tampered_paths = copy.deepcopy(artifact.document)
    tampered_paths["commit"]["changed_paths"] = ["flake.lock"]
    with pytest.raises(paths.ConfigError, match="flake.nix and flake.lock commit"):
        promotion_plan.validate_plan(tampered_paths)

    # Tampering entry_declaration digest in memory
    tampered_decl = copy.deepcopy(artifact.document)
    tampered_decl["entry_declaration"]["sha256"] = "invalid-digest"
    with pytest.raises(paths.ConfigError, match="entry declaration digest"):
        promotion_plan.validate_plan(tampered_decl)

    # Even self-consistent digest rebinding cannot authorize an unrelated declaration edit.
    decl_file = artifact.directory / "retained-lock/flake.nix.candidate"
    original_declaration = decl_file.read_bytes()
    decl_file.write_bytes(original_declaration + b"\n# tampered\n")
    rebound = copy.deepcopy(artifact.document)
    rebound["candidate_declaration"]["sha256"] = hashlib.sha256(decl_file.read_bytes()).hexdigest()
    with pytest.raises(paths.ConfigError, match="exact core repository transition"):
        promotion_plan._validate_retained_lock_artifacts(artifact.directory, rebound)

    # Tampering declaration file on disk also fails load_plan's digest binding.
    with pytest.raises(paths.ConfigError, match="bound artifact identity"):
        promotion_plan.load_plan(artifact.directory)

    # Missing candidate_declaration
    missing_decl = copy.deepcopy(artifact.document)
    del missing_decl["candidate_declaration"]
    with pytest.raises(paths.ConfigError, match="fields are invalid"):
        promotion_plan.validate_plan(missing_decl)


def test_core_repository_transition_install_rollback_on_failure(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    (
        workflow,
        selected,
        core_path,
        core_rev,
        contract_doc,
        state,
        runner,
        promotion_workflow,
        promotion_plan,
        paths,
    ) = core_transition_fixture(promotion_modules, repo_root, tmp_path, monkeypatch)

    output = tmp_path / "rollback-plan"
    artifact = promotion_workflow.prepare_promotion(
        workflow,
        selected,
        "core",
        core_rev,
        output,
        300,
        allow_local_fixture=True,
        allow_core_repository_transition=True,
    )

    # Read original preimages
    orig_nix = (selected / "flake.nix").read_bytes()
    orig_lock = (selected / "flake.lock").read_bytes()

    # Load candidate
    deployment_workflow = promotion_modules[0]
    candidate = deployment_workflow.load_retained_lock_candidate(
        artifact.directory / "retained-lock",
        artifact.document["source_authority"],
        artifact.document["entry_lock"]["sha256"],
        artifact.document["candidate_lock"]["sha256"],
    )

    # Inject failure during lock installation after flake.nix is replaced
    orig_replace = os.replace
    call_count = [0]

    def failing_replace(src, dst):
        call_count[0] += 1
        if call_count[0] == 2:  # fail on second replace (flake.lock)
            raise OSError("simulated disk failure on flake.lock install")
        return orig_replace(src, dst)

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError, match="simulated disk failure"):
        deployment_workflow.install_retained_lock(selected, candidate)

    # Verify both flake.nix and flake.lock were restored to original bytes
    assert (selected / "flake.nix").read_bytes() == orig_nix
    assert (selected / "flake.lock").read_bytes() == orig_lock

    # Verify journal recorded preimage restoration
    journal_path = candidate.output / "install-transaction.json"
    assert journal_path.exists()
    journal = json.loads(journal_path.read_text())
    assert journal["state"] == "preimage-restored"
    assert "declaration" in journal
    assert promotion_workflow._install_failure_restored(candidate, selected) is True
