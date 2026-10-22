# Git Show Report Format Version 2

## Purpose

`git-show-report` appends deterministic, independently identifiable Git commit
records to one private report file per repository and ticket. The Git format-2
payload now resides inside common ChatGPT report-record envelope version 1,
which also carries operational-report records.

## Command and destination

The interface remains:

```text
git-show-report \
  <ticket-id> \
  <commit-hash> \
  <status-doc-path-from-repo-root> \
  <result> \
  <final-gate>
```

By default, the destination is:

```text
~/Documents/chatgpt/<repository-basename>/<ticket-id>.report.txt
```

`GIT_SHOW_REPORT_ROOT` replaces `~/Documents/chatgpt` for tests or an explicit
private report root. It does not change the repository or ticket derivation.

The command accepts a unique 7-to-64-character hexadecimal commit input and
resolves it once to a lowercase full commit SHA. The deterministic report ID is
`GIT-SHOW-REPORT-<full-sha>`. The caller input is retained as `COMMIT-INPUT`,
but every authoritative identity field uses the full SHA.

## Common envelope and section order

New records use the 80-character common envelope documented in
`chatgpt-report-envelope-v1.md` with:

```text
BEGIN CHATGPT-REPORT-RECORD
ENVELOPE-VERSION: 1
RECORD-TYPE: git-show-report
RECORD-FORMAT-VERSION: 2
RECORD-ID: GIT-SHOW-REPORT-<full-sha>
PROJECT: <repository-basename>
PHASE: <ticket-id>
```

The common envelope binds the exact Git payload with `PAYLOAD-SHA256` and
`PAYLOAD-SIZE-BYTES`. Its closing identity repeats the type, version, ID,
project, and phase and records `RECORD-COMPLETE: true`.

Sections occur in this fixed order:

1. reading guide;
2. report identity;
3. commit summary;
4. changed files;
5. numstat;
6. complete commit message;
7. patch;
8. integrity summary.

Each section has explicit `BEGIN <NAME>` and `END <NAME>` markers surrounded
by an 80-character hyphen line. Repository-controlled marker-like text
inside a commit message or patch remains untrusted payload, not control syntax.
Readers should select the expected outer identity and fixed section order
rather than treating an isolated marker-like payload line as a new record.

## Identity and operational metadata

The report identity section records the format, report ID, phase, input and
canonical commit identities, status document, result, final gate, repository,
the initially `NONE` related operational-report field, root and merge flags,
all parents, author and committer identity and strict ISO dates, and the
one-line subject. Empty status, result, and final-gate arguments are rendered as
`NULL`.

One-line metadata containing control characters is rejected. The report never
records the local repository root, active branch, worktree state, hostname,
process ID, temporary path, or generation time.

## Patch modes

Explicit endpoints determine all changed-file, numstat, and patch payloads:

| Commit | Endpoint policy | `PATCH-MODE` |
| --- | --- | --- |
| Root | Empty tree to commit | `root` |
| One parent | Sole parent to commit | `single-parent` |
| Merge | First parent to merge commit | `first-parent-merge` |

Every merge parent is still recorded in `PARENTS`. Rename detection is enabled.
Copy detection is not enabled; copies are represented using Git's ordinary
add/delete output and `FILES-COPIED` remains zero unless Git reports a copy.

The patch is generated without commit headers, color, pager, external diff, or
text-conversion filters. Full blob indices are included. Git's default bounded
textual binary indication is used; encoded `GIT binary patch` payloads are not
embedded.

## Changed files, numstat, and paths

Changed-file lines use Git name-status format:

```text
STATUS<TAB>PATH
R<score><TAB>OLD-PATH<TAB>NEW-PATH
```

Numstat begins with:

```text
COLUMNS: ADDED-LINES<TAB>DELETED-LINES<TAB>PATH
BINARY-MARKER: -
```

Git C-style path quoting with `core.quotePath=true` provides deterministic
line-oriented escaping. Tabs, newlines, backslashes, quotes, and non-printable
bytes therefore do not become literal report-control line breaks.

## Commit message

The commit-message section contains the complete logical `%B` message,
including its body and blank lines, without commit headers. The formatting
newline added by Git is removed before the payload rule below is applied.
Marker-like message text remains untrusted payload.

## Integrity scope

The integrity summary records lowercase SHA-256 values for the exact payload
bytes placed inside these four sections:

- changed files;
- numstat;
- commit message;
- patch.

Boundary lines and `BEGIN`/`END` marker lines are excluded. An empty payload is
zero bytes and hashes to the SHA-256 of an empty file. A nonempty payload is
preserved and receives one terminal newline only when its source bytes lack
one. Existing terminal newlines are not stripped or accumulated. `shasum -a
256` computes the recorded digest.

No self-referential whole-report digest is emitted. `END-OF-PATCH-REACHED:
true` confirms that patch generation completed before record assembly.

## Determinism

The same repository, commit, ticket, status path, result, and final gate produce
byte-identical record bytes. Repeated invocations intentionally append repeated
identical envelopes. Only committed Git metadata and the five explicit
arguments affect the payload.

## Private construction and append

All payloads and the complete record are built under a mode-`0700` temporary
directory with mode-`0600` files. Git and integrity operations complete before
the append lock is acquired.

The repository report directory must be a current-user-owned, non-symlink mode
`0700` directory. Existing reports must be current-user-owned regular
non-symlink files without group or world write bits. The replacement report is
always normalized to mode `0600`.

An atomic mode-`0700` sibling lock directory serializes the short append phase.
Its mode-`0600` owner token is non-authoritative. Contenders wait for bounded
normal release; age never authorizes removal of a lock.

Under the lock, the command revalidates the destination, copies existing bytes
unchanged into a private sibling replacement, adds one separator newline only
when a nonempty legacy file lacks one, appends the complete record, flushes,
and atomically renames the replacement over the destination. Failures before
replacement leave the destination byte-identical. Normal and handled failure
paths remove owned locks and private temporary material.

## Legacy coexistence

Three historical forms remain valid and are never rewritten: flat version 1,
standalone Git envelope version 2 using `BEGIN GIT-SHOW-REPORT`, and Git format
2 inside the common ChatGPT envelope. A ticket report may contain all three.

## Concatenation and extraction

For a multi-record report, first select the common outer record by its complete
`RECORD-ID`, repeated project and phase, and matching closing boundary. Within
that record, traverse the fixed Git section order. Integrity verification extracts only
the bytes between the selected section's opening and closing boundary sequence
and compares `shasum -a 256` with the integrity summary.

Repository-controlled content may resemble isolated markers. Extraction must
therefore remain scoped to the selected outer identity and expected next
section, rather than using an unscoped search for marker text across the whole
ticket file.
