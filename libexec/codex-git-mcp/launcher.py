from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

from server import build_uvx_command


Execvp = Callable[[str, list[str]], None]


def main(argv: list[str] | None = None, *, execvp: Execvp = os.execvp) -> int:
    args = sys.argv[1:] if argv is None else argv
    if any(arg in {"-h", "--help"} for arg in args):
        from server import main as server_main

        return server_main(args)

    server_path = Path(__file__).resolve().with_name("server.py")
    command = build_uvx_command(server_path, args)
    execvp(command[0], command)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
