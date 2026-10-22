from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MODEL_MODULE_NAME = "_nix_darwin_test_report_model"


def load_sibling_model():
    if MODEL_MODULE_NAME in sys.modules:
        return sys.modules[MODEL_MODULE_NAME]

    model_path = Path(__file__).resolve().with_name("model.py")
    spec = spec_from_file_location(MODEL_MODULE_NAME, model_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load test report model from {model_path}")
    model = module_from_spec(spec)
    sys.modules[MODEL_MODULE_NAME] = model
    spec.loader.exec_module(model)
    return model


MODEL = load_sibling_model()
CoverageRecord = MODEL.CoverageRecord
TestCaseResult = MODEL.TestCaseResult
TreeNode = MODEL.TreeNode


def _insert_path(root: TreeNode, path: Path) -> TreeNode:
    node = root
    for part in path.parts:
        node = node.child(part)
    return node


def build_test_tree(tests: list[TestCaseResult]) -> TreeNode:
    root = TreeNode("nix-darwin", Path("nix-darwin"))
    for test in tests:
        node = _insert_path(root, test.test_path)
        node.tests.append(test)
    root.recalculate_totals()
    return root


def build_coverage_tree(records: list[CoverageRecord]) -> TreeNode:
    root = TreeNode("nix-darwin", Path("nix-darwin"))
    for record in records:
        node = _insert_path(root, record.source_path)
        node.coverage_records.append(record)
    return root
