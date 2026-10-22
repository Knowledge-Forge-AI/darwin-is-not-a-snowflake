# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_core.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/local_source.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/source_catalog.py
"""Real local Git/Nix history across the product-root structural transition."""
import dataclasses
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "libexec"))
from nix_darwin_local.deployment_workflow import Workflow
from nix_darwin_local.local_source import materialize_source
from nix_darwin_local.source_catalog import SourceCatalogEntry
from nix_darwin_local.promotion_core import authorize_core_lock_transition, validate_core_lock_authorization
from nix_darwin_local.paths import ConfigError

pytestmark = pytest.mark.skipif(shutil.which("nix") is None, reason="Nix unavailable")


def run(cwd, *args):
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True).stdout.strip()


def commit(repo):
    run(repo, "git", "add", "-A")
    run(repo, "git", "commit", "-qm", "fixture source")
    return run(repo, "git", "rev-parse", "HEAD")


def test_local_history_remains_evaluable_across_flatten_and_catalog_relocation(tmp_path):
    old = tmp_path / ".flakes"
    nested = old / "nix-darwin"
    nested.mkdir(parents=True)
    run(old, "git", "init", "-b", "main")
    run(old, "git", "config", "user.name", "Fixture")
    run(old, "git", "config", "user.email", "fixture@example.invalid")
    (nested / "flake.nix").write_text('{ outputs = { self }: { marker = "same-product"; }; }')
    first = commit(old)
    entry = SourceCatalogEntry("core", old, "https://unreachable.invalid/core.git", "refs/heads/main")
    workflow = Workflow(old, display_command_output=False)
    before = materialize_source(workflow, entry, first)
    (nested / "flake.nix").rename(old / "flake.nix")
    second = commit(old)
    after = materialize_source(workflow, entry, second)
    new = tmp_path / ".local/nix-darwin/darwin-is-not-a-snowflake"
    new.parent.mkdir(parents=True)
    old.rename(new)
    old.mkdir()
    (old / "nix-darwin").symlink_to(new, target_is_directory=True)
    relocated = dataclasses.replace(entry, checkout=new)
    realized = materialize_source(Workflow(new, display_command_output=False), relocated, second)
    assert realized.nar_hash == after.nar_hash
    assert relocated.canonical_repository_identity == entry.canonical_repository_identity
    assert not old.is_symlink() and (old / "nix-darwin").resolve() == new
    for snapshot, selector in ((before, "/nix-darwin"), (after, "")):
        assert run(tmp_path, "nix", "eval", "--raw", "--offline", "--no-update-lock-file", "--no-write-lock-file", f"path:{snapshot.store_path}{selector}#marker") == "same-product"
    # Nix supplies every immutable lock field. Only the portable identity comes from the catalog.
    def lock(source, nested):
        original = {"type": "git", "url": entry.repository, "ref": "refs/heads/main"}
        locked = {**original, "rev": source.revision, "narHash": source.nar_hash,
                  "lastModified": source.locked_metadata["lastModified"]}
        if nested:
            original["dir"] = locked["dir"] = "nix-darwin"
        return {"version": 7, "root": "root", "nodes": {"root": {"inputs": {"core": "core"}}, "core": {"original": original, "locked": locked}}}
    pre, post = lock(before, True), lock(after, False)
    with pytest.raises(ConfigError):
        authorize_core_lock_transition(pre, post, second)
    authorization = authorize_core_lock_transition(pre, post, second, allow_layout_transition=True)
    assert authorization["previousSubflake"] == "nix-darwin"
    assert authorization["subflake"] == ""
    assert validate_core_lock_authorization(authorization) == authorization
    assert str(tmp_path) not in json.dumps(post)
    assert "/nix/store/" not in json.dumps(post)
