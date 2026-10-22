# Every macOS application bundle core currently installs, recorded against the
# declaration that installs it. `applicationClaims` is derived from this list,
# and src/test/int/nix/application-claims.int.test.nix proves the list still
# matches what core actually declares.
#
# `surface` names the declaration surface the completeness check compares
# against:
#   homebrew.casks                      config.homebrew.casks
#   homebrew.masApps                    config.homebrew.masApps
#   environment.systemPackages          config.system.build.applications
#   system.activationScripts            bundle identifiers written by activation
#
# The activation surface is discovered by scanning the composed postActivation
# text for the two idioms core uses to stamp a generated bundle:
# `CFBundleIdentifier string "<id>"` and `create_kitty_launcher "<name>" "<id>"`.
# A future generator that stamps its identifier some other way must extend that
# scan in the integration test, not quietly bypass it.
#
# Identities come from installed metadata or official application build metadata,
# recorded in docs/evidence/core-office-identities.json for the three managed additions.
# GrandPerspective is core-owned diagnostically but remains externally installed,
# so it is not a managed application claim or a Homebrew installation declaration.
# A declaration does not establish acquisition or use.

let
  cask = package: artifact: id: declaration: {
    inherit artifact id package;
    provider = "homebrew-cask";
    surface = "homebrew.casks";
    inherit declaration;
  };

  # A cask that installs several bundles from one pkg payload. ADR 0002 section
  # 3.2 requires the suite marker so a token mapping to unrelated identities is
  # a deliberate declaration rather than a typo.
  suiteCask = package: artifact: id: declaration:
    (cask package artifact id declaration) // { suite = true; };

  masApp = package: artifact: id: declaration: {
    inherit artifact id package;
    provider = "mas";
    surface = "homebrew.masApps";
    inherit declaration;
  };

  nixApp = package: artifact: id: declaration: {
    inherit artifact id package;
    provider = "nix";
    surface = "environment.systemPackages";
    inherit declaration;
  };

  generated = provider: package: artifact: id: declaration: {
    inherit artifact id package provider;
    surface = "system.activationScripts";
    inherit declaration;
  };
  baseline = [
    (cask "adobe-acrobat-reader" "Adobe Acrobat Reader.app" "bundle:com.adobe.Reader" "modules/desktop-apps.nix")
    (cask "ente-auth" "Ente Auth.app" "bundle:io.ente.auth.mac" "modules/desktop-apps.nix")
    (cask "firefox" "Firefox.app" "bundle:org.mozilla.firefox" "modules/browsers.nix")
    (cask "keepassxc" "KeePassXC.app" "bundle:org.keepassxc.keepassxc" "modules/password-sync.nix")
    (cask "logi-options+" "logioptionsplus.app" "bundle:com.logi.optionsplus" "modules/desktop-apps.nix")
    (cask "logos" "Logos.app" "bundle:com.logos.desktop.logos" "modules/desktop-apps.nix")
    (cask "microsoft-teams" "Microsoft Teams.app" "bundle:com.microsoft.teams2" "modules/desktop-apps.nix")
    (cask "orbstack" "OrbStack.app" "bundle:dev.kdrag0n.MacVirt" "flake.nix")
    (cask "zoom" "zoom.us.app" "bundle:us.zoom.xos" "modules/desktop-apps.nix")

    (suiteCask "kitty" "kitty.app" "bundle:net.kovidgoyal.kitty" "modules/kitty.nix")


    (masApp "472790630" "Bible Study.app" "bundle:com.olivetree.BibleReaderMac" "modules/desktop-apps.nix")
    (masApp "1611378436" "Pure Paste.app" "bundle:com.sindresorhus.Pure-Paste" "modules/desktop-apps.nix")
    (masApp "6448461551" "Command X.app" "bundle:com.sindresorhus.Command-X" "modules/desktop-apps.nix")

    (nixApp "google-chrome" "Google Chrome.app" "bundle:com.google.Chrome" "modules/browsers.nix")
    (nixApp "qbittorrent" "qbittorrent.app" "bundle:org.qbittorrent.qBittorrent" "modules/desktop-apps.nix")

    # Activation copies pkgs.brave into a narrow single-site bundle, so the
    # application core installs here is nix-managed even though no .app reaches
    # the system application directory.
    (generated "nix" "brave" "Brave YouTube.app" "bundle:com.operator.brave-youtube" "modules/brave-youtube.nix")
    # The kitty launchers are stamped from the cask's own payload, so they carry
    # the cask token and join its suite group.
    ((generated "homebrew-cask" "kitty" "Kitty Bash.app" "bundle:com.operator.kitty.bash" "modules/kitty.nix") // { suite = true; })
    ((generated "homebrew-cask" "kitty" "Kitty Zsh.app" "bundle:com.operator.kitty.zsh" "modules/kitty.nix") // { suite = true; })
  ];

  # Casks core declares that install no application bundle, with the reason.
  # The completeness check requires every declared cask to appear either here or
  # as a claim, so a newly added GUI cask fails until it is claimed.
  nonApplicationCasks = { };

  # Command-line-only surfaces are excluded by ADR 0002 section 3.1. `mas` is
  # package-manager machinery and `yt-dlp` is a CLI, so neither is a claim.

  # Provenance this inventory records for the completeness check, stripped
  # before publication. Removing these two rather than keeping an allowlist of
  # contract fields is deliberate: an inventory key that belongs to neither set
  # reaches the validator's unknown-keys rule instead of being discarded
  # unnoticed.
  provenanceFields = [ "declaration" "surface" ];
  stockBrowsers = [
    (cask "librewolf" "LibreWolf.app" "bundle:io.gitlab.librewolf-community" "modules/browsers.nix")
    (cask "orion" "Orion.app" "bundle:com.kagi.kagimacOS" "modules/browsers.nix")
    (cask "brave-browser" "Brave Browser.app" "bundle:com.brave.Browser" "modules/browsers.nix")
  ];
  # Existing target-host office claims remain selected during the browser hold.
  office = [
    (cask "libreoffice" "LibreOffice.app" "bundle:org.libreoffice.script" "modules/desktop-apps.nix")
    (cask "onlyoffice" "ONLYOFFICE.app" "bundle:asc.onlyoffice.ONLYOFFICE" "modules/desktop-apps.nix")
    (cask "zettlr" "Zettlr.app" "bundle:com.zettlr.app" "modules/desktop-apps.nix")
  ];
  guiApplicationsFor = { hostName, system ? "aarch64-darwin", rolesR3 ? false, stockApplications ? false }:
    # Explicit acquisition matches the module on any host. Preserve the older
    # rolesR3 inventory gate; the core constructor selects only the current host.
    let selected = stockApplications || (rolesR3 && hostName == "example-primary"); in
    baseline
    ++ (if hostName == "example-primary" then office else [ ])
    ++ (if selected then stockBrowsers else [ ]);
in {
  inherit provenanceFields nonApplicationCasks guiApplicationsFor;
  # Legacy unparameterized exports describe the current target constructor.
  guiApplications = guiApplicationsFor { hostName = "example-primary"; stockApplications = true; };
}
