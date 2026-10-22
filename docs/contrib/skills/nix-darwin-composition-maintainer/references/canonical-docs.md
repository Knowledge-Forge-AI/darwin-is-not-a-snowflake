# Canonical Docs

## Normative, in this repository

Read these before changing a seam, a registry, or a contract:

- `docs/adr/2026/08/0002-domain-ownership-application-claims-and-migration-sequencing.md`
- `docs/adr/2026/08/0001-nix-darwin-core-subflake-extension-contract-and-owner-driven-migration.md`
- `docs/architecture/subflake-migration-guide.md`
- `docs/architecture/domain-migrations/owner-migration-template.md`
- `docs/adr/README.md` — ADR numbering, status, and the disclosure boundary

## Standards

These live in the operator's standards set, outside this repository. Read them
only when the task affects structure, tests, language boundaries, bootstrap, or
security:

- the general coding standards
- the general security standards
- the nix-darwin coding standards
- the nix-darwin test strategy and coverage gates
- the nix-darwin security standards

Resolve their location from the operator's local configuration. Do not write the
path into this tracked file: an absolute path here embeds an operator identity
in a repository intended to become publication-capable, and it couples the skill
to one working tree.

## Not here

Domain guidance belongs to the owning `*-nd` repository. If a task needs to know
how browser policy, storage sync, or an application's behaviour works, find the
owner rather than adding it to this skill.

Deployment and exit reports under `docs/deployments/` are historical evidence,
not decisions. Do not read them in bulk; consult a specific one only when a task
turns on what a past phase actually did.
