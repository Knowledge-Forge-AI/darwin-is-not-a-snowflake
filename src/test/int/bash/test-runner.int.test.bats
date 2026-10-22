#!/usr/bin/env bats

@test "test-runner.int.test" {
set -euo pipefail
unset NIX_DARWIN_BUILD_JOBS
unset NIX_DARWIN_TEST_JOBS
unset NIX_DARWIN_TEST_SOURCE

script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
repo_dir="$(cd "$script_dir/../../../.." && pwd)"
test_all="$repo_dir/scripts/test-all.sh"
test_runner="$repo_dir/tools/test/run"
kcov_bash_wrapper="$repo_dir/tools/test/kcov-bash-wrapper"
ruby_test_runner="$repo_dir/tools/test/ruby/minitest-runner.rb"
ruby_simplecov_prelude="$repo_dir/tools/test/ruby/simplecov-prelude.rb"
report_renderer="$repo_dir/tools/test/report/render.py"
test_report_awk_dir="$repo_dir/libexec/test-report"
statement_coverage_renderer="$repo_dir/libexec/test-report/statement_coverage.py"
statement_coverage_template="$repo_dir/libexec/test-report/statement-coverage.html"
statement_coverage_row_template="$repo_dir/libexec/test-report/statement-coverage-row.html"
nix_test="$repo_dir/bin/nix-test"
nix_unit_test="$repo_dir/bin/nix-unit-test"
nix_int_test="$repo_dir/bin/nix-int-test"

fail() {
  echo "test-runner.int.test: $*" >&2
  exit 1
}

[ -f "$test_all" ] || fail "missing scripts/test-all.sh"
[ -f "$test_runner" ] || fail "missing tools/test/run"
[ -f "$kcov_bash_wrapper" ] || fail "missing tools/test/kcov-bash-wrapper"
[ -f "$ruby_simplecov_prelude" ] || fail "missing tools/test/ruby/simplecov-prelude.rb"
[ -f "$report_renderer" ] || fail "missing tools/test/report/render.py"
[ -f "$statement_coverage_renderer" ] || fail "missing libexec/test-report/statement_coverage.py"
[ -f "$statement_coverage_template" ] || fail "missing libexec/test-report/statement-coverage.html"
[ -f "$statement_coverage_row_template" ] || fail "missing libexec/test-report/statement-coverage-row.html"
[ -f "$nix_test" ] || fail "missing bin/nix-test"
[ -f "$nix_unit_test" ] || fail "missing bin/nix-unit-test"
[ -f "$nix_int_test" ] || fail "missing bin/nix-int-test"
for test_report_awk in \
  format-coverage-ratio.awk \
  header-source-targets.awk \
  join-lines-by-comma.awk \
  kcov-stderr-filter.awk \
  line-count.awk \
  nonblank-lines.awk \
  relative-paths.awk \
  statement-coverage-counts.awk \
  statement-line-count.awk
do
  [ -f "$test_report_awk_dir/$test_report_awk" ] \
    || fail "missing libexec/test-report/$test_report_awk"
done

html_heredoc_marker="$(printf 'cat %s%sHTML' '<' '<')"
if rg -F -q "$html_heredoc_marker" "$test_runner"; then
  fail "tools/test/run must render statement coverage HTML through a dedicated renderer, not an embedded HTML heredoc"
fi
rg -F -q 'test_report_libexec="$repo_dir/libexec/test-report"' "$test_runner" \
  || fail "tools/test/run must locate its test-report libexec helpers"
for test_report_awk in \
  format-coverage-ratio.awk \
  header-source-targets.awk \
  join-lines-by-comma.awk \
  kcov-stderr-filter.awk \
  line-count.awk \
  nonblank-lines.awk \
  relative-paths.awk \
  statement-coverage-counts.awk \
  statement-line-count.awk
do
  rg -F -q "$test_report_awk" "$test_runner" \
    || fail "tools/test/run must delegate to libexec/test-report/$test_report_awk"
done
if rg -F -q "awk '" "$test_runner"; then
  fail "tools/test/run must not embed awk source directly"
fi
if rg -F -q '"$NIX_DARWIN_TEST_ROOT"/src/test/*' "$kcov_bash_wrapper"; then
  fail "kcov Bash wrapper must trace Bats test processes so sourced production Bash is visible to coverage"
fi
rg -F -q 'NIX_DARWIN_KCOV_EXCLUDE_PATHS="$repo_dir/src/test,$repo_dir/.test-reports,$repo_dir/tools/test"' "$test_runner" \
  || fail "tools/test/run must exclude Bats tests from kcov reports instead of bypassing their trace processes"

test_tmp_root="$(mktemp -d)"
cleanup() {
  rm -rf "$test_tmp_root"
}
trap cleanup EXIT

run_captured_status() {
  local output_file="$1"

  shift
  if "$@" > "$output_file" 2>&1; then
    return 0
  else
    return "$?"
  fi
}

fake_repo="$test_tmp_root/repo"
fake_home="$test_tmp_root/home"
fake_python_cache="$fake_home/.cache/python/__pycache__"
fake_bin="$test_tmp_root/bin"
run_log="$test_tmp_root/run.log"
nix_unit_gc_roots_regex='.*/nix-darwin-nix-unit\.[^/]*/gc-roots'
report_dir="$fake_repo/.test-reports"
mkdir -p \
  "$fake_repo/bin" \
  "$fake_repo/libexec/brave-youtube" \
  "$fake_repo/libexec/proton-vpn" \
  "$fake_repo/libexec/test-report" \
  "$fake_repo/scripts" \
  "$fake_repo/tools/bootstrap" \
  "$fake_repo/modules" \
  "$fake_repo/shell/sh_config/bash" \
  "$fake_repo/shell/sh_config/common" \
  "$fake_repo/shell/sh_config/zsh" \
  "$fake_repo/src/test/unit/bash" \
  "$fake_repo/src/test/int/bash" \
  "$fake_repo/src/test/unit/python" \
  "$fake_repo/src/test/int/python" \
  "$fake_repo/src/test/unit/awk" \
  "$fake_repo/src/test/int/awk" \
  "$fake_repo/src/test/unit/applescript" \
  "$fake_repo/src/test/int/applescript" \
  "$fake_repo/src/test/unit/zsh" \
  "$fake_repo/src/test/int/zsh" \
  "$fake_repo/src/test/unit/ruby" \
  "$fake_repo/src/test/int/ruby" \
  "$fake_repo/src/test/unit/nix" \
  "$fake_repo/src/test/int/nix" \
  "$fake_home" \
  "$fake_bin"
: > "$run_log"
cp "$test_report_awk_dir"/*.awk "$fake_repo/libexec/test-report/"

cat > "$fake_repo/flake.nix" <<'TEST'
{
  outputs = { self }: { };
}
TEST
cat > "$fake_repo/modules/foo.nix" <<'TEST'
{ ... }: { }
TEST
cat > "$fake_repo/modules/bar.nix" <<'TEST'
{ ... }: { }
TEST
cat > "$fake_repo/modules/baz.nix" <<'TEST'
{ ... }: { }
TEST

cat > "$fake_repo/bin/covered-bash-tool" <<'TEST'
#!/usr/bin/env bash
echo covered-bash-tool
TEST
chmod +x "$fake_repo/bin/covered-bash-tool"

cat > "$fake_repo/bin/covered-zsh-tool" <<'TEST'
#!/usr/bin/env zsh
covered_zsh_status=ready
TEST
chmod +x "$fake_repo/bin/covered-zsh-tool"

cat > "$fake_repo/bin/covered-python-tool" <<'TEST'
#!/usr/bin/env python3
print("covered-python-tool")
TEST
chmod +x "$fake_repo/bin/covered-python-tool"

cat > "$fake_repo/bin/covered-ruby-tool" <<'TEST'
#!/usr/bin/env ruby
puts "covered-ruby-tool"
TEST
chmod +x "$fake_repo/bin/covered-ruby-tool"

for runner_name in nix-test nix-unit-test nix-int-test; do
  cat > "$fake_repo/bin/$runner_name" <<'TEST'
#!/usr/bin/env bash
echo test-runner
TEST
  chmod +x "$fake_repo/bin/$runner_name"
done

cat > "$fake_repo/scripts/test-alpha.sh" <<'TEST'
#!/usr/bin/env bash
echo alpha
TEST
chmod +x "$fake_repo/scripts/test-alpha.sh"

cat > "$fake_repo/libexec/proton-vpn/example.awk" <<'TEST'
# comment
match-active-line
emit-done-line
TEST

cat > "$fake_repo/libexec/brave-youtube/display-notification.applescript" <<'TEST'
-- comment
use scripting additions
display notification "message" with title "title"
TEST

cat > "$fake_repo/tools/bootstrap/bootstrap-macos" <<'TEST'
#!/usr/bin/env zsh
# comment
bootstrap_status() {
  print ready
}
TEST
chmod +x "$fake_repo/tools/bootstrap/bootstrap-macos"

cat > "$fake_repo/src/test/unit/bash/alpha.unit.test.bats" <<'TEST'
#!/usr/bin/env bash
# nix-darwin-test-source-target: bin/covered-bash-tool
echo alpha
TEST
chmod +x "$fake_repo/src/test/unit/bash/alpha.unit.test.bats"

cat > "$fake_repo/src/test/unit/bash/beta.unit.test.bats" <<'TEST'
#!/usr/bin/env bash
# nix-darwin-test-source-target: bin/covered-bash-tool
echo beta
TEST
chmod +x "$fake_repo/src/test/unit/bash/beta.unit.test.bats"

cat > "$fake_repo/src/test/int/bash/runtime.int.test.bats" <<'TEST'
#!/usr/bin/env bash
# nix-darwin-test-source-target: bin/covered-bash-tool
echo runtime
TEST
chmod +x "$fake_repo/src/test/int/bash/runtime.int.test.bats"

cat > "$fake_repo/src/test/int/bash/runtime-extra.int.test.bats" <<'TEST'
#!/usr/bin/env bash
# nix-darwin-test-source-target: bin/covered-bash-tool
echo runtime-extra
TEST
chmod +x "$fake_repo/src/test/int/bash/runtime-extra.int.test.bats"

cat > "$fake_repo/src/test/unit/bash/legacy.unit.test.sh" <<'TEST'
#!/usr/bin/env bash
echo legacy
TEST
chmod +x "$fake_repo/src/test/unit/bash/legacy.unit.test.sh"

cat > "$fake_repo/scripts/test-beta.py" <<'TEST'
print("beta")
TEST

cat > "$fake_repo/src/test/unit/python/cloud_capture_sync.unit.test.py" <<'TEST'
# nix-darwin-test-source-target: bin/covered-python-tool
def test_unit_python_lane():
    assert True
TEST

cat > "$fake_repo/src/test/int/python/cloud_capture_sync.int.test.py" <<'TEST'
# nix-darwin-test-source-target: bin/covered-python-tool
def test_int_python_lane():
    assert True
TEST

cat > "$fake_repo/src/test/unit/awk/parser.unit.test.bats" <<'TEST'
#!/usr/bin/env bash
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
: "$repo_dir/libexec/test-report/format-coverage-ratio.awk"
: "$repo_dir/libexec/test-report/join-lines-by-comma.awk"
: "$repo_dir/libexec/test-report/kcov-stderr-filter.awk"
: "$repo_dir/libexec/test-report/line-count.awk"
: "$repo_dir/libexec/test-report/nonblank-lines.awk"
: "$repo_dir/libexec/test-report/relative-paths.awk"
: "$repo_dir/libexec/test-report/statement-coverage-counts.awk"
: "$repo_dir/libexec/test-report/statement-line-count.awk"
printf '%s\n' active | awk -f "$repo_dir/libexec/proton-vpn/example.awk"
TEST

cat > "$fake_repo/src/test/int/awk/parser.int.test.bats" <<'TEST'
#!/usr/bin/env bash
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
: "$repo_dir/libexec/test-report/format-coverage-ratio.awk"
: "$repo_dir/libexec/test-report/join-lines-by-comma.awk"
: "$repo_dir/libexec/test-report/kcov-stderr-filter.awk"
: "$repo_dir/libexec/test-report/line-count.awk"
: "$repo_dir/libexec/test-report/nonblank-lines.awk"
: "$repo_dir/libexec/test-report/relative-paths.awk"
: "$repo_dir/libexec/test-report/statement-coverage-counts.awk"
: "$repo_dir/libexec/test-report/statement-line-count.awk"
printf '%s\n' active | awk -f "$repo_dir/libexec/proton-vpn/example.awk"
TEST

cat > "$fake_repo/src/test/unit/applescript/display-notification.unit.test.bats" <<'TEST'
#!/usr/bin/env bash
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
osacompile -o /tmp/nix-darwin-applescript-test.scpt "$repo_dir/libexec/brave-youtube/display-notification.applescript"
TEST

cat > "$fake_repo/src/test/int/applescript/notification.int.test.bats" <<'TEST'
#!/usr/bin/env bash
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
osacompile -o /tmp/nix-darwin-applescript-test.scpt "$repo_dir/libexec/brave-youtube/display-notification.applescript"
TEST

cat > "$fake_repo/src/test/unit/applescript/compile-fixture.unit.test.applescript" <<'TEST'
display notification "unit fixture" with title "nix-darwin"
TEST

cat > "$fake_repo/src/test/unit/applescript/compile-extra.unit.test.applescript" <<'TEST'
display notification "unit extra fixture" with title "nix-darwin"
TEST

cat > "$fake_repo/src/test/int/applescript/compile-fixture.int.test.applescript" <<'TEST'
display notification "integration fixture" with title "nix-darwin"
TEST

cat > "$fake_repo/src/test/int/applescript/compile-extra.int.test.applescript" <<'TEST'
display notification "integration extra fixture" with title "nix-darwin"
TEST

cat > "$fake_repo/src/test/unit/zsh/bootstrap-macos.unit.zunit" <<'TEST'
#!/usr/bin/env zunit
# nix-darwin-test-source-target: bin/covered-zsh-tool
# nix-darwin-test-source-target: tools/bootstrap/bootstrap-macos
@test 'unit zsh lane' {
  source "$testdir/../../../../bin/covered-zsh-tool"
  source "$testdir/../../../../tools/bootstrap/bootstrap-macos"
  assert "$covered_zsh_status" same_as "ready"
  assert "$(bootstrap_status)" same_as "ready"
}
TEST

cat > "$fake_repo/src/test/unit/zsh/codex-restore.unit.zunit" <<'TEST'
#!/usr/bin/env zunit
# nix-darwin-test-source-target: bin/covered-zsh-tool
# nix-darwin-test-source-target: tools/bootstrap/bootstrap-macos
@test 'second unit zsh lane' {
  source "$testdir/../../../../bin/covered-zsh-tool"
  source "$testdir/../../../../tools/bootstrap/bootstrap-macos"
  assert "$covered_zsh_status" same_as "ready"
  assert "$(bootstrap_status)" same_as "ready"
}
TEST

cat > "$fake_repo/src/test/int/zsh/bootstrap-macos.int.zunit" <<'TEST'
#!/usr/bin/env zunit
# nix-darwin-test-source-target: bin/covered-zsh-tool
# nix-darwin-test-source-target: tools/bootstrap/bootstrap-macos
@test 'integration zsh lane' {
  source "$testdir/../../../../bin/covered-zsh-tool"
  source "$testdir/../../../../tools/bootstrap/bootstrap-macos"
  assert "$covered_zsh_status" same_as "ready"
  assert "$(bootstrap_status)" same_as "ready"
}
TEST

cat > "$fake_repo/src/test/int/zsh/codex-restore.int.zunit" <<'TEST'
#!/usr/bin/env zunit
# nix-darwin-test-source-target: bin/covered-zsh-tool
# nix-darwin-test-source-target: tools/bootstrap/bootstrap-macos
@test 'second integration zsh lane' {
  source "$testdir/../../../../bin/covered-zsh-tool"
  source "$testdir/../../../../tools/bootstrap/bootstrap-macos"
  assert "$covered_zsh_status" same_as "ready"
  assert "$(bootstrap_status)" same_as "ready"
}
TEST

cat > "$fake_repo/src/test/unit/ruby/vagrantfile.unit.test.rb" <<'TEST'
#!/usr/bin/env ruby
puts "unit ruby"
TEST

cat > "$fake_repo/src/test/unit/ruby/arq_list_wildcard_excludes.unit.test.rb" <<'TEST'
#!/usr/bin/env ruby
puts "unit arq ruby"
TEST

cat > "$fake_repo/src/test/int/ruby/arq_and_vagrant.int.test.rb" <<'TEST'
#!/usr/bin/env ruby
# nix-darwin-test-source-target: bin/covered-ruby-tool
puts "integration ruby"
TEST

cat > "$fake_repo/src/test/int/ruby/vagrant.int.test.rb" <<'TEST'
#!/usr/bin/env ruby
puts "integration vagrant ruby"
TEST

cat > "$fake_repo/src/test/unit/nix/config.unit.test.nix" <<'TEST'
# nix-darwin-test-source-target: flake.nix
# nix-darwin-test-source-target: modules/foo.nix
  {
    testUnitNixLane = {
      expr = true;
      expected = true;
    };
  }
TEST

cat > "$fake_repo/src/test/unit/nix/extra.unit.test.nix" <<'TEST'
# nix-darwin-test-source-target: flake.nix
# nix-darwin-test-source-target: modules/foo.nix
  {
    testExtraUnitNixLane = {
      expr = true;
      expected = true;
    };
  }
TEST

cat > "$fake_repo/src/test/int/nix/config.int.test.nix" <<'TEST'
# nix-darwin-test-source-target: modules/bar.nix
  {
    testIntegrationNixLane = {
      expr = true;
      expected = true;
    };
  }
TEST

cat > "$fake_repo/src/test/int/nix/extra.int.test.nix" <<'TEST'
# nix-darwin-test-source-target: modules/bar.nix
  {
    testExtraIntegrationNixLane = {
      expr = true;
      expected = true;
    };
  }
TEST

cat > "$fake_repo/scripts/test-all.sh" <<'TEST'
#!/usr/bin/env bash
echo recursive-runner
TEST

cat > "$fake_repo/shell/bashrc" <<'TEST'
echo fake bashrc
TEST
cat > "$fake_repo/shell/zshrc" <<'TEST'
echo fake zshrc
TEST
cat > "$fake_repo/shell/sh_config/bash/example" <<'TEST'
echo fake bash fragment
TEST
cat > "$fake_repo/shell/sh_config/zsh/example" <<'TEST'
echo fake zsh fragment
TEST
cat > "$fake_repo/shell/sh_config/common/example" <<'TEST'
echo fake common fragment
TEST

write_fake_command() {
  local command_name="$1"
  cat > "$fake_bin/$command_name" <<'FAKE'
#!/bin/bash
printf '%s\n' "$(basename "$0") $*" >> "$TEST_ALL_RUN_LOG"
case "$(basename "$0")" in
  bash)
    if [ "${1:-}" = "-n" ]; then
      if [ "${TEST_ALL_FAKE_SHELL_SYNTAX_FAILURE:-0}" = "1" ] \
        && [ "${2:-}" = "$TEST_ALL_FAKE_REPO/scripts/test-alpha.sh" ]; then
        printf 'TEST_REPORT_SHELL_SYNTAX_FAILURE_TOKEN\n' >&2
        exit 72
      fi
      exit 0
    fi
    exec /bin/bash "$@"
    ;;
  python3)
    printf 'python3-pycache-prefix %s\n' "${PYTHONPYCACHEPREFIX:-}" >> "$TEST_ALL_RUN_LOG"
    if [ "${1:-}" = "-c" ]; then
      exit 0
    fi
	    case "${1:-}" in
	      */libexec/test-report/statement_coverage.py)
	        while [ "$#" -gt 0 ]; do
	          case "$1" in
	            --html-file)
	              shift
	              html_file="$1"
	              ;;
	          esac
	          shift
	        done
	        mkdir -p "$(dirname "$html_file")"
	        printf '<html>statement coverage</html>\n' > "$html_file"
	        exit 0
	        ;;
	      */tools/test/report/render.py)
	        summary_only=0
	        render_only=0
	        while [ "$#" -gt 0 ]; do
          case "$1" in
            --report-dir)
              shift
              report_dir="$1"
              ;;
            --summary-file)
              shift
              summary_file="$1"
              ;;
            --summary-only)
              summary_only=1
              ;;
            --render-only)
              render_only=1
              ;;
          esac
          shift
        done
        if [ "$render_only" -eq 1 ]; then
          summary_file=""
        fi
        mkdir -p "$report_dir"
        bats_html=""
        for scope in unit int; do
          for language in bash awk applescript; do
            bats_count="$(find "$report_dir/$scope/$language" -type f -name report.xml 2>/dev/null | wc -l | tr -d ' ')"
            if [ "$bats_count" -gt 0 ]; then
              bats_html="$bats_html $scope $language"
              if [ -n "${summary_file:-}" ]; then
                printf '%s\t%s\ttests.total\t%s\n' "$scope" "$language" "$bats_count" >> "$summary_file"
                printf '%s\t%s\ttests.passed\t%s\n' "$scope" "$language" "$bats_count" >> "$summary_file"
                printf '%s\t%s\ttests.failed\t0\n' "$scope" "$language" >> "$summary_file"
                printf '%s\t%s\ttests.skipped\t0\n' "$scope" "$language" >> "$summary_file"
              fi
            fi
          done
          zsh_count="$(find "$report_dir/$scope/zsh" -type f -name output.tap 2>/dev/null | wc -l | tr -d ' ')"
          if [ "$zsh_count" -gt 0 ]; then
            bats_html="$bats_html $scope zsh"
            if [ -n "${summary_file:-}" ]; then
              printf '%s\tzsh\ttests.total\t%s\n' "$scope" "$zsh_count" >> "$summary_file"
              printf '%s\tzsh\ttests.passed\t%s\n' "$scope" "$zsh_count" >> "$summary_file"
              printf '%s\tzsh\ttests.failed\t0\n' "$scope" >> "$summary_file"
              printf '%s\tzsh\ttests.skipped\t0\n' "$scope" >> "$summary_file"
            fi
          fi
          nix_count="$(find "$report_dir/$scope/nix" -type f -name output.txt 2>/dev/null | wc -l | tr -d ' ')"
          if [ "$nix_count" -gt 0 ]; then
            bats_html="$bats_html $scope nix"
            if [ -n "${summary_file:-}" ]; then
              printf '%s\tnix\ttests.total\t%s\n' "$scope" "$nix_count" >> "$summary_file"
              printf '%s\tnix\ttests.passed\t%s\n' "$scope" "$nix_count" >> "$summary_file"
              printf '%s\tnix\ttests.failed\t0\n' "$scope" >> "$summary_file"
              printf '%s\tnix\ttests.skipped\t0\n' "$scope" >> "$summary_file"
            fi
          fi
        done
        if [ "${TEST_ALL_FAKE_GATE_FAILURES:-0}" = "1" ] && [ -n "${summary_file:-}" ]; then
          printf 'all\tpython\tcoverage.gate.failures\t1\n' >> "$summary_file"
          printf 'all\tpython\tcoverage.gate.warnings\t0\n' >> "$summary_file"
          printf 'all\tpython\tcoverage.gate.hard.statement\tfail 1 / 2 (50.00%%) threshold 85%%\n' >> "$summary_file"
        fi
        if [ "$summary_only" -eq 1 ]; then
          exit 0
        fi
        runner_html=""
        failure_html=""
        runner_status="UNKNOWN"
        runner_steps="$report_dir/runner/steps.tsv"
        runner_execution="$report_dir/runner/execution.tsv"
        if [ -f "$runner_execution" ]; then
          runner_status="$(sed -n '2s/\t.*//p' "$runner_execution")"
          case "$runner_status" in
            pass) runner_status="PASS" ;;
            fail) runner_status="FAIL" ;;
            *) runner_status="UNKNOWN" ;;
          esac
        fi
        if [ -f "$report_dir/runner/failures.tsv" ]; then
          while IFS="$(printf '\t')" read -r sequence label exit_code; do
            [ "$sequence" = "sequence" ] && continue
            failure_html="$failure_html $label $exit_code"
          done < "$report_dir/runner/failures.tsv"
        fi
        if [ -f "$runner_steps" ]; then
          while IFS="$(printf '\t')" read -r ordinal label status exit_code output_log; do
            [ "$ordinal" = "ordinal" ] && continue
            runner_html="$runner_html $label $status $exit_code $output_log"
          done < "$runner_steps"
        fi
        printf '{"summary":{"total":2,"passed":2,"failed":0,"skipped":0},"suites":[]}\n' > "$report_dir/test-results.json"
        printf '<html><body>Overall run status: %s Runner / qualification steps%s Failed execution lanes / qualification steps%s Parsed tests unit python unit ruby coverage%s</body></html>\n' "$runner_status" "$runner_html" "$failure_html" "$bats_html" > "$report_dir/index.html"
        exit 0
        ;;
    esac
    exit 0
    ;;
  git)
    if [ "${TEST_ALL_FAKE_PATCH_HYGIENE_FAILURE:-0}" = "1" ]; then
      printf 'TEST_REPORT_PATCH_HYGIENE_FAILURE_TOKEN\n' >&2
      exit 71
    fi
    exit 0
    ;;
  nix)
    if [ "${1:-}" = "flake" ] && [ "${2:-}" = "check" ] \
      && [ "${TEST_ALL_FAKE_FLAKE_CHECK_FAILURE:-0}" = "1" ]; then
      printf 'TEST_REPORT_FLAKE_CHECK_FAILURE_TOKEN\n' >&2
      exit 73
    fi
    if [ "${1:-}" = "build" ] \
      && [ "${TEST_ALL_FAKE_DARWIN_BUILD_FAILURE:-0}" = "1" ]; then
      printf 'TEST_REPORT_DARWIN_BUILD_FAILURE_TOKEN\n' >&2
      exit 74
    fi
    exit 0
    ;;
  pytest)
    printf 'pytest-pycache-prefix %s\n' "${PYTHONPYCACHEPREFIX:-}" >> "$TEST_ALL_RUN_LOG"
    should_fail=0
    for arg in "$@"; do
      if [ -e "$arg/fail.unit.test.py" ]; then
        should_fail=1
      fi
    done
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --cov-report=term-missing)
          printf 'TOTAL 10 1 90%%\n'
          ;;
        --cov-report=xml:*)
          path="${1#--cov-report=xml:}"
          mkdir -p "$(dirname "$path")"
          printf '<coverage line-rate="0.9" branch-rate="0.55"></coverage>\n' > "$path"
          ;;
        --cov-report=html:*)
          path="${1#--cov-report=html:}"
          mkdir -p "$path"
          printf '<html>coverage</html>\n' > "$path/index.html"
          ;;
        --junitxml=*)
          path="${1#--junitxml=}"
          mkdir -p "$(dirname "$path")"
          if [ "$should_fail" -eq 1 ]; then
            printf '<testsuite tests="1" failures="1"><testcase classname="python.fail" name="test_fail"><failure message="boom">python traceback</failure><system-out>python stdout</system-out></testcase></testsuite>\n' > "$path"
          else
            printf '<testsuite tests="1" failures="0"><testcase classname="python.pass" name="test_pass"><system-out>python stdout</system-out></testcase></testsuite>\n' > "$path"
          fi
          ;;
        --json-report)
          ;;
        --json-report-file=*)
          path="${1#--json-report-file=}"
          mkdir -p "$(dirname "$path")"
          if [ "$should_fail" -eq 1 ]; then
            printf '{"tests":[{"nodeid":"src/test/unit/python/fail.unit.test.py::test_fail","lineno":0,"outcome":"failed","setup":{"duration":0.001,"outcome":"passed"},"call":{"duration":0.002,"outcome":"failed","stdout":"python stdout\\n","crash":{"message":"boom"},"longrepr":"python traceback"},"teardown":{"duration":0.001,"outcome":"passed"}}]}\n' > "$path"
          else
            printf '{"tests":[{"nodeid":"src/test/unit/python/pass.unit.test.py::test_pass","lineno":0,"outcome":"passed","setup":{"duration":0.001,"outcome":"passed"},"call":{"duration":0.002,"outcome":"passed","stdout":"python stdout\\n"},"teardown":{"duration":0.001,"outcome":"passed"}}]}\n' > "$path"
          fi
          ;;
        --cov=*)
          ;;
      esac
      shift
    done
    if [ "$should_fail" -eq 1 ]; then
      printf 'python fake failure\n' >&2
      exit 7
    fi
    exit 0
    ;;
  ruby)
    printf 'ruby runner transcript\n'
    coverage_dir="${NIX_DARWIN_TEST_COVERAGE_DIR:-}"
    if [ -n "$coverage_dir" ]; then
      mkdir -p "$coverage_dir"
      printf '{"result":{"line":88.8,"branch":66.7}}\n' > "$coverage_dir/.last_run.json"
      printf '<html>coverage</html>\n' > "$coverage_dir/index.html"
    fi
    result_json="${NIX_DARWIN_TEST_RESULT_JSON:-}"
    if [ -n "$result_json" ]; then
      mkdir -p "$(dirname "$result_json")"
      printf '{"scope":"unit","language":"ruby","tests":[{"classname":"FakeRubyTest","name":"ruby_pass","status":"passed","stdout":"ruby stdout","stderr":"","failures":[]}]}\n' > "$result_json"
    fi
    if [ "${TEST_ALL_FAKE_RUBY_FILE_PARALLEL_PROBE:-0}" = "1" ] && [ -z "$result_json" ]; then
      probe_scope="${TEST_ALL_FAKE_RUBY_FILE_PARALLEL_SCOPE:-unit}"
      marker=""
      other_marker=""
      for arg in "$@"; do
        case "$arg" in
          */src/test/unit/ruby/arq_list_wildcard_excludes.unit.test.rb)
            if [ "$probe_scope" = "unit" ]; then
              marker="unit-ruby-arq"
              other_marker="unit-ruby-vagrant"
            fi
            ;;
          */src/test/unit/ruby/vagrantfile.unit.test.rb)
            if [ "$probe_scope" = "unit" ]; then
              marker="unit-ruby-vagrant"
              other_marker="unit-ruby-arq"
            fi
            ;;
          */src/test/int/ruby/arq_and_vagrant.int.test.rb)
            if [ "$probe_scope" = "int" ]; then
              marker="int-ruby-arq"
              other_marker="int-ruby-vagrant"
            fi
            ;;
          */src/test/int/ruby/vagrant.int.test.rb)
            if [ "$probe_scope" = "int" ]; then
              marker="int-ruby-vagrant"
              other_marker="int-ruby-arq"
            fi
            ;;
        esac
      done
      if [ -n "$marker" ]; then
        mkdir -p "$TEST_ALL_FAKE_RUBY_FILE_PARALLEL_DIR"
        : > "$TEST_ALL_FAKE_RUBY_FILE_PARALLEL_DIR/$marker.started"
        for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
          [ -e "$TEST_ALL_FAKE_RUBY_FILE_PARALLEL_DIR/$other_marker.started" ] && break
          sleep 0.1
        done
        if [ ! -e "$TEST_ALL_FAKE_RUBY_FILE_PARALLEL_DIR/$other_marker.started" ]; then
          printf 'ruby file parallel probe timed out waiting for %s\n' "$other_marker" >&2
          exit 64
        fi
        : > "$TEST_ALL_FAKE_RUBY_FILE_PARALLEL_DIR/$marker.finished"
      fi
    fi
    for arg in "$@"; do
      case "$arg" in
        *fail_ruby.unit.test.rb)
          if [ -n "$result_json" ]; then
            printf '{"scope":"unit","language":"ruby","tests":[{"classname":"FakeRubyTest","name":"ruby_fail","status":"failed","stdout":"ruby stdout","stderr":"ruby stderr","failures":[{"class":"RuntimeError","message":"ruby fail","backtrace":["fake_ruby_test.rb:1"]}]}]}\n' > "$result_json"
          fi
          printf 'ruby fake failure\n' >&2
          exit 8
          ;;
      esac
    done
    exit 0
    ;;
  zunit)
    output_text=0
    verbose=0
    test_path=""
    should_fail=0
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --tap)
          ;;
        --verbose)
          verbose=1
          ;;
        --output-text)
          output_text=1
          ;;
        *)
          test_path="$1"
          case "$test_path" in
            *fail.unit.zunit) should_fail=1 ;;
          esac
          ;;
      esac
      shift
    done
    if [ "${TEST_ALL_FAKE_ZUNIT_FILE_PARALLEL_PROBE:-0}" = "1" ]; then
      probe_scope="${TEST_ALL_FAKE_ZUNIT_FILE_PARALLEL_SCOPE:-unit}"
      marker=""
      other_marker=""
      case "$test_path" in
        */src/test/unit/zsh/bootstrap-macos.unit.zunit)
          if [ "$probe_scope" = "unit" ]; then
            marker="unit-zsh-bootstrap"
            other_marker="unit-zsh-codex"
          fi
          ;;
        */src/test/unit/zsh/codex-restore.unit.zunit)
          if [ "$probe_scope" = "unit" ]; then
            marker="unit-zsh-codex"
            other_marker="unit-zsh-bootstrap"
          fi
          ;;
        */src/test/int/zsh/bootstrap-macos.int.zunit)
          if [ "$probe_scope" = "int" ]; then
            marker="int-zsh-bootstrap"
            other_marker="int-zsh-codex"
          fi
          ;;
        */src/test/int/zsh/codex-restore.int.zunit)
          if [ "$probe_scope" = "int" ]; then
            marker="int-zsh-codex"
            other_marker="int-zsh-bootstrap"
          fi
          ;;
      esac
      if [ -n "$marker" ]; then
        mkdir -p "$TEST_ALL_FAKE_ZUNIT_FILE_PARALLEL_DIR"
        : > "$TEST_ALL_FAKE_ZUNIT_FILE_PARALLEL_DIR/$marker.started"
        for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
          [ -e "$TEST_ALL_FAKE_ZUNIT_FILE_PARALLEL_DIR/$other_marker.started" ] && break
          sleep 0.1
        done
        if [ ! -e "$TEST_ALL_FAKE_ZUNIT_FILE_PARALLEL_DIR/$other_marker.started" ]; then
          printf 'zunit file parallel probe timed out waiting for %s\n' "$other_marker" >&2
          exit 64
        fi
        : > "$TEST_ALL_FAKE_ZUNIT_FILE_PARALLEL_DIR/$marker.finished"
      fi
    fi
    if [ "$should_fail" -eq 1 ]; then
      printf 'zunit fake failure\n' >&2
      exit 43
    fi
    printf 'TAP version 13\n'
    if [ "$verbose" -eq 1 ]; then
      printf 'zunit verbose output for %s\n' "$(basename "$test_path")"
    fi
    printf 'ok 1 - %s\n' "$(basename "$test_path")"
    printf '1..1\n'
    if [ "$output_text" -eq 1 ]; then
      printf 'TAP version 13\nok 1 - %s\n1..1\n' "$(basename "$test_path")" > "$(basename "$test_path").tap"
    fi
    exit 0
    ;;
  kcov)
    printf 'kcov-open-files %s\n' "$(ulimit -Sn)" >> "$TEST_ALL_RUN_LOG"
    if [ "${1:-}" = "--merge" ]; then
      shift
      output_dir="$1"
      shift
      mkdir -p "$output_dir/kcov-merged"
      printf '{"percent_covered":"12.50","covered_lines":1,"total_lines":8}\n' > "$output_dir/kcov-merged/coverage.json"
      printf '<coverage line-rate="0.125" branch-rate="1.0"></coverage>\n' > "$output_dir/kcov-merged/cobertura.xml"
      printf '<html>merged bash coverage</html>\n' > "$output_dir/index.html"
      printf '<html>merged bash coverage</html>\n' > "$output_dir/kcov-merged/index.html"
      exit 0
    fi

    output_dir=""
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --*)
          shift
          ;;
        *)
          output_dir="$1"
          shift
          break
          ;;
      esac
    done
    script_path="$1"
    shift
    mkdir -p "$output_dir/$(basename "$script_path").fake"
    printf '{"percent_covered":"12.50","covered_lines":1,"total_lines":8}\n' > "$output_dir/$(basename "$script_path").fake/coverage.json"
    printf '<coverage line-rate="0.125" branch-rate="1.0"></coverage>\n' > "$output_dir/$(basename "$script_path").fake/cobertura.xml"
    printf '<html>bash coverage</html>\n' > "$output_dir/$(basename "$script_path").fake/index.html"
    printf 'kcov: error: 2set -euo pipefail\n is not an integer\n' >&2
    /bin/bash "$script_path" "$@"
    exit "$?"
    ;;
  nix-unit)
    printf 'nix-unit-conf-dir %s\n' "${NIX_CONF_DIR:-}" >> "$TEST_ALL_RUN_LOG"
    printf 'nix-unit-config %s\n' "${NIX_CONFIG:-}" >> "$TEST_ALL_RUN_LOG"
    status=0
    for arg in "$@"; do
      case "$arg" in
        *fail.unit.test.nix)
          printf 'nix-unit fake failure\n' >&2
          exit 44
          ;;
        *.nix)
          if [ "${TEST_ALL_FAKE_NIX_FILE_PARALLEL_PROBE:-0}" = "1" ]; then
            probe_scope="${TEST_ALL_FAKE_NIX_FILE_PARALLEL_SCOPE:-unit}"
            marker=""
            other_marker=""
            case "$arg" in
              */src/test/unit/nix/config.unit.test.nix)
                if [ "$probe_scope" = "unit" ]; then
                  marker="unit-nix-config"
                  other_marker="unit-nix-extra"
                fi
                ;;
              */src/test/unit/nix/extra.unit.test.nix)
                if [ "$probe_scope" = "unit" ]; then
                  marker="unit-nix-extra"
                  other_marker="unit-nix-config"
                fi
                ;;
              */src/test/int/nix/config.int.test.nix)
                if [ "$probe_scope" = "int" ]; then
                  marker="int-nix-config"
                  other_marker="int-nix-extra"
                fi
                ;;
              */src/test/int/nix/extra.int.test.nix)
                if [ "$probe_scope" = "int" ]; then
                  marker="int-nix-extra"
                  other_marker="int-nix-config"
                fi
                ;;
            esac
            if [ -n "$marker" ]; then
              mkdir -p "$TEST_ALL_FAKE_NIX_FILE_PARALLEL_DIR"
              : > "$TEST_ALL_FAKE_NIX_FILE_PARALLEL_DIR/$marker.started"
              for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
                [ -e "$TEST_ALL_FAKE_NIX_FILE_PARALLEL_DIR/$other_marker.started" ] && break
                sleep 0.1
              done
              if [ ! -e "$TEST_ALL_FAKE_NIX_FILE_PARALLEL_DIR/$other_marker.started" ]; then
                printf 'nix file parallel probe timed out waiting for %s\n' "$other_marker" >&2
                exit 64
              fi
              : > "$TEST_ALL_FAKE_NIX_FILE_PARALLEL_DIR/$marker.finished"
            fi
          fi
          printf '✅ testFakeNixLane\n'
          printf '🎉 1/1 successful\n'
          ;;
      esac
    done
    exit "$status"
    ;;
  osacompile)
    output_path=""
    test_path=""
    while [ "$#" -gt 0 ]; do
      case "$1" in
        -o)
          shift
          output_path="$1"
          ;;
        *)
          test_path="$1"
          ;;
      esac
      shift
    done
    case "$test_path" in
      *fail.unit.test.applescript)
        printf 'osacompile fake failure\n' >&2
        exit 45
        ;;
    esac
    if [ "${TEST_ALL_FAKE_APPLESCRIPT_FILE_PARALLEL_PROBE:-0}" = "1" ]; then
      probe_scope="${TEST_ALL_FAKE_APPLESCRIPT_FILE_PARALLEL_SCOPE:-unit}"
      marker=""
      other_marker=""
      case "$test_path" in
        */src/test/unit/applescript/compile-fixture.unit.test.applescript)
          if [ "$probe_scope" = "unit" ]; then
            marker="unit-applescript-fixture"
            other_marker="unit-applescript-extra"
          fi
          ;;
        */src/test/unit/applescript/compile-extra.unit.test.applescript)
          if [ "$probe_scope" = "unit" ]; then
            marker="unit-applescript-extra"
            other_marker="unit-applescript-fixture"
          fi
          ;;
        */src/test/int/applescript/compile-fixture.int.test.applescript)
          if [ "$probe_scope" = "int" ]; then
            marker="int-applescript-fixture"
            other_marker="int-applescript-extra"
          fi
          ;;
        */src/test/int/applescript/compile-extra.int.test.applescript)
          if [ "$probe_scope" = "int" ]; then
            marker="int-applescript-extra"
            other_marker="int-applescript-fixture"
          fi
          ;;
      esac
      if [ -n "$marker" ]; then
        mkdir -p "$TEST_ALL_FAKE_APPLESCRIPT_FILE_PARALLEL_DIR"
        : > "$TEST_ALL_FAKE_APPLESCRIPT_FILE_PARALLEL_DIR/$marker.started"
        for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
          [ -e "$TEST_ALL_FAKE_APPLESCRIPT_FILE_PARALLEL_DIR/$other_marker.started" ] && break
          sleep 0.1
        done
        if [ ! -e "$TEST_ALL_FAKE_APPLESCRIPT_FILE_PARALLEL_DIR/$other_marker.started" ]; then
          printf 'AppleScript file parallel probe timed out waiting for %s\n' "$other_marker" >&2
          exit 64
        fi
        : > "$TEST_ALL_FAKE_APPLESCRIPT_FILE_PARALLEL_DIR/$marker.finished"
      fi
    fi
    [ -n "$output_path" ] && : > "$output_path"
    exit 0
    ;;
  bats)
    if [ "${TEST_ALL_FAKE_REQUIRE_BATS_NO_TTY:-0}" = "1" ] && [ -t 0 ]; then
      printf 'bats inherited terminal stdin\n' >&2
      exit 65
    fi
    if [ -n "${NIX_DARWIN_KCOV_REAL_BASH:-}" ]; then
      printf 'bats-real-bash %s\n' "$NIX_DARWIN_KCOV_REAL_BASH" >> "$TEST_ALL_RUN_LOG"
    fi
    output_dir=""
    gather_dir=""
    test_path=""
    should_fail=0
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --report-formatter)
          shift
          ;;
        --output)
          shift
          output_dir="$1"
          ;;
        --gather-test-outputs-in)
          shift
          gather_dir="$1"
          ;;
        *)
          test_path="$1"
          case "$test_path" in
            *fail.unit.test.bats|*fail-first.unit.test.bats) should_fail=1 ;;
          esac
          ;;
      esac
      shift
    done
    if [ -n "$output_dir" ]; then
      mkdir -p "$output_dir"
      test_name="$(basename "$test_path")"
      if [ "$should_fail" -eq 1 ]; then
        printf '<testsuites><testsuite name="%s" tests="1" failures="1" errors="0" skipped="0"><testcase classname="%s" name="failing bats test"><failure message="boom">bats stack</failure><system-out>bats stdout</system-out></testcase></testsuite></testsuites>\n' "$test_name" "$test_name" > "$output_dir/report.xml"
      else
        printf '<testsuites><testsuite name="%s" tests="1" failures="0" errors="0" skipped="0"><testcase classname="%s" name="passing bats test"><system-out>bats stdout</system-out></testcase></testsuite></testsuites>\n' "$test_name" "$test_name" > "$output_dir/report.xml"
      fi
    fi
    if [ -n "$gather_dir" ]; then
      mkdir -p "$gather_dir"
      printf 'gathered bats stdout\n' > "$gather_dir/1-passing bats test.log"
    fi
    if [ "${TEST_ALL_FAKE_REPORT_PARALLEL_PROBE:-0}" = "1" ] && [ -n "$output_dir" ]; then
      marker=""
      other_marker=""
      case "$test_path" in
        */src/test/unit/bash/alpha.unit.test.bats)
          marker="unit-report-bash"
          other_marker="unit-report-awk"
          ;;
        */src/test/unit/awk/parser.unit.test.bats)
          marker="unit-report-awk"
          other_marker="unit-report-bash"
          ;;
        */src/test/int/bash/runtime.int.test.bats)
          marker="int-report-bash"
          other_marker="int-report-awk"
          ;;
        */src/test/int/awk/parser.int.test.bats)
          marker="int-report-awk"
          other_marker="int-report-bash"
          ;;
      esac
      if [ -n "$marker" ]; then
        mkdir -p "$TEST_ALL_FAKE_REPORT_PARALLEL_DIR"
        : > "$TEST_ALL_FAKE_REPORT_PARALLEL_DIR/$marker.started"
        for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
          [ -e "$TEST_ALL_FAKE_REPORT_PARALLEL_DIR/$other_marker.started" ] && break
          sleep 0.1
        done
        if [ ! -e "$TEST_ALL_FAKE_REPORT_PARALLEL_DIR/$other_marker.started" ]; then
          printf 'report lane parallel probe timed out waiting for %s\n' "$other_marker" >&2
          exit 64
        fi
        : > "$TEST_ALL_FAKE_REPORT_PARALLEL_DIR/$marker.finished"
      fi
    fi
    if [ -n "${TEST_ALL_FAKE_REPO:-}" ]; then
      case "$test_path" in
        */src/test/unit/bash/*|*/src/test/int/bash/*)
          bash "$TEST_ALL_FAKE_REPO/bin/covered-bash-tool"
          ;;
      esac
    fi
    if [ "${TEST_ALL_FAKE_PARALLEL_PROBE:-0}" = "1" ] && [ -z "$output_dir" ]; then
      probe_scope="${TEST_ALL_FAKE_PARALLEL_SCOPE:-unit}"
      marker=""
      other_marker=""
      case "$test_path" in
        */src/test/unit/bash/alpha.unit.test.bats)
          if [ "$probe_scope" = "unit" ]; then
            marker="bash"
            other_marker="awk"
          fi
          ;;
        */src/test/unit/awk/parser.unit.test.bats)
          if [ "$probe_scope" = "unit" ]; then
            marker="awk"
            other_marker="bash"
          fi
          ;;
        */src/test/int/bash/runtime.int.test.bats)
          if [ "$probe_scope" = "int" ]; then
            marker="int-bash"
            other_marker="int-awk"
          fi
          ;;
        */src/test/int/awk/parser.int.test.bats)
          if [ "$probe_scope" = "int" ]; then
            marker="int-awk"
            other_marker="int-bash"
          fi
          ;;
      esac
      if [ -n "$marker" ]; then
        mkdir -p "$TEST_ALL_FAKE_PARALLEL_DIR"
        : > "$TEST_ALL_FAKE_PARALLEL_DIR/$marker.started"
        for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
          [ -e "$TEST_ALL_FAKE_PARALLEL_DIR/$other_marker.started" ] && break
          sleep 0.1
        done
        if [ ! -e "$TEST_ALL_FAKE_PARALLEL_DIR/$other_marker.started" ]; then
          printf 'parallel probe timed out waiting for %s\n' "$other_marker" >&2
          exit 64
        fi
        : > "$TEST_ALL_FAKE_PARALLEL_DIR/$marker.finished"
      fi
    fi
    if [ "${TEST_ALL_FAKE_BATS_FILE_PARALLEL_PROBE:-0}" = "1" ] && [ -z "$output_dir" ]; then
      probe_scope="${TEST_ALL_FAKE_BATS_FILE_PARALLEL_SCOPE:-unit}"
      marker=""
      other_marker=""
      case "$test_path" in
        */src/test/unit/bash/alpha.unit.test.bats)
          if [ "$probe_scope" = "unit" ]; then
            marker="unit-bash-alpha"
            other_marker="unit-bash-beta"
          fi
          ;;
        */src/test/unit/bash/beta.unit.test.bats)
          if [ "$probe_scope" = "unit" ]; then
            marker="unit-bash-beta"
            other_marker="unit-bash-alpha"
          fi
          ;;
        */src/test/int/bash/runtime.int.test.bats)
          if [ "$probe_scope" = "int" ]; then
            marker="int-bash-runtime"
            other_marker="int-bash-runtime-extra"
          fi
          ;;
        */src/test/int/bash/runtime-extra.int.test.bats)
          if [ "$probe_scope" = "int" ]; then
            marker="int-bash-runtime-extra"
            other_marker="int-bash-runtime"
          fi
          ;;
      esac
      if [ -n "$marker" ]; then
        mkdir -p "$TEST_ALL_FAKE_BATS_FILE_PARALLEL_DIR"
        : > "$TEST_ALL_FAKE_BATS_FILE_PARALLEL_DIR/$marker.started"
        for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
          [ -e "$TEST_ALL_FAKE_BATS_FILE_PARALLEL_DIR/$other_marker.started" ] && break
          sleep 0.1
        done
        if [ ! -e "$TEST_ALL_FAKE_BATS_FILE_PARALLEL_DIR/$other_marker.started" ]; then
          printf 'Bats file parallel probe timed out waiting for %s\n' "$other_marker" >&2
          exit 64
        fi
        : > "$TEST_ALL_FAKE_BATS_FILE_PARALLEL_DIR/$marker.finished"
      fi
    fi
    if [ "$should_fail" -eq 1 ]; then
      printf 'bats fake failure\n' >&2
      exit 42
    fi
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
FAKE
  chmod +x "$fake_bin/$command_name"
}

