# nix-darwin-test-source-target: libexec/nix_darwin_local/deployment_plan.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/projection.py
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import pytest


def canonical(document):
    return (json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


@pytest.fixture
def modules(repo_root: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    from nix_darwin_local import deployment_plan, paths, projection

    return deployment_plan, paths, projection


def minimal_repository_identity(selected_path: str = "/fixture/flake") -> dict:
    nix_file_id = {
        "path": f"{selected_path}/flake.nix",
        "mode": 0o644,
        "uid": os.getuid(),
        "gid": os.getgid(),
        "kind": "regular",
        "link_count": 1,
        "sha256": "0" * 64,
        "size": 10,
    }
    lock_file_id = {
        "path": f"{selected_path}/flake.lock",
        "mode": 0o644,
        "uid": os.getuid(),
        "gid": os.getgid(),
        "kind": "regular",
        "link_count": 1,
        "sha256": "1" * 64,
        "size": 20,
    }
    return {
        "repository_root": str(Path(selected_path).parent),
        "commit": "a" * 40,
        "tree": "b" * 40,
        "branch": "main",
        "status": "",
        "operation_state": "clean",
        "index_sha256": "c" * 64,
        "flake_nix": json.dumps(nix_file_id, sort_keys=True, separators=(",", ":")),
        "flake_lock": json.dumps(lock_file_id, sort_keys=True, separators=(",", ":")),
        "ignored_status_sha256": "d" * 64,
        "ignored_status_count": "0",
    }


def minimal_plan(module):
    return {
        "schema": module.SCHEMA,
        "plan_digest": "",
        "created_at": "2026-08-25T00:00:00Z",
        "status": "BLOCKED",
        "blockers": ["fixture"],
        "preflight_only": False,
        "tool": {}, "environment": {},
        "selected_flake": {
            "path": "/fixture/flake",
            "host": "FixtureHost",
            "repository": minimal_repository_identity("/fixture/flake"),
        },
        "lock": {}, "allowed_lock_nodes": [],
        "control_snapshot": {}, "machine_control_snapshot": {}, "control_projection": {},
        "evaluation_root": "/fixture/eval", "evaluation_current": {},
        "projection": {}, "input_snapshots": [], "overrides": [], "checks": [],
        "target": {"derivation_output": "/nix/store/fixture-target", "closure": []},
        "host_candidates": [], "live": {}, "closure_delta": {}, "external_state": [],
        "state_probes": [], "control_transactions": [], "participants": [],
        "participant_context": {}, "participant_preflight": [],
        "profile_policy": {"profile": "/fixture/profile", "live": "/fixture/live", "generation_glob": "/fixture/system-*-link"}, "publish_local_config": False,
        "rollback_policy": {"pre_switch": "restore-all", "post_switch": "participant-local-reverse"},
        "confirmation": {}, "observations": {}, "mutable_state_comparison": {},
        "command_evidence_policy": {},
    }


def test_plan_serialization_is_canonical_digest_bound_and_owner_only(modules, tmp_path: Path):
    plan, _, _ = modules
    artifact = plan.write_plan(tmp_path / "plan", minimal_plan(plan), "digest {PLAN_DIGEST}\n")

    raw = (artifact.directory / "plan.json").read_bytes()
    assert raw == plan.canonical_bytes(json.loads(raw))
    assert artifact.digest in (artifact.directory / "plan.md").read_text()
    assert os.stat(artifact.directory).st_mode & 0o777 == 0o700
    assert os.stat(artifact.directory / "plan.json").st_mode & 0o777 == 0o600
    assert plan.load_plan(artifact.directory).digest == artifact.digest


@pytest.mark.parametrize("defect", [None, "missing", "revision", "nar", "repository", "mutable"])
def test_v3_plan_roundtrip_binds_exact_local_sources(modules, tmp_path, defect):
    plan, paths, _ = modules
    revision = "a" * 40
    nar = "sha256-" + "A" * 43 + "="
    repository = "https://example.invalid/core.git"
    source = dict(key="core", checkout="/fixture/core", repository=repository,
                  release_ref="refs/heads/main", revision=revision, head_ref="refs/heads/main",
                  head_revision=revision, clean=True, operation_state="clean",
                  store_path="/nix/store/" + "a" * 32 + "-source", nar_hash=nar, publication=None)
    document = minimal_plan(plan)
    document.update(schema="nix-update-deployment-plan-v3", local_sources=[{"node": "core", "source": source}],
                    lock={"nodes": {"core": {"locked": {"type": "git", "url": repository, "rev": revision, "narHash": nar}}}})
    if defect == "missing": document.pop("local_sources")
    elif defect == "revision": source["revision"] = "b" * 40
    elif defect == "nar": source["nar_hash"] = "sha256-" + "B" * 43 + "="
    elif defect == "repository": source["repository"] = "https://example.invalid/other.git"
    elif defect == "mutable": source["store_path"] = "/fixture/core"
    if defect is not None:
        with pytest.raises(paths.ConfigError):
            plan.write_plan(tmp_path / "plan", document, "fixture")
        return
    artifact = plan.write_plan(tmp_path / "plan", document, "fixture")
    loaded = plan.load_plan(artifact.directory)
    assert loaded.document["local_sources"] == document["local_sources"]
    assert loaded.digest == artifact.digest


@pytest.mark.parametrize("mutation", ["bytes", "digest", "noncanonical"])
def test_plan_byte_digest_and_canonical_drift_are_rejected(modules, tmp_path: Path, mutation: str):
    plan, paths, _ = modules
    artifact = plan.write_plan(tmp_path / "plan", minimal_plan(plan), "plan\n")
    document = artifact.document
    if mutation == "bytes":
        document["created_at"] = "2026-08-25T00:00:01Z"
    elif mutation == "digest":
        document["plan_digest"] = "0" * 64
    else:
        (artifact.directory / "plan.json").write_text(json.dumps(document, indent=2) + "\n")
        with pytest.raises(paths.ConfigError, match="not canonical"):
            plan.load_plan(artifact.directory)
        return
    (artifact.directory / "plan.json").write_bytes(plan.canonical_bytes(document))
    with pytest.raises(paths.ConfigError, match="digest"):
        plan.load_plan(artifact.directory)


@pytest.mark.parametrize(("mutation", "message"), [("schema", "plan schema"), ("missing-digest", "digest is missing")])
def test_plan_load_rejects_schema_and_digest_shape_before_content_validation(
    modules, tmp_path: Path, mutation, message
):
    plan, paths, _ = modules
    artifact = plan.write_plan(tmp_path / mutation, minimal_plan(plan), "plan\n")
    document = artifact.document
    if mutation == "schema":
        document["schema"] = "future"
    else:
        document["plan_digest"] = None
    (artifact.directory / "plan.json").write_bytes(plan.canonical_bytes(document))
    with pytest.raises(paths.ConfigError, match=message):
        plan.load_plan(artifact.directory)


def test_plan_and_result_reject_secret_bearing_keys(modules, tmp_path: Path):
    plan, paths, _ = modules
    document = minimal_plan(plan)
    document["api_token"] = "sentinel"

    with pytest.raises(paths.ConfigError, match="secret-bearing"):
        plan.write_plan(tmp_path / "plan", document, "plan\n")
    with pytest.raises(paths.ConfigError, match="secret-bearing"):
        plan.write_result(tmp_path / "result", {"password": "sentinel"})
    assert plan.redact_text("token=sentinel password: other") == "token=[REDACTED] password=[REDACTED]"
    assert plan.redact_text("api_token=sentinel") == "api_token=[REDACTED]"
    assert plan.redact_text("NIX_CONFIG=access_token=sentinel") == "NIX_CONFIG=access_token=[REDACTED]"
    assert plan.redact_text("Authorization: Bearer sentinel") == "Authorization: Bearer [REDACTED]"
    assert plan.redact_text("X-Api-Key: sentinel") == "X-Api-Key=[REDACTED]"
    assert plan.redact_text("Authorization: Basic sentinel") == "Authorization: Basic [REDACTED]"
    assert plan.redact_text("Authorization: Token sentinel") == "Authorization: Token [REDACTED]"
    assert plan.redact_text("Authorization: Digest sentinel") == "Authorization: Digest [REDACTED]"
    assert plan.redact_text("access_key=sentinel access-key: other") == "access_key=[REDACTED] access-key=[REDACTED]"
    assert plan.redact_text('{"access_key":"sentinel"}') == '{"access_key":"[REDACTED]"}'
    assert plan.redact_text('{"authorization":"Bearer sentinel"}') == '{"authorization":"[REDACTED]"}'
    assert "SENTINEL" not in plan.redact_text("{\"authorization\":\"Bearer O'ReillySENTINEL\"}")
    assert "SENTINEL" not in plan.redact_text(r'{"authorization":"Bearer abc\"SENTINEL"}')
    assert "SENTINEL" not in plan.redact_text(r'{"\u0061uthorization":"Bearer SENTINEL"}')
    assert "SENTINEL" not in plan.redact_text(r'{"access\u005fkey":"SENTINEL"}')
    assert "SENTINEL" not in plan.redact_text('{"authorization":"Bearer SENTINEL')
    assert plan.redact_text("AWS_ACCESS_KEY_ID=AKIAFIXTURE") == "AWS_ACCESS_KEY_ID=[REDACTED]"
    assert plan.redact_text("-----BEGIN PRIVATE KEY-----\nvalue\n") == "[REDACTED PRIVATE KEY OUTPUT]\n"

    with pytest.raises(paths.ConfigError, match="secret-bearing value"):
        plan.write_result(tmp_path / "result-value", {"output": "token=sentinel"})
    with pytest.raises(paths.ConfigError, match="secret-bearing value"):
        plan.write_result(
            tmp_path / "result-quoted-authorization",
            {"failure": r'{"authorization":"Bearer abc\"SENTINEL"}'},
        )
    with pytest.raises(paths.ConfigError, match="secret-bearing value"):
        plan.write_result(
            tmp_path / "result-escaped-authorization",
            {"failure": r'{"\u0061uthorization":"Bearer SENTINEL"}'},
        )

    assert plan.redact_text("https://user:pass@example.invalid/repo") == "https://[REDACTED]@example.invalid/repo"
    boundary = ("x" * 31 + '{"authorization":"Bearer sentinel"}').encode()
    assert "sentinel" not in plan.redacted_summary(boundary, 32)
    assert len(plan.redacted_summary(boundary, 32).encode()) <= 32
    with pytest.raises(paths.ConfigError, match="credential-bearing URL"):
        plan.write_result(tmp_path / "result-url", {"remote": "https://user:pass@example.invalid/repo"})


def test_secret_filter_allows_generic_git_ssh_transport_but_rejects_userinfo(modules):
    plan, paths, _ = modules
    safe_urls = (
        "ssh://git@github.com/example/repository.git",
        "git+ssh://git@github.com/example/repository.git",
    )
    for url in safe_urls:
        assert plan.redact_text(url) == url
        plan.reject_secrets({"lock": {"nodes": {"fixture": {"locked": {"url": url}}}}})

    for url in (
        "ssh://operator@github.com/example/repository.git",
        "ssh://GIT@github.com/example/repository.git",
        "ssh://git:sentinel@github.com/example/repository.git",
        "ssh://git@evil:sentinel@github.com/example/repository.git",
        "https://user:sentinel@example.invalid/repository.git",
    ):
        assert "[REDACTED]@" in plan.redact_text(url)
        with pytest.raises(paths.ConfigError, match="credential-bearing URL"):
            plan.reject_secrets({"url": url})


def test_plan_load_rejects_permission_drift(modules, tmp_path: Path):
    plan, paths, _ = modules
    artifact = plan.write_plan(tmp_path / "plan", minimal_plan(plan), "plan\n")
    (artifact.directory / "plan.json").chmod(0o640)

    with pytest.raises(paths.ConfigError, match="owner-only"):
        plan.load_plan(artifact.directory)


def test_plan_schema_empty_directory_and_optional_companion_guards(modules, tmp_path: Path):
    plan, paths, _ = modules
    wrong = minimal_plan(plan)
    wrong["schema"] = "future"
    with pytest.raises(paths.ConfigError, match="plan schema"):
        plan.write_plan(tmp_path / "wrong", wrong, "plan\n")

    occupied = tmp_path / "occupied"
    occupied.mkdir(mode=0o700)
    (occupied / "existing").write_text("keep")
    with pytest.raises(paths.ConfigError, match="not empty"):
        plan.write_plan(occupied, minimal_plan(plan), "plan\n")

    artifact = plan.write_plan(tmp_path / "without-companion", minimal_plan(plan), "plan\n")
    (artifact.directory / "plan.md").unlink()
    assert plan.load_plan(artifact.directory / "plan.json").digest == artifact.digest


def write_lock(path: Path, nodes: dict, root="root"):
    path.write_text(json.dumps({"nodes": nodes, "root": root, "version": 7}), encoding="utf-8")


def test_normalized_lock_delta_binds_metadata_and_rejects_unrelated_nodes(modules, tmp_path: Path):
    plan, paths, _ = modules
    before_path = tmp_path / "before.lock"
    after_path = tmp_path / "after.lock"
    write_lock(before_path, {"root": {"inputs": {"a": "a", "b": "b"}}, "a": {"locked": {"narHash": "old"}}, "b": {"locked": {"lastModified": 1}}})
    write_lock(after_path, {"root": {"inputs": {"a": "a", "b": "b"}}, "a": {"locked": {"narHash": "new"}}, "b": {"locked": {"lastModified": 2}}})
    changes = plan.lock_delta(plan.normalized_lock(before_path), plan.normalized_lock(after_path))

    assert [item["node"] for item in changes] == ["a", "b"]
    with pytest.raises(paths.ConfigError, match="unauthorized node: b"):
        plan.reject_unapproved_lock_delta(changes, ["a"])
    plan.reject_unapproved_lock_delta(changes, ["a", "b"])


@pytest.mark.parametrize("defect", ["json", "shape", "node"])
def test_normalized_lock_rejects_malformed_documents(modules, tmp_path: Path, defect):
    plan, paths, _ = modules
    lock = tmp_path / "flake.lock"
    if defect == "json":
        lock.write_text("{")
    elif defect == "shape":
        lock.write_text("[]\n")
    else:
        lock.write_text(json.dumps({"nodes": {"bad": []}, "root": "bad", "version": 7}))
    with pytest.raises(paths.ConfigError):
        plan.normalized_lock(lock)


def test_lock_root_and_version_movement_requires_exact_authority(modules, tmp_path: Path):
    plan, paths, _ = modules
    before = tmp_path / "before"
    after = tmp_path / "after"
    write_lock(before, {"root": {}}, root="root")
    write_lock(after, {"root": {}}, root="other")
    changes = plan.lock_delta(plan.normalized_lock(before), plan.normalized_lock(after))

    assert changes[0]["node"] == "$lock-metadata"
    with pytest.raises(paths.ConfigError):
        plan.reject_unapproved_lock_delta(changes, [])


def test_incident_git_archive_identity_is_not_path_nar_identity(modules):
    plan, _, _ = modules
    locked_path_nar = "sha256-ox1mfzrczX4dN38Ak7kdF2l7PnY9CGhFiqVxpkk4hGw="
    fixed_commit_archive_nar = "sha256-2AByg60sNDfJD+wkPMr26rMy+KsrRx6uP2D3fXZG1oo="
    fixed_commit = "5538c8013b1ca022f8d0c06c60753586b6a0bf5a"

    assert locked_path_nar != fixed_commit_archive_nar
    assert len(fixed_commit) == 40
    assert plan.sha256_bytes(fixed_commit.encode()) not in {locked_path_nar, fixed_commit_archive_nar}


def test_contract_accepts_exact_source_kinds_and_rejects_mutable_ambiguous_path(modules, tmp_path: Path):
    plan, paths, _ = modules
    valid = {
        "schema": plan.CONTRACT_SCHEMA,
        "profile_policy": {
            "profile": "/nix/var/nix/profiles/system",
            "live": "/run/current-system",
            "generation_glob": "/nix/var/nix/profiles/system-*-link",
        },
        "sources": [{"name": "fixed", "input": "fixed", "lock_node": "fixed", "kind": "immutable-snapshot", "path": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-example-source", "nar_hash": "sha256-example="}],
    }
    path = tmp_path / "contract.json"
    path.write_bytes(canonical(valid))
    assert plan.load_contract(path)["sources"][0]["kind"] == "immutable-snapshot"

    valid["sources"][0]["kind"] = "path"
    path.write_bytes(canonical(valid))
    with pytest.raises(paths.ConfigError, match="source kind"):
        plan.load_contract(path)


@pytest.mark.parametrize("defect", ["schema", "list", "boolean", "string-list"])
def test_contract_top_level_types_fail_closed(modules, tmp_path: Path, defect):
    plan, paths, _ = modules
    document = {
        "schema": plan.CONTRACT_SCHEMA,
        "profile_policy": {
            "profile": "/profile", "live": "/live", "generation_glob": "/generation-*",
        },
    }
    if defect == "schema":
        document["schema"] = "future"
    elif defect == "list":
        document["participants"] = {}
    elif defect == "boolean":
        document["publish_local_config"] = "yes"
    else:
        document["additional_hosts"] = [7]
    path = tmp_path / "contract.json"
    path.write_bytes(canonical(document))
    with pytest.raises(paths.ConfigError):
        plan.load_contract(path)


def test_scratch_projection_is_private_and_never_publishes_current(modules, tmp_path: Path):
    _, _, projection = modules
    control = tmp_path / "control"
    control.mkdir()
    (control / "module.toml").write_text('[public]\nvalue = "visible"\n[private]\nvalue = "hidden"\n')
    eval_root = tmp_path / "eval"
    eval_root.mkdir()
    current = eval_root / "current"
    current.symlink_to("existing")

    result = projection.render_scratch(control, tmp_path / "scratch", {"module.toml": ["public.value"]})

    assert current.readlink() == Path("existing")
    assert os.stat(result.root).st_mode & 0o777 == 0o700
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in result.root.iterdir())
    assert "hidden" not in (result.root / "module.toml").read_text()
    assert projection.inspect_scratch(result.root) == result


def test_file_and_tree_identities_bind_symlink_topology_and_missing_policy(modules, tmp_path: Path):
    plan, paths, _ = modules
    root = tmp_path / "root"
    root.mkdir()
    target = root / "target"
    target.write_text("managed")
    link = root / "link"
    link.symlink_to("target")

    identity = plan.file_identity(link)
    assert identity["kind"] == "symlink"
    assert identity["target"] == "target"
    assert any(
        item["kind"] == "symlink"
        for item in plan.tree_identity(root)["content_identity"]["entries"]
    )
    with pytest.raises(paths.ConfigError, match="missing or not a directory"):
        plan.tree_identity(tmp_path / "missing")
    with pytest.raises(paths.ConfigError, match="declared volatile entry is missing"):
        plan.tree_identity(root, [{
            "path": "absent", "kind": "regular", "uid": os.getuid(), "gid": os.getgid(),
            "mode": 0o600, "producer": "fixture", "content_policy": {"sha256": "a" * 64},
        }])


def test_tree_aliases_share_content_but_bind_independent_lexical_root_topology(modules, tmp_path: Path):
    plan, _, _ = modules
    target = tmp_path / "target"
    target.mkdir()
    (target / "file").write_text("same\n")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.symlink_to("target", target_is_directory=True)
    second.symlink_to("./target", target_is_directory=True)

    first_identity = plan.tree_identity(first)
    second_identity = plan.tree_identity(second)
    assert first_identity["content_identity"]["digest"] == second_identity["content_identity"]["digest"]
    assert first_identity["root_identity"]["target"] == "target"
    assert second_identity["root_identity"]["target"] == "./target"
    assert first_identity["digest"] != second_identity["digest"]


def test_tree_alias_retarget_and_symlink_to_directory_replacement_change_complete_identity(modules, tmp_path: Path):
    plan, _, _ = modules
    first_target = tmp_path / "target-one"
    second_target = tmp_path / "target-two"
    for target in (first_target, second_target):
        target.mkdir()
        (target / "file").write_text("same\n")
    alias = tmp_path / "alias"
    alias.symlink_to(first_target.name, target_is_directory=True)
    initial = plan.tree_identity(alias)
    alias.unlink()
    alias.symlink_to(second_target.name, target_is_directory=True)
    retargeted = plan.tree_identity(alias)
    assert initial["content_identity"]["digest"] == retargeted["content_identity"]["digest"]
    assert initial["digest"] != retargeted["digest"]
    alias.unlink()
    alias.mkdir()
    (alias / "file").write_text("same\n")
    replaced = plan.tree_identity(alias)
    assert replaced["root_identity"]["kind"] == "directory"
    assert replaced["content_identity"]["digest"] == initial["content_identity"]["digest"]
    assert replaced["digest"] != initial["digest"]


@pytest.mark.parametrize("drift", ["mode", "owner", "link-count"])
def test_tree_root_metadata_drift_changes_complete_digest(modules, tmp_path: Path, monkeypatch, drift: str):
    plan, _, _ = modules
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    (root / "file").write_text("same\n")
    initial = plan.tree_identity(root)
    if drift == "mode":
        root.chmod(0o750)
    else:
        original_lstat = Path.lstat

        def drifted_lstat(path):
            metadata = original_lstat(path)
            if path == root:
                fields = list(metadata)
                fields[4 if drift == "owner" else 3] += 1
                return os.stat_result(fields)
            return metadata

        monkeypatch.setattr(Path, "lstat", drifted_lstat)
    changed = plan.tree_identity(root)
    assert changed["content_identity"]["digest"] == initial["content_identity"]["digest"]
    assert changed["root_identity"] != initial["root_identity"]
    assert changed["digest"] != initial["digest"]


def test_tree_volatile_marker_is_exact_and_shared_only_as_content_across_aliases(modules, tmp_path: Path):
    plan, paths, _ = modules
    target = tmp_path / "target"
    target.mkdir()
    marker = target / "marker"
    marker.write_text("managed\n")
    marker.chmod(0o600)
    metadata = marker.lstat()
    policy = [{
        "path": "marker", "kind": "regular", "uid": metadata.st_uid,
        "gid": metadata.st_gid, "mode": 0o600, "producer": "fixture",
        "content_policy": {"sha256": hashlib.sha256(marker.read_bytes()).hexdigest()},
    }]
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.symlink_to("target", target_is_directory=True)
    second.symlink_to("./target", target_is_directory=True)
    first_identity = plan.tree_identity(first, policy)
    second_identity = plan.tree_identity(second, policy)
    assert first_identity["content_identity"] == second_identity["content_identity"]
    assert first_identity["digest"] != second_identity["digest"]
    marker.write_text("drift\n")
    with pytest.raises(paths.ConfigError, match="unexpected content"):
        plan.tree_identity(first, policy)


def test_tree_undeclared_descendant_drift_is_digest_bound(modules, tmp_path: Path):
    plan, _, _ = modules
    root = tmp_path / "root"
    root.mkdir()
    (root / "reviewed").write_text("reviewed\n")
    reviewed = plan.tree_identity(root)
    (root / "undeclared").write_text("drift\n")
    current = plan.tree_identity(root)
    assert current["content_identity"]["digest"] != reviewed["content_identity"]["digest"]
    assert current["digest"] != reviewed["digest"]


@pytest.mark.parametrize("drift", ["content", "mode", "extra"])
def test_scratch_projection_inspection_rejects_byte_or_topology_drift(modules, tmp_path: Path, drift):
    _, paths, projection = modules
    control = tmp_path / "control"
    control.mkdir()
    (control / "module.toml").write_text('value = "ok"\n')
    result = projection.render_scratch(control, tmp_path / "scratch", {"module.toml": ["value"]})
    if drift == "content":
        (result.root / "module.toml").write_text('value = "drift"\n')
        assert projection.inspect_scratch(result.root).digest != result.digest
        return
    if drift == "mode":
        (result.root / "module.toml").chmod(0o640)
    else:
        (result.root / "extra").write_text("drift")
        (result.root / "extra").chmod(0o640)
    with pytest.raises(paths.ConfigError, match="topology or mode"):
        projection.inspect_scratch(result.root)


@pytest.mark.parametrize(
    ("extra", "allowlist", "message"),
    [
        ("unknown.toml", {"module.toml": ["value"]}, "unknown TOML"),
        (None, {"module.toml": ["missing"]}, "allowlisted key is missing"),
        (None, {"nix-darwin.toml": ["flake.active"]}, "control TOML"),
    ],
)
def test_scratch_projection_rejects_unknown_missing_and_control_content(modules, tmp_path: Path, extra, allowlist, message):
    _, paths, projection = modules
    control = tmp_path / "control"
    control.mkdir()
    (control / "module.toml").write_text('value = "ok"\n')
    (control / "nix-darwin.toml").write_text('[flake]\nactive = ""\n')
    if extra:
        (control / extra).write_text('value = "unexpected"\n')
    if "nix-darwin.toml" in allowlist:
        (control / "module.toml").unlink()

    with pytest.raises(paths.ConfigError, match=message):
        projection.render_scratch(control, tmp_path / "scratch", allowlist)


def test_volatile_marker_policy_is_alias_consistent_but_drift_remains_visible(modules, tmp_path: Path):
    plan, paths, _ = modules
    skills = tmp_path / "skills"
    (skills / ".system").mkdir(parents=True)
    marker = skills / ".system/.codex-system-skills.marker"
    marker.write_text("managed\n")
    marker.chmod(0o600)
    marker_metadata = marker.lstat()
    policy = [{
        "path": ".system/.codex-system-skills.marker",
        "kind": "regular",
        "uid": marker_metadata.st_uid,
        "gid": marker_metadata.st_gid,
        "mode": 0o600,
        "producer": "runtime-system-skills",
        "content_policy": {"sha256": hashlib.sha256(b"managed\n").hexdigest()},
    }]
    alias = tmp_path / "alias"
    alias.symlink_to(skills)

    first = plan.tree_identity(skills, policy)
    second = plan.tree_identity(alias, policy)
    assert first["content_identity"]["digest"] == second["content_identity"]["digest"]
    assert first["digest"] != second["digest"]

    (skills / "undeclared").write_text("drift")
    assert plan.tree_identity(skills, policy)["digest"] != first["digest"]
    marker.chmod(0o644)
    with pytest.raises(paths.ConfigError, match="unexpected mode"):
        plan.tree_identity(skills, policy)


@pytest.mark.parametrize(
    ("items", "message"),
    [
        (["not-object"], "each source"),
        ([{"name": "x", "input": "x", "lock_node": "x", "kind": "unknown", "nar_hash": "sha256-x", "path": "/x"}], "source kind"),
        ([{"name": "x", "input": "x", "lock_node": "x", "kind": "store-path", "nar_hash": 7, "store_path": "/x"}], "nar_hash"),
        ([{"name": "x", "input": "x", "lock_node": "x", "kind": "store-path", "nar_hash": "sha256-x"}], "exact source path"),
        ([{"name": "x", "input": "x", "lock_node": "x", "kind": "git-source", "nar_hash": "sha256-x", "path": "/x"}], "source kind"),
        ([{"name": "x", "input": "x", "lock_node": "x", "kind": "store-path", "nar_hash": "sha256-x", "store_path": "/x", "unknown": 1}], "unknown source key"),
    ],
)
def test_source_contract_validation_branches(modules, items, message):
    plan, paths, _ = modules
    with pytest.raises(paths.ConfigError, match=message):
        plan._validate_sources(items)


def test_source_contract_rejects_mutable_absolute_snapshot(modules, tmp_path: Path):
    plan, paths, _ = modules
    with pytest.raises(paths.ConfigError, match="immutable /nix/store"):
        plan._validate_sources([{
            "name": "mutable", "input": "mutable", "lock_node": "mutable", "kind": "immutable-snapshot",
            "path": str(tmp_path), "nar_hash": "sha256-example=",
        }])


def valid_transaction():
    empty_hash = hashlib.sha256(b"").hexdigest()
    return {
        "path": "/control/file", "safe_ancestors": ["/control"], "preimage_sha256": empty_hash, "preimage_bytes_b64": "",
        "postimage_sha256": empty_hash, "postimage_bytes_b64": "", "pre_kind": "regular", "pre_uid": 1,
        "pre_gid": 1, "pre_mode": 0o644, "pre_link_count": 1, "post_mode": 0o600,
        "metadata_policy": {"owner_group": "preserve", "xattrs": "require-absent", "acl": "require-absent", "flags": "require-zero"},
        "authorization": "after-confirmation-before-switch",
        "restore_before_switch": True,
    }


@pytest.mark.parametrize("defect", ["shape", "authorization", "metadata", "exact-owner", "duplicate-path"])
def test_transaction_contract_validation_branches(modules, defect):
    plan, paths, _ = modules
    item = valid_transaction()
    if defect == "shape":
        item["unknown"] = True
    elif defect == "authorization":
        item["authorization"] = "before-confirmation"
    elif defect == "metadata":
        item["metadata_policy"] = "loose"
    elif defect == "exact-owner":
        item["metadata_policy"] = "exact-owner-group"
    if defect == "duplicate-path":
        with pytest.raises(paths.ConfigError, match="path must be unique"):
            plan._validate_transactions([item, dict(item)])
        return
    with pytest.raises(paths.ConfigError):
        plan._validate_transactions([item])


def valid_participant():
    return {
        "name": "participant", "protocol": "nix-update-participant-v1", "path": "/nix/store/participant",
        "sha256": "a" * 64, "phases": ["preflight"], "mutation_authority": [], "rollback_authority": [],
        "timeout_seconds": 30,
        "provenance": {"kind": "target-closure", "identity": "/nix/store/system"},
    }


@pytest.mark.parametrize(
    "defect",
    [
        "object", "unknown", "protocol", "path", "sha", "provenance", "provenance-kind",
        "phases-type", "phases-empty", "phases-name", "mutation", "rollback",
        "duplicate-name", "pre-switch-no-rollback", "readonly-mutation",
    ],
)
def test_participant_contract_validation_branches(modules, defect):
    plan, paths, _ = modules
    item = valid_participant()
    if defect == "object":
        value = ["invalid"]
    else:
        if defect == "unknown": item["unknown"] = 1
        elif defect == "protocol": item["protocol"] = "v0"
        elif defect == "path": item["path"] = "relative"
        elif defect == "sha": item["sha256"] = "short"
        elif defect == "provenance": item["provenance"] = "untyped"
        elif defect == "provenance-kind": item["provenance"] = {"kind": "shell", "identity": "x"}
        elif defect == "phases-type": item["phases"] = "preflight"
        elif defect == "phases-empty": item["phases"] = []
        elif defect == "phases-name": item["phases"] = ["shell"]
        elif defect == "mutation": item["mutation_authority"] = [7]
        elif defect == "rollback": item["rollback_authority"] = "resident"
        elif defect == "pre-switch-no-rollback":
            item["phases"] = ["pre-switch"]
            item["mutation_authority"] = ["resident"]
        elif defect == "readonly-mutation": item["mutation_authority"] = ["resident"]
        value = [item, dict(item)] if defect == "duplicate-name" else [item]
    with pytest.raises(paths.ConfigError):
        plan._validate_participants(value)


@pytest.mark.parametrize("defect", [None, "missing-root", "relative-root", "wrong-root", "escape", "root-is-file"])
def test_operator_participant_contract_binds_exact_private_root(modules, defect):
    plan, paths, _ = modules
    item = valid_participant()
    item["path"] = "/private/participants/bin/participant"
    item["provenance"] = {
        "kind": "operator-artifact", "identity": f"sha256:{item['sha256']}",
        "root": "/private/participants",
    }
    if defect == "missing-root": del item["provenance"]["root"]
    elif defect == "relative-root": item["provenance"]["root"] = "private/participants"
    elif defect == "wrong-root": item["provenance"]["root"] = "/private/other"
    elif defect == "escape": item["path"] = "/private/participants/../escape"
    elif defect == "root-is-file": item["path"] = "/private/participants"
    if defect is None:
        plan._validate_participants([item])
    else:
        with pytest.raises(paths.ConfigError):
            plan._validate_participants([item])


@pytest.mark.parametrize("defect", ["source-path", "frozen-path", "identity-field", "identity-hash", "identity-mode"])
def test_loaded_operator_participant_requires_closed_source_and_frozen_identity(modules, defect):
    plan, paths, _ = modules
    digest = "a" * 64
    source_path = "/private/source/participant"
    frozen_path = "/private/state/participant-artifacts/artifact.participant"

    def identity(path):
        return {
            "path": path, "device": 1, "inode": 2, "uid": 3, "gid": 4,
            "mode": 0o700, "link_count": 1, "size": 5, "sha256": digest,
        }

    item = valid_participant()
    item["path"] = frozen_path
    item["provenance"] = {
        "kind": "operator-artifact", "identity": f"sha256:{digest}",
        "root": "/private/source", "source_path": source_path,
        "source_identity": identity(source_path),
        "frozen_root": "/private/state/participant-artifacts",
        "frozen_path": frozen_path, "frozen_identity": identity(frozen_path),
    }
    if defect == "source-path": item["provenance"]["source_path"] = "/private/other/participant"
    elif defect == "frozen-path": item["path"] = "/private/state/other"
    elif defect == "identity-field": item["provenance"]["source_identity"]["extra"] = True
    elif defect == "identity-hash": item["provenance"]["frozen_identity"]["sha256"] = "0" * 64
    else: item["provenance"]["frozen_identity"]["mode"] = 0o770
    with pytest.raises(paths.ConfigError):
        plan._validate_participants([item], require_frozen=True)


@pytest.mark.parametrize("defect", ["object", "unknown", "name-type", "name-empty", "kind", "path-type", "path-relative", "volatile-type", "volatile-file"])
def test_probe_contract_validation_branches(modules, defect):
    plan, paths, _ = modules
    item = {"name": "probe", "kind": "tree", "path": "/probe", "volatile": []}
    if defect == "object": value = ["invalid"]
    else:
        if defect == "unknown": item["unknown"] = 1
        elif defect == "name-type": item["name"] = 7
        elif defect == "name-empty": item["name"] = ""
        elif defect == "kind": item["kind"] = "device"
        elif defect == "path-type": item["path"] = 7
        elif defect == "path-relative": item["path"] = "relative"
        elif defect == "volatile-type": item["volatile"] = "glob"
        else: item.update(kind="file", volatile=[{}])
        value = [item]
    with pytest.raises(paths.ConfigError):
        plan._validate_probes(value, "probe")


@pytest.mark.parametrize("defect", ["object", "unknown", "missing", "relative", "action", "generation"])
def test_profile_contract_validation_branches(modules, defect):
    plan, paths, _ = modules
    value = {"profile": "/profile", "live": "/live", "generation_glob": "/generation-*"}
    if defect == "object": value = []
    elif defect == "unknown": value["unknown"] = True
    elif defect == "missing": value.pop("live")
    elif defect == "relative": value["profile"] = "relative"
    elif defect == "action": value["action"] = "replace-link"
    else: value["action"] = "realign-selected-profile"
    with pytest.raises(paths.ConfigError):
        plan._validate_profile_policy(value)


def test_json_helpers_cover_nonobject_unknown_lists_and_file_kinds(modules, tmp_path: Path):
    plan, paths, _ = modules
    bad = tmp_path / "bad.json"
    bad.write_text("[]")
    with pytest.raises(paths.ConfigError, match="JSON object"):
        plan._read_json_object(bad, "fixture")
    with pytest.raises(paths.ConfigError, match="unable to read"):
        plan._read_json_object(tmp_path / "missing", "fixture")
    plan._reject_secrets([{"safe": [1, "two"]}])
    assert plan._normalized_json([{"z": 1, "a": 2}]) == [{"a": 2, "z": 1}]
    directory = tmp_path / "directory"
    directory.mkdir()
    symlink = tmp_path / "symlink"
    symlink.symlink_to(directory)
    assert plan._kind(directory.stat().st_mode) == "directory"
    assert plan._kind(symlink.lstat().st_mode) == "symlink"


@pytest.mark.parametrize(
    "defect",
    [
        "wrong-hash", "malformed-base64", "relative", "missing-ancestor", "unsafe-pre-mode",
        "unsafe-post-mode", "wrong-owner-type", "wrong-group-type", "wrong-link-type", "metadata-policy",
    ],
)
def test_control_transaction_schema_and_type_fault_matrix(modules, defect):
    plan, paths, _ = modules
    item = valid_transaction()
    if defect == "wrong-hash": item["postimage_sha256"] = "0" * 64
    elif defect == "malformed-base64": item["postimage_bytes_b64"] = "%%%"
    elif defect == "relative": item["path"] = "relative"
    elif defect == "missing-ancestor": item["safe_ancestors"] = []
    elif defect == "unsafe-pre-mode": item["pre_mode"] = 0o666
    elif defect == "unsafe-post-mode": item["post_mode"] = 0o640
    elif defect == "wrong-owner-type": item["pre_uid"] = True
    elif defect == "wrong-group-type": item["pre_gid"] = "1"
    elif defect == "wrong-link-type": item["pre_link_count"] = False
    else: item["metadata_policy"] = {"owner_group": "preserve"}
    with pytest.raises(paths.ConfigError):
        plan._validate_transactions([item], "/control")


def test_control_transaction_destinations_may_not_overlap(modules):
    plan, paths, _ = modules
    parent = valid_transaction()
    parent["path"] = "/control/tree"
    child = dict(parent)
    child["path"] = "/control/tree/child"
    child["safe_ancestors"] = ["/control", "/control/tree"]
    with pytest.raises(paths.ConfigError, match="must not overlap"):
        plan._validate_transactions([parent, child], "/control")


@pytest.mark.parametrize(
    "defect",
    ["selected", "target", "projection", "context", "rollback", "evidence", "confirmation", "duplicate-state-name"],
)
def test_ready_plan_nested_field_and_type_schema_fails_closed(modules, defect):
    plan, paths, _ = modules
    document = minimal_plan(plan)
    document.update(
        status="READY_FOR_REVIEW",
        blockers=[],
        projection={"root": "/fixture/projection", "files": [], "digest": "projection", "published": False},
        participant_context={"path": "/fixture/context.json", "digest": "a" * 64, "document": {}},
        command_evidence_policy={"raw_limit_bytes": 8192, "summary_limit_bytes": 1024, "root": "/fixture/evidence"},
        confirmation={"template": "APPLY NIX-UPDATE PLAN {plan_digest}"},
    )
    if defect == "selected": document["selected_flake"]["host"] = 7
    elif defect == "target": document["target"]["closure"] = {}
    elif defect == "projection": document["projection"]["files"] = "all"
    elif defect == "context": document["participant_context"]["path"] = "relative"
    elif defect == "rollback": document["rollback_policy"] = {"pre_switch": "ignore", "post_switch": "participant-local-reverse"}
    elif defect == "evidence": document["command_evidence_policy"]["summary_limit_bytes"] = 9000
    elif defect == "confirmation": document["confirmation"]["template"] = "yes"
    else:
        identity = {"name": "duplicate", "kind": "file", "policy": {}, "identity": {}}
        document["state_probes"] = [identity]
        document["external_state"] = [dict(identity)]
    with pytest.raises(paths.ConfigError):
        plan.validate_plan_document(document)


@pytest.mark.parametrize("defect", ["duplicate-state-name", "transaction-root", "orphan-root", "rollback-policy"])
def test_contract_cross_field_faults_fail_closed(modules, tmp_path: Path, defect):
    plan, paths, _ = modules
    document = {
        "schema": plan.CONTRACT_SCHEMA,
        "profile_policy": {"profile": "/profile", "live": "/live", "generation_glob": "/system-*-link"},
    }
    if defect == "duplicate-state-name":
        probe = {"name": "duplicate", "kind": "file", "path": "/fixture"}
        document["state_probes"] = [probe]
        document["external_state"] = [dict(probe)]
    elif defect == "transaction-root":
        document["control_transactions"] = [valid_transaction()]
    elif defect == "orphan-root":
        document["control_root"] = "relative"
    else:
        document["rollback_policy"] = {"pre_switch": "ignore", "post_switch": "participant-local-reverse"}
    path = tmp_path / f"{defect}.json"
    path.write_bytes(canonical(document))
    with pytest.raises(paths.ConfigError):
        plan.load_contract(path)


@pytest.mark.parametrize(
    "defect",
    ["root-destination", "ancestor-type", "hash-type", "base64-type", "pre-kind", "link-count", "mode-range"],
)
def test_transaction_remaining_exact_field_branches(modules, defect):
    plan, paths, _ = modules
    item = valid_transaction()
    if defect == "root-destination": item["path"] = "/control"
    elif defect == "ancestor-type": item["safe_ancestors"] = "control"
    elif defect == "hash-type": item["preimage_sha256"] = 7
    elif defect == "base64-type": item["preimage_bytes_b64"] = 7
    elif defect == "pre-kind": item["pre_kind"] = "symlink"
    elif defect == "link-count": item["pre_link_count"] = 2
    else: item["post_mode"] = 0o10000
    with pytest.raises(paths.ConfigError):
        plan._validate_transactions([item], None if defect == "ancestor-type" else "/control")


def test_write_result_reuses_only_an_exact_private_directory(modules, tmp_path: Path):
    plan, _, _ = modules
    directory = tmp_path / "result"
    directory.mkdir(mode=0o700)
    result_path = directory / "result.json"
    path = plan.write_result(directory, {
        "plan_digest": "a" * 64, "started_at": "start", "finished_at": "finish",
        "terminal_state": "FAILED_PRE_SWITCH", "switch_invocation_started": False,
        "events": [], "commands": [], "participants": [], "result_path": str(result_path),
    })
    assert path.parent == directory


def test_write_result_rejects_false_terminal_and_path_claims(modules, tmp_path: Path):
    plan, paths, _ = modules
    directory = tmp_path / "result"
    directory.mkdir(mode=0o700)
    base = {
        "plan_digest": "a" * 64, "started_at": "start", "finished_at": "finish",
        "terminal_state": "FAILED_PRE_SWITCH", "switch_invocation_started": False,
        "events": [], "commands": [], "participants": [],
        "result_path": str(directory / "result.json"),
    }
    with pytest.raises(paths.ConfigError):
        plan.write_result(directory, dict(base, terminal_state="NOT_A_TERMINAL_STATE"))
    with pytest.raises(paths.ConfigError):
        plan.write_result(directory, dict(base, result_path="/tmp/not-the-result.json"))


def test_write_result_rejects_unbound_nested_command_evidence_and_participant_shape(modules, tmp_path: Path):
    plan, paths, _ = modules
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    outside_root = tmp_path / "outside"
    outside_root.mkdir(mode=0o700)
    outside = outside_root / "stdout"
    outside.write_bytes(b"fixture")
    outside.chmod(0o600)
    stream = {
        "path": str(outside), "sha256": hashlib.sha256(b"fixture").hexdigest(),
        "retained_sha256": hashlib.sha256(b"fixture").hexdigest(),
        "size": 7, "retained_bytes": 7, "overflow": False, "limit": 16,
        "summary": "fixture",
    }
    command = {
        "label": "fixture", "argv": ["fixture"], "cwd": str(tmp_path),
        "started_at": "start", "finished_at": "finish", "exit_status": 0,
        "stdout": dict(stream), "stderr": dict(stream),
    }
    result_directory = tmp_path / "result-nested"
    base = {
        "plan_digest": "a" * 64, "started_at": "start", "finished_at": "finish",
        "terminal_state": "FAILED_PRE_SWITCH", "switch_invocation_started": False,
        "events": [], "commands": [command], "participants": [],
        "result_path": str(result_directory / "result.json"),
        "command_evidence_policy": {
            "raw_limit_bytes": 16, "summary_limit_bytes": 8, "root": str(evidence_root),
        },
    }
    with pytest.raises(paths.ConfigError, match="outside its reviewed root"):
        plan.write_result(result_directory, base)

    participant_directory = tmp_path / "participant-result"
    with pytest.raises(paths.ConfigError, match="participant result"):
        plan.write_result(participant_directory, {
            **base, "commands": [], "participants": [{"name": 7}],
            "result_path": str(participant_directory / "result.json"),
            "command_evidence_policy": {},
        })


def test_plan_and_result_paths_must_be_absolute(modules, tmp_path: Path, monkeypatch):
    plan, paths, _ = modules
    monkeypatch.chdir(tmp_path)
    with pytest.raises(paths.ConfigError, match="absolute"):
        plan.write_plan(Path("relative-plan"), {"schema": plan.SCHEMA}, "plan\n")
    with pytest.raises(paths.ConfigError, match="absolute"):
        plan.load_plan(Path("relative-plan"))
    with pytest.raises(paths.ConfigError, match="absolute"):
        plan.write_result(Path("relative-result"), {})


@pytest.mark.parametrize(
    "defect",
    ["root", "entries", "digest-type", "digest-value", "entry", "entry-fields"],
)
def test_loaded_tree_identity_schema_is_closed(modules, defect):
    plan, paths, _ = modules
    entries = [{
        "path": "file", "kind": "regular", "mode": 0o600, "uid": 1, "gid": 1,
        "link_count": 1, "sha256": "a" * 64, "size": 1,
    }]
    root = {
        "path": "/control", "kind": "directory", "mode": 0o700, "uid": 1,
        "gid": 1, "link_count": 2, "resolved_target": "/control",
    }
    content = {"entries": entries, "volatile": []}
    content["digest"] = plan.sha256_bytes(plan.canonical_bytes(content))
    value = {"root_identity": root, "content_identity": content}
    value["digest"] = plan.sha256_bytes(plan.canonical_bytes(value))
    if defect == "root": value["root_identity"]["path"] = "relative"
    elif defect == "entries": value["content_identity"]["entries"] = {}
    elif defect == "digest-type": value["digest"] = 7
    elif defect == "digest-value": value["digest"] = "0" * 64
    elif defect == "entry": value["content_identity"]["entries"] = ["file"]
    else:
        value["content_identity"]["entries"] = [{"path": "file"}]
    with pytest.raises(paths.ConfigError, match="tree"):
        plan._validate_tree_identity(value, "fixture")


@pytest.mark.parametrize(
    "defect",
    [
        "top-extra", "root-extra", "root-kind", "root-unnormalized", "root-mode-type",
        "root-link-zero", "root-symlink-fields", "content-extra", "entry-traversal",
        "entry-absolute", "entry-dot", "entry-duplicate", "entry-unsorted", "entry-kind",
        "entry-mode-type", "entry-link-zero", "regular-missing", "regular-forbidden",
        "directory-forbidden", "symlink-missing", "symlink-relative-target-identity",
        "volatile-extra", "volatile-traversal", "volatile-duplicate", "volatile-unbound",
        "volatile-annotation", "volatile-policy-digest", "content-digest", "complete-digest",
    ],
)
def test_loaded_tree_identity_rejects_every_open_schema_and_digest_case(modules, defect):
    plan, paths, _ = modules
    entry = {
        "path": "file", "kind": "regular", "mode": 0o600, "uid": 1, "gid": 1,
        "link_count": 1, "sha256": "a" * 64, "size": 1,
    }
    root = {
        "path": "/control", "kind": "directory", "mode": 0o700, "uid": 1,
        "gid": 1, "link_count": 2, "resolved_target": "/control",
    }
    value = {"root_identity": root, "content_identity": {"entries": [entry], "volatile": []}}

    if defect == "top-extra": value["extra"] = True
    elif defect == "root-extra": root["extra"] = True
    elif defect == "root-kind": root["kind"] = "regular"
    elif defect == "root-unnormalized": root["path"] = "/control/../control"
    elif defect == "root-mode-type": root["mode"] = True
    elif defect == "root-link-zero": root["link_count"] = 0
    elif defect == "root-symlink-fields": root["kind"] = "symlink"
    elif defect == "content-extra": value["content_identity"]["extra"] = True
    elif defect == "entry-traversal": entry["path"] = "../file"
    elif defect == "entry-absolute": entry["path"] = "/file"
    elif defect == "entry-dot": entry["path"] = "directory/./file"
    elif defect == "entry-duplicate": value["content_identity"]["entries"] = [entry, dict(entry)]
    elif defect == "entry-unsorted":
        later = dict(entry, path="z")
        earlier = dict(entry, path="a")
        value["content_identity"]["entries"] = [later, earlier]
    elif defect == "entry-kind": entry["kind"] = "socket"
    elif defect == "entry-mode-type": entry["mode"] = True
    elif defect == "entry-link-zero": entry["link_count"] = 0
    elif defect == "regular-missing": del entry["sha256"]
    elif defect == "regular-forbidden": entry["target"] = "other"
    elif defect == "directory-forbidden": entry.update(kind="directory")
    elif defect == "symlink-missing":
        entry.clear()
        entry.update(path="link", kind="symlink", mode=0o700, uid=1, gid=1, link_count=1)
    elif defect == "symlink-relative-target-identity":
        entry.clear()
        entry.update(
            path="link", kind="symlink", mode=0o700, uid=1, gid=1, link_count=1,
            target="target", target_identity="relative/target",
        )
    else:
        policy = {
            "path": "file", "kind": "regular", "uid": 1, "gid": 1, "mode": 0o600,
            "producer": "fixture", "content_policy": {"sha256": "a" * 64},
        }
        entry["volatile"] = {
            "producer": "fixture", "content_policy": {"sha256": "a" * 64},
        }
        value["content_identity"]["volatile"] = [policy]
        if defect == "volatile-extra": policy["extra"] = True
        elif defect == "volatile-traversal": policy["path"] = "../file"
        elif defect == "volatile-duplicate": value["content_identity"]["volatile"] = [policy, dict(policy)]
        elif defect == "volatile-unbound": del entry["volatile"]
        elif defect == "volatile-annotation": entry["volatile"]["producer"] = "other"
        elif defect == "volatile-policy-digest": policy["content_policy"]["sha256"] = "0" * 64

    content = value["content_identity"]
    content["digest"] = plan.sha256_bytes(plan.canonical_bytes({
        "entries": content.get("entries"), "volatile": content.get("volatile"),
    }))
    value["digest"] = plan.sha256_bytes(plan.canonical_bytes({
        "root_identity": value["root_identity"], "content_identity": content,
    }))
    if defect == "content-digest": content["digest"] = "0" * 64
    elif defect == "complete-digest": value["digest"] = "0" * 64
    with pytest.raises(paths.ConfigError, match="tree|volatile"):
        plan._validate_tree_identity(value, "fixture")


@pytest.mark.parametrize("defect", ["outside", "limit", "retained", "overflow", "digest", "retained-digest", "summary", "summary-binding", "mode"])
def test_command_evidence_contract_rejects_each_identity_mismatch(modules, tmp_path: Path, defect):
    plan, paths, _ = modules
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    stream_path = root / "stdout"
    stream_path.write_bytes(b"fixture")
    stream_path.chmod(0o600)
    policy = {"raw_limit_bytes": 16, "summary_limit_bytes": 8, "root": str(root)}
    stream = {
        "path": str(stream_path), "sha256": hashlib.sha256(b"fixture").hexdigest(),
        "retained_sha256": hashlib.sha256(b"fixture").hexdigest(),
        "size": 7, "retained_bytes": 7, "overflow": False, "limit": 16, "summary": "fixture",
    }
    if defect == "outside":
        outside = tmp_path / "outside"
        outside.write_bytes(b"fixture")
        outside.chmod(0o600)
        stream["path"] = str(outside)
    elif defect == "limit": stream["limit"] = 15
    elif defect == "retained": stream["retained_bytes"] = 6
    elif defect == "overflow": stream["overflow"] = True
    elif defect == "digest": stream["sha256"] = "0" * 64
    elif defect == "retained-digest": stream["retained_sha256"] = "0" * 64
    elif defect == "summary": stream["summary"] = "x" * 9
    elif defect == "summary-binding": stream["summary"] = "altered"
    else: stream_path.chmod(0o640)
    check = {
        "label": "fixture", "argv": ["fixture"], "cwd": str(tmp_path),
        "started_at": "start", "finished_at": "finish", "exit_status": 0,
        "stdout": dict(stream), "stderr": dict(stream),
    }
    with pytest.raises(paths.ConfigError):
        plan._validate_plan_checks([check], policy)


@pytest.mark.parametrize("defect", ["missing", "unknown", "relative", "duplicate", "type"])
def test_loaded_source_identity_schema_is_closed(modules, defect):
    plan, paths, _ = modules
    source = {
        "name": "source", "input": "source", "lock_node": "source", "kind": "immutable-snapshot",
        "path": "/nix/store/source", "nar_hash": "sha256-source=",
    }
    items = [source]
    if defect == "missing": del source["lock_node"]
    elif defect == "unknown": source["extra"] = True
    elif defect == "relative": source["path"] = "relative"
    elif defect == "duplicate": items = [source, dict(source)]
    else: source["name"] = 7
    with pytest.raises(paths.ConfigError, match="source identity"):
        plan._validate_plan_source_identities(items)


@pytest.mark.parametrize(
    "defect",
    [
        "unknown", "schema", "status", "digest", "created", "preflight", "blockers",
        "object", "list", "override", "evaluation-root", "projection", "publish",
        "rollback", "mutable-key", "mutable-value", "policy-keys", "policy-raw-type",
        "policy-raw-zero", "policy-summary-type", "policy-summary-range", "policy-root",
        "confirmation-keys", "confirmation-type", "confirmation-value",
    ],
)
def test_loaded_plan_top_level_types_fail_closed(modules, defect):
    plan, paths, _ = modules
    document = minimal_plan(plan)
    if defect == "unknown": document["unknown"] = True
    elif defect == "schema": document["schema"] = "future"
    elif defect == "status": document["status"] = "ready-ish"
    elif defect == "digest": document["plan_digest"] = None
    elif defect == "created": document["created_at"] = ""
    elif defect == "preflight": document["preflight_only"] = 1
    elif defect == "blockers": document["blockers"] = [7]
    elif defect == "object": document["tool"] = []
    elif defect == "list": document["overrides"] = {}
    elif defect == "override": document["overrides"] = [7]
    elif defect == "evaluation-root": document["evaluation_root"] = "relative"
    elif defect == "projection": document["projection"] = []
    elif defect == "publish": document["publish_local_config"] = 1
    elif defect == "rollback": document["rollback_policy"] = {"pre_switch": "ignore"}
    elif defect == "mutable-key": document["mutable_state_comparison"] = {7: True}
    elif defect == "mutable-value": document["mutable_state_comparison"] = {"fixture": 1}
    else:
        document["command_evidence_policy"] = {
            "raw_limit_bytes": 16, "summary_limit_bytes": 8, "root": "/evidence",
        }
        if defect == "policy-keys": document["command_evidence_policy"]["extra"] = True
        elif defect == "policy-raw-type": document["command_evidence_policy"]["raw_limit_bytes"] = True
        elif defect == "policy-raw-zero": document["command_evidence_policy"]["raw_limit_bytes"] = 0
        elif defect == "policy-summary-type": document["command_evidence_policy"]["summary_limit_bytes"] = True
        elif defect == "policy-summary-range": document["command_evidence_policy"]["summary_limit_bytes"] = 17
        elif defect == "policy-root": document["command_evidence_policy"]["root"] = "relative"
        elif defect == "confirmation-keys": document["confirmation"] = {"template": "", "extra": True}
        elif defect == "confirmation-type": document["confirmation"] = {"template": 7}
        else: document["confirmation"] = {"template": "wrong"}
    with pytest.raises(paths.ConfigError):
        plan.validate_plan_document(document)


@pytest.mark.parametrize("defect", ["keys", "path", "host-type", "host-empty", "repository"])
def test_loaded_selected_flake_schema_is_closed(modules, defect):
    plan, paths, _ = modules
    value = {"path": "/flake", "host": "Host", "repository": {}}
    if defect == "keys": value["extra"] = True
    elif defect == "path": value["path"] = "relative"
    elif defect == "host-type": value["host"] = 7
    elif defect == "host-empty": value["host"] = ""
    else: value["repository"] = []
    with pytest.raises(paths.ConfigError):
        plan._validate_plan_selected_flake(value)


@pytest.mark.parametrize(
    "defect", ["keys", "output", "closure-type", "entry-type", "entry-keys", "path", "hash", "size", "size-bool"]
)
def test_loaded_target_schema_is_closed(modules, defect):
    plan, paths, _ = modules
    entry = {"path": "/nix/store/item", "narHash": "sha256-item=", "narSize": 1}
    value = {"derivation_output": "/nix/store/target", "closure": [entry]}
    if defect == "keys": value["extra"] = True
    elif defect == "output": value["derivation_output"] = 7
    elif defect == "closure-type": value["closure"] = {}
    elif defect == "entry-type": value["closure"] = ["entry"]
    elif defect == "entry-keys": entry["extra"] = True
    elif defect == "path": entry["path"] = 7
    elif defect == "hash": entry["narHash"] = 7
    elif defect == "size": entry["narSize"] = "1"
    else: entry["narSize"] = True
    with pytest.raises(paths.ConfigError):
        plan._validate_plan_target(value)


@pytest.mark.parametrize(
    "defect", ["keys", "root-type", "root-relative", "files-type", "file-type", "file-length", "file-part", "digest-type", "digest-empty", "published"]
)
def test_loaded_projection_schema_is_closed(modules, defect):
    plan, paths, _ = modules
    value = {"root": "/projection", "files": [["flake.nix", "digest"]], "digest": "digest", "published": False}
    if defect == "keys": value["extra"] = True
    elif defect == "root-type": value["root"] = 7
    elif defect == "root-relative": value["root"] = "relative"
    elif defect == "files-type": value["files"] = {}
    elif defect == "file-type": value["files"] = ["file"]
    elif defect == "file-length": value["files"] = [["file"]]
    elif defect == "file-part": value["files"] = [["file", 7]]
    elif defect == "digest-type": value["digest"] = 7
    elif defect == "digest-empty": value["digest"] = ""
    else: value["published"] = 0
    with pytest.raises(paths.ConfigError):
        plan._validate_plan_projection(value, True)


@pytest.mark.parametrize(
    "defect", ["shape", "name", "phase", "status", "blockers", "blocker", "observations", "handle", "context", "attestation", "stdout", "stderr", "failure-kind", "timeout", "timeout-bool"]
)
def test_loaded_participant_result_schema_is_closed(modules, defect):
    plan, paths, _ = modules
    item = {
        "name": "participant", "phase": "preflight", "status": "ok", "blockers": [],
        "observations": {}, "rollback_handle": None, "context_digest": "a" * 64,
        "attestation_digest": "b" * 64, "stdout": "", "stderr": "",
        "failure_kind": None, "timeout_seconds": None,
    }
    if defect == "shape": item["extra"] = True
    elif defect == "name": item["name"] = 7
    elif defect == "phase": item["phase"] = 7
    elif defect == "status": item["status"] = "unknown"
    elif defect == "blockers": item["blockers"] = {}
    elif defect == "blocker": item["blockers"] = [7]
    elif defect == "observations": item["observations"] = []
    elif defect == "handle": item["rollback_handle"] = 7
    elif defect == "context": item["context_digest"] = "short"
    elif defect == "attestation": item["attestation_digest"] = "short"
    elif defect == "stdout": item["stdout"] = 7
    elif defect == "stderr": item["stderr"] = 7
    elif defect == "failure-kind": item["failure_kind"] = 7
    elif defect == "timeout": item["timeout_seconds"] = "1"
    else: item["timeout_seconds"] = True
    with pytest.raises(paths.ConfigError):
        plan._validate_plan_participant_results([item])


def valid_participant_context(plan):
    document = {
        "schema": "nix-update-participant-context-v1", "protocol": plan.PARTICIPANT_SCHEMA,
        "stage": "prepare", "plan_digest": "", "selected_repository": {},
        "selected_flake": "/flake", "flake_lock": {}, "host": "Host", "environment": {},
        "target": {"output": "/nix/store/target", "closure": []}, "overrides": [],
        "overrides_digest": plan.sha256_bytes(plan.canonical_bytes({"overrides": []})),
        "projection": {}, "sources": [], "control_projection": {}, "profile": {},
        "state_probes": [], "external_state": [], "participant_declarations": [],
        "participant_declaration_digest": plan.sha256_bytes(plan.canonical_bytes({"participants": []})),
    }
    document["plan_draft_identity"] = plan.sha256_bytes(plan.canonical_bytes(document))
    return {
        "path": "/context.json", "digest": plan.sha256_bytes(plan.canonical_bytes(document)),
        "document": document,
    }


@pytest.mark.parametrize(
    "defect",
    [
        "wrapper-keys", "path-type", "path-relative", "digest", "document-type", "document-digest",
        "document-keys", "schema", "protocol", "stage-type", "stage-empty", "plan-digest",
        "repository", "selected-type", "selected-relative", "lock", "host-type", "host-empty",
        "environment", "target-type", "target-keys", "target-output", "target-closure", "lists",
        "projection", "control", "profile", "overrides-digest", "declarations-digest", "draft-type", "draft-value",
    ],
)
def test_loaded_participant_context_schema_is_closed(modules, defect):
    plan, paths, _ = modules
    value = valid_participant_context(plan)
    document = value["document"]
    if defect == "wrapper-keys": value["extra"] = True
    elif defect == "path-type": value["path"] = 7
    elif defect == "path-relative": value["path"] = "relative"
    elif defect == "digest": value["digest"] = "short"
    elif defect == "document-type": value["document"] = []
    elif defect == "document-digest": document["host"] = "Changed"
    else:
        if defect == "document-keys": document["extra"] = True
        elif defect == "schema": document["schema"] = "future"
        elif defect == "protocol": document["protocol"] = "future"
        elif defect == "stage-type": document["stage"] = 7
        elif defect == "stage-empty": document["stage"] = ""
        elif defect == "plan-digest": document["plan_digest"] = 7
        elif defect == "repository": document["selected_repository"] = []
        elif defect == "selected-type": document["selected_flake"] = 7
        elif defect == "selected-relative": document["selected_flake"] = "relative"
        elif defect == "lock": document["flake_lock"] = []
        elif defect == "host-type": document["host"] = 7
        elif defect == "host-empty": document["host"] = ""
        elif defect == "environment": document["environment"] = []
        elif defect == "target-type": document["target"] = []
        elif defect == "target-keys": document["target"]["extra"] = True
        elif defect == "target-output": document["target"]["output"] = 7
        elif defect == "target-closure": document["target"]["closure"] = {}
        elif defect == "lists": document["sources"] = {}
        elif defect == "projection": document["projection"] = []
        elif defect == "control": document["control_projection"] = []
        elif defect == "profile": document["profile"] = []
        elif defect == "overrides-digest": document["overrides_digest"] = "0" * 64
        elif defect == "declarations-digest": document["participant_declaration_digest"] = "0" * 64
        elif defect == "draft-type": document["plan_draft_identity"] = 7
        else: document["plan_draft_identity"] = "0" * 64
        value["digest"] = plan.sha256_bytes(plan.canonical_bytes(document))
    with pytest.raises(paths.ConfigError):
        plan._validate_plan_participant_context(value, True)


@pytest.mark.parametrize("defect", ["evaluation", "live-keys", "live-profile", "live-closure", "delta-keys", "delta-type", "delta-item", "host-type", "host-keys", "host-name", "host-output", "host-closure"])
def test_loaded_ready_live_and_host_schema_is_closed(modules, defect):
    plan, paths, _ = modules
    document = {
        "evaluation_current": {"exists": False, "link": None},
        "live": {"profile": {}, "closure": []},
        "closure_delta": {"added": [], "removed": [], "unapproved": []},
        "host_candidates": [{"host": "Host", "output": "/nix/store/target", "closure": []}],
    }
    if defect == "evaluation": document["evaluation_current"] = {"exists": 0, "link": None}
    elif defect == "live-keys": document["live"]["extra"] = True
    elif defect == "live-profile": document["live"]["profile"] = []
    elif defect == "live-closure": document["live"]["closure"] = {}
    elif defect == "delta-keys": document["closure_delta"]["extra"] = []
    elif defect == "delta-type": document["closure_delta"]["added"] = {}
    elif defect == "delta-item": document["closure_delta"]["added"] = [7]
    elif defect == "host-type": document["host_candidates"] = ["host"]
    elif defect == "host-keys": document["host_candidates"][0]["extra"] = True
    elif defect == "host-name": document["host_candidates"][0]["host"] = ""
    elif defect == "host-output": document["host_candidates"][0]["output"] = 7
    else: document["host_candidates"][0]["closure"] = {}
    with pytest.raises(paths.ConfigError):
        plan._validate_ready_live_and_hosts(document)


@pytest.mark.parametrize("defect", ["keys", "kind", "uid", "gid", "mode", "content-type", "content"])
def test_volatile_identity_policy_checks_every_bound_field(modules, defect):
    plan, paths, _ = modules
    entry = {"path": "marker", "kind": "regular", "uid": 1, "gid": 2, "mode": 0o600, "sha256": "a" * 64}
    policy = {
        "path": "marker", "kind": "regular", "uid": 1, "gid": 2, "mode": 0o600,
        "producer": "fixture", "content_policy": {"sha256": "a" * 64},
    }
    if defect == "keys": policy["extra"] = True
    elif defect in {"kind", "uid", "gid", "mode"}: policy[defect] = "wrong"
    elif defect == "content-type": policy["content_policy"] = []
    else: policy["content_policy"] = {"sha256": "b" * 64}
    with pytest.raises(paths.ConfigError):
        plan._validate_volatile(entry, policy)


def test_plan_load_and_validate_rejects_v1_plan_with_precise_message(modules, tmp_path: Path):
    plan, paths, _ = modules
    document = minimal_plan(plan)
    document["schema"] = "nix-update-deployment-plan-v1"
    artifact_dir = tmp_path / "plan-v1"
    artifact_dir.mkdir(mode=0o700)
    (artifact_dir / "plan.json").write_bytes(plan.canonical_bytes(document))
    (artifact_dir / "plan.json").chmod(0o600)
    expected_msg = (
        "deployment plan schema nix-update-deployment-plan-v1 is incompatible with local repository authority; "
        "run nix-update prepare to create a v2 plan"
    )
    with pytest.raises(paths.ConfigError, match=re.escape(expected_msg)):
        plan.load_plan(artifact_dir)
    with pytest.raises(paths.ConfigError, match=re.escape(expected_msg)):
        plan.validate_plan_document(document)

    document["legacy_remote_authority"] = {"remote": "origin"}
    with pytest.raises(paths.ConfigError, match=re.escape(expected_msg)):
        plan.validate_plan_document(document)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda r: r.pop("commit"), "missing or unknown fields"),
        (lambda r: r.update(extra="field"), "missing or unknown fields"),
        (lambda r: r.update(repository_root="relative/path"), "must be an absolute path"),
        (lambda r: r.update(repository_root="/wrong/root"), "does not contain selected flake"),
        (lambda r: r.update(commit="not-a-sha"), "40-character hex SHA-1"),
        (lambda r: r.update(tree="123"), "40-character hex SHA-1"),
        (lambda r: r.update(branch=""), "non-empty branch name and not HEAD"),
        (lambda r: r.update(branch="HEAD"), "non-empty branch name and not HEAD"),
        (lambda r: r.update(status=" M dirty"), "must be clean"),
        (lambda r: r.update(operation_state="rebase-merge"), "operation_state must be clean"),
        (lambda r: r.update(index_sha256="bad"), "64-character hex SHA-256"),
        (lambda r: r.update(ignored_status_sha256="bad"), "64-character hex SHA-256"),
        (lambda r: r.update(ignored_status_count="not-digit"), "digit string"),
        (lambda r: r.update(flake_nix="not-json"), "JSON is invalid"),
        (lambda r: r.update(flake_lock=123), "must be a JSON string"),
        (lambda r: r.update(flake_nix=json.dumps({"bad": "keys"})), "invalid identity fields"),
    ],
)
def test_plan_selected_flake_repository_local_authority_is_strictly_validated(modules, mutation, match):
    plan, paths, _ = modules
    repo = minimal_repository_identity("/fixture/flake")
    mutation(repo)
    with pytest.raises(paths.ConfigError, match=match):
        plan._validate_plan_local_repository(repo, "/fixture/flake")


