# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_composition.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/local_source.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/source_catalog.py
"""Integration test for composition repository identity transition authorization and evidence.

This test exercises the physical public-composition repository transition
(composition-nd -> composition_dinas) while preserving the logical Nix input key
(composition-nd). It uses immutable lock metadata materialized through local Git
and Nix store realizations to validate authorization, contract validation,
and retained evidence readback in an isolated fixture environment.
"""
import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "libexec"))
from nix_darwin_local.deployment_workflow import Workflow
from nix_darwin_local.local_source import materialize_source
from nix_darwin_local.paths import ConfigError
from nix_darwin_local.promotion_composition import (
    COMPOSITION_AUTHORIZATION_SCHEMA,
    COMPOSITION_INPUT,
    COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL,
    COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_URL,
    authorize_composition_repository_transition,
    validate_composition_repository_authorization,
    validate_composition_transition_contract,
)
from nix_darwin_local.source_catalog import SourceCatalogEntry

pytestmark = pytest.mark.skipif(shutil.which("nix") is None, reason="Nix unavailable")


def run(cwd, *args):
    return subprocess.run(
        args, cwd=cwd, text=True, capture_output=True, check=True
    ).stdout.strip()


def commit(repo, message="fixture source"):
    run(repo, "git", "add", "-A")
    run(repo, "git", "commit", "-qm", message)
    return run(repo, "git", "rev-parse", "HEAD")


def test_composition_repository_transition_with_materialized_nix_metadata(tmp_path):
    repo = tmp_path / "fixture-composition"
    repo.mkdir(parents=True)
    run(repo, "git", "init", "-b", "main")
    run(repo, "git", "config", "user.name", "Fixture")
    run(repo, "git", "config", "user.email", "fixture@example.invalid")

    (repo / "flake.nix").write_text('{ outputs = { self }: { marker = "composition-dinas-identity"; }; }')
    first_rev = commit(repo, "first commit")

    entry = SourceCatalogEntry("composition-nd", repo, "https://unreachable.invalid/composition.git", "refs/heads/main")
    workflow = Workflow(repo, display_command_output=False)
    before_source = materialize_source(workflow, entry, first_rev)

    (repo / "marker.txt").write_text("candidate composition content\n")
    second_rev = commit(repo, "second commit")
    after_source = materialize_source(workflow, entry, second_rev)

    assert before_source.nar_hash != after_source.nar_hash

    # Verify Nix store realizations evaluate cleanly
    for snapshot in (before_source, after_source):
        assert run(
            tmp_path,
            "nix",
            "eval",
            "--raw",
            "--offline",
            "--no-update-lock-file",
            "--no-write-lock-file",
            f"path:{snapshot.store_path}#marker",
        ) == "composition-dinas-identity"

    preimage_url = COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_URL
    candidate_url = COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL

    def make_lock(source, url):
        original = {"type": "git", "url": url, "ref": "refs/heads/main"}
        locked = {
            **original,
            "rev": source.revision,
            "narHash": source.nar_hash,
            "lastModified": source.locked_metadata["lastModified"],
        }
        return {
            "version": 7,
            "root": "root",
            "nodes": {
                "root": {"inputs": {"composition-nd": "comp-node"}},
                "comp-node": {"original": original, "locked": locked},
            },
        }

    pre_lock = make_lock(before_source, preimage_url)
    post_lock = make_lock(after_source, candidate_url)

    # Rejection of non-composition-nd input name
    with pytest.raises(ConfigError, match="valid only for input composition-nd"):
        authorize_composition_repository_transition(
            pre_lock, post_lock, "comp-node", second_rev, input_name="core"
        )

    # Rejection of non-main release ref
    with pytest.raises(ConfigError, match="requires main release ref"):
        authorize_composition_repository_transition(
            pre_lock, post_lock, "comp-node", second_rev, expected_ref="refs/heads/feature"
        )

    # Succeeded composition repository transition authorization
    auth = authorize_composition_repository_transition(
        pre_lock, post_lock, "comp-node", second_rev
    )
    assert auth["schema"] == COMPOSITION_AUTHORIZATION_SCHEMA
    assert auth["selectedInput"] == "composition-nd"
    assert auth["selectedNode"] == "comp-node"
    assert auth["repositoryUrl"] == candidate_url
    assert auth["previousRepositoryUrl"] == preimage_url
    assert auth["requestedRevision"] == second_rev
    assert auth["changedNodes"] == ["comp-node"]
    assert "preimageOwnedNodes" in auth
    assert "candidateOwnedNodes" in auth
    assert "outsideGraphDigest" in auth
    assert "sharedFollowsDigest" in auth
    assert "authorizationDigest" in auth

    # Retained evidence validates and round-trips
    assert validate_composition_repository_authorization(auth) == auth
    round_trip = json.loads(json.dumps(auth))
    assert validate_composition_repository_authorization(round_trip) == round_trip

    # Tampering with destination repository URL is rejected
    tampered = copy.deepcopy(auth)
    tampered["repositoryUrl"] = "ssh://git@github.com/Knowledge-Forge-AI/other.git"
    with pytest.raises(ConfigError, match="destination repository URL evidence is invalid"):
        validate_composition_repository_authorization(tampered)

    # Tampering with preimage repository URL is rejected
    tampered_prev = copy.deepcopy(auth)
    tampered_prev["previousRepositoryUrl"] = "ssh://git@github.com/Knowledge-Forge-AI/other.git"
    with pytest.raises(ConfigError, match="preimage repository URL evidence is invalid"):
        validate_composition_repository_authorization(tampered_prev)

    # Tampering with digest is rejected
    tampered_digest = copy.deepcopy(auth)
    tampered_digest["authorizationDigest"] = "0" * 64
    with pytest.raises(ConfigError, match="digest does not match content"):
        validate_composition_repository_authorization(tampered_digest)

    # Portable lock contains no host/store leak
    serialized_post = json.dumps(post_lock)
    assert str(tmp_path) not in serialized_post
    assert "/nix/store/" not in serialized_post


