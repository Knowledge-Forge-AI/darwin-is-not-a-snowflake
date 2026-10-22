from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path

import pytest


REVISION = "a" * 40
OLD_REVISION = "b" * 40


@pytest.fixture
def native_modules(repo_root: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    return (
        importlib.import_module("nix_darwin_local.promotion_contract"),
        importlib.import_module("nix_darwin_local.promotion_result"),
        importlib.import_module("nix_darwin_local.paths"),
    )


@pytest.fixture
def lock_modules(repo_root: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    return (
        importlib.import_module("nix_darwin_local.promotion_lock"),
        importlib.import_module("nix_darwin_local.paths"),
    )


def module(name: str, url: str) -> dict:
    return {
        "name": name,
        "input": name,
        "url": url,
        "checkout": name,
        "bootstrapStrategy": "none",
        "ownsMachineLocalConfiguration": False,
    }


def package_set() -> str:
    return "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-package-set"


def lock_document(revision: str = REVISION) -> dict:
    return {
        "nodes": {
            "root": {"inputs": {"domain-adapter": "domain-adapter"}},
            "domain-adapter": {
                "original": {
                    "type": "git",
                    "url": "https://github.com/example/domain-adapter.git",
                    "ref": "refs/heads/main",
                },
                "locked": {
                    "type": "git",
                    "url": "https://github.com/example/domain-adapter.git",
                    "ref": "refs/heads/main",
                    "rev": revision,
                    "narHash": f"sha256-{revision[:8]}",
                },
            },
        },
        "root": "root",
        "version": 7,
    }


def base_contract() -> dict:
    return {
        "schema": "generic-base-promotion-v1",
        "version": 1,
        "composition": {
            "name": "lower-composition",
            "repositoryUrl": "git+ssh://git@github.com/example/lower-composition.git",
        },
        "system": "aarch64-darwin",
        "modules": [
            module(
                "domain-adapter",
                "git+ssh://git@github.com/example/domain-adapter.git",
            )
        ],
        "packageSetIdentity": package_set(),
        "hosts": ["GenericHost"],
        "checks": ["lower-check"],
        "qualification": {
            "entryPoint": "tools/qualify-promotion",
            "mode": "exact-candidate",
        },
        "promotableInputs": {
            "rule": "registered-direct-inputs-only",
            "names": ["domain-adapter"],
        },
    }


def layered_contract() -> dict:
    lower = base_contract()
    local = module(
        "higher-adapter",
        "git+ssh://git@github.com/example/higher-adapter.git",
    )
    return {
        "schema": "generic-layered-promotion-v1",
        "version": 1,
        "composition": {
            "name": "higher-composition",
            "repositoryUrl": "git+ssh://git@github.com/example/higher-composition.git",
        },
        "system": lower["system"],
        "requiredLowerPromotion": {
            "schema": lower["schema"],
            "version": 1,
            "candidateInput": "lower-composition",
        },
        "lowerPromotion": lower,
        "modules": [local],
        "combinedModules": lower["modules"] + [local],
        "packageSetIdentity": copy.deepcopy(lower["packageSetIdentity"]),
        "hosts": list(lower["hosts"]),
        "checks": ["higher-check"],
        "qualification": {
            "entryPoint": "tools/qualify-promotion",
            "mode": "exact-candidate",
        },
        "promotableInputs": {
            "rule": "explicit-direct-or-lower-inputs",
            "names": ["lower-composition", "higher-adapter"],
        },
        "semantics": {
            "nonActivation": True,
            "requiresExplicitLockSelection": True,
        },
    }


def version_two(contract: dict, refs: dict[str, str] | None = None) -> dict:
    updated = copy.deepcopy(contract)
    updated["version"] = 2
    names = updated["promotableInputs"]["names"]
    updated["releaseChannels"] = {
        "policy": "explicit-head-refs-v1",
        "refs": refs or {name: f"refs/heads/release/{name}" for name in names},
    }
    return updated


def native_result(contract: dict, input_name: str, repository_url: str, *, layered: bool) -> dict:
    checks = (
        {"lower": {"lower-check": "pass"}, "higher": {"higher-check": "pass"}}
        if layered else {"lower-check": "pass"}
    )
    result = {
        "schema": "generic-layered-result-v1" if layered else "generic-base-result-v1",
        "input": input_name,
        "candidate": {
            "url": f"{repository_url}?rev={REVISION}",
            "requestedRev": REVISION,
            "resolvedRev": REVISION,
            "resolvedUrl": "ssh://git@github.com/example/" + repository_url.rsplit("/", 1)[1],
            "revMatched": True,
            "urlMatched": True,
        },
        "checks": checks,
        "hosts": {
            "GenericHost": {
                "status": "pass",
                "drvPath": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-generic-host.drv",
            }
        },
        "lock": {"byteIdentical": True},
        "tree": {"byteIdentical": True},
        "outcome": "pass",
        "failure": None,
    }
    if layered:
        result.update({
            "version": 1,
            "contractCompatibility": {"status": "pass", "diffPaths": []},
            "snapshots": {"valid": True, "byteIdentical": True, "pre": {}, "post": {}},
        })
    return result


def test_base_and_layered_contracts_are_selected_structurally(native_modules):
    contract_module, _, _ = native_modules
    base = base_contract()
    layered = layered_contract()

    assert contract_module.validate_contract(base) is base
    assert contract_module.contract_dialect(base) == "base"
    assert contract_module.validate_contract(layered) is layered
    assert contract_module.contract_dialect(layered) == "layered"
    direct = contract_module.resolve_contract_input(layered, "higher-adapter")
    lower = contract_module.resolve_contract_input(layered, "lower-composition")
    assert direct["rule"] == "direct-module-url"
    assert direct["module"] == "higher-adapter"
    assert lower == {
        "rule": "embedded-lower-composition-url",
        "module": "lower-composition",
        "repository_url": base["composition"]["repositoryUrl"],
    }


def test_version_one_contracts_remain_byte_compatible_and_imply_main(native_modules):
    contract_module, _, _ = native_modules
    for contract in (base_contract(), layered_contract()):
        before = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
        assert contract_module.validate_contract(contract) is contract
        assert json.dumps(contract, sort_keys=True, separators=(",", ":")).encode() == before
        for name in contract["promotableInputs"]["names"]:
            assert contract_module.contract_release_ref(contract, name) == "refs/heads/main"


def test_version_two_base_and_layered_contracts_bind_exact_release_channels(native_modules):
    contract_module, _, _ = native_modules
    base = version_two(base_contract(), {"domain-adapter": "refs/heads/deploy"})
    layered = version_two(
        layered_contract(),
        {
            "lower-composition": "refs/heads/stable",
            "higher-adapter": "refs/heads/release/higher",
        },
    )

    assert contract_module.validate_contract(base) is base
    assert contract_module.contract_dialect(base) == "base"
    assert contract_module.contract_release_ref(base, "domain-adapter") == "refs/heads/deploy"
    assert contract_module.validate_contract(layered) is layered
    assert contract_module.contract_dialect(layered) == "layered"
    assert contract_module.contract_release_ref(layered, "lower-composition") == "refs/heads/stable"
    assert layered["lowerPromotion"]["version"] == 1
    assert contract_module.contract_release_ref(
        layered["lowerPromotion"], "domain-adapter"
    ) == "refs/heads/main"

    layered["lowerPromotion"] = version_two(
        layered["lowerPromotion"], {"domain-adapter": "refs/heads/lower-deploy"}
    )
    layered["requiredLowerPromotion"]["version"] = 2
    assert contract_module.validate_contract(layered) is layered


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["releaseChannels"].update(policy="unknown"),
        lambda value: value["releaseChannels"].update(extra=True),
        lambda value: value["releaseChannels"]["refs"].clear(),
        lambda value: value["releaseChannels"]["refs"].update(extra="refs/heads/extra"),
        lambda value: value["releaseChannels"].update(refs=[]),
    ],
)
def test_version_two_release_channel_shape_is_exact(native_modules, mutate):
    contract_module, _, paths_module = native_modules
    contract = version_two(base_contract(), {"domain-adapter": "refs/heads/deploy"})
    mutate(contract)

    with pytest.raises(paths_module.ConfigError):
        contract_module.validate_contract(contract)