for fake_command in git nix nix-unit bash zsh zunit python3 bats pytest ruby osacompile kcov; do
  write_fake_command "$fake_command"
done

run_runner() {
  HOME="$fake_home" \
  PYTHONPYCACHEPREFIX= \
  TEST_ALL_RUN_LOG="$run_log" \
    TEST_ALL_FAKE_REPO="$fake_repo" \
    NIX_DARWIN_TEST_ROOT="$fake_repo" \
    NIX_DARWIN_TEST_REPORT_ROOT="$report_dir" \
    PATH="$fake_bin:$PATH" \
    /bin/bash "$test_runner" "$@"
}

run_runner_captured() {
  local output_file="$1"

  shift
  run_captured_status "$output_file" env \
    HOME="$fake_home" \
    PYTHONPYCACHEPREFIX= \
    TEST_ALL_RUN_LOG="$run_log" \
    TEST_ALL_FAKE_GATE_FAILURES="${TEST_ALL_FAKE_GATE_FAILURES:-}" \
    TEST_ALL_FAKE_FLAKE_CHECK_FAILURE="${TEST_ALL_FAKE_FLAKE_CHECK_FAILURE:-}" \
    TEST_ALL_FAKE_DARWIN_BUILD_FAILURE="${TEST_ALL_FAKE_DARWIN_BUILD_FAILURE:-}" \
    TEST_ALL_FAKE_PATCH_HYGIENE_FAILURE="${TEST_ALL_FAKE_PATCH_HYGIENE_FAILURE:-}" \
    TEST_ALL_FAKE_SHELL_SYNTAX_FAILURE="${TEST_ALL_FAKE_SHELL_SYNTAX_FAILURE:-}" \
    NIX_DARWIN_SKIP_COVERAGE_GATES="${NIX_DARWIN_SKIP_COVERAGE_GATES:-}" \
    TEST_ALL_FAKE_REPO="$fake_repo" \
    NIX_DARWIN_TEST_ROOT="$fake_repo" \
    NIX_DARWIN_TEST_REPORT_ROOT="$report_dir" \
    PATH="$fake_bin:$PATH" \
    /bin/bash "$test_runner" "$@"
}