def test_serialize_lock_document_produces_exact_nix_document_keys(modules):
    plan, paths, _ = modules
    sample_nodes = {
        "root": {"inputs": {"core": "core"}},
        "core": {
            "original": {"type": "git", "url": "ssh://git@github.com/example/core.git"},
            "locked": {
                "type": "git",
                "url": "ssh://git@github.com/example/core.git",
                "rev": "1" * 40,
                "narHash": "sha256-abc=",
            },
        },
    }
    # Unannotated document
    doc_clean = {"nodes": sample_nodes, "root": "root", "version": 7}
    raw_clean = plan.serialize_lock_document(doc_clean)
    parsed_clean = json.loads(raw_clean.decode("utf-8"))
    assert set(parsed_clean.keys()) == {"nodes", "root", "version"}
    assert "byte_sha256" not in parsed_clean
    assert parsed_clean["nodes"] == sample_nodes
    assert parsed_clean["root"] == "root"
    assert parsed_clean["version"] == 7

    # Annotated document (with diagnostic byte_sha256)
    doc_annotated = {
        "nodes": sample_nodes,
        "root": "root",
        "version": 7,
        "byte_sha256": "f" * 64,
    }
    raw_from_annotated = plan.serialize_lock_document(doc_annotated)
    parsed_from_annotated = json.loads(raw_from_annotated.decode("utf-8"))
    assert set(parsed_from_annotated.keys()) == {"nodes", "root", "version"}
    assert "byte_sha256" not in parsed_from_annotated

    # Exact byte stability: clean and annotated inputs yield identical bytes
    assert raw_clean == raw_from_annotated
    # Repeated serialization stability
    assert plan.serialize_lock_document(parsed_clean) == raw_clean


