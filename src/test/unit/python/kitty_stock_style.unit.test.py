# nix-darwin-test-source-target: tools/kitty-stock-style.py
"""Data validation and explicit mutations, using disposable homes/repos only."""
from __future__ import annotations

import os
import importlib
import json
import errno
from pathlib import Path
import pty
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import time
import tomllib

import pytest


# The installed lock's real runtime, not the account shell or test doubles.
# Optional overrides let a parent exercise the same fixture after rebuilding.
LOCKED_BASH = '/nix/store/8gmij30qmmg3c9xp7b7yj502mh5krgv4-bash-interactive-5.3p3/bin/bash'
LOCKED_BLESH = '/nix/store/mkbq3k3xvwnfr1n50nc9q567k1ycxj5f-blesh-0.4.0-devel3/share/blesh/ble.sh'


@pytest.fixture
def stock_runtime(tmp_path, repo_root):
    """Render production templates; isolate every rc, cache and history write."""
    root = tmp_path.resolve()
    home = root / 'home'
    home.mkdir()
    for directory in ('.config', '.cache', '.local/share', '.local/state', 'run'):
        (home / directory).mkdir(parents=True, mode=0o700)
    bash = Path(os.environ.get('KITTY_STOCK_TEST_BASH', LOCKED_BASH))
    blesh = Path(os.environ.get('KITTY_STOCK_TEST_BLESH', LOCKED_BLESH))
    if not os.access(bash, os.X_OK) or not blesh.is_file():
        pytest.skip('Real locked Bash/ble.sh unavailable; parent must run the pinned PTY tests')
    replacements = {
        'BASH': bash, 'FALLBACK_BASH': bash, 'ENTRY': root / 'entry.bash',
        'EARLY': root / 'early.bash', 'INTERACTIVE': root / 'interactive.bash',
        'STAGE2': root / 'stage2', 'LOGIN': root / 'login',
        'SYSTEM_PROFILE': root / 'system-profile', 'SYSTEM_BASHRC': root / 'system-bashrc',
        'BLESH': blesh,
        'COMPLETION': '/nix/store/43c3gld3sv527whwcrf7ab8hr9mjr4bx-bash-completion-2.17.0/etc/profile.d/bash_completion.sh',
        'FZF': '/nix/store/kvlm9wwbaj5fsicjdfra3yp69vn4lcp2-fzf-0.67.0/share/fzf',
        'ZOXIDE': '/nix/store/9g1vr4sidfw2y0bzq57syc69a871yjma-zoxide-0.9.9/bin/zoxide',
        'TOOLS': '/nix/store/ssn2x4dy6l6z5z3s2jczgkh427khcbr6-kitty-stock-bash-tools',
        'DIRENV_ENABLED': '0', 'DIRENV': root / 'unused-direnv',
        'STYLE_PYTHON': '/nix/store/fcdrgksa8p21ykm55jm2jng8pyzdb4m3-python3-3.13.12/bin/python3',
        'STYLE_CLI': repo_root / 'tools/kitty-stock-style.py',
        'STYLE_DEFAULT': repo_root / 'config/kitty/stock-bash-style.toml',
    }
    # Every pinned replacement is a fixture prerequisite, including the style
    # interpreter. A collected store path must produce an honest skip, not an
    # unrelated palette/face-value failure. Parent rebuilds can supply new pins.
    for key in ('COMPLETION', 'FZF', 'ZOXIDE', 'TOOLS', 'STYLE_PYTHON'):
        replacements[key] = Path(os.environ.get('KITTY_STOCK_TEST_' + key, replacements[key]))
    prerequisites = {
        'COMPLETION': os.access(replacements['COMPLETION'], os.R_OK),
        'FZF': all(os.access(replacements['FZF'] / name, os.R_OK)
                   for name in ('completion.bash', 'key-bindings.bash')),
        'ZOXIDE': os.access(replacements['ZOXIDE'], os.X_OK),
        'TOOLS': (replacements['TOOLS'] / 'bin').is_dir(),
        'STYLE_PYTHON': os.access(replacements['STYLE_PYTHON'], os.X_OK),
    }
    missing = ', '.join(key for key, available in prerequisites.items() if not available)
    if missing:
        pytest.skip('Real locked runtime unavailable (' + missing + '); parent must run the pinned PTY tests')
    for name in ('stage2', 'launch', 'entry.bash', 'early.bash', 'interactive.bash'):
        suffix = '.sh' if name in ('stage2', 'launch') else ''
        text = (repo_root / f'config/kitty/stock-bash-{name}{suffix}').read_text()
        text = text.replace('#!/usr/bin/env bash', '#!' + str(bash))
        for key, value in replacements.items():
            text = text.replace('@' + key + '@', str(value))
        assert re.search(r'@[A-Z_]+@', text) is None
        (root / name).write_text(text)
        (root / name).chmod(0o700)
    # A fixture login boundary records accounting arguments without touching
    # utmp, real profiles, or the user's installed configuration.
    (root / 'login').write_text('#!' + str(bash) + '\nprintf "%s\\n" "$@" > "$HOME/login-args"\nshift 2\nexec "$@"\n')
    (root / 'login').chmod(0o700)
    (root / 'system-profile').write_text(
        'printf "system:%s:%s:%s:%s\\n" "$SHELL" "$BASH" "${BLE_VERSION:+loaded}" "${BLE_ATTACHED:-detached}" >> "$HOME/rc-order"\n')
    (root / 'system-bashrc').write_text('echo system-bashrc >> "$HOME/rc-order"\n')
    (home / '.bash_profile').write_text('echo profile >> "$HOME/rc-order"\nsource "$HOME/.bashrc"\n')
    (home / '.bash_login').write_text('echo WRONG-login >> "$HOME/rc-order"\n')
    (home / '.profile').write_text('echo WRONG-profile >> "$HOME/rc-order"\n')
    (home / '.bashrc').write_text("echo bashrc >> \"$HOME/rc-order\"\nPS1='STOCK_READY> '\nPROMPT_COMMAND=':'\n")
    env = {'HOME': str(home), 'USER': 'fixture', 'LOGNAME': 'fixture',
           'PATH': os.defpath, 'SHELL': '/bin/zsh', 'HISTFILE': str(home / '.bash_history'),
           'TERM': 'xterm-256color', 'LANG': 'C.UTF-8', 'INPUTRC': os.devnull,
           'XDG_CONFIG_HOME': str(home / '.config'), 'XDG_CACHE_HOME': str(home / '.cache'),
           'XDG_DATA_HOME': str(home / '.local/share'), 'XDG_STATE_HOME': str(home / '.local/state'),
           'XDG_RUNTIME_DIR': str(home / 'run'),
           'TMPDIR': str(root)}
    return root, home, bash, blesh, env


