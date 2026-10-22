# Stock application runtime repair

Status: combined source accepted by the parent after independent review amendments.
Real locked Bash/ble.sh startup is qualified in disposable native PTYs. The
installed user rc path, source adoption, build and deployment remain parent
work. Native Brave updater success is not established.

## Parent source composition disposition

The parent accepts the combined source correction, with native deployment still
open. Repair1's dispatcher remains historically blocked at its scope boundary;
its reviewed source candidate is not relabeled as a completed checkpoint.
The three caller integration/document paths closed under checkpoint manifest
`e9ca214cd00cc973a6d9bc78d0bc2d127705bc88da9b0da19a0b9f86d25d2b4e`.
Kitty repair3 closed under checkpoint manifest
`1fd55f251b537463e24a7fb3c3c9702466d91b2e1d13332f2eccadf8234a0c3b`.
The parent verified those resulting bytes and the unchanged Brave source.
Both constructors' eleven native integration checks now pass. The stale active
browser/Kitty descriptions are reconciled in this composition.

The parent also ran the older shell suite: 37 passed, with its two unconditional
placeholder skips retained (selected real packages and GUI/TCC). Real pinned
packages are covered by repair3's maintained tests; a fresh installed window
remains a separate readback. The first two legacy invocations lacked required
fixture variables and failed collection; the corrected invocation ran all 39
cases. These historical setup failures do not count as product failures or passes.

The operator-local palette has the old sample's exact TOML values, with one
comment difference. Its full preimage is retained privately. Parent deployment
removed only the five unsupported default face settings and corrected their
comment, preserving every supported value and all unrelated local configuration.
The remaining native app/updater/window boundaries below are not waived.

## Kitty interactive repair3

The parser and explicit-command checks from the earlier repair did not exercise
normal zero-argument interactive startup. That startup reproduced three
`/bin/zsh: no such option: noprofile` diagnostics and seven missing-face
diagnostics through real Bash 5.3p3 and ble.sh 0.4.0-devel3.

With this Bash, a leading-dash argv0 (`-bash`, including a dash followed by
an absolute path) initializes `BASH` to the account's Zsh. Native experiments
also reproduced that result with the pinned `SHELL` set before exec; changing
`SHELL` in the entry file was therefore insufficient. Stage2 now sets `SHELL`
for the exec and invokes the absolute pinned executable with `--login --posix`.
This gives both `SHELL` and `BASH` the selected executable identity before
ble.sh loads. `--login` retains the login-shell rc chain; the launcher still
passes through `login -fp` for accounting, and stage2 still uses exec without
changing PID or TTY ownership. Explicit commands and exclusion guards retain
their existing paths. No account shell or user rc is changed.

This executable-identity selection is generic Bash behavior, observed here on
Darwin. The enhanced path now exposes the absolute executable path in `$0`
instead of `-bash`, while `shopt login_shell` remains enabled. User rc code can
observe that difference; the parent's private installed-rc check remains
necessary.

The pinned ble.sh invokes `"$BASH" --noprofile --norc -i -c` for three readline
maps at `ble.sh:10960`. Its completion module defines `menu_filter_fixed` and
`menu_filter_input` at `lib/core-complete.sh:5498-5499`, but sourcing with
`--noattach` leaves that module lazy. The shell emitter now synchronously calls
the public `ble-import lib/core-complete` before applying faces and before the
entry's final attach. The import is guarded by ble.sh itself against repeated
loads (`ble.sh:6179-6183`). Import failure gets an explicit diagnostic and skips
the dependent styles. Face setter failures are no longer hidden by `|| true`.

All 73 previously accepted names were audited against the pinned installation.
The Python and Nix closed tables retain 65 supported names. Eight unsupported
names are now rejected as unknown faces:

- `menu_complete_match`, `menu_complete_selected`
- `menu_desc_default`, `menu_desc_quote`, `menu_desc_type`
- `command_suffix`, `command_suffix_new`, `varname_new`

