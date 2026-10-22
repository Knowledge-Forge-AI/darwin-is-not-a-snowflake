import builtins
import copy
import importlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


REVISION = "a" * 40
OLD_REVISION = "b" * 40


@pytest.fixture
def promotion_modules(repo_root: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    deployment_workflow = importlib.import_module("nix_darwin_local.deployment_workflow")
    promotion_plan = importlib.import_module("nix_darwin_local.promotion_plan")
    promotion_workflow = importlib.import_module("nix_darwin_local.promotion_workflow")
    paths = importlib.import_module("nix_darwin_local.paths")
    return deployment_workflow, promotion_plan, promotion_workflow, paths


def test_promotion_tool_identity_binds_complete_source_closure(
    promotion_modules, repo_root: Path, tmp_path: Path
):
    _, _, _, paths = promotion_modules
    qualification = importlib.import_module("nix_darwin_local.promotion_qualification")
    copied = tmp_path / "core"
    (copied / "bin").mkdir(parents=True)
    shutil.copy2(repo_root / "bin/nix-update", copied / "bin/nix-update")
    shutil.copytree(
        repo_root / "libexec/nix_darwin_local",
        copied / "libexec/nix_darwin_local",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    baseline = qualification.promotion_tool_identity(copied)
    addition = copied / "libexec/nix_darwin_local/native_adapter.txt"
    addition.write_text("one\n")
    with_addition = qualification.promotion_tool_identity(copied)
    assert with_addition["digest"] != baseline["digest"]
    assert any(
        item["path"] == "libexec/nix_darwin_local/native_adapter.txt"
        for item in with_addition["files"]
    )

    addition.write_text("two\n")
    assert qualification.promotion_tool_identity(copied)["digest"] != with_addition["digest"]
    addition.unlink()
    assert qualification.promotion_tool_identity(copied) == baseline

    symlink = copied / "libexec/nix_darwin_local/linked-source.py"
    symlink.symlink_to("paths.py")
    with pytest.raises(paths.ConfigError, match="owned single-link regular file"):
        qualification.promotion_tool_identity(copied)


def contract(remote: Path) -> dict:
    return {
        "schema": "fixture-base-promotion-v1",
        "version": 1,
        "composition": {
            "name": "stage-a-composition",
            "repositoryUrl": remote.as_uri(),
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


def qualifier_result(contract_document: dict) -> dict:
    repository_url = contract_document["modules"][0]["url"]
    return {
        "schema": "fixture-base-promotion-result-v1",
        "input": "domain-adapter",
        "registry": {"name": "domain-adapter"},
        "candidate": {
            "url": f"{repository_url}?rev={REVISION}",
            "requestedRev": REVISION,
            "resolvedRev": REVISION,
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
        "narHash": f"sha256-{revision[:8]}=",
    }
    if release_ref is not None:
        short_ref = release_ref.removeprefix("refs/heads/")
        original["ref"] = short_ref
        locked["ref"] = short_ref
    path.write_text(json.dumps({
        "nodes": {
            "root": {"inputs": {"domain-adapter": "domain-adapter-node"}},
            "domain-adapter-node": {
                "locked": locked,
                "original": original,
            },
        },
        "root": "root",
        "version": 7,
    }))


def initialize_repository(path: Path, tmp_path: Path) -> Path:
    remote = tmp_path / "composition-origin.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    path.mkdir()
    (path / "bin").mkdir()
    qualifier = path / "bin/qualify-promotion"
    qualifier.write_text("#!/bin/sh\nexit 97\n")
    qualifier.chmod(0o755)
    (path / "flake.nix").write_text("{}\n")
    write_lock(path / "flake.lock", OLD_REVISION)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Nix Update Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "nix-update@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", remote.as_uri()], cwd=path, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=path, check=True, capture_output=True)
    return remote


class PromotionRunner:
    def __init__(
        self,
        contract_document: dict,
        *,
        fail_push: bool = False,
        indeterminate: bool = False,
        qualifier_outcome: str = "pass",
        qualifier_payload: str | None = None,
        metadata_revision: str = REVISION,
        host_derivations: list[str] | None = None,
    ):
        self.contract = contract_document
        self.calls: list[tuple[list[str], Path]] = []
        self.fail_push = fail_push
        self.indeterminate = indeterminate
        self.qualifier_outcome = qualifier_outcome
        self.qualifier_payload = qualifier_payload
        self.metadata_revision = metadata_revision
        self.host_derivations = host_derivations or [
            "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-stage-a-system.drv"
        ]
        self.host_observation_count = 0
        self.push_seen = False

    def __call__(self, argv, cwd=None, **kwargs):
        argv = list(argv)
        cwd = Path(cwd)
        self.calls.append((argv, cwd))
        if argv[0] == "git":
            if argv[:2] == ["git", "ls-remote"] and argv[2] == "https://example.invalid/domain-adapter":
                return subprocess.CompletedProcess(
                    argv, 0, f"{self.metadata_revision}\t{argv[3]}\n", ""
                )
            if argv[1] == "push" and "HEAD:" in " ".join(argv):
                self.push_seen = True
                if self.fail_push:
                    return subprocess.CompletedProcess(argv, 1, "", "injected push rejection")
            if argv[1:3] == ["ls-remote", "--exit-code"] and self.push_seen and self.indeterminate:
                return subprocess.CompletedProcess(argv, 2, "", "injected remote uncertainty")
            return subprocess.run(argv, cwd=cwd, **kwargs)
        if argv[0].endswith("qualify-promotion"):
            if self.qualifier_payload is not None:
                return subprocess.CompletedProcess(argv, 0, self.qualifier_payload, "")
            result = qualifier_result(self.contract)
            result["outcome"] = self.qualifier_outcome
            return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")
        if argv[:3] == ["nix", "eval", "--json"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.contract), "")
        if argv[:3] == ["nix", "eval", "--raw"]:
            index = min(
                self.host_observation_count, len(self.host_derivations) - 1
            )
            derivation = self.host_derivations[index]
            self.host_observation_count += 1
            return subprocess.CompletedProcess(
                argv,
                0,
                derivation,
                "",
            )
        if argv[:3] == ["nix", "flake", "update"]:
            release_ref = None
            if self.contract["version"] == 2:
                release_ref = self.contract["releaseChannels"]["refs"]["domain-adapter"]
            write_lock(cwd / "flake.lock", REVISION, release_ref)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["nix", "flake", "metadata"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"revision": self.metadata_revision}), "")
        if argv[:2] == ["nix", "build"]:
            return subprocess.CompletedProcess(argv, 0, "/nix/store/stage-a-system\n", "")
        raise AssertionError(f"unexpected command: {argv}")


