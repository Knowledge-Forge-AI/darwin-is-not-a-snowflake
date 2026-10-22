from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import platform
import pwd
import re
import shutil
import signal
import select
import socket
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .config import operator_home
from .deployment_participants import (
    ParticipantResult,
    bounded_rollback,
    freeze_operator_artifacts,
    run_participant,
    validate_executable,
)
from .deployment_plan import (
    SCHEMA,
    PlanArtifact,
    canonical_bytes,
    file_identity,
    load_contract,
    load_plan,
    lock_delta,
    normalized_lock,
    read_lock_document,
    redact_text,
    redacted_summary,
    reject_unapproved_lock_delta,
    serialize_lock_document,
    sha256_bytes,
    tree_identity,
    write_plan,
    write_result,
)
from .deployment_state import (
    DeploymentLock,
    apply_control_transaction,
    competing_processes,
    durable_marker,
    inspect_profile,
    process_ancestors,
    preflight_control_transactions,
    read_process_table,
    realignment_command,
    restore_control_transaction,
    selected_generation,
    validate_control_transaction_current,
    validate_profile_transition,
    verify_control_postimage,
)
from .paths import ConfigError
from .repository_identity import local_git_identity
from .deployment_paths import (
    NOFOLLOW,
    atomic_private_write,
    create_private_directory,
    ensure_private_root,
    exclusive_private_write,
    fsync_directory,
    require_private_directory,
    require_private_file,
    validate_ancestor_chain,
)
from .projection import CurrentSnapshot, inspect_scratch, publish_scratch, render_scratch, restore_current, snapshot_current


@dataclass(frozen=True)
class CommandRecord:
    label: str
    argv: tuple[str, ...]
    cwd: str
    started_at: str
    finished_at: str
    exit_status: int
    stdout: dict[str, Any]
    stderr: dict[str, Any]


@dataclass(frozen=True)
class LockCandidate:
    output: Path
    authority: dict[str, Any]
    preimage_bytes: bytes
    preimage: dict[str, Any]
    candidate_bytes: bytes
    candidate: dict[str, Any]
    changes: list[dict[str, Any]]
    authorized_nodes: tuple[str, ...]
    closure_authorization: dict[str, Any] | None = None
    preimage_flake_nix_bytes: bytes | None = None
    candidate_flake_nix_bytes: bytes | None = None


class LockCommandFailure(ConfigError):
    def __init__(self, status: int):
        super().__init__(f"isolated lock maintenance failed with status {status}")
        self.status = status


Runner = Callable[..., subprocess.CompletedProcess[str]]


class Workflow:
    def __init__(
        self,
        core_dir: Path,
        runner: Runner = subprocess.run,
        *,
        display_command_output: bool = True,
        sources: Sequence[Any] = (),
    ):
        self.core_dir = core_dir.resolve()
        self.runner = runner
        self.display_command_output = display_command_output
        self.sources = tuple(sources)
        self.records: list[CommandRecord] = []
        self.evidence_root: Path | None = None

    def _evidence_directory(self) -> Path:
        if self.evidence_root is None:
            parent = ensure_private_root(_private_state_root() / "command-evidence", "command evidence root")
            candidate = parent / f"run-{os.getpid()}-{os.urandom(6).hex()}"
            self.evidence_root = create_private_directory(candidate, "command evidence directory")
        return self.evidence_root

    def command(self, label: str, argv: Sequence[str], cwd: Path, *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        print(f"nix-update: running {label}", flush=True)
        started = _now()
        evidence_directory = self._evidence_directory()
        record_number = len(self.records)
        stdout_path = evidence_directory / f"{record_number:04d}.stdout"
        stderr_path = evidence_directory / f"{record_number:04d}.stderr"
        if self.runner is subprocess.run:
            try:
                result, stdout_evidence, stderr_evidence = _run_streamed_command(
                    list(argv), cwd, stdout_path, stderr_path, timeout=timeout
                )
            except ConfigError as error:
                raise ConfigError(f"{label}: {error}") from error
        else:
            result = self.runner(
                list(argv), cwd=cwd, check=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                **({"timeout": timeout} if timeout is not None else {}),
            )
            try:
                stdout_evidence = _write_bounded_stream(stdout_path, result.stdout)
                stderr_evidence = _write_bounded_stream(stderr_path, result.stderr)
            except Exception:
                if not result.returncode:
                    raise
                return self._unretained_failure(label, argv, cwd, started, result)
        try:
            fsync_directory(evidence_directory)
            safe_stdout = _command_summary(stdout_evidence, result.returncode)
            safe_stderr = _command_summary(stderr_evidence, result.returncode)
        except Exception:
            if not result.returncode:
                raise
            safe_stdout = safe_stderr = "command summary unavailable"
        record = CommandRecord(
            label,
            tuple(argv),
            str(cwd),
            started,
            _now(),
            result.returncode,
            {**stdout_evidence, "summary": safe_stdout[:COMMAND_SUMMARY_LIMIT]},
            {**stderr_evidence, "summary": safe_stderr[:COMMAND_SUMMARY_LIMIT]},
        )
        self.records.append(record)
        if self.display_command_output and safe_stdout:
            print(
                safe_stdout[:COMMAND_SUMMARY_LIMIT],
                end="" if safe_stdout[:COMMAND_SUMMARY_LIMIT].endswith("\n") else "\n",
                flush=True,
            )
        if self.display_command_output and safe_stderr:
            print(
                safe_stderr[:COMMAND_SUMMARY_LIMIT],
                end="" if safe_stderr[:COMMAND_SUMMARY_LIMIT].endswith("\n") else "\n",
                file=os.sys.stderr,
                flush=True,
            )
        return result

    def _unretained_failure(self, label, argv, cwd, started, result):
        """Preserve a known nonzero process status if evidence writing fails."""
        self.records.append(CommandRecord(
            label, tuple(argv), str(cwd), started, _now(), result.returncode,
            {"summary": "command evidence unavailable"}, {"summary": "command evidence unavailable"},
        ))
        return result

    def complete_stdout(self, label: str) -> str:
        if not self.records or self.records[-1].label != label:
            raise ConfigError(f"complete command output is not bound to {label}")
        evidence = self.records[-1].stdout
        if evidence["overflow"]:
            raise ConfigError(f"{label} stdout exceeds the {evidence['limit']}-byte semantic limit")
        path = Path(evidence["path"])
        require_private_file(path, f"{label} stdout evidence")
        raw = path.read_bytes()
        if len(raw) != evidence["size"] or sha256_bytes(raw) != evidence["sha256"]:
            raise ConfigError(f"{label} stdout evidence identity is invalid")
        return raw.decode("utf-8", errors="strict")

    def diagnostic_stream(self, label: str, stream: str) -> tuple[str, Path, bool]:
        """Read bounded retained bytes for the latest invocation, never a child path."""
        if not self.records or self.records[-1].label != label or stream not in {"stdout", "stderr"}:
            raise ConfigError("diagnostic output is not bound to the failed invocation")
        evidence = getattr(self.records[-1], stream)
        if self.evidence_root is None:
            raise ConfigError("diagnostic evidence is unavailable")
        path = self.evidence_root / f"{len(self.records) - 1:04d}.{stream}"
        size = evidence.get("retained_bytes")
        if evidence.get("path") != str(path) or type(size) is not int or not 0 <= size <= COMMAND_RAW_LIMIT:
            raise ConfigError("diagnostic evidence binding is invalid")
        require_private_file(path, "diagnostic evidence")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1
                or metadata.st_size != size
            ):
                raise ConfigError("diagnostic evidence open identity is invalid")
            raw = source.read(size + 1)
        if len(raw) != size or sha256_bytes(raw) != evidence.get("retained_sha256"):
            raise ConfigError("diagnostic evidence content identity is invalid")
        return raw.decode("utf-8", errors="replace"), path, bool(evidence.get("overflow"))


def prepare(
    workflow: Workflow,
    selected: Path,
    host: str,
    control_dir: Path,
    contract_path: Path,
    output: Path,
    overrides: Sequence[str],
    test_jobs: str = "",
    build_jobs: str = "",
    preflight_only: bool = False,
) -> PlanArtifact:
    selected = selected.resolve()
    contract = load_contract(contract_path)
    state_root = _private_state_root()
    participants_declarations = freeze_operator_artifacts(contract.get("participants", []), state_root)
    blockers: list[str] = []
    observations: dict[str, Any] = {}
    with DeploymentLock(state_root / "transaction.lock"):
        _collect(
            blockers,
            "process/concurrency gate",
            lambda: _guard_processes(
                blockers, strict=False, participant_paths=[item["path"] for item in participants_declarations]
            ),
        )
        repository = _collect(blockers, "repository", lambda: local_git_identity(workflow, selected, require_clean=True))
        tool_identity = _collect(blockers, "tool identity", lambda: _tool_identity(workflow.core_dir))
        lock = _collect(blockers, "flake.lock", lambda: normalized_lock(selected / "flake.lock"))
        from .local_source_binding import capture_local_sources
        local_sources = _collect(
            blockers, "catalog local sources", lambda: capture_local_sources(workflow, lock or {})
        ) or []
        if lock is not None:
            _collect(
                blockers, "source lock binding",
                lambda: _validate_source_lock_bindings(lock, contract.get("sources", [])),
            )
        bound_overrides = _collect(
            blockers,
            "input override binding",
            lambda: _bind_source_overrides(overrides, contract.get("sources", [])),
        )
        evaluation_overrides = list(overrides if bound_overrides is None else bound_overrides)
        scratch_parent = Path(tempfile.mkdtemp(prefix="nix-update-projection-", dir=state_root))
        scratch_parent.chmod(0o700)
        require_private_directory(scratch_parent, "prepare scratch directory")
        control_projection = _collect(
            blockers,
            "control transaction preflight",
            lambda: preflight_control_transactions(
                control_dir,
                contract.get("control_transactions", []),
                scratch_parent / "virtual-control",
            ) if Path(str(contract.get("control_root", control_dir))).absolute() == control_dir.absolute()
            else (_ for _ in ()).throw(ConfigError("contract control_root does not equal selected control directory")),
        )
        allowlist = _collect(
            blockers, "config allowlist", lambda: evaluate_allowlist(workflow, selected, evaluation_overrides)
        ) if control_projection is not None else None
        scratch_projection = None
        if allowlist is not None and control_projection is not None:
            scratch_projection = _collect(
                blockers,
                "localConfig projection",
                lambda: render_scratch(Path(control_projection["virtual_root"]), scratch_parent / "localConfig", allowlist),
            )
        exact_overrides = list(evaluation_overrides)
        if scratch_projection is not None:
            exact_overrides.extend(["--override-input", "localConfig", f"path:{scratch_projection.root}"])
        source_identities = _collect_sources(workflow, selected, contract.get("sources", []), blockers)
        probe_identities = _collect_probes(contract.get("state_probes", []), blockers)
        profile = _collect(blockers, "system profile", lambda: _profile_snapshot(contract))
        if profile is not None and profile["relationship"] == "unknown":
            blockers.append("selected-profile/live-system relationship is unknown")
        external = _collect_external(contract.get("external_state", []), blockers)
        machine_control = (
            control_projection["current_tree"] if control_projection is not None
            else _collect(blockers, "machine-local control snapshot", lambda: tree_identity(control_dir))
        )
        evaluation_before = snapshot_current(control_dir.parent / "eval")
        participants: list[ParticipantResult] = []
        test_command = None
        if scratch_projection is not None:
            try:
                qualification_root = scratch_parent / "qualification-reports"
                create_private_directory(qualification_root, "qualification report root")
                test_command = _qualification_command(
                    selected, qualification_root, test_jobs, exact_overrides
                )
            except ConfigError as error:
                blockers.append(f"selected-flake report qualification: {error}")
        if test_command is not None:
            check_result = workflow.command("selected-flake report qualification", test_command, selected)
            if check_result.returncode:
                blockers.append(f"selected-flake report qualification exited {check_result.returncode}")
        if scratch_projection is not None:
            projected_check = workflow.command(
                "exact projected flake check",
                ["nix", "flake", "check", "path:.", "--no-update-lock-file", "--no-write-lock-file", *exact_overrides],
                selected,
            )
            if projected_check.returncode:
                blockers.append(f"exact projected flake check exited {projected_check.returncode}")
        build_command = [
            "nix", "build", "--print-out-paths", "--no-link", "--no-update-lock-file", "--no-write-lock-file",
            f"path:.#darwinConfigurations.{host}.system", *exact_overrides,
        ]
        if build_jobs:
            build_command.extend(["--max-jobs", build_jobs])
        target = ""
        closure: list[dict[str, Any]] = []
        host_candidates: list[dict[str, Any]] = []
        build_result = None if scratch_projection is None else workflow.command(
            "exact Darwin target build", build_command, selected
        )
        if build_result is not None and build_result.returncode:
            blockers.append(f"exact Darwin target build exited {build_result.returncode}")
        elif build_result is not None:
            paths = [line for line in build_result.stdout.splitlines() if line.startswith("/nix/store/")]
            if len(paths) != 1:
                blockers.append("exact Darwin target build did not emit one store path")
            else:
                target = paths[0]
                closure = _collect(blockers, "target closure", lambda: closure_inventory(workflow, selected, target)) or []
                host_candidates.append({"host": host, "output": target, "closure": closure})
                _collect(
                    blockers,
                    "participant provenance",
                    lambda: _validate_participant_provenance(participants_declarations, target, closure),
                )
        for additional_host in (
            contract.get("additional_hosts", []) if scratch_projection is not None else []
        ):
            additional_command = [
                "nix", "build", "--print-out-paths", "--no-link", "--no-update-lock-file", "--no-write-lock-file",
                f"path:.#darwinConfigurations.{additional_host}.system", *exact_overrides,
            ]
            if build_jobs:
                additional_command.extend(["--max-jobs", build_jobs])
            additional_result = workflow.command(f"exact Darwin target build ({additional_host})", additional_command, selected)
            additional_paths = [line for line in additional_result.stdout.splitlines() if line.startswith("/nix/store/")]
            if additional_result.returncode or len(additional_paths) != 1:
                blockers.append(f"additional Darwin target build failed: {additional_host}")
            else:
                additional_closure = _collect(
                    blockers,
                    f"target closure ({additional_host})",
                    lambda path=additional_paths[0]: closure_inventory(workflow, selected, path),
                ) or []
                host_candidates.append({"host": additional_host, "output": additional_paths[0], "closure": additional_closure})
        live_closure = _collect(blockers, "live closure", lambda: current_live_closure(workflow, selected, profile)) or []
        closure_delta = classify_closure_delta(live_closure, closure, contract.get("allowed_closure_delta", []))
        if closure_delta["unapproved"]:
            blockers.append(f"unapproved live-to-target closure movement: {closure_delta['unapproved'][0]}")
        participant_context_document = _participant_context_document(
            stage="prepare",
            plan_digest="",
            repository=repository or {},
            lock=lock or {},
            selected=selected,
            host=host,
            target=target,
            closure=closure,
            overrides=exact_overrides,
            projection={} if scratch_projection is None else {
                "root": str(scratch_projection.root),
                "files": [list(item) for item in scratch_projection.files],
                "digest": scratch_projection.digest, "published": False,
            },
            sources=source_identities,
            control_projection=control_projection or {},
            profile=profile or {},
            probes=probe_identities,
            external=external,
            participant_declarations=participants_declarations,
        )
        participant_context = _write_participant_context(state_root, participant_context_document)
        if target and closure:
            participants, participant_blockers = _safe_participant_phase(
                participants_declarations, "preflight", Path(participant_context["path"]), workflow.runner
            )
            blockers.extend(participant_blockers)
        elif participants_declarations:
            blockers.append("participant preflight requires an exact target and closure")
        nonmutation = {
            "repository_equal": _collect(blockers, "post-prepare repository", lambda: local_git_identity(workflow, selected, require_clean=True)) == repository,
            "flake_lock_equal": _collect(blockers, "post-prepare flake.lock", lambda: normalized_lock(selected / "flake.lock")) == lock,
            "machine_control_equal": _collect(blockers, "post-prepare machine control", lambda: tree_identity(control_dir)) == machine_control,
            "profile_equal": _collect(blockers, "post-prepare system profile", lambda: _profile_snapshot(contract)) == profile,
            "evaluation_current_equal": snapshot_current(control_dir.parent / "eval") == evaluation_before,
            "input_snapshots_equal": _collect_sources(
                workflow, selected, contract.get("sources", []), blockers
            ) == source_identities,
            "state_probes_equal": _collect_probes(contract.get("state_probes", []), blockers) == probe_identities,
            "external_state_equal": _collect_external(contract.get("external_state", []), blockers) == external,
        }
        for name, equal in nonmutation.items():
            if not equal:
                blockers.append(f"prepare mutated reviewed state: {name}")
        if getattr(workflow, "sources", ()):
            from .local_source_binding import revalidate_local_sources
            _collect(blockers, "local source revalidation", lambda: revalidate_local_sources(workflow, lock or {}, local_sources))
        creation = _now()
        confirmation = "APPLY NIX-UPDATE PLAN {plan_digest}"
        plan_document: dict[str, Any] = {
            "schema": SCHEMA,
            "tool": tool_identity or {},
            "environment": _environment_identity(),
            "plan_digest": "",
            "created_at": creation,
            "status": "BLOCKED" if blockers else "READY_FOR_REVIEW",
            "preflight_only": preflight_only,
            "blockers": blockers,
            "selected_flake": {"path": str(selected), "host": host, "repository": repository or {}},
            "lock": lock or {},
            "allowed_lock_nodes": contract.get("allowed_lock_nodes", []),
            "control_snapshot": file_identity(contract_path),
            "machine_control_snapshot": machine_control or {},
            "control_projection": control_projection or {},
            "evaluation_root": str(control_dir.parent / "eval"),
            "evaluation_current": asdict(evaluation_before),
            "projection": {} if scratch_projection is None else {
                "root": str(scratch_projection.root),
                "files": [list(item) for item in scratch_projection.files],
                "digest": scratch_projection.digest,
                "published": False,
            },
            "input_snapshots": source_identities,
            "overrides": exact_overrides,
            "checks": [asdict(record) for record in workflow.records],
            "target": {"derivation_output": target, "closure": closure},
            "host_candidates": host_candidates,
            "live": {"profile": profile, "closure": live_closure},
            "closure_delta": closure_delta,
            "external_state": external,
            "state_probes": probe_identities,
            "control_transactions": [] if control_projection is None else control_projection["effective_transactions"],
            "participants": participants_declarations,
            "participant_context": participant_context,
            "participant_preflight": [asdict(item) for item in participants],
            "profile_policy": contract.get("profile_policy", {}),
            "publish_local_config": contract.get("publish_local_config", False),
            "rollback_policy": contract.get(
                "rollback_policy",
                {"pre_switch": "restore-all", "post_switch": "participant-local-reverse"},
            ),
            "confirmation": {"template": confirmation},
            "observations": observations,
            "mutable_state_comparison": nonmutation,
            "command_evidence_policy": {
                "raw_limit_bytes": COMMAND_RAW_LIMIT,
                "summary_limit_bytes": COMMAND_SUMMARY_LIMIT,
                "root": str(workflow._evidence_directory()),
            },
        }
        if getattr(workflow, "sources", ()):
            plan_document["schema"] = "nix-update-deployment-plan-v3"
            plan_document["local_sources"] = local_sources
        markdown = _plan_markdown(plan_document)
        artifact = write_plan(output, plan_document, markdown)
        # The exact scratch bytes remain retained for review/apply. No eval/current
        # pointer is published during prepare or preflight-only.
        return artifact


