# nix-darwin-test-source-target: libexec/nix_darwin_local/source_snapshot_adoption.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/source_snapshot_status.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/config.py
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
from typing import Any, Mapping

import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local import (
    config,
    deployment_workflow,
    local_source,
    promotion_campaign_state,
    source_snapshot_adoption,
    source_snapshot_status,
)
from nix_darwin_local.config import load_control_config
from nix_darwin_local.deployment_paths import atomic_private_write, require_private_directory
from nix_darwin_local.deployment_plan import canonical_bytes, sha256_bytes
from nix_darwin_local.deployment_workflow import Workflow
from nix_darwin_local.paths import ConfigError
from nix_darwin_local.promotion_campaign import TerminalUnavailableError
from nix_darwin_local.promotion_campaign_state import persist_campaign, seal
from nix_darwin_local.source_snapshot_adoption import (
    CONFIRMATION_TEMPLATE,
    SNAPSHOT_ADOPTION_PLAN_SCHEMA,
    apply_snapshot_adoption,
    extract_campaign_pins,
    prepare_snapshot_adoption,
    render_candidate_config,
)

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
    "GIT_AUTHOR_NAME": "Test User",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test User",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def _init_git_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", "-q"], cwd=path, check=True, capture_output=True, env=GIT_ENV)
    (path / "README.md").write_text(f"# {path.name}\n")
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
def template_env(tmp_path_factory) -> tuple[Path, dict[str, str]]:
    template_dir = tmp_path_factory.mktemp("sources_template")
    pins = {}
    for key in SOURCE_KEYS:
        repo_dir = template_dir / key
        rev = _init_git_repo(repo_dir)
        pins[key] = rev
    return template_dir, pins


def _create_environment(tmp_path: Path, template_env: tuple[Path, dict[str, str]]):
    template_dir, template_pins = template_env
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

    # Write nix-darwin.toml
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

    loaded_config = load_control_config(config_path, operator_home=home)
    workflow = Workflow(CORE_ROOT, sources=loaded_config.sources)
    return home, state_root, config_path, checkouts, initial_pins, workflow


def _make_campaign(
    campaign_dir: Path,
    selected: Path,
    input_name: str,
    revision: str,
    stages: list[dict[str, Any]],
    state: str = "LOCAL_COMPLETE_AWAITING_ACTIVATION",
    dry_run: bool = False,
) -> dict[str, Any]:
    from nix_darwin_local.promotion_campaign_state import new_campaign
    campaign_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stage_templates = []
    for s in stages:
        stage_templates.append({
            "name": s["name"],
            "kind": s.get("kind", "composition"),
            "repository": Path(s["repository"]),
            "input": s.get("input", input_name),
            "revision": s.get("revision", revision),
        })
    if not stage_templates:
        stage_templates.append({
            "name": "core",
            "kind": "core",
            "repository": selected,
            "input": input_name,
            "revision": revision,
        })
    doc = new_campaign(selected, input_name, revision, stage_templates, dry_run=dry_run)
    doc["state"] = state
    doc["last_outcome"] = state
    if stages:
        doc["stages"] = stages
        doc["current_stage"] = len(stages)
    else:
        doc["stages"][0]["status"] = "LOCAL_COMMITTED"
        doc["stages"][0]["local_commit"] = revision
        doc["current_stage"] = 1
    return persist_campaign(campaign_dir, doc)


def test_domain_campaign_derives_exact_candidate_snapshot(tmp_path: Path, template_env, monkeypatch):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    # Domain promotion: developer-ux_dinas commit, plus composition and composition-private commits
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
    _make_campaign(camp_dir, checkouts["composition-private-nd"], "developer-ux_dinas", new_dev, stages)

    plan_dir = prepare_snapshot_adoption(
        workflow,
        config_path,
        campaign_dir=camp_dir,
        home=home,
    )

    plan = json.loads((plan_dir / "plan.json").read_text())
    assert plan["status"] == "READY_FOR_REVIEW"
    assert plan["candidate_pins"]["developer-ux_dinas"] == new_dev
    assert plan["candidate_pins"]["composition-nd"] == new_comp
    assert plan["candidate_pins"]["composition-private-nd"] == new_priv
    # Unchanged pins preserved exactly
    for key in SOURCE_KEYS:
        if key not in {"developer-ux_dinas", "composition-nd", "composition-private-nd"}:
            assert plan["candidate_pins"][key] == initial_pins[key]


