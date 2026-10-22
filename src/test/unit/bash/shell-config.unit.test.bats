#!/usr/bin/env bats
# nix-darwin-test-source-target: shell/sh_config/common/001_aliases
# nix-darwin-test-source-target: shell/sh_config/common/003_nvm
# nix-darwin-test-source-target: shell/sh_config/common/004_path
# nix-darwin-test-source-target: shell/sh_config/common/005_pyenv
# nix-darwin-test-source-target: shell/sh_config/common/006_rbenv
# nix-darwin-test-source-target: shell/sh_config/common/011_repo-map
# nix-darwin-test-source-target: shell/sh_config/common/012_nix-darwin-jobs

@test "shell-config.unit.test" {
set -euo pipefail

script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
repo_dir="$(cd "$script_dir/../../../.." && pwd)"

fail() {
  echo "test-shell-config: $*" >&2
  exit 1
}

module_file="$repo_dir/modules/shell.nix"
writer_file="$repo_dir/modules/shell-writer.sh"
bash_profile_file="$repo_dir/shell/bash_profile"
bashrc_file="$repo_dir/shell/bashrc"
zshrc_file="$repo_dir/shell/zshrc"
aliases_file="$repo_dir/shell/sh_config/common/001_aliases"
nvm_file="$repo_dir/shell/sh_config/common/003_nvm"
path_file="$repo_dir/shell/sh_config/common/004_path"
pyenv_file="$repo_dir/shell/sh_config/common/005_pyenv"
rbenv_file="$repo_dir/shell/sh_config/common/006_rbenv"
python_cache_file="$repo_dir/shell/sh_config/common/009_python"
env_snapshot_file="$repo_dir/shell/sh_config/common/010_env_snapshot"
repo_map_file="$repo_dir/shell/sh_config/common/011_repo-map"
jobs_file="$repo_dir/shell/sh_config/common/012_nix-darwin-jobs"
on_login_file="$repo_dir/shell/sh_config/common/999_on-login"

[ -f "$module_file" ] || fail "missing modules/shell.nix"
[ -f "$writer_file" ] || fail "missing modules/shell-writer.sh"
[ -f "$bash_profile_file" ] || fail "missing shell/bash_profile"
[ -f "$bashrc_file" ] || fail "missing shell/bashrc"
[ -f "$zshrc_file" ] || fail "missing shell/zshrc"
[ -f "$aliases_file" ] || fail "missing common aliases"
[ -f "$nvm_file" ] || fail "missing common nvm config"
[ -f "$path_file" ] || fail "missing common PATH config"
[ -f "$pyenv_file" ] || fail "missing common pyenv config"
[ -f "$rbenv_file" ] || fail "missing common rbenv config"
[ -f "$python_cache_file" ] || fail "missing common Python cache config"
[ -f "$env_snapshot_file" ] || fail "missing common env snapshot config"
[ -f "$repo_map_file" ] || fail "missing common RepoMap config"
[ -f "$jobs_file" ] || fail "missing common nix-darwin jobs config"
[ -f "$on_login_file" ] || fail "missing common 999_on-login hook"

for managed_link in \
  '"$user_home/.zshrc" "$zshrc_source"' \
  '"$user_home/.bashrc" "$bashrc_source"' \
  '"$user_home/.bash_profile" "$bash_profile_source"' \
  '"$user_home/.sh_config" "$sh_config_source"'
do
  rg -F -q "uh_link_managed_path $managed_link" "$writer_file" \
    || fail "shell writer must link managed path: $managed_link"
done

rg -F -q 'target_user=${lib.escapeShellArg primaryUser}' "$module_file" \
  || fail "shell module must resolve the configured primary user"
rg -F -q 'user_home=${lib.escapeShellArg userHome}' "$module_file" \
  || fail "shell module must resolve the configured primary-user home"
rg -F -q '/usr/bin/sudo -n -H -u "$target_user" --' "$module_file" \
  || fail "shell module must cross to the target user noninteractively"
rg -F -q '/bin/sh ${./shell-writer.sh}' "$module_file" \
  || fail "shell module must invoke the store-backed writer"
rg -F -q 'uh_ensure_directory "$user_home/.local"' "$writer_file" \
  || fail "shell writer must create an ordinary ~/.local"
if rg -q '\b(chown|chgrp|sudo)\b' "$writer_file"; then
  fail "shell writer must not perform ownership or privilege operations"
fi

bash -n "$bash_profile_file"
bash -n "$bashrc_file"
zsh -n "$zshrc_file"
bash -n "$env_snapshot_file"
zsh -n "$env_snapshot_file"
bash -n "$jobs_file"
zsh -n "$jobs_file"

rg -F -q '.bashrc' "$bash_profile_file" \
  || fail "bash_profile must source bashrc"
if rg -F -q '000_last-login' "$bash_profile_file" "$bashrc_file"; then
  fail "Bash startup must not resurrect the stale last-login polyfill"
fi

bash_common_line="$(rg -n -F 'for file in "$HOME"/.sh_config/common/*' "$bashrc_file" | head -1 | cut -d: -f1)"
bash_specific_line="$(rg -n -F 'for file in "$HOME"/.sh_config/bash/*' "$bashrc_file" | head -1 | cut -d: -f1)"
bash_on_login_line="$(rg -n -F '.sh_config/common/999_on-login' "$bashrc_file" | head -1 | cut -d: -f1)"
[ -n "$bash_common_line" ] && [ -n "$bash_specific_line" ] && [ -n "$bash_on_login_line" ] \
  || fail "bashrc must source common, bash-specific, and 999_on-login files"
[ "$bash_common_line" -lt "$bash_specific_line" ] && [ "$bash_specific_line" -lt "$bash_on_login_line" ] \
  || fail "bashrc must source common setup, bash setup, then 999_on-login"
rg -F -q '[ "$(basename "$file")" = "999_on-login" ] && continue' "$bashrc_file" \
  || fail "bashrc must skip 999_on-login during the common setup loop"

zsh_common_line="$(rg -n -F 'for file in "$HOME"/.sh_config/common/*(N)' "$zshrc_file" | head -1 | cut -d: -f1)"
zsh_specific_line="$(rg -n -F 'for file in "$HOME"/.sh_config/zsh/*(N)' "$zshrc_file" | head -1 | cut -d: -f1)"
zsh_on_login_line="$(rg -n -F '.sh_config/common/999_on-login' "$zshrc_file" | head -1 | cut -d: -f1)"
[ -n "$zsh_common_line" ] && [ -n "$zsh_specific_line" ] && [ -n "$zsh_on_login_line" ] \
  || fail "zshrc must source common, zsh-specific, and 999_on-login files"
[ "$zsh_common_line" -lt "$zsh_specific_line" ] && [ "$zsh_specific_line" -lt "$zsh_on_login_line" ] \
  || fail "zshrc must source common setup, zsh setup, then 999_on-login"
rg -F -q '[ "${file:t}" = "999_on-login" ] && continue' "$zshrc_file" \
  || fail "zshrc must skip 999_on-login during the common setup loop"

if rg -F -q "alias nix-update=" "$aliases_file"; then
  fail "nix-update must be a PATH command, not a shell alias"
fi
if rg -F -q "alias nix-rebuild=" "$aliases_file"; then
  fail "nix-rebuild must be a PATH command, not a shell alias"
fi
if rg -F -q "sudo darwin-rebuild switch" "$aliases_file"; then
  fail "shell aliases must not bypass the guarded nix-rebuild PATH command"
fi
rg -F -q "alias ff='fastfetch'" "$aliases_file" \
  || fail "ff alias must invoke fastfetch"
rg -F -q "alias pyenv-python='PYENV_VERSION=\$(pyenv global) pyenv exec python'" "$aliases_file" \
  || fail "pyenv-python alias must explicitly invoke the pyenv-managed Python"
rg -F -q "alias rbenv-ruby='RBENV_VERSION=\$(rbenv global) rbenv exec ruby'" "$aliases_file" \
  || fail "rbenv-ruby alias must explicitly invoke the rbenv-managed Ruby"
rg -F -q "alias nvm-node='nvm exec --silent default node'" "$aliases_file" \
  || fail "nvm-node alias must explicitly invoke the NVM default Node"

rg -F -q 'for _d in' "$path_file" \
  || fail "PATH config must iterate over direct children of ~/.local/nbin"
rg -F -q 'LC_ALL=C' "$path_file" \
  || fail "PATH config must use LC_ALL=C for deterministic directory scanning"
if rg -F -q '$HOME/ai/agent-central/bin' "$path_file"; then
  fail "PATH config must not consume the non-authoritative ~/ai view"
fi
nix_path_offset="$(rg -n -F '_path_add "/run/current-system/sw/bin"' "$path_file" | cut -d: -f1)"
homebrew_path_offset="$(rg -n -F '_path_add "/opt/homebrew/bin"' "$path_file" | cut -d: -f1)"
[ "$nix_path_offset" -gt 0 ] && [ "$homebrew_path_offset" -gt 0 ] && [ "$nix_path_offset" -lt "$homebrew_path_offset" ] \
  || fail "PATH config must rank Nix before Homebrew"
shell_home="$BATS_TEST_TMPDIR/agent-path-home"
mkdir -p "$shell_home/.local/nbin/dinas" "$shell_home/.local/nbin/alpha" \
  "$shell_home/.local/nbin/unsafe name" "$shell_home/.local/nbin_dev/devns"
expected_agent_path_prefix="$shell_home/.local/nbin/alpha:$shell_home/.local/nbin/dinas:$shell_home/.local/share/agentic-praxis-grimoire/bin:"
for shell_name in bash zsh; do
  run env -i HOME="$shell_home" PATH=/usr/bin:/bin "/bin/$shell_name" -c \
    "source '$path_file'; source '$path_file'; printf '%s' \"\$PATH\""
  [ "$status" -eq 0 ] || fail "common PATH config must source cleanly in $shell_name"
  case "$output" in
    "$expected_agent_path_prefix"*) ;;
    *) fail "common PATH config must expose children of nbin in $shell_name (got $output)" ;;
  esac
  case "$output" in
    *":$shell_home/.local/nbin:"*|"$shell_home/.local/nbin:"*) fail "PATH config must not include ~/.local/nbin root" ;;
    *":$shell_home/.local/nbin_dev"*|*"$shell_home/.local/nbin_dev"*) fail "PATH config must not include ~/.local/nbin_dev root" ;;
    *"$shell_home/.local/nbin/unsafe name"*) fail "PATH config must reject invalid namespace children" ;;
  esac
  path_count=0
  old_ifs=$IFS
  IFS=:
  for path_entry in $output; do
    [ "$path_entry" = "$shell_home/.local/nbin/alpha" ] && path_count=$((path_count + 1))
  done
  IFS=$old_ifs
  [ "$path_count" -eq 1 ] || fail "PATH config must deduplicate nbin children when re-sourced"
