# nix-darwin-test-source-target: bin/nix-update
# nix-darwin-test-source-target: libexec/nix_darwin_local/launcher.py
from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def launcher_modules(repo_root: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    paths = importlib.import_module("nix_darwin_local.paths")
    config = importlib.import_module("nix_darwin_local.config")
    launcher = importlib.import_module("nix_darwin_local.launcher")
    return paths, config, launcher


def make_flake(path: Path, with_tests: bool = False) -> Path:
    path.mkdir(parents=True)
    (path / "flake.nix").write_text("{ outputs = { self }: { }; }\n", encoding="utf-8")
    if with_tests:
        (path / "bin").mkdir()
        test = path / "bin/nix-test"
        test.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        test.chmod(0o755)
    return path


def test_selection_precedence_and_environment_is_diagnostic_only(
    launcher_modules,
    tmp_path: Path,
):
    _, _, launcher = launcher_modules
    core = make_flake(tmp_path / "core")
    configured = make_flake(tmp_path / "configured")
    explicit = make_flake(tmp_path / "explicit")
    config_path = tmp_path / "nix-darwin.toml"
    config_path.write_text(f'[flake]\nactive = "{configured}"\n', encoding="utf-8")

    selection = launcher.resolve_selection(
        core,
        str(explicit),
        config_path,
        {"NIX_DARWIN_ACTIVE_FLAKE": str(tmp_path / "stale")},
        tmp_path,
    )

    assert selection.path == explicit
    assert selection.source == "--flake"
    assert "NIX_DARWIN_ACTIVE_FLAKE" in "\n".join(selection.warnings)


def test_absent_and_explicit_empty_toml_select_core(launcher_modules, tmp_path: Path):
    _, _, launcher = launcher_modules
    core = make_flake(tmp_path / "core")
    missing = tmp_path / "missing.toml"

    assert launcher.resolve_selection(core, None, missing, {}, tmp_path).path == core

    empty = tmp_path / "nix-darwin.toml"
    empty.write_text("[flake]\nactive = \"\"\n", encoding="utf-8")
    assert launcher.resolve_selection(core, None, empty, {}, tmp_path).path == core


@pytest.mark.parametrize("text", ["not toml = [", '[flake]\nactive = "relative"\n'])
def test_invalid_toml_never_falls_back_to_core(launcher_modules, tmp_path: Path, text: str):
    paths, _, launcher = launcher_modules
    core = make_flake(tmp_path / "core")
    config_path = tmp_path / "nix-darwin.toml"
    config_path.write_text(text, encoding="utf-8")

    with pytest.raises(paths.ConfigError):
        launcher.resolve_selection(core, None, config_path, {}, tmp_path)


def test_missing_selected_flake_fails_closed(launcher_modules, tmp_path: Path):
    paths, _, launcher = launcher_modules
    core = make_flake(tmp_path / "core")
    config_path = tmp_path / "nix-darwin.toml"
    config_path.write_text(f'[flake]\nactive = "{tmp_path / "missing"}"\n', encoding="utf-8")

    with pytest.raises(paths.ConfigError, match="selected flake"):
        launcher.resolve_selection(core, None, config_path, {}, tmp_path)


def selection_for(launcher, source: str, path: Path):
    return launcher.Selection(path, source, None, ())


def test_implicit_core_fallback_is_refused_for_activating_operations(
    launcher_modules,
    tmp_path: Path,
):
    paths, _, launcher = launcher_modules
    implicit_core = selection_for(launcher, "core", tmp_path / "core")

    with pytest.raises(paths.ConfigError, match="no activating top-level flake selected"):
        launcher.require_activating_selection(implicit_core, False)


@pytest.mark.parametrize(
    ("source", "build_only"),
    [
        ("core", True),
        ("--flake", False),
        ("flake.active", False),
        ("--flake", True),
        ("flake.active", True),
    ],
)
def test_explicit_selection_and_build_only_stay_allowed(
    launcher_modules,
    tmp_path: Path,
    source: str,
    build_only: bool,
):
    _, _, launcher = launcher_modules

    launcher.require_activating_selection(selection_for(launcher, source, tmp_path), build_only)


def test_override_arguments_are_sorted_literal_and_require_checkout_shape(
    launcher_modules,
    tmp_path: Path,
):
    paths, config_module, launcher = launcher_modules
    first = make_flake(tmp_path / "first checkout")
    second = tmp_path / "source checkout"
    second.mkdir()
    (second / ".git").mkdir()
    config = config_module.ControlConfig(
        overrides=(("a", first), ("z/nested", second)),
        present=True,
    )

    assert launcher.override_arguments(config, None, False, True) == [
        "--override-input",
        "a",
        f"path:{first}",
        "--override-input",
        "z/nested",
        f"path:{second}",
    ]

    missing = config_module.ControlConfig(overrides=(("a", tmp_path / "missing"),), present=True)
    with pytest.raises(paths.ConfigError, match="checkout"):
        launcher.override_arguments(missing, None, False, True)


def test_core_override_requires_build_only_explicit_opt_in(launcher_modules, tmp_path: Path):
    paths, config_module, launcher = launcher_modules
    core = make_flake(tmp_path / "core checkout")
    config = config_module.ControlConfig(overrides=(("composition/core", core),), present=True)

    with pytest.raises(paths.ConfigError, match="--allow-core-override"):
        launcher.override_arguments(config, None, False, True)
    with pytest.raises(paths.ConfigError, match="build-only"):
        launcher.override_arguments(config, None, True, False)

    args = launcher.override_arguments(config, None, True, True)
    assert args[-2:] == ["composition/core", f"path:{core}"]


def test_local_config_override_is_reserved_and_uses_real_generation(
    launcher_modules,
    tmp_path: Path,
):
    paths, config_module, launcher = launcher_modules
    generation = make_flake(tmp_path / "eval/generations/one")
    config = config_module.ControlConfig(present=True)

    assert launcher.override_arguments(config, generation, False, True) == [
        "--override-input",
        "localConfig",
        f"path:{generation}",
    ]

    conflict = config_module.ControlConfig(
        overrides=(("localConfig", make_flake(tmp_path / "other")),),
        present=True,
    )
    with pytest.raises(paths.ConfigError, match="reserved"):
        launcher.override_arguments(conflict, generation, False, True)


def test_selected_flake_owns_test_command(launcher_modules, tmp_path: Path):
    _, _, launcher = launcher_modules
    selected = make_flake(tmp_path / "selected", with_tests=True)

    assert launcher.selected_test_command(selected, "8") == [
        str(selected / "bin/nix-test"),
        "--report",
        "--jobs",
        "8",
    ]
    (selected / "bin/nix-test").unlink()
    assert launcher.selected_test_command(selected, "") == [
        "nix",
        "flake",
        "check",
        "path:.",
        "--no-update-lock-file",
        "--no-write-lock-file",
    ]


def test_legacy_build_only_maps_to_locked_build_subcommand(launcher_modules):
    _, _, launcher = launcher_modules

    migrated = launcher._migrate_legacy_argv(["--build-only", "--test-jobs", "8"])

    assert migrated == ["build", "--test-jobs", "8"]
    assert launcher._parser().parse_args(migrated).command == "build"


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--switch-only"], "--switch-only is disabled"),
        (["--preserve-predecessor"], "now mandatory"),
        (["prepare"], "legacy 'prepare' has moved to 'nix-update advanced prepare'"),
        (["apply", "plan"], "legacy 'apply' has moved to 'nix-update advanced apply'"),
    ],
)
def test_unsafe_legacy_activation_paths_fail_closed(launcher_modules, argv, message):
    paths, _, launcher = launcher_modules

    with pytest.raises(paths.ConfigError, match=message):
        launcher._migrate_legacy_argv(argv)


