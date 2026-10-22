#!/usr/bin/env python3
"""Offline unit and integration tests for stock Kitty enhanced Bash shell behavior.

Tests cover:
- Rendering of @...@ placeholders in stock-bash-* templates
- PTY-driven launch with no arguments and extra arguments forwarding
- Exclusions: DINAS_PLAIN_BASH, bash -c commands, non-tty streams, dumb terminal, SSH
- Fallbacks: absent/broken ble.sh (fallback to Readline), missing optional tools (fzf, zoxide, direnv)
- Exit status preservation across prompt cycles
- String and array PROMPT_COMMAND hooks and single-tail idempotence on re-sourcing
- Ble.sh lifecycle ordering: detach -> completion -> config -> delayed fzf -> attach-last
- Readline prefix search and tab completion under PTY
- Navigation helpers: up [count] and ordinary cd builtin preservation
- HISTFILE desired fallback to $HOME/.bash_history and unexported state
- Selection isolation: launching unchanged Kitty Bash login argv, zsh -l, Terminal Bash, bash -c, and nested shells
- Baseline prompt, alias, and environment comparisons
- Regression for manager B1: host nested shell banner parity
- Dependency-backed test skips with explicit reasons
"""
from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

REPO_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_DIR / "config" / "kitty"
SHELL_DIR = REPO_DIR / "shell"
SCRATCH_TMP = Path(os.environ["KITTY_STOCK_TEST_TMP"]).resolve() / "shell-tests"
SCRATCH_LOGS = Path(os.environ["KITTY_STOCK_TEST_LOGS"])
PTY_DRIVER = REPO_DIR / "tests" / "kitty-stock-pty_drive.py"

MODERN_BASH = "/run/current-system/sw/bin/bash"
FALLBACK_BASH = "/bin/bash"
SYSTEM_ZSH = "/bin/zsh"


def setUpModule():
    SCRATCH_TMP.mkdir(parents=True, exist_ok=True)
    SCRATCH_LOGS.mkdir(parents=True, exist_ok=True)


