#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 7 ]; then
  printf 'SDKMAN activation: expected seven fixed arguments\n' >&2
  exit 1
fi

common=$1
target_user=$2
user_home=$3
managed_config=$4
bootstrap_script=$5
ensure_java_script=$6
bash_bin=$7

# shellcheck source=/dev/null
source "$common"
uh_initialize "$target_user" "$user_home"

"$bash_bin" "$bootstrap_script"

sdkman_dir=${SDKMAN_DIR:?SDKMAN_DIR must be set}
if [ "$sdkman_dir" != "$user_home/.sdkman" ]; then
  uh_fail "SDKMAN_DIR does not match the configured user home"
fi
uh_require_owned_directory "$sdkman_dir" "SDKMAN state root"
uh_ensure_directory "$sdkman_dir/etc" "SDKMAN config root" "$sdkman_dir" 0755

sdkman_config_file="$sdkman_dir/etc/config"
if [ -L "$sdkman_config_file" ]; then
  current=$(/usr/bin/readlink "$sdkman_config_file")
  if [ "$current" != "$managed_config" ]; then
    /bin/rm "$sdkman_config_file"
  fi
elif [ -f "$sdkman_config_file" ]; then
  if ! /usr/bin/cmp -s "$sdkman_config_file" "$managed_config"; then
    backup=$(uh_unique_backup_path "$sdkman_config_file")
    /bin/cp -p "$sdkman_config_file" "$backup"
  fi
  /bin/rm "$sdkman_config_file"
elif [ -e "$sdkman_config_file" ]; then
  uh_preserve_path "$sdkman_config_file" "SDKMAN config conflict"
fi
if [ ! -L "$sdkman_config_file" ]; then
  /bin/ln -s "$managed_config" "$sdkman_config_file"
fi

"$bash_bin" "$ensure_java_script"
