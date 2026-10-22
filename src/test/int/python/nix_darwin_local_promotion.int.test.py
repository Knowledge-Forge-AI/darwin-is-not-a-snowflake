# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_contract.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_lock.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_qualification.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/deployment_workflow.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_plan.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_result.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_workflow.py

from __future__ import annotations

import builtins
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local import deployment_workflow
from nix_darwin_local.deployment_workflow import Workflow
from nix_darwin_local.promotion_plan import load_plan
from nix_darwin_local import promotion_qualification
from nix_darwin_local import promotion_workflow


pytestmark = pytest.mark.skipif(
    shutil.which("nix") is None,
    reason="installed Nix is genuinely unavailable",
)


def _run(cwd: Path, *argv: str) -> str:
    result = subprocess.run(
        list(argv), cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    return result.stdout.strip()


def _init_work_repository(path: Path) -> None:
    path.mkdir()
    _run(path, "git", "init", "--initial-branch=main")
    _run(path, "git", "config", "user.name", "Promotion Fixture")
    _run(path, "git", "config", "user.email", "promotion-fixture@example.invalid")


def _commit(path: Path, message: str) -> str:
    _run(path, "git", "add", "-A")
    _run(path, "git", "commit", "-m", message)
    return _run(path, "git", "rev-parse", "HEAD")


def _write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def _qualifier_script(host_name: str = "fixture-host") -> str:
    return r'''#!/usr/bin/env python3
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

parser = argparse.ArgumentParser()
parser.add_argument("--composition", required=True)
parser.add_argument("--input", required=True)
parser.add_argument("--candidate", required=True)
parser.add_argument("--json", action="store_true")
parser.add_argument("--timeout-seconds")
args = parser.parse_args()
root = Path(args.composition)

def tree_identity():
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".git" not in path.relative_to(root).parts:
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()

parsed = urlsplit(args.candidate)
revision = parse_qs(parsed.query).get("rev", [None])[0]
repository_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
lock_before = (root / "flake.lock").read_bytes()
tree_before = tree_identity()
candidate_locator = args.candidate
if parsed.scheme == "file":
    candidate_locator = "git+" + candidate_locator
metadata = subprocess.run(
    ["nix", "flake", "metadata", "--json", candidate_locator],
    cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
)
selected_revision = (
    json.loads(metadata.stdout).get("revision") if metadata.returncode == 0 else None
)
check = subprocess.run(
    [
        "nix", "flake", "check", "path:.",
        "--no-update-lock-file", "--no-write-lock-file",
        "--override-input", args.input, candidate_locator,
    ],
    cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
)
if check.returncode != 0:
    print(check.stderr, file=sys.stderr)
passed = check.returncode == 0 and selected_revision == revision
document = {
    "schema": "fixture-native-qualification-v1",
    "version": 1,
    "outcome": "pass" if passed else "fail",
    "failure": None if passed else "fixture qualification failed",
    "input": args.input,
    "candidate": {
        "url": args.candidate,
        "requestedRev": revision,
        "resolvedRev": selected_revision,
        "resolvedUrl": repository_url,
        "revMatched": selected_revision == revision,
        "urlMatched": True,
    },
    "lock": {"byteIdentical": (root / "flake.lock").read_bytes() == lock_before},
    "tree": {"byteIdentical": tree_identity() == tree_before},
    "checks": {"native-check": "pass" if passed else "fail"},
    "hosts": {
        __HOST_NAME__: {
            "status": "pass" if passed else "fail",
            "drvPath": "/nix/store/00000000000000000000000000000000-fixture.drv",
        }
    },
}
print(json.dumps(document, sort_keys=True, separators=(",", ":")))
'''.replace("__HOST_NAME__", json.dumps(host_name))


def _composition_flake(
    system: str,
    domain_url: str,
    composition_url: str,
    *,
    input_release_ref: str | None = None,
    contract_release_ref: str | None = None,
    host_name: str = "fixture-host",
    source_dependent: bool = False,
    nested_host_confusion: bool = False,
) -> str:
    input_url = "git+" + domain_url
    if input_release_ref is not None:
        input_url += "?ref=" + input_release_ref.removeprefix("refs/heads/")
    contract_version = 2 if contract_release_ref is not None else 1
    release_channels = (
        f'''
        releaseChannels = {{
          policy = "explicit-head-refs-v1";
          refs.domain-adapter = {json.dumps(contract_release_ref)};
        }};'''
        if contract_release_ref is not None
        else ""
    )
    source_binding = "      compositionSource = ./.;\n" if source_dependent else ""
    fixture_args = (
        '[ "-c" "/bin/test -f $1/files/host-payload; /bin/mkdir -p $out" '
        '"fixture" compositionSource ]'
        if source_dependent
        else '[ "-c" "/bin/mkdir -p $out" ]'
    )
    nested_host = (
        '\n      darwinConfigurations.fixture.host.system = '
        'throw "nested host attribute must not be selected";'
        if nested_host_confusion
        else ""
    )
    return f'''{{
  description = "native promotion integration fixture";
  inputs.domain-adapter.url = {json.dumps(input_url)};
  outputs = inputs:
    let
      system = {json.dumps(system)};
{source_binding}
      fixture = builtins.derivation {{
        name = "native-promotion-fixture";
        inherit system;
        builder = "/bin/sh";
        args = {fixture_args};
      }};
    in {{
      lib.promotion = {{
        schema = "fixture-native-promotion-v1";
        version = {contract_version};
        composition = {{
          name = "fixture-composition";
          repositoryUrl = {json.dumps(composition_url)};
        }};
        inherit system;
        modules = [ {{
          name = "domain-adapter";
          input = "domain-adapter";
          url = {json.dumps(domain_url)};
          checkout = "domain-adapter";
          bootstrapStrategy = "none";
          ownsMachineLocalConfiguration = false;
        }} ];
        packageSetIdentity = "/nix/store/00000000000000000000000000000000-fixture";
        hosts = [ {json.dumps(host_name)} ];
        checks = [ "native-check" ];
        qualification = {{ entryPoint = "bin/qualify"; mode = "exact-candidate"; }};
        promotableInputs = {{ rule = "direct-modules"; names = [ "domain-adapter" ]; }};{release_channels}
      }};
      checks.${{system}}.native-check = fixture;
      darwinConfigurations.{json.dumps(host_name)}.system = fixture;{nested_host}
    }};
}}
'''


def _domain_flake(child_urls: list[str]) -> str:
    inputs = "\n".join(
        f"    leaf-{index}.url = {json.dumps('git+' + url)};"
        for index, url in enumerate(child_urls, start=1)
    )
    input_block = f"  inputs = {{\n{inputs}\n  }};\n" if child_urls else ""
    return f'''{{
  description = "owned closure integration fixture";
{input_block}  outputs = {{ self, ... }}: {{ }};
}}
'''


def test_real_nix_deploy_release_channel_beats_newer_main_and_mismatch_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(promotion_workflow, "_private_state_root", lambda: state_root)

    domain_bare = tmp_path / "domain-adapter.git"
    composition_bare = tmp_path / "fixture-composition.git"
    _run(tmp_path, "git", "init", "--bare", "--initial-branch=main", str(domain_bare))
    _run(tmp_path, "git", "init", "--bare", "--initial-branch=main", str(composition_bare))
    domain_url = domain_bare.resolve().as_uri()
    composition_url = composition_bare.resolve().as_uri()

    domain = tmp_path / "domain-work"
    _init_work_repository(domain)
    _write(domain / "flake.nix", '{ outputs = { self }: {}; }\n')
    _write(domain / "payload.txt", "old\n")
    old_revision = _commit(domain, "domain old")
    _run(domain, "git", "branch", "deploy")
    _run(domain, "git", "remote", "add", "origin", domain_url)
    _run(domain, "git", "push", "-u", "origin", "main", "deploy")

    system = _run(tmp_path, "nix", "eval", "--raw", "--impure", "--expr", "builtins.currentSystem")
    composition = tmp_path / "composition-work"
    _init_work_repository(composition)
    _write(
        composition / "flake.nix",
        _composition_flake(
            system,
            domain_url,
            composition_url,
            input_release_ref="refs/heads/deploy",
            contract_release_ref="refs/heads/deploy",
            host_name="fixture.host",
            source_dependent=True,
            nested_host_confusion=True,
        ),
    )
    _write(composition / "bin/qualify", _qualifier_script("fixture.host"), 0o700)
    _write(composition / "files/host-payload", "tracked baseline payload\n")
    _write(composition / ".gitignore", "ambient-only\n")
    _commit(composition, "composition fixture")
    _run(composition, "nix", "flake", "lock")
    _commit(composition, "lock old domain revision")
    _run(composition, "git", "remote", "add", "origin", composition_url)
    _run(composition, "git", "push", "-u", "origin", "main")
    _write(composition / "ambient-only", "ignored prepare bytes\n")

    _run(domain, "git", "checkout", "deploy")
    _write(domain / "payload.txt", "selected deploy\n")
    new_revision = _commit(domain, "domain deploy release")
    _run(domain, "git", "push", "origin", "deploy")
    _run(domain, "git", "checkout", "main")
    _write(domain / "payload.txt", "newer unselected main\n")
    main_revision = _commit(domain, "domain main remains development")
    _run(domain, "git", "push", "origin", "main")
    assert old_revision != new_revision
    assert main_revision != new_revision

    source_head = _run(composition, "git", "rev-parse", "HEAD")
    source_status = _run(
        composition, "git", "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching"
    )
    source_lock = (composition / "flake.lock").read_bytes()
    domain_status = _run(
        domain, "git", "status", "--porcelain=v1", "--untracked-files=all"
    )
    domain_refs = _run(tmp_path, "git", "--git-dir", str(domain_bare), "show-ref")
    composition_refs = _run(
        tmp_path, "git", "--git-dir", str(composition_bare), "show-ref"
    )
    workflow = Workflow(CORE_ROOT)

    first_state = tmp_path / "snapshot-parent-a"
    second_state = tmp_path / "snapshot-parent-b"
    first_state.mkdir(mode=0o700)
    second_state.mkdir(mode=0o700)
    snapshot_workflow = Workflow(CORE_ROOT)
    snapshot_authority = promotion_workflow._promotion_git_identity(
        snapshot_workflow, composition
    )
    snapshot_identity = promotion_workflow._canonical_snapshot_identity(
        composition, snapshot_authority, source_lock
    )
    observed_maps = []
    for private_parent in (first_state, second_state):
        with promotion_workflow._canonical_promotion_snapshot(
            snapshot_workflow,
            composition,
            snapshot_authority,
            source_lock,
            private_parent,
        ) as snapshot:
            assert snapshot.name == "snapshot"
            assert not (snapshot / "ambient-only").exists()
            snapshot_contract = promotion_qualification.evaluate_contract(
                snapshot_workflow, snapshot, allow_file_urls=True
            )
            observed_maps.append(
                promotion_qualification.observe_host_derivations(
                    snapshot_workflow,
                    snapshot,
                    snapshot_contract,
                    label_prefix="fixture snapshot location host derivation",
                )["hosts"]
            )
    assert observed_maps[0] == observed_maps[1]
    assert snapshot_identity == promotion_workflow._canonical_snapshot_identity(
        composition, snapshot_authority, source_lock
    )
    assert snapshot_identity["selected_flake_path"] == "."
    assert snapshot_identity["materialization_policy"] == (
        "git-archive-exact-tree-bound-lock-v1"
    )
    assert not list(first_state.iterdir())
    assert not list(second_state.iterdir())

    artifact = promotion_workflow.prepare_promotion(
        workflow,
        composition,
        "domain-adapter",
        new_revision,
        tmp_path / "deploy-plan",
        allow_local_fixture=True,
    )
    loaded = load_plan(artifact.directory)

    assert loaded.document["status"] == "READY_FOR_REVIEW"
    assert loaded.document["remote_ref"] == {
        "ref": "refs/heads/deploy",
        "revision": new_revision,
    }
    node_name = loaded.document["closure_authorization"]["selectedNode"]
    before_node = loaded.document["entry_lock"]["normalized"]["nodes"][node_name]
    after_node = loaded.document["candidate_lock"]["normalized"]["nodes"][node_name]
    assert before_node["locked"]["rev"] == old_revision
    assert after_node["locked"]["rev"] == new_revision
    assert after_node["locked"]["rev"] != main_revision
    assert before_node["original"]["ref"] == "deploy"
    assert before_node["locked"]["ref"] == "deploy"
    assert after_node["original"]["ref"] == "deploy"
    assert after_node["locked"]["ref"] == "deploy"
    assert loaded.document["impact"]["classification"] == "HOST_DERIVATIONS_CHANGED"
    assert loaded.document["impact"]["activationSignal"] == "HOST_CHANGE_REVIEW_REQUIRED"
    assert loaded.document["impact"]["changedHosts"] == ["fixture.host"]
    assert loaded.document["locked_qualification"]["hosts"][0]["derivation"].endswith(".drv")
    assert any(
        record.argv == ("nix", "flake", "update", "domain-adapter")
        for record in workflow.records
    )
    assert any(
        record.argv
        == ("git", "ls-remote", domain_url, "refs/heads/deploy")
        for record in workflow.records
    )

    assert _run(composition, "git", "rev-parse", "HEAD") == source_head
    assert _run(
        composition, "git", "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching"
    ) == source_status == "!! ambient-only"
    assert (composition / "flake.lock").read_bytes() == source_lock
    assert _run(domain, "git", "status", "--porcelain=v1", "--untracked-files=all") == domain_status
    assert _run(tmp_path, "git", "--git-dir", str(domain_bare), "show-ref") == domain_refs
    assert _run(
        tmp_path, "git", "--git-dir", str(composition_bare), "show-ref"
    ) == composition_refs
    assert not list(state_root.glob("nix-update-lock-*"))
    assert not list(state_root.glob("nix-update-promotion-*"))
    assert not list(state_root.rglob("reviewed-tree.tar"))

    _write(composition / "ambient-only", "ignored apply bytes changed in place\n")
    apply_workflow = Workflow(CORE_ROOT)
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {loaded.digest}",
    )
    assert promotion_workflow.apply_promotion(
        apply_workflow, loaded.directory
    ) == 0
    published = _run(composition, "git", "rev-parse", "HEAD")
    assert published != source_head
    assert _run(composition, "git", "rev-list", "--count", f"{source_head}..HEAD") == "1"
    assert _run(
        composition,
        "git",
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        "HEAD",
    ).splitlines() == ["flake.lock"]
    assert _run(
        composition,
        "git",
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignored=matching",
    ) == source_status
    assert _run(composition, "git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}") in {
        "0\t0",
        "0 0",
    }
    assert _run(
        tmp_path, "git", "--git-dir", str(composition_bare), "rev-parse", "refs/heads/main"
    ) == published
    assert _run(tmp_path, "git", "--git-dir", str(domain_bare), "show-ref") == domain_refs
    terminal = json.loads((loaded.directory / "result.json").read_text())
    assert terminal["terminal_state"] == "PROMOTION_PUBLISHED_AWAITING_ACTIVATION"
    assert terminal["impact"] == loaded.document["impact"]
    candidate_drv = loaded.document["impact"]["candidateHosts"]["fixture.host"]
    candidate_records = [
        record
        for record in apply_workflow.records
        if record.label in {
            "locked composition host derivation fixture.host",
            "promotion immediate candidate host derivation fixture.host",
        }
    ]
    assert len(candidate_records) == 3
    assert {
        Path(record.stdout["path"]).read_text().strip()
        for record in candidate_records
    } == {candidate_drv}
    assert all(
        record.cwd.endswith("/snapshot")
        for record in candidate_records
    )
    forbidden = {"sudo", "darwin-rebuild", "brew", "nix-update"}
    assert not any(record.argv[0] in forbidden for record in apply_workflow.records)
    assert not list(state_root.glob("nix-update-promotion-*"))
    assert not list(state_root.glob("nix-update-lock-*"))

    mismatch_bare = tmp_path / "mismatch-composition.git"
    _run(tmp_path, "git", "init", "--bare", "--initial-branch=main", str(mismatch_bare))
    mismatch_url = mismatch_bare.resolve().as_uri()
    mismatch = tmp_path / "mismatch-composition-work"
    _init_work_repository(mismatch)
    _write(
        mismatch / "flake.nix",
        _composition_flake(
            system,
            domain_url,
            mismatch_url,
            input_release_ref="refs/heads/main",
            contract_release_ref="refs/heads/deploy",
        ),
    )
    _write(mismatch / "bin/qualify", _qualifier_script(), 0o700)
    _commit(mismatch, "mismatched composition fixture")
    _run(mismatch, "nix", "flake", "lock")
    _commit(mismatch, "lock main despite deploy contract")
    _run(mismatch, "git", "remote", "add", "origin", mismatch_url)
    _run(mismatch, "git", "push", "-u", "origin", "main")
    mismatch_head = _run(mismatch, "git", "rev-parse", "HEAD")
    mismatch_lock = (mismatch / "flake.lock").read_bytes()

    blocked = promotion_workflow.prepare_promotion(
        Workflow(CORE_ROOT),
        mismatch,
        "domain-adapter",
        new_revision,
        tmp_path / "mismatch-plan",
        allow_local_fixture=True,
    )
    assert blocked.document["status"] == "BLOCKED"
    assert any("release ref mismatched" in blocker for blocker in blocked.document["blockers"])
    assert not (blocked.directory / "retained-lock").exists()
    assert _run(mismatch, "git", "rev-parse", "HEAD") == mismatch_head
    assert (mismatch / "flake.lock").read_bytes() == mismatch_lock
    assert _run(
        mismatch, "git", "status", "--porcelain=v1", "--untracked-files=all"
    ) == ""
    assert not list(state_root.glob("nix-update-promotion-*"))


