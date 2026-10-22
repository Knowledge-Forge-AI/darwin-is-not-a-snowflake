# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_core.py

from __future__ import annotations

import copy
import importlib

import pytest


OLD_REVISION = "b" * 40
REVISION = "a" * 40


@pytest.fixture
def core_module(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    return importlib.import_module("nix_darwin_local.promotion_core")


def _source(revision: str, *, selector: str = "nix-darwin") -> dict:
    return {
        "inputs": {
            "nixpkgs": "nixpkgs-node",
            "nix-darwin": "nix-darwin-node",
        },
        "original": {
            "type": "git",
            "url": "ssh://git@github.com/Knowledge-Forge-AI/.flakes.git",
            "dir": selector,
        },
        "locked": {
            "type": "git",
            "url": "ssh://git@github.com/Knowledge-Forge-AI/.flakes.git",
            "dir": selector,
            "ref": "refs/heads/main",
            "rev": revision,
            "revCount": 2,
            "lastModified": 100,
            "narHash": f"sha256-{revision[:8]}",
        },
    }


def _child(name: str) -> dict:
    return {
        "original": {"type": "github", "owner": "NixOS", "repo": name},
        "locked": {
            "type": "github",
            "owner": "NixOS",
            "repo": name,
            "rev": "c" * 40,
            "narHash": f"sha256-{name}",
        },
    }


def _lock(revision: str = OLD_REVISION) -> dict:
    return {
        "nodes": {
            "root": {"inputs": {"core": "core-node", "unrelated": "unrelated-node"}},
            "core-node": _source(revision),
            "nixpkgs-node": _child("nixpkgs"),
            "nix-darwin-node": _child("nix-darwin"),
            "unrelated-node": _child("unrelated"),
        },
        "root": "root",
        "version": 7,
    }


def test_core_route_accepts_only_the_selected_revision_move(core_module):
    before = _lock()
    after = _lock(REVISION)
    after["nodes"]["core-node"]["locked"].update(
        revCount=3, lastModified=200, narHash=f"sha256-{REVISION[:8]}"
    )

    authorization = core_module.authorize_core_lock_transition(before, after, REVISION)

    assert authorization["selectedInput"] == "core"
    assert authorization["selectedNode"] == "core-node"
    assert authorization["changedNodes"] == ["core-node"]
    assert authorization["preimageOwnedNodes"] == [
        "core-node",
        "nix-darwin-node",
        "nixpkgs-node",
    ]
    assert core_module.core_input_revision(after) == REVISION
    assert core_module.validate_core_lock_authorization(authorization) == authorization


@pytest.mark.parametrize(
    "mutate, message",
    [
        (
            lambda before, after: after["nodes"]["nixpkgs-node"]["locked"].update(
                rev="d" * 40
            ),
            "broader core release",
        ),
        (
            lambda before, after: after["nodes"]["core-node"]["inputs"].update(
                extra="unrelated-node"
            ),
            "unchanged core child inputs",
        ),
        (
            lambda before, after: after["nodes"]["core-node"]["original"].pop("dir"),
            "must select nix-darwin",
        ),
        (
            lambda before, after: after["nodes"]["root"]["inputs"].update(
                core="unrelated-node"
            ),
            "root input topology",
        ),
    ],
)
def test_core_route_rejects_child_or_source_topology_movement(core_module, mutate, message):
    before = _lock()
    after = _lock(REVISION)
    mutate(before, after)
    with pytest.raises(core_module.ConfigError, match=message):
        core_module.authorize_core_lock_transition(before, after, REVISION)


def test_core_route_rejects_non_core_input_and_revision_mismatch(core_module):
    before = _lock()
    after = _lock(REVISION)
    with pytest.raises(core_module.ConfigError, match="exactly core"):
        core_module.resolve_core_node(before, input_name="composition-nd/core")
    with pytest.raises(core_module.ConfigError, match="requested revision"):
        core_module.authorize_core_lock_transition(before, after, OLD_REVISION)


def test_core_route_rejects_tampered_authorization_digest(core_module):
    record = core_module.authorize_core_lock_transition(_lock(), _lock(REVISION), REVISION)
    tampered = copy.deepcopy(record)
    tampered["childGraphDigest"] = "0" * 64
    with pytest.raises(core_module.ConfigError, match="digest"):
        core_module.validate_core_lock_authorization(tampered)


def test_core_route_can_bind_canonical_repository_url(core_module):
    before = _lock()
    after = _lock(REVISION)
    assert core_module.authorize_core_lock_transition(
        before,
        after,
        REVISION,
        repository_url="git+ssh://git@github.com/Knowledge-Forge-AI/.flakes.git",
    )["subflake"] == "nix-darwin"


def test_core_route_authorization_defects(core_module):
    before = _lock()
    after = _lock(REVISION)
    valid_auth = core_module.authorize_core_lock_transition(before, after, REVISION)

    def check_auth_defect(auth, match):
        with pytest.raises(core_module.ConfigError, match=match):
            core_module.validate_core_lock_authorization(auth)

    bad = dict(valid_auth)
    del bad["schema"]
    check_auth_defect(bad, "authorization fields are invalid")
    check_auth_defect({**valid_auth, "schema": "wrong"}, "authorization schema is invalid")
    check_auth_defect({**valid_auth, "selectedInput": "wrong"}, "selected input identity is invalid")
    check_auth_defect({**valid_auth, "repositoryUrl": ""}, "repository URL evidence is invalid")
    check_auth_defect({**valid_auth, "subflake": "wrong"}, "subflake evidence is invalid")
    check_auth_defect({**valid_auth, "requestedRevision": "bad"}, "requested revision evidence is invalid")
    check_auth_defect({**valid_auth, "changedNodes": ["other"]}, "changed-node evidence is invalid")
    check_auth_defect({**valid_auth, "preimageOwnedNodes": ["b", "a"]}, "node identity evidence is not canonical")
    check_auth_defect({**valid_auth, "outsideGraphDigest": "not-hex"}, "authorization digest is invalid")


def test_core_route_additional_edge_cases(core_module):
    before = _lock()
    after = _lock(REVISION)

    # invalid expected_subflake
    with pytest.raises(core_module.ConfigError, match="subflake selector is invalid"):
        core_module.authorize_core_lock_transition(before, after, REVISION, expected_subflake="unsupported")

    # invalid revision
    with pytest.raises(core_module.ConfigError, match="revision is invalid"):
        core_module.authorize_core_lock_transition(before, after, "not-a-rev")

    # root or version changed
    after_root_changed = copy.deepcopy(after)
    after_root_changed["version"] = 8
    with pytest.raises(core_module.ConfigError, match="root or version changed"):
        core_module.authorize_core_lock_transition(before, after_root_changed, REVISION)

    # non-main ref
    after_non_main = copy.deepcopy(after)
    after_non_main["nodes"]["core-node"]["locked"]["ref"] = "refs/heads/feature"
    with pytest.raises(core_module.ConfigError, match="non-main release ref"):
        core_module.authorize_core_lock_transition(before, after_non_main, REVISION)

    # bad revision in locked source
    after_bad_rev = copy.deepcopy(after)
    after_bad_rev["nodes"]["core-node"]["locked"]["rev"] = "bad"
    with pytest.raises(core_module.ConfigError, match="requested revision"):
        core_module.authorize_core_lock_transition(before, after_bad_rev, REVISION)

    # bad narHash
    after_bad_hash = copy.deepcopy(after)
    after_bad_hash["nodes"]["core-node"]["locked"]["narHash"] = "hash with spaces"
    with pytest.raises(core_module.ConfigError, match="immutable content identity"):
        core_module.authorize_core_lock_transition(before, after_bad_hash, REVISION)

    # core_input_revision defects
    bad_rev_lock = copy.deepcopy(before)
    bad_rev_lock["nodes"]["core-node"]["locked"]["rev"] = "not-hex"
    with pytest.raises(core_module.ConfigError, match="selected revision is invalid"):
        core_module.core_input_revision(bad_rev_lock)

    # resolve_core_node defects
    missing_core = copy.deepcopy(before)
    del missing_core["nodes"]["root"]["inputs"]["core"]
    with pytest.raises(core_module.ConfigError, match="requires a direct root core input"):
        core_module.resolve_core_node(missing_core)

    unresolved_core = copy.deepcopy(before)
    unresolved_core["nodes"]["root"]["inputs"]["core"] = "missing-node"
    with pytest.raises(core_module.ConfigError, match="is unresolved"):
        core_module.resolve_core_node(unresolved_core)

    # repository_url mismatch
    with pytest.raises(core_module.ConfigError, match="repository URL mismatched"):
        core_module.authorize_core_lock_transition(
            before, after, REVISION, repository_url="https://github.com/example/other-repo.git"
        )

    # core inputs changed
    after_inputs_changed = copy.deepcopy(after)
    after_inputs_changed["nodes"]["extra-node"] = {"inputs": {}}
    after_inputs_changed["nodes"]["core-node"]["inputs"] = {"extra": "extra-node"}
    with pytest.raises(core_module.ConfigError, match="unchanged core child inputs"):
        core_module.authorize_core_lock_transition(before, after_inputs_changed, REVISION)

    # core selector changed
    after_selector_changed = copy.deepcopy(after)
    after_selector_changed["nodes"]["core-node"]["locked"]["submodules"] = True
    with pytest.raises(core_module.ConfigError, match="changed the core repository selector"):
        core_module.authorize_core_lock_transition(before, after_selector_changed, REVISION)

    # non-git type
    after_not_git = copy.deepcopy(after)
    after_not_git["nodes"]["core-node"]["original"]["type"] = "tarball"
    with pytest.raises(core_module.ConfigError, match="retain Git identity"):
        core_module.authorize_core_lock_transition(before, after_not_git, REVISION)

    # original and locked URLs disagree
    after_url_mismatch = copy.deepcopy(after)
    after_url_mismatch["nodes"]["core-node"]["locked"]["url"] = "https://github.com/example/other.git"
    with pytest.raises(core_module.ConfigError, match="URLs disagree"):
        core_module.authorize_core_lock_transition(before, after_url_mismatch, REVISION)

    # locked dir mismatch
    after_dir_mismatch = copy.deepcopy(after)
    after_dir_mismatch["nodes"]["core-node"]["locked"]["dir"] = "wrong-dir"
    with pytest.raises(core_module.ConfigError, match="source resolution must select"):
        core_module.authorize_core_lock_transition(before, after_dir_mismatch, REVISION)

    # source declaration changed
    after_source_decl = copy.deepcopy(after)
    after_source_decl["nodes"]["core-node"]["original"]["url"] = "https://github.com/example/other.git"
    after_source_decl["nodes"]["core-node"]["locked"]["url"] = "https://github.com/example/other.git"
    with pytest.raises(core_module.ConfigError, match="changed the core source declaration"):
        core_module.authorize_core_lock_transition(before, after_source_decl, REVISION)


def _root_source(
    revision: str,
    *,
    url: str = "ssh://git@github.com/Knowledge-Forge-AI/.flakes.git",
) -> dict:
    return {
        "inputs": {
            "nixpkgs": "nixpkgs-node",
            "nix-darwin": "nix-darwin-node",
        },
        "original": {
            "type": "git",
            "url": url,
        },
        "locked": {
            "type": "git",
            "url": url,
            "ref": "refs/heads/main",
            "rev": revision,
            "revCount": 2,
            "lastModified": 100,
            "narHash": f"sha256-{revision[:8]}",
        },
    }


def _root_lock(
    revision: str = OLD_REVISION,
    *,
    url: str = "ssh://git@github.com/Knowledge-Forge-AI/.flakes.git",
) -> dict:
    return {
        "nodes": {
            "root": {"inputs": {"core": "core-node", "unrelated": "unrelated-node"}},
            "core-node": _root_source(revision, url=url),
            "nixpkgs-node": _child("nixpkgs"),
            "nix-darwin-node": _child("nix-darwin"),
            "unrelated-node": _child("unrelated"),
        },
        "root": "root",
        "version": 7,
    }


def test_repository_transition_succeeds_only_with_opt_in(core_module):
    before = _root_lock(OLD_REVISION, url="ssh://git@github.com/Knowledge-Forge-AI/.flakes.git")
    after = _root_lock(
        REVISION, url="ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git"
    )

    # Ordinary promotion rejects repository change
    with pytest.raises(core_module.ConfigError, match="changed the core source declaration"):
        core_module.authorize_core_lock_transition(before, after, REVISION)

    # Repository transition succeeds with opt-in
    auth = core_module.authorize_core_lock_transition(
        before, after, REVISION, allow_repository_transition=True
    )
    assert auth["schema"] == "nix-update-core-repository-transition-v1"
    assert auth["selectedInput"] == "core"
    assert auth["selectedNode"] == "core-node"
    assert auth["repositoryUrl"] == "ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git"
    assert auth["previousRepositoryUrl"] == "ssh://git@github.com/Knowledge-Forge-AI/.flakes.git"
    assert auth["subflake"] == ""
    assert "previousSubflake" not in auth
    assert auth["requestedRevision"] == REVISION
    assert auth["changedNodes"] == ["core-node"]
    assert core_module.validate_core_lock_authorization(auth) == auth


@pytest.mark.parametrize(
    "old_url, new_url",
    [
        (
            "git+ssh://git@github.com/Knowledge-Forge-AI/.flakes.git",
            "git+ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git",
        ),
        (
            "ssh://git@github.com:22/Knowledge-Forge-AI/.flakes.git",
            "ssh://git@github.com:22/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git",
        ),
        (
            "ssh://git@github.com/Knowledge-Forge-AI/.flakes",
            "ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake",
        ),
    ],
)
def test_repository_transition_transport_normalization_variants(
    core_module, old_url, new_url
):
    before = _root_lock(OLD_REVISION, url=old_url)
    after = _root_lock(REVISION, url=new_url)
    auth = core_module.authorize_core_lock_transition(
        before, after, REVISION, allow_repository_transition=True
    )
    assert auth["repositoryUrl"] == "ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git"
    assert auth["previousRepositoryUrl"] == "ssh://git@github.com/Knowledge-Forge-AI/.flakes.git"
    assert core_module.validate_core_lock_authorization(auth) == auth


@pytest.mark.parametrize(
    "old_url, new_url",
    [
        (
            "ssh://git@github.com/someoneelse/.flakes.git",
            "ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git",
        ),
        (
            "ssh://git@github.com/Knowledge-Forge-AI/.flakes.git",
            "ssh://git@github.com/someoneelse/darwin-is-not-a-snowflake.git",
        ),
        (
            "ssh://git@github.com/Knowledge-Forge-AI/.flakes.git",
            "ssh://git@github.com/Knowledge-Forge-AI/other-repo.git",
        ),
        (
            "ssh://git@github.com/Knowledge-Forge-AI/.flakes.git",
            "ssh://git@github.com/Knowledge-Forge-AI/.flakes.git",
        ),
        (
            "ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git",
            "ssh://git@github.com/Knowledge-Forge-AI/.flakes.git",
        ),
        (
            "https://github.com/Knowledge-Forge-AI/.flakes.git",
            "ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git",
        ),
        (
            "ssh://git@github.com/Knowledge-Forge-AI/.flakes.git",
            "https://github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git",
        ),
        (
            "ssh://git@gitlab.com/Knowledge-Forge-AI/.flakes.git",
            "ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git",
        ),
    ],
)
def test_repository_transition_rejects_unauthorized_endpoints(
    core_module, old_url, new_url
):
    before = _root_lock(OLD_REVISION, url=old_url)
    after = _root_lock(REVISION, url=new_url)
    with pytest.raises(core_module.ConfigError, match="exact .flakes to darwin-is-not-a-snowflake transition"):
        core_module.authorize_core_lock_transition(
            before, after, REVISION, allow_repository_transition=True
        )


def test_repository_transition_cannot_combine_with_layout_transition(core_module):
    before_root = _root_lock(OLD_REVISION)
    after_root = _root_lock(
        REVISION, url="ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git"
    )

    # Combining both flags fails
    with pytest.raises(
        core_module.ConfigError, match="cannot combine layout and repository transitions"
    ):
        core_module.authorize_core_lock_transition(
            before_root,
            after_root,
            REVISION,
            allow_layout_transition=True,
            allow_repository_transition=True,
        )

    # Preimage with layout selector cannot use repository transition
    before_nested = _lock(OLD_REVISION)
    with pytest.raises(
        core_module.ConfigError, match="requires the root layout preimage"
    ):
        core_module.authorize_core_lock_transition(
            before_nested,
            after_root,
            REVISION,
            allow_repository_transition=True,
        )

    # Candidate with layout selector cannot use repository transition
    after_nested = copy.deepcopy(after_root)
    after_nested["nodes"]["core-node"]["original"]["dir"] = "nix-darwin"
    after_nested["nodes"]["core-node"]["locked"]["dir"] = "nix-darwin"
    with pytest.raises(
        core_module.ConfigError, match="root source must omit the directory selector"
    ):
        core_module.authorize_core_lock_transition(
            before_root,
            after_nested,
            REVISION,
            allow_repository_transition=True,
        )

    # Preimage with empty dir selector fails closed
    before_empty_dir = copy.deepcopy(before_root)
    before_empty_dir["nodes"]["core-node"]["original"]["dir"] = ""
    before_empty_dir["nodes"]["core-node"]["locked"]["dir"] = ""
    with pytest.raises(
        core_module.ConfigError, match="root source must omit the directory selector"
    ):
        core_module.authorize_core_lock_transition(
            before_empty_dir,
            after_root,
            REVISION,
            allow_repository_transition=True,
        )


@pytest.mark.parametrize(
    "mutate, message",
    [
        (
            lambda before, after: after["nodes"]["nixpkgs-node"]["locked"].update(
                rev="d" * 40
            ),
            "broader core release",
        ),
        (
            lambda before, after: after["nodes"]["core-node"]["inputs"].update(
                extra="unrelated-node"
            ),
            "unchanged core child inputs",
        ),
        (
            lambda before, after: after["nodes"]["root"]["inputs"].update(
                core="unrelated-node"
            ),
            "root input topology",
        ),
        (
            lambda before, after: after["nodes"]["core-node"]["locked"].update(
                ref="refs/heads/feature"
            ),
            "non-main release ref",
        ),
        (
            lambda before, after: after["nodes"]["core-node"]["locked"].update(
                submodules=True
            ),
            "changed the core repository selector",
        ),
        (
            lambda before, after: after["nodes"]["core-node"]["original"].update(
                submodules=True
            ),
            "changed the core source declaration",
        ),
        (
            lambda before, after: after["nodes"]["core-node"]["locked"].update(
                narHash="invalid hash"
            ),
            "lacks an immutable content identity",
        ),
    ],
)
def test_repository_transition_rejects_topology_and_child_changes(
    core_module, mutate, message
):
    before = _root_lock(OLD_REVISION)
    after = _root_lock(
        REVISION, url="ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git"
    )
    mutate(before, after)
    with pytest.raises(core_module.ConfigError, match=message):
        core_module.authorize_core_lock_transition(
            before, after, REVISION, allow_repository_transition=True
        )


def test_repository_transition_rejects_unrelated_node_change(core_module):
    before = _root_lock(OLD_REVISION)
    after = _root_lock(
        REVISION, url="ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git"
    )
    after["nodes"]["unrelated-node"]["locked"]["rev"] = "d" * 40
    with pytest.raises(core_module.ConfigError, match="public core node to move alone"):
        core_module.authorize_core_lock_transition(
            before, after, REVISION, allow_repository_transition=True
        )


def test_repository_transition_preimage_url_constraint(core_module):
    before = _root_lock(OLD_REVISION)
    after = _root_lock(
        REVISION, url="ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git"
    )
    # Correct preimage constraint passes
    assert core_module.authorize_core_lock_transition(
        before,
        after,
        REVISION,
        repository_url="git+ssh://git@github.com/Knowledge-Forge-AI/.flakes.git",
        allow_repository_transition=True,
    )["subflake"] == ""

    # Specifying destination URL as repository_url fails (it is a preimage constraint)
    with pytest.raises(core_module.ConfigError, match="repository URL mismatched"):
        core_module.authorize_core_lock_transition(
            before,
            after,
            REVISION,
            repository_url="ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git",
            allow_repository_transition=True,
        )


def test_repository_transition_retained_evidence_validation_and_tampering(core_module):
    import json
    before = _root_lock(OLD_REVISION)
    after = _root_lock(
        REVISION, url="ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git"
    )
    auth = core_module.authorize_core_lock_transition(
        before, after, REVISION, allow_repository_transition=True
    )

    # Valid record round-trips through JSON
    serialized = json.loads(json.dumps(auth))
    assert core_module.validate_core_lock_authorization(serialized) == auth

    def recompute_digest(record: dict) -> dict:
        copied = copy.deepcopy(record)
        copied["authorizationDigest"] = core_module._authorization_digest(copied)
        return copied

    # Tampered childGraphDigest without recomputed digest
    tampered_digest = copy.deepcopy(auth)
    tampered_digest["childGraphDigest"] = "0" * 64
    with pytest.raises(core_module.ConfigError, match="authorization digest does not match content"):
        core_module.validate_core_lock_authorization(tampered_digest)

    # Tampered repositoryUrl without recomputed digest (caught by semantic check)
    tampered_url = copy.deepcopy(auth)
    tampered_url["repositoryUrl"] = "ssh://git@github.com/Knowledge-Forge-AI/other.git"
    with pytest.raises(core_module.ConfigError, match="destination URL evidence is invalid"):
        core_module.validate_core_lock_authorization(tampered_url)

    # Tampered repositoryUrl with recomputed digest
    with pytest.raises(core_module.ConfigError, match="destination URL evidence is invalid"):
        core_module.validate_core_lock_authorization(recompute_digest(tampered_url))

    # Tampered previousRepositoryUrl with recomputed digest
    tampered_prev_url = copy.deepcopy(auth)
    tampered_prev_url["previousRepositoryUrl"] = "ssh://git@github.com/Knowledge-Forge-AI/other.git"
    with pytest.raises(core_module.ConfigError, match="preimage URL evidence is invalid"):
        core_module.validate_core_lock_authorization(recompute_digest(tampered_prev_url))

    # Swapped URLs with recomputed digest
    swapped = copy.deepcopy(auth)
    swapped["repositoryUrl"] = auth["previousRepositoryUrl"]
    swapped["previousRepositoryUrl"] = auth["repositoryUrl"]
    with pytest.raises(core_module.ConfigError, match="destination URL evidence is invalid"):
        core_module.validate_core_lock_authorization(recompute_digest(swapped))

    # Tampered subflake with recomputed digest
    tampered_subflake = copy.deepcopy(auth)
    tampered_subflake["subflake"] = "nix-darwin"
    with pytest.raises(core_module.ConfigError, match="subflake evidence is invalid"):
        core_module.validate_core_lock_authorization(recompute_digest(tampered_subflake))

    # Smuggled layout previousSubflake with recomputed digest
    tampered_layout_field = copy.deepcopy(auth)
    tampered_layout_field["previousSubflake"] = "nix-darwin"
    with pytest.raises(core_module.ConfigError, match="authorization fields are invalid"):
        core_module.validate_core_lock_authorization(recompute_digest(tampered_layout_field))

    # Missing previousRepositoryUrl with recomputed digest
    missing_prev = copy.deepcopy(auth)
    del missing_prev["previousRepositoryUrl"]
    with pytest.raises(core_module.ConfigError, match="authorization fields are invalid"):
        core_module.validate_core_lock_authorization(recompute_digest(missing_prev))
