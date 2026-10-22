# nix-darwin-test-source-target: delivery/browser-ownership-r2b/host-qualification/validate.py
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import runpy
import sys
from typing import Any

import pytest


def load_canonical_contract(repo_root: Path) -> dict[str, Any]:
    contract_path = repo_root / "config/browser/core-ownership.json"
    with contract_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_canonical_contract_sha(repo_root: Path) -> str:
    contract_path = repo_root / "config/browser/core-ownership.json"
    return hashlib.sha256(contract_path.read_bytes()).hexdigest()


def load_evidence_template(repo_root: Path) -> dict[str, Any]:
    template_path = repo_root / "delivery/browser-ownership-r2b/host-qualification/evidence-template.json"
    with template_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def make_valid_attended_evidence(contract: dict[str, Any], contract_sha: str) -> dict[str, Any]:
    policy_names = list(contract.get("chrome", {}).get("policies", {}).keys())
    policy_statuses = {
        p: {
            "status": "OK",
            "source": "platform",
            "scope": "machine",
            "level": "mandatory",
            "conflicts": [],
            "readable": True,
            "method": "chrome://policy",
        }
        for p in policy_names
    }
    return {
        "schema": "browser-host-qualification-evidence-v1",
        "contract_schema": contract.get("schema", "core-browser-ownership-v1"),
        "contract_sha256": contract_sha,
        "evidence_class": "attended-host",
        "overall_status": "ok",
        "qualification_target": "attended-host-check-readback",
        "operator_approval_gates": {
            "chrome_extension_policy_transition": {
                "changed": True,
                "approved": True,
                "operator": "operator-1",
                "timestamp": "2026-10-11T19:30:00Z",
            },
            "chrome_metrics_ui": {
                "changed": True,
                "approved": True,
                "description": "Operator approval to disable metrics reporting in Chrome UI",
                "operator": "operator-1",
                "timestamp": "2026-10-11T20:00:00Z",
            },
            "chrome_ad_privacy_ui": {
                "changed": True,
                "approved": True,
                "description": "Operator approval to disable Privacy Sandbox ad settings in Chrome UI if present",
                "operator": "operator-1",
                "timestamp": "2026-10-11T20:01:00Z",
            },
            "chrome_ubol_site_permissions": {
                "changed": True,
                "approved": True,
                "description": "Operator approval for uBlock Origin Lite optimal filtering and broad site permissions",
                "operator": "operator-1",
                "timestamp": "2026-10-11T20:02:00Z",
            },
            "safari_search_engine": {
                "changed": True,
                "approved": True,
                "description": "Operator approval for Safari normal and private search engine selection to DuckDuckGo",
                "operator": "operator-1",
                "timestamp": "2026-10-11T20:03:00Z",
            },
            "safari_privacy_protections": {
                "changed": True,
                "approved": True,
                "description": "Operator approval for Safari tracking, hide IP, and fraudulent warning protections",
                "operator": "operator-1",
                "timestamp": "2026-10-11T20:04:00Z",
            },
            "safari_wipr_extensions": {
                "changed": True,
                "approved": True,
                "description": "Operator approval to enable Wipr 2 content blocker extensions in Safari UI",
                "operator": "operator-1",
                "timestamp": "2026-10-11T20:05:00Z",
            },
        },
        "before_capture": {
            "capture_phase": "before",
            "read_only": True,
            "method": "attended-operator-inspection",
            "status": "ok",
            "timestamp": "2026-10-11T19:00:00Z",
            "chrome": {
                "policies_readback_status": "ok",
                "installed_extensions_readback_status": "ok",
                "method": "chrome://policy+chrome://extensions",
                "forced_extension_ids": ["felphkbfjadmcejnibcmcncimlappdde"],
                "installed_extension_ids": ["felphkbfjadmcejnibcmcncimlappdde", "oboonakemofpalcgghocfoadofidjkkk"],
                "legacy_keepassxc_root_present": True,
                "ui_settings": {
                    "metrics": {"ui_disabled": True, "readback_verified": True},
                    "ad_privacy": {"available": True, "ui_disabled": True, "readback_verified": True},
                    "ubol": {"installed": True, "site_permission_granted": True,
                             "filtering_mode": "optimal", "rulesets": "built-in-defaults", "readback_verified": True},
                },
            },
            "safari": {
                "bundle_id": "com.apple.Safari",
                "search_engine": "DuckDuckGo",
                "private_search_engine": "DuckDuckGo",
                "protections": {
                    "prevent_cross_site_tracking": True,
                    "hide_ip_address": "trackers",
                    "fraudulent_website_warning": True,
                },
                "wipr": {
                    "enabled": True,
                    "extensions": {
                        "Blocklist 1": {"enabled": True},
                        "Blocklist 2": {"enabled": True},
                        "Blocklist 3": {"enabled": True},
                        "Blocklist 4": {"enabled": True},
                        "Wipr Extra": {"enabled": True, "site_permission_granted": True},
                    },
                },
            },
        },
        "after_capture": {
            "capture_phase": "after",
            "read_only": True,
            "method": "attended-operator-inspection",
            "status": "ok",
            "timestamp": "2026-10-11T20:30:00Z",
            "chrome": {
                "bundle_id": "com.google.Chrome",
                "installed_version": "130.0.6723.58",
                "extension_transition": {
                    "installed_extension_ids": ["felphkbfjadmcejnibcmcncimlappdde", "oboonakemofpalcgghocfoadofidjkkk", "ddkjiahejlhfcafbddmgiahcphecmpfh"],
                    "extension_storage_preserved": True,
                    "retained_keepassxc_browser_functional": True,
                },
                "policies": {
                    "readback_status": "ok",
                    "UrlKeyedAnonymizedDataCollectionEnabled": False,
                    "SearchSuggestEnabled": False,
                    "NetworkPredictionOptions": 2,
                    "SafeBrowsingProtectionLevel": 1,
                    "HttpsOnlyMode": "force_balanced_enabled",
                    "ExtensionSettings": {
                        "*": {"installation_mode": "allowed"},
                        "ddkjiahejlhfcafbddmgiahcphecmpfh": {
                            "installation_mode": "force_installed",
                            "update_url": "https://clients2.google.com/service/update2/crx",
                        },
                    },
                    "policy_statuses": policy_statuses,
                },
                "ubol": {
                    "extension_id": "ddkjiahejlhfcafbddmgiahcphecmpfh",
                    "extension_name": "uBlock Origin Lite",
                    "installed": True,
                    "enabled": True,
                    "filtering_mode": "optimal",
                "rulesets": "built-in-defaults",
                    "site_permission_granted": True,
                    "filtering_readback_verified": True,
                },
                "metrics": {"ui_disabled": True, "readback_verified": True},
                "ad_privacy": {"available": True, "ui_disabled": True, "readback_verified": True},
            },
            "framework_chromium_clean_baseline": {
                "binary_identity_verified": True,
                "disposable_context_isolated": True,
                "playwright_verified": True,
                "representative_suite_smoke": True,
                "managed_policies_absent": True,
                "forced_extensions_absent": True,
            },
            "normal_chrome_lane": {
                "playwright_mcp": {"codex": True, "claude": "not-configured", "antigravity": True},
                "bundle_id": "com.google.Chrome",
                "binary_identity_verified": True,
                "device_policy_inherited": True,
                "disposable_context_isolated": True,
                "permitted_loopback_navigation": True,
                "new_tab_inspect_evaluate": True,
                "cdp_websocket_download": True,
                "devtools_availability": True,
                "playwright_verified": True,
                "representative_suite_smoke": True,
                "forced_extension_present_in_disposable_profiles": True,
                "chrome_devtools_mcp": {"codex": True, "claude": "not-configured", "antigravity": True},
            },
            "safari": {
                "bundle_id": "com.apple.Safari",
                "search_engine": "DuckDuckGo",
                "private_search_engine": "DuckDuckGo",
                "private_search_availability_documented": True,
                "direct_navigation_ddg_verified": True,
                "protections": {
                    "prevent_cross_site_tracking": True,
                    "hide_ip_address": "trackers",
                    "fraudulent_website_warning": True,
                    "preserve_other_enabled_protections": True,
                    "all_three_protections_verified": True,
                },
                "wipr": {
                    "bundle_id": "site.kaylees.Wipr2",
                    "app_store_id": "1662217862",
                    "app_metadata_identity_verified": True,
                    "extensions": {
                        "Blocklist 1": {"enabled": True, "status": "ok"},
                        "Blocklist 2": {"enabled": True, "status": "ok"},
                        "Blocklist 3": {"enabled": True, "status": "ok"},
                        "Blocklist 4": {"enabled": True, "status": "ok"},
                        "Wipr Extra": {"enabled": True, "status": "ok", "site_permission_granted": True},
                    },
                },
                "personal_data_and_bookmarks_preserved": True,
            },
            "stock": {
                "brave": {
                    "bundle_id": "com.brave.Browser",
                    "managed_policies_absent": True,
                    "single_brave_identity_verified": True,
                    "brave_youtube_bundle_id": "com.operator.brave-youtube",
                    "brave_youtube_startup_verified": True,
                    "brave_youtube_profile_preserved": True,
                    "startup_verified": True,
                },
                "librewolf": {
                    "bundle_id": "io.gitlab.librewolf-community",
                    "package": "librewolf",
                    "cask_readback_status": "ok",
                    "managed_policies_absent": True,
                    "startup_verified": True,
                },
            },
            "preserved_targets": {item: True for item in contract.get("preserve", [])},
        },
    }


