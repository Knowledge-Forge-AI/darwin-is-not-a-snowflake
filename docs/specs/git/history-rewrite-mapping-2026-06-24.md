# Nix-Darwin Root-To-Tip Rewrite Mapping

Date: 2026-06-24

## Goal

Rewrite `<repo>` history into a clean narrative that describes
the repository as it should have grown, not as the exploratory Nix learning
session actually unfolded.

The rewrite should make the final architecture look intentional from the
beginning:

- Nix modules are small and domain-focused.
- Config/resources live under `nix-darwin/config/<domain>/`.
- User-facing commands live under `nix-darwin/bin/`.
- Implementation helpers live under `nix-darwin/libexec/<domain>/`.
- Rarely used user tools live under `nix-darwin/tools/<domain>/`.
- Tests live under `nix-darwin/src/test/{unit,int}/<language>/`.
- Source files follow the one-language-per-file rule from their first
  appearance.

Safety refs already pushed:

```text
origin/main                                      950032f
origin/backup/main-before-history-rewrite-20260624 950032f
```

## Rewrite Method

Use an orphan reconstruction branch rather than a giant interactive rebase.

Recommended sequence:

1. Create `codex/rewrite-main-history` from current `main`.
2. Create an orphan working branch from the same final tree.
3. Build clean commits by selectively adding path groups from the final tree.
4. Preserve `backup/main-before-history-rewrite-20260624` untouched.
5. Run verification after the reconstructed branch is complete.
6. Replace `main` only after explicit approval.
7. Push rewritten `main` with `--force-with-lease` only after explicit approval.

## Rewrite Execution Status

As of 2026-06-24:

```text
main                                           ed1a0e2
backup/main-before-history-rewrite-20260624   950032f
origin/main                                    ed1a0e2
origin/backup/main-before-history-rewrite-20260624 950032f
codex/rewrite-main-history                     ed1a0e2
```

The local `codex/rewrite-main-history` branch has a 17-commit reconstructed
history and an identical final tree to `main`. Remote `main` has been rewritten
with `--force-with-lease`.

Verification completed before replacing `main`:

```text
git diff --check
nix-darwin/bin/codex-tooling-self-heal
nix-darwin/bin/nix-unit-test --report
nix flake check nix-darwin --no-build
```

`nix flake check nix-darwin --no-build` passed with upstream/deprecation
warnings only. The full build check was not run during this rewrite step.

Post-push cleanup:

- Deleted local `backup/html-test-report-tree-before-cleanup`.
- Kept local and remote `backup/main-before-history-rewrite-20260624` as the
  durable rollback point.

## Target Commit Map

