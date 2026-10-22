# nix-darwin-test-source-target: libexec/nix_darwin_local/command_diagnostics.py
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("raw,secret", [
    ('Authorization: Digest username="hidden", response="also-hidden"\nerror: denied', 'hidden'),
    ('Cookie: session=hidden; other=also-hidden\nerror: denied', 'hidden'),
    ('command --password "two words hidden"\nerror: denied', 'hidden'),
    ('{"argv": ["curl", "--token", "hidden"]', 'hidden'),
    ('\x1b]52;c;hidden\x07error: denied', 'hidden'),
    ('\x1b]8;;https://hidden\x1b\\error: denied\x1b]8;;\x1b\\', 'hidden'),
])
def test_secret_and_terminal_forms_in_text_fallback(diagnostics, raw, secret):
    safe = diagnostics.sanitize_text(raw)
    assert secret not in safe and "\x1b" not in safe


def test_formatter_exception_preserves_operation_exit_and_verified_pointer(diagnostics, tmp_path, monkeypatch):
    path = tmp_path / "0000.stdout"
    path.write_text("detail")
    workflow = MockWorkflow(streams={"stdout": ("detail", path, False), "stderr": ("", None, False)})
    monkeypatch.setattr(diagnostics, "_stream_detail", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("hidden")))
    message = diagnostics.failure_explanation(workflow, "fixture qualification", 17)
    assert "fixture qualification failed (exit 17)" in message
    assert "formatting unavailable" in message and "hidden" not in message
    assert message.endswith(str(path))
    monkeypatch.setattr(diagnostics, "sanitize_text", lambda *a: (_ for _ in ()).throw(RuntimeError("hidden")))
    message = diagnostics.failure_explanation(workflow, "fixture qualification", 17)
    assert "fixture qualification failed (exit 17)" in message and message.endswith(str(path))


def test_not_run_is_not_a_failed_check_and_partial_preview_is_marked(diagnostics, tmp_path):
    path = tmp_path / "0000.stdout"
    doc = {"schema": "composition-nd-promotion-result-v1", "outcome": "fail", "commands": [],
           "checks": {"contracts": "fail", "tests": "not-run"},
           "failure": {"message": "policy rejected"}}
    workflow = MockWorkflow(streams={"stdout": (json.dumps(doc), path, False), "stderr": ("", None, False)})
    message = diagnostics.failure_explanation(workflow, "qualifier", 1)
    assert "contracts (fail)" in message and "tests (not-run)" not in message
    workflow.streams["stdout"] = ("warning\npartial error", path, True)
    assert "truncated or unavailable" in diagnostics.failure_explanation(workflow, "qualifier", 1)


