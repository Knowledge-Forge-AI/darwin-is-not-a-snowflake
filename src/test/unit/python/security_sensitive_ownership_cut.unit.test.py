# nix-darwin-test-source-target: flake.nix
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]


def test_security_sensitive_integration_is_absent_from_active_core() -> None:
    source = (ROOT / "flake.nix").read_text(encoding="utf-8")
    for forbidden in (
        "agentSecuritySourcePin",
        "agentSecuritySourceFetch",
        "agentSecuritySource",
        "agentSecurityPackageSetIdentity",
        "hostRepositoryIdentity",
        "agentSecurityConsumer",
        "services.agentSecurityConsumer",
        "pkgs.agent-security",
        "pkgs.agent-operations",
        "pkgs.agent-security-consumer",
        "modules/agent-operations.nix",
        "modules/agent-security-consumer.nix",
    ):
        assert forbidden not in source


def test_security_sensitive_owned_paths_are_absent() -> None:
    for relative in (
        "config/agent-security-consumer/source-pin.json",
        "config/agent-operations/gitconfig",
        "config/agent-operations/source-provenance.json",
        "packages/agent-security/default.nix",
        "packages/agent-security-consumer/default.nix",
        "modules/agent-operations.nix",
        "modules/agent-security-consumer.nix",
        "src/test/int/bash/agent-operations-package.int.test.bats",
    ):
        assert not (ROOT / relative).exists()
