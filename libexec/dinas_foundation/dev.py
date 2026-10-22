from __future__ import annotations

import os
import pwd
import re
import sys
from pathlib import Path

from nix_darwin_local.config import load_control_config
from nix_darwin_local.paths import ConfigError


NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


class DevError(Exception):
    pass


def operator_home() -> Path:
    env_home = os.environ.get("HOME")
    if env_home:
        return Path(env_home)
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def resolve_dev_root() -> Path:
    home = operator_home()
    control_path = home / ".local/nix-darwin/config/nix-darwin.toml"
    return load_control_config(control_path, operator_home=home).nbin_dev_root


def validate_dev_root(dev_root: Path) -> Path:
    if not dev_root.exists():
        raise DevError(f"development namespace root does not exist: {dev_root}")
    if dev_root.is_symlink():
        raise DevError(f"development namespace root must not be a symlink: {dev_root}")
    if not dev_root.is_dir():
        raise DevError(f"development namespace root is not a directory: {dev_root}")
    stat = dev_root.lstat()
    if stat.st_uid != os.getuid():
        raise DevError(f"development namespace root is foreign-owned: {dev_root}")
    if stat.st_mode & 0o022:
        raise DevError(f"development namespace root has unsafe mode: {dev_root}")
    return dev_root


def validate_name(value: str, label: str) -> str:
    if not value or not isinstance(value, str):
        raise DevError(f"{label} must be a non-empty string")
    if not NAME_PATTERN.fullmatch(value):
        raise DevError(f"{label} contains forbidden characters: {value!r}")
    return value


def resolve_namespace_dir(dev_root: Path, namespace: str) -> Path:
    validate_name(namespace, "namespace")
    ns_entry = dev_root / namespace
    if not ns_entry.exists():
        raise DevError(f"namespace not found in development root: {namespace}")
    if ns_entry.is_symlink():
        symlink_stat = ns_entry.lstat()
        if symlink_stat.st_uid != os.getuid():
            raise DevError(f"namespace link is foreign-owned: {ns_entry}")
        target = ns_entry.resolve(strict=True)
    else:
        target = ns_entry
    if not target.is_dir():
        raise DevError(f"namespace target is not a directory: {target}")
    stat = target.stat()
    if stat.st_uid != os.getuid() and stat.st_uid != 0:
        raise DevError(f"namespace target is foreign-owned: {target}")
    if stat.st_mode & 0o022:
        raise DevError(f"namespace target has unsafe mode: {target}")
    return target


def resolve_command_executable(target_dir: Path, command: str) -> Path:
    validate_name(command, "command")
    cmd_path = target_dir / command
    if not cmd_path.exists():
        raise DevError(f"command not found in namespace: {command}")
    if not cmd_path.is_file():
        raise DevError(f"command is not a regular file: {cmd_path}")
    if not os.access(cmd_path, os.X_OK):
        raise DevError(f"command is not executable: {cmd_path}")
    stat = cmd_path.stat()
    if stat.st_uid != os.getuid() and stat.st_uid != 0:
        raise DevError(f"command executable is foreign-owned: {cmd_path}")
    if stat.st_mode & 0o022:
        raise DevError(f"command executable has unsafe mode: {cmd_path}")
    return cmd_path.resolve(strict=True)


def list_namespaces(dev_root: Path) -> int:
    try:
        validated = validate_dev_root(dev_root)
    except DevError as err:
        print(f"dinas-dev: {err}", file=sys.stderr)
        return 1
    entries = sorted(validated.iterdir(), key=lambda p: p.name)
    found = False
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            target = resolve_namespace_dir(validated, entry.name)
            print(f"{entry.name} -> {target}")
            found = True
        except DevError:
            continue
    if not found:
        print("dinas-dev: no active development namespaces", file=sys.stderr)
    return 0


def which_command(dev_root: Path, namespace: str, command: str) -> int:
    try:
        validated = validate_dev_root(dev_root)
        ns_dir = resolve_namespace_dir(validated, namespace)
        cmd = resolve_command_executable(ns_dir, command)
        print(str(cmd))
        return 0
    except DevError as err:
        print(f"dinas-dev: {err}", file=sys.stderr)
        return 1


def exec_command(dev_root: Path, namespace: str, command: str, args: list[str]) -> int:
    try:
        validated = validate_dev_root(dev_root)
        ns_dir = resolve_namespace_dir(validated, namespace)
        cmd = resolve_command_executable(ns_dir, command)
    except DevError as err:
        print(f"dinas-dev: {err}", file=sys.stderr)
        return 1
    argv = [str(cmd), *args]
    os.execv(str(cmd), argv)


def spawn_shell(dev_root: Path, namespace: str) -> int:
    try:
        validated = validate_dev_root(dev_root)
        ns_dir = resolve_namespace_dir(validated, namespace)
    except DevError as err:
        print(f"dinas-dev: {err}", file=sys.stderr)
        return 1
    shell_bin = os.environ.get("SHELL")
    if not shell_bin or not Path(shell_bin).is_file():
        shell_bin = pwd.getpwuid(os.getuid()).pw_shell
    if not shell_bin or not Path(shell_bin).is_file():
        shell_bin = "/bin/sh"
    new_env = os.environ.copy()
    current_path = new_env.get("PATH", "")
    new_env["PATH"] = f"{ns_dir}:{current_path}" if current_path else str(ns_dir)
    os.execve(shell_bin, [shell_bin], new_env)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] in {"-h", "--help"}:
        print("usage: dinas-dev <list|which|exec|shell> [args...]", file=sys.stderr)
        return 0 if argv and argv[0] in {"-h", "--help"} else 1

    subcommand = argv[0]
    try:
        dev_root = resolve_dev_root()
    except ConfigError as err:
        print(f"dinas-dev: {err}", file=sys.stderr)
        return 1

    if subcommand == "list":
        return list_namespaces(dev_root)
    elif subcommand == "which":
        if len(argv) != 3:
            print("usage: dinas-dev which <namespace> <command>", file=sys.stderr)
            return 1
        return which_command(dev_root, argv[1], argv[2])
    elif subcommand == "exec":
        if len(argv) < 3:
            print("usage: dinas-dev exec <namespace> <command> [args...]", file=sys.stderr)
            return 1
        return exec_command(dev_root, argv[1], argv[2], argv[3:])
    elif subcommand == "shell":
        if len(argv) != 2:
            print("usage: dinas-dev shell <namespace>", file=sys.stderr)
            return 1
        return spawn_shell(dev_root, argv[1])
    else:
        print(f"dinas-dev: unknown subcommand: {subcommand!r}", file=sys.stderr)
        return 1
