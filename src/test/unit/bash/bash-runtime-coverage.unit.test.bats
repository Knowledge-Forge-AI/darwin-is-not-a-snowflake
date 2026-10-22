#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

setup() {
  script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
  repo_dir="$(cd "$script_dir/../../../.." && pwd)"
}
fail_test() {
  echo "test-bash-runtime-coverage: $*" >&2
  exit 1
}

write_fake_command() {
  local bin_dir="$1"
  local name="$2"
  local body="$3"
  local path="$bin_dir/$name"

  printf '%s\n' '#!/usr/bin/env bash' > "$path"
  printf '%s\n' 'set -euo pipefail' >> "$path"
  printf '%s\n' "$body" >> "$path"
  chmod +x "$path"
}

@test "rclone sync wrappers dispatch safe dry-run and size commands" {
  codex_script="$repo_dir/bin/codex-rclone-sync"
  shared_images_script="$repo_dir/bin/shared-images-rclone-sync"
  fake_bin="$BATS_TEST_TMPDIR/bin"
  log_file="$BATS_TEST_TMPDIR/rclone.log"
  codex_home="$BATS_TEST_TMPDIR/codex"
  shared_images="$BATS_TEST_TMPDIR/shared-images"
  mkdir -p "$fake_bin" "$codex_home" "$shared_images"

  write_fake_command "$fake_bin" rclone 'printf "%s\n" "$*" >> "$RCLONE_TEST_LOG"'

  run env PATH="$fake_bin:$PATH" RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    CODEX_LOCAL_DIR="$codex_home" CODEX_CRYPT_REMOTE=codex-test: \
    "$codex_script" push
  [ "$status" -eq 0 ] || fail_test "codex push failed: $output"

  run env PATH="$fake_bin:$PATH" RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    CODEX_LOCAL_DIR="$codex_home" CODEX_CRYPT_REMOTE=codex-test: \
    "$codex_script" dry-run-push-merge
  [ "$status" -eq 0 ] || fail_test "codex dry-run-push-merge failed: $output"

  run env PATH="$fake_bin:$PATH" RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    CODEX_LOCAL_DIR="$codex_home" CODEX_CRYPT_REMOTE=codex-test: \
    "$codex_script" dry-run-pull-merge
  [ "$status" -eq 0 ] || fail_test "codex dry-run-pull-merge failed: $output"

  run env FAKE_PATH="$fake_bin" CODEX_LOCAL_DIR="$codex_home" CODEX_CRYPT_REMOTE=codex-test: \
    RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    bash -c "printf 'y\n' | PATH=\"\$FAKE_PATH:\$PATH\" CODEX_LOCAL_DIR=\"\$CODEX_LOCAL_DIR\" CODEX_CRYPT_REMOTE=\"\$CODEX_CRYPT_REMOTE\" RCLONE_TEST_LOG=\"\$RCLONE_TEST_LOG\" RCLONE=rclone '$codex_script' pull"
  [ "$status" -eq 0 ] || fail_test "codex pull failed: $output"

  run env PATH="$fake_bin:$PATH" RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    CODEX_LOCAL_DIR="$codex_home" CODEX_CRYPT_REMOTE=codex-test: \
    "$codex_script" push-merge
  [ "$status" -eq 0 ] || fail_test "codex push-merge failed: $output"

  run env PATH="$fake_bin:$PATH" RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    CODEX_LOCAL_DIR="$codex_home" CODEX_CRYPT_REMOTE=codex-test: \
    "$codex_script" pull-merge
  [ "$status" -eq 0 ] || fail_test "codex pull-merge failed: $output"

  run env PATH="$fake_bin:$PATH" RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    CODEX_LOCAL_DIR="$codex_home" CODEX_CRYPT_REMOTE=codex-test: \
    "$codex_script" size
  [ "$status" -eq 0 ] || fail_test "codex size failed: $output"

  run env PATH="$fake_bin:$PATH" RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    SHARED_IMAGES_LOCAL_DIR="$shared_images" SHARED_IMAGES_CRYPT_REMOTE=shared-test: \
    "$shared_images_script" dry-run-push
  [ "$status" -eq 0 ] || fail_test "shared-images dry-run-push failed: $output"

  run env PATH="$fake_bin:$PATH" RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    SHARED_IMAGES_LOCAL_DIR="$shared_images" SHARED_IMAGES_CRYPT_REMOTE=shared-test: \
    "$shared_images_script" push
  [ "$status" -eq 0 ] || fail_test "shared-images push failed: $output"

  run env PATH="$fake_bin:$PATH" RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    SHARED_IMAGES_LOCAL_DIR="$shared_images" SHARED_IMAGES_CRYPT_REMOTE=shared-test: \
    "$shared_images_script" dry-run-pull
  [ "$status" -eq 0 ] || fail_test "shared-images dry-run-pull failed: $output"

  run env PATH="$fake_bin:$PATH" RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    SHARED_IMAGES_LOCAL_DIR="$shared_images" SHARED_IMAGES_CRYPT_REMOTE=shared-test: \
    "$shared_images_script" pull
  [ "$status" -eq 0 ] || fail_test "shared-images pull failed: $output"

  run env PATH="$fake_bin:$PATH" RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    SHARED_IMAGES_LOCAL_DIR="$shared_images" SHARED_IMAGES_CRYPT_REMOTE=shared-test: \
    "$shared_images_script" size
  [ "$status" -eq 0 ] || fail_test "shared-images size failed: $output"

  grep -Fq 'copy --dry-run' "$log_file" || fail_test "codex dry-run merge did not call rclone copy --dry-run"
  grep -Fq 'sync --dry-run' "$log_file" || fail_test "shared-images dry-run did not call rclone sync --dry-run"
  grep -Fq 'size codex-test:' "$log_file" || fail_test "codex size did not target configured remote"
  grep -Fq 'size shared-test:' "$log_file" || fail_test "shared-images size did not target configured remote"
}

@test "rclone sync wrappers cover help, rejected pulls, and validation failures" {
  codex_script="$repo_dir/bin/codex-rclone-sync"
  shared_images_script="$repo_dir/bin/shared-images-rclone-sync"
  fake_bin="$BATS_TEST_TMPDIR/bin"
  log_file="$BATS_TEST_TMPDIR/rclone-errors.log"
  missing_codex="$BATS_TEST_TMPDIR/missing-codex"
  missing_shared="$BATS_TEST_TMPDIR/missing-shared"
  mkdir -p "$fake_bin"

  write_fake_command "$fake_bin" rclone 'printf "%s\n" "$*" >> "$RCLONE_TEST_LOG"'

  run env PATH="$fake_bin:$PATH" RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    CODEX_LOCAL_DIR="$missing_codex" CODEX_CRYPT_REMOTE=codex-test: \
    "$codex_script" --help
  [ "$status" -eq 0 ] || fail_test "codex help failed: $output"
  [[ "$output" == *"Usage: codex-rclone-sync"* ]] || fail_test "codex help omitted usage"

  run env PATH="$fake_bin:$PATH" RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    CODEX_LOCAL_DIR="$missing_codex" CODEX_CRYPT_REMOTE=codex-test: \
    "$codex_script" dry-run-push
  [ "$status" -eq 1 ] || fail_test "codex dry-run-push should reject missing local dir"
  [[ "$output" == *"Local Codex directory does not exist"* ]] || fail_test "codex missing local dir message changed"

  run env FAKE_PATH="$fake_bin" CODEX_LOCAL_DIR="$missing_codex" CODEX_CRYPT_REMOTE=codex-test: \
    RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    bash -c "printf 'no\n' | PATH=\"\$FAKE_PATH:\$PATH\" CODEX_LOCAL_DIR=\"\$CODEX_LOCAL_DIR\" CODEX_CRYPT_REMOTE=\"\$CODEX_CRYPT_REMOTE\" RCLONE_TEST_LOG=\"\$RCLONE_TEST_LOG\" RCLONE=rclone '$codex_script' pull"
  [ "$status" -eq 1 ] || fail_test "codex pull should reject no confirmation"
  [[ "$output" == *"Aborted."* ]] || fail_test "codex rejected pull did not report abort"

  run env PATH="$fake_bin:$PATH" RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    CODEX_LOCAL_DIR="$missing_codex" CODEX_CRYPT_REMOTE=codex-test: \
    "$codex_script" definitely-not-a-command
  [ "$status" -eq 2 ] || fail_test "codex unknown command should exit 2"

  run env PATH="$fake_bin:$PATH" RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    SHARED_IMAGES_LOCAL_DIR="$missing_shared" SHARED_IMAGES_CRYPT_REMOTE=shared-test: \
    "$shared_images_script" --help
  [ "$status" -eq 0 ] || fail_test "shared-images help failed: $output"
  [[ "$output" == *"Usage: shared-images-rclone-sync"* ]] || fail_test "shared-images help omitted usage"

  run env PATH="$fake_bin:$PATH" RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    SHARED_IMAGES_LOCAL_DIR="$missing_shared" SHARED_IMAGES_CRYPT_REMOTE=shared-test: \
    "$shared_images_script" push
  [ "$status" -eq 1 ] || fail_test "shared-images push should reject missing local dir"
  [[ "$output" == *"Local shared-images directory does not exist"* ]] || fail_test "shared-images missing local dir message changed"

  run env PATH="$fake_bin:$PATH" RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    SHARED_IMAGES_LOCAL_DIR="$missing_shared" SHARED_IMAGES_CRYPT_REMOTE=shared-test: \
    "$shared_images_script" nope
  [ "$status" -eq 2 ] || fail_test "shared-images unknown command should exit 2"
}

