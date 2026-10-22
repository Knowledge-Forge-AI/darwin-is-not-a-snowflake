from __future__ import annotations

import json
import os
import pwd
from dataclasses import dataclass, field
from pathlib import Path

from .dev import DevError, validate_name


class ReconcileError(Exception):
    pass


@dataclass(frozen=True)
class CollisionReport:
    collisions: dict[str, list[str]]

    @property
    def has_collisions(self) -> bool:
        return bool(self.collisions)


@dataclass(frozen=True)
class ReconcileAction:
    category: str
    action: str
    path: str
    target: str | None = None
    details: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = {
            "category": self.category,
            "action": self.action,
            "path": self.path,
        }
        if self.target is not None:
            data["target"] = self.target
        if self.details:
            data["details"] = self.details
        return data


@dataclass(frozen=True)
class ReconcilePlan:
    schema: str = "dinas-reconcile-check-v1"
    eligible: bool = True
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    nbin_root: str = ""
    nbin_status: str = "ok"
    nbin_actions: list[ReconcileAction] = field(default_factory=list)
    nbin_dev_root: str = ""
    nbin_dev_status: str = "ok"
    nbin_dev_actions: list[ReconcileAction] = field(default_factory=list)
    ai_index_root: str = ""
    ai_index_status: str = "ok"
    ai_index_actions: list[ReconcileAction] = field(default_factory=list)
    executable_collisions: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "eligible": self.eligible,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "roots": {
                "nbin_root": self.nbin_root,
                "nbin_dev_root": self.nbin_dev_root,
                "ai_index_root": self.ai_index_root,
            },
            "nbin": {
                "status": self.nbin_status,
                "actions": [a.to_dict() for a in self.nbin_actions],
            },
            "nbin_dev": {
                "status": self.nbin_dev_status,
                "actions": [a.to_dict() for a in self.nbin_dev_actions],
            },
            "ai_index": {
                "status": self.ai_index_status,
                "actions": [a.to_dict() for a in self.ai_index_actions],
            },
            "executable_collisions": self.executable_collisions,
        }


def operator_home() -> Path:
    env_home = os.environ.get("HOME")
    if env_home:
        return Path(env_home)
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def check_executable_collisions(namespaces: dict[str, Path | str]) -> CollisionReport:
    command_to_namespaces: dict[str, list[str]] = {}
    for ns_name, target in sorted(namespaces.items()):
        target_path = Path(target)
        if not target_path.is_dir():
            continue
        try:
            for item in target_path.iterdir():
                if item.is_file() and os.access(item, os.X_OK) and not item.name.startswith("."):
                    command_to_namespaces.setdefault(item.name, []).append(ns_name)
        except OSError:
            continue
    collisions = {
        cmd: nss
        for cmd, nss in sorted(command_to_namespaces.items())
        if len(nss) > 1
    }
    return CollisionReport(collisions=collisions)


def expected_ai_aliases(home: Path) -> dict[str, Path]:
    return {
        "codex": home / ".codex",
        "claude": home / ".claude",
        "gemini": home / ".gemini",
        "agents": home / ".agents",
        "claude-desktop": home / "Library/Application Support/Claude",
    }


def validate_namespace_target_readonly(target: Path | str, label: str) -> str | None:
    target_path = Path(target)
    if target_path.is_symlink() or not target_path.is_dir():
        return f"{label} target must be an ordinary directory: {target_path}"
    try:
        stat = target_path.lstat()
    except OSError as err:
        return f"{label} target cannot be accessed: {err}"
    if stat.st_uid not in {0, os.getuid()}:
        return f"{label} target is foreign-owned: {target_path}"
    if stat.st_mode & 0o022:
        return f"{label} target has unsafe mode: {target_path}"
    return None


