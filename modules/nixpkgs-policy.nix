{ lib, ... }:

{
  nixpkgs.config.allowUnfreePredicate = pkg:
    builtins.elem (lib.getName pkg) [
      "google-chrome"
      "ngrok"
      "softnet"
      "tart"
      "claude-code"
      "codex"
      "antigravity-cli"
      "agent-clis"
      "vagrant"
    ];
}
