"""Topology and route discovery for DINAS composition promotion.

Composition contracts describe ownership; the lock graph and each checkout's
registry describe how an operator can reach that ownership.  This module keeps
those concerns explicit and small:

* registry parsing is strict and read-only;
* route construction follows layered contracts recursively, so a private
  layer is optional and more than two composition layers remain possible;
* local checkout resolution uses the selected path, explicit operator mapping,
  or one deterministic child of the configured workspace root.  It never
  scans an outbox or imports a provider/dispatcher checkout.

The optional ``[promotion.checkouts]`` TOML mapping is keyed by the lower
composition input name (or another exact stage name).  In the common layout,
``workspace_root/<stage-name>`` supplies the same result without configuration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .paths import ConfigError, validate_input_path
from .promotion_core import CORE_INPUT
from .promotion_contract import (
    LOCAL_ONLY_MODULE_URL,
    contract_dialect,
    normalize_nix_repository_url,
    strict_json_loads,
    validate_contract,
)
from .promotion_lock import _resolve_input_path, _validated_graph


REGISTRY_SCHEMAS = frozenset(
    {"nix-darwin-composition-registry-v1", "nix-darwin-composition-registry-v2"}
)
_REGISTRY_FIELDS = frozenset({"schema", "modules"})
_REGISTRY_MODULE_FIELDS_V1 = frozenset(
    {"name", "input", "url", "checkout", "bootstrap"}
)
_REGISTRY_MODULE_FIELDS_V2 = _REGISTRY_MODULE_FIELDS_V1 | {"releaseRef"}
_BOOTSTRAP_FIELDS = frozenset({"strategy", "source", "config"})
_SAFE_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


@dataclass(frozen=True)
class RegistryModule:
    name: str
    input: str
    url: str
    checkout: str
    bootstrap_strategy: str
    release_ref: str | None = None


@dataclass(frozen=True)
class CompositionRegistry:
    schema: str
    modules: tuple[RegistryModule, ...]

    @property
    def inputs(self) -> tuple[str, ...]:
        return tuple(item.input for item in self.modules)


@dataclass(frozen=True)
class PromotionStage:
    """One ordered stage in a promotion campaign.

    ``input_name`` is the input that stage must advance.  A core stage owns
    the source repository revision and therefore has no composition input.
    ``name`` is the contract composition name for a composition stage.
    """

    name: str
    kind: str
    input_name: str | None


@dataclass(frozen=True)
class PromotionChain:
    top: Path
    stages: tuple[PromotionStage, ...]
    checkouts: tuple[Path, ...]

    @property
    def is_core_route(self) -> bool:
        return bool(self.stages) and self.stages[0].kind == "core"

    @property
    def stage_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.stages)


def load_registry(path: Path, *, allow_file_urls: bool = False) -> CompositionRegistry:
    """Read one composition registry with duplicate-key and shape checks."""

    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ConfigError(f"unable to read composition registry: {path}") from error
    document = strict_json_loads(raw, "composition registry")
    if not isinstance(document, dict) or set(document) != _REGISTRY_FIELDS:
        raise ConfigError("composition registry fields are invalid")
    schema = document.get("schema")
    if schema not in REGISTRY_SCHEMAS:
        raise ConfigError("composition registry schema is unsupported")
    modules = document.get("modules")
    if not isinstance(modules, list) or not modules:
        raise ConfigError("composition registry modules must be a non-empty list")

    expected_fields = (
        _REGISTRY_MODULE_FIELDS_V2
        if schema.endswith("-v2")
        else _REGISTRY_MODULE_FIELDS_V1
    )
    records: list[RegistryModule] = []
    seen_names: set[str] = set()
    seen_inputs: set[str] = set()
    seen_checkouts: set[str] = set()
    seen_urls: set[str] = set()
    for index, value in enumerate(modules):
        if not isinstance(value, dict) or set(value) != expected_fields:
            raise ConfigError(f"composition registry module {index} fields are invalid")
        name = _safe_identity(value.get("name"), f"registry module {index} name")
        input_name = _safe_input(value.get("input"), f"registry module {index} input")
        checkout = _safe_identity(value.get("checkout"), f"registry module {index} checkout")
        url_value = value.get("url")
        if url_value == LOCAL_ONLY_MODULE_URL:
            url = LOCAL_ONLY_MODULE_URL
        else:
            url = normalize_nix_repository_url(url_value, allow_file=allow_file_urls)
        if name in seen_names or input_name in seen_inputs or checkout in seen_checkouts:
            raise ConfigError("composition registry contains a duplicate identity")
        if url != LOCAL_ONLY_MODULE_URL and url in seen_urls:
            raise ConfigError("composition registry contains a duplicate repository URL")
        seen_names.add(name)
        seen_inputs.add(input_name)
        seen_checkouts.add(checkout)
        if url != LOCAL_ONLY_MODULE_URL:
            seen_urls.add(url)
        bootstrap = value.get("bootstrap")
        if not isinstance(bootstrap, dict) or not set(bootstrap) <= _BOOTSTRAP_FIELDS:
            raise ConfigError(f"composition registry module {index} bootstrap is invalid")
        strategy = _safe_identity(
            bootstrap.get("strategy"), f"registry module {index} bootstrap strategy"
        )
        for optional in ("source", "config"):
            if optional in bootstrap and not _safe_relative_path(bootstrap[optional]):
                raise ConfigError(f"composition registry module {index} bootstrap {optional} is invalid")
        release_ref: str | None = None
        if schema.endswith("-v2"):
            release_ref = value.get("releaseRef")
            if not isinstance(release_ref, str) or not release_ref.startswith("refs/heads/"):
                raise ConfigError(f"composition registry module {index} releaseRef is invalid")
        records.append(
            RegistryModule(
                name,
                input_name,
                url,
                checkout,
                strategy,
                release_ref,
            )
        )
    return CompositionRegistry(schema, tuple(records))


def registry_matches_contract(
    registry: CompositionRegistry,
    contract: Mapping[str, Any],
) -> bool:
    """Return whether registry order and identities match local contract rows."""

    modules = contract.get("modules")
    if not isinstance(modules, list):
        raise ConfigError("promotion contract modules are invalid")
    expected = tuple(str(item.get("input")) for item in modules if isinstance(item, dict))
    if len(expected) != len(modules) or registry.inputs != expected:
        return False
    for row, module in zip(modules, registry.modules, strict=True):
        if not isinstance(row, dict):
            return False
        if row.get("name") != module.name or row.get("input") != module.input:
            return False
        if row.get("url") != LOCAL_ONLY_MODULE_URL:
            try:
                if normalize_nix_repository_url(row.get("url")) != module.url:
                    return False
            except ConfigError:
                return False
    return True


def resolve_lock_input(lock: Mapping[str, Any], input_path: str) -> str:
    """Resolve a lock input path using DINAS's canonical follows resolver."""

    validate_input_path(input_path)
    nodes, root = _validated_graph(lock, "promotion topology")
    return _resolve_input_path(nodes, root, root, input_path.split("/"), set())


