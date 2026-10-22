# nix-darwin-test-source-target: libexec/nix_darwin_local/repository_identity.py
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local.deployment_plan import sha256_bytes
from nix_darwin_local.paths import ConfigError
from nix_darwin_local.repository_identity import (
    GIT_OPERATION_PATHS,
    configured_publication_identity,
    git_status_partition,
    local_git_identity,
    publication_git_identity,
)


class MockWorkflow:
    def __init__(self, runner=None, complete_outputs=None):
        self.records = []
        self._runner = runner or self._default_runner
        self._complete_outputs = complete_outputs or {}

    def _default_runner(self, argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, "", "")

    def command(self, label, argv, cwd):
        result = self._runner(argv, cwd=cwd)
        self.records.append((label, argv, result))
        return result

    def complete_stdout(self, label):
        if label in self._complete_outputs:
            return self._complete_outputs[label]
        for rec_label, _argv, result in reversed(self.records):
            if rec_label == label:
                return result.stdout
        raise KeyError(f"no record for label {label}")


def make_clean_flake_repo(repo_dir: Path) -> tuple[Path, Path]:
    repo_dir.mkdir(parents=True, exist_ok=True)
    flake_nix = repo_dir / "flake.nix"
    flake_lock = repo_dir / "flake.lock"
    flake_nix.write_text("{ description = \"test\"; }\n")
    flake_lock.write_text("{ \"version\": 7, \"nodes\": {} }\n")
    flake_nix.chmod(0o644)
    flake_lock.chmod(0o644)
    return flake_nix, flake_lock


