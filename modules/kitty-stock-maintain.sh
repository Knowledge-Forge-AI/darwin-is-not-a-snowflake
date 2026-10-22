#!/usr/bin/env bash
# One target-user transition; baseline writer and optional stock owner stay separate.
set -uo pipefail
[[ $# == 22 ]] || { printf 'kitty maintenance: expected 22 arguments\n' >&2; exit 1; }
writer=$1 stock_program=$2 mode=$3 stock_config=$4 stock_cmdline=$5
shift 5
"$BASH" "$writer" "$@" || exit 1
common=$1 target_user=$2 user_home=$3
stock_args=("$mode" "$common" "$target_user" "$user_home")
[[ $mode != enable ]] || stock_args+=("$stock_config" "$stock_cmdline")
if ! "$BASH" "$stock_program" "${stock_args[@]}"; then
  printf 'kitty stock %s did not complete; required combined rollout remains incomplete\n' "$mode" >&2
fi
