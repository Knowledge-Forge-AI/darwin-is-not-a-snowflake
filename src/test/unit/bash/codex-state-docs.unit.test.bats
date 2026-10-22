#!/usr/bin/env bats
# nix-darwin-test-source-target: modules/codex-state-writer.sh

setup() {
  repo_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../../.." && pwd)"
  writer="$repo_dir/modules/codex-state-writer.sh"
  test_user="$(/usr/bin/id -un)"
  test_home="$BATS_TEST_TMPDIR/home"
  mkdir -m 0755 "$test_home"
  runtime="$test_home/.local/nix-darwin/agent-central_dinas"
  mkdir -p "$runtime"
}

run_writer() {
  run /bin/sh "$writer" "$test_user" "$test_home" "$runtime"
  [ "$status" -eq 0 ]
}

assert_links() {
  [ -L "$test_home/codex" ]
  [ "$(readlink "$test_home/codex")" = "$runtime/codex/usr" ]
  [ ! -e "$test_home/codex-docs" ]
  [ -L "$test_home/.serena" ]
  [ "$(readlink "$test_home/.serena")" = "$runtime/codex/mcp/serena" ]
}

assert_file_content() {
  expected=$1
  file=$2
  [ "$(<"$file")" = "$expected" ]
}

@test "empty state creates intended roots and is repeat-stable" {
  runtime_mode="$(/usr/bin/stat -f '%Lp' "$runtime")"
  run_writer
  [ "$(/usr/bin/stat -f '%Lp' "$runtime")" = "$runtime_mode" ]
  assert_links

  for directory in \
    "$test_home/.codex" \
    "$runtime/codex" \
    "$runtime/codex/usr" \
    "$runtime/codex/mcp" \
    "$runtime/codex/mcp/serena"
  do
    [ -d "$directory" ]
    [ ! -L "$directory" ]
    [ "$(/usr/bin/stat -f '%u' "$directory")" = "$(/usr/bin/id -u)" ]
    [ "$(/usr/bin/stat -f '%Lp' "$directory")" = 755 ]
  done

  codex_inode="$(/usr/bin/stat -f '%i' "$test_home/codex")"
  serena_inode="$(/usr/bin/stat -f '%i' "$test_home/.serena")"
  run_writer
  assert_links
  [ "$(/usr/bin/stat -f '%i' "$test_home/codex")" = "$codex_inode" ]
  [ "$(/usr/bin/stat -f '%i' "$test_home/.serena")" = "$serena_inode" ]
}

@test "existing normal state preserves unrelated files and metadata" {
  mkdir -p "$test_home/.codex" "$runtime/codex"
  printf 'codex-unrelated\n' > "$test_home/.codex/history.json"
  printf 'source-unrelated\n' > "$runtime/codex/README.local"
  chmod 0640 "$test_home/.codex/history.json"
  before="$(/usr/bin/stat -f '%u:%g:%Lp:%z' "$test_home/.codex/history.json")"

  run_writer
  run_writer
  assert_links
  assert_file_content codex-unrelated "$test_home/.codex/history.json"
  assert_file_content source-unrelated "$runtime/codex/README.local"
  [ "$(/usr/bin/stat -f '%u:%g:%Lp:%z' "$test_home/.codex/history.json")" = "$before" ]
}

