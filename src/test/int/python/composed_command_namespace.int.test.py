# nix-darwin-test-source-target: modules/dinas/command-namespaces.nix
# nix-darwin-test-source-target: bin/monitor-temp
# nix-darwin-test-source-target: libexec/monitor-temp/power-watts.awk
# nix-darwin-test-source-target: libexec/monitor-temp/battery-temperature-c.awk
"""Run a core command through the composed namespace with synthetic host tools.

The Nix check supplies its actual module-evaluated buildEnv output. Without it,
exercise a disposable per-file symlink layout using the module's pathsToLink.
The latter qualifies resource lookup, not a Nix package build.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def test_composed_namespace_preserves_libexec_resources(tmp_path):
    root = Path(__file__).resolve().parents[4]
    package = os.environ.get("DINAS_COMPOSED_COMMAND_PACKAGE")
    if package:
        package = Path(package)
    else:
        package = tmp_path / "composed-package"
        (package / "bin").mkdir(parents=True)
        (package / "bin/monitor-temp").symlink_to(root / "bin/monitor-temp")
        (package / "bin/storage-sentinel").write_text("#!/bin/sh\nexit 0\n")
        module = (root / "modules/dinas/command-namespaces.nix").read_text()
        links = re.search(r'pathsToLink\s*=\s*\[(.*?)\];', module, re.S).group(1)
        if '"/libexec"' in links:
            (package / "libexec").mkdir()
            (package / "libexec/monitor-temp").symlink_to(root / "libexec/monitor-temp", target_is_directory=True)
            (package / "libexec/storage").mkdir()

    tmp_path = tmp_path.resolve()
    home = tmp_path / "home"
    home.mkdir()
    nbin = home / "nbin"
    nbin.mkdir()
    result = subprocess.run([sys.executable, str(root / "bin/dinas-reconcile"),
        "--nbin-root", str(nbin), "--nbin-dev-root", str(home / "nbin-dev"),
        "--ai-index-root", str(home / "ai"),
        "--namespaces-json", json.dumps({"dinas": str(package / "bin")})],
        env={**os.environ, "HOME": str(home)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    fake = tmp_path / "fake-tools"
    fake.mkdir()
    scripts = {
        "uname": 'echo Darwin',
        "sudo": 'case "$1" in true) exit 0;; powermetrics) printf "Current pressure level: Nominal\\nCombined Power: 4200 mW\\n";; *) exit 99;; esac',
        "ioreg": 'printf \'"Temperature" = 3180\\n\'',
    }
    for name, body in scripts.items():
        p = fake / name
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "PATH": f"{fake}:{os.environ['PATH']}"}
    env.pop("MONITOR_TEMP_LIBEXEC", None)
    result = subprocess.run(["bash", str(nbin / "dinas/monitor-temp"),
        "--count", "1", "--no-bell", "--no-color"], env=env,
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "cannot open" not in result.stderr
    assert "B:31.8C" in result.stdout
    assert "W:4.2W" in result.stdout
    assert (package / "libexec/storage").is_dir()
