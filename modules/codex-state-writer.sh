#!/bin/sh

PATH=/usr/bin:/bin
export PATH

[ "$#" -eq 3 ] || {
  printf 'codex state activation: expected three fixed arguments\n' >&2
  exit 1
}

target_user=$1
user_home=$2
agent_central_dir=$3
effective_uid=

fail() {
  printf 'codex state activation: %s\n' "$*" >&2
  exit 1
}

require_owned_directory() {
  required_directory=$1
  required_description=$2

  if [ -L "$required_directory" ] || [ ! -d "$required_directory" ]; then
    fail "$required_description is not an ordinary directory; ownership repair is required before retry"
  fi
  if [ "$(stat -f '%u' "$required_directory")" != "$effective_uid" ]; then
    fail "$required_description is not owned by the target user; ownership repair is required before retry"
  fi
  if [ ! -w "$required_directory" ] || [ ! -x "$required_directory" ]; then
    fail "$required_description is not writable and searchable by the target user; ownership repair is required before retry"
  fi
}

ensure_owned_directory() {
  ensure_directory=$1
  ensure_description=$2
  ensure_parent=$3

  require_owned_directory "$ensure_parent" "parent of $ensure_description"
  if [ ! -e "$ensure_directory" ] && [ ! -L "$ensure_directory" ]; then
    mkdir -m 0755 "$ensure_directory" || fail "could not create $ensure_description"
  fi
  require_owned_directory "$ensure_directory" "$ensure_description"
  set_directory_mode "$ensure_directory" "$ensure_description"
}

set_directory_mode() {
  mode_directory=$1
  mode_description=$2
  ensure_mode=$(stat -f '%Lp' "$mode_directory") \
    || fail "could not inspect mode for $mode_description"
  intended_mode=755
  if [ "${3:-}" = preserve_restrictions ]; then
    intended_mode=$(printf '%o' "$((0$ensure_mode & 0755))")
  fi
  if [ "$ensure_mode" != "$intended_mode" ]; then
    chmod "$intended_mode" "$mode_directory" \
      || fail "could not set the intended mode on $mode_description"
  fi
}

unique_path() {
  unique_base=$1
  unique_candidate=$unique_base
  unique_suffix=0

  while [ -e "$unique_candidate" ] || [ -L "$unique_candidate" ]; do
    unique_suffix=$((unique_suffix + 1))
    unique_candidate="$unique_base.$unique_suffix"
  done
  printf '%s\n' "$unique_candidate"
}

timestamped_collision_path() {
  collision_base=$1
  collision_suffix=$2
  collision_timestamp=$(date +%Y%m%d%H%M%S) \
    || fail "could not timestamp a migration collision"
  unique_path "$collision_base.$collision_suffix.$collision_timestamp"
}

move_directory_contents() {
  source_dir=$1
  target_dir=$2
  collision_suffix=$3
  source_description=$4

  require_owned_directory "$source_dir" "$source_description"
  require_owned_directory "$target_dir" "migration destination for $source_description"

  if ! find "$source_dir" -mindepth 1 -maxdepth 1 -print |
    while IFS= read -r entry; do
      name=${entry##*/}
      destination="$target_dir/$name"
      if [ -e "$destination" ] || [ -L "$destination" ]; then
        destination=$(timestamped_collision_path "$target_dir/$name" "$collision_suffix") \
          || exit 1
      fi
      mv "$entry" "$destination" \
        || fail "could not migrate $source_description entry $name"
    done
  then
    fail "could not migrate $source_description contents"
  fi

  rmdir "$source_dir" \
    || fail "$source_description was not empty after migration"
}

migrate_optional_directory() {
  migration_source=$1
  migration_target=$2
  migration_suffix=$3
  migration_description=$4

  if [ ! -e "$migration_source" ] && [ ! -L "$migration_source" ]; then
    return 0
  fi
  if [ -L "$migration_source" ] || [ ! -d "$migration_source" ]; then
    fail "$migration_description is not an ordinary directory; operator repair is required before retry"
  fi
  move_directory_contents \
    "$migration_source" "$migration_target" "$migration_suffix" "$migration_description"
}

compatibility_backup_path() {
  compatibility_path=$1
  compatibility_base="$compatibility_path.before-nix-darwin"

  if [ -e "$compatibility_base" ] || [ -L "$compatibility_base" ]; then
    compatibility_timestamp=$(date +%Y%m%d%H%M%S) \
      || fail "could not timestamp a compatibility backup"
    compatibility_base="$compatibility_base.$compatibility_timestamp"
  fi
  unique_path "$compatibility_base"
}

backup_compatibility_path() {
  existing_path=$1
  existing_description=$2
  backup_path=$(compatibility_backup_path "$existing_path") || exit 1
  mv "$existing_path" "$backup_path" \
    || fail "could not preserve $existing_description"
}

effective_uid=$(/usr/bin/id -u) || exit 1
if [ "$effective_uid" -eq 0 ]; then
  fail "refusing to mutate user-home state as root"
fi
if [ "$(/usr/bin/id -un)" != "$target_user" ]; then
  fail "effective user does not match target user"
fi
case "$user_home" in
  /*) ;;
  *) fail "resolved user home is not absolute" ;;
esac
case "$user_home" in
  /|*//*|*/./*|*/.|*/../*|*/..) fail "resolved user home is not a safe canonical path" ;;