def test_default_argv_maps_to_deploy(launcher_modules):
    _, _, launcher = launcher_modules
    assert launcher._migrate_legacy_argv([]) == ["deploy"]
    assert launcher._migrate_legacy_argv(["--dry-run"]) == ["deploy", "--dry-run"]


def test_parser_separates_prepare_apply_lock_and_optimize(launcher_modules, tmp_path: Path):
    _, _, launcher = launcher_modules

    deploy_args = launcher._parser().parse_args(["deploy", "--dry-run"])
    assert deploy_args.command == "deploy"
    assert deploy_args.dry_run is True

    adv_prep = launcher._parser().parse_args(
        ["advanced", "prepare", "--contract", str(tmp_path / "contract.json"), "--output", str(tmp_path / "plan")]
    )
    assert adv_prep.command == "advanced"
    assert adv_prep.advanced_command == "prepare"

    adv_apply = launcher._parser().parse_args(["advanced", "apply", str(tmp_path / "plan")])
    assert adv_apply.command == "advanced"
    assert adv_apply.advanced_command == "apply"
    assert launcher._parser().parse_args(
        ["lock", "--mode", "all", "--output", str(tmp_path / "candidate")]
    ).command == "lock"
    promotion = launcher._parser().parse_args([
        "promote", "prepare", "--flake", str(tmp_path / "selected"),
        "--input", "domain-adapter", "--revision", "a" * 40,
        "--output", str(tmp_path / "promotion"),
    ])
    assert (promotion.command, promotion.promotion_command) == ("promote", "prepare")
    promotion_apply = launcher._parser().parse_args(["promote", "apply", str(tmp_path / "promotion")])
    assert (promotion_apply.command, promotion_apply.promotion_command) == ("promote", "apply")
    with pytest.raises(SystemExit):
        launcher._parser().parse_args([
            "promote", "prepare", "--flake", str(tmp_path / "selected"),
            "--input", "domain-adapter", "--revision", "a" * 40,
            "--output", str(tmp_path / "promotion-with-override"),
            "--allow-node", "unrelated",
        ])
    assert launcher._parser().parse_args(["optimize", "--dry-run"]).dry_run is True


