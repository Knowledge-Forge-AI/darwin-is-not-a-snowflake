#!/usr/bin/env bats

@test "zsh-theme.unit.test" {
set -euo pipefail

script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
repo_dir="$(cd "$script_dir/../../../.." && pwd)"

fail() {
  echo "test-zsh-theme: $*" >&2
  exit 1
}

theme_file="$repo_dir/shell/oh-my-zsh-custom/themes/steeef-custom.zsh-theme"

[ -f "$theme_file" ] || fail "missing steeef custom theme"

zsh -n "$theme_file" || fail "theme must pass zsh syntax checks"

rg -F -q "autoload -Uz vcs_info" "$theme_file" \
  || fail "theme must use vcs_info"
rg -F -q "zstyle ':vcs_info:*:prompt:*' check-for-changes true" "$theme_file" \
  || fail "theme must let vcs_info detect staged and unstaged changes"
rg -F -q "zstyle ':vcs_info:git+set-message:*' hooks git-untracked" "$theme_file" \
  || fail "theme must use a vcs_info set-message hook for untracked git files"
rg -F -q "function +vi-git-untracked" "$theme_file" \
  || fail "theme must define the registered git untracked hook"
rg -q 'FMT_BRANCH=.*%m' "$theme_file" \
  || fail "theme branch format must leave untracked status to the %m hook field"

if rg -F -q "PR_GIT_UPDATE" "$theme_file"; then
  fail "theme must not keep the old prompt cache invalidation workaround"
fi
if rg -F -q "git ls-files --other --exclude-standard" "$theme_file"; then
  fail "theme must not use the old manual git ls-files untracked workaround"
fi
if rg -F -q "steeef_preexec" "$theme_file"; then
  fail "theme must not keep the old git-command preexec hook"
fi
if rg -F -q "steeef_chpwd" "$theme_file"; then
  fail "theme must not keep the old directory-change cache hook"
fi
if rg -F -q 'zstyle '\'':vcs_info:*:prompt:*'\'' formats "${FMT_BRANCH} "' "$theme_file"; then
  fail "theme must not mutate vcs_info formats inside precmd"
fi

tmp_dir="$(mktemp -d /private/tmp/test-zsh-theme.XXXXXX)"
trap 'rm -rf "$tmp_dir"' EXIT

git -C "$tmp_dir" init -q
git -C "$tmp_dir" config user.name "Codex Test"
git -C "$tmp_dir" config user.email "codex@example.invalid"
printf 'tracked\n' > "$tmp_dir/tracked.txt"
git -C "$tmp_dir" add tracked.txt
git -C "$tmp_dir" commit -q -m "initial"
printf 'untracked\n' > "$tmp_dir/untracked.txt"

prompt_vcs="$(
  THEME_FILE="$theme_file" TEST_REPO="$tmp_dir" zsh -f <<'ZSH'
cd "$TEST_REPO" || exit 1
source "$THEME_FILE" || exit 2
steeef_precmd
print -r -- "$vcs_info_msg_0_"
ZSH
)"

case "$prompt_vcs" in
  *"●"*) ;;
  *) fail "theme must show the untracked marker through the vcs_info hook; got: $prompt_vcs" ;;
esac

echo "zsh theme tests passed"

}
