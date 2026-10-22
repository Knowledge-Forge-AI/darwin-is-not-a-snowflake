from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .paths import ConfigError
from .promotion_contract import (
    HEX_REVISION,
    SAFE_IDENTITY,
    reject_promotion_secrets,
    repository_urls_match,
    validate_release_ref,
)
from .source_catalog import (
    Publication,
    SourceCatalog,
    SourceCatalogEntry,
    SourceError,
    canonical_repository_identity,
    coerce_catalog_entry,
    coerce_source_catalog,
)


GIT_OPERATION_PATHS = (
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "BISECT_LOG",
    "BISECT_START",
    "BISECT_HEAD",
    "rebase-merge",
    "rebase-apply",
    "sequencer",
    "AUTO_MERGE",
    "index.lock",
    "HEAD.lock",
    "packed-refs.lock",
    "config.lock",
)

SOURCE_IDENTITY_FIELDS = frozenset({
    "key", "checkout", "repository", "release_ref", "revision", "head_ref",
    "head_revision", "clean", "operation_state", "store_path", "nar_hash",
    "publication",
})
_SRI_NAR_HASH = re.compile(r"sha256-[A-Za-z0-9+/]+={0,2}\Z")

# Git and Nix permit process environment variables to replace the repository,
# object database, refs, or configuration consulted by a command.  Local
# source authority is fail-closed when any such override is present.  The
# values are intentionally never included in diagnostics.
_SOURCE_ENV_PREFIXES = (
    "GIT_CONFIG",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_QUARANTINE_PATH",
    "GIT_REPLACE_REF_BASE",
    "GIT_EXEC_PATH",
    "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_ATTR_",
    "GIT_ATTRIBUTES_FILE",
)
_SOURCE_ENV_NAMES = frozenset({
    "NIX_CONFIG", "NIX_USER_CONF_FILES", "NIX_CONF_DIR", "NIX_REMOTE",
    "GIT_GRAFT_FILE", "GIT_SHALLOW_FILE",
})


def _validate_source_environment() -> None:
    blocked = sorted(
        name for name in os.environ
        if name in _SOURCE_ENV_NAMES
        or any(name.startswith(prefix) for prefix in _SOURCE_ENV_PREFIXES)
    )
    if blocked:
        raise SourceError(
            "source observation refuses environment override(s): "
            + ", ".join(blocked)
        )


def _git_argv(*arguments: str) -> list[str]:
    return ["git", "--no-replace-objects", "--no-optional-locks", *arguments]


def _run_git(
    workflow: Any, label: str, checkout: Path, *arguments: str
) -> Any:
    return workflow.command(label, _git_argv(*arguments), checkout)


def _serialize_publication(publication: Any) -> dict[str, str] | None:
    if publication is None:
        return None
    if isinstance(publication, Publication):
        data = asdict(publication)
    elif isinstance(publication, Mapping):
        data = dict(publication)
    else:
        raise SourceError("source publication metadata must be a mapping")
    if set(data) != {"remote", "ref"}:
        raise SourceError("source publication metadata fields are invalid")
    remote, ref = data["remote"], data["ref"]
    if not isinstance(remote, str) or SAFE_IDENTITY.fullmatch(remote) is None:
        raise SourceError("source publication remote is invalid")
    if not isinstance(ref, str):
        raise SourceError("source publication ref is invalid")
    normalized_ref = _normalize_ref(ref)
    return {"remote": remote, "ref": normalized_ref}


@dataclass(frozen=True)
class LocalSourceIdentity(Mapping[str, Any]):
    key: str
    checkout: str
    repository: str
    release_ref: str
    revision: str
    head_ref: str
    head_revision: str
    clean: bool = True
    operation_state: str = "clean"
    store_path: str | None = None
    nar_hash: str | None = None
    publication: Any = None
    locked_metadata: Mapping[str, Any] | None = field(
        default=None, repr=False, compare=False
    )

    @property
    def path(self) -> str | None:
        """Return the immutable Nix store path, when materialized."""
        return self.store_path

    @property
    def locked(self) -> dict[str, Any]:
        """Return a detached copy of Nix's immutable Git realization fields."""
        return dict(self.locked_metadata or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "checkout": self.checkout,
            "repository": self.repository,
            "release_ref": self.release_ref,
            "revision": self.revision,
            "head_ref": self.head_ref,
            "head_revision": self.head_revision,
            "clean": self.clean,
            "operation_state": self.operation_state,
            "store_path": self.store_path,
            "nar_hash": self.nar_hash,
            "publication": _serialize_publication(self.publication),
        }

    to_dict = as_dict

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def __iter__(self):
        return iter(self.as_dict())

    def __len__(self) -> int:
        return len(self.as_dict())

    def get(self, key: str, default: Any = None) -> Any:
        return self.as_dict().get(key, default)




def _normalize_ref(ref: str) -> str:
    if not isinstance(ref, str) or not ref:
        raise SourceError("release ref must be a non-empty string")
    try:
        return validate_release_ref(ref)
    except ConfigError as error:
        raise SourceError(f"invalid release ref {ref!r}: {error}") from error


def _normalize_locked_ref(ref: Any) -> str:
    """Normalize Nix's branch shorthand for lock/catalog comparison only."""
    if not isinstance(ref, str) or not ref:
        raise SourceError("locked source ref must be a non-empty string")
    candidate = ref if ref.startswith("refs/") else f"refs/heads/{ref}"
    try:
        return validate_release_ref(candidate)
    except ConfigError as error:
        raise SourceError(f"invalid locked source ref {ref!r}: {error}") from error


