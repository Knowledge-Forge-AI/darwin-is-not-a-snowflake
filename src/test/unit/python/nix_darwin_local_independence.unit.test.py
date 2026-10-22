# nix-darwin-test-source-target: bin/nix-update
# nix-darwin-test-source-target: libexec/nix_darwin_local/repository_identity.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/deployment_workflow.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/deployment_plan.py
from __future__ import annotations

from pathlib import Path
import re

import pytest


FORBIDDEN_PATTERNS = [
    re.compile(r"\bagent[-_]central\b", re.IGNORECASE),
    re.compile(r"/ai/agent-central", re.IGNORECASE),
]

REQUIRED_PRODUCT_FILES = {
    Path("bin/nix-update"),
    Path("libexec/nix_darwin_local/deployment_plan.py"),
    Path("libexec/nix_darwin_local/deployment_workflow.py"),
}


def find_files_to_check(repo_root: Path) -> list[Path]:
    paths: list[Path] = []
    bin_update = repo_root / "bin" / "nix-update"
    if bin_update.exists():
        paths.append(bin_update)

    libexec = repo_root / "libexec" / "nix_darwin_local"
    if libexec.exists():
        paths.extend(p for p in libexec.glob("*.py") if p.is_file())

    return paths


def test_no_agent_central_coupling_in_core_tooling(repo_root: Path):
    files_to_check = find_files_to_check(repo_root)
    relative_files = {path.relative_to(repo_root) for path in files_to_check}
    assert REQUIRED_PRODUCT_FILES <= relative_files, (
        "Expected the launcher, deployment contract/schema, and deployment workflow "
        "to be included in the product independence scan"
    )

    violations: list[str] = []
    for file_path in files_to_check:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        for line_num, line in enumerate(content.splitlines(), start=1):
            for pattern in FORBIDDEN_PATTERNS:
                if pattern.search(line):
                    violations.append(f"{file_path.relative_to(repo_root)}:{line_num}: {line.strip()}")

    assert not violations, (
        f"Found forbidden agent-central references in core nix-update tooling:\n"
        + "\n".join(violations)
    )
