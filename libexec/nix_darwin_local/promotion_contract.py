from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from .deployment_plan import reject_secrets
from .paths import ConfigError


HEX_REVISION = re.compile(r"[0-9a-f]{40}")
SAFE_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SAFE_GIT_SSH_USERINFO = re.compile(
    r"(?i)\b((?:git\+)?ssh://)(?:git|\[REDACTED\])@"
)
SCP_GIT_URL = re.compile(
    r"git@(?P<host>[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?):"
    r"(?P<path>[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)+)"
)
LOCAL_ONLY_MODULE_URL = "local-only"
BASE_FIELDS = {
    "schema", "version", "composition", "system", "modules",
    "packageSetIdentity", "hosts", "checks", "qualification", "promotableInputs",
}
LAYERED_FIELDS = BASE_FIELDS | {
    "requiredLowerPromotion", "lowerPromotion", "combinedModules", "semantics",
}
BASE_V2_FIELDS = BASE_FIELDS | {"releaseChannels"}
LAYERED_V2_FIELDS = LAYERED_FIELDS | {"releaseChannels"}
RELEASE_CHANNEL_POLICY = "explicit-head-refs-v1"
MODULE_FIELDS = {
    "name", "input", "url", "checkout", "bootstrapStrategy",
    "ownsMachineLocalConfiguration",
}


def strict_json_loads(raw: str, label: str) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ConfigError(f"{label} contains a duplicate object key")
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=object_pairs)
    except json.JSONDecodeError as error:
        raise ConfigError(f"{label} did not return exactly one JSON value") from error


def reject_promotion_secrets(value: Any) -> None:
    def transformed(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: transformed(child) for key, child in item.items()}
        if isinstance(item, list):
            return [transformed(child) for child in item]
        if isinstance(item, str):
            return SAFE_GIT_SSH_USERINFO.sub(r"\1", item)
        return item

    reject_secrets(transformed(value))


def normalize_nix_repository_url(value: Any, *, allow_file: bool = False) -> str:
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise ConfigError("promotion canonical repository URL is invalid")
    parsed = urlsplit(value)
    if parsed.scheme == "git":
        raise ConfigError("anonymous git:// promotion repository URLs are unsupported")
    if parsed.scheme not in {"https", "ssh", "git+https", "git+ssh", "file"}:
        raise ConfigError("promotion canonical repository URL scheme is unsupported")
    if parsed.query or parsed.fragment or parsed.password is not None:
        raise ConfigError("promotion canonical repository URL contains a password, query, or fragment")
    if parsed.scheme == "file":
        if not allow_file:
            raise ConfigError("file promotion URLs are restricted to explicit local fixtures")
        if parsed.username is not None or parsed.netloc not in {"", "localhost"}:
            raise ConfigError("local fixture repository URL authority is invalid")
        _validate_url_path(parsed.path, absolute=True)
        return urlunsplit(("file", "", parsed.path.rstrip("/"), "", ""))
    username = parsed.username
    ssh_scheme = parsed.scheme in {"ssh", "git+ssh"}
    if username is not None and (not ssh_scheme or username != "git"):
        raise ConfigError("promotion repository URL permits only the git SSH username")
    if ssh_scheme and username != "git":
        raise ConfigError("promotion SSH repository URL must use the explicit git username")
    if parsed.hostname is None or "%" in parsed.netloc or "\\" in parsed.path:
        raise ConfigError("promotion canonical repository URL authority is invalid")
    _validate_url_path(parsed.path, absolute=True)
    try:
        port = parsed.port
    except ValueError as error:
        raise ConfigError("promotion canonical repository URL port is invalid") from error
    scheme = {"git+ssh": "ssh", "git+https": "https"}.get(parsed.scheme, parsed.scheme)
    if (scheme == "ssh" and port == 22) or (scheme == "https" and port == 443):
        port = None
    host = parsed.hostname.casefold()
    if ":" in host:
        host = f"[{host}]"
    authority = f"git@{host}" if scheme == "ssh" else host
    if port is not None:
        authority += f":{port}"
    return urlunsplit((scheme, authority, parsed.path.rstrip("/"), "", ""))


def normalize_git_transport_url(value: Any, *, allow_file: bool = False) -> str:
    if isinstance(value, str):
        match = SCP_GIT_URL.fullmatch(value)
        if match is not None:
            _validate_url_path(f"/{match.group('path')}", absolute=True)
            return f"ssh://git@{match.group('host').casefold()}/{match.group('path')}"
    return normalize_nix_repository_url(value, allow_file=allow_file)


