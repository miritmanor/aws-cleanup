# Tests

```bash
python3 -m unittest discover -b -s .     # whole suite, ~3s, no AWS calls; -b hides passing tests' output
python3 -m unittest tests.test_connections_network
```

Driven by mock AWS payloads via `fakes.py`. The clock is pinned in
`__init__.py` (`FROZEN_NOW`), which is what makes rendered output comparable
between runs.

## The safety net

`test_golden.py` + `corpus.py` is **the net for refactoring**: a synthetic
account rendered to all seven output formats and diffed byte for byte. The unit
tests say each piece behaves; this says the output as a whole did not move,
which is a different question.

Regenerate deliberately with `UPDATE_GOLDEN=1 python3 -m unittest
tests.test_golden` and **read the diff** — an unreviewed regeneration turns the
whole thing into a rubber stamp.

The corpus is awkward on purpose: every service, plus a report-only link, a
dangling reference, an id collision of the wrong type, a shared-plumbing hub
that must not fuse two projects, and a bridged edge.

`SnapshotRoundTripTests` **subclasses** the whole set and re-runs every case
against rows that went through `aws_scan.json` — so a new golden is covered on
both paths automatically, and "the snapshot is complete" is a byte-for-byte
assertion rather than a claim.

## The overview

Behaviour, not structure — but grouped here because all three answer questions
the picture cannot be trusted to answer for itself.

- **`test_project_summary.py`** — the By project numbers. Counts reconcile
  across projects and buckets without double-counting a shared resource;
  unknown telemetry never becomes "potentially unused"; an empty project has
  no percentage; a project carries no cost.
- **`test_service_summary.py`** — the By service numbers, the bill lines behind
  its cost circles, and the payload both front ends read: which view opens
  first, and that every circle names an item of its own view.
- **`test_group_names.py`** — `SavedGroupIdentityTests`: a saved group is one
  `project_id`, however many scan projects it spans.
- **`test_bubbles.py`** — the pack's geometry as properties, because every one
  of them is something a reader will believe from the picture whether or not it
  is true: area (not radius) proportional to value, no overlapping pair,
  deterministic and independent of input order, and no circle at all for a
  value that does not exist.
- **`test_render_membership.py`** — renaming a group reaches a regenerated
  report with no AWS call. The bug it pins: the web app applied the saved group
  store on every read and `render_outputs()` applied none of it.

## The structural guards

These walk the source with `ast` rather than exercising behaviour. Each exists
because the failure it catches is invisible at runtime.

- **`test_layers.py`** — the import-direction guard: a module may import its own
  tier and anything below it, never above. `KNOWN_VIOLATIONS` is the baseline
  and may shrink, never grow. Plus `NoAwsFromTheRendererTests` (what `render.py`
  transitively reaches must exclude `collect/`, with a companion assertion that
  `run.py` *does* reach it, so the first cannot pass by reaching nothing) and
  `TheCoreDoesNotKnowAboutTheWebAppTests` (no module in the package may import
  `backend`, `fastapi`, `pydantic` or `uvicorn`).
- **`test_registry.py`** — keeps the declared connection types and the detections
  the code actually performs provably the same set. These walk **every module in
  the package** and assert a floor on what they find: a glob that matched nothing
  would otherwise make all of them pass while checking nothing.
  `ConfigStateWiringTests` is the one place joining config resolution to the gate
  inside `add_edge` — without it a configured `connection_types` entry can be
  silently ignored.
- **`test_raw_capture_coverage.py`** — walks `collect/resources/*.py` and fails if
  a function calls `new_row()` without `raw_capture.record()`. Nothing reads the
  raw dump back, so a forgotten call ships correct reports and an incomplete dump.
- **`test_entrypoint.py`** — `LibraryRaisesCliExitsTests`: `AuditError` is an
  `Exception` and **not** a `SystemExit`, and no module in the package contains a
  `raise SystemExit`, `sys.exit()` or `os._exit()`. The failure it prevents is one
  call site added back in good faith.

