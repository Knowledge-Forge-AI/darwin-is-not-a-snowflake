#!/usr/bin/env bats
# nix-darwin-test-source-target: shell/sh_config/common/000_nix-darwin-local
# nix-darwin-test-source-target: shell/sh_config/common/013_nix-darwin-generated

bats_require_minimum_version 1.5.0

setup() {
  repo_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../../.." && pwd)"
  local_fragment="$repo_dir/shell/sh_config/common/000_nix-darwin-local"
  loader="$repo_dir/shell/sh_config/common/013_nix-darwin-generated"
  local_root="$BATS_TEST_TMPDIR/local root"
}

run_loader() {
  local shell_path="$1"
  shift
  env NIX_DARWIN_LOCAL="$local_root" "$shell_path" -c '
    . "$1"
    . "$2"
    printf "TRACE=%s VALUE=%s\n" "${TRACE:-}" "${VALUE:-}"
  ' _ "$local_fragment" "$loader" "$@"
}

run_zsh_option_probe() {
  local initial_state="$1"
  env NIX_DARWIN_LOCAL="$local_root" /bin/zsh -c '
    if [ "$3" = on ]; then
      setopt nomatch
    else
      unsetopt nomatch
    fi
    before=$options[nomatch]
    . "$1"
    . "$2"
    printf "before=%s after=%s\n" "$before" "$options[nomatch]"
  ' _ "$local_fragment" "$loader" "$initial_state"
}

@test "generated shell loader contains no executable eval" {
  run grep -E '(^|[^[:alnum:]_])eval([^[:alnum:]_]|$)' "$loader"
  [ "$status" -eq 1 ]
}

@test "generated shell loader is a no-op when no generation exists" {
  for shell_path in /bin/bash /bin/zsh; do
    run run_loader "$shell_path"
    [ "$status" -eq 0 ]
    [ "$output" = "TRACE= VALUE=" ]
  done

  mkdir -p "$local_root/generated/shell/generations/empty"
  ln -s generations/empty "$local_root/generated/shell/current"

  for shell_path in /bin/bash /bin/zsh; do
    run run_loader "$shell_path"
    [ "$status" -eq 0 ]
    [ "$output" = "TRACE= VALUE=" ]
  done
}

@test "generated fragments load in lexical order under Bash and Zsh" {
  generation="$local_root/generated/shell/generations/one"
  mkdir -p "$generation"
  printf '%s\n' 'TRACE="${TRACE:-}a"' > "$generation/100_first.sh"
  printf '%s\n' 'TRACE="${TRACE:-}b"' 'export VALUE=loaded' > "$generation/200_second.sh"
  ln -s generations/one "$local_root/generated/shell/current"

  for shell_path in /bin/bash /bin/zsh; do
    run run_loader "$shell_path"
    [ "$status" -eq 0 ]
    [ "$output" = "TRACE=ab VALUE=loaded" ]
  done
}

@test "one shell load never mixes generations across a pointer swap" {
  generated="$local_root/generated/shell"
  first="$generated/generations/first"
  second="$generated/generations/second"
  mkdir -p "$first" "$second"
  printf 'TRACE=old-a\nln -sfn %q %q\n' "$second" "$generated/current" > "$first/100_swap.sh"
  printf '%s\n' 'TRACE="${TRACE}-old-b"' > "$first/200_finish.sh"
  printf '%s\n' 'TRACE=new-a' > "$second/100_swap.sh"
  printf '%s\n' 'TRACE="${TRACE}-new-b"' > "$second/200_finish.sh"
  for shell_path in /bin/bash /bin/zsh; do
    ln -sfn generations/first "$generated/current"

    run run_loader "$shell_path"
    [ "$status" -eq 0 ]
    [ "$output" = "TRACE=old-a-old-b VALUE=" ]

    run run_loader "$shell_path"
    [ "$status" -eq 0 ]
    [ "$output" = "TRACE=new-a-new-b VALUE=" ]
  done
}

@test "Zsh restores nomatch state after an empty generation" {
  mkdir -p "$local_root/generated/shell/generations/empty"
  ln -s generations/empty "$local_root/generated/shell/current"

  run run_zsh_option_probe on
  [ "$status" -eq 0 ]
  [ "$output" = "before=on after=on" ]

  run run_zsh_option_probe off
  [ "$status" -eq 0 ]
  [ "$output" = "before=off after=off" ]
}

@test "generation and fragment metacharacters are data, not shell syntax" {
  generated="$local_root/generated/shell"
  generation="$generated/generations/generation\$(touch PWNED_PATH)"
  fragment="$generation/100_fragment_\$(touch PWNED_FILE).sh"
  mkdir -p "$generation"
  printf '%s\n' 'export VALUE=safe' > "$fragment"
  ln -s 'generations/generation$(touch PWNED_PATH)' "$generated/current"

  for shell_path in /bin/bash /bin/zsh; do
    run env NIX_DARWIN_LOCAL="$local_root" "$shell_path" -c '
      cd "$3"
      . "$1"
      . "$2"
      printf "VALUE=%s\n" "${VALUE:-}"
    ' _ "$local_fragment" "$loader" "$BATS_TEST_TMPDIR"
    [ "$status" -eq 0 ]
    [ "$output" = "VALUE=safe" ]
    [ ! -e "$BATS_TEST_TMPDIR/PWNED_PATH" ]
    [ ! -e "$BATS_TEST_TMPDIR/PWNED_FILE" ]
  done
}