@test "Clear New Tab sync copies extension and profile state" {
  sync_script="$repo_dir/bin/clear-new-tab-sync"
  extension_id="felphkbfjadmcejnibcmcncimlappdde"
  chrome_profile="$BATS_TEST_TMPDIR/chrome/Default"
  brave_profile="$BATS_TEST_TMPDIR/brave"
  source_extension="$chrome_profile/Extensions/$extension_id/1.2.3_0"
  mkdir -p \
    "$source_extension" \
    "$chrome_profile/IndexedDB/chrome-extension_${extension_id}_0.indexeddb.leveldb" \
    "$chrome_profile/IndexedDB/chrome-extension_${extension_id}_0.indexeddb.blob" \
    "$chrome_profile/Local Extension Settings/$extension_id"
  printf '{}\n' > "$source_extension/manifest.json"
  printf 'leveldb\n' > "$chrome_profile/IndexedDB/chrome-extension_${extension_id}_0.indexeddb.leveldb/CURRENT"
  printf 'blob\n' > "$chrome_profile/IndexedDB/chrome-extension_${extension_id}_0.indexeddb.blob/0"
  printf 'settings\n' > "$chrome_profile/Local Extension Settings/$extension_id/LOG"

  run env CLEAR_NEW_TAB_SOURCE_CHROME_PROFILE="$chrome_profile" \
    BRAVE_YOUTUBE_USER_DATA_DIR="$brave_profile" "$sync_script" brave-youtube

  [ "$status" -eq 0 ] || fail_test "clear-new-tab-sync failed: $output"
  [ -f "$brave_profile/clear-new-tab-extension/manifest.json" ] \
    || fail_test "clear-new-tab-sync did not copy extension bundle"
  [ -f "$brave_profile/Default/IndexedDB/chrome-extension_${extension_id}_0.indexeddb.leveldb/CURRENT" ] \
    || fail_test "clear-new-tab-sync did not copy IndexedDB leveldb"
  [ -f "$brave_profile/Default/IndexedDB/chrome-extension_${extension_id}_0.indexeddb.blob/0" ] \
    || fail_test "clear-new-tab-sync did not copy IndexedDB blob"
  [ -f "$brave_profile/Default/Local Extension Settings/$extension_id/LOG" ] \
    || fail_test "clear-new-tab-sync did not copy Local Extension Settings"

  run env CLEAR_NEW_TAB_SOURCE_CHROME_PROFILE="$chrome_profile" \
    BRAVE_YOUTUBE_USER_DATA_DIR="$brave_profile" "$sync_script" status
  [ "$status" -eq 0 ] || fail_test "clear-new-tab-sync status failed: $output"
  [[ "$output" == *"source_extension:"* ]] || fail_test "clear-new-tab-sync status omitted source extension"
}

@test "Clear New Tab sync covers help, missing source, optional dirs, and unknown commands" {
  sync_script="$repo_dir/bin/clear-new-tab-sync"
  extension_id="felphkbfjadmcejnibcmcncimlappdde"
  chrome_profile="$BATS_TEST_TMPDIR/chrome/Default"
  brave_profile="$BATS_TEST_TMPDIR/brave"
  source_extension="$chrome_profile/Extensions/$extension_id/2.0.0_0"
  mkdir -p "$source_extension" "$brave_profile/clear-new-tab-extension"
  printf '{}\n' > "$source_extension/manifest.json"
  printf 'old\n' > "$brave_profile/clear-new-tab-extension/old.txt"

  run "$sync_script" --help
  [ "$status" -eq 0 ] || fail_test "clear-new-tab-sync help failed: $output"
  [[ "$output" == *"Usage: clear-new-tab-sync"* ]] || fail_test "clear-new-tab-sync help omitted usage"

  run env CLEAR_NEW_TAB_SOURCE_CHROME_PROFILE="$BATS_TEST_TMPDIR/missing-profile" \
    BRAVE_YOUTUBE_USER_DATA_DIR="$brave_profile" "$sync_script" brave-youtube
  [ "$status" -eq 1 ] || fail_test "clear-new-tab-sync should fail when extension source is missing"
  [[ "$output" == *"Clear New Tab is not installed"* ]] || fail_test "clear-new-tab-sync missing source message changed"

  run env CLEAR_NEW_TAB_SOURCE_CHROME_PROFILE="$chrome_profile" \
    BRAVE_YOUTUBE_USER_DATA_DIR="$brave_profile" "$sync_script" brave-youtube
  [ "$status" -eq 0 ] || fail_test "clear-new-tab-sync optional-dir path failed: $output"
  [ -f "$brave_profile/clear-new-tab-extension/manifest.json" ] \
    || fail_test "clear-new-tab-sync did not replace existing extension target"
  [ ! -f "$brave_profile/clear-new-tab-extension/old.txt" ] \
    || fail_test "clear-new-tab-sync did not remove backed-up extension target"

  run "$sync_script" no-such-command
  [ "$status" -eq 2 ] || fail_test "clear-new-tab-sync unknown command should exit 2"
}

@test "monitor-temp prints one thermal sample with fake macOS tools" {
  monitor_script="$repo_dir/bin/monitor-temp"
  fake_bin="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$fake_bin"

  write_fake_command "$fake_bin" uname 'printf "Darwin\n"'
  write_fake_command "$fake_bin" sudo 'if [ "${1:-}" = "powermetrics" ]; then
  cat <<POWER
Current pressure level: Moderate
Combined Power: 4200 mW
POWER
else
  exit 0
fi'
  write_fake_command "$fake_bin" ioreg 'cat <<BATTERY
    "Temperature" = 3180
    "VirtualTemperature" = 3300
BATTERY'
  write_fake_command "$fake_bin" date 'printf "12:34:56\n"'
  write_fake_command "$fake_bin" sleep ':'

  run env PATH="$fake_bin:$PATH" MONITOR_TEMP_COLOR=never "$monitor_script" \
    --count 1 --interval 0 --bell-pressure moderate --no-bell --no-color
  [ "$status" -eq 0 ] || fail_test "monitor-temp failed: $output"
  [[ "$output" == *"12:34:56"* ]] || fail_test "monitor-temp omitted timestamp"
  [[ "$output" == *"P:Moderate"* ]] || fail_test "monitor-temp omitted pressure"
  [[ "$output" == *"B:31.8C"* ]] || fail_test "monitor-temp omitted battery temperature"
  [[ "$output" == *"W:4.2W"* ]] || fail_test "monitor-temp omitted converted watts"
}

