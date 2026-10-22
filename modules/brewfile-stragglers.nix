{ pkgs, ... }:

{
  environment.systemPackages = [
    pkgs.corepack
    pkgs.iproute2mac
    pkgs.liblinear
  ];

  fonts.packages = [
    pkgs.julia-mono
    pkgs.jetbrains-mono
    pkgs.nerd-fonts.fira-code
    pkgs.nerd-fonts.iosevka
    pkgs.nerd-fonts.jetbrains-mono
    pkgs.ubuntu-sans-mono
  ];

  homebrew.brews = [
    "kcov"
  ];
}
