#!/usr/bin/env zsh
set -euo pipefail

managed_user_home() {
  if [ -n "${CODEX_RESTORE_USER_HOME:-}" ]; then
    print -r -- "$CODEX_RESTORE_USER_HOME"
    return
  fi

  if [ -n "${CODEX_HOME:-}" ]; then
    print -r -- "${CODEX_HOME:h}"
    return
  fi

  if [ -n "${HOME:-}" ] && [ "$HOME" != "/var/root" ]; then
    print -r -- "$HOME"
    return
  fi

  print -r -- "/Users/operator"
}

usage() {
  cat <<'USAGE'
Usage: codex-restore --confirm [options]

Restore ~/.codex from the Codex Arq backup plan and run Codex tooling self-heal.

Options:
  --codex-home PATH       Restore target. Defaults to ~/.codex for the managed user.
  --backup-root PATH      Required local streamed Arq backup root.
  --plan-uuid UUID        Required Arq backup plan UUID.
  --folder-uuid UUID      Required Arq backup folder UUID.
  --target-name NAME      Temporary arq_restore target nickname.
  --work-dir PATH         Scratch directory for the Arq restore.
  --skip-self-heal        Do not run codex-tooling-self-heal.
  --keep-work-dir         Keep the scratch directory after success.
  -h, --help              Show this help.

Environment overrides:
  CODEX_RESTORE_USER_HOME
  CODEX_RESTORE_CODEX_HOME
  CODEX_RESTORE_ARQ_BACKUP_ROOT
  CODEX_RESTORE_ARQ_RESTORE_BIN
  CODEX_RESTORE_PLAN_UUID
  CODEX_RESTORE_FOLDER_UUID
  CODEX_RESTORE_TARGET_NICKNAME
  CODEX_RESTORE_WORK_DIR
  CODEX_RESTORE_SELF_HEAL_BIN
  CODEX_RESTORE_TIMESTAMP
USAGE
}

fail() {
  print -u2 -- "codex-restore: $*"
  exit 1
}

usage_error() {
  print -u2 -- "codex-restore: $*"
  print -u2 -- "codex-restore: pass --confirm to restore Codex state"
  exit 2
}

require_executable() {
  local path="$1"
  local label="$2"

  if ! command -v "$path" >/dev/null 2>&1 && [ ! -x "$path" ]; then
    fail "$label is not executable or on PATH: $path"
  fi
}

confirm_codex_restore() {
  local reply

  if [ "$confirm" -eq 1 ]; then
    return
  fi

  print -r -- "This will restore Codex state into: $codex_home"
  print -r -- "Any existing directory will be moved aside before replacement."
  printf "Type RESTORE to continue: "
  read -r reply || reply=""
  [ "$reply" = "RESTORE" ] || usage_error "restore is destructive"
}

restore_codex_snapshot() {
  local restore_root="$work_dir/restored"
  local restored_source="$restore_root/$folder_uuid"
  local restore_status

  /bin/mkdir -p "$restore_root"
  "$arq_restore_bin" addtarget "$target_name" local "$backup_root" >/dev/null
  target_added=1

  set +e
  (
    cd "$restore_root"
    "$arq_restore_bin" restore "$target_name" "$plan_uuid" "$folder_uuid"
  )
  restore_status="$?"
  set -e
  if [ "$restore_status" -ne 0 ]; then
    delete_arq_target
    return "$restore_status"
  fi

  [ -d "$restored_source" ] || fail "Arq restore did not create $restored_source"
  [ -f "$restored_source/config.toml" ] || fail "restored Codex tree is missing config.toml"
  restored_source_path="$restored_source"
}

install_restored_codex() {
  local restored_source="$1"
  local parent="${codex_home:h}"
  local existing_backup="${codex_home}.before-codex-restore.${timestamp}"

  /bin/mkdir -p "$parent"
  if [ -e "$codex_home" ] || [ -L "$codex_home" ]; then
    [ ! -e "$existing_backup" ] && [ ! -L "$existing_backup" ] \
      || fail "backup path already exists: $existing_backup"
    /bin/mv "$codex_home" "$existing_backup"
    print -r -- "Existing Codex state moved to: $existing_backup"
  fi

  /bin/mv "$restored_source" "$codex_home"
}