def apply(workflow: Workflow, plan_path: Path, confirmation: str | None = None) -> int:
    artifact = load_plan(plan_path)
    document = artifact.document
    result_root = artifact.directory / "results"
    if result_root.exists() or result_root.is_symlink():
        require_private_directory(result_root, "deployment result root")
    else:
        create_private_directory(result_root, "deployment result root")
    result_directory = result_root / dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    create_private_directory(result_directory, "deployment result directory")
    result: dict[str, Any] = {
        "plan_digest": artifact.digest,
        "started_at": _now(),
        "terminal_state": "FAILED_PRE_SWITCH",
        "switch_invocation_started": False,
        "events": [],
    }
    if document.get("status") != "READY_FOR_REVIEW" or document.get("blockers"):
        result["failure"] = "blocked deployment plan cannot be applied"
        return _finish_result(result_directory, result, workflow, [], 1)
    if document.get("preflight_only"):
        result["failure"] = "preflight-only artifact cannot be applied"
        return _finish_result(result_directory, result, workflow, [], 1)
    state_root = _private_state_root()
    prior_markers = _prior_apply_markers(artifact, state_root)
    if prior_markers:
        result["terminal_state"] = "REPLAY_BLOCKED"
        result["failure"] = "this plan already consumed its durable pre-switch mutation authority"
        return _finish_result(result_directory, result, workflow, [], 1)
    participant_results: list[ParticipantResult] = []
    # Long-running build/closure qualification happens before authority is
    # requested. The same identities are checked again after confirmation.
    try:
        with DeploymentLock(state_root / "transaction.lock"):
            precheck_blockers: list[str] = []
            _guard_processes(
                precheck_blockers, strict=True, participant_paths=[item["path"] for item in document["participants"]]
            )
            _revalidate_plan(workflow, document)
            _revalidate_target(workflow, document, document["target"]["derivation_output"])
            fresh_context = _fresh_participant_context(document, state_root, "apply-pre-confirmation")
            fresh_results, fresh_blockers = _safe_participant_phase(
                document["participants"], "preflight", Path(fresh_context["path"]), workflow.runner
            )
            participant_results.extend(fresh_results)
            if fresh_blockers:
                raise ConfigError(fresh_blockers[0])
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as error:
        result["failure"] = str(error)
        return _finish_result(result_directory, result, workflow, participant_results, 1)
    expected = f"APPLY NIX-UPDATE PLAN {artifact.digest}"
    if confirmation is None:
        print(f"Type exactly: {expected}", file=os.sys.stderr, flush=True)
        confirmation = os.sys.stdin.readline().rstrip("\n")
    if confirmation != expected:
        result["failure"] = "typed confirmation does not match the reviewed plan digest"
        return _finish_result(result_directory, result, workflow, [], 1)
    restored: list[Mapping[str, Any]] = []
    projection_restore: tuple[Path, CurrentSnapshot, CurrentSnapshot] | None = None
    final_evaluation_current = CurrentSnapshot(**document["evaluation_current"])
    profile_restore: Mapping[str, Any] | None = None
    participant_pre_switch_indeterminate = False
    post_switch_reconcile: list[ParticipantResult] = []
    post_switch_reconcile_context: Path | None = None
    post_switch_rollback_attempted = False
    try:
        mutation_lock = DeploymentLock(state_root / "transaction.lock")
        mutation_lock.__enter__()
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as error:
        result["failure"] = str(error)
        return _finish_result(result_directory, result, workflow, participant_results, 1)
    if mutation_lock is not None:
        try:
            if _prior_apply_markers(artifact, state_root):
                result["terminal_state"] = "REPLAY_BLOCKED"
                raise ConfigError("this plan already consumed its durable pre-switch mutation authority")
            _revalidate_plan(workflow, document)
            _guard_processes([], strict=True, participant_paths=[item["path"] for item in document["participants"]])
            target = document["target"]["derivation_output"]
            _revalidate_target(workflow, document, target)
            if document["live"]["profile"].get("relationship") == "split-retained":
                _final_privilege_attestation(
                    workflow, document, state_root, "selected-profile-realignment",
                    result, participant_results,
                )
            consumed_root = ensure_private_root(state_root / "consumed-authority", "consumed authority root")
            consumed_path = consumed_root / f"{artifact.digest}.json"
            if consumed_path.exists() or consumed_path.is_symlink():
                result["terminal_state"] = "REPLAY_BLOCKED"
                raise ConfigError("this plan digest already consumed its durable mutation authority")
            exclusive_private_write(consumed_path, canonical_bytes({
                "schema": "nix-update-consumed-authority-v1",
                "plan_digest": artifact.digest,
                "consumed_at": _now(),
            }))
            fsync_directory(consumed_root)
            result["consumed_authority"] = str(consumed_path)
            pre_switch_marker = durable_marker(result_directory, "pre-switch-mutation.json", {
                "plan_digest": artifact.digest,
                "pre_switch_mutation_authority_consumed": True,
                "written_at": _now(),
            })
            result["pre_switch_marker"] = str(pre_switch_marker)
            policy = document.get("profile_policy", {})
            final_profile = document["live"]["profile"]
            if document["live"]["profile"].get("relationship") == "split-retained":
                if policy.get("action") != "realign-selected-profile" or not isinstance(policy.get("safe_generation"), int):
                    raise ConfigError("reviewed profile/live split has no typed realignment policy")
                profile_restore = document["live"]["profile"]
                command = [
                    document["tool"]["external"]["sudo"]["path"],
                    *realignment_command(
                        Path(document["live"]["profile"]["selected"]["path"]), policy["safe_generation"]
                    ),
                ]
                command[1] = document["tool"]["external"]["nix-env"]["path"]
                profile_marker = durable_marker(result_directory, "selected-profile-realignment-invocation.json", {
                    "plan_digest": artifact.digest,
                    "selected_profile_realignment_started": True,
                    "written_at": _now(),
                })
                result["profile_realignment_marker"] = str(profile_marker)
                result["events"].append({"event": "privilege-marker-written", "transition": "selected-profile-realignment"})
                result["events"].append({"event": "privileged-command-invoked", "transition": "selected-profile-realignment"})
                realigned = workflow.command("selected-profile realignment", command, Path(document["selected_flake"]["path"]))
                if realigned.returncode:
                    raise ConfigError("selected-profile realignment failed")
                realigned_profile = _profile_snapshot(document)
                reviewed_profile = document["live"]["profile"]
                if realigned_profile["live"] != reviewed_profile["live"]:
                    raise ConfigError("profile realignment changed the live system")
                if realigned_profile["selected"]["target"] != reviewed_profile["live"]["target"]:
                    raise ConfigError("profile realignment did not select the reviewed safe generation")
                if realigned_profile["retained"] != reviewed_profile["retained"]:
                    raise ConfigError("profile realignment changed retained generations")
                _validate_profile_realignment(
                    reviewed_profile, realigned_profile, policy["safe_generation"]
                )
                final_profile = realigned_profile
                result["events"].append({"event": "selected-profile-realigned", "generation": policy["safe_generation"]})
            if document.get("publish_local_config"):
                eval_root = Path(document["evaluation_root"])
                previous = snapshot_current(eval_root)
                reviewed_previous = CurrentSnapshot(**document["evaluation_current"])
                if previous != reviewed_previous:
                    raise ConfigError("evaluation current drifted from reviewed plan")
                expected_publication = CurrentSnapshot(
                    True, f"generations/{document['projection']['digest']}"
                )
                final_evaluation_current = expected_publication
                projection_restore = (eval_root, previous, expected_publication)
                publication = publish_scratch(Path(document["projection"]["root"]), eval_root)
                if snapshot_current(eval_root) != expected_publication:
                    raise ConfigError("localConfig publication did not select the reviewed generation")
                result["events"].append({"event": "local-config-published", "generation": str(publication.generation)})
            for transaction in document["control_transactions"]:
                existing_markers = set(result_directory.glob("control-*.json"))
                try:
                    apply_control_transaction(transaction, result_directory)
                except (ConfigError, OSError, ValueError, KeyError, TypeError):
                    if set(result_directory.glob("control-*.json")) != existing_markers:
                        restored.append(transaction)
                    raise
                restored.append(transaction)
                result["events"].append({"event": "control-postimage-installed", "path": transaction["path"]})
            pre_switch_context = _fresh_participant_context(
                document, state_root, "pre-switch", profile=final_profile,
                control_postimages=True, projection_published=document.get("publish_local_config", False),
            )
            pre_switch, blockers = _safe_participant_phase(
                document["participants"], "pre-switch", Path(pre_switch_context["path"]), workflow.runner
            )
            participant_results.extend(pre_switch)
            if blockers:
                participant_pre_switch_indeterminate = _participant_phase_indeterminate(
                    document["participants"], "pre-switch", pre_switch
                )
                raise ConfigError(blockers[0])
            _revalidate_plan(
                workflow, document, control_postimages=True, expected_profile=final_profile,
                expected_evaluation_current=final_evaluation_current,
            )
            _revalidate_target(workflow, document, target)
            _final_privilege_attestation(
                workflow, document, state_root, "system-switch", result, participant_results,
                profile=final_profile, control_postimages=True,
                projection_published=document.get("publish_local_config", False),
                expected_evaluation_current=final_evaluation_current,
            )
            marker = durable_marker(result_directory, "switch-invocation.json", {
                "plan_digest": artifact.digest,
                "switch_invocation_started": True,
                "written_at": _now(),
            })
            result["switch_invocation_started"] = True
            result["switch_marker"] = str(marker)
            result["terminal_state"] = "POST_INVOCATION_FAILURE"
            result["events"].append({"event": "privilege-marker-written", "transition": "system-switch"})
            switch_command = [
                document["tool"]["external"]["sudo"]["path"],
                "HOMEBREW_NO_AUTO_UPDATE=1",
                document["tool"]["external"]["darwin-rebuild"]["path"],
                "switch", "--flake",
                f"{document['selected_flake']['path']}#{document['selected_flake']['host']}",
                "--no-update-lock-file", "--no-write-lock-file", *document["overrides"],
            ]
            result["events"].append({"event": "privileged-command-invoked", "transition": "system-switch"})
            switched = workflow.command("single reviewed Darwin switch", switch_command, Path(document["selected_flake"]["path"]))
            result["switch"] = asdict(workflow.records[-1])
            if switched.returncode:
                result["failure"] = f"switch exited {switched.returncode}; no restoration or second system mutation attempted"
                _add_post_invocation_disposition(result, document)
                return _finish_result(result_directory, result, workflow, participant_results, 1)
            for transaction in document["control_transactions"]:
                verify_control_postimage(transaction)
            _revalidate_plan(
                workflow, document, control_postimages=True,
                expected_evaluation_current=final_evaluation_current, validate_profile=False,
            )
            _verify_post_switch(document)
            post_switch_reconcile_context = Path(_fresh_participant_context(
                document, state_root, "post-switch-reconcile", control_postimages=True,
                projection_published=document.get("publish_local_config", False),
            )["path"])
            post_switch_reconcile, blockers = _safe_participant_phase(
                document["participants"], "post-switch-reconcile",
                post_switch_reconcile_context, workflow.runner
            )
            participant_results.extend(post_switch_reconcile)
            if blockers:
                raise ConfigError(blockers[0])
            verify, blockers = _safe_participant_phase(
                document["participants"], "post-switch-verify",
                Path(_fresh_participant_context(
                    document, state_root, "post-switch-verify", control_postimages=True,
                    projection_published=document.get("publish_local_config", False),
                )["path"]), workflow.runner
            )
            participant_results.extend(verify)
            if blockers:
                raise ConfigError(blockers[0])
            _revalidate_plan(
                workflow, document, control_postimages=True,
                expected_evaluation_current=final_evaluation_current, validate_profile=False,
            )
            _verify_post_switch(document)
            result["terminal_state"] = "SUCCESS"
            return _finish_result(result_directory, result, workflow, participant_results, 0)
        except (ConfigError, OSError, ValueError, KeyError, TypeError) as error:
            result["failure"] = str(error)
            if not result["switch_invocation_started"]:
                restoration_failures: list[str] = []
                if participant_pre_switch_indeterminate:
                    restoration_failures.append("participant pre-switch mutation state is indeterminate")
                try:
                    _participant_rollbacks(
                        document["participants"], participant_results,
                        Path(_fresh_participant_context(document, state_root, "rollback")["path"]), workflow.runner, participant_results,
                    )
                except (ConfigError, OSError, ValueError, KeyError, TypeError) as rollback_error:
                    restoration_failures.append(f"participant rollback: {rollback_error}")
                for transaction in reversed(restored):
                    try:
                        restore_control_transaction(transaction, result_directory)
                        result["events"].append({"event": "control-preimage-restored", "path": transaction["path"]})
                    except (ConfigError, OSError, ValueError, KeyError, TypeError) as restore_error:
                        restoration_failures.append(f"control restoration: {restore_error}")
                if projection_restore is not None:
                    try:
                        restore_current(*projection_restore)
                        result["events"].append({"event": "local-config-current-restored"})
                    except (ConfigError, OSError, ValueError, KeyError, TypeError) as restore_error:
                        restoration_failures.append(f"projection restoration: {restore_error}")
                if profile_restore is not None:
                    try:
                        _restore_profile_selection(workflow, document, profile_restore)
                        result["events"].append({"event": "selected-profile-restored"})
                    except (ConfigError, OSError, ValueError, KeyError, TypeError) as restore_error:
                        restoration_failures.append(f"profile restoration: {restore_error}")
                if restoration_failures:
                    result["terminal_state"] = "FAILED_PRE_SWITCH_RESTORE_INCOMPLETE"
                    result["restoration_failures"] = restoration_failures
            else:
                if (
                    post_switch_reconcile_context is not None
                    and not post_switch_rollback_attempted
                    and any(
                        "post-switch-reconcile" in item["phases"] and item.get("mutation_authority")
                        for item in document["participants"]
                    )
                ):
                    post_switch_rollback_attempted = True
                    indeterminate = _participant_phase_indeterminate(
                        document["participants"], "post-switch-reconcile", post_switch_reconcile
                    )
                    try:
                        rollback_failures = _participant_rollbacks(
                            document["participants"], post_switch_reconcile,
                            post_switch_reconcile_context, workflow.runner, participant_results,
                            phases=frozenset({"post-switch-reconcile"}), continue_on_failure=True,
                        )
                    except (ConfigError, OSError, ValueError, KeyError, TypeError) as rollback_error:
                        rollback_failures = [f"participant rollback: {rollback_error}"]
                    if indeterminate:
                        result["terminal_state"] = "POST_SWITCH_PARTICIPANT_INDETERMINATE"
                        result["participant_restoration"] = "indeterminate"
                    elif rollback_failures:
                        result["terminal_state"] = "POST_SWITCH_RESTORE_INCOMPLETE"
                        result["participant_restoration"] = "incomplete"
                    else:
                        result["terminal_state"] = "POST_SWITCH_RESTORED"
                        result["participant_restoration"] = "restored"
                    if rollback_failures:
                        result["participant_restoration_failures"] = rollback_failures
                _add_post_invocation_disposition(result, document)
            return _finish_result(result_directory, result, workflow, participant_results, 1)
        finally:
            try:
                mutation_lock.__exit__(None, None, None)
            except (OSError, ConfigError) as lock_error:
                result["terminal_state"] = "LOCK_RELEASE_FAILURE"
                result["failure"] = f"transaction lock release failed: {type(lock_error).__name__}"
                _finish_result(result_directory, result, workflow, participant_results, 1)
                raise ConfigError("transaction lock release failed after terminal result recording") from lock_error


