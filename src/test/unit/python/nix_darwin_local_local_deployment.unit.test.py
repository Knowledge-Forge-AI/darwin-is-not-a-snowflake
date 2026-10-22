# nix-darwin-test-source-target: libexec/nix_darwin_local/local_deployment.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/deployment_summary.py
from __future__ import annotations

import builtins
import json
import os
import pty
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local.deployment_state import derive_predecessor_generation
from nix_darwin_local.deployment_summary import format_deployment_summary, sanitize_diff_closures_line
from nix_darwin_local.local_deployment import (
    require_terminal_confirmation,
    ensure_sudo_credentials,
    deploy_local,
)
from nix_darwin_local.paths import ConfigError


def test_derive_predecessor_generation_aligned():
    profile = {
        "relationship": "aligned",
        "selected": {"generation_number": 10, "target": "/nix/store/t1", "link": "/nix/var/nix/profiles/system-10-link", "generation_path": "/nix/var/nix/profiles/system-10-link"},
        "live": {"generation_number": None, "target": "/nix/store/t1"},
        "retained": [{"generation": 10, "target": "/nix/store/t1", "path": "/nix/var/nix/profiles/system-10-link"}],
    }
    assert derive_predecessor_generation(profile) == 10


def test_derive_predecessor_generation_split_retained():
    profile = {
        "relationship": "split-retained",
        "selected": {"generation_number": 12, "target": "/nix/store/t2"},
        "live": {"generation_number": None, "target": "/nix/store/t1"},
        "retained": [
            {"generation": 11, "target": "/nix/store/t1"},
            {"generation": 12, "target": "/nix/store/t2"},
        ],
    }
    assert derive_predecessor_generation(profile) == 11


def test_derive_predecessor_generation_split_retained_ambiguous():
    profile = {
        "relationship": "split-retained",
        "selected": {"generation_number": 12, "target": "/nix/store/t2"},
        "live": {"generation_number": None, "target": "/nix/store/t1"},
        "retained": [
            {"generation": 10, "target": "/nix/store/t1"},
            {"generation": 11, "target": "/nix/store/t1"},
        ],
    }
    with pytest.raises(ConfigError, match="expected exactly 1 matching retained generation"):
        derive_predecessor_generation(profile)


def test_aligned_profiles_preserve_numbered_live_predecessor(tmp_path):
    from nix_darwin_local.deployment_state import inspect_profile

    target = tmp_path / "target"
    target.mkdir()
    for generation in (144, 145):
        (tmp_path / f"system-{generation}-link").symlink_to(target)
    selected = tmp_path / "system"
    selected.symlink_to("system-145-link")
    live = tmp_path / "current-system"
    live.symlink_to("system-144-link")
    profile = inspect_profile(selected, live, tmp_path / "system-*-link")
    assert profile["relationship"] == "aligned"
    assert derive_predecessor_generation(profile) == 144

    # An aligned direct link uses the selected retained alias.
    live.unlink()
    live.symlink_to(target)
    assert derive_predecessor_generation(
        inspect_profile(selected, live, tmp_path / "system-*-link")
    ) == 145


@pytest.mark.parametrize("live_generation", [144, 0, -1, True, "144", 1.5, [], {}])
def test_numbered_live_predecessor_requires_matching_retention(live_generation):
    profile = {
        "relationship": "aligned",
        "selected": {"link": "system-145-link", "target": "/nix/store/live"},
        "live": {"generation_number": live_generation, "target": "/nix/store/live"},
        "retained": [
            {"generation": 144, "path": "system-144-link", "target": "/nix/store/other"},
            {"generation": 145, "path": "system-145-link", "target": "/nix/store/live"},
        ],
    }
    with pytest.raises(ConfigError, match="live generation"):
        derive_predecessor_generation(profile)


def test_derive_predecessor_generation_unsupported():
    profile = {"relationship": "foreign-profile"}
    with pytest.raises(ConfigError, match="unsupported profile relationship"):
        derive_predecessor_generation(profile)


@pytest.mark.parametrize("aliases", [(10,), (10, 11), (10, 11, 12)])
def test_aligned_direct_predecessor_uses_selected_alias(tmp_path, aliases):
    from nix_darwin_local.deployment_state import inspect_profile

    target = tmp_path / "target"
    target.mkdir()
    for generation in aliases:
        (tmp_path / f"system-{generation}-link").symlink_to(target)
    selected = tmp_path / "system"
    selected.symlink_to("system-10-link")
    live = tmp_path / "current-system"
    live.symlink_to(target)
    profile = inspect_profile(selected, live, tmp_path / "system-*-link")
    assert profile["relationship"] == "aligned"
    assert profile["live"]["generation_number"] is None
    assert derive_predecessor_generation(profile) == 10


@pytest.mark.parametrize("aliases", [(10,), (10, 11)])
def test_split_direct_predecessor_requires_unique_live_alias(tmp_path, aliases):
    from nix_darwin_local.deployment_state import inspect_profile

    target = tmp_path / "target"
    target.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    for generation in aliases:
        (tmp_path / f"system-{generation}-link").symlink_to(target)
    (tmp_path / "system-12-link").symlink_to(other)
    selected = tmp_path / "system"
    selected.symlink_to("system-12-link")
    live = tmp_path / "current-system"
    live.symlink_to(target)
    profile = inspect_profile(selected, live, tmp_path / "system-*-link")
    assert profile["relationship"] == "split-retained"
    if len(aliases) == 1:
        assert derive_predecessor_generation(profile) == 10
    else:
        with pytest.raises(ConfigError, match="exactly 1 matching retained generation"):
            derive_predecessor_generation(profile)


@pytest.mark.parametrize("defect", ["selected-directory", "generation-directory", "unnumbered", "zero"])
def test_predecessor_rejects_invalid_filesystem_profile_identity(tmp_path, defect):
    from nix_darwin_local.deployment_state import inspect_profile

    target = tmp_path / "target"
    target.mkdir()
    generation = tmp_path / ("system-0-link" if defect == "zero" else "system-1-link")
    if defect == "generation-directory":
        generation.mkdir()
    else:
        generation.symlink_to(target)
    selected = tmp_path / "system"
    if defect == "selected-directory":
        selected.mkdir()
    else:
        selected.symlink_to(target if defect == "unnumbered" else generation)
    live = tmp_path / "current-system"
    live.symlink_to(target)
    with pytest.raises(ConfigError, match="not a symlink|positive integer"):
        derive_predecessor_generation(inspect_profile(selected, live, tmp_path / "system-*-link"))


@pytest.mark.parametrize("defect", [
    "missing", "other-target", "other-path", "selected-target", "missing-path",
    "boolean-retained", "string-retained",
])
def test_aligned_direct_predecessor_requires_exact_selected_retention(defect):
    profile = {
        "relationship": "aligned",
        "selected": {"generation_number": 1, "target": "/nix/store/live",
                     "generation_path": "/profiles/system-1-link"},
        "live": {"target": "/nix/store/live"},
        "retained": [
            {"generation": 1, "target": "/nix/store/live", "path": "/profiles/system-1-link"},
            {"generation": 2, "target": "/nix/store/live", "path": "/profiles/system-2-link"},
        ],
    }
    if defect == "missing":
        profile["retained"].pop(0)
    elif defect == "other-target":
        profile["retained"][0]["target"] = "/nix/store/other"
    elif defect == "other-path":
        profile["retained"][0]["path"] = "/profiles/system-alias-1-link"
    elif defect == "selected-target":
        profile["selected"]["target"] = "/nix/store/other"
    elif defect == "missing-path":
        profile["selected"].pop("generation_path")
    else:
        profile["retained"][0]["generation"] = True if defect == "boolean-retained" else "1"
    with pytest.raises(ConfigError, match="selected"):
        derive_predecessor_generation(profile)


@pytest.mark.parametrize("generation", [None, 0, -1, True, False, "1", 1.0, [], {}])
@pytest.mark.parametrize("relationship", ["aligned", "split-retained"])
def test_direct_predecessor_rejects_malformed_generation(generation, relationship):
    profile = {
        "relationship": relationship,
        "selected": {"generation_number": generation,
                     "target": "/nix/store/live" if relationship == "aligned" else "/nix/store/other",
                     "generation_path": "/profiles/system-1-link"},
        "live": {"target": "/nix/store/live"},
        "retained": [{"generation": 1 if relationship == "aligned" else generation,
                      "target": "/nix/store/live", "path": "/profiles/system-1-link"}],
    }
    with pytest.raises(ConfigError, match="positive integer"):
        derive_predecessor_generation(profile)


def test_format_deployment_summary():
    summary = format_deployment_summary(
        selected_flake=Path("/path/to/flake"),
        host="test-host",
        git_identity={"commit": "a" * 40, "clean": True},
        profile={"relationship": "aligned", "live": {"target": "/nix/store/live-sys"}},
        predecessor_generation=5,
        sources={"verified_count": 3},
        effects={"status": "absent"},
        preflight_results=[],
        target_system="/nix/store/target-sys",
        live_closure=[{"path": "/nix/store/a"}, {"path": "/nix/store/b"}],
        target_closure=[{"path": "/nix/store/b"}, {"path": "/nix/store/c"}],
        diff_closures_text="pkg: 1.0 -> 2.0\n",
        status="READY",
    )
    assert "selected flake: /path/to/flake#test-host" in summary
    assert "repository authority: commit " + ("a" * 40) + " (clean)" in summary
    assert "predecessor gen 5" in summary
    assert "3 lock-pinned sources verified" in summary
    assert "dinas.deploymentEffects absent" in summary
    assert "closure movement: +1 store paths, -1 store paths (2 paths in target)" in summary
    assert "pkg: 1.0 -> 2.0" in summary
    assert "status: READY" in summary


