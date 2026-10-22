# ADR 0002 — Domain Ownership, Application Claims, and Migration Sequencing

Status: Accepted

Date: 2026-08-17

Supersedes: [ADR 0001](0001-nix-darwin-core-subflake-extension-contract-and-owner-driven-migration.md)
in part — specifically the clause in "Core is a framework and a bootstrap
authority, not a host for project code" that assigns **host security** to core's
publication-safe base workstation capability.

It also narrows one further term in that same clause: where ADR 0001 assigns
"browsers" to core without qualification, section 2 below reads that as the
browser *binaries* only. Browser policy, extension policy, and browser UX belong
to their owning module. This narrowing is declared here so that a later reader
cannot cite ADR 0001's unqualified word to claim browser policy for core.

Everything else in ADR 0001 remains in force, including the `*-nd` output
contract, the layer extension contract, the parity and integrated-rebase
evidence rules, and the `NO NEW PROJECT-SPECIFIC OPTIONAL FOOTPRINT IN CORE`
rule.

## Context

ADR 0001 solved the ownership question for *projects*: an integration that
belongs to a project moves to that project's repository. It left one family
unresolved, because at the time it did not need resolving — the operator's own
workstation applications and the operator's own host posture. Those were swept
into "publication-safe base workstation capability" and assigned to core on the
theory that they are properties of the machine rather than of any one project.

Two things have since made that assignment wrong.

**The first is disclosure.** `modules/host-security.nix` does not describe a
generic macOS security posture. It hardcodes the MAC address of a specific
network gateway, schedules a stealth-mode daemon keyed to that identity, and
enforces a specific egress ruleset through a PF launchd daemon. `modules/ssh.nix`
states the host's actual OpenSSH authentication policy. These are operator facts
and operator posture. A repository intended to become public cannot own them,
and classifying them as "base workstation capability" does not change what a
reader learns from them.

**The second is scale.** Core currently installs, in three modules, the
operator's entertainment, office, storage, personal, and desktop-convenience
applications. `modules/desktop-apps.nix` alone declares thirteen Homebrew casks
and three Mac App Store applications spanning five unrelated concerns. None of
these are generic. A machine that needs Nix, a shell, a toolchain, and a way to
recover itself does not need a games launcher. Keeping them in core means core
cannot shrink to the bootstrap authority ADR 0001 described, and it means every
capability an operator adds lands in the repository with the widest blast
radius.

Decomposing those applications into domain modules creates a failure mode that
did not previously exist. Today one repository declares every application, so a
duplicate is visible in a diff. Once each domain declares its own, two modules
can independently install the same macOS application through two different
package managers, and nothing in the ADR 0001 contract notices. Homebrew and the
Mac App Store both write to `/Applications`; the last writer wins, silently,
with no evaluation error and no diff. Worse, ADR 0001's parity rule *requires* a
window in which core and the destination module both provide the same
application — so a naive "duplicate is always fatal" rule would make every
migration impossible.

This decision resolves ownership, defines the collision contract, and defines
the narrow overlap mechanism that makes the two compatible.

## Decision

### 1. Core stops owning operator host security

ADR 0001's list of core-owned base workstation capability is amended to remove
host security. The split is by *genericity*, not by subject matter:

```text
core owns:
  generic host-security extension seams — the module import points and option
    surfaces through which a security module contributes posture
  bootstrap-safe defaults that are genuinely operator-independent, meaning a
    recovering machine is not left less safe than a stock macOS install

an operator-designated non-public security module owns:
  operator host firewall posture
  trusted network identities
  host egress and stealth enforcement
  host SSH server policy
  private security applications
  security-specific host services
```

The test for a bootstrap-safe default is narrow, and is stated here so it is not
relitigated per item: a default stays in core only if it is (a) a hardening
posture no operator would want inverted, (b) expressible without naming a
network, a device, a credential, or a person, and (c) required before a
non-public composition can be installed. `GuestEnabled = false` qualifies. A
trusted gateway MAC does not. Where an item is arguable, it moves — a capability
wrongly held in a private layer costs an operator some convenience, while a
capability wrongly held in a public one costs disclosure that cannot be undone.

No source moves in this decision. `modules/host-security.nix` and
`modules/ssh.nix` remain grandfathered core footprints until their destination
owner migrates them under the ADR 0001 evidence rules.

#### Why this does not damage the composition model

