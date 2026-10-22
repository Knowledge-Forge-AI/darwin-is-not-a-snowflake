# nix-darwin-test-source-target: bin/dinas-root-cutover
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_core.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_binding.py
# nix-darwin-test-source-target: bin/nix-update
from pathlib import Path
import ast
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "libexec"))

import pytest


def discover(repo_root, tmp_path, monkeypatch, location, *, configured=""):
    source = ast.parse((repo_root / "bin/nix-update").read_text())
    function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "locate_core")
    import os
    import sys
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("NIX_DARWIN_FLAKE_DIR", configured)
    monkeypatch.setattr(sys, "argv", ["nix-update", "build"])
    namespace = {"Path": Path, "os": os, "sys": sys, "__file__": str(location / "bin/nix-update")}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "nix-update", "exec"), namespace)
    return namespace["locate_core"]()


def make_core(path):
    (path / "bin").mkdir(parents=True)
    (path / "libexec/nix_darwin_local").mkdir(parents=True)
    (path / "flake.nix").write_text("{}")
    (path / "libexec/nix_darwin_local/launcher.py").write_text("")
    return path


@pytest.mark.parametrize("layout", ["historical/nix-darwin", ".local/nix-darwin/darwin-is-not-a-snowflake", "archive", "worktree"])
def test_invoked_product_bytes_take_precedence_over_ambient_checkout(repo_root, tmp_path, monkeypatch, layout):
    product = make_core(tmp_path / layout)
    make_core(tmp_path / ".flakes/nix-darwin")
    assert discover(repo_root, tmp_path, monkeypatch, product) == product


def test_explicit_navigation_symlink_resolves_same_product(repo_root, tmp_path, monkeypatch):
    product = make_core(tmp_path / ".local/nix-darwin/darwin-is-not-a-snowflake")
    old = tmp_path / "navigation"
    old.mkdir()
    (old / "nix-darwin").symlink_to(product, target_is_directory=True)
    assert discover(repo_root, tmp_path, monkeypatch, old / "nix-darwin") == product


def test_copied_launcher_uses_canonical_default(repo_root, tmp_path, monkeypatch):
    product = make_core(tmp_path / ".local/nix-darwin/darwin-is-not-a-snowflake")
    assert discover(repo_root, tmp_path, monkeypatch, tmp_path / "copied") == product


def test_flattened_core_binding_and_revision_promotion():
    import copy
    from nix_darwin_local.promotion_binding import resolve_binding, authorize_transition
    from nix_darwin_local.promotion_core import validate_core_lock_authorization
    before = {"version": 7, "root": "root", "nodes": {
        "root": {"inputs": {"core": "core"}},
        "core": {"original": {"type": "git", "url": "https://example.org/core.git"},
                 "locked": {"type": "git", "url": "https://example.org/core.git", "rev": "a" * 40,
                            "narHash": "sha256-" + "A" * 43 + "="}},
    }}
    after = copy.deepcopy(before)
    after["nodes"]["core"]["locked"]["rev"] = "b" * 40
    assert resolve_binding({}, "core", before)["repository_url"] == "https://example.org/core.git"
    record = authorize_transition(before, after, "core", "https://example.org/core.git", "b" * 40,
                                  input_name="core", allow_file=False, expected_ref="refs/heads/main",
                                  allow_implicit_main=True)
    assert record["subflake"] == ""
    assert validate_core_lock_authorization(record) == record


