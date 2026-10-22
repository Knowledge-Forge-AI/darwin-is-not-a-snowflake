# nix-darwin-test-source-target: libexec/nix_darwin_local/source_catalog.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/config.py
from __future__ import annotations

import dataclasses
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def local_modules(repo_root: Path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "libexec"))
    paths = importlib.import_module("nix_darwin_local.paths")
    config = importlib.import_module("nix_darwin_local.config")
    source_catalog = importlib.import_module("nix_darwin_local.source_catalog")
    return paths, config, source_catalog


def create_safe_dir(path: Path, mode: int = 0o755) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(mode)
    return path


def test_source_catalog_entry_and_publication_dataclasses_frozen(local_modules, tmp_path: Path):
    _, _, source_catalog = local_modules
    pub = source_catalog.Publication(remote="origin", ref="refs/heads/main")
    assert pub.remote == "origin"
    assert pub.ref == "refs/heads/main"

    with pytest.raises(dataclasses.FrozenInstanceError):
        pub.remote = "other"  # type: ignore

    entry = source_catalog.SourceCatalogEntry(
        key="core",
        checkout=tmp_path / "checkout",
        repository="https://github.com/example/repo.git",
        release_ref="refs/heads/main",
        publication=pub,
    )
    assert entry.key == "core"
    assert entry.checkout == tmp_path / "checkout"
    assert entry.repository == "https://github.com/example/repo.git"
    assert entry.release_ref == "refs/heads/main"
    assert entry.publication == pub
    assert entry.canonical_repository_identity == "https://github.com/example/repo"

    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.key = "mutated"  # type: ignore


def test_valid_minimal_source_catalog_entry(local_modules, tmp_path: Path):
    paths, config, source_catalog = local_modules
    home = create_safe_dir(tmp_path / "home")
    checkout_dir = create_safe_dir(home / "src" / "nix-darwin")

    config_path = home / "control.toml"
    config_path.write_text(
        """
[sources.core]
checkout = "~/src/nix-darwin"
repository = "https://github.com/example/nix-darwin.git"
release_ref = "refs/heads/main"
"""
    )

    loaded = config.load_control_config(config_path, operator_home=home)
    assert len(loaded.sources) == 1
    entry = loaded.sources[0]
    assert entry.key == "core"
    assert entry.checkout == checkout_dir
    assert entry.repository == "https://github.com/example/nix-darwin.git"
    assert entry.release_ref == "refs/heads/main"
    assert entry.publication is None
    assert entry.canonical_repository_identity == "https://github.com/example/nix-darwin"


def test_valid_source_catalog_with_publication(local_modules, tmp_path: Path):
    paths, config, source_catalog = local_modules
    home = create_safe_dir(tmp_path / "home")
    checkout_dir = create_safe_dir(home / ".local" / "nix-darwin" / "developer-ux")

    config_path = home / "control.toml"
    config_path.write_text(
        """
[sources.developer-ux]
checkout = "$HOME/.local/nix-darwin/developer-ux"
repository = "ssh://git@github.com/example/developer-ux-nd.git"
release_ref = "refs/heads/release/2026"

[sources.developer-ux.publication]
remote = "origin"
ref = "refs/heads/main"
"""
    )

    loaded = config.load_control_config(config_path, operator_home=home)
    assert len(loaded.sources) == 1
    entry = loaded.sources[0]
    assert entry.key == "developer-ux"
    assert entry.checkout == checkout_dir
    assert entry.repository == "ssh://git@github.com/example/developer-ux-nd.git"
    assert entry.release_ref == "refs/heads/release/2026"
    assert entry.publication == source_catalog.Publication(
        remote="origin", ref="refs/heads/main"
    )


