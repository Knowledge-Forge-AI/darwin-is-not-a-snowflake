from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any, Iterable, Sequence


def normalize_allowed_roots(allowed_roots: Iterable[str | Path]) -> tuple[Path, ...]:
    roots = tuple(Path(root).expanduser().resolve() for root in allowed_roots)
    if not roots:
        raise ValueError("codex-git-mcp requires at least one approved repository")
    return roots


def validate_repo_path(repo_path: str | Path, allowed_roots: Sequence[str | Path]) -> None:
    roots = normalize_allowed_roots(allowed_roots)
    resolved_repo = Path(repo_path).expanduser().resolve()

    for allowed_root in roots:
        try:
            resolved_repo.relative_to(allowed_root)
            return
        except ValueError:
            continue

    approved = ", ".join(str(root) for root in roots)
    raise ValueError(
        f"Repository path '{repo_path}' is outside the approved Git MCP repositories: {approved}",
    )


def install_upstream_validator_patch(upstream: Any, allowed_roots: Sequence[str | Path]) -> None:
    roots = normalize_allowed_roots(allowed_roots)

    def allowlisted_validate_repo_path(
        repo_path: str | Path,
        _allowed_repository: str | Path | None,
    ) -> None:
        validate_repo_path(repo_path, roots)

    upstream.validate_repo_path = allowlisted_validate_repo_path


def build_uvx_command(server_path: str | Path, argv: Sequence[str]) -> list[str]:
    return [
        "uvx",
        "--from",
        "mcp-server-git",
        "--with",
        "mcp<2",
        "python",
        str(server_path),
        *argv,
    ]


async def serve(allowed_roots: Sequence[str | Path]) -> None:
    from mcp_server_git import server as upstream

    roots = normalize_allowed_roots(allowed_roots)
    install_upstream_validator_patch(upstream, roots)
    await upstream.serve(None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run mcp-server-git with a Codex-managed repository allowlist.",
    )
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        required=True,
        metavar="PATH",
        help="Approved Git repository root. Repeat for each repository agents may use.",
    )
    args = parser.parse_args(argv)

    asyncio.run(serve(args.allow))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
