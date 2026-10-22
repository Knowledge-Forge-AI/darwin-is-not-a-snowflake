# nix-darwin-test-source-target: libexec/nix_darwin_local/projection.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/shell_generation.py
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def local_modules(repo_root: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    paths = importlib.import_module("nix_darwin_local.paths")
    projection = importlib.import_module("nix_darwin_local.projection")
    shell_generation = importlib.import_module("nix_darwin_local.shell_generation")
    return paths, projection, shell_generation


def test_projection_copies_only_allowlisted_dotted_keys(local_modules, tmp_path: Path):
    _, projection, _ = local_modules
    control = tmp_path / "config"
    control.mkdir()
    (control / "module.toml").write_text(
        '[public]\nname = "visible"\n[private]\ntoken = "SECRET_SENTINEL"\n',
        encoding="utf-8",
    )

    result = projection.render_evaluation(
        control,
        tmp_path / "eval",
        {"module.toml": ["public.name"]},
    )

    rendered = (result.generation / "module.toml").read_text(encoding="utf-8")
    assert "visible" in rendered
    assert "SECRET_SENTINEL" not in rendered
    assert (result.generation / "flake.nix").is_file()
    assert (result.generation / "paths.json").is_file()
    assert result.current.resolve() == result.generation


def test_absent_kitty_file_reaches_admitted_consumer_default(local_modules, tmp_path: Path):
    _, projection, _ = local_modules
    control = tmp_path.resolve() / "control"
    control.mkdir()
    allowlist = {"kitty-bash.toml": ["schema", "palette", "terminal", "completion", "ansi?", "ble.faces?"]}
    result = projection.projected_files(control, allowlist)
    assert "kitty-bash.toml" not in result
    assert "flake.nix" in result


@pytest.mark.parametrize("invalid", ["unexpected = 1\n", "[ble]\nother = 1\n"])
def test_kitty_projection_cannot_hide_unknown_keys(local_modules, tmp_path: Path, invalid):
    paths, projection, _ = local_modules
    control = tmp_path.resolve() / "control"
    control.mkdir()
    (control / "kitty-bash.toml").write_text(
        "schema=1\n" + (invalid if not invalid.startswith("[") else "")
        + "[palette]\n[terminal]\n[completion]\n"
        + (invalid if invalid.startswith("[") else "")
    )
    with pytest.raises(paths.ConfigError, match="unknown Kitty top-level key|only \\[ble.faces\\]"):
        projection.projected_files(control, {"kitty-bash.toml": ["schema", "palette", "terminal", "completion", "ansi?", "ble.faces?"]})


def test_projection_emits_paths_json_schema(local_modules, tmp_path: Path):
    _, projection, _ = local_modules
    control = tmp_path / "config"
    control.mkdir()

    result = projection.render_evaluation(control, tmp_path / "eval", {})
    paths_file = result.generation / "paths.json"
    assert paths_file.is_file()
    payload = json.loads(paths_file.read_text(encoding="utf-8"))
    assert payload["schema"] == "dinas-paths-v1"
    for key in ("workspace_root", "nbin_root", "nbin_dev_root", "ai_index_root", "dinas_root"):
        assert key in payload


def test_projection_rejects_unknown_files_keys_and_control_toml(local_modules, tmp_path: Path):
    paths, projection, _ = local_modules
    control = tmp_path / "config"
    control.mkdir()
    (control / "module.toml").write_text('[public]\nname = "ok"\n', encoding="utf-8")
    (control / "extra.toml").write_text('value = "unexpected"\n', encoding="utf-8")

    with pytest.raises(paths.ConfigError, match="unknown TOML file"):
        projection.render_evaluation(control, tmp_path / "eval", {"module.toml": ["public.name"]})

    (control / "extra.toml").unlink()
    with pytest.raises(paths.ConfigError, match="allowlisted key"):
        projection.render_evaluation(control, tmp_path / "eval", {"module.toml": ["missing.key"]})

    (control / "nix-darwin.toml").write_text("[flake]\nactive = ''\n", encoding="utf-8")
    with pytest.raises(paths.ConfigError, match="control"):
        projection.render_evaluation(
            control,
            tmp_path / "eval",
            {"module.toml": ["public.name"], "nix-darwin.toml": ["flake.active"]},
        )


def test_projection_reuses_byte_identical_generation(local_modules, tmp_path: Path):
    _, projection, _ = local_modules
    control = tmp_path / "config"
    control.mkdir()
    (control / "module.toml").write_text('value = "same"\n', encoding="utf-8")

    first = projection.render_evaluation(control, tmp_path / "eval", {"module.toml": ["value"]})
    first_pointer_stat = os.lstat(first.current)
    second = projection.render_evaluation(control, tmp_path / "eval", {"module.toml": ["value"]})

    assert second.changed is False
    assert second.generation == first.generation
    assert os.lstat(second.current).st_ino == first_pointer_stat.st_ino


def test_projection_preserves_supported_toml_types_and_quotes_keys(local_modules, tmp_path: Path):
    _, projection, _ = local_modules
    control = tmp_path / "config"
    control.mkdir()
    (control / "module.toml").write_text(
        """
plain = 7
ratio = 1.5
enabled = true
values = [1, false, "two"]
"quoted key" = "quoted value"

[parent]
child = "nested"
""".lstrip(),
        encoding="utf-8",
    )

    result = projection.render_evaluation(
        control,
        tmp_path / "eval",
        {
            "module.toml": [
                "plain",
                "ratio",
                "enabled",
                "values",
                "quoted key",
                "parent.child",
            ],
        },
    )

    rendered = (result.generation / "module.toml").read_text(encoding="utf-8")
    assert '"quoted key" = "quoted value"' in rendered
    assert "enabled = true" in rendered
    assert "plain = 7" in rendered
    assert "ratio = 1.5" in rendered
    assert 'values = [1, false, "two"]' in rendered
    assert "[parent]" in rendered
    assert 'child = "nested"' in rendered


@pytest.mark.parametrize(
    ("allowlist", "message"),
    [
        ([], "filename mapping"),
        ({"../module.toml": ["value"]}, "invalid allowlist filename"),
        ({"module.toml": "value"}, "list of strings"),
        ({"module.toml": ["value", "value"]}, "duplicate key"),
        ({"module.toml": [""]}, "invalid allowlisted key"),
    ],
)
def test_projection_rejects_invalid_allowlist_contracts(
    local_modules,
    tmp_path: Path,
    allowlist,
    message: str,
):
    paths, projection, _ = local_modules
    control = tmp_path / "config"
    control.mkdir()
    (control / "module.toml").write_text('value = "ok"\n', encoding="utf-8")

    with pytest.raises(paths.ConfigError, match=message):
        projection.render_evaluation(control, tmp_path / "eval", allowlist)


def test_projection_rejects_tampered_immutable_generation(local_modules, tmp_path: Path):
    paths, projection, _ = local_modules
    control = tmp_path / "config"
    control.mkdir()
    (control / "module.toml").write_text('value = "same"\n', encoding="utf-8")
    allowlist = {"module.toml": ["value"]}

    first = projection.render_evaluation(control, tmp_path / "eval", allowlist)
    (first.generation / "unexpected.toml").write_text('value = "extra"\n', encoding="utf-8")
    with pytest.raises(paths.ConfigError, match="unexpected files"):
        projection.render_evaluation(control, tmp_path / "eval", allowlist)

    (first.generation / "unexpected.toml").unlink()
    (first.generation / "module.toml").write_text('value = "tampered"\n', encoding="utf-8")
    with pytest.raises(paths.ConfigError, match="content mismatch"):
        projection.render_evaluation(control, tmp_path / "eval", allowlist)


def test_projection_rejects_unsupported_toml_value_types(local_modules, tmp_path: Path):
    paths, projection, _ = local_modules
    control = tmp_path / "config"
    control.mkdir()
    (control / "module.toml").write_text("release_date = 2026-08-10\n", encoding="utf-8")

    with pytest.raises(paths.ConfigError, match="unsupported projected TOML value type: date"):
        projection.render_evaluation(
            control,
            tmp_path / "eval",
            {"module.toml": ["release_date"]},
        )


def test_reviewed_scratch_publication_and_pre_switch_pointer_restore(local_modules, tmp_path: Path):
    _, projection, _ = local_modules
    control = tmp_path / "control"
    control.mkdir()
    (control / "module.toml").write_text('value = "reviewed"\n')
    scratch = projection.render_scratch(control, tmp_path / "scratch", {"module.toml": ["value"]})
    eval_root = tmp_path / "eval"

    before = projection.snapshot_current(eval_root)
    assert before == projection.CurrentSnapshot(False, None)
    publication = projection.publish_scratch(scratch.root, eval_root)
    assert publication.current.resolve() == publication.generation
    published = projection.snapshot_current(eval_root)
    assert published.exists is True
    projection.restore_current(eval_root, before, published)
    assert not (eval_root / "current").exists()


def test_reviewed_pointer_restore_preserves_existing_relative_link(local_modules, tmp_path: Path):
    _, projection, _ = local_modules
    eval_root = tmp_path / "eval"
    (eval_root / "generations/old").mkdir(parents=True)
    eval_root.chmod(0o700)
    (eval_root / "generations").chmod(0o700)
    (eval_root / "generations/old").chmod(0o700)
    (eval_root / "current").symlink_to("generations/old")
    before = projection.snapshot_current(eval_root)
    control = tmp_path / "control"
    control.mkdir()
    (control / "module.toml").write_text('value = "new"\n')
    scratch = projection.render_scratch(control, tmp_path / "scratch", {"module.toml": ["value"]})

    projection.publish_scratch(scratch.root, eval_root)
    published = projection.snapshot_current(eval_root)
    projection.restore_current(eval_root, before, published)
    assert (eval_root / "current").readlink() == Path("generations/old")


@pytest.mark.parametrize("defect", ["missing-flake", "symlink", "current-file", "invalid-snapshot"])
def test_scratch_publication_and_restore_topology_fail_closed(local_modules, tmp_path: Path, defect):
    paths, projection, _ = local_modules
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "flake.nix").write_text("{}\n")
    eval_root = tmp_path / "eval"
    if defect == "missing-flake":
        (scratch / "flake.nix").unlink()
        (scratch / "other").write_text("x")
        function = lambda: projection.publish_scratch(scratch, eval_root)
    elif defect == "symlink":
        (scratch / "link").symlink_to(scratch / "flake.nix")
        function = lambda: projection.publish_scratch(scratch, eval_root)
    elif defect == "current-file":
        eval_root.mkdir()
        (eval_root / "current").write_text("not-link")
        function = lambda: projection.snapshot_current(eval_root)
    else:
        eval_root.mkdir()
        function = lambda: projection.restore_current(
            eval_root, projection.CurrentSnapshot(True, None), projection.CurrentSnapshot(False, None)
        )
    with pytest.raises(paths.ConfigError):
        function()


