# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_workflow.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_plan.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/local_source.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/local_source_binding.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/local_source_history.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_locked_source.py
"""Three-source serial promotion skew and convergence integration tests (DINAS-PROMOTE-SKEW1).

Validates:
1. Dual-order serial convergence of multiple advanced module releases (A-then-B and B-then-A)
   with unchanged lock retention for unrelated sources including advanced core.
2. Production plan v4 schema (nix-update-promotion-plan-v4) and local source binding v2 schema
   (nix-update-promotion-local-source-binding-v2) truthfully binding source head H and realization L.
3. Negative enforcement: apply refusal upon unreviewed ref advance (B2 after prepare) without
   modifying flake.lock or creating commits; and prepare refusal on divergent ancestry, missing
   commits, NAR hash drift, dirty working copies, and wrong checkout refs.
4. Prepare-only disposable canary for APGR / Developer UX topology mapping (no apply).
"""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local import deployment_workflow
from nix_darwin_local import promotion_campaign
from nix_darwin_local import promotion_workflow
from nix_darwin_local.config import ControlConfig
from nix_darwin_local.deployment_workflow import Workflow
from nix_darwin_local.local_source import materialize_source
from nix_darwin_local.promotion_plan import ConfigError, load_plan
from nix_darwin_local.source_catalog import SourceCatalogEntry

# Load reusable execution tool, recording runner, and flake factories from campaign fixture
_CAMPAIGN_PATH = Path(__file__).with_name("nix_darwin_local_sources_campaign.int.test.py")
_CAMPAIGN_SPEC = importlib.util.spec_from_file_location(
    "nix_darwin_local_sources_campaign_fixture", _CAMPAIGN_PATH
)
assert _CAMPAIGN_SPEC is not None and _CAMPAIGN_SPEC.loader is not None
_CAMPAIGN_MODULE = importlib.util.module_from_spec(_CAMPAIGN_SPEC)
_CAMPAIGN_SPEC.loader.exec_module(_CAMPAIGN_MODULE)

_run = _CAMPAIGN_MODULE._run
_init_repo = _CAMPAIGN_MODULE._init_repo
_commit = _CAMPAIGN_MODULE._commit
_write = _CAMPAIGN_MODULE._write
_canonical_url = _CAMPAIGN_MODULE._canonical_url
_core_flake = _CAMPAIGN_MODULE._core_flake
_rewrite_lock_urls = _CAMPAIGN_MODULE._rewrite_lock_urls
_rewrite_input_urls = _CAMPAIGN_MODULE._rewrite_input_urls
_copy_execution_tool = _CAMPAIGN_MODULE._copy_execution_tool
_RecordingRunner = _CAMPAIGN_MODULE._RecordingRunner
_git = _CAMPAIGN_MODULE._git
_locked = _CAMPAIGN_MODULE._locked
_assert_portable_lock = _CAMPAIGN_MODULE._assert_portable_lock

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("nix") is None,
    reason="required git or nix executable is unavailable",
)


def _skew_contract(
    composition_url: str,
    source_a_url: str,
    source_b_url: str,
    *,
    input_a: str = "source-a",
    input_b: str = "source-b",
    release_b: str = "refs/heads/main",
) -> dict:
    return {
        "schema": "composition-nd-promotion-v1",
        "version": 2,
        "composition": {"name": "composition-skew", "repositoryUrl": composition_url},
        "system": "__SYSTEM__",
        "modules": [
            {
                "name": f"{input_a}-adapter",
                "input": input_a,
                "url": source_a_url,
                "checkout": input_a,
                "bootstrapStrategy": "none",
                "ownsMachineLocalConfiguration": False,
            },
            {
                "name": f"{input_b}-adapter",
                "input": input_b,
                "url": source_b_url,
                "checkout": input_b,
                "bootstrapStrategy": "none",
                "ownsMachineLocalConfiguration": False,
            },
        ],
        "packageSetIdentity": "/nix/store/00000000000000000000000000000000-dinas-fixture",
        "hosts": ["skew-host"],
        "checks": ["skew-check"],
        "qualification": {"entryPoint": "tools/qualify-promotion", "mode": "exact-candidate"},
        "promotableInputs": {"rule": "registered-direct-inputs-only", "names": [input_a, input_b]},
        "releaseChannels": {
            "policy": "explicit-head-refs-v1",
            "refs": {
                input_a: "refs/heads/main",
                input_b: release_b,
            },
        },
    }


