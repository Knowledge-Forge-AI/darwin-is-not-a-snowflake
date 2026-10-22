from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .config import ControlConfig, load_control_config, operator_home, resolve_effective_source_revisions
from .deployment_workflow import Workflow, apply, build_only, maintain_lock, prepare
from .local_deployment import deploy_local
from .paths import ConfigError, resolve_portable_path, validate_portable_path
from .promotion_workflow import apply_promotion, prepare_promotion


EX_CONFIG = 78


@dataclass(frozen=True)
class Selection:
    path: Path
    source: str
    config: ControlConfig
    warnings: tuple[str, ...]


class PhaseFailure(RuntimeError):
    def __init__(self, status: int):
        self.status = status


def resolve_selection(
    core_dir: Path,
    explicit_path: str | None,
    config_path: Path,
    environment: Mapping[str, str],
    home: Path,
    allow_incomplete_snapshot: bool = False,
) -> Selection:
    config = load_control_config(
        config_path, operator_home=home, allow_incomplete_snapshot=allow_incomplete_snapshot
    )
    if explicit_path is not None:
        selected = resolve_portable_path(explicit_path, home)
        source = "--flake"
    elif config.active is not None:
        selected = config.active
        source = "flake.active"
    else:
        selected = core_dir
        source = "core"
    selected = selected.resolve(strict=False)
    if not selected.is_dir() or not (selected / "flake.nix").is_file():
        raise ConfigError(f"selected flake is missing or invalid ({source}): {selected}")
    warnings: list[str] = []
    visible = environment.get("NIX_DARWIN_ACTIVE_FLAKE", "")
    if visible and Path(visible).expanduser().resolve(strict=False) != selected:
        warnings.append(f"NIX_DARWIN_ACTIVE_FLAKE disagrees with the selected flake; using {selected}")
    locator = environment.get("NIX_DARWIN_FLAKE_DIR", "")
    if locator and Path(locator).expanduser().resolve(strict=False) != selected:
        warnings.append(
            "NIX_DARWIN_FLAKE_DIR locates core and no longer selects the build target; "
            f"using {selected}"
        )
    return Selection(selected, source, config, tuple(warnings))


def require_activating_selection(selection: Selection, build: bool) -> None:
    if build or selection.source != "core":
        return
    raise ConfigError("no activating top-level flake selected; set flake.active or pass --flake")


def override_arguments(
    config: ControlConfig,
    local_config_generation: Path | None,
    allow_core_override: bool,
    build: bool,
) -> list[str]:
    overrides = dict(config.overrides)
    if "localConfig" in overrides:
        raise ConfigError("flake.overrides.localConfig is reserved for evaluation projection")
    core_inputs = [name for name in overrides if name.split("/")[-1] == "core"]
    if core_inputs and not allow_core_override:
        raise ConfigError("core checkout override requires --allow-core-override")
    if core_inputs and not build:
        raise ConfigError("core checkout override is build-only and cannot enter a deployment plan")
    if local_config_generation is not None:
        overrides["localConfig"] = local_config_generation
    arguments: list[str] = []
    for input_path, checkout in sorted(overrides.items()):
        if not checkout.is_dir() or not ((checkout / "flake.nix").is_file() or (checkout / ".git").exists()):
            raise ConfigError(f"override checkout is missing or invalid ({input_path}): {checkout}")
        arguments.extend(["--override-input", input_path, f"path:{checkout}"])
    return arguments


def selected_test_command(selected: Path, test_jobs: str) -> list[str]:
    candidate = selected / "bin/nix-test"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        command = [str(candidate), "--report"]
        if test_jobs:
            command.extend(["--jobs", test_jobs])
        return command
    return ["nix", "flake", "check", "path:.", "--no-update-lock-file", "--no-write-lock-file"]


