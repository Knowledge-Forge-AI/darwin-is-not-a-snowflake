#!/bin/sh

[ "$#" -eq 8 ] || {
  printf 'fastfetch activation: expected eight fixed arguments\n' >&2
  exit 1
}

common=$1
target_user=$2
user_home=$3
default_config=$4
terminal_config=$5
kitty_config=$6
logo=$7
managed_dir_name=$8

[ "$managed_dir_name" = fastfetch ] || {
  printf 'fastfetch activation: unexpected managed directory name\n' >&2
  exit 1
}

# shellcheck source=/dev/null
. "$common"
uh_initialize "$target_user" "$user_home"
uh_ensure_directory "$user_home/.config" "user config root" "$user_home" 0755
fastfetch_dir="$user_home/.config/$managed_dir_name"
uh_ensure_directory "$fastfetch_dir" "fastfetch config root" "$user_home/.config" 0755

for stale_logo in logo.ansi logo.ascii; do
  if [ -L "$fastfetch_dir/$stale_logo" ]; then
    /bin/rm "$fastfetch_dir/$stale_logo" \
      || uh_fail "could not remove stale managed $stale_logo link"
  fi
done

uh_link_managed_path "$fastfetch_dir/config.jsonc" "$default_config" "$fastfetch_dir" "fastfetch default config"
uh_link_managed_path "$fastfetch_dir/config-terminal.jsonc" "$terminal_config" "$fastfetch_dir" "fastfetch Terminal config"
uh_link_managed_path "$fastfetch_dir/config-kitty.jsonc" "$kitty_config" "$fastfetch_dir" "fastfetch Kitty config"
uh_link_managed_path "$fastfetch_dir/logo.png" "$logo" "$fastfetch_dir" "fastfetch logo"
