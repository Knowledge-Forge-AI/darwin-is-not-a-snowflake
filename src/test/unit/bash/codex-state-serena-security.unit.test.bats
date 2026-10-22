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

teardown() {
  chmod -R u+w "$BATS_TEST_TMPDIR" 2>/dev/null || true
}

run_writer_ok() {
  run /bin/sh "$writer" "$test_user" "$test_home" "$runtime"
  [ "$status" -eq 0 ]
}

run_writer_fail() {
  run /bin/sh "$writer" "$test_user" "$test_home" "$runtime"
  [ "$status" -ne 0 ]
}

assert_file_content() {
  expected=$1
  file=$2
  [ "$(<"$file")" = "$expected" ]
}

@test "absent canonical checkout fails before creating any state" {
  rmdir "$runtime"
  run_writer_fail
  [[ "$output" == *"canonical Agent-Central checkout is missing"* ]]
  [ ! -e "$runtime" ]
  [ ! -e "$test_home/.codex" ]
  [ ! -e "$test_home/ai" ]
}

@test "symlinked canonical checkout fails without following it" {
  rmdir "$runtime"
  sentinel="$BATS_TEST_TMPDIR/runtime-sentinel"
  mkdir "$sentinel"
  ln -s "$sentinel" "$runtime"
  run_writer_fail
  [[ "$output" == *"agent-central root is not an ordinary directory"* ]]
  [ "$(readlink "$runtime")" = "$sentinel" ]
  [ ! -e "$sentinel/codex" ]
  [ ! -e "$test_home/.codex" ]
}

@test "unusable canonical checkout fails before state mutation" {
  chmod 0555 "$runtime"
  run_writer_fail
  chmod 0755 "$runtime"
  [[ "$output" == *"agent-central root is not writable and searchable"* ]]
  [ ! -e "$runtime/codex" ]
  [ ! -e "$test_home/.codex" ]
}

@test "writer requires exactly three fixed arguments and an absolute canonical root" {
  for arguments in missing extra; do
    if [ "$arguments" = missing ]; then
      run /bin/sh "$writer" "$test_user" "$test_home"
    else
      run /bin/sh "$writer" "$test_user" "$test_home" "$runtime" extra
    fi
    [ "$status" -ne 0 ]
    [[ "$output" == *"expected three fixed arguments"* ]]
  done
  for invalid_root in relative / /tmp/../runtime /tmp/./runtime /tmp//runtime; do
    run /bin/sh "$writer" "$test_user" "$test_home" "$invalid_root"
    [ "$status" -ne 0 ]
    [[ "$output" == *"agent-central root is not"* ]]
  done
  [ ! -e "$test_home/.codex" ]
}

@test "Serena directory migration preserves hidden content and collisions" {
  destination="$runtime/codex/mcp/serena"
  mkdir -p "$destination" "$test_home/.serena/.hidden-dir"
  printf 'destination\n' > "$destination/collision"
  printf 'incoming\n' > "$test_home/.serena/collision"
  printf 'hidden\n' > "$test_home/.serena/.hidden"
  printf 'nested\n' > "$test_home/.serena/.hidden-dir/item"
  printf 'space\n' > "$test_home/.serena/space name"
  printf 'glob\n' > "$test_home/.serena/literal*?[]"

  run_writer_ok
  [ -L "$test_home/.serena" ]
  [ "$(readlink "$test_home/.serena")" = "$destination" ]
  assert_file_content destination "$destination/collision"
  assert_file_content hidden "$destination/.hidden"
  assert_file_content nested "$destination/.hidden-dir/item"
  assert_file_content space "$destination/space name"
  assert_file_content glob "$destination/literal*?[]"
  backups=("$destination"/collision.before-serena-state-migration.*)
  [ "${#backups[@]}" -eq 1 ]
  assert_file_content incoming "${backups[0]}"
}

@test "correct Serena link is stable" {
  destination="$runtime/codex/mcp/serena"
  mkdir -p "$destination"
  ln -s "$destination" "$test_home/.serena"
  inode="$(/usr/bin/stat -f '%i' "$test_home/.serena")"

  run_writer_ok
  run_writer_ok
  [ "$(/usr/bin/stat -f '%i' "$test_home/.serena")" = "$inode" ]
}