def route_for_input(
    contract: Mapping[str, Any],
    input_name: str,
    *,
    validate: bool = True,
    allow_file_urls: bool = False,
) -> tuple[PromotionStage, ...]:
    """Build the low-to-high stage route for one requested input.

    Public-only contracts produce one composition stage.  A layered contract
    recursively appends its own stage after its embedded lower route.  The
    explicit core route starts with a core stage and then appends every
    composition layer, including optional private layers.
    """

    if validate:
        validate_contract(contract, allow_file_urls=allow_file_urls)
    if not isinstance(input_name, str) or not input_name:
        raise ConfigError("promotion route input is invalid")
    return _route_for_input(contract, input_name, include_core=True)


def _route_for_input(
    contract: Mapping[str, Any], input_name: str, *, include_core: bool
) -> tuple[PromotionStage, ...]:
    composition = contract.get("composition")
    if not isinstance(composition, dict) or not isinstance(composition.get("name"), str):
        raise ConfigError("promotion route composition identity is invalid")
    current = str(composition["name"])
    dialect = contract_dialect(contract)

    if input_name == CORE_INPUT:
        prefix = (PromotionStage(CORE_INPUT, "core", None),) if include_core else ()
        if dialect == "layered":
            lower = contract.get("lowerPromotion")
            required = contract.get("requiredLowerPromotion")
            if not isinstance(lower, dict) or not isinstance(required, dict):
                raise ConfigError("layered promotion route lower contract is invalid")
            lower_route = _route_for_input(lower, input_name, include_core=False)
            lower_name = required.get("candidateInput")
            if not isinstance(lower_name, str) or not lower_name:
                raise ConfigError("layered promotion route candidate input is invalid")
            return prefix + lower_route + (PromotionStage(current, "composition", lower_name),)
        return prefix + (PromotionStage(current, "composition", CORE_INPUT),)

    modules = contract.get("modules")
    if not isinstance(modules, list):
        raise ConfigError("promotion route modules are invalid")
    direct = [
        module for module in modules
        if isinstance(module, dict) and module.get("input") == input_name
    ]
    if len(direct) == 1:
        return (PromotionStage(current, "composition", input_name),)
    if len(direct) > 1:
        raise ConfigError("promotion route input is registered more than once")

    if dialect == "layered":
        lower = contract.get("lowerPromotion")
        required = contract.get("requiredLowerPromotion")
        if not isinstance(lower, dict) or not isinstance(required, dict):
            raise ConfigError("layered promotion route lower contract is invalid")
        lower_name = required.get("candidateInput")
        if not isinstance(lower_name, str) or not lower_name:
            raise ConfigError("layered promotion route candidate input is invalid")
        if input_name == lower_name:
            return (PromotionStage(current, "composition", lower_name),)
        lower_route = _route_for_input(lower, input_name, include_core=include_core)
        return lower_route + (PromotionStage(current, "composition", lower_name),)
    raise ConfigError("requested promotion input is unknown to the selected topology")


