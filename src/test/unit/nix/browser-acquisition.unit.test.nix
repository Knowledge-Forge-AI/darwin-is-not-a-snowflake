# nix-darwin-test-source-target: lib/core-application-inventory.nix
{ repoRoot, system, libPath ? null }:
let
  lib = if libPath == null then (builtins.getFlake "path:${toString repoRoot}").legacyPackages.${system}.lib else import libPath;
  claimsLib = import (repoRoot + "/lib/application-claims.nix") { inherit lib; };
  inventory = import (repoRoot + "/lib/core-application-inventory.nix");
  ids = [ "bundle:com.brave.Browser" "bundle:io.gitlab.librewolf-community" "bundle:com.kagi.kagimacOS" ];
  target = inventory.guiApplicationsFor { hostName = "example-primary"; stockApplications = true; inherit system; };
  selected = entries: builtins.filter (entry: builtins.elem entry.id ids) entries;
  count = entries: builtins.length (selected entries);
in {
  testBothHostClaimsValidateWithCoreOwnership = {
    expr = map (entries: (claimsLib.validate (claimsLib.attachOwner "core"
      (map (entry: builtins.removeAttrs entry inventory.provenanceFields) entries))).ok)
      [ target (inventory.guiApplicationsFor { hostName = "example-workstation"; }) ];
    expected = [ true true ];
  };
  testTargetAcquisitionClaimsExactlyThreeStockApps = {
    expr = {
      counts = map (id: builtins.length (builtins.filter (entry: entry.id == id) target)) ids;
      providers = map (entry: entry.provider) (selected target);
      stockBraveCasks = builtins.length (builtins.filter (entry: entry.package == "brave-browser") target);
      noBraveSuite = builtins.all (entry: !(entry.suite or false))
        (builtins.filter (entry: builtins.elem entry.package [ "brave" "brave-browser" ]) target);
      braveClaims = builtins.length (builtins.filter (entry: entry.package == "brave") target);
      youtubeProvider = (builtins.head (builtins.filter (entry: entry.package == "brave") target)).provider;
      currentExportMatches = inventory.guiApplications == target;
    };
    expected = { counts = [ 1 1 1 ]; providers = [ "homebrew-cask" "homebrew-cask" "homebrew-cask" ];
      stockBraveCasks = 1; noBraveSuite = true; braveClaims = 1; youtubeProvider = "nix"; currentExportMatches = true; };
  };
  testDefaultsAndExplicitDisabledSelectionDoNotAcquire = {
    expr = map (args: count (inventory.guiApplicationsFor args)) [
      { hostName = "example-primary"; }
      { hostName = "example-primary"; stockApplications = false; }
      { hostName = "example-workstation"; }
      { hostName = "example-workstation"; stockApplications = false; }
      { hostName = "generic"; }
    ];
    expected = [ 0 0 0 0 0 ];
  };
  testExplicitStockSelectionMatchesHostIndependentModuleAcquisition = {
    expr = map (hostName: count (inventory.guiApplicationsFor { inherit hostName; stockApplications = true; }))
      [ "example-primary" "example-workstation" "generic" ];
    expected = [ 3 3 3 ];
  };
  testLegacyR3InventorySelectionRetainsItsHostGate = {
    expr = map (hostName: count (inventory.guiApplicationsFor { inherit hostName; rolesR3 = true; }))
      [ "example-primary" "example-workstation" "generic" ];
    expected = [ 3 0 0 ];
  };
  testExistingR3SelectorStillImpliesOneAcquisitionSet = {
    expr = map (args: count (inventory.guiApplicationsFor (args // { hostName = "example-primary"; }))) [
      { rolesR3 = true; }
      { rolesR3 = true; stockApplications = false; }
      { rolesR3 = true; stockApplications = true; }
    ];
    expected = [ 3 3 3 ];
  };
}