*Core publication.* This strictly reduces what core must be cleared to publish.
It removes a publication blocker rather than creating one.

*Non-public composition layering.* Host security is contributed through the same
`darwinModules.default` seam every other module uses, and the module system
merges it into the same configuration core would otherwise have produced. A
module's footprint grows; the layering does not change.

*Owner-driven migration.* The destination owner runs this migration exactly like
any other: inventory, destination, registration, parity while core still
provides the old footprint, removal, integrated rebase. This ADR grants no
exemption from the ADR 0001 evidence sequence.

*Core remaining independently buildable.* Core keeps the seams and the
operator-independent defaults, so a core-only configuration still evaluates,
builds, and activates. It is less hardened than the operator's composed system.
That is the intended and already-accepted consequence in ADR 0001: "The
core-only state is deliberately smaller than the operator's full workstation."
It is not less hardened than stock macOS, which is the floor bootstrap must
hold.

The residual risk is stated plainly: between the moment host security leaves
core and the moment a machine installs the composition that restores it, a
core-only machine has stock macOS network posture. Bootstrap already stops short
of any non-public composition, so this window already exists for every other
non-public capability. It is a bootstrap sequencing property, not a regression
introduced here.

### 2. Domain ownership matrix

The 2026-10-08 decision supersedes the former separate office and storage
destinations. No new repository is created for either. The complete Office
provider payload joins the existing non-public owner; remaining office products
and GrandPerspective stay in core. The four specified additions are a limited
exception to the earlier prohibition on office additions in core. Other domain
assignments and the generic ownership rules remain in effect. Historical
examples elsewhere in this ADR do not authorize an obsolete destination.

Publication class is not self-declared. As in ADR 0001, a module is
public-capable because a public-capable composition registers it. The classes
below record the *intended* registry placement.

| Module | Class | Status | Owns |
|---|---|---|---|
| core | public-capable | exists | bootstrap and recovery, composition seams and host constructor, generic shell/terminal/toolchains, agent-useful developer primitives, generic browser binaries, generic test and coverage infrastructure, selected-flake machinery, generic package-management integration |
| `vscode-nd` | public-capable | exists | VS Code |
| `entertainment-nd` | public-capable | planned | Discord, VLC, OpenEmu, MacGameStore, `yt-dlp` |
| core office/diagnostic applications | public-capable | retained; limited additions authorized | Teams, Zoom, Adobe Acrobat Reader; LibreOffice, ONLYOFFICE Desktop Editors, Zettlr and GrandPerspective on the current host; future Docs/Sheets/Slides wrapper intent |
| `browser-ux_dinas` | public-capable | planned | Chrome managed policy, Firefox policy, extension policy, Clear New Tab, Brave YouTube, the Shared Images workflow, browser-side password-manager native messaging, browser activation helpers, browser UX tests/docs/skills |
| `developer-ux-nd` | public-capable | planned | Applite, Bruno, DBeaver, Zenmap, GIMP, Audacity, Petdex, the current ChatGPT desktop/current Codex GUI, Claude desktop, the Google Antigravity command center, and Google Antigravity IDE |
| a non-public storage/security owner | non-public | existing owner; source transfer candidate | cloud storage, backup, generic encrypted sync/restore, filesystem support and capture; complete Microsoft Office provider group, including OneDrive and declared Defender Shim |
| `desktop-ux-nd` | public-capable | planned | Logi Options+, Command X, Pure Paste, and future generic human desktop convenience utilities |
| a non-public security module | non-public | exists | the surfaces listed in section 1, plus private security applications and security-specific host services |
| a non-public agent-security module | non-public | exists | agent security posture, agent bridge exposure policy, agent-specific backup and restore policy, and the agent-facing Git/MCP integration currently grandfathered in core |
| a non-public personal module | non-public | planned | the operator's personal-domain applications currently declared in `modules/desktop-apps.nix` |

The non-public rows are deliberately described by role. This repository is
intended to become publication-capable, and its disclosure boundary forbids
naming an operator-designated non-public repository or module. The concrete
mapping — which core-resident surface goes to which non-public module — is
recorded in private archival evidence held with those modules, which is where
ADR 0001 already puts documentation that describes a non-public integration.
Recording it here would create exactly the publication blocker this
decomposition exists to remove.

Rules that fall out of the matrix and are binding:

