#!/usr/bin/env bats
# nix-darwin-test-source-target: bin/nix-rebuild
# nix-darwin-test-source-target: bin/nix-update

bats_require_minimum_version 1.5.0

setup() {
  repo_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../../.." && pwd)"
}

@test "nix-update launcher and nix-rebuild parse" {
  python3 -m py_compile "$repo_dir/bin/nix-update"
  bash -n "$repo_dir/bin/nix-rebuild"
}

@test "nix-update help exposes transactional commands and no bypass" {
  run "$repo_dir/bin/nix-update" --help

  [ "$status" -eq 0 ]
  [[ "$output" == *"{deploy,build,lock,promote,optimize,advanced,snapshot}"* ]]
  [[ "$output" == *"no production test-command or confirmation bypass"* ]]
  [[ "$output" != *"NIX_DARWIN_UPDATE_TEST_COMMAND"* ]]
  [[ "$output" != *"NIX_DARWIN_SKIP_UPDATE_TESTS"* ]]
}

@test "legacy prepare and apply reject with migration guidance" {
  run "$repo_dir/bin/nix-update" prepare

  [ "$status" -eq 78 ]
  [[ "$output" == *"legacy 'prepare' has moved to 'nix-update advanced prepare'"* ]]

  run "$repo_dir/bin/nix-update" apply

  [ "$status" -eq 78 ]
  [[ "$output" == *"legacy 'apply' has moved to 'nix-update advanced apply'"* ]]
}

@test "legacy switch-only is rejected with attended deployment guidance" {
  run "$repo_dir/bin/nix-update" --switch-only

  [ "$status" -eq 78 ]
  [[ "$output" == *"attended deployment"* ]]
  [[ "$output" == *"use 'nix-update'"* ]]
}

@test "legacy preserve-predecessor flag is rejected because retention is mandatory" {
  run "$repo_dir/bin/nix-update" --preserve-predecessor

  [ "$status" -eq 78 ]
  [[ "$output" == *"now mandatory for apply"* ]]
}

@test "legacy build-only maps to build and validates its existing options" {
  run "$repo_dir/bin/nix-update" --build-only --test-jobs 0

  [ "$status" -eq 2 ]
  [[ "$output" == *"argument --test-jobs: must be a positive integer"* ]]
}

@test "advanced prepare requires explicit contract and output" {
  run "$repo_dir/bin/nix-update" advanced prepare

  [ "$status" -eq 2 ]
  [[ "$output" == *"--contract"* ]]
  [[ "$output" == *"--output"* ]]
}

@test "lock requires explicit policy and isolated evidence output" {
  run "$repo_dir/bin/nix-update" lock

  [ "$status" -eq 2 ]
  [[ "$output" == *"--mode"* ]]
  [[ "$output" == *"--output"* ]]
}

@test "advanced apply requires one plan path" {
  run "$repo_dir/bin/nix-update" advanced apply

  [ "$status" -eq 2 ]
  [[ "$output" == *"plan"* ]]
}

@test "promotion has nested prepare and apply authority" {
  run "$repo_dir/bin/nix-update" promote prepare
  [ "$status" -eq 2 ]
  [[ "$output" == *"--flake"* ]]
  [[ "$output" == *"--input"* ]]
  [[ "$output" == *"--revision"* ]]
  [[ "$output" == *"--output"* ]]

  run "$repo_dir/bin/nix-update" promote apply
  [ "$status" -eq 2 ]
  [[ "$output" == *"plan"* ]]
}

@test "nix-rebuild requires a reviewed plan and documents compatibility boundary" {
  run "$repo_dir/bin/nix-rebuild" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: nix-rebuild PLAN"* ]]
  [[ "$output" == *"never updates flake inputs"* ]]

  run "$repo_dir/bin/nix-rebuild"
  [ "$status" -eq 2 ]
}

@test "symlinked launcher resolves its source checkout" {
  mkdir -p "$BATS_TEST_TMPDIR/local"
  ln -s "$repo_dir/bin" "$BATS_TEST_TMPDIR/local/nbin"

  run "$BATS_TEST_TMPDIR/local/nbin/nix-update" --help

  [ "$status" -eq 0 ]
  [[ "$output" == *"Qualify, build, inspect, and deploy"* ]]
}

@test "copied launcher honors only the core locator and does not select the target" {
  mkdir -p "$BATS_TEST_TMPDIR/copied"
  cp "$repo_dir/bin/nix-update" "$BATS_TEST_TMPDIR/copied/nix-update"

  run env NIX_DARWIN_FLAKE_DIR="$repo_dir" "$BATS_TEST_TMPDIR/copied/nix-update" --help

  [ "$status" -eq 0 ]
  [[ "$output" == *"Qualify, build, inspect, and deploy"* ]]
}
