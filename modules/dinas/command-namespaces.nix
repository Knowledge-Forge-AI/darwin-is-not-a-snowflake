{ localConfig }:
{
  config,
  lib,
  pkgs,
  ...
}:

let
  primaryUser = config.system.primaryUser;
  userHome = config.users.users.${primaryUser}.home;
  paths = (import ../../lib/paths.nix { inherit lib; }).loadProjection {
    projection = localConfig;
    inherit userHome;
  };

  namespaceSubmodule = lib.types.submodule ({ name, ... }: {
    options = {
      name = lib.mkOption {
        type = lib.types.str;
        default = name;
        description = "Command namespace identifier";
      };
      package = lib.mkOption {
        type = lib.types.package;
        description = "Package providing the namespace commands";
      };
      binSubdir = lib.mkOption {
        type = lib.types.str;
        default = "bin";
        description = "Relative subdirectory containing executables";
      };
    };
  });

  cfg = config.dinas.commandNamespaces;
  devCfg = config.dinas.devNamespaces;

  safeNameRegex = "^[a-zA-Z0-9_-]+$";
  safeSubdirRegex = "^[a-zA-Z0-9._+@-]+(/[a-zA-Z0-9._+@-]+)*$";
  validateNamespaces = namespaces:
    let
      values = builtins.attrValues namespaces;
      names = builtins.map (ns: ns.name) values;
      validSubdir = value:
        builtins.match safeSubdirRegex value != null
        && builtins.all (segment: segment != "." && segment != "..")
          (lib.splitString "/" value);
    in
    builtins.length names == builtins.length (lib.unique names)
    && builtins.all
      (ns: builtins.match safeNameRegex ns.name != null && validSubdir ns.binSubdir)
      values;

  namespacesJson = builtins.toJSON (
    lib.mapAttrs' (_: ns: lib.nameValuePair ns.name "${ns.package}/${ns.binSubdir}") cfg
  );

  devNamespacesJson = builtins.toJSON (
    lib.mapAttrs' (_: ns: lib.nameValuePair ns.name "${ns.package}/${ns.binSubdir}") devCfg
  );
in
{
  options = {
    dinas = {
      extraCommandPackages = lib.mkOption {
        type = lib.types.listOf lib.types.package;
        default = [ ];
        description = "Additional command packages in the existing DINAS namespace; duplicate executable names fail the build.";
      };
      commandNamespaces = lib.mkOption {
        type = lib.types.attrsOf namespaceSubmodule;
        default = { };
        description = "Activated system command namespaces";
      };
      devNamespaces = lib.mkOption {
        type = lib.types.attrsOf namespaceSubmodule;
        default = { };
        description = "Explicit development candidate namespaces";
      };
    };
  };

  config = {
    assertions = [
      {
        assertion = validateNamespaces cfg;
        message = "dinas.commandNamespaces contains invalid namespace identifiers or binSubdir";
      }
      {
        assertion = validateNamespaces devCfg;
        message = "dinas.devNamespaces contains invalid namespace identifiers or binSubdir";
      }
    ];

    dinas.commandNamespaces.dinas = {
      package = if config.dinas.extraCommandPackages == [ ] then pkgs.dinasCommands else pkgs.buildEnv {
        name = "dinas-composed-commands";
        paths = [ pkgs.dinasCommands ] ++ config.dinas.extraCommandPackages;
        pathsToLink = [ "/bin" "/libexec" ];
        ignoreCollisions = false;
      };
      binSubdir = "bin";
    };

    dinas.deploymentEffects.preflightParticipants = [
      {
        name = "dinas-reconcile";
        owner = "dinas";
        kind = "command";
        protocol = "command";
        executable = "${pkgs.python3}/bin/python3";
        arguments = [
          "${pkgs.dinasCommands}/bin/dinas-reconcile"
          "--nbin-root"
          paths.nbin_root
          "--nbin-dev-root"
          paths.nbin_dev_root
          "--ai-index-root"
          paths.ai_index_root
          "--namespaces-json"
          namespacesJson
          "--dev-namespaces-json"
          devNamespacesJson
          "--check"
        ];
        timeout = 60;
        readOnly = true;
        classification = "read-only";
      }
    ];

    system.activationScripts.postActivation.text = lib.mkAfter ''
      echo "reconciling DINAS command namespaces and AI index..."
      target_user=${lib.escapeShellArg primaryUser}
      /usr/bin/sudo -n -H -u "$target_user" -- \
        ${pkgs.python3}/bin/python3 ${pkgs.dinasCommands}/bin/dinas-reconcile \
        --nbin-root ${lib.escapeShellArg paths.nbin_root} \
        --nbin-dev-root ${lib.escapeShellArg paths.nbin_dev_root} \
        --ai-index-root ${lib.escapeShellArg paths.ai_index_root} \
        --namespaces-json '${namespacesJson}' \
        --dev-namespaces-json '${devNamespacesJson}'
    '';
  };
}
