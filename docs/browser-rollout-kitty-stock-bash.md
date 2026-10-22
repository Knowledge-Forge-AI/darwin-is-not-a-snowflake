# Required combined browser rollout integration

Stock Kitty enhanced Bash remains **required** in the pending combined browser batch. `deploymentAuthorized=false`; this source candidate authorizes no activation or publication. DESKTOP6 readiness advances independently and does not block this source work. The original KITTY1 phase remains blocked and has not been retroactively accepted.

The batch must select the accepted new core containing this cumulative patch, change the existing `browserRolesR3Selected` literal to true, and select stock enablement in that same reviewed candidate. Regenerate the affected browser-lane locks, snapshots and build identities there. Do not select an older core or silently omit stock. No browser/composition/selection file is edited here.

Apply these additions in the later combined candidate's existing constructor:

```diff
     browserRolesR3Selected = false;
+    kittyStockBashSelected = true;
     mkConfiguration = hostName: hostConfig: extraOverlays: { config, pkgs, ... }: {
+      dinas.kittyStockBash.enable = kittyStockBashSelected;
+      dinas.kittyStockBash.direnv.enable = false;
```

For the requested batch's validation only, require both selection literals true and require evaluated stock enablement and `config.programs.browserBaseline.rolesR3` on each browser-targeted host. Retain that evidence with the batch. Keep the enablement expression independent: a later browser option change must not silently disable stock. Do not add a persistent cross-owner equality assertion: rollout coordination is not a permanent runtime dependency. The core module's `system.build.kittyStockBash` JSON readback exposes evaluated enablement and maintenance/adapter paths. Stock-alone deployment does not complete this requested batch and is not authorized by this note.

Before an authorized activation, render the exact target-user orchestrator, writer and adapter arguments from the accepted core. Read-only preflight runs the writer with `--check` and its existing seventeen arguments, then the adapter with `--check enable COMMON USER HOME CONFIG CMDLINE`. Writer variants must report NOOP or justified ADOPT, without CONFIG-PENDING or transaction/drift conflicts. A genuine cask delta can report REBUILD; that is necessary normal maintenance, and requires explicit batch qualification of changed signatures/TCC before proceeding. The stock check can report PENDING before install or INSTALLED after install. Nonzero means refusal. Do not substitute the old unconditional writer or erase ownership/provenance records.

After activation, repeat both readbacks against the actual cask/generation. Require variant NOOP and stock INSTALLED, exit zero, preserved expected identities/configuration, and the planned native evidence before declaring the combined rollout complete. Optional stock failure prints `required combined rollout remains incomplete` while later activation sections continue. An overall activation exit zero is insufficient. A writer maintenance failure retains the baseline fatal boundary; it is not isolated as a stock failure.

## Deliberate future disable

A later reviewed change may set `kittyStockBashSelected=false` (or directly disable the option) while retaining browsers and both variants. No browser assertion prevents it. Both enable and disable use the same idempotent variant maintenance; disable never falls back to the old rebuilding implementation. The adapter removes/reverts only exact-owned stock selection files, restores validated preimages, preserves user edits and backups, and retains disabled receipts. Repeating disable is a no-op. Inspect readback and any unowned/adopted/relinquished paths: preserved user files can still select the stock config. Restore conflicts manually only with their owner's authorization. Never delete backups or sweep the state directory.

## Upgrade and native qualification boundaries

Cask version/input changes necessarily rebuild copies against the current resources/frameworks while retaining bundle IDs, names, launch commands and config/session semantics. A failed rebuild is explicitly not recovered when old copied code points to replaced cask resources. Same-source interruption can retain/restore the previous coherent app; interrupted publication/registration can resume from exact receipt hashes. Neither unchanged metadata nor retained TCC grants is promised for a necessary version change. Vendor app bytes, signing and ownership remain outside this implementation's mutations.

Native gates remain: real login accounting/startup; selected Nix package paths and ble/fzf/zoxide APIs/keys/rendering; Finder one-window/cwd and running-process reuse; Kitty LaunchServices config-directory isolation and missing optional includes; baseline migration/no-op metadata and signature readback; TCC/FDA responsibility. Shared-history limits and ordinary nested-shell behavior remain documented. Manager B1 accepts host-baseline banner parity, including nested Bash and explicit rc re-source, without claiming VM once-per-window parity. Offline fixture passes do not complete any native gate. Final verification belongs to dispatcher closeout; requested combined rollout acceptance belongs to its separately authorized batch.