@test "wrong Serena symlink is backed up without following external target" {
  sentinel="$BATS_TEST_TMPDIR/external"
  mkdir "$sentinel"
  printf 'unchanged\n' > "$sentinel/item"
  ln -s "$sentinel" "$test_home/.serena"

  run_writer_ok
  [ -L "$test_home/.serena.before-nix-darwin" ]
  [ "$(readlink "$test_home/.serena.before-nix-darwin")" = "$sentinel" ]
  assert_file_content unchanged "$sentinel/item"
  [ "$(readlink "$test_home/.serena")" = "$runtime/codex/mcp/serena" ]
}

@test "non-directory Serena conflict is backed up before linking" {
  printf 'regular-conflict\n' > "$test_home/.serena"
  run_writer_ok
  [ -L "$test_home/.serena" ]
  assert_file_content regular-conflict "$test_home/.serena.before-nix-darwin"
}

@test "symlinked and non-directory primary roots fail without touching targets" {
  sentinel="$BATS_TEST_TMPDIR/sentinel"
  mkdir "$sentinel"
  printf 'unchanged\n' > "$sentinel/item"
  ln -s "$sentinel" "$test_home/.codex"

  run_writer_fail
  assert_file_content unchanged "$sentinel/item"
  [ ! -e "$sentinel/usr" ]

  rm "$test_home/.codex"
  printf 'root-conflict\n' > "$test_home/.codex"
  run_writer_fail
  assert_file_content root-conflict "$test_home/.codex"
}

@test "symlinked Codex source root fails without traversing sentinel" {
  mkdir -p "$runtime"
  sentinel="$BATS_TEST_TMPDIR/sentinel"
  mkdir "$sentinel"
  printf 'unchanged\n' > "$sentinel/item"
  ln -s "$sentinel" "$runtime/codex"

  run_writer_fail
  assert_file_content unchanged "$sentinel/item"
  [ ! -e "$sentinel/usr" ]
}

@test "symlinked legacy docs root is rejected without following sentinel" {
  mkdir "$test_home/.codex"
  sentinel="$BATS_TEST_TMPDIR/sentinel"
  mkdir "$sentinel"
  printf 'unchanged\n' > "$sentinel/item"
  ln -s "$sentinel" "$test_home/.codex/usr"

  run_writer_fail
  assert_file_content unchanged "$sentinel/item"
  [ ! -e "$test_home/codex" ]
}

@test "nonwritable root fails with original state and compatibility paths intact" {
  mkdir "$test_home/.codex"
  printf 'unrelated\n' > "$test_home/.codex/unrelated"
  chmod 0555 "$test_home/.codex"

  run_writer_fail
  chmod 0755 "$test_home/.codex"
  assert_file_content unrelated "$test_home/.codex/unrelated"
  [ ! -e "$test_home/codex" ]
  [ ! -e "$test_home/.serena" ]
}

@test "identity mismatch refuses before creating user state" {
  run /bin/sh "$writer" definitely-not-the-effective-user "$test_home" "$runtime"
  [ "$status" -ne 0 ]
  [[ "$output" == *"effective user does not match target user"* ]]
  [ ! -e "$test_home/.codex" ]
  [ ! -e "$test_home/ai" ]
}

@test "stubbed root identity executes the production refusal branch before mutation" {
  id_stub="$BATS_TEST_TMPDIR/id-stub"
  root_writer="$BATS_TEST_TMPDIR/root-writer.sh"
  printf '%s\n' '#!/bin/sh' 'case "$1" in -u) printf "0\\n" ;; -un) printf "root\\n" ;; *) exit 2 ;; esac' > "$id_stub"
  chmod 0755 "$id_stub"
  sed "s|/usr/bin/id|$id_stub|g" "$writer" > "$root_writer"

  run /bin/sh "$root_writer" "$test_user" "$test_home" "$runtime"
  [ "$status" -ne 0 ]
  [[ "$output" == *"refusing to mutate user-home state as root"* ]]
  [ ! -e "$test_home/.codex" ]
  [ ! -e "$test_home/ai" ]
}

