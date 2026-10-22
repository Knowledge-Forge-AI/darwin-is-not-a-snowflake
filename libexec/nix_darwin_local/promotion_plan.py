from __future__ import annotations

import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .deployment_paths import (
    atomic_private_write,
    exclusive_private_write,
    fsync_directory,
    require_private_directory,
    require_private_file,
)
from .deployment_plan import canonical_bytes, lock_delta, normalized_lock, sha256_bytes
from .paths import ConfigError, validate_input_path, validate_portable_path
from .promotion_contract import (
    HEX_REVISION,
    SAFE_IDENTITY,
    validate_release_ref,
    candidate_reference,
    contract_repository_url,  # Compatibility re-export used by existing consumers.
    normalize_nix_repository_url,
    reject_promotion_secrets,
    repository_identity_key,
    repository_urls_match,
    resolve_contract_input,
    strict_json_loads,
    validate_contract,
)
from .promotion_result import (
    qualification_host_derivations,
    validate_impact_record,
    validate_native_result,
)
from .promotion_binding import (
    authorize_transition, authorization_kind, validate_authorization,
    release_ref as binding_release_ref, resolve_binding,
)
from .promotion_composition import (
    COMPOSITION_AUTHORIZATION_SCHEMA,
    COMPOSITION_INPUT,
    COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_KEY,
    COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL,
    COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_KEY,
    COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_URL,
    validate_composition_transition_contract,
)
from .promotion_core import (
    CORE_REPOSITORY_AUTHORIZATION_SCHEMA,
    CORE_REPOSITORY_TRANSITION_CANDIDATE_URL,
    CORE_REPOSITORY_TRANSITION_PREIMAGE_URL,
)


PLAN_SCHEMA = "nix-update-promotion-plan-v2"
PLAN_SCHEMA_V2 = "nix-update-promotion-plan-v2"
PLAN_SCHEMA_V3 = "nix-update-promotion-plan-v3"
PLAN_SCHEMA_V4 = "nix-update-promotion-plan-v4"
PLAN_SCHEMA_CORE_REPOSITORY_TRANSITION_V1 = (
    "nix-update-promotion-core-repository-transition-plan-v1"
)
PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1 = (
    "nix-update-promotion-composition-repository-transition-plan-v1"
)
RESULT_SCHEMA = "nix-update-promotion-result-v1"
RESULT_SCHEMA_V1 = "nix-update-promotion-result-v1"
RESULT_SCHEMA_V2 = "nix-update-promotion-result-v2"
HEX_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class PromotionArtifact:
    directory: Path
    document: dict[str, Any]
    digest: str


def write_plan(directory: Path, document: dict[str, Any], markdown: str) -> PromotionArtifact:
    if not directory.is_absolute():
        raise ConfigError("promotion plan directory must be absolute")
    require_private_directory(directory, "promotion plan directory")
    validate_plan(document, allow_unsigned=True)
    reject_promotion_secrets(document)
    unsigned = dict(document)
    unsigned["plan_digest"] = ""
    digest = sha256_bytes(canonical_bytes(unsigned))
    final = dict(document)
    final["plan_digest"] = digest
    validate_plan(final)
    plan_bytes = canonical_bytes(final)
    exclusive_private_write(directory / "plan.json", plan_bytes)
    exclusive_private_write(
        directory / "plan.md", markdown.replace("{PLAN_DIGEST}", digest).encode("utf-8")
    )
    fsync_directory(directory)
    return PromotionArtifact(directory, strict_json_loads(plan_bytes.decode("utf-8"), "promotion plan"), digest)


def load_plan(path: Path) -> PromotionArtifact:
    if not path.is_absolute():
        raise ConfigError("promotion plan path must be absolute")
    directory = path if path.is_dir() else path.parent
    json_path = directory / "plan.json" if path.is_dir() else path
    require_private_directory(directory, "promotion plan directory")
    require_private_file(json_path, "promotion plan")
    raw = json_path.read_bytes()
    try:
        document = strict_json_loads(raw.decode("utf-8"), "promotion plan")
    except UnicodeDecodeError as error:
        raise ConfigError("promotion plan is not valid JSON") from error
    if not isinstance(document, dict) or raw != canonical_bytes(document):
        raise ConfigError("promotion plan bytes are not canonical")
    validate_plan(document)
    recorded = document["plan_digest"]
    unsigned = dict(document)
    unsigned["plan_digest"] = ""
    if sha256_bytes(canonical_bytes(unsigned)) != recorded:
        raise ConfigError("promotion plan digest does not match canonical content")
    reject_promotion_secrets(document)
    _validate_bound_artifacts(directory, document)
    return PromotionArtifact(directory, document, recorded)


def validate_plan(document: Any, *, allow_unsigned: bool = False) -> None:
    if not isinstance(document, dict):
        raise ConfigError("promotion plan must be an object")
    schema = document.get("schema")
    if schema not in {
        PLAN_SCHEMA_V2,
        PLAN_SCHEMA_V3,
        PLAN_SCHEMA_V4,
        PLAN_SCHEMA_CORE_REPOSITORY_TRANSITION_V1,
        PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1,
    }:
        raise ConfigError(
            f"promotion plan schema must be {PLAN_SCHEMA_V2}, {PLAN_SCHEMA_V3}, {PLAN_SCHEMA_V4}, {PLAN_SCHEMA_CORE_REPOSITORY_TRANSITION_V1}, or {PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1}"
        )
    status = document.get("status")
    if status == "BLOCKED":
        _validate_blocked_plan(document)
    elif status == "ALREADY_SELECTED":
        _validate_already_selected_plan(document)
    elif status == "READY_FOR_REVIEW":
        _validate_ready_plan(document)
    else:
        raise ConfigError("promotion plan status is invalid")
    digest = document.get("plan_digest")
    if allow_unsigned:
        if digest != "":
            raise ConfigError("unsigned promotion plan digest must be empty")
    elif not isinstance(digest, str) or not HEX_SHA256.fullmatch(digest):
        raise ConfigError("promotion plan digest is invalid")
    if document.get("no_activation") is not True:
        raise ConfigError("promotion plan must explicitly forbid activation")


