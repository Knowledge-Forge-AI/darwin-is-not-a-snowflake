#!/usr/bin/env bash
# Entry selected by stock kitty only. Like Kitty Bash, login owns accounting.
shopt -s execfail
unset DINAS_KITTY_STOCK_PID DINAS_KITTY_STOCK_CWD DINAS_KITTY_STOCK_HISTFILE
if [[ $# != 0 ]]; then
  # Kitty's environment probe and explicit commands must finish, without extras.
  exec @LOGIN@ -fp "$(/usr/bin/id -un)" @BASH@ "$@"
  exec -a -bash @BASH@ "$@"
  exit 127
fi
exec @LOGIN@ -fp "$(/usr/bin/id -un)" @STAGE2@ "$PWD"
# Failure to execute login: use the same post-login stage without accounting.
exec @STAGE2@ "$PWD"
# Missing generation: ordinary host Bash remains usable; no markers survive.
unset ENV DINAS_KITTY_STOCK_PID DINAS_KITTY_STOCK_CWD DINAS_KITTY_STOCK_HISTFILE
exec -a -bash @FALLBACK_BASH@
exit 127