def main(core_dir: Path, argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    started = time.monotonic()
    timing = "--timing" in raw
    try:
        raw = _migrate_legacy_argv(raw)
        args = _parser().parse_args(raw)
        return _run(core_dir.resolve(), args, os.environ)
    except ConfigError as error:
        print(f"nix-update: {error}", file=sys.stderr, flush=True)
        return EX_CONFIG
    except PhaseFailure as error:
        return error.status
    finally:
        if timing:
            print(f"nix-update: timing: total: {int(time.monotonic() - started)}s", flush=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nix-update",
        description="Qualify, build, inspect, and deploy nix-darwin configurations.",
        epilog=(
            "nix-update performs attended local deployment of the active top-level flake. "
            "Use --dry-run to qualify and inspect without mutation, or --plan-only to generate "
            "a review artifact. Legacy contract/participant workflows are under 'advanced'. "
            "NIX_DARWIN_BUILD_JOBS and NIX_DARWIN_TEST_JOBS remain supported; "
            "there is no production test-command or confirmation bypass."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    deploy = commands.add_parser("deploy", help="attended local nix-darwin deployment")
    _selection_arguments(deploy)
    deploy.add_argument("--dry-run", action="store_true", help="build and inspect without host mutation")
    deploy.add_argument("--plan-only", action="store_true", help="write immutable review artifact and stop")
    deploy.add_argument("--output", type=Path, metavar="DIR", help="output directory for --plan-only")
    deploy.add_argument("--plan", type=Path, metavar="PLAN", help="optional pre-generated local deployment plan")

    build = commands.add_parser("build", help="run report qualification and a locked non-privileged build")
    _selection_arguments(build)
    lock = commands.add_parser("lock", help="perform isolated explicit lock maintenance")
    lock.add_argument("--flake", metavar="PATH")
    lock.add_argument("--mode", choices=("locked", "targeted", "all"), required=True)
    lock.add_argument("--input", action="append", default=[])
    lock.add_argument("--allow-node", action="append", default=[])
    lock.add_argument("--output", required=True, type=Path)
    lock.add_argument("--install", action="store_true", help="atomically install the reviewed candidate lock")
    promote = commands.add_parser(
        "promote", help="prepare or apply one reviewed composition promotion transaction"
    )
    promotion_commands = promote.add_subparsers(dest="promotion_command", required=True)
    publish = promotion_commands.add_parser("publish", help="explicitly publish exact catalog commits in dependency order")
    publish.add_argument("--source", required=True, help="exact logical source catalog key")
    publish.add_argument("--revision", required=True, help="exact local release commit")
    publish.add_argument("--dry-run", action="store_true", help="render the publication order without network queries or mutation")
    campaign = promotion_commands.add_parser(
        "campaign", help="coordinate composition promotion and stop before host activation"
    )
    campaign_commands = campaign.add_subparsers(dest="campaign_command", required=True)
    campaign_run = campaign_commands.add_parser("run", help="advance one exact input through the selected composition chain")
    campaign_run.add_argument("--input", required=True)
    campaign_run.add_argument("--revision", required=True)
    campaign_run.add_argument("--flake", metavar="TOP")
    campaign_run.add_argument("--dry-run", action="store_true")
    campaign_run.add_argument("--output", type=Path, metavar="DIRECTORY", help="optional owner-private campaign directory")
    for name in ("status", "resume"):
        command = campaign_commands.add_parser(name)
        command.add_argument("--campaign", type=Path, metavar="DIRECTORY")
        command.add_argument("--flake", metavar="TOP", help="select matching active topology")
    promotion_prepare = promotion_commands.add_parser(
        "prepare", help="qualify and retain one exact non-mutating lock promotion candidate"
    )
    promotion_prepare.add_argument("--flake", required=True, metavar="PATH")
    promotion_prepare.add_argument("--input", required=True)
    promotion_prepare.add_argument("--revision", required=True)
    promotion_prepare.add_argument("--output", required=True, type=Path)
    promotion_prepare.add_argument("--timeout-seconds", type=_promotion_timeout)
    promotion_prepare.add_argument(
        "--allow-core-repository-transition",
        action="store_true",
        help="authorize the exact one-time core repository identity transition from .flakes to darwin-is-not-a-snowflake (requires sources.core catalog prerequisite; independent of $HOME/.flakes)",
    )
    promotion_prepare.add_argument(
        "--allow-composition-repository-transition",
        action="store_true",
        help="authorize the exact one-time public composition repository identity transition from composition-nd to composition_dinas (requires sources catalog prerequisite)",
    )
    promotion_apply = promotion_commands.add_parser(
        "apply", help="install and commit one reviewed promotion plan (legacy plans also publish)"
    )
    promotion_apply.add_argument("plan", type=Path)
    optimize = commands.add_parser(
        "optimize",
        help="run separately authorized post-success Nix maintenance",
        description="Run separately authorized post-success Nix maintenance outside deployment.",
    )
    optimize.add_argument("--dry-run", action="store_true")
    optimize.add_argument("--keep", type=_positive_integer)

    advanced = commands.add_parser(
        "advanced",
        help="exceptional contract-driven and participant deployment operations",
        description="Run exceptional contract-driven or participant-supervised deployment operations.",
    )
    advanced_commands = advanced.add_subparsers(dest="advanced_command", required=True)
    adv_prepare = advanced_commands.add_parser("prepare", help="create an immutable non-privileged contract deployment plan")
    _selection_arguments(adv_prepare)
    adv_prepare.add_argument("--contract", required=True, type=Path)
    adv_prepare.add_argument("--output", required=True, type=Path)
    adv_prepare.add_argument("--preflight-only", action="store_true")
    adv_apply = advanced_commands.add_parser("apply", help="apply one exact reviewed contract deployment plan")
    adv_apply.add_argument("plan", type=Path)

    snapshot = commands.add_parser(
        "snapshot",
        help="inspect and adopt configured source snapshots",
        description="Inspect and adopt configured source snapshots.",
    )
    snapshot_commands = snapshot.add_subparsers(dest="snapshot_command", required=True)
    snapshot_status = snapshot_commands.add_parser("status", help="show configured source snapshot status and local HEADs")
    snapshot_status.add_argument("--flake", metavar="PATH")
    snapshot_status.add_argument(
        "--plan",
        type=Path,
        metavar="PATH",
        help="inspect candidate pins from a prepared adoption plan against status",
    )

    snapshot_prepare = snapshot_commands.add_parser(
        "prepare", help="prepare an immutable source snapshot adoption plan"
    )
    snapshot_prepare.add_argument(
        "--campaign",
        type=Path,
        metavar="PATH",
        help="path to completed promotion campaign directory",
    )
    snapshot_prepare.add_argument(
        "--pin",
        action="append",
        default=[],
        metavar="KEY=REV",
        help="explicit source pin override (KEY=REV)",
    )
    snapshot_prepare.add_argument(
        "--output",
        type=Path,
        metavar="DIR",
        help="owner-private destination directory for adoption plan",
    )
    snapshot_prepare.add_argument(
        "--flake",
        metavar="PATH",
        help="optional top-level flake selector to resolve default campaign",
    )

    snapshot_apply = snapshot_commands.add_parser(
        "apply", help="apply one exact reviewed source snapshot adoption plan"
    )
    snapshot_apply.add_argument(
        "plan",
        type=Path,
        help="path to reviewed snapshot adoption plan directory or plan.json",
    )
    return parser


def _selection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--flake", metavar="PATH")
    parser.add_argument("--allow-core-override", action="store_true")
    parser.add_argument("--test-jobs", metavar="N", type=_positive_integer)
    parser.add_argument("--build-jobs", metavar="N", type=_positive_integer)
    parser.add_argument(
        "--source-revision",
        action="append",
        default=[],
        metavar="KEY=REV",
        help="ephemeral catalog lock-source revision override (KEY=REV)",
    )
    parser.add_argument("--timing", action="store_true")


def _migrate_legacy_argv(arguments: list[str]) -> list[str]:
    if not arguments:
        return ["deploy"]
    if "--switch-only" in arguments:
        raise ConfigError("--switch-only is disabled; use 'nix-update' for attended deployment")
    if "--preserve-predecessor" in arguments:
        raise ConfigError("--preserve-predecessor is now mandatory for apply and is no longer an option")
    if arguments[0] == "prepare":
        raise ConfigError("legacy 'prepare' has moved to 'nix-update advanced prepare'; for normal attended deployment, run 'nix-update' without a contract")
    if arguments[0] == "apply":
        raise ConfigError("legacy 'apply' has moved to 'nix-update advanced apply'; for normal attended deployment, run 'nix-update' without a contract")
    if "--build-only" in arguments:
        if any(item in {"build", "deploy", "lock", "promote", "optimize", "advanced", "snapshot"} for item in arguments):
            raise ConfigError("--build-only cannot be combined with a subcommand")
        return ["build", *(item for item in arguments if item != "--build-only")]
    subcommands = {"build", "deploy", "lock", "promote", "optimize", "advanced", "snapshot"}
    if arguments[0] not in subcommands and arguments[0] not in {"-h", "--help"}:
        return ["deploy", *arguments]
    return arguments


def _positive_integer(value: str) -> int:
    if not value.isdigit() or int(value) <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return int(value)


def _promotion_timeout(value: str) -> int:
    parsed = _positive_integer(value)
    if parsed > 3600:
        raise argparse.ArgumentTypeError("must be between 1 and 3600")
    return parsed


def parse_source_revisions(
    items: Sequence[str],
    sources: Any,
) -> dict[str, str]:
    if not items:
        return {}
    from .source_catalog import coerce_source_catalog
    catalog = coerce_source_catalog(sources)
    if not catalog:
        raise ConfigError("--source-revision requires configured [sources] catalog")
    from .promotion_contract import HEX_REVISION
    result: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ConfigError(f"invalid --source-revision format: {item!r}, expected KEY=REV")
        key, rev = item.split("=", 1)
        key = key.strip()
        rev = rev.strip().lower()
        if not key or HEX_REVISION.fullmatch(rev) is None:
            raise ConfigError(f"invalid --source-revision format: {item!r}, expected KEY=40-hex-commit")
        if key not in catalog:
            raise ConfigError(f"unknown source key in --source-revision: {key!r}")
        if key in result:
            raise ConfigError(f"duplicate --source-revision for key: {key!r}")
        result[key] = rev
    return result


def _run(core_dir: Path, args: argparse.Namespace, environment: Mapping[str, str]) -> int:
    if args.command == "advanced" and args.advanced_command == "apply":
        return apply(_configured_workflow(core_dir, environment), args.plan)
    if args.command == "promote" and args.promotion_command == "apply":
        return apply_promotion(_configured_workflow(core_dir, environment), args.plan)
    if args.command == "promote" and args.promotion_command == "publish":
        from .source_publication import publish_sources
        import json
        workflow = _configured_workflow(core_dir, environment)
        result = publish_sources(workflow, workflow.sources, [(args.source, args.revision)], dry_run=args.dry_run)
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0 if result.get("status") in {"DRY_RUN", "PUBLISHED"} else 1
    if args.command == "optimize":
        arguments = [str(core_dir / "bin/nix-opt")]
        if args.dry_run:
            arguments.append("--dry-run")
        if args.keep is not None:
            arguments.extend(["--keep", str(args.keep)])
        return subprocess.run(arguments, check=False).returncode
    home = operator_home()
    local_root = _local_root(environment, home)
    if args.command == "snapshot":
        if args.snapshot_command == "status":
            from .source_snapshot_status import show_snapshot_status
            return show_snapshot_status(
                core_dir,
                args.flake,
                local_root / "config/nix-darwin.toml",
                environment,
                home,
                plan_path=args.plan,
            )
        if args.snapshot_command == "prepare":
            if args.campaign and args.pin:
                raise ConfigError("cannot combine --campaign and --pin; specify either a campaign or explicit pin overrides")
            from .source_snapshot_adoption import prepare_snapshot_adoption
            workflow = _configured_workflow(core_dir, environment, allow_incomplete_snapshot=True)
            plan_dir = prepare_snapshot_adoption(
                workflow,
                local_root / "config/nix-darwin.toml",
                campaign_dir=args.campaign,
                explicit_pins=args.pin,
                output_dir=args.output,
                flake_selector=args.flake,
                home=home,
            )
            print(f"nix-update: adoption plan READY_FOR_REVIEW: {plan_dir}", flush=True)
            return 0
        if args.snapshot_command == "apply":
            from .source_snapshot_adoption import apply_snapshot_adoption
            workflow = _configured_workflow(core_dir, environment, allow_incomplete_snapshot=True)
            return apply_snapshot_adoption(
                workflow,
                args.plan,
                home=home,
            )
    if args.command == "promote" and args.promotion_command == "campaign":
        return _run_campaign(core_dir, args, environment, home, local_root)
    selection = resolve_selection(core_dir, args.flake, local_root / "config/nix-darwin.toml", environment, home)
    for warning in selection.warnings:
        print(f"nix-update: warning: {warning}", file=sys.stderr, flush=True)
    if args.command == "lock":
        return maintain_lock(
            Workflow(core_dir), selection.path, args.mode, args.input, args.allow_node, args.output, args.install
        )
    if args.command == "promote":
        kwargs: dict[str, Any] = {}
        if getattr(args, "allow_core_repository_transition", False):
            kwargs["allow_core_repository_transition"] = True
        if getattr(args, "allow_composition_repository_transition", False):
            kwargs["allow_composition_repository_transition"] = True
        artifact = prepare_promotion(
            Workflow(core_dir, sources=selection.config.sources), selection.path, args.input, args.revision,
            args.output, args.timeout_seconds,
            **kwargs,
        )
        print(f"nix-update: promotion plan {artifact.document['status']}: {artifact.directory}", flush=True)
        return 1 if artifact.document["status"] == "BLOCKED" else 0
    require_activating_selection(selection, args.command == "build")
    build_jobs = str(args.build_jobs) if args.build_jobs is not None else environment.get("NIX_DARWIN_BUILD_JOBS", "")
    test_jobs = str(args.test_jobs) if args.test_jobs is not None else environment.get("NIX_DARWIN_TEST_JOBS", "")
    _validate_environment_jobs(build_jobs, test_jobs)
    if getattr(args, "source_revision", None):
        if args.command == "advanced" and getattr(args, "advanced_command", None) == "prepare":
            raise ConfigError("--source-revision is not supported for 'nix-update advanced prepare'")
    source_revisions = parse_source_revisions(
        getattr(args, "source_revision", []), selection.config.sources
    )
    effective = resolve_effective_source_revisions(
        selection.config, source_revisions
    )
    overrides = override_arguments(
        selection.config, None, args.allow_core_override, args.command == "build"
    )
    host = _darwin_config(environment)
    workflow = Workflow(
        core_dir, display_command_output=args.command != "deploy", sources=selection.config.sources
    )
    if args.command == "build":
        return build_only(
            workflow,
            selection.path,
            host,
            local_root / "config",
            overrides,
            test_jobs,
            build_jobs,
            effective,
        )
    if args.command == "deploy":
        return deploy_local(
            workflow,
            selection.path,
            host,
            local_root / "config",
            overrides,
            test_jobs,
            build_jobs,
            dry_run=args.dry_run,
            plan_only=args.plan_only,
            plan_output=args.output,
            plan_path=args.plan,
            source_revisions=effective,
        )
    # args.command == "advanced" and args.advanced_command == "prepare"
    if source_revisions:
        raise ConfigError("--source-revision is not supported for 'nix-update advanced prepare'")
    artifact = prepare(
        workflow,
        selection.path,
        host,
        local_root / "config",
        args.contract,
        args.output,
        overrides,
        test_jobs,
        build_jobs,
        args.preflight_only,
    )
    print(f"nix-update: plan {artifact.document['status']}: {artifact.directory}", flush=True)
    return 1 if artifact.document["blockers"] else 0


def _configured_workflow(
    core_dir: Path,
    environment: Mapping[str, str],
    allow_incomplete_snapshot: bool = False,
) -> Workflow:
    home = operator_home()
    config = load_control_config(
        _local_root(environment, home) / "config/nix-darwin.toml",
        operator_home=home,
        allow_incomplete_snapshot=allow_incomplete_snapshot,
    )
    return Workflow(core_dir, sources=config.sources)


def _run_campaign(core_dir, args, environment, home, local_root) -> int:
    from .promotion_campaign import run_campaign, resume_campaign, status_campaign

    # Status reads journals even when their source checkouts are unavailable.
    if args.campaign_command == "status":
        if args.campaign is not None:
            return status_campaign(None, args.campaign)
        config = load_control_config(local_root / "config/nix-darwin.toml", operator_home=home)
        selected = resolve_portable_path(args.flake, home) if args.flake else config.active
        return status_campaign(selected)
    selection = resolve_selection(
        core_dir, args.flake, local_root / "config/nix-darwin.toml", environment, home
    )
    workflow = Workflow(core_dir, sources=selection.config.sources)
    if args.campaign_command == "resume":
        return resume_campaign(workflow, selection.path, selection.config, args.campaign)
    return run_campaign(
        workflow, selection.path, selection.config, args.input, args.revision,
        output=args.output, dry_run=args.dry_run,
    )


def _validate_environment_jobs(build_jobs: str, test_jobs: str) -> None:
    for label, value in (("NIX_DARWIN_BUILD_JOBS", build_jobs), ("NIX_DARWIN_TEST_JOBS", test_jobs)):
        if value:
            try:
                _positive_integer(value)
            except argparse.ArgumentTypeError as error:
                raise ConfigError(f"{label} must be a positive integer") from error


def _local_root(environment: Mapping[str, str], home: Path) -> Path:
    configured = environment.get("NIX_DARWIN_LOCAL", "")
    if not configured:
        return home / ".local/nix-darwin"
    validate_portable_path(configured)
    return resolve_portable_path(configured, home)


def _darwin_config(environment: Mapping[str, str]) -> str:
    configured = environment.get("NIX_DARWIN_CONFIG", "")
    if configured:
        return configured
    for command in (
        ["scutil", "--get", "ComputerName"],
        ["scutil", "--get", "LocalHostName"],
        ["hostname", "-s"],
    ):
        result = subprocess.run(command, check=False, stdout=subprocess.PIPE, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    raise ConfigError("unable to determine Darwin configuration name")
