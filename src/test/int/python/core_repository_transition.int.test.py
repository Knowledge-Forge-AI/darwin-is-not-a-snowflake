# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_core.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/local_source.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/source_catalog.py
"""Integration test for core repository identity transition authorization and evidence.

This test exercises the narrow repository transition authorization helper using
immutable lock metadata materialized through local Git and Nix store realizations.
It validates the portable lock transition contract and retained evidence readback
in an isolated fixture environment, distinct from live GitHub operations or attended
composition migration.
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
from nix_darwin_local.promotion_core import (
    CORE_REPOSITORY_AUTHORIZATION_SCHEMA,
    authorize_core_lock_transition,
    validate_core_lock_authorization,
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


def test_core_repository_transition_with_materialized_nix_metadata(tmp_path):
    repo = tmp_path / "fixture-core"
    repo.mkdir(parents=True)
    run(repo, "git", "init", "-b", "main")
    run(repo, "git", "config", "user.name", "Fixture")
    run(repo, "git", "config", "user.email", "fixture@example.invalid")

    (repo / "flake.nix").write_text('{ outputs = { self }: { marker = "repo-identity"; }; }')
    first_rev = commit(repo, "first commit")

    entry = SourceCatalogEntry("core", repo, "https://unreachable.invalid/core.git", "refs/heads/main")
    workflow = Workflow(repo, display_command_output=False)
    before_source = materialize_source(workflow, entry, first_rev)

    (repo / "marker.txt").write_text("candidate content\n")
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
        ) == "repo-identity"

    # Construct portable lock documents with exact repository identities
    preimage_url = "ssh://git@github.com/Knowledge-Forge-AI/.flakes.git"
    candidate_url = "ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git"

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
                "root": {"inputs": {"core": "core"}},
                "core": {"original": original, "locked": locked},
            },
        }

    pre_lock = make_lock(before_source, preimage_url)
    post_lock = make_lock(after_source, candidate_url)

    # Ordinary promotion rejects repository change
    with pytest.raises(ConfigError, match="changed the core source declaration"):
        authorize_core_lock_transition(pre_lock, post_lock, second_rev)

    # Layout transition requires a historical subtree preimage, not root layout
    with pytest.raises(ConfigError, match="requires the historical preimage"):
        authorize_core_lock_transition(
            pre_lock, post_lock, second_rev, allow_layout_transition=True
        )

    # Repository transition succeeds with explicit opt-in
    auth = authorize_core_lock_transition(
        pre_lock, post_lock, second_rev, allow_repository_transition=True
    )
    assert auth["schema"] == CORE_REPOSITORY_AUTHORIZATION_SCHEMA
    assert auth["selectedInput"] == "core"
    assert auth["selectedNode"] == "core"
    assert auth["repositoryUrl"] == candidate_url
    assert auth["previousRepositoryUrl"] == preimage_url
    assert auth["subflake"] == ""
    assert "previousSubflake" not in auth
    assert auth["requestedRevision"] == second_rev
    assert auth["changedNodes"] == ["core"]

    # Retained evidence validates and round-trips
    assert validate_core_lock_authorization(auth) == auth
    round_trip = json.loads(json.dumps(auth))
    assert validate_core_lock_authorization(round_trip) == round_trip

    # Tampering with destination repository URL is rejected
    tampered = copy.deepcopy(auth)
    tampered["repositoryUrl"] = "ssh://git@github.com/Knowledge-Forge-AI/other.git"
    with pytest.raises(ConfigError, match="destination URL evidence is invalid"):
        validate_core_lock_authorization(tampered)

    # Tampering with preimage repository URL is rejected
    tampered_prev = copy.deepcopy(auth)
    tampered_prev["previousRepositoryUrl"] = "ssh://git@github.com/Knowledge-Forge-AI/other.git"
    with pytest.raises(ConfigError, match="preimage URL evidence is invalid"):
        validate_core_lock_authorization(tampered_prev)

    # Portable lock contains no host/store leak
    serialized_post = json.dumps(post_lock)
    assert str(tmp_path) not in serialized_post
    assert "/nix/store/" not in serialized_post
