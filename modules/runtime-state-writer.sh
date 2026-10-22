#!/bin/sh

[ "$#" -ge 7 ] || {
  printf 'runtime state activation: expected fixed identity, runtime, and command arguments\n' >&2
  exit 1
}

common=$1
target_user=$2
user_home=$3
runtime_name=$4
runtime_root=$5
shift 5
[ "$1" = -- ] || {
  printf 'runtime state activation: missing command separator\n' >&2
  exit 1
}
shift
[ "$#" -gt 0 ] || {
  printf 'runtime state activation: missing runtime command\n' >&2
  exit 1
}

# shellcheck source=/dev/null
. "$common"
uh_initialize "$target_user" "$user_home"

case "$runtime_name:$runtime_root" in
  "nvm:$user_home/.nvm")
    uh_ensure_directory "$runtime_root" "NVM state root" "$user_home" 0755
    ;;
  "rbenv:$user_home/.rbenv")
    uh_ensure_directory "$runtime_root" "rbenv state root" "$user_home" 0755
    uh_ensure_directory "$runtime_root/versions" "rbenv versions root" "$runtime_root" 0755
    ;;
  "pyenv:$user_home/.pyenv")
    uh_ensure_directory "$runtime_root" "pyenv state root" "$user_home" 0755
    uh_ensure_directory "$runtime_root/versions" "pyenv versions root" "$runtime_root" 0755
    uh_ensure_directory "$runtime_root/shims" "pyenv shims root" "$runtime_root" 0755
    ;;
  *)
    uh_fail "runtime root does not match the selected managed runtime"
    ;;
esac

exec "$@"
