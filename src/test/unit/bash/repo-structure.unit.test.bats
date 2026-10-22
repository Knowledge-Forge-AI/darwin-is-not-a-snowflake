#!/usr/bin/env bats

@test "repo-structure.unit.test" {
set -euo pipefail

script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
repo_dir="$(cd "$script_dir/../../../.." && pwd)"
git_root="$(git -C "$repo_dir" rev-parse --show-toplevel)"

fail() {
  echo "test-repo-structure: $*" >&2
  exit 1
}

flake_file="$repo_dir/flake.nix"
modules_dir="$repo_dir/modules"
rtk_package="$repo_dir/packages/rtk/default.nix"
shared_images_lib="$repo_dir/libexec/shared-images/shared_images_lib.py"
codex_env_run_lib="$repo_dir/libexec/codex-env/run.py"
codex_env_snapshot_lib="$repo_dir/libexec/codex-env/snapshot.py"
test_runner="$repo_dir/tools/test/run"
test_report_renderer="$repo_dir/tools/test/report/render.py"
pytest_config="$repo_dir/pytest.ini"

[ -f "$flake_file" ] || fail "missing flake.nix"
[ -d "$modules_dir" ] || fail "missing modules directory"
[ -f "$rtk_package" ] || fail "missing packages/rtk/default.nix"
[ -f "$test_runner" ] || fail "missing tools/test/run"
[ -f "$test_report_renderer" ] || fail "missing tools/test/report/render.py"

for test_command in nix-test nix-unit-test nix-int-test; do
  command_file="$repo_dir/bin/$test_command"
  [ -f "$command_file" ] || fail "missing test command: $test_command"
  rg -F -q '../tools/test/run' "$command_file" \
    || fail "$test_command must delegate to tools/test/run"
done

for test_lane in \
  src/test/unit/bash \
  src/test/unit/python \
  src/test/int/python \
  src/test/unit/awk \
  src/test/int/awk \
  src/test/unit/applescript \
  src/test/unit/zsh \
  src/test/unit/ruby
do
  [ -d "$repo_dir/$test_lane" ] || fail "missing test lane: $test_lane"
done

[ -f "$repo_dir/tools/bootstrap/bootstrap-macos" ] \
  || fail "user-facing bootstrap script must be extensionless"
[ ! -e "$repo_dir/tools/bootstrap/bootstrap-macos.sh" ] \
  || fail "user-facing bootstrap script must not keep a .sh extension"

while IFS= read -r applescript_source; do
  source_name="$(basename "$applescript_source" .applescript)"
  test_file="$repo_dir/src/test/unit/applescript/$source_name.unit.test.bats"
  [ -f "$test_file" ] \
    || fail "standalone AppleScript source must have a unit test: $applescript_source"
done < <(
  find "$repo_dir" \
    \( -path "$repo_dir/.git" -o -path "$repo_dir/src/test" \) -prune \
    -o -type f -name '*.applescript' -print |
    sort
)

while IFS= read -r legacy_bash_test; do
  fail "bash tests must use Bats (*.bats), not executable shell scripts: $legacy_bash_test"
done < <(find "$repo_dir/src/test" -path '*/bash/*' -type f -name '*.sh' | sort)

[ -f "$repo_dir/src/test/README.md" ] || fail "test layout must be documented in src/test/README.md"
[ -f "$pytest_config" ] || fail "pytest discovery must be configured in pytest.ini"
rg -F -q 'addopts = --import-mode=importlib' "$pytest_config" \
  || fail "pytest.ini must support standards-style dotted test filenames"
rg -F -q 'python_files = *.test.py' "$pytest_config" \
  || fail "pytest.ini must discover standards-style *.unit.test.py files"
rg -F -q 'cache_dir = .cache/.pytest_cache' "$pytest_config" \
  || fail "pytest cache must live under the repo cache root"

while IFS= read -r legacy_shell_test; do
  case "$(basename "$legacy_shell_test")" in
    test-all.sh)
      ;;
    *)
      fail "implementation helper scripts must live under libexec/<domain>, not scripts: $legacy_shell_test"
      ;;
  esac
done < <(find "$repo_dir/scripts" -maxdepth 1 -type f | sort)

if find "$repo_dir/scripts" -maxdepth 1 -type f -name 'test-*.py' -print -quit | rg -q .; then
  fail "Python tests must live under src/test/{unit,int}/python, not scripts"
fi

rg -F -q 'pkgs.bats' "$flake_file" \
  || fail "flake.nix must install bats for shell and awk test lanes"