@test "each legacy docs source migrates ordinary hidden and unusual names" {
  sources=(
    "$test_home/.codex/$test_user/codex"
    "$test_home/.codex/user-docs"
    "$test_home/.codex/usr"
  )
  index=0
  for source in "${sources[@]}"; do
    index=$((index + 1))
    mkdir -p "$source/.hidden-dir-$index"
    printf 'ordinary-%s\n' "$index" > "$source/ordinary-$index"
    printf 'hidden-%s\n' "$index" > "$source/.hidden-$index"
    printf 'nested-%s\n' "$index" > "$source/.hidden-dir-$index/item"
    printf 'space-%s\n' "$index" > "$source/space name $index"
    printf 'dash-%s\n' "$index" > "$source/-leading-$index"
    printf 'glob-%s\n' "$index" > "$source/literal*?$index[brackets]"
  done

  run_writer
  target="$runtime/codex/usr"
  index=0
  for source in "${sources[@]}"; do
    index=$((index + 1))
    [ ! -e "$source" ]
    assert_file_content "ordinary-$index" "$target/ordinary-$index"
    assert_file_content "hidden-$index" "$target/.hidden-$index"
    assert_file_content "nested-$index" "$target/.hidden-dir-$index/item"
    assert_file_content "space-$index" "$target/space name $index"
    assert_file_content "dash-$index" "$target/-leading-$index"
    assert_file_content "glob-$index" "$target/literal*?$index[brackets]"
  done
}

@test "legacy docs collisions preserve destination and incoming content" {
  mkdir -p "$test_home/.codex/user-docs" "$runtime/codex/usr"
  printf 'destination\n' > "$runtime/codex/usr/collision"
  printf 'incoming\n' > "$test_home/.codex/user-docs/collision"

  run_writer
  target="$runtime/codex/usr"
  assert_file_content destination "$target/collision"
  backups=("$target"/collision.before-codex-docs-migration.*)
  [ "${#backups[@]}" -eq 1 ]
  assert_file_content incoming "${backups[0]}"

  run_writer
  backups=("$target"/collision.before-codex-docs-migration.*)
  [ "${#backups[@]}" -eq 1 ]
}

@test "legacy codex-docs symlink is removed without following its target" {
  sentinel="$BATS_TEST_TMPDIR/sentinel"
  mkdir "$sentinel"
  printf 'unchanged\n' > "$sentinel/item"
  ln -s "$sentinel" "$test_home/codex-docs"

  run_writer
  [ ! -e "$test_home/codex-docs" ]
  assert_file_content unchanged "$sentinel/item"
}

@test "legacy codex-docs directory migrates and regular conflict is backed up" {
  mkdir "$test_home/codex-docs"
  printf 'legacy-dir\n' > "$test_home/codex-docs/.legacy"
  run_writer
  [ ! -e "$test_home/codex-docs" ]
  assert_file_content legacy-dir "$runtime/codex/usr/.legacy"

  rm "$test_home/codex"
  printf 'legacy-file\n' > "$test_home/codex-docs"
  run_writer
  [ ! -e "$test_home/codex-docs" ]
  assert_file_content legacy-file "$test_home/codex-docs.before-nix-darwin"
}

@test "correct codex link is no-churn and wrong link never follows external target" {
  mkdir -p "$runtime/codex/usr"
  target="$runtime/codex/usr"
  ln -s "$target" "$test_home/codex"
  inode="$(/usr/bin/stat -f '%i' "$test_home/codex")"
  run_writer
  [ "$(/usr/bin/stat -f '%i' "$test_home/codex")" = "$inode" ]

  rm "$test_home/codex"
  sentinel="$BATS_TEST_TMPDIR/external-file"
  printf 'unchanged\n' > "$sentinel"
  ln -s "$sentinel" "$test_home/codex"
  run_writer
  [ "$(readlink "$test_home/codex")" = "$target" ]
  assert_file_content unchanged "$sentinel"
}

@test "codex directory and regular conflicts are preserved before linking" {
  mkdir "$test_home/codex"
  printf 'directory-conflict\n' > "$test_home/codex/item"
  run_writer
  [ -L "$test_home/codex" ]
  assert_file_content directory-conflict "$test_home/codex.before-nix-darwin/item"

  rm "$test_home/codex"
  printf 'regular-conflict\n' > "$test_home/codex"
  run_writer
  [ -L "$test_home/codex" ]
  backups=("$test_home"/codex.before-nix-darwin.*)
  [ "${#backups[@]}" -eq 1 ]
  assert_file_content regular-conflict "${backups[0]}"
}
