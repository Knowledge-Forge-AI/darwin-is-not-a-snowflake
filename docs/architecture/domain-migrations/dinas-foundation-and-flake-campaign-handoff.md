# DINAS Foundation and Agent Track Handoff

## Target ownership

`agent_dinas` is the future public module and package track for essential
vendor agent execution CLIs, including Codex, Claude, `agy`, and other CLIs
required by JACA. It contains no personal profiles, credentials, dispatcher,
routing, campaigns, or operator configuration.

`agent-central_dinas` is the future private rename and reclassification of the
current Agent-Central. It owns profiles, dispatcher behavior, routing,
campaigns, and operator configuration, and may depend on `agent_dinas`.
Agent-Central ADR 0006 must be superseded, not rewritten, in that future phase.

`developer-ux_dinas` owns GUI and convenience tooling. DINAS core continues to
own the system-wide language and command bootstrap described in
[the development foundation guide](../../guides/dinas-development-foundation.md).

The forbidden dependency edges are:

- public module -> `agent-central_dinas`;
- DINAS core -> `agent-central_dinas`; and
- `agent_dinas` -> `agent-central_dinas`.

Neither `agent_dinas` nor `agent-central_dinas` is created in this phase. The
future convenience names `~/ai/agent-security_dinas` and
`~/ai/agent-central_dinas` are reserved but are not created.

## Flake operations migration order

Before either agent track is created or Agent-Central is reclassified, migrate
these private flake-operation responsibilities into DINAS core, preferably the
existing `nix-update` command family:

1. `agent-flake-promotion-campaign`;
2. final-stage impact interpretation; and
3. composition-chain journaling and resume.

The migration must preserve the existing reviewed-plan, owner-confirmation,
and publication boundaries. This document is an architecture handoff only; it
does not implement the move or modify Agent-Central.

## `~/ai` transition

The newly managed links `codex`, `claude`, `gemini`, `agents`, and
`claude-desktop` are navigation conveniences and must never be runtime
authority. The current pre-reclassification `~/ai/agent-central` directory is
a dated transitional exception for existing provider state writers; it is not
one of those managed aliases. New DINAS shell PATH and command consumers do
not use it. The exception ends when Agent-Central is reclassified and its host
paths are migrated under the separately accepted successor handoff.

## Petdex

Petdex remains explicitly deferred. This phase does not alter its packages,
hooks, policy, installation, configuration, or Developer UX source.
