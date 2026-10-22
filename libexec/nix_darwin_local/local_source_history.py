"""Exact historical Git/Nix realization beneath a reviewed clean release head."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from . import local_source as source
from .promotion_contract import HEX_REVISION


def _commit(workflow, entry, revision):
    if not isinstance(revision, str) or HEX_REVISION.fullmatch(revision) is None:
        raise source.SourceError("locked source revision must be an exact commit ID")
    label = f"Git locked revision kind ({entry.key})"
    result = source._run_git(workflow, label, Path(entry.checkout), "cat-file", "-t", revision)
    if result.returncode or workflow.complete_stdout(label).strip() != "commit":
        raise source.SourceError(f"repository for {entry.key} does not contain locked revision as a commit")


def check_ancestry(workflow, entry, locked_revision, head_revision, phase):
    checkout = Path(entry.checkout)
    source._check_no_replacement_refs(workflow, entry.key, checkout, phase)
    source._check_no_grafts_or_shallow(workflow, entry.key, checkout, phase)
    label = f"Git verify ancestry {phase} ({entry.key})"
    result = source._run_git(workflow, label, checkout, "merge-base", "--is-ancestor",
                             locked_revision, head_revision)
    if result.returncode != 0:
        raise source.SourceError(f"locked revision is not an ancestor of release head in {entry.key}")


def _fetch_realization(workflow, observed, revision, expected_nar):
    checkout = Path(observed.checkout)
    quote = source._nix_string_literal
    expression = ("let snapshot = builtins.fetchTree { type = \"git\"; "
                  f"url = {quote(checkout.as_uri())}; ref = {quote(observed.release_ref)}; "
                  f"rev = {quote(revision)}; "
                  "}; in (builtins.removeAttrs snapshot [ \"outPath\" ]) // { path = snapshot.outPath; }")
    label = f"materialize source ({observed.key})"
    result = workflow.command(label, ["nix", "eval", "--json", "--impure", "--offline",
                                      "--expr", expression], checkout)
    if result.returncode:
        raise source.SourceError("unable to materialize exact locked source via Nix fetchTree")
    try:
        data = json.loads(workflow.complete_stdout(label))
    except json.JSONDecodeError as error:
        raise source.SourceError("Nix fetchTree returned invalid JSON") from error
    return _realization(data, observed.release_ref, revision, expected_nar)


def _realization(data, release_ref, revision, expected_nar):
    if not isinstance(data, dict) or data.get("rev") != revision:
        raise source.SourceError("Nix materialized revision differs from locked revision")
    if data.get("ref") is not None and source._normalize_locked_ref(data["ref"]) != release_ref:
        raise source.SourceError("Nix materialized release ref differs from configured ref")
    nar = data.get("narHash")
    if not isinstance(nar, str) or source._SRI_NAR_HASH.fullmatch(nar) is None or nar != expected_nar:
        raise source.SourceError("materialized narHash differs from portable lock narHash")
    store = data.get("path")
    if (not isinstance(store, str)
            or re.fullmatch(r"/nix/store/[0-9a-z]{32}-[^/]+", store) is None
            or not Path(store).exists()):
        raise source.SourceError("materialized store path is invalid or absent")
    return {"revision": revision, "store_path": store, "nar_hash": nar}


def realize_historical_source(
    workflow: Any, catalog_entry: Any, locked_revision: str, *,
    expected_repository: str | None = None, expected_ref: str | None = None,
    expected_nar_hash: str | None = None,
) -> tuple[source.LocalSourceIdentity, dict[str, str]]:
    """Observe H truthfully, materialize L exactly, and reject observation races."""
    source._validate_source_environment()
    entry = source.coerce_catalog_entry(catalog_entry)
    release_ref = source._normalize_ref(entry.release_ref)
    _commit(workflow, entry, locked_revision)
    label = f"Git historical release head ({entry.key})"
    result = source._run_git(workflow, label, Path(entry.checkout), "rev-parse", "--verify",
                             f"{release_ref}^{{commit}}")
    if result.returncode:
        raise source.SourceError("configured historical source release ref is unavailable")
    head = workflow.complete_stdout(label).strip()
    observed = source.observe_source(
        workflow, entry, head,
        expected_repository=expected_repository, expected_ref=expected_ref,
    )
    check_ancestry(workflow, entry, locked_revision, head, "initial")
    realization = _fetch_realization(workflow, observed, locked_revision, expected_nar_hash)
    check_ancestry(workflow, entry, locked_revision, head, "final")
    final = source.observe_source(
        workflow, entry, head,
        expected_repository=expected_repository, expected_ref=expected_ref,
    )
    if final.as_dict() != observed.as_dict():
        raise source.SourceError("source authority changed during historical realization")
    return observed, realization
