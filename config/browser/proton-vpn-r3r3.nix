# Core-owned future attended delivery, separate from retained historical R3R2.
let
  chromium = {
    id = "jplgfhpmjnbigmhklmmbgecoobifkmpa";
    storeUrl = "https://chromewebstore.google.com/detail/proton-vpn-fast-secure/jplgfhpmjnbigmhklmmbgecoobifkmpa";
    updateUrl = "https://clients2.google.com/service/update2/crx";
  };
  gecko = {
    id = "vpn@proton.ch";
    storeUrl = "https://addons.mozilla.org/en-US/firefox/addon/proton-vpn-firefox-extension/";
    updateUrl = "https://addons.mozilla.org/firefox/downloads/latest/proton-vpn-firefox-extension/latest.xpi";
    identity = "PENDING_FRESH_AMO_GUID_XPI_AND_BROWSER_SIGNING_READBACK";
  };
  entry = role: extension: status: extension // {
    inherit role status;
    owner = "core";
    purpose = "security";
    name = "Proton VPN";
    method = if builtins.elem role ["brave-daily" "librewolf"]
      then "ATTENDED_OFFICIAL_STORE" else status;
    automaticInstallation = false;
    automaticSignIn = false;
    autoConnect = false;
    policy = {};
  } // (if builtins.elem role ["brave-daily" "librewolf"]
    then { procedure = ./proton-vpn-install.md; } else {});
  preserved = role: id: purpose: name: {
    inherit role id purpose name;
    owner = "core";
    method = "PRESERVE_EXISTING_OR_COMPATIBILITY_HELD_INTENT";
    status = "NO_INSTALLATION_CHANGE";
    automaticInstallation = false; automaticSignIn = false; autoConnect = false;
    policy = {};
  };
in {
  schema = "browser-proton-vpn-r3r3-v1";
  selected = false;
  deployment = "HELD_SOURCE_RELEASE_REQUIRED";
  availabilityEffect = "SOURCE_OWNED_ATTENDED_INSTALLATION_PREPARED_RUNTIME_INACTIVE";
  extensions = { inherit chromium gecko; };
  contributions = [
    (entry "brave-daily" chromium "ATTENDED_INSTALLATION_HELD_PENDING_RELEASE_AND_READBACK")
    (entry "librewolf" gecko "ATTENDED_INSTALLATION_HELD_PENDING_IDENTITY_RELEASE_AND_READBACK")
    (entry "brave-youtube" chromium "WRAPPER_PROJECTION_HELD")
    (entry "orion" chromium "REQUESTED_COMPATIBILITY_HELD")
    (preserved "brave-youtube" "felphkbfjadmcejnibcmcncimlappdde" "convenience" "Wrapper Clear New Tab")
    (preserved "brave-daily" "pejdijmoenmkgeppbflobdenhhabjlaj" "credentials" "Apple Passwords")
    (preserved "librewolf" "uBlock0@raymondhill.net" "blocker" "Bundled uBlock Origin")
    (preserved "librewolf" "password-manager-firefox-extension@apple.com" "credentials" "Apple Passwords")
    (preserved "orion" "uBlock0@raymondhill.net" "blocker" "Compatibility-held uBlock Origin")
    (preserved "orion" "pejdijmoenmkgeppbflobdenhhabjlaj" "credentials" "Compatibility-held Apple Passwords")
  ];
  automation = {
    status = "AUTOMATION_EXTENSION_NOT_APPLICABLE_BY_DESIGN";
    extensionAvailable = false;
    persistentInstallTarget = false;
    extensionsEnabled = false;
    ordinaryAgentAvailabilityBlockedByVpn = false;
    customContext = "SEPARATE_OPTIONAL_APPROVED_TASK";
    chromeMcpFallback = "UNCHANGED_UNTIL_SEPARATELY_PROVEN_BRAVE_CUTOVER";
  };
}