def repository_urls_match(
    nix_url: str, git_url: str, *, allow_file: bool = False
) -> bool:
    return repository_identity_key(
        normalize_nix_repository_url(nix_url, allow_file=allow_file)
    ) == repository_identity_key(
        normalize_git_transport_url(git_url, allow_file=allow_file)
    )


def repository_identity_key(normalized_url: str) -> str:
    parsed = urlsplit(normalized_url)
    if parsed.scheme != "file" and parsed.path.endswith(".git"):
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path.removesuffix(".git"), "", "")
        )
    return normalized_url


def validate_contract(
    document: Any, *, allow_file_urls: bool = False, _depth: int = 0
) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ConfigError("promotion contract must be a JSON object")
    if _depth > 8:
        raise ConfigError("promotion contract lower-layer nesting is too deep")
    keys = set(document)
    if keys in (BASE_FIELDS, BASE_V2_FIELDS):
        dialect = "base"
    elif keys in (LAYERED_FIELDS, LAYERED_V2_FIELDS):
        dialect = "layered"
    else:
        raise ConfigError("promotion contract fields do not match a supported structural dialect")
    _safe_identity(document["schema"], "promotion contract schema")
    version = document["version"]
    if type(version) is not int or version not in {1, 2}:
        raise ConfigError("promotion contract version must be exactly 1 or 2")
    has_release_channels = "releaseChannels" in document
    if has_release_channels != (version == 2):
        raise ConfigError("promotion contract version and releaseChannels presence disagree")
    _validate_composition(document["composition"], allow_file_urls)
    _safe_identity(document["system"], "promotion contract system")
    module_sets = _validate_modules(document["modules"], allow_file_urls, "promotion modules")
    _validate_package_set(document["packageSetIdentity"])
    _validate_named_list(document["hosts"], "promotion contract hosts")
    _validate_named_list(document["checks"], "promotion contract checks")
    _validate_qualification(document["qualification"])
    _validate_promotable(document["promotableInputs"])
    if version == 2:
        _validate_release_channels(
            document["releaseChannels"], document["promotableInputs"]["names"]
        )
    if any(
        module["url"] == LOCAL_ONLY_MODULE_URL
        and module["input"] in document["promotableInputs"]["names"]
        for module in document["modules"]
    ):
        raise ConfigError("machine-local-only promotion modules cannot be promotable")
    if dialect == "base":
        _require_promotable_subset(document, module_sets["inputs"])
    else:
        _validate_layered(document, module_sets, allow_file_urls, _depth)
    reject_promotion_secrets(document)
    return document


def contract_dialect(contract: Mapping[str, Any]) -> str:
    return "layered" if set(contract) in (LAYERED_FIELDS, LAYERED_V2_FIELDS) else "base"


def validate_release_ref(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8")) > 1024
        or not value.startswith("refs/heads/")
    ):
        raise ConfigError("promotion release ref must be a normalized full branch ref")
    branch = value.removeprefix("refs/heads/")
    if (
        not branch
        or branch.startswith("/")
        or branch.endswith("/")
        or "//" in branch
        or ".." in branch
        or "@{" in branch
        or branch in {"HEAD", "@"}
        or any(character in branch for character in (" ", "~", "^", ":", "?", "*", "[", "\\", "+"))
        or any(ord(character) < 32 or ord(character) == 127 for character in branch)
    ):
        raise ConfigError("promotion release ref contains a prohibited branch form")
    components = branch.split("/")
    if any(
        not component
        or component in {"HEAD", "@"}
        or component.startswith(".")
        or component.endswith(".")
        or component.endswith(".lock")
        for component in components
    ):
        raise ConfigError("promotion release ref contains a prohibited branch component")
    return value


def contract_release_ref(contract: Mapping[str, Any], input_name: str) -> str:
    if input_name not in contract["promotableInputs"]["names"]:
        raise ConfigError("requested promotion input has no declared release channel")
    if contract["version"] == 1:
        return "refs/heads/main"
    return validate_release_ref(contract["releaseChannels"]["refs"][input_name])


