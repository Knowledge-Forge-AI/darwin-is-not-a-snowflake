#!/usr/bin/env bash
set -euo pipefail
trap 'result=$?; if (( result != 0 )); then printf "kitty variant maintenance incomplete; not recovered (cask resources may have changed); retained state requires retry/readback\n" >&2; fi' EXIT

check_mode=0
if [[ ${1-} == --check ]]; then check_mode=1; shift; fi
if [ "$#" -ne 17 ]; then
  printf 'kitty activation: expected seventeen fixed arguments\n' >&2
  exit 1
fi

common=$1
target_user=$2
user_home=$3
source_app=$4
kitty_config=$5
launcher_config_template=$6
launcher_bash_config_fragment=$7
launcher_session_template=$8
launcher_plist_template=$9
magick_bin=${10}
font_file=${11}
iconutil_bin=${12}
xattr_bin=${13}
codesign_bin=${14}
lsregister_bin=${15}
zsh_bundle_id=${16}
bash_bundle_id=${17}

# Receipts are data, never shell code. No stock setting participates in this owner.
# shellcheck source=/dev/null
source "$common"
uh_initialize "$target_user" "$user_home"
shopt -s nullglob dotglob
kitty_dir="$user_home/.config/kitty"
state_dir="$kitty_dir/.dinas-kitty-state"
hash_file() { /usr/bin/shasum -a 256 "$1" | /usr/bin/awk '{print $1}'; }
field() { /usr/bin/sed -n "s/^$2=//p" "$1"; }
ordinary() { [[ -f $1 && ! -L $1 ]]; }
optional_hash() { if ordinary "$1"; then hash_file "$1"; else printf 'absent\n'; fi; }
signature() {
  if [[ -x $codesign_bin ]] && "$codesign_bin" --verify "$1" >/dev/null 2>&1; then
    printf 'verified\n'
  else printf 'unverified\n'; fi
}
version() {
  local value
  value=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$source_app/Contents/Info.plist" 2>/dev/null || true)
  printf '%s/' "${value:-unknown}"
  value=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$source_app/Contents/Info.plist" 2>/dev/null || true)
  printf '%s\n' "${value:-unknown}"
}
# Compare LC_UUID for every architecture, without copying or changing signed bytes.
# UUID is adoption evidence, not a security/integrity proof. A receipt subsequently
# pins the observed signed bytes. Exact equality also covers unsigned fixtures.
same_program() {
  /usr/bin/cmp -s "$source_app/Contents/MacOS/kitty" "$1" && return 0
  local original current
  original=$(/usr/bin/otool -l "$source_app/Contents/MacOS/kitty" 2>/dev/null | /usr/bin/awk '/uuid / {print $2}' | /usr/bin/sort) || return 1
  current=$(/usr/bin/otool -l "$1" 2>/dev/null | /usr/bin/awk '/uuid / {print $2}' | /usr/bin/sort) || return 1
  [[ -n $original && $original == "$current" ]]
}
require_parent() {
  [[ ! -e $1 && ! -L $1 ]] || uh_require_owned_directory "$1" 'existing kitty parent'
}
check_transaction_layout() {
  local path=$1 file entries
  [[ -e $path || -L $path ]] || return 0
  uh_require_owned_directory "$path" 'variant transaction'
  entries=("$path"/*)
  # mkdir can complete before the atomic owner rename, or rmdir can be
  # interrupted after removing owner. An empty owned directory is safe to retire.
  (( ${#entries[@]} != 0 )) || return 0
  [[ -f $path/owner && ! -L $path/owner && $(/bin/cat "$path/owner") == dinas-kitty-transaction-v1 ]] || uh_fail 'unrecognized variant transaction; retained'
  for file in "${entries[@]}"; do
    case ${file##*/} in owner|next|previous|receipt.next|icon-work) ;;
      *) uh_fail 'unexpected transaction content; retained' ;; esac
  done
}
# Complete input and existing-parent preflight, before creating even a directory.
[[ -d $source_app && ! -L $source_app ]] || uh_fail 'system kitty source app is not an ordinary directory'
for file in "$source_app/Contents/MacOS/kitty" "$source_app/Contents/MacOS/kitten" \
  "$source_app/Contents/Resources/kitty.icns" "$kitty_config" "$launcher_config_template" \
  "$launcher_bash_config_fragment" "$launcher_session_template" "$launcher_plist_template"; do
  [[ -f $file ]] || uh_fail 'required kitty input missing'
