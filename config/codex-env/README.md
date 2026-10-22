# Codex Environment Allowlists

These TSV files define explicit environment-variable allowlists for generated
`.env` files and future Codex shell-environment snapshots.

The format is intentionally strict. Exact names are safer than wildcard
patterns, and validator names are small enough to audit.

## Columns

```text
name    validator    max_bytes    required    default    description
```

- `name`: exact environment variable name. Wildcards are rejected.
- `validator`: one of the validators below.
- `max_bytes`: maximum UTF-8 byte length for a captured or default value.
- `required`: `true` or `false`.
- `default`: optional default value checked against the validator.
- `description`: human-readable note for editors and reviews.

Values containing NUL, carriage returns, newlines, or other control characters
are rejected. The sentinel value `NULL` is rejected case-insensitively.

## Validators

- `raw_safe`: control-character and size checks only.
- `token`: letters, digits, `.`, `_`, and `-`.
- `token_list`: comma-separated `token` values.
- `path`: non-empty path-like value.
- `path_list`: colon-separated non-empty path entries.
- `uri`: absolute URI with a scheme.
- `uri_or_path`: either `uri` or `path`.
- `host`: host-like value.
- `port`: integer from 1 through 65535.
- `bool`: `true`, `false`, `yes`, `no`, `1`, or `0`.
- `integer`: signed integer.
- `command`: control-character and size checks for command names/options.

Run `env-allowlist-check` to validate the repo-managed allowlists.