def discover_chain(
    top: Path,
    contract: Mapping[str, Any],
    input_name: str,
    *,
    core_dir: Path,
    workspace_root: Path | None = None,
    configured_checkouts: Mapping[str, Path] | Sequence[tuple[str, Path]] = (),
    allow_file_urls: bool = False,
) -> PromotionChain:
    """Resolve local checkouts for a route without filesystem scanning.

    ``top`` and ``core_dir`` are supplied by the caller's existing selection
    logic.  Lower composition layers use an exact configured mapping first,
    then ``workspace_root/<stage-name>``.  A missing lower checkout is a clear
    configuration error; a private checkout is never required for a public
    route.
    """

    if not top.is_absolute() or not core_dir.is_absolute():
        raise ConfigError("promotion topology checkout paths must be absolute")
    stages = route_for_input(contract, input_name, allow_file_urls=allow_file_urls)
    mappings = _checkout_mapping(configured_checkouts)
    checkouts: list[Path] = []
    top_resolved = top.resolve(strict=False)
    core_resolved = core_dir.resolve(strict=False)
    for index, stage in enumerate(stages):
        if stage.kind == "core":
            path = core_resolved
        elif index == len(stages) - 1:
            path = top_resolved
        else:
            path = _lower_checkout(
                stage,
                mappings,
                workspace_root.resolve(strict=False) if workspace_root is not None else None,
            )
        _require_flake_checkout(path, stage)
        if checkouts and path == checkouts[-1]:
            raise ConfigError("promotion topology contains duplicate composition checkout")
        checkouts.append(path)
    return PromotionChain(top_resolved, stages, tuple(checkouts))


def checkouts_from_config(config: Any) -> dict[str, Path]:
    """Extract the optional strict TOML mapping without coupling to its class."""

    values = getattr(config, "promotion_checkouts", ())
    return _checkout_mapping(values)


def _lower_checkout(
    stage: PromotionStage,
    mappings: Mapping[str, Path],
    workspace_root: Path | None,
) -> Path:
    for key in (stage.input_name, stage.name):
        if key is not None and key in mappings:
            return mappings[key].resolve(strict=False)
    if workspace_root is not None:
        return (workspace_root / stage.name).resolve(strict=False)
    raise ConfigError(
        f"promotion lower composition checkout is unresolved: {stage.name}; "
        "set paths.workspace_root or promotion.checkouts"
    )


def _require_flake_checkout(path: Path, stage: PromotionStage) -> None:
    if not path.is_dir() or not (path / "flake.nix").is_file():
        raise ConfigError(
            f"promotion {stage.kind} checkout is missing flake.nix: {path}"
        )


def _checkout_mapping(
    values: Mapping[str, Path] | Sequence[tuple[str, Path]] | Any,
) -> dict[str, Path]:
    if isinstance(values, Mapping):
        pairs = list(values.items())
    else:
        try:
            pairs = list(values)
        except TypeError as error:
            raise ConfigError("promotion checkout mapping is invalid") from error
    result: dict[str, Path] = {}
    for key, value in pairs:
        if not isinstance(key, str) or not key:
            raise ConfigError("promotion checkout mapping key is invalid")
        validate_input_path(key)
        if not isinstance(value, Path) or not value.is_absolute():
            raise ConfigError("promotion checkout mapping path must be absolute")
        if key in result:
            raise ConfigError("promotion checkout mapping contains a duplicate")
        result[key] = value
    return result