def test_read_lock_document_reads_raw_document_without_snapshot_mutation(modules, tmp_path: Path):
    plan, paths, _ = modules
    lock_file = tmp_path / "flake.lock"
    payload = {
        "nodes": {"root": {}},
        "root": "root",
        "version": 7,
        "future_field": "preserved",
    }
    lock_file.write_text(json.dumps(payload))
    read_doc = plan.read_lock_document(lock_file)
    assert read_doc == payload
    assert "byte_sha256" not in read_doc

    # Non-object lock file
    non_obj = tmp_path / "non_obj.lock"
    non_obj.write_text("[\"not\", \"a\", \"dict\"]")
    with pytest.raises(paths.ConfigError, match="must contain an object"):
        plan.read_lock_document(non_obj)

    # Malformed JSON
    bad_json = tmp_path / "bad.lock"
    bad_json.write_text("{bad json")
    with pytest.raises(paths.ConfigError, match="unable to read flake lock"):
        plan.read_lock_document(bad_json)


def test_serialize_lock_document_rejects_diagnostic_hash_relocated_into_nodes(modules):
    plan, paths, _ = modules
    # Relocated directly into a node
    doc_direct = {
        "nodes": {
            "root": {"inputs": {}},
            "leaf": {"byte_sha256": "bad", "locked": {"type": "git"}},
        },
        "root": "root",
        "version": 7,
    }
    with pytest.raises(paths.ConfigError, match="node contains invalid diagnostic metadata"):
        plan.serialize_lock_document(doc_direct)

    # Relocated deeper into locked metadata
    doc_nested = {
        "nodes": {
            "root": {"inputs": {}},
            "leaf": {"locked": {"type": "git", "byte_sha256": "bad"}},
        },
        "root": "root",
        "version": 7,
    }
    with pytest.raises(paths.ConfigError, match="node contains invalid diagnostic metadata"):
        plan.serialize_lock_document(doc_nested)

    # Relocated deeper into original metadata
    doc_original = {
        "nodes": {
            "root": {"inputs": {}},
            "leaf": {"original": {"type": "git", "byte_sha256": "bad"}},
        },
        "root": "root",
        "version": 7,
    }
    with pytest.raises(paths.ConfigError, match="node contains invalid diagnostic metadata"):
        plan.serialize_lock_document(doc_original)


