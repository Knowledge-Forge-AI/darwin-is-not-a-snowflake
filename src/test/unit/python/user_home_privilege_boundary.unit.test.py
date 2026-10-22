from __future__ import annotations

import json
import re
from pathlib import Path


CATEGORY_A = "USER_HOME_PRIVILEGED_MUTATION_NEEDS_HARDENING"
CATEGORY_B = "USER_HOME_MUTATION_ALREADY_UNPRIVILEGED"
CATEGORY_C = "READ_ONLY_USER_HOME"
CATEGORY_D = "SYSTEM_OWNED_OUT_OF_SCOPE"
CATEGORY_E = "NO_USER_HOME_SURFACE"

INITIAL_VULNERABLE_MODULES = {
    "modules/arq.nix",
    "modules/brave-youtube.nix",
    "modules/fastfetch.nix",
    "modules/kitty.nix",
    "modules/nvm.nix",
    "modules/oh-my-zsh.nix",
    "modules/pyenv.nix",
    "modules/rbenv.nix",
    "modules/sdkman.nix",
    "modules/shell.nix",
}

EXPECTED_WRITERS = {
    "modules/brave-youtube.nix": "modules/brave-youtube-app-writer.sh",
    "modules/fastfetch.nix": "modules/fastfetch-writer.sh",
    "modules/kitty.nix": "modules/kitty-app-writer.sh",
    "modules/nvm.nix": "modules/runtime-state-writer.sh",
    "modules/oh-my-zsh.nix": "modules/oh-my-zsh-writer.sh",
    "modules/pyenv.nix": "modules/runtime-state-writer.sh",
    "modules/rbenv.nix": "modules/runtime-state-writer.sh",
    "modules/sdkman.nix": "modules/sdkman-config-writer.sh",
    "modules/shell.nix": "modules/shell-writer.sh",
}

RETIRED_AFTER_DEVUX22 = {
    "modules/arq.nix",
    "modules/repo-map-manager.nix",
    "modules/repo-map-orchestrator.nix",
    "modules/repo-map-project-tools.nix",
    "modules/repo-map-runner.nix",
}

ADDED_AFTER_DEVUX22 = {
    "modules/deployment-effects.nix",
}

MUTATOR_LINE = re.compile(
    r"^\s*(?:/[A-Za-z0-9._+@%/{}$\"'-]+/)?"
    r"(?:mkdir|install|cp|chmod|chown|find|mv|rm|rmdir|ln|codesign|xattr|touch)\b"
)
USER_HOME_REFERENCE = re.compile(
    r"/Users/\$\{userName\}|\$user_home|\$\{?home\}?|"
    r"\$(?:state_root|state_dir|log_root|profile_dir|app_bundle|kitty_dir|"
    r"fastfetch_dir|sdkman_dir|nvm_dir|rbenv_root|pyenv_root)[}\"/]"
)


def inventory_path(repo_root: Path) -> Path:
    return (
        repo_root
        / "src/test/fixtures/user-home-boundary"
        / "classification.json"
    )


def load_inventory(repo_root: Path) -> dict[str, object]:
    return json.loads(inventory_path(repo_root).read_text(encoding="utf-8"))


