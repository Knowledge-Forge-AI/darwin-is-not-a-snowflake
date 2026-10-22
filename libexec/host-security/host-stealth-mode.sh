#!/usr/bin/env bash
set -euo pipefail

: "${TRUSTED_GATEWAY_MACS?TRUSTED_GATEWAY_MACS must be set}"

firewall="${HOST_STEALTH_MODE_FIREWALL:-/usr/libexec/ApplicationFirewall/socketfilterfw}"
netstat_bin="${HOST_STEALTH_MODE_NETSTAT:-/usr/sbin/netstat}"
arp_bin="${HOST_STEALTH_MODE_ARP:-/usr/sbin/arp}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
host_stealth_mode_libexec="${HOST_STEALTH_MODE_LIBEXEC:-$script_dir}"

normalize_mac() {
  /usr/bin/tr '[:upper:].-' '[:lower:]::' | /usr/bin/sed 's/::*/:/g; s/:$//'
}

physical_gateway="$(
  "$netstat_bin" -rn -f inet |
    /usr/bin/awk -f "$host_stealth_mode_libexec/physical-gateway.awk"
)"

gateway_mac=""
if [ -n "$physical_gateway" ]; then
  gateway_mac="$(
    "$arp_bin" -n "$physical_gateway" 2>/dev/null |
      /usr/bin/awk -f "$host_stealth_mode_libexec/arp-mac.awk" |
      normalize_mac ||
      true
  )"
fi

on_home_network=0
for trusted_mac in $TRUSTED_GATEWAY_MACS; do
  if [ "$gateway_mac" = "$trusted_mac" ]; then
    on_home_network=1
    break
  fi
done

if [ "$on_home_network" -eq 1 ]; then
  desired=off
else
  desired=on
fi

current="$("$firewall" --getstealthmode 2>/dev/null || true)"
case "$current" in
  *" is on"*|*" enabled"*) current_state=on ;;
  *) current_state=off ;;
esac

if [ "$current_state" != "$desired" ]; then
  "$firewall" --setstealthmode "$desired"
  echo "host-stealth-mode: set stealth mode $desired; gateway=$physical_gateway mac=$gateway_mac"
fi
