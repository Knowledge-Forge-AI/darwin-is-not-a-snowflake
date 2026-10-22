from __future__ import annotations

import hashlib
import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import load_control_config
from .paths import ConfigError
from .deployment_paths import (
    create_private_directory,
    ensure_private_root,
    exclusive_private_write,
    fsync_directory,
    require_private_directory,
)


FLAKE_TEXT = """{
  description = "machine-local nix-darwin configuration (generated; do not edit)";
  outputs = { self }: { };
}
"""
CONTROL_TOML = frozenset({"nix-darwin.toml", "disclosure-scan.toml"})


@dataclass(frozen=True)
class PublicationResult:
    generation: Path
    current: Path
    changed: bool


@dataclass(frozen=True)
class ScratchProjection:
    root: Path
    files: tuple[tuple[str, str], ...]
    digest: str


@dataclass(frozen=True)
class CurrentSnapshot:
    exists: bool
    link: str | None


def render_evaluation(
    control_dir: Path,
    eval_root: Path,
    allowlist: dict[str, list[str]],
) -> PublicationResult:
    files = projected_files(control_dir, allowlist)
    return _publish_generation(eval_root, files)


def render_scratch(
    control_dir: Path,
    scratch_root: Path,
    allowlist: dict[str, list[str]],
) -> ScratchProjection:
    """Render a private projection without publishing eval/current."""
    files = projected_files(control_dir, allowlist)
    create_private_directory(scratch_root, "scratch projection directory")
    inventory: list[tuple[str, str]] = []
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        path = scratch_root / name
        exclusive_private_write(path, content)
        file_digest = hashlib.sha256(content).hexdigest()
        inventory.append((name, file_digest))
        digest.update(name.encode("utf-8") + b"\0" + content + b"\0")
    fsync_directory(scratch_root)
    return ScratchProjection(scratch_root, tuple(inventory), digest.hexdigest())


def inspect_scratch(scratch_root: Path) -> ScratchProjection:
    """Recompute the exact private scratch inventory without publishing it."""
    require_private_directory(scratch_root, "scratch projection root")
    inventory: list[tuple[str, str]] = []
    digest = hashlib.sha256()
    for path in sorted(scratch_root.iterdir()):
        metadata = path.lstat()
        if not path.is_file() or path.is_symlink() or metadata.st_nlink != 1 or metadata.st_mode & 0o077:
            raise ConfigError(f"scratch projection topology or mode drift: {path}")
        content = path.read_bytes()
        inventory.append((path.name, hashlib.sha256(content).hexdigest()))
        digest.update(path.name.encode("utf-8") + b"\0" + content + b"\0")
    if not inventory or not any(name == "flake.nix" for name, _digest in inventory):
        raise ConfigError("scratch projection is incomplete")
    return ScratchProjection(scratch_root, tuple(inventory), digest.hexdigest())


