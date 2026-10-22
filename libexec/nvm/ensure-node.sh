set -eo pipefail

export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
mkdir -p "$NVM_DIR"

nvm_script=""
candidate_nvm_scripts=()
if [ -n "${NVM_SCRIPT:-}" ]; then
  candidate_nvm_scripts+=("$NVM_SCRIPT")
fi
candidate_nvm_scripts+=(/opt/homebrew/opt/nvm/nvm.sh)
candidate_nvm_scripts+=("$NVM_DIR/nvm.sh")

for candidate in "${candidate_nvm_scripts[@]}"; do
  if [ -s "$candidate" ]; then
    nvm_script="$candidate"
    break
  fi
done

if [ -z "$nvm_script" ]; then
  echo "nvm is not available yet; rerun nix-rebuild after Homebrew installs it." >&2
  exit 0
fi

. "$nvm_script"

if [ -d "$NVM_DIR/versions/node" ] && find "$NVM_DIR/versions/node" -mindepth 1 -maxdepth 1 -type d | grep -q .; then
  echo "nvm is already managing Node versions."
  exit 0
fi

echo "installing latest Node LTS with nvm..."
nvm install --lts
nvm alias default 'lts/*'
nvm use default
