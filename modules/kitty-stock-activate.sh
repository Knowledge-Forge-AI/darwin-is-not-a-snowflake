#!/usr/bin/env bash
# Own only two stock selection files. Mutable receipts are parsed, never sourced.
set -euo pipefail
check_mode=0
if [[ ${1-} == --check ]]; then check_mode=1; shift; fi
mode=${1-}; shift || true
case $mode:$# in enable:5|disable:3) ;; *) printf 'kitty stock: expected enable + five or disable + three arguments\n' >&2; exit 1 ;; esac
common=$1 target_user=$2 user_home=$3
stock_config_source=${4-} launch_services_source=${5-}
# shellcheck source=/dev/null
source "$common"
uh_initialize "$target_user" "$user_home"
kitty_dir=$user_home/.config/kitty
state_dir=$kitty_dir/.dinas-kitty-state
fail() { printf 'kitty stock: %s\n' "$*" >&2; exit 1; }
ordinary() { [[ -f $1 && ! -L $1 ]]; }
hash_file() { /usr/bin/shasum -a 256 "$1" | /usr/bin/awk '{print $1}'; }
field() { /usr/bin/sed -n "s/^$1=//p" "$record"; }
digest() {
  if [[ -L $1 ]]; then /usr/bin/readlink "$1" | /usr/bin/shasum -a 256 | /usr/bin/awk '{print $1}'
  elif ordinary "$1"; then hash_file "$1"
  else printf 'absent\n'; fi
}
load_record() {
  state=none installed=absent kind=absent prehash=absent backup=none previous=absent
  [[ -e $record || -L $record ]] || return 0
  ordinary "$record" || fail 'unsafe ownership receipt; preserved'
  [[ $(/usr/bin/wc -l < "$record") -eq 8 && $(field schema) == dinas-kitty-stock-v1 && $(field file) == "$name" ]] || fail 'corrupt ownership receipt; preserved'
  state=$(field state); installed=$(field installed); kind=$(field preimage); prehash=$(field prehash); backup=$(field backup); previous=$(field previous)
  [[ $state =~ ^(prepared|active|disabled|relinquished)$ && $installed =~ ^[a-f0-9]{64}$ && $kind =~ ^(absent|adopted|file|link)$ ]] || fail 'corrupt ownership fields; preserved'
  [[ $previous =~ ^(absent|[a-f0-9]{64})$ ]] || fail 'corrupt previous content hash; preserved'
  if [[ $kind == file || $kind == link ]]; then
    [[ $prehash =~ ^[a-f0-9]{64}$ && $backup == "$name.before-nix-darwin"* && $backup != */* ]] || fail 'corrupt preimage reference; preserved'
  else [[ $backup == none && $prehash == absent ]] || fail 'corrupt empty preimage; preserved'; fi
}
save_record() {
  local tmp
  tmp=$(/usr/bin/mktemp "$state_dir/.stock-receipt.XXXXXX")
  printf 'schema=dinas-kitty-stock-v1\nfile=%s\nstate=%s\ninstalled=%s\npreimage=%s\nprehash=%s\nbackup=%s\nprevious=%s\n' \
    "$name" "$state" "$installed" "$kind" "$prehash" "$backup" "$previous" > "$tmp"
  /bin/chmod 0600 "$tmp"
  /bin/mv "$tmp" "$record"
}
matches_installed() { ordinary "$dest" && [[ $(digest "$dest") == "$installed" || ( $state == prepared && $(digest "$dest") == "$previous" ) ]]; }
matches_preimage() {
  case $kind in
    file) ordinary "$dest" && [[ $(digest "$dest") == "$prehash" ]] ;;
    link) [[ -L $dest && $(digest "$dest") == "$prehash" ]] ;;
    *) return 1 ;;
  esac
}
validate_backup() {
  case $kind in
    file) ordinary "$kitty_dir/$backup" ;;
    link) [[ -L $kitty_dir/$backup ]] ;;
    *) return 0 ;;
  esac && [[ $(digest "$kitty_dir/$backup") == "$prehash" ]]
}
# Validate both records, every ancestor and sources before writing anything.
for dir in "$user_home/.config" "$kitty_dir" "$state_dir"; do
  [[ ! -e $dir && ! -L $dir ]] || uh_require_owned_directory "$dir" 'stock parent'
done
if [[ $mode == enable ]]; then
  for src in "$stock_config_source" "$launch_services_source"; do ordinary "$src" || fail 'stock input missing or symlink'; done
fi
for name in stock-bash.conf macos-launch-services-cmdline; do
  record=$state_dir/stock-$name.receipt; dest=$kitty_dir/$name
  load_record
  # Directories/special files cannot be atomically restored as selection files.
  # Refuse them in place; do not sweep or relocate private directories.
  if [[ $mode == enable && -e $dest && ! -L $dest && ! -f $dest ]]; then fail 'stock selection path is a directory or special file; preserved'; fi
  if [[ $kind == file || $kind == link ]]; then
    if [[ $state == prepared ]] && matches_preimage && [[ ! -e $kitty_dir/$backup && ! -L $kitty_dir/$backup ]]; then
      : # Preimage move not yet performed.
    elif [[ $mode == enable ]] || matches_installed || [[ ! -e $dest && ! -L $dest ]]; then
      validate_backup || fail 'preimage backup missing or edited; no changes made'
    fi
  fi
  if [[ $mode == enable && $state == prepared && ( -e $dest || -L $dest ) ]]; then
    matches_installed || matches_preimage || fail 'interrupted stock write conflicts with user edit; no changes made'
  fi
done
if (( ! check_mode )) && [[ $mode == enable ]]; then
  uh_ensure_directory "$user_home/.config" 'user config root' "$user_home" 0755
  uh_ensure_directory "$kitty_dir" 'kitty config root' "$user_home/.config" 0755
  uh_ensure_directory "$state_dir" 'stock receipts' "$kitty_dir" 0700
fi
install_one() {
  local src=$1 wanted tmp current
  wanted=$(hash_file "$src")
  current=$(digest "$dest")
  if [[ $state == active ]] && matches_installed && [[ $installed == "$wanted" ]]; then return 0; fi
  pending=1
  (( ! check_mode )) || return 0
  if [[ $state == prepared ]]; then
    # Recovery of a preimage move interrupted after its intent was recorded.
    if [[ $kind == file || $kind == link ]]; then
      if [[ ! -e $kitty_dir/$backup && ! -L $kitty_dir/$backup ]] && matches_preimage; then
        /bin/mv "$dest" "$kitty_dir/$backup"
      else validate_backup || fail 'prepared preimage missing or edited; preserved'; fi
    fi
    if [[ -e $dest || -L $dest ]]; then
      matches_installed || matches_preimage || fail 'interrupted stock write conflicts with user edit; preserved'
    fi
  elif [[ $state == none ]]; then
    if ordinary "$dest" && [[ $current == "$wanted" ]]; then kind=adopted
    elif [[ -e $dest || -L $dest ]]; then
      kind="file"; [[ ! -L $dest ]] || kind="link"
      prehash=$current; backup=$(uh_unique_backup_path "$dest"); backup=${backup##*/}
    fi
    installed=$wanted; state=prepared; save_record
    if [[ $kind == file || $kind == link ]]; then /bin/mv "$dest" "$kitty_dir/$backup"; fi
  elif [[ $kind == adopted ]] && matches_installed && [[ $installed != "$wanted" ]]; then
    # Identical pre-existing content becomes a real preimage before an update.
    kind="file"; prehash=$installed
    backup=$(uh_unique_backup_path "$dest"); backup=${backup##*/}
    previous=$installed; installed=$wanted; state=prepared; save_record
    /bin/mv "$dest" "$kitty_dir/$backup"
  elif [[ -e $dest || -L $dest ]]; then
    if matches_installed || matches_preimage; then
      : # Old managed content or a restored known preimage; existing backup kept.
    else uh_preserve_path "$dest" 'user-edited stock selection'; fi
  fi
  # Intent precedes replacement so a killed write can be safely resumed.
  if [[ $state != prepared ]]; then previous=$installed; fi
  installed=$wanted; state=prepared; save_record
  if ! ordinary "$dest" || [[ $(digest "$dest") != "$wanted" ]]; then
    tmp=$(/usr/bin/mktemp "$kitty_dir/.stock-write.XXXXXX")
    /bin/cp "$src" "$tmp"; /bin/chmod 0644 "$tmp"; /bin/mv "$tmp" "$dest"
  fi
  state=active; previous=absent; save_record
}
disable_one() {
  local tmp
  [[ $state != none && $state != disabled && $state != relinquished ]] || return 0
  pending=1
  (( ! check_mode )) || return 0
  if matches_preimage; then state=disabled; save_record; return 0; fi
  if [[ -e $dest || -L $dest ]]; then
    if ! matches_installed; then
      printf 'kitty stock: user-edited %s preserved; ownership relinquished\n' "$name" >&2
      state=relinquished; save_record; return 0
    fi
  fi
  validate_backup || fail 'preimage backup missing or edited; exact owned file retained'
  if [[ $kind == file || $kind == link ]]; then
    tmp=$(/usr/bin/mktemp "$kitty_dir/.stock-restore.XXXXXX")
    /bin/rm "$tmp"
    /bin/cp -pP "$kitty_dir/$backup" "$tmp"
    /bin/mv "$tmp" "$dest"
  elif matches_installed; then
    [[ $kind == adopted ]] || /bin/rm "$dest"
  fi
  state=disabled; save_record
}
pending=0
if [[ $mode == enable ]]; then
  for name in stock-bash.conf macos-launch-services-cmdline; do
    record=$state_dir/stock-$name.receipt; dest=$kitty_dir/$name; load_record
    if [[ $name == stock-bash.conf ]]; then install_one "$stock_config_source"; else install_one "$launch_services_source"; fi
  done
  if (( check_mode && pending )); then printf 'PENDING\n'; else printf 'INSTALLED\n'; fi
else
  for name in macos-launch-services-cmdline stock-bash.conf; do
    record=$state_dir/stock-$name.receipt; dest=$kitty_dir/$name; load_record; disable_one
  done
  if (( check_mode && pending )); then printf 'PENDING\n'; else printf 'ABSENT\n'; fi
fi
