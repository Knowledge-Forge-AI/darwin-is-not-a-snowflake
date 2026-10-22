# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_topology.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/config.py

from __future__ import annotations

import copy
import json
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def topology_modules(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    return (
        importlib.import_module("nix_darwin_local.promotion_topology"),
        importlib.import_module("nix_darwin_local.promotion_contract"),
        importlib.import_module("nix_darwin_local.config"),
    )


def _module(name: str, input_name: str, *, url: str | None = None) -> dict:
    return {
        "name": name,
        "input": input_name,
        "url": url or f"https://github.com/example/{input_name}.git",
        "checkout": input_name,
        "bootstrapStrategy": "none",
        "ownsMachineLocalConfiguration": False,
    }


def _base(name: str, modules: list[dict], *, schema: str | None = None) -> dict:
    names = [item["input"] for item in modules]
    return {
        "schema": schema or f"{name}-promotion-v1",
        "version": 2,
        "composition": {
            "name": name,
            "repositoryUrl": f"https://github.com/example/{name}.git",
        },
        "system": "aarch64-darwin",
        "modules": modules,
        "packageSetIdentity": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-pkgs",
        "hosts": ["TestHost"],
        "checks": ["contracts"],
        "qualification": {"entryPoint": "tools/qualify-promotion", "mode": "exact-candidate"},
        "promotableInputs": {"rule": "registered-direct-inputs-only", "names": names},
        "releaseChannels": {
            "policy": "explicit-head-refs-v1",
            "refs": {name: "refs/heads/main" for name in names},
        },
    }


def _layered(lower: dict, name: str, local: list[dict]) -> dict:
    lower_names = (
        list(lower["promotableInputs"]["names"])
        if "combinedModules" not in lower
        else list(lower["promotableInputs"]["names"])
    )
    modules = local
    names = [item["input"] for item in modules] + ["composition-nd"]
    return {
        "schema": f"{name}-promotion-v1",
        "version": 2,
        "composition": {
            "name": name,
            "repositoryUrl": f"https://github.com/example/{name}.git",
        },
        "system": lower["system"],
        "modules": modules,
        "packageSetIdentity": lower["packageSetIdentity"],
        "hosts": lower["hosts"],
        "checks": ["private-checks"],
        "qualification": {"entryPoint": "tools/qualify-promotion", "mode": "exact-candidate"},
        "promotableInputs": {
            "rule": "registered-direct-lower-composition-only",
            "names": names,
        },
        "releaseChannels": {
            "policy": "explicit-head-refs-v1",
            "refs": {name: "refs/heads/main" for name in names},
        },
        "requiredLowerPromotion": {
            "schema": lower["schema"],
            "version": lower["version"],
            "candidateInput": "composition-nd",
        },
        "lowerPromotion": lower,
        "combinedModules": lower["modules"] + modules,
        "semantics": {"nonActivation": True, "requiresExplicitLockSelection": True},
    }


def _registry(schema: str = "nix-darwin-composition-registry-v2") -> dict:
    module = {
        "name": "developer-ux",
        "input": "developer-ux-nd",
        "url": "https://github.com/example/developer-ux-nd.git",
        "checkout": "developer-ux-nd",
        "bootstrap": {"strategy": "none"},
    }
    if schema.endswith("-v2"):
        module["releaseRef"] = "refs/heads/main"
    return {"schema": schema, "modules": [module]}


@pytest.mark.parametrize("core_layout", ["root", "nested"])
def test_catalog_topology_uses_repository_identity_without_workspace_scanning(
    topology_modules, tmp_path, core_layout
):
    topology, _, config = topology_modules
    from nix_darwin_local.source_catalog import SourceCatalogEntry
    lower = _base("public-composition", [_module("domain", "domain")])
    contract = _layered(lower, "private", [_module("private-domain", "private-domain")])
    public = tmp_path / "arbitrary-public-name"
    private = tmp_path / "arbitrary-private-name"
    core = tmp_path / "arbitrary-core-name"
    expected_core = core if core_layout == "root" else core / "nix-darwin"
    for checkout in (public, private, expected_core):
        checkout.mkdir(parents=True)
        (checkout / "flake.nix").write_text("{}")
    if core_layout == "root":
        # promotion_topology prefers checkout/flake.nix then historical nested
        (core / "nix-darwin").mkdir(parents=True, exist_ok=True)
        (core / "nix-darwin" / "flake.nix").write_text("{}")
    (private / "promotion-contract.json").write_text(json.dumps(contract))
    entries = (
        SourceCatalogEntry("core", core, "https://example.invalid/core.git", "refs/heads/main"),
        SourceCatalogEntry("public-key", public, lower["composition"]["repositoryUrl"], "refs/heads/main"),
        SourceCatalogEntry("private-key", private, contract["composition"]["repositoryUrl"], "refs/heads/main"),
    )
    cfg = config.ControlConfig(
        sources=entries, workspace_root=tmp_path / "absent",
        promotion_checkouts=(("domain", tmp_path / "legacy-wrong"),),
    )
    stages = topology.resolve_campaign_stages(private, cfg, "core", "a" * 40)
    assert [item["repository"] for item in stages] == [expected_core, public, private]
    assert [item["revision"] for item in stages] == ["a" * 40, None, None]
    module_stages = topology.resolve_campaign_stages(private, cfg, "domain", "b" * 40)
    assert [item["repository"] for item in module_stages] == [public, private]
    with pytest.raises(config.ConfigError, match="catalog"):
        topology.resolve_campaign_stages(private, config.ControlConfig(sources=entries[:2]), "core", "a" * 40)


def _lock() -> dict:
    return {
        "nodes": {
            "root": {"inputs": {"composition-nd": "public-node"}},
            "public-node": {"inputs": {}},
        },
        "root": "root",
        "version": 7,
    }


def _make_flake(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "flake.nix").write_text("{}\n", encoding="utf-8")
    return path


def test_registry_reader_handles_current_v1_and_v2_shapes(topology_modules, tmp_path):
    topology, _, _ = topology_modules
    for index, schema in enumerate(
        ("nix-darwin-composition-registry-v1", "nix-darwin-composition-registry-v2")
    ):
        path = tmp_path / f"registry-{index}.json"
        path.write_text(json.dumps(_registry(schema)), encoding="utf-8")
        parsed = topology.load_registry(path)
        assert parsed.inputs == ("developer-ux-nd",)
        assert parsed.modules[0].bootstrap_strategy == "none"


def test_registry_reader_rejects_unknown_or_duplicate_module_identity(topology_modules, tmp_path):
    topology, _, _ = topology_modules
    value = _registry()
    value["modules"][0]["unexpected"] = True
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(topology.ConfigError, match="fields are invalid"):
        topology.load_registry(path)

    value = _registry()
    value["modules"].append(dict(value["modules"][0]))
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(topology.ConfigError, match="duplicate"):
        topology.load_registry(path)


def test_route_is_public_only_for_direct_public_input(topology_modules):
    topology, contracts, _ = topology_modules
    public = _base("composition-nd", [_module("domain", "domain-nd")])
    contracts.validate_contract(public)
    route = topology.route_for_input(public, "domain-nd")
    assert [(item.kind, item.name, item.input_name) for item in route] == [
        ("composition", "composition-nd", "domain-nd")
    ]


def test_layered_route_appends_private_stage_and_keeps_private_optional(topology_modules):
    topology, contracts, _ = topology_modules
    public = _base("composition-nd", [_module("domain", "domain-nd")])
    private = _layered(public, "composition-private-nd", [_module("private", "private-nd")])
    contracts.validate_contract(private)

    public_route = topology.route_for_input(private, "domain-nd")
    assert [(item.name, item.input_name) for item in public_route] == [
        ("composition-nd", "domain-nd"),
        ("composition-private-nd", "composition-nd"),
    ]
    private_route = topology.route_for_input(private, "private-nd")
    assert [(item.name, item.input_name) for item in private_route] == [
        ("composition-private-nd", "private-nd")
    ]


def test_core_route_includes_every_available_composition_layer(topology_modules):
    topology, contracts, _ = topology_modules
    public = _base("composition-nd", [_module("domain", "domain-nd")])
    private = _layered(public, "composition-private-nd", [_module("private", "private-nd")])
    contracts.validate_contract(private)
    route = topology.route_for_input(private, "core")
    assert [(item.kind, item.name, item.input_name) for item in route] == [
        ("core", "core", None),
        ("composition", "composition-nd", "core"),
        ("composition", "composition-private-nd", "composition-nd"),
    ]


def test_discover_chain_uses_workspace_child_without_requiring_private_for_public(
    topology_modules, tmp_path
):
    topology, contracts, config = topology_modules
    public = _base("composition-nd", [_module("domain", "domain-nd")])
    top = _make_flake(tmp_path / "selected public")
    core = _make_flake(tmp_path / "core")
    chain = topology.discover_chain(
        top,
        public,
        "domain-nd",
        core_dir=core,
        workspace_root=tmp_path / "workspace",
    )
    assert chain.stage_names == ("composition-nd",)
    assert chain.checkouts == (top.resolve(),)

    private = _layered(public, "composition-private-nd", [_module("private", "private-nd")])
    private_top = _make_flake(tmp_path / "workspace/private")
    public_checkout = _make_flake(tmp_path / "workspace/composition-nd")
    chain = topology.discover_chain(
        private_top,
        private,
        "domain-nd",
        core_dir=core,
        workspace_root=tmp_path / "workspace",
    )
    assert chain.checkouts == (
        public_checkout.resolve(),
        private_top.resolve(),
    )

    # The same lower checkout can be supplied explicitly through the optional
    # strict operator mapping.  No second topology document is needed.
    config_path = tmp_path / "nix-darwin.toml"
    config_path.write_text(
        "[promotion.checkouts]\n"
        '"composition-nd" = "$HOME/workspace/composition-nd"\n',
        encoding="utf-8",
    )
    parsed = config.load_control_config(config_path, operator_home=tmp_path)
    assert topology.checkouts_from_config(parsed) == {
        "composition-nd": public_checkout.resolve()
    }
    assert parsed.promotion_checkouts == (("composition-nd", public_checkout.resolve()),)


def test_strict_promotion_checkout_mapping_rejects_unknown_and_unsafe_values(
    topology_modules, tmp_path
):
    _, _, config = topology_modules
    unknown = tmp_path / "unknown.toml"
    unknown.write_text("[promotion]\nother = {}\n", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="unknown promotion key"):
        config.load_control_config(unknown, operator_home=tmp_path)

    invalid = tmp_path / "invalid.toml"
    invalid.write_text(
        "[promotion.checkouts]\n\"bad path\" = \"$HOME/workspace\"\n",
        encoding="utf-8",
    )
    with pytest.raises(config.ConfigError, match="invalid flake input path"):
        config.load_control_config(invalid, operator_home=tmp_path)


def test_discover_core_chain_requires_each_stage_checkout(topology_modules, tmp_path):
    topology, contracts, _ = topology_modules
    public = _base("composition-nd", [_module("domain", "domain-nd")])
    private = _layered(public, "composition-private-nd", [_module("private", "private-nd")])
    top = _make_flake(tmp_path / "private")
    core = _make_flake(tmp_path / "core")
    _make_flake(tmp_path / "workspace/composition-nd")
    chain = topology.discover_chain(
        top,
        private,
        "core",
        core_dir=core,
        workspace_root=tmp_path / "workspace",
    )
    assert chain.is_core_route
    assert chain.stage_names == ("core", "composition-nd", "composition-private-nd")
    assert chain.checkouts[0] == core.resolve()

    with pytest.raises(topology.ConfigError, match="missing flake.nix"):
        topology.discover_chain(
            top,
            private,
            "domain-nd",
            core_dir=core,
            workspace_root=tmp_path / "missing-workspace",
        )


def test_checkout_mapping_and_lower_checkout_defects(topology_modules, tmp_path):
    topology, _, _ = topology_modules

    with pytest.raises(topology.ConfigError, match="checkout mapping is invalid"):
        topology._checkout_mapping(123)

    with pytest.raises(topology.ConfigError, match="key is invalid"):
        topology._checkout_mapping({"": tmp_path})

    with pytest.raises(topology.ConfigError, match="must be absolute"):
        topology._checkout_mapping({"core": Path("relative/path")})

    stage = topology.PromotionStage("stage1", "composition", "core")
    with pytest.raises(topology.ConfigError, match="unresolved"):
        topology._lower_checkout(stage, {}, None)


def test_prepare_campaign_stage_routing_delegations(topology_modules, tmp_path, monkeypatch):
    topology, _, config_mod = topology_modules

    core_dir = _make_flake(tmp_path / "core")
    comp_dir = _make_flake(tmp_path / "comp")
    rev = "a" * 40

    class MockWorkflow:
        def __init__(self, d):
            self.core_dir = d
            self.records = []

    wf = MockWorkflow(core_dir)
    cfg = config_mod.ControlConfig()

    # 1. kind == "core" delegates to promotion_source.prepare_core_source
    core_calls = []

    def mock_prepare_core(workflow, repository, input_name, revision, output, config, stage):
        core_calls.append((workflow, repository, input_name, revision, output, config, stage))
        return "mocked_core_receipt"

    monkeypatch.setattr("nix_darwin_local.promotion_source.prepare_core_source", mock_prepare_core)

    stg0 = {"name": "core", "kind": "core", "repository": core_dir, "input": "core", "revision": rev}
    out0 = tmp_path / "out0"
    res0 = topology.prepare_campaign_stage(wf, core_dir, "core", rev, out0, cfg, stg0)
    assert res0 == "mocked_core_receipt"
    assert len(core_calls) == 1
    assert core_calls[0] == (wf, core_dir, "core", rev, out0, cfg, stg0)

    # 2. kind == "composition" with input == "core" delegates to promotion_workflow.prepare_promotion
    workflow_calls = []

    def mock_prepare_workflow(workflow, repository, input_name, revision, output, *, allow_local_fixture=False):
        workflow_calls.append((workflow, repository, input_name, revision, output, allow_local_fixture))
        return "mocked_native_plan"

    monkeypatch.setattr("nix_darwin_local.promotion_workflow.prepare_promotion", mock_prepare_workflow)

    stg1 = {"name": "comp", "kind": "composition", "repository": comp_dir, "input": "core", "revision": rev}
    out1 = tmp_path / "out1"
    res1 = topology.prepare_campaign_stage(wf, comp_dir, "core", rev, out1, cfg, stg1)
    assert res1 == "mocked_native_plan"
    assert len(workflow_calls) == 1
    assert workflow_calls[0] == (wf, comp_dir, "core", rev, out1, False)

    # 3. kind == "composition" with input == "other" and allow_local_fixture == True
    cfg_fixture = type("ConfigWithFixture", (), {"allow_local_fixture": True})()
    stg2 = {"name": "comp", "kind": "composition", "repository": comp_dir, "input": "other", "revision": rev}
    out2 = tmp_path / "out2"
    res2 = topology.prepare_campaign_stage(wf, comp_dir, "other", rev, out2, cfg_fixture, stg2)
    assert res2 == "mocked_native_plan"
    assert len(workflow_calls) == 2
    assert workflow_calls[1] == (wf, comp_dir, "other", rev, out2, True)


def test_resolve_campaign_stages_returns_none_when_missing(topology_modules, tmp_path):
    topology, _, config_mod = topology_modules
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    cfg = config_mod.ControlConfig()
    assert topology.resolve_campaign_stages(empty_dir, cfg, "core", "a" * 40) is None


def test_topology_route_defects(topology_modules, tmp_path):
    topology, _, config_mod = topology_modules

    contract_base = _base("comp", [_module("mod1", "input1")])

    # 1. invalid input_name
    with pytest.raises(topology.ConfigError, match="promotion route input is invalid"):
        topology.route_for_input(contract_base, "")
    with pytest.raises(topology.ConfigError, match="promotion route input is invalid"):
        topology.route_for_input(contract_base, 123)  # type: ignore

    # 2. bad composition identity
    bad_comp = dict(contract_base, composition="not dict")
    with pytest.raises(topology.ConfigError, match="composition identity is invalid"):
        topology.route_for_input(bad_comp, "input1", validate=False)

    # 3. modules not a list
    bad_modules = dict(contract_base, modules="not a list")
    with pytest.raises(topology.ConfigError, match="modules are invalid"):
        topology.route_for_input(bad_modules, "input1", validate=False)

    # 4. direct input registered more than once
    dup_modules = dict(contract_base, modules=[_module("m1", "input1"), _module("m2", "input1")])
    with pytest.raises(topology.ConfigError, match="registered more than once"):
        topology.route_for_input(dup_modules, "input1", validate=False)

    # 5. unknown input
    with pytest.raises(topology.ConfigError, match="unknown to the selected topology"):
        topology.route_for_input(contract_base, "nonexistent")

    # 6. layered defects
    layered = _layered(contract_base, "layered-comp", [_module("priv", "priv-input")])

    bad_lower = dict(layered, lowerPromotion="not dict")
    with pytest.raises(topology.ConfigError, match="lower contract is invalid"):
        topology.route_for_input(bad_lower, "input1", validate=False)

    bad_req = dict(layered, requiredLowerPromotion={})
    with pytest.raises(topology.ConfigError, match="candidate input is invalid"):
        topology.route_for_input(bad_req, "input1", validate=False)

    # Core route with lower defects
    with pytest.raises(topology.ConfigError, match="lower contract is invalid"):
        topology.route_for_input(bad_lower, "nix-darwin", validate=False)
    with pytest.raises(topology.ConfigError, match="candidate input is invalid"):
        topology.route_for_input(bad_req, "nix-darwin", validate=False)

    # input_name == lower_name directly
    stages = topology.route_for_input(layered, "composition-nd")
    assert len(stages) == 1
    assert stages[0].input_name == "composition-nd"

    # discover_chain paths not absolute
    with pytest.raises(topology.ConfigError, match="must be absolute"):
        topology.discover_chain(Path("relative"), contract_base, "input1", core_dir=tmp_path)
    with pytest.raises(topology.ConfigError, match="must be absolute"):
        topology.discover_chain(tmp_path, contract_base, "input1", core_dir=Path("relative"))

    # discover_chain lower checkout unresolved
    with pytest.raises(topology.ConfigError, match="lower composition checkout is unresolved"):
        topology.discover_chain(tmp_path, layered, "input1", core_dir=tmp_path, workspace_root=None)

    # discover_chain missing flake.nix
    with pytest.raises(topology.ConfigError, match="missing flake.nix"):
        topology.discover_chain(tmp_path, contract_base, "input1", core_dir=tmp_path)

    # discover_chain duplicate composition checkout
    (tmp_path / "flake.nix").write_text("{}\n")
    with pytest.raises(topology.ConfigError, match="duplicate composition checkout"):
        topology.discover_chain(
            tmp_path,
            layered,
            "input1",
            core_dir=tmp_path,
            configured_checkouts={"comp": tmp_path},
        )

    # checkouts mapping defects
    with pytest.raises(topology.ConfigError, match="mapping is invalid"):
        topology.checkouts_from_config(type("Cfg", (), {"promotion_checkouts": 123})())
    with pytest.raises(topology.ConfigError, match="key is invalid"):
        topology.checkouts_from_config(type("Cfg", (), {"promotion_checkouts": {123: tmp_path}})())
    with pytest.raises(topology.ConfigError, match="path must be absolute"):
        topology.checkouts_from_config(type("Cfg", (), {"promotion_checkouts": {"k": Path("relative")}})())
    with pytest.raises(topology.ConfigError, match="duplicate"):
        topology.checkouts_from_config(type("Cfg", (), {"promotion_checkouts": [("k", tmp_path), ("k", tmp_path)]})())

    # helper defects
    with pytest.raises(topology.ConfigError, match="invalid"):
        topology._safe_identity("bad name!", "label")
    with pytest.raises(topology.ConfigError, match="invalid"):
        topology._safe_identity(123, "label")
    with pytest.raises(topology.ConfigError, match="invalid"):
        topology._safe_input("bad/path!", "label")
    with pytest.raises(topology.ConfigError, match="invalid"):
        topology._safe_input(123, "label")

    assert not topology._safe_relative_path("/absolute")
    assert not topology._safe_relative_path("foo/../bar")
    assert not topology._safe_relative_path("foo/./bar")
    assert not topology._safe_relative_path("")
    assert not topology._safe_relative_path(123)

    # registry_matches_contract
    reg_path = tmp_path / "reg.json"
    reg_path.write_text(json.dumps(_registry()), encoding="utf-8")
    reg = topology.load_registry(reg_path)
    with pytest.raises(topology.ConfigError, match="modules are invalid"):
        topology.registry_matches_contract(reg, {"modules": "invalid"})
    assert not topology.registry_matches_contract(reg, {"modules": []})
    assert not topology.registry_matches_contract(reg, {"modules": ["not dict"]})
    assert not topology.registry_matches_contract(reg, {"modules": [{"input": "wrong", "name": "wrong"}]})
    assert not topology.registry_matches_contract(
        reg,
        {"modules": [{"input": reg.inputs[0], "name": "different", "url": reg.modules[0].url}]},
    )
    assert not topology.registry_matches_contract(
        reg,
        {"modules": [{"input": reg.inputs[0], "name": reg.modules[0].name, "url": "not a url :::"}]},
    )