esac
case "$agent_central_dir" in
  /*) ;;
  *) fail "agent-central root is not absolute" ;;
esac
case "$agent_central_dir" in
  /|*//*|*/./*|*/.|*/../*|*/..) fail "agent-central root is not a safe canonical path" ;;
esac

umask 022

codex_dir="$user_home/.codex"
codex_vc_dir="$agent_central_dir/codex"
docs_target="$codex_vc_dir/usr"
docs_link="$user_home/codex"
legacy_usr_target="$codex_dir/usr"
legacy_docs_target="$codex_dir/user-docs"
legacy_docs_link="$user_home/codex-docs"
legacy_user_root="$codex_dir/$target_user"
legacy_docs_dir="$codex_dir/$target_user/codex"
codex_vc_mcp_dir="$codex_vc_dir/mcp"
serena_dir="$codex_vc_mcp_dir/serena"
serena_legacy_dir="$user_home/.serena"

require_owned_directory "$user_home" "resolved user home"
# The runtime is independently maintained. Never create it or own the human index.
if [ ! -e "$agent_central_dir" ] && [ ! -L "$agent_central_dir" ]; then
  fail "canonical Agent-Central checkout is missing; restore the selected runtime before retry"
fi
require_owned_directory "$agent_central_dir" "agent-central root"
# Remove excess permissions without widening an operator's private checkout.
set_directory_mode "$agent_central_dir" "agent-central root" preserve_restrictions
ensure_owned_directory "$codex_dir" "Codex state root" "$user_home"
ensure_owned_directory "$codex_vc_dir" "Codex source root" "$agent_central_dir"
ensure_owned_directory "$docs_target" "Codex docs destination" "$codex_vc_dir"
ensure_owned_directory "$codex_vc_mcp_dir" "Codex MCP root" "$codex_vc_dir"
ensure_owned_directory "$serena_dir" "Serena state destination" "$codex_vc_mcp_dir"

if [ -e "$legacy_user_root" ] || [ -L "$legacy_user_root" ]; then
  require_owned_directory "$legacy_user_root" "legacy per-user Codex root"
fi
migrate_optional_directory \
  "$legacy_docs_dir" "$docs_target" before-codex-docs-migration \
  "legacy per-user Codex docs directory"
migrate_optional_directory \
  "$legacy_docs_target" "$docs_target" before-codex-docs-migration \
  "legacy Codex user-docs directory"
migrate_optional_directory \
  "$legacy_usr_target" "$docs_target" before-codex-docs-migration \
  "legacy Codex usr directory"

serena_already_linked=0
if [ -L "$serena_legacy_dir" ]; then
  current=$(readlink "$serena_legacy_dir") \
    || fail "could not inspect the Serena compatibility link"
  if [ "$current" = "$serena_dir" ]; then
    serena_already_linked=1
  else
    backup_compatibility_path "$serena_legacy_dir" "the existing Serena compatibility link"
  fi
elif [ -d "$serena_legacy_dir" ]; then
  move_directory_contents \
    "$serena_legacy_dir" "$serena_dir" before-serena-state-migration \
    "legacy Serena directory"
elif [ -e "$serena_legacy_dir" ]; then
  backup_compatibility_path "$serena_legacy_dir" "the existing Serena compatibility path"
fi
if [ "$serena_already_linked" -eq 0 ]; then
  ln -s "$serena_dir" "$serena_legacy_dir" \
    || fail "could not create the Serena compatibility link"
fi

if [ -L "$legacy_docs_link" ]; then
  rm "$legacy_docs_link" || fail "could not remove the legacy Codex docs link"
elif [ -d "$legacy_docs_link" ]; then
  move_directory_contents \
    "$legacy_docs_link" "$docs_target" before-codex-docs-migration \
    "legacy codex-docs directory"
elif [ -e "$legacy_docs_link" ]; then
  backup_compatibility_path "$legacy_docs_link" "the legacy codex-docs conflict"
fi

docs_already_linked=0
if [ -L "$docs_link" ]; then
  current=$(readlink "$docs_link") \
    || fail "could not inspect the Codex compatibility link"
  if [ "$current" = "$docs_target" ]; then
    docs_already_linked=1
  else
    rm "$docs_link" || fail "could not remove the incorrect Codex compatibility link"
  fi
elif [ -e "$docs_link" ]; then
  backup_compatibility_path "$docs_link" "the existing Codex compatibility path"
fi
if [ "$docs_already_linked" -eq 0 ]; then
  ln -s "$docs_target" "$docs_link" \
    || fail "could not create the Codex compatibility link"
fi