def test_portable_paths_resolution_and_validation(local_modules, tmp_path: Path):
    paths, config, source_catalog = local_modules
    home = create_safe_dir(tmp_path / "home")
    dir_tilde = create_safe_dir(home / "from-tilde")
    dir_env = create_safe_dir(home / "from-env")
    dir_abs = create_safe_dir(home / "from-abs")

    # Valid portable paths
    entry_tilde = source_catalog.parse_source_entry(
        "tilde",
        {
            "checkout": "~/from-tilde",
            "repository": "https://github.com/example/tilde.git",
            "release_ref": "refs/heads/main",
        },
        home,
    )
    assert entry_tilde.checkout == dir_tilde

    entry_env = source_catalog.parse_source_entry(
        "env",
        {
            "checkout": "$HOME/from-env",
            "repository": "https://github.com/example/env.git",
            "release_ref": "refs/heads/main",
        },
        home,
    )
    assert entry_env.checkout == dir_env

    entry_abs = source_catalog.parse_source_entry(
        "abs",
        {
            "checkout": str(dir_abs),
            "repository": "https://github.com/example/abs.git",
            "release_ref": "refs/heads/main",
        },
        home,
    )
    assert entry_abs.checkout == dir_abs

    # Invalid portable paths
    invalid_checkouts = [
        "relative/path",
        "${HOME}/expansion",
        "~/with/../traversal",
        "~/trailing/slash/",
        "~/with$badchar",
        "",
    ]
    for bad in invalid_checkouts:
        with pytest.raises(paths.ConfigError):
            source_catalog.parse_source_entry(
                "bad",
                {
                    "checkout": bad,
                    "repository": "https://github.com/example/bad.git",
                    "release_ref": "refs/heads/main",
                },
                home,
            )


def test_strict_unknown_fields_rejected(local_modules, tmp_path: Path):
    paths, config, source_catalog = local_modules
    home = create_safe_dir(tmp_path / "home")
    create_safe_dir(home / "src" / "test")

    # Unknown field in source entry
    with pytest.raises(paths.ConfigError, match="unknown sources.core key: extra_field"):
        source_catalog.parse_source_entry(
            "core",
            {
                "checkout": "~/src/test",
                "repository": "https://github.com/example/core.git",
                "release_ref": "refs/heads/main",
                "extra_field": "disallowed",
            },
            home,
        )

    # Unknown field in publication table
    with pytest.raises(paths.ConfigError, match="unknown sources.core.publication key: url"):
        source_catalog.parse_source_entry(
            "core",
            {
                "checkout": "~/src/test",
                "repository": "https://github.com/example/core.git",
                "release_ref": "refs/heads/main",
                "publication": {
                    "remote": "origin",
                    "ref": "refs/heads/main",
                    "url": "https://github.com/example/not-allowed.git",
                },
            },
            home,
        )


def test_missing_required_fields_rejected(local_modules, tmp_path: Path):
    paths, config, source_catalog = local_modules
    home = create_safe_dir(tmp_path / "home")
    create_safe_dir(home / "src" / "test")

    # Missing checkout
    with pytest.raises(paths.ConfigError, match="checkout"):
        source_catalog.parse_source_entry(
            "core",
            {
                "repository": "https://github.com/example/core.git",
                "release_ref": "refs/heads/main",
            },
            home,
        )

    # Missing repository
    with pytest.raises(paths.ConfigError, match="repository"):
        source_catalog.parse_source_entry(
            "core",
            {
                "checkout": "~/src/test",
                "release_ref": "refs/heads/main",
            },
            home,
        )

    # Missing release_ref
    with pytest.raises(paths.ConfigError, match="release_ref"):
        source_catalog.parse_source_entry(
            "core",
            {
                "checkout": "~/src/test",
                "repository": "https://github.com/example/core.git",
            },
            home,
        )

    # Publication missing remote
    with pytest.raises(paths.ConfigError, match="publication.remote"):
        source_catalog.parse_source_entry(
            "core",
            {
                "checkout": "~/src/test",
                "repository": "https://github.com/example/core.git",
                "release_ref": "refs/heads/main",
                "publication": {"ref": "refs/heads/main"},
            },
            home,
        )

    # Publication missing ref
    with pytest.raises(paths.ConfigError, match="publication.ref"):
        source_catalog.parse_source_entry(
            "core",
            {
                "checkout": "~/src/test",
                "repository": "https://github.com/example/core.git",
                "release_ref": "refs/heads/main",
                "publication": {"remote": "origin"},
            },
            home,
        )


def test_missing_checkout_directory_rejected(local_modules, tmp_path: Path):
    paths, config, source_catalog = local_modules
    home = create_safe_dir(tmp_path / "home")

    # Directory does not exist
    with pytest.raises(paths.ConfigError, match="does not exist"):
        source_catalog.parse_source_entry(
            "core",
            {
                "checkout": "~/src/nonexistent",
                "repository": "https://github.com/example/core.git",
                "release_ref": "refs/heads/main",
            },
            home,
        )

    # Checkout exists but is a regular file
    reg_file = home / "src" / "not_a_dir"
    reg_file.parent.mkdir(parents=True, exist_ok=True)
    reg_file.write_text("hello\n")
    with pytest.raises(paths.ConfigError, match="not a directory"):
        source_catalog.parse_source_entry(
            "core",
            {
                "checkout": "~/src/not_a_dir",
                "repository": "https://github.com/example/core.git",
                "release_ref": "refs/heads/main",
            },
            home,
        )


