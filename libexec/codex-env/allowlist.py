from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse


HEADER = ["name", "validator", "max_bytes", "required", "default", "description"]
KNOWN_VALIDATORS = {
    "bool",
    "command",
    "host",
    "integer",
    "path",
    "path_list",
    "port",
    "raw_safe",
    "token",
    "token_list",
    "uri",
    "uri_or_path",
}
NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
HOST_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
INTEGER_PATTERN = re.compile(r"^-?[0-9]+$")
WILDCARD_CHARS = set("*?[]{}")
DENIED_SENTINELS = {"null"}


@dataclass(frozen=True)
class AllowlistRow:
    name: str
    validator: str
    max_bytes: int
    required: bool
    default: str
    description: str
    line_number: int


@dataclass(frozen=True)
class AllowlistError:
    path: str
    line: int
    name: str
    message: str


@dataclass(frozen=True)
class AllowlistResult:
    path: Path
    ok: bool
    rows: list[AllowlistRow]
    errors: list[AllowlistError]


def default_allowlist_paths() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[2]
    config_dir = repo_root / "config" / "codex-env"
    return [
        config_dir / "project-env-allowlist.tsv",
        config_dir / "codex-sync-allowlist.tsv",
    ]


def check_allowlist_file(path: str | Path) -> AllowlistResult:
    allowlist_path = Path(path)
    rows: list[AllowlistRow] = []
    errors: list[AllowlistError] = []

    try:
        raw_text = allowlist_path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return AllowlistResult(
            path=allowlist_path,
            ok=False,
            rows=[],
            errors=[
                AllowlistError(
                    path=str(allowlist_path),
                    line=0,
                    name="<file>",
                    message="allowlist file does not exist",
                ),
            ],
        )

    if "\0" in raw_text:
        errors.append(
            _error(allowlist_path, 0, "<file>", "file contains a banned control character"),
        )

    without_crlf = raw_text.replace("\r\n", "\n")
    if "\r" in without_crlf:
        errors.append(
            _error(allowlist_path, 0, "<file>", "default contains a banned control character"),
        )
        without_crlf = without_crlf.replace("\r", "\n")

    lines = without_crlf.split("\n")
    header_line = _first_nonblank_line(lines)
    if header_line is None:
        errors.append(_error(allowlist_path, 0, "<file>", "allowlist file is empty"))
        return AllowlistResult(path=allowlist_path, ok=False, rows=[], errors=errors)

    header_number, header = header_line
    header_columns = header.split("\t")
    if header_columns != HEADER:
        errors.append(
            _error(
                allowlist_path,
                header_number,
                "<header>",
                f"header must be: {' tab '.join(HEADER)}",
            ),
        )
        return AllowlistResult(path=allowlist_path, ok=False, rows=[], errors=errors)

    seen_names: set[str] = set()
    for line_number, line in enumerate(lines[header_number:], start=header_number + 1):
        if not line.strip():
            continue

        columns = line.split("\t")
        if len(columns) != len(HEADER):
            errors.append(
                _error(
                    allowlist_path,
                    line_number,
                    columns[0] if columns and columns[0] else "<row>",
                    "row must contain exactly 6 tab-separated columns",
                ),
            )
            continue

        name, validator, max_bytes_text, required_text, default, description = columns
        parsed_max_bytes = _parse_max_bytes(
            allowlist_path,
            line_number,
            name,
            max_bytes_text,
            errors,
        )
        parsed_required = _parse_required(allowlist_path, line_number, name, required_text, errors)

        _validate_name(allowlist_path, line_number, name, seen_names, errors)
        if validator not in KNOWN_VALIDATORS:
            errors.append(
                _error(allowlist_path, line_number, name, f"unknown validator: {validator}"),
            )

        if parsed_max_bytes is not None and validator in KNOWN_VALIDATORS and default:
            validation_error = validate_value(default, validator, parsed_max_bytes)
            if validation_error is not None:
                errors.append(
                    _error(
                        allowlist_path,
                        line_number,
                        name,
                        f"default failed {validator} validation: {validation_error}",
                    ),
                )

        if parsed_max_bytes is not None and parsed_required is not None:
            rows.append(
                AllowlistRow(
                    name=name,
                    validator=validator,
                    max_bytes=parsed_max_bytes,
                    required=parsed_required,
                    default=default,
                    description=description,
                    line_number=line_number,
                ),
            )

    return AllowlistResult(path=allowlist_path, ok=not errors, rows=rows, errors=errors)


