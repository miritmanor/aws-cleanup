# Tech debt

Known-suboptimal things that work today. One short line each — the reasoning
belongs in `docs/decisions/` if it turns into a decision.

- **`safe_call` still returns a dict on failure.** It is now a `CallFailed`
  carrying the AWS error code and kind, and a test guards every call site
  (`collect/calls.py`). Raising instead would remove the dict shape
  entirely, at the cost of rewriting 107 call sites.
- **Collectors run one after another.** Running them in parallel would shorten
  a scan, but the EC2 collectors share a usage registry and must run in order,
  more calls per second means more throttled (ERR) rows, and role usage, raw
  capture and console output are shared. Only worth it if scans get too slow.
- **Repeated code that pylint's `duplicate-code` finds (turned off in `pyproject.toml`).**
  The CloudFormation stack walk in `collect/amplify.py` and `collect/stacks.py`;
  the counting properties of `ProjectSummary` and `ServiceSummary`; the
  JSON-object loader in `settings.py` and `backend/app/agent/settings.py`.
- **Hand-picked project members are applied after automatic grouping.** They
  correct a wrong merge but cannot prevent one, since grouping never sees them.
- **The HTML report needs a network connection.** `aws_inventory.html` loads
  Tabulator and Mermaid from unpkg.com, so the table and diagram fail offline.
- **ECS: deleting the app stack during a scan loses the scan.** Nothing checks
  whether one is running.
- **ECS: the permissions needed to deploy are broad.** Documented in
  `deploy/ecs/README.md`, not reduced.
- **ECS: the Scan panel's profile list reads a credentials file the task does
  not have**, and may show a misleading message there. Not checked.
- **Rows are untyped dicts.** `rows.ROW_FIELDS` declares the schema, but `Row`,
  `Edge` and `Reference` are not `TypedDict`s and most functions have no hints.
- **`HTML_TEMPLATE` doubles every literal brace** because it is rendered with
  `str.format()`. `string.Template` would remove that and let it be a real `.html`
  file; verify against a rendered golden, not just the unit tests.
