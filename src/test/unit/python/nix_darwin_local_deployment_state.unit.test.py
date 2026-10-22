# nix-darwin-test-source-target: libexec/nix_darwin_local/deployment_state.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/deployment_participants.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/deployment_workflow.py
from __future__ import annotations

import base64
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture
def modules(repo_root: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    from nix_darwin_local import deployment_participants, deployment_plan, deployment_state, deployment_workflow, paths

    return deployment_state, deployment_participants, deployment_plan, deployment_workflow, paths


def mock_process_table(monkeypatch, state, rows):
    metadata = "".join(
        f"{pid} {ppid} {comm}\n" for pid, ppid, comm, _rendered in rows
    )
    commands = "".join(
        f"{pid} {ppid} {rendered}\n" for pid, ppid, _comm, rendered in rows
    )

    def run(command, **_kwargs):
        if command[-1] == "pid=,ppid=,command=":
            return subprocess.CompletedProcess(command, 0, commands, "")
        if command[-1] == "pid=,ppid=,ucomm=":
            return subprocess.CompletedProcess(command, 0, metadata, "")
        raise AssertionError(f"unexpected process-table command: {command!r}")

    monkeypatch.setattr(state.subprocess, "run", run)


def control_spec(path: Path, pre: bytes = b'agents = ["codex", "claude"]\n', post: bytes = b"agents = []\n"):
    subprocess.run(["xattr", "-c", str(path)], check=True)
    metadata = path.lstat()
    return {
        "path": str(path),
        "safe_ancestors": [str(path.parent)],
        "preimage_sha256": hashlib.sha256(pre).hexdigest(),
        "preimage_bytes_b64": base64.b64encode(pre).decode(),
        "postimage_sha256": hashlib.sha256(post).hexdigest(),
        "postimage_bytes_b64": base64.b64encode(post).decode(),
        "pre_kind": "regular",
        "pre_uid": metadata.st_uid,
        "pre_gid": metadata.st_gid,
        "pre_mode": 0o644,
        "pre_link_count": 1,
        "post_mode": 0o600,
        "metadata_policy": {"owner_group": "preserve", "xattrs": "require-absent", "acl": "require-absent", "flags": "require-zero"},
        "authorization": "after-confirmation-before-switch",
        "restore_before_switch": True,
    }


def test_control_transaction_apgr_fixture_is_atomic_private_and_restorable(modules, tmp_path: Path):
    state, _, _, _, _ = modules
    root = tmp_path / "control"
    root.mkdir(mode=0o700)
    path = root / "runtime.toml"
    path.write_bytes(b'agents = ["codex", "claude"]\n')
    path.chmod(0o644)
    spec = control_spec(path)

    journal = tmp_path / "journal"
    result = state.apply_control_transaction(spec, journal)
    assert path.read_bytes() == b"agents = []\n"
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(Path(result["marker"]).read_text())["state"] == "postimage-installed"
    state.verify_control_postimage(spec)
    state.restore_control_transaction(spec, journal)
    assert path.read_bytes() == b'agents = ["codex", "claude"]\n'
    assert path.stat().st_mode & 0o777 == 0o644
    assert json.loads(Path(result["marker"]).read_text())["state"] == "preimage-restored"


def test_control_postrename_fsync_failure_remains_exactly_restorable(modules, tmp_path: Path, monkeypatch):
    state, _, _, _, _ = modules
    root = tmp_path / "control"
    root.mkdir(mode=0o700)
    path = root / "runtime.toml"
    path.write_bytes(b'agents = ["codex", "claude"]\n')
    path.chmod(0o644)
    spec = control_spec(path)
    original_fsync = state._fsync

    def fail_after_control_rename(candidate: Path):
        if candidate == root:
            raise OSError("injected directory fsync failure")
        original_fsync(candidate)

    monkeypatch.setattr(state, "_fsync", fail_after_control_rename)
    with pytest.raises(OSError, match="injected"):
        state.apply_control_transaction(spec, tmp_path / "journal")
    assert path.read_bytes() == b"agents = []\n"
    monkeypatch.setattr(state, "_fsync", original_fsync)
    state.restore_control_transaction(spec, tmp_path / "journal")
    assert path.read_bytes() == b'agents = ["codex", "claude"]\n'


def test_final_control_snapshot_accepts_only_the_reviewed_postimage_overlay(modules, tmp_path: Path):
    state, _, plan, workflow_module, _ = modules
    root = tmp_path / "control"
    root.mkdir(mode=0o700)
    path = root / "runtime.toml"
    path.write_bytes(b'agents = ["codex", "claude"]\n')
    path.chmod(0o644)
    spec = control_spec(path)
    document = {"machine_control_snapshot": plan.tree_identity(root), "control_transactions": [spec]}

    assert workflow_module._expected_control_snapshot(document, False) == document["machine_control_snapshot"]
    state.apply_control_transaction(spec, tmp_path / "journal")
    assert workflow_module._expected_control_snapshot(document, True) == plan.tree_identity(root)


def test_control_transaction_accepts_exact_idempotent_postimage(modules, tmp_path: Path):
    state, _, _, _, paths = modules
    root = tmp_path / "control"
    root.mkdir(mode=0o700)
    path = root / "runtime.toml"
    path.write_bytes(b"agents = []\n")
    path.chmod(0o600)

    stale = control_spec(path)
    with pytest.raises(paths.ConfigError, match="stale control transaction"):
        state.apply_control_transaction(stale, tmp_path / "stale-journal")
    effective = control_spec(path, pre=b"agents = []\n", post=b"agents = []\n")
    effective["pre_mode"] = 0o600
    effective["restore_before_switch"] = False
    state.apply_control_transaction(effective, tmp_path / "journal")
    state.verify_control_postimage(effective)


@pytest.mark.parametrize("drift", ["content", "mode", "hardlink"])
def test_control_transaction_rejects_preimage_drift(modules, tmp_path: Path, drift: str):
    state, _, _, _, paths = modules
    root = tmp_path / "control"
    root.mkdir(mode=0o700)
    path = root / "runtime.toml"
    path.write_bytes(b'agents = ["codex", "claude"]\n')
    path.chmod(0o644)
    if drift == "content":
        path.write_bytes(b"drift\n")
    elif drift == "mode":
        path.chmod(0o666)
    else:
        os.link(path, root / "second-link")

    with pytest.raises(paths.ConfigError):
        state.apply_control_transaction(control_spec(path), tmp_path / "journal")


def test_control_restore_revalidates_extended_metadata_on_already_restored_bytes(
    modules, tmp_path: Path
):
    state, _, _, _, paths = modules
    root = tmp_path / "control"
    root.mkdir(mode=0o700)
    path = root / "runtime.toml"
    path.write_bytes(b'agents = ["codex", "claude"]\n')
    path.chmod(0o644)
    specification = control_spec(path)
    subprocess.run(
        ["xattr", "-w", "com.example.nix-update", "fixture", str(path)], check=True
    )

    with pytest.raises(paths.ConfigError, match="xattr"):
        state.restore_control_transaction(specification, tmp_path / "journal")
    assert path.read_bytes() == b'agents = ["codex", "claude"]\n'


def test_single_run_lock_is_durable_and_exclusive(modules, tmp_path: Path):
    state, _, _, _, paths = modules
    lock_path = tmp_path / "state/transaction.lock"

    with state.DeploymentLock(lock_path):
        assert lock_path.read_text().strip() == str(os.getpid())
        assert lock_path.stat().st_mode & 0o777 == 0o600
        with pytest.raises(paths.ConfigError, match="another nix-update transaction"):
            with state.DeploymentLock(lock_path):
                pass


def test_deployment_lock_rejects_hardlink_race_without_touching_victim(modules, tmp_path: Path, monkeypatch):
    state, _, _, _, paths = modules
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    lock_path = root / "transaction.lock"
    victim = root / "victim"
    victim.write_text("preserve\n")
    victim.chmod(0o600)
    original_open = state.os.open
    injected = {"done": False}

    def raced_open(path, flags, mode=0o777):
        if Path(path) == lock_path and not injected["done"]:
            injected["done"] = True
            os.link(victim, lock_path)
        return original_open(path, flags, mode)

    monkeypatch.setattr(state.os, "open", raced_open)
    with pytest.raises(paths.ConfigError, match="single-link"):
        with state.DeploymentLock(lock_path):
            pass
    assert victim.read_text() == "preserve\n"


def test_structured_process_guard_ignores_prompt_text_and_services(modules):
    state, _, _, _, _ = modules
    records = [
        {"pid": 1, "argv": ["python", "agent", "prompt says darwin-rebuild switch"]},
        {"pid": 2, "argv": ["jaca-daemon", "serve"]},
        {"pid": 3, "argv": ["nix", "flake", "update", "security"]},
        {"pid": 4, "argv": ["/path/nix-update", "apply", "/plan"]},
        {"pid": 5, "argv": ["darwin-rebuild", "build"]},
        {"pid": 6, "argv": ["sudo", "HOMEBREW_NO_AUTO_UPDATE=1", "darwin-rebuild", "switch"]},
        {"pid": 7, "argv": ["env", "NIX_CONFIG=fixture", "nix-env", "--switch-generation", "187"]},
        {"pid": 8, "argv": ["python3.14", "/tools/nix-update", "apply", "/plan"]},
        {"pid": 9, "argv": ["bash", "/tools/darwin-rebuild", "switch"]},
        {"pid": 10, "argv": ["sh", "/tools/nix", "flake", "lock"]},
        {"pid": 11, "argv": ["python3", "-c", "print('nix-update apply')"]},
        {"pid": 12, "argv": ["env", "--chdir", "/tmp", "nix-update", "apply", "/plan"]},
        {"pid": 13, "argv": ["env", "-C", "/tmp", "nix", "flake", "update"]},
        {"pid": 14, "argv": ["python3", "-W", "ignore", "/tools/nix-update", "apply", "/plan"]},
        {"pid": 15, "argv": ["python3", "-m", "nix-update", "apply", "/plan"]},
        {"pid": 16, "argv": ["nix", "--option", "experimental-features", "nix-command", "flake", "update"]},
        {"pid": 17, "argv": ["/tmp/nix", "flake", "update"]},
        {"pid": 18, "argv": ["nix", "--help", "prompt", "says", "flake", "update"]},
        {"pid": 19, "argv": ["nix", "--fallback", "flake", "lock"]},
        {"pid": 20, "argv": ["nix", "--debug", "flake", "update"]},
        {"pid": 21, "argv": ["nix", "--log-format", "raw", "flake", "lock"]},
        {"pid": 22, "argv": ["nix", "--print-build-logs", "flake", "update"]},
        {"pid": 23, "argv": ["nix", "--quiet", "flake", "lock"]},
        {"pid": 24, "argv": ["nix", "--verbose", "flake", "update"]},
        {"pid": 25, "argv": ["nix", "-L", "flake", "lock"]},
        {"pid": 26, "argv": ["nix", "-vv", "flake", "update"]},
    ]

    matches = state.competing_processes(records)
    assert [item["pid"] for item in matches] == [
        3, 4, 6, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17, 19, 20, 21, 22, 23, 24, 25, 26,
    ]


def test_process_table_preserves_quoted_prompt_as_one_argument(modules, monkeypatch):
    state, _, _, _, _ = modules
    mock_process_table(
        monkeypatch,
        state,
        [(22, 1, "nix", 'nix --help "prompt says flake update"')],
    )
    assert state.read_process_table() == [
        {"pid": 22, "ppid": 1, "comm": "nix", "argv": ["nix", "--help", "prompt says flake update"]},
    ]


def test_process_gate_blocks_documented_nix_and_sudo_long_option_forms(modules, monkeypatch):
    state, _, _, _, _ = modules
    mock_process_table(
        monkeypatch,
        state,
        [
            (1, 99, "nix", "nix --repair flake lock"),
            (2, 99, "sudo", "sudo --user root nix flake update"),
            (3, 99, "sudo", "sudo --group wheel darwin-rebuild switch"),
            (4, 99, "sudo", "sudo --chdir /tmp nix-env --switch-generation 187"),
        ],
    )

    records = state.read_process_table()

    assert records == [
        {"pid": 1, "ppid": 99, "comm": "nix", "argv": ["nix", "--repair", "flake", "lock"]},
        {"pid": 2, "ppid": 99, "comm": "sudo", "argv": ["sudo", "--user", "root", "nix", "flake", "update"]},
        {
            "pid": 3,
            "ppid": 99,
            "comm": "sudo",
            "argv": ["sudo", "--group", "wheel", "darwin-rebuild", "switch"],
        },
        {
            "pid": 4,
            "ppid": 99,
            "comm": "sudo",
            "argv": ["sudo", "--chdir", "/tmp", "nix-env", "--switch-generation", "187"],
        },
    ]
    assert [item["pid"] for item in state.competing_processes(records)] == [1, 2, 3, 4]


@pytest.mark.parametrize(
    "rendered",
    [
        "nix --repair flake lock",
        "nix --log-format raw flake update",
        "nix --log-format=raw flake lock",
        "nix --option warn-dirty false flake update",
        "nix --option=warn-dirty false flake lock",
        "nix --max-jobs 4 flake update",
        "nix --no-warn-dirty flake lock",
        "nix -vv flake update",
    ],
)
def test_process_gate_blocks_nix_global_option_forms_from_process_table(modules, monkeypatch, rendered):
    state, _, _, _, _ = modules
    mock_process_table(monkeypatch, state, [(201, 99, "nix", rendered)])

    matches = state.competing_processes(state.read_process_table())

    assert [(item["pid"], item["reason"]) for item in matches] == [
        (201, "nix flake lock mutation"),
    ]


@pytest.mark.parametrize(
    ("comm", "rendered"),
    [
        ("sudo", "sudo -u root /nix/store/version/bin/nix flake update"),
        ("sudo", "sudo --user=root /run/current-system/sw/bin/nix flake lock"),
        ("sudo", "sudo --user root /tmp/nix flake update"),
        ("sudo", "sudo --group=wheel /old/darwin-rebuild switch"),
        ("sudo", "sudo --host build-host nix flake lock"),
        ("sudo", "sudo --prompt 'Password: ' nix flake update"),
        ("sudo", "sudo --close-from 3 nix flake lock"),
        ("sudo", "sudo --command-timeout=30 nix flake update"),
        ("sudo", "sudo --chdir /tmp nix-env --switch-generation 187"),
        ("sudo", "sudo --chroot=/tmp darwin-rebuild switch"),
        ("sudo", "sudo -C 3 -T 30 -D /tmp -R /tmp -g wheel -h host -p prompt -u root nix flake lock"),
        ("sudo", "sudo -nE -uroot nix flake update"),
        ("sudo", "sudo -- /tmp/nix flake update"),
        ("env", "env -C /tmp nix flake update"),
        ("env", "env -iv -uNIX_CONFIG nix flake update"),
        ("env", "env -P /bin nix flake lock"),
        ("env", "env --chdir=/tmp nix flake lock"),
        ("env", "env --unset NIX_CONFIG nix flake update"),
        ("env", "env --unset=NIX_CONFIG nix flake lock"),
        ("env", "env -S 'sudo --user root nix flake update'"),
        ("env", "env -S'nix flake lock'"),
        ("env", "env --split-string='nix flake lock'"),
        ("sudo", "sudo env A=1 sudo --user root env -C /tmp B=2 /tmp/nix flake update"),
        ("bash", "bash /tmp/nix flake lock"),
        ("python3.14", "python3.14 -W ignore /old/nix-update apply /reviewed-plan"),
    ],
)
def test_process_gate_unwraps_source_owned_wrapper_forms_from_process_table(
    modules, monkeypatch, comm, rendered,
):
    state, _, _, _, _ = modules
    mock_process_table(monkeypatch, state, [(202, 99, comm, rendered)])

    matches = state.competing_processes(state.read_process_table())

    assert len(matches) == 1
    assert matches[0]["pid"] == 202


@pytest.mark.parametrize(
    ("comm", "rendered"),
    [
        ("sudo", 'sudo --user root /tmp/nix flake update "unterminated'),
        ("sudo", 'sudo --user=root /run/current-system/sw/bin/nix flake lock "unterminated'),
        ("env", 'env --chdir /tmp /old/nix flake update "unterminated'),
        ("env", 'env --chdir=/tmp /nix/store/version/bin/nix flake lock "unterminated'),
        (
            "sudo",
            'sudo --user root env --unset=NIX_CONFIG /old/darwin-rebuild switch "unterminated',
        ),
    ],
)
def test_process_gate_recovers_known_wrapper_arity_after_parse_failure(
    modules, monkeypatch, comm, rendered,
):
    state, _, _, _, _ = modules
    mock_process_table(monkeypatch, state, [(205, 99, comm, rendered)])

    records = state.read_process_table()
    matches = state.competing_processes(records)

    assert records[0]["parse_error"] == "unmatched process arguments"
    assert [(item["pid"], item["reason"]) for item in matches] == [
        (205, "unparseable sensitive process"),
    ]


@pytest.mark.parametrize(
    ("comm", "rendered"),
    [
        ("sudo", 'sudo env --unknown "unterminated nix flake update'),
        ("env", 'env sudo --user "unterminated nix flake update'),
        ("sudo", 'sudo --user "unterminated nix flake update'),
        ("sudo", 'sudo -h "unterminated nix flake update'),
        ("env", 'env --unset "unterminated nix flake update'),
    ],
)
def test_process_gate_fails_closed_for_unknown_or_truncated_nested_wrappers(
    modules, monkeypatch, comm, rendered,
):
    state, _, _, _, _ = modules
    mock_process_table(monkeypatch, state, [(206, 99, comm, rendered)])

    matches = state.competing_processes(state.read_process_table())

    assert [(item["pid"], item["reason"]) for item in matches] == [
        (206, f"unparseable {comm} command position"),
    ]


@pytest.mark.parametrize(
    ("comm", "rendered"),
    [
        ("bash", "bash -O extglob /tmp/nix flake update"),
        ("sh", "sh -o errexit /tmp/nix flake update"),
        ("bash", "bash +O extglob /tmp/nix flake update"),
        ("sh", "sh +o errexit /tmp/nix flake update"),
        ("bash", "bash +eux /tmp/nix flake update"),
        (
            "python3",
            "python3 --check-hash-based-pycs always /old/nix-update apply /reviewed-plan",
        ),
        ("bash", "bash -eux -- /nix/store/version/bin/nix flake lock"),
        ("python3.13", "python3.13 -bb -X dev /tmp/nix flake update"),
        (
            "env",
            "env -C /tmp sudo --user root bash -O extglob "
            "/run/current-system/sw/bin/darwin-rebuild switch",
        ),
    ],
)
def test_process_gate_consumes_interpreter_option_operands(
    modules, monkeypatch, comm, rendered,
):
    state, _, _, _, _ = modules
    mock_process_table(monkeypatch, state, [(207, 99, comm, rendered)])

    matches = state.competing_processes(state.read_process_table())

    assert len(matches) == 1
    assert matches[0]["pid"] == 207


@pytest.mark.parametrize(
    ("comm", "rendered"),
    [
        ("bash", "bash -O"),
        ("bash", "bash +O"),
        ("bash", "bash +Oextglob /tmp/nix flake update"),
        ("sh", "sh --rcfile=/tmp/options /tmp/nix flake update"),
        (
            "python3",
            "python3 --check-hash-based-pycs=always /old/nix-update apply /reviewed-plan",
        ),
        (
            "python3",
            "python3 --check-hash-based-pycs sometimes /old/nix-update apply /reviewed-plan",
        ),
        ("python3", "python3 --future-option /tmp/nix flake update"),
    ],
)
def test_process_gate_fails_closed_for_unsupported_or_truncated_interpreter_options(
    modules, monkeypatch, comm, rendered,
):
    state, _, _, _, _ = modules
    mock_process_table(monkeypatch, state, [(208, 99, comm, rendered)])

    matches = state.competing_processes(state.read_process_table())

    assert [(item["pid"], item["reason"]) for item in matches] == [
        (208, "ambiguous interpreter options"),
    ]


@pytest.mark.parametrize(
    ("comm", "rendered"),
    [
        ("sudo", "sudo --login-class staff nix flake update"),
        ("sudo", "sudo --user"),
        ("env", "env --unknown nix flake lock"),
        ("env", "env --unset"),
    ],
)
def test_process_gate_fails_closed_for_ambiguous_wrapper_options(
    modules, monkeypatch, comm, rendered,
):
    state, _, _, _, _ = modules
    mock_process_table(monkeypatch, state, [(203, 99, comm, rendered)])

    matches = state.competing_processes(state.read_process_table())

    assert matches == [{
        "pid": 203,
        "argv": rendered.split(),
        "reason": "ambiguous wrapper options",
    }]


@pytest.mark.parametrize(
    "rendered",
    [
        "nix --future-setting value flake update",
        "nix --future-toggle flake lock",
    ],
)
def test_process_gate_fails_closed_for_ambiguous_nix_options(modules, monkeypatch, rendered):
    state, _, _, _, _ = modules
    mock_process_table(monkeypatch, state, [(203, 99, "nix", rendered)])

    matches = state.competing_processes(state.read_process_table())

    assert [(item["pid"], item["reason"]) for item in matches] == [
        (203, "ambiguous nix command options"),
    ]


@pytest.mark.parametrize(
    "rendered",
    [
        "nix --help 'prompt says flake update'",
        "nix --version flake lock",
        "sudo --list",
        "sudo -h",
        "env --help",
        "bash -c 'echo nix flake update'",
        "python3 -c 'print(\"nix flake update\")'",
    ],
)
def test_process_gate_keeps_terminal_and_prompt_text_safe_from_process_table(
    modules, monkeypatch, rendered,
):
    state, _, _, _, _ = modules
    mock_process_table(monkeypatch, state, [(204, 99, "prompt", rendered)])

    assert state.competing_processes(state.read_process_table()) == []


def test_process_gate_consumes_sudo_host_operand(modules, monkeypatch):
    state, _, _, _, _ = modules
    mock_process_table(
        monkeypatch,
        state,
        [(210, 99, "sudo", "sudo -h build-host /tmp/nix flake update")],
    )

    matches = state.competing_processes(state.read_process_table())

    assert [item["pid"] for item in matches] == [210]


@pytest.mark.parametrize(
    ("comm", "rendered"),
    [
        ("sudo", 'sudo --list "unterminated nix flake update'),
        ("env", 'env --version "unterminated nix flake update'),
        ("bash", 'bash -c "echo nix flake update'),
        ("sh", 'sh -c "echo darwin-rebuild switch'),
        ("python3", 'python3 -c "print(\\\"nix flake lock\\\")'),
    ],
)
def test_process_gate_keeps_unparseable_terminal_and_source_text_nonblocking(
    modules, monkeypatch, comm, rendered,
):
    state, _, _, _, _ = modules
    mock_process_table(monkeypatch, state, [(209, 99, comm, rendered)])

    records = state.read_process_table()

    assert records[0]["parse_error"] == "unmatched process arguments"
    assert state.competing_processes(records) == []


def test_structured_process_guard_blocks_only_the_exact_declared_participant(modules, tmp_path: Path):
    state, _, _, _, _ = modules
    participant = tmp_path / "resident-reconciler"
    records = [
        {"pid": 7, "argv": [str(participant), "--phase", "post-switch-reconcile"]},
        {"pid": 8, "argv": ["resident-reconciler", "status"]},
    ]

    matches = state.competing_processes(records, participant_paths=[str(participant)])
    assert [(item["pid"], item["reason"]) for item in matches] == [(7, "deployment participant mutation")]


@pytest.mark.parametrize(
    "argv",
    [
        ["/nix/store/other-nix/bin/nix", "flake", "update"],
        ["/run/current-system/sw/bin/darwin-rebuild", "switch"],
        ["/tmp/nix", "flake", "lock"],
        ["/old/tools/nix-update", "apply", "/reviewed-plan"],
        ["sudo", "--", "/tmp/nix", "flake", "update"],
        ["env", "NIX_CONFIG=fixture", "/tmp/nix", "flake", "lock"],
        ["sudo", "env", "A=1", "sudo", "env", "B=2", "/tmp/nix", "flake", "update"],
        ["bash", "/tmp/nix", "flake", "update"],
        ["python3", "-W", "ignore", "/old/nix-update", "apply", "/reviewed-plan"],
        ["nix", "--option", "substituters", "https://cache.invalid", "flake", "update"],
        ["nix", "--store", "daemon", "--show-trace", "flake", "lock"],
        ["/run/current-system/sw/bin/darwin-rebuild", "--flake", "/tmp/config#host", "switch"],
        ["darwin-rebuild", "--verbose", "activate"],
        ["darwin-rebuild", "build", "--name", "switch"],
    ],
)
def test_process_gate_blocks_mutation_across_every_executable_path_and_wrapper(modules, argv):
    state, _, _, _, _ = modules
    matches = state.competing_processes([{"pid": 101, "comm": Path(argv[0]).name, "argv": argv}])
    assert len(matches) == 1
    assert "mutation" in matches[0]["reason"] or matches[0]["reason"] == "nix-update apply"


@pytest.mark.parametrize(
    ("comm", "rendered"),
    [
        ("nix", "/tmp/nix flake update 'unterminated"),
        ("bash", "bash /tmp/nix flake update 'unterminated"),
        ("python3", "python3 /old/nix-update apply 'unterminated"),
    ],
)
def test_process_gate_fails_closed_for_unparseable_sensitive_processes(modules, comm, rendered):
    state, _, _, _, _ = modules
    record = {
        "pid": 102, "ppid": 1, "comm": comm, "argv": [],
        "rendered_args": rendered, "parse_error": "unmatched process arguments",
    }
    assert state.competing_processes([record]) == [{
        "pid": 102, "argv": [], "comm": comm, "reason": "unparseable sensitive process",
    }]


def test_process_gate_fails_closed_for_malformed_sensitive_wrapper_records(
    modules, monkeypatch,
):
    state, _, _, _, _ = modules
    expected_records = [
        {
            "pid": 301,
            "ppid": 1,
            "comm": "sudo",
            "argv": [],
            "rendered_args": 'sudo --unknown "unterminated nix flake update',
            "parse_error": "unmatched process arguments",
        },
        {
            "pid": 302,
            "ppid": 1,
            "comm": "env",
            "argv": [],
            "rendered_args": 'env --unknown "unterminated nix flake lock',
            "parse_error": "unmatched process arguments",
        },
    ]
    mock_process_table(
        monkeypatch,
        state,
        [
            (301, 1, "sudo", 'sudo --unknown "unterminated nix flake update'),
            (302, 1, "env", 'env --unknown "unterminated nix flake lock'),
        ],
    )

    records = state.read_process_table()

    assert records == expected_records
    assert state.competing_processes(records) == [
        {
            "pid": 301,
            "argv": [],
            "comm": "sudo",
            "reason": "unparseable sudo command position",
        },
        {
            "pid": 302,
            "argv": [],
            "comm": "env",
            "reason": "unparseable env command position",
        },
    ]


def test_process_table_keeps_whitespace_bearing_ucomm_out_of_argv(modules, monkeypatch):
    state, _, _, _, _ = modules
    mock_process_table(
        monkeypatch,
        state,
        [(401, 1, "accounting command", "/tmp/nix flake update")],
    )

    records = state.read_process_table()

    assert records == [{
        "pid": 401,
        "ppid": 1,
        "comm": "accounting command",
        "argv": ["/tmp/nix", "flake", "update"],
    }]
    assert [item["pid"] for item in state.competing_processes(records)] == [401]


@pytest.mark.parametrize(
    ("comm", "reason"),
    [
        ("sudo", "unparseable sudo command position"),
        ("", "unparseable process identity"),
    ],
)
def test_process_table_fails_closed_when_command_identity_is_unavailable(
    modules, monkeypatch, comm, reason,
):
    state, _, _, _, _ = modules
    mock_process_table(monkeypatch, state, [(402, 1, comm, "")])

    records = state.read_process_table()

    assert records[0]["parse_error"] == "process arguments unavailable"
    assert state.competing_processes(records) == [{
        "pid": 402,
        "argv": [],
        "comm": comm,
        "reason": reason,
    }]


def test_process_gate_does_not_promote_unparseable_prompt_text_to_a_command(modules):
    state, _, _, _, _ = modules
    record = {
        "pid": 103, "ppid": 1, "comm": "python3", "argv": [],
        "rendered_args": "python3 -c 'prompt mentions nix flake update",
        "parse_error": "unmatched process arguments",
    }
    assert state.competing_processes([record]) == []


def test_process_gate_fails_closed_without_structured_process_identity(modules):
    state, _, _, _, _ = modules
    record = {
        "pid": 104,
        "ppid": 1,
        "comm": "",
        "argv": [],
        "rendered_args": 'sudo --user root "unterminated',
        "parse_error": "unmatched process arguments",
    }

    assert state.competing_processes([record]) == [{
        "pid": 104,
        "argv": [],
        "comm": "",
        "reason": "unparseable process identity",
    }]


@pytest.mark.parametrize(
    ("comm", "include_noniterable_argv", "reason"),
    [
        ("sudo", False, "unparseable sudo command position"),
        ("nix", True, "unparseable sensitive process"),
    ],
)
def test_process_gate_fails_closed_when_parse_error_record_lacks_usable_argv(
    modules, comm, include_noniterable_argv, reason,
):
    state, _, _, _, _ = modules
    record = {
        "pid": 105,
        "ppid": 1,
        "comm": comm,
        "rendered_args": "",
        "parse_error": "process arguments unavailable",
    }
    if include_noniterable_argv:
        record["argv"] = None

    assert state.competing_processes([record]) == [{
        "pid": 105,
        "argv": [],
        "comm": comm,
        "reason": reason,
    }]


def test_process_gate_ignores_only_current_ancestry_not_alternate_mutation_tools(
    modules, monkeypatch,
):
    state, _, _, _, _ = modules
    mock_process_table(
        monkeypatch,
        state,
        [
            (10, 1, "nix-update", "/reviewed/nix-update apply"),
            (11, 1, "nix", "/tmp/nix flake update"),
        ],
    )
    records = state.read_process_table()

    assert [item["pid"] for item in state.competing_processes(records, ignored_pids={10})] == [11]


def make_profiles(tmp_path: Path, selected_generation: int, live_generation: int):
    profiles = tmp_path / "profiles"
    profiles.mkdir(parents=True)
    store = tmp_path / "store"
    store.mkdir()
    targets = {}
    for generation in (187, 188, 189):
        target = store / f"system-{generation}"
        target.mkdir()
        link = profiles / f"system-{generation}-link"
        link.symlink_to(target)
        targets[generation] = target
    selected = profiles / "system"
    selected.symlink_to(profiles / f"system-{selected_generation}-link")
    live = tmp_path / "current-system"
    live.symlink_to(targets[live_generation])
    return selected, live, profiles / "system-*-link", targets


@pytest.mark.parametrize(
    ("selected", "live", "relationship"),
    [(187, 187, "aligned"), (188, 187, "split-retained")],
)
def test_profile_live_relationships_are_classified_independently(modules, tmp_path: Path, selected, live, relationship):
    state, _, _, _, _ = modules
    profile, current, generation_glob, _ = make_profiles(tmp_path, selected, live)

    assert state.inspect_profile(profile, current, generation_glob)["relationship"] == relationship


def test_profile_unknown_relationship_blocks(modules, tmp_path: Path):
    state, _, _, _, _ = modules
    profile, current, generation_glob, targets = make_profiles(tmp_path, 188, 187)
    external = tmp_path / "external"
    external.mkdir()
    current.unlink()
    current.symlink_to(external)

    assert state.inspect_profile(profile, current, generation_glob)["relationship"] == "unknown"
    assert state.realignment_command(profile, 187)[-2:] == ["--switch-generation", "187"]


def test_apply_revalidation_binds_selected_profile_and_live_state(modules, tmp_path: Path, monkeypatch):
    _, _, _, workflow_module, paths = modules
    reviewed_profile = {"relationship": "aligned", "selected": {"target": "/safe"}, "live": {"target": "/safe"}}
    document = {
        "tool": {"identity": "tool"},
        "environment": {"identity": "environment"},
        "selected_flake": {"path": str(tmp_path), "repository": {"identity": "repository"}},
        "lock": {"identity": "lock"},
        "machine_control_snapshot": {
            "root_identity": {"path": str(tmp_path / "control")}, "identity": "control"
        },
        "evaluation_root": str(tmp_path / "eval"),
        "evaluation_current": {"exists": False, "link": None},
            "projection": {"root": str(tmp_path / "projection"), "files": [], "digest": "projection"},
            "overrides": ["--override-input", "localConfig", f"path:{tmp_path / 'projection'}"],
        "live": {"profile": reviewed_profile},
        "input_snapshots": [],
        "participants": [],
        "state_probes": [],
        "external_state": [],
        "profile_policy": {},
        "target": {"derivation_output": "/target", "closure": []},
    }
    monkeypatch.setattr(workflow_module, "_tool_identity", lambda *_args: document["tool"])
    monkeypatch.setattr(workflow_module, "_environment_identity", lambda: document["environment"])
    monkeypatch.setattr(workflow_module, "local_git_identity", lambda *_args: document["selected_flake"]["repository"])
    monkeypatch.setattr(workflow_module, "normalized_lock", lambda *_args: document["lock"])
    monkeypatch.setattr(workflow_module, "tree_identity", lambda *_args: document["machine_control_snapshot"])
    evaluation_current = {"value": workflow_module.CurrentSnapshot(False, None)}
    monkeypatch.setattr(workflow_module, "snapshot_current", lambda *_args: evaluation_current["value"])
    monkeypatch.setattr(
        workflow_module,
        "inspect_scratch",
        lambda *_args: type("Projection", (), {"files": (), "digest": "projection"})(),
    )
    monkeypatch.setattr(workflow_module, "_validate_virtual_control_projection", lambda *_args: None)
    monkeypatch.setattr(workflow_module, "_profile_snapshot", lambda *_args: reviewed_profile)
    workflow_module._revalidate_plan(workflow_module.Workflow(tmp_path), document)
    published = workflow_module.CurrentSnapshot(True, "generations/reviewed")
    evaluation_current["value"] = published
    workflow_module._revalidate_plan(
        workflow_module.Workflow(tmp_path), document,
        expected_evaluation_current=published, validate_profile=False,
    )
    with pytest.raises(paths.ConfigError, match="evaluation current"):
        workflow_module._revalidate_plan(workflow_module.Workflow(tmp_path), document)
    evaluation_current["value"] = workflow_module.CurrentSnapshot(False, None)
    monkeypatch.setattr(
        workflow_module,
        "_environment_identity",
        lambda: {"identity": "environment", "operator": {"uid": 999999}, "host": {}, "platform": {}},
    )
    with pytest.raises(paths.ConfigError, match="environment drifted"):
        workflow_module._revalidate_plan(workflow_module.Workflow(tmp_path), document)
    monkeypatch.setattr(workflow_module, "_environment_identity", lambda: document["environment"])
    monkeypatch.setattr(
        workflow_module,
        "_profile_snapshot",
        lambda *_args: {**reviewed_profile, "live": {"target": "/drift"}},
    )
    with pytest.raises(paths.ConfigError, match="selected-profile or live-system"):
        workflow_module._revalidate_plan(workflow_module.Workflow(tmp_path), document)


def participant_spec(path: Path, digest: str):
    metadata = path.lstat() if path.exists() else None
    identity = None if metadata is None else {
        "path": str(path),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": metadata.st_mode & 0o7777,
        "link_count": metadata.st_nlink,
        "size": metadata.st_size,
        "sha256": digest,
    }
    return {
        "name": "resident-reconciler",
        "protocol": "nix-update-participant-v1",
        "path": str(path),
        "sha256": digest,
        "phases": ["preflight", "pre-switch", "post-switch-reconcile", "post-switch-verify", "rollback"],
        "mutation_authority": ["resident"],
        "rollback_authority": ["resident"],
        "timeout_seconds": 30,
        "provenance": {
            "kind": "operator-artifact", "identity": f"sha256:{digest}",
            "root": str(path.parent), "source_path": str(path), "source_identity": identity,
            "frozen_root": str(path.parent), "frozen_path": str(path), "frozen_identity": identity,
        },
    }


def participant_output(phase: str, status="ok", blockers=None, rollback_handle=None, context_digest=""):
    document = {
        "protocol": "nix-update-participant-v1",
        "name": "resident-reconciler",
        "phase": phase,
        "status": status,
        "blockers": blockers or [],
        "observations": {"daemon": "stopped"},
        "context_digest": context_digest,
    }
    if rollback_handle:
        document["rollback_handle"] = rollback_handle
    if phase == "post-switch-reconcile":
        document["mutation_state"] = "mutated" if rollback_handle else "unchanged"
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def participant_context(path: Path, declarations=None, stage="preflight"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    declarations = [] if declarations is None else declarations
    declaration_digest = hashlib.sha256((
        json.dumps({"participants": declarations}, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()).hexdigest()
    document = {
        "schema": "nix-update-participant-context-v1",
        "protocol": "nix-update-participant-v1",
        "stage": stage,
        "plan_digest": "a" * 64,
        "target": {"output": "/nix/store/fixture-target", "closure": []},
        "participant_declarations": declarations,
        "participant_declaration_digest": declaration_digest,
    }
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
    path.chmod(0o600)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def participant_context_digest(argv):
    return hashlib.sha256(Path(argv[argv.index("--context") + 1]).read_bytes()).hexdigest()


def test_participant_is_exact_path_sha_protocol_and_exit_bound(modules, tmp_path: Path):
    _, participants, _, _, paths = modules
    executable = tmp_path / "participant"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    spec = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())

    context, _ = participant_context(tmp_path / "context.json")
    def runner(argv, **_kwargs):
        return subprocess.CompletedProcess([], 0, participant_output("preflight", context_digest=participant_context_digest(argv)), "")

    result = participants.run_participant(spec, "preflight", context, runner)
    assert result.status == "ok"
    spec["sha256"] = "0" * 64
    with pytest.raises(paths.ConfigError, match="SHA-256 drift"):
        participants.run_participant(spec, "preflight", context, runner)


def test_target_closure_participant_provenance_binds_target_and_closure(modules):
    _, _, _, workflow_module, paths = modules
    participant = {
        "name": "closure-participant",
        "path": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-participant/bin/participant",
        "provenance": {"kind": "target-closure", "identity": "/nix/store/target-system"},
    }
    closure = [{"path": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-participant"}]
    workflow_module._validate_participant_provenance([participant], "/nix/store/target-system", closure)
    with pytest.raises(paths.ConfigError, match="outside the reviewed target closure"):
        workflow_module._validate_participant_provenance([participant], "/nix/store/target-system", [])
    with pytest.raises(paths.ConfigError, match="does not equal reviewed target"):
        workflow_module._validate_participant_provenance([participant], "/nix/store/other-system", closure)


@pytest.mark.parametrize("defect", ["duplicate", "noncanonical", "exit", "unknown", "trailing"])
def test_participant_strict_json_and_exit_contract_fail_closed(modules, tmp_path: Path, defect: str):
    _, participants, _, _, paths = modules
    executable = tmp_path / "participant"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    spec = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())
    context, context_digest = participant_context(tmp_path / "context.json")
    output = participant_output("preflight", context_digest=context_digest)
    status = 0
    if defect == "duplicate":
        output = output.replace('"name":', '"name":"resident-reconciler","name":', 1)
    elif defect == "noncanonical":
        output = json.dumps(json.loads(output), indent=2) + "\n"
    elif defect == "exit":
        status = 21
    elif defect == "unknown":
        document = json.loads(output)
        document["command"] = "unsafe"
        output = json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    else:
        output += "trailing"

    def runner(*_args, **_kwargs):
        return subprocess.CompletedProcess([], status, output, "")

    with pytest.raises(paths.ConfigError):
        participants.run_participant(spec, "preflight", context, runner)


def test_participant_secret_bearing_observation_is_rejected_before_evidence_write(modules, tmp_path: Path):
    _, participants, _, _, paths = modules
    executable = tmp_path / "participant"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)
    spec = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())
    context, context_digest = participant_context(tmp_path / "context.json")
    document = json.loads(participant_output("preflight", context_digest=context_digest))
    document["observations"] = {"api_token": "sentinel"}
    output = json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"

    with pytest.raises(paths.ConfigError, match="secret-bearing"):
        participants.run_participant(
            spec,
            "preflight",
            context,
            lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, output, ""),
        )


@pytest.mark.parametrize(
    ("scenario", "phase", "status", "exit_status"),
    [
        ("stopped", "preflight", "ok", 0),
        ("running", "pre-switch", "ok", 0),
        ("unknown-status", "preflight", "blocked", 20),
        ("stop-failure", "pre-switch", "failed", 21),
        ("start-failure", "post-switch-reconcile", "failed", 21),
    ],
)
def test_resident_participant_acceptance_scenarios_are_typed(modules, tmp_path: Path, scenario, phase, status, exit_status):
    _, participants, _, _, _ = modules
    executable = tmp_path / "resident-participant"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    spec = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())
    blockers = [] if status == "ok" else [scenario]

    context, _ = participant_context(tmp_path / "context.json", [spec], phase)
    def runner(argv, **_kwargs):
        return subprocess.CompletedProcess([], exit_status, participant_output(phase, status, blockers, None, participant_context_digest(argv)), "")

    result = participants.run_participant(spec, phase, context, runner)
    assert result.status == status
    assert (not result.blockers) == (status == "ok")