def test_format_deployment_summary_with_declared_effects():
    summary = format_deployment_summary(
        selected_flake=Path("/path/to/flake"),
        host="test-host",
        git_identity={"commit": "b" * 40, "clean": False},
        profile={"relationship": "aligned", "live": {"target": "/nix/store/live-sys"}},
        predecessor_generation=5,
        sources={"verified_count": 1},
        effects={"status": "declared"},
        preflight_results=[{"name": "check-reconcile", "status": "passed"}],
        target_system="/nix/store/target-sys",
        live_closure=[{"path": "/nix/store/live-sys"}],
        target_closure=[{"path": "/nix/store/target-sys"}],
        diff_closures_text="",
        status="READY",
    )
    assert "1 read-only preflight(s) passed (check-reconcile)" in summary
    assert "closure movement: +1 store paths, -1 store paths (1 paths in target)" in summary
    assert "concise diff unavailable or empty; see machine closure movement" in summary


def test_format_deployment_summary_same_target_reapplication():
    summary = format_deployment_summary(
        selected_flake=Path("/path/to/flake"),
        host="test-host",
        git_identity={"commit": "c" * 40, "clean": True},
        profile={"relationship": "aligned", "live": {"target": "/nix/store/same-sys"}},
        predecessor_generation=5,
        sources={"verified_count": 2},
        effects={"status": "absent"},
        preflight_results=[],
        target_system="/nix/store/same-sys",
        live_closure=[{"path": "/nix/store/same-sys"}],
        target_closure=[{"path": "/nix/store/same-sys"}],
        diff_closures_text="",
        status="READY",
    )
    assert "closure movement: +0 store paths, -0 store paths (1 paths in target)" in summary
    assert "reapplication notice: evaluated target equals live system with no closure delta (attended reapplication; no package update)" in summary


def test_sanitize_diff_closures_line_removes_terminal_styling():
    assert sanitize_diff_closures_line("\x1b[31;1m+43.6 KiB\x1b[0m\n") == "+43.6 KiB"


def test_require_terminal_confirmation_with_pty(monkeypatch):
    master, slave = pty.openpty()
    slave_name = os.ttyname(slave)
    os.write(master, b"DEPLOY\n")

    orig_open = builtins.open
    def fake_open(file, mode="r", *args, **kwargs):
        if file == "/dev/tty":
            return orig_open(slave_name, mode, *args, **kwargs)
        return orig_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    try:
        assert require_terminal_confirmation() is True
    finally:
        os.close(slave)
        os.close(master)


def test_require_terminal_confirmation_declined_with_pty(monkeypatch):
    master, slave = pty.openpty()
    slave_name = os.ttyname(slave)
    os.write(master, b"NO\n")

    orig_open = builtins.open
    def fake_open(file, mode="r", *args, **kwargs):
        if file == "/dev/tty":
            return orig_open(slave_name, mode, *args, **kwargs)
        return orig_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    try:
        assert require_terminal_confirmation() is False
    finally:
        os.close(slave)
        os.close(master)


def test_require_terminal_confirmation_no_tty(monkeypatch):
    def fake_open(file, *args, **kwargs):
        if file == "/dev/tty":
            raise OSError("No such device or address: /dev/tty")
        return open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    assert require_terminal_confirmation() is False


def test_ensure_sudo_credentials_cached(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: SimpleNamespace(returncode=0))
    assert ensure_sudo_credentials() is True


def test_ensure_sudo_credentials_missing_executable_fails_closed(monkeypatch):
    def missing_sudo(cmd, **kwargs):
        raise OSError("sudo executable unavailable")

    monkeypatch.setattr(subprocess, "run", missing_sudo)
    assert ensure_sudo_credentials("/test/bin/sudo") is False


