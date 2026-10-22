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
  ohMyZsh = "${pkgs.oh-my-zsh}/share/oh-my-zsh";
  ohMyZshCustom = ../shell/oh-my-zsh-custom;
in
{
  environment.systemPackages = [
    pkgs.oh-my-zsh
  ];

  system.activationScripts.postActivation.text = ''
    echo "setting up Oh My Zsh..."

    target_user=${lib.escapeShellArg primaryUser}
    user_home=${lib.escapeShellArg userHome}
    /usr/bin/sudo -n -H -u "$target_user" -- \
      /bin/sh ${./oh-my-zsh-writer.sh} \
      ${./user-home-writer-common.sh} \
      "$target_user" "$user_home" \
      "${ohMyZsh}" "${ohMyZshCustom}" || exit 1
  '';
}