@test "monitor-temp covers help, validation, color, virtual temperature, and bell paths" {
  monitor_script="$repo_dir/bin/monitor-temp"
  fake_bin="$BATS_TEST_TMPDIR/bin"
  pressure_state="$BATS_TEST_TMPDIR/pressure-state"
  mkdir -p "$fake_bin"
  printf '0\n' > "$pressure_state"

  write_fake_command "$fake_bin" uname 'printf "${MONITOR_TEST_UNAME:-Darwin}\n"'
  write_fake_command "$fake_bin" date 'printf "12:34:%02d\n" "${MONITOR_TEST_SECOND:-0}"'
  write_fake_command "$fake_bin" sleep ':'
  write_fake_command "$fake_bin" ioreg 'cat <<BATTERY
    "VirtualTemperature" = 3300
BATTERY'
  write_fake_command "$fake_bin" sudo 'if [ "${1:-}" = "true" ]; then
  exit 0
fi
sample="$(cat "$MONITOR_TEST_PRESSURE_STATE")"
sample=$((sample + 1))
printf "%s\n" "$sample" > "$MONITOR_TEST_PRESSURE_STATE"
if [ "${MONITOR_TEST_BAD_POWER:-0}" = "1" ]; then
  cat <<POWER
Current pressure level: Unknown
Combined Power: unavailable
POWER
elif [ "$sample" -eq 1 ]; then
  cat <<POWER
Current pressure level: Nominal
Combined Power: 500 uW
POWER
else
  cat <<POWER
Current pressure level: Heavy
Combined Power: 25 W
POWER
fi'

  run env PATH="$fake_bin:$PATH" "$monitor_script" --help
  [ "$status" -eq 0 ] || fail_test "monitor-temp help failed: $output"
  [[ "$output" == *"Usage: monitor-temp"* ]] || fail_test "monitor-temp help omitted usage"

  run env PATH="$fake_bin:$PATH" "$monitor_script" --interval
  [ "$status" -eq 2 ] || fail_test "monitor-temp missing interval should exit 2"

  run env PATH="$fake_bin:$PATH" "$monitor_script" --count
  [ "$status" -eq 2 ] || fail_test "monitor-temp missing count should exit 2"

  run env PATH="$fake_bin:$PATH" "$monitor_script" --bell-pressure
  [ "$status" -eq 2 ] || fail_test "monitor-temp missing bell pressure should exit 2"

  run env PATH="$fake_bin:$PATH" "$monitor_script" --unknown
  [ "$status" -eq 2 ] || fail_test "monitor-temp unknown arg should exit 2"

  run env PATH="$fake_bin:$PATH" MONITOR_TEST_UNAME=Linux "$monitor_script" --count 1
  [ "$status" -eq 1 ] || fail_test "monitor-temp non-Darwin should exit 1"

  run env PATH="$fake_bin:$PATH" MONITOR_TEMP_COLOR=nope "$monitor_script" --count 1
  [ "$status" -eq 2 ] || fail_test "monitor-temp invalid color should exit 2"

  run env PATH="$fake_bin:$PATH" "$monitor_script" --count 1 --bell-pressure impossible
  [ "$status" -eq 2 ] || fail_test "monitor-temp invalid bell pressure should exit 2"

  run env PATH="$fake_bin:$PATH" MONITOR_TEST_PRESSURE_STATE="$pressure_state" \
    MONITOR_TEMP_COLOR=always "$monitor_script" --count 2 --interval 0 --bell-pressure light
  [ "$status" -eq 0 ] || fail_test "monitor-temp color/bell path failed: $output"
  [[ "$output" == *$'\a'* ]] || fail_test "monitor-temp did not emit bell on threshold crossing"
  [[ "$output" == *"P:Nominal"* ]] || fail_test "monitor-temp did not print nominal pressure"
  [[ "$output" == *"P:Heavy"* ]] || fail_test "monitor-temp did not print heavy pressure"
  [[ "$output" == *"VB:33.0C"* ]] || fail_test "monitor-temp did not use virtual battery temperature"
  [[ "$output" == *"W:25.0W"* ]] || fail_test "monitor-temp did not print W input"

  printf '0\n' > "$pressure_state"
  run env PATH="$fake_bin:$PATH" MONITOR_TEST_PRESSURE_STATE="$pressure_state" \
    MONITOR_TEST_BAD_POWER=1 MONITOR_TEMP_COLOR=never "$monitor_script" --count 1 --interval 0 --no-bell
  [ "$status" -eq 0 ] || fail_test "monitor-temp bad power fallback failed: $output"
  [[ "$output" == *"W:unavailable"* ]] || fail_test "monitor-temp did not preserve unparseable power"
}

@test "Brave YouTube session exposes safe status, stop, and help paths" {
  session_script="$repo_dir/bin/brave-youtube-session"
  fake_bin="$BATS_TEST_TMPDIR/bin"
  state_home="$BATS_TEST_TMPDIR/state"
  user_data="$BATS_TEST_TMPDIR/user-data"
  app_bundle="$BATS_TEST_TMPDIR/Brave YouTube.app"
  browser_bin="$app_bundle/Contents/MacOS/Brave YouTube.bin"
  theme_dir="$user_data/brave-youtube-theme"
  clear_dir="$user_data/clear-new-tab-extension"
  log_file="$BATS_TEST_TMPDIR/brave-session.log"
  page_state_file="$BATS_TEST_TMPDIR/page-state-count"
  mkdir -p "$fake_bin" "$(dirname "$browser_bin")" "$theme_dir" "$clear_dir"
  printf '0\n' > "$page_state_file"
  printf '{}\n' > "$theme_dir/manifest.json"
  printf '{}\n' > "$clear_dir/manifest.json"
  printf '#!/usr/bin/env bash\n' > "$browser_bin"
  chmod +x "$browser_bin"
  write_fake_command "$fake_bin" pmset 'case "$*" in
  "-g ps") printf "Now drawing from '\''AC Power'\''\n" ;;
  "-g batt") printf "Now drawing from '\''AC Power'\''\n -InternalBattery-0 (id=1)\t90%%; charged; 0:00 remaining present: true\n" ;;
  "-g") printf "SleepDisabled          0\n" ;;
esac'
  write_fake_command "$fake_bin" sudo 'printf "sudo %s\n" "$*" >> "$BRAVE_TEST_LOG"'
  write_fake_command "$fake_bin" caffeinate 'printf "caffeinate %s\n" "$*" >> "$BRAVE_TEST_LOG"'
  write_fake_command "$fake_bin" open 'printf "open %s\n" "$*" >> "$BRAVE_TEST_LOG"'
  write_fake_command "$fake_bin" sleep 'printf "sleep %s\n" "$*" >> "$BRAVE_TEST_LOG"'
  write_fake_command "$fake_bin" launchctl 'printf "launchctl %s\n" "$*" >> "$BRAVE_TEST_LOG"'
  write_fake_command "$fake_bin" ps ':'
  write_fake_command "$fake_bin" kill 'case "${1:-}" in -0) exit 1 ;; *) printf "kill %s\n" "$*" >> "$BRAVE_TEST_LOG" ;; esac'
  write_fake_command "$fake_bin" id 'printf "501\n"'
  write_fake_command "$fake_bin" date 'printf "2026-06-23 12:00:00\n"'
  write_fake_command "$fake_bin" clear-sync ':'
  write_fake_command "$fake_bin" cdp 'case "${1:-}" in
  page-state)
    count="$(cat "$BRAVE_TEST_PAGE_STATE")"
    count=$((count + 1))
    printf "%s\n" "$count" > "$BRAVE_TEST_PAGE_STATE"
    printf "no_pages count=%s\n" "$count"
    ;;
  media-state)
    printf "not_playing reason=test\n"
    ;;
  close-browser)
    printf "close-browser %s\n" "$*" >> "$BRAVE_TEST_LOG"
    ;;
