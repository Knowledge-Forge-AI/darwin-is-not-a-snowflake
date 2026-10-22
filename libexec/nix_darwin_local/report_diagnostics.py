from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any

from .command_diagnostics import sanitize_text
from .deployment_paths import require_private_directory, fsync_directory
from .deployment_workflow import _write_bounded_stream
from .paths import ConfigError

__all__ = ["retain_report_diagnostic"]

MAX_TSV_BYTES = 1024 * 1024  # 1 MiB max for failures.tsv and steps.tsv
MAX_TSV_ROWS = 10000
MAX_OUTPUT_FILE_BYTES = 10 * 1024 * 1024  # 10 MiB limit for runner output.txt
MAX_EXCERPT_CHARS = 1400
STEP_DIR_PATTERN = re.compile(r"^[0-9]{3,}-[a-z0-9-]+$")


def _validate_safe_directory(path: Path) -> bool:
    try:
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            return False
        if st.st_uid != os.getuid():
            return False
        return True
    except (OSError, ValueError):
        return False


def _open_safe_file(path: Path, max_bytes: int) -> tuple[int, os.stat_result] | None:
    try:
        if path.is_symlink():
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
    except (OSError, ValueError):
        return None

    try:
        st = os.fstat(descriptor)
        if not stat.S_ISREG(st.st_mode):
            os.close(descriptor)
            return None
        if st.st_nlink != 1:
            os.close(descriptor)
            return None
        if st.st_uid != os.getuid():
            os.close(descriptor)
            return None
        if st.st_size > max_bytes:
            os.close(descriptor)
            return None
        return descriptor, st
    except (OSError, ValueError):
        try:
            os.close(descriptor)
        except OSError:
            pass
        return None


def _read_bounded_tsv(
    path: Path, required_headers: tuple[str, ...]
) -> list[dict[str, str]] | None:
    opened = _open_safe_file(path, MAX_TSV_BYTES)
    if opened is None:
        return None
    descriptor, st = opened
    try:
        raw = os.read(descriptor, st.st_size + 1)
        if len(raw) != st.st_size:
            return None
        text = raw.decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    finally:
        os.close(descriptor)

    lines = text.splitlines()
    if not lines:
        return None

    header_line = lines[0].split("\t")
    if tuple(header_line) != required_headers:
        return None

    header_indices = {h: header_line.index(h) for h in required_headers}
    num_columns = len(header_line)

    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != num_columns:
            return None
        row = {}
        for h, idx in header_indices.items():
            val = parts[idx]
            if val == "":
                return None
            row[h] = val
        rows.append(row)
        if len(rows) > MAX_TSV_ROWS:
            return None

    return rows


def _report_location(workflow: Any, report_root: Path) -> Path | None:
    argv = workflow.records[-1].argv
    if argv.count("--report-root") != 1 or argv[argv.index("--report-root") + 1] != str(report_root):
        return None
    if not _validate_safe_directory(report_root):
        return None
    require_private_directory(report_root, "qualification report root")
    report_dir = report_root / "all" / "latest"
    if not all(_validate_safe_directory(path) for path in (report_root / "all", report_dir, report_dir / "runner")):
        return None
    return report_dir


def _first_failure(report_dir: Path) -> tuple[str, int, str | None] | None:
    runner_dir = report_dir / "runner"
    failures = _read_bounded_tsv(runner_dir / "failures.tsv", ("sequence", "label", "exit_code"))
    if not failures:
        return None
    first = failures[0]
    exit_code = int(first["exit_code"])
    if int(first["sequence"]) != 1 or not 1 <= exit_code <= 255:
        return None
    output = None
    steps_file = runner_dir / "steps.tsv"
    if steps_file.is_file():
        steps = _read_bounded_tsv(steps_file, ("ordinal", "label", "status", "exit_code", "output_log"))
        if steps is None:
            return None
        for step in steps:
            if step["label"] == first["label"] and step["status"] == "fail" and step["exit_code"] == first["exit_code"]:
                output = step["output_log"]
                break
    return first["label"], exit_code, output


def _runner_excerpt(report_dir: Path, relative: str) -> str | None:
    path = Path(relative)
    if (path.is_absolute() or ".." in path.parts or len(path.parts) != 3
            or path.parts[0] != "runner" or path.parts[2] != "output.txt"
            or not STEP_DIR_PATTERN.fullmatch(path.parts[1])):
        return None
    (report_dir / path).resolve().relative_to(report_dir.resolve())
    if not _validate_safe_directory(report_dir / path.parent):
        return None
    opened = _open_safe_file(report_dir / path, MAX_OUTPUT_FILE_BYTES)
    if opened is None:
        return None
    descriptor, metadata = opened
    with os.fdopen(descriptor, "rb") as source:
        raw = source.read(metadata.st_size + 1)
    if len(raw) != metadata.st_size:
        return None
    # Complete bounded read and redaction precede presentation truncation.
    return sanitize_text(raw.decode("utf-8", errors="replace")).strip()


def _report_message(label: str, status: int, excerpt: str) -> str:
    safe_label = sanitize_text(label).replace("\n", " ").replace("\t", " ")
    if len(safe_label) > 200:
        safe_label = safe_label[:180] + " [truncated]"
    message = f"first recorded failure: {safe_label} (exit {status})"
    if not excerpt:
        return message + "; no recorded child output"
    budget = MAX_EXCERPT_CHARS - len(message) - 40
    if len(excerpt) > budget:
        excerpt = "[truncated excerpt]\n" + excerpt[-budget:]
    return message + ":\n" + excerpt


def retain_report_diagnostic(workflow: Any, label: str, report_root: Path | str | None) -> tuple[str, Path] | None:
    """Retain only the first recorded failure from this invocation's core report."""
    if not getattr(workflow, "records", None) or workflow.records[-1].label != label:
        raise ConfigError(f"report diagnostic is not bound to {label}")
    if workflow.records[-1].exit_status == 0 or report_root is None:
        return None
    try:
        report_dir = _report_location(workflow, Path(report_root))
        if report_dir is None:
            return None
        failure = _first_failure(report_dir)
        if failure is None:
            return None
        failure_label, status, output = failure
        excerpt = _runner_excerpt(report_dir, output) if output is not None else ""
        if excerpt is None:
            return None
        message = _report_message(failure_label, status, excerpt)
        directory = workflow._evidence_directory()
        path = directory / f"{len(workflow.records) - 1:04d}.diagnostic"
        _write_bounded_stream(path, message)
        fsync_directory(directory)
        return message, path
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return None