def test_serialize_lock_document_rejects_unsupported_shapes_and_unknown_fields(modules):
    plan, paths, _ = modules
    base_doc = {"nodes": {"root": {}}, "root": "root", "version": 7}

    # Non-mapping document
    with pytest.raises(paths.ConfigError, match="must contain an object"):
        plan.serialize_lock_document([1, 2, 3])

    # Non-string document keys
    with pytest.raises(paths.ConfigError, match="document keys must be strings"):
        plan.serialize_lock_document({123: "val", "nodes": {"root": {}}, "root": "root", "version": 7})

    # Missing or non-mapping nodes
    with pytest.raises(paths.ConfigError, match="must contain a nodes mapping"):
        plan.serialize_lock_document({"root": "root", "version": 7})
    with pytest.raises(paths.ConfigError, match="must contain a nodes mapping"):
        plan.serialize_lock_document({"nodes": "not-a-dict", "root": "root", "version": 7})

    # Non-string node names
    with pytest.raises(paths.ConfigError, match="contains an invalid node"):
        plan.serialize_lock_document({"nodes": {123: {}}, "root": "root", "version": 7})

    # Missing or non-string root
    with pytest.raises(paths.ConfigError, match="must contain a root node reference"):
        plan.serialize_lock_document({"nodes": {"root": {}}, "version": 7})
    with pytest.raises(paths.ConfigError, match="must contain a root node reference"):
        plan.serialize_lock_document({"nodes": {"root": {}}, "root": 123, "version": 7})
    with pytest.raises(paths.ConfigError, match="must contain a root node reference"):
        plan.serialize_lock_document({"nodes": {"root": {}}, "root": "", "version": 7})

    # Missing or non-integer version
    with pytest.raises(paths.ConfigError, match="must contain an integer version"):
        plan.serialize_lock_document({"nodes": {"root": {}}, "root": "root"})
    with pytest.raises(paths.ConfigError, match="must contain an integer version"):
        plan.serialize_lock_document({"nodes": {"root": {}}, "root": "root", "version": "7"})
    with pytest.raises(paths.ConfigError, match="must contain an integer version"):
        plan.serialize_lock_document({"nodes": {"root": {}}, "root": "root", "version": True})

    # Invalid node value
    with pytest.raises(paths.ConfigError, match="contains an invalid node"):
        plan.serialize_lock_document({"nodes": {"root": [1, 2]}, "root": "root", "version": 7})

    # Unknown top-level fields (explicit rejection, preventing lossy key filtering)
    with pytest.raises(paths.ConfigError, match="unsupported field: extra_field"):
        plan.serialize_lock_document({**base_doc, "extra_field": "val"})
    with pytest.raises(paths.ConfigError, match="unsupported field: sha256"):
        plan.serialize_lock_document({**base_doc, "sha256": "renamed_hash"})
    with pytest.raises(paths.ConfigError, match="unsupported field: lock_sha256"):
        plan.serialize_lock_document({**base_doc, "lock_sha256": "renamed_hash"})


