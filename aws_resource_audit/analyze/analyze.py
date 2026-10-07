"""The analysis phase, in order: resolve_edges -> apply_tiers -> grouping -> build_graph_data
(POPS row["_graph"], so it runs once, last) -> attribute_costs. No AWS calls."""

from dataclasses import dataclass, field

from ..billing import not_queried
from .billing_coverage import assess_billing_coverage
from .active_regions import find_active_regions
from .cost import attribute_costs
from .default_network import hide_untouched_default_networks
from .graph import build_graph_data
from .grouping import apply_project_grouping
from .references import resolve_edges
from .tiers import apply_tiers


@dataclass
class AnalysisResult:
    """Everything the analysis phase concluded, as values; run() does the narration."""

    edge_links: list = field(default_factory=list)
    dangling_count: int = 0
    tier_counts: dict = field(default_factory=dict)
    grouping_notes: dict = field(default_factory=dict)
    graph_data: dict = field(default_factory=dict)
    # The bill attributed (cost_report) and compared against what was scanned.
    cost_report: object = None
    billing_coverage: object = None
    # Regions whose untouched default VPC was left out of the rows.
    hidden_default_networks: list = field(default_factory=list)
    # Which scanned (and billed) regions hold anything - an ActiveRegions.
    active_regions: object = None


def run_analysis(normalized_scan_results, *, default_vpc_ids=None,
                 default_vpcs_known=True, coverage=None, name_rules=None,
                 billing=None, regions=(), report=None, step=0, total=0):
    """Resolve, tier, group, graph and attribute cost; rows are mutated in place.
    default_vpcs_known=False suppresses VPC grouping; `billing` says whether it was queried."""
    def _report(**event):
        if report is not None:
            report(**event)

    coverage_entries = coverage.as_dicts() if hasattr(coverage, "as_dicts") else (coverage or [])
    # First, so no later pass links, groups or prices a row that is not shown.
    hidden_default_networks = hide_untouched_default_networks(
        normalized_scan_results, default_vpc_ids, coverage_entries,
        known=default_vpcs_known)

    _report(phase="analyze", stage="references", step=step, total=total)
    edge_links = resolve_edges(normalized_scan_results, coverage=coverage)
    dangling_count = sum(1 for r in normalized_scan_results if r.get("_has_dangling"))

    _report(phase="analyze", stage="tiers", step=step, total=total)
    tier_counts = apply_tiers(normalized_scan_results)

    _report(phase="analyze", stage="grouping", step=step, total=total)
    grouping_notes = apply_project_grouping(
        normalized_scan_results, edge_links=edge_links,
        default_vpc_ids=default_vpc_ids,
        default_vpcs_known=default_vpcs_known,
        name_rules=name_rules)

    _report(phase="analyze", stage="graph", step=step, total=total)
    graph_data = build_graph_data(
        normalized_scan_results, grouping_notes.get("grouping_links"))

    _report(phase="analyze", stage="cost", step=step, total=total)
    billing = billing if billing is not None else not_queried()
    cost_report = attribute_costs(normalized_scan_results, billing,
                                  coverage_entries=coverage_entries)
    billing_coverage = assess_billing_coverage(
        billing, coverage_entries=coverage_entries, rows=normalized_scan_results)
    active_regions = find_active_regions(normalized_scan_results, list(regions),
                                         coverage_entries, billing)

    return AnalysisResult(
        edge_links=edge_links,
        dangling_count=dangling_count,
        tier_counts=tier_counts,
        grouping_notes=grouping_notes,
        graph_data=graph_data,
        cost_report=cost_report,
        billing_coverage=billing_coverage,
        hidden_default_networks=hidden_default_networks,
        active_regions=active_regions,
    )
