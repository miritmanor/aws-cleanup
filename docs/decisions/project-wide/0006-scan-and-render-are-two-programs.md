# 6. Scanning and rendering are two programs, joined by a file

Date: 2026-08-09

## Context

A scan is expensive and a render is free, and until now you could not have one
without the other.

`run()` collected from AWS, analysed, and rendered in a single pass, with the
results held only in memory. So changing a colour in the legend, a column in
the table, a pill in the summary or the wording of the grouping audit meant
re-running the whole thing: minutes of AWS API calls across every region, plus
a chargeable `ce:GetCostAndUsage` request. The cost is not the objection on its
own — the objection is that it is charged for something that has nothing to do
with AWS. Iterating on how the output looks was gated on re-reading an account
that had not changed.

The package was already layered `collect/ → analyze/ → present/`, with
`tests/test_layers.py` failing on an import running the wrong way. The layering
was real; what was missing was a **file** at the seam. Layers describe how code
may depend on code. They say nothing about when it runs.

## Decision

`aws_cleanup.py` scans and writes `aws_scan.json`.
`aws_regenerate_report.py` reads that file and writes every rendered output.
Both go through one function, `render_outputs()` in `render.py` — a scan still
renders at the end, but by exactly the route `aws_regenerate_report.py` takes,
so the two cannot drift into producing different reports.

`run()` reads its own snapshot back before rendering, rather than rendering the
rows still in memory. It costs a fraction of a second and buys the property the
split rests on: a scan renders *through* the file, so anything the snapshot
fails to carry breaks the scan too, instead of surfacing days later the first
time someone runs `aws_regenerate_report.py`.

Three things about the file's contents were forced rather than chosen:

- **Rows are stored raw, not rendered.** The `connections` column is worded at
  render time from `row["_references"]`, on top of prose the collector already
  wrote into `row["connections"]`. Storing the rendered form would re-render
  into itself and double every cell. For the same reason `created`/`last_used`
  stay datetimes and `tags` stays a dict: `present/format.py` is the only place
  values become text, and it needs values to work with.
- **`_references` is stored**, though it is an internal field. Three separate
  outputs derive from it — the connections column, the grouping audit, the
  graph. `SNAPSHOT_ROW_FIELDS` therefore *selects* like `ROW_FIELDS` does: the
  other internal fields are not representable in the file, rather than merely
  absent from the last one written.
- **The graph is stored whole.** Which parts of it to draw is a display
  question, so freezing that answer at scan time would put a display choice back
  behind an AWS scan — the exact thing being fixed.

`--output-dir` became a setting in the same change. Where this account's scans
live is a standing fact about the setup, not a decision made afresh each run;
that is the standing rule. Pointing at the same directory each time was
already needed so saved names survived, which is an admission it was never a
per-run choice. `aws_regenerate_report.py` therefore
has no flags at all, and both programs read `output_dir` and `snapshot_file`
from `audit_config.json`.

Because that config is read from the current directory, rendering a *different*
scan is `cd`-ing to a directory with its own `audit_config.json` and running
`aws_regenerate_report.py` there. That is what `--output-dir` was actually for.

## Consequences

- Changing anything about how the output looks is now a sub-second re-render.
  The first scan after this change is the last one needed to iterate on
  presentation.
- `audit_groups` and the graph and diagram builders live in `analyze/` but run
  on the rendering side. They are pure functions of the snapshot, and putting them
  before the file would have frozen display choices into a scan. Layer position
  and execution side are different questions; this is where they diverge.
- The HTML header had to start taking its timestamp from the snapshot. It
  stamped itself with render time, which was harmless while rendering only ever
  followed a scan and became a lie the moment it did not: a page drawn today
  from last week's scan describes last week's account.
- `aws_scan.json` is the most sensitive file a run writes — every row, every
  resolved reference, the whole graph, in one place rather than one view of it.
  It is gitignored, and `snapshot_file` is required to be a bare `.json`
  filename so that the ignore rule can match it and so it cannot escape the
  output directory.
- `render.py` is APP tier, which may import anything, so the layer rules cannot
  express "no AWS from the renderer". `NoAwsFromTheRendererTests` walks what
  `render.py` transitively reaches and fails if `collect/` is in it. Without
  that, one import added in good faith to "just refresh that one field" would
  take the whole property away silently.
- Saved project names are applied on the rendering side too
  (`naming/effective.resolve_effective_membership`), so
  assigning a name needs no scan.
