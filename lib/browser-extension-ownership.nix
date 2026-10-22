# Semantic ownership is per (browser role, extension ID); physical writers are
# per destination. This merge emits data only and cannot install an extension.
{ lib }:
let
  allowedRoles = [ "brave-daily" "librewolf" "orion" "floorp" "brave-youtube" ];
  securityIds = [ "jplgfhpmjnbigmhklmmbgecoobifkmpa" "vpn@proton.ch"
    "ddkjiahejlhfcafbddmgiahcphecmpfh" "uBlock0@raymondhill.net"
    "pejdijmoenmkgeppbflobdenhhabjlaj" "password-manager-firefox-extension@apple.com"
    "oboonakemofpalcgghocfoadofidjkkk" "keepassxc-browser@keepassxc.org" ];
  expectedOwner = row:
    if row.role == "floorp" then "browser-ux"
    else if row.role == "brave-youtube" then "core" # Retained wrapper/theme/CNT.
    else if builtins.elem row.id securityIds then "core"
    else if row.purpose == "convenience" then "browser-ux" else "core";
  validId = row: builtins.match
    (if builtins.elem row.role [ "brave-daily" "brave-youtube" ] then "[a-p]{32}"
      else "[a-zA-Z0-9{}@._-]+") row.id != null;
  valid = row:
    builtins.elem row.role allowedRoles
    && builtins.elem row.purpose [ "security" "blocker" "credentials" "convenience" ]
    && row.owner == expectedOwner row
    && (!(builtins.elem row.id securityIds) || row.purpose != "convenience")
    && validId row && row.id != "" && row.id != "*"
    && !(row.automaticInstallation or false)
    && !(row.automaticSignIn or false) && !(row.autoConnect or false)
    && (row.policy or {}) == {};
  merge = rows: builtins.foldl' (state: row:
    let
      key = "${row.role}/${row.id}";
      duplicate = builtins.hasAttr key state.entries;
      errors = lib.optional duplicate "DUPLICATE_EXTENSION_ID:${key}"
        ++ lib.optional (!valid row) "INVALID_EXTENSION_OWNER_OR_EFFECT:${key}";
    in {
      errors = state.errors ++ errors;
      entries = if duplicate || errors != [] then state.entries
        else state.entries // { ${key} = row; };
    }) { entries = {}; errors = []; } rows;
in {
  inherit merge;
  # For later, separately authorized policy adoption: never replace an existing
  # ID, wildcard, forced setting or unrelated leaf. Duplicate IDs are rejected
  # even when values agree; two writers cannot own one leaf.
  preservingPolicyMerge = existing: additions:
    let
      forcedIds = map (entry: builtins.head (lib.splitString ";" entry))
        (existing.ExtensionInstallForcelist or []);
      existingIds = builtins.attrNames (existing.ExtensionSettings or {})
        ++ forcedIds ++ (existing.Extensions.Locked or []);
      collisions = lib.intersectLists existingIds (builtins.attrNames additions);
      unknownInstallIdentities = (existing.Extensions.Install or []) != [];
      # Grouped IDs, wildcard and update_url selectors can overlap individual
      # IDs. Hold them until a separately reviewed selector-aware adoption.
      unsupportedSelectors = lib.any (key: key == "*" || lib.hasInfix "," key
        || lib.hasPrefix "update_url:" key) (builtins.attrNames (existing.ExtensionSettings or {}));
      invalidAdditions = lib.any (key: builtins.match "[a-p]{32}|[a-zA-Z0-9{}._-]+@[a-zA-Z0-9._-]+" key == null)
        (builtins.attrNames additions);
    in if collisions != [] || invalidAdditions || unknownInstallIdentities || unsupportedSelectors then
      throw "EXTENSION_POLICY_LEAF_COLLISION"
    else existing // { ExtensionSettings = (existing.ExtensionSettings or {}) // additions; };
}
