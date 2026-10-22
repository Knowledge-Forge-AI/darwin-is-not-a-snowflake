from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .paths import ConfigError, require_within
from .deployment_paths import (
    NOFOLLOW,
    atomic_private_write,
    ensure_private_root,
    fsync_directory,
    require_private_directory,
    require_private_file,
    validate_ancestor_chain,
)


@dataclass
class DeploymentLock:
    path: Path
    descriptor: int | None = None

    def __enter__(self) -> DeploymentLock:
        if self.path.parent.exists() or self.path.parent.is_symlink():
            require_private_directory(self.path.parent, "deployment lock directory")
        else:
            ensure_private_root(self.path.parent, "deployment lock directory")
        created = False
        try:
            self.descriptor = os.open(
                self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o600
            )
            created = True
        except FileExistsError:
            require_private_file(self.path, "deployment lock")
            self.descriptor = os.open(self.path, os.O_RDWR | NOFOLLOW)
        opened = os.fstat(self.descriptor)
        current = self.path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            os.close(self.descriptor)
            self.descriptor = None
            raise ConfigError(f"deployment lock open identity is unsafe: {self.path}")
        if created:
            fsync_directory(self.path.parent)
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(self.descriptor)
            self.descriptor = None
            raise ConfigError(f"another nix-update transaction holds {self.path}") from error
        os.ftruncate(self.descriptor, 0)
        os.write(self.descriptor, f"{os.getpid()}\n".encode())
        os.fsync(self.descriptor)
        return self

    def __exit__(self, *_args: object) -> None:
        if self.descriptor is not None:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None


def competing_processes(
    records: Iterable[Mapping[str, Any]],
    ignored_pids: Iterable[int] = (),
    participant_paths: Iterable[str] = (),
) -> list[dict[str, Any]]:
    ignored = set(ignored_pids)
    participant_executables = {str(Path(item).resolve(strict=False)) for item in participant_paths}
    matches: list[dict[str, Any]] = []
    for record in records:
        try:
            pid = int(record["pid"])
        except (KeyError, TypeError, ValueError):
            continue
        try:
            argv = tuple(str(item) for item in record.get("argv", ()))
        except TypeError:
            if record.get("parse_error"):
                argv = ()
            else:
                continue
        if pid in ignored:
            continue
        if record.get("parse_error"):
            classification, reason = _unparseable_process_classification(record)
            if classification == _PROCESS_AMBIGUOUS:
                matches.append({
                    "pid": pid,
                    "argv": list(argv),
                    "comm": str(record.get("comm", "")),
                    "reason": reason,
                })
            continue
        if not argv:
            continue
        classification, command, reason = _classify_process_command(argv)
        if classification == _PROCESS_AMBIGUOUS:
            matches.append({
                "pid": pid,
                "argv": list(argv),
                "reason": reason,
            })
            continue
        if classification != _PROCESS_RESOLVED:
            continue
        executable = Path(command[0]).name
        arguments = command[1:]
        exact_executable = str(Path(command[0]).resolve(strict=False)) if Path(command[0]).is_absolute() else ""
        reason = (
            "deployment participant mutation"
            if exact_executable in participant_executables
            else _mutation_reason(executable, arguments)
        )
        if reason:
            matches.append({"pid": pid, "argv": list(argv), "reason": reason})
    return matches


