# Connected real source modules; inert packages/writers, never a native graph.
{ repoRoot, baselineRoot, browserUxRoot, liveBrowserUxRoot, compositionRoot,
  liveCompositionRoot, constructorFile, libPath, configurationFile ? null, emitScripts ? false }:
let
  lib = import libPath;
  artifact = name: text: { outPath = "/fixture/${name}"; inherit text; };
  package = name: { type = "derivation"; outPath = "/fixture/${name}"; inherit name; };
  pkgs = {
    google-chrome = package "google-chrome"; brave = package "brave"; python3 = package "python3";
    qbittorrent = package "qbittorrent";
    writeText = artifact; writeShellScript = artifact;
    writeShellScriptBin = name: text: (package name) // { inherit text; };
  };
  fixture = {
    _file = "/fixture/system/activation-scripts.nix";
    options = {
      assertions = lib.mkOption { type = lib.types.listOf lib.types.anything; default = []; };
      networking.hostName = lib.mkOption { type = lib.types.str; };
      environment.systemPackages = lib.mkOption { type = lib.types.listOf lib.types.package; default = []; };
      homebrew.casks = lib.mkOption { type = lib.types.listOf lib.types.anything; default = []; };
      homebrew.onActivation.cleanup = lib.mkOption { type = lib.types.str; default = "none"; };
      system.build = lib.mkOption { type = lib.types.attrsOf lib.types.anything; default = {}; };
      system.defaults = lib.mkOption { type = lib.types.attrsOf lib.types.anything; default = {}; };
      system.primaryUser = lib.mkOption { type = lib.types.str; default = "fixture-user"; };
      users.users = lib.mkOption { type = lib.types.attrsOf lib.types.anything; default = { fixture-user.home = "/fixture/user"; }; };
      system.activationScripts = lib.mkOption {
        type = lib.types.attrsOf (lib.types.submodule {
          options.text = lib.mkOption { type = lib.types.lines; default = ""; };
        }); default = {};
      };
    };
  };
  selection = import (compositionRoot + "/lib/browser-ux-host-selection.nix") { inherit lib; };
  activeSelection = import (compositionRoot + "/lib/browser-ux-host-selection.nix") { inherit lib; rolesR3Selected = true; };
  priorSelection = import (liveCompositionRoot + "/lib/browser-ux-host-selection.nix") { inherit lib; };
  projection = if configurationFile == null then { browser-ux.configuration.schema = 1; }
    else lib.mapAttrs (_: value: { configuration = value; }) (builtins.fromJSON (builtins.readFile configurationFile));
  evaluate = core: owner: selector: host: extra: (lib.evalModules {
    specialArgs = { inherit pkgs; };
    modules = [ (core + "/modules/browsers.nix") (owner + "/modules/default.nix") fixture
      (selector.moduleForHost host projection) { networking.hostName = host; }
      { homebrew.casks = selector.casksForHost host projection; }
    ] ++ extra;
  }).config;
  current = host: evaluate repoRoot browserUxRoot selection host [
    (import constructorFile host)
    # Represents an unrelated source contribution through the actual constructor.
    { environment.systemPackages = [ (package "unrelated-project-pin") ]; }
  ];
  prior = host: evaluate baselineRoot liveBrowserUxRoot priorSelection host [
    { environment.systemPackages = [ (package "unrelated-project-pin") ]; }
  ];
  active = evaluate repoRoot browserUxRoot activeSelection "example-primary" [
    { programs.browserBaseline = { rolesR3 = true; corePolicyOwnership = true; }; }
  ];
  wrongOwnership = evaluate repoRoot browserUxRoot activeSelection "example-primary" [
    { programs.browserBaseline.rolesR3 = true; }
  ];
  forced = evaluate repoRoot browserUxRoot selection "example-primary" [
    (import constructorFile "example-primary")
    { programs.browserBaseline = { rolesR3 = lib.mkForce true; corePolicyOwnership = lib.mkForce true; }; }
  ];
  payload = file: if builtins.isAttrs file then file.text else builtins.readFile file;
  manifestBytes = lib.mapAttrs (_: payload);
  targets = cfg: map (row: {
    inherit (row) target;
    action = row.action or "install";
    bytes = if row.source == null then null else payload row.source;
  }) cfg.system.build.browserBaseline.attendedTargets;
  # Commands are compared in order after removing comments/blank lines only.
  commands = text: lib.filter (line: line != "" && !(lib.hasPrefix "#" line))
    (map lib.strings.trim (lib.splitString "\n" text));
  script = root: owner: chromeFile: cfg: commands (builtins.replaceStrings
    [ (toString root) (toString owner) "${chromeFile}" ] [ "<core>" "<owner>" "<legacy-chrome-policy>" ]
    cfg.system.activationScripts.postActivation.text);
  preservation = host: let c = current host; p = prior host;
    b = c.system.build.browserBaseline; old = p.system.build.browserBaseline;
  in {
    assertionsPass = lib.all (a: a.assertion) c.assertions;
    inactive = !c.programs.browserBaseline.rolesR3 && !c.programs.browserBaseline.corePolicyOwnership && !b.deploymentHeld;
    packages = map (p: p.name) c.environment.systemPackages == map (p: p.name) p.environment.systemPackages;
    casks = c.homebrew.casks == p.homebrew.casks;
    chrome = payload b.chromeManagedPlist == payload old.chromeManagedPlist;
    firefox = b.firefoxPreferences == old.firefoxPreferences;
    extensions = manifestBytes b.chromeExternalExtensions == manifestBytes old.chromeExternalExtensions;
    nativeHosts = manifestBytes b.chromeNativeMessagingHosts == manifestBytes old.chromeNativeMessagingHosts;
    targets = targets c == targets p;
    legacyPlistBytes = builtins.readFile (repoRoot + "/config/browser/chrome-policy-legacy.plist")
      == builtins.readFile (baselineRoot + "/config/browser/chrome-policy.plist");
    activation = script repoRoot browserUxRoot (repoRoot + "/config/browser/chrome-policy-legacy.plist") c
      == script baselineRoot liveBrowserUxRoot (baselineRoot + "/config/browser/chrome-policy.plist") p;
    helpers = commands (builtins.replaceStrings [ (toString browserUxRoot) ] [ "<owner>" ] b.attendedApply.text)
      == commands (builtins.replaceStrings [ (toString liveBrowserUxRoot) ] [ "<owner>" ] old.attendedApply.text);
    noAutomation = b.automationBrowser == null;
    noPrivacyPackages = !(builtins.any (name: builtins.elem name [ "librewolf" "orion" ]) c.homebrew.casks);
    oneWriter = builtins.length (lib.splitString "setting up browser policies..." c.system.activationScripts.postActivation.text) == 2;
  };
  checks = { target = preservation "example-primary"; predecessor = preservation "example-workstation"; };
  b = active.system.build.browserBaseline;
  claimsLib = import (repoRoot + "/lib/application-claims.nix") { inherit lib; };
  inv = import (repoRoot + "/lib/core-application-inventory.nix");
  ownerClaims = import (browserUxRoot + "/lib/application-claims.nix");
  claims = host: claimsLib.attachOwner "core" (map (c: builtins.removeAttrs c inv.provenanceFields)
    (inv.guiApplicationsFor { hostName = host; }))
    ++ builtins.filter (selection.claimAppliesToHost host) (claimsLib.attachOwner "browser-ux"
      (ownerClaims.applicationClaimsFor (projection.browser-ux.configuration.replacements or {})));
  baselineInventory = import (baselineRoot + "/lib/core-application-inventory.nix");
  coreEntries = host: rolesR3: inv.guiApplicationsFor ({ hostName = host; }
    // lib.optionalAttrs (builtins.functionArgs inv.guiApplicationsFor ? rolesR3) { inherit rolesR3; });
  declaredCoreCasks = host: rolesR3:
    ((import (repoRoot + "/modules/desktop-apps.nix")) {
      config.networking.hostName = host; inherit lib pkgs;
    }).homebrew.casks
    ++ (lib.evalModules {
      specialArgs = { inherit pkgs; };
      modules = [ (repoRoot + "/modules/browsers.nix") fixture {
        networking.hostName = host;
        programs.browserBaseline = { inherit rolesR3; corePolicyOwnership = rolesR3; };
      } ];
    }).config.homebrew.casks;
  claimedCoreCasks = entries: map (entry: entry.package) (builtins.filter (entry:
    entry.surface == "homebrew.casks" && builtins.elem entry.declaration
      [ "modules/browsers.nix" "modules/desktop-apps.nix" ]) entries);
  inventoryPreservation = host: expectedCount: let entries = coreEntries host false;
    declared = declaredCoreCasks host false;
    claimed = claimedCoreCasks entries ++ builtins.attrNames inv.nonApplicationCasks;
  in {
    baselineEntries = entries == baselineInventory.guiApplicationsFor { hostName = host; };
    claimCount = builtins.length entries == expectedCount;
    declaredCasksClaimed = lib.subtractLists claimed declared == [];
    noStaleCaskClaims = lib.subtractLists declared claimed == [];
    inactiveClaimsValid = (claimsLib.validate (claimsLib.attachOwner "core"
      (map (entry: builtins.removeAttrs entry inv.provenanceFields) entries))).ok;
  };
  inventoryChecks = {
    target = inventoryPreservation "example-primary" 21;
    predecessor = inventoryPreservation "example-workstation" 18;
    defaultInventory = inv.guiApplications == baselineInventory.guiApplications;
    activeTarget = let entries = coreEntries "example-primary" true;
      declared = declaredCoreCasks "example-primary" true;
      claimed = claimedCoreCasks entries;
    in {
      claimCount = builtins.length entries == 24;
      declaredCasksClaimed = lib.subtractLists claimed declared == [];
      noStaleCaskClaims = lib.subtractLists declared claimed == [];
      officePreserved = builtins.filter (e: e.declaration == "modules/desktop-apps.nix") entries
        == builtins.filter (e: e.declaration == "modules/desktop-apps.nix") (coreEntries "example-primary" false);
      claimsValid = (claimsLib.validate (claimsLib.attachOwner "core"
        (map (entry: builtins.removeAttrs entry inv.provenanceFields) entries))).ok;
    };
    predecessorIgnoresR3 = coreEntries "example-workstation" true == coreEntries "example-workstation" false;
  };
  activeChecks = {
    assertionHeld = builtins.any (a: !a.assertion && a.message == b.deploymentHoldMessage) active.assertions;
    allHelpersHeld = lib.all (h: lib.hasInfix "exit 78" h.text) (builtins.attrValues b.attendedApplyByBrowser);
    activationHeld = lib.hasInfix "exit 78" active.system.activationScripts.postActivation.text;
    automationHeld = lib.hasInfix "exit 78" b.automationBrowser.text;
    partialSelectorHeld = wrongOwnership.system.build.browserBaseline.deploymentHeld;
    overrideRejected = builtins.any (a: !a.assertion) forced.assertions;
    sevenRoles = builtins.length b.rolesR3.roles == 7;
    preservingChrome = b.chromePreferences == import (repoRoot + "/config/browser/chrome-policy-legacy-defaults.nix");
    claimsTarget = (claimsLib.validate (claims "example-primary")).ok;
    claimsPredecessor = (claimsLib.validate (claims "example-workstation")).ok;
  };
in
if emitScripts then {
  activation = active.system.activationScripts.postActivation.text;
  aggregate = b.attendedApply.text;
  automation = b.automationBrowser.text;
  chrome = b.attendedApplyByBrowser.chrome.text;
  floorp = b.attendedApplyByBrowser.floorp.text;
  partial = wrongOwnership.system.activationScripts.postActivation.text;
} else { inherit checks activeChecks inventoryChecks; nativeGraphQualified = false; }