def plan_document(plan_module):
    root_identity = {
        "path": "/fixture/control", "kind": "directory", "mode": 0o700,
        "uid": 1, "gid": 1, "link_count": 2, "resolved_target": "/fixture/control",
    }
    content_identity = {"entries": [], "volatile": []}
    content_identity["digest"] = plan_module.sha256_bytes(
        plan_module.canonical_bytes(content_identity)
    )
    machine_control = {"root_identity": root_identity, "content_identity": content_identity}
    machine_control["digest"] = plan_module.sha256_bytes(
        plan_module.canonical_bytes(machine_control)
    )
    nix_file_id = {
        "path": "/fixture/flake/flake.nix",
        "mode": 0o644,
        "uid": os.getuid(),
        "gid": os.getgid(),
        "kind": "regular",
        "link_count": 1,
        "sha256": "0" * 64,
        "size": 10,
    }
    lock_file_id = {
        "path": "/fixture/flake/flake.lock",
        "mode": 0o644,
        "uid": os.getuid(),
        "gid": os.getgid(),
        "kind": "regular",
        "link_count": 1,
        "sha256": "1" * 64,
        "size": 20,
    }
    repository_fixture = {
        "repository_root": "/fixture",
        "commit": "a" * 40,
        "tree": "b" * 40,
        "branch": "main",
        "status": "",
        "operation_state": "clean",
        "index_sha256": "c" * 64,
        "flake_nix": json.dumps(nix_file_id, sort_keys=True, separators=(",", ":")),
        "flake_lock": json.dumps(lock_file_id, sort_keys=True, separators=(",", ":")),
        "ignored_status_sha256": "d" * 64,
        "ignored_status_count": "0",
    }
    context = {
        "schema": "nix-update-participant-context-v1", "protocol": "nix-update-participant-v1",
        "stage": "prepare", "plan_digest": "", "selected_repository": repository_fixture,
        "selected_flake": "/fixture/flake", "flake_lock": {}, "host": "FixtureHost",
        "environment": {}, "target": {"output": "/nix/store/fixture-system", "closure": []},
        "overrides": ["--override-input", "localConfig", "path:/fixture/projection"],
        "overrides_digest": plan_module.sha256_bytes(plan_module.canonical_bytes({
            "overrides": ["--override-input", "localConfig", "path:/fixture/projection"]
        })),
        "projection": {"root": "/fixture/projection", "files": [], "digest": "projection", "published": False},
        "sources": [], "control_projection": {}, "profile": {"relationship": "aligned"},
        "state_probes": [], "external_state": [], "participant_declarations": [],
        "participant_declaration_digest": plan_module.sha256_bytes(plan_module.canonical_bytes({"participants": []})),
    }
    context["plan_draft_identity"] = plan_module.sha256_bytes(plan_module.canonical_bytes(context))
    context_digest = plan_module.sha256_bytes(plan_module.canonical_bytes(context))
    return {
        "schema": plan_module.SCHEMA,
        "plan_digest": "",
        "status": "READY_FOR_REVIEW",
        "blockers": [],
        "preflight_only": False,
        "tool": {
            "external": {
                "sudo": {"path": "sudo"},
                "darwin-rebuild": {"path": "darwin-rebuild"},
                "nix-env": {"path": "nix-env"},
            }
        },
        "selected_flake": {"path": "/fixture/flake", "host": "FixtureHost", "repository": repository_fixture},
        "lock": {},
        "input_snapshots": [],
        "overrides": ["--override-input", "localConfig", "path:/fixture/projection"],
        "target": {"derivation_output": "/nix/store/fixture-system", "closure": []},
        "live": {"profile": {"relationship": "aligned"}, "closure": []},
        "profile_policy": {"profile": "/fixture/profile", "live": "/fixture/live", "generation_glob": "/fixture/system-*-link"},
        "control_transactions": [],
        "evaluation_root": "/fixture/eval",
        "evaluation_current": {"exists": False, "link": None},
        "participants": [],
        "state_probes": [],
        "external_state": [],
        "created_at": "2026-08-25T00:00:00Z",
        "environment": {}, "allowed_lock_nodes": [], "control_snapshot": {},
        "machine_control_snapshot": machine_control, "control_projection": {},
        "projection": {"root": "/fixture/projection", "files": [], "digest": "projection", "published": False}, "checks": [], "host_candidates": [], "closure_delta": {"added": [], "removed": [], "unapproved": []},
        "participant_context": {"path": "/fixture/context.json", "digest": context_digest, "document": context}, "participant_preflight": [],
        "rollback_policy": {"pre_switch": "restore-all", "post_switch": "participant-local-reverse"},
        "confirmation": {"template": "APPLY NIX-UPDATE PLAN {plan_digest}"}, "observations": {}, "mutable_state_comparison": {},
        "command_evidence_policy": {"raw_limit_bytes": 8192, "summary_limit_bytes": 1024, "root": "/fixture/evidence"}, "publish_local_config": False,
    }


def rebind_participant_context(plan_module, document):
    context = dict(document["participant_context"]["document"])
    context.update({
        "selected_repository": document["selected_flake"]["repository"],
        "selected_flake": document["selected_flake"]["path"],
        "flake_lock": document["lock"],
        "host": document["selected_flake"]["host"],
        "environment": document["environment"],
        "target": {"output": document["target"]["derivation_output"], "closure": document["target"]["closure"]},
        "overrides": document["overrides"],
        "overrides_digest": plan_module.sha256_bytes(plan_module.canonical_bytes({"overrides": document["overrides"]})),
        "projection": document["projection"],
        "sources": document["input_snapshots"],
        "control_projection": document["control_projection"],
        "profile": document["live"]["profile"],
        "state_probes": document["state_probes"],
        "external_state": document["external_state"],
        "participant_declarations": document["participants"],
        "participant_declaration_digest": plan_module.sha256_bytes(
            plan_module.canonical_bytes({"participants": document["participants"]})
        ),
    })
    context.pop("plan_draft_identity", None)
    context["plan_draft_identity"] = plan_module.sha256_bytes(plan_module.canonical_bytes(context))
    document["participant_context"] = {
        "path": document["participant_context"]["path"],
        "digest": plan_module.sha256_bytes(plan_module.canonical_bytes(context)),
        "document": context,
    }


@pytest.mark.parametrize(("switch_status", "terminal", "expected_status"), [(0, "SUCCESS", 0), (23, "POST_INVOCATION_FAILURE", 1)])
def test_apply_marks_then_attempts_exactly_one_switch_and_never_optimizes(modules, tmp_path: Path, monkeypatch, switch_status, terminal, expected_status):
    state, _, plan, workflow_module, _ = modules
    artifact = plan.write_plan(tmp_path / "plan", plan_document(plan), "plan {PLAN_DIGEST}\n")
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(workflow_module, "_guard_processes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_target", lambda *_args: None)
    monkeypatch.setattr(workflow_module, "_profile_snapshot", lambda *_args: document["live"]["profile"])
    monkeypatch.setattr(workflow_module, "_verify_post_switch", lambda *_args: None)
    synced_directories = []
    original_fsync_directory = workflow_module.fsync_directory

    def track_directory_fsync(path):
        synced_directories.append(Path(path))
        return original_fsync_directory(path)

    monkeypatch.setattr(workflow_module, "fsync_directory", track_directory_fsync)
    events = []

    def runner(argv, **_kwargs):
        events.append(list(argv))
        assert list((artifact.directory / "results").rglob("switch-invocation.json"))
        return subprocess.CompletedProcess(argv, switch_status, "", "switch failed" if switch_status else "")

    workflow = workflow_module.Workflow(tmp_path, runner)
    status = workflow_module.apply(workflow, artifact.directory, f"APPLY NIX-UPDATE PLAN {artifact.digest}")

    assert status == expected_status
    assert sum(
        [Path(command[0]).name, command[1], Path(command[2]).name]
        == ["sudo", "HOMEBREW_NO_AUTO_UPDATE=1", "darwin-rebuild"]
        for command in events
    ) == 1
    assert not any("nix-opt" in item or "collect-garbage" in item for command in events for item in command)
    result = json.loads(next((artifact.directory / "results").rglob("result.json")).read_text())
    assert result["terminal_state"] == terminal
    assert result["switch_invocation_started"] is True
    assert ("operator_disposition_commands" in result) is (switch_status != 0)
    assert state_root / "consumed-authority" in synced_directories


def test_apply_revalidates_reviewed_published_projection_and_process_gate_before_switch(
    modules, tmp_path: Path, monkeypatch
):
    _, _, plan, workflow_module, _ = modules
    document = plan_document(plan)
    document.update(
        publish_local_config=True,
        evaluation_root=str(tmp_path / "eval"),
        projection={"root": str(tmp_path / "projection"), "digest": "reviewed", "files": [], "published": False},
        overrides=["--override-input", "localConfig", f"path:{tmp_path / 'projection'}"],
    )
    rebind_participant_context(plan, document)
    artifact = plan.write_plan(tmp_path / "plan", document, "plan\n")
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    guard_calls = []
    monkeypatch.setattr(workflow_module, "_guard_processes", lambda *_args, **_kwargs: guard_calls.append(True))
    revalidations = []
    monkeypatch.setattr(
        workflow_module, "_revalidate_plan",
        lambda *_args, **kwargs: revalidations.append(kwargs),
    )
    monkeypatch.setattr(workflow_module, "_revalidate_target", lambda *_args: None)
    monkeypatch.setattr(workflow_module, "_verify_post_switch", lambda *_args: None)
    current = {"value": workflow_module.CurrentSnapshot(False, None)}
    monkeypatch.setattr(workflow_module, "snapshot_current", lambda *_args: current["value"])

    def publish(_root, _eval_root):
        current["value"] = workflow_module.CurrentSnapshot(True, "generations/reviewed")
        return type("Publication", (), {"generation": Path("generations/reviewed")})()

    monkeypatch.setattr(workflow_module, "publish_scratch", publish)
    runner = lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "", "")

    assert workflow_module.apply(
        workflow_module.Workflow(tmp_path, runner), artifact.directory,
        f"APPLY NIX-UPDATE PLAN {artifact.digest}",
    ) == 0
    reviewed = workflow_module.CurrentSnapshot(True, "generations/reviewed")
    assert any(item.get("expected_evaluation_current") == reviewed for item in revalidations)
    assert len(guard_calls) == 3


def test_split_profile_realigns_with_sudo_and_restores_on_pre_switch_failure(
    modules, tmp_path: Path, monkeypatch
):
    _, _, plan, workflow_module, _ = modules
    document = plan_document(plan)
    reviewed = {
        "relationship": "split-retained",
        "selected": {"path": "/fixture/profile", "link": "system-188-link", "symlink_text": "system-188-link", "target": "/failed", "resolved_target": "/failed", "generation_path": "/fixture/system-188-link", "generation_symlink_text": "/failed", "generation_number": 188},
        "live": {"path": "/fixture/live", "link": "/safe", "target": "/safe"},
        "retained": [
            {"path": "/fixture/system-187-link", "link": "/safe", "target": "/safe", "generation": 187},
            {"path": "/fixture/system-188-link", "link": "/failed", "target": "/failed", "generation": 188},
        ],
    }
    realigned = {
        **reviewed,
        "relationship": "aligned",
        "selected": {"path": "/fixture/profile", "link": "system-187-link", "symlink_text": "system-187-link", "target": "/safe", "resolved_target": "/safe", "generation_path": "/fixture/system-187-link", "generation_symlink_text": "/safe", "generation_number": 187},
    }
    document["live"]["profile"] = reviewed
    document["profile_policy"] = {
        "profile": "/fixture/profile", "live": "/fixture/live",
        "generation_glob": "/fixture/system-*-link", "action": "realign-selected-profile",
        "safe_generation": 187,
    }
    rebind_participant_context(plan, document)
    artifact = plan.write_plan(tmp_path / "plan", document, "plan\n")
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(workflow_module, "_guard_processes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_target", lambda *_args: None)
    monkeypatch.setattr(
        workflow_module,
        "_safe_participant_phase",
        lambda _participants, phase, *_args, **_kwargs: ([], ["forced pre-switch failure"] if phase == "pre-switch" else []),
    )
    current = {"value": reviewed}
    monkeypatch.setattr(workflow_module, "_profile_snapshot", lambda *_args: current["value"])
    calls = []

    def runner(argv, **_kwargs):
        calls.append(list(argv))
        generation = int(argv[-1])
        current["value"] = realigned if generation == 187 else reviewed
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert workflow_module.apply(
        workflow_module.Workflow(tmp_path, runner), artifact.directory,
        f"APPLY NIX-UPDATE PLAN {artifact.digest}",
    ) == 1
    assert [command[:2] for command in calls] == [["sudo", "nix-env"], ["sudo", "nix-env"]], calls
    assert [command[-1] for command in calls] == ["187", "188"]
    assert current["value"] == reviewed
    result = json.loads(next((artifact.directory / "results").rglob("result.json")).read_text())
    assert result["terminal_state"] == "FAILED_PRE_SWITCH"
    assert any(event["event"] == "selected-profile-restored" for event in result["events"])


