#!/usr/bin/env bash
set -euo pipefail

command_name="${VMWARE_FUSION_WRAPPER_NAME:-${0##*/}}"

case "$command_name" in
  vmcli) relative_path="Contents/Library/vmcli" ;;
  vmrest) relative_path="Contents/Library/vmrest" ;;
  vmrun) relative_path="Contents/Library/vmrun" ;;
  ovftool) relative_path="Contents/Library/VMware OVF Tool/ovftool" ;;
  *)
    echo "$command_name: unsupported VMware Fusion wrapper name" >&2
    exit 64
    ;;
esac

app="${VMWARE_FUSION_APP:-/Applications/VMware Fusion.app}"
bin="$app/$relative_path"

if [ ! -x "$bin" ]; then
  echo "$command_name: VMware Fusion is not installed at $app" >&2
  echo "Install VMware Fusion manually from Broadcom. Homebrew's vmware-fusion cask is disabled because it requires an authenticated download." >&2
  exit 127
fi

exec "$bin" "$@"
