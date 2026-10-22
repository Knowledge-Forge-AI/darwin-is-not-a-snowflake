# nix-darwin-test-source-target: bin/codex-git-mcp
# nix-darwin-test-source-target: libexec/codex-git-mcp/launcher.py
# nix-darwin-test-source-target: libexec/codex-git-mcp/server.py
from __future__ import annotations

from pathlib import Path

import pytest


def load_git_mcp(load_module):
    return load_module("libexec/codex-git-mcp/server.py")


def load_launcher(load_module, monkeypatch, repo_root: Path):
    monkeypatch.syspath_prepend(str(repo_root / "libexec" / "codex-git-mcp"))
    return load_module("libexec/codex-git-mcp/launcher.py")


def test_validate_repo_path_allows_exact_roots_and_descendants(load_module, tmp_path: Path):
    module = load_git_mcp(load_module)
    allowed = tmp_path / "approved"
    nested = allowed / "nested"
    nested.mkdir(parents=True)

    module.validate_repo_path(allowed, [allowed])
    module.validate_repo_path(nested, [allowed])


def test_validate_repo_path_rejects_outside_paths(load_module, tmp_path: Path):
    module = load_git_mcp(load_module)
    allowed = tmp_path / "approved"
    outside = tmp_path / "elsewhere"
    allowed.mkdir()
    outside.mkdir()

    with pytest.raises(ValueError, match="outside the approved Git MCP repositories"):
        module.validate_repo_path(outside, [allowed])


def test_validate_repo_path_rejects_empty_allowlist(load_module, tmp_path: Path):
    module = load_git_mcp(load_module)

    with pytest.raises(ValueError, match="at least one approved repository"):
        module.validate_repo_path(tmp_path, [])


def test_install_upstream_validator_patch_ignores_original_single_repo_argument(
    load_module,
    tmp_path: Path,
):
    module = load_git_mcp(load_module)
    allowed = tmp_path / "approved"
    blocked = tmp_path / "blocked"
    allowed.mkdir()
    blocked.mkdir()

    class Upstream:
        pass

    upstream = Upstream()
    module.install_upstream_validator_patch(upstream, [allowed])

    upstream.validate_repo_path(allowed, blocked)
    with pytest.raises(ValueError):
        upstream.validate_repo_path(blocked, allowed)


def test_build_uvx_command_runs_server_inside_mcp_server_git_environment(
    load_module,
    tmp_path: Path,
):
    module = load_git_mcp(load_module)
    server_path = tmp_path / "server.py"

    command = module.build_uvx_command(server_path, ["--allow", "/repo"])

    assert command == [
        "uvx",
        "--from",
        "mcp-server-git",
        "--with",
        "mcp<2",
        "python",
        str(server_path),
        "--allow",
        "/repo",
    ]


def test_main_requires_at_least_one_allow_argument(load_module, capsys):
    module = load_git_mcp(load_module)

    with pytest.raises(SystemExit) as error:
        module.main([])

    assert error.value.code == 2
    assert "--allow" in capsys.readouterr().err


def test_launcher_execs_uvx_command(load_module, monkeypatch, repo_root: Path):
    module = load_launcher(load_module, monkeypatch, repo_root)
    captured: dict[str, object] = {}

    def fake_execvp(file_name: str, args: list[str]) -> None:
        captured["file"] = file_name
        captured["args"] = args

    assert module.main(["--allow", "/repo"], execvp=fake_execvp) == 127
    assert captured["file"] == "uvx"
    args = captured["args"]
    assert isinstance(args, list)
    assert args[:6] == ["uvx", "--from", "mcp-server-git", "--with", "mcp<2", "python"]
    assert args[-2:] == ["--allow", "/repo"]
