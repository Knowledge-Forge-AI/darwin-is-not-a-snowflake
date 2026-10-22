"""Bounded failure diagnostic explanation helper for command workflows."""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any, Mapping, Sequence

from .deployment_plan import redact_text

SAFE_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")

OSC_PATTERN = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|\Z)")
CSI_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
ESC_PATTERN = re.compile(r"\x1b[@-Z\\-_]|\x1b")
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")

SHELL_VAR_QUOTED = re.compile(
    r'(?i)\b(?P<prefix>(?:export[ \t]+)?[a-z0-9_]*(?:credential|password|secret|token|cookie|private[_-]?key|api[_-]?key|access[_-]?key)[a-z0-9_]*[ \t]*=[ \t]*)(?P<quote>["\'])(?:\\.|(?!(?P=quote)).)*(?:(?P=quote)|\Z)',
    re.DOTALL,
)
CLI_SECRET_FLAG = re.compile(
    r'(?i)(?P<prefix>(?:^|[ \t]))(?P<flag>(?:--|-)(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|password|secret|credential|private[_-]?key)[ \t]+)(?P<quote>["\']?)[^\s"\';]+(?P=quote)',
    re.MULTILINE,
)
CLI_SHORT_P = re.compile(
    r'(?i)(?P<prefix>(?:^|[ \t]))(?P<flag>-p[ \t]+)(?P<quote>["\']?)[^\s"\';]+(?P=quote)',
    re.MULTILINE,
)
URL_SECRET_PARAM = re.compile(
    r'(?i)(?P<prefix>[?&](?:api[_-]?key|access[_-]?token|token|password|secret|credential)=)[^&\s"\';]+'
)
SECRET_HEADER = re.compile(
    r'(?i)\b(?P<header>(?:proxy-)?authorization\s*:\s*(?:bearer|basic|token|digest)?\s*|private-token\s*:\s*|x-auth-token\s*:\s*)(?P<quote>["\']?)[^\s,"\';]+(?P=quote)'
)


def sanitize_text(text: str) -> str:
    """Sanitize terminal control codes and redact secrets before bounding/cropping."""
    if not text:
        return ""
    # Normalize carriage returns and strip ANSI / OSC / control codes
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = OSC_PATTERN.sub("", cleaned)
    cleaned = CSI_PATTERN.sub("", cleaned)
    cleaned = ESC_PATTERN.sub("", cleaned)
    cleaned = CONTROL_PATTERN.sub("", cleaned)
    # Raw/malformed JSON fallbacks must not expose command argument arrays.
    cleaned = re.sub(r'(?s)"argv"\s*:\s*\[(?:[^]"\\]|\\.|"(?:\\.|[^"\\])*")*(?:\]|\Z)',
                     '"argv": ["[REDACTED]"]', cleaned)
    cleaned = re.sub(
        r'''(?is)(--(?:password|token|secret|credential|api-key)\s+)(["'])(?:\\.|(?!\2).)*(?:\2|\Z)''',
        r"\1[REDACTED]", cleaned,
    )

    # Redact display-specific gaps: shell expressions, space-separated flags, query params, headers
    cleaned = SHELL_VAR_QUOTED.sub(r"\g<prefix>\g<quote>[REDACTED]\g<quote>", cleaned)
    cleaned = CLI_SECRET_FLAG.sub(r"\g<prefix>\g<flag>\g<quote>[REDACTED]\g<quote>", cleaned)
    cleaned = CLI_SHORT_P.sub(r"\g<prefix>\g<flag>\g<quote>[REDACTED]\g<quote>", cleaned)
    cleaned = URL_SECRET_PARAM.sub(r"\g<prefix>[REDACTED]", cleaned)
    cleaned = SECRET_HEADER.sub(r"\g<header>\g<quote>[REDACTED]\g<quote>", cleaned)

    # Header values can contain whitespace and punctuation (e.g. Digest/cookies).
    cleaned = re.sub(
        r"(?im)\b((?:proxy-)?authorization|cookie|set-cookie|x-api-key)\s*:[^\r\n]*",
        r"\1: [REDACTED]", cleaned,
    )
    # Apply the existing known-form plan redactor; this is not a secret detector.
    return redact_text(cleaned)


