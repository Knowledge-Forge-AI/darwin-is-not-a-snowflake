{ localConfig }:
{
  config,
  lib,
  ...
}:

let
  primaryUser = config.system.primaryUser;
  userHome = config.users.users.${primaryUser}.home;
  paths = (import ../lib/paths.nix { inherit lib; }).loadProjection {
    projection = localConfig;
    inherit userHome;
  };
  agentCentralRoot = "${paths.workspace_root}/agent-central_dinas";
in
{
  system.activationScripts.postActivation.text = ''
    echo "setting up Codex state paths..."

    target_user=${lib.escapeShellArg primaryUser}
    user_home=${lib.escapeShellArg userHome}
    agent_central_root=${lib.escapeShellArg agentCentralRoot}
    /usr/bin/sudo -n -H -u "$target_user" -- \
      /bin/sh ${./codex-state-writer.sh} \
      "$target_user" "$user_home" "$agent_central_root" || exit 1
  '';
}
