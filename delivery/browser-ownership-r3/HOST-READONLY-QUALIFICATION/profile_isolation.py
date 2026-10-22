#!/usr/bin/env python3
"""Compare operator-supplied data-root topology without reading their contents."""
import argparse
import json
from pathlib import Path


def check(paths):
    roots = [Path(path).resolve(strict=True) for path in paths]
    if not all(root.is_dir() for root in roots):
        return False
    for index, left in enumerate(roots):
        for right in roots[index + 1:]:
            if left.samefile(right) or left in right.parents or right in left.parents:
                return False
    return len(roots) == 3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs=3, help="automation, daily and YouTube data roots; metadata only")
    args = parser.parse_args()
    try:
        result = check(args.roots)
    except (OSError, RuntimeError):
        result = False
    print(json.dumps({"status": "DISJOINT_ROOTS" if result else "ISOLATION_HELD", "profileContentsRead": False}))
    return 0 if result else 78


if __name__ == "__main__":
    raise SystemExit(main())
