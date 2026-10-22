"""Explicit provider-neutral publication of locally authoritative Git sources.

Local source selection and publication are deliberately separate operations.
This module only observes the selected checkouts, reads committed lock files,
and, when explicitly requested, fast-forwards configured Git remotes.  It does
not evaluate Nix, deploy a host, or modify a local checkout.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .deployment_plan import canonical_bytes, redact_text, sha256_bytes
from .deployment_paths import (
    atomic_private_write,
    create_private_directory,
    ensure_private_root,
    exclusive_private_write,
    fsync_directory,
    require_private_directory,
    require_private_file,
)
from .deployment_workflow import _private_state_root
from .local_source import SourceError, observe_source
from .paths import ConfigError
from .promotion_contract import (
    HEX_REVISION,
    SAFE_IDENTITY,
    normalize_git_transport_url,
    repository_urls_match,
    strict_json_loads,
    validate_release_ref,
)
from .source_catalog import SourceCatalog


PUBLICATION_SCHEMA = "dinas-source-publication-v1"
PUBLICATION_VERSION = 1
_SHA256_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _entry_publication(entry: Any) -> tuple[str, str] | None:
    publication = getattr(entry, "publication", None)
    if publication is None:
        return None
    remote = getattr(publication, "remote", None)
    ref = getattr(publication, "ref", None)
    if not isinstance(remote, str) or SAFE_IDENTITY.fullmatch(remote) is None:
        raise ConfigError("source publication remote is invalid")
    if not isinstance(ref, str):
        raise ConfigError("source publication ref is invalid")
    try:
        ref = validate_release_ref(ref)
    except ConfigError as error:
        raise ConfigError("source publication ref is invalid") from error
    return remote, ref


def _entries(sources: Any, workflow: Any) -> tuple[Any, ...]:
    if sources is None:
        sources = getattr(workflow, "sources", ())
    if isinstance(sources, SourceCatalog):
        return tuple(sources.entries)
    if isinstance(sources, Sequence) and not isinstance(sources, (str, bytes)):
        return tuple(sources)
    raise ConfigError("source catalog must be a SourceCatalog or entry sequence")


def _entry_key(entry: Any) -> str:
    key = getattr(entry, "key", None)
    if not isinstance(key, str) or not key:
        raise ConfigError("source catalog entry key is invalid")
    return key


def _entry_field(entry: Any, name: str) -> Any:
    return getattr(entry, name, None)


def _catalog_by_key(sources: tuple[Any, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for entry in sources:
        key = _entry_key(entry)
        if key in result:
            raise ConfigError(f"duplicate source catalog key: {key}")
        result[key] = entry
    return result


def _revision(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX_REVISION.fullmatch(value) is None:
        raise ConfigError(f"{label} must be an exact lowercase 40-hex commit")
    return value


def _selections(
    selections: Sequence[tuple[str, str]], entries: dict[str, Any]
) -> tuple[tuple[str, str], ...]:
    if isinstance(selections, (str, bytes)) or not isinstance(selections, Sequence):
        raise ConfigError("publication selections must be an ordered sequence")
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in selections:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise ConfigError("publication selection must be a (source_key, revision) pair")
        key, revision = item
        if not isinstance(key, str) or not key:
            raise ConfigError("publication selection source key is invalid")
        if key in seen:
            raise ConfigError(f"duplicate publication selection: {key}")
        if key not in entries:
            raise ConfigError(f"publication selection has no catalog entry: {key}")
        seen.add(key)
        result.append((key, _revision(revision, f"publication selection {key} revision")))
    return tuple(result)


def _run(workflow: Any, label: str, argv: Sequence[str], cwd: Path) -> tuple[Any, str]:
    try:
        result = workflow.command(label, list(argv), cwd)
    except ConfigError:
        raise
    except Exception as error:
        raise ConfigError(f"{label} could not be executed") from error
    if not hasattr(result, "returncode"):
        raise ConfigError(f"{label} returned an invalid command result")
    if callable(getattr(workflow, "complete_stdout", None)):
        stdout = workflow.complete_stdout(label)
    else:
        stdout = getattr(result, "stdout", "") or ""
    return result, str(stdout)


def _git_argv(*arguments: str) -> list[str]:
    """Run publication probes without replacement refs or incidental locks."""
    return ["git", "--no-replace-objects", "--no-optional-locks", *arguments]


def _run_private_git(
    workflow: Any, label: str, argv: Sequence[str], cwd: Path
) -> tuple[Any, str]:
    """Capture local Git config output without retaining raw URL evidence.

    A configured remote URL can contain credential material.  ``Workflow``
    intentionally retains command stdout for ordinary evidence, so remote URL
    probes use its already-authorized runner directly and keep the bounded
    value in memory only until validation completes.
    """
    runner = getattr(workflow, "runner", None)
    if not callable(runner):
        raise ConfigError(f"{label} requires a workflow runner")
    try:
        result = runner(
            list(argv),
            cwd=cwd,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception as error:
        raise ConfigError(f"{label} could not be executed") from error
    if not hasattr(result, "returncode"):
        raise ConfigError(f"{label} returned an invalid command result")
    stdout = getattr(result, "stdout", "") or ""
    if not isinstance(stdout, str):
        raise ConfigError(f"{label} returned invalid text output")
    try:
        size = len(stdout.encode("utf-8"))
    except UnicodeError as error:
        raise ConfigError(f"{label} returned invalid text output") from error
    if size > 64 * 1024:
        raise ConfigError(f"{label} returned excessive URL output")
    return result, stdout


def _observe(workflow: Any, entry: Any, revision: str) -> Mapping[str, Any]:
    checkout = Path(_entry_field(entry, "checkout"))
    repository = _entry_field(entry, "repository")
    release_ref = _entry_field(entry, "release_ref")
    if not isinstance(repository, str) or not repository:
        raise ConfigError(f"source {_entry_key(entry)} repository is invalid")
    if not isinstance(release_ref, str):
        raise ConfigError(f"source {_entry_key(entry)} release ref is invalid")
    try:
        identity = observe_source(
            workflow,
            entry,
            revision,
            expected_repository=repository,
            expected_ref=release_ref,
        )
    except SourceError:
        raise
    except ConfigError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise ConfigError(f"local source observation failed for {_entry_key(entry)}") from error
    if not isinstance(identity, Mapping) or identity.get("revision") != revision:
        raise ConfigError(f"local source observation returned an unexpected revision for {_entry_key(entry)}")
    return identity


def _safe_lock_path(directory: Any) -> str:
    if directory is None:
        return "flake.lock"
    if not isinstance(directory, str) or not directory or directory.startswith("/"):
        raise ConfigError("locked source subflake directory is invalid")
    parts = directory.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ConfigError("locked source subflake directory is invalid")
    return "/".join((*parts, "flake.lock"))


def _normalise_lock_ref(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{label} has an invalid release ref")
    candidate = value if value.startswith("refs/") else f"refs/heads/{value}"
    try:
        return validate_release_ref(candidate)
    except ConfigError as error:
        raise ConfigError(f"{label} has an invalid release ref") from error


def _read_lock(workflow: Any, entry: Any, revision: str) -> Mapping[str, Any] | None:
    path = _safe_lock_path(None)
    result, raw = _run(
        workflow,
        f"read committed source lock ({_entry_key(entry)})",
        _git_argv("show", f"{revision}:{path}"),
        Path(_entry_field(entry, "checkout")),
    )
    if result.returncode:
        # A source checkout without a flake.lock is a valid catalog leaf.  A
        # present but malformed lock is rejected below rather than ignored.
        return None
    if not raw.strip():
        raise ConfigError(f"committed source lock for {_entry_key(entry)} is empty")
    try:
        document = strict_json_loads(raw, f"committed source lock for {_entry_key(entry)}")
    except ConfigError:
        raise
    if not isinstance(document, Mapping):
        raise ConfigError(f"committed source lock for {_entry_key(entry)} is not an object")
    nodes = document.get("nodes")
    if nodes is not None and not isinstance(nodes, Mapping):
        raise ConfigError(f"committed source lock for {_entry_key(entry)} has invalid nodes")
    return document


def _repository_match(entry: Any, url: Any, *, allow_local_fixture: bool) -> bool:
    if not isinstance(url, str):
        return False
    repository = _entry_field(entry, "repository")
    try:
        return repository_urls_match(repository, url, allow_file=allow_local_fixture)
    except ConfigError:
        return False


def _catalog_match(
    entries: tuple[Any, ...], url: Any, *, allow_local_fixture: bool
) -> Any | None:
    matches = [
        entry for entry in entries
        if _repository_match(entry, url, allow_local_fixture=allow_local_fixture)
    ]
    if len(matches) > 1:
        raise ConfigError("locked source repository matches multiple catalog entries")
    return matches[0] if matches else None


def _dependencies(
    workflow: Any,
    entry: Any,
    revision: str,
    entries: tuple[Any, ...],
    *,
    allow_local_fixture: bool,
) -> tuple[dict[str, Any], ...]:
    document = _read_lock(workflow, entry, revision)
    if document is None:
        return ()
    nodes = document.get("nodes", {})
    if not isinstance(nodes, Mapping):
        return ()
    result: list[dict[str, Any]] = []

    def reachable_nodes() -> list[tuple[str, Any]]:
        root_name = document.get("root")
        if not isinstance(root_name, str) or root_name not in nodes:
            return list(nodes.items())
        pending = [root_name]
        seen: set[str] = set()
        reachable: list[tuple[str, Any]] = []
        while pending:
            node_name = pending.pop()
            if node_name in seen:
                continue
            seen.add(node_name)
            node_data = nodes.get(node_name)
            if not isinstance(node_data, Mapping):
                continue
            reachable.append((node_name, node_data))
            inputs = node_data.get("inputs")
            if isinstance(inputs, Mapping):
                for child in inputs.values():
                    if isinstance(child, str):
                        pending.append(child)
                    elif isinstance(child, Sequence) and not isinstance(child, (str, bytes)):
                        pending.extend(item for item in child if isinstance(item, str))
        return reachable

    for node_name, node_data in reachable_nodes():
        if not isinstance(node_name, str) or not isinstance(node_data, Mapping):
            continue
        locked = node_data.get("locked")
        original = node_data.get("original")
        if not isinstance(locked, Mapping) or locked.get("type") != "git":
            continue
        url = locked.get("url")
        if not isinstance(url, str) and isinstance(original, Mapping):
            url = original.get("url")
        matched = _catalog_match(entries, url, allow_local_fixture=allow_local_fixture)
        if matched is None:
            if not isinstance(url, str):
                continue
            # A Git input outside the local catalog may still be used as a
            # dependency, but it needs exact remote proof before its consumer
            # can be published.  The lock's portable URL/ref are the only
            # authority available for that proof.
            try:
                external_url = normalize_git_transport_url(
                    url, allow_file=allow_local_fixture
                )
                external_ref = _normalise_lock_ref(
                    locked.get("ref"), f"locked source {node_name}"
                )
            except ConfigError as error:
                raise ConfigError(
                    f"locked external source {node_name} has incomplete publication authority"
                ) from error
            result.append(
                {
                    "node": node_name,
                    "key": None,
                    "revision": _revision(
                        locked.get("rev"), f"locked source {node_name} revision"
                    ),
                    "ref": external_ref,
                    "url": external_url,
                    "entry": None,
                    "external": True,
                }
            )
            continue
        dep_key = _entry_key(matched)
        dep_revision = _revision(locked.get("rev"), f"locked source {node_name} revision")
        dep_ref = locked.get("ref")
        expected_ref = _entry_field(matched, "release_ref")
        if dep_ref is not None:
            if not isinstance(dep_ref, str):
                raise ConfigError(f"locked source {node_name} has an invalid release ref")
            try:
                if _normalise_lock_ref(dep_ref, f"locked source {node_name}") != _normalise_lock_ref(expected_ref, "catalog release ref"):
                    raise ConfigError(
                        f"locked source {node_name} ref does not agree with catalog authority"
                    )
            except ConfigError as error:
                if "does not agree" in str(error):
                    raise
                raise ConfigError(f"locked source {node_name} has an invalid release ref") from error
        if dep_key == _entry_key(entry) and dep_revision == revision:
            continue
        result.append(
            {
                "node": node_name,
                "key": dep_key,
                "revision": dep_revision,
                "ref": dep_ref,
                "url": url,
                "entry": matched,
                "external": False,
            }
        )
    return tuple(result)


def _discover_order(
    workflow: Any,
    requested: tuple[tuple[str, str], ...],
    by_key: Mapping[str, Any],
    catalog_entries: tuple[Any, ...],
    *,
    allow_local_fixture: bool,
) -> tuple[tuple[str, str], ...]:
    """Expand catalog-matched lock dependencies and return a topological order."""
    cache: dict[tuple[str, str], tuple[dict[str, Any], ...]] = {}
    visiting: set[str] = set()
    discovered: dict[str, str] = {}
    ordered: list[tuple[str, str]] = []

    def visit(key: str, revision: str) -> None:
        previous = discovered.get(key)
        if previous is not None:
            if previous != revision:
                raise ConfigError(
                    f"source {key} is selected at conflicting local revisions"
                )
            return
        if key in visiting:
            raise ConfigError(f"source publication dependency cycle includes {key}")
        visiting.add(key)
        cache_key = (key, revision)
        dependencies = cache.get(cache_key)
        if dependencies is None:
            dependencies = _dependencies(
                workflow,
                by_key[key],
                revision,
                catalog_entries,
                allow_local_fixture=allow_local_fixture,
            )
            cache[cache_key] = dependencies
        for dependency in dependencies:
            if dependency["entry"] is not None:
                visit(dependency["key"], dependency["revision"])
        visiting.remove(key)
        discovered[key] = revision
        ordered.append((key, revision))

    for key, revision in requested:
        visit(key, revision)
    return tuple(ordered)


def _remote_config(
    workflow: Any,
    entry: Any,
    *,
    allow_local_fixture: bool,
) -> dict[str, Any]:
    publication = _entry_publication(entry)
    if publication is None:
        raise ConfigError(f"source {_entry_key(entry)} has no publication authority")
    remote, ref = publication
    checkout = Path(_entry_field(entry, "checkout"))
    fetch_result, fetch_raw = _run_private_git(
        workflow,
        f"publication fetch URL ({_entry_key(entry)})",
        _git_argv("remote", "get-url", "--all", remote),
        checkout,
    )
    if fetch_result.returncode:
        raise ConfigError(f"publication remote {remote!r} has no configured fetch URL")
    push_result, push_raw = _run_private_git(
        workflow,
        f"publication push URL ({_entry_key(entry)})",
        _git_argv("remote", "get-url", "--all", "--push", remote),
        checkout,
    )
    if push_result.returncode:
        raise ConfigError(f"publication remote {remote!r} has no configured push URL")
    fetch_urls = tuple(line.strip() for line in fetch_raw.splitlines() if line.strip())
    push_urls = tuple(line.strip() for line in push_raw.splitlines() if line.strip())
    if not fetch_urls or len(push_urls) != 1:
        raise ConfigError(f"publication remote {remote!r} must have one push URL")
    try:
        if any(not repository_urls_match(_entry_field(entry, "repository"), url, allow_file=allow_local_fixture) for url in fetch_urls):
            raise ConfigError("configured publication fetch URL disagrees with source repository")
        if not repository_urls_match(_entry_field(entry, "repository"), push_urls[0], allow_file=allow_local_fixture):
            raise ConfigError("configured publication push URL disagrees with source repository")
        # Normalize each configured URL to reject unsupported or credentialed
        # forms even if a future identity helper becomes more permissive.
        for url in (*fetch_urls, *push_urls):
            normalize_git_transport_url(url, allow_file=allow_local_fixture)
    except ConfigError as error:
        if "credential" in str(error).lower() or "password" in str(error).lower():
            raise ConfigError("configured publication URL contains credential material") from error
        raise
    return {"remote": remote, "ref": ref, "fetch_urls": fetch_urls, "push_url": push_urls[0]}


def _authority_digest(
    entry: Any,
    remote_config: Mapping[str, Any],
    observation: Mapping[str, Any] | None = None,
) -> str:
    """Bind a publication step to the exact observed local authority.

    The digest is deliberately the only publication URL representation retained
    in a result or journal.  A later recovery can therefore prove that the
    checkout, catalog identity, and configured destination are unchanged without
    retaining credential-bearing configuration in private state.
    """
    observed_checkout = observation.get("checkout") if observation is not None else None
    if not isinstance(observed_checkout, str) or not Path(observed_checkout).is_absolute():
        observed_checkout = str(Path(_entry_field(entry, "checkout")).resolve(strict=False))
    fetch_urls = remote_config.get("fetch_urls")
    push_url = remote_config.get("push_url")
    if (
        not isinstance(fetch_urls, Sequence)
        or isinstance(fetch_urls, (str, bytes))
        or not all(isinstance(url, str) for url in fetch_urls)
        or not isinstance(push_url, str)
    ):
        raise ConfigError("publication authority configuration is invalid")
    material = {
        "checkout": observed_checkout,
        "repository": _entry_field(entry, "repository"),
        "release_ref": _entry_field(entry, "release_ref"),
        "remote": remote_config.get("remote"),
        "ref": remote_config.get("ref"),
        "fetch_urls": list(fetch_urls),
        "push_url": push_url,
    }
    return sha256_bytes(canonical_bytes(material))


def _require_authority_digest(
    entry: Any,
    remote_config: Mapping[str, Any],
    expected: Any,
    observation: Mapping[str, Any] | None = None,
) -> None:
    if not isinstance(expected, str) or _SHA256_DIGEST.fullmatch(expected) is None:
        raise ConfigError(
            f"publication authority digest is missing or invalid for {_entry_key(entry)}"
        )
    actual = _authority_digest(entry, remote_config, observation)
    if actual != expected:
        raise ConfigError(
            f"publication authority digest changed before push for {_entry_key(entry)}"
        )


def _revalidate_publication_config(
    workflow: Any,
    entry: Any,
    expected: Mapping[str, Any],
    *,
    allow_local_fixture: bool,
) -> dict[str, Any]:
    current = _remote_config(
        workflow,
        entry,
        allow_local_fixture=allow_local_fixture,
    )
    if current["remote"] != expected["remote"] or current["ref"] != expected["ref"]:
        raise ConfigError(
            f"publication authority changed before push for {_entry_key(entry)}"
        )
    return current


def _build_plan(
    workflow: Any,
    sources: Any,
    selections: Sequence[tuple[str, str]],
    *,
    allow_local_fixture: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    catalog_entries = _entries(sources, workflow)
    by_key = _catalog_by_key(catalog_entries)
    requested = _selections(selections, by_key)
    ordered = _discover_order(
        workflow,
        requested,
        by_key,
        catalog_entries,
        allow_local_fixture=allow_local_fixture,
    )
    positions = {key: index for index, (key, _revision_value) in enumerate(ordered)}
    selected_revisions = dict(ordered)
    context_steps: list[dict[str, Any]] = []
    public_steps: list[dict[str, Any]] = []

    for key, revision in ordered:
        entry = by_key[key]
        observation = _observe(workflow, entry, revision)
        publication = _entry_publication(entry)
        remote_config = None if publication is None else _remote_config(
            workflow, entry, allow_local_fixture=allow_local_fixture
        )
        authority_digest = (
            None
            if remote_config is None
            else _authority_digest(entry, remote_config, observation)
        )
        dependencies = _dependencies(
            workflow,
            entry,
            revision,
            catalog_entries,
            allow_local_fixture=allow_local_fixture,
        )
        dependency_public: list[dict[str, Any]] = []
        for dependency in dependencies:
            dep_key = dependency["key"]
            if dependency["entry"] is None:
                dependency_public.append(
                    {
                        "node": dependency["node"],
                        "key": None,
                        "revision": dependency["revision"],
                        "ref": dependency["ref"],
                        "selected": False,
                        "external": True,
                    }
                )
                continue
            dep_selected = dep_key in selected_revisions
            if dep_selected:
                if selected_revisions[dep_key] != dependency["revision"]:
                    raise ConfigError(
                        f"source {key} lock selects {dep_key} at a different local revision"
                    )
                if positions[dep_key] >= positions[key]:
                    raise ConfigError(
                        f"publication selections are not dependency ordered: {key} requires {dep_key}"
                    )
            elif _entry_publication(dependency["entry"]) is None:
                raise ConfigError(
                    f"source {key} requires unselected {dep_key} without publication authority"
                )
            dependency_public.append(
                {
                    "node": dependency["node"],
                    "key": dep_key,
                    "revision": dependency["revision"],
                    "ref": dependency["ref"],
                    "selected": dep_selected,
                    "external": False,
                }
            )
        state = "pending" if remote_config is not None else "unavailable"
        public_step = {
            "key": key,
            "local_commit": revision,
            "revision": revision,
            "remote": None if remote_config is None else remote_config["remote"],
            "ref": None if remote_config is None else remote_config["ref"],
            "authority_digest": authority_digest,
            "intended": None
            if remote_config is None
            else {"remote": remote_config["remote"], "ref": remote_config["ref"]},
            "dependencies": dependency_public,
            "state": state,
            "status": state,
            "remote_before": None,
            "remote_after": None,
        }
        public_steps.append(public_step)
        context_steps.append(
            {
                "key": key,
                "revision": revision,
                "entry": entry,
                "publication": remote_config,
                "dependencies": dependencies,
                "public": public_step,
            }
        )

    plan = {
        "schema": PUBLICATION_SCHEMA,
        "version": PUBLICATION_VERSION,
        "operation": "source-publication",
        "dry_run": True,
        "state": _overall_state(public_steps, dry_run=True),
        "status": "DRY_RUN",
        "no_activation": True,
        "local_commits": [
            {"key": key, "revision": revision} for key, revision in ordered
        ],
        "steps": public_steps,
    }
    return plan, {"steps": context_steps, "by_key": by_key}


def plan_publication(
    workflow: Any,
    sources: Any,
    selections: Sequence[tuple[str, str]],
    *,
    allow_local_fixture: bool = False,
) -> dict[str, Any]:
    """Validate and render a provider-neutral publication plan without network I/O."""
    plan, _context = _build_plan(
        workflow,
        sources,
        selections,
        allow_local_fixture=allow_local_fixture,
    )
    return plan


_JOURNAL_SCHEMA = "dinas-source-publication-journal-v1"
_JOURNAL_VERSION = 1
_JOURNAL_MAX_BYTES = 1024 * 1024


def _journal_parent() -> Path:
    return ensure_private_root(
        _private_state_root() / "source-publications",
        "source publication journal root",
    )


def _journal_step(step: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "key": step["key"],
        "local_commit": step["local_commit"],
        "remote": step["remote"],
        "ref": step["ref"],
        "authority_digest": step["authority_digest"],
        "state": "PLANNED" if step["state"] == "pending" else "UNAVAILABLE",
        "remote_before": None,
        "remote_after": None,
    }


def _journal_bytes(document: Mapping[str, Any]) -> bytes:
    raw = canonical_bytes(document)
    if len(raw) > _JOURNAL_MAX_BYTES:
        raise ConfigError("source publication journal exceeds its size limit")
    return raw


def _new_journal(plan: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    parent = _journal_parent()
    attempt_id = f"attempt-{os.getpid()}-{os.urandom(8).hex()}"
    directory = parent / attempt_id
    create_private_directory(directory, "source publication attempt directory")
    document: dict[str, Any] = {
        "schema": _JOURNAL_SCHEMA,
        "version": _JOURNAL_VERSION,
        "attempt_id": attempt_id,
        "state": "PLANNED",
        "no_activation": True,
        "local_commits": list(plan["local_commits"]),
        "steps": [_journal_step(step) for step in plan["steps"]],
    }
    journal = directory / "journal.json"
    exclusive_private_write(journal, _journal_bytes(document))
    fsync_directory(directory)
    return journal, document


def _journal_set_step(
    path: Path,
    document: Mapping[str, Any],
    key: str,
    state: str,
    *,
    remote_before: str | None = None,
    remote_after: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    updated = dict(document)
    updated_steps = [dict(step) for step in document["steps"]]
    matches = [step for step in updated_steps if step.get("key") == key]
    if len(matches) != 1:
        raise ConfigError("source publication journal step identity is invalid")
    step = matches[0]
    step["state"] = state
    step["remote_before"] = remote_before
    step["remote_after"] = remote_after
    if error is None:
        step.pop("error", None)
    else:
        step["error"] = redact_text(error)[:400]
    updated["steps"] = updated_steps
    states = {str(item.get("state")) for item in updated_steps}
    if "STARTED" in states:
        updated["state"] = "STARTED"
    elif "INDETERMINATE" in states:
        updated["state"] = "INDETERMINATE"
    elif "PENDING" in states or "PLANNED" in states:
        updated["state"] = "PENDING"
    elif "UNAVAILABLE" in states:
        updated["state"] = "UNAVAILABLE"
    else:
        updated["state"] = "PUBLISHED"
    atomic_private_write(path, _journal_bytes(updated))
    return updated


def _read_started_journal(key: str, revision: str) -> tuple[Path, dict[str, Any]] | None:
    parent = _journal_parent()
    matches: list[tuple[Path, dict[str, Any]]] = []
    for directory in sorted(parent.iterdir(), key=lambda candidate: candidate.name):
        if directory.is_symlink():
            raise ConfigError("source publication journal root contains a symlink")
        if not directory.is_dir():
            continue
        require_private_directory(directory, "source publication attempt directory")
        journal = directory / "journal.json"
        if not journal.exists():
            continue
        require_private_file(journal, "source publication journal")
        if journal.stat().st_size > _JOURNAL_MAX_BYTES:
            raise ConfigError("source publication journal exceeds its size limit")
        try:
            document = strict_json_loads(journal.read_text(encoding="utf-8"), "source publication journal")
        except (OSError, UnicodeError, ConfigError) as error:
            raise ConfigError("source publication journal is invalid") from error
        if (
            not isinstance(document, Mapping)
            or document.get("schema") != _JOURNAL_SCHEMA
            or document.get("version") != _JOURNAL_VERSION
            or not isinstance(document.get("steps"), list)
        ):
            raise ConfigError("source publication journal schema is invalid")
        if document.get("state") != "STARTED":
            continue
        if any(
            isinstance(step, Mapping)
            and step.get("key") == key
            and step.get("local_commit") == revision
            and step.get("state") == "STARTED"
            for step in document["steps"]
        ):
            matches.append((journal, dict(document)))
    if len(matches) > 1:
        raise ConfigError("multiple unresolved source publication attempts require reconciliation")
    return matches[0] if matches else None


def _remote_truth(
    workflow: Any,
    step: Mapping[str, Any],
    checkout: Path,
) -> tuple[str, str | None, str | None]:
    remote_config = step["publication"]
    remote = remote_config["remote"]
    # Use the exact validated push destination when the catalog supplied one.
    # Falling back to the remote name preserves the portable external-lock
    # proof shape, which has no configured catalog destination.
    remote_target = remote_config.get("push_url", remote)
    ref = remote_config["ref"]
    result, raw = _run(
        workflow,
        f"publication remote truth ({step['key']})",
        _git_argv("ls-remote", "--exit-code", remote_target, ref),
        checkout,
    )
    lines = [line.split() for line in raw.splitlines() if line.strip()]
    if result.returncode == 2 and not lines:
        return "missing", None, None
    if result.returncode:
        return "error", None, "remote truth query failed"
    matches = [fields for fields in lines if len(fields) == 2 and fields[1] == ref]
    if len(matches) != 1 or HEX_REVISION.fullmatch(matches[0][0]) is None:
        return "error", None, "remote truth did not contain exactly one valid requested ref"
    return "exact", matches[0][0], None


def _ensure_fast_forward(
    workflow: Any,
    entry: Any,
    remote_revision: str,
    revision: str,
) -> str | None:
    result, _raw = _run(
        workflow,
        f"publication fast-forward check ({_entry_key(entry)})",
        _git_argv("merge-base", "--is-ancestor", remote_revision, revision),
        Path(_entry_field(entry, "checkout")),
    )
    if result.returncode:
        return "remote publication would not be a fast-forward"
    return None


def _overall_state(steps: Sequence[Mapping[str, Any]], *, dry_run: bool) -> str:
    states = [str(step["state"]) for step in steps]
    if not states:
        return "pending" if dry_run else "published"
    if any(state == "indeterminate" for state in states):
        return "indeterminate"
    if all(state == "published" for state in states):
        return "pending" if dry_run else "published"
    if all(state == "unavailable" for state in states):
        return "unavailable"
    return "pending"


def _operation_status(state: str, *, dry_run: bool) -> str:
    if dry_run:
        return "DRY_RUN"
    return state.upper()


def publish_sources(
    workflow: Any,
    sources: Any,
    selections: Sequence[tuple[str, str]],
    dry_run: bool = True,
    *,
    allow_local_fixture: bool = False,
) -> dict[str, Any]:
    """Publish selected local commits in dependency order.

    A dry run performs local source, lock, and remote-configuration checks but
    never invokes ``git ls-remote`` or ``git push``.  A live run observes exact
    remote truth before and after each push and never retries a failed push.
    """
    plan, context = _build_plan(
        workflow,
        sources,
        selections,
        allow_local_fixture=allow_local_fixture,
    )
    result_steps = plan["steps"]
    result: dict[str, Any] = {
        **plan,
        "dry_run": bool(dry_run),
        "state": _overall_state(result_steps, dry_run=dry_run),
        "status": "DRY_RUN",
        "journal_path": None,
        "attempt_id": None,
        "errors": [],
    }
    if dry_run:
        return result

    journal_path: Path | None = None
    journal_document: dict[str, Any] | None = None

    def ensure_journal() -> None:
        nonlocal journal_path, journal_document
        if journal_path is not None or journal_document is not None:
            return
        journal_path, journal_document = _new_journal(plan)
        result["journal_path"] = str(journal_path)
        result["attempt_id"] = journal_document["attempt_id"]

    def journal_set(
        key: str,
        state: str,
        *,
        remote_before: str | None = None,
        remote_after: str | None = None,
        error: str | None = None,
    ) -> None:
        nonlocal journal_document
        ensure_journal()
        if journal_path is None or journal_document is None:
            return
        journal_document = _journal_set_step(
            journal_path,
            journal_document,
            key,
            state,
            remote_before=remote_before,
            remote_after=remote_after,
            error=error,
        )

    by_key = {step["key"]: step for step in context["steps"]}
    for context_step in context["steps"]:
        public = context_step["public"]
        entry = context_step["entry"]
        if context_step["publication"] is None:
            continue

        blocked: str | None = None
        for dependency in context_step["dependencies"]:
            dep_key = dependency["key"]
            if dependency["entry"] is None:
                external_truth, external_revision, external_error = _remote_truth(
                    workflow,
                    {
                        "key": f"external-{dependency['node']}",
                        "publication": {
                            "remote": dependency["url"],
                            "ref": dependency["ref"],
                        },
                    },
                    Path(_entry_field(entry, "checkout")),
                )
                if external_truth != "exact" or external_revision != dependency["revision"]:
                    blocked = external_error or (
                        f"external dependency {dependency['node']} is not published at its exact commit"
                    )
                    break
                continue
            # Discovery includes every catalog dependency before its consumer.
            # Published means its exact destination commit was observed this attempt.
            dep_state = by_key[dep_key]["public"]["state"]
            if dep_state != "published":
                blocked = f"dependency {dep_key} is {dep_state}"
                break

        if blocked is not None:
            public["state"] = "pending"
            public["status"] = "pending"
            public["error"] = blocked
            result["errors"].append(f"{public['key']}: {blocked}")
            journal_set(public["key"], "PENDING", error=blocked)
            continue

        # Revalidate the local release ref and the complete destination
        # authority immediately before consulting any recovery journal.  A
        # STARTED record may only be reconciled against the same authority
        # that was durably recorded before its push.
        observation = _observe(workflow, entry, context_step["revision"])
        current_publication = _revalidate_publication_config(
            workflow,
            entry,
            context_step["publication"],
            allow_local_fixture=allow_local_fixture,
        )
        _require_authority_digest(
            entry,
            current_publication,
            public.get("authority_digest"),
            observation,
        )
        prior = _read_started_journal(public["key"], context_step["revision"])
        if prior is not None:
            prior_path, prior_document = prior
            prior_steps = [
                step
                for step in prior_document["steps"]
                if isinstance(step, Mapping)
                and step.get("key") == public["key"]
                and step.get("local_commit") == context_step["revision"]
            ]
            if len(prior_steps) != 1 or (
                prior_steps[0].get("remote") != current_publication["remote"]
                or prior_steps[0].get("ref") != current_publication["ref"]
            ):
                raise ConfigError(
                    f"prior publication attempt targets a different remote for {public['key']}"
                )
            if prior_steps[0].get("authority_digest") != public.get("authority_digest"):
                raise ConfigError(
                    f"prior publication attempt authority differs for {public['key']}"
                )
        truth, remote_before, error = _remote_truth(
            workflow,
            {"key": public["key"], "publication": current_publication},
            Path(_entry_field(entry, "checkout")),
        )
        public["remote_before"] = remote_before
        if prior is not None:
            if truth == "exact" and remote_before == context_step["revision"]:
                _journal_set_step(
                    prior_path,
                    prior_document,
                    public["key"],
                    "PUBLISHED",
                    remote_before=remote_before,
                    remote_after=remote_before,
                )
                public["state"] = "published"
                public["status"] = "published"
                public["remote_after"] = remote_before
                journal_set(public["key"], "PUBLISHED", remote_before=remote_before, remote_after=remote_before)
            else:
                prior_state = "INDETERMINATE" if truth == "error" else "PENDING"
                prior_error = error or "prior publication attempt remains unresolved"
                _journal_set_step(
                    prior_path,
                    prior_document,
                    public["key"],
                    prior_state,
                    remote_before=remote_before,
                    error=prior_error,
                )
                public["state"] = prior_state.lower()
                public["status"] = public["state"]
                public["error"] = prior_error
                result["errors"].append(f"{public['key']}: {prior_error}")
                journal_set(public["key"], prior_state, remote_before=remote_before, error=prior_error)
            continue
        if truth == "error":
            public["state"] = "unavailable"
            public["status"] = "unavailable"
            public["error"] = error or "remote truth unavailable"
            result["errors"].append(f"{public['key']}: {public['error']}")
            journal_set(public["key"], "UNAVAILABLE", error=public["error"])
            continue
        if truth == "exact" and remote_before == context_step["revision"]:
            public["state"] = "published"
            public["status"] = "published"
            public["remote_after"] = remote_before
            journal_set(public["key"], "PUBLISHED", remote_before=remote_before, remote_after=remote_before)
            continue
        if truth == "exact" and remote_before is not None:
            fast_forward_error = _ensure_fast_forward(
                workflow, entry, remote_before, context_step["revision"]
            )
            if fast_forward_error is not None:
                public["state"] = "pending"
                public["status"] = "pending"
                public["error"] = fast_forward_error
                result["errors"].append(f"{public['key']}: {fast_forward_error}")
                journal_set(public["key"], "PENDING", remote_before=remote_before, error=fast_forward_error)
                continue

        # Close the observation-to-push race: the exact local release ref and
        # tracked worktree must still describe the reviewed commit.
        observation = _observe(workflow, entry, context_step["revision"])
        pre_push_publication = _revalidate_publication_config(
            workflow,
            entry,
            context_step["publication"],
            allow_local_fixture=allow_local_fixture,
        )
        _require_authority_digest(
            entry,
            pre_push_publication,
            public.get("authority_digest"),
            observation,
        )
        journal_set(public["key"], "STARTED", remote_before=remote_before)
        push_result, _push_raw = _run(
            workflow,
            f"publication push ({public['key']})",
            _git_argv(
                "push",
                pre_push_publication["push_url"],
                f"{context_step['revision']}:{pre_push_publication['ref']}",
            ),
            Path(_entry_field(entry, "checkout")),
        )
        after_truth, remote_after, after_error = _remote_truth(
            workflow,
            {"key": public["key"], "publication": pre_push_publication},
            Path(_entry_field(entry, "checkout")),
        )
        public["remote_after"] = remote_after
        if after_truth == "exact" and remote_after == context_step["revision"]:
            public["state"] = "published"
            public["status"] = "published"
            journal_set(public["key"], "PUBLISHED", remote_before=remote_before, remote_after=remote_after)
        elif after_truth == "error":
            public["state"] = "indeterminate"
            public["status"] = "indeterminate"
            public["error"] = after_error or "post-push remote truth unavailable"
            result["errors"].append(f"{public['key']}: {public['error']}")
            journal_set(public["key"], "INDETERMINATE", remote_before=remote_before, remote_after=remote_after, error=public["error"])
        elif push_result.returncode == 0:
            public["state"] = "indeterminate"
            public["status"] = "indeterminate"
            public["error"] = "post-push remote ref did not resolve to the selected commit"
            result["errors"].append(f"{public['key']}: {public['error']}")
            journal_set(public["key"], "INDETERMINATE", remote_before=remote_before, remote_after=remote_after, error=public["error"])
        else:
            public["state"] = "pending"
            public["status"] = "pending"
            public["error"] = "publication push was rejected"
            result["errors"].append(f"{public['key']}: {public['error']}")
            journal_set(public["key"], "PENDING", remote_before=remote_before, remote_after=remote_after, error=public["error"])
    result["state"] = _overall_state(result_steps, dry_run=False)
    result["status"] = _operation_status(result["state"], dry_run=False)
    if journal_path is not None and journal_document is not None:
        final_document = dict(journal_document)
        final_document["state"] = result["status"]
        atomic_private_write(journal_path, canonical_bytes(final_document))
    return result


__all__ = ["PUBLICATION_SCHEMA", "PUBLICATION_VERSION", "plan_publication", "publish_sources"]
