#!/usr/bin/env bats
# nix-darwin-test-source-target: modules/user-home-writer-common.sh
# nix-darwin-test-source-target: modules/shell-writer.sh
# nix-darwin-test-source-target: modules/fastfetch-writer.sh
# nix-darwin-test-source-target: modules/oh-my-zsh-writer.sh
# nix-darwin-test-source-target: modules/runtime-state-writer.sh
# nix-darwin-test-source-target: modules/sdkman-config-writer.sh

setup() {
  repo_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../../.." && pwd)"
  common="$repo_dir/modules/user-home-writer-common.sh"
  test_user="$(/usr/bin/id -un)"
  test_home="$BATS_TEST_TMPDIR/home"
  mkdir -m 0755 "$test_home"
}

make_sources() {
  sources="$BATS_TEST_TMPDIR/sources"
  mkdir "$sources"
  for name in zshrc bashrc bash-profile sh-config nbin default terminal kitty logo omz omz-custom sdkman-config; do
    printf '%s\n' "$name" >"$sources/$name"
  done
}

fingerprint() {
  /usr/bin/stat -f '%u:%g:%Lp:%z' "$1"
  /usr/bin/shasum -a 256 "$1"
}

@test "shell writer preserves conflicts, replaces links only, and is repeat-stable" {
  make_sources
  printf 'user zshrc\n' >"$test_home/.zshrc"
  printf 'outside\n' >"$BATS_TEST_TMPDIR/outside"
  ln -s "$BATS_TEST_TMPDIR/outside" "$test_home/.bashrc"
  before="$(fingerprint "$BATS_TEST_TMPDIR/outside")"

  run /bin/sh "$repo_dir/modules/shell-writer.sh" "$common" "$test_user" "$test_home" \
    "$sources/zshrc" "$sources/bashrc" "$sources/bash-profile" "$sources/sh-config"
  [ "$status" -eq 0 ]
  [ "$(readlink "$test_home/.zshrc")" = "$sources/zshrc" ]
  [ "$(<"$test_home/.zshrc.before-nix-darwin")" = "user zshrc" ]
  [ "$(readlink "$test_home/.bashrc")" = "$sources/bashrc" ]
  [ "$(readlink "$test_home/.bash_profile")" = "$sources/bash-profile" ]
  [ "$(readlink "$test_home/.sh_config")" = "$sources/sh-config" ]
  [ "$(fingerprint "$BATS_TEST_TMPDIR/outside")" = "$before" ]

  backups_before="$(find "$test_home" -name '*.before-nix-darwin*' -print | sort)"
  run /bin/sh "$repo_dir/modules/shell-writer.sh" "$common" "$test_user" "$test_home" \
    "$sources/zshrc" "$sources/bashrc" "$sources/bash-profile" "$sources/sh-config"
  [ "$status" -eq 0 ]
  [ "$(find "$test_home" -name '*.before-nix-darwin*' -print | sort)" = "$backups_before" ]
}

@test "shell writer fails safely when the local root is a symlink" {
  make_sources
  sentinel="$BATS_TEST_TMPDIR/sentinel"
  mkdir "$sentinel"
  printf 'protected\n' >"$sentinel/value"
  before="$(fingerprint "$sentinel/value")"
  ln -s "$sentinel" "$test_home/.local"

  run /bin/sh "$repo_dir/modules/shell-writer.sh" "$common" "$test_user" "$test_home" \
    "$sources/zshrc" "$sources/bashrc" "$sources/bash-profile" "$sources/sh-config"
  [ "$status" -ne 0 ]
  [ "$(fingerprint "$sentinel/value")" = "$before" ]
}

@test "Fastfetch writer preserves conflicts, removes only stale links, and rejects config-root substitution" {
  make_sources
  mkdir -p "$test_home/.config/fastfetch"
  printf 'user config\n' >"$test_home/.config/fastfetch/config.jsonc"
  printf 'unrelated\n' >"$test_home/.config/fastfetch/unrelated"
  ln -s "$BATS_TEST_TMPDIR/nowhere" "$test_home/.config/fastfetch/logo.ascii"
  writer="$repo_dir/modules/fastfetch-writer.sh"

  run /bin/sh "$writer" "$common" "$test_user" "$test_home" \
    "$sources/default" "$sources/terminal" "$sources/kitty" "$sources/logo" fastfetch
  [ "$status" -eq 0 ]
  [ "$(<"$test_home/.config/fastfetch/config.jsonc.before-nix-darwin")" = "user config" ]
  [ "$(<"$test_home/.config/fastfetch/unrelated")" = unrelated ]
  [ ! -L "$test_home/.config/fastfetch/logo.ascii" ]
  [ "$(readlink "$test_home/.config/fastfetch/logo.png")" = "$sources/logo" ]

  rm -rf "$test_home/.config"
  sentinel="$BATS_TEST_TMPDIR/config-sentinel"
  mkdir "$sentinel"
  printf 'protected\n' >"$sentinel/value"
  before="$(fingerprint "$sentinel/value")"
  ln -s "$sentinel" "$test_home/.config"
  run /bin/sh "$writer" "$common" "$test_user" "$test_home" \
    "$sources/default" "$sources/terminal" "$sources/kitty" "$sources/logo" fastfetch
  [ "$status" -ne 0 ]
  [ "$(fingerprint "$sentinel/value")" = "$before" ]
}