def build_only(
    workflow: Workflow,
    selected: Path,
    host: str,
    control_dir: Path,
    overrides: Sequence[str],
    test_jobs: str = "",
    build_jobs: str = "",
    source_revisions: Mapping[str, str] | None = None,
) -> int:
    from .command_diagnostics import failure_explanation
    from .config import ControlConfig, EffectiveSourceRevisions, load_control_config, resolve_effective_source_revisions
    from .local_source import match_selected_source, project_candidate_lock
    from .local_source_binding import capture_local_sources, revalidate_local_sources

    if isinstance(source_revisions, EffectiveSourceRevisions):
        effective = source_revisions
    else:
        cfg_path = control_dir / "nix-darwin.toml"
        if cfg_path.is_file():
            cfg = load_control_config(cfg_path)
        else:
            cfg = ControlConfig(sources=getattr(workflow, "sources", ()))
        effective = resolve_effective_source_revisions(cfg, source_revisions or {})

    state_root = _private_state_root()
    with DeploymentLock(state_root / "transaction.lock"):
        authority = local_git_identity(workflow, selected, False)
        repo_root = Path(authority["repository_root"]) if "repository_root" in authority else None
        selected_entry = match_selected_source(getattr(workflow, "sources", ()), selected, repo_root)

        if effective.active:
            override_count = effective.cli_override_count
            print(
                f"nix-update: source snapshot: {len(effective)} configured exact revisions "
                f"({override_count} CLI override{'' if override_count == 1 else 's'})",
                flush=True,
            )
            if selected_entry is None:
                raise ConfigError(f"selected flake {selected} does not match any configured source checkout in snapshot mode")
            selected_key = selected_entry.key
            expected_rev = effective.get(selected_key)
            if expected_rev is None:
                raise ConfigError(f"selected source {selected_key!r} has no configured snapshot revision")
            observed_rev = authority.get("commit")
            if observed_rev != expected_rev:
                raise ConfigError(
                    f"selected source {selected_key!r} commit {observed_rev} does not match expected snapshot revision {expected_rev}"
                )
            lock_backed_revisions = {k: v for k, v in effective.items() if k != selected_key}
        else:
            selected_key = selected_entry.key if selected_entry else None
            if selected_key and selected_key in effective:
                expected_rev = effective[selected_key]
                observed_rev = authority.get("commit")
                if observed_rev != expected_rev:
                    raise ConfigError(
                        f"selected source {selected_key!r} commit {observed_rev} does not match expected revision {expected_rev}"
                    )
                lock_backed_revisions = {k: v for k, v in effective.items() if k != selected_key}
            else:
                lock_backed_revisions = dict(effective)

        scratch_parent = Path(tempfile.mkdtemp(prefix="nix-update-build-", dir=state_root))
        scratch_target = scratch_parent / "target"
        if ((selected / ".git").exists() or (selected / ".git").is_file()) and "repository_root" in authority:
            _materialize_git_tree(workflow, selected, authority, scratch_target)
        else:
            shutil.copytree(selected, scratch_target, ignore=shutil.ignore_patterns(".git", ".local", "nix-update-*"))
        if lock_backed_revisions:
            project_candidate_lock(workflow, scratch_target, lock_backed_revisions)
        local_lock = normalized_lock(scratch_target / "flake.lock") if getattr(workflow, "sources", ()) else {}
        local_sources = capture_local_sources(workflow, local_lock)
        allowlist = evaluate_allowlist(workflow, scratch_target, overrides)
        projection = render_scratch(control_dir, scratch_parent / "localConfig", allowlist)
        exact = [*overrides, "--override-input", "localConfig", f"path:{projection.root}"]
        qualification_root = scratch_parent / "qualification-reports"
        create_private_directory(qualification_root, "qualification report root")
        tests = _qualification_command(scratch_target, qualification_root, test_jobs, exact)
        test_status = workflow.command("selected-flake report qualification", tests, scratch_target).returncode
        status = 1 if test_status else 0
        if test_status:
            print(failure_explanation(workflow, "selected-flake report qualification", test_status,
                                      report_root=qualification_root), file=os.sys.stderr, flush=True)
        if not test_status:
            exact_check = [
                "nix", "flake", "check", "path:.", "--no-update-lock-file", "--no-write-lock-file", *exact,
            ]
            # The identical fallback already ran in this selected directory.
            # A report hook does not establish equivalent projected checks.
            if tests != exact_check:
                check_status = workflow.command("exact projected flake check", exact_check, scratch_target).returncode
                status = 1 if check_status else 0
                if check_status:
                    print(failure_explanation(workflow, "exact projected flake check", check_status),
                          file=os.sys.stderr, flush=True)
        if status == 0:
            command = [
                "nix", "build", "--no-link", "--no-update-lock-file", "--no-write-lock-file",
                f"path:.#darwinConfigurations.{host}.system", *exact,
            ]
            if build_jobs:
                command.extend(["--max-jobs", build_jobs])
            status = workflow.command("locked Darwin build", command, scratch_target).returncode
            if status:
                print(failure_explanation(workflow, "locked Darwin build", status), file=os.sys.stderr, flush=True)
        current_authority = local_git_identity(workflow, selected, False)
        if current_authority.get("commit") != authority.get("commit"):
            raise ConfigError("complete Git source authority drifted during local build")
        revalidate_local_sources(workflow, local_lock, local_sources)
        return status


def _qualification_command(
    selected: Path, report_root: Path, test_jobs: str = "", flake_options: Sequence[str] = ()
) -> list[str]:
    test_entrypoint = selected / "bin/nix-test"
    if test_entrypoint.exists() or test_entrypoint.is_symlink():
        if test_entrypoint.is_symlink() or not test_entrypoint.is_file() or not os.access(test_entrypoint, os.X_OK):
            raise ConfigError("selected source-owned bin/nix-test is not an exact executable")
        require_private_directory(report_root, "qualification report root")
        command = [str(test_entrypoint), "--report", "--report-root", str(report_root)]
        if test_jobs:
            command.extend(["--jobs", test_jobs])
        for option in flake_options:
            command.extend(["--flake-option", option])
        return command
    return [
        "nix", "flake", "check", "path:.", "--no-update-lock-file", "--no-write-lock-file",
        *flake_options,
    ]


def maintain_lock(
    workflow: Workflow,
    selected: Path,
    mode: str,
    inputs: Sequence[str],
    allowed_nodes: Sequence[str],
    output: Path,
    install: bool,
) -> int:
    if mode == "targeted" and not inputs:
        raise ConfigError("targeted lock update requires at least one --input")
    if mode != "targeted" and inputs:
        raise ConfigError("--input is valid only for targeted lock update")
    if mode not in {"locked", "targeted", "all"}:
        raise ConfigError("lock mode must be locked, targeted, or all")
    _validate_lock_output(output, selected)
    state_root = _private_state_root()
    with DeploymentLock(state_root / "transaction.lock"):
        _require_repository_directory(selected)
        recovered_authority = (
            _recover_lock_install(output, selected, None)
            if install and (output.exists() or output.is_symlink()) else None
        )
        authority = local_git_identity(workflow, selected)
        if recovered_authority is not None:
            if recovered_authority != authority:
                raise ConfigError("recovered lock installation source authority no longer matches Git")
            raise ConfigError("recovered an interrupted lock installation; use a new evidence directory")
        try:
            candidate = prepare_lock_candidate(
                workflow, selected, mode, inputs, allowed_nodes, output, authority=authority
            )
        except LockCommandFailure as error:
            return error.status
        if install:
            if local_git_identity(workflow, selected) != authority:
                raise ConfigError("complete Git source authority drifted during isolated lock maintenance")
            install_retained_lock(selected, candidate)
        return 0


def prepare_lock_candidate(
    workflow: Workflow,
    selected: Path,
    mode: str,
    inputs: Sequence[str],
    allowed_nodes: Sequence[str],
    output: Path,
    *,
    authority: Mapping[str, Any] | None = None,
) -> LockCandidate:
    """Prepare and retain one isolated lock candidate without installing it."""
    if mode == "targeted" and not inputs:
        raise ConfigError("targeted lock update requires at least one --input")
    if mode != "targeted" and inputs:
        raise ConfigError("--input is valid only for targeted lock update")
    if mode not in {"locked", "targeted", "all"}:
        raise ConfigError("lock mode must be locked, targeted, or all")
    return _prepare_lock_candidate(
        workflow,
        selected,
        mode,
        inputs,
        allowed_nodes,
        output,
        authority=authority,
        enforce_exact_authorization=True,
    )


def _migrate_core_declaration_bytes(preimage_bytes: bytes) -> bytes:
    from .promotion_core import (
        CORE_REPOSITORY_TRANSITION_PREIMAGE_URL,
        CORE_REPOSITORY_TRANSITION_CANDIDATE_URL,
    )
    preimage_text = preimage_bytes.decode("utf-8")
    pattern = re.compile(
        r'(\bcore\.url\s*=\s*")((?:git\+)?' + re.escape(CORE_REPOSITORY_TRANSITION_PREIMAGE_URL) + r')(";)'
    )
    matches = list(pattern.finditer(preimage_text))
    if len(matches) != 1:
        raise ConfigError(
            "core repository transition requires exactly one historical core declaration in flake.nix"
        )
    m = matches[0]
    has_git_plus = m.group(2).startswith("git+")
    new_url = ("git+" if has_git_plus else "") + CORE_REPOSITORY_TRANSITION_CANDIDATE_URL
    candidate_text = preimage_text[:m.start(2)] + new_url + preimage_text[m.end(2):]
    return candidate_text.encode("utf-8")


