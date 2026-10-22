#!/usr/bin/env bash
# Set ENV after login(1), which may discard security-sensitive environment.
shopt -s execfail
unset ENV DINAS_KITTY_STOCK_PID DINAS_KITTY_STOCK_CWD DINAS_KITTY_STOCK_HISTFILE
if [[ -x @BASH@ && -r @ENTRY@ && -t 0 && -t 1 && -n ${TERM-} && $TERM != dumb && -z ${SSH_CONNECTION-}${SSH_ORIGINAL_COMMAND-} ]]; then
  export DINAS_KITTY_STOCK_PID=$$ DINAS_KITTY_STOCK_CWD=${1:-$PWD}
  if [[ ! ${HISTFILE+x} ]]; then
    export HISTFILE=$HOME/.bash_history DINAS_KITTY_STOCK_HISTFILE=unexport
  fi
  export ENV=@ENTRY@
  # A leading-dash argv0 makes Bash choose the account shell for BASH, even
  # with SHELL set. Keep the absolute executable argv0 and request
  # login behavior explicitly, before entry/ble.sh can run subprocesses.
  SHELL=@BASH@ exec @BASH@ --login --posix
fi
if [[ ${DINAS_KITTY_STOCK_HISTFILE-} == unexport ]]; then
  export -n HISTFILE
fi
unset ENV DINAS_KITTY_STOCK_PID DINAS_KITTY_STOCK_CWD DINAS_KITTY_STOCK_HISTFILE
[[ ! -d ${1-} ]] || cd -- "$1" || true
exec -a -bash @FALLBACK_BASH@
exit 127
