# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_source.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_core_qualification.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_result.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_binding.py
"""Adverse persisted-receipt and native locked-core qualification contracts."""

import copy
import importlib
import json
import subprocess

import pytest


@pytest.fixture
def receipt(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    source = importlib.import_module("nix_darwin_local.promotion_source")
    qualification = importlib.import_module("nix_darwin_local.promotion_qualification")
    return source, {
        "schema": source.SOURCE_RECEIPT_SCHEMA, "status": "ALREADY_SELECTED", "input": "core",
        "revision": "a" * 40,
        "repository": {"commit": "a" * 40, "head_commit": "b" * 40, "repository_root": "/fixture"},
        "execution_repository": {"commit": "b" * 40, "repository_root": "/fixture"},
        "tool": qualification.promotion_tool_identity(repo_root),
        "impact": None, "no_activation": True, "receipt_digest": "",
    }


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(extra=True),
    lambda d: d.pop("execution_repository"),
    lambda d: d.update(schema="nix-update-promotion-plan-v2"),
    lambda d: d.update(status="READY_FOR_REVIEW"),
    lambda d: d.update(input="domain"),
    lambda d: d.update(impact={}),
    lambda d: d.update(no_activation=False),
    lambda d: d.update(revision="HEAD"),
    lambda d: d.update(repository={}),
    lambda d: d["repository"].update(commit="HEAD"),
    lambda d: d["repository"].update(commit="c" * 40),
    lambda d: d["repository"].update(repository_root="relative"),
    lambda d: d["execution_repository"].update(commit=None),
    lambda d: d["execution_repository"].update(extra=True),
    lambda d: d["tool"].update(files=[]),
    lambda d: d.update(receipt_digest="0" * 64),
])
def test_source_receipt_rejects_inconsistent_authority(receipt, mutate):
    source, document = receipt
    source.validate_source_receipt(document, allow_unsigned=True)
    mutate(document)
    with pytest.raises(source.ConfigError):
        source.validate_source_receipt(document, allow_unsigned=True)


@pytest.mark.parametrize("raw_kind", ["noncanonical", "unicode", "digest", "artifact", "missing-digest"])
def test_source_receipt_loader_validates_exact_artifact(receipt, tmp_path, raw_kind):
    source, document = receipt
    artifact = source.write_source_receipt(tmp_path / "receipt", document)
    path = artifact.directory / "receipt.json"
    assert source.load_source_receipt(path).digest == artifact.digest
    if raw_kind == "noncanonical":
        path.write_text(json.dumps(artifact.document, indent=2))
    elif raw_kind == "unicode":
        path.write_bytes(b"\xff")
    elif raw_kind == "artifact":
        (artifact.directory / "unexpected").write_text("unbound")
    else:
        changed = copy.deepcopy(artifact.document)
        changed["receipt_digest"] = "0" * 64 if raw_kind == "digest" else "invalid"
        path.write_bytes(source.canonical_bytes(changed))
    with pytest.raises(source.ConfigError):
        source.load_source_receipt(artifact.directory)


def test_source_receipt_writer_and_loader_reject_relative_paths(receipt):
    from pathlib import Path
    source, document = receipt
    for operation in (lambda: source.write_source_receipt(Path("relative"), document),
                      lambda: source.load_source_receipt(Path("relative"))):
        with pytest.raises(source.ConfigError, match="absolute"):
            operation()


