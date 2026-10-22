# prompt style and colors based on Steve Losh's Prose theme:
# https://github.com/sjl/oh-my-zsh/blob/master/themes/prose.zsh-theme
#
# vcs_info modifications from Bart Trojanowski's zsh prompt:
# http://www.jukie.net/bart/blog/pimping-out-zsh-prompt
#
# git untracked files modification from Brian Carper:
# https://briancarper.net/blog/570/git-info-in-your-zsh-prompt

export VIRTUAL_ENV_DISABLE_PROMPT=1

function virtualenv_info {
    [[ -n ${VIRTUAL_ENV:-} ]] && print -r -- "(%F{blue}${VIRTUAL_ENV:t}%f) "
}

setopt prompt_subst

autoload -U add-zsh-hook
autoload -Uz vcs_info

#use extended color palette if available
if [[ $terminfo[colors] -ge 256 ]]; then
    turquoise="%F{81}"
    orange="%F{166}"
    purple="%F{135}"
    hotpink="%F{161}"
    limegreen="%F{118}"
    darkamber="%F{214}"
    lightamber="%F{220}"
    phosgreen="%F{83}"
    skyblue="%F{117}"
    red="%F{208}"
else
    turquoise="%F{cyan}"
    orange="%F{yellow}"
    purple="%F{magenta}"
    hotpink="%F{red}"
    limegreen="%F{green}"
    darkamber="%F{yellow}"
    lightamber="%F{yellow}"
    phosgreen="%F{green}"
    skyblue="%F{cyan}"
    hotpink="%F{red}"
fi

# enable VCS systems you use
zstyle ':vcs_info:*' enable git svn

# check-for-changes can be really slow.
# you should disable it, if you work with large repositories
zstyle ':vcs_info:*:prompt:*' check-for-changes true

# set formats
# %b - branchname
# %u - unstagedstr (see below)
# %c - stagedstr (see below)
# %m - misc hook output
# %a - action (e.g. rebase-i)
# %R - repository path
# %S - path in the repository
FMT_BRANCH="(%{$turquoise%}%b%{$lightamber%}%u%c%m)"
FMT_ACTION="(%{$phosgreen%}%a%{$lightamber%})"
FMT_UNSTAGED="%{$orange%}●%{$lightamber%}"
FMT_STAGED="%{$phosgreen%}●%{$lightamber%}"
FMT_UNTRACKED="%{$hotpink%}●%{$lightamber%}"

zstyle ':vcs_info:*:prompt:*' unstagedstr   "${FMT_UNSTAGED}"
zstyle ':vcs_info:*:prompt:*' stagedstr     "${FMT_STAGED}"
zstyle ':vcs_info:*:prompt:*' actionformats "${FMT_BRANCH}${FMT_ACTION} "
zstyle ':vcs_info:*:prompt:*' formats       "${FMT_BRANCH} "
zstyle ':vcs_info:*:prompt:*' nvcsformats   ""
zstyle ':vcs_info:git+set-message:*' hooks git-untracked

function +vi-git-untracked {
    emulate -L zsh
    [[ ${hook_com[vcs]} == git ]] || return 0
    command git rev-parse --is-inside-work-tree > /dev/null 2>&1 || return 0
    command git status --porcelain --untracked-files=normal --ignore-submodules=dirty 2> /dev/null |
        command grep -q '^?? ' || return 0
    hook_com[misc]+="${FMT_UNTRACKED}"
}

function steeef_precmd {
    vcs_info 'prompt'
}
add-zsh-hook precmd steeef_precmd

# PROMPT=$'
# %{$lightamber%}%n at %m [ %W | %* ]
# %~ $vcs_info_msg_0_$(virtualenv_info)
# $%f '

PROMPT=$'
%{$lightamber%}%~ $vcs_info_msg_0_$(virtualenv_info)
$%f '
