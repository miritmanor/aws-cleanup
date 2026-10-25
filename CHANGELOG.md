# Changelog

## Unreleased

- Every resource type's ID now links to its page in the AWS console, in the web app and the HTML report. Before, about 60 types added after the first release (Route 53 hosted zones, VPCs, SQS queues, ECS services and others) showed the ID as plain text. A few types with no addressable detail page (ElastiCache, Network Firewall, Route 53 Resolver endpoints, X-Ray) link to their service's list page. Rows for failed lookups no longer link to a resource named "ERROR". Regenerate or reload to see it; no re-scan needed.
- The web app can be deployed to AWS (ECS Fargate) in your own account, from a release's files with no clone or build, or from source with `deploy/ecs/ecs-up.sh`. It is reached through an encrypted tunnel at `http://127.0.0.1:8080` and has no public address. Every resource is tagged `Project=aws-cleanup`. See `deploy/ecs/README.md`.
- The web app's agent is now off by default. To use it, set `AUDIT_AGENT_PROVIDER` (`openai`, `anthropic` or `bedrock`) in `.env`, then `docker compose up --build`.
- The agent no longer sends account ids, resource ids, ARNs, names, tag values, project names, IP addresses or email addresses to the LLM provider. Each is replaced by a token such as `RES-3f9a…`, and the reply you read shows the real values again. The key behind the tokens is `agent_pseudonym.key` in the data volume. This includes your question and passages from uploaded documents.
- `iam/aws-audit-role.yaml` now names every action exactly, with no wildcards. It no longer grants `cloudtrail:ListTrails`, `cloudwatch:ListMetrics` or `iam:GenerateCredentialReport`, which nothing called. Redeploy the stack to apply it.
- The web app can run from published images instead of building them: set `AUDIT_BACKEND_IMAGE` and `AUDIT_FRONTEND_IMAGE` in `.env` (full image references with tag), then `docker compose pull && docker compose up`. Without them, `docker compose up --build` works as before; the images are now tagged `aws-cleanup-backend:local` and `aws-cleanup-frontend:local`.
- The agent no longer fails on newer OpenAI models (e.g. `gpt-5.6-luna`) with "Function tools with reasoning_effort are not supported". OpenAI requests now use the Responses API, and its replies show as text instead of "[object Object]". Rebuild the image to pick this up.
- The Graph tab is removed from the web app and the HTML report. Use the Architecture tab (Diagram in the report) for the application's shape and the Connections column for every link. The `graph_all_nodes` setting no longer does anything: delete it from `audit_config.json` (a file that still has it loads with a warning). `graph_in_html` now turns the report's Diagram view on or off.
- The CLI's scan command is renamed from `aws_resource_audit.py` to `aws_cleanup.py`. Flags are unchanged.
- The architecture diagram now shows a CloudWatch log group when it forwards events (a subscription filter to Lambda, Kinesis, Firehose or OpenSearch), with "logs to" and "forwards to" lines. Log groups that only receive logs are still left out. Regenerate or reload to see it; no re-scan needed.
- Reports, the snapshot and the config files are now always read and written as UTF-8. Before, the CLI on Windows used the local code page, which could fail on non-ASCII names.
- A resource on a bill that cannot be split per resource now shows which bill, its total and how many resources share it, e.g. "shared 'EC2 - Other' in us-east-1: 2.47 USD, 7 resources", instead of "unallocated (shared bill)". Its Cost notes give the amount, how AWS splits that bill and which resources share it; why it is not divided is in the "Why some resources show no cost" section. The web app's Resources tab now has the Cost notes column. Regenerate or reload to see it; no re-scan needed.
- In the Resources tab, "Add to existing project…" now lists every project, including ones you have not named. Adding resources no longer stops the project from picking up new resources on later scans.
- Projects can be merged in the web app: use "Merge into…" on the Projects tab. The merged project keeps growing on later scans. Forget its name to split the projects apart again. Merges also show in the CLI's reports.
- The web app and the HTML report are now titled "AWS Cleanup" and show a new icon, which is also the browser tab icon.
- S3 buckets are recorded in their own region instead of `global`. Each region's S3 charge is now split across the buckets there (an estimated average, not a measured figure). Links to a bucket still resolve from any region, and saved project names carry over. Re-scan to see it: an old snapshot keeps its buckets as `global`.
- The agent's provider is now chosen when the image is built: `AUDIT_AGENT_PROVIDER` in `.env` (`openai`, `anthropic`, `bedrock`, or `none` to build without the agent), then `docker compose up --build`. `agent_config.json` no longer accepts `provider` or `embedding_model`; remove them. Only the chosen provider is installed, and torch is gone, so the image is much smaller.
- Uploaded documents must be indexed again once after updating: they show "not indexed" until the next upload or delete in the Documents panel.
- The web app's Regions box no longer shows `us-east-1 eu-west-1` as an example when empty. It now shows the regions a blank box will actually scan. `--regions` help in the CLI now states the default.
- `docker compose up` no longer fails the first time with nginx's `host not found in upstream "backend"`. The frontend now waits until the backend is healthy.

## 0.1.0: initial public release

- Read-only inventory of AWS resources across regions, with each service's "last used" signal.
- Inferred projects, stale and orphaned cleanup candidates, and a risk-if-removed estimate.
- Cost Explorer spend attributed to resources where the bill allows it.
- Reports: HTML, CSV, JSON, Mermaid, draw.io architecture diagram, grouping self-review.
- A CLI (`aws_resource_audit.py`, `aws_regenerate_report.py`) and a local web app (`docker compose up`).
- A least-privilege IAM role for scanning (`iam/aws-audit-role.yaml`).
