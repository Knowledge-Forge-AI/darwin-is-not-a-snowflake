# nix-darwin-test-source-target: modules/browsers.nix
# nix-darwin-test-source-target: config/browser/chrome-policy-legacy-defaults.nix
{ repoRoot, system }:

let
  flake = builtins.getFlake "path:${toString repoRoot}";
  pkgs = flake.legacyPackages.${system};
  lib = pkgs.lib;
  browsersModule = import (repoRoot + "/modules/browsers.nix");

  stubModule = {
    options.assertions = lib.mkOption {
      type = lib.types.listOf lib.types.anything;
      default = [];
    };
    options.environment.systemPackages = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = [];
    };
    options.homebrew.casks = lib.mkOption {
      type = lib.types.listOf lib.types.anything;
      default = [];
    };
    options.system.build = lib.mkOption {
      type = lib.types.attrsOf lib.types.anything;
      default = {};
    };
    options.system.activationScripts = lib.mkOption {
      type = lib.types.attrsOf (lib.types.submodule {
        options.text = lib.mkOption {
          type = lib.types.lines;
          default = "";
        };
      });
      default = {};
    };
  };

  evalSeam = extraModules: lib.evalModules {
    specialArgs = { inherit pkgs; };
    # Generic-seam compatibility lane. Core ownership has its own fixture.
    modules = [ browsersModule stubModule { programs.browserBaseline.corePolicyOwnership = false; } ] ++ extraModules;
  };
