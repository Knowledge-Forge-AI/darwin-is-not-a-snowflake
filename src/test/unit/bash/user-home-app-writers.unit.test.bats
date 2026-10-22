#!/usr/bin/env bats
# nix-darwin-test-source-target: modules/kitty-app-writer.sh
# nix-darwin-test-source-target: modules/brave-youtube-app-writer.sh

setup() {
  repo_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../../.." && pwd)"
  common="$repo_dir/modules/user-home-writer-common.sh"
  test_user="$(/usr/bin/id -un)"
  test_home="$BATS_TEST_TMPDIR/home"
  mkdir -m 0755 "$test_home"
  make_stubs
}

make_stubs() {
  stub_dir="$BATS_TEST_TMPDIR/stubs"
  mkdir "$stub_dir"
  iconutil_stub="$stub_dir/iconutil"
  magick_stub="$stub_dir/magick"
  command_stub="$stub_dir/command"
  cat >"$iconutil_stub" <<'EOF'
#!/bin/sh
kind=
output=
while [ "$#" -gt 0 ]; do
  case "$1" in
    -c) kind=$2; shift 2 ;;
    -o) output=$2; shift 2 ;;
    *) shift ;;
  esac
done
case "$kind" in
  iconset)
    mkdir -p "$output"
    : >"$output/icon_128x128.png"
    ;;
  icns)
    mkdir -p "$(dirname "$output")"
    : >"$output"
    ;;
  *) exit 1 ;;
esac
EOF
  cat >"$magick_stub" <<'EOF'
#!/bin/sh
if [ "$1" = identify ]; then
  printf '128 128\n'
fi
EOF
  cat >"$command_stub" <<'EOF'
#!/bin/sh
exit 0
EOF
  chmod +x "$iconutil_stub" "$magick_stub" "$command_stub"
}

make_kitty_source() {
  kitty_source="$BATS_TEST_TMPDIR/kitty.app"
  mkdir -p "$kitty_source/Contents/MacOS" "$kitty_source/Contents/Resources" "$kitty_source/Contents/Frameworks"
  printf 'kitty\n' >"$kitty_source/Contents/MacOS/kitty"
  printf 'kitten\n' >"$kitty_source/Contents/MacOS/kitten"
  printf 'icon\n' >"$kitty_source/Contents/Resources/kitty.icns"
  printf 'resource\n' >"$kitty_source/Contents/Resources/terminfo"
  font_file="$BATS_TEST_TMPDIR/font.ttf"
  : >"$font_file"
}

run_kitty_writer() {
  run /bin/bash "$repo_dir/modules/kitty-app-writer.sh" \
    "$common" "$test_user" "$test_home" "$kitty_source" \
    "$repo_dir/config/kitty/kitty.conf" \
    "$repo_dir/config/kitty/launcher.conf.template" \
    "$repo_dir/config/kitty/launcher-bash.conf.fragment" \
    "$repo_dir/config/kitty/launcher.session.template" \
    "$repo_dir/config/kitty/launcher-info.plist.template" \
    "$magick_stub" "$font_file" "$iconutil_stub" \
    "$command_stub" "$command_stub" "$command_stub" \
    com.operator.kitty.zsh com.operator.kitty.bash
}

make_brave_source() {
  brave_source="$BATS_TEST_TMPDIR/Brave Browser.app"
  mkdir -p \
    "$brave_source/Contents/MacOS" \
    "$brave_source/Contents/Resources" \
    "$brave_source/Contents/Library/LaunchServices" \
    "$brave_source/Contents/Frameworks/Brave Browser Framework.framework/Versions/1/Helpers/BraveUpdater.app" \
    "$brave_source/Contents/Frameworks/Brave Browser Framework.framework/Versions/1/Frameworks/Sparkle.framework"
  printf 'brave\n' >"$brave_source/Contents/MacOS/Brave Browser"
  printf 'updater\n' >"$brave_source/Contents/Library/LaunchServices/com.brave.Browser.UpdaterPrivilegedHelper"
  /usr/bin/plutil -create xml1 "$brave_source/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c 'Add :CFBundleIdentifier string com.brave.Browser' "$brave_source/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c 'Add :CFBundleShortVersionString string 1.2.3' "$brave_source/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c 'Add :CFBundleVersion string 123' "$brave_source/Contents/Info.plist"
}