def run_stock_pty(executable, args, env, home, command, result_name='result', argv0=None):
    """Wait for a real first prompt, submit a command, then require clean exit."""
    try:
        pid, master = pty.fork()
    except OSError as exc:
        if exc.errno in (errno.EPERM, errno.EACCES, errno.ENODEV):
            pytest.skip('Native PTY denied; parent must run the pinned PTY tests')
        raise
    if pid == 0:
        try:
            os.chdir(home)
            os.execve(executable, [argv0 or str(executable), *args], env)
        except OSError:
            os._exit(127)
    (home / 'pty-pid').write_text(str(pid))
    transcript = b''
    pending = b''
    os.set_blocking(master, False)
    exited = False
    status = None
    sent = False
    result_file = home / result_name
    deadline = time.monotonic() + 25
    try:
        while time.monotonic() < deadline:
            readable, writable, _ = select.select([master], [master] if pending else [], [], 0.05)
            if writable:
                pending = pending[os.write(master, pending[:256]):]
            if readable:
                try:
                    chunk = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    chunk = b''
                transcript += chunk
                # Answer the terminal's cursor-position query in disposable PTY.
                if b'\x1b[6n' in chunk:
                    pending += b'\x1b[1;1R'
            visible = re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', transcript)
            if not sent and b'STOCK_READY> ' in visible:
                pending += (command + '\n').encode()
                sent = True
            if result_file.exists():
                pending += b'exit\n'
                # Only send exit once; the file's contents remain test evidence.
                result_file = home / 'no-further-command'
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited:
                exited = True
                break
        safe_output = transcript.decode(errors='replace').replace(str(home.parent), '<fixture>')
        assert sent, 'No real first prompt: ' + safe_output[-3500:]
        assert exited, 'PTY did not exit within 25s: ' + safe_output[-3500:]
        assert os.waitstatus_to_exitcode(status) == 0, safe_output[-3500:]
        assert (home / result_name).exists(), safe_output[-3500:]
        assert 'no such option: noprofile' not in safe_output
        face_errors = re.findall(
            r'ble-face:[^\r\n]*(?:not found|unrecognized|invalid)'
            r"|ble\.sh: the specified face `[^\r\n]*' is not defined\.", safe_output)
        assert not face_errors, 'Unexpected ble face diagnostics:\n' + '\n'.join(face_errors)
        return (home / result_name).read_text()
    finally:
        if not exited:
            # Kill this exact disposable shell group, including tool children.
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)
        os.close(master)


@pytest.mark.parametrize('initial_argv0', ['stage2', '-bash', 'launch'])
def test_locked_zero_argument_interactive_first_prompt_and_resource_identity(stock_runtime, initial_argv0):
    root, home, bash, _, env = stock_runtime
    executable = bash if initial_argv0 == '-bash' else root / initial_argv0
    args = ['--noprofile', '--norc', str(root / 'stage2')] if initial_argv0 == '-bash' else []
    result = run_stock_pty(executable, args, env, home,
        'printf "%s\\n" "$SHELL" "$BASH" "$0" "$BASHPID" "${BLE_ATTACHED:-missing}" '
        '"$(shopt -q login_shell && echo login)" "$PWD" > "$HOME/result"; '
        'ble-import -q lib/core-complete; printf "completion:%s\\n" "$?" >> "$HOME/result"; '
        'ble-face auto_complete menu_filter_fixed menu_filter_input >> "$HOME/result"',
        argv0='-bash' if initial_argv0 == '-bash' else None)
    rows = result.splitlines()
    assert rows[:3] == [str(bash), str(bash), str(bash)]
    assert rows[3] == (home / 'pty-pid').read_text()
    assert rows[4:7] == ['1', 'login', str(home)]
    assert rows[7] == 'completion:0'
    assert (home / 'rc-order').read_text().splitlines() == [
        f'system:{bash}:{bash}:loaded:detached', 'profile', 'bashrc']
    assert 'ble-face auto_complete=fg=#89939f,italic\n' in result
    assert 'ble-face menu_filter_fixed=fg=#a7d5d0,bold\n' in result
    assert 'ble-face menu_filter_input=fg=#ffffff,bg=#344c61\n' in result
    if initial_argv0 == 'launch':
        login_args = (home / 'login-args').read_text().splitlines()
        assert login_args[0] == '-fp' and login_args[2:] == [str(root / 'stage2'), str(home)]


