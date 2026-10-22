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
  fastfetchConfig = ../config/fastfetch/config.jsonc;
  fastfetchTerminalConfig = ../config/fastfetch/config-terminal.jsonc;
  fastfetchKittyConfig = ../config/fastfetch/config-kitty.jsonc;
  fastfetchLogo = ../config/fastfetch/logo.png;
  fastfetchWrapperSource = ../config/fastfetch/fastfetch-wrapper.sh;
  fastfetchWrapper = pkgs.writeTextFile {
    name = "fastfetch-profile-wrapper";
    destination = "/bin/fastfetch";
    executable = true;
    text = builtins.replaceStrings [ "@fastfetch@" ] [ "${pkgs.fastfetch}" ] (
      builtins.readFile fastfetchWrapperSource
    );
  };
in
{
  environment.systemPackages = [
    (pkgs.lib.hiPrio fastfetchWrapper)
    pkgs.fastfetch
  ];

  system.activationScripts.postActivation.text = ''
    echo "setting up fastfetch config..."

    target_user=${lib.escapeShellArg primaryUser}
    user_home=${lib.escapeShellArg userHome}
    /usr/bin/sudo -n -H -u "$target_user" -- \
      /bin/sh ${./fastfetch-writer.sh} \
      ${./user-home-writer-common.sh} \
      "$target_user" "$user_home" \
      ${fastfetchConfig} ${fastfetchTerminalConfig} \
      ${fastfetchKittyConfig} ${fastfetchLogo} fastfetch || exit 1
  '';
}
