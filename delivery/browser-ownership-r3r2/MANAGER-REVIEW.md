# R3R2 Proton VPN source revision disposition

Disposition: amend.
Candidate status: `BUX_R3R2_PROTONVPN_COMPATIBILITY_HELD_RETAINED`.
Final-byte review status: `PENDING_FINAL_INDEPENDENT_REVIEW`.

The dispatcher-owned independent review covered producer HEAD
`79021c01e63464020f2e1b447a6f4ec488699ae4`, tree
`6c664c77f97ced998a6e0e7013967d1692236666`. All 42 producer source records matched
at revision entry. That binding is evidence, not a runtime identity gate.
The revisor's corrections receive local source/fixture verification only. No
second independent review was obtained or requested, and final bytes are not
represented as independently accepted. Dispatcher owns terminal disposition and
Git finalization; this report authorizes no owner adoption or runtime action.

## Review finding dispositions

1. **Automation availability: amended.** The retained stock-Brave launcher
   creates a fresh empty temporary data root on every launch, disables extensions
   and forbids extension-loading overrides. There is no persistent target for a
   manual install. The catalog and matrix now say `AUTOMATION_AVAILABILITY_HELD`,
   with no persistent install target and extensions disabled. No fourth Brave
   root, launcher change, login, connection or CDP route is introduced.
2. **Browser-level split ownership: accepted as a held reconciliation item.**
   Retained core `roles-r3.json` assigns Browser UX convenience ownership for
   Brave daily, LibreWolf and Orion. The cumulative Browser UX patch defines
   those same browsers' extension suites. This violates the requested rule that
   the installing repository owns all extension definitions, despite per-Proton-ID
   uniqueness. New checks detect split ownership even for different extension IDs
   and report `singleOwnerQualified: false`. The candidate is held until a
   preserving installer-owned reconciliation is separately reviewed. The earlier
   R3 source, suites and owner patch are retained; no ownership migration is hidden
   inside this additive revision.
3. **No new runtime capability: accepted and clarified.** Core adds held intent
   metadata only; manual official-store installs were already permitted. Source
   reviewability does not establish extension availability, installation or tunnel
   protection in a running profile. R3 inactive output remains null and no writer
   consumes the catalog. Every delivered policy remains empty.
4. **Gecko provenance/update advice: adoption deferred.** `vpn@proton.ch` retains
   the SHA-bound 2026-10-07 AMO/signed-manifest provenance. The producer's `main`
   builder reference is unpinned. Revision fetched the public AMO listing, but
   fresh GUID/API/XPI and immutable upstream reference readback were unavailable.
   No pin or signed identity proof was invented. Identity adoption is held for
   fresh authenticated AMO GUID readback plus commit/tag-bound corroboration.
   `HOST-READBACK.md` adds these source checks and later attended LibreWolf
   signed-update settings/storage continuity checks. The official mutable
   `latest.xpi` locator is an update route, not a pinned package or identity proof.

## Resulting source and evidence

Core retains its minimal catalog exposure in `modules/browsers.nix`; only the
catalog's automation/ownership claims change in this revision. The one cumulative
Floorp owner patch remains byte-identical and preimage-bound. Fresh scratch-only
application checks its 13 postimages and three unchanged catalog dependencies,
including the actual `catalog.firefoxExtensions.trial."vpn@proton.ch"` value.
Floorp remains Browser UX-owned; the patch adds only Proton to its existing held
suite. No external owner file is edited; no composition or Agent-Central delta is
required because package and claim ownership did not change.

`SOURCE-BINDINGS.json`, `DELIVERY-INVENTORY.json`, `SHA256SUMS` and the original
validation reports are immutable producer evidence. They describe producer bytes,
not final-byte passing checks. The original `CORE-SOURCE-DIFF.patch` also remains
producer evidence; `REVISION-CORE-SOURCE-DIFF.patch` is the cumulative revised core
module/catalog/test delta against R3R1. `REVISION-BINDINGS.json` records exact producer
preimages and revised postimages while retaining all thirty R3R1 source records.
`REVISION-INVENTORY.json` and `REVISION-SHA256SUMS` bind the final delivery.
`REVISION-VALIDATION.json` records only qualification actually obtained by the
revisor, including failed reproduction followed by passing focused negatives.

The supplied old inactive R3R1 native PASS remains separate, bound to source digest
`3e9949480754c85020167652aca94208b243daa586fd2d1bfeeabac0fed6495b`
and archive SHA256
`beb6ded7459414ece0b34b3ba3aab5aaff647d671bd0f27828d4a884fc3b7796`.
Its subsequent shell setpgid diagnostic is not a failed Nix check. The amended
R3R2 full native graph/claims remain PENDING; provider denial is not a pass.
Read-only native preparation uses the revision ledger and applies patches only in
scratch, with offline/no-update/no-write-lock evaluation and no build or activation.

## Unresolved concerns and boundary

Single-owner reconciliation, fresh Gecko GUID and immutable corroboration, new
native graph/claims and actual attended browser behavior remain held or pending.
Brave YouTube retains `WRAPPER_PROJECTION_HELD` because its copied/renamed binary,
dedicated root and theme/CNT loading flags do not inherit stock Brave extensions.
Orion retains `REQUESTED_COMPATIBILITY_HELD` / `PROXY_API_UNQUALIFIED`; no new API
proof justifies its conditional attended canary. Public-IP/DNS routes, Firefox
DNS/DoH, account connection counts, auto-connect settings and playback/battery
behavior are unobserved. No active or protected status is asserted.

R3 remains unselected and uncommitted. Earlier R2B/R3/R3R1 evidence, unrelated
source/index bytes, snapshots, locks, profiles, extension storage, wallpaper and
Chrome MCP/CDP routes are preserved. No dependency or mandatory broker is added.
The prior full browser implementation is not repeated. Provider-local verification
cannot authorize commit, snapshot prepare/apply, extension installation, login,
connection, activation or browser profile manipulation.