@pytest.mark.parametrize(
    "release_ref",
    [
        "main",
        "refs/tags/release",
        "refs/remotes/origin/deploy",
        "refs/heads/",
        "refs/heads//deploy",
        "refs/heads/deploy/",
        "refs/heads/deploy..next",
        "refs/heads/deploy@{1}",
        "refs/heads/deploy.lock",
        "refs/heads/feature/test.lock/next",
        "refs/heads/.hidden",
        "refs/heads/trailing.",
        "refs/heads/HEAD",
        "refs/heads/@",
        "refs/heads/deploy*",
        "refs/heads/deploy?",
        "refs/heads/deploy[1]",
        "refs/heads/deploy:next",
        "refs/heads/+deploy",
        "refs/heads/deploy\\next",
        "refs/heads/deploy\nnext",
        "a" * 40,
        "HEAD",
    ],
)
def test_release_refs_reject_non_normalized_or_prohibited_forms(
    native_modules, release_ref
):
    contract_module, _, paths_module = native_modules
    contract = version_two(base_contract(), {"domain-adapter": release_ref})

    with pytest.raises(paths_module.ConfigError):
        contract_module.validate_contract(contract)


def test_release_channel_version_pairing_and_canonical_bytes_are_exact(native_modules):
    contract_module, _, paths_module = native_modules
    v1_with_channels = base_contract()
    v1_with_channels["releaseChannels"] = {
        "policy": "explicit-head-refs-v1",
        "refs": {"domain-adapter": "refs/heads/deploy"},
    }
    v2_without_channels = base_contract()
    v2_without_channels["version"] = 2
    with pytest.raises(paths_module.ConfigError):
        contract_module.validate_contract(v1_with_channels)
    with pytest.raises(paths_module.ConfigError):
        contract_module.validate_contract(v2_without_channels)

    main = version_two(base_contract(), {"domain-adapter": "refs/heads/main"})
    deploy = version_two(base_contract(), {"domain-adapter": "refs/heads/deploy"})
    assert json.dumps(main, sort_keys=True, separators=(",", ":")) != json.dumps(
        deploy, sort_keys=True, separators=(",", ":")
    )