@pytest.mark.parametrize("timeout", ["0", "3601", "not-a-number"])
def test_promotion_timeout_rejects_invalid_values_before_output_creation(
    launcher_modules, tmp_path: Path, timeout: str
):
    _, _, launcher = launcher_modules
    output = tmp_path / "promotion"

    with pytest.raises(SystemExit):
        launcher._parser().parse_args([
            "promote", "prepare", "--flake", str(tmp_path / "selected"),
            "--input", "domain-adapter", "--revision", "a" * 40,
            "--output", str(output), "--timeout-seconds", timeout,
        ])

    assert not output.exists()


def test_production_environment_has_no_test_or_confirmation_bypass(launcher_modules):
    _, _, launcher = launcher_modules
    help_text = launcher._parser().format_help()

    assert "NIX_DARWIN_UPDATE_TEST_COMMAND" not in help_text
    assert "NIX_DARWIN_SKIP_UPDATE_TESTS" not in help_text
    assert "confirmation bypass" in help_text


def test_launcher_job_environment_local_root_and_host_fallback_branches(launcher_modules, tmp_path: Path, monkeypatch):
    paths, _, launcher = launcher_modules
    launcher._validate_environment_jobs("2", "3")
    with pytest.raises(paths.ConfigError, match="NIX_DARWIN_BUILD_JOBS"):
        launcher._validate_environment_jobs("bad", "")
    with pytest.raises(paths.ConfigError, match="NIX_DARWIN_TEST_JOBS"):
        launcher._validate_environment_jobs("", "0")
    assert launcher._local_root({}, tmp_path) == tmp_path / ".local/nix-darwin"
    assert launcher._local_root({"NIX_DARWIN_LOCAL": str(tmp_path / "local")}, tmp_path) == tmp_path / "local"
    assert launcher._darwin_config({"NIX_DARWIN_CONFIG": "ExplicitHost"}) == "ExplicitHost"

    results = iter([
        type("Result", (), {"returncode": 1, "stdout": ""})(),
        type("Result", (), {"returncode": 0, "stdout": "FallbackHost\n"})(),
    ])
    monkeypatch.setattr(launcher.subprocess, "run", lambda *_args, **_kwargs: next(results))
    assert launcher._darwin_config({}) == "FallbackHost"
    monkeypatch.setattr(launcher.subprocess, "run", lambda *_args, **_kwargs: type("Result", (), {"returncode": 1, "stdout": ""})())
    with pytest.raises(paths.ConfigError, match="unable to determine"):
        launcher._darwin_config({})