def test_release_ref_normalization_and_validation(local_modules, tmp_path: Path):
    paths, config, source_catalog = local_modules
    home = create_safe_dir(tmp_path / "home")
    create_safe_dir(home / "src" / "test")

    # Valid refs
    for ref in ["refs/heads/main", "refs/heads/release/2026", "refs/heads/feat_1"]:
        entry = source_catalog.parse_source_entry(
            "core",
            {
                "checkout": "~/src/test",
                "repository": "https://github.com/example/core.git",
                "release_ref": ref,
            },
            home,
        )
        assert entry.release_ref == ref

    # Invalid refs
    bad_refs = [
        "main",
        "refs/tags/v1.0.0",
        "refs/heads/",
        "refs/heads/..",
        "refs/heads/foo*bar",
        "refs/heads/@{upstream}",
        "refs/heads/a//b",
        "refs/heads/branch.lock",
    ]
    for bad in bad_refs:
        with pytest.raises(paths.ConfigError):
            source_catalog.parse_source_entry(
                "core",
                {
                    "checkout": "~/src/test",
                    "repository": "https://github.com/example/core.git",
                    "release_ref": bad,
                },
                home,
            )

        # Publication ref validation
        with pytest.raises(paths.ConfigError):
            source_catalog.parse_source_entry(
                "core",
                {
                    "checkout": "~/src/test",
                    "repository": "https://github.com/example/core.git",
                    "release_ref": "refs/heads/main",
                    "publication": {"remote": "origin", "ref": bad},
                },
                home,
            )


def test_repository_url_normalization_and_secrets_rejection(local_modules, tmp_path: Path):
    paths, config, source_catalog = local_modules
    home = create_safe_dir(tmp_path / "home")
    create_safe_dir(home / "src" / "test")

    # Secret-bearing and invalid URLs rejected
    bad_urls = [
        "https://user:password@github.com/example/repo.git",
        "https://token=secret123@github.com/example/repo.git",
        "https://github.com/example/repo.git?secret=123",
        "https://github.com/example/repo.git#fragment",
        "ssh://user@github.com/example/repo.git",
        "git://github.com/example/repo.git",
        "not a url",
    ]
    for bad in bad_urls:
        with pytest.raises(paths.ConfigError):
            source_catalog.parse_source_entry(
                "core",
                {
                    "checkout": "~/src/test",
                    "repository": bad,
                    "release_ref": "refs/heads/main",
                },
                home,
            )

    # Valid URLs normalized
    entry_git_https = source_catalog.parse_source_entry(
        "core",
        {
            "checkout": "~/src/test",
            "repository": "git+https://GitHub.com/example/repo.git",
            "release_ref": "refs/heads/main",
        },
        home,
    )
    assert entry_git_https.repository == "https://github.com/example/repo.git"

    entry_git_ssh = source_catalog.parse_source_entry(
        "core",
        {
            "checkout": "~/src/test",
            "repository": "git+ssh://git@GitHub.com/example/repo.git",
            "release_ref": "refs/heads/main",
        },
        home,
    )
    assert entry_git_ssh.repository == "ssh://git@github.com/example/repo.git"


def test_unsafe_managed_root_directory_guards(local_modules, tmp_path: Path):
    paths, config, source_catalog = local_modules
    home = create_safe_dir(tmp_path / "home")

    # Group/world writable mode rejected
    unsafe_mode_dir = create_safe_dir(home / "src" / "unsafe_mode", mode=0o777)
    with pytest.raises(paths.ConfigError, match="unsafe mode"):
        source_catalog.parse_source_entry(
            "core",
            {
                "checkout": str(unsafe_mode_dir),
                "repository": "https://github.com/example/core.git",
                "release_ref": "refs/heads/main",
            },
            home,
        )

    # Symlink checkout directory rejected
    target_dir = create_safe_dir(home / "src" / "real_target")
    symlink_dir = home / "src" / "symlink_dir"
    symlink_dir.symlink_to(target_dir)
    with pytest.raises(paths.ConfigError, match="symlink ambiguity"):
        source_catalog.parse_source_entry(
            "core",
            {
                "checkout": str(symlink_dir),
                "repository": "https://github.com/example/core.git",
                "release_ref": "refs/heads/main",
            },
            home,
        )

    # Escapes home rejected
    outside = create_safe_dir(tmp_path / "outside")
    with pytest.raises(paths.ConfigError, match="escapes its required root"):
        source_catalog.parse_source_entry(
            "core",
            {
                "checkout": str(outside),
                "repository": "https://github.com/example/core.git",
                "release_ref": "refs/heads/main",
            },
            home,
        )

    # Home itself rejected
    with pytest.raises(paths.ConfigError, match="cannot be the root or home directory"):
        source_catalog.parse_source_entry(
            "core",
            {
                "checkout": str(home),
                "repository": "https://github.com/example/core.git",
                "release_ref": "refs/heads/main",
            },
            home,
        )