def test_ensure_sudo_credentials_interactive_success(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd == ["sudo", "-n", "-v"]:
            return SimpleNamespace(returncode=1)
        if cmd == ["sudo", "-v"]:
            return SimpleNamespace(returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    master, slave = pty.openpty()
    slave_name = os.ttyname(slave)
    orig_open = builtins.open
    def fake_open(file, mode="r", *args, **kwargs):
        if file == "/dev/tty":
            return orig_open(slave_name, mode, *args, **kwargs)
        return orig_open(file, mode, *args, **kwargs)
    monkeypatch.setattr("builtins.open", fake_open)
    try:
        assert ensure_sudo_credentials() is True
    finally:
        os.close(slave)
        os.close(master)


def test_ensure_sudo_credentials_interactive_tty_failure_fails_closed(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: SimpleNamespace(returncode=1),
    )

    def unavailable_tty(file, *args, **kwargs):
        if file == "/dev/tty":
            raise OSError("controlling terminal unavailable")
        return open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", unavailable_tty)
    assert ensure_sudo_credentials("/test/bin/sudo") is False


def test_deploy_local_dry_run_and_plan_only(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("nix_darwin_local.local_deployment.operator_home", lambda: tmp_path)
    monkeypatch.setattr("nix_darwin_local.local_deployment._guard_processes", lambda *a, **kw: None)
    monkeypatch.setattr("nix_darwin_local.local_deployment.local_git_identity", lambda *a: {"commit": "1" * 40, "clean": True})
    profile = {
        "relationship": "aligned",
        "selected": {"generation_number": 1, "target": "/nix/store/t1", "link": "/nix/var/nix/profiles/system-1-link", "generation_path": "/nix/var/nix/profiles/system-1-link"},
        "live": {"generation_number": None, "target": "/nix/store/t1"},
        "retained": [{"generation": 1, "target": "/nix/store/t1", "path": "/nix/var/nix/profiles/system-1-link"}],
    }
    monkeypatch.setattr("nix_darwin_local.local_deployment.inspect_profile", lambda *a: profile)
    monkeypatch.setattr("nix_darwin_local.local_deployment.evaluate_allowlist", lambda *a: [])
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    monkeypatch.setattr("nix_darwin_local.local_deployment.render_scratch", lambda *a: SimpleNamespace(root=scratch_root))
    monkeypatch.setattr("nix_darwin_local.local_deployment.evaluate_deployment_effects", lambda *a: {"status": "absent"})
    monkeypatch.setattr("nix_darwin_local.local_deployment.derive_source_identities", lambda *a, **kw: {"verified_count": 1, "sources": []})
    def mock_command(label, argv, cwd):
        if label == "exact Darwin target build":
            return SimpleNamespace(returncode=0, stdout="/nix/store/dummy-target-system\n")
        return SimpleNamespace(returncode=0, stdout="")

    workflow = SimpleNamespace(
        core_dir=tmp_path,
        command=mock_command,
        complete_stdout=lambda label: "diff closures line\n",
    )
    monkeypatch.setattr("nix_darwin_local.local_deployment.closure_inventory", lambda *a: [{"path": "/nix/store/dummy-target-system"}])
    monkeypatch.setattr("nix_darwin_local.local_deployment.current_live_closure", lambda *a: [{"path": "/nix/store/t1"}])

    control_dir = tmp_path / "control"
    control_dir.mkdir()

    # Test dry_run
    rc_dry = deploy_local(
        workflow=workflow,
        selected=tmp_path,
        host="test-host",
        control_dir=control_dir,
        overrides=[],
        test_jobs="2",
        build_jobs="2",
        dry_run=True,
    )
    assert rc_dry == 0

    # Test plan_only
    plan_out = tmp_path / "plan_out"
    rc_plan = deploy_local(
        workflow=workflow,
        selected=tmp_path,
        host="test-host",
        control_dir=control_dir,
        overrides=[],
        test_jobs="2",
        build_jobs="2",
        plan_only=True,
        plan_output=plan_out,
    )
    assert rc_plan == 0
    assert (plan_out / "plan.json").is_file()
    assert (plan_out / "plan.md").is_file()

    # Test plan_only missing output
    with pytest.raises(ConfigError, match="--plan-only requires --output"):
        deploy_local(
            workflow=workflow,
            selected=tmp_path,
            host="test-host",
            control_dir=control_dir,
            overrides=[],
            test_jobs="",
            build_jobs="",
            plan_only=True,
            plan_output=None,
        )


def test_deploy_local_rejects_mutating_effects(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("nix_darwin_local.local_deployment.operator_home", lambda: tmp_path)
    monkeypatch.setattr("nix_darwin_local.local_deployment._guard_processes", lambda *a, **kw: None)
    monkeypatch.setattr("nix_darwin_local.local_deployment.local_git_identity", lambda *a: {"commit": "1" * 40, "clean": True})
    profile = {
        "relationship": "aligned",
        "selected": {"generation_number": 1, "target": "/nix/store/t1", "link": "/nix/var/nix/profiles/system-1-link", "generation_path": "/nix/var/nix/profiles/system-1-link"},
        "live": {"generation_number": None, "target": "/nix/store/t1"},
        "retained": [{"generation": 1, "target": "/nix/store/t1", "path": "/nix/var/nix/profiles/system-1-link"}],
    }
    monkeypatch.setattr("nix_darwin_local.local_deployment.inspect_profile", lambda *a: profile)
    monkeypatch.setattr("nix_darwin_local.local_deployment.evaluate_allowlist", lambda *a: [])
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    monkeypatch.setattr("nix_darwin_local.local_deployment.render_scratch", lambda *a: SimpleNamespace(root=scratch_root))
    monkeypatch.setattr("nix_darwin_local.local_deployment.evaluate_deployment_effects", lambda *a: {
        "status": "declared",
        "preSwitchTransactions": [{"name": "mutating_tx"}],
    })

    control_dir = tmp_path / "control"
    control_dir.mkdir()

    with pytest.raises(ConfigError, match="run 'nix-update advanced prepare/apply'"):
        deploy_local(
            workflow=SimpleNamespace(),
            selected=tmp_path,
            host="test-host",
            control_dir=control_dir,
            overrides=[],
            test_jobs="",
            build_jobs="",
            dry_run=True,
        )


def test_deploy_local_confirmation_refusal(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("nix_darwin_local.local_deployment.operator_home", lambda: tmp_path)
    monkeypatch.setattr("nix_darwin_local.local_deployment._guard_processes", lambda *a, **kw: None)
    monkeypatch.setattr("nix_darwin_local.local_deployment.local_git_identity", lambda *a: {"commit": "1" * 40, "clean": True})
    profile = {
        "relationship": "aligned",
        "selected": {"generation_number": 1, "target": "/nix/store/t1", "link": "/nix/var/nix/profiles/system-1-link", "generation_path": "/nix/var/nix/profiles/system-1-link"},
        "live": {"generation_number": None, "target": "/nix/store/t1"},
        "retained": [{"generation": 1, "target": "/nix/store/t1", "path": "/nix/var/nix/profiles/system-1-link"}],
    }
    monkeypatch.setattr("nix_darwin_local.local_deployment.inspect_profile", lambda *a: profile)
    monkeypatch.setattr("nix_darwin_local.local_deployment.evaluate_allowlist", lambda *a: [])
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    monkeypatch.setattr("nix_darwin_local.local_deployment.render_scratch", lambda *a: SimpleNamespace(root=scratch_root))
    monkeypatch.setattr("nix_darwin_local.local_deployment.evaluate_deployment_effects", lambda *a: {"status": "absent"})
    monkeypatch.setattr("nix_darwin_local.local_deployment.derive_source_identities", lambda *a, **kw: {"verified_count": 1, "sources": []})

    def mock_command(label, argv, cwd):
        if label == "exact Darwin target build":
            return SimpleNamespace(returncode=0, stdout="/nix/store/dummy-target-system\n")
        return SimpleNamespace(returncode=0, stdout="")

    workflow = SimpleNamespace(
        core_dir=tmp_path,
        command=mock_command,
        complete_stdout=lambda label: "diff closures line\n",
    )
    monkeypatch.setattr("nix_darwin_local.local_deployment.closure_inventory", lambda *a: [{"path": "/nix/store/dummy-target-system"}])
    monkeypatch.setattr("nix_darwin_local.local_deployment.current_live_closure", lambda *a: [{"path": "/nix/store/t1"}])
    monkeypatch.setattr("nix_darwin_local.local_deployment.require_terminal_confirmation", lambda: False)
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment._tool_identity",
        lambda *a: {
            "external": {
                "sudo": {"resolved": "/test/bin/sudo"},
                "nix-env": {"path": "/test/bin/nix-env", "resolved": "/test/bin/nix"},
                "darwin-rebuild": {"resolved": "/test/bin/darwin-rebuild"},
            }
        },
    )

    control_dir = tmp_path / "control"
    control_dir.mkdir()

    rc = deploy_local(
        workflow=workflow,
        selected=tmp_path,
        host="test-host",
        control_dir=control_dir,
        overrides=[],
        test_jobs="",
        build_jobs="",
        dry_run=False,
    )
    assert rc == 1


def _profile(
    *,
    live_target: str = "/nix/store/live-system",
    selected_target: str = "/nix/store/live-system",
    relationship: str = "aligned",
    retained_generations: tuple[int, ...] = (1,),
    predecessor_target: str | None = None,
) -> dict[str, object]:
    if predecessor_target is None:
        predecessor_target = live_target
    retained = [
        {
            "generation": generation,
            "target": predecessor_target if generation == 1 else selected_target,
            "path": f"/nix/var/nix/profiles/system-{generation}-link",
        }
        for generation in retained_generations
    ]
    return {
        "relationship": relationship,
        "selected": {
            "generation_number": retained_generations[-1],
            "target": selected_target,
            "link": f"system-{retained_generations[-1]}-link",
            "path": "/nix/var/nix/profiles/system",
            "generation_path": f"/nix/var/nix/profiles/system-{retained_generations[-1]}-link",
        },
        "live": {"generation_number": None, "target": live_target},
        "retained": retained,
    }


def _configure_switch_case(
    tmp_path: Path,
    monkeypatch,
    *,
    profiles: list[dict[str, object]],
    switch_status: int = 0,
    git_identities: list[dict[str, Any]] | None = None,
    tool_identities: list[dict[str, Any]] | None = None,
):
    git_identity = {
        "commit": "1" * 40,
        "status": "",
        "operation_state": "clean",
    }
    tool_identity = {
        "external": {
            "sudo": {"resolved": "/test/bin/sudo"},
            "nix-env": {"path": "/test/bin/nix-env", "resolved": "/test/bin/nix"},
            "darwin-rebuild": {"resolved": "/test/bin/darwin-rebuild"},
        }
    }
    def stable_sequence(values):
        values = list(values)
        if not values:
            raise AssertionError("a deployment fixture sequence must not be empty")
        values_iter = iter(values)
        last = values[-1]

        def take():
            nonlocal last
            try:
                last = next(values_iter)
            except StopIteration:
                pass
            return last

        return take

    git_next = stable_sequence(git_identities or [git_identity])
    tool_next = stable_sequence(tool_identities or [tool_identity])
    profile_next = stable_sequence(profiles)
    commands: list[tuple[str, list[str]]] = []

    monkeypatch.setattr("nix_darwin_local.local_deployment.operator_home", lambda: tmp_path)
    monkeypatch.setattr("nix_darwin_local.local_deployment._guard_processes", lambda *a, **kw: None)
    monkeypatch.setattr("nix_darwin_local.local_deployment.local_git_identity", lambda *a: git_next())
    monkeypatch.setattr("nix_darwin_local.local_deployment.inspect_profile", lambda *a: profile_next())
    monkeypatch.setattr("nix_darwin_local.local_deployment.evaluate_allowlist", lambda *a: [])
    scratch_root = tmp_path / "projection"
    scratch_root.mkdir()
    scratch_projection = SimpleNamespace(
        root=scratch_root,
        inventory=(),
        digest="scratch-projection-digest",
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.render_scratch",
        lambda *a: scratch_projection,
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.inspect_scratch",
        lambda *a: scratch_projection,
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.evaluate_deployment_effects",
        lambda *a: {"status": "absent"},
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.derive_source_identities",
        lambda *a, **kw: {"verified_count": 1, "sources": []},
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.run_read_only_preflights",
        lambda *a: [],
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.closure_inventory",
        lambda *a: [{"path": "/nix/store/target-system"}],
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.current_live_closure",
        lambda *a: [{"path": "/nix/store/live-system"}],
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.require_terminal_confirmation",
        lambda: True,
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.ensure_sudo_credentials",
        lambda path: path == "/test/bin/sudo",
    )
    monkeypatch.setattr("nix_darwin_local.local_deployment._tool_identity", lambda *a: tool_next())

    def command(label, argv, cwd):
        commands.append((label, list(argv)))
        if label in {"exact Darwin target build", "local target revalidation"}:
            return SimpleNamespace(
                returncode=0, stdout="/nix/store/target-system\n"
            )
        if label == "single Darwin switch":
            return SimpleNamespace(returncode=switch_status, stdout="")
        return SimpleNamespace(returncode=0, stdout="")

    workflow = SimpleNamespace(
        core_dir=tmp_path,
        command=command,
        complete_stdout=lambda label: "package: 1 -> 2\n",
    )
    control_dir = tmp_path / "control"
    control_dir.mkdir()
    return workflow, control_dir, commands


def _run_switch_case(tmp_path: Path, monkeypatch, **kwargs) -> tuple[int, list[tuple[str, list[str]]]]:
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, **kwargs
    )
    status = deploy_local(
        workflow=workflow,
        selected=tmp_path,
        host="test-host",
        control_dir=control_dir,
        overrides=[],
        test_jobs="",
        build_jobs="",
    )
    return status, commands


def _result_journal(tmp_path: Path) -> dict[str, object]:
    journals = list(
        (tmp_path / ".local/state/nix-update").glob("run-*/journal.json")
    )
    assert len(journals) == 1
    return json.loads(journals[0].read_text(encoding="utf-8"))


def test_deploy_local_switches_once_with_bound_executables_and_journals_success(
    tmp_path: Path, monkeypatch
):
    post = _profile(
        live_target="/nix/store/target-system",
        selected_target="/nix/store/target-system",
        predecessor_target="/nix/store/live-system",
    )
    status, commands = _run_switch_case(
        tmp_path, monkeypatch, profiles=[_profile(), _profile(), _profile(), post]
    )

    assert status == 0
    switches = [argv for label, argv in commands if label == "single Darwin switch"]
    assert not any(label == "selected-profile realignment" for label, _ in commands)
    assert len(switches) == 1
    assert switches[0][:3] == [
        "/test/bin/sudo",
        "HOMEBREW_NO_AUTO_UPDATE=1",
        "/test/bin/darwin-rebuild",
    ]
    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "SUCCESS"
    assert journal["predecessor_generation"] == 1
    assert journal["same_target_reapplication"] is False


def test_deploy_local_same_target_reapplication_reports_truthfully_and_journals(
    tmp_path: Path, monkeypatch, capsys
):
    live_target = "/nix/store/live-system"
    post = _profile(
        live_target=live_target,
        selected_target=live_target,
        predecessor_target=live_target,
    )
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path,
        monkeypatch,
        profiles=[_profile(), _profile(), _profile(), post],
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.closure_inventory",
        lambda *a: [{"path": live_target}],
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.current_live_closure",
        lambda *a: [{"path": live_target}],
    )
    orig_cmd = workflow.command
    def command_same(label, argv, cwd):
        if label in {"exact Darwin target build", "local target revalidation"}:
            from types import SimpleNamespace
            return SimpleNamespace(returncode=0, stdout=f"{live_target}\n")
        return orig_cmd(label, argv, cwd)
    workflow.command = command_same

    status = deploy_local(
        workflow=workflow,
        selected=tmp_path,
        host="test-host",
        control_dir=control_dir,
        overrides=[],
        test_jobs="",
        build_jobs="",
    )
    assert status == 0
    out = capsys.readouterr().out
    assert "reapplication will run activation hooks with no package or closure change" in out
    assert "reapplication of already-selected system; no closure delta" in out

    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "SUCCESS"
    assert journal["same_target_reapplication"] is True


def test_deploy_local_with_absent_campaign_index(
    tmp_path: Path, monkeypatch
):
    state_dir = tmp_path / ".local/state/nix-update"
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_dir.chmod(0o700)
    index_file = state_dir / "campaign-index.json"
    index_file.unlink(missing_ok=True)

    post = _profile(
        live_target="/nix/store/target-system",
        selected_target="/nix/store/target-system",
        predecessor_target="/nix/store/live-system",
    )
    status, _ = _run_switch_case(
        tmp_path, monkeypatch, profiles=[_profile(), _profile(), _profile(), post]
    )
    assert status == 0
    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "SUCCESS"
    assert journal["same_target_reapplication"] is False


def test_deploy_local_with_unrelated_or_test_filled_campaign_index(
    tmp_path: Path, monkeypatch
):
    state_dir = tmp_path / ".local/state/nix-update"
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_dir.chmod(0o700)
    index_file = state_dir / "campaign-index.json"

    import json
    index_file.write_text(json.dumps({
        "schema": "nix-update-promotion-campaign-index-v1",
        "version": 1,
        "entries": [
            {"campaign": "/tmp/test-camp-1", "input": "inp1", "revision": "rev1", "selected": "/tmp/sel1", "state": "BLOCKED"},
            {"campaign": "/tmp/test-camp-2", "input": "inp2", "revision": "rev2", "selected": "/tmp/sel2", "state": "COMPLETE"},
        ],
    }), encoding="utf-8")
    index_file.chmod(0o600)

    post = _profile(
        live_target="/nix/store/target-system",
        selected_target="/nix/store/target-system",
        predecessor_target="/nix/store/live-system",
    )
    status, _ = _run_switch_case(
        tmp_path, monkeypatch, profiles=[_profile(), _profile(), _profile(), post]
    )
    assert status == 0
    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "SUCCESS"
    assert journal["same_target_reapplication"] is False


def test_deploy_local_switch_failure_is_truthful_and_never_retried(
    tmp_path: Path, monkeypatch
):
    status, commands = _run_switch_case(
        tmp_path,
        monkeypatch,
        profiles=[_profile(), _profile(), _profile()],
        switch_status=9,
    )

    assert status == 1
    assert sum(label == "single Darwin switch" for label, _ in commands) == 1
    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "POST_INVOCATION_FAILURE"
    assert journal["exit_status"] == 9


def test_deploy_local_switch_invocation_exception_is_journaled_without_retry(
    tmp_path: Path, monkeypatch
):
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[_profile(), _profile(), _profile()]
    )
    original_command = workflow.command

    def fail_switch(label, argv, cwd):
        if label == "single Darwin switch":
            commands.append((label, list(argv)))
            raise RuntimeError("switch transport broke")
        return original_command(label, argv, cwd)

    workflow.command = fail_switch
    status = deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")

    assert status == 1
    assert sum(label == "single Darwin switch" for label, _ in commands) == 1
    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "POST_INVOCATION_FAILURE"
    assert journal["switch_invoked"] is True
    assert "RuntimeError: switch transport broke" in journal["error"]


def test_deploy_local_post_switch_inspection_exception_is_journaled(
    tmp_path: Path, monkeypatch
):
    initial = _profile()
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[initial, initial, initial]
    )
    inspect_calls = 0

    def inspect_after_switch(*args):
        nonlocal inspect_calls
        inspect_calls += 1
        if inspect_calls == 4:
            raise RuntimeError("post-switch profile inspection broke")
        return initial

    monkeypatch.setattr("nix_darwin_local.local_deployment.inspect_profile", inspect_after_switch)
    status = deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")

    assert status == 1
    assert sum(label == "single Darwin switch" for label, _ in commands) == 1
    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "POST_SWITCH_VERIFICATION_FAILURE"
    assert journal["switch_invoked"] is True
    assert "RuntimeError: post-switch profile inspection broke" in journal["error"]


