"""Request and response shapes. ScanRequest is the schema for run()'s duck-typed
options, so a bad field fails at the edge, not minutes into a scan."""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Reject unknown fields instead of dropping them: a typo is a 422 naming the field."""

    model_config = ConfigDict(extra="forbid")


class ScanRequest(StrictModel):
    """The per-run CLI flags, minus tag_groups and confirm: the web app never writes to
    AWS, so those are pinned False below."""

    regions: Optional[list[str]] = Field(
        default=None,
        description="Specific regions to scan. Omit with all_regions=false to "
                    "use the session's default region.")
    all_regions: bool = Field(
        default=False, description="Scan every enabled region.")
    profile: Optional[str] = Field(
        default=None, description="AWS named profile.")
    no_cost: bool = Field(
        default=False,
        description="Skip the Cost Explorer lookup. It runs by default; this "
                    "opts out of the chargeable ce:GetCostAndUsage call "
                    "(~$0.01/request).")
    debug: bool = Field(
        default=False,
        description="Print the resolved connection-type table and the Lambda "
                    "env-var dump into the scan log.")

    @model_validator(mode="after")
    def _regions_and_all_regions_disagree(self):
        """Refuse regions together with all_regions rather than silently preferring one."""
        if self.all_regions and self.regions:
            raise ValueError(
                "pass either regions or all_regions, not both - "
                "all_regions would win and the named regions would be ignored")
        return self

    def to_options(self):
        """The options object run() reads, with the AWS writes pinned off."""
        return ScanOptions(
            regions=self.regions,
            all_regions=self.all_regions,
            profile=self.profile,
            no_cost=self.no_cost,
            debug=self.debug,
        )


class ScanOptions(StrictModel):
    """Exactly the seven attributes run() reads, with the two write flags pinned False."""

    regions: Optional[list[str]] = None
    all_regions: bool = False
    profile: Optional[str] = None
    no_cost: bool = False
    debug: bool = False
    tag_groups: Literal[False] = False
    confirm: Literal[False] = False


class ScanStatus(BaseModel):
    """What the UI polls. Every field may be null before a scan has ever run."""

    state: Literal["idle", "running", "done", "failed", "cancelled"]
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    phase: Optional[str] = None
    stage: Optional[str] = None
    region: Optional[str] = None
    collector: Optional[str] = None
    steps_done: int = 0
    steps_total: int = 0
    error: Optional[str] = None
    # A stop was requested and the scan has not ended yet.
    stopping: bool = False
    result: Optional[dict[str, Any]] = None
    log: list[str] = Field(default_factory=list)


class DefaultRegions(BaseModel):
    """The saved region list for the last scan's account; empty before the first scan."""

    account: str = ""
    regions: list[str] = Field(default_factory=list)
    updated_at: str = ""
    all_regions_scan_at: str = ""


class ScanSummary(BaseModel):
    """The scan's metadata and rows. Rows stay untyped: display_row() defines them, and
    a test checks the keys against ROW_FIELDS."""

    scanned_at: Optional[datetime] = None
    account: str = ""
    regions: list[str] = Field(default_factory=list)
    row_count: int = 0
    rows: list[dict[str, Any]] = Field(default_factory=list)
    # What the scan could and could not see; changes what the rows mean.
    coverage: list[dict[str, Any]] = Field(default_factory=list)


class OverviewResponse(BaseModel):
    """GET /api/overview: overview_payload() served whole; `views` is defined by the core."""

    schema_version: int = 2
    # One entry per view ("service", "project"): items, metrics, packs, lists, notes.
    views: list[dict[str, Any]] = Field(default_factory=list)
    default_view: str = ""
    default_metric: str = ""
    currency: str = ""
    period_start: str = ""
    period_end: str = ""
    cost_queried: bool = False
    scanned_at: str = ""
    account: str = ""


class BilledService(BaseModel):
    """One billed service and how much of it the scan can speak for. Amounts are strings,
    never floats."""

    service: str = Field(description="The Cost Explorer service name.")
    amount: str
    state: Literal["unsupported", "blocked", "not_found", "partial", "covered",
                   "not_a_resource"]
    collected: list[str] = Field(
        default_factory=list,
        description="Resource types this script collects for this bill.")
    gaps: list[str] = Field(
        default_factory=list,
        description="Resource types the same bill covers that it does not. A "
                    "statement about this script, never evidence that such a "
                    "resource exists.")
    row_count: int = 0
    lines: list[str] = Field(
        default_factory=list,
        description="The finding, already worded by present/cost.py: the "
                    "amount, what this report holds, and AWS's own breakdown "
                    "of the charge. Served rather than assembled in the "
                    "client so all three reports say the same thing.")


class BillingSummary(BaseModel):
    """The bill, what the scan could say about it, and the sentences (from present/cost.py)."""

    queried: bool = False
    complete: bool = True
    estimated: bool = False
    error: str = ""
    currency: str = ""
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    observed_at: Optional[datetime] = None
    total: str = "0"
    allocated: str = "0"
    unallocated: str = "0"
    reconciles: bool = True
    below_threshold: int = Field(
        default=0,
        description="Findings not listed because they cost less than a cent. "
                    "Counted so a short list is not mistaken for a filtered "
                    "one.")
    period_note: str = ""
    allocation_note: str = ""
    # The sub-cent findings by name, worded by present/cost.below_threshold_note.
    below_threshold_note: str = ""
    services: list[BilledService] = Field(
        default_factory=list,
        description="Findings a front end may list. Services charged less "
                    "than a cent are already dropped here, so a client may "
                    "filter on state alone.")


