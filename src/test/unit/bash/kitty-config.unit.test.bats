#!/usr/bin/env bats

@test "kitty-config.unit.test" {
set -euo pipefail

script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
repo_dir="$(cd "$script_dir/../../../.." && pwd)"

fail() {
  echo "test-kitty-config: $*" >&2
  exit 1
}

module_source_file="$repo_dir/modules/kitty.nix"
writer_file="$repo_dir/modules/kitty-app-writer.sh"
module_file="$BATS_TEST_TMPDIR/kitty-implementation"
cat "$module_source_file" "$writer_file" >"$module_file"
config_file="$repo_dir/config/kitty/kitty.conf"
launcher_config_template="$repo_dir/config/kitty/launcher.conf.template"
launcher_bash_config_fragment="$repo_dir/config/kitty/launcher-bash.conf.fragment"
launcher_session_template="$repo_dir/config/kitty/launcher.session.template"
launcher_plist_template="$repo_dir/config/kitty/launcher-info.plist.template"

[ -f "$module_file" ] || fail "missing modules/kitty.nix"
[ -f "$config_file" ] || fail "missing config/kitty/kitty.conf"
[ -f "$launcher_config_template" ] || fail "missing config/kitty/launcher.conf.template"
[ -f "$launcher_bash_config_fragment" ] || fail "missing config/kitty/launcher-bash.conf.fragment"
[ -f "$launcher_session_template" ] || fail "missing config/kitty/launcher.session.template"
[ -f "$launcher_plist_template" ] || fail "missing config/kitty/launcher-info.plist.template"

/usr/bin/plutil -lint "$launcher_plist_template" >/dev/null \
  || fail "kitty launcher plist template must be valid plist XML"

rg -F -q 'kittyLauncherConfigTemplate = ../config/kitty/launcher.conf.template;' "$module_file" \
  || fail "kitty module must source config/kitty/launcher.conf.template"
rg -F -q 'kittyLauncherBashConfigFragment = ../config/kitty/launcher-bash.conf.fragment;' "$module_file" \
  || fail "kitty module must source config/kitty/launcher-bash.conf.fragment"
rg -F -q 'kittyLauncherSessionTemplate = ../config/kitty/launcher.session.template;' "$module_file" \
  || fail "kitty module must source config/kitty/launcher.session.template"
rg -F -q 'kittyLauncherPlistTemplate = ../config/kitty/launcher-info.plist.template;' "$module_file" \
  || fail "kitty module must source config/kitty/launcher-info.plist.template"
if rg -F -q '<<KITTYCONF' "$module_file" ||
  rg -F -q '<<KITTYBASHCONF' "$module_file" ||
  rg -F -q '<<SESSION' "$module_file" ||
  rg -F -q '<<PLIST' "$module_file"; then
  fail "kitty module must not embed launcher config, session, or plist heredocs"
fi

rg -q 'homebrew\.casks = \[' "$module_file" \
  || fail "kitty module must install the Homebrew cask"
rg -q '"kitty"' "$module_file" \
  || fail "kitty module must include the kitty cask"
rg -q 'create_kitty_launcher "Kitty Zsh".*"Z"' "$module_file" \
  || fail "kitty module must create a zsh launcher app"
rg -q 'create_kitty_launcher "Kitty Bash".*"B"' "$module_file" \
  || fail "kitty module must create a bash launcher app"
rg -q '\$app_name\.app' "$module_file" \
  || fail "kitty launcher helper must create app bundles"
rg -q 'com\.operator\.kitty\.zsh' "$module_file" \
  || fail "kitty zsh launcher must have a stable bundle id"
rg -q 'com\.operator\.kitty\.bash' "$module_file" \
  || fail "kitty bash launcher must have a stable bundle id"
rg -F -q '/bin/cp "$source_app/Contents/MacOS/kitty" "$executable_file"' "$module_file" \
  || fail "kitty launcher app executable must be a copied native kitty binary"
rg -F -q '/bin/ln -s "$source_app/Contents/MacOS/kitten" "$macos_dir/kitten"' "$module_file" \
  || fail "kitty launcher bundles must symlink the cask kitten helper inside the app"
rg -F -q 'resources_dir="$contents_dir/Resources"' "$module_file" \
  || fail "kitty launcher bundles must use an explicit resources directory"
rg -F -q 'local icon_badge=$5' "$module_file" \
  || fail "kitty launcher helper must accept a per-launcher icon badge"
rg -F -q 'icon_file="$executable_name.icns"' "$module_file" \
  || fail "kitty launcher bundles must use per-launcher icon file names"
rg -F -q '"$iconutil_bin" -c iconset "$source_app/Contents/Resources/kitty.icns" -o "$iconset_dir"' "$module_file" \
  || fail "kitty launcher bundles must derive custom icons from the cask kitty icon"
rg -F -q '"$magick_bin" "$png"' "$module_file" \
  || fail "kitty launcher icon generation must use ImageMagick from the Nix store"
rg -F -q '${pkgs.nerd-fonts.jetbrains-mono}/share/fonts/truetype/NerdFonts/JetBrainsMono/JetBrainsMonoNerdFontMono-Bold.ttf' "$module_file" \
  || fail "kitty launcher icon badges must use JetBrainsMono Nerd Font Mono Bold"
rg -F -q 'x1=$((width * 63 / 128))' "$module_file" \
  || fail "kitty launcher icon generation must avoid covering the prompt glyph"
rg -F -q 'y1=$((height * 88 / 128))' "$module_file" \
  || fail "kitty launcher icon generation must erase only the original underscore band"
rg -F -q 'point=$((width * 35 / 128))' "$module_file" \
  || fail "kitty launcher icon badge letters must be tall enough to match the prompt glyph"
rg -F -q -- '-draw "text $tx,$ty '\''$icon_badge'\''"' "$module_file" \
  || fail "kitty launcher icon generation must draw the requested badge letter"
rg -F -q '"$iconutil_bin" -c icns "$iconset_dir" -o "$resources_dir/$icon_file"' "$module_file" \
  || fail "kitty launcher bundles must compile the badged iconset into the app resources"
rg -F -q '/bin/ln -s "$resource_item" "$resources_dir/$resource_name"' "$module_file" \
  || fail "kitty launcher bundles must symlink non-icon cask resources inside the resources directory"
if rg -F -q 'ln -sfn /Applications/kitty.app/Contents/Resources "$contents_dir/Resources"' "$module_file"; then
  fail "kitty launcher bundles must not symlink the whole Resources directory because Dock icons break"
fi
rg -F -q '/bin/ln -s "$source_app/Contents/Frameworks" "$contents_dir/Frameworks"' "$module_file" \
  || fail "kitty launcher bundles must symlink the cask frameworks inside the app"
rg -F -q 'launcher_config_dir="$kitty_dir/$executable_name"' "$module_file" \
  || fail "kitty launchers must use per-launcher config directories"
rg -F -q 'render_template "$launcher_config_template"' "$module_file" \
  || fail "kitty launcher configs must be rendered from the shared template"
rg -F -q '/bin/cat "$launcher_bash_config_fragment"; fi)' "$module_file" \
  || fail "kitty bash launcher config must append the bash-only fragment"
rg -F -q 'render_template "$launcher_session_template"' "$module_file" \
  || fail "kitty launcher sessions must be rendered from the shared template"
rg -F -q 'render_template "$launcher_plist_template"' "$module_file" \
  || fail "kitty launcher plists must be rendered from the shared template"
rg -F -q 'include /Users/@USER_NAME@/.config/kitty/kitty.conf' "$launcher_config_template" \
  || fail "per-launcher kitty configs must include the shared kitty config"
rg -F -q 'shell @SHELL_COMMAND@' "$launcher_config_template" \
  || fail "per-launcher kitty configs must select the requested shell"
rg -F -q 'startup_session launcher.session' "$launcher_config_template" \
  || fail "per-launcher kitty configs must select a startup session"
rg -F -q 'cd /Users/@USER_NAME@' "$launcher_session_template" \
  || fail "kitty startup sessions must start in the user home directory"
rg -F -q 'os_window_title @APP_NAME@' "$launcher_session_template" \
  || fail "kitty startup sessions must set the launcher window title"
rg -F -q '<key>CFBundleIconName</key>' "$launcher_plist_template" \
  || fail "kitty launcher plists must include an icon name for LaunchServices"
rg -F -q '<string>@ICON_FILE@</string>' "$launcher_plist_template" \
  || fail "kitty launcher plists must point at the per-launcher icon file"
rg -F -q '<key>KITTY_CONFIG_DIRECTORY</key>' "$launcher_plist_template" \
  || fail "kitty launcher plists must point kitty at the per-launcher config"
rg -F -q '<string>@LAUNCHER_CONFIG_DIR@</string>' "$launcher_plist_template" \
  || fail "kitty launcher plists must use the generated launcher config directory"
rg -F -q '"$codesign_bin" --force --sign - "$app_dir"' "$module_file" \
  || fail "kitty launcher apps must be ad-hoc signed for LaunchServices environment support"
rg -F -q '/usr/bin/touch "$app_dir"' "$module_file" \
  || fail "kitty launcher activation must refresh the top-level app timestamp for Dock icon caches"
rg -F -q '"$lsregister_bin" -f "$app_dir"' "$module_file" \
  || fail "kitty launcher apps must be registered by the target-user writer"
rg -F -q '/usr/bin/sudo -n -H -u "$target_user" --' "$module_source_file" \
  || fail "kitty module must cross the privilege boundary before invoking its writer"
rg -q '/bin/zsh -l' "$module_file" \
  || fail "kitty zsh launcher must start login zsh"
if rg -F -q 'create_kitty_launcher "Kitty Bash" "com.operator.kitty.bash" "kitty-bash" "/run/current-system/sw/bin/bash --login -i" "B"' "$module_file"; then
  fail "kitty bash launcher must not start bash directly because direct bash does not update macOS last-login accounting"
fi
rg -F -q 'create_kitty_launcher "Kitty Bash" "$bash_bundle_id" "kitty-bash" "/usr/bin/login -fp $target_user /run/current-system/sw/bin/bash" "B"' "$module_file" \
  || fail "kitty bash launcher must use macOS login so last-login is real"
rg -F -q 'if [ "$executable_name" = kitty-bash ]; then' "$module_file" \
  || fail "kitty bash launcher must have bash-specific kitty settings"
rg -F -q 'shell_integration no-cursor no-title' "$launcher_bash_config_fragment" \
  || fail "kitty bash must disable kitty title injection so the Bash theme owns the title"
rg -F -q 'confirm_os_window_close 0' "$launcher_bash_config_fragment" \
  || fail "kitty bash must disable idle close confirmation because macOS login wraps the shell process"

rg -q '^font_family JetBrainsMono Nerd Font$' "$config_file" \
  || fail "kitty must use the Terminal profile font family"
rg -q '^font_size 12\.0$' "$config_file" \
  || fail "kitty must use the Terminal profile font size"
rg -q '^foreground #33ff00$' "$config_file" \
  || fail "kitty must use the Terminal profile green foreground"
rg -q '^background #000000$' "$config_file" \
  || fail "kitty must use the Terminal profile black background"
rg -q '^cursor #33ff00$' "$config_file" \
  || fail "kitty must use the Terminal profile green cursor"
rg -q '^cursor_shape block$' "$config_file" \
  || fail "kitty must use a block cursor"
rg -q '^cursor_blink_interval 0\.5$' "$config_file" \
  || fail "kitty must keep cursor blinking enabled"
rg -F -q 'os_window_size 90c 30c' "$launcher_session_template" \
  || fail "kitty launcher sessions must open 90x30 windows"
rg -q '^initial_window_width 90c$' "$config_file" \
  || fail "kitty must default to 90 columns"
rg -q '^initial_window_height 30c$' "$config_file" \
  || fail "kitty must default to 30 rows"
rg -q '^window_padding_width 8$' "$config_file" \
  || fail "kitty must keep enough padding to avoid rounded-corner clipping"
rg -q '^shell_integration no-cursor$' "$config_file" \
  || fail "kitty shell integration must not force a beam cursor at prompts"
if rg -q '^shell ' "$config_file"; then
  fail "kitty.conf must not hardcode a single default shell"
fi

stock_config_file="$repo_dir/config/kitty/stock-bash.conf"
[ -f "$stock_config_file" ] || fail "missing config/kitty/stock-bash.conf"
rg -F -q 'include kitty.conf' "$stock_config_file" \
  || fail "stock-bash.conf must include neutral kitty.conf"
rg -F -q 'include /run/current-system/sw/share/kitty-stock-bash/kitty-stock.conf' "$stock_config_file" \
  || fail "stock-bash.conf must use supported include for absolute /run/current-system stock config"
if rg -F -q 'globinclude /run/current-system/sw/share/kitty-stock-bash/kitty-stock.conf' "$stock_config_file"; then
  fail "stock-bash.conf must not use globinclude for absolute /run/current-system stock config"
fi
rg -F -q 'globinclude stock-bash-colors.conf' "$stock_config_file" \
  || fail "stock-bash.conf must retain optional relative globinclude for stock-bash-colors.conf"

echo "kitty config tests passed"

}