def resolve_contract_input(contract: Mapping[str, Any], input_name: str) -> dict[str, str]:
    names = contract["promotableInputs"]["names"]
    if input_name not in names:
        raise ConfigError("requested promotion input is unknown, indirect, or not promotable")
    direct = [module for module in contract["modules"] if module["input"] == input_name]
    if len(direct) == 1:
        if direct[0]["url"] == LOCAL_ONLY_MODULE_URL:
            raise ConfigError("machine-local-only promotion modules cannot be resolved")
        return {
            "rule": "direct-module-url",
            "module": str(direct[0]["name"]),
            "repository_url": str(direct[0]["url"]),
        }
    if len(direct) > 1:
        raise ConfigError("requested promotion input repository URL is ambiguous")
    if contract_dialect(contract) == "layered":
        required = contract["requiredLowerPromotion"]
        if input_name == required["candidateInput"]:
            lower = contract["lowerPromotion"]
            return {
                "rule": "embedded-lower-composition-url",
                "module": str(lower["composition"]["name"]),
                "repository_url": str(lower["composition"]["repositoryUrl"]),
            }
    raise ConfigError("requested promotion input repository URL is missing or ambiguous")


def contract_repository_url(contract: Mapping[str, Any], input_name: str) -> str:
    return resolve_contract_input(contract, input_name)["repository_url"]


def candidate_reference(repository_url: str, revision: str, *, allow_file: bool = False) -> str:
    normalize_nix_repository_url(repository_url, allow_file=allow_file)
    if not HEX_REVISION.fullmatch(revision):
        raise ConfigError("promotion revision must be exactly 40 lowercase hexadecimal characters")
    return f"{repository_url}?rev={revision}"


def _validate_layered(
    document: Mapping[str, Any], local_sets: Mapping[str, set[str]], allow_file: bool, depth: int
) -> None:
    lower = validate_contract(document["lowerPromotion"], allow_file_urls=allow_file, _depth=depth + 1)
    required = document["requiredLowerPromotion"]
    _exact_keys(required, {"schema", "version", "candidateInput"}, "required lower promotion")
    if required["schema"] != lower["schema"] or required["version"] != lower["version"]:
        raise ConfigError("required lower promotion identity disagrees with the embedded contract")
    _safe_identity(required["candidateInput"], "required lower promotion candidate input")
    if required["candidateInput"] not in document["promotableInputs"]["names"]:
        raise ConfigError("required lower promotion candidate input is not promotable")
    allowed_promotable = local_sets["inputs"] | {required["candidateInput"]}
    _require_promotable_subset(document, allowed_promotable)
    expected_combined = list(lower["modules"]) + list(document["modules"])
    if document["combinedModules"] != expected_combined:
        raise ConfigError("layered promotion combinedModules disagrees with lower modules plus local modules")
    combined_sets = _validate_modules(document["combinedModules"], allow_file, "combined promotion modules")
    lower_modules = lower["combinedModules"] if contract_dialect(lower) == "layered" else lower["modules"]
    lower_sets = _module_identity_sets(lower_modules, allow_file)
    for family in ("names", "inputs", "checkouts", "urls"):
        if lower_sets[family] & local_sets[family]:
            raise ConfigError(f"layered promotion lower and local module {family} are not disjoint")
        if combined_sets[family] != _module_identity_sets(expected_combined, allow_file)[family]:
            raise ConfigError("layered promotion combined module identity is inconsistent")
    for field in ("system", "hosts", "packageSetIdentity"):
        if document[field] != lower[field]:
            raise ConfigError(f"layered promotion {field} disagrees with the lower contract")
    semantics = document["semantics"]
    _exact_keys(
        semantics, {"nonActivation", "requiresExplicitLockSelection"}, "layered promotion semantics"
    )
    if semantics["nonActivation"] is not True or semantics["requiresExplicitLockSelection"] is not True:
        raise ConfigError("layered promotion must be non-activating and require explicit lock selection")


def _validate_modules(modules: Any, allow_file: bool, label: str) -> dict[str, set[str]]:
    if not isinstance(modules, list) or not modules:
        raise ConfigError(f"{label} must be a non-empty ordered list")
    for index, record in enumerate(modules):
        if not isinstance(record, dict):
            raise ConfigError(f"{label} record {index} must be an object")
        _exact_keys(record, MODULE_FIELDS, f"{label} record {index}")
        for field in ("name", "input", "checkout", "bootstrapStrategy"):
            _safe_identity(record[field], f"{label} record {index} {field}")
        if type(record["ownsMachineLocalConfiguration"]) is not bool:
            raise ConfigError(f"{label} record {index} ownership flag must be Boolean")
        _module_url_identity(record, allow_file)
    result = _module_identity_sets(modules, allow_file)
    if any(len(result[family]) != len(modules) for family in ("names", "inputs", "checkouts")):
        raise ConfigError(f"{label} contains a duplicate module identity")
    remote_module_count = sum(
        module["url"] != LOCAL_ONLY_MODULE_URL for module in modules
    )
    if len(result["urls"]) != remote_module_count:
        raise ConfigError(f"{label} contains a duplicate module identity")
    return result