| # | Proposed Subject | Purpose | Source Paths | Old Commits Absorbed | Verification |
|---|---|---|---|---|---|
| 1 | Initialize nix-darwin flake structure | Establish the final repo root, `.gitignore`, flake inputs, host map, package policy, module import pattern, and README-level orientation. Include `.gitignore` in the initial skeleton, but do not add a license yet because the repository remains private until the security implications of publication are better understood. This commit should skip the early learning churn around nix-homebrew, UID experiments, and deprecated options. | `.gitignore`, `nix-darwin/README.md`, `nix-darwin/flake.nix`, `nix-darwin/flake.lock`, `nix-darwin/modules/nixpkgs-policy.nix` | `6282b30`, `06a7053`, `4b9c10c`, `520c8f3`, `76f2a24`, `87a40a5`, `056bc75`, `2db2e10`, `46b6432`, `f98ac2f`, `b8b8bf5`, `e567d64`, `5387417`, `34a7bd5`, `57b2c5`, `466bee9`, `b0580bf`, `c206226` | `nix flake check nix-darwin --no-build` |
| 2 | Document project standards | Add public-facing, scrubbed project standards for coding, security, and commit hygiene. Keep private local/device details in `<private-user-docs>`, with project-standard paths symlinked back to the repo docs. | `nix-darwin/docs/specs/**` | Current standards-doc split and related planning discussion | Markdown review and symlink readback |
| 3 | Add core shell and language manager modules | Add zsh/bash profile management, Oh My Zsh, SDKMAN, NVM, pyenv, rbenv, managed shell fragments, and the final shell startup ordering. Code should appear already extracted into config/libexec paths. | `nix-darwin/modules/shell.nix`, `nix-darwin/modules/oh-my-zsh.nix`, `nix-darwin/modules/sdkman.nix`, `nix-darwin/modules/nvm.nix`, `nix-darwin/modules/pyenv.nix`, `nix-darwin/modules/rbenv.nix`, `nix-darwin/shell/**`, `nix-darwin/config/sdkman/**`, `nix-darwin/libexec/sdkman/**`, `nix-darwin/libexec/nvm/**`, `nix-darwin/libexec/pyenv/**`, `nix-darwin/libexec/rbenv/**` | `57b2c5`, `c16074a`, `132289b`, `e063871`, `03d3c3e`, `ae6c4e9`, `bdaaa8e`, `09d8cc1`, `78739f2` | `nix-unit-test --report` shell and zsh lanes |
| 4 | Add desktop app and package management modules | Add Homebrew casks, independent desktop apps, VS Code, Codex app/CLI, Android tooling, GIMP, Git config, OrbStack, and DBeaver-era app management without preserving install false starts. | `nix-darwin/modules/brewfile-stragglers.nix`, `nix-darwin/modules/desktop-apps.nix`, `nix-darwin/modules/vscode.nix`, `nix-darwin/modules/codex.nix`, `nix-darwin/modules/android.nix`, `nix-darwin/modules/gimp.nix`, `nix-darwin/config/vscode/**`, `nix-darwin/config/gimp/**`, `nix-darwin/config/git/gitconfig`, `nix-darwin/libexec/git-gui/**` | `fcbfe47`, `7b6c627`, `f90282b`, `919f522`, `f488b2e`, `e49d854`, `e6e8d75`, `c952da9`, `27d61b5`, `1340ec9`, `319e434`, `2f17969` | `nix flake check nix-darwin --no-build` |
| 5 | Add terminal UX modules | Add Kitty launchers, Terminal profile management, fastfetch, monitor-temp, shell themes, and terminal-specific assets. History should not show the failed image/ascii experiments; it should show the final separated config and helper structure. | `nix-darwin/modules/kitty.nix`, `nix-darwin/modules/terminal.nix`, `nix-darwin/modules/fastfetch.nix`, `nix-darwin/bin/monitor-temp`, `nix-darwin/config/kitty/**`, `nix-darwin/config/fastfetch/**`, `nix-darwin/libexec/monitor-temp/**`, `nix-darwin/shell/oh-my-zsh-custom/**`, `nix-darwin/shell/sh_config/bash/steeef-custom.bash-theme` | `da74465`, `c16074a`, `c0007d7`, `721e563`, `4a21131`, terminal/fastfetch work from 2026-06-18 to 2026-06-20 | `nix-unit-test --report` terminal, kitty, fastfetch, bash-theme, zsh-theme lanes |
| 6 | Add host security and network tooling | Add SSH, host security, VPN checking, Proton VPN traffic guardrails, Mullvad, fingerprint sudo, and extracted awk/shell helpers. | `nix-darwin/modules/ssh.nix`, `nix-darwin/modules/host-security.nix`, `nix-darwin/modules/password-sync.nix`, `nix-darwin/bin/proton-vpn-*`, `nix-darwin/config/security/**`, `nix-darwin/libexec/host-security/**`, `nix-darwin/libexec/proton-vpn/**` | `2c92ab3`, `05b4d01`, `b6a32f4`, `9b22a7c`, `7b684c3`, `136d640`, `e223d72`, `3289419`, `0acf004` | `nix-unit-test --report` security, proton-vpn, awk lanes |
| 7 | Add browser policy and native messaging | Add Chrome/Firefox policy, KeePassXC native messaging, Clear New Tab support, camera/mic allowlist policy, Firefox cask choice, and browser helper extraction. | `nix-darwin/modules/browsers.nix`, `nix-darwin/bin/clear-new-tab-sync`, `nix-darwin/config/browser/**`, `nix-darwin/libexec/clear-new-tab/**` | `9104e7c`, `546c273`, `867e960`, `bd8dc7d`, `147551a`, browser hardening work through 2026-06-20 | `nix-unit-test --report` browser-config and clear-new-tab lanes |
| 8 | Add Brave YouTube app workflow | Add the Brave YouTube app, update strategy, low-battery guardrails, video-session power controls, CDP command, and all extracted assets/helpers as first-class files. | `nix-darwin/modules/brave-youtube.nix`, `nix-darwin/modules/video-session.nix`, `nix-darwin/bin/brave-youtube-*`, `nix-darwin/config/brave-youtube/**`, `nix-darwin/config/power/**`, `nix-darwin/libexec/brave-youtube/**` | `c05514d`, `3bad7f8`, `e980651`, `5c047f5`, `e688098`, `1d2ba5c`, `3f3053e`, `deff0ef`, `b7ddb75`, `b1cd0d5`, `37a12a1`, `b5d8f58`, `113d22c`, `f82355b` | `nix-unit-test --report` brave-youtube and applescript lanes |
| 9 | Add backup and sync workflows | Add encrypted rclone setup, `.codex` sync, shared-images sync, cloud-capture, Arq config, and Arq wildcard helper. | `nix-darwin/modules/arq.nix`, `nix-darwin/bin/*rclone*`, `nix-darwin/bin/cloud-capture-sync`, `nix-darwin/bin/arq-*`, `nix-darwin/config/arq/**` | `7915176`, `b7a2911`, `ee82ff1`, `c0a9d15`, `759012e`, `a1b572c`, `cb2eab3`, `96d877f` | `nix-unit-test --report` arq, cloud-capture, ruby lanes |
| 10 | Add shared-images workflow commands | Add shared-images DB audit, artist links, promotion, rehashing, workflow layout migration, gallery command, and supporting libraries. Use the final unified inbox/workflow split from the first appearance. | `nix-darwin/bin/shared-images-*`, `nix-darwin/bin/cnt-wallpaper-gallery`, `nix-darwin/libexec/shared-images/**` | `e10cce0`, `30d3c7c`, `edc9f82`, `91e86bb`, `712f33b` | `nix-unit-test --report` shared-images Python lanes |
| 11 | Add virtual machine and quarantine lab support | Add Vagrant/VMware/Tart-era VM management, quarantine lab, guest scripts, fixture resources, admin sudoers, and wrappers. Source files should appear in final config/libexec paths. | `nix-darwin/modules/virtual-machines.nix`, `nix-darwin/config/virtual-machines/**`, `nix-darwin/libexec/virtual-machines/**` | `00e0edc`, `5a9a85c`, `261c0bc`, `7b684c3`, `aaaee46`, `55419d3`, `5895d40`, `e282951` | `nix-unit-test --report` virtual-machines and ruby lanes |
| 12 | Add devcontainer templates | Add reusable devcontainer templates as a coherent artifact rather than scattering them through app/tooling history. | `nix-darwin/devcontainers/**` | Work currently embedded in existing repo state; map to final tree paths rather than preserving chronological origin. | File presence plus optional future template smoke checks |
| 13 | Add structured test runner and lanes | Add `nix-test`, `nix-unit-test`, `nix-int-test`, `tools/test/run`, pytest, bats, zunit, minitest, nix-unit, test layout docs, and initial test coverage across languages. | `nix-darwin/bin/nix-*test`, `nix-darwin/tools/test/run`, `nix-darwin/tools/test/ruby/**`, `nix-darwin/libexec/nix-unit-tests/**`, `nix-darwin/pytest.ini`, `nix-darwin/src/test/README.md`, `nix-darwin/src/test/{unit,int}/**` excluding report-specific additions if split later | `e8aa4fa`, `d218046`, `66b904b`, `e74288e`, `2dd2e08`, `d73acce`, `7cab299`, `b451d77`, `c30e494`, `cb2eab3`, `78739f2`, `c576551`, `e1ef6cb` | `nix-unit-test --report` |
| 14 | Add coverage collection and report artifacts | Add Python/Ruby coverage, Bats report generation, kcov Bash coverage, Nix source-target coverage, awk/applescript statement coverage, and coverage floor enforcement. | `nix-darwin/tools/test/run`, `nix-darwin/tools/test/kcov-bash-wrapper`, `nix-darwin/tools/test/ruby/**`, `nix-darwin/libexec/test-report/statement*`, coverage-related tests | `4256067`, `562bf15`, `e182821`, `c913c64`, `d9652af`, `c85666c`, `b070234`, `3aa89d1`, `6d0ac21`, `e1ef6cb` | `nix-unit-test --report`; inspect console coverage floor summaries |
| 15 | Add tree-based HTML test reports | Add normalized report model, parsers, source/test result trees, drilldown pages, and integration coverage. This should absorb the separate `backup/html-test-report-tree-before-cleanup` branch into the main narrative. | `nix-darwin/tools/test/report/**`, `nix-darwin/src/test/unit/python/test_report_*.py`, `nix-darwin/src/test/unit/python/statement_coverage_renderer.unit.test.py` | `7cab009`, `16b14aa`, `58bf47f`, plus branch commits `006f95d`, `68b8399`, `b04741e`, `161736f`, `11e7d76`, `1cfc17f`, `8f7f11b`, `920664b`, `bd85916`, `250c3e5` as applicable | `nix-unit-test --report`; open `.test-reports/unit/latest/index.html` if visual QA is needed |
| 16 | Add Codex state and tooling self-heal | Add `<private-user-docs>` link migration, Codex tooling desired state, self-heal checker, and source-checkout guardrails. Keep project standards in the repo with symlinks from `<private-user-docs>`; keep private local annotations only in `<private-user-docs>`. | `nix-darwin/modules/codex-state.nix`, `nix-darwin/bin/codex-tooling-self-heal`, `nix-darwin/libexec/codex-tooling/**`, `nix-darwin/config/codex-tooling/**`, Codex-state tests | `ee82ff1`, Codex docs migration work, `40b4b81` | `codex-tooling-self-heal`; `nix-unit-test --report` |
| 17 | Add bootstrap and maintenance tools | Add fresh-Mac bootstrap tooling and the remaining rare user tools. Keep bootstrap extensionless and Zsh-first. | `nix-darwin/tools/bootstrap/**`, `nix-darwin/scripts/test-all.sh`, any remaining `nix-darwin/tools/<domain>/**` | bootstrap migration/refactor work through 2026-06-23; `ff72169`; `3cf1ac6` only if ignored artifact policy remains relevant | `nix-unit-test --report`; targeted zunit bootstrap lane |

