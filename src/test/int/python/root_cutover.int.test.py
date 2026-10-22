# nix-darwin-test-source-target: bin/dinas-root-cutover
# nix-darwin-test-source-target: libexec/nix_darwin_local/root_cutover.py
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


@pytest.fixture
def bin_script(repo_root: Path) -> Path:
    script = repo_root / "bin/dinas-root-cutover"
    assert script.is_file()
    return script


def setup_disposable_environment(home: Path) -> tuple[Path, Path, Path, Path, str]:
    old_checkout = home / ".flakes"
    old_checkout.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(old_checkout), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(old_checkout), "config", "user.email", "operator@example.com"], check=True)
    subprocess.run(["git", "-C", str(old_checkout), "config", "user.name", "Operator"], check=True)

    (old_checkout / "flake.nix").write_text("# Darwin flake\n", encoding="utf-8")
    (old_checkout / "bin").mkdir(parents=True, exist_ok=True)
    (old_checkout / "bin/dinas-dev").write_text("#!/bin/sh\necho dev\n", encoding="utf-8")
    (old_checkout / "libexec/nix_darwin_local").mkdir(parents=True, exist_ok=True)
    (old_checkout / "libexec/nix_darwin_local/__init__.py").write_text("", encoding="utf-8")
    (old_checkout / "README.md").write_text("# Core Flake\n", encoding="utf-8")

    subprocess.run(["git", "-C", str(old_checkout), "add", "."], check=True)
    subprocess.run(["git", "-C", str(old_checkout), "commit", "-q", "-m", "Initial commit"], check=True)
    proc = subprocess.run(
        ["git", "-C", str(old_checkout), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    commit_sha = proc.stdout.strip()

    new_checkout = home / ".local/nix-darwin/darwin-is-not-a-snowflake"

    config_path = home / ".local/nix-darwin/config/nix-darwin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_content = """[paths]
workspace_root = "$HOME/.local/nix-darwin"
nbin_root = "$HOME/.local/nbin"

[flake]
active = ""

[sources.core]
checkout = "$HOME/.flakes"
repository = "https://github.com/example/darwin-is-not-a-snowflake.git"
release_ref = "refs/heads/main"
"""
    config_path.write_text(config_content, encoding="utf-8")

    nav_link = home / "ai/core-symlink"
    nav_link.parent.mkdir(parents=True, exist_ok=True)
    nav_link.symlink_to(old_checkout)

    return old_checkout, new_checkout, config_path, nav_link, commit_sha


def test_cli_help_options(bin_script: Path):
    help_proc = subprocess.run([sys.executable, str(bin_script), "--help"], capture_output=True, text=True)
    assert help_proc.returncode == 0
    assert "dinas-root-cutover" in help_proc.stdout
    assert "retired" in help_proc.stdout.lower()

    short_proc = subprocess.run([sys.executable, str(bin_script), "-h"], capture_output=True, text=True)
    assert short_proc.returncode == 0
    assert "dinas-root-cutover" in short_proc.stdout


def test_cli_refuses_operational_invocation_without_arguments(bin_script: Path):
    proc = subprocess.run([sys.executable, str(bin_script)], capture_output=True, text=True)
    assert proc.returncode == 1
    assert "dinas-root-cutover is retired" in proc.stderr
    assert "operational cutover is refused" in proc.stderr


def test_cli_refuses_former_operational_args_and_proves_no_filesystem_mutation(bin_script: Path, tmp_path: Path):
    home = tmp_path / "home"
    old_checkout, new_checkout, config_path, nav_link, commit_sha = setup_disposable_environment(home)
    journal_path = home / "cutover-journal.json"
    before_config = config_path.read_bytes()
    before_nav = nav_link.resolve()

    cmd = [
        sys.executable,
        str(bin_script),
        "--commit", commit_sha,
        "--home", str(home),
        "--old-path", str(old_checkout),
        "--new-path", str(new_checkout),
        "--config", str(config_path),
        "--journal-path", str(journal_path),
        "--navigation-symlink", str(nav_link),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 1
    assert "dinas-root-cutover is retired" in proc.stderr

    # Assert former args cannot recreate filesystem state or mutate existing state
    assert not new_checkout.exists()
    assert not journal_path.exists()
    assert not (home / "cutover-journal.json.lock").exists()
    assert config_path.read_bytes() == before_config
    assert nav_link.resolve() == before_nav
    assert (old_checkout / ".git").is_dir()


def test_cli_refuses_dry_run_flag(bin_script: Path, tmp_path: Path):
    home = tmp_path / "home"
    old_checkout, new_checkout, config_path, nav_link, commit_sha = setup_disposable_environment(home)

    cmd = [
        sys.executable,
        str(bin_script),
        "--dry-run",
        "--commit", commit_sha,
        "--home", str(home),
        "--new-path", str(new_checkout),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 1
    assert "dinas-root-cutover is retired" in proc.stderr
    assert not new_checkout.exists()


def test_cli_refuses_positional_commit(bin_script: Path):
    sha = "c" * 40
    proc = subprocess.run([sys.executable, str(bin_script), sha], capture_output=True, text=True)
    assert proc.returncode == 1
    assert "dinas-root-cutover is retired" in proc.stderr


@pytest.mark.parametrize(
    "flags",
    [
        ["--commit", "0" * 40],
        ["--home", "/tmp"],
        ["--old-path", "/tmp/.flakes"],
        ["--new-path", "/tmp/new"],
        ["--config", "/tmp/config.toml"],
        ["--journal-path", "/tmp/journal.json"],
        ["--navigation-symlink", "/tmp/link"],
    ],
)
def test_cli_refuses_individual_former_flags(bin_script: Path, flags: list[str]):
    proc = subprocess.run([sys.executable, str(bin_script), *flags], capture_output=True, text=True)
    assert proc.returncode == 1
    assert "dinas-root-cutover is retired" in proc.stderr