@test "stubbed foreign owner metadata is rejected without root workaround" {
  mkdir "$test_home/.codex"
  shim_dir="$BATS_TEST_TMPDIR/shims"
  mkdir "$shim_dir"
  stat_stub="$shim_dir/stat"
  test_writer="$BATS_TEST_TMPDIR/foreign-owner-writer.sh"
  printf '%s\n' \
    '#!/bin/sh' \
    'if [ "${3:-}" = "$CODEX_STATE_TEST_HOME/.codex" ]; then printf "2147483647\\n"; else exec /usr/bin/stat "$@"; fi' \
    > "$stat_stub"
  chmod 0755 "$stat_stub"
  sed 's|PATH=/usr/bin:/bin|PATH="$CODEX_STATE_TEST_COMMAND_PATH"|' "$writer" > "$test_writer"

  export CODEX_STATE_TEST_HOME="$test_home"
  export CODEX_STATE_TEST_COMMAND_PATH="$shim_dir:/usr/bin:/bin"
  run /bin/sh "$test_writer" "$test_user" "$test_home" "$runtime"
  [ "$status" -ne 0 ]
  [[ "$output" == *"not owned by the target user"* ]]
  [ ! -e "$test_home/ai" ]
}

@test "stubbed foreign canonical owner is rejected before any state mutation" {
  shim_dir="$BATS_TEST_TMPDIR/shims"
  mkdir "$shim_dir"
  stat_stub="$shim_dir/stat"
  test_writer="$BATS_TEST_TMPDIR/foreign-owner-writer.sh"
  printf '%s\n' \
    '#!/bin/sh' \
    'if [ "${3:-}" = "$CODEX_STATE_TEST_RUNTIME" ]; then printf "2147483647\\n"; else exec /usr/bin/stat "$@"; fi' \
    > "$stat_stub"
  chmod 0755 "$stat_stub"
  sed 's|PATH=/usr/bin:/bin|PATH="$CODEX_STATE_TEST_COMMAND_PATH"|' "$writer" > "$test_writer"

  export CODEX_STATE_TEST_RUNTIME="$runtime"
  export CODEX_STATE_TEST_COMMAND_PATH="$shim_dir:/usr/bin:/bin"
  run /bin/sh "$test_writer" "$test_user" "$test_home" "$runtime"
  [ "$status" -ne 0 ]
  [[ "$output" == *"not owned by the target user"* ]]
  [ ! -e "$test_home/ai" ]
  [ ! -e "$test_home/.codex" ]
  [ ! -e "$runtime/codex" ]
}

@test "instrumented path substitution never becomes privileged and preserves sentinel" {
  shim_dir="$BATS_TEST_TMPDIR/shims"
  mkdir "$shim_dir"
  mkdir_stub="$shim_dir/mkdir"
  race_writer="$BATS_TEST_TMPDIR/race-writer.sh"
  operation_log="$BATS_TEST_TMPDIR/operations.log"
  sentinel="$BATS_TEST_TMPDIR/protected-sentinel"
  mkdir "$sentinel"
  printf 'unchanged\n' > "$sentinel/item"
  chmod 0555 "$sentinel"

  printf '%s\n' \
    '#!/bin/sh' \
    'printf "mkdir uid=%s target=%s\\n" "$(/usr/bin/id -u)" "${3:-${1:-}}" >> "$CODEX_STATE_TEST_OPERATION_LOG"' \
    'target=${3:-${1:-}}' \
    'if [ "$target" = "$CODEX_STATE_TEST_HOME/.local/nix-darwin/agent-central_dinas/codex/usr" ]; then' \
    '  /bin/rmdir "$CODEX_STATE_TEST_HOME/.local/nix-darwin/agent-central_dinas/codex"' \
    '  /bin/ln -s "$CODEX_STATE_TEST_SENTINEL" "$CODEX_STATE_TEST_HOME/.local/nix-darwin/agent-central_dinas/codex"' \
    'fi' \
    'exec /bin/mkdir "$@"' \
    > "$mkdir_stub"
  chmod 0755 "$mkdir_stub"
  sed 's|PATH=/usr/bin:/bin|PATH="$CODEX_STATE_TEST_COMMAND_PATH"|' "$writer" > "$race_writer"

  export CODEX_STATE_TEST_HOME="$test_home"
  export CODEX_STATE_TEST_SENTINEL="$sentinel"
  export CODEX_STATE_TEST_OPERATION_LOG="$operation_log"
  export CODEX_STATE_TEST_COMMAND_PATH="$shim_dir:/usr/bin:/bin"
  run /bin/sh "$race_writer" "$test_user" "$test_home" "$runtime"
  chmod 0755 "$sentinel"

  [ "$status" -ne 0 ]
  assert_file_content unchanged "$sentinel/item"
  [ ! -e "$sentinel/usr" ]
  [ -s "$operation_log" ]
  process_uid="$(/usr/bin/id -u)"
  ! rg -v -F "mkdir uid=$process_uid target=" "$operation_log"
}

