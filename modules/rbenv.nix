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
  ensureRuby = ../libexec/rbenv/ensure-ruby.sh;
  rbenvPath = pkgs.lib.makeBinPath [
    pkgs.bash
    pkgs.coreutils
    pkgs.findutils
    pkgs.gawk
    pkgs.gnugrep
  ];
in
{
  homebrew.brews = [
    "rbenv"
    "ruby-build"
  ];

  system.activationScripts.postActivation.text = ''
    echo "setting up rbenv..."
    target_user=${lib.escapeShellArg primaryUser}
    user_home=${lib.escapeShellArg userHome}
    rbenv_root="$user_home/.rbenv"

    /usr/bin/sudo -n -H -u "$target_user" -- /usr/bin/env \
      HOME="$user_home" \
      RBENV_ROOT="$rbenv_root" \
      PATH="${rbenvPath}:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/bin:/bin:/usr/sbin:/sbin" \
      /bin/sh ${./runtime-state-writer.sh} \
      ${./user-home-writer-common.sh} \
      "$target_user" "$user_home" rbenv "$rbenv_root" -- \
      ${pkgs.bash}/bin/bash ${ensureRuby} || exit 1
  '';
}
