# Scan resilience: credentials, clock errors, default VPCs, the scan log

## Problem

An all-regions scan from the web app ran about 85 minutes of wall-clock time
because the Mac slept three times during it. Three things went wrong:

- After each wake, the Docker VM's clock was about 16 minutes behind AWS for a
  few seconds. Requests signed in that window were refused ("Signature
  expired", `RequestExpired`, `AuthFailure`).
- The AssumeRole credentials last one hour. Every lookup after that failed
  (`ExpiredToken`), including all of us-east-1.
- The scan saved anyway. It replaced a good snapshot and the saved region list
  with a scan that was mostly errors.

Two more problems showed up in the same scan:

- One region's failed default-VPC lookup turned off default-VPC hiding in every
  region. Quiet regions then showed 7 default-network rows each and counted as
  active.
- The on-screen log keeps only the last 800 lines, and no full copy was saved.

## Requirements

- **R1** – With `assume_role` on, the role's credentials renew themselves
  before they expire by calling AssumeRole again. No new IAM action.
- **R2** – If AWS rejects the credentials partway through a scan, the scan stops
  with an `AuditError` that says what happened and what to do. Nothing is
  written: snapshot, outputs and `aws_active_regions.json` stay as they were.
  "Rejected" means:
  - an expired token;
  - a failed renewal;
  - a clock error that persists after R3's retry.
- **R3** – A clock error ("Signature expired", `RequestExpired`,
  `RequestTimeTooSkewed`) waits 15 seconds and is retried once with a new
  signature.
- **R4** – Whether to hide an untouched default VPC is decided region by
  region.
- **R5** – `results/aws_scan_log.txt` holds the full console output of the
  latest scan:
  - written line by line while the scan runs;
  - overwritten by the next scan;
  - kept when a scan fails or is stopped.

  The CLI lists the file with its other output files. The web app shows a
  "full log" link next to "show log".

## Not included

- Faster scans.
- Renewing the base profile's own temporary credentials (SSO, `aws login`).
  When those expire, R2 stops the scan cleanly.
- Keeping the host awake.
- The rule that keeps a region whose lookup failed on the saved region list.

## Existing mechanisms reused

- `safe_call` / `paged` (`collect/calls.py`): failed calls are returned, not
  raised.
- `collect_all`'s `_stop_if_asked` checkpoint, which runs before each step.
- `AuditError`: the web app shows it as a failed scan and the CLI exits with
  it.
- `credentials_help()` for the remedy text.
- `console.set_sink()`: the web app already chains onto it.
- The `/files` mount of `results/`.
- `GET /api/scans/status`.

## Options considered

The `architect` agent's review compared these:

- **R1.**
  - *Chosen:* botocore refreshable credentials, attached to a botocore session
    through a credential provider, which is public API.
  - *Rejected:* writing a `role_arn` / `source_profile` profile to a config
    file. It needs a written file and changes how decision 0027 delivers
    credentials.
- **R2 and R3.**
  - *Chosen:* `collect/auth_guard.py`, which watches botocore events on the
    session. 23 sites in `collect/` catch `ClientError` themselves and bypass
    `safe_call`, and an event hook sees them all.
  - *Rejected:*
    - raising from `safe_call`: misses those sites and reverses the "returned,
      not raised" contract;
    - checking error rows after the fact: misses those sites and stops late.
- **R5.**
  - *Chosen:* a tee sink installed by `run()`, chained onto whatever sink was
    already set.
  - *Rejected:*
    - several sinks in `console.py`: duplicates the existing seam;
    - a logging `FileHandler`: decision 0036 keeps logging configuration in
      one module and keeps console output and logging as separate channels.

## Design

### collect/

**`collect/auth_guard.py`** holds the `AuthGuard` class, built by
`install(session)`. It registers two handlers on the session's event emitter,
so every client the scan creates afterwards inherits them.

- **`needs-retry`.** This handler is general, so the service-specific retry
  handler runs first; that one never retries these codes.
  - A clock error, or `ExpiredToken*`, on the first such failure of a request:
    return 15, and botocore waits and re-signs. The flag is kept in
    `request_dict["context"]`.
  - `ExpiredToken*` gets one retry too because a clock that is behind keeps
    botocore's renewal window shut until the clock is corrected.
  - The same code on the retried attempt is recorded as a rejection.
- **`after-call-error`.** A `RoleRefreshFailed` raised while signing is
  recorded as a rejection.
- **`check(profile)`** raises `AuditError` if a rejection was recorded. The
  message names the region, the operation and AWS's message. For a clock error
  it explains the clock; otherwise it appends `credentials_help()`.

`AuthFailure`, `InvalidClientTokenId` and `UnrecognizedClientException` are
neither retried nor treated as rejections. AWS also returns them for a region
that is not enabled for the account. Retrying would add 15 seconds per call
there, and stopping would blame the wrong thing.

**`collect/role_credentials.py`** builds the refreshable credentials:

- `assumed_role_session(base_session, role_arn)` calls AssumeRole once through
  `safe_call`, so the "Could not assume role" wording survives.
- It wraps the result in `RefreshableCredentials` whose refresh function calls
  AssumeRole again. A failed refresh raises `RoleRefreshFailed`, a
  `BotoCoreError`.
- It returns `boto3.Session(botocore_session=...)`.

`collect/session.py` uses it.

**`collect/collect.py`:**

- `collect_all(..., auth_guard=None)`: `_stop_if_asked` also calls
  `auth_guard.check()`.
- `default_vpcs_unknown` (R4) replaces `default_vpcs_known`.

### run() and the app tier

- **`aws_resource_audit/scan_log.py`**: the `ScanLog` context manager. It opens
  the file with mode `"w"` and line buffering, and installs a tee sink over the
  previous one. On an exception it writes the message and re-raises. It
  restores the previous sink on exit. A file that cannot be opened logs a
  warning, and the scan goes on without it.
- **`run()`** is split in two:
  - `run()` loads settings, opens the `ScanLog` and calls `_scan()`.
  - `_scan()` installs the guard on the session and checks it after
    `get_regions`, inside `collect_all`, and right after `collect_all`. That is
    before `_write_debug_json`, `record_active_regions` and `write_snapshot`.
- **`settings.py`**: `OUTPUT_FILENAMES["scan_log"] = "aws_scan_log.txt"`.

### analyze/

- `hide_untouched_default_networks(..., unknown_regions=())` replaces
  `known=`.
- `apply_project_grouping` loses its ignored `default_vpc*` parameters.

### Front ends

- **CLI:** `cli_output.py` lists the log among the files shown after a scan.
- **Backend:** `ScanStatus.log_url` is `/files/aws_scan_log.txt` when the file
  exists, else `null`. The name comes from `OUTPUT_FILENAMES`, so TypeScript
  never hard-codes it.
- **Frontend:** `ScanBar` shows a "full log" link (new tab) when `log_url` is
  set.

## Changes made during implementation

- **Fail fast after a rejection.** Once a rejection is recorded, later calls are
  no longer retried. Without this, a step that makes one call per resource
  could wait 15 seconds per call before the scan stopped.
- **Renewal failures that botocore does not report as a `BotoCoreError`.** A
  renewal that returns credentials already expired by the local clock, or a
  malformed AssumeRole reply, raises `RoleRefreshFailed`. botocore would
  otherwise raise a bare `RuntimeError` or `KeyError`, and the scan would crash
  with a traceback.
- **`scan_log.py` is app tier.** It sits next to `run` and `render`, not in the
  base tier: only `run()` uses it.
- **`apply_project_grouping` lost its unused default-VPC parameters.** Grouping
  never used them.

## Errors and logging

- **Retry:** WARNING, with region, operation and code.
- **Rejection recorded:** WARNING.
- **`check()` raising:** the `AuditError` is handled as today. The web app
  logs a WARNING, and the CLI logs at DEBUG with the traceback.
- **Log file that cannot be opened or written:** WARNING, then the scan goes
  on.

## Security, privacy and IAM

- **IAM:** no new AWS action. Renewal uses `sts:AssumeRole` from the base
  identity, as the scan already does at its start.
- **The log file:** holds what the console already shows, including account
  ID, resource names and errors. That is the same sensitivity as the other
  files in `results/`. It is served by the existing local-only `/files` mount,
  and nothing sends it to the agent.

## Scale

- **Retries:** a retry costs 15 seconds and happens only on a clock error.
- **Renewal:** one AssumeRole call per renewal, so about one per hour.

## Tests (outline; the full plan is in the final report)

- **Guard:**
  - the handler retries once, then records the rejection;
  - `AuthFailure` is left alone;
  - a refresh failure is recorded;
  - `check()` raises with the right wording.
- **End to end with a real botocore client:** a `before-send` hook returns
  clock-error responses, which proves the hook order and the re-signing.
- **Credentials:** the role credentials renew through the refresh function.
- **`run()`:** with a rejected credential nothing is written. The log file
  gets the error text.
- **Seams:** the tee forwards to the previous sink and restores it.
- **Backend:** `log_url` in the status.
- **R4:** the per-region test.
