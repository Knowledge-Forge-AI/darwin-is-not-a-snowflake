from __future__ import annotations

import os
import stat
from pathlib import Path

from .paths import ConfigError


NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
DIRECTORY = getattr(os, "O_DIRECTORY", 0)


def validate_ancestor_chain(path: Path, *, start: Path | None = None, owner_only: bool = False) -> None:
    if not path.is_absolute():
        raise ConfigError(f"path must be absolute: {path}")
    absolute = path.absolute()
    stop = Path(absolute.anchor) if start is None else start.absolute()
    try:
        relative = absolute.relative_to(stop)
    except ValueError as error:
        raise ConfigError(f"path is outside its reviewed root: {path}") from error
    current = stop
    entered_user_tree = False
    for part in relative.parts:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(metadata.st_mode):
            raise ConfigError(f"symlink traversal is forbidden: {current}")
        if owner_only and metadata.st_uid != os.getuid():
            if entered_user_tree:
                raise ConfigError(f"path leaves the current-user private tree: {current}")
        elif owner_only:
            entered_user_tree = True
        if current != absolute and not stat.S_ISDIR(metadata.st_mode):
            raise ConfigError(f"path ancestor is not a directory: {current}")
        forbidden_mode = 0o077 if start is not None else 0o022
        if owner_only and entered_user_tree and current != absolute and stat.S_IMODE(metadata.st_mode) & forbidden_mode:
            raise ConfigError(f"path ancestor is not owner-only: {current}")


def require_private_directory(path: Path, label: str, *, empty: bool = False) -> Path:
    validate_ancestor_chain(path, owner_only=True)
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise ConfigError(f"{label} is missing: {path}") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ConfigError(f"{label} must be an owner-only directory: {path}")
    if empty and any(path.iterdir()):
        raise ConfigError(f"{label} is not empty: {path}")
    return path


def create_private_directory(path: Path, label: str, *, parent: Path | None = None) -> Path:
    if not path.is_absolute() or Path(os.path.normpath(str(path))) != path:
        raise ConfigError(f"{label} path must be an exact normalized absolute path: {path}")
    if path.exists() or path.is_symlink():
        raise ConfigError(f"{label} already exists: {path}")
    reviewed_parent = path.parent if parent is None else parent
    if (
        not reviewed_parent.is_absolute()
        or Path(os.path.normpath(str(reviewed_parent))) != reviewed_parent
        or path.parent != reviewed_parent
    ):
        raise ConfigError(f"{label} is outside its exact reviewed parent: {reviewed_parent}")
    validate_ancestor_chain(reviewed_parent, owner_only=True)
    try:
        parent_metadata = reviewed_parent.lstat()
    except FileNotFoundError as error:
        raise ConfigError(f"{label} parent is missing: {reviewed_parent}") from error
    if (
        stat.S_ISLNK(parent_metadata.st_mode)
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != os.getuid()
        or stat.S_IMODE(parent_metadata.st_mode) != 0o700
    ):
        raise ConfigError(f"{label} parent is unsafe: {reviewed_parent}")
    descriptor = os.open(reviewed_parent, os.O_RDONLY | DIRECTORY | NOFOLLOW)
    try:
        opened_parent = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened_parent.st_mode)
            or opened_parent.st_dev != parent_metadata.st_dev
            or opened_parent.st_ino != parent_metadata.st_ino
            or opened_parent.st_uid != os.getuid()
            or stat.S_IMODE(opened_parent.st_mode) != 0o700
        ):
            raise ConfigError(f"{label} parent changed during validation: {reviewed_parent}")
        os.mkdir(path.name, 0o700, dir_fd=descriptor)
        child_metadata = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
        if (
            not stat.S_ISDIR(child_metadata.st_mode)
            or child_metadata.st_uid != os.getuid()
            or stat.S_IMODE(child_metadata.st_mode) != 0o700
        ):
            raise ConfigError(f"{label} created directory identity is unsafe: {path}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    require_private_directory(path, label)
    return path


def ensure_private_root(path: Path, label: str) -> Path:
    if path.exists() or path.is_symlink():
        return require_private_directory(path, label)
    missing: list[Path] = []
    cursor = path
    while not cursor.exists() and not cursor.is_symlink():
        missing.append(cursor)
        cursor = cursor.parent
    validate_ancestor_chain(cursor, owner_only=True)
    metadata = cursor.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise ConfigError(f"{label} ancestor is unsafe: {cursor}")
    for directory in reversed(missing):
        os.mkdir(directory, 0o700)
        require_private_directory(directory, label)
    return require_private_directory(path, label)


def require_private_file(path: Path, label: str) -> Path:
    validate_ancestor_chain(path, owner_only=True)
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise ConfigError(f"{label} must be an owner-only single-link regular file: {path}")
    return path


def exclusive_private_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    require_private_directory(path.parent, "private file parent")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, mode)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != mode or metadata.st_nlink != 1
        ):
            raise ConfigError(f"private file open identity is unsafe: {path}")
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    require_private_file(path, "private file")


def atomic_private_write(path: Path, content: bytes) -> None:
    require_private_directory(path.parent, "private file parent")
    if path.exists() or path.is_symlink():
        require_private_file(path, "private destination")
    temporary = path.parent / f".{path.name}.{os.getpid()}.{os.urandom(6).hex()}.tmp"
    exclusive_private_write(temporary, content)
    replaced = False
    try:
        os.replace(temporary, path)
        replaced = True
        fsync_directory(path.parent)
    finally:
        if not replaced and (temporary.exists() or temporary.is_symlink()):
            temporary.unlink()
    require_private_file(path, "private destination")


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise ConfigError(f"directory fsync identity is unsafe: {path}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