def _migrate_composition_declaration_bytes(preimage_bytes: bytes) -> bytes:
    from .promotion_composition import (
        COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_URL,
        COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL,
    )
    preimage_text = preimage_bytes.decode("utf-8")
    pattern = re.compile(
        r'(\b(?:inputs\.)?composition-nd\.url\s*=\s*")((?:git\+)?'
        + re.escape(COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_URL)
        + r'(?:\?ref=main)?)((";))'
    )
    matches = list(pattern.finditer(preimage_text))
    if len(matches) != 1:
        raise ConfigError(
            "composition repository transition requires exactly one historical composition-nd declaration in flake.nix"
        )
    m = matches[0]
    matched_url = m.group(2)
    has_git_plus = matched_url.startswith("git+")
    has_ref_main = matched_url.endswith("?ref=main")
    new_url = (
        ("git+" if has_git_plus else "")
        + COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL
        + ("?ref=main" if has_ref_main else "")
    )
    candidate_text = preimage_text[:m.start(2)] + new_url + preimage_text[m.end(2):]
    return candidate_text.encode("utf-8")


def prepare_promotion_lock_candidate(
    workflow: Workflow,
    selected: Path,
    input_name: str,
    repository_url: str,
    revision: str,
    output: Path,
    *,
    authority: Mapping[str, Any] | None = None,
    allow_file: bool = False,
    expected_ref: str = "refs/heads/main",
    allow_implicit_main: bool = True,
    local_source: Mapping[str, Any] | None = None,
    allow_core_repository_transition: bool = False,
    allow_composition_repository_transition: bool = False,
) -> LockCandidate:
    """Generate and authorize one promotion candidate from its selected graph."""
    from .promotion_binding import (
        authorize_transition, authorization_kind, validate_authorization,
    )
    from .promotion_core import CORE_REPOSITORY_TRANSITION_PREIMAGE_URL
    from .promotion_composition import COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_URL

    candidate: LockCandidate | None = None
    try:
        candidate = _prepare_lock_candidate(
            workflow,
            selected,
            "targeted",
            [input_name],
            [],
            output,
            authority=authority,
            enforce_exact_authorization=False,
            local_source=local_source,
            allow_core_repository_transition=allow_core_repository_transition,
            allow_composition_repository_transition=allow_composition_repository_transition,
        )
        before_root = candidate.preimage["root"]
        before_node = _resolve_lock_input(
            candidate.preimage["nodes"],
            before_root,
            before_root,
            input_name.split("/"),
            set(),
        )
        after_root = candidate.candidate["root"]
        after_node = _resolve_lock_input(
            candidate.candidate["nodes"],
            after_root,
            after_root,
            input_name.split("/"),
            set(),
        )
        if before_node != after_node:
            raise ConfigError("promotion selected root node name changed")
        if allow_core_repository_transition:
            expected_auth_url = CORE_REPOSITORY_TRANSITION_PREIMAGE_URL
        elif allow_composition_repository_transition:
            expected_auth_url = COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_URL
        else:
            expected_auth_url = repository_url
        authorization = authorize_transition(
            candidate.preimage,
            candidate.candidate,
            after_node,
            expected_auth_url,
            revision,
            input_name=input_name,
            allow_file=allow_file,
            expected_ref=expected_ref,
            allow_implicit_main=allow_implicit_main,
            allow_repository_transition=allow_core_repository_transition,
            allow_composition_repository_transition=allow_composition_repository_transition,
        )
        evidence = {
            "schema": "nix-update-lock-delta-v2",
            "mode": "targeted",
            "requested_inputs": [input_name],
            "authorization": {
                "kind": authorization_kind(authorization),
                "record": validate_authorization(authorization),
            },
            "changes": candidate.changes,
            "source_authority": candidate.authority,
            "preimage_sha256": sha256_bytes(candidate.preimage_bytes),
            "candidate_sha256": sha256_bytes(candidate.candidate_bytes),
        }
        if allow_core_repository_transition:
            evidence["core_repository_transition"] = True
            evidence["preimage_flake_nix_sha256"] = sha256_bytes(candidate.preimage_flake_nix_bytes)
            evidence["candidate_flake_nix_sha256"] = sha256_bytes(candidate.candidate_flake_nix_bytes)
        elif allow_composition_repository_transition:
            evidence["composition_repository_transition"] = True
            evidence["preimage_flake_nix_sha256"] = sha256_bytes(candidate.preimage_flake_nix_bytes)
            evidence["candidate_flake_nix_sha256"] = sha256_bytes(candidate.candidate_flake_nix_bytes)
        atomic_private_write(output / "delta.json", canonical_bytes(evidence))
        _fsync(output)
        return LockCandidate(
            candidate.output,
            candidate.authority,
            candidate.preimage_bytes,
            candidate.preimage,
            candidate.candidate_bytes,
            candidate.candidate,
            candidate.changes,
            tuple(authorization["candidateOwnedNodes"]),
            authorization,
            preimage_flake_nix_bytes=candidate.preimage_flake_nix_bytes,
            candidate_flake_nix_bytes=candidate.candidate_flake_nix_bytes,
        )
    except (ConfigError, OSError, ValueError, KeyError, TypeError):
        if output.exists() and output.is_dir():
            shutil.rmtree(output)
        raise


def _prepare_lock_candidate(
    workflow: Workflow,
    selected: Path,
    mode: str,
    inputs: Sequence[str],
    allowed_nodes: Sequence[str],
    output: Path,
    *,
    authority: Mapping[str, Any] | None,
    enforce_exact_authorization: bool,
    local_source: Mapping[str, Any] | None = None,
    allow_core_repository_transition: bool = False,
    allow_composition_repository_transition: bool = False,
) -> LockCandidate:
    _require_repository_directory(selected)
    source_authority = dict(authority or local_git_identity(workflow, selected))
    _validate_lock_output(
        output, selected, Path(str(source_authority["repository_root"])), require_empty=True
    )
    _require_isolated_flake_files(selected)
    real_lock = selected / "flake.lock"
    try:
        before_bytes = real_lock.read_bytes()
    except OSError as error:
        raise ConfigError("unable to read reviewed flake.lock preimage") from error
    before = normalized_lock(real_lock)
    if real_lock.read_bytes() != before_bytes:
        raise ConfigError("flake.lock changed while its preimage was normalized")
    authorized = set(allowed_nodes)
    if mode == "targeted":
        nodes = before["nodes"]
        root_node = before["root"]
        authorized.update(
            _resolve_lock_input(nodes, root_node, root_node, input_name.split("/"), set())
            for input_name in inputs
        )
    temporary_root = Path(tempfile.mkdtemp(prefix="nix-update-lock-", dir=_private_state_root()))
    try:
        return _prepare_lock_candidate_in_tree(
            workflow,
            selected,
            mode,
            inputs,
            output,
            source_authority,
            before_bytes,
            before,
            authorized,
            temporary_root / "candidate",
            enforce_exact_authorization=enforce_exact_authorization,
            local_source=local_source,
            allow_core_repository_transition=allow_core_repository_transition,
            allow_composition_repository_transition=allow_composition_repository_transition,
        )
    finally:
        shutil.rmtree(temporary_root)


def _prepare_lock_candidate_in_tree(
    workflow: Workflow,
    selected: Path,
    mode: str,
    inputs: Sequence[str],
    output: Path,
    source_authority: Mapping[str, Any],
    before_bytes: bytes,
    before: Mapping[str, Any],
    authorized: set[str],
    worktree: Path,
    *,
    enforce_exact_authorization: bool,
    local_source: Mapping[str, Any] | None = None,
    allow_core_repository_transition: bool = False,
    allow_composition_repository_transition: bool = False,
) -> LockCandidate:
    _materialize_git_tree(workflow, selected, source_authority, worktree)
    _require_isolated_flake_files(worktree)
    preimage_flake_nix_bytes = (worktree / "flake.nix").read_bytes()
    candidate_flake_nix_bytes = preimage_flake_nix_bytes
    if allow_core_repository_transition:
        candidate_flake_nix_bytes = _migrate_core_declaration_bytes(preimage_flake_nix_bytes)
        (worktree / "flake.nix").write_bytes(candidate_flake_nix_bytes)
    elif allow_composition_repository_transition:
        candidate_flake_nix_bytes = _migrate_composition_declaration_bytes(preimage_flake_nix_bytes)
        (worktree / "flake.nix").write_bytes(candidate_flake_nix_bytes)
    if mode == "locked":
        command = ["nix", "flake", "check", "path:.", "--no-update-lock-file", "--no-write-lock-file"]
    elif local_source is not None:
        if mode != "targeted" or len(inputs) != 1:
            raise ConfigError("local source realization requires exactly one targeted input")
        root = before["root"]
        node = _resolve_lock_input(before["nodes"], root, root, inputs[0].split("/"), set())
        selector = before["nodes"][node]["locked"].get("dir", "")
        store = Path(local_source["path"])
        if not str(store).startswith("/nix/store/") or not store.is_dir():
            raise ConfigError("local source realization must be an immutable Nix store path")
        command = ["nix", "flake", "lock", "--offline", "--override-input", inputs[0], f"path:{store / selector}"]
    elif mode == "targeted":
        command = ["nix", "flake", "update", *inputs]
    else:
        command = ["nix", "flake", "update"]
    result = workflow.command(f"isolated {mode} lock maintenance", command, worktree)
    if result.returncode:
        raise LockCommandFailure(result.returncode)
    _require_isolated_flake_files(worktree)
    if (worktree / "flake.nix").read_bytes() != candidate_flake_nix_bytes:
        raise ConfigError("isolated lock maintenance changed reviewed flake.nix bytes")
    candidate_path = worktree / "flake.lock"
    if local_source is not None:
        # Nix merges the selected commit's child lock graph. Only its temporary
        # source locator is replaced; immutable content fields come from Nix.
        generated = read_lock_document(candidate_path)
        if not isinstance(generated.get("nodes"), Mapping):
            raise ConfigError("flake.lock must contain a nodes mapping")
        generated_root = generated.get("root")
        if not isinstance(generated_root, str) or not generated_root:
            raise ConfigError("flake.lock must contain a root node reference")
        generated_node = _resolve_lock_input(generated["nodes"], generated_root, generated_root, inputs[0].split("/"), set())
        if generated_node != node:
            raise ConfigError("local source lock selected node identity changed")
        original_node = before["nodes"][node]
        locked = dict(original_node["locked"])
        for field in ("rev", "narHash", "lastModified", "revCount"):
            locked.pop(field, None)
            if field in local_source["locked"]:
                locked[field] = local_source["locked"][field]
        if allow_core_repository_transition:
            from .promotion_core import CORE_REPOSITORY_TRANSITION_CANDIDATE_URL

            candidate_url = CORE_REPOSITORY_TRANSITION_CANDIDATE_URL
            original_declaration = dict(original_node["original"])
            original_declaration["url"] = candidate_url
            locked["url"] = candidate_url
            generated["nodes"][node]["original"] = original_declaration
            generated["nodes"][node]["locked"] = locked
        elif allow_composition_repository_transition:
            from .promotion_composition import COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL

            candidate_url = COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL
            original_declaration = dict(original_node["original"])
            original_declaration["url"] = candidate_url
            locked["url"] = candidate_url
            generated["nodes"][node]["original"] = original_declaration
            generated["nodes"][node]["locked"] = locked
        else:
            generated["nodes"][node]["original"] = original_node["original"]
            generated["nodes"][node]["locked"] = locked
        candidate_path.write_bytes(serialize_lock_document(generated))
    candidate_bytes = candidate_path.read_bytes()
    after = normalized_lock(candidate_path)
    if candidate_path.read_bytes() != candidate_bytes:
        raise ConfigError("candidate flake.lock changed while it was normalized")
    changes = lock_delta(before, after)
    if output.exists() or output.is_symlink():
        require_private_directory(output, "lock evidence directory", empty=True)
    else:
        create_private_directory(output, "lock evidence directory")
    _write_private_file(output / "flake.lock.candidate", candidate_bytes)
    _write_private_file(output / "flake.lock.preimage", before_bytes)
    if allow_core_repository_transition or allow_composition_repository_transition:
        _write_private_file(output / "flake.nix.candidate", candidate_flake_nix_bytes)
        _write_private_file(output / "flake.nix.preimage", preimage_flake_nix_bytes)
    _write_private_file(
        output / "delta.json",
        canonical_bytes({
            "mode": mode, "requested_inputs": list(inputs), "authorized_nodes": sorted(authorized),
            "changes": changes, "source_authority": source_authority,
            "preimage_sha256": sha256_bytes(before_bytes),
            "candidate_sha256": sha256_bytes(candidate_bytes),
        }),
    )
    _fsync(output)
    if enforce_exact_authorization and mode != "all":
        reject_unapproved_lock_delta(changes, authorized)
    return LockCandidate(
        output, dict(source_authority), before_bytes, dict(before), candidate_bytes, after,
        changes, tuple(sorted(authorized)),
        preimage_flake_nix_bytes=preimage_flake_nix_bytes if (allow_core_repository_transition or allow_composition_repository_transition) else None,
        candidate_flake_nix_bytes=candidate_flake_nix_bytes if (allow_core_repository_transition or allow_composition_repository_transition) else None,
    )


