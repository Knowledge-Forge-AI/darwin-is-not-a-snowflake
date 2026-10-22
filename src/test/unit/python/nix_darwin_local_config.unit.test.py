# nix-darwin-test-source-target: libexec/nix_darwin_local/config.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/paths.py
from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def local_modules(repo_root: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    paths = importlib.import_module("nix_darwin_local.paths")
    config = importlib.import_module("nix_darwin_local.config")
    return paths, config


@pytest.mark.parametrize(
    "value",
    [
        "/absolute/path",
        "/path with spaces/ユニコード",
        "~/home-relative/path",
        "$HOME/path",
    ],
)
def test_portable_paths_accept_closed_literal_forms(local_modules, value: str):
    paths, _ = local_modules

    assert paths.validate_portable_path(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "relative/path",
        ".",
        "..",
        "/a/../b",
        "/a/./b",
        "/repeated//separators",
        "${HOME}/path",
        "$VAR/path",
        "~",
        "~other/path",
        "/tmp/*",
        "/tmp/{a,b}",
        "/tmp/$(touch nope)",
        "/tmp/`touch nope`",
        "/tmp/trailing/",
        "/tmp/tab\there",
        "/tmp/newline\nhere",
    ],
)
def test_portable_paths_reject_expansion_and_traversal(local_modules, value: str):
    paths, _ = local_modules

    with pytest.raises(paths.ConfigError):
        paths.validate_portable_path(value)


def test_home_expands_once_from_supplied_operator_home(local_modules):
    paths, _ = local_modules

    assert paths.resolve_portable_path("~/a literal", Path("/Users/operator")) == (
        Path("/Users/operator/a literal")
    )


@pytest.mark.parametrize("value", ["a", "a/b", "module-name/input_1"])
def test_input_paths_accept_literal_nested_names(local_modules, value: str):
    paths, _ = local_modules

    assert paths.validate_input_path(value) == value


@pytest.mark.parametrize("value", ["", "/a", "a/", "a//b", "a/../b", "a b", "a;$x"])
def test_input_paths_reject_invalid_nested_names(local_modules, value: str):
    paths, _ = local_modules

    with pytest.raises(paths.ConfigError):
        paths.validate_input_path(value)


def test_absent_and_explicit_empty_config_select_core(local_modules, tmp_path: Path):
    _, config = local_modules
    missing = tmp_path / "missing.toml"

    assert config.load_control_config(missing).active is None

    explicit = tmp_path / "nix-darwin.toml"
    explicit.write_text('[flake]\nactive = ""\nupdate_first = []\n', encoding="utf-8")
    assert config.load_control_config(explicit).active is None


def test_valid_config_is_typed_sorted_and_expands_paths(local_modules, tmp_path: Path):
    _, config = local_modules
    path = tmp_path / "nix-darwin.toml"
    path.write_text(
        """
[flake]
active = "~/selected"
update_first = ["z/input", "a"]

[flake.overrides]
"z/input" = "~/z checkout"
a = "/absolute/a"

[modules]
enabled = ["example-nd"]
""".lstrip(),
        encoding="utf-8",
    )

    parsed = config.load_control_config(path, operator_home=Path("/Users/operator"))

    assert parsed.active == Path("/Users/operator/selected")
    assert parsed.update_first == ("a", "z/input")
    assert parsed.overrides == (
        ("a", Path("/absolute/a")),
        ("z/input", Path("/Users/operator/z checkout")),
    )
    assert parsed.enabled_modules == ("example-nd",)


@pytest.mark.parametrize(
    "text",
    [
        "not toml = [",
        "flake = []\n",
        '[flake]\nactive = "relative"\n',
        '[flake]\nactive = 7\n',
        '[flake]\noverrides = { a = 7 }\n',
        '[flake]\nunknown = true\n',
        '[unknown]\nvalue = true\n',
        '[modules]\nenabled = "example-nd"\n',
        '[flake]\nupdate_first = ["a", "a"]\n',
    ],
)
def test_invalid_or_unknown_config_fails_closed(local_modules, tmp_path: Path, text: str):
    paths, config = local_modules
    path = tmp_path / "nix-darwin.toml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(paths.ConfigError):
        config.load_control_config(path)


