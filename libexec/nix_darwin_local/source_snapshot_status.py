from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Mapping

from .config import load_control_config
from .deployment_paths import require_private_file
from .local_source import match_selected_source
from .promotion_contract import strict_json_loads
from .source_catalog import coerce_source_catalog


def show_snapshot_status(
    core_dir: Path,
    explicit_flake: str | None,
    config_path: Path,
    environment: Mapping[str, str],
    home: Path,
    plan_path: Path | None = None,
) -> int:
    """Render declarative source snapshot status against local checkout HEAD observations."""
    from .launcher import resolve_selection

    selection = resolve_selection(
        core_dir, explicit_flake, config_path, environment, home, allow_incomplete_snapshot=True
    )
    for warning in selection.warnings:
        print(f"warning: {warning}")

    config = selection.config
    catalog = coerce_source_catalog(config.sources)
    has_snapshot = config.snapshot is not None
    snapshot_revisions = config.snapshot.by_key if config.snapshot else {}

    selected_path = selection.path
    selected_source = match_selected_source(config.sources, selected_path)
    selected_desc = f"{selected_source.key} ({selected_source.checkout})" if selected_source else f"none ({selected_path})"

    catalog_keys = set(catalog.keys)
    snapshot_keys = set(snapshot_revisions.keys())
    missing_keys = sorted(catalog_keys - snapshot_keys)

    plan_pins: dict[str, str] = {}
    plan_digest: str | None = None
    if plan_path is not None:
        p_path = Path(plan_path).resolve(strict=False)
        p_json = p_path / "plan.json" if p_path.is_dir() else p_path
        require_private_file(p_json, "adoption plan")
        p_doc = strict_json_loads(p_json.read_bytes().decode("utf-8"), "adoption plan")
        plan_pins = p_doc.get("candidate_pins", {})
        plan_digest = p_doc.get("plan_digest")

    print("nix-update: source snapshot status:")
    print(f"  configured: {'yes' if has_snapshot else 'no'}")
    if has_snapshot:
        if not missing_keys:
            print(f"  complete: yes ({len(snapshot_keys)} of {len(catalog_keys)} sources configured)")
        else:
            print(f"  complete: no ({len(snapshot_keys)} of {len(catalog_keys)} configured; missing: {', '.join(missing_keys)})")
    else:
        print("  complete: no (snapshot table absent)")
    print(f"  selected flake source: {selected_desc}")
    if plan_path is not None and plan_digest:
        print(f"  inspected plan: {plan_path} ({plan_digest[:16]}...)")
    print("  sources:")

    any_mismatch = False
    for key in sorted(catalog.keys):
        entry = catalog[key]
        expected = snapshot_revisions.get(key, "none")
        if entry.checkout.is_dir() and (entry.checkout / ".git").exists():
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=entry.checkout,
                capture_output=True,
                text=True,
            )
            head = res.stdout.strip() if res.returncode == 0 else "unknown"
        else:
            head = "missing-checkout"

        if has_snapshot:
            status = "match" if expected == head else "mismatch"
            if status == "mismatch":
                any_mismatch = True
            if plan_path is not None and key in plan_pins:
                plan_pin = plan_pins[key]
                print(f"    {key}: expected={expected}, plan={plan_pin}, head={head} ({status})")
            else:
                print(f"    {key}: expected={expected}, head={head} ({status})")
        else:
            any_mismatch = True
            if plan_path is not None and key in plan_pins:
                plan_pin = plan_pins[key]
                print(f"    {key}: plan={plan_pin}, head={head}")
            else:
                print(f"    {key}: head={head}")

    # Actionable guidance
    if has_snapshot:
        if missing_keys:
            print("  guidance: snapshot table is incomplete; run 'nix-update snapshot prepare' to derive a complete candidate snapshot")
        elif any_mismatch:
            print("  guidance: snapshot pins mismatch checkout HEADs; use 'nix-update snapshot prepare' to prepare an adoption plan")
        else:
            print("  guidance: all snapshot pins match checkout HEADs; normal deployment ('nix-update deploy') will consume adopted pins")
    else:
        print("  guidance: snapshot table absent; use 'nix-update snapshot prepare' to adopt an initial declarative snapshot")

    return 0
