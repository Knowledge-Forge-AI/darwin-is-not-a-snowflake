"""Retired root-cutover command; operational execution is permanently disabled."""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

RETIRED_DIAGNOSTIC = (
    "dinas-root-cutover: error: dinas-root-cutover is retired; the checkout "
    "relocation and repository transition are complete and operational "
    "cutover is refused."
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dinas-root-cutover",
        description=(
            "Retired checkout relocation command. The canonical DINAS root "
            "layout is complete; operational cutover is retired."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["-h"], ["--help"]):
        try:
            parse_args(args)
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 0
        return 0

    print(RETIRED_DIAGNOSTIC, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