def test_core_campaign_derives_exact_candidate_snapshot(tmp_path: Path, template_env, monkeypatch):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    new_core = _git_commit(checkouts["core"], "promote core")
    new_comp = _git_commit(checkouts["composition-nd"], "promote comp")
    new_priv = _git_commit(checkouts["composition-private-nd"], "promote priv")

    stages = [
        {
            "name": "composition-nd",
            "kind": "composition",
            "repository": os.fspath(checkouts["composition-nd"]),
            "input": "core",
            "revision": new_core,
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

    camp_dir = state_root / "campaigns/core-campaign"
    _make_campaign(camp_dir, checkouts["composition-private-nd"], "core", new_core, stages)

    plan_dir = prepare_snapshot_adoption(
        workflow,
        config_path,
        campaign_dir=camp_dir,
        home=home,
    )

    plan = json.loads((plan_dir / "plan.json").read_text())
    assert plan["candidate_pins"]["core"] == new_core
    assert plan["candidate_pins"]["composition-nd"] == new_comp
    assert plan["candidate_pins"]["composition-private-nd"] == new_priv
    assert plan["candidate_pins"]["agent_dinas"] == initial_pins["agent_dinas"]


def test_unchanged_pins_preserved_exactly(tmp_path: Path, template_env, monkeypatch):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    # Manual pin for single source
    new_agent = _git_commit(checkouts["agent_dinas"], "agent pin")
    plan_dir = prepare_snapshot_adoption(
        workflow,
        config_path,
        explicit_pins=[f"agent_dinas={new_agent}"],
        home=home,
    )
    plan = json.loads((plan_dir / "plan.json").read_text())
    assert plan["candidate_pins"]["agent_dinas"] == new_agent
    for k in SOURCE_KEYS:
        if k != "agent_dinas":
            assert plan["candidate_pins"][k] == initial_pins[k]


def test_source_key_and_repository_mismatch_rejection(tmp_path: Path, template_env, monkeypatch):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    # Unknown input
    camp_dir = state_root / "campaigns/bad-input"
    _make_campaign(
        camp_dir, checkouts["composition-private-nd"], "unknown-input-name", "a" * 40, []
    )
    with pytest.raises(ConfigError, match="does not correspond to any configured source key"):
        prepare_snapshot_adoption(workflow, config_path, campaign_dir=camp_dir, home=home)

    # Unknown stage repository
    other_repo = tmp_path / "other-repo"
    _init_git_repo(other_repo)
    bad_stages = [
        {
            "name": "other",
            "kind": "composition",
            "repository": os.fspath(other_repo),
            "input": "core",
            "revision": "a" * 40,
            "status": "LOCAL_COMMITTED",
            "local_commit": "a" * 40,
            "published_commit": None,
            "publication_state": None,
            "activation_state": "not_performed",
            "impact": None,
            "failure": None,
            "attempts": [],
            "current_plan": None,
        }
    ]
    camp_dir2 = state_root / "campaigns/bad-stage"
    _make_campaign(camp_dir2, checkouts["composition-private-nd"], "core", "a" * 40, bad_stages)
    with pytest.raises(ConfigError, match="does not match any configured source checkout"):
        prepare_snapshot_adoption(workflow, config_path, campaign_dir=camp_dir2, home=home)


def test_incomplete_or_blocked_campaign_rejection(tmp_path: Path, template_env, monkeypatch):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    # Blocked campaign
    camp_dir = state_root / "campaigns/blocked"
    _make_campaign(
        camp_dir, checkouts["composition-private-nd"], "core", "a" * 40, [], state="BLOCKED"
    )
    with pytest.raises(ConfigError, match="is blocked; cannot adopt pins"):
        prepare_snapshot_adoption(workflow, config_path, campaign_dir=camp_dir, home=home)

    # Dry-run campaign
    camp_dir_dry = state_root / "campaigns/dry"
    _make_campaign(
        camp_dir_dry, checkouts["composition-private-nd"], "core", "a" * 40, [], dry_run=True
    )
    with pytest.raises(ConfigError, match="dry-run campaigns do not provide adoption authority"):
        prepare_snapshot_adoption(workflow, config_path, campaign_dir=camp_dir_dry, home=home)

    # Non-terminal campaign (AWAITING_APPLY)
    camp_dir_apply = state_root / "campaigns/awaiting-apply"
    _make_campaign(
        camp_dir_apply, checkouts["composition-private-nd"], "core", "a" * 40, [], state="AWAITING_APPLY"
    )
    with pytest.raises(ConfigError, match="non-terminal state"):
        prepare_snapshot_adoption(workflow, config_path, campaign_dir=camp_dir_apply, home=home)


def test_tampered_campaign_journal_rejected(tmp_path: Path, template_env, monkeypatch):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    camp_dir = state_root / "campaigns/tampered"
    doc = _make_campaign(camp_dir, checkouts["composition-private-nd"], "core", "a" * 40, [])

    # Tamper with journal
    journal_path = camp_dir / "campaign.json"
    content = json.loads(journal_path.read_text())
    content["input"] = "developer-ux_dinas"
    atomic_private_write(journal_path, canonical_bytes(content))

    with pytest.raises(ConfigError, match="campaign digest does not match canonical content"):
        prepare_snapshot_adoption(workflow, config_path, campaign_dir=camp_dir, home=home)


def test_legacy_campaign_journal_rejected(tmp_path: Path, template_env, monkeypatch):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    camp_dir = state_root / "campaigns/legacy"
    camp_dir.mkdir(mode=0o700, parents=True)
    legacy_doc = {
        "schema": "nix-update-promotion-campaign-v1",
        "version": 1,
        "campaign_digest": "",
        "state": "COMPLETE_NO_ACTIVATION",
        "last_outcome": "COMPLETE_NO_ACTIVATION",
        "activationPerformed": False,
        "created_at": "2026-09-19T00:00:00Z",
        "updated_at": "2026-09-19T00:00:00Z",
        "input": "core",
        "revision": "a" * 40,
        "selected": os.fspath(checkouts["composition-private-nd"]),
        "dry_run": False,
        "stages": [
            {
                "name": "core",
                "kind": "core",
                "repository": os.fspath(checkouts["core"]),
                "input": "core",
                "revision": "a" * 40,
                "status": "READY_FOR_REVIEW",
                "attempts": [],
                "current_plan": None,
                "published_commit": None,
                "impact": None,
                "failure": None,
            }
        ],
        "current_stage": 0,
        "final_impact": None,
        "blocker": None,
        "history": [],
    }
    legacy_doc = seal(legacy_doc)
    atomic_private_write(camp_dir / "campaign.json", canonical_bytes(legacy_doc))

    with pytest.raises(ConfigError, match="uses legacy schema nix-update-promotion-campaign-v1"):
        prepare_snapshot_adoption(workflow, config_path, campaign_dir=camp_dir, home=home)


def test_arbitrary_checkout_head_movement_does_not_change_candidate(tmp_path: Path, template_env, monkeypatch):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    new_core = _git_commit(checkouts["core"], "promote core")
    new_comp = _git_commit(checkouts["composition-nd"], "promote comp")
    new_priv = _git_commit(checkouts["composition-private-nd"], "promote priv")

    stages = [
        {
            "name": "composition-nd",
            "kind": "composition",
            "repository": os.fspath(checkouts["composition-nd"]),
            "input": "core",
            "revision": new_core,
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

    camp_dir = state_root / "campaigns/core-camp"
    _make_campaign(camp_dir, checkouts["composition-private-nd"], "core", new_core, stages)

    # Now simulate detached HEAD or branch move in core
    subprocess.run(["git", "checkout", "-b", "scratch-work"], cwd=checkouts["core"], check=True, capture_output=True, env=GIT_ENV)
    _git_commit(checkouts["core"], "unrelated scratch commit")

    # Prepare should still bind candidate to new_core because of campaign authority!
    plan_dir = prepare_snapshot_adoption(workflow, config_path, campaign_dir=camp_dir, home=home)
    plan = json.loads((plan_dir / "plan.json").read_text())
    assert plan["candidate_pins"]["core"] == new_core
    assert plan["candidate_pins"]["composition-nd"] == new_comp
    assert plan["candidate_pins"]["composition-private-nd"] == new_priv


@pytest.mark.parametrize("output_name", [None, "reviewed-plan", "reviewer's plan $HOME; `echo no`"])
def test_plan_markdown_apply_artifact(tmp_path: Path, template_env, monkeypatch, output_name):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)
    new_core = _git_commit(checkouts["core"], "promoted commit")
    config_before = config_path.read_bytes(), config_path.stat()
    source_before = {
        path: path.read_bytes() for checkout in checkouts.values()
        for path in checkout.rglob("*") if path.is_file()
    }
    output_dir = state_root / output_name if output_name is not None else None
    plan_dir = prepare_snapshot_adoption(
        workflow, config_path, explicit_pins=[f"core={new_core}"], home=home,
        output_dir=output_dir,
    )
    if output_dir is not None:
        assert plan_dir == output_dir.resolve()
    else:
        assert plan_dir.name.startswith("plan-")
        assert plan_dir.parent == state_root / "snapshot-adoption-plans"
    artifacts_before = {path.name: path.read_bytes() for path in plan_dir.iterdir()}
    plan = json.loads(artifacts_before["plan.json"])
    plan_before = canonical_bytes(plan)
    markdown = (plan_dir / "plan.md").read_text()
    command, = [line for line in markdown.splitlines() if line.startswith("nix-update snapshot apply ")]
    assert shlex.split(command) == ["nix-update", "snapshot", "apply", str(plan_dir)]
    assert str(plan_dir) not in {plan["config_path"], plan["transaction"]["path"]}
    assert f"- **Config Target**: `{plan['config_path']}`" in markdown
    material_keys = {
        "schema", "version", "config_path", "preimage_sha256", "candidate_sha256",
        "candidate_pins", "current_pins", "changed_pins", "sources_authority",
        "campaign", "tool", "transaction",
    }
    assert set(plan) == material_keys | {"status", "created_at", "plan_digest"}
    assert plan["schema"] == "nix-update-snapshot-adoption-plan-v1"
    assert plan["version"] == 1
    material = {key: plan[key] for key in material_keys}
    assert sha256_bytes(canonical_bytes(material)) == plan["plan_digest"]
    assert source_snapshot_adoption.render_plan_markdown(plan, plan_dir) == markdown
    alternate = source_snapshot_adoption.render_plan_markdown(plan, plan_dir / "alternate")
    assert alternate != markdown
    assert canonical_bytes(plan) == plan_before == artifacts_before["plan.json"]
    assert {path.name: path.read_bytes() for path in plan_dir.iterdir()} == artifacts_before
    assert config_path.read_bytes() == config_before[0]
    after = config_path.stat()
    for field in ("st_mode", "st_uid", "st_gid", "st_ino", "st_mtime_ns"):
        assert getattr(after, field) == getattr(config_before[1], field)
    assert {
        path: path.read_bytes() for checkout in checkouts.values()
        for path in checkout.rglob("*") if path.is_file()
    } == source_before
    assert not (state_root / "snapshot-backups").exists()
    assert not (state_root / "snapshot-consumed").exists()


@pytest.mark.parametrize("symlink_ancestor", [False, True], ids=["directory", "ancestor"])
def test_prepare_rejects_symlink_output(tmp_path: Path, template_env, monkeypatch, symlink_ancestor):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    new_core = _git_commit(checkouts["core"], "promoted commit")
    target = state_root / "real-output"
    target.mkdir(mode=0o700)
    link = state_root / "linked-output"
    link.symlink_to(target, target_is_directory=True)
    output_dir = link / "plan" if symlink_ancestor else link
    config_before = config_path.read_bytes()

    with pytest.raises(ConfigError, match="symlink traversal is forbidden"):
        prepare_snapshot_adoption(
            workflow, config_path, explicit_pins=[f"core={new_core}"], home=home,
            output_dir=output_dir,
        )

    assert list(target.iterdir()) == []
    assert config_path.read_bytes() == config_before


def test_prepare_is_non_mutating(tmp_path: Path, template_env, monkeypatch):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    pre_bytes = config_path.read_bytes()
    new_core = _git_commit(checkouts["core"], "promote core")
    new_comp = _git_commit(checkouts["composition-nd"], "promote comp")
    new_priv = _git_commit(checkouts["composition-private-nd"], "promote priv")

    stages = [
        {
            "name": "composition-nd",
            "kind": "composition",
            "repository": os.fspath(checkouts["composition-nd"]),
            "input": "core",
            "revision": new_core,
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

    camp_dir = state_root / "campaigns/core-camp"
    _make_campaign(camp_dir, checkouts["composition-private-nd"], "core", new_core, stages)

    plan_dir = prepare_snapshot_adoption(workflow, config_path, campaign_dir=camp_dir, home=home)
    assert plan_dir.exists()
    assert (plan_dir / "plan.json").exists()
    assert (plan_dir / "plan.md").exists()
    assert (plan_dir / "candidate.toml").exists()

    # config file was NOT modified
    assert config_path.read_bytes() == pre_bytes


@pytest.mark.parametrize("plan_file", [False, True], ids=["directory", "plan-json"])
def test_apply_atomic_replacement_and_backup(tmp_path: Path, template_env, monkeypatch, plan_file):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    pre_bytes = config_path.read_bytes()
    orig_mode = stat.S_IMODE(config_path.stat().st_mode)

    new_core = _git_commit(checkouts["core"], "promoted commit")
    plan_dir = prepare_snapshot_adoption(
        workflow, config_path, explicit_pins=[f"core={new_core}"], home=home
    )
    plan = json.loads((plan_dir / "plan.json").read_text())
    digest = plan["plan_digest"]
    expected_confirm = CONFIRMATION_TEMPLATE.format(plan_digest=digest)

    ret = apply_snapshot_adoption(
        workflow,
        plan_dir / "plan.json" if plan_file else plan_dir,
        confirmation=expected_confirm,
        home=home,
    )
    assert ret == 0

    # Verify config was updated
    new_bytes = config_path.read_bytes()
    assert new_bytes != pre_bytes
    assert stat.S_IMODE(config_path.stat().st_mode) == orig_mode
    loaded = load_control_config(config_path, operator_home=home)
    assert loaded.snapshot.by_key["core"] == new_core

    # Verify backup exists
    backups = list((state_root / "snapshot-backups").glob("*.toml"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == pre_bytes

    # Verify result.json
    result = json.loads((plan_dir / "result.json").read_text())
    assert result["status"] == "ADOPTED"
    assert result["changed"] is True
    assert result["activation_performed"] is False
    assert result["publication_performed"] is False


def test_apply_preimage_drift_refusal(tmp_path: Path, template_env, monkeypatch):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    new_core = _git_commit(checkouts["core"], "promoted commit")
    plan_dir = prepare_snapshot_adoption(
        workflow, config_path, explicit_pins=[f"core={new_core}"], home=home
    )
    plan = json.loads((plan_dir / "plan.json").read_text())
    digest = plan["plan_digest"]

    # Mutate config before apply
    config_path.write_text(config_path.read_text() + "\n# drift\n")

    with pytest.raises(ConfigError, match="preimage drift"):
        apply_snapshot_adoption(
            workflow,
            plan_dir,
            confirmation=CONFIRMATION_TEMPLATE.format(plan_digest=digest),
            home=home,
        )


def test_apply_source_drift_refusal(tmp_path: Path, template_env, monkeypatch):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    # Initial commit on main
    init_rev = initial_pins["core"]
    # New commit on main
    new_core = _git_commit(checkouts["core"], "promoted commit")

    plan_dir = prepare_snapshot_adoption(
        workflow, config_path, explicit_pins=[f"core={new_core}"], home=home
    )
    plan = json.loads((plan_dir / "plan.json").read_text())
    digest = plan["plan_digest"]

    # Rewind main branch so new_core is no longer an ancestor
    subprocess.run(["git", "reset", "--hard", init_rev], cwd=checkouts["core"], check=True, capture_output=True, env=GIT_ENV)

    with pytest.raises(ConfigError, match="not an ancestor of release head"):
        apply_snapshot_adoption(
            workflow,
            plan_dir,
            confirmation=CONFIRMATION_TEMPLATE.format(plan_digest=digest),
            home=home,
        )


def test_apply_tool_drift_refusal(tmp_path: Path, template_env, monkeypatch):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    new_core = _git_commit(checkouts["core"], "promoted commit")
    plan_dir = prepare_snapshot_adoption(
        workflow, config_path, explicit_pins=[f"core={new_core}"], home=home
    )
    plan = json.loads((plan_dir / "plan.json").read_text())
    digest = plan["plan_digest"]

    # Mock tool identity drift
    from nix_darwin_local import promotion_qualification

    orig_tool = promotion_qualification.promotion_tool_identity(workflow.core_dir)
    drifted_tool = dict(orig_tool)
    drifted_tool["digest"] = "f" * 64
    monkeypatch.setattr(source_snapshot_adoption, "promotion_tool_identity", lambda _: drifted_tool)

    with pytest.raises(ConfigError, match="tool identity has drifted"):
        apply_snapshot_adoption(
            workflow,
            plan_dir,
            confirmation=CONFIRMATION_TEMPLATE.format(plan_digest=digest),
            home=home,
        )


def test_confirmation_and_no_tty_behavior(tmp_path: Path, template_env, monkeypatch):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    new_core = _git_commit(checkouts["core"], "promoted commit")
    plan_dir = prepare_snapshot_adoption(
        workflow, config_path, explicit_pins=[f"core={new_core}"], home=home
    )
    plan = json.loads((plan_dir / "plan.json").read_text())
    digest = plan["plan_digest"]

    # Mismatched confirmation
    with pytest.raises(ConfigError, match="typed confirmation does not match"):
        apply_snapshot_adoption(
            workflow,
            plan_dir,
            confirmation="WRONG CONFIRMATION",
            home=home,
        )

    # Missing TTY raises TerminalUnavailableError
    def fake_tty(prompt):
        raise TerminalUnavailableError("snapshot adoption confirmation requires a controlling terminal")

    monkeypatch.setattr(source_snapshot_adoption, "_snapshot_tty_confirmation", fake_tty)
    with pytest.raises(TerminalUnavailableError, match="snapshot adoption confirmation requires a controlling terminal"):
        apply_snapshot_adoption(workflow, plan_dir, confirmation=None, home=home)


def test_consumed_and_replayed_plan_truthful_reconciliation(tmp_path: Path, template_env, monkeypatch):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    new_core = _git_commit(checkouts["core"], "promoted commit")
    plan_dir = prepare_snapshot_adoption(
        workflow, config_path, explicit_pins=[f"core={new_core}"], home=home
    )
    plan = json.loads((plan_dir / "plan.json").read_text())
    digest = plan["plan_digest"]
    confirm = CONFIRMATION_TEMPLATE.format(plan_digest=digest)

    # First apply
    assert apply_snapshot_adoption(workflow, plan_dir, confirmation=confirm, home=home) == 0

    # Replay immediately when postimage is already installed: reconciles truthfully (returns 0)
    assert apply_snapshot_adoption(workflow, plan_dir, confirmation=confirm, home=home) == 0

    # Now if config is subsequently changed to something else, replay should fail closed
    config_path.write_text(config_path.read_text() + "\n# something else\n")
    with pytest.raises(ConfigError, match="already consumed its durable mutation authority"):
        apply_snapshot_adoption(workflow, plan_dir, confirmation=confirm, home=home)


def test_future_source_key_adoption_without_deadlock(tmp_path: Path, template_env, monkeypatch):
    """Adding a future 10th source key allows prepare to derive a complete candidate without deadlock."""
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    # Add a 10th source key to checkouts and nix-darwin.toml, but NOT to [snapshot.sources]
    tenth_dir = home / "sources/source-ten"
    tenth_rev = _init_git_repo(tenth_dir)

    raw_toml = config_path.read_text()
    tenth_section = (
        "\n[sources.source-ten]\n"
        f'checkout = "{tenth_dir}"\n'
        'repository = "git+ssh://git@github.com/Knowledge-Forge-AI/source-ten.git"\n'
        'release_ref = "refs/heads/main"\n'
    )
    # Insert before [snapshot.sources]
    parts = raw_toml.partition("[snapshot.sources]")
    new_toml = parts[0] + tenth_section + "\n" + parts[1] + parts[2]
    config_path.write_text(new_toml)

    # Prepare with --pin source-ten=<tenth_rev>
    plan_dir = prepare_snapshot_adoption(
        workflow,
        config_path,
        explicit_pins=[f"source-ten={tenth_rev}"],
        home=home,
    )
    plan = json.loads((plan_dir / "plan.json").read_text())
    assert "source-ten" in plan["candidate_pins"]
    assert plan["candidate_pins"]["source-ten"] == tenth_rev

    # Apply plan
    digest = plan["plan_digest"]
    confirm = CONFIRMATION_TEMPLATE.format(plan_digest=digest)
    assert apply_snapshot_adoption(workflow, plan_dir, confirmation=confirm, home=home) == 0

    # Now load_control_config strictly requires all 10 sources, and it passes!
    final_config = load_control_config(config_path, operator_home=home, allow_incomplete_snapshot=False)
    assert len(final_config.snapshot.by_key) == 10
    assert final_config.snapshot.by_key["source-ten"] == tenth_rev


def test_candidate_bytes_tamper_rejection(tmp_path: Path, template_env, monkeypatch):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    new_core = _git_commit(checkouts["core"], "promoted commit")
    plan_dir = prepare_snapshot_adoption(
        workflow, config_path, explicit_pins=[f"core={new_core}"], home=home
    )
    plan = json.loads((plan_dir / "plan.json").read_text())
    digest = plan["plan_digest"]
    confirm = CONFIRMATION_TEMPLATE.format(plan_digest=digest)

    # Tamper with candidate.toml in plan directory
    candidate_file = plan_dir / "candidate.toml"
    candidate_file.write_text(candidate_file.read_text() + "\n# tampered\n")

    with pytest.raises(ConfigError, match="candidate config bytes in plan directory do not match candidate_sha256 in plan"):
        apply_snapshot_adoption(workflow, plan_dir, confirmation=confirm, home=home)


def test_campaign_and_pin_mutual_exclusivity(tmp_path: Path, template_env, monkeypatch):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    camp_dir = state_root / "campaigns/test-camp"
    _make_campaign(camp_dir, checkouts["composition-private-nd"], "core", "a" * 40, [])

    with pytest.raises(ConfigError, match="cannot combine --campaign and --pin"):
        prepare_snapshot_adoption(
            workflow,
            config_path,
            campaign_dir=camp_dir,
            explicit_pins=["core=" + "a" * 40],
            home=home,
        )


def test_apply_time_campaign_drift_rejection(tmp_path: Path, template_env, monkeypatch):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    new_core = _git_commit(checkouts["core"], "core commit")

    camp_dir = state_root / "campaigns/comp-campaign"
    _make_campaign(camp_dir, checkouts["core"], "core", new_core, [])

    plan_dir = prepare_snapshot_adoption(workflow, config_path, campaign_dir=camp_dir, home=home)
    plan = json.loads((plan_dir / "plan.json").read_text())
    confirm = CONFIRMATION_TEMPLATE.format(plan_digest=plan["plan_digest"])

    # Modify campaign and re-persist so it has valid canonical bytes but different digest
    from nix_darwin_local.promotion_campaign_state import load_campaign
    camp_doc = load_campaign(camp_dir)
    camp_doc["state"] = "COMPLETE_NO_ACTIVATION"
    camp_doc["last_outcome"] = "COMPLETE_NO_ACTIVATION"
    persist_campaign(camp_dir, camp_doc)

    with pytest.raises(ConfigError, match="campaign journal has been modified or tampered with"):
        apply_snapshot_adoption(workflow, plan_dir, confirmation=confirm, home=home)


def test_render_candidate_config_handles_bare_snapshot_header(tmp_path: Path, template_env, monkeypatch):
    home, state_root, config_path, checkouts, initial_pins, workflow = _create_environment(tmp_path, template_env)
    monkeypatch.setattr(deployment_workflow, "_private_state_root", lambda: state_root)

    raw = config_path.read_text()
    # Replace [snapshot.sources] with [snapshot]\n[snapshot.sources]
    bare_and_sub = raw.replace("[snapshot.sources]", "[snapshot]\n[snapshot.sources]")
    config_path.write_text(bare_and_sub)

    new_core = _git_commit(checkouts["core"], "new core")
    plan_dir = prepare_snapshot_adoption(
        workflow, config_path, explicit_pins=[f"core={new_core}"], home=home
    )
    plan = json.loads((plan_dir / "plan.json").read_text())
    confirm = CONFIRMATION_TEMPLATE.format(plan_digest=plan["plan_digest"])
    assert apply_snapshot_adoption(workflow, plan_dir, confirmation=confirm, home=home) == 0

    final_toml = config_path.read_text()
    assert final_toml.count("[snapshot.sources]") == 1
    loaded = load_control_config(config_path, operator_home=home, allow_incomplete_snapshot=False)
    assert loaded.snapshot.by_key["core"] == new_core


@pytest.mark.parametrize(
    ("pin", "message"),
    [("core", "expected KEY=REV"), ("=abc", "empty source key"), ("core=abc", "expected 40-hex")],
)
def test_pin_override_rejects_malformed_authority(pin, message):
    with pytest.raises(ConfigError, match=message):
        source_snapshot_adoption.parse_pin_arg(pin)


def test_pin_override_normalizes_whitespace_and_revision_case():
    assert source_snapshot_adoption.parse_pin_arg(" core = " + "A" * 40) == ("core", "a" * 40)


@pytest.mark.parametrize("alias", ["repository", "checkout-name"])
def test_campaign_input_alias_resolves_to_catalog_key(tmp_path, alias):
    entry = local_source.SourceCatalogEntry(
        key="core", checkout=tmp_path / "core-checkout",
        repository="https://example.invalid/team/core.git", release_ref="refs/heads/main",
    )
    catalog = local_source.coerce_source_catalog((entry,))
    name = entry.canonical_repository_identity if alias == "repository" else entry.checkout.name
    assert source_snapshot_adoption.map_campaign_input_to_source(name, catalog) == entry


@pytest.mark.parametrize("status", ["LOCAL_COMMITTED", "PUBLISHED", "ALREADY_SELECTED", "READY_FOR_REVIEW"])
def test_reconciliation_campaign_requires_determinate_stage_authority(tmp_path, template_env, status):
    home, state_root, config_path, checkouts, pins, _ = _create_environment(tmp_path, template_env)
    campaign = state_root / "campaigns/reconciliation"
    doc = _make_campaign(
        campaign, checkouts["composition-private-nd"], "core", pins["core"], [],
        state="AWAITING_DEPLOYMENT_RECONCILIATION",
    )
    stage = doc["stages"][0]
    stage.update(repository=str(checkouts["composition-nd"]), status=status, local_commit=None)
    if status == "LOCAL_COMMITTED":
        stage["local_commit"] = pins["composition-nd"]
    elif status == "PUBLISHED":
        stage["published_commit"] = pins["composition-nd"]
    persist_campaign(campaign, doc)
    before = (campaign / "campaign.json").read_bytes()
    catalog = load_control_config(config_path, operator_home=home).source_catalog

    if status == "READY_FOR_REVIEW":
        with pytest.raises(ConfigError, match="indeterminate deployment impact"):
            extract_campaign_pins(campaign, catalog)
    else:
        result = extract_campaign_pins(campaign, catalog)
        expected = {"core": pins["core"]}
        if status != "ALREADY_SELECTED":
            expected["composition-nd"] = pins["composition-nd"]
        assert result["pins"] == expected
    assert (campaign / "campaign.json").read_bytes() == before


@pytest.mark.parametrize("defect", ["revision", "missing-stage-commit"])
def test_campaign_pin_extraction_rejects_incomplete_commit_authority(tmp_path, template_env, defect):
    home, state_root, config_path, checkouts, pins, _ = _create_environment(tmp_path, template_env)
    campaign = state_root / "campaigns/incomplete-authority"
    doc = _make_campaign(campaign, checkouts["core"], "core", pins["core"], [])
    if defect == "revision":
        doc["revision"] = "main"
        message = "invalid promoted revision"
    else:
        doc["stages"][0].update(local_commit=None, status="READY_FOR_REVIEW")
        message = "neither a valid commit nor ALREADY_SELECTED"
    persist_campaign(campaign, doc)
    before = (campaign / "campaign.json").read_bytes()
    catalog = load_control_config(config_path, operator_home=home).source_catalog
    with pytest.raises(ConfigError, match=message):
        extract_campaign_pins(campaign, catalog)
    assert (campaign / "campaign.json").read_bytes() == before


@pytest.mark.parametrize("placement", ["absent", "before-flake", "non-contiguous"])
def test_snapshot_render_preserves_other_tables_and_rejects_split_sections(tmp_path, template_env, placement):
    home, _, config_path, _, pins, _ = _create_environment(tmp_path, template_env)
    raw = config_path.read_text()
    other, snapshot = raw.split("[snapshot.sources]", 1)
    if placement == "absent":
        preimage = other.encode()
    elif placement == "before-flake":
        preimage = ("# operator comment\n[snapshot.sources]" + snapshot + other).encode()
    else:
        preimage = ("[snapshot]\n" + raw).encode()
    candidate_pins = {**pins, "core": "b" * 40}
    if placement == "non-contiguous":
        with pytest.raises(ConfigError, match="multiple non-contiguous snapshot"):
            render_candidate_config(preimage, candidate_pins, home)
    else:
        candidate = render_candidate_config(preimage, candidate_pins, home)
        assert other.encode() in candidate
        if placement == "before-flake":
            assert candidate.startswith(b"# operator comment\n")
        loaded = load_control_config(candidate, operator_home=home)
        assert loaded.snapshot.by_key == candidate_pins
    assert config_path.read_text() == raw


@pytest.mark.parametrize("preimage", [b"\xff", b"[snapshot\n"])
def test_snapshot_render_rejects_unparseable_preimage(tmp_path, preimage):
    with pytest.raises(ConfigError, match="UTF-8 TOML"):
        render_candidate_config(preimage, {"core": "a" * 40}, tmp_path)


@pytest.mark.parametrize("defect", ["missing", "directory", "hardlink", "unknown-pin", "no-authority", "incomplete-pins"])
def test_snapshot_prepare_refuses_invalid_inputs_before_source_observation(tmp_path, template_env, monkeypatch, defect):
    home, state_root, config_path, _, pins, workflow = _create_environment(tmp_path, template_env)
    target = config_path
    explicit = [f'core={pins["core"]}']
    message = ""
    if defect == "missing":
        target = config_path.parent / "missing.toml"
        message = "does not exist"
    elif defect == "directory":
        target = config_path.parent
        message = "not a regular file"
    elif defect == "hardlink":
        os.link(config_path, config_path.parent / "alias.toml")
        message = "multiple hard links"
    elif defect == "unknown-pin":
        explicit = ["unknown=" + "a" * 40]
        message = "unknown source key"
    elif defect == "no-authority":
        explicit = []
        message = "no promotion campaign or explicit pins"
    else:
        config_path.write_text(config_path.read_text().split("[snapshot.sources]")[0])
        message = "missing configured source keys"
    before = config_path.read_bytes()

    def unexpected_observation(*args, **kwargs):
        pytest.fail("invalid adoption input reached source observation")

    monkeypatch.setattr(source_snapshot_adoption, "validate_candidate_pin_local", unexpected_observation)
    with pytest.raises(ConfigError, match=message):
        prepare_snapshot_adoption(workflow, target, explicit_pins=explicit, home=home)
    assert config_path.read_bytes() == before
    assert not (state_root / "snapshot-adoption-plans").exists()


@pytest.mark.parametrize("matches", [1, 2])
def test_snapshot_prepare_auto_selection_requires_one_authoritative_campaign(tmp_path, template_env, monkeypatch, matches):
    home, state_root, config_path, checkouts, pins, workflow = _create_environment(tmp_path, template_env)
    campaign = state_root / "campaigns/dry"
    _make_campaign(campaign, checkouts["core"], "core", pins["core"], [], dry_run=True)
    monkeypatch.setattr(source_snapshot_adoption, "_existing_matches", lambda *args, **kwargs: [campaign] * matches)
    before = config_path.read_bytes()
    message = "dry-run campaigns" if matches == 1 else "multiple matching promotion campaigns"
    with pytest.raises(ConfigError, match=message):
        prepare_snapshot_adoption(workflow, config_path, home=home)
    assert config_path.read_bytes() == before
    assert not (state_root / "snapshot-adoption-plans").exists()
