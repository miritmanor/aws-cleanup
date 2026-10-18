# Architecture

How the auditor is put together: its components, what each is responsible for,
and what may depend on what. The rules that keep it this way are in
[AGENTS.md](../AGENTS.md); the reasons are in [decisions/](decisions/).
Module-level detail lives in each module's docstring, not here.

## Shape

The auditor inventories resources across regions, pulls whatever "last used"
signal each service exposes, clusters resources into inferred "projects," and
flags stale or orphaned cleanup candidates.

Two front ends over one core, and the core knows about neither:

    aws_cleanup.py /             the CLI. boto3 is its only dependency.
    aws_regenerate_report.py
    backend/ + frontend/         a local web app: FastAPI + React, in Docker.

The core is the `aws_resource_audit/` package. It is a library with a CLI in
front of it, not a CLI with a library inside it.

## The CLI: two programs joined by a file

`aws_cleanup.py` scans AWS and writes `aws_scan.json`;
`aws_regenerate_report.py` reads it and writes all seven outputs with no AWS
calls. A scan renders at the end by calling the same `render_outputs()`, after
reading its own snapshot back from disk.

The two scripts are the argparse layer and the only entry points. `run.py`
(`run(args)`) is the scan end to end; `render.py` (`render_outputs()`,
`render(args)`) turns a snapshot into the seven files and never talks to AWS.
`cli_output.py` beside them prints what only a CLI user needs: the file list
and the pointer to `help/cli-messages.md`. Library console lines are facts only
(`console.py`).

## Inside the package: three layers plus a base

    base  →  collect/  →  analyze/  →  present/

A module may import its own tier and anything below it, never above.

### Base

Modules at the package root that every layer speaks:

- **Configuration** — `config.py` (thresholds, service classification tables,
  `now()` / `set_now()`), `settings.py` (`audit_config.json` and the output-file
  layout), `region_store.py` (the per-account region list a scan with no regions
  given covers, `analyze/active_regions.py`).
- **Scan context** — `scope.py` (the account and partition, set once by
  `run()`), `coverage.py` (the coverage ledger: what the scan looked at, and
  whether "none found" or "lookup denied").
- **Row vocabulary** — `rows.py` (`new_row`, `add_edge`, the row schema),
  `snapshot.py` (the schema written down; both sides depend on it),
  `activity.py` (`ActivityEvidence`: used, measured idle, or no signal — three
  answers, never collapsed), `staleness.py` (base tier, not `analyze/`, because
  collectors call it with evidence only they have).
- **Billing vocabulary** — `billing.py` and `billing_tables.py` (how the bill
  maps onto resource types; re-exported by `config.py`), `aws_services.py`
  (which AWS product each resource type is filed under).
- **Connections** — `connection_types/` declares every link, one module per
  service family; `registry.py` holds each type's on/report-only/off state.
- **Output channels** — `console.py` holds every `print()`, via `say()` /
  `detail()` / `warn()`, which `set_sink()` redirects and which also mirror into
  `logging`. `logsetup.py` holds the one `dictConfig` dict; only a front end
  calls `configure()`. `errors.py` defines `AuditError`, raised when the *user*
  must act.

### `collect/` — everything that talks to AWS

- `collect.py` (`collect_all`) is the collection phase end to end.
- `resources/` holds one module per service family; new coverage goes here.
- Shared helpers: AWS calls and paging, regions, CloudWatch activity, IAM policy
  parsing, ARN resolution, references found in strings.
- `session.py` and `credentials.py` build the authenticated session (profile,
  optional AssumeRole) and turn an auth failure into advice.
- `stacks.py` walks CloudFormation stacks, nested ones included — AWS's own
  record of what owns what. `amplify.py` does the same for Amplify.
- `cost.py` makes the Cost Explorer request and caches it: one request, plus a
  second only when a bucket needs explaining (`collect/cost.py`).
- `raw_capture.py` writes the per-resource raw AWS dump. Nothing reads it back.

### `analyze/` — what the facts mean, no AWS ever

`analyze.py` (`run_analysis`) is the phase end to end. It first hides a default
VPC nothing uses and nobody changed (`analyze/default_network.py`), then:

- resolves recorded references to rows that exist;
- groups resources into projects and resolves hub membership;
- assigns tiers (runtime or deployment), a separate axis from `project_group`;
- computes risk, which needs the whole resolved row set;
- attributes the bill to the rows that caused it, or refuses and says why
  (decision 0037), and reads the bill as a checklist against the coverage
  ledger;
- finds active regions: regions with rows, billed regions, and regions where a
  lookup failed;
- builds the graph data, including the architecture graph (runtime types and
  runtime lines only, `analyze/architecture.py`) and network placement for diagram boxes;
- summarises projects (counted on `project_id`, no cost, `analyze/project_summary.py`) and
  services;
- audits its own grouping, returning findings and writing nothing.

### `present/` — rendering only

- `format.py` is the only place values become text.
- Flat outputs: CSV, JSON, Markdown, the `connections` column, cost columns and
  the coverage-gaps section, the scan log's region summary.
- `graph/` writes Mermaid and draw.io diagrams. `bands.py`, `network_tree.py`
  and `groups.py` decide nesting (project boxes; Region > VPC > subnet; ASG /
  EKS) for both formats. Icons come from generated `aws_icons_data.py`
  (`tools/build_aws_icons.py`).
- `bubbles/` packs the overview's circles in Python, not D3 (decision 0041),
  and builds the one payload both front ends read (`GET /api/overview`).
- `html/` renders the HTML report from `HTML_TEMPLATE` and `SERVICE_META`.

### `naming/` — not a layer

Saved project names, merges of one project into another (`naming/merges.py`),
resources added to a project by hand (`naming/additions.py`), old S3 keys, AWS tag
writing, and the passes that re-apply the store to scanned rows.
It spans all three layers, and only an entry point may use it: `run()`,
`render_outputs()`, `SnapshotStore.load()`, `GET /api/projects`.

## The web app

`backend/` (FastAPI) serves the same snapshot the CLI renders; `frontend/`
(React) displays it. Wherever Python already decides something, the backend
exposes the decision through a thin endpoint — `GET /api/service-meta`,
`GET /api/mermaid`, `GET /api/overview`, `GET /api/projects` — and the
frontend does not re-derive it.

The optional agent answers questions about a scan with an LLM. Everything sent
to the LLM passes through `backend/app/privacy/`, which masks identifiers and
reveals them in the reply (decision 0071).

## Seams for callers that are not a terminal

The web app drives the library through additive seams: `settings=`,
`progress=` and `should_stop=` on `run()`, `console.set_sink()`, and
`AuditError` in place of `SystemExit`. Omit them and the behaviour is exactly
what the CLI has always had, which is why "the CLI still passes" says nothing
about whether they work. `tests/test_seams.py` is what does.
