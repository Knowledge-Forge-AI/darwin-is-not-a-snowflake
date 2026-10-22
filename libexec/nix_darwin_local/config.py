from __future__ import annotations

import os
import pwd
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from collections.abc import Mapping as MappingABC

from .paths import (
    ConfigError,
    resolve_portable_path,
    validate_input_path,
    validate_safe_managed_root,
)
from .source_catalog import (
    Publication,
    SourceCatalog,
    SourceCatalogEntry,
    parse_sources,
)


TOP_LEVEL_KEYS = frozenset({"flake", "modules", "paths", "promotion", "sources", "snapshot"})
FLAKE_KEYS = frozenset({"active", "update_first", "overrides"})
MODULE_KEYS = frozenset({"enabled"})
PROMOTION_KEYS = frozenset({"checkouts"})
SNAPSHOT_KEYS = frozenset({"sources"})
PATHS_KEYS = frozenset({
    "workspace_root",
    "nbin_root",
    "nbin_dev_root",
    "ai_index_root",
    "dinas_root",
})
PROJECTED_PATHS_KEYS = PATHS_KEYS | {"schema"}


@dataclass(frozen=True)
class SourceSnapshot:
    revisions: tuple[tuple[str, str], ...] = ()

    @property
    def by_key(self) -> dict[str, str]:
        return dict(self.revisions)

    def __getitem__(self, key: str) -> str:
        return self.by_key[key]

    def __contains__(self, key: str) -> bool:
        return key in self.by_key

    def __len__(self) -> int:
        return len(self.revisions)

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.by_key.get(key, default)

    def keys(self) -> tuple[str, ...]:
        return tuple(k for k, _ in self.revisions)

    def values(self) -> tuple[str, ...]:
        return tuple(v for _, v in self.revisions)

    def items(self) -> tuple[tuple[str, str], ...]:
        return self.revisions


@dataclass(frozen=True, eq=False)
class EffectiveSourceRevisions(MappingABC[str, str]):
    active: bool
    revisions: tuple[tuple[str, str], ...] = ()
    provenance: tuple[tuple[str, str], ...] = ()
    configured_revisions: tuple[tuple[str, str], ...] | None = None

    @property
    def by_key(self) -> dict[str, str]:
        return dict(self.revisions)

    @property
    def provenance_by_key(self) -> dict[str, str]:
        return dict(self.provenance)

    @property
    def cli_override_count(self) -> int:
        return sum(1 for _, prov in self.provenance if prov == "cli")

    def __getitem__(self, key: str) -> str:
        return self.by_key[key]

    def __iter__(self):
        return iter(self.by_key)

    def __len__(self) -> int:
        return len(self.revisions)

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.by_key.get(key, default)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, EffectiveSourceRevisions):
            return (
                self.active == other.active
                and self.revisions == other.revisions
                and self.provenance == other.provenance
                and self.configured_revisions == other.configured_revisions
            )
        if isinstance(other, MappingABC):
            return self.by_key == dict(other)
        return False


@dataclass(frozen=True)
class ControlConfig:
    active: Path | None = None
    update_first: tuple[str, ...] = ()
    overrides: tuple[tuple[str, Path], ...] = ()
    enabled_modules: tuple[str, ...] = ()
    workspace_root: Path = Path()
    nbin_root: Path = Path()
    nbin_dev_root: Path = Path()
    ai_index_root: Path = Path()
    dinas_root: Path = Path()
    promotion_checkouts: tuple[tuple[str, Path], ...] = ()
    sources: tuple[SourceCatalogEntry, ...] = ()
    snapshot: SourceSnapshot | None = None
    present: bool = False

    @property
    def source_catalog(self) -> SourceCatalog:
        return SourceCatalog(self.sources)

    def source(self, key: str) -> SourceCatalogEntry | None:
        return self.source_catalog.lookup_key(key)

    def get_source(self, key: str) -> SourceCatalogEntry | None:
        return self.source_catalog.lookup_key(key)

    def source_by_repository(
        self, repository: str, *, allow_file: bool = False
    ) -> SourceCatalogEntry | None:
        return self.source_catalog.lookup_repository(repository, allow_file=allow_file)

    def get_source_by_repository(
        self, repository: str, *, allow_file: bool = False
    ) -> SourceCatalogEntry | None:
        return self.source_catalog.lookup_repository(repository, allow_file=allow_file)


