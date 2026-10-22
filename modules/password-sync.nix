{ pkgs, ... }:

{
  # KeePassXC's nixpkgs app bundle is ad-hoc signed on macOS, which can break
  # Touch ID / Apple Watch Quick Unlock. The cask installs the upstream signed
  # macOS app while keeping it under nix-darwin management.
  #
  homebrew.casks = [
    "keepassxc"
  ];
}
