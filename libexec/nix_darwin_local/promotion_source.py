"""Campaign-internal receipt for an observational, already-published core source."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .deployment_paths import (
    create_private_directory, exclusive_private_write, fsync_directory,
    require_private_directory, require_private_file,
)
from .deployment_plan import canonical_bytes, sha256_bytes
from .paths import ConfigError
from .promotion_contract import HEX_REVISION, reject_promotion_secrets, strict_json_loads
from .promotion_plan import HEX_SHA256, _validate_tool_identity
from .promotion_qualification import promotion_tool_identity

SOURCE_RECEIPT_SCHEMA = "nix-update-core-source-receipt-v1"
RECEIPT_FIELDS = {
    "schema", "status", "input", "revision", "repository", "execution_repository",
    "tool", "impact", "no_activation", "receipt_digest",
}


@dataclass(frozen=True)
class SourceReceipt:
    directory: Path
    document: dict[str, Any]
    digest: str


def validate_source_receipt(document: Any, *, allow_unsigned: bool = False) -> None:
    local = isinstance(document, dict) and document.get("schema") == "nix-update-core-source-receipt-v2"
    if not isinstance(document, dict) or set(document) != RECEIPT_FIELDS | ({"source_authority"} if local else set()):
        raise ConfigError("source receipt fields are invalid")
    if document["schema"] != SOURCE_RECEIPT_SCHEMA and not local:
        raise ConfigError("source receipt schema must not be a native promotion plan schema or unknown variant")
    if document["status"] != "ALREADY_SELECTED" or document["input"] != "core":
        raise ConfigError("source receipt must be an already-selected core source")
    if document["impact"] is not None:
        raise ConfigError("source receipt impact must be null (no host claim)")
    if document["no_activation"] is not True:
        raise ConfigError("source receipt must forbid activation")
    revision = document["revision"]
    if not isinstance(revision, str) or HEX_REVISION.fullmatch(revision) is None:
        raise ConfigError("source receipt revision is invalid")
    for field in ("repository", "execution_repository"):
        value = document[field]
        expected = {"commit", "repository_root"} | ({"head_commit"} if field == "repository" else set())
        if not isinstance(value, dict) or set(value) != expected:
            raise ConfigError("source receipt repository fields are invalid")
        if any(not isinstance(value[key], str) or HEX_REVISION.fullmatch(value[key]) is None
               for key in expected - {"repository_root"}):
            raise ConfigError("source receipt repository commit is invalid")
        if not isinstance(value["repository_root"], str) or not Path(value["repository_root"]).is_absolute():
            raise ConfigError("source receipt repository root is invalid")
    if document["repository"]["commit"] != revision:
        raise ConfigError("source receipt repository commit differs from requested revision")
    if local:
        authority = document["source_authority"]
        from .local_source import validate_source_identity
        from .promotion_contract import normalize_nix_repository_url
        validate_source_identity(authority)
        normalize_nix_repository_url(authority["repository"])
        if (not isinstance(authority, dict) or authority.get("revision") != revision
                or authority.get("checkout") != document["repository"]["repository_root"]
                or not isinstance(authority.get("clean"), bool) or authority.get("operation_state") != "clean"):
            raise ConfigError("source receipt local authority differs from the selected commit")
    _validate_tool_identity(document["tool"])
    digest = document["receipt_digest"]
    if allow_unsigned:
        if digest != "":
            raise ConfigError("unsigned source receipt digest must be empty")
    elif not isinstance(digest, str) or HEX_SHA256.fullmatch(digest) is None:
        raise ConfigError("source receipt digest is invalid")


def load_source_receipt(path: Path) -> SourceReceipt:
    if not path.is_absolute():
        raise ConfigError("source receipt path must be absolute")
    directory = path if path.is_dir() else path.parent
    json_path = directory / "receipt.json" if path.is_dir() else path
    require_private_directory(directory, "source receipt directory")
    if json_path.name != "receipt.json" or {item.name for item in directory.iterdir()} != {"receipt.json"}:
        raise ConfigError("source receipt directory contains an unbound artifact")
    require_private_file(json_path, "source receipt")
    raw = json_path.read_bytes()
    try:
        document = strict_json_loads(raw.decode("utf-8"), "source receipt")
    except UnicodeDecodeError as error:
        raise ConfigError("source receipt is not valid UTF-8") from error
    if not isinstance(document, dict) or raw != canonical_bytes(document):
        raise ConfigError("source receipt bytes are not canonical")
    validate_source_receipt(document)
    unsigned = {**document, "receipt_digest": ""}
    if sha256_bytes(canonical_bytes(unsigned)) != document["receipt_digest"]:
        raise ConfigError("source receipt digest does not match canonical content")
    reject_promotion_secrets(document)
    return SourceReceipt(directory, document, document["receipt_digest"])


def write_source_receipt(directory: Path, document: dict[str, Any]) -> SourceReceipt:
    if not directory.is_absolute():
        raise ConfigError("source receipt directory must be absolute")
    validate_source_receipt(document, allow_unsigned=True)
    reject_promotion_secrets(document)
    create_private_directory(directory, "source receipt directory")
    final = {**document, "receipt_digest": sha256_bytes(canonical_bytes(document))}
    exclusive_private_write(directory / "receipt.json", canonical_bytes(final))
    fsync_directory(directory)
    return load_source_receipt(directory)


def _git(workflow: Any, repository: Path, label: str, *arguments: str) -> str:
    result = workflow.command(label, ["git", *arguments], repository)
    if result.returncode:
        raise ConfigError(f"unable to {label}")
    return workflow.complete_stdout(label).strip()


def check_tracked_tool_drift(workflow: Any, tool_root: Path, git_root: Path) -> dict[str, Any]:
    """Compare actual tool bytes/modes with Git blobs, including skip-worktree files.

    The execution closure must have Git-blob-identical bytes: EOL conversion or
    clean/smudge transformations are unsupported. Filter-normalized equality
    cannot prove that the bytes Python actually executes are the published tool.
    This requirement applies only to the tool closure, not operational metadata.
    """
    tool = promotion_tool_identity(tool_root)
    relative = tool_root.relative_to(git_root)
    paths = [(relative / "bin/nix-update").as_posix(), (relative / "libexec/nix_darwin_local").as_posix()]
    listing = _git(workflow, git_root, "read tracked tool tree", "ls-tree", "-rz", "HEAD", "--", *paths)
    entries = {}
    for entry in listing.split("\0"):
        if entry:
            header, path = entry.split("\t", 1)
            mode, kind, digest = header.split()
            if kind != "blob" or mode not in {"100644", "100755"}:
                raise ConfigError("tracked tool source must contain ordinary Git blobs")
            entries[path] = (mode, digest)
    live = {(relative / item["path"]).as_posix(): item for item in tool["files"]}
    if entries.keys() != live.keys():
        raise ConfigError("tracked tool drift detected: source closure membership differs")
    for path, (mode, digest) in entries.items():
        source = git_root / path
        raw = source.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        executable = bool(source.stat().st_mode & 0o111)
        if actual != digest or executable != (mode == "100755"):
            raise ConfigError("tracked tool drift detected: source bytes or executable mode differ")
    if promotion_tool_identity(tool_root) != tool:
        raise ConfigError("promotion tool identity changed during source observation")
    return tool


def prepare_core_source(
    workflow: Any, repository: Path, input_name: str, revision: str,
    output: Path, config: Any, stage: Mapping[str, Any],
) -> SourceReceipt:
    from .promotion_workflow import _prepare_output

    if input_name != "core" or stage.get("kind") != "core":
        raise ConfigError("source receipt requires the observational core stage")
    if not isinstance(revision, str) or HEX_REVISION.fullmatch(revision) is None:
        raise ConfigError("core source revision must be a 40-character lowercase hex string")
    repository = repository.resolve(strict=True)
    result = workflow.command("verify core git revision object", ["git", "cat-file", "-t", revision], repository)
    if result.returncode:
        raise ConfigError("core repository does not contain requested revision")
    kind = workflow.complete_stdout("verify core git revision object").strip()
    if kind != "commit":
        raise ConfigError(f"requested revision is a {kind}, not a commit")
    if _git(workflow, repository, "verify core commit", "rev-parse", "--verify", f"{revision}^{{commit}}") != revision:
        raise ConfigError("core source resolved commit does not match requested revision")
    root = Path(_git(workflow, repository, "read core root", "rev-parse", "--show-toplevel")).resolve(strict=True)
    head = _git(workflow, repository, "read core HEAD", "rev-parse", "HEAD")
    source_authority = None
    if getattr(config, "sources", ()):
        from .local_source import observe_source
        from .source_catalog import lookup_by_key
        entry = lookup_by_key(config.sources, "core")
        if entry is None or entry.checkout.resolve() != root:
            raise ConfigError("core source checkout disagrees with the source catalog")
        source_authority = dict(observe_source(workflow, entry, revision))
    tool_root = workflow.core_dir.resolve(strict=True)
    tool_git = Path(_git(workflow, tool_root, "read execution root", "rev-parse", "--show-toplevel")).resolve(strict=True)
    tool_head = _git(workflow, tool_git, "read execution HEAD", "rev-parse", "HEAD")
    tool = check_tracked_tool_drift(workflow, tool_root, tool_git)
    if _git(workflow, tool_git, "revalidate execution HEAD", "rev-parse", "HEAD") != tool_head:
        raise ConfigError("execution repository changed during source observation")
    _prepare_output(output, repository, root)
    _prepare_output(output, tool_root, tool_git)
    if source_authority is not None and dict(observe_source(workflow, entry, revision)) != source_authority:
        raise ConfigError("core source catalog authority changed during observation")
    return write_source_receipt(output, {
        "schema": SOURCE_RECEIPT_SCHEMA if source_authority is None else "nix-update-core-source-receipt-v2",
        **({"source_authority": source_authority} if source_authority is not None else {}),
        "status": "ALREADY_SELECTED", "input": "core",
        "revision": revision,
        "repository": {"commit": revision, "head_commit": head, "repository_root": os.fspath(root)},
        "execution_repository": {"commit": tool_head, "repository_root": os.fspath(tool_git)},
        "tool": tool, "impact": None, "no_activation": True, "receipt_digest": "",
    })
