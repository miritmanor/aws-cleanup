"""The scan: collect from AWS, analyse, write the snapshot, then render through it.
Settings and progress may be passed in for non-CLI callers; argument parsing lives outside."""

import logging
import json
import os
from datetime import datetime, timezone

from . import config, console
from .collect import raw_capture
from .collect.regions import get_regions
from .collect.collect import collect_all, total_collection_steps
from .collect.session import authenticated_session
from . import coverage
from .config import COST_LOOKBACK_DAYS
from .present.regions import region_summary_lines
from .region_store import load_active_regions, record_active_regions
from .rows import partition_from_arn
from .scope import DEFAULT_PARTITION, set_scan_scope
from .connection_types.vocabulary import CONN_OFF, CONN_REPORT_ONLY
from .registry import format_connection_type_table, resolve_connection_states, set_connection_states
from .analyze.analyze import run_analysis
from .analyze.cost import total_spend
from .naming.group_names import (
    apply_manual_group_overrides,
    apply_assigned_names,
    load_name_rules,
)
from .naming.tagging import apply_group_tagging
from .render import render_outputs
from .settings import load_settings, output_paths
from .snapshot import read_snapshot, write_snapshot
from .console import say

logger = logging.getLogger(__name__)


def _write_debug_json(path, payload):
    """A best-effort runtime/ debug dump: a failed write must never lose a finished
    scan. default=str, since nothing reads it back."""
    try:
        with open(path, "w") as handle:
            json.dump(payload, handle, indent=2, default=str)
    except (OSError, TypeError, ValueError) as e:               # noqa: BLE001
        # console.warn already mirrors the message into logging; this adds the
        # traceback, which only DEBUG wants.
        logger.debug("writing %s failed", path, exc_info=True)
        console.warn(f"could not write {os.path.basename(path)} ({e}); continuing")


def _say_cost(billing, analysis):
    """The cost line of the closing summary: not queried, failed, incomplete and clean
    are four different statements, never one bare total."""
    if not billing.queried:
        say("Cost: Cost Explorer not queried (turned off for this scan) - no figure below is "
            "a claim that anything is free")
        return
    if billing.error and not billing.amounts:
        say(f"Cost: Cost Explorer unavailable ({billing.error}) - cost is "
            "unknown for every resource, which is not the same as zero")
        return

    total = total_spend(billing)
    caveats = [word for word, on in (("estimated by AWS", billing.estimated),
                                     ("INCOMPLETE response", not billing.complete))
               if on]
    say(f"Account spend, last {COST_LOOKBACK_DAYS}d: {total:.2f} "
        f"{billing.currency or 'USD'}"
        + (f" ({', '.join(caveats)})" if caveats else ""))
    report = analysis.cost_report
    if report is not None:
        say(f"       {report.allocated:.2f} attributed to resources, "
            f"{report.unallocated:.2f} held unallocated"
            + ("" if report.reconciles() else " - THESE DO NOT RECONCILE"))
    gaps = analysis.billing_coverage
    if gaps is not None and gaps.has_findings:
        say("       Billing coverage: "
            + ", ".join(f"{len(gaps.in_state(state))} {state}"
                        for state in ("unsupported", "incomplete", "partial")
                        if gaps.in_state(state))
            + " - services the scan does not cover")


