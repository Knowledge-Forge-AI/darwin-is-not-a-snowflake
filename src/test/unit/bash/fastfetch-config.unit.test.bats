#!/usr/bin/env bats

@test "fastfetch-config.unit.test" {
set -euo pipefail

script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
repo_dir="$(cd "$script_dir/../../../.." && pwd)"

fail() {
  echo "test-fastfetch-config: $*" >&2
  exit 1
}

flake_file="$repo_dir/flake.nix"
module_file="$repo_dir/modules/fastfetch.nix"
config_file="$repo_dir/config/fastfetch/config.jsonc"
terminal_config_file="$repo_dir/config/fastfetch/config-terminal.jsonc"
kitty_config_file="$repo_dir/config/fastfetch/config-kitty.jsonc"
logo_file="$repo_dir/config/fastfetch/logo.png"
wrapper_file="$repo_dir/config/fastfetch/fastfetch-wrapper.sh"

rg -q 'modules/fastfetch\.nix' "$flake_file" \
  || fail "flake.nix must import modules/fastfetch.nix"

[ -f "$module_file" ] || fail "missing modules/fastfetch.nix"
rg -q 'pkgs\.fastfetch' "$module_file" \
  || fail "fastfetch module must install pkgs.fastfetch"
if rg -q 'pkgs\.ascii-image-converter' "$module_file"; then
  fail "fastfetch module must not install ascii-image-converter when Apple Terminal uses default art"
fi
rg -q 'config/fastfetch/config\.jsonc' "$module_file" \
  || fail "fastfetch module must link a default managed config"
rg -q 'config/fastfetch/config-terminal\.jsonc' "$module_file" \
  || fail "fastfetch module must link the Apple Terminal config"
rg -q 'config/fastfetch/config-kitty\.jsonc' "$module_file" \
  || fail "fastfetch module must link the Kitty config"
rg -q 'config/fastfetch/logo\.png' "$module_file" \
  || fail "fastfetch module must link the managed logo"
if rg -q 'fastfetchAsciiLogo|ln -sfn .*logo\.ascii|config/fastfetch/logo\.ascii' "$module_file"; then
  fail "fastfetch module must not manage the discarded Apple Terminal ASCII logo"
fi
rg -q 'fastfetch-wrapper\.sh' "$module_file" \
  || fail "fastfetch module must install the profile-selecting wrapper"

[ -f "$config_file" ] || fail "missing config/fastfetch/config.jsonc"
jq empty "$config_file" >/dev/null \
  || fail "config/fastfetch/config.jsonc must be valid JSON"
[ -f "$terminal_config_file" ] || fail "missing config/fastfetch/config-terminal.jsonc"
jq empty "$terminal_config_file" >/dev/null \
  || fail "config/fastfetch/config-terminal.jsonc must be valid JSON"
[ -f "$kitty_config_file" ] || fail "missing config/fastfetch/config-kitty.jsonc"
jq empty "$kitty_config_file" >/dev/null \
  || fail "config/fastfetch/config-kitty.jsonc must be valid JSON"

for terminal_like_config in "$config_file" "$terminal_config_file"; do
  jq -e 'has("logo") | not' "$terminal_like_config" >/dev/null \
    || fail "$(basename "$terminal_like_config") must use fastfetch's default built-in logo"
  jq -e --slurpfile kitty "$kitty_config_file" '.display == $kitty[0].display' "$terminal_like_config" >/dev/null \
    || fail "$(basename "$terminal_like_config") must use the same display styling as Kitty"
  jq -e --slurpfile kitty "$kitty_config_file" '.modules == $kitty[0].modules' "$terminal_like_config" >/dev/null \
    || fail "$(basename "$terminal_like_config") must use the same compact info dashboard as Kitty"
done

[ "$(jq -r '.logo.source' "$kitty_config_file")" = "/Users/operator/.config/fastfetch/logo.png" ] \
  || fail "Kitty config must use the managed PNG logo"
[ "$(jq -r '.logo.type' "$kitty_config_file")" = "kitty-direct" ] \
  || fail "Kitty config must use Kitty image rendering"
[ "$(jq -r '.logo.width' "$kitty_config_file")" = "25" ] \
  || fail "Kitty logo width must leave room for module output"
[ "$(jq -r '.logo.height' "$kitty_config_file")" = "22" ] \
  || fail "Kitty logo height must be a little taller than the fastfetch dashboard"
[ "$(jq -r '.logo.position' "$kitty_config_file")" = "left" ] \
  || fail "Kitty logo must render on the left side"
[ "$(jq -r '.logo.preserveAspectRatio' "$kitty_config_file")" = "true" ] \
  || fail "Kitty logo must preserve aspect ratio"

[ "$(jq -r '.display.color.keys' "$kitty_config_file")" = "38;5;220" ] \
  || fail "Kitty fastfetch keys must use the steeef light amber color"
[ "$(jq -r '.display.color.separator' "$kitty_config_file")" = "38;5;220" ] \
  || fail "Kitty fastfetch separators must use the steeef light amber color"

jq -e 'any(.modules[]; type == "object" and .type == "packages" and .combined == true)' "$kitty_config_file" >/dev/null \
  || fail "config-kitty.jsonc must use compact combined package output"
jq -e 'any(.modules[]; type == "object" and .type == "disk" and .format == "{size-used} / {size-total} ({size-percentage})")' "$kitty_config_file" >/dev/null \
  || fail "config-kitty.jsonc must use compact disk output"
for profile_config in "$config_file" "$terminal_config_file" "$kitty_config_file"; do
  jq -e '
    def module_type: if type == "string" then . else .type end;
    ([.modules[] | module_type] | index("datetime"))
      < ([.modules[] | module_type] | index("os"))
  ' "$profile_config" >/dev/null \
    || fail "$(basename "$profile_config") must show DateTime above OS"
  jq -e '
    def module_type: if type == "string" then . else .type end;
    ([.modules[] | module_type] | index("cpuusage"))
      == (([.modules[] | module_type] | index("cpu")) + 1)
  ' "$profile_config" >/dev/null \
    || fail "$(basename "$profile_config") must show CPUUsage directly below CPU"
done

for profile_config in "$config_file" "$terminal_config_file" "$kitty_config_file"; do
  jq -e 'all(.modules[]; . != "colors" and (.type? // "") != "colors")' "$profile_config" >/dev/null \
    || fail "$(basename "$profile_config") must replace the stock colors module"
  jq -e 'any(.modules[]; type == "object" and .type == "custom" and (.format | contains("󰮯") and contains("󰊠")))' "$profile_config" >/dev/null \
    || fail "$(basename "$profile_config") must include the Nerd Font Pac-Man palette row"
  jq -e 'any(.modules[]; type == "object" and .type == "custom" and (.format | endswith("󰊠{#}  ")))' "$profile_config" >/dev/null \
    || fail "$(basename "$profile_config") must pad the Pac-Man row so the final ghost is not clipped"
  jq -e 'all(.modules[]; type != "object" or .type != "custom" or (.format | contains("ᗧ") or contains("ᗣ") | not))' "$profile_config" >/dev/null \
    || fail "$(basename "$profile_config") must not use the fallback Pac-Man glyphs"
done

[ -f "$logo_file" ] || fail "missing config/fastfetch/logo.png"
logo_properties="$(identify -format '%wx%h %[channels] %[opaque]' "$logo_file")"
[ "$logo_properties" = "640x896 srgba 4.0 False" ] \
  || fail "fastfetch geometric sample logo must be 640x896 with transparency, got $logo_properties"
left_alpha_mean="$(magick "$logo_file" -alpha extract -crop 50%x100%+0+0 -format '%[fx:mean]' info:)"
right_alpha_mean="$(magick "$logo_file" -alpha extract -gravity east -crop 50%x100%+0+0 -format '%[fx:mean]' info:)"
awk -v left="$left_alpha_mean" -v right="$right_alpha_mean" 'BEGIN { exit !(left > right) }' \
  || fail "fastfetch geometric sample logo must occupy the left pane"

if [ -e "$repo_dir/config/fastfetch/logo.ascii" ]; then
  fail "discarded Apple Terminal ASCII logo must not remain in the repo"
fi

[ -f "$wrapper_file" ] || fail "missing config/fastfetch/fastfetch-wrapper.sh"
rg -q 'KITTY_WINDOW_ID|xterm-kitty' "$wrapper_file" \
  || fail "fastfetch wrapper must detect Kitty"
rg -q 'FASTFETCH_PROFILE' "$wrapper_file" \
  || fail "fastfetch wrapper must allow explicit profile selection"

test_tmp_root="$(mktemp -d)"
cleanup() {
  rm -rf "$test_tmp_root"
}
trap cleanup EXIT

fake_fastfetch_root="$test_tmp_root/fake-fastfetch"
mkdir -p "$fake_fastfetch_root/bin"
cat > "$fake_fastfetch_root/bin/fastfetch" <<'FAKE'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$FAKE_FASTFETCH_ARGS"
FAKE
chmod +x "$fake_fastfetch_root/bin/fastfetch"

test_wrapper="$test_tmp_root/fastfetch"
sed "s|@fastfetch@|$fake_fastfetch_root|g" "$wrapper_file" > "$test_wrapper"
chmod +x "$test_wrapper"

run_wrapper_with_clean_env() {
  env \
    -u FASTFETCH_CONFIG_DIR \
    -u FASTFETCH_PROFILE \
    -u KITTY_PID \
    -u KITTY_WINDOW_ID \
    -u TERM_PROGRAM \
    "$@"
}

FAKE_FASTFETCH_ARGS="$test_tmp_root/apple.args" \
  run_wrapper_with_clean_env HOME=/Users/operator TERM=xterm-256color "$test_wrapper"
grep -Fxq -- "--config" "$test_tmp_root/apple.args" \
  || fail "wrapper must pass --config for Apple Terminal profile"
grep -Fxq -- "/Users/operator/.config/fastfetch/config-terminal.jsonc" "$test_tmp_root/apple.args" \
  || fail "wrapper must select Apple Terminal config outside Kitty"

FAKE_FASTFETCH_ARGS="$test_tmp_root/kitty.args" \
  run_wrapper_with_clean_env HOME=/Users/operator TERM=xterm-kitty KITTY_WINDOW_ID=1 "$test_wrapper"
grep -Fxq -- "/Users/operator/.config/fastfetch/config-kitty.jsonc" "$test_tmp_root/kitty.args" \
  || fail "wrapper must select Kitty config in Kitty"

FAKE_FASTFETCH_ARGS="$test_tmp_root/forced.args" \
  run_wrapper_with_clean_env HOME=/Users/operator TERM=xterm-kitty FASTFETCH_PROFILE=terminal "$test_wrapper"
grep -Fxq -- "/Users/operator/.config/fastfetch/config-terminal.jsonc" "$test_tmp_root/forced.args" \
  || fail "FASTFETCH_PROFILE=terminal must force Apple Terminal config"

FAKE_FASTFETCH_ARGS="$test_tmp_root/explicit.args" \
  run_wrapper_with_clean_env HOME=/Users/operator TERM=xterm-kitty KITTY_WINDOW_ID=1 "$test_wrapper" --config custom.jsonc
if grep -Fxq -- "/Users/operator/.config/fastfetch/config-kitty.jsonc" "$test_tmp_root/explicit.args"; then
  fail "explicit --config must bypass automatic profile selection"
fi

echo "fastfetch config tests passed"

}