def test_locked_all_accepted_faces_exist_and_resourcing_is_idempotent(stock_runtime, style):
    root, home, _, _, env = stock_runtime
    # Test the entire closed table through the real public setter after startup.
    names = ' '.join(sorted(style.BLE_FACES))
    command = (
        'source ' + shlex.quote(str(root / 'early.bash')) + '; source "$HOME/.bashrc"; source '
        + shlex.quote(str(root / 'interactive.bash')) + '; source '
        + shlex.quote(str(root / 'interactive.bash')) + '; '
        'for face in ' + names + '; do ble-face -s "$face" fg=#123456 || break; done; '
        '{ printf "last:%s\\n" "$face"; ble-face ' + names + '; '
        'printf "hooks:%s\\n" "${PROMPT_COMMAND[*]}"; '
        'printf "completion:%s\\n" "${BASH_COMPLETION_VERSINFO[0]}"; '
        'ble-import -q; } > "$HOME/result"')
    (root / 'check-faces.bash').write_text(command)
    result = run_stock_pty(root / 'stage2', [], env, home,
                           'source ' + shlex.quote(str(root / 'check-faces.bash')))
    assert result.splitlines()[0] == 'last:' + sorted(style.BLE_FACES)[-1]
    assert len(re.findall(r'^ble-face ', result, re.M)) == len(style.BLE_FACES)
    assert result.count('_dinas_stock_history_hook') == 1
    assert 'completion:2' in result
    assert result.count('/lib/core-complete.sh\n') == 1
    assert result.count('=fg=#123456\n') == len(style.BLE_FACES)


@pytest.mark.parametrize('setter', ['ble-face -s', 'ble-color-setface'])
def test_locked_missing_face_diagnostics_are_detected(stock_runtime, setter):
    root, home, _, _, env = stock_runtime
    # Probe both real pinned diagnostic forms with the old sample's seven
    # faces, forcing face initialization before the lazy completion module.
    legacy_faces = ('menu_complete_match', 'menu_complete_selected', 'menu_desc_default',
                    'menu_desc_quote', 'menu_desc_type', 'menu_filter_fixed', 'menu_filter_input')
    entry = root / 'entry.bash'
    entry.write_text(entry.read_text().replace('eval "$_dinas_style_vars"',
        '\n'.join(setter + ' ' + face + ' fg=#123456' for face in legacy_faces)
        + '\nble-face auto_complete >/dev/null'))
    with pytest.raises(AssertionError, match='not found|the specified face') as detected:
        run_stock_pty(root / 'stage2', [], env, home, 'printf done > "$HOME/result"')
    for face in legacy_faces:
        assert face in str(detected.value)


@pytest.mark.parametrize('guard', ['plain', 'ssh', 'dumb', 'owner'])
def test_locked_interactive_guards_exclude_blesh(stock_runtime, guard):
    root, home, bash, _, env = stock_runtime
    env.update({'plain': {'DINAS_PLAIN_BASH': '1'}, 'ssh': {'SSH_CONNECTION': 'fixture'},
                'dumb': {'TERM': 'dumb'}, 'owner': {}}[guard])
    owner = 'other' if guard == 'owner' else '$BASHPID'
    (root / 'guard-rc').write_text(
        '_dinas_stock_owner=' + owner + '\nsource ' + shlex.quote(str(root / 'early.bash'))
        + '\nsource ' + shlex.quote(str(root / 'interactive.bash')) + "\nPS1='STOCK_READY> '\n")
    result = run_stock_pty(bash, ['--noprofile', '--rcfile', str(root / 'guard-rc'), '-i'],
        env, home, 'printf "ble:%s\\n" "${BLE_VERSION:-absent}" > "$HOME/result"')
    assert result == 'ble:absent\n'
    assert not (home / '.cache/blesh').exists()