def test_reviewed_pointer_restore_refuses_concurrent_drift(local_modules, tmp_path: Path):
    paths, projection, _ = local_modules
    control = tmp_path / "control"
    control.mkdir()
    (control / "module.toml").write_text('value = "new"\n')
    scratch = projection.render_scratch(control, tmp_path / "scratch", {"module.toml": ["value"]})
    eval_root = tmp_path / "eval"
    before = projection.snapshot_current(eval_root)
    projection.publish_scratch(scratch.root, eval_root)
    published = projection.snapshot_current(eval_root)
    (eval_root / "current").unlink()
    (eval_root / "current").symlink_to("foreign")

    with pytest.raises(paths.ConfigError, match="drift prevents restoration"):
        projection.restore_current(eval_root, before, published)


def test_postrename_publication_fsync_failure_remains_restorable(local_modules, tmp_path: Path, monkeypatch):
    _, projection, _ = local_modules
    control = tmp_path / "control"
    control.mkdir()
    (control / "module.toml").write_text('value = "new"\n')
    scratch = projection.render_scratch(control, tmp_path / "scratch", {"module.toml": ["value"]})
    eval_root = tmp_path / "eval"
    before = projection.snapshot_current(eval_root)
    expected = projection.CurrentSnapshot(True, f"generations/{scratch.digest}")
    original_fsync = projection._fsync

    def fail_after_current_rename(candidate: Path):
        if candidate == eval_root:
            raise OSError("injected publication fsync failure")
        original_fsync(candidate)

    monkeypatch.setattr(projection, "_fsync", fail_after_current_rename)
    with pytest.raises(OSError, match="injected"):
        projection.publish_scratch(scratch.root, eval_root)
    assert projection.snapshot_current(eval_root) == expected
    monkeypatch.setattr(projection, "_fsync", original_fsync)
    projection.restore_current(eval_root, before, expected)
    assert projection.snapshot_current(eval_root) == before


