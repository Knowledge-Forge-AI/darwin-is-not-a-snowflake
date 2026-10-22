# R3R4 retained Vivaldi reference candidate

Status: `BUX_R3R4_VIVALDI_REFERENCE_REVIEWABLE_NOT_SELECTED`.
Independent work review: the sole dispatcher-owned checkpoint was received.
Final revised bytes: `PENDING_FINAL_INDEPENDENT_REVIEW`; no second independent
review occurred in this run.
Source adoption, browser deployment and later retirement remain held.

The October 13 decision retains Vivaldi installed, executable and unchanged as
a visual reference outside the seven primary browser roles. Floorp remains the
preferred power-user browser. Core carries the reference contract; Browser UX
owns the proposed active-R3 Vivaldi cask and application claim. Composition
keeps inactive selection and supplies no second cask writer under active R3.

`CORE-SOURCE-DIFF.patch` is the exact delta from the retained R3R3 core source.
`browser-ux.patch` and `composition.patch` are cumulative replacements against
current local owner preimages, with exact manifests. Apply each once in a
disposable source copy; never stack them with their historical predecessors.
External owner checkouts were read only. Historical R2B through R3R3 delivery
artifacts and attached closure/native receipts remain unchanged and apply only
to their original bytes. The old R3R3 candidate is superseded for adoption.

See `MANAGER-REVIEW.md`, `REVIEW-DISPOSITION.json`, `PRESERVATION-MATRIX.json`,
`core-delta.json` and the
owner delta manifests for the bounded change. `TEST-RESULTS.json` records source
evidence. `NATIVE-READBACK.json` records the fresh native attempt; neither a
fixture nor a historical PASS establishes R3R4 native qualification. The exact
ordinary-user readback procedure is in `HOST-READBACK.md`.

Any change after the sole independent work review leaves final bytes
`PENDING_FINAL_INDEPENDENT_REVIEW`. Separate manager disposition and a new
closure/hash plan are required before source adoption. This marker grants no
commit, publication or browser deployment authority.
