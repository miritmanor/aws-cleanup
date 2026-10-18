# 0059 - Project vocabulary, project identity, and every project can be named

Status: accepted

## Context

The code used four words for one thing. A set of resources that belong
together was a "project" (`project_id`), a "group" (`project_group`, the Groups
tab, `/api/groups`, `aws_project_groups.json`), a "cluster" (the naming pass,
the frontend's old `buildClusters()`) and its name was a "label". "group" and
"cluster" are also AWS resource types: security group, log group, Auto Scaling
group, EKS cluster.

Bugs came from the same confusion:

- The naming pass, the frontend's `buildClusters()`, the Resources table, the
  HTML report's table and both graph views grouped rows by the display name.
  Two projects with one name were treated as one, and the naming pass decided
  whether a whole project was tag-derived from its first row only.
- The group store changed only the label. A saved name covering resources from
  three detected projects drew three overview circles, all with that name and
  different counts, while the table, banding by name, showed one.
- Naming was refused for tag projects, silently: `POST /api/groups` answered 201
  and the name never appeared.

## Decision

**Vocabulary.** Used in code, docs and every visible string:

| Term | Meaning |
|---|---|
| Project | Resources that belong together. Has an ID and a name. |
| Project ID | `project_id`. The only thing anything may group, count or join on. |
| Project source | What put a resource's project together: **AWS tag** (`tag:`), **name rule** (`rule:`), **deployment** (`deployment:`, untagged resources joined by ownership links) or **hand-picked**. A named project spanning several detected projects has several. |
| Project tag | An AWS tag with key `Project`, `App` or `Application`, either case. |
| Name | **Default** (tag value, rule name, or a member's name) or **assigned** by the user. |
| Membership | **Detected** (tags, rules, links) or **hand-picked**. |
| Grouping method | Why one resource is in its project: `tag`, `rule`, `inferred`, `deployment`, `named`. |
| Saved name | An entry in `aws_project_groups.json`: a name plus the resources it covered. Matched to a project on each load at 40% overlap. Its list only grows. |

"group" and "cluster" mean AWS resource types only. "label" is not used.

**Everything keys on `project_id`.** Naming, the project list, table banding and
graph boxes group by ID and take the name from the row.

**A saved name is a project identity.** When the store claims a row it sets
`project_id = "group:<gid>"` as well as the name, through one `_claim()` helper,
so neither field can be set without the other. A saved name is therefore one
circle, one per-project diagram and one `.mmd`, however many detected projects
it spans. A saved name that loses its last resource keeps the same id.

**The grouping pass's own answer is kept per row** in `_detected_project_id`,
`_detected_project_group` and `_detected_grouping_method`.
`reset_assigned_names` restores them before the store is re-applied, so
removing a name restores the default name, the original grouping method and the
ID. These are internal fields: stored in the snapshot (`SNAPSHOT_ROW_FIELDS`),
because `run()` writes it after applying the store, and not in `ROW_FIELDS`, so
no output publishes them.

**Every project can be named, whatever its source.** An assigned name applies
to tag and rule projects too. The tag stays on the resource in AWS; only what
this app calls the project changes.

**The project list is Python's.** `naming/project_list.py` builds it and
`GET /api/projects` serves it; the frontend does not derive it.

**A saved name that matches no project is reported**, in the Projects tab and
in the CLI's grouping output.

## Consequences

- Not renamed: the `project_group` field (it holds the project name), the
  `group:` ID prefix, `/api/groups` and `aws_project_groups.json`. They are
  read as "saved names" under this vocabulary.
- `project_id` values starting `group:` appear in the CSV and JSON outputs.
  They are stable across scans for as long as the saved name exists.
- `membership` is unchanged by a claim: a shared resource added to a manual
  project is counted in it but its membership column still reads "shared".
- Hand-picking a tagged resource into another project is still refused. That
  changes membership, which the tag states; naming does not.
- Names are assigned in the web app (the table's project banner and the
  Projects tab). **The generated HTML report has no rename control**: it is a
  static file with no server to write the name store to. This is a stated
  exception to CLI/web-app parity; the CLI's equivalent is the saved-name store
  on disk, applied on the next render.
