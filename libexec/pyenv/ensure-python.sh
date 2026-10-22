set -eo pipefail

export PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}"
mkdir -p "$PYENV_ROOT/versions"

if [ -d "$PYENV_ROOT/versions" ] && find "$PYENV_ROOT/versions" -mindepth 1 -maxdepth 1 -type d | grep -q .; then
  echo "pyenv is already managing Python versions."
  exit 0
fi

# Python does not have an LTS channel, so use latest known stable CPython 3.x.
python_version="$(pyenv latest -k 3)"

echo "installing Python $python_version with pyenv..."
pyenv install -s "$python_version"
pyenv global "$python_version"
pyenv rehash
