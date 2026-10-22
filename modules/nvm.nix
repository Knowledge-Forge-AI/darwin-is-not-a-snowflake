{ userName }:
{
  config,
  lib,
  pkgs,
  ...
}:

let
  primaryUser = config.system.primaryUser;
  userHome = config.users.users.${primaryUser}.home;
  ensureNode = ../libexec/nvm/ensure-node.sh;
  nvmPath = pkgs.lib.makeBinPath [
    pkgs.bash
    pkgs.coreutils
    pkgs.curl
    pkgs.findutils
    pkgs.gnugrep
    pkgs.gnutar
    pkgs.xz
  ];
in
{
  homebrew.brews = [
    "nvm"
  ];

  system.activationScripts.postActivation.text = ''
    echo "setting up nvm..."
    target_user=${lib.escapeShellArg primaryUser}
    user_home=${lib.escapeShellArg userHome}
    nvm_dir="$user_home/.nvm"

    /usr/bin/sudo -n -H -u "$target_user" -- /usr/bin/env \
      HOME="$user_home" \
      NVM_DIR="$nvm_dir" \
      PATH="${nvmPath}:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/bin:/bin:/usr/sbin:/sbin" \
      /bin/sh ${./runtime-state-writer.sh} \
      ${./user-home-writer-common.sh} \
      "$target_user" "$user_home" nvm "$nvm_dir" -- \
      ${pkgs.bash}/bin/bash ${ensureNode} || exit 1
  '';
}
