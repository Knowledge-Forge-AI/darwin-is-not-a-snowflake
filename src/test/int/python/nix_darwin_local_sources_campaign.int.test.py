# nix-darwin-test-source-target: libexec/nix_darwin_local/config.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/local_source.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_campaign.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_campaign_state.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_plan.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_source.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_topology.py
# nix-darwin-test-source-target: libexec/nix_darwin_local/promotion_workflow.py
"""Provider-free real-Nix campaign over a local source catalog.

The fixture deliberately keeps canonical repository identities unreachable and
never configures a Git remote.  Nix lock metadata is generated from the
disposable repositories before the URLs are rewritten to their portable HTTPS
identities; all hashes remain the values emitted by Nix.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CORE_ROOT / "libexec"))

from nix_darwin_local import deployment_workflow
from nix_darwin_local import promotion_campaign
from nix_darwin_local import promotion_workflow
from nix_darwin_local.config import ControlConfig
from nix_darwin_local.deployment_workflow import Workflow
from nix_darwin_local.local_source import materialize_source
from nix_darwin_local.promotion_campaign_state import load_campaign
from nix_darwin_local.promotion_plan import load_plan
from nix_darwin_local.promotion_source import load_source_receipt
from nix_darwin_local.source_catalog import SourceCatalogEntry


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("nix") is None,
    reason="required git or nix executable is unavailable",
)


def _run(cwd: Path, *argv: str) -> str:
    result = subprocess.run(
        list(argv),
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "git", "init", "--initial-branch=main")
    _run(path, "git", "config", "user.name", "DINAS local campaign fixture")
    _run(path, "git", "config", "user.email", "fixture@example.invalid")


def _commit(path: Path, message: str) -> str:
    _run(path, "git", "add", "-A")
    _run(path, "git", "commit", "-m", message)
    return _run(path, "git", "rev-parse", "HEAD")


def _write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)


def _canonical_url(name: str) -> str:
    return f"https://unreachable.invalid/dinas/{name}.git"


def _core_flake() -> str:
    return """{
  description = "local core subflake fixture";
  outputs = { self }: {
    marker = builtins.readFile ./payload.txt;
  };
}
"""


def _public_contract(url: str, module_url: str) -> dict:
    return {
        "schema": "composition-nd-promotion-v1",
        "version": 2,
        "composition": {"name": "composition-nd", "repositoryUrl": url},
        "system": "__SYSTEM__",
        "modules": [{
            "name": "domain-adapter",
            "input": "domain",
            "url": module_url,
            "checkout": "domain",
            "bootstrapStrategy": "none",
            "ownsMachineLocalConfiguration": False,
        }],
        "packageSetIdentity": "/nix/store/00000000000000000000000000000000-dinas-fixture",
        "hosts": ["campaign-host"],
        "checks": ["public-check"],
        "qualification": {"entryPoint": "tools/qualify-promotion", "mode": "exact-candidate"},
        "promotableInputs": {"rule": "registered-direct-inputs-only", "names": ["domain"]},
        "releaseChannels": {
            "policy": "explicit-head-refs-v1",
            "refs": {"domain": "refs/heads/main"},
        },
    }


def _public_flake(system: str, core_input: str, domain_input: str, contract: dict) -> str:
    contract_nix = json.dumps({**contract, "system": system}, separators=(",", ":"))
    return f'''{{
  description = "provider-free public composition fixture";
  inputs.core.url = {json.dumps(core_input)};
  inputs.domain.url = {json.dumps(domain_input)};
  outputs = inputs:
    let
      system = {json.dumps(system)};
      checked = builtins.derivation {{
        name = "dinas-public-source-dependent";
        inherit system;
        builder = "/bin/sh";
        args = [ "-c" "test -f ${{inputs.core.outPath}}/payload.txt && test -f ${{inputs.domain.outPath}}/payload.txt && /bin/mkdir -p $out && /bin/cat ${{inputs.core.outPath}}/payload.txt > $out/core-payload && /bin/cat ${{inputs.domain.outPath}}/payload.txt > $out/domain-payload" ];
      }};
    in {{
      lib.promotion = builtins.fromJSON ''{contract_nix}'';
      checks.${{system}}.public-check = checked;
      darwinConfigurations.campaign-host.system = checked;
    }};
}}
'''


def _private_flake(
    system: str,
    public_input: str,
    private_module_input: str,
    public_contract: dict,
    private_url: str,
    private_module_url: str,
) -> str:
    public_actual = {**public_contract, "system": system}
    private_contract = {
        "schema": "composition-private-nd-promotion-v1",
        "version": 2,
        "composition": {"name": "composition-private-nd", "repositoryUrl": private_url},
        "system": system,
        "modules": [{
            "name": "private-adapter",
            "input": "private-module",
            "url": private_module_url,
            "checkout": "private-module",
            "bootstrapStrategy": "none",
            "ownsMachineLocalConfiguration": False,
        }],
        "packageSetIdentity": public_contract["packageSetIdentity"],
        "hosts": ["campaign-host"],
        "checks": ["private-check"],
        "qualification": {"entryPoint": "tools/qualify-promotion", "mode": "exact-candidate"},
        "promotableInputs": {
            "rule": "registered-direct-lower-composition-only",
            "names": ["private-module", "composition-nd"],
        },
        "releaseChannels": {
            "policy": "explicit-head-refs-v1",
            "refs": {
                "private-module": "refs/heads/main",
                "composition-nd": "refs/heads/main",
            },
        },
        "requiredLowerPromotion": {
            "schema": public_contract["schema"],
            "version": public_contract["version"],
            "candidateInput": "composition-nd",
        },
        "lowerPromotion": public_actual,
        "combinedModules": public_contract["modules"] + [{
            "name": "private-adapter",
            "input": "private-module",
            "url": private_module_url,
            "checkout": "private-module",
            "bootstrapStrategy": "none",
            "ownsMachineLocalConfiguration": False,
        }],
        "semantics": {"nonActivation": True, "requiresExplicitLockSelection": True},
    }
    contract_nix = json.dumps(private_contract, separators=(",", ":"))
    return f'''{{
  description = "provider-free private composition fixture";
  inputs."composition-nd".url = {json.dumps(public_input)};
  inputs."private-module".url = {json.dumps(private_module_input)};
  outputs = inputs:
    let
      system = {json.dumps(system)};
      checked = builtins.derivation {{
        name = "dinas-private-source-dependent";
        inherit system;
        builder = "/bin/sh";
        args = [ "-c" "test -f ${{inputs."composition-nd".outPath}}/flake.nix && test -f ${{inputs."private-module".outPath}}/payload.txt && /bin/mkdir -p $out && /bin/cat ${{inputs."private-module".outPath}}/payload.txt > $out/private-payload" ];
      }};
    in {{
      lib.promotion = builtins.fromJSON ''{contract_nix}'';
      checks.${{system}}.private-check = checked;
      darwinConfigurations.campaign-host.system = checked;
    }};
}}
'''


def _rewrite_lock_urls(path: Path, replacements: dict[Path, str]) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    for node in document.get("nodes", {}).values():
        if not isinstance(node, dict):
            continue
        for section in ("original", "locked"):
            record = node.get(section)
            if not isinstance(record, dict) or not isinstance(record.get("url"), str):
                continue
            value = record["url"]
            parsed = urlsplit(value)
            local = Path(unquote(parsed.path)).resolve()
            for checkout, canonical in replacements.items():
                if local == checkout.resolve():
                    query = parse_qs(parsed.query)
                    if "dir" in query and "dir" not in record:
                        record["dir"] = query["dir"][0]
                    if "ref" in query and "ref" not in record:
                        record["ref"] = query["ref"][0]
                    record["url"] = canonical
                    break
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _rewrite_input_urls(path: Path, replacements: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    for local, canonical in replacements.items():
        text = text.replace(local, canonical)
    path.write_text(text, encoding="utf-8")


def _copy_execution_tool(root: Path) -> Path:
    _init_repo(root)
    tool = root / "nix-darwin"
    (tool / "bin").mkdir(parents=True)
    shutil.copy2(CORE_ROOT / "bin/nix-update", tool / "bin/nix-update")
    libexec = CORE_ROOT / "libexec/nix_darwin_local"
    shutil.copytree(
        libexec,
        tool / "libexec/nix_darwin_local",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    _write(tool / "flake.nix", "{ outputs = { self }: {}; }\n")
    _commit(root, "commit the exact fixture execution tool")
    return tool


class _RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path]] = []

    def __call__(self, argv, cwd=None, **kwargs):
        args = list(argv)
        root = Path(cwd)
        self.calls.append((args, root))
        if args and args[0] == "git" and any(
            operation in {"push", "ls-remote"} for operation in args[1:]
        ):
            raise AssertionError(f"promotion attempted forbidden network Git operation: {args}")
        if args[0] in {"sudo", "darwin-rebuild"} or "switch" in args:
            raise AssertionError(f"promotion attempted host activation: {args}")
        return subprocess.run(args, cwd=root, **kwargs)


@pytest.fixture(params=["root", "nested"])
def local_campaign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
):
    core_layout = request.param
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    for owner in (deployment_workflow, promotion_workflow, promotion_campaign):
        monkeypatch.setattr(owner, "_private_state_root", lambda state=state: state)
    monkeypatch.setattr(promotion_workflow, "_guard_processes", lambda *_: None)
    monkeypatch.setattr(promotion_campaign, "_has_controlling_terminal", lambda: True)
    monkeypatch.setattr(
        promotion_campaign,
        "_tty_confirmation",
        lambda prompt: prompt.split("'")[1],
    )

    system = _run(tmp_path, "nix", "eval", "--raw", "--impure", "--expr", "builtins.currentSystem")
    core_url = _canonical_url("core")
    domain_url = _canonical_url("domain")
    public_url = _canonical_url("composition-nd")
    private_url = _canonical_url("composition-private-nd")
    private_module_url = _canonical_url("private-module")

    execution_tool = _copy_execution_tool(tmp_path / "execution")

    core = tmp_path / "core-source"
    _init_repo(core)
    core_target_dir = core if core_layout == "root" else core / "nix-darwin"
    _write(core_target_dir / "flake.nix", _core_flake())
    _write(core_target_dir / "payload.txt", "core-old\n")
    old_core = _commit(core, f"core baseline ({core_layout})")

    domain = tmp_path / "domain-source"
    _init_repo(domain)
    _write(domain / "flake.nix", '{ outputs = { self }: { }; }\n')
    _write(domain / "payload.txt", "domain-old\n")
    old_domain = _commit(domain, "domain baseline")

    public = tmp_path / "composition-nd"
    _init_repo(public)
    public_contract = _public_contract(public_url, domain_url)
    core_selector = "?dir=nix-darwin" if core_layout == "nested" else ""
    core_query_ref = f"{core_selector}&ref=main" if core_selector else "?ref=main"
    public_local_core = f"git+file://{core.resolve()}{core_query_ref}"
    public_local_domain = f"git+file://{domain.resolve()}?ref=main"
    _write(public / "flake.nix", _public_flake(system, public_local_core, public_local_domain, public_contract))
    # The local-native qualification path binds this executable's identity but
    # does not execute the owner qualifier.
    _write(public / "tools/qualify-promotion", "#!/bin/sh\nexit 0\n", 0o755)
    _run(public, "git", "add", "flake.nix", "tools/qualify-promotion")
    _run(public, "nix", "flake", "lock")
    _rewrite_lock_urls(public / "flake.lock", {core: core_url, domain: domain_url})
    _rewrite_input_urls(public / "flake.nix", {
        public_local_core: f"git+{core_url}{core_query_ref}",
        public_local_domain: f"git+{domain_url}?ref=main",
    })
    public_base = _commit(public, "public composition baseline")

    private = tmp_path / "composition-private-nd"
    _init_repo(private)
    private_local_public = f"git+file://{public.resolve()}?ref=main"
    private_local_module = f"git+file://{(tmp_path / 'private-module-source').resolve()}?ref=main"
    private_module = tmp_path / "private-module-source"
    _init_repo(private_module)
    _write(private_module / "flake.nix", '{ outputs = { self }: { }; }\n')
    _write(private_module / "payload.txt", "private-old\n")
    old_private_module = _commit(private_module, "private module baseline")
    _write(
        private / "flake.nix",
        _private_flake(
            system,
            private_local_public,
            private_local_module,
            public_contract,
            private_url,
            private_module_url,
        ),
    )
    _write(private / "tools/qualify-promotion", "#!/bin/sh\nexit 0\n", 0o755)
    _run(private, "git", "add", "flake.nix", "tools/qualify-promotion")
    _run(private, "nix", "flake", "lock")
    _rewrite_lock_urls(private / "flake.lock", {public: public_url, private_module: private_module_url})
    _rewrite_input_urls(private / "flake.nix", {
        private_local_public: f"git+{public_url}?ref=main",
        private_local_module: f"git+{private_module_url}?ref=main",
    })
    private_base = _commit(private, "private composition baseline")

    entries = (
        SourceCatalogEntry("core", core, core_url, "refs/heads/main"),
        SourceCatalogEntry("domain", domain, domain_url, "refs/heads/main"),
        SourceCatalogEntry("composition-nd", public, public_url, "refs/heads/main"),
        SourceCatalogEntry("composition-private-nd", private, private_url, "refs/heads/main"),
        SourceCatalogEntry("private-module", private_module, private_module_url, "refs/heads/main"),
    )
    config = ControlConfig(workspace_root=tmp_path, sources=entries, present=True)
    recorder = _RecordingRunner()
    workflow = Workflow(execution_tool, recorder, display_command_output=False, sources=entries)

    # Warm all old inputs before any release ref moves.  The campaign then has
    # no network source available for its baseline or candidate trees.
    for entry, revision in (
        (entries[0], old_core),
        (entries[1], old_domain),
        (entries[2], public_base),
        (entries[3], private_base),
        (entries[4], old_private_module),
    ):
        materialize_source(workflow, entry, revision)

    _write(core_target_dir / "payload.txt", "core-new\n")
    new_core = _commit(core, f"core local release ({core_layout})")
    return SimpleNamespace(
        tmp=tmp_path,
        state=state,
        core=core,
        core_layout=core_layout,
        domain=domain,
        public=public,
        private=private,
        private_module=private_module,
        execution_tool=execution_tool,
        entries=entries,
        config=config,
        workflow=workflow,
        recorder=recorder,
        old_core=old_core,
        new_core=new_core,
        old_domain=old_domain,
        public_base=public_base,
        private_base=private_base,
        old_private_module=old_private_module,
        public_url=public_url,
        private_url=private_url,
    )


def _git(root: Path, *args: str) -> str:
    return _run(root, "git", *args)


def _locked(lock: dict, name: str) -> dict:
    root = lock["nodes"][lock["root"]]
    node_name = root["inputs"][name]
    return lock["nodes"][node_name]["locked"]


def _assert_portable_lock(path: Path, temporary_root: Path) -> dict:
    raw = path.read_bytes()
    assert b"file://" not in raw
    assert str(temporary_root).encode() not in raw
    lock = json.loads(raw)
    for node in lock["nodes"].values():
        locked = node.get("locked") if isinstance(node, dict) else None
        if isinstance(locked, dict) and locked.get("type") == "git":
            assert locked["url"].startswith("https://")
            assert locked["rev"] and len(locked["rev"]) == 40
            assert locked["narHash"].startswith("sha256-")
    return lock


def test_provider_free_local_source_campaign_promotes_exact_unpublished_chain(
    local_campaign,
):
    fixture = local_campaign
    output = fixture.tmp / "campaign"
    assert promotion_campaign.run_campaign(
        fixture.workflow,
        fixture.private,
        fixture.config,
        "core",
        fixture.new_core,
        output,
    ) == 0

    journal = load_campaign(output)
    assert journal["state"] == "LOCAL_COMPLETE_AWAITING_ACTIVATION"
    assert journal["activationPerformed"] is False
    assert [stage["name"] for stage in journal["stages"]] == [
        "core", "composition-nd", "composition-private-nd"
    ]
    assert all(stage["activation_state"] == "not_performed" for stage in journal["stages"])
    assert all(stage["publication_state"] == "unavailable" for stage in journal["stages"])
    assert all(stage["published_commit"] is None for stage in journal["stages"])

    core_stage, public_stage, private_stage = journal["stages"]
    expected_core_stage_repo = fixture.core if fixture.core_layout == "root" else fixture.core / "nix-darwin"
    assert core_stage["repository"] == str(expected_core_stage_repo.resolve())
    assert core_stage["status"] == "LOCAL_COMMITTED"
    assert core_stage["local_commit"] == fixture.new_core
    public_commit = public_stage["local_commit"]
    private_commit = private_stage["local_commit"]
    assert public_stage["status"] == private_stage["status"] == "LOCAL_COMMITTED"
    assert public_commit == _git(fixture.public, "rev-parse", "HEAD")
    assert private_commit == _git(fixture.private, "rev-parse", "HEAD")
    assert public_commit != fixture.public_base
    assert private_commit != fixture.private_base
    assert _git(fixture.public, "rev-list", "--count", f"{fixture.public_base}..HEAD") == "1"
    assert _git(fixture.private, "rev-list", "--count", f"{fixture.private_base}..HEAD") == "1"
    for root in (fixture.public, fixture.private):
        assert _git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") == "flake.lock"

    public_lock = _assert_portable_lock(fixture.public / "flake.lock", fixture.tmp)
    private_lock = _assert_portable_lock(fixture.private / "flake.lock", fixture.tmp)
    assert _locked(public_lock, "core")["rev"] == fixture.new_core
    if fixture.core_layout == "root":
        assert "dir" not in _locked(public_lock, "core")
    else:
        assert _locked(public_lock, "core").get("dir") == "nix-darwin"
    assert _locked(private_lock, "composition-nd")["rev"] == public_commit
    assert fixture.new_core.encode() in (fixture.private / "flake.lock").read_bytes()
    assert all(_git(root, "remote") == "" for root in (
        fixture.core, fixture.domain, fixture.public, fixture.private, fixture.private_module
    ))

    for stage in (public_stage, private_stage):
        attempt = stage["attempts"][0]
        artifact = load_plan(Path(attempt["plan_path"]))
        assert artifact.digest == attempt["plan_digest"]
        assert artifact.document["schema"] == "nix-update-promotion-plan-v4"
        assert artifact.document["local_ref"] == {"ref": "refs/heads/main", "revision": stage["revision"]}
        assert "publication" not in artifact.document
        assert artifact.document["local_sources"]
        for binding in artifact.document["local_sources"]:
            assert binding["schema"] == "nix-update-promotion-local-source-binding-v2"
            assert binding["role"] in {"exact", "unchanged-lock"}
            assert binding["realization"]["store_path"].startswith("/nix/store/")
            assert binding["realization"]["nar_hash"].startswith("sha256-")
        if stage["name"] == "composition-nd":
            assert artifact.document["closure_authorization"]["schema"] == "nix-update-core-lock-transition-v1"
            assert artifact.document["closure_authorization"]["subflake"] == (
                "" if fixture.core_layout == "root" else "nix-darwin"
            )
        result = json.loads((artifact.directory / "result.json").read_text(encoding="utf-8"))
        assert result["schema"] == "nix-update-promotion-result-v2"
        assert result["terminal_state"] == "LOCAL_PROMOTION_COMMITTED"
        assert result["local_commit"] == stage["local_commit"]
        assert result["published_commit"] is None
        assert result["publication_state"] == "unavailable"
        assert result["activation_state"] == "not_performed"

    core_receipt = load_source_receipt(Path(core_stage["attempts"][0]["plan_path"]))
    assert core_receipt.document["revision"] == fixture.new_core
    assert core_receipt.document["impact"] is None
    assert core_receipt.document["no_activation"] is True

    # The lock hashes are the exact Nix materializations of the local catalog
    # refs, while their committed URLs remain provider-neutral and portable.
    for entry, revision, lock, node_name in (
        (fixture.entries[0], fixture.new_core, public_lock, "core"),
        (fixture.entries[2], public_commit, private_lock, "composition-nd"),
    ):
        materialized = materialize_source(fixture.workflow, entry, revision)
        assert materialized.nar_hash == _locked(lock, node_name)["narHash"]
        assert materialized.store_path.startswith("/nix/store/")

    # Exercise the resulting unpublished chain with network disabled at Nix's
    # boundary; these checks only evaluate/build and never switch a host.
    for root in (fixture.public, fixture.private):
        _run(root, "nix", "flake", "check", "--offline", "--no-update-lock-file", "--no-write-lock-file")
        _run(
            root,
            "nix", "build", "--offline", "--no-link", "--no-update-lock-file",
            "--no-write-lock-file", "path:.#darwinConfigurations.campaign-host.system",
        )

    assert not any(args[:2] == ["git", "ls-remote"] for args, _ in fixture.recorder.calls)
    assert not any(args[:2] == ["git", "push"] for args, _ in fixture.recorder.calls)
    assert not any(
        args and args[0].endswith("tools/qualify-promotion")
        for args, _ in fixture.recorder.calls
    )
    assert not any(args[0] in {"sudo", "darwin-rebuild"} or "switch" in args for args, _ in fixture.recorder.calls)
