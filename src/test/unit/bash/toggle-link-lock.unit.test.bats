#!/usr/bin/env bats
# nix-darwin-test-source-target: bin/toggle-link-lock

bats_require_minimum_version 1.5.0

setup() {
  repo_dir="$(cd "$(dirname "$BATS_TEST_FILENAME")/../../../.." && pwd)"
  target="$BATS_TEST_TMPDIR/target path"
  link="$BATS_TEST_TMPDIR/link path"
  printf 'target\n' > "$target"
}

teardown() {
  if [[ -n "${link:-}" && -L "$link" ]]; then
    chflags -h nouchg "$link" 2>/dev/null || true
  fi
}

link_flags() {
  stat -f '%Sf' "$1"
}

@test "toggle-link-lock exposes help and rejects invalid arguments" {
  bash -n "$repo_dir/bin/toggle-link-lock"

  run "$repo_dir/bin/toggle-link-lock" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: toggle-link-lock [-c] LINK"* ]]

  run "$repo_dir/bin/toggle-link-lock" "$target"
  [ "$status" -eq 1 ]
  [[ "$output" == *"not a symbolic link"* ]]

  run "$repo_dir/bin/toggle-link-lock"
  [ "$status" -eq 2 ]
  [[ "$output" == *"Usage: toggle-link-lock [-c] LINK"* ]]
}

@test "toggle-link-lock dry-run describes an unlocked symlink without changing it" {
  ln -s "$target" "$link"

  run "$repo_dir/bin/toggle-link-lock" "$link"

  [ "$status" -eq 0 ]
  [[ "$output" == *'"status": "dry-run"'* ]]
  [[ "$output" == *'"from": "unlocked"'* ]]
  [[ "$output" == *'"to": "locked"'* ]]
  [ "$(link_flags "$link")" = "-" ]
}

@test "toggle-link-lock dry-run describes a locked symlink without changing it" {
  ln -s "$target" "$link"
  chflags -h uchg "$link"

  run "$repo_dir/bin/toggle-link-lock" "$link"

  [ "$status" -eq 0 ]
  [[ "$output" == *'"from": "locked"'* ]]
  [[ "$output" == *'"to": "unlocked"'* ]]
  [ "$(link_flags "$link")" = "uchg" ]
}

@test "toggle-link-lock -c toggles an unlocked symlink to locked and back" {
  ln -s "$target" "$link"

  run "$repo_dir/bin/toggle-link-lock" -c "$link"
  [ "$status" -eq 0 ]
  [[ "$output" == *'"status": "committed"'* ]]
  [[ "$output" == *'"from": "unlocked"'* ]]
  [[ "$output" == *'"to": "locked"'* ]]
  [ "$(link_flags "$link")" = "uchg" ]

  run "$repo_dir/bin/toggle-link-lock" -c "$link"
  [ "$status" -eq 0 ]
  [[ "$output" == *'"from": "locked"'* ]]
  [[ "$output" == *'"to": "unlocked"'* ]]
  [ "$(link_flags "$link")" = "-" ]
}

@test "toggle-link-lock escapes paths in its JSON response" {
  link="$BATS_TEST_TMPDIR/link \"quoted\""
  ln -s "$target" "$link"

  run "$repo_dir/bin/toggle-link-lock" "$link"

  [ "$status" -eq 0 ]
  python3 -c 'import json, sys; assert json.load(sys.stdin)["path"] == sys.argv[1]' "$link" <<<"$output"
}