class GroupMember(BaseModel):
    member_key: str
    present: bool = Field(
        description="Whether this member is in the current scan. A stored name "
                    "outlives the resources it was given to, deliberately - "
                    "deleting a resource should not forget what the project "
                    "was called.")


class Group(BaseModel):
    id: str
    name: str
    updated_at: Optional[str] = None
    members: list[GroupMember] = Field(default_factory=list)
    live_members: int = 0
    manual: bool = Field(
        default=False,
        description="True if this group's membership is a hand-picked, fixed "
                    "list from a multi-select (see ManualGroupCreate) rather "
                    "than a project the automatic detection found and someone "
                    "named. A manual group never auto-extends.")


class Project(BaseModel):
    """One project, from naming/project_list.py."""
    project_id: str
    name: str
    name_kind: str = Field(description='"default" or "assigned".')
    sources: list[str] = Field(default_factory=list)
    resource_count: int = 0
    group_id: Optional[str] = Field(None, description="Set when the name is assigned.")
    seed_key: Optional[str] = Field(None, description="A member to seed a new name with.")
    manual: bool = False
    saved_members: int = 0
    live_members: int = 0
    updated_at: Optional[str] = None
    not_applied: str = Field("", description="Why a saved name is shown nowhere, or empty.")


class GroupCreate(StrictModel):
    name: str = Field(min_length=1)
    member_key: str = Field(
        min_length=1,
        description="One '<service>:<region>:<resource_id>' key. The next scan "
                    "claims that member's whole project and fills in the rest - "
                    "which is why one is enough.")


class GroupRename(StrictModel):
    name: str = Field(min_length=1)


class ManualGroupCreate(StrictModel):
    """Force two or more specific resources into one new manual group; its membership
    never grows on its own."""

    name: str = Field(min_length=1)
    member_keys: list[str] = Field(
        min_length=2,
        description="Two or more '<service>:<region>:<resource_id>' keys, "
                    "exactly as picked in the table. A single key has an "
                    "existing project to claim - use POST /api/groups instead.")


class GroupMembersAdd(StrictModel):
    """Add resources to a named group, which becomes manual from then on."""

    member_keys: list[str] = Field(min_length=1)


class ConnectionTypeInfo(BaseModel):
    """One connection type as the config editor shows it: `state` is what will run,
    `default_state` what the registry would do left alone."""

    id: str
    kind: str
    default_state: str
    state: str
    source: str
    targets: list[str] = Field(default_factory=list)
    mechanism: str
    confidence: Optional[str] = None
    description: str = ""


class ConfigResponse(BaseModel):
    """The config as written (`raw`) plus what it resolved to (`connection_types`)."""

    raw: dict[str, Any] = Field(default_factory=dict)
    path: str
    exists: bool
    authoritative_only: bool
    graph_in_html: bool
    graph_all_nodes: bool
    bubbles_in_html: bool
    bubbles_default_metric: str
    snapshot_file: str
    output_dir: str
    assume_role: bool
    role_name: str
    connection_types: list[ConnectionTypeInfo] = Field(default_factory=list)


class ProfilesResponse(BaseModel):
    """Profile names for the Scan panel, and the file they came from."""

    profiles: list[str] = Field(default_factory=list)
    path: str
    exists: bool


class ConfigUpdate(StrictModel):
    """A whole new audit_config.json (connection_types apply in file order). output_dir
    is absent and rejected: the mounted volume decides it."""

    connection_types: dict[str, Literal["on", "report-only", "off"]] = Field(
        default_factory=dict)
    authoritative_only: bool = False
    graph_in_html: bool = True
    graph_all_nodes: bool = False
    # Carried through because PUT replaces the whole file.
    bubbles_in_html: bool = True
    bubbles_default_metric: str = "resource_count"
    snapshot_file: str = "aws_scan.json"
    assume_role: bool = False
    role_name: str = "AWS-audit-role"


class ErrorResponse(BaseModel):
    detail: str


class UploadedDocument(BaseModel):
    """One document in the agent's corpus. `chunks` None = not indexed since it arrived;
    0 = indexed but nothing readable."""

    name: str
    size: int
    uploaded: datetime
    chunks: Optional[int] = None


class DocumentUploadResult(BaseModel):
    """The corpus after an upload, and which names it replaced."""

    documents: list[UploadedDocument]
    replaced: list[str]


class AgentQueryRequest(StrictModel):
    """One agent turn. `session_id` is a client-held conversation key (no auth);
    `resource_keys` are member keys in scope (empty = none)."""

    session_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    resource_keys: list[str] = Field(default_factory=list)


class AgentStatus(BaseModel):
    """Whether the agent can answer, and why not (a sentence) when it cannot."""

    available: bool
    reason: str = ""
