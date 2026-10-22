from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence


ANSI_SGR = re.compile(r"\x1b\[[0-9;]*m")


def sanitize_diff_closures_line(line: str) -> str:
    cleaned = ANSI_SGR.sub("", line).strip()
    return cleaned


def format_deployment_summary(
    selected_flake: Path,
    host: str,
    git_identity: Mapping[str, Any],
    profile: Mapping[str, Any],
    predecessor_generation: int,
    sources: Mapping[str, Any],
    effects: Mapping[str, Any],
    preflight_results: Sequence[Mapping[str, Any]],
    target_system: str,
    live_closure: Sequence[Mapping[str, Any]],
    target_closure: Sequence[Mapping[str, Any]],
    diff_closures_text: str,
    status: str = "READY",
    commit_map: Mapping[str, str] | None = None,
) -> str:
    lines: list[str] = ["nix-update: deployment summary:"]

    lines.append(f"  selected flake: {selected_flake}#{host}")

    commit = git_identity.get("commit", "unknown")
    clean = git_identity.get("clean")
    if clean is None:
        clean = (
            git_identity.get("status") == ""
            and git_identity.get("operation_state") == "clean"
        )
    clean_str = "clean" if clean else "dirty"
    lines.append(f"  repository authority: commit {commit} ({clean_str})")

    if commit_map:
        entries = ", ".join(f"{k}={v}" for k, v in sorted(commit_map.items()))
        lines.append(f"  resolved commit map: {entries}")

    rel = profile.get("relationship", "unknown")
    live_target = profile.get("live", {}).get("target", "unknown")
    lines.append(
        f"  profile state: {rel} (predecessor gen {predecessor_generation}, live {Path(live_target).name})"
    )

    verified_count = sources.get("verified_count", len(sources.get("sources", [])))
    lines.append(f"  source authority: {verified_count} lock-pinned sources verified")

    if effects.get("status") == "absent":
        lines.append("  deployment effects: none declared (dinas.deploymentEffects absent in target composition)")
    else:
        passed_names = [p.get("name", "") for p in preflight_results if p.get("status") == "passed"]
        if passed_names:
            names_str = ", ".join(passed_names)
            lines.append(f"  deployment effects: {len(passed_names)} read-only preflight(s) passed ({names_str})")
        else:
            lines.append("  deployment effects: none declared")

    lines.append(f"  target system: {target_system}")

    live_paths = {item["path"] for item in live_closure if "path" in item}
    target_paths = {item["path"] for item in target_closure if "path" in item}
    added_count = len(target_paths - live_paths)
    removed_count = len(live_paths - target_paths)
    lines.append(
        f"  closure movement: +{added_count} store paths, -{removed_count} store paths ({len(target_paths)} paths in target)"
    )
    if (
        profile.get("live", {}).get("target")
        and target_system == profile["live"]["target"]
        and added_count == 0
        and removed_count == 0
    ):
        lines.append(
            "  reapplication notice: evaluated target equals live system with no closure delta (attended reapplication; no package update)"
        )

    diff_lines = [
        sanitize_diff_closures_line(l)
        for l in diff_closures_text.splitlines()
        if l.strip()
    ]
    if diff_lines:
        lines.append("  package changes:")
        max_show = 25
        for l in diff_lines[:max_show]:
            lines.append(f"    {l}")
        if len(diff_lines) > max_show:
            lines.append(f"    ... ({len(diff_lines) - max_show} more changes)")
    else:
        lines.append("  package changes: concise diff unavailable or empty; see machine closure movement")

    lines.append(f"nix-update: status: {status}")
    return "\n".join(lines)
