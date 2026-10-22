# nix-darwin-test-source-target: delivery/browser-ownership-r3r2/readback.nix
# nix-darwin-test-source-target: delivery/browser-ownership-r3r3/readback.nix
{ repoRoot, system }:

let
  readbackR3R2 = import (repoRoot + "/delivery/browser-ownership-r3r2/readback.nix");
  readbackR3R3 = import (repoRoot + "/delivery/browser-ownership-r3r3/readback.nix");

  adapters = [ readbackR3R2 readbackR3R3 ];

  mkSyntheticHost =
    args:
    let
      defaults = {
        drvPath = "/nix/store/synthetic-system-toplevel.drv";
        rolesR3 = false; corePolicyOwnership = false; deploymentHeld = false;
        assertions = [ ]; guardViolations = [ ]; chromeEnable = false; firefoxEnable = false;
        chromeExternalExtensions = { }; chromeNativeMessagingHosts = { };
        systemPackages = [ ]; casks = [ ]; browserUxRoles = null;
        protonVpnR3R2 = null; protonVpnR3R3 = null; automationBrowser = null;
        attendedTargets = [ ];
      };
      a = defaults // args;
    in
    {
      config = {
        system.build = {
          toplevel.drvPath = a.drvPath;
          browserBaseline = {
            inherit (a)
              deploymentHeld guardViolations chromeEnable firefoxEnable
              chromeExternalExtensions chromeNativeMessagingHosts
              protonVpnR3R2 protonVpnR3R3 automationBrowser attendedTargets;
          };
        };
        programs.browserBaseline = {
          inherit (a) rolesR3 corePolicyOwnership;
        };
        environment.systemPackages = a.systemPackages;
        homebrew.casks = a.casks;
        assertions = a.assertions;
      } // (if a.browserUxRoles != null then {
        browser-ux.rolesR3 = a.browserUxRoles;
      } else { });
    };

  mkPair =
    {
      target ? { },
      predecessor ? { },
    }:
    {
      configurations = {
        "example-primary" = mkSyntheticHost target;
        "example-workstation" = mkSyntheticHost predecessor;
      };
    };
in
{
  testHostRoleSeparationAndReadinessAttribution = {
    expr = map (readback:
      let
        result = readback (mkPair {
          target = {
            rolesR3 = true; corePolicyOwnership = true; browserUxRoles = true;
            automationBrowser = "brave"; chromeEnable = true; deploymentHeld = true;
          };
          predecessor = {
            rolesR3 = false; corePolicyOwnership = false; browserUxRoles = false;
            automationBrowser = null; chromeEnable = false; deploymentHeld = false;
          };
        });
      in
      {
        target = {
          roles = result.target.rolesR3; core = result.target.coreOwnership;
          ux = result.target.browserUxRoles; auto = result.target.automationExposed;
          chrome = result.target.chromeEnabled; held = result.target.deploymentHeld;
        };
        predecessor = {
          roles = result.predecessor.rolesR3; core = result.predecessor.coreOwnership;
          ux = result.predecessor.browserUxRoles; auto = result.predecessor.automationExposed;
          chrome = result.predecessor.chromeEnabled; held = result.predecessor.deploymentHeld;
        };
      }) adapters;
    expected = map (_: {
      target = { roles = true; core = true; ux = true; auto = true; chrome = true; held = true; };
      predecessor = { roles = false; core = false; ux = false; auto = false; chrome = false; held = false; };
    }) adapters;
  };

  testFailedAssertionsExtractedAndPassingAssertionsIgnored = {
    expr = map (readback:
      let
        result = readback (mkPair {
          target = {
            assertions = [
              { assertion = true; message = "invariant holds"; }
              { assertion = false; message = "policy collision: conflicting chrome settings"; }
              { assertion = true; message = "dependency verified"; }
              { assertion = false; message = "missing required entitlement for target host"; }
            ];
          };
        });
      in
      result.target.failedAssertions) adapters;
    expected = map (_: [
      "policy collision: conflicting chrome settings"
      "missing required entitlement for target host"
    ]) adapters;
  };

  testAbsentBrowserUxRolesRemainNullForTheirHost = {
    expr = map (readback:
      let
        result = readback (mkPair {
          target = { browserUxRoles = true; };
        });
      in
      {
        target = result.target.browserUxRoles;
        predecessor = result.predecessor.browserUxRoles;
      }) adapters;
    expected = map (_: {
      target = true;
      predecessor = null;
    }) adapters;
  };

  testNonStringToplevelRefusesFullEvaluation = {
    expr = map (readback: {
      stringDrv = (readback (mkPair { target = { drvPath = "/nix/store/valid-toplevel.drv"; }; })).target.fullToplevelEvaluated;
      nullDrv = (readback (mkPair { target = { drvPath = null; }; })).target.fullToplevelEvaluated;
      attrDrv = (readback (mkPair { target = { drvPath = { outPath = "/not/drv"; }; }; })).target.fullToplevelEvaluated;
    }) adapters;
    expected = map (_: {
      stringDrv = true;
      nullDrv = false;
      attrDrv = false;
    }) adapters;
  };

  testPackageAndCaskNormalizationWithTargetActionDefaults = {
    expr = map (readback:
      let
        target = (readback (mkPair {
          target = {
            systemPackages = [ { pname = "brave"; } { name = "google-chrome-120"; } { } ];
            casks = [ "brave-browser" { name = "keepassxc"; } ];
            attendedTargets = [ { target = "brave-daily"; } { target = "google-chrome"; action = "remove"; } ];
          };
        })).target;
      in
      {
        packages = target.packageNames;
        casks = target.casks;
        targets = target.targets;
      }) adapters;
    expected = map (_: {
      packages = [ "brave" "google-chrome-120" "" ];
      casks = [ "brave-browser" "keepassxc" ];
      targets = [
        { target = "brave-daily"; action = "install"; }
        { target = "google-chrome"; action = "remove"; }
      ];
    }) adapters;
  };

  testHistoricalAndCurrentProtonFieldDistinctionAcrossRevisions = {
    expr =
      let
        inputs = mkPair {
          target = {
            protonVpnR3R2 = { tier = "plus"; };
            protonVpnR3R3 = { delivery = "cask"; };
          };
        };
        resR2 = readbackR3R2 inputs;
        resR3 = readbackR3R3 inputs;
      in
      {
        r2 = {
          schema = resR2.schema;
          hasIntent = builtins.hasAttr "protonVpnIntent" resR2.target;
          hasHistorical = builtins.hasAttr "protonVpnHistorical" resR2.target;
          hasDelivery = builtins.hasAttr "protonVpnDelivery" resR2.target;
          intent = resR2.target.protonVpnIntent;
        };
        r3 = {
          schema = resR3.schema;
          hasIntent = builtins.hasAttr "protonVpnIntent" resR3.target;
          hasHistorical = builtins.hasAttr "protonVpnHistorical" resR3.target;
          hasDelivery = builtins.hasAttr "protonVpnDelivery" resR3.target;
          historical = resR3.target.protonVpnHistorical;
          delivery = resR3.target.protonVpnDelivery;
        };
      };
    expected = {
      r2 = {
        schema = "browser-r3r2-native-readback-v1";
        hasIntent = true; hasHistorical = false; hasDelivery = false;
        intent = { tier = "plus"; };
      };
      r3 = {
        schema = "browser-r3r3-native-readback-v1";
        hasIntent = false; hasHistorical = true; hasDelivery = true;
        historical = { tier = "plus"; }; delivery = { delivery = "cask"; };
      };
    };
  };
}
