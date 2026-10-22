# Manager disposition requested for R3R4

Produced candidate: `BUX_R3R4_VIVALDI_REFERENCE_REVIEWABLE_NOT_SELECTED`.
Review boundary: the sole dispatcher-owned independent work review was received.
Final revised bytes: `PENDING_FINAL_INDEPENDENT_REVIEW`. No second independent
review occurs in this run; separate manager disposition and a new closure/hash
plan are required before adoption.

The old retained source decommissioned Vivaldi, omitted its active-R3 cask and
claim, and selected a null rendering. The R3R4 replacement retains an explicit
visual reference outside the seven roles, adds its cask and claim to Browser
UX's active target module, and carries the existing Vivaldi rendering and
policy/convenience definitions through core's existing physical writer. Floorp
remains preferred. Later retirement requires explicit operator approval and a
separate source release; disabling the reference, listing it as retired, or
enabling Homebrew cleanup fails source assertions.

Core's legacy exclusivity check needed one bounded change: when both held R3
selectors and the retained reference contract are present, separate Vivaldi
contributions may coexist with the legacy primary-browser baseline. Chrome,
Firefox and Floorp exclusivity, identifier checks, duplicate detection and
priority checks remain enforced. Maintained negative tests cover that boundary.
No installation ownership moves into core.

The work review identified a Floorp preservation regression: carrying the legacy
shared retraction flag into active R3 introduced deletion of Floorp's policy
plist. The revision carries that flag through a Vivaldi-only option instead.
The common Vivaldi/Floorp flag retains its existing inactive behavior. Connected
fixtures compare the active Floorp policy selection and attended activation text
with R3R3 and reject a new Floorp deletion; the new activation check failed on
the producer bytes and passes after correction. The core unit fixture also
proves Vivaldi-only retraction leaves Floorp alone and shared legacy retraction
still covers both browsers. The ADR now records the retained reference and
explicit later-retirement approval. `REVIEW-DISPOSITION.json` records all three
findings, the supplied producer binding and acceptance of the prior stage deltas.

The cumulative composition handoff retains the accepted R3R1 functional repair;
the R3R4 delta corrects its obsolete decommission comment only. Active R3
composition contributes no Vivaldi/Floorp casks, avoiding a second writer.
Inactive selection, both host claims and office selections remain equivalent
in the connected source fixture. Fresh native evidence is recorded separately.

Exact before/after hashes are in `core-delta.json`, `browser-ux-manifest.json`
and `composition-manifest.json`. Incremental R3R3-to-R3R4 patches are explanatory;
only the cumulative patches are eventual owner-adoption inputs. `SOURCE-BINDINGS.json`
binds source inputs; `SHA256SUMS` and its self digest bind delivery evidence.
Historical receipts are preserved, with their original lineage recorded in
`PREFLIGHT.json` and `BASELINE-NATIVE.json`.

Verification evidence: `TEST-RESULTS.json`, `PATCH-VERIFICATION.json`,
`NATIVE-READBACK.json` and `PRESERVATION.json`. Source fixtures use real local
modules/claims and inert package/artifact boundaries. They establish source
selection and preservation, not installed browser effectiveness. Native
evaluation may be environment denied; the ordinary-user kit compares both
inactive host readbacks and claims against the exact historical native receipt.

Deferrals: attended executable/policy/extension/data readback, any Gecko visual
recreation, full active native/browser qualification, owner adoption, source
closure and manager disposition. No profile was opened or read. No security
broker, Agent-Security or JACA admission dependency was added.

The Gemini worker performed only bounded owner-source investigation; parent
verification owns acceptance. The independent checkpoint remains exclusively
dispatcher-owned. These post-review corrections leave final bytes
`PENDING_FINAL_INDEPENDENT_REVIEW`. Require a separate manager disposition and
new closure/hash plan before any source adoption; no source-commit authority is
implied by this delivery.
