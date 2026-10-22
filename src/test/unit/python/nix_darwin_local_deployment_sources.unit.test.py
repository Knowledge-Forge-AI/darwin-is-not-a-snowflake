# nix-darwin-test-source-target: libexec/nix_darwin_local/deployment_sources.py
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local.deployment_sources import derive_source_identities, _collect_store_paths
from nix_darwin_local.paths import ConfigError


class MockWorkflow:
    def __init__(self, outputs=None, failures=None):
        self.outputs = outputs or {}
        self.failures = failures or set()
        self.calls = []

    def command(self, label, argv, cwd):
        self.calls.append((label, list(argv), cwd))
        code = 1 if label in self.failures else 0
        return SimpleNamespace(returncode=code)

    def complete_stdout(self, label):
        return self.outputs.get(label, "")


def test_derive_source_identities_success(tmp_path: Path):
    flake_dir = tmp_path / "flake"
    flake_dir.mkdir()
    lock_data = {
        "version": 7,
        "root": "root",
        "nodes": {
            "root": {
                "inputs": {
                    "nixpkgs": "nixpkgs",
                    "repo-map-src": "repo-map-src",
                    "security-nd": "security-nd",
                    "localConfig": "localConfig",
                }
            },
            "nixpkgs": {
                "locked": {
                    "type": "github",
                    "owner": "NixOS",
                    "repo": "nixpkgs",
                    "rev": "1234567890123456789012345678901234567890",
                    "narHash": "sha256-nixpkgs-hash=",
                },
                "original": {"type": "github", "owner": "NixOS", "repo": "nixpkgs"},
            },
            "repo-map-src": {
                "locked": {
                    "type": "path",
                    "path": "/some/path/repo-map-src",
                    "narHash": "sha256-repo-map-hash=",
                },
                "original": {"type": "path", "path": "/some/path/repo-map-src"},
            },
            "security-nd": {
                "locked": {
                    "type": "git",
                    "url": "file:///some/path/security-nd",
                    "rev": "abcdefabcdefabcdefabcdefabcdefabcdefabcd",
                    "narHash": "sha256-security-hash=",
                },
                "original": {
                    "type": "git",
                    "url": "file:///some/path/security-nd",
                },
            },
            "localConfig": {
                "locked": {"type": "path", "path": "/scratch/eval/localConfig"},
                "original": {"type": "path", "path": "/scratch/eval/localConfig"},
            },
        },
    }
    (flake_dir / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")

    archive_output = {
        "path": "/nix/store/archive-root",
        "inputs": {
            "nixpkgs": {"path": "/nix/store/nixpkgs-store-path"},
            "repo-map-src": {"path": "/nix/store/repo-map-store-path"},
            "security-nd": {"path": "/nix/store/security-store-path"},
        },
    }

    path_info_output = {
        "/nix/store/nixpkgs-store-path": {"narHash": "sha256-nixpkgs-hash="},
        "/nix/store/repo-map-store-path": {"narHash": "sha256-repo-map-hash="},
        "/nix/store/security-store-path": {"narHash": "sha256-security-hash="},
    }

    outputs = {
        "locked source tree realization": json.dumps(archive_output),
        "source store path inspection": json.dumps(path_info_output),
    }
    workflow = MockWorkflow(outputs)

    result = derive_source_identities(workflow, flake_dir, ["--override-input", "localConfig", "path:/tmp"])
    assert result["verified_count"] == 3
    sources_by_name = {s["name"]: s for s in result["sources"]}
    assert "localConfig" not in sources_by_name
    assert sources_by_name["nixpkgs"]["narHash"] == "sha256-nixpkgs-hash="
    assert sources_by_name["repo-map-src"]["narHash"] == "sha256-repo-map-hash="
    assert sources_by_name["repo-map-src"]["type"] == "path"
    assert sources_by_name["security-nd"]["type"] == "git"
    assert sources_by_name["security-nd"]["revision"] == (
        "abcdefabcdefabcdefabcdefabcdefabcdefabcd"
    )

    # Verify archive command options
    archive_call = [c for c in workflow.calls if c[0] == "locked source tree realization"][0]
    archive_cmd = archive_call[1]
    assert "--dry-run" in archive_cmd
    assert "--json" in archive_cmd
    assert "--no-update-lock-file" in archive_cmd
    assert "--no-write-lock-file" in archive_cmd
    assert "--override-input" in archive_cmd
    assert all(call[1][0] != "git" for call in workflow.calls)


def test_non_local_config_override_rejected_before_nix(tmp_path: Path):
    workflow = MockWorkflow()
    with pytest.raises(ConfigError, match="cannot bind flake.overrides input other"):
        derive_source_identities(
            workflow,
            tmp_path,
            ["--override-input", "other", "path:/tmp/other"],
        )
    assert workflow.calls == []


def test_missing_flake_lock(tmp_path: Path):
    flake_dir = tmp_path / "empty"
    flake_dir.mkdir()
    workflow = MockWorkflow()
    with pytest.raises(ConfigError, match="has no flake.lock"):
        derive_source_identities(workflow, flake_dir, [])


def test_invalid_json_flake_lock(tmp_path: Path):
    flake_dir = tmp_path / "broken"
    flake_dir.mkdir()
    (flake_dir / "flake.lock").write_text("not json", encoding="utf-8")
    workflow = MockWorkflow()
    with pytest.raises(ConfigError, match="flake.lock is not valid JSON"):
        derive_source_identities(workflow, flake_dir, [])


def test_invalid_nodes_flake_lock(tmp_path: Path):
    flake_dir = tmp_path / "broken_nodes"
    flake_dir.mkdir()
    (flake_dir / "flake.lock").write_text(json.dumps({"nodes": "not a dict"}), encoding="utf-8")
    workflow = MockWorkflow()
    with pytest.raises(ConfigError, match="invalid nodes mapping"):
        derive_source_identities(workflow, flake_dir, [])


def test_node_not_object(tmp_path: Path):
    flake_dir = tmp_path / "node_not_obj"
    flake_dir.mkdir()
    (flake_dir / "flake.lock").write_text(json.dumps({"nodes": {"n1": "string"}}), encoding="utf-8")
    archive_output = {}
    workflow = MockWorkflow({"locked source tree realization": json.dumps(archive_output)})
    with pytest.raises(ConfigError, match="node n1 is not an object"):
        derive_source_identities(workflow, flake_dir, [])


def test_unpinned_node_rejected(tmp_path: Path):
    flake_dir = tmp_path / "unpinned"
    flake_dir.mkdir()
    lock_data = {
        "nodes": {
            "root": {"inputs": {"node1": "node1"}},
            "node1": {},
        }
    }
    (flake_dir / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    workflow = MockWorkflow({"locked source tree realization": "{}"})
    with pytest.raises(ConfigError, match="unpinned or missing locked metadata"):
        derive_source_identities(workflow, flake_dir, [])


def test_missing_nar_hash(tmp_path: Path):
    flake_dir = tmp_path / "no_nar"
    flake_dir.mkdir()
    lock_data = {
        "nodes": {
            "root": {"inputs": {"node1": "node1"}},
            "node1": {
                "locked": {
                    "type": "git",
                    "rev": "1234567890123456789012345678901234567890",
                    # no narHash
                }
            }
        }
    }
    (flake_dir / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    archive_output = {}
    workflow = MockWorkflow({"locked source tree realization": json.dumps(archive_output)})
    with pytest.raises(ConfigError, match="has no locked narHash"):
        derive_source_identities(workflow, flake_dir, [])


def test_raw_mutable_path_without_locked_nar_is_rejected(tmp_path: Path):
    flake_dir = tmp_path / "raw_path"
    flake_dir.mkdir()
    lock_data = {
        "nodes": {
            "root": {"inputs": {"mutable": "mutable"}},
            "mutable": {
                "locked": {"type": "path", "path": "/tmp/mutable-source"}
            },
        }
    }
    (flake_dir / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    workflow = MockWorkflow({"locked source tree realization": "{}"})
    with pytest.raises(ConfigError, match="has no locked narHash"):
        derive_source_identities(workflow, flake_dir, [])


def test_unsupported_input_type(tmp_path: Path):
    flake_dir = tmp_path / "bad_type"
    flake_dir.mkdir()
    lock_data = {
        "nodes": {
            "root": {"inputs": {"node1": "node1"}},
            "node1": {
                "locked": {
                    "type": "custom-unsupported-type",
                    "narHash": "sha256-hash=",
                }
            }
        }
    }
    (flake_dir / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    archive_output = {}
    workflow = MockWorkflow({"locked source tree realization": json.dumps(archive_output)})
    with pytest.raises(ConfigError, match="has unsupported type"):
        derive_source_identities(workflow, flake_dir, [])


def test_vcs_missing_revision(tmp_path: Path):
    flake_dir = tmp_path / "no_rev"
    flake_dir.mkdir()
    lock_data = {
        "nodes": {
            "root": {"inputs": {"node1": "node1"}},
            "node1": {
                "locked": {
                    "type": "git",
                    "narHash": "sha256-hash=",
                    # missing rev
                }
            }
        }
    }
    (flake_dir / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    archive_output = {}
    workflow = MockWorkflow({"locked source tree realization": json.dumps(archive_output)})
    with pytest.raises(ConfigError, match="exact 40-hex locked revision"):
        derive_source_identities(workflow, flake_dir, [])


def test_vcs_abbreviated_revision_is_rejected(tmp_path: Path):
    flake_dir = tmp_path / "short_rev"
    flake_dir.mkdir()
    lock_data = {
        "nodes": {
            "root": {"inputs": {"node1": "node1"}},
            "node1": {
                "locked": {
                    "type": "git",
                    "url": "file:///tmp/local-git-source",
                    "rev": "1234567",
                    "narHash": "sha256-hash=",
                }
            },
        }
    }
    (flake_dir / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    workflow = MockWorkflow({"locked source tree realization": "{}"})
    with pytest.raises(ConfigError, match="exact 40-hex locked revision"):
        derive_source_identities(workflow, flake_dir, [])


def test_unresolved_store_nar(tmp_path: Path):
    flake_dir = tmp_path / "unresolved_nar"
    flake_dir.mkdir()
    lock_data = {
        "nodes": {
            "root": {"inputs": {"node1": "node1"}},
            "node1": {
                "locked": {
                    "type": "github",
                    "owner": "o", "repo": "r",
                    "rev": "1234567890123456789012345678901234567890",
                    "narHash": "sha256-expected=",
                }
            }
        }
    }
    (flake_dir / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    archive_output = {"path": "/nix/store/archive-root", "inputs": {"node1": {"path": "/nix/store/p"}}}
    path_info_output = {"/nix/store/p": {"narHash": "sha256-actual-different="}}
    workflow = MockWorkflow({
        "locked source tree realization": json.dumps(archive_output),
        "source store path inspection": json.dumps(path_info_output),
    })
    with pytest.raises(ConfigError, match="source NAR hash for input node1 not resolved in nix store"):
        derive_source_identities(workflow, flake_dir, [])


def test_archive_command_failure(tmp_path: Path):
    flake_dir = tmp_path / "archive_fail"
    flake_dir.mkdir()
    (flake_dir / "flake.lock").write_text(json.dumps({"nodes": {}}), encoding="utf-8")
    workflow = MockWorkflow(failures={"locked source tree realization"})
    with pytest.raises(ConfigError, match="unable to resolve source tree with nix flake archive"):
        derive_source_identities(workflow, flake_dir, [])


def test_archive_invalid_json(tmp_path: Path):
    flake_dir = tmp_path / "archive_bad_json"
    flake_dir.mkdir()
    (flake_dir / "flake.lock").write_text(json.dumps({"nodes": {}}), encoding="utf-8")
    workflow = MockWorkflow({"locked source tree realization": "not json"})
    with pytest.raises(ConfigError, match="nix flake archive emitted invalid JSON"):
        derive_source_identities(workflow, flake_dir, [])


def test_path_info_command_failure(tmp_path: Path):
    flake_dir = tmp_path / "info_fail"
    flake_dir.mkdir()
    lock_data = {
        "nodes": {
            "root": {"inputs": {"n1": "n1"}},
            "n1": {
                "locked": {
                    "type": "path",
                    "path": "/p",
                    "narHash": "sha256-h=",
                }
            }
        }
    }
    (flake_dir / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    archive_output = {"path": "/nix/store/p"}
    workflow = MockWorkflow(
        outputs={"locked source tree realization": json.dumps(archive_output)},
        failures={"source store path inspection"},
    )
    with pytest.raises(ConfigError, match="unable to inspect source store paths with nix path-info"):
        derive_source_identities(workflow, flake_dir, [])


def test_path_info_invalid_json(tmp_path: Path):
    flake_dir = tmp_path / "info_bad_json"
    flake_dir.mkdir()
    lock_data = {
        "nodes": {
            "root": {"inputs": {"n1": "n1"}},
            "n1": {
                "locked": {
                    "type": "path",
                    "path": "/p",
                    "narHash": "sha256-h=",
                }
            }
        }
    }
    (flake_dir / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    archive_output = {"path": "/nix/store/p"}
    workflow = MockWorkflow({
        "locked source tree realization": json.dumps(archive_output),
        "source store path inspection": "bad json",
    })
    with pytest.raises(ConfigError, match="nix path-info emitted invalid JSON"):
        derive_source_identities(workflow, flake_dir, [])


def test_archive_binding_checks_named_path_when_nar_hashes_are_globally_present(
    tmp_path: Path,
):
    lock_data = {
        "root": "root",
        "nodes": {
            "root": {"inputs": {"first": "node-a", "second": "node-b"}},
            "node-a": {
                "locked": {
                    "type": "git",
                    "rev": "a" * 40,
                    "narHash": "sha256-a=",
                }
            },
            "node-b": {
                "locked": {
                    "type": "git",
                    "rev": "b" * 40,
                    "narHash": "sha256-b=",
                }
            },
        },
    }
    (tmp_path / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    archive = {
        "path": "/nix/store/archive-root",
        "inputs": {
            "first": {"path": "/nix/store/first-path"},
            "second": {"path": "/nix/store/second-path"},
        },
    }
    workflow = MockWorkflow(
        {
            "locked source tree realization": json.dumps(archive),
            "source store path inspection": json.dumps(
                {
                    "/nix/store/first-path": {"narHash": "sha256-b="},
                    "/nix/store/second-path": {"narHash": "sha256-a="},
                }
            ),
        }
    )
    with pytest.raises(ConfigError, match="source NAR hash for input node-a"):
        derive_source_identities(workflow, tmp_path, [])


def test_archive_binding_resolves_nested_lock_names_through_input_edges(tmp_path: Path):
    lock_data = {
        "root": "root",
        "nodes": {
            "root": {"inputs": {"outer-alias": "outer-node"}},
            "outer-node": {
                "inputs": {"inner-alias": "inner-node"},
                "locked": {
                    "type": "path",
                    "path": "/sources/outer",
                    "narHash": "sha256-outer=",
                },
            },
            "inner-node": {
                "locked": {
                    "type": "git",
                    "rev": "c" * 40,
                    "narHash": "sha256-inner=",
                }
            },
        },
    }
    (tmp_path / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    archive = {
        "path": "/nix/store/archive-root",
        "inputs": {
            "outer-alias": {
                "path": "/nix/store/outer-path",
                "inputs": {"inner-alias": {"path": "/nix/store/inner-path"}},
            }
        },
    }
    workflow = MockWorkflow(
        {
            "locked source tree realization": json.dumps(archive),
            "source store path inspection": json.dumps(
                {
                    "/nix/store/outer-path": {"narHash": "sha256-outer="},
                    "/nix/store/inner-path": {"narHash": "sha256-inner="},
                }
            ),
        }
    )
    result = derive_source_identities(workflow, tmp_path, [])
    assert {source["name"] for source in result["sources"]} == {"outer-node", "inner-node"}
    paths = {source["name"]: source["store_path"] for source in result["sources"]}
    assert paths == {"outer-node": "/nix/store/outer-path", "inner-node": "/nix/store/inner-path"}


def test_archive_binding_accepts_omitted_follows_reusing_direct_nodes(tmp_path: Path):
    lock_data = {
        "root": "root",
        "nodes": {
            "root": {"inputs": {"direct": "direct-node", "consumer": "consumer-node"}},
            "direct-node": {
                "locked": {
                    "type": "git",
                    "rev": "d" * 40,
                    "narHash": "sha256-direct=",
                }
            },
            "consumer-node": {
                "inputs": {"shared": ["direct"]},
                "locked": {
                    "type": "path",
                    "path": "/sources/consumer",
                    "narHash": "sha256-consumer=",
                },
            },
        },
    }
    (tmp_path / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    archive = {
        "path": "/nix/store/archive-root",
        "inputs": {
            "direct": {"path": "/nix/store/direct-path"},
            "consumer": {"path": "/nix/store/consumer-path"},
        },
    }
    workflow = MockWorkflow(
        {
            "locked source tree realization": json.dumps(archive),
            "source store path inspection": json.dumps(
                {
                    "/nix/store/direct-path": {"narHash": "sha256-direct="},
                    "/nix/store/consumer-path": {"narHash": "sha256-consumer="},
                }
            ),
        }
    )
    result = derive_source_identities(workflow, tmp_path, [])
    assert result["verified_count"] == 2


def test_archive_binding_rejects_a_named_node_bound_to_two_paths(tmp_path: Path):
    lock_data = {
        "root": "root",
        "nodes": {
            "root": {"inputs": {"first": "shared-node", "second": "shared-node"}},
            "shared-node": {
                "locked": {
                    "type": "path",
                    "path": "/sources/shared",
                    "narHash": "sha256-shared=",
                }
            },
        },
    }
    (tmp_path / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    archive = {
        "path": "/nix/store/archive-root",
        "inputs": {
            "first": {"path": "/nix/store/first-path"},
            "second": {"path": "/nix/store/second-path"},
        },
    }
    workflow = MockWorkflow(
        {
            "locked source tree realization": json.dumps(archive),
            "source store path inspection": "{}",
        }
    )
    with pytest.raises(ConfigError, match="source input shared-node has no unique archive graph binding"):
        derive_source_identities(workflow, tmp_path, [])


def test_archive_binding_rejects_a_missing_named_node(tmp_path: Path):
    lock_data = {
        "root": "root",
        "nodes": {
            "root": {"inputs": {"missing": "missing-node"}},
            "missing-node": {
                "locked": {
                    "type": "git",
                    "rev": "e" * 40,
                    "narHash": "sha256-missing=",
                }
            },
        },
    }
    (tmp_path / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    archive = {"path": "/nix/store/archive-root", "inputs": {}}
    workflow = MockWorkflow(
        {
            "locked source tree realization": json.dumps(archive),
            "source store path inspection": "{}",
        }
    )
    with pytest.raises(ConfigError, match="source input missing-node has no unique archive graph binding"):
        derive_source_identities(workflow, tmp_path, [])


def test_collect_store_paths_nested():
    data = {
        "p1": "/nix/store/1111-a",
        "nested": [
            {"path": "/nix/store/2222-b"},
            "/nix/store/not-in-path-key",
            {"other": "string"},
        ],
        "more": {"path": "/nix/store/3333-c"},
    }
    results = set()
    _collect_store_paths(data, results)
    assert results == {"/nix/store/2222-b", "/nix/store/3333-c"}


def test_derive_source_identities_default_legacy_uses_evaluated_flake(tmp_path: Path):
    """Direct callers without selected_flake_identity report the evaluated flake path."""
    evaluated_dir = tmp_path / "evaluated_scratch"
    evaluated_dir.mkdir()
    lock_data = {
        "version": 7,
        "root": "root",
        "nodes": {
            "root": {"inputs": {"dep": "dep"}},
            "dep": {
                "locked": {
                    "type": "git",
                    "url": "file:///path/dep",
                    "rev": "a" * 40,
                    "narHash": "sha256-hash1=",
                }
            },
        },
    }
    (evaluated_dir / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    archive_output = {"path": "/nix/store/root", "inputs": {"dep": {"path": "/nix/store/dep-p"}}}
    path_info_output = {"/nix/store/dep-p": {"narHash": "sha256-hash1="}}
    workflow = MockWorkflow({
        "locked source tree realization": json.dumps(archive_output),
        "source store path inspection": json.dumps(path_info_output),
    })

    result = derive_source_identities(workflow, evaluated_dir, [])
    assert result["selected_flake"] == str(evaluated_dir.resolve(strict=False))
    assert result["verified_count"] == 1
    # Verify commands executed against evaluated_dir
    archive_call = [c for c in workflow.calls if c[0] == "locked source tree realization"][0]
    assert archive_call[2] == evaluated_dir


def test_derive_source_identities_durable_selected_identity_separation(tmp_path: Path):
    """Providing selected_flake_identity durable path reports canonical path while Nix evaluates scratch."""
    scratch_dir = tmp_path / "ephemeral_scratch"
    scratch_dir.mkdir()
    canonical_dir = tmp_path / "canonical_composition"
    canonical_dir.mkdir()
    lock_data = {
        "version": 7,
        "root": "root",
        "nodes": {
            "root": {"inputs": {"dep": "dep"}},
            "dep": {
                "locked": {
                    "type": "git",
                    "url": "file:///path/dep",
                    "rev": "b" * 40,
                    "narHash": "sha256-hash2=",
                }
            },
        },
    }
    (scratch_dir / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    archive_output = {"path": "/nix/store/root", "inputs": {"dep": {"path": "/nix/store/dep-p2"}}}
    path_info_output = {"/nix/store/dep-p2": {"narHash": "sha256-hash2="}}
    workflow = MockWorkflow({
        "locked source tree realization": json.dumps(archive_output),
        "source store path inspection": json.dumps(path_info_output),
    })

    # Pass as Path
    result = derive_source_identities(
        workflow,
        scratch_dir,
        [],
        selected_flake_identity=canonical_dir,
    )
    assert result["selected_flake"] == str(canonical_dir.resolve(strict=False))
    assert result["selected_flake"] != str(scratch_dir.resolve(strict=False))
    assert result["verified_count"] == 1
    # Verify commands ran against scratch_dir, not canonical_dir
    archive_call = [c for c in workflow.calls if c[0] == "locked source tree realization"][0]
    assert archive_call[2] == scratch_dir

    # Pass as str
    result_str = derive_source_identities(
        workflow,
        scratch_dir,
        [],
        selected_flake_identity=str(canonical_dir),
    )
    assert result_str["selected_flake"] == str(canonical_dir.resolve(strict=False))


TARBALL_NAR = "sha256-8FJsFqr1zQPaFYCzHKD9el3kk5Y2qOoXKfHV1RKrGrQ="
OTHER_NAR = "sha256-2SmKENGwc1g33EvYXaxkGw887yekfl1TpU8vP1svz/o="
TARBALL_URL = "https://github.com/nix-community/home-manager/archive/release-25.05.tar.gz"


def _tarball_case(tmp_path: Path, *, tarball=None, path_info=None, archive=None, root_inputs=None):
    """Composition-shaped lock: github nixpkgs, tarball home-manager following it."""
    tarball_locked = {"type": "tarball", "url": TARBALL_URL, "narHash": TARBALL_NAR}
    if tarball is not None:
        tarball_locked = tarball
    lock_data = {
        "version": 7,
        "root": "root",
        "nodes": {
            "root": {
                "inputs": root_inputs
                or {
                    "nixpkgs": "nixpkgs",
                    "home-manager": "home-manager",
                    "localConfig": "localConfig",
                }
            },
            "nixpkgs": {
                "locked": {
                    "type": "github",
                    "owner": "NixOS",
                    "repo": "nixpkgs",
                    "rev": "1" * 40,
                    "narHash": "sha256-nixpkgs-hash=",
                }
            },
            "home-manager": {
                "inputs": {"nixpkgs": ["nixpkgs"]},
                "locked": tarball_locked,
                "original": {"type": "tarball", "url": TARBALL_URL},
            },
            "localConfig": {
                "locked": {"type": "path", "path": "/scratch/eval/localConfig"},
            },
        },
    }
    lock_path = tmp_path / "flake.lock"
    lock_path.write_text(json.dumps(lock_data), encoding="utf-8")
    if archive is None:
        archive = {
            "path": "/nix/store/archive-root",
            "inputs": {
                "nixpkgs": {"path": "/nix/store/nixpkgs-path"},
                "home-manager": {"path": "/nix/store/home-manager-path"},
            },
        }
    if path_info is None:
        path_info = {
            "/nix/store/nixpkgs-path": {"narHash": "sha256-nixpkgs-hash="},
            "/nix/store/home-manager-path": {"narHash": TARBALL_NAR},
        }
    workflow = MockWorkflow({
        "locked source tree realization": json.dumps(archive),
        "source store path inspection": json.dumps(path_info),
    })
    return workflow, lock_path


def test_hash_pinned_https_tarball_is_bound_and_verified(tmp_path: Path):
    workflow, lock_path = _tarball_case(tmp_path)
    lock_bytes = lock_path.read_bytes()

    result = derive_source_identities(
        workflow, tmp_path, ["--override-input", "localConfig", "path:/tmp/projection"]
    )

    assert result["verified_count"] == 2
    sources = {source["name"]: source for source in result["sources"]}
    assert "localConfig" not in sources
    assert sources["home-manager"] == {
        "name": "home-manager",
        "type": "tarball",
        "revision": None,
        "narHash": TARBALL_NAR,
        "store_path": "/nix/store/home-manager-path",
        "url": TARBALL_URL,
    }
    assert sources["nixpkgs"]["revision"] == "1" * 40
    assert [call[0] for call in workflow.calls] == [
        "locked source tree realization",
        "source store path inspection",
    ]
    archive_argv = workflow.calls[0][1]
    assert archive_argv[:3] == ["nix", "flake", "archive"]
    for flag in ("--dry-run", "--no-update-lock-file", "--no-write-lock-file"):
        assert flag in archive_argv
    assert lock_path.read_bytes() == lock_bytes


def test_hash_pinned_tarball_records_an_exact_locked_revision(tmp_path: Path):
    tarball = {"type": "tarball", "url": TARBALL_URL, "narHash": TARBALL_NAR, "rev": "f" * 40}
    workflow, _ = _tarball_case(tmp_path, tarball=tarball)
    result = derive_source_identities(workflow, tmp_path, [])
    sources = {source["name"]: source for source in result["sources"]}
    assert sources["home-manager"]["revision"] == "f" * 40


def test_hash_pinned_tarball_accepts_an_immutable_query_url(tmp_path: Path):
    url = "https://api.flakehub.com/f/pinned/example/home-manager/0.1/abc.tar.gz?rev=" + "a" * 40
    tarball = {"type": "tarball", "url": url, "narHash": TARBALL_NAR}
    workflow, _ = _tarball_case(tmp_path, tarball=tarball)
    result = derive_source_identities(workflow, tmp_path, [])
    sources = {source["name"]: source for source in result["sources"]}
    assert sources["home-manager"]["url"] == url


def test_hash_pinned_tarball_store_nar_mismatch_is_rejected(tmp_path: Path):
    workflow, _ = _tarball_case(
        tmp_path,
        path_info={
            "/nix/store/nixpkgs-path": {"narHash": "sha256-nixpkgs-hash="},
            "/nix/store/home-manager-path": {"narHash": OTHER_NAR},
        },
    )
    with pytest.raises(ConfigError, match="source NAR hash for input home-manager not resolved"):
        derive_source_identities(workflow, tmp_path, [])


def test_hash_pinned_tarball_rejects_a_coincidental_global_nar(tmp_path: Path):
    """Another bound path carrying the expected NAR must not satisfy the tarball node."""
    lock_data = {
        "root": "root",
        "nodes": {
            "root": {"inputs": {"extra": "extra", "home-manager": "home-manager"}},
            "extra": {
                "locked": {"type": "path", "path": "/sources/extra", "narHash": TARBALL_NAR},
            },
            "home-manager": {
                "locked": {"type": "tarball", "url": TARBALL_URL, "narHash": TARBALL_NAR},
            },
        },
    }
    (tmp_path / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    archive = {
        "path": "/nix/store/archive-root",
        "inputs": {
            "extra": {"path": "/nix/store/extra-path"},
            "home-manager": {"path": "/nix/store/home-manager-path"},
        },
    }
    workflow = MockWorkflow({
        "locked source tree realization": json.dumps(archive),
        "source store path inspection": json.dumps({
            "/nix/store/extra-path": {"narHash": TARBALL_NAR},
            "/nix/store/home-manager-path": {"narHash": OTHER_NAR},
        }),
    })
    with pytest.raises(ConfigError, match="source NAR hash for input home-manager not resolved"):
        derive_source_identities(workflow, tmp_path, [])


def test_hash_pinned_tarball_without_archive_binding_is_rejected(tmp_path: Path):
    archive = {"path": "/nix/store/archive-root", "inputs": {"nixpkgs": {"path": "/nix/store/nixpkgs-path"}}}
    workflow, _ = _tarball_case(tmp_path, archive=archive)
    with pytest.raises(ConfigError, match="source input home-manager has no unique archive graph binding"):
        derive_source_identities(workflow, tmp_path, [])


def test_hash_pinned_tarball_bound_to_two_paths_is_rejected(tmp_path: Path):
    archive = {
        "path": "/nix/store/archive-root",
        "inputs": {
            "nixpkgs": {"path": "/nix/store/nixpkgs-path"},
            "home-manager": {"path": "/nix/store/home-manager-path"},
            "hm-alias": {"path": "/nix/store/home-manager-other-path"},
        },
    }
    workflow, _ = _tarball_case(
        tmp_path,
        root_inputs={
            "nixpkgs": "nixpkgs",
            "home-manager": "home-manager",
            "hm-alias": "home-manager",
            "localConfig": "localConfig",
        },
        archive=archive,
        path_info={
            "/nix/store/nixpkgs-path": {"narHash": "sha256-nixpkgs-hash="},
            "/nix/store/home-manager-path": {"narHash": TARBALL_NAR},
            "/nix/store/home-manager-other-path": {"narHash": TARBALL_NAR},
        },
    )
    with pytest.raises(ConfigError, match="source input home-manager has no unique archive graph binding"):
        derive_source_identities(workflow, tmp_path, [])


def test_unreferenced_tarball_node_is_not_accepted_by_matching_store_nar(tmp_path: Path):
    """A lock node with no edge from root stays unbound even if its NAR is in the store."""
    lock_data = {
        "root": "root",
        "nodes": {
            "root": {"inputs": {"nixpkgs": "nixpkgs"}},
            "nixpkgs": {
                "locked": {"type": "github", "owner": "o", "repo": "r", "rev": "1" * 40, "narHash": TARBALL_NAR},
            },
            "stray": {
                "locked": {"type": "tarball", "url": TARBALL_URL, "narHash": TARBALL_NAR},
            },
        },
    }
    (tmp_path / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    archive = {"path": "/nix/store/archive-root", "inputs": {"nixpkgs": {"path": "/nix/store/nixpkgs-path"}}}
    workflow = MockWorkflow({
        "locked source tree realization": json.dumps(archive),
        "source store path inspection": json.dumps({"/nix/store/nixpkgs-path": {"narHash": TARBALL_NAR}}),
    })
    with pytest.raises(ConfigError, match="source input stray has no unique archive graph binding"):
        derive_source_identities(workflow, tmp_path, [])


_URL_ERROR = "hash-pinned tarball input home-manager must have a nonempty HTTPS URL"
_NAR_ERROR = "hash-pinned tarball input home-manager must have a valid sha256 locked narHash"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"url": None}, _URL_ERROR),
        ({"url": ""}, _URL_ERROR),
        ({"url": 7}, _URL_ERROR),
        ({"url": "http://github.com/nix-community/home-manager/archive/x.tar.gz"}, _URL_ERROR),
        ({"url": "file:///sources/home-manager.tar.gz"}, _URL_ERROR),
        ({"url": "https://user:secret@example.invalid/home-manager.tar.gz"}, _URL_ERROR),
        ({"url": "https://token@example.invalid/home-manager.tar.gz"}, _URL_ERROR),
        ({"url": "https://example.invalid/home-manager.tar.gz#subdir"}, _URL_ERROR),
        ({"url": "https://example.invalid/home-manager.tar.gz#"}, _URL_ERROR),
        ({"url": "https://example.invalid/home manager.tar.gz"}, _URL_ERROR),
        ({"url": "https://example.invalid/home-manager.tar.gz\n"}, _URL_ERROR),
        ({"url": "https://example.invalid/x\x00.tar.gz"}, _URL_ERROR),
        ({"url": "https:///home-manager.tar.gz"}, _URL_ERROR),
        ({"url": "https://example.invalid:notaport/home-manager.tar.gz"}, _URL_ERROR),
        ({"url": "HTTPS-ish://example.invalid/home-manager.tar.gz"}, _URL_ERROR),
        ({"url": "https://example.invalid/source.tar.gz?access_token=synthetic"}, "hash-pinned tarball input home-manager must not contain credential query fields"),
        ({"url": "https://example.invalid/source.tar.gz?%54OKEN=synthetic"}, "hash-pinned tarball input home-manager must not contain credential query fields"),
        ({"url": "https://example.invalid/source.tar.gz?X-Amz-Signature=synthetic"}, "hash-pinned tarball input home-manager must not contain credential query fields"),
        ({"url": "https://example.invalid/source.tar.gz?api_key="}, "hash-pinned tarball input home-manager must not contain credential query fields"),
        ({"narHash": "sha256-hash="}, _NAR_ERROR),
        ({"narHash": "sha512-" + "A" * 86 + "=="}, _NAR_ERROR),
        ({"narHash": "sha256:0" + "a" * 51}, _NAR_ERROR),
        ({"narHash": TARBALL_NAR + " "}, _NAR_ERROR),
        ({"narHash": [TARBALL_NAR]}, _NAR_ERROR),
        ({"rev": "abc"}, "hash-pinned tarball input home-manager has an invalid locked revision"),
        ({"rev": 1}, "hash-pinned tarball input home-manager has an invalid locked revision"),
    ],
)
def test_malformed_hash_pinned_tarball_metadata_is_rejected(tmp_path: Path, override, message):
    tarball = {"type": "tarball", "url": TARBALL_URL, "narHash": TARBALL_NAR}
    for key, value in override.items():
        if value is None:
            tarball.pop(key)
        else:
            tarball[key] = value
    workflow, _ = _tarball_case(tmp_path, tarball=tarball)
    with pytest.raises(ConfigError) as raised:
        derive_source_identities(workflow, tmp_path, [])
    assert str(raised.value) == message
    assert "example.invalid" not in str(raised.value)


def test_tarball_without_locked_nar_hash_keeps_the_existing_rejection(tmp_path: Path):
    workflow, _ = _tarball_case(tmp_path, tarball={"type": "tarball", "url": TARBALL_URL})
    with pytest.raises(ConfigError, match="flake input home-manager has no locked narHash"):
        derive_source_identities(workflow, tmp_path, [])


@pytest.mark.parametrize("input_type", ["file", "indirect", "TARBALL", ""])
def test_other_archive_like_types_remain_unsupported(tmp_path: Path, input_type):
    tarball = {"type": input_type, "url": TARBALL_URL, "narHash": TARBALL_NAR}
    workflow, _ = _tarball_case(tmp_path, tarball=tarball)
    with pytest.raises(ConfigError, match=f"flake input home-manager has unsupported type: {input_type}$"):
        derive_source_identities(workflow, tmp_path, [])


def test_tarball_support_does_not_relax_vcs_exact_revision(tmp_path: Path):
    lock_data = {
        "root": "root",
        "nodes": {
            "root": {"inputs": {"home-manager": "home-manager", "dep": "dep"}},
            "dep": {"locked": {"type": "git", "url": TARBALL_URL, "narHash": TARBALL_NAR}},
            "home-manager": {
                "locked": {"type": "tarball", "url": TARBALL_URL, "narHash": TARBALL_NAR},
            },
        },
    }
    (tmp_path / "flake.lock").write_text(json.dumps(lock_data), encoding="utf-8")
    workflow = MockWorkflow({"locked source tree realization": "{}"})
    with pytest.raises(ConfigError, match="pinned VCS input dep must have an exact 40-hex locked revision"):
        derive_source_identities(workflow, tmp_path, [])
