from __future__ import annotations

import json
import stat
from pathlib import Path


def load_snapshot(load_module):
    load_module("libexec/codex-env/allowlist.py", "allowlist")
    return load_module("libexec/codex-env/snapshot.py")


def write_allowlist(path: Path, rows: list[str]) -> Path:
    path.write_text(
        "\n".join(
            [
                "name\tvalidator\tmax_bytes\trequired\tdefault\tdescription",
                *rows,
                "",
            ],
        ),
        encoding="utf-8",
    )
    return path


def test_sync_snapshot_writes_private_exports_and_metadata(load_module, tmp_path: Path):
    module = load_snapshot(load_module)
    allowlist = write_allowlist(
        tmp_path / "allowlist.tsv",
        [
            "PATH\tpath_list\t8192\ttrue\t/usr/bin:/bin\tExecutable search path",
            "PYTHONPYCACHEPREFIX\tpath\t2048\tfalse\t\tPython bytecode cache prefix",
            "EDITOR\tcommand\t512\tfalse\t\tDefault editor",
        ],
    )
    output_path = tmp_path / "runtime" / "shell-env" / "latest.zsh.env"

    result = module.write_snapshot(
        allowlist_path=allowlist,
        output_path=output_path,
        shell="zsh",
        context="global",
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPYCACHEPREFIX": "/Users/operator/.cache/python/__pycache__",
            "EDITOR": "vim -u NONE",
            "EXA_API_KEY": "do-not-capture",
        },
        write_metadata=True,
    )

    assert result.ok
    assert result.changed
    assert result.entries == ["PATH", "PYTHONPYCACHEPREFIX", "EDITOR"]
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(output_path.parent.stat().st_mode) == 0o700

    text = output_path.read_text(encoding="utf-8")
    assert "# context: global" in text
    assert "# shell: zsh" in text
    assert "export PATH=/usr/bin:/bin" in text
    assert "export PYTHONPYCACHEPREFIX=/Users/operator/.cache/python/__pycache__" in text
    assert "export EDITOR='vim -u NONE'" in text
    assert "EXA_API_KEY" not in text

    metadata = json.loads((tmp_path / "runtime" / "shell-env" / "latest.zsh.env.metadata.json").read_text())
    assert metadata["mode"] == "sync"
    assert metadata["shell"] == "zsh"
    assert metadata["context"] == "global"
    assert metadata["entries"] == ["PATH", "PYTHONPYCACHEPREFIX", "EDITOR"]
    assert metadata["rejected"] == []
    assert metadata["skipped_missing"] == []


def test_snapshot_reports_required_missing_and_rejected_values(load_module, tmp_path: Path):
    module = load_snapshot(load_module)
    allowlist = write_allowlist(
        tmp_path / "allowlist.tsv",
        [
            "PATH\tpath_list\t8192\ttrue\t/usr/bin:/bin\tExecutable search path",
            "TERM\ttoken\t32\tfalse\txterm-256color\tTerminal type",
            "PAGER\tcommand\t8\tfalse\tless\tPager",
        ],
    )

    result = module.write_snapshot(
        allowlist_path=allowlist,
        output_path=tmp_path / "latest.bash.env",
        shell="bash",
        context="global",
        env={
            "TERM": "xterm\n256color",
            "PAGER": "a command that is too large",
        },
        write_metadata=False,
    )

    assert not result.ok
    assert not (tmp_path / "latest.bash.env").exists()
    assert [problem.name for problem in result.problems] == ["PATH", "TERM", "PAGER"]
    assert "required environment variable is missing" in result.problems[0].message
    assert "banned control character" in result.problems[1].message
    assert "exceeds max_bytes" in result.problems[2].message


def test_snapshot_skips_empty_optional_values_but_rejects_empty_required_values(
    load_module,
    tmp_path: Path,
):
    module = load_snapshot(load_module)
    allowlist = write_allowlist(
        tmp_path / "allowlist.tsv",
        [
            "PATH\tpath_list\t8192\ttrue\t/usr/bin:/bin\tExecutable search path",
            "TERM\ttoken\t32\tfalse\txterm-256color\tTerminal type",
        ],
    )

    optional_empty = module.write_snapshot(
        allowlist_path=allowlist,
        output_path=tmp_path / "optional.env",
        shell="zsh",
        context="global",
        env={"PATH": "/usr/bin:/bin", "TERM": ""},
        write_metadata=False,
    )

    assert optional_empty.ok
    assert optional_empty.entries == ["PATH"]
    assert optional_empty.skipped_missing == ["TERM"]

    required_empty = module.write_snapshot(
        allowlist_path=allowlist,
        output_path=tmp_path / "required.env",
        shell="zsh",
        context="global",
        env={"PATH": "", "TERM": "xterm-256color"},
        write_metadata=False,
    )

    assert not required_empty.ok
    assert required_empty.problems[0].name == "PATH"
    assert "required environment variable is missing" in required_empty.problems[0].message