class QualificationFaultRunner(PromotionRunner):
    def __init__(self, contract_document: dict, mode: str):
        super().__init__(contract_document)
        self.mode = mode

    def __call__(self, argv, cwd=None, **kwargs):
        command = list(argv)
        if self.mode == "eval-fail" and command[:3] == ["nix", "eval", "--json"]:
            return subprocess.CompletedProcess(command, 1, "", "injected evaluation failure")
        if self.mode == "qualifier-fail" and command[0].endswith("qualify-promotion"):
            return subprocess.CompletedProcess(command, 1, "", "injected qualifier failure")
        if command[:2] == ["nix", "build"] and any("#checks." in item for item in command):
            if self.mode == "check-fail":
                return subprocess.CompletedProcess(command, 1, "", "injected check failure")
        if command[:2] == ["nix", "build"] and any(
            "#darwinConfigurations." in item for item in command
        ):
            if self.mode == "host-fail":
                return subprocess.CompletedProcess(command, 1, "", "injected host failure")
            if self.mode == "host-output-invalid":
                return subprocess.CompletedProcess(command, 0, "not-a-store-path\n", "")
        if command[:3] == ["nix", "eval", "--raw"]:
            if self.mode == "derivation-fail":
                return subprocess.CompletedProcess(command, 1, "", "injected derivation failure")
            if self.mode == "derivation-output-invalid":
                return subprocess.CompletedProcess(command, 0, "/nix/store/not-a-derivation\n", "")
        return super().__call__(command, cwd=cwd, **kwargs)


def promotion_fixture(promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch):
    deployment_workflow, _, promotion_workflow, _ = promotion_modules
    selected = tmp_path / "stage-a-composition"
    remote = initialize_repository(selected, tmp_path)
    contract_document = contract(remote)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotion_workflow, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotion_workflow, "_guard_processes", lambda *_args: None)
    runner = PromotionRunner(contract_document)
    workflow = deployment_workflow.Workflow(repo_root, runner)
    output = tmp_path / "promotion-plan"
    artifact = promotion_workflow.prepare_promotion(
        workflow, selected, "domain-adapter", REVISION, output, 300,
        allow_local_fixture=True,
    )
    return selected, remote, contract_document, state, artifact, runner


def test_qualification_bounds_and_command_failures_are_fail_closed(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    deployment_workflow, _, _, paths = promotion_modules
    qualification = importlib.import_module("nix_darwin_local.promotion_qualification")
    selected = tmp_path / "stage-a-composition"
    remote = initialize_repository(selected, tmp_path)
    contract_document = contract(remote)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state)
    candidate_ref = f"{contract_document['modules'][0]['url']}?rev={REVISION}"

    workflow = deployment_workflow.Workflow(
        repo_root, QualificationFaultRunner(contract_document, "eval-fail")
    )
    with pytest.raises(paths.ConfigError, match="unable to evaluate"):
        qualification.evaluate_contract(workflow, selected, allow_file_urls=True)

    with monkeypatch.context() as bounded:
        bounded.setattr(qualification, "CONTRACT_LIMIT", 1)
        workflow = deployment_workflow.Workflow(repo_root, PromotionRunner(contract_document))
        with pytest.raises(paths.ConfigError, match="bounded JSON limit"):
            qualification.evaluate_contract(workflow, selected, allow_file_urls=True)

    workflow = deployment_workflow.Workflow(repo_root, PromotionRunner(contract_document))
    with pytest.raises(paths.ConfigError, match="between 1 and 3600"):
        qualification.run_qualifier(
            workflow,
            selected,
            "domain-adapter",
            candidate_ref,
            contract_document,
            0,
            label="fixture qualifier",
        )

    workflow = deployment_workflow.Workflow(
        repo_root, QualificationFaultRunner(contract_document, "qualifier-fail")
    )
    with pytest.raises(paths.ConfigError, match="fixture qualifier failed") as failure:
        qualification.run_qualifier(
            workflow,
            selected,
            "domain-adapter",
            candidate_ref,
            contract_document,
            None,
            label="fixture qualifier",
        )
    assert "bin/qualify-promotion (exit 1)" in str(failure.value)
    assert "injected qualifier failure" in str(failure.value)
    assert "private evidence:" in str(failure.value)
    assert Path(workflow.records[-1].stderr["path"]).is_file()

    with monkeypatch.context() as bounded:
        bounded.setattr(qualification, "MAX_NATIVE_RESULT_BYTES", 1)
        workflow = deployment_workflow.Workflow(repo_root, PromotionRunner(contract_document))
        with pytest.raises(paths.ConfigError, match="10 MiB"):
            qualification.run_qualifier(
                workflow,
                selected,
                "domain-adapter",
                candidate_ref,
                contract_document,
                None,
                label="fixture qualifier",
            )

    for mode, message in (
        ("check-fail", "declared promotion check failed"),
        ("host-fail", "declared promotion host failed"),
        ("host-output-invalid", "invalid output identity"),
        ("derivation-fail", "unable to observe"),
        ("derivation-output-invalid", "derivation is invalid"),
    ):
        output = tmp_path / f"qualification-{mode}"
        output.mkdir(mode=0o700)
        workflow = deployment_workflow.Workflow(
            repo_root, QualificationFaultRunner(contract_document, mode)
        )
        with pytest.raises(paths.ConfigError, match=message):
            qualification.qualify_locked_tree(
                workflow,
                selected,
                "domain-adapter",
                candidate_ref,
                contract_document,
                None,
                output,
            )


def test_contract_is_generic_direct_and_exact(promotion_modules, tmp_path: Path):
    _, promotion_plan, _, paths = promotion_modules
    document = contract(tmp_path / "domain-adapter.git")

    assert promotion_plan.validate_contract(document, allow_file_urls=True) is document
    assert promotion_plan.contract_repository_url(document, "domain-adapter").endswith("domain-adapter")
    assert promotion_plan.candidate_reference(document["modules"][0]["url"], REVISION).endswith(
        f"?rev={REVISION}"
    )
    with pytest.raises(paths.ConfigError, match="unknown, indirect"):
        promotion_plan.contract_repository_url(document, "indirect-input")
    with pytest.raises(paths.ConfigError, match="40 lowercase"):
        promotion_plan.candidate_reference(document["modules"][0]["url"], REVISION.upper())


def test_process_gate_classifies_promotion_apply_as_mutation(repo_root: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    deployment_state = importlib.import_module("nix_darwin_local.deployment_state")

    matches = deployment_state.competing_processes([
        {"pid": 991, "argv": ["/managed/bin/nix-update", "promote", "apply", "/private/plan"]}
    ])
    assert matches == [{
        "pid": 991,
        "argv": ["/managed/bin/nix-update", "promote", "apply", "/private/plan"],
        "reason": "nix-update promote apply",
    }]


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda value: value.update(schema="wrong schema"), "schema is invalid"),
        (lambda value: value.update(version=2), "releaseChannels presence disagree"),
        (lambda value: value["modules"].append(dict(value["modules"][0])), "duplicate module"),
        (lambda value: value["promotableInputs"].update(names=["missing"]), "unmatched promotable"),
        (lambda value: value["qualification"].update(entryPoint="../outside"), "repository-relative"),
        (lambda value: value["qualification"].update(mode="command-string"), "mode is unsupported"),
        (lambda value: value["modules"][0].update(url="https://example.invalid/repo?dir=other"), "query"),
    ],
)
def test_contract_faults_fail_closed(promotion_modules, tmp_path: Path, mutate, message):
    _, promotion_plan, _, paths = promotion_modules
    document = contract(tmp_path / "domain-adapter.git")
    mutate(document)
    with pytest.raises(paths.ConfigError, match=message):
        promotion_plan.validate_contract(document, allow_file_urls=True)