done
[[ -d $source_app/Contents/Resources && -d $source_app/Contents/Frameworks ]] || uh_fail 'required cask resources missing'
for dir in "$user_home/.config" "$kitty_dir" "$state_dir" "$user_home/Applications" \
  "$kitty_dir/kitty-zsh" "$kitty_dir/kitty-bash"; do require_parent "$dir"; done
for app in "$user_home/Applications/Kitty Zsh.app" "$user_home/Applications/Kitty Bash.app"; do
  if [[ -d $app && ! -L $app ]]; then uh_require_owned_directory "$app" 'existing kitty launcher app'; fi
done
for exe in kitty-zsh kitty-bash; do
  record="$state_dir/variant-$exe.receipt"
  [[ ! -L $record && ( ! -e $record || -f $record ) ]] || uh_fail 'unsafe variant receipt'
  transaction="$user_home/Applications/.kitty-stock-maintain-$exe"
  check_transaction_layout "$transaction"
  check_transaction_layout "$transaction.cleanup"
  [[ ! -e $kitty_dir/$exe/macos-launch-services-cmdline && ! -L $kitty_dir/$exe/macos-launch-services-cmdline ]] || uh_fail 'variant LaunchServices selection conflict; preserved'
done
render_template() {
  local template_file=$1
  local app_name=$2
  local bundle_id=$3
  local executable_name=$4
  local shell_command=$5
  local icon_file=$6
  local launcher_config_dir=$7

  /usr/bin/sed \
    -e "s|@USER_NAME@|$target_user|g" \
    -e "s|@APP_NAME@|$app_name|g" \
    -e "s|@BUNDLE_ID@|$bundle_id|g" \
    -e "s|@EXECUTABLE_NAME@|$executable_name|g" \
    -e "s|@SHELL_COMMAND@|$shell_command|g" \
    -e "s|@ICON_FILE@|$icon_file|g" \
    -e "s|@LAUNCHER_CONFIG_DIR@|$launcher_config_dir|g" \
    "$template_file"
}


