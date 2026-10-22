# nix-darwin-test-source-target: modules/codex-state.nix
# nix-darwin-test-source-target: lib/paths.nix
{ repoRoot, system }:

let
  flake = builtins.getFlake "path:${toString repoRoot}";
  lib = flake.legacyPackages.${system}.lib;
  module = import (repoRoot + "/modules/codex-state.nix");
  primaryUser = "fixture-user";
  userHome = "/fixture/primary user's home";
  render = projection: (module { localConfig.outPath = projection; } {
    inherit lib;
    config = {
      system.primaryUser = primaryUser;
      users.users.${primaryUser}.home = userHome;
    };
  }).system.activationScripts.postActivation.text;
  expected = root: ''
    echo "setting up Codex state paths..."

    target_user=${lib.escapeShellArg primaryUser}
    user_home=${lib.escapeShellArg userHome}
    agent_central_root=${lib.escapeShellArg root}
    /usr/bin/sudo -n -H -u "$target_user" -- \
      /bin/sh ${repoRoot + "/modules/codex-state-writer.sh"} \
      "$target_user" "$user_home" "$agent_central_root" || exit 1
  '';
in
{
  testCodexStateDefaultProjectionUsesPrimaryUserHome = {
    expr = render (repoRoot + "/config/nix-darwin/default");
    expected = expected "${userHome}/.local/nix-darwin/agent-central_dinas";
  };

  testCodexStateCustomProjectionQuotesEveryFixedArgument = {
    expr = render (repoRoot + "/src/test/fixtures/codex-state-paths");
    expected = expected "${userHome}/custom workspace's root/agent-central_dinas";
  };
}