@pytest.mark.parametrize(
    "transform,message",
    [
        (lambda _value: [], "JSON object"),
        (lambda value: {**value, "modules": []}, "non-empty ordered list"),
        (lambda value: {**value, "modules": [None]}, "record 0 must be an object"),
        (
            lambda value: {
                **value,
                "modules": [
                    value["modules"][0],
                    {
                        **value["modules"][0],
                        "name": "second-module",
                        "url": "https://example.invalid/second",
                    },
                ],
            },
            "duplicate module identity",
        ),
        (lambda value: {**value, "promotableInputs": []}, "must be an object"),
        (lambda value: {**value, "qualification": []}, "must be an object"),
        (
            lambda value: {
                **value,
                "qualification": {**value["qualification"], "resultSchema": "invented"},
            },
            "fields are invalid",
        ),
        (lambda value: {**value, "checks": []}, "non-empty list"),
    ],
)
def test_contract_type_identity_and_set_boundaries_fail_closed(
    promotion_modules, tmp_path: Path, transform, message
):
    _, promotion_plan, _, paths = promotion_modules
    document = transform(contract(tmp_path / "domain-adapter.git"))
    with pytest.raises(paths.ConfigError, match=message):
        promotion_plan.validate_contract(document, allow_file_urls=True)


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda value: value.update(schema="wrong schema"), "schema identity"),
        (lambda value: value.update(outcome="fail"), "outcome or failure"),
        (lambda value: value["candidate"].update(requestedRev=OLD_REVISION), "candidate revision"),
        (lambda value: value["candidate"].update(resolvedRev=OLD_REVISION), "candidate revision"),
        (lambda value: value["candidate"].update(resolvedUrl="https://example.invalid/wrong"), "resolved URL"),
        (lambda value: value.update(contractCompatibility={"status": "fail"}), "compatibility"),
        (lambda value: value["checks"].update(composition="fail"), "non-pass check"),
        (lambda value: value["hosts"]["StageAHost"].update(status="fail"), "host status"),
        (lambda value: value["lock"].update(byteIdentical=False), "byte identity"),
        (lambda value: value.update(snapshots={"valid": False, "byteIdentical": True}), "snapshot"),
    ],
)
def test_qualifier_result_identity_pass_sets_and_no_activation_fail_closed(
    promotion_modules, tmp_path: Path, mutate, message
):
    _, _, promotion_workflow, paths = promotion_modules
    contract_document = contract(tmp_path / "domain-adapter.git")
    document = qualifier_result(contract_document)
    mutate(document)

    with pytest.raises(paths.ConfigError, match=message):
        promotion_workflow._validate_qualifier_result(
            document,
            "domain-adapter",
            f"{contract_document['modules'][0]['url']}?rev={REVISION}",
            contract_document,
            allow_file_urls=True,
        )


@pytest.mark.parametrize(
    "defect",
    ["symlink", "hardlink", "non-file", "non-executable"],
)
def test_qualifier_topology_is_fail_closed(promotion_modules, tmp_path: Path, defect: str):
    _, _, promotion_workflow, paths = promotion_modules
    selected = tmp_path / "stage-a-composition"
    (selected / "bin").mkdir(parents=True)
    qualifier = selected / "bin/qualify-promotion"
    qualifier.write_text("#!/bin/sh\nexit 0\n")
    qualifier.chmod(0o700)
    if defect == "symlink":
        qualifier.unlink()
        qualifier.symlink_to("../flake.nix")
        (selected / "flake.nix").write_text("{}\n")
    elif defect == "hardlink":
        os.link(qualifier, selected / "bin/qualifier-alias")
    elif defect == "non-file":
        qualifier.unlink()
        qualifier.mkdir()
    else:
        qualifier.chmod(0o600)

    with pytest.raises(paths.ConfigError, match="qualifier"):
        promotion_workflow._qualifier_identity(selected, contract(tmp_path / "domain-adapter.git"))


@pytest.mark.parametrize(
    "payload,message",
    [("{", "exactly one JSON"), ("x" * 65, "10 MiB machine-JSON limit")],
    ids=["malformed", "oversized"],
)
def test_prepare_blocks_malformed_or_oversized_qualifier_output(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch, payload: str, message: str
):
    deployment_workflow, _, promotion_workflow, _ = promotion_modules
    selected = tmp_path / "stage-a-composition"
    remote = initialize_repository(selected, tmp_path)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotion_workflow, "_private_state_root", lambda: state)
    if len(payload) == 65:
        promotion_qualification = importlib.import_module("nix_darwin_local.promotion_qualification")
        monkeypatch.setattr(promotion_qualification, "MAX_NATIVE_RESULT_BYTES", 64)
    artifact = promotion_workflow.prepare_promotion(
        deployment_workflow.Workflow(
            repo_root, PromotionRunner(contract(remote), qualifier_payload=payload)
        ),
        selected,
        "domain-adapter",
        REVISION,
        tmp_path / "blocked-output",
        allow_local_fixture=True,
    )
    assert artifact.document["status"] == "BLOCKED"
    assert message in artifact.document["blockers"][0]
    assert subprocess.run(
        ["git", "status", "--porcelain"], cwd=selected, check=True, capture_output=True, text=True
    ).stdout == ""


def test_prepare_is_nonmutating_digest_bound_and_locked_tree_qualified(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    selected, remote, _, _, artifact, runner = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=selected, check=True, capture_output=True, text=True).stdout.strip()

    assert artifact.document["status"] == "READY_FOR_REVIEW"
    assert artifact.document["schema"] == "nix-update-promotion-plan-v2"
    assert artifact.document["confirmation"] == "APPLY NIX-UPDATE PROMOTION {PLAN_DIGEST}"
    assert artifact.document["candidate_lock"]["normalized"]["nodes"]["domain-adapter-node"]["locked"]["rev"] == REVISION
    assert artifact.document["closure_authorization"]["selectedNode"] == "domain-adapter-node"
    assert artifact.document["closure_authorization"]["preimageOwnedNodes"] == ["domain-adapter-node"]
    assert artifact.document["closure_authorization"]["candidateOwnedNodes"] == ["domain-adapter-node"]
    assert artifact.document["lock_authorization_evidence"]["path"] == "retained-lock/delta.json"
    assert artifact.document["locked_qualification"]["checks"] == [{"name": "composition", "outcome": "pass"}]
    assert artifact.document["locked_qualification"]["hosts"][0]["derivation"].endswith(".drv")
    assert artifact.document["impact"]["classification"] == "HOST_DERIVATIONS_IDENTICAL"
    assert artifact.document["impact"]["activationPerformed"] is False
    assert artifact.document["remote_ref"] == {
        "ref": "refs/heads/main",
        "revision": REVISION,
    }
    assert artifact.document["impact_digest"] == promotion_modules[1].sha256_bytes(
        promotion_modules[1].canonical_bytes(artifact.document["impact"])
    )
    assert subprocess.run(["git", "status", "--porcelain"], cwd=selected, check=True, capture_output=True, text=True).stdout == ""
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=selected, check=True, capture_output=True, text=True).stdout.strip() == before
    assert subprocess.run(["git", "ls-remote", str(remote), "refs/heads/main"], check=True, capture_output=True, text=True).stdout.startswith(before)
    commands = [call for call, _cwd in runner.calls]
    assert [item for item in commands if item and item[0].endswith("qualify-promotion")][0][-2:] == ["--timeout-seconds", "300"]
    assert not any(item[0] in {"sudo", "darwin-rebuild", "brew", "nix-update"} for item in commands)