def test_canonical_contract_parity(repo_root: Path):
    contract = load_canonical_contract(repo_root)
    assert contract["schema"] == "core-browser-ownership-v1"
    assert contract["chrome"]["bundleId"] == "com.google.Chrome"
    assert contract["chrome"]["minimumMajor"] == 129
    assert contract["safari"]["searchEngine"] == "DuckDuckGo"
    assert contract["safari"]["wipr"]["appStoreId"] == "1662217862"



def test_evidence_template_integrity_never_pass(repo_root: Path, load_module):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    template_path = repo_root / "delivery/browser-ownership-r2b/host-qualification/evidence-template.json"
    raw_template_text = template_path.read_text(encoding="utf-8")
    assert "PASS" not in raw_template_text
    contract_path = repo_root / "config/browser/core-ownership.json"
    success, errors = validator.run_validation(contract_path, template_path)
    assert success is False
    assert len(errors) > 10
    assert any("evidence.evidence_class" in err for err in errors)
    assert any("evidence.overall_status" in err for err in errors)


def test_valid_attended_host_evidence_succeeds(repo_root: Path, load_module):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)
    valid_evidence = make_valid_attended_evidence(contract, sha)
    errors = validator.validate_contract_and_evidence(contract, valid_evidence, contract_sha=sha)
    assert errors == []


