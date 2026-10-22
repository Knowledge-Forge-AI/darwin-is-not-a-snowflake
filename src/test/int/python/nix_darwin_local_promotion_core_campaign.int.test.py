# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_binding.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_core_qualification.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_plan.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_workflow.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_campaign.py
"""Real Git/campaign/plan/apply boundaries; only Nix and terminal input controlled."""

from __future__ import annotations

import copy
import errno
import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))
from nix_darwin_local import deployment_workflow as deployment
from nix_darwin_local import promotion_campaign as campaign
from nix_darwin_local import promotion_campaign_state as campaign_state
from nix_darwin_local import promotion_topology as topology
from nix_darwin_local import promotion_workflow as promotion
from nix_darwin_local.deployment_plan import canonical_bytes, lock_delta, sha256_bytes
from nix_darwin_local.paths import ConfigError
from nix_darwin_local.promotion_plan import load_plan

HELPERS = runpy.run_path(str(CORE_ROOT / "src/test/unit/python/nix_darwin_local_promotion_topology.unit.test.py"))


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE, text=True).strip()


def commit(root, subject):
    git(root, "add", "-A")
    git(root, "commit", "-m", subject)
    git(root, "push", "-u", "origin", "main")
    return git(root, "rev-parse", "HEAD")


def repository(tmp, name):
    root, remote = tmp / name, tmp / f"{name}.git"
    git(tmp, "init", "--bare", "--initial-branch=main", str(remote))
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Promotion Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "remote", "add", "origin", remote.as_uri())
    return root, remote


def node(url, rev, *, core=False):
    original = {"type": "git", "url": url, "ref": "main"}
    if core:
        original["dir"] = "nix-darwin"
    return {"original": original, "locked": {**original, "rev": rev, "narHash": f"sha256-{rev}="}}


def write_composition(root, contract, nodes, inputs):
    (root / "tools").mkdir()
    qualifier = root / "tools/qualify-promotion"
    qualifier.write_text("#!/bin/sh\nexit 99\n")
    qualifier.chmod(0o700)
    (root / "flake.nix").write_text("{}\n")
    (root / "promotion-contract.json").write_text(json.dumps(contract))
    (root / "flake.lock").write_text(json.dumps({"version": 7, "root": "root", "nodes": {"root": {"inputs": inputs}, **nodes}}))