def test_deploy_local_post_sudo_revalidation_failure_is_journaled_before_switch(
    tmp_path: Path, monkeypatch
):
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[_profile(), _profile(), _profile()]
    )
    effects = iter([
        {"status": "absent"},
        {"status": "absent"},
        {"status": "declared"},
    ])
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.evaluate_deployment_effects",
        lambda *args: next(effects),
    )

    status = deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")

    assert status == 1
    assert all(label != "single Darwin switch" for label, _ in commands)
    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "PRE_SWITCH_FAILURE"
    assert journal["switch_invoked"] is False
    assert "module-owned deployment effects drifted" in journal["error"]


def test_deploy_local_post_sudo_source_drift_is_journaled_before_switch(
    tmp_path: Path, monkeypatch
):
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[_profile(), _profile(), _profile()]
    )
    sources = iter([
        {"verified_count": 1, "sources": []},
        {"verified_count": 1, "sources": []},
        {"verified_count": 2, "sources": [{"name": "drifted"}]},
    ])
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.derive_source_identities",
        lambda *args, **kw: next(sources),
    )

    status = deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")

    assert status == 1
    assert all(label != "single Darwin switch" for label, _ in commands)
    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "PRE_SWITCH_FAILURE"
    assert journal["switch_invoked"] is False
    assert "source authority drifted" in journal["error"]


def test_deploy_local_post_sudo_target_rebuild_drift_is_journaled_before_switch(
    tmp_path: Path, monkeypatch
):
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[_profile(), _profile(), _profile()]
    )
    original_command = workflow.command
    revalidation_calls = 0

    def drift_target(label, argv, cwd):
        nonlocal revalidation_calls
        if label == "local target revalidation":
            revalidation_calls += 1
            if revalidation_calls == 2:
                commands.append((label, list(argv)))
                return SimpleNamespace(returncode=0, stdout="/nix/store/drifted-target\n")
        return original_command(label, argv, cwd)

    workflow.command = drift_target
    status = deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")

    assert status == 1
    assert revalidation_calls == 2
    assert all(label != "single Darwin switch" for label, _ in commands)
    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "PRE_SWITCH_FAILURE"
    assert journal["switch_invoked"] is False
    assert "target system drifted" in journal["error"]


def test_deploy_local_post_sudo_closure_drift_is_journaled_before_switch(
    tmp_path: Path, monkeypatch
):
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[_profile(), _profile(), _profile()]
    )
    closures = iter([
        [{"path": "/nix/store/target-system"}],
        [{"path": "/nix/store/target-system"}],
        [{"path": "/nix/store/target-system"}, {"path": "/nix/store/extra"}],
    ])
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.closure_inventory",
        lambda *args: next(closures),
    )

    status = deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")

    assert status == 1
    assert all(label != "single Darwin switch" for label, _ in commands)
    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "PRE_SWITCH_FAILURE"
    assert journal["switch_invoked"] is False
    assert "target closure inventory drifted" in journal["error"]


def test_deploy_local_post_sudo_control_tree_drift_is_journaled_before_switch(
    tmp_path: Path, monkeypatch
):
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[_profile(), _profile(), _profile()]
    )
    identities = iter([
        {"tree": "stable"},
        {"tree": "stable"},
        {"tree": "changed"},
    ])
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.tree_identity",
        lambda *args: next(identities),
    )

    status = deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")

    assert status == 1
    assert all(label != "single Darwin switch" for label, _ in commands)
    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "PRE_SWITCH_FAILURE"
    assert journal["switch_invoked"] is False
    assert "machine-local control state drifted" in journal["error"]


def test_deploy_local_post_sudo_scratch_drift_is_journaled_before_switch(
    tmp_path: Path, monkeypatch
):
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[_profile(), _profile(), _profile()]
    )
    projection_root = tmp_path / "projection"
    projections = iter([
        SimpleNamespace(root=projection_root, inventory=(), digest="scratch-projection-digest"),
        SimpleNamespace(root=projection_root, inventory=("changed",), digest="drifted"),
    ])
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.inspect_scratch",
        lambda *args: next(projections),
    )

    status = deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")

    assert status == 1
    assert all(label != "single Darwin switch" for label, _ in commands)
    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "PRE_SWITCH_FAILURE"
    assert journal["switch_invoked"] is False
    assert "machine-local projection drifted" in journal["error"]


def test_deploy_local_post_sudo_profile_drift_is_journaled_before_switch(
    tmp_path: Path, monkeypatch
):
    initial = _profile()
    drifted = _profile(selected_target="/nix/store/drifted-target")
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[initial, initial, drifted]
    )

    status = deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")

    assert status == 1
    assert all(label != "single Darwin switch" for label, _ in commands)
    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "PRE_SWITCH_FAILURE"
    assert journal["switch_invoked"] is False
    assert "selected-profile or live-system state drifted" in journal["error"]


def test_deploy_local_journal_write_failure_returns_without_traceback(
    tmp_path: Path, monkeypatch, capsys
):
    post = _profile(
        live_target="/nix/store/target-system",
        selected_target="/nix/store/target-system",
        predecessor_target="/nix/store/live-system",
    )
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[_profile(), _profile(), _profile(), post]
    )
    from nix_darwin_local.local_deployment import durable_marker as original_marker

    def fail_journal(result_dir, name, document):
        if name == "journal.json":
            raise OSError("journal storage unavailable")
        return original_marker(result_dir, name, document)

    monkeypatch.setattr("nix_darwin_local.local_deployment.durable_marker", fail_journal)
    status = deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")

    captured = capsys.readouterr()
    assert status == 1
    assert sum(label == "single Darwin switch" for label, _ in commands) == 1
    assert "unable to persist terminal journal" in captured.err
    assert "Traceback" not in captured.err
    authority_files = list(
        (tmp_path / ".local/state/nix-update").glob("run-*/local-mutation-authority.json")
    )
    assert len(authority_files) == 1


@pytest.mark.parametrize(
    ("post_profile", "message"),
    [
        (
            _profile(selected_target="/nix/store/target-system"),
            "live system target",
        ),
        (
            _profile(live_target="/nix/store/target-system"),
            "selected profile target",
        ),
        (
            _profile(
                live_target="/nix/store/target-system",
                selected_target="/nix/store/target-system",
                retained_generations=(2,),
            ),
            "predecessor generation 1 was not retained",
        ),
        (
            _profile(
                live_target="/nix/store/target-system",
                selected_target="/nix/store/target-system",
                retained_generations=(1, 2),
                predecessor_target="/nix/store/wrong-predecessor",
            ),
            "predecessor generation 1 was not retained",
        ),
    ],
)
def test_deploy_local_journals_each_post_switch_verification_failure(
    tmp_path: Path, monkeypatch, post_profile, message
):
    status, commands = _run_switch_case(
        tmp_path,
        monkeypatch,
        profiles=[_profile(), _profile(), _profile(), post_profile],
    )

    assert status == 1
    assert sum(label == "single Darwin switch" for label, _ in commands) == 1
    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "POST_SWITCH_VERIFICATION_FAILURE"
    assert message in journal["error"]