def create_reconcile_plan(
    nbin_root: Path,
    namespaces: dict[str, Path | str],
    nbin_dev_root: Path | None = None,
    dev_namespaces: dict[str, Path | str] | None = None,
    ai_root: Path | None = None,
    home: Path | None = None,
) -> ReconcilePlan:
    """Inspect system topology in a strictly read-only manner and produce an immutable ReconcilePlan."""
    if home is None:
        home = operator_home()

    blockers: list[str] = []
    warnings: list[str] = []

    # 1. Validate namespace names
    for ns_name in sorted(namespaces.keys()):
        try:
            validate_name(ns_name, "namespace")
        except DevError as err:
            blockers.append(str(err))
    # 2. Inspect nbin_root
    nbin_actions: list[ReconcileAction] = []
    nbin_status = "ok"

    if nbin_root.is_symlink():
        try:
            stat = nbin_root.lstat()
            if stat.st_uid != os.getuid():
                blockers.append(f"foreign-owned symlink at nbin_root: {nbin_root}")
        except OSError as err:
            blockers.append(f"cannot inspect symlink at nbin_root: {err}")

        try:
            target = os.readlink(nbin_root)
        except OSError as err:
            target = "<unknown>"
            blockers.append(f"cannot inspect symlink at nbin_root: {err}")
        blockers.append(f"refusing to replace unexpected symlink at nbin_root: {nbin_root} -> {target}")
        nbin_status = "unexpected_symlink"
    elif nbin_root.exists():
        if not nbin_root.is_dir():
            blockers.append(f"nbin_root exists but is not a directory: {nbin_root}")
            nbin_status = "invalid_type"
        else:
            stat = nbin_root.lstat()
            if stat.st_uid != os.getuid():
                blockers.append(f"nbin_root is foreign-owned: {nbin_root}")
            if stat.st_mode & 0o022:
                blockers.append(f"nbin_root has unsafe mode: {nbin_root}")
            nbin_status = "directory"

            manifest_file = nbin_root / ".manifest.json"
            prior_namespaces: dict[str, str] = {}
            if manifest_file.is_file():
                try:
                    prior_data = json.loads(manifest_file.read_text(encoding="utf-8"))
                    if isinstance(prior_data, dict) and prior_data.get("schema") == "dinas-nbin-manifest-v1":
                        prior_namespaces = prior_data.get("namespaces", {})
                except (json.JSONDecodeError, OSError):
                    prior_namespaces = {}

            for ns_name, target in sorted(namespaces.items()):
                target_str = str(Path(target).resolve(strict=False))
                link_path = nbin_root / ns_name
                if link_path.is_symlink():
                    try:
                        if link_path.lstat().st_uid != os.getuid():
                            blockers.append(f"namespace link is foreign-owned: {link_path}")
                        current_target = os.readlink(link_path)
                        if current_target == target_str or Path(current_target).resolve(strict=False) == Path(target_str).resolve(strict=False):
                            nbin_actions.append(
                                ReconcileAction(
                                    category="nbin",
                                    action="keep_symlink",
                                    path=str(link_path),
                                    target=current_target,
                                )
                            )
                        elif ns_name in prior_namespaces and current_target == prior_namespaces[ns_name]:
                            nbin_actions.append(
                                ReconcileAction(
                                    category="nbin",
                                    action="update_symlink",
                                    path=str(link_path),
                                    target=target_str,
                                )
                            )
                        else:
                            blockers.append(f"refusing to replace unmanaged collision: {link_path} -> {current_target}")
                    except OSError as err:
                        blockers.append(f"cannot inspect namespace link {link_path}: {err}")
                elif link_path.exists():
                    blockers.append(f"refusing to replace unmanaged non-symlink: {link_path}")
                else:
                    nbin_actions.append(
                        ReconcileAction(
                            category="nbin",
                            action="create_symlink",
                            path=str(link_path),
                            target=target_str,
                        )
                    )

            for stale_name, stale_target in sorted(prior_namespaces.items()):
                if stale_name not in namespaces:
                    stale_link = nbin_root / stale_name
                    if stale_link.is_symlink():
                        try:
                            if os.readlink(stale_link) == stale_target:
                                nbin_actions.append(
                                    ReconcileAction(
                                        category="nbin",
                                        action="unlink_stale",
                                        path=str(stale_link),
                                        target=stale_target,
                                    )
                                )
                        except OSError:
                            pass
    else:
        nbin_status = "missing"
        nbin_actions.append(
            ReconcileAction(
                category="nbin",
                action="create_dir",
                path=str(nbin_root),
            )
        )
        for ns_name in sorted(namespaces.keys()):
            target_str = str(Path(namespaces[ns_name]).resolve(strict=False))
            nbin_actions.append(
                ReconcileAction(
                    category="nbin",
                    action="create_symlink",
                    path=str(nbin_root / ns_name),
                    target=target_str,
                )
            )

    # 3. Validate namespace targets
    resolved_namespaces: dict[str, str] = {}
    for ns_name, target in sorted(namespaces.items()):
        target_err = validate_namespace_target_readonly(target, "namespace")
        if target_err:
            blockers.append(target_err)
        else:
            resolved_namespaces[ns_name] = str(Path(target).resolve(strict=False))

    # 4. Check collisions
    collision_report = check_executable_collisions(namespaces)
    if collision_report.has_collisions:
        for cmd, nss in sorted(collision_report.collisions.items()):
            warnings.append(f"command collision: {cmd} in {', '.join(nss)}")

    # 5. Inspect nbin_dev_root if provided
    nbin_dev_actions: list[ReconcileAction] = []
    nbin_dev_status = "ok"

    if nbin_dev_root is not None:
        if nbin_dev_root.is_symlink():
            blockers.append(f"nbin_dev_root must not be a symlink: {nbin_dev_root}")
            nbin_dev_status = "invalid_symlink"
        elif nbin_dev_root.exists():
            if not nbin_dev_root.is_dir():
                blockers.append(f"nbin_dev_root is not a directory: {nbin_dev_root}")
                nbin_dev_status = "invalid_type"
            else:
                stat = nbin_dev_root.lstat()
                if stat.st_uid != os.getuid():
                    blockers.append(f"nbin_dev_root is foreign-owned: {nbin_dev_root}")
                if stat.st_mode & 0o022:
                    blockers.append(f"nbin_dev_root has unsafe mode: {nbin_dev_root}")
                nbin_dev_status = "directory"

                if dev_namespaces:
                    manifest_file = nbin_dev_root / ".manifest.json"
                    prior_namespaces = {}
                    if manifest_file.is_file():
                        try:
                            prior_data = json.loads(manifest_file.read_text(encoding="utf-8"))
                            if isinstance(prior_data, dict) and prior_data.get("schema") == "dinas-nbin-dev-manifest-v1":
                                prior_namespaces = prior_data.get("namespaces", {})
                        except (json.JSONDecodeError, OSError):
                            prior_namespaces = {}

                    for ns_name, target in sorted(dev_namespaces.items()):
                        try:
                            validate_name(ns_name, "namespace")
                        except DevError as err:
                            blockers.append(str(err))
                        target_err = validate_namespace_target_readonly(target, "development namespace")
                        if target_err:
                            blockers.append(target_err)
                            continue

                        target_str = str(Path(target).resolve(strict=False))
                        link_path = nbin_dev_root / ns_name
                        if link_path.is_symlink():
                            try:
                                if link_path.lstat().st_uid != os.getuid():
                                    blockers.append(f"development namespace link is foreign-owned: {link_path}")
                                current_target = os.readlink(link_path)
                                if current_target == target_str or Path(current_target).resolve(strict=False) == Path(target_str).resolve(strict=False):
                                    nbin_dev_actions.append(
                                        ReconcileAction(
                                            category="nbin_dev",
                                            action="keep_symlink",
                                            path=str(link_path),
                                            target=current_target,
                                        )
                                    )
                                elif ns_name in prior_namespaces and current_target == prior_namespaces[ns_name]:
                                    nbin_dev_actions.append(
                                        ReconcileAction(
                                            category="nbin_dev",
                                            action="update_symlink",
                                            path=str(link_path),
                                            target=target_str,
                                        )
                                    )
                                else:
                                    blockers.append(f"refusing to replace unmanaged collision: {link_path}")
                            except OSError as err:
                                blockers.append(f"cannot inspect development namespace link {link_path}: {err}")
                        elif link_path.exists():
                            blockers.append(f"refusing to replace unmanaged path: {link_path}")
                        else:
                            nbin_dev_actions.append(
                                ReconcileAction(
                                    category="nbin_dev",
                                    action="create_symlink",
                                    path=str(link_path),
                                    target=target_str,
                                )
                            )

                    for stale_name, stale_target in sorted(prior_namespaces.items()):
                        if stale_name not in dev_namespaces:
                            stale_link = nbin_dev_root / stale_name
                            if stale_link.is_symlink():
                                try:
                                    if os.readlink(stale_link) == stale_target:
                                        nbin_dev_actions.append(
                                            ReconcileAction(
                                                category="nbin_dev",
                                                action="unlink_stale",
                                                path=str(stale_link),
                                                target=stale_target,
                                            )
                                        )
                                except OSError:
                                    pass
        else:
            nbin_dev_status = "missing"
            nbin_dev_actions.append(
                ReconcileAction(
                    category="nbin_dev",
                    action="create_dir",
                    path=str(nbin_dev_root),
                )
            )

    # 5. Inspect ai_root if provided
    ai_index_actions: list[ReconcileAction] = []
    ai_index_status = "ok"

    if ai_root is not None:
        if ai_root.is_symlink():
            blockers.append(f"ai_root must not be a symlink: {ai_root}")
            ai_index_status = "invalid_symlink"
        elif ai_root.exists():
            if not ai_root.is_dir():
                blockers.append(f"ai_root is not a directory: {ai_root}")
                ai_index_status = "invalid_type"
            else:
                stat = ai_root.lstat()
                if stat.st_uid != os.getuid():
                    blockers.append(f"ai_root is foreign-owned: {ai_root}")
                if stat.st_mode & 0o022:
                    blockers.append(f"ai_root has unsafe mode: {ai_root}")
                ai_index_status = "directory"

                manifest_file = ai_root / ".manifest.json"
                prior_links: dict[str, str] = {}
                if manifest_file.is_file():
                    try:
                        prior_data = json.loads(manifest_file.read_text(encoding="utf-8"))
                        if isinstance(prior_data, dict) and prior_data.get("schema") == "dinas-ai-manifest-v1":
                            prior_links = prior_data.get("links", {})
                    except (json.JSONDecodeError, OSError):
                        prior_links = {}

                aliases = expected_ai_aliases(home)
                for alias_name, target in sorted(aliases.items()):
                    if not target.exists():
                        ai_index_actions.append(
                            ReconcileAction(
                                category="ai_index",
                                action="skip_missing_target",
                                path=str(ai_root / alias_name),
                                target=str(target),
                            )
                        )
                        continue

                    link_path = ai_root / alias_name
                    target_str = str(target)
                    if link_path.is_symlink():
                        try:
                            if link_path.lstat().st_uid != os.getuid():
                                blockers.append(f"AI index link is foreign-owned: {link_path}")
                            current_target = os.readlink(link_path)
                            if current_target == target_str or Path(current_target).resolve(strict=False) == target.resolve(strict=False):
                                ai_index_actions.append(
                                    ReconcileAction(
                                        category="ai_index",
                                        action="keep_symlink",
                                        path=str(link_path),
                                        target=current_target,
                                    )
                                )
                            elif alias_name in prior_links and current_target == prior_links[alias_name]:
                                ai_index_actions.append(
                                    ReconcileAction(
                                        category="ai_index",
                                        action="update_symlink",
                                        path=str(link_path),
                                        target=target_str,
                                    )
                                )
                            else:
                                blockers.append(f"refusing to replace unmanaged collision: {link_path} -> {current_target}")
                        except OSError as err:
                            blockers.append(f"cannot inspect AI index link {link_path}: {err}")
                    elif link_path.exists():
                        blockers.append(f"refusing to replace unmanaged non-symlink: {link_path}")
                    else:
                        ai_index_actions.append(
                            ReconcileAction(
                                category="ai_index",
                                action="create_symlink",
                                path=str(link_path),
                                target=target_str,
                            )
                        )

                for stale_name, stale_target in sorted(prior_links.items()):
                    if stale_name not in aliases or not aliases[stale_name].exists():
                        stale_link = ai_root / stale_name
                        if stale_link.is_symlink():
                            try:
                                if os.readlink(stale_link) == stale_target:
                                    ai_index_actions.append(
                                        ReconcileAction(
                                            category="ai_index",
                                            action="unlink_stale",
                                            path=str(stale_link),
                                            target=stale_target,
                                        )
                                    )
                            except OSError:
                                pass
        else:
            ai_index_status = "missing"
            ai_index_actions.append(
                ReconcileAction(
                    category="ai_index",
                    action="create_dir",
                    path=str(ai_root),
                )
            )
            aliases = expected_ai_aliases(home)
            for alias_name, target in sorted(aliases.items()):
                if target.exists():
                    ai_index_actions.append(
                        ReconcileAction(
                            category="ai_index",
                            action="create_symlink",
                            path=str(ai_root / alias_name),
                            target=str(target),
                        )
                    )
                else:
                    ai_index_actions.append(
                        ReconcileAction(
                            category="ai_index",
                            action="skip_missing_target",
                            path=str(ai_root / alias_name),
                            target=str(target),
                        )
                    )

    return ReconcilePlan(
        eligible=(len(blockers) == 0),
        blockers=sorted(blockers),
        warnings=sorted(warnings),
        nbin_root=str(nbin_root),
        nbin_status=nbin_status,
        nbin_actions=nbin_actions,
        nbin_dev_root=str(nbin_dev_root) if nbin_dev_root else "",
        nbin_dev_status=nbin_dev_status,
        nbin_dev_actions=nbin_dev_actions,
        ai_index_root=str(ai_root) if ai_root else "",
        ai_index_status=ai_index_status,
        ai_index_actions=ai_index_actions,
        executable_collisions=collision_report.collisions,
    )
