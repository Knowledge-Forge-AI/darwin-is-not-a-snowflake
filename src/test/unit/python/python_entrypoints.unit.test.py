# nix-darwin-test-source-target: bin/brave-youtube-cdp
# nix-darwin-test-source-target: bin/cnt-wallpaper-gallery
# nix-darwin-test-source-target: bin/codex-git-mcp
# nix-darwin-test-source-target: bin/env-allowlist-check
# nix-darwin-test-source-target: bin/env-run
# nix-darwin-test-source-target: bin/env-snapshot
# nix-darwin-test-source-target: bin/shared-images-db-audit
# nix-darwin-test-source-target: bin/shared-images-promote-original
# nix-darwin-test-source-target: bin/shared-images-refresh-artist-links
# nix-darwin-test-source-target: bin/shared-images-rehash
# nix-darwin-test-source-target: bin/shared-images-workflow-layout

from __future__ import annotations

import runpy
import sys
import types
from pathlib import Path

import pytest


def run_entrypoint(repo_root: Path, relative_path: str, monkeypatch, argv: list[str] | None = None):
    script_path = repo_root / relative_path
    monkeypatch.setattr(sys, "argv", [str(script_path), *(argv or [])])
    try:
        runpy.run_path(str(script_path), run_name="__main__")
    except SystemExit as error:
        return error.code
    return 0


def fake_main_module(monkeypatch, name: str, exit_code: int = 37) -> list[list[str]]:
    calls: list[list[str]] = []
    module = types.ModuleType(name)

    def main() -> int:
        calls.append(sys.argv[1:])
        return exit_code

    module.main = main
    monkeypatch.setitem(sys.modules, name, module)
    return calls


def fake_function_module(monkeypatch, name: str, function_name: str, exit_code: int = 37) -> list[list[str]]:
    calls: list[list[str]] = []
    module = types.ModuleType(name)

    def callback() -> int:
        calls.append(sys.argv[1:])
        return exit_code

    setattr(module, function_name, callback)
    monkeypatch.setitem(sys.modules, name, module)
    return calls


@pytest.mark.parametrize(
    ("relative_path", "module_name"),
    [
        ("bin/codex-git-mcp", "launcher"),
        ("bin/env-allowlist-check", "allowlist"),
        ("bin/env-run", "run"),
        ("bin/env-snapshot", "snapshot"),
        ("bin/shared-images-db-audit", "db_audit"),
        ("bin/shared-images-promote-original", "promote_original"),
        ("bin/shared-images-refresh-artist-links", "artist_links"),
        ("bin/shared-images-rehash", "rehash"),
        ("bin/shared-images-workflow-layout", "workflow_layout"),
    ],
)
def test_thin_python_entrypoints_delegate_to_libexec_main(
    repo_root: Path,
    monkeypatch,
    relative_path: str,
    module_name: str,
):
    calls = fake_main_module(monkeypatch, module_name)

    code = run_entrypoint(repo_root, relative_path, monkeypatch, ["--sentinel"])

    assert code == 37
    assert calls == [["--sentinel"]]



def test_brave_youtube_cdp_rejects_unknown_command(load_module, monkeypatch):
    module = load_module("bin/brave-youtube-cdp", "brave_youtube_cdp_entrypoint")
    monkeypatch.setattr(sys, "argv", ["brave-youtube-cdp", "bogus", "9222"])

    assert module.main() == 2


def test_cnt_wallpaper_gallery_normalizes_image_type_options(load_module):
    module = load_module("bin/cnt-wallpaper-gallery", "cnt_wallpaper_gallery_entrypoint")

    assert module.normalize_image_types("both") == {"wallpaper", "portrait"}
    assert module.normalize_image_types("portraits") == {"portrait"}
    assert module.image_type_label({"wallpaper", "portrait"}) == "both"