run_brave_writer() {
  run /bin/bash "$repo_dir/modules/brave-youtube-app-writer.sh" \
    "$common" "$test_user" "$test_home" "$brave_source" \
    "$repo_dir/config/brave-youtube/launcher.sh.template" \
    "$repo_dir/config/brave-youtube/theme-manifest.json" \
    "$repo_dir/libexec/brave-youtube/generate-icon.py" \
    "$repo_dir/libexec/brave-youtube/generate-theme-assets.py" \
    /usr/bin/python3 "$iconutil_stub" /usr/libexec/PlistBuddy \
    "$command_stub" "$command_stub" "$command_stub" com.operator.brave-youtube
}

fingerprint() {
  /usr/bin/stat -f '%u:%g:%Lp:%z' "$1"
  /usr/bin/shasum -a 256 "$1"
}

@test "Kitty writer builds exact launchers, repeats, and preserves a wrong identity" {
  make_kitty_source
  run_kitty_writer
  [ "$status" -eq 0 ]
  [ "$(readlink "$test_home/.config/kitty/kitty.conf")" = "$repo_dir/config/kitty/kitty.conf" ]
  [ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$test_home/Applications/Kitty Zsh.app/Contents/Info.plist")" = com.operator.kitty.zsh ]
  [ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$test_home/Applications/Kitty Bash.app/Contents/Info.plist")" = com.operator.kitty.bash ]
  [ -x "$test_home/Applications/Kitty Zsh.app/Contents/MacOS/kitty-zsh" ]
  [ -x "$test_home/Applications/Kitty Bash.app/Contents/MacOS/kitty-bash" ]
  rg -F -q 'shell /bin/zsh -l' "$test_home/.config/kitty/kitty-zsh/kitty.conf"
  rg -F -q "/usr/bin/login -fp $test_user /run/current-system/sw/bin/bash" "$test_home/.config/kitty/kitty-bash/kitty.conf"

  sentinel="$BATS_TEST_TMPDIR/kitty-internal-sentinel"
  printf 'protected\n' >"$sentinel"
  before="$(fingerprint "$sentinel")"
  ln -s "$sentinel" "$test_home/Applications/Kitty Zsh.app/Contents/external-link"
  run_kitty_writer
  [ "$status" -eq 0 ]
  [ "$(fingerprint "$sentinel")" = "$before" ]

  wrong_app="$test_home/Applications/Kitty Zsh.app"
  /usr/libexec/PlistBuddy -c 'Set :CFBundleIdentifier com.example.foreign' "$wrong_app/Contents/Info.plist"
  printf 'preserve\n' >"$wrong_app/foreign-marker"
  run_kitty_writer
  [ "$status" -eq 0 ]
  backup="$(find "$test_home/Applications" -maxdepth 1 -name 'Kitty Zsh.app.before-nix-darwin*' -print -quit)"
  [ -n "$backup" ]
  [ "$(<"$backup/foreign-marker")" = preserve ]
  [ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$test_home/Applications/Kitty Zsh.app/Contents/Info.plist")" = com.operator.kitty.zsh ]
}

@test "Kitty writer rejects an Applications symlink without touching its sentinel" {
  make_kitty_source
  sentinel="$BATS_TEST_TMPDIR/kitty-sentinel"
  mkdir "$sentinel"
  printf 'protected\n' >"$sentinel/value"
  before="$(fingerprint "$sentinel/value")"
  ln -s "$sentinel" "$test_home/Applications"

  run_kitty_writer
  [ "$status" -ne 0 ]
  [ "$(fingerprint "$sentinel/value")" = "$before" ]
  [ ! -e "$sentinel/Kitty Zsh.app" ]
}