@test "Oh My Zsh writer preserves conflicts and creates an ordinary cache" {
  make_sources
  mkdir "$test_home/.oh-my-zsh"
  printf 'user content\n' >"$test_home/.oh-my-zsh/value"
  writer="$repo_dir/modules/oh-my-zsh-writer.sh"

  run /bin/sh "$writer" "$common" "$test_user" "$test_home" "$sources/omz" "$sources/omz-custom"
  [ "$status" -eq 0 ]
  [ "$(readlink "$test_home/.oh-my-zsh")" = "$sources/omz" ]
  [ "$(<"$test_home/.oh-my-zsh.before-nix-darwin/value")" = "user content" ]
  [ "$(readlink "$test_home/.oh-my-zsh-custom")" = "$sources/omz-custom" ]
  [ -d "$test_home/.cache/oh-my-zsh" ]
  run /bin/sh "$writer" "$common" "$test_user" "$test_home" "$sources/omz" "$sources/omz-custom"
  [ "$status" -eq 0 ]
}

@test "runtime state writer owns NVM rbenv and pyenv creation behind one identity guard" {
  writer="$repo_dir/modules/runtime-state-writer.sh"
  marker="$BATS_TEST_TMPDIR/helper-ran"
  helper="$BATS_TEST_TMPDIR/helper"
  cat >"$helper" <<'EOF'
#!/bin/sh
printf 'ran\n' >"$RUNTIME_MARKER"
EOF
  chmod +x "$helper"

  for runtime in nvm rbenv pyenv; do
    root="$test_home/.$runtime"
    run env RUNTIME_MARKER="$marker-$runtime" /bin/sh "$writer" "$common" "$test_user" "$test_home" \
      "$runtime" "$root" -- /bin/sh "$helper"
    [ "$status" -eq 0 ]
    [ "$(<"$marker-$runtime")" = ran ]
    [ -d "$root" ]
  done
  [ -d "$test_home/.rbenv/versions" ]
  [ -d "$test_home/.pyenv/versions" ]
  [ -d "$test_home/.pyenv/shims" ]

  rm -rf "$test_home/.nvm"
  sentinel="$BATS_TEST_TMPDIR/runtime-sentinel"
  mkdir "$sentinel"
  printf 'protected\n' >"$sentinel/value"
  before="$(fingerprint "$sentinel/value")"
  ln -s "$sentinel" "$test_home/.nvm"
  run env RUNTIME_MARKER="$marker-bad" /bin/sh "$writer" "$common" "$test_user" "$test_home" \
    nvm "$test_home/.nvm" -- /bin/sh "$helper"
  [ "$status" -ne 0 ]
  [ "$(fingerprint "$sentinel/value")" = "$before" ]
  [ ! -e "$marker-bad" ]
}

@test "SDKMAN writer keeps bootstrap config and ensure-java in one user-owned flow" {
  make_sources
  bootstrap="$BATS_TEST_TMPDIR/bootstrap"
  ensure="$BATS_TEST_TMPDIR/ensure"
  cat >"$bootstrap" <<'EOF'
#!/bin/sh
mkdir -p "$SDKMAN_DIR/etc"
EOF
  cat >"$ensure" <<'EOF'
#!/bin/sh
printf 'ensure-java\n' >>"$SDKMAN_TEST_LOG"
EOF
  chmod +x "$bootstrap" "$ensure"
  log="$BATS_TEST_TMPDIR/sdkman.log"
  writer="$repo_dir/modules/sdkman-config-writer.sh"

  run env SDKMAN_DIR="$test_home/.sdkman" SDKMAN_TEST_LOG="$log" \
    /bin/bash "$writer" "$common" "$test_user" "$test_home" "$sources/sdkman-config" \
    "$bootstrap" "$ensure" /bin/bash
  [ "$status" -eq 0 ]
  [ "$(readlink "$test_home/.sdkman/etc/config")" = "$sources/sdkman-config" ]
  [ "$(<"$log")" = ensure-java ]

  rm "$test_home/.sdkman/etc/config"
  printf 'user config\n' >"$test_home/.sdkman/etc/config"
  run env SDKMAN_DIR="$test_home/.sdkman" SDKMAN_TEST_LOG="$log" \
    /bin/bash "$writer" "$common" "$test_user" "$test_home" "$sources/sdkman-config" \
    "$bootstrap" "$ensure" /bin/bash
  [ "$status" -eq 0 ]
  [ "$(<"$test_home/.sdkman/etc/config.before-nix-darwin")" = "user config" ]
  [ "$(readlink "$test_home/.sdkman/etc/config")" = "$sources/sdkman-config" ]
}
