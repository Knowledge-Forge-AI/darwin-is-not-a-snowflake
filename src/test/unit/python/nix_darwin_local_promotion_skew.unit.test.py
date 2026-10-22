# nix-darwin-test-source-target: libexec/nix_darwin_local/local_source_history.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_locked_source.py
"""Historical-realization adverse boundaries with explicit Git/Nix response fakes."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "libexec"))
from nix_darwin_local import local_source as source
from nix_darwin_local import local_source_history as history
from nix_darwin_local import promotion_locked_source as bindings

_SPEC = importlib.util.spec_from_file_location("source_skew_fixture", Path(__file__).with_name("nix_darwin_local_source_authority.unit.test.py"))
assert _SPEC is not None and _SPEC.loader is not None
_FIXTURE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_FIXTURE)
OLD, HEAD, NEW = "a" * 40, "b" * 40, "c" * 40
NAR = "sha256-" + "A" * 43 + "="


@pytest.fixture(scope="module")
def store_path():
    for item in Path("/nix/store").iterdir():
        if re.fullmatch(r"[0-9a-z]{32}-.+", item.name) and item.is_dir():
            return str(item)
    pytest.skip("Nix store unavailable for command-boundary fixture")


def _workflow(tmp_path, store_path, *, payload=None, ancestry=0, move=None, nix_code=0, **git_options):
    checkout, _, tree = _FIXTURE.make_checkout(tmp_path)
    entry = _FIXTURE.make_entry(checkout)
    state = {"head": HEAD}
    metadata = {"rev": OLD, "narHash": NAR, "path": store_path}
    if payload is not None:
        metadata = payload

    def command(label, argv, cwd):
        if argv[0] == "nix":
            assert OLD in argv[-1] and HEAD not in argv[-1]
            if move == "materialization":
                state["head"] = NEW
            return _FIXTURE.result(argv, metadata if isinstance(metadata, str) else json.dumps(metadata), nix_code)
        if argv[3:5] == ["merge-base", "--is-ancestor"]:
            assert argv[5:] == [OLD, HEAD]
            if move == "ancestry" and "final" in label:
                state["head"] = NEW
            return _FIXTURE.result(argv, "", ancestry)
        return _FIXTURE.git_handler(checkout, state["head"], tree, **git_options)(label, argv, cwd)

    return _FIXTURE.BoundaryWorkflow(command), entry


def _realize(workflow, entry):
    return history.realize_historical_source(workflow, entry, OLD,
        expected_repository=entry.repository, expected_ref=entry.release_ref, expected_nar_hash=NAR)


@pytest.mark.parametrize("detached", [False, True])
def test_historical_identity_separates_head_and_exact_realization(tmp_path, store_path, detached):
    workflow, entry = _workflow(tmp_path, store_path, symbolic_code=1 if detached else 0)
    observed, realized = _realize(workflow, entry)
    assert observed.revision == observed.head_revision == HEAD
    assert observed.store_path is observed.nar_hash is None
    assert realized == {"revision": OLD, "store_path": store_path, "nar_hash": NAR}


@pytest.mark.parametrize("code", [1, 128])
def test_divergent_or_inconclusive_ancestry_fails_closed(tmp_path, store_path, code):
    workflow, entry = _workflow(tmp_path, store_path, ancestry=code)
    with pytest.raises(source.SourceError, match="ancestor"):
        _realize(workflow, entry)


@pytest.mark.parametrize("moment", ["materialization", "ancestry"])
def test_head_movement_during_realization_fails_closed(tmp_path, store_path, moment):
    workflow, entry = _workflow(tmp_path, store_path, move=moment)
    with pytest.raises(source.SourceError, match="requested revision|changed"):
        _realize(workflow, entry)


@pytest.mark.parametrize("defect", ["json", "array", "revision", "ref", "nar", "path", "absent", "command"])
def test_nix_realization_must_be_exact(tmp_path, store_path, defect):
    payload = {"rev": OLD, "narHash": NAR, "path": store_path}
    if defect == "json":
        payload = "{"
    elif defect == "array":
        payload = []
    elif defect == "revision":
        payload["rev"] = HEAD
    elif defect == "ref":
        payload["ref"] = "other"
    elif defect == "nar":
        payload["narHash"] = "sha256-" + "B" * 43 + "="
    elif defect == "path":
        payload["path"] = "/nix/store/../tmp"
    elif defect == "absent":
        payload["path"] = "/nix/store/" + "0" * 32 + "-absent-skew-fixture"
    workflow, entry = _workflow(tmp_path, store_path, payload=payload, nix_code=1 if defect == "command" else 0)
    with pytest.raises(source.SourceError):
        _realize(workflow, entry)


@pytest.mark.parametrize("options", [
    {"revision_kind_code": 128}, {"revision_kind": "blob"},
    {"replacement_stdout": OLD}, {"ref_code": 128},
])
def test_historical_path_preserves_source_refusals(tmp_path, store_path, options):
    workflow, entry = _workflow(tmp_path, store_path, **options)
    with pytest.raises(source.SourceError):
        _realize(workflow, entry)


@pytest.mark.parametrize("options", [
    {"status_stdout": " M README.md\0"},
    {"symbolic_stdout": "refs/heads/other"},
])
def test_historical_path_permits_dirty_and_symbolic_ref_variants(tmp_path, store_path, options):
    workflow, entry = _workflow(tmp_path, store_path, **options)
    observed, realized = _realize(workflow, entry)
    assert realized == {"revision": OLD, "store_path": store_path, "nar_hash": NAR}


def _records(store_path):
    records, nodes = [], {"root": {"inputs": {"a": "a", "b": "b"}}}
    for name, locked_rev, head in (("a", HEAD, HEAD), ("b", OLD, HEAD)):
        identity = _FIXTURE.valid_identity()
        identity.update(key=name, repository=f"https://example.invalid/{name}.git",
                        revision=head, head_revision=head, store_path=None, nar_hash=None)
        nodes[name] = {"locked": {"type": "git", "url": identity["repository"],
                                   "ref": "main", "rev": locked_rev, "narHash": NAR}}
        records.append({"schema": bindings.PROMOTION_LOCAL_SOURCE_BINDING_SCHEMA_V2,
                        "node": name, "role": "exact" if name == "a" else "unchanged-lock",
                        "source": identity,
                        "realization": {"revision": locked_rev, "store_path": store_path, "nar_hash": NAR}})
    lock = {"version": 7, "root": "root", "nodes": nodes}
    return records, lock


@pytest.mark.parametrize("marker", ["MERGE_HEAD", "rebase-merge"])
def test_historical_path_refuses_active_git_operations(tmp_path, store_path, marker):
    workflow, entry = _workflow(tmp_path, store_path)
    operation = entry.checkout / ".git" / marker
    if marker == "rebase-merge":
        operation.mkdir()
    else:
        operation.write_text(OLD)
    with pytest.raises(source.SourceError, match="operation"):
        _realize(workflow, entry)


def test_selected_candidate_materializes_exact_commit_with_advanced_head(tmp_path, store_path):
    workflow, entry = _workflow(tmp_path, store_path)
    workflow.sources = (entry,)
    lock = {"version": 7, "root": "root", "nodes": {
        "root": {"inputs": {"source": "source"}},
        "source": {"locked": {"type": "git", "url": entry.repository,
                                 "ref": "main", "rev": OLD, "narHash": NAR}},
    }}
    records = bindings.capture_promotion_local_sources(workflow, lock, preimage_lock=lock, selected_node="source")
    assert len(records) == 1
    assert records[0]["node"] == "source"
    assert records[0]["role"] == "exact"
    assert records[0]["realization"]["revision"] == OLD
    assert any(record[1][0] == "nix" for record in workflow.records)


@pytest.mark.parametrize("defect", ["selected-role", "changed-unrelated", "missing-selected", "mixed-schema", "duplicate", "missing-preimage"])
def test_binding_roles_require_exact_graph_evidence(store_path, defect):
    records, lock = _records(store_path)
    before = deepcopy(lock)
    if defect == "selected-role":
        records[0]["role"] = "unchanged-lock"
    elif defect == "changed-unrelated":
        before["nodes"]["b"]["flake"] = False
    elif defect == "missing-selected":
        records.pop(0)
    elif defect == "mixed-schema":
        records[1].pop("schema")
    elif defect == "duplicate":
        records.append(deepcopy(records[0]))
    else:
        before = None
    with pytest.raises(source.ConfigError):
        bindings.validate_promotion_local_sources(records, lock, preimage_lock=before, selected_node="a")


@pytest.mark.parametrize("defect", ["omission", "head", "realization"])
def test_revalidation_recaptures_complete_reviewed_authority(store_path, monkeypatch, defect):
    reviewed, lock = _records(store_path)
    fresh = deepcopy(reviewed)
    if defect == "omission":
        reviewed.pop()
    elif defect == "head":
        fresh[1]["source"].update(revision=NEW, head_revision=NEW)
    else:
        fresh[1]["realization"]["nar_hash"] = "sha256-" + "B" * 43 + "="
    monkeypatch.setattr(bindings, "capture_promotion_local_sources", lambda *args, **kwargs: fresh)
    with pytest.raises(source.ConfigError, match="drifted"):
        bindings.revalidate_promotion_local_sources(SimpleNamespace(), lock, reviewed,
                                                    preimage_lock=lock, selected_node="a")
