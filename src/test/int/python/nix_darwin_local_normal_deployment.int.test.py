# nix-darwin-test-source-target: libexec/nix_darwin_local/local_deployment.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/launcher.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/local_deployment_plan.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/deployment_sources.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/deployment_effects.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/deployment_summary.py
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local import launcher
from nix_darwin_local.local_deployment_plan import load_local_plan


@pytest.mark.parametrize("failure,drift", [
    (False, None), (True, None), (False, "retarget"),
    (False, "nonexec"), (False, "missing"),
])
def test_local_realignment_multicall_endpoint(tmp_path, monkeypatch, failure, drift, capsys):
    """Execute a basename-sensitive endpoint, with only disposable profile links."""
    from nix_darwin_local import local_deployment as local
    from nix_darwin_local import deployment_workflow as deployment
    from nix_darwin_local.deployment_state import inspect_profile

    core = tmp_path / "core"
    bins = core / "bin"
    bins.mkdir(parents=True)
    for name in ("git", "sudo", "nix-update", "nix-rebuild", "darwin-rebuild"):
        endpoint = bins / name
        endpoint.write_text('#!/bin/sh\nexec "$@"\n' if name == "sudo" else "#!/bin/sh\nexit 0\n")
        endpoint.chmod(0o700)
    nix = bins / "nix"
    nix.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys\n"
        "if pathlib.Path(sys.argv[0]).name != 'nix-env':\n"
        "    print(\"error: unrecognised flag '--profile'\", file=sys.stderr)\n"
        "    sys.exit(9)\n"
        "assert sys.argv[1] == '--profile' and sys.argv[3] == '--switch-generation'\n"
        f"if {failure!r}:\n"
        "    print('error: permission denied; token=fixture-secret ' + 'x' * 10000, file=sys.stderr)\n"
        "    sys.exit(7)\n"
        "profile = pathlib.Path(sys.argv[2])\n"
        "assert profile.parent == pathlib.Path(__file__).resolve().parents[2] / 'profiles'\n"
        "profile.unlink()\n"
        "profile.symlink_to('system-' + sys.argv[4] + '-link')\n"
    )
    nix.chmod(0o700)
    invocation = bins / "nix-env"
    invocation.symlink_to("nix")
    monkeypatch.setattr(deployment.shutil, "which", lambda name: str(bins / name))
    original_identity = deployment._executable_identity
    def endpoint_identity(path, **kwargs):
        if path == Path("/run/current-system/sw/bin/darwin-rebuild"):
            path = bins / "darwin-rebuild"
        return original_identity(path, **kwargs)
    monkeypatch.setattr(deployment, "_executable_identity", endpoint_identity)
    identity = deployment._tool_identity(core)
    assert identity["external"]["nix-env"] == original_identity(invocation)
    assert identity["external"]["nix-env"]["path"] == str(invocation)
    assert identity["external"]["nix-env"]["resolved"] == str(nix.resolve())
    wrong = subprocess.run([str(nix), "--profile"], capture_output=True, text=True)
    assert wrong.returncode == 9 and "unrecognised flag" in wrong.stderr

    profiles = tmp_path / "profiles"
    profiles.mkdir()
    for generation in (1, 2):
        target = tmp_path / f"target-{generation}"
        target.mkdir()
        (profiles / f"system-{generation}-link").symlink_to(target)
    selected = profiles / "system"
    selected.symlink_to("system-2-link")
    live = profiles / "live"
    live.symlink_to(tmp_path / "target-1")
    def snapshot(*args):
        return inspect_profile(selected, live, profiles / "system-*-link")
    before = snapshot()
    monkeypatch.setattr(local, "inspect_profile", snapshot)
    monkeypatch.setattr(local, "operator_home", lambda: tmp_path)
    monkeypatch.setattr(local, "_guard_processes", lambda *a, **kw: None)
    monkeypatch.setattr(local, "local_git_identity", lambda *a: {"commit": "a" * 40, "clean": True})
    monkeypatch.setattr(local, "evaluate_allowlist", lambda *a: {})
    monkeypatch.setattr(local, "evaluate_deployment_effects", lambda *a: {"status": "absent"})
    monkeypatch.setattr(local, "derive_source_identities", lambda *a, **kw: {"verified_count": 0, "sources": []})
    monkeypatch.setattr(local, "run_read_only_preflights", lambda *a: [])
    monkeypatch.setattr(local, "closure_inventory", lambda *a: [])
    monkeypatch.setattr(local, "current_live_closure", lambda *a: [])
    def confirm():
        if drift == "retarget":
            invocation.unlink()
            invocation.symlink_to("git")
        elif drift == "nonexec":
            nix.chmod(0o600)
        elif drift == "missing":
            invocation.unlink()
        return True
    monkeypatch.setattr(local, "require_terminal_confirmation", confirm)
    monkeypatch.setattr(local, "ensure_sudo_credentials", lambda path: path == str(bins / "sudo"))
    control = tmp_path / "control"
    control.mkdir()
    workflow = deployment.Workflow(core, display_command_output=False)
    workflow.evidence_root = tmp_path / "evidence"
    if failure:
        # Keep the final command evidence path near its supported 600-character
        # bound, so journal truncation cannot silently cut off the pointer.
        workflow.evidence_root /= "a" * 180
        workflow.evidence_root /= "b" * 180
        workflow.evidence_root /= "c" * (570 - len(str(workflow.evidence_root)))
    workflow.evidence_root.mkdir(mode=0o700, parents=True)
    real_command = workflow.command
    commands = []
    def command(label, argv, cwd):
        commands.append((label, list(argv)))
        if label == "selected-profile realignment":
            return real_command(label, argv, cwd)
        if label == "single Darwin switch":
            # Stop at the switch boundary without executing any activation.
            return SimpleNamespace(returncode=8, stdout="")
        return SimpleNamespace(returncode=0, stdout="/nix/store/fixture-target\n")
    workflow.command = command
    workflow.complete_stdout = lambda label: ""
    if drift:
        monkeypatch.setattr(local, "ensure_sudo_credentials", lambda path: pytest.fail("drift must fail before privilege"))
        with pytest.raises(local.ConfigError, match="identity drifted|missing or not executable"):
            local.deploy_local(workflow, core, "fixture", control, [], "", "")
        assert snapshot() == before
        assert not any(label in {"selected-profile realignment", "single Darwin switch"} for label, _ in commands)
        return
    assert local.deploy_local(workflow, core, "fixture", control, [], "", "") == 1
    realignments = [argv for label, argv in commands if label == "selected-profile realignment"]
    assert realignments == [[str(bins / "sudo"), str(invocation), "--profile", str(selected), "--switch-generation", "1"]]
    after = snapshot()
    assert after["live"] == before["live"]
    assert after["retained"] == before["retained"]
    journal = json.loads(next((tmp_path / ".local/state/nix-update").glob("run-*/journal.json")).read_text())
    if failure:
        assert after == before
        assert not any(label == "single Darwin switch" for label, _ in commands)
        assert "permission denied" in journal["error"]
        assert "exit 7" in journal["error"]
        assert "private evidence:" in journal["error"]
        pointer = journal["error"].split("; private evidence: ")[1]
        assert pointer != "unavailable" and Path(pointer).is_file()
        assert len(pointer) > 570
        assert len(journal["error"].removeprefix("ConfigError: ")) <= 2048
        assert "fixture-secret" not in journal["error"]
        assert len(journal["error"]) < 2400
        assert "fixture-secret" not in capsys.readouterr().err
    else:
        assert after["selected"]["generation_number"] == 1
        assert after["selected"]["target"] == before["live"]["target"]
        assert sum(label == "single Darwin switch" for label, _ in commands) == 1


