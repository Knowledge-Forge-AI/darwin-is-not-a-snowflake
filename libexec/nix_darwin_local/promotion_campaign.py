"""Attended, recoverable composition promotion coordination for nix-update.

The coordinator owns stage handoff and durable state. Native DINAS promotion
code still owns every repository mutation, confirmation boundary, lock write,
qualification, commit, publication, and retained evidence artifact.
"""

from __future__ import annotations

import errno
import importlib
import io
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import ControlConfig
from .deployment_workflow import Workflow, _private_state_root
from .paths import ConfigError
from .promotion_qualification import promotion_tool_identity as _promotion_tool_identity
from .promotion_campaign_result import (
    LOCAL_COMMIT_CREATED_STATES,
    LOCAL_SUCCESS_STATES,
    REPREPARE_STATES,
    RETRYABLE_PUBLICATION_STATES,
    SUCCESS_STATES,
    failure_from_records,
    load_native_result,
)
from .promotion_campaign_state import (
    JOURNAL_NAME,
    allocate_campaign,
    append_attempt,
    load_campaign,
    matching_campaigns,
    new_campaign,
    persist_campaign,
    render_status,
    transition,
    update_attempt,
    update_index,
)
from .promotion_plan import load_plan
from .promotion_workflow import (
    _consumption_path,
    apply_promotion as native_apply_promotion,
    prepare_promotion as native_prepare_promotion,
)


MAX_STAGE_COUNT = 32


class TerminalUnavailableError(ConfigError):
    """Raised when an interactive controlling terminal is unavailable."""


TERMINAL_UNAVAILABLE_ERRNOS = frozenset(
    {
        errno.ENXIO,
        errno.ENODEV,
        errno.ENOTTY,
        errno.ENOENT,
        errno.EACCES,
        errno.EPERM,
    }
)


def _is_terminal_unavailable(error: BaseException) -> bool:
    if isinstance(error, TerminalUnavailableError):
        return True
    cause = getattr(error, "__cause__", None)
    if isinstance(cause, (io.UnsupportedOperation, UnicodeError)):
        return False
    if isinstance(cause, OSError) and cause.errno in TERMINAL_UNAVAILABLE_ERRNOS:
        return True
    if isinstance(error, OSError) and error.errno in TERMINAL_UNAVAILABLE_ERRNOS:
        return True
    if (
        isinstance(error, ConfigError)
        and str(error).strip() == "promotion confirmation requires a controlling terminal"
        and (cause is None or (isinstance(cause, OSError) and cause.errno in TERMINAL_UNAVAILABLE_ERRNOS))
    ):
        return True
    return False


@dataclass(frozen=True)
class CampaignStage:
    """A topology binding consumed by the coordinator.

    Topology discovery may return this type or a mapping with equivalent
    ``name``, ``repository``, ``input``, ``revision``, and ``kind`` fields.
    The coordinator deliberately stores only these serializable bindings in
    its journal.
    """

    name: str
    repository: Path
    input: str
    revision: str | None = None
    kind: str = "composition"


def run_campaign(
    workflow: Workflow,
    selected: Path,
    config: ControlConfig,
    input_name: str,
    revision: str,
    output: Path | None = None,
    dry_run: bool = False,
) -> int:
    """Create or continue a campaign and advance native stages as far as possible."""

    _bind_workflow_sources(workflow, config)
    selected = _absolute_path(selected, "selected flake")
    _validate_identity(input_name, "campaign input")
    _validate_identity(revision, "campaign revision")
    state_root = _private_state_root()
    campaign: Path | None = None
    if output is None:
        existing = _existing_matches(state_root, selected, input_name, revision, dry_run=dry_run)
        if len(existing) > 1:
            _print_choices(existing)
            return 1
        if existing:
            campaign = existing[0]
            try:
                document = load_campaign(campaign)
            except ConfigError:
                # An index entry is not authority. Keep it visible and force
                # an explicit operator choice instead of selecting another
                # campaign by scanning its outbox.
                _print_choices(existing)
                return 1
            if (
                document["selected"] != os.fspath(selected)
                or document["input"] != input_name
                or document["revision"] != revision
            ):
                _print_choices(existing)
                return 1
    if campaign is None:
        stages = resolve_campaign_stages(selected, config, input_name, revision, workflow=workflow)
        campaign = allocate_campaign(state_root, output)
        document = new_campaign(
            selected,
            input_name,
            revision,
            [_stage_mapping(stage) for stage in stages],
            dry_run=dry_run,
        )
        document = persist_campaign(campaign, document)
        _index(document, campaign, state_root)
    elif bool(document.get("dry_run")) != bool(dry_run):
        raise ConfigError("existing campaign dry-run mode does not match the request")
    elif document["state"] in {
        "COMPLETE_NO_ACTIVATION",
        "AWAITING_ACTIVATION",
        "AWAITING_DEPLOYMENT_RECONCILIATION",
        "LOCAL_COMPLETE_AWAITING_ACTIVATION",
        "DRY_RUN",
    }:
        print(render_status(document, campaign), end="", flush=True)
        return 0
    return _advance(workflow, selected, config, campaign, document)


