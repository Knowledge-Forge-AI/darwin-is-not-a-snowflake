# nix-darwin-test-source-target: libexec/nix_darwin_local/report_diagnostics.py
from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


def test_report_redaction_precedes_truncation_and_does_not_replace_evidence(
    repo_root, tmp_path, workflow_setup, monkeypatch,
):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    module = importlib.import_module("nix_darwin_local.report_diagnostics")
    wf = workflow_setup(label="qualification run")
    root = tmp_path / "reports"
    root.mkdir(mode=0o700)
    secret = "hidden-secret-line\n" * 5000
    _write_report(root, [(1, "flake check", 1)],
                  [(1, "flake check", "fail", 1, "runner/001-flake-check/output.txt")],
                  {"runner/001-flake-check/output.txt": "password='" + secret + "'\nerror: denied"})
    text, path = module.retain_report_diagnostic(wf, "qualification run", root)
    assert "hidden-secret-line" not in text and "error: denied" in text
    original = path.read_bytes()
    assert module.retain_report_diagnostic(wf, "qualification run", root) is None
    assert path.read_bytes() == original


@pytest.fixture
def workflow_setup(repo_root, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    deployment = importlib.import_module("nix_darwin_local.deployment_workflow")
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(deployment, "_private_state_root", lambda: state)

    def _create_workflow(exit_code: int = 1, label: str = "qualification check"):
        wf = deployment.Workflow(
            repo_root,
            lambda argv, **kw: subprocess.CompletedProcess(argv, exit_code, "stdout-msg", "stderr-msg"),
            display_command_output=False,
        )
        wf.command(label, ["test-command", "--report-root", str(tmp_path / "reports")], tmp_path)
        return wf

    return _create_workflow


def _write_report(
    report_root: Path,
    failures: list[tuple[int, str, int]],
    steps: list[tuple[int, str, str, int, str]] | None = None,
    outputs: dict[str, str] | None = None,
    mode: str = "all",
) -> Path:
    report_dir = report_root / mode / "latest"
    runner_dir = report_dir / "runner"
    runner_dir.mkdir(parents=True, mode=0o700)

    failures_file = runner_dir / "failures.tsv"
    f_lines = ["sequence\tlabel\texit_code\n"]
    for seq, label, code in failures:
        f_lines.append(f"{seq}\t{label}\t{code}\n")
    failures_file.write_text("".join(f_lines), encoding="utf-8")

    if steps is not None:
        steps_file = runner_dir / "steps.tsv"
        s_lines = ["ordinal\tlabel\tstatus\texit_code\toutput_log\n"]
        for ordn, label, status, code, out_log in steps:
            s_lines.append(f"{ordn}\t{label}\t{status}\t{code}\t{out_log}\n")
        steps_file.write_text("".join(s_lines), encoding="utf-8")

    if outputs:
        for rel_path, content in outputs.items():
            out_file = report_dir / rel_path
            out_file.parent.mkdir(parents=True, mode=0o700)
            out_file.write_text(content, encoding="utf-8")

    return report_dir


def test_retain_report_diagnostic_success_and_cleanup_retention(repo_root, tmp_path, workflow_setup, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    module = importlib.import_module("nix_darwin_local.report_diagnostics")

    wf = workflow_setup(exit_code=1, label="selected-flake report qualification")
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)

    _write_report(
        report_root,
        failures=[(1, "unit tests", 1)],
        steps=[(1, "unit tests", "fail", 1, "runner/001-unit-tests/output.txt")],
        outputs={
            "runner/001-unit-tests/output.txt": "running test\nFAILED test_calc.py::test_add\nAssertionError: 1 != 2\n",
        },
    )

    result = module.retain_report_diagnostic(wf, "selected-flake report qualification", report_root)
    assert result is not None
    text, path = result

    assert "unit tests (exit 1):" in text
    assert "FAILED test_calc.py::test_add" in text
    assert "AssertionError: 1 != 2" in text
    assert path.name == "0000.diagnostic"
    assert path.is_file()
    assert path.read_text() == text

    # Simulate scratch cleanup: delete report_root
    shutil.rmtree(report_root)
    assert not report_root.exists()

    # Retained diagnostic evidence remains available after report cleanup
    assert path.is_file()
    assert path.read_text() == text


def test_retain_report_diagnostic_preserves_first_recorded_failure_order(repo_root, tmp_path, workflow_setup, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    module = importlib.import_module("nix_darwin_local.report_diagnostics")

    wf = workflow_setup(exit_code=2, label="qualification run")
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)

    _write_report(
        report_root,
        failures=[
            (1, "first step failure", 2),
            (2, "second step failure", 3),
        ],
        steps=[
            (1, "first step failure", "fail", 2, "runner/001-first-step-failure/output.txt"),
            (2, "second step failure", "fail", 3, "runner/002-second-step-failure/output.txt"),
        ],
        outputs={
            "runner/001-first-step-failure/output.txt": "first error tail\n",
            "runner/002-second-step-failure/output.txt": "second error tail\n",
        },
    )

    result = module.retain_report_diagnostic(wf, "qualification run", report_root)
    assert result is not None
    text, path = result

    assert "first step failure (exit 2):" in text
    assert "first error tail" in text
    assert "second step failure" not in text
    assert "second error tail" not in text


def test_retain_report_diagnostic_framework_outcome_not_in_steps(repo_root, tmp_path, workflow_setup, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    module = importlib.import_module("nix_darwin_local.report_diagnostics")

    wf = workflow_setup(exit_code=4, label="qualification run")
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)

    # Framework/policy failure recorded in failures.tsv without steps.tsv entry
    _write_report(
        report_root,
        failures=[(1, "policy qualification gate", 4)],
        steps=[],
    )

    result = module.retain_report_diagnostic(wf, "qualification run", report_root)
    assert result is not None
    text, path = result

    assert "first recorded failure: policy qualification gate (exit 4)" in text
    assert "no recorded child output" in text
    assert path.is_file()
    assert path.read_text() == text


def test_retain_report_diagnostic_unbound_label_raises(repo_root, tmp_path, workflow_setup, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    module = importlib.import_module("nix_darwin_local.report_diagnostics")

    wf = workflow_setup(exit_code=1, label="recorded label")
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)

    with pytest.raises(Exception, match="bound"):
        module.retain_report_diagnostic(wf, "different unbound label", report_root)

    wf.records.clear()
    with pytest.raises(Exception, match="bound"):
        module.retain_report_diagnostic(wf, "recorded label", report_root)


def test_retain_report_diagnostic_passing_command_returns_none(repo_root, tmp_path, workflow_setup, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    module = importlib.import_module("nix_darwin_local.report_diagnostics")

    wf = workflow_setup(exit_code=0, label="successful run")
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)
    _write_report(report_root, failures=[(1, "ignored", 1)])

    assert module.retain_report_diagnostic(wf, "successful run", report_root) is None


def test_retain_report_diagnostic_missing_or_empty_reports_return_none(repo_root, tmp_path, workflow_setup, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    module = importlib.import_module("nix_darwin_local.report_diagnostics")

    wf = workflow_setup(exit_code=1, label="check")

    # None report root
    assert module.retain_report_diagnostic(wf, "check", None) is None

    # Non-existent report root
    assert module.retain_report_diagnostic(wf, "check", tmp_path / "nonexistent") is None

    # Empty report root
    empty_root = tmp_path / "empty"
    empty_root.mkdir(mode=0o700)
    assert module.retain_report_diagnostic(wf, "check", empty_root) is None

    # Missing failures.tsv
    missing_failures = tmp_path / "missing-failures"
    (missing_failures / "all" / "latest" / "runner").mkdir(parents=True, mode=0o700)
    assert module.retain_report_diagnostic(wf, "check", missing_failures) is None

    # Empty failures.tsv (header only, no failures recorded)
    empty_failures = tmp_path / "empty-failures"
    empty_failures.mkdir(mode=0o700)
    _write_report(empty_failures, failures=[])
    assert module.retain_report_diagnostic(wf, "check", empty_failures) is None


@pytest.mark.parametrize("hostile_path", [
    "runner/../../../etc/passwd",
    "/etc/passwd",
    "../outside.txt",
    "runner/escape/../../passwd",
    "arbitrary/path/output.txt",
    "runner/001-step/unsupported.log",
    "runner//output.txt",
])
def test_retain_report_diagnostic_rejects_hostile_paths(repo_root, tmp_path, workflow_setup, monkeypatch, hostile_path):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    module = importlib.import_module("nix_darwin_local.report_diagnostics")

    wf = workflow_setup(exit_code=1, label="qualification run")
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)

    _write_report(
        report_root,
        failures=[(1, "hostile step", 1)],
        steps=[(1, "hostile step", "fail", 1, hostile_path)],
    )

    assert module.retain_report_diagnostic(wf, "qualification run", report_root) is None


@pytest.mark.parametrize("target", ["report_root", "runner_dir", "failures_file", "output_file"])
def test_retain_report_diagnostic_rejects_symlinks(repo_root, tmp_path, workflow_setup, monkeypatch, target):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    module = importlib.import_module("nix_darwin_local.report_diagnostics")

    wf = workflow_setup(exit_code=1, label="qualification run")
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir(mode=0o700)

    _write_report(
        real_dir,
        failures=[(1, "unit tests", 1)],
        steps=[(1, "unit tests", "fail", 1, "runner/001-unit-tests/output.txt")],
        outputs={"runner/001-unit-tests/output.txt": "error output\n"},
    )

    outside = tmp_path / "outside_target"
    outside.write_text("compromised\n")

    if target == "report_root":
        symlink_root = tmp_path / "symlink_root"
        symlink_root.symlink_to(real_dir)
        assert module.retain_report_diagnostic(wf, "qualification run", symlink_root) is None
    elif target == "runner_dir":
        runner_dir = real_dir / "all" / "latest" / "runner"
        shutil.rmtree(runner_dir)
        runner_dir.symlink_to(outside)
        assert module.retain_report_diagnostic(wf, "qualification run", real_dir) is None
    elif target == "failures_file":
        f_file = real_dir / "all" / "latest" / "runner" / "failures.tsv"
        f_file.unlink()
        f_file.symlink_to(outside)
        assert module.retain_report_diagnostic(wf, "qualification run", real_dir) is None
    elif target == "output_file":
        out_file = real_dir / "all" / "latest" / "runner" / "001-unit-tests" / "output.txt"
        out_file.unlink()
        out_file.symlink_to(outside)
        assert module.retain_report_diagnostic(wf, "qualification run", real_dir) is None


def test_retain_report_diagnostic_rejects_hardlinks(repo_root, tmp_path, workflow_setup, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    module = importlib.import_module("nix_darwin_local.report_diagnostics")

    wf = workflow_setup(exit_code=1, label="qualification run")
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)

    _write_report(
        report_root,
        failures=[(1, "unit tests", 1)],
        steps=[(1, "unit tests", "fail", 1, "runner/001-unit-tests/output.txt")],
        outputs={"runner/001-unit-tests/output.txt": "error output\n"},
    )

    # Create hard link to failures.tsv
    failures_tsv = report_root / "all" / "latest" / "runner" / "failures.tsv"
    hardlink = report_root / "failures_hardlink.tsv"
    os.link(failures_tsv, hardlink)
    assert module.retain_report_diagnostic(wf, "qualification run", report_root) is None

    # Remove hard link to failures.tsv, add hard link to output.txt
    hardlink.unlink()
    output_txt = report_root / "all" / "latest" / "runner" / "001-unit-tests" / "output.txt"
    out_hardlink = report_root / "output_hardlink.txt"
    os.link(output_txt, out_hardlink)
    assert module.retain_report_diagnostic(wf, "qualification run", report_root) is None


def test_retain_report_diagnostic_rejects_foreign_ownership(repo_root, tmp_path, workflow_setup, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    module = importlib.import_module("nix_darwin_local.report_diagnostics")

    wf = workflow_setup(exit_code=1, label="qualification run")
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)

    _write_report(
        report_root,
        failures=[(1, "unit tests", 1)],
        steps=[(1, "unit tests", "fail", 1, "runner/001-unit-tests/output.txt")],
        outputs={"runner/001-unit-tests/output.txt": "error output\n"},
    )

    # Monkeypatch getuid to simulate foreign-owned file
    monkeypatch.setattr(os, "getuid", lambda: 99999)
    assert module.retain_report_diagnostic(wf, "qualification run", report_root) is None


@pytest.mark.parametrize("oversize_target", ["failures", "steps", "output"])
def test_retain_report_diagnostic_rejects_oversize_files(repo_root, tmp_path, workflow_setup, monkeypatch, oversize_target):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    module = importlib.import_module("nix_darwin_local.report_diagnostics")

    wf = workflow_setup(exit_code=1, label="qualification run")
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)

    _write_report(
        report_root,
        failures=[(1, "unit tests", 1)],
        steps=[(1, "unit tests", "fail", 1, "runner/001-unit-tests/output.txt")],
        outputs={"runner/001-unit-tests/output.txt": "normal output\n"},
    )

    report_dir = report_root / "all" / "latest"
    if oversize_target == "failures":
        f_file = report_dir / "runner" / "failures.tsv"
        # Over 1 MiB limit
        f_file.write_bytes(b"sequence\tlabel\texit_code\n" + b"1\tunit tests\t1\n" + b"#" * (module.MAX_TSV_BYTES + 10))
    elif oversize_target == "steps":
        s_file = report_dir / "runner" / "steps.tsv"
        # Over 1 MiB limit
        s_file.write_bytes(b"ordinal\tlabel\tstatus\texit_code\toutput_log\n" + b"#" * (module.MAX_TSV_BYTES + 10))
    elif oversize_target == "output":
        o_file = report_dir / "runner" / "001-unit-tests" / "output.txt"
        # Over 10 MiB limit
        with o_file.open("wb") as handle:
            handle.truncate(module.MAX_OUTPUT_FILE_BYTES + 100)

    assert module.retain_report_diagnostic(wf, "qualification run", report_root) is None