def _validate_blocked_plan(document: Mapping[str, Any]) -> None:
    _exact_keys(
        document,
        {"schema", "status", "blockers", "prepared_at", "no_activation", "replay", "plan_digest"},
        "inapplicable promotion plan",
    )
    if not isinstance(document["blockers"], list) or not document["blockers"]:
        raise ConfigError("blocked promotion plan must contain blockers")


def _validate_already_selected_plan(document: Mapping[str, Any]) -> None:
    is_v3 = document.get("schema") in {PLAN_SCHEMA_V3, PLAN_SCHEMA_V4}
    if is_v3:
        auth_key = "source_authority"
        _exact_keys(
            document,
            {
                "schema", "status", "blockers", "prepared_at", "tool", auth_key,
                "composition_catalog", "local_sources",
                "contract", "contract_canonical", "contract_digest", "contract_size",
                "qualifier", "input", "input_resolution", "canonical_repository_url",
                "canonical_transport_url", "revision", "candidate_ref", "local_fixture",
                "selected_lock", "qualifier_result", "locked_qualification",
                "local_ref", "baseline_host_observation", "impact", "impact_digest",
                "non_mutation", "no_activation", "replay", "plan_digest",
            },
            "already-selected promotion plan",
        )
    else:
        _exact_keys(
            document,
            {
                "schema", "status", "blockers", "prepared_at", "tool", "repository",
                "contract", "contract_canonical", "contract_digest", "contract_size",
                "qualifier", "input", "input_resolution", "canonical_repository_url",
                "canonical_transport_url", "revision", "candidate_ref", "local_fixture",
                "selected_lock", "qualifier_result", "locked_qualification",
                "remote_ref", "baseline_host_observation", "impact", "impact_digest",
                "non_mutation", "no_activation", "replay", "plan_digest",
            },
            "already-selected promotion plan",
        )
    if document["blockers"] != []:
        raise ConfigError("already-selected promotion plan may not contain blockers")
    contract = _validate_native_context(document)
    _validate_artifact_binding(document["selected_lock"], "selected lock", extra_key="normalized")
    _validate_locked_qualification(document["locked_qualification"], contract)
    _validate_impact_evidence(document, contract, require_identical=True)
    if is_v3:
        if document["non_mutation"] != {
            "git_authority_unchanged": True,
            "lock_bytes_unchanged": True,
            "local_ref_unchanged": True,
        }:
            raise ConfigError("already-selected non-mutation evidence is invalid")
    else:
        if document["non_mutation"] != {
            "git_authority_unchanged": True,
            "lock_bytes_unchanged": True,
            "remote_ref_unchanged": True,
        }:
            raise ConfigError("already-selected non-mutation evidence is invalid")


def _validate_ready_plan(document: Mapping[str, Any]) -> None:
    schema = document.get("schema")
    is_v3 = schema in {PLAN_SCHEMA_V3, PLAN_SCHEMA_V4}
    is_core_repo_transition = schema == PLAN_SCHEMA_CORE_REPOSITORY_TRANSITION_V1
    is_comp_repo_transition = schema == PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1
    if is_comp_repo_transition:
        _exact_keys(
            document,
            {
                "schema", "status", "blockers", "prepared_at", "tool", "source_authority",
                "composition_catalog", "local_sources",
                "contract", "contract_canonical", "contract_digest", "contract_size",
                "candidate_contract", "candidate_contract_canonical", "candidate_contract_digest", "candidate_contract_size",
                "qualifier", "input", "input_resolution", "preimage_input_resolution",
                "canonical_repository_url", "canonical_transport_url", "revision",
                "candidate_ref", "local_fixture",
                "entry_lock", "candidate_lock", "entry_declaration", "candidate_declaration",
                "lock_delta", "closure_authorization", "lock_authorization_evidence",
                "qualifier_result", "locked_qualification", "commit",
                "local_ref", "baseline_host_observation", "impact", "impact_digest",
                "confirmation", "no_activation", "replay", "plan_digest",
            },
            "promotion composition repository-transition plan",
        )
    elif is_core_repo_transition:
        _exact_keys(
            document,
            {
                "schema", "status", "blockers", "prepared_at", "tool", "source_authority",
                "composition_catalog", "local_sources",
                "contract", "contract_canonical", "contract_digest", "contract_size",
                "qualifier", "input", "input_resolution", "preimage_input_resolution",
                "canonical_repository_url", "canonical_transport_url", "revision",
                "candidate_ref", "local_fixture",
                "entry_lock", "candidate_lock", "entry_declaration", "candidate_declaration",
                "lock_delta", "closure_authorization", "lock_authorization_evidence",
                "qualifier_result", "locked_qualification", "commit",
                "local_ref", "baseline_host_observation", "impact", "impact_digest",
                "confirmation", "no_activation", "replay", "plan_digest",
            },
            "promotion repository-transition plan",
        )
    elif is_v3:
        auth_key = "source_authority"
        _exact_keys(
            document,
            {
                "schema", "status", "blockers", "prepared_at", "tool", auth_key,
                "composition_catalog", "local_sources",
                "contract", "contract_canonical", "contract_digest", "contract_size",
                "qualifier", "input", "input_resolution", "canonical_repository_url",
                "canonical_transport_url", "revision", "candidate_ref", "local_fixture",
                "entry_lock", "candidate_lock", "lock_delta",
                "closure_authorization", "lock_authorization_evidence",
                "qualifier_result", "locked_qualification", "commit",
                "local_ref", "baseline_host_observation", "impact", "impact_digest",
                "confirmation", "no_activation", "replay", "plan_digest",
            },
            "promotion plan",
        )
    else:
        _exact_keys(
            document,
            {
                "schema", "status", "blockers", "prepared_at", "tool", "repository",
                "contract", "contract_canonical", "contract_digest", "contract_size",
                "qualifier", "input", "input_resolution", "canonical_repository_url",
                "canonical_transport_url", "revision", "candidate_ref", "local_fixture",
                "entry_lock", "candidate_lock", "lock_delta",
                "closure_authorization", "lock_authorization_evidence",
                "qualifier_result", "locked_qualification", "commit",
                "remote_ref", "baseline_host_observation", "impact", "impact_digest",
                "publication", "confirmation", "no_activation", "replay", "plan_digest",
            },
            "promotion plan",
        )
    if document["blockers"] != [] or document["no_activation"] is not True:
        raise ConfigError("review-ready promotion plan status is inconsistent")
    contract = _validate_native_context(document)
    _validate_ready_artifacts(document, contract)
    _validate_commit_and_qualification(document)
    _validate_impact_evidence(document, contract, require_identical=False)