def test_contract_sha256_binding_and_tamper_detection(repo_root: Path, load_module):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)

    # Missing contract_sha256
    ev = make_valid_attended_evidence(contract, sha)
    del ev["contract_sha256"]
    errors = validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)
    assert any("contract_sha256" in err for err in errors)

    # Tampered SHA
    ev2 = make_valid_attended_evidence(contract, sha)
    ev2["contract_sha256"] = "0" * 64
    errors2 = validator.validate_contract_and_evidence(contract, ev2, contract_sha=sha)
    assert any("contract_sha256" in err for err in errors2)


@pytest.mark.parametrize("defect", ["missing_before", "unreadable_before", "unapproved", "hidden_transition", "extension_removed", "storage_lost", "keepass_broken", "invalid_ids", "cask_unavailable"])
def test_chrome_transition_and_cask_fail_closed(repo_root: Path, load_module, defect):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)
    ev = make_valid_attended_evidence(contract, sha)
    gate = ev["operator_approval_gates"]["chrome_extension_policy_transition"]
    transition = ev["after_capture"]["chrome"]["extension_transition"]
    if defect == "missing_before":
        del ev["before_capture"]["chrome"]
    elif defect == "unreadable_before":
        ev["before_capture"]["chrome"]["policies_readback_status"] = "unreadable"
    elif defect == "unapproved":
        gate["approved"] = False
    elif defect == "hidden_transition":
        gate.update(changed=False, approved=False)
    elif defect == "extension_removed":
        transition["installed_extension_ids"].remove("felphkbfjadmcejnibcmcncimlappdde")
    elif defect == "storage_lost":
        transition["extension_storage_preserved"] = False
    elif defect == "keepass_broken":
        transition["retained_keepassxc_browser_functional"] = False
    elif defect == "invalid_ids":
        ev["before_capture"]["chrome"]["forced_extension_ids"] = ["invalid"]
    else:
        ev["after_capture"]["stock"]["librewolf"]["cask_readback_status"] = "unavailable"
    assert validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)