def test_launcher_run_dispatches_closed_subcommands(launcher_modules, tmp_path: Path, monkeypatch):
    _, config_module, launcher = launcher_modules
    selected = make_flake(tmp_path / "selected")
    selection = launcher.Selection(selected, "--flake", config_module.ControlConfig(), ())
    monkeypatch.setattr(launcher, "operator_home", lambda: tmp_path)
    monkeypatch.setattr(launcher, "resolve_selection", lambda *_args: selection)
    monkeypatch.setattr(launcher, "_darwin_config", lambda _env: "Host")
    monkeypatch.setattr(launcher, "build_only", lambda *_args: 11)
    monkeypatch.setattr(launcher, "maintain_lock", lambda *_args: 12)
    dummy = type("Artifact", (), {"document": {"status": "READY_FOR_REVIEW", "blockers": []}, "directory": tmp_path / "plan"})()
    monkeypatch.setattr(launcher, "prepare", lambda *_args: dummy)
    monkeypatch.setattr(launcher, "apply", lambda *_args: 13)
    promotion_calls = []
    monkeypatch.setattr(
        launcher,
        "prepare_promotion",
        lambda *args: promotion_calls.append(args) or dummy,
    )
    monkeypatch.setattr(launcher, "apply_promotion", lambda *_args: 14)

    assert launcher._run(tmp_path, launcher._parser().parse_args(["build", "--flake", str(selected)]), {}) == 11
    assert launcher._run(tmp_path, launcher._parser().parse_args(["lock", "--flake", str(selected), "--mode", "all", "--output", str(tmp_path / "lock")]), {}) == 12
    assert launcher._run(tmp_path, launcher._parser().parse_args(["advanced", "prepare", "--flake", str(selected), "--contract", str(tmp_path / "contract"), "--output", str(tmp_path / "plan")]), {}) == 0
    assert launcher._run(tmp_path, launcher._parser().parse_args(["advanced", "apply", str(tmp_path / "plan")]), {}) == 13
    deploy_workflows = []
    monkeypatch.setattr(
        launcher,
        "deploy_local",
        lambda workflow, *_args, **_kwargs: deploy_workflows.append(workflow) or 15,
    )
    assert launcher._run(tmp_path, launcher._parser().parse_args(["deploy"]), {}) == 15
    assert deploy_workflows[0].display_command_output is False
    assert launcher._run(tmp_path, launcher._parser().parse_args([
        "promote", "prepare", "--flake", str(selected), "--input", "domain-adapter",
        "--revision", "a" * 40, "--output", str(tmp_path / "promotion"),
        "--timeout-seconds", "120",
    ]), {}) == 0
    assert len(promotion_calls) == 1
    assert promotion_calls[0][5] == 120
    assert type(promotion_calls[0][5]) is int
    transition_calls = []
    monkeypatch.setattr(
        launcher, "prepare_promotion",
        lambda *args, **kwargs: transition_calls.append((args, kwargs)) or dummy,
    )
    transition_argv = [
        "promote", "prepare", "--flake", str(selected), "--input", "core",
        "--revision", "a" * 40, "--output", str(tmp_path / "transition"),
    ]
    assert launcher._parser().parse_args(transition_argv).allow_core_repository_transition is False
    transition_args = launcher._parser().parse_args(
        [*transition_argv, "--allow-core-repository-transition"]
    )
    assert transition_args.allow_core_repository_transition is True
    assert launcher._run(tmp_path, transition_args, {}) == 0
    assert transition_calls[0][1] == {"allow_core_repository_transition": True}

    comp_transition_calls = []
    monkeypatch.setattr(
        launcher, "prepare_promotion",
        lambda *args, **kwargs: comp_transition_calls.append((args, kwargs)) or dummy,
    )
    comp_transition_argv = [
        "promote", "prepare", "--flake", str(selected), "--input", "composition-nd",
        "--revision", "a" * 40, "--output", str(tmp_path / "comp-transition"),
    ]
    assert launcher._parser().parse_args(comp_transition_argv).allow_composition_repository_transition is False
    comp_transition_args = launcher._parser().parse_args(
        [*comp_transition_argv, "--allow-composition-repository-transition"]
    )
    assert comp_transition_args.allow_composition_repository_transition is True
    assert launcher._run(tmp_path, comp_transition_args, {}) == 0
    assert comp_transition_calls[0][1] == {"allow_composition_repository_transition": True}

    assert launcher._run(
        tmp_path, launcher._parser().parse_args(["promote", "apply", str(tmp_path / "promotion")]), {}
    ) == 14


