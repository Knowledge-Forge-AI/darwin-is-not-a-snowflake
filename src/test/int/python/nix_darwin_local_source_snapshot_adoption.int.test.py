# nix-darwin-test-source-target: libexec/nix_darwin_local/source_snapshot_adoption.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/source_snapshot_status.py
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
from typing import Any

import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local import (
    deployment_workflow,
    launcher,
    source_snapshot_adoption,
    source_snapshot_status,
)
from nix_darwin_local.config import load_control_config
from nix_darwin_local.promotion_campaign_state import new_campaign, persist_campaign
from nix_darwin_local.source_snapshot_adoption import CONFIRMATION_TEMPLATE

SOURCE_KEYS = (
    "core",
    "composition-nd",
    "composition-private-nd",
    "agentic-praxis-grimoire_dinas",
    "entertainment-nd",
    "developer-ux_dinas",
    "agent_dinas",
    "agent-security-nd",
    "joint-agentic-command-aegis-nd",
)

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "DINAS Integration Test",
    "GIT_AUTHOR_EMAIL": "integration@example.invalid",
    "GIT_COMMITTER_NAME": "DINAS Integration Test",
    "GIT_COMMITTER_EMAIL": "integration@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def _init_git_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", "-q"], cwd=path, check=True, capture_output=True, env=GIT_ENV)
    (path / "README.md").write_text(f"# {path.name}\n")
    (path / "flake.nix").write_text('{ description = "fixture"; }\n')
    subprocess.run(["git", "add", "."], cwd=path, check=True, env=GIT_ENV)
    subprocess.run(["git", "commit", "-m", "initial", "-q"], cwd=path, check=True, capture_output=True, env=GIT_ENV)
    res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True, env=GIT_ENV)
    return res.stdout.strip()


def _git_commit(path: Path, msg: str = "update") -> str:
    readme = path / "README.md"
    readme.write_text(readme.read_text() + f"{msg}\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, env=GIT_ENV)
    subprocess.run(["git", "commit", "-m", msg, "-q"], cwd=path, check=True, capture_output=True, env=GIT_ENV)
    res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True, env=GIT_ENV)
    return res.stdout.strip()


@pytest.fixture(scope="module")
def int_template_env(tmp_path_factory) -> tuple[Path, dict[str, str]]:
    template_dir = tmp_path_factory.mktemp("int_sources_template")
    pins = {}
    for key in SOURCE_KEYS:
        repo_dir = template_dir / key
        rev = _init_git_repo(repo_dir)
        pins[key] = rev
    return template_dir, pins


def _setup_integration_env(tmp_path: Path, int_template_env: tuple[Path, dict[str, str]]):
    template_dir, template_pins = int_template_env
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    state_root = home / ".local/state/nix-update"
    state_root.mkdir(mode=0o700, parents=True)
    control_dir = home / ".local/nix-darwin/config"
    control_dir.mkdir(mode=0o700, parents=True)

    sources_dir = home / "sources"
    shutil.copytree(template_dir, sources_dir)

    initial_pins: dict[str, str] = dict(template_pins)
    checkouts: dict[str, Path] = {key: sources_dir / key for key in SOURCE_KEYS}

    config_path = control_dir / "nix-darwin.toml"
    lines = [
        "[flake]",
        f'active = "{checkouts["composition-private-nd"]}"',
        "",
    ]
    for key in SOURCE_KEYS:
        lines.extend([
            f"[sources.{key}]",
            f'checkout = "{checkouts[key]}"',
            f'repository = "git+ssh://git@github.com/Knowledge-Forge-AI/{key}.git"',
            'release_ref = "refs/heads/main"',
            "",
            f"[sources.{key}.publication]",
            'remote = "origin"',
            'ref = "refs/heads/main"',
            "",
        ])
    lines.append("[snapshot.sources]")
    for key in sorted(SOURCE_KEYS):
        lines.append(f'"{key}" = "{initial_pins[key]}"')
    lines.append("")

    config_path.write_text("\n".join(lines), encoding="utf-8")
    config_path.chmod(0o600)

    env = {
        **os.environ,
        "HOME": str(home),
        "NIX_DARWIN_LOCAL": str(home / ".local/nix-darwin"),
    }
    return home, state_root, config_path, checkouts, initial_pins, env


def _run_cli(argv: list[str], env: dict[str, str], monkeypatch, capsys=None) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "argv", ["nix-update", *argv])
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    state_root = Path(env["HOME"]) / ".local/state/nix-update"
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)
    code = launcher.main(CORE_ROOT, argv=argv)
    out, err = capsys.readouterr() if capsys else ("", "")
    return code, out, err


