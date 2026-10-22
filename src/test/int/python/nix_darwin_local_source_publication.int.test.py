# nix-darwin-test-source-target: libexec/nix_darwin_local/source_publication.py
"""Disposable Git integration coverage for explicit source publication."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local.deployment_workflow import Workflow
from nix_darwin_local.paths import ConfigError
from nix_darwin_local.source_catalog import Publication, SourceCatalogEntry
from nix_darwin_local import source_publication
from nix_darwin_local.source_publication import publish_sources


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="required git executable is unavailable"
)


@pytest.fixture(autouse=True)
def isolated_publication_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(source_publication, "_private_state_root", lambda: state_root)
    for name in (
        "NIX_CONFIG",
        "NIX_PATH",
        "NIX_USER_CONF_FILES",
        "NIX_CONF_DIR",
        "NIX_REMOTE",
    ):
        monkeypatch.delenv(name, raising=False)
    return state_root


def git(cwd: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check:
        assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def make_repo(tmp_path: Path, name: str) -> tuple[Path, Path]:
    checkout = tmp_path / name
    remote = tmp_path / f"{name}.git"
    checkout.mkdir()
    git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    git(checkout, "init", "--initial-branch=main")
    git(checkout, "config", "user.name", "Publication Fixture")
    git(checkout, "config", "user.email", "fixture@example.invalid")
    git(checkout, "remote", "add", "origin", remote.as_uri())
    (checkout / "flake.nix").write_text(
        "{ outputs = { self }: { value = 1; }; }\n", encoding="utf-8"
    )
    return checkout, remote


def commit(checkout: Path, message: str) -> str:
    git(checkout, "add", "flake.nix", "flake.lock")
    git(checkout, "commit", "-m", message)
    return git(checkout, "rev-parse", "HEAD")


def write_lock(checkout: Path, dependencies: dict[str, tuple[str, str]]) -> None:
    nodes: dict[str, object] = {"root": {"inputs": {}}}
    for key, (url, revision) in dependencies.items():
        nodes["root"]["inputs"][key] = key  # type: ignore[index]
        nodes[key] = {
            "locked": {
                "type": "git",
                "url": url,
                "ref": "main",
                "rev": revision,
            },
            "original": {
                "type": "git",
                "url": url,
                "ref": "main",
            },
        }
    (checkout / "flake.lock").write_text(
        json.dumps({"version": 7, "root": "root", "nodes": nodes}, indent=2) + "\n",
        encoding="utf-8",
    )


def entry(key: str, checkout: Path, remote: Path) -> SourceCatalogEntry:
    return SourceCatalogEntry(
        key=key,
        checkout=checkout,
        repository=remote.as_uri(),
        release_ref="refs/heads/main",
        publication=Publication(remote="origin", ref="refs/heads/main"),
    )


def remote_head(remote: Path) -> str:
    return git(remote, "rev-parse", "refs/heads/main")


def test_provider_free_source_lower_upper_publication_is_dependency_ordered(tmp_path: Path):
    source, source_remote = make_repo(tmp_path, "source")
    (source / "flake.lock").write_text(
        json.dumps({"version": 7, "root": "root", "nodes": {"root": {"inputs": {}}}})
        + "\n",
        encoding="utf-8",
    )
    source_revision = commit(source, "source")

    lower, lower_remote = make_repo(tmp_path, "composition")
    write_lock(lower, {"source": (source_remote.as_uri(), source_revision)})
    lower_revision = commit(lower, "lower composition")

    upper, upper_remote = make_repo(tmp_path, "composition-private")
    write_lock(
        upper,
        {
            "source": (source_remote.as_uri(), source_revision),
            "composition": (lower_remote.as_uri(), lower_revision),
        },
    )
    upper_revision = commit(upper, "upper composition")

    sources = (
        entry("source", source, source_remote),
        entry("composition", lower, lower_remote),
        entry("composition-private", upper, upper_remote),
    )
    workflow = Workflow(upper, display_command_output=False)
    result = publish_sources(
        workflow,
        sources,
        [("composition-private", upper_revision)],
        dry_run=False,
        allow_local_fixture=True,
    )

    assert result["schema"] == "dinas-source-publication-v1"
    assert result["status"] == "PUBLISHED"
    assert result["state"] == "published"
    assert result["no_activation"] is True
    assert [step["key"] for step in result["steps"]] == [
        "source",
        "composition",
        "composition-private",
    ]
    assert remote_head(source_remote) == source_revision
    assert remote_head(lower_remote) == lower_revision
    assert remote_head(upper_remote) == upper_revision
    assert not any(record.argv and record.argv[0] == "nix" for record in workflow.records)


def test_rejected_publication_preserves_local_commit_and_retries_without_rewrite(
    tmp_path: Path,
):
    checkout, remote = make_repo(tmp_path, "rejected")
    (checkout / "flake.lock").write_text(
        json.dumps({"version": 7, "root": "root", "nodes": {"root": {"inputs": {}}}})
        + "\n",
        encoding="utf-8",
    )
    revision = commit(checkout, "local promotion")
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o700)
    source = entry("source", checkout, remote)

    first = publish_sources(
        Workflow(checkout, display_command_output=False),
        (source,),
        [("source", revision)],
        dry_run=False,
        allow_local_fixture=True,
    )
    assert first["status"] == "PENDING"
    assert first["state"] == "pending"
    assert first["steps"][0]["state"] == "pending"
    assert git(checkout, "rev-parse", "HEAD") == revision
    assert git(remote, "show-ref", "refs/heads/main", check=False) == ""

    hook.unlink()
    second = publish_sources(
        Workflow(checkout, display_command_output=False),
        (source,),
        [("source", revision)],
        dry_run=False,
        allow_local_fixture=True,
    )
    assert second["status"] == "PUBLISHED"
    assert git(checkout, "rev-parse", "HEAD") == revision
    assert remote_head(remote) == revision


def test_missing_publication_metadata_reports_unavailable_without_mutation(tmp_path: Path):
    checkout, remote = make_repo(tmp_path, "local-only")
    (checkout / "flake.lock").write_text(
        json.dumps({"version": 7, "root": "root", "nodes": {"root": {"inputs": {}}}})
        + "\n",
        encoding="utf-8",
    )
    revision = commit(checkout, "local source")
    source = SourceCatalogEntry(
        key="local-only",
        checkout=checkout,
        repository=remote.as_uri(),
        release_ref="refs/heads/main",
    )
    workflow = Workflow(checkout, display_command_output=False)
    result = publish_sources(
        workflow,
        (source,),
        [("local-only", revision)],
        dry_run=False,
        allow_local_fixture=True,
    )
    assert result["status"] == "UNAVAILABLE"
    assert result["state"] == "unavailable"
    assert result["steps"][0]["state"] == "unavailable"
    assert result["steps"][0]["intended"] is None
    assert not any("ls-remote" in record.argv for record in workflow.records)
    assert git(remote, "show-ref", "refs/heads/main", check=False) == ""


def test_dry_run_renders_intended_ref_without_remote_truth_or_push(tmp_path: Path):
    checkout, remote = make_repo(tmp_path, "dry-run")
    (checkout / "flake.lock").write_text(
        json.dumps({"version": 7, "root": "root", "nodes": {"root": {"inputs": {}}}})
        + "\n",
        encoding="utf-8",
    )
    revision = commit(checkout, "dry-run source")
    source = entry("source", checkout, remote)
    workflow = Workflow(checkout, display_command_output=False)
    result = publish_sources(
        workflow,
        (source,),
        [("source", revision)],
        dry_run=True,
        allow_local_fixture=True,
    )
    assert result["dry_run"] is True
    assert result["status"] == "DRY_RUN"
    assert result["state"] == "pending"
    assert result["steps"][0]["state"] == "pending"
    assert result["steps"][0]["intended"] == {
        "remote": "origin",
        "ref": "refs/heads/main",
    }
    assert not any("ls-remote" in record.argv for record in workflow.records)
    assert not any("push" in record.argv for record in workflow.records)
    assert git(remote, "show-ref", "refs/heads/main", check=False) == ""


def test_started_journal_reconciles_before_an_explicit_retry(
    tmp_path: Path, isolated_publication_state: Path
):
    checkout, remote = make_repo(tmp_path, "interrupted")
    (checkout / "flake.lock").write_text(
        json.dumps({"version": 7, "root": "root", "nodes": {"root": {"inputs": {}}}})
        + "\n",
        encoding="utf-8",
    )
    revision = commit(checkout, "interrupted source")
    source = entry("source", checkout, remote)

    def crash_on_push(argv, **kwargs):
        if "push" in argv:
            raise KeyboardInterrupt
        return subprocess.run(argv, **kwargs)

    with pytest.raises(KeyboardInterrupt):
        publish_sources(
            Workflow(checkout, runner=crash_on_push, display_command_output=False),
            (source,),
            [("source", revision)],
            dry_run=False,
            allow_local_fixture=True,
        )

    retry_workflow = Workflow(checkout, display_command_output=False)
    retry = publish_sources(
        retry_workflow,
        (source,),
        [("source", revision)],
        dry_run=False,
        allow_local_fixture=True,
    )
    assert retry["status"] == "PENDING"
    assert retry["state"] == "pending"
    assert "prior publication attempt" in retry["steps"][0]["error"]
    assert not any("push" in record.argv for record in retry_workflow.records)
    journals = list((isolated_publication_state / "source-publications").glob("*/journal.json"))
    assert len(journals) == 2
    states = {json.loads(path.read_text(encoding="utf-8"))["state"] for path in journals}
    assert "PENDING" in states


def test_started_journal_authority_mismatch_fails_closed_without_remote_probe(
    tmp_path: Path, isolated_publication_state: Path
):
    checkout, remote = make_repo(tmp_path, "authority-drift")
    (checkout / "flake.lock").write_text(
        json.dumps({"version": 7, "root": "root", "nodes": {"root": {"inputs": {}}}})
        + "\n",
        encoding="utf-8",
    )
    revision = commit(checkout, "authority drift source")
    source = entry("source", checkout, remote)

    def crash_on_push(argv, **kwargs):
        if "push" in argv:
            raise KeyboardInterrupt
        return subprocess.run(argv, **kwargs)

    with pytest.raises(KeyboardInterrupt):
        publish_sources(
            Workflow(checkout, runner=crash_on_push, display_command_output=False),
            (source,),
            [("source", revision)],
            dry_run=False,
            allow_local_fixture=True,
        )

    journal = next(
        (isolated_publication_state / "source-publications").glob("*/journal.json")
    )
    original = journal.read_bytes()
    document = json.loads(original.decode("utf-8"))
    document["steps"][0]["authority_digest"] = "0" * 64
    journal.write_text(json.dumps(document), encoding="utf-8")
    journal.chmod(0o600)
    tampered = journal.read_bytes()

    retry_workflow = Workflow(checkout, display_command_output=False)
    with pytest.raises(ConfigError, match="authority differs"):
        publish_sources(
            retry_workflow,
            (source,),
            [("source", revision)],
            dry_run=False,
            allow_local_fixture=True,
        )

    assert tampered != original
    assert journal.read_bytes() == tampered
    assert not any("ls-remote" in record.argv for record in retry_workflow.records)
    assert not any("push" in record.argv for record in retry_workflow.records)
    assert git(remote, "show-ref", "refs/heads/main", check=False) == ""
    assert len(list((isolated_publication_state / "source-publications").glob("*/journal.json"))) == 1


def test_credentialed_publication_url_is_rejected_before_evidence_or_push(
    tmp_path: Path,
):
    checkout, remote = make_repo(tmp_path, "credentialed")
    (checkout / "flake.lock").write_text(
        json.dumps({"version": 7, "root": "root", "nodes": {"root": {"inputs": {}}}})
        + "\n",
        encoding="utf-8",
    )
    revision = commit(checkout, "credentialed source")
    secret = "publication-secret"
    git(
        checkout,
        "remote",
        "set-url",
        "origin",
        f"https://operator:{secret}@example.invalid/source.git",
    )
    workflow = Workflow(checkout, display_command_output=False)
    with pytest.raises(ConfigError, match="credential|password") as failure:
        publish_sources(
            workflow,
            (entry("source", checkout, remote),),
            [("source", revision)],
            dry_run=False,
            allow_local_fixture=True,
        )
    assert secret not in str(failure.value)
    assert all(secret not in record.argv for record in workflow.records)
    assert git(remote, "show-ref", "refs/heads/main", check=False) == ""


def test_publication_remote_drift_is_rejected_before_push(tmp_path: Path):
    checkout, remote = make_repo(tmp_path, "drifted")
    alternate = tmp_path / "alternate.git"
    git(tmp_path, "init", "--bare", "--initial-branch=main", str(alternate))
    (checkout / "flake.lock").write_text(
        json.dumps({"version": 7, "root": "root", "nodes": {"root": {"inputs": {}}}})
        + "\n",
        encoding="utf-8",
    )
    revision = commit(checkout, "drifted source")
    remote_url_reads = 0

    def runner(argv, **kwargs):
        nonlocal remote_url_reads
        result = subprocess.run(argv, **kwargs)
        if "get-url" in argv and "remote" in argv:
            remote_url_reads += 1
            if remote_url_reads == 2:
                subprocess.run(
                    ["git", "remote", "set-url", "origin", alternate.as_uri()],
                    cwd=checkout,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
        return result

    workflow = Workflow(checkout, runner=runner, display_command_output=False)
    with pytest.raises(ConfigError, match="publication.*(fetch|push).*disagrees"):
        publish_sources(
            workflow,
            (entry("source", checkout, remote),),
            [("source", revision)],
            dry_run=False,
            allow_local_fixture=True,
        )
    assert remote_url_reads >= 4
    assert not any("push" in record.argv for record in workflow.records)
    assert git(remote, "show-ref", "refs/heads/main", check=False) == ""
