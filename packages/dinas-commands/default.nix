{
  lib,
  stdenvNoCC,
  makeWrapper, bash, zsh, python3, rclone, arq_restore, coreutils,
}:

let
  # Commands packaged into dinasCommands bin/
  # Note: codex-tooling-self-heal was transferred to codex-vc and is an unpackaged local pointer.
  allowedCommands = [
    "brave-youtube-cdp"
    "brave-youtube-session"
    "clear-new-tab-sync"
    "cnt-wallpaper-gallery"
    "codex-git-mcp"
    "codex-rclone-sync"
    "codex-restore"
    "darwin-rebuild"
    "dinas-dev"
    "dinas-reconcile"
    "dinas-root-cutover"
    "env-allowlist-check"
    "env-run"
    "env-snapshot"
    "link-path"
    "monitor-temp"
    "nix-darwin-config"
    "nix-int-test"
    "nix-opt"
    "nix-rebuild"
    "nix-test"
    "nix-unit-test"
    "nix-update"
    "shared-images-db-audit"
    "shared-images-promote-original"
    "shared-images-rclone-sync"
    "shared-images-refresh-artist-links"
    "shared-images-rehash"
    "shared-images-workflow-layout"
    "toggle-link-lock"
  ];
in
stdenvNoCC.mkDerivation {
  pname = "dinas-commands";
  version = "1.0.0";
  src = lib.cleanSourceWith {
    src = ../..;
    filter = path: type:
      let
        rel = lib.removePrefix (toString ../.. + "/") (toString path);
      in
      rel == "bin"
      || (lib.hasPrefix "bin/" rel && builtins.elem (lib.removePrefix "bin/" rel) allowedCommands)
      || rel == "libexec"
      || lib.hasPrefix "libexec/" rel;
  };

  nativeBuildInputs = [ makeWrapper bash zsh python3 ];
  dontBuild = true;

  installPhase = ''
    mkdir -p $out/bin $out/libexec
    cp -R libexec/* $out/libexec/

    for cmd in ${lib.concatStringsSep " " allowedCommands}; do
      if [ -f "bin/$cmd" ]; then
        cp -P "bin/$cmd" "$out/bin/$cmd"
      else
        echo "Missing required command: bin/$cmd" >&2
        exit 1
      fi
    done
    patchShebangs "$out/bin/codex-rclone-sync" "$out/bin/shared-images-rclone-sync" "$out/bin/codex-restore" "$out/libexec/codex-dr"
    wrapProgram "$out/bin/codex-rclone-sync" \
      --set-default RCLONE "${rclone}/bin/rclone" \
      --prefix PATH : ${lib.makeBinPath [ bash coreutils ]}
    wrapProgram "$out/bin/shared-images-rclone-sync" \
      --set-default RCLONE "${rclone}/bin/rclone" \
      --prefix PATH : ${lib.makeBinPath [ bash coreutils ]}
    wrapProgram "$out/bin/codex-restore" \
      --set-default CODEX_RESTORE_ARQ_RESTORE_BIN "${arq_restore}/bin/arq_restore" \
      --prefix PATH : ${lib.makeBinPath [ zsh python3 coreutils ]}
  '';

  meta = with lib; {
    description = "DINAS core commands and update utilities";
    maintainers = [ ];
  };
}
