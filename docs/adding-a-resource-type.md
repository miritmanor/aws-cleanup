# Adding a resource type

Every new AWS resource type follows the same steps. The coverage guards in
`tests/` fail until each one is done.

1. **Collector** — `collect_<thing>(session, region)` returning rows via
   `new_row()`, wrapped so a `ClientError`/`BotoCoreError` produces `error_row()`
   rather than an empty list. Use a paginator when the API has one. Put it in
   the matching `collect/resources/<family>.py`; a new family only earns its own
   module at ~90+ lines. Always pass `tags=` (via `tags_to_dict`) **and**
   `description=` when the API response carries either. Not every service calls
   it `Description` (`AlarmDescription`, `Comment`, `TemplateDescription`), so
   check the actual response shape: this was missed for AMI and CloudWatchAlarm
   the first time, silently dropping user-written text that grouping relies on.
2. **Registrations** — export it from `collect/resources/__init__.py` and add it
   to `COLLECTORS_PER_REGION` (or `COLLECTORS_GLOBAL`), respecting
   `validate_collector_order()` if it touches the shared EC2 usage registry. In
   `config.py`: `SERVICE_BILLING` and exactly one of `STRUCTURAL_SERVICES` /
   `SUPPORTING_SERVICES`; in `billing_tables.py`, `CE_SERVICE_MAP` if Cost
   Explorer reports it. Every type needs a `SERVICE_META` entry in
   `present/html/service_meta.py`: its `category` must be the one AWS gives the
   service in `tools/aws_service_coverage.csv` (`ReportCategoryTests` fails
   otherwise), and its colour the one that category already uses. **That
   category also decides the structural/supporting split**: Compute, Storage,
   Databases, Application Integration and Front-End Web & Mobile are
   structural, the rest is not, and `StructuralCategoryTests` fails until the
   two agree. Genuine exceptions go in that test's override sets with a reason.
3. **Golden corpus** — add the type to `ALL_SERVICES` and `_BASELINE` in
   `tests/corpus.py`, then regenerate with `UPDATE_GOLDEN=1` and read the diff.
   Two guards fail until you do, on purpose: a collector that renders into
   nothing would otherwise leave every golden passing while checking less.
4. **ARN mapping** — extend `_resource_id_and_service_from_arn` in
   `collect/arns.py` and `ARN_RESOLVABLE_TARGETS` in
   `connection_types/vocabulary.py`. Every ARN-driven mechanism (IAM grants,
   Lambda env vars, log-group subscription filters) then reaches the new type.
5. **Connection types** — one `ConnectionType` entry in
   `connection_types/<family>.py` per detection mechanism, and `conn_type=` on
   each `add_edge` call. Heuristics must default to `report-only`; the registry
   test enforces this.
6. **Architecture diagram** — drawn types go in `ARCHITECTURE_SERVICES`
   (`analyze/architecture.py`), `SERVICE_ICONS` (`present/graph/icons.py`;
   re-run `tools/build_aws_icons.py` for a new icon), `DRAWIO_SHAPES`
   (`present/graph/drawio.py`) and `COLUMN_OF`
   (`present/graph/drawio_layout.py`). A new runtime connection type needs
   `semantics` of `invokes` or `reads_or_writes`, and a `VERBS` entry if
   "invokes"/"uses" says too little. Deployment-only types go in
   `DEPLOYMENT_SERVICES` (`analyze/tiers.py`). `ArchitectureIconTests` fails if
   a drawn type has no icon.
7. **IAM** — grant every new AWS action by exact name in
   `iam/aws-audit-role.yaml`. `tests/test_iam_role.py` fails otherwise. Without
   it a scan passes under `ReadOnlyAccess` and reports the type as `ERR` under
   the deployed role.
8. **Tests** — one collector test in its family file, one test per new
   connection type (asserted in all three states), and the type in
   `ALL_SERVICES` plus the denial-handling case list.

Add new public names to the explicit re-export list in `__init__.py` too;
anything omitted is absent from `audit.<name>` and the tests will say so.