run_self_heal() {
  [ "$self_heal" -eq 1 ] || return 0

  require_executable "$self_heal_bin" "Codex tooling self-heal"
  CODEX_HOME="$codex_home" "$self_heal_bin" --apply
}

delete_arq_target() {
  if [ "$target_added" -eq 1 ]; then
    "$arq_restore_bin" deletetarget "$target_name" >/dev/null 2>&1 || true
    target_added=0
  fi
}

cleanup() {
  delete_arq_target

  if [ "$cleanup_work_dir" -eq 1 ] && [ "$restore_succeeded" -eq 1 ] && [ "$keep_work_dir" -eq 0 ]; then
    /bin/rm -rf "$work_dir"
  fi
}

default_user_home="$(managed_user_home)"
script_dir="$(cd "${0:h}" && /bin/pwd -P)"
repo_root="$(cd "$script_dir/../.." && /bin/pwd -P)"

codex_home="${CODEX_RESTORE_CODEX_HOME:-${CODEX_HOME:-$default_user_home/.codex}}"
backup_root="${CODEX_RESTORE_ARQ_BACKUP_ROOT:-}"
arq_restore_bin="${CODEX_RESTORE_ARQ_RESTORE_BIN:-arq_restore}"
plan_uuid="${CODEX_RESTORE_PLAN_UUID:-}"
folder_uuid="${CODEX_RESTORE_FOLDER_UUID:-}"
target_name="${CODEX_RESTORE_TARGET_NICKNAME:-codex-restore-$$}"
self_heal_bin="${CODEX_RESTORE_SELF_HEAL_BIN:-$repo_root/bin/codex-tooling-self-heal}"
timestamp="${CODEX_RESTORE_TIMESTAMP:-$(/bin/date -u '+%Y%m%dT%H%M%SZ')}"
confirm=0
self_heal=1
keep_work_dir=0
target_added=0
restore_succeeded=0
restored_source_path=""

if [ -n "${CODEX_RESTORE_WORK_DIR:-}" ]; then
  work_dir="$CODEX_RESTORE_WORK_DIR"
  cleanup_work_dir=0
else
  work_dir=""
  cleanup_work_dir=0
fi

trap cleanup EXIT

while [ "$#" -gt 0 ]; do
  case "$1" in
    --codex-home)
      shift
      [ "$#" -gt 0 ] || fail "--codex-home requires a path"
      codex_home="$1"
      ;;
    --backup-root)
      shift
      [ "$#" -gt 0 ] || fail "--backup-root requires a path"
      backup_root="$1"
      ;;
    --plan-uuid)
      shift
      [ "$#" -gt 0 ] || fail "--plan-uuid requires a value"
      plan_uuid="$1"
      ;;
    --folder-uuid)
      shift
      [ "$#" -gt 0 ] || fail "--folder-uuid requires a value"
      folder_uuid="$1"
      ;;
    --target-name)
      shift
      [ "$#" -gt 0 ] || fail "--target-name requires a value"
      target_name="$1"
      ;;
    --work-dir)
      shift
      [ "$#" -gt 0 ] || fail "--work-dir requires a path"
      work_dir="$1"
      cleanup_work_dir=0
      ;;
    --confirm)
      confirm=1
      ;;
    --skip-self-heal)
      self_heal=0
      ;;
    --keep-work-dir)
      keep_work_dir=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown option: $1"
      ;;
  esac
  shift
done

[ -n "$backup_root" ] && [ -n "$plan_uuid" ] && [ -n "$folder_uuid" ] \
  || fail "explicit backup root, plan UUID and folder UUID are required"

[ -d "$backup_root" ] || fail "missing Arq backup root: $backup_root"
require_executable "$arq_restore_bin" "arq_restore"

confirm_codex_restore
if [ -z "$work_dir" ]; then
  work_dir="$(/usr/bin/mktemp -d /private/tmp/codex-restore.XXXXXX)"
  cleanup_work_dir=1
fi
/bin/mkdir -p "$work_dir"

restore_codex_snapshot
install_restored_codex "$restored_source_path"
run_self_heal

restore_succeeded=1
print -r -- "Codex restore complete: $codex_home"