def _validate_native_context(document: Mapping[str, Any]) -> Mapping[str, Any]:
    if type(document["local_fixture"]) is not bool:
        raise ConfigError("promotion plan local-fixture marker is invalid")
    contract = document["contract"]
    if not isinstance(contract, dict):
        raise ConfigError("promotion plan contract is invalid")
    validate_contract(contract, allow_file_urls=document["local_fixture"])
    raw = canonical_bytes(contract)
    if (
        document["contract_canonical"] != raw.decode("ascii")
        or document["contract_size"] != len(raw)
        or document["contract_digest"] != sha256_bytes(raw)
    ):
        raise ConfigError("promotion plan contract digest or canonical bytes are invalid")
    if not isinstance(document["input"], str):
        raise ConfigError("promotion plan input is invalid")
    schema = document.get("schema")
    is_core_repo_transition = schema == PLAN_SCHEMA_CORE_REPOSITORY_TRANSITION_V1
    is_comp_repo_transition = schema == PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1
    if is_core_repo_transition:
        if document["input"] != "core":
            raise ConfigError("core repository transition plan requires input core")
        _validate_artifact_binding(document["entry_lock"], "entry_lock", extra_key="normalized")
        _validate_artifact_binding(document["candidate_lock"], "candidate_lock", extra_key="normalized")
        preimage_res = resolve_binding(
            contract, "core", document["entry_lock"]["normalized"],
            allow_file=document["local_fixture"],
        )
        if preimage_res["repository_url"] != CORE_REPOSITORY_TRANSITION_PREIMAGE_URL:
            raise ConfigError("core repository transition plan requires historical .flakes preimage URL")
        if document.get("preimage_input_resolution") != preimage_res:
            raise ConfigError("promotion plan preimage input URL resolution drifted")
        candidate_res = resolve_binding(
            contract, "core", document["candidate_lock"]["normalized"],
            allow_file=document["local_fixture"],
        )
        if candidate_res["repository_url"] != CORE_REPOSITORY_TRANSITION_CANDIDATE_URL:
            raise ConfigError("core repository transition plan requires canonical darwin-is-not-a-snowflake candidate URL")
        if document["input_resolution"] != candidate_res:
            raise ConfigError("promotion plan native input URL resolution drifted")
        resolution = candidate_res
    elif is_comp_repo_transition:
        if document["input"] != "composition-nd":
            raise ConfigError("composition repository transition plan requires input composition-nd")
        _validate_artifact_binding(document["entry_lock"], "entry_lock", extra_key="normalized")
        _validate_artifact_binding(document["candidate_lock"], "candidate_lock", extra_key="normalized")
        preimage_res = resolve_binding(
            contract, "composition-nd", document["entry_lock"]["normalized"],
            allow_file=document["local_fixture"],
        )
        if (
            repository_identity_key(normalize_nix_repository_url(preimage_res["repository_url"], allow_file=document["local_fixture"]))
            != COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_KEY
        ):
            raise ConfigError("composition repository transition plan requires historical composition-nd preimage URL")
        if document.get("preimage_input_resolution") != preimage_res:
            raise ConfigError("promotion plan preimage input URL resolution drifted")

        cand_contract = document.get("candidate_contract")
        if not isinstance(cand_contract, dict):
            raise ConfigError("composition repository transition plan candidate contract is invalid")
        validate_contract(cand_contract, allow_file_urls=document["local_fixture"])
        cand_raw = canonical_bytes(cand_contract)
        if (
            document.get("candidate_contract_canonical") != cand_raw.decode("ascii")
            or document.get("candidate_contract_size") != len(cand_raw)
            or document.get("candidate_contract_digest") != sha256_bytes(cand_raw)
        ):
            raise ConfigError("promotion plan candidate contract digest or canonical bytes are invalid")
        validate_composition_transition_contract(
            contract, cand_contract, allow_file=document["local_fixture"]
        )

        candidate_res = resolve_binding(
            cand_contract, "composition-nd", document["candidate_lock"]["normalized"],
            allow_file=document["local_fixture"],
        )
        if (
            repository_identity_key(normalize_nix_repository_url(candidate_res["repository_url"], allow_file=document["local_fixture"]))
            != COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_KEY
        ):
            raise ConfigError("composition repository transition plan requires canonical composition_dinas candidate URL")
        # Pin the workflow's retained Git transport spelling; the candidate contract
        # may use Nix's git+ spelling. Preserve every other binding field exactly.
        retained_res = {
            **candidate_res, "repository_url": COMPOSITION_REPOSITORY_TRANSITION_CANDIDATE_URL
        }
        retained_url = normalize_nix_repository_url(
            retained_res["repository_url"], allow_file=document["local_fixture"]
        )
        candidate_url = normalize_nix_repository_url(
            candidate_res["repository_url"], allow_file=document["local_fixture"]
        )
        if (
            repository_identity_key(retained_url) != repository_identity_key(candidate_url)
            or document["input_resolution"] != retained_res
        ):
            raise ConfigError("promotion plan native input URL resolution drifted")
        resolution = retained_res
    elif document["input"] == "core":
        lock_field = "entry_lock" if document["status"] == "READY_FOR_REVIEW" else "selected_lock"
        _validate_artifact_binding(document[lock_field], lock_field, extra_key="normalized")
        resolution = resolve_binding(
            contract, "core", document[lock_field]["normalized"],
            allow_file=document["local_fixture"],
        )
        if document["input_resolution"] != resolution:
            raise ConfigError("promotion plan native input URL resolution drifted")
    else:
        resolution = resolve_contract_input(contract, document["input"])
        if document["input_resolution"] != resolution:
            raise ConfigError("promotion plan native input URL resolution drifted")
    expected_url = resolution["repository_url"]
    if document["canonical_repository_url"] != expected_url:
        raise ConfigError("promotion plan canonical repository URL drifted")
    expected_transport = normalize_nix_repository_url(
        expected_url, allow_file=document["local_fixture"]
    )
    if document["canonical_transport_url"] != expected_transport:
        raise ConfigError("promotion plan canonical Git transport URL drifted")
    if document["candidate_ref"] != candidate_reference(
        expected_url, document["revision"], allow_file=document["local_fixture"]
    ):
        raise ConfigError("promotion plan candidate reference is not exact")
    is_repo_transition = is_core_repo_transition or is_comp_repo_transition
    release_ref = binding_release_ref(contract, document["input"])
    if is_repo_transition and release_ref != "refs/heads/main":
        raise ConfigError(
            "composition repository transition plan requires main release ref"
            if is_comp_repo_transition
            else "core repository transition plan requires main release ref"
        )
    if "local_ref" in document:
        if document["local_ref"] != {
            "ref": release_ref,
            "revision": document["revision"],
        }:
            raise ConfigError("promotion plan local release-ref evidence is invalid")
    else:
        if document.get("remote_ref") != {
            "ref": release_ref,
            "revision": document["revision"],
        }:
            raise ConfigError("promotion plan remote release-ref evidence is invalid")
    if "composition_catalog" in document:
        _validate_composition_catalog(document["composition_catalog"], contract, document)
        lock_field = "candidate_lock" if document["status"] == "READY_FOR_REVIEW" else "selected_lock"
        if schema == PLAN_SCHEMA_V4:
            from .promotion_locked_source import validate_promotion_local_sources

            preimage = (
                document["entry_lock"]["normalized"]
                if document["status"] == "READY_FOR_REVIEW"
                else document[lock_field]["normalized"]
            )
            selected_node = _resolve_lock_input(document[lock_field]["normalized"], document["input"])
            validate_promotion_local_sources(
                document["local_sources"],
                document[lock_field]["normalized"],
                preimage_lock=preimage,
                selected_node=selected_node,
            )
            if not any(
                item.get("node") == selected_node
                and item.get("role") == "exact"
                and item.get("source", {}).get("revision") == document["revision"]
                and item.get("source", {}).get("release_ref") == document["local_ref"]["ref"]
                and item.get("realization", {}).get("revision") == document["revision"]
                and repository_urls_match(
                    item.get("source", {}).get("repository"),
                    document["canonical_repository_url"],
                    allow_file=document["local_fixture"],
                )
                for item in document["local_sources"]
            ):
                raise ConfigError("promotion local source bindings omit the selected candidate")
        else:
            from .local_source_binding import validate_local_sources

            validate_local_sources(document["local_sources"], document[lock_field]["normalized"], strict=True)
            if not any(item["source"]["revision"] == document["revision"] and
                       item["source"]["release_ref"] == document["local_ref"]["ref"] and
                       repository_urls_match(item["source"]["repository"], document["canonical_repository_url"], allow_file=document["local_fixture"])
                       for item in document["local_sources"]):
                raise ConfigError("promotion local source bindings omit the selected candidate")
    authority = document.get("source_authority") or document.get("repository")
    if authority is not None:
        _validate_source_authority(authority)
    qualifier = document["qualifier"]
    if not isinstance(qualifier, dict):
        raise ConfigError("promotion plan qualifier identity is invalid")
    _exact_keys(qualifier, {"relative_path", "identity"}, "promotion plan qualifier")
    if qualifier["relative_path"] != contract["qualification"]["entryPoint"]:
        raise ConfigError("promotion plan qualifier path drifted from the contract")
    _validate_tool_identity(document["tool"])
    _validate_artifact_binding(document["qualifier_result"], "qualifier result", extra_key="summary")
    return contract