def load_retained_lock_candidate(
    output: Path,
    authority: Mapping[str, Any],
    expected_preimage_sha256: str,
    expected_candidate_sha256: str,
    *,
    expected_closure_authorization: Mapping[str, Any] | None = None,
    expected_evidence_sha256: str | None = None,
) -> LockCandidate:
    """Load an exact retained candidate and its authorization evidence."""
    require_private_directory(output, "lock evidence directory")
    preimage_path = output / "flake.lock.preimage"
    candidate_path = output / "flake.lock.candidate"
    delta_path = output / "delta.json"
    for path, label in (
        (preimage_path, "lock preimage evidence"),
        (candidate_path, "lock candidate evidence"),
        (delta_path, "lock delta evidence"),
    ):
        require_private_file(path, label)
    preimage_bytes = preimage_path.read_bytes()
    candidate_bytes = candidate_path.read_bytes()
    if (
        sha256_bytes(preimage_bytes) != expected_preimage_sha256
        or sha256_bytes(candidate_bytes) != expected_candidate_sha256
    ):
        raise ConfigError("retained lock candidate bytes do not match the reviewed plan")
    delta_bytes = delta_path.read_bytes()
    if expected_evidence_sha256 is not None and sha256_bytes(delta_bytes) != expected_evidence_sha256:
        raise ConfigError("retained lock authorization evidence digest drifted")
    try:
        delta = json.loads(delta_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConfigError("retained lock delta evidence is invalid") from error
    if canonical_bytes(delta) != delta_bytes:
        raise ConfigError("retained lock delta evidence bytes are not canonical")
    if not isinstance(delta, dict) or delta.get("source_authority") != dict(authority):
        raise ConfigError("retained lock candidate source authority drifted")
    if delta.get("preimage_sha256") != expected_preimage_sha256 or delta.get("candidate_sha256") != expected_candidate_sha256:
        raise ConfigError("retained lock delta digests drifted")
    is_core_repo_transition = delta.get("core_repository_transition") is True
    is_comp_repo_transition = delta.get("composition_repository_transition") is True
    is_repo_transition = is_core_repo_transition or is_comp_repo_transition
    preimage_flake_nix_bytes: bytes | None = None
    candidate_flake_nix_bytes: bytes | None = None
    if is_repo_transition:
        preimage_fn = output / "flake.nix.preimage"
        candidate_fn = output / "flake.nix.candidate"
        require_private_file(preimage_fn, "declaration preimage evidence")
        require_private_file(candidate_fn, "declaration candidate evidence")
        preimage_flake_nix_bytes = preimage_fn.read_bytes()
        candidate_flake_nix_bytes = candidate_fn.read_bytes()
        if (
            sha256_bytes(preimage_flake_nix_bytes) != delta.get("preimage_flake_nix_sha256")
            or sha256_bytes(candidate_flake_nix_bytes) != delta.get("candidate_flake_nix_sha256")
        ):
            raise ConfigError("retained declaration candidate bytes do not match authorization evidence")
    closure_authorization: dict[str, Any] | None = None
    if expected_closure_authorization is not None:
        from .promotion_binding import authorization_kind, validate_authorization

        expected_keys = {
            "schema",
            "mode",
            "requested_inputs",
            "authorization",
            "changes",
            "source_authority",
            "preimage_sha256",
            "candidate_sha256",
        }
        if is_core_repo_transition:
            expected_keys.update({
                "core_repository_transition",
                "preimage_flake_nix_sha256",
                "candidate_flake_nix_sha256",
            })
        elif is_comp_repo_transition:
            expected_keys.update({
                "composition_repository_transition",
                "preimage_flake_nix_sha256",
                "candidate_flake_nix_sha256",
            })
        if set(delta) != expected_keys or delta.get("schema") != "nix-update-lock-delta-v2" or delta.get("mode") != "targeted":
            raise ConfigError("retained promotion lock evidence fields are invalid")
        authorization = delta.get("authorization")
        if (
            not isinstance(authorization, dict)
            or set(authorization) != {"kind", "record"}
            or authorization.get("kind") != authorization_kind(expected_closure_authorization)
        ):
            raise ConfigError("retained promotion lock authorization kind is invalid")
        closure_authorization = validate_authorization(
            authorization.get("record")
        )
        if closure_authorization != dict(expected_closure_authorization):
            raise ConfigError("retained promotion owned-closure authorization drifted")
        authorized_nodes = tuple(closure_authorization["candidateOwnedNodes"])
    else:
        authorized_nodes = tuple(delta.get("authorized_nodes", []))
    return LockCandidate(
        output,
        dict(authority),
        preimage_bytes,
        normalized_lock(preimage_path),
        candidate_bytes,
        normalized_lock(candidate_path),
        list(delta.get("changes", [])),
        authorized_nodes,
        closure_authorization,
        preimage_flake_nix_bytes=preimage_flake_nix_bytes,
        candidate_flake_nix_bytes=candidate_flake_nix_bytes,
    )


def install_retained_lock(selected: Path, candidate: LockCandidate) -> dict[str, Any]:
    """Install exact retained candidate bytes with journaled preimage restoration."""
    _require_isolated_flake_files(selected)
    real_lock = selected / "flake.lock"
    real_flake = selected / "flake.nix"
    if real_lock.read_bytes() != candidate.preimage_bytes:
        raise ConfigError("flake.lock preimage drifted before retained candidate installation")
    has_declaration = candidate.candidate_flake_nix_bytes is not None
    if has_declaration:
        if real_flake.read_bytes() != candidate.preimage_flake_nix_bytes:
            raise ConfigError("flake.nix preimage drifted before retained candidate installation")
    journal = candidate.output / "install-transaction.json"
    preimage_metadata = _lock_metadata(real_lock)
    journal_base: dict[str, Any] = {
        "schema": "nix-update-lock-install-v1", "state": "rename-pending",
        "source_authority": candidate.authority,
        "preimage_sha256": sha256_bytes(candidate.preimage_bytes),
        "candidate_sha256": sha256_bytes(candidate.candidate_bytes),
        "selected_path": str(selected.absolute()), "preimage_metadata": preimage_metadata,
    }
    flake_metadata = None
    if has_declaration:
        flake_metadata = _lock_metadata(real_flake)
        journal_base["declaration"] = {
            "preimage_sha256": sha256_bytes(candidate.preimage_flake_nix_bytes),
            "candidate_sha256": sha256_bytes(candidate.candidate_flake_nix_bytes),
            "preimage_metadata": flake_metadata,
        }
    atomic_private_write(journal, canonical_bytes(journal_base))
    temporary = selected / f".flake.lock.nix-update.{os.getpid()}.tmp"
    temporary_flake = selected / f".flake.nix.nix-update.{os.getpid()}.tmp" if has_declaration else None
    flake_replaced = False
    try:
        if has_declaration and temporary_flake is not None and flake_metadata is not None:
            _write_exclusive_file(temporary_flake, candidate.candidate_flake_nix_bytes, flake_metadata["mode"])
            os.chown(temporary_flake, flake_metadata["uid"], flake_metadata["gid"])
            os.chmod(temporary_flake, flake_metadata["mode"])
            os.replace(temporary_flake, real_flake)
            flake_replaced = True
            _fsync(selected)
            _verify_installed_lock(real_flake, candidate.candidate_flake_nix_bytes, flake_metadata)

        _write_exclusive_file(temporary, candidate.candidate_bytes, preimage_metadata["mode"])
        os.chown(temporary, preimage_metadata["uid"], preimage_metadata["gid"])
        os.chmod(temporary, preimage_metadata["mode"])
        os.replace(temporary, real_lock)
        _fsync(selected)
        _verify_installed_lock(real_lock, candidate.candidate_bytes, preimage_metadata)
    except (OSError, ConfigError) as install_error:
        try:
            temporary.unlink(missing_ok=True)
            if temporary_flake is not None:
                temporary_flake.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            if flake_replaced and flake_metadata is not None:
                _restore_lock_preimage(real_flake, candidate.preimage_flake_nix_bytes, flake_metadata)
            _restore_lock_preimage(real_lock, candidate.preimage_bytes, preimage_metadata)
        except (OSError, ConfigError) as rollback_error:
            _record_lock_terminal(journal, journal_base, "rollback-failed", rollback_error)
            raise ConfigError("lock installation failed and exact preimage restoration failed") from install_error
        _record_lock_terminal(journal, journal_base, "preimage-restored", install_error)
        raise
    try:
        _write_lock_journal(journal, journal_base, "installed")
    except (OSError, ConfigError) as evidence_error:
        try:
            if flake_replaced and flake_metadata is not None:
                _restore_lock_preimage(real_flake, candidate.preimage_flake_nix_bytes, flake_metadata)
            _restore_lock_preimage(real_lock, candidate.preimage_bytes, preimage_metadata)
        except (OSError, ConfigError) as rollback_error:
            _record_lock_terminal(journal, journal_base, "rollback-failed", rollback_error)
            raise ConfigError("installed lock lacked terminal evidence and rollback failed") from evidence_error
        _record_lock_terminal(journal, journal_base, "preimage-restored", evidence_error)
        raise ConfigError("lock installation was rolled back because terminal evidence failed") from evidence_error
    result_metadata = dict(preimage_metadata)
    if has_declaration and flake_metadata is not None:
        result_metadata["declaration"] = {"preimage_metadata": flake_metadata}
    return result_metadata


def _validate_lock_output(
    output: Path,
    selected: Path,
    repository_root: Path | None = None,
    *,
    require_empty: bool = False,
) -> None:
    if not output.is_absolute():
        raise ConfigError("lock evidence output must be an absolute path")
    resolved = output.resolve(strict=False)
    roots = [selected.resolve(strict=True)]
    if repository_root is not None:
        roots.append(repository_root.resolve(strict=True))
    for root in roots:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        raise ConfigError("lock evidence output must be outside the reviewed Git repository")
    if output.exists() or output.is_symlink():
        require_private_directory(
            output, "lock evidence directory", empty=require_empty
        )
    else:
        validate_ancestor_chain(output.parent, owner_only=True)


def _materialize_git_tree(
    workflow: Workflow, selected: Path, authority: Mapping[str, Any], destination: Path
) -> None:
    repository_root = Path(str(authority["repository_root"]))
    try:
        relative = selected.resolve().relative_to(repository_root.resolve())
    except ValueError as error:
        raise ConfigError("selected flake is outside the reviewed Git repository") from error
    archive = destination.parent / "reviewed-tree.tar"
    result = workflow.command(
        "Git reviewed tree archive",
        ["git", "archive", "--format=tar", f"--output={archive}", str(authority["commit"])],
        repository_root,
    )
    if result.returncode:
        raise ConfigError("unable to materialize the reviewed Git tree")
    archive_metadata = archive.lstat()
    if (
        not stat.S_ISREG(archive_metadata.st_mode)
        or archive_metadata.st_uid != os.getuid()
        or archive_metadata.st_nlink != 1
    ):
        raise ConfigError("Git archive is not a current-user single-link regular file")
    os.chmod(archive, 0o600)
    require_private_file(archive, "reviewed Git archive")
    extracted = destination.parent / "reviewed-tree"
    extracted.mkdir(mode=0o700)
    with tarfile.open(archive, "r") as bundle:
        for member in bundle.getmembers():
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise ConfigError("Git archive contains an unsafe path")
        bundle.extractall(extracted, filter="data")
    source = extracted / relative
    if not source.is_dir():
        raise ConfigError("reviewed Git tree does not contain the selected flake")
    os.replace(source, destination)


def _require_repository_directory(selected: Path) -> None:
    validate_ancestor_chain(selected, owner_only=True)
    try:
        metadata = selected.lstat()
    except OSError as error:
        raise ConfigError("selected deployment repository is unavailable") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise ConfigError("selected deployment repository must be a current-user non-symlink directory")


def _require_isolated_flake_files(root: Path) -> None:
    for name in ("flake.nix", "flake.lock"):
        path = root / name
        try:
            metadata = path.lstat()
        except OSError as error:
            raise ConfigError(f"isolated lock maintenance requires {name}") from error
        if (
            not path.is_file() or path.is_symlink() or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
        ):
            raise ConfigError(f"isolated lock maintenance requires a single-link regular {name}")


def _lock_metadata(path: Path) -> dict[str, int]:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
    ):
        raise ConfigError("flake.lock must be a current-user single-link regular file")
    return {
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": stat.S_IMODE(metadata.st_mode),
        "link_count": metadata.st_nlink,
    }


def _verify_installed_lock(path: Path, content: bytes, expected: Mapping[str, Any]) -> None:
    if _lock_metadata(path) != dict(expected) or path.read_bytes() != content:
        raise ConfigError("installed flake.lock does not equal the reviewed bytes and metadata")


def _restore_lock_preimage(path: Path, content: bytes, metadata: Mapping[str, Any]) -> None:
    rollback = path.parent / f".flake.lock.nix-update.rollback.{os.getpid()}.{os.urandom(4).hex()}.tmp"
    try:
        _write_exclusive_file(rollback, content, int(metadata["mode"]))
        os.chown(rollback, int(metadata["uid"]), int(metadata["gid"]))
        os.chmod(rollback, int(metadata["mode"]))
        os.replace(rollback, path)
        _fsync(path.parent)
        _verify_installed_lock(path, content, metadata)
    finally:
        try:
            rollback.unlink(missing_ok=True)
        except OSError:
            pass


def _write_lock_journal(
    journal: Path, base: Mapping[str, Any], state: str, error: BaseException | None = None
) -> None:
    document = dict(base)
    document["state"] = state
    if error is not None:
        document["failure"] = {
            "type": type(error).__name__,
            "summary": redact_text(str(error))[:COMMAND_SUMMARY_LIMIT],
        }
    atomic_private_write(journal, canonical_bytes(document))


def _record_lock_terminal(
    journal: Path, base: Mapping[str, Any], state: str, error: BaseException | None = None
) -> None:
    try:
        _write_lock_journal(journal, base, state, error)
    except (OSError, ConfigError) as journal_error:
        fallback = journal.parent / f"install-terminal-{state}-{os.urandom(4).hex()}.json"
        document = dict(base)
        document.update({
            "state": state,
            "journal_failure": type(journal_error).__name__,
            "failure": None if error is None else {
                "type": type(error).__name__, "summary": redact_text(str(error))[:COMMAND_SUMMARY_LIMIT],
            },
        })
        exclusive_private_write(fallback, canonical_bytes(document))
        fsync_directory(fallback.parent)
        raise ConfigError(f"lock terminal journal failed; fallback recorded at {fallback.name}") from journal_error


