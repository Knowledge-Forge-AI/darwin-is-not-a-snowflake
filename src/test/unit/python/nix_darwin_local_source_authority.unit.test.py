# nix-darwin-test-source-target: libexec/nix_darwin_local/local_source.py
"""Unit coverage for local Git source authority and immutable realization.

The workflow below is a command-boundary fake.  It keeps the production
authority, loader, and validators running while allowing each Git/Nix failure
state to be selected without mutating a real checkout.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local import local_source
from nix_darwin_local.local_source import (
    LOCK_SCHEMA_REVISION_KEYS,
    GIT_FETCHTREE_RESULT_ONLY_KEYS,
    LocalSourceIdentity,
    REVISION_DEPENDENT_LOCKED_KEYS,
    RESULT_ONLY_METADATA_KEYS,
    SourceCatalogEntry,
    SourceError,
    check_git_operations,
    coerce_catalog_entry,
    copy_serialized_locked_record,
    materialize_source,
    observe_source,
    project_git_locked_metadata,
    realize_catalog_locked_nodes,
    validate_source_identity,
)
from nix_darwin_local.source_catalog import Publication

REVISION = "a" * 40
OTHER_REVISION = "b" * 40
NAR_HASH = "sha256-" + base64.b64encode(hashlib.sha256(b"source").digest()).decode()


class BoundaryWorkflow:
    """Minimal command boundary with the same stdout readback contract."""

    def __init__(
        self,
        handler: Callable[[str, list[str], Path], subprocess.CompletedProcess[str]],
    ) -> None:
        self.handler = handler
        self.records: list[tuple[str, list[str], Path, subprocess.CompletedProcess[str]]] = []

    def command(self, label: str, argv: list[str], cwd: Path, **_kwargs: Any):
        result = self.handler(label, list(argv), Path(cwd))
        self.records.append((label, list(argv), Path(cwd), result))
        return result

    def complete_stdout(self, label: str) -> str:
        for record_label, _argv, _cwd, result in reversed(self.records):
            if record_label == label:
                return result.stdout
        raise AssertionError(f"no command result for {label}")


def result(argv: list[str], stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, returncode, stdout, "failure" if returncode else "")


def blob_digest(raw: bytes) -> str:
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()


def make_checkout(tmp_path: Path, *, name: str = "checkout") -> tuple[Path, str, str]:
    checkout = tmp_path / name
    (checkout / ".git").mkdir(parents=True)
    content = b"committed source\n"
    (checkout / "README.md").write_bytes(content)
    tree = f"100644 blob {blob_digest(content)}\tREADME.md\0"
    return checkout, REVISION, tree


def make_entry(checkout: Path, *, key: str = "source", repository: str = "https://example.invalid/source.git",
               release_ref: str = "refs/heads/main", publication: Any = None) -> SourceCatalogEntry:
    return SourceCatalogEntry(
        key=key,
        checkout=checkout,
        repository=repository,
        release_ref=release_ref,
        publication=publication,
    )


def git_handler(
    checkout: Path,
    revision: str = REVISION,
    tree: str = "",
    *,
    worktree_stdout: str = "true",
    worktree_code: int = 0,
    bare_stdout: str = "false",
    bare_code: int = 0,
    toplevel: Path | None = None,
    toplevel_code: int = 0,
    replacement_stdout: str = "",
    replacement_code: int = 0,
    revision_kind: str = "commit",
    revision_kind_code: int = 0,
    ref_stdout: str | None = None,
    ref_code: int = 0,
    head_stdout: str | None = None,
    head_code: int = 0,
    symbolic_stdout: str = "refs/heads/main",
    symbolic_code: int = 0,
    status_stdout: str = "",
    status_code: int = 0,
    tree_code: int = 0,
    git_dir_stdout: str = ".git",
    git_dir_code: int = 0,
    op_paths: dict[str, str] | None = None,
    op_codes: dict[str, int] | None = None,
) -> Callable[[str, list[str], Path], subprocess.CompletedProcess[str]]:
    """Return a deterministic Git command responder for source authority."""
    root = toplevel or checkout
    ref_stdout = revision if ref_stdout is None else ref_stdout
    head_stdout = revision if head_stdout is None else head_stdout
    op_paths = op_paths or {}
    op_codes = op_codes or {}

    def handler(_label: str, argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        assert argv[:3] == ["git", "--no-replace-objects", "--no-optional-locks"]
        args = argv[3:]
        if args == ["rev-parse", "--is-inside-work-tree"]:
            return result(argv, worktree_stdout + "\n", worktree_code)
        if args == ["rev-parse", "--is-bare-repository"]:
            return result(argv, bare_stdout + "\n", bare_code)
        if args == ["rev-parse", "--is-shallow-repository"]:
            return result(argv, "false\n")
        if args == ["rev-parse", "--show-toplevel"]:
            return result(argv, str(root) + "\n", toplevel_code)
        if args == ["replace", "-l"]:
            return result(argv, replacement_stdout, replacement_code)
        if args[:2] == ["cat-file", "-t"]:
            return result(argv, revision_kind + "\n", revision_kind_code)
        if len(args) == 3 and args[:2] == ["rev-parse", "--verify"] and args[2].endswith("^{commit}"):
            return result(argv, ref_stdout + "\n", ref_code)
        if args == ["rev-parse", "HEAD"]:
            return result(argv, head_stdout + "\n", head_code)
        if args == ["symbolic-ref", "-q", "HEAD"]:
            return result(argv, symbolic_stdout, symbolic_code)
        if args == ["rev-parse", "--git-dir"]:
            return result(argv, git_dir_stdout + "\n", git_dir_code)
        if len(args) == 3 and args[:2] == ["rev-parse", "--git-path"]:
            operation = args[2]
            default_path = f".git/{operation}"
            return result(argv, op_paths.get(operation, default_path) + "\n", op_codes.get(operation, 0))
        if args[:1] == ["status"]:
            return result(argv, status_stdout, status_code)
        if args[:4] == ["ls-tree", "-rz", "-r", "--full-tree"]:
            return result(argv, tree, tree_code)
        raise AssertionError(f"unexpected Git command: {argv} in {cwd}")

    return handler


def materialization_handler(
    checkout: Path,
    revision: str,
    tree: str,
    payload: Any,
    **git_options: Any,
) -> Callable[[str, list[str], Path], subprocess.CompletedProcess[str]]:
    git = git_handler(checkout, revision, tree, **git_options)

    def handler(label: str, argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if argv and argv[0] == "nix":
            return result(argv, json.dumps(payload) if not isinstance(payload, str) else payload)
        return git(label, argv, cwd)

    return handler


def valid_identity(checkout: str = "/operator/core", revision: str = REVISION) -> dict[str, Any]:
    return {
        "key": "core",
        "checkout": checkout,
        "repository": "https://example.invalid/core.git",
        "release_ref": "refs/heads/main",
        "revision": revision,
        "head_ref": "refs/heads/main",
        "head_revision": revision,
        "clean": True,
        "operation_state": "clean",
        "store_path": None,
        "nar_hash": None,
        "publication": None,
    }


def existing_store_path() -> str:
    store = Path("/nix/store")
    candidates = [child for child in store.iterdir() if child.is_dir()]
    if not candidates:
        pytest.skip("Nix store has no existing realization for a boundary fixture")
    return str(candidates[0])


def test_identity_mapping_shape_and_immutable_metadata(tmp_path: Path):
    publication = Publication(remote="origin", ref="refs/heads/main")
    identity = LocalSourceIdentity(
        key="core",
        checkout=str(tmp_path),
        repository="https://example.invalid/core.git",
        release_ref="refs/heads/main",
        revision=REVISION,
        head_ref="refs/heads/main",
        head_revision=REVISION,
        publication=publication,
        store_path="/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-source",
        nar_hash=NAR_HASH,
        locked_metadata={"rev": REVISION, "narHash": NAR_HASH},
    )
    assert set(identity) == local_source.SOURCE_IDENTITY_FIELDS
    assert len(identity) == 12
    assert identity["publication"] == {"remote": "origin", "ref": "refs/heads/main"}
    assert identity.to_dict() == identity.as_dict()
    assert identity.path == identity.store_path
    locked = identity.locked
    locked["rev"] = OTHER_REVISION
    assert identity.locked["rev"] == REVISION
    assert identity.get("missing", "default") == "default"
    with pytest.raises(KeyError):
        _ = identity["missing"]


@pytest.mark.parametrize(
    ("publication", "match"),
    [
        (object(), "must be a mapping"),
        ({"remote": "origin"}, "fields are invalid"),
        ({"remote": "origin/other", "ref": "refs/heads/main"}, "remote is invalid"),
        ({"remote": "origin", "ref": None}, "ref is invalid"),
        ({"remote": "origin", "ref": "main"}, "invalid release ref"),
        ({"remote": "origin", "ref": "refs/tags/release"}, "invalid release ref"),
    ],
)
def test_publication_serialization_rejects_malformed_values(publication: Any, match: str):
    identity = LocalSourceIdentity(
        key="core",
        checkout="/operator/core",
        repository="https://example.invalid/core.git",
        release_ref="refs/heads/main",
        revision=REVISION,
        head_ref="refs/heads/main",
        head_revision=REVISION,
        publication=publication,
    )
    with pytest.raises(SourceError, match=match):
        identity.as_dict()


def test_coerce_catalog_entry_uses_actual_type_and_duck_typed_shapes(tmp_path: Path):
    entry = make_entry(tmp_path)
    assert coerce_catalog_entry(entry) is entry
    mapped = {
        "key": "mapped",
        "checkout": str(tmp_path),
        "repository": entry.repository,
        "release_ref": entry.release_ref,
        "publication": {"remote": "origin", "ref": "refs/heads/main"},
    }
    converted = coerce_catalog_entry(mapped)
    assert isinstance(converted, SourceCatalogEntry)
    assert converted.checkout == tmp_path

    class DuckEntry:
        key = "duck"
        checkout = str(tmp_path)
        repository = entry.repository
        release_ref = entry.release_ref

    assert coerce_catalog_entry(DuckEntry()).key == "duck"
    with pytest.raises(SourceError, match="fields are invalid"):
        coerce_catalog_entry({"key": "missing"})
    with pytest.raises(SourceError, match="fields are invalid"):
        coerce_catalog_entry(SimpleNamespace(key="missing"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("key", ""),
        ("key", 1),
        ("checkout", 1),
        ("repository", ""),
        ("release_ref", ""),
        ("revision", ""),
        ("head_ref", 1),
        ("head_revision", ""),
        ("operation_state", 1),
    ],
)
def test_validate_identity_rejects_missing_or_wrong_scalar_fields(field: str, value: Any):
    document = valid_identity()
    document[field] = value
    with pytest.raises(SourceError, match=f"identity {field}"):
        validate_source_identity(document)


def test_validate_identity_accepts_mapping_and_rejects_shape_and_paths():
    document = valid_identity()
    validate_source_identity(document)
    with pytest.raises(SourceError, match="must be a mapping"):
        validate_source_identity([])
    document = valid_identity()
    document["extra"] = True
    with pytest.raises(SourceError, match="identity fields"):
        validate_source_identity(document)

    for bad_path in ("relative", "/operator/core/../core", "/operator/core\x00bad"):
        document = valid_identity(checkout=bad_path)
        with pytest.raises(SourceError, match="checkout"):
            validate_source_identity(document)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("repository", "https://user:secret@example.invalid/core.git", "repository"),
        ("repository", "not a URL", "repository"),
        ("release_ref", "main", "repository or ref"),
        ("head_ref", "refs/remotes/origin/main", "head_ref is invalid"),
        ("revision", "A" * 40, "revision"),
        ("revision", "a" * 39, "revision"),
        ("head_revision", "A" * 40, "head_revision"),
        ("head_revision", "a" * 39, "head_revision"),
        ("clean", "not-a-bool", "operation_state is invalid"),
        ("operation_state", "observing", "operation_state is invalid"),
    ],
)
def test_validate_identity_rejects_authority_mismatches(field: str, value: Any, match: str):
    document = valid_identity()
    document[field] = value
    with pytest.raises(SourceError, match=match):
        validate_source_identity(document)


def test_validate_identity_permits_dirty_and_head_divergence():
    document = valid_identity()
    document["clean"] = False
    document["head_revision"] = "b" * 40
    document["head_ref"] = "refs/heads/feature"
    validate_source_identity(document)

    document["head_ref"] = "HEAD"
    validate_source_identity(document)


def test_validate_identity_materialization_pairing_and_nar_rules():
    document = valid_identity()
    with pytest.raises(SourceError, match="requires store_path"):
        validate_source_identity(document, materialized=True)

    document["store_path"] = "/nix/store/example-source"
    with pytest.raises(SourceError, match="must be paired"):
        validate_source_identity(document)
    document["nar_hash"] = "sha256-!"
    with pytest.raises(SourceError, match="nar_hash"):
        validate_source_identity(document)
    document["nar_hash"] = NAR_HASH
    document["store_path"] = "/tmp/source"
    with pytest.raises(SourceError, match="Nix store path"):
        validate_source_identity(document)
    document["store_path"] = "/nix/store/example-source/"
    with pytest.raises(SourceError, match="store_path is not normalized"):
        validate_source_identity(document)

    document = valid_identity()
    document["publication"] = {"remote": "origin", "ref": "refs/heads/main"}
    validate_source_identity(document)


@pytest.mark.parametrize("value", [None, "", "main", "refs/tags/release", "refs/heads/a//b"])
def test_ref_normalizers_remain_strict_for_catalog_and_publication(value: Any):
    with pytest.raises(SourceError):
        local_source._normalize_ref(value)


def test_locked_ref_normalizer_accepts_only_branch_shorthand():
    assert local_source._normalize_locked_ref("main") == "refs/heads/main"
    assert local_source._normalize_locked_ref("refs/heads/release") == "refs/heads/release"
    for value in (None, "", "refs/remotes/origin/main", "refs/tags/release"):
        with pytest.raises(SourceError):
            local_source._normalize_locked_ref(value)


@pytest.mark.parametrize("name", ["GIT_GRAFT_FILE", "GIT_SHALLOW_FILE"])
def test_source_environment_rejects_ancestry_overrides(monkeypatch: pytest.MonkeyPatch, name: str):
    monkeypatch.setenv(name, "/nonexistent/ancestry-override")
    with pytest.raises(SourceError, match=name):
        local_source._validate_source_environment()


def test_source_environment_rejects_git_and_nix_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    with pytest.raises(SourceError, match="GIT_CONFIG_COUNT"):
        local_source._validate_source_environment()
    monkeypatch.delenv("GIT_CONFIG_COUNT")
    monkeypatch.setenv("NIX_CONFIG", "experimental-features = nix-command")
    with pytest.raises(SourceError, match="NIX_CONFIG"):
        local_source._validate_source_environment()


def test_nix_string_literal_escapes_interpolation_bytes_and_rejects_controls():
    assert local_source._nix_string_literal('a\\b"$c') == '"a\\\\b\\"\\$c"'
    for value in (None, "bad\nvalue", "bad\x7fvalue"):
        with pytest.raises(SourceError, match="Nix string"):
            local_source._nix_string_literal(value)


@pytest.mark.parametrize("name", ["info/grafts", "shallow"])
@pytest.mark.parametrize("shape", ["file", "dangling-symlink"])
def test_observe_source_rejects_ancestry_files_even_at_exact_head(tmp_path: Path, name: str, shape: str):
    checkout, revision, tree = make_checkout(tmp_path)
    marker = checkout / ".git" / name
    marker.parent.mkdir(parents=True, exist_ok=True)
    if shape == "file":
        marker.write_text("")
    else:
        marker.symlink_to(tmp_path / "absent")
    workflow = BoundaryWorkflow(git_handler(checkout, revision, tree))
    with pytest.raises(SourceError, match="graft or shallow"):
        observe_source(workflow, make_entry(checkout), revision)


@pytest.mark.parametrize("name", ["info/grafts", "shallow"])
def test_observe_source_rejects_failed_ancestry_path_probe(tmp_path: Path, name: str):
    checkout, revision, tree = make_checkout(tmp_path)
    workflow = BoundaryWorkflow(git_handler(checkout, revision, tree, op_codes={name: 128}))
    with pytest.raises(SourceError, match="probe failed"):
        observe_source(workflow, make_entry(checkout), revision)


def test_observe_source_success_binds_exact_identity_and_git_flags(tmp_path: Path):
    checkout, revision, tree = make_checkout(tmp_path)
    workflow = BoundaryWorkflow(git_handler(checkout, revision, tree))
    entry = make_entry(
        checkout,
        publication=Publication(remote="origin", ref="refs/heads/main"),
    )
    identity = observe_source(workflow, entry, revision)
    assert identity.revision == revision
    assert identity.head_ref == "refs/heads/main"
    assert identity.store_path is None
    assert identity.publication == {"remote": "origin", "ref": "refs/heads/main"}
    assert all(
        command[1][:3] == ["git", "--no-replace-objects", "--no-optional-locks"]
        for command in workflow.records
        if command[1][0] == "git"
    )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("key", "", "catalog key"),
        ("repository", "", "repository"),
        ("checkout", "/missing", "does not exist"),
        ("release_ref", "main", "invalid release ref"),
    ],
)
def test_observe_source_rejects_catalog_basics(tmp_path: Path, field: str, value: Any, match: str):
    checkout, revision, tree = make_checkout(tmp_path)
    entry = make_entry(checkout)
    if field == "checkout":
        entry = make_entry(Path(value))
    else:
        entry = SourceCatalogEntry(
            key=value if field == "key" else entry.key,
            checkout=entry.checkout,
            repository=value if field == "repository" else entry.repository,
            release_ref=value if field == "release_ref" else entry.release_ref,
        )
    with pytest.raises(SourceError, match=match):
        observe_source(BoundaryWorkflow(git_handler(checkout, revision, tree)), entry, revision)


def test_observe_source_rejects_expected_authority_and_invalid_revision(tmp_path: Path):
    checkout, revision, tree = make_checkout(tmp_path)
    entry = make_entry(checkout)
    workflow = BoundaryWorkflow(git_handler(checkout, revision, tree))
    with pytest.raises(SourceError, match="release ref"):
        observe_source(workflow, entry, revision, expected_ref="refs/heads/feature")
    with pytest.raises(SourceError, match="repository URL"):
        observe_source(workflow, entry, revision, expected_repository="https://example.invalid/other.git")
    with pytest.raises(SourceError, match="repository URL is invalid"):
        observe_source(workflow, entry, revision, expected_repository="https://user:secret@example.invalid/repo.git")
    for bad_revision in (None, "A" * 40, "a" * 39):
        with pytest.raises(SourceError, match="40-character"):
            observe_source(workflow, entry, bad_revision)


@pytest.mark.parametrize(
    ("options", "match"),
    [
        ({"worktree_code": 1}, "ordinary Git worktree"),
        ({"worktree_stdout": "false"}, "ordinary Git worktree"),
        ({"bare_code": 1}, "ordinary Git worktree"),
        ({"bare_stdout": "true"}, "ordinary Git worktree"),
        ({"toplevel_code": 1}, "accessible Git repository"),
        ({"replacement_code": 1}, "replacement refs"),
        ({"replacement_stdout": "deadbeef\n"}, "replacement refs"),
        ({"revision_kind_code": 1}, "does not contain requested"),
        ({"revision_kind": "tree"}, "not a commit"),
    ],
)
def test_observe_source_rejects_git_authority_failures(tmp_path: Path, options: dict[str, Any], match: str):
    checkout, revision, tree = make_checkout(tmp_path)
    workflow = BoundaryWorkflow(git_handler(checkout, revision, tree, **options))
    with pytest.raises(SourceError, match=match):
        observe_source(workflow, make_entry(checkout), revision)


def test_observe_source_rejects_checkout_outside_reported_git_root(tmp_path: Path):
    checkout, revision, tree = make_checkout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    workflow = BoundaryWorkflow(git_handler(checkout, revision, tree, toplevel=outside))
    with pytest.raises(SourceError, match="outside repository root"):
        observe_source(workflow, make_entry(checkout), revision)


@pytest.mark.parametrize(
    ("symbolic_code", "symbolic_stdout", "head_stdout", "expected_head_ref", "expected_head_rev"),
    [
        (0, "refs/heads/feature\n", REVISION, "refs/heads/feature", REVISION),
        (0, "refs/heads/main\n", OTHER_REVISION, "refs/heads/main", OTHER_REVISION),
        (2, "", REVISION, "refs/heads/main", REVISION),
        (1, "", OTHER_REVISION, "refs/heads/main", OTHER_REVISION),
    ],
)
def test_observe_source_permits_head_state_variants(
    tmp_path: Path,
    symbolic_code: int,
    symbolic_stdout: str,
    head_stdout: str,
    expected_head_ref: str,
    expected_head_rev: str,
):
    checkout, revision, tree = make_checkout(tmp_path)
    handler = git_handler(
        checkout,
        revision,
        tree,
        symbolic_code=symbolic_code,
        symbolic_stdout=symbolic_stdout,
        head_stdout=head_stdout,
    )
    identity = observe_source(BoundaryWorkflow(handler), make_entry(checkout), revision)
    assert identity.revision == revision
    assert identity.head_revision == expected_head_rev
    assert identity.head_ref == expected_head_ref


def test_read_source_state_accepts_exact_detached_head():
    checkout = Path("/operator/source")
    handler = git_handler(checkout, symbolic_code=1, symbolic_stdout="", head_stdout=REVISION)
    workflow = BoundaryWorkflow(handler)
    assert local_source._read_source_state(
        workflow, "source", checkout, "refs/heads/main", REVISION, "unit"
    ) == (REVISION, None, "refs/heads/main", REVISION)


def test_read_source_state_rejects_ref_and_head_command_failures():
    checkout = Path("/operator/source")
    with pytest.raises(SourceError, match="obtain HEAD"):
        local_source._read_source_state(
            BoundaryWorkflow(git_handler(checkout, head_code=1)),
            "source", checkout, "refs/heads/main", REVISION, "unit",
        )
    # Ref missing or resolved to different revision does not fail
    workflow = BoundaryWorkflow(git_handler(checkout, ref_code=1))
    res = local_source._read_source_state(
        workflow, "source", checkout, "refs/heads/main", REVISION, "unit"
    )
    assert res[0] == ""
    workflow2 = BoundaryWorkflow(git_handler(checkout, ref_stdout=OTHER_REVISION))
    res2 = local_source._read_source_state(
        workflow2, "source", checkout, "refs/heads/main", REVISION, "unit"
    )
    assert res2[0] == OTHER_REVISION


def test_git_operation_helpers_fail_closed_and_check_both_paths(tmp_path: Path):
    checkout, _revision, _tree = make_checkout(tmp_path)
    with pytest.raises(SourceError, match="git-dir"):
        local_source._git_directory(
            BoundaryWorkflow(git_handler(checkout, git_dir_code=1)),
            "source", checkout, "unit",
        )

    relative = local_source._git_directory(
        BoundaryWorkflow(git_handler(checkout, git_dir_stdout=".git")),
        "source", checkout, "unit",
    )
    absolute = local_source._git_directory(
        BoundaryWorkflow(git_handler(checkout, git_dir_stdout=str(checkout / ".git"))),
        "source", checkout, "unit",
    )
    assert relative == absolute == checkout / ".git"

    with pytest.raises(SourceError, match="probe failed"):
        local_source._check_active_git_operations(
            BoundaryWorkflow(git_handler(checkout, op_codes={"MERGE_HEAD": 23})),
            "source", checkout, "unit",
        )

    direct_marker = checkout / ".git" / "HEAD.lock"
    direct_marker.write_text("lock")
    with pytest.raises(SourceError, match="active Git operation: HEAD.lock"):
        local_source._check_active_git_operations(
            BoundaryWorkflow(git_handler(checkout)), "source", checkout, "unit"
        )

    candidate = checkout / "candidate-link"
    candidate.symlink_to(checkout / ".git")
    with pytest.raises(SourceError, match="active Git operation: MERGE_HEAD"):
        local_source._check_active_git_operations(
            BoundaryWorkflow(git_handler(checkout, op_paths={"MERGE_HEAD": str(candidate)})),
            "source", checkout, "unit",
        )

    direct_marker.unlink()
    check_git_operations(BoundaryWorkflow(git_handler(checkout)), checkout, "source")


@pytest.mark.parametrize(
    ("status_code", "status_stdout", "tree_code", "tree", "match"),
    [
        (1, "", 0, "", "check Git status"),
        (0, " M README.md\n", 0, "", "tracked drift"),
        (0, "", 1, "", "read committed source"),
        (0, "", 0, "malformed", "tree.*invalid"),
        (0, "", 0, "100644 tree " + "a" * 40 + "\tREADME.md\0", "non-ordinary"),
        (0, "", 0, "100600 blob " + "a" * 40 + "\tREADME.md\0", "non-ordinary"),
    ],
)
def test_tracked_source_drift_helper_rejects_malformed_observations(
    tmp_path: Path,
    status_code: int,
    status_stdout: str,
    tree_code: int,
    tree: str,
    match: str,
):
    checkout, _revision, _clean_tree = make_checkout(tmp_path)
    workflow = BoundaryWorkflow(
        git_handler(
            checkout,
            status_code=status_code,
            status_stdout=status_stdout,
            tree_code=tree_code,
            tree=tree,
        )
    )
    with pytest.raises(SourceError, match=match):
        local_source._check_tracked_source_drift(workflow, "source", checkout, checkout, "unit")


def test_tracked_source_drift_helper_checks_missing_modes_and_symlinks(tmp_path: Path):
    checkout, _revision, _clean_tree = make_checkout(tmp_path)
    good = (checkout / "README.md").read_bytes()

    missing_tree = f"100644 blob {blob_digest(good)}\tmissing.txt\0"
    with pytest.raises(SourceError, match="missing"):
        local_source._check_tracked_source_drift(
            BoundaryWorkflow(git_handler(checkout, tree=missing_tree)),
            "source", checkout, checkout, "unit",
        )

    (checkout / "README.md").unlink()
    (checkout / "README.md").mkdir()
    directory_tree = f"100644 blob {blob_digest(good)}\tREADME.md\0"
    with pytest.raises(SourceError, match="tracked drift"):
        local_source._check_tracked_source_drift(
            BoundaryWorkflow(git_handler(checkout, tree=directory_tree)),
            "source", checkout, checkout, "unit",
        )

    symlink_checkout, _revision, _tree = make_checkout(tmp_path, name="symlink_repo")
    target = symlink_checkout / "target"
    target.write_bytes(b"target\n")
    link = symlink_checkout / "link"
    link.symlink_to("target")
    link_tree = f"120000 blob {blob_digest(os.fsencode('target'))}\tlink\0"
    local_source._check_tracked_source_drift(
        BoundaryWorkflow(git_handler(symlink_checkout, tree=link_tree)),
        "source", symlink_checkout, symlink_checkout, "unit",
    )

    bad_link = f"120000 blob {blob_digest(os.fsencode('wrong'))}\tlink\0"
    with pytest.raises(SourceError, match="tracked drift"):
        local_source._check_tracked_source_drift(
            BoundaryWorkflow(git_handler(symlink_checkout, tree=bad_link)),
            "source", symlink_checkout, symlink_checkout, "unit",
        )


def test_tracked_source_drift_helper_checks_executable_and_blob_bytes(tmp_path: Path):
    checkout, _revision, _tree = make_checkout(tmp_path)
    executable = checkout / "tool"
    executable.write_bytes(b"#!/bin/sh\n")
    executable.chmod(0o755)
    tree = f"100755 blob {blob_digest(executable.read_bytes())}\ttool\0"
    local_source._check_tracked_source_drift(
        BoundaryWorkflow(git_handler(checkout, tree=tree)),
        "source", checkout, checkout, "unit",
    )

    with pytest.raises(SourceError, match="tracked drift"):
        local_source._check_tracked_source_drift(
            BoundaryWorkflow(git_handler(checkout, tree=f"100755 blob {'a' * 40}\ttool\0")),
            "source", checkout, checkout, "unit",
        )
    executable.chmod(0o644)
    with pytest.raises(SourceError, match="tracked drift"):
        local_source._check_tracked_source_drift(
            BoundaryWorkflow(git_handler(checkout, tree=tree)),
            "source", checkout, checkout, "unit",
        )


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ("command-failure", "unable to materialize"),
        ("invalid-json", "invalid JSON"),
        ([], "JSON object"),
        ({"path": "/nix/store/source", "rev": OTHER_REVISION, "narHash": NAR_HASH}, "locked rev"),
        ({"path": "/nix/store/source", "rev": REVISION, "narHash": "not-sri"}, "narHash"),
        ({"path": "/tmp/source", "rev": REVISION, "narHash": NAR_HASH}, "store path"),
        ({"path": "/nix/store/no-such-source", "rev": REVISION, "narHash": NAR_HASH}, "does not exist"),
    ],
)
def test_materialize_source_rejects_malformed_nix_output(
    tmp_path: Path, payload: Any, match: str
):
    checkout, revision, tree = make_checkout(tmp_path)
    if payload == "command-failure":
        base_handler = materialization_handler(checkout, revision, tree, {})

        def failing(label: str, argv: list[str], cwd: Path):
            if argv and argv[0] == "nix":
                return result(argv, returncode=1)
            return base_handler(label, argv, cwd)

        handler = failing
    elif payload == "invalid-json":
        handler = materialization_handler(checkout, revision, tree, "not-json")
    else:
        handler = materialization_handler(checkout, revision, tree, payload)
    with pytest.raises(SourceError, match=match):
        materialize_source(BoundaryWorkflow(handler), make_entry(checkout), revision)


def test_materialize_source_uses_fetch_tree_and_preserves_locked_metadata(tmp_path: Path):
    checkout, revision, tree = make_checkout(tmp_path)
    store_path = existing_store_path()
    payload = {
        "path": store_path,
        "rev": revision,
        "narHash": NAR_HASH,
        "shortRev": revision[:7],
    }
    workflow = BoundaryWorkflow(materialization_handler(checkout, revision, tree, payload))
    result_identity = materialize_source(workflow, make_entry(checkout), revision)
    assert result_identity.path == store_path
    assert result_identity.locked == {
        "rev": revision,
        "narHash": NAR_HASH,
        "shortRev": revision[:7],
    }
    nix_commands = [argv for _label, argv, _cwd, _result in workflow.records if argv[0] == "nix"]
    assert len(nix_commands) == 1
    assert "--expr" in nix_commands[0]
    expression = nix_commands[0][nix_commands[0].index("--expr") + 1]
    assert "builtins.fetchTree" in expression
    assert "flake metadata" not in expression


def test_materialize_source_wraps_post_snapshot_revalidation_failure(tmp_path: Path):
    checkout, revision, tree = make_checkout(tmp_path)
    store_path = existing_store_path()
    payload = {"path": store_path, "rev": revision, "narHash": NAR_HASH}
    base = materialization_handler(checkout, revision, tree, payload)
    final_op_seen = False
    snapshot_done = False

    def handler(label: str, argv: list[str], cwd: Path):
        nonlocal final_op_seen, snapshot_done
        if argv and argv[0] == "nix":
            snapshot_done = True
        if snapshot_done and "Git op check final" in label:
            final_op_seen = True
            return result(argv, returncode=1)
        return base(label, argv, cwd)

    with pytest.raises(SourceError, match="revalidation failed"):
        materialize_source(BoundaryWorkflow(handler), make_entry(checkout), revision)
    assert final_op_seen


def valid_lock(repository: str, revision: str = REVISION, *, ref: str | None = "main", nar_hash: str = NAR_HASH) -> dict[str, Any]:
    locked: dict[str, Any] = {"type": "git", "url": repository, "rev": revision, "narHash": nar_hash}
    if ref is not None:
        locked["ref"] = ref
    return {"nodes": {"source": {"locked": locked}}}


def test_realize_locked_nodes_supports_lock_and_catalog_shapes(tmp_path: Path):
    checkout, revision, tree = make_checkout(tmp_path)
    repository = "https://example.invalid/source.git"
    entry = make_entry(checkout, repository=repository)
    workflow = BoundaryWorkflow(
        materialization_handler(
            checkout,
            revision,
            tree,
            {"path": existing_store_path(), "rev": revision, "narHash": NAR_HASH},
        )
    )
    lock = valid_lock(repository)
    assert "source" in realize_catalog_locked_nodes(workflow, lock, {"entry": entry})
    assert "source" in realize_catalog_locked_nodes(workflow, lock, [entry])
    assert "source" in realize_catalog_locked_nodes(workflow, lock, entry)
    catalog = SimpleNamespace(entries=(entry,))
    assert "source" in realize_catalog_locked_nodes(workflow, lock, catalog)

    lock_file_dir = tmp_path / "lock-dir"
    lock_file_dir.mkdir()
    (lock_file_dir / "flake.lock").write_text(json.dumps(lock))
    assert "source" in realize_catalog_locked_nodes(workflow, lock_file_dir, {"entry": entry})


def test_realize_locked_nodes_skips_non_git_and_unmatched_nodes(tmp_path: Path):
    checkout, _revision, _tree = make_checkout(tmp_path)
    lock = {
        "nodes": {
            "list": [],
            "locked-list": {"locked": []},
            "tarball": {"locked": {"type": "tarball"}},
            "missing-url": {"locked": {"type": "git", "url": ""}},
            "bad-url": {"locked": {"type": "git", "url": 7}},
            "unmatched": {"locked": {"type": "git", "url": "https://other.invalid/repo.git"}},
        }
    }
    assert realize_catalog_locked_nodes(
        BoundaryWorkflow(git_handler(checkout)), lock, {"entry": make_entry(checkout)}
    ) == {}


def test_realize_locked_nodes_rejects_lock_inputs_and_node_shapes(tmp_path: Path):
    checkout, _revision, _tree = make_checkout(tmp_path)
    entry = make_entry(checkout)
    workflow = BoundaryWorkflow(git_handler(checkout))
    missing = tmp_path / "missing.lock"
    with pytest.raises(SourceError, match="not found"):
        realize_catalog_locked_nodes(workflow, missing, entry)
    invalid = tmp_path / "invalid.lock"
    invalid.write_text("not-json")
    with pytest.raises(SourceError, match="invalid JSON"):
        realize_catalog_locked_nodes(workflow, invalid, entry)
    with pytest.raises(SourceError, match="Mapping, Path"):
        realize_catalog_locked_nodes(workflow, 1, entry)
    for bad_lock in ({}, {"nodes": []}):
        with pytest.raises(SourceError, match="nodes mapping"):
            realize_catalog_locked_nodes(workflow, bad_lock, entry)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("ambiguous", "ambiguous catalog"),
        ("rev", "invalid rev"),
        ("expected", "does not match expected"),
        ("missing-ref", "missing ref"),
        ("invalid-ref", "invalid ref"),
        ("mismatch-ref", "does not agree"),
        ("missing-nar", "missing narHash"),
    ],
)
def test_realize_locked_nodes_rejects_authority_mismatches(tmp_path: Path, mutation: str, match: str):
    checkout, revision, tree = make_checkout(tmp_path)
    repository = "https://example.invalid/source.git"
    entry = make_entry(checkout, repository=repository)
    lock = valid_lock(repository)
    catalog: Any = {"entry": entry}
    if mutation == "ambiguous":
        catalog = [entry, make_entry(checkout, key="other", repository=repository)]
        lock = valid_lock(repository)
        lock["nodes"] = {"unrelated-node": lock["nodes"]["source"]}
    elif mutation == "rev":
        lock["nodes"]["source"]["locked"]["rev"] = "bad"
    elif mutation == "expected":
        lock = valid_lock(repository, OTHER_REVISION)
        # Keep the source ref/commit valid so expected-revisions is the gate.
        lock["nodes"]["source"]["locked"]["rev"] = revision
    elif mutation == "missing-ref":
        lock = valid_lock(repository, ref=None)
        other_entry = make_entry(checkout, repository=repository, release_ref="refs/heads/release")
        catalog = {"entry": other_entry}
    elif mutation == "invalid-ref":
        lock["nodes"]["source"]["locked"]["ref"] = "refs/remotes/origin/main"
    elif mutation == "mismatch-ref":
        lock["nodes"]["source"]["locked"]["ref"] = "develop"
    elif mutation == "missing-nar":
        lock["nodes"]["source"]["locked"].pop("narHash")

    workflow = BoundaryWorkflow(
        materialization_handler(
            checkout,
            revision,
            tree,
            {"path": existing_store_path(), "rev": revision, "narHash": NAR_HASH},
        )
    )
    expected = {"source": OTHER_REVISION} if mutation == "expected" else None
    with pytest.raises(SourceError, match=match):
        realize_catalog_locked_nodes(workflow, lock, catalog, expected_revisions=expected)


def test_realize_locked_nodes_rejects_nar_hash_mismatch_after_materialization(tmp_path: Path):
    checkout, revision, tree = make_checkout(tmp_path)
    repository = "https://example.invalid/source.git"
    lock = valid_lock(repository, nar_hash="sha256-" + "A" * 43 + "=")
    workflow = BoundaryWorkflow(
        materialization_handler(
            checkout,
            revision,
            tree,
            {"path": existing_store_path(), "rev": revision, "narHash": NAR_HASH},
        )
    )
    with pytest.raises(SourceError, match="does not agree"):
        realize_catalog_locked_nodes(workflow, lock, {"entry": make_entry(checkout, repository=repository)})


def test_project_git_locked_metadata_excludes_result_only_fields():
    existing = {
        "type": "git",
        "url": "ssh://git@github.com/example/repo.git",
        "ref": "refs/heads/main",
        "rev": REVISION,
        "narHash": NAR_HASH,
    }
    identity = SimpleNamespace(
        revision=REVISION,
        nar_hash=NAR_HASH,
        locked={
            "lastModified": 1789335288,
            "lastModifiedDate": "20260913213448",
            "narHash": NAR_HASH,
            "path": "/nix/store/dummy-source",
            "outPath": "/nix/store/dummy-source",
            "rev": REVISION,
            "shortRev": REVISION[:7],
            "submodules": False,
        },
    )
    projected = project_git_locked_metadata(existing, identity, expected_revision=REVISION)
    assert "shortRev" not in projected
    assert "lastModifiedDate" not in projected
    assert "path" not in projected
    assert "outPath" not in projected
    assert "submodules" not in projected
    assert projected["type"] == "git"
    assert projected["url"] == "ssh://git@github.com/example/repo.git"
    assert projected["ref"] == "refs/heads/main"
    assert projected["rev"] == REVISION
    assert projected["narHash"] == NAR_HASH
    assert projected["lastModified"] == 1789335288


def test_project_git_locked_metadata_validates_type_and_mapping():
    identity = SimpleNamespace(revision=REVISION, nar_hash=NAR_HASH, locked={})
    with pytest.raises(SourceError, match="existing locked node must be a mapping"):
        project_git_locked_metadata("not-a-mapping", identity)

    with pytest.raises(SourceError, match="unsupported locked node type for Git projection: 'tarball'"):
        project_git_locked_metadata({"type": "tarball"}, identity)

    with pytest.raises(SourceError, match="unsupported locked node type for Git projection: None"):
        project_git_locked_metadata({}, identity)


def test_project_git_locked_metadata_revision_refresh_purges_stale_metadata():
    existing = {
        "type": "git",
        "url": "ssh://git@github.com/example/repo.git",
        "rev": REVISION,
        "narHash": NAR_HASH,
        "lastModified": 1000,
        "revCount": 10,
    }
    new_rev = OTHER_REVISION
    new_nar = "sha256-" + "B" * 43 + "="
    # Identity without revCount
    identity_no_rc = SimpleNamespace(
        revision=new_rev,
        nar_hash=new_nar,
        locked={"lastModified": 2000, "rev": new_rev, "narHash": new_nar},
    )
    projected = project_git_locked_metadata(existing, identity_no_rc, expected_revision=new_rev)
    assert projected["rev"] == new_rev
    assert projected["narHash"] == new_nar
    assert projected["lastModified"] == 2000
    assert "revCount" not in projected

    # Identity with valid revCount
    identity_with_rc = SimpleNamespace(
        revision=new_rev,
        nar_hash=new_nar,
        locked={"lastModified": 3000, "revCount": 15, "rev": new_rev, "narHash": new_nar},
    )
    projected2 = project_git_locked_metadata(existing, identity_with_rc, expected_revision=new_rev)
    assert projected2["revCount"] == 15
    assert projected2["lastModified"] == 3000


def test_project_git_locked_metadata_preserves_existing_fetcher_options():
    existing = {
        "type": "git",
        "url": "ssh://git@github.com/example/repo.git",
        "ref": "refs/heads/main",
        "submodules": True,
        "dir": "subpath",
        "allRefs": True,
        "rev": REVISION,
        "narHash": NAR_HASH,
    }
    identity = SimpleNamespace(
        revision=REVISION,
        nar_hash=NAR_HASH,
        locked={"lastModified": 5000, "submodules": False, "shortRev": "abc"},
    )
    projected = project_git_locked_metadata(existing, identity)
    assert projected["submodules"] is True
    assert projected["dir"] == "subpath"
    assert projected["allRefs"] is True
    assert "shortRev" not in projected


def test_project_git_locked_metadata_idempotent_when_unchanged():
    existing = {
        "type": "git",
        "url": "ssh://git@github.com/example/repo.git",
        "ref": "refs/heads/main",
        "rev": REVISION,
        "narHash": NAR_HASH,
        "lastModified": 1789335288,
        "revCount": 42,
    }
    identity = SimpleNamespace(
        revision=REVISION,
        nar_hash=NAR_HASH,
        locked={
            "lastModified": 1789335288,
            "lastModifiedDate": "20260913213448",
            "narHash": NAR_HASH,
            "rev": REVISION,
            "shortRev": REVISION[:7],
            "submodules": False,
        },
    )
    projected = project_git_locked_metadata(existing, identity, expected_revision=REVISION)
    assert projected == existing


def test_project_git_locked_metadata_fails_closed_on_contradiction():
    existing = {"type": "git", "url": "ssh://git@github.com/example/repo.git"}
    # Mismatched expected_revision
    with pytest.raises(SourceError, match="does not match expected revision"):
        project_git_locked_metadata(
            existing,
            SimpleNamespace(revision=REVISION, nar_hash=NAR_HASH),
            expected_revision=OTHER_REVISION,
        )

    # Missing revision
    with pytest.raises(SourceError, match="missing valid revision"):
        project_git_locked_metadata(
            existing,
            SimpleNamespace(revision="", nar_hash=NAR_HASH),
        )

    # Missing or invalid narHash
    with pytest.raises(SourceError, match="missing valid narHash"):
        project_git_locked_metadata(
            existing,
            SimpleNamespace(revision=REVISION, nar_hash="invalid-hash"),
        )

    # Contradiction between locked metadata and identity
    with pytest.raises(SourceError, match="does not agree with identity revision"):
        project_git_locked_metadata(
            existing,
            SimpleNamespace(
                revision=REVISION,
                nar_hash=NAR_HASH,
                locked={"rev": OTHER_REVISION, "narHash": NAR_HASH},
            ),
        )
    with pytest.raises(SourceError, match="does not agree with identity narHash"):
        project_git_locked_metadata(
            existing,
            SimpleNamespace(
                revision=REVISION,
                nar_hash=NAR_HASH,
                locked={"rev": REVISION, "narHash": "sha256-" + "Z" * 43 + "="},
            ),
        )

    # Contradiction when revision is unchanged but existing locked narHash disagrees
    existing_with_nar = {
        "type": "git",
        "url": "ssh://git@github.com/example/repo.git",
        "rev": REVISION,
        "narHash": NAR_HASH,
    }
    with pytest.raises(SourceError, match="does not agree with materialized source narHash"):
        project_git_locked_metadata(
            existing_with_nar,
            SimpleNamespace(
                revision=REVISION,
                nar_hash="sha256-" + "Z" * 43 + "=",
            ),
        )


def test_lock_metadata_schema_constants():
    assert "shortRev" in RESULT_ONLY_METADATA_KEYS
    assert "lastModifiedDate" in RESULT_ONLY_METADATA_KEYS
    assert "path" in RESULT_ONLY_METADATA_KEYS
    assert "outPath" in RESULT_ONLY_METADATA_KEYS
    assert GIT_FETCHTREE_RESULT_ONLY_KEYS == RESULT_ONLY_METADATA_KEYS
    assert RESULT_ONLY_METADATA_KEYS.isdisjoint(LOCK_SCHEMA_REVISION_KEYS)
    assert LOCK_SCHEMA_REVISION_KEYS == {"rev", "narHash", "lastModified", "revCount"}
    assert REVISION_DEPENDENT_LOCKED_KEYS == {"rev", "narHash", "lastModified", "revCount"}


def test_copy_serialized_locked_record_preserves_path_and_multi_scheme():
    # 1. Direct path child
    direct_path = {
        "type": "path",
        "path": "./config/default",
    }
    copied_direct = copy_serialized_locked_record(direct_path)
    assert copied_direct == {
        "type": "path",
        "path": "./config/default",
    }
    assert copied_direct["path"] == "./config/default"

    # 2. Absolute path child (shaped like live graph)
    live_path = {
        "type": "path",
        "path": "/Users/example/.local/share/repo-map",
        "narHash": "sha256-3rpMGnFMUgBGRH7ndODxwROowz92XUxjnrO4Kp/q2Bk=",
        "lastModified": 1786904022,
    }
    copied_live = copy_serialized_locked_record(live_path)
    assert copied_live == live_path
    assert copied_live["path"] == "/Users/example/.local/share/repo-map"
    assert copied_live["narHash"] == "sha256-3rpMGnFMUgBGRH7ndODxwROowz92XUxjnrO4Kp/q2Bk="
    assert copied_live["lastModified"] == 1786904022

    # 3. GitHub scheme child
    github_child = {
        "type": "github",
        "owner": "nix-darwin",
        "repo": "nix-darwin",
        "rev": "ebec37af18215214173c98cf6356d0aca24a2585",
        "narHash": "sha256-Utk0zd8STPsUJPyjabhzPc5BpPodLTXrwkpXBHYnpeg=",
        "lastModified": 1772129556,
    }
    copied_github = copy_serialized_locked_record(github_child)
    assert copied_github == github_child

    # 4. Gitlab and tarball schemes
    gitlab_child = {
        "type": "gitlab",
        "owner": "example",
        "repo": "tools",
        "rev": "c" * 40,
    }
    assert copy_serialized_locked_record(gitlab_child) == gitlab_child

    tarball_child = {
        "type": "tarball",
        "url": "https://example.invalid/archive.tar.gz",
        "narHash": "sha256-xyz123=",
    }
    assert copy_serialized_locked_record(tarball_child) == tarball_child


def test_copy_serialized_locked_record_detached_deep_copy():
    source = {
        "type": "path",
        "path": "/path/to/source",
        "nested": {"key": "val"},
        "items": [1, 2, 3],
    }
    copied = copy_serialized_locked_record(source)
    assert copied == source

    copied["path"] = "/mutated"
    copied["nested"]["key"] = "mutated"
    copied["items"].append(4)

    assert source["path"] == "/path/to/source"
    assert source["nested"]["key"] == "val"
    assert source["items"] == [1, 2, 3]


def test_copy_serialized_locked_record_fails_closed_on_non_mapping():
    with pytest.raises(SourceError, match="must be a mapping"):
        copy_serialized_locked_record(None)  # type: ignore
    with pytest.raises(SourceError, match="must be a mapping"):
        copy_serialized_locked_record("not a mapping")  # type: ignore
    with pytest.raises(SourceError, match="must be a mapping"):
        copy_serialized_locked_record([1, 2, 3])  # type: ignore
    with pytest.raises(SourceError, match="missing required 'type' attribute"):
        copy_serialized_locked_record({"path": "./config"})
    with pytest.raises(SourceError, match="missing required 'type' attribute"):
        copy_serialized_locked_record({"type": "   ", "path": "./config"})
    with pytest.raises(SourceError, match="missing required 'path' attribute"):
        copy_serialized_locked_record({"type": "path"})
    with pytest.raises(SourceError, match="missing required 'path' attribute"):
        copy_serialized_locked_record({"type": "path", "path": "   "})