def test_duplicate_checkout_identities_rejected(local_modules, tmp_path: Path):
    paths, config, source_catalog = local_modules
    home = create_safe_dir(tmp_path / "home")
    shared_dir = create_safe_dir(home / "src" / "shared")

    config_path = home / "control.toml"
    config_path.write_text(
        """
[sources.a]
checkout = "~/src/shared"
repository = "https://github.com/example/a.git"
release_ref = "refs/heads/main"

[sources.b]
checkout = "$HOME/src/shared"
repository = "https://github.com/example/b.git"
release_ref = "refs/heads/main"
"""
    )

    with pytest.raises(paths.ConfigError, match="duplicate source checkout path"):
        config.load_control_config(config_path, operator_home=home)


def test_duplicate_repository_identities_rejected(local_modules, tmp_path: Path):
    paths, config, source_catalog = local_modules
    home = create_safe_dir(tmp_path / "home")
    dir_a = create_safe_dir(home / "src" / "a")
    dir_b = create_safe_dir(home / "src" / "b")

    config_path = home / "control.toml"
    config_path.write_text(
        """
[sources.a]
checkout = "~/src/a"
repository = "https://github.com/example/repo.git"
release_ref = "refs/heads/main"

[sources.b]
checkout = "~/src/b"
repository = "git+https://github.com/example/repo"
release_ref = "refs/heads/main"
"""
    )

    with pytest.raises(paths.ConfigError, match="duplicate source repository identity"):
        config.load_control_config(config_path, operator_home=home)


def test_promotion_checkouts_disjoint_compatibility(local_modules, tmp_path: Path):
    paths, config, source_catalog = local_modules
    home = create_safe_dir(tmp_path / "home")
    dir_stage = create_safe_dir(home / "src" / "stage1")
    dir_core = create_safe_dir(home / "src" / "core")

    # Overlapping key rejected
    overlap_config = home / "overlap.toml"
    overlap_config.write_text(
        """
[promotion.checkouts]
core = "~/src/stage1"

[sources.core]
checkout = "~/src/core"
repository = "https://github.com/example/core.git"
release_ref = "refs/heads/main"
"""
    )
    with pytest.raises(paths.ConfigError, match="overlapping key: core"):
        config.load_control_config(overlap_config, operator_home=home)

    # Disjoint keys allowed
    disjoint_config = home / "disjoint.toml"
    disjoint_config.write_text(
        """
[promotion.checkouts]
stage1 = "~/src/stage1"

[sources.core]
checkout = "~/src/core"
repository = "https://github.com/example/core.git"
release_ref = "refs/heads/main"
"""
    )
    loaded = config.load_control_config(disjoint_config, operator_home=home)
    assert len(loaded.promotion_checkouts) == 1
    assert loaded.promotion_checkouts[0] == ("stage1", dir_stage)
    assert len(loaded.sources) == 1
    assert loaded.sources[0].key == "core"
    assert loaded.sources[0].checkout == dir_core
    disjoint_config.write_text(disjoint_config.read_text().replace('stage1 = "~/src/stage1"', 'stage1 = "~/src/core"'))
    with pytest.raises(paths.ConfigError, match="duplicates a source catalog checkout"):
        config.load_control_config(disjoint_config, operator_home=home)


