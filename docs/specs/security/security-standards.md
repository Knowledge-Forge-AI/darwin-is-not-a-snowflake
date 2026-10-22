# Nix-Darwin Security Standards

Status: draft

## Purpose

These standards apply to this nix-darwin flake and its related local workflows.
They describe a practical security baseline for macOS setup, browser policy,
backup and sync tooling, VM/container workflows, activation scripts, and local
automation.

## Security Goals

The flake should make a Mac reproducible without making it fragile or careless.

Primary goals:

- Keep secrets out of the repo.
- Make privileged changes explicit and idempotent.
- Preserve browser role separation.
- Avoid accidental data loss in backup and sync workflows.
- Keep local automation understandable enough to audit.
- Prefer declarative ownership of security-sensitive settings.

## Secrets

Do not commit passwords, API keys, SSH private keys, tokens, cookies, recovery
codes, or cloud credentials.

Acceptable repo content:

- Templates with placeholders.
- Public keys.
- Non-secret configuration.
- Documentation describing where secrets should live.

Secret-bearing files installed or generated locally should use restrictive
permissions. Private keys and token files should generally be `0600`; secret
directories should generally be `0700`.

Prefer an explicit password manager for human-managed credentials. Prefer
cloud secret stores for deployed services.

## Activation Scripts

Activation scripts often run with elevated privilege or affect privileged
paths. They should be narrow, idempotent, and readable.

Rules:

- Check current state before changing it.
- Use explicit installation commands for file ownership and mode.
- Avoid running large data-processing blocks as root.
- Avoid hidden network downloads during activation unless the module clearly
  owns that behavior.
- Avoid destructive operations unless the target is tightly scoped.
- Print useful status messages for long or important steps.

When removing stale settings, delete only the specific keys or paths owned by
the module.

## Bootstrap Security

Fresh-machine bootstrap may need to install platform developer tools, Nix,
Homebrew, compatibility runtimes, and first-switch support.

Bootstrap scripts should:

- Prefer official installers or documented upstream endpoints.
- Keep downloaded installer paths temporary and scoped.
- Avoid embedding secrets.
- Explain manual operating-system trust or install prompts.
- Use elevated privilege only for the step that requires it.
- Leave the machine in a state where a rerun is safe.

## Browser Security

Browser policy should preserve clear roles. Typical roles include:

- Compatibility browser with managed utility extensions where useful.
- Privacy-oriented browser for everyday use.
- Hardened browser for high-stakes sites.
- Dedicated app or media wrapper that is not a general daily-driver browser.

Avoid turning every browser into the same browser. Role separation is a
security feature.

Managed policy should lock settings only when the lock is intentional. If a
setting needs user control, leave it unmanaged and purge stale mandatory keys
from managed preference domains.

Browser extension management should:

- Prefer known extension IDs and official update URLs.
- Keep forced extension lists small.
- Avoid stacking multiple content blockers unless there is a clear reason.
- Verify live policy behavior when changing locked settings.

## Backup And Sync

Backup and sync tools can destroy data when misconfigured, so automation must
be conservative.

For backup, sync, cloud mirror, and media-library workflows:

- Document the source of truth.
- Document intentional exclusions.
- Avoid broad delete operations unless tested and scoped.
- Prefer dry-run or audit commands before destructive syncs.
- Record cloud-provider assumptions where local mirrors are excluded from
  backup.

When excluding cloud storage from local backup, explicitly record the tradeoff:
local backup size and duplicate data are reduced, but cloud-side deletion,
account loss, or sync mistakes may not be covered by the local backup.

## Containers, VMs, And Quarantine

Container and VM state should be treated as disposable unless a project clearly
marks it as persistent.

Rules:

- Do not assume important data inside containers or VM volumes is backed up.
- Keep quarantine workflows isolated from normal browsing and app work.
- Prefer reproducible provisioning over preserving VM drift.
- Document any persistent VM or container volume that matters.

Do not introduce orchestration for simple projects that a local process or
single container can handle clearly.

## Shell And Input Boundaries

Direct terminal input on a personal machine may be treated as trusted when that
assumption keeps small tools simple.

Tighten security when input comes from:

- Downloaded files.
- Browser or clipboard content.
- Shared folders.
- Cloud sync.
- Network services.
- Another user account.
- Scheduled automation that processes changing external data.

For untrusted input, prefer language library APIs over shell string processing.

## Network And VPN Workflows

Network diagnostic and VPN scripts should avoid making silent security claims.

They should clearly distinguish:

- What was measured.
- What could not be measured.
- Whether a result is a best-effort diagnostic or a hard guarantee.

Scripts that test kill-switch or routing behavior should fail loudly when their
dependencies are missing.

## Review Checklist

Before accepting a security-sensitive change, check:

- Does it introduce or expose a secret?
- Does it expand sudo/root work?
- Does it add a destructive file operation?
- Does it process untrusted input?
- Does it change browser policy, backup, sync, VM, VPN, or privileged behavior?
- Does it need a live verification step after activation?
- Is the operational tradeoff documented where a future reader will find it?
