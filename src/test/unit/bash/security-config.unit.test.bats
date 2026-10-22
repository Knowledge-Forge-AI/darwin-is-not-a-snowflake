#!/usr/bin/env bats

@test "security-config.unit.test" {
set -euo pipefail

script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
repo_dir="$(cd "$script_dir/../../../.." && pwd)"

fail() {
  echo "test-security-config: $*" >&2
  exit 1
}

host_security_module="$repo_dir/modules/host-security.nix"
host_egress_rules="$repo_dir/config/security/host-egress.pf.conf"
host_egress_script="$repo_dir/libexec/host-security/host-egress-pf.sh"
host_stealth_script="$repo_dir/libexec/host-security/host-stealth-mode.sh"
host_stealth_gateway_parser="$repo_dir/libexec/host-security/physical-gateway.awk"
host_stealth_arp_parser="$repo_dir/libexec/host-security/arp-mac.awk"
video_session_module="$repo_dir/modules/video-session.nix"
video_session_sudoers="$repo_dir/config/power/video-session-pmset.sudoers"

[ -f "$host_security_module" ] || fail "missing modules/host-security.nix"
[ -f "$host_egress_rules" ] || fail "missing config/security/host-egress.pf.conf"
[ -f "$host_egress_script" ] || fail "missing libexec/host-security/host-egress-pf.sh"
[ -f "$host_stealth_script" ] || fail "missing libexec/host-security/host-stealth-mode.sh"
[ -f "$host_stealth_gateway_parser" ] || fail "missing libexec/host-security/physical-gateway.awk"
[ -f "$host_stealth_arp_parser" ] || fail "missing libexec/host-security/arp-mac.awk"
[ -f "$video_session_module" ] || fail "missing modules/video-session.nix"
[ -f "$video_session_sudoers" ] || fail "missing video session sudoers config"

bash -n "$host_egress_script"
bash -n "$host_stealth_script"

rg -F -q 'hostEgressRules = ../config/security/host-egress.pf.conf;' "$host_security_module" \
  || fail "host security module must source config/security/host-egress.pf.conf"
rg -F -q 'hostEgressScript = ../libexec/host-security/host-egress-pf.sh;' "$host_security_module" \
  || fail "host security module must source libexec/host-security/host-egress-pf.sh"
rg -F -q 'hostStealthModeScript = ../libexec/host-security/host-stealth-mode.sh;' "$host_security_module" \
  || fail "host security module must source libexec/host-security/host-stealth-mode.sh"
if rg -F -q 'pkgs.writeShellScript "host-' "$host_security_module"; then
  fail "host security module must not embed host security Bash source with pkgs.writeShellScript"
fi

rg -F -q 'blockAllIncoming = false;' "$host_security_module" \
  || fail "application firewall must not block all incoming traffic"
rg -F -q 'enableStealthMode = true;' "$host_security_module" \
  || fail "static stealth mode must default on before the dynamic controller runs"
rg -F -q 'touchIdAuth = true;' "$host_security_module" \
  || fail "sudo must keep Touch ID auth enabled"
rg -F -q 'reattach = true;' "$host_security_module" \
  || fail "sudo PAM must keep reattach enabled for terminal workflows"
rg -F -q 'system.defaults.loginwindow.GuestEnabled = false;' "$host_security_module" \
  || fail "Guest login must stay disabled"

for blocked_label in \
  'host-egress:block-mail-client-ports' \
  'host-egress:block-public-smb' \
  'host-egress:block-public-netbios' \
  'host-egress:block-public-smb6' \
  'host-egress:block-public-netbios6'
do
  rg -F -q "$blocked_label" "$host_egress_rules" \
    || fail "PF egress policy must include $blocked_label"
done

for local_table in \
  'table <host_egress_local_v4> const { 10/8, 172.16/12, 192.168/16, 169.254/16, 100.64/10 }' \
  'table <host_egress_local_v6> const { fc00::/7, fe80::/10 }'
do
  rg -F -q "$local_table" "$host_egress_rules" \
    || fail "PF egress policy must preserve local network table: $local_table"
done

rg -F -q 'trustedGatewayMacs = config.dinas.hostSecurity.trustedGatewayMacs;' "$host_security_module" \
  || fail "gateway hints must come from an explicit site-local module option"
rg -F -q 'default = [ ];' "$host_security_module" \
  || fail "no gateway may be trusted by default"
rg -F -q 'HOST_EGRESS_RULES = "${hostEgressRules}";' "$host_security_module" \
  || fail "PF launchd daemon must pass the managed PF rules path"
rg -F -q 'TRUSTED_GATEWAY_MACS = builtins.concatStringsSep " " trustedGatewayMacs;' "$host_security_module" \
  || fail "stealth mode launchd daemon must pass trusted gateway MACs"
rg -F -q 'HOST_STEALTH_MODE_LIBEXEC = "${hostStealthModeLibexec}";' "$host_security_module" \
  || fail "stealth mode launchd daemon must pass the managed libexec path"
rg -F -q 'pfctl_bin="${HOST_EGRESS_PFCTL:-/sbin/pfctl}"' "$host_egress_script" \
  || fail "PF helper must default to /sbin/pfctl while allowing test overrides"
rg -F -q '"$pfctl_bin" -q -a com.apple/nix-darwin-host-egress -f "$HOST_EGRESS_RULES"' "$host_egress_script" \
  || fail "PF helper must load the managed rules file"
rg -F -q '"$pfctl_bin" -E >/dev/null' "$host_egress_script" \
  || fail "PF helper must enable pf when needed"
rg -F -q ': "${TRUSTED_GATEWAY_MACS?TRUSTED_GATEWAY_MACS must be set}"' "$host_stealth_script" \
  || fail "stealth mode helper must require trusted gateway MACs from launchd"
rg -F -q 'firewall="${HOST_STEALTH_MODE_FIREWALL:-/usr/libexec/ApplicationFirewall/socketfilterfw}"' "$host_stealth_script" \
  || fail "stealth mode helper must default to Application Firewall while allowing test overrides"
rg -F -q 'netstat_bin="${HOST_STEALTH_MODE_NETSTAT:-/usr/sbin/netstat}"' "$host_stealth_script" \
  || fail "stealth mode helper must default to /usr/sbin/netstat while allowing test overrides"
rg -F -q 'arp_bin="${HOST_STEALTH_MODE_ARP:-/usr/sbin/arp}"' "$host_stealth_script" \
  || fail "stealth mode helper must default to /usr/sbin/arp while allowing test overrides"
rg -F -q 'normalize_mac()' "$host_stealth_script" \
  || fail "stealth mode helper must normalize gateway MACs before comparing"
rg -F -q '/usr/bin/awk -f "$host_stealth_mode_libexec/physical-gateway.awk"' "$host_stealth_script" \
  || fail "stealth mode helper must delegate physical gateway parsing to libexec awk"
rg -F -q '/usr/bin/awk -f "$host_stealth_mode_libexec/arp-mac.awk"' "$host_stealth_script" \
  || fail "stealth mode helper must delegate arp parsing to libexec awk"
if rg -F -q '$1 == "default" &&' "$host_stealth_script"; then
  fail "stealth mode helper must not embed the physical gateway awk program"
fi
if rg -F -q '/ at / { print $4; exit }' "$host_stealth_script"; then
  fail "stealth mode helper must not embed the arp mac awk program"
fi
rg -F -q 'launchd.daemons.host-stealth-mode' "$host_security_module" \
  || fail "host stealth mode must be managed by launchd"
rg -F -q 'StartInterval = 60;' "$host_security_module" \
  || fail "host stealth mode must re-check network context frequently"
rg -F -q 'launchd.daemons.host-egress-pf' "$host_security_module" \
  || fail "PF host egress rules must be managed by launchd"
rg -F -q 'StartInterval = 300;' "$host_security_module" \
  || fail "PF host egress rules must be periodically reloaded"

expected_sudoers='Cmnd_Alias PMSET_VIDEO_SESSION= /usr/bin/pmset -a disablesleep 1, /usr/bin/pmset -a disablesleep 0
%admin ALL=(ALL) NOPASSWD: PMSET_VIDEO_SESSION'
actual_sudoers="$(cat "$video_session_sudoers")"
[ "$actual_sudoers" = "$expected_sudoers" ] \
  || fail "video session sudoers file must allow only the exact pmset disable-sleep commands"

/usr/sbin/visudo -cf "$video_session_sudoers" >/dev/null \
  || fail "video session sudoers file must pass visudo validation"
rg -F -q 'install -m 0440 "${videoSessionSudoers}" "$sudoersTmp"' "$video_session_module" \
  || fail "video session activation must stage sudoers with 0440 mode"
rg -F -q '/usr/sbin/visudo -cf "$sudoersTmp"' "$video_session_module" \
  || fail "video session activation must validate sudoers before installing"
rg -F -q 'install -m 0440 "$sudoersTmp" /private/etc/sudoers.d/video_session_pmset' "$video_session_module" \
  || fail "video session activation must install sudoers with 0440 mode"
rg -F -q 'rm -f /private/etc/sudoers.d/amphetamine_PowerProtect' "$video_session_module" \
  || fail "video session activation must remove stale Amphetamine sudoers rule"

gateway_sample='default            10.0.0.1         UGScg               en0
default            link#20          UCSIg             utun5
default            172.16.0.1       UGScg             ipsec0
10.0.0/24          link#6           UCS               en0'
gateway="$(printf '%s\n' "$gateway_sample" | /usr/bin/awk -f "$host_stealth_gateway_parser")"
[ "$gateway" = "10.0.0.1" ] \
  || fail "physical gateway parser must ignore VPN/link routes; got $gateway"

arp_sample='? (10.0.0.1) at 02.00.00.00.00.01 on en0 ifscope [ethernet]'
arp_mac="$(printf '%s\n' "$arp_sample" | /usr/bin/awk -f "$host_stealth_arp_parser")"
[ "$arp_mac" = "02.00.00.00.00.01" ] \
  || fail "arp mac parser must print the arp hardware address; got $arp_mac"

echo "security config tests passed"

}
