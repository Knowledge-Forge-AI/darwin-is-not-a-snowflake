#!/usr/bin/env bats
# nix-darwin-test-source-target: bin/brave-youtube-session
# nix-darwin-test-source-target: bin/clear-new-tab-sync
# nix-darwin-test-source-target: bin/codex-rclone-sync
# nix-darwin-test-source-target: bin/darwin-rebuild
# nix-darwin-test-source-target: bin/link-path
# nix-darwin-test-source-target: bin/toggle-link-lock
# nix-darwin-test-source-target: bin/monitor-temp
# nix-darwin-test-source-target: bin/nix-int-test
# nix-darwin-test-source-target: bin/nix-opt
# nix-darwin-test-source-target: bin/nix-rebuild
# nix-darwin-test-source-target: bin/nix-test
# nix-darwin-test-source-target: bin/nix-update
# nix-darwin-test-source-target: bin/nix-unit-test
# nix-darwin-test-source-target: bin/shared-images-rclone-sync
# nix-darwin-test-source-target: tools/test/kcov-bash-wrapper
# nix-darwin-test-source-target: tools/test/run

bats_require_minimum_version 1.5.0

setup() {
  repo_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../../.." && pwd)"
}
write_fake_command() {
  local dir="$1"
  local name="$2"
  local body="$3"

  {
    printf '%s\n' '#!/usr/bin/env bash'
    printf '%s\n' 'set -euo pipefail'
    printf '%s\n' "$body"
  } > "$dir/$name"
  chmod +x "$dir/$name"
}

@test "host bash entrypoints expose safe help paths" {
  for command_name in \
    brave-youtube-session \
    clear-new-tab-sync \
    codex-rclone-sync \
    darwin-rebuild \
    link-path \
    toggle-link-lock \
    monitor-temp \
    nix-int-test \
    nix-opt \
    nix-rebuild \
    nix-test \
    nix-update \
    nix-unit-test \
    shared-images-rclone-sync
  do
    run "$repo_dir/bin/$command_name" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage:"* || "$output" == *"usage:"* || "$output" == *"Run nix-darwin tests."* ]]
  done
}

@test "unsafe nix-update compatibility flags cannot dispatch mutation commands" {
  fake_bin="$BATS_TEST_TMPDIR/bin"
  log_file="$BATS_TEST_TMPDIR/nix-update.log"
  mkdir -p "$fake_bin"
  : > "$log_file"
  for command in nix sudo darwin-rebuild nix-env nix-collect-garbage nix-store; do
    write_fake_command "$fake_bin" "$command" 'printf "%s %s\n" "$0" "$*" >> "$MUTATION_LOG"'
  done

  run env PATH="$fake_bin:$PATH" MUTATION_LOG="$log_file" "$repo_dir/bin/nix-update" --switch-only
  [ "$status" -eq 78 ]
  [ ! -s "$log_file" ]
}

@test "nix-rebuild delegates one reviewed plan to transactional apply" {
  wrapper_dir="$BATS_TEST_TMPDIR/wrapper"
  log_file="$BATS_TEST_TMPDIR/nix-rebuild.log"
  mkdir -p "$wrapper_dir"
  : > "$log_file"
  cp "$repo_dir/bin/nix-rebuild" "$wrapper_dir/nix-rebuild"
  write_fake_command "$wrapper_dir" nix-update 'printf "%s\n" "$*" > "$DELEGATE_LOG"'

  run env DELEGATE_LOG="$log_file" "$wrapper_dir/nix-rebuild" "$BATS_TEST_TMPDIR/reviewed-plan"
  [ "$status" -eq 0 ]
  [ "$(cat "$log_file")" = "advanced apply $BATS_TEST_TMPDIR/reviewed-plan" ]
}

@test "rclone sync entrypoints dispatch dry-run commands through configured rclone" {
  fake_bin="$BATS_TEST_TMPDIR/bin"
  log_file="$BATS_TEST_TMPDIR/rclone.log"
  codex_dir="$BATS_TEST_TMPDIR/codex"
  shared_images_dir="$BATS_TEST_TMPDIR/shared-images"
  mkdir -p "$fake_bin" "$codex_dir" "$shared_images_dir"
  : > "$log_file"
  write_fake_command "$fake_bin" rclone 'printf "%s\n" "$*" >> "$RCLONE_TEST_LOG"'

  run env PATH="$fake_bin:$PATH" RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    CODEX_LOCAL_DIR="$codex_dir" CODEX_CRYPT_REMOTE="codex-test:" \
    "$repo_dir/bin/codex-rclone-sync" dry-run-push-merge
  [ "$status" -eq 0 ]

  run env PATH="$fake_bin:$PATH" RCLONE_TEST_LOG="$log_file" RCLONE=rclone \
    SHARED_IMAGES_LOCAL_DIR="$shared_images_dir" SHARED_IMAGES_CRYPT_REMOTE="shared-test:" \
    "$repo_dir/bin/shared-images-rclone-sync" dry-run-push
  [ "$status" -eq 0 ]

  grep -Fq 'copy --dry-run' "$log_file"
  grep -Fq 'sync --dry-run' "$log_file"
}
