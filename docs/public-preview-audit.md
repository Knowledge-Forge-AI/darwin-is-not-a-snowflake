# v0.0.1 preview review scope

This release is a curated source snapshot, not a workstation security
certification. The review covers the published files and fresh public Git
history, licensing, obvious identity/path leakage, source pins, scanner wiring,
and the changed verification behavior. Private development history, operational
journals, personal images, browser profiles and credentials are excluded.

## Verification

The tarball verifier was exercised through the real source-verification function
and ordinary plan boundary. A native probe verified all 18 sources, including the
Home Manager tarball, in a previously qualified build snapshot without changing
its lock. The local Core focused source/deployment suite passed 216 tests.

A broad preliminary public-source Python run passed 2,469 tests and exposed 18
snapshot-fixture/environment failures. After correcting renamed fixture values,
providing a Git index, minimizing the historical classification fixture and using
the native temporary root for the relevant fake-browser tests, all 307 tests in
the affected files and source-verification boundary passed. The temporary test
root was removed. This is not a claim that the complete workstation was activated.

Malskanner 0.1.3 and the license-aligned frontend wrapper were built independently
with fixed npm dependency hashes. Harmless and suspicious local Malskanner
fixtures produced the expected different results. The upstream CLI currently
prints 0.1.1 for `--version`; installed package metadata is 0.1.3.

## Scanner evidence and limits

Betterleaks is run against files and public Git history with redaction, no
baseline and no credential validation. Private-key pattern matches in redaction
tests are intentionally retained: two have explicitly invalid non-base64 fixture
bodies, and another contains header markers without a key body. They exercise
rejection/redaction and are not usable credentials. No file-wide secret allowlist
is added to hide them.

Semgrep OSS runs with a local, revision-pinned upstream security rule selection,
metrics and update checks disabled. Every finding is reviewed against the
surrounding code and callers. Subprocess execution is an explicit capability of
operator tools; a sink match alone does not establish injection. SQL identifiers
come from fixed tables/columns and values use bound parameters. Git SHA-1 calls
implement Git object identity rather than password storage. The shell `eval`
sites consume generated initialization code from declared tools or a constrained
style emitter. Nix semantics are reviewed separately; the selected scanner does
not provide Nix language coverage. Five partial shell-parser cases are also
checked with their actual shell parsers. These syntax checks do not replace a
security review. Tests and report-generation sources are included using an empty
project `.semgrepignore`; Ruby rules are included in the pinned rule selection.
The report parser consumes locally generated coverage artifacts and is not
qualified for attacker-supplied XML or report trees.

Malskanner runs on local paths with credentials absent and without AI mode. Its
supported text-file selection and skipped symlinks limit coverage; an OK score
is not proof of absence of malicious content.

## Retained trust and availability limits

- SDKMAN's existing first-use bootstrap downloads and executes its upstream
  HTTPS installer as the target user. It is not content-hash pinned. Review that
  trust boundary before activation; this release does not execute the bootstrap.
- Homebrew formulas and casks have their own delivery/update trust. Semgrep is
  declared through Homebrew and is not an immutable Nix version pin.
- npm artifacts are version/integrity pinned, but registry signatures are not
  independently verified. Optional AI/MCP dependencies remain in Malskanner's
  upstream closure; no account, service or MCP registration is enabled here.
- Historical delivery kits are source fixtures, not current qualification or
  deployment authority. Browser-role cutover remains held by the source gates.
- Whole-composition availability depends on adapters not all public yet. No VM,
  host profile, firewall or personal browser configuration is changed by publishing
  this source preview.

Report suspected vulnerabilities privately as described in [SECURITY.md](../SECURITY.md).

## Public-template privacy corrections

Gateway hints are now a typed, site-local `dinas.hostSecurity.trustedGatewayMacs`
option with an empty default. Stealth mode starts enabled; an empty hint list
keeps it enabled. MAC matching is a spoofable convenience signal, not network
authentication. No operator gateway identifier is included in the public history.
The recovery helper requires an explicit backup root and plan/folder identifiers
through its existing flags or environment overrides before it creates state.

The public composition's nested parent-relative configuration input has no
standalone NAR hash. Direct ordinary source verification of that unmaterialized
graph currently fails closed; the successful ordinary-preview evidence covers
an explicitly materialized operator snapshot. This preview does not claim that
the unmaterialized public composition is ready for ordinary deployment.

The archive URL guard rejects common credential query names, not every possible
encoding of a credential. Credentials must never appear in source URLs. Existing
promotion URL handling, loopback diagnostic response trust, lexical path checks
and historical Git-blob SHA-1 checks retain documented review limits; they are
not new host-activation authority or a claim of comprehensive hardening.
