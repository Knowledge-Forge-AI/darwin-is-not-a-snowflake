{
  lib,
  stdenv,
  fetchFromGitHub,
  libxml2,
  zlib,
}:

stdenv.mkDerivation {
  pname = "arq_restore";
  version = "unstable-2026-04-13";

  src = fetchFromGitHub {
    owner = "arqbackup";
    repo = "arq_restore";
    rev = "0911ce278a2311c3ee16746d2e3f6a5ab2be0d35";
    hash = "sha256-Tw/iFXFLxa0D5dVBGst9jT9eZ3mN/Zz3xTPzusRGJUU=";
  };

  # Temporary local compatibility patch for Arq 7 Tree v4 node metadata.
  # Drop this once upstream arqbackup/arq_restore consumes the Tree v4 fields
  # documented by Arq and a temp-folder Codex restore succeeds without it.
  patches = [
    ./arq7-tree-v4-node-fields.patch
  ];

  buildInputs = [
    libxml2
    zlib
  ];

  buildPhase = builtins.readFile ./build.sh;

  installPhase = ''
    runHook preInstall
    install -Dm755 build/arq_restore "$out/bin/arq_restore"
    runHook postInstall
  '';

  meta = {
    description = "Command-line utility for restoring Arq backup data";
    homepage = "https://github.com/arqbackup/arq_restore";
    license = lib.licenses.bsd3;
    mainProgram = "arq_restore";
    platforms = lib.platforms.darwin;
  };
}
