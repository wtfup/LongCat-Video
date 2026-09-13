# W5 Publishers — evidence receipts

Produced by fresh executions on the build machine (macOS, Python 3.11.14,
pytest 9.1.1). Re-run instructions per artifact; all commands run from the
package directory `wtf/publishers/`.

| Artifact | What it proves | Reproduce |
|---|---|---|
| `test_run.log` | Full suite green: 135 passed. The socket tripwire is autouse — every test asserts zero network attempts (except the marked positive control). | `python -m pytest -v` |
| `gate_matrix_default.json` | Shipped state is dark: 0/4 channels unlocked (no env key, no approval files). | `env -u PUBLISH_LIVE python cli.py gate-report --json` |
| `gate_matrix_single_unlock.json` | With `PUBLISH_LIVE=instagram` + one valid (temporary, /tmp) approval file, exactly 1/4 channels unlocks; the other three stay blocked. | temp approval + `PUBLISH_LIVE=instagram python cli.py gate-report --json --approvals-dir /tmp/w5-gate-demo/approvals` |
| `dry_run_receipts.json` | All four adapters render dry-run receipts with `dry_run=true`, `transport=dry-run`, `steps_executed=0`. | `python cli.py dry-run --channel <c> ...` per channel |
| `dry_run_blocked_channel.json` | The instagram env unlock does NOT unlock youtube (`env_unlock_mismatch+approval_missing`). | `PUBLISH_LIVE=instagram python cli.py dry-run --channel youtube ...` |
| `zero_network_report.json` | In-process socket guard installed before import: **0 package socket attempts** across dark publishes, gate-closed live refusals, and gate-open-without-credentials; the positive control proves the guard is armed; unlocked-without-creds fails with a names-only `CredentialMissing`. | scripted run (socket guard + adapter matrix; see this file's git era counterpart in the task thread) |
| `source_and_secret_scan.json` | AST scan: zero network-module imports at any level outside `executors.py`, where they are function-local only. Secret scan: 0 matches; no shipped `<channel>.json` unlock files. | scripted run (AST + regex scan over the package) |
| `checksums.sha256` | sha256 of every file at freeze time (verify with `shasum -a 256 -c` after adjusting paths). | `shasum -a 256 <files>` |

Notes:

- The temporary approval used for the single-unlock matrix lives in `/tmp`
  and is deliberately NOT part of the package; `approvals/` in the repo ships
  only `README.md` + `example.approval.json` (which can never unlock).
- Gate timestamps are ISO-8601 with UTC offsets (validator requirement).
- The tripwire guards `socket.socket`, `socket.create_connection`,
  `socket.getaddrinfo`, and `ssl.wrap_socket`; wall-clock sleeps are also
  no-ops in tests so a forgotten sleeper can never stall a run.
