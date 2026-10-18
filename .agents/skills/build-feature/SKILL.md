---
name: build-feature
description: The workflow for adding new functionality to this repo — clarify requirements, design, implement, review, plan tests, write tests, review tests and coverage, final review. Use when the user asks to add a feature, capability, option, flag, endpoint, report section, UI control or AWS resource type, or otherwise extend what the auditor does. Not for bug fixes, refactors or documentation-only changes.
---

# Build a feature

Eight steps, in order. There is **one stop point**, at the end of step 1:
agree the scope with the user and whether the feature needs a design
document. After the user confirms, run steps 2 to 8 one after another
without waiting, and report once at the end.

Keep a checklist of the eight steps (TodoWrite or a list in your reply) and
update it as you go, so the user can see where the work is.

## 1. Clarify the requirements (stop point)

1. Read before asking. Read `AGENTS.md`, `docs/architecture.md`, and the code
   the feature touches, so the questions are ones the code cannot answer.
2. Restate the request in two to four sentences: what the user will be able
   to do that they cannot do now, and where (CLI, web app, both).
3. Ask about what is still open. Use AskUserQuestion for choices with a
   small set of answers. Typical open points in this repo:
   - **Scope and non-goals**: what is explicitly out.
   - **Front ends**: both, by default (`AGENTS.md`). If one only, why.
   - **Every CLI flag and UI control**: propose each one with its name,
     default and behaviour, and get the user's approval for each. Do not add
     an option the user has not approved.
   - **Data**: is it already in the snapshot, or does it need new AWS calls?
     New calls mean new IAM grants and a re-scan; Cost Explorer requests cost
     money.
   - **Output**: which of the output formats change (and so which golden
     files will move).
   - **README**: if the README will change, the exact text to add.
   - **Agent**: does the LLM agent see the new data? Then it goes through
     `backend/app/privacy/`.
   - **Edge cases**: denied permissions, empty account, missing region,
     resources without tags, a renamed project.
4. Propose the design size, with the reason. **Large** (design document) if
   any of these hold: a new module or package; a new endpoint; a new
   collector or resource type; a change to the snapshot schema or a stored
   format; a new trust or privacy surface; work in more than one tier plus
   the frontend; or more than about five files. Otherwise **small** (a few
   sections in the reply).
5. **Stop**: present the requirements as a numbered list (R1, R2, …), the
   non-goals, the approved options and the proposed design size. Wait for
   the user to confirm or correct them. This is the last time to wait.

## 2. Design

Find the existing mechanisms the feature should use (`safe_call`,
`new_row`/`add_edge`, `connection_types/`, the `GET /api/service-meta`
thin-endpoint pattern, `console`, `settings=`), and read every
`docs/decisions/` record whose subject it touches. For a new AWS resource
type, follow `docs/adding-a-resource-type.md`.

### Small feature

Write the design in your reply, a few short sections:

- **Changes**: each file and what changes in it, by tier.
- **Interfaces**: flags, endpoints, row fields, payload shapes.
- **Data flow**: from collection (or the snapshot) to each front end.
- **Traps and risks**: the documented traps near the change, errors, privacy.

### Large feature

1. Ask the `architect` agent (its Mode 2) for two or three options and a
   recommendation, giving it the confirmed requirements. Choose one.
2. Write `docs/designs/<feature-slug>.md` with these sections:
   - Problem and requirements (the R-list from step 1), non-goals
   - Existing mechanisms reused
   - Options considered and the one chosen, with the reason
   - Design: components per tier (`collect/` → `analyze/` → `present/`,
     backend, frontend), data model and snapshot changes, endpoints, CLI
     flags and UI controls (as approved)
   - Errors and logging: where each failure is caught and what it logs
   - Security, privacy and IAM: new AWS actions, what reaches the LLM,
     untrusted text crossing into HTML, Mermaid or draw.io
   - Scale: AWS calls, Cost Explorer requests, request-path work
   - Test approach (outline; the full plan is step 5)
3. If a choice meets the threshold in `docs/decisions/README.md`, write the
   decision record (or edit the existing one).

Continue to step 3.

## 3. Implement

1. Follow the design. If it turns out to be wrong or incomplete, change the
   design (and the document, for a large feature) and note why in the final
   report.
