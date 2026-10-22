#!/usr/bin/env bats

@test "browser-config.unit.test" {
set -euo pipefail

repo_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../../.." && pwd)"

fail() {
  echo "test-browser-config: $*" >&2
  exit 1
}

chrome_policy="$repo_dir/config/browser/chrome-policy-legacy.plist"
firefox_policy="$repo_dir/config/browser/firefox-policies.json"
browsers_module="$repo_dir/modules/browsers.nix"
brave_youtube_module="$BATS_TEST_TMPDIR/brave-youtube-implementation"
cat \
  "$repo_dir/modules/brave-youtube.nix" \
  "$repo_dir/modules/brave-youtube-app-writer.sh" \
  >"$brave_youtube_module"

/usr/bin/plutil -lint "$chrome_policy" >/dev/null
jq empty "$firefox_policy" >/dev/null

if /usr/libexec/PlistBuddy -c 'Print :PasswordManagerEnabled' "$chrome_policy" >/dev/null 2>&1; then
  fail "Developer Chrome password management must retain its native default"
fi
if /usr/libexec/PlistBuddy -c 'Print :AutofillCreditCardEnabled' "$chrome_policy" >/dev/null 2>&1; then
  fail "Chrome card autofill must be left to Chrome and Google Wallet"
fi
if /usr/libexec/PlistBuddy -c 'Print :AutofillAddressEnabled' "$chrome_policy" >/dev/null 2>&1; then
  fail "Chrome address autofill must be left to Chrome"
fi

# R3 retains the deployed Chrome/CNT payload until storage migration is qualified.
# Validate that continuity payload independently of the retired R2B projection.
chrome_policy_json="$(/usr/bin/plutil -convert json -r -o - "$chrome_policy" | jq -cS .)"
expected_chrome_json="$(nix-instantiate --eval --strict --json "$repo_dir/config/browser/chrome-policy-legacy-defaults.nix" | jq -cS .)"
[ "$chrome_policy_json" = "$expected_chrome_json" ] \
  || fail "Retained Chrome managed plist must equal the R3 continuity defaults"

for compatibility_policy in DeveloperToolsAvailability RemoteDebuggingAllowed DownloadRestrictions; do
  if /usr/libexec/PlistBuddy -c "Print :$compatibility_policy" "$chrome_policy" >/dev/null 2>&1; then
    fail "Developer Chrome must retain the native default for $compatibility_policy"
  fi
done
[ "$(printf '%s' "$chrome_policy_json" | jq -r '.DefaultMediaStreamSetting')" = "3" ] \
  || fail "Retained Chrome must require permission for microphone/camera capture"
[ "$(printf '%s' "$chrome_policy_json" | jq -r '.BlockThirdPartyCookies')" = "true" ] \
  || fail "Retained Chrome must block third-party cookies"
printf '%s' "$chrome_policy_json" | jq -e '
  (.AudioCaptureAllowedUrls | length > 0) and
  (.VideoCaptureAllowedUrls == .AudioCaptureAllowedUrls) and
  (.ExtensionSettings.felphkbfjadmcejnibcmcncimlappdde.installation_mode == "force_installed")
' >/dev/null || fail "Capture exceptions and forced CNT continuity must be retained"

if rg -q 'pkgs\.firefox|extraPolicies|firefoxPolicies = builtins\.fromJSON' "$browsers_module"; then
  fail "Firefox must not use Nixpkgs firefox policy wrapping because it can force local Firefox source builds"
fi
rg -q '"firefox"' "$browsers_module" \
  || fail "Firefox must be installed as a Homebrew cask"
rg -q 'programs\.browserBaseline' "$browsers_module" \
  || fail "Browser policies must be declared through programs.browserBaseline seam"
rg -q 'config/browser/firefox-policies\.json' "$browsers_module" \
  || fail "Firefox policy file must be referenced from legacy config"
rg -q '/Applications/Firefox\.app/Contents/Resources/distribution' "$browsers_module" \
  || fail "Firefox activation must install policies into the app bundle distribution directory"
rg -q '\$firefoxPolicyDir/policies\.json' "$browsers_module" \
  || fail "Firefox activation must install policies.json without wrapping pkgs.firefox"

# Evaluate real seam-rendered artifacts from the flake configuration
seam_artifacts_json="$(nix eval --offline --no-update-lock-file --no-write-lock-file --json "path:${repo_dir}#darwinConfigurations.example-primary.config.system.build.browserBaseline")"

# 1. AST semantic equality between R3 continuity plist and evaluated seam chromeManagedPlist
rendered_chrome_plist="$(printf '%s' "$seam_artifacts_json" | jq -r '.chromeManagedPlist')"
/usr/bin/plutil -convert json -r -o - "$rendered_chrome_plist" | jq -S . > "$BATS_TEST_TMPDIR/rendered-chrome-defaults.json"
/usr/bin/plutil -convert json -r -o - "$chrome_policy" | jq -S . > "$BATS_TEST_TMPDIR/orig-chrome-policy.json"
cmp -s "$BATS_TEST_TMPDIR/orig-chrome-policy.json" "$BATS_TEST_TMPDIR/rendered-chrome-defaults.json" \
  || fail "Seam-rendered Chrome managed plist AST does not match the retained continuity AST"

# 2. Byte parity between legacy source files and evaluated seam render artifacts
rendered_firefox_policies="$(printf '%s' "$seam_artifacts_json" | jq -r '.firefoxPolicies')"
rendered_cnt_ext="$(printf '%s' "$seam_artifacts_json" | jq -r '.chromeExternalExtensions.felphkbfjadmcejnibcmcncimlappdde')"
rendered_chrome_nmh="$(printf '%s' "$seam_artifacts_json" | jq -r '.chromeNativeMessagingHosts["org.keepassxc.keepassxc_browser"]')"
rendered_firefox_nmh="$(printf '%s' "$seam_artifacts_json" | jq -r '.firefoxNativeMessagingHosts["org.keepassxc.keepassxc_browser"]')"

cmp -s "$repo_dir/config/browser/firefox-policies.json" "$rendered_firefox_policies" \
  || fail "Firefox policies byte parity check failed"
cmp -s "$repo_dir/config/browser/chrome-clear-new-tab-external-extension.json" "$rendered_cnt_ext" \
  || fail "Chrome Clear New Tab external extension byte parity failed"
cmp -s "$repo_dir/config/browser/keepassxc-chrome-native-messaging.json" "$rendered_chrome_nmh" \
  || fail "Chrome KeePassXC Native Messaging byte parity failed"
cmp -s "$repo_dir/config/browser/keepassxc-firefox-native-messaging.json" "$rendered_firefox_nmh" \
  || fail "Firefox KeePassXC Native Messaging byte parity failed"

# 3. Default host delivery mode and plist nullity assertions
rendered_delivery="$(printf '%s' "$seam_artifacts_json" | jq -r '.firefoxPolicyDelivery')"
[ "$rendered_delivery" = "bundle" ] || fail "Expected firefoxPolicyDelivery to be bundle"
rendered_firefox_plist="$(printf '%s' "$seam_artifacts_json" | jq -r '.firefoxManagedPlist')"
[ "$rendered_firefox_plist" = "null" ] || fail "Expected firefoxManagedPlist to be null on default host"

# 4. macOS preferences delivery mode: evaluate disposable module and verify plist generation and exact types
test_macos_plist="$(
  nix build --no-link --print-out-paths --impure --expr '
    let
      flake = builtins.getFlake "path:'"${repo_dir}"'";
      pkgs = flake.legacyPackages.aarch64-darwin;
      lib = pkgs.lib;
      browsersModule = import ('"${repo_dir}"' + "/modules/browsers.nix");
      eval = lib.evalModules {
        specialArgs = { inherit pkgs; };
        modules = [
          browsersModule
          {
            options.assertions = lib.mkOption { type = lib.types.listOf lib.types.anything; default = []; };
            options.environment.systemPackages = lib.mkOption { type = lib.types.listOf lib.types.package; default = []; };
            options.homebrew.casks = lib.mkOption { type = lib.types.listOf lib.types.anything; default = []; };
            options.system.build = lib.mkOption { type = lib.types.attrsOf lib.types.anything; default = {}; };
            options.system.activationScripts = lib.mkOption {
              type = lib.types.attrsOf (lib.types.submodule {
                options.text = lib.mkOption { type = lib.types.lines; default = ""; };
              });
              default = {};
            };
          }
          {
            programs.browserBaseline.legacyPolicyDefaults.enable = false;
            programs.browserBaseline.firefox.policyDelivery = "macos-preferences";
            programs.browserBaseline.firefox.policies = {
              DisableTelemetry = true;
              MaxVersion = 100;
              OverrideFirstRunPage = "https://example.com";
              RequestedLocales = [ "en-US" ];
              ExtensionSettings = {
                "*" = {
                  installation_mode = "blocked";
                };
              };
            };
          }
        ];
      };
    in eval.config.system.build.browserBaseline.firefoxManagedPlist
  '
)"
[ -n "$test_macos_plist" ] || fail "Failed to evaluate test macOS preferences plist"
[ -f "$test_macos_plist" ] || fail "Rendered test macOS plist file does not exist: $test_macos_plist"