def make_disposable_flake(repo_path: Path) -> Path:
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Committer"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo_path, check=True)
    (repo_path / "flake.nix").write_text("{ description = \"disposable\"; }\n")
    lock_data = {
        "nodes": {
            "root": {"inputs": {"nixpkgs": "nixpkgs"}},
            "nixpkgs": {
                "locked": {
                    "type": "github",
                    "owner": "NixOS",
                    "repo": "nixpkgs",
                    "rev": "1234567890123456789012345678901234567890",
                    "narHash": "sha256-hash1=",
                }
            }
        },
        "root": "root",
        "version": 7,
    }
    (repo_path / "flake.lock").write_text(json.dumps(lock_data, indent=2) + "\n")
    subprocess.run(["git", "add", "flake.nix", "flake.lock"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_path, check=True, capture_output=True)
    return repo_path


def test_normal_deployment_int_dry_run_and_plan_only(tmp_path: Path, monkeypatch):
    flake_dir = make_disposable_flake(tmp_path / "composition")
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr("nix_darwin_local.config.operator_home", lambda: tmp_path)
    monkeypatch.setattr("nix_darwin_local.local_deployment.operator_home", lambda: tmp_path)
    monkeypatch.setattr("nix_darwin_local.launcher.operator_home", lambda: tmp_path)
    monkeypatch.delenv("NIX_DARWIN_LOCAL", raising=False)
    monkeypatch.delenv("NIX_DARWIN_ACTIVE_FLAKE", raising=False)
    monkeypatch.setattr("nix_darwin_local.local_deployment._guard_processes", lambda *a, **kw: None)

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

    git_outputs = {}

    def mock_command(self, label, argv, cwd):
        if argv[0] == "git":
            res = subprocess.run(argv, cwd=cwd, text=True, capture_output=True)
            git_outputs[label] = res.stdout
            return res
        if label == "locked source tree realization":
            archive_data = {"path": "/nix/store/archive-root", "inputs": {"nixpkgs": {"path": "/nix/store/nixpkgs-p"}}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(archive_data))
        if label == "source store path inspection":
            return SimpleNamespace(returncode=0, stdout=json.dumps({"/nix/store/nixpkgs-p": {"narHash": "sha256-hash1="}}))
        if label == "exact Darwin target build":
            return SimpleNamespace(returncode=0, stdout="/nix/store/built-target-system\n")
        return SimpleNamespace(returncode=0, stdout="")

    def mock_complete_stdout(self, label):
        if label in git_outputs:
            return git_outputs[label]
        return {
            "locked source tree realization": json.dumps({"path": "/nix/store/archive-root", "inputs": {"nixpkgs": {"path": "/nix/store/nixpkgs-p"}}}),
            "source store path inspection": json.dumps({"/nix/store/nixpkgs-p": {"narHash": "sha256-hash1="}}),
            "closure comparison": "pkg: 1.0 -> 2.0\n",
        }.get(label, "")

    monkeypatch.setattr("nix_darwin_local.deployment_workflow.Workflow.command", mock_command)
    monkeypatch.setattr("nix_darwin_local.deployment_workflow.Workflow.complete_stdout", mock_complete_stdout)

    monkeypatch.setattr("nix_darwin_local.local_deployment.closure_inventory", lambda *a: [{"path": "/nix/store/built-target-system"}])
    monkeypatch.setattr("nix_darwin_local.local_deployment.current_live_closure", lambda *a: [{"path": "/nix/store/t1"}])

    config_path = tmp_path / ".local/nix-darwin/config/nix-darwin.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(f"[flake]\nactive = \"{flake_dir}\"\n")

    # Test 1: dry-run via launcher main
    rc_dry = launcher.main(CORE_ROOT, ["--dry-run"])
    assert rc_dry == 0

    # Test 2: plan-only via launcher main
    plan_dir = tmp_path / "plan_dir"
    rc_plan = launcher.main(CORE_ROOT, ["deploy", "--plan-only", "--output", str(plan_dir)])
    assert rc_plan == 0
    assert (plan_dir / "plan.json").is_file()
    artifact = load_local_plan(plan_dir)
    assert artifact.document["target_system"] == "/nix/store/built-target-system"
    assert artifact.document["schema"] == "nix-update-local-plan-v1"


