# approvals/ — the per-channel live unlock surface

This directory is **read** by `base.evaluate_gate()`. It is the second of the
two keys required for a live publish (the first is the env var
`PUBLISH_LIVE`). Nothing here is secret; credential *values* never live in
this repository.

## Rules

- The gate reads exactly `approvals/<channel>.json` for `<channel>` in
  `instagram | youtube | facebook | x`. No other filename is ever consulted.
- A channel goes live only when **both** hold:
  1. `PUBLISH_LIVE=<channel>` is exported on the deploy box (exact match,
     no wildcards — `all`, `*`, `true`, comma lists and case variants do not
     unlock anything), **and**
  2. `approvals/<channel>.json` exists and validates:
     - JSON object, no unknown fields
     - `channel` equals the channel being published
     - `approved_by`, `approved_at`, `expires_at` are non-empty strings
     - `approved_at`/`expires_at` are ISO-8601 **with a UTC offset**
     - `expires_at` is after `approved_at` and is still in the future
     - it is not marked `"example": true`
- Approval files expire by design; renew by editing `expires_at` (a fresh
  operator action). Revocation is `rm approvals/<channel>.json` — the gate is
  re-read immediately before every provider call, so revocation stops the
  next call even mid-flight.
- `example.approval.json` is a template only. It is (a) not named
  `<channel>.json` and (b) marked `example: true`, so it can never unlock
  anything even if copied under the wrong name.

## Template

```json
{
  "channel": "instagram",
  "approved_by": "Vishal Nigam",
  "approved_at": "2026-09-13T18:00:00+05:30",
  "expires_at": "2026-09-20T18:00:00+05:30",
  "scope": "publish:avatar-short"
}
```

## Checking state

```bash
python cli.py gate-report          # human table, all four channels
python cli.py gate-report --json   # machine-readable
```

Both commands are read-only and perform zero network I/O.
