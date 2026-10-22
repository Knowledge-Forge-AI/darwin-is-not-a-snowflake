# RUNNER6-DEPLOY0 Hourly LaunchAgent Design

Date: 2026-07-11
Status: approved for implementation

## Objective

Deploy the accepted `repo-map-runner run-next` command as a declaratively
managed macOS user LaunchAgent. Each launch will synchronize control state,
execute at most one path-ordered pending request, and exit. `launchd` will own
the hourly recurrence; Runner will remain one-shot.

## Deployment Ownership

The nix-darwin flake owns this deployment because it already manages system
packages, per-user LaunchAgents, activation-time filesystem preparation, and
machine-level operational documentation. No Runner source or Runner status
record will change in this phase.

The durable deployment and exit record will be:

```text
nix-darwin/docs/deployments/runner6-deploy0-hourly-launchagent-exit.md
```

The `.flakes` repository has no numbered status-report namespace, so this
deployment will not introduce or consume a Runner five-digit counter.

## Runner Package

The flake will declare a non-flake Runner source input fetched over Git SSH and
pinned to the accepted RUNNER6-FIX1 source commit:

```text
57a6f456c38e5e5c497a2962a9417b32c1c2cefc
```

The input will use the existing noninteractive Git SSH authentication boundary,
set `flake = false`, and record its immutable revision and content hash in
`flake.lock`. No credential, key, token, agent socket, or private local path will
be committed. A fresh flake evaluation or source update therefore requires
working SSH access to the private Runner repository, while an already installed
scheduled binary does not.

The local overlay will pass that locked input to a `buildGoModule` package from
the flake's nixpkgs input. Runner has no third-party Go module dependencies, so
the package will not add a vendored dependency hash. The package will build the
existing `cmd/repo-map-runner` entrypoint and install the resulting executable
without modifying Runner source or version semantics. The installed binary is
expected to report `repo-map-runner dev`.

The package will be available as both:

- `pkgs.repo-map-runner` through the local overlay;
- `packages.aarch64-darwin.repo-map-runner` for direct build verification.

It will also be installed through `environment.systemPackages`. The LaunchAgent
will invoke the immutable package output directly rather than a transient
repository build or a shell wrapper.

## LaunchAgent Contract

A focused `modules/repo-map-runner.nix` module will define one per-user agent.
The generated plist will use the exact label:

```text
com.lair001.repo-map-runner.run-next
```

The module will override nix-darwin's default `org.nixos` label prefix for this
service. Nix-darwin will install the declarative plist as the system-managed
user LaunchAgent equivalent under `/Library/LaunchAgents/`.

The service will use separate `ProgramArguments` entries equivalent to:

```text
<nix-store-runner-binary>
run-next
--config
<private-runner-config>
```

It will not invoke a shell, evaluate dynamically composed arguments, source an
interactive profile, or build Runner during launch.

The remaining launchd policy is:

```text
RunAtLoad: false
ProcessType: Background
KeepAlive: absent
StartInterval: absent
StartCalendarInterval.Minute: 5
```

This schedules one wall-clock occurrence at minute 05 of every hour. When the
Mac sleeps through multiple occurrences, launchd may coalesce them into one
event after wake; the deployment will not claim that every missed hour is
replayed individually.

## Environment

The service will set only:

- `HOME`, derived from the existing nix-darwin `userName` parameter;
- `PATH`, assembled from pinned Nix Git and OpenSSH packages.

The explicit configuration argument removes any need for `XDG_CONFIG_HOME`.
Go is not required at runtime because the scheduled command uses an already
built executable. Interactive shell files, ambient language managers, and
terminal-only environment state are outside the service contract.

The module will derive the configuration, state, and log paths from
`userName`. Committed Nix and documentation will use parameters or placeholders
rather than a rendered private configuration file or private absolute runtime
path.

## Git Authentication

Deployment acceptance will first prove that Nix can resolve the locked Runner
input through noninteractive SSH without exposing authentication material. It
will then test the configured control and RepoMap repositories from the same
minimal environment used by the LaunchAgent:

```text
control fetch --prune
control push --dry-run
RepoMap fetch --prune
```

Both source resolution and scheduled Git operations must complete
noninteractively through the existing SSH and macOS credential integration. No
credential, key, agent socket, remote URL, or environment dump will be committed
or copied into deployment logs. If source or minimal-environment authentication
prompts or fails, activation will be treated as blocked until a noninteractive
mechanism is established.

## Logging And Rotation

The service will write separate stdout and stderr logs beneath the configured
Runner state root:

```text
<state-root>/logs/run-next.stdout.log
<state-root>/logs/run-next.stderr.log
```

Activation will create only the owned log directory and two files, with a
restrictive directory mode and `0600` log modes. It will not recursively change
ownership or permissions elsewhere in Runner state.

Nix-darwin will install a dedicated `/etc/newsyslog.d` entry for both logs with
this policy:

```text
maximum active size: 1024 KiB per log
retained archives: 5
compression: gzip
process signaling: disabled
```

