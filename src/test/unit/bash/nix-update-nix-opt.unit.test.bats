#!/usr/bin/env bats
# nix-darwin-test-source-target: bin/nix-opt
# nix-darwin-test-source-target: bin/nix-update

bats_require_minimum_version 1.5.0

setup() {
  repo_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../../.." && pwd)"
}

@test "deployment help never promises automatic optimization or garbage collection" {
  run "$repo_dir/bin/nix-update" --help

  [ "$status" -eq 0 ]
  [[ "$output" == *"optimize"* ]]
  [[ "$output" != *"post-switch nix-opt"* ]]
  [[ "$output" != *"garbage collection"* ]]
}

@test "optimization remains a separate explicit operation" {
  run "$repo_dir/bin/nix-update" optimize --help

  [ "$status" -eq 0 ]
  [[ "$output" == *"separately authorized post-success"* ]]
  [[ "$output" == *"--dry-run"* ]]
  [[ "$output" == *"--keep KEEP"* ]]
}

@test "legacy preserve-predecessor no longer toggles retention" {
  run "$repo_dir/bin/nix-update" --build-only --preserve-predecessor

  [ "$status" -eq 78 ]
  [[ "$output" == *"now mandatory for apply"* ]]
}
