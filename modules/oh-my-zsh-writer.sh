#!/bin/sh

[ "$#" -eq 5 ] || {
  printf 'Oh My Zsh activation: expected five fixed arguments\n' >&2
  exit 1
}

common=$1
target_user=$2
user_home=$3
oh_my_zsh_source=$4
oh_my_zsh_custom_source=$5

# shellcheck source=/dev/null
. "$common"
uh_initialize "$target_user" "$user_home"
uh_link_managed_path "$user_home/.oh-my-zsh" "$oh_my_zsh_source" "$user_home" "Oh My Zsh link"
uh_link_managed_path "$user_home/.oh-my-zsh-custom" "$oh_my_zsh_custom_source" "$user_home" "Oh My Zsh custom link"
uh_ensure_directory "$user_home/.cache" "user cache root" "$user_home" 0755
uh_ensure_directory "$user_home/.cache/oh-my-zsh" "Oh My Zsh cache root" "$user_home/.cache" 0755