def resume_campaign(
    workflow: Workflow,
    selected: Path,
    config: ControlConfig,
    campaign: Path | None = None,
) -> int:
    """Resume the indexed or explicitly named campaign from its next boundary."""

    _bind_workflow_sources(workflow, config)
    state_root = _private_state_root()
    selected = _absolute_path(selected, "selected flake")
    if campaign is None:
        normal_matches = _existing_matches(state_root, selected, None, None, dry_run=False)
        if len(normal_matches) == 1:
            campaign = normal_matches[0]
        elif len(normal_matches) > 1:
            _print_choices(normal_matches)
            return 1
        else:
            all_matches = _existing_matches(state_root, selected, None, None)
            if len(all_matches) == 1:
                campaign = all_matches[0]
            elif all_matches:
                _print_choices(all_matches)
                return 1
            else:
                print("nix-update: no matching promotion campaign is indexed", file=sys.stderr, flush=True)
                return 1
    else:
        campaign = _absolute_path(campaign, "campaign directory")
    document = load_campaign(campaign)
    if document["selected"] != os.fspath(selected):
        raise ConfigError("campaign selected flake does not match the requested topology")
    if document["dry_run"]:
        print("nix-update: dry-run campaign contains evidence only; start a normal campaign to publish", flush=True)
        return 0
    if document["state"] in {
        "COMPLETE_NO_ACTIVATION",
        "AWAITING_ACTIVATION",
        "AWAITING_DEPLOYMENT_RECONCILIATION",
        "LOCAL_COMPLETE_AWAITING_ACTIVATION",
    }:
        print(render_status(document, campaign), end="", flush=True)
        return 0
    return _advance(workflow, selected, config, campaign, document)


def status_campaign(selected: Path | None, campaign: Path | None = None) -> int:
    """Render durable state without consulting a source checkout or a model."""

    if campaign is not None:
        campaign = _absolute_path(campaign, "campaign directory")
        try:
            document = load_campaign(campaign)
        except ConfigError as error:
            if _render_historical(campaign):
                return 0
            print(f"nix-update: unable to read campaign: {error}", file=sys.stderr, flush=True)
            return 1
        if selected is not None and document["selected"] != os.fspath(_absolute_path(selected, "selected flake")):
            print("nix-update: campaign selected flake does not match the requested topology", file=sys.stderr, flush=True)
            return 1
        print(render_status(document, campaign), end="", flush=True)
        return 0
    if selected is None:
        print("nix-update: status requires --campaign or a selected flake", file=sys.stderr, flush=True)
        return 1
    selected = _absolute_path(selected, "selected flake")
    normal_matches = _existing_matches(_private_state_root(), selected, None, None, dry_run=False)
    if len(normal_matches) == 1:
        matches = normal_matches
    elif len(normal_matches) > 1:
        matches = normal_matches
    else:
        matches = _existing_matches(_private_state_root(), selected, None, None)
    if len(matches) != 1:
        if matches:
            _print_choices(matches)
        else:
            print("nix-update: no matching promotion campaign is indexed", file=sys.stderr, flush=True)
        return 1
    try:
        document = load_campaign(matches[0])
    except ConfigError as error:
        print(f"nix-update: unable to read indexed campaign: {error}", file=sys.stderr, flush=True)
        return 1
    print(render_status(document, matches[0]), end="", flush=True)
    return 0


def resolve_campaign_stages(
    selected: Path,
    config: ControlConfig,
    input_name: str,
    revision: str,
    *,
    workflow: Workflow | None = None,
    core_dir: Path | None = None,
) -> list[CampaignStage | Mapping[str, Any]]:
    """Resolve the active topology through the DINAS topology seam.

    The topology module is optional at import time. This keeps public-only
    installations independent of private composition code while allowing the
    topology implementation to provide layered and bootstrap routes.
    """

    candidates: list[Callable[..., Any]] = []
    for name in (
        "campaign_stages",
        "promotion_stages",
        "resolve_promotion_chain",
        "resolve_campaign_stages",
    ):
        value = getattr(config, name, None)
        if callable(value):
            candidates.append(value)
    try:
        topology = importlib.import_module(".promotion_topology", __package__)
    except ModuleNotFoundError as error:
        if error.name not in {f"{__package__}.promotion_topology", "promotion_topology"}:
            raise
    else:
        for name in (
            "resolve_campaign_stages",
            "resolve_promotion_chain",
            "campaign_stages",
            "promotion_stages",
        ):
            value = getattr(topology, name, None)
            if callable(value):
                candidates.append(value)
                break
    for resolver in candidates:
        try:
            result = resolver(selected, config, input_name, revision, workflow=workflow, core_dir=core_dir)
        except TypeError:
            result = resolver(selected, config, input_name, revision)
        if result is None:
            continue
        if hasattr(result, "stages"):
            result = result.stages
        stages = list(result)
        if not stages:
            raise ConfigError("promotion topology resolver returned no stages")
        if len(stages) > MAX_STAGE_COUNT:
            raise ConfigError("promotion topology contains too many stages")
        return [_coerce_stage(stage, input_name, revision, index) for index, stage in enumerate(stages)]
    return [
        CampaignStage(
            name="selected",
            kind="composition",
            repository=selected,
            input=input_name,
            revision=revision,
        )
    ]