def test_profile_realignment_rejects_different_generation_with_same_target(modules):
    _, _, _, workflow_module, paths = modules
    reviewed = {
        "retained": [
            {"path": "/fixture/system-186-link", "link": "/safe", "target": "/safe", "generation": 186},
            {"path": "/fixture/system-187-link", "link": "/safe", "target": "/safe", "generation": 187},
        ]
    }
    wrong = {
        "selected": {
            "generation_number": 186,
            "generation_path": "/fixture/system-186-link",
            "generation_symlink_text": "/safe",
            "resolved_target": "/safe",
        }
    }
    with pytest.raises(paths.ConfigError, match="exact reviewed safe generation"):
        workflow_module._validate_profile_realignment(reviewed, wrong, 187)


def test_confirmation_digest_mismatch_has_no_mutation_or_bypass(modules, tmp_path: Path, monkeypatch):
    _, _, plan, workflow_module, _ = modules
    artifact = plan.write_plan(tmp_path / "plan", plan_document(plan), "plan\n")
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(workflow_module, "_guard_processes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_target", lambda *_args: None)
    attempted = []
    workflow = workflow_module.Workflow(tmp_path, lambda argv, **_kwargs: attempted.append(argv))

    assert workflow_module.apply(workflow, artifact.directory, "yes") == 1
    assert attempted == []
    result = json.loads(next((artifact.directory / "results").rglob("result.json")).read_text())
    assert "typed confirmation" in result["failure"]


def test_preflight_plan_is_structurally_unapplicable(modules, tmp_path: Path):
    _, _, plan, workflow_module, _ = modules
    document = plan_document(plan)
    document["preflight_only"] = True
    artifact = plan.write_plan(tmp_path / "plan", document, "plan\n")

    assert workflow_module.apply(workflow_module.Workflow(tmp_path), artifact.directory, "unused") == 1
    result = json.loads(next((artifact.directory / "results").rglob("result.json")).read_text())
    assert "preflight-only" in result["failure"]


def test_plan_switch_marker_prevents_replay_and_second_switch(modules, tmp_path: Path, monkeypatch):
    _, _, plan, workflow_module, _ = modules
    artifact = plan.write_plan(tmp_path / "plan", plan_document(plan), "plan\n")
    marker = artifact.directory / "results/first/switch-invocation.json"
    marker.parent.mkdir(parents=True)
    (artifact.directory / "results").chmod(0o700)
    marker.parent.chmod(0o700)
    marker.write_text("{}\n")
    marker.chmod(0o600)
    attempted = []

    status = workflow_module.apply(
        workflow_module.Workflow(tmp_path, lambda argv, **kwargs: attempted.append(argv)),
        artifact.directory,
        f"APPLY NIX-UPDATE PLAN {artifact.digest}",
    )
    assert status == 1
    assert attempted == []
    results = sorted((artifact.directory / "results").rglob("result.json"))
    assert json.loads(results[-1].read_text())["terminal_state"] == "REPLAY_BLOCKED"


def test_plan_pre_switch_mutation_marker_prevents_replay(modules, tmp_path: Path):
    _, _, plan, workflow_module, _ = modules
    artifact = plan.write_plan(tmp_path / "plan", plan_document(plan), "plan\n")
    marker = artifact.directory / "results/first/pre-switch-mutation.json"
    marker.parent.mkdir(parents=True)
    (artifact.directory / "results").chmod(0o700)
    marker.parent.chmod(0o700)
    marker.write_text("{}\n")
    marker.chmod(0o600)
    attempted = []

    assert workflow_module.apply(
        workflow_module.Workflow(tmp_path, lambda argv, **kwargs: attempted.append(argv)),
        artifact.directory,
        f"APPLY NIX-UPDATE PLAN {artifact.digest}",
    ) == 1
    assert attempted == []


def test_replay_marker_created_after_confirmation_is_rechecked_under_mutation_lock(
    modules, tmp_path: Path, monkeypatch
):
    _, _, plan, workflow_module, _ = modules
    artifact = plan.write_plan(tmp_path / "plan", plan_document(plan), "plan\n")
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(workflow_module, "_guard_processes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_target", lambda *_args: None)
    calls = {"count": 0}

    def markers(_artifact, _state_root=None):
        calls["count"] += 1
        return [] if calls["count"] == 1 else [tmp_path / "concurrent-marker"]

    monkeypatch.setattr(workflow_module, "_prior_apply_markers", markers)
    attempted = []
    assert workflow_module.apply(
        workflow_module.Workflow(tmp_path, lambda argv, **kwargs: attempted.append(argv)),
        artifact.directory,
        f"APPLY NIX-UPDATE PLAN {artifact.digest}",
    ) == 1
    assert attempted == []
    result = json.loads(next((artifact.directory / "results").rglob("result.json")).read_text())
    assert result["terminal_state"] == "REPLAY_BLOCKED"


def write_fixture_lock(path: Path, nar="sha256-source="):
    path.write_text(json.dumps({
        "nodes": {
            "root": {"inputs": {"source": "source"}},
            "source": {"flake": False, "locked": {"type": "path", "path": "/fixture/source", "narHash": nar}, "original": {"type": "path", "path": "/fixture/source"}},
        },
        "root": "root",
        "version": 7,
    }))


def initialize_git_fixture(repository: Path, tmp_path: Path) -> Path:
    remote = tmp_path / f"{repository.name}-origin.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "init", "-b", "main"], cwd=repository, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "add", "-A"], cwd=repository, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Nix Update Test", "-c", "user.email=nix-update@example.invalid", "commit", "-m", "fixture"],
        cwd=repository, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repository, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=repository, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return remote


def test_exact_source_binding_uses_actual_input_path_and_rejects_archive_nar_substitution(
    modules, tmp_path: Path, monkeypatch
):
    _, _, _, workflow_module, paths = modules
    source = tmp_path / "security-source"
    source.mkdir()
    specification = {
        "name": "security",
        "input": "security",
        "lock_node": "security",
        "kind": "immutable-snapshot",
        "path": str(source),
        "nar_hash": "sha256-ox1mfzrczX4dN38Ak7kdF2l7PnY9CGhFiqVxpkk4hGw=",
    }
    monkeypatch.setattr(workflow_module, "_require_exact_store_path", lambda *_args: None)
    overrides = workflow_module._bind_source_overrides([], [specification])
    assert overrides == ["--override-input", "security", f"path:{source}"]

    def archive_semantics(argv, **_kwargs):
        return subprocess.CompletedProcess(
            argv, 0, "sha256-2AByg60sNDfJD+wkPMr26rMy+KsrRx6uP2D3fXZG1oo=\n", ""
        )
    with pytest.raises(paths.ConfigError, match="source NAR mismatch"):
        workflow_module._source_identity(
            workflow_module.Workflow(tmp_path, archive_semantics), tmp_path, specification
        )


@pytest.mark.parametrize("defect", ["nar", "node", "input"])
def test_source_contract_is_cross_bound_to_exact_lock_node(modules, tmp_path: Path, defect):
    _, _, plan, workflow_module, paths = modules
    lock_path = tmp_path / "flake.lock"
    write_fixture_lock(lock_path)
    lock = plan.normalized_lock(lock_path)
    source = {
        "name": "source", "input": "source", "lock_node": "source",
        "nar_hash": "sha256-source=",
    }
    if defect == "nar": source["nar_hash"] = "sha256-other="
    elif defect == "node": source["lock_node"] = "missing"
    else: source["input"] = "missing"
    with pytest.raises(paths.ConfigError):
        workflow_module._validate_source_lock_bindings(lock, [source])


def test_source_contract_accepts_followed_lock_input_path(modules, tmp_path: Path):
    _, _, plan, workflow_module, _ = modules
    lock_path = tmp_path / "flake.lock"
    write_lock_document = {
        "nodes": {
            "root": {"inputs": {"alias": ["source"], "source": "source"}},
            "source": {"flake": False, "locked": {"narHash": "sha256-source="}},
        },
        "root": "root", "version": 7,
    }
    lock_path.write_text(json.dumps(write_lock_document))
    workflow_module._validate_source_lock_bindings(
        plan.normalized_lock(lock_path),
        [{"name": "source", "input": "alias", "lock_node": "source", "nar_hash": "sha256-source="}],
    )

def test_exact_source_rejects_mutable_path_before_hash_or_build(modules, tmp_path: Path):
    _, _, _, workflow_module, paths = modules
    attempted = []
    with pytest.raises(paths.ConfigError, match="immutable Nix store path"):
        workflow_module._source_identity(
            workflow_module.Workflow(tmp_path, lambda argv, **kwargs: attempted.append(argv)),
            tmp_path,
            {
                "name": "mutable", "input": "mutable", "lock_node": "mutable", "kind": "immutable-snapshot",
                "path": str(tmp_path), "nar_hash": "sha256-example=",
            },
        )
    assert attempted == []


@pytest.mark.parametrize("defect", ["undeclared", "mismatch", "duplicate", "shape"])
def test_exact_source_override_binding_fails_closed(modules, tmp_path: Path, monkeypatch, defect):
    _, _, _, workflow_module, paths = modules
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(workflow_module, "_require_exact_store_path", lambda *_args: None)
    specification = {"input": "source", "path": str(source)}
    overrides = []
    if defect == "undeclared":
        overrides = ["--override-input", "other", f"path:{source}"]
    elif defect == "mismatch":
        other = tmp_path / "other"
        other.mkdir()
        overrides = ["--override-input", "source", f"path:{other}"]
    elif defect == "duplicate":
        specification = [specification, dict(specification)]
    else:
        overrides = ["--impure"]
    sources = specification if isinstance(specification, list) else [specification]
    with pytest.raises(paths.ConfigError):
        workflow_module._bind_source_overrides(overrides, sources)


def test_tool_identity_binds_source_and_resolved_external_executables(modules, tmp_path: Path, monkeypatch):
    _, _, _, workflow_module, _ = modules
    core = tmp_path / "core"
    (core / "bin").mkdir(parents=True)
    (core / "libexec/nix_darwin_local").mkdir(parents=True)
    for path in (core / "bin/nix-update", core / "bin/nix-rebuild", core / "libexec/nix_darwin_local/module.py"):
        path.write_text("source\n")
        path.chmod(0o700)
    monkeypatch.setattr(workflow_module.shutil, "which", lambda name: f"/tools/{name}")
    monkeypatch.setattr(
        workflow_module,
        "_executable_identity",
        lambda path, **_kwargs: {
            "path": str(path), "resolved": str(path), "identity": {"kind": "regular"}
        },
    )

    identity = workflow_module._tool_identity(core)
    assert set(identity["external"]) == {
        "git", "nix", "nix-env", "sudo", "darwin-rebuild", "darwin-rebuild-upstream"
    }
    assert len(identity["digest"]) == 64


def test_git_identity_rejects_and_redacts_credential_bearing_remote(modules, tmp_path: Path):
    from nix_darwin_local import repository_identity
    _, _, _, workflow_module, paths = modules

    def runner(argv, **_kwargs):
        if argv[:3] == ["git", "config", "--get"]:
            value = "refs/heads/main\n" if argv[-1].endswith(".merge") else "origin\n"
            return subprocess.CompletedProcess(argv, 0, value, "")
        if argv[:3] == ["git", "remote", "get-url"]:
            return subprocess.CompletedProcess(argv, 0, "https://user:pass@example.invalid/repo\n", "")
        return subprocess.CompletedProcess(argv, 0, "value\n", "")

    workflow = workflow_module.Workflow(tmp_path, runner)
    with pytest.raises(paths.ConfigError, match="credential-bearing"):
        repository_identity.publication_git_identity(workflow, tmp_path, {"branch": "main", "commit": "a" * 40})
    remote_record = next(record for record in workflow.records if record.label == "Git remote")
    assert "pass" not in remote_record.stdout["summary"]
    assert "[REDACTED]" in remote_record.stdout["summary"]


@pytest.mark.parametrize("defect", ["relative", "missing", "nonexec"])
def test_executable_identity_rejects_unbound_tools(modules, tmp_path: Path, defect):
    _, _, _, workflow_module, paths = modules
    executable = tmp_path / "tool"
    if defect != "missing":
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o600 if defect == "nonexec" else 0o700)
    candidate = Path("tool") if defect == "relative" else executable
    with pytest.raises(paths.ConfigError, match="missing or not executable"):
        workflow_module._executable_identity(candidate)


def test_executable_identity_records_resolved_regular_bytes(modules, tmp_path: Path):
    _, _, _, workflow_module, _ = modules
    executable = tmp_path / "tool"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)
    identity = workflow_module._executable_identity(executable)
    assert identity["path"] == str(executable)
    assert identity["identity"]["sha256"] == hashlib.sha256(executable.read_bytes()).hexdigest()


def test_executable_identity_uses_declared_probe_when_root_owned_bytes_are_unreadable(
    modules, tmp_path: Path, monkeypatch
):
    _, _, _, workflow_module, _ = modules
    executable = tmp_path / "tool"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)
    monkeypatch.setattr(
        workflow_module,
        "file_identity",
        lambda _path: (_ for _ in ()).throw(PermissionError("injected unreadable executable")),
    )
    observed = []
    monkeypatch.setattr(
        workflow_module,
        "_unreadable_executable_identity",
        lambda path, probe: observed.append((path, probe)) or {"content": "probe-bound"},
    )

    identity = workflow_module._executable_identity(executable, unreadable_probe=("--version",))
    assert identity["identity"] == {"content": "probe-bound"}
    assert observed == [(executable.resolve(), ("--version",))]


def test_unreadable_executable_identity_binds_root_owned_metadata_and_probe(modules, monkeypatch):
    _, _, _, workflow_module, _ = modules
    metadata = type(
        "Metadata",
        (),
        {
            "st_mode": stat.S_IFREG | 0o4511,
            "st_uid": 0,
            "st_gid": 0,
            "st_nlink": 1,
            "st_size": 123,
            "st_mtime_ns": 456,
            "st_ctime_ns": 789,
            "st_dev": 10,
            "st_ino": 11,
        },
    )()
    monkeypatch.setattr(Path, "lstat", lambda _self: metadata)
    monkeypatch.setattr(
        workflow_module.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, b"version\n", b""),
    )

    identity = workflow_module._unreadable_executable_identity(Path("/usr/bin/sudo"), ("--version",))
    assert identity["content"] == "unreadable-root-owned-executable"
    assert identity["mode"] == 0o4511
    assert identity["size"] == 123
    assert identity["probe"] == ["--version"]
    assert identity["probe_stdout_sha256"] == hashlib.sha256(b"version\n").hexdigest()


@pytest.mark.parametrize(("uid", "mode"), [(501, 0o0111), (0, 0o0711)])
def test_unreadable_executable_identity_rejects_mutable_or_nonroot_tools(
    modules, monkeypatch, uid, mode
):
    _, _, _, workflow_module, paths = modules
    metadata = type(
        "Metadata",
        (),
        {
            "st_mode": stat.S_IFREG | mode,
            "st_uid": uid,
            "st_gid": 0,
            "st_nlink": 1,
            "st_size": 123,
            "st_mtime_ns": 456,
            "st_ctime_ns": 789,
            "st_dev": 10,
            "st_ino": 11,
        },
    )()
    monkeypatch.setattr(Path, "lstat", lambda _self: metadata)

    with pytest.raises(paths.ConfigError, match="immutable root-owned regular file"):
        workflow_module._unreadable_executable_identity(Path("/usr/bin/sudo"), ("--version",))


