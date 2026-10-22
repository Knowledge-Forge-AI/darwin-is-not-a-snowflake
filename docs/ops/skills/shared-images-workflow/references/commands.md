# Shared Images Command Notes

Commands use canonical checkout paths under
`$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin`.
`$HOME/.flakes` has been retired and must not be recreated as active
compatibility state. The managed command namespace root is `~/.local/nbin`,
an ordinary directory.

Audit before completion:

```sh
$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/shared-images-db-audit --check-hashes
```

Stronger audit:

```sh
$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/shared-images-db-audit --check-hashes --check-symlinks
```

Check or migrate intake/workflow layout:

```sh
$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/shared-images-workflow-layout check
$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/shared-images-workflow-layout migrate --dry-run
$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/shared-images-workflow-layout migrate
```

Promote original:

```sh
$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/shared-images-promote-original --dry-run <original-path>
$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/shared-images-promote-original <original-path>
```

Refresh artist symlinks:

```sh
$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/shared-images-refresh-artist-links --dry-run --artist <artist>
$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/shared-images-refresh-artist-links --artist <artist>
```

Sync:

```sh
$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/shared-images-rclone-sync dry-run-push
$HOME/.local/nix-darwin/darwin-is-not-a-snowflake/bin/shared-images-rclone-sync push
```

Use dry-run first for file-moving and sync operations.