def test_authorization_evaluation_uses_only_canonical_snapshot_trees(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    deployment_workflow, _, promotion_workflow, _ = promotion_modules
    selected, _remote, contract_document, _state, artifact, prepare_runner = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )

    def authorization_cwds(runner):
        return [
            cwd
            for argv, cwd in runner.calls
            if argv[:2] in (["nix", "eval"], ["nix", "build"])
            or (argv and argv[0].endswith("qualify-promotion"))
        ]

    assert authorization_cwds(prepare_runner)
    assert {path.name for path in authorization_cwds(prepare_runner)} == {"snapshot"}
    assert selected not in authorization_cwds(prepare_runner)

    apply_runner = PromotionRunner(contract_document)
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )
    assert promotion_workflow.apply_promotion(
        deployment_workflow.Workflow(repo_root, apply_runner), artifact.directory
    ) == 0
    assert authorization_cwds(apply_runner)
    assert {path.name for path in authorization_cwds(apply_runner)} == {"snapshot"}
    assert selected not in authorization_cwds(apply_runner)


def test_canonical_snapshot_identity_is_logical_and_tamper_fails_before_materialization(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    deployment_workflow, _, promotion_workflow, paths = promotion_modules
    selected, _remote, _contract, state, artifact, _runner = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    lock_bytes = (artifact.directory / artifact.document["candidate_lock"]["path"]).read_bytes()
    identity = promotion_workflow._canonical_snapshot_identity(
        selected, artifact.document["repository"], lock_bytes
    )
    assert identity == {
        "repository_commit": artifact.document["repository"]["commit"],
        "repository_tree": artifact.document["repository"]["tree"],
        "selected_flake_path": ".",
        "lock_sha256": artifact.document["candidate_lock"]["sha256"],
        "lock_size": artifact.document["candidate_lock"]["size"],
        "materialization_policy": "git-archive-exact-tree-bound-lock-v1",
    }

    tampered = dict(artifact.document["repository"])
    tampered["tree"] = "0" * 40
    with pytest.raises(paths.ConfigError, match="tree identity drifted"):
        with promotion_workflow._canonical_promotion_snapshot(
            deployment_workflow.Workflow(repo_root, PromotionRunner(contract(Path("/tmp")))),
            selected,
            tampered,
            lock_bytes,
            state,
        ):
            raise AssertionError("tampered snapshot must not materialize")
    assert not list(state.glob("nix-update-promotion-*"))


def test_snapshot_and_literal_host_identity_validation_fails_closed(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    _deployment, _plan, promotion_workflow, paths = promotion_modules
    qualification = importlib.import_module("nix_darwin_local.promotion_qualification")
    selected, _remote, _contract, _state, artifact, _runner = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    lock_bytes = (selected / "flake.lock").read_bytes()

    with pytest.raises(paths.ConfigError, match="host identity"):
        qualification._literal_host_selector('fixture"host', "system")

    malformed = dict(artifact.document["repository"])
    malformed["flake_nix"] = "not-json"
    with pytest.raises(paths.ConfigError, match="flake path identity"):
        promotion_workflow._canonical_snapshot_identity(
            selected, malformed, lock_bytes
        )

    wrong_name = dict(artifact.document["repository"])
    flake_identity = json.loads(wrong_name["flake_nix"])
    flake_identity["path"] = str(selected / "not-flake.nix")
    wrong_name["flake_nix"] = json.dumps(flake_identity)
    with pytest.raises(paths.ConfigError, match="flake path identity"):
        promotion_workflow._canonical_snapshot_identity(
            selected, wrong_name, lock_bytes
        )


def test_prepare_blocks_unknown_input_and_unsafe_qualifier(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    deployment_workflow, _, promotion_workflow, _ = promotion_modules
    selected = tmp_path / "stage-a-composition"
    remote = initialize_repository(selected, tmp_path)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotion_workflow, "_private_state_root", lambda: state)
    runner = PromotionRunner(contract(remote))

    blocked = promotion_workflow.prepare_promotion(
        deployment_workflow.Workflow(repo_root, runner),
        selected,
        "indirect-input",
        REVISION,
        tmp_path / "blocked-plan",
        allow_local_fixture=True,
    )
    assert blocked.document["status"] == "BLOCKED"
    assert "unknown, indirect" in blocked.document["blockers"][0]

    subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=selected, check=True, capture_output=True)
    qualifier = selected / "bin/qualify-promotion"
    qualifier.unlink()
    qualifier.symlink_to("../flake.nix")
    subprocess.run(["git", "add", "-A"], cwd=selected, check=True)
    subprocess.run(["git", "commit", "-m", "unsafe qualifier fixture"], cwd=selected, check=True, capture_output=True)
    subprocess.run(["git", "push"], cwd=selected, check=True, capture_output=True)
    unsafe = promotion_workflow.prepare_promotion(
        deployment_workflow.Workflow(repo_root, runner),
        selected,
        "domain-adapter",
        REVISION,
        tmp_path / "unsafe-plan",
        allow_local_fixture=True,
    )
    assert unsafe.document["status"] == "BLOCKED"
    assert "snapshot" in unsafe.document["blockers"][0]


@pytest.mark.parametrize("catalog_adopted", [False, True])
def test_apply_requires_exact_confirmation_then_commits_and_publishes_only_lock(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch, catalog_adopted
):
    deployment_workflow, _, promotion_workflow, paths = promotion_modules
    selected, remote, contract_document, state, artifact, prepare_runner = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=selected, check=True, capture_output=True, text=True).stdout.strip()
    apply_runner = PromotionRunner(contract_document)
    monkeypatch.setattr(builtins, "input", lambda _prompt: "wrong")
    with pytest.raises(paths.ConfigError, match="confirmation"):
        promotion_workflow.apply_promotion(deployment_workflow.Workflow(repo_root, apply_runner), artifact.directory)
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=selected, check=True, capture_output=True, text=True).stdout.strip() == before

    workflow = deployment_workflow.Workflow(repo_root, apply_runner)
    if catalog_adopted:
        from nix_darwin_local.source_catalog import SourceCatalogEntry
        workflow.sources = (SourceCatalogEntry(
            "unrelated", selected, "https://example.invalid/unrelated.git", "refs/heads/main"
        ),)
    monkeypatch.setattr(
        builtins, "input", lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}"
    )
    assert promotion_workflow.apply_promotion(
        workflow, artifact.directory
    ) == 0
    published = subprocess.run(["git", "rev-parse", "HEAD"], cwd=selected, check=True, capture_output=True, text=True).stdout.strip()
    assert published != before
    assert subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
        cwd=selected, check=True, capture_output=True, text=True,
    ).stdout.splitlines() == ["flake.lock"]
    assert subprocess.run(["git", "status", "--porcelain"], cwd=selected, check=True, capture_output=True, text=True).stdout == ""
    assert subprocess.run(["git", "ls-remote", str(remote), "refs/heads/main"], check=True, capture_output=True, text=True).stdout.startswith(published)
    result = json.loads((artifact.directory / "result.json").read_text())
    assert result["terminal_state"] == "PROMOTION_PUBLISHED_AWAITING_ACTIVATION"
    assert result["published_commit"] == published
    assert result["impact"] == artifact.document["impact"]
    assert (state / "promotion-consumed" / f"{artifact.digest}.json").is_file()
    commands = [call for call, _cwd in apply_runner.calls]
    assert any(command[0].endswith("qualify-promotion") for command in commands)
    assert not any(item[0] in {"sudo", "darwin-rebuild", "brew", "nix-update"} for item in commands)


