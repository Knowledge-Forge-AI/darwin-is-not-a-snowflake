# Darwin Is Not a Snowflake

**v0.0.1 public preview.** DINAS is a modular macOS workstation framework built
on nix-darwin. Core owns the bootstrap, shared package set, command namespace,
configuration projection, qualification and attended deployment machinery.
Domain adapters contribute their own modules through explicit contracts.

This first public release is a curated source snapshot. It does not include the
private development history, operator journals, credentials, personal host
identifiers or the operator's artwork. The example account is `operator`; the
example hosts are `example-workstation` and `example-primary`. Review and adapt
those examples before building a workstation configuration. This is a broad
workstation baseline, not a minimal installer or a security certification.

## Start with source and checks

The flake targets `aarch64-darwin`. It pins nixpkgs, nix-darwin and the public
RepoMap source. Nix must already be installed to run these commands:

```sh
nix flake show
nix flake check --no-build
nix build .#malskanner
./scripts/test-all.sh
```

`./scripts/test-all.sh --full` also builds a Darwin system. A build does not
activate it. Some checks need the declared development tools and macOS services;
see the test runner's help for individual suites.

`./tools/bootstrap/bootstrap-macos` is an attended bootstrap for a fresh Mac.
Read it first: it may install Apple Command Line Tools and multi-user Nix and
clone the public composition. It does not switch or activate a host. The full
[composition](https://github.com/Knowledge-Forge-AI/composition_dinas) retains
adapters whose repositories are not all public yet; see its access requirements.

## Operating model

`./bin/nix-update build` qualifies and builds the configured top-level flake.
`./bin/nix-update deploy --plan-only` creates a review artifact without activation.
An ordinary `deploy` requires attended confirmation before a privileged switch.
Promotion, source snapshot selection and host activation are separate operations.
Use a disposable configuration root when evaluating the preview; do not point it
at an existing workstation control root without reviewing its contents.

- [Development foundation](docs/guides/dinas-development-foundation.md)
- [Deployment and ownership](docs/ops/domain-change-deployment.md)
- [Architecture decisions](docs/adr/README.md)
- [Security reporting and audit limits](SECURITY.md)

Core declares developer tools through Nix and nix-darwin's Homebrew module.
Semgrep is declared as a Homebrew formula; Malskanner is a pinned Nix CLI package.
Malskanner's optional AI mode is not configured or enabled by Core. No scanner
service, MCP registration or account credential is installed by that addition.

## License and contributions

Original project code is offered under **AGPL-3.0-or-later**, with a separate
commercial license available from Samuel Leighton Lair at
[sam.lair@knowledge-forge.ai](mailto:sam.lair@knowledge-forge.ai).
Third-party components retain their own licenses and notices.

Read [LICENSE](LICENSE), [NOTICE](NOTICE),
[COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md),
[CONTRIBUTING.md](CONTRIBUTING.md) and [CLA.md](CLA.md) before contributing.
Outside contributors use the included pull request template and explicitly
accept the contribution terms. No package registry release accompanies this
source preview.
