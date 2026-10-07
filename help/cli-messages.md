# Scan messages: what they mean and what to do (command line)

A scan and `aws_regenerate_report.py` print what they found and nothing more.
This page says what to do about the messages that need it. The web app has its
own version of this page, linked from its scan log, because the remedies there
are different.

Paths below are the defaults, under `Output/`. If `audit_config.json` sets
`output_dir`, the files are under `<output_dir>/results/` instead; the "Open
the report" list at the end of every run prints the real paths.

## Regions

**No default region configured; defaulting to us-east-1.**
The profile has no region and this account has no saved region list yet. Set
one with `AWS_DEFAULT_REGION` or a `region =` line for the profile in
`~/.aws/config`, name regions with `--regions us-east-1 eu-west-1`, or scan
everything with `--all-regions`.

**Regions: … - the saved list for this account, from DATE**
With no `--regions` or `--all-regions`, a scan covers the regions where earlier
scans of this account found resources (`aws_active_regions.json`). Run with
`--all-regions` to look everywhere.

**Saved as this account's default regions: … (added to the saved list; none removed)**
A scan of named regions only adds to the saved list. Only an `--all-regions`
scan replaces it, so a region you emptied stays on the list until you run one.

## What the scan could not see

**`ERR` next to a resource type**, **Coverage: N denied**, and
**N lookup(s) failed and are not in the report**.
The credential lacks a read permission, or AWS returned an error. The resource
type is left out of the report. The "Scan coverage" section of
`results/grouping_audit.md` lists each failed call. To grant the permissions,
deploy or update the role in [`iam/aws-audit-role.yaml`](../iam/aws-audit-role.yaml)
as [`iam/README.md`](../iam/README.md) describes.

**Billing coverage: N unsupported, … - services the scan does not cover**
The bill has charges for services this tool does not scan. The section "What
you are paying for that this report does not cover" in
`results/grouping_audit.md` names them.

## Cost

**Cost: Cost Explorer not queried (turned off for this scan)**
The scan ran with `--no-cost`. Run without it to get cost figures (about
$0.01 per request).

**Cost: Cost Explorer unavailable (…)**
Usually a missing `ce:GetCostAndUsage` permission; see `iam/README.md`.

## Projects

**N link(s) crossed a project boundary and were NOT merged**
Two projects are linked, but each has its own tag or rule, so they stay
separate. "Links that crossed a project boundary" in
`results/grouping_audit.md` lists them.

**N name(s) could group resources that nothing else connects (…)**
These words appear in several resource names that have no other link between
them. To group by one, add it to `name_rules` in
`results/aws_project_groups.json`, mapping the word to a project name:

```json
{ "name_rules": { "challenge": "Coding challenge" } }
```

"Names that could group things" in `results/grouping_audit.md` lists which
resources each word would join. Name rules apply during a scan, so scan again
afterwards; `aws_regenerate_report.py` does not apply them.

**N project(s) with a default name**
These projects are named after a tag value, a rule or one of their members.
To give one a name, add an entry to `results/aws_project_groups.json`; see
"Projects and their names" in the [README](../README.md#projects-and-their-names).

**Saved name "…" not applied: no project holds 40% of its resources**
The resources that name was saved for have changed too much to match a
project. Edit or remove the entry in `results/aws_project_groups.json`.

## Graph and audit

**N supporting resource(s) contracted away**
IAM roles, log groups, security groups and similar are hidden from the graph
and their links drawn through. Set `"graph_all_nodes": true` in
`audit_config.json` and run `aws_regenerate_report.py` to draw them.

**Audit: N oversized project(s), …**
The grouping pass checking itself. Details are in `results/grouping_audit.md`.

## Changing only how the report looks

Every output file is rendered from `results/aws_scan.json`. After changing
`audit_config.json` settings or saved project names, run
`python3 aws_regenerate_report.py` instead of scanning again: it takes seconds
and makes no AWS calls.
