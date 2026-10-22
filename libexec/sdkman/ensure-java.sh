#!/usr/bin/env bash
set -euo pipefail

: "${SDKMAN_DIR:?SDKMAN_DIR must be set}"

source_sdkman() {
  set +u
  source "$SDKMAN_DIR/bin/sdkman-init.sh"
  sdkman_init_status=$?
  set -u
  return "$sdkman_init_status"
}

source_sdkman
if ! type sdk >/dev/null 2>&1; then
  echo "SDKMAN is installed but the sdk command is not available after init." >&2
  exit 1
fi

run_sdk() {
  set +u
  sdk "$@"
  sdkman_command_status=$?
  set -u
  return "$sdkman_command_status"
}

current_java="$SDKMAN_DIR/candidates/java/current"
existing_default=""
if [ -x "$current_java/bin/java" ]; then
  if [ -L "$current_java" ]; then
    existing_default="$(basename "$(readlink "$current_java")")"
  else
    existing_default="$(basename "$current_java")"
  fi
fi

java_catalog="$(
  run_sdk list java 2>/dev/null |
    sed -E 's/\x1b\[[0-9;]*[mK]//g' ||
    true
)"

resolve_corretto_version() {
  major="$1"
  fallback="$2"
  java_version="$(
    printf '%s\n' "$java_catalog" |
      awk -v major="$major" \
        '$NF ~ "^" major "\\..*-amzn$" { print $NF; exit }'
  )"

  if [ -n "$java_version" ]; then
    printf '%s\n' "$java_version"
  else
    printf '%s\n' "$fallback"
  fi
}

has_installed_jdk() {
  [ -x "$SDKMAN_DIR/candidates/java/$1/bin/java" ]
}

ensure_corretto_major() {
  major="$1"
  fallback="$2"
  java_version="$(resolve_corretto_version "$major" "$fallback")"

  if has_installed_jdk "$java_version"; then
    echo "Amazon Corretto $java_version is already installed." >&2
  else
    echo "installing Amazon Corretto $java_version with SDKMAN..." >&2
    printf 'n\n' | USE=n sdkman_auto_answer=true run_sdk install java "$java_version"
  fi

  resolved_java_version="$java_version"
}

export sdkman_auto_answer=true

ensure_corretto_major 25 25.0.3-amzn
java25="$resolved_java_version"
ensure_corretto_major 21 21.0.11-amzn
ensure_corretto_major 17 17.0.19-amzn
ensure_corretto_major 11 11.0.31-amzn
ensure_corretto_major 8 8.0.472-amzn

if [ -n "$existing_default" ]; then
  echo "restoring existing SDKMAN default Java: $existing_default"
  run_sdk default java "$existing_default"
else
  echo "setting SDKMAN default Java to Amazon Corretto $java25"
  run_sdk default java "$java25"
fi
