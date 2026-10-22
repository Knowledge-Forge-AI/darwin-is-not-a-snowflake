#!/usr/bin/env bash
set -euo pipefail

real_fastfetch="@fastfetch@/bin/fastfetch"
config_dir="${FASTFETCH_CONFIG_DIR:-$HOME/.config/fastfetch}"

for arg in "$@"; do
  case "$arg" in
    -c|--config|--config=*)
      exec "$real_fastfetch" "$@"
      ;;
    -h|--help|-v|--version|--version-raw|--list-*|--print-*)
      exec "$real_fastfetch" "$@"
      ;;
  esac
done

profile="${FASTFETCH_PROFILE:-auto}"
case "$profile" in
  auto|"")
    if [ "${TERM:-}" = "xterm-kitty" ] || [ -n "${KITTY_WINDOW_ID:-}" ]; then
      config="$config_dir/config-kitty.jsonc"
    else
      config="$config_dir/config-terminal.jsonc"
    fi
    ;;
  kitty)
    config="$config_dir/config-kitty.jsonc"
    ;;
  terminal|apple-terminal|ansi|ascii)
    config="$config_dir/config-terminal.jsonc"
    ;;
  upstream|none|raw)
    exec "$real_fastfetch" "$@"
    ;;
  *)
    printf 'fastfetch: unknown FASTFETCH_PROFILE=%s\n' "$profile" >&2
    printf 'fastfetch: expected auto, kitty, terminal, upstream, none, or raw\n' >&2
    exit 2
    ;;
esac

exec "$real_fastfetch" --config "$config" "$@"
