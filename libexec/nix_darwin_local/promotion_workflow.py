from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import shutil
import stat
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from .deployment_paths import (
    create_private_directory,
    ensure_private_root,
    exclusive_private_write,
    fsync_directory,
    require_private_file,
    validate_ancestor_chain,
)
from .deployment_plan import canonical_bytes, redact_text, sha256_bytes
from .deployment_state import DeploymentLock
from .deployment_workflow import (
    LockCandidate,
    Workflow,
    _guard_processes,
    _materialize_git_tree,
    _migrate_core_declaration_bytes,
    _migrate_composition_declaration_bytes,
    _private_state_root,
    _recover_lock_install,
    _require_isolated_flake_files,
    _resolve_lock_input,
    _restore_lock_preimage,
    _verify_installed_lock,
    install_retained_lock,
    load_retained_lock_candidate,
    prepare_promotion_lock_candidate,
)
from .repository_identity import (
    configured_publication_identity,
    git_status_partition,
    local_git_identity,
    publication_git_identity,
)
from .paths import ConfigError
from .promotion_contract import (
    candidate_reference,
    normalize_nix_repository_url,
    repository_identity_key,
    repository_urls_match,
)
from .promotion_result import (
    compute_host_impact,
    qualification_host_derivations,
)
from .promotion_lock import validate_selected_lock_node
from .promotion_binding import (
    authorize_transition, resolve_binding, release_ref as binding_release_ref,
)
from .promotion_composition import (
    COMPOSITION_AUTHORIZATION_SCHEMA,
    COMPOSITION_INPUT,
    COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_KEY,
    COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL,
    COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_KEY,
    COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_URL,
    validate_composition_transition_contract,
)
from .promotion_core import (
    CORE_REPOSITORY_TRANSITION_PREIMAGE_URL,
    CORE_REPOSITORY_TRANSITION_CANDIDATE_URL,
)
from .promotion_plan import (
    PLAN_SCHEMA,
    PLAN_SCHEMA_V2,
    PLAN_SCHEMA_V3,
    PLAN_SCHEMA_V4,
    PLAN_SCHEMA_CORE_REPOSITORY_TRANSITION_V1,
    PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1,
    RESULT_SCHEMA,
    RESULT_SCHEMA_V1,
    RESULT_SCHEMA_V2,
    PromotionArtifact,
    load_plan,
    write_plan,
    write_result,
)
from .local_source import observe_source, materialize_source
from .local_source_binding import capture_local_sources, revalidate_local_sources, revalidate_composition_catalog
from .promotion_locked_source import capture_promotion_local_sources, revalidate_promotion_local_sources
from .promotion_qualification import (
    evaluate_contract as _evaluate_contract,
    observe_host_derivations as _observe_host_derivations,
    promotion_tool_identity as _promotion_tool_identity,
    qualify_locked_tree as _qualify_locked_tree,
    qualifier_identity as _qualifier_identity,
    run_qualifier as _run_qualifier,
    validate_qualifier_result as _validate_qualifier_result,
    write_json_artifact as _write_json_artifact,
)


TERMINAL_STATES = {
    "PROMOTION_PUBLISHED_AWAITING_ACTIVATION",
    "BLOCKED_BEFORE_MUTATION",
    "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED",
    "COMMITTED_NOT_PUBLISHED",
    "PUBLICATION_INDETERMINATE",
    "PROMOTION_RESULT_EVIDENCE_FAILURE",
    "PROMOTION_PUBLISHED_REPOSITORY_STATE_FAILURE",
    "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORATION_UNPROVEN",
    "LOCAL_PROMOTION_COMMITTED",
    "LOCAL_COMMIT_CREATED",
}

CANONICAL_SNAPSHOT_POLICY = "git-archive-exact-tree-bound-lock-v1"


class PromotionCommitCreated(ConfigError):
    def __init__(self, commit: str, message: str):
        super().__init__(message)
        self.commit = commit


def prepare_promotion(
    workflow: Workflow,
    selected: Path,
    input_name: str,
    revision: str,
    output: Path,
    timeout_seconds: int | None = None,
    *,
    allow_local_fixture: bool = False,
    allow_core_repository_transition: bool = False,
    allow_composition_repository_transition: bool = False,
) -> PromotionArtifact:
    created = False
    is_local = bool(getattr(workflow, "sources", None))
    try:
        if allow_core_repository_transition and allow_composition_repository_transition:
            raise ConfigError("core and composition repository transitions cannot be combined")
        if allow_core_repository_transition and input_name != "core":
            raise ConfigError("core repository transition is valid only for input core")
        if allow_core_repository_transition and not is_local:
            raise ConfigError("core repository transition requires the canonical source catalog")
        if allow_composition_repository_transition and input_name != COMPOSITION_INPUT:
            raise ConfigError("composition repository transition is valid only for input composition-nd")
        if allow_composition_repository_transition and not is_local:
            raise ConfigError("composition repository transition requires the canonical source catalog")
        state_root = _private_state_root()
        with DeploymentLock(state_root / "transaction.lock"):
            if is_local:
                authority = local_git_identity(workflow, selected)
            else:
                authority = _promotion_git_identity(workflow, selected)
            _prepare_output(output, selected, Path(authority["repository_root"]))
            create_private_directory(output, "promotion plan directory")
            created = True
            context = _prepare_promotion_context(
                workflow,
                selected,
                input_name,
                revision,
                timeout_seconds,
                authority,
                allow_local_fixture,
                state_root,
                is_local=is_local,
                allow_core_repository_transition=allow_core_repository_transition,
                allow_composition_repository_transition=allow_composition_repository_transition,
            )
            if (
                context["locked_revision"] == revision
                and not allow_core_repository_transition
                and not allow_composition_repository_transition
            ):
                return _prepare_already_selected(
                    workflow, selected, output, context, state_root
                )
            return _prepare_review_ready(workflow, selected, output, context, state_root)
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as error:
        if not created:
            raise
        schema = (
            PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1
            if allow_composition_repository_transition
            else (
                PLAN_SCHEMA_CORE_REPOSITORY_TRANSITION_V1
                if allow_core_repository_transition
                else (PLAN_SCHEMA_V4 if is_local else PLAN_SCHEMA)
            )
        )
        return _write_inapplicable(
            output, "BLOCKED", [redact_text(str(error))],
            schema=schema,
        )


def _find_source_entry(
    sources: Sequence[Any],
    input_name: str,
    repository_url: str,
    *,
    allow_file: bool = False,
    key_only: bool = False,
) -> Any:
    for s in sources:
        if s.key == input_name:
            return s
    if not key_only:
        for s in sources:
            if repository_urls_match(repository_url, s.repository, allow_file=allow_file):
                return s
    raise ConfigError(f"no source catalog entry found for input {input_name} ({repository_url})")


def _find_composition_source_entry(
    sources: Sequence[Any],
    selected: Path,
    repository_root: Path,
    repository_url: str,
    *,
    allow_file: bool = False,
) -> Any:
    selected_resolved = selected.resolve()
    repo_resolved = repository_root.resolve()
    for s in sources:
        checkout_resolved = Path(s.checkout).resolve()
        if checkout_resolved == selected_resolved or checkout_resolved == repo_resolved:
            if not repository_urls_match(repository_url, s.repository, allow_file=allow_file):
                raise ConfigError("selected composition catalog repository disagrees with native promotion contract")
            return s
    raise ConfigError("no matching source catalog entry found for selected composition")


def _prepare_promotion_context(
    workflow: Workflow,
    selected: Path,
    input_name: str,
    revision: str,
    timeout_seconds: int | None,
    authority: Mapping[str, Any],
    allow_local_fixture: bool,
    state_root: Path,
    *,
    is_local: bool = False,
    allow_core_repository_transition: bool = False,
    allow_composition_repository_transition: bool = False,
) -> dict[str, Any]:
    entry_lock = _read_lock(selected / "flake.lock")
    with _canonical_promotion_snapshot(
        workflow, selected, authority, entry_lock["bytes"], state_root
    ) as entry_tree:
        contract = _evaluate_contract(
            workflow, entry_tree, allow_file_urls=allow_local_fixture
        )
        composition_catalog: dict[str, Any] | None = None
        candidate_entry: Any = None
        local_source_record: dict[str, Any] | None = None
        if is_local:
            comp_entry = _find_composition_source_entry(
                workflow.sources,
                selected,
                Path(authority["repository_root"]),
                contract["composition"]["repositoryUrl"],
                allow_file=allow_local_fixture,
            )
            observe_source(
                workflow,
                comp_entry,
                authority["commit"],
                expected_repository=comp_entry.repository,
                expected_ref=comp_entry.release_ref,
            )
            pub_dict = None
            if comp_entry.publication is not None:
                if hasattr(comp_entry.publication, "__dataclass_fields__"):
                    pub_dict = asdict(comp_entry.publication)
                elif isinstance(comp_entry.publication, Mapping):
                    pub_dict = dict(comp_entry.publication)
                else:
                    pub_dict = {"remote": comp_entry.publication.remote, "ref": comp_entry.publication.ref}
            composition_catalog = {
                "key": comp_entry.key,
                "checkout": str(Path(comp_entry.checkout).resolve()),
                "repository": comp_entry.repository,
                "release_ref": comp_entry.release_ref,
                "publication": pub_dict,
            }
        else:
            if not repository_urls_match(
                contract["composition"]["repositoryUrl"],
                authority["remote"],
                allow_file=allow_local_fixture,
            ):
                raise ConfigError(
                    "selected composition upstream disagrees with its native promotion contract"
                )
        preimage_resolution = resolve_binding(
            contract, input_name, entry_lock["normalized"], allow_file=allow_local_fixture
        )
        if allow_core_repository_transition:
            if preimage_resolution["repository_url"] != CORE_REPOSITORY_TRANSITION_PREIMAGE_URL:
                raise ConfigError("core repository transition requires historical .flakes preimage URL")
            resolution = {
                "rule": "direct-core-subflake",
                "module": "core",
                "repository_url": CORE_REPOSITORY_TRANSITION_CANDIDATE_URL,
            }
        elif allow_composition_repository_transition:
            if (
                repository_identity_key(normalize_nix_repository_url(preimage_resolution["repository_url"], allow_file=allow_local_fixture))
                != COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_KEY
            ):
                raise ConfigError("composition repository transition requires historical composition-nd preimage URL")
            resolution = dict(preimage_resolution)
            resolution["repository_url"] = COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL
        else:
            resolution = preimage_resolution
        repository_url = resolution["repository_url"]
        transport_url = normalize_nix_repository_url(
            repository_url, allow_file=allow_local_fixture
        )
        candidate_ref = candidate_reference(
            repository_url, revision, allow_file=allow_local_fixture
        )
        release_ref = binding_release_ref(contract, input_name)
        if (allow_core_repository_transition or allow_composition_repository_transition) and release_ref != "refs/heads/main":
            raise ConfigError(
                "composition repository transition requires main release ref"
                if allow_composition_repository_transition
                else "core repository transition requires main release ref"
            )
        if is_local:
            candidate_entry = _find_source_entry(
                workflow.sources,
                input_name,
                repository_url,
                allow_file=allow_local_fixture,
                key_only=allow_composition_repository_transition,
            )
            if allow_composition_repository_transition:
                if not repository_urls_match(
                    candidate_entry.repository,
                    COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL,
                    allow_file=allow_local_fixture,
                ):
                    raise ConfigError(
                        "candidate composition source catalog entry must point to canonical composition_dinas repository"
                    )
            observe_source(
                workflow,
                candidate_entry,
                revision,
                expected_repository=repository_url,
                expected_ref=release_ref,
            )
            materialized = materialize_source(
                workflow,
                candidate_entry,
                revision,
                expected_repository=repository_url,
                expected_ref=release_ref,
            )
            local_source_record = {
                "path": materialized.store_path,
                "locked": materialized.locked,
            }
        else:
            _require_canonical_remote_ref(
                workflow,
                selected,
                transport_url,
                release_ref,
                revision,
                "promotion candidate remote release ref",
            )
        qualifier = _qualifier_identity(entry_tree, contract)
        entry_node = _input_node(entry_lock["normalized"], input_name)
        locked_revision = _locked_revision(entry_lock["normalized"], entry_node)
        if locked_revision is None:
            raise ConfigError("selected promotion lock node has no exact revision")
        if input_name != "core":
            expected_entry_node_url = (
                preimage_resolution["repository_url"]
                if allow_composition_repository_transition
                else repository_url
            )
            validate_selected_lock_node(
                entry_lock["normalized"],
                entry_node,
                expected_entry_node_url,
                locked_revision,
                allow_file=allow_local_fixture,
                expected_ref=release_ref,
                allow_implicit_main=contract["version"] == 1,
            )
        baseline_host_observation = _observe_host_derivations(
            workflow,
            entry_tree,
            contract,
            label_prefix="promotion baseline host derivation",
        )
        qualifier_result = qualifier_summary = None
        if (input_name != "core" and not is_local) or (
            locked_revision == revision
            and not allow_core_repository_transition
            and not allow_composition_repository_transition
        ):
            qualifier_result = _run_qualifier(
                workflow,
                entry_tree,
                input_name,
                candidate_ref,
                contract,
                timeout_seconds,
                label="candidate promotion qualifier",
                allow_file_urls=allow_local_fixture,
                is_local=is_local,
            )
            qualifier_summary = _validate_qualifier_result(
                qualifier_result,
                input_name,
                candidate_ref,
                contract,
                allow_file_urls=allow_local_fixture,
            )
    tool = _promotion_tool_identity(workflow.core_dir)
    res = {
        "authority": dict(authority),
        "contract": contract,
        "contract_bytes": canonical_bytes(contract),
        "input": input_name,
        "revision": revision,
        "input_resolution": resolution,
        "repository_url": repository_url,
        "transport_url": transport_url,
        "release_ref": release_ref,
        "candidate_ref": candidate_ref,
        "qualifier": qualifier,
        "tool": tool,
        "entry_lock": entry_lock,
        "entry_node": entry_node,
        "locked_revision": locked_revision,
        "baseline_host_observation": baseline_host_observation,
        "qualifier_result": qualifier_result,
        "qualifier_summary": qualifier_summary,
        "timeout_seconds": timeout_seconds,
        "local_fixture": allow_local_fixture,
        "is_local": is_local,
        "allow_core_repository_transition": allow_core_repository_transition,
        "allow_composition_repository_transition": allow_composition_repository_transition,
    }
    if allow_core_repository_transition or allow_composition_repository_transition:
        res["preimage_input_resolution"] = preimage_resolution
    if is_local:
        res["composition_catalog"] = composition_catalog
        res["candidate_entry"] = candidate_entry
        res["local_source"] = local_source_record
    return res


