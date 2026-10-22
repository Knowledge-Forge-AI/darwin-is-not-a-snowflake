#!/usr/bin/env bats
# nix-darwin-test-source-target: src/test/fixtures/node-bootstrap/package.json

bats_require_minimum_version 1.5.0

setup() {
  repo_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../../.." && pwd)"
  fixture_dir="$repo_dir/src/test/fixtures/node-bootstrap"
}

@test "node-bootstrap fixture validates system tools, local override, and react compilation" {
  test_tmp="$BATS_TEST_TMPDIR/node-bootstrap"
  mkdir -p "$test_tmp"
  cp -R "$fixture_dir"/* "$test_tmp/"
  cd "$test_tmp"

  node_bin="$(nix build --no-link --print-out-paths "path:$repo_dir#packages.aarch64-darwin.nixDarwinManagedNode")/bin"
  export PATH="$node_bin:$PATH"

  # System tools are available before local installation
  command -v node >/dev/null 2>&1
  command -v npm >/dev/null 2>&1
  command -v tsc >/dev/null 2>&1
  command -v vite >/dev/null 2>&1
  command -v vitest >/dev/null 2>&1

  command -v tsx >/dev/null 2>&1
  node --version | grep -Eq '^v22\.'

  # Nix supplies the exact lock's dependency cache and runs npm, tsc, Vitest,
  # and the ReactDOM server render without registry access in the build sandbox.
  run nix build --no-link --print-out-paths \
    "path:$repo_dir#checks.aarch64-darwin.node-bootstrap-fixture"
  [ "$status" -eq 0 ]
  fixture_out="${lines[${#lines[@]} - 1]}"
  run grep -E '"vitest":"vitest/3\.' \
    "$fixture_out/share/dinas-node-bootstrap-fixture/local-tools.json"
  [ "$status" -eq 0 ]
}
