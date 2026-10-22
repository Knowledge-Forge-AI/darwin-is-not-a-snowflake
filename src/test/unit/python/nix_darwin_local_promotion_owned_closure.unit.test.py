# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_lock.py

from __future__ import annotations

import copy
import importlib

import pytest


OLD_REVISION = "b" * 40
REVISION = "a" * 40
REPOSITORY_URL = "https://github.com/example/domain-adapter.git"


@pytest.fixture
def modules(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    return (
        importlib.import_module("nix_darwin_local.promotion_lock"),
        importlib.import_module("nix_darwin_local.paths"),
    )


def _git_node(revision: str) -> dict:
    return {
        "inputs": {"nixpkgs": ["nixpkgs"]},
        "original": {"type": "git", "url": REPOSITORY_URL},
        "locked": {
            "type": "git",
            "url": REPOSITORY_URL,
            "ref": "refs/heads/main",
            "rev": revision,
            "narHash": f"sha256-{revision[:8]}",
        },
    }


def _outside_node(name: str) -> dict:
    return {
        "original": {"type": "github", "owner": "example", "repo": name},
        "locked": {
            "type": "github",
            "owner": "example",
            "repo": name,
            "rev": "c" * 40,
            "narHash": f"sha256-{name}",
        },
    }


def _remote_file(name: str, *, query: str = "") -> dict:
    url = f"https://downloads.example.invalid/{name}{query}"
    return {
        "flake": False,
        "original": {"type": "file", "url": url},
        "locked": {"type": "file", "url": url, "narHash": f"sha256-{name}"},
    }


def _lock(revision: str) -> dict:
    return {
        "nodes": {
            "root": {
                "inputs": {
                    "domain-adapter": "domain-adapter",
                    "nixpkgs": "nixpkgs",
                    "unrelated": "unrelated",
                }
            },
            "domain-adapter": _git_node(revision),
            "nixpkgs": _outside_node("nixpkgs"),
            "unrelated": _outside_node("unrelated"),
        },
        "root": "root",
        "version": 7,
    }


def _authorize(module, before: dict, after: dict) -> dict:
    return module.authorize_owned_lock_transition(
        before,
        after,
        "domain-adapter",
        REPOSITORY_URL,
        REVISION,
    )


def test_revision_only_selected_node_has_canonical_single_node_authorization(modules):
    lock_module, _ = modules
    authorization = _authorize(lock_module, _lock(OLD_REVISION), _lock(REVISION))

    assert authorization["selectedNode"] == "domain-adapter"
    assert authorization["preimageOwnedNodes"] == ["domain-adapter"]
    assert authorization["candidateOwnedNodes"] == ["domain-adapter"]
    assert authorization["addedNodes"] == []
    assert authorization["removedNodes"] == []
    assert authorization["changedNodes"] == ["domain-adapter"]
    assert len(authorization["outsideGraphDigest"]) == 64
    assert len(authorization["sharedFollowsDigest"]) == 64
    assert lock_module.validate_owned_closure_authorization(authorization) == authorization


@pytest.mark.parametrize("recorded_ref", ["deploy", "refs/heads/deploy"])
def test_selected_node_accepts_fixture_backed_v2_release_ref_spellings(
    modules, recorded_ref
):
    lock_module, _ = modules
    lock = _lock(REVISION)
    for field in ("original", "locked"):
        lock["nodes"]["domain-adapter"][field]["ref"] = recorded_ref

    assert lock_module.validate_selected_lock_node(
        lock,
        "domain-adapter",
        REPOSITORY_URL,
        REVISION,
        expected_ref="refs/heads/deploy",
        allow_implicit_main=False,
    )


@pytest.mark.parametrize(
    "original_ref,locked_ref",
    [
        (None, "deploy"),
        ("deploy", None),
        ("main", "main"),
        ("refs/heads/main", "refs/heads/main"),
        ("refs/tags/deploy", "refs/tags/deploy"),
    ],
)
def test_selected_node_rejects_v2_missing_or_mismatched_refs(
    modules, original_ref, locked_ref
):
    lock_module, paths_module = modules
    lock = _lock(REVISION)
    for field, value in (("original", original_ref), ("locked", locked_ref)):
        if value is None:
            lock["nodes"]["domain-adapter"][field].pop("ref", None)
        else:
            lock["nodes"]["domain-adapter"][field]["ref"] = value

    with pytest.raises(paths_module.ConfigError):
        lock_module.validate_selected_lock_node(
            lock,
            "domain-adapter",
            REPOSITORY_URL,
            REVISION,
            expected_ref="refs/heads/deploy",
            allow_implicit_main=False,
        )


@pytest.mark.parametrize("recorded_ref", [None, "main", "refs/heads/main"])
def test_v1_ref_accepts_only_equivalent_main_representations(modules, recorded_ref):
    lock_module, paths_module = modules
    lock = _lock(REVISION)
    for field in ("original", "locked"):
        if recorded_ref is None:
            lock["nodes"]["domain-adapter"][field].pop("ref", None)
        else:
            lock["nodes"]["domain-adapter"][field]["ref"] = recorded_ref
    lock_module.validate_selected_lock_node(
        lock, "domain-adapter", REPOSITORY_URL, REVISION
    )

    for field in ("original", "locked"):
        lock["nodes"]["domain-adapter"][field]["ref"] = "deploy"
    with pytest.raises(paths_module.ConfigError):
        lock_module.validate_selected_lock_node(
            lock, "domain-adapter", REPOSITORY_URL, REVISION
        )


def test_selected_root_adds_three_owned_hashed_remote_file_children(modules):
    lock_module, _ = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    children = ["release-cli", "release-desktop", "release-asset"]
    after["nodes"]["domain-adapter"]["inputs"].update({name: name for name in children})
    for name in children:
        after["nodes"][name] = _remote_file(name, query="?channel=stable")

    authorization = _authorize(lock_module, before, after)

    assert authorization["addedNodes"] == sorted(children)
    assert authorization["candidateOwnedNodes"] == sorted(["domain-adapter", *children])


def test_selected_root_removes_owned_children(modules):
    lock_module, _ = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    for lock in (before, after):
        lock["nodes"]["domain-adapter"]["inputs"]["obsolete"] = "obsolete"
        lock["nodes"]["obsolete"] = _remote_file("obsolete")
    del after["nodes"]["domain-adapter"]["inputs"]["obsolete"]
    del after["nodes"]["obsolete"]

    authorization = _authorize(lock_module, before, after)

    assert authorization["removedNodes"] == ["obsolete"]


def test_owned_transitive_node_change_and_nested_closure_are_authorized(modules):
    lock_module, _ = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    for lock, suffix in ((before, "old"), (after, "new")):
        lock["nodes"]["domain-adapter"]["inputs"]["child"] = "child"
        lock["nodes"]["child"] = _remote_file("child")
        lock["nodes"]["child"]["inputs"] = {"grandchild": "grandchild"}
        lock["nodes"]["grandchild"] = _remote_file("grandchild")
        lock["nodes"]["grandchild"]["locked"]["narHash"] = f"sha256-{suffix}"

    authorization = _authorize(lock_module, before, after)

    assert authorization["candidateOwnedNodes"] == ["child", "domain-adapter", "grandchild"]
    assert "grandchild" in authorization["changedNodes"]


@pytest.mark.parametrize(
    "mutate,message",
    [
        (
            lambda before, after: after["nodes"]["domain-adapter"]["inputs"].update(
                child="missing"
            ),
            "unresolved",
        ),
        (
            lambda before, after: (
                after["nodes"]["domain-adapter"]["inputs"].update(child="child"),
                after["nodes"].update(
                    child={"inputs": {"parent": "domain-adapter"}, **_remote_file("child")}
                ),
            ),
            "cyclic",
        ),
        (
            lambda before, after: after["nodes"]["root"]["inputs"].update(
                **{"domain-adapter": "unrelated"}
            ),
            "root node",
        ),
        (
            lambda before, after: after["nodes"]["unrelated"]["locked"].update(
                narHash="sha256-mutated"
            ),
            "outside the selected",
        ),
        (
            lambda before, after: after["nodes"].update(orphan=_remote_file("orphan")),
            "outside the selected",
        ),
    ],
)
def test_malformed_cycle_root_and_outside_changes_fail_closed(modules, mutate, message):
    lock_module, paths = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    mutate(before, after)

    with pytest.raises(paths.ConfigError, match=message):
        _authorize(lock_module, before, after)


def test_shared_owned_child_and_duplicate_root_resolution_are_rejected(modules):
    lock_module, paths = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    for lock in (before, after):
        lock["nodes"]["domain-adapter"]["inputs"]["nixpkgs-owned"] = "nixpkgs"
    with pytest.raises(paths.ConfigError, match="outside string-edge owner"):
        _authorize(lock_module, before, after)

    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    for lock in (before, after):
        lock["nodes"]["root"]["inputs"]["alias"] = "domain-adapter"
    with pytest.raises(paths.ConfigError, match="root resolution is ambiguous"):
        _authorize(lock_module, before, after)


def test_outside_node_cannot_capture_an_added_owned_node(modules):
    lock_module, paths = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    after["nodes"]["domain-adapter"]["inputs"]["child"] = "child"
    after["nodes"]["unrelated"]["inputs"] = {"capture": "child"}
    after["nodes"]["child"] = _remote_file("child")

    with pytest.raises(paths.ConfigError, match="outside string-edge owner"):
        _authorize(lock_module, before, after)


def test_common_follows_are_exact_and_added_child_may_follow_unchanged_shared_node(modules):
    lock_module, paths = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    after["nodes"]["domain-adapter"]["inputs"]["nixpkgs"] = ["unrelated"]
    with pytest.raises(paths.ConfigError, match="follows edge"):
        _authorize(lock_module, before, after)

    after = _lock(REVISION)
    after["nodes"]["domain-adapter"]["inputs"]["child"] = "child"
    after["nodes"]["child"] = _remote_file("child")
    after["nodes"]["child"]["inputs"] = {"nixpkgs": ["nixpkgs"]}
    authorization = _authorize(lock_module, before, after)
    assert authorization["addedNodes"] == ["child"]


@pytest.mark.parametrize(
    "node,message",
    [
        (
            {
                "original": {"type": "path", "path": "/tmp/mutable"},
                "locked": {"type": "path", "path": "/tmp/mutable", "narHash": "sha256-x"},
            },
            "immutable store path",
        ),
        (
            {
                "original": {"type": "file", "url": "https://user@example.invalid/file"},
                "locked": {
                    "type": "file",
                    "url": "https://user@example.invalid/file",
                    "narHash": "sha256-x",
                },
            },
            "unsafe",
        ),
        (
            {
                "original": {"type": "indirect"},
                "locked": {
                    "type": "github",
                    "owner": "example",
                    "repo": "child",
                    "rev": "d" * 40,
                    "narHash": "sha256-x",
                },
            },
            "indirect",
        ),
    ],
)
def test_unsafe_added_owned_sources_are_rejected(modules, node, message):
    lock_module, paths = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    after["nodes"]["domain-adapter"]["inputs"]["child"] = "child"
    after["nodes"]["child"] = node

    with pytest.raises(paths.ConfigError, match=message):
        _authorize(lock_module, before, after)


def test_store_path_source_is_accepted_and_non_store_relative_path_is_rejected(modules):
    lock_module, paths = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    after["nodes"]["domain-adapter"]["inputs"]["child"] = "child"
    after["nodes"]["child"] = {
        "original": {"type": "path", "path": "/nix/store/aaaaaaaa-child"},
        "locked": {
            "type": "path",
            "path": "/nix/store/aaaaaaaa-child",
            "narHash": "sha256-child",
        },
    }
    assert _authorize(lock_module, before, after)["addedNodes"] == ["child"]

    after["nodes"]["child"]["original"]["path"] = "relative"
    after["nodes"]["child"]["locked"]["path"] = "relative"
    with pytest.raises(paths.ConfigError, match="immutable store path"):
        _authorize(lock_module, before, after)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("url", "https://github.com/example/other.git", "original identity changed"),
        ("ref", "refs/heads/other", "repository, ref, or selector"),
        ("dir", "subflake", "original identity changed"),
    ],
)
def test_selected_repository_ref_and_dir_drift_are_rejected(modules, field, value, message):
    lock_module, paths = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    after["nodes"]["domain-adapter"]["locked"][field] = value
    if field in {"url", "dir"}:
        after["nodes"]["domain-adapter"]["original"][field] = value

    with pytest.raises(paths.ConfigError, match=message):
        _authorize(lock_module, before, after)