@test "native Kitty parser selects the required absolute include and optional palette" {
  kitty_bin="/Applications/kitty.app/Contents/MacOS/kitty"
  [ -x "$kitty_bin" ] || skip "native Kitty unavailable; parent must run the documented +runpy check"
  repo_dir="$(cd "$(dirname "$BATS_TEST_FILENAME")/../../../.." && pwd)"
  scratch_config_dir="$BATS_TEST_TMPDIR/parser"
  mkdir -p "$scratch_config_dir/run-share"
  cp "$repo_dir/config/kitty/kitty.conf" "$scratch_config_dir/kitty.conf"
  printf '%s\n' 'shell /fixture/stock-launch' > "$scratch_config_dir/run-share/kitty-stock.conf"
  sed "s|/run/current-system/sw/share/kitty-stock-bash/kitty-stock.conf|$scratch_config_dir/run-share/kitty-stock.conf|" \
    "$repo_dir/config/kitty/stock-bash.conf" > "$scratch_config_dir/stock-bash.conf"
  KITTY_CONFIG_DIRECTORY="$scratch_config_dir" "$kitty_bin" +runpy "
from pathlib import Path
from kitty.config import load_config
p = Path('$scratch_config_dir')
bad = []
opts = load_config(str(p / 'stock-bash.conf'), accumulate_bad_lines=bad)
assert not bad, bad
assert opts.shell == '/fixture/stock-launch', opts.shell
(p / 'stock-bash-colors.conf').write_text('foreground #123456\\n')
with_palette = load_config(str(p / 'stock-bash.conf'), accumulate_bad_lines=bad)
expected = load_config('/dev/null', overrides=['foreground #123456'])
assert not bad, bad
assert with_palette.foreground == expected.foreground
assert with_palette.shell == opts.shell
"
}

