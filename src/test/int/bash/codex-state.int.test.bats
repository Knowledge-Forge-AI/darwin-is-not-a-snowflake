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

@test "Codex and Serena writer completes one integrated legacy transition" {
  mkdir -p \
    "$test_home/.codex/$test_user/codex" \
    "$test_home/.codex/user-docs" \
    "$test_home/.codex/usr" \
    "$runtime/codex/usr" \
    "$test_home/.serena"
  printf 'per-user\n' > "$test_home/.codex/$test_user/codex/.per-user"
  printf 'user-docs\n' > "$test_home/.codex/user-docs/space name"
  printf 'usr\n' > "$test_home/.codex/usr/-usr"
  printf 'serena\n' > "$test_home/.serena/.serena-state"

  run /bin/sh "$writer" "$test_user" "$test_home" "$runtime"
  [ "$status" -eq 0 ]

  docs_target="$runtime/codex/usr"
  serena_target="$runtime/codex/mcp/serena"
  [ "$(<"$docs_target/.per-user")" = per-user ]
  [ "$(<"$docs_target/space name")" = user-docs ]
  [ "$(<"$docs_target/-usr")" = usr ]
  [ "$(<"$serena_target/.serena-state")" = serena ]
  [ "$(readlink "$test_home/codex")" = "$docs_target" ]
  [ "$(readlink "$test_home/.serena")" = "$serena_target" ]
  [ ! -e "$test_home/codex-docs" ]

  run /bin/sh "$writer" "$test_user" "$test_home" "$runtime"
  [ "$status" -eq 0 ]
  [ "$(readlink "$test_home/codex")" = "$docs_target" ]
  [ "$(readlink "$test_home/.serena")" = "$serena_target" ]
}


@test "canonical checkout with absolute compatibility alias succeeds without alias churn" {
  mkdir "$test_home/ai"
  ln -s "$runtime" "$test_home/ai/agent-central"
  alias_before="$(/usr/bin/stat -f '%i:%m:%c' "$test_home/ai/agent-central")"

  run /bin/sh "$writer" "$test_user" "$test_home" "$runtime"
  [ "$status" -eq 0 ]
  [ "$(readlink "$test_home/ai/agent-central")" = "$runtime" ]
  [ "$(/usr/bin/stat -f '%i:%m:%c' "$test_home/ai/agent-central")" = "$alias_before" ]
  [ "$(readlink "$test_home/codex")" = "$runtime/codex/usr" ]
}

@test "canonical checkout needs no human index endpoint" {
  run /bin/sh "$writer" "$test_user" "$test_home" "$runtime"
  [ "$status" -eq 0 ]
  [ ! -e "$test_home/ai" ]
  [ ! -L "$test_home/ai" ]
  [ -d "$runtime/codex/mcp/serena" ]
}

@test "custom workspace with spaces preserves a relative compatibility alias and migrations" {
  runtime="$test_home/custom workspace/agent-central_dinas"
  mkdir -p "$runtime" "$test_home/ai" "$test_home/.codex/usr" "$test_home/.serena"
  relative_alias='../custom workspace/agent-central_dinas'
  ln -s "$relative_alias" "$test_home/ai/agent-central"
  alias_before="$(/usr/bin/stat -f '%i:%m:%c' "$test_home/ai/agent-central")"
  printf 'docs\n' > "$test_home/.codex/usr/item"
  printf 'serena\n' > "$test_home/.serena/item"

  run /bin/sh "$writer" "$test_user" "$test_home" "$runtime"
  [ "$status" -eq 0 ]
  [ "$(readlink "$test_home/ai/agent-central")" = "$relative_alias" ]
  [ "$(/usr/bin/stat -f '%i:%m:%c' "$test_home/ai/agent-central")" = "$alias_before" ]
  [ "$(<"$runtime/codex/usr/item")" = docs ]
  [ "$(<"$runtime/codex/mcp/serena/item")" = serena ]
  [ "$(readlink "$test_home/codex")" = "$runtime/codex/usr" ]
  [ "$(readlink "$test_home/.serena")" = "$runtime/codex/mcp/serena" ]
}