def test_reordered_or_digest_tampered_authorization_is_rejected(modules):
    lock_module, paths = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    after["nodes"]["domain-adapter"]["inputs"].update(
        {"a-child": "a-child", "z-child": "z-child"}
    )
    after["nodes"]["a-child"] = _remote_file("a-child")
    after["nodes"]["z-child"] = _remote_file("z-child")
    authorization = _authorize(lock_module, before, after)

    reordered = copy.deepcopy(authorization)
    reordered["candidateOwnedNodes"].reverse()
    with pytest.raises(paths.ConfigError, match="not canonical"):
        lock_module.validate_owned_closure_authorization(reordered)

    tampered = copy.deepcopy(authorization)
    tampered["authorizationDigest"] = "0" * 64
    with pytest.raises(paths.ConfigError, match="authorization digest"):
        lock_module.validate_owned_closure_authorization(tampered)


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda lock: lock.update(nodes=[]), "root, version, or nodes"),
        (lambda lock: lock.update(root="missing"), "root, version, or nodes"),
        (lambda lock: lock.update(version=True), "root, version, or nodes"),
        (lambda lock: lock["nodes"].update(broken=[]), "node identity"),
        (
            lambda lock: lock["nodes"]["domain-adapter"].update(inputs=[]),
            "inputs mapping",
        ),
        (
            lambda lock: lock["nodes"]["domain-adapter"].update(inputs=None),
            "inputs mapping",
        ),
        (
            lambda lock: lock["nodes"]["domain-adapter"].update(inputs={"": "nixpkgs"}),
            "input identity",
        ),
        (
            lambda lock: lock["nodes"]["domain-adapter"].update(inputs={"bad": []}),
            "follows path",
        ),
        (
            lambda lock: lock["nodes"]["domain-adapter"].update(inputs={"bad": 7}),
            "input reference",
        ),
        (
            lambda lock: lock["nodes"]["domain-adapter"].update(inputs={"bad": ["missing"]}),
            "follows path is unresolved",
        ),
    ],
)
def test_malformed_lock_graph_shapes_fail_closed(modules, mutate, message):
    lock_module, paths = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    mutate(after)

    with pytest.raises(paths.ConfigError, match=message):
        _authorize(lock_module, before, after)