run_runner_without_path_pytest() {
  HOME="$fake_home" \
  PYTHONPYCACHEPREFIX= \
  TEST_ALL_RUN_LOG="$run_log" \
    TEST_ALL_FAKE_REPO="$fake_repo" \
    NIX_DARWIN_TEST_ROOT="$fake_repo" \
    NIX_DARWIN_TEST_REPORT_ROOT="$report_dir" \
    PATH="$fake_bin:/usr/bin:/bin" \
    /bin/bash "$test_runner" "$@"
}

run_runner_without_path_nix_unit() {
  HOME="$fake_home" \
  PYTHONPYCACHEPREFIX= \
  TEST_ALL_RUN_LOG="$run_log" \
    TEST_ALL_FAKE_REPO="$fake_repo" \
    NIX_DARWIN_TEST_ROOT="$fake_repo" \
    NIX_DARWIN_TEST_REPORT_ROOT="$report_dir" \
    PATH="$fake_bin:/usr/bin:/bin" \
    /bin/bash "$test_runner" "$@"
}

run_runner_without_path_bats() {
  HOME="$fake_home" \
  PYTHONPYCACHEPREFIX= \
  TEST_ALL_RUN_LOG="$run_log" \
    TEST_ALL_FAKE_REPO="$fake_repo" \
    NIX_DARWIN_TEST_ROOT="$fake_repo" \
    NIX_DARWIN_TEST_REPORT_ROOT="$report_dir" \
    PATH="$fake_bin:/usr/bin:/bin" \
    /bin/bash "$test_runner" "$@"
}

run_runner_without_path_zunit() {
  HOME="$fake_home" \
  PYTHONPYCACHEPREFIX= \
  TEST_ALL_RUN_LOG="$run_log" \
    TEST_ALL_FAKE_REPO="$fake_repo" \
    NIX_DARWIN_TEST_ROOT="$fake_repo" \
    NIX_DARWIN_TEST_REPORT_ROOT="$report_dir" \
    PATH="$fake_bin:/usr/bin:/bin" \
    /bin/bash "$test_runner" "$@"
}

run_wrapper() {
  local wrapper="$1"
  shift
  HOME="$fake_home" \
  PYTHONPYCACHEPREFIX= \
  TEST_ALL_RUN_LOG="$run_log" \
    NIX_DARWIN_TEST_ROOT="$fake_repo" \
    NIX_DARWIN_TEST_REPORT_ROOT="$report_dir" \
    PATH="$fake_bin:$PATH" \
    /bin/bash "$wrapper" "$@"
}

run_test_all() {
  TEST_ALL_RUN_LOG="$run_log" \
    NIX_DARWIN_TEST_ROOT="$fake_repo" \
    NIX_DARWIN_TEST_REPORT_ROOT="$report_dir" \
    PATH="$fake_bin:$PATH" \
    /bin/bash "$test_all" "$@"
}