in
{
  testLegacyDefaultsEnabledByDefault = {
    expr = (evalSeam []).config.programs.browserBaseline.legacyPolicyDefaults.enable;
    expected = true;
  };

  testLegacySeamEvaluatesSuccessfully = {
    expr =
      let
        eval = evalSeam [];
        bb = eval.config.system.build.browserBaseline;
      in {
        violations = bb.guardViolations;
        hasPlist = bb.chromeManagedPlist != null;
        hasFirefox = bb.firefoxPolicies != null;
        hasExt = bb.chromeExternalExtensions ? "felphkbfjadmcejnibcmcncimlappdde";
        hasChromeNMH = bb.chromeNativeMessagingHosts ? "org.keepassxc.keepassxc_browser";
        hasFirefoxNMH = bb.firefoxNativeMessagingHosts ? "org.keepassxc.keepassxc_browser";
      };
    expected = {
      violations = [];
      hasPlist = true;
      hasFirefox = true;
      hasExt = true;
      hasChromeNMH = true;
      hasFirefoxNMH = true;
    };
  };

  testDifferentPolicyKeysCoexist = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
          }
          {
            programs.browserBaseline.chrome.managedPreferences.BlockThirdPartyCookies = true;
          }
          {
            programs.browserBaseline.chrome.managedPreferences.DefaultMediaStreamSetting = 3;
          }
        ];
      in eval.config.system.build.browserBaseline.guardViolations;
    expected = [];
  };

  testDuplicateSameKeyDefinitionsFail = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
          }
          {
            programs.browserBaseline.chrome.managedPreferences.BlockThirdPartyCookies = true;
          }
          {
            programs.browserBaseline.chrome.managedPreferences.BlockThirdPartyCookies = false;
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasDuplicate = builtins.elem "duplicate-key" (map (v: v.type) violations);
        key = (builtins.head violations).key or "";
      };
    expected = {
      hasDuplicate = true;
      key = "BlockThirdPartyCookies";
    };
  };

  testDuplicateIdenticalSameKeyDefinitionsFail = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
          }
          {
            programs.browserBaseline.chrome.managedPreferences.BlockThirdPartyCookies = true;
          }
          {
            programs.browserBaseline.chrome.managedPreferences.BlockThirdPartyCookies = true;
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasDuplicate = builtins.elem "duplicate-key" (map (v: v.type) violations);
        key = (builtins.head violations).key or "";
      };
    expected = {
      hasDuplicate = true;
      key = "BlockThirdPartyCookies";
    };
  };

  testMkForceFails = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
          }
          {
            programs.browserBaseline.chrome.managedPreferences = lib.mkForce {
              BlockThirdPartyCookies = true;
            };
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasPriority = builtins.elem "priority-override" (map (v: v.type) violations);
        prio = (builtins.head violations).priority or 0;
      };
    expected = {
      hasPriority = true;
      prio = 50;
    };
  };

  testMkOverrideFails = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
          }
          {
            programs.browserBaseline.chrome.managedPreferences = lib.mkOverride 50 {
              BlockThirdPartyCookies = true;
            };
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasPriority = builtins.elem "priority-override" (map (v: v.type) violations);
        prio = (builtins.head violations).priority or 0;
      };
    expected = {
      hasPriority = true;
      prio = 50;
    };
  };

  testMkDefaultFails = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
          }
          {
            programs.browserBaseline.chrome.managedPreferences = lib.mkDefault {
              BlockThirdPartyCookies = true;
            };
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasPriority = builtins.elem "priority-override" (map (v: v.type) violations);
        prio = (builtins.head violations).priority or 0;
      };
    expected = {
      hasPriority = true;
      prio = 1000;
    };
  };

  testMkOptionDefaultFails = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
          }
          {
            programs.browserBaseline.chrome.managedPreferences = lib.mkOptionDefault {
              BlockThirdPartyCookies = true;
            };
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasPriority = builtins.elem "priority-override" (map (v: v.type) violations);
        prio = (builtins.head violations).priority or 0;
      };
    expected = {
      hasPriority = true;
      prio = 1500;
    };
  };

  testNestedPerKeyOverrideFails = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
          }
          {
            programs.browserBaseline.chrome.managedPreferences.BlockThirdPartyCookies = lib.mkForce true;
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasPriority = builtins.elem "priority-override" (map (v: v.type) violations);
        key = (builtins.head violations).key or "";
      };
    expected = {
      hasPriority = true;
      key = "BlockThirdPartyCookies";
    };
  };

  testLegacyEnabledPlusExternalContributionFails = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = true;
          }
          {
            programs.browserBaseline.chrome.managedPreferences.BlockThirdPartyCookies = true;
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasExclusivity = builtins.elem "legacy-exclusivity" (map (v: v.type) violations);
      };
    expected = {
      hasExclusivity = true;
    };
  };

  testLegacyDisabledPlusValidExternalContributionSucceeds = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.chrome.managedPreferences = {
              BlockThirdPartyCookies = true;
              DefaultGeolocationSetting = 3;
            };
            programs.browserBaseline.chrome.externalExtensions = {
              "felphkbfjadmcejnibcmcncimlappdde" = {
                external_update_url = "https://clients2.google.com/service/update2/crx";
              };
            };
            programs.browserBaseline.chrome.nativeMessagingHosts = {
              "org.keepassxc.keepassxc_browser" = repoRoot + "/config/browser/keepassxc-chrome-native-messaging.json";
            };
            programs.browserBaseline.firefox.policies = {
              DisableTelemetry = true;
            };
            programs.browserBaseline.firefox.nativeMessagingHosts = {
              "org.keepassxc.keepassxc_browser" = repoRoot + "/config/browser/keepassxc-firefox-native-messaging.json";
            };
          }
        ];
      in eval.config.system.build.browserBaseline.guardViolations;
    expected = [];
  };

  testEmptyContributionYieldsEmptyRenderAndCleanupIntent = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
          }
        ];
        bb = eval.config.system.build.browserBaseline;
      in {
        violations = bb.guardViolations;
        plist = bb.chromeManagedPlist;
        ff = bb.firefoxPolicies;
        exts = bb.chromeExternalExtensions;
        chromeNMH = bb.chromeNativeMessagingHosts;
        firefoxNMH = bb.firefoxNativeMessagingHosts;
      };
    expected = {
      violations = [];
      plist = null;
      ff = null;
      exts = {};
      chromeNMH = {};
      firefoxNMH = {};
    };
  };

  testNoBrowserUxCheckoutRequired = {
    expr =
      let
        eval = evalSeam [];
        bb = eval.config.system.build.browserBaseline;
      in {
        violations = bb.guardViolations;
        legacyDefaultsEnabled = eval.config.programs.browserBaseline.legacyPolicyDefaults.enable;
        isPurelyCoreSeam = bb.effectiveState.hasChromePreferences && bb.effectiveState.hasFirefoxPolicies;
      };
    expected = {
      violations = [];
      legacyDefaultsEnabled = true;
      isPurelyCoreSeam = true;
    };
  };

  testLegacyActivationContainsInstallAndLinkActions = {
    expr =
      let
        eval = evalSeam [];
        text = eval.config.system.activationScripts.postActivation.text;
      in {
        installsChromePlist = lib.hasInfix ''install -m 0644 "'' text && lib.hasInfix ''/Library/Managed Preferences/com.google.Chrome.plist"'' text;
        installsChromeExtension = lib.hasInfix ''install -m 0644 "'' text && lib.hasInfix ''felphkbfjadmcejnibcmcncimlappdde.json"'' text;
        linksChromeNMH = lib.hasInfix ''ln -sfn "'' text && lib.hasInfix ''/Library/Google/Chrome/NativeMessagingHosts/org.keepassxc.keepassxc_browser.json"'' text;
        linksFirefoxNMH = lib.hasInfix ''ln -sfn "'' text && lib.hasInfix ''/Library/Application Support/Mozilla/NativeMessagingHosts/org.keepassxc.keepassxc_browser.json"'' text;
        installsFirefoxPolicies = lib.hasInfix ''install -m 0644 "'' text && lib.hasInfix ''$firefoxPolicyDir/policies.json"'' text;
        warnsMissingFirefox = lib.hasInfix ''echo "WARNING: [browserBaseline] Firefox policy is configured but /Applications/Firefox.app was not found'' text;
      };
    expected = {
      installsChromePlist = true;
      installsChromeExtension = true;
      linksChromeNMH = true;
      linksFirefoxNMH = true;
      installsFirefoxPolicies = true;
      warnsMissingFirefox = true;
    };
  };

  testEmptyContributionActivationContainsCleanupActions = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
          }
        ];
        text = eval.config.system.activationScripts.postActivation.text;
      in {
        removesChromePlist = lib.hasInfix ''rm -f "/Library/Managed Preferences/com.google.Chrome.plist"'' text;
        removesChromeExtension = lib.hasInfix ''rm -f "$chromeExternalExtensionsDir/felphkbfjadmcejnibcmcncimlappdde.json"'' text;
        hasChromeNMHReadlinkGuard = lib.hasInfix ''/nix/store/*|*/config/browser/keepassxc-*)'' text
          && lib.hasInfix ''rm -f "$chromeNMH"'' text;
        hasFirefoxNMHReadlinkGuard = lib.hasInfix ''/nix/store/*|*/config/browser/keepassxc-*)'' text
          && lib.hasInfix ''rm -f "$mozillaNMH"'' text;
        removesFirefoxPolicies = lib.hasInfix ''rm -f "$firefoxPolicyDir/policies.json"'' text;
        noInstallPolicyCommands = ! lib.hasInfix ''install -m 0644'' text;
        noSymlinkCommands = ! lib.hasInfix ''ln -sfn'' text;
      };
    expected = {
      removesChromePlist = true;
      removesChromeExtension = true;
      hasChromeNMHReadlinkGuard = true;
      hasFirefoxNMHReadlinkGuard = true;
      removesFirefoxPolicies = true;
      noInstallPolicyCommands = true;
      noSymlinkCommands = true;
    };
  };

  testFirefoxBothStructuredAndPayloadFails = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policies = {
              DisableTelemetry = true;
            };
            programs.browserBaseline.firefox.policiesJsonPayload = repoRoot + "/config/browser/firefox-policies.json";
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasConflict = builtins.elem "conflicting-definitions" (map (v: v.type) violations);
      };
    expected = {
      hasConflict = true;
    };
  };

  testPriorityShadowingBoundary = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.chrome.managedPreferences = lib.mkDefault {
              BlockThirdPartyCookies = true;
            };
          }
          {
            programs.browserBaseline.chrome.managedPreferences = {
              DefaultMediaStreamSetting = 3;
            };
          }
        ];
      in {
        violations = eval.config.system.build.browserBaseline.guardViolations;
        prefs = eval.config.system.build.browserBaseline.effectiveState.chromePreferenceCount;
      };
    expected = {
      violations = [];
      prefs = 1;
    };
  };

  testOrderWrapperDoesNotBypassDuplicateDetection = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.chrome.managedPreferences.BlockThirdPartyCookies = lib.mkBefore true;
          }
          {
            programs.browserBaseline.chrome.managedPreferences.BlockThirdPartyCookies = false;
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasDuplicate = builtins.elem "duplicate-key" (map (v: v.type) violations);
        key = (builtins.head violations).key or "";
      };
    expected = {
      hasDuplicate = true;
      key = "BlockThirdPartyCookies";
    };
  };

  testLegitimatePriorityPolicyAttributeNotFlagged = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.chrome.managedPreferences.CustomPriorityPolicy = {
              priority = 1;
              level = "mandatory";
            };
          }
        ];
      in eval.config.system.build.browserBaseline.guardViolations;
    expected = [];
  };

  testInvalidExtensionIdFormatFails = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.chrome.externalExtensions."INVALID-ID-SHORT" = {
              external_update_url = "https://example.com/crx";
            };
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasInvalidIdentifier = builtins.elem "invalid-identifier" (map (v: v.type) violations);
      };
    expected = {
      hasInvalidIdentifier = true;
    };
  };

  testInvalidHostNameFormatFails = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.chrome.nativeMessagingHosts."../traversal/bad" = repoRoot + "/config/browser/keepassxc-chrome-native-messaging.json";
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasInvalidIdentifier = builtins.elem "invalid-identifier" (map (v: v.type) violations);
      };
    expected = {
      hasInvalidIdentifier = true;
    };
  };

  testEquivalenceOfGenericAndLegacyChromeRender = {
    expr =
      let
        legacyEval = evalSeam [];
        genericEval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.chrome.managedPreferences = import (repoRoot + "/config/browser/chrome-policy-legacy-defaults.nix");
          }
        ];
        legacyPlist = builtins.readFile legacyEval.config.system.build.browserBaseline.chromeManagedPlist;
        genericPlist = builtins.readFile genericEval.config.system.build.browserBaseline.chromeManagedPlist;
      in legacyPlist == genericPlist;
    expected = true;
  };

  testFirefoxByteParityWithPayloadRecord = {
    expr =
      let
        legacyEval = evalSeam [];
        genericEval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policiesJsonPayload = repoRoot + "/config/browser/firefox-policies.json";
          }
        ];
        legacyFF = builtins.readFile legacyEval.config.system.build.browserBaseline.firefoxPolicies;
        genericFF = builtins.readFile genericEval.config.system.build.browserBaseline.firefoxPolicies;
      in legacyFF == genericFF;
    expected = true;
  };

  testExternalExtensionsByteParityWithPayloadRecord = {
    expr =
      let
        legacyEval = evalSeam [];
        genericEval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.chrome.externalExtensions."felphkbfjadmcejnibcmcncimlappdde" =
              repoRoot + "/config/browser/chrome-clear-new-tab-external-extension.json";
          }
        ];
        legacyExt = builtins.readFile legacyEval.config.system.build.browserBaseline.chromeExternalExtensions."felphkbfjadmcejnibcmcncimlappdde";
        genericExt = builtins.readFile genericEval.config.system.build.browserBaseline.chromeExternalExtensions."felphkbfjadmcejnibcmcncimlappdde";
      in legacyExt == genericExt;
    expected = true;
  };

  testNativeMessagingByteParityWithPayloadRecord = {
    expr =
      let
        legacyEval = evalSeam [];
        genericEval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.chrome.nativeMessagingHosts."org.keepassxc.keepassxc_browser" =
              repoRoot + "/config/browser/keepassxc-chrome-native-messaging.json";
            programs.browserBaseline.firefox.nativeMessagingHosts."org.keepassxc.keepassxc_browser" =
              repoRoot + "/config/browser/keepassxc-firefox-native-messaging.json";
          }
        ];
        legacyChrome = builtins.readFile legacyEval.config.system.build.browserBaseline.chromeNativeMessagingHosts."org.keepassxc.keepassxc_browser";
        genericChrome = builtins.readFile genericEval.config.system.build.browserBaseline.chromeNativeMessagingHosts."org.keepassxc.keepassxc_browser";
        legacyFirefox = builtins.readFile legacyEval.config.system.build.browserBaseline.firefoxNativeMessagingHosts."org.keepassxc.keepassxc_browser";
        genericFirefox = builtins.readFile genericEval.config.system.build.browserBaseline.firefoxNativeMessagingHosts."org.keepassxc.keepassxc_browser";
      in legacyChrome == genericChrome && legacyFirefox == genericFirefox;
    expected = true;
  };

  testFirefoxPolicyDeliveryOptionDefaultsToBundle = {
    expr = (evalSeam []).config.programs.browserBaseline.firefox.policyDelivery;
    expected = "bundle";
  };

  testBux3aPayloadContributionResolvesToBundle = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policiesJsonPayload = repoRoot + "/config/browser/firefox-policies.json";
          }
        ];
        bb = eval.config.system.build.browserBaseline;
      in {
        delivery = bb.firefoxPolicyDelivery;
        hasPayloadFile = bb.firefoxPolicies != null;
        managedPlist = bb.firefoxManagedPlist;
        violations = bb.guardViolations;
      };
    expected = {
      delivery = "bundle";
      hasPayloadFile = true;
      managedPlist = null;
      violations = [];
    };
  };

  testBundleModeRenderIntentRemainsCurrentBehavior = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policiesJsonPayload = repoRoot + "/config/browser/firefox-policies.json";
          }
        ];
        bb = eval.config.system.build.browserBaseline;
        text = eval.config.system.activationScripts.postActivation.text;
      in {
        installsBundle = builtins.any (line: lib.hasInfix ''install -m 0644 "'' line && lib.hasInfix ''"$firefoxPolicyDir/policies.json"'' line) (lib.splitString "\n" text);
        removesPreferencesPlist = lib.hasInfix ''rm -f "/Library/Preferences/org.mozilla.firefox.plist"'' text;
        warnsMissingFirefox = lib.hasInfix ''echo "WARNING: [browserBaseline] Firefox policy is configured but /Applications/Firefox.app was not found'' text;
        noPlistInstall = ! lib.hasInfix ''/Library/Preferences/org.mozilla.firefox.plist.tmp'' text;
        firefoxPreferencesEmpty = bb.firefoxPreferences == {};
      };
    expected = {
      installsBundle = true;
      removesPreferencesPlist = true;
      warnsMissingFirefox = true;
      noPlistInstall = true;
      firefoxPreferencesEmpty = true;
    };
  };

  testMacosModeAcceptsStructuredFirefoxPolicies = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policyDelivery = "macos-preferences";
            programs.browserBaseline.firefox.policies = {
              DisableTelemetry = true;
              BlockAboutConfig = true;
            };
          }
        ];
      in eval.config.system.build.browserBaseline.guardViolations;
    expected = [];
  };

  testMacosModeGeneratesNonNullPlist = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policyDelivery = "macos-preferences";
            programs.browserBaseline.firefox.policies = {
              DisableTelemetry = true;
            };
          }
        ];
        bb = eval.config.system.build.browserBaseline;
      in {
        hasPlist = bb.firefoxManagedPlist != null;
        hasBundleFile = bb.firefoxPolicies != null;
      };
    expected = {
      hasPlist = true;
      hasBundleFile = false;
    };
  };

  testMacosModePlistIncludesEnterprisePoliciesEnabled = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policyDelivery = "macos-preferences";
            programs.browserBaseline.firefox.policies = {
              DisableTelemetry = true;
            };
          }
        ];
        bb = eval.config.system.build.browserBaseline;
        plistContent = builtins.readFile bb.firefoxManagedPlist;
      in {
        prefHasKey = bb.firefoxPreferences ? "EnterprisePoliciesEnabled";
        prefValue = bb.firefoxPreferences.EnterprisePoliciesEnabled;
        xmlHasKey = lib.hasInfix "<key>EnterprisePoliciesEnabled</key>" plistContent;
        xmlHasTrue = lib.hasInfix "<true/>" plistContent;
      };
    expected = {
      prefHasKey = true;
      prefValue = true;
      xmlHasKey = true;
      xmlHasTrue = true;
    };
  };

  testMacosModePlistContainsAllSuppliedKeysWithExactTypes = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policyDelivery = "macos-preferences";
            programs.browserBaseline.firefox.policies = {
              DisableTelemetry = true;
              MaxVersion = 100;
              OverrideFirstRunPage = "https://example.com";
              RequestedLocales = [ "en-US" "en-GB" ];
              ExtensionSettings = {
                "*" = {
                  installation_mode = "blocked";
                };
              };
            };
          }
        ];
        bb = eval.config.system.build.browserBaseline;
        prefs = bb.firefoxPreferences;
        plistXml = builtins.readFile bb.firefoxManagedPlist;
      in {
        enterprisePoliciesEnabledType = builtins.typeOf prefs.EnterprisePoliciesEnabled;
        disableTelemetryType = builtins.typeOf prefs.DisableTelemetry;
        maxVersionType = builtins.typeOf prefs.MaxVersion;
        overrideFirstRunPageType = builtins.typeOf prefs.OverrideFirstRunPage;
        requestedLocalesType = builtins.typeOf prefs.RequestedLocales;
        extensionSettingsType = builtins.typeOf prefs.ExtensionSettings;
        nestedSettingType = builtins.typeOf prefs.ExtensionSettings."*".installation_mode;
        plistHasBoolTrue = lib.hasInfix "<key>EnterprisePoliciesEnabled</key>\n\t<true/>" plistXml;
        plistHasInteger = lib.hasInfix "<key>MaxVersion</key>\n\t<integer>100</integer>" plistXml;
        plistHasString = lib.hasInfix "<key>OverrideFirstRunPage</key>\n\t<string>https://example.com</string>" plistXml;
        plistHasArray = lib.hasInfix "<key>RequestedLocales</key>\n\t<array>\n\t\t<string>en-US</string>\n\t\t<string>en-GB</string>\n\t</array>" plistXml;
        plistHasNestedDict = lib.hasInfix "<key>installation_mode</key>\n\t\t\t<string>blocked</string>" plistXml;
      };
    expected = {
      enterprisePoliciesEnabledType = "bool";
      disableTelemetryType = "bool";
      maxVersionType = "int";
      overrideFirstRunPageType = "string";
      requestedLocalesType = "list";
      extensionSettingsType = "set";
      nestedSettingType = "string";
      plistHasBoolTrue = true;
      plistHasInteger = true;
      plistHasString = true;
      plistHasArray = true;
      plistHasNestedDict = true;
    };
  };

  testMacosModeContainsInstallIntentForPreferencesPlist = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policyDelivery = "macos-preferences";
            programs.browserBaseline.firefox.policies = {
              DisableTelemetry = true;
            };
          }
        ];
        text = eval.config.system.activationScripts.postActivation.text;
      in {
        installsTemp = lib.hasInfix ''install -o root -g wheel -m 0644 "'' text
          && lib.hasInfix ''/Library/Preferences/org.mozilla.firefox.plist.tmp.$$'' text;
        movesAtomically = lib.hasInfix ''mv -f "/Library/Preferences/org.mozilla.firefox.plist.tmp.$$" "/Library/Preferences/org.mozilla.firefox.plist"'' text;
        invalidatesCache = lib.hasInfix ''killall cfprefsd >/dev/null 2>&1 || true'' text;
      };
    expected = {
      installsTemp = true;
      movesAtomically = true;
      invalidatesCache = true;
    };
  };

  testMacosModeContainsRemovalIntentForInBundlePolicies = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policyDelivery = "macos-preferences";
            programs.browserBaseline.firefox.policies = {
              DisableTelemetry = true;
            };
          }
        ];
        text = eval.config.system.activationScripts.postActivation.text;
      in lib.hasInfix ''rm -f "$firefoxPolicyDir/policies.json"'' text;
    expected = true;
  };

  testBundleModeContainsRemovalIntentForPreferencesPlist = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policyDelivery = "bundle";
            programs.browserBaseline.firefox.policies = {
              DisableTelemetry = true;
            };
          }
        ];
        text = eval.config.system.activationScripts.postActivation.text;
      in lib.hasInfix ''rm -f "/Library/Preferences/org.mozilla.firefox.plist"'' text;
    expected = true;
  };

  testEmptyFirefoxContributionRemovesBothSurfacesAndEmitsNeitherInstall = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policyDelivery = "macos-preferences";
          }
        ];
        text = eval.config.system.activationScripts.postActivation.text;
      in {
        removesBundle = lib.hasInfix ''rm -f "$firefoxPolicyDir/policies.json"'' text;
        removesPlist = lib.hasInfix ''rm -f "/Library/Preferences/org.mozilla.firefox.plist"'' text;
        noInstallBundle = ! lib.hasInfix ''$firefoxPolicyDir/policies.json"'' (lib.concatStrings (lib.filter (l: lib.hasInfix "install" l) (lib.splitString "\n" text)));
        noInstallPlist = ! lib.hasInfix ''org.mozilla.firefox.plist'' (lib.concatStrings (lib.filter (l: lib.hasInfix "install" l) (lib.splitString "\n" text)));
      };
    expected = {
      removesBundle = true;
      removesPlist = true;
      noInstallBundle = true;
      noInstallPlist = true;
    };
  };

  testMacosModePlusOpaquePayloadFails = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policyDelivery = "macos-preferences";
            programs.browserBaseline.firefox.policiesJsonPayload = repoRoot + "/config/browser/firefox-policies.json";
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasUnsupportedPayload = builtins.elem "unsupported-delivery-payload" (map (v: v.type) violations);
      };
    expected = {
      hasUnsupportedPayload = true;
    };
  };

  testStructuredPlusPayloadConflictRemainsRejected = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policies = {
              DisableTelemetry = true;
            };
            programs.browserBaseline.firefox.policiesJsonPayload = repoRoot + "/config/browser/firefox-policies.json";
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasConflict = builtins.elem "conflicting-definitions" (map (v: v.type) violations);
      };
    expected = {
      hasConflict = true;
    };
  };

  testNoFirefoxAppAbsenceWarningInMacosMode = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policyDelivery = "macos-preferences";
            programs.browserBaseline.firefox.policies = {
              DisableTelemetry = true;
            };
          }
        ];
        text = eval.config.system.activationScripts.postActivation.text;
      in lib.hasInfix ''echo "WARNING: [browserBaseline] Firefox policy is configured but /Applications/Firefox.app was not found'' text;
    expected = false;
  };

  testCurrentDefaultDoesNotInstallMacosPreferencesPlist = {
    expr =
      let
        eval = evalSeam [];
        text = eval.config.system.activationScripts.postActivation.text;
      in lib.hasInfix ''org.mozilla.firefox.plist.tmp'' text;
    expected = false;
  };

  testMacosModeInspectableEffectiveState = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policyDelivery = "macos-preferences";
            programs.browserBaseline.firefox.policies = {
              DisableTelemetry = true;
            };
          }
        ];
        state = eval.config.system.build.browserBaseline.effectiveState;
      in {
        delivery = state.firefoxPolicyDelivery;
        hasFirefoxPolicies = state.hasFirefoxPolicies;
        hasFirefoxPlist = state.hasFirefoxPlist;
      };
    expected = {
      delivery = "macos-preferences";
      hasFirefoxPolicies = true;
      hasFirefoxPlist = true;
    };
  };

  testMacosModePriorityOverrideFails = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policyDelivery = lib.mkForce "macos-preferences";
            programs.browserBaseline.firefox.policies = {
              DisableTelemetry = true;
            };
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasPriorityOverride = builtins.elem "priority-override" (map (v: v.type) violations);
      };
    expected = {
      hasPriorityOverride = true;
    };
  };

  testMacosModeWithLegacyEnabledFailsExclusivity = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = true;
            programs.browserBaseline.firefox.policyDelivery = "macos-preferences";
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasLegacyExclusivity = builtins.elem "legacy-exclusivity" (map (v: v.type) violations);
      };
    expected = {
      hasLegacyExclusivity = true;
    };
  };

  testEmptyMacosModeDoesNotGeneratePlist = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policyDelivery = "macos-preferences";
          }
        ];
        bb = eval.config.system.build.browserBaseline;
      in {
        managedPlist = bb.firefoxManagedPlist;
        hasFirefoxPolicies = bb.effectiveState.hasFirefoxPolicies;
        hasFirefoxPlist = bb.effectiveState.hasFirefoxPlist;
      };
    expected = {
      managedPlist = null;
      hasFirefoxPolicies = false;
      hasFirefoxPlist = false;
    };
  };

  testBundleModeEmptyContributionRemovesBothSurfacesAndEmitsNeitherInstall = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policyDelivery = "bundle";
          }
        ];
        text = eval.config.system.activationScripts.postActivation.text;
      in {
        removesBundle = lib.hasInfix ''rm -f "$firefoxPolicyDir/policies.json"'' text;
        removesPlist = lib.hasInfix ''rm -f "/Library/Preferences/org.mozilla.firefox.plist"'' text;
        noInstallBundle = ! lib.hasInfix ''$firefoxPolicyDir/policies.json"'' (lib.concatStrings (lib.filter (l: lib.hasInfix "install" l) (lib.splitString "\n" text)));
        noInstallPlist = ! lib.hasInfix ''org.mozilla.firefox.plist'' (lib.concatStrings (lib.filter (l: lib.hasInfix "install" l) (lib.splitString "\n" text)));
      };
    expected = {
      removesBundle = true;
      removesPlist = true;
      noInstallBundle = true;
      noInstallPlist = true;
    };
  };

  testFirefoxReservedKeyFails = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policies = {
              EnterprisePoliciesEnabled = false;
            };
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasReservedKey = builtins.elem "reserved-key" (map (v: v.type) violations);
      };
    expected = {
      hasReservedKey = true;
    };
  };

  testMacosModeNullValueFails = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policyDelivery = "macos-preferences";
            programs.browserBaseline.firefox.policies = {
              DisableTelemetry = null;
            };
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasUnsupportedNull = builtins.elem "unsupported-null-value" (map (v: v.type) violations);
      };
    expected = {
      hasUnsupportedNull = true;
    };
  };

  testMacosModePriorityMkDefaultFails = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policyDelivery = lib.mkDefault "macos-preferences";
            programs.browserBaseline.firefox.policies = {
              DisableTelemetry = true;
            };
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasPriorityOverride = builtins.elem "priority-override" (map (v: v.type) violations);
      };
    expected = {
      hasPriorityOverride = true;
    };
  };

  testMacosModePriorityMkOptionDefaultFails = {
    expr =
      let
        eval = evalSeam [
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policyDelivery = lib.mkOptionDefault "bundle";
            programs.browserBaseline.firefox.policies = {
              DisableTelemetry = true;
            };
          }
        ];
        violations = eval.config.system.build.browserBaseline.guardViolations;
      in {
        hasPriorityOverride = builtins.elem "priority-override" (map (v: v.type) violations);
      };
    expected = {
      hasPriorityOverride = true;
    };
  };
}
