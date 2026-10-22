from __future__ import annotations

import importlib.util
import itertools
import re
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest


_module_counter = itertools.count()


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


@pytest.fixture
def load_module(repo_root: Path):
    def _load_module(relative_path: str, module_name: str | None = None):
        path = repo_root / relative_path
        if module_name is None:
            safe_name = re.sub(r"[^0-9A-Za-z_]", "_", relative_path)
            module_name = f"nix_darwin_test_{next(_module_counter)}_{safe_name}"

        loader = SourceFileLoader(module_name, str(path))
        spec = importlib.util.spec_from_loader(module_name, loader)
        assert spec is not None
        assert spec.loader is not None

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    return _load_module