assert_applescript_compile_logged() {
  local test_file="$1"
  local description="$2"

  if ! grep -F "osacompile -o " "$run_log" | grep -F " $test_file" >/dev/null; then
    fail "$description"
  fi

  if grep -Fqx "osacompile -o /tmp/nix-darwin-applescript-test.scpt $test_file" "$run_log"; then
    fail "AppleScript compile checks must not use the shared /tmp output path"
  fi
}

help_output="$(run_runner --help)"
case "$help_output" in
  *"--full"* ) ;;
  * ) fail "--help must document --full" ;;
esac

TEST_ALL_FAKE_REQUIRE_BATS_NO_TTY=1
export TEST_ALL_FAKE_REQUIRE_BATS_NO_TTY
if ! run_runner unit >/dev/null; then
  fail "Bats test lanes must not inherit terminal stdin"
fi
unset TEST_ALL_FAKE_REQUIRE_BATS_NO_TTY
case "$help_output" in
  *"--report"* ) ;;
  * ) fail "--help must document --report" ;;
esac
case "$help_output" in
  *"-j, --jobs N"* ) ;;
  * ) fail "--help must document -j/--jobs" ;;
esac
case "$help_output" in
  *"NIX_DARWIN_TEST_JOBS"* ) ;;
  * ) fail "--help must document NIX_DARWIN_TEST_JOBS" ;;
esac
case "$help_output" in
  *"NIX_DARWIN_BUILD_JOBS"* ) ;;
  * ) fail "--help must document NIX_DARWIN_BUILD_JOBS" ;;
esac
case "$help_output" in
  *"--jobs 1"* ) ;;
  * ) fail "--help must document the sequential --jobs 1 baseline" ;;
esac
case "$help_output" in
  *"unit"* ) ;;
  * ) fail "--help must document unit test mode" ;;
esac
case "$help_output" in
  *"int"* ) ;;
  * ) fail "--help must document integration test mode" ;;
esac

: > "$run_log"
run_runner unit --jobs 2
grep -Fqx "bats $fake_repo/src/test/unit/bash/alpha.unit.test.bats" "$run_log" \
  || fail "--jobs 2 must still run unit bash Bats tests"
grep -Fqx "pytest -n 2 $fake_repo/src/test/unit/python" "$run_log" \
  || fail "--jobs 2 must pass the bounded job count to pytest-xdist"
if grep -Fqx "bats $fake_repo/src/test/int/bash/runtime.int.test.bats" "$run_log"; then
  fail "--jobs 2 must not run integration lanes for unit mode"
fi

: > "$run_log"
run_runner unit -j 2
grep -Fqx "bats $fake_repo/src/test/unit/bash/alpha.unit.test.bats" "$run_log" \
  || fail "-j 2 must still run unit bash Bats tests"

: > "$run_log"
NIX_DARWIN_TEST_JOBS=2 run_runner unit
grep -Fqx "bats $fake_repo/src/test/unit/bash/alpha.unit.test.bats" "$run_log" \
  || fail "NIX_DARWIN_TEST_JOBS=2 must still run unit bash Bats tests"

: > "$run_log"
NIX_DARWIN_BUILD_JOBS=4 run_runner int --jobs 2
grep -Fqx "nix flake check $fake_repo --no-update-lock-file --no-write-lock-file --max-jobs 4" "$run_log" \
  || fail "int mode must pass NIX_DARWIN_BUILD_JOBS to nix flake check"
grep -Fqx "pytest -n 2 $fake_repo/src/test/int/python" "$run_log" \
  || fail "test jobs must remain independent from Nix build jobs"
if grep -Fqx "nix flake check $fake_repo --no-update-lock-file --no-write-lock-file --max-jobs 2" "$run_log"; then
  fail "test --jobs must not set Nix build jobs"
fi

: > "$run_log"
NIX_DARWIN_BUILD_JOBS=4 run_runner all --jobs 2
grep -Fqx "nix flake check $fake_repo --no-update-lock-file --no-write-lock-file --max-jobs 4" "$run_log" \
  || fail "all mode must pass NIX_DARWIN_BUILD_JOBS to nix flake check"

: > "$run_log"
NIX_DARWIN_BUILD_JOBS=4 run_runner all --full --jobs 2
grep -Fqx "nix build --no-link --no-update-lock-file --no-write-lock-file $fake_repo#darwinConfigurations.example-primary.system --max-jobs 4" "$run_log" \
  || fail "--full must pass NIX_DARWIN_BUILD_JOBS to the Darwin system build"

parallel_probe_dir="$test_tmp_root/parallel-probe"
mkdir -p "$parallel_probe_dir"
: > "$run_log"
TEST_ALL_FAKE_PARALLEL_PROBE=1 \
  TEST_ALL_FAKE_PARALLEL_DIR="$parallel_probe_dir" \
  run_runner unit --jobs 2
[ -f "$parallel_probe_dir/bash.finished" ] \
  || fail "--jobs 2 must run the unit bash lane through the parallel helper"
[ -f "$parallel_probe_dir/awk.finished" ] \
  || fail "--jobs 2 must run the unit awk lane through the parallel helper"
grep -Fqx "pytest -n 2 $fake_repo/src/test/unit/python" "$run_log" \
  || fail "--jobs 2 parallel unit mode must run Python through pytest-xdist"
if grep -Fqx "bats $fake_repo/src/test/int/bash/runtime.int.test.bats" "$run_log"; then
  fail "--jobs 2 parallel unit mode must not run integration lanes"
fi

cat > "$fake_repo/src/test/unit/python/fail.unit.test.py" <<'TEST'
def test_fail():
    assert False
TEST
parallel_failure_output="$test_tmp_root/parallel-failure.out"
if run_runner_captured "$parallel_failure_output" unit --jobs 2; then
  fail "parallel unit runs must fail when a child lane fails"
fi
rm "$fake_repo/src/test/unit/python/fail.unit.test.py"
grep -Fq "nix-darwin-test: unit/python/pytest failed" "$parallel_failure_output" \
  || fail "parallel unit failures must report the failing lane label"
grep -Fq "python fake failure" "$parallel_failure_output" \
  || fail "parallel unit failures must print the captured failing lane log"

parallel_int_probe_dir="$test_tmp_root/parallel-int-probe"
mkdir -p "$parallel_int_probe_dir"
: > "$run_log"
TEST_ALL_FAKE_PARALLEL_PROBE=1 \
  TEST_ALL_FAKE_PARALLEL_SCOPE=int \
  TEST_ALL_FAKE_PARALLEL_DIR="$parallel_int_probe_dir" \
  run_runner int --jobs 2
[ -f "$parallel_int_probe_dir/int-bash.finished" ] \
  || fail "--jobs 2 must run the integration bash lane through the parallel helper"
[ -f "$parallel_int_probe_dir/int-awk.finished" ] \
  || fail "--jobs 2 must run the integration awk lane through the parallel helper"
grep -Fqx "pytest -n 2 $fake_repo/src/test/int/python" "$run_log" \
  || fail "--jobs 2 parallel int mode must run integration Python through pytest-xdist"
if grep -Fqx "bats $fake_repo/src/test/unit/bash/alpha.unit.test.bats" "$run_log"; then
  fail "--jobs 2 parallel int mode must not run unit lanes"
fi

parallel_all_int_probe_dir="$test_tmp_root/parallel-all-int-probe"
mkdir -p "$parallel_all_int_probe_dir"
: > "$run_log"
TEST_ALL_FAKE_PARALLEL_PROBE=1 \
  TEST_ALL_FAKE_PARALLEL_SCOPE=int \
  TEST_ALL_FAKE_PARALLEL_DIR="$parallel_all_int_probe_dir" \
  run_runner all --jobs 2
[ -f "$parallel_all_int_probe_dir/int-bash.finished" ] \
  || fail "all --jobs 2 must run the integration bash lane through the parallel helper"
[ -f "$parallel_all_int_probe_dir/int-awk.finished" ] \
  || fail "all --jobs 2 must run the integration awk lane through the parallel helper"
grep -Fqx "bats $fake_repo/src/test/unit/bash/alpha.unit.test.bats" "$run_log" \
  || fail "all --jobs 2 must still run unit bash Bats tests"
grep -Fqx "pytest -n 2 $fake_repo/src/test/int/python" "$run_log" \
  || fail "all --jobs 2 must run integration Python through pytest-xdist"

bats_unit_file_probe_dir="$test_tmp_root/bats-unit-file-probe"
mkdir -p "$bats_unit_file_probe_dir"
: > "$run_log"
TEST_ALL_FAKE_BATS_FILE_PARALLEL_PROBE=1 \
  TEST_ALL_FAKE_BATS_FILE_PARALLEL_SCOPE=unit \
  TEST_ALL_FAKE_BATS_FILE_PARALLEL_DIR="$bats_unit_file_probe_dir" \
  run_runner unit --jobs 2
[ -f "$bats_unit_file_probe_dir/unit-bash-alpha.finished" ] \
  || fail "unit --jobs 2 must run unit bash alpha through file-level parallelism"
[ -f "$bats_unit_file_probe_dir/unit-bash-beta.finished" ] \
  || fail "unit --jobs 2 must run unit bash beta through file-level parallelism"
grep -Fqx "bats $fake_repo/src/test/unit/bash/beta.unit.test.bats" "$run_log" \
  || fail "unit --jobs 2 file-level parallel mode must still run unit bash beta"

bats_int_file_probe_dir="$test_tmp_root/bats-int-file-probe"
mkdir -p "$bats_int_file_probe_dir"
: > "$run_log"
TEST_ALL_FAKE_BATS_FILE_PARALLEL_PROBE=1 \
  TEST_ALL_FAKE_BATS_FILE_PARALLEL_SCOPE=int \
  TEST_ALL_FAKE_BATS_FILE_PARALLEL_DIR="$bats_int_file_probe_dir" \
  run_runner int --jobs 2
[ -f "$bats_int_file_probe_dir/int-bash-runtime.finished" ] \
  || fail "int --jobs 2 must run integration bash runtime through file-level parallelism"
[ -f "$bats_int_file_probe_dir/int-bash-runtime-extra.finished" ] \
  || fail "int --jobs 2 must run integration bash extra through file-level parallelism"
grep -Fqx "bats $fake_repo/src/test/int/bash/runtime-extra.int.test.bats" "$run_log" \
  || fail "int --jobs 2 file-level parallel mode must still run integration bash extra"

cat > "$fake_repo/src/test/unit/bash/fail.unit.test.bats" <<'TEST'
#!/usr/bin/env bash
echo failing
TEST
chmod +x "$fake_repo/src/test/unit/bash/fail.unit.test.bats"
bats_file_failure_output="$test_tmp_root/bats-file-failure.out"
if run_runner_captured "$bats_file_failure_output" unit --jobs 2; then
  fail "parallel Bats file runs must fail when a child test file fails"
fi
rm "$fake_repo/src/test/unit/bash/fail.unit.test.bats"
grep -Fq "nix-darwin-test: unit/bash/fail.unit.test.bats failed" "$bats_file_failure_output" \
  || fail "parallel Bats file failures must report the failing test file label"
grep -Fq "bats fake failure" "$bats_file_failure_output" \
  || fail "parallel Bats file failures must print the captured failing file log"

zunit_unit_file_probe_dir="$test_tmp_root/zunit-unit-file-probe"
mkdir -p "$zunit_unit_file_probe_dir"
: > "$run_log"
TEST_ALL_FAKE_ZUNIT_FILE_PARALLEL_PROBE=1 \
  TEST_ALL_FAKE_ZUNIT_FILE_PARALLEL_SCOPE=unit \
  TEST_ALL_FAKE_ZUNIT_FILE_PARALLEL_DIR="$zunit_unit_file_probe_dir" \
  run_runner unit --jobs 2
[ -f "$zunit_unit_file_probe_dir/unit-zsh-bootstrap.finished" ] \
  || fail "unit --jobs 2 must run unit zsh bootstrap through file-level parallelism"
[ -f "$zunit_unit_file_probe_dir/unit-zsh-codex.finished" ] \
  || fail "unit --jobs 2 must run unit zsh codex through file-level parallelism"
grep -Fqx "zunit --tap $fake_repo/src/test/unit/zsh/codex-restore.unit.zunit" "$run_log" \
  || fail "unit --jobs 2 file-level parallel mode must still run unit zsh codex"

zunit_int_file_probe_dir="$test_tmp_root/zunit-int-file-probe"
mkdir -p "$zunit_int_file_probe_dir"
: > "$run_log"
TEST_ALL_FAKE_ZUNIT_FILE_PARALLEL_PROBE=1 \
  TEST_ALL_FAKE_ZUNIT_FILE_PARALLEL_SCOPE=int \
  TEST_ALL_FAKE_ZUNIT_FILE_PARALLEL_DIR="$zunit_int_file_probe_dir" \
  run_runner int --jobs 2
[ -f "$zunit_int_file_probe_dir/int-zsh-bootstrap.finished" ] \
  || fail "int --jobs 2 must run integration zsh bootstrap through file-level parallelism"
[ -f "$zunit_int_file_probe_dir/int-zsh-codex.finished" ] \
  || fail "int --jobs 2 must run integration zsh codex through file-level parallelism"
grep -Fqx "zunit --tap $fake_repo/src/test/int/zsh/codex-restore.int.zunit" "$run_log" \
  || fail "int --jobs 2 file-level parallel mode must still run integration zsh codex"

cat > "$fake_repo/src/test/unit/zsh/fail.unit.zunit" <<'TEST'
#!/usr/bin/env zunit
@test 'failing zunit file' {
  assert "no" same_as "yes"
}
TEST
zunit_file_failure_output="$test_tmp_root/zunit-file-failure.out"
if run_runner_captured "$zunit_file_failure_output" unit --jobs 2; then
  fail "parallel zunit file runs must fail when a child test file fails"
fi
rm "$fake_repo/src/test/unit/zsh/fail.unit.zunit"
grep -Fq "nix-darwin-test: unit/zsh/fail.unit.zunit failed" "$zunit_file_failure_output" \
  || fail "parallel zunit file failures must report the failing test file label"
grep -Fq "zunit fake failure" "$zunit_file_failure_output" \
  || fail "parallel zunit file failures must print the captured failing file log"

nix_unit_file_probe_dir="$test_tmp_root/nix-unit-file-probe"
mkdir -p "$nix_unit_file_probe_dir"
: > "$run_log"
TEST_ALL_FAKE_NIX_FILE_PARALLEL_PROBE=1 \
  TEST_ALL_FAKE_NIX_FILE_PARALLEL_SCOPE=unit \
  TEST_ALL_FAKE_NIX_FILE_PARALLEL_DIR="$nix_unit_file_probe_dir" \
  run_runner unit --jobs 2
[ -f "$nix_unit_file_probe_dir/unit-nix-config.finished" ] \
  || fail "unit --jobs 2 must run unit nix config through file-level parallelism"
[ -f "$nix_unit_file_probe_dir/unit-nix-extra.finished" ] \
  || fail "unit --jobs 2 must run unit nix extra through file-level parallelism"
grep -Eq "^nix-unit --gc-roots-dir $nix_unit_gc_roots_regex --arg repoRoot $fake_repo --argstr system aarch64-darwin $fake_repo/src/test/unit/nix/extra\\.unit\\.test\\.nix$" "$run_log" \
  || fail "unit --jobs 2 file-level parallel mode must still run unit nix extra"

nix_int_file_probe_dir="$test_tmp_root/nix-int-file-probe"
mkdir -p "$nix_int_file_probe_dir"
: > "$run_log"
TEST_ALL_FAKE_NIX_FILE_PARALLEL_PROBE=1 \
  TEST_ALL_FAKE_NIX_FILE_PARALLEL_SCOPE=int \
  TEST_ALL_FAKE_NIX_FILE_PARALLEL_DIR="$nix_int_file_probe_dir" \
  run_runner int --jobs 2
[ -f "$nix_int_file_probe_dir/int-nix-config.finished" ] \
  || fail "int --jobs 2 must run integration nix config through file-level parallelism"
[ -f "$nix_int_file_probe_dir/int-nix-extra.finished" ] \
  || fail "int --jobs 2 must run integration nix extra through file-level parallelism"
grep -Eq "^nix-unit --gc-roots-dir $nix_unit_gc_roots_regex --arg repoRoot $fake_repo --argstr system aarch64-darwin $fake_repo/src/test/int/nix/extra\\.int\\.test\\.nix$" "$run_log" \
  || fail "int --jobs 2 file-level parallel mode must still run integration nix extra"

cat > "$fake_repo/src/test/unit/nix/fail.unit.test.nix" <<'TEST'
{
  testFailingNixFile = {
    expr = false;
    expected = true;
  };
}
TEST
nix_file_failure_output="$test_tmp_root/nix-file-failure.out"
if run_runner_captured "$nix_file_failure_output" unit --jobs 2; then
  fail "parallel Nix file runs must fail when a child test file fails"
fi
rm "$fake_repo/src/test/unit/nix/fail.unit.test.nix"
grep -Fq "nix-darwin-test: unit/nix/fail.unit.test.nix failed" "$nix_file_failure_output" \
  || fail "parallel Nix file failures must report the failing test file label"
grep -Fq "nix-unit fake failure" "$nix_file_failure_output" \
  || fail "parallel Nix file failures must print the captured failing file log"

applescript_unit_file_probe_dir="$test_tmp_root/applescript-unit-file-probe"
mkdir -p "$applescript_unit_file_probe_dir"
: > "$run_log"
TEST_ALL_FAKE_APPLESCRIPT_FILE_PARALLEL_PROBE=1 \
  TEST_ALL_FAKE_APPLESCRIPT_FILE_PARALLEL_SCOPE=unit \
  TEST_ALL_FAKE_APPLESCRIPT_FILE_PARALLEL_DIR="$applescript_unit_file_probe_dir" \
  run_runner unit --jobs 2
[ -f "$applescript_unit_file_probe_dir/unit-applescript-fixture.finished" ] \
  || fail "unit --jobs 2 must compile unit AppleScript fixture through file-level parallelism"
[ -f "$applescript_unit_file_probe_dir/unit-applescript-extra.finished" ] \
  || fail "unit --jobs 2 must compile unit AppleScript extra through file-level parallelism"
