set -eo pipefail

export RBENV_ROOT="${RBENV_ROOT:-$HOME/.rbenv}"
mkdir -p "$RBENV_ROOT/versions"

if [ -d "$RBENV_ROOT/versions" ] && find "$RBENV_ROOT/versions" -mindepth 1 -maxdepth 1 -type d | grep -q .; then
  echo "rbenv is already managing Ruby versions."
  exit 0
fi

ruby_version="$(
  rbenv install --list \
    | awk -f "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/latest-stable-ruby-version.awk"
)"

if [ -z "$ruby_version" ]; then
  echo "could not determine latest stable Ruby version from rbenv install --list." >&2
  exit 1
fi

if command -v brew >/dev/null 2>&1; then
  openssl_prefix="$(brew --prefix openssl@3 2>/dev/null || true)"
  if [ -n "$openssl_prefix" ]; then
    export RUBY_CONFIGURE_OPTS="--with-openssl-dir=$openssl_prefix ${RUBY_CONFIGURE_OPTS:-}"
  fi
fi

echo "installing Ruby $ruby_version with rbenv..."
rbenv install -s "$ruby_version"
rbenv global "$ruby_version"
rbenv rehash
