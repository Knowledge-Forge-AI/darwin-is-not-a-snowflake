# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_plan.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_workflow.py
"""Retained-plan interpretation and downgrade boundaries for promotion v4.

Nix responses use the existing local-promotion unit fixture; Git and plan/result
loading are real disposable operations. These are compatibility, not Nix evidence.
"""

from copy import deepcopy
import importlib.util
from pathlib import Path
import shutil

import pytest


_SPEC = importlib.util.spec_from_file_location(
    "promotion_local_compat_fixture",
    Path(__file__).with_name("nix_darwin_local_promotion_local.unit.test.py"),
)
assert _SPEC is not None and _SPEC.loader is not None
_FIXTURE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_FIXTURE)
promotion_modules = _FIXTURE.promotion_modules


def _retained_v3(promotion_modules, repo_root, tmp_path, monkeypatch):
    fixture, modern = _FIXTURE.prepare_local_artifact(
        promotion_modules, repo_root, tmp_path, monkeypatch, "modern"
    )
    document = deepcopy(modern.document)
    assert document["schema"] == "nix-update-promotion-plan-v4"
    document["schema"] = "nix-update-promotion-plan-v3"
    document["plan_digest"] = ""
    records = []
    for record in document["local_sources"]:
        source = record["source"]
        realization = record["realization"]
        assert source["revision"] == realization["revision"]
        source.update(store_path=realization["store_path"], nar_hash=realization["nar_hash"])
        records.append({"node": record["node"], "source": source})
    document["local_sources"] = records
    retained = tmp_path / "retained-v3"
    shutil.copytree(modern.directory, retained)
    for name in ("plan.json", "plan.md"):
        (retained / name).unlink()
    artifact = fixture[8].write_plan(retained, document, "Retained v3 fixture\n")
    return fixture, artifact


def test_retained_v3_load_apply_and_consumed_reconciliation(
    promotion_modules, repo_root, tmp_path, monkeypatch
):
    fixture, artifact = _retained_v3(promotion_modules, repo_root, tmp_path, monkeypatch)
    loaded = fixture[8].load_plan(artifact.directory)
    assert loaded.document["schema"] == "nix-update-promotion-plan-v3"
    assert loaded.digest == artifact.digest
    committed = _FIXTURE.apply_prepared_local_artifact(fixture, artifact)
    # A repeated apply reconciles the consumed authority; it cannot create a
    # second lock commit or reinterpret the retained v3 records as safe-forward.
    assert _FIXTURE.apply_prepared_local_artifact(fixture, artifact) == committed


@pytest.mark.parametrize("schema", ["nix-update-promotion-plan-v2", "nix-update-promotion-plan-v3"])
def test_v4_records_cannot_be_downgraded_by_schema_label(
    promotion_modules, repo_root, tmp_path, monkeypatch, schema
):
    fixture, artifact = _FIXTURE.prepare_local_artifact(
        promotion_modules, repo_root, tmp_path, monkeypatch, "modern"
    )
    document = deepcopy(artifact.document)
    document["schema"] = schema
    with pytest.raises(fixture[8].ConfigError):
        fixture[8].validate_plan(document, allow_unsigned=True)


def test_retained_v3_cannot_claim_a_different_observed_head(
    promotion_modules, repo_root, tmp_path, monkeypatch
):
    fixture, artifact = _retained_v3(promotion_modules, repo_root, tmp_path, monkeypatch)
    document = deepcopy(artifact.document)
    document["local_sources"][0]["source"]["head_revision"] = "f" * 40
    with pytest.raises(fixture[8].ConfigError, match="exact immutable commit"):
        fixture[8].validate_plan(document, allow_unsigned=True)


@pytest.mark.parametrize("defect", ["release-ref", "store-traversal", "duplicate", "mixed-binding-schema"])
def test_v4_loader_rejects_malformed_source_realization(
    promotion_modules, repo_root, tmp_path, monkeypatch, defect
):
    fixture, artifact = _FIXTURE.prepare_local_artifact(
        promotion_modules, repo_root, tmp_path, monkeypatch, "modern"
    )
    document = deepcopy(artifact.document)
    record = document["local_sources"][0]
    if defect == "release-ref":
        record["source"].update(release_ref="refs/heads/other", head_ref="refs/heads/other")
    elif defect == "store-traversal":
        record["realization"]["store_path"] = "/nix/store/../tmp"
    elif defect == "duplicate":
        document["local_sources"].append(deepcopy(record))
    else:
        record["schema"] = "nix-update-promotion-local-source-binding-v1"
    with pytest.raises(fixture[8].ConfigError):
        fixture[8].validate_plan(document, allow_unsigned=True)