@pytest.mark.parametrize("explicit_main", [False, True], ids=["implicit-main", "short-main"])
def test_real_nix_version_one_main_ref_representations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    explicit_main: bool,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(promotion_workflow, "_private_state_root", lambda: state_root)

    domain_bare = tmp_path / "domain-adapter.git"
    composition_bare = tmp_path / "fixture-composition.git"
    _run(tmp_path, "git", "init", "--bare", "--initial-branch=main", str(domain_bare))
    _run(tmp_path, "git", "init", "--bare", "--initial-branch=main", str(composition_bare))
    domain_url = domain_bare.resolve().as_uri()
    composition_url = composition_bare.resolve().as_uri()

    domain = tmp_path / "domain-work"
    _init_work_repository(domain)
    _write(domain / "flake.nix", '{ outputs = { self }: {}; }\n')
    revision = _commit(domain, "domain main")
    _run(domain, "git", "remote", "add", "origin", domain_url)
    _run(domain, "git", "push", "-u", "origin", "main")

    system = _run(
        tmp_path, "nix", "eval", "--raw", "--impure", "--expr", "builtins.currentSystem"
    )
    composition = tmp_path / "composition-work"
    _init_work_repository(composition)
    _write(
        composition / "flake.nix",
        _composition_flake(
            system,
            domain_url,
            composition_url,
            input_release_ref="refs/heads/main" if explicit_main else None,
        ),
    )
    _write(composition / "bin/qualify", _qualifier_script(), 0o700)
    _commit(composition, "composition fixture")
    _run(composition, "nix", "flake", "lock")
    _commit(composition, "lock main domain revision")
    _run(composition, "git", "remote", "add", "origin", composition_url)
    _run(composition, "git", "push", "-u", "origin", "main")

    lock = json.loads((composition / "flake.lock").read_text())
    node_name = lock["nodes"][lock["root"]]["inputs"]["domain-adapter"]
    node = lock["nodes"][node_name]
    if explicit_main:
        assert node["original"]["ref"] == "main"
        assert node["locked"]["ref"] == "main"
    else:
        assert "ref" not in node["original"]
        assert node["locked"].get("ref") in (None, "main", "refs/heads/main")

    artifact = promotion_workflow.prepare_promotion(
        Workflow(CORE_ROOT),
        composition,
        "domain-adapter",
        revision,
        tmp_path / "already-selected-plan",
        allow_local_fixture=True,
    )
    loaded = load_plan(artifact.directory)
    assert loaded.document["status"] == "ALREADY_SELECTED"
    assert loaded.document["contract"]["version"] == 1
    assert loaded.document["remote_ref"] == {
        "ref": "refs/heads/main",
        "revision": revision,
    }
    assert loaded.document["impact"]["classification"] == "HOST_DERIVATIONS_IDENTICAL"
    assert not list(state_root.glob("nix-update-promotion-*"))
    assert not list(state_root.glob("nix-update-lock-*"))


