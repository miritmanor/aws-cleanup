# AGENTS.md

Instructions for coding agents working in this repository.

A personal, read-only AWS account auditor: a CLI and a local web app over one
Python core. How it is put together: [docs/architecture.md](docs/architecture.md).
Usage, flags and configuration: [README.md](README.md). Tests:
[tests/README.md](tests/README.md).

## Rules

- **A user-facing change lands in both front ends.** Any feature or bug fix
  applies to the CLI and the web app together, unless genuinely specific to one
  (a CLI-only flag, a web-app-only session concern).
- **Where Python already decides something, the web app serves that decision
  through a thin endpoint** rather than re-deriving it in TypeScript. A tiering
  rule, a colour table, a row schema, a rendered diagram — one source. See
  `GET /api/service-meta` and `GET /api/mermaid` for the pattern.
- **Never re-scan to change how output looks.** Run `aws_regenerate_report.py`.
  A scan costs minutes and a chargeable Cost Explorer request.
- **The layer arrow runs one way:** `collect/` → `analyze/` → `present/`. A
  module may import its own tier and anything below it, never above.
  `tests/test_layers.py` enforces this by reading every intra-package import
  with `ast`; `KNOWN_VIOLATIONS` is the baseline and may shrink, never grow.
- **Nothing in `aws_resource_audit/` may import `backend`, `fastapi`, `pydantic`
  or `uvicorn`.** This is what keeps the CLI runnable on a machine that has
  never heard of FastAPI.
- **`render.py` must never reach `collect/`.** The layer rules can't express
  this (both are app tier), so `NoAwsFromTheRendererTests` checks reachability.
- **New AWS coverage goes in `collect/resources/`.** Consult
  [`docs/adding-a-resource-type.md`](docs/adding-a-resource-type.md) first —
  its checklist is what keeps the coverage guards passing.
- **A new collector that calls a new AWS action must grant it in**
  [`iam/aws-audit-role.yaml`](iam/aws-audit-role.yaml), the single place
  permissions are declared, by exact name: no wildcards, reads only.
  `tests/test_iam_role.py` fails on a call that is not granted and on a grant
  nothing calls. Without it the failure is silent: the scan passes under
  `ReadOnlyAccess` and, under the deployed role, reports the type as `ERR`.
- **Nothing reaches the agent's LLM except through `backend/app/privacy/`.**
  Rows, document hits and the user's question are masked with the
  `Pseudonymizer`; the reply is revealed with `RevealStream`. A new tool or
  prompt input goes through it too. See `docs/decisions/agent/0071`.
- **A project's identity is `project_id`; `project_group` is a label.** Two
  projects can share a label and a rename changes one, so nothing may group,
  count or join on it — in Python or in TypeScript. The vocabulary, and the
  bugs this caused, are in `docs/decisions/project-wide/0059`.
- **Every inferred link between resources needs a registry entry.** Declare it
  in `connection_types/<family>.py` and pass `conn_type=` to `add_edge`, which
  makes it a required keyword-only argument so a new call site cannot silently
  create links no configuration can turn off.
- **Every failure site logs, and the level is `AUDIT_LOG_LEVEL`.** WARNING or
  ERROR wherever something was swallowed; DEBUG for per-resource detail and for
  the agent's raw LLM traffic (`app.agent.payload`, off by default: it shows
  tokens, but document-search hits are logged before masking — see
  `docs/decisions/project-wide/0036`). Configuration lives in one dict in
  `logsetup.py`; nothing else may call `dictConfig`, and no module in
  `aws_resource_audit/` may configure logging at all. `console.say/detail/warn`
  is the *UI*, not logging, and both channels stay.
- **Output files are generated — never hand-edit them.** That includes
  `tests/golden/`; regenerate with `UPDATE_GOLDEN=1` and read the diff.
- **Decision records are only for important architectural or structural
  choices**: a method, an API, a data model, a storage or database, a
  deployment option, a security boundary, how the code is divided. Not for
  product behaviour, detection rules, features, refinements of an existing
  record, or tooling and workflow; their reasoning goes in a short comment at
  the code. Records go in the matching `docs/decisions/` subfolder (criteria in
  its README). When a decision changes, update its record; when it no longer
  applies, delete it.

## Before you edit

- Traps that would silently corrupt output are documented **in the code**, at
  the site where the mistake gets made — brace doubling in `HTML_TEMPLATE`,
  the `row["_graph"]` pop, the `__getattr__`-served live names. Read the
  module docstring before editing one of these.
- Read the relevant file in [`docs/decisions/`](docs/decisions/) **before**
  reversing a design choice; several record dead ends that look like obvious
  improvements.

## Before you commit

- After finishing an implementation and before every commit, run the
  `pre-commit-review` skill
  ([`.agents/skills/pre-commit-review/SKILL.md`](.agents/skills/pre-commit-review/SKILL.md);
  Claude Code reads it through the `.claude/skills` symlink). It reports
  findings and edits nothing; the user chooses which to fix.

## Where to look

- [`docs/architecture.md`](docs/architecture.md) — components, what each is
  responsible for, and what may depend on what.
- [`docs/decisions/`](docs/decisions/) — why things are the way they are.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — AWS resource types planned but not
  scanned yet.
- [`docs/adding-a-resource-type.md`](docs/adding-a-resource-type.md) — the
  checklist for adding one.
- [`docs/TECH_DEBT.md`](docs/TECH_DEBT.md) — known rough edges.
- [`iam/aws-audit-role.yaml`](iam/aws-audit-role.yaml) and
  [`iam/README.md`](iam/README.md) — the read permissions a scan needs.