def operator_home() -> Path:
    env_home = os.environ.get("HOME")
    if env_home:
        return Path(env_home)
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def default_paths(home: Path) -> dict[str, Path]:
    return {
        "workspace_root": home / ".local/nix-darwin",
        "nbin_root": home / ".local/nbin",
        "nbin_dev_root": home / ".local/nbin_dev",
        "ai_index_root": home / "ai",
        "dinas_root": home / ".local/nix-darwin/darwin-is-not-a-snowflake",
    }


def load_control_config(
    path: Path | bytes | str,
    operator_home: Path | None = None,
    allow_incomplete_snapshot: bool = False,
) -> ControlConfig:
    home = operator_home if operator_home is not None else globals()["operator_home"]()
    defaults = default_paths(home)
    if isinstance(path, bytes):
        raw = path
    else:
        path_obj = Path(path)
        if not path_obj.exists():
            return ControlConfig(
                workspace_root=defaults["workspace_root"],
                nbin_root=defaults["nbin_root"],
                nbin_dev_root=defaults["nbin_dev_root"],
                ai_index_root=defaults["ai_index_root"],
                dinas_root=defaults["dinas_root"],
                present=False,
            )
        try:
            raw = path_obj.read_bytes()
        except OSError as error:
            raise ConfigError(f"unable to read control config: {path}") from error
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        path_label = "<bytes>" if isinstance(path, bytes) else str(path)
        raise ConfigError(f"unable to parse control config: {path_label}") from error
    _require_mapping(document, "document")
    _reject_unknown(document, TOP_LEVEL_KEYS, "top-level")
    flake = _table(document, "flake")
    modules = _table(document, "modules")
    promotion = _table(document, "promotion")
    _reject_unknown(flake, FLAKE_KEYS, "flake")
    _reject_unknown(modules, MODULE_KEYS, "modules")
    _reject_unknown(promotion, PROMOTION_KEYS, "promotion")

    paths_present = "paths" in document
    paths_table = _table(document, "paths") if paths_present else {}
    if paths_present:
        _reject_unknown(paths_table, PATHS_KEYS, "paths")

    resolved_paths: dict[str, Path] = {}
    for key in ("workspace_root", "nbin_root", "nbin_dev_root", "ai_index_root", "dinas_root"):
        if key in paths_table:
            val_text = paths_table[key]
            if not isinstance(val_text, str) or not val_text:
                raise ConfigError(f"paths.{key} must be a non-empty string")
            resolved_paths[key] = resolve_portable_path(val_text, home)
        else:
            resolved_paths[key] = defaults[key]

    for key, p in resolved_paths.items():
        validate_safe_managed_root(p, home, f"paths.{key}")

    if resolved_paths["nbin_root"] == resolved_paths["nbin_dev_root"]:
        raise ConfigError("paths.nbin_root and paths.nbin_dev_root must be distinct")

    if operator_home is None and paths_present and "NIX_DARWIN_LOCAL" in os.environ:
        expected_ws = Path(os.environ["NIX_DARWIN_LOCAL"]).resolve(strict=False)
        if resolved_paths["workspace_root"].resolve(strict=False) != expected_ws:
            raise ConfigError("paths.workspace_root disagrees with NIX_DARWIN_LOCAL")

    active_text = flake.get("active", "")
    if not isinstance(active_text, str):
        raise ConfigError("flake.active must be a string")
    active = resolve_portable_path(active_text, home) if active_text else None
    update_first = _input_list(flake.get("update_first", []), "flake.update_first")
    overrides_table = flake.get("overrides", {})
    _require_mapping(overrides_table, "flake.overrides")
    overrides: list[tuple[str, Path]] = []
    for input_path, checkout in overrides_table.items():
        validate_input_path(input_path)
        if not isinstance(checkout, str):
            raise ConfigError(f"flake.overrides.{input_path} must be a string")
        overrides.append((input_path, resolve_portable_path(checkout, home)))
    enabled = _string_list(modules.get("enabled", []), "modules.enabled")
    checkouts_table = promotion.get("checkouts", {})
    _require_mapping(checkouts_table, "promotion.checkouts")
    promotion_checkouts: list[tuple[str, Path]] = []
    for input_path, checkout in checkouts_table.items():
        validate_input_path(input_path)
        if not isinstance(checkout, str) or not checkout:
            raise ConfigError(f"promotion.checkouts.{input_path} must be a non-empty string")
        resolved_checkout = resolve_portable_path(checkout, home)
        validate_safe_managed_root(
            resolved_checkout, home, f"promotion.checkouts.{input_path}"
        )
        promotion_checkouts.append((input_path, resolved_checkout))
    sources_present = "sources" in document
    sources_table = _table(document, "sources") if sources_present else {}
    promotion_keys = {item[0] for item in promotion_checkouts}
    sources = parse_sources(sources_table, home, disjoint_keys=promotion_keys)
    if any(
        checkout.exists() and checkout.samefile(entry.checkout)
        for _, checkout in promotion_checkouts for entry in sources
    ):
        raise ConfigError("promotion.checkouts duplicates a source catalog checkout; remove the legacy mapping")
    snapshot_present = "snapshot" in document
    snapshot: SourceSnapshot | None = None
    if snapshot_present:
        snapshot_table = _table(document, "snapshot")
        _reject_unknown(snapshot_table, SNAPSHOT_KEYS, "snapshot")
        if "sources" not in snapshot_table:
            raise ConfigError("snapshot.sources is required")
        snapshot_sources = snapshot_table["sources"]
        _require_mapping(snapshot_sources, "snapshot.sources")
        if not snapshot_sources:
            raise ConfigError("snapshot.sources must not be empty")

        from .promotion_contract import HEX_REVISION

        catalog_keys = {entry.key for entry in sources}
        snapshot_keys = set(snapshot_sources.keys())

        missing = sorted(catalog_keys - snapshot_keys)
        if missing and not allow_incomplete_snapshot:
            raise ConfigError(f"snapshot.sources is missing configured source keys: {', '.join(missing)}")
        unknown = sorted(snapshot_keys - catalog_keys)
        if unknown:
            raise ConfigError(f"snapshot.sources contains unknown source keys: {', '.join(unknown)}")

        validated_revisions: list[tuple[str, str]] = []
        for key in sorted(snapshot_sources.keys()):
            raw_rev = snapshot_sources[key]
            if not isinstance(raw_rev, str):
                raise ConfigError(
                    f"invalid snapshot revision for source {key!r}: {raw_rev!r}, expected 40-hex-commit"
                )
            rev = raw_rev.strip().lower()
            if HEX_REVISION.fullmatch(rev) is None:
                raise ConfigError(
                    f"invalid snapshot revision for source {key!r}: {raw_rev!r}, expected 40-hex-commit"
                )
            validated_revisions.append((key, rev))

        snapshot = SourceSnapshot(revisions=tuple(validated_revisions))

    return ControlConfig(
        active=active,
        update_first=tuple(sorted(update_first)),
        overrides=tuple(sorted(overrides, key=lambda item: item[0])),
        enabled_modules=tuple(enabled),
        workspace_root=resolved_paths["workspace_root"],
        nbin_root=resolved_paths["nbin_root"],
        nbin_dev_root=resolved_paths["nbin_dev_root"],
        ai_index_root=resolved_paths["ai_index_root"],
        dinas_root=resolved_paths["dinas_root"],
        promotion_checkouts=tuple(sorted(promotion_checkouts, key=lambda item: item[0])),
        sources=sources,
        snapshot=snapshot,
        present=True,
    )


