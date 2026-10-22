#!/bin/sh

[ "$#" -eq 7 ] || {
  printf 'shell activation: expected seven fixed arguments\n' >&2
  exit 1
}

common=$1
target_user=$2
user_home=$3
zshrc_source=$4
bashrc_source=$5
bash_profile_source=$6
sh_config_source=$7

# shellcheck source=/dev/null
. "$common"
uh_initialize "$target_user" "$user_home"
uh_ensure_directory "$user_home/.local" "local state root" "$user_home" 0755

uh_link_managed_path "$user_home/.zshrc" "$zshrc_source" "$user_home" "zsh configuration link"
uh_link_managed_path "$user_home/.bashrc" "$bashrc_source" "$user_home" "bash configuration link"
uh_link_managed_path "$user_home/.bash_profile" "$bash_profile_source" "$user_home" "bash profile link"
uh_link_managed_path "$user_home/.sh_config" "$sh_config_source" "$user_home" "shared shell configuration link"