def _recover_lock_install(
    output: Path,
    selected: Path,
    authority: Mapping[str, Any] | None,
    *,
    restore_installed: bool = False,
) -> Mapping[str, Any] | None:
    """Restore an interrupted rename-pending lock install before any new child command."""
    require_private_directory(output, "lock evidence directory")
    journal = output / "install-transaction.json"
    if not journal.exists() and not journal.is_symlink():
        return None
    require_private_file(journal, "lock installation journal")
    try:
        document = json.loads(journal.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConfigError("lock installation journal is invalid") from error
    if not isinstance(document, dict) or document.get("schema") != "nix-update-lock-install-v1":
        raise ConfigError("lock installation journal schema is invalid")
    recoverable_states = {"rename-pending", "rollback-failed"}
    if restore_installed:
        recoverable_states.add("installed")
    if document.get("state") not in recoverable_states:
        return None
    recorded_authority = document.get("source_authority")
    if not isinstance(recorded_authority, dict):
        raise ConfigError("interrupted lock installation source authority is invalid")
    if (authority is not None and recorded_authority != authority) or document.get("selected_path") != str(selected.absolute()):
        raise ConfigError("interrupted lock installation authority does not match current source")
    metadata = document.get("preimage_metadata")
    if (
        not isinstance(metadata, dict)
        or set(metadata) != {"uid", "gid", "mode", "link_count"}
        or any(type(metadata[name]) is not int for name in metadata)
    ):
        raise ConfigError("interrupted lock installation metadata is invalid")
    preimage = output / "flake.lock.preimage"
    candidate = output / "flake.lock.candidate"
    try:
        require_private_file(preimage, "lock preimage evidence")
        require_private_file(candidate, "lock candidate evidence")
        preimage_bytes = preimage.read_bytes()
        candidate_bytes = candidate.read_bytes()
    except (OSError, ConfigError) as error:
        raise ConfigError("interrupted lock installation evidence is unavailable or unsafe") from error
    if (
        sha256_bytes(preimage_bytes) != document.get("preimage_sha256")
        or sha256_bytes(candidate_bytes) != document.get("candidate_sha256")
    ):
        raise ConfigError("interrupted lock installation evidence digest is invalid")
    current = selected / "flake.lock"
    try:
        current_bytes = current.read_bytes()
    except OSError as error:
        raise ConfigError("interrupted lock installation current flake.lock is unavailable") from error
    if current_bytes == preimage_bytes:
        _verify_installed_lock(current, preimage_bytes, metadata)
    elif current_bytes == candidate_bytes:
        try:
            _restore_lock_preimage(current, preimage_bytes, metadata)
        except (OSError, ConfigError) as restore_error:
            _record_lock_terminal(journal, document, "rollback-failed", restore_error)
            raise ConfigError("interrupted lock installation preimage restoration failed") from restore_error
    else:
        raise ConfigError("interrupted lock installation has an unknown current flake.lock")

    decl_info = document.get("declaration")
    if decl_info is not None:
        decl_metadata = decl_info.get("preimage_metadata")
        if (
            not isinstance(decl_metadata, dict)
            or set(decl_metadata) != {"uid", "gid", "mode", "link_count"}
            or any(type(decl_metadata[name]) is not int for name in decl_metadata)
        ):
            raise ConfigError("interrupted lock installation declaration metadata is invalid")
        decl_preimage = output / "flake.nix.preimage"
        decl_candidate = output / "flake.nix.candidate"
        try:
            require_private_file(decl_preimage, "declaration preimage evidence")
            require_private_file(decl_candidate, "declaration candidate evidence")
            decl_preimage_bytes = decl_preimage.read_bytes()
            decl_candidate_bytes = decl_candidate.read_bytes()
        except (OSError, ConfigError) as error:
            raise ConfigError("interrupted lock installation declaration evidence is unavailable or unsafe") from error
        if (
            sha256_bytes(decl_preimage_bytes) != decl_info.get("preimage_sha256")
            or sha256_bytes(decl_candidate_bytes) != decl_info.get("candidate_sha256")
        ):
            raise ConfigError("interrupted lock installation declaration evidence digest is invalid")
        current_flake = selected / "flake.nix"
        try:
            current_flake_bytes = current_flake.read_bytes()
        except OSError as error:
            raise ConfigError("interrupted lock installation current flake.nix is unavailable") from error
        if current_flake_bytes == decl_preimage_bytes:
            _verify_installed_lock(current_flake, decl_preimage_bytes, decl_metadata)
        elif current_flake_bytes == decl_candidate_bytes:
            try:
                _restore_lock_preimage(current_flake, decl_preimage_bytes, decl_metadata)
            except (OSError, ConfigError) as restore_error:
                _record_lock_terminal(journal, document, "rollback-failed", restore_error)
                raise ConfigError("interrupted lock installation declaration preimage restoration failed") from restore_error
        else:
            raise ConfigError("interrupted lock installation has an unknown current flake.nix")

    _record_lock_terminal(journal, document, "preimage-restored")
    return recorded_authority



def evaluate_allowlist(workflow: Workflow, selected: Path, overrides: Sequence[str]) -> dict[str, list[str]]:
    label = "configAllowlist evaluation"
    command = ["nix", "eval", "--json", "path:.#configAllowlist", "--no-update-lock-file", "--no-write-lock-file", *overrides]
    result = workflow.command(label, command, selected)
    if result.returncode:
        raise ConfigError("configAllowlist evaluation failed")
    try:
        allowlist = json.loads(workflow.complete_stdout(label))
    except json.JSONDecodeError as error:
        raise ConfigError("selected flake returned invalid configAllowlist JSON") from error
    if not isinstance(allowlist, dict):
        raise ConfigError("configAllowlist must be a mapping")
    return allowlist


def closure_inventory(workflow: Workflow, selected: Path, target: str) -> list[dict[str, Any]]:
    label = "target closure inventory"
    result = workflow.command(label, ["nix", "path-info", "-r", "--json", target], selected)
    if result.returncode:
        raise ConfigError("unable to inspect target closure")
    value = json.loads(workflow.complete_stdout(label))
    if isinstance(value, dict):
        items = []
        for path, item in value.items():
            if not isinstance(item, dict):
                raise ConfigError("target closure inventory returned an invalid entry")
            items.append({**item, "path": path})
    else:
        items = value if isinstance(value, list) else [value]
    normalized = []
    for item in items:
        normalized.append({key: item.get(key) for key in ("path", "narHash", "narSize")})
    return sorted(normalized, key=lambda item: str(item["path"]))


def current_live_closure(workflow: Workflow, selected: Path, profile: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not profile:
        return []
    return closure_inventory(workflow, selected, profile["live"]["target"])


def classify_closure_delta(before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]], allowed: Sequence[str]) -> dict[str, Any]:
    left = {str(item["path"]) for item in before}
    right = {str(item["path"]) for item in after}
    added = sorted(right - left)
    removed = sorted(left - right)
    approved = set(allowed)
    unapproved = [path for path in [*added, *removed] if path not in approved]
    return {"added": added, "removed": removed, "unapproved": unapproved}


