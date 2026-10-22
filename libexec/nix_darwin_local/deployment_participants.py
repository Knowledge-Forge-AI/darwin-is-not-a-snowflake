from __future__ import annotations

import hashlib
import json
import os
import signal
import shlex
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .deployment_plan import PARTICIPANT_SCHEMA, redact_text, reject_secrets
from .deployment_plan import canonical_bytes, sha256_bytes
from .deployment_paths import (
    NOFOLLOW,
    atomic_private_write,
    ensure_private_root,
    require_private_directory,
)
from .paths import ConfigError


@dataclass(frozen=True)
class ParticipantResult:
    name: str
    phase: str
    status: str
    blockers: tuple[str, ...]
    observations: dict[str, Any]
    rollback_handle: str | None
    context_digest: str
    attestation_digest: str
    stdout: str
    stderr: str
    failure_kind: str | None = None
    timeout_seconds: int | None = None
    mutation_state: str | None = None


def freeze_operator_artifacts(
    declarations: Sequence[Mapping[str, Any]], state_root: Path
) -> list[dict[str, Any]]:
    frozen_root = ensure_private_root(state_root / "participant-artifacts", "participant artifact root")
    frozen: list[dict[str, Any]] = []
    for declaration in declarations:
        item = json.loads(json.dumps(declaration))
        provenance = item["provenance"]
        if provenance["kind"] != "operator-artifact":
            frozen.append(item)
            continue
        source_path = Path(item["path"])
        source_root = Path(provenance["root"])
        try:
            raw, source_identity = _read_private_artifact(source_root, source_path)
        except OSError as error:
            raise ConfigError(
                f"operator participant provenance traversal failed: {item['name']}"
            ) from error
        digest = hashlib.sha256(raw).hexdigest()
        if digest != item["sha256"]:
            raise ConfigError(f"participant SHA-256 drift: {item['name']}")
        frozen_path = frozen_root / f"{digest}.participant"
        _install_frozen_artifact(frozen_root, frozen_path, raw)
        frozen_raw, frozen_identity = _read_private_artifact(frozen_root, frozen_path)
        if frozen_raw != raw:
            raise ConfigError(f"frozen participant bytes changed during installation: {item['name']}")
        item["path"] = str(frozen_path)
        item["provenance"] = {
            "kind": "operator-artifact",
            "identity": f"sha256:{digest}",
            "root": str(source_root),
            "source_path": str(source_path),
            "source_identity": source_identity,
            "frozen_root": str(frozen_root),
            "frozen_path": str(frozen_path),
            "frozen_identity": frozen_identity,
        }
        frozen.append(item)
    return frozen


def validate_executable(specification: Mapping[str, Any]) -> Path:
    path, descriptor, _ = _open_validated_executable(specification)
    os.close(descriptor)
    return path


