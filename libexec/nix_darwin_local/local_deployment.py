from __future__ import annotations

import datetime as dt
import subprocess
import sys
import tempfile
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from .config import operator_home
from .paths import ConfigError
from .repository_identity import local_git_identity
from .deployment_paths import (
    create_private_directory,
    ensure_private_root,
)
from .deployment_state import (
    DeploymentLock,
    derive_predecessor_generation,
    durable_marker,
    inspect_profile,
    realignment_command,
)
from .deployment_workflow import (
    Workflow,
    _guard_processes,
    _qualification_command,
    _tool_identity,
    closure_inventory,
    current_live_closure,
    evaluate_allowlist,
)
from .projection import render_scratch, inspect_scratch
from .deployment_plan import tree_identity, redact_text
from .command_diagnostics import failure_explanation
from .deployment_effects import (
    classify_deployment_effects,
    evaluate_deployment_effects,
    run_read_only_preflights,
)
from .deployment_sources import derive_source_identities
from .deployment_summary import format_deployment_summary
from .local_deployment_plan import load_local_plan, write_local_plan


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def require_terminal_confirmation(prompt: str = "nix-update: type 'DEPLOY' to activate host: ") -> bool:
    try:
        with open("/dev/tty", "w", encoding="utf-8") as tty_out, open("/dev/tty", "r", encoding="utf-8") as tty_in:
            tty_out.write(prompt)
            tty_out.flush()
            line = tty_in.readline()
            if not line:
                return False
            return line.strip() == "DEPLOY"
    except (OSError, IOError):
        return False


