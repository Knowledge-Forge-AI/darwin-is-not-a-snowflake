# nix-darwin-test-source-target: libexec/nix_darwin_local/local_deployment_plan.py
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local.local_deployment_plan import (
    SCHEMA,
    LocalPlanArtifact,
    validate_local_plan_document,
    write_local_plan,
    load_local_plan,
)
from nix_darwin_local.paths import ConfigError


def sample_doc():
    return {
        "schema": SCHEMA,
        "selected_flake": "/path/to/flake",
        "host": "test-host",
        "target_system": "/nix/store/test-system",
        "predecessor_generation": 42,
        "git_authority": {"commit": "0" * 40, "clean": True},
    }


def test_validate_local_plan_document_valid():
    validate_local_plan_document(sample_doc())


def test_validate_local_plan_document_invalid_schema():
    doc = sample_doc()
    doc["schema"] = "wrong-schema"
    with pytest.raises(ConfigError, match="invalid local plan schema"):
        validate_local_plan_document(doc)


@pytest.mark.parametrize("missing_key", ["selected_flake", "host", "target_system", "predecessor_generation", "git_authority"])
def test_validate_local_plan_document_missing_key(missing_key):
    doc = sample_doc()
    del doc[missing_key]
    with pytest.raises(ConfigError, match=f"missing required key in local plan: {missing_key}"):
        validate_local_plan_document(doc)


def test_validate_local_plan_document_rejects_secrets():
    doc = sample_doc()
    doc["leaked_key"] = "-----BEGIN OPENSSH PRIVATE KEY-----"
    with pytest.raises(ConfigError):
        validate_local_plan_document(doc)


def test_write_and_load_local_plan(tmp_path: Path):
    plan_dir = tmp_path / "plan_dir"
    plan_dir.mkdir(mode=0o700)
    doc = sample_doc()
    md = "# Deployment Plan\nReady for switch."

    artifact = write_local_plan(plan_dir, doc, md)
    assert artifact.directory == plan_dir
    assert artifact.document == doc
    assert artifact.markdown == md
    assert len(artifact.digest) == 64

    # Load from directory
    loaded_dir = load_local_plan(plan_dir)
    assert loaded_dir.digest == artifact.digest
    assert loaded_dir.document == doc
    assert loaded_dir.markdown == md

    # Load from file path
    loaded_file = load_local_plan(plan_dir / "plan.json")
    assert loaded_file.digest == artifact.digest


def test_write_local_plan_rejects_secrets_in_markdown(tmp_path: Path):
    plan_dir = tmp_path / "secret_md"
    plan_dir.mkdir(mode=0o700)
    doc = sample_doc()
    md = "Secret: -----BEGIN RSA PRIVATE KEY-----"
    with pytest.raises(ConfigError):
        write_local_plan(plan_dir, doc, md)


def test_load_local_plan_invalid_json(tmp_path: Path):
    plan_dir = tmp_path / "broken_plan"
    plan_dir.mkdir(mode=0o700)
    plan_file = plan_dir / "plan.json"
    plan_file.write_text("not json", encoding="utf-8")
    plan_file.chmod(0o600)

    with pytest.raises(ConfigError, match="unable to decode local plan"):
        load_local_plan(plan_dir)
