# nix-darwin-test-source-target: libexec/nix_darwin_local/launcher.py
from __future__ import annotations

import importlib
import json
from pathlib import Path
import re
import shlex
import subprocess

import pytest


GUIDE = Path(__file__).parents[4] / "docs/ops/domain-change-deployment.md"
TEXT = GUIDE.read_text(encoding="utf-8")
BASH_BLOCKS = re.findall(r"```bash\n(.*?)```", TEXT, re.DOTALL)


def documented_nix_update_commands() -> list[str]:
    commands: list[str] = []
    for block in BASH_BLOCKS:
        lines = block.splitlines()
        index = 0
        while index < len(lines):
            if '"$NIX_UPDATE"' not in lines[index]:
                index += 1
                continue
            parts = [lines[index]]
            while parts[-1].rstrip().endswith("\\"):
                index += 1
                parts.append(lines[index])
            commands.append("\n".join(parts))
            index += 1
    return commands


def documented_nix_update_argv() -> list[list[str]]:
    parsed: list[list[str]] = []
    for command in documented_nix_update_commands():
        tokens = shlex.split(command.replace("\\\n", " "))
        assert tokens[0] == "$NIX_UPDATE"
        argv: list[str] = []
        for token in tokens[1:]:
            if re.match(r"\d?>", token):
                continue
            if token == "EXACT-OWNED-NODE":
                argv.append("security-nd/nixpkgs")
            elif token.startswith("$"):
                replacement = (
                    "0123456789abcdef0123456789abcdef01234567"
                    if argv and argv[-1] == "--revision"
                    else "/tmp/documented-value"
                )
                argv.append(replacement)
            else:
                argv.append(token)
        parsed.append(argv)
    return parsed


@pytest.fixture
def launcher_module(repo_root: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    return importlib.import_module("nix_darwin_local.launcher")


@pytest.fixture
def parser(launcher_module):
    return launcher_module._parser()


@pytest.mark.parametrize(
    "argv",
    [
        ["deploy", "--flake", "/tmp/private", "--dry-run"],
        ["deploy", "--flake", "/tmp/private", "--plan-only", "--output", "/tmp/local-plan"],
        ["build", "--flake", "/tmp/private", "--timing"],
        [
            "advanced", "prepare", "--flake", "/tmp/private", "--contract", "/tmp/contract.json",
            "--output", "/tmp/preflight", "--preflight-only",
        ],
        [
            "advanced", "prepare", "--flake", "/tmp/private", "--contract", "/tmp/contract.json",
            "--output", "/tmp/plan",
        ],
        ["advanced", "apply", "/tmp/plan"],
        [
            "lock", "--flake", "/tmp/private", "--mode", "targeted", "--input",
            "security-nd", "--output", "/tmp/lock-review",
        ],
        [
            "lock", "--flake", "/tmp/private", "--mode", "targeted", "--input",
            "security-nd", "--output", "/tmp/lock-install", "--install",
        ],
        [
            "promote", "prepare", "--flake", "/tmp/public", "--input",
            "developer-ux-nd", "--revision",
            "0123456789abcdef0123456789abcdef01234567", "--output", "/tmp/public-plan",
        ],
        ["promote", "apply", "/tmp/public-plan"],
        [
            "promote", "prepare", "--flake", "/tmp/private", "--input",
            "composition-nd", "--revision",
            "0123456789abcdef0123456789abcdef01234567", "--output", "/tmp/private-plan",
        ],
    ],
)
def test_every_documented_nix_update_shape_parses(parser, argv: list[str]) -> None:
    parser.parse_args(argv)


def test_every_extracted_nix_update_command_parses(parser, launcher_module) -> None:
    commands = documented_nix_update_argv()
    assert commands
    for argv in commands:
        parser.parse_args(launcher_module._migrate_legacy_argv(argv))


def test_every_shown_nix_update_subcommand_and_option_is_current(parser) -> None:
    help_text = parser.format_help()
    for command in ("deploy", "build", "lock", "promote", "optimize", "advanced"):
        assert command in help_text
    option_tokens = set(re.findall(
        r"(?<![A-Za-z0-9_])--[a-z][a-z0-9-]*",
        "\n".join(documented_nix_update_commands()),
    ))
    current = {
        "--flake", "--timing", "--contract", "--output", "--preflight-only",
        "--mode", "--input", "--allow-node", "--install", "--revision",
        "--dry-run", "--plan-only", "--plan",
    }
    assert option_tokens <= current


def test_ordinary_deployment_leads_with_non_mutating_then_attended_commands() -> None:
    ordinary_heading = TEXT.index("## Ordinary local deployment")
    ownership_heading = TEXT.index("## Choose the owning path first")
    commands = "```bash\nnix-update --dry-run\nnix-update\n```"
    assert ordinary_heading < ownership_heading
    assert ordinary_heading < TEXT.index(commands) < ownership_heading
    assert "Plans are optional for this ordinary lane" in TEXT
    assert "Contracts are exceptional" in TEXT


def test_evaluation_examples_cannot_rewrite_locks() -> None:
    assert (
        "nix flake check \\\n"
        "     --no-update-lock-file --no-write-lock-file"
    ) in TEXT
    assert (
        "nix eval --no-update-lock-file --no-write-lock-file --raw --impure --expr"
    ) in TEXT
    assert "--allow-node EXACT-OWNED-NODE \\\n" in TEXT


def test_registry_topology_matches_examples() -> None:
    local_root = Path.home() / ".local/nix-darwin"
    public_path = local_root / "composition-nd/registry.json"
    private_path = local_root / "composition-private-nd/registry.json"
    if not public_path.is_file() or not private_path.is_file():
        pytest.skip("local composition registries are not installed")
    public = json.loads(public_path.read_text(encoding="utf-8"))["modules"]
    private = json.loads(private_path.read_text(encoding="utf-8"))["modules"]
    assert any(row["input"] == "developer-ux-nd" for row in public)
    matches = [row for row in private if row["input"] == "security-nd"]
    assert len(matches) == 1 and matches[0]["url"] == "local-only"


def test_shell_blocks_are_syntax_valid_after_placeholder_substitution() -> None:
    assert BASH_BLOCKS
    for block in BASH_BLOCKS:
        substituted = block.replace("/ABSOLUTE/", "/tmp/")
        completed = subprocess.run(
            ["bash", "-n"], input=substituted, text=True, capture_output=True, check=False
        )
        assert completed.returncode == 0, completed.stderr


def test_examples_do_not_execute_prohibited_shortcuts() -> None:
    code = "\n".join(BASH_BLOCKS)
    assert "nix flake update" not in code
    assert "darwin-rebuild switch" not in code
    assert "--switch-only" not in code
    assert "git checkout --" not in code
    assert "APPLY NIX-UPDATE" not in code
    assert "APPLY NIX-UPDATE PROMOTION <exact-64-hex-plan-digest>" in TEXT
    assert "APPLY NIX-UPDATE PLAN <exact-64-hex-plan-digest>" in TEXT


def test_ownership_and_secret_boundaries_are_explicit() -> None:
    assert "Agent-Central commit; no flake promotion" in TEXT
    assert '`url: "local-only"`' in TEXT
    assert 'package = "proton-pass";' in TEXT
    assert 'bundle:me.proton.pass.electron' in TEXT
    assert "--input developer-ux-nd" in TEXT
    assert "--input composition-nd" in TEXT
    assert "GOOGLE_APPLICATION_CREDENTIALS" in TEXT
    assert "never activates the host" in TEXT