def _stage_plan_tool_is_stale(workflow: Workflow, stage: Mapping[str, Any]) -> bool:
    plan_path = stage.get("current_plan")
    if not plan_path and stage.get("attempts"):
        plan_path = stage["attempts"][-1].get("plan_path")
    if not plan_path:
        return False
    plan_path = Path(plan_path)
    if not plan_path.exists():
        return False
    if stage.get("published_commit") is not None or stage.get("local_commit") is not None:
        return False
    if (plan_path / "result.json").exists():
        return False
    attempts = stage.get("attempts")
    if isinstance(attempts, list):
        for attempt in attempts:
            if (
                attempt.get("published_commit")
                or attempt.get("local_commit")
                or attempt.get("result_path")
                or attempt.get("result_digest")
            ):
                return False
            if attempt.get("terminal_state") in RETRYABLE_PUBLICATION_STATES or attempt.get("terminal_state") in SUCCESS_STATES:
                return False
    try:
        artifact = load_plan(plan_path)
    except (ConfigError, OSError, ValueError, KeyError, TypeError):
        return False
    if artifact.document.get("status") != "READY_FOR_REVIEW":
        return False
    marker = _consumption_path(artifact.digest)
    if marker.exists() or marker.is_symlink():
        return False
    plan_tool = artifact.document.get("tool")
    if not isinstance(plan_tool, Mapping):
        return False
    try:
        current_tool = _promotion_tool_identity(workflow.core_dir)
    except (ConfigError, OSError, ValueError, KeyError, TypeError):
        return False
    return current_tool != plan_tool


def _advance(
    workflow: Workflow,
    selected: Path,
    config: ControlConfig,
    campaign: Path,
    document: dict[str, Any],
) -> int:
    state_root = _private_state_root()
    while document["current_stage"] < len(document["stages"]):
        index = document["current_stage"]
        stage = document["stages"][index]
        if stage["status"] in {"PUBLISHED", "ALREADY_SELECTED", "LOCAL_COMMITTED"}:
            document = _next_stage(document, index)
            document = persist_campaign(campaign, document)
            _index(document, campaign, state_root)
            continue
        if stage["status"] == "DRY_RUN":
            document = transition(document, "DRY_RUN", "dry-run evidence retained")
            document = persist_campaign(campaign, document)
            _index(document, campaign, state_root)
            print(render_status(document, campaign), end="", flush=True)
            return 1 if stage.get("failure") else 0
        if stage["status"] == "APPLYING":
            result_doc = _reconcile_applying_stage(workflow, campaign, document, stage)
            if result_doc is None:
                return 0
            document = result_doc
            stage = document["stages"][index]
            if document["state"] == "BLOCKED":
                return 1
            if stage["status"] in {"PUBLISHED", "ALREADY_SELECTED", "LOCAL_COMMITTED"}:
                continue
            if stage["status"] == "READY_FOR_REVIEW":
                pass
            else:
                return 1
        if stage["status"] == "BLOCKED":
            if stage.get("local_commit"):
                # A commit-created failure is a durable local boundary. Do
                # not prepare or apply again and risk creating a second local
                # promotion commit while publication is unresolved.
                print(
                    f"nix-update: campaign stage {stage['name']} retains local commit "
                    f"{stage['local_commit']}; publication recovery is separate",
                    file=sys.stderr,
                    flush=True,
                )
                return 1
            if _retry_same_plan(stage):
                stage["status"] = "READY_FOR_REVIEW"
                stage["failure"] = None
                document = transition(document, "AWAITING_APPLY", "retrying retained native plan")
                document = persist_campaign(campaign, document)
            else:
                stage["status"] = "NEW"
                stage["failure"] = None
                stage["current_plan"] = None
                document["blocker"] = None
                document = transition(document, "PREPARING", "retrying native prepare after retained failure")
                document = persist_campaign(campaign, document)
            _index(document, campaign, state_root)
            stage = document["stages"][index]
        if stage["status"] in {"NEW", "PREPARING"}:
            result = _prepare_stage(workflow, selected, config, campaign, document, stage)
            if result is not None:
                document = result
            else:
                document = load_campaign(campaign)
            stage = document["stages"][index]
        if stage["status"] == "DRY_RUN":
            continue
        if stage["status"] in {"ALREADY_SELECTED", "LOCAL_COMMITTED"}:
            continue
        if stage["status"] == "BLOCKED":
            return 1
        if stage["status"] == "READY_FOR_REVIEW":
            if _stage_plan_tool_is_stale(workflow, stage):
                print(
                    f"nix-update: campaign stage {stage['name']} plan tool identity drifted; refreshing native preparation",
                    flush=True,
                )
                document = transition(
                    document,
                    "PREPARING",
                    f"refreshing native prepare for stage {stage['name']} after tool identity drifted",
                )
                document = persist_campaign(campaign, document)
                _index(document, campaign, state_root)
                stage = document["stages"][index]
                result = _prepare_stage(workflow, selected, config, campaign, document, stage)
                if result is not None:
                    document = result
                else:
                    document = load_campaign(campaign)
                stage = document["stages"][index]
                if stage["status"] == "BLOCKED":
                    return 1
                if stage["status"] in {"DRY_RUN", "ALREADY_SELECTED", "LOCAL_COMMITTED"}:
                    continue
                if stage["status"] != "READY_FOR_REVIEW":
                    return 1
            if not _has_controlling_terminal():
                _awaiting_confirmation(document, campaign, stage)
                return 0
            document = _apply_stage(workflow, campaign, document, stage)
            if document["state"] == "BLOCKED":
                return 1
            if stage["status"] == "READY_FOR_REVIEW":
                return 0
            if stage["status"] in {"PUBLISHED", "ALREADY_SELECTED", "LOCAL_COMMITTED"}:
                continue
            return 1
        return 1
    document = _complete(document)
    document = persist_campaign(campaign, document)
    _index(document, campaign, state_root)
    print(render_status(document, campaign), end="", flush=True)
    return 0


