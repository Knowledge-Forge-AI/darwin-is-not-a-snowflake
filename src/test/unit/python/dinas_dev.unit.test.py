# nix-darwin-test-source-target: libexec/dinas_foundation/dev.py
# nix-darwin-test-source-target: bin/dinas-dev
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def dev_module(repo_root: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    return importlib.import_module("dinas_foundation.dev")


def test_validate_name_rejects_traversal_and_slashes(dev_module):
    with pytest.raises(dev_module.DevError, match="forbidden characters"):
        dev_module.validate_name("a/b", "command")
    with pytest.raises(dev_module.DevError, match="forbidden characters"):
        dev_module.validate_name("..", "namespace")
    with pytest.raises(dev_module.DevError, match="forbidden characters"):
        dev_module.validate_name("a b", "command")
    with pytest.raises(dev_module.DevError, match="non-empty string"):
        dev_module.validate_name("", "command")
    assert dev_module.validate_name("valid-name_1", "command") == "valid-name_1"


def test_list_and_which_and_exec(dev_module, tmp_path: Path, monkeypatch, capsys):
    dev_root = tmp_path / "nbin_dev"
    dev_root.mkdir()
    monkeypatch.setattr(dev_module, "resolve_dev_root", lambda: dev_root)

    # List empty dev root
    assert dev_module.main(["list"]) == 0
    captured = capsys.readouterr()
    assert "no active development namespaces" in captured.err

    # Create a namespace directory with an executable script
    ns_dir = tmp_path / "checkout_bin"
    ns_dir.mkdir()
    script = ns_dir / "my-cmd"
    script.write_text("#!/bin/sh\nprintf '%s\n' \"$@\"\n", encoding="utf-8")
    script.chmod(0o755)

    # Link namespace in dev_root
    (dev_root / "dinas").symlink_to(ns_dir)

    # List now shows the namespace
    assert dev_module.main(["list"]) == 0
    captured = capsys.readouterr()
    assert f"dinas -> {ns_dir}" in captured.out

    # Which finds the command
    assert dev_module.main(["which", "dinas", "my-cmd"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == str(script)

    # Which fails for missing command
    assert dev_module.main(["which", "dinas", "nonexistent"]) == 1
    captured = capsys.readouterr()
    assert "command not found" in captured.err

    # Which fails for missing namespace
    assert dev_module.main(["which", "missing", "my-cmd"]) == 1
    captured = capsys.readouterr()
    assert "namespace not found" in captured.err


def test_dinas_dev_cli_process(repo_root: Path, tmp_path: Path):
    dev_root = tmp_path / ".local/nbin_dev"
    dev_root.mkdir(parents=True)

    ns_dir = tmp_path / "ns_bin"
    ns_dir.mkdir()
    script = ns_dir / "echo-args"
    script.write_text("#!/bin/sh\nprintf '%s\n' \"$@\"\n", encoding="utf-8")
    script.chmod(0o755)

    (dev_root / "testns").symlink_to(ns_dir)

    env = os.environ.copy()
    env["HOME"] = str(tmp_path)

    bin_path = repo_root / "bin/dinas-dev"

    # Test exec forwards arguments exactly
    proc = subprocess.run(
        [sys.executable, str(bin_path), "exec", "testns", "echo-args", "arg 1", "arg2"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0
    assert proc.stdout == "arg 1\narg2\n"

    # Test shell spawns with modified PATH
    proc_shell = subprocess.run(
        [sys.executable, str(bin_path), "shell", "testns"],
        capture_output=True,
        text=True,
        input="which echo-args\nexit\n",
        env=env,
    )
    assert proc_shell.returncode == 0
    assert str(script) in proc_shell.stdout


def test_resolve_dev_root_honors_typed_control_config(dev_module, tmp_path: Path, monkeypatch):
    home = tmp_path / "user"
    control_dir = home / ".local/nix-darwin/config"
    control_dir.mkdir(parents=True)
    (control_dir / "nix-darwin.toml").write_text(
        '[paths]\nnbin_dev_root = "$HOME/.local/custom-candidates"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    assert dev_module.resolve_dev_root() == home / ".local/custom-candidates"