esac'

  run env XDG_STATE_HOME="$state_home" HOME="$BATS_TEST_TMPDIR/home" \
    BRAVE_YOUTUBE_APP_BUNDLE="$app_bundle" \
    BRAVE_YOUTUBE_BIN="$browser_bin" \
    BRAVE_YOUTUBE_USER_DATA_DIR="$user_data" \
    BRAVE_YOUTUBE_THEME_EXTENSION_DIR="$theme_dir" \
    BRAVE_YOUTUBE_CLEAR_NEW_TAB_EXTENSION_DIR="$clear_dir" \
    "$session_script" --help
  [ "$status" -eq 0 ] || fail_test "Brave YouTube help failed: $output"
  [[ "$output" == *"Usage: brave-youtube-session"* ]] || fail_test "Brave YouTube help omitted usage"

  run env XDG_STATE_HOME="$state_home" HOME="$BATS_TEST_TMPDIR/home" \
    BRAVE_YOUTUBE_APP_BUNDLE="$app_bundle" \
    BRAVE_YOUTUBE_BIN="$browser_bin" \
    BRAVE_YOUTUBE_USER_DATA_DIR="$user_data" \
    BRAVE_YOUTUBE_THEME_EXTENSION_DIR="$theme_dir" \
    BRAVE_YOUTUBE_CLEAR_NEW_TAB_EXTENSION_DIR="$clear_dir" \
    "$session_script" status
  [ "$status" -eq 0 ] || fail_test "Brave YouTube status failed: $output"
  [[ "$output" == *"session: stopped"* ]] || fail_test "Brave YouTube status omitted stopped session"
  [[ "$output" == *"extension_audit:"* ]] || fail_test "Brave YouTube status omitted extension audit"

  run env XDG_STATE_HOME="$state_home" HOME="$BATS_TEST_TMPDIR/home" \
    BRAVE_YOUTUBE_APP_BUNDLE="$app_bundle" \
    BRAVE_YOUTUBE_BIN="$browser_bin" \
    BRAVE_YOUTUBE_USER_DATA_DIR="$user_data" \
    "$session_script" stop
  [ "$status" -eq 0 ] || fail_test "Brave YouTube stop failed: $output"
  [[ "$output" == *"session is not running"* ]] || fail_test "Brave YouTube stop did not report stopped session"

  run env PATH="$fake_bin:$PATH" XDG_STATE_HOME="$state_home" HOME="$BATS_TEST_TMPDIR/home" \
    BRAVE_TEST_LOG="$log_file" BRAVE_TEST_PAGE_STATE="$page_state_file" \
    BRAVE_YOUTUBE_PMSET="$fake_bin/pmset" \
    BRAVE_YOUTUBE_SUDO="$fake_bin/sudo" \
    BRAVE_YOUTUBE_CAFFEINATE="$fake_bin/caffeinate" \
    BRAVE_YOUTUBE_OPEN="$fake_bin/open" \
    BRAVE_YOUTUBE_SLEEP="$fake_bin/sleep" \
    BRAVE_YOUTUBE_LAUNCHCTL="$fake_bin/launchctl" \
    BRAVE_YOUTUBE_PS="$fake_bin/ps" \
    BRAVE_YOUTUBE_KILL="$fake_bin/kill" \
    BRAVE_YOUTUBE_ID="$fake_bin/id" \
    BRAVE_YOUTUBE_DATE="$fake_bin/date" \
    BRAVE_YOUTUBE_CDP_HELPER="$fake_bin/cdp" \
    BRAVE_YOUTUBE_CLEAR_NEW_TAB_SYNC="$fake_bin/clear-sync" \
    BRAVE_YOUTUBE_APP_BUNDLE="$app_bundle" \
    BRAVE_YOUTUBE_BIN="$browser_bin" \
    BRAVE_YOUTUBE_USER_DATA_DIR="$user_data" \
    BRAVE_YOUTUBE_THEME_EXTENSION_DIR="$theme_dir" \
    BRAVE_YOUTUBE_CLEAR_NEW_TAB_EXTENSION_DIR="$clear_dir" \
    BRAVE_YOUTUBE_CHECK_INTERVAL=0 \
    "$session_script" daemon
  [ "$status" -eq 0 ] || fail_test "Brave YouTube daemon failed: $output"
  grep -Fq 'open -n' "$log_file" || fail_test "Brave YouTube daemon did not open the app"
  grep -Fq 'caffeinate -d -i -m -u' "$log_file" || fail_test "Brave YouTube daemon did not start caffeinate"
  grep -Fq 'close-browser close-browser' "$log_file" || fail_test "Brave YouTube daemon did not close the browser"
}

@test "Brave YouTube session covers lifecycle errors, running status, and low battery guardrails" {
  session_script="$repo_dir/bin/brave-youtube-session"
  fake_bin="$BATS_TEST_TMPDIR/bin"
  state_home="$BATS_TEST_TMPDIR/state"
  user_data="$BATS_TEST_TMPDIR/user-data"
  app_bundle="$BATS_TEST_TMPDIR/Brave YouTube.app"
  browser_bin="$app_bundle/Contents/MacOS/Brave YouTube.bin"
  theme_dir="$user_data/brave-youtube-theme"
  clear_dir="$user_data/clear-new-tab-extension"
  log_file="$BATS_TEST_TMPDIR/brave-lifecycle.log"
  mkdir -p "$fake_bin" "$(dirname "$browser_bin")" "$theme_dir" "$clear_dir" "$user_data/Default"
  printf '{}\n' > "$theme_dir/manifest.json"
  printf '{}\n' > "$clear_dir/manifest.json"
  printf '{}\n' > "$user_data/Default/Secure Preferences"
  printf '#!/usr/bin/env bash\n' > "$browser_bin"
  chmod +x "$browser_bin"

  write_fake_command "$fake_bin" pmset 'case "$*" in
  "-g ps") printf "Now drawing from '\''${BRAVE_TEST_POWER_SOURCE:-AC Power}'\''\n" ;;
  "-g batt") printf "Now drawing from '\''${BRAVE_TEST_POWER_SOURCE:-AC Power}'\''\n -InternalBattery-0 (id=1)\t${BRAVE_TEST_BATTERY:-90}%%; discharging; 0:10 remaining present: true\n" ;;
  "-g") printf "SleepDisabled          ${BRAVE_TEST_SLEEP_DISABLED:-0}\n" ;;
esac'
  write_fake_command "$fake_bin" sudo 'case "${BRAVE_TEST_SUDO_FAIL:-0}" in
  1) exit 1 ;;
  *) printf "sudo %s\n" "$*" >> "$BRAVE_TEST_LOG" ;;
esac'
  write_fake_command "$fake_bin" caffeinate 'printf "caffeinate %s\n" "$*" >> "$BRAVE_TEST_LOG"'
  write_fake_command "$fake_bin" open 'printf "open %s\n" "$*" >> "$BRAVE_TEST_LOG"'
  write_fake_command "$fake_bin" sleep ':'
  write_fake_command "$fake_bin" launchctl 'printf "launchctl %s\n" "$*" >> "$BRAVE_TEST_LOG"'
  write_fake_command "$fake_bin" id 'printf "501\n"'
  write_fake_command "$fake_bin" date 'printf "2026-06-23 12:00:00\n"'
  write_fake_command "$fake_bin" clear-sync 'case "${BRAVE_TEST_CLEAR_SYNC_FAIL:-0}" in 1) exit 1 ;; *) : ;; esac'
  write_fake_command "$fake_bin" cdp 'case "${1:-}" in
  page-state) printf "pages count=1\n" ;;
  media-state) printf "playing reason=test\n" ;;
  close-browser) printf "close-browser %s\n" "$*" >> "$BRAVE_TEST_LOG" ;;
esac'
  write_fake_command "$fake_bin" ps 'case "${BRAVE_TEST_PS_MODE:-stopped}" in
  command-running) printf "%s --user-data-dir=%s --profile-directory=Default\n" "$BRAVE_TEST_BROWSER_BIN" "$BRAVE_TEST_USER_DATA" ;;
  pid-running) printf "4321 %s --user-data-dir=%s --profile-directory=Default\n" "$BRAVE_TEST_BROWSER_BIN" "$BRAVE_TEST_USER_DATA" ;;
esac'
  write_fake_command "$fake_bin" kill 'if [ "${1:-}" = "-0" ]; then
  case "${BRAVE_TEST_KILL_ZERO:-fail}" in success) exit 0 ;; *) exit 1 ;; esac