def test_serialize_lock_document_preserves_dependency_data_and_graph_semantics(modules):
    plan, _, _ = modules
    rich_nodes = {
        "root": {"inputs": {"dep": "dep", "sub": ["dep", "sub"]}},
        "dep": {
            "flake": False,
            "original": {"type": "git", "url": "https://example.com/repo", "ref": "feature"},
            "locked": {
                "type": "git",
                "url": "https://example.com/repo",
                "rev": "2" * 40,
                "narHash": "sha256-nar=",
                "lastModified": 1780000000,
                "revCount": 42,
            },
            "custom_future_attr": {"nested_key": [1, 2, 3]},
        },
    }
    doc = {"nodes": rich_nodes, "root": "root", "version": 7}
    raw = plan.serialize_lock_document(doc)
    parsed = json.loads(raw.decode("utf-8"))
    assert parsed["nodes"]["dep"]["flake"] is False
    assert parsed["nodes"]["dep"]["custom_future_attr"] == {"nested_key": [1, 2, 3]}
    assert parsed["nodes"]["dep"]["locked"]["revCount"] == 42
    assert parsed["nodes"]["root"]["inputs"]["sub"] == ["dep", "sub"]


def test_serialize_lock_document_supports_byte_sha256_as_dependency_and_input_name(modules):
    plan, _, _ = modules
    nodes = {
        "root": {
            "inputs": {
                "byte_sha256": "byte_sha256",
                "other": "other_dep",
                "indirect": ["byte_sha256", "sub"],
            }
        },
        "byte_sha256": {
            "inputs": {"sub": "sub_dep"},
            "original": {
                "type": "git",
                "url": "https://example.com/byte_sha256.git",
                "ref": "main",
            },
            "locked": {
                "type": "git",
                "url": "https://example.com/byte_sha256.git",
                "rev": "3" * 40,
                "narHash": "sha256-dep=",
                "lastModified": 1790000000,
            },
            "flake": True,
            "custom_future_attr": {"byte_sha256": "nested_in_non_locator"},
        },
        "other_dep": {
            "inputs": {"byte_sha256": "byte_sha256"},
            "original": {"type": "git", "url": "https://example.com/other.git"},
            "locked": {
                "type": "git",
                "url": "https://example.com/other.git",
                "rev": "4" * 40,
                "narHash": "sha256-other=",
            },
        },
        "sub_dep": {
            "locked": {
                "type": "git",
                "url": "https://example.com/sub.git",
                "rev": "5" * 40,
                "narHash": "sha256-sub=",
            },
        },
    }
    doc = {"nodes": nodes, "root": "root", "version": 7}
    raw = plan.serialize_lock_document(doc)
    parsed = json.loads(raw.decode("utf-8"))

    # Legitimate input key named byte_sha256 is preserved
    assert parsed["nodes"]["root"]["inputs"]["byte_sha256"] == "byte_sha256"
    assert parsed["nodes"]["other_dep"]["inputs"]["byte_sha256"] == "byte_sha256"
    assert parsed["nodes"]["root"]["inputs"]["indirect"] == ["byte_sha256", "sub"]

    # Node named byte_sha256 is preserved with all attributes
    assert "byte_sha256" in parsed["nodes"]
    assert parsed["nodes"]["byte_sha256"]["locked"]["rev"] == "3" * 40
    assert parsed["nodes"]["byte_sha256"]["locked"]["narHash"] == "sha256-dep="
    assert parsed["nodes"]["byte_sha256"]["original"]["url"] == "https://example.com/byte_sha256.git"
    assert parsed["nodes"]["byte_sha256"]["flake"] is True
    # Non-locator extension attributes containing nested byte_sha256 are accepted graph data
    assert parsed["nodes"]["byte_sha256"]["custom_future_attr"] == {"byte_sha256": "nested_in_non_locator"}

    # Document-level schema guarantees
    assert set(parsed.keys()) == {"nodes", "root", "version"}
    assert "byte_sha256" not in parsed

    # Byte stability and idempotence
    assert plan.serialize_lock_document(parsed) == raw

    # Document with BOTH historical top-level byte_sha256 AND input key byte_sha256
    doc_with_top_level = {
        **doc,
        "byte_sha256": "f" * 64,
    }
    raw_with_top_level = plan.serialize_lock_document(doc_with_top_level)
    assert raw_with_top_level == raw
    parsed_with_top_level = json.loads(raw_with_top_level.decode("utf-8"))
    assert set(parsed_with_top_level.keys()) == {"nodes", "root", "version"}
    assert "byte_sha256" not in parsed_with_top_level
    assert parsed_with_top_level["nodes"]["root"]["inputs"]["byte_sha256"] == "byte_sha256"


