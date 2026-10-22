from __future__ import annotations

import json
import os
import pwd
from dataclasses import dataclass
from pathlib import Path

from .dev import DevError, validate_name
from .reconcile_plan import (
    CollisionReport,
    ReconcileAction,
    ReconcileError,
    ReconcilePlan,
    check_executable_collisions,
    create_reconcile_plan,
    expected_ai_aliases,
    operator_home,
    validate_namespace_target_readonly,
)


def validate_namespace_target(target: Path | str, label: str) -> Path:
    err = validate_namespace_target_readonly(target, label)
    if err:
        raise ReconcileError(err)
    return Path(target)


def reconcile_nbin(
    nbin_root: Path,
    namespaces: dict[str, Path | str],
) -> CollisionReport:
    # Preserve the public DevError contract without classifying free-form,
    # alphabetically sorted preflight diagnostics.
    for ns_name in sorted(namespaces):
        validate_name(ns_name, "namespace")

    if nbin_root.is_symlink():
        stat = nbin_root.lstat()
        if stat.st_uid != os.getuid():
            raise ReconcileError(f"foreign-owned symlink at nbin_root: {nbin_root}")
        try:
            target = os.readlink(nbin_root)
        except OSError as err:
            raise ReconcileError(f"cannot inspect symlink at nbin_root: {err}")
        raise ReconcileError(f"refusing to replace unexpected symlink at nbin_root: {nbin_root} -> {target}")

    plan = create_reconcile_plan(
        nbin_root=nbin_root,
        namespaces=namespaces,
    )
    if not plan.eligible:
        raise ReconcileError(plan.blockers[0])

    if nbin_root.exists():
        if not nbin_root.is_dir():
            raise ReconcileError(f"nbin_root exists but is not a directory: {nbin_root}")
        stat = nbin_root.lstat()
        if stat.st_uid != os.getuid():
            raise ReconcileError(f"nbin_root is foreign-owned: {nbin_root}")
        if stat.st_mode & 0o022:
            raise ReconcileError(f"nbin_root has unsafe mode: {nbin_root}")
    else:
        nbin_root.mkdir(parents=True, mode=0o755)

    return _reconcile_nbin_directory(nbin_root, namespaces)


