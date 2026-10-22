#!/usr/bin/env python3
"""Strict data-only TOML controls for DINAS stock Kitty enhanced Bash.

`emit-shell` prints only constant-key, shell-quoted assignments and whitelisted
ble-face calls. The user-editable TOML is NEVER sourced as executable shell.
`promote --commit` is an explicit separate future action (no push/activation).
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DEFAULT = ROOT / 'config/kitty/stock-bash-style.toml'
INSTALLED_DEFAULT = Path('/run/current-system/sw/share/kitty-stock-bash/style-default.toml')
MAX_BYTES = 32768
PALETTE = ('path', 'branch', 'venv', 'unstaged', 'staged', 'untracked')
TERMINAL = ('foreground', 'background', 'cursor', 'selection_background', 'selection_foreground')
CMPL = ('style', 'align_min', 'align_max', 'max_lines')
ANSI = tuple('color' + str(i) for i in range(16))
KITTY_HEADER = '# DINAS stock Kitty palette — generated, do not edit directly\n'
# Faces defined by the pinned ble.sh 0.4.0-devel3, closed to reject typos/code.
# Completion faces require synchronous `ble-import lib/core-complete` first.
BLE_FACES = frozenset(('auto_complete menu_filter_fixed menu_filter_input '
  'region region_target region_match region_insert disabled overwrite_mode '
  'vbell vbell_erase vbell_flash prompt_status_line '
  'syntax_default syntax_command syntax_quoted syntax_quotation syntax_escape '
  'syntax_expr syntax_error syntax_varname syntax_delimiter syntax_param_expansion '
  'syntax_history_expansion syntax_function_name syntax_comment syntax_glob '
  'syntax_brace syntax_tilde syntax_document syntax_document_begin '
  'command_builtin_dot command_builtin command_alias command_function command_file '
  'command_keyword command_jobs command_directory '
  'filename_directory filename_directory_sticky filename_link filename_orphan '
  'filename_executable filename_setuid filename_setgid filename_other '
  'filename_socket filename_pipe filename_character filename_block '
  'filename_warning filename_url filename_ls_colors '
  'varname_array varname_empty varname_export varname_expr varname_hash '
  'varname_number varname_readonly varname_transform varname_unset '
  'argument_option argument_error').split())
HEX = re.compile(r'^#[0-9a-fA-F]{6}$')
ALLOWED_STYLES = {'align', 'align-nowrap', 'linewise', 'desc-text', 'dense', 'dense-nowrap'}
GFLAGS = {'bold', 'italic', 'underline', 'reverse', 'dim', 'strike', 'blink'}
GCOLORS = {'black','red','green','yellow','blue','magenta','cyan','white','gray',
           'brown','teal','lime','orange','pink'}

class Invalid(ValueError):
    pass

def physical(path: Path, *, final_may_be_absent=False, allow_hardlinks=False):
    path = path.absolute()
    if '..' in path.parts:
        raise Invalid('noncanonical path')
    for item in (*reversed(path.parents), path):
        if item.is_symlink():
            raise Invalid('symlink path: ' + str(item))
    if not final_may_be_absent and not path.is_file():
        raise Invalid('missing file: ' + str(path))
    if path.is_file() and path.stat().st_nlink != 1 and not allow_hardlinks:
        raise Invalid('hardlinked file: ' + str(path))
    return path

def default_path():
    # Resolve only this trusted system-profile alias, never user input paths.
    # An explicit --default is required for a standalone installed store file
    # when no selected stock share is present.
    if INSTALLED_DEFAULT.exists() or INSTALLED_DEFAULT.is_symlink():
        return INSTALLED_DEFAULT.resolve(strict=True)
    return SOURCE_DEFAULT

def read_toml(path: Path, *, optional=False, allow_hardlinks=False):
    path = physical(path, final_may_be_absent=optional, allow_hardlinks=allow_hardlinks)
    if not path.exists() and optional:
        return {}
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise Invalid('nonregular or oversized TOML')
    with path.open('rb') as inp:
        data = tomllib.load(inp)
    if type(data) is not dict or set(data)-{'schema','palette','terminal','ansi','completion','ble'}:
        raise Invalid('unknown top-level TOML keys')
    if type(data.get('schema')) is not int or data.get('schema')!=1:
        raise Invalid('schema must equal 1')
    known = {
      'palette': set(PALETTE), 'terminal':set(TERMINAL),
      'ansi':set(ANSI), 'completion':set(CMPL)
    }
    for section,allowed in known.items():
        value=data.get(section,{})
        if type(value) is not dict or set(value)-allowed:
            raise Invalid('unknown TOML keys in '+section)
        for key,item in value.items():
            if section in ('palette','terminal','ansi'):
                if type(item) is not str or HEX.fullmatch(item) is None:
                    raise Invalid('invalid hex color in '+section+'.'+key)
            elif key == 'style':
                if type(item) is not str or item not in ALLOWED_STYLES:
                    raise Invalid('unsupported completion style')
            elif type(item) is not int or not 1 <= item <= 150:
                raise Invalid('completion width must be integer 1..150')
    ble=data.get('ble',{})
    if type(ble) is not dict or set(ble)-{'faces'}:
        raise Invalid('only [ble.faces] is supported')
    faces=ble.get('faces',{})
    if type(faces) is not dict or set(faces)-BLE_FACES:
        raise Invalid('unknown ble.sh face')
    for face,gspec in faces.items():
        valid_gspec(gspec,face)
    completion=data.get('completion',{})
    if ('align_min' in completion and 'align_max' in completion
            and completion['align_min']>completion['align_max']):
        raise Invalid('align_min > align_max')
    for section in ('palette', 'terminal', 'completion'):
        if section not in data:
            raise Invalid('required missing table: ' + section)
    return data

def valid_gspec(s,face=''):
    if type(s) is not str or not s or len(s)>140:
        raise Invalid('invalid ble face: '+face)
    if s == 'none':
        return
    seen=set()
    for part in s.split(','):
        if part in GFLAGS:
            if part in seen:raise Invalid('duplicate style flag: '+face)
            seen.add(part);continue
        match=re.fullmatch(r'(fg|bg)=(#[0-9A-Fa-f]{6}|[0-9]{1,3}|[a-z]+)',part)
        if not match:
            raise Invalid('invalid ble face graphic spec: '+face)
        which,color=match.groups()
        if which in seen:raise Invalid('duplicate face property: '+face)
        seen.add(which)
        if color.startswith('#'):
            continue
        if color.isdigit() and int(color)<=255:
            continue
        if color in GCOLORS:
            continue
        raise Invalid('unknown color in '+face)

def selected(home: Path, *, explicit:Path|None=None, default:Path|None=None):
    default = default if default is not None else default_path()
    # Trusted, read-only defaults may be hardlinked by Nix store optimisation.
    # Local overrides and every write destination retain the link refusal.
    base=read_toml(default, allow_hardlinks=True)
    user=read_toml(explicit if explicit is not None else home/'.local/nix-darwin/config/kitty-bash.toml',
                   optional=explicit is None)
    merged={k:base.get(k,{}).copy() for k in ('palette','terminal','ansi','completion')}
    merged['ble']={'faces': base.get('ble',{}).get('faces',{}).copy()}
    for key in ('palette','terminal','ansi','completion'):
        merged[key].update(user.get(key,{}))
    merged['ble']['faces'].update(user.get('ble',{}).get('faces',{}))
    needed={'path','branch','venv','unstaged','staged','untracked'}
    if set(merged['palette']) != needed or set(merged['terminal'])!=set(TERMINAL):
        raise Invalid('committed fallback is incomplete')
    if set(merged['completion'])!=set(CMPL):
        raise Invalid('completion settings are incomplete')
    if merged['completion']['align_min']>merged['completion']['align_max']:
        raise Invalid('align_min > align_max')
    return merged

def ansi(color):
    h=color[1:]
    r,g,b=(int(h[i:i+2],16) for i in (0,2,4))
    return rf'\[\033[38;2;{r};{g};{b}m\]'

def emit_shell(config):
    pal=config['palette']; cm=config['completion']
    mapping={'STEEEF_LIGHTAMBER':'path','STEEEF_TURQUOISE':'branch',
             'STEEEF_PHOSGREEN':'staged','STEEEF_ORANGE':'unstaged',
             'STEEEF_HOTPINK':'untracked','STEEEF_BLUE':'venv'}
    rows=[f'{var}={shlex.quote(ansi(pal[role]))}' for var,role in mapping.items()]
    rows += [
      'STEEEF_RESET='+shlex.quote(r'\[\033[0m\]'),
      'STEEEF_UNSTAGED="${STEEEF_ORANGE}●${STEEEF_LIGHTAMBER}"',
      'STEEEF_STAGED="${STEEEF_PHOSGREEN}●${STEEEF_LIGHTAMBER}"',
      'STEEEF_UNTRACKED="${STEEEF_HOTPINK}●${STEEEF_LIGHTAMBER}"'
    ]
    for key, val in {
       'DINAS_STOCK_MENU_STYLE':cm['style'],
       'DINAS_STOCK_MENU_ALIGN_MIN':cm['align_min'],
       'DINAS_STOCK_MENU_ALIGN_MAX':cm['align_max'],
       'DINAS_STOCK_MENU_MAX_LINES':cm['max_lines']}.items():
        rows.append(f'{key}={shlex.quote(str(val))}')
    if config['ble']['faces']:
        rows.append('if [[ ${BLE_VERSION-} ]] && declare -F ble-face >/dev/null && declare -F ble-import >/dev/null; then')
        # --noattach leaves core-complete lazy. Public synchronous import defines
        # its faces before setters (and remains idempotent on repeated emission).
        rows.append('  if ble-import lib/core-complete; then')
        for name,spec in sorted(config['ble']['faces'].items()):
            # Constant face whitelist and parsed gspec, quoted as one Bash word.
            rows.append(f'    ble-face -s {name} {shlex.quote(spec)}')
        rows.append('  else')
        rows.append("    printf '%s\\n' 'DINAS stock Bash: completion module unavailable; ble faces not applied' >&2")
        rows.append('  fi')
        rows.append('fi')
    return '\n'.join(rows)+'\n'

def render_kitty(config):
    rows=[KITTY_HEADER.rstrip('\n')]
    rows.extend(f'{key} {config["terminal"][key]}' for key in TERMINAL)
    rows.extend(f'{key} {val}' for key,val in sorted(config['ansi'].items(),
                  key=lambda p:int(p[0][5:])))
    return '\n'.join(rows)+'\n'

def write_new(path:Path,data:bytes):
    physical(path,final_may_be_absent=True)
    path.parent.mkdir(parents=True,exist_ok=True)
    physical(path.parent,final_may_be_absent=True)
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as out:
        out.write(data)

def replace_managed(path:Path,data:bytes):
    physical(path,final_may_be_absent=True)
    if path.exists():
        if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_uid!=os.getuid():
            raise Invalid('unowned target')
        old=path.read_bytes()
        if not old.startswith(KITTY_HEADER.encode()):
            raise Invalid('refusing to overwrite unmanaged Kitty file')
        if old==data:return
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.stock-style.',delete=False) as fp:
        temp=Path(fp.name)
        fp.write(data)
        fp.flush()
        os.fchmod(fp.fileno(),0o600)
    try:os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)

def git(root,*args):
    # Retain deliberate config isolation (including fixture /dev/null globals),
    # while discarding repository/index/worktree and injected-config overrides.
    env={k:v for k,v in os.environ.items() if not k.startswith('GIT_')
         or k in {'GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_GLOBAL'}}
    env.update(GIT_TERMINAL_PROMPT='0',GIT_OPTIONAL_LOCKS='0')
    p=subprocess.run(['git','-C',str(root),'-c','protocol.allow=never',*args],env=env,
                     input='',text=True,capture_output=True,timeout=40)
    if p.returncode:raise Invalid('git refused: '+' '.join(args[:2]))
    return p.stdout.strip()

def promote(root:Path,home:Path,config:dict,do_commit:bool):
    root=physical(root,final_may_be_absent=True)
    if not root.is_dir():raise Invalid('core checkout missing')
    if Path(git(root,'rev-parse','--show-toplevel')).resolve()!=root.resolve():
        raise Invalid('not core root')
    if git(root,'symbolic-ref','--short','HEAD')!='main':raise Invalid('core is not on main')
    dest=root/'config/kitty/stock-bash-style.toml'
    physical(dest)
    if git(root,'diff','--cached','--name-only'):
        raise Invalid('index already staged; preserve unrelated work')
    rel='config/kitty/stock-bash-style.toml'
    if git(root,'diff','--name-only','--',rel):
        raise Invalid('default already edited')
    lines=['# DINAS selected stock Bash style','schema = 1','']
    for section in ('palette','terminal','ansi','completion'):
        if not config[section] and section=='ansi':continue
        lines.append('['+section+']')
        for key,val in config[section].items():
            lines.append(f'{key} = '+(str(val) if type(val) is int else '"'+val+'"'))
        lines.append('')
    lines.append('[ble.faces]')
    for key,val in sorted(config['ble']['faces'].items()):
        lines.append(f'{key} = "{val}"')
    lines.append('')
    data='\n'.join(lines).encode()
    if not do_commit:
        print('PROMOTE_DRY_RUN=true (would commit only '+rel+')')
        return
    # Changing a source file is deliberate; signing/building/deployment are not.
    physical(dest)
    temp=dest.with_name('.'+dest.name+'.next-'+str(os.getpid()))
    write_new(temp,data)
    try:os.replace(temp,dest)
    finally:temp.unlink(missing_ok=True)
    git(root,'add','--',rel)
    git(root,'-c','user.useConfigOnly=true','commit','--only','-m',
        'kitty: adopt selected stock Bash palette','--',rel)
    print('COMMITTED='+git(root,'rev-parse','HEAD'))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('validate','emit-shell','render-kitty','init-user','promote'))
    p.add_argument('--home',type=Path,default=Path.home())
    p.add_argument('--file',type=Path)
    p.add_argument('--default',type=Path,
                   help='installed palette fallback; otherwise active stock share, then source sample')
    p.add_argument('--repo',type=Path,default=Path.home()/'.local/nix-darwin/darwin-is-not-a-snowflake')
    p.add_argument('--apply',action='store_true',help='only render-kitty: write stock-only include')
    p.add_argument('--commit',action='store_true',help='only promote: commit selected TOML')
    a=p.parse_args()
    if a.apply and a.mode!='render-kitty':raise Invalid('--apply is render-kitty only')
    if a.commit and a.mode!='promote':raise Invalid('--commit is promote only')
    a.default = a.default if a.default is not None else default_path()
    config=selected(a.home,explicit=a.file,default=a.default)
    if a.mode=='validate':print('STYLE_VALID=true');return
    if a.mode=='emit-shell':sys.stdout.write(emit_shell(config));return
    if a.mode=='init-user':
        target=a.home/'.local/nix-darwin/config/kitty-bash.toml'
        write_new(target,a.default.read_bytes())
        print('CREATED='+str(target));return
    if a.mode=='render-kitty':
        rendered=render_kitty(config)
        if a.apply:
            target=a.home/'.config/kitty/stock-bash-colors.conf'
            replace_managed(target,rendered.encode())
            print('STOCK_KITTY_COLORS_WRITTEN='+str(target))
        else:
            sys.stdout.write(rendered)
        return
    if a.mode=='promote':promote(a.repo,a.home,config,a.commit)

if __name__=='__main__':
    try:main()
    except (Invalid,OSError,UnicodeError,tomllib.TOMLDecodeError) as error:
        print('STYLE_REFUSED='+str(error),file=sys.stderr)
        raise SystemExit(2)
