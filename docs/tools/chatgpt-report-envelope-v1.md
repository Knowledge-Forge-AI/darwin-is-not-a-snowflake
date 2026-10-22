# ChatGPT Report Record Envelope Version 1

## Purpose

The common envelope makes independently generated Git and operational records
unambiguous when appended to one ticket file or concatenated into an omnibus
file. It is a local evidence format and grants no workflow authority.

## Grammar

Every new record starts and ends with an 80-character equals-sign boundary.
The opening control block is:

```text
================================================================================
BEGIN CHATGPT-REPORT-RECORD
ENVELOPE-FORMAT: chatgpt-report-record
ENVELOPE-VERSION: 1
RECORD-TYPE: <git-show-report|operational-report>
RECORD-FORMAT-VERSION: <integer>
RECORD-ID: <deterministic-id>
PROJECT: <repository-root-basename>
PHASE: <phase-id>
PAYLOAD-SHA256: <lowercase-sha256>
PAYLOAD-SIZE-BYTES: <integer>
================================================================================
```

The complete type-specific payload follows. The closing control block repeats
the type, format version, record ID, project, and phase, then records
`RECORD-COMPLETE: true`. One newline follows its final boundary.

The payload digest and size cover only the exact type-specific payload bytes
between the opening and closing outer boundaries. The outer record is not
self-referential.

## Record types and identities

- `git-show-report`, format 2: `GIT-SHOW-REPORT-<full-commit-sha>`.
- `operational-report`, format 1:
  `OPERATIONAL-REPORT-<exact-source-payload-sha256>`.

`PROJECT` is derived from the current Git repository root basename. Callers
cannot override it, and no local repository path is emitted. Duplicate complete
records are permitted and retain their individual positions.

## Reading an omnibus file

Readers identify a control marker only when it occurs in the fixed outer
boundary position. Repository-controlled commit messages, patches, and
operational bodies may contain marker-like text and remain untrusted payload.
Readers then dispatch on `RECORD-TYPE`, validate matching opening and closing
identity fields, verify the payload digest and size, and require the final
completion boundary. A missing final boundary is a truncated record even when
an earlier payload line resembles an end marker.

Records may be interleaved in any order and may come from different projects or
phases. Concatenation preserves record order. No prose outside the record
envelopes is required.

## Private append behavior

Both producers use one shared helper. It validates the ticket and destination,
constructs private temporary files, validates the complete envelope, acquires a
private sibling lock with bounded waiting, preserves existing bytes, adds one
separator newline only when required, and atomically replaces the destination.
Lock age never permits recovery. Normal and handled failure paths remove owned
locks and temporary files.

Repository report directories use mode `0700`; report and temporary files use
mode `0600`. Existing legacy bytes are never rewritten.

## Trust boundary

The helper does not execute payloads, evaluate shell text from report content,
access credentials or a network, infer approval, or create workflow authority.
Git access remains confined to the Git-specific producer.