**A public-capable module must not depend on a non-public module.** ADR 0001
already implies this — a module's class is the class of its most restricted
required dependency — but the browser case makes it concrete enough to be worth
stating. `browser-ux_dinas` owns the *browser side* of password-manager
integration: the native messaging host manifests it installs into Chrome's and
Firefox's manifest directories, behind a public Browser UX configuration toggle
(currently `config."browser-ux".configuration.keepassxc.enable`) that is enabled
by default and explicitly disableable through `[browser-ux.keepassxc] enable = false`,
targeting the stable upstream native-host location. It does not install the
password manager, does not import a non-public module, and does not read
anything one produces. The application itself is owned elsewhere. Neither side
needs to know the other exists.

**BUX3 amendment:** The accepted product default is now enabled for both Chrome
and Firefox. BUX1's neutral, disabled bootstrap remains a historical fact.
Disabling the native bridge does not remove the KeePassXC-Browser extensions:
they remain `default-on` unless separately opted out for the relevant browser.
Bridge enablement remains independent of KeePassXC application ownership.

**`developer-ux-nd` takes human-oriented development applications; core keeps
agent-useful developer primitives.** `nil`, `nixd`, `nixfmt`, `ripgrep`,
`shellcheck`, `shfmt`, and the language toolchains stay in core because an
autonomous engineering workflow running against a core-only checkout needs them
to do its job. A GUI database browser is not that.

That rule binds the human-facing AI application split. The Google Antigravity
command center, Google Antigravity IDE, Anthropic Claude desktop GUI, and
OpenAI's current ChatGPT desktop/current Codex GUI belong to
`developer-ux-nd`. Core retains the `antigravity-cli` cask and its `agy`
command, the `codex` cask and its Codex CLI, and `pkgs.claude-code` as
agent-useful terminal primitives. The current ChatGPT application also bundles
non-development Chat and Work surfaces. The operator nevertheless assigns the
combined application to `developer-ux-nd`, because Codex is the
developer-facing reason for installing it on this workstation and the single
application bundle cannot be divided between owners. The deprecated
`codex-app` cask is not a second application assignment and must not be added.

**`desktop-ux-nd` is created rather than folded into another domain.** It is
small — three applications today — and the case for deferring it is real. It is
created anyway because the alternative is worse: Logi Options+ is a mouse driver
and Command X is a Finder utility, and the only reason to put either in
`entertainment-nd` or `developer-ux-nd` would be that those repositories already
exist. Placing an application in a domain it does not belong to teaches the next
reader that the taxonomy is arbitrary, and moving it later costs more than one
small repository costs now. If migration sequencing later shows `desktop-ux-nd`
has too few surfaces to justify a repository, that finding gets its own ADR; it
does not license folding the applications into an unrelated domain in the
meantime.

**Core keeps the browser binaries, claims, and bare baseline; `browser-ux_dinas` owns the host overlay.**
`pkgs.google-chrome` and the `firefox` cask stay in core permanently to provide
a deliberately small core-only browser baseline for agent VMs, frontend development,
cross-browser validation, and bootstrap/recovery. Core retains Google Chrome and
Firefox binary/install authority, their application claims, and a generic,
declarative policy composition and rendering seam. `browser-ux_dinas` owns the
host-specific/browser-UX overlay: Chrome managed preferences, Firefox policy
values, external extension policy, camera/microphone permissions and allowlists,
password-manager/autofill integration, Clear New Tab, Brave YouTube, the Shared
Images workflow, browser-side password-manager native messaging, browser-specific
activation helpers, and browser UX tests/docs/skills. Separating the bare core
browser baseline from the host overlay allows autonomous workflows to evaluate,
build, and validate without depending on the browser UX repository.

### 3. The `applicationClaims` contract

This section defines the contract. It is **not implemented by this decision**;
implementing it across core and the compositions is the next phase's work, and
the shape below is normative for that phase.

#### 3.1 Canonical application identity

A claim identifies a GUI application by a stable identity, in this order of
preference:

```text
bundle:<macOS bundle identifier>     preferred for anything with an app bundle
mas:<Mac App Store application ID>   when the application is Mac App Store only
                                     and its bundle identifier is not knowable
                                     without installing it
```

Any other scheme requires an ADR naming it. A display name is never an identity:
"Firefox" is not a key, `bundle:org.mozilla.firefox` is.