def _prepare_stage(
    workflow: Workflow,
    selected: Path,
    config: ControlConfig,
    campaign: Path,
    document: dict[str, Any],
    stage: dict[str, Any],
) -> dict[str, Any] | None:
    index = document["current_stage"]
    target_revision = stage.get("revision")
    if target_revision is None:
        target_revision = _inherited_revision(document, index)
    if target_revision is None:
        return _block(
            document,
            campaign,
            stage,
            "downstream stage has no published parent revision (local or published)",
            workflow,
        )
    stage["revision"] = target_revision
    attempt_number = len(stage["attempts"]) + 1
    stage_root = campaign / "stages" / _safe_stage_name(stage["name"])
    stage_root = _ensure_stage_root(campaign, stage_root)
    plan_path = stage_root / f"attempt-{attempt_number}"
    try:
        artifact = _prepare_native(
            workflow,
            Path(stage["repository"]),
            stage["input"],
            target_revision,
            plan_path,
            config,
            stage,
        )
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as error:
        return _block(document, campaign, stage, str(error), workflow)
    plan = {"path": os.fspath(artifact.directory), "digest": artifact.digest, "status": artifact.document.get("status")}
    stage.update(append_attempt(stage, plan))
    if artifact.document.get("status") == "BLOCKED":
        blocker = _plan_failure(artifact.document, artifact.directory)
        stage["status"] = "BLOCKED"
        stage["current_plan"] = None
        stage["failure"] = blocker
        document["blocker"] = {"stage": stage["name"], "failure": blocker}
        document = transition(document, "BLOCKED", "native prepare blocked the campaign", blocker=document["blocker"])
        document = persist_campaign(campaign, document)
        _index(document, campaign, _private_state_root())
        _print_failure(stage, blocker, campaign=campaign)
        return document
    if artifact.document.get("status") == "ALREADY_SELECTED":
        selected_commit = _plan_commit(artifact.document)
        if selected_commit is None:
            return _block(document, campaign, stage, "already-selected plan has no selected commit identity", workflow)
        # Keep a loaded v1 campaign on its legacy receipt shape. New local
        # authority is available only to the v2 stage schema.
        local = _local_authority_enabled(workflow) and "local_commit" in stage
        stage["status"] = "LOCAL_COMMITTED" if local else "ALREADY_SELECTED"
        stage["current_plan"] = None
        if local:
            stage["local_commit"] = selected_commit
            stage["publication_state"] = _local_publication_state(
                workflow, Path(stage["repository"])
            )
            stage["activation_state"] = "not_performed"
        else:
            stage["published_commit"] = selected_commit
        stage["impact"] = artifact.document.get("impact")
        if local:
            stage["attempts"][-1]["local_commit"] = selected_commit
            stage["attempts"][-1]["publication_state"] = stage["publication_state"]
            stage["attempts"][-1]["activation_state"] = "not_performed"
        else:
            stage["attempts"][-1]["published_commit"] = selected_commit
        document["blocker"] = None
        document = transition(document, "PREPARING", "native stage was already selected")
        document = persist_campaign(campaign, document)
        _index(document, campaign, _private_state_root())
        print(
            f"nix-update: campaign stage {stage['name']} already selected; "
            f"{'local commit retained' if local else 'no empty commit'}",
            flush=True,
        )
        return document
    if artifact.document.get("status") != "READY_FOR_REVIEW":
        return _block(document, campaign, stage, "native prepare returned an unrecognized plan status", workflow)
    if document.get("dry_run"):
        stage["status"] = "DRY_RUN"
        document["blocker"] = None
        document = transition(document, "DRY_RUN", "dry-run evidence retained")
        document = persist_campaign(campaign, document)
        _index(document, campaign, _private_state_root())
        print(
            f"nix-update: campaign stage {stage['name']} prepared (dry-run); plan: {artifact.directory}",
            flush=True,
        )
        return document
    stage["status"] = "READY_FOR_REVIEW"
    document["blocker"] = None
    document = transition(document, "AWAITING_APPLY", "native stage prepared and awaits confirmation")
    document = persist_campaign(campaign, document)
    _index(document, campaign, _private_state_root())
    print(
        f"nix-update: campaign stage {stage['name']} prepared; plan: {artifact.directory}",
        flush=True,
    )
    return document


