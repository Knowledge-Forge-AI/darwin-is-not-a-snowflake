# Architecture Decision Records

This repository's ADRs are organized by the local calendar year and month in
which the logical document was first introduced to the repository.

```text
docs/adr/YYYY/MM/NNNN-<slug>.md
```

Use the introducing commit's committer timestamp and preserve the calendar date
encoded in that timestamp's numeric offset. Do not convert the timestamp to UTC
or to the reviewing machine's timezone. Later edits or moves do not change the
assigned introduction month.

ADR numbers form an independent, repository-global four-digit sequence. Keep
assigned numbers and basenames stable, never reuse a number, allocate the next
number from accepted `main`, and update active repository links during an
authorized move.

An ADR uses the Status / Context / Decision / Consequences shape. Status is
`Accepted` only when the decision's gates and its review are complete;
otherwise `Proposed`.

## Active decisions and guides

- [ADR 0001 — Nix-Darwin Core, Subflake Extension Contract, and Owner-Driven Migration](2026/08/0001-nix-darwin-core-subflake-extension-contract-and-owner-driven-migration.md)
  — superseded in part by ADR 0002 on host-security ownership.
- [ADR 0002 — Domain Ownership, Application Claims, and Migration Sequencing](2026/08/0002-domain-ownership-application-claims-and-migration-sequencing.md)
- [ADR 0003 — `entertainment-nd` Ownership Migration](2026/08/0003-entertainment-nd-migration.md)
- [ADR 0004 — `developer-ux-nd` Ownership Migration](2026/08/0004-developer-ux-nd-migration.md)
- [Subflake migration guide](../architecture/subflake-migration-guide.md) — the
  actionable protocol for a team moving its own footprint out of this
  repository. Start here if that is you.
- [Owner migration template](../architecture/domain-migrations/owner-migration-template.md)
  — the per-domain checklist an owner fills in for its own migration.

## Relationship to `docs/deployments/`

`docs/deployments/` is a historical archive of phase deployment and exit
reports. Those records are evidence, not decisions. They are not ADRs, they are
not renumbered into this sequence, and a decision that needs to be normative
must be written here even when a deployment report already describes it.

## Disclosure boundary

This repository is intended to become publication-capable. An ADR here may name
this repository, the public-capable composition repository, the public
`vscode-nd` module repository, and generic `*-nd` module repositories. It must
not name any operator-designated non-public repository, module, Git URL, flake
input, checkout path, option name, or configuration identity. Refer to
non-public layers generically, as "an operator-designated non-public
composition" or "an additional non-public composition layer".

This repository's tracked tree today still contains legacy footprints that
violate that boundary. Those are grandfathered pending migration by their owner
teams; they are not a licence to add new ones.
