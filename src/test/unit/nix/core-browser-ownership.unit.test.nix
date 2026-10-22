# nix-darwin-test-source-target: modules/browsers.nix
# nix-darwin-test-source-target: config/browser/chrome-policy-defaults.nix
# nix-darwin-test-source-target: lib/browser-extension-ownership.nix
# nix-darwin-test-source-target: config/browser/proton-vpn-r3r3.nix
# Module fixture only: render packages record text, never build or activate.
{ repoRoot, system, libPath ? null }:
let
  lib = if libPath == null then (builtins.getFlake "path:${toString repoRoot}").legacyPackages.${system}.lib
    else import libPath;
  artifact = name: text: { outPath = "/fixture/${name}"; inherit text; };
  package = name: { type = "derivation"; outPath = "/fixture/${name}"; inherit name; };
  pkgs = {
    google-chrome = package "google-chrome";
    brave = package "brave";
    python3 = package "python3";
    writeText = artifact;
    writeShellScript = artifact;
    writeShellScriptBin = name: text: (package name) // { outPath = "/fixture/script-${name}"; inherit text; };
  };
  stub = {
    _file = "/fixture-declaration/system/activation-scripts.nix";
    options = {
      assertions = lib.mkOption { type = lib.types.listOf lib.types.anything; default = []; };
      environment.systemPackages = lib.mkOption { type = lib.types.listOf lib.types.package; default = []; };
      # Match nix-darwin's string coercion and per-cask submodule name semantics.
      homebrew.casks = lib.mkOption {
        type = with lib.types; listOf (coercedTo str (name: { inherit name; }) (submodule {
          options.name = lib.mkOption { type = str; };
        })); default = [];
      };
      system.build = lib.mkOption { type = lib.types.attrsOf lib.types.anything; default = {}; };
      system.defaults = lib.mkOption { type = lib.types.attrsOf lib.types.anything; default = {}; };
      system.activationScripts = lib.mkOption {
        type = lib.types.attrsOf (lib.types.submodule {
          options.text = lib.mkOption { type = lib.types.lines; default = ""; };
        }); default = {};
      };
    };
  };
  evaluate = modules: (lib.evalModules {
    specialArgs = { inherit pkgs; modulesPath = "/fixture-framework"; };
    modules = [ (repoRoot + "/modules/browsers.nix") stub ] ++ modules;
  }).config;
  evaluateActive = modules: evaluate ([{ programs.browserBaseline = { rolesR3 = true; corePolicyOwnership = true; }; }] ++ modules);
  base = evaluateActive [];
  bb = base.system.build.browserBaseline;
  # Ownership rejection must be independent of the intentional deployment hold.
  rejects = module: (evaluateActive [module]).system.build.browserBaseline.guardViolations != [];
  targetCount = target: builtins.length (builtins.filter (row: row.target == target) bb.attendedTargets);
  ownership = import (repoRoot + "/lib/browser-extension-ownership.nix") { inherit lib; };
  vpn = builtins.head bb.protonVpnR3R3.contributions;
  cnt = { role = "brave-daily"; id = "felphkbfjadmcejnibcmcncimlappdde";
    owner = "browser-ux"; purpose = "convenience"; };
  policy = { ExtensionSettings.${vpn.id}.installation_mode = "force_installed";
    DisableTelemetry = true; };
  additions = { "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".installation_mode = "normal_installed"; };
