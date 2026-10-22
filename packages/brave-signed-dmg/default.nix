{ brave, fetchurl, stdenv, undmg, unzip }:

let
  version = "1.91.180";
  # Official release asset digests for the version in the locked nixpkgs recipe:
  # https://github.com/brave/brave-browser/releases/expanded_assets/v1.91.180
  archives = {
    aarch64-darwin = {
      arch = "arm64";
      hash = "sha256-/aCdbvLSsWssv8DHHliy4NN4FEG6tRHGfIL2nN+jMyM=";
    };
    x86_64-darwin = {
      arch = "x64";
      hash = "sha256-IknPBGW5VRFd+dH7NXXKq6ezNuJzQei8iH0NvxId1bo=";
    };
  };
  archive = archives.${stdenv.hostPlatform.system};
in
assert stdenv.hostPlatform.isDarwin;
# Fail closed on a version update: refresh the exact assets, or remove this
# backport once selected nixpkgs includes PR #560971's signed-DMG packaging
# and the native signing qualification passes for that selected package.
assert brave.version == version;
brave.overrideAttrs (old: {
  # Backport the packaging fix from nixpkgs PR #560971 without changing pins.
  src = fetchurl {
    url = "https://github.com/brave/brave-browser/releases/download/v${version}/Brave-Browser-${archive.arch}.dmg";
    inherit (archive) hash;
  };
  nativeBuildInputs = builtins.filter (input: input != unzip) old.nativeBuildInputs ++ [ undmg ];
  # The locked installPhase copies '.' into $out/Applications/Brave Browser.app.
  sourceRoot = "Brave Browser.app";
  # Keep the upstream Developer ID signature and TeamIdentifier intact.
  dontStrip = true;
  # Other default fixups (including patchShebangs) can also alter sealed files.
  # The inherited installPhase creates the CLI wrapper outside the signed app.
  dontFixup = true;
})