def prepare_fixture(modules, tmp_path: Path, monkeypatch, *, failures=()):
    _, _, plan, workflow_module, paths = modules
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "flake.nix").write_text("{ outputs = { self }: { }; }\n")
    write_fixture_lock(selected / "flake.lock")
    (selected / "bin").mkdir()
    (selected / "bin/nix-test").write_text("#!/bin/sh\nexit 0\n")
    (selected / "bin/nix-test").chmod(0o600 if "nonexec-test" in failures else 0o700)
    control = tmp_path / "control"
    control.mkdir(mode=0o700)
    apgr = control / "apgr.toml"
    apgr.write_bytes(b'agents = ["codex", "claude"]\n')
    apgr.chmod(0o644)
    transaction = control_spec(apgr)
    source = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-fixture-source"
    probe = tmp_path / "skills"
    (probe / ".system").mkdir(parents=True)
    marker = probe / ".system/.codex-system-skills.marker"
    marker.write_text("managed\n")
    marker.chmod(0o600)
    marker_metadata = marker.lstat()
    volatile = [{
        "path": ".system/.codex-system-skills.marker", "kind": "regular",
        "uid": marker_metadata.st_uid, "gid": marker_metadata.st_gid, "mode": 0o600,
        "producer": "runtime-system-skills",
        "content_policy": {"sha256": hashlib.sha256(b"managed\n").hexdigest()},
    }]
    external = tmp_path / "external"
    external.write_text("external")
    profile, live, generation_glob, targets = make_profiles(tmp_path / "system-state", 188, 187)
    target = "/nix/store/fixture-target-system"
    live_target = str(targets[187])
    participant = tmp_path / "jaca-participant"
    participant.write_text(
        f"#!{Path(sys.executable).resolve()}\n"
        "import hashlib,json,sys\n"
        "from pathlib import Path\n"
        "a=sys.argv; phase=a[a.index('--phase')+1]; context=Path(a[a.index('--context')+1])\n"
        "doc=json.loads(context.read_text()); tx=doc['control_projection']['transactions'][0]\n"
        "ok=doc['target']['output']=='/nix/store/fixture-target-system' and tx['projected_postimage']['sha256']==hashlib.sha256(b'agents = []\\n').hexdigest()\n"
        "out={'blockers':[] if ok else ['wrong target or control projection'],'context_digest':hashlib.sha256(context.read_bytes()).hexdigest(),'name':'resident-reconciler','observations':{'target':doc['target']['output']},'phase':phase,'protocol':'nix-update-participant-v1','status':'ok' if ok else 'blocked'}\n"
        "print(json.dumps(out,sort_keys=True,separators=(',',':'))); sys.exit(0 if ok else 20)\n"
    )
    participant.chmod(0o700)
    participant_digest = hashlib.sha256(participant.read_bytes()).hexdigest()
    contract = {
        "schema": plan.CONTRACT_SCHEMA,
        "additional_hosts": ["ExtraHost"],
        "allowed_lock_nodes": [],
        "allowed_closure_delta": [target, live_target],
        "sources": [{"name": "source", "input": "source", "lock_node": "source", "kind": "immutable-snapshot", "path": source, "nar_hash": "sha256-source="}],
        "state_probes": [{"name": "codex-skills", "kind": "tree", "path": str(probe), "volatile": volatile}],
        "external_state": [{"name": "external-file", "kind": "file", "path": str(external)}],
        "profile_policy": {
            "profile": str(profile), "live": str(live), "generation_glob": str(generation_glob),
            "action": "realign-selected-profile", "safe_generation": 187,
        },
        "control_root": str(control),
        "control_transactions": [transaction],
        "participants": [{
            "name": "resident-reconciler", "protocol": "nix-update-participant-v1",
            "path": str(participant), "sha256": participant_digest, "phases": ["preflight"],
            "mutation_authority": [], "rollback_authority": [], "timeout_seconds": 30,
            "provenance": {
                "kind": "operator-artifact", "identity": f"sha256:{participant_digest}",
                "root": str(tmp_path),
            },
        }],
        "rollback_policy": {"pre_switch": "restore-all", "post_switch": "participant-local-reverse"},
        "publish_local_config": True,
        "allowed_closure_delta": [target, live_target],
    }
    if "additional-build" in failures:
        contract["additional_hosts"] = ["ExtraHost"]
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract, sort_keys=True, separators=(",", ":")) + "\n")
    state_root = tmp_path / "workflow-state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(workflow_module, "_require_exact_store_path", lambda *_args: None)
    monkeypatch.setattr(
        workflow_module,
        "_tool_identity",
        lambda core: {
            "name": "nix-update", "version": "1", "core": str(Path(core).resolve()),
            "files": [], "digest": "0" * 64,
            "external": {
                "sudo": {"path": "sudo"}, "nix-env": {"path": "nix-env"},
                "darwin-rebuild": {"path": "darwin-rebuild"},
            },
        },
    )
    controls = {
        "target_build_count": 0, "fail_target_build_at": None, "switch_status": 9,
        "process_drift": False, "after_target_build": None,
    }
    def guard_processes(blockers, strict, **_kwargs):
        if "process" not in failures and not controls["process_drift"]:
            return
        blockers.append("competing process")
        if strict:
            raise paths.ConfigError("competing process")

    monkeypatch.setattr(workflow_module, "_guard_processes", guard_processes)
    calls = []

    def assert_projected_override(argv):
        effective = [
            argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "--flake-option"
        ] if "--flake-option" in argv else list(argv)
        indices = [index for index, value in enumerate(effective) if value == "--override-input"]
        local = [index for index in indices if effective[index + 1] == "localConfig"]
        assert len(local) == 1
        projection = Path(str(effective[local[0] + 2]).removeprefix("path:"))
        assert (projection / "apgr.toml").read_text() == "agents = []\n"
        assert "--agent" not in (projection / "apgr.toml").read_text()

    def runner(argv, cwd=None, **_kwargs):
        calls.append(list(argv))
        label = " ".join(str(item) for item in argv)
        if "--protocol" in argv and "nix-update-participant-v1" in argv:
            return subprocess.run(argv, cwd=cwd, **_kwargs)
        if argv[:2] == ["git", "rev-parse"] and argv[-1] == "HEAD":
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\n", "")
        if argv[:2] == ["git", "rev-parse"] and argv[-1] == "HEAD^{tree}":
            return subprocess.CompletedProcess(argv, 0, "b" * 40 + "\n", "")
        if argv[:2] == ["git", "branch"]:
            return subprocess.CompletedProcess(argv, 0, "main\n", "")
        if argv[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return subprocess.CompletedProcess(argv, 0, "origin/main\n", "")
        if argv[:3] == ["git", "config", "--get"]:
            value = "refs/heads/main\n" if argv[-1].endswith(".merge") else "origin\n"
            return subprocess.CompletedProcess(argv, 0, value, "")
        if argv[:3] == ["git", "remote", "get-url"]:
            return subprocess.CompletedProcess(argv, 0, "git@example.invalid:owner/repo.git\n", "")
        if argv[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(argv, 0, "dirty\n" if "git" in failures else "", "")
        if argv[:2] == ["git", "rev-list"]:
            return subprocess.CompletedProcess(argv, 0, "0\t0\n", "")
        if argv[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return subprocess.CompletedProcess(argv, 0, str(selected) + "\n", "")
        if argv[:2] == ["git", "ls-files"]:
            return subprocess.CompletedProcess(argv, 0, "100644 " + "c" * 40 + " 0\tflake.nix\n", "")
        if argv[:3] == ["git", "rev-parse", "--git-path"]:
            return subprocess.CompletedProcess(argv, 0, str(selected / ".git" / argv[-1]) + "\n", "")
        if argv[:2] == ["git", "ls-remote"]:
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\trefs/heads/main\n", "")
        if argv[:2] == ["nix", "eval"]:
            return subprocess.CompletedProcess(
                argv, 1 if "allowlist" in failures else 0,
                json.dumps({"apgr.toml": ["agents"]}) + "\n",
                "eval failed" if "allowlist" in failures else "",
            )
        if argv[:3] == ["nix", "hash", "path"]:
            return subprocess.CompletedProcess(argv, 1 if "source" in failures else 0, "sha256-source=\n", "")
        if argv and str(argv[0]).endswith("bin/nix-test"):
            if "allowlist" not in failures:
                assert_projected_override(argv)
            return subprocess.CompletedProcess(argv, 7 if "test" in failures else 0, "tests\n", "")
        if argv[:3] == ["nix", "flake", "check"]:
            if "localConfig" in argv:
                assert_projected_override(argv)
            return subprocess.CompletedProcess(
                argv, 6 if "projected" in failures else 0, "projected check\n", "projected failed"
            )
        if argv[:2] == ["nix", "build"]:
            if "localConfig" in argv:
                assert_projected_override(argv)
            if "FixtureHost" in label:
                controls["target_build_count"] += 1
                if controls["target_build_count"] == controls["fail_target_build_at"]:
                    return subprocess.CompletedProcess(argv, 8, "", "injected target drift")
                if controls["after_target_build"] is not None:
                    controls["after_target_build"](controls["target_build_count"])
            failed = "build" in failures or ("additional-build" in failures and "ExtraHost" in label)
            output = "not-a-store-path\n" if "build-output" in failures else target + "\n"
            return subprocess.CompletedProcess(argv, 8 if failed else 0, output, "build failed" if failed else "")
        if argv[:2] == ["nix", "path-info"]:
            path = argv[-1]
            return subprocess.CompletedProcess(argv, 0, json.dumps([{"path": path, "narHash": "sha256-closure=", "narSize": 1}]), "")
        if argv[:2] == ["sudo", "nix-env"]:
            generation = int(argv[-1])
            profile.unlink()
            profile.symlink_to(Path(generation_glob).parent / f"system-{generation}-link")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["sudo", "HOMEBREW_NO_AUTO_UPDATE=1", "darwin-rebuild"]:
            assert_projected_override(argv)
            return subprocess.CompletedProcess(
                argv, controls["switch_status"], "",
                "injected switch failure" if controls["switch_status"] else "",
            )
        raise AssertionError(f"unexpected command: {label}")

    workflow = workflow_module.Workflow(tmp_path, runner)
    artifact = workflow_module.prepare(
        workflow, selected, "FixtureHost", control, contract_path, tmp_path / "plan", [], "2", "3", "preflight" in failures
    )
    return artifact, calls, selected, contract_path, target, workflow, controls


def test_prepare_collects_exact_plan_without_live_projection_or_mutation(modules, tmp_path: Path, monkeypatch):
    artifact, calls, selected, _, target, _, _ = prepare_fixture(modules, tmp_path, monkeypatch)

    assert artifact.document["status"] == "READY_FOR_REVIEW"
    assert artifact.document["target"]["derivation_output"] == target
    assert artifact.document["projection"]["published"] is False
    assert len(artifact.document["host_candidates"]) == 2
    assert artifact.document["live"]["profile"]["relationship"] == "split-retained"
    assert artifact.document["participants"][0]["name"] == "resident-reconciler"
    assert artifact.document["participant_preflight"][0]["context_digest"] == artifact.document["participant_context"]["digest"]
    control_path = Path(artifact.document["control_transactions"][0]["path"])
    assert control_path.read_bytes() == b'agents = ["codex", "claude"]\n'
    assert control_path.stat().st_mode & 0o777 == 0o644
    projected = Path(artifact.document["projection"]["root"]) / "apgr.toml"
    assert projected.read_text() == "agents = []\n"
    assert "--agent" not in projected.read_text()
    assert not (tmp_path / "eval/current").exists()
    assert (selected / "flake.lock").is_file()
    assert any("--report" in command for command in calls)
    assert any("--no-update-lock-file" in command and "--no-write-lock-file" in command for command in calls)


def test_coupled_prepare_apply_uses_one_postimage_projection_and_restores_before_switch(
    modules, tmp_path: Path, monkeypatch
):
    _, _, _, workflow_module, _ = modules
    artifact, calls, _, _, _, workflow, controls = prepare_fixture(modules, tmp_path, monkeypatch)
    controls["target_build_count"] = 0
    controls["fail_target_build_at"] = 3
    control_path = Path(artifact.document["control_transactions"][0]["path"])
    profile_path = Path(artifact.document["profile_policy"]["profile"])
    reviewed_profile_link = os.readlink(profile_path)
    retained_projection = workflow_module.inspect_scratch(Path(artifact.document["projection"]["root"]))
    assert [list(item) for item in retained_projection.files] == artifact.document["projection"]["files"], (
        retained_projection.files, artifact.document["projection"]["files"]
    )
    assert retained_projection.digest == artifact.document["projection"]["digest"]

    assert workflow_module.apply(
        workflow, artifact.directory, f"APPLY NIX-UPDATE PLAN {artifact.digest}"
    ) == 1

    result = json.loads(next((artifact.directory / "results").rglob("result.json")).read_text())
    assert result["terminal_state"] == "FAILED_PRE_SWITCH"
    assert control_path.read_text() == 'agents = ["codex", "claude"]\n'
    assert control_path.stat().st_mode & 0o777 == 0o644
    assert os.readlink(profile_path) == reviewed_profile_link
    assert not (Path(artifact.document["evaluation_root"]) / "current").exists()
    assert not any(command[:3] == ["sudo", "HOMEBREW_NO_AUTO_UPDATE=1", "darwin-rebuild"] for command in calls)
    projection_locators = []
    for command in calls:
        for index, value in enumerate(command):
            if value == "--override-input" and command[index + 1] == "localConfig":
                projection_locators.append(command[index + 2])
    assert projection_locators
    assert set(projection_locators) == {f"path:{artifact.document['projection']['root']}"}
    assert all(
        "--override-input" in command and "source" in command
        for command in calls if command[:2] == ["nix", "build"]
    )
    assert any(event["event"] == "control-preimage-restored" for event in result["events"]), result
    assert any(event["event"] == "local-config-current-restored" for event in result["events"])


@pytest.mark.parametrize("drift", ["state", "process"])
def test_apply_catches_drift_injected_by_last_target_rebuild_before_system_privilege(
    modules, tmp_path: Path, monkeypatch, drift: str,
):
    _, _, _, workflow_module, _ = modules
    artifact, calls, _, _, _, workflow, controls = prepare_fixture(modules, tmp_path, monkeypatch)
    controls["target_build_count"] = 0
    controls["switch_status"] = 0
    monkeypatch.setattr(workflow_module, "_verify_post_switch", lambda *_args: None)
    probe_root = Path(artifact.document["state_probes"][0]["policy"]["path"])

    def inject(build_count: int) -> None:
        if build_count != 3:
            return
        if drift == "state":
            extra = probe_root / "undeclared-drift"
            extra.write_text("drift\n")
            extra.chmod(0o600)
        else:
            controls["process_drift"] = True

    controls["after_target_build"] = inject
    assert workflow_module.apply(
        workflow, artifact.directory, f"APPLY NIX-UPDATE PLAN {artifact.digest}"
    ) == 1
    result = json.loads(next((artifact.directory / "results").rglob("result.json")).read_text())
    assert controls["target_build_count"] == 3
    assert result["terminal_state"] == "FAILED_PRE_SWITCH"
    assert not any(command[:3] == ["sudo", "HOMEBREW_NO_AUTO_UPDATE=1", "darwin-rebuild"] for command in calls)
    assert "drift" in result["failure"] or "competing process" in result["failure"]


def test_apply_event_log_proves_immediate_final_attestation_to_each_privileged_command(
    modules, tmp_path: Path, monkeypatch,
):
    _, _, _, workflow_module, _ = modules
    artifact, calls, _, _, _, workflow, controls = prepare_fixture(modules, tmp_path, monkeypatch)
    controls["target_build_count"] = 0
    controls["switch_status"] = 0
    monkeypatch.setattr(workflow_module, "_verify_post_switch", lambda *_args: None)
    status = workflow_module.apply(
        workflow, artifact.directory, f"APPLY NIX-UPDATE PLAN {artifact.digest}"
    )
    result = json.loads(next((artifact.directory / "results").rglob("result.json")).read_text())
    assert status == 0, result.get("failure")
    expected = [
        "final-mutable-state-recheck-started", "final-mutable-state-recheck-passed",
        "final-process-gate-passed", "final-participant-attestation-passed",
        "privilege-marker-written", "privileged-command-invoked",
    ]
    for transition in ("selected-profile-realignment", "system-switch"):
        assert [
            event["event"] for event in result["events"] if event.get("transition") == transition
        ] == expected
    switch_index = next(
        index for index, argv in enumerate(calls)
        if argv[:3] == ["sudo", "HOMEBREW_NO_AUTO_UPDATE=1", "darwin-rebuild"]
    )
    final_preflight = max(
        index for index, argv in enumerate(calls[:switch_index])
        if "--phase" in argv and argv[argv.index("--phase") + 1] == "preflight"
    )
    assert calls[final_preflight + 1:switch_index] == []
    assert sum(
        argv[:3] == ["sudo", "HOMEBREW_NO_AUTO_UPDATE=1", "darwin-rebuild"]
        for argv in calls
    ) == 1


@pytest.mark.parametrize(
    ("scenario", "terminal", "status", "rollback_order"),
    [
        ("resident-exact", "SUCCESS", 0, []),
        ("replacement-started", "SUCCESS", 0, []),
        ("stop-failure", "POST_SWITCH_RESTORED", 1, []),
        ("start-failure-rolled-back", "POST_SWITCH_RESTORED", 1, ["first"]),
        ("later-participant-failure", "POST_SWITCH_RESTORED", 1, ["second", "first"]),
        ("rollback-failure", "POST_SWITCH_RESTORE_INCOMPLETE", 1, ["first"]),
        ("malformed-after-mutation", "POST_SWITCH_PARTICIPANT_INDETERMINATE", 1, []),
        ("timeout-after-mutation", "POST_SWITCH_PARTICIPANT_INDETERMINATE", 1, []),
        ("exit-mismatch-after-mutation", "POST_SWITCH_PARTICIPANT_INDETERMINATE", 1, []),
        ("post-switch-verify-failure", "POST_SWITCH_RESTORED", 1, ["first"]),
    ],
)
def test_apply_enforces_post_switch_participant_rollback_protocol(
    modules, tmp_path: Path, monkeypatch, scenario: str, terminal: str,
    status: int, rollback_order: list[str],
):
    _, _, plan, workflow_module, _ = modules
    participant_names = ["first", "second"] if scenario == "later-participant-failure" else ["first"]
    specifications = []
    name_by_path = {}
    for name in participant_names:
        executable = tmp_path / f"{name}-participant"
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o700)
        specification = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())
        specification["name"] = name
        specification["phases"] = ["preflight", "post-switch-reconcile", "post-switch-verify", "rollback"]
        specifications.append(specification)
        name_by_path[str(executable)] = name
    document = plan_document(plan)
    document["participants"] = specifications
    rebind_participant_context(plan, document)
    artifact = plan.write_plan(tmp_path / "plan", document, "plan\n")
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(workflow_module, "_guard_processes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_target", lambda *_args: None)
    monkeypatch.setattr(workflow_module, "_profile_snapshot", lambda *_args: document["live"]["profile"])
    monkeypatch.setattr(workflow_module, "_verify_post_switch", lambda *_args: None)
    calls = []
    rollbacks = []

    def output(argv, name, phase, result_status="ok", blockers=None, handle=None, mutation_state=None):
        context_digest = participant_context_digest(argv)
        value = json.loads(participant_output(
            phase, result_status, blockers or [], handle, context_digest,
        ))
        value["name"] = name
        if phase == "post-switch-reconcile":
            value["mutation_state"] = mutation_state
        return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"

    def runner(argv, **_kwargs):
        calls.append(list(argv))
        if "--phase" not in argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        phase = argv[argv.index("--phase") + 1]
        name = name_by_path[str(argv[0])]
        if phase == "preflight":
            return subprocess.CompletedProcess(argv, 0, output(argv, name, phase), "")
        if phase == "post-switch-reconcile":
            if scenario == "resident-exact":
                return subprocess.CompletedProcess(argv, 0, output(argv, name, phase, mutation_state="unchanged"), "")
            if scenario == "stop-failure":
                return subprocess.CompletedProcess(
                    argv, 21, output(argv, name, phase, "failed", ["stop failed"], mutation_state="unchanged"), "",
                )
            if scenario == "start-failure-rolled-back":
                return subprocess.CompletedProcess(
                    argv, 21, output(argv, name, phase, "failed", ["start failed"], "restore-first", "partial"), "",
                )
            if scenario == "later-participant-failure" and name == "second":
                return subprocess.CompletedProcess(
                    argv, 21, output(argv, name, phase, "failed", ["second failed"], "restore-second", "partial"), "",
                )
            if scenario == "malformed-after-mutation":
                return subprocess.CompletedProcess(argv, 0, "not-json\n", "")
            if scenario == "timeout-after-mutation":
                raise subprocess.TimeoutExpired(argv, 30)
            if scenario == "exit-mismatch-after-mutation":
                return subprocess.CompletedProcess(
                    argv, 21, output(argv, name, phase, mutation_state="unchanged"), "",
                )
            return subprocess.CompletedProcess(
                argv, 0, output(argv, name, phase, handle=f"restore-{name}", mutation_state="mutated"), "",
            )
        if phase == "post-switch-verify":
            if scenario in {"post-switch-verify-failure", "rollback-failure"}:
                return subprocess.CompletedProcess(
                    argv, 21, output(argv, name, phase, "failed", ["verification failed"]), "",
                )
            return subprocess.CompletedProcess(argv, 0, output(argv, name, phase), "")
        assert phase == "rollback"
        rollbacks.append(name)
        if scenario == "rollback-failure":
            return subprocess.CompletedProcess(
                argv, 21, output(argv, name, phase, "failed", ["rollback failed"]), "",
            )
        return subprocess.CompletedProcess(argv, 0, output(argv, name, phase), "")

    actual_status = workflow_module.apply(
        workflow_module.Workflow(tmp_path, runner), artifact.directory,
        f"APPLY NIX-UPDATE PLAN {artifact.digest}",
    )
    result = json.loads(next((artifact.directory / "results").rglob("result.json")).read_text())
    assert actual_status == status, result.get("failure")
    assert result["terminal_state"] == terminal
    assert rollbacks == rollback_order
    assert sum(
        argv[:3] == ["sudo", "HOMEBREW_NO_AUTO_UPDATE=1", "darwin-rebuild"] for argv in calls
    ) == 1
    if terminal == "POST_SWITCH_RESTORED":
        assert result["participant_restoration"] == "restored"
    elif terminal == "POST_SWITCH_RESTORE_INCOMPLETE":
        assert result["participant_restoration"] == "incomplete"
    elif terminal == "POST_SWITCH_PARTICIPANT_INDETERMINATE":
        assert result["participant_restoration"] == "indeterminate"


def test_coupled_prepare_apply_success_retains_postimage_projection_through_switch(
    modules, tmp_path: Path, monkeypatch
):
    _, _, _, workflow_module, _ = modules
    artifact, calls, _, _, _, workflow, controls = prepare_fixture(modules, tmp_path, monkeypatch)
    controls["target_build_count"] = 0
    controls["switch_status"] = 0
    monkeypatch.setattr(workflow_module, "_verify_post_switch", lambda *_args: None)
    control_path = Path(artifact.document["control_transactions"][0]["path"])

    assert workflow_module.apply(
        workflow, artifact.directory, f"APPLY NIX-UPDATE PLAN {artifact.digest}"
    ) == 0

    result = json.loads(next((artifact.directory / "results").rglob("result.json")).read_text())
    assert result["terminal_state"] == "SUCCESS"
    assert control_path.read_text() == "agents = []\n"
    assert control_path.stat().st_mode & 0o777 == 0o600
    current = Path(artifact.document["evaluation_root"]) / "current"
    assert current.is_symlink()
    assert os.readlink(current) == f"generations/{artifact.document['projection']['digest']}"
    switches = [
        command for command in calls
        if command[:3] == ["sudo", "HOMEBREW_NO_AUTO_UPDATE=1", "darwin-rebuild"]
    ]
    assert len(switches) == 1
    assert "--agent" not in switches[0]
    assert f"path:{artifact.document['projection']['root']}" in switches[0]


def test_apply_stale_old_preimage_contract_cannot_restore_already_current_postimage(
    modules, tmp_path: Path, monkeypatch
):
    _, _, _, workflow_module, _ = modules
    artifact, _, _, _, _, workflow, _ = prepare_fixture(modules, tmp_path, monkeypatch)
    control_path = Path(artifact.document["control_transactions"][0]["path"])
    control_path.write_text("agents = []\n")
    control_path.chmod(0o600)
    monkeypatch.setattr(workflow_module, "_revalidate_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_target", lambda *_args, **_kwargs: None)

    assert workflow_module.apply(
        workflow, artifact.directory, f"APPLY NIX-UPDATE PLAN {artifact.digest}"
    ) == 1
    assert control_path.read_text() == "agents = []\n"
    assert control_path.stat().st_mode & 0o777 == 0o600
    result = json.loads(next((artifact.directory / "results").rglob("result.json")).read_text())
    assert result["terminal_state"] == "FAILED_PRE_SWITCH"
    assert not any(event.get("event") == "control-preimage-restored" for event in result["events"])


def test_loaded_plan_rejects_projection_override_rebinding(modules, tmp_path: Path):
    _, _, plan, _, paths = modules
    document = plan_document(plan)
    document["overrides"][-1] = "path:/different-projection"
    rebind_participant_context(plan, document)
    with pytest.raises(paths.ConfigError, match="exact sources and projection"):
        plan.write_plan(tmp_path / "plan", document, "plan\n")


def test_qualification_command_binds_private_report_root_and_projection_options(modules, tmp_path: Path):
    _, _, _, workflow_module, _ = modules
    selected = tmp_path / "selected"
    (selected / "bin").mkdir(parents=True)
    entrypoint = selected / "bin/nix-test"
    entrypoint.write_text("#!/bin/sh\n")
    entrypoint.chmod(0o700)
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)
    command = workflow_module._qualification_command(
        selected, report_root, "4",
        ["--no-update-lock-file", "--no-write-lock-file", "--override-input", "localConfig", "path:/projection"],
    )
    assert command[:4] == [str(entrypoint), "--report", "--report-root", str(report_root)]
    assert command.count("--flake-option") == 5


def test_actual_participant_state_drift_blocks_immediate_preprivilege_switch(
    modules, tmp_path: Path, monkeypatch
):
    _, _, plan, workflow_module, _ = modules
    executable = tmp_path / "stateful-participant"
    count_path = tmp_path / "stateful-participant.count"
    executable.write_text(
        f"#!{Path(sys.executable).resolve()}\n"
        "import hashlib,json,sys\n"
        "from pathlib import Path\n"
        f"a=sys.argv; phase=a[a.index('--phase')+1]; context=Path(a[a.index('--context')+1]); count_path=Path({str(count_path)!r})\n"
        "count=int(count_path.read_text())+1 if count_path.exists() else 1; count_path.write_text(str(count))\n"
        "blocked=count>1; doc={'blockers':['resident state became stale'] if blocked else [],'context_digest':hashlib.sha256(context.read_bytes()).hexdigest(),'name':'resident-reconciler','observations':{'observation':count},'phase':phase,'protocol':'nix-update-participant-v1','status':'blocked' if blocked else 'ok'}\n"
        "print(json.dumps(doc,sort_keys=True,separators=(',',':'))); sys.exit(20 if blocked else 0)\n"
    )
    executable.chmod(0o700)
    document = plan_document(plan)
    specification = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())
    specification["phases"] = ["preflight"]
    specification["mutation_authority"] = []
    specification["rollback_authority"] = []
    document["participants"] = [specification]
    rebind_participant_context(plan, document)
    artifact = plan.write_plan(tmp_path / "plan", document, "plan\n")
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(workflow_module, "_guard_processes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_target", lambda *_args: None)
    monkeypatch.setattr(workflow_module, "_profile_snapshot", lambda *_args: document["live"]["profile"])
    switch_attempts = []

    def runner(argv, **kwargs):
        if str(argv[0]) == str(executable):
            return subprocess.run(argv, **kwargs)
        switch_attempts.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert workflow_module.apply(
        workflow_module.Workflow(tmp_path, runner), artifact.directory,
        f"APPLY NIX-UPDATE PLAN {artifact.digest}",
    ) == 1
    assert switch_attempts == []
    result = json.loads(next((artifact.directory / "results").rglob("result.json")).read_text())
    assert result["terminal_state"] == "FAILED_PRE_SWITCH"
    assert [item["status"] for item in result["participants"]] == ["ok", "blocked"]


def test_production_streaming_bounds_redacts_and_binds_actual_child(modules, tmp_path: Path, monkeypatch, capsys):
    _, _, plan, workflow_module, _ = modules
    monkeypatch.setattr(workflow_module, "COMMAND_RAW_LIMIT", 1024)
    monkeypatch.setattr(workflow_module, "COMMAND_SUMMARY_LIMIT", 256)
    stdout = "api_key=fixture-secret " + "x" * 5000
    stderr = "Authorization: Basic fixture-secret " + "y" * 5000
    script = (
        "import sys;"
        "sys.stdout.write('api_key=fixture-secret '+'x'*5000);sys.stdout.flush();"
        "sys.stderr.write('Authorization: Basic fixture-secret '+'y'*5000);sys.stderr.flush();"
        "raise SystemExit(17)"
    )
    workflow = workflow_module.Workflow(tmp_path, subprocess.run)
    result = workflow.command("actual oversized child", [sys.executable, "-c", script], tmp_path)
    assert result.returncode == 17
    record = workflow.records[-1]
    for evidence, raw in ((record.stdout, stdout.encode()), (record.stderr, stderr.encode())):
        assert evidence["overflow"] is True
        assert evidence["size"] == len(raw)
        assert evidence["retained_bytes"] == 1024
        assert Path(evidence["path"]).stat().st_size == 1024
        assert evidence["sha256"] == hashlib.sha256(raw).hexdigest()
        assert evidence["retained_sha256"] == hashlib.sha256(raw[:1024]).hexdigest()
        assert "fixture-secret" not in evidence["summary"]
    assert len(plan.canonical_bytes({"commands": [record.__dict__]})) < 5000
    captured = capsys.readouterr()
    assert captured.out.startswith("nix-update: running actual oversized child\n")


def test_workflow_can_retain_private_evidence_without_displaying_child_output(
    modules, tmp_path: Path, monkeypatch, capsys
):
    _, _, _, workflow_module, _ = modules
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    script = (
        "import sys;"
        "sys.stdout.write('private closure evidence');"
        "sys.stderr.write('private diagnostic')"
    )
    workflow = workflow_module.Workflow(
        tmp_path, subprocess.run, display_command_output=False
    )

    result = workflow.command(
        "quiet evidence child", [sys.executable, "-c", script], tmp_path
    )

    assert result.returncode == 0
    assert workflow.complete_stdout("quiet evidence child") == "private closure evidence"
    assert workflow.records[-1].stderr["summary"] == "private diagnostic"
    captured = capsys.readouterr()
    assert captured.out == "nix-update: running quiet evidence child\n"
    assert captured.err == ""


def test_production_streaming_redacts_secret_crossing_summary_boundary(modules, tmp_path: Path, monkeypatch, capsys):
    _, _, plan, workflow_module, _ = modules
    monkeypatch.setattr(workflow_module, "COMMAND_RAW_LIMIT", 4096)
    monkeypatch.setattr(workflow_module, "COMMAND_SUMMARY_LIMIT", 64)
    raw = "x" * 60 + '{"authorization":"Bearer sentinel"}'
    script = f"import sys;sys.stdout.write({raw!r})"
    workflow = workflow_module.Workflow(tmp_path, subprocess.run)
    result = workflow.command("boundary-redaction child", [sys.executable, "-c", script], tmp_path)
    assert result.returncode == 0
    record = workflow.records[-1]
    assert "sentinel" not in record.stdout["summary"]
    assert record.stdout["summary"] == plan.redacted_summary(
        Path(record.stdout["path"]).read_bytes(), 64,
    )
    assert len(record.stdout["summary"].encode()) <= 64
    assert "sentinel" not in capsys.readouterr().out


@pytest.mark.parametrize("escaped_quote", [False, True])
def test_production_streaming_redacts_secret_cut_by_raw_limit(
    modules, tmp_path: Path, monkeypatch, capsys, escaped_quote
):
    _, _, plan, workflow_module, _ = modules
    monkeypatch.setattr(workflow_module, "COMMAND_RAW_LIMIT", 48)
    monkeypatch.setattr(workflow_module, "COMMAND_SUMMARY_LIMIT", 48)
    value = r'Bearer abc\"SECRETVALUE' if escaped_quote else "Bearer SECRETVALUE"
    raw = '{"authorization":"' + value + "x" * 100 + '"}'
    script = f"import sys;sys.stdout.write({raw!r})"
    workflow = workflow_module.Workflow(tmp_path, subprocess.run)
    result = workflow.command("raw-boundary-redaction child", [sys.executable, "-c", script], tmp_path)
    assert result.returncode == 0
    record = workflow.records[-1]
    assert record.stdout["overflow"] is True
    assert "SECRETVALUE" not in record.stdout["summary"]
    assert record.stdout["summary"] == plan.redacted_summary(
        Path(record.stdout["path"]).read_bytes(), 48,
    )
    assert "SECRETVALUE" not in capsys.readouterr().out


def test_production_streaming_write_failure_kills_child_and_returns_typed_failure(
    modules, tmp_path: Path, monkeypatch
):
    _, _, _, workflow_module, paths = modules
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    original_write = workflow_module.os.write
    failed = {"value": False}

    def fail_first_evidence_write(descriptor, value):
        if not failed["value"]:
            failed["value"] = True
            raise OSError("injected evidence write failure")
        return original_write(descriptor, value)

    monkeypatch.setattr(workflow_module.os, "write", fail_first_evidence_write)
    started = time.monotonic()
    with pytest.raises(paths.ConfigError, match="command evidence stream failed"):
        workflow_module._run_streamed_command(
            [sys.executable, "-c", "import sys;sys.stdout.write('x'*1048576);sys.stdout.flush()"],
            tmp_path, evidence / "stdout", evidence / "stderr",
        )
    assert time.monotonic() - started < 3


@pytest.mark.parametrize(
    "failure", ["process", "git", "allowlist", "source", "test", "nonexec-test", "projected", "build", "build-output", "additional-build"]
)
def test_prepare_aggregates_safe_blockers_and_never_emits_success(modules, tmp_path: Path, monkeypatch, failure):
    artifact, calls, _, _, _, _, _ = prepare_fixture(modules, tmp_path, monkeypatch, failures={failure})

    assert artifact.document["status"] == "BLOCKED"
    assert artifact.document["blockers"]
    assert "SUCCESS" not in (artifact.directory / "plan.md").read_text()
    assert not any(command[:2] == ["sudo", "darwin-rebuild"] for command in calls)


def test_successful_preflight_means_review_ready_but_remains_inapplicable(modules, tmp_path: Path, monkeypatch):
    artifact, calls, _, _, _, _, _ = prepare_fixture(modules, tmp_path, monkeypatch, failures={"preflight"})

    assert artifact.document["status"] == "READY_FOR_REVIEW"
    assert artifact.document["preflight_only"] is True
    assert not any(command[:2] == ["sudo", "darwin-rebuild"] for command in calls)


@pytest.mark.parametrize("source_drift", [False, True])
def test_catalog_build_binds_sources_before_evaluation_and_revalidates_after_build(
    modules, tmp_path, monkeypatch, source_drift,
):
    from nix_darwin_local import local_source_binding
    _, _, _, workflow_module, paths = modules
    _, _, selected, _, _, _, _ = prepare_fixture(modules, tmp_path, monkeypatch)
    events = []
    records = [{"node": "fixture", "source": "immutable fixture binding"}]
    expected_lock = workflow_module.normalized_lock(selected / "flake.lock")
    def capture(workflow, lock):
        assert workflow.sources == ("configured fixture",)
        assert lock == expected_lock
        events.append("capture")
        return records
    def revalidate(workflow, lock, retained):
        assert lock == expected_lock and retained is records
        assert events[-1] == "build"
        events.append("revalidate")
        if source_drift:
            raise paths.ConfigError("fixture catalog source drift")
    monkeypatch.setattr(local_source_binding, "capture_local_sources", capture)
    monkeypatch.setattr(local_source_binding, "revalidate_local_sources", revalidate)
    monkeypatch.setattr(workflow_module, "local_git_identity", lambda *_args: {"commit": "a" * 40})
    def runner(argv, **kwargs):
        assert events[0] == "capture"
        if argv[:2] == ["nix", "eval"]:
            events.append("eval")
            return subprocess.CompletedProcess(argv, 0, json.dumps({"apgr.toml": ["agents"]}), "")
        if argv[:2] == ["nix", "build"]: events.append("build")
        assert "switch" not in argv and "push" not in argv
        return subprocess.CompletedProcess(argv, 0, "", "")
    workflow = workflow_module.Workflow(tmp_path, runner, sources=("configured fixture",))
    arguments = (workflow, selected, "FixtureHost", tmp_path / "control", [])
    if source_drift:
        with pytest.raises(paths.ConfigError, match="catalog source drift"):
            workflow_module.build_only(*arguments)
    else:
        assert workflow_module.build_only(*arguments) == 0
    assert events == ["capture", "eval", "build", "revalidate"]


def test_build_only_runs_report_then_locked_build_with_separate_job_controls(modules, tmp_path: Path, monkeypatch):
    _, _, _, workflow_module, _ = modules
    artifact, _, selected, _, _, _, _ = prepare_fixture(modules, tmp_path, monkeypatch)
    control = tmp_path / "control"
    calls = []
    authority_calls = []
    monkeypatch.setattr(
        workflow_module,
        "local_git_identity",
        lambda *_args: authority_calls.append(True) or {"commit": "a" * 40},
    )

    def runner(argv, **_kwargs):
        calls.append(list(argv))
        if argv[:2] == ["nix", "eval"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"apgr.toml": ["agents"]}) + "\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    status = workflow_module.build_only(workflow_module.Workflow(tmp_path, runner), selected, "FixtureHost", control, [], "4", "2")
    assert status == 0
    assert calls[1][calls[1].index("--jobs"):calls[1].index("--jobs") + 2] == ["--jobs", "4"]
    assert "--override-input" in calls[1]
    assert calls[3][-2:] == ["--max-jobs", "2"]
    assert all("flake update" not in " ".join(command) for command in calls)
    assert len(authority_calls) == 2
    assert artifact.document["status"] == "READY_FOR_REVIEW"


