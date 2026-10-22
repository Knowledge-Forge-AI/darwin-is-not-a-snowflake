# nix-darwin-test-source-target: bin/brave-youtube-cdp
# nix-darwin-test-source-target: bin/cnt-wallpaper-gallery
# nix-darwin-test-source-target: bin/codex-git-mcp
# nix-darwin-test-source-target: bin/dinas-dev
# nix-darwin-test-source-target: bin/dinas-reconcile
# nix-darwin-test-source-target: libexec/dinas_foundation/reconcile_plan.py
# nix-darwin-test-source-target: libexec/dinas_foundation/reconciler.py
# nix-darwin-test-source-target: bin/env-allowlist-check
# nix-darwin-test-source-target: bin/env-run
# nix-darwin-test-source-target: bin/env-snapshot
# nix-darwin-test-source-target: bin/nix-darwin-config
# nix-darwin-test-source-target: bin/shared-images-db-audit
# nix-darwin-test-source-target: bin/shared-images-promote-original
# nix-darwin-test-source-target: bin/shared-images-refresh-artist-links
# nix-darwin-test-source-target: bin/shared-images-rehash
# nix-darwin-test-source-target: bin/shared-images-workflow-layout

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def run_entrypoint(repo_root: Path, relative_path: str, monkeypatch, argv: list[str] | None = None):
    script_path = repo_root / relative_path
    monkeypatch.setattr(sys, "argv", [str(script_path), *(argv or [])])
    try:
        runpy.run_path(str(script_path), run_name="__main__")
    except SystemExit as error:
        return error.code
    return 0



def test_brave_youtube_cdp_rejects_unknown_command(repo_root: Path, monkeypatch, capsys):
    assert run_entrypoint(repo_root, "bin/brave-youtube-cdp", monkeypatch, ["bogus", "9222"]) == 2
    assert "Usage: brave-youtube-cdp" in capsys.readouterr().err



def test_cnt_wallpaper_gallery_help_is_available(repo_root: Path, monkeypatch, capsys):
    assert run_entrypoint(repo_root, "bin/cnt-wallpaper-gallery", monkeypatch, ["--help"]) == 0
    assert "Clear Clear New Tab DBs or load shared image import files" in capsys.readouterr().out


def test_codex_git_mcp_help_is_available(repo_root: Path, monkeypatch, capsys):
    assert run_entrypoint(repo_root, "bin/codex-git-mcp", monkeypatch, ["--help"]) == 0
    assert "Codex-managed repository allowlist" in capsys.readouterr().out


@pytest.mark.parametrize("entrypoint", ["bin/dinas-dev", "bin/dinas-reconcile"])
def test_dinas_foundation_entrypoint_help_is_available(
    repo_root: Path,
    monkeypatch,
    capsys,
    entrypoint: str,
):
    assert run_entrypoint(repo_root, entrypoint, monkeypatch, ["--help"]) == 0
    captured = capsys.readouterr()
    assert "dinas" in (captured.out + captured.err).casefold()


def test_dinas_reconcile_check_is_read_only(repo_root: Path, tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    nbin_root = home / "Command Namespaces"
    nbin_root.mkdir()
    package_bin = tmp_path / "package/bin"
    package_bin.mkdir(parents=True)

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "bin/dinas-reconcile"),
            "--check",
            "--nbin-root", str(nbin_root),
            "--nbin-dev-root", str(home / "Development Namespaces"),
            "--ai-index-root", str(home / "AI Index"),
            "--namespaces-json", json.dumps({"dinas": str(package_bin)}),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["eligible"] is True
    assert not (nbin_root / "dinas").exists()
    assert not (home / "Development Namespaces").exists()
    assert not (home / "AI Index").exists()

    removed_result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "bin/dinas-reconcile"),
            "--legacy-nbin-target", str(home / "legacy-bin"),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
    )
    assert removed_result.returncode == 2
    assert "unrecognized arguments: --legacy-nbin-target" in removed_result.stderr


@pytest.mark.parametrize("check", [False, True])
@pytest.mark.parametrize("target_kind", ["former_flakes", "arbitrary", "dangling", "cyclic"])
def test_dinas_reconcile_rejects_root_symlinks_without_mutation(repo_root, tmp_path, check, target_kind):
    home = tmp_path / "home"
    home.mkdir()
    root = home / "nbin"
    if target_kind == "former_flakes":
        target = home / ".flakes/nix-darwin/bin"
        target.mkdir(parents=True)
    elif target_kind == "arbitrary":
        target = home / "other-bin"
        target.mkdir()
    elif target_kind == "dangling":
        target = home / "missing-bin"
    else:
        target = root
    root.symlink_to(target)
    marker = home / "preserved"
    marker.write_text("unchanged")
    before = sorted(str(p.relative_to(home)) for p in home.rglob("*"))

    result = subprocess.run(
        [sys.executable, str(repo_root / "bin/dinas-reconcile"),
         *(["--check"] if check else []),
         "--nbin-root", str(root),
         "--nbin-dev-root", str(home / "nbin_dev"),
         "--ai-index-root", str(home / "ai")],
        capture_output=True, text=True, env={**os.environ, "HOME": str(home)},
    )
    assert result.returncode == 2
    assert "paths.nbin_root has symlink ambiguity" in result.stderr
    assert result.stdout == ""
    assert root.is_symlink() and root.readlink() == target
    assert sorted(str(p.relative_to(home)) for p in home.rglob("*")) == before
    assert marker.read_text() == "unchanged"