def test_normalized_lock_recomputes_evidence_hash_and_ignores_stale_embedded_hash(modules, tmp_path: Path):
    plan, _, _ = modules
    lock_path = tmp_path / "flake.lock"
    # Write lock containing an intentional fake/stale embedded byte_sha256
    stale_hash = "0" * 64
    lock_doc = {
        "nodes": {"root": {}},
        "root": "root",
        "version": 7,
        "byte_sha256": stale_hash,
    }
    raw_bytes = (json.dumps(lock_doc) + "\n").encode("utf-8")
    lock_path.write_bytes(raw_bytes)
    stat_before = lock_path.stat()

    normalized = plan.normalized_lock(lock_path)
    # Evidence hash is recomputed from raw bytes, never trusted from embedded hash
    true_hash = plan.sha256_bytes(raw_bytes)
    assert normalized["byte_sha256"] == true_hash
    assert normalized["byte_sha256"] != stale_hash

    # Reading normalized_lock does not rewrite or recanonicalize the file on disk
    stat_after = lock_path.stat()
    assert lock_path.read_bytes() == raw_bytes
    assert stat_before.st_mtime_ns == stat_after.st_mtime_ns

    # Mutation / drift is reliably detected
    mutated_bytes = raw_bytes.replace(b"root", b"main")
    lock_path.write_bytes(mutated_bytes)
    normalized_mutated = plan.normalized_lock(lock_path)
    assert normalized_mutated["byte_sha256"] == plan.sha256_bytes(mutated_bytes)
    assert normalized_mutated["byte_sha256"] != true_hash


