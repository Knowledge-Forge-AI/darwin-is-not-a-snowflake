#!/usr/bin/env python3
"""Render declared statement coverage details as HTML."""

from __future__ import annotations

import argparse
import csv
import html
from collections.abc import Sequence
from pathlib import Path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", required=True)
    parser.add_argument("--details-file", required=True, type=Path)
    parser.add_argument("--html-file", required=True, type=Path)
    parser.add_argument("--template-file", type=Path, default=script_dir / "statement-coverage.html")
    parser.add_argument("--row-template-file", type=Path, default=script_dir / "statement-coverage-row.html")
    return parser.parse_args(argv)


def replace_placeholders(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{ {key} }}}}", value)
    return rendered


def render_row(row_template: str, row: dict[str, str]) -> str:
    status = row["status"]
    return replace_placeholders(
        row_template,
        {
            "target": html.escape(row["target"]),
            "status_class": html.escape(status),
            "status": html.escape(status),
            "covered_statements": html.escape(row["covered_statements"]),
            "total_statements": html.escape(row["total_statements"]),
        },
    )


def render_statement_coverage(
    language: str,
    details_file: Path,
    *,
    template_file: Path,
    row_template_file: Path,
) -> str:
    template = template_file.read_text(encoding="utf-8")
    row_template = row_template_file.read_text(encoding="utf-8").rstrip("\n")
    with details_file.open(encoding="utf-8", newline="") as details:
        rows = "\n".join(render_row(row_template, row) for row in csv.DictReader(details, delimiter="\t"))

    return replace_placeholders(
        template,
        {
            "language": html.escape(language),
            "rows": rows,
        },
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.html_file.write_text(
        render_statement_coverage(
            args.language,
            args.details_file,
            template_file=args.template_file,
            row_template_file=args.row_template_file,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
