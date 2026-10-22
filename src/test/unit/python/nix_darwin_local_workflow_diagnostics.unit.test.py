# nix-darwin-test-source-target: libexec/nix_darwin_local/deployment_workflow.py
from __future__ import annotations

import importlib
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def captured(repo_root, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    module = importlib.import_module("nix_darwin_local.deployment_workflow")
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(module, "_private_state_root", lambda: state)
    workflow = module.Workflow(
        repo_root, lambda argv, **kw: subprocess.CompletedProcess(argv, 7, "detail", "error"),
        display_command_output=False,
    )
    workflow.command("failed check", ["fake"], tmp_path)
    return workflow


def test_diagnostic_stream_is_bound_and_retained(captured):
    text, path, overflow = captured.diagnostic_stream("failed check", "stdout")
    assert (text, overflow) == ("detail", False)
    assert path.read_text() == text
    with pytest.raises(Exception, match="bound"):
        captured.diagnostic_stream("different invocation", "stdout")


@pytest.mark.parametrize("damage", ["missing", "contents", "path", "oversize", "symlink"])
def test_diagnostic_stream_rejects_unverified_evidence(captured, tmp_path, damage):
    record = captured.records[-1].stdout
    path = Path(record["path"])
    if damage == "missing":
        path.unlink()
    elif damage == "contents":
        path.write_text("forged")
    elif damage == "path":
        record["path"] = str(tmp_path / "outside")
    elif damage == "oversize":
        record["retained_bytes"] = 100 * 1024 * 1024
    else:
        target = tmp_path / "outside"
        target.write_text("detail")
        path.unlink()
        path.symlink_to(target)
    with pytest.raises((OSError, ValueError)):
        captured.diagnostic_stream("failed check", "stdout")


def test_diagnostic_reader_accepts_verified_overflow_and_invalid_utf8(captured, monkeypatch):
    module = importlib.import_module("nix_darwin_local.deployment_workflow")
    record = captured.records[-1].stdout
    path = Path(record["path"])
    raw = b"error: invalid byte \xff"
    path.write_bytes(raw)
    record.update(retained_bytes=len(raw), retained_sha256=module.sha256_bytes(raw), overflow=True)
    text, verified, overflow = captured.diagnostic_stream("failed check", "stdout")
    assert "error: invalid byte" in text and "\ufffd" in text
    assert overflow and verified == path


@pytest.mark.parametrize("failure_point", ["writer", "summary", "fsync"])
def test_known_process_failure_survives_evidence_failure(captured, monkeypatch, failure_point):
    module = importlib.import_module("nix_darwin_local.deployment_workflow")
    diagnostics = importlib.import_module("nix_darwin_local.command_diagnostics")
    name = {"writer": "_write_bounded_stream", "summary": "redacted_summary", "fsync": "fsync_directory"}[failure_point]
    monkeypatch.setattr(module, name, lambda *a, **kw: (_ for _ in ()).throw(OSError("fixture storage failure")))
    result = captured.command("second failure", ["fake"], captured.core_dir)
    assert result.returncode == 7
    message = diagnostics.failure_explanation(captured, "second failure", result.returncode)
    assert "second failure failed (exit 7)" in message
    if failure_point == "writer":
        assert "evidence unavailable" in message
    else:
        assert "error" in message and "private evidence:" in message


def test_failed_command_live_summary_is_terminal_safe(captured, capsys):
    captured.display_command_output = True
    captured.runner = lambda argv, **kw: subprocess.CompletedProcess(
        argv, 7, "", "\x1b]52;c;hidden\x07error: denied; --password 'hidden words'\n",
    )
    captured.command("failed check", ["fake"], captured.core_dir)
    output = capsys.readouterr()
    assert "error: denied" in output.err
    assert "hidden" not in output.err and "\x1b" not in output.err
