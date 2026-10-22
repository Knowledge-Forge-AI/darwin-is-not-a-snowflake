#!/usr/bin/env bats

@test "bash-theme.unit.test" {
set -euo pipefail

script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
repo_dir="$(cd "$script_dir/../../../.." && pwd)"

fail() {
  echo "test-bash-theme: $*" >&2
  exit 1
}

theme_file="$repo_dir/shell/sh_config/bash/steeef-custom.bash-theme"
bash_profile_file="$repo_dir/shell/bash_profile"
bashrc_file="$repo_dir/shell/bashrc"
last_login_file="$repo_dir/shell/sh_config/bash/000_last-login"
zshrc_file="$repo_dir/shell/zshrc"

[ -f "$theme_file" ] || fail "missing Bash steeef theme"
[ ! -e "$last_login_file" ] \
  || fail "bash must not polyfill macOS last-login; the launcher should use /usr/bin/login"
if rg -F -q '.sh_config/bash/000_last-login' "$bash_profile_file" "$bashrc_file"; then
  fail "Bash startup files must not source the removed last-login polyfill"
fi
rg -F -q '.bashrc' "$bash_profile_file" \
  || fail "bash_profile must still run bashrc"
rg -F -q 'for file in "$HOME"/.sh_config/bash/*' "$bashrc_file" \
  || fail "bashrc must source files from ~/.sh_config/bash"
rg -F -q '.sh_config/common/999_on-login' "$bashrc_file" \
  || fail "bashrc must run common/999_on-login after shell-specific setup"
bash_common_line="$(rg -n -F 'for file in "$HOME"/.sh_config/common/*' "$bashrc_file" | head -1 | cut -d: -f1)"
bash_specific_line="$(rg -n -F 'for file in "$HOME"/.sh_config/bash/*' "$bashrc_file" | head -1 | cut -d: -f1)"
bash_on_login_line="$(rg -n -F '.sh_config/common/999_on-login' "$bashrc_file" | head -1 | cut -d: -f1)"
[ "$bash_common_line" -lt "$bash_specific_line" ] && [ "$bash_specific_line" -lt "$bash_on_login_line" ] \
  || fail "bashrc must source common setup, bash setup, then common/999_on-login"
rg -F -q '.sh_config/common/999_on-login' "$zshrc_file" \
  || fail "zshrc must run common/999_on-login after shell-specific setup"
zsh_specific_line="$(rg -n -F '.sh_config/zsh/*' "$zshrc_file" | head -1 | cut -d: -f1)"
zsh_on_login_line="$(rg -n -F '.sh_config/common/999_on-login' "$zshrc_file" | head -1 | cut -d: -f1)"
[ "$zsh_specific_line" -lt "$zsh_on_login_line" ] \
  || fail "zshrc must source zsh setup before common/999_on-login"
rg -F -q 'PROMPT_COMMAND=steeef_prompt_command' "$theme_file" \
  || fail "Bash steeef theme must install its prompt command"
rg -F -q 'steeef_set_window_title' "$theme_file" \
  || fail "Bash steeef theme must set a zsh-style window title"
if rg -F -q 'steeef_prompt_window_title' "$theme_file"; then
  fail "Bash steeef theme must not embed title escapes in PS1; PROMPT_COMMAND owns the title"
fi
rg -F -q '\033]0;' "$theme_file" \
  || fail "Bash steeef theme must emit an OSC title sequence"
rg -F -q '38;5;220' "$theme_file" \
  || fail "Bash steeef theme must use the same light amber as zsh"
rg -F -q '38;5;81' "$theme_file" \
  || fail "Bash steeef theme must use the same turquoise branch color as zsh"
rg -F -q 'VIRTUAL_ENV_DISABLE_PROMPT=1' "$theme_file" \
  || fail "Bash steeef theme must own virtualenv prompt rendering"
if rg -F -q 'steeef_print_last_login_once' "$theme_file"; then
  fail "Bash steeef theme must not print last-login after common/999_on-login"
fi
if rg -F -q 'bash_print_last_login_once' "$theme_file" "$bash_profile_file" "$bashrc_file"; then
  fail "Bash startup must not keep the stale last-login polyfill"
fi

test_tmp_root="$(mktemp -d)"
cleanup() {
  rm -rf "$test_tmp_root"
}
trap cleanup EXIT

repo="$test_tmp_root/repo"
mkdir -p "$repo"
git -C "$repo" init -b main >/dev/null
git -C "$repo" config user.email "codex@example.invalid"
git -C "$repo" config user.name "Codex"
printf 'hello\n' > "$repo/tracked.txt"
git -C "$repo" add tracked.txt
git -C "$repo" commit -m 'initial' >/dev/null

(
  cd "$repo"
  # shellcheck disable=SC1090
  source "$theme_file"

  declare -F steeef_prompt_command >/dev/null \
    || fail "steeef_prompt_command must be defined"
  declare -F steeef_git_info >/dev/null \
    || fail "steeef_git_info must be defined"
  declare -F steeef_virtualenv_info >/dev/null \
    || fail "steeef_virtualenv_info must be defined"
  declare -F steeef_set_window_title >/dev/null \
    || fail "steeef_set_window_title must be defined"
  declare -F steeef_title_path >/dev/null \
    || fail "steeef_title_path must be defined"
  [ "${PROMPT_COMMAND:-}" = "steeef_prompt_command" ] \
    || fail "Bash steeef theme must set PROMPT_COMMAND"

  HOME="$repo"
  title_path="$(steeef_title_path)"
  [ "$title_path" = "~" ] \
    || fail "window title path must compact HOME to ~"
  title_output="$(steeef_set_window_title)"
  [[ "$title_output" == $'\033]0;'* ]] \
    || fail "window title must start with an OSC title escape"
  [[ "$title_output" == *"${USER:-operator}@"* ]] \
    || fail "window title must include the current user"
  [[ "$title_output" == *":~"* ]] \
    || fail "window title must include compact cwd"

  clean_git_info="$(steeef_git_info)"
  [[ "$clean_git_info" == *main* ]] \
    || fail "clean Git prompt must include the branch name"
  [[ "$clean_git_info" != *●* ]] \
    || fail "clean Git prompt must not include dirty-state markers"

  printf 'changed\n' >> tracked.txt
  git -C "$repo" add tracked.txt
  printf 'more\n' >> tracked.txt
  printf 'new\n' > untracked.txt
  dirty_git_info="$(steeef_git_info)"
  [[ "$dirty_git_info" == *main* ]] \
    || fail "dirty Git prompt must include the branch name"
  [ "$(printf '%s' "$dirty_git_info" | awk -F '●' '{ print NF - 1 }')" -eq 3 ] \
    || fail "dirty Git prompt must include unstaged, staged, and untracked markers"

  VIRTUAL_ENV="/tmp/example-venv"
  venv_info="$(steeef_virtualenv_info)"
  [[ "$venv_info" == *example-venv* ]] \
    || fail "virtualenv prompt must include the environment basename"

  prompt_output_file="$test_tmp_root/prompt-output"
  steeef_prompt_command > "$prompt_output_file"
  prompt_command_output="$(cat "$prompt_output_file")"
  [[ "$prompt_command_output" == $'\033]0;'* ]] \
    || fail "PROMPT_COMMAND must emit the window title before drawing the prompt"
  [[ "$PS1" == *'\w'* ]] \
    || fail "PS1 must include Bash's compact working-directory escape"
  if [[ "$PS1" == *$'\033]0;'* ]]; then
    fail "PS1 must not contain embedded title escapes"
  fi
  if [[ "$PS1" == *'$(steeef_git_info)'* ]]; then
    fail "PS1 must be rendered by PROMPT_COMMAND, not by nested command substitutions"
  fi
)

echo "bash theme tests passed"

}
