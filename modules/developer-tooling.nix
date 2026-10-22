{ pkgs, ... }:

{
  environment.systemPackages = [
    pkgs.liquibase
    pkgs.malskanner
    pkgs.nil
    pkgs.nixd
    pkgs.nixfmt-rfc-style
    pkgs.ripgrep
    pkgs.shellcheck
    pkgs.shfmt
  ];

  # Adopts the installed formula; nixpkgs pins an older Semgrep. See
  # docs/architecture/source-audit-tools.md.
  homebrew.brews = [ "semgrep" ];
}