def _prepare_already_selected(
    workflow: Workflow,
    selected: Path,
    output: Path,
    context: Mapping[str, Any],
    state_root: Path,
) -> PromotionArtifact:
    if context.get("is_local"):
        context = dict(context)
        context["realized_lock"] = context["entry_lock"]["normalized"]
        selected_node = _input_node(context["realized_lock"], context["input"])
        context["local_sources"] = capture_promotion_local_sources(
            workflow,
            context["realized_lock"],
            preimage_lock=context["realized_lock"],
            selected_node=selected_node,
        )
    qualifier_binding = _write_json_artifact(
        output,
        "qualifier-result.json",
        context["qualifier_result"],
        summary=context["qualifier_summary"],
    )
    with _canonical_promotion_snapshot(
        workflow,
        selected,
        context["authority"],
        context["entry_lock"]["bytes"],
        state_root,
    ) as selected_tree:
        locked_qualification = _qualify_locked_tree(
            workflow,
            selected_tree,
            context["input"],
            context["candidate_ref"],
            context["contract"],
            context["timeout_seconds"],
            output,
            allow_file_urls=context["local_fixture"],
            expected_result_identity=context["qualifier_summary"]["identity"],
            is_local=bool(context.get("is_local")),
        )
    candidate_hosts = qualification_host_derivations(
        locked_qualification, context["contract"]["hosts"]
    )
    impact = compute_host_impact(
        context["baseline_host_observation"]["hosts"],
        candidate_hosts,
        context["contract"]["hosts"],
    )
    if impact["classification"] != "HOST_DERIVATIONS_IDENTICAL":
        raise ConfigError("already-selected promotion host derivations changed")
    _revalidate_prepare(workflow, selected, context, state_root)
    lock_path = "selected-flake.lock"
    exclusive_private_write(output / lock_path, context["entry_lock"]["bytes"])
    document = _already_selected_plan_document(
        context,
        qualifier_binding,
        locked_qualification,
        _lock_binding(
            lock_path,
            context["entry_lock"]["bytes"],
            context["entry_lock"]["normalized"],
        ),
        impact,
    )
    if context.get("is_local"):
        document["local_sources"] = context["local_sources"]
    return write_plan(output, document, _plan_markdown(document))


def _prepare_review_ready(
    workflow: Workflow,
    selected: Path,
    output: Path,
    context: Mapping[str, Any],
    state_root: Path,
) -> PromotionArtifact:
    is_core_repo_transition = bool(context.get("allow_core_repository_transition"))
    is_comp_repo_transition = bool(context.get("allow_composition_repository_transition"))
    candidate = prepare_promotion_lock_candidate(
        workflow,
        selected,
        context["input"],
        context["repository_url"],
        context["revision"],
        output / "retained-lock",
        authority=context["authority"],
        allow_file=context["local_fixture"],
        expected_ref=context["release_ref"],
        allow_implicit_main=context["contract"]["version"] == 1,
        local_source=context.get("local_source"),
        allow_core_repository_transition=is_core_repo_transition,
        allow_composition_repository_transition=is_comp_repo_transition,
    )
    candidate_node = _input_node(candidate.candidate, context["input"])
    if candidate_node != context["entry_node"]:
        raise ConfigError("promotion targeted lock input resolved to a different node")
    _revalidate_closure_authorization(
        context, candidate, str(context["repository_url"])
    )
    if context.get("is_local"):
        context = dict(context)
        context["realized_lock"] = candidate.candidate
        if is_core_repo_transition or is_comp_repo_transition:
            context["local_sources"] = capture_local_sources(workflow, candidate.candidate)
        else:
            context["local_sources"] = capture_promotion_local_sources(
                workflow,
                candidate.candidate,
                preimage_lock=candidate.preimage,
                selected_node=candidate_node,
            )
    with _canonical_promotion_snapshot(
        workflow,
        selected,
        context["authority"],
        candidate.candidate_bytes,
        state_root,
        declaration_bytes=candidate.candidate_flake_nix_bytes,
    ) as reviewed_tree:
        candidate_contract = _evaluate_contract(
            workflow,
            reviewed_tree,
            allow_file_urls=context["local_fixture"],
        )
        if is_comp_repo_transition:
            validate_composition_transition_contract(
                context["contract"],
                candidate_contract,
                allow_file=context["local_fixture"],
            )
        else:
            if canonical_bytes(candidate_contract) != context["contract_bytes"]:
                raise ConfigError(
                    "promotion candidate contract differs from the reviewed entry contract"
                )
        if _qualifier_identity(reviewed_tree, candidate_contract) != context["qualifier"]:
            raise ConfigError("promotion candidate qualifier identity differs from entry")
        if context["input"] == "core" or context.get("is_local"):
            input_name = context["input"]
            context = dict(context)
            context["qualifier_result"] = _run_qualifier(
                workflow,
                reviewed_tree,
                input_name,
                context["candidate_ref"],
                candidate_contract,
                context["timeout_seconds"],
                label="candidate core qualification"
                if input_name == "core"
                else f"candidate {input_name} qualification",
                allow_file_urls=context["local_fixture"],
                is_local=bool(context.get("is_local")),
            )
            context["qualifier_summary"] = _validate_qualifier_result(
                context["qualifier_result"],
                input_name,
                context["candidate_ref"],
                candidate_contract,
                allow_file_urls=context["local_fixture"],
            )
        qualifier_binding = _write_json_artifact(
            output, "qualifier-result.json", context["qualifier_result"],
            summary=context["qualifier_summary"],
        )
        locked_qualification = _qualify_locked_tree(
            workflow,
            reviewed_tree,
            context["input"],
            context["candidate_ref"],
            candidate_contract if is_comp_repo_transition else context["contract"],
            context["timeout_seconds"],
            output,
            allow_file_urls=context["local_fixture"],
            expected_result_identity=context["qualifier_summary"]["identity"],
            is_local=bool(context.get("is_local")),
        )
    impact = compute_host_impact(
        context["baseline_host_observation"]["hosts"],
        qualification_host_derivations(
            locked_qualification, context["contract"]["hosts"]
        ),
        context["contract"]["hosts"],
    )
    _revalidate_prepare(workflow, selected, context, state_root)
    document = _ready_plan_document(
        workflow,
        output,
        context["tool"],
        context["authority"],
        context["contract"],
        context["qualifier"],
        context["input"],
        context["repository_url"],
        context["revision"],
        context["candidate_ref"],
        candidate,
        qualifier_binding,
        locked_qualification,
        context["baseline_host_observation"],
        impact,
        input_resolution=context["input_resolution"],
        transport_url=context["transport_url"],
        local_fixture=context["local_fixture"],
        is_local=bool(context.get("is_local")),
        composition_catalog=context.get("composition_catalog"),
        allow_core_repository_transition=is_core_repo_transition,
        allow_composition_repository_transition=is_comp_repo_transition,
        preimage_input_resolution=context.get("preimage_input_resolution"),
        candidate_contract=candidate_contract if is_comp_repo_transition else None,
    )
    if context.get("is_local"):
        document["local_sources"] = context["local_sources"]
    return write_plan(output, document, _plan_markdown(document))


def _revalidate_prepare(
    workflow: Workflow,
    selected: Path,
    context: Mapping[str, Any],
    state_root: Path,
) -> None:
    is_local = bool(context.get("is_local"))
    if is_local:
        revalidate_composition_catalog(workflow, context["composition_catalog"], context["authority"]["commit"])
        is_repo_trans = bool(context.get("allow_core_repository_transition") or context.get("allow_composition_repository_transition"))
        if is_repo_trans:
            revalidate_local_sources(workflow, context["realized_lock"], context["local_sources"])
        else:
            selected_node = _input_node(context["realized_lock"], context["input"])
            preimage = context["entry_lock"]["normalized"] if "entry_lock" in context else None
            revalidate_promotion_local_sources(
                workflow,
                context["realized_lock"],
                context["local_sources"],
                preimage_lock=preimage,
                selected_node=selected_node,
            )
        if local_git_identity(workflow, selected) != context["authority"]:
            raise ConfigError("selected composition Git authority drifted during promotion prepare")
    else:
        if _promotion_git_identity(workflow, selected) != context["authority"]:
            raise ConfigError("selected composition Git authority drifted during promotion prepare")
    if (selected / "flake.lock").read_bytes() != context["entry_lock"]["bytes"]:
        raise ConfigError("selected composition lock changed during promotion prepare")
    with _canonical_promotion_snapshot(
        workflow,
        selected,
        context["authority"],
        context["entry_lock"]["bytes"],
        state_root,
    ) as baseline_tree:
        contract = _evaluate_contract(
            workflow, baseline_tree, allow_file_urls=context["local_fixture"]
        )
        if canonical_bytes(contract) != context["contract_bytes"]:
            raise ConfigError(
                "selected composition native promotion contract drifted during prepare"
            )
        if _qualifier_identity(baseline_tree, contract) != context["qualifier"]:
            raise ConfigError("selected composition qualifier identity drifted during prepare")
        current_baseline = _observe_host_derivations(
            workflow,
            baseline_tree,
            contract,
            label_prefix="promotion final baseline host derivation",
        )
        if current_baseline["hosts"] != context["baseline_host_observation"]["hosts"]:
            raise ConfigError(
                "selected composition baseline host derivations drifted during prepare"
            )
    if _promotion_tool_identity(workflow.core_dir) != context["tool"]:
        raise ConfigError("promotion tool identity drifted during prepare")
    if is_local:
        observe_source(
            workflow,
            context["candidate_entry"],
            context["revision"],
            expected_repository=context["repository_url"],
            expected_ref=context["release_ref"],
        )
    else:
        _require_canonical_remote_ref(
            workflow,
            selected,
            context["transport_url"],
            context["release_ref"],
            context["revision"],
            "promotion final candidate remote release ref",
        )