def run(args, *, settings=None, progress=None, should_stop=None):
    """Scan AWS, analyse, write the snapshot, render; returns the Snapshot. `progress`
    gets one dict per step; `should_stop` ends the scan before any file is written."""
    # Read before authenticating, so a bad audit_config.json fails instantly.
    settings = settings if settings is not None else load_settings()
    paths = output_paths(settings)

    # A failing progress callback is reported, never allowed to lose the scan.
    def _report(**event):
        if progress is None:
            return
        try:
            progress(event)
        except Exception as e:                          # noqa: BLE001
            # The broadest catch in the package - it is swallowing a bug in
            # someone else's callback, so the traceback is the whole evidence.
            logger.exception("progress callback failed for event %r", event)
            console.warn(f"progress callback failed ({e}); continuing the scan")

    # Set ON the owning module; a local assignment would be silently ignored.
    console.VERBOSE = args.debug

    # Pin the clock to the start of the scan.
    config.set_now(datetime.now(timezone.utc))

    states = resolve_connection_states(
        overrides=settings.connection_types,
        authoritative_only=settings.authoritative_only,
    )
    set_connection_states(states)
    if args.debug:
        say(format_connection_type_table(states))

    disabled = sorted(cid for cid, s in states.items() if s == CONN_OFF)
    demoted = sorted(cid for cid, s in states.items() if s == CONN_REPORT_ONLY)
    if disabled:
        say(f"Connection detection: {len(disabled)} type(s) off ({', '.join(disabled)})")
    if demoted:
        say(f"Connection detection: {len(demoted)} type(s) report-only, shown but not grouped "
              f"({', '.join(demoted)})")

    # Base profile, then - if settings.assume_role says so - a role assumed on
    # top of it. See collect/session.py; raises AuditError on either failure.
    session, ident = authenticated_session(args, settings)
    # Before any row is built: identity includes account and partition (aws-cn, aws-us-gov).
    # A fresh ledger per scan, since the web app runs several in one process.
    coverage.reset()
    set_scan_scope(ident.get("Account") or "",
                   partition_from_arn(ident.get("Arn") or "") or DEFAULT_PARTITION)
    say(f"AWS resource audit - account {ident.get('Account')} ({ident.get('Arn', '').rsplit('/', 1)[-1]})")

    account = ident.get("Account") or ""
    saved = (None if (args.regions or args.all_regions)
             else load_active_regions(paths["active_regions"], account))
    regions = get_regions(session, args.regions, args.all_regions,
                          saved=saved and saved["regions"])
    say(f"Regions: {', '.join(regions)}"
        + (f" - the saved list for this account, from {saved['updated_at'][:10]}"
           if saved else ""))

    # Counted, not hard-coded, so a new collector cannot make the progress total wrong.
    total_steps = total_collection_steps(regions)
    step = 0
    _report(phase="start", account=ident.get("Account"), regions=regions,
            step=0, total=total_steps)

    # Collectors write one raw line per resource as they go, so a crash keeps what it had.
    raw_capture.start(paths["raw_capture"])
    try:
        collection = collect_all(session, regions, no_cost=args.no_cost,
                                 report=_report, step=step,
                                 should_stop=should_stop)
    finally:
        raw_capture.close()

    normalized_scan_results = collection.normalized_scan_results
    _write_debug_json(paths["normalized_scan_results"], {
        "captured_at": config.now().isoformat(),
        "rows": normalized_scan_results,
    })

    say()
    # Nothing past here talks to AWS; steps are reported by name.
    analysis = run_analysis(
        normalized_scan_results,
        default_vpc_ids=collection.default_vpc_ids,
        default_vpcs_known=collection.default_vpcs_known,
        coverage=coverage.ledger(),
        name_rules=load_name_rules(paths["groups"]),
        billing=collection.billing, regions=regions,
        report=_report, step=step, total=total_steps)

    gaps = {k: v for k, v in coverage.ledger().counts().items()
            if k != coverage.COMPLETE}
    if gaps:
    # Before the counts: "nothing references this" means less when a collector was denied.
        say("Coverage: " + ", ".join(f"{n} {status}" for status, n in sorted(gaps.items())))
        # The console gets counts; the log gets scope, type, call and error per entry.
        for entry in coverage.ledger().gaps():
            logger.warning(
                "coverage %s: %s %s %s in %s%s",
                entry.status, entry.operation or entry.capability,
                entry.service or entry.collector or "-", entry.capability,
                entry.scope or "global",
                f" - {entry.error.splitlines()[0]}" if entry.error else "")
    else:
        say("Coverage: every lookup completed")

    if analysis.hidden_default_networks:
        hidden = analysis.hidden_default_networks
        say(f"Default VPCs: left out in {len(hidden)} region(s) ({', '.join(hidden)}) - "
            "nothing is in them and they are as AWS created them")

    for line in region_summary_lines(analysis.active_regions,
                                     collection.billing.currency or "USD"):
        say(line)
    # Replaced only by a scan that really listed every enabled region.
    listed_all = args.all_regions and not any(
        e.operation == "describe_regions" and e.status != coverage.COMPLETE
        for e in coverage.ledger().entries)
    stored = record_active_regions(
        paths["active_regions"], account, analysis.active_regions.regions,
        replace=listed_all, observed_at=config.now())
    say(f"Saved as this account's default regions: {', '.join(stored) or 'none'}"
        + ("" if listed_all else " (added to the saved list; none removed)"))

    say(f"Connections: {len(analysis.edge_links)} link(s) resolved"
        + (f", {analysis.dangling_count} resource(s) point outside this scan"
           if analysis.dangling_count else ""))
    if analysis.tier_counts:
        say("Tiers: " + ", ".join(f"{count} {tier}"
                                  for tier, count in sorted(analysis.tier_counts.items())))

    apply_assigned_names(normalized_scan_results, paths["groups"])
    apply_manual_group_overrides(normalized_scan_results, paths["groups"])

    # Cost wording is prose, so present/cost.py writes it at render time; nothing here
    # writes a column.
    _say_cost(collection.billing, analysis)

    if args.tag_groups:
        apply_group_tagging(normalized_scan_results, session,
                            ident.get("Account"), args.confirm)

    # Save before rendering: if a renderer fails, aws_regenerate_report.py can finish.
    _report(phase="snapshot", step=step, total=total_steps)
    write_snapshot(paths["snapshot"], normalized_scan_results,
                   analysis.graph_data, analysis.grouping_notes,
                   ident.get("Account"), regions, config.now(),
                   coverage=coverage.ledger().as_dicts(),
                   billing=collection.billing)

    # Rendered from the file, not memory, so anything the snapshot fails to carry
    # breaks the scan immediately.
    snapshot = read_snapshot(paths["snapshot"])

    _report(phase="render", step=step, total=total_steps)
    render_outputs(snapshot, paths, settings)

    stale_count = sum(1 for r in normalized_scan_results if r["flag"].startswith("STALE"))
    high_risk = sum(1 for r in normalized_scan_results if r["risk_if_removed"].startswith("HIGH"))
    low_risk = sum(1 for r in normalized_scan_results if r["risk_if_removed"].startswith("LOW"))
    cw_failed = sum(1 for r in normalized_scan_results if "Could not read CloudWatch metric" in r["notes"])
    idle_ebs = sum(1 for r in normalized_scan_results if r["service"] == "EBSVolume" and r["flag"] in
                   ("STALE (UNATTACHED)", "STALE (ATTACHED TO STOPPED INSTANCE)"))

    stale_rows = [r for r in normalized_scan_results if r["flag"].startswith("STALE")]
    stale_cost = [r for r in stale_rows if r.get("billing") in ("cost", "indirect")]
    stale_free = [r for r in stale_rows if r.get("billing") in ("free", "usage")]

    # Failed lookups are not rows, so they are counted here: "empty account" differs
    # from "could not look".
    failed_lookups = collection.failed_lookups
    say(f"\n{len(normalized_scan_results)} resources - {stale_count} stale, "
        f"{low_risk} low-risk cleanup candidates, {high_risk} high-risk (leave alone)")
    if stale_cost:
        by_svc = {}
        for r in stale_cost:
            by_svc[r["service"]] = by_svc.get(r["service"], 0) + 1
        detail = ", ".join(f"{n} {s}" for s, n in sorted(by_svc.items(), key=lambda kv: -kv[1])[:4])
        say(f"Costing you money: {len(stale_cost)} stale billable resource(s) - {detail}")
    if stale_free:
        say(f"Tidy-up only (no charge): {len(stale_free)} stale resource(s) - deleting saves clutter, not money")
    if idle_ebs:
        say(f"{idle_ebs} EBS volume(s) billed but idle - unattached, or attached to a stopped instance")
    if failed_lookups and not normalized_scan_results:
        say(f"Nothing was readable: all {failed_lookups} lookup(s) were denied or failed. "
            "This credential is missing read permissions - see iam/README.md. "
            "The report below is empty because nothing was found, not because "
            "nothing is there.")
    elif failed_lookups:
        say(f"{failed_lookups} lookup(s) failed and are not in the report - "
            "the ERR lines above say which, and why")
    if cw_failed:
        say(f"{cw_failed} row(s) had a CloudWatch lookup fail - under-reported, not confirmed idle")
    say("Staleness marked INFERRED comes from the creation date, not real usage. Verify before deleting.")

    _report(phase="done", step=total_steps, total=total_steps,
            rows=len(normalized_scan_results), stale=stale_count,
            errors=failed_lookups)

    # Returned for non-CLI callers; the CLI ignores it.
    return snapshot
