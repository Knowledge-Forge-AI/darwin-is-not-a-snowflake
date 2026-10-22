# nix-darwin-test-source-target: bin/dinas-root-cutover
# nix-darwin-test-source-target: libexec/nix_darwin_local/root_cutover.py
"""Retirement boundary verification: former options fail closed and never touch filesystem."""
from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "libexec"))
from nix_darwin_local import root_cutover as cut


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path):
    old = tmp_path / ".flakes"
    (old / "bin").mkdir(parents=True)
    (old / "libexec/nix_darwin_local").mkdir(parents=True)
    (old / "flake.nix").write_text("{}")
    (old / "bin/nix-update").write_text("fixture")
    (old / "libexec/nix_darwin_local/launcher.py").write_text("fixture")
    git(old, "init", "-b", "main")
    git(old, "config", "user.name", "Fixture")
    git(old, "config", "user.email", "fixture@example.invalid")
    git(old, "add", ".")
    git(old, "commit", "-qm", "fixture")
    config = tmp_path / ".local/nix-darwin/config/nix-darwin.toml"
    config.parent.mkdir(parents=True)
    config.write_text('[sources.core]\ncheckout = "$HOME/.flakes"\nrepository = "https://example.org/core.git"\nrelease_ref = "refs/heads/main"\n')
    return tmp_path, old, config, git(old, "rev-parse", "HEAD")


def test_refuse_operational_invocation_preserves_checkout_and_config(checkout, capsys):
    home, old, config, rev = checkout
    before_config = config.read_bytes()
    before_git = git(old, "rev-parse", "HEAD")
    target = home / ".local/nix-darwin/darwin-is-not-a-snowflake"

    assert cut.main(["--commit", rev, "--home", str(home), "--old-path", str(old), "--new-path", str(target), "--config", str(config)]) == 1

    captured = capsys.readouterr()
    assert "dinas-root-cutover is retired" in captured.err

    # Check that filesystem is completely unmodified
    assert (old / ".git").is_dir()
    assert git(old, "rev-parse", "HEAD") == before_git
    assert config.read_bytes() == before_config
    assert not target.exists()
    assert not (home / ".local/nix-darwin/root-cutover-journal.json").exists()


def test_dry_run_fails_closed_without_inspection(checkout, capsys):
    home, old, config, rev = checkout
    target = home / ".local/nix-darwin/darwin-is-not-a-snowflake"

    assert cut.main(["--dry-run", "--commit", rev, "--home", str(home)]) == 1

    captured = capsys.readouterr()
    assert "dinas-root-cutover is retired" in captured.err
    assert not target.exists()


@pytest.mark.parametrize(
    "flag_combo",
    [
        ["--old-path", "/nonexistent/old"],
        ["--new-path", "/nonexistent/new"],
        ["--journal-path", "/nonexistent/journal.json"],
        ["--navigation-symlink", "/nonexistent/symlink"],
        ["--config", "/nonexistent/config.toml"],
        ["--commit", "bad_hex_sha"],
        ["--commit", ""],
        ["positional_rev_only"],
        ["--unknown-legacy-switch"],
    ],
)
def test_boundary_argument_combinations_fail_closed_inertly(checkout, capsys, flag_combo):
    assert cut.main(flag_combo) == 1
    captured = capsys.readouterr()
    assert "dinas-root-cutover is retired" in captured.err


def test_bin_wrapper_via_runpy_fails_closed(repo_root, checkout, monkeypatch, capsys):
    home, old, config, rev = checkout
    target = home / ".local/nix-darwin/darwin-is-not-a-snowflake"
    script = repo_root / "bin/dinas-root-cutover"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dinas-root-cutover",
            "--commit", rev,
            "--home", str(home),
            "--new-path", str(target),
        ],
    )
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(script), run_name="__main__")

    assert stopped.value.code == 1
    captured = capsys.readouterr()
    assert "dinas-root-cutover is retired" in captured.err
    assert not target.exists()


def test_bin_wrapper_help_via_runpy(repo_root, monkeypatch, capsys):
    script = repo_root / "bin/dinas-root-cutover"
    monkeypatch.setattr(sys, "argv", ["dinas-root-cutover", "--help"])

    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(script), run_name="__main__")

    assert stopped.value.code == 0
    captured = capsys.readouterr()
    assert "dinas-root-cutover" in captured.out
    assert "retired" in captured.out.lower()
