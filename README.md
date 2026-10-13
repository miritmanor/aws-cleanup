# AWS Cleanup

**Which of these AWS resources can I delete, and what is still costing me?**

A read-only auditor for a personal or small AWS account. It inventories resources
across regions, pulls whatever "last used" signal each service actually exposes,
groups resources into projects, attributes the bill where it honestly can, and
flags stale or orphaned candidates for cleanup — via a CLI
(`aws_cleanup.py` / `aws_regenerate_report.py`) or a local web app
(`docker compose up`).

It never deletes anything. It tells you what it found, how sure it is, and why.

![The HTML report's overview, rendered from the synthetic test account](assets/screenshot.png)

> **Status:** a personal project, shared as-is. It is read-only by design, but you
> run it against your own account at your own risk. MIT licensed, no warranty.

## Quick start

Needs Python 3.9+ and an AWS profile (ideally one that assumes the role in
[`iam/`](iam/README.md)).

```bash
git clone https://github.com/miritmanor/aws-cleanup.git && cd aws-cleanup
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # boto3, the only dependency

# scan: minutes, ~$0.01-$0.02 (Cost Explorer; see "What a scan costs")
python3 aws_cleanup.py --all-regions --profile <your-profile>

# re-render the last scan: free, instant, no AWS calls
python3 aws_regenerate_report.py
```

Results go to `./Output/results/`. Start with `aws_inventory.html`. The same
data is in `aws_inventory.csv` and `aws_inventory.json`. The architecture diagram
is in `aws_resource_graph.mmd` (Mermaid) and `aws_architecture.drawio` (opens in
draw.io, fully editable), plus one of each per project. `grouping_audit.md`
explains how resources were grouped and lists anything the scan could not read.
`aws_scan.json` is the saved scan that `aws_regenerate_report.py` renders from.

Run `python3 aws_cleanup.py --help` to see the flags.

### Projects and their names

A **project** is a set of resources that belong together. It has an **ID**
(`project_id`) and a **name** (the `project_group` column). The terms below are
used the same way everywhere.

| Term | Meaning |
|---|---|
| Project source | What put the project together: an **AWS tag** (`Project`, `App` or `Application`), a **name rule**, a **deployment** (untagged resources joined by ownership links), or **hand-picked** resources. |
| Name | **Default** (the tag value, the rule's name, or a member's name) or **assigned** by you. Any project can be given a name, whatever its source. |
| Grouping method | Why one resource is in its project: `tag`, `rule`, `inferred` (linked to a member), `deployment`, or `named`. |
| Saved name | An entry in `aws_project_groups.json`: a name plus the resources the project had. On each run it is applied to whichever project holds at least 40% of those resources that still exist. A saved name that matches no project is reported. |

"group" and "cluster" mean AWS resource types only (security group, EKS
cluster). Assigning a name has no flag on purpose: you can only choose a name
after seeing a run. Use the web app's Projects tab, or add an entry to
`aws_project_groups.json` seeded with one `<service>:<region>:<resource_id>`
member key; the next run claims that member's project and fills in the rest.

To add resources to a project, select them in the Resources tab and use **Add to
existing project…**. To combine two projects, use **Merge into…** on the
Projects tab. Forgetting a project's name undoes both.

### Configuration: `audit_config.json`

Optional, read from the current directory. Every key is optional. An invalid
file stops the run before any AWS call.

```json
{
  "output_dir": "./Output",
  "snapshot_file": "aws_scan.json",
  "connection_types": {"amplify.apigateway.name-match": "off"},
  "authoritative_only": false,
  "graph_in_html": true,
  "bubbles_in_html": true,
  "bubbles_default_metric": "resource_count",
  "assume_role": false,
  "role_name": "AWS-audit-role"
}
```

- `output_dir`, `snapshot_file`: where results go. `snapshot_file` must be a
  plain `.json` filename, not a path.
- `connection_types`: set individual link types to `on`, `off` or
  `report-only`. Keys are ids or wildcards; `--debug` prints the full list.
- `authoritative_only`: group resources only on links read directly from AWS,
  never on inferred ones.
- `graph_in_html`: show the Diagram view in the HTML report.
- `bubbles_in_html`, `bubbles_default_metric`: show the overview chart, and
  which metric it opens on (`resource_count`, `unused_pct`, `unused_count` or
  `cost`).
- `assume_role`, `role_name`: assume this IAM role before scanning. See
  [`iam/README.md`](iam/README.md).

Changing `graph_in_html` or the `bubbles_*` keys needs only
`aws_regenerate_report.py`, not a new scan.

### Web app

```bash
docker compose up --build
open http://127.0.0.1:8080
```

Read-only against AWS (tagging is not exposed and cannot be requested), one
scan at a time, no authentication, bound to `127.0.0.1`. Credentials arrive as
a single read-only Docker secret sourced from `AUDIT_AWS_CREDENTIALS_FILE` on
the host (default `~/.aws-audit/credentials`); the app will not start without
that file, and changing it needs `up --force-recreate` rather than `up`.

No AWS config file reaches the container, so a profile there has no default
region. Before the first scan a blank Regions box scans `us-east-1`; after it,
the box is filled in with the regions that scan found resources in.

## Why trust this with your AWS account?

Handing a script `sts:AssumeRole` or IAM credentials is a real ask, so here's
what's true about this one and how to verify it yourself rather than take our
word for it:

- **Read-only by construction, not by promise.** Every AWS call is
  `Describe*`/`List*`/`Get*` — inventory reads, nothing that creates,
  modifies, or deletes a resource. The one exception is opt-in and impossible
  to trigger by accident: `--tag-groups` writes a `Project=<name>` tag, and
  even that runs in dry-run mode unless you also pass `--confirm`. Verify it
  yourself: `aws_resource_audit/collect/` (everything that talks to AWS
  during a scan) contains no `tag_resources`/`create_*`/`delete_*`/`put_*`/
  `modify_*`/`update_*` call at all — the only such call in the whole package
  is `tag_resources` in [`naming/tagging.py`](aws_resource_audit/naming/tagging.py),
  which `run()` reaches only from `--tag-groups`.
- **A least-privilege IAM role ships with the repo**: its permission list is
  exactly the read actions the collectors call — not the AWS-managed
  `ReadOnlyAccess` policy, which covers every AWS service. Deploy
  [`iam/aws-audit-role.yaml`](iam/aws-audit-role.yaml) and the identity you scan
  with never holds more than that; see [`iam/README.md`](iam/README.md) for the
  two-identity setup (a bare `sts:AssumeRole`-only base identity, and the narrow
  role it assumes). `--tag-groups --confirm` deliberately fails through this
  role — the role backing routine scans can't write, on purpose.
- **No network calls except to AWS's own APIs.** No telemetry, no phone-home,
  no third-party endpoint. `aws_resource_audit/collect/` is the only place
  that talks to the network, and it only talks to `boto3`.
- **No dependency beyond `boto3`** for the CLI — nothing else is on the
  network's attack surface or in the supply chain for the path that actually
  touches your account. (The optional web app adds FastAPI/React, but it's
  the same read-only core underneath, still bound to `127.0.0.1` with no
  auth exposed beyond your machine.)
- **A test suite that makes no AWS calls at all**, entirely against mock data
  (`tests/`, see [`tests/README.md`](tests/README.md)) — you can read exactly
  what the code is asserted to do without ever pointing it at a real account.
- **The scan output stays local.** `aws_scan.json` and the rendered reports
  are the whole account's inventory and are `.gitignore`d — nothing is
  uploaded anywhere by this tool.

If you'd rather not take even a read-only role's word for it, run it once
under a profile with no permissions at all — every failed call is reported
explicitly (on the console and in the "Scan coverage" section of
`grouping_audit.md`) rather than silently swallowed, so you can see exactly
what it tried to read.

## What a scan costs

Each scan makes one or two Cost Explorer requests, about $0.01 each. With
`--no-cost` it makes none, and the report shows "not queried" wherever a cost
would be.

## Where the money went that the report will not attribute

A scan attributes a bill to resources only when it can do so honestly: the
charge is attributed to a region, every resource type billing into that category
was enumerated completely there, and the category covers nothing this tool does
not collect. Otherwise the money stays in an **unallocated** bucket, listed in
`grouping_audit.md` with the reason. Attributed plus unallocated always
reconciles to the total.

## What you are paying for that the report does not cover

`grouping_audit.md`, the top of the HTML report, and the web app all carry the
same section, which answers one question: **is AWS charging me for anything
that is not in this report?** Every billed service is checked against what this
tool collects and against what the scan managed to read, and anything that does
not line up is listed as one of four things:

| Finding | What it means for you |
|---|---|
| Charged for, and this tool does not scan it at all | No collector exists. Whatever you have there is missing from the inventory completely — check the AWS console. |
| Charged for, and the scan was not allowed to look | A lookup was denied or failed. Grant the permission (`iam/aws-audit-role.yaml`) and re-scan. |
| Charged for, but the scan found none of them | Scanned, none found. It may be deleted, in a region this scan skipped, or a type the collector misses. |
| Charged for more than this tool collects under that name | AWS groups several kinds of resource under one billing name. Where AWS provides a usage-type breakdown, it is shown, and the lines marked NOT SCANNED are the ones with no resource behind them. |

Each finding says how many resources of each type the report actually holds,
so "AWS charged me and this table is empty" is visible rather than inferred.

## How it is built

Two front ends over one core, and the core knows about neither:

- **The CLI** is two programs joined by a file. `aws_cleanup.py` scans
  AWS and writes `aws_scan.json`; `aws_regenerate_report.py` reads it and writes
  every report with no AWS calls. A scan renders through the same function.
- **The web app** (`backend/` FastAPI + `frontend/` React, in Docker) serves the
  same snapshot. Wherever Python already decides something — a tier, a colour, a
  diagram — the web app asks the backend rather than re-deriving it.

Inside `aws_resource_audit/`, three layers run one way only:
`collect/` (everything that talks to AWS) → `analyze/` (what the facts mean, no
AWS) → `present/` (rendering only). `tests/test_layers.py` enforces the
direction, and nothing in the package imports the web stack, so the CLI runs
with only boto3 installed. New AWS coverage goes in `collect/resources/`, and
any new AWS action it calls must be added to
[`iam/aws-audit-role.yaml`](iam/aws-audit-role.yaml).

Tests make no AWS calls: see [`tests/README.md`](tests/README.md).

## Status and contributing

A personal tool, built for auditing my own account and shared in case it helps
someone else. Issues and PRs are welcome, but there is no support commitment —
read the code before you run it, as you should for anything that touches your
cloud credentials. Please never attach real scan output to an issue: it is a map
of your account. Security reports: see [SECURITY.md](SECURITY.md).

Licensed under the [MIT License](LICENSE).
