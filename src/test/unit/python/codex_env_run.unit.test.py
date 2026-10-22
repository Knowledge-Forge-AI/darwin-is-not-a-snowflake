# nix-darwin-test-source-target: bin/env-run
# nix-darwin-test-source-target: libexec/codex-env/run.py
from __future__ import annotations

from pathlib import Path


def load_env_run(load_module):
    return load_module("libexec/codex-env/run.py")


def test_parse_env_file_accepts_exports_plain_assignments_and_quotes(load_module, tmp_path: Path):
    module = load_env_run(load_module)
    env_file = tmp_path / "project.env"
    env_file.write_text(
        "\n".join(
            [
                "# project env",
                "export PATH=/custom/bin:/usr/bin",
                "EDITOR='vim -u NONE'",
                'PROJECT_NAME="repo map"',
                "EMPTY_VALUE=",
                "",
            ],
        ),
        encoding="utf-8",
    )

    parsed = module.parse_env_file(env_file)

    assert parsed == {
        "PATH": "/custom/bin:/usr/bin",
        "EDITOR": "vim -u NONE",
        "PROJECT_NAME": "repo map",
        "EMPTY_VALUE": "",
    }


def test_parse_env_file_rejects_shell_source_constructs_and_bad_names(load_module, tmp_path: Path):
    module = load_env_run(load_module)
    env_file = tmp_path / "bad.env"
    env_file.write_text(
        "\n".join(
            [
                "export VALID=/usr/bin",
                "unset VALID",
                "1BAD=value",
            ],
        ),
        encoding="utf-8",
    )

    result = module.parse_env_file_result(env_file)

    assert not result.ok
    assert result.values == {"VALID": "/usr/bin"}
    messages = "\n".join(problem.message for problem in result.problems)
    assert "unsupported env line" in messages
    assert "invalid environment variable name" in messages


def test_default_env_file_uses_latest_zsh_snapshot(load_module, tmp_path: Path):
    module = load_env_run(load_module)

    assert module.default_env_file({"HOME": str(tmp_path)}) == (
        tmp_path / ".codex" / "runtime" / "shell-env" / "latest.zsh.env"
    )


def test_main_merges_default_env_file_and_execs_command(load_module, monkeypatch, tmp_path: Path, capsys):
    module = load_env_run(load_module)
    env_file = tmp_path / ".codex" / "runtime" / "shell-env" / "latest.zsh.env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text(
        "export PATH=/managed/bin:/usr/bin\n"
        "export PYTHONPYCACHEPREFIX=/Users/operator/.cache/python/__pycache__\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_execvpe(file_name: str, args: list[str], env: dict[str, str]) -> None:
        captured["file"] = file_name
        captured["args"] = args
        captured["env"] = env
        raise module.ExecReplaced()

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    assert module.main(["--", "nix-test", "--report"], execvpe=fake_execvpe) == 0

    assert captured["file"] == "nix-test"
    assert captured["args"] == ["nix-test", "--report"]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["PATH"] == "/managed/bin:/usr/bin"
    assert env["PYTHONPYCACHEPREFIX"] == "/Users/operator/.cache/python/__pycache__"
    assert capsys.readouterr().err == ""


def test_main_uses_explicit_env_file(load_module, monkeypatch, tmp_path: Path):
    module = load_env_run(load_module)
    env_file = tmp_path / "custom.env"
    env_file.write_text("TOOL_MODE=test\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_execvpe(file_name: str, args: list[str], env: dict[str, str]) -> None:
        captured["env"] = env
        raise module.ExecReplaced()

    monkeypatch.setenv("HOME", str(tmp_path))

    assert module.main(["--env-file", str(env_file), "--", "python", "-V"], execvpe=fake_execvpe) == 0

    env = captured["env"]
    assert isinstance(env, dict)
    assert env["TOOL_MODE"] == "test"


def test_main_reports_missing_file_bad_env_and_missing_command(load_module, tmp_path: Path, capsys):
    module = load_env_run(load_module)

    assert module.main(["--env-file", str(tmp_path / "missing.env"), "--", "true"]) == 1
    assert "env file does not exist" in capsys.readouterr().err

    bad_env = tmp_path / "bad.env"
    bad_env.write_text("export BAD-NAME=value\n", encoding="utf-8")
    assert module.main(["--env-file", str(bad_env), "--", "true"]) == 1
    assert "invalid environment variable name" in capsys.readouterr().err

    good_env = tmp_path / "good.env"
    good_env.write_text("PATH=/usr/bin:/bin\n", encoding="utf-8")
    assert module.main(["--env-file", str(good_env)]) == 2
    assert "command is required" in capsys.readouterr().err