def _apply_stage(
    workflow: Workflow,
    campaign: Path,
    document: dict[str, Any],
    stage: dict[str, Any],
) -> dict[str, Any]:
    plan_path = stage.get("current_plan")
    if not plan_path and stage.get("attempts"):
        plan_path = stage["attempts"][-1].get("plan_path")
        stage["current_plan"] = plan_path
    if not plan_path:
        return _block(document, campaign, stage, "awaiting stage has no native plan", workflow)
    plan_path = Path(plan_path)
    try:
        artifact = load_plan(plan_path)
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as error:
        return _block(document, campaign, stage, str(error), workflow)
    if artifact.document.get("status") != "READY_FOR_REVIEW":
        return _block(document, campaign, stage, "retained native plan is no longer review-ready", workflow)
    stage["status"] = "APPLYING"
    document = transition(document, "AWAITING_APPLY", "awaiting native apply confirmation")
    document = persist_campaign(campaign, document)
    record_index = len(getattr(workflow, "records", ()) or ())
    try:
        status = native_apply_promotion(workflow, plan_path, confirm=_tty_confirmation)
    except (ConfigError, OSError, ValueError, KeyError, TypeError, EOFError) as error:
        if _is_terminal_unavailable(error):
            stage["status"] = "READY_FOR_REVIEW"
            stage["failure"] = None
            stage["current_plan"] = os.fspath(plan_path)
            document["blocker"] = None
            document = transition(document, "AWAITING_APPLY", "terminal unavailable for confirmation; returned to prepared boundary")
            document = persist_campaign(campaign, document)
            _index(document, campaign, _private_state_root())
            _awaiting_confirmation(document, campaign, stage)
            return document
        failure = _failure_with_records(workflow, str(error), operation_hint="promotion confirmation", since_index=record_index)
        return _block(document, campaign, stage, failure, workflow, failure_object=True)
    try:
        result = load_native_result(plan_path, artifact.digest)
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as error:
        failure = _failure_with_records(workflow, str(error), operation_hint="native result validation", since_index=record_index)
        return _block(document, campaign, stage, failure, workflow, failure_object=True)
    stage.update(update_attempt(stage, result))
    stage["impact"] = result.get("impact") or artifact.document.get("impact")
    if result["terminal_state"] in LOCAL_SUCCESS_STATES:
        stage["status"] = "LOCAL_COMMITTED"
        stage["failure"] = None
        stage["current_plan"] = None
        document["blocker"] = None
        document = transition(document, "PREPARING", "native stage committed locally; advancing to the next stage")
        document = persist_campaign(campaign, document)
        _index(document, campaign, _private_state_root())
        print(
            f"nix-update: campaign stage {stage['name']} committed locally "
            f"at {result['local_commit']}; publication and activation remain separate",
            flush=True,
        )
        return document
    if result["terminal_state"] in SUCCESS_STATES:
        stage["status"] = "PUBLISHED"
        stage["failure"] = None
        stage["current_plan"] = None
        document["blocker"] = None
        document = transition(document, "PREPARING", "native stage published; advancing to the next stage")
        document = persist_campaign(campaign, document)
        _index(document, campaign, _private_state_root())
        print(
            f"nix-update: campaign stage {stage['name']} published {result['published_commit']}; activation remains separate",
            flush=True,
        )
        return document
    failure = result.get("failure") or _failure_with_records(
        workflow, f"native apply ended in {result['terminal_state']}", operation_hint="native apply"
    )
    stage["status"] = "BLOCKED"
    stage["failure"] = failure
    # A local commit-created outcome is durable even though the campaign is
    # blocked. Keep its identity and do not retry native apply automatically.
    # Publication recovery is native apply recovery. Retain the exact plan so
    # resume can inspect the remote and invoke that recovery transaction.
    if result["terminal_state"] in LOCAL_COMMIT_CREATED_STATES:
        stage["current_plan"] = None
    elif result["terminal_state"] in RETRYABLE_PUBLICATION_STATES:
        stage["current_plan"] = os.fspath(plan_path)
    else:
        stage["current_plan"] = None
    document["blocker"] = {"stage": stage["name"], "failure": failure}
    document = transition(document, "BLOCKED", f"native apply ended in {result['terminal_state']}", blocker=document["blocker"])
    document = persist_campaign(campaign, document)
    _index(document, campaign, _private_state_root())
    _print_failure(stage, failure, campaign=campaign)
    return document


