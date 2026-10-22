# Connected real source modules and claims; packages/writers are inert fixtures.
{ repoRoot, previousRoot, ownerRoot, previousOwnerRoot, compositionRoot,
  previousCompositionRoot, configurationFile, libPath, emitScripts ? false }:
let
  lib = import libPath;
  target = "example-primary";
  predecessor = "example-workstation";
  artifact = name: text: { outPath = "/fixture/${name}"; inherit text; };
  package = name: { type = "derivation"; outPath = "/fixture/${name}"; inherit name; };
  pkgs = {
    google-chrome = package "google-chrome"; brave = package "brave";
    python3 = package "python3"; qbittorrent = package "qbittorrent";
    writeText = artifact; writeShellScript = artifact;
    writeShellScriptBin = name: text: (package name) // { inherit text; };
  };
  stub = {
    _file = "/fixture/system/activation-scripts.nix";
    options = {
      assertions = lib.mkOption { type = lib.types.listOf lib.types.anything; default = []; };
      networking.hostName = lib.mkOption { type = lib.types.str; };
      environment.systemPackages = lib.mkOption { type = lib.types.listOf lib.types.package; default = []; };
      homebrew.casks = lib.mkOption { type = lib.types.listOf lib.types.anything; default = []; };
      homebrew.brews = lib.mkOption { type = lib.types.listOf lib.types.str; default = []; };
      homebrew.masApps = lib.mkOption { type = lib.types.attrsOf lib.types.int; default = {}; };
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
  projection = lib.mapAttrs (_: configuration: { inherit configuration; })
    (builtins.fromJSON (builtins.readFile configurationFile));
  selector = composition: selected: import (composition + "/lib/browser-ux-host-selection.nix") {
    inherit lib; rolesR3Selected = selected;
  };
  evaluate = core: owner: composition: selected: host: extras:
    let selection = selector composition selected; in (lib.evalModules {
      specialArgs = { inherit pkgs; };
      modules = [ (core + "/modules/browsers.nix") (core + "/modules/desktop-apps.nix")
        (owner + "/modules/default.nix") stub (selection.moduleForHost host projection) {
          networking.hostName = host;
          programs.browserBaseline = { rolesR3 = selected && host == target;
            corePolicyOwnership = selected && host == target; };
          homebrew.casks = selection.casksForHost host projection;
          environment.systemPackages = [ (package "unrelated-project-pin") ];
        }
      ] ++ extras;
    }).config;
  current = selected: host: extras: evaluate repoRoot ownerRoot compositionRoot selected host extras;
  prior = host: evaluate previousRoot previousOwnerRoot previousCompositionRoot false host [];
  active = current true target [];
  priorActive = evaluate previousRoot previousOwnerRoot previousCompositionRoot true target [];
  inactive = host: current false host [];
  normalize = core: owner: value: builtins.replaceStrings
    [ (toString core) (toString owner) ] [ "<core>" "<owner>" ] (builtins.toJSON value);
  same = normalize repoRoot ownerRoot;
  old = normalize previousRoot previousOwnerRoot;
  equal = a: b: same a == old b;
  payload = item: if item == null then null else item.text;
  claimsLib = import (repoRoot + "/lib/application-claims.nix") { inherit lib; };
  inventory = core: import (core + "/lib/core-application-inventory.nix");
  claims = core: owner: composition: selected: host:
    let inv = inventory core; selection = selector composition selected;
      ownerClaims = import (owner + "/lib/application-claims.nix");
      coreClaims = claimsLib.attachOwner "core" (map (c: builtins.removeAttrs c inv.provenanceFields)
        (inv.guiApplicationsFor { hostName = host; rolesR3 = selected; }));
      uxClaims = if selected then ownerClaims.applicationClaimsForHost { hostName = host; }
        else ownerClaims.applicationClaimsFor (projection.browser-ux.configuration.replacements or {});
    in selection.activeClaimsForHost host projection (coreClaims
      ++ builtins.filter (selection.claimAppliesToHost host) (claimsLib.attachOwner "browser-ux" uxClaims));
  currentClaims = claims repoRoot ownerRoot compositionRoot;
  priorClaims = claims previousRoot previousOwnerRoot previousCompositionRoot false;
  inactiveChecks = host: let c = inactive host; p = prior host; in {
    noGlobalFreeze = !c.system.build.browserBaseline.deploymentHeld && lib.all (a: a.assertion) c.assertions;
    packages = equal c.environment.systemPackages p.environment.systemPackages;
    casks = c.homebrew.casks == p.homebrew.casks;
    officeCasks = builtins.filter (n: builtins.elem n [ "libreoffice" "onlyoffice" "zettlr" ]) c.homebrew.casks
      == builtins.filter (n: builtins.elem n [ "libreoffice" "onlyoffice" "zettlr" ]) p.homebrew.casks;
    policyAndStorageOutputs = equal c.system.build.browserBaseline p.system.build.browserBaseline;
    rendering = equal c."browser-ux".replacementRendering p."browser-ux".replacementRendering;
    activation = equal c.system.activationScripts p.system.activationScripts;
    claims = currentClaims false host == priorClaims host;
  };
  b = active.system.build.browserBaseline;
  referenceBaseline = (inactive target).system.build.browserBaseline;
  activeClaims = currentClaims true target;
  vivaldiClaims = builtins.filter (c: c.claim.id == "bundle:com.vivaldi.Vivaldi") activeClaims;
  officeClaims = builtins.filter (c: builtins.elem c.claim.package [ "libreoffice" "onlyoffice" "zettlr" ]);
  rejectReference = extras:
    let result = builtins.tryEval (builtins.any (a: !a.assertion
      && lib.hasPrefix "Vivaldi visual reference" a.message) (current true target extras).assertions);
    in !result.success || result.value;
  roles = import (ownerRoot + "/config/browser/roles-r3.nix");
  previousRoles = import (previousOwnerRoot + "/config/browser/roles-r3.nix");
  guards = { activation = active.system.activationScripts.postActivation.text;
    aggregate = b.attendedApply.text; automation = b.automationBrowser.text;
  } // lib.mapAttrs (_: h: h.text) b.attendedApplyByBrowser;
in if emitScripts then guards else {
  checks = {
    target = inactiveChecks target;
    predecessor = inactiveChecks predecessor;
    active = {
      sevenPrimaryRoles = builtins.length b.rolesR3.roles == 7
        && !(lib.any (r: r.id == "vivaldi") b.rolesR3.roles);
      referenceRetained = b.rolesR3.continuity.vivaldi == "VIVALDI_VISUAL_REFERENCE_RETAINED_DECOMMISSION_HELD"
        && !b.rolesR3.references.vivaldi.primaryRole && !roles.vivaldi.primaryRole;
      oneVivaldiCask = builtins.length (builtins.filter (n: n == "vivaldi") active.homebrew.casks) == 1;
      oneVivaldiClaim = builtins.length vivaldiClaims == 1 && (builtins.head vivaldiClaims).owner == "browser-ux"
        && (builtins.head vivaldiClaims).claim.package == "vivaldi";
      claimsValid = (claimsLib.validate activeClaims).ok;
      noCompositionSecondWriter = (selector compositionRoot true).casksForHost target projection == [];
      officePreserved = officeClaims activeClaims == officeClaims (currentClaims false target);
      cleanupNone = active.homebrew.onActivation.cleanup == "none";
      renderingPreserved = equal active."browser-ux".replacementRendering.vivaldi
        (prior target)."browser-ux".replacementRendering.vivaldi;
      renderingNotRetracted = active."browser-ux".replacementRendering.vivaldi != null;
      policyBytesPreserved = payload b.vivaldiManagedPlist == payload referenceBaseline.vivaldiManagedPlist;
      extensionsPreserved = equal b.vivaldiExternalExtensions referenceBaseline.vivaldiExternalExtensions;
      nativeMessagingPreserved = equal b.vivaldiNativeMessagingHosts referenceBaseline.vivaldiNativeMessagingHosts;
      replacementWriterPreserved = active.programs.browserBaseline.vivaldiPolicyOwnership
        == (inactive target).programs.browserBaseline.replacementPolicyOwnership;
      floorpActivationPreserved = equal b.attendedApplyByBrowser.floorp.text
        priorActive.system.build.browserBaseline.attendedApplyByBrowser.floorp.text
        && !(lib.hasInfix ''rm -f "/Library/Preferences/app.floorp.Floorp.plist"''
          active.system.activationScripts.postActivation.text);
      floorpPolicySelectionPreserved = payload b.floorpManagedPlist
        == payload priorActive.system.build.browserBaseline.floorpManagedPlist
        && equal active."browser-ux".replacementRendering.floorp
          priorActive."browser-ux".replacementRendering.floorp;
      productivityUnchanged = active."browser-ux".replacementRendering.vivaldi.featureOverlays
        == (inactive target)."browser-ux".replacementRendering.vivaldi.featureOverlays;
      floorpPreferred = b.rolesR3.references.vivaldi.primaryRole == false
        && lib.any (r: r.id == "floorp" && r.role == "Floorp power-user") b.rolesR3.roles;
      otherUxRolesUnchanged = equal (builtins.removeAttrs roles ["vivaldi"])
        (builtins.removeAttrs previousRoles ["vivaldi"]);
      noProfileWriter = !roles.vivaldi.profileWriter && !roles.vivaldi.mailOAuthTroubleshooting;
      onlyDeploymentAssertionFails = builtins.filter (a: !a.assertion) active.assertions
        == [{ assertion = false; message = b.deploymentHoldMessage; }];
      guardsBeforeEffects = lib.all (s: lib.hasInfix "exit 78" s) (builtins.attrValues guards);
      noSemanticCollisions = b.guardViolations == [];
      disableReferenceRejected = rejectReference [{ "browser-ux".configuration.replacements.vivaldi.enable = lib.mkForce false; }];
      disableReferenceWithoutMailRejected = rejectReference [{ "browser-ux".configuration.replacements.vivaldi = {
        enable = lib.mkForce false;
        mail = { enable = lib.mkForce false; "preserve-resolved" = lib.mkForce false; };
      }; }];
      retireReferenceRejected = rejectReference [{ "browser-ux".configuration.primaryCutover.retiredBrowsers = [ "vivaldi" ]; }];
      cleanupReferenceRejected = rejectReference [{ homebrew.onActivation.cleanup = "zap"; }];
      predecessorUnaffectedByActive = equal (current true predecessor []).system.build.browserBaseline
        (prior predecessor).system.build.browserBaseline
        && currentClaims true predecessor == priorClaims predecessor;
    };
  };
  nativeGraphQualified = false;
}