@test "Kitty launcher target substitution preserves the link and external target" {
  make_kitty_source
  mkdir "$test_home/Applications"
  sentinel="$BATS_TEST_TMPDIR/kitty-launcher-sentinel"
  mkdir "$sentinel"
  printf 'protected\n' >"$sentinel/value"
  before="$(fingerprint "$sentinel/value")"
  ln -s "$sentinel" "$test_home/Applications/Kitty Zsh.app"

  run_kitty_writer
  [ "$status" -eq 0 ]
  [ "$(fingerprint "$sentinel/value")" = "$before" ]
  backup="$(find "$test_home/Applications" -maxdepth 1 -name 'Kitty Zsh.app.before-nix-darwin*' -print -quit)"
  [ -L "$backup" ]
  [ "$(readlink "$backup")" = "$sentinel" ]
}

@test "Brave YouTube writer builds exact app/profile state, preserves narrower Library mode, repeats, and preserves wrong identity" {
  mkdir -m 0700 "$test_home/Library"
  mkdir -m 0700 "$test_home/Library/Application Support"
  make_brave_source
  run_brave_writer
  [ "$status" -eq 0 ]
  [ "$(stat -f '%Lp' "$test_home/Library")" = 700 ]
  [ "$(stat -f '%Lp' "$test_home/Library/Application Support")" = 700 ]
  [ "$(stat -f '%Lp' "$test_home/Library/Application Support/Brave YouTube")" = 700 ]
  app="$test_home/Applications/Brave YouTube.app"
  profile="$test_home/Library/Application Support/Brave YouTube"
  [ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$app/Contents/Info.plist")" = com.operator.brave-youtube ]
  [ -x "$app/Contents/MacOS/Brave YouTube" ]
  [ -x "$app/Contents/MacOS/Brave YouTube.bin" ]
  [ -f "$profile/brave-youtube-theme/manifest.json" ]
  [ ! -e "$app/Contents/Library/LaunchServices/com.brave.Browser.UpdaterPrivilegedHelper" ]
  sentinel="$BATS_TEST_TMPDIR/brave-internal-sentinel"
  printf 'protected\n' >"$sentinel"
  before="$(fingerprint "$sentinel")"
  ln -s "$sentinel" "$app/Contents/external-link"
  run_brave_writer
  [ "$status" -eq 0 ]
  [ "$(fingerprint "$sentinel")" = "$before" ]

  /usr/libexec/PlistBuddy -c 'Set :CFBundleIdentifier com.example.foreign' "$app/Contents/Info.plist"
  printf 'preserve\n' >"$app/foreign-marker"
  run_brave_writer
  [ "$status" -eq 0 ]
  backup="$(find "$test_home/Applications" -maxdepth 1 -name 'Brave YouTube.app.before-nix-darwin*' -print -quit)"
  [ -n "$backup" ]
  [ "$(<"$backup/foreign-marker")" = preserve ]
  [ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$app/Contents/Info.plist")" = com.operator.brave-youtube ]
}

@test "Brave app target substitution cannot redirect recursive mutation" {
  make_brave_source
  mkdir "$test_home/Applications"
  sentinel="$BATS_TEST_TMPDIR/brave-app-sentinel"
  mkdir "$sentinel"
  printf 'protected\n' >"$sentinel/value"
  before="$(fingerprint "$sentinel/value")"
  ln -s "$sentinel" "$test_home/Applications/Brave YouTube.app"

  run_brave_writer
  [ "$status" -eq 0 ]
  [ "$(fingerprint "$sentinel/value")" = "$before" ]
  backup="$(find "$test_home/Applications" -maxdepth 1 -name 'Brave YouTube.app.before-nix-darwin*' -print -quit)"
  [ -L "$backup" ]
  [ "$(readlink "$backup")" = "$sentinel" ]
}

@test "Brave writer rejects a profile-root symlink without external mutation" {
  make_brave_source
  mkdir "$test_home/Library"
  sentinel="$BATS_TEST_TMPDIR/brave-profile-sentinel"
  mkdir "$sentinel"
  printf 'protected\n' >"$sentinel/value"
  before="$(fingerprint "$sentinel/value")"
  ln -s "$sentinel" "$test_home/Library/Application Support"

  run_brave_writer
  [ "$status" -ne 0 ]
  [ "$(fingerprint "$sentinel/value")" = "$before" ]
  [ ! -e "$sentinel/Brave YouTube" ]
}