def apply_promotion(
    workflow: Workflow, plan_path: Path, *, confirm: Callable[[str], str] | None = None
) -> int:
    artifact = load_plan(plan_path)
    if artifact.document["status"] != "READY_FOR_REVIEW":
        raise ConfigError("blocked, already-selected, or preflight promotion artifacts are inapplicable")
    expected = f"APPLY NIX-UPDATE PROMOTION {artifact.digest}"
    try:
        supplied = (confirm or input)(f"Type '{expected}' to continue: ")
    except EOFError as error:
        raise ConfigError("promotion confirmation was not supplied") from error
    if supplied != expected:
        raise ConfigError("promotion confirmation text did not match the reviewed plan digest")
    document = artifact.document
    is_local = (
        document.get("schema")
        in {
            PLAN_SCHEMA_V4,
            PLAN_SCHEMA_V3,
            PLAN_SCHEMA_CORE_REPOSITORY_TRANSITION_V1,
            PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1,
        }
    )
    authority_doc = document.get("source_authority") or document.get("repository")
    selected = _selected_flake_path(authority_doc)
    result = _initial_result(artifact.digest, document["impact"], is_local=is_local)
    # Campaigns reuse a workflow; earlier stages retain their own evidence.
    result["_command_start"] = len(workflow.records)
    try:
        state_root = _private_state_root()
        with DeploymentLock(state_root / "transaction.lock"):
            recovered = _recover_consumed_promotion(
                workflow, artifact, selected, result, is_local=is_local
            )
            if recovered is not None:
                return recovered
            candidate = _revalidate_apply(
                workflow, artifact, selected, state_root, is_local=is_local
            )
            _consume_authority(artifact.digest, authority_doc)
            if is_local:
                return _install_qualify_commit_local(
                    workflow, artifact, selected, candidate, result, state_root
                )
            return _install_qualify_commit_publish(
                workflow, artifact, selected, candidate, result, state_root
            )
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as error:
        result["failure"] = redact_text(str(error))
        return _finish_result(artifact.directory, result, workflow, 1)


def _install_qualify_commit_publish(
    workflow: Workflow,
    artifact: PromotionArtifact,
    selected: Path,
    candidate: LockCandidate,
    result: dict[str, Any],
    state_root: Path,
) -> int:
    document = artifact.document
    try:
        metadata = install_retained_lock(selected, candidate)
    except (ConfigError, OSError) as error:
        restored = _install_failure_restored(candidate, selected)
        result["terminal_state"] = (
            "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED"
            if restored else "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORATION_UNPROVEN"
        )
        result["failure"] = redact_text(str(error))
        result["recovery"] = (
            "The exact reviewed flake.lock preimage was restored; prepare a new plan."
            if restored else "Lock restoration is unproven; inspect the repository before action."
        )
        return _finish_result(artifact.directory, result, workflow, 1)
    try:
        with _canonical_promotion_snapshot(
            workflow,
            selected,
            document["repository"],
            candidate.candidate_bytes,
            state_root,
            declaration_bytes=candidate.candidate_flake_nix_bytes,
        ) as candidate_tree:
            installed_contract = _evaluate_contract(
                workflow,
                candidate_tree,
                allow_file_urls=document["local_fixture"],
            )
            if (
                canonical_bytes(installed_contract).decode("ascii")
                != document["contract_canonical"]
            ):
                raise ConfigError(
                    "installed promotion candidate contract drifted from the reviewed plan"
                )
            if _qualifier_identity(candidate_tree, installed_contract) != document["qualifier"]:
                raise ConfigError(
                    "installed promotion qualifier identity drifted from the reviewed plan"
                )
            installed_qualification = _qualify_locked_tree(
                workflow,
                candidate_tree,
                document["input"],
                document["candidate_ref"],
                installed_contract,
                None,
                artifact.directory,
                retain=False,
                allow_file_urls=document["local_fixture"],
                expected_result_identity=document["qualifier_result"]["summary"]["identity"],
                is_local=False,
            )
        installed_hosts = qualification_host_derivations(
            installed_qualification, document["contract"]["hosts"]
        )
        installed_impact = compute_host_impact(
            document["impact"]["baselineHosts"],
            installed_hosts,
            document["contract"]["hosts"],
        )
        if installed_impact != document["impact"]:
            raise ConfigError("installed promotion host impact drifted from the reviewed plan")
        _revalidate_commit_authority(
            workflow, selected, document, candidate, state_root, staged=False
        )
        expected_tree = _stage_only_lock(
            workflow, selected, document["repository"],
            changed_paths=document["commit"]["changed_paths"],
        )
        _revalidate_commit_authority(
            workflow, selected, document, candidate, state_root, staged=True
        )
        committed = _commit_promotion(workflow, selected, document, expected_tree)
    except PromotionCommitCreated as error:
        result["terminal_state"] = "COMMITTED_NOT_PUBLISHED"
        result["published_commit"] = error.commit
        result["failure"] = redact_text(str(error))
        result["recovery"] = "Preserve the commit and reconcile publication without history rewrite."
        return _finish_result(artifact.directory, result, workflow, 1)
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as error:
        result["failure"] = redact_text(str(error))
        try:
            _unstage_lock(workflow, selected, changed_paths=document["commit"]["changed_paths"])
            lock_metadata = {k: v for k, v in metadata.items() if k != "declaration"}
            _restore_lock_preimage(selected / "flake.lock", candidate.preimage_bytes, lock_metadata)
            if candidate.preimage_flake_nix_bytes is not None and "declaration" in metadata:
                _restore_lock_preimage(selected / "flake.nix", candidate.preimage_flake_nix_bytes, metadata["declaration"]["preimage_metadata"])
        except (ConfigError, OSError) as restore_error:
            result["terminal_state"] = "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORATION_UNPROVEN"
            result["failure"] = redact_text(f"{error}; restoration failed: {restore_error}")
            result["recovery"] = "Lock restoration is unproven; inspect the repository before action."
        else:
            result["terminal_state"] = "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED"
            result["recovery"] = "The exact reviewed preimage files were restored; prepare a new plan."
        return _finish_result(artifact.directory, result, workflow, 1)
    try:
        return _publish_commit(workflow, artifact, selected, committed, result)
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as error:
        result["terminal_state"] = "PUBLICATION_INDETERMINATE"
        result["published_commit"] = committed
        result["failure"] = redact_text(str(error))
        result["recovery"] = "Preserve the commit and establish exact remote truth before any retry."
        return _finish_result(artifact.directory, result, workflow, 1)


def _initial_result(digest: str, impact: Mapping[str, Any], *, is_local: bool = False) -> dict[str, Any]:
    if is_local:
        return {
            "schema": RESULT_SCHEMA_V2,
            "plan_digest": digest,
            "no_activation": True,
            "terminal_state": "BLOCKED_BEFORE_MUTATION",
            "local_commit": None,
            "published_commit": None,
            "publication_state": "unavailable",
            "activation_state": "not_performed",
            "started_at": _now(),
            "finished_at": None,
            "failure": None,
            "recovery": None,
            "impact": dict(impact),
            "commands": [],
            "result_path": "",
        }
    return {
        "plan_digest": digest,
        "terminal_state": "BLOCKED_BEFORE_MUTATION",
        "published_commit": None,
        "started_at": _now(),
        "finished_at": None,
        "failure": None,
        "recovery": None,
        "impact": dict(impact),
        "no_activation": True,
    }


def _recover_consumed_promotion(
    workflow: Workflow,
    artifact: PromotionArtifact,
    selected: Path,
    result: dict[str, Any],
    *,
    is_local: bool = False,
) -> int | None:
    marker = _consumption_path(artifact.digest)
    if not marker.exists() and not marker.is_symlink():
        return None
    document = artifact.document
    authority_doc = document.get("source_authority") or document.get("repository")
    candidate = load_retained_lock_candidate(
        artifact.directory / "retained-lock",
        authority_doc,
        document["entry_lock"]["sha256"],
        document["candidate_lock"]["sha256"],
        expected_closure_authorization=document["closure_authorization"],
        expected_evidence_sha256=document["lock_authorization_evidence"]["sha256"],
    )
    head = _git_value(workflow, selected, "promotion recovery HEAD", ["git", "rev-parse", "HEAD"])
    if head != authority_doc["commit"]:
        if is_local:
            return _reconcile_consumed_commit_local(
                workflow, artifact, selected, candidate, head, result
            )
        return _reconcile_consumed_commit(
            workflow, artifact, selected, candidate, head, result
        )
    try:
        recovered_authority = _recover_lock_install(
            candidate.output,
            selected,
            authority_doc,
            restore_installed=True,
        )
        if recovered_authority is not None and recovered_authority != authority_doc:
            raise ConfigError("recovered promotion authority does not match the reviewed repository")
        _unstage_lock(workflow, selected, changed_paths=document.get("commit", {}).get("changed_paths", ["flake.lock"]))
        if (selected / "flake.lock").read_bytes() != candidate.preimage_bytes:
            raise ConfigError("consumed promotion did not restore the exact lock preimage")
        if candidate.preimage_flake_nix_bytes is not None and (selected / "flake.nix").read_bytes() != candidate.preimage_flake_nix_bytes:
            raise ConfigError("consumed promotion did not restore the exact declaration preimage")
        status = _git_value(
            workflow,
            selected,
            "promotion recovered worktree status",
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        )
        if status:
            raise ConfigError("consumed promotion recovery did not leave a clean repository")
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as error:
        result["terminal_state"] = "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORATION_UNPROVEN"
        result["failure"] = redact_text(str(error))
        result["recovery"] = "Lock restoration is unproven; inspect the repository before action."
    else:
        result["terminal_state"] = "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED"
        result["recovery"] = "Interrupted promotion authority was consumed and the exact lock preimage was restored."
    return _finish_result(artifact.directory, result, workflow, 1)


def _reconcile_consumed_commit(
    workflow: Workflow,
    artifact: PromotionArtifact,
    selected: Path,
    candidate: LockCandidate,
    head: str,
    result: dict[str, Any],
) -> int:
    document = artifact.document
    result["published_commit"] = head
    try:
        expected_changed = sorted(document.get("commit", {}).get("changed_paths", ["flake.lock"]))
        local = local_git_identity(workflow, selected)
        configured = configured_publication_identity(workflow, selected, local["branch"])
        if (
            local["branch"] != document["repository"]["branch"]
            or any(configured[key] != document["repository"][key] for key in configured)
            or (selected / "flake.lock").read_bytes() != candidate.candidate_bytes
            or (candidate.candidate_flake_nix_bytes is not None and (selected / "flake.nix").read_bytes() != candidate.candidate_flake_nix_bytes)
        ):
            raise ConfigError("promotion recovery repository or publication authority drifted")
        parent = _git_value(workflow, selected, "promotion recovery commit parent", ["git", "rev-parse", "HEAD^"])
        changed = sorted(_git_value(
            workflow,
            selected,
            "promotion recovery commit changed paths",
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
        ).splitlines())
        shown = workflow.command(
            "promotion recovery committed lock",
            ["git", "show", "HEAD:flake.lock"],
            selected,
        )
        if (
            shown.returncode
            or parent != document["repository"]["commit"]
            or changed != expected_changed
            or workflow.complete_stdout("promotion recovery committed lock").encode() != candidate.candidate_bytes
        ):
            raise ConfigError("consumed promotion commit does not match the reviewed candidate")
        if candidate.candidate_flake_nix_bytes is not None:
            shown_decl = workflow.command(
                "promotion recovery committed declaration",
                ["git", "show", "HEAD:flake.nix"],
                selected,
            )
            if (
                shown_decl.returncode
                or workflow.complete_stdout("promotion recovery committed declaration").encode() != candidate.candidate_flake_nix_bytes
            ):
                raise ConfigError("consumed promotion commit does not match the reviewed declaration candidate")
        remote = _remote_head(workflow, selected, document["publication"])
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as error:
        result["terminal_state"] = "PUBLICATION_INDETERMINATE"
        result["failure"] = redact_text(str(error))
        result["recovery"] = "Preserve the commit and establish exact remote truth before any retry."
        return _finish_result(artifact.directory, result, workflow, 1)
    if remote == head:
        return _finish_published(workflow, artifact, selected, head, result)
    if remote == document["repository"]["commit"]:
        return _publish_commit(workflow, artifact, selected, head, result)
    else:
        result["terminal_state"] = "PUBLICATION_INDETERMINATE"
        result["recovery"] = "Preserve the commit and establish exact remote truth before any retry."
    return _finish_result(artifact.directory, result, workflow, 1)