def test_unreadable_config_fails_closed(local_modules, tmp_path: Path, monkeypatch):
    paths, config = local_modules
    path = tmp_path / "nix-darwin.toml"
    path.write_text("[flake]\n", encoding="utf-8")

    def deny(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_bytes", deny)
    with pytest.raises(paths.ConfigError):
        config.load_control_config(path)


def test_paths_table_defaults_and_explicit_resolution(local_modules, tmp_path: Path):
    _, config = local_modules
    home = tmp_path / "user"
    home.mkdir()
    (home / ".local").mkdir()

    # Absent paths table populates defaults
    empty = tmp_path / "empty.toml"
    empty.write_text("[flake]\nactive = ''\n", encoding="utf-8")
    parsed_default = config.load_control_config(empty, operator_home=home)
    assert parsed_default.workspace_root == home / ".local/nix-darwin"
    assert parsed_default.nbin_root == home / ".local/nbin"
    assert parsed_default.nbin_dev_root == home / ".local/nbin_dev"
    assert parsed_default.ai_index_root == home / "ai"
    assert parsed_default.dinas_root == home / ".local/nix-darwin/darwin-is-not-a-snowflake"

    # Explicit paths table with $HOME/... resolution
    explicit = tmp_path / "explicit.toml"
    explicit.write_text(
        """
[paths]
workspace_root = "$HOME/.local/custom-ws"
nbin_root = "$HOME/.local/custom-nbin"
nbin_dev_root = "$HOME/.local/custom-nbin_dev"
ai_index_root = "$HOME/custom-ai"
dinas_root = "$HOME/.local/custom-ws/dinas"
""".lstrip(),
        encoding="utf-8",
    )
    parsed_explicit = config.load_control_config(explicit, operator_home=home)
    assert parsed_explicit.workspace_root == home / ".local/custom-ws"
    assert parsed_explicit.nbin_root == home / ".local/custom-nbin"
    assert parsed_explicit.nbin_dev_root == home / ".local/custom-nbin_dev"
    assert parsed_explicit.ai_index_root == home / "custom-ai"
    assert parsed_explicit.dinas_root == home / ".local/custom-ws/dinas"


@pytest.mark.parametrize(
    "text,expected_match",
    [
        ('[paths]\nunknown_key = "$HOME/.local"\n', "unknown paths key"),
        ('[paths]\nworkspace_root = 123\n', "must be a non-empty string"),
        ('[paths]\nworkspace_root = ""\n', "must be a non-empty string"),
        ('[paths]\nworkspace_root = "${HOME}/nope"\n', "forbidden expansion"),
        ('[paths]\nworkspace_root = "$HOME/../escape"\n', "forbidden segment"),
        ('[paths]\nworkspace_root = "/escaped/from/home"\n', "escapes its required root"),
        (
            '[paths]\nnbin_root = "$HOME/.local/nbin"\nnbin_dev_root = "$HOME/.local/nbin"\n',
            "distinct",
        ),
    ],
)
def test_paths_table_invalid_inputs_fail_closed(
    local_modules, tmp_path: Path, text: str, expected_match: str
):
    paths, config = local_modules
    home = tmp_path / "user"
    home.mkdir(exist_ok=True)
    (home / ".local").mkdir(exist_ok=True)
    path = tmp_path / "nix-darwin.toml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(paths.ConfigError, match=expected_match):
        config.load_control_config(path, operator_home=home)


def test_paths_workspace_root_disagreement_with_nix_darwin_local(
    local_modules, tmp_path: Path, monkeypatch
):
    paths, config = local_modules
    home = tmp_path / "user"
    home.mkdir()
    (home / ".local").mkdir()
    (home / ".local/nix-darwin").mkdir()
    path = tmp_path / "nix-darwin.toml"
    path.write_text(
        '[paths]\nworkspace_root = "$HOME/.local/nix-darwin"\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "operator_home", lambda: home)
    monkeypatch.setenv("NIX_DARWIN_LOCAL", str(home / ".local/other-ws"))
    with pytest.raises(paths.ConfigError, match="disagrees with NIX_DARWIN_LOCAL"):
        config.load_control_config(path)

    monkeypatch.setenv("NIX_DARWIN_LOCAL", str(home / ".local/nix-darwin"))
    parsed = config.load_control_config(path)
    assert parsed.workspace_root == home / ".local/nix-darwin"


def test_projected_paths_use_control_config_type_and_exact_schema(local_modules, tmp_path: Path):
    paths, config = local_modules
    home = tmp_path / "user"
    home.mkdir()
    projection = tmp_path / "paths.json"
    projection.write_text(
        """{
  "schema": "dinas-paths-v1",
  "workspace_root": "$HOME/.local/workspace",
  "nbin_root": "$HOME/.local/commands",
  "nbin_dev_root": "$HOME/.local/candidates",
  "ai_index_root": "$HOME/ai",
  "dinas_root": "$HOME/.local/workspace/dinas"
}
""",
        encoding="utf-8",
    )

    parsed = config.load_projected_paths(projection, operator_home=home)
    assert isinstance(parsed, config.ControlConfig)
    assert parsed.nbin_root == home / ".local/commands"
    assert parsed.nbin_dev_root == home / ".local/candidates"

    projection.write_text(
        projection.read_text(encoding="utf-8").replace(
            '"schema": "dinas-paths-v1",',
            '"schema": "dinas-paths-v1",\n  "unknown": "value",',
        ),
        encoding="utf-8",
    )
    with pytest.raises(paths.ConfigError, match="unknown paths projection key"):
        config.load_projected_paths(projection, operator_home=home)


def test_operator_home_honors_home_env_and_falls_back(local_modules, monkeypatch, tmp_path: Path):
    import os, pwd
    _, config = local_modules
    fake_home = tmp_path / "custom_home"
    monkeypatch.setenv("HOME", str(fake_home))
    assert config.operator_home() == fake_home

    # When HOME is unset or empty, falls back to password database
    expected_pw_dir = Path(pwd.getpwuid(os.getuid()).pw_dir)
    monkeypatch.delenv("HOME", raising=False)
    assert config.operator_home() == expected_pw_dir

    monkeypatch.setenv("HOME", "")
    assert config.operator_home() == expected_pw_dir


def test_operator_home_divergence_feeds_default_paths(local_modules, tmp_path: Path):
    _, config = local_modules
    custom_home = tmp_path / "divergent_home"
    paths = config.default_paths(custom_home)
    assert paths["workspace_root"] == custom_home / ".local/nix-darwin"
    assert paths["nbin_root"] == custom_home / ".local/nbin"
    assert paths["nbin_dev_root"] == custom_home / ".local/nbin_dev"
    assert paths["ai_index_root"] == custom_home / "ai"
    assert paths["dinas_root"] == custom_home / ".local/nix-darwin/darwin-is-not-a-snowflake"


@pytest.mark.parametrize("target_kind", ["former_flakes", "arbitrary", "dangling", "cyclic"])
def test_managed_nbin_root_rejects_every_symlink(local_modules, tmp_path, target_kind):
    paths, _ = local_modules
    home = tmp_path / "home"
    home.mkdir()
    root = home / "nbin"
    if target_kind == "former_flakes":
        target = home / ".flakes/nix-darwin/bin"
        target.mkdir(parents=True)
    elif target_kind == "arbitrary":
        target = home / "other-bin"
        target.mkdir()
    elif target_kind == "dangling":
        target = home / "missing-bin"
    else:
        target = root
    root.symlink_to(target)

    with pytest.raises(paths.ConfigError, match="paths.nbin_root has symlink ambiguity"):
        paths.validate_safe_managed_root(root, home, "paths.nbin_root")

    assert root.is_symlink()
    assert root.readlink() == target


def test_snapshot_absent_legacy_config_accepted(local_modules, tmp_path: Path):
    paths, config = local_modules
    home = tmp_path / "home"
    home.mkdir()
    core_dir = home / "core"
    core_dir.mkdir()
    path = tmp_path / "nix-darwin.toml"
    path.write_text(
        f"""
[sources.core]
checkout = "{core_dir}"
repository = "ssh://git@github.com/example/core.git"
release_ref = "refs/heads/main"
"""
    )
    parsed = config.load_control_config(path, operator_home=home)
    assert parsed.present is True
    assert parsed.snapshot is None


def test_complete_snapshot_accepted_and_deterministically_sorted(local_modules, tmp_path: Path):
    paths, config = local_modules
    home = tmp_path / "home"
    home.mkdir()
    core_dir = home / "core"
    core_dir.mkdir()
    leaf_dir = home / "leaf"
    leaf_dir.mkdir()
    rev_core = "43b6f165d56c65b8b32a90b87b3f5dd9ec45df00"
    rev_leaf = "a88c6d6a9780b668c426a6ae3511e19bfd97b378"

    path = tmp_path / "nix-darwin.toml"
    # Provide keys in non-alphabetical order
    path.write_text(
        f"""
[sources.leaf]
checkout = "{leaf_dir}"
repository = "ssh://git@github.com/example/leaf.git"
release_ref = "refs/heads/main"

[sources.core]
checkout = "{core_dir}"
repository = "ssh://git@github.com/example/core.git"
release_ref = "refs/heads/main"

[snapshot.sources]
leaf = "{rev_leaf}"
core = "{rev_core}"
"""
    )
    parsed = config.load_control_config(path, operator_home=home)
    assert parsed.snapshot is not None
    assert isinstance(parsed.snapshot, config.SourceSnapshot)
    # Must be deterministically key-sorted
    assert parsed.snapshot.revisions == (("core", rev_core), ("leaf", rev_leaf))
    assert parsed.snapshot.by_key == {"core": rev_core, "leaf": rev_leaf}
    assert parsed.snapshot["core"] == rev_core
    assert "core" in parsed.snapshot
    assert len(parsed.snapshot) == 2


def test_snapshot_missing_source_key_fails_closed(local_modules, tmp_path: Path):
    paths, config = local_modules
    home = tmp_path / "home"
    home.mkdir()
    core_dir = home / "core"
    core_dir.mkdir()
    leaf_dir = home / "leaf"
    leaf_dir.mkdir()

    path = tmp_path / "nix-darwin.toml"
    path.write_text(
        f"""
[sources.core]
checkout = "{core_dir}"
repository = "ssh://git@github.com/example/core.git"
release_ref = "refs/heads/main"

[sources.leaf]
checkout = "{leaf_dir}"
repository = "ssh://git@github.com/example/leaf.git"
release_ref = "refs/heads/main"

[snapshot.sources]
core = "43b6f165d56c65b8b32a90b87b3f5dd9ec45df00"
"""
    )
    with pytest.raises(paths.ConfigError, match="snapshot.sources is missing configured source keys: leaf"):
        config.load_control_config(path, operator_home=home)


def test_snapshot_unknown_source_key_fails_closed(local_modules, tmp_path: Path):
    paths, config = local_modules
    home = tmp_path / "home"
    home.mkdir()
    core_dir = home / "core"
    core_dir.mkdir()

    path = tmp_path / "nix-darwin.toml"
    path.write_text(
        f"""
[sources.core]
checkout = "{core_dir}"
repository = "ssh://git@github.com/example/core.git"
release_ref = "refs/heads/main"

[snapshot.sources]
core = "43b6f165d56c65b8b32a90b87b3f5dd9ec45df00"
unknown = "a88c6d6a9780b668c426a6ae3511e19bfd97b378"
"""
    )
    with pytest.raises(paths.ConfigError, match="snapshot.sources contains unknown source keys: unknown"):
        config.load_control_config(path, operator_home=home)


@pytest.mark.parametrize(
    "bad_rev",
    [
        "shortrev",
        "HEAD",
        "refs/heads/main",
        "43b6f165d56c65b8b32a90b87b3f5dd9ec45df0Z",  # non-hex
        "43b6f165d56c65b8b32a90b87b3f5dd9ec45df000", # 41 chars
        12345,
    ],
)
def test_snapshot_malformed_revision_fails_closed(local_modules, tmp_path: Path, bad_rev):
    paths, config = local_modules
    home = tmp_path / "home"
    home.mkdir()
    core_dir = home / "core"
    core_dir.mkdir()

    path = tmp_path / "nix-darwin.toml"
    bad_val = f'"{bad_rev}"' if isinstance(bad_rev, str) else bad_rev
    path.write_text(
        f"""
[sources.core]
checkout = "{core_dir}"
repository = "ssh://git@github.com/example/core.git"
release_ref = "refs/heads/main"

[snapshot.sources]
core = {bad_val}
"""
    )
    with pytest.raises(paths.ConfigError, match="expected 40-hex-commit"):
        config.load_control_config(path, operator_home=home)


def test_snapshot_normalizes_uppercase_hex_revision(local_modules, tmp_path: Path):
    paths, config = local_modules
    home = tmp_path / "home"
    home.mkdir()
    core_dir = home / "core"
    core_dir.mkdir()
    rev_core_upper = "43B6F165D56C65B8B32A90B87B3F5DD9EC45DF00"
    rev_core_lower = rev_core_upper.lower()

    path = tmp_path / "nix-darwin.toml"
    path.write_text(
        f"""
[sources.core]
checkout = "{core_dir}"
repository = "ssh://git@github.com/example/core.git"
release_ref = "refs/heads/main"

[snapshot.sources]
core = "{rev_core_upper}"
"""
    )
    parsed = config.load_control_config(path, operator_home=home)
    assert parsed.snapshot is not None
    assert parsed.snapshot["core"] == rev_core_lower


def test_snapshot_table_with_no_sources_fails_closed(local_modules, tmp_path: Path):
    paths, config = local_modules
    home = tmp_path / "home"
    home.mkdir()
    core_dir = home / "core"
    core_dir.mkdir()

    # Case 1: [snapshot] with no sources key
    path1 = tmp_path / "nix-darwin1.toml"
    path1.write_text(
        f"""
[sources.core]
checkout = "{core_dir}"
repository = "ssh://git@github.com/example/core.git"
release_ref = "refs/heads/main"

[snapshot]
"""
    )
    with pytest.raises(paths.ConfigError, match="snapshot.sources is required"):
        config.load_control_config(path1, operator_home=home)

    # Case 2: [snapshot.sources] is empty
    path2 = tmp_path / "nix-darwin2.toml"
    path2.write_text(
        f"""
[sources.core]
checkout = "{core_dir}"
repository = "ssh://git@github.com/example/core.git"
release_ref = "refs/heads/main"

[snapshot.sources]
"""
    )
    with pytest.raises(paths.ConfigError, match="snapshot.sources must not be empty"):
        config.load_control_config(path2, operator_home=home)


def test_snapshot_key_names_with_hyphens_and_underscores(local_modules, tmp_path: Path):
    paths, config = local_modules
    home = tmp_path / "home"
    home.mkdir()

    keys = [
        "agent-security-nd",
        "agent_dinas",
        "agentic-praxis-grimoire_dinas",
        "composition-nd",
        "composition-private-nd",
        "core",
        "developer-ux_dinas",
        "entertainment-nd",
        "joint-agentic-command-aegis-nd",
    ]
    sources_toml = []
    snapshot_toml = []
    rev = "a" * 40
    for k in keys:
        d = home / k
        d.mkdir()
        sources_toml.append(f'[sources."{k}"]\ncheckout = "{d}"\nrepository = "ssh://git@example/{k}.git"\nrelease_ref = "refs/heads/main"\n')
        snapshot_toml.append(f'"{k}" = "{rev}"\n')

    path = tmp_path / "nix-darwin.toml"
    path.write_text("\n".join(sources_toml) + "\n[snapshot.sources]\n" + "\n".join(snapshot_toml))

    parsed = config.load_control_config(path, operator_home=home)
    assert parsed.snapshot is not None
    assert len(parsed.snapshot) == 9
    assert sorted(parsed.snapshot.by_key.keys()) == sorted(keys)


def test_resolve_effective_source_revisions_snapshot_alone(local_modules, tmp_path: Path):
    _, config = local_modules
    home = tmp_path / "home"
    home.mkdir()
    core_dir = home / "core"
    core_dir.mkdir()
    leaf_dir = home / "leaf"
    leaf_dir.mkdir()
    rev_core = "1" * 40
    rev_leaf = "2" * 40

    path = tmp_path / "nix-darwin.toml"
    path.write_text(
        f"""
[sources.core]
checkout = "{core_dir}"
repository = "ssh://git@github.com/example/core.git"
release_ref = "refs/heads/main"

[sources.leaf]
checkout = "{leaf_dir}"
repository = "ssh://git@github.com/example/leaf.git"
release_ref = "refs/heads/main"

[snapshot.sources]
core = "{rev_core}"
leaf = "{rev_leaf}"
"""
    )
    cfg = config.load_control_config(path, operator_home=home)
    effective = config.resolve_effective_source_revisions(cfg, {})

    assert effective.active is True
    assert effective.by_key == {"core": rev_core, "leaf": rev_leaf}
    assert effective.provenance_by_key == {"core": "snapshot", "leaf": "snapshot"}
    assert effective.cli_override_count == 0


def test_resolve_effective_source_revisions_one_cli_override(local_modules, tmp_path: Path):
    _, config = local_modules
    home = tmp_path / "home"
    home.mkdir()
    core_dir = home / "core"
    core_dir.mkdir()
    leaf_dir = home / "leaf"
    leaf_dir.mkdir()
    rev_core = "1" * 40
    rev_leaf = "2" * 40
    rev_core_override = "3" * 40

    path = tmp_path / "nix-darwin.toml"
    path.write_text(
        f"""
[sources.core]
checkout = "{core_dir}"
repository = "ssh://git@github.com/example/core.git"
release_ref = "refs/heads/main"

[sources.leaf]
checkout = "{leaf_dir}"
repository = "ssh://git@github.com/example/leaf.git"
release_ref = "refs/heads/main"

[snapshot.sources]
core = "{rev_core}"
leaf = "{rev_leaf}"
"""
    )
    cfg = config.load_control_config(path, operator_home=home)
    effective = config.resolve_effective_source_revisions(cfg, {"core": rev_core_override})

    assert effective.active is True
    assert effective.by_key == {"core": rev_core_override, "leaf": rev_leaf}
    assert effective.provenance_by_key == {"core": "cli", "leaf": "snapshot"}
    assert effective.cli_override_count == 1


def test_resolve_effective_source_revisions_cli_only_no_snapshot(local_modules, tmp_path: Path):
    _, config = local_modules
    home = tmp_path / "home"
    home.mkdir()
    core_dir = home / "core"
    core_dir.mkdir()
    rev_cli = "9" * 40

    path = tmp_path / "nix-darwin.toml"
    path.write_text(
        f"""
[sources.core]
checkout = "{core_dir}"
repository = "ssh://git@github.com/example/core.git"
release_ref = "refs/heads/main"
"""
    )
    cfg = config.load_control_config(path, operator_home=home)

    # Empty CLI
    eff_empty = config.resolve_effective_source_revisions(cfg, {})
    assert eff_empty.active is False
    assert eff_empty.by_key == {}
    assert eff_empty.cli_override_count == 0

    # With CLI override
    eff_cli = config.resolve_effective_source_revisions(cfg, {"core": rev_cli})
    assert eff_cli.active is False
    assert eff_cli.by_key == {"core": rev_cli}
    assert eff_cli.provenance_by_key == {"core": "cli"}
    assert eff_cli.cli_override_count == 1


def test_resolve_effective_source_revisions_unknown_cli_key_fails(local_modules, tmp_path: Path):
    paths, config = local_modules
    home = tmp_path / "home"
    home.mkdir()
    core_dir = home / "core"
    core_dir.mkdir()

    path = tmp_path / "nix-darwin.toml"
    path.write_text(
        f"""
[sources.core]
checkout = "{core_dir}"
repository = "ssh://git@github.com/example/core.git"
release_ref = "refs/heads/main"
"""
    )
    cfg = config.load_control_config(path, operator_home=home)

    with pytest.raises(paths.ConfigError, match="unknown source key in --source-revision: 'missing'"):
        config.resolve_effective_source_revisions(cfg, {"missing": "a" * 40})