@pytest.mark.parametrize("same_target", [False, True])
def test_normal_deployment_int_switch_invokes_projected_scratch_flake(tmp_path: Path, monkeypatch, capsys, same_target):
    """Integration test proving mutating switch invokes --flake against projected scratch target, not canonical selected."""
    flake_dir = make_disposable_flake(tmp_path / "composition")
    state_root = tmp_path / ".local/state/nix-update"
    state_root.mkdir(mode=0o700, parents=True)
    monkeypatch.setattr("nix_darwin_local.config.operator_home", lambda: tmp_path)
    monkeypatch.setattr("nix_darwin_local.local_deployment.operator_home", lambda: tmp_path)
    monkeypatch.setattr("nix_darwin_local.launcher.operator_home", lambda: tmp_path)
    monkeypatch.delenv("NIX_DARWIN_LOCAL", raising=False)
    monkeypatch.delenv("NIX_DARWIN_ACTIVE_FLAKE", raising=False)
    monkeypatch.setenv("NIX_DARWIN_CONFIG", "test-host")
    monkeypatch.setattr("nix_darwin_local.local_deployment._guard_processes", lambda *a, **kw: None)

    target_path = "/nix/store/built-target-system"
    predecessor_target = target_path if same_target else "/nix/store/t1"
    from nix_darwin_local.deployment_state import inspect_profile

    profiles = tmp_path / "profiles"
    profiles.mkdir()
    old_target = tmp_path / "old-target"
    old_target.mkdir()
    new_target = old_target if same_target else tmp_path / "new-target"
    new_target.mkdir(exist_ok=True)
    (profiles / "system-1-link").symlink_to(old_target)
    selected = profiles / "system"
    selected.symlink_to("system-1-link")
    live = profiles / "current-system"
    live.symlink_to(old_target)
    target_names = {str(old_target): predecessor_target, str(new_target): target_path}

    def snapshot(*args):
        inspected = inspect_profile(selected, live, profiles / "system-*-link")
        # Translate only target values to the build boundary's store namespace.
        # Numbered identity, retention, link metadata and topology come from
        # real disposable symlinks; no host store path or activation is needed.
        for identity in [inspected["selected"], inspected["live"], *inspected["retained"]]:
            for key in ("target", "resolved_target"):
                if key in identity:
                    identity[key] = target_names[identity[key]]
        return inspected

    before = snapshot()
    monkeypatch.setattr("nix_darwin_local.local_deployment.inspect_profile", snapshot)
    monkeypatch.setattr("nix_darwin_local.local_deployment.evaluate_allowlist", lambda *a: [])
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir(mode=0o700)
    scratch_root.chmod(0o700)
    scratch_projection = SimpleNamespace(root=scratch_root, inventory=(), digest="test-digest")
    monkeypatch.setattr("nix_darwin_local.local_deployment.render_scratch", lambda *a: scratch_projection)
    monkeypatch.setattr("nix_darwin_local.local_deployment.inspect_scratch", lambda *a: scratch_projection)
    monkeypatch.setattr("nix_darwin_local.local_deployment.evaluate_deployment_effects", lambda *a: {"status": "absent"})
    monkeypatch.setattr("nix_darwin_local.local_deployment.closure_inventory", lambda *a: [{"path": target_path}])
    monkeypatch.setattr("nix_darwin_local.local_deployment.current_live_closure", lambda *a: [{"path": predecessor_target}])
    confirmations = []
    def confirm():
        output = capsys.readouterr().out
        if same_target:
            assert "reapplication notice:" in output
            assert "reapplication will run activation hooks" in output
        confirmations.append(True)
        return True
    monkeypatch.setattr("nix_darwin_local.local_deployment.require_terminal_confirmation", confirm)
    monkeypatch.setattr("nix_darwin_local.local_deployment.ensure_sudo_credentials", lambda *a: True)

    commands_executed: list[tuple[str, list[str]]] = []
    git_outputs = {}

    def mock_command(self, label, argv, cwd):
        commands_executed.append((label, list(argv)))
        if argv[0] == "git":
            res = subprocess.run(argv, cwd=cwd, text=True, capture_output=True)
            git_outputs[label] = res.stdout
            return res
        if label == "locked source tree realization":
            archive_data = {"path": "/nix/store/archive-root", "inputs": {"nixpkgs": {"path": "/nix/store/nixpkgs-p"}}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(archive_data))
        if label == "source store path inspection":
            return SimpleNamespace(returncode=0, stdout=json.dumps({"/nix/store/nixpkgs-p": {"narHash": "sha256-hash1="}}))
        if label in {"exact Darwin target build", "local target revalidation"}:
            return SimpleNamespace(returncode=0, stdout=f"{target_path}\n")
        if label == "single Darwin switch":
            assert not (profiles / "system-2-link").exists()
            (profiles / "system-2-link").symlink_to(new_target)
            selected.unlink()
            selected.symlink_to("system-2-link")
            live.unlink()
            live.symlink_to(new_target)
            return SimpleNamespace(returncode=0, stdout="")
        assert label in {"closure comparison", "selected-flake report qualification"}, f"unexpected command: {label}"
        return SimpleNamespace(returncode=0, stdout="")

    def mock_complete_stdout(self, label):
        if label in git_outputs:
            return git_outputs[label]
        return {
            "locked source tree realization": json.dumps({"path": "/nix/store/archive-root", "inputs": {"nixpkgs": {"path": "/nix/store/nixpkgs-p"}}}),
            "source store path inspection": json.dumps({"/nix/store/nixpkgs-p": {"narHash": "sha256-hash1="}}),
            "closure comparison": "" if same_target else "pkg: 1.0 -> 2.0\n",
        }.get(label, "")

    monkeypatch.setattr("nix_darwin_local.deployment_workflow.Workflow.command", mock_command)
    monkeypatch.setattr("nix_darwin_local.deployment_workflow.Workflow.complete_stdout", mock_complete_stdout)

    config_path = tmp_path / ".local/nix-darwin/config/nix-darwin.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(f"[flake]\nactive = \"{flake_dir}\"\n")

    rc = launcher.main(CORE_ROOT, ["deploy"])
    assert rc == 0

    switches = [argv for label, argv in commands_executed if label == "single Darwin switch"]
    assert len(switches) == 1
    switch_argv = switches[0]
    flake_idx = switch_argv.index("--flake")
    switch_flake = switch_argv[flake_idx + 1]

    # Prove switch authority is projected scratch flake, not canonical active flake
    assert switch_flake != f"path:{flake_dir.resolve()}#test-host"
    assert "nix-update-local-" in switch_flake
    assert switch_flake.endswith("#test-host")

    journal_files = list(state_root.glob("run-*/journal.json"))
    assert len(journal_files) == 1
    journal = json.loads(journal_files[0].read_text(encoding="utf-8"))
    assert journal["terminal_state"] == "SUCCESS"
    assert journal["selected_flake"] == str(flake_dir)
    assert journal["switch_flake"] == "projected-scratch"
    assert journal["target"] == target_path

    assert journal["same_target_reapplication"] is same_target
    assert journal["predecessor_generation"] == 1
    assert journal["realignment_invoked"] is False
    after = snapshot()
    assert after["retained"][0] == before["retained"][0]
    assert len(after["retained"]) == 2
    assert after["selected"]["generation_number"] == 2
    if same_target:
        assert {item["target"] for item in after["retained"]} == {target_path}
        assert journal["closure_delta"]["added"] == []
        assert journal["closure_delta"]["removed"] == []

    # The next normal inspection must consume the successful transition.
    monkeypatch.setattr("nix_darwin_local.local_deployment.require_terminal_confirmation",
                        lambda: pytest.fail("plan-only must not confirm"))
    monkeypatch.setattr("nix_darwin_local.local_deployment.ensure_sudo_credentials",
                        lambda *a: pytest.fail("plan-only must not acquire privilege"))
    plan_dir = tmp_path / "next-plan"
    assert launcher.main(CORE_ROOT, ["deploy", "--plan-only", "--output", str(plan_dir)]) == 0
    plan = load_local_plan(plan_dir).document
    assert plan["predecessor_generation"] == 2
    assert plan["target_system"] == target_path
    assert snapshot() == after
    assert confirmations == [True]
    assert sum(label == "single Darwin switch" for label, _ in commands_executed) == 1
    assert len(list(state_root.glob("run-*/journal.json"))) == 1


