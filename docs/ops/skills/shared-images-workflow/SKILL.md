---
name: shared-images-workflow
description: Maintain an explicitly configured local image archive. Use when triaging images, moving wallpapers or portraits, editing shared-images SQLite records, refreshing by-artist symlinks, promoting originals, rehashing edited files, loading Clear New Tab backgrounds, auditing the shared-images DB, or syncing the encrypted shared-images remote.
---

# Shared Images Workflow

## Overview

Use this skill to keep the shared-images archive consistent across files, SQLite rows, derived symlinks, browser imports, and encrypted sync.

## Required Context

Read the tool help and the archive owner's policy before modifying files or database
rows. Configure archive and database locations locally. This source preview includes
no personal archive, collection inventory, remote identity or private continuation
notes. Directory categories and database schemas are tool interfaces, not evidence
of an operator's collection contents.

The commands below are repository-owned helpers. Use explicit paths and dry runs
for an archive you own; do not infer permission to operate on another archive.

## Operating Rules

1. Treat SQLite as the source of truth for canonical image metadata.
2. Treat `by-artist` directories as derived symlink views, not authoritative data.
3. Use `unknown` for unclear artists. Do not guess attribution.
4. Use dry-run modes before destructive file moves or symlink refreshes.
5. Use transactions for DB writes.
6. Use `sqlite3 -readonly` for inspection.
7. Rehash edited files immediately.
8. Run an audit before claiming completion.
9. Put newly found images in the unified raw inbox first, then classify them into portrait or wallpaper workflow lanes by visual/contextual judgment.
10. Treat low-res determination as a post-classification workflow step under `<image-type>/workflow/low-res/`.

## Core Commands

```sh
$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/shared-images-db-audit --check-hashes
$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/shared-images-db-audit --check-hashes --check-symlinks
$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/shared-images-rehash <prod-path>
$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/shared-images-workflow-layout check
$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/shared-images-workflow-layout migrate --dry-run
$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/shared-images-promote-original --dry-run <original-path>
$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/shared-images-refresh-artist-links --dry-run --artist <artist>
$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/shared-images-rclone-sync dry-run-push
```

## Visual Work

When classifying wallpaper versus portrait, SFW versus NSFW, or transparency quality, use visual inspection. Conservative image-shape heuristics can help, but user judgment wins.
