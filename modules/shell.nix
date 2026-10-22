{
  userName,
  localConfig,
}:
{
  config,
  lib,
  pkgs,
  ...
}:

let
  primaryUser = config.system.primaryUser;
  userHome = config.users.users.${primaryUser}.home;
  paths = (import ../lib/paths.nix { inherit lib; }).loadProjection {
    projection = localConfig;
    inherit userHome;
  };
  bashProfile = ../shell/bash_profile;
  bashrc = ../shell/bashrc;
  dinasPathsFragment = ''
# Auto-generated from DINAS paths projection
_dinas_projected_nbin_root=${lib.escapeShellArg paths.nbin_root}
  '';
  dinasPathsFile = pkgs.writeText "004_dinas-paths" dinasPathsFragment;
  shConfig = pkgs.runCommandLocal "sh_config" {
    passthru = {
      dinasProjectedNbinRoot = paths.nbin_root;
      inherit dinasPathsFragment;
    };
  } ''
    mkdir -p "$out"
    cp -R "${../shell/sh_config}/"* "$out/"
    chmod -R u+w "$out"
    cp ${dinasPathsFile} "$out/common/004_dinas-paths"
  '';
  zshrc = ../shell/zshrc;
in
{
  system.build.dinasShellConfig = shConfig;

  system.activationScripts.postActivation.text = ''
    echo "setting up shell configuration..."

    target_user=${lib.escapeShellArg primaryUser}
    user_home=${lib.escapeShellArg userHome}
    /usr/bin/sudo -n -H -u "$target_user" -- \
      /bin/sh ${./shell-writer.sh} \
      ${./user-home-writer-common.sh} \
      "$target_user" "$user_home" \
      "${zshrc}" "${bashrc}" "${bashProfile}" "${shConfig}" || exit 1
  '';
}
