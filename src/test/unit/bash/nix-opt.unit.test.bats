#!/usr/bin/env bats
# nix-darwin-test-source-target: bin/nix-opt

bats_require_minimum_version 1.5.0

setup() {
  repo_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../../.." && pwd)"
  fake_bin="$BATS_TEST_TMPDIR/bin"
  profile_dir="$BATS_TEST_TMPDIR/profiles"
  system_profile="$profile_dir/system"
  log_file="$BATS_TEST_TMPDIR/nix-opt.log"
  mkdir -p "$fake_bin" "$profile_dir"
  : > "$log_file"

  write_fake nix-collect-garbage 'printf "nix-collect-garbage%s\n" "${*:+ $*}" >> "$NIX_OPT_TEST_LOG"; if [[ -z "$*" && "${NIX_OPT_FAIL_COMMAND:-}" == 1 ]]; then exit 11; fi'
  write_fake sudo 'printf "sudo%s\n" "${*:+ $*}" >> "$NIX_OPT_TEST_LOG"; if [[ "$1" == "nix-collect-garbage" && "${NIX_OPT_FAIL_COMMAND:-}" == 2 ]]; then exit 12; fi; if [[ "$1" == "nix-env" && "${NIX_OPT_FAIL_COMMAND:-}" == 4 ]]; then exit 14; fi'
  write_fake nix-store 'printf "nix-store%s\n" "${*:+ $*}" >> "$NIX_OPT_TEST_LOG"; if [[ "${NIX_OPT_FAIL_COMMAND:-}" == 3 ]]; then exit 13; fi'
}

write_fake() {
  local name="$1"
  local body="$2"

  {
    printf '%s\n' '#!/usr/bin/env bash'
    printf '%s\n' 'set -euo pipefail'
    printf '%s\n' "$body"
  } > "$fake_bin/$name"
  chmod +x "$fake_bin/$name"
}

make_generations() {
  local current="$1"
  shift

  local id
  for id in "$@"; do
    mkdir -p "$profile_dir/store-$id"
    ln -sfn "store-$id" "$profile_dir/system-$id-link"
  done
  ln -sfn "system-$current-link" "$system_profile"
}

make_range() {
  local first="$1" last="$2" id
  local ids=()
  for ((id = first; id <= last; id++)); do
    ids+=("$id")
  done
  make_generations "$last" "${ids[@]}"
}

run_nix_opt() {
  run env PATH="$fake_bin:$PATH" \
    NIX_OPT_TEST_LOG="$log_file" \
    NIX_OPT_FAIL_COMMAND="${NIX_OPT_FAIL_COMMAND:-}" \
    NIX_DARWIN_SYSTEM_PROFILE="$system_profile" \
    "$repo_dir/bin/nix-opt" "$@"
}

deletion_line() {
  grep -E '^sudo nix-env ' "$log_file" || true
}

@test "nix-opt help documents the retained rollback window without running maintenance" {
  run "$repo_dir/bin/nix-opt" --help

  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"newest 16 system generations are retained"* ]]
  [[ "$output" == *"active generation is always retained"* ]]
  [ ! -s "$log_file" ]
}

@test "nix-opt deletes nothing when the profile holds exactly the retained window" {
  make_range 1 16
  run_nix_opt

  [ "$status" -eq 0 ]
  [[ "$output" == *"generations to delete: none"* ]]
  [ -z "$(deletion_line)" ]
}

@test "nix-opt deletes nothing when the profile holds fewer than the retained window" {
  make_range 169 173
  run_nix_opt

  [ "$status" -eq 0 ]
  [[ "$output" == *"current generation: 173"* ]]
  [[ "$output" == *"generations to delete: none"* ]]
  [ -z "$(deletion_line)" ]
}

@test "nix-opt deletes the single oldest generation at seventeen" {
  make_range 1 17
  run_nix_opt

  [ "$status" -eq 0 ]
  [ "$(deletion_line)" = "sudo nix-env --profile $system_profile --delete-generations 1" ]
}

@test "nix-opt retains the newest sixteen at twenty generations" {
  make_range 1 20
  run_nix_opt

  [ "$status" -eq 0 ]
  [ "$(deletion_line)" = "sudo nix-env --profile $system_profile --delete-generations 4 3 2 1" ]
  [[ "$output" == *"retained generations: 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5"* ]]
}

