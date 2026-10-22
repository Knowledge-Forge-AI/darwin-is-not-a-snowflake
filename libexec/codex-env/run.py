from __future__ import annotations

import argparse
import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


Execvpe = Callable[[str, list[str], dict[str, str]], None]
NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ExecReplaced(Exception):
    """Test sentinel raised by fake exec functions."""


@dataclass(frozen=True)
class EnvProblem:
    line: int
    name: str
    message: str


@dataclass(frozen=True)
class EnvParseResult:
    ok: bool
    values: dict[str, str]
    problems: list[EnvProblem]


class EnvFileError(ValueError):
    def __init__(self, problems: list[EnvProblem]) -> None:
        super().__init__("\n".join(problem.message for problem in problems))
        self.problems = problems


def default_env_file(env: Mapping[str, str] | None = None) -> Path:
    runtime_env = os.environ if env is None else env
    home = Path(runtime_env.get("HOME", str(Path.home())))
    return home / ".codex" / "runtime" / "shell-env" / "latest.zsh.env"


def parse_env_file(path: str | Path) -> dict[str, str]:
    result = parse_env_file_result(path)
    if not result.ok:
        raise EnvFileError(result.problems)
    return result.values


def parse_env_file_result(path: str | Path) -> EnvParseResult:
    env_path = Path(path)
    values: dict[str, str] = {}
    problems: list[EnvProblem] = []

    try:
        raw_text = env_path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return EnvParseResult(
            ok=False,
            values={},
            problems=[
                EnvProblem(0, "<file>", f"env file does not exist: {env_path}"),
            ],
        )

    if "\0" in raw_text:
        problems.append(EnvProblem(0, "<file>", "env file contains a banned NUL byte"))
        raw_text = raw_text.replace("\0", "")

    normalized = raw_text.replace("\r\n", "\n")
    if "\r" in normalized:
        problems.append(EnvProblem(0, "<file>", "env file contains a banned carriage return"))
        normalized = normalized.replace("\r", "\n")

    for line_number, line in enumerate(normalized.split("\n"), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        assignment = _parse_assignment(stripped)
        if assignment is None:
            problems.append(EnvProblem(line_number, "<line>", "unsupported env line"))
            continue

        name, value = assignment
        if not NAME_PATTERN.fullmatch(name):
            problems.append(EnvProblem(line_number, name, "invalid environment variable name"))
            continue

        values[name] = value

    return EnvParseResult(ok=not problems, values=values, problems=problems)


def main(argv: list[str] | None = None, *, execvpe: Execvpe = os.execvpe) -> int:
    parser = argparse.ArgumentParser(
        description="Run a command with variables imported from a parser-safe .env file.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Environment file to import. Defaults to ~/.codex/runtime/shell-env/latest.zsh.env.",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    command = _normalize_command(args.command)
    if not command:
        print("env-run: command is required", file=sys.stderr)
        return 2

    env_file = args.env_file or default_env_file(os.environ)
    result = parse_env_file_result(env_file)
    if not result.ok:
        for problem in result.problems:
            location = f"{env_file}:{problem.line}" if problem.line else str(env_file)
            print(f"env-run: {location}: {problem.name}: {problem.message}", file=sys.stderr)
        return 1

    child_env = dict(os.environ)
    child_env.update(result.values)

    try:
        execvpe(command[0], command, child_env)
    except ExecReplaced:
        return 0
    return 0


def _normalize_command(command: list[str]) -> list[str]:
    if command and command[0] == "--":
        return command[1:]
    return command


def _parse_assignment(line: str) -> tuple[str, str] | None:
    try:
        words = shlex.split(line, posix=True)
    except ValueError:
        return None

    if len(words) == 1:
        return _split_assignment(words[0])

    if len(words) == 2 and words[0] == "export":
        return _split_assignment(words[1])

    return None


def _split_assignment(word: str) -> tuple[str, str] | None:
    if "=" not in word:
        return None
    name, value = word.split("=", 1)
    if not name:
        return None
    return name, value


if __name__ == "__main__":
    raise SystemExit(main())
