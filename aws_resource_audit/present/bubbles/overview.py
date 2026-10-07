"""The overview payload (both views, circles, lists) that the report embeds and
GET /api/overview serves. Duck-typed on the summaries: present/ may not import analyze/."""

from ...config import now
from .pack import DEFAULT_METRIC, METRIC_IDS, METRICS, pack, pack_all

VIEW_SERVICE = "service"
VIEW_PROJECT = "project"

# There is no per-project cost, so the project view has three.
PROJECT_METRICS = ("resource_count", "unused_pct", "unused_count")
SERVICE_METRICS = METRIC_IDS

PAYLOAD_SCHEMA_VERSION = 2


def _item(summary):
    return {**summary.as_dict(), "id": summary.bubble_id}


def _metrics(ids):
    return [list(m) for m in METRICS if m[0] in ids]


def _service_view(services):
    # Cost circles are bill lines, every other metric is services; the side
    # list shows whichever set the current metric draws.
    def items_for(metric):
        return services.bill_lines if metric == "cost" else services.services
    return {
        "id": VIEW_SERVICE,
        "label": "By service",
        "noun": "service",
        "items": [_item(s) for s in services.services + services.bill_lines],
        "metrics": _metrics(SERVICE_METRICS),
        "packs": {m: [b.as_dict() for b in pack(items_for(m), m)]
                  for m in SERVICE_METRICS},
        "lists": {m: [s.bubble_id for s in items_for(m)] for m in SERVICE_METRICS},
        "notes": list(services.notes),
    }


def _project_view(projects):
    ids = [p.bubble_id for p in projects.projects]
    return {
        "id": VIEW_PROJECT,
        "label": "By project",
        "noun": "project",
        "items": [_item(p) for p in projects.projects],
        "metrics": _metrics(PROJECT_METRICS),
        "packs": pack_all(projects.projects, PROJECT_METRICS),
        "lists": {m: ids for m in PROJECT_METRICS},
        "notes": list(projects.notes),
    }


def overview_payload(projects, services, *, default_metric, scanned_at=None,
                     account=""):
    """Everything either overview panel needs. Opens By project only once a person
    has tagged, named or rule-matched a project."""
    return {
        "schema_version": PAYLOAD_SCHEMA_VERSION,
        "views": [_service_view(services), _project_view(projects)],
        "default_view": (VIEW_PROJECT if projects.has_named_projects
                         else VIEW_SERVICE),
        "default_metric": (default_metric if default_metric in METRIC_IDS
                           else DEFAULT_METRIC),
        "currency": services.currency,
        "period_start": services.period_start,
        "period_end": services.period_end,
        "cost_queried": services.cost_queried,
        "scanned_at": (scanned_at or now()).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "account": account or "",
    }