def test_composition_repository_transition_contract_validation(tmp_path):
    preimage_url = COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_URL
    candidate_url = COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL

    # Test layered contract validation
    def make_layered_contract(comp_url):
        return {
            "schema": "fixture-private-promotion-v1",
            "version": 1,
            "composition": {
                "name": "composition-private-nd",
                "repositoryUrl": f"file://{tmp_path}/parent",
            },
            "system": "aarch64-darwin",
            "modules": [
                {
                    "name": "private-module",
                    "input": "private-module",
                    "url": "https://example.invalid/private-module",
                    "checkout": "private-module",
                    "bootstrapStrategy": "none",
                    "ownsMachineLocalConfiguration": False,
                }
            ],
            "promotableInputs": {
                "rule": "layered-private-parent",
                "names": [COMPOSITION_INPUT],
            },
            "qualification": {
                "entryPoint": "bin/qualify-promotion",
                "mode": "exact-candidate",
            },
            "checks": ["composition"],
            "hosts": ["StageAHost"],
            "packageSetIdentity": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-nixpkgs",
            "requiredLowerPromotion": {
                "schema": "fixture-lower-promotion-v1",
                "version": 1,
                "candidateInput": COMPOSITION_INPUT,
            },
            "lowerPromotion": {
                "schema": "fixture-lower-promotion-v1",
                "version": 1,
                "composition": {
                    "name": "composition-nd",
                    "repositoryUrl": comp_url,
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
            },
            "combinedModules": [
                {
                    "name": "base-module",
                    "input": "base-module",
                    "url": "https://example.invalid/base-module",
                    "checkout": "base-module",
                    "bootstrapStrategy": "none",
                    "ownsMachineLocalConfiguration": False,
                },
                {
                    "name": "private-module",
                    "input": "private-module",
                    "url": "https://example.invalid/private-module",
                    "checkout": "private-module",
                    "bootstrapStrategy": "none",
                    "ownsMachineLocalConfiguration": False,
                },
            ],
            "semantics": {
                "nonActivation": True,
                "requiresExplicitLockSelection": True,
            },
        }

    entry_contract = make_layered_contract(preimage_url)
    cand_contract = make_layered_contract(candidate_url)

    # Authorized layered transition succeeds
    validate_composition_transition_contract(entry_contract, cand_contract, allow_file=True)

    # Drift in lowerPromotion beyond repositoryUrl is rejected
    drifted_lower = copy.deepcopy(cand_contract)
    drifted_lower["lowerPromotion"]["checks"].append("unauthorized_check")
    with pytest.raises(ConfigError, match="lower promotion contract drifted"):
        validate_composition_transition_contract(entry_contract, drifted_lower, allow_file=True)

    # Drift in outer contract is rejected
    drifted_outer = copy.deepcopy(cand_contract)
    drifted_outer["checks"].append("unauthorized_check")
    with pytest.raises(ConfigError, match="candidate promotion contract drifted"):
        validate_composition_transition_contract(entry_contract, drifted_outer, allow_file=True)

    # Test direct contract validation
    def make_direct_contract(comp_url):
        return {
            "schema": "fixture-base-promotion-v1",
            "version": 1,
            "composition": {
                "name": "composition-nd",
                "repositoryUrl": f"file://{tmp_path}/composition",
            },
            "system": "aarch64-darwin",
            "modules": [
                {
                    "name": "composition-nd",
                    "input": COMPOSITION_INPUT,
                    "url": comp_url,
                    "checkout": "composition-nd",
                    "bootstrapStrategy": "none",
                    "ownsMachineLocalConfiguration": False,
                }
            ],
            "promotableInputs": {
                "rule": "direct-module",
                "names": [COMPOSITION_INPUT],
            },
            "qualification": {
                "entryPoint": "bin/qualify-promotion",
                "mode": "exact-candidate",
            },
            "checks": ["composition"],
            "hosts": ["StageAHost"],
            "packageSetIdentity": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-nixpkgs",
        }

    direct_entry = make_direct_contract(preimage_url)
    direct_cand = make_direct_contract(candidate_url)

    validate_composition_transition_contract(direct_entry, direct_cand, allow_file=True)
