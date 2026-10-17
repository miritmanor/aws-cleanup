---
name: pre-commit-review
description: Review the uncommitted change in this repo for bugs, unhandled errors, missing or wrong logging, security risks, secrets or account data leaking to logs, storage or the LLM, and test coverage. Use after finishing an implementation and before every commit.
---

# Pre-commit review

Review the working tree's uncommitted change against the checks below and
report findings. **Do not edit any file.** Fixes are applied only after the
user picks which findings to act on.

## 1. Collect the change

1. `git status --porcelain` and `git diff HEAD` (staged and unstaged together).
   Read every untracked file in full. If there is no change, say so and stop.
2. For each changed hunk, read the whole enclosing function and the module
   docstring. This repo documents traps in the code, at the site where the
   mistake gets made (brace doubling in `HTML_TEMPLATE`, the `row["_graph"]`
   pop, `__getattr__`-served names). A hunk read on its own misses them.
3. Find the callers of every changed function signature or return shape
   (grep for the name in `aws_resource_audit/`, `backend/`, `frontend/src/`,
   `tests/`, `backend/apitests/`).
4. Read the rules in `AGENTS.md`. Read a file in `docs/decisions/` only when
   the change touches its subject; the ones most often relevant are named in
   the checks below.

## 2. The checks

Go through every check, even when an earlier one found a lot.

### 1. Bugs

- Logic errors: wrong condition, off-by-one, inverted flag, wrong variable,
  unreachable branch, a default that hides a missing value.
- `None`, empty list, empty string, missing dict key, a resource in a region
  or account the code did not expect.
- Shared state: module-level attributes, the store instances in
  `backend/app/stores.py`, the scan running in a worker thread while requests
  read the snapshot. Look for unlocked read-modify-write.
- A `safe_call` result read without checking `__error__` (`collect/calls.py`).
  A failed lookup turned into "found nothing".
- Grouping, counting or joining on `project_group` instead of `project_id`,
  in Python or TypeScript (decision 0059).
- `add_edge` without `conn_type=`, or a new link with no entry in
  `connection_types/<family>.py`.
- Callers found in step 1.3 that the change broke.

### 2. Unhandled errors and exceptions

- `except Exception`, bare `except:`, or `except ...: pass` that hides a
  failure. Catching broadly is acceptable only at a boundary (request handler,
  scan thread, progress callback) and only if it logs.
- The library ending the process: `raise SystemExit`, `sys.exit()`,
  `os._exit()` inside `aws_resource_audit/` (decision 0009). It raises
  `AuditError` for something the user must fix; a bug stays a plain exception.
- `AuditError` raised for a bug, which hides the traceback.
- Files, JSON, env vars and AWS responses read without handling the missing,
  corrupt or unexpected case.
- Backend endpoints: an exception that becomes a bare 500 with no log, or a
  traceback or raw exception text sent to the browser.
- Frontend: a `fetch` whose `res.ok` is not checked, an unhandled promise
  rejection, a request with no error state shown in the UI.

### 3. Logging (decision 0036, `aws_resource_audit/logsetup.py`)

The format is set in one place, `logsetup._default_config()`
(`<UTC time>Z <LEVEL> <logger name> <message>` on stderr). The check is that
new code feeds that setup correctly:

- Every module that logs uses `logger = logging.getLogger(__name__)`, so its
  records fall under the `aws_resource_audit` or `app` tree. The agent's raw
  LLM traffic uses the `app.agent.payload` logger and nothing else does.
- Every failure site logs. WARNING or ERROR wherever something is swallowed or
  degraded; `logger.exception(...)` (or `exc_info=True`) where the traceback
  is needed to act on it. DEBUG for per-resource detail. INFO is not used for
  a failure.
- Messages use `%s` arguments, not f-strings, and say what failed, on which
  object, and what happens next (as in `rows.py`: `"lookup failed: %s %s %s - %s"`).
- No `dictConfig`, `basicConfig`, `addHandler`, `setLevel` or formatter
  outside `logsetup.py`. No logging configuration anywhere in
  `aws_resource_audit/`.
- `print()` is not used for diagnostics. `console.say/detail/warn` is the
  user interface, not logging: where the user is told about a failure, it is
  also logged. Both channels stay.
- No third-party logger raised above WARNING.

### 4. Security

- Shell commands built from input (`shell=True`, `os.system`, string
  concatenation into `subprocess`). `eval`, `exec`, `pickle`, `yaml.load`
  without a safe loader.
- Paths built from request input without confinement: path traversal into the
  data volume, `runtime/` or outside `results/`.
- Anything loaded from the current directory that can execute code:
  `dictConfig` instantiates callables through `"()"` keys (decision 0036).
- HTML output: unescaped values in `render.py` / `HTML_TEMPLATE`, React
  `dangerouslySetInnerHTML`, Mermaid or draw.io labels built from resource
  names or tag values (a tag value is attacker-controllable text).
- Exposure: a port in `docker-compose.yml` published beyond `127.0.0.1`, a
  wider ingress rule in `deploy/ecs/`, permissive CORS, a new endpoint that
  writes or deletes without the same checks as its neighbours.