def test_host_impact_classifies_identical_and_changed_maps(native_modules):
    _, result_module, _ = native_modules
    hosts = ["Alpha", "Beta", "Gamma"]
    baseline = {
        "Alpha": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-alpha.drv",
        "Beta": "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-beta.drv",
        "Gamma": "/nix/store/cccccccccccccccccccccccccccccccc-gamma.drv",
    }
    identical = result_module.compute_host_impact(baseline, baseline, hosts)
    assert identical == {
        "schema": "nix-update-promotion-impact-v1",
        "baselineHosts": baseline,
        "candidateHosts": baseline,
        "changedHosts": [],
        "classification": "HOST_DERIVATIONS_IDENTICAL",
        "activationSignal": "NO_HOST_CHANGE",
        "activationPerformed": False,
    }
    assert result_module.validate_impact_record(identical, hosts) == identical

    candidate = dict(baseline)
    candidate["Gamma"] = "/nix/store/dddddddddddddddddddddddddddddddd-gamma.drv"
    candidate["Alpha"] = "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-alpha.drv"
    changed = result_module.compute_host_impact(baseline, candidate, hosts)
    assert changed["changedHosts"] == ["Alpha", "Gamma"]
    assert changed["classification"] == "HOST_DERIVATIONS_CHANGED"
    assert changed["activationSignal"] == "HOST_CHANGE_REVIEW_REQUIRED"
    assert changed["activationPerformed"] is False


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["baselineHosts"].pop("GenericHost"),
        lambda value: value["candidateHosts"].update(
            Extra="/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-extra.drv"
        ),
        lambda value: value["baselineHosts"].update(GenericHost="not-a-derivation"),
        lambda value: value.update(changedHosts=["GenericHost", "GenericHost"]),
        lambda value: value.update(changedHosts=["GenericHost"]),
        lambda value: value.update(classification="HOST_DERIVATIONS_CHANGED"),
        lambda value: value.update(activationSignal="HOST_CHANGE_REVIEW_REQUIRED"),
        lambda value: value.update(activationPerformed=True),
        lambda value: value.update(extra=True),
    ],
)
def test_host_impact_rejects_malformed_or_caller_classified_evidence(
    native_modules, mutate
):
    _, result_module, paths_module = native_modules
    hosts = ["GenericHost"]
    derivations = {
        "GenericHost": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-generic.drv"
    }
    impact = result_module.compute_host_impact(derivations, derivations, hosts)
    mutate(impact)
    with pytest.raises(paths_module.ConfigError):
        result_module.validate_impact_record(impact, hosts)