def test_shell_generation_orders_fragments_and_rejects_duplicates(local_modules, tmp_path: Path):
    paths, _, shell_generation = local_modules
    generated = tmp_path / "generated"

    result = shell_generation.publish_shell_generation(
        generated,
        [
            shell_generation.ShellFragment(200, "beta", {"BETA": "/two"}),
            shell_generation.ShellFragment(100, "alpha", {"ALPHA": "/one"}),
        ],
    )

    assert [path.name for path in sorted(result.generation.glob("*.sh"))] == [
        "100_alpha.sh",
        "200_beta.sh",
    ]
    assert result.current.resolve() == result.generation

    with pytest.raises(paths.ConfigError, match="duplicate exported variable"):
        shell_generation.publish_shell_generation(
            generated,
            [
                shell_generation.ShellFragment(100, "a", {"SAME": "/one"}),
                shell_generation.ShellFragment(200, "b", {"SAME": "/two"}),
            ],
        )


@pytest.mark.parametrize(
    "fragments",
    [
        [(-1, "negative", {"VALUE": "/one"})],
        [(100, "first", {}), (100, "second", {})],
        [(100, "invalid module", {"VALUE": "/one"})],
        [(100, "module", {"INVALID-NAME": "/one"})],
    ],
    ids=["order-range", "duplicate-order", "module-name", "variable-name"],
)
def test_shell_generation_rejects_invalid_fragment_grammar(
    local_modules,
    tmp_path: Path,
    fragments,
):
    paths, _, shell_generation = local_modules
    specifications = [shell_generation.ShellFragment(*fragment) for fragment in fragments]

    with pytest.raises(paths.ConfigError):
        shell_generation.publish_shell_generation(tmp_path / "generated", specifications)