/usr/bin/plutil -lint "$test_macos_plist" >/dev/null \
  || fail "Rendered org.mozilla.firefox.plist failed plutil -lint"

# Verify exact plutil types (distinguishing integer from float/number)
[ "$(/usr/bin/plutil -type EnterprisePoliciesEnabled "$test_macos_plist")" = "bool" ] \
  || fail "EnterprisePoliciesEnabled type must be bool"
[ "$(/usr/bin/plutil -type DisableTelemetry "$test_macos_plist")" = "bool" ] \
  || fail "DisableTelemetry type must be bool"
[ "$(/usr/bin/plutil -type MaxVersion "$test_macos_plist")" = "integer" ] \
  || fail "MaxVersion type must be integer"
[ "$(/usr/bin/plutil -type OverrideFirstRunPage "$test_macos_plist")" = "string" ] \
  || fail "OverrideFirstRunPage type must be string"
[ "$(/usr/bin/plutil -type RequestedLocales "$test_macos_plist")" = "array" ] \
  || fail "RequestedLocales type must be array"
[ "$(/usr/bin/plutil -type ExtensionSettings "$test_macos_plist")" = "dictionary" ] \
  || fail "ExtensionSettings type must be dictionary"
[ "$(/usr/bin/plutil -type 'ExtensionSettings.*.installation_mode' "$test_macos_plist")" = "string" ] \
  || fail "Nested ExtensionSettings installation_mode type must be string"