def test_host_impact_duplicate_host_evidence_fails_during_strict_json_load(
    native_modules,
):
    contract_module, _, paths_module = native_modules
    duplicate_hosts = (
        '{"impact":{"baselineHosts":{'
        '"GenericHost":"/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-first.drv",'
        '"GenericHost":"/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-second.drv"'
        '}}}'
    )
    with pytest.raises(paths_module.ConfigError, match="duplicate object key"):
        contract_module.strict_json_loads(duplicate_hosts, "promotion plan fixture")


def test_host_impact_blocks_contract_host_addition_or_removal(native_modules):
    _, result_module, paths_module = native_modules
    baseline = {
        "Alpha": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-alpha.drv"
    }
    candidate = {
        "Beta": "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-beta.drv"
    }
    with pytest.raises(paths_module.ConfigError):
        result_module.compute_host_impact(baseline, candidate, ["Alpha"])


def test_layered_contract_accepts_owned_non_promotable_local_only_module(native_modules):
    contract_module, _, _ = native_modules
    contract = layered_contract()
    local_only = module("machine-overlay", contract_module.LOCAL_ONLY_MODULE_URL)
    local_only["ownsMachineLocalConfiguration"] = True
    contract["modules"].append(local_only)
    contract["combinedModules"].append(copy.deepcopy(local_only))

    assert contract_module.validate_contract(contract) is contract
    assert "machine-overlay" not in contract["promotableInputs"]["names"]


def test_layered_contract_allows_distinct_local_only_modules_across_layers(native_modules):
    contract_module, _, _ = native_modules
    contract = layered_contract()

    lower_only = module("lower-overlay", contract_module.LOCAL_ONLY_MODULE_URL)
    lower_only["ownsMachineLocalConfiguration"] = True
    contract["lowerPromotion"]["modules"].append(lower_only)

    local_modules = []
    for name in ("machine-overlay-a", "machine-overlay-b"):
        local_only = module(name, contract_module.LOCAL_ONLY_MODULE_URL)
        local_only["ownsMachineLocalConfiguration"] = True
        local_modules.append(local_only)
    contract["modules"].extend(local_modules)
    contract["combinedModules"] = (
        contract["lowerPromotion"]["modules"] + contract["modules"]
    )

    assert contract_module.validate_contract(contract) is contract


@pytest.mark.parametrize("owned,promotable", [(False, False), (True, True)])
def test_local_only_module_cannot_be_unowned_or_promotable(
    native_modules, owned, promotable
):
    contract_module, _, paths = native_modules
    contract = layered_contract()
    local_only = module("machine-overlay", contract_module.LOCAL_ONLY_MODULE_URL)
    local_only["ownsMachineLocalConfiguration"] = owned
    contract["modules"].append(local_only)
    contract["combinedModules"].append(copy.deepcopy(local_only))
    if promotable:
        contract["promotableInputs"]["names"].append("machine-overlay")

    with pytest.raises(paths.ConfigError, match="local-only"):
        contract_module.validate_contract(contract)


def test_local_only_module_is_defensively_unresolvable(native_modules):
    contract_module, _, paths = native_modules
    contract = layered_contract()
    contract["modules"][0]["url"] = contract_module.LOCAL_ONLY_MODULE_URL
    contract["modules"][0]["ownsMachineLocalConfiguration"] = True

    with pytest.raises(paths.ConfigError, match="cannot be resolved"):
        contract_module.resolve_contract_input(contract, "higher-adapter")


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda value: value.update(extra=True), "structural dialect"),
        (lambda value: value["requiredLowerPromotion"].update(schema="other"), "identity disagrees"),
        (lambda value: value.update(combinedModules=value["modules"]), "combinedModules"),
        (lambda value: value["modules"][0].update(name="domain-adapter"), "duplicate module identity"),
        (lambda value: value.update(system="x86_64-darwin"), "system disagrees"),
        (lambda value: value["semantics"].update(nonActivation=False), "non-activating"),
        (lambda value: value["promotableInputs"].update(names=["higher-adapter"]), "not promotable"),
    ],
)
def test_layered_contract_invariants_fail_closed(native_modules, mutate, message):
    contract_module, _, paths = native_modules
    value = layered_contract()
    mutate(value)
    with pytest.raises(paths.ConfigError, match=message):
        contract_module.validate_contract(value)