def _reconcile_consumed_commit_local(
    workflow: Workflow,
    artifact: PromotionArtifact,
    selected: Path,
    candidate: LockCandidate,
    head: str,
    result: dict[str, Any],
) -> int:
    document = artifact.document
    authority_doc = document.get("source_authority") or document.get("repository")
    result["local_commit"] = head
    result["published_commit"] = None
    result["publication_state"] = "pending" if document["composition_catalog"]["publication"] else "unavailable"
    result["activation_state"] = "not_performed"
    try:
        revalidate_composition_catalog(workflow, document["composition_catalog"], head)
        if document["schema"] == PLAN_SCHEMA_V4:
            selected_node = _input_node(candidate.candidate, document["input"])
            revalidate_promotion_local_sources(
                workflow,
                candidate.candidate,
                document["local_sources"],
                preimage_lock=candidate.preimage if hasattr(candidate, "preimage") else None,
                selected_node=selected_node,
            )
        else:
            revalidate_local_sources(workflow, candidate.candidate, document["local_sources"])
        expected_changed = sorted(document.get("commit", {}).get("changed_paths", ["flake.lock"]))
        local = local_git_identity(workflow, selected)
        if (
            local["branch"] != authority_doc["branch"]
            or (selected / "flake.lock").read_bytes() != candidate.candidate_bytes
            or (candidate.candidate_flake_nix_bytes is not None and (selected / "flake.nix").read_bytes() != candidate.candidate_flake_nix_bytes)
        ):
            raise ConfigError("promotion recovery repository authority drifted")
        parent = _git_value(workflow, selected, "promotion recovery commit parent", ["git", "rev-parse", "HEAD^"])
        changed = sorted(_git_value(
            workflow,
            selected,
            "promotion recovery commit changed paths",
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
        ).splitlines())
        shown = workflow.command(
            "promotion recovery committed lock",
            ["git", "show", "HEAD:flake.lock"],
            selected,
        )
        if (
            shown.returncode
            or parent != authority_doc["commit"]
            or changed != expected_changed
            or workflow.complete_stdout("promotion recovery committed lock").encode() != candidate.candidate_bytes
        ):
            raise ConfigError("consumed promotion commit does not match the reviewed candidate")
        if candidate.candidate_flake_nix_bytes is not None:
            shown_decl = workflow.command(
                "promotion recovery committed declaration",
                ["git", "show", "HEAD:flake.nix"],
                selected,
            )
            if (
                shown_decl.returncode
                or workflow.complete_stdout("promotion recovery committed declaration").encode() != candidate.candidate_flake_nix_bytes
            ):
                raise ConfigError("consumed promotion commit does not match the reviewed declaration candidate")
        _verify_local_committed_repository(workflow, selected, head)
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as error:
        result["terminal_state"] = "LOCAL_COMMIT_CREATED"
        result["failure"] = redact_text(str(error))
        result["recovery"] = "Preserve the commit and reconcile local repository state without history rewrite."
        return _finish_result(artifact.directory, result, workflow, 1)
    result["terminal_state"] = "LOCAL_PROMOTION_COMMITTED"
    result["recovery"] = "Activation remains a separate nix-update deploy transaction."
    return _finish_result(artifact.directory, result, workflow, 0)


def _publish_commit(
    workflow: Workflow,
    artifact: PromotionArtifact,
    selected: Path,
    committed: str,
    result: dict[str, Any],
) -> int:
    document = artifact.document
    remote_before = _remote_head(workflow, selected, document["publication"])
    if remote_before != document["repository"]["commit"]:
        result["terminal_state"] = "COMMITTED_NOT_PUBLISHED"
        result["published_commit"] = committed
        result["failure"] = "upstream advanced after the promotion commit was created"
        result["recovery"] = "Preserve the commit; reconcile upstream manually without reset, amend, or rebase."
        return _finish_result(artifact.directory, result, workflow, 1)
    push = workflow.command(
        "promotion fast-forward push",
        [
            "git", "push", document["publication"]["remote_name"],
            f"HEAD:{document['publication']['remote_ref']}",
        ],
        selected,
    )
    remote_after = _remote_head(workflow, selected, document["publication"])
    if remote_after == committed:
        return _finish_published(workflow, artifact, selected, committed, result)
    if push.returncode and remote_after == document["repository"]["commit"]:
        result["terminal_state"] = "COMMITTED_NOT_PUBLISHED"
        result["published_commit"] = committed
        result["failure"] = "ordinary fast-forward push was rejected"
        result["recovery"] = "Preserve the commit and resolve publication manually; do not rewrite it."
        return _finish_result(artifact.directory, result, workflow, 1)
    result["terminal_state"] = "PUBLICATION_INDETERMINATE"
    result["published_commit"] = committed
    result["failure"] = "remote branch truth could not be established after push"
    result["recovery"] = "Inspect the exact upstream branch before any retry; never retry blindly."
    return _finish_result(artifact.directory, result, workflow, 1)


def _finish_published(
    workflow: Workflow,
    artifact: PromotionArtifact,
    selected: Path,
    committed: str,
    result: dict[str, Any],
) -> int:
    result["terminal_state"] = "PROMOTION_PUBLISHED_AWAITING_ACTIVATION"
    result["published_commit"] = committed
    result["recovery"] = "Activation remains a separate nix-update prepare/apply transaction."
    try:
        _verify_published_repository(workflow, selected, committed)
    except (ConfigError, OSError) as error:
        result["terminal_state"] = "PROMOTION_PUBLISHED_REPOSITORY_STATE_FAILURE"
        result["failure"] = redact_text(str(error))
        result["recovery"] = "Publication is confirmed; inspect local cleanliness and parity without rewriting the commit."
        return _finish_result(artifact.directory, result, workflow, 1)
    return _finish_result(artifact.directory, result, workflow, 0)


def _install_qualify_commit_local(
    workflow: Workflow,
    artifact: PromotionArtifact,
    selected: Path,
    candidate: LockCandidate,
    result: dict[str, Any],
    state_root: Path,
) -> int:
    document = artifact.document
    authority_doc = document.get("source_authority") or document.get("repository")
    try:
        metadata = install_retained_lock(selected, candidate)
    except (ConfigError, OSError) as error:
        restored = _install_failure_restored(candidate, selected)
        result["terminal_state"] = (
            "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED"
            if restored else "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORATION_UNPROVEN"
        )
        result["failure"] = redact_text(str(error))
        result["recovery"] = (
            "The exact reviewed flake.lock preimage was restored; prepare a new plan."
            if restored else "Lock restoration is unproven; inspect the repository before action."
        )
        return _finish_result(artifact.directory, result, workflow, 1)
    try:
        with _canonical_promotion_snapshot(
            workflow,
            selected,
            authority_doc,
            candidate.candidate_bytes,
            state_root,
            declaration_bytes=candidate.candidate_flake_nix_bytes,
        ) as candidate_tree:
            installed_contract = _evaluate_contract(
                workflow,
                candidate_tree,
                allow_file_urls=document["local_fixture"],
            )
            expected_canonical = (
                document["candidate_contract_canonical"]
                if document.get("schema") == PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1
                else document["contract_canonical"]
            )
            if (
                canonical_bytes(installed_contract).decode("ascii")
                != expected_canonical
            ):
                raise ConfigError(
                    "installed promotion candidate contract drifted from the reviewed plan"
                )
            if _qualifier_identity(candidate_tree, installed_contract) != document["qualifier"]:
                raise ConfigError(
                    "installed promotion qualifier identity drifted from the reviewed plan"
                )
            installed_qualification = _qualify_locked_tree(
                workflow,
                candidate_tree,
                document["input"],
                document["candidate_ref"],
                installed_contract,
                None,
                artifact.directory,
                retain=False,
                allow_file_urls=document["local_fixture"],
                expected_result_identity=document["qualifier_result"]["summary"]["identity"],
                is_local=True,
            )
        installed_hosts = qualification_host_derivations(
            installed_qualification, document["contract"]["hosts"]
        )
        installed_impact = compute_host_impact(
            document["impact"]["baselineHosts"],
            installed_hosts,
            document["contract"]["hosts"],
        )
        if installed_impact != document["impact"]:
            raise ConfigError("installed promotion host impact drifted from the reviewed plan")
        _revalidate_commit_authority_local(
            workflow, selected, document, candidate, state_root, staged=False
        )
        expected_tree = _stage_only_lock(
            workflow, selected, authority_doc,
            changed_paths=document["commit"]["changed_paths"],
        )
        _revalidate_commit_authority_local(
            workflow, selected, document, candidate, state_root, staged=True
        )
        committed = _commit_promotion(workflow, selected, document, expected_tree)
    except PromotionCommitCreated as error:
        result["terminal_state"] = "LOCAL_COMMIT_CREATED"
        result["local_commit"] = error.commit
        result["published_commit"] = None
        result["publication_state"] = "pending" if document["composition_catalog"]["publication"] else "unavailable"
        result["failure"] = redact_text(str(error))
        result["recovery"] = "Preserve the commit and reconcile local repository state without history rewrite."
        return _finish_result(artifact.directory, result, workflow, 1)
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as error:
        result["failure"] = redact_text(str(error))
        try:
            _unstage_lock(workflow, selected, changed_paths=document["commit"]["changed_paths"])
            lock_metadata = {k: v for k, v in metadata.items() if k != "declaration"}
            _restore_lock_preimage(selected / "flake.lock", candidate.preimage_bytes, lock_metadata)
            if candidate.preimage_flake_nix_bytes is not None and "declaration" in metadata:
                _restore_lock_preimage(selected / "flake.nix", candidate.preimage_flake_nix_bytes, metadata["declaration"]["preimage_metadata"])
        except (ConfigError, OSError) as restore_error:
            result["terminal_state"] = "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORATION_UNPROVEN"
            result["failure"] = redact_text(f"{error}; restoration failed: {restore_error}")
            result["recovery"] = "Lock restoration is unproven; inspect the repository before action."
        else:
            result["terminal_state"] = "FAILED_PRE_PUBLICATION_PREIMAGE_RESTORED"
            result["recovery"] = "The exact reviewed preimage files were restored; prepare a new plan."
        return _finish_result(artifact.directory, result, workflow, 1)
    return _finish_local_committed(workflow, artifact, selected, committed, result)


def _verify_local_committed_repository(workflow: Workflow, selected: Path, commit: str) -> None:
    identity = local_git_identity(workflow, selected)
    if identity["commit"] != commit:
        raise ConfigError("committed promotion repository did not reach expected local commit")


