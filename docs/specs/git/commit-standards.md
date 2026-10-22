# Commit Standards

Status: draft

## Purpose

These standards keep the project history readable while preserving room for
exploratory work. Commits should tell a coherent story after the work has
settled, even when the actual development session was nonlinear.

## Subject Line

Use an imperative subject line. Keep subjects at 72 characters or fewer when
practical.

Examples:

```text
Add shared-images workflow layout migration
Extract browser policy exception helpers
Render report source coverage tree
```

If an external tracker is introduced for a future project, prefix the subject
with the ticket key and keep the verb capitalized:

```text
ABC-1234 Add shared-images workflow layout migration
```

## Body

Use a blank line after the subject. Wrap body text at 80 characters by default.

Use the body to record motivation, risk, verification, migration effects, or
non-obvious tradeoffs.

Suggested sections when useful:

```text
Why:
Explain the motivation or tradeoff.

Verification:
- nix-unit-test --report
- nix flake check nix-darwin --no-build

Migration:
Mention filesystem, DB, config, or user action effects.
```

## Atomicity

A good commit changes one review question:

- one behavior change plus tests;
- one refactor boundary;
- one source extraction plus callers updated;
- one tool or workflow with its tests;
- one documentation/spec update;
- one generated asset update if it is logically independent.

Split when a reviewer would naturally ask different questions about different
parts. Squash fixups, typo repairs, and false starts.

## Staging Discipline

Use an explicit status checkpoint before staging:

```sh
git status --short
git diff --stat
git diff --name-only
```

Stage by path or patch:

```sh
git add path/to/file path/to/test
git add -p
```

Before committing:

```sh
git diff --cached --stat
git diff --cached --check
```

This keeps exploratory flow while forcing one small discipline checkpoint
before history becomes permanent.

## History Rewrites

Never rewrite shared history without an explicit backup ref and explicit
approval.

Use `--force-with-lease` instead of a plain force push when replacing a remote
branch after a planned rewrite.
