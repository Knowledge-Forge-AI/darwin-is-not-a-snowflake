{ config, lib, pkgs, ... }:

{
  environment.systemPackages = [
    pkgs.qbittorrent
  ];

  homebrew.brews = [
    "mas"
  ];

  homebrew.casks = [
    "adobe-acrobat-reader"
    "ente-auth"
    "logi-options+"
    "logos"
    "microsoft-teams"
    "zoom"
  ] ++ lib.optionals (config.networking.hostName == "example-primary") [
    "libreoffice"
    "onlyoffice"
    "zettlr"
  ];

  homebrew.masApps = {
    "Bible Study" = 472790630;
    "Command X" = 6448461551;
    "Pure Paste" = 1611378436;
  };
}
