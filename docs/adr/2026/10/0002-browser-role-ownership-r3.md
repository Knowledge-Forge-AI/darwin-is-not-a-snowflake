# Browser roles and physical ownership, R3

Status: amended after the dispatcher-owned work review;
PENDING_FINAL_INDEPENDENT_REVIEW of the final bytes outside this run.
Not selected. Host effectiveness and complete native composed graph unqualified.
This decision supersedes the target-host R1/R2B lineup. ADR 0001 and the entire
R2B delivery remain retained evidence, without current acceptance authority.

The role table and physical owner contract are in
[MANAGER-REVIEW.md](../../../../delivery/browser-ownership-r3/MANAGER-REVIEW.md)
and [roles-r3.json](../../../../config/browser/roles-r3.json). Core provides one
stock Nix Brave package, Homebrew LibreWolf and Orion in the active target-host candidate, and
Safari privacy/search/Wipr attestation. Browser UX provides Floorp and bounded
convenience intent, plus the retained Vivaldi reference described below. The host
constructor preserves the predecessor baseline.
No source pin, lock, release branch or installed browser state changes here.

Chrome's legacy forced extension policy, CNT manifest and KeePassXC bridge remain
continuity inputs. The destructive R2B projection is not the R3 selected payload.
No Chrome or Vivaldi retirement occurs. The existing Brave YouTube core writer,
bundle identity, artwork and Chrome wallpaper source remain unchanged. Its current
generated bundle copies Brave from the same package; the desired lighter wrapper
and destination ownership cut require separately qualified preservation and parity.
There is no new Brave YouTube writer, installation or destination claim.

R3R1 scopes the hold by making the candidate inactive in source. Both hosts keep
their pre-R3 package, policy and activation baseline while the core constructor's
`browserRolesR3Selected` literal is false. Unrelated deployments therefore do not
fail a browser hold assertion. Activating either R3 selector still fails before
package deployment, and browser fragment/helper guards refuse before effects.
Direct module consumers default to inactive; selecting superseded R2B ownership
is rejected. Changing the constructor selection and releasing the hold require
separate reviewed source changes after native qualification. The composition
amendment preserves legacy selection while inactive and R3 ownership while active.
Fresh R3R1 evidence is retained in `delivery/browser-ownership-r3r1/`; the original
R2B/R3 evidence and owner patches remain unchanged.
The automation entrypoint also exits 78 before profile creation or browser launch.
Its tested adapter seals the stock executable, accepts only one empty owned native
temporary data directory, seeds current Chromium credential-saving/autosignin/
passkey-saving and address/card-autofill preferences, disables sync, and rejects
personal-profile reuse, extension injection and unsafe debugging arguments.
Browser policy can override profile preferences, so native effective readback is
required before any reviewed source release removes these guards. There is no
environment variable, option or evidence-checkbox bypass.

The optional Agent-Central route patch is a later proposal only. Adopt it after
the hold has been released by reviewed source, the adapter is installed and
executes successfully, and native isolated Playwright/DevTools qualification has
passed. Keep existing MCP routes operational until that boundary. Manager review
alone cannot make an exit-78 adapter available. The source adapter normalizes
Playwright's exact `--no-sandbox` default by omitting it, leaving sandboxing enabled.

Daily Brave and Orion convenience installation remains per-profile attended work.
No global Brave extension policy is emitted; automation receives no convenience
or credential manager. Browser UX supplies only Clear New Tab convenience for
LibreWolf and does not overwrite stock privacy or bundled uBO. Floorp's package
belongs to Browser UX; configuration and native password suppression remain held
until actual Apple password, TOTP and passkey behavior succeeds. Unsupported
integration must retain native stores and keep the role unqualified.

Supported native UI is the source of truth for search/privacy/blockers, with
independent readback. Brave Search is the desired general default; only Brave
Search, Startpage and DuckDuckGo are intentionally configured. Safari uses native
DuckDuckGo. Built-in engine/site-search removal is not claimed. Apple Passwords
remains the sole desired live store, without imports into Orion's separate vault.
Orion uBO uses the Firefox build linked from Kagi's supported guide; disable the
overlapping native content blocker only after uBO is effective, and retain Tracking
Prevention. Its convenience extensions each require compatibility evidence.

The October 13 R3R4 decision supersedes earlier Vivaldi retirement intent.
Vivaldi remains installed and runnable as a visual reference outside the seven
primary roles; Floorp remains the preferred power-user browser. The held active
R3 proposal assigns Vivaldi's cask and application claim to Browser UX and carries
existing policy, extension and rendering selection through core's single physical
writer. Vivaldi-only retraction ownership preserves the prior active-R3 Floorp
policy behavior. Existing preferences, credentials, bookmarks, Mail/Calendar/Feeds,
OAuth and local or unsent data remain available, with no forced feeds-only change.
Homebrew cleanup must be none. Later retirement requires explicit operator
approval and a separate reviewed source release. See the
[R3R4 handoff](../../../../delivery/browser-ownership-r3r4/README.md) for exact
bindings and the deferred Gecko visual-qualification note. Tor and Mullvad are
outside this change. Source adoption requires manager
review of these final bytes and each exact owner patch. Later qualification and
any guard-release edit need their own reviewed disposition. A policy rollback
cannot recover extension storage lost through browser reconciliation.

Fresh source-fixture evidence and held native commands are recorded in the R3
delivery. Nix profile: Orange, bounded owner/host/activation change; legacy large
module receives narrow edits and a data contract. Python profile: Orange for the
automation adapter's filesystem/subprocess boundary; tests use owned fixtures and
never launch a browser. No new Python, Nix input or test-runner dependency is added.
Orion acquisition is explicitly authorized by the requested lineup; its proprietary
app remains a Homebrew cask, with live acquisition/Gatekeeper qualification and
source-only rollback. Primary sources are retained in PRIMARY-SOURCES.json.