def _module_identity_sets(modules: Any, allow_file: bool) -> dict[str, set[str]]:
    urls: set[str] = set()
    for module in modules:
        identity = _module_url_identity(module, allow_file)
        if identity != LOCAL_ONLY_MODULE_URL:
            urls.add(identity)
    return {
        "names": {str(module["name"]) for module in modules},
        "inputs": {str(module["input"]) for module in modules},
        "checkouts": {str(module["checkout"]) for module in modules},
        "urls": urls,
    }


def _module_url_identity(module: Mapping[str, Any], allow_file: bool) -> str:
    if module["url"] == LOCAL_ONLY_MODULE_URL:
        if module["ownsMachineLocalConfiguration"] is not True:
            raise ConfigError("local-only module URL requires machine-local ownership")
        return LOCAL_ONLY_MODULE_URL
    return normalize_nix_repository_url(module["url"], allow_file=allow_file)


def _validate_composition(value: Any, allow_file: bool) -> None:
    if not isinstance(value, dict):
        raise ConfigError("promotion contract composition must be an object")
    _exact_keys(value, {"name", "repositoryUrl"}, "promotion contract composition")
    _safe_identity(value["name"], "promotion contract composition name")
    normalize_nix_repository_url(value["repositoryUrl"], allow_file=allow_file)


def _validate_package_set(value: Any) -> None:
    if not isinstance(value, str) or not value.startswith("/nix/store/") or any(
        char.isspace() for char in value
    ):
        raise ConfigError("promotion package-set identity is invalid")


def _validate_qualification(value: Any) -> None:
    if not isinstance(value, dict):
        raise ConfigError("promotion contract qualification must be an object")
    _exact_keys(value, {"entryPoint", "mode"}, "promotion qualification")
    entry = value["entryPoint"]
    if not isinstance(entry, str) or not _safe_relative_path(entry):
        raise ConfigError("promotion qualifier entryPoint must be a stable repository-relative path")
    if value["mode"] != "exact-candidate":
        raise ConfigError("promotion qualifier mode is unsupported")


def _validate_promotable(value: Any) -> None:
    if not isinstance(value, dict):
        raise ConfigError("promotion contract promotableInputs must be an object")
    _exact_keys(value, {"rule", "names"}, "promotion promotableInputs")
    _safe_identity(value["rule"], "promotion promotable-input rule")
    _validate_named_list(value["names"], "promotion contract promotable input names")


def _validate_release_channels(value: Any, promotable_names: list[str]) -> None:
    if not isinstance(value, dict):
        raise ConfigError("promotion releaseChannels must be an object")
    _exact_keys(value, {"policy", "refs"}, "promotion releaseChannels")
    if value["policy"] != RELEASE_CHANNEL_POLICY:
        raise ConfigError("promotion release-channel policy is unsupported")
    refs = value["refs"]
    if not isinstance(refs, dict) or set(refs) != set(promotable_names):
        raise ConfigError("promotion release-channel refs must exactly match promotable inputs")
    for release_ref in refs.values():
        validate_release_ref(release_ref)


def _require_promotable_subset(document: Mapping[str, Any], allowed: set[str]) -> None:
    if any(name not in allowed for name in document["promotableInputs"]["names"]):
        raise ConfigError("promotion contract contains an unmatched promotable input")


def _validate_named_list(value: Any, label: str) -> None:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{label} must be a non-empty list")
    if any(not isinstance(item, str) or SAFE_IDENTITY.fullmatch(item) is None for item in value):
        raise ConfigError(f"{label} contains an invalid identity")
    if len(set(value)) != len(value):
        raise ConfigError(f"{label} contains a duplicate identity")


def _safe_identity(value: Any, label: str) -> None:
    if not isinstance(value, str) or SAFE_IDENTITY.fullmatch(value) is None:
        raise ConfigError(f"{label} is invalid")


def _safe_relative_path(value: str) -> bool:
    pure = PurePosixPath(value)
    return not pure.is_absolute() and value == pure.as_posix() and pure.parts and ".." not in pure.parts


def _validate_url_path(path: str, *, absolute: bool) -> None:
    if not path or (absolute and not path.startswith("/")) or path == "/":
        raise ConfigError("promotion canonical repository URL path is incomplete")
    parts = path.split("/")[1:] if absolute else path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ConfigError("promotion canonical repository URL path is ambiguous")


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ConfigError(f"{label} fields are invalid")
