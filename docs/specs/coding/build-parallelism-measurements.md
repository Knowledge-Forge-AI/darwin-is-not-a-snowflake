# Build Parallelism Measurements

Status: BPAR9 operational measurement, 2026-07-10

## Conclusion

Keep the current defaults:

- `NIX_DARWIN_TEST_JOBS=8`
- `NIX_DARWIN_BUILD_JOBS=4`

Test jobs `8` was fastest in every measured scope and did not produce failures,
memory pressure, or macOS thermal warnings. Build jobs `6` did not improve the
warm build or flake-check measurements. The build results were cache-limited,
so they do not justify changing the build default in either direction.

No immediate BPAR follow-up is justified. A future build-jobs trial should wait
for a naturally uncached system closure and compare jobs `4` and `6` while
observing memory and thermal behavior. The Nix store should not be invalidated
solely to manufacture that workload.

## Method

Measurements ran on the managed Apple M5 MacBook Pro with 64 GB RAM while
connected to AC power. Commands ran one at a time. Wall time was measured around
each command with zsh's `EPOCHREALTIME`; `nix-update --timing` also supplied its
whole-second phase timings.

The test matrix explicitly set `NIX_DARWIN_BUILD_JOBS=4` because the
non-interactive measurement shell did not source the managed interactive-shell
defaults. This kept Nix build concurrency fixed while test jobs varied. Each
requested point was measured once in ascending job order. Caches were not
cleared, so later runs had warmer test and Nix state. A final warm-cache
`all --jobs 1` confirmation was added to check the main ordering concern.

Per-command output was captured in temporary files outside the repository and
was not committed.

## Test Fan-Out

Each matrix point used `tools/test/run SCOPE --jobs N` with build jobs fixed at
`4`.

Wall-clock seconds:

| Scope | Jobs 1 | Jobs 2 | Jobs 4 | Jobs 8 | Jobs 8 vs. jobs 1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `unit` | 72.228 | 63.697 | 40.527 | 28.121 | 61.1% faster, 2.568x speedup |
| `int` | 63.343 | 49.560 | 49.764 | 35.208 | 44.4% faster, 1.799x speedup |
| `all` | 120.951 | 110.904 | 98.565 | 59.850 | 50.5% faster, 2.021x speedup |

The final warm-cache `all --jobs 1` confirmation took 125.872 seconds. Its
agreement with the original sequential result indicates that the jobs `8`
advantage was not merely an ascending-order cache effect.

All 13 test-fan-out commands, including the confirmation run, exited zero. No
test failures or flakes occurred.

## Build-Only Path

Each command used:

```sh
bin/nix-update --build-only --test-jobs 8 --build-jobs N --timing
```

The command ran the fast plain `nix-test` guardrail, skipped flake updates, and
performed a non-activating `darwin-rebuild build`.

| Build jobs | External wall | Pre-update tests | Darwin build | Internal total |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 75.612s | 68s | 8s | 76s |
| 2 | 61.318s | 61s | 0s | 61s |
| 4 | 59.765s | 59s | 1s | 60s |
| 6 | 63.495s | 63s | 0s | 63s |

The first command performed eight seconds of build work. The identical closure
was already present for later commands, whose build phases rounded to zero or
one second. Most wall-time variation therefore came from repeated test runs,
not from Nix build scheduling. This data confirms safe jobs plumbing but is not
a valid cold-build comparison.

## Flake-Check Path

The integration suite was measured with test jobs fixed at `8` while
`NIX_DARWIN_BUILD_JOBS` varied:

| Build jobs | Wall time |
| ---: | ---: |
| 1 | 30.340s |
| 2 | 30.937s |
| 4 | 31.338s |
| 6 | 31.222s |

The 0.998-second total spread is consistent with warm-cache noise. Jobs `6`
provided no measurable advantage.

## Host Observations

- System-wide memory was 79% free before measurement, reached a sampled low of
  78% after the build-only sweep, and returned to 79% afterward.
- macOS reported no thermal or performance warning before, during, or after the
  measured batches.
- Sampled load averages rose from 2.18 before measurement to a high of 5.08,
  then fell to 3.68 after the final batch.
- Command control and polling remained responsive. Interactive UI latency was
  not benchmarked.
- Fan speed and acoustic behavior were not instrumented, so no fan claim is
  made.
- All 21 measured commands exited zero. No failures or flakes occurred.

## Safety Checks

- `flake.lock` retained the same checksum before and after measurement.
- No flake update or system switch ran.
- No report-mode `nix-update` test path ran.
- The generated `result` symlink from `darwin-rebuild build` was removed after
  measurement because no such link existed before the sweep.
- Default job values and executable behavior were not changed.