# Verify exact extracted values
[ "$(/usr/bin/plutil -extract EnterprisePoliciesEnabled raw "$test_macos_plist")" = "true" ] \
  || fail "EnterprisePoliciesEnabled value must be true"
[ "$(/usr/bin/plutil -extract DisableTelemetry raw "$test_macos_plist")" = "true" ] \
  || fail "DisableTelemetry value must be true"
[ "$(/usr/bin/plutil -extract MaxVersion raw "$test_macos_plist")" = "100" ] \
  || fail "MaxVersion value must be 100"
[ "$(/usr/bin/plutil -extract OverrideFirstRunPage raw "$test_macos_plist")" = "https://example.com" ] \
  || fail "OverrideFirstRunPage value must be https://example.com"
[ "$(/usr/bin/plutil -extract RequestedLocales.0 raw "$test_macos_plist")" = "en-US" ] \
  || fail "RequestedLocales.0 value must be en-US"
[ "$(/usr/bin/plutil -extract 'ExtensionSettings.*.installation_mode' raw "$test_macos_plist")" = "blocked" ] \
  || fail "Nested ExtensionSettings installation_mode value must be blocked"

# Verify full semantic key equality and reject unexpected extra keys
converted_json="$(/usr/bin/plutil -convert json -r -o - "$test_macos_plist")"
expected_keys='["DisableTelemetry","EnterprisePoliciesEnabled","ExtensionSettings","MaxVersion","OverrideFirstRunPage","RequestedLocales"]'
actual_keys="$(printf '%s' "$converted_json" | jq -c 'keys | sort')"
[ "$actual_keys" = "$expected_keys" ] \
  || fail "Top-level keys mismatch: got $actual_keys, expected $expected_keys"


firefox_password_policy_count="$(
  jq '
    (.policies // {})
    |
    [
      has("PasswordManagerEnabled"),
      has("OfferToSaveLogins")
    ]
    | map(select(. == true))
    | length
  ' "$firefox_policy"
)"
[ "$firefox_password_policy_count" = "0" ] \
  || fail "Firefox password manager must be left to KeePassXC-Browser privacy control"

rg -q 'source_short_version=' "$brave_youtube_module" \
  || fail "Brave YouTube must copy Brave version metadata from the source bundle"
rg -q 'BraveUpdater.app' "$brave_youtube_module" \
  || fail "Brave YouTube must remove Brave's inherited updater app"
rg -q 'Sparkle.framework' "$brave_youtube_module" \
  || fail "Brave YouTube must remove Brave's inherited Sparkle updater framework"
rg -q 'SUPublicEDKey' "$brave_youtube_module" \
  || fail "Brave YouTube must remove Sparkle public-key metadata"
rg -q 'SMPrivilegedExecutables' "$brave_youtube_module" \
  || fail "Brave YouTube must remove inherited privileged updater metadata"

echo "browser config tests passed"

}