valid_receipt() {
  ordinary "$record" && [[ $(/usr/bin/wc -l < "$record") -eq 10 ]] &&
    [[ $(field "$record" schema) == dinas-kitty-variant-v1 ]] &&
    [[ $(field "$record" state) =~ ^(complete|pending-register)$ ]] &&
    [[ $(field "$record" version) =~ ^[a-zA-Z0-9._+/-]+$ ]] &&
    [[ $(field "$record" inputs) =~ ^[a-f0-9]{64}$ ]] &&
    [[ $(field "$record" executable) =~ ^[a-f0-9]{64}$ ]] &&
    [[ $(field "$record" icon) =~ ^[a-f0-9]{64}$ ]] &&
    [[ $(field "$record" signature) =~ ^(verified|unverified)$ ]] &&
    [[ $(field "$record" config) =~ ^[a-f0-9]{64}$ ]] &&
    [[ $(field "$record" session) =~ ^[a-f0-9]{64}$ ]] &&
    [[ $(field "$record" seal) =~ ^(absent|[a-f0-9]{64})$ ]]
}
linked() { [[ -L $1 && $(/usr/bin/readlink "$1") == "$2" ]]; }
structure_matches() {
  local item name
  for item in "$app_dir" "$app_dir/Contents" "$macos_dir" "$resources_dir"; do
    [[ -d $item && ! -L $item ]] || return 1
  done
  if [[ -e $app_dir/Contents/_CodeSignature || -L $app_dir/Contents/_CodeSignature ]]; then
    [[ -d $app_dir/Contents/_CodeSignature && ! -L $app_dir/Contents/_CodeSignature ]] || return 1
  fi
  ordinary "$plist_file" && /usr/bin/cmp -s <(printf '%s\n' "$plist") "$plist_file" || return 1
  ordinary "$executable_file" && [[ -x $executable_file && -s $executable_file ]] && ordinary "$resources_dir/$icon_file" && [[ -s $resources_dir/$icon_file ]] || return 1
  linked "$macos_dir/kitten" "$source_app/Contents/MacOS/kitten" || return 1
  linked "$app_dir/Contents/Frameworks" "$source_app/Contents/Frameworks" || return 1
  for item in "$source_app"/Contents/Resources/*; do
    name=${item##*/}
    [[ $name == kitty.icns ]] || linked "$resources_dir/$name" "$item" || return 1
  done
  for item in "$resources_dir"/*; do
    name=${item##*/}
    [[ $name == "$icon_file" ]] && continue
    [[ $name != kitty.icns && ( -e $source_app/Contents/Resources/$name || -L $source_app/Contents/Resources/$name ) ]] || return 1
  done
}
observed_matches() {
  [[ $(field "$record" executable) == "$(hash_file "$executable_file")" &&
     $(field "$record" icon) == "$(hash_file "$resources_dir/$icon_file")" &&
     $(field "$record" seal) == "$(optional_hash "$app_dir/Contents/_CodeSignature/CodeResources")" ]]
}
write_receipt() {
  local dest=$1 state=$2 tmp
  tmp=$(/usr/bin/mktemp "$state_dir/.variant-write.XXXXXX")
  printf 'schema=dinas-kitty-variant-v1\nstate=%s\nversion=%s\ninputs=%s\nexecutable=%s\nicon=%s\nseal=%s\nsignature=%s\nconfig=%s\nsession=%s\n' \
    "$state" "$(version)" "$inputs" "$(hash_file "$executable_file")" "$(hash_file "$resources_dir/$icon_file")" \
    "$(optional_hash "$app_dir/Contents/_CodeSignature/CodeResources")" "$sig" \
    "$config_hash" "$session_hash" > "$tmp"
  /bin/mv "$tmp" "$dest"
}
update_config() {
  local dest=$1 content=$2 old_hash=$3 tmp
  if ordinary "$dest" && /usr/bin/cmp -s <(printf '%s\n' "$content") "$dest"; then return; fi
  if [[ -e $dest || -L $dest ]]; then
    if ! ordinary "$dest" || [[ $(hash_file "$dest") != "$old_hash" ]]; then
      uh_preserve_path "$dest" 'kitty launcher config conflict'
    fi
  fi
  tmp=$(/usr/bin/mktemp "$launcher_config_dir/.config-write.XXXXXX")
  printf '%s\n' "$content" > "$tmp"
  /bin/chmod 0644 "$tmp"
  /bin/mv "$tmp" "$dest"
}
preflight_transaction() {
  [[ -d $transaction ]] || return 0
  local saved_record=$record
  # No staged/published bytes exist yet (or only the owner remains).
  [[ -e $transaction/next || -L $transaction/next || -e $transaction/previous || -L $transaction/previous ||
     -e $transaction/receipt.next || -L $transaction/receipt.next ]] || return 0
  if [[ -e $transaction/previous || -L $transaction/previous ]]; then
    uh_require_owned_directory "$transaction/previous" 'previous kitty bundle'
    [[ $(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$transaction/previous/Contents/Info.plist" 2>/dev/null || true) == "$bundle_id" ]] || uh_fail 'previous bundle identity differs; retained'
    if [[ ( -e $final || -L $final ) && -e $transaction/next ]]; then uh_fail 'ambiguous variant transaction; retained'; fi
  fi
  if [[ ! -e $transaction/next && ( -e $final || -L $final ) ]]; then
    [[ ! -e $transaction/receipt.next && ! -L $transaction/receipt.next ]] || record="$transaction/receipt.next"
    if ! valid_receipt || ! structure_matches || ! observed_matches; then uh_fail 'published transaction conflict; retained'; fi
  fi
  record=$saved_record
}
remove_retired_transaction() {
  local retired="$transaction.cleanup" file
  # Keep owner until all payloads are gone; an interrupted final rmdir is empty.
  [[ -d $retired ]] || return 0
  for file in "$retired"/*; do
    [[ ${file##*/} == owner ]] || /bin/rm -rf "$file"
  done
  /bin/rm -f "$retired/owner"
  /bin/rmdir "$retired"
}
cleanup_transaction() {
  # Retirement separates publication recovery from possibly partial deletion.
  if [[ -d $transaction ]]; then /bin/mv "$transaction" "$transaction.cleanup"; fi
  remove_retired_transaction
}
recover_transaction() {
  # A retired transaction has already completed its publication decision.
  remove_retired_transaction
  [[ -d $transaction ]] || return 0
  if [[ -e $transaction/previous || -L $transaction/previous ]]; then
    if [[ ! -e $final && ! -L $final ]]; then
      [[ -d $transaction/previous && ! -L $transaction/previous ]] || uh_fail 'unsafe previous bundle; retained'
      [[ $(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$transaction/previous/Contents/Info.plist" 2>/dev/null || true) == "$bundle_id" ]] || uh_fail 'previous bundle identity differs; retained'
      /bin/mv "$transaction/previous" "$final"
    elif [[ ! -e $transaction/next && -f $transaction/receipt.next && ! -L $transaction/receipt.next ]]; then
      # Candidate published before the receipt rename: finish only exact bytes.
      record="$transaction/receipt.next"
      if ! valid_receipt || ! structure_matches || ! observed_matches; then uh_fail 'published transaction conflict; retained'; fi
      /bin/mv "$record" "$final_record"
    elif valid_receipt && structure_matches && observed_matches; then
      : # Receipt published; registration was interrupted.
    else uh_fail 'ambiguous variant transaction; retained'; fi
  elif [[ ! -e $transaction/next && -f $transaction/receipt.next && -e $final ]]; then
    record="$transaction/receipt.next"
    if ! valid_receipt || ! structure_matches || ! observed_matches; then uh_fail 'new transaction conflict; retained'; fi
    /bin/mv "$record" "$final_record"
  fi
  record=$final_record
  cleanup_transaction
}
# These helpers run in the local invocation context established below.
render_variant() {
  plist=$(render_template "$launcher_plist_template" "$app_name" "$bundle_id" "$executable_name" "$shell_command" "$icon_file" "$launcher_config_dir")
  config=$(render_template "$launcher_config_template" "$app_name" "$bundle_id" "$executable_name" "$shell_command" "$icon_file" "$launcher_config_dir"
    if [ "$executable_name" = kitty-bash ]; then /bin/cat "$launcher_bash_config_fragment"; fi)
  session=$(render_template "$launcher_session_template" "$app_name" "$bundle_id" "$executable_name" "$shell_command" "$icon_file" "$launcher_config_dir")
  config_hash=$(printf '%s\n' "$config" | /usr/bin/shasum -a 256 | /usr/bin/awk '{print $1}')
  session_hash=$(printf '%s\n' "$session" | /usr/bin/shasum -a 256 | /usr/bin/awk '{print $1}')
  inputs=$({ printf 'variant-recipe-v1\n%s\n%s\n%s\n%s\n' "$source_app" "$(version)" "$icon_badge" "$plist"
    hash_file "$source_app/Contents/MacOS/kitty"; hash_file "$source_app/Contents/Resources/kitty.icns"
    for item in "$source_app"/Contents/Resources/*; do printf '%s\n' "${item##*/}"; done
  } | /usr/bin/shasum -a 256 | /usr/bin/awk '{print $1}')
}
plan_variant() {
  sig=$(signature "$app_dir")
  if valid_receipt; then
    old_config=$(field "$record" config); old_session=$(field "$record" session)
    if structure_matches && [[ $(field "$record" inputs) == "$inputs" ]] && observed_matches; then
      action=NOOP
      [[ $(field "$record" state) != pending-register ]] || action=REGISTER
      # Record an observed regression explicitly; do not silently re-sign it.
      [[ $(field "$record" signature) != verified || $sig == verified ]] || uh_fail 'variant signature regressed; bundle retained'
    fi
  elif structure_matches && same_program "$executable_file"; then action=ADOPT
  elif [[ -e $app_dir && ! -L $app_dir ]] && structure_matches; then
    # A baseline copied executable can predate a manual cask upgrade even when
    # resource names are unchanged. It is not adoptable; stage current code.
    printf '%s: baseline provenance differs; normal staged maintenance required\n' "$executable_name"
  fi
  if [[ $action == REBUILD ]]; then
    for tool in "$magick_bin" "$iconutil_bin" "$xattr_bin" "$codesign_bin" "$lsregister_bin"; do [[ -x $tool ]] || uh_fail 'rebuild tool unavailable'; done
    [[ -f $font_file ]] || uh_fail 'icon font unavailable'
  elif [[ $action == REGISTER ]]; then
    [[ -x $lsregister_bin ]] || uh_fail 'registration tool unavailable'
  fi
  printf '%s: %s (version %s; signature %s)\n' "$executable_name" "$action" "$(version)" "$sig"
  if ! ordinary "$launcher_config_file" || ! /usr/bin/cmp -s <(printf '%s\n' "$config") "$launcher_config_file" ||
    ! ordinary "$launcher_session_file" || ! /usr/bin/cmp -s <(printf '%s\n' "$session") "$launcher_session_file"; then
    printf '%s: CONFIG-PENDING (bundle action above)\n' "$executable_name"
  fi
}
render_icon() {
  iconset_base="$transaction/icon-work"
  /bin/mkdir "$iconset_base"
  local iconset_dir="$iconset_base/kitty.iconset"
  "$iconutil_bin" -c iconset "$source_app/Contents/Resources/kitty.icns" -o "$iconset_dir"
  local png dims width height x1 y1 x2 y2 tx ty point
  for png in "$iconset_dir"/*.png; do
    dims=$("$magick_bin" identify -format '%w %h' "$png")
    width=$(printf '%s\n' "$dims" | /usr/bin/awk '{print $1}')
    height=$(printf '%s\n' "$dims" | /usr/bin/awk '{print $2}')
    x1=$((width * 63 / 128))
    y1=$((height * 88 / 128))
    x2=$((width * 84 / 128))
    y2=$((height * 92 / 128))
    tx=$((width * 63 / 128))
    ty=$((height * 92 / 128))
    point=$((width * 35 / 128))
    if [ "$point" -lt 7 ]; then
      point=7
    fi
    "$magick_bin" "$png" \
      -fill black -draw "rectangle $x1,$y1 $x2,$y2" \
      -font "$font_file" \
      -pointsize "$point" -fill white -draw "text $tx,$ty '$icon_badge'" \
      "$png"
  done
  "$iconutil_bin" -c icns "$iconset_dir" -o "$resources_dir/$icon_file"
  /bin/rm -rf "$iconset_base"
  iconset_base=

}
stage_variant() {
  local owner_tmp
  owner_tmp=$(/usr/bin/mktemp "$state_dir/.variant-owner.XXXXXX")
  printf 'dinas-kitty-transaction-v1\n' > "$owner_tmp"
  /bin/mkdir -m 0700 "$transaction"
  /bin/mv "$owner_tmp" "$transaction/owner"
  # Hidden same-volume staging, with no .app suffix. Old app remains untouched.
  app_dir="$transaction/next"; contents_dir="$app_dir/Contents"; macos_dir="$contents_dir/MacOS"
  resources_dir="$contents_dir/Resources"; executable_file="$macos_dir/$executable_name"; plist_file="$contents_dir/Info.plist"
  /bin/mkdir -p "$macos_dir" "$resources_dir"
  /bin/cp "$source_app/Contents/MacOS/kitty" "$executable_file"
  /bin/chmod 0755 "$executable_file"
  /bin/ln -s "$source_app/Contents/MacOS/kitten" "$macos_dir/kitten"
  local resource_item resource_name
  for resource_item in "$source_app"/Contents/Resources/*; do
    resource_name=$(/usr/bin/basename "$resource_item")
    if [ "$resource_name" = kitty.icns ]; then
      continue
    fi
    /bin/ln -s "$resource_item" "$resources_dir/$resource_name"
  done

  render_icon

  /bin/ln -s "$source_app/Contents/Frameworks" "$contents_dir/Frameworks"
  render_template "$launcher_plist_template" \
    "$app_name" "$bundle_id" "$executable_name" "$shell_command" "$icon_file" "$launcher_config_dir" > "$plist_file"

  "$xattr_bin" -d com.apple.quarantine "$executable_file" "$resources_dir/$icon_file" "$plist_file" 2>/dev/null || true
  "$codesign_bin" --force --sign - "$app_dir"
  /usr/bin/touch "$app_dir"
  sig=$(signature "$app_dir")
  # A successful sign command with failed verification is recorded unverified;
  # external cask symlinks require native qualification, not repeated signing.
  write_receipt "$transaction/receipt.next" pending-register
  if [[ -e $final || -L $final ]]; then
    if [[ ! -L $final && -d $final && $(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$final/Contents/Info.plist" 2>/dev/null || true) == "$bundle_id" ]]; then
      /bin/mv "$final" "$transaction/previous"
    else uh_preserve_path "$final" 'kitty launcher app conflict'; fi
  fi
  /bin/mv "$app_dir" "$final"
  /bin/mv "$transaction/receipt.next" "$record"
  app_dir=$final; contents_dir="$app_dir/Contents"; macos_dir="$contents_dir/MacOS"; resources_dir="$contents_dir/Resources"
  executable_file="$macos_dir/$executable_name"
}
create_kitty_launcher() {
  local app_name=$1 bundle_id=$2 executable_name=$3 shell_command=$4
  local icon_badge=$5
  local final="$user_home/Applications/$app_name.app" app_dir="$user_home/Applications/$app_name.app"
  local contents_dir="$app_dir/Contents" macos_dir="$app_dir/Contents/MacOS" resources_dir="$app_dir/Contents/Resources"
  local plist_file="$app_dir/Contents/Info.plist" executable_file="$app_dir/Contents/MacOS/$executable_name"
  local icon_file="$executable_name.icns" launcher_config_dir="$kitty_dir/$executable_name"
  local launcher_config_file="$launcher_config_dir/kitty.conf" launcher_session_file="$launcher_config_dir/launcher.session"
  local final_record="$state_dir/variant-$executable_name.receipt" record="$state_dir/variant-$executable_name.receipt"
  local transaction="$user_home/Applications/.kitty-stock-maintain-$executable_name"
  local plist config session inputs config_hash session_hash sig action=REBUILD old_config='' old_session='' iconset_base=''
  render_variant
  if (( check_mode )) && [[ -d $transaction || -d $transaction.cleanup ]]; then
    preflight_transaction
    printf '%s: PENDING transaction\n' "$executable_name"
    return 0
  fi
  recover_transaction
  plan_variant
  (( ! check_mode )) || return 0
  uh_ensure_directory "$launcher_config_dir" 'kitty launcher config root' "$kitty_dir" 0755
  update_config "$launcher_config_file" "$config" "$old_config"
  update_config "$launcher_session_file" "$session" "$old_session"
  if [[ $action == NOOP ]]; then
    if [[ $old_config != "$config_hash" || $old_session != "$session_hash" ]]; then write_receipt "$record" complete; fi
    return 0
  fi
  if [[ $action == ADOPT ]]; then write_receipt "$record" complete; return 0; fi
  if [[ $action == REBUILD ]]; then
    stage_variant
  fi
  "$lsregister_bin" -f "$app_dir"
  write_receipt "$record" complete
  cleanup_transaction
}
if (( ! check_mode )); then
  # Plan both variants before any mutation, including rebuild-only tool inputs.
  check_mode=1
  create_kitty_launcher "Kitty Zsh" "$zsh_bundle_id" "kitty-zsh" "/bin/zsh -l" "Z" >/dev/null
  create_kitty_launcher "Kitty Bash" "$bash_bundle_id" "kitty-bash" "/usr/bin/login -fp $target_user /run/current-system/sw/bin/bash" "B" >/dev/null
  check_mode=0
  uh_ensure_directory "$user_home/.config" 'user config root' "$user_home" 0755
  uh_ensure_directory "$kitty_dir" 'kitty config root' "$user_home/.config" 0755
  uh_ensure_directory "$state_dir" 'kitty receipts' "$kitty_dir" 0700
  uh_ensure_directory "$user_home/Applications" 'user Applications root' "$user_home" 0755
  uh_link_managed_path "$kitty_dir/kitty.conf" "$kitty_config" "$kitty_dir" 'kitty config'
fi
create_kitty_launcher "Kitty Zsh" "$zsh_bundle_id" "kitty-zsh" "/bin/zsh -l" "Z"
create_kitty_launcher "Kitty Bash" "$bash_bundle_id" "kitty-bash" "/usr/bin/login -fp $target_user /run/current-system/sw/bin/bash" "B"