## The rest

- `test_collectors_<family>.py` (ec2, network, data, compute, integration,
  security) — one test per collected resource type; shared helpers in
  `collectors_base.py`. `test_collectors_schema.py` holds the cross-cutting
  guards: every type has a test in some collector or connection module, is
  classified in `SERVICE_BILLING`, and emits an ERROR row rather than an empty
  result when its API call is denied — which is how it tells collection
  "denied", not "none".
- `test_connections_<family>.py` — one test per registered connection type,
  asserted in all three states (on / report-only / off), grouped like
  `aws_resource_audit/connection_types/`; `assert_three_states` lives in
  `connections_base.py`. A coverage test in `test_connections_grouping.py` fails
  if a connection type is added without a test in any of them.
- `test_graph.py` — the graph payload. `GraphContractionTests` covers the
  bridged edge (the one deliberate exception to graph/`connections` agreement)
  and the rules that keep bridging from redrawing the supercluster. Shared
  helpers are in `graph_helpers.py`.
- `test_graph_render.py` — the Mermaid view, the HTML report's graph and diagram
  tabs, and project extraction. The `render_html_report` tests catch a mistake
  in `HTML_TEMPLATE`'s doubled braces at test time rather than at the end of a
  real multi-minute scan.
- `test_tiers.py` — one test per tiering rule on its own evidence, and the
  precedence between them. Includes that the absence of evidence produces an
  *assumed* `runtime` with `DEFAULT_WHY_TIER` (empty), distinguishable from a rule that
  actually fired. Plus the collect half (the
  `LogicalResourceId`/`deploymentArtifacts` capture) and that both graph renderers
  compartment a project only when it holds more than one tier.
- `test_run_pipeline.py` — `run()` end to end against a faked AWS. Every piece the
  scan orchestrates was covered and the sequence joining them was not, which is the
  seam that broke when collection and analysis moved into their own modules. It
  patches authentication and region discovery and uses a permissive fake client, so
  an unstubbed operation returns an empty result rather than raising.
- `test_seams.py` — the three seams a non-terminal caller drives the package
  through: `console.set_sink`, `settings=` and `progress=`. Each is additive, so
  "the CLI still passes" would stay true of a seam that quietly did nothing. Its
  load-bearing case is that a render given explicit settings ignores an
  `audit_config.json` in the current directory — with a deliberately *invalid*
  decoy, so it cannot pass by the two agreeing.
- `test_snapshot.py` — what the snapshot may carry (it *selects*, so a future
  internal field cannot leak into it), and that a missing, stale or corrupt one
  produces a sentence naming the file and telling you to run a scan.
- `test_raw_capture.py` — that the raw dump is written as it goes, describes
  exactly one scan, and never costs a scan more than a line of notes when it fails.
- `test_service_map.py` — `ReportCategoryTests` pins `SERVICE_META` categories to
  `tools/aws_service_coverage.csv`; `StructuralCategoryTests` pins the graph's
  membership to those categories. Neither is meaningful without the other.
- `test_references.py` — how each kind of reference is worded in `connections`.
- `test_json.py`, `test_cost.py`, `test_clock.py`, `test_settings.py` — the JSON
  schema, the cost split, the pinned clock, output paths and config validation.
  `test_billing_coverage.py` checks the bill against the scan and the wording of
  cost findings; shared cost fixtures are in `cost_helpers.py`.

`golden/` holds rendered copies of all seven outputs for the synthetic account.
Those are fixtures, not output.

## Backend

```bash
python3 -m unittest discover -b -s backend -t backend # needs fastapi + httpx
```

`docker compose build` runs this suite and the core one in the image's `test`
stage; a failing test fails the build. Locally, the
tests that start the app need the agent's packages (`langchain` and the rest),
which only the image installs.

`backend/apitests/` is **not** called `tests/` deliberately: this suite owns that
name, and those import `tests.corpus` from here — the same synthetic account the
golden files render from, so the API is exercised against the awkward cases for
free.
