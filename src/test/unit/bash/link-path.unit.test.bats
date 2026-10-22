#!/usr/bin/env bats
# nix-darwin-test-source-target: bin/link-path

bats_require_minimum_version 1.5.0

setup() {
  repo_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../../.." && pwd)"
  target="$BATS_TEST_TMPDIR/target path"
  link="$BATS_TEST_TMPDIR/links/link path"
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

@test "link-path exposes help and requires exactly two paths" {
  bash -n "$repo_dir/bin/link-path"

  run "$repo_dir/bin/link-path" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: link-path [-l] TARGET LINK"* ]]

  run "$repo_dir/bin/link-path" "$target"
  [ "$status" -eq 2 ]
  [[ "$output" == *"Usage: link-path [-l] TARGET LINK"* ]]

  run "$repo_dir/bin/link-path" "$target" "$link" extra
  [ "$status" -eq 2 ]
  [[ "$output" == *"Usage: link-path [-l] TARGET LINK"* ]]
}

@test "link-path rejects a missing target before creating the link parent" {
  run "$repo_dir/bin/link-path" "$BATS_TEST_TMPDIR/missing" "$link"

  [ "$status" -eq 1 ]
  [[ "$output" == *"target does not exist"* ]]
  [ ! -e "$(dirname "$link")" ]
}

@test "link-path creates parent directories and a symlink with exact path arguments" {
  run "$repo_dir/bin/link-path" "$target" "$link"

  [ "$status" -eq 0 ]
  [ -L "$link" ]
  [ "$(readlink "$link")" = "$target" ]
}

@test "link-path leaves an already-correct symlink unchanged" {
  mkdir -p "$(dirname "$link")"
  ln -s "$target" "$link"
  before_inode="$(stat -f '%i' "$link")"

  run "$repo_dir/bin/link-path" "$target" "$link"

  [ "$status" -eq 0 ]
  [ "$(stat -f '%i' "$link")" = "$before_inode" ]
  [ "$(readlink "$link")" = "$target" ]
}

@test "link-path preserves a locked correct symlink during an idempotent run" {
  mkdir -p "$(dirname "$link")"
  ln -s "$target" "$link"
  chflags -h uchg "$link"

  run "$repo_dir/bin/link-path" "$target" "$link"

  [ "$status" -eq 0 ]
  [ "$(readlink "$link")" = "$target" ]
  [ "$(link_flags "$link")" = "uchg" ]
}

@test "link-path -l locks a new symlink with an isolated PATH" {
  run env PATH="/usr/bin:/bin" "$repo_dir/bin/link-path" -l "$target" "$link"

  [ "$status" -eq 0 ]
  [ -L "$link" ]
  [ "$(readlink "$link")" = "$target" ]
  [ "$(link_flags "$link")" = "uchg" ]
}

@test "link-path -l locks an existing correct symlink without replacing it" {
  mkdir -p "$(dirname "$link")"
  ln -s "$target" "$link"
  before_inode="$(stat -f '%i' "$link")"

  run "$repo_dir/bin/link-path" -l "$target" "$link"

  [ "$status" -eq 0 ]
  [ "$(stat -f '%i' "$link")" = "$before_inode" ]
  [ "$(link_flags "$link")" = "uchg" ]
}

@test "link-path -l replaces a locked incorrect symlink and locks the replacement" {
  mkdir -p "$(dirname "$link")"
  ln -s "$BATS_TEST_TMPDIR/old target" "$link"
  chflags -h uchg "$link"

  run "$repo_dir/bin/link-path" -l "$target" "$link"

  [ "$status" -eq 0 ]
  [ "$(readlink "$link")" = "$target" ]
  [ "$(link_flags "$link")" = "uchg" ]
}

@test "link-path replaces an incorrect symlink without a backup" {
  mkdir -p "$(dirname "$link")"
  ln -s "$BATS_TEST_TMPDIR/old target" "$link"

  run "$repo_dir/bin/link-path" "$target" "$link"

  [ "$status" -eq 0 ]
  [ "$(readlink "$link")" = "$target" ]
  shopt -s nullglob
  backups=("$link".before-link-path.*)
  [ "${#backups[@]}" -eq 0 ]
}

@test "link-path backs up a non-symlink occupant before linking" {
  mkdir -p "$(dirname "$link")"
  printf 'original\n' > "$link"

  run "$repo_dir/bin/link-path" "$target" "$link"

  [ "$status" -eq 0 ]
  [ "$(readlink "$link")" = "$target" ]
  shopt -s nullglob
  backups=("$link".before-link-path.*)
  [ "${#backups[@]}" -eq 1 ]
  [[ "${backups[0]}" =~ \.before-link-path\.[0-9]{14}$ ]]
  [ "$(cat "${backups[0]}")" = 'original' ]
}