def load_projected_paths(path: Path, operator_home: Path | None = None) -> ControlConfig:
    """Load the generated paths projection into the canonical typed config."""
    home = operator_home if operator_home is not None else globals()["operator_home"]()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConfigError(f"unable to read paths projection: {path}") from error
    _require_mapping(document, "paths projection")
    _reject_unknown(document, PROJECTED_PATHS_KEYS, "paths projection")
    if set(document) != PROJECTED_PATHS_KEYS or document.get("schema") != "dinas-paths-v1":
        raise ConfigError("paths projection has an invalid schema or missing key")

    resolved: dict[str, Path] = {}
    for key in PATHS_KEYS:
        value = document[key]
        if not isinstance(value, str) or not value:
            raise ConfigError(f"paths projection {key} must be a non-empty string")
        resolved[key] = resolve_portable_path(value, home)
        validate_safe_managed_root(resolved[key], home, f"paths.{key}")
    if resolved["nbin_root"] == resolved["nbin_dev_root"]:
        raise ConfigError("paths.nbin_root and paths.nbin_dev_root must be distinct")
    return ControlConfig(
        workspace_root=resolved["workspace_root"],
        nbin_root=resolved["nbin_root"],
        nbin_dev_root=resolved["nbin_dev_root"],
        ai_index_root=resolved["ai_index_root"],
        dinas_root=resolved["dinas_root"],
        present=True,
    )


