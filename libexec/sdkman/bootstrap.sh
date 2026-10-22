#!/usr/bin/env bash
set -euo pipefail

: "${SDKMAN_DIR:?SDKMAN_DIR must be set}"

sdkman_is_usable() {
  [ -s "$SDKMAN_DIR/bin/sdkman-init.sh" ] || return 1
  set +u
  source "$SDKMAN_DIR/bin/sdkman-init.sh" >/dev/null 2>&1
  sdkman_init_status=$?
  set -u
  [ "$sdkman_init_status" -eq 0 ] || return 1
  type sdk >/dev/null 2>&1
}

if sdkman_is_usable; then
  exit 0
fi

profile_home="$(mktemp -d "${TMPDIR:-/tmp}/sdkman-bootstrap-home.XXXXXX")"
cleanup_profile_home() {
  rm -rf "$profile_home"
}
trap cleanup_profile_home EXIT

touch \
  "$profile_home/.bash_profile" \
  "$profile_home/.bashrc" \
  "$profile_home/.zshrc"

echo "installing SDKMAN..."
curl --fail --location --show-error --silent "https://get.sdkman.io" |
  HOME="$profile_home" bash

if ! sdkman_is_usable; then
  echo "SDKMAN install did not produce a usable sdk command." >&2
  exit 1
fi