def test_repository_url_normalization_separates_nix_and_git_transports(native_modules, tmp_path: Path):
    contract_module, _, paths = native_modules
    nix_url = "git+ssh://git@GitHub.com/example/domain-adapter.git"
    assert contract_module.normalize_nix_repository_url(nix_url) == (
        "ssh://git@github.com/example/domain-adapter.git"
    )
    assert contract_module.normalize_git_transport_url(
        "git@github.com:example/domain-adapter.git"
    ) == "ssh://git@github.com/example/domain-adapter.git"
    assert contract_module.normalize_nix_repository_url(
        "git+https://github.com/example/domain-adapter.git"
    ) == "https://github.com/example/domain-adapter.git"
    assert contract_module.normalize_nix_repository_url(
        "ssh://git@github.com/example/domain-adapter.git"
    ) == "ssh://git@github.com/example/domain-adapter.git"
    assert contract_module.normalize_nix_repository_url(
        "https://GitHub.com/example/domain-adapter.git"
    ) == "https://github.com/example/domain-adapter.git"
    assert contract_module.repository_urls_match(
        "git+ssh://git@github.com/example/domain-adapter.git",
        "ssh://git@github.com/example/domain-adapter",
    )
    local = (tmp_path / "fixture.git").as_uri()
    with pytest.raises(paths.ConfigError, match="explicit local fixtures"):
        contract_module.normalize_nix_repository_url(local)
    assert contract_module.normalize_nix_repository_url(local, allow_file=True) == local


@pytest.mark.parametrize(
    "url",
    [
        "git+ssh://root@github.com/example/repo.git",
        "git+ssh://git:password@github.com/example/repo.git",
        "https://user@github.com/example/repo.git",
        "https://github.com/example/repo.git?dir=other",
        "https://github.com/example/repo.git#fragment",
        "https://github.com/example/repo git",
        "ssh://github.com/example/repo.git",
        "git://github.com/example/repo.git",
    ],
)
def test_repository_url_rejects_credentials_selectors_and_ambiguity(native_modules, url):
    contract_module, _, paths = native_modules
    with pytest.raises(paths.ConfigError):
        contract_module.normalize_nix_repository_url(url)


def test_promotion_secret_scan_accepts_safe_and_redacted_git_transport_users(native_modules):
    contract_module, _, _ = native_modules
    contract_module.reject_promotion_secrets(
        {
            "native": "git+ssh://git@github.com/example/repo.git",
            "evidence": "ssh://[REDACTED]@github.com/example/repo.git",
        }
    )


def test_native_base_and_layered_results_preserve_common_semantics(native_modules):
    contract_module, result_module, _ = native_modules
    base = base_contract()
    base_url = contract_module.resolve_contract_input(base, "domain-adapter")["repository_url"]
    base_summary = result_module.validate_native_result(
        native_result(base, "domain-adapter", base_url, layered=False),
        "domain-adapter",
        f"{base_url}?rev={REVISION}",
        base,
    )
    layered = layered_contract()
    lower_url = contract_module.resolve_contract_input(layered, "lower-composition")["repository_url"]
    layered_summary = result_module.validate_native_result(
        native_result(layered, "lower-composition", lower_url, layered=True),
        "lower-composition",
        f"{lower_url}?rev={REVISION}",
        layered,
    )
    assert base_summary["checks"]["selected_path"] == []
    assert layered_summary["checks"]["selected_path"] == ["higher"]
    assert layered_summary["checks"]["leaf_count"] == 2
    assert layered_summary["proofs"] == {
        "lock_byte_identical": True,
        "tree_byte_identical": True,
        "contract_compatible": True,
        "snapshots_valid": True,
        "snapshots_byte_identical": True,
    }


def test_native_result_accepts_nix_normalized_git_suffix(native_modules):
    contract_module, result_module, _ = native_modules
    contract = base_contract()
    repository_url = contract_module.resolve_contract_input(contract, "domain-adapter")[
        "repository_url"
    ]
    result = native_result(contract, "domain-adapter", repository_url, layered=False)
    result["candidate"]["resolvedUrl"] = result["candidate"]["resolvedUrl"].removesuffix(
        ".git"
    )

    summary = result_module.validate_native_result(
        result,
        "domain-adapter",
        f"{repository_url}?rev={REVISION}",
        contract,
    )

    assert summary["candidate"]["resolved_repository_url"].endswith("domain-adapter")