def test_locked_noninteractive_and_explicit_command_contracts(stock_runtime):
    root, home, bash, _, env = stock_runtime
    before = sorted(home.rglob('*'))
    code = ('_dinas_stock_owner=$BASHPID; source ' + shlex.quote(str(root / 'entry.bash'))
            + '; source ' + shlex.quote(str(root / 'early.bash')) + '; source '
            + shlex.quote(str(root / 'interactive.bash'))
            + '; printf "%s:%s:%s\\n" "${BLE_VERSION:-absent}" "${ENV:-absent}" "${DINAS_KITTY_STOCK_PID:-absent}"')
    inherited = {**env, 'DINAS_KITTY_STOCK_PID': 'other', 'ENV': str(root / 'entry.bash')}
    result = subprocess.run([bash, '--noprofile', '--norc', '-c', code], env=inherited,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and result.stdout == 'absent:absent:absent\n'
    assert sorted(home.rglob('*')) == before
    code = ('printf "%s\\n" "$SHELL" "${BLE_VERSION:-absent}" '
            '"${DINAS_KITTY_STOCK_PID:-absent}" "$1"')
    result = subprocess.run([root / 'launch', '--noprofile', '--norc', '-c', code,
                             'fixture', 'argument with spaces'], env=inherited,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == '/bin/zsh\nabsent\nabsent\nargument with spaces\n'
    assert not (home / 'rc-order').exists()
    assert (home / 'login-args').read_text().splitlines()[2:5] == [str(bash), '--noprofile', '--norc']


@pytest.fixture
def style(load_module):
    return load_module("tools/kitty-stock-style.py")


@pytest.fixture
def fixture(tmp_path, repo_root):
    # Canonicalize our base, never an intentional input/output symlink.
    root = tmp_path.resolve()
    home = root / "home"
    home.mkdir()
    fallback = root / "installed.toml"
    shutil.copyfile(repo_root / "config/kitty/stock-bash-style.toml", fallback)
    local = home / ".local/nix-darwin/config/kitty-bash.toml"
    local.parent.mkdir(parents=True)
    env = {"HOME": str(home), "PATH": os.defpath, "TMPDIR": str(root),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
           "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}

    def run(mode, *args, expected=0, default=fallback, file=None, program=None):
        cmd = [sys.executable, "-I", "-S", "-B",
               str(program or repo_root / "tools/kitty-stock-style.py"), mode,
               "--home", str(home), "--default", str(default)]
        if file is not None:
            cmd += ["--file", str(file)]
        result = subprocess.run([*cmd, *map(str, args)], env=env, text=True,
                                capture_output=True, timeout=15)
        assert result.returncode == expected, result.stderr
        return result

    return root, home, fallback, local, env, run


def test_first_run_validation_emission_and_preview_do_not_write(fixture):
    root, home, _, local, _, run = fixture
    for directory in (local.parent, local.parent.parent, home / '.local'):
        directory.rmdir()
    before = sorted(home.rglob("*"))
    assert run("validate").stdout == "STYLE_VALID=true\n"
    assert "STEEEF_LIGHTAMBER=" in run("emit-shell").stdout
    assert "foreground #33ff00" in run("render-kitty").stdout
    assert sorted(home.rglob("*")) == before
    assert not local.exists()
    assert not (home / ".config").exists()


@pytest.mark.parametrize(("text", "reason"), [
    ("", "schema must equal 1"),
    ("schema = true", "schema must equal 1"),
    ("schema = 2", "schema must equal 1"),
    ("schema = 1\nextra = 1", "unknown top-level"),
    ("schema = 1\npalette = 1", "unknown TOML keys in palette"),
    ("schema = 1\n[palette]\nwrong = '#111111'", "unknown TOML keys in palette"),
    ("schema = 1\n[palette]\npath = 1", "invalid hex color"),
    ("schema = 1\n[terminal]\ncursor = '#fff'", "invalid hex color"),
    ("schema = 1\n[ansi]\ncolor16 = '#111111'", "unknown TOML keys in ansi"),
    ("schema = 1\n[completion]\nstyle = 'shell;command'", "unsupported completion style"),
    ("schema = 1\n[completion]\nalign_min = true", "integer 1..150"),
    ("schema = 1\n[completion]\nmax_lines = 151", "integer 1..150"),
    ("schema = 1\n[completion]\nalign_min = 20\nalign_max = 10", "align_min > align_max"),
    ("schema = 1\n[ble]\nother = 1", "only [ble.faces]"),
    ("schema = 1\n[ble]\nfaces = 1", "unknown ble.sh face"),
    ("schema = 1\n[ble.faces]\ninvented = 'none'", "unknown ble.sh face"),
    ("schema = 1\n[ble.faces]\nauto_complete = 1", "invalid ble face"),
    ("schema = 1\n[ble.faces]\nauto_complete = 'fg=999'", "unknown color"),
    ("schema = 1\n[ble.faces]\nauto_complete = 'fg=١٢'", "graphic spec"),
    ("schema = 1\n[ble.faces]\nauto_complete = 'bg=１２'", "graphic spec"),
    ("schema = 1\n[ble.faces]\nauto_complete = 'fg=red,fg=blue'", "duplicate face property"),
    ("schema = 1\n[ble.faces]\nauto_complete = 'bold,bold'", "duplicate style flag"),
    ("schema = 1\n[ble.faces]\nauto_complete = '$(touch sentinel)'", "graphic spec"),
    ("schema = 1\n[ble.faces]\nauto_complete = 'fg=red;touch sentinel'", "graphic spec"),
])
def test_invalid_local_data_refuses_before_shell_or_output(fixture, text, reason):
    _, home, _, local, _, run = fixture
    local.write_text(text)
    result = run("emit-shell", expected=2)
    assert result.stdout == ""
    assert "STYLE_REFUSED=" in result.stderr and reason in result.stderr
    run("render-kitty", "--apply", expected=2)
    assert not (home / ".config").exists()


@pytest.mark.parametrize('face', [
    'menu_complete_match', 'menu_complete_selected', 'menu_desc_default',
    'menu_desc_quote', 'menu_desc_type', 'command_suffix', 'command_suffix_new', 'varname_new',
])
def test_faces_absent_from_pinned_blesh_are_rejected_before_emission(fixture, face):
    _, home, _, local, _, run = fixture
    local.write_text('schema=1\n[palette]\n[terminal]\n[completion]\n'
                     '[ble.faces]\n' + face + '="fg=#123456"\n')
    result = run('emit-shell', expected=2)
    assert result.stdout == '' and 'unknown ble.sh face' in result.stderr
    assert not (home / '.config').exists()


def test_missing_invalid_and_incomplete_default(fixture):
    root, _, default, local, _, run = fixture
    result = run("validate", default=root / "absent", expected=2)
    assert "missing file" in result.stderr
    result = run("validate", file=local, expected=2)
    assert "missing file" in result.stderr
    default.write_text("schema=1\n")
    assert "required missing table" in run("validate", expected=2).stderr
    default.write_bytes(b"\xff")
    assert "STYLE_REFUSED=" in run("validate", expected=2).stderr
    default.write_text("schema=1\n[broken")
    assert "STYLE_REFUSED=" in run("validate", expected=2).stderr
    default.write_text("#" * 32769)
    assert "oversized TOML" in run("validate", expected=2).stderr


@pytest.mark.parametrize("tables", ["", "[ansi]\n", "[ble]\n", "[ble.faces]\n",
                                        "[ansi]\n[ble.faces]\n"])
def test_partial_and_empty_optional_tables(fixture, tables):
    _, _, _, local, _, run = fixture
    local.write_text("schema=1\n[palette]\n[terminal]\n[completion]\n" + tables)
    assert "STYLE_VALID=true" in run("validate").stdout


def test_projected_A_local_B_render_then_remove_override(fixture, style):
    _, home, default, local, _, run = fixture
    text = default.read_text().replace('#ffd700', '#112233').replace('#33ff00', '#224466')
    text = text.replace('fg=#89939f,italic', 'fg=#112233,italic')
    default.write_text(text)
    include = home / ".config/kitty/stock-bash-colors.conf"

    def check(prompt, face, terminal):
        shell = run("emit-shell").stdout
        assert style.ansi(prompt) in shell
        assert f"ble-face -s auto_complete '{face}'" in shell or f"ble-face -s auto_complete {face}" in shell
        assert f"foreground {terminal}\n" in run("render-kitty").stdout

    check('#112233', 'fg=#112233,italic', '#224466')
    run("render-kitty", "--apply")
    local.write_text('schema=1\n[palette]\npath="#aabbcc"\n[terminal]\nforeground="#abcdef"\n'
                     '[completion]\n[ble.faces]\nauto_complete="fg=#aabbcc,bold"\n')
    check('#aabbcc', 'fg=#aabbcc,bold', '#abcdef')
    assert 'foreground #224466' in include.read_text()
    run("render-kitty", "--apply")
    rendered_B = include.read_bytes()
    # Removal of one key falls back to installed A, not committed gold.
    local.write_text(local.read_text().replace('path="#aabbcc"\n', ''))
    check('#112233', 'fg=#aabbcc,bold', '#abcdef')
    assert include.read_bytes() == rendered_B
    local.unlink()
    check('#112233', 'fg=#112233,italic', '#224466')
    assert include.read_bytes() == rendered_B
    run("render-kitty", "--apply")
    assert 'foreground #224466' in include.read_text()


def test_ansi_numeric_order(fixture):
    _, _, _, local, _, run = fixture
    local.write_text('schema=1\n[palette]\n[terminal]\n[completion]\n'
                     '[ansi]\ncolor10="#101010"\ncolor2="#020202"\ncolor0="#000000"\n')
    rows = [s for s in run("render-kitty").stdout.splitlines() if s.startswith('color')]
    assert rows == ['color0 #000000', 'color2 #020202', 'color10 #101010']


def test_emitted_shell_executes_only_valid_assignments_and_faces(fixture, style):
    root, _, _, local, env, run = fixture
    local.write_text('schema=1\n[palette]\n[terminal]\n[completion]\n'
                     '[ble.faces]\nauto_complete="fg=255,bg=orange,italic"\nregion="none"\n')
    shell = run("emit-shell").stdout
    command = ('BLE_VERSION=fixture\nble-import() { printf "import:%s\\n" "$*"; }\n'
               'ble-face() { printf "face:%s\\n" "$*"; }\n' + shell
               + '\nprintf "prompt:%s\\nmenu:%s:%s:%s\\n" "$STEEEF_LIGHTAMBER" '
                 '"$DINAS_STOCK_MENU_STYLE" "$DINAS_STOCK_MENU_ALIGN_MIN" "$DINAS_STOCK_MENU_ALIGN_MAX"\n')
    result = subprocess.run([shutil.which('bash'), '--noprofile', '--norc', '-c', command],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith('import:lib/core-complete\n')
    assert 'face:-s auto_complete fg=255,bg=orange,italic' in result.stdout
    assert 'face:-s region none' in result.stdout
    assert 'prompt:' + style.ansi('#ffd700') in result.stdout
    assert 'menu:align-nowrap:4:64' in result.stdout
    assert not (root / 'sentinel').exists()


def test_emission_reports_failed_completion_import_without_applying_faces(fixture):
    _, _, _, _, env, run = fixture
    command = ('BLE_VERSION=fixture\nble-import() { return 1; }\n'
               'ble-face() { echo UNEXPECTED-SETTER; }\n' + run('emit-shell').stdout)
    result = subprocess.run([shutil.which('bash'), '--noprofile', '--norc', '-c', command],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert result.stdout == ''
    assert result.stderr == 'DINAS stock Bash: completion module unavailable; ble faces not applied\n'


def test_sample_branch_status_and_virtualenv_use_declared_rgb_even_on_low_color_terminal(
    fixture, style, repo_root,
):
    root, _, _, _, env, run = fixture
    work = root / 'prompt-repository'
    work.mkdir()
    for args in [('init', '-b', 'style-fixture'), ('config', 'core.hooksPath', str(root / 'no-hooks')),
                 ('config', 'commit.gpgsign', 'false')]:
        subprocess.run(['git', '-C', str(work), *args], env=env, check=True, capture_output=True)
    (work / 'tracked').write_text('base\n')
    for args in [('add', 'tracked'), ('commit', '-m', 'fixture base')]:
        subprocess.run(['git', '-C', str(work), *args], env=env, check=True, capture_output=True)
    (work / 'tracked').write_text('unstaged\n')
    (work / 'staged').write_text('staged\n')
    subprocess.run(['git', '-C', str(work), 'add', 'staged'], env=env, check=True, capture_output=True)
    (work / 'untracked').write_text('untracked\n')
    command = ('tput() { printf 8; }\nsource '
               + str(repo_root / 'shell/sh_config/bash/steeef-custom.bash-theme')
               + '\nprintf "host-blue:%s\\n" "$STEEEF_BLUE"\n'
               + run('emit-shell').stdout
               + '\nVIRTUAL_ENV=/fixture/venv\nsteeef_git_info\nsteeef_virtualenv_info\n')
    result = subprocess.run([shutil.which('bash'), '--noprofile', '--norc', '-c', command],
                            cwd=work, env={**env, 'TERM': 'vt100'}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    gold = style.ansi('#ffd700')
    markers = ''.join(style.ansi(color) + '●' + gold for color in ('#d75f00', '#5fff5f', '#d7005f'))
    assert result.stdout == ('host-blue:' + r'\[\033[34m\]' + '\n('
        + style.ansi('#5fd7ff') + 'style-fixture' + gold + markers + ') ('
        + style.ansi('#0000ff') + 'venv' + r'\[\033[0m\]' + ') ')


def test_isolated_python_ignores_inherited_python_code(fixture, repo_root):
    root, _, _, _, env, run = fixture
    sentinel = root / 'injected-python'
    (root / 'sitecustomize.py').write_text(f"from pathlib import Path; Path({str(sentinel)!r}).touch()\n")
    env['PYTHONPATH'] = str(root)
    env['PYTHONSTARTUP'] = str(root / 'sitecustomize.py')
    assert run('emit-shell').stdout.startswith('STEEEF_LIGHTAMBER=')
    assert not sentinel.exists()
    entry = (repo_root / 'config/kitty/stock-bash-entry.bash').read_text()
    assert '@STYLE_PYTHON@ -I -S -B @STYLE_CLI@ emit-shell' in entry


def test_renderer_managed_write_and_noop_preserve_metadata(fixture):
    _, home, _, _, _, run = fixture
    run("render-kitty", "--apply")
    target = home / ".config/kitty/stock-bash-colors.conf"
    before = target.stat()
    assert before.st_mode & 0o777 == 0o600
    run("render-kitty", "--apply")
    after = target.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


@pytest.mark.parametrize("kind", ['unmanaged', 'prefix-collision', 'directory', 'symlink', 'hardlink'])
def test_renderer_refuses_collisions_and_linked_output(fixture, kind):
    root, home, _, _, _, run = fixture
    target = home / ".config/kitty/stock-bash-colors.conf"
    target.parent.mkdir(parents=True)
    external = root / "external"
    external.write_text('untouched')
    reason = 'unmanaged Kitty file'
    if kind == 'directory':
        target.mkdir(); reason = 'unowned target'
    elif kind == 'symlink':
        target.symlink_to(external); reason = 'symlink path'
    elif kind == 'hardlink':
        os.link(external, target); reason = 'hardlinked file'
    else:
        target.write_text('# DINAS stock Kitty palette collision\n' if kind == 'prefix-collision' else 'user colors\n')
    before = target.lstat()
    assert reason in run("render-kitty", "--apply", expected=2).stderr
    after = target.lstat()
    # Reading an ownership marker may update atime. Refusal must preserve
    # identity, ownership, permissions, content and modification metadata.
    for field in ("st_mode", "st_ino", "st_dev", "st_nlink", "st_uid", "st_gid",
                  "st_size", "st_mtime_ns", "st_ctime_ns"):
        assert getattr(after, field) == getattr(before, field), field
    if kind in {'unmanaged', 'prefix-collision'}:
        expected = '# DINAS stock Kitty palette collision\n' if kind == 'prefix-collision' else 'user colors\n'
        assert target.read_text() == expected
    assert external.read_text() == 'untouched'


@pytest.mark.parametrize("kind", ['input-symlink', 'input-hardlink', 'parent-symlink', 'output-parent-symlink'])
def test_intentional_links_stay_visible_to_validator(fixture, kind):
    root, home, _, local, _, run = fixture
    external = root / "external"
    external.write_text('schema=1\n')
    reason = 'symlink path'
    if kind == 'input-symlink':
        local.symlink_to(external)
    elif kind == 'input-hardlink':
        os.link(external, local); reason = 'hardlinked file'
    elif kind == 'parent-symlink':
        local.parent.rmdir(); local.parent.symlink_to(root)
    else:
        (home / '.config').symlink_to(root)
    mode = 'render-kitty' if kind == 'output-parent-symlink' else 'validate'
    args = ('--apply',) if mode == 'render-kitty' else ()
    assert reason in run(mode, *args, expected=2).stderr
    assert external.read_text() == 'schema=1\n'


def test_aliased_temporary_parent_is_canonicalized_by_fixture(fixture, style):
    root, _, default, _, _, _ = fixture
    alias = root / "temporary-alias"
    alias.symlink_to(root, target_is_directory=True)
    canonical = (alias / 'another-home').resolve()
    canonical.mkdir()
    assert style.selected(canonical, default=default)['palette']['path'] == '#ffd700'
    with pytest.raises(style.Invalid, match='symlink path'):
        style.selected(alias / 'another-home', default=default)


def test_store_file_interface_has_explicit_fallback(fixture):
    root, _, default, _, _, run = fixture
    store_file = root / "standalone-store-file.py"
    shutil.copyfile(Path(__file__).resolve().parents[4] / 'tools/kitty-stock-style.py', store_file)
    assert run('validate', program=store_file, default=default).stdout == 'STYLE_VALID=true\n'


def test_manual_default_discovers_same_installed_palette_as_startup(fixture, style, monkeypatch):
    root, home, default, _, _, _ = fixture
    default.write_text(default.read_text().replace('#ffd700', '#123456'))
    profile_alias = root / 'profile-palette.toml'
    profile_alias.symlink_to(default)
    monkeypatch.setattr(style, 'INSTALLED_DEFAULT', profile_alias)
    assert style.default_path() == default
    assert style.selected(home) == style.selected(home, default=default)
    profile_alias.unlink()
    assert style.default_path() == style.SOURCE_DEFAULT


def test_hardlinked_fallback_works_explicitly_and_through_profile_alias(fixture, style, monkeypatch):
    root, home, default, _, _, run = fixture
    before = run('emit-shell').stdout
    os.link(default, root / 'store-optimisation-link')
    assert default.stat().st_nlink == 2
    assert run('emit-shell').stdout == before
    alias = root / 'profile-default.toml'
    alias.symlink_to(default)
    monkeypatch.setattr(style, 'INSTALLED_DEFAULT', alias)
    assert style.selected(home) == style.selected(home, default=default)
    run('init-user')
    assert (home / '.local/nix-darwin/config/kitty-bash.toml').stat().st_nlink == 1


def test_init_user_collision_and_explicit_modes(fixture):
    _, _, default, local, _, run = fixture
    run('init-user')
    assert local.read_bytes() == default.read_bytes()
    assert local.stat().st_mode & 0o777 == 0o600
    assert 'File exists' in run('init-user', expected=2).stderr
    assert '--apply is render-kitty only' in run('validate', '--apply', expected=2).stderr
    assert '--commit is promote only' in run('validate', '--commit', expected=2).stderr


def test_fixture_promotion_dry_run_then_commit_only_palette(fixture):
    root, _, default, local, env, run = fixture
    repo = root / 'disposable-repository'
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], env=env, text=True).strip()

    git('init', '-b', 'main')
    git('config', 'core.hooksPath', str(root / 'no-hooks'))
    git('config', 'commit.gpgsign', 'false')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    dest = repo / 'config/kitty/stock-bash-style.toml'
    dest.parent.mkdir(parents=True)
    shutil.copyfile(default, dest)
    git('add', '.'); git('commit', '-m', 'fixture base')
    initial = git('rev-parse', 'HEAD')
    local.write_text('schema=1\n[palette]\npath="#123456"\n[terminal]\n[completion]\n')
    env['GIT_DIR'] = str(root / 'unrelated-git-directory')
    assert 'PROMOTE_DRY_RUN=true' in run('promote', '--repo', repo).stdout
    del env['GIT_DIR']
    assert git('rev-parse', 'HEAD') == initial and git('status', '--porcelain') == ''
    (repo / 'unrelated').write_text('preserve')
    git('add', 'unrelated')
    assert 'index already staged' in run('promote', '--repo', repo, '--commit', expected=2).stderr
    git('reset', '--', 'unrelated')
    assert 'COMMITTED=' in run('promote', '--repo', repo, '--commit').stdout
    assert git('rev-parse', 'HEAD') != initial
    assert git('diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD') == 'config/kitty/stock-bash-style.toml'
    assert '#123456' in dest.read_text()
    assert (repo / 'unrelated').read_text() == 'preserve'


@pytest.fixture
def nix_style(repo_root, fixture):
    """Optional cached-library evaluation, with no fetching or realization."""
    library = os.environ.get('KITTY_STYLE_TEST_NIX_LIB')
    nix = shutil.which('nix-instantiate')
    if not library or not nix:
        pytest.skip('Set KITTY_STYLE_TEST_NIX_LIB to an existing nixpkgs lib for offline typed-style checks')
    _, _, _, _, env, _ = fixture

    def evaluate(expression, expected=0):
        import json
        prefix = ('let lib = import (builtins.toPath ' + json.dumps(library) + '); '
                  'style = import (builtins.toPath ' + json.dumps(str(repo_root / 'lib/kitty-stock-style.nix'))
                  + ') { inherit lib; }; base = { schema=1; palette={}; terminal={}; completion={}; }; in ')
        result = subprocess.run([nix, '--eval', '--strict', '--json',
                                 '--option', 'allow-import-from-derivation', 'false',
                                 '--expr', prefix + expression], env=env, capture_output=True,
                                text=True, timeout=15)
        assert result.returncode == expected, result.stderr
        return result

    return evaluate


@pytest.mark.parametrize(('override', 'reason'), [
    ('{ schema=true; }', 'schema must equal 1'),
    ('{ unknown=1; }', 'unknown Kitty key'),
    ('{ palette="scalar"; }', 'scalar parent: palette'),
    ('{ palette={ path="bad"; }; }', 'malformed color: palette.path'),
    ('{ terminal={ cursor=false; }; }', 'malformed color: terminal.cursor'),
    ('{ ansi={ color16="#123456"; }; }', 'unknown Kitty key: ansi.color16'),
    ('{ completion={ align_min=true; }; }', 'completion width must be integer 1..150'),
    ('{ completion={ align_min=151; }; }', 'completion width must be integer 1..150'),
    ('{ completion={ align_min=64; align_max=4; }; }', 'align_min > align_max'),
    ('{ completion={ style="shell;command"; }; }', 'unsupported completion style'),
    ('{ completion={ style={}; }; }', 'unsupported completion style'),
    ('{ ble={ other={}; }; }', 'only [ble.faces]'),
    ('{ ble={ faces={ invented="none"; }; }; }', 'unknown ble.sh face'),
    ('{ ble={ faces={ auto_complete="bold,bold"; }; }; }', 'duplicate style flag'),
    ('{ ble={ faces={ auto_complete="fg=999"; }; }; }', 'unknown color'),
    ('{ ble={ faces={ auto_complete="fg=١٢"; }; }; }', 'unknown color'),
    ('{ ble={ faces={ auto_complete="bg=１２"; }; }; }', 'unknown color'),
    ('{ ble={ faces={ auto_complete="$(command)"; }; }; }', 'graphic spec'),
])
def test_typed_installation_refuses_for_intended_reason(nix_style, override, reason):
    result = nix_style('style.validateToml (base // ' + override + ')', expected=1)
    assert reason in result.stderr
    assert result.stdout == ''


def test_typed_render_roundtrip_is_consumed_by_real_cli(nix_style, fixture):
    import json
    root, _, fallback, local, _, run = fixture
    sample = json.dumps(str(fallback))
    expression = ('style.renderToml (style.mergeStyles { sample = builtins.fromTOML '
                  '(builtins.readFile (builtins.toPath ' + sample + ')); '
                  'projected = style.validateToml (base // { palette={ path="#112233"; }; '
                  'ansi={color10="#101010";color2="#020202";}; '
                  'ble.faces.auto_complete="fg=007,bold"; }); })')
    fallback.write_text(json.loads(nix_style(expression).stdout))
    assert 'foreground #33ff00' in run('render-kitty').stdout
    assert 'color2 #020202\ncolor10 #101010' in run('render-kitty').stdout
    assert 'fg=007,bold' in run('emit-shell').stdout
    local.write_text('schema=1\n[palette]\npath="#aabbcc"\n[terminal]\n[completion]\n')
    assert '170;187;204' in run('emit-shell').stdout
    local.unlink()
    assert '17;34;51' in run('emit-shell').stdout


def test_python_and_nix_accept_the_same_pinned_face_table(nix_style, style):
    assert set(json.loads(nix_style('style.bleFaces').stdout)) == style.BLE_FACES


@pytest.mark.parametrize('missing', ['schema', 'palette', 'terminal', 'completion', 'all-tables', None])
def test_requiredness_matches_real_allowlist_projection_and_nix(
    missing, nix_style, fixture, repo_root, monkeypatch,
):
    root, home, fallback, local, _, run = fixture
    declaration = ('(import (builtins.toPath ' + json.dumps(str(repo_root / 'flake.nix'))
        + ')).outputs { self={}; nix-darwin={}; nixpkgs={ inherit lib; }; nixpkgs-unstable={}; }')
    contract = json.loads(nix_style('{ allowlist=(' + declaration + ').configAllowlist; '
                                   'top=style.allowedTopLevelKeys; }').stdout)
    allowlist = contract['allowlist']
    assert sorted(k.rstrip('?').split('.')[0] for k in allowlist['kitty-bash.toml']) == contract['top']
    parts = {'schema': 'schema=1\n', 'palette': '[palette]\n',
             'terminal': '[terminal]\n', 'completion': '[completion]\n'}
    text = ''.join(value for key, value in parts.items()
                   if key != missing and not (missing == 'all-tables' and key != 'schema'))
    local.write_text(text)
    monkeypatch.syspath_prepend(str(repo_root / 'libexec'))
    projection = importlib.import_module('nix_darwin_local.projection')
    paths = importlib.import_module('nix_darwin_local.paths')
    expression = 'style.validateToml (builtins.fromTOML (builtins.readFile (builtins.toPath ' + json.dumps(str(local)) + ')))'
    if missing is not None:
        assert 'STYLE_REFUSED=' in run('validate', expected=2).stderr
        with pytest.raises(paths.ConfigError, match='allowlisted key is missing'):
            projection.projected_files(local.parent, allowlist)
        nix_style(expression, expected=1)
    else:
        assert run('validate').stdout == 'STYLE_VALID=true\n'
        projected = projection.projected_files(local.parent, allowlist)['kitty-bash.toml']
        assert tomllib.loads(projected.decode()) == tomllib.loads(text)
        nix_style(expression)
        # The public default must remain accepted through the same projection.
        local.write_bytes((repo_root / 'config/nix-darwin/default/kitty-bash.toml').read_bytes())
        projection.projected_files(local.parent, allowlist)
        run('validate')