def _table(document: dict[str, Any], name: str) -> dict[str, Any]:
    value = document.get(name, {})
    _require_mapping(value, name)
    return value


def _require_mapping(value: Any, label: str) -> None:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a table")


def _reject_unknown(table: dict[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(f"unknown {label} key: {unknown[0]}")


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"{label} must be a list of strings")
    if len(set(value)) != len(value):
        raise ConfigError(f"{label} contains a duplicate")
    return value


def _input_list(value: Any, label: str) -> list[str]:
    values = _string_list(value, label)
    for item in values:
        validate_input_path(item)
    return values


def resolve_effective_source_revisions(
    config: ControlConfig,
    cli_revisions: MappingABC[str, str] | None = None,
) -> EffectiveSourceRevisions:
    cli = dict(cli_revisions or {})
    if hasattr(config.sources, "keys"):
        catalog_keys = set(config.sources.keys)
    else:
        catalog_keys = {entry.key if hasattr(entry, "key") else str(entry) for entry in config.sources}

    from .promotion_contract import HEX_REVISION
    for key, rev in cli.items():
        if key not in catalog_keys:
            raise ConfigError(f"unknown source key in --source-revision: {key!r}")
        if not isinstance(rev, str) or HEX_REVISION.fullmatch(rev) is None:
            raise ConfigError(f"invalid --source-revision format: {key}={rev!r}, expected KEY=40-hex-commit")

    if config.snapshot is None:
        revs = tuple(sorted(cli.items()))
        prov = tuple((k, "cli") for k, _ in revs)
        return EffectiveSourceRevisions(
            active=False,
            revisions=revs,
            provenance=prov,
            configured_revisions=None,
        )

    configured = config.snapshot.revisions
    eff_revs: list[tuple[str, str]] = []
    eff_prov: list[tuple[str, str]] = []

    for key, snap_rev in configured:
        if key in cli:
            eff_revs.append((key, cli[key]))
            eff_prov.append((key, "cli"))
        else:
            eff_revs.append((key, snap_rev))
            eff_prov.append((key, "snapshot"))

    return EffectiveSourceRevisions(
        active=True,
        revisions=tuple(eff_revs),
        provenance=tuple(eff_prov),
        configured_revisions=configured,
    )
