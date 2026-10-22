from __future__ import annotations

import struct
from pathlib import Path


def png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    assert header[12:16] == b"IHDR"
    return struct.unpack(">II", header[16:24])


def test_icon_generator_writes_valid_png_at_requested_size(load_module, tmp_path: Path):
    module = load_module("libexec/brave-youtube/generate-icon.py")
    output = tmp_path / "icon_16x16.png"

    module.write_png(output, 16)

    assert png_size(output) == (16, 16)


def test_icon_generator_main_writes_expected_iconset_files(load_module, monkeypatch, tmp_path: Path):
    module = load_module("libexec/brave-youtube/generate-icon.py")
    monkeypatch.setattr(module.sys, "argv", ["brave-youtube-generate-icon.py", str(tmp_path)])

    module.main()

    assert png_size(tmp_path / "icon_16x16.png") == (16, 16)
    assert png_size(tmp_path / "icon_512x512@2x.png") == (1024, 1024)


def test_theme_asset_generator_writes_valid_png_with_requested_dimensions(load_module, tmp_path: Path):
    module = load_module("libexec/brave-youtube/generate-theme-assets.py")
    output = tmp_path / "theme.png"

    module.write_png(output, 3, 2, lambda x, y, width, height: module.RED)

    assert png_size(output) == (3, 2)


def test_theme_asset_generator_main_writes_chrome_theme_assets(load_module, monkeypatch, tmp_path: Path):
    module = load_module("libexec/brave-youtube/generate-theme-assets.py")
    monkeypatch.setattr(module.sys, "argv", ["brave-youtube-generate-theme-assets.py", str(tmp_path)])

    module.main()

    assert png_size(tmp_path / "theme_frame.png") == (3000, 220)
    assert png_size(tmp_path / "theme_toolbar.png") == (3000, 120)
    assert png_size(tmp_path / "theme_ntp_background.png") == (1600, 1000)