def test_tool_content_identity_is_independent_of_root_spelling(tmp_path):
    from nix_darwin_local.promotion_qualification import promotion_tool_identity
    from nix_darwin_local.promotion_source import check_tracked_tool_drift
    from nix_darwin_local.deployment_workflow import Workflow
    import shutil
    import subprocess
    root = make_core(tmp_path / "root")
    (root / "bin/nix-update").write_text("#!/usr/bin/env python3\n")
    (root / "bin/nix-update").chmod(0o755)
    for args in (["init", "-b", "main"], ["config", "user.name", "Fixture"],
                 ["config", "user.email", "fixture@example.invalid"], ["add", "."], ["commit", "-qm", "fixture"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    other = tmp_path / "materialized"
    shutil.copytree(root, other, ignore=shutil.ignore_patterns(".git"))
    alias = tmp_path / "compatibility"
    alias.symlink_to(root, target_is_directory=True)
    expected = promotion_tool_identity(root)
    assert promotion_tool_identity(other) == expected
    assert promotion_tool_identity(alias) == expected
    assert check_tracked_tool_drift(Workflow(root, display_command_output=False), root, root) == expected
    (other / "libexec/nix_darwin_local/launcher.py").write_text("changed")
    assert promotion_tool_identity(other) != expected


@pytest.mark.parametrize("mutation", ["repository", "child", "reverse", "selector", "tamper"])
def test_layout_authorization_rejects_unrelated_or_ambiguous_changes(mutation):
    import copy
    from nix_darwin_local.promotion_core import authorize_core_lock_transition, validate_core_lock_authorization
    from nix_darwin_local.paths import ConfigError
    original = {"type": "git", "url": "https://example.org/core.git", "dir": "nix-darwin"}
    before = {"version": 7, "root": "root", "nodes": {"root": {"inputs": {"core": "core"}},
        "core": {"original": original, "locked": {**original, "rev": "a" * 40, "narHash": "sha256-" + "A" * 43 + "="}}}}
    after = copy.deepcopy(before)
    for field in ("original", "locked"):
        after["nodes"]["core"][field].pop("dir")
    after["nodes"]["core"]["locked"]["rev"] = "b" * 40
    if mutation == "repository":
        for field in ("original", "locked"):
            after["nodes"]["core"][field]["url"] = "https://example.org/other.git"
    elif mutation == "child":
        after["nodes"]["extra"] = {"inputs": {}}
    elif mutation == "reverse":
        before, after = after, before
    elif mutation == "selector":
        for field in ("original", "locked"):
            after["nodes"]["core"][field]["dir"] = ""
    with pytest.raises(ConfigError):
        record = authorize_core_lock_transition(before, after, "b" * 40, allow_layout_transition=True)
        if mutation == "tamper":
            record["previousSubflake"] = "different"
            validate_core_lock_authorization(record)


def test_repository_transition_authorization_and_validation():
    import copy
    from nix_darwin_local.promotion_core import (
        CORE_REPOSITORY_AUTHORIZATION_SCHEMA,
        authorize_core_lock_transition,
        validate_core_lock_authorization,
    )
    from nix_darwin_local.paths import ConfigError

    original = {"type": "git", "url": "ssh://git@github.com/Knowledge-Forge-AI/.flakes.git"}
    before = {
        "version": 7,
        "root": "root",
        "nodes": {
            "root": {"inputs": {"core": "core"}},
            "core": {
                "original": original,
                "locked": {
                    **original,
                    "rev": "a" * 40,
                    "narHash": "sha256-" + "A" * 43 + "=",
                },
            },
        },
    }
    after = copy.deepcopy(before)
    cand_url = "ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git"
    for field in ("original", "locked"):
        after["nodes"]["core"][field]["url"] = cand_url
    after["nodes"]["core"]["locked"]["rev"] = "b" * 40

    with pytest.raises(ConfigError, match="changed the core source declaration"):
        authorize_core_lock_transition(before, after, "b" * 40)

    with pytest.raises(ConfigError, match="requires the historical preimage"):
        authorize_core_lock_transition(
            before, after, "b" * 40, allow_layout_transition=True
        )

    record = authorize_core_lock_transition(
        before, after, "b" * 40, allow_repository_transition=True
    )
    assert record["schema"] == CORE_REPOSITORY_AUTHORIZATION_SCHEMA
    assert record["repositoryUrl"] == cand_url
    assert record["previousRepositoryUrl"] == "ssh://git@github.com/Knowledge-Forge-AI/.flakes.git"
    assert record["subflake"] == ""
    assert validate_core_lock_authorization(record) == record



def test_repository_metadata_links_are_relocatable_and_library_is_not_ignored(repo_root, tmp_path):
    import shutil
    import subprocess
    (tmp_path / ".gitignore").write_bytes((repo_root / ".gitignore").read_bytes())
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/contract.nix").write_text("{}")
    assert subprocess.run(["git", "-C", str(tmp_path), "check-ignore", "lib/contract.nix"], capture_output=True).returncode == 1
    for name in (".agents", ".claude"):
        for link in (repo_root / name).rglob("*"):
            if link.is_symlink():
                assert not link.readlink().is_absolute()
                assert link.resolve().is_dir()


def test_cutover_entrypoint_is_retired_and_fails_closed(repo_root, monkeypatch, capsys):
    import runpy
    monkeypatch.setattr(sys, "argv", ["dinas-root-cutover"])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(repo_root / "bin/dinas-root-cutover"), run_name="__main__")
    assert stopped.value.code == 1
    captured = capsys.readouterr()
    assert "dinas-root-cutover is retired" in captured.err


def test_product_archive_omits_operational_metadata_without_deleting_it(repo_root, tmp_path):
    import io
    import subprocess
    import tarfile
    (tmp_path / ".gitattributes").write_bytes((repo_root / ".gitattributes").read_bytes())
    (tmp_path / ".serena").mkdir()
    (tmp_path / ".serena/operational").write_text("private-state")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin/codex-tooling-self-heal").symlink_to("/unavailable/owned-elsewhere")
    (tmp_path / "flake.nix").write_text("{}")
    for args in (["init", "-q"], ["config", "user.name", "Fixture"], ["config", "user.email", "fixture@example.invalid"], ["add", "."], ["commit", "-qm", "fixture"]):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    raw = subprocess.check_output(["git", "-C", str(tmp_path), "archive", "HEAD"])
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        names = archive.getnames()
    assert "flake.nix" in names
    assert not any(name.startswith(".serena") for name in names)
    assert "bin/codex-tooling-self-heal" not in names
    assert (tmp_path / ".serena/operational").read_text() == "private-state"
    assert (tmp_path / "bin/codex-tooling-self-heal").is_symlink()


def effective_tracked_paths(repo_root: Path) -> list[str]:
    # This checks the unstaged publication candidate, not the accepted HEAD.
    # A deleted legacy path in HEAD still fails the cutover verifier. Replace
    # this transitional carve-out with an accepted-tree assertion after publication.
    import os
    import subprocess
    raw_tracked = subprocess.check_output(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
    )
    tracked = [p for p in raw_tracked.decode().split("\0") if p]
    raw_deleted = subprocess.check_output(
        ["git", "-C", str(repo_root), "ls-files", "-z", "--deleted"],
    )
    deleted = {p for p in raw_deleted.decode().split("\0") if p}
    return [
        p for p in tracked
        if not (p in deleted and not os.path.lexists(repo_root / p))
    ]


def test_candidate_repository_contains_no_tracked_legacy_paths(repo_root):
    effective = effective_tracked_paths(repo_root)
    legacy = [p for p in effective if p == "nix-darwin" or p.startswith("nix-darwin/")]
    assert legacy == [], f"effective candidate contains tracked legacy paths: {legacy}"


def test_effective_tracked_paths_fixture_distinguishes_present_and_deleted(tmp_path):
    import subprocess
    for args in (["init", "-b", "main"], ["config", "user.name", "Fixture"],
                 ["config", "user.email", "fixture@example.invalid"]):
        subprocess.run(["git", "-c", "core.excludesFile=/dev/null", "-C", str(tmp_path), *args], check=True, capture_output=True)
    (tmp_path / "flake.nix").write_text("{}")
    (tmp_path / "nix-darwin/.serena").mkdir(parents=True)
    (tmp_path / "nix-darwin/.serena/project.yml").write_text("test")
    (tmp_path / "nix-darwin/bin").mkdir(parents=True)
    target = tmp_path / "claude_target"
    target.write_text("#!/bin/sh\n")
    (tmp_path / "nix-darwin/bin/claude").symlink_to(target)
    (tmp_path / "nix-darwin/hidden").mkdir(parents=True)
    (tmp_path / "nix-darwin/hidden/.dotfile").write_text("dot")
    subprocess.run(["git", "-c", "core.excludesFile=/dev/null", "-C", str(tmp_path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True, capture_output=True)

    # When tracked and present, effective_tracked_paths includes all of them
    effective = effective_tracked_paths(tmp_path)
    legacy = [p for p in effective if p.startswith("nix-darwin/")]
    assert set(legacy) == {
        "nix-darwin/.serena/project.yml",
        "nix-darwin/bin/claude",
        "nix-darwin/hidden/.dotfile",
    }

    # Remove symlink and ordinary file from disk without staging
    (tmp_path / "nix-darwin/bin/claude").unlink()
    (tmp_path / "nix-darwin/.serena/project.yml").unlink()
    effective_after_deletion = effective_tracked_paths(tmp_path)
    # The deleted files are excluded because they do not lexist on disk
    assert "nix-darwin/bin/claude" not in effective_after_deletion
    assert "nix-darwin/.serena/project.yml" not in effective_after_deletion
    # But surviving file remains reported
    assert "nix-darwin/hidden/.dotfile" in effective_after_deletion

    # If a file is reported deleted by git but actually exists on disk (e.g., recreated),
    # the carve-out must NOT exclude it
    (tmp_path / "nix-darwin/bin/claude").symlink_to(target)
    effective_recreated = effective_tracked_paths(tmp_path)
    assert "nix-darwin/bin/claude" in effective_recreated