def test_no_legacy_transition_requires_no_new_approval(repo_root: Path, load_module):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)
    ev = make_valid_attended_evidence(contract, sha)
    ev["before_capture"]["chrome"].update(forced_extension_ids=[contract["chrome"]["requiredExtension"]["id"]], legacy_keepassxc_root_present=False)
    ev["operator_approval_gates"]["chrome_extension_policy_transition"].update(changed=False, approved=False)
    assert validator.validate_contract_and_evidence(contract, ev, contract_sha=sha) == []


def test_operator_approval_gates_conditional(repo_root: Path, load_module):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)

    # 1. Gate with changed: False permits observed-existing WITHOUT new approval
    ev = make_valid_attended_evidence(contract, sha)
    ev["operator_approval_gates"]["chrome_metrics_ui"]["changed"] = False
    ev["operator_approval_gates"]["chrome_metrics_ui"]["approved"] = False
    errors = validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)
    assert not any("chrome_metrics_ui" in err for err in errors)

    # 2. Gate with changed: True requires approved: True
    ev2 = make_valid_attended_evidence(contract, sha)
    ev2["operator_approval_gates"]["chrome_metrics_ui"]["changed"] = True
    ev2["operator_approval_gates"]["chrome_metrics_ui"]["approved"] = False
    errors2 = validator.validate_contract_and_evidence(contract, ev2, contract_sha=sha)
    assert any("operator_approval_gates.chrome_metrics_ui.approved" in err for err in errors2)

    # 3. Gate with changed: True requires before < approval < after timestamps
    ev3 = make_valid_attended_evidence(contract, sha)
    # Approval timestamp before before_capture timestamp
    ev3["operator_approval_gates"]["chrome_metrics_ui"]["timestamp"] = "2026-10-11T18:00:00Z"
    errors3 = validator.validate_contract_and_evidence(contract, ev3, contract_sha=sha)
    assert any("operator_approval_gates.chrome_metrics_ui.timestamp" in err for err in errors3)


@pytest.mark.parametrize("setting,field,old,gate", [
    ("metrics", "ui_disabled", False, "chrome_metrics_ui"),
    ("ad_privacy", "ui_disabled", False, "chrome_ad_privacy_ui"),
    ("ubol", "installed", False, "chrome_ubol_site_permissions"),
    ("ubol", "site_permission_granted", False, "chrome_ubol_site_permissions"),
    ("ubol", "filtering_mode", "basic", "chrome_ubol_site_permissions"),
    ("ubol", "rulesets", "custom", "chrome_ubol_site_permissions"),
])
def test_changed_chrome_ui_requires_declared_approval(repo_root, load_module, setting, field, old, gate):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)
    ev = make_valid_attended_evidence(contract, sha)
    ev["before_capture"]["chrome"]["ui_settings"][setting][field] = old
    ev["operator_approval_gates"][gate].update(changed=False, approved=False)
    assert f"operator_approval_gates.{gate}.changed" in validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)
    ev["operator_approval_gates"][gate].update(changed=True, approved=True)
    assert validator.validate_contract_and_evidence(contract, ev, contract_sha=sha) == []


@pytest.mark.parametrize("defect", ["missing", "unreadable", "boolean_integer", "pending_mode", "unknown_group"])
def test_before_chrome_ui_capture_cannot_be_invented_or_omitted(repo_root, load_module, defect):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)
    ev = make_valid_attended_evidence(contract, sha)
    ui = ev["before_capture"]["chrome"]["ui_settings"]
    if defect == "missing":
        del ev["before_capture"]["chrome"]["ui_settings"]
    elif defect == "unreadable":
        ui["metrics"]["readback_verified"] = False
    elif defect == "boolean_integer":
        ui["ad_privacy"]["available"] = 1
    elif defect == "pending_mode":
        ui["ubol"]["filtering_mode"] = "pending"
    else:
        ui["personal_data"] = {}
    assert validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)


