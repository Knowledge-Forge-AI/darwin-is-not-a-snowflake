# Source audit tools

## Problem

Agents and operators read repositories before trusting them. Core had no
managed static analyser and no local check for hidden prompt-injection text,
so any such tool would have to come from an unmanaged install. Core now
declares two command-line tools in `modules/developer-tooling.nix`: Semgrep
and Malskanner. Neither is a GUI application, so neither gains an application
claim. Neither adds a launchd service, MCP registration or credential.

## Alternatives

Semgrep is packaged by Homebrew and by nixpkgs. The installed Homebrew formula
was 1.181.0 when this change was reviewed. Core's pinned nixpkgs offers 1.143.0
and its pinned nixpkgs-unstable offers 1.172.0. A Nix package would therefore
downgrade the tool. Getting a current version from Nix would need a lock
refresh, which changes far more than one tool. Core declares the
`semgrep` formula instead. With the default `onActivation` settings (cleanup
`none`, no upgrade), the declaration adopts the formula already installed.
It neither reinstalls nor removes the formula. No Nix Semgrep package is added
alongside it.

Malskanner is published only to npm. A floating `npx malskanner` or a global
`npm install` would resolve dependencies at run time and could run lifecycle
scripts on the host. Vendoring the upstream TypeScript source would mean owning
a build. Core instead uses its existing `buildNpmPackage` pattern:
- a private wrapper `package.json` depends on exactly `malskanner@0.1.3`;
- a committed `package-lock.json` pins every transitive tarball;
- a fixed `npmDepsHash` pins the dependency set.

## License and supply-chain review

Malskanner 0.1.3 is MIT licensed. Its registry tarball matched the published
sha512 integrity and shasum, and the lock records that same integrity. The
static code was reviewed before anything was executed:
- the CLI entry point never loads the MCP server module;
- the Anthropic SDK is loaded only by a dynamic import behind `--ai`, and only
  when an API key is present;
- `git clone` runs only for remote-URL targets;
- local scans read text and documentation files only, skip symlinks, and make
  no network calls.

The lock has 105 entries, every one resolved from `registry.npmjs.org` with
sha512 integrity. None declares an install script. Their declared licenses are
MIT (94), ISC (7), BSD-3-Clause (2), BSD-2-Clause (1) and Unlicense (1). The
build passes `--ignore-scripts`, `--no-audit` and `--no-fund` to npm, and the
nixpkgs hooks run npm offline against the prefetched cache. Upstream license
files ship unmodified inside `lib/node_modules`. One transitive package
declares MIT in its `package.json` but ships no license file. The registry
signature is recorded, but it was not cryptographically verified.

Semgrep Community Edition is LGPL-2.1-only, as Homebrew reports. Homebrew owns
its build and supply chain, and Core pins no formula version. Registry rule
packs and Semgrep's metrics are network features. For offline use, run with
local rule files and `--metrics=off`. Core configures neither.

## Packaging, runtime and compatibility

Malskanner runs on the Node 22 runtime that Core already manages, through a
wrapper that calls the store copy of `dist/cli.js`. Node 20 or later is
supported upstream. The package is not added to the managed Node environment.

The closure includes the Anthropic and MCP SDKs, which the default CLI path
never loads. The closure with the Node runtime is roughly 215 MiB.

`malskanner --version` prints `0.1.1` because upstream hard-codes that string.
The installed package metadata is 0.1.3.

The `--ai` option sends documentation text to a hosted model. Core supplies no
credentials and never enables it.

The Semgrep formula version follows Homebrew. A later `brew upgrade` can
change it independently of Core.

## Rollback

To roll back, remove the `semgrep` brew and `pkgs.malskanner` declarations,
the overlay and package output lines, and `packages/malskanner/`. The previous
system generation keeps the Nix package. Removing the brew declaration leaves
the formula installed, because cleanup stays `none`.

## Measured fixture results

These checks ran against the built store binary from a cleared environment
with no proxy or API variables. They used local paths only and never passed
`--ai`.
- A plain README returned `OK` with no findings and exit status 0.
- A README containing all three of the following returned `REFUSE` with
  exit status 2:
  - a hidden comment carrying an instruction;
  - Unicode-tag-smuggled text;
  - a bare agent-directed instruction.

  Score was 100, with five findings under the rules `hidden-html-comment`,
  `unicode-tag-smuggling` and `agent-directed-instruction`.
- Scanning the same file directly gave the same result. SARIF 2.1.0 output
  parsed with five results.
- A missing path returned exit status 3.

The expected rules were taken from the scanner source before the fixtures
ran.

## Not established

This note does not establish host delivery or activation, the Semgrep runtime
behaviour, detection quality beyond these fixtures, a complete security audit
of either tool, or readiness for public release.

## Source URL privacy

Ordinary source verification accepts hash-pinned HTTPS tarballs only when the
locked source binds uniquely to its archive path and the store NAR matches.
Common credential and signed-URL query fields are rejected because source URLs
are retained in deployment plans. Do not put credentials in any flake URL;
non-secret revision queries remain supported.
