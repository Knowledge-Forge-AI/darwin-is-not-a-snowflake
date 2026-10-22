{
  fetchFromGitHub,
  gitMinimal,
  lib,
  makeWrapper,
  pkg-config,
  rustPlatform,
  sqlite,
}:

rustPlatform.buildRustPackage rec {
  pname = "rtk";
  version = "0.43.0";

  src = fetchFromGitHub {
    owner = "rtk-ai";
    repo = "rtk";
    rev = "v${version}";
    hash = "sha256-n5bkPPsrdM4fE5ltocTjlq+JwRgp39yib6S79fci4m4=";
  };

  cargoHash = "sha256-XKUKdhxfnwUCOx9slqx4oUFa09HcosPLVh5Xkh87oSk=";

  nativeBuildInputs = [
    makeWrapper
    pkg-config
  ];

  buildInputs = [
    sqlite
  ];

  nativeCheckInputs = [
    gitMinimal
  ];

  preCheck = ''
    export HOME="$TMPDIR"
  '';

  postInstall = ''
    wrapProgram "$out/bin/rtk" \
      --prefix PATH : ${lib.makeBinPath [ gitMinimal ]}
  '';

  meta = {
    description = "CLI proxy that reduces LLM token consumption on common development commands";
    homepage = "https://github.com/rtk-ai/rtk";
    changelog = "https://github.com/rtk-ai/rtk/blob/v${version}/CHANGELOG.md";
    license = lib.licenses.asl20;
    mainProgram = "rtk";
    platforms = lib.platforms.unix;
  };
}
