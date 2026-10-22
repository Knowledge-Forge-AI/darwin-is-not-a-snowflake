#!/usr/bin/env python3
"""Host qualification kit validator for browser ownership."""
from __future__ import annotations
import argparse, hashlib, json, re, sys
from datetime import datetime
from pathlib import Path
from typing import Any

EXACT_WIPR = ("Blocklist 1", "Blocklist 2", "Blocklist 3", "Blocklist 4", "Wipr Extra")
REQUIRED_GATES = ("chrome_extension_policy_transition", "chrome_metrics_ui", "chrome_ad_privacy_ui", "chrome_ubol_site_permissions", "safari_search_engine", "safari_privacy_protections", "safari_wipr_extensions")
ALLOWED_BEFORE_SAFARI = {"bundle_id", "search_engine", "private_search_engine", "protections", "wipr"}

def sanitize_text(text: str) -> str:
    s = re.sub(r"/(?:Users|home)/[^\s'\"<>,:]+", "<redacted-path>", re.sub(r"https?://[^\s'\"<>]+", "<redacted-url>", str(text)))
    return re.sub(r"(?:token|secret|key|bearer|auth)[=:\s]+[A-Za-z0-9_\-\.]{8,}", "<redacted-secret>", s, flags=re.I)

def strict_eq(a: Any, b: Any) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return set(a) == set(b) and all(strict_eq(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(strict_eq(x, y) for x, y in zip(a, b))
    return a == b

def parse_iso(ts: Any) -> datetime | None:
    if not isinstance(ts, str) or ts == "pending":
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (TypeError, ValueError):
        return None

def check_bool(val: Any, exp: bool, f: str, errs: list[str]) -> None:
    if type(val) is not bool or val is not exp:
        errs.append(f)

def check_all_bools(d: dict, keys: tuple[str, ...], prefix: str, errs: list[str]) -> None:
    for k in keys:
        check_bool(d.get(k), True, f"{prefix}.{k}", errs)

def d_get(p: dict, k: str, f: str, errs: list[str]) -> dict | None:
    v = p.get(k)
    if not isinstance(v, dict):
        errs.append(f)
        return None
    return v

def check_meta_and_sha(c: dict, ev: dict, sha: str | None, errs: list[str]) -> None:
    for f, k, exp in (("contract.schema", c.get("schema"), "core-browser-ownership-v1"),
                      ("evidence.schema", ev.get("schema"), "browser-host-qualification-evidence-v1"),
                      ("evidence.contract_schema", ev.get("contract_schema"), "core-browser-ownership-v1"),
                      ("evidence.evidence_class", ev.get("evidence_class"), "attended-host"),
                      ("evidence.overall_status", ev.get("overall_status"), "ok"),
                      ("evidence.qualification_target", ev.get("qualification_target"), "attended-host-check-readback")):
        if k != exp:
            errs.append(f)
    exp_sha, ev_sha = sha or c.get("contract_sha256"), ev.get("contract_sha256")
    if not exp_sha or not ev_sha or ev_sha != exp_sha:
        errs.append("evidence.contract_sha256")

def check_captures_meta(b: dict, a: dict, errs: list[str]) -> tuple[datetime | None, datetime | None]:
    for p, d in (("before_capture", b), ("after_capture", a)):
        check_bool(d.get("read_only"), True, f"{p}.read_only", errs)
        if d.get("status") != "ok":
            errs.append(f"{p}.status")
        if not d.get("method") or d.get("method") == "pending":
            errs.append(f"{p}.method")
    tb, ta = parse_iso(b.get("timestamp")), parse_iso(a.get("timestamp"))
    if not tb:
        errs.append("before_capture.timestamp")
    if not ta or (tb and ta <= tb):
        errs.append("after_capture.timestamp")
    return tb, ta

def check_approval_gates(gates: dict, tb: datetime | None, ta: datetime | None, errs: list[str]) -> None:
    for gname in REQUIRED_GATES:
        g = gates.get(gname)
        if not isinstance(g, dict):
            errs.append(f"operator_approval_gates.{gname}")
        elif type(g.get("changed")) is not bool:
            errs.append(f"operator_approval_gates.{gname}.changed")
        elif g["changed"]:
            check_bool(g.get("approved"), True, f"operator_approval_gates.{gname}.approved", errs)
            if not g.get("operator") or g.get("operator") == "pending":
                errs.append(f"operator_approval_gates.{gname}.operator")
            tg = parse_iso(g.get("timestamp"))
            if not tg or not tb or not ta or not (tb < tg < ta):
                errs.append(f"operator_approval_gates.{gname}.timestamp")

def check_policy_statuses(pol_stats: Any, req_keys: list[str], errs: list[str]) -> None:
    if not isinstance(pol_stats, dict):
        errs.append("after_capture.chrome.policies.policy_statuses")
        return
    for k in req_keys:
        st = pol_stats.get(k)
        f = f"after_capture.chrome.policies.policy_statuses.{k}"
        if not isinstance(st, dict):
            errs.append(f)
            continue
        ok = (st.get("status") == "OK" and st.get("source") == "platform" and st.get("scope") == "machine"
              and st.get("level") == "mandatory" and isinstance(st.get("conflicts"), list)
              and len(st["conflicts"]) == 0 and st.get("readable") is True and st.get("method") == "chrome://policy")
        if not ok:
            errs.append(f)

def check_chrome_policies(pol: dict, cpol: dict, ubol_id: str, errs: list[str]) -> None:
    if pol.get("readback_status") != "ok":
        errs.append("after_capture.chrome.policies.readback_status")
    for p in ("UrlKeyedAnonymizedDataCollectionEnabled", "SearchSuggestEnabled", "NetworkPredictionOptions",
              "SafeBrowsingProtectionLevel", "HttpsOnlyMode"):
        if not strict_eq(pol.get(p), cpol.get(p)):
            errs.append(f"after_capture.chrome.policies.{p}")
    es = pol.get("ExtensionSettings")
    if not strict_eq(es, cpol.get("ExtensionSettings")):
        errs.append("after_capture.chrome.policies.ExtensionSettings")
    if set(pol) != set(cpol) | {"readback_status", "policy_statuses"}:
        errs.append("after_capture.chrome.policies.keys")
    if not isinstance(es, dict) or not isinstance(es.get("*"), dict) or es["*"].get("installation_mode") != "allowed":
        errs.append("after_capture.chrome.policies.ExtensionSettings.*")
    ub_es = es.get(ubol_id, {}) if isinstance(es, dict) else {}
    if not isinstance(ub_es, dict):
        errs.append("after_capture.chrome.policies.ExtensionSettings.required")
        return
    if ub_es.get("installation_mode") != "force_installed":
        errs.append(f"after_capture.chrome.policies.ExtensionSettings.{ubol_id}.installation_mode")
    if ub_es.get("update_url") != cpol.get("ExtensionSettings", {}).get(ubol_id, {}).get("update_url"):
        errs.append(f"after_capture.chrome.policies.ExtensionSettings.{ubol_id}.update_url")

def check_chrome_details(ch: dict, ub_req: dict, errs: list[str]) -> None:
    ub = d_get(ch, "ubol", "after_capture.chrome.ubol", errs)
    if ub is not None:
        if ub.get("extension_id") != ub_req.get("id"):
            errs.append("after_capture.chrome.ubol.extension_id")
        if ub.get("extension_name") != ub_req.get("name"):
            errs.append("after_capture.chrome.ubol.extension_name")
        if ub.get("filtering_mode") != ub_req.get("filteringMode"):
            errs.append("after_capture.chrome.ubol.filtering_mode")
        check_all_bools(ub, ("installed", "enabled", "site_permission_granted", "filtering_readback_verified"), "after_capture.chrome.ubol", errs)
        if ub.get("rulesets") != ub_req.get("rulesets"):
            errs.append("after_capture.chrome.ubol.rulesets")
    met = d_get(ch, "metrics", "after_capture.chrome.metrics", errs)
    if met is not None:
        check_all_bools(met, ("ui_disabled", "readback_verified"), "after_capture.chrome.metrics", errs)
    ad = d_get(ch, "ad_privacy", "after_capture.chrome.ad_privacy", errs)
    if ad is not None:
        if type(ad.get("available")) is not bool:
            errs.append("after_capture.chrome.ad_privacy.available")
        if ad.get("available") is False:
            version = re.fullmatch(r"(\d+)(?:\.\d+){1,3}", str(ch.get("installed_version", "")))
            if not version or int(version.group(1)) < 150:
                errs.append("after_capture.chrome.ad_privacy.available")
            if not ad.get("method") or ad.get("method") == "pending":
                errs.append("after_capture.chrome.ad_privacy.method")
            check_bool(ad.get("readback_verified"), True, "after_capture.chrome.ad_privacy.readback_verified", errs)
        else:
            check_all_bools(ad, ("ui_disabled", "readback_verified"), "after_capture.chrome.ad_privacy", errs)

def check_chrome(ch: dict, cch: dict, errs: list[str]) -> None:
    if ch.get("bundle_id") != cch.get("bundleId"):
        errs.append("after_capture.chrome.bundle_id")
    m = re.fullmatch(r"(\d+)(?:\.\d+){1,3}", str(ch.get("installed_version", "")))
    if not m or int(m.group(1)) < cch.get("minimumMajor", 129):
        errs.append("after_capture.chrome.installed_version")
    pol, cpol = d_get(ch, "policies", "after_capture.chrome.policies", errs), cch.get("policies", {})
    if pol is not None:
        check_chrome_policies(pol, cpol, cch.get("requiredExtension", {}).get("id", ""), errs)
        check_policy_statuses(pol.get("policy_statuses"), list(cpol.keys()), errs)
    check_chrome_details(ch, cch.get("requiredExtension", {}), errs)

def check_lanes(a: dict, cch: dict, errs: list[str]) -> None:
    fw = d_get(a, "framework_chromium_clean_baseline", "after_capture.framework_chromium_clean_baseline", errs)
    if fw is not None:
        check_all_bools(fw, ("binary_identity_verified", "disposable_context_isolated", "playwright_verified",
                             "representative_suite_smoke", "managed_policies_absent", "forced_extensions_absent"), "after_capture.framework_chromium_clean_baseline", errs)
    norm = d_get(a, "normal_chrome_lane", "after_capture.normal_chrome_lane", errs)
    if norm is not None:
        if norm.get("bundle_id") != cch.get("bundleId", "com.google.Chrome"):
            errs.append("after_capture.normal_chrome_lane.bundle_id")
        check_all_bools(norm, ("binary_identity_verified", "device_policy_inherited", "disposable_context_isolated",
                              "permitted_loopback_navigation", "new_tab_inspect_evaluate", "cdp_websocket_download",
                              "devtools_availability", "playwright_verified", "representative_suite_smoke",
                              "forced_extension_present_in_disposable_profiles"), "after_capture.normal_chrome_lane", errs)
        mcp = d_get(norm, "chrome_devtools_mcp", "after_capture.normal_chrome_lane.chrome_devtools_mcp", errs)
        if mcp is not None:
            check_all_bools(mcp, tuple(cch["mcpClients"]), "after_capture.normal_chrome_lane.chrome_devtools_mcp", errs)
        playwright = d_get(norm, "playwright_mcp", "after_capture.normal_chrome_lane.playwright_mcp", errs)
        if playwright is not None:
            check_all_bools(playwright, tuple(cch["mcpClients"]), "after_capture.normal_chrome_lane.playwright_mcp", errs)
        for group, values in (("chrome_devtools_mcp", mcp), ("playwright_mcp", playwright)):
            for client in cch["mcpClientsAbsent"]:
                if values is None or values.get(client) != "not-configured":
                    errs.append(f"after_capture.normal_chrome_lane.{group}.{client}")

def check_safari_wipr(w: dict, cw: dict, bw: dict | None, errs: list[str]) -> None:
    if w.get("bundle_id") != cw.get("bundleId", "site.kaylees.Wipr2"):
        errs.append("after_capture.safari.wipr.bundle_id")
    if str(w.get("app_store_id")) != str(cw.get("appStoreId", "1662217862")):
        errs.append("after_capture.safari.wipr.app_store_id")
    check_bool(w.get("app_metadata_identity_verified"), True, "after_capture.safari.wipr.app_metadata_identity_verified", errs)
    exts = d_get(w, "extensions", "after_capture.safari.wipr.extensions", errs)
    if exts is None or set(exts.keys()) != set(EXACT_WIPR):
        if exts is not None:
            errs.append("after_capture.safari.wipr.extensions")
        return
    for ext in EXACT_WIPR:
        ed = exts.get(ext, {})
        if not isinstance(ed, dict):
            errs.append(f"after_capture.safari.wipr.extensions.{ext}")
            continue
        check_bool(ed.get("enabled"), True, f"after_capture.safari.wipr.extensions.{ext}.enabled", errs)
        if ed.get("status") != "ok":
            errs.append(f"after_capture.safari.wipr.extensions.{ext}.status")
    check_bool(exts.get("Wipr Extra", {}).get("site_permission_granted"), True, "after_capture.safari.wipr.extensions.Wipr Extra.site_permission_granted", errs)
    if bw and isinstance(bw.get("extensions"), dict):
        for pe in bw["extensions"]:
            if pe not in exts:
                errs.append("after_capture.safari.wipr.extensions.prior_membership")

def check_safari(saf: dict, bsaf: dict | None, csaf: dict, errs: list[str]) -> None:
    if saf.get("bundle_id") != csaf.get("bundleId", "com.apple.Safari"):
        errs.append("after_capture.safari.bundle_id")
    if saf.get("search_engine") != csaf.get("searchEngine", "DuckDuckGo"):
        errs.append("after_capture.safari.search_engine")
    pse = saf.get("private_search_engine")
    if pse == "same-as-normal":
        if not (saf.get("private_search_available") is False and saf.get("private_search_availability_documented") is True):
            errs.append("after_capture.safari.private_search_engine")
    elif pse != csaf.get("privateSearchEngine", "DuckDuckGo"):
        errs.append("after_capture.safari.private_search_engine")
    else:
        check_bool(saf.get("private_search_availability_documented"), True, "after_capture.safari.private_search_availability_documented", errs)
    check_bool(saf.get("direct_navigation_ddg_verified"), True, "after_capture.safari.direct_navigation_ddg_verified", errs)
    p = d_get(saf, "protections", "after_capture.safari.protections", errs)
    if p is not None:
        check_all_bools(p, ("prevent_cross_site_tracking", "fraudulent_website_warning", "preserve_other_enabled_protections",
                            "all_three_protections_verified"), "after_capture.safari.protections", errs)
        hip, b_hip = p.get("hide_ip_address"), bsaf.get("protections", {}).get("hide_ip_address") if bsaf else None
        if hip not in ("trackers", "trackers-and-websites") or (b_hip == "trackers-and-websites" and hip != "trackers-and-websites"):
            errs.append("after_capture.safari.protections.hide_ip_address")
    w = d_get(saf, "wipr", "after_capture.safari.wipr", errs)
    if w is not None:
        check_safari_wipr(w, csaf.get("wipr", {}), bsaf.get("wipr") if isinstance(bsaf, dict) else None, errs)
    check_bool(saf.get("personal_data_and_bookmarks_preserved"), True, "after_capture.safari.personal_data_and_bookmarks_preserved", errs)

def check_before_safari(bsaf: Any, errs: list[str]) -> None:
    if not isinstance(bsaf, dict) or any(k not in ALLOWED_BEFORE_SAFARI for k in bsaf):
        errs.append("before_capture.safari")
        return
    if not bsaf.get("search_engine"):
        errs.append("before_capture.safari.search_engine")
    p = bsaf.get("protections")
    if not isinstance(p, dict) or any(k not in p for k in ("prevent_cross_site_tracking", "hide_ip_address", "fraudulent_website_warning")):
        errs.append("before_capture.safari.protections")
    w = bsaf.get("wipr")
    if not isinstance(w, dict) or not isinstance(w.get("extensions"), dict) or set(w["extensions"]) != set(EXACT_WIPR):
        errs.append("before_capture.safari.wipr")

def extension_ids(value: Any, field: str, errs: list[str]) -> set[str] | None:
    if not isinstance(value, list) or any(not isinstance(i, str) or not re.fullmatch(r"[a-p]{32}", i) for i in value):
        errs.append(field)
        return None
    if len(value) != len(set(value)):
        errs.append(field)
        return None
    return set(value)

def check_chrome_ui_change_gates(before: dict, after: dict, gates: dict, errs: list[str]) -> None:
    ui = d_get(before, "ui_settings", "before_capture.chrome.ui_settings", errs)
    if ui is None:
        return
    fields = {
        "metrics": ("ui_disabled",),
        "ad_privacy": ("available", "ui_disabled"),
        "ubol": ("installed", "site_permission_granted", "filtering_mode", "rulesets"),
    }
    if set(ui) != set(fields):
        errs.append("before_capture.chrome.ui_settings.keys")
    for setting, keys in fields.items():
        prefix = f"before_capture.chrome.ui_settings.{setting}"
        old = d_get(ui, setting, prefix, errs)
        if old is None:
            continue
        check_bool(old.get("readback_verified"), True, f"{prefix}.readback_verified", errs)
        if set(old) != set(keys) | {"readback_verified"}:
            errs.append(f"{prefix}.keys")
        for key in keys:
            if key not in ("filtering_mode", "rulesets") and type(old.get(key)) is not bool:
                errs.append(f"{prefix}.{key}")
        if setting == "ubol" and old.get("filtering_mode") not in ("none", "basic", "optimal", "complete", "not-installed"):
            errs.append(f"{prefix}.filtering_mode")
        if setting == "ubol" and old.get("rulesets") not in ("built-in-defaults", "custom", "not-installed"):
            errs.append(f"{prefix}.rulesets")
        new = after.get(setting, {})
        # Feature removal on upgrade is readback, not an operator UI change.
        if setting == "ad_privacy" and new.get("available") is False:
            continue
        if not strict_eq({k: old.get(k) for k in keys}, {k: new.get(k) for k in keys}):
            gate = "chrome_ubol_site_permissions" if setting == "ubol" else f"chrome_{setting}_ui"
            if gates.get(gate, {}).get("changed") is not True:
                errs.append(f"operator_approval_gates.{gate}.changed")

def check_chrome_transition(b: dict, ch: dict, gates: dict, ubol_id: str, errs: list[str]) -> None:
    before = d_get(b, "chrome", "before_capture.chrome", errs)
    after = d_get(ch, "extension_transition", "after_capture.chrome.extension_transition", errs)
    if before is None or after is None:
        return
    for name in ("policies_readback_status", "installed_extensions_readback_status"):
        if before.get(name) != "ok":
            errs.append(f"before_capture.chrome.{name}")
    if before.get("method") != "chrome://policy+chrome://extensions":
        errs.append("before_capture.chrome.method")
    allowed = {"policies_readback_status", "installed_extensions_readback_status", "method", "forced_extension_ids", "installed_extension_ids", "legacy_keepassxc_root_present", "ui_settings"}
    if set(before) != allowed:
        errs.append("before_capture.chrome.keys")
    forced = extension_ids(before.get("forced_extension_ids"), "before_capture.chrome.forced_extension_ids", errs)
    installed = extension_ids(before.get("installed_extension_ids"), "before_capture.chrome.installed_extension_ids", errs)
    retained = extension_ids(after.get("installed_extension_ids"), "after_capture.chrome.extension_transition.installed_extension_ids", errs)
    check_bool(after.get("extension_storage_preserved"), True, "after_capture.chrome.extension_transition.extension_storage_preserved", errs)
    if installed is not None and retained is not None and not installed <= retained:
        errs.append("after_capture.chrome.extension_transition.extensions_removed")
    root_present = before.get("legacy_keepassxc_root_present")
    if type(root_present) is not bool:
        errs.append("before_capture.chrome.legacy_keepassxc_root_present")
    if forced is not None and (forced - {ubol_id} or root_present is True):
        if gates.get("chrome_extension_policy_transition", {}).get("changed") is not True:
            errs.append("operator_approval_gates.chrome_extension_policy_transition.changed")
    if installed is not None and "oboonakemofpalcgghocfoadofidjkkk" in installed:
        check_bool(after.get("retained_keepassxc_browser_functional"), True, "after_capture.chrome.extension_transition.retained_keepassxc_browser_functional", errs)

def check_safari_change_gates(before: dict, after: dict, gates: dict, errs: list[str]) -> None:
    def selected(data, keys):
        return {key: data.get(key) for key in keys}
    pairs = {
        "safari_search_engine": (selected(before, ("search_engine", "private_search_engine")), selected(after, ("search_engine", "private_search_engine"))),
        "safari_privacy_protections": (selected(before.get("protections", {}), ("prevent_cross_site_tracking", "hide_ip_address", "fraudulent_website_warning")), selected(after.get("protections", {}), ("prevent_cross_site_tracking", "hide_ip_address", "fraudulent_website_warning"))),
    }
    for gate, (old, new) in pairs.items():
        if not strict_eq(old, new) and gates.get(gate, {}).get("changed") is not True:
            errs.append(f"operator_approval_gates.{gate}.changed")
    old_ext = before.get("wipr", {}).get("extensions", {})
    new_ext = after.get("wipr", {}).get("extensions", {})
    changed = any(not strict_eq(selected(old_ext.get(name, {}), ("enabled", "site_permission_granted")), selected(new_ext.get(name, {}), ("enabled", "site_permission_granted"))) for name in EXACT_WIPR)
    if changed and gates.get("safari_wipr_extensions", {}).get("changed") is not True:
        errs.append("operator_approval_gates.safari_wipr_extensions.changed")

def check_stock_and_preserved(a: dict, c: dict, errs: list[str]) -> None:
    st = d_get(a, "stock", "after_capture.stock", errs)
    if st is not None:
        br, lw = st.get("brave", {}), st.get("librewolf", {})
        if br.get("bundle_id") != c.get("stock", {}).get("brave", {}).get("bundleId", "com.brave.Browser"):
            errs.append("after_capture.stock.brave.bundle_id")
        check_all_bools(br, ("managed_policies_absent", "single_brave_identity_verified", "brave_youtube_profile_preserved", "startup_verified"), "after_capture.stock.brave", errs)
        check_bool(br.get("brave_youtube_startup_verified"), True, "after_capture.stock.brave.brave_youtube_startup_verified", errs)
        if br.get("brave_youtube_bundle_id") != "com.operator.brave-youtube":
            errs.append("after_capture.stock.brave.brave_youtube_bundle_id")
        if lw.get("bundle_id") != c.get("stock", {}).get("librewolf", {}).get("bundleId", "io.gitlab.librewolf-community"):
            errs.append("after_capture.stock.librewolf.bundle_id")
        if lw.get("package") != "librewolf" or lw.get("cask_readback_status") != "ok":
            errs.append("after_capture.stock.librewolf.cask_readback_status")
        check_all_bools(lw, ("managed_policies_absent", "startup_verified"), "after_capture.stock.librewolf", errs)
    pt = d_get(a, "preserved_targets", "after_capture.preserved_targets", errs)
    if pt is not None:
        check_all_bools(pt, tuple(c.get("preserve", [])), "after_capture.preserved_targets", errs)

def validate_contract_and_evidence(
    contract_data: dict[str, Any], evidence_data: dict[str, Any], contract_sha: str | None = None
) -> list[str]:
    errs: list[str] = []
    if not isinstance(contract_data, dict) or not isinstance(evidence_data, dict):
        return ["contract_or_evidence_object"]
    check_meta_and_sha(contract_data, evidence_data, contract_sha, errs)
    b, a = d_get(evidence_data, "before_capture", "before_capture", errs), d_get(evidence_data, "after_capture", "after_capture", errs)
    if a is None:
        return errs
    tb, ta = check_captures_meta(b or {}, a, errs)
    gates = d_get(evidence_data, "operator_approval_gates", "operator_approval_gates", errs)
    if gates is not None:
        check_approval_gates(gates, tb, ta, errs)
    check_before_safari(b.get("safari") if b else None, errs)
    ch = d_get(a, "chrome", "after_capture.chrome", errs)
    if ch is not None:
        check_chrome(ch, contract_data.get("chrome", {}), errs)
        check_chrome_transition(b or {}, ch, gates or {}, contract_data.get("chrome", {}).get("requiredExtension", {}).get("id", ""), errs)
        if b and isinstance(b.get("chrome"), dict):
            check_chrome_ui_change_gates(b["chrome"], ch, gates or {}, errs)
    check_lanes(a, contract_data.get("chrome", {}), errs)
    saf = d_get(a, "safari", "after_capture.safari", errs)
    if saf is not None:
        check_safari(saf, b.get("safari") if b else None, contract_data.get("safari", {}), errs)
        if b and isinstance(b.get("safari"), dict) and gates is not None:
            check_safari_change_gates(b["safari"], saf, gates, errs)
    check_stock_and_preserved(a, contract_data, errs)
    return errs

def run_validation(contract_path: Path, evidence_path: Path) -> tuple[bool, list[str]]:
    if not contract_path.exists():
        return False, ["contract_path"]
    if not evidence_path.exists():
        return False, ["evidence_path"]
    try:
        c_bytes = contract_path.read_bytes()
        c_data, sha = json.loads(c_bytes.decode("utf-8")), hashlib.sha256(c_bytes).hexdigest()
        ev_data = json.loads(evidence_path.read_text(encoding="utf-8"))
    except Exception:
        return False, ["contract_or_evidence_json"]
    try:
        errs = validate_contract_and_evidence(c_data, ev_data, contract_sha=sha)
    except (AttributeError, KeyError, TypeError, ValueError):
        return False, ["evidence_schema_shape"]
    return len(errs) == 0, [sanitize_text(e) for e in errs]

def main() -> int:
    p = argparse.ArgumentParser(prog="validate.py")
    p.add_argument("--contract", required=True, type=Path)
    p.add_argument("--evidence", required=True, type=Path)
    args = p.parse_args()
    ok, errs = run_validation(args.contract, args.evidence)
    if ok:
        print("EVIDENCE_CHECKLIST_COMPLETE; independent host qualification required")
        return 0
    print(f"VALIDATION STATUS: FAILED\nTotal validation errors: {len(errs)}")
    for i, e in enumerate(errs, 1):
        print(f"  {i}. {e}")
    return 1

if __name__ == "__main__":
    sys.exit(main())
