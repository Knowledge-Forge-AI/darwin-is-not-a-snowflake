# nix-darwin-test-source-target: libexec/nix_darwin_local/deployment_workflow.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/repository_identity.py
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local import deployment_workflow as workflow_module
from nix_darwin_local.deployment_plan import CONTRACT_SCHEMA
from nix_darwin_local.paths import ConfigError
from nix_darwin_local.repository_identity import (
    local_git_identity,
    publication_git_identity,
)


def init_disposable_repo(repo_path: Path) -> Path:
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=repo_path,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(["git", "config", "user.name", "Test Committer"], cwd=repo_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo_path,
        check=True,
    )
    (repo_path / "flake.nix").write_text('{ description = "disposable"; }\n')
    (repo_path / "flake.lock").write_text(
        json.dumps(
            {
                "nodes": {
                    "root": {"inputs": {"source": "source"}},
                    "source": {
                        "flake": False,
                        "locked": {
                            "type": "path",
                            "path": "/fixture/source",
                            "narHash": "sha256-source=",
                        },
                        "original": {"type": "path", "path": "/fixture/source"},
                    },
                },
                "root": "root",
                "version": 7,
            },
            sort_keys=True,
        )
        + "\n"
    )
    subprocess.run(["git", "add", "flake.nix", "flake.lock"], cwd=repo_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=repo_path,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    remotes = subprocess.run(
        ["git", "remote"], cwd=repo_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert remotes == ""
    return repo_path


class RemoteFreeRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.target = "/nix/store/00000000000000000000000000000000-fixture-system"

    def __call__(self, argv, cwd=None, **kwargs):
        command = [str(item) for item in argv]
        self.commands.append(command)
        if command[0] == "git":
            return subprocess.run(command, cwd=cwd, **kwargs)
        if command[:2] == ["nix", "eval"]:
            return subprocess.CompletedProcess(command, 0, "{}\n", "")
        if command[:3] == ["nix", "flake", "check"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["nix", "flake", "update"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:2] == ["nix", "build"]:
            return subprocess.CompletedProcess(command, 0, self.target + "\n", "")
        if command[:2] == ["nix", "path-info"]:
            closure = [{"path": command[-1], "narHash": "sha256-closure=", "narSize": 1}]
            return subprocess.CompletedProcess(command, 0, json.dumps(closure) + "\n", "")
        raise AssertionError(f"unexpected command: {command}")


def make_workflow(tmp_path: Path, monkeypatch) -> tuple[workflow_module.Workflow, RemoteFreeRunner]:
    state_root = tmp_path / "workflow-state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    runner = RemoteFreeRunner()
    return workflow_module.Workflow(CORE_ROOT, runner), runner


def assert_no_remote_authority_commands(commands: list[list[str]]) -> None:
    for command in commands:
        assert command[:3] != ["git", "remote", "get-url"]
        assert command[:2] != ["git", "ls-remote"]
        assert "@{upstream}" not in command
        assert "HEAD...@{upstream}" not in command


def test_remote_free_repository_local_and_publication_authority(tmp_path: Path, monkeypatch):
    repo = init_disposable_repo(tmp_path / "remote-free-repo")
    workflow, runner = make_workflow(tmp_path, monkeypatch)

    identity = local_git_identity(workflow, repo)
    assert identity["branch"] == "main"
    assert identity["status"] == ""
    assert identity["operation_state"] == "clean"
    assert identity["ignored_status_count"] == "0"
    assert len(identity["commit"]) == 40
    assert len(identity["tree"]) == 40
    assert len(identity["index_sha256"]) == 64

    with pytest.raises(ConfigError, match="unable to obtain configured Git upstream authority"):
        publication_git_identity(workflow, repo, identity)
    assert_no_remote_authority_commands(runner.commands)


def test_remote_free_repository_ignored_files_do_not_block_local_identity(tmp_path: Path, monkeypatch):
    repo = init_disposable_repo(tmp_path / "repo-with-ignored")
    (repo / ".gitignore").write_text(".serena/\n*.ignored\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add gitignore"], cwd=repo, check=True, capture_output=True)
    (repo / ".serena").mkdir()
    (repo / ".serena/project.yml").write_text("name: test\n")
    (repo / "temp.ignored").write_text("data\n")
    workflow, runner = make_workflow(tmp_path, monkeypatch)

    identity = local_git_identity(workflow, repo)
    assert identity["status"] == ""
    assert int(identity["ignored_status_count"]) >= 2
    assert len(identity["ignored_status_sha256"]) == 64
    assert_no_remote_authority_commands(runner.commands)


@pytest.mark.parametrize(
    ("mode", "inputs", "expected_command"),
    [
        ("locked", [], ["nix", "flake", "check"]),
        ("targeted", ["source"], ["nix", "flake", "update", "source"]),
    ],
)
def test_remote_free_lock_reaches_isolated_candidate_command(
    tmp_path: Path, monkeypatch, mode: str, inputs: list[str], expected_command: list[str]
):
    repo = init_disposable_repo(tmp_path / f"remote-free-{mode}")
    workflow, runner = make_workflow(tmp_path, monkeypatch)

    status = workflow_module.maintain_lock(
        workflow,
        repo,
        mode,
        inputs,
        [],
        tmp_path / f"{mode}-evidence",
        False,
    )

    assert status == 0
    assert any(command[: len(expected_command)] == expected_command for command in runner.commands)
    assert_no_remote_authority_commands(runner.commands)


def test_remote_free_build_prepare_and_apply_preflight_revalidate_local_identity(
    tmp_path: Path, monkeypatch
):
    repo = init_disposable_repo(tmp_path / "remote-free-deployment")
    control = tmp_path / "control"
    control.mkdir(mode=0o700)
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "schema": CONTRACT_SCHEMA,
                "profile_policy": {
                    "profile": "/fixture/profile",
                    "live": "/fixture/live",
                    "generation_glob": "/fixture/system-*-link",
                },
            }
        )
        + "\n"
    )
    workflow, runner = make_workflow(tmp_path, monkeypatch)
    profile = {
        "relationship": "aligned",
        "selected": {"path": "/fixture/profile", "target": runner.target},
        "live": {"path": "/fixture/live", "target": runner.target},
        "retained": [],
    }
    tool_identity = {"name": "nix-update", "version": "test", "digest": "0" * 64}
    monkeypatch.setattr(workflow_module, "_guard_processes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_profile_snapshot", lambda _contract: profile)
    monkeypatch.setattr(workflow_module, "_tool_identity", lambda _core: tool_identity)

    pre_build_git_commits = sum(record.label == "Git commit" for record in workflow.records)
    assert workflow_module.build_only(workflow, repo, "FixtureHost", control, []) == 0
    assert sum(record.label == "Git commit" for record in workflow.records) >= pre_build_git_commits + 2

    def retain_plan(output, document, _markdown):
        output.mkdir(mode=0o700)
        final = dict(document)
        final["plan_digest"] = "d" * 64
        return SimpleNamespace(directory=output, document=final, digest="d" * 64)

    monkeypatch.setattr(workflow_module, "write_plan", retain_plan)
    artifact = workflow_module.prepare(
        workflow,
        repo,
        "FixtureHost",
        control,
        contract_path,
        tmp_path / "plan",
        [],
    )
    assert artifact.document["status"] == "READY_FOR_REVIEW"
    assert artifact.document["selected_flake"]["repository"] == local_git_identity(workflow, repo)
    assert any(record.label == "selected-flake report qualification" for record in workflow.records)
    assert any(record.label == "exact Darwin target build" for record in workflow.records)

    pre_apply_git_commits = sum(record.label == "Git commit" for record in workflow.records)
    monkeypatch.setattr(workflow_module, "load_plan", lambda _path: artifact)
    assert workflow_module.apply(workflow, artifact.directory, "DECLINE") == 1
    assert sum(record.label == "Git commit" for record in workflow.records) > pre_apply_git_commits
    result = json.loads(next((artifact.directory / "results").rglob("result.json")).read_text())
    assert "typed confirmation" in result["failure"]
    assert not any(command and command[0] == "sudo" for command in runner.commands)
    assert_no_remote_authority_commands(runner.commands)