def test_version_two_apply_revalidates_only_the_plan_bound_release_ref(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    deployment_workflow, _, promotion_workflow, _ = promotion_modules
    selected = tmp_path / "stage-a-composition"
    remote = initialize_repository(selected, tmp_path)
    contract_document = contract(remote)
    contract_document["version"] = 2
    contract_document["releaseChannels"] = {
        "policy": "explicit-head-refs-v1",
        "refs": {"domain-adapter": "refs/heads/deploy"},
    }
    write_lock(selected / "flake.lock", OLD_REVISION, "refs/heads/deploy")
    subprocess.run(["git", "add", "flake.lock"], cwd=selected, check=True)
    subprocess.run(
        ["git", "commit", "-m", "bind deploy release channel"],
        cwd=selected,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "push"], cwd=selected, check=True, capture_output=True)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotion_workflow, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotion_workflow, "_guard_processes", lambda *_args: None)
    prepare_runner = PromotionRunner(contract_document)
    artifact = promotion_workflow.prepare_promotion(
        deployment_workflow.Workflow(repo_root, prepare_runner),
        selected,
        "domain-adapter",
        REVISION,
        tmp_path / "v2-plan",
        allow_local_fixture=True,
    )
    assert artifact.document["status"] == "READY_FOR_REVIEW"
    assert artifact.document["remote_ref"]["ref"] == "refs/heads/deploy"

    apply_runner = PromotionRunner(contract_document)
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )
    assert promotion_workflow.apply_promotion(
        deployment_workflow.Workflow(repo_root, apply_runner), artifact.directory
    ) == 0
    remote_checks = [
        command
        for command, _cwd in [*prepare_runner.calls, *apply_runner.calls]
        if command[:3]
        == ["git", "ls-remote", "https://example.invalid/domain-adapter"]
    ]
    assert len(remote_checks) >= 4
    assert all(command[3] == "refs/heads/deploy" for command in remote_checks)
    assert not any(command[3] == "refs/heads/main" for command in remote_checks)
    result = json.loads((artifact.directory / "result.json").read_text())
    assert result["impact"] == artifact.document["impact"]


def test_prepare_records_changed_host_impact_without_activation(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    deployment_workflow, _, promotion_workflow, _ = promotion_modules
    selected = tmp_path / "stage-a-composition"
    remote = initialize_repository(selected, tmp_path)
    contract_document = contract(remote)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotion_workflow, "_private_state_root", lambda: state)
    baseline = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-stage-a-system.drv"
    candidate = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-stage-a-system.drv"
    runner = PromotionRunner(
        contract_document,
        host_derivations=[baseline, candidate, baseline],
    )

    artifact = promotion_workflow.prepare_promotion(
        deployment_workflow.Workflow(repo_root, runner),
        selected,
        "domain-adapter",
        REVISION,
        tmp_path / "changed-impact-plan",
        allow_local_fixture=True,
    )

    assert artifact.document["status"] == "READY_FOR_REVIEW"
    assert artifact.document["impact"]["changedHosts"] == ["StageAHost"]
    assert artifact.document["impact"]["classification"] == "HOST_DERIVATIONS_CHANGED"
    assert artifact.document["impact"]["activationSignal"] == "HOST_CHANGE_REVIEW_REQUIRED"
    assert artifact.document["impact"]["activationPerformed"] is False
    assert not any(
        command[0] in {"sudo", "darwin-rebuild", "nix-update"}
        for command, _cwd in runner.calls
    )


def test_apply_blocks_baseline_host_drift_before_mutation(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    deployment_workflow, _, promotion_workflow, _ = promotion_modules
    selected, _remote, contract_document, state, artifact, _ = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    before_lock = (selected / "flake.lock").read_bytes()
    runner = PromotionRunner(
        contract_document,
        host_derivations=[
            "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-stage-a-system.drv"
        ],
    )
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )

    assert promotion_workflow.apply_promotion(
        deployment_workflow.Workflow(repo_root, runner), artifact.directory
    ) == 1
    result = json.loads((artifact.directory / "result.json").read_text())
    assert result["terminal_state"] == "BLOCKED_BEFORE_MUTATION"
    assert "baseline host derivations drifted" in result["failure"]
    assert (selected / "flake.lock").read_bytes() == before_lock
    consumed = state / "promotion-consumed"
    assert not consumed.exists() or not list(consumed.glob("*.json"))


def test_candidate_impact_drift_after_install_restores_exact_preimage(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    deployment_workflow, _, promotion_workflow, _ = promotion_modules
    selected, _remote, contract_document, _state, artifact, _ = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    before_lock = (selected / "flake.lock").read_bytes()
    expected = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-stage-a-system.drv"
    drifted = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-stage-a-system.drv"
    runner = PromotionRunner(
        contract_document,
        host_derivations=[expected, expected, expected, drifted],
    )
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )

    assert promotion_workflow.apply_promotion(
        deployment_workflow.Workflow(repo_root, runner), artifact.directory
    ) == 1
    result = json.loads((artifact.directory / "result.json").read_text())
    assert result["terminal_state"] == "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED"
    assert "host impact drifted before commit" in result["failure"]
    assert result["impact"] == artifact.document["impact"]
    assert (selected / "flake.lock").read_bytes() == before_lock
    assert not any(command[:2] == ["git", "push"] for command, _cwd in runner.calls)
    assert not list(_state.glob("nix-update-promotion-*"))


@pytest.mark.parametrize("drift", ["untracked", "index", "lock"])
def test_apply_blocks_repository_authority_drift_before_consuming_or_mutating(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch, drift: str
):
    deployment_workflow, _, promotion_workflow, _ = promotion_modules
    selected, _remote, contract_document, state, artifact, _ = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    before_lock = (selected / "flake.lock").read_bytes()
    if drift == "untracked":
        (selected / "untracked").write_text("drift\n")
    elif drift == "index":
        (selected / "flake.nix").write_text("{ drift = true; }\n")
        subprocess.run(["git", "add", "flake.nix"], cwd=selected, check=True)
    else:
        (selected / "flake.lock").write_bytes(before_lock + b" ")
    monkeypatch.setattr(builtins, "input", lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}")

    assert promotion_workflow.apply_promotion(
        deployment_workflow.Workflow(repo_root, PromotionRunner(contract_document)), artifact.directory
    ) == 1
    assert json.loads((artifact.directory / "result.json").read_text())["terminal_state"] == "BLOCKED_BEFORE_MUTATION"
    assert not (state / "promotion-consumed" / f"{artifact.digest}.json").exists()


