# 9. The library raises, the command line exits

Date: 2026-08-10

## Context

Sixteen places in the package ended the process directly: `raise SystemExit(...)`
in `settings.py` (a malformed `audit_config.json`), `snapshot.py` (a missing,
stale or corrupt scan), `registry.py` (a `connection_types` key matching no
known id), and `sys.exit(1)` in `run.py` (credentials that do not work).

Every one of those messages is well written and says what to do. That was the
point: a silently ignored setting is the
failure mode to fear, so configuration errors are loud and immediate, before any
AWS call. None of that is in question.

What is in question is *who decides the process should stop*. While the only
caller was a command line, exiting and reporting were the same act, so putting
them in the same statement cost nothing. With a second caller they are different
acts, and `SystemExit` is actively hostile to the second one:

**`SystemExit` inherits from `BaseException`, not `Exception`.** A caller that
wraps work in `except Exception` — which is what a request handler does, and
what a worker thread running a multi-minute scan does — does not catch it. The
error skips the handler written to deal with it, and a scan thread dies with the
lock still held and nothing reported anywhere the user can see. The failure is
worse than an unhandled error, because it looks like nothing happened.

## Decision

`aws_resource_audit/errors.py` defines one exception:

```python
class AuditError(Exception):
    """Something the user must fix before the run can continue."""
```

The library raises it. `aws_cleanup.py` and `aws_regenerate_report.py`
catch it and re-raise `SystemExit(str(e))`, so the CLI's message, its stderr,
and its exit code are exactly what they were.

It subclasses `Exception` deliberately and that is the whole content of the
decision. Making it a `SystemExit` subclass would have let every existing test
pass untouched and preserved precisely the property being fixed.

`errors.py` is base tier: it imports nothing, and base (`settings`, `registry`),
model (`snapshot`) and app (`run`) all raise it. A module every tier depends on
cannot live in one of them.

What does **not** belong in `AuditError` is a bug. An `AttributeError` or a
`KeyError` should stay as loud and as detailed as it is, because nobody can act
on it except whoever is reading the traceback.

## Consequences

- The CLI is unchanged, and this is asserted rather than assumed:
  `tests/test_entrypoint.py` drives both entry points as subprocesses with every
  credential source removed, and checks the exit code, the absence of a
  traceback, and the exact sentences ("Run aws_cleanup.py first", "Could not
  authenticate to AWS").
- `tests/test_entrypoint.py::LibraryRaisesCliExitsTests` pins the property in
  both directions: `AuditError` is an `Exception`, is *not* a `SystemExit`, and
  no module in the package contains a `raise SystemExit`, `sys.exit()` or
  `os._exit()` anywhere. That last one is read structurally with `ast`, because
  the failure this prevents is one call site added back in good faith — and it
  would look completely reasonable sitting next to the message it prints.
- A backend can map `AuditError` to a `400` with the library's own sentence as
  the body. The messages were already written to be the whole of what a user
  sees, so they need no rewording for HTTP.
- `run()` no longer imports `sys`.
- A failed AWS call is a different case: `safe_call` returns a structured
  failure (`CallFailed`) rather than raising.