def test_normal_deployment_int_cross_scratch_plan_and_replay(tmp_path: Path, monkeypatch):
    """Integration test proving cross-scratch plan replay succeeds:
    - plan created in scratch directory A records canonical selected path in sources.selected_flake;
    - plan replay executes in distinct scratch directory B;
    - source authority check succeeds because durable evidence uses canonical selected path;
    - switch uses scratch directory B;
    - journal records switch_flake='projected-scratch' and SUCCESS.
    """
    flake_dir = make_disposable_flake(tmp_path / "composition")
    state_root = tmp_path / ".local/state/nix-update"
    state_root.mkdir(mode=0o700, parents=True)
    monkeypatch.setattr("nix_darwin_local.config.operator_home", lambda: tmp_path)
    monkeypatch.setattr("nix_darwin_local.local_deployment.operator_home", lambda: tmp_path)
    monkeypatch.setattr("nix_darwin_local.launcher.operator_home", lambda: tmp_path)
    monkeypatch.delenv("NIX_DARWIN_LOCAL", raising=False)
    monkeypatch.delenv("NIX_DARWIN_ACTIVE_FLAKE", raising=False)
    monkeypatch.setenv("NIX_DARWIN_CONFIG", "test-host")
    monkeypatch.setattr("nix_darwin_local.local_deployment._guard_processes", lambda *a, **kw: None)

    target_path = "/nix/store/built-target-system"
    predecessor_target = "/nix/store/t1"

    switched = False
    def mock_inspect_profile(*a):
        if switched:
            return {"relationship": "aligned", "selected": {"generation_number": 2, "target": target_path, "link": "/nix/var/nix/profiles/system-2-link", "generation_path": "/nix/var/nix/profiles/system-2-link"},
                    "live": {"generation_number": None, "target": target_path}, "retained": [{"generation": 1, "target": predecessor_target, "path": "/nix/var/nix/profiles/system-1-link"}, {"generation": 2, "target": target_path, "path": "/nix/var/nix/profiles/system-2-link"}]}
        return {"relationship": "aligned", "selected": {"generation_number": 1, "target": predecessor_target, "link": "/nix/var/nix/profiles/system-1-link", "generation_path": "/nix/var/nix/profiles/system-1-link"},
                "live": {"generation_number": None, "target": predecessor_target}, "retained": [{"generation": 1, "target": predecessor_target, "path": "/nix/var/nix/profiles/system-1-link"}]}

    monkeypatch.setattr("nix_darwin_local.local_deployment.inspect_profile", mock_inspect_profile)
    monkeypatch.setattr("nix_darwin_local.local_deployment.evaluate_allowlist", lambda *a: [])
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir(mode=0o700)
    scratch_projection = SimpleNamespace(root=scratch_root, inventory=(), digest="test-digest")
    monkeypatch.setattr("nix_darwin_local.local_deployment.render_scratch", lambda *a: scratch_projection)
    monkeypatch.setattr("nix_darwin_local.local_deployment.inspect_scratch", lambda *a: scratch_projection)
    monkeypatch.setattr("nix_darwin_local.local_deployment.evaluate_deployment_effects", lambda *a: {"status": "absent"})
    monkeypatch.setattr("nix_darwin_local.local_deployment.closure_inventory", lambda *a: [{"path": target_path}])
    monkeypatch.setattr("nix_darwin_local.local_deployment.current_live_closure", lambda *a: [{"path": predecessor_target}])
    monkeypatch.setattr("nix_darwin_local.local_deployment.require_terminal_confirmation", lambda: True)
    monkeypatch.setattr("nix_darwin_local.local_deployment.ensure_sudo_credentials", lambda *a: True)

    commands_executed: list[tuple[str, list[str], Path]] = []
    git_outputs = {}

    def mock_command(self, label, argv, cwd):
        commands_executed.append((label, list(argv), Path(cwd)))
        if argv[0] == "git":
            res = subprocess.run(argv, cwd=cwd, text=True, capture_output=True)
            git_outputs[label] = res.stdout
            return res
        if label == "locked source tree realization":
            archive_data = {"path": "/nix/store/archive-root", "inputs": {"nixpkgs": {"path": "/nix/store/nixpkgs-p"}}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(archive_data))
        if label == "source store path inspection":
            return SimpleNamespace(returncode=0, stdout=json.dumps({"/nix/store/nixpkgs-p": {"narHash": "sha256-hash1="}}))
        if label in {"exact Darwin target build", "local target revalidation"}:
            return SimpleNamespace(returncode=0, stdout=f"{target_path}\n")
        if label == "single Darwin switch":
            nonlocal switched
            switched = True
            return SimpleNamespace(returncode=0, stdout="")
        return SimpleNamespace(returncode=0, stdout="")

    def mock_complete_stdout(self, label):
        if label in git_outputs:
            return git_outputs[label]
        return {
            "locked source tree realization": json.dumps({"path": "/nix/store/archive-root", "inputs": {"nixpkgs": {"path": "/nix/store/nixpkgs-p"}}}),
            "source store path inspection": json.dumps({"/nix/store/nixpkgs-p": {"narHash": "sha256-hash1="}}),
            "closure comparison": "pkg: 1.0 -> 2.0\n",
        }.get(label, "")

    monkeypatch.setattr("nix_darwin_local.deployment_workflow.Workflow.command", mock_command)
    monkeypatch.setattr("nix_darwin_local.deployment_workflow.Workflow.complete_stdout", mock_complete_stdout)

    res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=flake_dir, check=True, text=True, capture_output=True)
    flake_rev = res.stdout.strip()

    config_path = tmp_path / ".local/nix-darwin/config/nix-darwin.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        f"[flake]\n"
        f"active = \"{flake_dir}\"\n\n"
        f"[sources.composition]\n"
        f"checkout = \"{flake_dir}\"\n"
        f"repository = \"ssh://git@example.invalid/composition.git\"\n"
        f"release_ref = \"refs/heads/main\"\n\n"
        f"[snapshot.sources]\n"
        f"composition = \"{flake_rev}\"\n"
    )

    # Step 1: Create local plan in scratch directory A
    plan_dir = tmp_path / "plan-export"
    rc_plan = launcher.main(CORE_ROOT, ["deploy", "--plan-only", "--output", str(plan_dir)])
    assert rc_plan == 0

    # Locate scratch directory A from commands executed during plan-only
    scratch_a_dirs = {cwd for label, _, cwd in commands_executed if "nix-update-local-" in str(cwd)}
    assert len(scratch_a_dirs) == 1
    scratch_a = next(iter(scratch_a_dirs))

    # Verify durable plan evidence
    plan_art = load_local_plan(plan_dir)
    doc = plan_art.document
    assert doc["schema"] == "nix-update-local-plan-v3"
    assert doc["source_snapshot"]["active"] is True
    assert doc["selected_flake"] == str(flake_dir.resolve())
    assert doc["sources"]["selected_flake"] == str(flake_dir.resolve())
    assert "nix-update-local-" not in doc["sources"]["selected_flake"]
    assert doc["sources"]["selected_flake"] != str(scratch_a)
    assert doc["sources"]["verified_count"] == 1

    # Step 2: Replay plan in independent scratch directory B
    commands_executed.clear()
    rc_replay = launcher.main(CORE_ROOT, ["deploy", "--plan", str(plan_dir)])
    assert rc_replay == 0

    # Locate scratch directory B from commands executed during replay
    scratch_b_dirs = {cwd for label, _, cwd in commands_executed if "nix-update-local-" in str(cwd)}
    assert len(scratch_b_dirs) == 1
    scratch_b = next(iter(scratch_b_dirs))

    # Assert Scratch A and Scratch B are independently created with different absolute paths
    assert scratch_a != scratch_b

    # Verify switch was reached and uses scratch B
    switches = [argv for label, argv, _ in commands_executed if label == "single Darwin switch"]
    assert len(switches) == 1
    switch_argv = switches[0]
    flake_idx = switch_argv.index("--flake")
    switch_flake = switch_argv[flake_idx + 1]
    assert f"path:{scratch_b}#test-host" == switch_flake

    # Verify journal
    journal_files = list(state_root.glob("run-*/journal.json"))
    assert len(journal_files) == 1
    journal = json.loads(journal_files[0].read_text(encoding="utf-8"))
    assert journal["terminal_state"] == "SUCCESS"
    assert journal["selected_flake"] == str(flake_dir)
    assert journal["switch_flake"] == "projected-scratch"


