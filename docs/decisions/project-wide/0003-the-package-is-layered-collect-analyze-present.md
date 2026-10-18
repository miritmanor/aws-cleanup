# 3. The package is layered: collect → analyze → present

Date: 2026-08-09

## Status

Accepted.

## Context

The package was eighteen flat modules, and the row was one dict serving as both
the in-memory record of an AWS resource and the row of the HTML table. That
fusion was not merely untidy; it produced defects:

- Collectors called `fmt_dt()` while collecting, so a row reached the rest of
  the pipeline with its dates already text. Nothing downstream could subtract
  them, which is why `last_used_days` existed as a *separate stored column* —
  a value carried because another had been destroyed.
- `tags` was flattened for display and the real dict kept alongside as `_tags`:
  one fact stored twice, with the typed half wearing the underscore.
- `resolve_edges` built the `connections` prose and the graph records in the
  same loop. The code said outright that prose "must never be parsed back into
  structure" — and `analyze/group_audit.py` did exactly that, recovering its
  dangling and collision counts by searching the rendered column for
  `"DANGLING ->"`. Rewording one sentence silently emptied a section of the
  audit, and nothing failed.
- `test_graph.py` carried a class of tests whose only job was checking those two
  derivations had not drifted.
- The JSON writer serialized whatever keys survived the pipeline, so keeping an
  internal field out of a user's output depended on an earlier pass removing it
  at the right moment — and one field could not be removed at the right moment
  at all, so `build_graph_data` popped it while consuming it. Calling it twice
  returned an empty graph with no error.

Every one of those is the same shape: no boundary between gathering a fact,
judging it, and wording it.

## Decision

Three layers with a one-way dependency rule, plus a base tier of constants and
declarations that any of them may use:

    collect/   talks to AWS, produces facts
    analyze/   decides what they mean. No AWS, no I/O, no prose.
    present/   renders. Nothing imports it.

Enforced by `tests/test_layers.py`, which reads every intra-package import with
`ast`. A comment stating the rule is worth approximately nothing — the codebase
had one, and broke it in three places.

Two structural consequences carry most of the value:

**Writers select; the pipeline does not strip.** `present/` emits exactly
`rows.ROW_FIELDS`. An internal field cannot leak regardless of what a row
carries, so nothing needs removing at a particular moment and
`build_graph_data` is a pure function.

**References are resolved once, structured.** `row["_references"]` is read by
the graph, the grouping audit, and the renderer that words the `connections`
column. Those three cannot disagree about what is linked.

## Consequences

The agreement tests are deleted rather than kept passing — the drift they
guarded against is no longer representable. What survived moved to
`tests/test_references.py`, which tests the wording itself.

Two placements look wrong and are deliberate, both recorded because the obvious
correction would break something:

- `staleness.py` is **not** in `analyze/`. 25 collectors call `flag_stale()`,
  and they cannot not: staleness is decided with evidence only the collector
  has. No later pass could re-derive it without duplicating every collector, so
  the rule would have had no correct implementation.
- `console.py` is **not** in `present/`. Every layer reports progress, so a
  console there would make three layers import upward.

An import guard is necessary and not sufficient. It cannot see analysis
substring-matching a string another module wrote, because that involves no
import. One such dependency remains and is recorded in `test_layers.py`: the
weak-evidence check reads `why_grouped`.

## Alternatives considered

**`TypedDict`s over the existing dict** (the previous ROADMAP entry). Types the
fused record rather than splitting it, so every defect above survives.

**Shrinking `__init__.py` so importing the package does not load `present/`.**
Attempted and reverted. `run()` wires every layer together, so it pulls
`present/` in regardless; making `run` lazy as well achieved the property and
broke the CLI, because `from aws_resource_audit import run` then resolves to the
submodule rather than the function. All 315 tests passed with it broken, which
is how `tests/test_entrypoint.py` came to exist. The layering is enforced in the
source, which is where it matters.