def _collect_sources(workflow: Workflow, selected: Path, sources: Sequence[Mapping[str, Any]], blockers: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for source in sources:
        identity = _collect(blockers, f"source {source.get('name')}", lambda source=source: _source_identity(workflow, selected, source))
        if identity is not None:
            results.append(identity)
    return results


def _source_identity(workflow: Workflow, selected: Path, source: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(str(source.get("path", source.get("store_path", ""))))
    _require_exact_store_path(path, str(source.get("name")))
    result = workflow.command(f"source NAR {source['name']}", ["nix", "hash", "path", "--sri", str(path)], selected)
    if result.returncode or result.stdout.strip() != source["nar_hash"]:
        raise ConfigError(f"source NAR mismatch: {source['name']}")
    identity = {
        "name": source["name"],
        "input": source["input"],
        "lock_node": source["lock_node"],
        "kind": source["kind"],
        "path": str(path.resolve()),
        "nar_hash": result.stdout.strip(),
    }
    if "locator" in source:
        identity["locator"] = source["locator"]
    return identity


def _validate_source_lock_bindings(lock: Mapping[str, Any], sources: Sequence[Mapping[str, Any]]) -> None:
    if not sources:
        return
    nodes = lock.get("nodes")
    root = lock.get("root")
    if not isinstance(nodes, dict) or not isinstance(root, str) or root not in nodes:
        raise ConfigError("flake.lock root cannot bind deployment sources")
    for source in sources:
        lock_node = source["lock_node"]
        node = nodes.get(lock_node)
        if not isinstance(node, dict):
            raise ConfigError(f"source lock node is missing: {source['name']}")
        locked = node.get("locked")
        if not isinstance(locked, dict) or locked.get("narHash") != source["nar_hash"]:
            raise ConfigError(f"source NAR does not equal locked input node: {source['name']}")
        resolved = _resolve_lock_input(nodes, root, root, str(source["input"]).split("/"), set())
        if resolved != lock_node:
            raise ConfigError(f"source input does not resolve to declared lock node: {source['name']}")


def _resolve_lock_input(
    nodes: Mapping[str, Any], root: str, current: str, segments: Sequence[str],
    seen: set[tuple[str, tuple[str, ...]]],
    depth: int = 0,
) -> str:
    key = (current, tuple(segments))
    if depth > 32 or key in seen or not segments:
        raise ConfigError("flake.lock input path is cyclic or empty")
    seen.add(key)
    node = nodes.get(current)
    inputs = node.get("inputs") if isinstance(node, dict) else None
    reference = inputs.get(segments[0]) if isinstance(inputs, dict) else None
    remaining = list(segments[1:])
    if isinstance(reference, list) and all(isinstance(item, str) for item in reference):
        return _resolve_lock_input(nodes, root, root, [*reference, *remaining], seen, depth + 1)
    if not isinstance(reference, str) or reference not in nodes:
        raise ConfigError(f"flake.lock input path is unresolved: {'/'.join(segments)}")
    if not remaining:
        return reference
    return _resolve_lock_input(nodes, root, reference, remaining, seen, depth + 1)


def _require_exact_store_path(path: Path, name: str) -> None:
    store = Path("/nix/store")
    if not path.is_absolute() or path == store or store not in path.parents:
        raise ConfigError(f"exact source is not an immutable Nix store path: {name}")
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError as error:
        raise ConfigError(f"exact source is unavailable: {name}") from error
    if path.is_symlink() or resolved != path or metadata.st_nlink < 1:
        raise ConfigError(f"exact source is not a stable Nix store identity: {name}")


def _bind_source_overrides(
    overrides: Sequence[str], sources: Sequence[Mapping[str, Any]]
) -> list[str]:
    if len(overrides) % 3 or any(overrides[index] != "--override-input" for index in range(0, len(overrides), 3)):
        raise ConfigError("deployment overrides must be exact --override-input triples")
    configured: dict[str, str] = {}
    for index in range(0, len(overrides), 3):
        input_name, locator = overrides[index + 1], overrides[index + 2]
        if input_name in configured or not locator.startswith("path:"):
            raise ConfigError("deployment overrides must contain unique exact path inputs")
        configured[input_name] = locator[5:]
    declared: dict[str, Path] = {}
    for source in sources:
        input_name = str(source["input"])
        source_path = Path(str(source.get("path", source.get("store_path", ""))))
        _require_exact_store_path(source_path, str(source.get("name", input_name)))
        if input_name in declared:
            raise ConfigError(f"deployment source input is duplicated: {input_name}")
        declared[input_name] = source_path
    undeclared = sorted(set(configured) - set(declared))
    if undeclared:
        raise ConfigError(f"configured deployment override lacks an exact source contract: {undeclared[0]}")
    result = list(overrides)
    for input_name, source_path in sorted(declared.items()):
        if input_name in configured:
            if Path(configured[input_name]).resolve(strict=False) != source_path:
                raise ConfigError(f"configured deployment override disagrees with exact source: {input_name}")
            continue
        result.extend(["--override-input", input_name, f"path:{source_path}"])
    return result


def _collect_probes(specifications: Sequence[Mapping[str, Any]], blockers: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for probe in specifications:
        identity = _collect(blockers, f"state probe {probe.get('name')}", lambda probe=probe: _probe_identity(probe))
        if identity is not None:
            results.append(identity)
    return results


def _probe_identity(probe: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(str(probe["path"]))
    if probe.get("kind") == "tree":
        identity = tree_identity(path, probe.get("volatile", []))
    elif probe.get("kind") == "file":
        identity = file_identity(path)
    else:
        raise ConfigError(f"unknown state probe kind: {probe.get('kind')}")
    return {"name": probe.get("name"), "kind": probe.get("kind"), "policy": dict(probe), "identity": identity}


def _collect_external(specifications: Sequence[Mapping[str, Any]], blockers: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in specifications:
        identity = _collect(blockers, f"external state {item.get('name')}", lambda item=item: _probe_identity(item))
        if identity is not None:
            results.append(identity)
    return results


def _profile_snapshot(contract: Mapping[str, Any]) -> dict[str, Any]:
    policy = contract.get("profile_policy", {})
    required = {"profile", "live", "generation_glob"}
    if not isinstance(policy, dict) or not required <= set(policy):
        raise ConfigError("profile_policy must declare profile, live, and generation_glob")
    return inspect_profile(Path(policy["profile"]), Path(policy["live"]), Path(policy["generation_glob"]))


def _validate_profile_realignment(
    reviewed: Mapping[str, Any], realigned: Mapping[str, Any], safe_generation: int,
) -> None:
    safe_generations = [
        item for item in reviewed["retained"] if item.get("generation") == safe_generation
    ]
    selected = realigned["selected"]
    if len(safe_generations) != 1 or (
        selected.get("generation_number") != safe_generation
        or selected.get("generation_path") != safe_generations[0].get("path")
        or selected.get("generation_symlink_text") != safe_generations[0].get("link")
        or selected.get("resolved_target") != safe_generations[0].get("target")
    ):
        raise ConfigError("profile realignment did not bind the exact reviewed safe generation identity")


def _restore_profile_selection(
    workflow: Workflow,
    document: Mapping[str, Any],
    reviewed: Mapping[str, Any],
) -> None:
    current = _profile_snapshot(document)
    if current == reviewed:
        return
    if (
        current.get("live") != reviewed.get("live")
        or current.get("retained") != reviewed.get("retained")
        or current.get("selected", {}).get("target") != reviewed.get("live", {}).get("target")
    ):
        raise ConfigError("profile drift prevents bounded pre-switch restoration")
    generation = selected_generation(reviewed)
    command = [
        document["tool"]["external"]["sudo"]["path"],
        *realignment_command(Path(reviewed["selected"]["path"]), generation),
    ]
    command[1] = document["tool"]["external"]["nix-env"]["path"]
    restored = workflow.command(
        "selected-profile pre-switch restoration", command, Path(document["selected_flake"]["path"])
    )
    if restored.returncode or _profile_snapshot(document) != reviewed:
        raise ConfigError("selected-profile pre-switch restoration did not restore reviewed state")


def _revalidate_plan(
    workflow: Workflow,
    document: Mapping[str, Any],
    control_postimages: bool = False,
    expected_profile: Mapping[str, Any] | None = None,
    expected_evaluation_current: CurrentSnapshot | None = None,
    validate_profile: bool = True,
) -> None:
    selected = Path(document["selected_flake"]["path"])
    if document.get("schema") == "nix-update-deployment-plan-v3":
        from .local_source_binding import revalidate_local_sources
        revalidate_local_sources(workflow, document["lock"], document["local_sources"])
    if _tool_identity(workflow.core_dir) != document["tool"]:
        raise ConfigError("nix-update tool identity drifted from reviewed plan")
    if _environment_identity() != document["environment"]:
        raise ConfigError("deployment environment drifted from reviewed plan")
    if local_git_identity(workflow, selected) != document["selected_flake"]["repository"]:
        raise ConfigError("selected repository identity drifted from reviewed plan")
    if normalized_lock(selected / "flake.lock") != document["lock"]:
        raise ConfigError("flake.lock drifted from reviewed plan")
    _validate_source_lock_bindings(document["lock"], document["input_snapshots"])
    expected_overrides = _bind_source_overrides([], document["input_snapshots"])
    if document["projection"]:
        expected_overrides.extend([
            "--override-input", "localConfig", f"path:{document['projection']['root']}",
        ])
    if expected_overrides != document["overrides"]:
        raise ConfigError("reviewed overrides no longer bind exact sources and projection")
    control_root = Path(document["machine_control_snapshot"]["root_identity"]["path"])
    if tree_identity(control_root) != _expected_control_snapshot(document, control_postimages):
        raise ConfigError("machine-local control snapshot drifted from reviewed plan")
    for transaction in document.get("control_transactions", []):
        validate_control_transaction_current(transaction, expect_postimage=control_postimages)
    reviewed_evaluation_current = (
        CurrentSnapshot(**document["evaluation_current"])
        if expected_evaluation_current is None else expected_evaluation_current
    )
    if snapshot_current(Path(document["evaluation_root"])) != reviewed_evaluation_current:
        raise ConfigError("evaluation current drifted from reviewed plan")
    if validate_profile:
        reviewed_profile = document["live"]["profile"] if expected_profile is None else expected_profile
        if _profile_snapshot(document) != reviewed_profile:
            raise ConfigError("selected-profile or live-system state drifted from reviewed plan")
    projection = document.get("projection")
    if not isinstance(projection, dict):
        raise ConfigError("reviewed plan has no exact scratch projection")
    actual_projection = inspect_scratch(Path(projection["root"]))
    if [list(item) for item in actual_projection.files] != projection["files"]:
        raise ConfigError("scratch projection file inventory drifted from reviewed plan")
    if actual_projection.digest != projection["digest"]:
        raise ConfigError("scratch projection digest drifted from reviewed plan")
    _validate_virtual_control_projection(document.get("control_projection"))
    for source in document["input_snapshots"]:
        if _source_identity(workflow, selected, source) != source:
            raise ConfigError(f"source drifted from reviewed plan: {source['name']}")
    for participant in document["participants"]:
        validate_executable(participant)
    _validate_participant_provenance(
        document["participants"], document["target"]["derivation_output"], document["target"]["closure"]
    )
    for probe in document["state_probes"]:
        current = _probe_identity(probe["policy"])
        if current != probe:
            raise ConfigError(f"state probe drifted from reviewed plan: {probe['name']}")
    for external in document["external_state"]:
        if _probe_identity(external["policy"]) != external:
            raise ConfigError(f"external state drifted from reviewed plan: {external['name']}")


def _validate_virtual_control_projection(control_projection: Any) -> None:
    if not isinstance(control_projection, dict):
        raise ConfigError("reviewed plan has no virtual control projection")
    virtual_root = Path(control_projection["virtual_root"])
    require_private_directory(virtual_root, "virtual control projection root")
    projected_tree = tree_identity(virtual_root)
    if projected_tree != control_projection["projected_tree"] or sha256_bytes(canonical_bytes({
        "projected_tree": projected_tree,
        "transactions": control_projection["transactions"],
    })) != control_projection["digest"]:
        raise ConfigError("virtual control projection drifted from reviewed plan")


def _expected_control_snapshot(document: Mapping[str, Any], postimages: bool) -> dict[str, Any]:
    snapshot = json.loads(json.dumps(document["machine_control_snapshot"]))
    if not postimages:
        return snapshot
    root = Path(snapshot["root_identity"]["path"])
    content_identity = snapshot["content_identity"]
    entries = {item["path"]: item for item in content_identity["entries"]}
    for transaction in document["control_transactions"]:
        verify_control_postimage(transaction)
        path = Path(transaction["path"])
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if relative not in entries:
            raise ConfigError(f"control transaction path was absent from reviewed machine control: {path}")
        content = base64.b64decode(transaction["postimage_bytes_b64"], validate=True)
        entry = entries[relative]
        entry.update(
            {
                "kind": "regular",
                "mode": transaction["post_mode"],
                "sha256": sha256_bytes(content),
                "size": len(content),
            }
        )
        entry.pop("target", None)
        entry.pop("target_identity", None)
    content_material = {
        "entries": content_identity["entries"],
        "volatile": content_identity["volatile"],
    }
    content_identity["digest"] = sha256_bytes(canonical_bytes(content_material))
    snapshot["digest"] = sha256_bytes(canonical_bytes({
        "root_identity": snapshot["root_identity"],
        "content_identity": content_identity,
    }))
    return snapshot


def _revalidate_target(workflow: Workflow, document: Mapping[str, Any], target: str) -> None:
    selected = Path(document["selected_flake"]["path"])
    command = [
        "nix", "build", "--print-out-paths", "--no-link", "--no-update-lock-file", "--no-write-lock-file",
        f"path:.#darwinConfigurations.{document['selected_flake']['host']}.system", *document["overrides"],
    ]
    result = workflow.command("reviewed target revalidation", command, selected)
    if result.returncode or result.stdout.strip() != target:
        raise ConfigError("rebuilt target does not equal reviewed closure")
    if closure_inventory(workflow, selected, target) != document["target"]["closure"]:
        raise ConfigError("target closure inventory drifted from reviewed plan")
    for candidate in document.get("host_candidates", []):
        if candidate["host"] == document["selected_flake"]["host"]:
            continue
        additional = workflow.command(
            f"reviewed target revalidation ({candidate['host']})",
            [
                "nix", "build", "--print-out-paths", "--no-link", "--no-update-lock-file", "--no-write-lock-file",
                f"path:.#darwinConfigurations.{candidate['host']}.system", *document["overrides"],
            ],
            selected,
        )
        if additional.returncode or additional.stdout.strip() != candidate["output"]:
            raise ConfigError(f"additional host target drifted: {candidate['host']}")
        if closure_inventory(workflow, selected, candidate["output"]) != candidate["closure"]:
            raise ConfigError(f"additional host closure drifted: {candidate['host']}")


def _final_privilege_attestation(
    workflow: Workflow,
    document: Mapping[str, Any],
    state_root: Path,
    transition: str,
    result: dict[str, Any],
    participant_results: list[ParticipantResult],
    *,
    profile: Mapping[str, Any] | None = None,
    control_postimages: bool = False,
    projection_published: bool = False,
    expected_evaluation_current: CurrentSnapshot | None = None,
) -> None:
    result["events"].append({"event": "final-mutable-state-recheck-started", "transition": transition})
    _revalidate_plan(
        workflow,
        document,
        control_postimages=control_postimages,
        expected_profile=profile,
        expected_evaluation_current=expected_evaluation_current,
    )
    result["events"].append({"event": "final-mutable-state-recheck-passed", "transition": transition})
    _guard_processes(
        [], strict=True, participant_paths=[item["path"] for item in document["participants"]]
    )
    result["events"].append({"event": "final-process-gate-passed", "transition": transition})
    context = _fresh_participant_context(
        document,
        state_root,
        f"apply-final-{transition}",
        profile=profile,
        control_postimages=control_postimages,
        projection_published=projection_published,
    )
    fresh_results, blockers = _safe_participant_phase(
        document["participants"], "preflight", Path(context["path"]), workflow.runner
    )
    participant_results.extend(fresh_results)
    if blockers:
        raise ConfigError(blockers[0])
    result["events"].append({
        "event": "final-participant-attestation-passed",
        "transition": transition,
        "context_digest": context["digest"],
    })


def _verify_post_switch(document: Mapping[str, Any]) -> None:
    current = _profile_snapshot(document)
    validate_profile_transition(document["live"]["profile"], current, document["target"]["derivation_output"])
    for probe in document["state_probes"]:
        # Volatile policies are already reduced into the reviewed identity; apply
        # must compare the same declared policy, never a generic ignore glob.
        current_probe = _probe_identity(probe["policy"])
        if current_probe != probe:
            raise ConfigError(f"post-switch state probe drift: {probe['name']}")
    for external in document["external_state"]:
        if _probe_identity(external["policy"]) != external:
            raise ConfigError(f"post-switch external state drift: {external['name']}")


def _guard_processes(blockers: list[str], strict: bool, participant_paths: Sequence[str] = ()) -> None:
    records = read_process_table()
    matches = competing_processes(
        records, process_ancestors(records, os.getpid()), participant_paths
    )
    blockers.extend(f"competing mutation process {item['pid']}: {item['reason']}" for item in matches)
    if matches and strict:
        raise ConfigError(blockers[0])


def _participant_context_document(
    *, stage: str, plan_digest: str, repository: Mapping[str, Any], lock: Mapping[str, Any],
    selected: Path, host: str, target: str, closure: Sequence[Mapping[str, Any]],
    overrides: Sequence[str], projection: Mapping[str, Any], sources: Sequence[Mapping[str, Any]],
    control_projection: Mapping[str, Any], profile: Mapping[str, Any], probes: Sequence[Mapping[str, Any]],
    external: Sequence[Mapping[str, Any]], participant_declarations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    declarations = [dict(item) for item in participant_declarations]
    material: dict[str, Any] = {
        "schema": "nix-update-participant-context-v1",
        "protocol": "nix-update-participant-v1",
        "stage": stage,
        "plan_digest": plan_digest,
        "selected_repository": dict(repository),
        "selected_flake": str(selected),
        "flake_lock": dict(lock),
        "host": host,
        "environment": _environment_identity(),
        "target": {"output": target, "closure": list(closure)},
        "overrides": list(overrides),
        "overrides_digest": sha256_bytes(canonical_bytes({"overrides": list(overrides)})),
        "projection": dict(projection),
        "sources": list(sources),
        "control_projection": dict(control_projection),
        "profile": dict(profile),
        "state_probes": list(probes),
        "external_state": list(external),
        "participant_declarations": declarations,
        "participant_declaration_digest": sha256_bytes(canonical_bytes({"participants": declarations})),
    }
    material["plan_draft_identity"] = sha256_bytes(canonical_bytes(material))
    return material


def _write_participant_context(state_root: Path, document: Mapping[str, Any]) -> dict[str, Any]:
    parent = ensure_private_root(state_root / "participant-contexts", "participant context root")
    raw = canonical_bytes(document)
    digest = sha256_bytes(raw)
    path = parent / f"{digest}.json"
    atomic_private_write(path, raw)
    return {"path": str(path), "digest": digest, "document": dict(document)}


def _fresh_participant_context(
    document: Mapping[str, Any],
    state_root: Path,
    stage: str,
    *,
    profile: Mapping[str, Any] | None = None,
    control_postimages: bool = False,
    projection_published: bool = False,
) -> dict[str, Any]:
    projection = dict(document["projection"])
    projection["published"] = projection_published
    live_profile = (
        dict(document["live"]["profile"])
        if profile is None and not document["participants"]
        else (_profile_snapshot(document) if profile is None else dict(profile))
    )
    probes = [_probe_identity(item["policy"]) for item in document["state_probes"]]
    external = [_probe_identity(item["policy"]) for item in document["external_state"]]
    control_projection = json.loads(json.dumps(document["control_projection"]))
    control_projection["current_tree"] = _expected_control_snapshot(document, control_postimages)
    if control_postimages:
        for transaction in control_projection.get("transactions", []):
            transaction["current"] = transaction["projected_postimage"]
    return _write_participant_context(
        state_root,
        _participant_context_document(
            stage=stage,
            plan_digest=str(document["plan_digest"]),
            repository=document["selected_flake"]["repository"],
            lock=document["lock"],
            selected=Path(document["selected_flake"]["path"]),
            host=document["selected_flake"]["host"],
            target=document["target"]["derivation_output"],
            closure=document["target"]["closure"],
            overrides=document["overrides"],
            projection=projection,
            sources=document["input_snapshots"],
            control_projection=control_projection,
            profile=live_profile,
            probes=probes,
            external=external,
            participant_declarations=document["participants"],
        ),
    )


def _safe_participant_phase(participants: Sequence[Mapping[str, Any]], phase: str, context: Path, runner: Runner) -> tuple[list[ParticipantResult], list[str]]:
    require_private_directory(context.parent, "participant context directory")
    require_private_file(context, "participant context")
    results: list[ParticipantResult] = []
    blockers: list[str] = []
    for participant in participants:
        if phase not in participant["phases"]:
            continue
        try:
            result = run_participant(participant, phase, context, runner)
        except ConfigError as error:
            blockers.append(str(error))
            break
        results.append(result)
        if phase == "pre-switch" and participant.get("mutation_authority") and not result.rollback_handle:
            blockers.append(f"mutating pre-switch participant did not return a rollback handle: {result.name}")
        if result.status != "ok":
            blockers.extend(f"participant {result.name}: {item}" for item in result.blockers)
            if not result.blockers:
                blockers.append(f"participant {result.name} returned {result.status}")
    return results, blockers


def _participant_rollbacks(
    participants: Sequence[Mapping[str, Any]],
    results: Sequence[ParticipantResult],
    context: Path,
    runner: Runner,
    sink: list[ParticipantResult],
    *,
    phases: frozenset[str] = frozenset({"pre-switch"}),
    continue_on_failure: bool = False,
) -> list[str]:
    by_name = {item["name"]: item for item in participants}
    failures: list[str] = []
    for result in reversed(results):
        if result.phase in phases and result.rollback_handle:
            declaration = by_name[result.name]
            if not declaration.get("mutation_authority") or not declaration.get("rollback_authority"):
                raise ConfigError(f"participant emitted rollback handle without rollback authority: {result.name}")
            originating_context = context.parent / f"{result.context_digest}.json"
            require_private_file(originating_context, "originating participant context")
            raw_context = originating_context.read_bytes()
            if sha256_bytes(raw_context) != result.context_digest:
                raise ConfigError(f"originating participant context digest changed: {result.name}")
            _validate_rollback_origin(raw_context, declaration, result)
            rollback = bounded_rollback(declaration, originating_context, result.rollback_handle, runner)
            sink.append(rollback)
            if rollback.status != "ok":
                message = f"participant rollback did not succeed: {result.name}"
                failures.append(message)
                if not continue_on_failure:
                    raise ConfigError(message)
    return failures


def _validate_participant_provenance(
    participants: Sequence[Mapping[str, Any]],
    target: str,
    closure: Sequence[Mapping[str, Any]],
) -> None:
    closure_paths = {str(item.get("path")) for item in closure}
    for participant in participants:
        provenance = participant["provenance"]
        if provenance["kind"] != "target-closure":
            continue
        if provenance["identity"] != target:
            raise ConfigError(f"participant target provenance does not equal reviewed target: {participant['name']}")
        path = Path(participant["path"])
        parts = path.parts
        if len(parts) < 4 or parts[:3] != ("/", "nix", "store"):
            raise ConfigError(f"participant executable is outside the Nix store: {participant['name']}")
        store_root = str(Path(*parts[:4]))
        if store_root not in closure_paths:
            raise ConfigError(f"participant executable is outside the reviewed target closure: {participant['name']}")


def _participant_phase_indeterminate(
    participants: Sequence[Mapping[str, Any]],
    phase: str,
    results: Sequence[ParticipantResult],
) -> bool:
    declared = [item for item in participants if phase in item["phases"]]
    by_name = {item["name"]: item for item in declared}
    declared_mutating = [item for item in declared if item.get("mutation_authority")]
    result_names = {item.name for item in results}
    if any(item["name"] not in result_names for item in declared_mutating):
        return True
    return any(
        bool(by_name[result.name].get("mutation_authority"))
        and (
            result.mutation_state == "indeterminate"
            or (not result.rollback_handle and result.mutation_state != "unchanged")
        )
        for result in results
    )


def _validate_rollback_origin(
    raw_context: bytes,
    declaration: Mapping[str, Any],
    result: ParticipantResult,
) -> None:
    try:
        document = json.loads(raw_context)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConfigError(f"originating participant context is invalid: {result.name}") from error
    if (
        not isinstance(document, dict)
        or document.get("schema") != "nix-update-participant-context-v1"
        or document.get("protocol") != "nix-update-participant-v1"
        or document.get("stage") != result.phase
        or not isinstance(document.get("plan_digest"), str)
        or not isinstance(document.get("target"), dict)
        or not isinstance(document["target"].get("output"), str)
        or not isinstance(document["target"].get("closure"), list)
        or not isinstance(document.get("participant_declarations"), list)
    ):
        raise ConfigError(f"originating participant context is not target-aware: {result.name}")
    declarations = document["participant_declarations"]
    expected_digest = sha256_bytes(canonical_bytes({"participants": declarations}))
    if (
        document.get("participant_declaration_digest") != expected_digest
        or not any(item == declaration for item in declarations)
        or sha256_bytes(canonical_bytes(declaration)) != sha256_bytes(canonical_bytes(
            next(item for item in declarations if item.get("name") == result.name)
        ))
    ):
        raise ConfigError(f"originating participant declaration changed: {result.name}")


def _prior_apply_markers(artifact: PlanArtifact, state_root: Path | None = None) -> list[Path]:
    markers = [
            *artifact.directory.glob("results/*/pre-switch-mutation.json"),
            *artifact.directory.glob("results/*/switch-invocation.json"),
        ]
    if state_root is not None:
        consumed = state_root / "consumed-authority" / f"{artifact.digest}.json"
        if consumed.exists() or consumed.is_symlink():
            markers.append(consumed)
    return sorted(markers, key=str)


def _collect(blockers: list[str], label: str, function: Callable[[], Any]) -> Any:
    try:
        return function()
    except (ConfigError, OSError, ValueError, json.JSONDecodeError) as error:
        blockers.append(f"{label}: {error}")
        return None


def _finish_result(directory: Path, result: dict[str, Any], workflow: Workflow, participants: Sequence[ParticipantResult], status: int) -> int:
    result["finished_at"] = _now()
    result["commands"] = [asdict(item) for item in workflow.records]
    result["participants"] = [asdict(item) for item in participants]
    result["result_path"] = str(directory / "result.json")
    result["command_evidence_policy"] = {
        "raw_limit_bytes": COMMAND_RAW_LIMIT,
        "summary_limit_bytes": COMMAND_SUMMARY_LIMIT,
        "root": str(workflow._evidence_directory()),
    }
    try:
        write_result(directory, result)
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as error:
        try:
            fallback_root = ensure_private_root(_private_state_root() / "failure-results", "failure result root")
            fallback = fallback_root / f"{result.get('plan_digest', 'unknown')}-{os.getpid()}-{os.urandom(4).hex()}.json"
            failure = {
                "schema": "nix-update-deployment-result-fallback-v1",
                "plan_digest": result.get("plan_digest"),
                "terminal_state": "EVIDENCE_WRITE_FAILURE",
                "original_terminal_state": result.get("terminal_state"),
                "switch_invocation_started": bool(result.get("switch_invocation_started")),
                "failure": redact_text(str(error)),
                "result_path": str(fallback),
                "written_at": _now(),
            }
            exclusive_private_write(fallback, canonical_bytes(failure))
            fsync_directory(fallback_root)
            print(f"nix-update: deployment result evidence fallback: {fallback}", file=os.sys.stderr, flush=True)
        except (ConfigError, OSError, ValueError) as fallback_error:
            print(
                f"nix-update: unable to persist terminal evidence: {type(fallback_error).__name__}",
                file=os.sys.stderr, flush=True,
            )
        return 1
    if result["terminal_state"] == "SUCCESS":
        print("nix-update: deployment result: SUCCESS", flush=True)
    else:
        print(f"nix-update: deployment result: {result['terminal_state']}", file=os.sys.stderr, flush=True)
    return status


def _add_post_invocation_disposition(result: dict[str, Any], document: Mapping[str, Any]) -> None:
    profile = document["profile_policy"]["profile"]
    live = document["profile_policy"]["live"]
    commands = [
        ["readlink", profile],
        ["readlink", live],
        [document["tool"]["external"]["nix-env"]["path"], "--profile", profile, "--list-generations"],
    ]
    result["operator_disposition_commands"] = commands
    print("nix-update: switch was invoked; inspect state before any further mutation:", file=os.sys.stderr, flush=True)
    for command in commands:
        print(f"nix-update: disposition argv: {json.dumps(command, separators=(',', ':'))}", file=os.sys.stderr, flush=True)


def _tool_identity(core_dir: Path) -> dict[str, Any]:
    paths = [core_dir / "bin/nix-update", core_dir / "bin/nix-rebuild"]
    paths.extend(sorted((core_dir / "libexec/nix_darwin_local").glob("*.py")))
    missing = [path for path in paths[:2] if not path.exists()]
    if missing:
        raise ConfigError(f"nix-update tool source is missing: {missing[0]}")
    files = [file_identity(path) for path in paths]
    external: dict[str, Any] = {}
    for name in ("git", "nix", "nix-env", "sudo"):
        executable = shutil.which(name)
        if executable is None:
            raise ConfigError(f"required deployment executable is unavailable: {name}")
        unreadable_probe = ("--version",) if name == "sudo" else None
        external[name] = _executable_identity(Path(executable), unreadable_probe=unreadable_probe)
    external["darwin-rebuild"] = _executable_identity(core_dir / "bin/darwin-rebuild")
    external["darwin-rebuild-upstream"] = _executable_identity(Path("/run/current-system/sw/bin/darwin-rebuild"))
    material = {"files": files, "external": external}
    return {
        "name": "nix-update",
        "version": "1",
        "core": str(core_dir.resolve()),
        "files": files,
        "external": external,
        "digest": sha256_bytes(canonical_bytes(material)),
    }


def _executable_identity(
    path: Path, *, unreadable_probe: tuple[str, ...] | None = None
) -> dict[str, Any]:
    if not path.is_absolute() or not path.exists() or not os.access(path, os.X_OK):
        raise ConfigError(f"deployment executable is missing or not executable: {path}")
    resolved = path.resolve(strict=True)
    try:
        identity = file_identity(resolved)
    except PermissionError as error:
        if unreadable_probe is None:
            raise ConfigError(f"deployment executable bytes are unreadable: {resolved}") from error
        identity = _unreadable_executable_identity(resolved, unreadable_probe)
    return {"path": str(path), "resolved": str(resolved), "identity": identity}


def _unreadable_executable_identity(path: Path, probe: tuple[str, ...]) -> dict[str, Any]:
    metadata = path.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or mode & 0o222
        or not probe
    ):
        raise ConfigError(
            f"unreadable deployment executable is not an immutable root-owned regular file: {path}"
        )
    try:
        result = subprocess.run(
            [str(path), *probe], capture_output=True, check=False, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ConfigError(f"unable to probe unreadable deployment executable: {path}") from error
    if result.returncode:
        raise ConfigError(f"unreadable deployment executable probe failed: {path}")
    return {
        "path": str(path.absolute()),
        "mode": mode,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "kind": "regular",
        "link_count": metadata.st_nlink,
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "content": "unreadable-root-owned-executable",
        "probe": list(probe),
        "probe_stdout_sha256": sha256_bytes(result.stdout),
        "probe_stderr_sha256": sha256_bytes(result.stderr),
    }


def _environment_identity() -> dict[str, Any]:
    names = ("HOME", "LANG", "LC_ALL", "NIX_CONFIG", "NIX_PATH", "NIX_REMOTE", "PATH")
    identity: dict[str, dict[str, Any]] = {}
    for name in names:
        value = os.environ.get(name)
        identity[name] = {
            "present": value is not None,
            "sha256": None if value is None else sha256_bytes(value.encode()),
        }
    user = pwd.getpwuid(os.getuid())
    identity["operator"] = {
        "uid": os.getuid(), "euid": os.geteuid(), "gid": os.getgid(), "egid": os.getegid(),
        "name_sha256": sha256_bytes(user.pw_name.encode()),
        "home_sha256": sha256_bytes(user.pw_dir.encode()),
    }
    identity["host"] = {
        "hostname_sha256": sha256_bytes(socket.gethostname().encode()),
        "node_sha256": sha256_bytes(platform.node().encode()),
    }
    identity["platform"] = {
        "system": platform.system(), "release": platform.release(),
        "machine": platform.machine(), "python": platform.python_version(),
    }
    return identity


def _plan_markdown(document: Mapping[str, Any]) -> str:
    lines = [
        "# nix-update deployment plan",
        "",
        f"Status: **{document['status']}**",
        "",
        f"Created: `{document['created_at']}`",
        f"Selected flake: `{document['selected_flake']['path']}`",
        f"Host: `{document['selected_flake']['host']}`",
        "Plan digest: `{PLAN_DIGEST}`",
        f"Exact target: `{document['target']['derivation_output'] or 'unavailable'}`",
        "",
        "## Blockers",
        "",
    ]
    lines.extend(f"- {item}" for item in document["blockers"])
    if not document["blockers"]:
        lines.append("- None. Ready for manual review; this is not deployment authorization.")
    lines.extend(["", "Apply requires the exact typed confirmation stored in `plan.json`.", ""])
    return "\n".join(lines)


def _private_state_root() -> Path:
    root = operator_home() / ".local/state/nix-update"
    return ensure_private_root(root, "nix-update private state root")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _fsync(path: Path) -> None:
    fsync_directory(path)


def _write_private_file(path: Path, content: bytes) -> None:
    _write_exclusive_file(path, content, 0o600)


def _write_exclusive_file(path: Path, content: bytes, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


COMMAND_RAW_LIMIT = 10 * 1024 * 1024
COMMAND_SUMMARY_LIMIT = 8192


def _command_summary(evidence: Mapping[str, Any], status: int) -> str:
    with Path(evidence["path"]).open("rb") as source:
        raw = source.read(COMMAND_RAW_LIMIT)
    if status:
        from .command_diagnostics import sanitize_text
        safe = sanitize_text(raw.decode("utf-8", errors="replace"))
        raw = safe.encode("utf-8")
    return redacted_summary(raw, COMMAND_SUMMARY_LIMIT)


def _write_bounded_stream(path: Path, value: str) -> dict[str, Any]:
    raw = value.encode("utf-8", errors="replace")
    retained = raw[:COMMAND_RAW_LIMIT]
    exclusive_private_write(path, retained)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "retained_sha256": hashlib.sha256(retained).hexdigest(),
        "size": len(raw),
        "retained_bytes": len(retained),
        "overflow": len(raw) > COMMAND_RAW_LIMIT,
        "limit": COMMAND_RAW_LIMIT,
    }


def _run_streamed_command(
    argv: list[str], cwd: Path, stdout_path: Path, stderr_path: Path,
    *, timeout: float | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any], dict[str, Any]]:
    require_private_directory(stdout_path.parent, "command evidence directory")
    descriptors = {
        "stdout": os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
        "stderr": os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
    }
    try:
        process = subprocess.Popen(
            argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except BaseException:
        for descriptor in descriptors.values():
            os.close(descriptor)
        raise
    evidence: dict[str, dict[str, Any]] = {}
    summaries: dict[str, bytes] = {}
    pump_errors: list[BaseException] = []
    pump_failed = threading.Event()
    stop_pumps = threading.Event()

    def pump(name: str, stream: Any) -> None:
        try:
            digest = hashlib.sha256()
            retained_digest = hashlib.sha256()
            size = 0
            retained = 0
            summary = bytearray()
            descriptor = descriptors[name]
            stream_descriptor = stream.fileno()
            os.set_blocking(stream_descriptor, False)
            while not stop_pumps.is_set():
                readable, _, _ = select.select([stream_descriptor], [], [], 0.05)
                if not readable:
                    continue
                chunk = os.read(stream_descriptor, 65536)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                if retained < COMMAND_RAW_LIMIT:
                    retained_chunk = chunk[: COMMAND_RAW_LIMIT - retained]
                    retained_digest.update(retained_chunk)
                    piece = memoryview(retained_chunk)
                    while piece:
                        written = os.write(descriptor, piece)
                        piece = piece[written:]
                    retained += min(len(chunk), COMMAND_RAW_LIMIT - retained)
                if len(summary) < COMMAND_SUMMARY_LIMIT:
                    summary.extend(chunk[: COMMAND_SUMMARY_LIMIT - len(summary)])
            os.fsync(descriptor)
            evidence[name] = {
                "path": str(stdout_path if name == "stdout" else stderr_path),
                "sha256": digest.hexdigest(), "retained_sha256": retained_digest.hexdigest(),
                "size": size, "retained_bytes": retained,
                "overflow": size > COMMAND_RAW_LIMIT, "limit": COMMAND_RAW_LIMIT,
            }
            summaries[name] = bytes(summary)
        except BaseException as error:
            pump_errors.append(error)
            pump_failed.set()

    threads = [
        threading.Thread(target=pump, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=pump, args=("stderr", process.stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    deadline = None if timeout is None else time.monotonic() + timeout
    timed_out = False
    try:
        while process.poll() is None or any(thread.is_alive() for thread in threads):
            timed_out = deadline is not None and time.monotonic() >= deadline
            if pump_failed.wait(0.05) or timed_out:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                break
        status = process.wait()
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        raise
    finally:
        stop_pumps.set()
        for thread in threads:
            thread.join(timeout=1)
        process.stdout.close()
        process.stderr.close()
        for descriptor in descriptors.values():
            os.close(descriptor)
    if timed_out:
        raise ConfigError("command exceeded its declared timeout")
    if pump_errors:
        raise ConfigError(f"command evidence stream failed (exit {status}): {type(pump_errors[0]).__name__}") from pump_errors[0]
    result = subprocess.CompletedProcess(
        argv, status,
        summaries["stdout"].decode("utf-8", errors="replace"),
        summaries["stderr"].decode("utf-8", errors="replace"),
    )
    return result, evidence["stdout"], evidence["stderr"]