@pytest.mark.parametrize("report_hook", [False, True])
def test_build_only_accepts_default_job_controls(modules, tmp_path: Path, monkeypatch, report_hook):
    _, _, _, workflow_module, _ = modules
    _, _, selected, _, _, _, _ = prepare_fixture(modules, tmp_path, monkeypatch)
    if not report_hook:
        (selected / "bin/nix-test").unlink()
    calls = []
    authority_calls = []
    monkeypatch.setattr(
        workflow_module,
        "local_git_identity",
        lambda *_args: authority_calls.append(True) or {"commit": "a" * 40},
    )

    def runner(argv, **_kwargs):
        calls.append(list(argv))
        if argv[:2] == ["nix", "eval"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"apgr.toml": ["agents"]}) + "\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    status = workflow_module.build_only(
        workflow_module.Workflow(tmp_path, runner), selected, "FixtureHost", tmp_path / "control", [],
    )
    assert status == 0
    assert not any("--jobs" in command or "--max-jobs" in command for command in calls)
    assert len(authority_calls) == 2
    checks = [command for command in calls if command[:3] == ["nix", "flake", "check"]]
    assert len(checks) == 1
    assert checks[0][:8] == ["nix", "flake", "check", "path:.", "--no-update-lock-file",
                            "--no-write-lock-file", "--override-input", "localConfig"]
    assert checks[0][8].startswith("path:") and len(checks[0]) == 9
    builds = [command for command in calls if command[:2] == ["nix", "build"]]
    assert len(builds) == 1 and builds[0][-3:] == checks[0][-3:]
    reports = [command for command in calls if command[0].endswith("/bin/nix-test")]
    assert len(reports) == int(report_hook)
    if report_hook:
        assert reports[0][-6:] == ["--flake-option", "--override-input", "--flake-option",
                                  "localConfig", "--flake-option", checks[0][8]]


def test_nonexecutable_selected_test_hook_is_a_blocker(modules, tmp_path: Path):
    _, _, _, workflow_module, paths = modules
    selected = tmp_path / "selected"
    (selected / "bin").mkdir(parents=True)
    (selected / "bin/nix-test").write_text("#!/bin/sh\n")
    (selected / "bin/nix-test").chmod(0o600)
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)

    with pytest.raises(paths.ConfigError, match="not an exact executable"):
        workflow_module._qualification_command(selected, report_root, "8")


def test_prepare_never_falls_back_from_an_unsafe_selected_test_hook(modules, tmp_path: Path, monkeypatch):
    artifact, calls, _, _, _, _, _ = prepare_fixture(
        modules, tmp_path, monkeypatch, failures={"nonexec-test"}
    )

    assert artifact.document["status"] == "BLOCKED"
    assert any("not an exact executable" in blocker for blocker in artifact.document["blockers"])
    flake_checks = [command for command in calls if command[:3] == ["nix", "flake", "check"]]
    assert flake_checks
    assert all("localConfig" in command for command in flake_checks)


@pytest.mark.parametrize("failed_command", ["report", "check", "build"])
def test_build_only_stops_at_each_qualification_failure(modules, tmp_path: Path, monkeypatch, failed_command, capsys):
    _, _, _, workflow_module, _ = modules
    _, _, selected, _, _, _, _ = prepare_fixture(modules, tmp_path, monkeypatch)
    calls = []
    authority_calls = []
    monkeypatch.setattr(
        workflow_module,
        "local_git_identity",
        lambda *_args: authority_calls.append(True) or {"commit": "a" * 40},
    )

    def runner(argv, **_kwargs):
        calls.append(list(argv))
        if argv[:2] == ["nix", "eval"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"apgr.toml": ["agents"]}) + "\n", "")
        label = "report" if argv and str(argv[0]).endswith("bin/nix-test") else "build" if argv[:2] == ["nix", "build"] else "check"
        return subprocess.CompletedProcess(argv, 9 if label == failed_command else 0, "", "fixture command failed")

    status = workflow_module.build_only(
        workflow_module.Workflow(tmp_path, runner), selected, "FixtureHost", tmp_path / "control", [],
    )
    assert status == (9 if failed_command == "build" else 1)
    if failed_command != "build":
        assert not any(command[:2] == ["nix", "build"] for command in calls)
    assert len(authority_calls) == 2
    error = capsys.readouterr().err
    assert "exit 9" in error and "fixture command failed" in error
    assert "private evidence:" in error


@pytest.mark.parametrize(
    ("mode", "inputs", "message"),
    [
        ("targeted", [], "requires at least one"),
        ("locked", ["source"], "valid only for targeted"),
        ("surprise", [], "must be locked"),
    ],
)
def test_lock_maintenance_rejects_ambiguous_authority_before_workspace_creation(
    modules, tmp_path: Path, mode, inputs, message
):
    _, _, _, workflow_module, paths = modules
    with pytest.raises(paths.ConfigError, match=message):
        workflow_module.maintain_lock(
            workflow_module.Workflow(tmp_path, lambda *_args, **_kwargs: None),
            tmp_path / "selected", mode, inputs, [], tmp_path / "evidence", False,
        )
    assert not (tmp_path / "evidence").exists()


def test_lock_evidence_output_is_absolute_and_outside_repository(modules, tmp_path: Path):
    _, _, _, workflow_module, paths = modules
    selected = tmp_path / "repository" / "selected"
    selected.mkdir(parents=True)
    with pytest.raises(paths.ConfigError, match="absolute"):
        workflow_module._validate_lock_output(Path("relative-evidence"), selected)
    with pytest.raises(paths.ConfigError, match="outside"):
        workflow_module._validate_lock_output(selected / "evidence", selected)
    sibling = tmp_path / "repository" / "evidence"
    with pytest.raises(paths.ConfigError, match="outside"):
        workflow_module._validate_lock_output(sibling, selected, tmp_path / "repository")


@pytest.mark.parametrize(("mode", "install"), [("locked", False), ("targeted", False), ("all", True)])
def test_lock_maintenance_is_isolated_semantic_and_explicit(modules, tmp_path: Path, monkeypatch, mode, install):
    _, _, _, workflow_module, _ = modules
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "flake.nix").write_text("{}\n")
    write_fixture_lock(selected / "flake.lock", "sha256-before=")
    initialize_git_fixture(selected, tmp_path)
    before = (selected / "flake.lock").read_bytes()
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)

    def runner(argv, cwd=None, **_kwargs):
        if argv[0] == "git":
            return subprocess.run(argv, cwd=cwd, **_kwargs)
        if mode != "locked":
            write_fixture_lock(Path(cwd) / "flake.lock", "sha256-after=")
        return subprocess.CompletedProcess(argv, 0, "", "")

    inputs = ["source"] if mode == "targeted" else []
    status = workflow_module.maintain_lock(
        workflow_module.Workflow(tmp_path, runner), selected, mode, inputs, ["source"], tmp_path / "lock-evidence", install
    )
    assert status == 0
    evidence = tmp_path / "lock-evidence"
    assert (evidence / "flake.lock.candidate").stat().st_mode & 0o777 == 0o600
    delta = json.loads((evidence / "delta.json").read_text())
    assert delta["preimage_sha256"] == hashlib.sha256((evidence / "flake.lock.preimage").read_bytes()).hexdigest()
    assert delta["candidate_sha256"] == hashlib.sha256((evidence / "flake.lock.candidate").read_bytes()).hexdigest()
    assert ((selected / "flake.lock").read_bytes() != before) is install


def test_lock_preimage_normalization_detects_concurrent_byte_drift(modules, tmp_path: Path, monkeypatch):
    _, _, _, workflow_module, paths = modules
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "flake.nix").write_text("{}\n")
    write_fixture_lock(selected / "flake.lock", "sha256-before=")
    initialize_git_fixture(selected, tmp_path)
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    original = workflow_module.normalized_lock

    def drift_after_normalization(path):
        value = original(path)
        if Path(path) == selected / "flake.lock":
            write_fixture_lock(selected / "flake.lock", "sha256-drift=")
        return value

    monkeypatch.setattr(workflow_module, "normalized_lock", drift_after_normalization)
    attempted = []

    def runner(argv, cwd=None, **kwargs):
        if argv[0] == "git":
            return subprocess.run(argv, cwd=cwd, **kwargs)
        attempted.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    with pytest.raises(paths.ConfigError, match="changed while its preimage was normalized"):
        workflow_module.maintain_lock(
            workflow_module.Workflow(tmp_path, runner),
            selected, "locked", [], [], tmp_path / "evidence", False,
        )
    assert attempted == []


def test_failed_isolated_lock_update_preserves_real_lock(modules, tmp_path: Path, monkeypatch):
    _, _, _, workflow_module, _ = modules
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "flake.nix").write_text("{}\n")
    write_fixture_lock(selected / "flake.lock")
    initialize_git_fixture(selected, tmp_path)
    before = (selected / "flake.lock").read_bytes()
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    def runner(argv, cwd=None, **kwargs):
        if argv[0] == "git":
            return subprocess.run(argv, cwd=cwd, **kwargs)
        return subprocess.CompletedProcess(argv, 19, "", "failed")

    result = workflow_module.maintain_lock(
        workflow_module.Workflow(tmp_path, runner),
        selected, "all", [], [], tmp_path / "evidence", False,
    )
    assert result == 19
    assert (selected / "flake.lock").read_bytes() == before
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize("name", ["flake.nix", "flake.lock"])
def test_isolated_lock_maintenance_rejects_symlinked_authority_files(
    modules, tmp_path: Path, monkeypatch, name: str
):
    _, _, _, workflow_module, paths = modules
    selected = tmp_path / "selected"
    selected.mkdir()
    external = tmp_path / f"external-{name}"
    if name == "flake.lock":
        write_fixture_lock(external)
        (selected / "flake.nix").write_text("{}\n")
    else:
        external.write_text("{}\n")
        write_fixture_lock(selected / "flake.lock")
    (selected / name).symlink_to(external)
    initialize_git_fixture(selected, tmp_path)
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    attempted = []

    with pytest.raises(paths.ConfigError, match="single-link regular"):
        workflow_module.maintain_lock(
            workflow_module.Workflow(
                tmp_path,
                lambda argv, cwd=None, **kwargs: subprocess.run(argv, cwd=cwd, **kwargs)
                if argv[0] == "git" else attempted.append(argv),
            ),
            selected, "locked", [], [], tmp_path / "evidence", False,
        )
    assert attempted == []


@pytest.mark.parametrize("drift", ["lock-symlink", "lock-hardlink", "flake-nix-bytes"])
def test_isolated_lock_maintenance_revalidates_candidate_files_after_child(
    modules, tmp_path: Path, monkeypatch, drift: str
):
    _, _, _, workflow_module, paths = modules
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "flake.nix").write_text("{}\n")
    write_fixture_lock(selected / "flake.lock")
    initialize_git_fixture(selected, tmp_path)
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    external = tmp_path / "external-lock"
    write_fixture_lock(external, "sha256-external=")

    def runner(argv, cwd=None, **kwargs):
        if argv[0] == "git":
            return subprocess.run(argv, cwd=cwd, **kwargs)
        candidate_root = Path(cwd)
        if drift == "flake-nix-bytes":
            (candidate_root / "flake.nix").write_text("{ changed = true; }\n")
        else:
            candidate_lock = candidate_root / "flake.lock"
            candidate_lock.unlink()
            if drift == "lock-symlink":
                candidate_lock.symlink_to(external)
            else:
                os.link(external, candidate_lock)
        return subprocess.CompletedProcess(argv, 0, "", "")

    with pytest.raises(paths.ConfigError, match="single-link regular|changed reviewed flake.nix"):
        workflow_module.maintain_lock(
            workflow_module.Workflow(tmp_path, runner),
            selected, "locked", [], [], tmp_path / "evidence", False,
        )
    assert not (tmp_path / "evidence").exists()


def test_unapproved_lock_delta_retains_candidate_evidence_without_install(modules, tmp_path: Path, monkeypatch):
    _, _, _, workflow_module, paths = modules
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "flake.nix").write_text("{}\n")
    write_fixture_lock(selected / "flake.lock", "sha256-before=")
    initialize_git_fixture(selected, tmp_path)
    before = (selected / "flake.lock").read_bytes()
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)

    def runner(argv, cwd=None, **_kwargs):
        if argv[0] == "git":
            return subprocess.run(argv, cwd=cwd, **_kwargs)
        write_fixture_lock(Path(cwd) / "flake.lock", "sha256-unapproved=")
        return subprocess.CompletedProcess(argv, 0, "", "")

    evidence = tmp_path / "evidence"
    with pytest.raises(paths.ConfigError, match="unauthorized node"):
        workflow_module.maintain_lock(
            workflow_module.Workflow(tmp_path, runner), selected, "locked", [], [], evidence, False,
        )
    assert (evidence / "flake.lock.candidate").is_file()
    assert json.loads((evidence / "delta.json").read_text())["changes"][0]["node"] == "source"
    assert (selected / "flake.lock").read_bytes() == before


def test_participant_phase_skip_empty_blocker_and_bounded_rollback(modules, tmp_path: Path):
    _, participants, plan, _, paths = modules
    executable = tmp_path / "participant"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)
    spec = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())
    context, _ = participant_context(tmp_path / "context")

    rollback_contexts = []

    def runner(argv, **_kwargs):
        phase = argv[argv.index("--phase") + 1]
        if phase == "rollback":
            rollback_contexts.append(Path(argv[argv.index("--context") + 1]))
        output = participant_output(phase, "blocked" if phase == "preflight" else "ok", [], context_digest=participant_context_digest(argv))
        return subprocess.CompletedProcess(argv, 20 if phase == "preflight" else 0, output, "")

    results, blockers = participants.run_phase([spec], "preflight", context, runner)
    assert results[0].status == "blocked"
    assert blockers == ["participant resident-reconciler returned blocked"]
    assert participants.run_phase([spec], "unused", context, runner) == ([], [])
    assert participants.bounded_rollback(spec, context, "handle", runner).status == "ok"
    rollback_context = rollback_contexts[0]
    rollback_document = json.loads(rollback_context.read_text())
    assert rollback_context.stat().st_mode & 0o777 == 0o600
    assert rollback_document["parent_context_digest"] == hashlib.sha256(context.read_bytes()).hexdigest()
    assert rollback_document["declaration_digest"] == hashlib.sha256(
        plan.canonical_bytes(spec)
    ).hexdigest()
    no_rollback = dict(spec, rollback_authority=[])
    with pytest.raises(paths.ConfigError, match="no independently safe rollback"):
        participants.bounded_rollback(no_rollback, context, "handle", runner)


def test_partial_pre_switch_participant_results_remain_rollback_capable(modules, tmp_path: Path):
    _, _, _, workflow_module, _ = modules
    first = tmp_path / "first-participant"
    second = tmp_path / "second-participant"
    for executable in (first, second):
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o700)
    first_spec = participant_spec(first, hashlib.sha256(first.read_bytes()).hexdigest())
    first_spec["name"] = "first"
    second_spec = participant_spec(second, hashlib.sha256(second.read_bytes()).hexdigest())
    second_spec["name"] = "second"
    calls = []

    def runner(argv, **_kwargs):
        calls.append(list(argv))
        phase = argv[argv.index("--phase") + 1]
        name = Path(argv[0]).name.split("-")[0]
        if name == "second":
            return subprocess.CompletedProcess(argv, 0, "not-json\n", "")
        output = json.loads(participant_output(phase, rollback_handle="first-handle" if phase == "pre-switch" else None, context_digest=participant_context_digest(argv)))
        output["name"] = "first"
        return subprocess.CompletedProcess(
            argv, 0, json.dumps(output, sort_keys=True, separators=(",", ":")) + "\n", ""
        )

    context, _ = participant_context(
        tmp_path / "context.json", [first_spec, second_spec], "pre-switch"
    )
    results, blockers = workflow_module._safe_participant_phase(
        [first_spec, second_spec], "pre-switch", context, runner
    )
    assert [item.name for item in results] == ["first"]
    assert blockers
    assert workflow_module._participant_phase_indeterminate(
        [first_spec, second_spec], "pre-switch", results
    ) is True
    originating_context = context.parent / f"{results[0].context_digest}.json"
    originating_context.write_bytes(context.read_bytes())
    originating_context.chmod(0o600)
    sink = list(results)
    workflow_module._participant_rollbacks(
        [first_spec, second_spec], results, context, runner, sink
    )
    assert [item.phase for item in sink] == ["pre-switch", "rollback"]


def test_failed_participant_rollback_is_not_reported_as_restored(modules, tmp_path: Path):
    _, participants, _, workflow_module, paths = modules
    executable = tmp_path / "participant"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)
    spec = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())

    def runner(argv, **_kwargs):
        phase = argv[argv.index("--phase") + 1]
        if phase == "pre-switch":
            return subprocess.CompletedProcess(argv, 0, participant_output(phase, rollback_handle="handle", context_digest=participant_context_digest(argv)), "")
        return subprocess.CompletedProcess(argv, 21, participant_output(phase, "failed", ["rollback failed"], context_digest=participant_context_digest(argv)), "")

    context, _ = participant_context(tmp_path / "context.json", [spec], "pre-switch")
    result = participants.run_participant(spec, "pre-switch", context, runner)
    originating_context = context.parent / f"{result.context_digest}.json"
    originating_context.write_bytes(context.read_bytes())
    originating_context.chmod(0o600)
    sink = [result]
    with pytest.raises(paths.ConfigError, match="rollback did not succeed"):
        workflow_module._participant_rollbacks([spec], [result], context, runner, sink)
    assert sink[-1].status == "failed"


def test_participant_rollback_rejects_tampered_originating_context(modules, tmp_path: Path):
    _, participants, _, workflow_module, paths = modules
    executable = tmp_path / "participant"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)
    spec = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())

    def runner(argv, **_kwargs):
        phase = argv[argv.index("--phase") + 1]
        return subprocess.CompletedProcess(
            argv, 0,
            participant_output(phase, rollback_handle="handle", context_digest=participant_context_digest(argv)),
            "",
        )

    context, _ = participant_context(tmp_path / "context.json")
    result = participants.run_participant(spec, "pre-switch", context, runner)
    originating_context = context.parent / f"{result.context_digest}.json"
    originating_context.write_text('{"tampered":true}\n')
    originating_context.chmod(0o600)
    with pytest.raises(paths.ConfigError, match="context digest changed"):
        workflow_module._participant_rollbacks([spec], [result], context, runner, [result])


@pytest.mark.parametrize("defect", ["stage", "target", "declaration"])
def test_participant_rollback_requires_exact_target_aware_origin_and_declaration(
    modules, tmp_path: Path, defect: str,
):
    _, _, plan, workflow_module, paths = modules
    executable = tmp_path / "participant"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)
    spec = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())
    context, _ = participant_context(tmp_path / "context.json", [spec], "pre-switch")
    document = json.loads(context.read_text())
    if defect == "stage":
        document["stage"] = "preflight"
    elif defect == "target":
        document["target"] = {"output": "/nix/store/other"}
    else:
        changed = json.loads(json.dumps(spec))
        changed["timeout_seconds"] += 1
        document["participant_declarations"] = [changed]
        document["participant_declaration_digest"] = plan.sha256_bytes(
            plan.canonical_bytes({"participants": [changed]})
        )
    raw = plan.canonical_bytes(document)
    digest = plan.sha256_bytes(raw)
    origin = context.parent / f"{digest}.json"
    origin.write_bytes(raw)
    origin.chmod(0o600)
    result = workflow_module.ParticipantResult(
        spec["name"], "pre-switch", "ok", (), {}, "handle", digest, "b" * 64, "", "",
    )
    with pytest.raises(paths.ConfigError, match="originating participant"):
        workflow_module._participant_rollbacks(
            [spec], [result], context,
            lambda *_args, **_kwargs: pytest.fail("rollback must not spawn"), [result],
        )


def test_mutating_pre_switch_result_without_rollback_handle_is_indeterminate(modules, tmp_path: Path):
    _, _, _, workflow_module, _ = modules
    executable = tmp_path / "participant"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)
    spec = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())
    result = workflow_module.ParticipantResult(
        "resident-reconciler", "pre-switch", "ok", (), {}, None, "a" * 64, "b" * 64, "", ""
    )

    assert workflow_module._participant_phase_indeterminate([spec], "pre-switch", [result]) is True


def test_mutating_pre_switch_participant_must_return_rollback_handle(modules, tmp_path: Path):
    _, _, _, workflow_module, _ = modules
    executable = tmp_path / "participant"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)
    spec = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())

    def runner(argv, **_kwargs):
        phase = argv[argv.index("--phase") + 1]
        return subprocess.CompletedProcess(argv, 0, participant_output(phase, context_digest=participant_context_digest(argv)), "")

    context, _ = participant_context(tmp_path / "context.json")
    results, blockers = workflow_module._safe_participant_phase(
        [spec], "pre-switch", context, runner
    )
    assert len(results) == 1
    assert blockers == ["mutating pre-switch participant did not return a rollback handle: resident-reconciler"]


def test_read_only_participant_phase_allows_later_declared_mutation_authority(modules, tmp_path: Path):
    _, _, _, workflow_module, _ = modules
    executable = tmp_path / "participant"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)
    spec = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())
    attempted = []
    context, _ = participant_context(tmp_path / "context.json", [spec], "preflight")

    def runner(argv, **_kwargs):
        attempted.append(argv)
        return subprocess.CompletedProcess(
            argv, 0,
            participant_output("preflight", context_digest=participant_context_digest(argv)),
            "",
        )

    results, blockers = workflow_module._safe_participant_phase(
        [spec], "preflight", context, runner,
    )
    assert len(results) == 1
    assert blockers == []
    assert len(attempted) == 1


@pytest.mark.parametrize(
    "defect",
    ["relative", "symlink", "hardlink", "public", "nonexec", "phase", "list", "identity", "status", "blockers", "observations", "handle", "context"],
)
def test_participant_remaining_validation_branches(modules, tmp_path: Path, defect):
    _, participants, _, _, paths = modules
    executable = tmp_path / "participant"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)
    spec = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())
    if defect == "relative": spec["path"] = "participant"
    elif defect == "symlink":
        link = tmp_path / "link"; link.symlink_to(executable); spec["path"] = str(link)
    elif defect == "hardlink": os.link(executable, tmp_path / "other-link")
    elif defect == "public": executable.chmod(0o755)
    elif defect == "nonexec": executable.chmod(0o600)
    elif defect == "phase": spec["phases"] = ["preflight"]
    context, context_digest = participant_context(tmp_path / "context")
    document = json.loads(participant_output("preflight", context_digest=context_digest))
    if defect == "list": output = "[]\n"
    else:
        if defect == "identity": document["name"] = "wrong"
        elif defect == "status": document["status"] = "unknown"
        elif defect == "blockers": document["blockers"] = [7]
        elif defect == "observations": document["observations"] = []
        elif defect == "handle": document["rollback_handle"] = 7
        elif defect == "context": document["context_digest"] = 7
        output = json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"

    def runner(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, output, "")

    with pytest.raises(paths.ConfigError):
        participants.run_participant(spec, "post-switch-verify" if defect == "phase" else "preflight", context, runner)


