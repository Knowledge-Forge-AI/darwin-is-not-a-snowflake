"""Operational adoption and automated tooling for refreshing [snapshot.sources] pins post-promotion.

SNAPSHOT3 replaces manual editing of nix-darwin.toml with a native, bounded,
reviewable DINAS transaction:
  nix-update snapshot prepare [--campaign DIR] [--pin KEY=REV ...] [--output DIR]
  nix-update snapshot apply <plan>
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shlex
import stat
from typing import Any, Mapping, Sequence

import tomllib

from .config import load_control_config, operator_home
from .deployment_paths import (
    atomic_private_write,
    ensure_private_root,
    exclusive_private_write,
    fsync_directory,
    require_private_directory,
    require_private_file,
    validate_ancestor_chain,
)
from .deployment_plan import canonical_bytes, sha256_bytes
from .deployment_state import DeploymentLock, _atomic_replace
from .deployment_workflow import Workflow
from .local_source import (
    _run_git,
    observe_source,
)
from .local_source_history import check_ancestry
from .paths import ConfigError, resolve_portable_path
from .promotion_campaign import (
    TERMINAL_UNAVAILABLE_ERRNOS,
    TerminalUnavailableError,
    _existing_matches,
    _stage_commit,
)
from .promotion_campaign_state import (
    CAMPAIGN_SCHEMA,
    CAMPAIGN_VERSION,
    LEGACY_CAMPAIGN_SCHEMA,
    load_campaign,
)
from .promotion_contract import HEX_REVISION, strict_json_loads
from .promotion_qualification import promotion_tool_identity
from .source_catalog import SourceCatalog, SourceCatalogEntry

SNAPSHOT_ADOPTION_PLAN_SCHEMA = "nix-update-snapshot-adoption-plan-v1"
SNAPSHOT_ADOPTION_PLAN_VERSION = 1
SNAPSHOT_ADOPTION_RESULT_SCHEMA = "nix-update-snapshot-adoption-result-v1"
SNAPSHOT_ADOPTION_RESULT_VERSION = 1
SNAPSHOT_CONSUMED_SCHEMA = "nix-update-snapshot-consumed-v1"
SNAPSHOT_BACKUP_SCHEMA = "nix-update-snapshot-backup-v1"
CONFIRMATION_TEMPLATE = "APPLY NIX-UPDATE SNAPSHOT {plan_digest}"

VALID_CAMPAIGN_TERMINAL_STATES = frozenset({
    "LOCAL_COMPLETE_AWAITING_ACTIVATION",
    "COMPLETE_NO_ACTIVATION",
    "AWAITING_ACTIVATION",
    "AWAITING_DEPLOYMENT_RECONCILIATION",
})


def _snapshot_tty_confirmation(prompt: str) -> str:
    try:
        with open("/dev/tty", "w", encoding="utf-8", errors="strict") as tty_out, open(
            "/dev/tty", "r", encoding="utf-8", errors="strict"
        ) as tty_in:
            tty_out.write(prompt)
            tty_out.flush()
            line = tty_in.readline()
            if not line:
                raise EOFError("snapshot adoption confirmation stream ended")
            return line.rstrip("\r\n")
    except OSError as error:
        if error.errno in TERMINAL_UNAVAILABLE_ERRNOS:
            raise TerminalUnavailableError(
                "snapshot adoption confirmation requires a controlling terminal"
            ) from error
        raise


def parse_pin_arg(pin: str) -> tuple[str, str]:
    if "=" not in pin:
        raise ConfigError(f"invalid --pin argument (expected KEY=REV): {pin!r}")
    key, sep, rev = pin.partition("=")
    key = key.strip()
    rev = rev.strip().lower()
    if not key:
        raise ConfigError(f"empty source key in --pin argument: {pin!r}")
    if HEX_REVISION.fullmatch(rev) is None:
        raise ConfigError(f"invalid revision in --pin override for {key!r}: {rev!r}, expected 40-hex commit")
    return key, rev


def map_campaign_input_to_source(
    input_name: str,
    catalog: SourceCatalog,
) -> SourceCatalogEntry:
    entry = catalog.get(input_name)
    if entry is not None:
        return entry
    for e in catalog:
        if e.canonical_repository_identity == input_name:
            return e
    for e in catalog:
        if e.checkout.name == input_name:
            return e
    raise ConfigError(
        f"campaign input {input_name!r} does not correspond to any configured source key in catalog"
    )


def map_stage_repository_to_source(
    stage_repo_path: str,
    catalog: SourceCatalog,
) -> SourceCatalogEntry:
    try:
        resolved = Path(stage_repo_path).resolve(strict=False)
    except Exception as error:
        raise ConfigError(f"invalid campaign stage repository path: {stage_repo_path}") from error
    for e in catalog:
        try:
            if e.checkout.resolve(strict=False) == resolved:
                return e
        except Exception:
            continue
    raise ConfigError(
        f"campaign stage repository {stage_repo_path} does not match any configured source checkout"
    )


def extract_campaign_pins(
    campaign_dir: Path,
    catalog: SourceCatalog,
) -> dict[str, Any]:
    campaign_dir = Path(campaign_dir).resolve(strict=False)
    campaign = load_campaign(campaign_dir)
    schema = campaign.get("schema")
    version = campaign.get("version")
    if schema == LEGACY_CAMPAIGN_SCHEMA:
        raise ConfigError(
            f"campaign {campaign_dir} uses legacy schema {schema}; "
            "snapshot adoption requires native v2 promotion campaign journals with local commit authority"
        )
    if schema != CAMPAIGN_SCHEMA or version != CAMPAIGN_VERSION:
        raise ConfigError(
            f"campaign {campaign_dir} has unsupported schema {schema!r} or version {version!r}"
        )
    if campaign.get("dry_run"):
        raise ConfigError(
            f"campaign {campaign_dir} is a dry run; dry-run campaigns do not provide adoption authority"
        )
    state = campaign.get("state")
    if state == "BLOCKED":
        raise ConfigError(f"campaign {campaign_dir} is blocked; cannot adopt pins from a blocked campaign")
    if state not in VALID_CAMPAIGN_TERMINAL_STATES:
        raise ConfigError(
            f"campaign {campaign_dir} is in non-terminal state {state!r}; cannot adopt pins"
        )

    # Truthful reconciliation for AWAITING_DEPLOYMENT_RECONCILIATION
    if state == "AWAITING_DEPLOYMENT_RECONCILIATION":
        stages = campaign.get("stages", [])
        if not stages:
            raise ConfigError(f"campaign {campaign_dir} has no stages")
        for stage in stages:
            st = stage.get("status")
            if st not in {"LOCAL_COMMITTED", "PUBLISHED", "ALREADY_SELECTED"}:
                raise ConfigError(
                    f"campaign {campaign_dir} ended with indeterminate deployment impact; "
                    f"stage {stage.get('name', 'unnamed')} status is {st!r}"
                )

    campaign_pins: dict[str, str] = {}

    input_name = campaign.get("input", "")
    input_rev = campaign.get("revision", "")
    if not isinstance(input_rev, str) or HEX_REVISION.fullmatch(input_rev) is None:
        raise ConfigError(f"campaign {campaign_dir} has invalid promoted revision: {input_rev!r}")
    input_entry = map_campaign_input_to_source(input_name, catalog)
    campaign_pins[input_entry.key] = input_rev

    for stage in campaign.get("stages", []):
        stage_repo = stage.get("repository", "")
        stage_entry = map_stage_repository_to_source(stage_repo, catalog)
        stage_commit = _stage_commit(stage)
        status = stage.get("status")
        if status == "ALREADY_SELECTED":
            # Stage was already pointing to candidate; unchanged
            continue
        if stage_commit:
            if not isinstance(stage_commit, str) or HEX_REVISION.fullmatch(stage_commit) is None:
                raise ConfigError(f"campaign stage in {stage_repo} has invalid commit: {stage_commit!r}")
            campaign_pins[stage_entry.key] = stage_commit
        else:
            raise ConfigError(
                f"campaign stage in {stage_repo} has neither a valid commit nor ALREADY_SELECTED status"
            )

    return {
        "directory": os.fspath(campaign_dir.resolve(strict=True)),
        "digest": campaign.get("campaign_digest"),
        "state": state,
        "input": input_name,
        "revision": input_rev,
        "pins": campaign_pins,
    }


def _format_snapshot_table(candidate_pins: Mapping[str, str]) -> str:
    lines = ["[snapshot.sources]"]
    for key in sorted(candidate_pins.keys()):
        lines.append(f'"{key}" = "{candidate_pins[key]}"')
    return "\n".join(lines) + "\n"


def render_candidate_config(
    preimage_bytes: bytes,
    candidate_pins: Mapping[str, str],
    home: Path,
) -> bytes:
    try:
        raw_text = preimage_bytes.decode("utf-8")
        parsed = tomllib.loads(raw_text)
    except Exception as error:
        raise ConfigError("unable to parse control config preimage as UTF-8 TOML") from error

    lines = raw_text.splitlines(keepends=True)
    snapshot_start: int | None = None
    snapshot_end: int | None = None
    in_snapshot = False

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.match(r"^\[(\[?)(snapshot(?:\.[A-Za-z0-9_.-]+)?)(\]?)\s*(#.*)?$", stripped):
            if not in_snapshot:
                if snapshot_start is not None:
                    raise ConfigError("multiple non-contiguous snapshot table headers found in control config")
                snapshot_start = idx
                in_snapshot = True
        elif re.match(r"^\[\[?[A-Za-z0-9_.-]+\]?\]", stripped):
            if in_snapshot:
                snapshot_end = idx
                in_snapshot = False

    if in_snapshot and snapshot_start is not None:
        snapshot_end = len(lines)

    replacement_table = _format_snapshot_table(candidate_pins)

    if snapshot_start is not None:
        if snapshot_end is None:
            snapshot_end = len(lines)
        new_lines = lines[:snapshot_start] + [replacement_table] + lines[snapshot_end:]
        new_text = "".join(new_lines)
    else:
        stripped_text = raw_text.rstrip("\r\n")
        new_text = stripped_text + ("\n\n" if stripped_text else "") + replacement_table

    candidate_bytes = new_text.encode("utf-8")

    # Finding 5(b): Semantic postimage assertion
    preimage_config = load_control_config(
        preimage_bytes, operator_home=home, allow_incomplete_snapshot=True
    )
    candidate_config = load_control_config(
        candidate_bytes, operator_home=home, allow_incomplete_snapshot=False
    )
    for field in (
        "active", "update_first", "overrides", "enabled_modules",
        "workspace_root", "nbin_root", "nbin_dev_root", "ai_index_root", "dinas_root",
        "promotion_checkouts", "sources",
    ):
        if getattr(candidate_config, field) != getattr(preimage_config, field):
            raise ConfigError(
                f"candidate config altered non-snapshot field {field}: "
                f"{getattr(candidate_config, field)!r} != {getattr(preimage_config, field)!r}"
            )
    if candidate_config.snapshot is None:
        raise ConfigError("candidate config does not contain a valid snapshot table")
    if dict(candidate_config.snapshot.revisions) != dict(candidate_pins):
        raise ConfigError("candidate config snapshot pins do not match derived candidate pins")

    return candidate_bytes


def validate_candidate_pin_local(
    workflow: Workflow,
    entry: SourceCatalogEntry,
    revision: str,
    phase: str,
) -> dict[str, Any]:
    observed = observe_source(
        workflow,
        entry,
        revision,
        expected_repository=entry.repository,
        expected_ref=entry.release_ref,
        require_clean=False,
        require_exact_head=False,
    )

    checkout = Path(entry.checkout)
    ref_label = f"Git verify release_ref {phase} ({entry.key})"
    ref_res = _run_git(
        workflow, ref_label, checkout, "rev-parse", "--verify", f"{entry.release_ref}^{{commit}}"
    )
    if ref_res.returncode != 0:
        raise ConfigError(f"release ref {entry.release_ref} does not exist in {entry.key}")
    release_ref_commit = workflow.complete_stdout(ref_label).strip()

    # Finding 2: Pin must be reachable from (ancestor of or equal to) release_ref
    check_ancestry(workflow, entry, revision, release_ref_commit, phase)

    return {
        "key": entry.key,
        "checkout": os.fspath(checkout),
        "repository": entry.repository,
        "release_ref": entry.release_ref,
        "release_ref_commit": release_ref_commit,
        "candidate_revision": revision,
        "head_revision": observed.head_revision,
        "clean": observed.clean,
    }


def render_plan_markdown(plan: Mapping[str, Any], plan_dir: Path) -> str:
    lines = [
        "# Nix-Darwin Source Snapshot Adoption Plan",
        "",
        f"- **Plan Digest**: `{plan['plan_digest']}`",
        f"- **Status**: `{plan['status']}`",
        f"- **Config Target**: `{plan['config_path']}`",
        f"- **Preimage SHA-256**: `{plan['preimage_sha256']}`",
        f"- **Candidate SHA-256**: `{plan['candidate_sha256']}`",
    ]
    if plan.get("campaign"):
        camp = plan["campaign"]
        lines.append(f"- **Campaign Source**: `{camp['directory']}` (digest: `{camp['digest'][:16]}...`)")
        lines.append(f"- **Campaign Input**: `{camp['input']}` -> `{camp['revision']}`")
    else:
        lines.append("- **Authority**: Operator Explicit Pins")

    lines.extend([
        "",
        "## Source Pins",
        "",
        "| Source Key | Current Pin | Candidate Pin | Status |",
        "| :--- | :--- | :--- | :--- |",
    ])
    for key in sorted(plan["candidate_pins"].keys()):
        cur = plan["current_pins"].get(key, "none")
        cand = plan["candidate_pins"][key]
        st = "**CHANGED**" if cur != cand else "UNCHANGED"
        lines.append(f"| `{key}` | `{cur}` | `{cand}` | {st} |")

    lines.extend([
        "",
        "## Verifications Performed",
        "- Complete coverage: all configured source catalog entries have candidate pins",
        "- Local Git validation: all candidate commits exist in local checkouts",
        "- Release ref reachability: all candidate commits verified as ancestors of release refs",
        "- Config preservation: non-snapshot tables and comments preserved byte-for-byte",
        "- No active Git operations (rebase/merge/locks) in any source repository",
        "",
        "## Apply Instructions",
        "```bash",
        f"nix-update snapshot apply {shlex.quote(os.fspath(plan_dir))}",
        "```",
        f"Attended confirmation string: `APPLY NIX-UPDATE SNAPSHOT {plan['plan_digest']}`",
        "",
        "> [!IMPORTANT]",
        "> Applying this snapshot adoption plan updates `nix-darwin.toml` only.",
        "> It does **not** activate the host generation and does **not** publish source commits.",
        "> To switch the live system after adoption, run `nix-update deploy`.",
        "",
    ])
    return "\n".join(lines)


def prepare_snapshot_adoption(
    workflow: Workflow,
    config_path: Path,
    campaign_dir: Path | None = None,
    explicit_pins: Sequence[str] = (),
    output_dir: Path | None = None,
    flake_selector: str | None = None,
    home: Path | None = None,
) -> Path:
    home = home if home is not None else operator_home()
    config_path = Path(config_path).resolve(strict=False)
    if not config_path.exists():
        raise ConfigError(f"control config file does not exist: {config_path}")
    validate_ancestor_chain(config_path, owner_only=False)
    metadata = config_path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ConfigError(f"control config is not a regular file: {config_path}")
    if stat.S_ISLNK(metadata.st_mode):
        raise ConfigError(f"control config must not be a symlink: {config_path}")
    if metadata.st_nlink != 1:
        raise ConfigError(f"control config has multiple hard links: {config_path}")
    if metadata.st_uid != os.getuid():
        raise ConfigError(f"control config is not owned by current user: {config_path}")
    pre_mode = stat.S_IMODE(metadata.st_mode)

    preimage_bytes = config_path.read_bytes()
    preimage_sha256 = sha256_bytes(preimage_bytes)

    config = load_control_config(preimage_bytes, operator_home=home, allow_incomplete_snapshot=True)
    catalog = config.source_catalog
    if not catalog.entries:
        raise ConfigError("source catalog is empty; cannot prepare snapshot adoption")

    base_pins: dict[str, str] = dict(config.snapshot.revisions) if config.snapshot else {}
    campaign_record: dict[str, Any] | None = None

    if campaign_dir is not None and explicit_pins:
        raise ConfigError("cannot combine --campaign and --pin; specify either a campaign or explicit pin overrides")

    if campaign_dir is None and not explicit_pins:
        state_root = home / ".local/state/nix-update"
        selected = resolve_portable_path(flake_selector, home) if flake_selector else config.active
        if selected is not None and state_root.exists():
            matches = _existing_matches(state_root, selected, None, None, dry_run=False)
            if len(matches) == 1:
                campaign_dir = matches[0]
            elif len(matches) > 1:
                raise ConfigError(
                    "multiple matching promotion campaigns are indexed; choose one with --campaign <PATH>"
                )

    if campaign_dir is not None:
        campaign_record = extract_campaign_pins(Path(campaign_dir), catalog)

    explicit_pin_map: dict[str, str] = {}
    for pin_str in explicit_pins:
        k, r = parse_pin_arg(pin_str)
        if k not in catalog:
            raise ConfigError(f"unknown source key in --pin override: {k!r}")
        explicit_pin_map[k] = r

    if not campaign_record and not explicit_pin_map:
        raise ConfigError(
            "no promotion campaign or explicit pins provided; specify --campaign <PATH> or --pin KEY=REV"
        )

    candidate_pins = dict(base_pins)
    if campaign_record:
        candidate_pins.update(campaign_record["pins"])
    candidate_pins.update(explicit_pin_map)

    catalog_keys = set(catalog.keys)
    cand_keys = set(candidate_pins.keys())
    missing = sorted(catalog_keys - cand_keys)
    if missing:
        raise ConfigError(
            f"candidate snapshot is missing configured source keys: {', '.join(missing)}"
        )
    unknown = sorted(cand_keys - catalog_keys)
    if unknown:
        raise ConfigError(
            f"candidate snapshot contains unknown source keys: {', '.join(unknown)}"
        )

    sources_authority: dict[str, Any] = {}
    for key in sorted(candidate_pins.keys()):
        rev = candidate_pins[key]
        entry = catalog[key]
        auth = validate_candidate_pin_local(workflow, entry, rev, "snapshot-adoption-prepare")
        sources_authority[key] = auth

    candidate_bytes = render_candidate_config(preimage_bytes, candidate_pins, home)
    candidate_sha256 = sha256_bytes(candidate_bytes)

    safe_ancestors: list[str] = []
    curr = Path(config_path.anchor)
    safe_ancestors.append(str(curr))
    for part in config_path.parent.relative_to(config_path.anchor).parts:
        curr /= part
        safe_ancestors.append(str(curr))

    tool = promotion_tool_identity(workflow.core_dir)

    changed_pins: dict[str, dict[str, Any]] = {}
    for key in sorted(candidate_pins.keys()):
        cur_rev = base_pins.get(key)
        new_rev = candidate_pins[key]
        if cur_rev != new_rev:
            changed_pins[key] = {"current": cur_rev, "candidate": new_rev}

    plan_material = {
        "schema": SNAPSHOT_ADOPTION_PLAN_SCHEMA,
        "version": SNAPSHOT_ADOPTION_PLAN_VERSION,
        "config_path": os.fspath(config_path),
        "preimage_sha256": preimage_sha256,
        "candidate_sha256": candidate_sha256,
        "candidate_pins": candidate_pins,
        "current_pins": base_pins,
        "changed_pins": changed_pins,
        "sources_authority": sources_authority,
        "campaign": campaign_record,
        "tool": tool,
        "transaction": {
            "path": os.fspath(config_path),
            "safe_ancestors": safe_ancestors,
            "preimage_sha256": preimage_sha256,
            "candidate_sha256": candidate_sha256,
            "pre_mode": pre_mode,
            "post_mode": pre_mode,
            "pre_uid": metadata.st_uid,
            "pre_gid": metadata.st_gid,
            "pre_link_count": 1,
        },
    }
    plan_digest = sha256_bytes(canonical_bytes(plan_material))

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if output_dir is not None:
        plan_dir = Path(output_dir).absolute()
        ensure_private_root(plan_dir, "adoption plan directory")
        stale_result = plan_dir / "result.json"
        if stale_result.exists():
            stale_result.unlink()
    else:
        state_root = ensure_private_root(home / ".local/state/nix-update", "nix-update state root")
        plans_parent = ensure_private_root(state_root / "snapshot-adoption-plans", "adoption plan parent directory")
        plan_dir = ensure_private_root(plans_parent / f"plan-{timestamp}-{plan_digest[:16]}", "adoption plan directory")

    full_plan = {
        "status": "READY_FOR_REVIEW",
        "created_at": timestamp,
        "plan_digest": plan_digest,
        **plan_material,
    }

    atomic_private_write(plan_dir / "plan.json", canonical_bytes(full_plan))
    atomic_private_write(plan_dir / "preimage.toml", preimage_bytes)
    atomic_private_write(plan_dir / "candidate.toml", candidate_bytes)

    plan_md = render_plan_markdown(full_plan, plan_dir)
    atomic_private_write(plan_dir / "plan.md", plan_md.encode("utf-8"))

    return plan_dir


def apply_snapshot_adoption(
    workflow: Workflow,
    plan_path: Path,
    confirmation: str | None = None,
    home: Path | None = None,
) -> int:
    home = home if home is not None else operator_home()
    plan_path = Path(plan_path).resolve(strict=False)
    plan_dir = plan_path if plan_path.is_dir() else plan_path.parent
    require_private_directory(plan_dir, "adoption plan directory")
    plan_json_path = plan_dir / "plan.json"
    require_private_file(plan_json_path, "adoption plan")

    raw_plan = plan_json_path.read_bytes().decode("utf-8")
    plan_doc = strict_json_loads(raw_plan, "adoption plan")

    if (
        plan_doc.get("schema") != SNAPSHOT_ADOPTION_PLAN_SCHEMA
        or plan_doc.get("version") != SNAPSHOT_ADOPTION_PLAN_VERSION
    ):
        raise ConfigError("adoption plan schema or version is invalid")
    if plan_doc.get("status") != "READY_FOR_REVIEW":
        raise ConfigError(f"adoption plan status is {plan_doc.get('status')!r}, expected READY_FOR_REVIEW")

    plan_digest = plan_doc["plan_digest"]
    material_keys = {
        "schema", "version", "config_path", "preimage_sha256", "candidate_sha256",
        "candidate_pins", "current_pins", "changed_pins", "sources_authority",
        "campaign", "tool", "transaction",
    }
    extracted_material = {k: plan_doc[k] for k in material_keys}
    if sha256_bytes(canonical_bytes(extracted_material)) != plan_digest:
        raise ConfigError("adoption plan digest does not match its contents")

    state_root = ensure_private_root(home / ".local/state/nix-update", "nix-update state root")
    consumed_root = ensure_private_root(state_root / "snapshot-consumed", "snapshot consumed authority root")
    consumed_path = consumed_root / f"{plan_digest}.json"

    with DeploymentLock(state_root / "transaction.lock"):
        config_path = Path(plan_doc["transaction"]["path"]).resolve(strict=False)
        validate_ancestor_chain(config_path, owner_only=False)
        require_private_file(plan_dir / "candidate.toml", "adoption candidate bytes")
        candidate_bytes = (plan_dir / "candidate.toml").read_bytes()
        if sha256_bytes(candidate_bytes) != plan_doc["candidate_sha256"]:
            raise ConfigError(
                "candidate config bytes in plan directory do not match candidate_sha256 in plan"
            )

        if consumed_path.exists():
            current_bytes = config_path.read_bytes()
            if current_bytes == candidate_bytes:
                print("nix-update: snapshot adoption plan already applied; configuration matches candidate postimage")
                return 0
            raise ConfigError(
                f"this snapshot adoption plan already consumed its durable mutation authority: {plan_digest}"
            )

        expected_confirmation = CONFIRMATION_TEMPLATE.format(plan_digest=plan_digest)
        if confirmation is None:
            prompt = f"Type exactly: {expected_confirmation}\n"
            confirmation = _snapshot_tty_confirmation(prompt)
        if confirmation != expected_confirmation:
            raise ConfigError("typed confirmation does not match the reviewed adoption plan digest")

        current_tool = promotion_tool_identity(workflow.core_dir)
        if current_tool["digest"] != plan_doc["tool"]["digest"]:
            raise ConfigError("tool identity has drifted since adoption plan was prepared")

        current_metadata = config_path.lstat()
        if (
            not stat.S_ISREG(current_metadata.st_mode)
            or stat.S_ISLNK(current_metadata.st_mode)
            or current_metadata.st_nlink != 1
            or current_metadata.st_uid != plan_doc["transaction"]["pre_uid"]
            or stat.S_IMODE(current_metadata.st_mode) != plan_doc["transaction"]["pre_mode"]
        ):
            raise ConfigError(f"control config metadata drift: {config_path}")

        current_bytes = config_path.read_bytes()
        current_digest = sha256_bytes(current_bytes)

        if current_digest == plan_doc["candidate_sha256"]:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            if not consumed_path.parent.exists():
                consumed_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            require_private_directory(consumed_path.parent, "snapshot consumed authority root")
            exclusive_private_write(
                consumed_path,
                canonical_bytes({
                    "schema": SNAPSHOT_CONSUMED_SCHEMA,
                    "plan_digest": plan_digest,
                    "consumed_at": timestamp,
                })
            )
            fsync_directory(consumed_path.parent)
            print("nix-update: snapshot adoption configuration is already at candidate postimage")
            return 0

        if current_digest != plan_doc["preimage_sha256"]:
            raise ConfigError(
                f"control config preimage drift: {config_path} content has changed since adoption plan was prepared"
            )

        cfg = load_control_config(current_bytes, operator_home=home, allow_incomplete_snapshot=True)
        catalog = cfg.source_catalog
        for key in sorted(plan_doc["candidate_pins"].keys()):
            cand_rev = plan_doc["candidate_pins"][key]
            entry = catalog.get(key)
            if entry is None:
                raise ConfigError(f"configured source {key} is missing from catalog during apply")
            validate_candidate_pin_local(workflow, entry, cand_rev, "snapshot-adoption-apply")

        if plan_doc.get("campaign"):
            camp_dir = Path(plan_doc["campaign"]["directory"])
            camp_doc = load_campaign(camp_dir)
            if camp_doc.get("campaign_digest") != plan_doc["campaign"]["digest"]:
                raise ConfigError("campaign journal has been modified or tampered with")
            if camp_doc.get("state") not in VALID_CAMPAIGN_TERMINAL_STATES:
                raise ConfigError("campaign state is no longer in an authoritative terminal state")

        backup_dir = ensure_private_root(state_root / "snapshot-backups", "snapshot backup root")

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_dir / f"nix-darwin-{timestamp}-{plan_doc['preimage_sha256'][:16]}.toml"
        backup_meta_path = backup_dir / f"nix-darwin-{timestamp}-{plan_doc['preimage_sha256'][:16]}.json"

        atomic_private_write(backup_path, current_bytes)
        atomic_private_write(
            backup_meta_path,
            canonical_bytes({
                "schema": SNAPSHOT_BACKUP_SCHEMA,
                "created_at": timestamp,
                "preimage_sha256": plan_doc["preimage_sha256"],
                "plan_digest": plan_digest,
                "config_path": os.fspath(config_path),
                "revisions": plan_doc["current_pins"],
            })
        )

        _atomic_replace(config_path, candidate_bytes, plan_doc["transaction"]["pre_mode"])

        if not consumed_path.parent.exists():
            consumed_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        require_private_directory(consumed_path.parent, "snapshot consumed authority root")
        exclusive_private_write(
            consumed_path,
            canonical_bytes({
                "schema": SNAPSHOT_CONSUMED_SCHEMA,
                "plan_digest": plan_digest,
                "consumed_at": timestamp,
            })
        )
        fsync_directory(consumed_path.parent)

        result_path = plan_dir / "result.json"
        result_doc = {
            "schema": SNAPSHOT_ADOPTION_RESULT_SCHEMA,
            "version": SNAPSHOT_ADOPTION_RESULT_VERSION,
            "status": "ADOPTED",
            "plan_digest": plan_digest,
            "applied_at": timestamp,
            "config": os.fspath(config_path),
            "backup": os.fspath(backup_path),
            "consumed_authority": os.fspath(consumed_path),
            "changed": current_bytes != candidate_bytes,
            "activation_performed": False,
            "publication_performed": False,
        }
        atomic_private_write(result_path, canonical_bytes(result_doc))

        print("nix-update: snapshot adoption applied successfully")
        print(f"  config: {config_path}")
        print(f"  backup: {backup_path}")
        print("  status: ADOPTED")
        print("  host status: host is NOT yet activated; run 'nix-update build' or 'nix-update deploy' to switch")
        return 0