def test_shell_generation_serializes_spaces_literally(local_modules, tmp_path: Path):
    _, _, shell_generation = local_modules

    result = shell_generation.publish_shell_generation(
        tmp_path / "generated",
        [shell_generation.ShellFragment(100, "spaced", {"VALUE": "/a/path with spaces"})],
    )

    assert (result.generation / "100_spaced.sh").read_text(encoding="utf-8") == (
        "export VALUE='/a/path with spaces'\n"
    )


@pytest.mark.parametrize(
    "value",
    [
        "/a/'single-quote'",
        '/a/"double-quote"',
        "/a/$dollar",
        "/a/`backtick`",
        "/a/semicolon;command",
        "/a/newline\ncommand",
        "/a/$(command-substitution)",
        "/a/glob*?[abc]",
    ],
    ids=[
        "single-quote",
        "double-quote",
        "dollar",
        "backtick",
        "semicolon",
        "newline",
        "command-substitution",
        "glob",
    ],
)
def test_shell_generation_rejects_values_outside_path_grammar(
    local_modules,
    tmp_path: Path,
    value: str,
):
    paths, _, shell_generation = local_modules

    with pytest.raises(paths.ConfigError):
        shell_generation.publish_shell_generation(
            tmp_path / "generated",
            [shell_generation.ShellFragment(100, "unsafe", {"VALUE": value})],
        )


def test_config_cli_renders_eval_and_shell_generations(local_modules, tmp_path: Path, capsys):
    _, _, _ = local_modules
    cli = importlib.import_module("nix_darwin_local.cli")
    control = tmp_path / "config"
    control.mkdir()
    (control / "module.toml").write_text('value = "visible"\n', encoding="utf-8")
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(json.dumps({"module.toml": ["value"]}), encoding="utf-8")

    assert cli.main(
        [
            "render-eval",
            "--config-dir",
            str(control),
            "--eval-root",
            str(tmp_path / "eval"),
            "--allowlist-json",
            str(allowlist),
        ],
    ) == 0
    assert "eval/current" in capsys.readouterr().out

    fragments = tmp_path / "fragments.json"
    fragments.write_text(
        json.dumps([{"order": 100, "module": "core", "exports": {"VISIBLE": "/value"}}]),
        encoding="utf-8",
    )
    assert cli.main(
        [
            "render-shell",
            "--generated-root",
            str(tmp_path / "generated"),
            "--fragments-json",
            str(fragments),
        ],
    ) == 0
    assert "generated/shell/current" in capsys.readouterr().out