## Old Commit Handling Notes

- The 2024 and early 2026 Nix-learning commits should not survive as separate
  commits unless they still explain a current design decision. Most should be
  absorbed into target commit 1 or dropped as false starts.
- Typo-only and correction commits should be folded into the relevant target:
  `Simplift`, `Intiate`, and similar subjects should not survive.
- The one-language-per-file work should mostly disappear as a separate
  historical event. The rewritten history should introduce extracted files at
  the same time as their consuming modules or commands.
- The standalone enforcement commit can still be represented indirectly in the
  test-runner/coverage commits because the policy is enforced by tests.
- The report-tree backup branch should be incorporated into target commit 14
  if its changes are already present in current `main`.

## Verification Plan After Reconstruction

Run these before replacing `main`:

```sh
git diff --check
nix-darwin/bin/codex-tooling-self-heal
nix-darwin/bin/nix-unit-test --report
nix flake check nix-darwin --no-build
```

Attempt full validation when the Nix cache/network situation is calm:

```sh
nix flake check nix-darwin
```

If the full flake check fails only on substitute download/network errors, keep
the failure text in the handoff notes rather than treating it as a rewrite
blocker.

## Decisions

- Keep devcontainer templates as a standalone commit.
- Include `.gitignore` in the initial flake skeleton. The actual repository was
  not version-controlled at first because Git was originally introduced as an
  ad hoc investigation into how Nix uses Git state, but the rewritten history
  should model the normal project shape.
- Do not add a license in the rewritten initial commit. Keep the repository
  private until the security implications of public system-configuration
  history are better understood.
- Keep public-facing repo-specific standards in `nix-darwin/docs/specs/` and
  expose them under `<private-user-docs>/nix-darwin/specs/` with symlinks. Keep
  private local annotations only under `<private-user-docs>`.
- Keep `backup/html-test-report-tree-before-cleanup` until the rewritten `main`
  is verified and pushed, then delete it. Keep the main backup branch longer as
  the durable rollback point.
