# nix-darwin-test-source-target: modules/browsers.nix
# nix-darwin-test-source-target: packages/brave-signed-dmg/default.nix
{ repoRoot, system }:

let
  flake = builtins.getFlake "path:${toString repoRoot}";
  pkgs = flake.legacyPackages.${system};
  lib = pkgs.lib;
  config = flake.darwinConfigurations.example-primary.config;
  laptopConfig = flake.darwinConfigurations.example-workstation.config;

  packageNames = cfg: builtins.map (p: p.pname or p.name or "") cfg.environment.systemPackages;
  caskNames = cfg: builtins.map (c: c.name or c) cfg.homebrew.casks;
  activationText = cfg: cfg.system.activationScripts.postActivation.text;

  countOccurrences = needle: haystack:
    builtins.length (lib.splitString needle haystack) - 1;
in
{
  testStockBraveUsesStableCaskAndCliAdapterWhileYoutubeRetainsSignedDmgOnBothHosts = {
    expr = map (hostName: let
      host = flake.darwinConfigurations.${hostName};
      cfg = host.config;
      brave = host.pkgs.brave;
      stock = builtins.filter (p: (p.pname or p.name or "") == "brave") cfg.environment.systemPackages;
      cli = if stock != [] then builtins.head stock else null;
      casks = caskNames cfg;
      nixAppDir = "${cfg.system.build.applications}/Applications";
      nixApps = builtins.attrNames (builtins.readDir nixAppDir);
    in {
      inherit hostName;
      stableCaskCount = builtins.length (builtins.filter (c: c == "brave-browser") casks);
      duplicateCask = builtins.any
        (name: builtins.elem name [ "brave-browser@beta" "brave-browser@nightly" ])
        casks;
      cliPresent = cli != null;
      cliDistinctDrv = if cli != null then cli.drvPath != brave.drvPath else false;
      # CLI adapter tested via observable bin output; preserve string context for builder inputs.
      cliBinOutput = if cli != null
        then lib.hasInfix "exec '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser' \"$@\""
          (builtins.readFile "${cli}/bin/brave")
        else false;
      noNixBraveApp = !(builtins.elem "Brave Browser.app" nixApps);
      # Drop context only from the comparison pattern, never from a builder input.
      youtubeUsesSealedPackage = lib.hasInfix
        (builtins.unsafeDiscardStringContext ''"${brave}/Applications/Brave Browser.app"'')
        (activationText cfg);
      signedDmg = lib.hasSuffix ".dmg" brave.src.url && brave.dontStrip && brave.dontFixup;
    }) (builtins.attrNames flake.darwinConfigurations);
    expected = lib.sort (a: b: a.hostName < b.hostName) [
      {
        hostName = "example-workstation";
        stableCaskCount = 0;
        duplicateCask = false;
        cliPresent = false;
        cliDistinctDrv = false;
        cliBinOutput = false;
        noNixBraveApp = true;
        youtubeUsesSealedPackage = true;
        signedDmg = true;
      }
      {
        hostName = "example-primary";
        stableCaskCount = 1;
        duplicateCask = false;
        cliPresent = true;
        cliDistinctDrv = true;
        cliBinOutput = true;
        noNixBraveApp = true;
        youtubeUsesSealedPackage = true;
        signedDmg = true;
      }
    ];
  };

  testConstructorRejectsConflictingStockSelectionOnBothHosts = {
    expr = map (hostName: let
      altered = flake.lib.mkDarwinConfiguration {
        inherit hostName;
        extraModules = [{
          programs.browserBaseline.stockApplications = lib.mkForce (hostName != "example-primary");
        }];
      };
    in builtins.any (a: !a.assertion && lib.hasInfix "host constructor selects target stock applications" a.message)
      altered.config.assertions) [ "example-primary" "example-workstation" ];
    expected = [ true true ];
  };
  testConstructorSelectsAcquisitionWithMatchingCoreClaims = {
    expr = let
      ids = [ "bundle:com.brave.Browser" "bundle:io.gitlab.librewolf-community" "bundle:com.kagi.kagimacOS" ];
      targetClaims = flake.applicationClaimsFor { hostName = "example-primary"; inherit system; };
      predecessorClaims = flake.applicationClaimsFor { hostName = "example-workstation"; inherit system; };
    in {
      selected = config.programs.browserBaseline.stockApplications;
      predecessorSelected = laptopConfig.programs.browserBaseline.stockApplications;
      policy = config.programs.browserBaseline.corePolicyOwnership;
      roles = config.programs.browserBaseline.rolesR3;
      release = flake.lib.browserRolesR3Selected;
      braveCount = builtins.length (builtins.filter (name: name == "brave") (packageNames config));
      newCasks = builtins.filter (name: builtins.elem name [ "librewolf" "orion" "brave-browser" ]) (caskNames config);
      claimCounts = map (id: builtins.length (builtins.filter (claim: claim.id == id) targetClaims)) ids;
      coreOwners = map (claim: claim.owner)
        (builtins.filter (entry: builtins.elem entry.claim.id ids) (flake.lib.applicationClaims.attachOwner "core" targetClaims));
      claimsAgree = targetClaims == flake.applicationClaims;
      predecessorAcquisitions = builtins.filter (claim: builtins.elem claim.id ids) predecessorClaims;
      predecessorClaimCount = builtins.length predecessorClaims;
      automation = config.system.build.browserBaseline.automationBrowser;
      plan = config.system.build.browserBaseline.extensionDeliveryR3R3;
    };
    expected = {
      selected = true; predecessorSelected = false; policy = false; roles = false; release = false;
      braveCount = 1; newCasks = [ "brave-browser" "librewolf" "orion" ]; claimCounts = [ 1 1 1 ];
      coreOwners = [ "core" "core" "core" ]; claimsAgree = true; predecessorAcquisitions = [];
      predecessorClaimCount = 18; automation = null; plan = null;
    };
  };
  testChromeRemainsInCoreSystemPackages = {
    expr = {
      macBookPro = builtins.elem "google-chrome" (packageNames config);
      laptop = builtins.elem "google-chrome" (packageNames laptopConfig);
    };
    expected = {
      macBookPro = true;
      laptop = true;
    };
  };

  testFirefoxRemainsInCoreHomebrewCasks = {
    expr = {
      macBookPro = builtins.elem "firefox" (caskNames config);
      laptop = builtins.elem "firefox" (caskNames laptopConfig);
    };
    expected = {
      macBookPro = true;
      laptop = true;
    };
  };

  testCurrentHostActivationHasExactlyOneBrowserBaselineWriter = {
    expr = {
      macBookProWriterCount = countOccurrences "setting up browser policies..." (activationText config);
      laptopWriterCount = countOccurrences "setting up browser policies..." (activationText laptopConfig);
      macBookProChromeInstallCount = countOccurrences ''install -m 0644 "'' (activationText config);
      laptopChromeInstallCount = countOccurrences ''install -m 0644 "'' (activationText laptopConfig);
      macBookProSymlinkCount = countOccurrences ''ln -sfn "'' (activationText config);
      laptopSymlinkCount = countOccurrences ''ln -sfn "'' (activationText laptopConfig);
    };
    expected = {
      macBookProWriterCount = 1;
      laptopWriterCount = 1;
      macBookProChromeInstallCount = 3; # Preserved Chrome plist, CNT and Firefox policies
      laptopChromeInstallCount = 3;
      macBookProSymlinkCount = 2; # Preserved Chrome and Firefox NMH
      laptopSymlinkCount = 2;
    };
  };

  testGeneratedRenderArtifactsUseCoreChromeAndUnchangedFirefox = {
    expr =
      let
        bb = config.system.build.browserBaseline;
        laptopBb = laptopConfig.system.build.browserBaseline;
      in {
        mbpViolations = bb.guardViolations;
        mbpHasPlist = bb.chromeManagedPlist != null;
        mbpHasFirefox = bb.firefoxPolicies != null;
        mbpHasExt = bb.chromeExternalExtensions ? "felphkbfjadmcejnibcmcncimlappdde";
        mbpHasChromeNMH = bb.chromeNativeMessagingHosts ? "org.keepassxc.keepassxc_browser";
        mbpHasFirefoxNMH = bb.firefoxNativeMessagingHosts ? "org.keepassxc.keepassxc_browser";
        laptopViolations = laptopBb.guardViolations;
        laptopHasPlist = laptopBb.chromeManagedPlist != null;
        laptopHasFirefox = laptopBb.firefoxPolicies != null;
      };
    expected = {
      mbpViolations = [];
      mbpHasPlist = true;
      mbpHasFirefox = true;
      mbpHasExt = true;
      mbpHasChromeNMH = true;
      mbpHasFirefoxNMH = true;
      laptopViolations = [];
      laptopHasPlist = true;
      laptopHasFirefox = true;
    };
  };

  testNoDestinationRootWriterModuleRequired = {
    expr = {
      legacyDefaultsEnabled = config.programs.browserBaseline.legacyPolicyDefaults.enable;
      violations = config.system.build.browserBaseline.guardViolations;
      writerActive = countOccurrences "setting up browser policies..." (activationText config) == 1;
    };
    expected = {
      legacyDefaultsEnabled = true;
      violations = [];
      writerActive = true;
    };
  };

  testInactiveCoreBrowserApplicationClaimsRetainChromeFirefox = {
    expr =
      let
        claims = flake.applicationClaims;
        findClaim = id: lib.findFirst (c: c.id == id) null claims;
        chromeClaim = findClaim "bundle:com.google.Chrome";
        firefoxClaim = findClaim "bundle:org.mozilla.firefox";
        hasBrowserUxClaim = builtins.any (c:
          lib.hasInfix "browser-ux" c.id || lib.hasInfix "browser-ux" (c.provider or "")
        ) claims;
      in {
        chrome = {
          id = chromeClaim.id;
          provider = chromeClaim.provider;
          package = chromeClaim.package;
          artifact = chromeClaim.artifact;
        };
        firefox = {
          id = firefoxClaim.id;
          provider = firefoxClaim.provider;
          package = firefoxClaim.package;
          artifact = firefoxClaim.artifact;
        };
        browserClaimCount = builtins.length (builtins.filter (c:
          builtins.elem c.id [ "bundle:com.google.Chrome" "bundle:org.mozilla.firefox" "bundle:com.operator.brave-youtube" ]
        ) claims);
        claimCount = builtins.length claims;
        hasBrowserUxClaim = hasBrowserUxClaim;
      };
    expected = {
      chrome = {
        id = "bundle:com.google.Chrome";
        provider = "nix";
        package = "google-chrome";
        artifact = "Google Chrome.app";
      };
      firefox = {
        id = "bundle:org.mozilla.firefox";
        provider = "homebrew-cask";
        package = "firefox";
        artifact = "Firefox.app";
      };
      browserClaimCount = 3;
      claimCount = 24;
      hasBrowserUxClaim = false;
    };
  };

  testCurrentHostDefaultsToBundleModeAndRemovesPreferencesPlist = {
    expr =
      let
        mbpBb = config.system.build.browserBaseline;
        laptopBb = laptopConfig.system.build.browserBaseline;
        mbpAct = activationText config;
        laptopAct = activationText laptopConfig;
      in {
        mbpDelivery = mbpBb.firefoxPolicyDelivery;
        laptopDelivery = laptopBb.firefoxPolicyDelivery;
        mbpPlistNull = mbpBb.firefoxManagedPlist == null;
        laptopPlistNull = laptopBb.firefoxManagedPlist == null;
        mbpHasFirefoxPolicies = mbpBb.effectiveState.hasFirefoxPolicies;
        mbpHasFirefoxPlist = mbpBb.effectiveState.hasFirefoxPlist;
        mbpRemovesPlist = lib.hasInfix ''rm -f "/Library/Preferences/org.mozilla.firefox.plist"'' mbpAct;
        laptopRemovesPlist = lib.hasInfix ''rm -f "/Library/Preferences/org.mozilla.firefox.plist"'' laptopAct;
        mbpNoPlistInstall = ! lib.hasInfix ''org.mozilla.firefox.plist.tmp'' mbpAct;
        laptopNoPlistInstall = ! lib.hasInfix ''org.mozilla.firefox.plist.tmp'' laptopAct;
      };
    expected = {
      mbpDelivery = "bundle";
      laptopDelivery = "bundle";
      mbpPlistNull = true;
      laptopPlistNull = true;
      mbpHasFirefoxPolicies = true;
      mbpHasFirefoxPlist = false;
      mbpRemovesPlist = true;
      laptopRemovesPlist = true;
      mbpNoPlistInstall = true;
      laptopNoPlistInstall = true;
    };
  };
  testInactiveR3DoesNotFreezeEitherHost = {
    expr = {
      target = {
        brave = builtins.elem "brave" (packageNames config);
        librewolf = builtins.elem "librewolf" (caskNames config);
        orion = builtins.elem "orion" (caskNames config);
        held = config.system.build.browserBaseline.deploymentHeld;
      };
      predecessor = {
        brave = builtins.elem "brave" (packageNames laptopConfig);
        librewolf = builtins.elem "librewolf" (caskNames laptopConfig);
        orion = builtins.elem "orion" (caskNames laptopConfig);
        held = laptopConfig.system.build.browserBaseline.deploymentHeld;
      };
    };
    expected = {
      target = { brave = true; librewolf = true; orion = true; held = false; };
      predecessor = { brave = false; librewolf = false; orion = false; held = false; };
    };
  };
}
