{ userName, localConfig ? ../config/nix-darwin/default }:
{
  config,
  lib,
  pkgs,
  ...
}:

let
  primaryUser = config.system.primaryUser;
  userHome = config.users.users.${primaryUser}.home;
  kittyConfig = ../config/kitty/kitty.conf;
  kittyLauncherConfigTemplate = ../config/kitty/launcher.conf.template;
  kittyLauncherBashConfigFragment = ../config/kitty/launcher-bash.conf.fragment;
  kittyLauncherSessionTemplate = ../config/kitty/launcher.session.template;
  kittyLauncherPlistTemplate = ../config/kitty/launcher-info.plist.template;
  stock = config.dinas.kittyStockBash;
  kittyStockStyle = import ../lib/kitty-stock-style.nix { inherit lib; };
  fallbackStyle = kittyStockStyle.loadAndMergeStyle {
    inherit localConfig;
    samplePath = ../config/kitty/stock-bash-style.toml;
  };
  stockStyleScript = ../tools/kitty-stock-style.py;
  stockStyleDefault = pkgs.writeText "style-default.toml" (kittyStockStyle.renderToml fallbackStyle);
  # No global Bash or tool PATH change: the stock rc appends this tools tail.
  stockTools = pkgs.runCommandLocal "kitty-stock-bash-tools" { } ''
    mkdir -p "$out/bin"
    ln -s ${pkgs.fzf}/bin/fzf "$out/bin/fzf"
    ln -s ${pkgs.zoxide}/bin/zoxide "$out/bin/zoxide"
    ${lib.optionalString stock.direnv.enable ''
      mkdir -p "$out/direnv"
      printf 'source %s\n' ${pkgs.nix-direnv}/share/nix-direnv/direnvrc > "$out/direnv/direnvrc"
      cat > "$out/bin/direnv" <<EOF
#!${pkgs.runtimeShell}
export DIRENV_CONFIG=$out/direnv
exec ${pkgs.direnv}/bin/direnv "\$@"
EOF
      chmod +x "$out/bin/direnv"
    ''}
  '';
  # Generate scripts with their own output path at build time, avoiding a
  # recursive derivation reference. Every other replacement retains context.
  stockShare = pkgs.runCommandLocal "kitty-stock-bash" { } (
    let
      scripts = [
        [ "launch" ../config/kitty/stock-bash-launch.sh ]
        [ "stage2" ../config/kitty/stock-bash-stage2.sh ]
        [ "entry.bash" ../config/kitty/stock-bash-entry.bash ]
        [ "early.bash" ../config/kitty/stock-bash-early.bash ]
        [ "interactive.bash" ../config/kitty/stock-bash-interactive.bash ]
      ];
      # Use a token rather than depending on this derivation's own output.
      render = file: builtins.replaceStrings
        [ "#!/usr/bin/env bash" "@LOGIN@" "@BASH@" "@FALLBACK_BASH@" "@STAGE2@"
          "@ENTRY@" "@EARLY@" "@INTERACTIVE@" "@SYSTEM_PROFILE@" "@SYSTEM_BASHRC@"
          "@BLESH@" "@COMPLETION@" "@FZF@" "@ZOXIDE@" "@TOOLS@"
          "@DIRENV_ENABLED@" "@DIRENV@" "@STYLE_PYTHON@" "@STYLE_CLI@" "@STYLE_DEFAULT@" ]
        [ "#!${pkgs.bashInteractive}/bin/bash" "/usr/bin/login"
          "${pkgs.bashInteractive}/bin/bash" "/run/current-system/sw/bin/bash"
          "@STOCK_ROOT@/stage2" "@STOCK_ROOT@/entry.bash" "@STOCK_ROOT@/early.bash"
          "@STOCK_ROOT@/interactive.bash" "/etc/profile" "/etc/bashrc"
          "${pkgs.blesh}/share/blesh/ble.sh"
          "${pkgs.bash-completion}/etc/profile.d/bash_completion.sh"
          "${pkgs.fzf}/share/fzf" "${pkgs.zoxide}/bin/zoxide" "${stockTools}"
          (if stock.direnv.enable then "1" else "0") "${stockTools}/bin/direnv"
          "${pkgs.python3}/bin/python3" "${stockStyleScript}" "${stockStyleDefault}" ]
        (builtins.readFile file);
    in ''
      mkdir -p "$out/share/kitty-stock-bash"
      cp ${stockStyleDefault} "$out/share/kitty-stock-bash/style-default.toml"
      chmod 0644 "$out/share/kitty-stock-bash/style-default.toml"
      cp ${stockStyleScript} "$out/share/kitty-stock-bash/kitty-stock-style.py"
      chmod 0644 "$out/share/kitty-stock-bash/kitty-stock-style.py"
    '' + lib.concatMapStrings (item: ''
      substitute ${pkgs.writeText "kitty-stock-${builtins.head item}" (render (builtins.elemAt item 1))} \
        "$out/share/kitty-stock-bash/${builtins.head item}" \
        --replace-quiet '@STOCK_ROOT@' "$out/share/kitty-stock-bash"
    '') scripts + ''
      chmod +x "$out/share/kitty-stock-bash/launch" "$out/share/kitty-stock-bash/stage2"
      substitute ${pkgs.writeText "kitty-stock.conf" (builtins.replaceStrings
        [ "@BASH_FRAGMENT@" "@LAUNCH@" ]
        [ (builtins.readFile kittyLauncherBashConfigFragment) "@STOCK_ROOT@/launch" ]
        (builtins.readFile ../config/kitty/stock-kitty.conf.template))} \
        "$out/share/kitty-stock-bash/kitty-stock.conf" \
        --replace-fail '@STOCK_ROOT@' "$out/share/kitty-stock-bash"
    ''
  );
  stockLaunchServices = pkgs.writeText "kitty-stock-launch-services-cmdline"
    (builtins.replaceStrings [ "@STOCK_CONFIG@" ]
      [ (lib.escapeShellArg "${userHome}/.config/kitty/stock-bash.conf") ]
      (builtins.readFile ../config/kitty/stock-launch-services-cmdline.template));
  stockMode = if stock.enable then "enable" else "disable";
  stockActivationArgs = if stock.enable then
    "${../config/kitty/stock-bash.conf} ${stockLaunchServices}" else "unused unused";

