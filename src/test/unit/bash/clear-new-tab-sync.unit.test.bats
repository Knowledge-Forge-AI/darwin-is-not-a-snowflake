#!/usr/bin/env bats

@test "clear-new-tab-sync.unit.test" {
set -euo pipefail

script_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")" && pwd)"
repo_dir="$(cd "$script_dir/../../../.." && pwd)"

fail() {
  echo "test-clear-new-tab-sync: $*" >&2
  exit 1
}

sync_script="$repo_dir/bin/clear-new-tab-sync"
latest_helper="$repo_dir/libexec/clear-new-tab/latest-source-extension-dir.py"
extension_id="felphkbfjadmcejnibcmcncimlappdde"

[ -f "$sync_script" ] || fail "missing bin/clear-new-tab-sync"
[ -f "$latest_helper" ] || fail "missing libexec/clear-new-tab/latest-source-extension-dir.py"

bash -n "$sync_script" || fail "clear-new-tab-sync must be valid Bash"
python3 -c 'import pathlib, sys; compile(pathlib.Path(sys.argv[1]).read_text(), sys.argv[1], "exec")' "$latest_helper" \
  || fail "Clear New Tab latest extension helper must compile"

rg -F -q '/usr/bin/python3 "$(clear_new_tab_libexec_dir)/latest-source-extension-dir.py" "$root"' "$sync_script" \
  || fail "clear-new-tab-sync must delegate extension version sorting to libexec helper"
if rg -F -q '/usr/bin/python3 - "$root" <<'\''PY'\''' "$sync_script"; then
  fail "clear-new-tab-sync must not embed Python heredocs"
fi

for expected in \
  'clear_new_tab_libexec_dir()' \
  'latest_source_extension_dir()' \
  'sync_dir "$source_extension_dir" "$target_root/clear-new-tab-extension"' \
  'CLEAR_NEW_TAB_SOURCE_CHROME_PROFILE'
do
  rg -F -q "$expected" "$sync_script" \
    || fail "clear-new-tab-sync must preserve expected behavior marker: $expected"
done

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

profile="$tmp_dir/Chrome/Default"
extension_root="$profile/Extensions/$extension_id"
mkdir -p \
  "$extension_root/1.9.0" \
  "$extension_root/1.10.0" \
  "$extension_root/1.2.20"

latest="$(CLEAR_NEW_TAB_SOURCE_CHROME_PROFILE="$profile" "$sync_script" status | awk -F': ' '/^source_extension:/ { print $2 }')"
[ "$latest" = "$extension_root/1.10.0" ] \
  || fail "status must report numerically latest extension version; got $latest"

echo "Clear New Tab sync tests passed"

}
