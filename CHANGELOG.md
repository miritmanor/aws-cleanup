# Changelog

## Unreleased

- S3 buckets are recorded in their own region instead of `global`. Each region's S3 charge is now split across the buckets there (an estimated average, not a measured figure). Links to a bucket still resolve from any region, and saved project names carry over. Re-scan to see it: an old snapshot keeps its buckets as `global`.
- The agent's provider is now chosen when the image is built: `AUDIT_AGENT_PROVIDER` in `.env` (`openai`, `anthropic`, `bedrock`, or `none` to build without the agent), then `docker compose up --build`. `agent_config.json` no longer accepts `provider` or `embedding_model`; remove them. Only the chosen provider is installed, and torch is gone, so the image is much smaller.
- Uploaded documents must be indexed again once after updating: they show "not indexed" until the next upload or delete in the Documents panel.
- `docker compose up` no longer fails the first time with nginx's `host not found in upstream "backend"`. The frontend now waits until the backend is healthy.

## 0.1.0: initial public release

- Read-only inventory of AWS resources across regions, with each service's "last used" signal.
- Inferred projects, stale and orphaned cleanup candidates, and a risk-if-removed estimate.
- Cost Explorer spend attributed to resources where the bill allows it.
- Reports: HTML, CSV, JSON, Mermaid, draw.io architecture diagram, grouping self-review.
- A CLI (`aws_resource_audit.py`, `aws_regenerate_report.py`) and a local web app (`docker compose up`).
- A least-privilege IAM role for scanning (`iam/aws-audit-role.yaml`).
