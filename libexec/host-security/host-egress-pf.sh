#!/usr/bin/env bash
set -euo pipefail

: "${HOST_EGRESS_RULES:?HOST_EGRESS_RULES must be set}"

pfctl_bin="${HOST_EGRESS_PFCTL:-/sbin/pfctl}"

pf_output="$({ "$pfctl_bin" -q -a com.apple/nix-darwin-host-egress -f "$HOST_EGRESS_RULES"; } 2>&1)" || {
  printf '%s\n' "$pf_output" >&2
  exit 1
}

if [ -n "$pf_output" ]; then
  printf '%s\n' "$pf_output" |
    /usr/bin/grep -v \
      -e '^pfctl: Use of -f option' \
      -e '^present in the main ruleset' \
      -e '^See /etc/pf.conf' >&2 ||
    true
fi

if ! "$pfctl_bin" -s info | /usr/bin/grep -q '^Status: Enabled'; then
  "$pfctl_bin" -E >/dev/null
fi
