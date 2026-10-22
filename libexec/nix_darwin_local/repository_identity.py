from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

from .deployment_plan import file_identity, redact_text, sha256_bytes
from .paths import ConfigError

if TYPE_CHECKING:
    from .deployment_workflow import Workflow


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
)


def git_status_partition(status: str) -> tuple[str, str, int]:
    lines = status.splitlines()
    ignored = [line for line in lines if line.startswith("!! ")]
    relevant = [line for line in lines if not line.startswith("!! ")]
    return "\n".join(relevant), sha256_bytes("\n".join(ignored).encode()), len(ignored)


def local_git_identity(
    workflow: Any,
    selected: Path,
    require_clean: bool = True,
    require_attached_branch: bool | None = None,
) -> dict[str, Any]:
    fields: dict[str, str] = {}
    commands = {
        "commit": ["git", "rev-parse", "HEAD"],
        "tree": ["git", "rev-parse", "HEAD^{tree}"],
        "branch": ["git", "branch", "--show-current"],
        "status": ["git", "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching"],
        "repository_root": ["git", "rev-parse", "--show-toplevel"],
        "index": ["git", "ls-files", "--stage", "-z"],
    }
    for name, command in commands.items():
        result = workflow.command(f"Git {name}", command, selected)
        if result.returncode:
            raise ConfigError(f"unable to obtain Git {name}")
        fields[name] = workflow.complete_stdout(f"Git {name}").strip()

    repo_root = Path(fields["repository_root"]).resolve(strict=True)
    try:
        selected.resolve(strict=True).relative_to(repo_root)
    except (OSError, ValueError) as error:
        raise ConfigError("selected flake is outside repository root") from error

    if not fields["branch"]:
        fields["branch"] = "HEAD"

    fields["status"], ignored_digest, ignored_count = git_status_partition(fields["status"])
    fields["ignored_status_sha256"] = ignored_digest
    fields["ignored_status_count"] = str(ignored_count)

    if require_attached_branch is None:
        require_attached_branch = require_clean

    if require_attached_branch:
        if not fields["branch"] or fields["branch"] == "HEAD":
            raise ConfigError("selected deployment repository has a detached HEAD; local branch is required")
    if require_clean:
        if fields["status"]:
            raise ConfigError("selected deployment repository is not clean")

    active: list[str] = []
    for operation in GIT_OPERATION_PATHS:
        result = workflow.command(
            f"Git operation state ({operation})",
            ["git", "rev-parse", "--git-path", operation],
            selected,
        )
        if result.returncode:
            raise ConfigError("unable to obtain Git operation state")
        candidate = Path(workflow.complete_stdout(f"Git operation state ({operation})").strip())
        if not candidate.is_absolute():
            candidate = selected / candidate
        if candidate.exists() or candidate.is_symlink():
            active.append(operation)
    if active:
        raise ConfigError(f"selected deployment repository has active Git operation state: {active[0]}")

    for filename in ("flake.nix", "flake.lock"):
        target_path = selected / filename
        if not target_path.exists() or target_path.is_symlink() or not target_path.is_file():
            raise ConfigError(f"selected flake file must be a single-link regular file: {filename}")
        metadata = target_path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigError(f"selected flake file must be a single-link regular file: {filename}")
        if metadata.st_nlink != 1:
            raise ConfigError(f"selected flake file must be a single-link regular file: {filename}")
        if metadata.st_uid != os.getuid():
            raise ConfigError(f"selected flake file is not owned by the current user: {filename}")

    fields["operation_state"] = "clean"
    fields["index_sha256"] = sha256_bytes(fields.pop("index").encode())
    fields["flake_nix"] = json.dumps(file_identity(selected / "flake.nix"), sort_keys=True, separators=(",", ":"))
    fields["flake_lock"] = json.dumps(file_identity(selected / "flake.lock"), sort_keys=True, separators=(",", ":"))
    return fields


def configured_publication_identity(
    workflow: Any, selected: Path, branch: str
) -> dict[str, str]:
    if not branch or branch == "HEAD":
        raise ConfigError("publication requires a named local branch")

    fields: dict[str, str] = {}
    remote_name_result = workflow.command(
        "Git upstream remote name",
        ["git", "config", "--get", f"branch.{branch}.remote"],
        selected,
    )
    if remote_name_result.returncode:
        raise ConfigError("unable to obtain configured Git upstream authority")
    fields["remote_name"] = workflow.complete_stdout("Git upstream remote name").strip()

    remote_ref_result = workflow.command(
        "Git upstream remote ref",
        ["git", "config", "--get", f"branch.{branch}.merge"],
        selected,
    )
    if remote_ref_result.returncode:
        raise ConfigError("unable to obtain configured Git upstream authority")
    fields["remote_ref"] = workflow.complete_stdout("Git upstream remote ref").strip()

    if not fields["remote_name"] or not fields["remote_ref"].startswith("refs/heads/"):
        raise ConfigError("configured Git upstream authority is invalid")

    remote_url = workflow.command(
        "Git remote",
        ["git", "remote", "get-url", fields["remote_name"]],
        selected,
    )
    if remote_url.returncode:
        raise ConfigError("unable to obtain Git remote")
    fields["remote"] = workflow.complete_stdout("Git remote").strip()
    if redact_text(fields["remote"]) != fields["remote"]:
        raise ConfigError("canonical Git remote contains credential-bearing material")
    return fields


def publication_git_identity(
    workflow: Any, selected: Path, local_identity: Mapping[str, Any]
) -> dict[str, Any]:
    if local_identity.get("status"):
        raise ConfigError("selected deployment repository is not clean")
    if not local_identity.get("branch") or local_identity.get("branch") == "HEAD":
        raise ConfigError("selected deployment repository has a detached HEAD; local branch is required")

    fields: dict[str, Any] = configured_publication_identity(
        workflow, selected, str(local_identity.get("branch", ""))
    )

    remote = workflow.command(
        "Git canonical remote HEAD",
        ["git", "ls-remote", "--exit-code", fields["remote_name"], fields["remote_ref"]],
        selected,
    )
    if remote.returncode:
        raise ConfigError("unable to obtain canonical remote branch identity")
    remote_stdout = workflow.complete_stdout("Git canonical remote HEAD")
    if not remote_stdout.strip():
        raise ConfigError("unable to obtain canonical remote branch identity")
    remote_fields = remote_stdout.split()
    if len(remote_fields) != 2 or remote_fields[1] != fields["remote_ref"]:
        raise ConfigError("unable to obtain canonical remote branch identity")
    fields["remote_head"] = remote_fields[0]

    commit = str(local_identity.get("commit", ""))
    if fields["remote_head"] != commit:
        raise ConfigError("selected deployment repository differs from canonical remote branch")

    # The configured remote and its actual ref are publication authority.  A
    # remote-tracking ref is only a cached observation: it may be stale or
    # absent, so never let it block publication or manufacture a parity value.
    fields["upstream"] = None
    fields["parity"] = None
    upstream_result = workflow.command(
        "Git upstream",
        ["git", "rev-parse", "--abbrev-ref", "@{upstream}"],
        selected,
    )
    if not upstream_result.returncode:
        upstream = workflow.complete_stdout("Git upstream").strip()
        if upstream:
            fields["upstream"] = upstream
            parity_result = workflow.command(
                "Git parity",
                ["git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
                selected,
            )
            if not parity_result.returncode:
                parity = workflow.complete_stdout("Git parity").strip()
                if parity:
                    fields["parity"] = parity

    return fields