fi
printf "kill %s\n" "$*" >> "$BRAVE_TEST_LOG"'
  write_fake_command "$fake_bin" tail 'printf "tail %s\n" "$*" >> "$BRAVE_TEST_LOG"'

  common_env=(
    PATH="$fake_bin:$PATH"
    XDG_STATE_HOME="$state_home"
    HOME="$BATS_TEST_TMPDIR/home"
    BRAVE_TEST_LOG="$log_file"
    BRAVE_TEST_BROWSER_BIN="$browser_bin"
    BRAVE_TEST_USER_DATA="$user_data"
    BRAVE_YOUTUBE_PMSET="$fake_bin/pmset"
    BRAVE_YOUTUBE_SUDO="$fake_bin/sudo"
    BRAVE_YOUTUBE_CAFFEINATE="$fake_bin/caffeinate"
    BRAVE_YOUTUBE_OPEN="$fake_bin/open"
    BRAVE_YOUTUBE_SLEEP="$fake_bin/sleep"
    BRAVE_YOUTUBE_LAUNCHCTL="$fake_bin/launchctl"
    BRAVE_YOUTUBE_PS="$fake_bin/ps"
    BRAVE_YOUTUBE_KILL="$fake_bin/kill"
    BRAVE_YOUTUBE_ID="$fake_bin/id"
    BRAVE_YOUTUBE_DATE="$fake_bin/date"
    BRAVE_YOUTUBE_CDP_HELPER="$fake_bin/cdp"
    BRAVE_YOUTUBE_CLEAR_NEW_TAB_SYNC="$fake_bin/clear-sync"
    BRAVE_YOUTUBE_APP_BUNDLE="$app_bundle"
    BRAVE_YOUTUBE_BIN="$browser_bin"
    BRAVE_YOUTUBE_USER_DATA_DIR="$user_data"
    BRAVE_YOUTUBE_THEME_EXTENSION_DIR="$theme_dir"
    BRAVE_YOUTUBE_CLEAR_NEW_TAB_EXTENSION_DIR="$clear_dir"
    BRAVE_YOUTUBE_CHECK_INTERVAL=0
  )

  run env "${common_env[@]}" "$session_script" nope
  [ "$status" -eq 2 ] || fail_test "Brave unknown command should exit 2"

  run env "${common_env[@]}" "$session_script" is-active
  [ "$status" -eq 1 ] || fail_test "Brave is-active should fail without a session"

  mkdir -p "$state_home/brave-youtube-session"
  printf '123\n' > "$state_home/brave-youtube-session/session.pid"
  run env "${common_env[@]}" BRAVE_TEST_KILL_ZERO=success "$session_script" start
  [ "$status" -eq 0 ] || fail_test "Brave start existing session failed: $output"
  [[ "$output" == *"already running"* ]] || fail_test "Brave start existing session did not report running session"

  run env "${common_env[@]}" BRAVE_TEST_KILL_ZERO=success BRAVE_TEST_PS_MODE=command-running "$session_script" status
  [ "$status" -eq 0 ] || fail_test "Brave running status failed: $output"
  [[ "$output" == *"browser_profile: running"* ]] || fail_test "Brave status did not report running browser profile"

  printf '123\n' > "$state_home/brave-youtube-session/session.pid"
  printf '456\n' > "$state_home/brave-youtube-session/caffeinate.pid"
  : > "$state_home/brave-youtube-session/power-active"
  printf '0\n' > "$state_home/brave-youtube-session/previous-sleep-disabled"
  run env "${common_env[@]}" BRAVE_TEST_KILL_ZERO=success "$session_script" stop
  [ "$status" -eq 0 ] || fail_test "Brave stop running session failed: $output"
  [[ "$output" == *"Stopped Brave YouTube session"* ]] || fail_test "Brave stop did not report stopped session"

  rm -rf "$state_home/brave-youtube-session"
  run env "${common_env[@]}" "$session_script" start
  [ "$status" -eq 1 ] || fail_test "Brave start should fail when launchctl does not create a session pid"
  [[ "$output" == *"session exited during startup"* ]] || fail_test "Brave start failure did not explain startup exit"

  rm -rf "$state_home/brave-youtube-session"
  run env "${common_env[@]}" BRAVE_TEST_POWER_SOURCE="Battery Power" BRAVE_TEST_BATTERY=10 \
    "$session_script" daemon
  [ "$status" -eq 0 ] || fail_test "Brave low-battery daemon guard failed: $output"
  grep -Fq 'not starting: battery 10% <= 20%' "$state_home/brave-youtube-session/session.log" \
    || fail_test "Brave low-battery guard did not log refusal"

  rm -rf "$state_home/brave-youtube-session" "$app_bundle"
  run env "${common_env[@]}" "$session_script" daemon
  [ "$status" -eq 1 ] || fail_test "Brave daemon should fail when app bundle is missing"
  [[ "$output" == *"app bundle not found"* ]] || fail_test "Brave missing app bundle message changed"
}

@test "language manager bootstrap helpers run installed and install paths" {
  fake_bin="$BATS_TEST_TMPDIR/bin"
  fake_home="$BATS_TEST_TMPDIR/home"
  log_file="$BATS_TEST_TMPDIR/language-managers.log"
  mkdir -p "$fake_bin" "$fake_home"

  write_fake_command "$fake_bin" pyenv 'printf "%s\n" "$*" >> "$LANG_TEST_LOG"
case "${1:-}" in
  latest) printf "3.14.0\n" ;;
esac'
  write_fake_command "$fake_bin" rbenv 'printf "%s\n" "$*" >> "$LANG_TEST_LOG"
if [ "${1:-}" = "install" ] && [ "${2:-}" = "--list" ]; then
  printf "  3.4.5\n  4.0.5\n"
fi'
  write_fake_command "$fake_bin" brew 'printf "/opt/homebrew/opt/openssl@3\n"'

  run env PATH="$fake_bin:$PATH" HOME="$fake_home" LANG_TEST_LOG="$log_file" \
    PYENV_ROOT="$fake_home/.pyenv" bash "$repo_dir/libexec/pyenv/ensure-python.sh"
  [ "$status" -eq 0 ] || fail_test "ensure-python failed: $output"

  run env PATH="$fake_bin:$PATH" HOME="$fake_home" LANG_TEST_LOG="$log_file" \
    RBENV_ROOT="$fake_home/.rbenv" bash "$repo_dir/libexec/rbenv/ensure-ruby.sh"
  [ "$status" -eq 0 ] || fail_test "ensure-ruby failed: $output"

  mkdir -p "$fake_home/.nvm"
  cat > "$fake_home/.nvm/nvm.sh" <<'NVM'
nvm() {
  printf '%s\n' "nvm $*" >> "$LANG_TEST_LOG"
}
NVM
  run env PATH="$fake_bin:$PATH" HOME="$fake_home" LANG_TEST_LOG="$log_file" \
    NVM_DIR="$fake_home/.nvm" NVM_SCRIPT="$fake_home/.nvm/nvm.sh" \
    bash "$repo_dir/libexec/nvm/ensure-node.sh"
  [ "$status" -eq 0 ] || fail_test "ensure-node failed: $output"

  mkdir -p "$fake_home/.sdkman/bin" "$fake_home/.sdkman/candidates/java"
  cat > "$fake_home/.sdkman/bin/sdkman-init.sh" <<'SDK'
sdk() {
  if [ -z "$sdkman_healthcheck_enable" ]; then
    :
  fi
  case "$1 $2" in
    "list java")
      printf '  25.0.3-amzn\n  21.0.11-amzn\n  17.0.19-amzn\n  11.0.31-amzn\n  8.0.472-amzn\n'
      ;;
    *)
      printf '%s\n' "sdk $*" >> "$LANG_TEST_LOG"
      ;;
  esac
}
SDK
  run env PATH="$fake_bin:$PATH" HOME="$fake_home" LANG_TEST_LOG="$log_file" \
    SDKMAN_DIR="$fake_home/.sdkman" bash "$repo_dir/libexec/sdkman/ensure-java.sh"
  [ "$status" -eq 0 ] || fail_test "ensure-java failed: $output"

  grep -Fq 'install -s 3.14.0' "$log_file" || fail_test "ensure-python did not install latest 3.x"
  grep -Fq 'install -s 4.0.5' "$log_file" || fail_test "ensure-ruby did not install latest stable Ruby"
  grep -Fq 'nvm install --lts' "$log_file" || fail_test "ensure-node did not install Node LTS"
  grep -Fq 'sdk install java 25.0.3-amzn' "$log_file" || fail_test "ensure-java did not install Corretto 25"
}