def ensure_sudo_credentials(sudo_path: str = "sudo") -> bool:
    try:
        check = subprocess.run(
            [sudo_path, "-n", "-v"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    if check.returncode == 0:
        return True
    try:
        with open("/dev/tty", "r", encoding="utf-8") as tty_in, open("/dev/tty", "w", encoding="utf-8") as tty_out:
            cmd = subprocess.run(
                [sudo_path, "-v"],
                stdin=tty_in,
                stdout=tty_out,
                stderr=tty_out,
                check=False,
            )
            return cmd.returncode == 0
    except (OSError, IOError):
        return False


GIT_AUTHORITY_KEYS = ("commit", "tree", "index_sha256", "status", "flake_nix", "flake_lock")


def _check_selected_git_authority(
    workflow: Any, selected: Path, git_identity: Mapping[str, Any]
) -> None:
    current_git = local_git_identity(workflow, selected, False)
    for key in GIT_AUTHORITY_KEYS:
        if current_git.get(key) != git_identity.get(key):
            raise ConfigError(
                f"selected repository Git authority drifted before local deployment ({key})"
            )


def _switch_flake_marker(switch_command: Sequence[str], scratch_target: Path, selected: Path) -> str:
    try:
        flake_idx = list(switch_command).index("--flake")
        flake_arg = switch_command[flake_idx + 1]
        flake_path_str = flake_arg.split("#")[0].removeprefix("path:")
        resolved = Path(flake_path_str).resolve(strict=False)
        scratch_resolved = scratch_target.resolve(strict=False)
        selected_resolved = selected.resolve(strict=False)
        if resolved == scratch_resolved or scratch_resolved in resolved.parents:
            return "projected-scratch"
        if resolved == selected_resolved or selected_resolved in resolved.parents:
            return "selected"
        return "other"
    except Exception:
        return "unknown"


def _scratch_tree_identity(target: Path) -> dict[str, Any]:
    from .deployment_plan import tree_identity as ti
    return ti(target)


def _journal_document(
    selected: Path,
    host: str,
    target: str,
    predecessor_generation: int,
    terminal_state: str,
    exit_status: int,
    error: str | None = None,
    switch_flake: str = "projected-scratch",
) -> dict[str, Any]:
    journal = {
        "schema": "nix-update-deployment-journal-v1",
        "selected_flake": str(selected),
        "switch_flake": switch_flake,
        "host": host,
        "target": target,
        "predecessor_generation": predecessor_generation,
        "terminal_state": terminal_state,
        "exit_status": exit_status,
        "publication_invoked": False,
        "lock_maintenance_invoked": False,
        "store_optimization_invoked": False,
        "garbage_collection_invoked": False,
        "finished_at": _now(),
    }
    if error is not None:
        journal["error"] = error
    return journal


def _post_switch_error(
    post_profile: dict[str, Any], target: str, predecessor_generation: int,
    predecessor_target: str,
) -> str | None:
    live_target = post_profile.get("live", {}).get("target")
    if live_target != target:
        return f"post-switch verification failed: live system target {live_target} != {target}"
    selected_target = post_profile.get("selected", {}).get("target")
    if selected_target != target:
        return f"post-switch verification failed: selected profile target {selected_target} != {target}"
    retained_generations = {
        (item.get("generation"), item.get("target"))
        for item in post_profile.get("retained", [])
    }
    if (predecessor_generation, predecessor_target) not in retained_generations:
        return (
            "post-switch verification failed: predecessor generation "
            f"{predecessor_generation} was not retained with its live target"
        )
    return None


def _closure_delta(
    live_closure: Sequence[dict[str, Any]],
    target_closure: Sequence[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    live_by_path = {item["path"]: item for item in live_closure}
    target_by_path = {item["path"]: item for item in target_closure}
    return {
        "added": [target_by_path[path] for path in sorted(target_by_path.keys() - live_by_path.keys())],
        "removed": [live_by_path[path] for path in sorted(live_by_path.keys() - target_by_path.keys())],
    }


def deploy_local(
    workflow: Workflow,
    selected: Path,
    host: str,
    control_dir: Path,
    overrides: Sequence[str],
    test_jobs: str,
    build_jobs: str,
    dry_run: bool = False,
    plan_only: bool = False,
    plan_output: Path | None = None,
    plan_path: Path | None = None,
    source_revisions: Mapping[str, str] | None = None,
) -> int:
    from .config import ControlConfig, EffectiveSourceRevisions, load_control_config, resolve_effective_source_revisions
    from .local_source import match_selected_source

    if plan_only and not dry_run and plan_output is None:
        raise ConfigError("--plan-only requires --output <directory>")

    if isinstance(source_revisions, EffectiveSourceRevisions):
        effective = source_revisions
    else:
        cfg_path = control_dir / "nix-darwin.toml"
        if cfg_path.is_file():
            cfg = load_control_config(cfg_path)
        else:
            cfg = ControlConfig(sources=getattr(workflow, "sources", ()))
        effective = resolve_effective_source_revisions(cfg, source_revisions or {})

    state_root = ensure_private_root(operator_home() / ".local/state/nix-update", "nix-update private state root")
    lock_path = state_root / "transaction.lock"

    with DeploymentLock(lock_path):
        _guard_processes([], strict=True)

        git_identity = local_git_identity(workflow, selected, False)
        repo_root = Path(git_identity["repository_root"]) if "repository_root" in git_identity else None
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
            observed_rev = git_identity.get("commit")
            if observed_rev != expected_rev:
                raise ConfigError(
                    f"selected source {selected_key!r} commit {observed_rev} does not match expected snapshot revision {expected_rev}"
                )
            lock_backed_revisions = {k: v for k, v in effective.items() if k != selected_key}
        else:
            selected_key = selected_entry.key if selected_entry else None
            if selected_key and selected_key in effective:
                expected_rev = effective[selected_key]
                observed_rev = git_identity.get("commit")
                if observed_rev != expected_rev:
                    raise ConfigError(
                        f"selected source {selected_key!r} commit {observed_rev} does not match expected revision {expected_rev}"
                    )
                lock_backed_revisions = {k: v for k, v in effective.items() if k != selected_key}
            else:
                lock_backed_revisions = dict(effective)

        from .deployment_plan import normalized_lock
        from .local_source_binding import capture_local_sources, revalidate_local_sources

        profile_path = Path("/nix/var/nix/profiles/system")
        live_path = Path("/run/current-system")
        generations_glob = Path("/nix/var/nix/profiles/system-*-link")

        profile = inspect_profile(profile_path, live_path, generations_glob)
        if profile.get("relationship") == "unknown":
            raise ConfigError("selected-profile/live-system relationship is unknown")

        predecessor_generation = derive_predecessor_generation(profile)

        loaded_doc: Mapping[str, Any] | None = None
        if plan_path is not None:
            loaded_artifact = load_local_plan(plan_path)
            loaded_doc = loaded_artifact.document
            if loaded_doc.get("schema") == "nix-update-local-plan-v3":
                plan_ss = loaded_doc.get("source_snapshot")
                if not isinstance(plan_ss, Mapping):
                    raise ConfigError("local plan v3 missing valid source_snapshot")
                plan_active = plan_ss.get("active", False)
                if plan_active != effective.active:
                    raise ConfigError("configured snapshot intent changed since reviewed local plan was created")
                if plan_active:
                    plan_configured = plan_ss.get("configured_revisions") or {}
                    curr_configured = dict(effective.configured_revisions) if effective.configured_revisions else {}
                    if plan_configured != curr_configured:
                        raise ConfigError("configured snapshot revisions changed since reviewed local plan was created")
                if plan_ss.get("effective_revisions") != dict(effective.by_key):
                    raise ConfigError("effective source revisions changed since reviewed local plan was created")
                plan_sel = plan_ss.get("selected_source")
                curr_sel = {
                    "key": selected_key,
                    "revision": git_identity.get("commit"),
                } if selected_key is not None and "commit" in git_identity else None
                if plan_sel != curr_sel:
                    raise ConfigError("selected source authority changed since reviewed local plan was created")
                if plan_ss.get("provenance") != dict(effective.provenance_by_key):
                    raise ConfigError("source revision provenance changed since reviewed local plan was created")
                if plan_ss.get("lock_backed_revisions") != dict(lock_backed_revisions):
                    raise ConfigError("lock-backed source revisions changed since reviewed local plan was created")
            elif effective.active:
                raise ConfigError("configured snapshot intent changed since reviewed local plan was created")

        control_identity = tree_identity(control_dir)
        with tempfile.TemporaryDirectory(prefix="nix-update-local-", dir=state_root) as scratch_name:
            scratch_parent = Path(scratch_name)
            scratch_target = scratch_parent / "target"
            if ((selected / ".git").exists() or (selected / ".git").is_file()) and "repository_root" in git_identity:
                from .deployment_workflow import _materialize_git_tree
                _materialize_git_tree(workflow, selected, git_identity, scratch_target)
            else:
                import shutil
                shutil.copytree(selected, scratch_target, ignore=shutil.ignore_patterns(".git", ".local", "nix-update-*"))
            if loaded_doc is not None:
                from .local_source import validate_explicit_lock_revisions
                validate_explicit_lock_revisions(workflow, loaded_doc.get("lock") or {}, lock_backed_revisions)
            if loaded_doc is not None and loaded_doc.get("lock"):
                from .deployment_plan import serialize_lock_document
                (scratch_target / "flake.lock").write_bytes(serialize_lock_document(loaded_doc["lock"]))
            elif lock_backed_revisions:
                from .local_source import project_candidate_lock
                project_candidate_lock(workflow, scratch_target, lock_backed_revisions)
            local_lock = normalized_lock(scratch_target / "flake.lock") if getattr(workflow, "sources", ()) else {}
            projected_lock_bytes = (scratch_target / "flake.lock").read_bytes() if (scratch_target / "flake.lock").is_file() else b""
            scratch_tree_identity = _scratch_tree_identity(scratch_target.resolve())
            local_sources = capture_local_sources(workflow, local_lock)
            commit_map = {item["source"]["key"]: item["source"]["revision"] for item in local_sources}
            if "commit" in git_identity:
                commit_map["selected"] = git_identity["commit"]

            allowlist = evaluate_allowlist(workflow, scratch_target, overrides)
            scratch_projection = render_scratch(control_dir, scratch_parent / "localConfig", allowlist)
            exact_overrides = list(overrides)
            exact_overrides.extend(["--override-input", "localConfig", f"path:{scratch_projection.root}"])

            effects = evaluate_deployment_effects(workflow, scratch_target, host, exact_overrides)
            is_safe, reasons = classify_deployment_effects(effects)
            if not is_safe:
                msg = "; ".join(reasons)
                raise ConfigError(
                    f"configuration declares exceptional deployment effects; "
                    f"run 'nix-update advanced prepare/apply' for transactional deployment: {msg}"
                )

            sources = derive_source_identities(
                workflow,
                scratch_target,
                exact_overrides,
                selected_flake_identity=selected,
            )

            qualification_root = scratch_parent / "qualification-reports"
            create_private_directory(qualification_root, "qualification report root")
            test_command = _qualification_command(scratch_target, qualification_root, test_jobs, exact_overrides)
            check_result = workflow.command("selected-flake report qualification", test_command, scratch_target)
            if check_result.returncode:
                raise ConfigError(failure_explanation(workflow, "selected-flake report qualification", check_result.returncode, report_root=qualification_root))

            projected_check_command = [
                "nix", "flake", "check", "path:.", "--no-update-lock-file", "--no-write-lock-file", *exact_overrides,
            ]
            # Only the identical fallback argv, executed in the same selected
            # directory above, proves this gate. Do not infer report-hook parity.
            if test_command != projected_check_command:
                projected_check = workflow.command(
                    "exact projected flake check", projected_check_command, scratch_target,
                )
                if projected_check.returncode:
                    raise ConfigError(failure_explanation(workflow, "exact projected flake check", projected_check.returncode))

            build_command = [
                "nix", "build", "--print-out-paths", "--no-link", "--no-update-lock-file", "--no-write-lock-file",
                f"path:.#darwinConfigurations.{host}.system", *exact_overrides,
            ]
            if build_jobs:
                build_command.extend(["--max-jobs", build_jobs])

            build_result = workflow.command("exact Darwin target build", build_command, scratch_target)
            if build_result.returncode:
                raise ConfigError(failure_explanation(workflow, "exact Darwin target build", build_result.returncode))

            paths = [line for line in build_result.stdout.splitlines() if line.startswith("/nix/store/")]
            if len(paths) != 1:
                raise ConfigError("exact Darwin target build did not emit exactly one store path")
            target = paths[0]

            target_closure = closure_inventory(workflow, scratch_target, target)
            live_closure = current_live_closure(workflow, scratch_target, profile)
            machine_closure_delta = _closure_delta(live_closure, target_closure)

            diff_command = ["nix", "store", "diff-closures", profile["live"]["target"], target]
            workflow.command("closure comparison", diff_command, scratch_target)
            diff_text = workflow.complete_stdout("closure comparison")

            preflight_results = run_read_only_preflights(effects, workflow, scratch_target)

            tool_identity = None if dry_run or plan_only else _tool_identity(workflow.core_dir)

            summary = format_deployment_summary(
                selected_flake=selected,
                host=host,
                git_identity=git_identity,
                profile=profile,
                predecessor_generation=predecessor_generation,
                sources=sources,
                effects=effects,
                preflight_results=preflight_results,
                target_system=target,
                live_closure=live_closure,
                target_closure=target_closure,
                diff_closures_text=diff_text,
                status="READY",
                commit_map=commit_map,
            )
            print(summary, flush=True)

            plan_document = {
                "schema": "nix-update-local-plan-v1",
                "selected_flake": str(selected.resolve(strict=False)),
                "host": host,
                "git_authority": git_identity,
                "commit_map": commit_map,
                "target_system": target,
                "predecessor_generation": predecessor_generation,
                "profile_snapshot": profile,
                "sources": sources,
                "effects": effects,
                "preflight_results": preflight_results,
                "closure_delta": machine_closure_delta,
                "created_at": _now(),
            }
            if getattr(workflow, "sources", ()):
                source_snapshot_evidence = {
                    "active": effective.active,
                    "configured_revisions": dict(effective.configured_revisions) if effective.active and effective.configured_revisions else None,
                    "effective_revisions": dict(sorted(effective.by_key.items())),
                    "provenance": dict(sorted(effective.provenance_by_key.items())),
                    "selected_source": {
                        "key": selected_key,
                        "revision": git_identity["commit"],
                    } if selected_key is not None and "commit" in git_identity else None,
                    "lock_backed_revisions": dict(sorted(lock_backed_revisions.items())),
                }
                plan_document.update(
                    schema="nix-update-local-plan-v3",
                    local_sources=local_sources,
                    lock=local_lock,
                    source_snapshot=source_snapshot_evidence,
                )
                revalidate_local_sources(workflow, local_lock, local_sources)

            if dry_run:
                return 0

            if plan_only:
                create_private_directory(plan_output, "plan output directory")
                write_local_plan(plan_output, plan_document, summary)
                print(f"nix-update: wrote local deployment plan to {plan_output}", flush=True)
                return 0

            if loaded_doc is not None:
                if loaded_doc.get("commit_map") is not None and loaded_doc.get("commit_map") != commit_map:
                    raise ConfigError("source commit map changed since reviewed local plan was created")
                if loaded_doc.get("schema") in {"nix-update-local-plan-v2", "nix-update-local-plan-v3"}:
                    revalidate_local_sources(workflow, loaded_doc.get("lock", {}), loaded_doc.get("local_sources", []))
                if loaded_doc.get("target_system") != target:
                    raise ConfigError("target system changed since reviewed local plan was created")
                if loaded_doc.get("selected_flake") != str(selected.resolve(strict=False)):
                    raise ConfigError("selected flake does not match reviewed local plan")
                if loaded_doc.get("host") != host:
                    raise ConfigError("host does not match reviewed local plan")
                plan_git_auth = loaded_doc.get("git_authority", {})
                if plan_git_auth.get("commit") != git_identity.get("commit"):
                    raise ConfigError("Git authority changed since reviewed local plan was created")
                if loaded_doc.get("profile_snapshot") != profile:
                    raise ConfigError("profile state changed since reviewed local plan was created")
                if loaded_doc.get("predecessor_generation") != predecessor_generation:
                    raise ConfigError("predecessor generation changed since reviewed local plan was created")
                if loaded_doc.get("sources") != sources:
                    raise ConfigError("source authority changed since reviewed local plan was created")
                if loaded_doc.get("effects") != effects:
                    raise ConfigError("deployment effects changed since reviewed local plan was created")
                if loaded_doc.get("preflight_results") != preflight_results:
                    raise ConfigError("preflight results changed since reviewed local plan was created")
                if loaded_doc.get("closure_delta") != machine_closure_delta:
                    raise ConfigError("closure delta changed since reviewed local plan was created")

            def _check_scratch_integrity() -> None:
                if (scratch_target / "flake.lock").is_file():
                    if (scratch_target / "flake.lock").read_bytes() != projected_lock_bytes:
                        raise ConfigError("projected scratch lock drifted before local deployment")
                    if getattr(workflow, "sources", ()) and normalized_lock(scratch_target / "flake.lock") != local_lock:
                        raise ConfigError("projected scratch lock drifted before local deployment")
                elif projected_lock_bytes:
                    raise ConfigError("projected scratch lock drifted before local deployment")
                if _scratch_tree_identity(scratch_target.resolve()) != scratch_tree_identity:
                    raise ConfigError("projected scratch target tree drifted before local deployment")

            def revalidate(expected_profile: dict[str, Any]) -> None:
                revalidate_local_sources(workflow, local_lock, local_sources)
                _check_scratch_integrity()
                if evaluate_allowlist(workflow, scratch_target, overrides) != allowlist:
                    raise ConfigError("configuration allowlist drifted before local deployment")
                # Check executable/source authority before evaluating, and again at the boundary.
                if _tool_identity(workflow.core_dir) != tool_identity:
                    raise ConfigError("deployment tool or executable identity drifted before local deployment")
                _check_selected_git_authority(workflow, selected, git_identity)
                if evaluate_deployment_effects(workflow, scratch_target, host, exact_overrides) != effects:
                    raise ConfigError("module-owned deployment effects drifted before local deployment")
                if (
                    derive_source_identities(
                        workflow,
                        scratch_target,
                        exact_overrides,
                        selected_flake_identity=selected,
                    )
                    != sources
                ):
                    raise ConfigError("source authority drifted before local deployment")
                rebuilt = workflow.command("local target revalidation", build_command, scratch_target)
                if rebuilt.returncode or [line for line in rebuilt.stdout.splitlines() if line.startswith("/nix/store/")] != [target]:
                    raise ConfigError("target system drifted before local deployment")
                if closure_inventory(workflow, scratch_target, target) != target_closure:
                    raise ConfigError("target closure inventory drifted before local deployment")
                if run_read_only_preflights(effects, workflow, scratch_target) != preflight_results:
                    raise ConfigError("module-owned preflight results drifted before local deployment")
                _guard_processes([], strict=True)
                _check_scratch_integrity()
                _check_selected_git_authority(workflow, selected, git_identity)
                if tree_identity(control_dir) != control_identity:
                    raise ConfigError("machine-local control state drifted before local deployment")
                if inspect_scratch(scratch_projection.root) != scratch_projection:
                    raise ConfigError("machine-local projection drifted before local deployment")
                if _tool_identity(workflow.core_dir) != tool_identity:
                    raise ConfigError("deployment tool or executable identity drifted before local deployment")
                if inspect_profile(profile_path, live_path, generations_glob) != expected_profile:
                    raise ConfigError("selected-profile or live-system state drifted before local deployment")

            same_target = bool(
                profile.get("live", {}).get("target")
                and target == profile["live"]["target"]
                and not machine_closure_delta.get("added")
                and not machine_closure_delta.get("removed")
            )
            if same_target:
                print(
                    "nix-update: notice: target system equals live system; reapplication will run activation hooks with no package or closure change",
                    flush=True,
                )

            confirmed = require_terminal_confirmation()
            if not confirmed:
                print("nix-update: deployment confirmation refused; no host mutation attempted", file=sys.stderr, flush=True)
                return 1

            revalidate(profile)
            sudo_path = tool_identity["external"]["sudo"]["resolved"]
            # nix-env is a basename-sensitive multicall endpoint. Revalidation
            # binds this executable invocation path AND its resolved bytes.
            nix_env_path = tool_identity["external"]["nix-env"]["path"]
            darwin_rebuild_path = tool_identity["external"]["darwin-rebuild"]["resolved"]
            if not ensure_sudo_credentials(sudo_path):
                print("nix-update: unable to acquire sudo credentials; deployment stopped", file=sys.stderr, flush=True)
                return 1

            switch_command = [
                sudo_path,
                "HOMEBREW_NO_AUTO_UPDATE=1",
                darwin_rebuild_path,
                "switch",
                "--flake",
                f"path:{scratch_target.resolve(strict=False)}#{host}",
                "--no-update-lock-file",
                "--no-write-lock-file",
                *exact_overrides,
            ]
            switch_flake_marker = _switch_flake_marker(switch_command, scratch_target, selected)

            result_dir = state_root / f"run-{os.getpid()}-{os.urandom(6).hex()}"
            create_private_directory(result_dir, "deployment result directory")
            durable_marker(result_dir, "local-mutation-authority.json", {
                "target": target,
                "predecessor": predecessor_generation,
                "predecessor_target": profile["live"]["target"],
                "switch_flake": switch_flake_marker,
                "started_at": _now(),
            })
            # Authentication may have waited indefinitely; revalidate after it as well.
            terminal_state = "PRE_SWITCH_FAILURE"
            exit_status = 1
            error_message = None
            switch_invoked = False
            realignment_invoked = False
            try:
                revalidate(profile)
                if profile.get("relationship") == "split-retained":
                    realignment = realignment_command(
                        Path(profile["selected"]["path"]), predecessor_generation
                    )
                    realign_cmd = [sudo_path, nix_env_path, *realignment[1:]]
                    realignment_invoked = True
                    realign_res = workflow.command("selected-profile realignment", realign_cmd, selected)
                    if realign_res.returncode:
                        raise ConfigError(failure_explanation(
                            workflow, "selected-profile realignment", realign_res.returncode
                        ))
                    aligned = inspect_profile(profile_path, live_path, generations_glob)
                    if (aligned.get("live") != profile.get("live")
                            or aligned.get("retained") != profile.get("retained")
                            or aligned.get("selected", {}).get("target") != profile["live"]["target"]
                            or aligned.get("selected", {}).get("generation_number") != predecessor_generation):
                        raise ConfigError("selected-profile realignment did not preserve the live predecessor")
                    revalidate(aligned)

                terminal_state = "POST_INVOCATION_FAILURE"
                switch_invoked = True
                switch_result = workflow.command("single Darwin switch", switch_command, scratch_target)
                exit_status = switch_result.returncode
                if exit_status:
                    error_message = "darwin-rebuild switch failed; no automatic restoration or second switch attempted"
                else:
                    terminal_state = "POST_SWITCH_VERIFICATION_FAILURE"
                    exit_status = 1
                    post_profile = inspect_profile(profile_path, live_path, generations_glob)
                    error_message = _post_switch_error(
                        post_profile, target, predecessor_generation, profile["live"]["target"]
                    )
                    if error_message is None:
                        terminal_state = "SUCCESS"
                        exit_status = 0
            except (Exception, KeyboardInterrupt) as error:
                # After invoking a mutator, even a transport/evidence exception can mean
                # the host changed. Report the attempted boundary without retrying it.
                error_message = f"{type(error).__name__}: {redact_text(str(error))[:2048]}"
                exit_status = 1

            journal = _journal_document(
                selected, host, target, predecessor_generation,
                terminal_state, exit_status, error_message,
                switch_flake=switch_flake_marker,
            )
            journal["switch_invoked"] = switch_invoked
            journal["realignment_invoked"] = realignment_invoked
            journal["predecessor_target"] = profile["live"]["target"]
            journal["closure_delta"] = machine_closure_delta
            journal["same_target_reapplication"] = same_target
            try:
                durable_marker(result_dir, "journal.json", journal)
            except (OSError, ConfigError) as error:
                print(
                    f"nix-update: {terminal_state}; unable to persist terminal journal "
                    f"({type(error).__name__}); mutation authority record retained at {result_dir}",
                    file=sys.stderr, flush=True,
                )
                return 1
            if exit_status:
                print(f"nix-update: {terminal_state}: {error_message}; journal: {result_dir}", file=sys.stderr, flush=True)
                return 1
            if same_target:
                print(
                    f"nix-update: deployment successful: active system {target} (reapplication of already-selected system; no closure delta)",
                    flush=True,
                )
            else:
                print(f"nix-update: deployment successful: active system {target}", flush=True)
            return 0
