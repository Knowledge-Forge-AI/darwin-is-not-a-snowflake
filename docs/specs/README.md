# Nix-Darwin Project Standards

These standards describe how this nix-darwin flake is structured, tested, and
maintained.

They are public-facing project rules: they should be safe to read without
knowing anything about a particular developer's accounts, devices, private
paths, or personal workflow details.

## Standards

- [Coding standards](coding/coding-standards.md)
- [HTML test report design](coding/html-test-report-design.md)
- [HTML test report implementation plan](coding/html-test-report-implementation-plan.md)
- [Build parallelism command inventory](coding/build-parallelism.md)
- [Build parallelism measurements](coding/build-parallelism-measurements.md)
- [Test strategy and coverage gates](coding/test-strategy-and-coverage-gates.md)
- [Generic composition promotion transactions](deployment/2026-08-27-nix-update-promote3a-generic-promotion-transactions.md)
- [Security standards](security/security-standards.md)
- [Commit standards](git/commit-standards.md)
- [Root-to-tip rewrite mapping](git/history-rewrite-mapping-2026-06-24.md)

## Private Companion Notes

Private local details, such as exact checkout paths, current host/tool version
snapshots, and personal workflow context, belong outside the repo in the
operator's private notes.