rg -F -q 'pkgs.nix-unit' "$flake_file" \
  || fail "flake.nix must install nix-unit for Nix test lanes"
rg -F -q 'pkgs.zunit' "$flake_file" \
  || fail "flake.nix must install zunit for zsh test lanes"
rg -F -q 'pkgs.python3Packages.pytest' "$flake_file" \
  || fail "flake.nix must install pytest for Python test lanes"
rg -F -q 'rtk = final.callPackage ./packages/rtk { };' "$flake_file" \
  || fail "flake.nix must define rtk through the local overlay"
rg -F -q '          pkgs.rtk' "$flake_file" \
  || fail "flake.nix must install rtk from the local overlay"
rg -F -q 'rtk = pkgs.rtk;' "$flake_file" \
  || fail "flake.nix must expose rtk as a package output"
rg -F -q 'version = "0.43.0";' "$rtk_package" \
  || fail "the rtk package must pin the reviewed upstream version"
rg -F -q 'hash = "sha256-n5bkPPsrdM4fE5ltocTjlq+JwRgp39yib6S79fci4m4=";' "$rtk_package" \
  || fail "the rtk package must pin the reviewed source hash"
rg -F -q 'cargoHash = "sha256-XKUKdhxfnwUCOx9slqx4oUFa09HcosPLVh5Xkh87oSk=";' "$rtk_package" \
  || fail "the rtk package must pin the reviewed Cargo dependency hash"
rg -F -q 'license = lib.licenses.asl20;' "$rtk_package" \
  || fail "the rtk package must record its Apache-2.0 license"
if rg -F -q 'pythonPackages.pyyaml' "$flake_file"; then
  rg -F -q 'Workstation automation and skill validation YAML processing support.' "$flake_file" \
    || fail "flake.nix must document the Python YAML consumer when PyYAML is present"
fi
if rg -F -q 'pythonPackages.pillow' "$flake_file"; then
  rg -F -q 'Workstation image-processing support.' "$flake_file" \
    || fail "flake.nix must document Pillow as workstation image-processing support"
fi
rg -F -q 'pythonPackages.pytest-cov' "$flake_file" \
  || fail "flake.nix must provide pytest-cov for Python coverage reporting"
rg -F -q 'pythonPackages.pytest-json-report' "$flake_file" \
  || fail "flake.nix must provide pytest-json-report for Python test diagnostics"
rg -F -q 'rubyPackages.simplecov' "$flake_file" \
  || fail "flake.nix must provide SimpleCov for Ruby coverage reporting"
rg -F -q 'rubyPackages.minitest' "$flake_file" \
  || fail "flake.nix must provide Minitest for Ruby tests"
rg -F -q '"kcov"' "$repo_dir/modules/brewfile-stragglers.nix" \
  || fail "Homebrew must install kcov for Bash coverage reporting on Darwin"
rg -F -q 'nixDarwinTestPython' "$flake_file" \
  || fail "flake.nix must expose a Python test runtime with pytest and project runtime dependencies"
rg -F -q 'nixDarwinTestRuby' "$flake_file" \
  || fail "flake.nix must expose a Ruby test runtime with SimpleCov"
python3 -c 'import pathlib, sys; compile(pathlib.Path(sys.argv[1]).read_text(), sys.argv[1], "exec")' "$test_report_renderer" \
  || fail "test report renderer must compile"

[ -f "$git_root/.gitignore" ] || fail "generated test artifacts must be ignored at the git root"
rg -F -q '.test-reports/' "$git_root/.gitignore" \
  || fail ".test-reports must be git ignored"
rg -F -q '.coverage' "$git_root/.gitignore" \
  || fail ".coverage must be git ignored"
rg -F -q '.cache/' "$git_root/.gitignore" \
  || fail "directories named .cache must be git ignored"
rg -F -q '.coverage.*' "$git_root/.gitignore" \
  || fail ".coverage.* must be git ignored"

while IFS= read -r module_file; do
  module_name="$(basename "$module_file")"
  rg -F -q "(import ./modules/$module_name" "$flake_file" \
    || fail "flake.nix must import modules/$module_name"
done < <(find "$modules_dir" -maxdepth 1 -type f -name '*.nix' | sort)

while IFS=: read -r source_file referenced_path; do
  [ -n "$source_file" ] || continue
  [ -n "$referenced_path" ] || continue

  resolved_path="$modules_dir/$referenced_path"
  [ -e "$resolved_path" ] \
    || fail "$(basename "$source_file") references missing path $referenced_path"
done < <(
  rg -o '\.\./(bin|config|scripts)/[^; ")}]+' "$modules_dir" | sort -u
)

