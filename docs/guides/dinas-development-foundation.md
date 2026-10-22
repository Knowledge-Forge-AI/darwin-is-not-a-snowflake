# DINAS System-Wide Development Foundation

## Ownership doctrine

The system environment is primary. It provides always-available runtimes,
compilers, language servers, test runners, bootstrap CLIs, and diagnostics in
an ordinary new login shell without `nix develop`.

Native project manifests own exact dependencies and versions: `package.json`
and its lock, `pyproject.toml` and its lock or environment, `Gemfile` or a
gemspec and lock, `go.mod` or `go.work`, `Cargo.toml` and `Cargo.lock`, optional
`rust-toolchain.toml`, and Tauri configuration. React and ReactDOM are project
dependencies, never global dependency authority. Project-local npm scripts and
`node_modules/.bin` take precedence over system fallbacks.

Dev shells are optional and project-owned. A project can add one without a
central DINAS source edit and does not import private composition or module
state to obtain the baseline tools.

## Managed language environments

- Python includes the existing packages and Pillow plus pypistats 1.14.0. The
  pypistats sdist is pinned to SHA-256
  `d0ca68a2497e37c7b7416289f262a3b94111e637f83b1cda1d4931c7ab4b426c`.
  Its required termcolor 3.2.0 is packaged exactly because pinned nixpkgs has
  3.1.0. Activation never runs pip.
- Node uses the pinned Node 22 package and provides npm, Corepack, TypeScript,
  TypeScript Language Server, `tsx`, Vite, and Vitest. The system Vite/Vitest
  bundle is immutable; an exact-lock disposable fixture proves local npm
  script precedence, TypeScript compilation, Vitest execution, and ReactDOM
  server rendering.
- Rust provides `rustc`, Cargo, rustfmt, Clippy, rust-analyzer, pkg-config, and
  the pinned Tauri CLI major 2 as a fallback. Project-local
  `@tauri-apps/cli` or Cargo commands remain authoritative. The macOS desktop
  prerequisite is the existing Xcode Command Line Tools; DINAS does not install
  or mutate Xcode, Android Studio, Android SDK, CocoaPods, mobile Rust targets,
  or signing identities.
- Go uses the system baseline. Projects select official toolchains with `go`
  and `toolchain` lines in `go.mod` or `go.work`, plus `GOTOOLCHAIN` when
  needed. DINAS does not add `goenv` without an established gap.
- Agent CLIs (Claude Code, OpenAI Codex, and Google Antigravity) are
  authoritatively managed and packaged by the public `agent_dinas` module with
  cryptographic provenance verification and failure-safe release lifecycle tools.
  Core preserves runtime compatibility and developer tooling without maintaining
  duplicate packaging or version/hash tables.

`nvm`, `pyenv`, and `rbenv` remain available explicit alternatives and are not
forced for projects compatible with the system baseline. System Rust is the
ceremony-free baseline; a project may opt into rustup and
`rust-toolchain.toml` for an exact alternate toolchain without a DINAS edit.
Activation does not initialize or mutate rustup state.

## Strict path projection

`nix-darwin.toml` accepts one exact `[paths]` schema:

```toml
[paths]
workspace_root = "$HOME/.local/nix-darwin"
nbin_root = "$HOME/.local/nbin"
nbin_dev_root = "$HOME/.local/nbin_dev"
ai_index_root = "$HOME/ai"
dinas_root = "$HOME/.local/nix-darwin/darwin-is-not-a-snowflake"
```

The typed Python `ControlConfig` parser rejects unknown keys, traversal,
ambiguous symlink ancestors, unsafe existing ownership or modes, root/home
targets, and unapproved containment. When `[paths]` is absent, these defaults
apply. The deployment projection emits exact `paths.json`; Nix reads that
projection and does not parse TOML independently. Following the DINAS ROOT1
flattening of product-tracked files from `nix-darwin/` to root, `dinas_root`
canonically identifies the checkout at
`$HOME/.local/nix-darwin/darwin-is-not-a-snowflake`. `$HOME/.flakes` is retired
and must not be recreated as active compatibility state. Original paths remain
in historical evidence (see the
[ROOT1 cutover guide](../ops/dinas-root1-cutover.md)).

## Current and development commands

`~/.local/nbin` is an ordinary managed directory. The projected `paths.nbin_root`
authority is generated into `shConfig` (`004_dinas-paths`) as
`_dinas_projected_nbin_root`. Its validated direct children, in lexical order,
enter PATH; the root itself does not. The `dinas` child points to an immutable
package `bin/` containing DINAS and `nix-update` commands. Reconciliation is
manifest-owned, collision-reporting, atomic for links, and refuses unmanaged
collisions. Any symlink at the namespace root is rejected without migration,
regardless of its target. Read-only preflight reconciliation
is available via `dinas-reconcile --check`, returning `dinas-reconcile-check-v1`
JSON without filesystem mutation.

The deployment preflight invokes this check with the canonical projected roots
and namespace maps as a read-only participant. Activation runs the same
reconciler and propagates failure.

`~/.local/nbin_dev` is also an ordinary directory but never enters global
PATH. Development candidates require explicit use:

```text
dinas-dev list
dinas-dev which <namespace> <command>
dinas-dev exec <namespace> <command> [args...]
dinas-dev shell <namespace>
```

The helper validates names, ownership, modes, symlinks, and executables;
preserves the target argument vector and exit/signal behavior; and gives only a
child shell the selected namespace. Attested checkout `bin/` directories are
permitted in this foundation phase, while immutable built candidate outputs
are the preferred mature topology.

## `~/ai` convenience index

When their targets already exist, reconciliation creates these links:

```text
~/ai/codex          -> ~/.codex
~/ai/claude         -> ~/.claude
~/ai/gemini         -> ~/.gemini
~/ai/agents         -> ~/.agents
~/ai/claude-desktop -> ~/Library/Application Support/Claude
```

Missing provider roots are not created. Unmanaged collisions are refused and
unrelated content is preserved. These aliases are a full-root operator/IDE
view only: source, hooks, packages, providers, dispatch, and deployment do not
consume the managed aliases. `~/.agents` remains the vendor-neutral shared
skill and asset root.

## Boundary

The next boundary is clean Git qualification and local composition selection,
followed by a separately reviewed attended `nix-update` deployment. After that,
flake campaign machinery can migrate before `agent_dinas` is created or
Agent-Central is reclassified. Petdex remains deferred.
