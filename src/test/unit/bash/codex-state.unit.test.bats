#!/usr/bin/env bats
# nix-darwin-test-source-target: modules/codex-state-writer.sh

setup() {
  repo_dir="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../../.." && pwd)"
  module_file="$repo_dir/modules/codex-state.nix"
  writer_file="$repo_dir/modules/codex-state-writer.sh"
}

@test "Codex state module performs one fail-fast target-user transition" {
  [ -f "$module_file" ]
  [ -f "$writer_file" ]

  run rg -F -c 'codex-state-writer.sh' "$module_file"
  [ "$status" -eq 0 ]
  [ "$output" = 1 ]

  rg -F -q 'primaryUser = config.system.primaryUser;' "$module_file"
  rg -F -q 'userHome = config.users.users.${primaryUser}.home;' "$module_file"
  rg -F -q 'target_user=${lib.escapeShellArg primaryUser}' "$module_file"
  rg -F -q 'user_home=${lib.escapeShellArg userHome}' "$module_file"
  rg -F -q '/usr/bin/sudo -n -H -u "$target_user" --' "$module_file"
  rg -F -q '/bin/sh ${./codex-state-writer.sh}' "$module_file"
  rg -F -q '"$target_user" "$user_home" "$agent_central_root" || exit 1' "$module_file"

  ! rg -q '\b(chown|chgrp|find|install|ln|mkdir|mv|rm|rmdir)\b' "$module_file"
}

@test "writer statically binds identity refusal before user-home mutation" {
  rg -F -q 'PATH=/usr/bin:/bin' "$writer_file"
  rg -F -q 'effective_uid=$(/usr/bin/id -u)' "$writer_file"
  rg -F -q 'if [ "$effective_uid" -eq 0 ]; then' "$writer_file"
  rg -F -q 'refusing to mutate user-home state as root' "$writer_file"
  rg -F -q 'if [ "$(/usr/bin/id -un)" != "$target_user" ]; then' "$writer_file"

  root_guard_line="$(rg -n -F 'if [ "$effective_uid" -eq 0 ]; then' "$writer_file" | cut -d: -f1)"
  first_state_line="$(rg -n -F 'require_owned_directory "$user_home"' "$writer_file" | cut -d: -f1)"
  [ "$root_guard_line" -lt "$first_state_line" ]

  ! rg -q '\b(chown|chgrp|sudo)\b' "$writer_file"
  ! rg -F -q '/Users/' "$writer_file"
}

@test "writer retains exact Codex and Serena migration roots and backups" {
  rg -F -q 'docs_target="$codex_vc_dir/usr"' "$writer_file"
  rg -F -q 'legacy_usr_target="$codex_dir/usr"' "$writer_file"
  rg -F -q 'legacy_docs_target="$codex_dir/user-docs"' "$writer_file"
  rg -F -q 'legacy_docs_dir="$codex_dir/$target_user/codex"' "$writer_file"
  rg -F -q 'legacy_docs_link="$user_home/codex-docs"' "$writer_file"
  rg -F -q 'docs_link="$user_home/codex"' "$writer_file"
  rg -F -q 'before-codex-docs-migration' "$writer_file"
  rg -F -q 'serena_dir="$codex_vc_mcp_dir/serena"' "$writer_file"
  rg -F -q 'serena_legacy_dir="$user_home/.serena"' "$writer_file"
  rg -F -q 'before-serena-state-migration' "$writer_file"
  rg -F -q '.before-nix-darwin' "$writer_file"
  rg -F -q 'find "$source_dir" -mindepth 1 -maxdepth 1 -print' "$writer_file"
  rg -F -q 'IFS= read -r entry' "$writer_file"
}