def _format_argv(argv: Sequence[str] | None) -> str:
    if not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(arg, str) for arg in argv):
        return "command"
    if Path(argv[0]).name in {"sh", "bash", "zsh", "fish", "python", "python3", "node", "ruby"}:
        return f"{sanitize_text(Path(argv[0]).name)} [arguments redacted]"
    sanitized: list[str] = []
    redact_next = 0
    for arg in argv:
        arg_str = str(arg)
        clean_arg = sanitize_text(arg_str)
        if redact_next:
            sanitized.append("[REDACTED]")
            redact_next -= 1
            continue
        lower = clean_arg.lower()
        if lower in (
            "--token",
            "--password",
            "-p",
            "--api-key",
            "--access-token",
            "--secret",
            "--credential",
            "--private-key",
            "--auth-token",
            "--expr", "-e", "--config", "--settings", "--header", "-h",
        ):
            sanitized.append(clean_arg)
            redact_next = 1
        elif lower in {"--arg", "--argstr", "--option"}:
            sanitized.append(clean_arg)
            redact_next = 2
        elif lower.startswith(("--expr=", "--config=", "--settings=", "--header=")):
            sanitized.append(clean_arg.split("=", 1)[0] + "=[REDACTED]")
        else:
            sanitized.append(clean_arg)

    if len(sanitized) > 25:
        sanitized = sanitized[:20] + [f"... ({len(sanitized) - 20} more args)"]
    formatted = shlex.join(sanitized)
    if len(formatted) > 500:
        formatted = formatted[:480] + " ..."
    return formatted


def _bound_text(text: str, max_chars: int = 1600) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    marker = "\n... [truncated] ...\n" if "\n" in text else " ... [truncated] ... "
    budget = max_chars - len(marker)
    if budget <= 0:
        return text[:max_chars]
    head_budget = budget // 2
    tail_budget = budget - head_budget
    head = text[:head_budget]
    tail = text[-tail_budget:]
    return f"{head}{marker}{tail}"


