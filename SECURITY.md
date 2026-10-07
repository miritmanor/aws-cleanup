# Security

## Reporting a problem

If you find a security issue — for example a code path that writes to AWS without
`--tag-groups --confirm`, a way the web app could be reached from outside
`127.0.0.1`, or scan data leaking somewhere it should not — please report it
privately through GitHub's **Report a vulnerability** button on the repository's
Security tab, rather than in a public issue.

This is a personal project with no support commitment, but security reports will
be looked at first.

## Never share scan output

Everything a scan writes (`Output/`, `aws_scan.json`, the reports, the raw capture)
is a map of your AWS account: account ids, ARNs, resource and role names. Do not
attach it to issues or pull requests. If you need to show a problem, reproduce it
with the synthetic test corpus in `tests/corpus.py` instead.

## What the tool can touch

- A scan only reads (`Describe*` / `List*` / `Get*`), plus one or two chargeable
  Cost Explorer requests unless you pass `--no-cost`.
- The only write is `--tag-groups --confirm`, which adds `Project=<name>` tags.
- [`iam/aws-audit-role.yaml`](iam/aws-audit-role.yaml) is the least-privilege
  role to scan with; it cannot write at all.
