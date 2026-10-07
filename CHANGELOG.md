# Changelog

## Unreleased

- S3 buckets are recorded in their own region instead of `global`. Each region's S3 charge is now split across the buckets there (an estimated average, not a measured figure). Links to a bucket still resolve from any region, and saved project names carry over. Re-scan to see it: an old snapshot keeps its buckets as `global`.

## 0.1.0: initial public release

- Read-only inventory of AWS resources across regions, with each service's "last used" signal.
- Inferred projects, stale and orphaned cleanup candidates, and a risk-if-removed estimate.
- Cost Explorer spend attributed to resources where the bill allows it.
- Reports: HTML, CSV, JSON, Mermaid, draw.io architecture diagram, grouping self-review.
- A CLI (`aws_resource_audit.py`, `aws_regenerate_report.py`) and a local web app (`docker compose up`).
- A least-privilege IAM role for scanning (`iam/aws-audit-role.yaml`).
