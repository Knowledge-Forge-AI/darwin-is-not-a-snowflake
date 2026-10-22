#!/usr/bin/env bats

@test "terminal-config.unit.test" {
set -euo pipefail

script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
repo_dir="$(cd "$script_dir/../../../.." && pwd)"

fail() {
  echo "test-terminal-config: $*" >&2
  exit 1
}

flake_file="$repo_dir/flake.nix"
module_file="$repo_dir/modules/terminal.nix"

rg -q 'modules/terminal\.nix' "$flake_file" \
  || fail "flake.nix must import modules/terminal.nix"
[ -f "$module_file" ] || fail "missing modules/terminal.nix"

rg -F -q 'terminalProfileName = "terminal_settings";' "$module_file" \
  || fail "Terminal module must target the terminal_settings profile"
rg -F -q 'terminalColumns = 90;' "$module_file" \
  || fail "Terminal profile must use 90 columns"
rg -F -q 'terminalRows = 30;' "$module_file" \
  || fail "Terminal profile must use 30 rows"
rg -F -q 'terminalFontWidthSpacing = "1.0";' "$module_file" \
  || fail "Terminal profile must normalize character spacing to Kitty"
rg -F -q 'terminalFontHeightSpacing = "1.0";' "$module_file" \
  || fail "Terminal profile must normalize line spacing to Kitty"
rg -F -q 'terminalFontName = "JetBrainsMonoNFM-Regular";' "$module_file" \
  || fail "Terminal profile must use the mono JetBrains Nerd Font face"
rg -F -q 'terminalFontArchive =' "$module_file" \
  || fail "Terminal module must carry the archived NSFont data"
rg -F -q '/usr/bin/defaults export com.apple.Terminal "$tmp_plist"' "$module_file" \
  || fail "Terminal module must edit the GUI user's Terminal preferences"
rg -F -q '/usr/bin/defaults import com.apple.Terminal "$tmp_plist"' "$module_file" \
  || fail "Terminal module must import the updated Terminal preferences"
rg -F -q 'run_as_user /usr/bin/mktemp -t terminal-settings' "$module_file" \
  || fail "Terminal module must create the preferences temp file as the GUI user"
rg -F -q "/usr/bin/plutil -replace 'Window Settings.\${terminalProfileName}.columnCount' -integer \${toString terminalColumns}" "$module_file" \
  || fail "Terminal module must update profile columns"
rg -F -q "/usr/bin/plutil -replace 'Window Settings.\${terminalProfileName}.rowCount' -integer \${toString terminalRows}" "$module_file" \
  || fail "Terminal module must update profile rows"
rg -F -q "/usr/bin/plutil -replace 'Window Settings.\${terminalProfileName}.FontWidthSpacing' -float \${terminalFontWidthSpacing}" "$module_file" \
  || fail "Terminal module must update character spacing"
rg -F -q "/usr/bin/plutil -replace 'Window Settings.\${terminalProfileName}.FontHeightSpacing' -float \${terminalFontHeightSpacing}" "$module_file" \
  || fail "Terminal module must update line spacing"
rg -F -q "/usr/bin/plutil -replace 'Window Settings.\${terminalProfileName}.Font' -data \${terminalFontArchive}" "$module_file" \
  || fail "Terminal module must update the archived font"
rg -F -q '/usr/bin/defaults write com.apple.Terminal "Default Window Settings" -string "${terminalProfileName}"' "$module_file" \
  || fail "Terminal module must keep terminal_settings as the default profile"
rg -F -q '/usr/bin/defaults write com.apple.Terminal "Startup Window Settings" -string "${terminalProfileName}"' "$module_file" \
  || fail "Terminal module must keep terminal_settings as the startup profile"

echo "terminal config tests passed"

}