in {
  testStockAcquisitionRejectsCompetingBraveCasksWithoutPolicySelection = {
    expr = map (cask:
      let c = evaluate [{
        programs.browserBaseline.stockApplications = true;
        homebrew.casks = [ cask ];
      }]; in {
        rejected = builtins.any (v: v.type == "duplicate-application") c.system.build.browserBaseline.guardViolations;
        policy = c.programs.browserBaseline.corePolicyOwnership;
      }) [ "brave-browser" { name = "brave-browser"; } "brave-browser@beta" "brave-browser@nightly"
        { name = lib.mkForce "brave-browser@beta"; } { name = lib.mkForce "brave-browser@nightly"; } ];
    expected = lib.replicate 6 { rejected = true; policy = false; };
  };
  testDuplicateBraveGuardIsScopedToStockBraveAcquisition = {
    expr = map (selected:
      (evaluate [{
        programs.browserBaseline.stockApplications = selected;
        homebrew.casks = [ "brave-browser" ];
      }]).system.build.browserBaseline.guardViolations != []) [ false true ];
    expected = [ false true ];
  };
  testRepeatedExternalStableCaskDeclarationsAreRejected = {
    expr = let c = evaluate [
      { programs.browserBaseline.stockApplications = true; }
      { _file = "stock-owner-a"; homebrew.casks = [ "brave-browser" ]; }
      { _file = "stock-owner-b"; homebrew.casks = [ { name = "brave-browser"; } ]; }
    ]; in builtins.any (v: v.type == "duplicate-application") c.system.build.browserBaseline.guardViolations;
    expected = true;
  };
  testCaskSubmoduleDefinitionsUseMergedNamesWithoutGuardErrors = {
    expr = map (cask:
      (evaluate [{
        programs.browserBaseline.stockApplications = true;
        homebrew.casks = [ cask ];
      }]).system.build.browserBaseline.guardViolations != []
    ) [
      ({ ... }: { name = "unrelated-app"; })
      ({ ... }: { config.name = "unrelated-app"; })
      ({ ... }: { name = "brave-browser"; })
      ({ ... }: { config.name = "brave-browser"; })
      ({ ... }: { name = "brave-browser@beta"; })
      ({ ... }: { config.name = "brave-browser@nightly"; })
    ];
    expected = [ false false true true true true ];
  };
  testUnrelatedPackageOutputIsNotForcedByStockGuards = {
    expr = (evaluate [{
      programs.browserBaseline.stockApplications = true;
      environment.systemPackages = [ ((package "unrelated-app") // {
        outPath = throw "Stock Brave guards forced an unrelated package output";
      }) ];
    }]).system.build.browserBaseline.guardViolations;
    expected = [];
  };
  testStockAcquisitionRejectsNixBraveAndOtherChannels = {
    expr = map (selected: map (p:
      (evaluate [{
        programs.browserBaseline.stockApplications = selected;
        environment.systemPackages = [ p ];
      }]).system.build.browserBaseline.guardViolations != []
    ) [ pkgs.brave (package "brave-beta-1.0") (package "brave-nightly-1.0") ]) [ false true ];
    expected = [ [ false false false ] [ true true true ] ];
  };
  testStockAcquisitionCannotBeReplacedWithPriorityWrappers = {
    expr = map (module:
      (evaluate [{ programs.browserBaseline.stockApplications = true; } module])
        .system.build.browserBaseline.guardViolations != []
    ) [
      { homebrew.casks = lib.mkForce []; }
      { homebrew.casks = lib.mkForce [ "brave-browser" ]; }
      { homebrew.casks = lib.mkForce [ "brave-browser@beta" ]; }
      { homebrew.casks = lib.mkForce [ ({ ... }: { name = "brave-browser"; }) ]; }
      { environment.systemPackages = lib.mkForce []; }
      { environment.systemPackages = lib.mkForce [ pkgs.brave ]; }
    ];
    expected = lib.replicate 6 true;
  };
  testSelectedAcquisitionAllowsPackagePriorityWhenOwnedCLIRemains = {
    expr = let
      cli = builtins.head (builtins.filter (p: p.name == "brave")
        (evaluate [{ programs.browserBaseline.stockApplications = true; }]).environment.systemPackages);
    in map (wrap:
      (evaluate [{
        programs.browserBaseline.stockApplications = true;
        environment.systemPackages = wrap [ cli (package "unrelated-app") ];
      }]).system.build.browserBaseline.guardViolations
    ) [ lib.mkForce (lib.mkOverride 90) ];
    expected = [ [] [] ];
  };
  testUnselectedAcquisitionPreservesGenericPackagePriority = {
    expr = let c = evaluate [{
      homebrew.casks = lib.mkForce [ "brave-browser" ];
      environment.systemPackages = lib.mkForce [ pkgs.brave ];
    }]; in c.system.build.browserBaseline.guardViolations;
    expected = [];
  };
  testStockCLIAndHeldAutomationUseCanonicalCaskApp = {
    expr = let
      c = evaluateActive [];
      cli = builtins.head (builtins.filter (p: p.name == "brave") c.environment.systemPackages);
      automation = c.system.build.browserBaseline.automationBrowser.text;
      target = "'/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'";
    in {
      cliIsAdapter = cli.outPath != pkgs.brave.outPath;
      cliText = cli.text;
      canonicalHeldTarget = lib.hasInfix target automation;
      holdPrecedesLaunch = lib.hasPrefix ''
        printf '%s\n' 'R3_AUTOMATION_HELD_NATIVE_QUALIFICATION_AND_SOURCE_RELEASE_REQUIRED' >&2
        exit 78
      '' automation;
      noStoreBrowser = !(lib.hasInfix pkgs.brave.outPath automation);
    };
    expected = {
      cliIsAdapter = true;
      cliText = ''
        exec '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser' "$@"
      '';
      canonicalHeldTarget = true; holdPrecedesLaunch = true; noStoreBrowser = true;
    };
  };
  testStockAcquisitionSelectorRejectsPriorityOverrides = {
    expr = map (selection:
      builtins.any (v: v.type == "priority-override" && v.option == "programs.browserBaseline.stockApplications")
        (evaluate [{ programs.browserBaseline.stockApplications = selection; }]).system.build.browserBaseline.guardViolations
    ) [ (lib.mkForce true) (lib.mkDefault true) (lib.mkOverride 1500 false) (lib.mkForce false) ];
    expected = [ true true true true ];
  };
  testStockAcquisitionDoesNotSelectPolicyOrProfileTransitions = {
    expr = let c = evaluate [{ programs.browserBaseline.stockApplications = true; }];
      inactive = evaluate [];
      r = c.system.build.browserBaseline;
    in {
      packages = map (p: p.name) c.environment.systemPackages;
      casks = map (cask: cask.name) c.homebrew.casks;
      roles = c.programs.browserBaseline.rolesR3;
      ownership = c.programs.browserBaseline.corePolicyOwnership;
      failures = builtins.filter (a: !a.assertion) c.assertions;
      activationUnchanged = c.system.activationScripts == inactive.system.activationScripts;
      policyUnchanged = r.chromePreferences == inactive.system.build.browserBaseline.chromePreferences;
      automation = r.automationBrowser;
      extensionPlan = r.extensionDeliveryR3R3;
      held = r.deploymentHeld;
      attendedUnchanged = r.attendedTargets == inactive.system.build.browserBaseline.attendedTargets;
      legacyOutputsUnchanged = builtins.all (field: r.${field} == inactive.system.build.browserBaseline.${field}) [
        "chromeExternalExtensions" "chromeNativeMessagingHosts" "firefoxPolicies"
        "firefoxPolicyDelivery" "firefoxNativeMessagingHosts" "vivaldiManagedPlist"
        "vivaldiExternalExtensions" "vivaldiNativeMessagingHosts" "floorpPreferences"
      ];
    };
    expected = {
      packages = ["google-chrome" "brave"]; casks = ["firefox" "brave-browser" "librewolf" "orion"];
      roles = false; ownership = false; failures = []; activationUnchanged = true;
      policyUnchanged = true; automation = null; extensionPlan = null; held = false;
      attendedUnchanged = true; legacyOutputsUnchanged = true;
    };
  };
  testExplicitlyDisabledStockAcquisitionPreservesGenericDefaults = {
    expr = let c = evaluate [{ programs.browserBaseline.stockApplications = false; }];
    in {
      defaultSelection = (evaluate []).programs.browserBaseline.stockApplications;
      packages = map (p: p.name) c.environment.systemPackages;
      casks = map (cask: cask.name) c.homebrew.casks;
      activationUnchanged = c.system.activationScripts == (evaluate []).system.activationScripts;
    };
    expected = { defaultSelection = false; packages = ["google-chrome"]; casks = ["firefox"]; activationUnchanged = true; };
  };
  testR3AndStockSelectionDoNotDuplicateAcquisitionOrReleaseHolds = {
    expr = let c = evaluateActive [{ programs.browserBaseline.stockApplications = true; }];
    in {
      packages = map (p: p.name) c.environment.systemPackages;
      casks = map (cask: cask.name) c.homebrew.casks;
      held = c.system.build.browserBaseline.deploymentHeld;
      automationHeld = lib.hasInfix "exit 78" c.system.build.browserBaseline.automationBrowser.text;
      activationHeld = lib.hasInfix "exit 78" c.system.activationScripts.postActivation.text;
    };
    expected = { packages = ["google-chrome" "brave" "browser-automation"]; casks = ["firefox" "brave-browser" "librewolf" "orion"];
      held = true; automationHeld = true; activationHeld = true; };
  };
  testDistinctSemanticOwnersComposeThroughOnePlan = {
    expr = let r = (evaluateActive [{ programs.browserBaseline.extensionContributions = [cnt]; }])
      .system.build.browserBaseline; d = r.extensionDeliveryR3R3;
    in { errors = r.guardViolations; owner = d.semanticEntries."${cnt.role}/${cnt.id}".owner;
      vpnOwner = d.semanticEntries."${vpn.role}/${vpn.id}".owner;
      writer = d.canonicalPlanWriter; physicalPolicyWriter = d.physicalPolicyWriter;
      runtimeInstalled = d.runtimeInstalled; };
    expected = { errors = []; owner = "browser-ux"; vpnOwner = "core";
      writer = "core:modules/browsers.nix"; physicalPolicyWriter = null; runtimeInstalled = false; };
  };
  testSemanticDuplicatesOwnersEffectsAndPriorityAreRejected = {
    expr = map (rows: rejects { programs.browserBaseline.extensionContributions = rows; }) [
      [vpn] [(cnt // { owner = "core"; })]
      [(vpn // { role = "floorp"; id = "new@fixture.invalid"; })]
      [(cnt // { id = vpn.id; })]
      [(cnt // { automaticInstallation = true; })]
      [(cnt // { automaticSignIn = true; })] [(cnt // { autoConnect = true; })]
      [(cnt // { policy.ExtensionSettings = {}; })]
      (lib.mkForce [])
    ];
    expected = lib.replicate 9 true;
  };
  testHeldRolesHaveNoInstallerProcedure = {
    expr = let plan = builtins.fromJSON bb.extensionDeliveryR3R3.attendedPlan.text;
      held = map (role: plan."${role}/${vpn.id}") ["brave-youtube" "orion"];
    in map (row: row.method == row.status && !(row ? procedure)) held;
    expected = [true true];
  };
  testHistoricalR3R2IsExplicitlySuperseded = {
    expr = { current = bb.protonVpnR3R2.current or true; by = bb.protonVpnR3R2.supersededBy or null;
      oldConflictsAbsent = !(bb.protonVpnR3R2 ? heldBrowserOwnerConflicts); };
    expected = { current = false; by = "protonVpnR3R3"; oldConflictsAbsent = true; };
  };
  testYoutubeWrapperConvenienceHasOneCoreOwner = {
    expr = let row = cnt // { role = "brave-youtube"; owner = "core"; };
      delivery = bb.extensionDeliveryR3R3;
    in { accepted = (ownership.merge [row]).errors == [];
      otherOwnerRejected = (ownership.merge [ (row // { owner = "browser-ux"; }) ]).errors != [];
      sourceOwned = (delivery.semanticEntries."brave-youtube/${cnt.id}".owner or null) == "core"; };
    expected = { accepted = true; otherOwnerRejected = true; sourceOwned = true; };
  };
  testPolicyMergePreservesDisjointExistingLeaves = {
    expr = let merged = ownership.preservingPolicyMerge policy additions;
    in { forced = merged.ExtensionSettings.${vpn.id}; telemetry = merged.DisableTelemetry;
      added = merged.ExtensionSettings.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; };
    expected = { forced.installation_mode = "force_installed"; telemetry = true;
      added.installation_mode = "normal_installed"; };
  };
  testPolicyMergeRejectsCollisionsAndUnboundedSelectors = {
    expr = map (existing: !(builtins.tryEval (ownership.preservingPolicyMerge existing additions)).success) [
      { ExtensionSettings = additions; }
      { ExtensionInstallForcelist = ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa;https://example.invalid/update"]; }
      { Extensions.Locked = ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]; }
      { Extensions.Install = ["https://example.invalid/moving.xpi"]; }
      { ExtensionSettings."${vpn.id},aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" = {}; }
      { ExtensionSettings."update_url:https://clients2.google.com/service/update2/crx" = {}; }
      { ExtensionSettings."*" = {}; }
    ];
    expected = lib.replicate 7 true;
  };
  testPolicyMergeRejectsNonIdAdditions = {
    expr = map (key: !(builtins.tryEval (ownership.preservingPolicyMerge {} { ${key} = {}; })).success)
      ["*" "${vpn.id},aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" "update_url:https://example.invalid" "invalid/path"];
    expected = lib.replicate 4 true;
  };
  testR3RetainsChromeStorageInputsAndUsesCaskWithCLI = {
    expr = let c = evaluateActive [];
      r = c.system.build.browserBaseline;
    in {
      policies = r.chromePreferences == import (repoRoot + "/config/browser/chrome-policy-legacy-defaults.nix");
      cnt = builtins.attrNames r.chromeExternalExtensions;
      nmh = builtins.attrNames r.chromeNativeMessagingHosts;
      destructiveTargets = builtins.filter (t: (t.action or "write") != "write") r.attendedTargets;
      packages = map (p: p.name) c.environment.systemPackages;
      casks = map (cask: cask.name) c.homebrew.casks;
      roles = map (role: role.id) r.rolesR3.roles;
      floorpHelperHeld = lib.hasInfix "exit 78" r.attendedApplyByBrowser.floorp.text;
      vivaldiHelperHeld = lib.hasInfix "exit 78" r.attendedApplyByBrowser.vivaldi.text;
      failures = builtins.length (builtins.filter (a: !a.assertion) c.assertions);
      preservesRecommended = !(lib.hasInfix ''rm "$recommendedChromePolicy"'' c.system.activationScripts.postActivation.text);
    };
    expected = {
      policies = true; cnt = ["felphkbfjadmcejnibcmcncimlappdde"];
      nmh = ["org.keepassxc.keepassxc_browser"]; destructiveTargets = [];
      packages = ["google-chrome" "brave" "browser-automation"]; casks = ["firefox" "brave-browser" "librewolf" "orion"];
      roles = ["brave-automation" "safari" "librewolf" "brave-daily" "orion" "floorp" "brave-youtube"];
      floorpHelperHeld = true; vivaldiHelperHeld = true; failures = 1; preservesRecommended = true;
    };
  };
  testR3CorePrivacyWritersRejectExternalDefinitions = {
    expr = map (domain: rejects {
      programs.browserBaseline.rolesR3 = true;
      system.defaults.CustomUserPreferences.${domain}.SomeKey = true;
    }) ["com.brave.Browser" "com.kagi.kagimacOS" "io.gitlab.librewolf-community"];
    expected = [true true true];
  };
  testR3CannotBypassTheDeploymentHoldByDisablingOwnership = {
    expr = let c = evaluate [{ programs.browserBaseline = { rolesR3 = true; corePolicyOwnership = false; }; }];
    in builtins.any (a: !a.assertion && lib.hasInfix "R3 roles require" a.message) c.assertions;
    expected = true;
  };
  testPredecessorKeepsLegacyPackagesAndPolicies = {
    expr = let c = evaluate [{ programs.browserBaseline = { rolesR3 = false; corePolicyOwnership = false; }; }];
      r = c.system.build.browserBaseline;
    in {
      packages = map (p: p.name) c.environment.systemPackages;
      casks = map (cask: cask.name) c.homebrew.casks;
      legacy = r.chromePreferences == import (repoRoot + "/config/browser/chrome-policy-legacy-defaults.nix");
      roles = r.rolesR3; held = r.deploymentHeld;
    };
    expected = { packages = ["google-chrome"]; casks = ["firefox"]; legacy = true; roles = null; held = false; };
  };
  testCoreOwnsExactChromePolicies = {
    expr = bb.chromePreferences;
    expected = import (repoRoot + "/config/browser/chrome-policy-legacy-defaults.nix");
  };
  testDefaultConsumerIsInactiveAndRejectsRetiredR2BSelection = {
    expr = {
      roles = (evaluate []).programs.browserBaseline.rolesR3;
      rejected = builtins.any (a: !a.assertion && lib.hasInfix "retired R2B" a.message)
        (evaluate [{ programs.browserBaseline.corePolicyOwnership = true; }]).assertions;
    };
    expected = { roles = false; rejected = true; };
  };
  testCoreOwnershipIndependentOfLegacyAndVivaldi = {
    expr = let other = evaluateActive [{
      programs.browserBaseline.legacyPolicyDefaults.enable = false;
      programs.browserBaseline.vivaldi.managedPreferences.SearchSuggestEnabled = true;
      programs.browserBaseline.floorp.policies.DisableTelemetry = true;
    }]; in {
      chrome = other.system.build.browserBaseline.chromePreferences == bb.chromePreferences;
      vivaldi = other.system.build.browserBaseline.vivaldiManagedPlist != null;
      floorp = other.system.build.browserBaseline.floorpPreferences.DisableTelemetry;
      accepted = other.system.build.browserBaseline.guardViolations == [];
    };
    expected = { chrome = true; vivaldi = true; floorp = true; accepted = true; };
  };
  testRetainedVivaldiKeepsLegacyPrimaryBrowserOutputs = {
    expr = let c = (evaluateActive [{ programs.browserBaseline.vivaldi.managedPreferences.SearchSuggestEnabled = true; }])
      .system.build.browserBaseline;
    in { accepted = c.guardViolations == []; vivaldi = c.vivaldiManagedPlist != null;
      chrome = c.chromeManagedPlist == bb.chromeManagedPlist;
      firefox = c.firefoxPolicies == bb.firefoxPolicies;
      extensions = c.chromeExternalExtensions == bb.chromeExternalExtensions;
      nativeHosts = c.chromeNativeMessagingHosts == bb.chromeNativeMessagingHosts;
      deploymentHeld = c.deploymentHeld; };
    expected = { accepted = true; vivaldi = true; chrome = true; firefox = true;
      extensions = true; nativeHosts = true; deploymentHeld = true; };
  };
  testReferenceExceptionDoesNotRelaxOtherLegacyContributions = {
    expr = {
      inactiveVivaldi = (evaluate [{ programs.browserBaseline.vivaldi.managedPreferences.SearchSuggestEnabled = true; }])
        .system.build.browserBaseline.guardViolations != [];
      activeFloorp = rejects { programs.browserBaseline.floorp.policies.DisableTelemetry = true; };
      activeChrome = rejects { programs.browserBaseline.chrome.managedPreferences.SearchSuggestEnabled = true; };
    };
    expected = { inactiveVivaldi = true; activeFloorp = true; activeChrome = true; };
  };
  testVivaldiOwnershipDoesNotRetractFloorp = {
    expr = let
      scoped = evaluateActive [{ programs.browserBaseline.vivaldiPolicyOwnership = true; }];
      legacy = evaluateActive [{ programs.browserBaseline.replacementPolicyOwnership = true; }];
      script = c: c.system.activationScripts.postActivation.text;
      removesVivaldi = c: lib.hasInfix ''rm -f "/Library/Managed Preferences/com.vivaldi.Vivaldi.plist"'' (script c);
      removesFloorp = c: lib.hasInfix ''rm -f "/Library/Preferences/app.floorp.Floorp.plist"'' (script c);
    in {
      vivaldiOwned = removesVivaldi scoped;
      floorpPreserved = !removesFloorp scoped;
      legacyVivaldiUnchanged = removesVivaldi legacy;
      legacyFloorpUnchanged = removesFloorp legacy;
      deploymentHeld = scoped.system.build.browserBaseline.deploymentHeld;
    };
    expected = { vivaldiOwned = true; floorpPreserved = true;
      legacyVivaldiUnchanged = true; legacyFloorpUnchanged = true; deploymentHeld = true; };
  };
  testVivaldiReferenceRetainsSeamSafetyGuards = {
    expr = {
      priority = rejects { programs.browserBaseline.vivaldi.managedPreferences = lib.mkForce {}; };
      invalidId = rejects { programs.browserBaseline.vivaldi.externalExtensions."invalid/id" = {}; };
      duplicate = (evaluateActive [
        { _file = "reference-owner-a"; programs.browserBaseline.vivaldi.managedPreferences.SearchSuggestEnabled = true; }
        { _file = "reference-owner-b"; programs.browserBaseline.vivaldi.managedPreferences.SearchSuggestEnabled = true; }
      ]).system.build.browserBaseline.guardViolations != [];
    };
    expected = { priority = true; invalidId = true; duplicate = true; };
  };
  testInjectedBrowserUxDefinitionsRejected = {
    expr = {
      chrome = rejects { _file = "browser-ux/modules/default.nix"; programs.browserBaseline.chrome.managedPreferences.SearchSuggestEnabled = false; };
      noncolliding = rejects { programs.browserBaseline.chrome.managedPreferences.HomepageLocation = "https://example.invalid"; };
      extension = rejects { programs.browserBaseline.chrome.externalExtensions.ddkjiahejlhfcafbddmgiahcphecmpfh.external_update_url = "https://clients2.google.com/service/update2/crx"; };
      safari = rejects { programs.browserBaseline.safari.managedPreferences.SomeUndocumentedKey = true; };
      wipr = rejects { programs.browserBaseline.safari.requiredExtensions.Wipr = true; };
      customDefaults = rejects { system.defaults.CustomUserPreferences."com.apple.Safari".SomeUndocumentedKey = true; };
      secondWriter = rejects { _file = "browser-ux/modules/writer.nix"; system.activationScripts.postActivation.text = ''install -m 0644 fake /Library/Managed\ Preferences/com.google.Chrome.plist''; };
      chromeRetirement = rejects { programs.browserBaseline.chrome.enable = false; };
      firefoxRetirement = rejects { programs.browserBaseline.firefox.enable = false; };
      override = rejects { programs.browserBaseline.corePolicyOwnership = lib.mkForce false; };
      braveCask = rejects { homebrew.casks = ["brave-browser"]; };
    };
    expected = {
      chrome = true; noncolliding = true; extension = true; safari = true; wipr = true;
      customDefaults = true; secondWriter = true; chromeRetirement = true; firefoxRetirement = true; override = true; braveCask = true;
    };
  };
  testOneChromeWriterAndNoSafariPreferenceWriter = {
    expr = {
      owner = bb.policyDefinitionOwner;
      chromeTargets = targetCount "/Library/Managed Preferences/com.google.Chrome.plist";
      safariPlist = bb.safariManagedPlist;
      externalManifests = bb.chromeExternalExtensions;
      nativeHosts = bb.chromeNativeMessagingHosts;
      render = bb.chromeManagedPlist.text == lib.generators.toPlist { escape = true; } bb.chromePreferences;
      packages = map (p: p.name) base.environment.systemPackages;
      casks = map (cask: cask.name) base.homebrew.casks;
      firefoxPreserved = bb.firefoxPolicies == repoRoot + "/config/browser/firefox-policies.json";
    };
    expected = {
      owner = "core"; chromeTargets = 1; safariPlist = null;
      externalManifests = { "felphkbfjadmcejnibcmcncimlappdde" = repoRoot + "/config/browser/chrome-clear-new-tab-external-extension.json"; };
      nativeHosts = { "org.keepassxc.keepassxc_browser" = repoRoot + "/config/browser/keepassxc-chrome-native-messaging.json"; };
      render = true; packages = ["google-chrome" "brave" "browser-automation"]; casks = ["firefox" "brave-browser" "librewolf" "orion"]; firefoxPreserved = true;
    };
  };
  testSafariDuckDuckGoAndFiveWiprComponentsAreIntent = {
    expr = {
      delivery = bb.safariIntent.delivery;
      search = bb.safariIntent.searchEngine;
      privateSearch = bb.safariIntent.privateSearchEngine;
      wipr = bb.safariIntent.wipr.extensionNames;
      purchase = bb.safariIntent.wipr.acquisition;
      policies = bb.safariIntent.managedPreferences;
      held = bb.deploymentHeld;
      extra = bb.safariIntent.wipr.desiredState."Wipr Extra";
    };
    expected = {
      delivery = "attended-ui-intent"; search = "DuckDuckGo"; privateSearch = "DuckDuckGo";
      wipr = ["Blocklist 1" "Blocklist 2" "Blocklist 3" "Blocklist 4" "Wipr Extra"];
      purchase = "preserve-existing-purchased-app"; policies = {};
      held = true; extra = { enabled = true; privateBrowsing = true; otherWebsites = "Allow"; };
    };
  };
  testStockSelectionDeclaresSafariDesiredStateWithoutWriter = {
    expr = let
      c = evaluate [{ programs.browserBaseline.stockApplications = true; }];
      inactive = evaluate [];
      r = c.system.build.browserBaseline;
      desired = r.safariIntent.wipr.desiredState;
    in {
      components = lib.mapAttrs (_: row: row.enabled) desired;
      namesMatch = lib.sort builtins.lessThan r.safariIntent.wipr.extensionNames == builtins.attrNames desired;
      extra = desired."Wipr Extra";
      enforcement = r.safariIntent.wipr.enforcement;
      unattended = r.safariIntent.wipr.unattendedSupport;
      search = r.safariIntent.searchEngine;
      managedPreferences = r.safariIntent.managedPreferences;
      plist = r.safariManagedPlist;
      ownership = c.programs.browserBaseline.corePolicyOwnership;
      roles = c.programs.browserBaseline.rolesR3;
      held = r.deploymentHeld;
      activationUnchanged = c.system.activationScripts == inactive.system.activationScripts;
      attendedUnchanged = r.attendedTargets == inactive.system.build.browserBaseline.attendedTargets
        && r.attendedApply.text == inactive.system.build.browserBaseline.attendedApply.text;
      violations = r.guardViolations;
      inactiveIntent = inactive.system.build.browserBaseline.safariIntent;
      disabledIntent = (evaluate [{ programs.browserBaseline.stockApplications = false; }]).system.build.browserBaseline.safariIntent;
    };
    expected = {
      components = { "Blocklist 1" = true; "Blocklist 2" = true; "Blocklist 3" = true; "Blocklist 4" = true; "Wipr Extra" = true; };
      namesMatch = true;
      extra = { enabled = true; privateBrowsing = true; otherWebsites = "Allow"; };
      enforcement = "attended-ui-readback-only"; unattended = "UNSUPPORTED_WITHOUT_SUPERVISED_MDM";
      search = "DuckDuckGo"; managedPreferences = {}; plist = null;
      ownership = false; roles = false; held = false;
      activationUnchanged = true; attendedUnchanged = true; violations = [];
      inactiveIntent = null; disabledIntent = null;
    };
  };
  testFrameworkAggregationIsNotAnIndependentWriter = {
    expr = let e = evaluateActive [ ({ config, ... }: {
      # Declaration provenance is authoritative even when modulesPath differs.
      _file = "/fixture-declaration/system/activation-scripts.nix";
      system.activationScripts.script.text = config.system.activationScripts.postActivation.text;
    }) ]; in {
      violations = e.system.build.browserBaseline.guardViolations;
      hasChrome = e.system.build.browserBaseline.chromeManagedPlist != null;
      externalAggregateRejected = rejects {
        _file = "browser-ux/modules/aggregate.nix";
        system.activationScripts.script.text = "install fake /Library/ManagedPreferences/com.google.Chrome.plist";
      };
    };
    expected = { violations = []; hasChrome = true; externalAggregateRejected = true; };
  };
  testInactiveWriterWrappersAreNotForced = {
    expr = let e = evaluateActive [{
      _file = "browser-ux/modules/inactive.nix";
      system.activationScripts.extra.text = lib.mkIf false (throw "inactive text forced");
    }]; in e.system.build.browserBaseline.guardViolations;
    expected = [];
  };
  testBareDomainsAndAdditionalActivationWritersRejected = {
    expr = map (text: rejects {
      _file = "browser-ux/modules/writer.nix";
      system.activationScripts.extra.text = lib.mkAfter text;
    }) [
      "defaults write '/Library/Managed Preferences/com.google.Chrome' SearchSuggestEnabled -bool false"
      "defaults write com.apple.Safari SomeKey -bool true"
      "plutil -replace SearchSuggestEnabled -bool false '/Library/Managed Preferences/com.google.Chrome.plist'"
      "PlistBuddy -c 'Set :SomeKey true' '/Library/Preferences/com.apple.Safari.plist'"
      "mv fake '/Library/Managed Preferences/com.google.Chrome.plist'"
      "tee '/Library/Managed Preferences/com.google.Chrome.plist' < fake"
      "ditto fake '/Library/Preferences/com.apple.Safari.plist'"
    ];
    expected = [ true true true true true true true ];
  };
  testDeploymentHeldBeforeAnyBrowserEffect = {
    expr = {
      held = bb.deploymentHeld or false;
      failedAssertions = builtins.length (builtins.filter (a: !a.assertion) base.assertions);
      chromeHelper = lib.hasInfix "exit 78" bb.attendedApplyByBrowser.chrome.text;
      aggregateHelper = lib.hasInfix "exit 78" bb.attendedApply.text;
      activation = lib.hasInfix "exit 78" base.system.activationScripts.postActivation.text;
      vivaldiHelperAvailable = !(lib.hasInfix "exit 78" bb.attendedApplyByBrowser.vivaldi.text);
      cntDependency = lib.hasInfix "Clear New Tab" (bb.deploymentHoldMessage or "");
    };
    expected = {
      held = true; failedAssertions = 1; chromeHelper = true;
      aggregateHelper = true; activation = true;
      vivaldiHelperAvailable = false; cntDependency = true;
    };
  };
}
