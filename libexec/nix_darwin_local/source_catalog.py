"""Strict typed operator source catalog for DINAS-SOURCES1."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .paths import (
    ConfigError,
    resolve_portable_path,
    validate_input_path,
    validate_safe_managed_root,
)
from .promotion_contract import (
    SAFE_IDENTITY,
    normalize_git_transport_url,
    normalize_nix_repository_url,
    reject_promotion_secrets,
    repository_identity_key,
    validate_release_ref,
)

SOURCE_ENTRY_KEYS = frozenset({"checkout", "repository", "release_ref", "publication"})
PUBLICATION_KEYS = frozenset({"remote", "ref"})


class SourceError(ConfigError):
    """Raised when local Git source observation, materialization, or catalog coercion fails."""


@dataclass(frozen=True)
class Publication:
    remote: str
    ref: str


@dataclass(frozen=True)
class SourceCatalogEntry:
    key: str
    checkout: Path
    repository: str
    release_ref: str
    publication: Publication | None = None

    @property
    def canonical_repository_identity(self) -> str:
        return repository_identity_key(self.repository)

    @property
    def canonical_repository(self) -> str:
        return self.canonical_repository_identity


def coerce_catalog_entry(entry: Any) -> SourceCatalogEntry:
    if isinstance(entry, SourceCatalogEntry):
        return entry
    if isinstance(entry, Mapping):
        try:
            pub = entry.get("publication")
            if isinstance(pub, Mapping):
                pub = Publication(remote=pub["remote"], ref=pub["ref"])
            return SourceCatalogEntry(
                key=entry["key"],
                checkout=Path(entry["checkout"]),
                repository=entry["repository"],
                release_ref=entry["release_ref"],
                publication=pub,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise SourceError("source catalog entry fields are invalid") from error
    try:
        return SourceCatalogEntry(
            key=entry.key,
            checkout=Path(entry.checkout),
            repository=entry.repository,
            release_ref=entry.release_ref,
            publication=getattr(entry, "publication", None),
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise SourceError("source catalog entry fields are invalid") from error


def coerce_source_catalog(sources: Any) -> SourceCatalog:
    if isinstance(sources, SourceCatalog):
        return sources
    if hasattr(sources, "source_catalog") and isinstance(sources.source_catalog, SourceCatalog):
        return sources.source_catalog
    if sources is None:
        return SourceCatalog(())
    if isinstance(sources, Mapping):
        entries = tuple(
            entry if isinstance(entry, SourceCatalogEntry) else coerce_catalog_entry(entry)
            for entry in sources.values()
        )
        return SourceCatalog(entries)
    if hasattr(sources, "entries") and not isinstance(sources, type):
        raw_entries = sources.entries
        if isinstance(raw_entries, (Sequence, set, frozenset)):
            entries = tuple(
                entry if isinstance(entry, SourceCatalogEntry) else coerce_catalog_entry(entry)
                for entry in raw_entries
            )
            return SourceCatalog(entries)
    if isinstance(sources, (Sequence, set, frozenset)) and not isinstance(sources, (str, bytes)):
        entries = tuple(
            entry if isinstance(entry, SourceCatalogEntry) else coerce_catalog_entry(entry)
            for entry in sources
        )
        return SourceCatalog(entries)
    if sources:
        return SourceCatalog((coerce_catalog_entry(sources),))
    return SourceCatalog(())


@dataclass(frozen=True)
class SourceCatalog:
    entries: tuple[SourceCatalogEntry, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.entries, Mapping):
            object.__setattr__(
                self,
                "entries",
                tuple(
                    e if isinstance(e, SourceCatalogEntry) else coerce_catalog_entry(e)
                    for e in self.entries.values()
                ),
            )
        elif not isinstance(self.entries, tuple):
            object.__setattr__(
                self,
                "entries",
                tuple(
                    e if isinstance(e, SourceCatalogEntry) else coerce_catalog_entry(e)
                    for e in self.entries
                ),
            )

    @classmethod
    def coerce(cls, sources: Any) -> SourceCatalog:
        return coerce_source_catalog(sources)

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(entry.key for entry in self.entries)

    @property
    def by_key(self) -> dict[str, SourceCatalogEntry]:
        return {entry.key: entry for entry in self.entries}

    @property
    def by_repository(self) -> dict[str, SourceCatalogEntry]:
        return {entry.canonical_repository_identity: entry for entry in self.entries}

    def get(
        self, key: str, default: SourceCatalogEntry | None = None
    ) -> SourceCatalogEntry | None:
        return self.by_key.get(key, default)

    def lookup_key(self, key: str) -> SourceCatalogEntry | None:
        return self.by_key.get(key)

    def lookup_repository(
        self, repository: str, *, allow_file: bool = False
    ) -> SourceCatalogEntry | None:
        target_id: str | None = None
        try:
            target_id = canonical_repository_identity(repository, allow_file=allow_file)
        except ConfigError:
            pass
        for entry in self.entries:
            if entry.repository == repository:
                return entry
            if target_id is not None and entry.canonical_repository_identity == target_id:
                return entry
        return None

    def __getitem__(self, key: str) -> SourceCatalogEntry:
        val = self.lookup_key(key)
        if val is None:
            raise KeyError(key)
        return val

    def __contains__(self, key: str) -> bool:
        return key in self.by_key

    def __iter__(self) -> Iterator[SourceCatalogEntry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


def _require_mapping(value: Any, label: str) -> None:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a table")


def _reject_unknown(table: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(f"unknown {label} key: {unknown[0]}")


def parse_source_entry(
    key: str,
    data: Any,
    operator_home: Path,
    *,
    allow_file: bool = False,
) -> SourceCatalogEntry:
    validate_input_path(key)
    _require_mapping(data, f"sources.{key}")
    _reject_unknown(data, SOURCE_ENTRY_KEYS, f"sources.{key}")

    # checkout
    if "checkout" not in data:
        raise ConfigError(f"sources.{key}.checkout is required")
    checkout_raw = data["checkout"]
    if not isinstance(checkout_raw, str) or not checkout_raw:
        raise ConfigError(f"sources.{key}.checkout must be a non-empty string")
    resolved_checkout = resolve_portable_path(checkout_raw, operator_home)
    validate_safe_managed_root(resolved_checkout, operator_home, f"sources.{key}.checkout")
    if not resolved_checkout.exists():
        raise ConfigError(
            f"sources.{key}.checkout directory does not exist: {resolved_checkout}"
        )
    if not resolved_checkout.is_dir():
        raise ConfigError(
            f"sources.{key}.checkout is not a directory: {resolved_checkout}"
        )

    # repository
    if "repository" not in data:
        raise ConfigError(f"sources.{key}.repository is required")
    repo_raw = data["repository"]
    if not isinstance(repo_raw, str) or not repo_raw:
        raise ConfigError(f"sources.{key}.repository must be a non-empty string")
    reject_promotion_secrets(repo_raw)
    normalized_repo = normalize_nix_repository_url(repo_raw, allow_file=allow_file)

    # release_ref
    if "release_ref" not in data:
        raise ConfigError(f"sources.{key}.release_ref is required")
    ref_raw = data["release_ref"]
    if not isinstance(ref_raw, str) or not ref_raw:
        raise ConfigError(f"sources.{key}.release_ref must be a non-empty string")
    normalized_release_ref = validate_release_ref(ref_raw)

    # publication (optional)
    pub_obj: Publication | None = None
    if "publication" in data:
        pub_data = data["publication"]
        _require_mapping(pub_data, f"sources.{key}.publication")
        _reject_unknown(pub_data, PUBLICATION_KEYS, f"sources.{key}.publication")

        if "remote" not in pub_data:
            raise ConfigError(f"sources.{key}.publication.remote is required")
        pub_remote = pub_data["remote"]
        if not isinstance(pub_remote, str) or not pub_remote:
            raise ConfigError(
                f"sources.{key}.publication.remote must be a non-empty string"
            )
        if SAFE_IDENTITY.fullmatch(pub_remote) is None:
            raise ConfigError(
                f"sources.{key}.publication.remote is invalid: {pub_remote!r}"
            )

        if "ref" not in pub_data:
            raise ConfigError(f"sources.{key}.publication.ref is required")
        pub_ref = pub_data["ref"]
        if not isinstance(pub_ref, str) or not pub_ref:
            raise ConfigError(
                f"sources.{key}.publication.ref must be a non-empty string"
            )
        normalized_pub_ref = validate_release_ref(pub_ref)
        pub_obj = Publication(remote=pub_remote, ref=normalized_pub_ref)

    return SourceCatalogEntry(
        key=key,
        checkout=resolved_checkout,
        repository=normalized_repo,
        release_ref=normalized_release_ref,
        publication=pub_obj,
    )


def parse_sources(
    sources_table: Any,
    operator_home: Path,
    *,
    allow_file: bool = False,
    disjoint_keys: Sequence[str] | set[str] | None = None,
) -> tuple[SourceCatalogEntry, ...]:
    _require_mapping(sources_table, "sources")

    parsed_entries: list[SourceCatalogEntry] = []
    seen_checkouts: dict[tuple[int, int], str] = {}
    seen_repos: dict[str, str] = {}

    for key, entry_data in sources_table.items():
        entry = parse_source_entry(
            key, entry_data, operator_home, allow_file=allow_file
        )
        metadata = entry.checkout.stat()
        checkout_identity = (metadata.st_dev, metadata.st_ino)
        if checkout_identity in seen_checkouts:
            raise ConfigError(
                f"duplicate source checkout path {entry.checkout} between {seen_checkouts[checkout_identity]!r} and {entry.key!r}"
            )
        seen_checkouts[checkout_identity] = entry.key

        canonical_repo = entry.canonical_repository_identity
        if canonical_repo in seen_repos:
            raise ConfigError(
                f"duplicate source repository identity {canonical_repo!r} between {seen_repos[canonical_repo]!r} and {entry.key!r}"
            )
        seen_repos[canonical_repo] = entry.key

        parsed_entries.append(entry)

    if disjoint_keys:
        source_keys = {entry.key for entry in parsed_entries}
        overlapping = sorted(source_keys & set(disjoint_keys))
        if overlapping:
            raise ConfigError(
                f"promotion.checkouts and sources define overlapping key: {overlapping[0]}"
            )

    return tuple(sorted(parsed_entries, key=lambda item: item.key))


def canonical_repository_identity(value: str, *, allow_file: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError("repository URL must be a non-empty string")
    reject_promotion_secrets(value)
    try:
        normalized = normalize_git_transport_url(value, allow_file=allow_file)
    except ConfigError:
        normalized = normalize_nix_repository_url(value, allow_file=allow_file)
    return repository_identity_key(normalized)


def lookup_by_key(
    sources: Any,
    key: str,
) -> SourceCatalogEntry | None:
    catalog = coerce_source_catalog(sources)
    return catalog.lookup_key(key)


def lookup_by_repository(
    sources: Any,
    repository: str,
    *,
    allow_file: bool = False,
) -> SourceCatalogEntry | None:
    catalog = coerce_source_catalog(sources)
    return catalog.lookup_repository(repository, allow_file=allow_file)


lookup_source_by_key = lookup_by_key
lookup_source_by_repository = lookup_by_repository