assert_applescript_compile_logged \
  "$fake_repo/src/test/unit/applescript/compile-extra.unit.test.applescript" \
  "unit --jobs 2 file-level parallel mode must still compile unit AppleScript extra"

applescript_int_file_probe_dir="$test_tmp_root/applescript-int-file-probe"
mkdir -p "$applescript_int_file_probe_dir"
: > "$run_log"
TEST_ALL_FAKE_APPLESCRIPT_FILE_PARALLEL_PROBE=1 \
  TEST_ALL_FAKE_APPLESCRIPT_FILE_PARALLEL_SCOPE=int \
  TEST_ALL_FAKE_APPLESCRIPT_FILE_PARALLEL_DIR="$applescript_int_file_probe_dir" \
  run_runner int --jobs 2
[ -f "$applescript_int_file_probe_dir/int-applescript-fixture.finished" ] \
  || fail "int --jobs 2 must compile integration AppleScript fixture through file-level parallelism"
[ -f "$applescript_int_file_probe_dir/int-applescript-extra.finished" ] \
  || fail "int --jobs 2 must compile integration AppleScript extra through file-level parallelism"
assert_applescript_compile_logged \
  "$fake_repo/src/test/int/applescript/compile-extra.int.test.applescript" \
  "int --jobs 2 file-level parallel mode must still compile integration AppleScript extra"

cat > "$fake_repo/src/test/unit/applescript/fail.unit.test.applescript" <<'TEST'
display notification "fail" with title "nix-darwin"
TEST
applescript_file_failure_output="$test_tmp_root/applescript-file-failure.out"
if run_runner_captured "$applescript_file_failure_output" unit --jobs 2; then
  fail "parallel AppleScript compile runs must fail when a child test file fails"
fi
rm "$fake_repo/src/test/unit/applescript/fail.unit.test.applescript"
grep -Fq "nix-darwin-test: unit/applescript/fail.unit.test.applescript failed" "$applescript_file_failure_output" \
  || fail "parallel AppleScript compile failures must report the failing test file label"
grep -Fq "osacompile fake failure" "$applescript_file_failure_output" \
  || fail "parallel AppleScript compile failures must print the captured failing file log"

ruby_unit_file_probe_dir="$test_tmp_root/ruby-unit-file-probe"
mkdir -p "$ruby_unit_file_probe_dir"
: > "$run_log"
TEST_ALL_FAKE_RUBY_FILE_PARALLEL_PROBE=1 \
  TEST_ALL_FAKE_RUBY_FILE_PARALLEL_SCOPE=unit \
  TEST_ALL_FAKE_RUBY_FILE_PARALLEL_DIR="$ruby_unit_file_probe_dir" \
  run_runner unit --jobs 2
[ -f "$ruby_unit_file_probe_dir/unit-ruby-arq.finished" ] \
  || fail "unit --jobs 2 must run unit Ruby arq through file-level parallelism"
[ -f "$ruby_unit_file_probe_dir/unit-ruby-vagrant.finished" ] \
  || fail "unit --jobs 2 must run unit Ruby vagrant through file-level parallelism"
grep -Fqx "ruby $fake_repo/src/test/unit/ruby/arq_list_wildcard_excludes.unit.test.rb" "$run_log" \
  || fail "unit --jobs 2 file-level parallel mode must still run unit Ruby arq"

ruby_int_file_probe_dir="$test_tmp_root/ruby-int-file-probe"
mkdir -p "$ruby_int_file_probe_dir"
: > "$run_log"
TEST_ALL_FAKE_RUBY_FILE_PARALLEL_PROBE=1 \
  TEST_ALL_FAKE_RUBY_FILE_PARALLEL_SCOPE=int \
  TEST_ALL_FAKE_RUBY_FILE_PARALLEL_DIR="$ruby_int_file_probe_dir" \
  run_runner int --jobs 2
[ -f "$ruby_int_file_probe_dir/int-ruby-arq.finished" ] \
  || fail "int --jobs 2 must run integration Ruby arq through file-level parallelism"
[ -f "$ruby_int_file_probe_dir/int-ruby-vagrant.finished" ] \
  || fail "int --jobs 2 must run integration Ruby vagrant through file-level parallelism"
grep -Fqx "ruby $fake_repo/src/test/int/ruby/vagrant.int.test.rb" "$run_log" \
  || fail "int --jobs 2 file-level parallel mode must still run integration Ruby vagrant"

cat > "$fake_repo/src/test/unit/ruby/fail_ruby.unit.test.rb" <<'TEST'
#!/usr/bin/env ruby
raise "ruby fail"
TEST
ruby_file_failure_output="$test_tmp_root/ruby-file-failure.out"
if run_runner_captured "$ruby_file_failure_output" unit --jobs 2; then
  fail "parallel Ruby file runs must fail when a child test file fails"
fi
rm "$fake_repo/src/test/unit/ruby/fail_ruby.unit.test.rb"
grep -Fq "nix-darwin-test: unit/ruby/fail_ruby.unit.test.rb failed" "$ruby_file_failure_output" \
  || fail "parallel Ruby file failures must report the failing test file label"
grep -Fq "ruby fake failure" "$ruby_file_failure_output" \
  || fail "parallel Ruby file failures must print the captured failing file log"

: > "$run_log"
run_runner all

grep -Fqx "git --no-pager -C $fake_repo diff --check" "$run_log" \
  || fail "default run must include non-paged git diff --check"
grep -Fqx "nix flake check $fake_repo --no-update-lock-file --no-write-lock-file" "$run_log" \
  || fail "default run must include nix flake check"
if grep -Fq "nix flake check $fake_repo --no-update-lock-file --no-write-lock-file --max-jobs" "$run_log"; then
  fail "default run must preserve flake check arguments when build jobs are unset"
fi
grep -Fqx "zsh -n $fake_repo/tools/bootstrap/bootstrap-macos" "$run_log" \
  || fail "default run must syntax-check zsh bootstrap tools"
grep -Fqx "bats $fake_repo/src/test/unit/bash/alpha.unit.test.bats" "$run_log" \
  || fail "default run must execute unit bash Bats tests"
grep -Fqx "bats $fake_repo/src/test/int/bash/runtime.int.test.bats" "$run_log" \
  || fail "default run must execute integration bash Bats tests"
if grep -Fqx "bash $fake_repo/src/test/unit/bash/legacy.unit.test.sh" "$run_log"; then
  fail "default run must not execute legacy bash .sh tests"
fi
if grep -Fqx "bash $fake_repo/scripts/test-alpha.sh" "$run_log"; then
  fail "default run must not execute legacy shell tests from scripts"
fi
if grep -Fqx "python3 $fake_repo/scripts/test-beta.py" "$run_log"; then
  fail "default run must not execute legacy Python tests from scripts"
fi
grep -Fqx "pytest $fake_repo/src/test/unit/python" "$run_log" \
  || fail "all mode must run unit Python pytest tests"
grep -Fqx "pytest $fake_repo/src/test/int/python" "$run_log" \
  || fail "all mode must run integration Python pytest tests"
grep -Fqx "pytest-pycache-prefix $fake_python_cache" "$run_log" \
  || fail "pytest runs must default Python bytecode to ~/.cache/python/__pycache__"
grep -Fqx "bats $fake_repo/src/test/unit/awk/parser.unit.test.bats" "$run_log" \
  || fail "all mode must run unit awk bats tests"
grep -Fqx "bats $fake_repo/src/test/int/awk/parser.int.test.bats" "$run_log" \
  || fail "all mode must run integration awk bats tests"
grep -Fqx "bats $fake_repo/src/test/unit/applescript/display-notification.unit.test.bats" "$run_log" \
  || fail "all mode must run unit AppleScript Bats tests"
grep -Fqx "bats $fake_repo/src/test/int/applescript/notification.int.test.bats" "$run_log" \
  || fail "all mode must run integration AppleScript Bats tests"
assert_applescript_compile_logged \
  "$fake_repo/src/test/unit/applescript/compile-fixture.unit.test.applescript" \
  "all mode must compile unit AppleScript test fixtures"
assert_applescript_compile_logged \
  "$fake_repo/src/test/int/applescript/compile-fixture.int.test.applescript" \
  "all mode must compile integration AppleScript test fixtures"
grep -Fqx "zunit --tap $fake_repo/src/test/unit/zsh/bootstrap-macos.unit.zunit" "$run_log" \
  || fail "all mode must run unit zsh zunit tests"
grep -Fqx "zunit --tap $fake_repo/src/test/int/zsh/bootstrap-macos.int.zunit" "$run_log" \
  || fail "all mode must run integration zsh zunit tests"
grep -Fqx "ruby $fake_repo/src/test/unit/ruby/vagrantfile.unit.test.rb" "$run_log" \
  || fail "all mode must run unit Ruby tests"
grep -Fqx "ruby $fake_repo/src/test/int/ruby/arq_and_vagrant.int.test.rb" "$run_log" \
  || fail "all mode must run integration Ruby tests"
grep -Fqx "ruby $fake_repo/src/test/int/ruby/vagrant.int.test.rb" "$run_log" \
  || fail "all mode must run extra integration Ruby tests"
grep -Eq "^nix-unit --gc-roots-dir $nix_unit_gc_roots_regex --arg repoRoot $fake_repo --argstr system aarch64-darwin $fake_repo/src/test/unit/nix/config\\.unit\\.test\\.nix$" "$run_log" \
  || fail "all mode must run unit Nix tests"
grep -Eq "^nix-unit --gc-roots-dir $nix_unit_gc_roots_regex --arg repoRoot $fake_repo --argstr system aarch64-darwin $fake_repo/src/test/int/nix/config\\.int\\.test\\.nix$" "$run_log" \
  || fail "all mode must run integration Nix tests"
if grep -Fqx "bash $fake_repo/scripts/test-all.sh" "$run_log"; then
  fail "runner must not execute itself recursively"
fi
if grep -Fq "darwinConfigurations.example-primary.system" "$run_log"; then
  fail "default run must not perform the full Darwin system build"
fi

set +e
all_report_output="$(run_runner all --report)"
all_report_status="$?"
set -e
[ "$all_report_status" -eq 0 ] \
  || fail "reported all-mode run failed before Bash interpreter assertions: $all_report_output"
grep -Fq "Overall run status: PASS" "$report_dir/all/latest/index.html" \
  || fail "a complete successful reported run must render overall PASS"
[ "$(sed -n '1p' "$report_dir/all/latest/unit/bash/kcov-wrapper/bash")" = "#!$fake_bin/bash" ] \
  || fail "reported Bash coverage must render the kcov wrapper with managed Bash"
grep -Fqx "bats-real-bash $fake_bin/bash" "$run_log" \
  || fail "reported Bash coverage must retain the managed Bash resolved before the kcov wrapper"
if grep -Fqx 'bats-real-bash /bin/bash' "$run_log"; then
  fail "reported Bash coverage must not fall back to the macOS Bash 3.2 runtime"
fi
grep -F -- "--bash-parser=$fake_bin/bash" "$run_log" >/dev/null \
  || fail "reported Bash coverage must pass the managed Bash path to kcov"
grep -Fqx 'kcov-open-files 1024' "$run_log" \
  || fail "reported Bash coverage must cap Darwin's file-descriptor limit for kcov"
if grep -F -- '--debug-force-bash-stderr' "$run_log" >/dev/null; then
  fail "reported Bash coverage must preserve command stderr instead of using it for xtrace"
fi
case "$all_report_output" in
  *"Test Report Summary"* ) ;;
  * ) fail "reported runs must print a test report summary" ;;
esac
case "$all_report_output" in
  *"TOTAL 10 1 90%"* )
    fail "reported runs must keep raw Python runner output in report artifacts, not terminal output"
    ;;
esac
case "$all_report_output" in
  *"covered-bash-tool"* )
    fail "reported runs must keep raw Bats runner output in report artifacts, not terminal output"
    ;;
esac
case "$all_report_output" in
  *"ruby runner transcript"* )
    fail "reported runs must keep raw Ruby runner output in report artifacts, not terminal output"
    ;;
esac
case "$all_report_output" in
  *"TAP version 13"* )
    fail "reported runs must keep raw zunit TAP output in report artifacts, not terminal output"
    ;;
esac
case "$all_report_output" in
  *"testFakeNixLane"* )
    fail "reported runs must keep raw nix-unit output in report artifacts, not terminal output"
    ;;
esac
grep -Fqx "python3-pycache-prefix $fake_python_cache" "$run_log" \
  || fail "reported runs must default report-renderer bytecode to ~/.cache/python/__pycache__"
rg -F -q '"--cov=$repo_dir/tools/test/report"' "$test_runner" \
  || fail "reported Python coverage must include tools/test/report renderer source"
case "$all_report_output" in
  *"unit python statement coverage: 90%"* ) ;;
  * ) fail "reported runs must summarize unit Python statement coverage" ;;
esac
case "$all_report_output" in
  *"unit python branch coverage: 55%"* ) ;;
  * ) fail "reported runs must summarize unit Python branch coverage" ;;
esac
[ -f "$report_dir/all/latest/summary.d/unit-python.tsv" ] \
  || fail "--report must write unit Python coverage metrics through a summary fragment"
grep -Fqx $'unit\tpython\tstatement coverage\t90%' "$report_dir/all/latest/summary.d/unit-python.tsv" \
  || fail "--report must record unit Python statement coverage in the summary fragment"
case "$all_report_output" in
  *"unit python source-target coverage: 1 / 1 (100%)"* ) ;;
  * ) fail "reported runs must summarize unit Python source-target coverage" ;;
esac
case "$all_report_output" in
  *"unit ruby statement coverage: 88.8%"* ) ;;
  * ) fail "reported runs must summarize unit Ruby statement coverage" ;;
esac
case "$all_report_output" in
  *"unit ruby branch coverage: 66.7%"* ) ;;
  * ) fail "reported runs must summarize unit Ruby branch coverage" ;;
esac
[ -f "$report_dir/all/latest/summary.d/unit-ruby.tsv" ] \
  || fail "--report must write unit Ruby coverage metrics through a summary fragment"
grep -Fqx $'unit\truby\tstatement coverage\t88.8%' "$report_dir/all/latest/summary.d/unit-ruby.tsv" \
  || fail "--report must record unit Ruby statement coverage in the summary fragment"
case "$all_report_output" in
  *"integration ruby source-target coverage: 1 / 1 (100%)"* ) ;;
  * ) fail "reported all-mode runs must summarize integration Ruby source-target coverage separately" ;;
esac
case "$all_report_output" in
  *"all ruby source-target coverage: 1 / 1 (100%)"* ) ;;
  * ) fail "reported all-mode runs must summarize aggregate Ruby source-target coverage" ;;
esac
case "$all_report_output" in
  *"unit bash statement coverage: 12.50%"* ) ;;
  * ) fail "reported runs must summarize unit Bash statement coverage" ;;
esac
case "$all_report_output" in
  *"integration bash statement coverage: 12.50%"* ) ;;
  * ) fail "reported runs must summarize integration Bash statement coverage" ;;
esac
case "$all_report_output" in
  *"all bash statement coverage: 12.50%"* ) ;;
  * ) fail "reported runs must summarize aggregate Bash statement coverage" ;;
esac
case "$all_report_output" in
  *"unit bash source-target coverage: 1 / 1 (100%)"* ) ;;
  * ) fail "reported all-mode runs must summarize unit Bash source-target coverage" ;;
esac
case "$all_report_output" in
  *"integration bash source-target coverage: 1 / 1 (100%)"* ) ;;
  * ) fail "reported all-mode runs must summarize integration Bash source-target coverage" ;;
esac
case "$all_report_output" in
  *"all bash source-target coverage: 1 / 1 (100%)"* ) ;;
  * ) fail "reported all-mode runs must summarize aggregate Bash source-target coverage" ;;
esac
case "$all_report_output" in
  *"unit awk statement coverage:"*"(100%)"* ) ;;
  * ) fail "reported runs must summarize unit awk statement coverage" ;;
esac
case "$all_report_output" in
  *"integration awk statement coverage:"*"(100%)"* ) ;;
  * ) fail "reported runs must summarize integration awk statement coverage" ;;
esac
case "$all_report_output" in
  *"all awk statement coverage:"*"(100%)"* ) ;;
  * ) fail "reported runs must summarize aggregate awk statement coverage" ;;
esac
case "$all_report_output" in
  *"unit applescript statement coverage: 2 / 2 (100%)"* ) ;;
  * ) fail "reported runs must summarize unit AppleScript statement coverage" ;;
esac
case "$all_report_output" in
  *"integration applescript statement coverage: 2 / 2 (100%)"* ) ;;
  * ) fail "reported runs must summarize integration AppleScript statement coverage" ;;
esac
case "$all_report_output" in
  *"all applescript statement coverage: 2 / 2 (100%)"* ) ;;
  * ) fail "reported runs must summarize aggregate AppleScript statement coverage" ;;
esac
case "$all_report_output" in
  *"unit zsh statement coverage:"*"(100%)"* ) ;;
  * ) fail "reported runs must summarize unit zsh statement coverage" ;;
esac
case "$all_report_output" in
  *"integration zsh statement coverage:"*"(100%)"* ) ;;
  * ) fail "reported runs must summarize integration zsh statement coverage" ;;
esac
case "$all_report_output" in
  *"all zsh statement coverage:"*"(100%)"* ) ;;
  * ) fail "reported runs must summarize aggregate zsh statement coverage" ;;
esac
case "$all_report_output" in
  *"zsh branch coverage"* )
    fail "reported runs must not summarize zsh branch coverage without a trustworthy zsh branch tracer"
    ;;
esac
case "$all_report_output" in
  *"bash branch coverage"* )
    fail "reported runs must not summarize kcov Bash branch coverage because kcov reports a non-actionable shell branch-rate"
    ;;
esac
case "$all_report_output" in
  *"unit nix source-target coverage: 2 / 4 (50%)"* ) ;;
  * ) fail "reported runs must summarize unit Nix source-target coverage" ;;
esac
case "$all_report_output" in
  *"integration python source-target coverage: 1 / 1 (100%)"* ) ;;
  * ) fail "reported all-mode runs must summarize integration Python source-target coverage separately" ;;
esac
case "$all_report_output" in
  *"all python source-target coverage: 1 / 1 (100%)"* ) ;;
  * ) fail "reported all-mode runs must summarize aggregate Python source-target coverage" ;;