def test_snapshot_does_not_rewrite_unchanged_files(load_module, tmp_path: Path):
    module = load_snapshot(load_module)
    allowlist = write_allowlist(
        tmp_path / "allowlist.tsv",
        ["PATH\tpath_list\t8192\ttrue\t/usr/bin:/bin\tExecutable search path"],
    )
    output_path = tmp_path / "latest.zsh.env"

    first = module.write_snapshot(
        allowlist_path=allowlist,
        output_path=output_path,
        shell="zsh",
        context="global",
        env={"PATH": "/usr/bin:/bin"},
        write_metadata=True,
    )
    second = module.write_snapshot(
        allowlist_path=allowlist,
        output_path=output_path,
        shell="zsh",
        context="global",
        env={"PATH": "/usr/bin:/bin"},
        write_metadata=True,
    )

    assert first.changed
    assert not second.changed


def test_snapshot_helpers_detect_shells_and_default_paths(load_module, tmp_path: Path):
    module = load_snapshot(load_module)

    assert module.detect_shell({"ZSH_VERSION": "5.9", "SHELL": "/bin/bash"}) == "zsh"
    assert module.detect_shell({"BASH_VERSION": "5.3", "SHELL": "/bin/zsh"}) == "bash"
    assert module.detect_shell({"SHELL": "/bin/zsh"}) == "zsh"
    assert module.detect_shell({"SHELL": "/bin/fish"}) is None

    assert module.default_allowlist_path("sync").name == "codex-sync-allowlist.tsv"
    assert module.default_allowlist_path("env-file").name == "project-env-allowlist.tsv"
    assert module.default_output_path("bash", {"HOME": str(tmp_path)}) == (
        tmp_path / ".codex" / "runtime" / "shell-env" / "latest.bash.env"
    )


def test_cli_sync_defaults_to_current_shell_snapshot_path(load_module, monkeypatch, tmp_path: Path):
    module = load_snapshot(load_module)
    allowlist = write_allowlist(
        tmp_path / "allowlist.tsv",
        ["PATH\tpath_list\t8192\ttrue\t/usr/bin:/bin\tExecutable search path"],
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASH_VERSION", "5.3")
    monkeypatch.delenv("ZSH_VERSION", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    assert module.main(["--mode", "sync", "--allowlist", str(allowlist)]) == 0

    output_path = tmp_path / ".codex" / "runtime" / "shell-env" / "latest.bash.env"
    assert output_path.exists()
    assert "export PATH=/usr/bin:/bin" in output_path.read_text(encoding="utf-8")


def test_cli_reports_env_file_argument_errors(load_module, monkeypatch, tmp_path: Path, capsys):
    module = load_snapshot(load_module)
    allowlist = write_allowlist(
        tmp_path / "allowlist.tsv",
        ["PATH\tpath_list\t8192\ttrue\t/usr/bin:/bin\tExecutable search path"],
    )
    monkeypatch.setenv("BASH_VERSION", "5.3")

    assert module.main(["--mode", "env-file", "--allowlist", str(allowlist)]) == 1
    assert "--context is required" in capsys.readouterr().err

    assert module.main(
        ["--mode", "env-file", "--context", "demo", "--allowlist", str(allowlist)],
    ) == 1
    assert "--output is required" in capsys.readouterr().err


def test_cli_reports_snapshot_validation_errors(load_module, monkeypatch, tmp_path: Path, capsys):
    module = load_snapshot(load_module)
    allowlist = write_allowlist(
        tmp_path / "allowlist.tsv",
        ["PATH\tpath_list\t8192\ttrue\t/usr/bin:/bin\tExecutable search path"],
    )
    monkeypatch.setenv("BASH_VERSION", "5.3")
    monkeypatch.delenv("PATH", raising=False)

    assert module.main(
        [
            "--mode",
            "sync",
            "--allowlist",
            str(allowlist),
            "--output",
            str(tmp_path / "snapshot.env"),
        ],
    ) == 1
    assert "PATH: required environment variable is missing" in capsys.readouterr().err


def test_cli_reports_invalid_shell_detection(load_module, monkeypatch, tmp_path: Path, capsys):
    module = load_snapshot(load_module)
    allowlist = write_allowlist(
        tmp_path / "allowlist.tsv",
        ["PATH\tpath_list\t8192\ttrue\t/usr/bin:/bin\tExecutable search path"],
    )
    monkeypatch.delenv("BASH_VERSION", raising=False)
    monkeypatch.delenv("ZSH_VERSION", raising=False)
    monkeypatch.setenv("SHELL", "/bin/fish")

    assert module.main(["--mode", "sync", "--allowlist", str(allowlist)]) == 1
    assert "unable to detect current shell" in capsys.readouterr().err


def test_invalid_allowlist_is_reported_as_snapshot_problem(load_module, tmp_path: Path):
    module = load_snapshot(load_module)
    bad_allowlist = tmp_path / "bad.tsv"
    bad_allowlist.write_text("name\tvalidator\nPATH\tpath_list\n", encoding="utf-8")

    result = module.write_snapshot(
        allowlist_path=bad_allowlist,
        output_path=tmp_path / "snapshot.env",
        shell="bash",
        context="global",
        env={"PATH": "/usr/bin:/bin"},
        write_metadata=True,
    )

    assert not result.ok
    assert result.problems[0].name == "<header>"
    assert "header must be" in result.problems[0].message