@pytest.fixture
def core_qualification(repo_root, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    deployment = importlib.import_module("nix_darwin_local.deployment_workflow")
    core = importlib.import_module("nix_darwin_local.promotion_core_qualification")
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(deployment, "_private_state_root", lambda: state)
    tree = tmp_path / "tree"
    tree.mkdir()
    url, revision = "ssh://git@example.invalid/core.git", "a" * 40
    declaration = {"type": "git", "url": url, "dir": "nix-darwin", "ref": "main"}
    lock = {"version": 7, "root": "root", "nodes": {
        "root": {"inputs": {"core": "core"}},
        "core": {"original": declaration, "locked": {**declaration, "rev": revision, "narHash": "sha256-fixture="}},
    }}
    (tree / "flake.lock").write_text(json.dumps(lock))
    contract = {"system": "aarch64-darwin", "checks": ["contracts"], "hosts": ["HostA", "HostB"]}
    return deployment, core, tree, contract, lock, f"{url}?rev={revision}"


@pytest.mark.parametrize("fault", ["revision", "url", "metadata-fail", "metadata-large", "metadata-drift", "build-fail", "build-empty", "build-invalid", "tree-drift", "lock-drift"])
def test_locked_core_qualification_fails_closed(core_qualification, repo_root, fault, monkeypatch):
    deployment, core, tree, contract, lock, ref = core_qualification
    if fault == "metadata-large":
        monkeypatch.setattr(core, "MAX_NATIVE_RESULT_BYTES", 1024)

    def runner(argv, **kwargs):
        status, out = 0, "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-fixture\n"
        if argv[:3] == ["nix", "flake", "metadata"]:
            status = 1 if fault == "metadata-fail" else 0
            out = json.dumps({"locks": lock})
            if fault == "metadata-large":
                out = "x" * (core.MAX_NATIVE_RESULT_BYTES + 1)
            if fault == "metadata-drift":
                out = json.dumps({"locks": {}})
        elif argv[:2] == ["nix", "build"]:
            if fault == "build-fail":
                status = 1
            elif fault == "build-empty":
                out = ""
            elif fault == "build-invalid":
                out = "not-a-store-path"
            elif fault == "tree-drift":
                (tree / "unexpected").write_text("mutation")
            elif fault == "lock-drift":
                (tree / "flake.lock").write_text(json.dumps(lock, indent=2))
        else:
            out = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-fixture.drv"
        return subprocess.CompletedProcess(argv, status, out, "")

    workflow = deployment.Workflow(repo_root, runner, display_command_output=False)
    if fault == "revision":
        ref = ref.replace("a" * 40, "b" * 40)
    elif fault == "url":
        ref = ref.replace("core.git", "other.git")
    message = "core qualification must select the exact retained candidate revision" if fault in {"revision", "url"} else None
    with pytest.raises(core.ConfigError, match=message):
        core.qualify_core(workflow, tree, ref, contract)


def test_core_binding_never_authorizes_other_release_channels(core_qualification):
    _, _, _, _, lock, _ = core_qualification
    binding = importlib.import_module("nix_darwin_local.promotion_binding")
    with pytest.raises(binding.ConfigError, match="main release ref"):
        binding.authorize_transition(lock, lock, "core", "unused", "a" * 40,
                                     input_name="core", allow_file=False,
                                     expected_ref="refs/heads/deploy", allow_implicit_main=False)


@pytest.mark.parametrize("optional_evidence", [False, True])
def test_core_qualification_summary_reports_only_observed_proofs(core_qualification, repo_root, optional_evidence):
    deployment, core, tree, contract, lock, ref = core_qualification

    def runner(argv, **kwargs):
        if argv[:3] == ["nix", "flake", "metadata"]:
            out = json.dumps({"locks": lock})
        elif argv[:2] == ["nix", "build"]:
            out = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-fixture\n"
        else:
            out = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-fixture.drv"
        return subprocess.CompletedProcess(argv, 0, out, "")

    workflow = deployment.Workflow(repo_root, runner, display_command_output=False)
    result = core.qualify_core(workflow, tree, ref, contract)
    validate = importlib.import_module("nix_darwin_local.promotion_result").validate_native_result
    summary = validate(result, "core", ref, contract)
    assert summary["proofs"] == {"lock_byte_identical": True, "tree_byte_identical": True}
    assert result["qualification"]["ownerEntryPointRole"] == "identity-drift-check-only"
    assert result["candidate"]["revMatched"] is True
    assert result["candidate"]["urlMatched"] is True

    if optional_evidence:
        result["contractCompatibility"] = {"status": "pass"}
        result["snapshots"] = {"valid": True, "byteIdentical": True}
        observed = validate(result, "core", ref, contract)
        assert observed["proofs"]["contract_compatible"] is True
        assert observed["proofs"]["snapshots_valid"] is True
        assert observed["proofs"]["snapshots_byte_identical"] is True

    # Existing registered-input summaries retain their historical optional defaults.
    result["input"] = "domain"
    legacy = validate(result, "domain", ref, contract)
    assert legacy["proofs"]["snapshots_valid"] is True
    assert legacy["proofs"]["contract_compatible"] is True


@pytest.mark.parametrize("input_name", ["core", "composition-nd"])
@pytest.mark.parametrize("candidate_url,binding_url,allow_file,accepted", [
    ("ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git", "git+ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git", False, True),
    ("git+ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git", "ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git", False, True),
    ("ssh://git@github.com/Knowledge-Forge-AI/composition_dinas", "git+ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git", False, True),
    ("ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git", "ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git", False, True),
    ("ssh://git@github.com/Knowledge-Forge-AI/other.git", "git+ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git", False, False),
    ("ssh://git@other.invalid/Knowledge-Forge-AI/composition_dinas.git", "git+ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git", False, False),
    ("ssh://git@github.com:2222/Knowledge-Forge-AI/composition_dinas.git", "git+ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git", False, False),
    ("file:///fixture/core.git", "file:///fixture/core.git", False, False),
    ("file:///fixture/core.git", "file:///fixture/core.git", True, True),
    ("file:///fixture/core", "file:///fixture/core.git", True, False),
])
def test_qualifier_repository_identity(core_qualification, repo_root, input_name,
                                       candidate_url, binding_url, allow_file, accepted):
    deployment, core, tree, contract, lock, _ = core_qualification
    for field in ("original", "locked"):
        lock["nodes"]["core"][field]["url"] = binding_url
    if input_name != "core":
        lock["nodes"]["root"]["inputs"] = {input_name: "core"}
        lower = {"composition": {"name": input_name, "repositoryUrl": binding_url}}
        contract.update({
            "schema": "fixture-private-promotion-v1", "version": 1,
            "composition": {"name": "private", "repositoryUrl": "ssh://git@example.invalid/private.git"},
            "modules": [], "packageSetIdentity": "/nix/store/fixture-nixpkgs",
            "qualification": {"entryPoint": "bin/qualify-promotion", "mode": "exact-candidate"},
            "promotableInputs": {"rule": "layered-private-parent", "names": [input_name]},
            "requiredLowerPromotion": {"candidateInput": input_name},
            "lowerPromotion": lower, "combinedModules": [],
            "semantics": {"nonActivation": True, "requiresExplicitLockSelection": True},
        })
    (tree / "flake.lock").write_text(json.dumps(lock))
    ref = f"{candidate_url}?rev={'a' * 40}"

    def runner(argv, **kwargs):
        if argv[:3] == ["nix", "flake", "metadata"]:
            out = json.dumps({"locks": lock})
        elif argv[:2] == ["nix", "build"]:
            out = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-fixture"
        else:
            out = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-fixture.drv"
        return subprocess.CompletedProcess(argv, 0, out, "")

    workflow = deployment.Workflow(repo_root, runner, display_command_output=False)
    if not accepted:
        with pytest.raises(core.ConfigError):
            core.qualify_core(workflow, tree, ref, contract,
                              input_name=input_name, allow_file_urls=allow_file)
        assert workflow.records == []
        return
    result = core.qualify_core(workflow, tree, ref, contract,
                               input_name=input_name, allow_file_urls=allow_file)
    assert result["candidate"] == {
        "url": ref, "requestedRev": "a" * 40, "resolvedRev": "a" * 40,
        "resolvedUrl": core.resolve_binding(contract, input_name, lock, allow_file=allow_file)["repository_url"],
        "revMatched": True, "urlMatched": True,
    }
    validate = importlib.import_module("nix_darwin_local.promotion_result").validate_native_result
    validate(result, input_name, ref, contract, allow_file_urls=allow_file)


@pytest.mark.parametrize("reference", [
    "ssh://git@example.invalid/core.git",
    "ssh://git@example.invalid/core.git?rev=HEAD",
    "ssh://git@example.invalid/core.git?rev={rev}?rev={rev}",
    "ssh://git@example.invalid/core.git?rev={rev}&dir=nix-darwin",
    "ssh://git@example.invalid/core.git?ref=main?rev={rev}",
    "ssh://git@example.invalid/core.git#selector?rev={rev}",
    "ssh://git:password@example.invalid/core.git?rev={rev}",
    "git@example.invalid:core.git?rev={rev}",
    "git://example.invalid/core.git?rev={rev}",
    "ssh://git@example.invalid/core.git?rev=" + "b" * 40,
])
def test_qualifier_rejects_invalid_candidate_before_commands(core_qualification, repo_root, reference):
    deployment, core, tree, contract, _, _ = core_qualification
    def runner(*args, **kwargs):
        pytest.fail("invalid candidate reached qualification commands")
    workflow = deployment.Workflow(repo_root, runner, display_command_output=False)
    with pytest.raises(core.ConfigError):
        core.qualify_core(workflow, tree, reference.format(rev="a" * 40), contract)
    assert workflow.records == []
