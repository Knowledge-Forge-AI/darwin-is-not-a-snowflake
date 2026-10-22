# Build Parallelism Command Inventory

Status: current as of BPAR8

## Purpose

This inventory identifies repository-owned Nix build, check, and switch command
sites and records where build concurrency belongs. Test-process concurrency is
separate: `NIX_DARWIN_TEST_JOBS` and test runner `--jobs` control test fan-out,
while `NIX_DARWIN_BUILD_JOBS` controls Nix build concurrency.

Routine build-job plumbing preserves the unmodified command when
`NIX_DARWIN_BUILD_JOBS` is unset or empty. Bootstrap is the explicit exception:
it runs before managed shell configuration exists and therefore defaults to
`4`. Every selected value is validated as a positive integer before it is
passed as `--max-jobs N`.

## Command Sites

| Site | Command role | Classification | Decision |
| --- | --- | --- | --- |
| `bin/nix-update` | locked `nix build` during `build`, `prepare`, and apply revalidation | Should receive build parallelism | Implemented: `NIX_DARWIN_BUILD_JOBS` or `--build-jobs N` adds `--max-jobs N` during build/prepare; the reviewed command vector and virtual post-transaction projection are recorded in the plan. |
| `bin/nix-update` | isolated targeted or explicit broad `nix flake update` | Should not receive build parallelism | Lock maintenance is a separate non-deployment transaction built from a clean reviewed Git tree and never implies a switch. |
| `bin/nix-update` | one reviewed `darwin-rebuild switch` | Uses the reviewed plan | Apply binds the command to the prebuilt target and does not add unrelated scheduling policy after confirmation. |
| `tools/test/run` | Unit/integration `nix flake check` | Should receive build parallelism | Implemented in BPAR4 with validated `NIX_DARWIN_BUILD_JOBS`; both occurrences represent the same check lane for different scopes. |
| `tools/test/run` | `--full` Darwin `nix build --no-link` | Should receive build parallelism | Implemented in BPAR4 with validated `NIX_DARWIN_BUILD_JOBS`; the Darwin build itself remains sequential relative to the preceding test lanes. |
| `tools/test/run` | `nix shell` fallbacks for Bats, pytest, zunit, Ruby, and nix-unit | Should not receive build parallelism | These calls acquire a missing test runner. Test execution remains governed by test jobs, and fallback command shapes stay unchanged. |
| `tools/test/run` | Direct `nix-unit` execution | Should not receive build parallelism | This is a test-runner invocation, not a Nix build command accepting `--max-jobs`. |
| `tools/bootstrap/bootstrap-macos` | Initial `nix build` of the Darwin system | Should receive build parallelism | Implemented: `--build-jobs N` or `NIX_DARWIN_BUILD_JOBS` adds validated `--max-jobs N`; a fresh host without managed shell config defaults to `4`. |
| `tools/bootstrap/bootstrap-macos` | First switch through the built system's `darwin-rebuild` | Should receive build parallelism | Implemented: the same validated build limit is forwarded to cover any build work remaining during the first switch. |
| `flake.nix` | `checks` derivation built with `runCommandLocal` | Should not receive direct plumbing | This is a declarative derivation, not a shell command site. The outer `nix flake check --max-jobs N` controls build scheduling. |
| `flake.nix` | Commented `darwin-rebuild build` examples | Should not receive runtime plumbing | These are informational examples, not executable paths. |
| `bin/nix-test`, `bin/nix-unit-test`, `bin/nix-int-test`, and `scripts/test-all.sh` | Test command wrappers | Should not receive direct plumbing | These delegate to `tools/test/run`; the runner owns build-job validation and Nix command arguments. |

## Completed Follow-Through

1. Apply build jobs to `tools/test/run` flake checks and the `--full` Darwin
   build.
2. Keep `nix-update` test/build job separation covered with command mocks.
3. Add optional timing so performance changes can be measured without reports.
4. Apply the same validated build limit to bootstrap's initial build and first
   switch, with an explicit fresh-host default.

No command in this inventory authorizes a real update or system switch during
normal verification.