def test_old_retained_annotated_candidate_is_read_without_changing_bytes_or_digest(modules, tmp_path: Path):
    plan, _, _ = modules
    candidate_path = tmp_path / "flake.lock.candidate"
    historical_content = (
        json.dumps({
            "byte_sha256": "9" * 64,
            "nodes": {"root": {"inputs": {"dep": "dep"}}, "dep": {"locked": {"rev": "a" * 40}}},
            "root": "root",
            "version": 7,
        }, sort_keys=True) + "\n"
    ).encode("utf-8")
    candidate_path.write_bytes(historical_content)
    stat_before = candidate_path.stat()
    expected_digest = plan.sha256_bytes(historical_content)

    snapshot = plan.normalized_lock(candidate_path)
    assert snapshot["byte_sha256"] == expected_digest
    assert candidate_path.read_bytes() == historical_content
    stat_after = candidate_path.stat()
    assert stat_before.st_mtime_ns == stat_after.st_mtime_ns


def test_lock_delta_detects_genuine_drift_and_ignores_diagnostic_metadata_differences(modules, tmp_path: Path):
    plan, paths, _ = modules
    # Preimage has historical diagnostic annotation, candidate is clean
    nodes_common = {
        "root": {"inputs": {"core": "core"}},
        "core": {"locked": {"rev": "1" * 40, "narHash": "sha256-old="}},
    }
    preimage_annotated = {
        "nodes": nodes_common,
        "root": "root",
        "version": 7,
        "byte_sha256": "preimage_hash",
    }
    candidate_clean = {
        "nodes": nodes_common,
        "root": "root",
        "version": 7,
        "byte_sha256": "candidate_hash",
    }
    # No node/root/version changes -> changes is empty, no unexpected $lock-metadata
    changes = plan.lock_delta(preimage_annotated, candidate_clean)
    assert changes == []

    # Genuine node drift (rev, narHash) is detected
    candidate_rev_drift = {
        "nodes": {
            "root": {"inputs": {"core": "core"}},
            "core": {"locked": {"rev": "2" * 40, "narHash": "sha256-new="}},
        },
        "root": "root",
        "version": 7,
        "byte_sha256": "cand_hash2",
    }
    changes_drift = plan.lock_delta(preimage_annotated, candidate_rev_drift)
    assert len(changes_drift) == 1
    assert changes_drift[0]["node"] == "core"

    # Genuine root movement is detected as $lock-metadata
    candidate_root_drift = {
        "nodes": nodes_common,
        "root": "other_root",
        "version": 7,
        "byte_sha256": "cand_hash3",
    }
    changes_root = plan.lock_delta(preimage_annotated, candidate_root_drift)
    assert any(c["node"] == "$lock-metadata" for c in changes_root)

    # Genuine version movement is detected as $lock-metadata
    candidate_ver_drift = {
        "nodes": nodes_common,
        "root": "root",
        "version": 8,
        "byte_sha256": "cand_hash4",
    }
    changes_ver = plan.lock_delta(preimage_annotated, candidate_ver_drift)
    assert any(c["node"] == "$lock-metadata" for c in changes_ver)
