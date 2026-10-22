# Ordinary-user read-only native qualification

R3R3 native status is PENDING. Run this kit on the operator's Mac against the
actual current complete source graph. It evaluates, does not build, apply,
switch, install a browser, write policies or launch a profile. Nix may need its
ordinary daemon/cache permissions. Do not use old R3R1 PASS as new qualification.

From the core repository, choose an empty directory in the configured shared
scratch root. Substitute absolute source roots locally; do not publish them.

```sh
rtk proxy env PYTHONDONTWRITEBYTECODE=1 python3 delivery/browser-ownership-r3r3/prepare-readback.py \
  --scratch "$R3R3_SCRATCH" --browser-ux "$BROWSER_UX_SOURCE" --composition "$COMPOSITION_SOURCE"
rtk proxy env PYTHONDONTWRITEBYTECODE=1 python3 delivery/browser-ownership-r3r3/verify-source.py \
  --owner-candidate "$R3R3_SCRATCH/browser-ux"
rtk proxy env PYTHONDONTWRITEBYTECODE=1 python3 delivery/browser-ownership-r3r3/native-readback.py \
  --selected-flake "$CURRENT_SELECTED_FLAKE" --browser-ux "$R3R3_SCRATCH/browser-ux" \
  --composition "$R3R3_SCRATCH/composition" --output "$R3R3_SCRATCH/native-result.json"
```

`prepare-readback.py` requires exact owner preimages and core bindings, copies
the current sources, and applies only the single R3R3 Browser UX cumulative
patch and retained R3R1 composition patch in empty scratch. Do not additionally
apply R3 or R3R2 Browser UX patches. No external repository is mutated.
`native-readback.py` uses offline/no-lock-update/no-lock-write evaluation,
overrides core/Browser UX/composition inputs to those exact sources, forces both
inactive host toplevel derivation paths and reads both host application claims.
It checks source bindings before and after and sanitizes output paths. The
selected flake must be the actual current full composition, not a reduced
fixture. Review package/claim mappings, one physical writer, old Chrome/Firefox
and office continuity in the resulting readback. No security broker is needed.

Fresh Gecko identity is a separate read-only HTTPS observation:

```sh
rtk proxy env PYTHONDONTWRITEBYTECODE=1 python3 delivery/browser-ownership-r3r3/identity-readback.py \
  --output "$R3R3_SCRATCH/gecko-identity.json"
```

This reads the two immutable upstream files and compares their Git blob SHA1,
then reads AMO's current GUID and package and compares SHA256, manifest ID and
version. A signature container still requires Firefox-family browser signing
verification at a later attended install. `latest.xpi` remains a moving update
route, never immutable identity evidence. Refresh this observation at the
installation session. Keep raw responses and packages local.

If native evaluation is denied, report ENVIRONMENT_DENIED and keep R3R3 native
qualification pending. A pure/inert fixture pass is source verification only.
Do not build, change locks, deploy, log in, connect, launch browsers, or use
privileged host bypasses to turn a provider restriction into qualification.

Maintained ownership regression tests need no external owner or previous-core
root. With `R3R3_NIX_LIB` set locally to the current cached Nixpkgs library path,
run this evaluation-only check from core; the expected result is `[]`:

```sh
rtk proxy nix-instantiate --eval --strict --json --impure --expr '
  let
    libPath = builtins.toPath (builtins.getEnv "R3R3_NIX_LIB");
    lib = import libPath;
  in lib.runTests (import ./src/test/unit/nix/core-browser-ownership.unit.test.nix {
    repoRoot = ./.; system = "aarch64-darwin"; inherit libPath;
  })'
```

This reproduces the maintained source tests, not the complete native graph.
The final amendment remains PENDING_FINAL_INDEPENDENT_REVIEW; this run has no
second independent checkpoint and this kit cannot satisfy that review.