def _reconcile_nbin_directory(
    nbin_root: Path,
    namespaces: dict[str, Path | str],
) -> CollisionReport:
    """Reconcile one already-validated ordinary namespace root."""

    collision_report = check_executable_collisions(namespaces)

    manifest_file = nbin_root / ".manifest.json"
    prior_namespaces: dict[str, str] = {}
    if manifest_file.is_file():
        try:
            prior_data = json.loads(manifest_file.read_text(encoding="utf-8"))
            if isinstance(prior_data, dict) and prior_data.get("schema") == "dinas-nbin-manifest-v1":
                prior_namespaces = prior_data.get("namespaces", {})
        except (json.JSONDecodeError, OSError):
            prior_namespaces = {}

    active_namespaces: dict[str, str] = {}
    for ns_name, target in sorted(namespaces.items()):
        validate_name(ns_name, "namespace")
        target_str = str(validate_namespace_target(target, "namespace"))
        link_path = nbin_root / ns_name

        if link_path.is_symlink():
            if link_path.lstat().st_uid != os.getuid():
                raise ReconcileError(f"namespace link is foreign-owned: {link_path}")
            current_target = os.readlink(link_path)
            if current_target == target_str or Path(current_target).resolve(strict=False) == Path(target_str).resolve(strict=False):
                active_namespaces[ns_name] = current_target
                continue
            if ns_name in prior_namespaces and current_target == prior_namespaces[ns_name]:
                # Managed link update
                tmp_link = nbin_root / f".tmp.{ns_name}.{os.getpid()}"
                tmp_link.symlink_to(target_str)
                os.replace(tmp_link, link_path)
                active_namespaces[ns_name] = target_str
                continue
            raise ReconcileError(f"refusing to replace unmanaged collision: {link_path} -> {current_target}")
        elif link_path.exists():
            raise ReconcileError(f"refusing to replace unmanaged non-symlink: {link_path}")
        else:
            tmp_link = nbin_root / f".tmp.{ns_name}.{os.getpid()}"
            tmp_link.symlink_to(target_str)
            os.replace(tmp_link, link_path)
            active_namespaces[ns_name] = target_str

    for stale_name, stale_target in prior_namespaces.items():
        if stale_name not in namespaces:
            stale_link = nbin_root / stale_name
            if stale_link.is_symlink():
                try:
                    if os.readlink(stale_link) == stale_target:
                        stale_link.unlink()
                except OSError:
                    pass

    manifest_data = {
        "schema": "dinas-nbin-manifest-v1",
        "namespaces": active_namespaces,
    }
    tmp_manifest = nbin_root / f".manifest.json.tmp.{os.getpid()}"
    tmp_manifest.write_text(json.dumps(manifest_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_manifest, manifest_file)

    return collision_report


def reconcile_nbin_dev(nbin_dev_root: Path, dev_namespaces: dict[str, Path | str] | None = None) -> None:
    if nbin_dev_root.is_symlink():
        raise ReconcileError(f"nbin_dev_root must not be a symlink: {nbin_dev_root}")
    if nbin_dev_root.exists():
        if not nbin_dev_root.is_dir():
            raise ReconcileError(f"nbin_dev_root is not a directory: {nbin_dev_root}")
        stat = nbin_dev_root.lstat()
        if stat.st_uid != os.getuid():
            raise ReconcileError(f"nbin_dev_root is foreign-owned: {nbin_dev_root}")
        if stat.st_mode & 0o022:
            raise ReconcileError(f"nbin_dev_root has unsafe mode: {nbin_dev_root}")
    else:
        nbin_dev_root.mkdir(parents=True, mode=0o755)

    if not dev_namespaces:
        return

    manifest_file = nbin_dev_root / ".manifest.json"
    prior_namespaces: dict[str, str] = {}
    if manifest_file.is_file():
        try:
            prior_data = json.loads(manifest_file.read_text(encoding="utf-8"))
            if isinstance(prior_data, dict) and prior_data.get("schema") == "dinas-nbin-dev-manifest-v1":
                prior_namespaces = prior_data.get("namespaces", {})
        except (json.JSONDecodeError, OSError):
            prior_namespaces = {}

    active_namespaces: dict[str, str] = {}
    for ns_name, target in sorted(dev_namespaces.items()):
        validate_name(ns_name, "namespace")
        target_str = str(validate_namespace_target(target, "development namespace"))
        link_path = nbin_dev_root / ns_name
        if link_path.is_symlink():
            if link_path.lstat().st_uid != os.getuid():
                raise ReconcileError(f"development namespace link is foreign-owned: {link_path}")
            current_target = os.readlink(link_path)
            if current_target == target_str or Path(current_target).resolve(strict=False) == Path(target_str).resolve(strict=False):
                active_namespaces[ns_name] = current_target
                continue
            if ns_name in prior_namespaces and current_target == prior_namespaces[ns_name]:
                tmp_link = nbin_dev_root / f".tmp.{ns_name}.{os.getpid()}"
                tmp_link.symlink_to(target_str)
                os.replace(tmp_link, link_path)
                active_namespaces[ns_name] = target_str
                continue
            raise ReconcileError(f"refusing to replace unmanaged collision: {link_path}")
        elif link_path.exists():
            raise ReconcileError(f"refusing to replace unmanaged path: {link_path}")
        else:
            tmp_link = nbin_dev_root / f".tmp.{ns_name}.{os.getpid()}"
            tmp_link.symlink_to(target_str)
            os.replace(tmp_link, link_path)
            active_namespaces[ns_name] = target_str

    for stale_name, stale_target in prior_namespaces.items():
        if stale_name not in dev_namespaces:
            stale_link = nbin_dev_root / stale_name
            if stale_link.is_symlink():
                try:
                    if os.readlink(stale_link) == stale_target:
                        stale_link.unlink()
                except OSError:
                    pass

    manifest_data = {
        "schema": "dinas-nbin-dev-manifest-v1",
        "namespaces": active_namespaces,
    }
    tmp_manifest = nbin_dev_root / f".manifest.json.tmp.{os.getpid()}"
    tmp_manifest.write_text(json.dumps(manifest_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_manifest, manifest_file)


def reconcile_ai_index(ai_root: Path, operator_home: Path) -> None:
    if ai_root.is_symlink():
        raise ReconcileError(f"ai_root must not be a symlink: {ai_root}")
    if ai_root.exists():
        if not ai_root.is_dir():
            raise ReconcileError(f"ai_root is not a directory: {ai_root}")
        stat = ai_root.lstat()
        if stat.st_uid != os.getuid():
            raise ReconcileError(f"ai_root is foreign-owned: {ai_root}")
        if stat.st_mode & 0o022:
            raise ReconcileError(f"ai_root has unsafe mode: {ai_root}")
    else:
        ai_root.mkdir(parents=True, mode=0o755)

    manifest_file = ai_root / ".manifest.json"
    prior_links: dict[str, str] = {}
    if manifest_file.is_file():
        try:
            prior_data = json.loads(manifest_file.read_text(encoding="utf-8"))
            if isinstance(prior_data, dict) and prior_data.get("schema") == "dinas-ai-manifest-v1":
                prior_links = prior_data.get("links", {})
        except (json.JSONDecodeError, OSError):
            prior_links = {}

    aliases = expected_ai_aliases(operator_home)
    active_links: dict[str, str] = {}

    for alias_name, target in sorted(aliases.items()):
        if not target.exists():
            continue

        link_path = ai_root / alias_name
        target_str = str(target)

        if link_path.is_symlink():
            if link_path.lstat().st_uid != os.getuid():
                raise ReconcileError(f"AI index link is foreign-owned: {link_path}")
            current_target = os.readlink(link_path)
            if current_target == target_str or Path(current_target).resolve(strict=False) == target.resolve(strict=False):
                active_links[alias_name] = current_target
                continue
            if alias_name in prior_links and current_target == prior_links[alias_name]:
                tmp_link = ai_root / f".tmp.{alias_name}.{os.getpid()}"
                tmp_link.symlink_to(target_str)
                os.replace(tmp_link, link_path)
                active_links[alias_name] = target_str
                continue
            raise ReconcileError(f"refusing to replace unmanaged collision: {link_path} -> {current_target}")
        elif link_path.exists():
            raise ReconcileError(f"refusing to replace unmanaged non-symlink: {link_path}")
        else:
            tmp_link = ai_root / f".tmp.{alias_name}.{os.getpid()}"
            tmp_link.symlink_to(target_str)
            os.replace(tmp_link, link_path)
            active_links[alias_name] = target_str

    for stale_name, stale_target in prior_links.items():
        if stale_name not in active_links:
            stale_link = ai_root / stale_name
            if stale_link.is_symlink():
                try:
                    if os.readlink(stale_link) == stale_target:
                        stale_link.unlink()
                except OSError:
                    pass

    manifest_data = {
        "schema": "dinas-ai-manifest-v1",
        "links": active_links,
    }
    tmp_manifest = ai_root / f".manifest.json.tmp.{os.getpid()}"
    tmp_manifest.write_text(json.dumps(manifest_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_manifest, manifest_file)