def _reconcile_applying_stage(
    workflow: Workflow,
    campaign: Path,
    document: dict[str, Any],
    stage: dict[str, Any],
) -> dict[str, Any] | None:
    plan_path = stage.get("current_plan")
    if not plan_path and stage.get("attempts"):
        plan_path = stage["attempts"][-1].get("plan_path")
        stage["current_plan"] = plan_path
    if not plan_path:
        return _block(document, campaign, stage, "interrupted applying stage has no native plan", workflow)
    plan_path = Path(plan_path)
    try:
        artifact = load_plan(plan_path)
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as error:
        return _block(document, campaign, stage, f"unable to load retained native plan: {error}", workflow)

    result_path = plan_path / "result.json"
    if result_path.exists():
        try:
            result = load_native_result(plan_path, artifact.digest)
        except (ConfigError, OSError, ValueError, KeyError, TypeError) as error:
            return _block(document, campaign, stage, f"interrupted applying stage native result is invalid: {error}", workflow)
        stage.update(update_attempt(stage, result))
        stage["impact"] = result.get("impact") or artifact.document.get("impact")
        if result["terminal_state"] in LOCAL_SUCCESS_STATES:
            stage["status"] = "LOCAL_COMMITTED"
            stage["failure"] = None
            stage["current_plan"] = None
            document["blocker"] = None
            document = transition(document, "PREPARING", "adopted locally committed result; advancing to next stage")
            document = persist_campaign(campaign, document)
            _index(document, campaign, _private_state_root())
            print(
                f"nix-update: campaign stage {stage['name']} committed locally "
                f"at {result['local_commit']}; publication and activation remain separate",
                flush=True,
            )
            return document
        if result["terminal_state"] in SUCCESS_STATES:
            stage["status"] = "PUBLISHED"
            stage["failure"] = None
            stage["current_plan"] = None
            document["blocker"] = None
            document = transition(document, "PREPARING", "adopted completed native result; advancing to next stage")
            document = persist_campaign(campaign, document)
            _index(document, campaign, _private_state_root())
            print(
                f"nix-update: campaign stage {stage['name']} published {result['published_commit']}; activation remains separate",
                flush=True,
            )
            return document
        failure = result.get("failure") or _failure_with_records(
            workflow, f"native apply ended in {result['terminal_state']}", operation_hint="native apply"
        )
        stage["status"] = "BLOCKED"
        stage["failure"] = failure
        if result["terminal_state"] in LOCAL_COMMIT_CREATED_STATES:
            stage["current_plan"] = None
        elif result["terminal_state"] in RETRYABLE_PUBLICATION_STATES:
            stage["current_plan"] = os.fspath(plan_path)
        else:
            stage["current_plan"] = None
        document["blocker"] = {"stage": stage["name"], "failure": failure}
        document = transition(document, "BLOCKED", f"native apply ended in {result['terminal_state']}", blocker=document["blocker"])
        document = persist_campaign(campaign, document)
        _index(document, campaign, _private_state_root())
        _print_failure(stage, failure, campaign=campaign)
        return document

    marker = _consumption_path(artifact.digest)
    if not marker.exists():
        stage["status"] = "READY_FOR_REVIEW"
        stage["failure"] = None
        stage["current_plan"] = os.fspath(plan_path)
        document["blocker"] = None
        document = transition(document, "AWAITING_APPLY", "interrupted before native mutation; returned to prepared boundary")
        document = persist_campaign(campaign, document)
        _index(document, campaign, _private_state_root())
        return document

    stage["current_plan"] = os.fspath(plan_path)
    if not _has_controlling_terminal():
        _awaiting_confirmation(document, campaign, stage)
        return None
    document = _apply_stage(workflow, campaign, document, stage)
    if document.get("state") == "BLOCKED":
        return document
    stage = document["stages"][document["current_stage"]]
    if stage["status"] == "READY_FOR_REVIEW":
        return None
    return document


def _prepare_native(
    workflow: Workflow,
    repository: Path,
    input_name: str,
    revision: str,
    output: Path,
    config: ControlConfig,
    stage: Mapping[str, Any],
) -> Any:
    hook = _topology_hook("prepare_campaign_stage")
    if hook is not None:
        return hook(workflow, repository, input_name, revision, output, config, dict(stage))
    allow_local_fixture = bool(getattr(config, "allow_local_fixture", False))
    return native_prepare_promotion(
        workflow,
        repository,
        input_name,
        revision,
        output,
        allow_local_fixture=allow_local_fixture,
    )


def _topology_hook(name: str) -> Callable[..., Any] | None:
    try:
        module = importlib.import_module(".promotion_topology", __package__)
    except ModuleNotFoundError as error:
        if error.name not in {f"{__package__}.promotion_topology", "promotion_topology"}:
            raise
        return None
    value = getattr(module, name, None)
    return value if callable(value) else None


def _next_stage(document: Mapping[str, Any], index: int) -> dict[str, Any]:
    value = dict(document)
    value["current_stage"] = index + 1
    stages = [dict(stage) for stage in value["stages"]]
    if index + 1 < len(stages):
        next_stage = stages[index + 1]
        if next_stage.get("revision") is None:
            next_stage["revision"] = _stage_commit(stages[index])
    value["stages"] = stages
    value["blocker"] = None
    return value