done
if rg -F -q 'pyenv init' "$pyenv_file"; then
  fail "pyenv config must not initialize pyenv shims globally"
fi
rg -F -q 'export PYENV_ROOT="$HOME/.pyenv"' "$pyenv_file" \
  || fail "pyenv config must keep PYENV_ROOT available"
if rg -F -q 'rbenv init' "$rbenv_file"; then
  fail "rbenv config must not initialize rbenv shims globally"
fi
rg -F -q 'export RBENV_ROOT="$HOME/.rbenv"' "$rbenv_file" \
  || fail "rbenv config must keep RBENV_ROOT available"

shell_home="$BATS_TEST_TMPDIR/ruby-path-home"
mkdir -p "$shell_home"
run env -i HOME="$shell_home" PATH=/usr/bin:/bin bash -c "
  rbenv() {
    if [ \"\$1\" = init ]; then
      printf 'export PATH=\"%s/.rbenv/shims:\${PATH}\"\\n' \"\$HOME\"
    fi
  }
  source '$path_file'
  source '$rbenv_file'
  printf '%s' \"\$PATH\"
"
[ "$status" -eq 0 ] || fail "common PATH and rbenv config must source cleanly"
expected_nix_path_prefix="$shell_home/.local/share/agentic-praxis-grimoire/bin:/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:"
case "$output" in
  "$expected_nix_path_prefix"*) ;;
  *) fail "rbenv config must preserve Nix-managed PATH precedence (got $output)" ;;