def _parse_qualifier_json(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    trimmed = text.strip()
    doc: Any = None
    if trimmed.startswith("{") and trimmed.endswith("}"):
        try:
            doc = json.loads(trimmed)
        except Exception:
            doc = None
    if not isinstance(doc, dict):
        return None
    schema = doc.get("schema")
    if not isinstance(schema, str) or not schema:
        return None
    if not SAFE_IDENTITY.fullmatch(schema):
        return None
    if ("commands" in doc and not isinstance(doc["commands"], list)) or doc.get("outcome") not in {"pass", "fail"}:
        return None
    return doc


def _find_first_failing_command(doc: Mapping[str, Any]) -> dict[str, Any] | None:
    commands = doc.get("commands")
    if not isinstance(commands, list):
        return None
    cleanup_only = None
    for cmd in commands:
        if isinstance(cmd, dict):
            status = cmd.get("status")
            exit_code = cmd.get("exitCode")
            timed_out = cmd.get("timedOut")
            failure_reason = cmd.get("failureReason")
            cleanup = cmd.get("cleanupDisposition")
            if (
                status == "fail"
                or (type(exit_code) is int and exit_code != 0)
                or timed_out is True
                or failure_reason is not None
            ):
                return cmd
            if cleanup_only is None and cleanup in ("incomplete", "uncontained"):
                cleanup_only = cmd
    return cleanup_only


def _format_failing_command(cmd: Mapping[str, Any]) -> str:
    argv_str = _format_argv(cmd.get("argv"))
    exit_code = cmd.get("exitCode")
    timed_out = cmd.get("timedOut")
    failure_reason = cmd.get("failureReason")
    timeout_sec = cmd.get("timeoutSeconds")

    if timed_out:
        status_str = f"timed out after {timeout_sec}s" if timeout_sec else "timed out"
    elif failure_reason:
        status_str = str(failure_reason)
    elif exit_code is not None:
        status_str = f"exit {exit_code}"
    else:
        status_str = "failed"

    cleanup = cmd.get("cleanupDisposition")
    cleanup_only = (cmd.get("status") != "fail" and exit_code == 0
                    and not timed_out and failure_reason is None
                    and cleanup in ("incomplete", "uncontained"))
    description = "first recorded command with cleanup failure" if cleanup_only else "first recorded failing nested command"
    parts: list[str] = [f"{description}: {argv_str} ({sanitize_text(status_str)})"]
    if cleanup in ("incomplete", "uncontained"):
        parts.append(f"cleanup {cleanup}")

    stderr_prev = (
        cmd.get("stderr", {}).get("preview", "")
        if isinstance(cmd.get("stderr"), dict)
        else ""
    )
    stdout_prev = (
        cmd.get("stdout", {}).get("preview", "")
        if isinstance(cmd.get("stdout"), dict)
        else ""
    )

    clean_err = sanitize_text(str(stderr_prev)).strip()
    clean_out = sanitize_text(str(stdout_prev)).strip()

    if clean_err:
        parts.append(f"nested stderr: {_bound_text(clean_err, 700)}")
    if clean_out:
        parts.append(f"nested stdout: {_bound_text(clean_out, 500)}")
    if any(isinstance(cmd.get(s), dict) and cmd[s].get("truncated") is True for s in ("stdout", "stderr")):
        parts.append("child preview truncated by qualifier")

    return "; ".join(parts)


def _extract_policy_failure(doc: Mapping[str, Any], *, child_present: bool = False) -> str:
    parts: list[str] = []

    # Check failure mapping
    failure = doc.get("failure")
    if isinstance(failure, dict):
        msg = failure.get("message")
        cat = failure.get("category")
        if msg:
            parts.append(sanitize_text(str(msg)))
        if cat and (not msg or str(cat) not in str(msg)):
            parts.append(f"category: {sanitize_text(str(cat))}")
    elif failure:
        parts.append(sanitize_text(str(failure)))

    # Collect failed checks
    failed_checks: list[str] = []

    def _collect_checks(node: Any, prefix: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                name = f"{prefix}.{k}" if prefix else str(k)
                if isinstance(v, dict):
                    _collect_checks(v, name)
                elif isinstance(v, str) and v in {"fail", "failed", "blocked", "error", "timeout"}:
                    failed_checks.append(f"{name} ({v})")
                elif v is False:
                    failed_checks.append(f"{name} ({v})")

    _collect_checks(doc.get("checks"))
    if failed_checks:
        parts.append(f"failed check(s): {sanitize_text(', '.join(failed_checks))}")

    # Check proof elements
    lock = doc.get("lock")
    if isinstance(lock, dict) and lock.get("byteIdentical") is False:
        parts.append("lock byte identity mismatch")

    tree = doc.get("tree")
    if isinstance(tree, dict) and tree.get("byteIdentical") is False:
        parts.append("tree byte identity mismatch")

    compat = doc.get("contractCompatibility")
    if isinstance(compat, dict) and compat.get("status") != "pass":
        parts.append("contract compatibility did not pass")

    candidate = doc.get("candidate")
    if isinstance(candidate, dict):
        if candidate.get("revMatched") is False:
            parts.append("candidate revision mismatched")
        if candidate.get("urlMatched") is False:
            parts.append("candidate URL mismatched")

    if not parts and not child_present:
        parts.append("qualification policy failure without child command")

    return "; ".join(parts)


def _read_stream(workflow: Any, label: str, stream: str) -> tuple[str, Path | None, bool]:
    try:
        return workflow.diagnostic_stream(label, stream)
    except Exception:
        # Only in-memory summaries from this invocation are an eligible fallback.
        try:
            record = workflow.records[-1]
            if record.label == label:
                summary = getattr(record, stream).get("summary", "")
                if isinstance(summary, str) and len(summary) <= 8192:
                    return summary, None, True
        except Exception:
            pass
        return "", None, True


def _stream_detail(stdout: str, stderr: str, *, structured: bool) -> tuple[str, str]:
    document = _parse_qualifier_json(stdout) if structured else None
    if document is not None:
        child = _find_first_failing_command(document)
        parts = []
        if child is not None:
            parts.append(_bound_text(_format_failing_command(child), 850))
        policy = _extract_policy_failure(document, child_present=child is not None)
        if policy:
            parts.append(_bound_text(policy, 300))
        if stderr.strip():
            parts.append("outer stderr: " + _bound_text(sanitize_text(stderr), 300))
        return "; ".join(parts), "stdout"
    clean_err, clean_out = sanitize_text(stderr).strip(), sanitize_text(stdout).strip()
    if clean_err and clean_out:
        return f"stderr: {_bound_text(clean_err, 800)}; stdout: {_bound_text(clean_out, 600)}", "stderr"
    if clean_err:
        return _bound_text(clean_err, 1400), "stderr"
    if clean_out:
        return _bound_text(clean_out, 1400), "stdout"
    return "no diagnostic output; child detail unknown", "stderr"


def _failure_explanation(
    workflow: Any, label: str, exit_status: int, entry_point: str | None = None,
    *, report_root: Path | None = None,
) -> str:
    """Display observed failure evidence; never authorize a result or run a command."""
    stdout, out_path, out_partial = _read_stream(workflow, label, "stdout")
    stderr, err_path, err_partial = _read_stream(workflow, label, "stderr")
    outcome = "result rejected" if exit_status == 0 else "failed"
    prefix = f"{_bound_text(sanitize_text(label), 160)} {outcome}"
    if entry_point:
        prefix += f": {_bound_text(sanitize_text(entry_point), 240)}"
    prefix += f" (exit {exit_status})"
    try:
        detail, stream = _stream_detail(stdout, stderr, structured=not out_partial)
    except Exception:
        # A malformed child document or a formatter bug cannot mask the operation.
        detail, stream = "diagnostic formatting unavailable; child detail unknown", "stdout" if out_path else "stderr"
    path = out_path if stream == "stdout" else err_path
    report_used = False
    if report_root is not None:
        try:
            from .report_diagnostics import retain_report_diagnostic
            retained = retain_report_diagnostic(workflow, label, report_root)
            if retained is not None:
                detail, path = retained
                report_used = True
        except Exception:
            detail += "; report diagnostic unavailable"
    if not report_used and (out_partial if stream == "stdout" else err_partial):
        detail += "; retained output truncated or unavailable"
    if not report_used and (out_path is None or err_path is None):
        detail += "; some command evidence unavailable"
    pointer = str(path) if path is not None else "unavailable"
    if len(pointer) > 600 or sanitize_text(pointer) != pointer or "\n" in pointer:
        pointer = "unavailable (path cannot be safely displayed)"
    additional = ""
    other_path = err_path if stream == "stdout" else out_path
    other_stream = "stderr" if stream == "stdout" else "stdout"
    if not report_used and stdout.strip() and stderr.strip() and other_path is not None:
        other = str(other_path)
        if len(other) <= 600 and sanitize_text(other) == other and "\n" not in other:
            additional = f"; additional {other_stream} evidence: {other}"
    # Reserve the complete evidence pointer within the journal's 2,048-character
    # exception-detail limit; truncating the assembled message can corrupt it.
    suffix = f"{additional}; private evidence: {pointer}"
    detail_limit = min(1400 - len(additional), 2048 - len(prefix) - 2 - len(suffix))
    # Sanitize the assembled detail before the final presentation bound.
    try:
        detail = _bound_text(sanitize_text(detail), detail_limit)
    except Exception:
        detail = "diagnostic formatting unavailable"
    return f"{prefix}: {detail}{suffix}"


def failure_explanation(workflow, label, exit_status, entry_point=None, *, report_root=None):
    try:
        return _failure_explanation(workflow, label, exit_status, entry_point, report_root=report_root)
    except Exception:
        # Last-resort display boundary: do not let formatting replace failure.
        safe_label = label if isinstance(label, str) and re.fullmatch(r"[A-Za-z0-9 /._-]{1,160}", label) else "command"
        pointer = "unavailable"
        for stream in ("stderr", "stdout"):
            _, path, _ = _read_stream(workflow, label, stream)
            if path is not None and re.fullmatch(r"/[A-Za-z0-9 /._-]{1,599}", str(path)):
                pointer = str(path)
                break
        outcome = "result rejected" if exit_status == 0 else "failed"
        return f"{safe_label} {outcome} (exit {exit_status}): diagnostic formatting unavailable; private evidence: {pointer}"