esac
case "$all_report_output" in
  *"integration nix source-target coverage: 1 / 4 (25%)"* ) ;;
  * ) fail "reported runs must summarize integration Nix source-target coverage" ;;
esac
case "$all_report_output" in
  *"all nix source-target coverage: 3 / 4 (75%)"* ) ;;
  * ) fail "reported all-mode runs must summarize aggregate Nix source-target coverage" ;;
esac
case "$all_report_output" in
  *"unit bash tests.total: 2"* ) ;;
  * ) fail "reported all-mode runs must summarize unit bash Bats totals" ;;
esac
case "$all_report_output" in
  *"unit awk tests.total: 1"* ) ;;
  * ) fail "reported all-mode runs must summarize unit awk Bats totals" ;;
esac
case "$all_report_output" in
  *"unit applescript tests.total: 1"* ) ;;
  * ) fail "reported all-mode runs must summarize unit AppleScript Bats totals" ;;
esac
case "$all_report_output" in
  *"integration bash tests.total: 2"* ) ;;
  * ) fail "reported all-mode runs must summarize integration bash Bats totals" ;;
esac
case "$all_report_output" in
  *"unit zsh tests.total: 2"* ) ;;
  * ) fail "reported all-mode runs must summarize unit zsh zunit totals" ;;
esac
case "$all_report_output" in
  *"integration zsh tests.total: 2"* ) ;;
  * ) fail "reported all-mode runs must summarize integration zsh zunit totals" ;;
esac
case "$all_report_output" in
  *"unit nix tests.total: 2"* ) ;;
  * ) fail "reported all-mode runs must summarize unit Nix totals" ;;
esac
case "$all_report_output" in
  *"integration nix tests.total: 2"* ) ;;
  * ) fail "reported all-mode runs must summarize integration Nix totals" ;;
esac
[ -f "$report_dir/all/latest/unit/python/junit.xml" ] \
  || fail "--report must write unit Python JUnit XML"
[ -f "$report_dir/all/latest/unit/python/report.json" ] \
  || fail "--report must write unit Python pytest JSON report"
[ -f "$report_dir/all/latest/unit/python/output.txt" ] \
  || fail "--report must write unit Python raw execution output"
grep -Fq 'TOTAL 10 1 90%' "$report_dir/all/latest/unit/python/output.txt" \
  || fail "--report must preserve unit Python raw execution output"
[ -f "$report_dir/all/latest/unit/python/coverage.xml" ] \
  || fail "--report must write unit Python coverage XML"
[ -f "$report_dir/all/latest/unit/bash/alpha.unit.test.bats/report.xml" ] \
  || fail "--report must write unit bash Bats JUnit XML"
[ -f "$report_dir/all/latest/unit/bash/alpha.unit.test.bats/output.txt" ] \
  || fail "--report must write unit bash Bats raw execution output"
grep -Fq 'covered-bash-tool' "$report_dir/all/latest/unit/bash/alpha.unit.test.bats/output.txt" \
  || fail "--report must preserve unit bash Bats raw execution output"
[ -f "$report_dir/all/latest/unit/bash/alpha.unit.test.bats/test-outputs/1-passing bats test.log" ] \
  || fail "--report must gather per-test Bats output logs"
if grep -Fq 'is not an integer' "$report_dir/all/latest/unit/bash/alpha.unit.test.bats/output.txt"; then
  fail "--report must filter noisy kcov Bash parser diagnostics from Bats raw output"
fi
if grep -Fq 'is not an integer' "$report_dir/all/latest/unit/bash/alpha.unit.test.bats/test-outputs/1-passing bats test.log"; then
  fail "--report must filter noisy kcov Bash parser diagnostics from per-test Bats logs"
fi
grep -Fq -- '--show-output-of-passing-tests' "$run_log" \
  || fail "--report must ask Bats to expose passing test output"
grep -Fq -- '--verbose-run' "$run_log" \
  || fail "--report must ask Bats run helpers to print captured output"
grep -Fq -- '--print-output-on-failure' "$run_log" \
  || fail "--report must ask Bats to expose failed run output"
grep -Fq -- '--gather-test-outputs-in' "$run_log" \
  || fail "--report must ask Bats to gather per-test output files"
[ -f "$report_dir/all/latest/int/bash/runtime.int.test.bats/report.xml" ] \
  || fail "--report must write integration bash Bats JUnit XML"
[ -f "$report_dir/all/latest/unit/bash/kcov/kcov-merged/coverage.json" ] \
  || fail "--report must write unit Bash kcov coverage JSON"
[ -f "$report_dir/all/latest/unit/bash/kcov/kcov-merged/cobertura.xml" ] \
  || fail "--report must write unit Bash kcov Cobertura XML"
[ -f "$report_dir/all/latest/unit/bash/kcov/index.html" ] \
  || fail "--report must write unit Bash kcov HTML"
[ -f "$report_dir/all/latest/int/bash/kcov/kcov-merged/coverage.json" ] \
  || fail "--report must write integration Bash kcov coverage JSON"
[ -f "$report_dir/all/latest/all/bash/kcov/kcov-merged/coverage.json" ] \
  || fail "--report must write aggregate Bash kcov coverage JSON"
[ -f "$report_dir/all/latest/unit/bash/source-target-coverage.tsv" ] \
  || fail "--report must write unit Bash source-target coverage details"
[ -f "$report_dir/all/latest/int/bash/source-target-coverage.tsv" ] \
  || fail "--report must write integration Bash source-target coverage details"
[ -f "$report_dir/all/latest/all/bash/source-target-coverage.tsv" ] \
  || fail "--report must write aggregate Bash source-target coverage details"
[ -f "$report_dir/all/latest/unit/awk/statement-coverage.tsv" ] \
  || fail "--report must write unit awk statement coverage details"
[ -f "$report_dir/all/latest/unit/awk/statement-coverage.html" ] \
  || fail "--report must write unit awk statement coverage HTML"
[ -f "$report_dir/all/latest/int/awk/statement-coverage.tsv" ] \
  || fail "--report must write integration awk statement coverage details"
[ -f "$report_dir/all/latest/all/awk/statement-coverage.tsv" ] \
  || fail "--report must write aggregate awk statement coverage details"
[ -f "$report_dir/all/latest/unit/applescript/statement-coverage.tsv" ] \
  || fail "--report must write unit AppleScript statement coverage details"
[ -f "$report_dir/all/latest/unit/applescript/statement-coverage.html" ] \
  || fail "--report must write unit AppleScript statement coverage HTML"
[ -f "$report_dir/all/latest/int/applescript/statement-coverage.tsv" ] \
  || fail "--report must write integration AppleScript statement coverage details"
[ -f "$report_dir/all/latest/all/applescript/statement-coverage.tsv" ] \
  || fail "--report must write aggregate AppleScript statement coverage details"
[ -f "$report_dir/all/latest/unit/awk/parser.unit.test.bats/report.xml" ] \
  || fail "--report must write unit awk Bats JUnit XML"
[ -f "$report_dir/all/latest/int/awk/parser.int.test.bats/report.xml" ] \
  || fail "--report must write integration awk Bats JUnit XML"
[ -f "$report_dir/all/latest/unit/applescript/display-notification.unit.test.bats/report.xml" ] \
  || fail "--report must write unit AppleScript Bats JUnit XML"
[ -f "$report_dir/all/latest/int/applescript/notification.int.test.bats/report.xml" ] \
  || fail "--report must write integration AppleScript Bats JUnit XML"
[ -f "$report_dir/all/latest/unit/zsh/bootstrap-macos.unit.zunit/output.tap" ] \
  || fail "--report must write unit zsh zunit TAP"
grep -Fq 'TAP version 13' "$report_dir/all/latest/unit/zsh/bootstrap-macos.unit.zunit/output.tap" \
  || fail "--report must preserve unit zsh raw TAP output"
grep -Fq 'zunit verbose output for bootstrap-macos.unit.zunit' "$report_dir/all/latest/unit/zsh/bootstrap-macos.unit.zunit/output.tap" \
  || fail "--report must run zunit verbosely so per-test output can be parsed"
grep -Fq "zunit --tap --verbose $fake_repo/src/test/unit/zsh/bootstrap-macos.unit.zunit" "$run_log" \
  || fail "--report must invoke zunit with verbose TAP output"
[ -f "$report_dir/all/latest/int/zsh/bootstrap-macos.int.zunit/output.tap" ] \
  || fail "--report must write integration zsh zunit TAP"
[ -f "$report_dir/all/latest/unit/zsh/statement-coverage.tsv" ] \
  || fail "--report must write unit zsh statement coverage details"
grep -Fq 'bin/covered-zsh-tool' "$report_dir/all/latest/unit/zsh/statement-coverage.tsv" \
  || fail "--report must include zsh-shebang bin commands in zsh statement coverage"
[ -f "$report_dir/all/latest/int/zsh/statement-coverage.tsv" ] \
  || fail "--report must write integration zsh statement coverage details"
[ -f "$report_dir/all/latest/all/zsh/statement-coverage.tsv" ] \
  || fail "--report must write aggregate zsh statement coverage details"
[ -f "$report_dir/all/latest/unit/nix/config.unit.test.nix/output.txt" ] \
  || fail "--report must write unit Nix output"
grep -Fq 'testFakeNixLane' "$report_dir/all/latest/unit/nix/config.unit.test.nix/output.txt" \
  || fail "--report must preserve unit Nix raw execution output"
[ -f "$report_dir/all/latest/int/nix/config.int.test.nix/output.txt" ] \
  || fail "--report must write integration Nix output"
[ -f "$report_dir/all/latest/unit/nix/source-target-coverage.tsv" ] \
  || fail "--report must write unit Nix source-target coverage details"
[ -f "$report_dir/all/latest/int/nix/source-target-coverage.tsv" ] \
  || fail "--report must write integration Nix source-target coverage details"
[ -f "$report_dir/all/latest/all/nix/source-target-coverage.tsv" ] \
  || fail "--report must write aggregate Nix source-target coverage details"
[ -f "$report_dir/all/latest/unit/python/source-target-coverage.tsv" ] \
  || fail "--report must write unit Python source-target coverage details"
[ -f "$report_dir/all/latest/int/python/source-target-coverage.tsv" ] \
  || fail "--report must write integration Python source-target coverage details"
[ -f "$report_dir/all/latest/all/python/source-target-coverage.tsv" ] \
  || fail "--report must write aggregate Python source-target coverage details"
[ -f "$report_dir/all/latest/unit/ruby/.last_run.json" ] \
  || fail "--report must write unit Ruby SimpleCov metadata"
[ -f "$report_dir/all/latest/unit/ruby/output.txt" ] \
  || fail "--report must write unit Ruby raw execution output"
grep -Fq 'ruby runner transcript' "$report_dir/all/latest/unit/ruby/output.txt" \
  || fail "--report must preserve unit Ruby raw execution output"
[ -f "$report_dir/all/latest/int/ruby/results.json" ] \
  || fail "--report must write integration Ruby result JSON"
[ -f "$report_dir/all/latest/int/ruby/output.txt" ] \
  || fail "--report must write integration Ruby raw execution output"
grep -Fq 'ruby runner transcript' "$report_dir/all/latest/int/ruby/output.txt" \
  || fail "--report must preserve integration Ruby raw execution output"
[ -f "$report_dir/all/latest/int/ruby/source-target-coverage.tsv" ] \
  || fail "--report must write integration Ruby source-target coverage details"
[ -f "$report_dir/all/latest/all/ruby/source-target-coverage.tsv" ] \
  || fail "--report must write aggregate Ruby source-target coverage details"
grep -Fq -- '--cov-branch' "$run_log" \
  || fail "--report must request Python branch coverage"
grep -Fq 'SimpleCov.enable_coverage :branch' "$ruby_simplecov_prelude" \
  || fail "Ruby SimpleCov prelude must enable branch coverage"
[ ! -e "$report_dir/all/latest/unit/ruby/simplecov_prelude.rb" ] \
  || fail "--report must load the Ruby SimpleCov prelude from source, not generate source into the report directory"
grep -Fq -- '--bash-parse-files-in-dir=' "$run_log" \
  || fail "--report must configure kcov to parse project Bash source directories"
grep -Fq -- '--include-pattern=' "$run_log" \
  || fail "--report must restrict kcov to discovered Bash source files"
grep -F -- '--include-pattern=' "$run_log" | grep -F "$fake_repo/bin/covered-bash-tool" >/dev/null \
  || fail "--report must include discovered repo Bash source files in kcov"
if grep -F -- '--include-pattern=' "$run_log" | grep -F "$fake_repo/scripts/test-all.sh" >/dev/null; then
  fail "--report must exclude legacy test runner scripts from Bash kcov source coverage"
fi
if grep -F -- '--include-pattern=' "$run_log" | grep -F "$fake_repo/bin/nix-test" >/dev/null; then
  fail "--report must exclude test runner launcher scripts from Bash kcov source coverage"
fi
grep -Fq -- '--merge' "$run_log" \
  || fail "--report must merge per-script kcov Bash coverage runs"
grep -Fqx "ruby -r $ruby_simplecov_prelude $ruby_test_runner --verbose $fake_repo/src/test/unit/ruby/arq_list_wildcard_excludes.unit.test.rb $fake_repo/src/test/unit/ruby/vagrantfile.unit.test.rb" "$run_log" \
  || fail "--report must run Ruby tests verbosely in one SimpleCov process"
grep -Fqx "ruby $ruby_test_runner --verbose $fake_repo/src/test/int/ruby/arq_and_vagrant.int.test.rb $fake_repo/src/test/int/ruby/vagrant.int.test.rb" "$run_log" \
  || fail "--report must run integration Ruby tests verbosely in one result-collecting process"
[ -f "$report_dir/all/latest/summary.tsv" ] \
  || fail "--report must write an aggregate summary"
grep -Fqx $'scope\tlanguage\tmetric\tvalue' "$report_dir/all/latest/summary.tsv" \
  || fail "aggregate summary must include a tabular header"
[ -f "$report_dir/all/latest/test-results.json" ] \
  || fail "--report must write normalized test result JSON"
[ -f "$report_dir/all/latest/index.html" ] \
  || fail "--report must write an HTML test result report"
grep -Fq 'unit python' "$report_dir/all/latest/index.html" \
  || fail "HTML report must include Python unit results"
grep -Fq 'unit ruby' "$report_dir/all/latest/index.html" \
  || fail "HTML report must include Ruby unit results"
grep -Fq 'unit bash' "$report_dir/all/latest/index.html" \
  || fail "HTML report must include unit bash Bats results"
grep -Fq 'unit awk' "$report_dir/all/latest/index.html" \
  || fail "HTML report must include unit awk Bats results"
grep -Fq 'unit applescript' "$report_dir/all/latest/index.html" \
  || fail "HTML report must include unit AppleScript Bats results"
grep -Fq 'unit zsh' "$report_dir/all/latest/index.html" \
  || fail "HTML report must include unit zsh zunit results"
grep -Fq 'int bash' "$report_dir/all/latest/index.html" \
  || fail "HTML report must include integration bash Bats results"
grep -Fq 'int zsh' "$report_dir/all/latest/index.html" \
  || fail "HTML report must include integration zsh zunit results"
grep -Fq 'unit nix' "$report_dir/all/latest/index.html" \
  || fail "HTML report must include unit Nix results"
grep -Fq 'int nix' "$report_dir/all/latest/index.html" \
  || fail "HTML report must include integration Nix results"
grep -Fq 'coverage' "$report_dir/all/latest/index.html" \
  || fail "HTML report must link coverage output"

report_all_parallel_probe_dir="$test_tmp_root/report-all-parallel-probe"
mkdir -p "$report_all_parallel_probe_dir"
: > "$run_log"
TEST_ALL_FAKE_REPORT_PARALLEL_PROBE=1 \
  TEST_ALL_FAKE_REPORT_PARALLEL_DIR="$report_all_parallel_probe_dir" \
  run_runner all --report --jobs 2 >/dev/null
[ -f "$report_all_parallel_probe_dir/unit-report-bash.finished" ] \
  || fail "all --report --jobs 2 must run unit bash report lane through the parallel helper"
[ -f "$report_all_parallel_probe_dir/unit-report-awk.finished" ] \
  || fail "all --report --jobs 2 must run unit awk report lane through the parallel helper"
[ -f "$report_all_parallel_probe_dir/int-report-bash.finished" ] \
  || fail "all --report --jobs 2 must run integration bash report lane through the parallel helper"
[ -f "$report_all_parallel_probe_dir/int-report-awk.finished" ] \
  || fail "all --report --jobs 2 must run integration awk report lane through the parallel helper"

gate_failure_output="$test_tmp_root/gate-failure.out"
TEST_ALL_FAKE_GATE_FAILURES=1
if run_runner_captured "$gate_failure_output" all --report; then
  fail "coverage gate failures must fail reported runs"
fi
TEST_ALL_FAKE_GATE_FAILURES=
grep -Fq "coverage gates failed (1 failure(s))" "$gate_failure_output" \
  || fail "coverage gate failures must print a clear failure message"
grep -Fq "NIX_DARWIN_SKIP_COVERAGE_GATES=1" "$gate_failure_output" \
  || fail "coverage gate failures must mention the emergency override"

gate_override_default_output="$test_tmp_root/gate-override-default.out"
TEST_ALL_FAKE_GATE_FAILURES=1
NIX_DARWIN_SKIP_COVERAGE_GATES=1
if run_runner_captured "$gate_override_default_output" all --report < /dev/null; then
  fail "coverage gate override must fail closed unless the user types yes"
fi
TEST_ALL_FAKE_GATE_FAILURES=
NIX_DARWIN_SKIP_COVERAGE_GATES=
grep -Fq "EMERGENCY COVERAGE GATE OVERRIDE REQUESTED" "$gate_override_default_output" \
  || fail "coverage gate override must print a loud warning"
grep -Fq "Type yes to continue" "$gate_override_default_output" \
  || fail "coverage gate override must prompt for explicit confirmation"

gate_override_yes_output="$test_tmp_root/gate-override-yes.out"
TEST_ALL_FAKE_GATE_FAILURES=1
NIX_DARWIN_SKIP_COVERAGE_GATES=1
if ! run_runner_captured "$gate_override_yes_output" all --report <<< "YeS"; then
  fail "coverage gate override must accept explicit case-insensitive yes"