2. Keep to the rules in `AGENTS.md` while writing, not only at review time.
   The ones a feature most often breaks: one front end only; TypeScript
   re-deriving a Python decision; an import against the layer arrow; a new
   AWS call not granted in `iam/aws-audit-role.yaml`; data reaching the LLM
   outside `backend/app/privacy/`; grouping on `project_group`; `add_edge`
   without `conn_type=`; a swallowed failure that does not log.
3. Personal rules: one module or class per file, no file over 500 lines, no
   comment over 2 lines.
4. Docs that belong to the change: `CHANGELOG.md` for a user-facing change;
   `docs/architecture.md` if a component or dependency changed; the README
   text agreed in step 1, and no other README change.
5. Run the core suite, `python3 -m unittest discover -b -s .`, to confirm
   nothing existing broke. If golden files fail and the output change is
   intended, regenerate with `UPDATE_GOLDEN=1 python3 -m unittest
   tests.test_golden` and read the diff; if it is not intended, it is a bug.
   New tests come in step 6.

## 4. Review the implementation

1. Run the `architecture-reviewer` skill on the working tree. For a large
   feature, use the `architect` agent so the review starts without this
   session's assumptions.
2. Run the `pre-commit-review` skill's checks 1 to 5 and 7. Skip check 6
   (tests); steps 5 to 7 cover it.
3. Check the change against the requirements: for each R, name the code
   that implements it. A requirement with no code is a finding.
4. Fix every confirmed finding, then rerun the core suite. Those skills say
   to wait for the user to pick findings; inside this workflow, do not wait.

## 5. Plan the tests

Read `tests/README.md` first. For each requirement and each branch the change
added, list the tests:

- the normal path, boundaries, empty and missing input;
- error paths: the denied-API ERROR row, the log record (`assertLogs`), the
  HTTP status and message;
- a connection type in all three states (on, report-only, off);
- rendering on both the live and the snapshot round-trip path;
- the backend endpoint in `backend/apitests/` when the web app changed;
- which golden files should change, and which structural guards
  (`test_layers.py`, `test_iam_role.py`, the coverage guards) must still pass.

For each test give: the module it goes in (follow the placement in
`tests/README.md`), its name, what it asserts, and the bug it would catch.
Reuse the shared helpers (`fakes.py`, `corpus.py`, `collectors_base.py`, …).
Keep the plan as a table; it goes in the final report.

## 6. Implement the tests

1. Write the planned tests, or give the plan to the `tester` agent (its
   Mode 1). Tests use fakes only: no real AWS, no real account data.
2. Run the whole core suite. The backend suite and `tsc` run only in Docker
   (`docker compose build`); if they could not run, note it and name the
   tests not run. Do not report them as passing.
3. If a test exposes a bug in production code, fix the code and note the bug
   in the final report.

## 7. Review the tests and coverage

1. Use the `tester` agent's Mode 2 on the change, so the tests are reviewed
   by an agent that did not write them.
2. Check each planned test exists and asserts what the plan said.
3. Coverage: `coverage.py` is not installed here, so trace it by hand. For
   every new or changed branch in production code, name the test that runs
   it. A branch with no test is a finding.
4. For the two or three most important behaviours, confirm the test fails
   without the change: temporarily undo the relevant production line with an
   edit, run the test, then restore the line and rerun.
5. Fix every confirmed finding (untested, weak, wrong) and rerun the suite.

## 8. Final review and report

1. Run the `pre-commit-review` skill in full, on the whole change. Fix its
   confirmed findings and rerun the suite.
2. Check the docs: `CHANGELOG.md`, the agreed README text,
   `docs/architecture.md`, the decision record, and, for a large feature,
   `docs/designs/<feature-slug>.md` updated to match what was built.
3. Report once, covering:
   - what was built, file by file;
   - the design (small) or the design document's path (large), and any
     change made to it during implementation, with the reason;
   - a traceability table: requirement → code (`path:line`) → test;
   - the findings from steps 4, 7 and 8 and how each was fixed;
   - findings left unfixed and why (a fix that would go beyond the agreed
     scope, or a choice only the user can make);
   - the exact test results, and the suites that could not run here;
   - the checklist of all eight steps.

Do not commit unless the user asks.