def test_process_table_parsing_ancestors_and_failure(modules, monkeypatch):
    state, _, _, _, paths = modules
    mock_process_table(
        monkeypatch,
        state,
        [
            (10, 5, "nix", "nix flake update source"),
            (5, 1, "shell", "shell parent"),
            (11, 1, "nix", "nix 'unterminated"),
        ],
    )
    records = state.read_process_table()
    assert records[0]["argv"] == ["nix", "flake", "update", "source"]
    assert records[0]["comm"] == "nix"
    assert records[-1]["parse_error"] == "unmatched process arguments"
    assert state.process_ancestors(records, 10) == {1, 5, 10}
    monkeypatch.setattr(state.subprocess, "run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, "", "failed"))
    with pytest.raises(paths.ConfigError, match="process table"):
        state.read_process_table()


def test_profile_transition_preserves_predecessors_and_rejects_drift(modules):
    state, _, _, _, paths = modules
    def generation(number, target):
        return {
            "path": f"/profile/system-{number}-link", "link": target, "target": target,
            "generation": number, "kind": "symlink", "uid": os.getuid(), "gid": os.getgid(),
            "mode": 0o755, "link_count": 1,
        }

    before = {"retained": [generation(187, "/safe"), generation(188, "/failed")]}
    selected = {
        "target": "/target", "resolved_target": "/target", "link": "system-189-link",
        "symlink_text": "system-189-link", "generation_path": "/profile/system-189-link",
        "generation_number": 189, "generation_symlink_text": "/target",
    }
    after = {
        "selected": selected,
        "live": {"target": "/target", "resolved_target": "/target", "link": "/target", "symlink_text": "/target"},
        "retained": [*before["retained"], generation(189, "/target")],
    }
    state.validate_profile_transition(before, after, "/target")
    with pytest.raises(paths.ConfigError, match="do not equal"):
        state.validate_profile_transition(before, dict(after, selected=dict(selected, target="/other")), "/target")
    with pytest.raises(paths.ConfigError, match="do not equal"):
        state.validate_profile_transition(before, dict(after, live={"target": "/other"}), "/target")
    repointed_live = dict(after, live={
        "target": "/target", "resolved_target": "/target",
        "link": "/different-link", "symlink_text": "/different-link",
    })
    with pytest.raises(paths.ConfigError, match="live-system link"):
        state.validate_profile_transition(before, repointed_live, "/target")
    with pytest.raises(paths.ConfigError, match="changed"):
        state.validate_profile_transition(before, dict(after, retained=[generation(189, "/target")]), "/target")
    with pytest.raises(paths.ConfigError, match="count"):
        state.validate_profile_transition(before, dict(after, retained=[*after["retained"], generation(190, "/extra")]), "/target")
    low_number = dict(after, retained=[*before["retained"], generation(188, "/target")])
    low_number["retained"][-1]["path"] = "/profile/system-190-link"
    with pytest.raises(paths.ConfigError, match="number violates"):
        state.validate_profile_transition(before, low_number, "/target")
    wrong_new_target = dict(after, retained=[*before["retained"], generation(189, "/other")])
    with pytest.raises(paths.ConfigError, match="new generation"):
        state.validate_profile_transition(before, wrong_new_target, "/target")
    wrong_selected_link = dict(after, selected=dict(selected, generation_path="/profile/system-190-link"))
    with pytest.raises(paths.ConfigError, match="exact new generation"):
        state.validate_profile_transition(before, wrong_selected_link, "/target")
    renamed = json.loads(json.dumps(after))
    renamed["retained"][0]["path"] = "/profile/system-186-link"
    renamed["retained"][0]["generation"] = 186
    with pytest.raises(paths.ConfigError, match="changed"):
        state.validate_profile_transition(before, renamed, "/target")
    repointed = json.loads(json.dumps(after))
    repointed["retained"][0]["link"] = "/same-target-through-different-link"
    with pytest.raises(paths.ConfigError, match="changed"):
        state.validate_profile_transition(before, repointed, "/target")
    with pytest.raises(paths.ConfigError, match="positive"):
        state.realignment_command(Path("/profile"), 0)


@pytest.mark.parametrize("defect", ["prehash", "posthash", "relative", "unsafe-ancestor"])
def test_control_transaction_contract_failures_before_mutation(modules, tmp_path: Path, defect):
    state, _, _, _, paths = modules
    root = tmp_path / "control"
    root.mkdir(mode=0o700)
    path = root / "runtime.toml"
    path.write_bytes(b'agents = ["codex", "claude"]\n')
    path.chmod(0o644)
    spec = control_spec(path)
    if defect == "prehash": spec["preimage_sha256"] = "0" * 64
    elif defect == "posthash": spec["postimage_sha256"] = "0" * 64
    elif defect == "relative": spec["path"] = "relative"
    else:
        root.chmod(0o777)
    with pytest.raises((paths.ConfigError, FileNotFoundError)):
        state.apply_control_transaction(spec, tmp_path / "journal")
    assert path.read_bytes() == b'agents = ["codex", "claude"]\n'


@pytest.mark.parametrize(
    "defect",
    ["no-restore", "restore-drift", "restore-mode", "restore-hardlink", "post-mode", "post-content", "marker-name"],
)
def test_control_restore_verify_and_marker_failure_branches(modules, tmp_path: Path, defect):
    state, _, _, _, paths = modules
    root = tmp_path / "control"
    root.mkdir(mode=0o700)
    path = root / "runtime.toml"
    path.write_bytes(b'agents = ["codex", "claude"]\n')
    path.chmod(0o644)
    spec = control_spec(path)
    if defect == "marker-name":
        with pytest.raises(paths.ConfigError):
            state.durable_marker(tmp_path / "state", "unsafe", {})
        return
    state.apply_control_transaction(spec, tmp_path / "journal")
    if defect == "no-restore":
        spec["restore_before_switch"] = False
        state.restore_control_transaction(spec)
        assert path.read_bytes() == b"agents = []\n"
    elif defect == "restore-drift":
        path.write_text("drift\n")
        with pytest.raises(paths.ConfigError, match="content drift"):
            state.restore_control_transaction(spec)
    elif defect == "restore-mode":
        path.chmod(0o644)
        with pytest.raises(paths.ConfigError, match="mode drift"):
            state.restore_control_transaction(spec)
    elif defect == "restore-hardlink":
        os.link(path, root / "other-link")
        with pytest.raises(paths.ConfigError, match="topology drift"):
            state.restore_control_transaction(spec)
    elif defect == "post-mode":
        path.chmod(0o644)
        with pytest.raises(paths.ConfigError, match="mode drift"):
            state.verify_control_postimage(spec)
    else:
        path.write_text("drift\n")
        with pytest.raises(paths.ConfigError, match="content drift"):
            state.verify_control_postimage(spec)


def test_control_preflight_derives_virtual_postimage_and_idempotent_contract(modules, tmp_path: Path):
    state, _, _, _, _ = modules
    root = tmp_path / "control"
    root.mkdir(mode=0o700)
    path = root / "runtime.toml"
    path.write_bytes(b'agents = ["codex", "claude"]\n')
    path.chmod(0o644)
    specification = control_spec(path)
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)

    projection = state.preflight_control_transactions(root, [specification], scratch / "virtual")
    assert path.read_bytes() == b'agents = ["codex", "claude"]\n'
    assert path.stat().st_mode & 0o777 == 0o644
    projected = scratch / "virtual/runtime.toml"
    assert projected.read_bytes() == b"agents = []\n"
    assert projected.stat().st_mode & 0o777 == 0o600
    assert projection["transactions"][0]["projected_postimage_actual"] == projection["transactions"][0]["projected_postimage"]

    path.write_bytes(b"agents = []\n")
    path.chmod(0o600)
    second = tmp_path / "second"
    second.mkdir(mode=0o700)
    idempotent = state.preflight_control_transactions(root, [control_spec(path)], second / "virtual")
    effective = idempotent["effective_transactions"][0]
    assert effective["preimage_sha256"] == effective["postimage_sha256"]
    assert effective["preimage_bytes_b64"] == effective["postimage_bytes_b64"]
    assert effective["restore_before_switch"] is False


def test_control_preflight_rejects_malformed_postimage_toml_before_projection(modules, tmp_path: Path):
    state, _, _, _, paths = modules
    root = tmp_path / "control"
    root.mkdir(mode=0o700)
    path = root / "runtime.toml"
    path.write_bytes(b'agents = ["codex", "claude"]\n')
    path.chmod(0o644)
    malformed = b"agents = [\n"
    specification = control_spec(path, post=malformed)
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)

    with pytest.raises(paths.ConfigError, match="valid TOML"):
        state.preflight_control_transactions(root, [specification], scratch / "virtual")
    assert path.read_bytes() == b'agents = ["codex", "claude"]\n'
    assert not (scratch / "virtual").exists()


def test_control_preflight_rejects_symlinked_root_ancestor(modules, tmp_path: Path):
    state, _, _, _, paths = modules
    real_parent = tmp_path / "real"
    root = real_parent / "control"
    root.mkdir(parents=True, mode=0o700)
    path = root / "runtime.toml"
    path.write_bytes(b'agents = ["codex", "claude"]\n')
    path.chmod(0o644)
    alias = tmp_path / "alias"
    alias.symlink_to(real_parent)
    aliased_root = alias / "control"
    spec = control_spec(aliased_root / "runtime.toml")
    spec["safe_ancestors"] = [str(aliased_root)]
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)
    with pytest.raises(paths.ConfigError, match="symlink (ancestor|traversal)"):
        state.preflight_control_transactions(aliased_root, [spec], scratch / "virtual")


def test_retained_virtual_control_projection_is_digest_revalidated(modules, tmp_path: Path):
    _, _, plan, workflow_module, paths = modules
    virtual = tmp_path / "virtual"
    virtual.mkdir(mode=0o700)
    projected = virtual / "runtime.toml"
    projected.write_text("agents = []\n")
    projected.chmod(0o600)
    tree = plan.tree_identity(virtual)
    transactions = [{"path": "/control/runtime.toml"}]
    projection = {
        "virtual_root": str(virtual), "projected_tree": tree, "transactions": transactions,
        "digest": plan.sha256_bytes(plan.canonical_bytes({"projected_tree": tree, "transactions": transactions})),
    }
    workflow_module._validate_virtual_control_projection(projection)
    virtual.chmod(0o777)
    with pytest.raises(paths.ConfigError, match="owner-only"):
        workflow_module._validate_virtual_control_projection(projection)
    virtual.chmod(0o700)
    projected.write_text('agents = ["codex"]\n')
    with pytest.raises(paths.ConfigError, match="virtual control projection drifted"):
        workflow_module._validate_virtual_control_projection(projection)


@pytest.mark.parametrize(
    "defect",
    ["missing-ancestor", "symlink-ancestor", "wrong-uid", "wrong-gid", "wrong-link", "stale", "xattr", "acl", "flags"],
)
def test_control_preflight_fault_matrix_blocks_before_projection(modules, tmp_path: Path, monkeypatch, defect):
    state, _, _, _, paths = modules
    root = tmp_path / "control"
    nested = root / "nested"
    nested.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    path = nested / "runtime.toml"
    path.write_bytes(b'agents = ["codex", "claude"]\n')
    path.chmod(0o644)
    specification = control_spec(path)
    specification["safe_ancestors"] = [str(root), str(nested)]
    if defect == "missing-ancestor":
        specification["safe_ancestors"] = [str(root)]
    elif defect == "symlink-ancestor":
        real = root / "real"
        nested.rename(real)
        nested.symlink_to(real)
    elif defect == "wrong-uid":
        specification["pre_uid"] += 1
    elif defect == "wrong-gid":
        specification["pre_gid"] += 1
    elif defect == "wrong-link":
        os.link(path, nested / "other")
    elif defect == "stale":
        path.write_text("stale = true\n")
    elif defect == "xattr":
        subprocess.run(["xattr", "-w", "com.example.nix-update", "fixture", str(path)], check=True)
    elif defect == "acl":
        original_run = state.subprocess.run

        def acl_run(argv, **kwargs):
            if argv[:2] == ["ls", "-lde"]:
                return subprocess.CompletedProcess(argv, 0, "-rw-r--r-- 1 fixture fixture 0 file\n 0: everyone deny write\n", "")
            return original_run(argv, **kwargs)

        monkeypatch.setattr(state.subprocess, "run", acl_run)
    else:
        subprocess.run(["chflags", "uchg", str(path)], check=True)
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)
    try:
        with pytest.raises((paths.ConfigError, FileNotFoundError)):
            state.preflight_control_transactions(root, [specification], scratch / "virtual")
    finally:
        if defect == "flags":
            subprocess.run(["chflags", "nouchg", str(path)], check=True)
    assert not (scratch / "virtual").exists()


def test_participant_timeout_is_typed_and_context_bound(modules, tmp_path: Path):
    _, participants, _, _, paths = modules
    executable = tmp_path / "participant"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)
    specification = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())
    specification["timeout_seconds"] = 1
    context, _ = participant_context(tmp_path / "context.json")

    def runner(argv, **_kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    result = participants.run_participant(specification, "preflight", context, runner)
    assert result.status == "failed"
    assert result.failure_kind == "timeout"
    assert result.timeout_seconds == 1
    assert "timed out" in result.blockers[0]


@pytest.mark.parametrize("defect", ["dirty-flake", "staged", "untracked", "conflicted", "sequencer", "upstream-drift"])
def test_git_authority_rejects_every_nontransactional_source_state(modules, tmp_path: Path, monkeypatch, defect):
    _, _, _, workflow_module, paths = modules
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "flake.nix").write_text("{}\n")
    write_fixture_lock(selected / "flake.lock")
    initialize_git_fixture(selected, tmp_path)
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    if defect == "dirty-flake":
        (selected / "flake.nix").write_text("{ dirty = true; }\n")
    elif defect == "staged":
        (selected / "flake.nix").write_text("{ staged = true; }\n")
        subprocess.run(["git", "add", "flake.nix"], cwd=selected, check=True)
    elif defect == "untracked":
        (selected / "untracked").write_text("fixture\n")
    elif defect == "conflicted":
        base = subprocess.run(
            ["git", "rev-parse", "HEAD:flake.nix"], cwd=selected, check=True,
            stdout=subprocess.PIPE, text=True,
        ).stdout.strip()
        ours = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"], cwd=selected, check=True,
            input="{ ours = true; }\n", stdout=subprocess.PIPE, text=True,
        ).stdout.strip()
        theirs = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"], cwd=selected, check=True,
            input="{ theirs = true; }\n", stdout=subprocess.PIPE, text=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "update-index", "--index-info"], cwd=selected, check=True, text=True,
            input=f"100644 {base} 1\tflake.nix\n100644 {ours} 2\tflake.nix\n100644 {theirs} 3\tflake.nix\n",
        )
    elif defect == "sequencer":
        git_dir = Path(subprocess.run(
            ["git", "rev-parse", "--git-dir"], cwd=selected, check=True,
            stdout=subprocess.PIPE, text=True,
        ).stdout.strip())
        (selected / git_dir / "sequencer").mkdir()
    else:
        (selected / "flake.nix").write_text("{ committed = true; }\n")
        subprocess.run(["git", "add", "flake.nix"], cwd=selected, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Nix Update Test", "-c", "user.email=nix-update@example.invalid", "commit", "-m", "ahead"],
            cwd=selected, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    workflow = workflow_module.Workflow(
        tmp_path,
        lambda argv, cwd=None, **kwargs: subprocess.run(argv, cwd=cwd, **kwargs),
    )
    with pytest.raises(paths.ConfigError):
        from nix_darwin_local import repository_identity
        local = repository_identity.local_git_identity(workflow, selected)
        repository_identity.publication_git_identity(workflow, selected, local)


def test_lock_input_alias_and_follow_resolve_to_normalized_node(modules):
    _, _, _, workflow_module, _ = modules
    nodes = {
        "root": {"inputs": {"alias": ["owner", "actual"]}},
        "owner-node": {"inputs": {"actual": "normalized-node"}},
        "normalized-node": {"locked": {"narHash": "sha256-fixture="}},
    }
    nodes["root"]["inputs"]["owner"] = "owner-node"
    assert workflow_module._resolve_lock_input(nodes, "root", "root", ["alias"], set()) == "normalized-node"


def test_lock_install_postrename_fsync_failure_restores_exact_preimage_and_index(modules, tmp_path: Path, monkeypatch):
    _, _, _, workflow_module, _ = modules
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "flake.nix").write_text("{}\n")
    write_fixture_lock(selected / "flake.lock", "sha256-before=")
    initialize_git_fixture(selected, tmp_path)
    before = (selected / "flake.lock").read_bytes()
    index_before = subprocess.run(
        ["git", "ls-files", "--stage", "-z"], cwd=selected, check=True, stdout=subprocess.PIPE,
    ).stdout
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    original_fsync = workflow_module._fsync
    failures = {"selected": 0}

    def injected_fsync(path):
        if Path(path) == selected and failures["selected"] == 0:
            failures["selected"] += 1
            raise OSError("injected post-rename directory fsync failure")
        return original_fsync(path)

    monkeypatch.setattr(workflow_module, "_fsync", injected_fsync)

    def runner(argv, cwd=None, **kwargs):
        if argv[0] == "git":
            return subprocess.run(argv, cwd=cwd, **kwargs)
        write_fixture_lock(Path(cwd) / "flake.lock", "sha256-after=")
        return subprocess.CompletedProcess(argv, 0, "", "")

    evidence = tmp_path / "evidence"
    with pytest.raises(OSError, match="post-rename"):
        workflow_module.maintain_lock(
            workflow_module.Workflow(tmp_path, runner), selected, "all", [], [], evidence, True,
        )
    assert (selected / "flake.lock").read_bytes() == before
    assert json.loads((evidence / "install-transaction.json").read_text())["state"] == "preimage-restored"
    assert subprocess.run(
        ["git", "ls-files", "--stage", "-z"], cwd=selected, check=True, stdout=subprocess.PIPE,
    ).stdout == index_before


def test_copying_plan_directory_does_not_restore_consumed_authority(modules, tmp_path: Path, monkeypatch):
    _, _, plan, workflow_module, _ = modules
    artifact = plan.write_plan(tmp_path / "plan", plan_document(plan), "plan\n")
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(workflow_module, "_guard_processes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_target", lambda *_args: None)
    monkeypatch.setattr(workflow_module, "_verify_post_switch", lambda *_args: None)
    runner = lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "", "")
    assert workflow_module.apply(
        workflow_module.Workflow(tmp_path, runner), artifact.directory,
        f"APPLY NIX-UPDATE PLAN {artifact.digest}",
    ) == 0

    copied = tmp_path / "copied-plan"
    copied.mkdir(mode=0o700)
    for name in ("plan.json", "plan.md"):
        destination = copied / name
        destination.write_bytes((artifact.directory / name).read_bytes())
        destination.chmod(0o600)
    attempted = []
    assert workflow_module.apply(
        workflow_module.Workflow(tmp_path, lambda argv, **_kwargs: attempted.append(argv)), copied,
        f"APPLY NIX-UPDATE PLAN {artifact.digest}",
    ) == 1
    assert attempted == []
    result = json.loads(next((copied / "results").rglob("result.json")).read_text())
    assert result["terminal_state"] == "REPLAY_BLOCKED"


def test_command_evidence_is_bounded_redacted_digest_bound_and_result_sized(modules, tmp_path: Path, monkeypatch, capsys):
    _, _, plan, workflow_module, _ = modules
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(workflow_module, "COMMAND_RAW_LIMIT", 32)
    monkeypatch.setattr(workflow_module, "COMMAND_SUMMARY_LIMIT", 24)
    stdout = "token=fixture-value " + "x" * 200
    stderr = "password:fixture-value " + "y" * 200
    workflow = workflow_module.Workflow(
        tmp_path,
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 17, stdout, stderr),
    )
    result = workflow.command("oversized failing child", ["fixture-child"], tmp_path)
    record = workflow.records[0]
    assert result.returncode == 17
    assert record.stdout["size"] == len(stdout.encode())
    assert record.stderr["size"] == len(stderr.encode())
    assert record.stdout["overflow"] is True and record.stderr["overflow"] is True
    assert Path(record.stdout["path"]).stat().st_size == 32
    assert Path(record.stderr["path"]).stat().st_size == 32
    assert record.stdout["sha256"] == hashlib.sha256(stdout.encode()).hexdigest()
    assert record.stderr["sha256"] == hashlib.sha256(stderr.encode()).hexdigest()
    assert "fixture-value" not in record.stdout["summary"]
    assert "fixture-value" not in record.stderr["summary"]
    assert len(record.stdout["summary"]) <= 24 and len(record.stderr["summary"]) <= 24
    canonical_record = plan.canonical_bytes({"commands": [record.__dict__]})
    assert len(canonical_record) < 4096
    captured = capsys.readouterr()
    assert captured.out.startswith("nix-update: running oversized failing child\n")


def test_target_aware_jaca_unknown_state_blocks_apply_before_confirmation(modules, tmp_path: Path, monkeypatch, capsys):
    _, _, plan, workflow_module, _ = modules
    executable = tmp_path / "jaca-participant"
    jaca_state = tmp_path / "jaca-state.json"
    executable.write_text(
        f"#!{Path(sys.executable).resolve()}\n"
        "import hashlib,json,sys\n"
        "from pathlib import Path\n"
        "a=sys.argv; phase=a[a.index('--phase')+1]; cp=Path(a[a.index('--context')+1])\n"
        f"ctx=json.loads(cp.read_text()); state=json.loads(Path({str(jaca_state)!r}).read_text())\n"
        "target=ctx['target']['output']; stale=state['daemon'] not in {'stopped','running'} or state['resident_target'] != target\n"
        "doc={'blockers':['daemon or resident state is unknown or stale'] if stale else [],'context_digest':hashlib.sha256(cp.read_bytes()).hexdigest(),'name':'resident-reconciler','observations':{'daemon':state['daemon'],'resident_target':state['resident_target'],'target':target},'phase':phase,'protocol':'nix-update-participant-v1','status':'blocked' if stale else 'ok'}\n"
        "print(json.dumps(doc,sort_keys=True,separators=(',',':'))); sys.exit(20 if stale else 0)\n"
    )
    executable.chmod(0o700)
    jaca_state.write_text(
        json.dumps({"daemon": "unknown", "resident_target": "/nix/store/stale-system"}) + "\n"
    )
    specification = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())
    specification["phases"] = ["preflight"]
    specification["mutation_authority"] = []
    specification["rollback_authority"] = []
    document = plan_document(plan)
    document["participants"] = [specification]
    jaca_policy = {"name": "jaca-daemon-resident", "kind": "file", "path": str(jaca_state)}
    document["external_state"] = [{
        "name": "jaca-daemon-resident", "kind": "file", "policy": jaca_policy,
        "identity": plan.file_identity(jaca_state),
    }]
    rebind_participant_context(plan, document)
    artifact = plan.write_plan(tmp_path / "plan", document, "plan\n")
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(workflow_module, "_guard_processes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_target", lambda *_args: None)
    monkeypatch.setattr(workflow_module, "_profile_snapshot", lambda *_args: document["live"]["profile"])
    switch_attempts = []

    def runner(argv, **_kwargs):
        if str(argv[0]) == str(executable):
            return subprocess.run(argv, **_kwargs)
        switch_attempts.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert workflow_module.apply(workflow_module.Workflow(tmp_path, runner), artifact.directory, None) == 1
    assert switch_attempts == []
    assert "Type exactly:" not in capsys.readouterr().err
    result = json.loads(next((artifact.directory / "results").rglob("result.json")).read_text())
    assert "unknown or stale" in result["failure"]
    contexts = [json.loads(path.read_text()) for path in (state_root / "participant-contexts").glob("*.json")]
    assert contexts
    assert contexts[0]["external_state"][0]["name"] == "jaca-daemon-resident"
    assert contexts[0]["external_state"][0]["identity"]["sha256"] == hashlib.sha256(jaca_state.read_bytes()).hexdigest()


def test_apply_uses_fresh_target_context_before_confirmation_and_immediate_sudo(modules, tmp_path: Path, monkeypatch):
    _, _, plan, workflow_module, _ = modules
    executable = tmp_path / "jaca-participant"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)
    specification = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())
    specification["phases"] = ["preflight"]
    specification["mutation_authority"] = []
    specification["rollback_authority"] = []
    document = plan_document(plan)
    document["participants"] = [specification]
    rebind_participant_context(plan, document)
    artifact = plan.write_plan(tmp_path / "plan", document, "plan\n")
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(workflow_module, "_guard_processes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_plan", lambda *_args, **_kwargs: None)
    target_revalidations = []
    monkeypatch.setattr(
        workflow_module, "_revalidate_target",
        lambda *_args: target_revalidations.append("target"),
    )
    monkeypatch.setattr(workflow_module, "_verify_post_switch", lambda *_args: None)
    monkeypatch.setattr(workflow_module, "_profile_snapshot", lambda *_args: document["live"]["profile"])
    contexts = []

    def runner(argv, **_kwargs):
        if str(argv[0]) == str(executable):
            context = Path(argv[argv.index("--context") + 1])
            contexts.append(json.loads(context.read_text()))
            return subprocess.CompletedProcess(
                argv, 0,
                participant_output("preflight", context_digest=hashlib.sha256(context.read_bytes()).hexdigest()),
                "",
            )
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert workflow_module.apply(
        workflow_module.Workflow(tmp_path, runner), artifact.directory,
        f"APPLY NIX-UPDATE PLAN {artifact.digest}",
    ) == 0
    assert [item["stage"] for item in contexts] == [
        "apply-pre-confirmation", "apply-final-system-switch",
    ]
    assert all(item["target"]["output"] == "/nix/store/fixture-system" for item in contexts)
    assert contexts[0]["plan_draft_identity"] != contexts[1]["plan_draft_identity"]
    assert len(target_revalidations) == 3


def test_owner_only_path_primitives_reject_without_repairing_untrusted_objects(modules, tmp_path: Path):
    _, _, plan, _, paths = modules
    from nix_darwin_local import deployment_paths

    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o755)
    unsafe.chmod(0o755)
    with pytest.raises(paths.ConfigError, match="owner-only"):
        deployment_paths.require_private_directory(unsafe, "unsafe fixture")
    assert unsafe.stat().st_mode & 0o777 == 0o755

    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    target = private / "target"
    target.write_text("preserve\n")
    target.chmod(0o600)
    link = private / "link"
    link.symlink_to(target)
    with pytest.raises(paths.ConfigError):
        deployment_paths.atomic_private_write(link, b"replace\n")
    assert target.read_text() == "preserve\n"

    hardlink = private / "hardlink"
    os.link(target, hardlink)
    with pytest.raises(paths.ConfigError, match="single-link"):
        deployment_paths.require_private_file(target, "hardlinked fixture")

    with pytest.raises(paths.ConfigError, match="owner-only"):
        plan.write_result(unsafe, {"terminal_state": "FAILED_PRE_SWITCH"})
    assert unsafe.stat().st_mode & 0o777 == 0o755

    with pytest.raises(paths.ConfigError, match="outside its reviewed root"):
        deployment_paths.validate_ancestor_chain(private, start=tmp_path / "elsewhere")
    with pytest.raises(paths.ConfigError, match="ancestor is not a directory"):
        deployment_paths.validate_ancestor_chain(target / "child", start=tmp_path)
    with pytest.raises(paths.ConfigError, match="ancestor is not owner-only"):
        deployment_paths.validate_ancestor_chain(unsafe / "child", start=tmp_path, owner_only=True)
    with pytest.raises(paths.ConfigError, match="is missing"):
        deployment_paths.require_private_directory(tmp_path / "missing", "missing fixture")
    with pytest.raises(paths.ConfigError, match="not empty"):
        deployment_paths.require_private_directory(private, "nonempty fixture", empty=True)
    with pytest.raises(paths.ConfigError, match="already exists"):
        deployment_paths.create_private_directory(private, "existing fixture")
    with pytest.raises(paths.ConfigError, match="parent is unsafe"):
        deployment_paths.create_private_directory(target / "child", "file-parent fixture", parent=target)
    with pytest.raises(paths.ConfigError, match="ancestor is unsafe"):
        deployment_paths.ensure_private_root(target / "nested/root", "file-ancestor fixture")
    writable = tmp_path / "writable"
    writable.mkdir(mode=0o777)
    writable.chmod(0o777)
    with pytest.raises(paths.ConfigError, match="ancestor is unsafe"):
        deployment_paths.ensure_private_root(writable / "nested/root", "writable-ancestor fixture")
    assert not (writable / "nested").exists()
    ordinary = private / "ordinary"
    ordinary.write_text("old\n")
    ordinary.chmod(0o600)
    deployment_paths.atomic_private_write(ordinary, b"new\n")
    assert ordinary.read_bytes() == b"new\n"
    assert deployment_paths.ensure_private_root(private, "existing private root") == private


@pytest.mark.parametrize("mode", [0o720, 0o702, 0o777, 0o1700])
def test_create_private_directory_rejects_unsafe_reviewed_parent_without_mutation(
    modules, tmp_path: Path, mode: int,
):
    _, _, _, _, paths = modules
    from nix_darwin_local import deployment_paths

    parent = tmp_path / "parent"
    parent.mkdir(mode=0o700)
    parent.chmod(mode)
    child = parent / "child"
    with pytest.raises(paths.ConfigError, match="parent"):
        deployment_paths.create_private_directory(child, "fixture", parent=parent)
    assert not child.exists() and not child.is_symlink()
    assert stat.S_IMODE(parent.lstat().st_mode) == mode


@pytest.mark.parametrize("defect", ["symlink", "foreign", "wrong-kind", "wrong-parent", "unnormalized"])
def test_create_private_directory_closes_parent_identity_and_root_boundary(
    modules, tmp_path: Path, monkeypatch, defect: str,
):
    _, _, _, _, paths = modules
    from nix_darwin_local import deployment_paths

    safe = tmp_path / "safe"
    safe.mkdir(mode=0o700)
    reviewed_parent = safe
    child = safe / "child"
    if defect == "symlink":
        target = tmp_path / "target"
        target.mkdir(mode=0o700)
        reviewed_parent = tmp_path / "alias"
        reviewed_parent.symlink_to(target, target_is_directory=True)
        child = reviewed_parent / "child"
    elif defect == "foreign":
        current_uid = os.getuid()
        monkeypatch.setattr(deployment_paths.os, "getuid", lambda: current_uid + 1)
    elif defect == "wrong-kind":
        reviewed_parent = tmp_path / "file"
        reviewed_parent.write_text("not a directory")
        reviewed_parent.chmod(0o700)
        child = reviewed_parent / "child"
    elif defect == "wrong-parent":
        reviewed_parent = tmp_path
    else:
        child = safe / "nested" / ".." / "child"
    with pytest.raises(paths.ConfigError):
        deployment_paths.create_private_directory(child, "fixture", parent=reviewed_parent)
    assert not (safe / "child").exists()


