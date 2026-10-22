#!/usr/bin/env bats

repo_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../../.." && pwd)"

run_awk_file() {
  local awk_file="$1"
  local input="$2"
  shift 2

  printf '%s' "$input" | awk "$@" -f "$awk_file"
}
@test "pmset power source parser extracts the active source" {
  run run_awk_file \
    "$repo_dir/libexec/brave-youtube/pmset-power-source.awk" \
    "Now drawing from 'Battery Power'
 -InternalBattery-0 (id=1234567)	82%; discharging; 3:12 remaining present: true"

  [ "$status" -eq 0 ]
  [ "$output" = "Battery Power" ]
}

# nix-darwin-test-source-target: libexec/brave-youtube/first-field.awk
@test "first field parser extracts the leading token" {
  run run_awk_file \
    "$repo_dir/libexec/brave-youtube/first-field.awk" \
    "pages count=1
ignored"

  [ "$status" -eq 0 ]
  [ "$output" = "pages" ]
}

@test "pmset battery percent parser extracts the numeric percent" {
  run run_awk_file \
    "$repo_dir/libexec/brave-youtube/pmset-battery-percent.awk" \
    "Now drawing from 'AC Power'
 -InternalBattery-0 (id=1234567)	100%; charged; 0:00 remaining present: true"

  [ "$status" -eq 0 ]
  [ "$output" = "100" ]
}

@test "pmset sleep disabled parser defaults to 0 when assertion is absent" {
  run run_awk_file \
    "$repo_dir/libexec/brave-youtube/pmset-sleep-disabled.awk" \
    "Assertion status system-wide:
   PreventUserIdleDisplaySleep    0"

  [ "$status" -eq 0 ]
  [ "$output" = "0" ]
}

@test "pmset sleep disabled parser reports the assertion value" {
  run run_awk_file \
    "$repo_dir/libexec/brave-youtube/pmset-sleep-disabled.awk" \
    "Assertion status system-wide:
   SleepDisabled                  1"

  [ "$status" -eq 0 ]
  [ "$output" = "1" ]
}

@test "test report awk helpers format and count report data" {
  run run_awk_file \
    "$repo_dir/libexec/test-report/line-count.awk" \
    "alpha
beta
gamma
"

  [ "$status" -eq 0 ]
  [ "$output" = "3" ]

  run run_awk_file \
    "$repo_dir/libexec/test-report/format-coverage-ratio.awk" \
    "" \
    -v covered=3 \
    -v total=4

  [ "$status" -eq 0 ]
  [ "$output" = "3 / 4 (75%)" ]

  run run_awk_file \
    "$repo_dir/libexec/test-report/format-coverage-ratio.awk" \
    "" \
    -v covered=0 \
    -v total=0

  [ "$status" -eq 0 ]
  [ "$output" = "0 / 0 (0%)" ]

  run run_awk_file \
    "$repo_dir/libexec/test-report/statement-coverage-counts.awk" \
    $'target\tstatus\tcovered_statements\ttotal_statements\none\tcovered\t2\t2\ntwo\tmissed\t0\t3\n'

  [ "$status" -eq 0 ]
  [ "$output" = $'2\t5' ]
}

