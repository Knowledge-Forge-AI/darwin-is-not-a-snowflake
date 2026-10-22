#!/usr/bin/env bats
# nix-darwin-test-source-target: bin/nix-update
# nix-darwin-test-source-target: libexec/nix_darwin_local/config.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/launcher.py

bats_require_minimum_version 1.5.0

setup() {
  repo_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../../.." && pwd)"
}

@test "invalid explicit flake is rejected before qualification" {
  run "$repo_dir/bin/nix-update" build --flake "$BATS_TEST_TMPDIR/missing"

  [ "$status" -eq 78 ]
  [[ "$output" == *"selected flake is missing or invalid (--flake)"* ]]
}

@test "malformed control TOML fails closed before command execution" {
  mkdir -p "$BATS_TEST_TMPDIR/local/config"
  printf '%s\n' 'not toml = [' > "$BATS_TEST_TMPDIR/local/config/nix-darwin.toml"

  run env NIX_DARWIN_LOCAL="$BATS_TEST_TMPDIR/local" "$repo_dir/bin/nix-update" build

  [ "$status" -eq 78 ]
  [[ "$output" == *"unable to parse control config"* ]]
}

@test "activating deploy refuses implicit core selection" {
  mkdir -p "$BATS_TEST_TMPDIR/local/config"

  run env NIX_DARWIN_LOCAL="$BATS_TEST_TMPDIR/local" \
    "$repo_dir/bin/nix-update" deploy

  [ "$status" -eq 78 ]
  [[ "$output" == *"no activating top-level flake selected"* ]]
}

@test "activating advanced prepare refuses implicit core selection" {
  mkdir -p "$BATS_TEST_TMPDIR/local/config"

  run env NIX_DARWIN_LOCAL="$BATS_TEST_TMPDIR/local" \
    "$repo_dir/bin/nix-update" advanced prepare \
    --contract "$BATS_TEST_TMPDIR/contract.json" --output "$BATS_TEST_TMPDIR/plan"

  [ "$status" -eq 78 ]
  [[ "$output" == *"no activating top-level flake selected"* ]]
}