def test_self_referential_follows_path_fails_closed(modules):
    lock_module, paths = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    after["nodes"]["domain-adapter"]["inputs"]["shared"] = [
        "domain-adapter",
        "shared",
    ]

    with pytest.raises(paths.ConfigError, match="follows path is cyclic"):
        _authorize(lock_module, before, after)


def test_nested_and_recursive_follows_resolution_is_exact(modules):
    lock_module, _ = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    for lock in (before, after):
        lock["nodes"]["root"]["inputs"]["shared"] = ["nixpkgs"]
        lock["nodes"]["domain-adapter"]["inputs"]["shared"] = ["shared"]
        lock["nodes"]["domain-adapter"]["inputs"]["nested"] = [
            "domain-adapter",
            "shared",
        ]

    authorization = _authorize(lock_module, before, after)

    assert authorization["preimageOwnedNodes"] == ["domain-adapter"]


def test_added_child_cannot_follow_owned_target(modules):
    lock_module, paths = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    after["nodes"]["domain-adapter"]["inputs"]["child"] = "child"
    after["nodes"]["child"] = _remote_file("child")
    after["nodes"]["child"]["inputs"] = {"owner": ["domain-adapter"]}

    with pytest.raises(paths.ConfigError, match="follows an owned"):
        _authorize(lock_module, before, after)


