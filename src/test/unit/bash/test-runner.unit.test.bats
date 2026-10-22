#!/usr/bin/env bats
# nix-darwin-test-source-target: bin/nix-int-test
# nix-darwin-test-source-target: bin/nix-test
# nix-darwin-test-source-target: bin/nix-unit-test
# nix-darwin-test-source-target: tools/test/kcov-bash-wrapper
# nix-darwin-test-source-target: tools/test/run

@test "test runner launchers expose safe help and parse as bash" {
  set -euo pipefail

  script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
  repo_dir="$(cd "$script_dir/../../../.." && pwd)"

  for script in \
    "$repo_dir/bin/nix-int-test" \
    "$repo_dir/bin/nix-test" \
    "$repo_dir/bin/nix-unit-test" \
    "$repo_dir/scripts/test-all.sh" \
    "$repo_dir/tools/test/run"
  do
    [ "$(sed -n '1p' "$script")" = '#!/run/current-system/sw/bin/bash' ]
  done

  for script in \
    "$repo_dir/tools/test/kcov-bash-wrapper" \
    "$repo_dir/tools/test/run"
  do
    bash -n "$script"
  done

  [ "$(sed -n '1p' "$repo_dir/tools/test/kcov-bash-wrapper")" = '#!/usr/bin/env bash' ]
  ! rg -F -q '/bin/bash' \
    "$repo_dir/tools/test/kcov-bash-wrapper" \
    "$repo_dir/tools/test/run"

  run "$repo_dir/tools/test/run" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: run"* || "$output" == *"Usage:"* ]]
  [[ "$output" == *"--report"* ]]
  [[ "$output" == *"-j, --jobs N"* ]]
  [[ "$output" == *"NIX_DARWIN_TEST_JOBS"* ]]
  [[ "$output" == *"NIX_DARWIN_TEST_SOURCE"* ]]
  [[ "$output" == *"--jobs 1"* ]]

  for launcher in nix-int-test nix-test nix-unit-test; do
    run "$repo_dir/bin/$launcher" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Run nix-darwin tests."* ]]
  done
}

@test "test runner keeps git purity by default and validates worktree opt-in" {
  set -euo pipefail

  script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
  repo_dir="$(cd "$script_dir/../../../.." && pwd)"
  rg -F -q 'test_source="${NIX_DARWIN_TEST_SOURCE:-git}"' "$repo_dir/tools/test/run"
  rg -F -q 'worktree) flake_ref="path:$repo_dir"' "$repo_dir/tools/test/run"

  run env NIX_DARWIN_TEST_SOURCE=invalid "$repo_dir/tools/test/run" --help
  [ "$status" -eq 2 ]
  [[ "$output" == *"must be git or worktree"* ]]
}

@test "test runner re-execs through Nix-managed Bash when forced through macOS Bash" {
  set -euo pipefail

  script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
  repo_dir="$(cd "$script_dir/../../../.." && pwd)"
  managed_bash="/run/current-system/sw/bin/bash"

  [ -x "$managed_bash" ]

  run /bin/bash -x "$repo_dir/tools/test/run" --help

  [ "$status" -eq 0 ]
  [[ "$output" == *"exec $managed_bash $repo_dir/tools/test/run --help"* ]]
  [[ "$output" == *"Run nix-darwin tests."* ]]
}

@test "test runner integration fixture isolates the sequential baseline" {
  set -euo pipefail

  script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
  repo_dir="$(cd "$script_dir/../../../.." && pwd)"
  integration_test="$repo_dir/src/test/int/bash/test-runner.int.test.bats"

  rg -F -q 'unset NIX_DARWIN_TEST_JOBS' "$integration_test"
  rg -F -q 'unset NIX_DARWIN_TEST_SOURCE' "$integration_test"
}

@test "test runner launchers resolve the flake root through symlinked nbin" {
  set -euo pipefail

  script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
  repo_dir="$(cd "$script_dir/../../../.." && pwd)"
  link_parent="$BATS_TEST_TMPDIR/local"
  mkdir -p "$link_parent"
  ln -s "$repo_dir/bin" "$link_parent/nbin"

  for launcher in nix-int-test nix-test nix-unit-test; do
    run "$link_parent/nbin/$launcher" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Run nix-darwin tests."* ]]
  done
}

@test "test runner launchers pass jobs through with their scope" {
  set -euo pipefail

  script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
  repo_dir="$(cd "$script_dir/../../../.." && pwd)"
  fake_root="$BATS_TEST_TMPDIR/runner-root"
  log_file="$BATS_TEST_TMPDIR/wrapper.log"
  launchers=(nix-test nix-unit-test nix-int-test)
  scopes=(all unit int)
  mkdir -p "$fake_root/bin" "$fake_root/tools/test"

  for launcher in "${launchers[@]}"; do
    cp "$repo_dir/bin/$launcher" "$fake_root/bin/$launcher"
  done

  cat > "$fake_root/tools/test/run" <<'TEST'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" > "$NIX_DARWIN_TEST_WRAPPER_LOG"
TEST
  chmod +x "$fake_root/tools/test/run"

  for index in "${!launchers[@]}"; do
    : > "$log_file"
    run env NIX_DARWIN_TEST_WRAPPER_LOG="$log_file" \
      /bin/bash "$fake_root/bin/${launchers[$index]}" --jobs 2
    [ "$status" -eq 0 ]
    [ "$(cat "$log_file")" = "${scopes[$index]} --jobs 2" ]
  done
}

@test "test runner rejects invalid jobs values before running lanes" {
  set -euo pipefail

  script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
  repo_dir="$(cd "$script_dir/../../../.." && pwd)"
  missing_repo="$BATS_TEST_TMPDIR/missing-repo"

  for jobs_value in 0 -1 nope ""; do
    run env NIX_DARWIN_TEST_ROOT="$missing_repo" "$repo_dir/tools/test/run" unit --jobs "$jobs_value"
    [ "$status" -ne 0 ]
    [[ "$output" == *"nix-darwin-test: jobs must be a positive integer"* ]]
  done

  for jobs_value in 0 ""; do
    run env NIX_DARWIN_TEST_ROOT="$missing_repo" NIX_DARWIN_TEST_JOBS="$jobs_value" "$repo_dir/tools/test/run" unit
    [ "$status" -ne 0 ]
    [[ "$output" == *"nix-darwin-test: jobs must be a positive integer"* ]]
  done

  run env NIX_DARWIN_TEST_ROOT="$missing_repo" "$repo_dir/tools/test/run" unit --jobs
  [ "$status" -ne 0 ]
  [[ "$output" == *"nix-darwin-test: --jobs requires a positive integer"* ]]
}

@test "test runner rejects invalid Nix build jobs before running checks" {
  set -euo pipefail

  script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
  repo_dir="$(cd "$script_dir/../../../.." && pwd)"
  missing_repo="$BATS_TEST_TMPDIR/missing-repo"

  for build_jobs in 0 -1 nope; do
    run env NIX_DARWIN_TEST_ROOT="$missing_repo" \
      NIX_DARWIN_BUILD_JOBS="$build_jobs" \
      "$repo_dir/tools/test/run" int --jobs 2
    [ "$status" -ne 0 ]
    [[ "$output" == *"nix-darwin-test: NIX_DARWIN_BUILD_JOBS must be a positive integer"* ]]
  done
}
