from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from allowlist import AllowlistRow, check_allowlist_file, validate_value


@dataclass(frozen=True)
class SnapshotProblem:
    name: str
    message: str


@dataclass(frozen=True)
class SnapshotResult:
    ok: bool
    changed: bool
    entries: list[str]
    skipped_missing: list[str]
    problems: list[SnapshotProblem]
    output_path: Path
    metadata_path: Path | None


def default_allowlist_path(mode: str) -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    filename = "codex-sync-allowlist.tsv" if mode == "sync" else "project-env-allowlist.tsv"
    return repo_root / "config" / "codex-env" / filename


def detect_shell(env: Mapping[str, str]) -> str | None:
    if env.get("ZSH_VERSION"):
        return "zsh"
    if env.get("BASH_VERSION"):
        return "bash"

    shell_name = Path(env.get("SHELL", "")).name
    return shell_name if shell_name in {"bash", "zsh"} else None


def default_output_path(shell: str, env: Mapping[str, str] | None = None) -> Path:
    runtime_env = os.environ if env is None else env
    home = Path(runtime_env.get("HOME", str(Path.home())))
    return home / ".codex" / "runtime" / "shell-env" / f"latest.{shell}.env"


def write_snapshot(
    *,
    allowlist_path: str | Path,
    output_path: str | Path,
    shell: str,
    context: str,
    env: Mapping[str, str] | None = None,
    write_metadata: bool,
) -> SnapshotResult:
    snapshot_env = os.environ if env is None else env
    output = Path(output_path)
    metadata_path = output.with_suffix(output.suffix + ".metadata.json") if write_metadata else None
    allowlist_result = check_allowlist_file(allowlist_path)

    if not allowlist_result.ok:
        problems = [
            SnapshotProblem(error.name, f"{error.path}:{error.line}: {error.message}")
            for error in allowlist_result.errors
        ]
        return SnapshotResult(False, False, [], [], problems, output, metadata_path)

    entries: list[str] = []
    skipped_missing: list[str] = []
    problems: list[SnapshotProblem] = []
    export_lines: list[str] = []

    for row in allowlist_result.rows:
        value = snapshot_env.get(row.name)
        if value is None or value == "":
            if row.required:
                problems.append(SnapshotProblem(row.name, "required environment variable is missing"))
            else:
                skipped_missing.append(row.name)
            continue

        validation_error = validate_value(value, row.validator, row.max_bytes)
        if validation_error is not None:
            problems.append(SnapshotProblem(row.name, validation_error))
            continue

        entries.append(row.name)
        export_lines.append(_format_export(row, value))

    if problems:
        return SnapshotResult(False, False, entries, skipped_missing, problems, output, metadata_path)

    content = _render_snapshot(
        context=context,
        shell=shell,
        allowlist_path=Path(allowlist_path),
        export_lines=export_lines,
    )
    content_changed = _write_private_file_if_changed(output, content)
    changed = content_changed

    if metadata_path is not None:
        if content_changed or not metadata_path.exists():
            metadata = _metadata_payload(
                allowlist_path=Path(allowlist_path),
                output_path=output,
                shell=shell,
                context=context,
                entries=entries,
                skipped_missing=skipped_missing,
                problems=problems,
                content=content,
            )
            metadata_content = json.dumps(metadata, indent=2, sort_keys=True) + "\n"
            metadata_changed = _write_private_file_if_changed(metadata_path, metadata_content)
            changed = changed or metadata_changed

    return SnapshotResult(True, changed, entries, skipped_missing, problems, output, metadata_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write allowlisted shell environment snapshots.",
    )
    parser.add_argument("--mode", choices=["env-file", "sync"], default="env-file")
    parser.add_argument("--shell", choices=["auto", "bash", "zsh"], default="auto")
    parser.add_argument("--context", default="")
    parser.add_argument("--allowlist", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--metadata", action=argparse.BooleanOptionalAction, default=None)
    args = parser.parse_args(argv)

    shell = detect_shell(os.environ) if args.shell == "auto" else args.shell
    if shell is None:
        print("env-snapshot: unable to detect current shell", file=sys.stderr)
        return 1

    context = args.context.strip()
    if not context:
        if args.mode == "sync":
            context = "global"
        else:
            print("env-snapshot: --context is required in env-file mode", file=sys.stderr)
            return 1

    allowlist_path = args.allowlist or default_allowlist_path(args.mode)
    output_path = args.output
    if output_path is None:
        if args.mode == "sync":
            output_path = default_output_path(shell, os.environ)
        else:
            print("env-snapshot: --output is required in env-file mode", file=sys.stderr)
            return 1

    write_metadata = args.metadata if args.metadata is not None else args.mode == "sync"
    result = write_snapshot(
        allowlist_path=allowlist_path,
        output_path=output_path,
        shell=shell,
        context=context,
        env=os.environ,
        write_metadata=write_metadata,
    )

    if not result.ok:
        for problem in result.problems:
            print(f"env-snapshot: {problem.name}: {problem.message}", file=sys.stderr)
        return 1

    return 0


def _format_export(row: AllowlistRow, value: str) -> str:
    return f"export {row.name}={shlex.quote(value)}"


def _render_snapshot(
    *,
    context: str,
    shell: str,
    allowlist_path: Path,
    export_lines: list[str],
) -> str:
    lines = [
        "# Generated by env-snapshot. Do not edit.",
        f"# context: {context}",
        f"# shell: {shell}",
        f"# allowlist: {allowlist_path}",
        "",
        *export_lines,
        "",
    ]
    return "\n".join(lines)


def _write_private_file_if_changed(path: Path, content: str) -> bool:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)

    if path.exists() and path.read_text(encoding="utf-8") == content:
        path.chmod(0o600)
        return False

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
    ) as temp_file:
        temp_file.write(content)
        temp_path = Path(temp_file.name)

    temp_path.chmod(0o600)
    temp_path.replace(path)
    return True


def _metadata_payload(
    *,
    allowlist_path: Path,
    output_path: Path,
    shell: str,
    context: str,
    entries: list[str],
    skipped_missing: list[str],
    problems: list[SnapshotProblem],
    content: str,
) -> dict[str, object]:
    return {
        "allowlist_path": str(allowlist_path),
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "context": context,
        "entries": entries,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "mode": "sync",
        "output_path": str(output_path),
        "rejected": [asdict(problem) for problem in problems],
        "shell": shell,
        "skipped_missing": skipped_missing,
    }


if __name__ == "__main__":
    raise SystemExit(main())