@pytest.mark.parametrize("drift_field,drift_val", [
    ("revision", "0" * 40),
    ("narHash", "sha256-tampered-hash="),
    ("store_path", "/nix/store/tampered-store-path"),
    ("url", "https://tampered.example.invalid/repo.git"),
])
def test_normal_deployment_int_replay_fails_on_real_source_drift(
    tmp_path: Path, monkeypatch, capsys, drift_field, drift_val
):
    """Integration test proving that altering durable source identity fields between plan and replay fails closed."""
    flake_dir = make_disposable_flake(tmp_path / "composition")
    state_root = tmp_path / ".local/state/nix-update"
    state_root.mkdir(mode=0o700, parents=True)
    monkeypatch.setattr("nix_darwin_local.config.operator_home", lambda: tmp_path)
    monkeypatch.setattr("nix_darwin_local.local_deployment.operator_home", lambda: tmp_path)
    monkeypatch.setattr("nix_darwin_local.launcher.operator_home", lambda: tmp_path)
    monkeypatch.delenv("NIX_DARWIN_LOCAL", raising=False)
    monkeypatch.delenv("NIX_DARWIN_ACTIVE_FLAKE", raising=False)
    monkeypatch.setenv("NIX_DARWIN_CONFIG", "test-host")
    monkeypatch.setattr("nix_darwin_local.local_deployment._guard_processes", lambda *a, **kw: None)

    target_path = "/nix/store/built-target-system"
    predecessor_target = "/nix/store/t1"

    profile_snap = {
        "relationship": "aligned",
        "selected": {"generation_number": 1, "target": predecessor_target, "link": "/nix/var/nix/profiles/system-1-link", "generation_path": "/nix/var/nix/profiles/system-1-link"},
        "live": {"generation_number": None, "target": predecessor_target},
        "retained": [{"generation": 1, "target": predecessor_target, "path": "/nix/var/nix/profiles/system-1-link"}],
    }
    monkeypatch.setattr("nix_darwin_local.local_deployment.inspect_profile", lambda *a: profile_snap)
    monkeypatch.setattr("nix_darwin_local.local_deployment.evaluate_allowlist", lambda *a: [])
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir(mode=0o700)
    scratch_projection = SimpleNamespace(root=scratch_root, inventory=(), digest="test-digest")
    monkeypatch.setattr("nix_darwin_local.local_deployment.render_scratch", lambda *a: scratch_projection)
    monkeypatch.setattr("nix_darwin_local.local_deployment.inspect_scratch", lambda *a: scratch_projection)
    monkeypatch.setattr("nix_darwin_local.local_deployment.evaluate_deployment_effects", lambda *a: {"status": "absent"})
    monkeypatch.setattr("nix_darwin_local.local_deployment.closure_inventory", lambda *a: [{"path": target_path}])
    monkeypatch.setattr("nix_darwin_local.local_deployment.current_live_closure", lambda *a: [{"path": predecessor_target}])
    monkeypatch.setattr("nix_darwin_local.local_deployment.require_terminal_confirmation", lambda: pytest.fail("drift must fail before confirmation"))
    monkeypatch.setattr("nix_darwin_local.local_deployment.ensure_sudo_credentials", lambda *a: True)

    commands_executed = []
    git_outputs = {}

    def mock_command(self, label, argv, cwd):
        commands_executed.append((label, list(argv)))
        if argv[0] == "git":
            res = subprocess.run(argv, cwd=cwd, text=True, capture_output=True)
            git_outputs[label] = res.stdout
            return res
        if label == "locked source tree realization":
            archive_data = {"path": "/nix/store/archive-root", "inputs": {"nixpkgs": {"path": "/nix/store/nixpkgs-p"}}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(archive_data))
        if label == "source store path inspection":
            return SimpleNamespace(returncode=0, stdout=json.dumps({"/nix/store/nixpkgs-p": {"narHash": "sha256-hash1="}}))
        if label in {"exact Darwin target build", "local target revalidation"}:
            return SimpleNamespace(returncode=0, stdout=f"{target_path}\n")
        return SimpleNamespace(returncode=0, stdout="")

    def mock_complete_stdout(self, label):
        if label in git_outputs:
            return git_outputs[label]
        return {
            "locked source tree realization": json.dumps({"path": "/nix/store/archive-root", "inputs": {"nixpkgs": {"path": "/nix/store/nixpkgs-p"}}}),
            "source store path inspection": json.dumps({"/nix/store/nixpkgs-p": {"narHash": "sha256-hash1="}}),
            "closure comparison": "pkg: 1.0 -> 2.0\n",
        }.get(label, "")

    monkeypatch.setattr("nix_darwin_local.deployment_workflow.Workflow.command", mock_command)
    monkeypatch.setattr("nix_darwin_local.deployment_workflow.Workflow.complete_stdout", mock_complete_stdout)

    config_path = tmp_path / ".local/nix-darwin/config/nix-darwin.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(f"[flake]\nactive = \"{flake_dir}\"\n")

    plan_dir = tmp_path / "plan-export"
    rc_plan = launcher.main(CORE_ROOT, ["deploy", "--plan-only", "--output", str(plan_dir)])
    assert rc_plan == 0

    # Modify the durable source identity field in the saved plan
    plan_file = plan_dir / "plan.json"
    plan_data = json.loads(plan_file.read_text(encoding="utf-8"))
    plan_data["sources"]["sources"][0][drift_field] = drift_val
    plan_file.write_text(json.dumps(plan_data), encoding="utf-8")

    capsys.readouterr()
    rc_replay = launcher.main(CORE_ROOT, ["deploy", "--plan", str(plan_dir)])
    assert rc_replay == 78
    captured = capsys.readouterr()
    assert "source authority changed since reviewed local plan was created" in captured.err
    assert all(label != "single Darwin switch" for label, _ in commands_executed)