def test_unchanged_chrome_ui_requires_no_reapproval(repo_root, load_module):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)
    ev = make_valid_attended_evidence(contract, sha)
    for gate in ("chrome_metrics_ui", "chrome_ad_privacy_ui", "chrome_ubol_site_permissions"):
        ev["operator_approval_gates"][gate].update(changed=False, approved=False)
    assert validator.validate_contract_and_evidence(contract, ev, contract_sha=sha) == []


@pytest.mark.parametrize("available", [None, 1, "pending"])
def test_ad_privacy_availability_requires_observation(repo_root, load_module, available):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)
    ev = make_valid_attended_evidence(contract, sha)
    ev["after_capture"]["chrome"]["ad_privacy"]["available"] = available
    assert validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)


def test_strict_type_comparison_int_vs_bool(repo_root: Path, load_module):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)

    # Integer 0 instead of Boolean False
    ev = make_valid_attended_evidence(contract, sha)
    ev["after_capture"]["chrome"]["policies"]["UrlKeyedAnonymizedDataCollectionEnabled"] = 0
    errors = validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)
    assert any("UrlKeyedAnonymizedDataCollectionEnabled" in err for err in errors)

    # Boolean True instead of Integer 2
    ev2 = make_valid_attended_evidence(contract, sha)
    ev2["after_capture"]["chrome"]["policies"]["NetworkPredictionOptions"] = True
    errors2 = validator.validate_contract_and_evidence(contract, ev2, contract_sha=sha)
    assert any("NetworkPredictionOptions" in err for err in errors2)

    # Boolean True instead of Integer 1
    ev3 = make_valid_attended_evidence(contract, sha)
    ev3["after_capture"]["chrome"]["policies"]["SafeBrowsingProtectionLevel"] = True
    errors3 = validator.validate_contract_and_evidence(contract, ev3, contract_sha=sha)
    assert any("SafeBrowsingProtectionLevel" in err for err in errors3)


def test_chrome_installed_version_minimum_major(repo_root: Path, load_module):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)

    # Normal Chrome installed version < 129
    ev = make_valid_attended_evidence(contract, sha)
    ev["after_capture"]["chrome"]["installed_version"] = "128.0.6613.119"
    errors = validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)
    assert any("after_capture.chrome.installed_version" in err for err in errors)

    # Version >= 129 succeeds
    ev2 = make_valid_attended_evidence(contract, sha)
    ev2["after_capture"]["chrome"]["installed_version"] = "129.0.6668.70"
    errors2 = validator.validate_contract_and_evidence(contract, ev2, contract_sha=sha)
    assert not any("after_capture.chrome.installed_version" in err for err in errors2)


def test_per_policy_policy_statuses_strict(repo_root: Path, load_module):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)

    # Status summary OK but per-policy status 'ignored' must fail
    ev = make_valid_attended_evidence(contract, sha)
    ev["after_capture"]["chrome"]["policies"]["policy_statuses"]["SearchSuggestEnabled"]["status"] = "ignored"
    errors = validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)
    assert any("after_capture.chrome.policies.policy_statuses.SearchSuggestEnabled" in err for err in errors)

    # Status error or unsupported
    ev2 = make_valid_attended_evidence(contract, sha)
    ev2["after_capture"]["chrome"]["policies"]["policy_statuses"]["HttpsOnlyMode"]["status"] = "unsupported"
    errors2 = validator.validate_contract_and_evidence(contract, ev2, contract_sha=sha)
    assert any("after_capture.chrome.policies.policy_statuses.HttpsOnlyMode" in err for err in errors2)

    # Missing per-policy entry
    ev3 = make_valid_attended_evidence(contract, sha)
    del ev3["after_capture"]["chrome"]["policies"]["policy_statuses"]["NetworkPredictionOptions"]
    errors3 = validator.validate_contract_and_evidence(contract, ev3, contract_sha=sha)
    assert any("after_capture.chrome.policies.policy_statuses.NetworkPredictionOptions" in err for err in errors3)