def test_lookup_by_key_and_canonical_repository(local_modules, tmp_path: Path):
    paths, config, source_catalog = local_modules
    home = create_safe_dir(tmp_path / "home")
    dir_core = create_safe_dir(home / "src" / "core")
    dir_dev = create_safe_dir(home / "src" / "dev")

    config_path = home / "control.toml"
    config_path.write_text(
        """
[sources.core]
checkout = "~/src/core"
repository = "https://github.com/example/core.git"
release_ref = "refs/heads/main"

[sources.developer-ux]
checkout = "~/src/dev"
repository = "ssh://git@github.com/example/developer-ux-nd.git"
release_ref = "refs/heads/release/2026"
[sources.developer-ux.publication]
remote = "origin"
ref = "refs/heads/main"
"""
    )

    loaded = config.load_control_config(config_path, operator_home=home)
    catalog = loaded.source_catalog

    # Lookup by key
    assert source_catalog.lookup_by_key(loaded.sources, "core") == loaded.sources[0]
    assert source_catalog.lookup_by_key(loaded.sources, "nonexistent") is None
    assert catalog.lookup_key("core") == loaded.sources[0]
    assert catalog.lookup_key("nonexistent") is None
    assert catalog.get("core") == loaded.sources[0]
    assert catalog["core"] == loaded.sources[0]
    assert "core" in catalog
    assert "nonexistent" not in catalog
    assert len(catalog) == 2
    assert set(catalog.keys) == {"core", "developer-ux"}

    with pytest.raises(KeyError):
        _ = catalog["nonexistent"]

    # ControlConfig helpers
    assert loaded.source("core") == loaded.sources[0]
    assert loaded.get_source("core") == loaded.sources[0]
    assert loaded.source("nonexistent") is None

    # Lookup by repository canonical identity
    # HTTPS matching with and without .git
    match1 = source_catalog.lookup_by_repository(loaded.sources, "https://github.com/example/core")
    assert match1 == loaded.sources[0]
    match2 = source_catalog.lookup_by_repository(loaded.sources, "https://github.com/example/core.git")
    assert match2 == loaded.sources[0]
    match3 = source_catalog.lookup_by_repository(loaded.sources, "git+https://github.com/example/core.git")
    assert match3 == loaded.sources[0]

    # SSH matching with transport syntax and nix URL syntax
    match4 = source_catalog.lookup_by_repository(loaded.sources, "ssh://git@github.com/example/developer-ux-nd.git")
    assert match4 == loaded.sources[1]
    match5 = source_catalog.lookup_by_repository(loaded.sources, "git@github.com:example/developer-ux-nd.git")
    assert match5 == loaded.sources[1]

    # Nonexistent repo lookup returns None
    assert source_catalog.lookup_by_repository(loaded.sources, "https://github.com/example/other.git") is None
    assert source_catalog.lookup_by_repository(loaded.sources, "not-a-valid-url") is None

    # Catalog & Config repository lookup
    assert catalog.lookup_repository("https://github.com/example/core") == loaded.sources[0]
    assert loaded.source_by_repository("https://github.com/example/core") == loaded.sources[0]
    assert loaded.get_source_by_repository("https://github.com/example/core") == loaded.sources[0]
    assert loaded.source_by_repository("https://github.com/example/other") is None


def test_sources_omitted_produces_empty_tuple(local_modules, tmp_path: Path):
    paths, config, source_catalog = local_modules
    home = create_safe_dir(tmp_path / "home")

    config_path = home / "empty.toml"
    config_path.write_text(
        """
[flake]
active = "~/src/flake"
"""
    )
    create_safe_dir(home / "src" / "flake")

    loaded = config.load_control_config(config_path, operator_home=home)
    assert loaded.sources == ()
    assert loaded.source("core") is None
    assert loaded.source_by_repository("https://github.com/example/core") is None


@pytest.mark.parametrize("legacy", [False, True])
def test_checkout_case_aliases_follow_filesystem_identity(local_modules, tmp_path, legacy):
    paths, config, _ = local_modules
    home = create_safe_dir(tmp_path / "home")
    checkout = create_safe_dir(home / "source")
    alias = home / "SOURCE"
    aliases = alias.exists() and alias.samefile(checkout)
    if not aliases:
        create_safe_dir(alias)
    control = home / "control.toml"
    second = (
        '[promotion.checkouts]\nother = "~/SOURCE"\n' if legacy else
        '[sources.other]\ncheckout = "~/SOURCE"\n'
        'repository = "https://example.invalid/other.git"\nrelease_ref = "refs/heads/main"\n'
    )
    control.write_text(
        '[sources.core]\ncheckout = "~/source"\n'
        'repository = "https://example.invalid/core.git"\nrelease_ref = "refs/heads/main"\n'
        + second
    )
    if aliases:
        with pytest.raises(paths.ConfigError, match="duplicat"):
            config.load_control_config(control, operator_home=home)
    else:
        loaded = config.load_control_config(control, operator_home=home)
        assert len(loaded.sources) == (1 if legacy else 2)
