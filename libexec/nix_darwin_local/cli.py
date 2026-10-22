from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import load_control_config
from .paths import ConfigError
from .projection import render_evaluation
from .shell_generation import ShellFragment, publish_shell_generation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate and render machine-local nix-darwin configuration.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate control TOML.")
    validate_parser.add_argument("--config", required=True, type=Path)

    eval_parser = subparsers.add_parser("render-eval", help="Render evaluation-safe TOML.")
    eval_parser.add_argument("--config-dir", required=True, type=Path)
    eval_parser.add_argument("--eval-root", required=True, type=Path)
    eval_parser.add_argument("--allowlist-json", required=True, type=Path)

    shell_parser = subparsers.add_parser("render-shell", help="Render generated shell fragments.")
    shell_parser.add_argument("--generated-root", required=True, type=Path)
    shell_parser.add_argument("--fragments-json", required=True, type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            load_control_config(args.config)
            print(f"nix-darwin-config: valid: {args.config}")
        elif args.command == "render-eval":
            allowlist = _load_json(args.allowlist_json)
            result = render_evaluation(args.config_dir, args.eval_root, allowlist)
            print(result.current)
        else:
            fragments = _parse_fragments(_load_json(args.fragments_json))
            result = publish_shell_generation(args.generated_root, fragments)
            print(result.current)
    except (ConfigError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"nix-darwin-config: {error}", file=sys.stderr)
        return 78
    return 0


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_fragments(payload: Any) -> list[ShellFragment]:
    if not isinstance(payload, list):
        raise ConfigError("shell fragment specification must be a list")
    fragments: list[ShellFragment] = []
    for item in payload:
        if not isinstance(item, dict) or set(item) != {"order", "module", "exports"}:
            raise ConfigError("shell fragment must contain order, module, and exports")
        order = item["order"]
        module = item["module"]
        exports = item["exports"]
        if not isinstance(order, int) or isinstance(order, bool):
            raise ConfigError("shell fragment order must be an integer")
        if not isinstance(module, str) or not isinstance(exports, dict):
            raise ConfigError("shell fragment module and exports have invalid types")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in exports.items()):
            raise ConfigError("shell fragment exports must map strings to strings")
        fragments.append(ShellFragment(order, module, exports))
    return fragments
