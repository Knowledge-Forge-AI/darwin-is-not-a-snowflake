#!/usr/bin/env bats

@test "language-manager-config.unit.test" {
set -euo pipefail

script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
repo_dir="$(cd "$script_dir/../../../.." && pwd)"

fail() {
  echo "test-language-manager-config: $*" >&2
  exit 1
}

nvm_module="$repo_dir/modules/nvm.nix"
pyenv_module="$repo_dir/modules/pyenv.nix"
rbenv_module="$repo_dir/modules/rbenv.nix"
nvm_helper="$repo_dir/libexec/nvm/ensure-node.sh"
pyenv_helper="$repo_dir/libexec/pyenv/ensure-python.sh"
rbenv_helper="$repo_dir/libexec/rbenv/ensure-ruby.sh"

for required_file in \
  "$nvm_module" \
  "$pyenv_module" \
  "$rbenv_module" \
  "$nvm_helper" \
  "$pyenv_helper" \
  "$rbenv_helper"
do
  [ -f "$required_file" ] || fail "missing required language manager file: $required_file"
done

bash -n "$nvm_helper"
bash -n "$pyenv_helper"
bash -n "$rbenv_helper"

rg -F -q 'ensureNode = ../libexec/nvm/ensure-node.sh;' "$nvm_module" \
  || fail "nvm module must source libexec/nvm/ensure-node.sh"
rg -F -q 'ensurePython = ../libexec/pyenv/ensure-python.sh;' "$pyenv_module" \
  || fail "pyenv module must source libexec/pyenv/ensure-python.sh"
rg -F -q 'ensureRuby = ../libexec/rbenv/ensure-ruby.sh;' "$rbenv_module" \
  || fail "rbenv module must source libexec/rbenv/ensure-ruby.sh"

rg -F -q '. "$nvm_script"' "$nvm_helper" \
  || fail "nvm helper must source the discovered nvm.sh before installing Node"
rg -F -q 'nvm install --lts' "$nvm_helper" \
  || fail "nvm helper must install the latest Node LTS"
rg -F -q "nvm alias default 'lts/*'" "$nvm_helper" \
  || fail "nvm helper must set the default Node alias to LTS"
rg -F -q 'pyenv install -s "$python_version"' "$pyenv_helper" \
  || fail "pyenv helper must install Python idempotently"
rg -F -q 'rbenv install -s "$ruby_version"' "$rbenv_helper" \
  || fail "rbenv helper must install Ruby idempotently"

echo "language manager config tests passed"

}