The bundle identifier is canonical, and a Homebrew cask token is not, precisely
because of the collision this contract exists to catch. Two managers installing
the same application agree on the bundle identifier and disagree on everything
else — a cask token has no meaning to the Mac App Store, and a Mac App Store
numeric ID has no meaning to Homebrew. Keying on a manager's own naming makes
cross-manager duplicates invisible, and that is the only kind of duplicate that
is hard to see by reading a diff.

A cask token or Mac App Store ID may still be carried as provider metadata, and
should be, because it is what a human needs in order to act on a collision
report. It is metadata, not identity.

**Command-line-only packages are excluded.** A claim is for something that
installs a macOS application bundle. Command-line packages already collide
visibly and mechanically: two overlays defining the same attribute is an
ordering question ADR 0001 already answers, and two modules adding the same
package to `environment.systemPackages` produces a build-visible duplicate
rather than a silent overwrite of a shared directory. Extending claims to those
packages would make every module declare a second inventory of what it already
declares, buying no detection it does not already have.

#### 3.2 Claim shape

```nix
applicationClaims = [
  {
    id = "bundle:org.mozilla.firefox";
    artifact = "Firefox.app";
    provider = "homebrew-cask";
    package = "firefox";
  }
];
```

Required fields:

- `id` — the canonical identity string defined in 3.1.
- `artifact` — the bare application bundle file name the claim installs, such as
  `Firefox.app`. See 3.2.1.
- `provider` — one of `nix`, `homebrew-cask`, `mas`. The set is closed; adding a
  provider requires an ADR, which is what keeps a new deployment system from
  becoming authoritative by accident (see 3.5).
- `package` — the provider-native name: the attribute, the cask token, or the
  Mac App Store application ID as a string. This is the actionable field in a
  collision report.

Optional fields:

- `handoff` — the migration overlap declaration defined in 3.4. Absent on an
  ordinary claim.
- `suite` — see 3.2.1. Absent on an ordinary claim.

**The owner name is attached by the composition, not supplied by the module.** A
module states what it installs; it does not get to state who it is. A
self-reported owner is unverifiable and could be used to fabricate a handoff
record, and the composition already knows the registry name it is aggregating
under. This mirrors ADR 0001's rule that publication class is registry
placement, not self-declaration.

There is no version field, no priority field, and no preferred-provider field.
Those would turn this into a package-manager framework that arbitrates
ownership. It does not arbitrate; it detects and fails.

#### 3.2.1 Amendment: artifact identity, suites, and case normalization

This subsection is added by the implementation phase that built the contract.
The review of this ADR deferred three mechanics to that phase, and each turned
out to need a normative sentence rather than only a test.

**Comparison is case-normalized.** `bundle:COM.Example.App` and
`bundle:com.example.app` are the same identity, and `Foo.app` and `foo.APP` are
the same artifact. Bundle identifiers are case-insensitive to Launch Services
and file names are case-insensitive on a default macOS volume, so a
case-sensitive comparison would let exactly the silent overwrite this contract
exists to catch through. The declared spelling is preserved and reported; only
the comparison key is lowered.

**`artifact` is required, and it is a bare bundle file name.** Identity alone
does not catch two different applications installing over the same
`/Applications/Foo.app`, which is the collision that actually destroys a
working system. `artifact` supplies the second key: after normalization, one
artifact may map to at most one identity. A slash is rejected, so a claim can
name the bundle without naming an operator's home directory — the key stays
machine-independent and publication-safe. Two claims joined by a valid handoff
must agree on it, because a handoff transfers one application.

**`suite` marks a provider package that supplies more than one bundle.** One
cask token asserting two unrelated identities is normally a typo, and is fatal.
But `microsoft-office` genuinely installs Word, Excel, PowerPoint, OneNote,
Outlook and the OneDrive client 3.6 already assigns, from one pkg payload.
Rather than widening the rule, a package that really is a suite says so on
every one of its claims. A `suite = true` on a package that supplies a single
application is also an error, so the marker cannot decay into a stale
annotation — the same self-removing property 3.4 relies on.

**For a bundle an activation script writes, `provider` names the origin of the
payload, not the step that stamps it.** A launcher an activation script builds
around a Homebrew-supplied binary claims `homebrew-cask` with that cask's
token. The provider set stays closed, and the launcher lands in the same
provider/package group as the application it wraps — which is what makes the
suite rule above see all the bundles one payload is responsible for.

This does not add arbitration. Every field added here is a detection key.

#### 3.3 Composition behavior

Aggregation is outward from core, in the same total order ADR 0001 defines for
overlays:

```text
core's claims
  -> the public composition's registered modules, in its declared registry order
    -> a non-public composition's registered modules, in its declared order
```

Each layer aggregates its own registered modules' claims together with the
claims of the layer below, annotating each with the registry name of the module
that supplied it. **Core exports `applicationClaims`** for the applications it
still declares, because a grandfathered core footprint colliding with a
destination module is precisely the case 3.4 exists to handle, and core cannot
participate in a handoff it does not declare.

The uniqueness check is: after aggregation, no normalized `id` and no normalized
`artifact` appears more than once, except where an authorized handoff record
permits exactly one overlapping pair. A violation is an evaluation error, not a
warning.

The failure message names the identity, every claiming owner, and each owner's
provider and package, because "duplicate claim" without those sends the reader
back to grep:

```text
error: application claim collision

  id: bundle:com.microsoft.teams2

  claimed by:
    core             provider=homebrew-cask  package=microsoft-teams
    office-nd        provider=nix            package=microsoft-teams

  Exactly one module may install an application. If this is a migration
  handoff, declare it on the destination module's claim:
    handoff = { from = "core"; adr = "<path to the migration ADR>"; }
```

**The selected top-level composition validates.** Validation runs where the
complete set is known, which is where ADR 0001 already puts package-set identity
assertion. A lower layer cannot validate a set it cannot see: the public
composition genuinely does not know whether a non-public module also claims a
given application, and it must not be able to find out. Each layer may check its
own subset for early feedback, but the binding gate is at the top, because the
top-level composition is the only party that can produce a correct answer.

#### 3.4 Migration handoff: the grandfathered overlap

ADR 0001 requires parity to be proved *while core still provides the module*.
For an application, that means core's declaration and the destination module's
declaration are both live at once. Under a naive rule that is a fatal collision,
and the migration cannot happen.

The mechanism is a **handoff record on the destination claim**:

```nix
applicationClaims = [
  {
    id = "bundle:com.hnc.Discord";
    artifact = "Discord.app";
    provider = "homebrew-cask";
    package = "discord";
    handoff = {
      from = "core";
      adr = "docs/adr/2026/08/0003-entertainment-nd-migration.md";
    };
  }
];
```

Both `from` and `adr` are required when `handoff` is present.

A handoff is valid only when **all** of the following hold, checked mechanically
at evaluation:

1. The claim carrying it is the destination claim — the overlap it authorizes is
   between exactly this claim and exactly one other claim with the same `id`.
2. `from` names the registry name of the module supplying that other claim, and
   that module actually supplies a claim with this `id`. A handoff naming a
   module that does not claim the application is an error, not a no-op.
3. Exactly two claims exist for the `id`. Three is an error regardless of any
   handoff record.
4. The other claim carries no `handoff` of its own. Overlap is authorized by the
   destination, in one direction. Two modules cannot mutually authorize each
   other into permanent shared ownership.
5. `adr` is present and non-empty and names a tracked path. It does not need to
   resolve at evaluation — evaluation-time file reads are the coupling ADR 0001
   forbids — but its presence means every overlap is attributable to a decision
   record a reviewer can open.

Why this cannot be abused into general shared ownership:

- **It is not a wildcard.** A handoff authorizes one `id` between one named pair
  of owners. There is no per-module, per-provider, or pattern form, and the
  design deliberately offers no way to express one.
- **It is directional and singular.** Rule 4 means the arrangement always has a
  source that does not consent and a destination that declares intent. Two
  unrelated modules that both wanted to install an application would each have
  to name the other as `from`, which rule 4 rejects.
- **It removes itself.** The migration commit that removes the source footprint
  removes the source claim, at which point rule 2 no longer holds and evaluation
  fails until the `handoff` block is deleted. The migration is therefore not
  complete until the exception is gone, and the exception cannot be forgotten —
  it becomes an error rather than decaying into a stale annotation. This is the
  same property ADR 0001 relies on when it forbids compatibility stubs: an
  assignment to a removed option is an evaluation error, and that is what makes
  removal verifiable.
- **It is reviewable.** Every handoff is a tracked, named pair carrying an ADR
  reference, and the live set is enumerable by reading tracked sources rather
  than by inspecting a build.

A handoff does not suppress the collision — it records that one specific
collision is expected. Ordinary duplicate ownership, with no handoff or with a
handoff that fails any of the five rules, remains an evaluation failure.