def _operator_source_spec(path: Path) -> dict:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    specification = participant_spec(path, digest)
    specification["provenance"] = {
        "kind": "operator-artifact", "identity": f"sha256:{digest}", "root": str(path.parent),
    }
    return specification


@pytest.mark.parametrize("defect", ["writable-parent", "symlink-ancestor", "wrong-root", "escape", "hardlink"])
def test_operator_participant_freeze_rejects_unsafe_source_provenance(
    modules, tmp_path: Path, defect: str,
):
    _, participants, _, _, paths = modules
    source_root = tmp_path / "source"
    source_root.mkdir(mode=0o700)
    parent = source_root / "nested"
    parent.mkdir(mode=0o700)
    executable = parent / "participant"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    specification = _operator_source_spec(executable)
    if defect == "writable-parent":
        parent.chmod(0o770)
    elif defect == "symlink-ancestor":
        alias = source_root / "alias"
        alias.symlink_to(parent, target_is_directory=True)
        specification["path"] = str(alias / executable.name)
        specification["provenance"]["root"] = str(source_root)
    elif defect == "wrong-root":
        specification["provenance"]["root"] = str(source_root / "other")
    elif defect == "escape":
        specification["path"] = str(source_root / "nested" / ".." / "outside")
    else:
        linked = parent / "linked"
        os.link(executable, linked)
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    with pytest.raises(paths.ConfigError):
        participants.freeze_operator_artifacts([specification], state_root)


def test_operator_participant_freeze_detects_byte_swap_during_capture(
    modules, tmp_path: Path, monkeypatch,
):
    _, participants, _, _, paths = modules
    source_root = tmp_path / "source"
    source_root.mkdir(mode=0o700)
    executable = source_root / "participant"
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    specification = _operator_source_spec(executable)
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    original_read = participants.os.read
    injected = {"done": False}

    def raced_read(descriptor, size):
        if not injected["done"]:
            injected["done"] = True
            with executable.open("r+b", buffering=0) as stream:
                stream.write(b"#!/bin/sh\nexit 1\n")
        return original_read(descriptor, size)

    monkeypatch.setattr(participants.os, "read", raced_read)
    with pytest.raises(paths.ConfigError, match="SHA-256 drift"):
        participants.freeze_operator_artifacts([specification], state_root)


def test_operator_participant_executes_only_frozen_sha_bound_copy(modules, tmp_path: Path):
    _, participants, _, _, _ = modules
    source_root = tmp_path / "source"
    source_root.mkdir(mode=0o700)
    executable = source_root / "participant"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    frozen = participants.freeze_operator_artifacts([_operator_source_spec(executable)], state_root)[0]
    assert frozen["path"] != str(executable)
    assert frozen["provenance"]["source_path"] == str(executable)
    executable.write_text("#!/bin/sh\nexit 99\n")
    executable.chmod(0o700)
    context, _ = participant_context(tmp_path / "context.json", [frozen], "preflight")
    invoked = []

    def runner(argv, **_kwargs):
        invoked.append(argv)
        assert Path(argv[0]).read_text() == "#!/bin/sh\nexit 0\n"
        return subprocess.CompletedProcess(
            argv, 0,
            participant_output("preflight", context_digest=participant_context_digest(argv)), "",
        )

    result = participants.run_participant(frozen, "preflight", context, runner)
    assert result.status == "ok"
    assert invoked[0][0] == frozen["provenance"]["frozen_path"]


def test_operator_participant_frozen_copy_executes_through_actual_bounded_spawn(modules, tmp_path: Path):
    _, participants, _, _, _ = modules
    source_root = tmp_path / "source"
    source_root.mkdir(mode=0o700)
    executable = source_root / "participant"
    executable.write_text(
        f"#!{Path(sys.executable).resolve()}\n"
        "import hashlib, json, pathlib, sys\n"
        "phase = sys.argv[sys.argv.index('--phase') + 1]\n"
        "context = pathlib.Path(sys.argv[sys.argv.index('--context') + 1])\n"
        "print(json.dumps({'protocol':'nix-update-participant-v1','name':'resident-reconciler',"
        "'phase':phase,'status':'ok','blockers':[],'observations':{},"
        "'context_digest':hashlib.sha256(context.read_bytes()).hexdigest()}, sort_keys=True, separators=(',', ':')))\n"
    )
    executable.chmod(0o700)
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    frozen = participants.freeze_operator_artifacts([_operator_source_spec(executable)], state_root)[0]
    context, _ = participant_context(tmp_path / "context.json", [frozen], "preflight")
    result = participants.run_participant(frozen, "preflight", context)
    assert result.status == "ok"
    assert Path(frozen["path"]).parent == state_root / "participant-artifacts"


def test_operator_participant_exec_descriptor_survives_path_swap_at_spawn(
    modules, tmp_path: Path, monkeypatch,
):
    _, participants, _, _, _ = modules
    source_root = tmp_path / "source"
    source_root.mkdir(mode=0o700)
    executable = source_root / "participant"
    executable.write_text(
        f"#!{Path(sys.executable).resolve()}\n"
        "import hashlib, json, pathlib, sys\n"
        "phase = sys.argv[sys.argv.index('--phase') + 1]\n"
        "context = pathlib.Path(sys.argv[sys.argv.index('--context') + 1])\n"
        "print(json.dumps({'protocol':'nix-update-participant-v1','name':'resident-reconciler',"
        "'phase':phase,'status':'ok','blockers':[],'observations':{'copy':'reviewed'},"
        "'context_digest':hashlib.sha256(context.read_bytes()).hexdigest()}, sort_keys=True, separators=(',', ':')))\n"
    )
    executable.chmod(0o700)
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    frozen = participants.freeze_operator_artifacts(
        [_operator_source_spec(executable)], state_root
    )[0]
    frozen_path = Path(frozen["path"])
    replacement = frozen_path.with_name("replacement")
    replacement.write_text("#!/bin/sh\nexit 99\n")
    replacement.chmod(0o700)
    context, _ = participant_context(tmp_path / "context.json", [frozen], "preflight")
    original_popen = participants.subprocess.Popen
    swapped = {"done": False}

    def raced_popen(*args, **kwargs):
        if not swapped["done"]:
            swapped["done"] = True
            os.replace(replacement, frozen_path)
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(participants.subprocess, "Popen", raced_popen)
    result = participants.run_participant(frozen, "preflight", context)
    assert result.status == "ok"
    assert result.observations == {"copy": "reviewed"}
    assert frozen_path.read_text() == "#!/bin/sh\nexit 99\n"


def test_operator_participant_rejects_non_shebang_frozen_program(modules, tmp_path: Path):
    _, participants, _, _, paths = modules
    source_root = tmp_path / "source"
    source_root.mkdir(mode=0o700)
    executable = source_root / "participant"
    executable.write_bytes(b"not-a-shebang-program\n")
    executable.chmod(0o700)
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    frozen = participants.freeze_operator_artifacts(
        [_operator_source_spec(executable)], state_root
    )[0]
    context, _ = participant_context(tmp_path / "context.json", [frozen], "preflight")
    with pytest.raises(paths.ConfigError, match="bounded shebang program"):
        participants.run_participant(frozen, "preflight", context)


def test_operator_participant_rejects_path_selected_shebang_dispatch(modules, tmp_path: Path):
    _, participants, _, _, paths = modules
    source_root = tmp_path / "source"
    source_root.mkdir(mode=0o700)
    executable = source_root / "participant"
    executable.write_text("#!/usr/bin/env python3\n")
    executable.chmod(0o700)
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    frozen = participants.freeze_operator_artifacts(
        [_operator_source_spec(executable)], state_root
    )[0]
    context, _ = participant_context(tmp_path / "context.json", [frozen], "preflight")
    with pytest.raises(paths.ConfigError, match="dispatch target must be an exact absolute path"):
        participants.run_participant(frozen, "preflight", context)


@pytest.mark.parametrize("swap", ["inode", "bytes"])
def test_operator_participant_revalidates_frozen_identity_immediately_before_spawn(
    modules, tmp_path: Path, swap: str,
):
    _, participants, _, _, paths = modules
    source_root = tmp_path / "source"
    source_root.mkdir(mode=0o700)
    executable = source_root / "participant"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    frozen = participants.freeze_operator_artifacts([_operator_source_spec(executable)], state_root)[0]
    frozen_path = Path(frozen["path"])
    if swap == "inode":
        replacement = frozen_path.with_name("replacement")
        replacement.write_bytes(frozen_path.read_bytes())
        replacement.chmod(0o700)
        os.replace(replacement, frozen_path)
    else:
        frozen_path.write_text("#!/bin/sh\nexit 1\n")
        frozen_path.chmod(0o700)
    context, _ = participant_context(tmp_path / "context.json", [frozen], "preflight")
    invoked = []
    with pytest.raises(paths.ConfigError, match="frozen participant identity drift"):
        participants.run_participant(frozen, "preflight", context, lambda argv, **_kwargs: invoked.append(argv))
    assert invoked == []


def test_atomic_private_write_does_not_unlink_after_completed_replace(modules, tmp_path: Path, monkeypatch):
    from nix_darwin_local import deployment_paths

    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    destination = directory / "result.json"
    destination.write_text("old\n")
    destination.chmod(0o600)
    original_unlink = Path.unlink

    def reject_temporary_unlink(path, *args, **kwargs):
        if path.name.startswith(".result.json."):
            raise OSError("injected cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", reject_temporary_unlink)
    deployment_paths.atomic_private_write(destination, b"new\n")
    assert destination.read_bytes() == b"new\n"


def test_apply_rejects_preexisting_unsafe_result_root_without_repair(modules, tmp_path: Path):
    _, _, plan, workflow_module, paths = modules
    artifact = plan.write_plan(tmp_path / "plan", plan_document(plan), "plan\n")
    result_root = artifact.directory / "results"
    result_root.mkdir(mode=0o755)
    result_root.chmod(0o755)

    with pytest.raises(paths.ConfigError, match="owner-only"):
        workflow_module.apply(workflow_module.Workflow(tmp_path), artifact.directory, None)
    assert result_root.stat().st_mode & 0o777 == 0o755
    assert not list(result_root.iterdir())


@pytest.mark.parametrize("defect", ["target-provenance", "oversized", "wrong-context"])
def test_participant_provenance_output_bound_and_context_faults(modules, tmp_path: Path, defect):
    _, participants, _, _, paths = modules
    executable = tmp_path / "participant"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)
    specification = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())
    context, context_digest = participant_context(tmp_path / "context.json")
    if defect == "target-provenance":
        specification["provenance"] = {"kind": "target-closure", "identity": "/nix/store/fixture-system"}
        with pytest.raises(paths.ConfigError, match="immutable store executable"):
            participants.validate_executable(specification)
        return

    def runner(argv, **_kwargs):
        if defect == "oversized":
            return subprocess.CompletedProcess(argv, 0, "x" * 65537, "")
        return subprocess.CompletedProcess(
            argv, 0, participant_output("preflight", context_digest="0" * 64), "",
        )

    with pytest.raises(paths.ConfigError, match="exceeds|wrong context"):
        participants.run_participant(specification, "preflight", context, runner)


def test_second_apply_lock_race_is_a_structured_terminal_failure(modules, tmp_path: Path, monkeypatch):
    _, _, plan, workflow_module, paths = modules
    artifact = plan.write_plan(tmp_path / "plan", plan_document(plan), "plan\n")
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(workflow_module, "_guard_processes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_module, "_revalidate_target", lambda *_args: None)
    enters = {"count": 0}

    class RacingLock:
        def __init__(self, _path): pass
        def __enter__(self):
            enters["count"] += 1
            if enters["count"] == 2:
                raise paths.ConfigError("injected second lock race")
            return self
        def __exit__(self, *_args): return None

    monkeypatch.setattr(workflow_module, "DeploymentLock", RacingLock)
    assert workflow_module.apply(
        workflow_module.Workflow(tmp_path, lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "", "")),
        artifact.directory,
        f"APPLY NIX-UPDATE PLAN {artifact.digest}",
    ) == 1
    result = json.loads(next((artifact.directory / "results").rglob("result.json")).read_text())
    assert result["terminal_state"] == "FAILED_PRE_SWITCH"
    assert "second lock race" in result["failure"]


def test_result_evidence_failure_writes_owner_only_fallback(modules, tmp_path: Path, monkeypatch):
    _, _, _, workflow_module, _ = modules
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    result_directory = tmp_path / "result"
    result_directory.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(workflow_module, "write_result", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected evidence failure")))
    fsynced = []
    monkeypatch.setattr(workflow_module, "fsync_directory", lambda path: fsynced.append(Path(path)))
    status = workflow_module._finish_result(
        result_directory,
        {"plan_digest": "a" * 64, "terminal_state": "FAILED_PRE_SWITCH", "switch_invocation_started": False},
        workflow_module.Workflow(tmp_path),
        [],
        1,
    )
    assert status == 1
    fallback = next((state_root / "failure-results").glob("*.json"))
    assert fallback.stat().st_mode & 0o777 == 0o600
    assert json.loads(fallback.read_text())["terminal_state"] == "EVIDENCE_WRITE_FAILURE"
    assert state_root / "failure-results" in fsynced


def test_closure_inventory_reads_complete_digest_bound_evidence_beyond_summary(modules, tmp_path: Path, monkeypatch):
    _, _, _, workflow_module, _ = modules
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    closure = [
        {"path": f"/nix/store/{index:05d}-fixture", "narHash": f"sha256-{index}=", "narSize": index}
        for index in range(300)
    ]
    raw = json.dumps(closure) + "\n"
    assert len(raw) > workflow_module.COMMAND_SUMMARY_LIMIT
    workflow = workflow_module.Workflow(
        tmp_path, lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, raw, "")
    )
    assert workflow_module.closure_inventory(workflow, tmp_path, "/nix/store/target") == closure
    assert workflow.records[-1].stdout["size"] == len(raw.encode())


def test_closure_inventory_accepts_path_keyed_nix_json(modules, tmp_path: Path):
    _, _, _, workflow_module, _ = modules
    raw = json.dumps(
        {
            "/nix/store/zzzzz-fixture": {
                "narHash": "sha256-z=",
                "narSize": 2,
                "references": [],
            },
            "/nix/store/aaaaa-fixture": {
                "narHash": "sha256-a=",
                "narSize": 1,
                "references": [],
            },
        }
    )
    workflow = workflow_module.Workflow(
        tmp_path, lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, raw, "")
    )
    assert workflow_module.closure_inventory(workflow, tmp_path, "/nix/store/target") == [
        {"path": "/nix/store/aaaaa-fixture", "narHash": "sha256-a=", "narSize": 1},
        {"path": "/nix/store/zzzzz-fixture", "narHash": "sha256-z=", "narSize": 2},
    ]


def test_semantic_output_overflow_fails_closed_with_retained_digest(modules, tmp_path: Path, monkeypatch):
    _, _, _, workflow_module, paths = modules
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(workflow_module, "COMMAND_RAW_LIMIT", 64)
    workflow = workflow_module.Workflow(
        tmp_path, lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "[" + " " * 200 + "]", "")
    )
    with pytest.raises(paths.ConfigError, match="semantic limit"):
        workflow_module.closure_inventory(workflow, tmp_path, "/nix/store/target")
    assert workflow.records[-1].stdout["overflow"] is True


def test_git_identity_uses_configured_non_origin_upstream(modules, tmp_path: Path):
    from nix_darwin_local import repository_identity
    _, _, _, workflow_module, _ = modules
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "flake.nix").write_text("{}\n")
    write_fixture_lock(selected / "flake.lock")
    initialize_git_fixture(selected, tmp_path)
    subprocess.run(["git", "remote", "rename", "origin", "reviewed"], cwd=selected, check=True)
    workflow = workflow_module.Workflow(tmp_path, lambda argv, cwd=None, **kwargs: subprocess.run(argv, cwd=cwd, **kwargs))
    local = repository_identity.local_git_identity(workflow, selected)
    identity = repository_identity.publication_git_identity(workflow, selected, local)
    assert identity["remote_name"] == "reviewed"
    assert identity["upstream"] == "reviewed/main"
    assert identity["remote_ref"] == "refs/heads/main"


def test_local_git_identity_tolerates_and_digest_binds_ignored_state_and_rejects_untracked(modules, tmp_path: Path):
    from nix_darwin_local import repository_identity
    _, _, _, workflow_module, paths = modules
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "flake.nix").write_text("{}\n")
    write_fixture_lock(selected / "flake.lock")
    (selected / ".gitignore").write_text("ignored-state\n")
    initialize_git_fixture(selected, tmp_path)
    (selected / "ignored-state").write_text("bound but not source-visible\n")
    workflow = workflow_module.Workflow(
        tmp_path, lambda argv, cwd=None, **kwargs: subprocess.run(argv, cwd=cwd, **kwargs)
    )

    identity = repository_identity.local_git_identity(workflow, selected)

    assert identity["status"] == ""
    assert identity["ignored_status_count"] == "1"
    assert len(identity["ignored_status_sha256"]) == 64

    (selected / "untracked").write_text("untracked still blocks\n")
    with pytest.raises(paths.ConfigError, match="not clean"):
        repository_identity.local_git_identity(workflow, selected)


def test_lock_install_repeated_directory_fsync_failure_records_rollback_failed(modules, tmp_path: Path, monkeypatch):
    _, _, _, workflow_module, paths = modules
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "flake.nix").write_text("{}\n")
    write_fixture_lock(selected / "flake.lock", "sha256-before=")
    initialize_git_fixture(selected, tmp_path)
    before = (selected / "flake.lock").read_bytes()
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    original_fsync = workflow_module._fsync

    def always_fail_selected(path):
        if Path(path) == selected:
            raise OSError("injected repeated directory fsync failure")
        return original_fsync(path)

    monkeypatch.setattr(workflow_module, "_fsync", always_fail_selected)

    def runner(argv, cwd=None, **kwargs):
        if argv[0] == "git":
            return subprocess.run(argv, cwd=cwd, **kwargs)
        write_fixture_lock(Path(cwd) / "flake.lock", "sha256-after=")
        return subprocess.CompletedProcess(argv, 0, "", "")

    evidence = tmp_path / "evidence"
    with pytest.raises(paths.ConfigError, match="restoration failed"):
        workflow_module.maintain_lock(
            workflow_module.Workflow(tmp_path, runner), selected, "all", [], [], evidence, True,
        )
    assert (selected / "flake.lock").read_bytes() == before
    assert json.loads((evidence / "install-transaction.json").read_text())["state"] == "rollback-failed"


def test_lock_install_terminal_journal_failure_rolls_back_exact_mode(modules, tmp_path: Path, monkeypatch):
    _, _, _, workflow_module, paths = modules
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "flake.nix").write_text("{}\n")
    write_fixture_lock(selected / "flake.lock", "sha256-before=")
    initialize_git_fixture(selected, tmp_path)
    (selected / "flake.lock").chmod(0o600)
    before = (selected / "flake.lock").read_bytes()
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    original_journal = workflow_module._write_lock_journal
    failed = {"installed": False}

    def fail_installed(journal, base, state, error=None):
        if state == "installed" and not failed["installed"]:
            failed["installed"] = True
            raise OSError("injected terminal journal failure")
        return original_journal(journal, base, state, error)

    monkeypatch.setattr(workflow_module, "_write_lock_journal", fail_installed)

    def runner(argv, cwd=None, **kwargs):
        if argv[0] == "git":
            return subprocess.run(argv, cwd=cwd, **kwargs)
        write_fixture_lock(Path(cwd) / "flake.lock", "sha256-after=")
        return subprocess.CompletedProcess(argv, 0, "", "")

    evidence = tmp_path / "evidence"
    with pytest.raises(paths.ConfigError, match="terminal evidence failed"):
        workflow_module.maintain_lock(
            workflow_module.Workflow(tmp_path, runner), selected, "all", [], [], evidence, True,
        )
    assert (selected / "flake.lock").read_bytes() == before
    assert (selected / "flake.lock").stat().st_mode & 0o777 == 0o600
    assert json.loads((evidence / "install-transaction.json").read_text())["state"] == "preimage-restored"


def test_interrupted_lock_install_is_recovered_before_clean_git_gate(modules, tmp_path: Path, monkeypatch):
    _, _, _, workflow_module, paths = modules
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "flake.nix").write_text("{}\n")
    write_fixture_lock(selected / "flake.lock", "sha256-before=")
    initialize_git_fixture(selected, tmp_path)
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    git_runner = lambda argv, cwd=None, **kwargs: subprocess.run(argv, cwd=cwd, **kwargs)
    authority = workflow_module.local_git_identity(workflow_module.Workflow(tmp_path, git_runner), selected)
    preimage = (selected / "flake.lock").read_bytes()
    metadata = workflow_module._lock_metadata(selected / "flake.lock")
    candidate_path = tmp_path / "candidate.lock"
    write_fixture_lock(candidate_path, "sha256-after=")
    candidate = candidate_path.read_bytes()
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    for name, content in (("flake.lock.preimage", preimage), ("flake.lock.candidate", candidate)):
        path = evidence / name
        path.write_bytes(content)
        path.chmod(0o600)
    journal = {
        "schema": "nix-update-lock-install-v1", "state": "rename-pending",
        "source_authority": authority, "selected_path": str(selected.absolute()),
        "preimage_sha256": hashlib.sha256(preimage).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate).hexdigest(), "preimage_metadata": metadata,
    }
    (evidence / "install-transaction.json").write_bytes(workflow_module.canonical_bytes(journal))
    (evidence / "install-transaction.json").chmod(0o600)
    (selected / "flake.lock").write_bytes(candidate)
    (selected / "flake.lock").chmod(metadata["mode"])

    with pytest.raises(paths.ConfigError, match="recovered an interrupted"):
        workflow_module.maintain_lock(
            workflow_module.Workflow(tmp_path, git_runner), selected, "all", [], [], evidence, True,
        )
    assert (selected / "flake.lock").read_bytes() == preimage
    assert json.loads((evidence / "install-transaction.json").read_text())["state"] == "preimage-restored"


@pytest.mark.parametrize(
    "operation",
    [
        "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG", "BISECT_START",
        "BISECT_HEAD", "rebase-merge", "rebase-apply", "sequencer",
    ],
)
def test_git_identity_rejects_each_sequencer_and_bisect_marker(modules, tmp_path: Path, operation):
    _, _, _, workflow_module, paths = modules
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "flake.nix").write_text("{}\n")
    write_fixture_lock(selected / "flake.lock")
    initialize_git_fixture(selected, tmp_path)
    git_path = subprocess.run(
        ["git", "rev-parse", "--git-path", operation], cwd=selected, check=True,
        stdout=subprocess.PIPE, text=True,
    ).stdout.strip()
    marker = Path(git_path)
    if not marker.is_absolute():
        marker = selected / marker
    if operation in {"rebase-merge", "rebase-apply", "sequencer"}:
        marker.mkdir(parents=True)
    else:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("fixture\n")
    with pytest.raises(paths.ConfigError, match="active Git operation"):
        workflow_module.local_git_identity(
            workflow_module.Workflow(tmp_path, lambda argv, cwd=None, **kwargs: subprocess.run(argv, cwd=cwd, **kwargs)),
            selected,
        )


@pytest.mark.parametrize("move_unrelated", [False, True])
def test_targeted_lock_resolves_alias_before_child_and_rejects_transitive_movement(
    modules, tmp_path: Path, monkeypatch, move_unrelated: bool
):
    _, _, _, workflow_module, paths = modules
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "flake.nix").write_text("{}\n")

    def alias_lock(path: Path, normalized_hash: str, unrelated_hash: str) -> None:
        path.write_text(json.dumps({
            "nodes": {
                "root": {"inputs": {"alias": ["owner", "actual"], "owner": "owner-node", "other": "other-node"}},
                "owner-node": {"inputs": {"actual": "normalized-node"}},
                "normalized-node": {"locked": {"narHash": normalized_hash}},
                "other-node": {"locked": {"narHash": unrelated_hash}},
            },
            "root": "root", "version": 7,
        }))

    alias_lock(selected / "flake.lock", "sha256-before=", "sha256-other=")
    initialize_git_fixture(selected, tmp_path)
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(workflow_module, "_private_state_root", lambda: state_root)
    nix_calls = []

    def runner(argv, cwd=None, **kwargs):
        if argv[0] == "git":
            return subprocess.run(argv, cwd=cwd, **kwargs)
        nix_calls.append(list(argv))
        alias_lock(
            Path(cwd) / "flake.lock", "sha256-after=",
            "sha256-unrelated-moved=" if move_unrelated else "sha256-other=",
        )
        return subprocess.CompletedProcess(argv, 0, "", "")

    evidence = tmp_path / "evidence"
    if move_unrelated:
        with pytest.raises(paths.ConfigError, match="unauthorized node"):
            workflow_module.maintain_lock(
                workflow_module.Workflow(tmp_path, runner), selected, "targeted", ["alias"], [], evidence, False,
            )
    else:
        assert workflow_module.maintain_lock(
            workflow_module.Workflow(tmp_path, runner), selected, "targeted", ["alias"], [], evidence, False,
        ) == 0
    assert nix_calls == [["nix", "flake", "update", "alias"]]
    delta = json.loads((evidence / "delta.json").read_text())
    assert "normalized-node" in delta["authorized_nodes"]


def test_default_participant_runner_is_bounded_and_context_attested(modules, tmp_path: Path):
    _, participants, _, _, _ = modules
    executable = tmp_path / "participant"
    executable.write_text(
        f"#!{Path(sys.executable).resolve()}\n"
        "import hashlib,json,sys\n"
        "from pathlib import Path\n"
        "a=sys.argv; p=Path(a[a.index('--context')+1]); phase=a[a.index('--phase')+1]\n"
        "d={'blockers':[],'context_digest':hashlib.sha256(p.read_bytes()).hexdigest(),'name':'resident-reconciler','observations':{'actual':True},'phase':phase,'protocol':'nix-update-participant-v1','status':'ok'}\n"
        "print(json.dumps(d,sort_keys=True,separators=(',',':')))\n"
    )
    executable.chmod(0o700)
    specification = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())
    context, digest = participant_context(tmp_path / "context.json")
    result = participants.run_participant(specification, "preflight", context)
    assert result.status == "ok"
    assert result.context_digest == digest
    assert result.observations == {"actual": True}