def test_retain_report_diagnostic_sanitizes_control_and_ansi(repo_root, tmp_path, workflow_setup, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    module = importlib.import_module("nix_darwin_local.report_diagnostics")

    wf = workflow_setup(exit_code=1, label="qualification run")
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)

    raw_output = "\x1b[31;1mFATAL ERROR\x1b[0m\r\nProgress: 50%\rProgress: 100%\nNull byte: \x00 Bell: \x07 End of trace"
    _write_report(
        report_root,
        failures=[(1, "test with \x1b[32mcolors\x1b[0m", 1)],
        steps=[(1, "test with \x1b[32mcolors\x1b[0m", "fail", 1, "runner/001-test/output.txt")],
        outputs={"runner/001-test/output.txt": raw_output},
    )

    result = module.retain_report_diagnostic(wf, "qualification run", report_root)
    assert result is not None
    text, path = result

    assert "\x1b" not in text
    assert "\x00" not in text
    assert "\x07" not in text
    assert "\r" not in text
    assert "test with colors (exit 1):" in text
    assert "FATAL ERROR" in text
    assert "End of trace" in text


def test_retain_report_diagnostic_redacts_secrets(repo_root, tmp_path, workflow_setup, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    module = importlib.import_module("nix_darwin_local.report_diagnostics")

    wf = workflow_setup(exit_code=1, label="qualification run")
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)

    secret_output = (
        (
        'error occurred during auth\n'
        'Authorization: Bearer super-secret-token-12345\n'
        'token=ghp_000000000000000000000000000000000000\n'
        'password=super-confidential-password\n'
        '-----BEGIN OPENSSH PRIVATE KEY-----\n'
        'SYNTHETIC_REDACTION_FIXTURE_NOT_A_CRYPTOGRAPHIC_KEY\n'
        '-----END OPENSSH PRIVATE KEY-----\n'
    )
    )

    _write_report(
        report_root,
        failures=[(1, "auth test", 1)],
        steps=[(1, "auth test", "fail", 1, "runner/001-auth-test/output.txt")],
        outputs={"runner/001-auth-test/output.txt": secret_output},
    )

    result = module.retain_report_diagnostic(wf, "qualification run", report_root)
    assert result is not None
    text, path = result

    assert "super-secret-token-12345" not in text
    assert "super-confidential-password" not in text
    assert "SYNTHETIC_REDACTION_FIXTURE_NOT_A_CRYPTOGRAPHIC_KEY" not in text
    assert "[REDACTED]" in text or "[REDACTED PRIVATE KEY OUTPUT]" in text