The sample removes the first five, which it previously requested, and retains
the supported ghost-text and menu-filter colors. The last three were accepted
by the validators but absent from the sample and pinned implementation. This
is a compatibility correction: local overrides using any of these eight names
must remove them; runtime validation refuses the invalid override before shell
emission and reports the existing palette fallback diagnostic. No replacement
faces are manufactured, and selection/description effects absent from this pin
are not claimed. Supported syntax, command, filename, variable, region and bell
customization remains available.

## Repair3 verification and limits

The maintained Python tests render the actual launcher, stage2, entry, early
and interactive templates with pinned runtime replacements and fixture
system/user rc files. The acceptance paths use real locked Bash and ble.sh;
neither `ble-face` nor `ble-import` is replaced. The login adapter is a fixture
that records the unchanged accounting arguments without updating real login
state. Homes, rc files, caches, history and runtime files are disposable.

Native PTY checks cover zero-argument stage2 and launcher startup, entry from
an inherited `-bash`/`SHELL=/bin/zsh` process, detached early loading, first
prompt, a command, login rc order, unchanged PID, selected `SHELL`/`BASH`,
completion import, exact sample face values, and bounded clean exit without
the reported diagnostics. Another real-runtime check sets and reads all 65
accepted faces and re-sources the rc helpers without duplicating the history
hook or completion module. Plain, SSH, dumb-terminal, owner and noninteractive
guards, explicit command arguments, existing validation/injection/no-write
contracts, and Python/Nix face-table parity are checked separately.

The PTY diagnostic check rejects both `ble-face: ... not found` and the pinned
deferred `ble.sh: the specified face ... is not defined.` form. Two real-runtime
probes request the seven legacy sample faces before completion initialization,
using `ble-face -s` and `ble-color-setface`, then require rejection of every
reported face. The deferred probe failed with the former matcher and passes
with the corrected matcher. These are diagnostic-detector probes; normal
startup acceptance continues to render the production templates unchanged.

The original code failed the real PTY regression with all three Zsh option
errors and all seven startup face errors. A pre-exec `SHELL` assignment alone
still failed; the absolute argv0 plus `--login` correction passed. The full-face
runtime test also reproduced the three additional unsupported names before
their removal. These checks establish source-template behavior in a native
disposable environment. They do not establish the installed user's actual rc
behavior or successful normal deployment. The parent must privately exercise
a fresh stock Kitty window after source adoption and the normal build. No
private transcript is needed in this document.

Re-run the focused source checks from Core with the parent's native toolchain.
Use a task-owned scratch directory and the existing locked Nixpkgs library:

```sh
rtk env KITTY_STYLE_TEST_NIX_LIB="$locked_nixpkgs_lib" TMPDIR="$task_scratch" PYTHONDONTWRITEBYTECODE=1 pytest -q -o cache_dir="$task_scratch/pytest-cache" src/test/unit/python/kitty_stock_style.unit.test.py
rtk nix-unit --arg repoRoot "$PWD" --argstr system aarch64-darwin --arg lib "import $locked_nixpkgs_lib" src/test/unit/nix/kitty-stock-style.unit.test.nix
rtk env TMPDIR="$task_scratch" bats src/test/unit/bash/kitty-config.unit.test.bats
rtk env TMPDIR="$task_scratch" PYTHONDONTWRITEBYTECODE=1 KITTY_STOCK_TEST_TMP="$task_scratch" KITTY_STOCK_TEST_LOGS="$task_scratch/logs" python3 tests/test_kitty_stock_shell.py --record
```

The PTY tests default to the installed locked Bash/ble.sh store paths and accept
explicit `KITTY_STOCK_TEST_BASH` / `KITTY_STOCK_TEST_BLESH` overrides for parent
readback. The other observed installed package defaults are fixture pins, not
derived dynamically from `flake.lock`; rebuild readback can override them with
`KITTY_STOCK_TEST_COMPLETION`, `KITTY_STOCK_TEST_FZF`, `KITTY_STOCK_TEST_ZOXIDE`,
`KITTY_STOCK_TEST_TOOLS` and `KITTY_STOCK_TEST_STYLE_PYTHON`. Every required
replacement is checked, including the style interpreter. A missing-interpreter
probe produced three explicit prerequisite skips instead of palette failures.
Missing packages or denied PTY allocation are reported as skips,
which require a parent run and are not qualification. No dependency, lock,
coverage gate or runner policy is changed. The shared existing test file owns
this bounded startup fixture because repair3's write scope permits no new test
owner; broader test-file decomposition is deferred.

