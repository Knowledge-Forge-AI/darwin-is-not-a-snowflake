# nix-darwin-test-source-target: libexec/dinas_foundation/reconciler.py
# nix-darwin-test-source-target: libexec/dinas_foundation/reconcile_plan.py
# nix-darwin-test-source-target: bin/dinas-reconcile
from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest


@pytest.fixture
def reconciler_module(repo_root: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    return importlib.import_module("dinas_foundation.reconciler")


@pytest.fixture
def plan_module(repo_root: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    return importlib.import_module("dinas_foundation.reconcile_plan")


def test_reconcile_nbin_ordinary_and_missing_root_success(reconciler_module, plan_module, tmp_path: Path):
    pkg1 = tmp_path / "pkg1"
    (pkg1 / "bin").mkdir(parents=True)
    cmd1 = pkg1 / "bin/my-cmd"
    cmd1.write_text("#!/bin/sh\n", encoding="utf-8")
    cmd1.chmod(0o755)

    # 1. Ordinary existing root directory
    ordinary_root = tmp_path / "ordinary_nbin"
    ordinary_root.mkdir()
    plan_ordinary = plan_module.create_reconcile_plan(ordinary_root, {"dinas": pkg1 / "bin"})
    assert plan_ordinary.eligible is True
    assert plan_ordinary.nbin_status == "directory"

    report = reconciler_module.reconcile_nbin(ordinary_root, {"dinas": pkg1 / "bin"})
    assert not report.has_collisions
    assert ordinary_root.is_dir()
    assert not ordinary_root.is_symlink()
    assert (ordinary_root / "dinas").is_symlink()
    assert (ordinary_root / "dinas").resolve() == (pkg1 / "bin").resolve()
    manifest = json.loads((ordinary_root / ".manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "dinas-nbin-manifest-v1"
    assert "dinas" in manifest["namespaces"]

    # 2. Missing root directory (auto-created)
    missing_root = tmp_path / "missing_nbin"
    assert not missing_root.exists()
    plan_missing = plan_module.create_reconcile_plan(missing_root, {"dinas": pkg1 / "bin"})
    assert plan_missing.eligible is True
    assert plan_missing.nbin_status == "missing"
    assert any(a.action == "create_dir" for a in plan_missing.nbin_actions)

    report_missing = reconciler_module.reconcile_nbin(missing_root, {"dinas": pkg1 / "bin"})
    assert not report_missing.has_collisions
    assert missing_root.is_dir()
    assert (missing_root / "dinas").is_symlink()
    assert (missing_root / "dinas").resolve() == (pkg1 / "bin").resolve()
    manifest_missing = json.loads((missing_root / ".manifest.json").read_text(encoding="utf-8"))
    assert manifest_missing["namespaces"] == {"dinas": str((pkg1 / "bin").resolve())}


@pytest.mark.parametrize(
    "symlink_kind",
    ["former_flakes", "arbitrary", "dangling", "cyclic"],
)
def test_reconcile_nbin_rejects_symlink_roots_without_mutation(
    reconciler_module,
    plan_module,
    tmp_path: Path,
    symlink_kind: str,
):
    nbin_root = tmp_path / f"nbin_{symlink_kind}"
    if symlink_kind == "former_flakes":
        target = tmp_path / "home/.flakes/nix-darwin/bin"
        target.mkdir(parents=True)
        nbin_root.symlink_to(target)
    elif symlink_kind == "arbitrary":
        target = tmp_path / "arbitrary_dir"
        target.mkdir()
        nbin_root.symlink_to(target)
    elif symlink_kind == "dangling":
        target = tmp_path / "nonexistent_target"
        nbin_root.symlink_to(target)
    elif symlink_kind == "cyclic":
        target = nbin_root
        nbin_root.symlink_to(nbin_root)

    expected_target_str = os.readlink(nbin_root)

    pkg = tmp_path / "pkg/bin"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "tool").write_text("#!/bin/sh\n", encoding="utf-8")
    (pkg / "tool").chmod(0o755)

    # 1. Planner checks: blocked unexpected_symlink with no nbin actions
    plan = plan_module.create_reconcile_plan(nbin_root, {"dinas": pkg})
    assert plan.eligible is False
    assert plan.nbin_status == "unexpected_symlink"
    assert plan.nbin_actions == []
    expected_blocker = f"refusing to replace unexpected symlink at nbin_root: {nbin_root} -> {expected_target_str}"
    assert expected_blocker in plan.blockers

    # 2. Reconcile API: raises ReconcileError before writes without mutation
    with pytest.raises(reconciler_module.ReconcileError) as exc_info:
        reconciler_module.reconcile_nbin(nbin_root, {"dinas": pkg})
    assert expected_blocker in str(exc_info.value)

    # Verify zero filesystem mutation
    assert nbin_root.is_symlink()
    assert os.readlink(nbin_root) == expected_target_str
    assert not (nbin_root / "dinas").exists()
    assert not (nbin_root / ".manifest.json").exists()


def test_reconcile_nbin_invalid_name_precedence_on_symlink_root(
    reconciler_module,
    tmp_path: Path,
):
    nbin_root = tmp_path / "nbin_symlink"
    target = tmp_path / "target"
    target.mkdir()
    nbin_root.symlink_to(target)

    bad_namespaces = {"bad/name": tmp_path / "package"}
    with pytest.raises(reconciler_module.DevError, match="forbidden characters"):
        reconciler_module.reconcile_nbin(nbin_root, bad_namespaces)
    assert nbin_root.is_symlink()
    assert os.readlink(nbin_root) == str(target)


def test_reconcile_nbin_invalid_name_remains_dev_error_with_other_blockers(
    reconciler_module,
    plan_module,
    monkeypatch,
    tmp_path: Path,
):
    nbin_root = tmp_path / "nbin"
    nbin_root.mkdir()
    package = tmp_path / "package"
    package.mkdir()
    current_uid = os.getuid()
    monkeypatch.setattr(plan_module.os, "getuid", lambda: current_uid + 1)

    with pytest.raises(reconciler_module.DevError, match="forbidden characters"):
        reconciler_module.reconcile_nbin(nbin_root, {"bad/name": package})


def test_reconcile_nbin_collision_detection(reconciler_module, tmp_path: Path):
    nbin_root = tmp_path / "nbin"
    pkg1 = tmp_path / "pkg1"
    (pkg1 / "bin").mkdir(parents=True)
    (pkg1 / "bin/dup-cmd").write_text("#!/bin/sh\n", encoding="utf-8")
    (pkg1 / "bin/dup-cmd").chmod(0o755)

    pkg2 = tmp_path / "pkg2"
    (pkg2 / "bin").mkdir(parents=True)
    (pkg2 / "bin/dup-cmd").write_text("#!/bin/sh\n", encoding="utf-8")
    (pkg2 / "bin/dup-cmd").chmod(0o755)

    report = reconciler_module.reconcile_nbin(
        nbin_root,
        {"ns1": pkg1 / "bin", "ns2": pkg2 / "bin"},
    )
    assert report.has_collisions
    assert "dup-cmd" in report.collisions
    assert set(report.collisions["dup-cmd"]) == {"ns1", "ns2"}


def test_reconcile_nbin_refuses_unmanaged_collision_and_cleans_stale(reconciler_module, tmp_path: Path):
    nbin_root = tmp_path / "nbin"
    nbin_root.mkdir()

    # Unmanaged path exists
    (nbin_root / "foreign").write_text("not a symlink", encoding="utf-8")
    (tmp_path / "pkg").mkdir()

    with pytest.raises(reconciler_module.ReconcileError, match="unmanaged"):
        reconciler_module.reconcile_nbin(nbin_root, {"foreign": tmp_path / "pkg"})

    # Valid run
    (nbin_root / "foreign").unlink()
    pkgA = tmp_path / "pkgA"
    pkgA.mkdir()
    reconciler_module.reconcile_nbin(nbin_root, {"nsA": pkgA})
    assert (nbin_root / "nsA").is_symlink()

    # Next run without nsA cleans it up because it was in prior manifest
    pkgB = tmp_path / "pkgB"
    pkgB.mkdir()
    reconciler_module.reconcile_nbin(nbin_root, {"nsB": pkgB})
    assert not (nbin_root / "nsA").exists()
    assert (nbin_root / "nsB").is_symlink()


def test_reconcile_ai_index_manages_convenience_aliases(reconciler_module, tmp_path: Path):
    home = tmp_path / "user"
    home.mkdir()
    ai_root = home / "ai"

    # Only create targets for codex and claude
    (home / ".codex").mkdir()
    (home / ".claude").mkdir()
    # agents and gemini do NOT exist yet

    # Unrelated entry in ai_root
    ai_root.mkdir()
    (ai_root / "agent-central").mkdir()
    (ai_root / "agent-central/marker").write_text("untouched", encoding="utf-8")

    reconciler_module.reconcile_ai_index(ai_root, home)

    # Codex and Claude are linked
    assert (ai_root / "codex").is_symlink()
    assert os.readlink(ai_root / "codex") == str(home / ".codex")
    assert (ai_root / "claude").is_symlink()
    assert os.readlink(ai_root / "claude") == str(home / ".claude")

    # Missing targets are NOT linked and provider root is NOT created
    assert not (ai_root / "gemini").exists()
    assert not (home / ".gemini").exists()
    assert not (ai_root / "agents").exists()
    assert not (home / ".agents").exists()

    # Unrelated directory preserved untouched
    assert (ai_root / "agent-central/marker").read_text(encoding="utf-8") == "untouched"

    # Idempotent re-run
    reconciler_module.reconcile_ai_index(ai_root, home)
    assert (ai_root / "codex").is_symlink()


def test_ai_index_aliases_are_not_runtime_authority(repo_root: Path):
    forbidden = ("codex", "claude", "gemini", "agents", "claude-desktop")
    violations: list[str] = []
    for root_name in ("bin", "libexec", "modules", "packages", "shell"):
        root = repo_root / root_name
        for path in root.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for alias in forbidden:
                if f"$HOME/ai/{alias}" in text or f"~/ai/{alias}" in text:
                    violations.append(str(path.relative_to(repo_root)))
    assert violations == []


# === Two-phase Reconcile Plan & Check Mode Tests ===

@pytest.mark.parametrize(
    "symlink_kind",
    ["former_flakes", "arbitrary", "dangling", "cyclic"],
)
def test_reconcile_plan_rejects_symlink_roots(
    plan_module,
    tmp_path: Path,
    symlink_kind: str,
):
    nbin_root = tmp_path / f"nbin_{symlink_kind}"
    if symlink_kind == "former_flakes":
        target = tmp_path / "home/.flakes/nix-darwin/bin"
        target.mkdir(parents=True)
        nbin_root.symlink_to(target)
    elif symlink_kind == "arbitrary":
        target = tmp_path / "arbitrary_bin"
        target.mkdir()
        nbin_root.symlink_to(target)
    elif symlink_kind == "dangling":
        target = tmp_path / "dangling_bin"
        nbin_root.symlink_to(target)
    elif symlink_kind == "cyclic":
        nbin_root.symlink_to(nbin_root)

    expected_target_str = os.readlink(nbin_root)

    pkg = tmp_path / "pkg/bin"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "tool").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (pkg / "tool").chmod(0o755)

    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / ".codex").mkdir(exist_ok=True)

    plan = plan_module.create_reconcile_plan(
        nbin_root=nbin_root,
        namespaces={"dinas": pkg},
        nbin_dev_root=tmp_path / "nbin_dev",
        ai_root=home / "ai",
        home=home,
    )
    assert plan.eligible is False
    assert plan.nbin_status == "unexpected_symlink"
    assert plan.nbin_actions == []
    expected_blocker = f"refusing to replace unexpected symlink at nbin_root: {nbin_root} -> {expected_target_str}"
    assert expected_blocker in plan.blockers
    d = plan.to_dict()
    assert d["schema"] == "dinas-reconcile-check-v1"
    assert d["eligible"] is False
    assert d["nbin"]["status"] == "unexpected_symlink"
    assert d["nbin"]["actions"] == []


def test_reconcile_plan_unmanaged_collision_blocker(plan_module, tmp_path: Path):
    nbin_root = tmp_path / "nbin"
    nbin_root.mkdir()
    (nbin_root / "unmanaged-tool").write_text("collision", encoding="utf-8")

    pkg = tmp_path / "pkg"
    pkg.mkdir()

    plan = plan_module.create_reconcile_plan(
        nbin_root=nbin_root,
        namespaces={"unmanaged-tool": pkg},
    )
    assert plan.eligible is False
    assert any("unmanaged non-symlink" in b for b in plan.blockers)


def test_reconcile_plan_unsafe_mode_blocker(plan_module, tmp_path: Path):
    nbin_root = tmp_path / "nbin"
    nbin_root.mkdir()
    nbin_root.chmod(0o777)

    try:
        plan = plan_module.create_reconcile_plan(
            nbin_root=nbin_root,
            namespaces={},
        )
        assert plan.eligible is False
        assert any("unsafe mode" in b for b in plan.blockers)
    finally:
        nbin_root.chmod(0o755)


def test_reconcile_plan_zero_filesystem_mutation_guarantee(plan_module, tmp_path: Path):
    """Prove that create_reconcile_plan causes zero filesystem changes."""
    nbin_root = tmp_path / "nbin"
    nbin_root.mkdir()

    symlink_root = tmp_path / "symlink_nbin"
    symlink_target = tmp_path / "legacy_bin"
    symlink_target.mkdir()
    (symlink_target / "existing_cmd").write_text("#!/bin/sh\n", encoding="utf-8")
    (symlink_target / "existing_cmd").chmod(0o755)
    symlink_root.symlink_to(symlink_target)

    pkg = tmp_path / "pkg/bin"
    pkg.mkdir(parents=True)
    (pkg / "dinas_cmd").write_text("#!/bin/sh\n", encoding="utf-8")
    (pkg / "dinas_cmd").chmod(0o755)

    home = tmp_path / "home"
    home.mkdir()
    (home / ".codex").mkdir()
    (home / ".claude").mkdir()
    ai_root = home / "ai"
    ai_root.mkdir()
    (ai_root / "custom_doc").write_text("hello", encoding="utf-8")

    def snapshot(root: Path) -> dict:
        entries = {}
        for p in sorted(root.rglob("*")):
            rel = str(p.relative_to(root))
            st = p.lstat()
            target = os.readlink(p) if p.is_symlink() else None
            sha = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and not p.is_symlink() else None
            entries[rel] = {
                "is_dir": p.is_dir(),
                "is_symlink": p.is_symlink(),
                "mode": oct(st.st_mode),
                "target": target,
                "sha256": sha,
            }
        return entries

    before = snapshot(tmp_path)

    plan = plan_module.create_reconcile_plan(
        nbin_root=nbin_root,
        namespaces={"dinas": pkg},
        nbin_dev_root=tmp_path / "nbin_dev",
        ai_root=ai_root,
        home=home,
    )
    assert plan.eligible is True

    plan_symlink = plan_module.create_reconcile_plan(
        nbin_root=symlink_root,
        namespaces={"dinas": pkg},
        nbin_dev_root=tmp_path / "nbin_dev",
        ai_root=ai_root,
        home=home,
    )
    assert plan_symlink.eligible is False

    after = snapshot(tmp_path)
    assert before == after, "Preflight inspection mutated the filesystem!"


def test_dinas_reconcile_cli_check_mode(repo_root: Path, tmp_path: Path):
    """Test bin/dinas-reconcile with --check."""
    cli = repo_root / "bin/dinas-reconcile"
    home = tmp_path / "home"
    home.mkdir()
    local_dir = home / ".local"
    local_dir.mkdir()

    nbin_root = local_dir / "nbin"
    nbin_root.mkdir()

    pkg = tmp_path / "pkg/bin"
    pkg.mkdir(parents=True)
    (pkg / "tool").write_text("#!/bin/sh\n", encoding="utf-8")
    (pkg / "tool").chmod(0o755)

    # 1. Eligible check with ordinary root -> 0
    res = subprocess.run(
        [
            sys.executable,
            str(cli),
            "--check",
            "--nbin-root", str(nbin_root),
            "--nbin-dev-root", str(local_dir / "nbin_dev"),
            "--ai-index-root", str(home / "ai"),
            "--namespaces-json", json.dumps({"dinas": str(pkg)}),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
    )
    assert res.returncode == 0, f"Expected 0, got {res.returncode}: {res.stderr}"
    data = json.loads(res.stdout)
    assert data["eligible"] is True
    assert data["schema"] == "dinas-reconcile-check-v1"

    # 2. Blocked check (unexpected symlink, e.g. former flakes target) -> 1
    symlink_root = local_dir / "symlink_nbin"
    former_target = home / ".flakes/nix-darwin/bin"
    former_target.mkdir(parents=True)
    symlink_root.symlink_to(former_target)

    res_blocked = subprocess.run(
        [
            sys.executable,
            str(cli),
            "--check",
            "--nbin-root", str(symlink_root),
            "--nbin-dev-root", str(local_dir / "nbin_dev"),
            "--ai-index-root", str(home / "ai"),
            "--namespaces-json", json.dumps({"dinas": str(pkg)}),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
    )
    assert res_blocked.returncode == 2
    assert "paths.nbin_root has symlink ambiguity" in res_blocked.stderr
    assert res_blocked.stdout == ""
    assert symlink_root.readlink() == former_target

    # 3. Removed option rejected -> 2
    res_removed = subprocess.run(
        [
            sys.executable,
            str(cli),
            "--legacy-nbin-target", str(home / "legacy_bin"),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
    )
    assert res_removed.returncode == 2
    assert "unrecognized arguments: --legacy-nbin-target" in res_removed.stderr

    # 4. Invalid arguments -> 2
    res_invalid = subprocess.run(
        [
            sys.executable,
            str(cli),
            "--check",
            "--nbin-root", str(nbin_root),
            # Missing other direct roots
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
    )
    assert res_invalid.returncode == 2


def test_claude_resolution_theorem_with_fixture_home(repo_root: Path, tmp_path: Path):
    """A non-default namespace root resolves managed Claude ahead of a native decoy."""
    fixture_home = tmp_path / "fixture_home"
    fixture_home.mkdir()

    # Create decoy ~/.local/bin/claude
    decoy_bin = fixture_home / ".local/bin"
    decoy_bin.mkdir(parents=True)
    decoy_claude = decoy_bin / "claude"
    decoy_claude.write_text("#!/bin/sh\necho 'decoy 2.1.140'\nexit 0\n", encoding="utf-8")
    decoy_claude.chmod(0o755)

    # Create candidate managed sw/bin/claude
    fake_sw = tmp_path / "fake_system/sw/bin"
    fake_sw.mkdir(parents=True)
    managed_claude = fake_sw / "claude"
    managed_claude.write_text("#!/bin/sh\necho '2.1.259 (Claude Code)'\nexit 0\n", encoding="utf-8")
    managed_claude.chmod(0o755)

    custom_nbin_root = fixture_home / "Command Namespaces"
    custom_nbin_root.mkdir()
    (custom_nbin_root / "dinas").symlink_to(fake_sw)

    # Test in Bash and Zsh
    path_config = repo_root / "shell/sh_config/common/004_path"

    for shell_name in ("bash", "zsh"):
        # Pre-set PATH with ~/.local/bin first to test reordering!
        initial_path = f"{decoy_bin}:/usr/bin:/bin"
        script = f"""
        # Sourcing 004_path with fake /run/current-system replaced by fake_sw
        # We simulate sw/bin in the path config logic
        _dinas_projected_nbin_root="{custom_nbin_root}"
        source "{path_config}"
        # Substitute fake_sw for /run/current-system/sw/bin for isolated testing
        export PATH="${{PATH//\\/run\\/current-system\\/sw\\/bin/{fake_sw}}}"
        python3 -c '
import shutil, subprocess, os
resolved = shutil.which("claude")
print("RESOLVED:", resolved)
assert resolved == "{custom_nbin_root}/dinas/claude", f"Expected managed claude, got {{resolved}}"
out = subprocess.check_output([resolved, "--version"], text=True).strip()
print("VERSION:", out)
assert "2.1.259" in out
version = tuple(int(part) for part in out.split()[0].split("."))
assert version >= (2, 1, 250)
assert "decoy" not in out
'
        """
        res = subprocess.run(
            [f"/bin/{shell_name}", "-c", script],
            capture_output=True,
            text=True,
            env={"HOME": str(fixture_home), "PATH": initial_path},
        )
        assert res.returncode == 0, f"{shell_name} theorem failed: {res.stderr}\nStdout: {res.stdout}"


def test_disposable_activation_topology_symlink_rejected(reconciler_module, repo_root: Path, tmp_path: Path):
    """Prove that an existing nbin symlink to checkout/bin is strictly rejected without mutation."""
    checkout_bin = tmp_path / "checkout/bin"
    checkout_bin.mkdir(parents=True)
    (checkout_bin / "claude").write_text("#!/bin/sh\necho 'native-bridge-2.1.259'\n", encoding="utf-8")
    (checkout_bin / "claude").chmod(0o755)

    nbin_root = tmp_path / "nbin"
    nbin_root.symlink_to(checkout_bin)

    dinas_pkg = tmp_path / "dinas_pkg/bin"
    dinas_pkg.mkdir(parents=True)
    (dinas_pkg / "dinas-reconcile").write_text("#!/bin/sh\n", encoding="utf-8")
    (dinas_pkg / "dinas-reconcile").chmod(0o755)

    with pytest.raises(reconciler_module.ReconcileError, match="unexpected symlink") as exc_info:
        reconciler_module.reconcile_nbin(
            nbin_root=nbin_root,
            namespaces={"dinas": dinas_pkg},
        )
    assert str(checkout_bin) in str(exc_info.value)
    assert nbin_root.is_symlink()
    assert os.readlink(nbin_root) == str(checkout_bin)
    assert (checkout_bin / "claude").exists()
    assert not (nbin_root / "dinas").exists()


def test_untracked_claude_structural_isolation(repo_root: Path):
    """Structural test: nix-darwin/bin/claude is untracked and excluded from packaging."""
    dinas_commands_nix = (repo_root / "packages/dinas-commands/default.nix").read_text(encoding="utf-8")
    assert '"claude"' not in dinas_commands_nix

    # Check git tracking
    res = subprocess.run(
        ["git", "ls-files", "bin/claude"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    assert res.stdout.strip() == "", "bin/claude must not be tracked in git!"

    # Verify all tracked files in bin/ are accounted for
    tracked_files = subprocess.check_output(
        ["git", "ls-files", "bin/"],
        cwd=str(repo_root),
        text=True,
    ).splitlines()

    for path_str in tracked_files:
        cmd = Path(path_str).name
        if cmd == "codex-tooling-self-heal":
            # Documented non-packaged foreign link
            continue
        assert f'"{cmd}"' in dinas_commands_nix, f"Tracked command {cmd} missing from allowedCommands"


def _write_reconcile_manifest(path: Path, schema: str, field: str, entries: dict[str, str]) -> None:
    path.write_text(
        json.dumps({"schema": schema, field: entries}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _fake_foreign_lstat(original_lstat, foreign_paths: set[Path]):
    def foreign_lstat(path):
        result = original_lstat(path)
        if path in foreign_paths:
            return SimpleNamespace(st_uid=result.st_uid + 1, st_mode=result.st_mode)
        return result

    return foreign_lstat


def test_reconcile_plan_target_validation_and_collision_edges(plan_module, monkeypatch, tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    target_file = tmp_path / "target-file"
    target_file.write_text("file", encoding="utf-8")
    target_link = tmp_path / "target-link"
    target_link.symlink_to(target)

    assert "ordinary directory" in plan_module.validate_namespace_target_readonly(target_file, "namespace")
    assert "ordinary directory" in plan_module.validate_namespace_target_readonly(target_link, "namespace")

    target.chmod(0o777)
    try:
        assert "unsafe mode" in plan_module.validate_namespace_target_readonly(target, "namespace")
    finally:
        target.chmod(0o755)

    monkeypatch.setattr(
        plan_module.Path,
        "lstat",
        _fake_foreign_lstat(plan_module.Path.lstat, {target}),
    )
    assert "foreign-owned" in plan_module.validate_namespace_target_readonly(target, "namespace")

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "shared").write_text("#!/bin/sh\n", encoding="utf-8")
    (second / "shared").write_text("#!/bin/sh\n", encoding="utf-8")
    (first / "shared").chmod(0o755)
    (second / "shared").chmod(0o755)
    (first / ".hidden").write_text("hidden", encoding="utf-8")
    (first / "plain").write_text("plain", encoding="utf-8")
    report = plan_module.check_executable_collisions(
        {"first": first, "second": second, "missing": tmp_path / "missing"}
    )
    assert report.collisions == {"shared": ["first", "second"]}


def test_reconcile_plan_nbin_directory_manifest_actions(plan_module, tmp_path: Path):
    nbin_root = tmp_path / "nbin"
    nbin_root.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    old_target = tmp_path / "old-target"
    old_target.mkdir()
    stale_target = tmp_path / "stale-target"
    stale_target.mkdir()
    mismatch_target = tmp_path / "mismatch-target"
    mismatch_target.mkdir()

    (nbin_root / "keep").symlink_to(target)
    (nbin_root / "update").symlink_to(old_target)
    (nbin_root / "stale").symlink_to(stale_target)
    (nbin_root / "mismatch").symlink_to(mismatch_target)
    _write_reconcile_manifest(
        nbin_root / ".manifest.json",
        "dinas-nbin-manifest-v1",
        "namespaces",
        {
            "keep": str(target),
            "update": str(old_target),
            "stale": str(stale_target),
            "mismatch": str(tmp_path / "different"),
        },
    )

    plan = plan_module.create_reconcile_plan(
        nbin_root=nbin_root,
        namespaces={"keep": target, "update": target},
    )
    assert plan.eligible is True
    actions = {action.action: action for action in plan.nbin_actions}
    assert actions["keep_symlink"].path == str(nbin_root / "keep")
    assert actions["update_symlink"].target == str(target)
    assert actions["unlink_stale"].path == str(nbin_root / "stale")
    assert all(action.path != str(nbin_root / "mismatch") for action in plan.nbin_actions)


def test_reconcile_plan_nbin_root_guard_variants(plan_module, monkeypatch, tmp_path: Path):
    file_root = tmp_path / "file-root"
    file_root.write_text("not a directory", encoding="utf-8")
    file_plan = plan_module.create_reconcile_plan(file_root, {})
    assert file_plan.nbin_status == "invalid_type"
    assert any("not a directory" in blocker for blocker in file_plan.blockers)

    unsafe_root = tmp_path / "unsafe-root"
    unsafe_root.mkdir()
    unsafe_root.chmod(0o777)
    try:
        unsafe_plan = plan_module.create_reconcile_plan(unsafe_root, {})
        assert any("unsafe mode" in blocker for blocker in unsafe_plan.blockers)
    finally:
        unsafe_root.chmod(0o755)

    foreign_root = tmp_path / "foreign-root"
    foreign_root.mkdir()
    foreign_link = tmp_path / "foreign-link"
    foreign_target = tmp_path / "foreign-target"
    foreign_target.mkdir()
    foreign_link.symlink_to(foreign_target)
    foreign_paths = {foreign_root, foreign_link}
    monkeypatch.setattr(
        plan_module.Path,
        "lstat",
        _fake_foreign_lstat(plan_module.Path.lstat, foreign_paths),
    )
    assert any("foreign-owned" in blocker for blocker in plan_module.create_reconcile_plan(foreign_root, {}).blockers)
    link_plan = plan_module.create_reconcile_plan(
        foreign_link,
        {},
    )
    assert any("foreign-owned symlink" in blocker for blocker in link_plan.blockers)


def test_reconcile_plan_dev_root_states_and_manifest(plan_module, tmp_path: Path):
    nbin_root = tmp_path / "nbin"
    nbin_root.mkdir()
    missing_dev = tmp_path / "missing-dev"
    missing_plan = plan_module.create_reconcile_plan(
        nbin_root,
        {},
        nbin_dev_root=missing_dev,
        dev_namespaces={"dev": tmp_path},
    )
    assert missing_plan.nbin_dev_status == "missing"
    assert missing_plan.nbin_dev_actions[0].action == "create_dir"

    invalid_dev = tmp_path / "invalid-dev"
    invalid_dev.write_text("file", encoding="utf-8")
    invalid_plan = plan_module.create_reconcile_plan(nbin_root, {}, nbin_dev_root=invalid_dev)
    assert invalid_plan.nbin_dev_status == "invalid_type"

    target = tmp_path / "dev-target"
    target.mkdir()
    old_target = tmp_path / "dev-old"
    old_target.mkdir()
    stale_target = tmp_path / "dev-stale"
    stale_target.mkdir()
    dev_root = tmp_path / "dev"
    dev_root.mkdir()
    (dev_root / "keep").symlink_to(target)
    (dev_root / "update").symlink_to(old_target)
    (dev_root / "stale").symlink_to(stale_target)
    (dev_root / "unmanaged").write_text("owned by operator", encoding="utf-8")
    _write_reconcile_manifest(
        dev_root / ".manifest.json",
        "dinas-nbin-dev-manifest-v1",
        "namespaces",
        {"update": str(old_target), "stale": str(stale_target)},
    )
    invalid_target = tmp_path / "invalid-target"
    invalid_target.write_text("not a directory", encoding="utf-8")
    dev_plan = plan_module.create_reconcile_plan(
        nbin_root,
        {},
        nbin_dev_root=dev_root,
        dev_namespaces={
            "keep": target,
            "update": target,
            "unmanaged": target,
            "bad/name": target,
            "bad-target": invalid_target,
        },
    )
    assert dev_plan.eligible is False
    assert any("forbidden characters" in blocker for blocker in dev_plan.blockers)
    assert any("development namespace target" in blocker for blocker in dev_plan.blockers)
    assert any("unmanaged path" in blocker for blocker in dev_plan.blockers)
    actions = {action.action: action for action in dev_plan.nbin_dev_actions}
    assert actions["keep_symlink"].path == str(dev_root / "keep")
    assert actions["update_symlink"].target == str(target)
    assert actions["unlink_stale"].path == str(dev_root / "stale")


def test_reconcile_plan_dev_root_link_and_ownership_blockers(plan_module, monkeypatch, tmp_path: Path):
    nbin_root = tmp_path / "nbin"
    nbin_root.mkdir()
    target = tmp_path / "target"
    target.mkdir()

    dev_link = tmp_path / "dev-link"
    dev_link.symlink_to(target)
    link_plan = plan_module.create_reconcile_plan(nbin_root, {}, nbin_dev_root=dev_link)
    assert link_plan.nbin_dev_status == "invalid_symlink"

    dev_root = tmp_path / "dev-root"
    dev_root.mkdir()
    dev_root.chmod(0o777)
    try:
        unsafe_plan = plan_module.create_reconcile_plan(nbin_root, {}, nbin_dev_root=dev_root)
        assert any("nbin_dev_root has unsafe mode" in blocker for blocker in unsafe_plan.blockers)
    finally:
        dev_root.chmod(0o755)

    monkeypatch.setattr(
        plan_module.Path,
        "lstat",
        _fake_foreign_lstat(plan_module.Path.lstat, {dev_root}),
    )
    foreign_plan = plan_module.create_reconcile_plan(nbin_root, {}, nbin_dev_root=dev_root)
    assert any("nbin_dev_root is foreign-owned" in blocker for blocker in foreign_plan.blockers)


def test_reconcile_plan_ai_root_missing_and_manifest(plan_module, tmp_path: Path):
    nbin_root = tmp_path / "nbin"
    nbin_root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (home / ".codex").mkdir()
    (home / ".claude").mkdir()
    missing_root = home / "ai"
    missing_plan = plan_module.create_reconcile_plan(nbin_root, {}, ai_root=missing_root, home=home)
    assert missing_plan.ai_index_status == "missing"
    assert any(action.action == "create_symlink" for action in missing_plan.ai_index_actions)
    assert any(action.action == "skip_missing_target" for action in missing_plan.ai_index_actions)

    ai_root = tmp_path / "ai"
    ai_root.mkdir()
    old_target = tmp_path / "old-claude"
    old_target.mkdir()
    (ai_root / "codex").symlink_to(home / ".codex")
    (ai_root / "claude").symlink_to(old_target)
    (ai_root / "gemini").write_text("operator content", encoding="utf-8")
    stale_target = tmp_path / "stale-ai"
    stale_target.mkdir()
    (ai_root / "obsolete").symlink_to(stale_target)
    _write_reconcile_manifest(
        ai_root / ".manifest.json",
        "dinas-ai-manifest-v1",
        "links",
        {"claude": str(old_target), "obsolete": str(stale_target)},
    )
    (home / ".gemini").mkdir()
    ai_plan = plan_module.create_reconcile_plan(nbin_root, {}, ai_root=ai_root, home=home)
    assert ai_plan.eligible is False
    assert any("unmanaged non-symlink" in blocker for blocker in ai_plan.blockers)
    actions = {action.action: action for action in ai_plan.ai_index_actions}
    assert actions["keep_symlink"].path == str(ai_root / "codex")
    assert actions["update_symlink"].target == str(home / ".claude")
    assert actions["unlink_stale"].path == str(ai_root / "obsolete")


def test_reconcile_plan_ai_root_guard_variants(plan_module, tmp_path: Path):
    nbin_root = tmp_path / "nbin"
    nbin_root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "target"
    target.mkdir()

    ai_link = tmp_path / "ai-link"
    ai_link.symlink_to(target)
    link_plan = plan_module.create_reconcile_plan(nbin_root, {}, ai_root=ai_link, home=home)
    assert link_plan.ai_index_status == "invalid_symlink"

    ai_file = tmp_path / "ai-file"
    ai_file.write_text("file", encoding="utf-8")
    file_plan = plan_module.create_reconcile_plan(nbin_root, {}, ai_root=ai_file, home=home)
    assert file_plan.ai_index_status == "invalid_type"

    ai_root = tmp_path / "ai-root"
    ai_root.mkdir()
    ai_root.chmod(0o777)
    try:
        unsafe_plan = plan_module.create_reconcile_plan(nbin_root, {}, ai_root=ai_root, home=home)
        assert any("ai_root has unsafe mode" in blocker for blocker in unsafe_plan.blockers)
    finally:
        ai_root.chmod(0o755)


def test_reconciler_namespace_target_failure_modes(reconciler_module, monkeypatch, tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    target_file = tmp_path / "target-file"
    target_file.write_text("file", encoding="utf-8")
    target_link = tmp_path / "target-link"
    target_link.symlink_to(target)

    with pytest.raises(reconciler_module.ReconcileError, match="ordinary directory"):
        reconciler_module.validate_namespace_target(target_file, "namespace")
    with pytest.raises(reconciler_module.ReconcileError, match="ordinary directory"):
        reconciler_module.validate_namespace_target(target_link, "namespace")

    target.chmod(0o777)
    try:
        with pytest.raises(reconciler_module.ReconcileError, match="unsafe mode"):
            reconciler_module.validate_namespace_target(target, "namespace")
    finally:
        target.chmod(0o755)

    monkeypatch.setattr(
        reconciler_module.Path,
        "lstat",
        _fake_foreign_lstat(reconciler_module.Path.lstat, {target}),
    )
    with pytest.raises(reconciler_module.ReconcileError, match="foreign-owned"):
        reconciler_module.validate_namespace_target(target, "namespace")


def test_reconcile_nbin_topology_guards(reconciler_module, monkeypatch, tmp_path: Path):
    package = tmp_path / "package"
    package.mkdir()
    nbin_file = tmp_path / "nbin-file"
    nbin_file.write_text("file", encoding="utf-8")
    with pytest.raises(reconciler_module.ReconcileError, match="not a directory"):
        reconciler_module.reconcile_nbin(nbin_file, {"dinas": package})

    unsafe_root = tmp_path / "unsafe-root"
    unsafe_root.mkdir()
    unsafe_root.chmod(0o777)
    try:
        with pytest.raises(reconciler_module.ReconcileError, match="unsafe mode"):
            reconciler_module.reconcile_nbin(unsafe_root, {"dinas": package})
    finally:
        unsafe_root.chmod(0o755)

    foreign_root = tmp_path / "foreign-root"
    foreign_root.mkdir()
    foreign_link = tmp_path / "foreign-link"
    foreign_target = tmp_path / "foreign-target"
    foreign_target.mkdir()
    foreign_link.symlink_to(foreign_target)
    foreign_paths = {foreign_root, foreign_link}
    monkeypatch.setattr(
        reconciler_module.Path,
        "lstat",
        _fake_foreign_lstat(reconciler_module.Path.lstat, foreign_paths),
    )
    with pytest.raises(reconciler_module.ReconcileError, match="foreign-owned"):
        reconciler_module.reconcile_nbin(foreign_root, {"dinas": package})
    with pytest.raises(reconciler_module.ReconcileError, match="foreign-owned symlink"):
        reconciler_module.reconcile_nbin(
            foreign_link,
            {"dinas": package},
        )

    symlink_root = tmp_path / "symlink-root"
    symlink_target = tmp_path / "symlink-target"
    symlink_target.mkdir()
    symlink_root.symlink_to(symlink_target)
    with pytest.raises(reconciler_module.ReconcileError, match="unexpected symlink"):
        reconciler_module.reconcile_nbin(
            symlink_root,
            {"dinas": package},
        )
    assert symlink_root.is_symlink()


def test_reconcile_nbin_manifest_updates_and_stale_cleanup(reconciler_module, tmp_path: Path):
    nbin_root = tmp_path / "nbin"
    nbin_root.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    old_target = tmp_path / "old-target"
    old_target.mkdir()
    stale_target = tmp_path / "stale-target"
    stale_target.mkdir()
    package = tmp_path / "package"
    package.mkdir()

    (nbin_root / "keep").symlink_to(target)
    (nbin_root / "update").symlink_to(old_target)
    (nbin_root / "stale").symlink_to(stale_target)
    _write_reconcile_manifest(
        nbin_root / ".manifest.json",
        "dinas-nbin-manifest-v1",
        "namespaces",
        {"keep": str(target), "update": str(old_target), "stale": str(stale_target)},
    )

    report = reconciler_module.reconcile_nbin(
        nbin_root,
        {"keep": target, "update": target},
    )
    assert not report.has_collisions
    assert (nbin_root / "keep").resolve() == target
    assert (nbin_root / "update").resolve() == target
    assert not (nbin_root / "stale").exists()
    assert not (nbin_root / "stale").is_symlink()
    manifest = json.loads((nbin_root / ".manifest.json").read_text(encoding="utf-8"))
    assert manifest["namespaces"] == {
        "keep": str(target),
        "update": str(target),
    }

    collision_root = tmp_path / "collision-root"
    collision_root.mkdir()
    (collision_root / "dinas").write_text("operator-owned", encoding="utf-8")
    with pytest.raises(reconciler_module.ReconcileError, match="unmanaged non-symlink"):
        reconciler_module.reconcile_nbin(collision_root, {"dinas": package})


def test_reconcile_nbin_dev_guards_and_manifest(reconciler_module, monkeypatch, tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    root_link = tmp_path / "dev-link"
    root_link.symlink_to(target)
    with pytest.raises(reconciler_module.ReconcileError, match="must not be a symlink"):
        reconciler_module.reconcile_nbin_dev(root_link)

    root_file = tmp_path / "dev-file"
    root_file.write_text("file", encoding="utf-8")
    with pytest.raises(reconciler_module.ReconcileError, match="not a directory"):
        reconciler_module.reconcile_nbin_dev(root_file)

    unsafe_root = tmp_path / "unsafe-dev"
    unsafe_root.mkdir()
    unsafe_root.chmod(0o777)
    try:
        with pytest.raises(reconciler_module.ReconcileError, match="unsafe mode"):
            reconciler_module.reconcile_nbin_dev(unsafe_root)
    finally:
        unsafe_root.chmod(0o755)

    foreign_root = tmp_path / "foreign-dev"
    foreign_root.mkdir()
    monkeypatch.setattr(
        reconciler_module.Path,
        "lstat",
        _fake_foreign_lstat(reconciler_module.Path.lstat, {foreign_root}),
    )
    with pytest.raises(reconciler_module.ReconcileError, match="foreign-owned"):
        reconciler_module.reconcile_nbin_dev(foreign_root)

    state_root = tmp_path / "state-dev"
    state_root.mkdir()
    old_target = tmp_path / "old-target"
    old_target.mkdir()
    stale_target = tmp_path / "stale-target"
    stale_target.mkdir()
    (state_root / "keep").symlink_to(target)
    (state_root / "update").symlink_to(old_target)
    (state_root / "stale").symlink_to(stale_target)
    _write_reconcile_manifest(
        state_root / ".manifest.json",
        "dinas-nbin-dev-manifest-v1",
        "namespaces",
        {"update": str(old_target), "stale": str(stale_target)},
    )
    reconciler_module.reconcile_nbin_dev(
        state_root,
        {"keep": target, "update": target},
    )
    assert (state_root / "update").resolve() == target
    assert not (state_root / "stale").exists()
    assert not (state_root / "stale").is_symlink()

    collision_root = tmp_path / "collision-dev"
    collision_root.mkdir()
    (collision_root / "dev").write_text("operator-owned", encoding="utf-8")
    with pytest.raises(reconciler_module.ReconcileError, match="unmanaged path"):
        reconciler_module.reconcile_nbin_dev(collision_root, {"dev": target})


def test_reconcile_ai_index_guards_and_manifest(reconciler_module, monkeypatch, tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    ai_link = tmp_path / "ai-link"
    ai_link.symlink_to(target)
    with pytest.raises(reconciler_module.ReconcileError, match="must not be a symlink"):
        reconciler_module.reconcile_ai_index(ai_link, tmp_path / "home")

    ai_file = tmp_path / "ai-file"
    ai_file.write_text("file", encoding="utf-8")
    with pytest.raises(reconciler_module.ReconcileError, match="not a directory"):
        reconciler_module.reconcile_ai_index(ai_file, tmp_path / "home")

    unsafe_root = tmp_path / "unsafe-ai"
    unsafe_root.mkdir()
    unsafe_root.chmod(0o777)
    try:
        with pytest.raises(reconciler_module.ReconcileError, match="unsafe mode"):
            reconciler_module.reconcile_ai_index(unsafe_root, tmp_path / "home")
    finally:
        unsafe_root.chmod(0o755)

    foreign_root = tmp_path / "foreign-ai"
    foreign_root.mkdir()
    monkeypatch.setattr(
        reconciler_module.Path,
        "lstat",
        _fake_foreign_lstat(reconciler_module.Path.lstat, {foreign_root}),
    )
    with pytest.raises(reconciler_module.ReconcileError, match="foreign-owned"):
        reconciler_module.reconcile_ai_index(foreign_root, tmp_path / "home")

    home = tmp_path / "home"
    home.mkdir()
    (home / ".codex").mkdir()
    (home / ".claude").mkdir()
    (home / ".gemini").mkdir()
    old_target = tmp_path / "old-claude"
    old_target.mkdir()
    stale_target = tmp_path / "stale-ai"
    stale_target.mkdir()
    state_root = tmp_path / "state-ai"
    state_root.mkdir()
    (state_root / "codex").symlink_to(home / ".codex")
    (state_root / "claude").symlink_to(old_target)
    (state_root / "obsolete").symlink_to(stale_target)
    _write_reconcile_manifest(
        state_root / ".manifest.json",
        "dinas-ai-manifest-v1",
        "links",
        {"claude": str(old_target), "obsolete": str(stale_target)},
    )
    reconciler_module.reconcile_ai_index(state_root, home)
    assert (state_root / "claude").resolve() == home / ".claude"
    assert (state_root / "gemini").resolve() == home / ".gemini"
    assert not (state_root / "obsolete").exists()
    assert not (state_root / "obsolete").is_symlink()

    collision_root = tmp_path / "collision-ai"
    collision_root.mkdir()
    (collision_root / "codex").write_text("operator-owned", encoding="utf-8")
    with pytest.raises(reconciler_module.ReconcileError, match="unmanaged non-symlink"):
        reconciler_module.reconcile_ai_index(collision_root, home)