@pytest.mark.parametrize(
    "mutate,message",
    [
        (
            lambda value: value.update(
                checks={"one": {"higher-check": "pass"}, "two": {"higher-check": "pass"}}
            ),
            "ambiguous",
        ),
        (lambda value: value["checks"]["lower"].update(lower_check="fail"), "non-pass"),
        (lambda value: value["hosts"]["GenericHost"].update(drvPath="/tmp/not-a.drv"), "derivation"),
        (lambda value: value["hosts"].update(Unknown=value["hosts"]["GenericHost"]), "host set"),
        (lambda value: value["snapshots"].update(byteIdentical=False), "snapshot"),
    ],
)
def test_native_result_ambiguity_hosts_and_proofs_fail_closed(native_modules, mutate, message):
    contract_module, result_module, paths = native_modules
    contract = layered_contract()
    repository_url = contract_module.resolve_contract_input(contract, "lower-composition")["repository_url"]
    result = native_result(contract, "lower-composition", repository_url, layered=True)
    mutate(result)
    with pytest.raises(paths.ConfigError, match=message):
        result_module.validate_native_result(
            result,
            "lower-composition",
            f"{repository_url}?rev={REVISION}",
            contract,
        )


def test_native_result_identity_drift_and_duplicate_json_keys_fail_closed(native_modules):
    contract_module, result_module, paths = native_modules
    contract = base_contract()
    repository_url = contract_module.resolve_contract_input(contract, "domain-adapter")["repository_url"]
    result = native_result(contract, "domain-adapter", repository_url, layered=False)
    with pytest.raises(paths.ConfigError, match="identity drifted"):
        result_module.validate_native_result(
            result,
            "domain-adapter",
            f"{repository_url}?rev={REVISION}",
            contract,
            expected_identity={"schema": "reviewed-result-v1", "version": None},
        )
    with pytest.raises(paths.ConfigError, match="duplicate object key"):
        contract_module.strict_json_loads('{"schema":"a","schema":"b"}', "fixture")


def test_native_result_artifact_has_exact_ten_mib_bound(native_modules, monkeypatch, tmp_path: Path):
    _, _, paths = native_modules
    qualification_module = importlib.import_module("nix_darwin_local.promotion_qualification")
    monkeypatch.setattr(qualification_module, "MAX_NATIVE_RESULT_BYTES", 64)
    with pytest.raises(paths.ConfigError, match="10 MiB"):
        qualification_module.write_json_artifact(
            tmp_path, "native.json", {"payload": "x" * 80}, summary={"outcome": "pass"}
        )


@pytest.mark.parametrize(
    "mutate,revision",
    [
        (lambda value: value.update(nodes=[]), REVISION),
        (lambda value: value["nodes"].update(**{"domain-adapter": []}), REVISION),
        (lambda value: value["nodes"]["domain-adapter"].update(original=[]), REVISION),
        (lambda value: value["nodes"]["domain-adapter"].update(locked=[]), REVISION),
        (lambda value: value["nodes"]["domain-adapter"]["original"].update(dir="subflake"), REVISION),
        (lambda value: value["nodes"]["domain-adapter"]["locked"].update(dir="subflake"), REVISION),
        (lambda value: value["nodes"]["domain-adapter"]["original"].update(type="tarball"), REVISION),
        (
            lambda value: value["nodes"]["domain-adapter"]["locked"].update(
                url="https://github.com/example/other.git"
            ),
            REVISION,
        ),
        (lambda value: value["nodes"]["domain-adapter"]["locked"].update(ref="deploy"), REVISION),
        (lambda value: None, "not-a-revision"),
        (lambda value: value["nodes"]["domain-adapter"]["locked"].update(rev=OLD_REVISION), REVISION),
    ],
)
def test_selected_lock_node_rejects_every_ambiguous_identity_branch(
    lock_modules, mutate, revision
):
    lock_module, paths = lock_modules
    value = lock_document()
    mutate(value)
    with pytest.raises(paths.ConfigError):
        lock_module.validate_selected_lock_node(
            value,
            "domain-adapter",
            "https://github.com/example/domain-adapter.git",
            revision,
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda before, after: after.update(version=8),
        lambda before, after: after.update(nodes=[]),
        lambda before, after: after["nodes"].update(extra={}),
        lambda before, after: after["nodes"]["domain-adapter"].update(
            original={"type": "git", "url": "https://github.com/example/other.git"}
        ),
        lambda before, after: after["nodes"]["domain-adapter"]["locked"].update(ref="main"),
    ],
)
def test_owned_lock_transition_rejects_non_dynamic_or_graph_changes(lock_modules, mutate):
    lock_module, paths = lock_modules
    before = lock_document(OLD_REVISION)
    after = lock_document(REVISION)
    mutate(before, after)
    with pytest.raises(paths.ConfigError):
        lock_module.authorize_owned_lock_transition(
            before,
            after,
            "domain-adapter",
            "https://github.com/example/domain-adapter.git",
            REVISION,
        )