The newsyslog flags will include `N` because Runner is one-shot and no process
should be signaled after rotation, and `Z` for gzip compression. Rotation will
remain separate from Runner and will not add truncation or maintenance behavior
to `run-next`.

## Lock And Overlap Operations

The service uses Runner's existing fixed lock beneath the state root. A normal
idle or active run removes it. If an hourly occurrence overlaps a still-running
invocation, the later invocation will fail closed with exit `10`, emit the
bounded lock-unavailable diagnostic, and execute no request.

No `KeepAlive`, intentional parallel launch, stale-lock timer, PID heuristic,
or cleanup agent will be added.

The operator recovery procedure will require these ordered steps:

1. boot out or disable the LaunchAgent;
2. confirm no service process is active;
3. inspect the retained lock without deleting it;
4. inspect control local/remote state and immutable result evidence;
5. inspect Runner run-state and worktree directories;
6. remove the lock only after establishing that no invocation owns it;
7. bootstrap the LaunchAgent again;
8. run one manual `run-next`;
9. verify exit, logs, repository equality, and normal cleanup.

## Installation, Upgrade, And Rollback

The implementation will use the existing tested nix-darwin switch workflow.
The switch will install the pinned package, declarative LaunchAgent, log
directories, and newsyslog configuration as one generation.

Upgrade procedure:

1. boot out the LaunchAgent;
2. verify no active process or retained lock;
3. update and verify the pinned Runner package;
4. switch to the new nix-darwin generation;
5. run `version` and one direct idle invocation;
6. bootstrap or verify the LaunchAgent;
7. kick it once and inspect exit status, logs, and cleanup.

Rollback procedure:

1. boot out the LaunchAgent;
2. restore the previous nix-darwin generation;
3. verify Runner configuration compatibility;
4. run one direct idle invocation;
5. bootstrap or verify the previous LaunchAgent;
6. inspect exit status, logs, control equality, and lock cleanup.

## Verification Design

Static and host-safe checks will cover:

- the locked non-flake Runner SSH input and exact source revision;
- package exposure and system installation;
- exact pinned Runner source commit;
- exact launchd label and four fixed program arguments;
- calendar minute `5` with no `StartInterval` or `KeepAlive`;
- `RunAtLoad = false` and `ProcessType = Background`;
- minimal `HOME` and managed Git/OpenSSH `PATH`;
- separate state-root logs;
- activation-time modes and narrow ownership changes;
- newsyslog size, count, gzip, and no-signal policy;
- coverage ownership for each new Nix source file;
- absence of committed rendered private paths or credentials.

Deployment verification will include:

1. package build and `version` check;
2. rendered plist lint and structural inspection;
3. direct idle invocation;
4. launchd-like minimal-environment idle invocation;
5. minimal-environment Git authentication checks;
6. nix-darwin activation and loaded-domain inspection;
7. manual idle kick;
8. one new immutable deployment request from a separate control actor;
9. manual kick that delivers exactly that request and pushes one result commit;
10. result protocol, manifest, path, hash, and cleanup verification;
11. a second idle kick with no new commit;
12. lock absence and transient-state cleanup;
13. newsyslog dry-run and isolated rotation proof;
14. isolated failure checks for invalid configuration, unavailable binary,
    authentication failure, retained lock, dirty control, divergence, and an
    invalid pending index.

Destructive failure cases will use disposable repositories and state roots.
The operational control checkout and state root will not be deliberately
corrupted or wedged.

## Documentation And Exit Record

The deployment exit record will also serve as the operator runbook. It will
document architecture, label, schedule, environment, Git authentication,
logging and rotation, installation, upgrade, manual kick, disable/bootout,
retained-lock recovery, rollback, verification evidence, privacy boundaries,
and known limitations.

The report will reference logical or redacted paths and exact public commit
identities. It will not include rendered private configuration, private
absolute paths, credentials, remote transport details, environment dumps,
operational log payloads, or generated launchd runtime state.

## Alternatives Rejected

A manually installed binary combined with a declarative LaunchAgent was
rejected because the binary would not be owned by the same reproducible
generation as the plist. A manual plist template and bootstrap script were
rejected because `.flakes` already has a working declarative LaunchAgent
facility. A transient repository build path was rejected because it is not a
stable deployment artifact.

Anonymous `fetchFromGitHub` source was rejected because the private Runner
repository returns `404` without authentication. A Nix GitHub access token was
rejected because it would add a new secret-management dependency when the
existing noninteractive SSH boundary already resolves the exact source commit.

`StartInterval`, `KeepAlive`, an internal Runner loop, catch-up polling, a
separate stale-lock cleanup job, and a Runner-owned deployment status report
were rejected because they cross the accepted one-shot or ownership boundary.

## Scope

This phase may change the nix-darwin package overlay, one focused deployment
module, Nix tests and coverage ownership, deployment documentation, and the
flake lock only when the pinned public Runner source requires it. It may switch
the local nix-darwin generation and add one immutable control request/result
pair for live acceptance.

It will not change Runner source, add another scheduler, implement retries,
archive requests, delete retained locks automatically, add signing, add
distributed coordination, or alter historical control artifacts.
