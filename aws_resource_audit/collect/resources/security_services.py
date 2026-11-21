"""GuardDuty, Security Hub, Inspector, Macie and Detective: account-level services that
bill per region from the moment they are enabled. Not enabled means no row."""

import logging
import re

from ... import coverage
from ...config import RETRY_CONFIG
from ...collect.calls import paged, parse_aws_ts_string, safe_call
from ...rows import error_row, new_row
from ...staleness import days_ago
from ...text import join_nonempty
from .aws_config import BILLS_WHILE_ENABLED

logger = logging.getLogger(__name__)

ENABLED_FLAG = "ACTIVE (enabled - bills while on)"
_NOTES = "Bills while enabled in this region. No usage signal: the enabled state is the evidence. "
_ON = ("ENABLED", "ENABLING")
_SUSPENDED = ("SUSPENDED", "SUSPENDING", "DISABLING")


def _row(service, region, rid, name, created, flag, notes, **kwargs):
    hint = BILLS_WHILE_ENABLED if flag == ENABLED_FLAG else None
    row = new_row(service, region, rid, name, created, None, days_ago(created), True,
                  flag, notes, cost_hint=hint, **kwargs)
    return row


def collect_guardduty_detectors(session, region):
    client = session.client("guardduty", region_name=region, config=RETRY_CONFIG)
    ids, page_error = paged(client, "list_detectors", "DetectorIds", service="GuardDutyDetector")
    if page_error and not ids:
        return [error_row("GuardDutyDetector", region, "ERROR", page_error)]
    rows = []
    for detector_id in ids:
        detail = safe_call(client.get_detector, DetectorId=detector_id)
        if "__error__" in detail:
            logger.warning("GuardDuty detector %s in %s: details unreadable, status UNKNOWN - %s",
                           detector_id, region, detail["__error__"])
            rows.append(_row("GuardDutyDetector", region, detector_id, detector_id, None, "UNKNOWN",
                             f"{_NOTES}Detector details could not be read: {detail['__error__']} "))
            continue
        enabled = detail.get("Status") == "ENABLED"
        features = [f["Name"] for f in detail.get("Features") or [] if f.get("Status") == "ENABLED"]
        rows.append(_row(
            "GuardDutyDetector", region, detector_id, detector_id,
            parse_aws_ts_string(detail.get("CreatedAt")),
            ENABLED_FLAG if enabled else "STALE (suspended - not billing)",
            _NOTES + "Billed per volume of logs and events analysed. ",
            tags=dict(detail.get("Tags") or {}),
            description=join_nonempty([f"{len(features)} feature(s) on"] + features, ", ")))
    return rows


def _standard_name(arn):
    # arn:aws:securityhub:<region>::standards/<name>/v/<version>, or ruleset/<name>/... for CIS 1.2
    parts = (arn or "").split(":")[-1].split("/")
    return parts[1] if len(parts) > 1 else arn


def collect_security_hub(session, region):
    client = session.client("securityhub", region_name=region, config=RETRY_CONFIG)
    hub = safe_call(client.describe_hub, capability=coverage.INVENTORY, absent_when=(
        ("InvalidAccessException", "not subscribed"), ("ResourceNotFoundException", "")))
    if "__error__" in hub:
        if hub.kind == coverage.COMPLETE:
            return []
        return [error_row("SecurityHubHub", region, "ERROR", hub["__error__"])]
    arn = hub.get("HubArn", "")
    standards, std_error = paged(client, "get_enabled_standards", "StandardsSubscriptions",
                                 capability=coverage.CONFIGURATION, service="SecurityHubHub")
    notes = _NOTES + "Billed per security check and finding ingested. "
    if std_error:
        logger.warning("Security Hub in %s: enabled standards unreadable - %s", region, std_error)
        notes += f"Enabled standards could not be read: {std_error} "
    row = _row("SecurityHubHub", region, arn, "Security Hub",
               parse_aws_ts_string(hub.get("SubscribedAt")), ENABLED_FLAG, notes,
               description=join_nonempty([f"{len(standards)} standard(s)"] + sorted(
                   _standard_name(s.get("StandardsArn")) for s in standards), ", "),
               arn=arn)
    return [row]


def _inspector_flag(status, enabled_types):
    if status in _ON or enabled_types:
        return ENABLED_FLAG
    if status in _SUSPENDED:
        return "STALE (suspended - not billing)"
    return None


def collect_inspector(session, region):
    client = session.client("inspector2", region_name=region, config=RETRY_CONFIG)
    resp = safe_call(client.batch_get_account_status, capability=coverage.INVENTORY)
    if "__error__" in resp:
        return [error_row("InspectorAccount", region, "ERROR", resp["__error__"])]
    rows = []
    for account in resp.get("accounts") or []:
        status = (account.get("state") or {}).get("status", "")
        scans = account.get("resourceState") or {}
        enabled_types = sorted(k for k, v in scans.items() if (v or {}).get("status") in _ON)
        flag = _inspector_flag(status, enabled_types)
        if flag is None:
            continue  # never enabled, or fully disabled: nothing bills
        rows.append(_row(
            "InspectorAccount", region, "inspector", "Inspector", None, flag,
            _NOTES + "Billed per instance, image and function scanned each month. ",
            description=join_nonempty([status] + enabled_types, ", "),
            account=account.get("accountId") or None))
    return rows


def collect_macie(session, region):
    client = session.client("macie2", region_name=region, config=RETRY_CONFIG)
    resp = safe_call(client.get_macie_session, capability=coverage.INVENTORY, absent_when=(
        ("AccessDeniedException", "not enabled"), ("ResourceNotFoundException", "")))
    if "__error__" in resp:
        if resp.kind == coverage.COMPLETE:
            return []
        return [error_row("MacieAccount", region, "ERROR", resp["__error__"])]
    enabled = resp.get("status") == "ENABLED"
    row = _row("MacieAccount", region, "macie", "Macie", resp.get("createdAt"),
               ENABLED_FLAG if enabled else "STALE (paused - not billing)",
               _NOTES + "Billed per bucket monitored and per GB inspected. ",
               description=resp.get("status", ""))
    return [row]


def collect_detective_graphs(session, region):
    client = session.client("detective", region_name=region, config=RETRY_CONFIG)
    graphs, page_error = paged(client, "list_graphs", "GraphList", service="DetectiveGraph")
    if page_error and not graphs:
        return [error_row("DetectiveGraph", region, "ERROR", page_error)]
    rows = []
    for graph in graphs:
        arn = graph["Arn"]
        # arn:aws:detective:<region>:<account>:graph:<id>
        rows.append(_row("DetectiveGraph", region, arn, re.split(r"[:/]", arn)[-1],
                         graph.get("CreatedTime"), ENABLED_FLAG,
                         _NOTES + "Billed per GB of data ingested into the behavior graph. ", arn=arn))
    return rows
