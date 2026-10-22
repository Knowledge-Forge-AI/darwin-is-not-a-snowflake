from __future__ import annotations

import csv
from pathlib import Path


HOST_SAFE_SCOPES = {"unit", "int"}


def manifest_rows(repo_root: Path) -> list[tuple[str, str, str]]:
    manifest_path = repo_root / "src/test/coverage-manifest.tsv"
    with manifest_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [
            (row["scope"], row["language"], row["source_path"])
            for row in reader
        ]


def manifest_entries(repo_root: Path) -> set[tuple[str, str, str]]:
    return set(manifest_rows(repo_root))


def source_language(source_path: Path) -> str:
    if source_path.name == "Vagrantfile" or source_path.suffix == ".rb":
        return "ruby"
    if source_path.suffix == ".py":
        return "python"
    if source_path.suffix == ".awk":
        return "awk"
    if source_path.suffix == ".applescript":
        return "applescript"
    if source_path.suffix == ".nix":
        return "nix"

    first_line = source_path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines()[0]
    if "python" in first_line:
        return "python"
    if "ruby" in first_line:
        return "ruby"
    if "zsh" in first_line:
        return "zsh"
    if "bash" in first_line or first_line.startswith("#!/bin/sh"):
        return "bash"
    raise AssertionError(f"do not know manifest language for {source_path}")


def executable_sources(root: Path) -> list[Path]:
    resolved_root = root.resolve()
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.stat().st_mode & 0o111
        and path.resolve().is_relative_to(resolved_root)
    )


def test_executable_sources_excludes_externally_owned_symlinks(tmp_path: Path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    owned_command = source_root / "owned-command"
    owned_command.write_text("#!/bin/sh\n", encoding="utf-8")
    owned_command.chmod(0o755)

    external_command = tmp_path / "external-command"
    external_command.write_text("#!/bin/sh\n", encoding="utf-8")
    external_command.chmod(0o755)
    (source_root / "external-command").symlink_to(external_command)

    assert executable_sources(source_root) == [owned_command]


def required_user_entrypoint_sources(repo_root: Path) -> list[Path]:
    """Source files that are direct core user command entrypoints."""

    return [
        *executable_sources(repo_root / "bin"),
        *executable_sources(repo_root / "tools"),
    ]


def test_user_entrypoints_and_vm_guest_scripts_are_manifested_for_unit_and_int(repo_root: Path):
    entries = manifest_entries(repo_root)

    missing = []
    for source in required_user_entrypoint_sources(repo_root):
        relative_source = source.relative_to(repo_root).as_posix()
        language = source_language(source)
        for scope in sorted(HOST_SAFE_SCOPES):
            if (scope, language, relative_source) not in entries:
                missing.append((scope, language, relative_source))

    assert missing == [], "\n".join("\t".join(row) for row in missing)


def test_coverage_manifest_has_no_duplicate_rows(repo_root: Path):
    rows = manifest_rows(repo_root)

    duplicate_rows = sorted(row for row in set(rows) if rows.count(row) > 1)

    assert duplicate_rows == [], "\n".join("\t".join(row) for row in duplicate_rows)