def test_apply_blocks_contract_and_remote_candidate_drift_before_mutation(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    deployment_workflow, _, promotion_workflow, _ = promotion_modules
    selected, _remote, contract_document, state, artifact, _ = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    before_lock = (selected / "flake.lock").read_bytes()
    drifted_contract = copy.deepcopy(contract_document)
    drifted_contract["packageSetIdentity"] = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-nixpkgs"
    monkeypatch.setattr(builtins, "input", lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}")
    assert promotion_workflow.apply_promotion(
        deployment_workflow.Workflow(repo_root, PromotionRunner(drifted_contract)), artifact.directory
    ) == 1
    assert (selected / "flake.lock").read_bytes() == before_lock
    assert not (state / "promotion-consumed" / f"{artifact.digest}.json").exists()

    (artifact.directory / "result.json").unlink()
    assert promotion_workflow.apply_promotion(
        deployment_workflow.Workflow(
            repo_root, PromotionRunner(contract_document, metadata_revision=OLD_REVISION)
        ),
        artifact.directory,
    ) == 1
    assert (selected / "flake.lock").read_bytes() == before_lock
    assert not (state / "promotion-consumed" / f"{artifact.digest}.json").exists()


def test_consumed_plan_without_install_is_closed_as_restored_without_repository_mutation(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    deployment_workflow, _, promotion_workflow, _ = promotion_modules
    selected, _remote, contract_document, _state, artifact, _ = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    before_lock = (selected / "flake.lock").read_bytes()
    before_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=selected, check=True, capture_output=True, text=True
    ).stdout.strip()
    promotion_workflow._consume_authority(artifact.digest, artifact.document["repository"])
    monkeypatch.setattr(builtins, "input", lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}")

    assert promotion_workflow.apply_promotion(
        deployment_workflow.Workflow(repo_root, PromotionRunner(contract_document)), artifact.directory
    ) == 1
    assert json.loads((artifact.directory / "result.json").read_text())["terminal_state"] == "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED"
    assert (selected / "flake.lock").read_bytes() == before_lock
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=selected, check=True, capture_output=True, text=True
    ).stdout.strip() == before_head


def test_interrupted_installed_lock_is_restored_on_consumed_plan_reentry(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    deployment_workflow, _, promotion_workflow, _ = promotion_modules
    selected, _remote, contract_document, _state, artifact, _ = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    before_lock = (selected / "flake.lock").read_bytes()
    candidate = deployment_workflow.load_retained_lock_candidate(
        artifact.directory / "retained-lock",
        artifact.document["repository"],
        artifact.document["entry_lock"]["sha256"],
        artifact.document["candidate_lock"]["sha256"],
    )
    promotion_workflow._consume_authority(artifact.digest, artifact.document["repository"])
    deployment_workflow.install_retained_lock(selected, candidate)
    assert (selected / "flake.lock").read_bytes() == candidate.candidate_bytes
    monkeypatch.setattr(builtins, "input", lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}")

    assert promotion_workflow.apply_promotion(
        deployment_workflow.Workflow(repo_root, PromotionRunner(contract_document)), artifact.directory
    ) == 1
    assert (selected / "flake.lock").read_bytes() == before_lock
    assert subprocess.run(
        ["git", "status", "--porcelain"], cwd=selected, check=True, capture_output=True, text=True
    ).stdout == ""
    result = json.loads((artifact.directory / "result.json").read_text())
    assert result["terminal_state"] == "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED"
    assert json.loads((artifact.directory / "retained-lock/install-transaction.json").read_text())["state"] == "preimage-restored"


def test_push_rejection_preserves_commit_without_publication(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    deployment_workflow, _, promotion_workflow, _ = promotion_modules
    selected, remote, contract_document, _, artifact, _ = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    remote_before = subprocess.run(
        ["git", "ls-remote", str(remote), "refs/heads/main"], check=True, capture_output=True, text=True
    ).stdout.split()[0]
    runner = PromotionRunner(contract_document, fail_push=True)
    monkeypatch.setattr(builtins, "input", lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}")

    assert promotion_workflow.apply_promotion(
        deployment_workflow.Workflow(repo_root, runner), artifact.directory
    ) == 1
    local = subprocess.run(["git", "rev-parse", "HEAD"], cwd=selected, check=True, capture_output=True, text=True).stdout.strip()
    remote_after = subprocess.run(
        ["git", "ls-remote", str(remote), "refs/heads/main"], check=True, capture_output=True, text=True
    ).stdout.split()[0]
    assert local != remote_before
    assert remote_after == remote_before
    assert json.loads((artifact.directory / "result.json").read_text())["terminal_state"] == "COMMITTED_NOT_PUBLISHED"

    recovery_runner = PromotionRunner(contract_document)
    assert promotion_workflow.apply_promotion(
        deployment_workflow.Workflow(repo_root, recovery_runner), artifact.directory
    ) == 0
    assert json.loads((artifact.directory / "result.json").read_text())["terminal_state"] == "PROMOTION_PUBLISHED_AWAITING_ACTIVATION"
    assert any(command[:2] == ["git", "push"] for command, _cwd in recovery_runner.calls)
    assert subprocess.run(
        ["git", "ls-remote", str(remote), "refs/heads/main"], check=True, capture_output=True, text=True
    ).stdout.split()[0] == local


@pytest.mark.parametrize("cache", ["missing", "stale"])
def test_apply_uses_actual_remote_after_tracking_cache_changes(
    promotion_modules, repo_root, tmp_path, monkeypatch, cache
):
    deployment, _, promotion, _ = promotion_modules
    selected, remote, contract_document, _, artifact, _ = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    if cache == "missing":
        subprocess.run(["git", "update-ref", "-d", "refs/remotes/origin/main"], cwd=selected, check=True)
    else:
        tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=selected, check=True, capture_output=True, text=True).stdout.strip()
        unrelated = subprocess.run(["git", "commit-tree", tree, "-m", "stale tracking fixture"], cwd=selected, check=True, capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "update-ref", "refs/remotes/origin/main", unrelated], cwd=selected, check=True)
    workflow = deployment.Workflow(repo_root, PromotionRunner(contract_document))
    assert promotion.apply_promotion(
        workflow, artifact.directory,
        confirm=lambda prompt: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    ) == 0
    result = json.loads((artifact.directory / "result.json").read_text())
    assert result["terminal_state"] == "PROMOTION_PUBLISHED_AWAITING_ACTIVATION"
    assert subprocess.run(["git", "ls-remote", str(remote), "refs/heads/main"], check=True, capture_output=True, text=True).stdout.split()[0] == result["published_commit"]