def projected_files(
    control_dir: Path,
    allowlist: dict[str, list[str]],
) -> dict[str, bytes]:
    """Return the exact allowlisted projection as bytes without writing it."""
    _validate_allowlist(allowlist)
    actual_toml = {path.name for path in control_dir.glob("*.toml")}
    projected_names = set(allowlist)
    unknown = sorted(actual_toml - projected_names - CONTROL_TOML)
    if unknown:
        raise ConfigError(f"unknown TOML file in control directory: {unknown[0]}")
    forbidden = sorted(projected_names & CONTROL_TOML)
    if forbidden:
        raise ConfigError(f"control TOML must never be projected: {forbidden[0]}")

    files: dict[str, bytes] = {"flake.nix": FLAKE_TEXT.encode("utf-8")}
    config_source = control_dir / "nix-darwin.toml"
    control_cfg = (
        load_control_config(config_source)
        if config_source.exists()
        else load_control_config(Path("/nonexistent"))
    )
    paths_payload = {
        "schema": "dinas-paths-v1",
        "workspace_root": str(control_cfg.workspace_root),
        "nbin_root": str(control_cfg.nbin_root),
        "nbin_dev_root": str(control_cfg.nbin_dev_root),
        "ai_index_root": str(control_cfg.ai_index_root),
        "dinas_root": str(control_cfg.dinas_root),
    }
    files["paths.json"] = (json.dumps(paths_payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    for filename in sorted(allowlist):
        source = control_dir / filename
        try:
            metadata = source.lstat()
            if source.is_symlink() or not source.is_file() or metadata.st_nlink != 1:
                raise ConfigError(f"allowlisted TOML file is not an exact regular file: {filename}")
            document = tomllib.loads(source.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            # Kitty's typed consumer supplies config/nix-darwin/default/kitty-bash.toml.
            # Only this explicitly admitted file has an absent-file default.
            if filename == "kitty-bash.toml":
                continue
            raise ConfigError(f"allowlisted TOML file is missing: {filename}") from error
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise ConfigError(f"unable to read allowlisted TOML file: {filename}") from error
        if filename == "kitty-bash.toml":
            # Do not filter away invalid keys before the typed consumer sees them.
            if set(document) - {"schema", "palette", "terminal", "completion", "ansi", "ble"}:
                raise ConfigError("unknown Kitty top-level key")
            if "ble" in document and not isinstance(document["ble"], dict):
                raise ConfigError("allowlisted key parent is not a table in kitty-bash.toml: ble.faces")
            if "ble" in document and set(document["ble"]) - {"faces"}:
                raise ConfigError("only [ble.faces] is supported")
        projected: dict[str, Any] = {}
        for dotted_key in allowlist[filename]:
            value = _lookup(document, dotted_key, filename)
            if value is not _MISSING:
                key_path = dotted_key[:-1] if dotted_key.endswith("?") else dotted_key
                _assign(projected, key_path, value)
        files[filename] = _dump_toml(projected).encode("utf-8")
    return files


def publish_scratch(scratch_root: Path, eval_root: Path) -> PublicationResult:
    """Publish only exact retained scratch bytes after reviewed authorization."""
    files: dict[str, bytes] = {}
    for path in sorted(scratch_root.iterdir()):
        metadata = path.lstat()
        if not path.is_file() or path.is_symlink() or metadata.st_nlink != 1:
            raise ConfigError(f"scratch projection topology drift: {path}")
        files[path.name] = path.read_bytes()
    if not files or "flake.nix" not in files:
        raise ConfigError("scratch projection is incomplete")
    return _publish_generation(eval_root, files)


def snapshot_current(eval_root: Path) -> CurrentSnapshot:
    if eval_root.exists() or eval_root.is_symlink():
        require_private_directory(eval_root, "evaluation root")
    current = eval_root / "current"
    if not current.exists() and not current.is_symlink():
        return CurrentSnapshot(False, None)
    if not current.is_symlink():
        raise ConfigError(f"evaluation current is not a symlink: {current}")
    metadata = current.lstat()
    if metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
        raise ConfigError(f"evaluation current has unsafe ownership or topology: {current}")
    return CurrentSnapshot(True, os.readlink(current))


def restore_current(eval_root: Path, snapshot: CurrentSnapshot, published: CurrentSnapshot) -> None:
    current = eval_root / "current"
    observed = snapshot_current(eval_root)
    if observed == snapshot:
        return
    if observed != published:
        raise ConfigError("evaluation current drift prevents restoration")
    temporary = eval_root / f".current.restore.{os.getpid()}"
    if snapshot.exists:
        if snapshot.link is None:
            raise ConfigError("evaluation current snapshot is invalid")
        temporary.symlink_to(snapshot.link)
        os.replace(temporary, current)
    else:
        current.unlink(missing_ok=True)
    _fsync(eval_root)


_MISSING = object()


def _validate_allowlist(allowlist: dict[str, list[str]]) -> None:
    if not isinstance(allowlist, dict):
        raise ConfigError("configAllowlist must be a filename mapping")
    for filename, keys in allowlist.items():
        if Path(filename).name != filename or not filename.endswith(".toml"):
            raise ConfigError(f"invalid allowlist filename: {filename!r}")
        if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
            raise ConfigError(f"allowlist for {filename} must be a list of strings")
        clean_keys = [k[:-1] if k.endswith("?") else k for k in keys]
        if len(set(clean_keys)) != len(keys):
            raise ConfigError(f"allowlist for {filename} contains a duplicate key")
        for key, clean in zip(keys, clean_keys):
            if not clean or any(part in {"", ".", ".."} for part in clean.split(".")):
                raise ConfigError(f"invalid allowlisted key for {filename}: {key!r}")


def _lookup(document: dict[str, Any], dotted_key: str, filename: str) -> Any:
    is_optional = dotted_key.endswith("?")
    key_path = dotted_key[:-1] if is_optional else dotted_key
    if not key_path or any(part in {"", ".", ".."} for part in key_path.split(".")):
        raise ConfigError(f"invalid allowlisted key for {filename}: {dotted_key!r}")
    value: Any = document
    for part in key_path.split("."):
        if not isinstance(value, dict):
            raise ConfigError(f"allowlisted key parent is not a table in {filename}: {key_path}")
        if part not in value:
            if is_optional:
                return _MISSING
            raise ConfigError(f"allowlisted key is missing from {filename}: {key_path}")
        value = value[part]
    return value


def _assign(target: dict[str, Any], dotted_key: str, value: Any) -> None:
    cursor = target
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        existing = cursor.setdefault(part, {})
        if not isinstance(existing, dict):
            raise ConfigError(f"allowlisted keys conflict at: {dotted_key}")
        cursor = existing
    cursor[parts[-1]] = value


def _dump_toml(document: dict[str, Any]) -> str:
    lines: list[str] = []
    _write_table(lines, (), document)
    return "\n".join(lines).rstrip() + "\n"


def _write_table(lines: list[str], prefix: tuple[str, ...], table: dict[str, Any]) -> None:
    scalars = [(key, value) for key, value in sorted(table.items()) if not isinstance(value, dict)]
    children = [(key, value) for key, value in sorted(table.items()) if isinstance(value, dict)]
    if prefix:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append("[" + ".".join(_toml_key(part) for part in prefix) + "]")
    for key, value in scalars:
        lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
    for key, value in children:
        _write_table(lines, (*prefix, key), value)


def _toml_key(value: str) -> str:
    if value.replace("_", "a").replace("-", "a").isalnum():
        return value
    return _quote(value)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise ConfigError(f"unsupported projected TOML value type: {type(value).__name__}")


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _publish_generation(root: Path, files: dict[str, bytes]) -> PublicationResult:
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8") + b"\0" + files[name] + b"\0")
    ensure_private_root(root, "evaluation root")
    generations = ensure_private_root(root / "generations", "evaluation generations root")
    generation = generations / digest.hexdigest()
    current = root / "current"
    if generation.exists():
        require_private_directory(generation, "evaluation generation")
        _verify_generation(generation, files)
    else:
        create_private_directory(generation, "evaluation generation")
        for name, content in files.items():
            path = generation / name
            exclusive_private_write(path, content)
        _fsync(generation)
    if current.is_symlink() and current.resolve() == generation.resolve():
        return PublicationResult(generation, current, False)
    temporary = root / f".current.new.{os.getpid()}"
    try:
        if temporary.exists() or temporary.is_symlink():
            raise ConfigError(f"evaluation temporary path already exists: {temporary}")
        temporary.symlink_to(Path("generations") / generation.name)
        temporary_metadata = temporary.lstat()
        if temporary_metadata.st_uid != os.getuid() or temporary_metadata.st_nlink != 1:
            raise ConfigError(f"evaluation temporary link is unsafe: {temporary}")
        os.replace(temporary, current)
        _fsync(root)
    finally:
        temporary.unlink(missing_ok=True)
    return PublicationResult(generation, current, True)


def _verify_generation(generation: Path, files: dict[str, bytes]) -> None:
    actual = {path.name for path in generation.iterdir()}
    if actual != set(files):
        raise ConfigError(f"evaluation generation contains unexpected files: {generation}")
    for name, content in files.items():
        path = generation / name
        metadata = path.lstat()
        if (
            not path.is_file() or path.is_symlink() or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1 or metadata.st_mode & 0o077
        ):
            raise ConfigError(f"evaluation generation file is unsafe: {path}")
        if path.read_bytes() != content:
            raise ConfigError(f"evaluation generation content mismatch: {generation / name}")


def _fsync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