fi
TEST_ALL_FAKE_GATE_FAILURES=
NIX_DARWIN_SKIP_COVERAGE_GATES=
grep -Fq "coverage gate override confirmed" "$gate_override_yes_output" \
  || fail "coverage gate override must report confirmed continuation"

: > "$run_log"
unit_report_output="$(run_runner unit --report)"
case "$unit_report_output" in
  *"unit python statement coverage: 90%"* ) ;;
  * ) fail "unit reported runs must summarize unit Python statement coverage" ;;
esac
case "$unit_report_output" in
  *"unit python branch coverage: 55%"* ) ;;
  * ) fail "unit reported runs must summarize unit Python branch coverage" ;;
esac
case "$unit_report_output" in
  *"unit python source-target coverage: 1 / 1 (100%)"* ) ;;
  * ) fail "unit reported runs must summarize unit Python source-target coverage" ;;
esac
case "$unit_report_output" in
  *"unit bash statement coverage: 12.50%"* ) ;;
  * ) fail "unit reported runs must summarize unit Bash statement coverage" ;;
esac
case "$unit_report_output" in
  *"unit bash source-target coverage: 1 / 1 (100%)"* ) ;;
  * ) fail "unit reported runs must summarize unit Bash source-target coverage" ;;
esac
case "$unit_report_output" in
  *"unit nix source-target coverage: 2 / 4 (50%)"* ) ;;
  * ) fail "unit reported runs must summarize unit Nix source-target coverage" ;;
esac
case "$unit_report_output" in
  *"unit awk statement coverage:"*"(100%)"* ) ;;
  * ) fail "unit reported runs must summarize unit awk statement coverage" ;;
esac
case "$unit_report_output" in
  *"unit applescript statement coverage: 2 / 2 (100%)"* ) ;;
  * ) fail "unit reported runs must summarize unit AppleScript statement coverage" ;;
esac
case "$unit_report_output" in
  *"unit zsh statement coverage:"*"(100%)"* ) ;;
  * ) fail "unit reported runs must summarize unit zsh statement coverage" ;;
esac
case "$unit_report_output" in
  *"integration ruby source-target coverage"* | *"all ruby source-target coverage"* )
    fail "unit reported runs must not include integration or aggregate Ruby source-target coverage"
    ;;
esac
case "$unit_report_output" in
  *"integration python statement coverage"* | *"integration python branch coverage"* | *"integration python source-target coverage"* )
    fail "unit reported runs must not include integration coverage"
    ;;
esac
case "$unit_report_output" in
  *"integration nix source-target coverage"* | *"all nix source-target coverage"* )
    fail "unit reported runs must not include integration or aggregate Nix source-target coverage"
    ;;
esac
case "$unit_report_output" in
  *"integration bash statement coverage"* | *"integration bash source-target coverage"* | *"all bash statement coverage"* | *"all bash source-target coverage"* )
    fail "unit reported runs must not include integration or aggregate Bash coverage"
    ;;
esac
case "$unit_report_output" in
  *"integration awk statement coverage"* | *"all awk statement coverage"* | *"integration applescript statement coverage"* | *"all applescript statement coverage"* )
    fail "unit reported runs must not include integration or aggregate awk/AppleScript statement coverage"
    ;;
esac
case "$unit_report_output" in
  *"integration zsh statement coverage"* | *"all zsh statement coverage"* )
    fail "unit reported runs must not include integration or aggregate zsh statement coverage"
    ;;
esac

report_parallel_probe_dir="$test_tmp_root/report-parallel-probe"
mkdir -p "$report_parallel_probe_dir"
: > "$run_log"
TEST_ALL_FAKE_REPORT_PARALLEL_PROBE=1 \
  TEST_ALL_FAKE_REPORT_PARALLEL_DIR="$report_parallel_probe_dir" \
  run_runner unit --report --jobs 2 >/dev/null
[ -f "$report_parallel_probe_dir/unit-report-bash.finished" ] \
  || fail "unit --report --jobs 2 must run unit bash report lane through the parallel helper"
[ -f "$report_parallel_probe_dir/unit-report-awk.finished" ] \
  || fail "unit --report --jobs 2 must run unit awk report lane through the parallel helper"

[ -f "$report_dir/unit/latest/unit/python/junit.xml" ] \
  || fail "nix-unit-test style reporting must write unit Python JUnit XML"
[ -f "$report_dir/unit/latest/unit/python/report.json" ] \
  || fail "nix-unit-test style reporting must write unit Python pytest JSON report"
[ -f "$report_dir/unit/latest/unit/python/output.txt" ] \
  || fail "nix-unit-test style reporting must write unit Python raw execution output"
[ -f "$report_dir/unit/latest/unit/python/source-target-coverage.tsv" ] \
  || fail "nix-unit-test style reporting must write unit Python source-target coverage details"
[ -f "$report_dir/unit/latest/unit/bash/alpha.unit.test.bats/report.xml" ] \
  || fail "nix-unit-test style reporting must write unit bash Bats JUnit XML"
[ -f "$report_dir/unit/latest/unit/bash/alpha.unit.test.bats/output.txt" ] \
  || fail "nix-unit-test style reporting must write unit bash Bats raw execution output"
[ -f "$report_dir/unit/latest/unit/bash/kcov/kcov-merged/coverage.json" ] \
  || fail "nix-unit-test style reporting must write unit bash kcov coverage JSON"
[ -f "$report_dir/unit/latest/unit/bash/source-target-coverage.tsv" ] \
  || fail "nix-unit-test style reporting must write unit Bash source-target coverage details"
[ -f "$report_dir/unit/latest/unit/awk/parser.unit.test.bats/report.xml" ] \
  || fail "nix-unit-test style reporting must write unit awk Bats JUnit XML"
[ -f "$report_dir/unit/latest/unit/awk/statement-coverage.tsv" ] \
  || fail "nix-unit-test style reporting must write unit awk statement coverage details"
[ -f "$report_dir/unit/latest/unit/applescript/display-notification.unit.test.bats/report.xml" ] \
  || fail "nix-unit-test style reporting must write unit AppleScript Bats JUnit XML"
[ -f "$report_dir/unit/latest/unit/applescript/statement-coverage.tsv" ] \
  || fail "nix-unit-test style reporting must write unit AppleScript statement coverage details"
[ -f "$report_dir/unit/latest/unit/zsh/bootstrap-macos.unit.zunit/output.tap" ] \
  || fail "nix-unit-test style reporting must write unit zsh zunit TAP"
[ -f "$report_dir/unit/latest/unit/zsh/statement-coverage.tsv" ] \
  || fail "nix-unit-test style reporting must write unit zsh statement coverage details"
[ -f "$report_dir/unit/latest/unit/nix/config.unit.test.nix/output.txt" ] \
  || fail "nix-unit-test style reporting must write unit Nix output"

: > "$run_log"
int_report_output="$(run_runner int --report)"
case "$int_report_output" in
  *"integration python source-target coverage: 1 / 1 (100%)"* ) ;;
  * ) fail "integration reported runs must summarize integration Python source-target coverage" ;;
esac
case "$int_report_output" in
  *"integration ruby source-target coverage: 1 / 1 (100%)"* ) ;;
  * ) fail "integration reported runs must summarize integration Ruby source-target coverage" ;;
esac
case "$int_report_output" in
  *"integration bash statement coverage: 12.50%"* ) ;;
  * ) fail "integration reported runs must summarize integration Bash statement coverage" ;;
esac
case "$int_report_output" in
  *"integration bash source-target coverage: 1 / 1 (100%)"* ) ;;
  * ) fail "integration reported runs must summarize integration Bash source-target coverage" ;;
esac
case "$int_report_output" in
  *"integration nix source-target coverage: 1 / 4 (25%)"* ) ;;
  * ) fail "integration reported runs must summarize integration Nix source-target coverage" ;;
esac
case "$int_report_output" in
  *"integration awk statement coverage:"*"(100%)"* ) ;;
  * ) fail "integration reported runs must summarize integration awk statement coverage" ;;
esac
case "$int_report_output" in
  *"integration applescript statement coverage: 2 / 2 (100%)"* ) ;;
  * ) fail "integration reported runs must summarize integration AppleScript statement coverage" ;;
esac
case "$int_report_output" in
  *"integration zsh statement coverage:"*"(100%)"* ) ;;
  * ) fail "integration reported runs must summarize integration zsh statement coverage" ;;
esac
case "$int_report_output" in
  *"unit python statement coverage"* | *"unit python branch coverage"* | *"unit python source-target coverage"* )
    fail "integration reported runs must not include unit coverage"
    ;;
esac
case "$int_report_output" in
  *"unit ruby statement coverage"* | *"unit ruby branch coverage"* | *"unit ruby source-target coverage"* | *"all ruby source-target coverage"* )
    fail "integration reported runs must not include unit or aggregate Ruby coverage"
    ;;
esac
case "$int_report_output" in
  *"unit nix source-target coverage"* | *"all nix source-target coverage"* )
    fail "integration reported runs must not include unit or aggregate Nix source-target coverage"
    ;;
esac
case "$int_report_output" in
  *"unit bash statement coverage"* | *"unit bash source-target coverage"* | *"all bash statement coverage"* | *"all bash source-target coverage"* )
    fail "integration reported runs must not include unit or aggregate Bash coverage"
    ;;
esac
case "$int_report_output" in
  *"unit awk statement coverage"* | *"all awk statement coverage"* | *"unit applescript statement coverage"* | *"all applescript statement coverage"* )
    fail "integration reported runs must not include unit or aggregate awk/AppleScript statement coverage"
    ;;
esac
case "$int_report_output" in
  *"unit zsh statement coverage"* | *"all zsh statement coverage"* )
    fail "integration reported runs must not include unit or aggregate zsh statement coverage"
    ;;
esac

report_int_parallel_probe_dir="$test_tmp_root/report-int-parallel-probe"
mkdir -p "$report_int_parallel_probe_dir"
: > "$run_log"
TEST_ALL_FAKE_REPORT_PARALLEL_PROBE=1 \
  TEST_ALL_FAKE_REPORT_PARALLEL_DIR="$report_int_parallel_probe_dir" \
  run_runner int --report --jobs 2 >/dev/null
[ -f "$report_int_parallel_probe_dir/int-report-bash.finished" ] \
  || fail "int --report --jobs 2 must run integration bash report lane through the parallel helper"
[ -f "$report_int_parallel_probe_dir/int-report-awk.finished" ] \
  || fail "int --report --jobs 2 must run integration awk report lane through the parallel helper"

[ -f "$report_dir/int/latest/int/python/junit.xml" ] \
  || fail "nix-int-test style reporting must write integration Python JUnit XML"
[ -f "$report_dir/int/latest/int/python/report.json" ] \
  || fail "nix-int-test style reporting must write integration Python pytest JSON report"
[ -f "$report_dir/int/latest/int/python/output.txt" ] \
  || fail "nix-int-test style reporting must write integration Python raw execution output"
[ -f "$report_dir/int/latest/int/python/source-target-coverage.tsv" ] \
  || fail "nix-int-test style reporting must write integration Python source-target coverage details"
[ -f "$report_dir/int/latest/int/ruby/results.json" ] \
  || fail "nix-int-test style reporting must write integration Ruby result JSON"
[ -f "$report_dir/int/latest/int/ruby/output.txt" ] \
  || fail "nix-int-test style reporting must write integration Ruby raw execution output"
[ -f "$report_dir/int/latest/int/ruby/source-target-coverage.tsv" ] \
  || fail "nix-int-test style reporting must write integration Ruby source-target coverage details"
[ -f "$report_dir/int/latest/int/bash/runtime.int.test.bats/report.xml" ] \
  || fail "nix-int-test style reporting must write integration bash Bats JUnit XML"
[ -f "$report_dir/int/latest/int/bash/runtime.int.test.bats/output.txt" ] \
  || fail "nix-int-test style reporting must write integration bash Bats raw execution output"
[ -f "$report_dir/int/latest/int/bash/kcov/kcov-merged/coverage.json" ] \
  || fail "nix-int-test style reporting must write integration bash kcov coverage JSON"
[ -f "$report_dir/int/latest/int/bash/source-target-coverage.tsv" ] \
  || fail "nix-int-test style reporting must write integration Bash source-target coverage details"
[ -f "$report_dir/int/latest/int/awk/parser.int.test.bats/report.xml" ] \
  || fail "nix-int-test style reporting must write integration awk Bats JUnit XML"
[ -f "$report_dir/int/latest/int/awk/statement-coverage.tsv" ] \
  || fail "nix-int-test style reporting must write integration awk statement coverage details"
[ -f "$report_dir/int/latest/int/applescript/notification.int.test.bats/report.xml" ] \
  || fail "nix-int-test style reporting must write integration AppleScript Bats JUnit XML"
[ -f "$report_dir/int/latest/int/applescript/statement-coverage.tsv" ] \
  || fail "nix-int-test style reporting must write integration AppleScript statement coverage details"
[ -f "$report_dir/int/latest/int/zsh/bootstrap-macos.int.zunit/output.tap" ] \
  || fail "nix-int-test style reporting must write integration zsh zunit TAP"
[ -f "$report_dir/int/latest/int/zsh/statement-coverage.tsv" ] \
  || fail "nix-int-test style reporting must write integration zsh statement coverage details"
[ -f "$report_dir/int/latest/int/nix/config.int.test.nix/output.txt" ] \
  || fail "nix-int-test style reporting must write integration Nix output"

: > "$run_log"
run_runner all --full
grep -Fqx "nix build --no-link --no-update-lock-file --no-write-lock-file $fake_repo#darwinConfigurations.example-primary.system" "$run_log" \
  || fail "--full must run the Darwin system build"

: > "$run_log"
run_runner unit --jobs 1
grep -Fqx "pytest $fake_repo/src/test/unit/python" "$run_log" \
  || fail "unit --jobs 1 must preserve sequential pytest execution"

: > "$run_log"
run_runner unit --report --jobs 2 >/dev/null
if grep -Fq "pytest -n 2" "$run_log"; then
  fail "unit --report --jobs 2 must keep pytest internal execution sequential"
fi

: > "$run_log"
run_wrapper "$nix_test"
grep -Fqx "bats $fake_repo/src/test/unit/bash/alpha.unit.test.bats" "$run_log" \
  || fail "nix-test must run the default suite"

: > "$run_log"
run_wrapper "$nix_unit_test"
grep -Fqx "bats $fake_repo/src/test/unit/bash/alpha.unit.test.bats" "$run_log" \
  || fail "nix-unit-test must run unit bash Bats tests"
grep -Fqx "pytest $fake_repo/src/test/unit/python" "$run_log" \
  || fail "nix-unit-test must run unit Python pytest tests"
grep -Fqx "bats $fake_repo/src/test/unit/awk/parser.unit.test.bats" "$run_log" \
  || fail "nix-unit-test must run unit awk bats tests"
grep -Fqx "bats $fake_repo/src/test/unit/applescript/display-notification.unit.test.bats" "$run_log" \
  || fail "nix-unit-test must run unit AppleScript Bats tests"
assert_applescript_compile_logged \
  "$fake_repo/src/test/unit/applescript/compile-fixture.unit.test.applescript" \
  "nix-unit-test must compile unit AppleScript test fixtures"
grep -Fqx "zunit --tap $fake_repo/src/test/unit/zsh/bootstrap-macos.unit.zunit" "$run_log" \
  || fail "nix-unit-test must run unit zsh zunit tests"
grep -Fqx "ruby $fake_repo/src/test/unit/ruby/vagrantfile.unit.test.rb" "$run_log" \
  || fail "nix-unit-test must run unit Ruby tests"
grep -Eq "^nix-unit --gc-roots-dir $nix_unit_gc_roots_regex --arg repoRoot $fake_repo --argstr system aarch64-darwin $fake_repo/src/test/unit/nix/config\\.unit\\.test\\.nix$" "$run_log" \
  || fail "nix-unit-test must run unit Nix tests"
grep -Eq '^nix-unit-conf-dir .*/nix-darwin-nix-unit\.[^/]*/conf$' "$run_log" \
  || fail "nix-unit-test must isolate nix-unit from host daemon settings"
grep -Fqx 'nix-unit-config experimental-features = nix-command flakes' "$run_log" \
  || fail "nix-unit-test must explicitly enable the required Nix features"
if grep -Fqx "bats $fake_repo/src/test/int/bash/runtime.int.test.bats" "$run_log"; then
  fail "nix-unit-test must not run integration bash Bats tests"
fi
if grep -Fqx "pytest $fake_repo/src/test/int/python" "$run_log"; then
  fail "nix-unit-test must not run integration Python pytest tests"
fi
if grep -Fq "$fake_repo/src/test/int/awk/parser.int.test.bats" "$run_log"; then
  fail "nix-unit-test must not run integration awk bats tests"
fi
if grep -Fq "$fake_repo/src/test/int/applescript/notification.int.test.bats" "$run_log"; then
  fail "nix-unit-test must not run integration AppleScript Bats tests"
fi
if grep -Fq "$fake_repo/src/test/int/applescript/compile-fixture.int.test.applescript" "$run_log"; then
  fail "nix-unit-test must not compile integration AppleScript test fixtures"
fi
if grep -Fq "$fake_repo/src/test/int/zsh/bootstrap-macos.int.zunit" "$run_log"; then
  fail "nix-unit-test must not run integration zsh tests"
fi
if grep -Fq "$fake_repo/src/test/int/nix/config.int.test.nix" "$run_log"; then
  fail "nix-unit-test must not run integration Nix tests"
fi
if grep -Fqx "bash $fake_repo/scripts/test-alpha.sh" "$run_log"; then
  fail "nix-unit-test must not run legacy all-suite script tests"
fi

mv "$fake_bin/pytest" "$fake_bin/pytest.disabled"
: > "$run_log"
run_runner_without_path_pytest unit
grep -Fqx "nix shell $fake_repo#nixDarwinTestPython -c pytest $fake_repo/src/test/unit/python" "$run_log" \
  || fail "runner must fall back to the flake-pinned Python test runtime when pytest is not on PATH"
