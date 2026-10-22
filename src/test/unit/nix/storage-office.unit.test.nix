# nix-darwin-test-source-target: modules/desktop-apps.nix
# nix-darwin-test-source-target: lib/core-application-inventory.nix
{ repoRoot, system }:
let
  inventory = import (repoRoot + "/lib/core-application-inventory.nix");
  desktop = hostName: (import (repoRoot + "/modules/desktop-apps.nix")) {
    config.networking.hostName = hostName;
    lib.optionals = condition: values: if condition then values else [ ];
    pkgs.qbittorrent = "synthetic-cli-package";
  };
  current = inventory.guiApplicationsFor { hostName = "example-primary"; inherit system; };
  predecessor = inventory.guiApplicationsFor { hostName = "example-workstation"; inherit system; };
  additions = builtins.filter (claim: builtins.elem claim.package [ "libreoffice" "onlyoffice" "zettlr" ]) current;
in {
  testThreeManagedOfficeIdentities = {
    expr = map (claim: { inherit (claim) package artifact id; }) additions;
    expected = [
      { package = "libreoffice"; artifact = "LibreOffice.app"; id = "bundle:org.libreoffice.script"; }
      { package = "onlyoffice"; artifact = "ONLYOFFICE.app"; id = "bundle:asc.onlyoffice.ONLYOFFICE"; }
      { package = "zettlr"; artifact = "Zettlr.app"; id = "bundle:com.zettlr.app"; }
    ];
  };
  testHostSpecificActualDesktopModule = {
    expr = {
      current = (desktop "example-primary").homebrew.casks;
      predecessor = (desktop "example-workstation").homebrew.casks;
      predecessorClaims = builtins.length predecessor;
      currentClaims = builtins.length current;
    };
    expected = {
      current = [ "adobe-acrobat-reader" "ente-auth" "logi-options+" "logos" "microsoft-teams" "zoom" "libreoffice" "onlyoffice" "zettlr" ];
      predecessor = [ "adobe-acrobat-reader" "ente-auth" "logi-options+" "logos" "microsoft-teams" "zoom" ];
      predecessorClaims = 18;
      currentClaims = 21;
    };
  };
  testUnmanagedGrandPerspectiveHasNoManagedClaimOrCask = {
    expr = {
      caskPresent = builtins.elem "grandperspective" (desktop "example-primary").homebrew.casks;
      claimed = builtins.any (claim: claim.package == "grandperspective") current;
      managedOfficeCasks = builtins.filter (name: builtins.elem name [ "libreoffice" "onlyoffice" "zettlr" ])
        (desktop "example-primary").homebrew.casks;
    };
    expected = {
      caskPresent = false;
      claimed = false;
      managedOfficeCasks = [ "libreoffice" "onlyoffice" "zettlr" ];
    };
  };
  testTransferClaimsAreAbsent = {
    expr = builtins.any (claim: builtins.elem claim.package [ "arq" "google-drive" "maestral" "microsoft-office" "onedrive" ]) current;
    expected = false;
  };
}