def _finish_local_committed(
    workflow: Workflow,
    artifact: PromotionArtifact,
    selected: Path,
    committed: str,
    result: dict[str, Any],
) -> int:
    result["terminal_state"] = "LOCAL_PROMOTION_COMMITTED"
    result["local_commit"] = committed
    result["published_commit"] = None
    result["publication_state"] = "pending" if artifact.document["composition_catalog"]["publication"] else "unavailable"
    result["activation_state"] = "not_performed"
    result["recovery"] = "Activation remains a separate nix-update deploy transaction."
    try:
        _verify_local_committed_repository(workflow, selected, committed)
    except (ConfigError, OSError) as error:
        result["terminal_state"] = "LOCAL_COMMIT_CREATED"
        result["failure"] = redact_text(str(error))
        result["recovery"] = "Local commit exists; inspect repository cleanliness without rewriting the commit."
        return _finish_result(artifact.directory, result, workflow, 1)
    return _finish_result(artifact.directory, result, workflow, 0)


def _revalidate_commit_authority_local(
    workflow: Workflow,
    selected: Path,
    document: Mapping[str, Any],
    candidate: LockCandidate,
    state_root: Path,
    *,
    staged: bool,
) -> None:
    authority_doc = document.get("source_authority") or document.get("repository")
    revalidate_composition_catalog(workflow, document["composition_catalog"], authority_doc["commit"], installed_lock=True)
    if document["schema"] == PLAN_SCHEMA_V4:
        selected_node = _input_node(candidate.candidate, document["input"])
        revalidate_promotion_local_sources(
            workflow,
            candidate.candidate,
            document["local_sources"],
            preimage_lock=candidate.preimage if hasattr(candidate, "preimage") else None,
            selected_node=selected_node,
        )
    else:
        revalidate_local_sources(workflow, candidate.candidate, document["local_sources"])
    values = {
        "branch": _git_value(
            workflow, selected, "promotion immediate branch", ["git", "branch", "--show-current"]
        ),
        "commit": _git_value(
            workflow, selected, "promotion immediate parent", ["git", "rev-parse", "HEAD"]
        ),
    }
    if any(values[key] != authority_doc[key] for key in values):
        raise ConfigError("promotion Git branch or parent drifted before commit")
    changed_paths = document.get("commit", {}).get("changed_paths", ["flake.lock"])
    sorted_paths = sorted(changed_paths)
    staged_paths = _git_value(
        workflow,
        selected,
        "promotion immediate staged paths",
        ["git", "diff", "--cached", "--name-only", "-z"],
    )
    expected_staged = "".join(f"{p}\x00" for p in sorted_paths) if staged else ""
    if staged_paths != expected_staged:
        raise ConfigError("promotion index authority drifted before commit")
    expected_status = "\n".join(f"M  {p}" for p in sorted_paths) if staged else "\n".join(f" M {p}" for p in sorted_paths)
    status_label = "promotion immediate worktree status"
    status_result = workflow.command(
        status_label,
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching"],
        selected,
    )
    raw_status = workflow.complete_stdout(status_label).rstrip("\n")
    relevant_status, ignored_digest, ignored_count = git_status_partition(raw_status)
    if (
        status_result.returncode
        or relevant_status != expected_status
        or ignored_digest != authority_doc["ignored_status_sha256"]
        or str(ignored_count) != authority_doc["ignored_status_count"]
    ):
        raise ConfigError("promotion worktree change set is not exactly expected changed paths")
    if (selected / "flake.lock").read_bytes() != candidate.candidate_bytes:
        raise ConfigError("promotion candidate lock bytes drifted before commit")
    if candidate.candidate_flake_nix_bytes is not None and (selected / "flake.nix").read_bytes() != candidate.candidate_flake_nix_bytes:
        raise ConfigError("promotion candidate declaration bytes drifted before commit")
    _revalidate_closure_authorization(
        document, candidate, str(document["canonical_repository_url"])
    )
    with _canonical_promotion_snapshot(
        workflow,
        selected,
        authority_doc,
        candidate.candidate_bytes,
        state_root,
        declaration_bytes=candidate.candidate_flake_nix_bytes,
    ) as candidate_tree:
        contract = _evaluate_contract(
            workflow, candidate_tree, allow_file_urls=document["local_fixture"]
        )
        is_comp = document.get("schema") == PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1
        expected_contract_canonical = (
            document["candidate_contract_canonical"]
            if is_comp
            else document["contract_canonical"]
        )
        expected_contract_digest = (
            document["candidate_contract_digest"]
            if is_comp
            else document["contract_digest"]
        )
        if (
            canonical_bytes(contract).decode("ascii") != expected_contract_canonical
            or sha256_bytes(canonical_bytes(contract)) != expected_contract_digest
        ):
            raise ConfigError("promotion contract identity drifted before commit")
        if _qualifier_identity(candidate_tree, contract) != document["qualifier"]:
            raise ConfigError("promotion qualifier identity drifted before commit")
        _verify_candidate_impact(
            workflow,
            candidate_tree,
            document,
            label_prefix="promotion immediate candidate host derivation",
        )
    if _promotion_tool_identity(workflow.core_dir) != document["tool"]:
        raise ConfigError("promotion tool identity drifted before commit")
    candidate_entry = _find_source_entry(
        workflow.sources,
        document["input"],
        str(document["canonical_repository_url"]),
        allow_file=document["local_fixture"],
        key_only=is_comp,
    )
    observe_source(
        workflow,
        candidate_entry,
        document["revision"],
        expected_repository=str(document["canonical_repository_url"]),
        expected_ref=document["local_ref"]["ref"],
    )
    _guard_processes([], True)


def _revalidate_apply(
    workflow: Workflow,
    artifact: PromotionArtifact,
    selected: Path,
    state_root: Path,
    *,
    is_local: bool = False,
) -> LockCandidate:
    document = artifact.document
    authority_doc = document.get("source_authority") or document.get("repository")
    if is_local:
        revalidate_composition_catalog(workflow, document["composition_catalog"], authority_doc["commit"])
        if document.get("schema") == PLAN_SCHEMA_V4:
            selected_node = _input_node(document["candidate_lock"]["normalized"], document["input"])
            revalidate_promotion_local_sources(
                workflow,
                document["candidate_lock"]["normalized"],
                document["local_sources"],
                preimage_lock=document["entry_lock"]["normalized"] if "entry_lock" in document else None,
                selected_node=selected_node,
            )
        else:
            revalidate_local_sources(workflow, document["candidate_lock"]["normalized"], document["local_sources"])
        if local_git_identity(workflow, selected) != authority_doc:
            raise ConfigError("promotion repository Git authority drifted")
    else:
        if _promotion_git_identity(workflow, selected) != authority_doc:
            raise ConfigError("promotion repository Git authority drifted")
    if _promotion_tool_identity(workflow.core_dir) != document["tool"]:
        raise ConfigError("promotion tool or external executable identity drifted")
    candidate = load_retained_lock_candidate(
        artifact.directory / "retained-lock",
        authority_doc,
        document["entry_lock"]["sha256"],
        document["candidate_lock"]["sha256"],
        expected_closure_authorization=document["closure_authorization"],
        expected_evidence_sha256=document["lock_authorization_evidence"]["sha256"],
    )
    if (
        candidate.preimage != document["entry_lock"]["normalized"]
        or candidate.candidate != document["candidate_lock"]["normalized"]
        or candidate.changes != document["lock_delta"]
        or candidate.closure_authorization != document["closure_authorization"]
    ):
        raise ConfigError("retained lock candidate authorization drifted after review")
    if document.get("schema") == PLAN_SCHEMA_CORE_REPOSITORY_TRANSITION_V1:
        if (
            candidate.preimage_flake_nix_bytes is None
            or candidate.candidate_flake_nix_bytes is None
            or sha256_bytes(candidate.preimage_flake_nix_bytes) != document["entry_declaration"]["sha256"]
            or sha256_bytes(candidate.candidate_flake_nix_bytes) != document["candidate_declaration"]["sha256"]
            or candidate.candidate_flake_nix_bytes != _migrate_core_declaration_bytes(candidate.preimage_flake_nix_bytes)
        ):
            raise ConfigError("retained declaration candidate bytes drifted after review")
    elif document.get("schema") == PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1:
        if (
            candidate.preimage_flake_nix_bytes is None
            or candidate.candidate_flake_nix_bytes is None
            or sha256_bytes(candidate.preimage_flake_nix_bytes) != document["entry_declaration"]["sha256"]
            or sha256_bytes(candidate.candidate_flake_nix_bytes) != document["candidate_declaration"]["sha256"]
            or candidate.candidate_flake_nix_bytes != _migrate_composition_declaration_bytes(candidate.preimage_flake_nix_bytes)
        ):
            raise ConfigError("retained declaration candidate bytes drifted after review")
    before_node = _input_node(candidate.preimage, document["input"])
    node = _input_node(candidate.candidate, document["input"])
    if before_node != node:
        raise ConfigError("retained lock candidate input resolution drifted after review")
    _revalidate_closure_authorization(
        document, candidate, str(document["canonical_repository_url"])
    )
    with _canonical_promotion_snapshot(
        workflow,
        selected,
        authority_doc,
        candidate.preimage_bytes,
        state_root,
        declaration_bytes=candidate.preimage_flake_nix_bytes,
    ) as baseline_tree:
        contract = _evaluate_contract(
            workflow, baseline_tree, allow_file_urls=document["local_fixture"]
        )
        if (
            contract != document["contract"]
            or canonical_bytes(contract).decode("ascii") != document["contract_canonical"]
            or sha256_bytes(canonical_bytes(contract)) != document["contract_digest"]
        ):
            raise ConfigError("promotion contract drifted after review")
        if _qualifier_identity(baseline_tree, contract) != document["qualifier"]:
            raise ConfigError("promotion qualifier identity drifted after review")
        baseline = _observe_host_derivations(
            workflow,
            baseline_tree,
            contract,
            label_prefix="promotion apply baseline host derivation",
        )
        if baseline["hosts"] != document["impact"]["baselineHosts"]:
            raise ConfigError("promotion baseline host derivations drifted after review")
    if is_local:
        candidate_entry = _find_source_entry(
            workflow.sources,
            document["input"],
            str(document["canonical_repository_url"]),
            allow_file=document["local_fixture"],
            key_only=document.get("schema") == PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1,
        )
        observe_source(
            workflow,
            candidate_entry,
            document["revision"],
            expected_repository=str(document["canonical_repository_url"]),
            expected_ref=document["local_ref"]["ref"],
        )
    else:
        _require_canonical_remote_ref(
            workflow,
            selected,
            document["canonical_transport_url"],
            document["remote_ref"]["ref"],
            document["revision"],
            "promotion apply candidate remote release ref",
        )
    _guard_processes([], True)
    marker = _consumption_path(artifact.digest)
    if marker.exists() or marker.is_symlink():
        raise ConfigError("promotion authority was already consumed")
    return candidate


def _require_canonical_remote_ref(
    workflow: Workflow,
    selected: Path,
    transport_url: str,
    release_ref: str,
    revision: str,
    label: str,
) -> None:
    result = workflow.command(
        label,
        ["git", "ls-remote", transport_url, release_ref],
        selected,
    )
    if result.returncode:
        raise ConfigError("unable to establish canonical promotion candidate remote release ref")
    lines = [line.split() for line in workflow.complete_stdout(label).splitlines() if line]
    if lines != [[revision, release_ref]]:
        raise ConfigError(
            "canonical promotion candidate remote release ref differs from the requested revision"
        )


def _consume_authority(digest: str, repository: Mapping[str, Any]) -> None:
    directory = ensure_private_root(_private_state_root() / "promotion-consumed", "promotion replay state")
    exclusive_private_write(
        directory / f"{digest}.json",
        canonical_bytes({"schema": "nix-update-promotion-consumed-v1", "plan_digest": digest, "repository": repository}),
    )
    fsync_directory(directory)


def _consumption_path(digest: str) -> Path:
    return _private_state_root() / "promotion-consumed" / f"{digest}.json"