def test_retain_report_diagnostic_bounds_large_output_tail(repo_root, tmp_path, workflow_setup, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    module = importlib.import_module("nix_darwin_local.report_diagnostics")

    wf = workflow_setup(exit_code=1, label="qualification run")
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)

    # 5000 lines of output
    lines = [f"info line {i}" for i in range(5000)]
    lines.append("CRITICAL: root cause at the end")
    large_output = "\n".join(lines) + "\n"

    _write_report(
        report_root,
        failures=[(1, "large step", 1)],
        steps=[(1, "large step", "fail", 1, "runner/001-large-step/output.txt")],
        outputs={"runner/001-large-step/output.txt": large_output},
    )

    result = module.retain_report_diagnostic(wf, "qualification run", report_root)
    assert result is not None
    text, path = result

    assert "CRITICAL: root cause at the end" in text
    # Earliest lines should be truncated, only meaningful tail preserved
    assert "info line 0\n" not in text
    assert "info line 1\n" not in text
    assert len(text) <= module.MAX_EXCERPT_CHARS + 100


@pytest.mark.parametrize("bad_failures_content", [
    "bad\theader\tonly\n1\tstep\t1\n",
    "sequence\tlabel\texit_code\n1\tstep\tnotanint\n",
    "sequence\tlabel\texit_code\n1\tstep\t0\n",
    "sequence\tlabel\texit_code\n-1\tstep\t1\n",
    "sequence\tlabel\texit_code\n1\t\t1\n",
    "sequence\tlabel\texit_code\n1\tstep\n",
])
def test_retain_report_diagnostic_rejects_malformed_tsv(repo_root, tmp_path, workflow_setup, monkeypatch, bad_failures_content):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    module = importlib.import_module("nix_darwin_local.report_diagnostics")

    wf = workflow_setup(exit_code=1, label="qualification run")
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)

    runner_dir = report_root / "all" / "latest" / "runner"
    runner_dir.mkdir(parents=True, mode=0o700)
    (runner_dir / "failures.tsv").write_text(bad_failures_content, encoding="utf-8")

    assert module.retain_report_diagnostic(wf, "qualification run", report_root) is None


def test_open_safe_file_rejects_fifo_promptly(repo_root, tmp_path):
    if not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"):
        pytest.skip("nonblocking FIFOs are not supported on this platform")

    fifo_path = tmp_path / "report.fifo"
    os.mkfifo(fifo_path)
    # A blocking regression must time out without hanging the test runner.
    script = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from nix_darwin_local.report_diagnostics import _open_safe_file
assert _open_safe_file(Path(sys.argv[2]), 1024) is None
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(repo_root / "libexec"), str(fifo_path)],
        capture_output=True,
        text=True,
        timeout=10.0,
    )
    assert result.returncode == 0, result.stderr
