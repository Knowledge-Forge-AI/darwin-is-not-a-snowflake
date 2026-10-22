# nix-darwin-test-source-target: libexec/nix_darwin_local/repository_identity.py
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local.paths import ConfigError
from nix_darwin_local.repository_identity import publication_git_identity


class MockWorkflow:
    def __init__(self, runner):
        self.records = []
        self._runner = runner

    def command(self, label, argv, cwd):
        result = self._runner(argv, cwd=cwd)
        self.records.append((label, argv, result))
        return result

    def complete_stdout(self, label):
        for record_label, _argv, result in reversed(self.records):
            if record_label == label:
                return result.stdout
        raise KeyError(f"no record for label {label}")


def make_publication_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "publication repo"
    remote = tmp_path / "publication remote.git"
    repo.mkdir(parents=True)
    (repo / "flake.nix").write_text('{ description = "test"; }\n')
    (repo / "flake.lock").write_text('{ "version": 7, "nodes": {} }\n')
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=repo, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    subprocess.run(["git", "config", "user.name", "Test Committer"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True,
    )
    subprocess.run(["git", "add", "flake.nix", "flake.lock"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=repo, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo, check=True)
    subprocess.run(
        ["git", "push", "-u", "origin", "main"], cwd=repo, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    head = git(repo, "rev-parse", "HEAD")
    return repo, remote, head


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    ).stdout.strip()


def workflow_for_real_git() -> MockWorkflow:
    return MockWorkflow(
        lambda argv, cwd=None: subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True
        )
    )


def test_actual_remote_wins_over_stale_tracking_cache(tmp_path: Path):
    repo, _remote, initial = make_publication_repo(tmp_path)
    (repo / "second").write_text("second\n")
    git(repo, "add", "second")
    git(repo, "commit", "-m", "second")
    git(repo, "push", "origin", "main")
    current = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/main", initial)

    workflow = workflow_for_real_git()
    identity = publication_git_identity(
        workflow, repo, {"branch": "main", "commit": current}
    )

    assert identity["remote_head"] == current
    assert identity["upstream"] == "origin/main"
    assert identity["parity"] in {"1\t0", "1 0"}
    labels = [label for label, _command, _result in workflow.records]
    assert labels.index("Git canonical remote HEAD") < labels.index("Git upstream")


def test_actual_remote_wins_when_tracking_cache_is_missing(tmp_path: Path):
    repo, _remote, head = make_publication_repo(tmp_path)
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")

    workflow = workflow_for_real_git()
    identity = publication_git_identity(
        workflow, repo, {"branch": "main", "commit": head}
    )

    assert identity["remote_head"] == head
    assert identity["upstream"] is None
    assert identity["parity"] is None
    labels = [label for label, _command, _result in workflow.records]
    assert labels.index("Git canonical remote HEAD") < labels.index("Git upstream")


def test_actual_remote_divergence_is_rejected_even_with_matching_cache(tmp_path: Path):
    repo, remote, head = make_publication_repo(tmp_path)
    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", "--branch", "main", str(remote), str(other)], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    subprocess.run(["git", "config", "user.name", "Other Committer"], cwd=other, check=True)
    subprocess.run(
        ["git", "config", "user.email", "other@example.invalid"], cwd=other, check=True,
    )
    (other / "remote-only").write_text("remote-only\n")
    subprocess.run(["git", "add", "remote-only"], cwd=other, check=True)
    subprocess.run(
        ["git", "commit", "-m", "remote-only"], cwd=other, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["git", "push", "origin", "main"], cwd=other, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    git(repo, "update-ref", "refs/remotes/origin/main", head)

    with pytest.raises(ConfigError, match="differs from canonical remote branch"):
        publication_git_identity(
            workflow_for_real_git(), repo, {"branch": "main", "commit": head}
        )
