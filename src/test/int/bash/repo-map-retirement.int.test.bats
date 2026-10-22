#!/usr/bin/env bats
# nix-darwin-test-source-target: flake.nix

bats_require_minimum_version 1.5.0

setup() {
  repo_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../../.." && pwd)"
  flake_ref="$repo_dir"
  if [ "${NIX_DARWIN_TEST_SOURCE:-git}" = worktree ]; then
    flake_ref="path:$repo_dir"
  fi
}

@test "core Darwin systems contain no repo-map-orchestrator executable" {
  for host in example-workstation example-primary
  do
    run nix build --no-link --print-out-paths --no-warn-dirty \
      "$flake_ref#darwinConfigurations.$host.system"
    [ "$status" -eq 0 ]
    [ -n "$output" ]
    [ ! -e "$output/sw/bin/repo-map-orchestrator" ]
  done
}

@test "repomap-kg remains buildable and exposes its console command" {
  run nix build --no-link --print-out-paths --no-warn-dirty \
    "$flake_ref#repomap-kg"
  [ "$status" -eq 0 ]
  [ -x "$output/bin/repomap-kg" ]
}