@pytest.mark.parametrize("shell_name", ["bash", "zsh"])
def test_non_default_nbin_root_reconcile_and_shell_discovery(
    repo_root: Path,
    tmp_path: Path,
    shell_name: str,
):
    home = tmp_path / "home"
    home.mkdir()
    nbin_root = home / "Command Namespaces"
    nbin_root.mkdir()
    nbin_dev_root = home / "Development Namespaces"
    ai_root = home / "AI Index"
    package_bin = tmp_path / "managed package/bin"
    package_bin.mkdir(parents=True)
    managed_claude = package_bin / "claude"
    managed_claude.write_text("#!/bin/sh\necho '2.1.259 (Claude Code)'\n", encoding="utf-8")
    managed_claude.chmod(0o755)
    for child in ("alpha", "zeta"):
        (nbin_root / child).mkdir()
    (nbin_root / "bad child").mkdir()
    unsafe_child = nbin_root / "unsafe"
    unsafe_child.mkdir()
    unsafe_child.chmod(0o777)
    native_bin = home / ".local/bin"
    native_bin.mkdir(parents=True)
    native_claude = native_bin / "claude"
    native_claude.write_text("#!/bin/sh\necho '2.1.140 native'\n", encoding="utf-8")
    native_claude.chmod(0o755)

    reconcile = subprocess.run(
        [
            sys.executable,
            str(repo_root / "bin/dinas-reconcile"),
            "--nbin-root", str(nbin_root),
            "--nbin-dev-root", str(nbin_dev_root),
            "--ai-index-root", str(ai_root),
            "--namespaces-json", json.dumps({"dinas": str(package_bin)}),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
    )
    assert reconcile.returncode == 0, reconcile.stderr

    shell_result = subprocess.run(
        [
            f"/bin/{shell_name}",
            "-c",
            f'_dinas_projected_nbin_root="$1"; . "$2"; printf "%s\\n" "$PATH"; command -v claude; claude --version',
            shell_name,
            str(nbin_root),
            str(repo_root / "shell/sh_config/common/004_path"),
        ],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": f"{native_bin}:/usr/bin:/bin"},
    )
    assert shell_result.returncode == 0, shell_result.stderr
    path_value, resolved, version = shell_result.stdout.splitlines()
    path_entries = path_value.split(":")
    assert path_entries[:3] == [
        str(nbin_root / "alpha"),
        str(nbin_root / "dinas"),
        str(nbin_root / "zeta"),
    ]
    assert str(nbin_root) not in path_entries
    assert str(nbin_dev_root) not in path_entries
    assert str(nbin_root / "bad child") not in path_entries
    assert str(unsafe_child) not in path_entries
    assert resolved == str(nbin_root / "dinas/claude")
    assert version == "2.1.259 (Claude Code)"


def test_env_allowlist_check_help_is_available(repo_root: Path, monkeypatch, capsys):
    assert run_entrypoint(repo_root, "bin/env-allowlist-check", monkeypatch, ["--help"]) == 0
    assert "allowlist" in capsys.readouterr().out.casefold()


def test_env_run_imports_env_file_for_child_process(repo_root: Path, tmp_path: Path):
    env_file = tmp_path / "project.env"
    env_file.write_text("ENV_RUN_SENTINEL=present\n", encoding="utf-8")

    result = subprocess.run(
        [
            str(repo_root / "bin/env-run"),
            "--env-file",
            str(env_file),
            "--",
            "/usr/bin/env",
            "python3",
            "-c",
            "import os; print(os.environ.get('ENV_RUN_SENTINEL', ''))",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.stdout == "present\n"
    assert result.stderr == ""


def test_env_snapshot_help_is_available(repo_root: Path, monkeypatch, capsys):
    assert run_entrypoint(repo_root, "bin/env-snapshot", monkeypatch, ["--help"]) == 0
    assert "snapshot" in capsys.readouterr().out.casefold()


def test_nix_darwin_config_help_is_available(repo_root: Path, monkeypatch, capsys):
    assert run_entrypoint(repo_root, "bin/nix-darwin-config", monkeypatch, ["--help"]) == 0
    assert "machine-local nix-darwin" in capsys.readouterr().out.casefold()


def test_shared_images_db_audit_help_is_available(repo_root: Path, monkeypatch, capsys):
    assert run_entrypoint(repo_root, "bin/shared-images-db-audit", monkeypatch, ["--help"]) == 0
    assert "shared-images" in capsys.readouterr().out


def test_shared_images_promote_original_help_is_available(repo_root: Path, monkeypatch, capsys):
    assert run_entrypoint(repo_root, "bin/shared-images-promote-original", monkeypatch, ["--help"]) == 0
    assert "original" in capsys.readouterr().out.casefold()


def test_shared_images_refresh_artist_links_help_is_available(repo_root: Path, monkeypatch, capsys):
    assert run_entrypoint(repo_root, "bin/shared-images-refresh-artist-links", monkeypatch, ["--help"]) == 0
    assert "artist" in capsys.readouterr().out.casefold()


def test_shared_images_rehash_help_is_available(repo_root: Path, monkeypatch, capsys):
    assert run_entrypoint(repo_root, "bin/shared-images-rehash", monkeypatch, ["--help"]) == 0
    assert "sha256" in capsys.readouterr().out


def test_shared_images_workflow_layout_help_is_available(repo_root: Path, monkeypatch, capsys):
    assert run_entrypoint(repo_root, "bin/shared-images-workflow-layout", monkeypatch, ["--help"]) == 0
    assert "workflow" in capsys.readouterr().out.casefold()