@test "kitty stock activation manages v1 upgrade, preserves first preimage backup, and refuses user edits" {
  script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
  repo_dir="$(cd "$script_dir/../../../.." && pwd)"
  common="$repo_dir/modules/user-home-writer-common.sh"
  activator="$repo_dir/modules/kitty-stock-activate.sh"
  test_user="$(/usr/bin/id -un)"

  fixture_home="$BATS_TEST_TMPDIR/fixture-home"
  mkdir -m 0755 "$fixture_home"
  kitty_dest_dir="$fixture_home/.config/kitty"
  mkdir -p "$kitty_dest_dir"

  source_dir="$BATS_TEST_TMPDIR/sources"
  mkdir -p "$source_dir"
  v1_source="$source_dir/stock-bash-v1.conf"
  v2_source="$source_dir/stock-bash-v2.conf"
  ls_source="$source_dir/macos-launch-services-cmdline"

  # Reconstruct the deployed source and upgrade to this candidate's actual bytes.
  sed 's|^include /run/current-system/sw/share/kitty-stock-bash/kitty-stock.conf$|globinclude /run/current-system/sw/share/kitty-stock-bash/kitty-stock.conf|' \
    "$repo_dir/config/kitty/stock-bash.conf" > "$v1_source"
  cp "$repo_dir/config/kitty/stock-bash.conf" "$v2_source"
  run cmp -s "$v1_source" "$v2_source"
  [ "$status" -eq 1 ]
  printf '%s\n' "--config $kitty_dest_dir/stock-bash.conf" > "$ls_source"

  # Initial unmanaged user selection files
  printf '%s\n' "original user custom stock config" > "$kitty_dest_dir/stock-bash.conf"
  printf '%s\n' "original user launch cmdline" > "$kitty_dest_dir/macos-launch-services-cmdline"

  # Initial activation (v1): backs up user files to .before-nix-darwin
  run /bin/bash "$activator" enable "$common" "$test_user" "$fixture_home" "$v1_source" "$ls_source"
  [ "$status" -eq 0 ]
  cmp -s "$kitty_dest_dir/stock-bash.conf" "$v1_source"
  [ "$(<"$kitty_dest_dir/stock-bash.conf.before-nix-darwin")" = "original user custom stock config" ]
  [ "$(<"$kitty_dest_dir/macos-launch-services-cmdline.before-nix-darwin")" = "original user launch cmdline" ]

  receipt="$kitty_dest_dir/.dinas-kitty-state/stock-stock-bash.conf.receipt"
  [ -f "$receipt" ]
  rg -q "^state=active$" "$receipt"
  rg -q "^preimage=file$" "$receipt"
  rg -q "^backup=stock-bash.conf.before-nix-darwin$" "$receipt"
  rg -q "^previous=absent$" "$receipt"

  # Managed source upgrade from v1 to v2: upgrades content without loss of first preimage/backups
  run /bin/bash "$activator" enable "$common" "$test_user" "$fixture_home" "$v2_source" "$ls_source"
  [ "$status" -eq 0 ]
  cmp -s "$kitty_dest_dir/stock-bash.conf" "$v2_source"
  [ "$(<"$kitty_dest_dir/stock-bash.conf.before-nix-darwin")" = "original user custom stock config" ]
  backup_count="$(find "$kitty_dest_dir" -maxdepth 1 -name 'stock-bash.conf.before-nix-darwin*' | wc -l | tr -d ' ')"
  [ "$backup_count" -eq 1 ]
  rg -q "^state=active$" "$receipt"
  rg -q "^backup=stock-bash.conf.before-nix-darwin$" "$receipt"

  # Idempotent re-run does not churn or duplicate backups
  run /bin/bash "$activator" enable "$common" "$test_user" "$fixture_home" "$v2_source" "$ls_source"
  [ "$status" -eq 0 ]
  backup_count="$(find "$kitty_dest_dir" -maxdepth 1 -name 'stock-bash.conf.before-nix-darwin*' | wc -l | tr -d ' ')"
  [ "$backup_count" -eq 1 ]

  # Disable restores the first preimage; re-enable keeps that same backup.
  run /bin/bash "$activator" disable "$common" "$test_user" "$fixture_home"
  [ "$status" -eq 0 ]
  [ "$(<"$kitty_dest_dir/stock-bash.conf")" = "original user custom stock config" ]
  [ "$(<"$kitty_dest_dir/macos-launch-services-cmdline")" = "original user launch cmdline" ]
  run /bin/bash "$activator" enable "$common" "$test_user" "$fixture_home" "$v2_source" "$ls_source"
  [ "$status" -eq 0 ]
  cmp -s "$kitty_dest_dir/stock-bash.conf" "$v2_source"
  [ "$(<"$kitty_dest_dir/stock-bash.conf.before-nix-darwin")" = "original user custom stock config" ]

  # Refusal of user edits: edited backup causes enable to fail safely without making changes
  printf '%s\n' "tampered backup" > "$kitty_dest_dir/stock-bash.conf.before-nix-darwin"
  run /bin/bash "$activator" enable "$common" "$test_user" "$fixture_home" "$v2_source" "$ls_source"
  [ "$status" -ne 0 ]
  [[ "$output" =~ "preimage backup missing or edited; no changes made" ]]
  printf '%s\n' "original user custom stock config" > "$kitty_dest_dir/stock-bash.conf.before-nix-darwin"

  # Refusal of user edits: config edited during prepared/interrupted state is refused
  tmp_receipt="$BATS_TEST_TMPDIR/receipt.tmp"
  sed 's/^state=active$/state=prepared/' "$receipt" > "$tmp_receipt"
  mv "$tmp_receipt" "$receipt"
  printf '%s\n' "unauthorized local edit" > "$kitty_dest_dir/stock-bash.conf"
  run /bin/bash "$activator" enable "$common" "$test_user" "$fixture_home" "$v2_source" "$ls_source"
  [ "$status" -ne 0 ]
  [[ "$output" =~ "interrupted stock write conflicts with user edit; no changes made" ]]
  [ "$(<"$kitty_dest_dir/stock-bash.conf")" = "unauthorized local edit" ]

  # Refusal of directory collision at selection path
  dir_home="$BATS_TEST_TMPDIR/fixture-home-dir-conflict"
  mkdir -m 0755 "$dir_home"
  mkdir -p "$dir_home/.config/kitty/stock-bash.conf"
  run /bin/bash "$activator" enable "$common" "$test_user" "$dir_home" "$v1_source" "$ls_source"
  [ "$status" -ne 0 ]
  [[ "$output" =~ "stock selection path is a directory or special file; preserved" ]]

  # Disable with user edit preserves user edits and relinquishes ownership
  sed 's/^state=prepared$/state=active/' "$receipt" > "$tmp_receipt"
  mv "$tmp_receipt" "$receipt"
  printf '%s\n' "user custom change" > "$kitty_dest_dir/stock-bash.conf"
  run /bin/bash "$activator" disable "$common" "$test_user" "$fixture_home"
  [ "$status" -eq 0 ]
  [[ "$output" =~ "user-edited stock-bash.conf preserved; ownership relinquished" ]]
  [ "$(<"$kitty_dest_dir/stock-bash.conf")" = "user custom change" ]
  rg -q "^state=relinquished$" "$receipt"
}