def _stage_only_lock(
    workflow: Workflow,
    selected: Path,
    repository: Mapping[str, Any],
    *,
    changed_paths: Sequence[str] = ("flake.lock",),
) -> str:
    sorted_paths = sorted(changed_paths)
    result = workflow.command("promotion stage paths", ["git", "add", "--", *sorted_paths], selected)
    if result.returncode:
        raise ConfigError("unable to stage promotion changed paths")
    staged = workflow.command(
        "promotion staged paths", ["git", "diff", "--cached", "--name-only", "-z"], selected
    )
    expected_staged = "".join(f"{p}\x00" for p in sorted_paths)
    if staged.returncode or workflow.complete_stdout("promotion staged paths") != expected_staged:
        raise ConfigError("promotion index contains paths other than expected changed paths")
    status = workflow.command(
        "promotion worktree status", ["git", "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching"], selected
    )
    raw_status = workflow.complete_stdout("promotion worktree status").strip()
    relevant_status, ignored_digest, ignored_count = git_status_partition(raw_status)
    expected_status = "\n".join(f"M  {p}" for p in sorted_paths)
    if (
        status.returncode
        or relevant_status != expected_status
        or ignored_digest != repository["ignored_status_sha256"]
        or str(ignored_count) != repository["ignored_status_count"]
    ):
        raise ConfigError("promotion worktree contains changes outside the staged paths")
    return _git_value(workflow, selected, "promotion expected commit tree", ["git", "write-tree"])


def _revalidate_commit_authority(
    workflow: Workflow,
    selected: Path,
    document: Mapping[str, Any],
    candidate: LockCandidate,
    state_root: Path,
    *,
    staged: bool,
) -> None:
    repository = document["repository"]
    values = {
        "branch": _git_value(
            workflow, selected, "promotion immediate branch", ["git", "branch", "--show-current"]
        ),
        "commit": _git_value(
            workflow, selected, "promotion immediate parent", ["git", "rev-parse", "HEAD"]
        ),
    }
    if any(values[key] != repository[key] for key in values):
        raise ConfigError("promotion Git branch or parent drifted before commit")
    configured = configured_publication_identity(workflow, selected, values["branch"])
    if any(configured[key] != repository[key] for key in configured):
        raise ConfigError("promotion configured publication authority drifted before commit")
    if _remote_head(workflow, selected, document["publication"]) != repository["commit"]:
        raise ConfigError("promotion remote parent drifted before commit")
    changed_paths = document.get("commit", {}).get("changed_paths", ["flake.lock"])
    sorted_paths = sorted(changed_paths)
    staged_paths = _git_value(
        workflow,
        selected,
        "promotion immediate staged paths",
        ["git", "diff", "--cached", "--name-only", "-z"],
    )
    expected_staged = "".join(f"{p}\x00" for p in sorted_paths) if staged else ""
    if staged_paths != expected_staged:
        raise ConfigError("promotion index authority drifted before commit")
    expected_status = "\n".join(f"M  {p}" for p in sorted_paths) if staged else "\n".join(f" M {p}" for p in sorted_paths)
    status_label = "promotion immediate worktree status"
    status_result = workflow.command(
        status_label,
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching"],
        selected,
    )
    raw_status = workflow.complete_stdout(status_label).rstrip("\n")
    relevant_status, ignored_digest, ignored_count = git_status_partition(raw_status)
    if (
        status_result.returncode
        or relevant_status != expected_status
        or ignored_digest != repository["ignored_status_sha256"]
        or str(ignored_count) != repository["ignored_status_count"]
    ):
        raise ConfigError("promotion worktree change set is not exactly expected changed paths")
    if (selected / "flake.lock").read_bytes() != candidate.candidate_bytes:
        raise ConfigError("promotion candidate lock bytes drifted before commit")
    if candidate.candidate_flake_nix_bytes is not None and (selected / "flake.nix").read_bytes() != candidate.candidate_flake_nix_bytes:
        raise ConfigError("promotion candidate declaration bytes drifted before commit")
    _revalidate_closure_authorization(
        document, candidate, str(document["canonical_repository_url"])
    )
    with _canonical_promotion_snapshot(
        workflow,
        selected,
        document["repository"],
        candidate.candidate_bytes,
        state_root,
        declaration_bytes=candidate.candidate_flake_nix_bytes,
    ) as candidate_tree:
        contract = _evaluate_contract(
            workflow, candidate_tree, allow_file_urls=document["local_fixture"]
        )
        is_comp = document.get("schema") == PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1
        expected_contract_canonical = (
            document["candidate_contract_canonical"]
            if is_comp
            else document["contract_canonical"]
        )
        expected_contract_digest = (
            document["candidate_contract_digest"]
            if is_comp
            else document["contract_digest"]
        )
        if (
            canonical_bytes(contract).decode("ascii") != expected_contract_canonical
            or sha256_bytes(canonical_bytes(contract)) != expected_contract_digest
        ):
            raise ConfigError("promotion contract identity drifted before commit")
        if _qualifier_identity(candidate_tree, contract) != document["qualifier"]:
            raise ConfigError("promotion qualifier identity drifted before commit")
        _verify_candidate_impact(
            workflow,
            candidate_tree,
            document,
            label_prefix="promotion immediate candidate host derivation",
        )
    if _promotion_tool_identity(workflow.core_dir) != document["tool"]:
        raise ConfigError("promotion tool identity drifted before commit")
    _require_canonical_remote_ref(
        workflow,
        selected,
        document["canonical_transport_url"],
        document["remote_ref"]["ref"],
        document["revision"],
        "promotion immediate candidate remote release ref",
    )
    _guard_processes([], True)


def _verify_candidate_impact(
    workflow: Workflow,
    tree: Path,
    document: Mapping[str, Any],
    *,
    label_prefix: str,
) -> None:
    contract = document.get("candidate_contract", document["contract"])
    observation = _observe_host_derivations(
        workflow,
        tree,
        contract,
        label_prefix=label_prefix,
    )
    impact = compute_host_impact(
        document["impact"]["baselineHosts"],
        observation["hosts"],
        contract["hosts"],
    )
    if impact != document["impact"]:
        raise ConfigError("promotion candidate host impact drifted before commit")


def _unstage_lock(
    workflow: Workflow, selected: Path, *, changed_paths: Sequence[str] = ("flake.lock",)
) -> None:
    result = workflow.command(
        "promotion unstage recovery", ["git", "restore", "--staged", "--", *changed_paths], selected
    )
    if result.returncode:
        raise ConfigError("unable to restore the promotion index after failure")


def _commit_promotion(
    workflow: Workflow,
    selected: Path,
    document: Mapping[str, Any],
    expected_tree: str,
) -> str:
    authority_doc = document.get("source_authority") or document.get("repository")
    parent = authority_doc["commit"]
    policy = document["commit"]
    result = workflow.command(
        "promotion flake.lock commit",
        ["git", "commit", "-m", policy["subject"], "-m", policy["body"]],
        selected,
    )
    if result.returncode:
        current = _git_value(workflow, selected, "promotion failed-commit HEAD", ["git", "rev-parse", "HEAD"])
        if current != parent:
            raise PromotionCommitCreated(current, "promotion commit exists but commit command reported failure")
        raise ConfigError("unable to create the reviewed promotion commit")
    commit = _git_value(workflow, selected, "promotion commit identity", ["git", "rev-parse", "HEAD"])
    actual_parent = _git_value(workflow, selected, "promotion commit parent", ["git", "rev-parse", "HEAD^"])
    actual_tree = _git_value(workflow, selected, "promotion commit tree", ["git", "rev-parse", "HEAD^{tree}"])
    changed = _git_value(
        workflow, selected, "promotion commit changed paths",
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
    ).splitlines()
    expected_changed = sorted(policy.get("changed_paths", ["flake.lock"]))
    if actual_parent != parent or actual_tree != expected_tree or sorted(changed) != expected_changed:
        raise PromotionCommitCreated(commit, "promotion commit parent, tree, or changed path set is invalid")
    return commit


