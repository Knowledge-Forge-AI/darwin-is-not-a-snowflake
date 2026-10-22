# nix-darwin-test-source-target: packages/brave-signed-dmg/default.nix
# nix-darwin-test-source-target: flake.nix
{ repoRoot, system, nixpkgsPath ? (builtins.getFlake "path:${toString repoRoot}").inputs.nixpkgs.outPath }:

let
  lib = import (nixpkgsPath + "/lib");
  # Capture the constructor's actual overlays at the darwinSystem boundary.
  # Package evaluation below is real; this does not claim a full host evaluation.
  core = (import (repoRoot + "/flake.nix")).outputs {
    self = { };
    nixpkgs = { inherit lib; };
    nixpkgs-unstable = { };
    nix-darwin.lib.darwinSystem = args: args;
  };
  constructor = core.lib.mkDarwinConfiguration {
    hostName = builtins.head (builtins.attrNames core.lib.hosts);
  };
  configurationModules = builtins.filter (module: module ? nixpkgs.overlays)
    (map (module: if builtins.isFunction module
      then module { config = { }; pkgs = base; }
      else module) constructor.modules);
  configuration = assert builtins.length configurationModules == 1;
    builtins.head configurationModules;
  overlays = configuration.nixpkgs.overlays;
  packageSet = platform: overlays: import nixpkgsPath {
    system = platform;
    inherit overlays;
    config = (import (repoRoot + "/modules/nixpkgs-policy.nix") { inherit lib; }).nixpkgs.config;
  };
  base = packageSet system [ ];
  candidate = packageSet system overlays;
  braveFor = platform: (packageSet platform overlays).brave;
in
{
  testExactVersionedOfficialDmgForBothDarwinArchitectures = {
    expr = map (platform: let brave = braveFor platform; in {
      inherit (brave) version dontStrip dontFixup sourceRoot;
      inherit (brave.src) url outputHash;
    }) [ "aarch64-darwin" "x86_64-darwin" ];
    expected = [
      {
        version = "1.91.180";
        dontStrip = true;
        dontFixup = true;
        sourceRoot = "Brave Browser.app";
        url = "https://github.com/brave/brave-browser/releases/download/v1.91.180/Brave-Browser-arm64.dmg";
        outputHash = "sha256-/aCdbvLSsWssv8DHHliy4NN4FEG6tRHGfIL2nN+jMyM=";
      }
      {
        version = "1.91.180";
        dontStrip = true;
        dontFixup = true;
        sourceRoot = "Brave Browser.app";
        url = "https://github.com/brave/brave-browser/releases/download/v1.91.180/Brave-Browser-x64.dmg";
        outputHash = "sha256-IknPBGW5VRFd+dH7NXXKq6ezNuJzQei8iH0NvxId1bo=";
      }
    ];
  };

  testOnlyArchiveExtractionAndSignaturePreservationChange = {
    expr = {
      publicOverlayNames = builtins.attrNames (core.overlays.default candidate base);
      inputs = map (input: input.name) candidate.brave.nativeBuildInputs;
      sameInstall = candidate.brave.installPhase == base.brave.installPhase;
      sameMeta = candidate.brave.meta == base.brave.meta;
      changesBrave = candidate.brave.drvPath != base.brave.drvPath;
      otherPackagesUnchanged = builtins.all
        (name: candidate.${name}.drvPath == base.${name}.drvPath)
        [ "google-chrome" "firefox" "floorp-bin" "kitty" ];
    };
    expected = {
      publicOverlayNames = [ ];
      inputs = [ "make-shell-wrapper-hook" "undmg-1.1.0-unstable-2024-08-02" ];
      sameInstall = true;
      sameMeta = true;
      changesBrave = true;
      otherPackagesUnchanged = true;
    };
  };

  testLinuxPackageAndOverlayRemainUnchanged = {
    expr = map (platform: let
      before = packageSet platform [ ];
      after = packageSet platform overlays;
    in {
      names = builtins.attrNames (core.overlays.default after before);
      unchanged = before.brave.drvPath == after.brave.drvPath;
    }) [ "aarch64-linux" "x86_64-linux" ];
    expected = [ { names = [ ]; unchanged = true; } { names = [ ]; unchanged = true; } ];
  };

  testVersionDriftRequiresAnExplicitDmgRefresh = {
    expr = (builtins.tryEval ((candidate.callPackage
      (repoRoot + "/packages/brave-signed-dmg") {
        brave = base.brave.overrideAttrs (_: {
          version = "0.0.0";
          __intentionallyOverridingVersion = true;
        });
      }).version)).success;
    expected = false;
  };
}
