# Connected source evaluation with inert package/writer boundaries; no host effects.
{ repoRoot, previousRoot, ownerRoot, ownerR3Root, libPath }:
let
  lib = import libPath;
  artifact = name: text: { outPath = "/fixture/${name}"; inherit text; };
  package = name: { type = "derivation"; outPath = "/fixture/${name}"; inherit name; };
  pkgs = {
    google-chrome = package "google-chrome"; brave = package "brave"; python3 = package "python3";
    writeText = artifact; writeShellScript = artifact;
    writeShellScriptBin = name: text: (package name) // { inherit text; };
  };
  stub = {
    _file = "/fixture/system/activation-scripts.nix";
    options = {
      assertions = lib.mkOption { type = lib.types.listOf lib.types.anything; default = []; };
      environment.systemPackages = lib.mkOption { type = lib.types.listOf lib.types.package; default = []; };
      homebrew.casks = lib.mkOption { type = lib.types.listOf lib.types.anything; default = []; };
      system.build = lib.mkOption { type = lib.types.attrsOf lib.types.anything; default = {}; };
      system.defaults = lib.mkOption { type = lib.types.attrsOf lib.types.anything; default = {}; };
      system.activationScripts = lib.mkOption {
        type = lib.types.attrsOf (lib.types.submodule {
          options.text = lib.mkOption { type = lib.types.lines; default = ""; };
        }); default = {};
      };
    };
  };
  evaluate = root: selected: (lib.evalModules {
    specialArgs = { inherit pkgs; };
    modules = [ (root + "/modules/browsers.nix") stub {
      programs.browserBaseline = { rolesR3 = selected; corePolicyOwnership = selected; };
    } ];
  }).config;
  prior = evaluate previousRoot true;
  active = evaluate repoRoot true;
  inactive = evaluate repoRoot false;
  b = active.system.build.browserBaseline;
  normalize = root: value: builtins.replaceStrings [ (toString root) ] [ "<core>" ] (builtins.toJSON value);
  previousOwner = import (ownerR3Root + "/config/browser/roles-r3.nix");
  owner = import (ownerRoot + "/config/browser/roles-r3.nix");
  vpn = owner.floorp.extensions."vpn@proton.ch";
in {
  checks = {
    inactiveCatalogAbsent = inactive.system.build.browserBaseline.protonVpnR3R2 == null;
    inactiveAssertionsPass = lib.all (a: a.assertion) inactive.assertions;
    inactiveNoDeploymentFreeze = !inactive.system.build.browserBaseline.deploymentHeld;
    activeCatalogMatches = b.protonVpnR3R2 == builtins.fromJSON
      (builtins.readFile (repoRoot + "/config/browser/proton-vpn-r3r2.json"));
    allPriorActiveOutputsPreserved = normalize repoRoot (builtins.removeAttrs b [ "protonVpnR3R2" ])
      == normalize previousRoot prior.system.build.browserBaseline;
    activePackagesPreserved = active.environment.systemPackages == prior.environment.systemPackages;
    activeCasksPreserved = active.homebrew.casks == prior.homebrew.casks;
    activeDefaultsPreserved = active.system.defaults == prior.system.defaults;
    activeActivationPreserved = normalize repoRoot active.system.activationScripts
      == normalize previousRoot prior.system.activationScripts;
    activeRefusalPreserved = builtins.any (a: !a.assertion && a.message == b.deploymentHoldMessage) active.assertions;
    floorpExistingSuitePreserved = builtins.removeAttrs owner.floorp.extensions [ "vpn@proton.ch" ]
      == previousOwner.floorp.extensions;
    onlyFloorpOwnerIntentChanged = builtins.removeAttrs owner [ "floorp" ] == builtins.removeAttrs previousOwner [ "floorp" ];
    floorpExistingSettingsPreserved = builtins.removeAttrs owner.floorp [ "extensions" ]
      == builtins.removeAttrs previousOwner.floorp [ "extensions" ];
    floorpOneAddition = builtins.length (builtins.attrNames owner.floorp.extensions)
      == builtins.length (builtins.attrNames previousOwner.floorp.extensions) + 1;
    floorpCorrectSignedIdentity = vpn.geckoId == "vpn@proton.ch";
    floorpNoAutomaticEffects = !vpn.automaticInstallation && !vpn.automaticSignIn && !vpn.autoConnectDefault;
    floorpNoPolicies = owner.floorp.deliveredPolicies == {};
    coreNoFloorpExtensionOwner = !(b.protonVpnR3R2.roles ? floorp);
    automationAvailabilityHeld = b.protonVpnR3R2.roles.brave-automation.status == "AUTOMATION_AVAILABILITY_HELD"
      && !b.protonVpnR3R2.roles.brave-automation.persistentInstallTarget
      && !b.protonVpnR3R2.roles.brave-automation.extensionsEnabled;
    ownershipReconciliationHeld = b.protonVpnR3R2.heldBrowserOwnerConflicts == [ "brave-daily" "librewolf" "orion" ];
  };
  floorpExtensions = owner.floorp.extensions;
  ownerExtensionIntents = owner;
  activeScripts = {
    activation = active.system.activationScripts.postActivation.text;
    aggregate = b.attendedApply.text;
    automation = b.automationBrowser.text;
  } // lib.mapAttrs (_: helper: helper.text) b.attendedApplyByBrowser;
  nativeGraphQualified = false;
}