def test_chrome_ad_privacy_absent_on_chrome_150(repo_root: Path, load_module):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)

    # Chrome 150 with absent AdPrivacy setting
    ev = make_valid_attended_evidence(contract, sha)
    ev["after_capture"]["chrome"]["installed_version"] = "150.0.7000.0"
    ev["after_capture"]["chrome"]["ad_privacy"] = {
        "available": False,
        "method": "observed-absent-chrome150",
        "readback_verified": True,
    }
    # Approval gate can have changed: False
    ev["operator_approval_gates"]["chrome_ad_privacy_ui"]["changed"] = False
    errors = validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)
    assert not any("ad_privacy" in err for err in errors)


def test_safari_before_capture_snapshot_and_allowed_keys(repo_root: Path, load_module):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)

    # Safari before snapshot missing
    ev = make_valid_attended_evidence(contract, sha)
    del ev["before_capture"]["safari"]
    errors = validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)
    assert any("before_capture.safari" in err for err in errors)

    # Extra personal keys (e.g. bookmarks or history) forbidden
    ev2 = make_valid_attended_evidence(contract, sha)
    ev2["before_capture"]["safari"]["bookmarks"] = ["https://personal.com"]
    errors2 = validator.validate_contract_and_evidence(contract, ev2, contract_sha=sha)
    assert any("before_capture.safari" in err for err in errors2)


def test_safari_wipr_extra_permissions_and_no_deletion(repo_root: Path, load_module):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)

    # Wipr Extra site_permission_granted False
    ev = make_valid_attended_evidence(contract, sha)
    ev["after_capture"]["safari"]["wipr"]["extensions"]["Wipr Extra"]["site_permission_granted"] = False
    errors = validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)
    assert any("Wipr Extra.site_permission_granted" in err for err in errors)

    # Extension deleted between before and after
    ev2 = make_valid_attended_evidence(contract, sha)
    ev2["before_capture"]["safari"]["wipr"]["extensions"]["CustomExt"] = {"enabled": True}
    errors2 = validator.validate_contract_and_evidence(contract, ev2, contract_sha=sha)
    assert any("after_capture.safari.wipr.extensions" in err for err in errors2)


def test_safari_private_search_and_hide_ip(repo_root: Path, load_module):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)

    # Private search same-as-normal without documentation / unavailable flag -> fails
    ev = make_valid_attended_evidence(contract, sha)
    ev["after_capture"]["safari"]["private_search_engine"] = "same-as-normal"
    errors = validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)
    assert any("after_capture.safari.private_search_engine" in err for err in errors)

    # Private search same-as-normal with documented unavailable option -> succeeds
    ev2 = make_valid_attended_evidence(contract, sha)
    ev2["after_capture"]["safari"]["private_search_engine"] = "same-as-normal"
    ev2["after_capture"]["safari"]["private_search_available"] = False
    ev2["after_capture"]["safari"]["private_search_availability_documented"] = True
    errors2 = validator.validate_contract_and_evidence(contract, ev2, contract_sha=sha)
    assert not any("after_capture.safari.private_search_engine" in err for err in errors2)

    # Stronger HideIP trackers-and-websites -> succeeds
    ev3 = make_valid_attended_evidence(contract, sha)
    ev3["after_capture"]["safari"]["protections"]["hide_ip_address"] = "trackers-and-websites"
    errors3 = validator.validate_contract_and_evidence(contract, ev3, contract_sha=sha)
    assert not any("hide_ip_address" in err for err in errors3)


def test_stock_browsers_startup_verified_and_brave_youtube(repo_root: Path, load_module):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)

    # Brave startup_verified False
    ev = make_valid_attended_evidence(contract, sha)
    ev["after_capture"]["stock"]["brave"]["startup_verified"] = False
    errors = validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)
    assert any("after_capture.stock.brave.startup_verified" in err for err in errors)

    # LibreWolf startup_verified False
    ev2 = make_valid_attended_evidence(contract, sha)
    ev2["after_capture"]["stock"]["librewolf"]["startup_verified"] = False
    errors2 = validator.validate_contract_and_evidence(contract, ev2, contract_sha=sha)
    assert any("after_capture.stock.librewolf.startup_verified" in err for err in errors2)

    # Brave YouTube profile not preserved
    ev3 = make_valid_attended_evidence(contract, sha)
    ev3["after_capture"]["stock"]["brave"]["brave_youtube_profile_preserved"] = False
    errors3 = validator.validate_contract_and_evidence(contract, ev3, contract_sha=sha)
    assert any("brave_youtube_profile_preserved" in err for err in errors3)