mv "$fake_bin/pytest.disabled" "$fake_bin/pytest"

mv "$fake_bin/bats" "$fake_bin/bats.disabled"
: > "$run_log"
run_runner_without_path_bats unit
grep -Fqx "nix shell $fake_repo#legacyPackages.aarch64-darwin.bats -c bats $fake_repo/src/test/unit/bash/alpha.unit.test.bats" "$run_log" \
  || fail "runner must fall back to flake-pinned bats when bats is not on PATH"
mv "$fake_bin/bats.disabled" "$fake_bin/bats"

mv "$fake_bin/nix-unit" "$fake_bin/nix-unit.disabled"
: > "$run_log"
run_runner_without_path_nix_unit unit
grep -Eq "^nix shell $fake_repo#legacyPackages\\.aarch64-darwin\\.nix-unit -c nix-unit --gc-roots-dir $nix_unit_gc_roots_regex --arg repoRoot $fake_repo --argstr system aarch64-darwin $fake_repo/src/test/unit/nix/config\\.unit\\.test\\.nix$" "$run_log" \
  || fail "runner must fall back to flake-pinned nix-unit when nix-unit is not on PATH"
mv "$fake_bin/nix-unit.disabled" "$fake_bin/nix-unit"

mv "$fake_bin/zunit" "$fake_bin/zunit.disabled"
: > "$run_log"
run_runner_without_path_zunit unit
grep -Fqx "nix shell $fake_repo#legacyPackages.aarch64-darwin.zunit -c zunit --tap $fake_repo/src/test/unit/zsh/bootstrap-macos.unit.zunit" "$run_log" \
  || fail "runner must fall back to flake-pinned zunit when zunit is not on PATH"
mv "$fake_bin/zunit.disabled" "$fake_bin/zunit"

: > "$run_log"
run_wrapper "$nix_int_test"
grep -Fqx "bats $fake_repo/src/test/int/bash/runtime.int.test.bats" "$run_log" \
  || fail "nix-int-test must run integration bash Bats tests"
grep -Fqx "pytest $fake_repo/src/test/int/python" "$run_log" \
  || fail "nix-int-test must run integration Python pytest tests"
grep -Fqx "bats $fake_repo/src/test/int/awk/parser.int.test.bats" "$run_log" \
  || fail "nix-int-test must run integration awk bats tests"
grep -Fqx "bats $fake_repo/src/test/int/applescript/notification.int.test.bats" "$run_log" \
  || fail "nix-int-test must run integration AppleScript Bats tests"
assert_applescript_compile_logged \
  "$fake_repo/src/test/int/applescript/compile-fixture.int.test.applescript" \
  "nix-int-test must compile integration AppleScript test fixtures"
grep -Fqx "zunit --tap $fake_repo/src/test/int/zsh/bootstrap-macos.int.zunit" "$run_log" \
  || fail "nix-int-test must run integration zsh zunit tests"
grep -Fqx "ruby $fake_repo/src/test/int/ruby/arq_and_vagrant.int.test.rb" "$run_log" \
  || fail "nix-int-test must run integration Ruby tests"
grep -Fqx "ruby $fake_repo/src/test/int/ruby/vagrant.int.test.rb" "$run_log" \
  || fail "nix-int-test must run extra integration Ruby tests"
grep -Eq "^nix-unit --gc-roots-dir $nix_unit_gc_roots_regex --arg repoRoot $fake_repo --argstr system aarch64-darwin $fake_repo/src/test/int/nix/config\\.int\\.test\\.nix$" "$run_log" \
  || fail "nix-int-test must run integration Nix tests"
if grep -Fqx "bats $fake_repo/src/test/unit/bash/alpha.unit.test.bats" "$run_log"; then
  fail "nix-int-test must not run unit bash Bats tests"
fi
if grep -Fqx "pytest $fake_repo/src/test/unit/python" "$run_log"; then
  fail "nix-int-test must not run unit Python pytest tests"
fi
if grep -Fq "$fake_repo/src/test/unit/awk/parser.unit.test.bats" "$run_log"; then
  fail "nix-int-test must not run unit awk bats tests"
fi
if grep -Fq "$fake_repo/src/test/unit/applescript/display-notification.unit.test.bats" "$run_log"; then
  fail "nix-int-test must not run unit AppleScript Bats tests"
fi
if grep -Fq "$fake_repo/src/test/unit/applescript/compile-fixture.unit.test.applescript" "$run_log"; then
  fail "nix-int-test must not compile unit AppleScript test fixtures"
fi
if grep -Fq "$fake_repo/src/test/unit/ruby/vagrantfile.unit.test.rb" "$run_log"; then
  fail "nix-int-test must not run unit Ruby tests"
fi
if grep -Fq "$fake_repo/src/test/unit/zsh/bootstrap-macos.unit.zunit" "$run_log"; then
  fail "nix-int-test must not run unit zsh tests"
fi
if grep -Fq "$fake_repo/src/test/unit/nix/config.unit.test.nix" "$run_log"; then
  fail "nix-int-test must not run unit Nix tests"
fi

: > "$run_log"
run_test_all
grep -Fqx "bats $fake_repo/src/test/unit/bash/alpha.unit.test.bats" "$run_log" \
  || fail "scripts/test-all.sh must delegate to the default suite"
grep -Fqx "pytest $fake_repo/src/test/unit/python" "$run_log" \
  || fail "scripts/test-all.sh must delegate to the Python pytest lane"
grep -Eq "^nix-unit --gc-roots-dir $nix_unit_gc_roots_regex --arg repoRoot $fake_repo --argstr system aarch64-darwin $fake_repo/src/test/unit/nix/config\\.unit\\.test\\.nix$" "$run_log" \
  || fail "scripts/test-all.sh must delegate to the Nix nix-unit lane"
grep -Fqx "zunit --tap $fake_repo/src/test/unit/zsh/bootstrap-macos.unit.zunit" "$run_log" \
  || fail "scripts/test-all.sh must delegate to the zsh zunit lane"

flake_check_report_output="$test_tmp_root/flake-check-report.out"
: > "$run_log"
TEST_ALL_FAKE_FLAKE_CHECK_FAILURE=1
if run_runner_captured "$flake_check_report_output" all --report; then
  fail "reported runs must return the preserved flake-check failure"
fi
TEST_ALL_FAKE_FLAKE_CHECK_FAILURE=
grep -Fq "pytest" "$run_log" \
  || fail "reported runs must continue to later language lanes after flake-check failure"
grep -Fq "TEST_REPORT_FLAKE_CHECK_FAILURE_TOKEN" "$flake_check_report_output" \
  || fail "reported suite-step output must remain visible in the terminal stream"
runner_steps="$report_dir/all/latest/runner/steps.tsv"
[ -f "$runner_steps" ] \
  || fail "reported runs must write normalized runner-step results"
grep -Fq "$(printf '2\tflake check\tfail\t73\trunner/002-flake-check/output.txt')" "$runner_steps" \
  || fail "reported runs must preserve the flake-check label, status, exit code, and output link"
grep -Fq "TEST_REPORT_FLAKE_CHECK_FAILURE_TOKEN" \
  "$report_dir/all/latest/runner/002-flake-check/output.txt" \
  || fail "reported runs must preserve the exact flake-check diagnostic"
grep -Fq "$(printf 'fail\t73')" \
  "$report_dir/all/latest/runner/execution.tsv" \
  || fail "the execution artifact must preserve the authoritative failure"
grep -Fq "$(printf '1\tflake check\t73')" \
  "$report_dir/all/latest/runner/failures.tsv" \
  || fail "the failures artifact must attribute the flake-check failure"
grep -Fq "Overall run status: FAIL" "$report_dir/all/latest/index.html" \
  || fail "the report homepage must expose the overall runner failure"
grep -Fq "flake check fail 73 runner/002-flake-check/output.txt" \
  "$report_dir/all/latest/index.html" \
  || fail "the report homepage must link the failing runner step"
grep -Fq "flake check 73" "$report_dir/all/latest/index.html" \
  || fail "the report homepage must render execution-failure attribution"

flake_check_parallel_output="$test_tmp_root/flake-check-parallel-report.out"
: > "$run_log"
TEST_ALL_FAKE_FLAKE_CHECK_FAILURE=1
if run_runner_captured "$flake_check_parallel_output" all --report --jobs 4; then
  flake_check_parallel_status=0
else
  flake_check_parallel_status="$?"
fi
TEST_ALL_FAKE_FLAKE_CHECK_FAILURE=
[ "$flake_check_parallel_status" -eq 73 ] \
  || fail "parallel reported runs must preserve the flake-check exit 73"
grep -Fq "pytest " "$run_log" \
  || fail "parallel reported runs must continue to later language lanes after flake-check failure"

cat > "$fake_repo/src/test/unit/python/fail.unit.test.py" <<'TEST'
def test_fail():
    assert False
TEST
for report_jobs in 1 4; do
  flake_and_lane_output="$test_tmp_root/flake-and-lane-jobs-$report_jobs.out"
  : > "$run_log"
  TEST_ALL_FAKE_FLAKE_CHECK_FAILURE=1
  if run_runner_captured "$flake_and_lane_output" all --report --jobs "$report_jobs"; then
    flake_and_lane_status=0
  else
    flake_and_lane_status="$?"
  fi
  TEST_ALL_FAKE_FLAKE_CHECK_FAILURE=
  [ "$flake_and_lane_status" -eq 73 ] \
    || fail "reported jobs=$report_jobs must preserve flake-check exit 73 across a lane failure"
  grep -Fq "$(printf 'fail\t73')" \
    "$report_dir/all/latest/runner/execution.tsv" \
    || fail "reported jobs=$report_jobs must finalize execution with exit 73"
  grep -Fq "$(printf '1\tflake check\t73')" \
    "$report_dir/all/latest/runner/failures.tsv" \
    || fail "reported jobs=$report_jobs must record flake check as the first failure"
  grep -Fq "$(printf '2\tunit/python/pytest\t7')" \
    "$report_dir/all/latest/runner/failures.tsv" \
    || fail "reported jobs=$report_jobs must record the canonical Python lane as the second failure"
done
rm "$fake_repo/src/test/unit/python/fail.unit.test.py"

flake_and_gate_output="$test_tmp_root/flake-and-gate-report.out"
: > "$run_log"
TEST_ALL_FAKE_FLAKE_CHECK_FAILURE=1
TEST_ALL_FAKE_GATE_FAILURES=1
if run_runner_captured "$flake_and_gate_output" all --report; then
  flake_and_gate_status=0
else
  flake_and_gate_status="$?"
fi
TEST_ALL_FAKE_FLAKE_CHECK_FAILURE=
TEST_ALL_FAKE_GATE_FAILURES=
[ "$flake_and_gate_status" -eq 73 ] \
  || fail "a later coverage-gate failure must preserve the first flake-check exit 73"

flake_check_plain_output="$test_tmp_root/flake-check-plain.out"
: > "$run_log"
TEST_ALL_FAKE_FLAKE_CHECK_FAILURE=1
if run_runner_captured "$flake_check_plain_output" all; then
  fail "plain runs must return the flake-check failure"
fi
TEST_ALL_FAKE_FLAKE_CHECK_FAILURE=
if grep -Fq "pytest" "$run_log"; then
  fail "plain runs must remain fail-fast after flake-check failure"
fi

cat > "$fake_repo/src/test/unit/bash/a-fail-first.unit.test.bats" <<'TEST'
#!/usr/bin/env bash
exit 75
TEST
cat > "$fake_repo/src/test/unit/bash/b-after-failure.unit.test.bats" <<'TEST'
#!/usr/bin/env bash
exit 0
TEST
chmod +x \
  "$fake_repo/src/test/unit/bash/a-fail-first.unit.test.bats" \
  "$fake_repo/src/test/unit/bash/b-after-failure.unit.test.bats"
plain_same_lane_output="$test_tmp_root/plain-same-lane.out"
: > "$run_log"
if run_runner_captured "$plain_same_lane_output" unit; then
  fail "plain runs must fail at the first failing file in a lane"
fi
grep -Fq "a-fail-first.unit.test.bats" "$run_log" \
  || fail "plain fail-fast proof must execute the first failing lane file"
if grep -Fq "b-after-failure.unit.test.bats" "$run_log"; then
  fail "plain fail-fast must not execute a later file in the same lane"
fi
rm \
  "$fake_repo/src/test/unit/bash/a-fail-first.unit.test.bats" \
  "$fake_repo/src/test/unit/bash/b-after-failure.unit.test.bats"

patch_hygiene_report_output="$test_tmp_root/patch-hygiene-report.out"
: > "$run_log"
TEST_ALL_FAKE_PATCH_HYGIENE_FAILURE=1
if run_runner_captured "$patch_hygiene_report_output" all --report; then
  fail "reported runs must return the preserved patch-hygiene failure"
fi
TEST_ALL_FAKE_PATCH_HYGIENE_FAILURE=
grep -Fq "$(printf '1\tpatch hygiene\tfail\t71\trunner/001-patch-hygiene/output.txt')" \
  "$report_dir/all/latest/runner/steps.tsv" \
  || fail "the runner-step mechanism must generically capture patch hygiene"
grep -Fq "TEST_REPORT_PATCH_HYGIENE_FAILURE_TOKEN" \
  "$report_dir/all/latest/runner/001-patch-hygiene/output.txt" \
  || fail "reported runs must preserve the exact patch-hygiene diagnostic"

shell_syntax_report_output="$test_tmp_root/shell-syntax-report.out"
: > "$run_log"
TEST_ALL_FAKE_SHELL_SYNTAX_FAILURE=1
if run_runner_captured "$shell_syntax_report_output" all --report; then
  fail "reported runs must return the preserved shell-syntax failure"
fi
TEST_ALL_FAKE_SHELL_SYNTAX_FAILURE=
grep -Fq "$(printf '3\tshell syntax\tfail\t72\trunner/003-shell-syntax/output.txt')" \
  "$report_dir/all/latest/runner/steps.tsv" \
  || fail "shell syntax must record a non-terminal syntax-check failure"
grep -Fq "TEST_REPORT_SHELL_SYNTAX_FAILURE_TOKEN" \
  "$report_dir/all/latest/runner/003-shell-syntax/output.txt" \
  || fail "shell syntax must preserve the exact syntax diagnostic"
grep -Fq "zsh -n $fake_repo/shell/zshrc" "$run_log" \
  || fail "report shell-syntax checks must continue after a captured early failure"
grep -Fq "pytest " "$run_log" \
  || fail "reported runs must continue to language lanes after shell-syntax failure"

darwin_build_report_output="$test_tmp_root/darwin-build-report.out"
: > "$run_log"
TEST_ALL_FAKE_DARWIN_BUILD_FAILURE=1
if run_runner_captured "$darwin_build_report_output" all --full --report; then
  fail "reported full runs must return the preserved Darwin-build failure"
fi
TEST_ALL_FAKE_DARWIN_BUILD_FAILURE=
grep -Fq "TEST_REPORT_DARWIN_BUILD_FAILURE_TOKEN" "$darwin_build_report_output" \
  || fail "the trailing Darwin-build failure must remain visible in the terminal stream"
grep -Fq "Darwin system build" "$report_dir/all/latest/runner/steps.tsv" \
  || fail "the trailing Darwin-build step must be recorded before rendering"
darwin_build_output_log="$(awk -F "$(printf '\t')" '$2 == "Darwin system build" { print $5 }' \
  "$report_dir/all/latest/runner/steps.tsv")"
[ -n "$darwin_build_output_log" ] \
  || fail "the trailing Darwin-build step must have an output path"
grep -Fq "TEST_REPORT_DARWIN_BUILD_FAILURE_TOKEN" \
  "$report_dir/all/latest/$darwin_build_output_log" \
  || fail "the trailing Darwin-build diagnostic must be captured before rendering"
grep -Fq "Overall run status: FAIL" "$report_dir/all/latest/index.html" \
  || fail "the trailing Darwin-build failure must affect the rendered overall status"

cat > "$fake_repo/src/test/unit/bash/fail.unit.test.bats" <<'TEST'
#!/usr/bin/env bash
exit 42
TEST
chmod +x "$fake_repo/src/test/unit/bash/fail.unit.test.bats"

if run_runner all >/dev/null 2>&1; then
  fail "runner must fail when a child test fails"
fi

: > "$run_log"
if run_runner unit --report >/dev/null 2>&1; then
  fail "reported runs must return failure when a Bats lane fails"
fi
grep -Fq "pytest" "$run_log" \
  || fail "reported runs must still run the Python lane after Bats failures"
[ -f "$report_dir/unit/latest/unit/bash/fail.unit.test.bats/report.xml" ] \
  || fail "failed reported Bats runs must still write Bats JUnit XML"
[ -f "$report_dir/unit/latest/index.html" ] \
  || fail "failed reported Bats runs must still write an HTML test result report"

rm "$fake_repo/src/test/unit/bash/fail.unit.test.bats"
cat > "$fake_repo/src/test/unit/python/fail.unit.test.py" <<'TEST'
def test_fail():
    assert False
TEST
cat > "$fake_repo/src/test/unit/ruby/fail_ruby.unit.test.rb" <<'TEST'
#!/usr/bin/env ruby
raise "ruby fail"
TEST

: > "$run_log"
if run_runner unit --report >/dev/null 2>&1; then
  fail "reported runs must return failure when a Python or Ruby lane fails"
fi
grep -Fq "pytest" "$run_log" \
  || fail "reported runs must still run the Python lane when collecting failures"
grep -Fq "$ruby_test_runner" "$run_log" \
  || fail "reported runs must still run the Ruby lane after Python failures"
[ -f "$report_dir/unit/latest/test-results.json" ] \
  || fail "failed reported runs must still write normalized test result JSON"
[ -f "$report_dir/unit/latest/index.html" ] \
  || fail "failed reported runs must still write an HTML test result report"
grep -Fq "$(printf '\tunit/python/pytest\t7')" \
  "$report_dir/unit/latest/runner/failures.tsv" \
  || fail "serial report failures must use the canonical Python lane label"
grep -Fq "$(printf '\tunit/ruby\t8')" \
  "$report_dir/unit/latest/runner/failures.tsv" \
  || fail "serial report failures must use the canonical Ruby lane label"

echo "test-all tests passed"

}