def test_config_cli_validates_control_config(local_modules, tmp_path: Path, capsys):
    cli = importlib.import_module("nix_darwin_local.cli")
    config = tmp_path / "nix-darwin.toml"
    config.write_text("[flake]\n", encoding="utf-8")

    assert cli.main(["validate", "--config", str(config)]) == 0
    assert capsys.readouterr().out == f"nix-darwin-config: valid: {config}\n"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [None],
        [{"order": True, "module": "core", "exports": {}}],
        [{"order": 100, "module": 7, "exports": {}}],
        [{"order": 100, "module": "core", "exports": {"VALUE": 7}}],
    ],
    ids=["not-list", "item-shape", "order-type", "module-type", "exports-type"],
)
def test_config_cli_translates_invalid_fragment_schema_to_config_exit(
    local_modules,
    tmp_path: Path,
    capsys,
    payload,
):
    cli = importlib.import_module("nix_darwin_local.cli")
    fragments = tmp_path / "fragments.json"
    fragments.write_text(json.dumps(payload), encoding="utf-8")

    assert cli.main(
        [
            "render-shell",
            "--generated-root",
            str(tmp_path / "generated"),
            "--fragments-json",
            str(fragments),
        ],
    ) == 78
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("nix-darwin-config: ")


def test_projection_handles_optional_keys_present_and_absent(local_modules, tmp_path: Path):
    _, projection, _ = local_modules
    control = tmp_path / "config"
    control.mkdir()
    (control / "kitty-bash.toml").write_text(
        """
schema = 1

[palette]
path = "#ffd700"

[terminal]
foreground = "#33ff00"

[completion]
style = "align-nowrap"

[ble.faces]
auto_complete = "fg=#89939f,italic"
""".lstrip(),
        encoding="utf-8",
    )

    # When optional keys (ble.faces?) are present and ansi? is absent
    allowlist = {
        "kitty-bash.toml": [
            "schema",
            "palette",
            "terminal",
            "completion",
            "ansi?",
            "ble.faces?",
        ]
    }
    result = projection.render_evaluation(control, tmp_path / "eval", allowlist)
    rendered = (result.generation / "kitty-bash.toml").read_text(encoding="utf-8")
    assert "schema = 1" in rendered
    assert "[palette]" in rendered
    assert 'path = "#ffd700"' in rendered
    assert "[ble.faces]" in rendered
    assert 'auto_complete = "fg=#89939f,italic"' in rendered
    assert "[ansi]" not in rendered

    # When ble is entirely absent
    (control / "kitty-bash.toml").write_text(
        """
schema = 1

[palette]
path = "#ffd700"

[terminal]
foreground = "#33ff00"

[completion]
style = "align-nowrap"
""".lstrip(),
        encoding="utf-8",
    )
    result2 = projection.render_evaluation(control, tmp_path / "eval2", allowlist)
    rendered2 = (result2.generation / "kitty-bash.toml").read_text(encoding="utf-8")
    assert "[ble]" not in rendered2
    assert "[ble.faces]" not in rendered2
    assert "[ansi]" not in rendered2
    assert "[palette]" in rendered2


def test_projection_rejects_scalar_parent_with_optional_key(local_modules, tmp_path: Path):
    paths, projection, _ = local_modules
    control = tmp_path / "config"
    control.mkdir()
    (control / "kitty-bash.toml").write_text(
        """
schema = 1
ble = "not-a-table"
""".lstrip(),
        encoding="utf-8",
    )
    allowlist = {"kitty-bash.toml": ["schema", "ble.faces?"]}
    with pytest.raises(paths.ConfigError, match="parent is not a table"):
        projection.render_evaluation(control, tmp_path / "eval", allowlist)


@pytest.mark.parametrize(
    ("allowlist", "message"),
    [
        ({"module.toml": ["value", "value?"]}, "duplicate key"),
        ({"module.toml": ["?"]}, "invalid allowlisted key"),
        ({"module.toml": ["valid?", "valid?"]}, "duplicate key"),
    ],
)
def test_projection_rejects_optional_key_contract_defects(local_modules, tmp_path: Path, allowlist, message):
    paths, projection, _ = local_modules
    control = tmp_path / "config"
    control.mkdir()
    (control / "module.toml").write_text('value = "ok"\n', encoding="utf-8")

    with pytest.raises(paths.ConfigError, match=message):
        projection.render_evaluation(control, tmp_path / "eval", allowlist)