@pytest.mark.parametrize(
    "node,message",
    [
        (
            {
                "original": {"type": "file", "url": "https://example.invalid/file"},
                "locked": {"type": "file", "url": "https://example.invalid/file"},
            },
            "content identity",
        ),
        (
            {
                "original": {"type": "indirect", "id": "bad identity"},
                "locked": {
                    "type": "github",
                    "owner": "example",
                    "repo": "child",
                    "rev": "d" * 40,
                    "narHash": "sha256-x",
                },
            },
            "indirect source identity is malformed",
        ),
        (
            {
                "original": {"type": "indirect", "id": "child"},
                "locked": {
                    "type": "indirect",
                    "id": "child",
                    "narHash": "sha256-x",
                },
            },
            "indirect source identity is unresolved",
        ),
        (
            {
                "original": {"type": "file", "url": "https://example.invalid/file"},
                "locked": {
                    "type": "tarball",
                    "url": "https://example.invalid/file",
                    "narHash": "sha256-x",
                },
            },
            "types disagree",
        ),
        (
            {
                "original": {"type": "git", "url": REPOSITORY_URL},
                "locked": {
                    "type": "git",
                    "url": REPOSITORY_URL,
                    "rev": "not-a-revision",
                    "narHash": "sha256-x",
                },
            },
            "Git source revision",
        ),
        (
            {
                "original": {"type": "github", "owner": "bad owner", "repo": "child"},
                "locked": {
                    "type": "github",
                    "owner": "bad owner",
                    "repo": "child",
                    "rev": "d" * 40,
                    "narHash": "sha256-x",
                },
            },
            "hosted source identity",
        ),
        (
            {
                "original": {"type": "file", "url": "http://example.invalid/file"},
                "locked": {
                    "type": "file",
                    "url": "http://example.invalid/file",
                    "narHash": "sha256-x",
                },
            },
            "remote source URL is unsafe",
        ),
        (
            {
                "original": {"type": "file", "url": "https://example.invalid/file#part"},
                "locked": {
                    "type": "file",
                    "url": "https://example.invalid/file#part",
                    "narHash": "sha256-x",
                },
            },
            "remote source URL is unsafe",
        ),
        (
            {
                "original": {"type": "unknown"},
                "locked": {"type": "unknown", "narHash": "sha256-x"},
            },
            "unsupported or not immutable",
        ),
    ],
)
def test_additional_owned_source_failures_are_closed(modules, node, message):
    lock_module, paths = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    after["nodes"]["domain-adapter"]["inputs"]["child"] = "child"
    after["nodes"]["child"] = node

    with pytest.raises(paths.ConfigError, match=message):
        _authorize(lock_module, before, after)


