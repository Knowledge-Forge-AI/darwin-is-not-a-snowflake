from __future__ import annotations

import os
import re
from pathlib import Path


INPUT_SEGMENT = re.compile(r"^[A-Za-z0-9._+@-]+$")
DENIED_PATH_CHARS = frozenset("$`%*?[]{}\\\n\r\t\0'")


class ConfigError(ValueError):
    """A stable configuration error reported with EX_CONFIG."""


def validate_portable_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError("path must be a non-empty string")
    if value.startswith("${HOME}"):
        raise ConfigError(f"path contains forbidden expansion syntax: {value!r}")
    if value.startswith("$HOME/"):
        remainder = value[len("$HOME/"):]
    elif value.startswith("~/"):
        remainder = value[2:]
    elif value.startswith("/"):
        remainder = value[1:]
    else:
        raise ConfigError(f"path must be absolute or begin with $HOME/ or ~/: {value!r}")
    if any(char in remainder for char in DENIED_PATH_CHARS):
        raise ConfigError(f"path contains a forbidden character: {value!r}")
    if value not in {"/", "~/", "$HOME/"} and value.endswith("/"):
        raise ConfigError(f"path must not have a trailing slash: {value!r}")
    if "//" in remainder:
        raise ConfigError(f"path must not contain repeated separators: {value!r}")
    for segment in remainder.split("/") if remainder else ():
        if segment in {"", ".", ".."}:
            raise ConfigError(f"path contains a forbidden segment: {value!r}")
        if not all(char.isalnum() or char in "._-+@ " for char in segment):
            raise ConfigError(f"path contains a forbidden character: {value!r}")
    return value


def resolve_portable_path(value: str, operator_home: Path) -> Path:
    validate_portable_path(value)
    if value.startswith("$HOME/"):
        resolved = operator_home / value[len("$HOME/"):]
    elif value.startswith("~/"):
        resolved = operator_home / value[2:]
    else:
        resolved = Path(value)
    return Path(str(resolved))


def validate_input_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError("flake input path must be a non-empty string")
    segments = value.split("/")
    if any(not segment or not INPUT_SEGMENT.fullmatch(segment) for segment in segments):
        raise ConfigError(f"invalid flake input path: {value!r}")
    if any(segment in {".", ".."} for segment in segments):
        raise ConfigError(f"invalid flake input path: {value!r}")
    return value


def require_within(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise ConfigError(f"{label} escapes its required root") from error
    return resolved


def validate_safe_managed_root(path: Path, operator_home: Path, label: str) -> Path:
    # Reject the leaf before resolving it, including dangling and cyclic links.
    if path.is_symlink():
        raise ConfigError(f"{label} has symlink ambiguity: {path}")
    require_within(path, operator_home, label)
    if path == operator_home or path == Path("/"):
        raise ConfigError(f"{label} cannot be the root or home directory")
    # Finding 3: system ancestors / and /Users are root-owned; validate at and below operator_home
    current = path.parent
    while current != operator_home and current != current.parent:
        if current.is_symlink():
            raise ConfigError(f"{label} has symlink ancestor ambiguity: {current}")
        if current.exists():
            stat = current.lstat()
            if stat.st_uid != os.getuid():
                raise ConfigError(f"{label} has foreign-owned ancestor: {current}")
            if stat.st_mode & 0o022:
                raise ConfigError(f"{label} has unsafe ancestor mode (group/world writable): {current}")
        current = current.parent

    if path.exists():
        stat = path.lstat()
        if stat.st_uid != os.getuid():
            raise ConfigError(f"{label} is foreign-owned: {path}")
        if stat.st_mode & 0o022:
            raise ConfigError(f"{label} has unsafe mode (group/world writable): {path}")
    return path