def test_local_git_identity_returns_exact_eleven_keys_and_runs_no_remote_commands(tmp_path: Path):
    flake_nix, flake_lock = make_clean_flake_repo(tmp_path)
    executed_commands: list[list[str]] = []

    def runner(argv, **_kwargs):
        executed_commands.append(list(argv))
        cmd = argv[:3]
        if argv[:2] == ["git", "rev-parse"] and argv[-1] == "--show-toplevel":
            return subprocess.CompletedProcess(argv, 0, str(tmp_path) + "\n", "")
        if argv[:2] == ["git", "rev-parse"] and argv[-1] == "HEAD":
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\n", "")
        if argv[:2] == ["git", "rev-parse"] and argv[-1] == "HEAD^{tree}":
            return subprocess.CompletedProcess(argv, 0, "b" * 40 + "\n", "")
        if argv[:2] == ["git", "branch"] and argv[-1] == "--show-current":
            return subprocess.CompletedProcess(argv, 0, "main\n", "")
        if argv[:2] == ["git", "ls-files"]:
            return subprocess.CompletedProcess(argv, 0, "100644 " + "1" * 40 + " 0\tflake.nix\n", "")
        if argv[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["git", "rev-parse", "--git-path"]:
            return subprocess.CompletedProcess(argv, 0, str(tmp_path / ".git" / argv[-1]) + "\n", "")
        raise AssertionError(f"unexpected command: {argv}")

    wf = MockWorkflow(runner)
    identity = local_git_identity(wf, tmp_path)

    expected_keys = {
        "repository_root",
        "commit",
        "tree",
        "branch",
        "status",
        "operation_state",
        "index_sha256",
        "flake_nix",
        "flake_lock",
        "ignored_status_sha256",
        "ignored_status_count",
    }
    assert set(identity.keys()) == expected_keys
    assert identity["commit"] == "a" * 40
    assert identity["tree"] == "b" * 40
    assert identity["branch"] == "main"
    assert identity["status"] == ""
    assert identity["operation_state"] == "clean"
    assert identity["ignored_status_count"] == "0"
    assert identity["ignored_status_sha256"] == sha256_bytes(b"")

    # Assert no remote commands were executed
    for cmd in executed_commands:
        assert cmd[:3] != ["git", "remote", "get-url"]
        assert cmd[:2] != ["git", "ls-remote"]
        assert cmd[:3] != ["git", "rev-parse", "--abbrev-ref"]
        assert cmd[:2] != ["git", "rev-list"]


def test_local_git_identity_accepts_detached_head(tmp_path: Path):
    make_clean_flake_repo(tmp_path)

    for bad_branch in ("", "HEAD"):
        def runner(argv, **_kwargs):
            if argv[:2] == ["git", "rev-parse"] and argv[-1] == "--show-toplevel":
                return subprocess.CompletedProcess(argv, 0, str(tmp_path) + "\n", "")
            if argv[:2] == ["git", "rev-parse"]:
                return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\n", "")
            if argv[:2] == ["git", "branch"]:
                return subprocess.CompletedProcess(argv, 0, f"{bad_branch}\n", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        wf = MockWorkflow(runner)
        identity = local_git_identity(wf, tmp_path, False)
        assert identity["branch"] == "HEAD"


def test_local_git_identity_accepts_dirty_worktree(tmp_path: Path):
    make_clean_flake_repo(tmp_path)

    def runner(argv, **_kwargs):
        if argv[:2] == ["git", "rev-parse"] and argv[-1] == "--show-toplevel":
            return subprocess.CompletedProcess(argv, 0, str(tmp_path) + "\n", "")
        if argv[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\n", "")
        if argv[:2] == ["git", "branch"]:
            return subprocess.CompletedProcess(argv, 0, "main\n", "")
        if argv[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(argv, 0, " M flake.nix\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    wf = MockWorkflow(runner)
    identity = local_git_identity(wf, tmp_path, False)
    assert identity["status"] == "M flake.nix"


def test_local_git_identity_tolerates_and_digests_ignored_status(tmp_path: Path):
    make_clean_flake_repo(tmp_path)

    def runner(argv, **_kwargs):
        if argv[:2] == ["git", "rev-parse"] and argv[-1] == "--show-toplevel":
            return subprocess.CompletedProcess(argv, 0, str(tmp_path) + "\n", "")
        if argv[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\n", "")
        if argv[:2] == ["git", "branch"]:
            return subprocess.CompletedProcess(argv, 0, "main\n", "")
        if argv[:2] == ["git", "ls-files"]:
            return subprocess.CompletedProcess(argv, 0, "stage\n", "")
        if argv[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(argv, 0, "!! .serena/\n!! result/\n", "")
        if argv[:3] == ["git", "rev-parse", "--git-path"]:
            return subprocess.CompletedProcess(argv, 0, str(tmp_path / ".git" / argv[-1]) + "\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    wf = MockWorkflow(runner)
    identity = local_git_identity(wf, tmp_path)
    assert identity["status"] == ""
    assert identity["ignored_status_count"] == "2"
    assert identity["ignored_status_sha256"] == sha256_bytes(b"!! .serena/\n!! result/")


@pytest.mark.parametrize("operation", GIT_OPERATION_PATHS)
def test_local_git_identity_rejects_active_operation(tmp_path: Path, operation: str):
    make_clean_flake_repo(tmp_path)
    op_path = tmp_path / ".git" / operation
    op_path.parent.mkdir(parents=True, exist_ok=True)
    if "rebase" in operation or operation == "sequencer":
        op_path.mkdir(parents=True, exist_ok=True)
    else:
        op_path.write_text("marker\n")

    def runner(argv, **_kwargs):
        if argv[:2] == ["git", "rev-parse"] and argv[-1] == "--show-toplevel":
            return subprocess.CompletedProcess(argv, 0, str(tmp_path) + "\n", "")
        if argv[:2] == ["git", "rev-parse"] and argv[-1] == "HEAD":
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\n", "")
        if argv[:2] == ["git", "rev-parse"] and argv[-1] == "HEAD^{tree}":
            return subprocess.CompletedProcess(argv, 0, "b" * 40 + "\n", "")
        if argv[:2] == ["git", "branch"]:
            return subprocess.CompletedProcess(argv, 0, "main\n", "")
        if argv[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["git", "ls-files"]:
            return subprocess.CompletedProcess(argv, 0, "stage\n", "")
        if argv[:3] == ["git", "rev-parse", "--git-path"]:
            return subprocess.CompletedProcess(argv, 0, str(tmp_path / ".git" / "missing") + "\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    wf = MockWorkflow(
        runner,
        {f"Git operation state ({operation})": str(op_path) + "\n"},
    )
    with pytest.raises(ConfigError, match=f"active Git operation state: {operation}"):
        local_git_identity(wf, tmp_path)


def test_local_git_identity_flake_files_must_be_regular_and_single_link(tmp_path: Path):
    flake_nix, flake_lock = make_clean_flake_repo(tmp_path)
    link_path = tmp_path / "hardlink"
    os.link(flake_nix, link_path)

    def runner(argv, **_kwargs):
        if argv[:2] == ["git", "rev-parse"] and argv[-1] == "--show-toplevel":
            return subprocess.CompletedProcess(argv, 0, str(tmp_path) + "\n", "")
        if argv[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\n", "")
        if argv[:2] == ["git", "branch"]:
            return subprocess.CompletedProcess(argv, 0, "main\n", "")
        if argv[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["git", "ls-files"]:
            return subprocess.CompletedProcess(argv, 0, "stage\n", "")
        if argv[:3] == ["git", "rev-parse", "--git-path"]:
            return subprocess.CompletedProcess(argv, 0, str(tmp_path / ".git" / argv[-1]) + "\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    wf = MockWorkflow(runner)
    with pytest.raises(ConfigError, match="single-link regular"):
        local_git_identity(wf, tmp_path)


def test_publication_git_identity_returns_six_keys(tmp_path: Path):
    local = {
        "branch": "main",
        "commit": "a" * 40,
    }

    def runner(argv, **_kwargs):
        if argv[:3] == ["git", "config", "--get"] and argv[-1].endswith(".remote"):
            return subprocess.CompletedProcess(argv, 0, "origin\n", "")
        if argv[:3] == ["git", "config", "--get"] and argv[-1].endswith(".merge"):
            return subprocess.CompletedProcess(argv, 0, "refs/heads/main\n", "")
        if argv[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return subprocess.CompletedProcess(argv, 0, "origin/main\n", "")
        if argv[:3] == ["git", "remote", "get-url"]:
            return subprocess.CompletedProcess(argv, 0, "git@example.invalid:owner/repo.git\n", "")
        if argv[:2] == ["git", "rev-list"]:
            return subprocess.CompletedProcess(argv, 0, "0\t0\n", "")
        if argv[:2] == ["git", "ls-remote"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected command: {argv}")

    wf = MockWorkflow(
        runner,
        {"Git canonical remote HEAD": "a" * 40 + "\trefs/heads/main\n"},
    )
    pub = publication_git_identity(wf, tmp_path, local)
    expected_keys = {"remote_name", "remote_ref", "remote", "upstream", "parity", "remote_head"}
    assert set(pub.keys()) == expected_keys
    assert pub["remote_name"] == "origin"
    assert pub["remote_ref"] == "refs/heads/main"
    assert pub["parity"] == "0\t0"
    assert pub["remote_head"] == "a" * 40


def test_configured_publication_identity_is_local_authority_only(tmp_path: Path):
    executed_commands: list[list[str]] = []

    def runner(argv, **_kwargs):
        executed_commands.append(list(argv))
        if argv[:3] == ["git", "config", "--get"] and argv[-1].endswith(".remote"):
            return subprocess.CompletedProcess(argv, 0, "origin\n", "")
        if argv[:3] == ["git", "config", "--get"] and argv[-1].endswith(".merge"):
            return subprocess.CompletedProcess(argv, 0, "refs/heads/main\n", "")
        if argv[:3] == ["git", "remote", "get-url"]:
            return subprocess.CompletedProcess(argv, 0, "git@example.invalid:owner/repo.git\n", "")
        raise AssertionError(f"unexpected command: {argv}")

    identity = configured_publication_identity(MockWorkflow(runner), tmp_path, "main")

    assert identity == {
        "remote_name": "origin",
        "remote_ref": "refs/heads/main",
        "remote": "git@example.invalid:owner/repo.git",
    }
    assert not any(command[:2] == ["git", "ls-remote"] for command in executed_commands)
    assert not any("@{upstream}" in command for command in executed_commands)


def test_publication_git_identity_fails_when_no_remote_configured(tmp_path: Path):
    local = {"branch": "main", "commit": "a" * 40}
    executed_commands: list[list[str]] = []

    def runner(argv, **_kwargs):
        executed_commands.append(list(argv))
        if argv[:3] == ["git", "config", "--get"]:
            return subprocess.CompletedProcess(argv, 1, "", "error")
        return subprocess.CompletedProcess(argv, 0, "", "")

    wf = MockWorkflow(runner)
    with pytest.raises(ConfigError, match="unable to obtain configured Git upstream authority"):
        publication_git_identity(wf, tmp_path, local)

    # Assert ls-remote was NEVER invoked
    for cmd in executed_commands:
        assert cmd[:2] != ["git", "ls-remote"]


@pytest.mark.parametrize(
    ("failed_stage", "message"),
    [
        ("remote-name", "configured Git upstream authority"),
        ("remote-ref", "configured Git upstream authority"),
        ("remote", "Git remote"),
        ("remote-head", "canonical remote branch identity"),
    ],
)
def test_publication_git_identity_fails_closed_on_command_error(
    tmp_path: Path, failed_stage: str, message: str
):
    local = {"branch": "main", "commit": "a" * 40}

    def runner(argv, **_kwargs):
        stage = ""
        output = ""
        if argv[:3] == ["git", "config", "--get"]:
            stage = "remote-ref" if argv[-1].endswith(".merge") else "remote-name"
            output = "refs/heads/main\n" if stage == "remote-ref" else "origin\n"
        elif argv[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            stage, output = "upstream", "origin/main\n"
        elif argv[:3] == ["git", "remote", "get-url"]:
            stage, output = "remote", "git@example.invalid:owner/repo.git\n"
        elif argv[:2] == ["git", "rev-list"]:
            stage, output = "parity", "0\t0\n"
        elif argv[:2] == ["git", "ls-remote"]:
            stage, output = "remote-head", "a" * 40 + "\trefs/heads/main\n"
        else:
            raise AssertionError(f"unexpected command: {argv}")
        return subprocess.CompletedProcess(argv, 1 if stage == failed_stage else 0, output, "failure")

    with pytest.raises(ConfigError, match=message):
        publication_git_identity(MockWorkflow(runner), tmp_path, local)


def test_publication_git_identity_rejects_diverged_parity(tmp_path: Path):
    local = {"branch": "main", "commit": "a" * 40}

    def runner(argv, **_kwargs):
        if argv[:3] == ["git", "config", "--get"] and argv[-1].endswith(".remote"):
            return subprocess.CompletedProcess(argv, 0, "origin\n", "")
        if argv[:3] == ["git", "config", "--get"] and argv[-1].endswith(".merge"):
            return subprocess.CompletedProcess(argv, 0, "refs/heads/main\n", "")
        if argv[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return subprocess.CompletedProcess(argv, 0, "origin/main\n", "")
        if argv[:3] == ["git", "remote", "get-url"]:
            return subprocess.CompletedProcess(argv, 0, "git@example.invalid:owner/repo.git\n", "")
        if argv[:2] == ["git", "rev-list"]:
            return subprocess.CompletedProcess(argv, 0, "1\t0\n", "")
        if argv[:2] == ["git", "ls-remote"]:
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\trefs/heads/main\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    wf = MockWorkflow(runner)
    identity = publication_git_identity(wf, tmp_path, local)
    assert identity["remote_head"] == "a" * 40
    assert identity["parity"] == "1\t0"


def test_git_status_partition_empty_and_mixed():
    rel, digest, count = git_status_partition("")
    assert rel == ""
    assert count == 0
    assert digest == sha256_bytes(b"")

    sample = " M file1.py\n!! ignored.txt\n?? untracked.py\n!! ignored2.txt\n"
    rel, digest, count = git_status_partition(sample)
    assert rel == " M file1.py\n?? untracked.py"
    assert count == 2
    assert digest == sha256_bytes(b"!! ignored.txt\n!! ignored2.txt")