def test_deploy_local_rejects_git_drift_before_any_switch(
    tmp_path: Path, monkeypatch
):
    initial = {"commit": "1" * 40, "status": "", "operation_state": "clean"}
    changed = {**initial, "commit": "2" * 40}
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path,
        monkeypatch,
        profiles=[_profile(), _profile()],
        git_identities=[initial, changed],
    )

    with pytest.raises(ConfigError, match="Git authority drifted"):
        deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")
    assert all(label != "single Darwin switch" for label, _ in commands)


def test_deploy_local_rejects_tool_drift_before_any_switch(
    tmp_path: Path, monkeypatch
):
    initial = {
        "external": {
            "sudo": {"resolved": "/test/bin/sudo"},
            "nix-env": {"path": "/test/bin/nix-env", "resolved": "/test/bin/nix"},
            "darwin-rebuild": {"resolved": "/test/bin/darwin-rebuild"},
        }
    }
    changed = {
        "external": {
            **initial["external"],
            "darwin-rebuild": {"resolved": "/other/darwin-rebuild"},
        }
    }
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path,
        monkeypatch,
        profiles=[_profile(), _profile()],
        tool_identities=[initial, changed],
    )

    with pytest.raises(ConfigError, match="executable identity drifted"):
        deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")
    assert all(label != "single Darwin switch" for label, _ in commands)


def test_deploy_local_realigns_split_profile_with_bound_nix_env(
    tmp_path: Path, monkeypatch
):
    split = _profile(
        selected_target="/nix/store/selected-system",
        relationship="split-retained",
        retained_generations=(1, 2),
    )
    post = _profile(
        live_target="/nix/store/target-system",
        selected_target="/nix/store/target-system",
        retained_generations=(1, 2),
        predecessor_target="/nix/store/live-system",
    )
    aligned_predecessor = {
        "relationship": "aligned",
        "selected": {
            "generation_number": 1,
            "target": "/nix/store/live-system",
            "link": "system-1-link",
            "path": "/nix/var/nix/profiles/system",
        },
        "live": split["live"],
        "retained": split["retained"],
    }
    status, commands = _run_switch_case(
        tmp_path,
        monkeypatch,
        profiles=[split, split, split, aligned_predecessor, aligned_predecessor, post],
    )

    assert status == 0
    realignments = [
        argv for label, argv in commands if label == "selected-profile realignment"
    ]
    assert len(realignments) == 1
    assert realignments[0] == [
        "/test/bin/sudo", "/test/bin/nix-env", "--profile",
        "/nix/var/nix/profiles/system", "--switch-generation", "1",
    ]


def test_deploy_local_build_failure_occurs_before_confirmation_or_privilege(
    tmp_path: Path, monkeypatch
):
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[_profile()]
    )
    original_command = workflow.command

    def fail_build(label, argv, cwd):
        if label == "exact Darwin target build":
            commands.append((label, list(argv)))
            return SimpleNamespace(returncode=8, stdout="")
        return original_command(label, argv, cwd)

    workflow.command = fail_build
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.require_terminal_confirmation",
        lambda: pytest.fail("confirmation must not run after build failure"),
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.ensure_sudo_credentials",
        lambda path: pytest.fail("sudo must not run after build failure"),
    )

    with pytest.raises(ConfigError, match=r"target build failed \(exit 8\)"):
        deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")
    assert all(label != "single Darwin switch" for label, _ in commands)


def test_deploy_local_preflight_failure_occurs_before_confirmation_or_privilege(
    tmp_path: Path, monkeypatch
):
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[_profile()]
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.run_read_only_preflights",
        lambda *a: (_ for _ in ()).throw(ConfigError("preflight failed")),
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.require_terminal_confirmation",
        lambda: pytest.fail("confirmation must not run after preflight failure"),
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.ensure_sudo_credentials",
        lambda path: pytest.fail("sudo must not run after preflight failure"),
    )

    with pytest.raises(ConfigError, match="preflight failed"):
        deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")
    assert all(label != "single Darwin switch" for label, _ in commands)


def test_deploy_local_sudo_refusal_stops_before_profile_mutation(
    tmp_path: Path, monkeypatch
):
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[_profile()], tool_identities=[{
            "external": {
                "sudo": {"resolved": "/test/bin/sudo"},
                "nix-env": {"path": "/test/bin/nix-env", "resolved": "/test/bin/nix"},
                "darwin-rebuild": {"resolved": "/test/bin/darwin-rebuild"},
            }
        }]
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.ensure_sudo_credentials",
        lambda path: False,
    )

    assert deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "") == 1
    assert all(label != "single Darwin switch" for label, _ in commands)


def test_deploy_local_rechecks_module_preflight_before_profile_mutation(
    tmp_path: Path, monkeypatch
):
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[_profile(), _profile()]
    )
    results = iter([[], [{"name": "changed", "status": "passed"}]])
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.run_read_only_preflights",
        lambda *a: next(results),
    )

    with pytest.raises(ConfigError, match="preflight results drifted"):
        deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")
    assert all(label != "single Darwin switch" for label, _ in commands)


def test_deploy_local_realign_failure_stops_before_switch(
    tmp_path: Path, monkeypatch
):
    split = _profile(
        selected_target="/nix/store/selected-system",
        relationship="split-retained",
        retained_generations=(1, 2),
    )
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[split, split, split]
    )
    original_command = workflow.command

    def fail_realign(label, argv, cwd):
        if label == "selected-profile realignment":
            commands.append((label, list(argv)))
            return SimpleNamespace(returncode=7, stdout="")
        return original_command(label, argv, cwd)

    workflow.command = fail_realign

    status = deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")
    assert status == 1
    assert all(label != "single Darwin switch" for label, _ in commands)
    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "PRE_SWITCH_FAILURE"
    assert "selected-profile realignment failed (exit 7)" in journal["error"]
    assert "private evidence:" in journal["error"]


@pytest.mark.parametrize("drift", ["live", "retained", "target", "generation"])
def test_local_realignment_requires_preserved_predecessor(tmp_path, monkeypatch, drift):
    split = _profile(
        selected_target="/nix/store/selected-system",
        relationship="split-retained", retained_generations=(1, 2),
    )
    aligned = {
        **split, "relationship": "aligned",
        "selected": {**split["selected"], "generation_number": 1,
                     "target": split["live"]["target"], "link": "system-1-link"},
    }
    if drift == "live":
        aligned["live"] = {**split["live"], "target": "/nix/store/drifted"}
    elif drift == "retained":
        aligned["retained"] = split["retained"][:1]
    elif drift == "target":
        aligned["selected"]["target"] = "/nix/store/drifted"
    else:
        aligned["selected"]["generation_number"] = 2
    status, commands = _run_switch_case(
        tmp_path, monkeypatch, profiles=[split, split, split, aligned],
    )
    assert status == 1
    assert sum(label == "selected-profile realignment" for label, _ in commands) == 1
    assert not any(label == "single Darwin switch" for label, _ in commands)
    assert "did not preserve the live predecessor" in _result_journal(tmp_path)["error"]


@pytest.mark.parametrize("report_hook", [False, True])
def test_local_qualification_runs_exact_projection_once(tmp_path, monkeypatch, report_hook):
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[_profile()],
    )
    if report_hook:
        entrypoint = tmp_path / "bin/nix-test"
        entrypoint.parent.mkdir()
        entrypoint.write_text("#!/bin/sh\nexit 0\n")
        entrypoint.chmod(0o700)
    assert deploy_local(workflow, tmp_path, "test-host", control_dir, [], "2", "", dry_run=True) == 0
    exact = ["nix", "flake", "check", "path:.", "--no-update-lock-file", "--no-write-lock-file",
             "--override-input", "localConfig", f"path:{tmp_path / 'projection'}"]
    assert [argv for _, argv in commands].count(exact) == 1
    qualification = next(argv for label, argv in commands if label == "selected-flake report qualification")
    if report_hook:
        assert qualification[0].endswith("/bin/nix-test") and qualification[1] == "--report"
        assert qualification[-8:] == ["--jobs", "2", "--flake-option", "--override-input",
                                     "--flake-option", "localConfig", "--flake-option", f"path:{tmp_path / 'projection'}"]
    else:
        assert qualification == exact
    assert sum(label == "exact Darwin target build" for label, _ in commands) == 1


def _local_plan_document(tmp_path: Path, profile: dict[str, object]) -> dict[str, object]:
    return {
        "selected_flake": str(tmp_path.resolve()),
        "host": "test-host",
        "target_system": "/nix/store/target-system",
        "git_authority": {
            "commit": "1" * 40,
            "status": "",
            "operation_state": "clean",
        },
        "profile_snapshot": profile,
        "predecessor_generation": 1,
        "sources": {"verified_count": 1, "sources": []},
        "effects": {"status": "absent"},
        "preflight_results": [],
        "closure_delta": {
            "added": [{"path": "/nix/store/target-system"}],
            "removed": [{"path": "/nix/store/live-system"}],
        },
    }


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("target_system", "/nix/store/other", "target system changed"),
        ("selected_flake", "/other/flake", "selected flake does not match"),
        ("host", "other-host", "host does not match"),
        ("git_authority", {"commit": "2" * 40}, "Git authority changed"),
        ("profile_snapshot", {}, "profile state changed"),
        ("predecessor_generation", 99, "predecessor generation changed"),
        ("sources", {}, "source authority changed"),
        ("effects", {}, "deployment effects changed"),
        ("preflight_results", [{}], "preflight results changed"),
        ("closure_delta", {}, "closure delta changed"),
    ],
)
def test_deploy_local_rejects_each_stale_local_plan_authority(
    tmp_path: Path, monkeypatch, field, replacement, message
):
    profile = _profile()
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[profile]
    )
    document = _local_plan_document(tmp_path, profile)
    document[field] = replacement
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.load_local_plan",
        lambda path: SimpleNamespace(document=document),
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.require_terminal_confirmation",
        lambda: pytest.fail("stale plan must stop before confirmation"),
    )

    with pytest.raises(ConfigError, match=message):
        deploy_local(
            workflow,
            tmp_path,
            "test-host",
            control_dir,
            [],
            "",
            "",
            plan_path=tmp_path / "plan",
        )
    assert all(label != "single Darwin switch" for label, _ in commands)