class ShellFixture:
    """Manages an isolated fixture environment with mock tools, templates, and HOME."""

    def __init__(
        self,
        base_dir: Path,
        *,
        blesh_content: str | None = None,
        blesh_missing: bool = False,
        direnv_enabled: bool = False,
        custom_env: dict[str, str] | None = None,
    ):
        base_dir = base_dir.resolve()
        self.base_dir = base_dir
        self.home_dir = base_dir / "home"
        self.home_dir.mkdir(parents=True, mode=0o755, exist_ok=True)
        self.bin_dir = base_dir / "bin"
        self.bin_dir.mkdir(parents=True, mode=0o755, exist_ok=True)
        self.etc_dir = base_dir / "etc"
        self.etc_dir.mkdir(parents=True, mode=0o755, exist_ok=True)
        self.share_dir = base_dir / "share" / "kitty-stock-bash"
        self.share_dir.mkdir(parents=True, mode=0o755, exist_ok=True)
        self.tools_dir = base_dir / "tools"
        (self.tools_dir / "bin").mkdir(parents=True, mode=0o755, exist_ok=True)
        self.transcripts_dir = base_dir / "transcripts"
        self.transcripts_dir.mkdir(parents=True, mode=0o755, exist_ok=True)
        self.m_dir = base_dir / "m"
        self.m_dir.mkdir(parents=True, mode=0o755, exist_ok=True)

        self.events_log = self.m_dir / "events.log"
        self.events_log.touch()

        self._setup_home()
        self._setup_etc()
        self._setup_mocks(
            blesh_content=blesh_content,
            blesh_missing=blesh_missing,
            direnv_enabled=direnv_enabled,
        )
        self._render_scripts(direnv_enabled=direnv_enabled)

    def _setup_home(self):
        """Populate fixture HOME with host rc files and theme."""
        (self.home_dir / ".bash_profile").write_text((SHELL_DIR / "bash_profile").read_text())
        (self.home_dir / ".bashrc").write_text((SHELL_DIR / "bashrc").read_text())

        sh_config_bash = self.home_dir / ".sh_config" / "bash"
        sh_config_common = self.home_dir / ".sh_config" / "common"
        sh_config_bash.mkdir(parents=True, mode=0o755, exist_ok=True)
        sh_config_common.mkdir(parents=True, mode=0o755, exist_ok=True)

        theme_src = SHELL_DIR / "sh_config" / "bash" / "steeef-custom.bash-theme"
        (sh_config_bash / "steeef-custom.bash-theme").write_text(theme_src.read_text())

        for name in ("001_aliases", "010_env_snapshot", "999_on-login"):
            src = SHELL_DIR / "sh_config" / "common" / name
            (sh_config_common / name).write_text(src.read_text())

    def _setup_etc(self):
        """Create non-real system profile and bashrc fixtures."""
        (self.etc_dir / "profile").write_text("# Fixture system profile\nexport FIXTURE_SYS_PROFILE=1\n")
        (self.etc_dir / "bashrc").write_text("# Fixture system bashrc\nexport FIXTURE_SYS_BASHRC=1\n")
        self.baseline_entry = self.etc_dir / "baseline-entry"
        self.baseline_entry.write_text(
            'set +o posix\nshopt -u inherit_errexit\nunset ENV\n'
            + f'source "{self.etc_dir}/profile"\nsource "$HOME/.bash_profile"\n')

    def _setup_mocks(self, *, blesh_content: str | None, blesh_missing: bool, direnv_enabled: bool):
        """Create mock binaries and enhancement packages."""
        # 1. mock login
        mock_login = self.bin_dir / "mock_login"
        mock_login.write_text(
            f"#!{MODERN_BASH}\n"
            "set -e\n"
            "while [[ $# -gt 0 ]]; do\n"
            "  case \"$1\" in\n"
            "    -f|-p|-fp|-pf) shift ;;\n"
            "    *) break ;;\n"
            "  esac\n"
            "done\n"
            "if [[ $# -gt 0 && ! -x \"$1\" && ! -f \"$1\" ]]; then\n"
            "  shift\n"
            "fi\n"
            "unset ENV\n"
            "cd \"$HOME\" || exit 1\n"
            "program=$1; shift\n"
            f'if [[ $program == {MODERN_BASH} && $# == 0 ]]; then\n'
            f'  export ENV="{self.baseline_entry}"\n'
            f'  exec -a -bash "{MODERN_BASH}" --posix\n'
            'elif [[ ${program##*/} == bash ]]; then\n'
            '  exec -a -bash "$program" --noprofile "$@"\n'
            'else\n  exec "$program" "$@"\nfi\n'
        )
        mock_login.chmod(0o755)
        self.fallback = self.bin_dir / "ordinary-fallback"
        self.fallback.write_text(
            f'#!{MODERN_BASH}\nexport ENV="{self.baseline_entry}"\n'
            f'exec -a -bash {MODERN_BASH} --posix "$@"\n')
        self.fallback.chmod(0o755)

        # 2. mock env-snapshot (must not invoke real env-snapshot)
        mock_env_snapshot = self.bin_dir / "env-snapshot"
        mock_env_snapshot.write_text(
            f"#!{MODERN_BASH}\n"
            f"printf 'env-snapshot %s\\n' \"$*\" >> \"{self.events_log}\"\n"
            "exit 0\n"
        )
        mock_env_snapshot.chmod(0o755)

        # 3. mock fastfetch (records window identity and banner call)
        mock_fastfetch = self.bin_dir / "fastfetch"
        mock_fastfetch.write_text(
            f"#!{MODERN_BASH}\n"
            f"printf 'fastfetch: %s:%s\\n' \"${{KITTY_PID:-none}}\" \"${{KITTY_WINDOW_ID:-none}}\" >> \"{self.events_log}\"\n"
            "printf 'OS: Darwin (mock)\\nHost: mock-host\\n'\n"
        )
        mock_fastfetch.chmod(0o755)
        (self.bin_dir / "hostname").write_text("#!/bin/sh\nprintf 'fixture-host\\n'\n")
        (self.bin_dir / "hostname").chmod(0o755)

        # 4. mock ble.sh
        self.blesh_path = self.base_dir / "mock_ble.sh"
        if blesh_missing:
            self.blesh_path = self.base_dir / "nonexistent_ble.sh"
        elif blesh_content is not None:
            self.blesh_path.write_text(blesh_content)
        else:
            self.blesh_path.write_text(
                "# Mock ble.sh for adapter contract verification\n"
                "if [[ \"$1\" == \"--noattach\" ]]; then\n"
                "  BLE_VERSION=\"0.4.0-mock\"\n"
                "  BLE_ATTACHED=0\n"
                f"  printf 'detach\\n' >> \"{self.events_log}\"\n"
                "fi\n"
                "ble-detach() {\n"
                "  BLE_ATTACHED=0\n"
                f"  printf 'ble-detach\\n' >> \"{self.events_log}\"\n"
                "}\n"
                "ble-attach() {\n"
                "  BLE_ATTACHED=1\n"
                f"  printf 'attach-last\\n' >> \"{self.events_log}\"\n"
                "}\n"
                "bleopt() {\n"
                f"  printf 'bleopt %s\\n' \"$*\" >> \"{self.events_log}\"\n"
                "}\n"
                "ble-bind() {\n"
                f"  printf 'ble-bind %s\\n' \"$*\" >> \"{self.events_log}\"\n"
                "}\n"
                "ble-import() {\n"
                f"  printf 'ble-import %s\\n' \"$*\" >> \"{self.events_log}\"\n"
                "}\n"
            )

        # 5. mock bash-completion
        self.completion_path = self.base_dir / "mock_completion.sh"
        self.completion_path.write_text(
            "# Mock bash-completion\n"
            "export BASH_COMPLETION_VERSINFO=(2 11)\n"
            f"printf 'completion\\n' >> \"{self.events_log}\"\n"
        )

        # 6. mock fzf
        self.fzf_dir = self.base_dir / "fzf"
        self.fzf_dir.mkdir(exist_ok=True)
        (self.fzf_dir / "completion.bash").write_text(
            f"printf 'fzf-completion-file-sourced\\n' >> \"{self.events_log}\"\n"
        )
        (self.fzf_dir / "key-bindings.bash").write_text(
            f"printf 'fzf-key-bindings-file-sourced\\n' >> \"{self.events_log}\"\n"
        )

        # 7. mock zoxide
        self.zoxide_bin = self.tools_dir / "bin" / "zoxide"
        self.zoxide_bin.write_text(
            f"#!{MODERN_BASH}\n"
            "if [[ \"$1\" == \"init\" && \"$2\" == \"bash\" ]]; then\n"
            "  cat <<'EOF'\n"
            "__zoxide_hook() { :; }\n"
            "z() { cd \"$@\" || return; }\n"
            "zi() { cd \"$@\" || return; }\n"
            "EOF\n"
            "fi\n"
        )
        self.zoxide_bin.chmod(0o755)

        # 8. mock direnv
        self.direnv_bin = self.tools_dir / "bin" / "direnv"
        self.direnv_bin.write_text(
            f"#!{MODERN_BASH}\n"
            "if [[ \"$1\" == \"hook\" && \"$2\" == \"bash\" ]]; then\n"
            "  cat <<'EOF'\n"
            "_direnv_hook() { :; }\n"
            "EOF\n"
            "fi\n"
        )
        self.direnv_bin.chmod(0o755)

    def _render_scripts(self, *, direnv_enabled: bool):
        """Render templates substituting @...@ placeholders."""
        mock_login = self.bin_dir / "mock_login"
        sys_profile = self.etc_dir / "profile"
        sys_bashrc = self.etc_dir / "bashrc"
        self.style_default = self.share_dir / "style-default.toml"
        shutil.copyfile(CONFIG_DIR / "stock-bash-style.toml", self.style_default)

        # 1. early.bash
        early_tpl = (CONFIG_DIR / "stock-bash-early.bash").read_text()
        early_rendered = early_tpl.replace("@BLESH@", str(self.blesh_path))
        self.early_file = self.share_dir / "early.bash"
        self.early_file.write_text(early_rendered)

        # 2. interactive.bash
        inter_tpl = (CONFIG_DIR / "stock-bash-interactive.bash").read_text()
        inter_rendered = (
            inter_tpl.replace("@COMPLETION@", str(self.completion_path))
            .replace("@FZF@", str(self.fzf_dir))
            .replace("@ZOXIDE@", str(self.zoxide_bin))
            .replace("@DIRENV_ENABLED@", "1" if direnv_enabled else "0")
            .replace("@DIRENV@", str(self.direnv_bin))
            .replace("@TOOLS@", str(self.tools_dir))
        )
        self.interactive_file = self.share_dir / "interactive.bash"
        self.interactive_file.write_text(inter_rendered)

        # 3. entry.bash
        entry_tpl = (CONFIG_DIR / "stock-bash-entry.bash").read_text()
        entry_rendered = (
            entry_tpl.replace("@EARLY@", str(self.early_file))
            .replace("@BASH@", MODERN_BASH)
            .replace("@STYLE_PYTHON@", sys.executable)
            .replace("@STYLE_CLI@", str(REPO_DIR / "tools/kitty-stock-style.py"))
            .replace("@STYLE_DEFAULT@", str(self.style_default))
            .replace("@SYSTEM_PROFILE@", str(sys_profile))
            .replace("@SYSTEM_BASHRC@", str(sys_bashrc))
            .replace("@INTERACTIVE@", str(self.interactive_file))
        )
        self.entry_file = self.share_dir / "entry.bash"
        self.entry_file.write_text(entry_rendered)

        # 4. stage2.sh
        stage2_tpl = (CONFIG_DIR / "stock-bash-stage2.sh").read_text()
        stage2_rendered = (
            stage2_tpl.replace("@BASH@", MODERN_BASH)
            .replace("@ENTRY@", str(self.entry_file))
            .replace("@FALLBACK_BASH@", str(self.fallback))
        )
        self.stage2_file = self.share_dir / "stage2"
        self.stage2_file.write_text(stage2_rendered)
        self.stage2_file.chmod(0o755)

        # 5. launch.sh
        launch_tpl = (CONFIG_DIR / "stock-bash-launch.sh").read_text()
        launch_rendered = (
            launch_tpl.replace("@LOGIN@", str(mock_login))
            .replace("@BASH@", MODERN_BASH)
            .replace("@STAGE2@", str(self.stage2_file))
            .replace("@FALLBACK_BASH@", str(self.fallback))
        )
        self.launch_file = self.share_dir / "launch"
        self.launch_file.write_text(launch_rendered)
        self.launch_file.chmod(0o755)

    def get_clean_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Produce a clean environment without host user profile leakage."""
        env = {
            "HOME": str(self.home_dir),
            "TMPDIR": str(self.base_dir),
            "TMP": str(self.base_dir),
            "TEMP": str(self.base_dir),
            "PATH": f"{self.bin_dir}:/run/current-system/sw/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "TERM": "xterm-256color",
            "USER": "fixture",
            "HOSTNAME": "fixture-host",
            # Nix Bash's host /etc/bashrc opt-out, from the pinned donor tests.
            "NOSYSBASHRC": "1",
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "FIXTURE_EVENTS_LOG": str(self.events_log),
        }
        if extra:
            for k, v in extra.items():
                if v is None:
                    env.pop(k, None)
                else:
                    env[k] = v
        return env

    def run_pty(
        self,
        command: list[str],
        script_steps: str,
        *,
        name: str = "session",
        env_extra: dict[str, str] | None = None,
        timeout: float = 30.0,
        cwd: Path | None = None,
    ) -> tuple[int, bytes]:
        """Drive a session under pseudo-terminal with kitty-stock-pty_drive.py."""
        transcript_path = self.transcripts_dir / f"{name}.transcript"
        steps_rendered = script_steps.replace("@M@", str(self.m_dir))

        env = self.get_clean_env(env_extra)
        run_cwd = cwd or self.m_dir

        cmd = [
            sys.executable,
            str(PTY_DRIVER),
            "--transcript",
            str(transcript_path),
            "--timeout",
            str(timeout),
            "--",
        ] + command

        res = subprocess.run(
            cmd,
            input=steps_rendered,
            text=True,
            capture_output=True,
            env=env,
            cwd=str(run_cwd),
        )

        output_bytes = transcript_path.read_bytes() if transcript_path.exists() else b""
        SCRATCH_LOGS.mkdir(parents=True, exist_ok=True)
        retained = output_bytes.replace(str(self.base_dir).encode(), b"<fixture>")
        retained = retained.replace(getpass.getuser().encode(), b"<user>")
        (SCRATCH_LOGS / f"pty-{name}.log").write_bytes(retained)
        if res.returncode != 0:
            (SCRATCH_LOGS / f"failure-{name}.log").write_bytes(retained)

        return res.returncode, output_bytes


class KittyStockRenderingTests(unittest.TestCase):
    """Validates complete placeholder substitution across all stock scripts."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=str(SCRATCH_TMP), prefix="render-")
        self.fixture = ShellFixture(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_render_all_placeholders_substituted(self):
        files_to_check = [
            self.fixture.launch_file,
            self.fixture.stage2_file,
            self.fixture.entry_file,
            self.fixture.early_file,
            self.fixture.interactive_file,
        ]
        for f in files_to_check:
            content = f.read_text()
            matches = re.findall(r"@[A-Z_]+@", content)
            self.assertEqual(matches, [], f"Unexpanded tokens found in {f.name}: {matches}")

    def test_executable_permissions(self):
        for script in (self.fixture.launch_file, self.fixture.stage2_file):
            mode = script.stat().st_mode
            self.assertTrue(bool(mode & stat.S_IXUSR), f"{script} must be executable")


class KittyStockLaunchTests(unittest.TestCase):
    """Tests launch.sh and stage2 behavior under pseudo-terminal and direct invocation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=str(SCRATCH_TMP), prefix="launch-")
        self.fixture = ShellFixture(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_noargs_launch_under_pty(self):
        steps = (
            "line touch @M@/started\n"
            "wait @M@/started\n"
            "line echo \"OWNER:$BASHPID\" > @M@/owner\n"
            "wait @M@/owner\n"
            "line echo \"INTERACTIVE:$-:\" > @M@/interactive\n"
            "wait @M@/interactive\n"
            "line exit\n"
        )
        rc, transcript = self.fixture.run_pty(
            [str(self.fixture.launch_file)],
            steps,
            name="launch-noargs",
            timeout=15.0,
        )
        self.assertEqual(rc, 0, "noargs launch under PTY must exit 0")
        self.assertTrue((self.fixture.m_dir / "started").exists())

        owner = (self.fixture.m_dir / "owner").read_text().strip()
        self.assertTrue(owner.startswith("OWNER:"))
        pid = owner.split(":", 1)[1]
        self.assertTrue(pid.isdigit())

        interactive = (self.fixture.m_dir / "interactive").read_text().strip()
        self.assertIn("i", interactive, "Shell must have interactive flag 'i' set")

    def test_extra_arguments_forwarding_terminates(self):
        target = self.fixture.m_dir / "extra_ran.txt"
        env = self.fixture.get_clean_env()

        # Extra args forwarding should execute via @BASH@ "$@" and terminate
        cmd = [str(self.fixture.launch_file), "-c", f"echo extra-forwarded > '{target}'"]
        res = subprocess.run(cmd, env=env, cwd=str(self.fixture.m_dir), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text().strip(), "extra-forwarded")

        # Verify exit status is forwarded correctly
        cmd_status = [str(self.fixture.launch_file), "-c", "exit 42"]
        res_status = subprocess.run(cmd_status, env=env, cwd=str(self.fixture.m_dir), capture_output=True)
        self.assertEqual(res_status.returncode, 42)

        # Extras must not have run
        events = self.fixture.events_log.read_text()
        self.assertNotIn("detach", events, "Stage2/early should not execute for forwarded commands")
        self.assertNotIn("attach-last", events)

    def test_cwd_restoration_after_login(self):
        sub_dir = self.fixture.m_dir / "requested_cwd"
        sub_dir.mkdir(parents=True, exist_ok=True)
        steps = (
            "line pwd > @M@/actual_cwd\n"
            "wait @M@/actual_cwd\n"
            "line exit\n"
        )
        rc, _ = self.fixture.run_pty(
            [str(self.fixture.launch_file)],
            steps,
            name="launch-cwd",
            cwd=sub_dir,
            timeout=15.0,
        )
        self.assertEqual(rc, 0)
        actual_cwd = (self.fixture.m_dir / "actual_cwd").read_text().strip()
        # On macOS, /private/tmp vs /tmp symlink resolution: resolve both paths
        self.assertEqual(Path(actual_cwd).resolve(), sub_dir.resolve())


class KittyStockExclusionTests(unittest.TestCase):
    """Verifies that exclusions prevent stock enhancements from loading."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=str(SCRATCH_TMP), prefix="exclude-")
        self.fixture = ShellFixture(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_exclusion_plain_dinas_bash(self):
        steps = (
            "line echo \"BLE:${BLE_VERSION-none}\" > @M@/ble\n"
            "wait @M@/ble\n"
            "line type -t z > @M@/z\n"
            "wait @M@/z\n"
            "line echo \"PC:${PROMPT_COMMAND[*]}\" > @M@/pc\n"
            "wait @M@/pc\n"
            "line exit\n"
        )
        rc, _ = self.fixture.run_pty(
            [str(self.fixture.launch_file)],
            steps,
            name="plain-dinas-bash",
            env_extra={"DINAS_PLAIN_BASH": "1"},
            timeout=15.0,
        )
        self.assertEqual(rc, 0)
        self.assertEqual((self.fixture.m_dir / "ble").read_text().strip(), "BLE:none")
        self.assertEqual((self.fixture.m_dir / "z").read_text().strip(), "")
        pc = (self.fixture.m_dir / "pc").read_text().strip()
        self.assertIn("_dinas_stock_history_hook", pc)
        self.assertIn("steeef_prompt_command", pc)

        events = self.fixture.events_log.read_text()
        self.assertNotIn("detach", events)
        self.assertNotIn("attach-last", events)

    def test_exclusion_redirected_stream_not_a_tty(self):
        # Non-tty execution (piped input) must bypass stage2 enhancement
        env = self.fixture.get_clean_env()
        script = 'echo "BLE:${BLE_VERSION-none}"; echo "PC:${PROMPT_COMMAND-unset}"'
        res = subprocess.run(
            [str(self.fixture.launch_file)],
            input=script,
            text=True,
            capture_output=True,
            env=env,
            cwd=str(self.fixture.m_dir),
        )
        self.assertIn("BLE:none", res.stdout)
        events = self.fixture.events_log.read_text()
        self.assertNotIn("detach", events)

    def test_exclusion_dumb_terminal(self):
        steps = (
            "line echo \"BLE:${BLE_VERSION-none}\" > @M@/ble\n"
            "wait @M@/ble\n"
            "line exit\n"
        )
        rc, _ = self.fixture.run_pty(
            [str(self.fixture.launch_file)],
            steps,
            name="dumb-term",
            env_extra={"TERM": "dumb"},
            timeout=15.0,
        )
        self.assertEqual(rc, 0)
        self.assertEqual((self.fixture.m_dir / "ble").read_text().strip(), "BLE:none")
        events = self.fixture.events_log.read_text()
        self.assertNotIn("detach", events)

    def test_exclusion_ssh_connection(self):
        steps = (
            "line echo \"BLE:${BLE_VERSION-none}\" > @M@/ble\n"
            "wait @M@/ble\n"
            "line exit\n"
        )
        rc, _ = self.fixture.run_pty(
            [str(self.fixture.launch_file)],
            steps,
            name="ssh-conn",
            env_extra={"SSH_CONNECTION": "10.0.0.1 22 10.0.0.2 22"},
            timeout=15.0,
        )
        self.assertEqual(rc, 0)
        self.assertEqual((self.fixture.m_dir / "ble").read_text().strip(), "BLE:none")
        events = self.fixture.events_log.read_text()
        self.assertNotIn("detach", events)

    def test_absent_ble_falls_back_to_readline(self):
        absent_tmp = tempfile.TemporaryDirectory(dir=str(SCRATCH_TMP), prefix="absent-ble-")
        fixture = ShellFixture(Path(absent_tmp.name), blesh_missing=True)
        try:
            steps = (
                "line echo \"BLE:${BLE_VERSION-none}\" > @M@/ble\n"
                "wait @M@/ble\n"
                "line bind -p | grep history-search > @M@/bindings\n"
                "wait @M@/bindings\n"
                "line exit\n"
            )
            rc, _ = fixture.run_pty([str(fixture.launch_file)], steps, name="absent-ble", timeout=15.0)
            self.assertEqual(rc, 0)
            self.assertEqual((fixture.m_dir / "ble").read_text().strip(), "BLE:none")
            bindings = (fixture.m_dir / "bindings").read_text()
            self.assertIn('"\\e[A": history-search-backward', bindings)
            self.assertIn('"\\e[B": history-search-forward', bindings)
        finally:
            absent_tmp.cleanup()

    def test_broken_ble_falls_back_to_readline(self):
        broken_tmp = tempfile.TemporaryDirectory(dir=str(SCRATCH_TMP), prefix="broken-ble-")
        fixture = ShellFixture(Path(broken_tmp.name), blesh_content="if then\n")
        try:
            steps = (
                "line echo \"BLE:${BLE_VERSION-none}\" > @M@/ble\n"
                "wait @M@/ble\n"
                "line exit\n"
            )
            rc, _ = fixture.run_pty([str(fixture.launch_file)], steps, name="broken-ble", timeout=15.0)
            self.assertEqual(rc, 0)
            self.assertEqual((fixture.m_dir / "ble").read_text().strip(), "BLE:none")
        finally:
            broken_tmp.cleanup()


class KittyStockHooksAndHistoryTests(unittest.TestCase):
    """Tests prompt command hook composition, re-source idempotence, and lifecycle order."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=str(SCRATCH_TMP), prefix="hooks-")
        self.fixture = ShellFixture(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_exit_status_preservation(self):
        steps = (
            "line (exit 42)\n"
            "line echo $? > @M@/status\n"
            "wait @M@/status\n"
            "line exit\n"
        )
        rc, _ = self.fixture.run_pty([str(self.fixture.launch_file)], steps, name="exit-status", timeout=15.0)
        self.assertEqual(rc, 0)
        status = (self.fixture.m_dir / "status").read_text().strip()
        self.assertEqual(status, "42", "Hook must preserve prior exit status")

    def test_prompt_command_string_and_array_composition(self):
        steps = (
            "line echo \"PC_COUNT:${#PROMPT_COMMAND[@]}\" > @M@/pc_count\n"
            "wait @M@/pc_count\n"
            "line printf '%s\\n' \"${PROMPT_COMMAND[@]}\" > @M@/pc_elements\n"
            "wait @M@/pc_elements\n"
            "line exit\n"
        )
        rc, _ = self.fixture.run_pty([str(self.fixture.launch_file)], steps, name="pc-composition", timeout=15.0)
        self.assertEqual(rc, 0)
        elements = (self.fixture.m_dir / "pc_elements").read_text().splitlines()
        self.assertTrue(len(elements) >= 2, f"Expected array PROMPT_COMMAND with at least 2 entries: {elements}")
        self.assertEqual(elements[-1], "_dinas_stock_history_hook")
        self.assertIn("steeef_prompt_command", elements[0])

    def test_resource_idempotence(self):
        steps = (
            "line source ~/.bashrc\n"
            "line source ~/.bashrc\n"
            "line printf '%s\\n' \"${PROMPT_COMMAND[@]}\" > @M@/pc_after\n"
            "wait @M@/pc_after\n"
            "line exit\n"
        )
        rc, _ = self.fixture.run_pty([str(self.fixture.launch_file)], steps, name="resource-idempotence", timeout=15.0)
        self.assertEqual(rc, 0)
        elements = (self.fixture.m_dir / "pc_after").read_text().splitlines()
        hook_count = sum(1 for e in elements if "_dinas_stock_history_hook" in e)
        self.assertEqual(hook_count, 1, f"_dinas_stock_history_hook should only appear once in PROMPT_COMMAND: {elements}")
        self.assertEqual(elements[-1], "_dinas_stock_history_hook")

    def test_mock_ble_lifecycle_order(self):
        steps = (
            "line touch @M@/alive\n"
            "wait @M@/alive\n"
            "line exit\n"
        )
        rc, _ = self.fixture.run_pty([str(self.fixture.launch_file)], steps, name="ble-order", timeout=15.0)
        self.assertEqual(rc, 0)

        events = (self.fixture.events_log).read_text().splitlines()
        filtered = [
            e.split()[0]
            for e in events
            if any(e.startswith(prefix) for prefix in ("detach", "completion", "bleopt", "ble-bind", "ble-import", "attach-last"))
        ]

        self.assertIn("detach", filtered)
        self.assertIn("completion", filtered)
        self.assertIn("attach-last", filtered)

        idx_detach = filtered.index("detach")
        idx_comp = filtered.index("completion")
        idx_attach = filtered.index("attach-last")

        self.assertLess(idx_detach, idx_comp, "detach must occur before completion")
        self.assertLess(idx_comp, idx_attach, "completion must occur before attach-last")
        self.assertEqual(idx_attach, len(filtered) - 1, "attach-last must be the final lifecycle action")

    def test_up_and_cd_ordinary(self):
        sub_path = self.fixture.m_dir / "level1" / "level2" / "level3"
        sub_path.mkdir(parents=True, exist_ok=True)
        steps = (
            f"line cd {sub_path} && up 2 && pwd > @M@/up_pwd\n"
            "wait @M@/up_pwd\n"
            "line up invalid 2> @M@/up_err || echo $? > @M@/up_rc\n"
            "wait @M@/up_rc\n"
            "line type -t cd > @M@/cd_type\n"
            "wait @M@/cd_type\n"
            "line exit\n"
        )
        rc, _ = self.fixture.run_pty([str(self.fixture.launch_file)], steps, name="nav-up", timeout=15.0)
        self.assertEqual(rc, 0)

        up_pwd = (self.fixture.m_dir / "up_pwd").read_text().strip()
        expected = (self.fixture.m_dir / "level1").resolve()
        self.assertEqual(Path(up_pwd).resolve(), expected)

        up_err = (self.fixture.m_dir / "up_err").read_text().strip()
        self.assertEqual(up_err, "usage: up [count]")

        up_rc = (self.fixture.m_dir / "up_rc").read_text().strip()
        self.assertEqual(up_rc, "2")

        cd_type = (self.fixture.m_dir / "cd_type").read_text().strip()
        self.assertEqual(cd_type, "builtin", "cd must remain an ordinary shell builtin")

    def test_histfile_fallback_and_export_state(self):
        steps = (
            "line echo \"HISTFILE:$HISTFILE\" > @M@/hist\n"
            "wait @M@/hist\n"
            "line export -p | grep HISTFILE > @M@/hist_export || true\n"
            "line touch @M@/export_done\n"
            "wait @M@/export_done\n"
            "line exit\n"
        )
        rc, _ = self.fixture.run_pty([str(self.fixture.launch_file)], steps, name="histfile-state", timeout=15.0)
        self.assertEqual(rc, 0)

        hist = (self.fixture.m_dir / "hist").read_text().strip()
        desired_hist = str(self.fixture.home_dir / ".bash_history")
        self.assertEqual(hist, f"HISTFILE:{desired_hist}", "HISTFILE must fallback to desired ~/.bash_history")

        hist_export = (self.fixture.m_dir / "hist_export").read_text().strip()
        self.assertEqual(hist_export, "", "HISTFILE must be unexported (shell-local)")


class KittyStockPTYInteractiveTests(unittest.TestCase):
    """Tests Readline interactive key-handling under actual pseudo-terminal."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=str(SCRATCH_TMP), prefix="pty-inter-")
        self.fixture = ShellFixture(Path(self.tmp.name), blesh_missing=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_readline_prefix_search_pty(self):
        (self.fixture.home_dir / ".bash_history").write_text(
            f"echo alpha-command-ran > {self.fixture.m_dir}/up-result\n"
            f"echo beta-other > {self.fixture.m_dir}/other\n"
        )
        steps = (
            "line echo al\\x1b[A\n"
            "wait @M@/up-result\n"
            "line exit\n"
        )
        rc, _ = self.fixture.run_pty([str(self.fixture.launch_file)], steps, name="readline-prefix", timeout=15.0)
        self.assertEqual(rc, 0)
        recalled = (self.fixture.m_dir / "up-result").read_text().strip()
        self.assertEqual(recalled, "alpha-command-ran", "Up-arrow with prefix 'al' must recall and run alpha-command")

    def test_readline_tab_completion_pty(self):
        unique_file = self.fixture.m_dir / "unique_marker_alpha.txt"
        unique_file.write_text("content-unique\n")

        steps = (
            "send cat @M@/unique_marker_al\n"
            "sleep 0.5\n"
            "send \\t\n"
            "sleep 0.5\n"
            "line  > @M@/completed\n"
            "wait @M@/completed\n"
            "line exit\n"
        )
        rc, _ = self.fixture.run_pty([str(self.fixture.launch_file)], steps, name="readline-tab", timeout=15.0)
        self.assertEqual(rc, 0)
        completed = (self.fixture.m_dir / "completed").read_text().strip()
        self.assertEqual(completed, "content-unique", "Tab completion must expand to unique_marker_alpha.txt")


class KittyStockSelectionIsolationTests(unittest.TestCase):
    """Verifies that stock enhancements and markers never leak into other shells."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=str(SCRATCH_TMP), prefix="isolation-")
        self.fixture = ShellFixture(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_launch_unchanged_kitty_bash_argv_from_enhanced_stock(self):
        # From within enhanced stock, launch unchanged Kitty Bash login argv
        steps = (
            f"line {self.fixture.bin_dir}/mock_login -fp {getpass.getuser()} {MODERN_BASH}\n"
            "line echo \"NESTED_BLE:${BLE_VERSION-none}\" > @M@/nested_ble\n"
            "wait @M@/nested_ble\n"
            "line echo \"NESTED_OWNER:${_dinas_stock_owner-none}\" > @M@/nested_owner\n"
            "wait @M@/nested_owner\n"
            "line echo \"NESTED_ENV:${ENV-none}\" > @M@/nested_env\n"
            "wait @M@/nested_env\n"
            "line exit\n"
            "line exit\n"
        )
        rc, _ = self.fixture.run_pty([str(self.fixture.launch_file)], steps, name="iso-kitty-bash", timeout=15.0)
        self.assertEqual(rc, 0)
        self.assertEqual((self.fixture.m_dir / "nested_ble").read_text().strip(), "NESTED_BLE:none")
        self.assertEqual((self.fixture.m_dir / "nested_owner").read_text().strip(), "NESTED_OWNER:none")
        self.assertEqual((self.fixture.m_dir / "nested_env").read_text().strip(), "NESTED_ENV:none")

    def test_launch_zsh_login_from_enhanced_stock(self):
        zdotdir = self.fixture.base_dir / "zdotdir"
        zdotdir.mkdir(parents=True, exist_ok=True)
        (zdotdir / ".zprofile").write_text("# zprofile\n")
        (zdotdir / ".zshrc").write_text("# zshrc\n")

        steps = (
            f"line ZDOTDIR={zdotdir} {SYSTEM_ZSH} -l\n"
            "line echo \"ZSH_BLE:${BLE_VERSION-none}\" > @M@/zsh_ble\n"
            "wait @M@/zsh_ble\n"
            "line echo \"ZSH_ENV:${ENV-none}\" > @M@/zsh_env\n"
            "wait @M@/zsh_env\n"
            "line exit\n"
            "line exit\n"
        )
        rc, _ = self.fixture.run_pty([str(self.fixture.launch_file)], steps, name="iso-zsh", timeout=15.0)
        self.assertEqual(rc, 0)
        self.assertEqual((self.fixture.m_dir / "zsh_ble").read_text().strip(), "ZSH_BLE:none")
        self.assertEqual((self.fixture.m_dir / "zsh_env").read_text().strip(), "ZSH_ENV:none")

    def test_launch_terminal_bash_from_enhanced_stock(self):
        steps = (
            f"line {self.fixture.bin_dir}/mock_login -fp fixture {MODERN_BASH}\n"
            "line echo \"TERM_BLE:${BLE_VERSION-none}\" > @M@/term_ble\n"
            "wait @M@/term_ble\n"
            "line exit\n"
            "line exit\n"
        )
        rc, _ = self.fixture.run_pty([str(self.fixture.launch_file)], steps, name="iso-term-bash", timeout=15.0)
        self.assertEqual(rc, 0)
        self.assertEqual((self.fixture.m_dir / "term_ble").read_text().strip(), "TERM_BLE:none")

    def test_launch_bash_c_from_enhanced_stock(self):
        steps = (
            "line bash -c 'echo \"C_ENV:${ENV-none}:${DINAS_KITTY_STOCK_PID-none}\"' > @M@/c_res\n"
            "wait @M@/c_res\n"
            "line exit\n"
        )
        rc, _ = self.fixture.run_pty([str(self.fixture.launch_file)], steps, name="iso-bash-c", timeout=15.0)
        self.assertEqual(rc, 0)
        self.assertEqual((self.fixture.m_dir / "c_res").read_text().strip(), "C_ENV:none:none")

    def test_nested_bash_banner_matches_host_kitty_bash_baseline(self):
        """B1: preserve host rc semantics; no VM once-per-window parity claim."""
        counts = []
        for label, command in [
            ("baseline", [str(self.fixture.bin_dir / "mock_login"), "-fp", getpass.getuser(), MODERN_BASH]),
            ("stock", [str(self.fixture.launch_file)]),
        ]:
            self.fixture.events_log.write_text("")
            steps = (
                f"line {MODERN_BASH}\n"
                "line source ~/.bashrc\n"
                "line printf '%s:%s:%s:%s' \"${BLE_VERSION-none}\" \"${_dinas_stock_owner-none}\" \"$(type -t ff)\" \"$(type -t cd)\" > @M@/nested-" + label + "\n"
                "wait @M@/nested-" + label + "\n"
                "line env > @M@/exports-" + label + "\n"
                "wait @M@/exports-" + label + "\n"
                "line exit\nline exit\n"
            )
            rc, transcript = self.fixture.run_pty(command, steps, name="banner-" + label,
                env_extra={"KITTY_PID": "1234", "KITTY_WINDOW_ID": "1"}, timeout=15.0)
            self.assertEqual(rc, 0, transcript[-1200:])
            self.assertEqual((self.fixture.m_dir / ("nested-" + label)).read_text(), "none:none:alias:builtin")
            exports = (self.fixture.m_dir / ("exports-" + label)).read_text()
            self.assertNotRegex(exports, r"(?m)^(ENV|BASH_ENV|ZDOTDIR|DINAS_BANNER_WINDOW|DINAS_KITTY_STOCK_\w+)=")
            counts.append(self.fixture.events_log.read_text().count("fastfetch:"))
        self.assertGreaterEqual(counts[0], 2)
        self.assertEqual(counts[0], counts[1])
        (SCRATCH_LOGS / "host-banner-parity.json").write_text(json.dumps({"managerDecision": "B1", "hostBaseline": counts[0], "stock": counts[1], "VMParityClaimed": False}) + "\n")

    def test_benign_baseline_comparison(self):
        # Terminal Bash baseline environment
        env_baseline_out = subprocess.check_output(
            [MODERN_BASH, "-c", "shopt -s expand_aliases; source ~/.bash_profile; alias; echo '---'; type -t ff; echo '---'; echo \"$PROMPT_COMMAND\""],
            env=self.fixture.get_clean_env(),
            text=True,
            cwd=str(self.fixture.m_dir),
            stderr=subprocess.DEVNULL,
        )
        aliases_part, ff_part, pc_part = env_baseline_out.split("---")

        self.assertIn("alias ff='fastfetch'", aliases_part)
        self.assertIn("alias nvm-node=", aliases_part)
        self.assertIn("alias pyenv-python=", aliases_part)
        self.assertIn("alias rbenv-ruby=", aliases_part)
        self.assertEqual(ff_part.strip(), "alias")
        self.assertIn("steeef_prompt_command", pc_part)
        self.assertNotIn("_dinas_stock_history_hook", pc_part)


class KittyStockAdditionalContracts(unittest.TestCase):
    setUp = KittyStockLaunchTests.setUp
    tearDown = KittyStockLaunchTests.tearDown

    def session(self, body, name, **kwargs):
        rc, transcript = self.fixture.run_pty(
            [str(self.fixture.launch_file)], body + "\nline exit\n", name=name, **kwargs)
        self.assertEqual(rc, 0, transcript[-1200:])
        return transcript

    def test_plain_and_ssh_exit_status(self):
        for label, extra in [("plain-exit", {"DINAS_PLAIN_BASH": "1"}), ("ssh-exit", {"SSH_CONNECTION": "fixture"})]:
            rc, _ = self.fixture.run_pty([str(self.fixture.launch_file)], "line exit 37\n", name=label, env_extra=extra)
            self.assertEqual(rc, 37)

    def test_unset_term_exclusion(self):
        self.session("line printf '%s:%s' \"${BLE_VERSION-none}\" \"${_dinas_stock_owner-none}\" > @M@/no-term\nwait @M@/no-term",
                     "no-term", env_extra={"TERM": None})
        self.assertEqual((self.fixture.m_dir / "no-term").read_text(), "none:none")

    def test_stdout_only_redirection_excludes_enhancements(self):
        launch = self.fixture.launch_file
        self.session(f"line {launch} > @M@/redirected-output\n"
                     "line printf '%s:%s' \"${BLE_VERSION-none}\" \"${_dinas_stock_owner-none}\" > @M@/redirected-state\nwait @M@/redirected-state\nline exit",
                     "stdout-only")
        self.assertEqual((self.fixture.m_dir / "redirected-state").read_text(), "none:none")

    def test_leaked_marker_and_command_matrix(self):
        entry = str(self.fixture.entry_file)
        for name, command, extra in [
            ("wrong-pid", [MODERN_BASH, "--posix", "-i"], {"ENV": entry, "DINAS_KITTY_STOCK_PID": "1"}),
            ("term-marker", [MODERN_BASH, "--noprofile", "--norc", "-i"], {"KITTY_PID": "99", "DINAS_KITTY_STOCK_PID": "1"}),
            ("foreign-sh", ["/bin/sh", "-i"], {"ENV": entry, "DINAS_KITTY_STOCK_PID": "1"}),
        ]:
            target = self.fixture.m_dir / name
            rc, _ = self.fixture.run_pty(command,
                f"line printf '%s:%s' \"${{BLE_VERSION-none}}\" \"$(type -t up)\" > {target}\nwait {target}\nline exit\n",
                name=name, env_extra=extra)
            self.assertEqual(rc, 0)
            self.assertEqual(target.read_text(), "none:")
        for flags in [["-c"], ["-ic"], ["-lic"]]:
            r = subprocess.run([str(self.fixture.launch_file), *flags,
                'printf "command:%s:%s" "${BLE_VERSION-none}" "${_dinas_stock_owner-none}"'],
                env=self.fixture.get_clean_env(), capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 0)
            self.assertIn("command:none:none", r.stdout)

    def test_every_optional_component_missing_and_failing_banner(self):
        self.fixture.blesh_path.unlink()
        self.fixture.completion_path.unlink()
        for path in [self.fixture.fzf_dir / "completion.bash", self.fixture.fzf_dir / "key-bindings.bash",
                     self.fixture.zoxide_bin, self.fixture.direnv_bin]:
            path.unlink()
        (self.fixture.bin_dir / "fastfetch").write_text("#!/bin/sh\necho simulated-banner-failure\nexit 7\n")
        transcript = self.session(
            "line printf '%s:%s:%s' \"${BLE_VERSION-none}\" \"$(type -t cd)\" \"$HISTFILESIZE\" > @M@/missing\nwait @M@/missing\n"
            "line bind -p > @M@/bindings\nwait @M@/bindings", "missing-all")
        self.assertEqual((self.fixture.m_dir / "missing").read_text(), "none:builtin:200000")
        self.assertIn("menu-complete", (self.fixture.m_dir / "bindings").read_text())
        self.assertIn(b"simulated-banner-failure", transcript)

    def test_prior_array_hooks_and_stock_resource(self):
        profile = self.fixture.home_dir / ".bash_profile"
        with profile.open("a") as stream:
            stream.write("\nPROMPT_COMMAND=('steeef_prompt_command' 'prior_array_hook')\nprior_array_hook() { return 19; }\n")
        self.session(
            f"line source {self.fixture.interactive_file}; source {self.fixture.interactive_file}\n"
            "line printf '%s\\\\n' \"${PROMPT_COMMAND[@]}\" > @M@/array\nwait @M@/array\n"
            "line _dinas_stock_status 7; _dinas_stock_history_hook; printf '%s' $? > @M@/hook-status\nwait @M@/hook-status",
            "prior-array")
        elements = (self.fixture.m_dir / "array").read_text().splitlines()
        self.assertEqual(elements, ["steeef_prompt_command", "prior_array_hook", "_dinas_stock_history_hook"])
        self.assertEqual((self.fixture.m_dir / "hook-status").read_text(), "7")

    def test_generated_zoxide_hook_survives_host_resource(self):
        self.fixture.zoxide_bin.write_text(
            f"#!{MODERN_BASH}\ncat <<'EOF'\n"
            "__zoxide_hook() { :; }\nz() { :; }\nzi() { :; }\n"
            "PROMPT_COMMAND=\"${PROMPT_COMMAND:+$PROMPT_COMMAND;}__zoxide_hook\"\nEOF\n")
        self.session(
            "line source ~/.bashrc; source ~/.bashrc\n"
            "line printf '%s\\\\n' \"${PROMPT_COMMAND[@]}\" > @M@/tools-pc\nwait @M@/tools-pc\n"
            "line declare -F __zoxide_hook z zi > @M@/tools-functions\nwait @M@/tools-functions", "tool-hooks")
        self.assertEqual((self.fixture.m_dir / "tools-pc").read_text().count("_dinas_stock_history_hook"), 1)
        self.assertNotIn("__zoxide_hook", (self.fixture.m_dir / "tools-pc").read_text())
        self.assertIn("__zoxide_hook", (self.fixture.m_dir / "tools-functions").read_text())

    def test_default_styled_prompt_and_alias_parity(self):
        baseline = self.fixture.m_dir / "baseline"
        script = f"source '{self.fixture.etc_dir}/profile'; source ~/.bash_profile; steeef_prompt_command; printf '%s' \"$PS1\" > '{baseline}'; alias -p > '{baseline}.aliases'"
        r = subprocess.run([MODERN_BASH, "--noprofile", "--norc", "-ic", script],
            env=self.fixture.get_clean_env(), cwd=self.fixture.m_dir,
            capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.session("line printf '%s' \"$PS1\" > @M@/stock-prompt\nwait @M@/stock-prompt\n"
                     "line alias -p > @M@/stock-alias\nwait @M@/stock-alias", "golden-prompt")
        # Stock styling expresses the same default gold in RGB; the ordinary
        # host theme keeps its indexed color. Compare in the same working dir.
        indexed_gold = rb'\[\033[38;5;220m\]'
        rgb_gold = rb'\[\033[38;2;255;215;0m\]'
        self.assertIn(indexed_gold, baseline.read_bytes())
        self.assertEqual(baseline.read_bytes().replace(indexed_gold, rgb_gold),
                         (self.fixture.m_dir / "stock-prompt").read_bytes())
        self.assertEqual(Path(str(baseline) + ".aliases").read_bytes(), (self.fixture.m_dir / "stock-alias").read_bytes())

    def test_local_style_and_installed_fallback_apply_before_ble_attach(self):
        self.fixture.style_default.write_text(self.fixture.style_default.read_text().replace('#ffd700', '#112233'))
        local = self.fixture.home_dir / '.local/nix-darwin/config/kitty-bash.toml'
        local.parent.mkdir(parents=True)
        local.write_text('schema=1\n[palette]\npath="#aabbcc"\n'
                         '[terminal]\n[completion]\nalign_min=8\nalign_max=45\n'
                         '[ble.faces]\nauto_complete="fg=#aabbcc,bold"\n')
        with self.fixture.blesh_path.open('a') as stream:
            stream.write(f'\nble-face() {{ printf "face:%s\\n" "$*" >> "{self.fixture.events_log}"; }}\n')
        include = self.fixture.home_dir / '.config/kitty/stock-bash-colors.conf'
        include.parent.mkdir(parents=True)
        include.write_text('existing manually rendered terminal colors\n')
        self.session("line printf '%s|%s' \"$SHELL\" \"$STEEEF_LIGHTAMBER\" > @M@/style-B\nwait @M@/style-B",
                     'local-style', env_extra={'SHELL': SYSTEM_ZSH})
        self.assertEqual((self.fixture.m_dir / 'style-B').read_text(),
                         MODERN_BASH + r'|\[\033[38;2;170;187;204m\]')
        events = self.fixture.events_log.read_text()
        self.assertLess(events.index('face:-s auto_complete fg=#aabbcc,bold'), events.index('attach-last'))
        self.assertIn('bleopt menu_align_min=8\n', events)
        self.assertIn('bleopt menu_align_max=45\n', events)
        local.unlink()
        self.session("line printf '%s' \"$STEEEF_LIGHTAMBER\" > @M@/style-A\nwait @M@/style-A", 'installed-style')
        self.assertEqual((self.fixture.m_dir / 'style-A').read_text(), r'\[\033[38;2;17;34;51m\]')
        self.assertEqual(include.read_text(), 'existing manually rendered terminal colors\n')

    def test_environment_handoff_is_not_inherited(self):
        self.session("line env > @M@/exports\nwait @M@/exports\n"
                     "line type -t z zi cd > @M@/navigation\nwait @M@/navigation", "export-state")
        exports = (self.fixture.m_dir / "exports").read_text()
        self.assertNotRegex(exports, r"(?m)^(ENV|BASH_ENV|DINAS_KITTY_STOCK_\w+|BASH_FUNC_\w+)=" )
        self.assertEqual((self.fixture.m_dir / "navigation").read_text(), "function\nfunction\nbuiltin\n")

    def test_missing_login_exec_falls_back_without_leaked_state(self):
        self.fixture.launch_file.write_text(self.fixture.launch_file.read_text().replace(
            str(self.fixture.bin_dir / "mock_login"), str(self.fixture.bin_dir / "absent-login")))
        self.session("line printf '%s:%s' \"${BLE_VERSION-none}\" \"${DINAS_KITTY_STOCK_PID-none}\" > @M@/fallback\nwait @M@/fallback", "login-exec-fallback")
        self.assertEqual((self.fixture.m_dir / "fallback").read_text(), "0.4.0-mock:none")

    def test_history_append_import_preserves_existing_lines(self):
        self.fixture.blesh_path.unlink()
        history = self.fixture.home_dir / ".bash_history"
        history.write_text("echo old-sentinel\n")
        self.session("line history -s 'echo local-sentinel'; history -a\n"
                     "line printf 'echo external-sentinel\\\\n' >> ~/.bash_history; history -n\n"
                     "line history > @M@/history-state\nwait @M@/history-state", "history-fallback")
        self.assertIn("old-sentinel", history.read_text())
        self.assertIn("local-sentinel", history.read_text())
        self.assertIn("external-sentinel", (self.fixture.m_dir / "history-state").read_text())


class KittyStockDependencyBackedSkippedTests(unittest.TestCase):
    def test_real_optional_packages(self):
        self.skipTest("Real ble.sh/fzf/zoxide absent offline; selected-package scenarios NOT RUN")

    def test_native_gui_window_qualification(self):
        self.skipTest("Native Kitty GUI/TCC qualification forbidden in this source-only phase")


def run_and_record():
    SCRATCH_LOGS.mkdir(parents=True, exist_ok=True)
    log_file = SCRATCH_LOGS / "kitty-stock-shell-tests.log"
    summary_file = SCRATCH_LOGS / "kitty-stock-shell-summary.json"

    with log_file.open("w") as stream:
        runner = unittest.TextTestRunner(stream=stream, verbosity=2)
        suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        result = runner.run(suite)

    summary = {
        "testsRun": result.testsRun,
        "successful": result.wasSuccessful(),
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "skippedDetails": [{"test": str(test), "reason": reason} for test, reason in result.skipped],
        "logPath": "logs/kitty-stock-shell-tests.log",
    }
    summary_file.write_text(json.dumps(summary, indent=2) + "\n")

    sys.stdout.write(f"Ran {result.testsRun} tests ({len(result.skipped)} skipped)\n")
    sys.stdout.write(f"OK: {result.wasSuccessful()}\n")
    sys.stdout.write(f"Log: {log_file}\n")
    sys.stdout.write(f"Summary: {summary_file}\n")

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    if "--record" in sys.argv:
        sys.argv.remove("--record")
        sys.exit(run_and_record())
    else:
        unittest.main(verbosity=2)