def test_captures_read_only_and_chronologically_ordered(repo_root: Path, load_module):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)

    # Before capture not read_only
    ev = make_valid_attended_evidence(contract, sha)
    ev["before_capture"]["read_only"] = False
    errors = validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)
    assert any("before_capture.read_only" in err for err in errors)

    # After capture not read_only
    ev2 = make_valid_attended_evidence(contract, sha)
    ev2["after_capture"]["read_only"] = False
    errors2 = validator.validate_contract_and_evidence(contract, ev2, contract_sha=sha)
    assert any("after_capture.read_only" in err for err in errors2)

    # After capture timestamp earlier than before capture timestamp
    ev3 = make_valid_attended_evidence(contract, sha)
    ev3["after_capture"]["timestamp"] = "2026-10-11T18:00:00Z"
    errors3 = validator.validate_contract_and_evidence(contract, ev3, contract_sha=sha)
    assert any("after_capture.timestamp" in err for err in errors3)


def test_output_sanitization_no_secrets_urls_or_private_paths(load_module):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    raw_text = (
        "Error connecting to https://clients2.google.com/service/update2/crx "
        "at /Users/operator-fixture/.local/nix-darwin/file.json with token=secr3t_token_val_12345"
    )
    sanitized = validator.sanitize_text(raw_text)
    assert "https://" not in sanitized
    assert "<redacted-url>" in sanitized
    assert "/Users/operator-fixture" not in sanitized
    assert "<redacted-path>" in sanitized
    assert "secr3t_token" not in sanitized
    assert "<redacted-secret>" in sanitized


def test_cli_main_exit_codes(repo_root: Path, monkeypatch, capsys, tmp_path: Path):
    contract_path = repo_root / "config/browser/core-ownership.json"
    template_path = repo_root / "delivery/browser-ownership-r2b/host-qualification/evidence-template.json"
    validator_script = str(repo_root / "delivery/browser-ownership-r2b/host-qualification/validate.py")

    # 1. Run against evidence template -> exit 1
    monkeypatch.setattr(
        sys,
        "argv",
        ["validate.py", "--contract", str(contract_path), "--evidence", str(template_path)],
    )
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(validator_script, run_name="__main__")
    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert "VALIDATION STATUS: FAILED" in captured.out

    # 2. Run against valid evidence -> exit 0
    contract_data = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)
    valid_ev = make_valid_attended_evidence(contract_data, sha)
    valid_path = tmp_path / "valid-evidence.json"
    valid_path.write_text(json.dumps(valid_ev, indent=2), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["validate.py", "--contract", str(contract_path), "--evidence", str(valid_path)],
    )
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(validator_script, run_name="__main__")
    assert exit_info.value.code == 0
    captured = capsys.readouterr()
    assert "EVIDENCE_CHECKLIST_COMPLETE" in captured.out


def test_chromium_lanes_isolation_and_distinct_mcp(repo_root: Path, load_module):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)

    # 1. Normal Chrome lane bundle ID mismatch
    ev = make_valid_attended_evidence(contract, sha)
    ev["after_capture"]["normal_chrome_lane"]["bundle_id"] = "com.google.Chrome.canary"
    errors = validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)
    assert any("normal_chrome_lane.bundle_id" in err for err in errors)

    # 2. Normal Chrome lane missing chrome_devtools_mcp client
    ev2 = make_valid_attended_evidence(contract, sha)
    ev2["after_capture"]["normal_chrome_lane"]["chrome_devtools_mcp"]["antigravity"] = False
    errors2 = validator.validate_contract_and_evidence(contract, ev2, contract_sha=sha)
    assert any("chrome_devtools_mcp.antigravity" in err for err in errors2)

    # 3. Framework Chromium baseline leaking managed policies
    ev3 = make_valid_attended_evidence(contract, sha)
    ev3["after_capture"]["framework_chromium_clean_baseline"]["managed_policies_absent"] = False
    errors3 = validator.validate_contract_and_evidence(contract, ev3, contract_sha=sha)
    assert any("framework_chromium_clean_baseline.managed_policies_absent" in err for err in errors3)

    # 4. Framework clean baseline without MCP is permitted when normal lane has MCP
    ev4 = make_valid_attended_evidence(contract, sha)
    if "chrome_devtools_mcp" in ev4["after_capture"]["framework_chromium_clean_baseline"]:
        del ev4["after_capture"]["framework_chromium_clean_baseline"]["chrome_devtools_mcp"]
    errors4 = validator.validate_contract_and_evidence(contract, ev4, contract_sha=sha)
    assert errors4 == []



