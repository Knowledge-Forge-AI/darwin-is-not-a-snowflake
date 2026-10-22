# Nix Ruby PATH Precedence Design

Date: 2026-07-11
Status: approved for implementation

## Objective

Make the Nix-managed Ruby environment the default Ruby toolchain in interactive
Bash and Zsh sessions while retaining explicit access to the Ruby selected by
rbenv.

## Current Behavior

The common PATH configuration places `/run/current-system/sw/bin` before
Homebrew. A later shell fragment runs `rbenv init -`, which prepends
`$RBENV_ROOT/shims` and causes rbenv Ruby to shadow the Nix-managed Ruby.

The pyenv configuration already avoids global shim initialization and exposes
an explicit `pyenv-python` alias for the managed-language escape hatch.

## Design

The rbenv shell fragment will continue to export `RBENV_ROOT` but will no
longer evaluate `rbenv init -`. This preserves the Nix-first PATH established by
the earlier common PATH fragment.

The common aliases file will expose:

```sh
alias rbenv-ruby='RBENV_VERSION=$(rbenv global) rbenv exec ruby'
```

The alias selects the configured global rbenv version explicitly and invokes
its Ruby without adding rbenv shims to the global PATH. This mirrors the
existing `pyenv-python` behavior.

## Alternatives Considered

Re-prepending Nix paths after `rbenv init -` was rejected because it duplicates
PATH policy and depends on shell-fragment ordering. Keeping global rbenv shims
and wrapping only `ruby` was rejected because other rbenv-provided executables
would continue to shadow Nix-managed tools.

## Verification

Unit coverage will prove that:

- the common rbenv fragment does not invoke `rbenv init`;
- the common aliases file defines the explicit `rbenv-ruby` alias;
- sourcing the relevant shell fragments with a fake rbenv does not prepend its
  shim directory;
- the explicit alias invokes the rbenv-managed Ruby.

The focused shell tests will be run first, followed by the repository's full
host-safe and Darwin-build validation.

## Scope

No Ruby versions, packages, Homebrew declarations, activation behavior, or
flake inputs will change. Existing unrelated staged changes will remain outside
the implementation commit.