def test_default_participant_runner_bounds_stderr_and_timeout(modules, tmp_path: Path):
    _, participants, _, _, paths = modules
    context, _ = participant_context(tmp_path / "context.json")
    noisy = tmp_path / "noisy"
    noisy.write_text(
        f"#!{Path(sys.executable).resolve()}\n"
        "import sys\n"
        "sys.stderr.write('x'*70000)\n"
    )
    noisy.chmod(0o700)
    noisy_spec = participant_spec(noisy, hashlib.sha256(noisy.read_bytes()).hexdigest())
    with pytest.raises(paths.ConfigError, match="stderr exceeds"):
        participants.run_participant(noisy_spec, "preflight", context)

    sleeper = tmp_path / "sleeper"
    sleeper.write_text(f"#!{Path(sys.executable).resolve()}\nimport time\ntime.sleep(2)\n")
    sleeper.chmod(0o700)
    sleeper_spec = participant_spec(sleeper, hashlib.sha256(sleeper.read_bytes()).hexdigest())
    sleeper_spec["timeout_seconds"] = 1
    result = participants.run_participant(sleeper_spec, "preflight", context)
    assert result.failure_kind == "timeout"
    assert result.status == "failed"


def test_default_participant_timeout_kills_descendants_holding_pipes(modules, tmp_path: Path):
    _, participants, _, _, _ = modules
    context, _ = participant_context(tmp_path / "context.json")
    executable = tmp_path / "descendant-sleeper"
    executable.write_text(
        f"#!{Path(sys.executable).resolve()}\n"
        "import subprocess,sys,time\n"
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n"
        "time.sleep(30)\n"
    )
    executable.chmod(0o700)
    specification = participant_spec(executable, hashlib.sha256(executable.read_bytes()).hexdigest())
    specification["timeout_seconds"] = 1
    started = time.monotonic()
    result = participants.run_participant(specification, "preflight", context)
    assert result.failure_kind == "timeout"
    assert time.monotonic() - started < 4


@pytest.mark.parametrize(
    "defect",
    ["missing", "terminal", "json", "schema", "authority-type", "authority-mismatch", "metadata", "metadata-type", "digest", "unknown", "current-missing", "evidence-missing"],
)
def test_interrupted_lock_recovery_contract_fails_closed(modules, tmp_path: Path, defect):
    _, _, _, workflow_module, paths = modules
    output = tmp_path / "evidence"
    output.mkdir(mode=0o700)
    selected = tmp_path / "selected"
    selected.mkdir()
    preimage = b'{"version":7,"nodes":{}}\n'
    candidate = b'{"version":7,"nodes":{"changed":{}}}\n'
    lock = selected / "flake.lock"
    lock.write_bytes(preimage if defect != "unknown" else b"unknown\n")
    lock.chmod(0o644)
    metadata = workflow_module._lock_metadata(lock) if defect != "unknown" else {
        "uid": os.getuid(), "gid": os.getgid(), "mode": 0o644, "link_count": 1,
    }
    authority = {"commit": "a" * 40}
    if defect == "missing":
        assert workflow_module._recover_lock_install(output, selected, authority) is None
        return
    for name, content in (("flake.lock.preimage", preimage), ("flake.lock.candidate", candidate)):
        if defect == "evidence-missing" and name == "flake.lock.candidate":
            continue
        path = output / name
        path.write_bytes(content)
        path.chmod(0o600)
    document = {
        "schema": "nix-update-lock-install-v1",
        "state": "installed" if defect == "terminal" else "rename-pending",
        "source_authority": authority,
        "selected_path": str(selected.absolute()),
        "preimage_sha256": hashlib.sha256(preimage).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
        "preimage_metadata": metadata,
    }
    if defect == "json": raw = b"not-json\n"
    else:
        if defect == "schema": document["schema"] = "future"
        elif defect == "authority-type": document["source_authority"] = "authority"
        elif defect == "authority-mismatch": document["source_authority"] = {"commit": "b" * 40}
        elif defect == "metadata": document["preimage_metadata"] = {"mode": 0o644}
        elif defect == "metadata-type": document["preimage_metadata"]["mode"] = "0644"
        elif defect == "digest": document["candidate_sha256"] = "0" * 64
        raw = workflow_module.canonical_bytes(document)
    journal = output / "install-transaction.json"
    journal.write_bytes(raw)
    journal.chmod(0o600)
    if defect == "current-missing":
        lock.unlink()
    if defect == "terminal":
        assert workflow_module._recover_lock_install(output, selected, authority) is None
    else:
        with pytest.raises(paths.ConfigError):
            workflow_module._recover_lock_install(output, selected, authority)


def test_interrupted_lock_recovery_accepts_exact_current_preimage(modules, tmp_path: Path):
    _, _, _, workflow_module, _ = modules
    output = tmp_path / "evidence"
    output.mkdir(mode=0o700)
    selected = tmp_path / "selected"
    selected.mkdir()
    preimage = b"preimage\n"
    candidate = b"candidate\n"
    lock = selected / "flake.lock"
    lock.write_bytes(preimage)
    lock.chmod(0o644)
    authority = {"commit": "a" * 40}
    for name, content in (("flake.lock.preimage", preimage), ("flake.lock.candidate", candidate)):
        path = output / name
        path.write_bytes(content)
        path.chmod(0o600)
    journal = {
        "schema": "nix-update-lock-install-v1", "state": "rename-pending",
        "source_authority": authority, "selected_path": str(selected.absolute()),
        "preimage_sha256": hashlib.sha256(preimage).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
        "preimage_metadata": workflow_module._lock_metadata(lock),
    }
    journal_path = output / "install-transaction.json"
    journal_path.write_bytes(workflow_module.canonical_bytes(journal))
    journal_path.chmod(0o600)
    assert workflow_module._recover_lock_install(output, selected, authority) == authority
    assert json.loads(journal_path.read_text())["state"] == "preimage-restored"


def test_interrupted_lock_recovery_records_fallback_if_terminal_journal_fails(
    modules, tmp_path: Path, monkeypatch
):
    _, _, _, workflow_module, paths = modules
    output = tmp_path / "evidence"
    output.mkdir(mode=0o700)
    selected = tmp_path / "selected"
    selected.mkdir()
    preimage = b"preimage\n"
    candidate = b"candidate\n"
    lock = selected / "flake.lock"
    lock.write_bytes(candidate)
    lock.chmod(0o644)
    authority = {"commit": "a" * 40}
    for name, content in (("flake.lock.preimage", preimage), ("flake.lock.candidate", candidate)):
        path = output / name
        path.write_bytes(content)
        path.chmod(0o600)
    journal = {
        "schema": "nix-update-lock-install-v1", "state": "rename-pending",
        "source_authority": authority, "selected_path": str(selected.absolute()),
        "preimage_sha256": hashlib.sha256(preimage).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
        "preimage_metadata": workflow_module._lock_metadata(lock),
    }
    journal_path = output / "install-transaction.json"
    journal_path.write_bytes(workflow_module.canonical_bytes(journal))
    journal_path.chmod(0o600)
    original_write = workflow_module._write_lock_journal

    def fail_terminal(path, base, state, error=None):
        if state == "preimage-restored":
            raise OSError("injected terminal journal failure")
        return original_write(path, base, state, error)

    monkeypatch.setattr(workflow_module, "_write_lock_journal", fail_terminal)
    with pytest.raises(paths.ConfigError, match="fallback recorded"):
        workflow_module._recover_lock_install(output, selected, authority)
    assert lock.read_bytes() == preimage
    fallback = next(output.glob("install-terminal-preimage-restored-*.json"))
    assert json.loads(fallback.read_text())["state"] == "preimage-restored"


def test_interrupted_lock_recovery_records_rollback_failure_if_restore_fails(
    modules, tmp_path: Path, monkeypatch
):
    _, _, _, workflow_module, paths = modules
    output = tmp_path / "evidence"
    output.mkdir(mode=0o700)
    selected = tmp_path / "selected"
    selected.mkdir()
    preimage = b"preimage\n"
    candidate = b"candidate\n"
    lock = selected / "flake.lock"
    lock.write_bytes(candidate)
    lock.chmod(0o644)
    authority = {"commit": "a" * 40}
    for name, content in (("flake.lock.preimage", preimage), ("flake.lock.candidate", candidate)):
        path = output / name
        path.write_bytes(content)
        path.chmod(0o600)
    journal = {
        "schema": "nix-update-lock-install-v1", "state": "rename-pending",
        "source_authority": authority, "selected_path": str(selected.absolute()),
        "preimage_sha256": hashlib.sha256(preimage).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
        "preimage_metadata": workflow_module._lock_metadata(lock),
    }
    journal_path = output / "install-transaction.json"
    journal_path.write_bytes(workflow_module.canonical_bytes(journal))
    journal_path.chmod(0o600)
    monkeypatch.setattr(
        workflow_module, "_restore_lock_preimage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected recovery fsync failure")),
    )

    with pytest.raises(paths.ConfigError, match="preimage restoration failed"):
        workflow_module._recover_lock_install(output, selected, authority)
    terminal = json.loads(journal_path.read_text())
    assert terminal["state"] == "rollback-failed"
    assert terminal["failure"]["type"] == "OSError"


def test_lock_terminal_journal_failure_has_exclusive_fallback(modules, tmp_path: Path, monkeypatch):
    _, _, _, workflow_module, paths = modules
    output = tmp_path / "evidence"
    output.mkdir(mode=0o700)
    journal = output / "install-transaction.json"
    monkeypatch.setattr(
        workflow_module, "_write_lock_journal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("journal unavailable")),
    )
    synced = []
    monkeypatch.setattr(workflow_module, "fsync_directory", lambda path: synced.append(Path(path)))
    with pytest.raises(paths.ConfigError, match="fallback recorded"):
        workflow_module._record_lock_terminal(journal, {"schema": "fixture"}, "rollback-failed")
    fallback = next(output.glob("install-terminal-rollback-failed-*.json"))
    assert fallback.stat().st_mode & 0o777 == 0o600
    assert json.loads(fallback.read_text())["failure"] is None
    assert synced == [output]


def test_profile_transition_allows_two_reviewed_generations_without_single_selection_binding(modules):
    state, _, _, _, _ = modules
    target = "/nix/store/system-190"
    before = {"retained": [{"path": "/profile/system-189-link", "generation": 189, "target": "/nix/store/system-189", "link": "/nix/store/system-189"}]}
    added = [
        {"path": "/profile/system-190-link", "generation": 190, "target": target, "link": target},
        {"path": "/profile/system-191-link", "generation": 191, "target": target, "link": "/nix/store/system-191"},
    ]
    after = {
        "selected": {"target": target},
        "live": {"target": target, "link": target, "symlink_text": target, "resolved_target": target},
        "retained": [*before["retained"], *added],
    }
    state.validate_profile_transition(before, after, target, allowed_new_generations=2)


@pytest.mark.parametrize(
    "arguments",
    [
        ["build"],
        ["lock", "--mode", "all", "--output", "/private/lock-evidence"],
        ["--dry-run"],
        ["--plan-only", "--output", "/private/plan"],
        ["advanced", "prepare"],
        ["promote", "prepare"],
        ["--build-only"],
    ],
)
def test_process_guard_allows_declared_non_mutating_nix_update_forms(modules, arguments):
    state, _, _, _, _ = modules
    assert state._mutation_reason("nix-update", arguments) is None


@pytest.mark.parametrize(
    ("argv", "classification", "reason"),
    [
        (["sudo", "-h"], "ambiguous sensitive wrapper or command position", "ambiguous wrapper options"),
        (["sudo"], "ambiguous sensitive wrapper or command position", "ambiguous wrapper command position"),
        (["env", "-S"], "ambiguous sensitive wrapper or command position", "ambiguous wrapper options"),
        (["bash", "--rcfile"], "ambiguous sensitive wrapper or command position", "ambiguous interpreter options"),
        (["bash", "--norc"], "ambiguous sensitive wrapper or command position", "ambiguous interpreter command position"),
        (["python3", "-m"], "ambiguous sensitive wrapper or command position", "ambiguous interpreter options"),
        (["python3", "-W"], "ambiguous sensitive wrapper or command position", "ambiguous interpreter options"),
        (["python3", "-q"], "ambiguous sensitive wrapper or command position", "ambiguous interpreter command position"),
    ],
)
def test_process_guard_fails_closed_for_incomplete_wrapper_and_interpreter_commands(
    modules, argv, classification, reason
):
    state, _, _, _, _ = modules
    actual_classification, _command, actual_reason = state._classify_process_command(
        argv, incomplete=True
    )
    assert actual_classification == classification
    assert actual_reason == reason


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["--", "flake", "update"], ("flake", "update")),
        (["-I", "/tmp/src", "flake", "lock"], ("flake", "lock")),
        (["-j4", "flake", "lock"], ("flake", "lock")),
        (["--option=warn-dirty", "false", "flake", "lock"], ("flake", "lock")),
        (["--option", "warn-dirty", "false", "flake", "lock"], ("flake", "lock")),
        (["--max-jobs"], None),
        (["--future-setting", "value"], None),
        (["--no-warn-dirty=maybe"], None),
    ],
)
def test_process_guard_parses_nix_global_options_before_classifying_mutation(
    modules, arguments, expected
):
    state, _, _, _, _ = modules
    assert state._nix_operation_arguments(arguments) == expected


@pytest.mark.parametrize(
    ("wrapper", "token", "expected"),
    [
        ("sudo", "-n", 0),
        ("sudo", "-u", 1),
        ("sudo", "-un", 0),
        ("env", "-u", 1),
        ("env", "--unset=HOME", 0),
        ("env", "-z", None),
    ],
)
def test_process_guard_binds_wrapper_option_arity_before_command_position(
    modules, wrapper, token, expected
):
    state, _, _, _, _ = modules
    assert state._wrapper_option_arity(wrapper, token) == expected


@pytest.mark.parametrize(
    ("comm", "rendered", "classification", "reason"),
    [
        ("worker", "worker --help", "innocent/non-sensitive invocation", ""),
        ("nix", "", "ambiguous sensitive wrapper or command position", "unparseable sensitive process"),
        ("sudo", "", "ambiguous sensitive wrapper or command position", "unparseable sudo command position"),
        ("sudo", "other --help", "ambiguous sensitive wrapper or command position", "unparseable sudo command position"),
        ("bash", "bash -c 'echo safe'", "terminal non-mutating invocation", ""),
        ("bash", "bash /tmp/tool", "innocent/non-sensitive invocation", ""),
        ("bash", "bash /tmp/nix flake update", "ambiguous sensitive wrapper or command position", "unparseable sensitive process"),
    ],
)
def test_process_guard_classifies_unparseable_identity_with_fail_closed_defaults(
    modules, comm, rendered, classification, reason
):
    state, _, _, _, _ = modules
    actual_classification, actual_reason = state._unparseable_process_classification(
        {"comm": comm, "rendered_args": rendered}
    )
    assert actual_classification == classification
    assert actual_reason == reason


def test_preflight_control_transactions_projects_pending_toml_without_mutating_real_tree(
    modules, tmp_path: Path
):
    state, _, _, _, _ = modules
    root = tmp_path / "control"
    root.mkdir(mode=0o700)
    path = root / "runtime.toml"
    path.write_bytes(b'agents = ["codex", "claude"]\n')
    path.chmod(0o644)
    specification = control_spec(path)
    virtual_parent = tmp_path / "virtual-parent"
    virtual_parent.mkdir(mode=0o700)
    virtual_root = virtual_parent / "projected"

    result = state.preflight_control_transactions(root, [specification], virtual_root)

    assert result["transactions"][0]["state"] == "pending"
    assert (virtual_root / "runtime.toml").read_bytes() == b"agents = []\n"
    assert (virtual_root / "runtime.toml").stat().st_mode & 0o777 == 0o600
    assert path.read_bytes() == b'agents = ["codex", "claude"]\n'
    assert path.stat().st_mode & 0o777 == 0o644


def test_preflight_control_transactions_accepts_already_current_postimage(
    modules, tmp_path: Path
):
    state, _, _, _, _ = modules
    root = tmp_path / "control"
    root.mkdir(mode=0o700)
    path = root / "runtime.toml"
    path.write_bytes(b"agents = []\n")
    path.chmod(0o600)
    specification = control_spec(path)
    virtual_parent = tmp_path / "virtual-parent"
    virtual_parent.mkdir(mode=0o700)

    result = state.preflight_control_transactions(
        root, [specification], virtual_parent / "projected"
    )

    effective = result["effective_transactions"][0]
    assert result["transactions"][0]["state"] == "already-postimage"
    assert effective["restore_before_switch"] is False
    assert effective["pre_mode"] == effective["post_mode"] == 0o600


@pytest.mark.parametrize("defect", ["root-symlink", "invalid-toml", "virtual-exists"])
def test_preflight_control_transactions_rejects_unsafe_projection_inputs(
    modules, tmp_path: Path, defect
):
    state, _, _, _, paths = modules
    root = tmp_path / "control"
    root.mkdir(mode=0o700)
    path = root / "runtime.toml"
    path.write_bytes(b'agents = ["codex", "claude"]\n')
    path.chmod(0o644)
    specification = control_spec(path)
    virtual_parent = tmp_path / "virtual-parent"
    virtual_parent.mkdir(mode=0o700)
    virtual_root = virtual_parent / "projected"
    if defect == "root-symlink":
        linked = tmp_path / "linked-control"
        linked.symlink_to(root, target_is_directory=True)
        target_root = linked
        message = "exact directory"
    elif defect == "invalid-toml":
        invalid = b"agents = [\n"
        specification["postimage_bytes_b64"] = base64.b64encode(invalid).decode()
        specification["postimage_sha256"] = hashlib.sha256(invalid).hexdigest()
        target_root = root
        message = "valid TOML"
    else:
        virtual_root.mkdir(mode=0o700)
        target_root = root
        message = "already exists"

    with pytest.raises(paths.ConfigError, match=message):
        state.preflight_control_transactions(target_root, [specification], virtual_root)


def test_preflight_control_transactions_rejects_projected_identity_drift(
    modules, tmp_path: Path, monkeypatch
):
    state, _, _, _, paths = modules
    root = tmp_path / "control"
    root.mkdir(mode=0o700)
    path = root / "runtime.toml"
    path.write_bytes(b'agents = ["codex", "claude"]\n')
    path.chmod(0o644)
    specification = control_spec(path)
    virtual_parent = tmp_path / "virtual-parent"
    virtual_parent.mkdir(mode=0o700)
    original_identity = state._control_identity
    calls = {"count": 0}

    def mismatched_identity(candidate: Path):
        calls["count"] += 1
        identity = original_identity(candidate)
        if calls["count"] == 2:
            identity = {**identity, "sha256": "0" * 64}
        return identity

    monkeypatch.setattr(state, "_control_identity", mismatched_identity)
    with pytest.raises(paths.ConfigError, match="postimage identity is invalid"):
        state.preflight_control_transactions(root, [specification], virtual_parent / "projected")


def test_validate_control_transaction_current_binds_preimage_and_postimage_modes(
    modules, tmp_path: Path
):
    state, _, _, _, paths = modules
    root = tmp_path / "control"
    root.mkdir(mode=0o700)
    path = root / "runtime.toml"
    path.write_bytes(b'agents = ["codex", "claude"]\n')
    path.chmod(0o644)
    specification = control_spec(path)

    state.validate_control_transaction_current(specification)
    path.write_bytes(b"agents = []\n")
    path.chmod(0o600)
    state.validate_control_transaction_current(specification, expect_postimage=True)
    path.write_bytes(b"drift\n")
    with pytest.raises(paths.ConfigError, match="postimage content drift"):
        state.validate_control_transaction_current(specification, expect_postimage=True)


def test_validate_control_transaction_current_rejects_empty_ancestor_chain(
    modules, tmp_path: Path
):
    state, _, _, _, paths = modules
    root = tmp_path / "control"
    root.mkdir(mode=0o700)
    path = root / "runtime.toml"
    path.write_bytes(b"agents = []\n")
    path.chmod(0o600)
    specification = control_spec(path)
    specification["safe_ancestors"] = []
    with pytest.raises(paths.ConfigError, match="ancestor chain is empty"):
        state.validate_control_transaction_current(specification)


def test_tool_identity_rejects_missing_source_and_required_external_tool(
    modules, tmp_path: Path, monkeypatch
):
    _, _, _, workflow_module, paths = modules
    core = tmp_path / "core"
    (core / "bin").mkdir(parents=True)
    (core / "libexec/nix_darwin_local").mkdir(parents=True)
    with pytest.raises(paths.ConfigError, match="source is missing"):
        workflow_module._tool_identity(core)
    for path in (core / "bin/nix-update", core / "bin/nix-rebuild"):
        path.write_text("source\n")
        path.chmod(0o700)
    monkeypatch.setattr(workflow_module.shutil, "which", lambda _name: None)
    with pytest.raises(paths.ConfigError, match="required deployment executable is unavailable: git"):
        workflow_module._tool_identity(core)


def test_exact_store_source_unavailability_is_reported_without_probe(
    modules, tmp_path: Path
):
    _, _, _, workflow_module, paths = modules
    missing = Path("/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-missing-source")
    with pytest.raises(paths.ConfigError, match="exact source is unavailable"):
        workflow_module._require_exact_store_path(missing, "fixture source")


def test_post_switch_verification_checks_profile_probes_and_external_state(
    modules, monkeypatch
):
    _, _, _, workflow_module, _ = modules
    probe = {"name": "state probe", "policy": {"name": "state probe"}}
    external = {"name": "external state", "policy": {"name": "external state"}}
    document = {
        "live": {"profile": {"relationship": "aligned"}},
        "target": {"derivation_output": "/nix/store/target"},
        "state_probes": [probe],
        "external_state": [external],
    }
    observed = []
    monkeypatch.setattr(workflow_module, "_profile_snapshot", lambda _document: {"profile": "current"})
    monkeypatch.setattr(
        workflow_module,
        "validate_profile_transition",
        lambda before, after, target: observed.append((before, after, target)),
    )
    monkeypatch.setattr(
        workflow_module,
        "_probe_identity",
        lambda policy: {"name": policy["name"], "policy": policy},
    )

    workflow_module._verify_post_switch(document)

    assert observed == [
        (document["live"]["profile"], {"profile": "current"}, "/nix/store/target")
    ]


@pytest.mark.parametrize(
    ("field", "message"),
    [("state_probes", "post-switch state probe drift"), ("external_state", "post-switch external state drift")],
)
def test_post_switch_verification_blocks_probe_or_external_drift(
    modules, monkeypatch, field, message
):
    _, _, _, workflow_module, paths = modules
    item = {"name": "drifted", "policy": {"name": "drifted"}}
    document = {
        "live": {"profile": {}},
        "target": {"derivation_output": "/nix/store/target"},
        "state_probes": [item] if field == "state_probes" else [],
        "external_state": [item] if field == "external_state" else [],
    }
    monkeypatch.setattr(workflow_module, "_profile_snapshot", lambda _document: {})
    monkeypatch.setattr(workflow_module, "validate_profile_transition", lambda *_args: None)
    monkeypatch.setattr(workflow_module, "_probe_identity", lambda _policy: {"different": True})
    with pytest.raises(paths.ConfigError, match=message):
        workflow_module._verify_post_switch(document)


def test_complete_stdout_requires_matching_bound_and_intact_evidence(
    modules, tmp_path: Path, monkeypatch
):
    _, _, _, workflow_module, paths = modules
    workflow = workflow_module.Workflow(tmp_path, subprocess.run)
    with pytest.raises(paths.ConfigError, match="complete command output is not bound"):
        workflow.complete_stdout("missing")
    monkeypatch.setattr(workflow_module, "COMMAND_RAW_LIMIT", 8)
    workflow.command("bounded child", [sys.executable, "-c", "print('0123456789')"], tmp_path)
    with pytest.raises(paths.ConfigError, match="complete command output is not bound"):
        workflow.complete_stdout("other child")
    with pytest.raises(paths.ConfigError, match="exceeds"):
        workflow.complete_stdout("bounded child")


def test_streamed_command_timeout_kills_owned_process_group_and_inherited_pipe_child(
    modules, tmp_path: Path
):
    _, _, _, workflow_module, paths = modules
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    child_pid_path = tmp_path / "child.pid"
    script = (
        "import subprocess,sys,time;"
        f"child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
        f"open({str(child_pid_path)!r},'w').write(str(child.pid));"
        "time.sleep(30)"
    )
    with pytest.raises(paths.ConfigError, match="declared timeout"):
        workflow_module._run_streamed_command(
            [sys.executable, "-c", script],
            tmp_path,
            evidence / "stdout",
            evidence / "stderr",
            timeout=1.0,
        )
    child_pid = int(child_pid_path.read_text())
    for _ in range(60):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"timed-out descendant remains alive: {child_pid}")
    assert (evidence / "stdout").stat().st_mode & 0o777 == 0o600
    assert (evidence / "stderr").stat().st_mode & 0o777 == 0o600


def test_streamed_command_timeout_returns_when_detached_child_holds_inherited_pipes(
    modules, tmp_path: Path
):
    _, _, _, workflow_module, paths = modules
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    child_pid_path = tmp_path / "detached-child.pid"
    detached_code = "import os,time;os.setsid();time.sleep(30)"
    script = (
        "import subprocess,sys,time;"
        f"child=subprocess.Popen([sys.executable,'-c',{detached_code!r}]);"
        f"open({str(child_pid_path)!r},'w').write(str(child.pid));"
        "time.sleep(30)"
    )
    detached_pid = None
    started = time.monotonic()
    try:
        with pytest.raises(paths.ConfigError, match="declared timeout"):
            workflow_module._run_streamed_command(
                [sys.executable, "-c", script],
                tmp_path,
                evidence / "stdout",
                evidence / "stderr",
                timeout=1.0,
            )
        detached_pid = int(child_pid_path.read_text())
        assert time.monotonic() - started < 4
    finally:
        if detached_pid is None and child_pid_path.exists():
            detached_pid = int(child_pid_path.read_text())
        if detached_pid is not None:
            try:
                os.kill(detached_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    assert (evidence / "stdout").stat().st_mode & 0o777 == 0o600
    assert (evidence / "stderr").stat().st_mode & 0o777 == 0o600


def test_streamed_command_closes_evidence_descriptors_when_launch_fails(
    modules, tmp_path: Path, monkeypatch
):
    _, _, _, workflow_module, _ = modules
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    opened = []
    closed = []
    original_open = workflow_module.os.open
    original_close = workflow_module.os.close

    def track_open(path, flags, mode=0o777):
        descriptor = original_open(path, flags, mode)
        if Path(path).parent == evidence:
            opened.append(descriptor)
        return descriptor

    def track_close(descriptor):
        closed.append(descriptor)
        return original_close(descriptor)

    monkeypatch.setattr(workflow_module.os, "open", track_open)
    monkeypatch.setattr(workflow_module.os, "close", track_close)
    monkeypatch.setattr(
        workflow_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected launch failure")),
    )
    with pytest.raises(OSError, match="injected launch failure"):
        workflow_module._run_streamed_command(
            [sys.executable, "-c", "pass"],
            tmp_path,
            evidence / "stdout",
            evidence / "stderr",
        )
    assert opened
    assert set(opened) <= set(closed)
