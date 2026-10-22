# Nix-Managed Node Environment Design

Date: 2026-07-11
Status: approved for implementation

## Objective

Make the Node.js runtime and selected Node developer CLIs from pinned nixpkgs
available through the nix-darwin system environment, with Nix-managed Node
taking precedence over NVM-managed Node in interactive Bash and Zsh sessions.
Retain explicit access to the NVM default Node version through an alias.

## Selected Packages

The managed environment will use only direct attributes from the pinned
nixpkgs package set:

- `pkgs.nodejs` (`22.22.2`) for `node`, `npm`, and `npx`;
- `pkgs.nodePackages."@angular/cli"` (`19.2.3`) for `ng`;
- `pkgs.nodePackages."@nestjs/cli"` (`11.0.16`) for `nest`;
- `pkgs.playwright-test` (`1.56.1`) for `playwright`.

The flake will define one `mkManagedNode` helper that returns a `buildEnv`
containing these derivations. The same helper will supply the Darwin system
package and the independently buildable `nixDarwinManagedNode` flake output.
The package list will not be repeated elsewhere.

## PATH Precedence And NVM Access

The common shell configuration loads NVM before the common PATH fragment
prepends `/run/current-system/sw/bin`. Installing the managed Node environment
therefore makes Nix-managed `node`, `npm`, `npx`, and the selected CLIs win
normal command lookup without adding another PATH mutation.

The common aliases file will expose:

```sh
alias nvm-node='nvm exec --silent default node'
```

The alias invokes the Node version selected by NVM's `default` alias without
placing NVM's Node directory ahead of the managed system environment. It will
fail normally if NVM or its default Node version is unavailable; it will not
silently fall back to Nix-managed Node.

## Dependency Boundary

This workstation configuration manages the Node runtime and shared developer
executables only. Individual JavaScript and TypeScript repositories will keep
application and test dependencies in `package.json` and their selected
package-manager lockfile. The managed environment will not set `NODE_PATH`,
install global npm libraries, or run `npm install` or `npx` during activation.

The following requested tools are deferred because the pinned package set does
not expose suitable direct package attributes:

- Jest;
- Vitest;
- `create-vite` for React project generation;
- `express-generator`;
- third-party debug tooling.

## Alternatives Considered

A lockfile-backed `buildNpmPackage` bundle was considered for the missing CLIs
and deferred at the user's direction. Generated `node2nix` packages were
rejected because they would add substantial generated package maintenance.
Runtime `npx` downloads were rejected because they are not reproducible or
centrally Nix-managed.

Keeping NVM's Node directory first in PATH and wrapping only `node` was rejected
because `npm`, `npx`, and NVM-installed executables could still shadow managed
tools. Removing NVM was rejected because the explicit compatibility path is a
requirement.

## Verification

Nix unit coverage will prove that:

- `nixDarwinManagedNode` exists and is a derivation;
- `mkManagedNode` uses the four selected pinned-nixpkgs derivations;
- the Darwin system package configuration installs the helper result;
- the implementation does not add Homebrew Node packages, global npm installs,
  a custom npm bundle, or `NODE_PATH`.

Shell unit coverage will prove that:

- the Nix system profile remains ahead of an NVM Node directory after the
  common shell fragments are sourced;
- `nvm-node` dispatches through `nvm exec --silent default node`.

Validation will build `nixDarwinManagedNode`, run direct version checks for
`node`, `npm`, `npx`, `ng`, `nest`, and `playwright`, run `nix flake check`, run
the report and coverage gate, and build the Darwin system configuration. The
existing `flake.lock` must not change as part of this implementation.

## Documentation

The README will document the normal `nix-rebuild` application path, direct
version checks, the independently buildable flake output, Nix-first PATH
behavior, the `nvm-node` escape hatch, the repository dependency boundary, and
the deferred CLI list.

## Scope

No NVM installation, NVM activation behavior, Homebrew package, flake input,
or unrelated language runtime will change. Existing unrelated staged changes
will remain outside the implementation commit.
