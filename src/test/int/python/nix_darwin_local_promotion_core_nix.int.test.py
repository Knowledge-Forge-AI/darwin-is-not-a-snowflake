# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_topology.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_plan.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_workflow.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_binding.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_core.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_core_qualification.py

from __future__ import annotations

import builtins
import importlib.util
import json
import os
import shutil
import sys
import types
from pathlib import Path

import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local import deployment_workflow
from nix_darwin_local import promotion_topology
from nix_darwin_local import promotion_workflow
from nix_darwin_local.deployment_workflow import Workflow
from nix_darwin_local.promotion_plan import load_plan

# Reference existing real Nix integration helper functions
INT_HELPER_PATH = Path(__file__).resolve().parent / "nix_darwin_local_promotion.int.test.py"
_helper_spec = importlib.util.spec_from_file_location(
    "nix_darwin_local_promotion_int", INT_HELPER_PATH
)
assert _helper_spec is not None and _helper_spec.loader is not None
_helper_mod = importlib.util.module_from_spec(_helper_spec)
_helper_spec.loader.exec_module(_helper_mod)

_run = _helper_mod._run
_init_work_repository = _helper_mod._init_work_repository
_commit = _helper_mod._commit
_write = _helper_mod._write


pytestmark = pytest.mark.skipif(
    shutil.which("nix") is None,
    reason="installed Nix is genuinely unavailable",
)


def _core_subflake_content() -> str:
    return """{
  description = "core subflake fixture";
  outputs = { self }: {
    payload = "core-fixture-root";
  };
}
"""


def _domain_flake_content() -> str:
    return """{
  description = "domain adapter fixture";
  outputs = { self }: { };
}
"""


def _qualifier_script_content() -> str:
    return """#!/bin/sh
exit 0
"""


def _composition_flake_content(
    system: str,
    core_url: str,
    domain_url: str,
    composition_url: str,
    *,
    core_layout: str = "nested",
) -> str:
    core_selector = "?dir=nix-darwin" if core_layout == "nested" else ""
    return f"""{{
  description = "composition fixture with core subflake and two hosts";
  inputs.core.url = "git+{core_url}{core_selector}";
  inputs.domain-adapter.url = "git+{domain_url}";
  outputs = inputs:
    let
      system = {json.dumps(system)};
      hostA = builtins.derivation {{
        name = "host-a";
        inherit system;
        builder = "/bin/sh";
        args = [ "-c" "/bin/mkdir -p $out && echo ${{inputs.core.outPath}} > $out/rev" ];
      }};
      hostB = builtins.derivation {{
        name = "host-b";
        inherit system;
        builder = "/bin/sh";
        args = [ "-c" "/bin/mkdir -p $out && echo static > $out/static" ];
      }};
      checkA = builtins.derivation {{
        name = "check-a";
        inherit system;
        builder = "/bin/sh";
        args = [ "-c" "/bin/mkdir -p $out" ];
      }};
      checkB = builtins.derivation {{
        name = "check-b";
        inherit system;
        builder = "/bin/sh";
        args = [ "-c" "/bin/mkdir -p $out" ];
      }};
    in {{
      lib.promotion = {{
        schema = "fixture-native-promotion-v1";
        version = 1;
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
        hosts = [ "host-a" "host-b" ];
        checks = [ "check-a" "check-b" ];
        qualification = {{ entryPoint = "bin/qualify"; mode = "exact-candidate"; }};
        promotableInputs = {{ rule = "direct-modules"; names = [ "domain-adapter" ]; }};
      }};
      checks.${{system}} = {{
        check-a = checkA;
        check-b = checkB;
      }};
      darwinConfigurations.host-a.system = hostA;
      darwinConfigurations.host-b.system = hostB;
    }};
}}
"""


