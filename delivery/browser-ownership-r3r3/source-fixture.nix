# Connected source modules and owner patch; inert package/artifact boundaries.
{ repoRoot, previousRoot, ownerRoot, previousOwnerRoot, libPath }:
let
  lib = import libPath;
  ownership = import (repoRoot + "/lib/browser-extension-ownership.nix") { inherit lib; };
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
      networking.hostName = lib.mkOption { type = lib.types.str; default = "example-primary"; };
      environment.systemPackages = lib.mkOption { type = lib.types.listOf lib.types.package; default = []; };
      homebrew.casks = lib.mkOption { type = lib.types.listOf lib.types.anything; default = []; };
      homebrew.onActivation.cleanup = lib.mkOption { type = lib.types.str; default = "none"; };
      system.build = lib.mkOption { type = lib.types.attrsOf lib.types.anything; default = {}; };
      system.defaults = lib.mkOption { type = lib.types.attrsOf lib.types.anything; default = {}; };
      system.activationScripts = lib.mkOption {
        type = lib.types.attrsOf (lib.types.submodule {
          options.text = lib.mkOption { type = lib.types.lines; default = ""; };
        }); default = {};
      };
    };
  };
  evaluate = core: owner: selected: extras: (lib.evalModules {
    specialArgs = { inherit pkgs; };
    modules = [ (core + "/modules/browsers.nix") (owner + "/modules/default.nix") stub {
      programs.browserBaseline = { rolesR3 = selected; corePolicyOwnership = selected; };
    } ] ++ extras;
  }).config;
  active = evaluate repoRoot ownerRoot true [];
  priorActive = evaluate previousRoot previousOwnerRoot true [];
  inactive = host: evaluate repoRoot ownerRoot false [{ networking.hostName = host; }];
  priorInactive = host: evaluate previousRoot previousOwnerRoot false [{ networking.hostName = host; }];
  b = active.system.build.browserBaseline;
  delivery = b.extensionDeliveryR3R3;
  entries = delivery.semanticEntries;
  vpn = entries."brave-daily/jplgfhpmjnbigmhklmmbgecoobifkmpa";
  normalize = core: owner: value: builtins.replaceStrings
    [ (toString core) (toString owner) ] [ "<core>" "<owner>" ] (builtins.toJSON value);
  same = value: normalize repoRoot ownerRoot value;
  old = value: normalize previousRoot previousOwnerRoot value;
  rejects = extras: (evaluate repoRoot ownerRoot true extras).system.build.browserBaseline.guardViolations != [];
  existing = { DisableTelemetry = true; ExtensionInstallForcelist = ["keep-forced"];
    ExtensionSettings = { "existing-cnt" = { installation_mode = "force_installed"; }; }; };
  added = { ${vpn.id} = { installation_mode = "normal_installed"; update_url = vpn.updateUrl; }; };
  merged = ownership.preservingPolicyMerge existing added;
  ownerRoles = import (ownerRoot + "/config/browser/roles-r3.nix");
  previousRoles = import (previousOwnerRoot + "/config/browser/roles-r3.nix");
  inactivePreservation = host: let c = inactive host; p = priorInactive host; in {
    inactiveNoGlobalFreeze = !c.system.build.browserBaseline.deploymentHeld;
    assertionsPass = lib.all (a: a.assertion) c.assertions;
    packages = c.environment.systemPackages == p.environment.systemPackages;
    casks = c.homebrew.casks == p.homebrew.casks;
    defaults = c.system.defaults == p.system.defaults;
    policyAndStorageOutputs = same (builtins.removeAttrs c.system.build.browserBaseline
      ["protonVpnR3R3" "extensionDeliveryR3R3"])
      == old p.system.build.browserBaseline;
    activation = same c.system.activationScripts == old p.system.activationScripts;
    deliveryAbsent = c.system.build.browserBaseline.extensionDeliveryR3R3 == null;
  };