def read_process_table() -> list[dict[str, Any]]:
    results = [
        subprocess.run(
            ["ps", "-ww", "-axo", fields],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for fields in ("pid=,ppid=,command=", "pid=,ppid=,ucomm=")
    ]
    if any(result.returncode != 0 for result in results):
        raise ConfigError("unable to inspect process table")
    identity_by_pid: dict[int, tuple[int, str]] = {}
    for line in results[1].stdout.splitlines():
        fields = line.strip().split(maxsplit=2)
        if (
            len(fields) == 3
            and fields[0].isdigit()
            and fields[1].isdigit()
        ):
            identity_by_pid[int(fields[0])] = (int(fields[1]), fields[2])
    records: list[dict[str, Any]] = []
    for line in results[0].stdout.splitlines():
        fields = line.strip().split(maxsplit=2)
        if len(fields) < 2 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        pid = int(fields[0])
        ppid = int(fields[1])
        rendered = fields[2] if len(fields) == 3 else ""
        identity = identity_by_pid.get(pid)
        comm = identity[1] if identity is not None and identity[0] == ppid else ""
        try:
            argv = shlex.split(rendered, posix=True)
            if not argv and (
                not rendered or Path(comm).name in _PROCESS_IDENTITY_SENSITIVE
            ):
                raise ValueError("process arguments unavailable")
            records.append({"pid": pid, "ppid": ppid, "comm": comm, "argv": argv})
        except ValueError:
            records.append({
                "pid": pid,
                "ppid": ppid,
                "comm": comm,
                "argv": [],
                "rendered_args": rendered,
                "parse_error": (
                    "unmatched process arguments"
                    if rendered
                    else "process arguments unavailable"
                ),
            })
    return records


def process_ancestors(records: Iterable[Mapping[str, Any]], pid: int) -> set[int]:
    parents = {int(item["pid"]): int(item["ppid"]) for item in records}
    ancestors = {pid}
    while pid in parents and parents[pid] not in ancestors and parents[pid] > 0:
        pid = parents[pid]
        ancestors.add(pid)
    return ancestors


def inspect_profile(profile: Path, live: Path, generations: Path) -> dict[str, Any]:
    selected = _link_identity(profile, "selected system profile")
    active = _link_identity(live, "live system")
    retained: list[dict[str, Any]] = []
    for path in sorted(generations.parent.glob(generations.name)):
        if not path.is_symlink():
            raise ConfigError(f"retained generation is not a symlink: {path}")
        metadata = path.lstat()
        match = re.fullmatch(r".*-(\d+)-link", path.name)
        if match is None:
            raise ConfigError(f"retained generation has no generation number: {path}")
        retained.append({
            "path": str(path),
            "link": os.readlink(path),
            "target": str(path.resolve(strict=True)),
            "generation": int(match.group(1)),
            "kind": "symlink",
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
            "mode": stat.S_IMODE(metadata.st_mode),
            "link_count": metadata.st_nlink,
        })
    selected_retained = any(item["target"] == selected["target"] for item in retained)
    live_retained = any(item["target"] == active["target"] for item in retained)
    if selected["target"] == active["target"]:
        relationship = "aligned"
    elif selected_retained and live_retained:
        relationship = "split-retained"
    else:
        relationship = "unknown"
    return {
        "selected": selected,
        "live": active,
        "retained": retained,
        "relationship": relationship,
    }


def validate_profile_transition(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    target: str,
    allowed_new_generations: int = 1,
) -> None:
    if after["selected"]["target"] != target or after["live"]["target"] != target:
        raise ConfigError("post-switch profile and live system do not equal the reviewed target")
    live = after["live"]
    if (
        live.get("link") != target
        or live.get("symlink_text") != target
        or live.get("resolved_target") != target
    ):
        raise ConfigError("post-switch live-system link does not bind the exact reviewed target")
    before_by_path = {item["path"]: item for item in before["retained"]}
    after_by_path = {item["path"]: item for item in after["retained"]}
    for path, reviewed in before_by_path.items():
        if after_by_path.get(path) != reviewed:
            raise ConfigError("post-switch state changed a reviewed generation identity")
    added = [item for path, item in after_by_path.items() if path not in before_by_path]
    if len(added) != allowed_new_generations:
        raise ConfigError("post-switch generation count changed unexpectedly")
    highest_reviewed = max((int(item["generation"]) for item in before["retained"]), default=0)
    if any(int(item["generation"]) <= highest_reviewed for item in added):
        raise ConfigError("post-switch generation number violates the reviewed policy")
    if any(item["target"] != target for item in added):
        raise ConfigError("post-switch new generation does not bind the reviewed target")
    selected = after["selected"]
    if len(added) == 1:
        new_generation = added[0]
        if (
            selected.get("generation_path") != new_generation["path"]
            or selected.get("generation_number") != new_generation["generation"]
            or selected.get("generation_symlink_text") != new_generation["link"]
            or selected.get("symlink_text") != selected.get("link")
            or selected.get("resolved_target") != target
        ):
            raise ConfigError("post-switch selected profile does not bind the exact new generation identity")


def realignment_command(profile: Path, safe_generation: int) -> list[str]:
    if safe_generation <= 0:
        raise ConfigError("safe generation must be a positive integer")
    return ["nix-env", "--profile", str(profile), "--switch-generation", str(safe_generation)]


def selected_generation(profile: Mapping[str, Any]) -> int:
    link = str(profile.get("selected", {}).get("link", ""))
    match = re.fullmatch(r".*-(\d+)-link", Path(link).name)
    if match is None:
        raise ConfigError("selected profile does not point through a numbered generation link")
    generation = int(match.group(1))
    retained = {
        int(match.group(1))
        for item in profile.get("retained", [])
        if (match := re.fullmatch(r".*-(\d+)-link", Path(str(item.get("path", ""))).name)) is not None
    }
    if generation not in retained:
        raise ConfigError("selected profile generation is not retained")
    return generation


def derive_predecessor_generation(profile: Mapping[str, Any]) -> int:
    relationship = profile.get("relationship", "")
    if relationship not in {"aligned", "split-retained"}:
        raise ConfigError(
            f"unsupported profile relationship for predecessor derivation: {relationship}"
        )
    live = profile.get("live", {})
    matching = [
        item for item in profile.get("retained", [])
        if item.get("target") == live.get("target")
    ]
    live_generation = live.get("generation_number")
    if live_generation is not None:
        if (isinstance(live_generation, bool) or not isinstance(live_generation, int)
                or live_generation <= 0):
            raise ConfigError("live generation must be a positive integer")
        if not any(type(item.get("generation")) is int
                   and item["generation"] == live_generation for item in matching):
            raise ConfigError("live generation is not retained with its live target")
        return live_generation
    if relationship == "aligned":
        selected = profile.get("selected", {})
        if selected.get("target") != live.get("target"):
            raise ConfigError("aligned selected target does not equal the live target")
        generation = selected.get("generation_number")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation <= 0:
            raise ConfigError("selected generation must be a positive integer")
        generation_path = selected.get("generation_path")
        if not generation_path or not any(
            type(item.get("generation")) is int
            and item["generation"] == generation
            and item.get("path") == generation_path
            for item in matching
        ):
            raise ConfigError("selected generation is not retained with its live target and path")
        # Aligned aliases share a target; the selected numbered identity is the
        # canonical representative, not a claim of unique activation history.
        return generation
    # A split selected profile cannot identify an unnumbered live-side alias.
    if len(matching) != 1:
        raise ConfigError(
            "cannot derive live predecessor: expected exactly 1 matching retained "
            f"generation for live target, found {len(matching)}"
        )
    generation = matching[0].get("generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation <= 0:
        raise ConfigError("retained live generation must be a positive integer")
    return generation


def preflight_control_transactions(
    control_root: Path,
    specifications: Sequence[Mapping[str, Any]],
    virtual_root: Path,
) -> dict[str, Any]:
    from .deployment_plan import _validate_transactions, canonical_bytes, sha256_bytes, tree_identity

    root = control_root.absolute()
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ConfigError("reviewed control_root must be an exact directory")
    validate_ancestor_chain(root)
    if root.resolve(strict=True) != root:
        raise ConfigError("reviewed control_root may not traverse a symlink ancestor")
    root_metadata = root.lstat()
    if root_metadata.st_uid != os.getuid() or root_metadata.st_mode & 0o022:
        raise ConfigError("reviewed control_root ownership or mode is unsafe")
    _validate_transactions(list(specifications), str(root))
    effective: list[dict[str, Any]] = []
    identities: list[dict[str, Any]] = []
    for specification_value in specifications:
        specification = dict(specification_value)
        path = Path(specification["path"])
        _validate_complete_ancestor_chain(path, root, specification["safe_ancestors"])
        preimage = base64.b64decode(specification["preimage_bytes_b64"], validate=True)
        postimage = base64.b64decode(specification["postimage_bytes_b64"], validate=True)
        if path.suffix == ".toml":
            try:
                tomllib.loads(postimage.decode("utf-8"))
            except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
                raise ConfigError(f"control transaction postimage is not valid TOML: {path}") from error
        metadata = path.lstat()
        actual = _control_identity(path)
        expected_pre = {
            "kind": specification["pre_kind"], "uid": specification["pre_uid"],
            "gid": specification["pre_gid"], "mode": specification["pre_mode"],
            "link_count": specification["pre_link_count"], "size": len(preimage),
            "sha256": specification["preimage_sha256"],
        }
        expected_post = {
            "kind": "regular", "uid": specification["pre_uid"], "gid": specification["pre_gid"],
            "mode": specification["post_mode"], "link_count": 1, "size": len(postimage),
            "sha256": specification["postimage_sha256"],
        }
        extended_metadata = _verify_extended_metadata(path, specification["metadata_policy"])
        if actual == expected_pre:
            state_name = "pending"
        elif actual == expected_post:
            state_name = "already-postimage"
            specification.update(
                preimage_sha256=specification["postimage_sha256"],
                preimage_bytes_b64=specification["postimage_bytes_b64"],
                pre_mode=specification["post_mode"],
                restore_before_switch=False,
            )
            expected_pre = dict(expected_post)
        else:
            raise ConfigError(f"control transaction current state does not equal preimage or postimage: {path}")
        effective.append(specification)
        identities.append({
            "path": str(path), "state": state_name, "current": actual,
            "effective_preimage": expected_pre, "projected_postimage": expected_post,
            "metadata_policy": specification["metadata_policy"],
            "current_extended_metadata": extended_metadata,
        })
    if virtual_root.exists() or virtual_root.is_symlink():
        raise ConfigError(f"virtual control root already exists: {virtual_root}")
    require_private_directory(virtual_root.parent, "virtual control parent")
    shutil.copytree(root, virtual_root, symlinks=True)
    for candidate in sorted(virtual_root.rglob("*")):
        if candidate.is_symlink():
            continue
        candidate.chmod(0o700 if candidate.is_dir() else 0o600)
    virtual_root.chmod(0o700)
    for index, specification in enumerate(effective):
        relative = Path(specification["path"]).relative_to(root)
        destination = virtual_root / relative
        if destination.is_symlink() or not destination.is_file():
            raise ConfigError(f"virtual control destination is unsafe: {destination}")
        destination.write_bytes(base64.b64decode(specification["postimage_bytes_b64"], validate=True))
        destination.chmod(int(specification["post_mode"]))
        projected_actual = _control_identity(destination)
        projected_expected = identities[index]["projected_postimage"]
        if projected_actual != projected_expected:
            raise ConfigError(f"virtual control postimage identity is invalid: {destination}")
        identities[index]["projected_postimage_actual"] = projected_actual
        identities[index]["projected_extended_metadata"] = _verify_extended_metadata(
            destination, specification["metadata_policy"]
        )
    projected_tree = tree_identity(virtual_root)
    return {
        "control_root": str(root),
        "current_tree": tree_identity(root),
        "virtual_root": str(virtual_root),
        "projected_tree": projected_tree,
        "transactions": identities,
        "effective_transactions": effective,
        "digest": sha256_bytes(canonical_bytes({"projected_tree": projected_tree, "transactions": identities})),
    }


def apply_control_transaction(specification: Mapping[str, Any], state_directory: Path) -> dict[str, Any]:
    path = Path(str(specification["path"]))
    _validate_safe_ancestor(path, [Path(item) for item in specification["safe_ancestors"]])
    preimage = base64.b64decode(str(specification["preimage_bytes_b64"]), validate=True)
    postimage = base64.b64decode(str(specification["postimage_bytes_b64"]), validate=True)
    if _sha256(preimage) != specification["preimage_sha256"]:
        raise ConfigError(f"control transaction preimage hash is invalid: {path}")
    if _sha256(postimage) != specification["postimage_sha256"]:
        raise ConfigError(f"control transaction postimage hash is invalid: {path}")
    _verify_control_preimage(path, specification, preimage, postimage)
    if state_directory.exists() or state_directory.is_symlink():
        require_private_directory(state_directory, "control transaction state directory")
    else:
        ensure_private_root(state_directory, "control transaction state directory")
    marker = state_directory / f"control-{_sha256(str(path).encode())}.json"
    marker_document = {
        "path": str(path),
        "preimage_sha256": specification["preimage_sha256"],
        "postimage_sha256": specification["postimage_sha256"],
        "state": "rename-pending",
    }
    _atomic_private_write(marker, json.dumps(marker_document, sort_keys=True).encode() + b"\n", 0o600)
    if preimage == postimage and specification["pre_mode"] == specification["post_mode"]:
        marker_document["state"] = "postimage-already-current"
        _atomic_private_write(marker, json.dumps(marker_document, sort_keys=True).encode() + b"\n", 0o600)
        return {"path": str(path), "marker": str(marker), "changed": False}
    _atomic_replace(path, postimage, int(specification["post_mode"]))
    _verify_extended_metadata(path, specification["metadata_policy"])
    marker_document["state"] = "postimage-installed"
    _atomic_private_write(marker, json.dumps(marker_document, sort_keys=True).encode() + b"\n", 0o600)
    return {"path": str(path), "marker": str(marker), "changed": preimage != postimage or stat.S_IMODE(path.stat().st_mode) != specification["pre_mode"]}


def restore_control_transaction(specification: Mapping[str, Any], state_directory: Path | None = None) -> None:
    if not specification.get("restore_before_switch"):
        return
    path = Path(str(specification["path"]))
    preimage = base64.b64decode(str(specification["preimage_bytes_b64"]), validate=True)
    metadata = path.lstat()
    if (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == specification["pre_uid"]
        and metadata.st_gid == specification["pre_gid"]
        and stat.S_IMODE(metadata.st_mode) == specification["pre_mode"]
        and metadata.st_nlink == specification["pre_link_count"]
        and path.read_bytes() == preimage
    ):
        _verify_extended_metadata(path, specification["metadata_policy"])
        _record_control_restored(specification, state_directory)
        return
    verify_control_postimage(specification)
    _atomic_replace(path, preimage, int(specification["pre_mode"]))
    os.chown(path, int(specification["pre_uid"]), int(specification["pre_gid"]))
    _fsync(path.parent)
    _verify_control_preimage(path, specification, preimage, base64.b64decode(str(specification["postimage_bytes_b64"]), validate=True))
    _verify_extended_metadata(path, specification["metadata_policy"])
    _record_control_restored(specification, state_directory)


def _record_control_restored(specification: Mapping[str, Any], state_directory: Path | None) -> None:
    if state_directory is not None:
        path = Path(str(specification["path"]))
        marker = state_directory / f"control-{_sha256(str(path).encode())}.json"
        _atomic_private_write(
            marker,
            json.dumps(
                {
                    "path": str(path),
                    "preimage_sha256": specification["preimage_sha256"],
                    "postimage_sha256": specification["postimage_sha256"],
                    "state": "preimage-restored",
                },
                sort_keys=True,
            ).encode()
            + b"\n",
            0o600,
        )


def verify_control_postimage(specification: Mapping[str, Any]) -> None:
    path = Path(str(specification["path"]))
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ConfigError(f"control transaction postimage topology drift: {path}")
    if stat.S_IMODE(metadata.st_mode) != specification["post_mode"]:
        raise ConfigError(f"control transaction postimage mode drift: {path}")
    if metadata.st_uid != specification["pre_uid"] or metadata.st_gid != specification["pre_gid"]:
        raise ConfigError(f"control transaction postimage ownership drift: {path}")
    if _sha256(path.read_bytes()) != specification["postimage_sha256"]:
        raise ConfigError(f"control transaction postimage content drift: {path}")
    _verify_extended_metadata(path, specification["metadata_policy"])


def validate_control_transaction_current(
    specification: Mapping[str, Any], *, expect_postimage: bool = False
) -> None:
    """Revalidate the exact typed control contract without changing the real tree."""
    from .deployment_plan import _validate_transactions

    path = Path(str(specification["path"]))
    ancestors = list(specification["safe_ancestors"])
    if not ancestors:
        raise ConfigError("control transaction ancestor chain is empty")
    root = Path(ancestors[0])
    _validate_transactions([dict(specification)], str(root))
    _validate_complete_ancestor_chain(path, root, ancestors)
    preimage = base64.b64decode(str(specification["preimage_bytes_b64"]), validate=True)
    postimage = base64.b64decode(str(specification["postimage_bytes_b64"]), validate=True)
    if _sha256(preimage) != specification["preimage_sha256"]:
        raise ConfigError(f"control transaction preimage hash is invalid: {path}")
    if _sha256(postimage) != specification["postimage_sha256"]:
        raise ConfigError(f"control transaction postimage hash is invalid: {path}")
    if expect_postimage:
        verify_control_postimage(specification)
    else:
        _verify_control_preimage(path, specification, preimage, postimage)
        _verify_extended_metadata(path, specification["metadata_policy"])


def durable_marker(directory: Path, name: str, value: Any) -> Path:
    if Path(name).name != name or not name.endswith(".json"):
        raise ConfigError("marker name must be one JSON filename")
    if directory.exists() or directory.is_symlink():
        require_private_directory(directory, "durable marker directory")
    else:
        ensure_private_root(directory, "durable marker directory")
    path = directory / name
    _atomic_private_write(path, json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n", 0o600)
    return path


def _verify_control_preimage(path: Path, specification: Mapping[str, Any], preimage: bytes, postimage: bytes) -> None:
    metadata = path.lstat()
    expected = {
        "kind": specification["pre_kind"],
        "uid": specification["pre_uid"],
        "gid": specification["pre_gid"],
        "mode": specification["pre_mode"],
        "link_count": specification["pre_link_count"],
    }
    actual = {
        "kind": "regular" if stat.S_ISREG(metadata.st_mode) else "other",
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": stat.S_IMODE(metadata.st_mode),
        "link_count": metadata.st_nlink,
    }
    content = path.read_bytes()
    post_metadata = dict(expected)
    post_metadata["mode"] = specification["post_mode"]
    if content == postimage and actual == post_metadata:
        if (
            preimage != postimage
            or specification["pre_mode"] != specification["post_mode"]
            or specification.get("restore_before_switch")
        ):
            raise ConfigError(f"stale control transaction preimage contract: {path}")
        return
    if actual != expected:
        raise ConfigError(f"control transaction metadata drift: {path}")
    if content != preimage:
        raise ConfigError(f"control transaction preimage drift: {path}")


def _validate_safe_ancestor(path: Path, ancestors: Sequence[Path]) -> None:
    if not path.is_absolute() or not ancestors:
        raise ConfigError("control transaction requires an absolute path and safe ancestors")
    for ancestor in ancestors:
        if not ancestor.is_absolute():
            raise ConfigError("control transaction safe ancestors must be absolute")
        require_within(path.resolve(strict=False), ancestor.resolve(strict=True), "control transaction")
        metadata = ancestor.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise ConfigError(f"unsafe control transaction ancestor: {ancestor}")


def _validate_complete_ancestor_chain(path: Path, root: Path, ancestors: Sequence[str]) -> None:
    expected: list[Path] = [root]
    current = root
    for part in path.parent.relative_to(root).parts:
        current /= part
        expected.append(current)
    if [str(item) for item in expected] != list(ancestors):
        raise ConfigError("control transaction ancestor chain is incomplete")
    for ancestor in expected:
        metadata = ancestor.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o022
        ):
            raise ConfigError(f"unsafe control transaction ancestor: {ancestor}")


def _control_identity(path: Path) -> dict[str, Any]:
    metadata = path.lstat()
    content = path.read_bytes() if stat.S_ISREG(metadata.st_mode) else b""
    return {
        "kind": "regular" if stat.S_ISREG(metadata.st_mode) else "other",
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": stat.S_IMODE(metadata.st_mode),
        "link_count": metadata.st_nlink,
        "size": len(content),
        "sha256": _sha256(content),
    }


def _verify_extended_metadata(path: Path, policy: Mapping[str, Any]) -> dict[str, Any]:
    if policy.get("xattrs") != "require-absent" or policy.get("acl") != "require-absent" or policy.get("flags") != "require-zero":
        raise ConfigError("unsupported control transaction extended metadata policy")
    if hasattr(os, "listxattr"):
        try:
            xattrs = os.listxattr(path, follow_symlinks=False)
        except OSError as error:
            raise ConfigError(f"unable to inspect control transaction xattrs: {path}") from error
        xattr_names = sorted(str(item) for item in xattrs)
    else:
        inspected = subprocess.run(
            ["xattr", "-l", str(path)], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if inspected.returncode:
            raise ConfigError(f"unable to inspect control transaction xattrs: {path}")
        xattr_names = sorted(
            line.decode("utf-8", "strict").split(":", 1)[0]
            for line in inspected.stdout.splitlines()
            if line and not line.startswith((b" ", b"\t"))
        )
    # macOS attaches com.apple.provenance to some newly created files and does
    # not permit an unprivileged process to remove it. It is explicitly outside
    # transaction ownership: it must remain present if the kernel supplies it,
    # while every operator-controlled xattr is rejected.
    disallowed_xattrs = [name for name in xattr_names if name != "com.apple.provenance"]
    if disallowed_xattrs:
        raise ConfigError(f"control transaction xattr policy requires none: {path}")
    metadata = path.lstat()
    if int(getattr(metadata, "st_flags", 0)) != 0:
        raise ConfigError(f"control transaction flag policy requires zero: {path}")
    acl = subprocess.run(
        ["ls", "-lde", str(path)], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if acl.returncode:
        raise ConfigError(f"unable to inspect control transaction ACL: {path}")
    if any(re.match(r"\s*\d+:\s", line) for line in acl.stdout.splitlines()[1:]):
        raise ConfigError(f"control transaction ACL policy requires none: {path}")
    return {
        "xattrs": xattr_names,
        "system_managed_xattrs": [name for name in xattr_names if name == "com.apple.provenance"],
        "acl_entries": 0,
        "flags": int(getattr(metadata, "st_flags", 0)),
    }


def _atomic_replace(path: Path, content: bytes, mode: int) -> None:
    temporary = path.parent / f".{path.name}.nix-update.{os.getpid()}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        existing = path.lstat()
        os.chown(temporary, existing.st_uid, existing.st_gid)
        os.replace(temporary, path)
        _fsync(path.parent)
    finally:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _atomic_private_write(path: Path, content: bytes, mode: int) -> None:
    if mode != 0o600:
        raise ConfigError("private state files must use mode 0600")
    atomic_private_write(path, content)


def _link_identity(path: Path, label: str) -> dict[str, Any]:
    if not path.is_symlink():
        raise ConfigError(f"{label} is not a symlink: {path}")
    metadata = path.lstat()
    link = os.readlink(path)
    target = str(path.resolve(strict=True))
    identity: dict[str, Any] = {
        "path": str(path), "link": link, "symlink_text": link,
        "target": target, "resolved_target": target, "kind": "symlink",
        "uid": metadata.st_uid, "gid": metadata.st_gid,
        "mode": stat.S_IMODE(metadata.st_mode), "link_count": metadata.st_nlink,
        "generation_path": None, "generation_symlink_text": None, "generation_number": None,
    }
    generation_path = path.parent / link
    match = re.fullmatch(r".*-(\d+)-link", generation_path.name)
    if match is not None:
        if not generation_path.is_symlink():
            raise ConfigError(f"{label} generation path is not a symlink: {generation_path}")
        identity.update({
            "generation_path": str(generation_path),
            "generation_symlink_text": os.readlink(generation_path),
            "generation_number": int(match.group(1)),
        })
    return identity


# Root option arities come from the installed Nix 2.31.5 `nix __dump-cli`
# metadata. Explicit compatibility options follow it; unknown future options
# remain a typed fail-closed match instead of being guessed.
_NIX_ZERO_OPERAND_OPTIONS = frozenset("""
--abort-on-warn --accept-flake-config --allow-dirty --allow-dirty-locks --allow-import-from-derivation --allow-symlinked-store
--allow-unsafe-native-code-during-evaluation --always-allow-substitutes --auto-allocate-uids --auto-optimise-store --builders-use-substitutes --compress-build-log
--darwin-log-sandbox-violations --debug --debugger-on-trace --debugger-on-warn --eval-cache --fallback
--fsync-metadata --fsync-store-paths --help --http2 --ignore-try --impersonate-linux-26
--keep-build-log --keep-derivations --keep-env-derivations --keep-failed --keep-going --keep-outputs
--nix-shell-always-looks-for-shell-nix --nix-shell-shebang-arguments-relative-to-script --no-abort-on-warn --no-accept-flake-config --no-allow-dirty --no-allow-dirty-locks
--no-allow-import-from-derivation --no-allow-symlinked-store --no-allow-unsafe-native-code-during-evaluation --no-always-allow-substitutes --no-auto-allocate-uids --no-auto-optimise-store
--no-builders-use-substitutes --no-compress-build-log --no-darwin-log-sandbox-violations --no-debugger-on-trace --no-debugger-on-warn --no-eval-cache
--no-fallback --no-fsync-metadata --no-fsync-store-paths --no-http2 --no-ignore-try --no-impersonate-linux-26
--no-keep-build-log --no-keep-derivations --no-keep-env-derivations --no-keep-failed --no-keep-going --no-keep-outputs
--no-nix-shell-always-looks-for-shell-nix --no-nix-shell-shebang-arguments-relative-to-script --no-preallocate-contents --no-print-missing --no-pure-eval --no-require-drop-supplementary-groups
--no-require-sigs --no-restrict-eval --no-run-diff-hook --no-sandbox --no-sandbox-fallback --no-show-trace
--no-substitute --no-sync-before-registering --no-trace-function-calls --no-trace-import-from-derivation --no-trace-verbose --no-trust-tarballs-from-git-forges
--no-use-case-hack --no-use-registries --no-use-sqlite-wal --no-use-xdg-base-directories --no-warn-dirty --no-warn-short-path-literals
--offline --preallocate-contents --print-build-logs --print-missing --pure-eval --quiet
--refresh --relaxed-sandbox --require-drop-supplementary-groups --require-sigs --restrict-eval --run-diff-hook
--sandbox --sandbox-fallback --show-trace --substitute --sync-before-registering --trace-function-calls
--trace-import-from-derivation --trace-verbose --trust-tarballs-from-git-forges --use-case-hack --use-registries --use-sqlite-wal
--use-xdg-base-directories --verbose --version --warn-dirty --warn-short-path-literals
--debugger --impure --no-build-output --no-update-lock-file --no-write-lock-file --repair
""".split())

_NIX_ONE_OPERAND_OPTIONS = frozenset("""
--access-tokens --allowed-impure-host-deps --allowed-uris --allowed-users --bash-prompt --bash-prompt-prefix
--bash-prompt-suffix --build-dir --build-hook --build-poll-interval --build-users-group --builders
--commit-lock-file-summary --connect-timeout --cores --diff-hook --download-attempts --download-buffer-size
--download-speed --eval-profile-file --eval-profiler --eval-profiler-frequency --eval-system --experimental-features
--extra-access-tokens --extra-allowed-impure-host-deps --extra-allowed-uris --extra-allowed-users --extra-build-hook --extra-experimental-features
--extra-extra-platforms --extra-hashed-mirrors --extra-impure-env --extra-nix-path --extra-platforms --extra-plugin-files
--extra-sandbox-paths --extra-secret-key-files --extra-substituters --extra-system-features --extra-trusted-public-keys --extra-trusted-substituters
--extra-trusted-users --flake-registry --gc-reserved-space --hashed-mirrors --http-connections --id-count
--impure-env --json-log-path --log-format --log-lines --max-build-log-size --max-call-depth
--max-free --max-jobs --max-silent-time --max-substitution-jobs --min-free --min-free-check-interval
--nar-buffer-size --narinfo-cache-negative-ttl --narinfo-cache-positive-ttl --netrc-file --nix-path --plugin-files
--post-build-hook --pre-build-hook --sandbox-paths --secret-key-files --ssl-cert-file --stalled-download-timeout
--start-id --store --substituters --system --system-features --tarball-ttl
--timeout --trusted-public-keys --trusted-substituters --trusted-users --upgrade-nix-store-path-url --user-agent-suffix
--warn-large-path-threshold --arg-from-stdin --eval-store --include
""".split())

_NIX_TWO_OPERAND_OPTIONS = frozenset({
    "--arg", "--arg-from-file", "--argstr", "--option", "--override-flake",
    "--override-input", "--redirect", "--set-env-var",
})

_SUDO_ZERO_OPERAND_OPTIONS = frozenset({
    "-A", "--askpass", "-B", "--bell", "-b", "--background", "-E", "--preserve-env",
    "-H", "--set-home", "-i", "--login", "-K", "--remove-timestamp", "-k",
    "--reset-timestamp", "-N", "--no-update", "-n", "--non-interactive", "-P",
    "--preserve-groups", "-S", "--stdin", "-s", "--shell", "-v", "--validate",
})
_SUDO_ONE_OPERAND_OPTIONS = frozenset({
    "-C", "--close-from", "-D", "--chdir", "-g", "--group", "-h", "--host",
    "-p", "--prompt", "-R", "--chroot", "-T", "--command-timeout", "-u", "--user",
    "-U", "--other-user",
})
_ENV_ZERO_OPERAND_OPTIONS = frozenset({
    "-0", "--null", "-i", "--ignore-environment", "-v", "--debug",
    "--block-signal", "--default-signal", "--ignore-signal", "--list-signal-handling",
})
_ENV_ONE_OPERAND_OPTIONS = frozenset({
    "-a", "--argv0", "-C", "--chdir", "-P", "-S", "-u", "--unset",
})
_ENV_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*", re.DOTALL)
_PROCESS_RESOLVED = "resolved command"
_PROCESS_TERMINAL = "terminal non-mutating invocation"
_PROCESS_AMBIGUOUS = "ambiguous sensitive wrapper or command position"
_PROCESS_INNOCENT = "innocent/non-sensitive invocation"
_SENSITIVE_EXECUTABLES = frozenset({"nix-update", "nix", "darwin-rebuild", "nix-env"})
_SENSITIVE_WRAPPERS = frozenset({"sudo", "env"})
_SHELL_ZERO_SHORT_OPTIONS = frozenset("abefhkmnptuvxBCEHPTilrsD")
_SHELL_ZERO_LONG_OPTIONS = frozenset({
    "--debug", "--debugger", "--dump-po-strings", "--dump-strings", "--login",
    "--noediting", "--noprofile", "--norc", "--posix", "--pretty-print",
    "--restricted", "--verbose",
})
_SHELL_ONE_LONG_OPTIONS = frozenset({"--init-file", "--rcfile"})
_PYTHON_ZERO_SHORT_OPTIONS = frozenset({
    "-B", "-d", "-E", "-i", "-I", "-P", "-q", "-s", "-S", "-u", "-x",
})
_PYTHON_TERMINAL_OPTIONS = frozenset({
    "-h", "-?", "-V", "-VV", "--help", "--version", "--help-env",
    "--help-xoptions", "--help-all",
})
_PROCESS_IDENTITY_SENSITIVE = _SENSITIVE_EXECUTABLES | _SENSITIVE_WRAPPERS


def _mutation_reason(executable: str, arguments: Sequence[str]) -> str | None:
    if executable == "nix-update":
        if arguments and arguments[0] == "apply":
            return "nix-update apply"
        if tuple(arguments[:2]) == ("advanced", "apply"):
            return "nix-update advanced apply"
        if tuple(arguments[:2]) == ("promote", "apply"):
            return "nix-update promote apply"
        non_mutating_subcommands = {"build", "lock", "optimize"}
        non_mutating_options = {"--dry-run", "--plan-only", "-h", "--help"}
        if arguments and arguments[0] in non_mutating_subcommands:
            return None
        if tuple(arguments[:2]) in {("advanced", "prepare"), ("promote", "prepare")}:
            return None
        if any(arg in non_mutating_options for arg in arguments):
            return None
        if "--build-only" in arguments:
            return None
        return "nix-update deploy"
    if executable == "nix":
        operation_arguments = _nix_operation_arguments(arguments)
        if operation_arguments is None:
            return "ambiguous nix command options"
        if operation_arguments[:2] in {("flake", "update"), ("flake", "lock")}:
            return "nix flake lock mutation"
    if executable == "darwin-rebuild" and any(
        argument in {
            "switch", "activate", "rollback", "--rollback", "--switch-generation", "-G"
        }
        for argument in arguments
    ):
        return "darwin-rebuild mutation"
    if executable == "nix-env" and any(
        item in {"--switch-generation", "--delete-generations", "--rollback", "--switch-profile", "-G"}
        for item in arguments
    ):
        return "profile generation mutation"
    return None


def _nix_operation_arguments(arguments: Sequence[str]) -> tuple[str, ...] | None:
    values = list(arguments)
    index = 0
    while index < len(values) and values[index].startswith("-"):
        option = values[index]
        if option == "--":
            return tuple(values[index + 1:])
        if option in {"--help", "--version"}:
            return ()
        if re.fullmatch(r"-v+", option) or option in {"-K", "-k", "-L", "-Q"}:
            operand_count = 0
        elif option in {"-I", "-j"}:
            operand_count = 1
        elif option.startswith(("-I", "-j")) and len(option) > 2:
            operand_count = 0
        else:
            name, separator, _inline_value = option.partition("=")
            if name in _NIX_ZERO_OPERAND_OPTIONS:
                if separator:
                    return None
                operand_count = 0
            elif name in _NIX_ONE_OPERAND_OPTIONS:
                operand_count = 0 if separator else 1
            elif name in _NIX_TWO_OPERAND_OPTIONS:
                operand_count = 1 if separator else 2
            else:
                return None
        if len(values) - index - 1 < operand_count:
            return None
        index += operand_count + 1
    return tuple(values[index:])


def _wrapper_option_arity(wrapper: str, token: str) -> int | None:
    zero_options = _SUDO_ZERO_OPERAND_OPTIONS if wrapper == "sudo" else _ENV_ZERO_OPERAND_OPTIONS
    one_options = _SUDO_ONE_OPERAND_OPTIONS if wrapper == "sudo" else _ENV_ONE_OPERAND_OPTIONS
    if token in zero_options:
        return 0
    if token in one_options:
        return 1
    name, separator, _inline_value = token.partition("=")
    optional_equals = (
        {"--preserve-env"} if wrapper == "sudo"
        else {"--block-signal", "--default-signal", "--ignore-signal"}
    )
    if separator and (name in one_options or name in optional_equals):
        return 0
    if token.startswith("-") and not token.startswith("--"):
        short = token[1:]
        short_zero = {option for option in zero_options if len(option) == 2}
        short_one = {option for option in one_options if len(option) == 2}
        while short:
            option = "-" + short[0]
            if option in short_zero:
                short = short[1:]
                continue
            if option in short_one:
                return 0 if len(short) > 1 else 1
            return None
        return 0
    return None


def _classify_process_command(
    argv: Sequence[str],
    *,
    incomplete: bool = False,
) -> tuple[str, tuple[str, ...], str]:
    command = list(argv)
    while command and Path(command[0]).name in _SENSITIVE_WRAPPERS:
        wrapper = Path(command.pop(0)).name
        while command:
            token = command[0]
            if token == "--":
                command.pop(0)
                break
            if wrapper == "sudo" and token == "-h" and len(command) == 1:
                if incomplete:
                    return _PROCESS_AMBIGUOUS, (), "ambiguous wrapper options"
                return _PROCESS_TERMINAL, (), ""
            if wrapper == "sudo" and token in {"--help", "--version", "-V", "--list", "-l", "--edit", "-e"}:
                return _PROCESS_TERMINAL, (), ""
            if wrapper == "env" and token in {"--help", "--version"}:
                return _PROCESS_TERMINAL, (), ""
            if wrapper == "env" and token in {"-S", "--split-string"}:
                if len(command) < 2:
                    return _PROCESS_AMBIGUOUS, (), "ambiguous wrapper options"
                try:
                    command = shlex.split(command[1], posix=True) + command[2:]
                except ValueError:
                    return _PROCESS_AMBIGUOUS, (), "ambiguous wrapper options"
                continue
            if wrapper == "env" and token.startswith("-S") and not token.startswith("--"):
                try:
                    command = shlex.split(token[2:], posix=True) + command[1:]
                except ValueError:
                    return _PROCESS_AMBIGUOUS, (), "ambiguous wrapper options"
                continue
            if wrapper == "env" and token.startswith("--split-string="):
                try:
                    command = shlex.split(token.partition("=")[2], posix=True) + command[1:]
                except ValueError:
                    return _PROCESS_AMBIGUOUS, (), "ambiguous wrapper options"
                continue
            if _ENV_ASSIGNMENT.fullmatch(token):
                command.pop(0)
                continue
            option_arity = _wrapper_option_arity(wrapper, token)
            if option_arity is not None:
                if len(command) <= option_arity:
                    return _PROCESS_AMBIGUOUS, (), "ambiguous wrapper options"
                command = command[option_arity + 1:]
                continue
            if token.startswith("-"):
                return _PROCESS_AMBIGUOUS, (), "ambiguous wrapper options"
            break
        if not command:
            if incomplete:
                return _PROCESS_AMBIGUOUS, (), "ambiguous wrapper command position"
            return _PROCESS_TERMINAL, (), ""
    interpreter_name = Path(command[0]).name if command else ""
    if interpreter_name in {"bash", "sh"}:
        return _classify_shell_command(command, incomplete=incomplete)
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", interpreter_name) is not None:
        return _classify_python_command(command, incomplete=incomplete)
    if not command:
        return _PROCESS_INNOCENT, (), ""
    return _PROCESS_RESOLVED, tuple(command), ""


def _classify_shell_command(
    argv: Sequence[str],
    *,
    incomplete: bool,
) -> tuple[str, tuple[str, ...], str]:
    command = list(argv[1:])
    while (
        command
        and command[0][:1] in {"-", "+"}
        and command[0] not in {"-", "+"}
    ):
        token = command.pop(0)
        if token == "--":
            break
        if token in {"--help", "--version"}:
            return _PROCESS_TERMINAL, (), ""
        if token.startswith("--"):
            name, separator, _value = token.partition("=")
            if name in _SHELL_ZERO_LONG_OPTIONS and not separator:
                continue
            if name in _SHELL_ONE_LONG_OPTIONS:
                if separator:
                    return _PROCESS_AMBIGUOUS, (), "ambiguous interpreter options"
                if not command:
                    return _PROCESS_AMBIGUOUS, (), "ambiguous interpreter options"
                command.pop(0)
                continue
            return _PROCESS_AMBIGUOUS, (), "ambiguous interpreter options"
        if token in {"-O", "-o", "+O", "+o"}:
            if not command:
                return _PROCESS_AMBIGUOUS, (), "ambiguous interpreter options"
            command.pop(0)
            continue
        option_prefix = token[0]
        short = token[1:]
        while short:
            option = short[0]
            short = short[1:]
            if option in _SHELL_ZERO_SHORT_OPTIONS:
                continue
            if option_prefix == "-" and option == "c":
                return _PROCESS_TERMINAL, (), ""
            if option in {"O", "o"}:
                return _PROCESS_AMBIGUOUS, (), "ambiguous interpreter options"
            return _PROCESS_AMBIGUOUS, (), "ambiguous interpreter options"
    if not command:
        if incomplete:
            return _PROCESS_AMBIGUOUS, (), "ambiguous interpreter command position"
        return _PROCESS_TERMINAL, (), ""
    if Path(command[0]).name in _SENSITIVE_EXECUTABLES:
        return _PROCESS_RESOLVED, tuple(command), ""
    return _PROCESS_INNOCENT, tuple(command), ""


def _classify_python_command(
    argv: Sequence[str],
    *,
    incomplete: bool,
) -> tuple[str, tuple[str, ...], str]:
    command = list(argv[1:])
    while command and command[0].startswith("-") and command[0] != "-":
        token = command.pop(0)
        if token == "--":
            break
        if token in _PYTHON_TERMINAL_OPTIONS:
            return _PROCESS_TERMINAL, (), ""
        if token == "--check-hash-based-pycs":
            if not command:
                return _PROCESS_AMBIGUOUS, (), "ambiguous interpreter options"
            if command.pop(0) not in {"always", "default", "never"}:
                return _PROCESS_AMBIGUOUS, (), "ambiguous interpreter options"
            continue
        if token.startswith("--"):
            return _PROCESS_AMBIGUOUS, (), "ambiguous interpreter options"
        if token == "-c" or token.startswith("-c"):
            return _PROCESS_TERMINAL, (), ""
        if token == "-m" or token.startswith("-m"):
            if token == "-m":
                if not command:
                    return _PROCESS_AMBIGUOUS, (), "ambiguous interpreter options"
                module = command.pop(0)
            else:
                module = token[2:]
            if module == "nix-update":
                return _PROCESS_RESOLVED, ("nix-update", *command), ""
            return _PROCESS_INNOCENT, (), ""
        if token in {"-W", "-X"}:
            if not command:
                return _PROCESS_AMBIGUOUS, (), "ambiguous interpreter options"
            command.pop(0)
            continue
        if token.startswith(("-W", "-X")) and len(token) > 2:
            continue
        if (
            token in _PYTHON_ZERO_SHORT_OPTIONS
            or re.fullmatch(r"-b{1,2}", token)
            or re.fullmatch(r"-O{1,2}", token)
            or re.fullmatch(r"-v+", token)
        ):
            continue
        return _PROCESS_AMBIGUOUS, (), "ambiguous interpreter options"
    if not command:
        if incomplete:
            return _PROCESS_AMBIGUOUS, (), "ambiguous interpreter command position"
        return _PROCESS_TERMINAL, (), ""
    if Path(command[0]).name in _SENSITIVE_EXECUTABLES:
        return _PROCESS_RESOLVED, tuple(command), ""
    return _PROCESS_INNOCENT, tuple(command), ""


def _unparseable_process_classification(record: Mapping[str, Any]) -> tuple[str, str]:
    comm = Path(str(record.get("comm", ""))).name
    if not comm:
        return _PROCESS_AMBIGUOUS, "unparseable process identity"
    if comm in _SENSITIVE_EXECUTABLES:
        return _PROCESS_AMBIGUOUS, "unparseable sensitive process"
    if comm not in _SENSITIVE_WRAPPERS and comm not in {"bash", "sh"} and not re.fullmatch(
        r"python(?:\d+(?:\.\d+)*)?", comm
    ):
        return _PROCESS_INNOCENT, ""
    rendered = str(record.get("rendered_args", ""))
    if not rendered:
        return _PROCESS_AMBIGUOUS, f"unparseable {comm} command position"
    tokens = _shlex_prefix(rendered)
    if not tokens or Path(tokens[0]).name != comm:
        return _PROCESS_AMBIGUOUS, f"unparseable {comm} command position"
    classification, command, _reason = _classify_process_command(tokens, incomplete=True)
    if classification == _PROCESS_AMBIGUOUS:
        return _PROCESS_AMBIGUOUS, f"unparseable {comm} command position"
    if classification in {_PROCESS_TERMINAL, _PROCESS_INNOCENT}:
        return classification, ""
    executable = Path(command[0]).name
    if executable in _SENSITIVE_EXECUTABLES:
        return _PROCESS_AMBIGUOUS, "unparseable sensitive process"
    return _PROCESS_INNOCENT, ""


def _shlex_prefix(rendered: str) -> tuple[str, ...]:
    lexer = shlex.shlex(rendered, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens: list[str] = []
    try:
        tokens.extend(lexer)
    except ValueError:
        pass
    return tuple(tokens)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fsync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
