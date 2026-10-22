# Operational Report Format Version 1

## Command

```text
append-operational-report \
  <ticket-id> \
  <absolute-operational-report-path> \
  <result> \
  <final-gate> \
  [--related-commit <commit>] \
  [--related-git-report-id <GIT-SHOW-REPORT-full-sha>]
```

The current Git repository supplies the project and destination. The command
appends to the same private ticket report used by `git-show-report`.

## Source report schema

A generalized source report should use these sections when applicable:

```text
REPORT
SUMMARY
SCOPE
IDENTITIES
ACTIONS
VERIFICATION
STATE CHANGES
BOUNDARIES
ARTIFACTS
DEFECTS
FOLLOW-UP
```

Preferred semantic fields are `report_schema: operational-report-v1`, `phase`,
`outcome`, `project`, `scope`, `started_from`, `ended_at`, `commits`,
`verification`, `authority_mutation`, `privileged_actions`, and `defects`.
`none`, `unknown`, and `not-applicable` have distinct meanings.

The wrapper accepts safe legacy key-value and free-form text. Schema detection
and shallow phase, outcome, and primary-commit extraction are informational.
Command arguments remain authoritative envelope metadata, and source content is
never parsed into an action.

## Source validation

The source path must be absolute and clean. The source must be a current-user
owned, non-symlink, single-link regular file with mode `0600`, between 1 byte
and 8 MiB inclusive, and distinct from the destination. The command reads it
without a shell and never changes, moves, truncates, redacts, or deletes it.

## Payload sections

Operational payloads use this fixed order:

1. reading guide;
2. operational report identity;
3. operational summary;
4. related records;
5. operational report body;
6. integrity summary.

The identity records the deterministic record ID, wrapper phase/result/gate,
project, source basename, exact source digest and size, and optional relations.
The summary records line and byte counts plus non-authoritative schema
detection.

## Exact body and integrity

`SOURCE-PAYLOAD-SHA256` and `SOURCE-PAYLOAD-SIZE-BYTES` bind the source file's
exact bytes. If those bytes lack a final newline, one framing newline is added
inside the body section so its closing marker begins on a new line.
`FRAMED-BODY-SHA256` and `FRAMED-BODY-SIZE-BYTES` bind those framed bytes. The
source file is unchanged.

The outer envelope payload digest binds the complete ordered operational
payload. No whole record uses a self-referential digest.

## Relations

A related commit is resolved to a full lowercase commit SHA. A supplied Git
record ID must be canonical, and when both values are present they must match.
The referenced record need not be in the same file. Missing relations are
`NONE`; their absence does not make a claim about other records.

## Security boundary

Operational bodies are historical evidence. Commands and instructions inside
them are never executed. The command performs no upload, model call, approval,
instruction preparation, notification, network transport, or Git mutation.