def _safe_identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SAFE_IDENTITY.fullmatch(value) is None:
        raise ConfigError(f"{label} is invalid")
    return value


def _safe_input(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{label} is invalid")
    try:
        return validate_input_path(value)
    except ConfigError as error:
        raise ConfigError(f"{label} is invalid") from error


def _safe_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.startswith("/"):
        return False
    parts = value.split("/")
    return all(part and part not in {".", ".."} for part in parts)


def resolve_campaign_stages(
    selected: Path,
    config: Any,
    input_name: str,
    revision: str,
    *,
    workflow: Any | None = None,
    core_dir: Path | None = None,
) -> list[dict[str, Any]] | None:
    """Resolve the ordered campaign stages for a selected composition and input."""
    selected = selected.resolve(strict=False)
    contract: Mapping[str, Any] | None = None
    contract_file = selected / "promotion-contract.json"
    if contract_file.is_file():
        try:
            contract = strict_json_loads(contract_file.read_text(encoding="utf-8"), "promotion contract")
            validate_contract(contract, allow_file_urls=True)
        except Exception:
            contract = None
    if contract is None:
        try:
            from .deployment_workflow import Workflow
            from .promotion_qualification import evaluate_contract
            eval_workflow = workflow if workflow is not None else Workflow(core_dir or selected, display_command_output=False)
            contract = evaluate_contract(eval_workflow, selected, allow_file_urls=True)
        except Exception:
            contract = None
    if contract is None:
        return None

    core = core_dir or getattr(workflow, "core_dir", None) or getattr(config, "dinas_root", None)
    if core is None or not isinstance(core, Path) or not core.is_absolute():
        core = selected
    workspace_root = getattr(config, "workspace_root", None)
    checkouts = checkouts_from_config(config)
    sources = tuple(getattr(config, "sources", ()))
    if sources:
        from .source_catalog import lookup_by_repository, lookup_by_key

        # Catalog identities own routing; legacy input aliases must not shadow
        # a composition's exact repository match.
        checkouts = {}
        workspace_root = None
        current_contract = contract
        while current_contract is not None:
            composition = current_contract["composition"]
            entry = lookup_by_repository(sources, composition["repositoryUrl"])
            if entry is None:
                raise ConfigError("composition contract has no exact source catalog identity")
            checkouts[composition["name"]] = entry.checkout
            if current_contract is contract and entry.checkout.resolve() != selected:
                raise ConfigError("selected composition checkout disagrees with source catalog")
            current_contract = current_contract.get("lowerPromotion")
        if input_name == "core":
            core_entry = lookup_by_key(sources, "core")
            if core_entry is None:
                raise ConfigError("core promotion requires the core source catalog entry")
            core = core_entry.checkout
            if not (core / "flake.nix").is_file():
                core = core / "nix-darwin"
    chain = discover_chain(
        selected,
        contract,
        input_name,
        core_dir=core,
        workspace_root=workspace_root,
        configured_checkouts=checkouts,
        allow_file_urls=True,
    )
    stages: list[dict[str, Any]] = []
    for index, (stage_item, checkout) in enumerate(zip(chain.stages, chain.checkouts)):
        stages.append(
            {
                "name": stage_item.name,
                "kind": stage_item.kind,
                "repository": checkout,
                "input": stage_item.input_name or input_name,
                "revision": revision if index == 0 else None,
            }
        )
    return stages


def prepare_campaign_stage(
    workflow: Any,
    repository: Path,
    input_name: str,
    revision: str,
    output: Path,
    config: Any,
    stage: Mapping[str, Any],
) -> Any:
    if getattr(config, "sources", ()):
        workflow.sources = tuple(config.sources)
    if stage.get("kind") == "core":
        from .promotion_source import prepare_core_source

        return prepare_core_source(
            workflow,
            repository,
            input_name,
            revision,
            output,
            config,
            stage,
        )

    from .promotion_workflow import prepare_promotion as native_prepare_promotion

    allow_local_fixture = bool(getattr(config, "allow_local_fixture", False))
    return native_prepare_promotion(
        workflow,
        repository,
        input_name,
        revision,
        output,
        allow_local_fixture=allow_local_fixture,
    )
