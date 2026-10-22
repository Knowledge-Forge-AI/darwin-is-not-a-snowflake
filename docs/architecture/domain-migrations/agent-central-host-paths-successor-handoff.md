# Successor Handoff: Extract Agent-Central Host Paths

## Scope and boundary

The local-first deployment product in `bin/nix-update` and
`libexec/nix_darwin_local/` does not depend on Agent-Central. Agent-Central is
an optional client, and `nix-update` remains the canonical deployment
authority.

Core still owns historical workstation configuration that points into
`$HOME/ai/agent-central`. This record inventories that actual footprint so a
successor can move it into an optional, domain-owned module. It authorizes no
cross-repository edits, registration, activation, or host mutation.

## Current core footprint

| Core owner | Host path or value | Current behavior |
| --- | --- | --- |
| `modules/codex-state-writer.sh` | `$HOME/ai/agent-central` | Creates and ownership-checks the Agent-Central root and its `codex` subtree. |
| `modules/codex-state-writer.sh` | `$HOME/codex` to `$HOME/ai/agent-central/codex/usr` | Migrates legacy Codex documentation and maintains the compatibility link. |
| `modules/codex-state-writer.sh` | `$HOME/.serena` to `$HOME/ai/agent-central/codex/mcp/serena` | Migrates legacy Serena state and maintains the compatibility link. |
| `shell/sh_config/common/004_path` | `$HOME/ai/agent-central/bin` | Prepends Agent-Central commands to workstation `PATH`. |
| `shell/sh_config/common/011_repo-map` | `$HOME/ai/agent-central/codex/mcp/repo-map` | Supplies the default `REPOMAP_HOME` while preserving an explicit override. |
| `config/codex-tooling/codex-tooling-desired-state.json` | Agent-Central root, Codex specs directory, and reports directory | Encodes an allowed Git root and default managed/report paths under Agent-Central. |
| `config/codex-tooling/codex-tooling-self-healing.md` | Agent-Central Codex specs directory | Documents the current desired-state source location. |

Tests under `src/test/` intentionally describe and protect these existing
behaviors. They are evidence for the migration surface, not additional host
couplings.

## Destination contract

The domain owner should publish an optional `*-nd` module through the existing
composition contract. The destination owns:

- the Agent-Central and Codex directory topology;
- legacy documentation and Serena migration, including collision backups;
- compatibility links for `$HOME/codex` and `$HOME/.serena`;
- the Agent-Central `PATH` contribution;
- the `REPOMAP_HOME` default;
- the Codex tooling desired-state paths; and
- the corresponding unit, integration, coverage-manifest, and operator
  documentation ownership.

Core must retain no stub, alias, fallback probe, or implicit checkout
discovery after cutover. The destination must be explicitly registered; core
must still evaluate and build when the optional checkout is physically absent.

## Required successor sequence

1. Re-inventory every source and test listed above against the then-current
   tree.
2. Implement the domain module without changing core behavior.
3. Register it explicitly in the owning composition with the package-set and
   shell-contribution contracts preserved.
4. Prove parity for both managed hosts while core still supplies the
   footprint, including link topology, ownership checks, migration backups,
   shell ordering, and explicit `REPOMAP_HOME` override behavior.
5. Remove the core sources and move their coverage-manifest ownership in the
   same migration.
6. Build core with the destination checkout physically absent, then build the
   composed configuration.
7. Leave activation to a separately reviewed `nix-update prepare` and
   `nix-update apply` transaction.

## Rollback and deferred risk

Before core removal, rollback is removal of the optional registration and
destination module. During cutover, preserve exact preimage bytes and the
existing collision-backup behavior; do not delete or rewrite user state to
force parity. Build parity alone does not prove live link ownership, shell
startup behavior, or migration safety, so those remain explicit activation
risks for the successor phase.