def _validate_composition_catalog(
    cat: Any,
    contract: Mapping[str, Any],
    document: Mapping[str, Any],
) -> None:
    if not isinstance(cat, dict):
        raise ConfigError("composition catalog identity must be an object")
    _exact_keys(cat, {"key", "checkout", "repository", "release_ref", "publication"}, "composition catalog identity")
    validate_input_path(cat["key"])
    if (not isinstance(cat["checkout"], str) or not Path(cat["checkout"]).is_absolute()
            or str(Path(cat["checkout"])) != cat["checkout"] or ".." in Path(cat["checkout"]).parts):
        raise ConfigError("composition catalog checkout must be an absolute path")
    validate_portable_path(cat["checkout"])
    if not isinstance(cat["repository"], str) or not cat["repository"]:
        raise ConfigError("composition catalog repository is invalid")
    validate_release_ref(cat["release_ref"])
    if cat["publication"] is not None:
        if not isinstance(cat["publication"], dict):
            raise ConfigError("composition catalog publication must be an object or null")
        _exact_keys(cat["publication"], {"remote", "ref"}, "composition catalog publication")
        if not isinstance(cat["publication"]["remote"], str) or not SAFE_IDENTITY.fullmatch(cat["publication"]["remote"]):
            raise ConfigError("composition catalog publication fields are invalid")
        validate_release_ref(cat["publication"]["ref"])
    authority = document["source_authority"]
    if (not isinstance(authority, dict) or authority.get("repository_root") != cat["checkout"]
            or "refs/heads/" + str(authority.get("branch")) != cat["release_ref"]):
        raise ConfigError("composition catalog checkout/ref disagrees with local Git authority")
    if not repository_urls_match(
        contract["composition"]["repositoryUrl"],
        cat["repository"],
        allow_file=document["local_fixture"],
    ):
        raise ConfigError("composition catalog repository disagrees with contract composition")