@test "forced migration failure preserves original conflict and withholds links" {
  mkdir -p "$test_home/.codex/user-docs" "$runtime/codex/usr"
  source_item="$test_home/.codex/user-docs/collision"
  destination_item="$runtime/codex/usr/collision"
  unrelated_item="$runtime/codex/unrelated"
  printf 'original-conflict\n' > "$source_item"
  printf 'destination\n' > "$destination_item"
  printf 'unrelated\n' > "$unrelated_item"

  shim_dir="$BATS_TEST_TMPDIR/shims"
  mkdir "$shim_dir"
  mv_stub="$shim_dir/mv"
  failure_writer="$BATS_TEST_TMPDIR/failure-writer.sh"
  printf '%s\n' \
    '#!/bin/sh' \
    'if [ "$1" = "$CODEX_STATE_TEST_FAIL_SOURCE" ]; then exit 73; fi' \
    'exec /bin/mv "$@"' \
    > "$mv_stub"
  chmod 0755 "$mv_stub"
  sed 's|PATH=/usr/bin:/bin|PATH="$CODEX_STATE_TEST_COMMAND_PATH"|' "$writer" > "$failure_writer"

  export CODEX_STATE_TEST_COMMAND_PATH="$shim_dir:/usr/bin:/bin"
  export CODEX_STATE_TEST_FAIL_SOURCE="$source_item"
  run /bin/sh "$failure_writer" "$test_user" "$test_home" "$runtime"

  [ "$status" -ne 0 ]
  assert_file_content original-conflict "$source_item"
  assert_file_content destination "$destination_item"
  assert_file_content unrelated "$unrelated_item"
  [ ! -e "$test_home/codex" ]
  [ ! -e "$test_home/.serena" ]
}

@test "precondition failure preserves originals and never creates misleading links" {
  mkdir -p "$test_home/.codex/user-docs" "$runtime/codex/usr"
  printf 'original-conflict\n' > "$test_home/.codex/user-docs/collision"
  printf 'destination\n' > "$runtime/codex/usr/collision"
  printf 'unrelated\n' > "$runtime/codex/unrelated"
  chmod 0555 "$runtime/codex/usr"

  run_writer_fail
  chmod 0755 "$runtime/codex/usr"
  assert_file_content original-conflict "$test_home/.codex/user-docs/collision"
  assert_file_content destination "$runtime/codex/usr/collision"
  assert_file_content unrelated "$runtime/codex/unrelated"
  [ ! -e "$test_home/codex" ]
  [ ! -e "$test_home/.serena" ]
}

@test "canonical checkout preserves private mode while normalizing state descendants" {
  chmod 0700 "$runtime"
  run_writer_ok
  [ "$(/usr/bin/stat -f '%Lp' "$runtime")" = 700 ]
  [ "$(/usr/bin/stat -f '%Lp' "$runtime/codex/usr")" = 755 ]
  run_writer_ok
  [ "$(/usr/bin/stat -f '%Lp' "$runtime")" = 700 ]
}
