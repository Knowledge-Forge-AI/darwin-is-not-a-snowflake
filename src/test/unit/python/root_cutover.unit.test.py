# nix-darwin-test-source-target: libexec/nix_darwin_local/root_cutover.py
from __future__ import annotations

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "libexec"))
from nix_darwin_local import root_cutover


def test_main_refuses_operational_invocation_without_args(capsys):
    result = root_cutover.main([])
    assert result == 1
    captured = capsys.readouterr()
    assert "dinas-root-cutover is retired" in captured.err
    assert "operational cutover is refused" in captured.err


def test_main_refuses_operational_invocation_with_default_sys_argv(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["dinas-root-cutover"])
    result = root_cutover.main()
    assert result == 1
    captured = capsys.readouterr()
    assert "dinas-root-cutover is retired" in captured.err


def test_main_refuses_former_arguments(tmp_path: Path, capsys):
    home = tmp_path / "home"
    home.mkdir()
    old_path = home / ".flakes"
    old_path.mkdir()
    new_path = home / ".local/nix-darwin/darwin-is-not-a-snowflake"
    cfg = home / "config.toml"
    cfg.write_text("[sources.core]\ncheckout = '$HOME/.flakes'\n", encoding="utf-8")
    cfg_bytes = cfg.read_bytes()
    journal = home / "journal.json"
    nav = home / "link"

    former_argv = [
        "--commit", "a" * 40,
        "--home", str(home),
        "--old-path", str(old_path),
        "--new-path", str(new_path),
        "--config", str(cfg),
        "--journal-path", str(journal),
        "--navigation-symlink", str(nav),
        "--dry-run",
    ]

    result = root_cutover.main(former_argv)
    assert result == 1

    captured = capsys.readouterr()
    assert "dinas-root-cutover is retired" in captured.err

    # Prove former arguments cannot recreate filesystem state or mutate existing state
    assert not new_path.exists()
    assert not journal.exists()
    assert not (home / "journal.json.lock").exists()
    assert not nav.exists()
    assert cfg.read_bytes() == cfg_bytes
    assert old_path.is_dir()


def test_main_refuses_positional_commit(tmp_path: Path, capsys):
    result = root_cutover.main(["b" * 40])
    assert result == 1
    captured = capsys.readouterr()
    assert "dinas-root-cutover is retired" in captured.err


def test_main_help_flag_displays_diagnostic_and_exits_zero(capsys):
    result_long = root_cutover.main(["--help"])
    assert result_long == 0
    captured_long = capsys.readouterr()
    assert "dinas-root-cutover" in captured_long.out
    assert "retired" in captured_long.out.lower()

    result_short = root_cutover.main(["-h"])
    assert result_short == 0
    captured_short = capsys.readouterr()
    assert "dinas-root-cutover" in captured_short.out


def test_main_help_flag_with_sys_argv(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["dinas-root-cutover", "--help"])
    result = root_cutover.main()
    assert result == 0
    captured = capsys.readouterr()
    assert "dinas-root-cutover" in captured.out


def test_parse_args_help(capsys):
    with pytest.raises(SystemExit) as exc:
        root_cutover.parse_args(["--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "dinas-root-cutover" in captured.out


def test_parse_args_empty():
    ns = root_cutover.parse_args([])
    assert ns is not None


def test_no_callable_migration_helpers_remain():
    assert not hasattr(root_cutover, "execute_cutover")
    assert not hasattr(root_cutover, "_execute_cutover")
    assert not hasattr(root_cutover, "CutoverPaths")


@pytest.mark.parametrize("args", [["--help", "--dry-run"], ["--commit", "a" * 40, "-h"]])
def test_help_does_not_mask_retired_operational_arguments(capsys, args):
    from nix_darwin_local import root_cutover
    assert root_cutover.main(args) == 1
    assert "retired" in capsys.readouterr().err
