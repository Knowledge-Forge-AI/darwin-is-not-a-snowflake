# ENV is scoped to stage2 -> Bash. Never install it in the user's environment.
[ -n "${BASH_VERSION-}" ] || {
  unset ENV DINAS_KITTY_STOCK_PID DINAS_KITTY_STOCK_CWD DINAS_KITTY_STOCK_HISTFILE
  return 0
}
case ${0##*/} in
  bash|-bash) ;;
  *)
    unset ENV DINAS_KITTY_STOCK_PID DINAS_KITTY_STOCK_CWD DINAS_KITTY_STOCK_HISTFILE
    return 0 ;;
esac
if [[ $- != *i* ]]; then
  unset ENV DINAS_KITTY_STOCK_PID DINAS_KITTY_STOCK_CWD DINAS_KITTY_STOCK_HISTFILE
  return 0
fi
_dinas_stock_selected=0
if [[ ${DINAS_KITTY_STOCK_PID-} == "$$" && -z ${BASH_EXECUTION_STRING+set} && -t 0 && -t 1 && -n ${TERM-} && $TERM != dumb && -z ${SSH_CONNECTION-}${SSH_ORIGINAL_COMMAND-} ]]; then
  _dinas_stock_selected=1
fi
_dinas_stock_cwd=${DINAS_KITTY_STOCK_CWD-}
if [[ ${DINAS_KITTY_STOCK_HISTFILE-} == unexport ]]; then
  export -n HISTFILE
fi
unset ENV DINAS_KITTY_STOCK_PID DINAS_KITTY_STOCK_CWD DINAS_KITTY_STOCK_HISTFILE
set +o posix
# Bash does not undo this POSIX side effect when POSIX mode is disabled.
shopt -u inherit_errexit
# POSIX Bash would otherwise choose ~/.sh_history. Do this before rc loading.
[[ ${HISTFILE+x} ]] || HISTFILE=$HOME/.bash_history
if [[ $_dinas_stock_selected == 1 ]]; then
  # Stage2 selected an absolute argv0 plus --login and set SHELL before exec.
  # Assigning SHELL here would be too late for ble.sh's BASH subprocesses.
  _dinas_stock_owner=$BASHPID
  source @EARLY@
fi
# Compare with host Kitty Bash: /etc/profile, first readable login file.
if shopt -q login_shell; then
  [[ ! -r @SYSTEM_PROFILE@ ]] || source @SYSTEM_PROFILE@
  for _dinas_stock_profile in "$HOME/.bash_profile" "$HOME/.bash_login" "$HOME/.profile"; do
    if [[ -r $_dinas_stock_profile ]]; then
      source "$_dinas_stock_profile"
      break
    fi
  done
else
  [[ ! -r @SYSTEM_BASHRC@ ]] || source @SYSTEM_BASHRC@
  [[ ! -r $HOME/.bashrc ]] || source "$HOME/.bashrc"
fi
# login(1) changes cwd; restore Finder/kitty's requested working directory.
[[ ! -d $_dinas_stock_cwd ]] || cd -- "$_dinas_stock_cwd" || true
if [[ $_dinas_stock_selected == 1 ]]; then
  # Parse TOML as data, never source a user-writable TOML file.
  # The emitter outputs only fixed assignments with strict hex/integer values.
  if _dinas_style_vars=$(@STYLE_PYTHON@ -I -S -B @STYLE_CLI@ emit-shell \
      --home "$HOME" --default @STYLE_DEFAULT@); then
    eval "$_dinas_style_vars"
  else
    printf '%s\n' 'DINAS stock Bash: invalid TOML palette; using original prompt colors' >&2
  fi
  unset _dinas_style_vars
  source @INTERACTIVE@
  # Last startup action, following the exact donor's detached/attach contract.
  if [[ ${BLE_VERSION-} ]] && declare -F ble-attach >/dev/null; then
    ble-attach || true
  fi
fi
unset _dinas_stock_selected _dinas_stock_cwd _dinas_stock_profile
