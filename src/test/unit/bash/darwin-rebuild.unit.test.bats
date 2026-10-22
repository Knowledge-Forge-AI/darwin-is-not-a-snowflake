#!/usr/bin/env bats
# nix-darwin-test-source-target: bin/darwin-rebuild

bats_require_minimum_version 1.5.0

setup() {
  repo_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../../.." && pwd)"
  guard="$repo_dir/bin/darwin-rebuild"
  upstream="$BATS_TEST_TMPDIR/darwin-rebuild-upstream"
  log_file="$BATS_TEST_TMPDIR/darwin-rebuild.log"
  : > "$log_file"

  {
    printf '%s\n' '#!/usr/bin/env bash'
    printf '%s\n' 'set -euo pipefail'
    printf '%s\n' 'printf "%s\n" "$*" >> "$DARWIN_REBUILD_TEST_LOG"'
  } > "$upstream"
  chmod +x "$upstream"
}

run_guard() {
  env \
    DARWIN_REBUILD_TEST_LOG="$log_file" \
    NIX_DARWIN_TEST_UPSTREAM_DARWIN_REBUILD="$upstream" \
    bash "$guard" "$@"
}

@test "darwin-rebuild guard exposes safe help without invoking upstream" {
  [ "$(sed -n '1p' "$guard")" = '#!/run/current-system/sw/bin/bash' ]

  run bash "$guard" --help

  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: darwin-rebuild"* ]]
  [[ "$output" == *"rollback"* ]]
  [ ! -s "$log_file" ]
}

@test "darwin-rebuild guard delegates non-mutating build dry-runs" {
  run run_guard build --dry-run --flake .#UnitHost

  [ "$status" -eq 0 ]
  [ "$(cat "$log_file")" = "build --dry-run --flake .#UnitHost" ]
}

@test "darwin-rebuild guard rejects rollback combined with dry-run" {
  for args in \
    "build --rollback --dry-run" \
    "--rollback build --dry-run" \
    "switch --rollback --dry-run" \
    "--switch-generation 2 --dry-run"
  do
    : > "$log_file"
    run run_guard $args

    [ "$status" -eq 2 ]
    [[ "$output" == *"dry-run does not make rollback non-mutating"* ]]
    [ ! -s "$log_file" ]
  done
}

@test "darwin-rebuild guard rejects rollback combined with non-switch actions" {
  for args in \
    "build --rollback" \
    "--rollback build" \
    "check --rollback" \
    "--list-generations --rollback"
  do
    : > "$log_file"
    run run_guard $args

    [ "$status" -eq 2 ]
    [[ "$output" == *"rollback cannot be combined with action"* ]]
    [ ! -s "$log_file" ]
  done
}

@test "darwin-rebuild guard rejects dry-run with activating actions" {
  for args in \
    "switch --dry-run --flake .#UnitHost" \
    "check --dry-run --flake .#UnitHost"
  do
    : > "$log_file"
    run run_guard $args

    [ "$status" -eq 2 ]
    [[ "$output" == *"dry-run cannot be combined with activating action"* ]]
    [ ! -s "$log_file" ]
  done
}

@test "darwin-rebuild guard delegates explicit rollback operations" {
  run run_guard switch --rollback --flake .#UnitHost

  [ "$status" -eq 0 ]
  [ "$(cat "$log_file")" = "switch --rollback --flake .#UnitHost" ]

  : > "$log_file"
  run run_guard --rollback

  [ "$status" -eq 0 ]
  [ "$(cat "$log_file")" = "--rollback" ]
}

@test "darwin-rebuild guard fails closed when upstream is unavailable" {
  rm -f "$upstream"

  run -127 run_guard build --dry-run --flake .#UnitHost

  [ "$status" -eq 127 ]
  [[ "$output" == *"managed upstream command is unavailable"* ]]
  [ ! -s "$log_file" ]
}