#### 3.5 Applite is not a deployment authority

```text
Applite is not yet a second authoritative deployment system.
```

Applite is a GUI front end over Homebrew. Today it is a cask core installs, and
under this ADR it becomes an application `developer-ux-nd` owns. Nothing
declarative reads its inventory, and nothing reconciles what a user installs
through it.

The consequence that matters here: **installing an application through Applite
neither creates a claim nor satisfies one.** A module migration may not treat
"the operator can get it from Applite" as equivalent to a tracked claim, because
an Applite-installed application is invisible to evaluation, absent from parity
artifacts, and gone after a clean rebuild.

The integration seam is defined only as far as needed to prevent the duplicate
ownership this contract exists to catch: because `provider` is a closed set
(3.2), a future Applite-backed claim requires an ADR that adds the provider, and
that ADR must state how Applite inventory is reconciled against declared claims.
Until it exists, Applite-installed applications are undeclared operator state,
the same as anything else installed by hand.

#### 3.6 Office and OneDrive

Live evidence: `/Applications/OneDrive.app` is present with bundle identifier
`com.microsoft.OneDrive`; core declares the `microsoft-office` cask and declares
no standalone `onedrive` cask; and `bin/cloud-capture-sync` consumes
`~/Library/CloudStorage/OneDrive-*` as a data root. The premise holds, so the
rule is recorded as a decision rather than as an open question:

The 2026-10-08 ownership supersession assigns the whole existing
`microsoft-office` provider group and OneDrive storage workflows to the same
non-public owner. There is no standalone `onedrive` cask and no split installer
ownership. Separately packaged Teams, Zoom and Adobe Acrobat Reader stay in core.
Defender discovery preserves the shim and any installed protection; removal is
future work. A declared shim does not establish active antivirus or EDR.

This is the general shape for "one module installs, another configures": the
installing module claims, the configuring module does not. A claim is about
which module puts the bundle in `/Applications`, not about which module cares
about it. Without that rule every consumer of an application would claim it, and
the check would fire on cooperation rather than on conflict.

If Office is ever unbundled such that OneDrive must be installed separately, the
claim moves to whichever module then installs it — one module, still.

### 4. Skill ownership records

These are recorded now so the future migrations do not have to rediscover them.
No skill moves in this decision except the framework skill replacement in
section 5.

**`shared-images-workflow` → `browser-ux_dinas`.** It currently lives at
`docs/ops/skills/shared-images-workflow` and is globally exposed through this
repository's agent skill roots solely because it is stored here. It is a project
workflow for a specific image library, not a global operator capability, and it
should not be globally exposed on that basis. Its future project-local links
under the current archive root are:

```text
$HOME/Pictures/shared-images/.agents/skills/shared-images-workflow
$HOME/Pictures/shared-images/.claude/skills/shared-images-workflow
```

The migration must rewrite its hardcoded references from core paths to
`browser-ux_dinas`-owned commands on a stable installed path, remove private
continuation-document dependencies, sanitize operator-specific hardcoded paths,
and consume an explicit `${pkgs.rclone}/bin/rclone` package closure boundary
rather than assuming generic rclone ownership or ambient mutable `PATH`. The
project-local skill links are established as an operator workflow action when the
archive root exists, not as an unconditional host activation side effect.

**`browser-hardening-policy` → `browser-ux_dinas`.** It is browser policy guidance,
and it belongs with the module that owns browser policy. Its future discovery is
project-local to the `browser-ux_dinas` checkout rather than global exposure from
core; its `agents/openai.yaml` is display metadata that moves with it, not an
exposure mechanism.

### 5. The framework skill follows the framework

The `nix-darwin-refactor` skill is monolith-specific: it names this repository as
*the* repository, and it assumes one repository owns modules, helpers, tests,
coverage, and application behavior together. Under this architecture that
assumption is wrong in a way that produces wrong actions — an agent following it
will edit core to change a behavior a domain module owns, which is exactly the
coupling ADR 0001 forbids.

It is replaced by `nix-darwin-composition-maintainer`, whose scope is the
ecosystem and framework layer: determining the owning repository before editing,
enforcing the no-new-core-footprint rule, the `*-nd` output contract, explicit
registries, package-set identity, overlay ordering, allowlist collisions,
application claims, owner-driven migration mechanics, the distinction between
public-capable and publication-ready, and separately authorized activation.

