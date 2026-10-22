# nix-darwin-test-source-target: modules/nixpkgs-policy.nix
{ repoRoot, system }:

let
  core = builtins.getFlake "path:${toString repoRoot}";
  pkgs = core.legacyPackages.${system};
  policy = import (repoRoot + "/modules/nixpkgs-policy.nix") { inherit (pkgs) lib; };
  allowed = policy.nixpkgs.config.allowUnfreePredicate;
  previousNames = [
    "google-chrome"
    "ngrok"
    "softnet"
    "tart"
    "claude-code"
    "codex"
    "antigravity-cli"
    "agent-clis"
  ];
in
{
  testVagrantPnameIsAdmitted = {
    expr = allowed { pname = "vagrant"; version = "2.4.9"; };
    expected = true;
  };

  testVersionedVagrantNameIsAdmitted = {
    expr = allowed { name = "vagrant-2.4.9"; };
    expected = true;
  };

  testPreviousAdmissionsArePreserved = {
    expr = builtins.all
      (name: allowed { pname = name; } && allowed { name = "${name}-1.2.3"; })
      previousNames;
    expected = true;
  };

  testUnrelatedAndPrefixedNamesRemainDenied = {
    expr = builtins.filter (name: allowed { inherit name; }) [
      "steam"
      "unrelated-unfree-1.0"
      "vagrant-extra"
      "vagrant-extra-2.4.9"
      "vagrantish"
      "vagrant-vmware-desktop"
      "google-chrome-extra"
      "codex-extra"
    ];
    expected = [ ];
  };

  testCoreVagrantDerivationEvaluatesWithoutOverride = {
    expr = builtins.tryEval (
      assert allowed pkgs.vagrant;
      builtins.deepSeq pkgs.vagrant.drvPath (pkgs.lib.getName pkgs.vagrant)
    );
    expected = { success = true; value = "vagrant"; };
  };
}