def activation_body(source: str) -> str:
    match = re.search(
        r"system\.activationScripts\.postActivation\.text\s*=\s*(?:lib\.mkAfter\s+)?''(.*?)\n\s*'';",
        source,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def test_inventory_records_the_complete_devux22_entry_snapshot(repo_root: Path):
    inventory = load_inventory(repo_root)
    rows = inventory["modules"]
    modules_root = repo_root / "modules"

    actual_modules = {
        path.relative_to(repo_root).as_posix() for path in modules_root.glob("*.nix")
    }
    inventoried_modules = {row["module"] for row in rows}
    assert actual_modules <= inventoried_modules | ADDED_AFTER_DEVUX22
    assert len(rows) == inventory["entry"]["moduleCount"] == 28

    actual_definitions = {
        path.relative_to(repo_root).as_posix()
        for path in modules_root.glob("*.nix")
        if "system.activationScripts.postActivation.text"
        in path.read_text(encoding="utf-8")
    }
    inventoried_definitions = {
        row["module"] for row in rows if row["definesPostActivation"]
    }
    assert actual_definitions <= inventoried_definitions
    assert len(inventoried_definitions) == inventory["entry"]["postActivationDefinitionCount"] == 17
    assert RETIRED_AFTER_DEVUX22 <= inventoried_modules



def test_inventory_records_the_exact_entry_vulnerable_set_and_review_b_correction(repo_root: Path):
    rows = load_inventory(repo_root)["modules"]
    initial_a = {
        row["module"] for row in rows if row["initialCategory"] == CATEGORY_A
    }
    assert initial_a == INITIAL_VULNERABLE_MODULES

    orchestrator = next(
        row for row in rows if row["module"] == "modules/repo-map-orchestrator.nix"
    )
    assert orchestrator["initialCategory"] == CATEGORY_B
    assert orchestrator["finalCategory"] == CATEGORY_B
    assert any("/Library/" in surface for surface in orchestrator["surfaces"])
    assert any("target-user" in surface for surface in orchestrator["surfaces"])


EXPECTED_FINAL_CATEGORY_COUNTS = {
    CATEGORY_A: 0,
    CATEGORY_B: 15,
    CATEGORY_C: 0,
    CATEGORY_D: 2,
    CATEGORY_E: 11,
}


def test_final_inventory_has_no_category_a_or_uncategorized_definition(repo_root: Path):
    inventory = load_inventory(repo_root)
    rows = inventory["modules"]
    counts = {
        category: sum(row["finalCategory"] == category for row in rows)
        for category in (CATEGORY_A, CATEGORY_B, CATEGORY_C, CATEGORY_D, CATEGORY_E)
    }

    assert counts == EXPECTED_FINAL_CATEGORY_COUNTS
    assert counts == inventory["requiredFinal"]["categoryCounts"]
    assert counts[CATEGORY_A] == 0
    assert len(rows) == 28
    for row in rows:
        if row["module"] in INITIAL_VULNERABLE_MODULES:
            assert row["finalCategory"] == CATEGORY_B, row["module"]
    assert inventory["requiredFinal"]["UNCATEGORIZED_POSTACTIVATION_USER_HOME_MUTATORS"] == 0
    assert inventory["requiredFinal"]["CORE_PRIVILEGED_USER_HOME_MUTATOR_COUNT"] == 0


def test_every_former_category_a_wrapper_drops_privilege_before_home_mutation(repo_root: Path):
    for module, writer in EXPECTED_WRITERS.items():
        module_text = (repo_root / module).read_text(encoding="utf-8")
        body = activation_body(module_text)

        assert 'target_user=${lib.escapeShellArg primaryUser}' in body
        assert 'user_home=${lib.escapeShellArg userHome}' in body
        transition = '/usr/bin/sudo -n -H -u "$target_user" --'
        assert body.count(transition) == 1, module
        assert Path(writer).name in module_text, module
        assert (repo_root / writer).is_file(), writer

        before_transition = body.split(transition, 1)[0]
        assert not any(
            MUTATOR_LINE.search(line) for line in before_transition.splitlines()
        ), module
        assert not re.search(r"^\s*[A-Za-z_][A-Za-z0-9_]*\(\)\s*\{", body, re.MULTILINE), module


def test_path_aware_gate_rejects_pretransition_user_home_mutation_in_every_b_definition(repo_root: Path):
    rows = load_inventory(repo_root)["modules"]
    recognized_transitions = (
        "/usr/bin/sudo -n -H -u",
        "/usr/bin/sudo -u",
        "/bin/launchctl asuser",
    )

    violations: list[str] = []
    for row in rows:
        if not row["definesPostActivation"] or row["finalCategory"] != CATEGORY_B:
            continue
        module = row["module"]
        if module in RETIRED_AFTER_DEVUX22:
            continue
        module_path = repo_root / module
        assert module_path.is_file(), module
        body = activation_body(module_path.read_text(encoding="utf-8"))
        transition_offsets = [
            body.find(marker) for marker in recognized_transitions if marker in body
        ]
        assert transition_offsets, module
        before_transition = body[: min(transition_offsets)]
        for line_number, line in enumerate(before_transition.splitlines(), start=1):
            if MUTATOR_LINE.search(line) and USER_HOME_REFERENCE.search(line):
                violations.append(f"{module}:{line_number}:{line.strip()}")

    assert violations == []


def test_entry_snapshot_positive_and_negative_controls_remain_classified(repo_root: Path):
    rows = {row["module"]: row for row in load_inventory(repo_root)["modules"]}
    assert rows["modules/codex-state.nix"]["finalCategory"] == CATEGORY_B
    assert rows["modules/repo-map-runner.nix"]["finalCategory"] == CATEGORY_B
    assert rows["modules/browsers.nix"]["finalCategory"] == CATEGORY_D
    assert rows["modules/video-session.nix"]["finalCategory"] == CATEGORY_D
