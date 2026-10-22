from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .paths import ConfigError, validate_portable_path
from .projection import PublicationResult, _publish_generation


MODULE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ShellFragment:
    order: int
    module: str
    exports: dict[str, str]


def publish_shell_generation(
    generated_root: Path,
    fragments: list[ShellFragment],
) -> PublicationResult:
    orders: set[int] = set()
    variables: set[str] = set()
    files: dict[str, bytes] = {}
    for fragment in sorted(fragments, key=lambda item: (item.order, item.module)):
        if fragment.order < 0 or fragment.order > 999:
            raise ConfigError("shell fragment order must be between 000 and 999")
        if fragment.order in orders:
            raise ConfigError(f"duplicate shell fragment order: {fragment.order:03d}")
        if not MODULE_NAME.fullmatch(fragment.module):
            raise ConfigError(f"invalid shell fragment module: {fragment.module!r}")
        orders.add(fragment.order)
        lines: list[str] = []
        for name, value in sorted(fragment.exports.items()):
            if not VARIABLE_NAME.fullmatch(name):
                raise ConfigError(f"invalid exported variable name: {name!r}")
            if name in variables:
                raise ConfigError(f"duplicate exported variable: {name}")
            validate_portable_path(value)
            variables.add(name)
            lines.append(f"export {name}='{value}'")
        filename = f"{fragment.order:03d}_{fragment.module}.sh"
        files[filename] = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    return _publish_generation(generated_root / "shell", files)