@pytest.mark.parametrize(
    "mutate,candidate_ref,message",
    [
        (lambda value: value.update(schema=""), None, "schema identity"),
        (lambda value: value.update(version=2), None, "version"),
        (lambda value: value.update(input="other"), None, "input identity"),
        (lambda value: value.update(candidate=[]), None, "candidate must be an object"),
        (lambda value: value["candidate"].pop("url"), None, "incomplete"),
        (
            lambda value: value["candidate"].update(
                resolvedUrl="https://github.com/example/other.git"
            ),
            None,
            "resolved URL",
        ),
        (lambda value: value["lock"].update(byteIdentical=False), None, "byte identity"),
        (lambda value: value.update(contractCompatibility=[]), None, "compatibility"),
        (lambda value: value.update(snapshots=[]), None, "snapshot"),
        (lambda value: value.update(checks={}), None, "empty or non-map"),
        (lambda value: value.update(checks={"bad key": "pass"}), None, "check identity"),
        (
            lambda value: value["hosts"]["GenericHost"].update(extra=True),
            None,
            "host evidence fields",
        ),
        (
            lambda value: value["hosts"]["GenericHost"].update(status="fail"),
            None,
            "host status",
        ),
        (lambda value: None, "not-a-candidate", "candidate reference"),
        (
            lambda value: None,
            "git+ssh://git@github.com/example/domain-adapter.git?rev=short",
            "reference revision",
        ),
    ],
)
def test_native_result_rejects_invalid_protocol_branches(
    native_modules, mutate, candidate_ref, message
):
    contract_module, result_module, paths = native_modules
    contract = base_contract()
    repository_url = contract_module.resolve_contract_input(contract, "domain-adapter")[
        "repository_url"
    ]
    result = native_result(contract, "domain-adapter", repository_url, layered=False)
    mutate(result)
    with pytest.raises(paths.ConfigError, match=message):
        result_module.validate_native_result(
            result,
            "domain-adapter",
            candidate_ref or f"{repository_url}?rev={REVISION}",
            contract,
        )


def test_native_result_rejects_non_object_and_accepts_derivation_alias(native_modules):
    contract_module, result_module, paths = native_modules
    contract = base_contract()
    repository_url = contract_module.resolve_contract_input(contract, "domain-adapter")[
        "repository_url"
    ]
    candidate_ref = f"{repository_url}?rev={REVISION}"
    with pytest.raises(paths.ConfigError, match="JSON object"):
        result_module.validate_native_result([], "domain-adapter", candidate_ref, contract)

    result = native_result(contract, "domain-adapter", repository_url, layered=False)
    host = result["hosts"]["GenericHost"]
    host["derivation"] = host.pop("drvPath")
    summary = result_module.validate_native_result(
        result, "domain-adapter", candidate_ref, contract
    )
    assert summary["hosts"]["all_pass"] is True


def test_native_result_small_artifact_binding_is_digest_bound(native_modules, tmp_path: Path):
    _, _, _ = native_modules
    qualification_module = importlib.import_module("nix_darwin_local.promotion_qualification")
    binding = qualification_module.write_json_artifact(
        tmp_path, "native.json", {"outcome": "pass"}, summary={"outcome": "pass"}
    )
    assert binding["path"] == "native.json"
    assert binding["size"] > 0
    assert len(binding["sha256"]) == 64
    assert (tmp_path / "native.json").is_file()