def test_normal_deployment_int_replay_fails_on_canonical_git_authority_drift(
    tmp_path: Path, monkeypatch, capsys
):
    """Integration test proving that advancing canonical repository git commit between plan and replay fails closed."""
    flake_dir = make_disposable_flake(tmp_path / "composition")
    state_root = tmp_path / ".local/state/nix-update"
    state_root.mkdir(mode=0o700, parents=True)
    monkeypatch.setattr("nix_darwin_local.config.operator_home", lambda: tmp_path)
    monkeypatch.setattr("nix_darwin_local.local_deployment.operator_home", lambda: tmp_path)
    monkeypatch.setattr("nix_darwin_local.launcher.operator_home", lambda: tmp_path)
    monkeypatch.delenv("NIX_DARWIN_LOCAL", raising=False)
    monkeypatch.delenv("NIX_DARWIN_ACTIVE_FLAKE", raising=False)
    monkeypatch.setenv("NIX_DARWIN_CONFIG", "test-host")
    monkeypatch.setattr("nix_darwin_local.local_deployment._guard_processes", lambda *a, **kw: None)

    target_path = "/nix/store/built-target-system"
    predecessor_target = "/nix/store/t1"

    profile_snap = {
        "relationship": "aligned",
        "selected": {"generation_number": 1, "target": predecessor_target, "link": "/nix/var/nix/profiles/system-1-link", "generation_path": "/nix/var/nix/profiles/system-1-link"},
        "live": {"generation_number": None, "target": predecessor_target},
        "retained": [{"generation": 1, "target": predecessor_target, "path": "/nix/var/nix/profiles/system-1-link"}],
    }
    monkeypatch.setattr("nix_darwin_local.local_deployment.inspect_profile", lambda *a: profile_snap)
    monkeypatch.setattr("nix_darwin_local.local_deployment.evaluate_allowlist", lambda *a: [])
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir(mode=0o700)
    scratch_projection = SimpleNamespace(root=scratch_root, inventory=(), digest="test-digest")
    monkeypatch.setattr("nix_darwin_local.local_deployment.render_scratch", lambda *a: scratch_projection)
    monkeypatch.setattr("nix_darwin_local.local_deployment.inspect_scratch", lambda *a: scratch_projection)
    monkeypatch.setattr("nix_darwin_local.local_deployment.evaluate_deployment_effects", lambda *a: {"status": "absent"})
    monkeypatch.setattr("nix_darwin_local.local_deployment.closure_inventory", lambda *a: [{"path": target_path}])
    monkeypatch.setattr("nix_darwin_local.local_deployment.current_live_closure", lambda *a: [{"path": predecessor_target}])
    monkeypatch.setattr("nix_darwin_local.local_deployment.require_terminal_confirmation", lambda: pytest.fail("drift must fail before confirmation"))
    monkeypatch.setattr("nix_darwin_local.local_deployment.ensure_sudo_credentials", lambda *a: True)

    commands_executed = []
    git_outputs = {}

    def mock_command(self, label, argv, cwd):
        commands_executed.append((label, list(argv)))
        if argv[0] == "git":
            res = subprocess.run(argv, cwd=cwd, text=True, capture_output=True)
            git_outputs[label] = res.stdout
            return res
        if label == "locked source tree realization":
            archive_data = {"path": "/nix/store/archive-root", "inputs": {"nixpkgs": {"path": "/nix/store/nixpkgs-p"}}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(archive_data))
        if label == "source store path inspection":
            return SimpleNamespace(returncode=0, stdout=json.dumps({"/nix/store/nixpkgs-p": {"narHash": "sha256-hash1="}}))
        if label in {"exact Darwin target build", "local target revalidation"}:
            return SimpleNamespace(returncode=0, stdout=f"{target_path}\n")
        return SimpleNamespace(returncode=0, stdout="")

    def mock_complete_stdout(self, label):
        if label in git_outputs:
            return git_outputs[label]
        return {
            "locked source tree realization": json.dumps({"path": "/nix/store/archive-root", "inputs": {"nixpkgs": {"path": "/nix/store/nixpkgs-p"}}}),
            "source store path inspection": json.dumps({"/nix/store/nixpkgs-p": {"narHash": "sha256-hash1="}}),
            "closure comparison": "pkg: 1.0 -> 2.0\n",
        }.get(label, "")

    monkeypatch.setattr("nix_darwin_local.deployment_workflow.Workflow.command", mock_command)
    monkeypatch.setattr("nix_darwin_local.deployment_workflow.Workflow.complete_stdout", mock_complete_stdout)

    config_path = tmp_path / ".local/nix-darwin/config/nix-darwin.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(f"[flake]\nactive = \"{flake_dir}\"\n")

    plan_dir = tmp_path / "plan-export"
    rc_plan = launcher.main(CORE_ROOT, ["deploy", "--plan-only", "--output", str(plan_dir)])
    assert rc_plan == 0

    # Advance git commit in canonical flake
    (flake_dir / "extra.txt").write_text("drift\n")
    subprocess.run(["git", "add", "extra.txt"], cwd=flake_dir, check=True)
    subprocess.run(["git", "commit", "-m", "advance"], cwd=flake_dir, check=True, capture_output=True)

    capsys.readouterr()
    rc_replay = launcher.main(CORE_ROOT, ["deploy", "--plan", str(plan_dir)])
    assert rc_replay == 78
    captured = capsys.readouterr()
    assert (
        "source commit map changed since reviewed local plan was created" in captured.err
        or "Git authority changed since reviewed local plan was created" in captured.err
    )
    assert all(label != "single Darwin switch" for label, _ in commands_executed)