def _validate_source_authority(authority: Any) -> None:
    if not isinstance(authority, dict):
        raise ConfigError("source authority must be an object")
    required = {"commit", "tree", "branch", "status", "repository_root"}
    if not required <= set(authority):
        raise ConfigError("source authority fields are incomplete")
    if not isinstance(authority["commit"], str) or not HEX_REVISION.fullmatch(authority["commit"]):
        raise ConfigError("source authority commit is invalid")
    if not isinstance(authority["tree"], str) or not HEX_REVISION.fullmatch(authority["tree"]):
        raise ConfigError("source authority tree is invalid")
    if not isinstance(authority["branch"], str) or not authority["branch"]:
        raise ConfigError("source authority branch is invalid")
    if not isinstance(authority["repository_root"], str) or not Path(authority["repository_root"]).is_absolute():
        raise ConfigError("source authority repository root is invalid")


def _validate_ready_artifacts(document: Mapping[str, Any], contract: Mapping[str, Any]) -> None:
    schema = document.get("schema")
    is_core_repo_transition = schema == PLAN_SCHEMA_CORE_REPOSITORY_TRANSITION_V1
    is_comp_repo_transition = schema == PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1
    is_repo_transition = is_core_repo_transition or is_comp_repo_transition
    for field in ("entry_lock", "candidate_lock"):
        _validate_artifact_binding(document[field], field, extra_key="normalized")
    if is_repo_transition:
        _validate_artifact_binding(document["entry_declaration"], "entry declaration")
        _validate_artifact_binding(document["candidate_declaration"], "candidate declaration")
    _validate_artifact_binding(
        document["lock_authorization_evidence"], "lock authorization evidence"
    )
    if not isinstance(document["lock_delta"], list):
        raise ConfigError("promotion plan lock authorization is invalid")
    before = document["entry_lock"]["normalized"]
    after = document["candidate_lock"]["normalized"]
    if document["lock_delta"] != lock_delta(before, after):
        raise ConfigError("promotion plan lock delta is not canonical")
    reviewed = validate_authorization(document["closure_authorization"])
    if is_core_repo_transition and reviewed.get("schema") != CORE_REPOSITORY_AUTHORIZATION_SCHEMA:
        raise ConfigError("core repository transition plan requires repository transition authorization schema")
    if is_comp_repo_transition and reviewed.get("schema") != COMPOSITION_AUTHORIZATION_SCHEMA:
        raise ConfigError("composition repository transition plan requires composition repository transition authorization schema")
    selected = _resolve_lock_input(after, document["input"])
    if selected != _resolve_lock_input(before, document["input"]):
        raise ConfigError("promotion plan selected root node name changed")
    expected_ref = (
        document["local_ref"]["ref"]
        if "local_ref" in document
        else document["remote_ref"]["ref"]
    )
    if is_core_repo_transition:
        expected_auth_url = CORE_REPOSITORY_TRANSITION_PREIMAGE_URL
    elif is_comp_repo_transition:
        expected_auth_url = COMPOSITION_REPOSITORY_TRANSITION_PREIMAGE_URL
    else:
        expected_auth_url = document["canonical_repository_url"]
    actual = authorize_transition(
        before,
        after,
        selected,
        expected_auth_url,
        document["revision"],
        input_name=document["input"],
        allow_file=document["local_fixture"],
        expected_ref=expected_ref,
        allow_implicit_main=contract["version"] == 1,
        allow_repository_transition=is_core_repo_transition,
        allow_composition_repository_transition=is_comp_repo_transition,
    )
    if reviewed != actual:
        raise ConfigError("promotion plan owned-closure authorization is not exact")