class NixRunner:
    def __init__(self, core, public, changed):
        self.core, self.public, self.changed = core, public, changed
        self.calls = []
        self.fail_checks = False
        self.fail_push = False
        self.indeterminate = False
        self.pushed = False
        self.contract_drift = False

    def __call__(self, argv, cwd=None, **kwargs):
        argv, root = list(argv), Path(cwd)
        self.calls.append((argv, root))
        if argv[0] == "git":
            if argv[1] == "push" and any(arg.startswith("HEAD:") for arg in argv):
                self.pushed = True
                if self.fail_push:
                    return subprocess.CompletedProcess(argv, 1, "", "injected rejection")
            if self.indeterminate and self.pushed and argv[1:3] == ["ls-remote", "--exit-code"]:
                return subprocess.CompletedProcess(argv, 2, "", "injected uncertainty")
            return subprocess.run(argv, cwd=root, **kwargs)
        lock = json.loads((root / "flake.lock").read_text())
        contract = json.loads((root / "promotion-contract.json").read_text())
        selected = "core" if contract["composition"]["name"] == "composition-nd" else "composition-nd"
        selected_node = lock["nodes"][selected]
        revision = selected_node["locked"]["rev"]
        target = git(self.core if selected == "core" else self.public, "rev-parse", "HEAD")
        if argv[:3] == ["nix", "eval", "--json"]:
            if self.contract_drift and revision == target:
                contract["checks"] = ["drifted-check"]
            out = json.dumps(contract)
        elif argv[:3] == ["nix", "eval", "--raw"]:
            suffix = revision if self.changed else "identical"
            out = f"/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-{suffix}.drv"
        elif argv[:3] == ["nix", "flake", "update"]:
            selected_node["locked"].update(rev=target, narHash=f"sha256-{target}=")
            if selected == "composition-nd":
                public_lock = json.loads((self.public / "flake.lock").read_text())
                lock["nodes"]["core"] = public_lock["nodes"]["core"]
            (root / "flake.lock").write_text(json.dumps(lock))
            out = ""
        elif argv[:3] == ["nix", "flake", "metadata"]:
            out = json.dumps({"locks": lock})
        elif argv[:2] == ["nix", "build"]:
            if self.fail_checks:
                return subprocess.CompletedProcess(argv, 1, "", "injected check failure")
            out = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-fixture\n"
        elif argv[0].endswith("qualify-promotion"):
            # The private stage uses the unchanged owner entry point/protocol.
            assert selected == "composition-nd"
            ref = argv[argv.index("--candidate") + 1]
            requested = ref.split("?rev=")[1]
            out = json.dumps({
                "schema": "fixture-owner-qualification-v1", "input": selected,
                "outcome": "pass", "failure": None,
                "candidate": {"url": ref, "requestedRev": requested, "resolvedRev": requested,
                              "resolvedUrl": self.public.as_uri().replace("composition-nd", "composition-nd.git"),
                              "revMatched": True, "urlMatched": True},
                "checks": {name: "pass" for name in contract["checks"]},
                "hosts": {host: {"status": "pass", "drvPath": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-fixture.drv"} for host in contract["hosts"]},
                "lock": {"byteIdentical": True}, "tree": {"byteIdentical": True},
            })
        else:
            raise AssertionError(argv)
        return subprocess.CompletedProcess(argv, 0, out, "")


@pytest.fixture
def chain(tmp_path, monkeypatch):
    core, core_remote = repository(tmp_path, "source")
    tool = core / "nix-darwin"
    (tool / "bin").mkdir(parents=True)
    shutil.copy2(CORE_ROOT / "bin/nix-update", tool / "bin/nix-update")
    shutil.copytree(CORE_ROOT / "libexec/nix_darwin_local", tool / "libexec/nix_darwin_local", ignore=shutil.ignore_patterns("__pycache__"))
    (tool / "flake.nix").write_text("{}\n")
    old = commit(core, "source baseline")
    public, public_remote = repository(tmp_path, "composition-nd")
    private, private_remote = repository(tmp_path, "composition-private-nd")
    module = HELPERS["_module"]("domain", "domain")
    public_contract = HELPERS["_base"]("composition-nd", [module])
    public_contract["hosts"] = ["HostA", "HostB"]
    public_contract["composition"]["repositoryUrl"] = public_remote.as_uri()
    core_node = node(core_remote.as_uri(), old, core=True)
    core_node["inputs"] = {"child": "child"}
    leaf = node("https://example.invalid/child", "c" * 40)
    write_composition(public, public_contract, {"core": core_node, "child": leaf}, {"core": "core"})
    public_base = commit(public, "public baseline")
    private_contract = HELPERS["_layered"](public_contract, "composition-private-nd", [HELPERS["_module"]("private", "private")])
    private_contract["composition"]["repositoryUrl"] = private_remote.as_uri()
    public_node = node(public_remote.as_uri(), public_base)
    public_node["inputs"] = {"core": "core"}
    write_composition(private, private_contract, {"composition-nd": public_node, "core": core_node, "child": leaf}, {"composition-nd": "composition-nd"})
    private_base = commit(private, "private baseline")
    (tool / "payload").write_text("new release\n")
    revision = commit(core, "source release")
    for relative in ("nix-darwin/.serena/session.json", "nix-darwin/bin/claude"):
        path = core / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"unrelated operational bytes\n")
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    for owner in (deployment, promotion, campaign):
        monkeypatch.setattr(owner, "_private_state_root", lambda: state)
    monkeypatch.setattr(promotion, "_guard_processes", lambda *_: None)
    runner = NixRunner(core, public, True)
    workflow = deployment.Workflow(tool, runner, display_command_output=False)
    config = SimpleNamespace(allow_local_fixture=True, workspace_root=tmp_path, promotion_checkouts=())
    return SimpleNamespace(core=core, tool=tool, public=public, private=private,
                           public_remote=public_remote, private_remote=private_remote,
                           public_base=public_base, private_base=private_base, revision=revision,
                           workflow=workflow, runner=runner, config=config, tmp=tmp_path)


def prepare(chain):
    artifact = topology.prepare_campaign_stage(
        chain.workflow, chain.public, "core", chain.revision, chain.tmp / "plan",
        chain.config, {"kind": "composition"},
    )
    assert artifact.document["status"] == "READY_FOR_REVIEW", artifact.document
    return load_plan(artifact.directory)


def confirm(prompt):
    return prompt.split("'")[1]


@pytest.mark.parametrize("changed", [True, False])
def test_core_campaign_real_loader_apply_both_layers_and_impact(chain, monkeypatch, changed):
    chain.runner.changed = changed
    monkeypatch.setattr(campaign, "_has_controlling_terminal", lambda: True)
    monkeypatch.setattr(campaign, "_tty_confirmation", confirm)
    output = chain.tmp / "campaign"
    assert campaign.run_campaign(chain.workflow, chain.private, chain.config, "core", chain.revision, output) == 0
    document = campaign_state.load_campaign(output)
    assert document["state"] == ("AWAITING_ACTIVATION" if changed else "COMPLETE_NO_ACTIVATION")
    assert document["activationPerformed"] is False
    assert [item["name"] for item in document["stages"]] == ["core", "composition-nd", "composition-private-nd"]
    public, private = document["stages"][1:]
    assert private["revision"] == public["published_commit"] == git(chain.public, "rev-parse", "HEAD")
    for root, base, stage in ((chain.public, chain.public_base, public), (chain.private, chain.private_base, private)):
        assert git(root, "rev-list", "--count", f"{base}..HEAD") == "1"
        assert git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") == "flake.lock"
        assert git(root, "rev-parse", "HEAD") == git(root, "rev-parse", "origin/main")
        artifact = load_plan(Path(stage["attempts"][0]["plan_path"]))
        assert artifact.digest == stage["attempts"][0]["plan_digest"]
        assert json.loads((artifact.directory / "result.json").read_text())["impact"] == artifact.document["impact"]
    assert (chain.tool / ".serena/session.json").read_bytes() == b"unrelated operational bytes\n"
    assert (chain.tool / "bin/claude").read_bytes() == b"unrelated operational bytes\n"
    assert not any(argv[0] in {"sudo", "darwin-rebuild"} for argv, _ in chain.runner.calls)


def test_core_no_terminal_preserves_plan_and_resume_uses_exact_digest(chain, monkeypatch):
    def unavailable(_):
        raise OSError(errno.ENXIO, "no controlling terminal")
    monkeypatch.setattr(campaign, "_tty_confirmation", unavailable)
    monkeypatch.setattr(campaign, "_has_controlling_terminal", lambda: True)
    output = chain.tmp / "campaign"
    campaign.run_campaign(chain.workflow, chain.private, chain.config, "core", chain.revision, output)
    waiting = campaign_state.load_campaign(output)
    assert waiting["state"] == "AWAITING_APPLY"
    stage = waiting["stages"][1]
    prepared = load_plan(Path(stage["current_plan"]))
    assert git(chain.public, "rev-parse", "HEAD") == chain.public_base
    monkeypatch.setattr(campaign, "_tty_confirmation", confirm)
    assert campaign.resume_campaign(chain.workflow, chain.private, chain.config, output) == 0
    done = campaign_state.load_campaign(output)
    assert done["state"] == "AWAITING_ACTIVATION"
    assert done["stages"][1]["attempts"][0]["plan_digest"] == prepared.digest


def test_core_prepublication_failure_restores_exact_preimage(chain):
    original = (chain.public / "flake.lock").read_bytes()
    artifact = prepare(chain)
    chain.runner.fail_checks = True
    assert promotion.apply_promotion(chain.workflow, artifact.directory, confirm=confirm) == 1
    result = json.loads((artifact.directory / "result.json").read_text())
    assert result["terminal_state"] == "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED"
    assert (chain.public / "flake.lock").read_bytes() == original
    assert git(chain.public, "rev-parse", "HEAD") == chain.public_base
    assert git(chain.public, "status", "--porcelain") == ""


@pytest.mark.parametrize("indeterminate", [False, True])
def test_core_publication_interruption_and_consumed_recovery(chain, indeterminate):
    artifact = prepare(chain)
    chain.runner.fail_push = True
    chain.runner.indeterminate = indeterminate
    assert promotion.apply_promotion(chain.workflow, artifact.directory, confirm=confirm) == 1
    result = json.loads((artifact.directory / "result.json").read_text())
    assert result["terminal_state"] == ("PUBLICATION_INDETERMINATE" if indeterminate else "COMMITTED_NOT_PUBLISHED")
    created = git(chain.public, "rev-parse", "HEAD")
    chain.runner.fail_push = chain.runner.indeterminate = False
    assert promotion.apply_promotion(chain.workflow, artifact.directory, confirm=confirm) == 0
    assert git(chain.public, "rev-parse", "HEAD") == created == git(chain.public, "rev-parse", "origin/main")
    assert git(chain.public, "rev-list", "--count", f"{chain.public_base}..HEAD") == "1"


def test_core_candidate_contract_change_blocks_before_mutation(chain):
    chain.runner.contract_drift = True
    artifact = topology.prepare_campaign_stage(chain.workflow, chain.public, "core", chain.revision, chain.tmp / "plan", chain.config, {"kind": "composition"})
    assert load_plan(artifact.directory).document["status"] == "BLOCKED"
    assert "candidate contract differs" in artifact.document["blockers"][0]
    assert git(chain.public, "rev-parse", "HEAD") == chain.public_base


def persist_mutated_plan(artifact, document):
    document["plan_digest"] = ""
    document["plan_digest"] = sha256_bytes(canonical_bytes(document))
    (artifact.directory / "plan.json").write_bytes(canonical_bytes(document))


@pytest.mark.parametrize("defect", ["subflake", "url", "ref", "children", "outside", "authorization", "extra", "missing", "digest", "retained"])
def test_core_production_loader_rejects_tampered_authority(chain, defect):
    artifact = prepare(chain)
    document = copy.deepcopy(artifact.document)
    candidate = document["candidate_lock"]["normalized"]
    core = candidate["nodes"]["core"]
    if defect == "subflake":
        core["locked"]["dir"] = "other"
    elif defect == "url":
        core["locked"]["url"] = "https://example.invalid/wrong"
    elif defect == "ref":
        core["original"]["ref"] = "deploy"
    elif defect == "children":
        core["inputs"] = {}
    elif defect == "outside":
        candidate["nodes"]["root"]["extra"] = True
    elif defect == "authorization":
        document["closure_authorization"]["childGraphDigest"] = "0" * 64
    elif defect == "extra":
        document["trusted"] = True
    elif defect == "missing":
        del document["tool"]
    elif defect == "retained":
        path = artifact.directory / document["candidate_lock"]["path"]
        path.write_bytes(path.read_bytes() + b"\n")
    if defect in {"subflake", "url", "ref", "children", "outside"}:
        document["lock_delta"] = lock_delta(document["entry_lock"]["normalized"], candidate)
    persist_mutated_plan(artifact, document)
    if defect == "digest":
        document["plan_digest"] = "0" * 64
        (artifact.directory / "plan.json").write_bytes(canonical_bytes(document))
    with pytest.raises(ConfigError):
        load_plan(artifact.directory)
    assert git(chain.public, "rev-parse", "HEAD") == chain.public_base


def test_core_plan_repository_and_execution_tool_drift_block_apply(chain):
    artifact = prepare(chain)
    document = copy.deepcopy(artifact.document)
    document["repository"]["commit"] = "f" * 40
    persist_mutated_plan(artifact, document)
    with pytest.raises(ConfigError):
        load_plan(artifact.directory)  # retained source authority still binds the real repo
    persist_mutated_plan(artifact, artifact.document.copy())
    (chain.tool / "libexec/nix_darwin_local/promotion_workflow.py").write_text("# drift\n")
    assert promotion.apply_promotion(chain.workflow, artifact.directory, confirm=confirm) == 1
    result = json.loads((artifact.directory / "result.json").read_text())
    assert result["terminal_state"] == "BLOCKED_BEFORE_MUTATION"
    assert "identity drifted" in result["failure"]
    assert git(chain.public, "rev-parse", "HEAD") == chain.public_base


def test_core_stale_tool_campaign_reprepares_without_applying_old_plan(chain, monkeypatch):
    monkeypatch.setattr(campaign, "_has_controlling_terminal", lambda: False)
    output = chain.tmp / "campaign"
    campaign.run_campaign(chain.workflow, chain.private, chain.config, "core", chain.revision, output)
    waiting = campaign_state.load_campaign(output)
    first = load_plan(Path(waiting["stages"][1]["current_plan"]))
    tool_file = chain.tool / "libexec/nix_darwin_local/promotion_binding.py"
    tool_file.write_text(tool_file.read_text() + "\n# new tool release\n")
    campaign.resume_campaign(chain.workflow, chain.private, chain.config, output)
    refreshed = campaign_state.load_campaign(output)
    stage = refreshed["stages"][1]
    assert len(stage["attempts"]) == 2
    assert stage["attempts"][0]["plan_digest"] == first.digest
    assert load_plan(Path(stage["current_plan"])).digest != first.digest
    assert not (first.directory / "result.json").exists()
    assert git(chain.public, "rev-parse", "HEAD") == chain.public_base


def test_core_already_selected_layers_do_not_create_empty_commits(chain, monkeypatch):
    artifact = prepare(chain)
    assert promotion.apply_promotion(chain.workflow, artifact.directory, confirm=confirm) == 0
    public_head = git(chain.public, "rev-parse", "HEAD")
    monkeypatch.setattr(campaign, "_has_controlling_terminal", lambda: True)
    monkeypatch.setattr(campaign, "_tty_confirmation", confirm)
    first = chain.tmp / "campaign"
    assert campaign.run_campaign(chain.workflow, chain.private, chain.config, "core", chain.revision, first) == 0
    assert campaign_state.load_campaign(first)["stages"][1]["status"] == "ALREADY_SELECTED"
    assert git(chain.public, "rev-parse", "HEAD") == public_head
    private_head = git(chain.private, "rev-parse", "HEAD")
    second = chain.tmp / "second-campaign"
    assert campaign.run_campaign(chain.workflow, chain.private, chain.config, "core", chain.revision, second) == 0
    final = campaign_state.load_campaign(second)
    assert [item["status"] for item in final["stages"]] == ["ALREADY_SELECTED"] * 3
    assert final["state"] == "AWAITING_DEPLOYMENT_RECONCILIATION"
    assert git(chain.public, "rev-parse", "HEAD") == public_head
    assert git(chain.private, "rev-parse", "HEAD") == private_head
