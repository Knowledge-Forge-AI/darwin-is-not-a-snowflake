# Adapted from the exact VM donor's bash/interactive.bash (863fe6c).
# The host owns aliases, colours, steeef prompt, title, environment and ff.
[[ ${_dinas_stock_owner-} == "$BASHPID" && $- == *i* && -z ${BASH_EXECUTION_STRING+set} && -t 0 && -t 1 && -n ${TERM-} && $TERM != dumb && -z ${SSH_CONNECTION-}${SSH_ORIGINAL_COMMAND-} ]] || return 0

shopt -s histappend checkwinsize autocd cdspell globstar
HISTCONTROL=ignoreboth
HISTSIZE=100000
HISTFILESIZE=200000

up() {
  local count=${1:-1} target=
  [[ $# -le 1 && $count =~ ^[1-9][0-9]*$ ]] || { printf 'usage: up [count]\n' >&2; return 2; }
  while ((count--)); do target+=../; done
  cd -- "$target" || return
}

if [[ -z ${BASH_COMPLETION_VERSINFO-} && -z ${_dinas_stock_completion_loaded-} && -r @COMPLETION@ ]]; then
  _dinas_stock_completion_loaded=1
  source @COMPLETION@ || true
fi
bind 'set completion-ignore-case on'
bind 'set completion-map-case on'
bind 'set show-all-if-ambiguous on'
bind 'set menu-complete-display-prefix on'
bind 'set colored-stats on'
bind 'set colored-completion-prefix on'

# Align completion candidates into left-justified, padded columns rather
# than a densely wrapped list. Only the stock Bash instance gets this policy.
if [[ ${BLE_VERSION-} ]] && declare -F bleopt >/dev/null; then
  bleopt complete_menu_style=${DINAS_STOCK_MENU_STYLE:-align-nowrap} || true
  bleopt menu_align_min=${DINAS_STOCK_MENU_ALIGN_MIN:-4} || true
  bleopt menu_align_max=${DINAS_STOCK_MENU_ALIGN_MAX:-64} || true
  bleopt complete_menu_maxlines=${DINAS_STOCK_MENU_MAX_LINES:-12} || true
fi

if [[ ${BLE_VERSION-} ]] && declare -F ble-bind >/dev/null && declare -F ble-import >/dev/null; then
  bleopt history_share=1 || true
  ble-bind -f up history-search-backward || true
  ble-bind -f down history-search-forward || true
  ble-bind -m nsearch -f RET nsearch/accept-line || true
  ble-bind -m nsearch -f C-m nsearch/accept-line || true
  if [[ -z ${_dinas_stock_fzf_loaded-} ]]; then
    _dinas_stock_fzf_loaded=1
    _ble_contrib_fzf_base=@FZF@
    ble-import -d integration/fzf-completion || true
    ble-import -d integration/fzf-key-bindings || true
  fi
else
  bind '"\e[A": history-search-backward'
  bind '"\e[B": history-search-forward'
  bind '"\eOA": history-search-backward'
  bind '"\eOB": history-search-forward'
  bind 'TAB: menu-complete'
  bind '"\e[Z": menu-complete-backward'
  if _dinas_enhanced && [[ -z ${_dinas_stock_fzf_loaded-} ]]; then
    _dinas_stock_fzf_loaded=1
    [[ ! -r @FZF@/completion.bash ]] || source @FZF@/completion.bash || true
    [[ ! -r @FZF@/key-bindings.bash ]] || source @FZF@/key-bindings.bash || true
  fi
fi

# Keep generated tool hooks out of PROMPT_COMMAND. Host bashrc writes [0] on
# re-source; a single tail element survives and calls each tool exactly once.
_dinas_stock_init_tools() {
  local PROMPT_COMMAND='' _dinas_stock_code
  if [[ -x @ZOXIDE@ ]]; then
    _dinas_stock_code=$(@ZOXIDE@ init bash) && eval "$_dinas_stock_code"
    [[ ! ${BLE_VERSION-} ]] || ble-import -d integration/zoxide || true
  fi
  if [[ @DIRENV_ENABLED@ == 1 && -x @DIRENV@ ]]; then
    # Use the stock wrapper on every export, so its nix-direnv configuration
    # remains scoped here even when direnv's generated hook embeds its binary.
    _direnv_hook() {
      local previous_exit_status=$? output
      output=$(@DIRENV@ export bash) && eval "$output"
      return "$previous_exit_status"
    }
  fi
}
if _dinas_enhanced && [[ -z ${_dinas_stock_tools_loaded-} ]]; then
  _dinas_stock_tools_loaded=1
  case :$PATH: in
    *:@TOOLS@/bin:*) ;;
    *) export PATH=$PATH:@TOOLS@/bin ;;
  esac
  _dinas_stock_init_tools || true
fi

_dinas_stock_status() { return "$1"; }
_dinas_stock_history_hook() {
  local status=$?
  [[ ${BLE_ATTACHED-} ]] || { history -a; history -n; }
  if _dinas_enhanced; then
    if declare -F __zoxide_hook >/dev/null; then
      _dinas_stock_status "$status"
      __zoxide_hook
    fi
    if [[ @DIRENV_ENABLED@ == 1 ]] && declare -F _direnv_hook >/dev/null; then
      _dinas_stock_status "$status"
      _direnv_hook
    fi
  fi
  return "$status"
}
if [[ " ${PROMPT_COMMAND[*]-} " != *_dinas_stock_history_hook* ]]; then
  if [[ ${PROMPT_COMMAND@a} == *a* ]]; then
    PROMPT_COMMAND+=(_dinas_stock_history_hook)
  else
    # Preserve the complete existing scalar as one command, including ';'.
    PROMPT_COMMAND=("${PROMPT_COMMAND-}" _dinas_stock_history_hook)
  fi
fi