@pytest.mark.parametrize("mode", ["plan", "build"])
def test_snapshot_nested_explicit_child_real_projection(tmp_path, monkeypatch, mode, capsys):
    """Real local Git/Nix pins; stub only Darwin qualification/build and host reads."""
    from nix_darwin_local import config, deployment_workflow as dw, local_deployment as ld
    root = tmp_path.resolve()
    # Enable the parser's existing local-fixture option only for these disposable URLs.
    from nix_darwin_local import local_source_binding
    match_repository = local_source_binding.repository_urls_match
    monkeypatch.setattr(local_source_binding, "repository_urls_match",
                        lambda a, b: match_repository(a, b, allow_file=True))
    parse_sources = config.parse_sources
    monkeypatch.setattr(config, "parse_sources", lambda *a, **kw: parse_sources(*a, **kw, allow_file=True))
    def run(path, *args):
        return subprocess.run(args, cwd=path, check=True, capture_output=True, text=True).stdout.strip()
    def repo(name, flake):
        path = root / name
        path.mkdir()
        run(path, "git", "init", "-b", "main")
        run(path, "git", "config", "user.name", "Fixture")
        run(path, "git", "config", "user.email", "fixture@example.invalid")
        (path / "flake.nix").write_text(flake)
        run(path, "git", "add", ".")
        run(path, "nix", "flake", "lock", "--offline")
        run(path, "git", "add", ".")
        run(path, "git", "commit", "-m", "fixture")
        return path, run(path, "git", "rev-parse", "HEAD")
    child, old = repo("adapter", '{ outputs = { self }: { value = 10; }; }')
    parent, parent_rev = repo("composition", f'{{ inputs.adapter.url = "git+{child.as_uri()}"; outputs = {{ self, adapter }}: {{ value = adapter.value; }}; }}')
    selected, selected_rev = repo("selected", f'{{ inputs.composition.url = "git+{parent.as_uri()}"; outputs = {{ self, composition }}: {{ value = composition.value; }}; }}')
    (child / "flake.nix").write_text('{ outputs = { self }: { value = 11; }; }')
    run(child, "git", "add", ".")
    run(child, "git", "commit", "-m", "new adapter")
    new = run(child, "git", "rev-parse", "HEAD")
    original_lock = (selected / "flake.lock").read_bytes()
    assert json.loads(original_lock)["nodes"]["adapter"]["locked"]["rev"] == old
    control = root / ".local/nix-darwin/config"
    control.mkdir(parents=True)
    entries = [("adapter", child, new), ("composition", parent, parent_rev), ("selected", selected, selected_rev)]
    toml = f'[flake]\nactive = "{selected}"\n'
    for key, path, rev in entries:
        toml += f'[sources.{key}]\ncheckout = "{path}"\nrepository = "{path.as_uri()}"\nrelease_ref = "refs/heads/main"\n'
    toml += '[snapshot.sources]\n' + ''.join(f'{key} = "{rev}"\n' for key, _, rev in entries)
    (control / "nix-darwin.toml").write_text(toml)
    for module in (config, dw, ld, launcher):
        if hasattr(module, "operator_home"):
            monkeypatch.setattr(module, "operator_home", lambda: root)
    monkeypatch.delenv("NIX_DARWIN_LOCAL", raising=False)
    monkeypatch.delenv("NIX_DARWIN_ACTIVE_FLAKE", raising=False)
    monkeypatch.setattr(ld, "_guard_processes", lambda *a, **kw: None)
    profile = {"relationship": "aligned", "selected": {"generation_number": 1, "target": "/nix/store/old", "generation_path": "/nix/var/nix/profiles/system-1-link"},
        "live": {"target": "/nix/store/old"}, "retained": [{"generation": 1, "target": "/nix/store/old", "path": "/nix/var/nix/profiles/system-1-link"}]}
    monkeypatch.setattr(ld, "inspect_profile", lambda *a: profile)
    for module in (ld, dw):
        monkeypatch.setattr(module, "evaluate_allowlist", lambda *a: {})
    monkeypatch.setattr(ld, "evaluate_deployment_effects", lambda *a: {"status": "absent"})
    monkeypatch.setattr(ld, "closure_inventory", lambda *a: [])
    monkeypatch.setattr(ld, "current_live_closure", lambda *a: [])
    monkeypatch.setattr(ld, "require_terminal_confirmation", lambda: pytest.fail("must not activate"))
    original_command = dw.Workflow.command
    observed = []
    def command(self, label, argv, cwd):
        if label in {"selected-flake report qualification", "exact projected flake check", "exact Darwin target build", "locked Darwin build", "closure comparison"}:
            doc = json.loads((Path(cwd) / "flake.lock").read_bytes())
            assert doc["nodes"]["adapter"]["locked"]["rev"] == new
            # Evaluate the projected value with installed Nix, prohibiting relocking.
            if label == "selected-flake report qualification":
                assert run(cwd, "nix", "eval", "--json", "--offline", "--no-update-lock-file", "--no-write-lock-file", "path:.#value") == "11"
                observed.append(doc)
            return SimpleNamespace(returncode=0, stdout="/nix/store/fixture-system\n" if label == "exact Darwin target build" else "")
        return original_command(self, label, argv, cwd)
    monkeypatch.setattr(dw.Workflow, "command", command)
    original_stdout = dw.Workflow.complete_stdout
    monkeypatch.setattr(dw.Workflow, "complete_stdout",
                        lambda self, label: "" if label == "closure comparison" else original_stdout(self, label))
    if mode == "plan":
        plan = root / "plan"
        assert launcher.main(CORE_ROOT, ["deploy", "--plan-only", "--output", str(plan)]) == 0
        doc = load_local_plan(plan).document
        assert doc["commit_map"]["adapter"] == new
        assert doc["source_snapshot"]["lock_backed_revisions"]["adapter"] == new
        assert doc["lock"]["nodes"]["adapter"]["locked"]["rev"] == new
        records = {item["source"]["key"]: item["source"]["revision"] for item in doc["local_sources"]}
        assert records["adapter"] == new
    else:
        cfg = config.load_control_config(control / "nix-darwin.toml")
        wf = dw.Workflow(CORE_ROOT, sources=cfg.sources, display_command_output=False)
        effective = config.resolve_effective_source_revisions(cfg, {})
        assert dw.build_only(wf, selected, "fixture", control, [], source_revisions=effective) == 0
    assert observed
    assert (selected / "flake.lock").read_bytes() == original_lock
    assert run(child, "git", "status", "--porcelain") == ""


def test_plan_only_launcher_missing_output_stops_before_observation(tmp_path, monkeypatch, capsys):
    from nix_darwin_local import local_deployment as ld
    flake = make_disposable_flake(tmp_path / "composition")
    for module in (launcher, ld):
        monkeypatch.setattr(module, "operator_home", lambda: tmp_path)
    monkeypatch.setattr("nix_darwin_local.config.operator_home", lambda: tmp_path)
    monkeypatch.delenv("NIX_DARWIN_LOCAL", raising=False)
    monkeypatch.delenv("NIX_DARWIN_ACTIVE_FLAKE", raising=False)
    config_path = tmp_path / ".local/nix-darwin/config/nix-darwin.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(f'[flake]\nactive = "{flake}"\n')
    def forbidden(*a, **kw):
        pytest.fail("source or host observation must not start")
    monkeypatch.setattr(ld, "ensure_private_root", forbidden)
    monkeypatch.setattr(ld, "local_git_identity", forbidden)
    monkeypatch.setattr(ld, "inspect_profile", forbidden)
    assert launcher.main(CORE_ROOT, ["deploy", "--plan-only"]) != 0
    assert "--plan-only requires --output <directory>" in capsys.readouterr().err