esac

run env -i HOME="$shell_home" PATH=/usr/bin:/bin bash -O expand_aliases -c "
  rbenv() {
    case \"\$1\" in
      global)
        printf '4.0.5\\n'
        ;;
      exec)
        shift
        [ \"\$1\" = ruby ] || return 1
        printf 'rbenv-ruby=%s\\n' \"\$RBENV_VERSION\"
        ;;
    esac
  }
  source '$aliases_file'
  eval 'rbenv-ruby --version'
"
[ "$status" -eq 0 ] || fail "rbenv-ruby alias must invoke rbenv successfully"
[ "$output" = "rbenv-ruby=4.0.5" ] \
  || fail "rbenv-ruby alias must select the globally configured rbenv Ruby"

node_shell_home="$BATS_TEST_TMPDIR/node-path-home"
fake_nvm_script="$BATS_TEST_TMPDIR/nvm.sh"
fake_nvm_node_bin="$node_shell_home/.nvm/versions/node/v24.0.0/bin"
mkdir -p "$fake_nvm_node_bin"
cat > "$fake_nvm_script" <<'TEST'
export PATH="$NVM_DIR/versions/node/v24.0.0/bin:$PATH"
TEST

run env -i \
  HOME="$node_shell_home" \
  PATH=/usr/bin:/bin \
  NVM_HOMEBREW_SCRIPT="$fake_nvm_script" \
  bash -c "
    source '$nvm_file'
    source '$path_file'
    printf '%s' \"\$PATH\"
  "
