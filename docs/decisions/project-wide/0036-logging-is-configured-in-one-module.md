# 0036 — Logging is configured in one module, not in audit_config.json

Status: accepted
Date: 2026-09-17

## Context

The repo had no `logging` at all — not one `import logging`, in either front
end. What stood in for it was `console.py`, whose own docstring called itself a
deliberate stand-in: *"A thin wrapper over print() rather than `logging`, for
now… Moving to logging afterwards is a change in one file instead of fifty."*

The cost was that failures were invisible. `safe_call()` swallowed every AWS
API error at 54 call sites and returned the message as a string.
`collect/amplify.py` caught `ClientError` with a bare `pass`, silently losing
the authoritative evidence behind Amplify grouping. `api/groups.py` caught
`Exception`, returned `set()`, and thereby switched **off** the "is this key in
the current scan" validation without saying so. A crashed scan's traceback
existed only in an in-memory deque, so a restart lost it. And the agent kept no
record of what it sent the model, what came back, what the RAG search matched,
or which tools ran — the newest code in the repo, and the least observable.

## Decision

A new base-tier module, `aws_resource_audit/logsetup.py`. It holds
`_default_config(level)`, which returns one `logging.config.dictConfig` dict.
**That dict is the destination decision**, and today it defines a single stderr
handler. Adding a file is an edit to it, or a `logging.json` that replaces it
wholesale; no call site in either front end changes.

Four things follow from where it sits.

**The level is an environment variable, not a setting.** `AUDIT_LOG_LEVEL`
first, then the CLI's existing `--debug`, then INFO. No new flag: `--debug`
already means "tell me more" and a second knob for the same intent is a knob to
keep in sync. `aws_regenerate_report.py` takes the env var only — it has no
flags and `tests/test_entrypoint.py` enforces that.

**It is not a key in `audit_config.json`.** Three reasons, any one sufficient:
`load_settings()` raises `AuditError`, so a level stored there would be unread
exactly when the config is broken and logging has something to say; that file
is read from the *current directory* by design, so the level would change
depending on which scan you `cd` into, answering a per-process question
per-directory; and it would mean touching `_STR_KEYS`, `schemas.py`,
`api/config.py` and the React settings form for something meant to be an env
var. A `logging.json` is still honoured, but only at a path named explicitly —
`AUDIT_LOG_CONFIG`, or the backend's own data volume. Never by searching the
current directory: `dictConfig` instantiates arbitrary importable callables
through its `"()"` keys, so auto-discovery would be code execution from
wherever you happened to `cd`.

**The root logger stays at WARNING, always.** Raising it with everything else
is how `AUDIT_LOG_LEVEL=DEBUG` would switch on botocore's wire log, which
prints every signed AWS request including `Authorization` headers — in a tool
whose entire security posture is "this is a complete map of a real AWS account,
bind it to 127.0.0.1." The two trees this program owns (`aws_resource_audit`,
`app`) are named explicitly and are the only ones the level applies to; a list
of third-party loggers is pinned at WARNING beside them.

**`aws_resource_audit.console` gets no handler.** `say()`/`detail()`/`warn()`
now also emit records, at INFO/DEBUG/WARNING — but they have *already printed*
the line, and a handler would put every progress line on the terminal twice.
The records are created and dropped. They exist so that the file destination,
when it arrives, captures a scan's whole commentary with nothing else changing.

The mirror lives inside `console.py` rather than being installed through
`console.set_sink()`. The sink was the obvious place and is the wrong one: its
signature is `sink(message, stream, end, flush)`, and `say()` and `detail()`
reach it identically as `(msg, sys.stdout, "\n", False)`. A sink-based mirror
therefore logs every `--debug` diagnostic at INFO, which is the opposite of the
point. `console.py` knows which function was called; the seam does not.

## Consequences

`safe_call` logs at **DEBUG**, not WARNING. It runs inside per-resource loops
at 54 call sites, so one missing IAM permission across seventeen regions would
be thousands of records on a console-only destination. What the user needs is
already reported — `str(e)` goes back in `__error__` and reaches the notes
column through `brief_error()`. Only the traceback was lost, and DEBUG is where
it belongs. The bounded choke point for failures is `error_row()` in `rows.py`,
which every failed lookup passes through whichever collector built it: one
`logger.warning` there covers 38 call sites and cannot drift.

The agent's raw traffic goes to **one** logger, `app.agent.payload`, named once
in `backend/app/agent/__init__.py` rather than derived from `__name__`. Three
modules write to it and enabling it is a single question: show me what the
model actually saw. It is a child of `app.agent`, so `AUDIT_LOG_LEVEL=DEBUG`
turns it on with everything else, and a `logging.json` can pin it back to INFO
on its own line.

**It is off by default because it can hold real identifiers.** The payload log
shows tokens, as the model saw them (decision 0071), but the document-search
line logs hits before they are masked, so at DEBUG text from uploaded documents
appears in the log as written. Every call
site uses `%`-args rather than f-strings and the accumulated reply is behind an
`isEnabledFor` guard, so the payloads are not even *built* when DEBUG is off.

`disable_existing_loggers` is `False`, and that is load-bearing rather than
incidental. uvicorn installs its own `dictConfig` in `Config.__init__`, before
`Config.load()` imports `app.main`, so ours runs second; the default (`True`)
would switch off `uvicorn.error` and `uvicorn.access`, and the symptom — a
container that logs nothing about requests — looks like a container problem
rather than a logging one.

`backend/apitests/__init__.py` sets `AUDIT_LOG_LEVEL=CRITICAL` with
`setdefault`, because importing `app.main` configures logging for real and that
suite drives deliberate failures through it. `setdefault`, so
`AUDIT_LOG_LEVEL=DEBUG python3 -m unittest …` still works on a failing test.
Third-party WARNINGs (pypdf on a deliberately broken upload, say) still reach
the root handler, which is the honest behaviour and mild test noise.

`aws_resource_audit/__init__.py` puts a `NullHandler` on the package logger.
Without it `logging.lastResort` writes unformatted WARNINGs to stderr for any
program that embeds the package and never configured logging — the core test
suite included, since `tests/corpus.py` deliberately carries error rows.

## Not done

A file destination, rotation, JSON output, and request-id correlation. All of
them are edits to `_default_config`, or a `logging.json`; none needs a call-site
change. That was the property this decision was shaped to buy.