def test_remote_advance_before_staging_restores_preimage_and_does_not_commit_or_push(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    deployment_workflow, _, promotion_workflow, _ = promotion_modules
    selected, _remote, contract_document, _state, artifact, _ = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=selected, check=True, capture_output=True, text=True
    ).stdout.strip()
    runner = PromotionRunner(contract_document)
    monkeypatch.setattr(promotion_workflow, "_remote_head", lambda *_args: "c" * 40)
    monkeypatch.setattr(builtins, "input", lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}")

    assert promotion_workflow.apply_promotion(
        deployment_workflow.Workflow(repo_root, runner), artifact.directory
    ) == 1
    result = json.loads((artifact.directory / "result.json").read_text())
    assert result["terminal_state"] == "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED"
    assert before == subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=selected, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert not any(command[:2] == ["git", "push"] for command, _cwd in runner.calls)


def test_apply_qualification_failure_restores_exact_preimage(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    deployment_workflow, _, promotion_workflow, _ = promotion_modules
    selected, remote, contract_document, _, artifact, _ = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    before_lock = (selected / "flake.lock").read_bytes()
    before_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=selected, check=True, capture_output=True, text=True
    ).stdout.strip()
    runner = PromotionRunner(contract_document, qualifier_outcome="fail")
    monkeypatch.setattr(builtins, "input", lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}")

    assert promotion_workflow.apply_promotion(
        deployment_workflow.Workflow(repo_root, runner), artifact.directory
    ) == 1
    assert (selected / "flake.lock").read_bytes() == before_lock
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=selected, check=True, capture_output=True, text=True
    ).stdout.strip() == before_commit
    assert subprocess.run(["git", "status", "--porcelain"], cwd=selected, check=True, capture_output=True, text=True).stdout == ""
    assert subprocess.run(
        ["git", "ls-remote", str(remote), "refs/heads/main"], check=True, capture_output=True, text=True
    ).stdout.startswith(before_commit)
    assert json.loads((artifact.directory / "result.json").read_text())["terminal_state"] == "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED"