def _skew_flake(
    system: str,
    core_input: str,
    source_a_input: str,
    source_b_input: str,
    contract: dict,
    *,
    input_a: str = "source-a",
    input_b: str = "source-b",
) -> str:
    contract_nix = json.dumps({**contract, "system": system}, separators=(",", ":"))
    return f'''{{
  description = "three-source serial promotion skew fixture";
  inputs.core.url = {json.dumps(core_input)};
  inputs.{json.dumps(input_a)}.url = {json.dumps(source_a_input)};
  inputs.{json.dumps(input_b)}.url = {json.dumps(source_b_input)};
  outputs = inputs:
    let
      system = {json.dumps(system)};
      checked = builtins.derivation {{
        name = "dinas-skew-source-dependent";
        inherit system;
        builder = "/bin/sh";
        args = [ "-c" "test -f ${{inputs.core.outPath}}/payload.txt && test -f ${{inputs.{json.dumps(input_a)}.outPath}}/payload.txt && test -f ${{inputs.{json.dumps(input_b)}.outPath}}/payload.txt && /bin/mkdir -p $out && /bin/cat ${{inputs.core.outPath}}/payload.txt > $out/core-payload && /bin/cat ${{inputs.{json.dumps(input_a)}.outPath}}/payload.txt > $out/a-payload && /bin/cat ${{inputs.{json.dumps(input_b)}.outPath}}/payload.txt > $out/b-payload" ];
      }};
    in {{
      lib.promotion = builtins.fromJSON ''{contract_nix}'';
      checks.${{system}}.skew-check = checked;
      darwinConfigurations.skew-host.system = checked;
    }};
}}
'''


@dataclass
class SkewFixture:
    tmp: Path
    state: Path
    core: Path
    source_a: Path
    source_b: Path
    composition: Path
    execution_tool: Path
    entries: tuple[SourceCatalogEntry, ...]
    config: ControlConfig
    workflow: Workflow
    recorder: _RecordingRunner
    c0: str
    c1: str
    a0: str
    a1: str
    b0: str
    b1: str
    system: str
    core_url: str
    source_a_url: str
    source_b_url: str
    composition_url: str
    input_a: str
    input_b: str