@test "nix-opt orders generations numerically rather than lexically" {
  make_generations 12 1 2 9 10 11 12
  run_nix_opt --keep 3

  [ "$status" -eq 0 ]
  [[ "$output" == *"discovered generations: 12 11 10 9 2 1"* ]]
  [ "$(deletion_line)" = "sudo nix-env --profile $system_profile --delete-generations 9 2 1" ]
}

@test "nix-opt handles non-contiguous generation identifiers" {
  make_generations 900 4 17 300 900
  run_nix_opt --keep 2

  [ "$status" -eq 0 ]
  [[ "$output" == *"retained generations: 900 300"* ]]
  [ "$(deletion_line)" = "sudo nix-env --profile $system_profile --delete-generations 17 4" ]
}

@test "nix-opt protects the current generation when a rollback places it outside the window" {
  make_generations 5 1 2 3 4 5 6 7 8
  run_nix_opt --keep 2

  [ "$status" -eq 0 ]
  [[ "$output" == *"current generation: 5"* ]]
  [[ "$output" == *"retained generations: 8 7 5"* ]]
  [[ "$output" == *"current generation protected outside the newest 2"* ]]
  [ "$(deletion_line)" = "sudo nix-env --profile $system_profile --delete-generations 6 4 3 2 1" ]
}

@test "nix-opt reports the current generation as inside the window when it is newest" {
  make_range 1 20
  run_nix_opt

  [ "$status" -eq 0 ]
  [[ "$output" == *"current generation is inside the newest 16"* ]]
}

@test "nix-opt reports the current generation as inside the window when it is not newest" {
  make_generations 18 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20
  run_nix_opt

  [ "$status" -eq 0 ]
  [[ "$output" == *"current generation: 18"* ]]
  [[ "$output" == *"current generation is inside the newest 16"* ]]
  [ "$(deletion_line)" = "sudo nix-env --profile $system_profile --delete-generations 4 3 2 1" ]
}

@test "nix-opt fails closed when the current generation link is unmapped" {
  make_range 1 5
  ln -sfn "system-99-link" "$system_profile"
  run_nix_opt

  [ "$status" -eq 65 ]
  [[ "$output" == *"current generation 99 has no generation link"* ]]
  [ ! -s "$log_file" ]
}

@test "nix-opt fails closed when the current link is not a generation link" {
  make_range 1 5
  ln -sfn "store-3" "$system_profile"
  run_nix_opt

  [ "$status" -eq 65 ]
  [[ "$output" == *"cannot identify the current generation"* ]]
  [ ! -s "$log_file" ]
}

@test "nix-opt fails closed when a generation link is malformed" {
  make_range 1 5
  ln -sfn "store-1" "$profile_dir/system-old-link"
  run_nix_opt

  [ "$status" -eq 65 ]
  [[ "$output" == *"malformed generation link: system-old-link"* ]]
  [ ! -s "$log_file" ]
}

@test "nix-opt fails closed when the profile holds no generations" {
  ln -sfn "system-1-link" "$system_profile"
  run_nix_opt

  [ "$status" -eq 65 ]
  [ ! -s "$log_file" ]
}

@test "nix-opt fails closed when the system profile is not a symlink" {
  : > "$system_profile"
  run_nix_opt

  [ "$status" -eq 65 ]
  [[ "$output" == *"not a symlink"* ]]
  [ ! -s "$log_file" ]
}

@test "nix-opt rejects a zero retain count" {
  make_range 1 5
  run_nix_opt --keep 0

  [ "$status" -eq 64 ]
  [[ "$output" == *"--keep must be at least 1"* ]]
  [ ! -s "$log_file" ]
}

@test "nix-opt rejects a negative retain count" {
  make_range 1 5
  run_nix_opt --keep -3

  [ "$status" -eq 64 ]
  [[ "$output" == *"--keep must be a positive integer"* ]]
  [ ! -s "$log_file" ]
}

@test "nix-opt rejects a non-integer retain count" {
  make_range 1 5
  run_nix_opt --keep sixteen

  [ "$status" -eq 64 ]
  [[ "$output" == *"--keep must be a positive integer"* ]]
  [ ! -s "$log_file" ]
}

@test "nix-opt rejects an unbounded retain count" {
  make_range 1 5
  run_nix_opt --keep 100000

  [ "$status" -eq 64 ]
  [[ "$output" == *"--keep must not exceed 10000"* ]]
  [ ! -s "$log_file" ]
}