def test_indeterminate_push_never_retries(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    deployment_workflow, _, promotion_workflow, _ = promotion_modules
    _selected, _remote, contract_document, _, artifact, _ = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    runner = PromotionRunner(contract_document, fail_push=True, indeterminate=True)
    monkeypatch.setattr(builtins, "input", lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}")

    assert promotion_workflow.apply_promotion(
        deployment_workflow.Workflow(repo_root, runner), artifact.directory
    ) == 1
    assert json.loads((artifact.directory / "result.json").read_text())["terminal_state"] == "PUBLICATION_INDETERMINATE"
    assert sum(1 for command, _cwd in runner.calls if command[:2] == ["git", "push"]) == 1


def test_result_evidence_failure_does_not_misreport_confirmed_publication(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    deployment_workflow, _, promotion_workflow, _ = promotion_modules
    _selected, remote, contract_document, state, artifact, _ = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    monkeypatch.setattr(builtins, "input", lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}")
    monkeypatch.setattr(
        promotion_workflow,
        "write_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected result failure")),
    )

    assert promotion_workflow.apply_promotion(
        deployment_workflow.Workflow(repo_root, PromotionRunner(contract_document)), artifact.directory
    ) == 1
    published = subprocess.run(
        ["git", "ls-remote", str(remote), "refs/heads/main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()[0]
    fallbacks = list((state / "promotion-failure-results").glob(f"{artifact.digest}-*.json"))
    assert len(fallbacks) == 1
    fallback = json.loads(fallbacks[0].read_text())
    assert fallback["terminal_state"] == "PROMOTION_RESULT_EVIDENCE_FAILURE"
    assert fallback["published_commit"] == published


def test_already_selected_plan_is_structurally_inapplicable(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    deployment_workflow, promotion_plan, promotion_workflow, paths = promotion_modules
    selected = tmp_path / "stage-a-composition"
    remote = initialize_repository(selected, tmp_path)
    write_lock(selected / "flake.lock", REVISION)
    subprocess.run(["git", "add", "flake.lock"], cwd=selected, check=True)
    subprocess.run(["git", "commit", "-m", "already selected"], cwd=selected, check=True, capture_output=True)
    subprocess.run(["git", "push"], cwd=selected, check=True, capture_output=True)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotion_workflow, "_private_state_root", lambda: state)
    artifact = promotion_workflow.prepare_promotion(
        deployment_workflow.Workflow(repo_root, PromotionRunner(contract(remote))),
        selected,
        "domain-adapter",
        REVISION,
        tmp_path / "already-selected",
        allow_local_fixture=True,
    )
    assert artifact.document["status"] == "ALREADY_SELECTED"
    assert artifact.document["qualifier_result"]["summary"]["outcome"] == "pass"
    assert artifact.document["locked_qualification"]["no_activation"] is True
    assert artifact.document["remote_ref"] == {
        "ref": "refs/heads/main",
        "revision": REVISION,
    }
    assert artifact.document["impact"]["classification"] == "HOST_DERIVATIONS_IDENTICAL"
    assert artifact.document["impact"]["baselineHosts"] == artifact.document["impact"][
        "candidateHosts"
    ]
    assert set(artifact.document["non_mutation"].values()) == {True}
    assert (artifact.directory / "qualifier-result.json").is_file()
    assert (artifact.directory / "locked-qualifier-result.json").is_file()
    markdown = (artifact.directory / "plan.md").read_text()
    assert "structurally inapplicable" in markdown
    assert "Apply confirmation" not in markdown
    assert not list(state.glob("nix-update-promotion-*"))
    assert not list(state.glob("nix-update-lock-*"))
    with pytest.raises(paths.ConfigError, match="inapplicable"):
        promotion_workflow.apply_promotion(
            deployment_workflow.Workflow(repo_root, PromotionRunner(contract(remote))), artifact.directory
        )


def test_plan_tampering_replay_and_qualifier_failures_are_inapplicable(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    deployment_workflow, promotion_plan, promotion_workflow, paths = promotion_modules
    _selected, _remote, contract_document, _state, artifact, _ = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    candidate = artifact.directory / "retained-lock/flake.lock.candidate"
    candidate.write_bytes(candidate.read_bytes() + b" ")
    with pytest.raises(paths.ConfigError, match="artifact identity"):
        promotion_plan.load_plan(artifact.directory)

    bad_contract = dict(contract_document)
    secret_result = qualifier_result(bad_contract)
    secret_result["api_token"] = "do-not-store"
    runner = PromotionRunner(bad_contract)
    runner.__call__ = runner.__call__
    with pytest.raises(paths.ConfigError, match="secret-bearing"):
        promotion_workflow._validate_qualifier_result(
            secret_result,
            "domain-adapter",
            f"{bad_contract['modules'][0]['url']}?rev={REVISION}",
            bad_contract,
            allow_file_urls=True,
        )


def test_reviewed_plan_field_tampering_fails_closed(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    _deployment_workflow, promotion_plan, _promotion_workflow, paths = promotion_modules
    _selected, _remote, _contract_document, _state, artifact, _ = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    defects = [
        (lambda value: value.update(schema="wrong"), "plan schema"),
        (lambda value: value.update(plan_digest="wrong"), "plan digest"),
        (lambda value: value.update(blockers=["unexpected"]), "status is inconsistent"),
        (lambda value: value.update(contract=None), "plan contract"),
        (lambda value: value.update(contract_digest="0" * 64), "contract digest"),
        (lambda value: value.update(input=None), "plan input"),
        (lambda value: value.update(canonical_repository_url="https://example.invalid/wrong"), "URL drifted"),
        (lambda value: value.update(candidate_ref="https://example.invalid/wrong?rev=" + REVISION), "reference is not exact"),
        (
            lambda value: value["remote_ref"].update(ref="refs/heads/deploy"),
            "remote release-ref evidence",
        ),
        (
            lambda value: value.update(impact_digest="0" * 64),
            "impact digest",
        ),
        (
            lambda value: value["impact"].update(
                classification="HOST_DERIVATIONS_CHANGED"
            ),
            "impact record classification",
        ),
        (
            lambda value: value["impact"].update(
                changedHosts=["StageAHost", "StageAHost"]
            ),
            "impact record classification",
        ),
        (
            lambda value: value["baseline_host_observation"].update(hosts={}),
            "baseline host observation",
        ),
        (lambda value: value.update(lock_delta=None), "lock authorization"),
        (lambda value: value.update(closure_authorization={}), "authorization fields"),
        (
            lambda value: value["closure_authorization"].update(
                candidateOwnedNodes=["z", "a"]
            ),
            "not canonical",
        ),
        (
            lambda value: value["closure_authorization"].update(
                authorizationDigest="0" * 64
            ),
            "authorization digest",
        ),
        (
            lambda value: value.update(lock_authorization_evidence=None),
            "lock authorization evidence binding",
        ),
        (lambda value: value.update(qualifier=None), "qualifier identity"),
        (lambda value: value["qualifier"].update(relative_path="bin/wrong"), "path drifted"),
        (lambda value: value.update(commit=None), "commit policy"),
        (lambda value: value["commit"].update(changed_paths=["flake.nix"]), "flake.lock-only"),
        (lambda value: value.update(confirmation="YES"), "confirmation shape"),
        (lambda value: value.update(locked_qualification=None), "locked qualification evidence"),
        (lambda value: value["locked_qualification"].update(no_activation=False), "must forbid activation"),
        (lambda value: value.update(publication=None), "publication or replay"),
        (lambda value: value.update(entry_lock=None), "entry_lock binding"),
        (lambda value: value["entry_lock"].update(path="/absolute"), "path is invalid"),
        (lambda value: value["entry_lock"].update(sha256="wrong"), "digest is invalid"),
        (lambda value: value["entry_lock"].update(size=-1), "size is invalid"),
    ]
    for mutate, message in defects:
        document = copy.deepcopy(artifact.document)
        mutate(document)
        with pytest.raises(paths.ConfigError, match=message):
            promotion_plan.validate_plan(document)

    with pytest.raises(paths.ConfigError, match="must be an object"):
        promotion_plan.validate_plan([])
    invalid_status = copy.deepcopy(artifact.document)
    invalid_status["status"] = "PREFLIGHT"
    with pytest.raises(paths.ConfigError, match="status is invalid"):
        promotion_plan.validate_plan(invalid_status)
    unsigned = copy.deepcopy(artifact.document)
    with pytest.raises(paths.ConfigError, match="unsigned promotion plan digest"):
        promotion_plan.validate_plan(unsigned, allow_unsigned=True)


def test_plan_rejects_unbound_artifact_topology(
    promotion_modules, repo_root: Path, tmp_path: Path, monkeypatch
):
    _deployment_workflow, promotion_plan, _promotion_workflow, paths = promotion_modules
    _selected, _remote, _contract_document, _state, artifact, _ = promotion_fixture(
        promotion_modules, repo_root, tmp_path, monkeypatch
    )
    # result.json, result.md, and install-terminal fallback files are allowed non-authority artifacts
    result_json = artifact.directory / "result.json"
    result_json.write_text("{}\n")
    result_json.chmod(0o600)
    result_md = artifact.directory / "result.md"
    result_md.write_text("# Result\n")
    result_md.chmod(0o600)
    fallback = artifact.directory / "retained-lock" / "install-terminal-failure-12345678.json"
    fallback.write_text("{}\n")
    fallback.chmod(0o600)
    assert promotion_plan.load_plan(artifact.directory).digest == artifact.digest

    unbound = artifact.directory / "unbound-evidence.json"
    unbound.write_text("{}\n")
    unbound.chmod(0o600)
    with pytest.raises(paths.ConfigError, match="unbound artifact"):
        promotion_plan.load_plan(artifact.directory)


def test_promotion_source_structurally_forbids_activation_tokens(repo_root: Path):
    promotion_dir = repo_root / "libexec/nix_darwin_local"
    forbidden_tokens = [
        "sudo",
        "darwin-rebuild",
        "activate-system",
        "/nix/var/nix/profiles",
        "brew",
        "localConfig",
    ]
    for source_file in sorted(promotion_dir.glob("promotion_*.py")):
        text = source_file.read_text()
        for token in forbidden_tokens:
            assert token not in text, f"Forbidden token {token!r} found in {source_file.name}"


def test_core_campaign_prepared_plan_roundtrips_production_loader(
    promotion_modules, repo_root, tmp_path, monkeypatch
):
    deployment, plans, promotions, _ = promotion_modules
    from nix_darwin_local import promotion_topology
    from types import SimpleNamespace

    selected = tmp_path / "composition"
    remote = initialize_repository(selected, tmp_path)
    document = json.loads((selected / "flake.lock").read_text())
    document["nodes"]["root"]["inputs"] = {"core": "domain-adapter-node"}
    node = document["nodes"]["domain-adapter-node"]
    node["original"]["dir"] = node["locked"]["dir"] = "nix-darwin"
    (selected / "flake.lock").write_text(json.dumps(document))
    subprocess.run(["git", "commit", "-am", "core fixture"], cwd=selected, check=True, capture_output=True)
    subprocess.run(["git", "push"], cwd=selected, check=True, capture_output=True)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(deployment, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotions, "_private_state_root", lambda: state)

    class CoreRunner(PromotionRunner):
        def __call__(self, argv, cwd=None, **kwargs):
            if list(argv)[:3] == ["nix", "flake", "update"]:
                candidate = copy.deepcopy(document)
                candidate["nodes"]["domain-adapter-node"]["locked"].update(
                    rev=REVISION, narHash="sha256-new="
                )
                (Path(cwd) / "flake.lock").write_text(json.dumps(candidate))
                return subprocess.CompletedProcess(argv, 0, "", "")
            if list(argv)[:3] == ["nix", "flake", "metadata"]:
                return subprocess.CompletedProcess(
                    argv, 0, json.dumps({"locks": json.loads((Path(cwd) / "flake.lock").read_text())}), ""
                )
            return super().__call__(argv, cwd=cwd, **kwargs)

    workflow = deployment.Workflow(repo_root, CoreRunner(contract(remote)))
    artifact = promotion_topology.prepare_campaign_stage(
        workflow, selected, "core", REVISION, tmp_path / "plan",
        SimpleNamespace(allow_local_fixture=True), {"kind": "composition"},
    )
    assert artifact.document["status"] == "READY_FOR_REVIEW", artifact.document
    loaded = plans.load_plan(artifact.directory)
    assert loaded.digest == artifact.digest
    assert loaded.document["closure_authorization"]["schema"] == "nix-update-core-lock-transition-v1"
