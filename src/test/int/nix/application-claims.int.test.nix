# nix-darwin-test-source-target: flake.nix
# nix-darwin-test-source-target: lib/application-claims.nix
# nix-darwin-test-source-target: lib/core-application-inventory.nix
# nix-darwin-test-source-target: modules/brave-youtube.nix
# nix-darwin-test-source-target: modules/brewfile-stragglers.nix
# nix-darwin-test-source-target: modules/browsers.nix
# nix-darwin-test-source-target: modules/codex.nix
# nix-darwin-test-source-target: modules/desktop-apps.nix
# nix-darwin-test-source-target: modules/developer-tooling.nix
# nix-darwin-test-source-target: modules/kitty.nix
# nix-darwin-test-source-target: modules/password-sync.nix
{ repoRoot, system }:

let
  flake = builtins.getFlake "path:${toString repoRoot}";
  pkgs = flake.legacyPackages.${system};
  lib = pkgs.lib;
  config = flake.darwinConfigurations.example-primary.config;

  claimsLib = flake.lib.applicationClaims;
  inventory = flake.lib.coreApplicationInventory;
  nonApplicationCasks = builtins.attrNames flake.lib.nonApplicationCasks;
  coreClaims = flake.applicationClaims;

  sortUnique = values: lib.sort (a: b: a < b) (lib.unique values);
  bundleIdOf = entry: lib.removePrefix "bundle:" entry.id;
  onSurface = surface: builtins.filter (entry: entry.surface == surface) inventory;
  packagesOn = surface: sortUnique (map (entry: entry.package) (onSurface surface));
  artifactsOn = surface: sortUnique (map (entry: entry.artifact) (onSurface surface));

  # Declared surfaces, read from the evaluated configuration rather than from
  # the inventory, so a declaration the inventory does not know about shows up.
  declaredCasks = sortUnique (map (cask: cask.name) config.homebrew.casks);
  declaredMasApps = sortUnique (map toString (builtins.attrValues config.homebrew.masApps));
  declaredNixApplications =
    sortUnique (builtins.attrNames
      (builtins.readDir "${config.system.build.applications}/Applications"));

  activationText = config.system.activationScripts.postActivation.text;
  scanActivation = marker: index:
    map
      (part: builtins.elemAt (lib.splitString "\"" part) index)
      (builtins.tail (lib.splitString marker activationText));
  declaredGeneratedBundleIds = sortUnique (
    scanActivation "CFBundleIdentifier string \"" 0
    ++ scanActivation "kitty_zsh_bundle_id=\"" 0
    ++ scanActivation "kitty_bash_bundle_id=\"" 0
    ++ scanActivation "brave_youtube_bundle_id=\"" 0
  );

  # The two halves of the completeness contract, as functions of the declared
  # surface so a synthetic new declaration can be run through them.
  unclaimed = claimed: declared: lib.subtractLists claimed declared;
  stale = claimed: declared: lib.subtractLists declared claimed;

  claimedCasks = sortUnique (packagesOn "homebrew.casks"
    ++ map (entry: entry.package)
      (builtins.filter (entry: entry.provider == "homebrew-cask") (onSurface "system.activationScripts"))
    ++ nonApplicationCasks);

  moduleSource = entry: builtins.readFile (repoRoot + "/${entry.declaration}");
  declarationToken = entry:
    if entry.surface == "homebrew.casks" then "\"${entry.package}\""
    else if entry.surface == "homebrew.masApps" then entry.package
    else if entry.surface == "environment.systemPackages" then "pkgs.${entry.package}"
    else bundleIdOf entry;
