{ configurations }:
let
  host = name:
    let cfg = configurations.${name}.config; bb = cfg.system.build.browserBaseline;
    in {
      fullToplevelEvaluated = builtins.isString cfg.system.build.toplevel.drvPath;
      rolesR3 = cfg.programs.browserBaseline.rolesR3;
      coreOwnership = cfg.programs.browserBaseline.corePolicyOwnership;
      deploymentHeld = bb.deploymentHeld;
      failedAssertions = map (a: a.message) (builtins.filter (a: !a.assertion) cfg.assertions);
      guardViolations = bb.guardViolations;
      chromeEnabled = bb.chromeEnable;
      firefoxEnabled = bb.firefoxEnable;
      chromeExtensionIds = builtins.attrNames bb.chromeExternalExtensions;
      chromeNativeHostNames = builtins.attrNames bb.chromeNativeMessagingHosts;
      packageNames = map (p: p.pname or p.name or "") cfg.environment.systemPackages;
      casks = map (c: c.name or c) cfg.homebrew.casks;
      browserUxRoles = cfg."browser-ux".rolesR3 or null;
      protonVpnHistorical = bb.protonVpnR3R2;
      protonVpnDelivery = bb.protonVpnR3R3;
      automationExposed = bb.automationBrowser != null;
      targets = map (row: { inherit (row) target; action = row.action or "install"; }) bb.attendedTargets;
    };
in {
  schema = "browser-r3r3-native-readback-v1";
  target = host "example-primary";
  predecessor = host "example-workstation";
}
