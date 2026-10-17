# 10. One scan at a time, and the lock is what makes the globals safe

Date: 2026-08-10

Status: accepted. A running scan can be stopped (`should_stop`); a stopped
scan saves nothing.

## Context

The web app scans by calling `run()` in a background thread. Two of them at once
would not be slow or contended — they would be *wrong*, and the reasons are
already written down elsewhere in the codebase as deliberate design:

- **`registry.CONNECTION_STATES`** is a module global, set once per run by
  `set_connection_states()` from `audit_config.json`. A second scan starting
  mid-way through the first would silently reconfigure which links the first
  one detects, halfway through its own row set.
- **`console.VERBOSE`** is a module global set from `--debug`. Both must be
  read as module attributes rather than imported by value (see their module
  docstrings); the same property that makes them work makes them shared.
- **`config.now()`** is pinned once per scan so that a multi-minute run measures
  every region against one instant. Two overlapping scans share one clock, and
  the later one dates its rows from whenever the earlier one started.
- **`aws_project_groups.json`** is read-modify-written by
  `apply_assigned_names` at the end of every scan. Two writers, last one
  wins, and what is lost is the user's names.

None of these is a bug to be fixed by adding locks inside the core. They are
consequences of a design where a scan is a *program run* — which is what it was.

## Decision

**One scan per process, enforced by an in-memory `threading.Lock`.** A `POST
/api/scans` while one is running is refused with `409`, not queued.

The lock is acquired on the **request** thread with `blocking=False`, before the
worker thread is started. Acquiring it inside the thread would let two requests
both return `202` and then serialise — which is a queue, and a queue is exactly
what is being refused.

It is released in a `finally` in the worker, and the worker catches
`BaseException` rather than `Exception` so that nothing can end that thread with
the lock still held. The `on_finish` callback runs *after* the release, so a
callback that raises cannot leave the app permanently convinced a scan is
running.

**The lock lives in memory and never on disk.** A lockfile would survive the
crash it was recording, and the app would come back up believing a scan that no
longer exists is still going. A container restart must clear it, and an in-memory
lock is the only kind that does so by construction.

**No queue.** There is one user, one account and one scan; Redis plus a worker
would be two moving parts serving a job that cannot have a backlog.

## Consequences

- The process-global state in `registry` and `console` is now **safe by
  construction rather than by luck**, and that is a property being relied on
  rather than an accident that has held so far. This decision is where it is
  written down; the alternative was a comment in `scan_runner.py` that nothing
  enforced.
- `backend/apitests/test_api_scan_control.py` pins the two failures that
  would matter: a second `POST` is refused, and **a failed scan releases the
  lock**. The second is the worse bug — a scan that dies holding it leaves the
  app unable to scan again until someone restarts the container, and the symptom
  ("Scan is greyed out") says nothing about the cause.
- A browser reload during a scan rejoins it, because the progress is server-side
  state read through `GET /api/scans/status` rather than anything the client
  holds. That falls out of the design rather than being built.
- Scanning two AWS accounts at once is not possible in one process. It is also
  not possible for other reasons — `member_key` carries no account id, so two
  accounts collide in one snapshot — and both would have to be solved together.
- A restart mid-scan loses the scan. Accepted: it is minutes, it is idempotent,
  and restarting it is a button. If scan history or a second account ever
  arrives, this is the piece to revisit, and the answer then is a real job queue.
