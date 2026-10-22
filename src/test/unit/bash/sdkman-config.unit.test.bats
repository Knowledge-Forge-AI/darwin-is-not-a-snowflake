#!/usr/bin/env bats

@test "sdkman-config.unit.test" {
set -euo pipefail

script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
repo_dir="$(cd "$script_dir/../../../.." && pwd)"

fail() {
  echo "test-sdkman-config: $*" >&2
  exit 1
}

module_file="$repo_dir/modules/sdkman.nix"
writer_file="$repo_dir/modules/sdkman-config-writer.sh"
config_file="$repo_dir/config/sdkman/config"
bootstrap_script="$repo_dir/libexec/sdkman/bootstrap.sh"
ensure_java_script="$repo_dir/libexec/sdkman/ensure-java.sh"

[ -f "$module_file" ] || fail "missing modules/sdkman.nix"
[ -f "$writer_file" ] || fail "missing modules/sdkman-config-writer.sh"
[ -f "$config_file" ] || fail "missing config/sdkman/config"
[ -f "$bootstrap_script" ] || fail "missing libexec/sdkman/bootstrap.sh"
[ -f "$ensure_java_script" ] || fail "missing libexec/sdkman/ensure-java.sh"

bash -n "$bootstrap_script"
bash -n "$ensure_java_script"

rg -F -q 'sdkmanConfig = ../config/sdkman/config;' "$module_file" \
  || fail "SDKMAN module must source config/sdkman/config"
rg -F -q 'sdkmanBootstrap = ../libexec/sdkman/bootstrap.sh;' "$module_file" \
  || fail "SDKMAN module must source libexec/sdkman/bootstrap.sh"
rg -F -q 'sdkmanEnsureJava = ../libexec/sdkman/ensure-java.sh;' "$module_file" \
  || fail "SDKMAN module must source libexec/sdkman/ensure-java.sh"
rg -F -q 'sdkmanPath = pkgs.lib.makeBinPath [' "$module_file" \
  || fail "SDKMAN module must build an explicit managed PATH"
if rg -F -q 'pkgs.writeShellScript "sdkman-' "$module_file"; then
  fail "SDKMAN module must not embed SDKMAN Bash source with pkgs.writeShellScript"
fi

for package in bash coreutils curl gawk gnugrep gnused unzip zip; do
  rg -F -q "pkgs.$package" "$module_file" \
    || fail "SDKMAN managed PATH must include pkgs.$package"
done

for package in curl unzip zip; do
  rg -F -q "pkgs.$package" "$module_file" \
    || fail "SDKMAN system packages must include pkgs.$package"
done

rg -F -q 'https://get.sdkman.io' "$bootstrap_script" \
  || fail "SDKMAN bootstrap must use the official installer endpoint"
rg -F -q 'profile_home="$(mktemp -d' "$bootstrap_script" \
  || fail "SDKMAN bootstrap must use a temporary HOME"
rg -F -q 'trap cleanup_profile_home EXIT' "$bootstrap_script" \
  || fail "SDKMAN bootstrap must clean up its temporary HOME"
rg -F -q 'touch \' "$bootstrap_script" \
  || fail "SDKMAN bootstrap must create neutral shell startup files"
for startup_file in .bash_profile .bashrc .zshrc; do
  rg -F -q "\"\$profile_home/$startup_file\"" "$bootstrap_script" \
    || fail "SDKMAN bootstrap must neutralize $startup_file in temporary HOME"
done

rg -F -q 'source "$SDKMAN_DIR/bin/sdkman-init.sh"' "$ensure_java_script" \
  || fail "SDKMAN Java script must source sdkman-init.sh"
rg -F -q 'run_sdk list java' "$ensure_java_script" \
  || fail "SDKMAN Java script must inspect the Java catalog"
rg -F -q 'existing_default="$(basename "$(readlink "$current_java")")"' "$ensure_java_script" \
  || fail "SDKMAN Java script must remember existing symlinked default Java"
rg -F -q 'run_sdk default java "$existing_default"' "$ensure_java_script" \
  || fail "SDKMAN Java script must restore an existing Java default"
rg -F -q 'run_sdk default java "$java25"' "$ensure_java_script" \
  || fail "SDKMAN Java script must default to Java 25 only when no default exists"

expected_majors=(
  'ensure_corretto_major 25 25.0.3-amzn'
  'ensure_corretto_major 21 21.0.11-amzn'
  'ensure_corretto_major 17 17.0.19-amzn'
  'ensure_corretto_major 11 11.0.31-amzn'
  'ensure_corretto_major 8 8.0.472-amzn'
)

for expected_major in "${expected_majors[@]}"; do
  rg -F -q "$expected_major" "$ensure_java_script" \
    || fail "SDKMAN Java script must include $expected_major"
done

rg -F -q 'run_sdk() {' "$ensure_java_script" \
  || fail "SDKMAN Java script must wrap SDKMAN commands for strict Bash compatibility"
rg -F -q 'printf '\''n\n'\'' | USE=n sdkman_auto_answer=true run_sdk install java "$java_version"' "$ensure_java_script" \
  || fail "SDKMAN Java install must decline SDKMAN default switching during installs"
rg -F -q 'SDKMAN_DIR="$sdkman_dir"' "$module_file" \
  || fail "SDKMAN activation must pass SDKMAN_DIR explicitly"
rg -F -q 'PATH="$sdkman_env_path"' "$module_file" \
  || fail "SDKMAN activation must pass the managed PATH explicitly"
rg -F -q '/usr/bin/sudo -n -H -u "$target_user" -- /usr/bin/env' "$module_file" \
  || fail "SDKMAN activation must cross once to the configured target user"
rg -F -q '${./sdkman-config-writer.sh}' "$module_file" \
  || fail "SDKMAN activation must invoke the store-backed config writer"
rg -F -q 'uh_ensure_directory "$sdkman_dir/etc"' "$writer_file" \
  || fail "SDKMAN writer must create the config directory as the target user"
rg -F -q 'if ! /usr/bin/cmp -s "$sdkman_config_file" "$managed_config"; then' "$writer_file" \
  || fail "SDKMAN writer must compare non-link user config before preserving it"
rg -F -q '/bin/cp -p "$sdkman_config_file" "$backup"' "$writer_file" \
  || fail "SDKMAN writer must preserve file metadata when backing up config"
rg -F -q '/bin/ln -s "$managed_config" "$sdkman_config_file"' "$writer_file" \
  || fail "SDKMAN writer must symlink the managed config"
if rg -q '\b(chown|chgrp|sudo)\b' "$writer_file"; then
  fail "SDKMAN writer must not perform ownership or privilege operations"
fi

for config_line in \
  'sdkman_auto_answer=false' \
  'sdkman_auto_complete=true' \
  'sdkman_auto_env=false' \
  'sdkman_checksum_enable=true' \
  'sdkman_insecure_ssl=false' \
  'sdkman_selfupdate_feature=true'
do
  rg -F -q "$config_line" "$config_file" \
    || fail "SDKMAN config must contain $config_line"
done

echo "SDKMAN config tests passed"

}