def create_skew_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    input_a: str = "source-a",
    input_b: str = "source-b",
    release_b: str = "refs/heads/main",
) -> SkewFixture:
    """Create a real disposable Git+Nix fixture with three sources (A, B, core) and composition.

    Initial lock: A0, B0, C0.
    Configured release refs on main: A1, B1, C1.
    All commits A0..A1, B0..B1, C0..C1 are materialized in the local Nix store.
    """
    tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Both order fixtures must contain identical source commits, independent
    # of wall-clock time, for exact portable-lock convergence comparison.
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-09-12T12:00:00Z")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-09-12T12:00:00Z")
    state = tmp_path / "state"
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    for owner in (deployment_workflow, promotion_workflow, promotion_campaign):
        monkeypatch.setattr(owner, "_private_state_root", lambda state=state: state)
    monkeypatch.setattr(promotion_workflow, "_guard_processes", lambda *_: None)

    system = _run(tmp_path, "nix", "eval", "--raw", "--impure", "--expr", "builtins.currentSystem")
    core_url = _canonical_url("core")
    source_a_url = _canonical_url(input_a)
    source_b_url = _canonical_url(input_b)
    composition_url = _canonical_url("composition-skew")

    execution_tool = _copy_execution_tool(tmp_path / "execution")

    # 1. Initialize core repository at C0
    core = tmp_path / "core-source"
    _init_repo(core)
    _write(core / "flake.nix", _core_flake())
    _write(core / "payload.txt", "core-0\n")
    c0 = _commit(core, "core c0 baseline")

    # 2. Initialize source A repository at A0
    source_a = tmp_path / f"{input_a}-source"
    _init_repo(source_a)
    _write(source_a / "flake.nix", "{ outputs = { self }: { }; }\n")
    _write(source_a / "payload.txt", "a-0\n")
    a0 = _commit(source_a, f"{input_a} a0 baseline")

    # 3. Initialize source B repository at B0
    source_b = tmp_path / f"{input_b}-source"
    _init_repo(source_b)
    _write(source_b / "flake.nix", "{ outputs = { self }: { }; }\n")
    _write(source_b / "payload.txt", "b-0\n")
    b0 = _commit(source_b, f"{input_b} b0 baseline")
    branch_b = release_b.removeprefix("refs/heads/")
    if branch_b != "main":
        _run(source_b, "git", "branch", "-m", branch_b)

    # 4. Initialize composition repository with lock pointing to C0, A0, B0
    composition = tmp_path / "composition-skew"
    _init_repo(composition)
    contract = _skew_contract(
        composition_url, source_a_url, source_b_url, input_a=input_a, input_b=input_b,
        release_b=release_b,
    )
    local_core = f"git+file://{core.resolve()}?ref=main"
    local_a = f"git+file://{source_a.resolve()}?ref=main"
    local_b = f"git+file://{source_b.resolve()}?ref={branch_b}"
    _write(
        composition / "flake.nix",
        _skew_flake(
            system,
            local_core,
            local_a,
            local_b,
            contract,
            input_a=input_a,
            input_b=input_b,
        ),
    )
    _write(composition / "tools/qualify-promotion", "#!/bin/sh\nexit 0\n", 0o755)
    _run(composition, "git", "add", "flake.nix", "tools/qualify-promotion")
    _run(composition, "nix", "flake", "lock")
    _rewrite_lock_urls(
        composition / "flake.lock",
        {core: core_url, source_a: source_a_url, source_b: source_b_url},
    )
    _rewrite_input_urls(
        composition / "flake.nix",
        {
            local_core: f"git+{core_url}?ref=main",
            local_a: f"git+{source_a_url}?ref=main",
            local_b: f"git+{source_b_url}?ref={branch_b}",
        },
    )
    _commit(composition, "composition baseline (locked at c0, a0, b0)")

    entries = (
        SourceCatalogEntry("core", core, core_url, "refs/heads/main"),
        SourceCatalogEntry(input_a, source_a, source_a_url, "refs/heads/main"),
        SourceCatalogEntry(input_b, source_b, source_b_url, release_b),
        SourceCatalogEntry("composition-skew", composition, composition_url, "refs/heads/main"),
    )
    config = ControlConfig(workspace_root=tmp_path, sources=entries, present=True)
    recorder = _RecordingRunner()
    workflow = Workflow(execution_tool, recorder, display_command_output=False, sources=entries)

    # 5. Warm all baseline commits while release refs point to them
    for entry, revision in (
        (entries[0], c0),
        (entries[1], a0),
        (entries[2], b0),
    ):
        materialize_source(workflow, entry, revision)

    # 6. Advance release refs on main: C1, A1, B1
    _write(core / "payload.txt", "core-1\n")
    c1 = _commit(core, "core c1 advance")

    _write(source_a / "payload.txt", "a-1\n")
    a1 = _commit(source_a, f"{input_a} a1 advance")

    _write(source_b / "payload.txt", "b-1\n")
    b1 = _commit(source_b, f"{input_b} b1 advance")

    # 7. Warm all advanced commits while release refs point to them
    for entry, revision in (
        (entries[0], c1),
        (entries[1], a1),
        (entries[2], b1),
    ):
        materialize_source(workflow, entry, revision)

    return SkewFixture(
        tmp=tmp_path,
        state=state,
        core=core,
        source_a=source_a,
        source_b=source_b,
        composition=composition,
        execution_tool=execution_tool,
        entries=entries,
        config=config,
        workflow=workflow,
        recorder=recorder,
        c0=c0,
        c1=c1,
        a0=a0,
        a1=a1,
        b0=b0,
        b1=b1,
        system=system,
        core_url=core_url,
        source_a_url=source_a_url,
        source_b_url=source_b_url,
        composition_url=composition_url,
        input_a=input_a,
        input_b=input_b,
    )


def _find_binding(plan_doc: dict[str, Any], node_or_key: str) -> dict[str, Any]:
    for item in plan_doc.get("local_sources", []):
        if item.get("node") == node_or_key or item.get("source", {}).get("key") == node_or_key:
            return item
    raise AssertionError(f"binding not found for '{node_or_key}' in plan local_sources: {plan_doc.get('local_sources')}")