@test "language manager helpers cover already-installed and unavailable paths" {
  fake_bin="$BATS_TEST_TMPDIR/bin"
  fake_home="$BATS_TEST_TMPDIR/home"
  log_file="$BATS_TEST_TMPDIR/language-manager-branches.log"
  mkdir -p "$fake_bin" "$fake_home"

  write_fake_command "$fake_bin" pyenv 'printf "%s\n" "$*" >> "$LANG_TEST_LOG"'
  mkdir -p "$fake_home/.pyenv/versions/3.14.0"
  run env PATH="$fake_bin:$PATH" HOME="$fake_home" LANG_TEST_LOG="$log_file" \
    PYENV_ROOT="$fake_home/.pyenv" bash "$repo_dir/libexec/pyenv/ensure-python.sh"
  [ "$status" -eq 0 ] || fail_test "ensure-python existing version failed: $output"
  [[ "$output" == *"pyenv is already managing Python versions"* ]] || fail_test "ensure-python did not report existing version"

  write_fake_command "$fake_bin" rbenv 'printf "%s\n" "$*" >> "$LANG_TEST_LOG"'
  mkdir -p "$fake_home/.rbenv/versions/4.0.5"
  run env PATH="$fake_bin:$PATH" HOME="$fake_home" LANG_TEST_LOG="$log_file" \
    RBENV_ROOT="$fake_home/.rbenv" bash "$repo_dir/libexec/rbenv/ensure-ruby.sh"
  [ "$status" -eq 0 ] || fail_test "ensure-ruby existing version failed: $output"
  [[ "$output" == *"rbenv is already managing Ruby versions"* ]] || fail_test "ensure-ruby did not report existing version"

  rm -rf "$fake_home/.rbenv/versions"
  run env PATH="$fake_bin:$PATH" HOME="$fake_home" LANG_TEST_LOG="$log_file" \
    RBENV_ROOT="$fake_home/.rbenv" bash "$repo_dir/libexec/rbenv/ensure-ruby.sh"
  [ "$status" -eq 1 ] || fail_test "ensure-ruby should fail when no stable version is listed"

  mkdir -p "$fake_home/.nvm/versions/node/v24.0.0"
  cat > "$fake_home/.nvm/nvm.sh" <<'NVM'
nvm() { :; }
NVM
  run env PATH="$fake_bin:$PATH" HOME="$fake_home" LANG_TEST_LOG="$log_file" \
    NVM_DIR="$fake_home/.nvm" bash "$repo_dir/libexec/nvm/ensure-node.sh"
  [ "$status" -eq 0 ] || fail_test "ensure-node existing version failed: $output"
  [[ "$output" == *"nvm is already managing Node versions"* ]] || fail_test "ensure-node did not report existing version"

  run env PATH="$fake_bin:$PATH" HOME="$fake_home" SDKMAN_DIR="$fake_home/missing-sdkman" \
    bash "$repo_dir/libexec/sdkman/ensure-java.sh"
  [ "$status" -eq 1 ] || fail_test "ensure-java should fail when sdkman init is missing"

  mkdir -p "$fake_home/.sdkman/bin"
  printf ': sdkman init without sdk function\n' > "$fake_home/.sdkman/bin/sdkman-init.sh"
  run env PATH="$fake_bin:$PATH" HOME="$fake_home" SDKMAN_DIR="$fake_home/.sdkman" \
    bash "$repo_dir/libexec/sdkman/ensure-java.sh"
  [ "$status" -eq 1 ] || fail_test "ensure-java should fail when sdk function is missing"
  [[ "$output" == *"sdk command is not available"* ]] || fail_test "ensure-java missing sdk message changed"

  sdkman_nounset="$BATS_TEST_TMPDIR/sdkman-nounset"
  mkdir -p "$sdkman_nounset/bin" "$sdkman_nounset/candidates/java"
  cat > "$sdkman_nounset/bin/sdkman-init.sh" <<'SDK'
if [ -z "$ZSH_VERSION" ]; then
  :
fi
sdk() {
  case "$1 $2" in
    "list java")
      printf '  25.0.3-amzn\n  21.0.11-amzn\n  17.0.19-amzn\n  11.0.31-amzn\n  8.0.472-amzn\n'
      ;;
    *)
      printf '%s\n' "nounset sdk $*" >> "$LANG_TEST_LOG"
      ;;
  esac
}
SDK
  run env PATH="$fake_bin:$PATH" HOME="$fake_home" LANG_TEST_LOG="$log_file" \
    SDKMAN_DIR="$sdkman_nounset" bash "$repo_dir/libexec/sdkman/ensure-java.sh"
  [ "$status" -eq 0 ] || fail_test "ensure-java should tolerate SDKMAN init under nounset: $output"
  grep -Fq 'nounset sdk install java 25.0.3-amzn' "$log_file" \
    || fail_test "ensure-java did not use nounset-hostile SDKMAN init"

  sdkman_existing="$BATS_TEST_TMPDIR/sdkman-existing"
  mkdir -p "$sdkman_existing/bin" "$sdkman_existing/candidates/java"
  for version in 25.0.3-amzn 21.0.11-amzn 17.0.19-amzn 11.0.31-amzn 8.0.472-amzn; do
    mkdir -p "$sdkman_existing/candidates/java/$version/bin"
    printf '#!/usr/bin/env bash\n' > "$sdkman_existing/candidates/java/$version/bin/java"
    chmod +x "$sdkman_existing/candidates/java/$version/bin/java"
  done
  ln -s "$sdkman_existing/candidates/java/21.0.11-amzn" "$sdkman_existing/candidates/java/current"
  cat > "$sdkman_existing/bin/sdkman-init.sh" <<'SDK'
sdk() {
  case "$1 $2" in
    "list java")
      :
      ;;
    "default java")
      printf '%s\n' "sdk $*" >> "$LANG_TEST_LOG"
      ;;
    *)
      printf '%s\n' "unexpected sdk $*" >> "$LANG_TEST_LOG"
      ;;
  esac
}
SDK
  run env PATH="$fake_bin:$PATH" HOME="$fake_home" LANG_TEST_LOG="$log_file" \
    SDKMAN_DIR="$sdkman_existing" bash "$repo_dir/libexec/sdkman/ensure-java.sh"
  [ "$status" -eq 0 ] || fail_test "ensure-java existing JDK path failed: $output"
  [[ "$output" == *"restoring existing SDKMAN default Java: 21.0.11-amzn"* ]] \
    || fail_test "ensure-java did not restore existing default Java"
  grep -Fq 'sdk default java 21.0.11-amzn' "$log_file" \
    || fail_test "ensure-java did not call sdk default for the restored JDK"
}

@test "SDKMAN bootstrap installs through fake installer when sdkman is unavailable" {
  bootstrap_script="$repo_dir/libexec/sdkman/bootstrap.sh"
  fake_bin="$BATS_TEST_TMPDIR/bin"
  fake_home="$BATS_TEST_TMPDIR/home"
  sdkman_dir="$BATS_TEST_TMPDIR/sdkman"
  mkdir -p "$fake_bin" "$fake_home" "$sdkman_dir"

  write_fake_command "$fake_bin" curl 'cat <<INSTALLER
mkdir -p "$SDKMAN_DIR/bin"
cat > "$SDKMAN_DIR/bin/sdkman-init.sh" <<'\''SDK'\''
sdk() { :; }
SDK
INSTALLER'

  run env PATH="$fake_bin:$PATH" HOME="$fake_home" SDKMAN_DIR="$sdkman_dir" bash "$bootstrap_script"

  [ "$status" -eq 0 ] || fail_test "SDKMAN bootstrap failed: $output"
  [ -s "$sdkman_dir/bin/sdkman-init.sh" ] || fail_test "SDKMAN bootstrap did not create sdkman init script"

  usable_sdkman_dir="$BATS_TEST_TMPDIR/usable-sdkman"
  mkdir -p "$usable_sdkman_dir/bin"
  cat > "$usable_sdkman_dir/bin/sdkman-init.sh" <<'SDK'
if [ -z "$ZSH_VERSION" ]; then
  :
fi
sdk() { :; }
SDK
  run env PATH="$fake_bin:$PATH" HOME="$fake_home" SDKMAN_DIR="$usable_sdkman_dir" bash "$bootstrap_script"
  [ "$status" -eq 0 ] || fail_test "SDKMAN bootstrap usable path failed: $output"

  failing_sdkman_dir="$BATS_TEST_TMPDIR/failing-sdkman"
  mkdir -p "$failing_sdkman_dir"
  write_fake_command "$fake_bin" curl ':'
  run env PATH="$fake_bin:$PATH" HOME="$fake_home" SDKMAN_DIR="$failing_sdkman_dir" bash "$bootstrap_script"
  [ "$status" -eq 1 ] || fail_test "SDKMAN bootstrap should fail when installer does not create sdk"
  [[ "$output" == *"SDKMAN install did not produce a usable sdk command."* ]] \
    || fail_test "SDKMAN bootstrap failure message changed"
}

@test "VMware Fusion CLI wrapper dispatches mapped commands and rejects unknown commands" {
  vmware_wrapper="$repo_dir/libexec/virtual-machines/vmware-fusion-cli.sh"
  fake_app="$BATS_TEST_TMPDIR/VMware Fusion.app"
  log_file="$BATS_TEST_TMPDIR/vmware.log"
  mkdir -p "$fake_app/Contents/Library/VMware OVF Tool"

  for relative in \
    Contents/Library/vmcli \
    Contents/Library/vmrest \
    Contents/Library/vmrun \
    "Contents/Library/VMware OVF Tool/ovftool"
  do
    target="$fake_app/$relative"
    mkdir -p "$(dirname "$target")"
    cat > "$target" <<'FAKE'
#!/usr/bin/env bash
printf '%s\n' "$0 $*" >> "$VMWARE_TEST_LOG"
FAKE
    chmod +x "$target"
  done

  for command_name in vmcli vmrest vmrun ovftool; do
    run env VMWARE_FUSION_APP="$fake_app" VMWARE_FUSION_WRAPPER_NAME="$command_name" \
      VMWARE_TEST_LOG="$log_file" "$vmware_wrapper" --version
    [ "$status" -eq 0 ] || fail_test "$command_name wrapper failed: $output"
  done

  run env VMWARE_FUSION_APP="$fake_app" VMWARE_FUSION_WRAPPER_NAME=not-vmware \
    VMWARE_TEST_LOG="$log_file" "$vmware_wrapper"
  [ "$status" -eq 64 ] || fail_test "unknown VMware wrapper command should exit 64, got $status"
  [[ "$output" == *"unsupported VMware Fusion wrapper name"* ]] \
    || fail_test "unknown VMware wrapper command did not explain the problem"

  run -127 env VMWARE_FUSION_APP="$BATS_TEST_TMPDIR/missing.app" VMWARE_FUSION_WRAPPER_NAME=vmrun \
    VMWARE_TEST_LOG="$log_file" "$vmware_wrapper"
  [ "$status" -eq 127 ] || fail_test "missing VMware app should exit 127, got $status"
  [[ "$output" == *"VMware Fusion is not installed"* ]] \
    || fail_test "missing VMware app message changed"

  grep -Fq 'Contents/Library/vmcli --version' "$log_file" || fail_test "vmcli did not dispatch"
  grep -Fq 'Contents/Library/vmrest --version' "$log_file" || fail_test "vmrest did not dispatch"
  grep -Fq 'Contents/Library/vmrun --version' "$log_file" || fail_test "vmrun did not dispatch"
  grep -Fq 'Contents/Library/VMware OVF Tool/ovftool --version' "$log_file" || fail_test "ovftool did not dispatch"
}