@pytest.mark.parametrize("field", [
    "chrome", "safari", "stock", "preserved_targets",
    "framework_chromium_clean_baseline", "normal_chrome_lane",
])
def test_empty_required_capture_cannot_pass(repo_root, load_module, field):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)
    ev = make_valid_attended_evidence(contract, sha)
    ev["after_capture"][field] = {}
    assert validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)


@pytest.mark.parametrize("mutation", ["missing_wipr", "disabled_wipr", "empty_gates", "custom_rules", "extra_forced_extension", "unversioned", "missing_playwright_mcp"])
def test_required_effective_evidence_negatives(repo_root, load_module, mutation):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)
    ev = make_valid_attended_evidence(contract, sha)
    after = ev["after_capture"]
    if mutation == "missing_wipr":
        del after["safari"]["wipr"]["extensions"]["Blocklist 4"]
    elif mutation == "disabled_wipr":
        after["safari"]["wipr"]["extensions"]["Wipr Extra"]["enabled"] = False
    elif mutation == "empty_gates":
        ev["operator_approval_gates"] = {}
    elif mutation == "custom_rules":
        after["chrome"]["ubol"]["rulesets"] = "custom-rules"
    elif mutation == "extra_forced_extension":
        after["chrome"]["policies"]["ExtensionSettings"]["a" * 32] = {"installation_mode": "force_installed"}
    elif mutation == "unversioned":
        after["chrome"]["installed_version"] = "149garbage"
    else:
        after["normal_chrome_lane"]["playwright_mcp"] = {}
    assert validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)


def test_readback_validator_preserves_dirty_non_target_files(repo_root, load_module, tmp_path):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    marker = tmp_path / "unrelated-dirty-source.txt"
    marker.write_bytes(b"operator working content\n")
    before = marker.read_bytes()
    ok, errors = validator.run_validation(
        repo_root / "config/browser/core-ownership.json",
        repo_root / "delivery/browser-ownership-r2b/host-qualification/evidence-template.json",
    )
    assert not ok and errors
    assert marker.read_bytes() == before


def test_observed_safari_change_cannot_skip_approval(repo_root, load_module):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)
    ev = make_valid_attended_evidence(contract, sha)
    ev["before_capture"]["safari"]["search_engine"] = "Google"
    ev["operator_approval_gates"]["safari_search_engine"]["changed"] = False
    assert "operator_approval_gates.safari_search_engine.changed" in validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)


def test_actual_absent_mcp_client_is_preserved(repo_root, load_module):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    sha = get_canonical_contract_sha(repo_root)
    ev = make_valid_attended_evidence(contract, sha)
    ev["after_capture"]["normal_chrome_lane"]["playwright_mcp"]["claude"] = True
    assert "after_capture.normal_chrome_lane.playwright_mcp.claude" in validator.validate_contract_and_evidence(contract, ev, contract_sha=sha)


def test_malformed_contract_returns_sanitized_shape_error(repo_root, load_module, tmp_path):
    validator = load_module("delivery/browser-ownership-r2b/host-qualification/validate.py")
    contract = load_canonical_contract(repo_root)
    del contract["chrome"]["mcpClients"]
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract))
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(make_valid_attended_evidence(contract, sha)))
    assert validator.run_validation(path, evidence) == (False, ["evidence_schema_shape"])