[ "$status" -eq 0 ] || fail "common NVM and PATH config must source cleanly"
expected_node_path_prefix="$node_shell_home/.local/share/agentic-praxis-grimoire/bin:/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:"
case "$output" in
  "$expected_node_path_prefix"*) ;;
  *) fail "common PATH config must keep Nix-managed Node ahead of NVM Node" ;;
esac
remaining_node_path="${output#"$expected_node_path_prefix"}"
case ":$remaining_node_path:" in
  *":$fake_nvm_node_bin:"*) ;;
  *) fail "common PATH config must retain the NVM Node path after the Nix-managed prefix" ;;
esac

run env -i HOME="$node_shell_home" PATH=/usr/bin:/bin \
  bash -O expand_aliases -c "
    nvm() {
      [ \"\$1\" = exec ] || return 1
      [ \"\$2\" = --silent ] || return 1
      [ \"\$3\" = default ] || return 1
      [ \"\$4\" = node ] || return 1
      printf 'nvm-node=%s\n' \"\$3\"
    }
    source '$aliases_file'
    eval 'nvm-node --version'
  "
[ "$status" -eq 0 ] || fail "nvm-node alias must invoke NVM successfully"
[ "$output" = "nvm-node=default" ] \
  || fail "nvm-node alias must select NVM's default Node"

rg -F -q '/Applications/OrbStack.app/Contents/MacOS/xbin' "$path_file" \
  || fail "PATH config must expose OrbStack bundled Docker tools"
rg -F -q 'export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-$HOME/.cache/python/__pycache__}"' "$python_cache_file" \
  || fail "Python cache config must default bytecode caches to ~/.cache/python/__pycache__"
rg -F -q '[ -n "${ZSH_VERSION:-}" ]' "$env_snapshot_file" \
  || fail "env snapshot config must detect zsh from the running shell"
rg -F -q '[ -n "${BASH_VERSION:-}" ]' "$env_snapshot_file" \
  || fail "env snapshot config must detect bash from the running shell"
rg -F -q 'env-snapshot --mode sync --shell "$_nix_darwin_snapshot_shell" --context global' "$env_snapshot_file" \
  || fail "env snapshot config must call env-snapshot in sync mode"
rg -F -q 'add-zsh-hook precmd _nix_darwin_env_snapshot' "$env_snapshot_file" \
  || fail "env snapshot config must install a zsh prompt hook"
rg -F -q 'PROMPT_COMMAND="_nix_darwin_env_snapshot; ${PROMPT_COMMAND:-}"' "$env_snapshot_file" \
  || fail "env snapshot config must install a bash prompt hook"
rg -F -q 'export REPOMAP_HOME="${REPOMAP_HOME:-/Users/operator/ai/agent-central/codex/mcp/repo-map}"' "$repo_map_file" \
  || fail "RepoMap config must default REPOMAP_HOME to the agent-central Codex MCP repo-map home"
run env -i bash -c "source '$repo_map_file'; [ \"\$REPOMAP_HOME\" = '/Users/operator/ai/agent-central/codex/mcp/repo-map' ]" \
  || fail "RepoMap config must set REPOMAP_HOME when unset"
run env -i REPOMAP_HOME=/tmp/custom-repo-map-home bash -c "source '$repo_map_file'; [ \"\$REPOMAP_HOME\" = '/tmp/custom-repo-map-home' ]" \
  || fail "RepoMap config must preserve explicit REPOMAP_HOME overrides"

shell_home="$BATS_TEST_TMPDIR/shell-home"
mkdir -p "$shell_home/.sh_config/common" "$shell_home/.sh_config/bash" "$shell_home/.sh_config/zsh"
ln -s "$jobs_file" "$shell_home/.sh_config/common/012_nix-darwin-jobs"
: > "$shell_home/.sh_config/common/999_on-login"

for shell_name in bash zsh; do
  shell_rc_file="$repo_dir/shell/${shell_name}rc"
  run env -i HOME="$shell_home" PATH=/usr/bin:/bin "/bin/$shell_name" -c \
    "source '$shell_rc_file'; printf '%s %s' \"\$NIX_DARWIN_TEST_JOBS\" \"\$NIX_DARWIN_BUILD_JOBS\""
  [ "$status" -eq 0 ] || fail "$shell_name startup must source common nix-darwin job defaults"
  [ "$output" = "8 4" ] || fail "$shell_name startup must default nix-darwin jobs to 8 tests and 4 builds"

  run env -i HOME="$shell_home" PATH=/usr/bin:/bin \
    NIX_DARWIN_TEST_JOBS=3 NIX_DARWIN_BUILD_JOBS=2 \
    "/bin/$shell_name" -c \
    "source '$shell_rc_file'; printf '%s %s' \"\$NIX_DARWIN_TEST_JOBS\" \"\$NIX_DARWIN_BUILD_JOBS\""
  [ "$status" -eq 0 ] || fail "$shell_name startup must accept explicit nix-darwin job overrides"
  [ "$output" = "3 2" ] || fail "$shell_name startup must preserve explicit nix-darwin job overrides"
done

expected_on_login='_nix_darwin_env_snapshot_install_prompt_hook 2>/dev/null || true
echo
ff'
actual_on_login="$(cat "$on_login_file")"
[ "$actual_on_login" = "$expected_on_login" ] \
  || fail "999_on-login must install prompt snapshots, then print a blank line followed by ff"

echo "shell config tests passed"

}