@test "host security helpers run against fake firewall and pfctl commands" {
  stealth_script="$repo_dir/libexec/host-security/host-stealth-mode.sh"
  egress_script="$repo_dir/libexec/host-security/host-egress-pf.sh"
  fake_bin="$BATS_TEST_TMPDIR/bin"
  log_file="$BATS_TEST_TMPDIR/host-security.log"
  rules_file="$BATS_TEST_TMPDIR/egress.pf"
  mkdir -p "$fake_bin"
  printf 'block drop out\n' > "$rules_file"

  write_fake_command "$fake_bin" netstat 'cat <<ROUTES
default            192.168.1.1        UGScg           en0
10.0.0/8           utun4              USc             utun4
ROUTES'
  write_fake_command "$fake_bin" arp 'printf "? (192.168.1.1) at aa-bb-cc-dd-ee-ff on en0 ifscope [ethernet]\n"'
  write_fake_command "$fake_bin" socketfilterfw 'printf "firewall %s\n" "$*" >> "$HOST_SECURITY_LOG"
case "$*" in
  --getstealthmode*) printf "Stealth mode is off\n" ;;
esac'
  write_fake_command "$fake_bin" pfctl 'printf "pfctl %s\n" "$*" >> "$HOST_SECURITY_LOG"
case "$*" in
  *"-f "*) printf "pfctl: Use of -f option, could result in flushing of rules\n" ;;
  "-s info") printf "Status: Disabled\n" ;;
esac'

  run env HOST_SECURITY_LOG="$log_file" \
    TRUSTED_GATEWAY_MACS="aa:bb:cc:dd:ee:ff" \
    HOST_STEALTH_MODE_NETSTAT="$fake_bin/netstat" \
    HOST_STEALTH_MODE_ARP="$fake_bin/arp" \
    HOST_STEALTH_MODE_FIREWALL="$fake_bin/socketfilterfw" \
    "$stealth_script"
  [ "$status" -eq 0 ] || fail_test "host stealth mode failed: $output"
  grep -Fq 'firewall --getstealthmode' "$log_file" || fail_test "host stealth mode did not read firewall state"
  if grep -Fq 'firewall --setstealthmode' "$log_file"; then
    fail_test "a matching explicit hint must preserve the already-off fixture state"
  fi

  : > "$log_file"
  run env HOST_SECURITY_LOG="$log_file" \
    TRUSTED_GATEWAY_MACS="" \
    HOST_STEALTH_MODE_NETSTAT="$fake_bin/netstat" \
    HOST_STEALTH_MODE_ARP="$fake_bin/arp" \
    HOST_STEALTH_MODE_FIREWALL="$fake_bin/socketfilterfw" \
    "$stealth_script"
  [ "$status" -eq 0 ] || fail_test "empty gateway hints must be valid: $output"
  grep -Fq 'firewall --setstealthmode on' "$log_file" \
    || fail_test "an empty hint list must enable stealth even with a discovered gateway"

  run env HOST_SECURITY_LOG="$log_file" HOST_EGRESS_RULES="$rules_file" \
    HOST_EGRESS_PFCTL="$fake_bin/pfctl" "$egress_script"
  [ "$status" -eq 0 ] || fail_test "host egress pf failed: $output"
  grep -Fq 'pfctl -q -a com.apple/nix-darwin-host-egress -f' "$log_file" \
    || fail_test "host egress pf did not load anchor rules"
  grep -Fq 'pfctl -E' "$log_file" || fail_test "host egress pf did not enable pf when disabled"

  write_fake_command "$fake_bin" pfctl 'printf "pfctl %s\n" "$*" >> "$HOST_SECURITY_LOG"
for arg in "$@"; do
  if [ "$arg" = "-f" ]; then
    printf "pfctl: fake load failure\n"
    exit 1
  fi
done'
  run env HOST_SECURITY_LOG="$log_file" HOST_EGRESS_RULES="$rules_file" \
    HOST_EGRESS_PFCTL="$fake_bin/pfctl" "$egress_script"
  [ "$status" -eq 1 ] || fail_test "host egress pf should fail when pfctl cannot load rules"
  [[ "$output" == *"fake load failure"* ]] || fail_test "host egress pf did not print pfctl failure"

  : > "$log_file"
  run env HOST_SECURITY_LOG="$log_file" \
    TRUSTED_GATEWAY_MACS="11:22:33:44:55:66" \
    HOST_STEALTH_MODE_NETSTAT="$fake_bin/netstat" \
    HOST_STEALTH_MODE_ARP="$fake_bin/arp" \
    HOST_STEALTH_MODE_FIREWALL="$fake_bin/socketfilterfw" \
    "$stealth_script"
  [ "$status" -eq 0 ] || fail_test "host stealth mode untrusted gateway failed: $output"
  grep -Fq 'firewall --setstealthmode on' "$log_file" \
    || fail_test "host stealth mode did not enable stealth on untrusted gateway"
}

@test "shell profile fragments and bash theme execute under Bash" {
  fake_home="$BATS_TEST_TMPDIR/home"
  mkdir -p "$fake_home/.sh_config/common" "$fake_home/.sh_config/bash" "$fake_home/.nvm" "$fake_home/.sdkman/bin"
  printf 'export COMMON_FRAGMENT_RAN=1\n' > "$fake_home/.sh_config/common/001_common"
  printf 'export LOGIN_FRAGMENT_RAN=1\n' > "$fake_home/.sh_config/common/999_on-login"
  printf 'export BASH_FRAGMENT_RAN=1\n' > "$fake_home/.sh_config/bash/001_bash"
  printf 'export BASHRC_RAN=1\n' > "$fake_home/.bashrc"
  printf 'export NVM_LOADED=1\n' > "$fake_home/.nvm/nvm.sh"
  printf 'export NVM_COMPLETION_LOADED=1\n' > "$fake_home/.nvm/bash_completion"
  printf 'export SDKMAN_LOADED=1\n' > "$fake_home/.sdkman/bin/sdkman-init.sh"
  nvm_log="$BATS_TEST_TMPDIR/nvm-fallback.log"
  printf 'printf "nvm local fallback\n" >> "$NVM_TEST_LOG"\n' > "$fake_home/.nvm/nvm.sh"
  printf 'printf "nvm completion fallback\n" >> "$NVM_TEST_LOG"\n' > "$fake_home/.nvm/bash_completion"

  run env HOME="$fake_home" bash "$repo_dir/shell/bash_profile"
  [ "$status" -eq 0 ] || fail_test "bash_profile failed: $output"

  run env HOME="$fake_home" bash "$repo_dir/shell/bashrc"
  [ "$status" -eq 0 ] || fail_test "bashrc failed: $output"

  run env HOME="$fake_home" NVM_DIR="$fake_home/.nvm" NVM_TEST_LOG="$nvm_log" \
    NVM_HOMEBREW_SCRIPT="$BATS_TEST_TMPDIR/missing-nvm.sh" /bin/bash "$repo_dir/shell/sh_config/common/003_nvm"
  [ "$status" -eq 0 ] || fail_test "003_nvm failed: $output"

  run env HOME="$fake_home" SDKMAN_DIR="$fake_home/.sdkman" bash "$repo_dir/shell/sh_config/common/007_sdkman"
  [ "$status" -eq 0 ] || fail_test "007_sdkman failed: $output"

  run env HOME="$fake_home" NVM_DIR="$fake_home/.nvm" NVM_TEST_LOG="$nvm_log" \
    NVM_HOMEBREW_COMPLETION="$BATS_TEST_TMPDIR/missing-nvm-completion" \
    /bin/bash "$repo_dir/shell/sh_config/bash/nvm_completion"
  [ "$status" -eq 0 ] || fail_test "nvm_completion failed: $output"
  run env HOME="$fake_home" NVM_DIR="$fake_home/.nvm" NVM_TEST_LOG="$nvm_log" \
    NVM_HOMEBREW_SCRIPT="$BATS_TEST_TMPDIR/missing-nvm.sh" \
    NVM_HOMEBREW_COMPLETION="$BATS_TEST_TMPDIR/missing-nvm-completion" \
    bash "$repo_dir/src/test/support/bash/nvm-fallback-coverage-harness"
  [ "$status" -eq 0 ] || fail_test "nvm fallback harness failed: $output"
  fake_homebrew_nvm="$BATS_TEST_TMPDIR/homebrew-nvm.sh"
  fake_homebrew_completion="$BATS_TEST_TMPDIR/homebrew-nvm-completion"
  printf 'printf "nvm homebrew script\n" >> "$NVM_TEST_LOG"\n' > "$fake_homebrew_nvm"
  printf 'printf "nvm homebrew completion\n" >> "$NVM_TEST_LOG"\n' > "$fake_homebrew_completion"
  run env HOME="$fake_home" NVM_DIR="$fake_home/.nvm" NVM_TEST_LOG="$nvm_log" \
    NVM_HOMEBREW_SCRIPT="$fake_homebrew_nvm" \
    NVM_HOMEBREW_COMPLETION="$fake_homebrew_completion" \
    bash "$repo_dir/src/test/support/bash/nvm-fallback-coverage-harness"
  [ "$status" -eq 0 ] || fail_test "nvm homebrew harness failed: $output"
  grep -Fq "nvm local fallback" "$nvm_log" || fail_test "003_nvm did not source the local fallback"
  grep -Fq "nvm completion fallback" "$nvm_log" || fail_test "nvm_completion did not source the local fallback"
  grep -Fq "nvm homebrew script" "$nvm_log" || fail_test "003_nvm did not source the Homebrew override"
  grep -Fq "nvm homebrew completion" "$nvm_log" || fail_test "nvm_completion did not source the Homebrew override"

  run env HOME="$fake_home" TERM=xterm-256color bash "$repo_dir/shell/sh_config/bash/steeef-custom.bash-theme"
  [ "$status" -eq 0 ] || fail_test "bash theme direct execution failed: $output"

  run env HOME="$fake_home" TERM=xterm-256color bash -c \
    "source '$repo_dir/shell/sh_config/bash/steeef-custom.bash-theme'; steeef_setup_colors; steeef_title_path >/dev/null; steeef_prompt_command; [[ -n \${PS1:-} ]]"
  [ "$status" -eq 0 ] || fail_test "bash theme failed: $output"

  git_dir="$BATS_TEST_TMPDIR/git/.git"
  common_dir="$BATS_TEST_TMPDIR/git-common"
  fake_bin="$BATS_TEST_TMPDIR/theme-bin"
  mkdir -p "$git_dir" "$common_dir" "$fake_bin"
  write_fake_command "$fake_bin" git 'case "$*" in
  "rev-parse --git-dir") printf "%s\n" "$THEME_TEST_GIT_DIR" ;;
  "rev-parse --git-common-dir") printf "%s\n" "$THEME_TEST_COMMON_DIR" ;;
  "rev-parse --is-inside-work-tree") exit 0 ;;
  "symbolic-ref --quiet --short HEAD") printf "main\n" ;;
  "diff --quiet --ignore-submodules=dirty --") exit 1 ;;
  "diff --cached --quiet --ignore-submodules=dirty --") exit 1 ;;
  "status --porcelain --untracked-files=normal --ignore-submodules=dirty") printf "?? new-file\n" ;;
