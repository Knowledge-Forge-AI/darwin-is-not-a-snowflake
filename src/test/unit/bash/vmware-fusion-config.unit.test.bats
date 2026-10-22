#!/usr/bin/env bats
# nix-darwin-test-source-target: modules/virtual-machines.nix
# nix-darwin-test-source-target: libexec/virtual-machines/vmware-fusion-cli.sh

@test "vmware fusion module and wrapper remain generic core capability" {
  set -euo pipefail

  repo_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../../.." && pwd)"
  module_file="$repo_dir/modules/virtual-machines.nix"
  wrapper="$repo_dir/libexec/virtual-machines/vmware-fusion-cli.sh"

  bash -n "$wrapper"
  rg -F -q 'vmwareFusionCli = ../libexec/virtual-machines/vmware-fusion-cli.sh;' "$module_file"
  for command in vmcli vmrest vmrun ovftool; do
    rg -F -q "(fusionCli \"$command\")" "$module_file"
  done
  ! rg -qi 'vagrant|quarantine|pkgs\.tart' "$module_file"
}

@test "vmware wrapper dispatches to an overridden Fusion app bundle" {
  set -euo pipefail

  repo_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../../.." && pwd)"
  wrapper="$repo_dir/libexec/virtual-machines/vmware-fusion-cli.sh"
  test_root="$BATS_TEST_TMPDIR/vmware-wrapper"
  fake_app="$test_root/VMware Fusion.app"
  fake_library="$fake_app/Contents/Library"
  run_log="$test_root/run.log"
  mkdir -p "$fake_library"

  cat > "$fake_library/vmrun" <<'FAKE'
#!/usr/bin/env bash
printf '%s\n' "$0" > "$VMWARE_TEST_LOG"
printf '%s\n' "$*" >> "$VMWARE_TEST_LOG"
FAKE
  chmod +x "$fake_library/vmrun"
  ln -s "$wrapper" "$test_root/vmrun"

  run env VMWARE_FUSION_APP="$fake_app" VMWARE_TEST_LOG="$run_log" \
    "$test_root/vmrun" -T fusion list

  [ "$status" -eq 0 ]
  grep -Fqx "$fake_library/vmrun" "$run_log"
  grep -Fqx -- "-T fusion list" "$run_log"
}

@test "vmware wrapper rejects an unknown command name" {
  set -euo pipefail

  repo_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../../.." && pwd)"
  wrapper="$repo_dir/libexec/virtual-machines/vmware-fusion-cli.sh"
  link="$BATS_TEST_TMPDIR/not-a-fusion-command"
  ln -s "$wrapper" "$link"

  run "$link"
  [ "$status" -eq 64 ]
  [[ "$output" == *"unsupported VMware Fusion wrapper name"* ]]
}