if git -C "$repo_dir" ls-files | rg -q '(^|/)__pycache__/|\.pyc$'; then
  fail "tracked Python bytecode/cache files are not allowed"
fi

if find "$repo_dir/bin" \( -name __pycache__ -o -name '*.pyc' \) -print -quit | rg -q .; then
  fail "Python bytecode/cache files are not allowed under PATH-facing nix-darwin/bin"
fi

while IFS= read -r python_command; do
  if rg -q 'sys\.pycache_prefix' "$python_command"; then
    fail "PATH-facing Python commands must rely on shell-managed PYTHONPYCACHEPREFIX: $python_command"
  fi
done < <(
  while IFS= read -r bin_file; do
    head -n 1 "$repo_dir/$bin_file" | rg -q 'python3' && printf '%s\n' "$repo_dir/$bin_file"
  done < <(git -C "$repo_dir" ls-files 'bin/*')
)

while IFS= read -r bin_file; do
  base_name="$(basename "$bin_file")"
  if [[ "$base_name" == *.* ]]; then
    fail "PATH-facing nix-darwin/bin files must be extensionless command entrypoints: $bin_file"
  fi
done < <(git -C "$repo_dir" ls-files 'bin/*')

source_heredoc_pattern='(cat[[:space:]]+>>?[^<]*\.(sh|bash|zsh|py|rb|awk|applescript|js|ts|java|c|cpp|nix)[^<]*<<|<<'\''?(SCRIPT|VERIFY|RUBY|PYTHON|BASH|ZSH|AWK|APPLESCRIPT|JS|TS|JAVA|CPP|C)'\''?)'
while IFS= read -r source_file; do
  case "$source_file" in
    src/test/*|tools/bootstrap/*)
      continue
      ;;
  esac

  if rg -n "$source_heredoc_pattern" "$repo_dir/$source_file" >/dev/null; then
    fail "source-code heredocs must be separate source files, not embedded in $source_file"
  fi
done < <(git -C "$repo_dir" ls-files | sort)

[ -f "$shared_images_lib" ] || fail "shared-images runtime library must live under libexec/shared-images"
python3 -c 'import pathlib, sys; compile(pathlib.Path(sys.argv[1]).read_text(), sys.argv[1], "exec")' "$shared_images_lib" \
  || fail "shared-images runtime library must compile"

[ -f "$codex_env_run_lib" ] \
  || fail "env-run implementation must live under libexec/codex-env"
python3 -c 'import pathlib, sys; compile(pathlib.Path(sys.argv[1]).read_text(), sys.argv[1], "exec")' "$codex_env_run_lib" \
  || fail "env-run implementation must compile"
env_run_command="$repo_dir/bin/env-run"
[ -f "$env_run_command" ] || fail "missing env-run command"
rg -F -q 'libexec" / "codex-env"' "$env_run_command" \
  || fail "env-run must delegate to libexec/codex-env"
rg -F -q 'from run import main' "$env_run_command" \
  || fail "env-run must invoke the tested Python implementation"

[ -f "$codex_env_snapshot_lib" ] \
  || fail "env-snapshot implementation must live under libexec/codex-env"
python3 -c 'import pathlib, sys; compile(pathlib.Path(sys.argv[1]).read_text(), sys.argv[1], "exec")' "$codex_env_snapshot_lib" \
  || fail "env-snapshot implementation must compile"
env_snapshot_command="$repo_dir/bin/env-snapshot"
[ -f "$env_snapshot_command" ] || fail "missing env-snapshot command"
rg -F -q 'libexec" / "codex-env"' "$env_snapshot_command" \
  || fail "env-snapshot must delegate to libexec/codex-env"
rg -F -q 'from snapshot import main' "$env_snapshot_command" \
  || fail "env-snapshot must invoke the tested Python implementation"

for shared_images_command in \
  shared-images-db-audit \
  shared-images-promote-original \
  shared-images-refresh-artist-links \
  shared-images-rehash \
  shared-images-workflow-layout
do
  command_file="$repo_dir/bin/$shared_images_command"
  [ -f "$command_file" ] || fail "missing shared-images command: $shared_images_command"
  if rg -q 'sys\.pycache_prefix' "$command_file"; then
    fail "$shared_images_command must rely on shell-managed PYTHONPYCACHEPREFIX before importing helpers"
  fi
  rg -F -q 'Path(__file__).resolve().parents[1] / "libexec" / "shared-images"' "$command_file" \
    || fail "$shared_images_command must import shared_images_lib from libexec/shared-images"
done

echo "repo structure tests passed"

}