esac'

  run env PATH="$fake_bin:$PATH" HOME="$fake_home" PWD="$fake_home" TERM=xterm-256color \
    THEME_TEST_GIT_DIR="$git_dir" THEME_TEST_COMMON_DIR="$common_dir" bash -c \
    "cd \"\$HOME\"; source '$repo_dir/shell/sh_config/bash/steeef-custom.bash-theme'; TERM=dumb steeef_setup_colors; VIRTUAL_ENV=/tmp/theme-venv steeef_virtualenv_info >/dev/null; steeef_title_path >/dev/null; cd \"\$HOME/subdir\" 2>/dev/null || mkdir -p \"\$HOME/subdir\" && cd \"\$HOME/subdir\"; steeef_title_path >/dev/null; steeef_git_info >/dev/null"
  [ "$status" -eq 0 ] || fail_test "bash theme git marker path failed: $output"

  for action in rebase merge cherry-pick revert bisect; do
    rm -rf "$git_dir/rebase-merge" "$git_dir/rebase-apply" \
      "$common_dir/MERGE_HEAD" "$common_dir/CHERRY_PICK_HEAD" \
      "$common_dir/REVERT_HEAD" "$common_dir/BISECT_LOG"
    case "$action" in
      rebase) mkdir -p "$git_dir/rebase-merge" ;;
      merge) : > "$common_dir/MERGE_HEAD" ;;
      cherry-pick) : > "$common_dir/CHERRY_PICK_HEAD" ;;
      revert) : > "$common_dir/REVERT_HEAD" ;;
      bisect) : > "$common_dir/BISECT_LOG" ;;
    esac
    run env PATH="$fake_bin:$PATH" HOME="$fake_home" TERM=xterm-256color \
      THEME_TEST_GIT_DIR="$git_dir" THEME_TEST_COMMON_DIR="$common_dir" bash -c \
      "source '$repo_dir/shell/sh_config/bash/steeef-custom.bash-theme'; steeef_setup_colors; steeef_git_action >/dev/null"
    [ "$status" -eq 0 ] || fail_test "bash theme did not report git action $action: $output"
  done
}

@test "coverage harnesses exercise sourced Bash functions under kcov" {
  fake_bin="$BATS_TEST_TMPDIR/bin"
  fake_home="$BATS_TEST_TMPDIR/home"
  fake_state="$BATS_TEST_TMPDIR/state"
  fake_user_data="$BATS_TEST_TMPDIR/user-data"
  fake_app="$BATS_TEST_TMPDIR/Brave YouTube.app"
  fake_browser_bin="$fake_app/Contents/MacOS/Brave YouTube.bin"
  log_file="$BATS_TEST_TMPDIR/harness.log"
  mkdir -p "$fake_bin" "$fake_home" "$fake_user_data" "$(dirname "$fake_browser_bin")"
  printf '#!/usr/bin/env bash\n' > "$fake_browser_bin"
  chmod +x "$fake_browser_bin"

  theme_git_dir="$BATS_TEST_TMPDIR/theme-git/.git"
  theme_common_dir="$BATS_TEST_TMPDIR/theme-git-common"
  mkdir -p "$theme_git_dir" "$theme_common_dir"
  write_fake_command "$fake_bin" git 'case "$*" in
  "config user.email") printf "operator@example.invalid\n" ;;
  "rev-parse --git-dir") printf "%s\n" "$THEME_TEST_GIT_DIR" ;;
  "rev-parse --git-common-dir") printf "%s\n" "$THEME_TEST_COMMON_DIR" ;;
  "rev-parse --is-inside-work-tree") exit 0 ;;
  "symbolic-ref --quiet --short HEAD") printf "main\n" ;;
  "diff --quiet --ignore-submodules=dirty --") exit 1 ;;
  "diff --cached --quiet --ignore-submodules=dirty --") exit 1 ;;
  "status --porcelain --untracked-files=normal --ignore-submodules=dirty") printf "?? new-file\n" ;;
  *) exit 1 ;;
esac'
  write_fake_command "$fake_bin" ssh-keygen 'key_path=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -f) shift; key_path="$1" ;;
    -y) printf "ssh-ed25519 fake-public-key\n"; exit 0 ;;
  esac
  shift
done
if [ -n "$key_path" ]; then
  printf "fake-private-key\n" > "$key_path"
  printf "ssh-ed25519 fake-public-key\n" > "$key_path.pub"
fi'

  run env PATH="$fake_bin:$PATH" HOME="$fake_home" TERM=xterm-256color \
    THEME_TEST_GIT_DIR="$theme_git_dir" THEME_TEST_COMMON_DIR="$theme_common_dir" \
    bash "$repo_dir/src/test/support/bash/theme-coverage-harness"
  [ "$status" -eq 0 ] || fail_test "theme coverage harness failed: $output"

  run env PATH="$fake_bin:$PATH" HOME="$fake_home" XDG_STATE_HOME="$fake_state" \
    BRAVE_YOUTUBE_APP_BUNDLE="$fake_app" \
    BRAVE_YOUTUBE_BIN="$fake_browser_bin" \
    BRAVE_YOUTUBE_USER_DATA_DIR="$fake_user_data" \
    BRAVE_YOUTUBE_THEME_EXTENSION_DIR="$fake_user_data/brave-youtube-theme" \
    BRAVE_YOUTUBE_CLEAR_NEW_TAB_EXTENSION_DIR="$fake_user_data/clear-new-tab-extension" \
    bash "$repo_dir/src/test/support/bash/brave-youtube-session-coverage-harness"
  [ "$status" -eq 0 ] || fail_test "Brave YouTube coverage harness failed: $output"
}