def _validate_commit_and_qualification(document: Mapping[str, Any]) -> None:
    commit = document["commit"]
    if not isinstance(commit, dict):
        raise ConfigError("promotion commit policy is invalid")
    _exact_keys(commit, {"subject", "body", "changed_paths"}, "promotion commit policy")
    schema = document.get("schema")
    is_core_repo_transition = schema == PLAN_SCHEMA_CORE_REPOSITORY_TRANSITION_V1
    is_comp_repo_transition = schema == PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1
    is_repo_transition = is_core_repo_transition or is_comp_repo_transition
    expected_paths = ["flake.nix", "flake.lock"] if is_repo_transition else ["flake.lock"]
    if commit["changed_paths"] != expected_paths or not all(
        isinstance(commit[item], str) and commit[item].strip() for item in ("subject", "body")
    ):
        if is_repo_transition:
            raise ConfigError("promotion commit policy must bind a flake.nix and flake.lock commit")
        raise ConfigError("promotion commit policy must bind a flake.lock-only commit")
    if document["confirmation"] != "APPLY NIX-UPDATE PROMOTION {PLAN_DIGEST}":
        raise ConfigError("promotion confirmation shape is invalid")
    qualification_contract = (
        document["candidate_contract"] if is_comp_repo_transition else document["contract"]
    )
    _validate_locked_qualification(document["locked_qualification"], qualification_contract)
    if not isinstance(document.get("replay"), dict):
        raise ConfigError("promotion publication or replay authority is invalid")
    if schema not in {
        PLAN_SCHEMA_V3,
        PLAN_SCHEMA_V4,
        PLAN_SCHEMA_CORE_REPOSITORY_TRANSITION_V1,
        PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1,
    }:
        if not isinstance(document.get("publication"), dict):
            raise ConfigError("promotion publication or replay authority is invalid")


def _validate_impact_evidence(
    document: Mapping[str, Any],
    contract: Mapping[str, Any],
    *,
    require_identical: bool,
) -> None:
    impact = validate_impact_record(document["impact"], contract["hosts"])
    if document["impact_digest"] != sha256_bytes(canonical_bytes(impact)):
        raise ConfigError("promotion impact digest is invalid")
    baseline = document["baseline_host_observation"]
    if (
        not isinstance(baseline, dict)
        or set(baseline) != {"hosts", "commands"}
        or baseline["hosts"] != impact["baselineHosts"]
        or not isinstance(baseline["commands"], list)
        or len(baseline["commands"]) != len(contract["hosts"])
    ):
        raise ConfigError("promotion baseline host observation evidence is invalid")
    for host, command in zip(contract["hosts"], baseline["commands"], strict=True):
        expected_argv = [
            "nix",
            "eval",
            "--raw",
            f'path:.#darwinConfigurations."{host}".system.drvPath',
            "--no-update-lock-file",
            "--no-write-lock-file",
        ]
        if (
            not isinstance(command, dict)
            or command.get("label") != f"promotion baseline host derivation {host}"
            or list(command.get("argv", [])) != expected_argv
            or command.get("exit_status") != 0
        ):
            raise ConfigError("promotion baseline host observation command is invalid")
    candidate = qualification_host_derivations(
        document["locked_qualification"], contract["hosts"]
    )
    if candidate != impact["candidateHosts"]:
        raise ConfigError("promotion candidate host evidence drifted from impact")
    if require_identical and (
        impact["classification"] != "HOST_DERIVATIONS_IDENTICAL"
        or impact["baselineHosts"] != impact["candidateHosts"]
    ):
        raise ConfigError("already-selected promotion impact must be identical")


def _validate_locked_qualification(
    locked: Any, contract: Mapping[str, Any]
) -> None:
    if not isinstance(locked, dict):
        raise ConfigError("promotion locked qualification evidence is invalid")
    _exact_keys(locked, {"qualifier", "checks", "hosts", "commands", "no_activation"}, "promotion locked qualification")
    _validate_artifact_binding(locked["qualifier"], "locked qualifier result", extra_key="summary")
    if locked["no_activation"] is not True:
        raise ConfigError("promotion locked qualification must forbid activation")
    checks = locked["checks"]
    if (
        not isinstance(checks, list)
        or [item.get("name") for item in checks if isinstance(item, dict)] != contract["checks"]
        or any(item.get("outcome") != "pass" for item in checks if isinstance(item, dict))
        or any(not isinstance(item, dict) for item in checks)
    ):
        raise ConfigError("promotion locked declared-check evidence is invalid")
    hosts = locked["hosts"]
    if (
        not isinstance(hosts, list)
        or [item.get("name") for item in hosts if isinstance(item, dict)] != contract["hosts"]
        or any(item.get("outcome") != "pass" for item in hosts if isinstance(item, dict))
        or any(
            not isinstance(item.get("derivation"), str)
            or not item["derivation"].startswith("/nix/store/")
            or not item["derivation"].endswith(".drv")
            for item in hosts if isinstance(item, dict)
        )
        or any(not isinstance(item, dict) for item in hosts)
    ):
        raise ConfigError("promotion locked host evidence is invalid")
    if not isinstance(locked["commands"], list):
        raise ConfigError("promotion locked command evidence is invalid")