def test_deploy_local_accepts_matching_revalidated_local_plan(
    tmp_path: Path, monkeypatch
):
    initial = _profile()
    post = _profile(
        live_target="/nix/store/target-system",
        selected_target="/nix/store/target-system",
        predecessor_target="/nix/store/live-system",
    )
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[initial, initial, initial, post]
    )
    document = _local_plan_document(tmp_path, initial)
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.load_local_plan",
        lambda path: SimpleNamespace(document=document),
    )

    status = deploy_local(
        workflow,
        tmp_path,
        "test-host",
        control_dir,
        [],
        "",
        "",
        plan_path=tmp_path / "plan",
    )
    assert status == 0
    assert sum(label == "single Darwin switch" for label, _ in commands) == 1


def test_deploy_local_divergent_lock_switch_uses_projected_scratch_and_verifies_target(
    tmp_path: Path, monkeypatch
):
    """Prove that when canonical selected lock and projected scratch lock diverge:
    - canonical selected flake lock corresponds to target A;
    - projected scratch lock corresponds to target B;
    - switch command points --flake to scratch_target (NOT canonical selected);
    - switch target evaluation corresponds to projected target B;
    - post-switch verification verifies target B;
    - journal records switch_flake='projected-scratch' and target B.
    """
    target_a = "/nix/store/target-a-canonical"
    target_b = "/nix/store/target-b-projected"
    predecessor_target = "/nix/store/live-system"

    initial_profile = _profile(live_target=predecessor_target, selected_target=predecessor_target)
    post_profile = _profile(
        live_target=target_b,
        selected_target=target_b,
        predecessor_target=predecessor_target,
    )

    canonical_lock = {
        "nodes": {
            "root": {"inputs": {"dep": "dep"}},
            "dep": {"locked": {"type": "git", "rev": "a" * 40, "narHash": "sha256-canonical="}},
        },
        "root": "root",
        "version": 7,
    }
    (tmp_path / "flake.lock").write_text(json.dumps(canonical_lock, indent=2) + "\n")

    workflow, control_dir, commands = _configure_switch_case(
        tmp_path,
        monkeypatch,
        profiles=[initial_profile, initial_profile, initial_profile, post_profile],
    )

    def mock_project_candidate_lock(wf, scratch, revs):
        projected = {
            "nodes": {
                "root": {"inputs": {"dep": "dep"}},
                "dep": {"locked": {"type": "git", "rev": "b" * 40, "narHash": "sha256-projected="}},
            },
            "root": "root",
            "version": 7,
        }
        (scratch / "flake.lock").write_text(json.dumps(projected, indent=2) + "\n")

    monkeypatch.setattr("nix_darwin_local.local_source.project_candidate_lock", mock_project_candidate_lock)

    observed_switch_locks = []

    def command_divergent(label, argv, cwd):
        commands.append((label, list(argv)))
        if label in {"exact Darwin target build", "local target revalidation"}:
            assert cwd != tmp_path
            assert (cwd / "flake.lock").is_file()
            lock_doc = json.loads((cwd / "flake.lock").read_text(encoding="utf-8"))
            assert lock_doc["nodes"]["dep"]["locked"]["rev"] == "b" * 40
            return SimpleNamespace(returncode=0, stdout=f"{target_b}\n")
        if label == "single Darwin switch":
            flake_arg = argv[argv.index("--flake") + 1]
            assert flake_arg.startswith("path:")
            switch_flake_path = Path(flake_arg.split("#")[0].removeprefix("path:"))
            # Proves --flake uses the projected scratch flake, not canonical selected
            assert switch_flake_path != tmp_path.resolve()
            assert "nix-update-local-" in str(switch_flake_path)
            lock_doc = json.loads((switch_flake_path / "flake.lock").read_text(encoding="utf-8"))
            assert lock_doc["nodes"]["dep"]["locked"]["rev"] == "b" * 40
            observed_switch_locks.append(lock_doc)
            return SimpleNamespace(returncode=0, stdout="")
        return SimpleNamespace(returncode=0, stdout="")

    workflow.command = command_divergent
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.closure_inventory",
        lambda *a: [{"path": target_b}],
    )

    from nix_darwin_local.config import EffectiveSourceRevisions

    status = deploy_local(
        workflow=workflow,
        selected=tmp_path,
        host="test-host",
        control_dir=control_dir,
        overrides=[],
        test_jobs="",
        build_jobs="",
        source_revisions=EffectiveSourceRevisions(
            active=False,
            revisions=(("dep", "b" * 40),),
            provenance=(("dep", "cli"),),
            configured_revisions=None,
        ),
    )

    assert status == 0
    switches = [argv for label, argv in commands if label == "single Darwin switch"]
    assert len(switches) == 1
    switch_argv = switches[0]
    flake_idx = switch_argv.index("--flake")
    switch_flake = switch_argv[flake_idx + 1]

    # 1. Assert --flake references projected scratch flake, NOT canonical selected
    assert switch_flake != f"path:{tmp_path.resolve()}#test-host"
    assert "nix-update-local-" in switch_flake
    assert switch_flake.endswith("#test-host")

    # 2. Assert consumed lock was divergent projected lock (rev b*40), not canonical (rev a*40)
    assert len(observed_switch_locks) == 1
    canonical_lock_text = (tmp_path / "flake.lock").read_text(encoding="utf-8")
    assert "a" * 40 in canonical_lock_text
    assert "b" * 40 not in canonical_lock_text
    assert observed_switch_locks[0]["nodes"]["dep"]["locked"]["rev"] == "b" * 40

    # 3. Post-switch verification succeeded for target_b (not canonical target_a)
    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "SUCCESS"
    assert journal["target"] == target_b
    assert journal["selected_flake"] == str(tmp_path)
    assert journal["switch_flake"] == "projected-scratch"


def test_deploy_local_divergent_lock_switch_rejects_canonical_target_mismatch(
    tmp_path: Path, monkeypatch
):
    """If darwin-rebuild switch had applied canonical target A instead of projected target B,
    post-switch verification fails closed and records POST_SWITCH_VERIFICATION_FAILURE.
    """
    target_a = "/nix/store/target-a-canonical"
    target_b = "/nix/store/target-b-projected"
    predecessor_target = "/nix/store/live-system"

    initial_profile = _profile(live_target=predecessor_target, selected_target=predecessor_target)
    post_profile_bug = _profile(
        live_target=target_a,
        selected_target=target_a,
        predecessor_target=predecessor_target,
    )

    workflow, control_dir, commands = _configure_switch_case(
        tmp_path,
        monkeypatch,
        profiles=[initial_profile, initial_profile, initial_profile, post_profile_bug],
    )

    def command_divergent(label, argv, cwd):
        commands.append((label, list(argv)))
        if label in {"exact Darwin target build", "local target revalidation"}:
            return SimpleNamespace(returncode=0, stdout=f"{target_b}\n")
        if label == "single Darwin switch":
            return SimpleNamespace(returncode=0, stdout="")
        return SimpleNamespace(returncode=0, stdout="")

    workflow.command = command_divergent
    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.closure_inventory",
        lambda *a: [{"path": target_b}],
    )

    status = deploy_local(
        workflow=workflow,
        selected=tmp_path,
        host="test-host",
        control_dir=control_dir,
        overrides=[],
        test_jobs="",
        build_jobs="",
    )

    assert status == 1
    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "POST_SWITCH_VERIFICATION_FAILURE"
    assert f"live system target {target_a} != {target_b}" in journal["error"]


def test_deploy_local_plan_replay_switches_from_reviewed_projected_lock(
    tmp_path: Path, monkeypatch
):
    """With a v3 reviewed plan containing a retained projected lock:
    - scratch is populated from the plan lock;
    - switch uses that scratch flake;
    - canonical selected lock is not substituted at mutation time.
    """
    target = "/nix/store/target-system"
    post = _profile(
        live_target=target,
        selected_target=target,
        predecessor_target="/nix/store/live-system",
    )
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[_profile(), _profile(), _profile(), post]
    )

    reviewed_lock = {
        "nodes": {
            "root": {"inputs": {"dep": "dep"}},
            "dep": {
                "locked": {
                    "type": "git",
                    "rev": "b" * 40,
                    "narHash": "sha256-projected-lock=",
                },
            },
        },
        "root": "root",
        "version": 7,
    }

    doc = _local_plan_document(tmp_path, _profile())
    doc.update({
        "schema": "nix-update-local-plan-v3",
        "lock": reviewed_lock,
        "local_sources": [],
        "source_snapshot": {
            "active": False,
            "configured_revisions": None,
            "effective_revisions": {},
            "provenance": {},
            "selected_source": None,
            "lock_backed_revisions": {},
        },
    })

    monkeypatch.setattr(
        "nix_darwin_local.local_deployment.load_local_plan",
        lambda path: SimpleNamespace(document=doc),
    )

    observed_scratch_locks = []
    orig_command = workflow.command
    def capture_switch(label, argv, cwd):
        if label == "single Darwin switch":
            flake_arg = argv[argv.index("--flake") + 1]
            scratch_dir = Path(flake_arg.split("#")[0].removeprefix("path:"))
            observed_scratch_locks.append((scratch_dir / "flake.lock").read_text(encoding="utf-8"))
        return orig_command(label, argv, cwd)

    workflow.command = capture_switch

    status = deploy_local(
        workflow,
        tmp_path,
        "test-host",
        control_dir,
        [],
        "",
        "",
        plan_path=tmp_path / "plan",
    )
    assert status == 0
    assert len(observed_scratch_locks) == 1
    consumed_lock = json.loads(observed_scratch_locks[0])
    assert consumed_lock["nodes"]["dep"]["locked"]["rev"] == "b" * 40
    switches = [argv for label, argv in commands if label == "single Darwin switch"]
    assert len(switches) == 1
    assert "nix-update-local-" in switches[0][switches[0].index("--flake") + 1]


