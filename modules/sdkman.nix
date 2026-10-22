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
  sdkmanConfig = ../config/sdkman/config;
  sdkmanBootstrap = ../libexec/sdkman/bootstrap.sh;
  sdkmanEnsureJava = ../libexec/sdkman/ensure-java.sh;

  sdkmanPath = pkgs.lib.makeBinPath [
    pkgs.bash
    pkgs.coreutils
    pkgs.curl
    pkgs.gawk
    pkgs.gnugrep
    pkgs.gnused
    pkgs.unzip
    pkgs.zip
  ];
in
{
  environment.systemPackages = [
    pkgs.curl
    pkgs.unzip
    pkgs.zip
  ];

  system.activationScripts.postActivation.text = ''
    echo "setting up SDKMAN..."
    target_user=${lib.escapeShellArg primaryUser}
    user_home=${lib.escapeShellArg userHome}
    sdkman_dir="$user_home/.sdkman"
    sdkman_env_path="${sdkmanPath}:/usr/bin:/bin:/usr/sbin:/sbin"

    /usr/bin/sudo -n -H -u "$target_user" -- /usr/bin/env \
      HOME="$user_home" \
      SDKMAN_DIR="$sdkman_dir" \
      PATH="$sdkman_env_path" \
      ${pkgs.bash}/bin/bash ${./sdkman-config-writer.sh} \
      ${./user-home-writer-common.sh} \
      "$target_user" "$user_home" \
      ${sdkmanConfig} ${sdkmanBootstrap} ${sdkmanEnsureJava} \
      ${pkgs.bash}/bin/bash || exit 1
  '';
}
