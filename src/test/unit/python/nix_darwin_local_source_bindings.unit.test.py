# nix-darwin-test-source-target: libexec/nix_darwin_local/local_source_binding.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/local_deployment_plan.py
from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "libexec"))
from nix_darwin_local import local_source_binding as binding
from nix_darwin_local import local_deployment_plan as plans
from nix_darwin_local.paths import ConfigError


def source_binding():
    revision = "a" * 40
    nar = "sha256-" + "A" * 43 + "="
    source = dict(key="core", checkout="/operator/core", repository="https://example.invalid/core.git",
                  release_ref="refs/heads/main", revision=revision, head_ref="refs/heads/main",
                  head_revision=revision, clean=True, operation_state="clean", store_path="/nix/store/" + "a" * 32 + "-source",
                  nar_hash=nar, publication=None)
    lock = {"nodes": {"core": {"locked": {"type": "git", "url": source["repository"], "rev": revision, "narHash": nar}}}}
    return [{"node": "core", "source": source}], lock


def test_exact_catalog_binding_requires_no_override():
    records, lock = source_binding()
    binding.validate_local_sources(records, lock)
    assert binding.capture_local_sources(SimpleNamespace(sources=()), lock) == []
    binding.revalidate_local_sources(SimpleNamespace(sources=()), lock, [])
    records[0]["source"]["publication"] = {"remote": "origin", "ref": "refs/heads/main"}
    binding.validate_local_sources(records, lock)


@pytest.mark.parametrize("field,value", [
    ("revision", "b" * 40), ("nar_hash", "sha256-wrong"), ("repository", "https://example.invalid/other.git"),
    ("release_ref", "refs/remotes/origin/main"), ("head_revision", 40),
    ("checkout", "relative"), ("store_path", "/mutable/source"), ("clean", "not-a-bool"),
    ("operation_state", "merging"), ("publication", {"remote": "origin"}), ("key", 4),
])
def test_catalog_binding_rejects_drift_and_malformed_identity(field, value):
    records, lock = source_binding()
    records[0]["source"][field] = value
    with pytest.raises(ConfigError):
        binding.validate_local_sources(records, lock)


def test_catalog_binding_permits_dirty_and_head_divergence():
    records, lock = source_binding()
    records[0]["source"]["clean"] = False
    records[0]["source"]["head_revision"] = "b" * 40
    records[0]["source"]["head_ref"] = "refs/heads/feature"
    binding.validate_local_sources(records, lock)


@pytest.mark.parametrize("mutation", ["unknown", "duplicate", "record", "node", "list"])
def test_catalog_binding_has_closed_schema(mutation):
    records, lock = source_binding()
    if mutation == "unknown": records[0]["source"]["command"] = "run"
    elif mutation == "duplicate": records.append(copy.deepcopy(records[0]))
    elif mutation == "record": records[0]["extra"] = True
    elif mutation == "node": records[0]["node"] = 2
    else: records = {}
    with pytest.raises(ConfigError):
        binding.validate_local_sources(records, lock)


def test_revalidation_requires_catalog_to_remain_available():
    records, lock = source_binding()
    with pytest.raises(ConfigError, match="drifted"):
        binding.revalidate_local_sources(SimpleNamespace(sources=()), lock, records)


@pytest.mark.parametrize("publication", [
    "origin", {"remote": "origin/unsafe", "ref": "refs/heads/main"},
    {"remote": "origin", "ref": "refs/remotes/origin/main"},
])
def test_source_binding_rejects_unreviewable_publication_metadata(publication):
    records, lock = source_binding()
    records[0]["source"]["publication"] = publication
    with pytest.raises(ConfigError):
        binding.validate_local_sources(records, lock)


@pytest.mark.parametrize("malformed", [None, {"nodes": []}, {"nodes": {"core": 1}}, {"nodes": {"core": {"locked": []}}}])
def test_source_binding_rejects_malformed_lock_graph(malformed):
    records, _ = source_binding()
    with pytest.raises(ConfigError):
        binding.validate_local_sources(records, malformed)


def test_local_plan_v2_roundtrip_preserves_bound_catalog(tmp_path):
    records, lock = source_binding()
    document = dict(schema="nix-update-local-plan-v2", selected_flake="/operator/composition", host="fixture",
                    git_authority={}, target_system="/nix/store/target", predecessor_generation=1,
                    profile_snapshot={}, sources=[], effects={}, preflight_results=[], closure_delta={},
                    created_at="fixture", local_sources=records, lock=lock)
    tmp_path.chmod(0o700)
    written = plans.write_local_plan(tmp_path, document, "fixture")
    assert plans.load_local_plan(tmp_path).digest == written.digest
    assert plans.load_local_plan(tmp_path).document == document
    document["unknown"] = True
    with pytest.raises(ConfigError, match="unknown"):
        plans.validate_local_plan_document(document)


def test_installed_lock_revalidation_rejects_catalog_and_git_operation_drift(tmp_path):
    from dataclasses import asdict
    from nix_darwin_local.source_catalog import SourceCatalogEntry

    entry = SourceCatalogEntry("composition", tmp_path, "https://example.invalid/composition.git", "refs/heads/main")
    record = asdict(entry)
    record["checkout"] = str(tmp_path.resolve())
    workflow = SimpleNamespace(sources=(entry,))
    responses = {}
    def command(label, argv, cwd):
        responses[label] = str(tmp_path / (".git" if argv[-1] == "--git-dir" else argv[-1]))
        return SimpleNamespace(returncode=0)
    workflow.command = command
    workflow.complete_stdout = responses.__getitem__
    binding.revalidate_composition_catalog(workflow, record, "a" * 40, installed_lock=True)
    (tmp_path / "index.lock").write_text("another Git operation")
    with pytest.raises(ConfigError, match="active Git operation"):
        binding.revalidate_composition_catalog(workflow, record, "a" * 40, installed_lock=True)
    drifted = {**record, "release_ref": "refs/heads/release"}
    with pytest.raises(ConfigError, match="authority drifted"):
        binding.revalidate_composition_catalog(workflow, drifted, "a" * 40, installed_lock=True)
    workflow.sources = ()
    with pytest.raises(ConfigError, match="unavailable"):
        binding.revalidate_composition_catalog(workflow, record, "a" * 40, installed_lock=True)