def test_real_nix_composed_prepare_authorizes_added_owned_transitive_nodes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(promotion_workflow, "_private_state_root", lambda: state_root)

    child_urls: list[str] = []
    for index in range(1, 4):
        bare = tmp_path / f"leaf-{index}.git"
        _run(tmp_path, "git", "init", "--bare", "--initial-branch=main", str(bare))
        work = tmp_path / f"leaf-{index}-work"
        _init_work_repository(work)
        _write(work / "flake.nix", '{ outputs = { self }: {}; }\n')
        _commit(work, f"leaf {index}")
        url = bare.resolve().as_uri()
        _run(work, "git", "remote", "add", "origin", url)
        _run(work, "git", "push", "-u", "origin", "main")
        child_urls.append(url)

    domain_bare = tmp_path / "domain-adapter.git"
    composition_bare = tmp_path / "fixture-composition.git"
    _run(tmp_path, "git", "init", "--bare", "--initial-branch=main", str(domain_bare))
    _run(tmp_path, "git", "init", "--bare", "--initial-branch=main", str(composition_bare))
    domain_url = domain_bare.resolve().as_uri()
    composition_url = composition_bare.resolve().as_uri()

    domain = tmp_path / "domain-work"
    _init_work_repository(domain)
    _write(domain / "flake.nix", _domain_flake([]))
    old_revision = _commit(domain, "domain without owned leaves")
    _run(domain, "git", "remote", "add", "origin", domain_url)
    _run(domain, "git", "push", "-u", "origin", "main")

    system = _run(
        tmp_path, "nix", "eval", "--raw", "--impure", "--expr", "builtins.currentSystem"
    )
    composition = tmp_path / "composition-work"
    _init_work_repository(composition)
    _write(composition / "flake.nix", _composition_flake(system, domain_url, composition_url))
    _write(composition / "bin/qualify", _qualifier_script(), 0o700)
    _commit(composition, "composition fixture")
    _run(composition, "nix", "flake", "lock")
    _commit(composition, "lock domain without owned leaves")
    _run(composition, "git", "remote", "add", "origin", composition_url)
    _run(composition, "git", "push", "-u", "origin", "main")

    _write(domain / "flake.nix", _domain_flake(child_urls))
    _run(domain, "nix", "flake", "lock")
    new_revision = _commit(domain, "domain adds three owned leaves")
    _run(domain, "git", "push", "origin", "main")
    assert old_revision != new_revision

    source_head = _run(composition, "git", "rev-parse", "HEAD")
    source_status = _run(
        composition,
        "git",
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignored=matching",
    )
    source_lock = (composition / "flake.lock").read_bytes()
    output = tmp_path / "composed-plan"
    workflow = Workflow(CORE_ROOT)

    artifact = promotion_workflow.prepare_promotion(
        workflow,
        composition,
        "domain-adapter",
        new_revision,
        output,
        allow_local_fixture=True,
    )
    loaded = load_plan(artifact.directory)

    assert loaded.document["status"] == "READY_FOR_REVIEW"
    assert loaded.document["revision"] == new_revision
    authorization = loaded.document["closure_authorization"]
    assert authorization["selectedNode"] in authorization["preimageOwnedNodes"]
    assert authorization["selectedNode"] in authorization["candidateOwnedNodes"]
    assert len(authorization["addedNodes"]) >= 3
    assert set(authorization["addedNodes"]) <= set(authorization["candidateOwnedNodes"])
    assert loaded.document["lock_authorization_evidence"]["path"] == "retained-lock/delta.json"
    assert loaded.document["qualifier_result"]["summary"]["outcome"] == "pass"
    assert loaded.document["locked_qualification"]["no_activation"] is True
    assert loaded.document["locked_qualification"]["checks"] == [
        {"name": "native-check", "outcome": "pass"}
    ]
    assert loaded.document["locked_qualification"]["hosts"][0]["outcome"] == "pass"
    assert (output / "qualifier-result.json").is_file()
    assert (output / "locked-qualifier-result.json").is_file()
    assert (output / "retained-lock/flake.lock.preimage").read_bytes() == source_lock
    assert _run(composition, "git", "rev-parse", "HEAD") == source_head
    assert _run(
        composition,
        "git",
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignored=matching",
    ) == source_status
    assert (composition / "flake.lock").read_bytes() == source_lock
    assert not list(state_root.glob("nix-update-lock-*"))
    assert not list(state_root.glob("nix-update-promotion-*"))
    assert not (output / "result.json").exists()
    consumed = state_root / "promotion-consumed"
    assert not consumed.exists() or not list(consumed.glob("*.json"))
    assert any(
        record.argv == ("nix", "flake", "update", "domain-adapter")
        for record in workflow.records
    )