@test "kitty stock upgrade preserves an adopted v1 source as its first preimage" {
  repo_dir="$(cd "$(dirname "$BATS_TEST_FILENAME")/../../../.." && pwd)"
  common="$repo_dir/modules/user-home-writer-common.sh"
  activator="$repo_dir/modules/kitty-stock-activate.sh"
  test_user="$(/usr/bin/id -un)"
  fixture_home="$BATS_TEST_TMPDIR/adopted-home"
  kitty_dest_dir="$fixture_home/.config/kitty"
  mkdir -p "$kitty_dest_dir"
  v1_source="$BATS_TEST_TMPDIR/adopted-v1.conf"
  v2_source="$repo_dir/config/kitty/stock-bash.conf"
  ls_source="$BATS_TEST_TMPDIR/adopted-cmdline"
  sed 's|^include /run/current-system/sw/share/kitty-stock-bash/kitty-stock.conf$|globinclude /run/current-system/sw/share/kitty-stock-bash/kitty-stock.conf|' \
    "$v2_source" > "$v1_source"
  cp "$v1_source" "$kitty_dest_dir/stock-bash.conf"
  printf '%s\n' "--config $kitty_dest_dir/stock-bash.conf" > "$ls_source"
  run /bin/bash "$activator" enable "$common" "$test_user" "$fixture_home" "$v1_source" "$ls_source"
  [ "$status" -eq 0 ]
  [ ! -e "$kitty_dest_dir/stock-bash.conf.before-nix-darwin" ]
  run /bin/bash "$activator" enable "$common" "$test_user" "$fixture_home" "$v2_source" "$ls_source"
  [ "$status" -eq 0 ]
  cmp -s "$kitty_dest_dir/stock-bash.conf" "$v2_source"
  cmp -s "$kitty_dest_dir/stock-bash.conf.before-nix-darwin" "$v1_source"
  run /bin/bash "$activator" disable "$common" "$test_user" "$fixture_home"
  [ "$status" -eq 0 ]
  cmp -s "$kitty_dest_dir/stock-bash.conf" "$v1_source"
  cmp -s "$kitty_dest_dir/stock-bash.conf.before-nix-darwin" "$v1_source"
}