def _validate_absolute_path(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise SourceError(f"source identity {field_name} must be an absolute path")
    if "\x00" in value or os.path.normpath(value) != value:
        raise SourceError(f"source identity {field_name} is not normalized")


def validate_source_identity(
    value: Any, *, materialized: bool = False
) -> None:
    """Validate the stable, serialized twelve-field source identity shape."""
    if isinstance(value, LocalSourceIdentity):
        document = value.as_dict()
    elif isinstance(value, Mapping):
        document = dict(value)
    else:
        raise SourceError("source identity must be a mapping")
    if set(document) != SOURCE_IDENTITY_FIELDS:
        raise SourceError("source identity fields are invalid")

    for field_name in (
        "key", "checkout", "repository", "release_ref", "revision", "head_ref",
        "head_revision", "operation_state",
    ):
        if not isinstance(document[field_name], str) or not document[field_name]:
            raise SourceError(f"source identity {field_name} is invalid")
    _validate_absolute_path(document["checkout"], "checkout")
    try:
        reject_promotion_secrets(document["repository"])
        canonical_repository_identity(document["repository"], allow_file=True)
        validate_release_ref(document["release_ref"])
    except ConfigError as error:
        raise SourceError(f"source identity repository or ref is invalid: {error}") from error
    if document["head_ref"] != "HEAD":
        try:
            validate_release_ref(document["head_ref"])
        except ConfigError as error:
            raise SourceError(f"source identity head_ref is invalid: {error}") from error
    if HEX_REVISION.fullmatch(document["revision"]) is None:
        raise SourceError("source identity revision is invalid")
    if HEX_REVISION.fullmatch(document["head_revision"]) is None:
        raise SourceError("source identity head_revision is invalid")
    if not isinstance(document["clean"], bool) or document["operation_state"] != "clean":
        raise SourceError("source identity operation_state is invalid")

    store_path, nar_hash = document["store_path"], document["nar_hash"]
    if (store_path is None) != (nar_hash is None):
        raise SourceError("source identity store_path and nar_hash must be paired")
    if materialized and store_path is None:
        raise SourceError("materialized source identity requires store_path and nar_hash")
    if store_path is not None:
        _validate_absolute_path(store_path, "store_path")
        if not store_path.startswith("/nix/store/"):
            raise SourceError("source identity store_path must be a Nix store path")
        if not isinstance(nar_hash, str) or _SRI_NAR_HASH.fullmatch(nar_hash) is None:
            raise SourceError("source identity nar_hash is invalid")

    publication = document["publication"]
    if publication is not None:
        _serialize_publication(publication)


def _read_source_state(
    workflow: Any,
    entry_key: str,
    checkout: Path,
    release_ref: str,
    revision: str,
    phase: str,
    require_exact_head: bool = False,
) -> tuple[str, str | None, str, str]:
    ref_label = f"Git verify release_ref {phase} ({entry_key})"
    ref_result = _run_git(
        workflow, ref_label, checkout,
        "rev-parse", "--verify", f"{release_ref}^{{commit}}",
    )
    if require_exact_head and ref_result.returncode != 0:
        raise SourceError(f"release ref {release_ref} does not exist in {entry_key}")
    ref_commit = ""
    if ref_result.returncode == 0:
        ref_commit = workflow.complete_stdout(ref_label).strip()
    if require_exact_head and ref_commit != revision:
        raise SourceError(
            f"release ref {release_ref} in {entry_key} resolves to {ref_commit}, "
            f"expected requested revision {revision}"
        )

    head_label = f"Git HEAD commit {phase} ({entry_key})"
    head_result = _run_git(workflow, head_label, checkout, "rev-parse", "HEAD")
    if head_result.returncode:
        raise SourceError(f"unable to obtain HEAD commit for {entry_key}")
    head_commit = workflow.complete_stdout(head_label).strip()

    symbolic_label = f"Git HEAD symbolic-ref {phase} ({entry_key})"
    symbolic_result = _run_git(
        workflow, symbolic_label, checkout, "symbolic-ref", "-q", "HEAD"
    )
    if symbolic_result.returncode == 0:
        symbolic_ref = workflow.complete_stdout(symbolic_label).strip()
        if require_exact_head:
            if symbolic_ref != release_ref:
                raise SourceError(
                    f"HEAD ref {symbolic_ref} does not match requested ref {release_ref}"
                )
            if head_commit != revision:
                raise SourceError(
                    f"HEAD commit {head_commit} does not match requested revision {revision}"
                )
        return ref_commit, symbolic_ref, symbolic_ref, head_commit
    if require_exact_head:
        if symbolic_result.returncode != 1:
            raise SourceError(f"unable to inspect HEAD symbolic ref for {entry_key}")
        if head_commit != revision:
            raise SourceError(
                f"HEAD is detached at unrelated commit {head_commit}, expected requested revision {revision}"
            )
    return ref_commit, None, release_ref, head_commit


def _check_no_replacement_refs(workflow: Any, entry_key: str, checkout: Path, phase: str) -> None:
    label = f"Git replacement refs {phase} ({entry_key})"
    result = _run_git(workflow, label, checkout, "replace", "-l")
    if result.returncode:
        raise SourceError(f"unable to inspect Git replacement refs for {entry_key}")
    if workflow.complete_stdout(label).strip():
        raise SourceError(f"repository for {entry_key} contains Git replacement refs")


def _git_directory(
    workflow: Any, entry_key: str, checkout: Path, phase: str
) -> Path:
    label = f"Git dir {phase} ({entry_key})"
    result = _run_git(workflow, label, checkout, "rev-parse", "--git-dir")
    if result.returncode:
        raise SourceError(f"unable to determine git-dir for {entry_key}")
    raw = Path(workflow.complete_stdout(label).strip())
    return (raw if raw.is_absolute() else checkout / raw).resolve()


def _check_active_git_operations(
    workflow: Any, entry_key: str, checkout: Path, phase: str
) -> None:
    git_dir = _git_directory(workflow, entry_key, checkout, phase)
    for operation in GIT_OPERATION_PATHS:
        label = f"Git op check {phase} {operation} ({entry_key})"
        result = _run_git(workflow, label, checkout, "rev-parse", "--git-path", operation)
        if result.returncode:
            raise SourceError(f"Git operation probe failed for {entry_key}: {operation}")
        candidate = Path(workflow.complete_stdout(label).strip())
        if not candidate.is_absolute():
            candidate = checkout / candidate
        if candidate.exists() or candidate.is_symlink():
            raise SourceError(f"repository for {entry_key} has active Git operation: {operation}")
        direct_candidate = git_dir / operation
        if direct_candidate.exists() or direct_candidate.is_symlink():
            raise SourceError(f"repository for {entry_key} has active Git operation: {operation}")


def _check_no_grafts_or_shallow(
    workflow: Any, entry_key: str, checkout: Path, phase: str
) -> None:
    git_dir = _git_directory(workflow, entry_key, checkout, phase)
    for name in ("info/grafts", "shallow"):
        label = f"Git {name} check {phase} ({entry_key})"
        result = _run_git(workflow, label, checkout, "rev-parse", "--git-path", name)
        if result.returncode:
            raise SourceError(f"Git {name} probe failed for {entry_key}")
        candidate = Path(workflow.complete_stdout(label).strip())
        if not candidate.is_absolute():
            candidate = checkout / candidate
        if candidate.exists() or candidate.is_symlink():
            raise SourceError(f"repository for {entry_key} contains graft or shallow file: {name}")
        direct_candidate = git_dir / name
        if direct_candidate.exists() or direct_candidate.is_symlink():
            raise SourceError(f"repository for {entry_key} contains graft or shallow file: {name}")
    shallow_label = f"Git is shallow {phase} ({entry_key})"
    shallow_res = _run_git(workflow, shallow_label, checkout, "rev-parse", "--is-shallow-repository")
    if shallow_res.returncode or workflow.complete_stdout(shallow_label).strip() != "false":
        raise SourceError(f"repository for {entry_key} has unproven non-shallow history")




def check_git_operations(workflow: Any, checkout: Path, key: str) -> None:
    """Recheck Git operation markers without requiring a clean worktree."""
    _validate_source_environment()
    _check_active_git_operations(workflow, key, Path(checkout), "selected")


def _git_blob_digest(raw: bytes) -> str:
    header = f"blob {len(raw)}\0".encode("ascii")
    return hashlib.sha1(header + raw).hexdigest()


def _nix_string_literal(value: str) -> str:
    """Encode a bounded value as a Nix double-quoted string literal."""
    if not isinstance(value, str) or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise SourceError("source value contains a prohibited Nix string character")
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _check_tracked_source_drift(
    workflow: Any, entry_key: str, checkout: Path, repo_root: Path, phase: str
) -> None:
    status_label = f"Git status {phase} ({entry_key})"
    status_result = _run_git(
        workflow, status_label, checkout,
        "status", "--porcelain=v1", "--untracked-files=no",
    )
    if status_result.returncode:
        raise SourceError(f"unable to check Git status for {entry_key}")
    status_out = workflow.complete_stdout(status_label).strip()
    if status_out:
        raise SourceError(f"repository for {entry_key} has tracked drift:\n{status_out}")

    tree_label = f"Git tracked tree {phase} ({entry_key})"
    tree_result = _run_git(
        workflow, tree_label, checkout,
        "ls-tree", "-rz", "-r", "--full-tree", "HEAD",
    )
    if tree_result.returncode:
        raise SourceError(f"unable to read committed source tree for {entry_key}")
    tree_output = workflow.complete_stdout(tree_label)
    for record in tree_output.split("\0"):
        if not record:
            continue
        try:
            header, relative = record.split("\t", 1)
            mode, kind, digest = header.split()
        except ValueError as error:
            raise SourceError(f"committed source tree for {entry_key} is invalid") from error
        if kind != "blob" or mode not in {"100644", "100755", "120000"}:
            raise SourceError(f"committed source tree for {entry_key} contains a non-ordinary entry")
        source = repo_root / relative
        try:
            metadata = source.lstat()
        except OSError as error:
            raise SourceError(f"repository for {entry_key} has tracked drift: missing {relative}") from error
        if mode == "120000":
            if not stat.S_ISLNK(metadata.st_mode):
                raise SourceError(f"repository for {entry_key} has tracked drift: {relative}")
            raw = os.fsencode(os.readlink(source))
            executable = False
        else:
            if not stat.S_ISREG(metadata.st_mode):
                raise SourceError(f"repository for {entry_key} has tracked drift: {relative}")
            raw = source.read_bytes()
            executable = bool(metadata.st_mode & 0o111)
        if _git_blob_digest(raw) != digest or executable != (mode == "100755"):
            raise SourceError(f"repository for {entry_key} has tracked drift: {relative}")


def observe_source(
    workflow: Any,
    catalog_entry: Any,
    revision: str,
    expected_repository: str | None = None,
    expected_ref: str | None = None,
    require_clean: bool = False,
    require_exact_head: bool = False,
) -> LocalSourceIdentity:
    _validate_source_environment()
    entry = coerce_catalog_entry(catalog_entry)
    if not isinstance(entry.key, str) or not entry.key:
        raise SourceError("source catalog key must be a non-empty string")
    if not isinstance(entry.repository, str) or not entry.repository:
        raise SourceError(f"source repository for {entry.key} must be a non-empty string")
    reject_promotion_secrets(entry.repository)
    publication = _serialize_publication(entry.publication)
    checkout = Path(entry.checkout)
    if not checkout.exists() or not checkout.is_dir():
        raise SourceError(f"source checkout path does not exist or is not a directory: {checkout}")

    if not isinstance(revision, str) or HEX_REVISION.fullmatch(revision) is None:
        raise SourceError(f"source revision must be a 40-character lowercase hex string: {revision!r}")

    release_ref = _normalize_ref(entry.release_ref)

    if expected_ref is not None:
        norm_expected = _normalize_ref(expected_ref)
        if norm_expected != release_ref:
            raise SourceError(
                f"source release ref {release_ref} does not match expected {norm_expected}"
            )

    if expected_repository is not None:
        try:
            repository_matches = repository_urls_match(
                expected_repository, entry.repository, allow_file=True
            )
        except ConfigError as error:
            raise SourceError(f"source repository URL is invalid: {error}") from error
        if not repository_matches:
            raise SourceError(
                f"source repository URL {entry.repository} does not match expected {expected_repository}"
            )

    # 1. Prove an ordinary non-bare Git worktree before consulting any source data.
    worktree_label = f"Git worktree ({entry.key})"
    worktree_result = _run_git(
        workflow, worktree_label, checkout, "rev-parse", "--is-inside-work-tree"
    )
    if (
        worktree_result.returncode
        or workflow.complete_stdout(worktree_label).strip() != "true"
    ):
        raise SourceError(f"source checkout {checkout} is not an ordinary Git worktree")
    bare_label = f"Git bare repository ({entry.key})"
    bare_result = _run_git(
        workflow, bare_label, checkout, "rev-parse", "--is-bare-repository"
    )
    if bare_result.returncode or workflow.complete_stdout(bare_label).strip() != "false":
        raise SourceError(f"source checkout {checkout} is not an ordinary Git worktree")

    toplevel_label = f"Git toplevel ({entry.key})"
    toplevel_res = _run_git(workflow, toplevel_label, checkout, "rev-parse", "--show-toplevel")
    if toplevel_res.returncode:
        raise SourceError(f"source checkout {checkout} is not an accessible Git repository")
    repo_root = Path(workflow.complete_stdout(toplevel_label).strip()).resolve(strict=True)

    try:
        checkout.resolve(strict=True).relative_to(repo_root)
    except (OSError, ValueError) as error:
        raise SourceError(f"source checkout {checkout} is outside repository root {repo_root}") from error

    if require_clean:
        _check_tracked_source_drift(workflow, entry.key, checkout, repo_root, "initial")

    _check_no_replacement_refs(workflow, entry.key, checkout, "initial")
    _check_no_grafts_or_shallow(workflow, entry.key, checkout, "initial")

    # 2. Check requested revision exists in repository and is a commit object.
    rev_kind_label = f"Git revision kind ({entry.key})"
    rev_kind_res = _run_git(workflow, rev_kind_label, checkout, "cat-file", "-t", revision)
    if rev_kind_res.returncode:
        raise SourceError(f"repository for {entry.key} does not contain requested revision {revision}")
    kind = workflow.complete_stdout(rev_kind_label).strip()
    if kind != "commit":
        raise SourceError(f"requested revision {revision} in {entry.key} is a {kind}, not a commit")

    # 3. Read the exact ref and HEAD, then repeat every source-state check at
    # the end so a concurrent ref/worktree change cannot be accepted.
    ref_commit, symbolic_head_ref, head_ref, head_commit = _read_source_state(
        workflow, entry.key, checkout, release_ref, revision, "initial",
        require_exact_head=require_exact_head,
    )
    _check_active_git_operations(workflow, entry.key, checkout, "initial")

    status_label = f"Git status ({entry.key})"
    status_result = _run_git(
        workflow, status_label, checkout,
        "status", "--porcelain=v1", "--untracked-files=no",
    )
    is_clean = (status_result.returncode == 0 and not workflow.complete_stdout(status_label).strip())
    if require_clean and not is_clean:
        raise SourceError(f"repository for {entry.key} has tracked drift:\n{workflow.complete_stdout(status_label).strip()}")

    _validate_source_environment()
    _check_no_replacement_refs(workflow, entry.key, checkout, "final")
    _check_no_grafts_or_shallow(workflow, entry.key, checkout, "final")
    _check_active_git_operations(workflow, entry.key, checkout, "final")
    if require_clean:
        _check_tracked_source_drift(workflow, entry.key, checkout, repo_root, "final")

    if require_exact_head:
        final_ref, final_symbolic_head_ref, final_head_ref, final_head = _read_source_state(
            workflow, entry.key, checkout, release_ref, revision, "final",
            require_exact_head=True,
        )
        if (
            final_ref != ref_commit
            or final_symbolic_head_ref != symbolic_head_ref
            or final_head_ref != head_ref
            or final_head != head_commit
        ):
            raise SourceError(f"source ref or HEAD changed during observation for {entry.key}")

    identity = LocalSourceIdentity(
        key=entry.key,
        checkout=os.fspath(checkout.resolve(strict=True)),
        repository=entry.repository,
        release_ref=release_ref,
        revision=revision,
        head_ref=head_ref,
        head_revision=head_commit,
        clean=is_clean,
        operation_state="clean",
        store_path=None,
        nar_hash=None,
        publication=publication,
    )
    validate_source_identity(identity)
    return identity


def materialize_source(
    workflow: Any,
    catalog_entry: Any,
    revision: str,
    expected_repository: str | None = None,
    expected_ref: str | None = None,
    require_clean: bool = False,
    require_exact_head: bool = False,
) -> LocalSourceIdentity:
    observed = observe_source(
        workflow,
        catalog_entry,
        revision,
        expected_repository=expected_repository,
        expected_ref=expected_ref,
        require_clean=require_clean,
        require_exact_head=require_exact_head,
    )

    checkout_path = Path(observed.checkout).resolve(strict=True)
    # fetchTree realizes the exact Git object without evaluating a flake at the
    # checkout root.  Values are encoded as Nix strings, so path/ref/rev bytes
    # cannot become Nix interpolation or shell syntax.
    fetch_tree_expression = f"""let
  snapshot = builtins.fetchTree {{
    type = \"git\";
    url = {_nix_string_literal(checkout_path.as_uri())};
    rev = {_nix_string_literal(revision)};
  }};
in (builtins.removeAttrs snapshot [ \"outPath\" ]) // {{ path = snapshot.outPath; }}
"""
    meta_label = f"materialize source ({observed.key})"
    meta_cmd = [
        "nix", "eval", "--json", "--impure", "--offline",
        "--expr", fetch_tree_expression,
    ]
    meta_res = workflow.command(meta_label, meta_cmd, checkout_path)
    if meta_res.returncode:
        raise SourceError(f"unable to materialize source {observed.key} via Nix fetchTree")

    meta_raw = workflow.complete_stdout(meta_label)
    try:
        data = json.loads(meta_raw)
    except json.JSONDecodeError as error:
        raise SourceError(f"Nix fetchTree for {observed.key} returned invalid JSON") from error

    if not isinstance(data, dict):
        raise SourceError("Nix fetchTree output must be a JSON object")

    store_path = data.pop("path", None)
    locked = dict(data)

    locked_rev = locked.get("rev")
    if locked_rev != revision:
        raise SourceError(
            f"materialized locked rev {locked_rev} does not match requested revision {revision}"
        )

    nar_hash = locked.get("narHash")
    if not isinstance(nar_hash, str) or not nar_hash.startswith("sha256-"):
        raise SourceError(f"materialized locked narHash is missing or invalid: {nar_hash!r}")

    if not isinstance(store_path, str) or not store_path.startswith("/nix/store/"):
        raise SourceError(f"materialized store path is missing or invalid: {store_path!r}")

    if not Path(store_path).exists():
        raise SourceError(f"materialized store path does not exist in store: {store_path}")

    # Post-validation observation proves no source mutation occurred during or
    # after realization.  The returned lock metadata remains exactly what Nix
    # produced; no narHash or other immutable field is synthesized here.
    try:
        revalidated = observe_source(
            workflow,
            catalog_entry,
            revision,
            expected_repository=expected_repository,
            expected_ref=expected_ref,
            require_clean=require_clean,
            require_exact_head=require_exact_head,
        )
    except ConfigError as error:
        raise SourceError(f"source revalidation failed after snapshot: {error}") from error
    if require_exact_head:
        if revalidated.head_revision != observed.head_revision or revalidated.head_ref != observed.head_ref:
            raise SourceError(f"source ref or HEAD mutated after snapshot for {observed.key}")

    result = LocalSourceIdentity(
        key=observed.key,
        checkout=observed.checkout,
        repository=observed.repository,
        release_ref=observed.release_ref,
        revision=observed.revision,
        head_ref=revalidated.head_ref,
        head_revision=revalidated.head_revision,
        clean=revalidated.clean,
        operation_state="clean",
        store_path=store_path,
        nar_hash=nar_hash,
        publication=observed.publication,
        locked_metadata=locked,
    )
    validate_source_identity(result, materialized=True)
    return result


def realize_catalog_locked_nodes(
    workflow: Any,
    lock: Mapping[str, Any] | Path | str,
    catalog: Mapping[str, Any] | Sequence[Any] | Any,
    *,
    expected_revisions: Mapping[str, str] | None = None,
) -> dict[str, LocalSourceIdentity]:
    if isinstance(lock, (Path, str)):
        path = Path(lock)
        if path.is_dir():
            path = path / "flake.lock"
        if not path.is_file():
            raise SourceError(f"lock file not found: {path}")
        try:
            lock_dict = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise SourceError(f"lock file contains invalid JSON: {path}") from error
    elif isinstance(lock, Mapping):
        lock_dict = dict(lock)
    else:
        raise SourceError("lock must be a Mapping, Path, or str")

    nodes = lock_dict.get("nodes")
    if not isinstance(nodes, dict):
        raise SourceError("lock file nodes mapping is missing or invalid")

    entries = list(coerce_source_catalog(catalog).entries)

    realized: dict[str, LocalSourceIdentity] = {}

    for node_name, node_data in sorted(nodes.items()):
        if not isinstance(node_data, dict):
            continue
        locked = node_data.get("locked")
        if not isinstance(locked, dict):
            continue
        if locked.get("type") != "git":
            continue
        locked_url = locked.get("url")
        if not locked_url or not isinstance(locked_url, str):
            continue

        matching_entries = [
            e for e in entries
            if repository_urls_match(e.repository, locked_url, allow_file=True)
        ]
        if not matching_entries:
            continue

        if len(matching_entries) > 1:
            exact_key_matches = [e for e in matching_entries if e.key == node_name]
            if len(exact_key_matches) == 1:
                matching_entry = exact_key_matches[0]
            else:
                raise SourceError(f"ambiguous catalog entries for locked node {node_name}")
        else:
            matching_entry = matching_entries[0]

        locked_rev = locked.get("rev")
        if not isinstance(locked_rev, str) or HEX_REVISION.fullmatch(locked_rev) is None:
            raise SourceError(f"locked node {node_name} has invalid rev: {locked_rev!r}")

        if expected_revisions is not None:
            expected = expected_revisions.get(node_name) or expected_revisions.get(matching_entry.key)
            if expected is not None and locked_rev != expected:
                raise SourceError(
                    f"locked node {node_name} revision {locked_rev} does not match expected {expected}"
                )

        locked_ref = locked.get("ref")
        norm_entry_ref = _normalize_ref(matching_entry.release_ref)
        if locked_ref is None:
            if norm_entry_ref != "refs/heads/main":
                raise SourceError(f"locked node {node_name} missing ref")
            norm_locked_ref = "refs/heads/main"
        else:
            try:
                norm_locked_ref = _normalize_locked_ref(locked_ref)
            except SourceError as error:
                raise SourceError(
                    f"locked node {node_name} has invalid ref: {locked_ref!r}"
                ) from error
        if norm_locked_ref != norm_entry_ref:
            raise SourceError(
                f"locked node {node_name} ref {locked_ref} does not agree with catalog "
                f"selected ref authority {matching_entry.release_ref}"
            )

        # Materialize source into Nix store
        mat = materialize_source(
            workflow,
            matching_entry,
            locked_rev,
            expected_repository=locked_url,
            expected_ref=matching_entry.release_ref,
        )

        # Recorded narHash agreement requirement (no invented hashes)
        locked_nar = locked.get("narHash")
        if not locked_nar or not isinstance(locked_nar, str):
            raise SourceError(f"locked node {node_name} missing narHash")
        if locked_nar != mat.nar_hash:
            raise SourceError(
                f"locked node {node_name} recorded narHash {locked_nar} does not agree with "
                f"materialized source narHash {mat.nar_hash}"
            )

        realized[node_name] = mat

    return realized


def realize_historical_source(
    workflow: Any, catalog_entry: Any, locked_revision: str, *,
    expected_repository: str | None = None, expected_ref: str | None = None,
    expected_nar_hash: str | None = None,
) -> tuple[LocalSourceIdentity, dict[str, str]]:
    """Promotion-only historical realization; ordinary materialization stays strict."""
    from .local_source_history import realize_historical_source as realize

    return realize(workflow, catalog_entry, locked_revision,
                   expected_repository=expected_repository, expected_ref=expected_ref,
                   expected_nar_hash=expected_nar_hash)


# Keys produced exclusively by Nix evaluation / builtins.fetchTree on Git repositories
# that must NOT be serialized into Git locked nodes in flake.lock.
# These keys are specific to Git fetchTree evaluation results and must NEVER be stripped
# from serialized child flake.lock records of other schemes (e.g. type="path" requires 'path').
RESULT_ONLY_METADATA_KEYS: frozenset[str] = frozenset({
    "path",
    "outPath",
    "shortRev",
    "lastModifiedDate",
})

# Explicit alias documenting the Git fetchTree-only scope of this key set
GIT_FETCHTREE_RESULT_ONLY_KEYS: frozenset[str] = RESULT_ONLY_METADATA_KEYS

REVISION_DEPENDENT_LOCKED_KEYS: frozenset[str] = frozenset({
    "rev",
    "narHash",
    "lastModified",
    "revCount",
})

LOCK_SCHEMA_REVISION_KEYS: frozenset[str] = frozenset({
    "rev",
    "narHash",
    "lastModified",
    "revCount",
})


def project_git_locked_metadata(
    existing_locked: Mapping[str, Any],
    materialized_identity: Any,
    *,
    expected_revision: str | None = None,
) -> dict[str, Any]:
    """Project materialized Git source metadata into an existing lock node's locked record.

    Enforces an explicit boundary separating Nix fetchTree result metadata
    from the valid flake.lock Git locked schema:
    - Existing lock node is authority for fetcher/input-spec fields (type, url, ref,
      fetcher options like submodules, allRefs, shallow, dir).
    - Materialized identity is authority for immutable revision metadata:
      rev, narHash, lastModified, and revCount (only when validly returned).
    - Result-only fields (path, outPath, shortRev, lastModifiedDate) are never serialized.
    - FetchTree defaults (such as submodules=false) are never synthesized onto nodes
      that did not previously declare them.
    - When the revision changes, stale revision-dependent fields are purged before
      re-adding only the explicit lock-schema allowlist.
    - When the requested source revision already equals the existing lock's revision
      and narHash/lastModified agree, projection is stable and idempotent.
    """
    if not isinstance(existing_locked, Mapping):
        raise SourceError("existing locked node must be a mapping")

    node_type = existing_locked.get("type")
    if node_type != "git":
        raise SourceError(f"unsupported locked node type for Git projection: {node_type!r}")

    # Authority for immutable source revision
    mat_rev = getattr(materialized_identity, "revision", None)
    if mat_rev is None and isinstance(materialized_identity, Mapping):
        mat_rev = materialized_identity.get("rev")
    if not isinstance(mat_rev, str) or not mat_rev:
        raise SourceError("materialized identity missing valid revision")

    if expected_revision is not None and mat_rev != expected_revision:
        raise SourceError(
            f"materialized revision {mat_rev} does not match expected revision {expected_revision}"
        )

    # Authority for immutable source narHash
    mat_nar = getattr(materialized_identity, "nar_hash", None)
    if mat_nar is None and isinstance(materialized_identity, Mapping):
        mat_nar = materialized_identity.get("narHash")
    if not isinstance(mat_nar, str) or not mat_nar.startswith("sha256-"):
        raise SourceError(f"materialized identity missing valid narHash: {mat_nar!r}")

    # Extract fetchTree metadata dictionary from identity if present
    mat_locked_raw = getattr(materialized_identity, "locked", None)
    if callable(mat_locked_raw):
        mat_locked_raw = mat_locked_raw()
    elif mat_locked_raw is None:
        mat_locked_raw = getattr(materialized_identity, "locked_metadata", None)
    mat_locked = dict(mat_locked_raw) if isinstance(mat_locked_raw, Mapping) else {}

    # Fail closed if materialized locked metadata contradicts identity
    if "rev" in mat_locked and mat_locked["rev"] != mat_rev:
        raise SourceError(
            f"materialized locked metadata rev {mat_locked['rev']} does not agree with identity revision {mat_rev}"
        )
    if "narHash" in mat_locked and mat_locked["narHash"] != mat_nar:
        raise SourceError(
            f"materialized locked metadata narHash {mat_locked['narHash']} does not agree with identity narHash {mat_nar}"
        )

    # Validated optional revision metadata
    mat_last_modified = mat_locked.get("lastModified")
    has_valid_last_mod = (
        isinstance(mat_last_modified, int)
        and not isinstance(mat_last_modified, bool)
        and mat_last_modified >= 0
    )

    mat_rev_count = mat_locked.get("revCount")
    has_valid_rev_count = (
        isinstance(mat_rev_count, int)
        and not isinstance(mat_rev_count, bool)
        and mat_rev_count >= 0
    )

    result = dict(existing_locked)

    # Never allow Git fetchTree result-only metadata to leak into flake.lock
    for key in GIT_FETCHTREE_RESULT_ONLY_KEYS:
        result.pop(key, None)

    existing_rev = existing_locked.get("rev")
    revision_changed = (existing_rev != mat_rev)

    if revision_changed:
        # Purge stale revision-dependent fields from existing lock
        for key in REVISION_DEPENDENT_LOCKED_KEYS:
            result.pop(key, None)
    else:
        existing_nar = existing_locked.get("narHash")
        if existing_nar is not None and existing_nar != mat_nar:
            raise SourceError(
                f"existing locked node recorded narHash {existing_nar} does not agree with "
                f"materialized source narHash {mat_nar} for unchanged revision {mat_rev}"
            )

    # Apply only the explicit allowlist of lock-schema revision metadata
    candidate_metadata: dict[str, Any] = {
        "rev": mat_rev,
        "narHash": mat_nar,
    }
    if has_valid_last_mod:
        candidate_metadata["lastModified"] = mat_last_modified
    if has_valid_rev_count:
        candidate_metadata["revCount"] = mat_rev_count

    for key, val in candidate_metadata.items():
        if key in LOCK_SCHEMA_REVISION_KEYS:
            result[key] = val

    return result


def copy_serialized_locked_record(source_locked: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached deep copy of an owned child's serialized flake.lock record.

    Preserves scheme-specific lock attributes byte-for-byte without passing
    through the Git fetchTree result-only metadata sanitizer. Scheme-required
    attributes (such as 'path' for path-type inputs or scheme-specific attributes
    for github/gitlab/tarball) are retained exactly from the committed lock.
    """
    if not isinstance(source_locked, Mapping):
        raise SourceError(
            f"owned child locked record must be a mapping, got {type(source_locked).__name__}"
        )
    node_type = source_locked.get("type")
    if not isinstance(node_type, str) or not node_type.strip():
        raise SourceError("owned child locked record missing required 'type' attribute")
    if node_type == "path":
        node_path = source_locked.get("path")
        if not isinstance(node_path, str) or not node_path.strip():
            raise SourceError("owned child path locked record missing required 'path' attribute")
    return copy.deepcopy(dict(source_locked))


def _explicit_lock_targets(workflow, lock_doc, source_revisions):
    """Resolve all explicit authorities against one unchanged candidate graph."""
    nodes = lock_doc.get("nodes", {})
    catalog = coerce_source_catalog(getattr(workflow, "sources", ()))
    targets = {}
    claims = {}
    for key, rev in source_revisions.items():
        if key not in catalog:
            raise ConfigError(f"source revision key {key!r} not found in catalog")
        entry = catalog[key]
        matches = [
            name for name, node in nodes.items()
            if isinstance(node.get("locked"), Mapping)
            and node["locked"].get("type") == "git"
            and node["locked"].get("url")
            and repository_urls_match(entry.repository, node["locked"]["url"], allow_file=True)
        ]
        if not matches:
            raise ConfigError(f"candidate source {key!r} has no matching node in flake.lock")
        if len(matches) != 1:
            raise ConfigError(f"ambiguous nodes in flake.lock for source {key!r}: {sorted(matches)}")
        node = matches[0]
        if node in claims:
            other = claims[node]
            if (source_revisions[other] != rev or not repository_urls_match(
                    catalog[other].repository, entry.repository, allow_file=True)):
                raise ConfigError(f"contradictory explicit sources {other!r} and {key!r} for node {node!r}")
        claims[node] = key
        targets[key] = node
    return targets


def validate_explicit_lock_revisions(workflow, lock_doc, source_revisions, expected=None):
    """Fail closed on final or saved-lock revision, mapping, or metadata drift."""
    from .deployment_plan import canonical_bytes
    targets = _explicit_lock_targets(workflow, lock_doc, source_revisions)
    for key, node in targets.items():
        locked = lock_doc["nodes"][node]["locked"]
        if locked.get("rev") != source_revisions[key]:
            raise ConfigError(f"explicit source {key!r} node {node!r} does not contain requested revision")
        if expected is not None and (node != expected[key][0] or canonical_bytes(locked) != canonical_bytes(expected[key][1])):
            raise ConfigError(f"explicit source {key!r} final lock mapping or immutable metadata drifted")


def _owned_projection_records(nodes, target, committed, protected):
    """Pair dependencies by input edges, stopping at explicit authority boundaries.

    Projection preserves the candidate input graph (including its follows overrides).
    A changed string-edge topology requires a separately regenerated composition
    lock; guessing node names or silently retaining stale descendants is unsafe.
    """
    inner = committed.get("nodes", {})
    pending = [(target, committed.get("root", "root"))]
    visited = set()
    records = {}
    while pending:
        outer_name, inner_name = pending.pop()
        pair = (outer_name, inner_name)
        if pair in visited:
            continue
        visited.add(pair)
        if outer_name not in nodes or inner_name not in inner:
            raise ConfigError(f"candidate source {target!r} has unresolved owned dependency")
        outer_inputs = nodes[outer_name].get("inputs", {})
        inner_inputs = inner[inner_name].get("inputs", {})
        if set(outer_inputs) != set(inner_inputs):
            raise ConfigError(f"candidate source {target!r} owned input topology differs at {outer_name!r}")
        for key, child in outer_inputs.items():
            # Follows edges are deliberate composition overrides, not owned children.
            if isinstance(child, list) or child in protected:
                continue
            inner_child = inner_inputs[key]
            if not isinstance(inner_child, str) or inner_child not in inner or child not in nodes:
                raise ConfigError(f"candidate source {target!r} cannot map owned input {key!r}")
            record = copy_serialized_locked_record(inner[inner_child].get("locked"))
            _merge_projected_record(records, child, record)
            pending.append((child, inner_child))
    return records


def _merge_projected_record(records, node, record):
    from .deployment_plan import canonical_bytes
    if node in records and canonical_bytes(records[node]) != canonical_bytes(record):
        raise ConfigError(f"contradictory projected authority for lock node {node!r}")
    records[node] = record


def project_candidate_lock(
    workflow: Any,
    target_dir: Path,
    source_revisions: Mapping[str, str],
) -> None:
    """Validate exact explicit and inherited authority before writing the lock."""
    if not source_revisions:
        return
    lock_path = target_dir / "flake.lock"
    if not lock_path.is_file():
        raise ConfigError(f"cannot project candidate lock: missing {lock_path}")
    from .deployment_plan import read_lock_document, serialize_lock_document
    lock_doc = read_lock_document(lock_path)
    nodes = lock_doc.get("nodes", {})
    catalog = coerce_source_catalog(getattr(workflow, "sources", ()))
    targets = _explicit_lock_targets(workflow, lock_doc, source_revisions)
    protected = set(targets.values())
    explicit = {}
    inherited = {}
    expected = {}
    for key, rev in source_revisions.items():
        target = targets[key]
        mat = materialize_source(workflow, catalog[key], rev)
        record = project_git_locked_metadata(nodes[target]["locked"], mat, expected_revision=rev)
        _merge_projected_record(explicit, target, record)
        expected[key] = (target, record)
        committed_path = Path(mat.store_path) / "flake.lock" if mat.store_path else None
        if committed_path is not None and committed_path.is_file():
            committed = read_lock_document(committed_path)
            for child, child_record in _owned_projection_records(nodes, target, committed, protected).items():
                _merge_projected_record(inherited, child, child_record)
        elif nodes[target].get("inputs"):
            raise ConfigError(f"explicit source {key!r} has no committed lock to prove its dependencies")
    owners = protected | set(inherited)
    for owner, data in nodes.items():
        for child in data.get("inputs", {}).values():
            if isinstance(child, str) and child in inherited and owner not in owners:
                raise ConfigError(f"owned lock node {child!r} has an unprojected outside owner {owner!r}")
    for node, record in {**inherited, **explicit}.items():
        # Keep the expected records independent of candidate mutations.
        nodes[node]["locked"] = copy_serialized_locked_record(record)
    validate_explicit_lock_revisions(workflow, lock_doc, source_revisions, expected)
    lock_path.write_bytes(serialize_lock_document(lock_doc))


def match_selected_source(
    sources: Sequence[Any] | Any,
    selected: Path,
    repo_root: Path | None = None,
) -> Any:
    """Identify whether the selected flake path corresponds to one configured source checkout."""
    from .source_catalog import SourceError, coerce_source_catalog
    try:
        catalog = coerce_source_catalog(sources)
    except (SourceError, ConfigError, AttributeError):
        return None
    if not catalog:
        return None

    sel_resolved = selected.resolve(strict=False)
    repo_resolved = repo_root.resolve(strict=False) if repo_root is not None else None

    matches: list[Any] = []
    for entry in catalog.entries:
        entry_resolved = entry.checkout.resolve(strict=False)
        matched = False
        if entry_resolved == sel_resolved:
            matched = True
        elif entry.checkout.exists() and selected.exists() and entry.checkout.samefile(selected):
            matched = True
        elif repo_resolved is not None and entry_resolved == repo_resolved:
            matched = True
        elif repo_root is not None and entry.checkout.exists() and repo_root.exists() and entry.checkout.samefile(repo_root):
            matched = True

        if matched:
            matches.append(entry)

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        keys = [m.key for m in matches]
        raise ConfigError(f"selected flake {selected} matches multiple configured source checkouts: {keys}")
    return None