@pytest.mark.parametrize("status,expected", [("DRY_RUN", 0), ("PUBLISHED", 0), ("PENDING", 1), ("INDETERMINATE", 1), ("UNAVAILABLE", 1)])
def test_explicit_publication_routes_catalog_and_reports_outcome(
    launcher_modules, tmp_path, monkeypatch, capsys, status, expected,
):
    from types import SimpleNamespace
    from nix_darwin_local import source_publication
    _, _, launcher = launcher_modules
    catalog = ("fixture source authority",)
    workflow = SimpleNamespace(sources=catalog)
    monkeypatch.setattr(launcher, "_configured_workflow", lambda *_args: workflow)
    calls = []
    def publish(actual_workflow, sources, selections, *, dry_run):
        calls.append((actual_workflow, sources, selections, dry_run))
        return {"status": status, "no_activation": True}
    monkeypatch.setattr(source_publication, "publish_sources", publish)
    argv = ["promote", "publish", "--source", "composition", "--revision", "a" * 40]
    if status == "DRY_RUN": argv.append("--dry-run")
    assert launcher._run(tmp_path, launcher._parser().parse_args(argv), {}) == expected
    assert calls == [(workflow, catalog, [("composition", "a" * 40)], status == "DRY_RUN")]
    assert status in capsys.readouterr().out


def test_launcher_optimize_and_blocked_prepare_results(launcher_modules, tmp_path: Path, monkeypatch):
    _, config_module, launcher = launcher_modules
    calls = []
    monkeypatch.setattr(launcher.subprocess, "run", lambda argv, **_kwargs: calls.append(argv) or type("Result", (), {"returncode": 9})())
    args = launcher._parser().parse_args(["optimize", "--dry-run", "--keep", "4"])
    assert launcher._run(tmp_path, args, {}) == 9
    assert calls == [[str(tmp_path / "bin/nix-opt"), "--dry-run", "--keep", "4"]]

    selected = make_flake(tmp_path / "selected")
    selection = launcher.Selection(selected, "--flake", config_module.ControlConfig(), ("diagnostic",))
    monkeypatch.setattr(launcher, "operator_home", lambda: tmp_path)
    monkeypatch.setattr(launcher, "resolve_selection", lambda *_args: selection)
    monkeypatch.setattr(launcher, "_darwin_config", lambda _env: "Host")
    blocked = type("Artifact", (), {"document": {"status": "BLOCKED", "blockers": ["one"]}, "directory": tmp_path / "plan"})()
    monkeypatch.setattr(launcher, "prepare", lambda *_args: blocked)
    args = launcher._parser().parse_args(["advanced", "prepare", "--flake", str(selected), "--contract", str(tmp_path / "contract"), "--output", str(tmp_path / "plan")])
    assert launcher._run(tmp_path, args, {}) == 1


def test_main_translates_config_errors_and_reports_timing(launcher_modules, tmp_path: Path, monkeypatch, capsys):
    _, _, launcher = launcher_modules
    monkeypatch.setattr(launcher, "_run", lambda *_args: (_ for _ in ()).throw(launcher.ConfigError("blocked")))
    assert launcher.main(tmp_path, ["build", "--timing"]) == 78
    captured = capsys.readouterr()
    assert "blocked" in captured.err
    assert "timing: total" in captured.out

    with pytest.raises(launcher.ConfigError, match="cannot be combined"):
        launcher._migrate_legacy_argv(["build", "--build-only"])


def test_campaign_parser_defaults_and_literal_output(launcher_modules, tmp_path):
    _, _, launcher = launcher_modules
    parser = launcher._parser()
    args = parser.parse_args(["promote", "campaign", "run", "--input", "domain", "--revision", "a" * 40])
    assert args.output is None and args.flake is None and not args.dry_run
    output = tmp_path / "campaign with spaces"
    args = parser.parse_args(["promote", "campaign", "run", "--input", "core", "--revision", "b" * 40, "--dry-run", "--output", str(output)])
    assert args.output == output and args.dry_run
    for operation in ("status", "resume"):
        args = parser.parse_args(["promote", "campaign", operation])
        assert args.campaign is None
    with pytest.raises(SystemExit):
        parser.parse_args(["promote", "campaign", "run", "--input", "core", "--revision", "a" * 40, "--yes"])