in
{
  testCoreApplicationClaimsValidateUnderTheSharedContract = {
    expr =
      let result = claimsLib.validate (claimsLib.attachOwner "core" coreClaims); in
      {
        ok = result.ok;
        errors = result.errors;
        count = builtins.length coreClaims;
        neverSelfReportsOwner =
          !(builtins.any (claim: claim ? owner) coreClaims);
        doesNotClaimUnmanagedGrandPerspective =
          !(builtins.any (claim: claim.package == "grandperspective") coreClaims);
        everyClaimIsABundleIdentity =
          builtins.all (claim: lib.hasPrefix "bundle:" claim.id) coreClaims;
      };
    expected = {
      ok = true;
      errors = [ ];
      count = 24;
      neverSelfReportsOwner = true;
      doesNotClaimUnmanagedGrandPerspective = true;
      everyClaimIsABundleIdentity = true;
    };
  };

  testEveryDeclaredHomebrewCaskIsClaimedOrRecordedAsInstallingNoApplication = {
    expr = {
      unclaimed = unclaimed claimedCasks declaredCasks;
      stale = stale claimedCasks declaredCasks;
      # A newly added GUI cask with no claim must fail this check.
      detectsNewUnclaimedCask =
        unclaimed claimedCasks (sortUnique (declaredCasks ++ [ "slack" ]));
      # A claim pointing at a declaration that no longer exists must fail too.
      detectsRemovedDeclaration =
        stale claimedCasks (lib.remove "zoom" declaredCasks);
    };
    expected = {
      unclaimed = [ ];
      stale = [ ];
      detectsNewUnclaimedCask = [ "slack" ];
      detectsRemovedDeclaration = [ "zoom" ];
    };
  };

  testEveryDeclaredMacAppStoreApplicationIsClaimed = {
    expr =
      let claimed = packagesOn "homebrew.masApps"; in
      {
        unclaimed = unclaimed claimed declaredMasApps;
        stale = stale claimed declaredMasApps;
        detectsNewUnclaimedMasApp =
          unclaimed claimed (sortUnique (declaredMasApps ++ [ "497799835" ]));
      };
    expected = {
      unclaimed = [ ];
      stale = [ ];
      detectsNewUnclaimedMasApp = [ "497799835" ];
    };
  };

  testEveryNixApplicationBundleCoreInstallsIsClaimed = {
    expr =
      let claimed = artifactsOn "environment.systemPackages"; in
      {
        unclaimed = unclaimed claimed declaredNixApplications;
        stale = stale claimed declaredNixApplications;
        detectsNewUnclaimedApplication =
          unclaimed claimed (sortUnique (declaredNixApplications ++ [ "Slack.app" ]));
      };
    expected = {
      unclaimed = [ ];
      stale = [ ];
      detectsNewUnclaimedApplication = [ "Slack.app" ];
    };
  };

  testStockBraveCaskAndYoutubeSourceHaveIndependentOwnership = {
    expr = {
      stock = map (claim: {
        inherit (claim) provider package surface;
        suite = claim.suite or false;
      }) (builtins.filter (claim: claim.id == "bundle:com.brave.Browser") inventory);
      youtube = map (claim: {
        inherit (claim) provider package surface;
        suite = claim.suite or false;
      }) (builtins.filter (claim: claim.id == "bundle:com.operator.brave-youtube") inventory);
      stableCasks = map (c: c.name) (builtins.filter (c: c.name == "brave-browser") config.homebrew.casks);
      noNixStockBundle = !(builtins.elem "Brave Browser.app" declaredNixApplications);
    };
    expected = {
      stock = [{ provider = "homebrew-cask"; package = "brave-browser"; surface = "homebrew.casks"; suite = false; }];
      youtube = [{ provider = "nix"; package = "brave"; surface = "system.activationScripts"; suite = false; }];
      stableCasks = [ "brave-browser" ]; noNixStockBundle = true;
    };
  };

  testEveryActivationGeneratedApplicationBundleIsClaimed = {
    expr =
      let claimed = sortUnique (map bundleIdOf (onSurface "system.activationScripts")); in
      {
        unclaimed = unclaimed claimed declaredGeneratedBundleIds;
        stale = stale claimed declaredGeneratedBundleIds;
        declared = declaredGeneratedBundleIds;
      };
    expected = {
      unclaimed = [ ];
      stale = [ ];
      declared = [
        "com.operator.brave-youtube"
        "com.operator.kitty.bash"
        "com.operator.kitty.zsh"
      ];
    };
  };

  testEveryClaimNamesADeclarationThatStillExistsAndStillDeclaresIt = {
    expr = {
      missingDeclarationFiles = builtins.filter
        (entry: !(builtins.pathExists (repoRoot + "/${entry.declaration}")))
        inventory;
      declarationsNoLongerNamingTheClaim = map
        (entry: "${entry.declaration}: ${entry.id}")
        (builtins.filter
          (entry: !(lib.hasInfix (declarationToken entry) (moduleSource entry)))
          inventory);
    };
    expected = {
      missingDeclarationFiles = [ ];
      declarationsNoLongerNamingTheClaim = [ ];
    };
  };

  testCommandLineOnlyPackagesAreExcludedFromApplicationClaims = {
    expr =
      let
        claimsMentioning = token: builtins.filter
          (claim: lib.hasInfix token (lib.toLower "${claim.package} ${claim.id} ${claim.artifact}"))
          coreClaims;
      in
      {
        masBrewHasNoClaim =
          builtins.filter (claim: claim.package == "mas") coreClaims == [ ];
        antigravityHasNoClaim = claimsMentioning "antigravity" == [ ];
        codexHasNoClaim = claimsMentioning "codex" == [ ];
      };
    expected = {
      masBrewHasNoClaim = true;
      antigravityHasNoClaim = true;
      codexHasNoClaim = true;
    };
  };

  testCoreClaimsCarryNoOperatorSpecificPaths = {
    expr = builtins.filter
      (claim: lib.hasInfix "/" claim.artifact || lib.hasInfix "~" claim.artifact)
      coreClaims;
    expected = [ ];
  };
}