in {
  checks = {
    target = inactivePreservation "example-primary";
    predecessor = inactivePreservation "example-workstation";
    active = {
      onlyExpectedDeploymentAssertion = builtins.filter (a: !a.assertion) active.assertions
        == [{ assertion = false; message = b.deploymentHoldMessage; }];
      noSemanticCollisions = b.guardViolations == [];
      coreSecurityAndUxConvenienceMerge = entries ? "brave-daily/felphkbfjadmcejnibcmcncimlappdde"
        && entries."brave-daily/felphkbfjadmcejnibcmcncimlappdde".owner == "browser-ux"
        && vpn.owner == "core";
      onePlanWriterNoPolicyWriter = delivery.canonicalPlanWriter == "core:modules/browsers.nix"
        && delivery.physicalPolicyWriter == null && !delivery.runtimeInstalled;
      actualCoreProcedure = lib.hasInfix "confirm **Add extension**" delivery.attendedPlan.text;
      actualFloorpProcedure = lib.hasInfix "approve **Add**" delivery.attendedPlan.text;
      floorpSoleSemanticOwner = entries."floorp/vpn@proton.ch".owner == "browser-ux";
      existingActiveOutputsPreserved = same (builtins.removeAttrs b ["protonVpnR3R2" "protonVpnR3R3" "extensionDeliveryR3R3"])
        == old (builtins.removeAttrs priorActive.system.build.browserBaseline ["protonVpnR3R2"]);
      activeActivationUnchanged = same active.system.activationScripts == old priorActive.system.activationScripts;
      floorpSuitePreserved = builtins.removeAttrs ownerRoles.floorp.extensions ["vpn@proton.ch"]
        == builtins.removeAttrs previousRoles.floorp.extensions ["vpn@proton.ch"];
      floorpSettingsPreserved = builtins.removeAttrs ownerRoles.floorp ["extensions"]
        == builtins.removeAttrs previousRoles.floorp ["extensions"];
      floorpCredentialIntentPreserved = ownerRoles.floorp.credentialExtension == "password-manager-firefox-extension@apple.com";
      automationNotApplicable = b.protonVpnR3R3.automation.status == "AUTOMATION_EXTENSION_NOT_APPLICABLE_BY_DESIGN"
        && !b.protonVpnR3R3.automation.extensionAvailable;
      existingPolicyLeavesPreserved = builtins.removeAttrs merged ["ExtensionSettings"]
        == builtins.removeAttrs existing ["ExtensionSettings"]
        && merged.ExtensionSettings."existing-cnt" == existing.ExtensionSettings."existing-cnt";
      duplicateIdRejected = rejects [{ programs.browserBaseline.extensionContributions = [vpn]; }];
      wrongFloorpOwnerRejected = rejects [{ programs.browserBaseline.extensionContributions = [
        (vpn // { role = "floorp"; id = "new@fixture.invalid"; }) ]; }];
      securityIdCannotBeRelabelledConvenience = rejects [{ programs.browserBaseline.extensionContributions = [
        (vpn // { role = "brave-youtube"; id = "pejdijmoenmkgeppbflobdenhhabjlaj";
          owner = "browser-ux"; purpose = "convenience"; }) ]; }];
      priorityOverrideRejected = rejects [{ programs.browserBaseline.extensionContributions = lib.mkForce []; }];
      invalidIdRejected = rejects [{ programs.browserBaseline.extensionContributions = [
        (vpn // { id = "invalid/path"; }) ]; }];
      autoConnectRejected = rejects [{ programs.browserBaseline.extensionContributions = [
        (vpn // { id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"; autoConnect = true; }) ]; }];
      automaticInstallRejected = rejects [{ programs.browserBaseline.extensionContributions = [
        (vpn // { id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"; automaticInstallation = true; }) ]; }];
      automaticSignInRejected = rejects [{ programs.browserBaseline.extensionContributions = [
        (vpn // { id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"; automaticSignIn = true; }) ]; }];
      conflictingPolicyLeafRejected = rejects [{ programs.browserBaseline.extensionContributions = [
        (vpn // { id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"; policy.ExtensionSettings = {}; }) ]; }];
      duplicatePhysicalWriterRejected = rejects [{ _file = "external-writer";
        system.activationScripts.other.text = "defaults write com.brave.Browser ExtensionSettings broken"; }];
      policyDuplicateIdRejected = !(builtins.tryEval (ownership.preservingPolicyMerge existing
        { "existing-cnt" = existing.ExtensionSettings."existing-cnt"; })).success;
      forcedListIdCannotBeOverridden = !(builtins.tryEval (ownership.preservingPolicyMerge
        { ExtensionInstallForcelist = [ "${vpn.id};${vpn.updateUrl}" ]; } added)).success;
      geckoLockedIdCannotBeOverridden = !(builtins.tryEval (ownership.preservingPolicyMerge
        { Extensions.Locked = [ "vpn@proton.ch" ]; }
        { "vpn@proton.ch".installation_mode = "normal_installed"; })).success;
      unidentifiedLegacyInstallPolicyHeld = !(builtins.tryEval (ownership.preservingPolicyMerge
        { Extensions.Install = [ "https://addons.mozilla.org/unknown-moving.xpi" ]; } added)).success;
      policyWildcardRejected = !(builtins.tryEval (ownership.preservingPolicyMerge existing { "*" = {}; })).success;
    };
  };
  semanticEntries = lib.mapAttrs (_: row: builtins.removeAttrs row ["procedure"]) entries;
  activeScripts = { activation = active.system.activationScripts.postActivation.text;
    aggregate = b.attendedApply.text; automation = b.automationBrowser.text;
  } // lib.mapAttrs (_: helper: helper.text) b.attendedApplyByBrowser;
  nativeGraphQualified = false;
}
