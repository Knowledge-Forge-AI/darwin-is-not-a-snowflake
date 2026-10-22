"""Native promotion binding dispatch; registered-domain rules stay unchanged.

As in campaign routing, ``core`` is reserved for the direct native subflake
input. Composition owners must not register a domain module under that name.
"""

from __future__ import annotations

from typing import Any, Mapping

from .paths import ConfigError
from .promotion_contract import contract_release_ref, resolve_contract_input
from .promotion_composition import (
    COMPOSITION_AUTHORIZATION_SCHEMA,
    authorize_composition_repository_transition,
    validate_composition_repository_authorization,
)
from .promotion_core import (
    CORE_AUTHORIZATION_SCHEMA,
    CORE_LAYOUT_AUTHORIZATION_SCHEMA,
    CORE_REPOSITORY_AUTHORIZATION_SCHEMA,
    CORE_SUBFLAKE,
    _validate_core_source,
    _validate_revision_and_hash,
    authorize_core_lock_transition,
    resolve_core_node,
    validate_core_lock_authorization,
)
from .promotion_lock import _record, authorize_owned_lock_transition, validate_owned_closure_authorization


def resolve_binding(
    contract: Mapping[str, Any], input_name: str, lock: Mapping[str, Any],
    *, allow_file: bool = False,
) -> dict[str, str]:
    if input_name != "core":
        return resolve_contract_input(contract, input_name)
    node = _record(lock["nodes"][resolve_core_node(lock)], "core promotion binding node")
    source = _validate_core_source(
        node, "core promotion binding",
        expected_subflake=_record(node.get("original"), "core promotion original source").get("dir", ""), allow_file=allow_file
    )
    _validate_revision_and_hash(node["locked"], "core promotion binding")
    return {"rule": "direct-core-subflake", "module": "core", "repository_url": source["url"]}


def release_ref(contract: Mapping[str, Any], input_name: str) -> str:
    return "refs/heads/main" if input_name == "core" else contract_release_ref(contract, input_name)


def authorize_transition(
    before: Mapping[str, Any], after: Mapping[str, Any], node: str,
    repository_url: str, revision: str, *, input_name: str, allow_file: bool,
    expected_ref: str, allow_implicit_main: bool,
    allow_layout_transition: bool = False,
    allow_repository_transition: bool = False,
    allow_composition_repository_transition: bool = False,
) -> dict[str, Any]:
    if allow_composition_repository_transition and (allow_repository_transition or allow_layout_transition):
        raise ConfigError("cannot combine core and composition repository transitions")
    if allow_composition_repository_transition:
        if input_name != "composition-nd":
            raise ConfigError("composition repository transition is valid only for input composition-nd")
        return authorize_composition_repository_transition(
            before, after, node, revision, input_name=input_name, allow_file=allow_file,
            expected_ref=expected_ref, allow_implicit_main=allow_implicit_main,
        )
    if input_name == "core":
        if expected_ref != "refs/heads/main":
            raise ConfigError("core promotion requires the main release ref")
        return authorize_core_lock_transition(
            before, after, revision, repository_url=repository_url, allow_file=allow_file,
            allow_layout_transition=allow_layout_transition,
            allow_repository_transition=allow_repository_transition,
        )
    if allow_repository_transition or allow_layout_transition:
        raise ConfigError("repository or layout transitions are supported only for core")
    return authorize_owned_lock_transition(
        before, after, node, repository_url, revision, allow_file=allow_file,
        expected_ref=expected_ref, allow_implicit_main=allow_implicit_main,
    )


def authorization_kind(record: Mapping[str, Any]) -> str:
    schema = record.get("schema")
    if schema == CORE_AUTHORIZATION_SCHEMA:
        return "core"
    if schema == CORE_LAYOUT_AUTHORIZATION_SCHEMA:
        return "core-layout-transition"
    if schema == CORE_REPOSITORY_AUTHORIZATION_SCHEMA:
        return "core-repository-transition"
    if schema == COMPOSITION_AUTHORIZATION_SCHEMA:
        return "composition-repository-transition"
    return "owned-closure"


def validate_authorization(record: Any) -> dict[str, Any]:
    if isinstance(record, dict) and record.get("schema") in (
        CORE_AUTHORIZATION_SCHEMA,
        CORE_LAYOUT_AUTHORIZATION_SCHEMA,
        CORE_REPOSITORY_AUTHORIZATION_SCHEMA,
    ):
        return validate_core_lock_authorization(record)
    if isinstance(record, dict) and record.get("schema") == COMPOSITION_AUTHORIZATION_SCHEMA:
        return validate_composition_repository_authorization(record)
    return validate_owned_closure_authorization(record)
