# Ordinary-user read-only R3R4 qualification

Use the actual selected complete composition and current local owner sources.
Run as the ordinary user with normal Nix daemon permissions. These commands
evaluate source; they do not build, switch, install, write policy or launch a
browser. Keep absolute source roots and raw diagnostics local.

Choose an empty directory under the configured shared scratch root. Set
`R3R4_SCRATCH`, `BROWSER_UX_SOURCE`, `COMPOSITION_SOURCE`, `BROWSER_CONFIG`,
`CURRENT_SELECTED_FLAKE` and `R3R4_NIX_LIB` locally. `BROWSER_CONFIG` is the
current browser configuration TOML, not a profile. The shipped configuration
binding must match; drift requires bounded manager disposition, not a reset.

```sh
rtk proxy env PYTHONDONTWRITEBYTECODE=1 python3 delivery/browser-ownership-r3r4/prepare-readback.py \
  --scratch "$R3R4_SCRATCH" --browser-ux "$BROWSER_UX_SOURCE" \
  --composition "$COMPOSITION_SOURCE" --browser-config "$BROWSER_CONFIG"
rtk proxy env PYTHONDONTWRITEBYTECODE=1 python3 delivery/browser-ownership-r3r4/verify-source.py \
  --owner-candidate "$R3R4_SCRATCH/browser-ux-r3r4" \
  --composition-candidate "$R3R4_SCRATCH/composition-r3r4"
rtk proxy env PYTHONDONTWRITEBYTECODE=1 python3 delivery/browser-ownership-r3r4/reproduce-patches.py \
  --scratch "$R3R4_SCRATCH" --output "$R3R4_SCRATCH/patch-reproduction.json"
rtk proxy env PYTHONDONTWRITEBYTECODE=1 python3 delivery/browser-ownership-r3r4/verify-fixtures.py \
  --scratch "$R3R4_SCRATCH" --lib-path "$R3R4_NIX_LIB" --output "$R3R4_SCRATCH/source-tests.json"
rtk proxy env PYTHONDONTWRITEBYTECODE=1 python3 delivery/browser-ownership-r3r4/native-readback.py \
  --selected-flake "$CURRENT_SELECTED_FLAKE" \
  --browser-ux "$R3R4_SCRATCH/browser-ux-r3r4" \
  --composition "$R3R4_SCRATCH/composition-r3r4" --browser-config "$BROWSER_CONFIG" \
  --output "$R3R4_SCRATCH/native-result.json"
```

Preparation copies sources, applies only the R3R4 cumulative owner patches, and
reconstructs the retained R3R3 comparison copies in scratch. It changes no live
owner or Git index. Do not also apply earlier cumulative patches. The fixture
compares inactive modules/claims/office selections and active held Vivaldi
cask, claim, rendering, policy bytes, extensions, productivity and refusal.
Source parsing and fixture output remain source evidence.
The revised fixture also compares active Floorp policy and attended activation
against R3R3 and rejects any new Floorp policy deletion. Vivaldi retains its own
legacy retraction ownership through the single existing core writer.

This kit binds the post-review revised bytes, which remain
`PENDING_FINAL_INDEPENDENT_REVIEW`. No second independent review occurred in the
revision run. Source adoption requires separate manager disposition and a new
closure/hash plan.
Patch reproduction independently reconstructs the proposed owner files from
the historical R3R3 projection plus the incremental R3R4 delta and regenerates
each cumulative patch against its local preimages. Both patch hashes must
match the handoff manifests exactly. Use a fresh prepared scratch directory for
each reproduction attempt.

Native readback uses offline, no-lock-update and no-lock-write evaluation and
exact core/Browser UX/composition overrides. It forces both inactive host
toplevel derivation paths and full application claims. It checks final source
bindings before and after and compares canonical readback digests, excluding
only the schema revision label, with `BASELINE-NATIVE.json`. A changed office,
cask, package or claim projection fails this comparison. An old native PASS is
historical; only this fresh exact-byte readback can qualify inactive R3R4.
The native command also binds the current browser configuration bytes before
and after evaluation and selects its configuration directory through the
existing `localConfig` input. The fixture checks the canonical configuration
copy against the same binding before evaluating any module.

Review both host outputs and one Vivaldi cask/claim on the target, with no new
predecessor claim. Confirm office continuity and no unrelated global freeze.
Active R3 remains mechanically held and has source-fixture evidence only; a
native inactive PASS is not active deployment or browser-effectiveness proof.
If denied, report `ENVIRONMENT_DENIED` and leave native qualification pending.
Do not use privileged bypasses or security brokers. Return sanitized result
JSON and its SHA-256 for separate manager disposition and a new closure plan.
