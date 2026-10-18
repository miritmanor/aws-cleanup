# Decisions

One file per architectural or structural decision: context, decision,
consequences. Each record describes the current design; when a decision
changes, its record is updated, and when it no longer applies, it is deleted.

## When to write one

Write a record only for an important architectural or structural choice: a
method, an API, a data model, a storage or database, a deployment option, a
security or privacy boundary, or how the code is divided into parts. It should
be a choice that limits later changes and that someone could plausibly reverse
without knowing why it was made.

Do not write one for:

- product behaviour or a detection rule (what is shown, hidden or flagged);
- a feature, a UI control or an endpoint added for it;
- a refinement or extension of an existing record (edit that record instead);
- tooling and workflow (linters, CI gates, release steps, agent skills).

Reasoning about any of these goes in a short comment at the code it explains,
especially a trap or a rejected alternative that looks like an obvious fix.

## Layout

Records are filed by the part of the project they constrain. Numbers are unique
across all folders; a new record takes the highest number plus one.

| Folder | Covers |
|---|---|
| [project-wide/](project-wide/) | Layers, scan/render split, errors, logging, project identity |
| [collect/](collect/) | Service taxonomy, the audit role and its IAM actions |
| [cost/](cost/) | Attributing the bill to resources |
| [graph/](graph/) | The architecture diagram's draw.io output |
| [report/](report/) | Where the overview's layout is computed |
| [web-app/](web-app/) | Scan concurrency in the backend |
| [agent/](agent/) | The optional LLM agent's packaging and privacy boundary |
| [deploy/](deploy/) | Credentials delivery and the ECS deployment |
