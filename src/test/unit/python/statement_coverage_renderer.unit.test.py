from __future__ import annotations

import runpy
import sys
from pathlib import Path


def test_statement_coverage_renderer_escapes_values_and_uses_templates(load_module, repo_root: Path, tmp_path: Path):
    module = load_module("libexec/test-report/statement_coverage.py")
    details_file = tmp_path / "statement-coverage.tsv"
    html_file = tmp_path / "statement-coverage.html"
    details_file.write_text(
        "\n".join(
            [
                "target\tstatus\tcovered_statements\ttotal_statements",
                "libexec/proton-vpn/<parser>.awk\tcovered\t5\t5",
                "bin/bad&missing\tmissed\t0\t3",
                "",
            ],
        ),
        encoding="utf-8",
    )

    assert module.main(
        [
            "--language",
            "awk & AppleScript",
            "--details-file",
            str(details_file),
            "--html-file",
            str(html_file),
            "--template-file",
            str(repo_root / "libexec/test-report/statement-coverage.html"),
            "--row-template-file",
            str(repo_root / "libexec/test-report/statement-coverage-row.html"),
        ],
    ) == 0

    rendered = html_file.read_text(encoding="utf-8")
    assert "awk &amp; AppleScript Statement Coverage" in rendered
    assert "libexec/proton-vpn/&lt;parser&gt;.awk" in rendered
    assert "bin/bad&amp;missing" in rendered
    assert 'class="covered">covered' in rendered
    assert 'class="missed">missed' in rendered


def test_statement_coverage_renderer_cli_runs_main(repo_root: Path, monkeypatch, tmp_path: Path):
    details_file = tmp_path / "statement-coverage.tsv"
    html_file = tmp_path / "statement-coverage.html"
    details_file.write_text(
        "\n".join(
            [
                "target\tstatus\tcovered_statements\ttotal_statements",
                "libexec/example.awk\tcovered\t2\t2",
            ],
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "statement_coverage.py",
            "--language",
            "awk",
            "--details-file",
            str(details_file),
            "--html-file",
            str(html_file),
            "--template-file",
            str(repo_root / "libexec/test-report/statement-coverage.html"),
            "--row-template-file",
            str(repo_root / "libexec/test-report/statement-coverage-row.html"),
        ],
    )

    try:
        runpy.run_path(str(repo_root / "libexec/test-report/statement_coverage.py"), run_name="__main__")
    except SystemExit as error:
        assert error.code == 0

    assert "libexec/example.awk" in html_file.read_text(encoding="utf-8")