Fresh revision checks on 2026-10-19:

| Check | Result |
| --- | --- |
| Python style, real locked PTY startup, guard, diagnostic and parity tests | 98/98 passed; no skips |
| Nix style unit tests | 10/10 passed |
| Kitty configuration, native parser and receipt Bats fixtures | 4/4 passed |
| Older stock-shell suite (`tests/test_kitty_stock_shell.py --record`) | 39 run: 37 passed, 2 explicit skips; no failures/errors |
| Locked Bash syntax for stage2, entry, early and interactive templates | 4/4 passed |
| Locked Nixpkgs library identity | Source NAR hash matched `flake.lock` |
| Scope preservation | HEAD/index, all unowned tracked bytes and 87 unrelated untracked files unchanged |

The older suite's skips are its native GUI/TCC check and its placeholder
selected-package scenario. Those legacy skip messages do not establish package
absence: the maintained 98-test suite above exercised the real installed
packages separately. GUI/TCC remains parent work. Every recorded real-runtime
PTY child from this revision was confirmed absent after the tests.

The producer's bounded Gemini worker supplied a read-only pinned-capability
audit; its result was checked against native runtime behavior and its cleanup completed.
No reviewer was invoked by the producer. The dispatcher owns the independent
checkpoint. No installed profile/config/cache/history, VM, dependency, lock,
test runner or coverage policy was modified.

## Independent work-review disposition

The supplied review's diagnostic gap and package-prerequisite gap are amended
as described above. The Darwin-specific wording is corrected, and the `$0`
compatibility difference is now explicit. The startup ordering and 65-face
capability corrections are retained, with fresh real-runtime qualification.
No second independent review was requested or obtained after this revision;
the revisor's checks do not constitute another dispatcher checkpoint.

The active [stock Bash guide](kitty-stock-bash.md) still claims selected/matched
menu-item and description faces and calls the `noprofile` cause undiagnosed.
That guide is outside the eleven-path scope. The parent must reconcile those
statements against this pinned-capability and executable-identity correction,
alongside the already assigned combined README/status reconciliation. Build,
deployment and private installed-user startup remain external acceptance work.

## Repair3 candidate checkpoint

The candidate remains unstaged and uncommitted. These hashes bind all ten
owned source/test files, including the unchanged early/interactive helpers and
the adopted Bats parser/receipt correction. This document's final hash and the
aggregate of all eleven owned paths are returned in the handoff. The aggregate
uses sorted lines of `SHA256  repository/path` with a final newline.

| File | SHA-256 |
| --- | --- |
| `config/kitty/stock-bash-stage2.sh` | `13558f61567829d7d3c0f22f3662f8d32c9142c908c00b4f356aa701de107b6b` |
| `config/kitty/stock-bash-entry.bash` | `45299c7830d0a63dfb89c35faa264d70cb483d2af626ae121774496601949952` |
| `config/kitty/stock-bash-early.bash` | `3b24300109b831dfb2cc04fcd32e570ca7bc56f71e9301ed1ffacab0e1039a23` |
| `config/kitty/stock-bash-interactive.bash` | `9258cdd962689bae19a9088c03175916c6335a4aec085ae1f9c200de850ca2ed` |
| `config/kitty/stock-bash-style.toml` | `bba7926c9e7174eecdb80fb2f271128fc2e4f24dcd3b475b8881f40154491a22` |
| `tools/kitty-stock-style.py` | `4364c14fb7029b8036640fc8a3c2b0377f63ed2eb9f9a524df1e3e6e5068fb9e` |
| `lib/kitty-stock-style.nix` | `f23c15ad9b24d9bc0f7a73af7488a21fe148f2edc55a5faa5124facf3e8c00eb` |
| `src/test/unit/bash/kitty-config.unit.test.bats` | `c81f18c4971454fd1eae5f3ac65ae8aa3ff3d0474eaf38b704004f353e5568cb` |
| `src/test/unit/python/kitty_stock_style.unit.test.py` | `a315d75ea3a610867cc7d6e5cefccd8f31b93311eef4e1ff91389c8c19b1eab7` |
| `src/test/unit/nix/kitty-stock-style.unit.test.nix` | `2e43629bb30b11a7f44effaa9237c231b5efa5cf69eb2c31edc1aacc2c9acb11` |