@test "kcov stderr filter hides parser noise and preserves real errors" {
  run run_awk_file \
    "$repo_dir/libexec/test-report/kcov-stderr-filter.awk" \
    "kcov: error: 2set -euo pipefail
 is not an integer
kcov: error: Can't start/attach to script
other stderr
kcov: error: 1status_session
 is not an integer
"

  [ "$status" -eq 0 ]
  [ "$output" = "kcov: error: Can't start/attach to script
other stderr" ]
}

@test "test report awk helpers normalize target lists" {
  run run_awk_file \
    "$repo_dir/libexec/test-report/relative-paths.awk" \
    "$repo_dir/flake.nix
/tmp/outside.nix
$repo_dir/modules/kitty.nix" \
    -v prefix="$repo_dir/"

  [ "$status" -eq 0 ]
  [ "$output" = "flake.nix
modules/kitty.nix" ]

  run run_awk_file \
    "$repo_dir/libexec/test-report/nonblank-lines.awk" \
    "
alpha

beta
"

  [ "$status" -eq 0 ]
  [ "$output" = "alpha
beta" ]

  run run_awk_file \
    "$repo_dir/libexec/test-report/join-lines-by-comma.awk" \
    "alpha
beta
gamma
"

  [ "$status" -eq 0 ]
  [ "$output" = "alpha,beta,gamma" ]
}

# nix-darwin-test-source-target: libexec/test-report/header-source-targets.awk
@test "header source-target parser ignores fixture body declarations" {
  local marker
  local sample

  marker="nix-darwin-test-source-target"
  sample="#!/usr/bin/env bats
# ${marker}: bin/real-entrypoint
# regular header comment

@test \"body\" {
  printf '%s\n' '# ${marker}: bin/fixture-entrypoint'
}"

  run run_awk_file \
    "$repo_dir/libexec/test-report/header-source-targets.awk" \
    "$sample"

  [ "$status" -eq 0 ]
  [ "$output" = "bin/real-entrypoint" ]
}

@test "test report statement line counter ignores language comments and blanks" {
  run run_awk_file \
    "$repo_dir/libexec/test-report/statement-line-count.awk" \
    "# comment
NF { print }

END { print count }" \
    -v language=awk

  [ "$status" -eq 0 ]
  [ "$output" = "2" ]

  run run_awk_file \
    "$repo_dir/libexec/test-report/statement-line-count.awk" \
    "-- comment
display notification \"hello\"

use scripting additions" \
    -v language=applescript

  [ "$status" -eq 0 ]
  [ "$output" = "2" ]
}

@test "host security arp parser extracts the gateway MAC address" {
  run run_awk_file \
    "$repo_dir/libexec/host-security/arp-mac.awk" \
    "? (192.168.1.1) at a0:b1:c2:d3:e4:f5 on en0 ifscope [ethernet]"

  [ "$status" -eq 0 ]
  [ "$output" = "a0:b1:c2:d3:e4:f5" ]
}

@test "host security physical gateway parser ignores virtual default routes" {
  run run_awk_file \
    "$repo_dir/libexec/host-security/physical-gateway.awk" \
    "default            link#25           UCSIg               utun4
default            192.168.1.1       UGScg               en0
default            link#17           UCSIg               bridge100"

  [ "$status" -eq 0 ]
  [ "$output" = "192.168.1.1" ]
}

# nix-darwin-test-source-target: libexec/monitor-temp/power-watts.awk
@test "monitor temp power parser normalizes power units to watts" {
  run awk -v raw="4200 mW" -f "$repo_dir/libexec/monitor-temp/power-watts.awk"
  [ "$status" -eq 0 ]
  [ "$output" = "4.2" ]

  run awk -v raw="2500000 uW" -f "$repo_dir/libexec/monitor-temp/power-watts.awk"
  [ "$status" -eq 0 ]
  [ "$output" = "2.5" ]

  run awk -v raw="22 W" -f "$repo_dir/libexec/monitor-temp/power-watts.awk"
  [ "$status" -eq 0 ]
  [ "$output" = "22.0" ]

  run awk -v raw="22 bananas" -f "$repo_dir/libexec/monitor-temp/power-watts.awk"
  [ "$status" -eq 1 ]
}

# nix-darwin-test-source-target: libexec/monitor-temp/battery-temperature-c.awk
@test "monitor temp battery parser extracts selected battery temperature field" {
  battery_sample='    "Temperature" = 3180
    "VirtualTemperature" = 3300'

  run run_awk_file \
    "$repo_dir/libexec/monitor-temp/battery-temperature-c.awk" \
    "$battery_sample" \
    -F'= ' \
    -v field=Temperature
  [ "$status" -eq 0 ]
  [ "$output" = "31.8" ]

  run run_awk_file \
    "$repo_dir/libexec/monitor-temp/battery-temperature-c.awk" \
    "$battery_sample" \
    -F'= ' \
    -v field=VirtualTemperature
  [ "$status" -eq 0 ]
  [ "$output" = "33.0" ]
}

# nix-darwin-test-source-target: libexec/rbenv/latest-stable-ruby-version.awk
@test "rbenv ruby version parser picks the latest stable version" {
  run run_awk_file \
    "$repo_dir/libexec/rbenv/latest-stable-ruby-version.awk" \
    "  3.3.8
  3.4.5
  jruby-10.0.0.0
  4.0.0-preview1"

  [ "$status" -eq 0 ]
  [ "$output" = "3.4.5" ]
}
