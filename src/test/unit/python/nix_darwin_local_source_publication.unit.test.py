# nix-darwin-test-source-target: libexec/nix_darwin_local/source_publication.py
"""Unit coverage for the provider-neutral source publication state machine."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local import source_publication as publication
from nix_darwin_local.paths import ConfigError
from nix_darwin_local.source_catalog import Publication, SourceCatalog, SourceCatalogEntry
from nix_darwin_local.local_source import SourceError


_REAL_OBSERVE = publication._observe


REV_A = "a" * 40
REV_B = "b" * 40
REV_C = "c" * 40
REV_D = "d" * 40
AUTHORITY_A = "e" * 64


def _result(argv: list[str], code: int = 0, stdout: str = ""):
    return subprocess.CompletedProcess(argv, code, stdout, "")


class FakeWorkflow:
    """Bounded command/runner double with explicit Git observations."""

    def __init__(
        self,
        *,
        locks: dict[str, Any] | None = None,
        remote_urls: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] | None = None,
        truths: dict[tuple[str, str], tuple[str, str | None]] | None = None,
        fast_forwards: dict[tuple[str, str], bool] | None = None,
        push_code: int = 0,
        push_updates: bool = True,
        post_push_truth: tuple[str, str | None] | None = None,
    ):
        self.locks = locks or {}
        self.remote_urls = remote_urls or {}
        self.truths = truths or {}
        self.fast_forwards = fast_forwards or {}
        self.push_code = push_code
        self.push_updates = push_updates
        self.post_push_truth = post_push_truth
        self.records: list[SimpleNamespace] = []
        self.private_records: list[list[str]] = []
        self.push_calls: list[list[str]] = []
        self.runner = self._private_runner

    @staticmethod
    def _operation(argv: list[str]) -> str:
        for operation in ("show", "ls-remote", "merge-base", "push"):
            if operation in argv:
                return operation
        return ""

    def _truth_remote(self, remote: str, cwd: Path | None = None) -> str:
        for configured, (fetch_urls, push_urls) in self.remote_urls.items():
            if isinstance(configured, tuple):
                checkout, remote_name = configured
                if cwd is not None and Path(cwd) != Path(checkout):
                    continue
            else:
                remote_name = configured
            if remote == remote_name or remote in (*fetch_urls, *push_urls):
                return remote_name
        return remote

    def command(self, label: str, argv: list[str], cwd: Path, **_kwargs):
        argv = list(argv)
        operation = self._operation(argv)
        code = 0
        stdout = ""
        if operation == "show":
            requested = argv[-1].split(":", 1)[0]
            value = self.locks.get(requested)
            if value is None:
                code = 1
            elif isinstance(value, str):
                stdout = value
            else:
                stdout = json.dumps(value)
        elif operation == "ls-remote":
            position = argv.index("ls-remote")
            remote, ref = argv[position + 2], argv[position + 3]
            truth_remote = self._truth_remote(remote, cwd)
            state, revision = self.truths.get(
                (truth_remote, ref), self.truths.get((remote, ref), ("missing", None))
            )
            if state == "exact":
                stdout = f"{revision}\t{ref}\n"
            elif state == "missing":
                code = 2
            elif state == "error":
                code = 128
            else:
                stdout = str(revision or state)
        elif operation == "merge-base":
            position = argv.index("merge-base")
            remote_revision, selected_revision = argv[position + 2 : position + 4]
            if not self.fast_forwards.get((remote_revision, selected_revision), True):
                code = 1
        elif operation == "push":
            self.push_calls.append(argv)
            position = argv.index("push")
            remote = argv[position + 1]
            revision, ref = argv[position + 2].split(":", 1)
            truth_remote = self._truth_remote(remote, cwd)
            if self.push_code == 0 and self.push_updates:
                self.truths[(truth_remote, ref)] = ("exact", revision)
            if self.post_push_truth is not None:
                self.truths[(truth_remote, ref)] = self.post_push_truth
            code = self.push_code
        result = _result(argv, code, stdout)
        self.records.append(SimpleNamespace(label=label, argv=argv, cwd=cwd, result=result))
        return result

    def complete_stdout(self, label: str) -> str:
        for record in reversed(self.records):
            if record.label == label:
                return record.result.stdout
        raise ConfigError(f"missing fake command output for {label}")

    def _private_runner(self, argv: list[str], **_kwargs):
        argv = list(argv)
        self.private_records.append(argv)
        if "get-url" not in argv or "remote" not in argv:
            return _result(argv, 1)
        remote = argv[-1]
        cwd = _kwargs.get("cwd")
        fetch, push = self.remote_urls.get(
            (Path(cwd), remote), self.remote_urls.get(remote, ((), ()))
        )
        values = push if "--push" in argv else fetch
        if not values:
            return _result(argv, 1)
        return _result(argv, 0, "".join(f"{value}\n" for value in values))


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(publication, "_private_state_root", lambda: state_root)
    monkeypatch.setattr(
        publication,
        "_observe",
        lambda _workflow, entry, revision: {
            "key": entry.key,
            "revision": revision,
            "clean": True,
        },
    )
    return state_root


def make_entry(
    tmp_path: Path,
    key: str,
    *,
    revision_repo: str | None = None,
    published: bool = True,
    remote: str = "origin",
    publication_ref: str = "refs/heads/main",
    release_ref: str = "refs/heads/main",
) -> SourceCatalogEntry:
    return SourceCatalogEntry(
        key=key,
        checkout=tmp_path / key,
        repository=revision_repo or f"https://example.invalid/{key}.git",
        release_ref=release_ref,
        publication=Publication(remote, publication_ref) if published else None,
    )


def lock_document(
    dependencies: dict[str, tuple[str, str, str | None]],
) -> dict[str, Any]:
    nodes: dict[str, Any] = {"root": {"inputs": {}}}
    for node, (url, revision, ref) in dependencies.items():
        nodes["root"]["inputs"][node] = node
        locked = {"type": "git", "url": url, "rev": revision}
        original = {"type": "git", "url": url}
        if ref is not None:
            locked["ref"] = ref
            original["ref"] = ref
        nodes[node] = {"locked": locked, "original": original}
    return {"version": 7, "root": "root", "nodes": nodes}


def configure_workflow(
    entries: tuple[SourceCatalogEntry, ...],
    *,
    locks: dict[str, Any] | None = None,
    truths: dict[tuple[str, str], tuple[str, str | None]] | None = None,
    fast_forwards: dict[tuple[str, str], bool] | None = None,
    push_code: int = 0,
    push_updates: bool = True,
    post_push_truth: tuple[str, str | None] | None = None,
) -> FakeWorkflow:
    remote_urls = {
        (entry.checkout, entry.publication.remote): ((entry.repository,), (entry.repository,))
        for entry in entries
        if entry.publication is not None
    }
    return FakeWorkflow(
        locks=locks,
        remote_urls=remote_urls,
        truths=truths,
        fast_forwards=fast_forwards,
        push_code=push_code,
        push_updates=push_updates,
        post_push_truth=post_push_truth,
    )


def test_entry_and_selection_validation(tmp_path: Path):
    source = make_entry(tmp_path, "source")
    assert publication._entry_publication(source) == ("origin", "refs/heads/main")
    assert publication._entry_publication(make_entry(tmp_path, "local", published=False)) is None
    assert publication._entries(SourceCatalog((source,)), None) == (source,)
    assert publication._entries((source,), None) == (source,)
    assert publication._entry_key(source) == "source"
    assert publication._entry_field(source, "checkout") == source.checkout
    assert publication._revision(REV_A, "revision") == REV_A
    assert publication._selections([("source", REV_A)], {"source": source}) == (("source", REV_A),)
    assert publication._entries(None, SimpleNamespace(sources=(source,))) == (source,)

    with pytest.raises(ConfigError, match="remote is invalid"):
        publication._entry_publication(SimpleNamespace(publication=SimpleNamespace(remote="bad/name", ref="refs/heads/main")))
    with pytest.raises(ConfigError, match="ref is invalid"):
        publication._entry_publication(SimpleNamespace(publication=SimpleNamespace(remote="origin", ref="main")))
    with pytest.raises(ConfigError, match="ref is invalid"):
        publication._entry_publication(SimpleNamespace(publication=SimpleNamespace(remote="origin", ref=1)))
    with pytest.raises(ConfigError, match="source catalog"):
        publication._entries("source", None)
    with pytest.raises(ConfigError, match="duplicate source"):
        publication._catalog_by_key((source, source))
    with pytest.raises(ConfigError, match="entry key"):
        publication._catalog_by_key((SimpleNamespace(key=""),))
    with pytest.raises(ConfigError, match="revision"):
        publication._revision("A" * 40, "revision")
    with pytest.raises(ConfigError, match="ordered sequence"):
        publication._selections("bad", {"source": source})
    with pytest.raises(ConfigError, match="selection"):
        publication._selections([("source", REV_A, REV_B)], {"source": source})
    with pytest.raises(ConfigError, match="source key"):
        publication._selections([("", REV_A)], {"source": source})
    with pytest.raises(ConfigError, match="duplicate publication"):
        publication._selections([("source", REV_A), ("source", REV_A)], {"source": source})
    with pytest.raises(ConfigError, match="no catalog entry"):
        publication._selections([("missing", REV_A)], {"source": source})


def test_command_helpers_and_private_url_reader():
    workflow = FakeWorkflow()
    result = _result(["git"], 0, "stdout")
    workflow.command = lambda label, argv, cwd: result
    workflow.complete_stdout = lambda _label: "complete"
    assert publication._run(workflow, "command", ["git"], Path("/tmp"))[1] == "complete"
    assert publication._git_argv("show", "HEAD") == [
        "git", "--no-replace-objects", "--no-optional-locks", "show", "HEAD"
    ]

    broken = SimpleNamespace(command=lambda *_args: (_ for _ in ()).throw(RuntimeError("hidden")))
    with pytest.raises(ConfigError, match="could not be executed"):
        publication._run(broken, "command", ["git"], Path("/tmp"))
    invalid = SimpleNamespace(command=lambda *_args: SimpleNamespace())
    with pytest.raises(ConfigError, match="invalid command result"):
        publication._run(invalid, "command", ["git"], Path("/tmp"))
    config_failure = SimpleNamespace(
        command=lambda *_args: (_ for _ in ()).throw(ConfigError("bound failure"))
    )
    with pytest.raises(ConfigError, match="bound failure"):
        publication._run(config_failure, "command", ["git"], Path("/tmp"))
    without_complete = SimpleNamespace(
        command=lambda *_args: _result(["git"], 0, "fallback")
    )
    assert publication._run(without_complete, "command", ["git"], Path("/tmp"))[1] == "fallback"

    with pytest.raises(ConfigError, match="workflow runner"):
        publication._run_private_git(SimpleNamespace(), "URL", ["git"], Path("/tmp"))
    with pytest.raises(ConfigError, match="could not be executed"):
        publication._run_private_git(
            SimpleNamespace(runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError())),
            "URL",
            ["git"],
            Path("/tmp"),
        )
    with pytest.raises(ConfigError, match="invalid command result"):
        publication._run_private_git(
            SimpleNamespace(runner=lambda *_args, **_kwargs: SimpleNamespace()),
            "URL",
            ["git"],
            Path("/tmp"),
        )
    with pytest.raises(ConfigError, match="invalid text"):
        publication._run_private_git(
            SimpleNamespace(runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=b"bad")),
            "URL",
            ["git"],
            Path("/tmp"),
        )
    with pytest.raises(ConfigError, match="invalid text"):
        publication._run_private_git(
            SimpleNamespace(
                runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="\ud800")
            ),
            "URL",
            ["git"],
            Path("/tmp"),
        )


def test_private_url_reader_bounds_output():
    huge = "x" * (64 * 1024 + 1)
    workflow = SimpleNamespace(
        runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=huge)
    )
    with pytest.raises(ConfigError, match="excessive URL"):
        publication._run_private_git(workflow, "URL", ["git"], Path("/tmp"))


def test_observe_wrapper_binds_expected_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    source = make_entry(tmp_path, "source")
    monkeypatch.setattr(publication, "observe_source", lambda *args, **kwargs: {"revision": REV_A})
    assert _REAL_OBSERVE(FakeWorkflow(), source, REV_A)["revision"] == REV_A
    monkeypatch.setattr(publication, "observe_source", lambda *args, **kwargs: {"revision": REV_B})
    with pytest.raises(ConfigError, match="unexpected revision"):
        _REAL_OBSERVE(FakeWorkflow(), source, REV_A)
    monkeypatch.setattr(publication, "observe_source", lambda *args, **kwargs: (_ for _ in ()).throw(SourceError("source")))
    with pytest.raises(SourceError):
        _REAL_OBSERVE(FakeWorkflow(), source, REV_A)
    monkeypatch.setattr(publication, "observe_source", lambda *args, **kwargs: (_ for _ in ()).throw(ConfigError("bound")))
    with pytest.raises(ConfigError, match="bound"):
        _REAL_OBSERVE(FakeWorkflow(), source, REV_A)
    monkeypatch.setattr(publication, "observe_source", lambda *args, **kwargs: (_ for _ in ()).throw(TypeError("source")))
    with pytest.raises(ConfigError, match="observation failed"):
        _REAL_OBSERVE(FakeWorkflow(), source, REV_A)
    with pytest.raises(ConfigError, match="repository is invalid"):
        _REAL_OBSERVE(FakeWorkflow(), SimpleNamespace(key="source", checkout=tmp_path, repository="", release_ref="refs/heads/main"), REV_A)
    with pytest.raises(ConfigError, match="release ref is invalid"):
        _REAL_OBSERVE(FakeWorkflow(), SimpleNamespace(key="source", checkout=tmp_path, repository=source.repository, release_ref=1), REV_A)


@pytest.mark.parametrize(
    ("directory", "expected"),
    [(None, "flake.lock"), ("nested/sub", "nested/sub/flake.lock")],
)
def test_lock_path_and_ref_normalization(directory: str | None, expected: str):
    assert publication._safe_lock_path(directory) == expected
    assert publication._normalise_lock_ref("main", "lock") == "refs/heads/main"
    assert publication._normalise_lock_ref("refs/heads/main", "lock") == "refs/heads/main"
    with pytest.raises(ConfigError, match="invalid"):
        publication._safe_lock_path("../escape")
    with pytest.raises(ConfigError, match="invalid"):
        publication._safe_lock_path("/absolute")
    with pytest.raises(ConfigError, match="invalid"):
        publication._normalise_lock_ref("refs/tags/v1", "lock")


def test_read_lock_accepts_leaf_and_rejects_empty_or_malformed(tmp_path: Path):
    source = make_entry(tmp_path, "source")
    no_lock = FakeWorkflow()
    assert publication._read_lock(no_lock, source, REV_A) is None
    empty = FakeWorkflow(locks={REV_A: ""})
    with pytest.raises(ConfigError, match="empty"):
        publication._read_lock(empty, source, REV_A)
    malformed = FakeWorkflow(locks={REV_A: "not-json"})
    with pytest.raises(ConfigError, match="exactly one JSON"):
        publication._read_lock(malformed, source, REV_A)
    bad_nodes = FakeWorkflow(locks={REV_A: {"nodes": "bad"}})
    with pytest.raises(ConfigError, match="invalid nodes"):
        publication._read_lock(bad_nodes, source, REV_A)
    not_object = FakeWorkflow(locks={REV_A: []})
    with pytest.raises(ConfigError, match="not an object"):
        publication._read_lock(not_object, source, REV_A)


def test_repository_and_catalog_matching(tmp_path: Path):
    source = make_entry(tmp_path, "source", revision_repo="https://example.invalid/source.git")
    same = make_entry(tmp_path, "same", revision_repo="https://example.invalid/source.git")
    other = make_entry(tmp_path, "other")
    assert publication._repository_match(source, source.repository, allow_local_fixture=False)
    assert not publication._repository_match(source, other.repository, allow_local_fixture=False)
    assert publication._catalog_match((source,), source.repository, allow_local_fixture=False) is source
    assert publication._catalog_match((source,), other.repository, allow_local_fixture=False) is None
    with pytest.raises(ConfigError, match="multiple catalog"):
        publication._catalog_match((source, same), source.repository, allow_local_fixture=False)
    assert not publication._repository_match(source, "not-a-url", allow_local_fixture=False)
    assert not publication._repository_match(source, None, allow_local_fixture=False)


def test_dependencies_recurse_only_reachable_nodes_and_validate_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = make_entry(tmp_path, "source")
    lower = make_entry(tmp_path, "lower")
    hidden = make_entry(tmp_path, "hidden")
    lock = lock_document({"source": (source.repository, REV_A, "main")})
    lock["nodes"]["hidden"] = {
        "locked": {"type": "git", "url": hidden.repository, "rev": REV_D, "ref": "main"},
        "original": {"type": "git", "url": hidden.repository, "ref": "main"},
    }
    workflow = FakeWorkflow(locks={REV_B: lock})
    deps = publication._dependencies(workflow, lower, REV_B, (source, lower, hidden), allow_local_fixture=False)
    assert [(item["key"], item["revision"]) for item in deps] == [("source", REV_A)]
    self_lock = lock_document({"lower": (lower.repository, REV_B, "main")})
    self_deps = publication._dependencies(
        FakeWorkflow(locks={REV_B: self_lock}), lower, REV_B, (lower,), allow_local_fixture=False
    )
    assert self_deps == ()

    external = lock_document({"external": ("https://example.invalid/external.git", REV_C, "main")})
    external_deps = publication._dependencies(
        FakeWorkflow(locks={REV_B: external}), lower, REV_B, (lower,), allow_local_fixture=False
    )
    assert external_deps[0]["external"] is True
    missing_ref = lock_document({"external": ("https://example.invalid/external.git", REV_C, None)})
    with pytest.raises(ConfigError, match="incomplete publication"):
        publication._dependencies(
            FakeWorkflow(locks={REV_B: missing_ref}), lower, REV_B, (lower,), allow_local_fixture=False
        )

    bad_ref = lock_document({"source": (source.repository, REV_A, "refs/heads/other")})
    with pytest.raises(ConfigError, match="does not agree"):
        publication._dependencies(
            FakeWorkflow(locks={REV_B: bad_ref}), lower, REV_B, (source, lower), allow_local_fixture=False
        )
    with pytest.raises(ConfigError, match="invalid release ref"):
        publication._dependencies(
            FakeWorkflow(locks={REV_B: lock_document({"source": (source.repository, REV_A, "refs/tags/v1")})}),
            lower,
            REV_B,
            (source, lower),
            allow_local_fixture=False,
        )
    no_lock = FakeWorkflow()
    assert publication._dependencies(no_lock, lower, REV_B, (source, lower), allow_local_fixture=False) == ()

    no_root = {"nodes": {"source": {"locked": {"type": "git", "url": source.repository, "rev": REV_A, "ref": "main"}}}}
    no_root_deps = publication._dependencies(
        FakeWorkflow(locks={REV_B: no_root}), lower, REV_B, (source, lower), allow_local_fixture=False
    )
    assert no_root_deps[0]["key"] == "source"
    repeated = {
        "root": {"inputs": {"children": ["child", "child"]}},
        "child": {"locked": {"type": "git", "url": source.repository, "rev": REV_A, "ref": "main"}},
    }
    assert len(publication._dependencies(FakeWorkflow(locks={REV_B: {"root": "root", "nodes": repeated}}), lower, REV_B, (source, lower), allow_local_fixture=False)) == 1
    malformed_child = {"root": {"inputs": {"child": "child"}}, "child": "not-an-object"}
    assert publication._dependencies(FakeWorkflow(locks={REV_B: {"root": "root", "nodes": malformed_child}}), lower, REV_B, (source, lower), allow_local_fixture=False) == ()
    sequence_children = {
        "root": {"inputs": {"children": ["child", "other"]}},
        "child": {"locked": {"type": "path"}},
        "other": {"locked": {"type": "path"}},
    }
    assert publication._dependencies(FakeWorkflow(locks={REV_B: {"root": "root", "nodes": sequence_children}}), lower, REV_B, (source, lower), allow_local_fixture=False) == ()
    no_url = {"nodes": {"source": {"locked": {"type": "git", "rev": REV_A}}}}
    assert publication._dependencies(FakeWorkflow(locks={REV_B: no_url}), lower, REV_B, (source, lower), allow_local_fixture=False) == ()
    original_url = {
        "nodes": {
            "source": {
                "locked": {"type": "git", "rev": REV_A, "ref": "main"},
                "original": {"type": "git", "url": source.repository},
            }
        }
    }
    assert publication._dependencies(FakeWorkflow(locks={REV_B: original_url}), lower, REV_B, (source, lower), allow_local_fixture=False)[0]["key"] == "source"
    integer_node = {"nodes": {1: {"locked": {"type": "git", "url": source.repository, "rev": REV_A, "ref": "main"}}}}
    monkeypatch.setattr(publication, "_read_lock", lambda *args, **kwargs: integer_node)
    assert publication._dependencies(FakeWorkflow(), lower, REV_B, (source, lower), allow_local_fixture=False) == ()

    monkeypatch.setattr(publication, "_read_lock", lambda *args, **kwargs: {"nodes": "invalid"})
    assert publication._dependencies(FakeWorkflow(), lower, REV_B, (source, lower), allow_local_fixture=False) == ()

    monkeypatch.setattr(publication, "_read_lock", lambda *args, **kwargs: {"nodes": {
        "root": {"inputs": {"child": ["child", "child"]}},
        "child": {"locked": {"type": "path"}},
    }})
    assert publication._dependencies(FakeWorkflow(), lower, REV_B, (source, lower), allow_local_fixture=False) == ()
    missing_ref = {"nodes": {"source": {"locked": {"type": "git", "url": source.repository, "rev": REV_A}}}}
    monkeypatch.setattr(publication, "_read_lock", lambda *args, **kwargs: missing_ref)
    assert publication._dependencies(FakeWorkflow(), lower, REV_B, (source, lower), allow_local_fixture=False)[0]["key"] == "source"
    bad_ref_type = {"nodes": {"source": {"locked": {"type": "git", "url": source.repository, "rev": REV_A, "ref": 7}}}}
    monkeypatch.setattr(publication, "_read_lock", lambda *args, **kwargs: bad_ref_type)
    with pytest.raises(ConfigError, match="invalid release ref"):
        publication._dependencies(FakeWorkflow(), lower, REV_B, (source, lower), allow_local_fixture=False)


def test_discover_order_recurses_conflicts_and_cycles(tmp_path: Path):
    source = make_entry(tmp_path, "source", published=False)
    lower = make_entry(tmp_path, "lower", published=False)
    upper = make_entry(tmp_path, "upper", published=False)
    locks = {
        REV_A: lock_document({}),
        REV_B: lock_document({"source": (source.repository, REV_A, "main")}),
        REV_C: lock_document({"lower": (lower.repository, REV_B, "main")}),
    }
    workflow = FakeWorkflow(locks=locks)
    ordered = publication._discover_order(
        workflow,
        (("upper", REV_C),),
        {entry.key: entry for entry in (source, lower, upper)},
        (source, lower, upper),
        allow_local_fixture=False,
    )
    assert ordered == (("source", REV_A), ("lower", REV_B), ("upper", REV_C))

    with pytest.raises(ConfigError, match="conflicting"):
        publication._discover_order(
            workflow,
            (("source", REV_A), ("source", REV_B)),
            {entry.key: entry for entry in (source, lower, upper)},
            (source, lower, upper),
            allow_local_fixture=False,
        )
    cycle_lock_a = lock_document({"lower": (lower.repository, REV_B, "main")})
    cycle_lock_b = lock_document({"source": (source.repository, REV_A, "main")})
    with pytest.raises(ConfigError, match="cycle"):
        publication._discover_order(
            FakeWorkflow(locks={REV_A: cycle_lock_a, REV_B: cycle_lock_b}),
            (("source", REV_A),),
            {"source": source, "lower": lower},
            (source, lower),
            allow_local_fixture=False,
        )


def test_remote_configuration_validates_fetch_push_and_credentials(tmp_path: Path):
    source = make_entry(tmp_path, "source")
    valid = configure_workflow((source,))
    config = publication._remote_config(valid, source, allow_local_fixture=False)
    assert config["remote"] == "origin"
    assert config["push_url"] == source.repository

    no_publication = make_entry(tmp_path, "local", published=False)
    with pytest.raises(ConfigError, match="no publication"):
        publication._remote_config(valid, no_publication, allow_local_fixture=False)
    with pytest.raises(ConfigError, match="fetch URL"):
        publication._remote_config(
            FakeWorkflow(remote_urls={"origin": ((), (source.repository,))}), source, allow_local_fixture=False
        )
    with pytest.raises(ConfigError, match="one push URL"):
        publication._remote_config(
            FakeWorkflow(remote_urls={"origin": ((source.repository,), (source.repository, source.repository))}),
            source,
            allow_local_fixture=False,
        )
    with pytest.raises(ConfigError, match="configured push URL"):
        publication._remote_config(
            FakeWorkflow(remote_urls={"origin": ((source.repository,), ())}),
            source,
            allow_local_fixture=False,
        )
    with pytest.raises(ConfigError, match="credential material"):
        publication._remote_config(
            FakeWorkflow(remote_urls={"origin": (("https://user:password@example.invalid/source.git",), ("https://user:password@example.invalid/source.git",))}),
            source,
            allow_local_fixture=False,
        )
    with pytest.raises(ConfigError, match="disagrees"):
        publication._remote_config(
            FakeWorkflow(remote_urls={"origin": (("https://example.invalid/other.git",), ("https://example.invalid/other.git",))}),
            source,
            allow_local_fixture=False,
        )
    with pytest.raises(ConfigError, match="push URL disagrees"):
        publication._remote_config(
            FakeWorkflow(
                remote_urls={
                    "origin": (
                        (source.repository,),
                        ("https://example.invalid/other.git",),
                    )
                }
            ),
            source,
            allow_local_fixture=False,
        )


def test_build_plan_and_plan_publication_render_dependencies(tmp_path: Path):
    source = make_entry(tmp_path, "source")
    lower = make_entry(tmp_path, "lower")
    upper = make_entry(tmp_path, "upper")
    locks = {
        REV_A: lock_document({}),
        REV_B: lock_document({"source": (source.repository, REV_A, "main")}),
        REV_C: lock_document(
            {
                "source": (source.repository, REV_A, "main"),
                "lower": (lower.repository, REV_B, "main"),
            }
        ),
    }
    workflow = configure_workflow((source, lower, upper), locks=locks)
    plan = publication.plan_publication(
        workflow,
        SourceCatalog((source, lower, upper)),
        [("upper", REV_C)],
    )
    assert plan["schema"] == publication.PUBLICATION_SCHEMA
    assert plan["status"] == "DRY_RUN"
    assert plan["no_activation"] is True
    assert [step["key"] for step in plan["steps"]] == ["source", "lower", "upper"]
    assert all(step["state"] == "pending" for step in plan["steps"])
    assert not any("ls-remote" in record.argv for record in workflow.records)

    local_only = make_entry(tmp_path, "local-only", published=False)
    unavailable = publication.plan_publication(
        configure_workflow((local_only,), locks={REV_D: lock_document({})}),
        (local_only,),
        [("local-only", REV_D)],
    )
    assert unavailable["steps"][0]["state"] == "unavailable"


def test_build_plan_rejects_unselected_dependency_without_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = make_entry(tmp_path, "source", published=False)
    upper = make_entry(tmp_path, "upper")
    locks = {REV_B: lock_document({"source": (source.repository, REV_A, "main")})}
    monkeypatch.setattr(publication, "_discover_order", lambda *args, **kwargs: (("upper", REV_B),))
    with pytest.raises(ConfigError, match="without publication authority"):
        publication.plan_publication(
            configure_workflow((source, upper), locks=locks),
            (source, upper),
            [("upper", REV_B)],
        )


def test_build_plan_rejects_dependency_revision_and_order_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = make_entry(tmp_path, "source")
    upper = make_entry(tmp_path, "upper")
    locks = {REV_B: lock_document({"source": (source.repository, REV_C, "main")})}
    monkeypatch.setattr(
        publication,
        "_discover_order",
        lambda *args, **kwargs: (("source", REV_A), ("upper", REV_B)),
    )
    with pytest.raises(ConfigError, match="different local revision"):
        publication.plan_publication(
            configure_workflow((source, upper), locks=locks),
            (source, upper),
            [("upper", REV_B)],
        )

    locks = {REV_B: lock_document({"source": (source.repository, REV_A, "main")})}
    monkeypatch.setattr(
        publication,
        "_discover_order",
        lambda *args, **kwargs: (("upper", REV_B), ("source", REV_A)),
    )
    with pytest.raises(ConfigError, match="dependency ordered"):
        publication.plan_publication(
            configure_workflow((source, upper), locks=locks),
            (source, upper),
            [("upper", REV_B)],
        )


@pytest.mark.parametrize(
    ("steps", "dry_run", "expected"),
    [
        ([], True, "pending"),
        ([], False, "published"),
        ([{"state": "published"}], False, "published"),
        ([{"state": "published"}], True, "pending"),
        ([{"state": "unavailable"}], False, "unavailable"),
        ([{"state": "indeterminate"}, {"state": "published"}], False, "indeterminate"),
        ([{"state": "pending"}, {"state": "unavailable"}], False, "pending"),
    ],
)
def test_overall_state_and_operation_status(steps, dry_run, expected):
    assert publication._overall_state(steps, dry_run=dry_run) == expected
    assert publication._operation_status(expected, dry_run=True) == "DRY_RUN"
    assert publication._operation_status(expected, dry_run=False) == expected.upper()


def test_remote_truth_and_fast_forward_outcomes(tmp_path: Path):
    source = make_entry(tmp_path, "source")
    step = {"key": "source", "publication": {"remote": "origin", "ref": "refs/heads/main"}}
    exact = FakeWorkflow(truths={("origin", "refs/heads/main"): ("exact", REV_A)})
    assert publication._remote_truth(exact, step, source.checkout) == ("exact", REV_A, None)
    missing = FakeWorkflow()
    assert publication._remote_truth(missing, step, source.checkout) == ("missing", None, None)
    error = FakeWorkflow(truths={("origin", "refs/heads/main"): ("error", None)})
    assert publication._remote_truth(error, step, source.checkout)[0] == "error"
    malformed = FakeWorkflow()
    malformed.command = lambda label, argv, cwd: _result(argv, 0, "bad output\n")
    malformed.complete_stdout = lambda _label: "bad output\n"
    assert publication._remote_truth(malformed, step, source.checkout)[0] == "error"
    ff = configure_workflow((source,), fast_forwards={(REV_A, REV_B): True})
    assert publication._ensure_fast_forward(ff, source, REV_A, REV_B) is None
    no_ff = configure_workflow((source,), fast_forwards={(REV_A, REV_B): False})
    assert "fast-forward" in publication._ensure_fast_forward(no_ff, source, REV_A, REV_B)


def test_journal_lifecycle_and_started_recovery(isolated_state: Path, tmp_path: Path):
    source = make_entry(tmp_path, "source")
    plan = {
        "local_commits": [{"key": "source", "revision": REV_A}],
        "steps": [{
            "key": "source",
            "local_commit": REV_A,
            "remote": "origin",
            "ref": "refs/heads/main",
            "authority_digest": AUTHORITY_A,
            "state": "pending",
        }],
    }
    path, document = publication._new_journal(plan)
    assert path.exists()
    assert document["state"] == "PLANNED"
    document = publication._journal_set_step(path, document, "source", "STARTED", remote_before=None)
    assert json.loads(path.read_text())["state"] == "STARTED"
    assert publication._read_started_journal("source", REV_A) is not None
    document = publication._journal_set_step(path, document, "source", "INDETERMINATE", error="remote unavailable")
    assert document["state"] == "INDETERMINATE"
    assert publication._read_started_journal("source", REV_A) is None
    document = publication._journal_set_step(path, document, "source", "PUBLISHED", remote_after=REV_A)
    assert document["state"] == "PUBLISHED"
    with pytest.raises(ConfigError, match="step identity"):
        publication._journal_set_step(path, document, "missing", "PENDING")


def test_journal_set_step_tracks_pending_and_unavailable_precedence(
    isolated_state: Path, tmp_path: Path
):
    source = make_entry(tmp_path, "source")
    plan = {
        "local_commits": [
            {"key": "source", "revision": REV_A},
            {"key": "other", "revision": REV_B},
        ],
        "steps": [
            {"key": "source", "local_commit": REV_A, "remote": "origin", "ref": "refs/heads/main", "authority_digest": AUTHORITY_A, "state": "pending"},
            {"key": "other", "local_commit": REV_B, "remote": None, "ref": None, "authority_digest": None, "state": "unavailable"},
        ],
    }
    path, document = publication._new_journal(plan)
    document = publication._journal_set_step(path, document, "source", "PENDING")
    assert document["state"] == "PENDING"
    document = publication._journal_set_step(path, document, "other", "UNAVAILABLE")
    assert document["state"] == "PENDING"
    document = publication._journal_set_step(path, document, "source", "PUBLISHED")
    assert document["state"] == "UNAVAILABLE"
    document = publication._journal_set_step(path, document, "other", "PUBLISHED")
    assert document["state"] == "PUBLISHED"


def test_journal_reader_rejects_unsafe_or_unresolved_records(isolated_state: Path):
    parent = publication._journal_parent()

    os.symlink(parent / "missing", parent / "symlink")
    with pytest.raises(ConfigError, match="contains a symlink"):
        publication._read_started_journal("source", REV_A)
    (parent / "symlink").unlink()

    parent = publication._journal_parent()
    (parent / "not-a-directory").write_text("ignored")
    (parent / "not-a-directory").chmod(0o600)
    empty = parent / "empty-attempt"
    empty.mkdir(mode=0o700)
    assert publication._read_started_journal("source", REV_A) is None

    oversized = parent / "oversized"
    oversized.mkdir(mode=0o700)
    oversized_file = oversized / "journal.json"
    oversized_file.write_bytes(b"x" * (publication._JOURNAL_MAX_BYTES + 1))
    oversized_file.chmod(0o600)
    with pytest.raises(ConfigError, match="exceeds"):
        publication._read_started_journal("source", REV_A)
    oversized_file.unlink()
    oversized.rmdir()

    pending = parent / "pending-attempt"
    pending.mkdir(mode=0o700)
    pending_file = pending / "journal.json"
    pending_file.write_text(
        json.dumps({
            "schema": publication._JOURNAL_SCHEMA,
            "version": publication._JOURNAL_VERSION,
            "state": "PENDING",
            "steps": [],
        }),
        encoding="utf-8",
    )
    pending_file.chmod(0o600)
    assert publication._read_started_journal("source", REV_A) is None

    started_other = parent / "started-other"
    started_other.mkdir(mode=0o700)
    started_other_file = started_other / "journal.json"
    started_other_file.write_text(
        json.dumps({
            "schema": publication._JOURNAL_SCHEMA,
            "version": publication._JOURNAL_VERSION,
            "state": "STARTED",
            "steps": [{"key": "other", "local_commit": REV_A, "state": "STARTED"}],
        }),
        encoding="utf-8",
    )
    started_other_file.chmod(0o600)
    assert publication._read_started_journal("source", REV_A) is None


@pytest.mark.parametrize(
    "payload",
    [b"\xff", b"{}"],
    ids=["invalid-encoding", "invalid-schema"],
)
def test_journal_reader_rejects_invalid_payloads(isolated_state: Path, payload: bytes):
    parent = publication._journal_parent()
    attempt = parent / "invalid"
    attempt.mkdir(mode=0o700)
    journal = attempt / "journal.json"
    journal.write_bytes(payload)
    journal.chmod(0o600)
    with pytest.raises(ConfigError, match="invalid|schema"):
        publication._read_started_journal("source", REV_A)


def test_journal_reader_rejects_multiple_started_attempts(isolated_state: Path):
    parent = publication._journal_parent()
    for index in range(2):
        attempt = parent / f"attempt-{index}"
        attempt.mkdir(mode=0o700)
        document = {
            "schema": publication._JOURNAL_SCHEMA,
            "version": publication._JOURNAL_VERSION,
            "state": "STARTED",
            "steps": [{"key": "source", "local_commit": REV_A, "state": "STARTED"}],
        }
        journal = attempt / "journal.json"
        journal.write_text(json.dumps(document), encoding="utf-8")
        journal.chmod(0o600)
    with pytest.raises(ConfigError, match="multiple unresolved"):
        publication._read_started_journal("source", REV_A)


def _chain(tmp_path: Path, *, published: bool = True):
    source = make_entry(tmp_path, "source", published=published)
    lower = make_entry(tmp_path, "lower", published=published)
    upper = make_entry(tmp_path, "upper", published=published)
    locks = {
        REV_A: lock_document({}),
        REV_B: lock_document({"source": (source.repository, REV_A, "main")}),
        REV_C: lock_document({
            "source": (source.repository, REV_A, "main"),
            "lower": (lower.repository, REV_B, "main"),
        }),
    }
    return (source, lower, upper), locks


def test_publish_sources_dry_run_and_provider_free_success(
    tmp_path: Path, isolated_state: Path
):
    entries, locks = _chain(tmp_path)
    workflow = configure_workflow(entries, locks=locks)
    dry = publication.publish_sources(workflow, entries, [("upper", REV_C)], dry_run=True)
    assert dry["status"] == "DRY_RUN"
    assert dry["state"] == "pending"
    assert dry["journal_path"] is None
    assert all(
        isinstance(step["authority_digest"], str)
        and len(step["authority_digest"]) == 64
        for step in dry["steps"]
    )
    assert not any("ls-remote" in record.argv or "push" in record.argv for record in workflow.records)

    workflow = configure_workflow(entries, locks=locks)
    live = publication.publish_sources(workflow, entries, [("upper", REV_C)], dry_run=False)
    assert live["status"] == "PUBLISHED"
    assert live["state"] == "published"
    assert live["no_activation"] is True
    assert [step["key"] for step in live["steps"]] == ["source", "lower", "upper"]
    assert len(workflow.push_calls) == 3
    assert all("--force" not in call for call in workflow.push_calls)
    assert all("--no-replace-objects" in call for call in workflow.push_calls)
    journals = list((isolated_state / "source-publications").glob("*/journal.json"))
    assert len(journals) == 1
    journal = json.loads(journals[0].read_text(encoding="utf-8"))
    assert [step["authority_digest"] for step in journal["steps"]] == [
        step["authority_digest"] for step in live["steps"]
    ]
    journal_bytes = journals[0].read_text(encoding="utf-8")
    assert all(entry.repository not in journal_bytes for entry in entries)


def test_publish_sources_missing_metadata_and_remote_failure(tmp_path: Path):
    local = make_entry(tmp_path, "local", published=False)
    workflow = configure_workflow((local,), locks={REV_A: lock_document({})})
    result = publication.publish_sources(workflow, (local,), [("local", REV_A)], dry_run=False)
    assert result["status"] == "UNAVAILABLE"
    assert result["steps"][0]["state"] == "unavailable"
    assert workflow.private_records == []

    source = make_entry(tmp_path, "unavailable")
    workflow = configure_workflow(
        (source,), locks={REV_A: lock_document({})}, truths={("origin", "refs/heads/main"): ("error", None)}
    )
    result = publication.publish_sources(workflow, (source,), [("unavailable", REV_A)], dry_run=False)
    assert result["status"] == "UNAVAILABLE"
    assert result["steps"][0]["state"] == "unavailable"
    assert workflow.push_calls == []


def test_publish_sources_rejected_and_non_fast_forward_preserve_local_state(tmp_path: Path):
    source = make_entry(tmp_path, "rejected")
    workflow = configure_workflow(
        (source,), locks={REV_A: lock_document({})}, push_code=1, push_updates=False
    )
    result = publication.publish_sources(workflow, (source,), [("rejected", REV_A)], dry_run=False)
    assert result["status"] == "PENDING"
    assert result["steps"][0]["state"] == "pending"
    assert workflow.push_calls
    assert workflow.push_calls[0][-1] == f"{REV_A}:refs/heads/main"

    source = make_entry(tmp_path, "non-ff")
    workflow = configure_workflow(
        (source,),
        locks={REV_A: lock_document({})},
        truths={("origin", "refs/heads/main"): ("exact", REV_B)},
        fast_forwards={(REV_B, REV_A): False},
    )
    result = publication.publish_sources(workflow, (source,), [("non-ff", REV_A)], dry_run=False)
    assert result["status"] == "PENDING"
    assert "fast-forward" in result["steps"][0]["error"]
    assert workflow.push_calls == []


def test_publish_sources_indeterminate_post_push_and_reconcile(tmp_path: Path):
    source = make_entry(tmp_path, "indeterminate")
    workflow = configure_workflow(
        (source,),
        locks={REV_A: lock_document({})},
        post_push_truth=("error", None),
    )
    result = publication.publish_sources(workflow, (source,), [("indeterminate", REV_A)], dry_run=False)
    assert result["status"] == "INDETERMINATE"
    assert result["state"] == "indeterminate"
    assert len(workflow.push_calls) == 1

    wrong = make_entry(tmp_path, "wrong-after")
    workflow = configure_workflow(
        (wrong,),
        locks={REV_A: lock_document({})},
        push_updates=False,
        post_push_truth=("missing", None),
    )
    result = publication.publish_sources(workflow, (wrong,), [("wrong-after", REV_A)], dry_run=False)
    assert result["status"] == "INDETERMINATE"
    assert result["steps"][0]["remote_after"] is None

    source = make_entry(tmp_path, "reconcile")
    workflow = configure_workflow((source,), locks={REV_A: lock_document({})})
    authority_digest = publication._authority_digest(
        source,
        {
            "remote": "origin",
            "ref": "refs/heads/main",
            "fetch_urls": (source.repository,),
            "push_url": source.repository,
        },
    )
    journal_plan = {
        "local_commits": [{"key": "reconcile", "revision": REV_A}],
        "steps": [{
            "key": "reconcile", "local_commit": REV_A, "remote": "origin",
            "ref": "refs/heads/main", "authority_digest": authority_digest, "state": "pending",
        }],
    }
    path, doc = publication._new_journal(journal_plan)
    publication._journal_set_step(path, doc, "reconcile", "STARTED")
    workflow.truths[("origin", "refs/heads/main")] = ("exact", REV_A)
    result = publication.publish_sources(workflow, (source,), [("reconcile", REV_A)], dry_run=False)
    assert result["status"] == "PUBLISHED"
    assert workflow.push_calls == []

    source = make_entry(tmp_path, "pending-reconcile")
    workflow = configure_workflow((source,), locks={REV_A: lock_document({})})
    authority_digest = publication._authority_digest(
        source,
        {
            "remote": "origin",
            "ref": "refs/heads/main",
            "fetch_urls": (source.repository,),
            "push_url": source.repository,
        },
    )
    path, doc = publication._new_journal({
        "local_commits": [{"key": "pending-reconcile", "revision": REV_A}],
        "steps": [{
            "key": "pending-reconcile", "local_commit": REV_A, "remote": "origin",
            "ref": "refs/heads/main", "authority_digest": authority_digest, "state": "pending",
        }],
    })
    publication._journal_set_step(path, doc, "pending-reconcile", "STARTED")
    result = publication.publish_sources(workflow, (source,), [("pending-reconcile", REV_A)], dry_run=False)
    assert result["status"] == "PENDING"
    assert workflow.push_calls == []


def test_publish_sources_success_with_existing_remote_and_external_dependency(tmp_path: Path):
    source = make_entry(tmp_path, "source")
    workflow = configure_workflow(
        (source,),
        locks={REV_A: lock_document({})},
        truths={("origin", "refs/heads/main"): ("exact", REV_A)},
    )
    result = publication.publish_sources(workflow, (source,), [("source", REV_A)], dry_run=False)
    assert result["status"] == "PUBLISHED"
    assert workflow.push_calls == []

    upper = make_entry(tmp_path, "external-upper")
    external_url = "https://example.invalid/external.git"
    second_url = "https://example.invalid/external-second.git"
    external_lock = lock_document({
        "external": (external_url, REV_B, "main"),
        "external-second": (second_url, REV_D, "main"),
    })
    workflow = configure_workflow((upper,), locks={REV_C: external_lock})
    workflow.truths[(external_url, "refs/heads/main")] = ("exact", REV_B)
    workflow.truths[(second_url, "refs/heads/main")] = ("exact", REV_D)
    result = publication.publish_sources(workflow, (upper,), [("external-upper", REV_C)], dry_run=False)
    assert result["status"] == "PUBLISHED"
    assert len(workflow.push_calls) == 1


def test_publish_sources_blocks_unpublished_selected_dependency(tmp_path: Path):
    source = make_entry(tmp_path, "source", published=False)
    upper = make_entry(tmp_path, "upper")
    locks = {REV_A: lock_document({}), REV_B: lock_document({"source": (source.repository, REV_A, "main")})}
    workflow = configure_workflow((source, upper), locks=locks)
    result = publication.publish_sources(workflow, (source, upper), [("upper", REV_B)], dry_run=False)
    assert result["status"] == "PENDING"
    assert result["steps"][1]["state"] == "pending"
    assert workflow.push_calls == []


def test_publish_sources_blocks_unpublished_external_dependency(tmp_path: Path):
    upper = make_entry(tmp_path, "external-blocked")
    external_url = "https://external.invalid/external-blocked.git"
    locks = {REV_B: lock_document({"external": (external_url, REV_A, "main")})}
    workflow = configure_workflow((upper,), locks=locks)
    result = publication.publish_sources(
        workflow,
        (upper,),
        [("external-blocked", REV_B)],
        dry_run=False,
    )
    assert result["status"] == "PENDING"
    assert "external dependency" in result["steps"][0]["error"]
    assert workflow.push_calls == []


def test_publish_sources_rejects_started_attempt_for_different_target(
    isolated_state: Path, tmp_path: Path
):
    source = make_entry(tmp_path, "target-drift")
    path, document = publication._new_journal({
        "local_commits": [{"key": "target-drift", "revision": REV_A}],
        "steps": [{
            "key": "target-drift", "local_commit": REV_A, "remote": "other",
            "ref": "refs/heads/main", "authority_digest": publication._authority_digest(
                source,
                {"remote": "origin", "ref": "refs/heads/main", "fetch_urls": (source.repository,), "push_url": source.repository},
            ),
            "state": "pending",
        }],
    })
    publication._journal_set_step(path, document, "target-drift", "STARTED")
    workflow = configure_workflow((source,), locks={REV_A: lock_document({})})
    with pytest.raises(ConfigError, match="different remote"):
        publication.publish_sources(workflow, (source,), [("target-drift", REV_A)], dry_run=False)
    assert workflow.push_calls == []


def test_started_attempt_authority_digest_mismatch_fails_closed(
    isolated_state: Path, tmp_path: Path
):
    source = make_entry(tmp_path, "authority-drift")
    workflow = configure_workflow((source,), locks={REV_A: lock_document({})})
    stale_digest = "0" * 64
    path, document = publication._new_journal({
        "local_commits": [{"key": "authority-drift", "revision": REV_A}],
        "steps": [{
            "key": "authority-drift",
            "local_commit": REV_A,
            "remote": "origin",
            "ref": "refs/heads/main",
            "authority_digest": stale_digest,
            "state": "pending",
        }],
    })
    publication._journal_set_step(path, document, "authority-drift", "STARTED")
    before = path.read_bytes()

    with pytest.raises(ConfigError, match="authority differs"):
        publication.publish_sources(
            workflow,
            (source,),
            [("authority-drift", REV_A)],
            dry_run=False,
        )

    assert path.read_bytes() == before
    assert workflow.push_calls == []
    assert not any("ls-remote" in record.argv for record in workflow.records)
    assert len(list((isolated_state / "source-publications").glob("*/journal.json"))) == 1


def test_revalidate_publication_config_detects_changed_authority(tmp_path: Path):
    source = make_entry(tmp_path, "source")
    expected = {"remote": "origin", "ref": "refs/heads/main"}
    workflow = configure_workflow((source,))
    assert publication._revalidate_publication_config(
        workflow, source, expected, allow_local_fixture=False
    )["remote"] == "origin"
    changed = make_entry(tmp_path, "source", remote="other")
    workflow = FakeWorkflow(
        remote_urls={"other": ((changed.repository,), (changed.repository,))}
    )
    with pytest.raises(ConfigError, match="authority changed"):
        publication._revalidate_publication_config(
            workflow, changed, expected, allow_local_fixture=False
        )


@pytest.mark.parametrize("operation", ["create", "update"])
def test_journal_write_size_limit_preserves_recoverable_bytes(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch, operation: str
):
    plan = {
        "local_commits": [{"key": "source", "revision": REV_A}],
        "steps": [{
            "key": "source", "local_commit": REV_A, "remote": "origin",
            "ref": "refs/heads/main", "authority_digest": AUTHORITY_A,
            "state": "pending",
        }],
    }
    if operation == "create":
        monkeypatch.setattr(publication, "_JOURNAL_MAX_BYTES", 1)
        with pytest.raises(ConfigError, match="size limit"):
            publication._new_journal(plan)
        assert not list(isolated_state.rglob("journal.json"))
    else:
        path, document = publication._new_journal(plan)
        before = path.read_bytes()
        monkeypatch.setattr(publication, "_JOURNAL_MAX_BYTES", len(before))
        with pytest.raises(ConfigError, match="size limit"):
            publication._journal_set_step(
                path, document, "source", "INDETERMINATE", error="remote unavailable"
            )
        assert path.read_bytes() == before