def _complete(document: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(document)
    final = value["stages"][-1]
    value["final_impact"] = final.get("impact")
    impact = final.get("impact") or {}
    classification = impact.get("classification") if isinstance(impact, Mapping) else None

    if final.get("status") == "LOCAL_COMMITTED" or final.get("local_commit"):
        return transition(
            value,
            "LOCAL_COMPLETE_AWAITING_ACTIVATION",
            "final composition committed locally; publication and host activation remain separate",
        )
    if final.get("status") == "ALREADY_SELECTED":
        return transition(
            value,
            "AWAITING_DEPLOYMENT_RECONCILIATION",
            "final composition was already selected; live deployment state is unassessed "
            "(run 'nix-update --dry-run' or 'nix-update' to observe live host state)",
        )
    if classification == "HOST_DERIVATIONS_CHANGED":
        return transition(
            value,
            "AWAITING_ACTIVATION",
            "final composition published; host activation remains separate (run 'nix-update' to activate)",
        )
    if classification == "HOST_DERIVATIONS_IDENTICAL":
        return transition(
            value,
            "COMPLETE_NO_ACTIVATION",
            "final composition published with no additional host change from this promotion",
        )
    return transition(
        value,
        "AWAITING_DEPLOYMENT_RECONCILIATION",
        "final composition deployment impact is unassessed or unrecognized "
        "(run 'nix-update --dry-run' to observe live host state)",
    )


def _block(
    document: Mapping[str, Any],
    campaign: Path,
    stage: dict[str, Any],
    cause: str | Mapping[str, Any],
    workflow: Workflow,
    *,
    failure_object: bool = False,
) -> dict[str, Any]:
    failure = dict(cause) if failure_object and isinstance(cause, Mapping) else _failure_with_records(workflow, str(cause), operation_hint="native promotion")
    stage["status"] = "BLOCKED"
    stage["current_plan"] = stage.get("current_plan")
    stage["failure"] = failure
    blocker = {"stage": stage["name"], "failure": failure}
    value = dict(document)
    value["blocker"] = blocker
    value = transition(value, "BLOCKED", f"stage {stage['name']} blocked", blocker=blocker)
    value = persist_campaign(campaign, value)
    _index(value, campaign, _private_state_root())
    _print_failure(stage, failure, campaign=campaign)
    return value


def _failure_with_records(workflow: Workflow, cause: str, *, operation_hint: str, since_index: int = 0) -> dict[str, Any]:
    failure = failure_from_records(workflow, cause, since_index=since_index)
    if failure["operation"] == "native promotion" and operation_hint:
        failure["operation"] = operation_hint
    return failure


def _plan_failure(document: Mapping[str, Any], directory: Path) -> dict[str, Any]:
    blockers = document.get("blockers")
    cause = blockers[0] if isinstance(blockers, list) and blockers else "native promotion plan is blocked"
    return {
        "message": str(cause)[:2048],
        "operation": "native promotion prepare",
        "evidence": os.fspath(directory / "plan.json"),
    }


def _plan_commit(document: Mapping[str, Any]) -> str | None:
    for field in ("source_authority", "repository"):
        authority = document.get(field)
        commit = authority.get("commit") if isinstance(authority, Mapping) else None
        if isinstance(commit, str) and len(commit) == 40 and all(char in "0123456789abcdef" for char in commit):
            return commit
    return None


def _inherited_revision(document: Mapping[str, Any], index: int) -> str | None:
    if index == 0:
        return document.get("revision")
    return _stage_commit(document["stages"][index - 1])


def _retry_same_plan(stage: Mapping[str, Any]) -> bool:
    if stage.get("local_commit"):
        return False
    plan = stage.get("current_plan")
    if not plan:
        return False
    failure = stage.get("failure")
    if isinstance(failure, Mapping) and "confirmation" in str(failure.get("operation", "")).casefold():
        return True
    attempts = stage.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        return False
    return attempts[-1].get("terminal_state") in RETRYABLE_PUBLICATION_STATES


def _awaiting_confirmation(document: Mapping[str, Any], campaign: Path, stage: Mapping[str, Any]) -> None:
    plan_path = stage.get("current_plan")
    if not plan_path and stage.get("attempts"):
        plan_path = stage["attempts"][-1].get("plan_path")
    try:
        artifact = load_plan(Path(plan_path)) if plan_path else None
    except (ConfigError, OSError, ValueError, KeyError, TypeError):
        artifact = None
    digest = artifact.digest if artifact is not None else "unknown"
    print(
        f"nix-update: awaiting native confirmation for campaign stage {stage['name']}; "
        f"campaign: {campaign}; plan: {plan_path}; "
        f"type 'APPLY NIX-UPDATE PROMOTION {digest}' at the repository mutation boundary",
        flush=True,
    )
    print(
        f"nix-update: to resume, run 'nix-update promote campaign resume --campaign {campaign}' in an interactive terminal",
        flush=True,
    )



def _tty_confirmation(prompt: str) -> str:
    try:
        with open("/dev/tty", "w", encoding="utf-8", errors="strict") as tty_out, open(
            "/dev/tty", "r", encoding="utf-8", errors="strict"
        ) as tty_in:
            tty_out.write(prompt)
            tty_out.flush()
            line = tty_in.readline()
            if not line:
                raise EOFError("promotion confirmation stream ended")
            return line.rstrip("\r\n")
    except OSError as error:
        if error.errno in TERMINAL_UNAVAILABLE_ERRNOS:
            raise TerminalUnavailableError("promotion confirmation requires a controlling terminal") from error
        raise


def _has_controlling_terminal() -> bool:
    try:
        descriptor = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        return False
    try:
        return os.isatty(descriptor)
    finally:
        os.close(descriptor)


def _coerce_stage(value: Any, input_name: str, revision: str, index: int) -> CampaignStage | Mapping[str, Any]:
    if isinstance(value, CampaignStage):
        return value
    if isinstance(value, Mapping):
        result = dict(value)
        result.setdefault("name", result.get("stage", f"stage-{index + 1}"))
        result.setdefault("kind", result.get("role", "composition"))
        result.setdefault("input", result.get("input_name", input_name if index == 0 else input_name))
        result.setdefault("revision", revision if index == 0 else None)
        if "repository" not in result and "path" in result:
            result["repository"] = result["path"]
        return result
    for fields in (("name", "repository", "input", "revision", "kind"),):
        if all(hasattr(value, field) for field in fields[:2]):
            return CampaignStage(
                name=str(getattr(value, "name")),
                repository=Path(getattr(value, "repository")),
                input=str(getattr(value, "input", input_name)),
                revision=getattr(value, "revision", revision if index == 0 else None),
                kind=str(getattr(value, "kind", "composition")),
            )
    raise ConfigError("promotion topology stage has an unsupported shape")


def _stage_mapping(stage: CampaignStage | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(stage, CampaignStage):
        return {
            "name": stage.name,
            "kind": stage.kind,
            "repository": stage.repository,
            "input": stage.input,
            "revision": stage.revision,
        }
    return stage


def _ensure_stage_root(campaign: Path, path: Path) -> Path:
    from .deployment_paths import ensure_private_root

    stages_root = ensure_private_root(campaign / "stages", "campaign stages root")
    safe = path if path.parent == stages_root else path
    return ensure_private_root(safe, "campaign stage root") if safe.exists() else _create_stage_root(safe, stages_root)


def _create_stage_root(path: Path, parent: Path) -> Path:
    from .deployment_paths import create_private_directory

    return create_private_directory(path, "campaign stage root", parent=parent)


def _safe_stage_name(value: str) -> str:
    text = "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
    if not text:
        text = "stage"
    return text[:96]


def _existing_matches(
    state_root: Path,
    selected: Path,
    input_name: str | None,
    revision: str | None,
    *,
    dry_run: bool | None = None,
) -> list[Path]:
    matches = matching_campaigns(
        state_root,
        selected=selected,
        input_name=input_name,
        revision=revision,
        dry_run=dry_run,
    )
    return [path for path in matches if path.exists() and path.is_dir()]


def _index(document: Mapping[str, Any], campaign: Path, state_root: Path) -> None:
    update_index(
        state_root,
        campaign=campaign,
        selected=Path(document["selected"]),
        input_name=document["input"],
        revision=document["revision"],
        state=document["state"],
        dry_run=bool(document.get("dry_run", False)),
    )


def _print_choices(campaigns: list[Path]) -> None:
    print("nix-update: multiple matching promotion campaigns; choose one with --campaign:", file=sys.stderr, flush=True)
    for path in campaigns:
        print(f"  {path}", file=sys.stderr, flush=True)


def _print_failure(stage: Mapping[str, Any], failure: Mapping[str, Any], *, campaign: Path | None = None) -> None:
    print(
        f"nix-update: campaign blocked at stage {stage['name']}: {failure.get('message', 'native promotion failed')}",
        file=sys.stderr,
        flush=True,
    )
    if failure.get("evidence"):
        print(f"nix-update: detailed private evidence: {failure['evidence']}", file=sys.stderr, flush=True)
    if campaign is not None and "confirmation" in str(failure.get("operation", "")).casefold():
        print(
            f"nix-update: to resume, run 'nix-update promote campaign resume --campaign {campaign}' in an interactive terminal",
            file=sys.stderr,
            flush=True,
        )


def _render_historical(campaign: Path) -> bool:
    from .deployment_paths import require_private_directory, require_private_file
    from .promotion_contract import strict_json_loads

    try:
        require_private_directory(campaign, "campaign directory")
        path = campaign / JOURNAL_NAME
        require_private_file(path, "campaign journal")
        raw = path.read_bytes()
        value = strict_json_loads(raw.decode("utf-8"), "campaign journal")
    except (ConfigError, OSError, UnicodeDecodeError):
        return False
    if not isinstance(value, Mapping):
        return False
    print(f"nix-update: historical campaign journal (read-only): {campaign}", flush=True)
    for key in ("schema", "version", "state", "input", "revision", "updated_at"):
        if key in value and isinstance(value[key], (str, int, float, bool)):
            print(f"{key}: {value[key]}", flush=True)
    print("recovery: start a new DINAS campaign; historical evidence was not modified", flush=True)
    return True


def _absolute_path(path: Path, label: str) -> Path:
    path = Path(path)
    if not path.is_absolute():
        path = path.absolute()
    normalized = Path(os.path.normpath(os.fspath(path)))
    if normalized != path:
        raise ConfigError(f"{label} must be an exact normalized absolute path: {path}")
    return path


def _validate_identity(value: str, label: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 128 or any(char.isspace() for char in value):
        raise ConfigError(f"{label} is invalid")


def _stage_commit(stage: Mapping[str, Any]) -> str | None:
    """Return the durable stage commit, preferring local authority."""

    local = stage.get("local_commit")
    if isinstance(local, str) and local:
        return local
    published = stage.get("published_commit")
    return published if isinstance(published, str) and published else None


def _local_authority_enabled(workflow: Workflow) -> bool:
    return bool(getattr(workflow, "sources", ()))


def _local_publication_state(workflow: Workflow, repository: Path) -> str:
    """Report whether the local source has an explicit publication authority."""

    normalized = Path(os.path.normpath(os.fspath(repository)))
    for source in getattr(workflow, "sources", ()) or ():
        checkout = getattr(source, "checkout", None)
        if checkout is None:
            continue
        try:
            source_checkout = Path(os.path.normpath(os.fspath(checkout)))
        except (TypeError, ValueError, OSError):
            continue
        if source_checkout == normalized:
            return "pending" if getattr(source, "publication", None) is not None else "unavailable"
    return "unavailable"


def _bind_workflow_sources(workflow: Workflow, config: ControlConfig) -> None:
    """Bind API-created workflows to the operator source catalog."""

    sources = getattr(config, "sources", None)
    if sources is not None:
        workflow.sources = tuple(sources)