def _validate_tool_identity(value: Any) -> None:
    if not isinstance(value, dict):
        raise ConfigError("promotion tool identity is invalid")
    _exact_keys(value, {"name", "version", "files", "external", "digest"}, "promotion tool identity")
    if value["name"] != "nix-update-promote" or value["version"] != "1":
        raise ConfigError("promotion tool name or version is invalid")
    files = value["files"]
    if not isinstance(files, list) or not files:
        raise ConfigError("promotion tool source closure is empty")
    paths = [item.get("path") for item in files if isinstance(item, dict)]
    if len(paths) != len(files) or paths != sorted(paths) or len(set(paths)) != len(paths):
        raise ConfigError("promotion tool source closure paths are invalid")
    for item in files:
        if (
            set(item) != {"path", "mode", "uid", "gid", "kind", "link_count", "sha256", "size"}
            or item["kind"] != "regular"
            or item["link_count"] != 1
            or not isinstance(item["path"], str)
            or not _safe_relative_path(item["path"])
            or not isinstance(item["sha256"], str)
            or HEX_SHA256.fullmatch(item["sha256"]) is None
        ):
            raise ConfigError("promotion tool source closure identity is invalid")
    if not isinstance(value["external"], dict) or set(value["external"]) != {"git", "nix"}:
        raise ConfigError("promotion external executable closure is invalid")
    material = {"files": files, "external": value["external"]}
    if value["digest"] != sha256_bytes(canonical_bytes(material)):
        raise ConfigError("promotion tool closure digest is invalid")


def write_result(directory: Path, document: dict[str, Any]) -> Path:
    if not directory.is_absolute():
        raise ConfigError("promotion result directory must be absolute")
    require_private_directory(directory, "promotion result directory")
    final = dict(document)
    if "schema" not in final:
        final["schema"] = RESULT_SCHEMA
    elif final["schema"] not in {RESULT_SCHEMA_V1, RESULT_SCHEMA_V2}:
        raise ConfigError("promotion result schema is invalid")
    reject_promotion_secrets(final)
    path = directory / "result.json"
    atomic_private_write(path, canonical_bytes(final))
    return path


def _safe_relative_path(value: str) -> bool:
    path = PurePosixPath(value)
    return not path.is_absolute() and bool(path.parts) and all(part not in {"", ".", ".."} for part in path.parts)


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ConfigError(f"{label} fields are invalid")


def _validate_artifact_binding(value: Any, label: str, *, extra_key: str | None = None) -> None:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} binding must be an object")
    keys = {"path", "sha256", "size"}
    if extra_key is not None:
        keys.add(extra_key)
    _exact_keys(value, keys, f"{label} binding")
    if not isinstance(value["path"], str) or not _safe_relative_path(value["path"]):
        raise ConfigError(f"{label} path is invalid")
    if not isinstance(value["sha256"], str) or not HEX_SHA256.fullmatch(value["sha256"]):
        raise ConfigError(f"{label} digest is invalid")
    if type(value["size"]) is not int or value["size"] < 0:
        raise ConfigError(f"{label} size is invalid")


def _validate_bound_artifacts(directory: Path, document: Mapping[str, Any]) -> None:
    if document["status"] == "BLOCKED":
        return
    schema = document.get("schema")
    is_core_repo_transition = schema == PLAN_SCHEMA_CORE_REPOSITORY_TRANSITION_V1
    is_comp_repo_transition = schema == PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1
    is_repo_transition = is_core_repo_transition or is_comp_repo_transition
    if document["status"] == "READY_FOR_REVIEW":
        bindings = [
            document[field]
            for field in (
                "entry_lock",
                "candidate_lock",
                "lock_authorization_evidence",
                "qualifier_result",
            )
        ]
        if is_repo_transition:
            bindings.append(document["entry_declaration"])
            bindings.append(document["candidate_declaration"])
    else:
        bindings = [document[field] for field in ("selected_lock", "qualifier_result")]
    bindings.append(document["locked_qualification"]["qualifier"])
    allowed = {
        "plan.json",
        "plan.md",
        "result.json",
        "result.md",
    }
    if document["status"] == "READY_FOR_REVIEW":
        allowed.update({
            "retained-lock",
            "retained-lock/delta.json",
            "retained-lock/install-transaction.json",
        })
        if is_repo_transition:
            allowed.update({
                "retained-lock/flake.nix.preimage",
                "retained-lock/flake.nix.candidate",
            })
    allowed.update(binding["path"] for binding in bindings)
    for path in directory.rglob("*"):
        relative = path.relative_to(directory).as_posix()
        if relative not in allowed and not (
            relative.startswith("retained-lock/install-terminal-") and relative.endswith(".json")
        ):
            raise ConfigError("promotion plan directory contains an unbound artifact")
        metadata = path.lstat()
        if relative == "retained-lock":
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise ConfigError("promotion retained-lock artifact topology is invalid")
            require_private_directory(path, "promotion retained-lock directory")
        else:
            require_private_file(path, "promotion bound artifact")
    for binding in bindings:
        path = directory / binding["path"]
        try:
            path.resolve(strict=False).relative_to(directory.resolve(strict=True))
        except ValueError as error:
            raise ConfigError("promotion artifact escapes the reviewed plan directory") from error
        require_private_file(path, "promotion bound artifact")
        raw = path.read_bytes()
        if len(raw) != binding["size"] or sha256_bytes(raw) != binding["sha256"]:
            raise ConfigError("promotion bound artifact identity is invalid")
    if document["status"] == "READY_FOR_REVIEW":
        _validate_retained_lock_artifacts(directory, document)
    elif document["input"] == "core":
        binding = document["selected_lock"]
        if normalized_lock(directory / binding["path"]) != binding["normalized"]:
            raise ConfigError("promotion selected core graph drifted from retained bytes")
    for binding in (
        document["qualifier_result"], document["locked_qualification"]["qualifier"]
    ):
        raw = (directory / binding["path"]).read_bytes()
        try:
            native = strict_json_loads(raw.decode("utf-8"), "promotion native result artifact")
        except UnicodeDecodeError as error:
            raise ConfigError("promotion native result artifact is not UTF-8") from error
        if not isinstance(native, dict) or raw != canonical_bytes(native):
            raise ConfigError("promotion native result artifact bytes are not canonical")
        summary = validate_native_result(
            native,
            document["input"],
            document["candidate_ref"],
            document.get("candidate_contract", document["contract"]),
            allow_file_urls=document["local_fixture"],
            expected_identity=document["qualifier_result"]["summary"]["identity"],
        )
        if binding["summary"] != summary:
            raise ConfigError("promotion native result summary drifted from retained evidence")


