# Revise/close disposition

Disposition: amend

Rationale: accept the source-owned attended installation design and correct
all seven independent advisory findings within the original source-only scope.
Runtime remains inactive. Final bytes are PENDING_FINAL_INDEPENDENT_REVIEW;
there is no second independent review in this run.

The dispatcher supplied producer/current head and tree bindings and a
read-only post-work review. Before revision, all 74 source bindings and all
delivery SHA256 records matched live bytes. The review ran no tests and could
not inspect the external owner or recompute patch hashes. Parent verification
subsequently read the owner, checked exact preimages, replayed the single
cumulative patch in fresh scratch and proved all 15 postimages and reverse
applicability. These are provider-local verification, not another independent
checkpoint or dispatcher finalization. Git identities are evidence only.

| Finding | Disposition and resulting behavior | Final evidence |
| --- | --- | --- |
| 1. Missing maintained ownership coverage | Amend. Eight maintained Nix tests cover semantic merge, duplicates, wrong owners, security relabelling, effect/priority rejection, held procedures and preserving policy merges without external owner roots. | 22 maintained core Nix tests pass; five defects reproduced before correction. |
| 2. R3R2 emitted as current | Amend. Its compatibility output is a superseded historical marker; rejected conflict fields are absent. Native readback labels it historical. Retained R3R2 files are unchanged. | Maintained supersession test and final connected fixture pass. |
| 3. Held roles include installer procedures | Amend. YouTube/Orion methods are their held status values and carry no procedure in semantic entries or rendered plans. | Maintained rendered-plan test passes. |
| 4. Boolean identity and moving version | Amend. Gecko presence takes the identity kit JSON receipt, matches GUID/version/SHA256 reference and signing-container presence, and rejects stale/malformed timestamps. Receipt must be refreshed during the installation session within 24 hours. Version movement requires refresh/comparison, never downgrade. | Python version/receipt/freshness and CLI tests pass; browser signing remains external. |
| 5. Empty baseline/preexisting Proton | Amend. Inventories require complete-readback flags; empty inventories need explicit confirmation. Preexisting Proton must use OBSERVED_EXISTING and is never reported installed by this session. | Python preservation and existing-install CLI tests pass. |
| 6. YouTube convenience owner incomplete | Amend. Retained wrapper convenience belongs to core. CNT's exact source ID is in the plan/matrix/provenance. The unpacked theme's ID depends on its projected path; its core owner and physical projection are recorded separately without fabricating an ID. | Maintained owner test; wrapper/theme/CNT sources remain byte-identical. |
| 7. Composite policy selectors | Amend by conservative rejection. Grouped-ID, wildcard and update-URL selectors, and non-ID additions are held by the data-only merge until separately reviewed selector-aware adoption. | Maintained collision tests pass. No Proton managed policy is adopted. |

Qualification evidence: 45 current core/owner source checks, 57 two-host
constructor/composition/application preservation checks, 22 maintained core
Nix tests, two patched owner Nix contracts, 49 selected Python tests and seven
pre-effect guards in two environment variants passed. Exact core diff and
cumulative owner patch replay, syntax, JSON and bindings are verified in the
final ledgers. Two fresh native baseline evaluations returned
ENVIRONMENT_DENIED. The current identity kit returned IDENTITY_READBACK_HELD
with URLError. No fresh complete composed native graph was obtained; the
ordinary-user full-graph kit remains pending externally. Historical native
PASS does not qualify these bytes.

Unresolved concerns: final independent review, ordinary-user complete native
graph qualification, fresh signed AMO/XPI identity, attended presence and later
connection/DNS/DoH/leak canaries remain pending externally. The unpacked theme's
runtime ID was not read. YouTube and Orion retain their compatibility holds.
Receipt matching binds operator observations to fetched metadata; it is not
independent inspection of installed bytes or cryptographic signing proof.

The amendment adds no dependency, changes no installer/profile/policy runtime,
and preserves unrelated core source, index, locks, owner repositories and
historical evidence. Optional workers were declined because these corrections
share the same source/evidence boundary; no new worker or reviewer ran.

Nix merge judgment is bounded to known semantic inputs with explicit collision
rejection. The existing large browser module receives a narrow adapter
correction with no added runtime responsibility. Python evidence-schema
strengthening is confined to the inactive attended validator and covered by
adverse and CLI tests. Rollback is restoration of the exact retained producer
source in the retained scratch copy; no runtime rollback or deployment ran.