def test_end_to_end_campaign_snapshot_adoption_cli(tmp_path: Path, int_template_env, monkeypatch, capsys):
    home, state_root, config_path, checkouts, initial_pins, env = _setup_integration_env(tmp_path, int_template_env)

    # 1. Initial status: all sources match pin
    code, out, err = _run_cli(["snapshot", "status"], env, monkeypatch, capsys)
    assert code == 0, f"code={code}\nout={out}\nerr={err}"
    assert "all snapshot pins match checkout HEADs" in out

    # 2. Promote developer-ux_dinas domain
    new_dev = _git_commit(checkouts["developer-ux_dinas"], "promote dev")
    new_comp = _git_commit(checkouts["composition-nd"], "promote comp")
    new_priv = _git_commit(checkouts["composition-private-nd"], "promote priv")

    stages = [
        {
            "name": "developer-ux_dinas",
            "kind": "domain",
            "repository": os.fspath(checkouts["developer-ux_dinas"]),
            "input": "developer-ux_dinas",
            "revision": new_dev,
            "status": "LOCAL_COMMITTED",
            "local_commit": new_dev,
            "published_commit": None,
            "publication_state": None,
            "activation_state": "not_performed",
            "impact": None,
            "failure": None,
            "attempts": [],
            "current_plan": None,
        },
        {
            "name": "composition-nd",
            "kind": "composition",
            "repository": os.fspath(checkouts["composition-nd"]),
            "input": "developer-ux_dinas",
            "revision": new_dev,
            "status": "LOCAL_COMMITTED",
            "local_commit": new_comp,
            "published_commit": None,
            "publication_state": None,
            "activation_state": "not_performed",
            "impact": None,
            "failure": None,
            "attempts": [],
            "current_plan": None,
        },
        {
            "name": "composition-private-nd",
            "kind": "composition",
            "repository": os.fspath(checkouts["composition-private-nd"]),
            "input": "composition-nd",
            "revision": new_comp,
            "status": "LOCAL_COMMITTED",
            "local_commit": new_priv,
            "published_commit": None,
            "publication_state": None,
            "activation_state": "not_performed",
            "impact": None,
            "failure": None,
            "attempts": [],
            "current_plan": None,
        },
    ]

    camp_dir = state_root / "campaigns/dev-campaign"
    (state_root / "campaigns").mkdir(mode=0o700, parents=True, exist_ok=True)
    (state_root / "campaigns").chmod(0o700)
    camp_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    camp_dir.chmod(0o700)
    doc = new_campaign(
        checkouts["composition-private-nd"],
        "developer-ux_dinas",
        new_dev,
        [{"name": s["name"], "kind": s["kind"], "repository": Path(s["repository"]), "input": s["input"], "revision": s["revision"]} for s in stages],
    )
    doc["state"] = "LOCAL_COMPLETE_AWAITING_ACTIVATION"
    doc["last_outcome"] = "LOCAL_COMPLETE_AWAITING_ACTIVATION"
    doc["stages"] = stages
    doc["current_stage"] = len(stages)
    persist_campaign(camp_dir, doc)

    # 3. Snapshot status shows drift and recommends prepare --campaign
    code, out, _ = _run_cli(["snapshot", "status"], env, monkeypatch, capsys)
    assert code == 0
    assert "snapshot pins mismatch checkout HEADs" in out
    assert "nix-update snapshot prepare" in out

    # 4. Prepare snapshot adoption via CLI
    pre_bytes = config_path.read_bytes()
    code, out, _ = _run_cli(["snapshot", "prepare", "--campaign", str(camp_dir)], env, monkeypatch, capsys)
    assert code == 0
    assert "READY_FOR_REVIEW" in out
    # Verify non-mutating
    assert config_path.read_bytes() == pre_bytes

    # Find created plan
    plan_dirs = list((state_root / "snapshot-adoption-plans").glob("plan-*"))
    assert len(plan_dirs) == 1
    plan_dir = plan_dirs[0]
    command, = [line for line in (plan_dir / "plan.md").read_text().splitlines()
                if line.startswith("nix-update snapshot apply ")]
    assert shlex.split(command) == ["nix-update", "snapshot", "apply", str(plan_dir)]

    # 5. Snapshot status with --plan
    code, out, _ = _run_cli(["snapshot", "status", "--plan", str(plan_dir)], env, monkeypatch, capsys)
    assert code == 0
    assert "inspected plan:" in out
    assert "plan=" in out

    # 6. Apply snapshot adoption via CLI with confirmation
    plan_doc = json.loads((plan_dir / "plan.json").read_text())
    expected_confirm = CONFIRMATION_TEMPLATE.format(plan_digest=plan_doc["plan_digest"])
    monkeypatch.setattr(source_snapshot_adoption, "_snapshot_tty_confirmation", lambda _: expected_confirm)

    code, out, _ = _run_cli(["snapshot", "apply", str(plan_dir)], env, monkeypatch, capsys)
    assert code == 0

    # Verify config was atomically updated
    assert config_path.read_bytes() != pre_bytes
    loaded = load_control_config(config_path, operator_home=home)
    assert loaded.snapshot.by_key["developer-ux_dinas"] == new_dev
    assert loaded.snapshot.by_key["composition-nd"] == new_comp
    assert loaded.snapshot.by_key["composition-private-nd"] == new_priv

    # 7. Post-adoption status: all sources match pin!
    code, out, _ = _run_cli(["snapshot", "status"], env, monkeypatch, capsys)
    assert code == 0
    assert "all snapshot pins match checkout HEADs" in out

    # 8. Verify backup and consumed records
    backups = list((state_root / "snapshot-backups").glob("*.toml"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == pre_bytes

    consumed = list((state_root / "snapshot-consumed").glob("*.json"))
    assert len(consumed) == 1

    # 9. Verify ordinary deployment resolves adopted snapshot pins with zero CLI overrides
    from nix_darwin_local.config import resolve_effective_source_revisions

    selection = launcher.resolve_selection(checkouts["composition-private-nd"], None, config_path, env, home)
    assert selection.config.snapshot is not None
    assert selection.config.snapshot.by_key["developer-ux_dinas"] == new_dev
    assert selection.config.snapshot.by_key["composition-nd"] == new_comp
    assert selection.config.snapshot.by_key["composition-private-nd"] == new_priv

    effective = resolve_effective_source_revisions(selection.config, {})
    assert effective.active is True
    assert effective.cli_override_count == 0
    assert effective.get("developer-ux_dinas") == new_dev
    assert effective.get("composition-nd") == new_comp
    assert effective.get("composition-private-nd") == new_priv
    for key, prov in effective.provenance:
        assert prov == "snapshot"

    # Confirm no composition-lock mutation or convergence occurred during adoption
    assert not (checkouts["composition-nd"] / "flake.lock").exists()


def test_end_to_end_explicit_pin_snapshot_adoption_cli(tmp_path: Path, int_template_env, monkeypatch, capsys):
    home, state_root, config_path, checkouts, initial_pins, env = _setup_integration_env(tmp_path, int_template_env)

    new_agent = _git_commit(checkouts["agent_dinas"], "promote agent")
    monkeypatch.chdir(state_root)
    output_dir = Path("reviewer's plan $HOME; `echo no`")
    pre_bytes = config_path.read_bytes()

    # Prepare with --pin
    code, out, _ = _run_cli(
        ["snapshot", "prepare", "--pin", f"agent_dinas={new_agent}", "--output", str(output_dir)],
        env,
        monkeypatch,
        capsys,
    )
    assert code == 0
    assert "READY_FOR_REVIEW" in out

    plan_dir = output_dir.resolve()
    plan_doc = json.loads((plan_dir / "plan.json").read_text())
    markdown = (plan_dir / "plan.md").read_text()
    command, = [line for line in markdown.splitlines() if line.startswith("nix-update snapshot apply ")]
    assert shlex.split(command) == ["nix-update", "snapshot", "apply", str(plan_dir)]
    assert f"- **Config Target**: `{plan_doc['config_path']}`" in markdown
    assert config_path.read_bytes() == pre_bytes

    # Apply
    expected_confirm = CONFIRMATION_TEMPLATE.format(plan_digest=plan_doc["plan_digest"])
    monkeypatch.setattr(source_snapshot_adoption, "_snapshot_tty_confirmation", lambda _: expected_confirm)

    code, out, _ = _run_cli(["snapshot", "apply", str(plan_dir)], env, monkeypatch, capsys)
    assert code == 0

    loaded = load_control_config(config_path, operator_home=home)
    assert loaded.snapshot.by_key["agent_dinas"] == new_agent
    for k in SOURCE_KEYS:
        if k != "agent_dinas":
            assert loaded.snapshot.by_key[k] == initial_pins[k]


def test_cli_plan_replay_and_drift_safeguards(tmp_path: Path, int_template_env, monkeypatch, capsys):
    home, state_root, config_path, checkouts, initial_pins, env = _setup_integration_env(tmp_path, int_template_env)

    new_agent = _git_commit(checkouts["agent_dinas"], "agent pin")

    code, out, _ = _run_cli(["snapshot", "prepare", "--pin", f"agent_dinas={new_agent}"], env, monkeypatch, capsys)
    assert code == 0

    plan_dirs = list((state_root / "snapshot-adoption-plans").glob("plan-*"))
    plan_dir = plan_dirs[0]
    plan_doc = json.loads((plan_dir / "plan.json").read_text())
    expected_confirm = CONFIRMATION_TEMPLATE.format(plan_digest=plan_doc["plan_digest"])
    monkeypatch.setattr(source_snapshot_adoption, "_snapshot_tty_confirmation", lambda _: expected_confirm)

    # First apply
    code, out, _ = _run_cli(["snapshot", "apply", str(plan_dir)], env, monkeypatch, capsys)
    assert code == 0

    # Truthful replay
    code, out, _ = _run_cli(["snapshot", "apply", str(plan_dir)], env, monkeypatch, capsys)
    assert code == 0
    assert "already applied" in out

    # Mutate config -> fails closed
    config_path.write_text(config_path.read_text() + "\n# drift\n")
    code, out, err = _run_cli(["snapshot", "apply", str(plan_dir)], env, monkeypatch, capsys)
    assert code != 0
    assert "already consumed its durable mutation authority" in err