## Historical repair1 record: review-blocked source candidate

The following sections preserve the earlier Brave/parser repair1 provenance,
including its then-outstanding integration and documentation scope blockers
and historical hashes. Later parent amendments are pending in the shared
working tree and are preserved by repair3. The parent owns reconciliation and
the final combined affected gates; the historical results below are not fresh
repair3 evidence or a native Brave updater success claim.

### Scope and resulting behavior

Kitty's required absolute stock include uses `include`. Its optional relative
palette remains `globinclude`. The installed Kitty 0.47.3 parser reproduced the
old `Non-relative patterns are unsupported` error with shell `.`; the corrected
source selects the stock launcher without parser errors.

Stock Brave is acquired through Homebrew's official `brave-browser` cask at
`/Applications/Brave Browser.app`. The [official cask definition](https://github.com/Homebrew/homebrew-cask/blob/master/Casks/b/brave-browser.rb)
declares `auto_updates true` and the upstream app artifact. A small `bin/brave`
adapter forwards arguments to that canonical app. It installs no application
bundle through Nix. This replaces the stock Nix acquisition and shared Brave
suite statements in [ADR 0003](adr/2026/10/0003-additive-stock-browser-acquisition.md)
for this repair; the host selectors and policy decisions remain as recorded.

The signed `pkgs.brave` overlay remains the independent source for the existing
Brave YouTube writer. Stock Brave has one `homebrew-cask` / `brave-browser`
claim; Brave YouTube retains its separate `nix` / `brave` claim. Neither carries
a Brave suite association. YouTube's callsite, writer, profile and artwork are
unchanged.

When stock acquisition is selected, guards reject external stable declarations,
including repeated string/attribute declarations, beta/nightly casks and Nix
Brave application packages. Function-form casks use their merged names without
guard evaluation errors. Package overrides are allowed when the owned CLI
adapter remains; cask overrides cannot suppress the owned declaration. Generic
unselected defaults remain available. The R3 automation adapter still exits 78
before launch; its unreachable target now
names the canonical stock app. R3 selection, policy and profile holds remain.

Kitty activation code and receipt schema are unchanged. Fixture execution
proves that a managed v1 source upgrades to the corrected bytes while retaining
the first preimage and backups, including an initially adopted identical file.
Disable restores the preimage. Existing refusal of edited backups, interrupted
write conflicts and directory collisions is preserved. Existing active-edit
handling remains: re-enable backs up an edited selection, and disable preserves
it in place and relinquishes ownership. This is not a new blanket refusal of
every active-file edit.

### Verification

Re-run after revision with Nix 2.31.5, Bats 1.12.0 and native Kitty 0.47.3:

| Check | Result |
| --- | --- |
| Browser ownership module tests | 40/40 passed |
| Browser acquisition/claim unit tests | 6/6 passed |
| Kitty configuration, native parser and receipt fixtures | 4/4 passed |
| Existing Brave YouTube configuration and app-writer fixtures | 7/7 passed |
| Native parser using the unchanged absolute runtime include | Passed; stock launch selected, optional palette absent/present |
| Complete application-claim integration suite | Blocked; 0/9 completed successfully because the sandbox denied the native daemon socket |
| Browser baseline integration suite | Blocked; 0/11 completed successfully because the sandbox denied the native daemon socket; stale assertions also remain, as described below |
| Patch whitespace check | Passed |

The module tests evaluated the actual browser module with package-rendering
fixtures and nix-darwin-style cask coercion, using the locked Nixpkgs library.
Its local source NAR hash was rechecked with `nix hash path` against
`flake.lock`. A task-owned local evaluation store was necessary because the
sandbox denied the system daemon socket. Both integration suites were attempted
with the native store and stopped at that socket boundary. The earlier producer
also encountered locked-input network resolution failures with an isolated
store. None of these attempts establishes an integration pass. No runners,
thresholds, test manifests, dependencies or pins were changed.

The maintained parser regression accumulates bad lines and asserts the chosen
shell and palette values. A separate native check loaded the source verbatim
from an isolated directory, with the existing absolute runtime include intact.
Neither check opens a window or proves a real interactive Bash session.

Re-run the focused suites from Core with the parent's native toolchain:

```sh
rtk nix-unit --arg repoRoot "$PWD" --argstr system aarch64-darwin src/test/unit/nix/browser-acquisition.unit.test.nix
rtk nix-unit --arg repoRoot "$PWD" --argstr system aarch64-darwin src/test/unit/nix/core-browser-ownership.unit.test.nix
rtk nix-unit --arg repoRoot "$PWD" --argstr system aarch64-darwin src/test/int/nix/application-claims.int.test.nix
rtk nix-unit --arg repoRoot "$PWD" --argstr system aarch64-darwin src/test/int/nix/browser-baseline.int.test.nix
rtk bats src/test/unit/bash/kitty-config.unit.test.bats src/test/unit/bash/brave-youtube-config.unit.test.bats src/test/unit/bash/user-home-app-writers.unit.test.bats
```

The claim integration suite compares the complete inventory against native
cask, Mac App Store, Nix application-directory and generated-bundle outputs.
Its added assertion checks independent Brave ownership and the absence of
`Brave Browser.app` from Nix-installed applications. Native completion remains
required before deployment acceptance.

### Review disposition and scope blockers

The producer's eight file hashes matched the candidate inspected at revision
entry. The supplied independent review was dispositioned as follows:

1. **Accepted, unresolved scope blocker:**
   `src/test/int/nix/browser-baseline.int.test.nix` still tests the retired
   acquisition. `testStockBraveAndYoutubeUseTheSameDmgPackageOnBothHosts`
   expects a stock `pkgs.brave` derivation and no Brave cask;
   `testConstructorSelectsAcquisitionWithMatchingCoreClaims` excludes
   `brave-browser` from the expected new casks. These assertions contradict the
   accepted cask/CLI design. The file is outside this task's write scope and
   remains unchanged. The parent must authorize or perform that amendment and
   obtain native results from both integration suites before accepting the
   source unit. The socket failure did not execute these conflicting assertions.
2. **Accepted, deferred documentation correction:**
   [the browser README](../config/browser/README.md) and
   [ADR 0003](adr/2026/10/0003-additive-stock-browser-acquisition.md) still
   describe Nix stock acquisition and shared suite claims. Both are outside
   this task's write scope. This repair records the replacement decision, but
   the parent still needs to reconcile those active documents.
3. **Amended:** the guard checks package names before comparing output paths
   and safely skips opaque raw cask definitions. Regression cases cover an
   unrelated package whose output throws and function-form casks with implicit
   or explicit `config.name`, including stable, beta and nightly conflicts.
4. **Amended in part:** the generic package-priority prohibition is replaced
   by an owned CLI presence check. `mkForce` and `mkOverride` remain valid when
   that adapter is retained. The selected cask-priority check is retained to
   reject replacement by a function-form stable cask whose raw name is opaque;
   the regression covers that case. No unselected generic contract changes.
5. **Amended:** the v1/v2 fixture comparison now captures `cmp` status and
   requires exactly 1, so identical files or comparison errors fail the test.
6. **Amended:** pre-switch instructions below inspect canonical app existence
   and Homebrew ownership before any cask installation.

The revised guard regressions first exposed the raw-function error, eager
output forcing and broad package-priority rejection; all 40 module tests pass
after correction. No second independent review was requested or obtained
after revision. These local checks do not replace the dispatcher's checkpoint.

### Parent build, deployment and readback

After the parent resolves the scope blocker and accepts the source, bind the
candidate through the parent's normal source-adoption process. An unchanged
older source pin would build the
older implementation. Follow the [domain deployment guide](ops/domain-change-deployment.md)
for the selected top-level composition. The ordinary commands are:

```sh
rtk ./bin/nix-update build
rtk ./bin/nix-update deploy --dry-run
rtk ./bin/nix-update deploy
```

Before switching, inspect the native selected configuration: exactly one stable
Brave cask, only the CLI adapter in system packages, no stock Brave bundle under
`system.build.applications`, separate YouTube source/claim, and no R3 release.
Inspect both full integration results. Also inspect whether the canonical app
already exists and whether Homebrew owns it:

```sh
rtk ls -ld '/Applications/Brave Browser.app'
rtk brew list --cask brave-browser
```

An absent app/cask produces a nonzero inspection status. An existing app
without matching cask ownership requires parent reconciliation before switching;
Homebrew can refuse to install over it. Preserve the app and browser data rather
than force-overwriting or deleting them. Generation rollback alone does not
reconcile mutable cask state; any acquisition rollback needs a
separately reviewed plan that preserves browser data.

After the separately authorized switch, confirm that the target user can write
the canonical app and required parent directories, the app is an ordinary cask
installation, and the old Nix application export is absent. Verify the new
app's signature and Gatekeeper assessment:

```sh
rtk codesign --verify --deep --strict --verbose=2 '/Applications/Brave Browser.app'
rtk spctl --assess --type execute --verbose=4 '/Applications/Brave Browser.app'
rtk brave --version
```

The parent separately preserves and reconciles the exact stale cache relauncher
after inspecting its metadata. This source correction neither deletes a cache
nor repairs permissions on existing cache/store/root trees. An attended built-in
update must still prove replacement and relaunch. A cask declaration or a good
signature does not prove that updater transaction succeeds.

For deployed Kitty parser readback, the parent can run:

```sh
rtk /Applications/kitty.app/Contents/MacOS/kitty +runpy 'import os; from kitty.config import load_config; bad=[]; c=load_config(os.path.expanduser("~/.config/kitty/stock-bash.conf"), accumulate_bad_lines=bad); assert not bad, bad; assert c.shell.endswith("/launch"), "stock launcher not selected"; print("parser OK; stock shell selected")'
```

Then open a fresh stock Kitty window and check `BASH_VERSION` and the actual
shell process. Read back the upgraded receipt, first preimage and backups
without altering them. A parser pass alone is not shell-launch evidence.

### Candidate checkpoint (historical repair1)

The source remains unstaged and uncommitted after disposition of the supplied
independent review. The revision used no additional workers or reviewers.
Existing delivery artifacts, the index, source pins, YouTube implementation and
Kitty activation implementation were checked for preservation. This checkpoint
is an amended candidate with the scope blockers above, not deployment acceptance.

SHA-256 values below bind the seven changed source/test files. This document's
own hash and the complete eight-file candidate digest are returned in the
revision handoff to avoid a self-referential document hash.

| File | SHA-256 |
| --- | --- |
| `config/kitty/stock-bash.conf` | `9f7f7e445af67678ed39df0463e295328a17a6f5090ec2c7f7975a7116ef61e1` |
| `lib/core-application-inventory.nix` | `6d37f75c25e04ae15967b347caaa0fcf7a9174c9eea1b1bbcd58df504085745f` |
| `modules/browsers.nix` | `0419ba0277b955ba64481a0efa039915b9980accdb5432c5ea435ab28ac122c2` |
| `src/test/int/nix/application-claims.int.test.nix` | `ceb6f4aca010cbb63d6b6ef5bff6f51c6f671e139e9848aafbe68e16320ca0ce` |
| `src/test/unit/bash/kitty-config.unit.test.bats` | `c81f18c4971454fd1eae5f3ac65ae8aa3ff3d0474eaf38b704004f353e5568cb` |
| `src/test/unit/nix/browser-acquisition.unit.test.nix` | `413f15cbe95c8dbacc83c03b9e5635a6e986609165e9c28a204288f77315a9ab` |
| `src/test/unit/nix/core-browser-ownership.unit.test.nix` | `f270e5f414414335ccefe396a77cc35bd1f01594cc96a30660ff30e65eafcb9d` |

The complete candidate digest uses sorted lines of `SHA256  repository/path`
with a final newline, including this document.