def test_deploy_local_legacy_no_snapshot_switches_from_scratch_flake(
    tmp_path: Path, monkeypatch
):
    """Normal legacy deployment without snapshot still switches from materialized scratch flake."""
    post = _profile(
        live_target="/nix/store/target-system",
        selected_target="/nix/store/target-system",
        predecessor_target="/nix/store/live-system",
    )
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[_profile(), _profile(), _profile(), post]
    )

    status = deploy_local(
        workflow,
        tmp_path,
        "test-host",
        control_dir,
        [],
        "",
        "",
    )
    assert status == 0
    switches = [argv for label, argv in commands if label == "single Darwin switch"]
    assert len(switches) == 1
    flake_arg = switches[0][switches[0].index("--flake") + 1]
    assert flake_arg != f"path:{tmp_path.resolve()}#test-host"
    assert "nix-update-local-" in flake_arg
    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "SUCCESS"
    assert journal["switch_flake"] == "projected-scratch"


@pytest.mark.parametrize(
    ("drift_key", "drift_val"),
    [
        ("commit", "2" * 40),
        ("tree", "3" * 40),
        ("index_sha256", "4" * 64),
        ("status", "M flake.nix"),
    ],
)
def test_deploy_local_selected_git_authority_drift_fails_closed_before_switch(
    tmp_path: Path, monkeypatch, drift_key, drift_val
):
    """Change in selected HEAD/tree/index/worktree fails closed before switch."""
    initial = {
        "commit": "1" * 40,
        "tree": "1" * 40,
        "index_sha256": "1" * 64,
        "status": "",
        "operation_state": "clean",
    }
    changed = {**initial, drift_key: drift_val}
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path,
        monkeypatch,
        profiles=[_profile(), _profile()],
        git_identities=[initial, changed],
    )

    with pytest.raises(ConfigError, match="selected repository Git authority drifted"):
        deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")

    assert all(label != "single Darwin switch" for label, _ in commands)


@pytest.mark.parametrize("drift_mode", ["modify", "delete"])
def test_deploy_local_scratch_lock_drift_fails_closed_before_any_switch(
    tmp_path: Path, monkeypatch, drift_mode
):
    """Mutation or replacement/removal of projected scratch lock after qualification fails closed."""
    (tmp_path / "flake.lock").write_text('{"nodes":{"root":{}},"root":"root","version":7}\n')
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[_profile(), _profile(), _profile()]
    )

    orig_command = workflow.command
    def drift_lock_command(label, argv, cwd):
        if label == "local target revalidation":
            lock_path = cwd / "flake.lock"
            if drift_mode == "modify":
                lock_path.write_text('{"nodes": {"tampered": {}}, "root": "root", "version": 7}\n')
            elif drift_mode == "delete":
                lock_path.unlink()
        return orig_command(label, argv, cwd)

    workflow.command = drift_lock_command

    with pytest.raises(ConfigError, match="projected scratch lock drifted"):
        deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")
    assert all(label != "single Darwin switch" for label, _ in commands)


def test_deploy_local_post_sudo_scratch_lock_drift_is_journaled_before_switch(
    tmp_path: Path, monkeypatch
):
    """Mutation of projected scratch lock post-sudo is journaled as PRE_SWITCH_FAILURE."""
    (tmp_path / "flake.lock").write_text('{"nodes":{"root":{}},"root":"root","version":7}\n')
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[_profile(), _profile(), _profile()]
    )

    call_count = 0
    orig_command = workflow.command
    def drift_post_sudo(label, argv, cwd):
        nonlocal call_count
        if label == "local target revalidation":
            call_count += 1
            if call_count == 2:
                # Drift only on the second (post-sudo) revalidation
                (cwd / "flake.lock").write_text('{"nodes": {"tampered": {}}, "root": "root", "version": 7}\n')
        return orig_command(label, argv, cwd)

    workflow.command = drift_post_sudo

    status = deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")
    assert status == 1
    assert all(label != "single Darwin switch" for label, _ in commands)
    journal = _result_journal(tmp_path)
    assert journal["terminal_state"] == "PRE_SWITCH_FAILURE"
    assert journal["switch_invoked"] is False
    assert "projected scratch lock drifted" in journal["error"]


def test_deploy_local_scratch_tree_drift_fails_closed_before_switch(
    tmp_path: Path, monkeypatch
):
    """Mutation of a file in projected scratch directory (other than flake.lock) fails closed before switch."""
    (tmp_path / "flake.lock").write_text('{"nodes":{"root":{}},"root":"root","version":7}\n')
    (tmp_path / "extra.txt").write_text("initial\n")
    workflow, control_dir, commands = _configure_switch_case(
        tmp_path, monkeypatch, profiles=[_profile(), _profile(), _profile()]
    )

    orig_command = workflow.command
    def drift_tree_command(label, argv, cwd):
        if label == "local target revalidation":
            (cwd / "extra.txt").write_text("tampered\n")
        return orig_command(label, argv, cwd)

    workflow.command = drift_tree_command

    with pytest.raises(ConfigError, match="projected scratch target tree drifted"):
        deploy_local(workflow, tmp_path, "test-host", control_dir, [], "", "")
    assert all(label != "single Darwin switch" for label, _ in commands)