def test_safe_child_dir_and_hashed_tarball_are_accepted(modules):
    lock_module, _ = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    url = "https://example.invalid/archive.tar.gz?release=stable"
    after["nodes"]["domain-adapter"]["inputs"]["child"] = "child"
    after["nodes"]["child"] = {
        "original": {"type": "tarball", "url": url, "dir": "flake/root"},
        "locked": {
            "type": "tarball",
            "url": url,
            "dir": "flake/root",
            "narHash": "sha256-child",
        },
    }

    assert _authorize(lock_module, before, after)["addedNodes"] == ["child"]


@pytest.mark.parametrize("directory", ["../escape", "/absolute", "bad//path"])
def test_unsafe_child_dir_is_rejected(modules, directory):
    lock_module, paths = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    node = _remote_file("child")
    node["original"]["dir"] = directory
    node["locked"]["dir"] = directory
    after["nodes"]["domain-adapter"]["inputs"]["child"] = "child"
    after["nodes"]["child"] = node

    with pytest.raises(paths.ConfigError, match="unsafe dir selector"):
        _authorize(lock_module, before, after)


def test_authorization_record_shape_schema_selected_and_digest_validation(modules):
    lock_module, paths = modules
    authorization = _authorize(lock_module, _lock(OLD_REVISION), _lock(REVISION))

    mutations = [
        (lambda value: value.pop("addedNodes"), "fields"),
        (lambda value: value.update(schema="future"), "schema"),
        (lambda value: value.update(selectedNode=""), "selected node"),
        (lambda value: value.update(outsideGraphDigest="x"), "digest"),
    ]
    for mutate, message in mutations:
        invalid = copy.deepcopy(authorization)
        mutate(invalid)
        with pytest.raises(paths.ConfigError, match=message):
            lock_module.validate_owned_closure_authorization(invalid)


def test_root_version_selected_identity_and_preimage_revision_fail_closed(modules):
    lock_module, paths = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    after["version"] = 8
    with pytest.raises(paths.ConfigError, match="root or version changed"):
        _authorize(lock_module, before, after)

    with pytest.raises(paths.ConfigError, match="selected lock node identity"):
        lock_module.authorize_owned_lock_transition(
            _lock(OLD_REVISION),
            _lock(REVISION),
            "root",
            REPOSITORY_URL,
            REVISION,
        )

    before = _lock(OLD_REVISION)
    before["nodes"]["domain-adapter"]["locked"]["rev"] = None
    with pytest.raises(paths.ConfigError, match="preimage revision"):
        _authorize(lock_module, before, _lock(REVISION))


def test_valid_hosted_child_and_mutable_original_store_path_cases(modules):
    lock_module, paths = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    after["nodes"]["domain-adapter"]["inputs"]["child"] = "child"
    after["nodes"]["child"] = _outside_node("child")
    assert _authorize(lock_module, before, after)["addedNodes"] == ["child"]

    after["nodes"]["child"] = {
        "original": {"type": "path", "path": "/tmp/mutable"},
        "locked": {
            "type": "path",
            "path": "/nix/store/aaaaaaaa-child",
            "narHash": "sha256-child",
        },
    }
    with pytest.raises(paths.ConfigError, match="mutable absolute path"):
        _authorize(lock_module, before, after)


def test_recursive_follows_and_missing_source_type_fail_closed(modules):
    lock_module, paths = modules
    before = _lock(OLD_REVISION)
    after = _lock(REVISION)
    after["nodes"]["root"]["inputs"]["recursive"] = ["recursive"]
    with pytest.raises(paths.ConfigError, match="cyclic or empty"):
        _authorize(lock_module, before, after)

    after = _lock(REVISION)
    after["nodes"]["domain-adapter"]["inputs"]["child"] = "child"
    after["nodes"]["child"] = {
        "original": {"url": "https://example.invalid/file"},
        "locked": {
            "type": "file",
            "url": "https://example.invalid/file",
            "narHash": "sha256-child",
        },
    }
    with pytest.raises(paths.ConfigError, match="source type is invalid"):
        _authorize(lock_module, before, after)