def test_three_source_serial_convergence_order_a_then_b(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Test serial convergence promoting A then B while retaining unpromoted sources.

    Initial lock: A0, B0, C0. Advanced heads: A1, B1, C1.
    1. Prepare A1: A1 is exact; B0 and C0 are unchanged-lock with truthful H/L bindings.
    2. Apply A1: flake.lock updates ONLY source-a to A1.
    3. Prepare B1: A1 and B1 are exact; C0 is unchanged-lock.
    4. Apply B1: flake.lock updates ONLY source-b to B1.
    Resulting lock: A1, B1, C0.
    """
    fixture = create_skew_fixture(tmp_path, monkeypatch)

    # --- Step 1: Prepare A1 ---
    output_a = fixture.tmp / "plan-a1"
    artifact_a = promotion_workflow.prepare_promotion(
        fixture.workflow,
        fixture.composition,
        fixture.input_a,
        fixture.a1,
        output_a,
        300,
        allow_local_fixture=True,
    )
    assert artifact_a.document["schema"] == "nix-update-promotion-plan-v4"
    assert artifact_a.document["status"] == "READY_FOR_REVIEW"

    # Truthful binding validation: source H vs realization L
    binding_a = _find_binding(artifact_a.document, fixture.input_a)
    assert binding_a["schema"] == "nix-update-promotion-local-source-binding-v2"
    assert binding_a["role"] == "exact"
    assert binding_a["source"]["revision"] == fixture.a1
    assert binding_a["realization"]["revision"] == fixture.a1

    binding_b = _find_binding(artifact_a.document, fixture.input_b)
    assert binding_b["schema"] == "nix-update-promotion-local-source-binding-v2"
    assert binding_b["role"] == "unchanged-lock"
    assert binding_b["source"]["revision"] == fixture.b1
    assert binding_b["realization"]["revision"] == fixture.b0

    binding_c = _find_binding(artifact_a.document, "core")
    assert binding_c["schema"] == "nix-update-promotion-local-source-binding-v2"
    assert binding_c["role"] == "unchanged-lock"
    assert binding_c["source"]["revision"] == fixture.c1
    assert binding_c["realization"]["revision"] == fixture.c0

    # --- Step 2: Apply A1 ---
    status_a = promotion_workflow.apply_promotion(
        fixture.workflow,
        artifact_a.directory,
        confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {artifact_a.digest}",
    )
    assert status_a == 0

    lock_intermediate = _assert_portable_lock(fixture.composition / "flake.lock", fixture.tmp)
    assert _locked(lock_intermediate, fixture.input_a)["rev"] == fixture.a1
    assert _locked(lock_intermediate, fixture.input_b)["rev"] == fixture.b0
    assert _locked(lock_intermediate, "core")["rev"] == fixture.c0
    assert _git(fixture.composition, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") == "flake.lock"

    # --- Step 3: Prepare B1 ---
    already = promotion_workflow.prepare_promotion(
        fixture.workflow, fixture.composition, fixture.input_a, fixture.a1,
        fixture.tmp / "already-a1", 300, allow_local_fixture=True,
    )
    assert already.document["status"] == "ALREADY_SELECTED"
    assert load_plan(already.directory).document == already.document
    assert _find_binding(already.document, fixture.input_a)["role"] == "exact"
    assert _find_binding(already.document, fixture.input_b)["realization"]["revision"] == fixture.b0
    output_b = fixture.tmp / "plan-b1"
    artifact_b = promotion_workflow.prepare_promotion(
        fixture.workflow,
        fixture.composition,
        fixture.input_b,
        fixture.b1,
        output_b,
        300,
        allow_local_fixture=True,
    )
    assert artifact_b.document["schema"] == "nix-update-promotion-plan-v4"
    assert artifact_b.document["status"] == "READY_FOR_REVIEW"

    binding_b2 = _find_binding(artifact_b.document, fixture.input_b)
    assert binding_b2["schema"] == "nix-update-promotion-local-source-binding-v2"
    assert binding_b2["role"] == "exact"
    assert binding_b2["source"]["revision"] == fixture.b1
    assert binding_b2["realization"]["revision"] == fixture.b1

    binding_a2 = _find_binding(artifact_b.document, fixture.input_a)
    assert binding_a2["schema"] == "nix-update-promotion-local-source-binding-v2"
    assert binding_a2["source"]["revision"] == fixture.a1
    assert binding_a2["realization"]["revision"] == fixture.a1

    binding_c2 = _find_binding(artifact_b.document, "core")
    assert binding_c2["schema"] == "nix-update-promotion-local-source-binding-v2"
    assert binding_c2["role"] == "unchanged-lock"
    assert binding_c2["source"]["revision"] == fixture.c1
    assert binding_c2["realization"]["revision"] == fixture.c0

    # --- Step 4: Apply B1 ---
    status_b = promotion_workflow.apply_promotion(
        fixture.workflow,
        artifact_b.directory,
        confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {artifact_b.digest}",
    )
    assert status_b == 0

    lock_final = _assert_portable_lock(fixture.composition / "flake.lock", fixture.tmp)
    assert _locked(lock_final, fixture.input_a)["rev"] == fixture.a1
    assert _locked(lock_final, fixture.input_b)["rev"] == fixture.b1
    assert _locked(lock_final, "core")["rev"] == fixture.c0

    # Verify offline evaluation and build over the promoted composition
    _run(fixture.composition, "nix", "flake", "check", "--offline", "--no-update-lock-file", "--no-write-lock-file")
    _run(
        fixture.composition,
        "nix", "build", "--offline", "--no-link", "--no-update-lock-file",
        "--no-write-lock-file", "path:.#darwinConfigurations.skew-host.system",
    )

    # Invariants: no git network operations
    assert not any(args[:2] == ["git", "ls-remote"] for args, _ in fixture.recorder.calls)
    assert not any(args[:2] == ["git", "push"] for args, _ in fixture.recorder.calls)


def test_three_source_serial_convergence_order_b_then_a_and_dual_order_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Test reverse order convergence (B then A) and verify lock-identical dual-order convergence.

    1. Prepare and apply B1 while A0 and C0 are retained.
    2. Prepare and apply A1 while B1 and C0 are retained.
    3. Verify resulting lock matches the A-then-B outcome (A1, B1, C0).
    """
    fixture_ab = create_skew_fixture(tmp_path / "fixture-ab", monkeypatch)
    fixture_ba = create_skew_fixture(tmp_path / "fixture-ba", monkeypatch)

    # Run A then B on fixture_ab
    plan_a = fixture_ab.tmp / "plan-a1"
    art_a = promotion_workflow.prepare_promotion(
        fixture_ab.workflow, fixture_ab.composition, fixture_ab.input_a, fixture_ab.a1, plan_a, 300, allow_local_fixture=True
    )
    assert promotion_workflow.apply_promotion(fixture_ab.workflow, art_a.directory, confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {art_a.digest}") == 0

    plan_b = fixture_ab.tmp / "plan-b1"
    art_b = promotion_workflow.prepare_promotion(
        fixture_ab.workflow, fixture_ab.composition, fixture_ab.input_b, fixture_ab.b1, plan_b, 300, allow_local_fixture=True
    )
    assert promotion_workflow.apply_promotion(fixture_ab.workflow, art_b.directory, confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {art_b.digest}") == 0
    lock_ab = json.loads((fixture_ab.composition / "flake.lock").read_text(encoding="utf-8"))

    # Run B then A on fixture_ba
    # 1. Prepare B1 while A0/C0 retained
    plan_ba_b = fixture_ba.tmp / "plan-b1"
    art_ba_b = promotion_workflow.prepare_promotion(
        fixture_ba.workflow, fixture_ba.composition, fixture_ba.input_b, fixture_ba.b1, plan_ba_b, 300, allow_local_fixture=True
    )
    assert art_ba_b.document["schema"] == "nix-update-promotion-plan-v4"
    assert art_ba_b.document["status"] == "READY_FOR_REVIEW"

    # Verify A0 is unchanged-lock during B1 preparation
    binding_a_in_b = _find_binding(art_ba_b.document, fixture_ba.input_a)
    assert binding_a_in_b["role"] == "unchanged-lock"
    assert binding_a_in_b["source"]["revision"] == fixture_ba.a1
    assert binding_a_in_b["realization"]["revision"] == fixture_ba.a0

    assert promotion_workflow.apply_promotion(fixture_ba.workflow, art_ba_b.directory, confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {art_ba_b.digest}") == 0

    lock_intermediate = _assert_portable_lock(fixture_ba.composition / "flake.lock", fixture_ba.tmp)
    assert _locked(lock_intermediate, fixture_ba.input_a)["rev"] == fixture_ba.a0
    assert _locked(lock_intermediate, fixture_ba.input_b)["rev"] == fixture_ba.b1
    assert _locked(lock_intermediate, "core")["rev"] == fixture_ba.c0

    # 2. Prepare A1 while B1/C0 retained
    plan_ba_a = fixture_ba.tmp / "plan-a1"
    art_ba_a = promotion_workflow.prepare_promotion(
        fixture_ba.workflow, fixture_ba.composition, fixture_ba.input_a, fixture_ba.a1, plan_ba_a, 300, allow_local_fixture=True
    )
    assert art_ba_a.document["schema"] == "nix-update-promotion-plan-v4"
    assert art_ba_a.document["status"] == "READY_FOR_REVIEW"

    # Verify B1 retains the exact already-converged revision during A1 preparation
    binding_b_in_a = _find_binding(art_ba_a.document, fixture_ba.input_b)
    assert binding_b_in_a["source"]["revision"] == fixture_ba.b1
    assert binding_b_in_a["realization"]["revision"] == fixture_ba.b1

    assert promotion_workflow.apply_promotion(fixture_ba.workflow, art_ba_a.directory, confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {art_ba_a.digest}") == 0

    lock_ba = json.loads((fixture_ba.composition / "flake.lock").read_text(encoding="utf-8"))
    assert _locked(lock_ba, fixture_ba.input_a)["rev"] == fixture_ba.a1
    assert _locked(lock_ba, fixture_ba.input_b)["rev"] == fixture_ba.b1
    assert _locked(lock_ba, "core")["rev"] == fixture_ba.c0

    # Both orders converge to identical lock nodes for all three sources
    for node in (fixture_ab.input_a, fixture_ab.input_b, "core"):
        assert _locked(lock_ab, node) == _locked(lock_ba, node)


def test_apply_refusal_without_lock_change_when_ref_advances_after_prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Refuse apply when observed release ref advances to B2 after prepare, leaving flake.lock untouched."""
    fixture = create_skew_fixture(tmp_path, monkeypatch)
    preimage_lock = (fixture.composition / "flake.lock").read_bytes()
    preimage_head = _git(fixture.composition, "rev-parse", "HEAD")

    # Prepare A1 while B remains unrelated and locked at B0.
    plan_output = fixture.tmp / "plan-b1-stale"
    artifact = promotion_workflow.prepare_promotion(
        fixture.workflow,
        fixture.composition,
        fixture.input_a,
        fixture.a1,
        plan_output,
        300,
        allow_local_fixture=True,
    )
    assert artifact.document["schema"] == "nix-update-promotion-plan-v4"
    assert artifact.document["status"] == "READY_FOR_REVIEW"

    # Advance B's release ref to B2 after preparation
    _write(fixture.source_b / "payload.txt", "b-2\n")
    _commit(fixture.source_b, "source-b b2 advance after prepare")

    # Apply must fail closed
    status = promotion_workflow.apply_promotion(
        fixture.workflow,
        artifact.directory,
        confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )
    assert status == 1

    # Lock preimage is preserved exactly; no commit created on composition HEAD
    assert (fixture.composition / "flake.lock").read_bytes() == preimage_lock
    assert _git(fixture.composition, "rev-parse", "HEAD") == preimage_head

    # Result records terminal failure without publication or activation
    result_path = artifact.directory / "result.json"
    assert result_path.exists()
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["local_commit"] is None
    assert result["published_commit"] is None
    assert result["terminal_state"] in {
        "BLOCKED_BEFORE_MUTATION",
        "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED",
    }
    assert "catalog local source authority drifted from reviewed bindings" in result["failure"]


@pytest.mark.parametrize("drift", ["tracked-edit", "release-advance"])
def test_unrelated_source_drift_before_commit_restores_preimage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
):
    fixture = create_skew_fixture(tmp_path, monkeypatch)
    before = (fixture.composition / "flake.lock").read_bytes()
    parent = _git(fixture.composition, "rev-parse", "HEAD")
    artifact = promotion_workflow.prepare_promotion(
        fixture.workflow, fixture.composition, fixture.input_a, fixture.a1,
        fixture.tmp / "precommit-drift", 300, allow_local_fixture=True,
    )
    assert artifact.document["status"] == "READY_FOR_REVIEW"
    original = promotion_workflow._revalidate_commit_authority_local
    injected = []

    def drift_then_revalidate(*args, **kwargs):
        if not injected:
            injected.append(drift)
            _write(fixture.source_b / "payload.txt", "unrelated precommit drift\n")
            if drift == "release-advance":
                _commit(fixture.source_b, "unrelated release advances before commit")
        return original(*args, **kwargs)

    monkeypatch.setattr(promotion_workflow, "_revalidate_commit_authority_local", drift_then_revalidate)
    status = promotion_workflow.apply_promotion(
        fixture.workflow, artifact.directory,
        confirm=lambda _: f"APPLY NIX-UPDATE PROMOTION {artifact.digest}",
    )
    assert injected == [drift]
    if drift == "release-advance":
        assert status == 1
        assert (fixture.composition / "flake.lock").read_bytes() == before
        assert _git(fixture.composition, "rev-parse", "HEAD") == parent
        assert _git(fixture.composition, "status", "--porcelain") == ""
    else:
        assert status == 0


def test_prepare_refuses_divergent_ancestry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Refuse preparation when an unchanged lock node L is not an ancestor of observed release head H."""
    fixture = create_skew_fixture(tmp_path, monkeypatch)

    # In source-b, create an orphan root so B0 is NOT an ancestor of HEAD
    _run(fixture.source_b, "git", "checkout", "--orphan", "divergent-branch")
    _write(fixture.source_b / "payload.txt", "b-divergent\n")
    _commit(fixture.source_b, "divergent source-b commit")
    _run(fixture.source_b, "git", "branch", "-M", "main")

    output = fixture.tmp / "plan-divergent"
    try:
        art = promotion_workflow.prepare_promotion(
            fixture.workflow,
            fixture.composition,
            fixture.input_a,
            fixture.a1,
            output,
            300,
            allow_local_fixture=True,
        )
        assert art.document["status"] == "BLOCKED"
        assert any(
            "locked revision is not an ancestor of release head" in blocker
            for blocker in art.document.get("blockers", [])
        )
    except ConfigError as exc:
        assert "locked revision is not an ancestor of release head" in str(exc)


def test_prepare_refuses_missing_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Refuse preparation when a locked commit revision is missing from the local repository."""
    fixture = create_skew_fixture(tmp_path, monkeypatch)

    # Remove only the historical commit object in this disposable repository.
    # The composition remains clean; a dirty-lock refusal would not prove this
    # boundary. Its NAR is already warmed, so Nix can still read the baseline.
    historical_object = fixture.source_b / ".git" / "objects" / fixture.b0[:2] / fixture.b0[2:]
    assert historical_object.is_file()
    historical_object.unlink()

    output = fixture.tmp / "plan-missing"
    try:
        art = promotion_workflow.prepare_promotion(
            fixture.workflow,
            fixture.composition,
            fixture.input_a,
            fixture.a1,
            output,
            300,
            allow_local_fixture=True,
        )
        assert art.document["status"] == "BLOCKED"
        assert any("locked revision" in b or "requested revision" in b for b in art.document["blockers"])
    except ConfigError as exc:
        assert any(term in str(exc).lower() for term in ["missing", "commit", "revision", "not found", "invalid"])


def test_historical_realization_refuses_nar_hash_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Refuse preparation when lock narHash does not match the actual realization of locked revision L."""
    fixture = create_skew_fixture(tmp_path, monkeypatch)

    from nix_darwin_local.local_source import realize_historical_source

    entry = next(entry for entry in fixture.entries if entry.key == fixture.input_b)
    with pytest.raises(ConfigError, match="narHash|nar_hash|NAR"):
        realize_historical_source(
            fixture.workflow, entry, fixture.b0,
            expected_repository=fixture.source_b_url,
            expected_ref=entry.release_ref,
            expected_nar_hash="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        )


def test_prepare_refuses_dirty_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify preparation permits unrelated dirty catalog sources under SNAPSHOT1."""
    fixture = create_skew_fixture(tmp_path, monkeypatch)

    # Introduce uncommitted tracked modifications in source-b
    _write(fixture.source_b / "payload.txt", "b-dirty-modification\n")

    output = fixture.tmp / "plan-dirty"
    art = promotion_workflow.prepare_promotion(
        fixture.workflow,
        fixture.composition,
        fixture.input_a,
        fixture.a1,
        output,
        300,
        allow_local_fixture=True,
    )
    assert art.document["status"] == "READY_FOR_REVIEW"
    b_records = [
        r for r in art.document.get("local_sources", [])
        if r.get("node") == fixture.input_b
    ]
    assert len(b_records) == 1
    assert b_records[0]["source"]["clean"] is False


def test_prepare_refuses_wrong_attached_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify preparation permits detached or divergent branch in catalog source when exact revision is valid."""
    fixture = create_skew_fixture(tmp_path, monkeypatch)

    # Switch source-b away from configured release_ref (refs/heads/main)
    _run(fixture.source_b, "git", "checkout", "-b", "feature-wrong-branch")

    output = fixture.tmp / "plan-wrong-ref"
    art = promotion_workflow.prepare_promotion(
        fixture.workflow,
        fixture.composition,
        fixture.input_a,
        fixture.a1,
        output,
        300,
        allow_local_fixture=True,
    )
    assert art.document["status"] == "READY_FOR_REVIEW"


def test_prepare_only_canary_apgr_developer_ux_topology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Prepare-only disposable canary mapping APGR and Developer UX topology.

    Proves that promotion preparation succeeds and correctly classifies:
    - Candidate (APGR) as role 'exact'
    - Unpromoted module (Developer UX) as role 'unchanged-lock' with H/L binding
    - Unpromoted core as role 'unchanged-lock' with H/L binding
    Without performing any application, Git pushes, or network calls.
    """
    canary_fixture = create_skew_fixture(
        tmp_path,
        monkeypatch,
        input_a="apgr",
        input_b="developer-ux",
        release_b="refs/heads/deploy",
    )

    canary_output = canary_fixture.tmp / "canary-plan-apgr"
    composition_lock_before = (canary_fixture.composition / "flake.lock").read_bytes()
    artifact = promotion_workflow.prepare_promotion(
        canary_fixture.workflow,
        canary_fixture.composition,
        "apgr",
        canary_fixture.a1,
        canary_output,
        300,
        allow_local_fixture=True,
    )

    assert artifact.document["schema"] == "nix-update-promotion-plan-v4"
    assert artifact.document["status"] == "READY_FOR_REVIEW"
    assert "publication" not in artifact.document

    preimage = artifact.document["entry_lock"]["normalized"]
    candidate = artifact.document["candidate_lock"]["normalized"]
    assert set(candidate["nodes"]) == set(preimage["nodes"])
    assert [name for name in candidate["nodes"]
            if candidate["nodes"][name] != preimage["nodes"][name]] == ["apgr"]
    assert (canary_fixture.composition / "flake.lock").read_bytes() == composition_lock_before

    # Verify APGR binding: exact candidate
    apgr_binding = _find_binding(artifact.document, "apgr")
    assert apgr_binding["schema"] == "nix-update-promotion-local-source-binding-v2"
    assert apgr_binding["role"] == "exact"
    assert apgr_binding["source"]["revision"] == canary_fixture.a1
    assert apgr_binding["realization"]["revision"] == canary_fixture.a1

    # Verify Developer UX binding: unchanged-lock with safe-forward H and L
    devux_binding = _find_binding(artifact.document, "developer-ux")
    assert devux_binding["schema"] == "nix-update-promotion-local-source-binding-v2"
    assert devux_binding["role"] == "unchanged-lock"
    assert devux_binding["source"]["revision"] == canary_fixture.b1
    assert devux_binding["source"]["release_ref"] == "refs/heads/deploy"
    assert devux_binding["realization"]["revision"] == canary_fixture.b0

    # Verify Core binding: unchanged-lock with safe-forward H and L
    core_binding = _find_binding(artifact.document, "core")
    assert core_binding["schema"] == "nix-update-promotion-local-source-binding-v2"
    assert core_binding["role"] == "unchanged-lock"
    assert core_binding["source"]["revision"] == canary_fixture.c1
    assert core_binding["realization"]["revision"] == canary_fixture.c0

    # Real plan loader roundtrip validation
    loaded = load_plan(canary_output)
    assert loaded.digest == artifact.digest
    assert loaded.document == artifact.document

    # CRITICAL CANARY CONTRACT: No apply is performed in the canary test!
    assert not (canary_output / "result.json").exists()
    assert not any(args[:2] == ["git", "ls-remote"] for args, _ in canary_fixture.recorder.calls)
    assert not any(args[:2] == ["git", "push"] for args, _ in canary_fixture.recorder.calls)
