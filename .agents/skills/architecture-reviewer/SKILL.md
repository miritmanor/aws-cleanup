---
name: architecture-reviewer
description: Review the architectural quality of a change in this repo — fit with the project's architecture and design patterns, maintainability, scalability, where errors are handled, and trust boundaries. Use when a change adds a module, endpoint, collector or seam, moves responsibility between components, or before merging a branch.
allowed-tools: Read, Grep, Glob, Bash(git status:*), Bash(git diff:*), Bash(git log:*)
---

# Architecture review

Review a change as a senior software architect would: does it fit how this
project is built, and will it still be easy to change in a year? **Do not edit
any file.** Fixes are applied only after the user picks which findings to act
on.

This review is about structure. Line-level bugs, logging details, secrets and
test coverage belong to `pre-commit-review`; report them here only where the
cause is structural (a responsibility in the wrong component, a missing seam).

## 1. Collect the change

1. Decide the scope. If the user named a branch, PR, commit range or path,
   review that. Otherwise review the working tree: `git status --porcelain`
   and `git diff HEAD`; if that is empty, the branch against `main`
   (`git diff main...HEAD`). Read every new file in full.
2. Read [docs/architecture.md](../../../docs/architecture.md) and the Rules
   section of `AGENTS.md`. These describe the intended shape; the review
   compares the change against them.
3. For each changed module, read its module docstring and the modules it now
   imports or is now imported by. Grep for callers in `aws_resource_audit/`,
   `backend/`, `frontend/src/` and `tests/`.
4. List `docs/decisions/` and read every record whose subject the change
   touches. A change that reverses a recorded decision without updating the
   record is a finding, even if the new design is better.

## 2. The checks

Go through every check, even when an earlier one found a lot.

### 1. Project architecture

- The layer arrow: base → `collect/` → `analyze/` → `present/`. A new import
  against it, or a module placed in a tier that does not match what it does
  (AWS calls outside `collect/`, interpretation in `present/`, text formatting
  outside `present/format.py`).
- The core stays a library: nothing in `aws_resource_audit/` imports
  `backend`, `fastapi`, `pydantic` or `uvicorn`, ends the process, or
  configures logging (decisions 0003, 0009, 0036).
- `render.py` and the regenerate path never reach `collect/`; output changes
  must not need a re-scan (decision 0006).
- `naming/` used only from an entry point (`run()`, `render_outputs()`,
  `SnapshotStore.load()`, `GET /api/projects`).
- The two front ends: a user-facing change reaches both, and the web app gets
  a Python decision through a thin endpoint instead of re-deriving it in
  TypeScript (the `GET /api/service-meta` pattern).
- Seams for non-terminal callers (`settings=`, `progress=`, `should_stop=`,
  `console.set_sink()`, `AuditError`) stay additive: omitting them gives the
  CLI's behaviour unchanged.

### 2. Existing design patterns

- New AWS coverage follows the `collect/resources/` family pattern and the
  `docs/adding-a-resource-type.md` checklist; new links are declared in
  `connection_types/<family>.py` and passed with `conn_type=`.
- Rows are built with `new_row`/`add_edge` and match `snapshot.py`; a new
  field is added to the schema, not carried as an ad-hoc key.
- Failed lookups use `safe_call`'s structured failure and the coverage ledger,
  never "found nothing" (`collect/calls.py`). Activity stays three-valued
  (`ActivityEvidence`).
- A new mechanism that duplicates an existing one: a second config reader, a
  second place values become text, a second store, a second way to call AWS.
  Name the existing one it should use.

### 3. Maintainability

- Responsibilities: one module or class per file, no file over 500 lines, a
  module whose docstring no longer describes what it does.
- Coupling: a change that forces edits in many unrelated modules, a circular
  dependency, private names (`_x`) imported across modules, a function whose
  return shape now has callers that each special-case it.
- Identity vs label: anything grouping, counting or joining on
  `project_group` instead of `project_id` (decision 0059).
- Hidden traps: a change near brace doubling in `HTML_TEMPLATE`, the
  `row["_graph"]` pop, or `__getattr__`-served names that the docstring warns
  about but the change ignores.
- An architectural or structural choice (a method, an API, a data model, a
  store, a deployment option, a security boundary) with no record in
  `docs/decisions/`. Smaller choices get a short comment at the code instead.

### 4. Scalability

Scale here means a larger account: more regions, resources, projects, links
and a bigger bill.

- AWS calls per resource where a list or batch call exists; missing
  pagination; work repeated per region that could be done once.
- Quadratic passes over rows or edges in `analyze/` or `present/graph/`
  (nested loops, `in` on a list inside a loop).
- Cost Explorer requests beyond the documented one or two (`collect/cost.py`).
- Web app: work done lazily on the request path instead of at scan or
  rebuild time; whole-snapshot payloads where the page needs a slice;
  state shared with the scan thread without a lock (decision 0010).

### 5. Error handling (structure)

- Each failure is handled at the right layer: the library raises
  (`AuditError` for something the user must fix, a plain exception for a
  bug); the front end decides what to show and whether to exit.
- Broad catches only at boundaries (request handler, scan thread, progress
  callback), and those boundaries log.
- One failed collector, region or lookup degrades its own rows and the
  coverage ledger, and does not abort the scan or erase other results.
- The web app maps library errors to HTTP status in one place, not per
  endpoint.

### 6. Security (structure)

- Trust boundaries: tag values, resource names and request input are
  untrusted. Where does the change cross from data into HTML, Mermaid,
  draw.io, a file path or a shell command, and which component escapes or
  confines it?
- Everything reaching the agent's LLM passes through `backend/app/privacy/`
  (decision 0071); a new tool or prompt input that bypasses it.
- AWS access stays read-only and exact: a new action is granted by name in
  `iam/aws-audit-role.yaml` (decision 0073).
- Exposure added by the design: a new endpoint that writes or deletes, a
  wider port or ingress rule, credentials outside Docker secrets
  (decision 0027).

## 3. Verify before reporting

For each candidate finding, reread the code and confirm it. Say what goes
wrong and when: the concrete change that becomes hard, the input that breaks,
the account size where it slows down. Drop a finding you cannot confirm, or
report it under "Unverified" with what would confirm it. Do not report a
style preference as an architectural issue.

## 4. Report

Start with one line: what was reviewed (scope, files, lines changed).

Then the findings, numbered so the user can pick them:

```
### 1. Critical issues
1. [architecture] aws_resource_audit/present/html/report.py:42 — imports
   collect.cost to fetch the bill at render time. Consequence: regenerating
   the report makes a chargeable Cost Explorer request and needs AWS
   credentials. Fix: read the cost already stored in the snapshot.

### 2. Important improvements
### 3. Minor suggestions
### Unverified
```

**Critical issues**: a broken rule in `AGENTS.md`, a layer or boundary
violation, an unrecorded reversal of a decision, a design that bypasses the
privacy layer or widens access. **Important improvements**: duplication of
an existing mechanism, coupling that makes the next change expensive, a
scaling problem on a realistic account. **Minor suggestions**: everything
else worth saying.

End with a table with one row per check (1 to 6): finding count, or "nothing
found" with one line on what was checked.

Then ask which findings to fix. Do not commit.
