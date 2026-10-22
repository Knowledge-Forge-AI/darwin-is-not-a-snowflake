# DINAS Coding Standards

Status: draft

## Purpose

These standards apply to the darwin-is-not-a-snowflake core flake. They adapt general engineering
practice to reproducible macOS setup, browser and app policy, shell
configuration, local tools, backup workflows, and personal-device automation.

The core principle is:

> Use the right tool for the job, write idiomatic code for that language, and
> respect the conventions established by this project.

Standards are guardrails. They should keep the repo understandable without
turning small tools into frameworks.

## Priorities

Favor these outcomes:

1. Declarative, repeatable machine setup.
2. Clear operational behavior on a fresh Mac.
3. Small modules with obvious ownership.
4. Extracted config and helper files instead of large inline strings.
5. Idempotent activation and bootstrap scripts.
6. Tests or checks for risky setup behavior.
7. Minimal churn outside the task at hand.

## Repository Structure

Use the Nix-oriented layout:

```text
darwin-is-not-a-snowflake/
  flake.nix
  modules/
  config/
  bin/
  libexec/
  tools/
  scripts/
  shell/
  src/test/
```

Conventions:

- `modules/`: nix-darwin modules, each focused on one system area.
- `config/`: source files installed or consumed by modules.
- `bin/`: user-facing commands and recurring workflow entrypoints.
- `libexec/`: helper programs invoked by commands or modules but not intended
  as primary shell entrypoints.
- `tools/<domain>/`: rarely used user tools and maintenance workflows.
- `scripts/`: compatibility wrappers and temporary catch-all scripts. Audit
  this directory periodically and move clear patterns elsewhere.
- `shell/`: managed shell startup files and shell fragments.
- `src/test/{unit,int}/<language>/`: structured test suites.

Do not force a generic application layout onto the flake unless a real
application or library grows large enough to need it.

## Language Selection

Use Nix for declarative system state, package selection, launchd services,
system defaults, Homebrew integration, and module composition.

Use Zsh for fresh macOS bootstrap code that must run before Nix-managed tools
exist.

Use managed Bash for post-bootstrap shell automation when the task is mostly
orchestration around platform commands.

Use Python, Ruby, Node/TypeScript, Java, or AppleScript when the runtime or
ecosystem is the better fit. Prefer Python or Ruby over shell once parsing,
structured data, or complex error handling becomes central.

Use AppleScript only for macOS app automation.

Record bootstrap minimums and managed tool targets in project documentation or
flake inputs when they matter. Do not let one developer's current machine state
be the only compatibility definition.

## Source File Language Boundaries

Source files should contain one programming language by default.

Do not hide substantial Python, Ruby, JavaScript, AppleScript, or other source
code inside shell, Nix, or another host language through heredocs, quoted
strings, or append blocks. Put the embedded language in its own source file and
have the caller install, copy, invoke, or reference it.

Exception: directly executed bootstrap, installer, or single-file distribution
artifacts may embed another language when doing so materially simplifies fresh
machine deployment. Keep embedded code small, clearly marked, and limited to
the deployment problem.

## Shell And Bootstrap Conventions

Shell scripts should:

- Use a clear shebang.
- Prefer strict mode when it improves safety and readability.
- Quote variables by default.
- Keep external command calls isolated enough to fake in tests.
- Use temp directories with cleanup traps for scratch work.
- Print actionable failure messages.
- Stay idempotent when they manage machine state.

Fresh-machine bootstrap scripts should:

- Avoid dependencies that only exist after Nix, Homebrew, or language managers
  are installed.
- Detect existing machine state before changing it.
- Keep privileged operations small and explicit.
- Explain manual macOS prompts or trust steps.
- Be safe to rerun.

## Shell Environment Helpers

This project manages a small pair of environment helpers for bridging the
user's interactive shell environment into targeted commands without making
Codex or the global shell state magical.

- `bin/env-snapshot` writes filtered snapshots under
  `/Users/operator/.codex/runtime/shell-env/`. The current default sync target is
  `latest.<shell>.env`, such as `latest.zsh.env` or `latest.bash.env`.
- `bin/env-run` runs a command with variables imported from a parser-safe env
  file. It defaults to `~/.codex/runtime/shell-env/latest.zsh.env` and accepts
  `--env-file PATH` when a project-specific env file should be used.
- `env-run` supports simple `NAME=value` and `export NAME=value` lines, plus
  blank lines and comments. It intentionally does not source arbitrary shell
  code, so shell functions, command substitutions, `unset`, and other shell
  constructs should be rejected rather than executed.
- Keep PATH-facing commands in `bin/` and implementation code under
  `libexec/codex-env/`. Behavior changes should update unit tests, integration
  tests, and `src/test/coverage-manifest.tsv`.

Use `env-run` when a command should see a captured shell environment or an
explicit `.env` file without permanently changing the caller's environment.

## Nix Module Conventions

Modules should be small and purpose-driven. A module should usually correspond
to a system concern such as browsers, shell config, desktop apps, language
managers, terminal UX, backup, virtual machines, or a specific workflow.

Prefer:

- Config source files under `config/<area>/`.
- Helper programs under `libexec/<area>/`.
- User commands under `bin/`.
- Rare maintenance tools under `tools/<area>/`.
- Explicit imports in `flake.nix`.

Avoid:

- Large embedded shell heredocs when normal source files are clearer.
- Unrelated refactors while changing a module.
- Hidden machine-specific assumptions without comments or docs.

## Testing And Coverage

Use behavior-focused tests for scripts and config assembly.

Current test layout:

```text
src/test/unit/<language>/
src/test/int/<language>/
```

Test files should make their test type obvious, for example:

```text
example.unit.test.bats
example.int.test.py
```

Aim for at least 85 percent coverage by every metric tracked for the language
or file type being changed. For new code and substantially refactored code,
85 percent is a hard floor for each tracked metric.

Follow the project test-level definitions and gate behavior in
[Test Strategy And Coverage Gates](test-strategy-and-coverage-gates.md).
In short:

- `nix-unit-test` runs isolated unit tests.
- `nix-int-test` runs host-safe integration tests.
- `nix-test` runs the combined host-safe suite.
- Future smoke and system tests may mutate disposable macOS environments and
  must remain standalone.
- Coverage gates are language-specific. Do not hide a weak language or metric
  inside a multi-language aggregate.

For shell, Nix activation behavior, and bootstrap logic, prioritize behavior
coverage for the risky paths:

- Fresh-machine bootstrap branches.
- Idempotent reruns.
- Privileged file installation.
- Browser policy and managed-preference behavior.
- Backup, sync, and destructive-operation boundaries.

## Documentation

Keep public project standards under `docs/specs/`.

Keep private operator notes outside the repo. Private notes may record local
paths, device-specific snapshots, account-specific operational context, or
publication-risk analysis that should not be part of a public-facing project
history.
