# Adapted from virtual-machines_dinas 863fe6c, bash/early.bash.
# Only the post-login stock entry owns this non-exported shell-local token.
[[ ${_dinas_stock_owner-} == "$BASHPID" && $- == *i* ]] || return 0
_dinas_enhanced() {
  [[ -z ${DINAS_PLAIN_BASH-} && -z ${BASH_EXECUTION_STRING+set} ]] &&
    [[ -t 0 && -t 1 && -n ${TERM-} && $TERM != dumb && -z ${SSH_CONNECTION-}${SSH_ORIGINAL_COMMAND-} ]]
}
if _dinas_enhanced && [[ -z ${_dinas_stock_early_loaded-} ]]; then
  _dinas_stock_early_loaded=1
  if [[ -r @BLESH@ ]]; then
    if ! source @BLESH@ --noattach; then
      declare -F ble-detach >/dev/null && ble-detach || true
      unset BLE_VERSION BLE_ATTACHED
    fi
  fi
fi