- `iam/aws-audit-role.yaml`: anything but exact read actions; wildcards; a
  write action (decision 0073). The deploy templates: public exposure, wide
  security groups, secrets in plain parameters or env vars.
- Secret files created without restricted permissions (the pseudonym key is
  0600). Credentials passed as env vars rather than Docker secrets
  (decision 0027).

### 5. Secrets and account data: logs, messages, storage, LLM

What counts here:

- **Secrets**: AWS access keys, secret keys, session tokens, `Authorization`
  headers, the agent's API key, `agent_pseudonym.key`, anything read from
  `/run/secrets/`. These may appear **nowhere**: no log at any level, no error
  message, no API response, no stored file, no LLM input.
- **Account data**: account ids, ARNs, resource ids and names, tag values, IP
  addresses, host names, email addresses, project names, uploaded-document
  text. Personal data (people's names, emails) is in this group too.

Rules for account data:

- **LLM**: nothing reaches the model except through `backend/app/privacy/`.
  Rows, document hits, tool results and the user's question are masked with
  the `Pseudonymizer`; the reply goes through `RevealStream`; a tool's query
  from the model is revealed before a local search and its hits masked before
  return (decision 0071). Any new tool, prompt input, system-prompt text built
  from data, or error text passed back to the model goes through it.
- **Logs**: DEBUG may carry per-resource identifiers. WARNING and above should
  name the resource only as far as needed to act. The unmasked
  `app.agent.payload` logger stays DEBUG-only (decision 0036); no new logging
  of prompts, replies or document text at INFO or above.
- **Storage**: account data goes only to the gitignored output locations
  (`results/`, `runtime/`, the data volume). Nothing new is written inside a
  tracked path. A new stored field is checked for secrets.
- **Tests and fixtures**: no real account ids, ARNs, names or keys. Tests use
  the synthetic corpus (`tests/corpus.py`) and fake values. This repo has
  already had account data committed and scrubbed out of history.
- **Messages to the user**: an error message may name the user's own resource;
  it must not echo a credential or a raw botocore exception that contains one.

### 6. Tests

- Every behaviour change has a test. For each new or changed branch, name
  the test that runs it. If none does, it is a finding.
- The test fails if the change is reverted. A test that would pass against
  the old code does not cover the change: check the assertion, not just that
  the code runs.
- Assertions are specific: exact values, not `assertTrue(result)` or "no
  exception". Error paths are asserted (the ERROR row, the log record with
  `assertLogs`, the HTTP status and message).
- A guard that walks files asserts a floor on what it found, so it cannot pass
  by matching nothing (see `tests/README.md`).
- Mocks are not so permissive that the code under test is skipped
  (`test_run_pipeline.py`'s fake returns empty for any unstubbed call).
- Placement follows `tests/README.md`: a collector test in its family file, a
  connection type asserted in all three states, a new AWS action granted in
  the IAM role (`tests/test_iam_role.py`).
- If `tests/golden/` changed, it was regenerated with `UPDATE_GOLDEN=1`, not
  edited, and every line of its diff is explained by the change.

Run the core suite: `python3 -m unittest discover -b -s .`. Report the
result. The backend suite and `tsc` run only in Docker (`docker compose build`);
if the change touches `backend/` or `frontend/`, say they were not run here.

### 7. Project rules from AGENTS.md

Check the change against each rule in `AGENTS.md`'s Rules section. The ones
most often broken: a user-facing change that reaches only one front end; the
web app re-deriving in TypeScript something Python already decides; an import
against the `collect/` → `analyze/` → `present/` direction; `aws_resource_audit/`
importing `backend`, `fastapi`, `pydantic` or `uvicorn`; a hand-edited output
file. Also the personal rules: no code file over 500 lines, one module or class
per file, no comment longer than 2 lines, a significant decision recorded in
`docs/decisions/`, a user-facing change in `CHANGELOG.md`.

## 3. Verify before reporting

For each candidate finding, reread the code and confirm it. State the concrete
input or state that produces the wrong result. Drop a finding you cannot
confirm, or report it under "Unverified" with what would confirm it. Do not
report style preferences as bugs.

## 4. Report

Start with one line: what was reviewed (files, lines changed) and the test
result.

Then the findings, most severe first, numbered so the user can pick them:

```
### Must fix
1. [5 · secrets] backend/app/agent/tools.py:88 — the tool's error text is
   returned to the model unmasked. Failure: a ClientError naming
   arn:aws:s3:::payments-prod reaches the provider. Fix: pass it through
   pseudonymizer.mask().

### Should fix
### Minor
### Unverified
```

Severity: **Must fix** is wrong output, a crash, a security hole, a secret or
unmasked identifier leaving the machine, or a rule in `AGENTS.md` broken.
**Should fix** is a missing log, a missing test, an error handled badly.
**Minor** is everything else worth saying.

End with a table with one row per check (1 to 7): finding count, or "nothing
found" with one line on what was checked. Every check gets a row, so a check
with no finding is visibly done and not skipped.

Then ask which findings to fix. Do not commit.
