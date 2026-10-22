from __future__ import annotations

import json
from pathlib import Path

import pytest


def load_allowlist(load_module):
    return load_module("libexec/codex-env/allowlist.py")


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


def test_valid_allowlist_parses_rows_and_validates_defaults(load_module, tmp_path: Path):
    module = load_allowlist(load_module)
    allowlist = write_allowlist(
        tmp_path / "allowlist.tsv",
        [
            "PATH\tpath_list\t8192\ttrue\t/usr/bin:/bin\tExecutable search path",
            "JAVA_HOME\tpath\t1024\tfalse\t/Library/Java/Home\tJDK root",
            "POSTGRES_PORT\tport\t5\tfalse\t5432\tPostgres port",
            "SPRING_PROFILES_ACTIVE\ttoken_list\t512\tfalse\tdev,test\tSpring profiles",
        ],
    )

    result = module.check_allowlist_file(allowlist)

    assert result.ok
    assert [row.name for row in result.rows] == [
        "PATH",
        "JAVA_HOME",
        "POSTGRES_PORT",
        "SPRING_PROFILES_ACTIVE",
    ]
    assert result.errors == []


def test_allowlist_rejects_wildcard_names_duplicates_and_unknown_validators(
    load_module,
    tmp_path: Path,
):
    module = load_allowlist(load_module)
    allowlist = write_allowlist(
        tmp_path / "allowlist.tsv",
        [
            "DOCKER_*\traw_safe\t1024\tfalse\t\tWildcard should fail",
            "PATH\tpath_list\t8192\ttrue\t/usr/bin\tFirst path",
            "PATH\tpath_list\t8192\tfalse\t/bin\tDuplicate path",
            "PROJECT_MODE\tmystery\t128\tfalse\tdev\tUnknown validator",
        ],
    )

    result = module.check_allowlist_file(allowlist)

    assert not result.ok
    messages = "\n".join(error.message for error in result.errors)
    assert "wildcard characters are not allowed" in messages
    assert "duplicate allowlist name" in messages
    assert "unknown validator" in messages


def test_allowlist_rejects_malformed_limits_booleans_and_defaults(load_module, tmp_path: Path):
    module = load_allowlist(load_module)
    allowlist = write_allowlist(
        tmp_path / "allowlist.tsv",
        [
            "TERM\ttoken\t0\tfalse\txterm-256color\tInvalid zero max",
            "DEBUG\tbool\t16\tmaybe\ttrue\tInvalid required flag",
            "REDIS_URL\turi\t32\tfalse\tnot a uri\tInvalid URI default",
            "BAD_SENTINEL\ttoken\t32\tfalse\tnUlL\tCase-insensitive denial",
        ],
    )

    result = module.check_allowlist_file(allowlist)

    assert not result.ok
    messages = "\n".join(error.message for error in result.errors)
    assert "max_bytes must be a positive integer" in messages
    assert "required must be true or false" in messages
    assert "default failed uri validation" in messages
    assert "default value is denied" in messages


def test_allowlist_rejects_control_characters_and_large_defaults(load_module, tmp_path: Path):
    module = load_allowlist(load_module)
    allowlist = write_allowlist(
        tmp_path / "allowlist.tsv",
        [
            "EDITOR\tcommand\t4\tfalse\tvim -u NONE\tToo large",
            "BROKEN\traw_safe\t128\tfalse\tcarriage\rreturn\tControl char",
        ],
    )

    result = module.check_allowlist_file(allowlist)

    assert not result.ok
    messages = "\n".join(error.message for error in result.errors)
    assert "default exceeds max_bytes" in messages
    assert "contains a banned control character" in messages


def test_cli_reports_text_and_json_results(load_module, tmp_path: Path, capsys):
    module = load_allowlist(load_module)
    valid = write_allowlist(
        tmp_path / "valid.tsv",
        ["PATH\tpath_list\t8192\ttrue\t/usr/bin:/bin\tExecutable search path"],
    )
    invalid = write_allowlist(
        tmp_path / "invalid.tsv",
        ["DOCKER_*\traw_safe\t1024\tfalse\t\tWildcard should fail"],
    )

    assert module.main([str(valid)]) == 0
    out = capsys.readouterr().out
    assert "ok" in out
    assert "1 entries" in out

    assert module.main(["--format", "json", str(invalid)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["files"][0]["errors"][0]["name"] == "DOCKER_*"


def test_repo_managed_default_allowlists_are_valid(load_module, capsys):
    module = load_allowlist(load_module)

    assert module.main([]) == 0
    out = capsys.readouterr().out

    assert "project-env-allowlist.tsv" in out
    assert "codex-sync-allowlist.tsv" in out


def test_allowlist_reports_missing_empty_and_bad_header(load_module, tmp_path: Path):
    module = load_allowlist(load_module)

    missing = module.check_allowlist_file(tmp_path / "missing.tsv")
    assert not missing.ok
    assert missing.errors[0].message == "allowlist file does not exist"

    empty_file = tmp_path / "empty.tsv"
    empty_file.write_text("\n\n", encoding="utf-8")
    empty = module.check_allowlist_file(empty_file)
    assert not empty.ok
    assert empty.errors[0].message == "allowlist file is empty"

    bad_header = tmp_path / "bad-header.tsv"
    bad_header.write_text("name\tvalidator\nPATH\tpath_list\n", encoding="utf-8")
    result = module.check_allowlist_file(bad_header)
    assert not result.ok
    assert result.errors[0].message.startswith("header must be")


def test_value_validators_accept_and_reject_representative_values(load_module):
    module = load_allowlist(load_module)

    assert module.validate_value("anything safe", "raw_safe", 128) is None
    assert module.validate_value("bad token!", "token", 128) == "must contain only token characters"
    assert module.validate_value("dev,,prod", "token_list", 128) == (
        "must contain only non-empty comma-separated tokens"
    )
    assert module.validate_value("/usr/bin::/bin", "path_list", 128) == (
        "must contain only non-empty path entries"
    )
    assert module.validate_value("redis://localhost:6379", "uri_or_path", 128) is None
    assert module.validate_value("/tmp/project", "uri_or_path", 128) is None
    assert module.validate_value("local host", "host", 128) == "must be a host-like value"
    assert module.validate_value("65536", "port", 5) == "must be between 1 and 65535"
    assert module.validate_value("five", "port", 5) == "must be a numeric port"
    assert module.validate_value("maybe", "bool", 128) == "must be a boolean"
    assert module.validate_value("12a", "integer", 128) == "must be an integer"
    assert module.validate_value("vim -u NONE", "command", 128) is None
    assert module.validate_value("x", "unknown", 128) == "unknown validator: unknown"


def test_name_validation_rejects_invalid_exact_names(load_module, tmp_path: Path):
    module = load_allowlist(load_module)
    allowlist = write_allowlist(
        tmp_path / "allowlist.tsv",
        [
            "1BAD\ttoken\t128\tfalse\tdev\tStarts with a digit",
            "BAD-NAME\ttoken\t128\tfalse\tdev\tContains a dash",
        ],
    )

    result = module.check_allowlist_file(allowlist)

    assert not result.ok
    messages = "\n".join(error.message for error in result.errors)
    assert messages.count("name must be an exact environment variable name") == 2