def _open_validated_executable(
    specification: Mapping[str, Any]
) -> tuple[Path, int, tuple[str, ...] | None]:
    path = Path(str(specification["path"]))
    provenance = specification["provenance"]
    if provenance["kind"] == "target-closure":
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            raise ConfigError(f"participant must be an exact regular file: {path}")
        descriptor = os.open(path, os.O_RDONLY | NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ConfigError(f"participant must be a single-link regular file: {path}")
            if Path("/nix/store") not in path.parents or path.resolve(strict=True) != path or metadata.st_mode & 0o022:
                raise ConfigError(f"target-closure participant is not an immutable store executable: {path}")
            raw = _read_descriptor(descriptor)
            interpreter = None
        except Exception:
            os.close(descriptor)
            raise
    else:
        if path != Path(provenance.get("frozen_path", "")):
            raise ConfigError(f"operator participant must execute its frozen copy: {path}")
        try:
            descriptor, raw, identity = _open_private_artifact(
                Path(provenance["frozen_root"]), path
            )
        except OSError as error:
            raise ConfigError(
                f"frozen participant provenance traversal failed: {specification['name']}"
            ) from error
        if identity != provenance.get("frozen_identity"):
            os.close(descriptor)
            raise ConfigError(f"frozen participant identity drift: {specification['name']}")
        interpreter = _validated_operator_interpreter(raw, specification["name"])
    if hashlib.sha256(raw).hexdigest() != specification["sha256"]:
        os.close(descriptor)
        raise ConfigError(f"participant SHA-256 drift: {specification.get('name', path.name)}")
    if not os.fstat(descriptor).st_mode & 0o111:
        os.close(descriptor)
        raise ConfigError(f"participant is not executable: {path}")
    os.lseek(descriptor, 0, os.SEEK_SET)
    return path, descriptor, interpreter


def run_participant(
    specification: Mapping[str, Any],
    phase: str,
    context_path: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ParticipantResult:
    if phase not in specification["phases"]:
        raise ConfigError(f"participant does not declare phase {phase}: {specification['name']}")
    context_digest = sha256_bytes(context_path.read_bytes())
    # The frozen executable identity check is the final operation before the
    # fixed spawn vector is constructed and invoked.
    path, executable_descriptor, interpreter = _open_validated_executable(specification)
    try:
        argv = [str(path), "--protocol", PARTICIPANT_SCHEMA, "--phase", phase, "--context", str(context_path)]
        if runner is subprocess.run:
            result = _run_bounded_process(
                argv, specification["timeout_seconds"], executable_descriptor, interpreter
            )
        else:
            spawn_argv, executable, pass_fds = _participant_spawn_vector(
                argv, executable_descriptor, interpreter
            )
            result = runner(
                spawn_argv, check=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, timeout=specification["timeout_seconds"],
                executable=executable, pass_fds=pass_fds,
            )
    except subprocess.TimeoutExpired:
        blocker = (
            f"participant {specification['name']} timed out in {phase} "
            f"after {specification['timeout_seconds']} seconds"
        )
        attestation = {
            "protocol": PARTICIPANT_SCHEMA,
            "name": specification["name"],
            "phase": phase,
            "status": "failed",
            "blockers": [blocker],
            "observations": {},
            "context_digest": context_digest,
            "failure_kind": "timeout",
            "timeout_seconds": specification["timeout_seconds"],
        }
        return ParticipantResult(
            specification["name"], phase, "failed", (blocker,), {}, None,
            context_digest, sha256_bytes(canonical_bytes(attestation)), "", "",
            "timeout", specification["timeout_seconds"],
            "indeterminate" if phase == "post-switch-reconcile" and specification.get("mutation_authority") else None,
        )
    finally:
        os.close(executable_descriptor)
    if len(result.stdout.encode("utf-8")) > 65536:
        raise ConfigError(f"participant {specification['name']} output exceeds 65536 bytes")
    if len(result.stderr.encode("utf-8")) > 65536:
        raise ConfigError(f"participant {specification['name']} stderr exceeds 65536 bytes")
    document = _parse_output(result.stdout, specification["name"], phase)
    if document.get("rollback_handle") and (
        phase not in {"pre-switch", "post-switch-reconcile"}
        or not specification.get("mutation_authority")
        or not specification.get("rollback_authority")
    ):
        raise ConfigError(
            f"participant {specification['name']} emitted a rollback handle from a read-only phase"
        )
    mutation_state = document.get("mutation_state")
    if phase == "post-switch-reconcile" and specification.get("mutation_authority"):
        if mutation_state not in {"unchanged", "mutated", "partial"}:
            raise ConfigError(f"participant {specification['name']} did not attest its reconcile mutation state")
        if mutation_state in {"mutated", "partial"} and not document.get("rollback_handle"):
            raise ConfigError(f"participant {specification['name']} mutated without a rollback handle")
        if mutation_state == "unchanged" and document.get("rollback_handle"):
            raise ConfigError(f"participant {specification['name']} emitted a rollback handle without mutation")
    elif mutation_state is not None:
        raise ConfigError(f"participant {specification['name']} emitted mutation state from the wrong phase")
    if document["context_digest"] != context_digest:
        raise ConfigError(f"participant {specification['name']} attested the wrong context")
    reject_secrets(document)
    expected_exit = {"ok": 0, "blocked": 20, "failed": 21}[document["status"]]
    if result.returncode != expected_exit:
        raise ConfigError(
            f"participant {specification['name']} exit {result.returncode} does not match status {document['status']}"
        )
    return ParticipantResult(
        specification["name"],
        phase,
        document["status"],
        tuple(document["blockers"]),
        document["observations"],
        document.get("rollback_handle"),
        context_digest,
        sha256_bytes(canonical_bytes(document)),
        redact_text(result.stdout)[:8192],
        redact_text(result.stderr)[:8192],
        mutation_state=mutation_state,
    )


def _run_bounded_process(
    argv: list[str], timeout: int, executable_descriptor: int,
    interpreter: tuple[str, ...] | None,
) -> subprocess.CompletedProcess[str]:
    spawn_argv, executable, pass_fds = _participant_spawn_vector(
        argv, executable_descriptor, interpreter
    )
    process = subprocess.Popen(
        spawn_argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, executable=executable, pass_fds=pass_fds,
    )
    retained = {"stdout": bytearray(), "stderr": bytearray()}

    def pump(name: str, stream: Any) -> None:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            if len(retained[name]) <= 65536:
                retained[name].extend(chunk[: 65537 - len(retained[name])])

    threads = [
        threading.Thread(target=pump, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=pump, args=("stderr", process.stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout

    def terminate_group() -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            if process.poll() is None:
                process.kill()

    try:
        status = process.wait(timeout=timeout)
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in threads):
            raise subprocess.TimeoutExpired(argv, timeout)
    except subprocess.TimeoutExpired:
        terminate_group()
        process.wait()
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        for thread in threads:
            thread.join(1)
        raise
    return subprocess.CompletedProcess(
        argv, status,
        retained["stdout"].decode("utf-8", errors="replace"),
        retained["stderr"].decode("utf-8", errors="replace"),
    )


def _participant_spawn_vector(
    argv: list[str], executable_descriptor: int, interpreter: tuple[str, ...] | None
) -> tuple[list[str], str, tuple[int, ...]]:
    if interpreter is None:
        return argv, argv[0], ()
    return (
        [argv[0], *interpreter[1:], f"/dev/fd/{executable_descriptor}", *argv[1:]],
        interpreter[0],
        (executable_descriptor,),
    )


def run_phase(
    participants: Sequence[Mapping[str, Any]],
    phase: str,
    context_path: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[list[ParticipantResult], list[str]]:
    results: list[ParticipantResult] = []
    blockers: list[str] = []
    for participant in participants:
        if phase not in participant["phases"]:
            continue
        result = run_participant(participant, phase, context_path, runner)
        results.append(result)
        if result.status != "ok":
            blockers.extend(f"participant {result.name}: {item}" for item in result.blockers)
            if not result.blockers:
                blockers.append(f"participant {result.name} returned {result.status}")
    return results, blockers


def bounded_rollback(
    specification: Mapping[str, Any],
    context_path: Path,
    rollback_handle: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ParticipantResult:
    if "rollback" not in specification["phases"] or not specification.get("rollback_authority"):
        raise ConfigError(f"participant has no independently safe rollback: {specification['name']}")
    rollback_context = context_path.parent / f"{context_path.name}.rollback.json"
    require_private_directory(context_path.parent, "participant context directory")
    atomic_private_write(
        rollback_context,
        canonical_bytes({
            "schema": "nix-update-participant-rollback-context-v1",
            "participant": specification["name"],
            "declaration_digest": sha256_bytes(canonical_bytes(specification)),
            "parent_context": str(context_path),
            "parent_context_digest": sha256_bytes(context_path.read_bytes()),
            "rollback_handle": rollback_handle,
        }),
    )
    return run_participant(specification, "rollback", rollback_context, runner)


def _read_private_artifact(root: Path, path: Path) -> tuple[bytes, dict[str, Any]]:
    descriptor, raw, identity = _open_private_artifact(root, path)
    os.close(descriptor)
    return raw, identity


def _open_private_artifact(root: Path, path: Path) -> tuple[int, bytes, dict[str, Any]]:
    if (
        not root.is_absolute()
        or not path.is_absolute()
        or os.path.normpath(str(root)) != str(root)
        or os.path.normpath(str(path)) != str(path)
    ):
        raise ConfigError("operator participant paths must be normalized and absolute")
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ConfigError(f"operator participant escapes its reviewed root: {path}") from error
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ConfigError(f"operator participant is not a lexical root descendant: {path}")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | NOFOLLOW
    directory = os.open(root, directory_flags)
    try:
        _require_private_artifact_directory(os.fstat(directory), root)
        current = root
        for part in relative.parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = child
            current = current / part
            _require_private_artifact_directory(os.fstat(directory), current)
        descriptor = os.open(relative.parts[-1], os.O_RDONLY | NOFOLLOW, dir_fd=directory)
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) & 0o077
                or stat.S_IMODE(before.st_mode) & 0o111 == 0
                or before.st_nlink != 1
            ):
                raise ConfigError(f"operator participant is not an owner-only executable: {path}")
            raw = _read_descriptor(descriptor)
            after = os.fstat(descriptor)
            identity_fields = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size")
            if any(getattr(before, field) != getattr(after, field) for field in identity_fields):
                raise ConfigError(f"operator participant changed during capture: {path}")
            identity = _artifact_identity(path, after, raw)
            os.lseek(descriptor, 0, os.SEEK_SET)
        except Exception:
            os.close(descriptor)
            raise
    finally:
        os.close(directory)
    return descriptor, raw, identity


def _read_descriptor(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _validated_operator_interpreter(raw: bytes, name: str) -> tuple[str, ...]:
    first_line = raw.split(b"\n", 1)[0]
    if not first_line.startswith(b"#!") or len(first_line) > 255:
        raise ConfigError(f"operator participant must be a bounded shebang program: {name}")
    try:
        words = shlex.split(first_line[2:].decode("utf-8"), posix=True)
    except (UnicodeDecodeError, ValueError) as error:
        raise ConfigError(f"operator participant shebang is invalid: {name}") from error
    if not 1 <= len(words) <= 2:
        raise ConfigError(f"operator participant shebang must name one interpreter and optional argument: {name}")
    for index, word in enumerate(words):
        interpreter = Path(word)
        label = "interpreter" if index == 0 else "interpreter dispatch target"
        if not interpreter.is_absolute() or interpreter.is_symlink():
            raise ConfigError(
                f"operator participant {label} must be an exact absolute path: {name}"
            )
        metadata = interpreter.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_mode & 0o022
            or metadata.st_mode & 0o111 == 0
        ):
            raise ConfigError(
                f"operator participant {label} is not a root-owned executable: {name}"
            )
    return tuple(words)


def _require_private_artifact_directory(metadata: os.stat_result, path: Path) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ConfigError(f"operator participant ancestor is not an owner-only directory: {path}")


def _artifact_identity(path: Path, metadata: os.stat_result, raw: bytes) -> dict[str, Any]:
    return {
        "path": str(path),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": stat.S_IMODE(metadata.st_mode),
        "link_count": metadata.st_nlink,
        "size": metadata.st_size,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _install_frozen_artifact(root: Path, path: Path, raw: bytes) -> None:
    require_private_directory(root, "participant artifact root")
    if path.parent != root or os.path.normpath(str(path)) != str(path):
        raise ConfigError(f"frozen participant path escapes its private root: {path}")
    if path.exists() or path.is_symlink():
        existing, _ = _read_private_artifact(root, path)
        if existing != raw:
            raise ConfigError(f"existing frozen participant has different bytes: {path}")
        return
    directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | NOFOLLOW)
    try:
        _require_private_artifact_directory(os.fstat(directory), root)
        descriptor = os.open(
            path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o700,
            dir_fd=directory,
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700
                or metadata.st_nlink != 1
            ):
                raise ConfigError(f"frozen participant open identity is unsafe: {path}")
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
        os.fsync(directory)
    finally:
        os.close(directory)


def _parse_output(output: str, name: str, phase: str) -> dict[str, Any]:
    try:
        document = json.loads(output, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ConfigError) as error:
        raise ConfigError(f"participant {name} did not emit one strict JSON document") from error
    if not isinstance(document, dict):
        raise ConfigError(f"participant {name} output must be a JSON object")
    required = {"protocol", "name", "phase", "status", "blockers", "observations", "context_digest"}
    allowed = required | {"rollback_handle", "mutation_state"}
    if not required <= set(document) or not set(document) <= allowed:
        raise ConfigError(f"participant {name} output has missing or unknown keys")
    if document["protocol"] != PARTICIPANT_SCHEMA or document["name"] != name or document["phase"] != phase:
        raise ConfigError(f"participant {name} output identity mismatch")
    if document["status"] not in {"ok", "blocked", "failed"}:
        raise ConfigError(f"participant {name} output status is invalid")
    if not isinstance(document["blockers"], list) or any(not isinstance(item, str) for item in document["blockers"]):
        raise ConfigError(f"participant {name} blockers must be strings")
    if not isinstance(document["observations"], dict):
        raise ConfigError(f"participant {name} observations must be an object")
    if not isinstance(document["context_digest"], str) or len(document["context_digest"]) != 64:
        raise ConfigError(f"participant {name} context_digest must be a SHA-256")
    if "rollback_handle" in document and not isinstance(document["rollback_handle"], str):
        raise ConfigError(f"participant {name} rollback handle must be a string")
    if "mutation_state" in document and document["mutation_state"] not in {"unchanged", "mutated", "partial"}:
        raise ConfigError(f"participant {name} mutation state is invalid")
    if output.encode("utf-8") != (json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode():
        raise ConfigError(f"participant {name} output is not canonical JSON")
    return document


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError(f"duplicate JSON key: {key}")
        result[key] = value
    return result