in
{
  options.dinas.kittyStockBash = {
    enable = lib.mkEnableOption "enhanced Bash in stock Kitty; select with the pending combined browser batch";
    direnv.enable = lib.mkEnableOption "stock-only direnv/nix-direnv (each project still requires direnv allow)";
    installedStyle = lib.mkOption {
      type = lib.types.attrs;
      readOnly = true;
      description = "Resolved deterministic stock Kitty Bash style";
    };
  };

  config = {
    dinas.kittyStockBash.installedStyle = fallbackStyle;
    # Rollout coordination is validated by the batch operator and readback.
    # A future deliberate stock disable must remain independent of browsers.
    system.build.kittyStockBash = pkgs.writeText "kitty-stock-readback" (builtins.toJSON {
      enabled = stock.enable;
      activation = "${./kitty-stock-activate.sh}";
      maintenance = "${./kitty-app-writer.sh}";
      needsCombinedBrowserBatch = true;
    });

    environment.systemPackages = lib.optionals stock.enable [ stockShare ];
    environment.pathsToLink = lib.optionals stock.enable [ "/share/kitty-stock-bash" ];

    homebrew.casks = [
      "kitty"
    ];

    system.activationScripts.postActivation.text = ''
      echo "setting up kitty config..."
      target_user=${lib.escapeShellArg primaryUser}
      user_home=${lib.escapeShellArg userHome}
      kitty_zsh_bundle_id="com.operator.kitty.zsh"
      kitty_bash_bundle_id="com.operator.kitty.bash"
      /usr/bin/sudo -n -H -u "$target_user" -- \
        ${pkgs.bash}/bin/bash ${./kitty-stock-maintain.sh} \
        ${./kitty-app-writer.sh} ${./kitty-stock-activate.sh} ${stockMode} ${stockActivationArgs} \
        ${./user-home-writer-common.sh} \
        "$target_user" "$user_home" /Applications/kitty.app \
        ${kittyConfig} ${kittyLauncherConfigTemplate} \
        ${kittyLauncherBashConfigFragment} ${kittyLauncherSessionTemplate} \
        ${kittyLauncherPlistTemplate} ${pkgs.imagemagick}/bin/magick \
        ${pkgs.nerd-fonts.jetbrains-mono}/share/fonts/truetype/NerdFonts/JetBrainsMono/JetBrainsMonoNerdFontMono-Bold.ttf \
        /usr/bin/iconutil /usr/bin/xattr /usr/bin/codesign \
        /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
        "$kitty_zsh_bundle_id" "$kitty_bash_bundle_id" || exit 1
    '';
  };
}
