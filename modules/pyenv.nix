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
  ensurePython = ../libexec/pyenv/ensure-python.sh;
  pythonBuildDeps = [
    pkgs.bzip2
    pkgs.libffi
    pkgs.ncurses
    pkgs.openssl
    pkgs.readline
    pkgs.sqlite
    pkgs.xz
    pkgs.zlib
  ];
  pyenvPath = pkgs.lib.makeBinPath (
    [
      pkgs.bash
      pkgs.coreutils
      pkgs.findutils
      pkgs.gawk
      pkgs.gnugrep
      pkgs.gnumake
      pkgs.gnused
      pkgs.pkg-config
      pkgs.pyenv
      pkgs.curl
    ]
    ++ pythonBuildDeps
  );
  includeFlags = pkgs.lib.concatMapStringsSep " " (
    pkg: "-I${pkgs.lib.getDev pkg}/include"
  ) pythonBuildDeps;
  libFlags = pkgs.lib.concatMapStringsSep " " (pkg: "-L${pkgs.lib.getLib pkg}/lib") pythonBuildDeps;
  pkgConfigPath = pkgs.lib.concatMapStringsSep ":" (
    pkg: "${pkgs.lib.getDev pkg}/lib/pkgconfig"
  ) pythonBuildDeps;
in
{
  environment.systemPackages = [
    pkgs.pyenv
    pkgs.uv
  ]
  ++ pythonBuildDeps;

  system.activationScripts.postActivation.text = ''
    echo "setting up pyenv..."
    target_user=${lib.escapeShellArg primaryUser}
    user_home=${lib.escapeShellArg userHome}
    pyenv_root="$user_home/.pyenv"

    /usr/bin/sudo -n -H -u "$target_user" -- /usr/bin/env \
      HOME="$user_home" \
      PYENV_ROOT="$pyenv_root" \
      PATH="${pyenvPath}:/usr/bin:/bin:/usr/sbin:/sbin" \
      CPPFLAGS="${includeFlags}" \
      LDFLAGS="${libFlags}" \
      PKG_CONFIG_PATH="${pkgConfigPath}" \
      /bin/sh ${./runtime-state-writer.sh} \
      ${./user-home-writer-common.sh} \
      "$target_user" "$user_home" pyenv "$pyenv_root" -- \
      ${pkgs.bash}/bin/bash ${ensurePython} || exit 1
  '';
}
