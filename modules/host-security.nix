{ config, lib, pkgs, ... }:

let
  trustedGatewayMacs = config.dinas.hostSecurity.trustedGatewayMacs;

  hostEgressRules = ../config/security/host-egress.pf.conf;
  hostEgressScript = ../libexec/host-security/host-egress-pf.sh;
  hostStealthModeScript = ../libexec/host-security/host-stealth-mode.sh;
  hostStealthModeLibexec = ../libexec/host-security;
in
{
  options.dinas.hostSecurity.trustedGatewayMacs = lib.mkOption {
    type = lib.types.listOf (lib.types.strMatching "[0-9a-f]{2}(:[0-9a-f]{2}){5}");
    default = [ ];
    description = "Explicit site-local gateway hints for stealth-mode convenience. MAC addresses are spoofable and are not network authentication.";
  };

  config = {
    networking.applicationFirewall = {
      enable = true;
      blockAllIncoming = false;
      allowSigned = true;
      allowSignedApp = true;
      enableStealthMode = true;
    };

    system.defaults.loginwindow.GuestEnabled = false;

    security.pam.services.sudo_local = {
      touchIdAuth = true;
      reattach = true;
    };

    launchd.daemons.host-stealth-mode = {
      serviceConfig = {
        ProgramArguments = [
          "${pkgs.bash}/bin/bash"
          "${hostStealthModeScript}"
        ];
        EnvironmentVariables = {
          TRUSTED_GATEWAY_MACS = builtins.concatStringsSep " " trustedGatewayMacs;
          HOST_STEALTH_MODE_LIBEXEC = "${hostStealthModeLibexec}";
        };
        RunAtLoad = true;
        StartInterval = 60;
        StandardOutPath = "/var/log/host-stealth-mode.log";
        StandardErrorPath = "/var/log/host-stealth-mode.log";
      };
    };

    launchd.daemons.host-egress-pf = {
      serviceConfig = {
        ProgramArguments = [
          "${pkgs.bash}/bin/bash"
          "${hostEgressScript}"
        ];
        EnvironmentVariables = {
          HOST_EGRESS_RULES = "${hostEgressRules}";
        };
        RunAtLoad = true;
        StartInterval = 300;
        StandardOutPath = "/var/log/host-egress-pf.log";
        StandardErrorPath = "/var/log/host-egress-pf.log";
      };
    };
  };
}