@test "nix-opt rejects a missing retain value" {
  make_range 1 5
  run_nix_opt --keep

  [ "$status" -eq 64 ]
  [[ "$output" == *"--keep requires a value"* ]]
  [ ! -s "$log_file" ]
}

@test "nix-opt rejects unknown arguments" {
  make_range 1 5
  run_nix_opt --purge

  [ "$status" -eq 64 ]
  [[ "$output" == *"unknown argument: --purge"* ]]
  [ ! -s "$log_file" ]
}

@test "nix-opt accepts a valid retain count" {
  make_range 1 20
  run_nix_opt --keep 18

  [ "$status" -eq 0 ]
  [[ "$output" == *"retain count: 18"* ]]
  [ "$(deletion_line)" = "sudo nix-env --profile $system_profile --delete-generations 2 1" ]
}

@test "nix-opt dry run mutates nothing" {
  make_range 1 20
  run_nix_opt --dry-run

  [ "$status" -eq 0 ]
  [[ "$output" == *"current generation: 20"* ]]
  [[ "$output" == *"discovered generations: 20 19"* ]]
  [[ "$output" == *"retained generations: 20 19"* ]]
  [[ "$output" == *"generations to delete: 4 3 2 1"* ]]
  [[ "$output" == *"retain count: 16"* ]]
  [[ "$output" == *"current generation is inside the newest 16"* ]]
  [[ "$output" == *"dry run"* ]]
  [ ! -s "$log_file" ]
}

@test "nix-opt dry run honours an explicit retain count without mutating" {
  make_range 1 20
  run_nix_opt --dry-run --keep 5

  [ "$status" -eq 0 ]
  [[ "$output" == *"retain count: 5"* ]]
  [[ "$output" == *"generations to delete: 15 14 13"* ]]
  [ ! -s "$log_file" ]
}

@test "nix-opt runs pruning, then garbage collection, then optimization" {
  make_range 1 20
  run_nix_opt

  [ "$status" -eq 0 ]
  [ "$(sed -n '1p' "$log_file")" = "sudo nix-env --profile $system_profile --delete-generations 4 3 2 1" ]
  [ "$(sed -n '2p' "$log_file")" = "nix-collect-garbage" ]
  [ "$(sed -n '3p' "$log_file")" = "sudo nix-collect-garbage" ]
  [ "$(sed -n '4p' "$log_file")" = "nix-store --optimise" ]
  [ "$(wc -l < "$log_file" | tr -d ' ')" -eq 4 ]
}

@test "nix-opt never invokes destructive nix-collect-garbage -d" {
  make_range 1 20
  run_nix_opt

  [ "$status" -eq 0 ]
  ! grep -Fq 'nix-collect-garbage -d' "$log_file"
  ! grep -Fq -- '--delete-old' "$log_file"
}

@test "nix-opt stops when generation pruning fails" {
  make_range 1 20
  NIX_OPT_FAIL_COMMAND=4
  run_nix_opt

  [ "$status" -eq 14 ]
  [ "$(wc -l < "$log_file" | tr -d ' ')" -eq 1 ]
}

@test "nix-opt stops when unprivileged garbage collection fails" {
  make_range 1 5
  NIX_OPT_FAIL_COMMAND=1
  run_nix_opt

  [ "$status" -eq 11 ]
  [ "$(cat "$log_file")" = "nix-collect-garbage" ]
}

@test "nix-opt stops before store optimization when privileged collection fails" {
  make_range 1 5
  NIX_OPT_FAIL_COMMAND=2
  run_nix_opt

  [ "$status" -eq 12 ]
  [ "$(sed -n '1p' "$log_file")" = "nix-collect-garbage" ]
  [ "$(sed -n '2p' "$log_file")" = "sudo nix-collect-garbage" ]
  [ "$(wc -l < "$log_file" | tr -d ' ')" -eq 2 ]
}

@test "nix-opt propagates store optimization failure" {
  make_range 1 5
  NIX_OPT_FAIL_COMMAND=3
  run_nix_opt

  [ "$status" -eq 13 ]
  [ "$(sed -n '3p' "$log_file")" = "nix-store --optimise" ]
  [ "$(wc -l < "$log_file" | tr -d ' ')" -eq 3 ]
}