def validate_value(value: str, validator: str, max_bytes: int) -> str | None:
    common_error = _validate_common_value(value, max_bytes)
    if common_error is not None:
        return common_error

    if validator == "raw_safe":
        return None
    if validator == "token":
        return _validate_token(value)
    if validator == "token_list":
        return _validate_token_list(value)
    if validator == "path":
        return _validate_path(value)
    if validator == "path_list":
        return _validate_path_list(value)
    if validator == "uri":
        return _validate_uri(value)
    if validator == "uri_or_path":
        if _validate_uri(value) is None or _validate_path(value) is None:
            return None
        return "must be a URI or path"
    if validator == "host":
        return None if HOST_PATTERN.fullmatch(value) else "must be a host-like value"
    if validator == "port":
        return _validate_port(value)
    if validator == "bool":
        return (
            None
            if value.lower() in {"0", "1", "false", "no", "true", "yes"}
            else "must be a boolean"
        )
    if validator == "integer":
        return None if INTEGER_PATTERN.fullmatch(value) else "must be an integer"
    if validator == "command":
        return None
    return f"unknown validator: {validator}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate Codex environment allowlist TSV files.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Allowlist TSV files to check. Defaults to the repo-managed allowlists.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="Output format.",
    )
    args = parser.parse_args(argv)

    paths = args.paths if args.paths else default_allowlist_paths()
    results = [check_allowlist_file(path) for path in paths]

    if args.format == "json":
        print(json.dumps(_json_payload(results), indent=2, sort_keys=True))
    else:
        _print_text_results(results)

    return 0 if all(result.ok for result in results) else 1


def _first_nonblank_line(lines: list[str]) -> tuple[int, str] | None:
    for line_number, line in enumerate(lines, start=1):
        if line.strip():
            return line_number, line
    return None


def _parse_max_bytes(
    path: Path,
    line_number: int,
    name: str,
    max_bytes_text: str,
    errors: list[AllowlistError],
) -> int | None:
    try:
        max_bytes = int(max_bytes_text)
    except ValueError:
        errors.append(_error(path, line_number, name, "max_bytes must be a positive integer"))
        return None

    if max_bytes <= 0:
        errors.append(_error(path, line_number, name, "max_bytes must be a positive integer"))
        return None
    return max_bytes


def _parse_required(
    path: Path,
    line_number: int,
    name: str,
    required_text: str,
    errors: list[AllowlistError],
) -> bool | None:
    if required_text.lower() == "true":
        return True
    if required_text.lower() == "false":
        return False

    errors.append(_error(path, line_number, name, "required must be true or false"))
    return None


def _validate_name(
    path: Path,
    line_number: int,
    name: str,
    seen_names: set[str],
    errors: list[AllowlistError],
) -> None:
    if any(char in name for char in WILDCARD_CHARS):
        errors.append(
            _error(path, line_number, name, "wildcard characters are not allowed in names"),
        )
    elif not NAME_PATTERN.fullmatch(name):
        errors.append(
            _error(path, line_number, name, "name must be an exact environment variable name"),
        )

    if name in seen_names:
        errors.append(_error(path, line_number, name, "duplicate allowlist name"))
    seen_names.add(name)


def _validate_common_value(value: str, max_bytes: int) -> str | None:
    if len(value.encode("utf-8")) > max_bytes:
        return "default exceeds max_bytes"
    if value.lower() in DENIED_SENTINELS:
        return "default value is denied"
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return "default contains a banned control character"
    return None


def _validate_token(value: str) -> str | None:
    return None if TOKEN_PATTERN.fullmatch(value) else "must contain only token characters"


def _validate_token_list(value: str) -> str | None:
    tokens = value.split(",")
    if any(not token for token in tokens):
        return "must contain only non-empty comma-separated tokens"
    bad_tokens = [token for token in tokens if _validate_token(token) is not None]
    return None if not bad_tokens else "must contain only non-empty comma-separated tokens"


def _validate_path(value: str) -> str | None:
    return None if value.strip() else "must be a non-empty path"


def _validate_path_list(value: str) -> str | None:
    parts = value.split(":")
    if all(part.strip() for part in parts):
        return None
    return "must contain only non-empty path entries"


def _validate_uri(value: str) -> str | None:
    parsed = urlparse(value)
    if parsed.scheme and (parsed.netloc or parsed.path):
        return None
    return "must be an absolute URI"


def _validate_port(value: str) -> str | None:
    if not value.isdigit():
        return "must be a numeric port"
    port = int(value)
    return None if 1 <= port <= 65535 else "must be between 1 and 65535"


def _error(path: Path, line: int, name: str, message: str) -> AllowlistError:
    return AllowlistError(path=str(path), line=line, name=name, message=message)


def _json_payload(results: list[AllowlistResult]) -> dict[str, object]:
    return {
        "ok": all(result.ok for result in results),
        "files": [
            {
                "path": str(result.path),
                "ok": result.ok,
                "entries": len(result.rows),
                "errors": [asdict(error) for error in result.errors],
            }
            for result in results
        ],
    }


def _print_text_results(results: list[AllowlistResult]) -> None:
    for result in results:
        if result.ok:
            print(f"ok: {result.path} ({len(result.rows)} entries)")
            continue

        print(f"error: {result.path}")
        for error in result.errors:
            line = f":{error.line}" if error.line else ""
            print(f"  {error.name}{line}: {error.message}")


if __name__ == "__main__":
    raise SystemExit(main())