@pytest.fixture
def diagnostics(repo_root: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    return importlib.import_module("nix_darwin_local.command_diagnostics")


class MockWorkflow:
    def __init__(
        self,
        streams: dict[str, tuple[str, Path | None, bool] | Exception] | None = None,
        records: list | None = None,
    ):
        self.streams = streams or {}
        self.records = records or []

    def diagnostic_stream(self, label: str, stream: str) -> tuple[str, Path, bool]:
        if stream in self.streams:
            res = self.streams[stream]
            if isinstance(res, Exception):
                raise res
            return res
        raise ValueError(f"no stream configured for {stream}")


def test_historical_revision_not_unavailable_with_earlier_pass_commands(diagnostics, tmp_path):
    evidence_file = tmp_path / "0001.stdout"
    evidence_file.write_text("qualifier output")
    rev = "a" * 40

    qualifier_result = {
        "schema": "composition-nd-promotion-result-v1",
        "outcome": "fail",
        "candidate": {
            "url": f"https://github.com/example/repo?rev={rev}",
            "requestedRev": rev,
            "resolvedRev": rev,
            "revMatched": True,
            "urlMatched": True,
        },
        "commands": [
            {
                "argv": ["nix", "flake", "metadata", f"https://github.com/example/repo?rev={rev}"],
                "exitCode": 0,
                "status": "pass",
                "stdout": {"preview": "metadata success"},
                "stderr": {"preview": ""},
            },
            {
                "argv": ["nix", "eval", ".#darwinConfigurations.test"],
                "exitCode": 1,
                "status": "fail",
                "stdout": {"preview": ""},
                "stderr": {"preview": "error: attribute 'test' missing"},
            },
        ],
    }

    workflow = MockWorkflow(
        streams={
            "stdout": (json.dumps(qualifier_result), evidence_file, False),
            "stderr": ("", None, False),
        }
    )

    msg = diagnostics.failure_explanation(
        workflow, "candidate qualifier", 1, entry_point="tools/qualify-promotion"
    )

    assert "tools/qualify-promotion (exit 1)" in msg
    assert "first recorded failing nested command: nix eval" in msg
    assert "error: attribute 'test' missing" in msg
    assert "private evidence: unavailable" not in msg
    assert f"private evidence: {evidence_file}" in msg
    # Metadata success from pass command should not dominate
    assert msg.index("first recorded failing nested command") < 200
    assert len(msg) < 2600


def test_stdout_only_failure(diagnostics, tmp_path):
    evidence_file = tmp_path / "0000.stdout"
    evidence_file.write_text("out of memory while building derivation")

    workflow = MockWorkflow(
        streams={
            "stdout": ("error: out of memory while building derivation", evidence_file, False),
            "stderr": ("", None, False),
        }
    )

    msg = diagnostics.failure_explanation(workflow, "build step", 137)

    assert "build step failed (exit 137)" in msg
    assert "out of memory while building derivation" in msg
    assert f"private evidence: {evidence_file}" in msg
    assert len(msg) < 2600


def test_no_child_policy_failure_and_failed_checks(diagnostics, tmp_path):
    evidence_file = tmp_path / "0002.stdout"
    evidence_file.write_text("policy failure")

    qualifier_result = {
        "schema": "composition-nd-promotion-result-v1",
        "outcome": "fail",
        "failure": {
            "category": "protected-file-mutation",
            "message": "protected file flake.lock was modified",
            "details": {"file": "flake.lock"},
        },
        "commands": [
            {
                "argv": ["nix", "flake", "lock"],
                "exitCode": 0,
                "status": "pass",
            }
        ],
        "checks": {
            "lower": {"lint": "pass"},
            "higher": {"security-audit": "fail", "typecheck": "failed"},
        },
        "lock": {"byteIdentical": False},
    }

    workflow = MockWorkflow(
        streams={
            "stdout": (json.dumps(qualifier_result), evidence_file, False),
            "stderr": ("", None, False),
        }
    )

    msg = diagnostics.failure_explanation(
        workflow, "candidate qualifier", 1, entry_point="tools/qualify-promotion"
    )

    assert "tools/qualify-promotion (exit 1)" in msg
    assert "protected-file-mutation" in msg or "flake.lock was modified" in msg
    assert "security-audit (fail)" in msg
    assert "typecheck (failed)" in msg
    assert "lock byte identity" in msg
    assert f"private evidence: {evidence_file}" in msg
    assert len(msg) < 2600


def test_malformed_json_and_reader_errors(diagnostics, tmp_path):
    # Malformed JSON should not raise, original exit preserved, evidence unavailable
    workflow_malformed = MockWorkflow(
        streams={
            "stdout": ('{"schema": "composition-nd-promotion-result-v1", "unclosed', None, False),
            "stderr": ("", None, False),
        }
    )
    msg1 = diagnostics.failure_explanation(workflow_malformed, "run qualifier", 2)
    assert "run qualifier failed (exit 2)" in msg1
    assert "private evidence: unavailable" in msg1

    # Reader exception should not crash
    workflow_err = MockWorkflow(
        streams={
            "stdout": RuntimeError("corrupted stream read"),
            "stderr": RuntimeError("corrupted stream read"),
        }
    )
    msg2 = diagnostics.failure_explanation(
        workflow_err, "compile", 3, entry_point="bin/compile"
    )
    assert "compile failed: bin/compile (exit 3)" in msg2
    assert "private evidence: unavailable" in msg2


def test_oversized_stream_bounded_and_preserves_head_and_tail(diagnostics, tmp_path):
    evidence_file = tmp_path / "0000.stderr"
    evidence_file.write_text("big stderr")

    head_error = "CRITICAL: configuration parsing syntax error at line 5"
    tail_error = "FATAL: process terminated due to earlier syntax error"
    middle_padding = "x" * 20000
    big_diagnostic = f"{head_error}\n{middle_padding}\n{tail_error}"

    workflow = MockWorkflow(
        streams={
            "stdout": ("", None, False),
            "stderr": (big_diagnostic, evidence_file, True),
        }
    )

    msg = diagnostics.failure_explanation(workflow, "builder", 1, entry_point="bin/builder")

    assert "builder failed: bin/builder (exit 1)" in msg
    assert head_error in msg
    assert tail_error in msg
    assert "[truncated]" in msg
    assert f"private evidence: {evidence_file}" in msg
    assert len(msg) < 2600


def test_control_and_ansi_osc_sanitized(diagnostics, tmp_path):
    evidence_file = tmp_path / "0000.stderr"
    evidence_file.write_text("terminal control")

    raw = (
        "\x1b[31;1mError:\x1b[0m \x1b[2Ksomething bad happened\r\n"
        "\x1b]8;;https://example.invalid/phish\x07click here\x1b]8;;\x07\x00\x07\x08"
    )

    workflow = MockWorkflow(
        streams={
            "stdout": ("", None, False),
            "stderr": (raw, evidence_file, False),
        }
    )

    msg = diagnostics.failure_explanation(workflow, "task", 1)

    assert "\x1b" not in msg
    assert "\x00" not in msg
    assert "\x07" not in msg
    assert "\x08" not in msg
    assert "Error:" in msg
    assert "something bad happened" in msg
    assert "click here" in msg
    assert len(msg) < 2600


def test_secrets_and_gap_closures_redacted(diagnostics, tmp_path):
    evidence_file = tmp_path / "0000.stderr"
    evidence_file.write_text("secrets")

    raw = (
        "token=super-secret-12345\n"
        "password: my-cleartext-pass\n"
        "export GITHUB_TOKEN=\"quoted-gh-token-value\"\n"
        "SHELL_SECRET='quoted-single-secret'\n"
        "--password flag-secret-value\n"
        "--token space-token-value\n"
        "-p short-pass-value\n"
        "Proxy-Authorization: Bearer proxy-secret-token\n"
        "Authorization: Bearer auth-bearer-token\n"
        "https://user:secretpass@example.com/api?api_key=key-secret-999&other=1\n"
    )

    workflow = MockWorkflow(
        streams={
            "stdout": ("", None, False),
            "stderr": (raw, evidence_file, False),
        }
    )

    msg = diagnostics.failure_explanation(workflow, "auth task", 1)

    assert "super-secret-12345" not in msg
    assert "my-cleartext-pass" not in msg
    assert "quoted-gh-token-value" not in msg
    assert "quoted-single-secret" not in msg
    assert "flag-secret-value" not in msg
    assert "space-token-value" not in msg
    assert "short-pass-value" not in msg
    assert "proxy-secret-token" not in msg
    assert "auth-bearer-token" not in msg
    assert "secretpass" not in msg
    assert "key-secret-999" not in msg
    assert "[REDACTED]" in msg
    assert len(msg) < 2600


def test_private_key_redacted(diagnostics, tmp_path):
    evidence_file = tmp_path / "0000.stderr"
    evidence_file.write_text("private key")

    raw = (
        (
        '-----BEGIN PRIVATE KEY-----\n'
        'SYNTHETIC_REDACTION_FIXTURE_NOT_A_CRYPTOGRAPHIC_KEY\n'
        '-----END PRIVATE KEY-----\n'
    )
    )

    workflow = MockWorkflow(
        streams={
            "stdout": ("", None, False),
            "stderr": (raw, evidence_file, False),
        }
    )

    msg = diagnostics.failure_explanation(workflow, "key task", 1)

    assert "SYNTHETIC_REDACTION_FIXTURE_NOT_A_CRYPTOGRAPHIC_KEY" not in msg
    assert "REDACTED PRIVATE KEY OUTPUT" in msg
    assert len(msg) < 2600


def test_argv_boundaries_and_nested_previews(diagnostics, tmp_path):
    evidence_file = tmp_path / "0000.stdout"
    evidence_file.write_text("argv boundaries")

    huge_argv = ["nix", "build", "--token", "secret-token-in-argv", "--arg", "with space"] + [
        f"--flag{i}=val{i}" for i in range(100)
    ]

    qualifier_result = {
        "schema": "composition-nd-promotion-result-v1",
        "outcome": "fail",
        "commands": [
            {
                "argv": huge_argv,
                "exitCode": 2,
                "status": "fail",
                "timedOut": False,
                "failureReason": None,
                "stdout": {"preview": "stdout info"},
                "stderr": {"preview": "error: build derivation failed"},
            }
        ],
    }

    workflow = MockWorkflow(
        streams={
            "stdout": (json.dumps(qualifier_result), evidence_file, False),
            "stderr": ("", None, False),
        }
    )

    msg = diagnostics.failure_explanation(
        workflow, "qualifier", 2, entry_point="tools/qualify-promotion"
    )

    assert "secret-token-in-argv" not in msg
    assert "[REDACTED]" in msg
    assert "first recorded failing nested command: nix build" in msg
    assert "nested stderr: error: build derivation failed" in msg
    assert len(msg) < 2600


def test_empty_argv_and_no_diagnostic_streams(diagnostics):
    workflow = MockWorkflow(
        streams={
            "stdout": ("", None, False),
            "stderr": ("", None, False),
        }
    )

    msg = diagnostics.failure_explanation(workflow, "empty task", 1)

    assert "empty task failed (exit 1)" in msg
    assert "no diagnostic output" in msg
    assert "private evidence: unavailable" in msg


def test_both_streams_useful_reported(diagnostics, tmp_path):
    stdout_file = tmp_path / "0000.stdout"
    stdout_file.write_text("stdout details")
    stderr_file = tmp_path / "0000.stderr"
    stderr_file.write_text("stderr details")

    workflow = MockWorkflow(
        streams={
            "stdout": ("warning: output generated but unusable", stdout_file, False),
            "stderr": ("error: syntax validation failed", stderr_file, False),
        }
    )

    msg = diagnostics.failure_explanation(workflow, "check step", 1)

    assert "check step failed (exit 1)" in msg
    assert "stderr: error: syntax validation failed" in msg
    assert "stdout: warning: output generated but unusable" in msg
    assert f"private evidence: {stderr_file}" in msg
    assert f"additional stdout evidence: {stdout_file}" in msg
    assert len(msg) < 2600


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_promotion_qualification_diagnostics_contract_compatibility(
    diagnostics, tmp_path, stream
):
    evidence_path = tmp_path / f"0000.{stream}"
    diagnostic = "config/public.toml: unknown setting; token=secret-value\n" + "x" * 5000
    evidence_path.write_text(diagnostic)

    workflow = MockWorkflow(
        streams={
            "stdout": (diagnostic if stream == "stdout" else "", evidence_path if stream == "stdout" else None, False),
            "stderr": (diagnostic if stream == "stderr" else "", evidence_path if stream == "stderr" else None, False),
        }
    )

    message = diagnostics.failure_explanation(
        workflow, "candidate qualifier", 3, entry_point="tools/qualify-promotion"
    )

    assert "config/public.toml: unknown setting" in message
    assert "tools/qualify-promotion (exit 3)" in message
    assert "secret-value" not in message
    assert "[REDACTED]" in message
    assert "private evidence:" in message
    assert len(message) < 2600
    parsed_path = Path(message.split("private evidence: ")[1].strip())
    assert parsed_path.name.endswith(f".{stream}")
    assert parsed_path.is_file()
    assert parsed_path.stat().st_size > 0


@pytest.mark.parametrize("commands", [None, []])
def test_policy_result_accepts_omitted_commands(diagnostics, tmp_path, commands):
    doc = {"schema": "composition-nd-promotion-result-v1", "outcome": "fail",
           "failure": {"message": "policy rejected"},
           "checks": {"contracts": "fail", "tests": "not-run", "unknown": None,
                      "numeric": 1, "timing": "timeout", "evaluation": "error"}}
    if commands is not None:
        doc["commands"] = commands
    workflow = MockWorkflow(streams={"stdout": (json.dumps(doc), tmp_path / "stdout", False)})
    message = diagnostics.failure_explanation(workflow, "qualifier", 1)
    assert "contracts (fail)" in message and "policy rejected" in message
    assert "timing (timeout)" in message and "evaluation (error)" in message
    assert "unknown (" not in message and "numeric (" not in message
    assert "tests (not-run)" not in message and "first recorded" not in message


def test_structured_failure_preserves_checks_and_outer_stderr(diagnostics, tmp_path):
    doc = {"schema": "composition-nd-promotion-result-v1", "outcome": "fail",
           "checks": {"aggregate": "fail"}, "failure": {"message": "public check failed"},
           "commands": [{"argv": ["nix", "build", ".#opaque"], "exitCode": 1,
                         "stderr": {"preview": "nested assertion failed"}}]}
    workflow = MockWorkflow(streams={
        "stdout": (json.dumps(doc), tmp_path / "stdout", False),
        "stderr": ("Traceback: qualifier cleanup error", tmp_path / "stderr", False)})
    message = diagnostics.failure_explanation(workflow, "qualifier", 5)
    for detail in ("aggregate (fail)", "public check failed", "nested assertion failed",
                   "Traceback: qualifier cleanup error", "additional stderr evidence:"):
        assert detail in message
    assert len(message) < 2500 and message.endswith(str(tmp_path / "stdout"))


def test_cleanup_only_record_does_not_preempt_process_failure(diagnostics, tmp_path):
    clean = {"argv": ["metadata"], "status": "pass", "exitCode": 0,
             "cleanupDisposition": "incomplete"}
    failed = {"argv": ["build"], "exitCode": 7, "stderr": {"preview": "build error"}}
    doc = {"schema": "composition-nd-promotion-result-v1", "outcome": "fail", "commands": [clean, failed]}
    workflow = MockWorkflow(streams={"stdout": (json.dumps(doc), tmp_path / "stdout", False)})
    assert "first recorded failing nested command: build (exit 7)" in diagnostics.failure_explanation(workflow, "qualifier", 1)
    doc["commands"] = [clean]
    workflow.streams["stdout"] = (json.dumps(doc), tmp_path / "stdout", False)
    message = diagnostics.failure_explanation(workflow, "qualifier", 1)
    assert "cleanup incomplete" in message and "exit 0" in message
    assert "first recorded failing nested command" not in message


def test_retained_report_does_not_inherit_stream_annotations(diagnostics, tmp_path, monkeypatch):
    report = importlib.import_module("nix_darwin_local.report_diagnostics")
    monkeypatch.setattr(report, "retain_report_diagnostic", lambda *a: ("report assertion failed", tmp_path / "diagnostic"))
    workflow = MockWorkflow(streams={"stdout": ("partial", None, True)})
    message = diagnostics.failure_explanation(workflow, "qualification", 7, report_root=tmp_path)
    assert "report assertion failed" in message and message.endswith(str(tmp_path / "diagnostic"))
    assert "truncated or unavailable" not in message and "some command evidence unavailable" not in message