def test_deploy_local_plan_evidence_persists_canonical_selected_path_not_scratch(
    tmp_path: Path, monkeypatch
):
    """Prove plan creation persists canonical selected flake path in both top-level and sources evidence."""
    flake_dir = tmp_path / "composition"
    flake_dir.mkdir()
    lock_data = {
        "nodes": {
            "root": {"inputs": {"dep": "dep"}},
            "dep": {
                "locked": {
                    "type": "git",
                    "url": "ssh://git@example.invalid/dep.git",
                    "rev": "c" * 40,
                    "narHash": "sha256-hash-c=",
                }
            },
        },
        "root": "root",
        "version": 7,
    }
    (flake_dir / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    workflow, control_dir, commands = _configure_switch_case(
        flake_dir, monkeypatch, profiles=[_profile()]
    )
    from nix_darwin_local.deployment_sources import derive_source_identities as real_derive
    monkeypatch.setattr("nix_darwin_local.local_deployment.derive_source_identities", real_derive)

    orig_command = workflow.command
    def command_with_archive(label, argv, cwd):
        if label == "locked source tree realization":
            archive = {"path": "/nix/store/root", "inputs": {"dep": {"path": "/nix/store/dep-store"}}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(archive))
        if label == "source store path inspection":
            info = {"/nix/store/dep-store": {"narHash": "sha256-hash-c="}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(info))
        return orig_command(label, argv, cwd)

    workflow.command = command_with_archive
    workflow.complete_stdout = lambda label: {
        "locked source tree realization": json.dumps({"path": "/nix/store/root", "inputs": {"dep": {"path": "/nix/store/dep-store"}}}),
        "source store path inspection": json.dumps({"/nix/store/dep-store": {"narHash": "sha256-hash-c="}}),
        "closure comparison": "pkg: 1 -> 2\n",
    }.get(label, "")

    plan_dir = tmp_path / "plan-out"
    status = deploy_local(
        workflow,
        flake_dir,
        "test-host",
        control_dir,
        [],
        "",
        "",
        plan_only=True,
        plan_output=plan_dir,
    )
    assert status == 0
    from nix_darwin_local.local_deployment_plan import load_local_plan
    plan_art = load_local_plan(plan_dir)
    doc = plan_art.document
    assert doc["selected_flake"] == str(flake_dir.resolve(strict=False))
    assert doc["sources"]["selected_flake"] == str(flake_dir.resolve(strict=False))
    assert "nix-update-local-" not in doc["sources"]["selected_flake"]
    assert doc["sources"]["verified_count"] == 1


def test_deploy_local_cross_scratch_replay_passes_source_authority_and_switches_scratch(
    tmp_path: Path, monkeypatch
):
    """Prove that plan generated in scratch A replays in distinct scratch B without source authority failure."""
    flake_dir = tmp_path / "composition"
    flake_dir.mkdir()
    lock_data = {
        "nodes": {
            "root": {"inputs": {"dep": "dep"}},
            "dep": {
                "locked": {
                    "type": "git",
                    "url": "ssh://git@example.invalid/dep.git",
                    "rev": "c" * 40,
                    "narHash": "sha256-hash-c=",
                }
            },
        },
        "root": "root",
        "version": 7,
    }
    (flake_dir / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")

    initial = _profile()
    post = _profile(
        live_target="/nix/store/target-system",
        selected_target="/nix/store/target-system",
        predecessor_target="/nix/store/live-system",
    )
    workflow, control_dir, commands = _configure_switch_case(
        flake_dir, monkeypatch, profiles=[initial]
    )
    switched = False
    def mock_profile(*a):
        return post if switched else initial
    monkeypatch.setattr("nix_darwin_local.local_deployment.inspect_profile", mock_profile)

    from nix_darwin_local.deployment_sources import derive_source_identities as real_derive
    monkeypatch.setattr("nix_darwin_local.local_deployment.derive_source_identities", real_derive)

    orig_command = workflow.command
    def command_with_archive(label, argv, cwd):
        if label == "locked source tree realization":
            archive = {"path": "/nix/store/root", "inputs": {"dep": {"path": "/nix/store/dep-store"}}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(archive))
        if label == "source store path inspection":
            info = {"/nix/store/dep-store": {"narHash": "sha256-hash-c="}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(info))
        if label == "single Darwin switch":
            nonlocal switched
            switched = True
        return orig_command(label, argv, cwd)

    workflow.command = command_with_archive
    workflow.complete_stdout = lambda label: {
        "locked source tree realization": json.dumps({"path": "/nix/store/root", "inputs": {"dep": {"path": "/nix/store/dep-store"}}}),
        "source store path inspection": json.dumps({"/nix/store/dep-store": {"narHash": "sha256-hash-c="}}),
        "closure comparison": "pkg: 1 -> 2\n",
    }.get(label, "")

    # Plan creation (scratch A)
    plan_dir = tmp_path / "plan-out"
    status_plan = deploy_local(
        workflow,
        flake_dir,
        "test-host",
        control_dir,
        [],
        "",
        "",
        plan_only=True,
        plan_output=plan_dir,
    )
    assert status_plan == 0

    # Replay in independent transaction (scratch B)
    commands.clear()
    status_replay = deploy_local(
        workflow,
        flake_dir,
        "test-host",
        control_dir,
        [],
        "",
        "",
        plan_path=plan_dir,
    )
    assert status_replay == 0
    switches = [argv for label, argv in commands if label == "single Darwin switch"]
    assert len(switches) == 1
    switch_argv = switches[0]
    flake_idx = switch_argv.index("--flake")
    switch_flake = switch_argv[flake_idx + 1]
    assert "nix-update-local-" in switch_flake
    assert switch_flake.endswith("#test-host")


def test_deploy_local_cross_scratch_replay_v3_plan_passes_source_authority_and_switches_scratch(
    tmp_path: Path, monkeypatch
):
    """Prove that a v3 plan (catalog + snapshot + projected lock) created in scratch A
    replays in distinct scratch B:
    - plan schema is nix-update-local-plan-v3;
    - sources.selected_flake is canonical selected path, not scratch A;
    - replay evaluates scratch B and derives sources.selected_flake as canonical selected path;
    - sources match and pre-switch source authority revalidation passes;
    - single Darwin switch command is executed against scratch B."""
    from nix_darwin_local.config import ControlConfig, SourceCatalogEntry, SourceSnapshot, resolve_effective_source_revisions
    from nix_darwin_local.deployment_sources import derive_source_identities as real_derive
    from nix_darwin_local.local_deployment_plan import load_local_plan

    flake_dir = tmp_path / "composition"
    flake_dir.mkdir()
    lock_data = {
        "nodes": {
            "root": {"inputs": {"dep": "dep"}},
            "dep": {
                "locked": {
                    "type": "git",
                    "url": "ssh://git@example.invalid/dep.git",
                    "rev": "c" * 40,
                    "narHash": "sha256-hash-c=",
                }
            },
        },
        "root": "root",
        "version": 7,
    }
    (flake_dir / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")

    initial = _profile()
    post = _profile(
        live_target="/nix/store/target-system",
        selected_target="/nix/store/target-system",
        predecessor_target="/nix/store/live-system",
    )
    workflow, control_dir, commands = _configure_switch_case(
        flake_dir, monkeypatch, profiles=[initial]
    )
    switched = False
    def mock_profile(*a):
        return post if switched else initial
    monkeypatch.setattr("nix_darwin_local.local_deployment.inspect_profile", mock_profile)

    entry = SourceCatalogEntry("composition-private", flake_dir, "ssh://git@example.invalid/comp.git", "refs/heads/main")
    workflow.sources = (entry,)

    snap = SourceSnapshot((("composition-private", "1" * 40),))
    cfg = ControlConfig(sources=(entry,), snapshot=snap)
    eff = resolve_effective_source_revisions(cfg, {})

    monkeypatch.setattr(
        "nix_darwin_local.local_source_binding.capture_local_sources",
        lambda *a: [],
    )
    monkeypatch.setattr(
        "nix_darwin_local.local_source_binding.revalidate_local_sources",
        lambda *a: None,
    )
    monkeypatch.setattr("nix_darwin_local.local_deployment.derive_source_identities", real_derive)

    orig_command = workflow.command
    def command_with_archive(label, argv, cwd):
        if label == "locked source tree realization":
            archive = {"path": "/nix/store/root", "inputs": {"dep": {"path": "/nix/store/dep-store"}}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(archive))
        if label == "source store path inspection":
            info = {"/nix/store/dep-store": {"narHash": "sha256-hash-c="}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(info))
        if label == "single Darwin switch":
            nonlocal switched
            switched = True
        return orig_command(label, argv, cwd)

    workflow.command = command_with_archive
    workflow.complete_stdout = lambda label: {
        "locked source tree realization": json.dumps({"path": "/nix/store/root", "inputs": {"dep": {"path": "/nix/store/dep-store"}}}),
        "source store path inspection": json.dumps({"/nix/store/dep-store": {"narHash": "sha256-hash-c="}}),
        "closure comparison": "pkg: 1 -> 2\n",
    }.get(label, "")

    # Plan creation (scratch A)
    plan_dir = tmp_path / "plan-out"
    status_plan = deploy_local(
        workflow,
        flake_dir,
        "test-host",
        control_dir,
        [],
        "",
        "",
        plan_only=True,
        plan_output=plan_dir,
        source_revisions=eff,
    )
    assert status_plan == 0

    plan_art = load_local_plan(plan_dir)
    doc = plan_art.document
    assert doc["schema"] == "nix-update-local-plan-v3"
    assert doc["source_snapshot"]["active"] is True
    assert doc["selected_flake"] == str(flake_dir.resolve(strict=False))
    assert doc["sources"]["selected_flake"] == str(flake_dir.resolve(strict=False))
    assert "nix-update-local-" not in doc["sources"]["selected_flake"]

    # Replay in independent transaction (scratch B)
    commands.clear()
    status_replay = deploy_local(
        workflow,
        flake_dir,
        "test-host",
        control_dir,
        [],
        "",
        "",
        plan_path=plan_dir,
        source_revisions=eff,
    )
    assert status_replay == 0
    switches = [argv for label, argv in commands if label == "single Darwin switch"]
    assert len(switches) == 1
    switch_argv = switches[0]
    flake_idx = switch_argv.index("--flake")
    switch_flake = switch_argv[flake_idx + 1]
    assert "nix-update-local-" in switch_flake
    assert switch_flake.endswith("#test-host")


_TARBALL_URL = "https://github.com/nix-community/home-manager/archive/release-25.05.tar.gz"
_TARBALL_NAR = "sha256-8FJsFqr1zQPaFYCzHKD9el3kk5Y2qOoXKfHV1RKrGrQ="


def _tarball_plan_case(tmp_path: Path, monkeypatch, *, tarball_locked, store_nar=_TARBALL_NAR):
    """Ordinary plan over a composition lock whose home-manager input is a locked tarball."""
    from nix_darwin_local.deployment_sources import derive_source_identities as real_derive

    flake_dir = tmp_path / "composition"
    flake_dir.mkdir()
    lock_data = {
        "nodes": {
            "root": {"inputs": {"nixpkgs": "nixpkgs", "home-manager": "home-manager"}},
            "nixpkgs": {
                "locked": {
                    "type": "github",
                    "owner": "NixOS",
                    "repo": "nixpkgs",
                    "rev": "d" * 40,
                    "narHash": "sha256-nixpkgs-d=",
                }
            },
            "home-manager": {
                "inputs": {"nixpkgs": ["nixpkgs"]},
                "locked": tarball_locked,
                "original": {"type": "tarball", "url": _TARBALL_URL},
            },
        },
        "root": "root",
        "version": 7,
    }
    (flake_dir / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    workflow, control_dir, commands = _configure_switch_case(
        flake_dir, monkeypatch, profiles=[_profile()]
    )
    monkeypatch.setattr("nix_darwin_local.local_deployment.derive_source_identities", real_derive)
    archive = {
        "path": "/nix/store/root",
        "inputs": {
            "nixpkgs": {"path": "/nix/store/nixpkgs-store"},
            "home-manager": {"path": "/nix/store/home-manager-store"},
        },
    }
    info = {
        "/nix/store/nixpkgs-store": {"narHash": "sha256-nixpkgs-d="},
        "/nix/store/home-manager-store": {"narHash": store_nar},
    }
    workflow.complete_stdout = lambda label: {
        "locked source tree realization": json.dumps(archive),
        "source store path inspection": json.dumps(info),
        "closure comparison": "pkg: 1 -> 2\n",
    }.get(label, "")
    return workflow, flake_dir, control_dir, commands


def test_deploy_local_plan_accepts_hash_pinned_tarball_source(tmp_path: Path, monkeypatch):
    tarball = {"type": "tarball", "url": _TARBALL_URL, "narHash": _TARBALL_NAR}
    workflow, flake_dir, control_dir, commands = _tarball_plan_case(
        tmp_path, monkeypatch, tarball_locked=tarball
    )
    plan_dir = tmp_path / "plan-out"
    status = deploy_local(
        workflow, flake_dir, "test-host", control_dir, [], "", "",
        plan_only=True, plan_output=plan_dir,
    )
    assert status == 0
    from nix_darwin_local.local_deployment_plan import load_local_plan
    doc = load_local_plan(plan_dir).document
    assert doc["sources"]["verified_count"] == 2
    sources = {source["name"]: source for source in doc["sources"]["sources"]}
    assert sources["home-manager"] == {
        "name": "home-manager",
        "type": "tarball",
        "revision": None,
        "narHash": _TARBALL_NAR,
        "store_path": "/nix/store/home-manager-store",
        "url": _TARBALL_URL,
    }
    labels = [label for label, _ in commands]
    assert "selected-flake report qualification" in labels
    assert "single Darwin switch" not in labels


@pytest.mark.parametrize(
    ("tarball", "store_nar", "message"),
    [
        (
            {"type": "tarball", "url": _TARBALL_URL, "narHash": _TARBALL_NAR},
            "sha256-2SmKENGwc1g33EvYXaxkGw887yekfl1TpU8vP1svz/o=",
            "source NAR hash for input home-manager not resolved",
        ),
        (
            {"type": "tarball", "url": _TARBALL_URL.replace("https:", "http:"), "narHash": _TARBALL_NAR},
            _TARBALL_NAR,
            "hash-pinned tarball input home-manager must have a nonempty HTTPS URL",
        ),
    ],
)
def test_deploy_local_plan_rejects_unverified_tarball_before_qualification(
    tmp_path: Path, monkeypatch, tarball, store_nar, message
):
    workflow, flake_dir, control_dir, commands = _tarball_plan_case(
        tmp_path, monkeypatch, tarball_locked=tarball, store_nar=store_nar
    )
    plan_dir = tmp_path / "plan-out"
    with pytest.raises(ConfigError, match=message):
        deploy_local(
            workflow, flake_dir, "test-host", control_dir, [], "", "",
            plan_only=True, plan_output=plan_dir,
        )
    labels = [label for label, _ in commands]
    assert "selected-flake report qualification" not in labels
    assert "single Darwin switch" not in labels
    assert not (plan_dir / "plan.json").exists()