It does not carry domain-specific instructions. Browser policy guidance belongs
to `browser-ux_dinas`, storage guidance to its non-public owner, and so on. A framework
skill that accumulates domain knowledge becomes the monolith it replaced.

`nix-darwin-refactor` is removed rather than deprecated. Nothing outside its own
directory and its agent-root symlinks referenced it, so a shim would preserve no
consumer; and a shim that still carried the monolith guidance would be a second
skill disagreeing with the first, which is worse than having none.

## Consequences

Core's target shape is now stated concretely enough to be checked: bootstrap,
composition mechanism, generic toolchain, agent-useful primitives, generic
browser binaries, and test infrastructure. Most of what remains has a named
destination and a named owner.

The matrix in section 2 is not yet exhaustive over core's current application
footprint, and saying otherwise would let an unowned surface travel unnoticed
into whichever migration touched it last. Known unassigned items today:
`qbittorrent`, KeePassXC, OrbStack, and the `mas` brew. Each needs an owner
decision before it can be claimed, and no migration may adopt one by
convenience. The 2026-10-08 limited exception retains GrandPerspective in
core alongside the three specified office additions. The two Antigravity GUI
products and Claude desktop retain their existing domain decisions. The `chatgpt` cask is existing core footprint
assigned to the developer-ux migration; the sibling `codex` cask remains a
core-owned CLI-only primitive.

The `NO NEW PROJECT-SPECIFIC OPTIONAL FOOTPRINT
IN CORE` rule gains a companion — a new operator application now has a domain
repository to go to, so "add it to core for now" loses the excuse that there is
nowhere else.

The number of repositories increases substantially: several new modules on top
of those that exist. That cost is accepted for the same reason ADR 0001 accepted
it, and it is bounded — these are the operator's domains, not an open-ended set.

Moving host security into a non-public module concentrates the most sensitive
configuration in the layer least visible to public review. The mitigation is
that it was already the most sensitive configuration; it was simply in the wrong
place, where its sensitivity was also a publication blocker.

The application claims contract adds an evaluation-time gate that can block a
build for a reason unrelated to the change being made — a collision surfaces
whenever the configuration is evaluated, not when the collision is introduced.
That is the intended trade: a loud failure at evaluation beats two package
managers overwriting each other in `/Applications` at activation, which is the
current silent behavior and which produces a system whose contents depend on
activation ordering.

The handoff mechanism is the part most likely to be wrong. It is deliberately
the narrowest thing that satisfies both requirements, and it fails closed in
every direction it was not designed for. If a real migration finds a case it
cannot express, the correct response is an ADR extending it, not a wildcard.

This decision is architecture only. It moves no module, creates no repository,
and changes no composition. Its value is that the next implementation phase can
extend the composition contract without relitigating ownership semantics — and
if that phase finds a semantic here that does not survive contact with an
implementation, that is a finding worth an ADR, which is far cheaper than
discovering it during a migration.

## References

- [ADR 0001 — Nix-Darwin Core, Subflake Extension Contract, and Owner-Driven Migration](0001-nix-darwin-core-subflake-extension-contract-and-owner-driven-migration.md)
- [ADR 0003 — entertainment-nd ownership migration](0003-entertainment-nd-migration.md)
- [ADR 0004 — developer-ux-nd ownership migration](0004-developer-ux-nd-migration.md)
- [Subflake migration guide](../../../architecture/subflake-migration-guide.md)
- [Owner migration template](../../../architecture/domain-migrations/owner-migration-template.md)
- [entertainment-nd migration inventory](../../../architecture/domain-migrations/entertainment-nd-inventory.md)
- [developer-ux-nd migration inventory](../../../architecture/domain-migrations/developer-ux-nd-inventory.md)

### 2026-10-11: GrandPerspective externally installed exception

GrandPerspective remains externally installed and core-owned as a disk-space
diagnostic. Host read-only inspection confirmed version 3.7.2 build 25 and no
Homebrew or Mac App Store receipt. Its origin is unknown, not proven to be a
direct download. Core intentionally does not declare a Homebrew cask or managed
application claim for that existing bundle during SECURITY2. LibreOffice,
ONLYOFFICE Desktop Editors and Zettlr remain managed core additions. Any future
GrandPerspective provider acquisition or replacement requires a separate
reviewed operator decision preserving the installed bundle and user state.
This amends the prior four-installation intention without changing the
storage/security owner transfer or the historical record.