def _validate_retained_lock_artifacts(
    directory: Path, document: Mapping[str, Any]
) -> None:
    for field in ("entry_lock", "candidate_lock"):
        binding = document[field]
        normalized = normalized_lock(directory / binding["path"])
        if normalized != binding["normalized"]:
            raise ConfigError(f"promotion {field} normalized graph drifted from retained bytes")

    binding = document["lock_authorization_evidence"]
    raw = (directory / binding["path"]).read_bytes()
    try:
        evidence = strict_json_loads(raw.decode("utf-8"), "promotion lock authorization evidence")
    except UnicodeDecodeError as error:
        raise ConfigError("promotion lock authorization evidence is not UTF-8") from error
    if not isinstance(evidence, dict) or raw != canonical_bytes(evidence):
        raise ConfigError("promotion lock authorization evidence bytes are not canonical")
    schema = document.get("schema")
    is_core_repo_transition = schema == PLAN_SCHEMA_CORE_REPOSITORY_TRANSITION_V1
    is_comp_repo_transition = schema == PLAN_SCHEMA_COMPOSITION_REPOSITORY_TRANSITION_V1
    is_repo_transition = is_core_repo_transition or is_comp_repo_transition
    if is_core_repo_transition:
        from .deployment_workflow import _migrate_core_declaration_bytes

        preimage = (directory / document["entry_declaration"]["path"]).read_bytes()
        candidate = (directory / document["candidate_declaration"]["path"]).read_bytes()
        if candidate != _migrate_core_declaration_bytes(preimage):
            raise ConfigError("promotion declaration is not the exact core repository transition")
    elif is_comp_repo_transition:
        from .deployment_workflow import _migrate_composition_declaration_bytes

        preimage = (directory / document["entry_declaration"]["path"]).read_bytes()
        candidate = (directory / document["candidate_declaration"]["path"]).read_bytes()
        if candidate != _migrate_composition_declaration_bytes(preimage):
            raise ConfigError("promotion declaration is not the exact composition repository transition")
    expected_keys = {
        "schema",
        "mode",
        "requested_inputs",
        "authorization",
        "changes",
        "source_authority",
        "preimage_sha256",
        "candidate_sha256",
    }
    if is_core_repo_transition:
        expected_keys.update({
            "core_repository_transition",
            "preimage_flake_nix_sha256",
            "candidate_flake_nix_sha256",
        })
    elif is_comp_repo_transition:
        expected_keys.update({
            "composition_repository_transition",
            "preimage_flake_nix_sha256",
            "candidate_flake_nix_sha256",
        })
    if set(evidence) != expected_keys:
        raise ConfigError("promotion lock authorization evidence fields are invalid")
    authorization = evidence.get("authorization")
    if (
        evidence.get("schema") != "nix-update-lock-delta-v2"
        or evidence.get("mode") != "targeted"
        or evidence.get("requested_inputs") != [document["input"]]
        or not isinstance(authorization, dict)
        or set(authorization) != {"kind", "record"}
        or authorization.get("kind") != authorization_kind(document["closure_authorization"])
        or authorization.get("record") != document["closure_authorization"]
        or evidence.get("changes") != document["lock_delta"]
        or evidence.get("source_authority") != (document.get("source_authority") or document.get("repository"))
        or evidence.get("preimage_sha256") != document["entry_lock"]["sha256"]
        or evidence.get("candidate_sha256") != document["candidate_lock"]["sha256"]
        or (is_core_repo_transition and evidence.get("core_repository_transition") is not True)
        or (is_comp_repo_transition and evidence.get("composition_repository_transition") is not True)
        or (is_repo_transition and evidence.get("preimage_flake_nix_sha256") != document["entry_declaration"]["sha256"])
        or (is_repo_transition and evidence.get("candidate_flake_nix_sha256") != document["candidate_declaration"]["sha256"])
    ):
        raise ConfigError("promotion retained lock authorization evidence drifted")


def _resolve_lock_input(lock: Mapping[str, Any], input_name: str) -> str:
    nodes = lock.get("nodes")
    root = lock.get("root")
    if not isinstance(nodes, dict) or not isinstance(root, str) or root not in nodes:
        raise ConfigError("promotion plan lock root cannot resolve its selected input")

    def resolve(
        current: str,
        segments: list[str],
        seen: set[tuple[str, tuple[str, ...]]],
        depth: int = 0,
    ) -> str:
        key = (current, tuple(segments))
        if depth > 32 or key in seen or not segments:
            raise ConfigError("promotion plan selected input path is cyclic or empty")
        seen.add(key)
        node = nodes.get(current)
        inputs = node.get("inputs") if isinstance(node, dict) else None
        reference = inputs.get(segments[0]) if isinstance(inputs, dict) else None
        remaining = segments[1:]
        if isinstance(reference, list) and all(isinstance(item, str) for item in reference):
            return resolve(root, [*reference, *remaining], seen, depth + 1)
        if not isinstance(reference, str) or reference not in nodes:
            raise ConfigError("promotion plan selected input path is unresolved")
        if not remaining:
            return reference
        return resolve(reference, remaining, seen, depth + 1)

    return resolve(root, input_name.split("/"), set(), 0)