@pytest.mark.parametrize("core_layout", ["root", "nested"])
def test_real_nix_core_promotion_e2e(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, core_layout: str
) -> None:
    # 1. Private state root isolation
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(promotion_workflow, "_private_state_root", lambda: state_root)
    # Ambient host mutation processes do not own these disposable repositories.
    monkeypatch.setattr(promotion_workflow, "_guard_processes", lambda *_: None)

    # 2. Disposable core repository (root or nested nix-darwin subflake)
    core_bare = tmp_path / "core.git"
    _run(tmp_path, "git", "init", "--bare", "--initial-branch=main", str(core_bare))
    core_url = core_bare.resolve().as_uri()

    core_work = tmp_path / "core-work"
    _init_work_repository(core_work)
    core_target_dir = core_work if core_layout == "root" else core_work / "nix-darwin"
    _write(core_target_dir / "flake.nix", _core_subflake_content())
    _write(core_target_dir / "payload.txt", "core-old-payload\n")
    old_core_rev = _commit(core_work, f"initial core commit ({core_layout})")
    _run(core_work, "git", "remote", "add", "origin", core_url)
    _run(core_work, "git", "push", "-u", "origin", "main")

    # 3. Disposable domain adapter repository (registered module input)
    domain_bare = tmp_path / "domain-adapter.git"
    _run(tmp_path, "git", "init", "--bare", "--initial-branch=main", str(domain_bare))
    domain_url = domain_bare.resolve().as_uri()

    domain_work = tmp_path / "domain-work"
    _init_work_repository(domain_work)
    _write(domain_work / "flake.nix", _domain_flake_content())
    _commit(domain_work, "initial domain adapter commit")
    _run(domain_work, "git", "remote", "add", "origin", domain_url)
    _run(domain_work, "git", "push", "-u", "origin", "main")

    # 4. Disposable composition repository
    system = _run(tmp_path, "nix", "eval", "--raw", "--impure", "--expr", "builtins.currentSystem")
    comp_bare = tmp_path / "fixture-composition.git"
    _run(tmp_path, "git", "init", "--bare", "--initial-branch=main", str(comp_bare))
    comp_url = comp_bare.resolve().as_uri()

    comp_work = tmp_path / "composition-work"
    _init_work_repository(comp_work)
    _write(
        comp_work / "flake.nix",
        _composition_flake_content(system, core_url, domain_url, comp_url, core_layout=core_layout),
    )
    _write(comp_work / "bin" / "qualify", _qualifier_script_content(), 0o755)
    _commit(comp_work, "initial composition commit")

    # Lock baseline composition
    _run(comp_work, "nix", "flake", "lock")
    _commit(comp_work, "lock old core and domain adapter")
    _run(comp_work, "git", "remote", "add", "origin", comp_url)
    _run(comp_work, "git", "push", "-u", "origin", "main")

    source_head = _run(comp_work, "git", "rev-parse", "HEAD")
    source_lock = (comp_work / "flake.lock").read_bytes()

    # 5. Advance core to new revision
    _write(core_target_dir / "payload.txt", "core-new-payload\n")
    new_core_rev = _commit(core_work, f"advance core ({core_layout})")
    _run(core_work, "git", "push", "origin", "main")
    assert old_core_rev != new_core_rev

    # 6. Prepare campaign stage for core input via actual production entrypoint
    workflow = Workflow(CORE_ROOT)
    plan_output = tmp_path / "core-promotion-plan"
    stage_spec = {
        "name": "fixture-composition",
        "kind": "composition",
        "input": "core",
    }
    config = types.SimpleNamespace(allow_local_fixture=True)

    artifact = promotion_topology.prepare_campaign_stage(
        workflow,
        comp_work,
        "core",
        new_core_rev,
        plan_output,
        config,
        stage_spec,
    )

    # 7. Production plan loader
    loaded = load_plan(artifact.directory)
    assert loaded.digest == artifact.digest
    assert (
        loaded.document["status"] == "READY_FOR_REVIEW"
    ), f"Plan status is {loaded.document.get('status')}: {loaded.document.get('blockers')}"
    assert loaded.document["input"] == "core"
    assert loaded.document["revision"] == new_core_rev
    assert loaded.document["closure_authorization"]["schema"] == "nix-update-core-lock-transition-v1"
    assert loaded.document["closure_authorization"]["subflake"] == (
        "" if core_layout == "root" else "nix-darwin"
    )
    assert loaded.document["remote_ref"] == {
        "ref": "refs/heads/main",
        "revision": new_core_rev,
    }
    assert loaded.document["impact"]["classification"] == "HOST_DERIVATIONS_CHANGED"
    assert "host-a" in loaded.document["impact"]["changedHosts"]
    assert "host-b" not in loaded.document["impact"]["changedHosts"]
    assert loaded.document["no_activation"] is True

    # Tree and lock in composition work repo must remain unchanged before apply
    assert _run(comp_work, "git", "rev-parse", "HEAD") == source_head
    assert (comp_work / "flake.lock").read_bytes() == source_lock

    # 8. Apply promotion confirmation
    apply_workflow = Workflow(CORE_ROOT)
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt: f"APPLY NIX-UPDATE PROMOTION {loaded.digest}",
    )
    apply_result = promotion_workflow.apply_promotion(apply_workflow, loaded.directory)
    assert apply_result == 0

    # 9. Verify post-apply publication: one flake.lock commit and fast-forward push
    published_head = _run(comp_work, "git", "rev-parse", "HEAD")
    assert published_head != source_head
    assert _run(comp_work, "git", "rev-list", "--count", f"{source_head}..HEAD") == "1"
    diff_tree = _run(
        comp_work,
        "git",
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        "HEAD",
    ).splitlines()
    assert diff_tree == ["flake.lock"]

    # Remote bare tracking matches published head (FF push)
    remote_head = _run(
        tmp_path, "git", "--git-dir", str(comp_bare), "rev-parse", "refs/heads/main"
    )
    assert remote_head == published_head

    # Result document verification
    result_path = loaded.directory / "result.json"
    assert result_path.is_file()
    terminal = json.loads(result_path.read_text(encoding="utf-8"))
    assert terminal["terminal_state"] == "PROMOTION_PUBLISHED_AWAITING_ACTIVATION"
    assert terminal["no_activation"] is True
    assert terminal["published_commit"] == published_head

    # Verify lock selector structure: root layout omits dir selector, nested includes it
    final_lock = json.loads((comp_work / "flake.lock").read_text(encoding="utf-8"))
    core_node = final_lock["nodes"][final_lock["nodes"][final_lock["root"]]["inputs"]["core"]]
    if core_layout == "root":
        assert "dir" not in core_node.get("original", {})
        assert "dir" not in core_node.get("locked", {})
    else:
        assert core_node.get("original", {}).get("dir") == "nix-darwin"
        assert core_node.get("locked", {}).get("dir") == "nix-darwin"

    # 10. Consumed recovery idempotent re-run
    recovery_workflow = Workflow(CORE_ROOT)
    assert promotion_workflow.apply_promotion(recovery_workflow, loaded.directory) == 0