def _install_failure_restored(candidate: LockCandidate, selected: Path) -> bool:
    journal = candidate.output / "install-transaction.json"
    try:
        require_private_file(journal, "promotion lock installation journal")
        document = json.loads(journal.read_text())
        metadata = document["preimage_metadata"]
        if document.get("state") != "preimage-restored" or not isinstance(metadata, dict):
            return False
        _verify_installed_lock(selected / "flake.lock", candidate.preimage_bytes, metadata)
        if candidate.preimage_flake_nix_bytes is not None:
            decl = document.get("declaration")
            if not isinstance(decl, dict):
                return False
            decl_metadata = decl.get("preimage_metadata")
            if not isinstance(decl_metadata, dict):
                return False
            _verify_installed_lock(selected / "flake.nix", candidate.preimage_flake_nix_bytes, decl_metadata)
    except (ConfigError, OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        return False
    return True


def _remote_head(workflow: Workflow, selected: Path, publication: Mapping[str, Any]) -> str | None:
    label = f"promotion remote truth {len(workflow.records)}"
    result = workflow.command(
        label,
        ["git", "ls-remote", "--exit-code", publication["remote_name"], publication["remote_ref"]],
        selected,
    )
    if result.returncode:
        return None
    fields = workflow.complete_stdout(label).split()
    return fields[0] if len(fields) == 2 and fields[1] == publication["remote_ref"] else None


def _verify_published_repository(workflow: Workflow, selected: Path, commit: str) -> None:
    identity = _promotion_git_identity(workflow, selected)
    if identity["commit"] != commit or identity["remote_head"] != commit:
        raise ConfigError("published promotion repository did not reach clean 0/0 parity")


def _promotion_git_identity(workflow: Workflow, selected: Path) -> dict[str, Any]:
    local = local_git_identity(workflow, selected)
    publication = publication_git_identity(workflow, selected, local)
    return {**local, **{key: value for key, value in publication.items() if key not in {"upstream", "parity"}}}


def _finish_result(directory: Path, result: dict[str, Any], workflow: Workflow, status: int) -> int:
    if result["terminal_state"] not in TERMINAL_STATES:
        raise ConfigError("promotion terminal state is invalid")
    result["finished_at"] = _now()
    command_start = result.pop("_command_start", 0)
    result["commands"] = [asdict(record) for record in workflow.records[command_start:]]
    result["result_path"] = str(directory / "result.json")
    try:
        write_result(directory, result)
    except (ConfigError, OSError, ValueError, TypeError) as error:
        result["terminal_state"] = "PROMOTION_RESULT_EVIDENCE_FAILURE"
        try:
            fallback = ensure_private_root(
                _private_state_root() / "promotion-failure-results", "promotion failure result root"
            ) / f"{result['plan_digest']}-{os.getpid()}-{os.urandom(4).hex()}.json"
            exclusive_private_write(
                fallback,
                canonical_bytes({
                    "schema": "nix-update-promotion-result-fallback-v2" if "local_commit" in result else "nix-update-promotion-result-fallback-v1",
                    "plan_digest": result["plan_digest"],
                    "terminal_state": "PROMOTION_RESULT_EVIDENCE_FAILURE",
                    "published_commit": result.get("published_commit"),
                    **({
                        "local_commit": result["local_commit"],
                        "publication_state": result["publication_state"],
                        "activation_state": result["activation_state"],
                    } if "local_commit" in result else {}),
                    "failure": redact_text(str(error)),
                    "written_at": _now(),
                }),
            )
        except (ConfigError, OSError, ValueError):
            pass
        return 1
    message = f"nix-update: promotion result: {result['terminal_state']}"
    print(message, file=os.sys.stdout if status == 0 else os.sys.stderr, flush=True)
    return status


def _ready_plan_document(
    workflow: Workflow,
    output: Path,
    tool: Mapping[str, Any],
    authority: Mapping[str, Any],
    contract: Mapping[str, Any],
    qualifier: Mapping[str, Any],
    input_name: str,
    repository_url: str,
    revision: str,
    candidate_ref: str,
    candidate: LockCandidate,
    qualifier_binding: Mapping[str, Any],
    locked_qualification: Mapping[str, Any],
    baseline_host_observation: Mapping[str, Any],
    impact: Mapping[str, Any],
    *,
    input_resolution: Mapping[str, Any],
    transport_url: str,
    local_fixture: bool,
    is_local: bool = False,
    composition_catalog: Mapping[str, Any] | None = None,
    allow_core_repository_transition: bool = False,
    allow_composition_repository_transition: bool = False,
    preimage_input_resolution: Mapping[str, Any] | None = None,
    candidate_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    contract_bytes = canonical_bytes(contract)
    if allow_composition_repository_transition:
        if candidate_contract is None:
            raise ConfigError("composition repository transition requires candidate contract")
        cand_contract_bytes = canonical_bytes(candidate_contract)
        return {
            "schema": PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1,
            "status": "READY_FOR_REVIEW",
            "blockers": [],
            "prepared_at": _now(),
            "tool": dict(tool),
            "source_authority": dict(authority),
            "composition_catalog": dict(composition_catalog or {}),
            "contract": dict(contract),
            "contract_canonical": contract_bytes.decode("ascii"),
            "contract_digest": sha256_bytes(contract_bytes),
            "contract_size": len(contract_bytes),
            "candidate_contract": dict(candidate_contract),
            "candidate_contract_canonical": cand_contract_bytes.decode("ascii"),
            "candidate_contract_digest": sha256_bytes(cand_contract_bytes),
            "candidate_contract_size": len(cand_contract_bytes),
            "qualifier": dict(qualifier),
            "input": input_name,
            "input_resolution": dict(input_resolution),
            "preimage_input_resolution": dict(preimage_input_resolution or {}),
            "canonical_repository_url": repository_url,
            "canonical_transport_url": transport_url,
            "revision": revision,
            "candidate_ref": candidate_ref,
            "local_fixture": local_fixture,
            "entry_lock": _lock_binding("retained-lock/flake.lock.preimage", candidate.preimage_bytes, candidate.preimage),
            "candidate_lock": _lock_binding("retained-lock/flake.lock.candidate", candidate.candidate_bytes, candidate.candidate),
            "entry_declaration": _file_binding("retained-lock/flake.nix.preimage", output / "retained-lock/flake.nix.preimage"),
            "candidate_declaration": _file_binding("retained-lock/flake.nix.candidate", output / "retained-lock/flake.nix.candidate"),
            "lock_delta": candidate.changes,
            "closure_authorization": dict(candidate.closure_authorization or {}),
            "lock_authorization_evidence": _file_binding(
                "retained-lock/delta.json", output / "retained-lock/delta.json"
            ),
            "qualifier_result": dict(qualifier_binding),
            "locked_qualification": dict(locked_qualification),
            "commit": {
                "subject": "nix-update: promote one composition input",
                "body": f"Promote {input_name} to exact revision {revision} via a reviewed nix-update transaction.",
                "changed_paths": ["flake.nix", "flake.lock"],
            },
            "local_ref": {
                "ref": binding_release_ref(contract, input_name),
                "revision": revision,
            },
            "baseline_host_observation": dict(baseline_host_observation),
            "impact": dict(impact),
            "impact_digest": sha256_bytes(canonical_bytes(impact)),
            "confirmation": "APPLY NIX-UPDATE PROMOTION {PLAN_DIGEST}",
            "no_activation": True,
            "replay": {"mode": "durable-single-consumption", "state": "unconsumed"},
            "plan_digest": "",
        }
    if allow_core_repository_transition:
        return {
            "schema": PLAN_SCHEMA_CORE_REPOSITORY_TRANSITION_V1,
            "status": "READY_FOR_REVIEW",
            "blockers": [],
            "prepared_at": _now(),
            "tool": dict(tool),
            "source_authority": dict(authority),
            "composition_catalog": dict(composition_catalog or {}),
            "contract": dict(contract),
            "contract_canonical": contract_bytes.decode("ascii"),
            "contract_digest": sha256_bytes(contract_bytes),
            "contract_size": len(contract_bytes),
            "qualifier": dict(qualifier),
            "input": input_name,
            "input_resolution": dict(input_resolution),
            "preimage_input_resolution": dict(preimage_input_resolution or {}),
            "canonical_repository_url": repository_url,
            "canonical_transport_url": transport_url,
            "revision": revision,
            "candidate_ref": candidate_ref,
            "local_fixture": local_fixture,
            "entry_lock": _lock_binding("retained-lock/flake.lock.preimage", candidate.preimage_bytes, candidate.preimage),
            "candidate_lock": _lock_binding("retained-lock/flake.lock.candidate", candidate.candidate_bytes, candidate.candidate),
            "entry_declaration": _file_binding("retained-lock/flake.nix.preimage", output / "retained-lock/flake.nix.preimage"),
            "candidate_declaration": _file_binding("retained-lock/flake.nix.candidate", output / "retained-lock/flake.nix.candidate"),
            "lock_delta": candidate.changes,
            "closure_authorization": dict(candidate.closure_authorization or {}),
            "lock_authorization_evidence": _file_binding(
                "retained-lock/delta.json", output / "retained-lock/delta.json"
            ),
            "qualifier_result": dict(qualifier_binding),
            "locked_qualification": dict(locked_qualification),
            "commit": {
                "subject": "nix-update: promote one composition input",
                "body": f"Promote {input_name} to exact revision {revision} via a reviewed nix-update transaction.",
                "changed_paths": ["flake.nix", "flake.lock"],
            },
            "local_ref": {
                "ref": binding_release_ref(contract, input_name),
                "revision": revision,
            },
            "baseline_host_observation": dict(baseline_host_observation),
            "impact": dict(impact),
            "impact_digest": sha256_bytes(canonical_bytes(impact)),
            "confirmation": "APPLY NIX-UPDATE PROMOTION {PLAN_DIGEST}",
            "no_activation": True,
            "replay": {"mode": "durable-single-consumption", "state": "unconsumed"},
            "plan_digest": "",
        }
    if is_local:
        return {
            "schema": PLAN_SCHEMA_V4,
            "status": "READY_FOR_REVIEW",
            "blockers": [],
            "prepared_at": _now(),
            "tool": dict(tool),
            "source_authority": dict(authority),
            "composition_catalog": dict(composition_catalog or {}),
            "contract": dict(contract),
            "contract_canonical": contract_bytes.decode("ascii"),
            "contract_digest": sha256_bytes(contract_bytes),
            "contract_size": len(contract_bytes),
            "qualifier": dict(qualifier),
            "input": input_name,
            "input_resolution": dict(input_resolution),
            "canonical_repository_url": repository_url,
            "canonical_transport_url": transport_url,
            "revision": revision,
            "candidate_ref": candidate_ref,
            "local_fixture": local_fixture,
            "entry_lock": _lock_binding("retained-lock/flake.lock.preimage", candidate.preimage_bytes, candidate.preimage),
            "candidate_lock": _lock_binding("retained-lock/flake.lock.candidate", candidate.candidate_bytes, candidate.candidate),
            "lock_delta": candidate.changes,
            "closure_authorization": dict(candidate.closure_authorization or {}),
            "lock_authorization_evidence": _file_binding(
                "retained-lock/delta.json", output / "retained-lock/delta.json"
            ),
            "qualifier_result": dict(qualifier_binding),
            "locked_qualification": dict(locked_qualification),
            "commit": {
                "subject": "nix-update: promote one composition input",
                "body": f"Promote {input_name} to exact revision {revision} via a reviewed nix-update transaction.",
                "changed_paths": ["flake.lock"],
            },
            "local_ref": {
                "ref": binding_release_ref(contract, input_name),
                "revision": revision,
            },
            "baseline_host_observation": dict(baseline_host_observation),
            "impact": dict(impact),
            "impact_digest": sha256_bytes(canonical_bytes(impact)),
            "confirmation": "APPLY NIX-UPDATE PROMOTION {PLAN_DIGEST}",
            "no_activation": True,
            "replay": {"mode": "durable-single-consumption", "state": "unconsumed"},
            "plan_digest": "",
        }
    return {
        "schema": PLAN_SCHEMA,
        "status": "READY_FOR_REVIEW",
        "blockers": [],
        "prepared_at": _now(),
        "tool": dict(tool),
        "repository": dict(authority),
        "contract": dict(contract),
        "contract_canonical": contract_bytes.decode("ascii"),
        "contract_digest": sha256_bytes(contract_bytes),
        "contract_size": len(contract_bytes),
        "qualifier": dict(qualifier),
        "input": input_name,
        "input_resolution": dict(input_resolution),
        "canonical_repository_url": repository_url,
        "canonical_transport_url": transport_url,
        "revision": revision,
        "candidate_ref": candidate_ref,
        "local_fixture": local_fixture,
        "entry_lock": _lock_binding("retained-lock/flake.lock.preimage", candidate.preimage_bytes, candidate.preimage),
        "candidate_lock": _lock_binding("retained-lock/flake.lock.candidate", candidate.candidate_bytes, candidate.candidate),
        "lock_delta": candidate.changes,
        "closure_authorization": dict(candidate.closure_authorization or {}),
        "lock_authorization_evidence": _file_binding(
            "retained-lock/delta.json", output / "retained-lock/delta.json"
        ),
        "qualifier_result": dict(qualifier_binding),
        "locked_qualification": dict(locked_qualification),
        "remote_ref": {
            "ref": binding_release_ref(contract, input_name),
            "revision": revision,
        },
        "baseline_host_observation": dict(baseline_host_observation),
        "impact": dict(impact),
        "impact_digest": sha256_bytes(canonical_bytes(impact)),
        "commit": {
            "subject": "nix-update: promote one composition input",
            "body": f"Promote {input_name} to exact revision {revision} via a reviewed nix-update transaction.",
            "changed_paths": ["flake.lock"],
        },
        "publication": {
            "remote_name": authority["remote_name"],
            "remote_ref": authority["remote_ref"],
            "mode": "ordinary-fast-forward",
        },
        "confirmation": "APPLY NIX-UPDATE PROMOTION {PLAN_DIGEST}",
        "no_activation": True,
        "replay": {"mode": "durable-single-consumption", "state": "unconsumed"},
        "plan_digest": "",
    }


def _already_selected_plan_document(
    context: Mapping[str, Any],
    qualifier_binding: Mapping[str, Any],
    locked_qualification: Mapping[str, Any],
    selected_lock: Mapping[str, Any],
    impact: Mapping[str, Any],
) -> dict[str, Any]:
    contract_bytes = context["contract_bytes"]
    is_local = bool(context.get("is_local"))
    if is_local:
        return {
            "schema": PLAN_SCHEMA_V4,
            "status": "ALREADY_SELECTED",
            "blockers": [],
            "prepared_at": _now(),
            "tool": dict(context["tool"]),
            "source_authority": dict(context["authority"]),
            "composition_catalog": dict(context["composition_catalog"]),
            "contract": dict(context["contract"]),
            "contract_canonical": contract_bytes.decode("ascii"),
            "contract_digest": sha256_bytes(contract_bytes),
            "contract_size": len(contract_bytes),
            "qualifier": dict(context["qualifier"]),
            "input": context["input"],
            "input_resolution": dict(context["input_resolution"]),
            "canonical_repository_url": context["repository_url"],
            "canonical_transport_url": context["transport_url"],
            "revision": context["revision"],
            "candidate_ref": context["candidate_ref"],
            "local_fixture": context["local_fixture"],
            "selected_lock": dict(selected_lock),
            "qualifier_result": dict(qualifier_binding),
            "locked_qualification": dict(locked_qualification),
            "local_ref": {
                "ref": context["release_ref"],
                "revision": context["revision"],
            },
            "baseline_host_observation": dict(context["baseline_host_observation"]),
            "impact": dict(impact),
            "impact_digest": sha256_bytes(canonical_bytes(impact)),
            "non_mutation": {
                "git_authority_unchanged": True,
                "lock_bytes_unchanged": True,
                "local_ref_unchanged": True,
            },
            "no_activation": True,
            "replay": {"mode": "inapplicable", "state": "not-authority"},
            "plan_digest": "",
        }
    return {
        "schema": PLAN_SCHEMA,
        "status": "ALREADY_SELECTED",
        "blockers": [],
        "prepared_at": _now(),
        "tool": dict(context["tool"]),
        "repository": dict(context["authority"]),
        "contract": dict(context["contract"]),
        "contract_canonical": contract_bytes.decode("ascii"),
        "contract_digest": sha256_bytes(contract_bytes),
        "contract_size": len(contract_bytes),
        "qualifier": dict(context["qualifier"]),
        "input": context["input"],
        "input_resolution": dict(context["input_resolution"]),
        "canonical_repository_url": context["repository_url"],
        "canonical_transport_url": context["transport_url"],
        "revision": context["revision"],
        "candidate_ref": context["candidate_ref"],
        "local_fixture": context["local_fixture"],
        "selected_lock": dict(selected_lock),
        "qualifier_result": dict(qualifier_binding),
        "locked_qualification": dict(locked_qualification),
        "remote_ref": {
            "ref": context["release_ref"],
            "revision": context["revision"],
        },
        "baseline_host_observation": dict(context["baseline_host_observation"]),
        "impact": dict(impact),
        "impact_digest": sha256_bytes(canonical_bytes(impact)),
        "non_mutation": {
            "git_authority_unchanged": True,
            "lock_bytes_unchanged": True,
            "remote_ref_unchanged": True,
        },
        "no_activation": True,
        "replay": {"mode": "inapplicable", "state": "not-authority"},
        "plan_digest": "",
    }


def _write_inapplicable(
    output: Path, status: str, blockers: Sequence[str], *, schema: str = PLAN_SCHEMA
) -> PromotionArtifact:
    document = {
        "schema": schema,
        "status": status,
        "blockers": list(blockers),
        "prepared_at": _now(),
        "no_activation": True,
        "replay": {"mode": "inapplicable", "state": "not-authority"},
        "plan_digest": "",
    }
    return write_plan(output, document, _plan_markdown(document))


def _prepare_output(output: Path, selected: Path, repository_root: Path) -> None:
    if not output.is_absolute():
        raise ConfigError("promotion plan output must be an absolute path")
    resolved = output.resolve(strict=False)
    for root in (selected.resolve(strict=True), repository_root.resolve(strict=True)):
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        raise ConfigError("promotion plan output must be outside every reviewed repository")
    if output.exists() or output.is_symlink():
        raise ConfigError("promotion plan output must be a new directory")
    validate_ancestor_chain(output.parent, owner_only=True)


def _read_lock(path: Path) -> dict[str, Any]:
    from .deployment_plan import normalized_lock

    raw = path.read_bytes()
    normalized = normalized_lock(path)
    if path.read_bytes() != raw:
        raise ConfigError("flake.lock changed while its identity was captured")
    return {"bytes": raw, "normalized": normalized}


def _selected_flake_path(authority: Mapping[str, Any]) -> Path:
    repository_root = Path(str(authority.get("repository_root", "")))
    try:
        flake_identity = json.loads(str(authority.get("flake_nix", "")))
        flake_path = Path(flake_identity["path"])
        selected = flake_path.parent
        selected.resolve(strict=True).relative_to(repository_root.resolve(strict=True))
    except (json.JSONDecodeError, KeyError, OSError, TypeError, ValueError) as error:
        raise ConfigError("promotion selected flake path identity is invalid") from error
    if flake_path.name != "flake.nix" or not flake_path.is_absolute():
        raise ConfigError("promotion selected flake path identity is invalid")
    return selected


def _canonical_snapshot_identity(
    selected: Path, authority: Mapping[str, Any], lock_bytes: bytes
) -> dict[str, Any]:
    bound_selected = _selected_flake_path(authority)
    try:
        selected_resolved = selected.resolve(strict=True)
        repository_root = Path(str(authority["repository_root"])).resolve(strict=True)
        relative = selected_resolved.relative_to(repository_root)
    except (KeyError, OSError, ValueError) as error:
        raise ConfigError("promotion snapshot flake path is outside its reviewed repository") from error
    if selected_resolved != bound_selected.resolve(strict=True):
        raise ConfigError("promotion snapshot flake path differs from reviewed Git authority")
    commit = authority.get("commit")
    tree = authority.get("tree")
    if not isinstance(commit, str) or not isinstance(tree, str):
        raise ConfigError("promotion snapshot Git identity is invalid")
    return {
        "repository_commit": commit,
        "repository_tree": tree,
        "selected_flake_path": relative.as_posix() or ".",
        "lock_sha256": sha256_bytes(lock_bytes),
        "lock_size": len(lock_bytes),
        "materialization_policy": CANONICAL_SNAPSHOT_POLICY,
    }


def _require_canonical_snapshot_tree(tree: Path) -> None:
    try:
        paths = [tree, *tree.rglob("*")]
        for path in paths:
            metadata = path.lstat()
            relative = path.relative_to(tree)
            if path != tree and ".git" in relative.parts:
                raise ConfigError("canonical promotion snapshot contains Git metadata")
            if path.is_symlink() or metadata.st_uid != os.getuid():
                raise ConfigError("canonical promotion snapshot contains unsafe metadata")
            if stat.S_ISDIR(metadata.st_mode):
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ConfigError("canonical promotion snapshot contains an unsafe file")
    except OSError as error:
        raise ConfigError("unable to validate canonical promotion snapshot") from error


@contextlib.contextmanager
def _canonical_promotion_snapshot(
    workflow: Workflow,
    selected: Path,
    authority: Mapping[str, Any],
    lock_bytes: bytes,
    state_root: Path,
    *,
    declaration_bytes: bytes | None = None,
) -> Iterator[Path]:
    identity = _canonical_snapshot_identity(selected, authority, lock_bytes)
    repository_root = Path(str(authority["repository_root"]))
    label = "promotion canonical snapshot Git tree"
    result = workflow.command(
        label,
        ["git", "rev-parse", f'{identity["repository_commit"]}^{{tree}}'],
        repository_root,
    )
    if (
        result.returncode
        or workflow.complete_stdout(label).strip() != identity["repository_tree"]
    ):
        raise ConfigError("promotion canonical snapshot tree identity drifted")
    temporary_root = Path(
        tempfile.mkdtemp(prefix="nix-update-promotion-", dir=state_root)
    )
    try:
        metadata = temporary_root.lstat()
        if (
            not temporary_root.is_dir()
            or temporary_root.is_symlink()
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise ConfigError("promotion snapshot root is not current-user owner-private")
        snapshot_tree = temporary_root / "snapshot"
        _materialize_git_tree(workflow, selected, authority, snapshot_tree)
        if declaration_bytes is not None:
            flake_nix = snapshot_tree / "flake.nix"
            nix_metadata = flake_nix.stat()
            flake_nix.write_bytes(declaration_bytes)
            os.chmod(flake_nix, stat.S_IMODE(nix_metadata.st_mode))
            if flake_nix.read_bytes() != declaration_bytes:
                raise ConfigError("canonical promotion snapshot declaration bytes drifted")
        _install_tree_candidate(snapshot_tree, lock_bytes)
        _require_canonical_snapshot_tree(snapshot_tree)
        if (snapshot_tree / "flake.lock").read_bytes() != lock_bytes:
            raise ConfigError("canonical promotion snapshot lock bytes drifted")
        yield snapshot_tree
    finally:
        shutil.rmtree(temporary_root)


def _input_node(lock: Mapping[str, Any], input_name: str) -> str:
    nodes = lock["nodes"]
    root = lock["root"]
    return _resolve_lock_input(nodes, root, root, input_name.split("/"), set())


def _locked_revision(lock: Mapping[str, Any], node: str) -> str | None:
    value = lock["nodes"].get(node)
    locked = value.get("locked") if isinstance(value, dict) else None
    revision = locked.get("rev") if isinstance(locked, dict) else None
    return revision if isinstance(revision, str) else None


def _install_tree_candidate(tree: Path, content: bytes) -> None:
    _require_isolated_flake_files(tree)
    lock = tree / "flake.lock"
    metadata = lock.stat()
    lock.write_bytes(content)
    os.chmod(lock, stat.S_IMODE(metadata.st_mode))
    if lock.read_bytes() != content:
        raise ConfigError("reviewed candidate tree did not retain exact lock bytes")


def _lock_binding(path: str, content: bytes, normalized: Mapping[str, Any]) -> dict[str, Any]:
    return {"path": path, "sha256": sha256_bytes(content), "size": len(content), "normalized": dict(normalized)}


def _file_binding(path: str, source: Path) -> dict[str, Any]:
    content = source.read_bytes()
    return {"path": path, "sha256": sha256_bytes(content), "size": len(content)}


def _revalidate_closure_authorization(
    target: Mapping[str, Any], candidate: LockCandidate, repository_url: str
) -> None:
    node = _input_node(candidate.candidate, str(target["input"]))
    before_node = _input_node(candidate.preimage, str(target["input"]))
    if before_node != node:
        raise ConfigError("promotion selected root node name changed")
    is_core_repo_transition = (
        bool(target.get("allow_core_repository_transition"))
        or target.get("schema") == PLAN_SCHEMA_CORE_REPOSITORY_TRANSITION_V1
    )
    is_comp_repo_transition = (
        bool(target.get("allow_composition_repository_transition"))
        or target.get("schema") == PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1
    )
    if is_core_repo_transition:
        expected_auth_url = CORE_REPOSITORY_TRANSITION_PREIMAGE_URL
    elif is_comp_repo_transition:
        expected_auth_url = COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_URL
    else:
        expected_auth_url = repository_url
    authorization = authorize_transition(
        candidate.preimage,
        candidate.candidate,
        node,
        expected_auth_url,
        str(target["revision"]),
        input_name=str(target["input"]),
        allow_file=bool(target["local_fixture"]),
        expected_ref=(
            str(target["release_ref"])
            if "release_ref" in target
            else str(target.get("local_ref", {}).get("ref") or target.get("remote_ref", {}).get("ref"))
        ),
        allow_implicit_main=target["contract"]["version"] == 1,
        allow_repository_transition=is_core_repo_transition,
        allow_composition_repository_transition=is_comp_repo_transition,
    )
    if candidate.closure_authorization != authorization:
        raise ConfigError("promotion owned-closure authorization drifted")


def _git_value(workflow: Workflow, selected: Path, label: str, command: Sequence[str]) -> str:
    result = workflow.command(label, command, selected)
    if result.returncode:
        raise ConfigError(f"unable to obtain {label}")
    return workflow.complete_stdout(label).strip()


def _plan_markdown(document: Mapping[str, Any]) -> str:
    lines = [
        "# nix-update promotion plan",
        "",
        f"Status: {document['status']}",
        "",
        "This artifact never authorizes host activation.",
        "",
        "Plan digest: `{PLAN_DIGEST}`",
        "",
    ]
    if document.get("schema") == PLAN_SCHEMA_V4 and document.get("local_sources"):
        from .promotion_locked_source import render_promotion_local_sources

        lines.extend(render_promotion_local_sources(document["local_sources"]))
    if document.get("input") == "core":
        lines.extend([
            "Qualification protocol: native locked core checks and declared hosts.",
            "The `qualifier` field binds the owner's declared entry point for identity",
            "drift detection; this protocol does not execute that entry point.",
            "",
        ])
    if document["status"] == "READY_FOR_REVIEW":
        lines.extend([
            "Apply confirmation: `APPLY NIX-UPDATE PROMOTION {PLAN_DIGEST}`",
            "",
        ])
    else:
        lines.extend([
            "This disposition is structurally inapplicable to `promote apply`.",
            "",
        ])
    return "\n".join(lines)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()
