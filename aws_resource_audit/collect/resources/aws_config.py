"""AWS Config recorders and rules. A recorder bills per configuration item it records and
a rule per evaluation, from the moment they are on; neither has a "last used" signal."""

import logging

from ... import coverage
from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.calls import paged, safe_call
from ...rows import error_row, new_row
from ...text import join_nonempty

logger = logging.getLogger(__name__)

BILLS_WHILE_ENABLED = "bills_while_enabled"


def _client(session, region):
    return session.client("config", region_name=region, config=RETRY_CONFIG)


def _recorder_flag(status):
    if status is None:
        return "UNKNOWN", None
    if not status.get("recording"):
        return "STALE (not recording - not billing)", None
    return "ACTIVE (recording - bills per configuration item)", BILLS_WHILE_ENABLED


def _scope(recorder):
    group = recorder.get("recordingGroup") or {}
    strategy = (group.get("recordingStrategy") or {}).get("useOnly", "")
    if group.get("allSupported") or strategy == "ALL_SUPPORTED_RESOURCE_TYPES":
        return "all resource types"
    if strategy == "EXCLUSION_BY_RESOURCE_TYPES":
        excluded = (group.get("exclusionByResourceTypes") or {}).get("resourceTypes") or []
        return f"all but {len(excluded)} resource type(s)"
    return f"{len(group.get('resourceTypes') or [])} resource type(s)"


def collect_config_recorders(session, region):
    """The customer-managed recorder: with no name given, AWS returns only that one.
    Service-linked recorders belong to another service and are not listed."""
    client = _client(session, region)
    resp = safe_call(client.describe_configuration_recorders, capability=coverage.INVENTORY)
    if "__error__" in resp:
        return [error_row("ConfigRecorder", region, "ERROR", resp["__error__"])]
    recorders = resp.get("ConfigurationRecorders") or []
    if not recorders:
        return []
    status_resp = safe_call(client.describe_configuration_recorder_status)
    statuses = {} if "__error__" in status_resp else {
        s.get("name"): s for s in status_resp.get("ConfigurationRecordersStatus") or []}
    if "__error__" in status_resp:
        logger.warning("Config recorders in %s: recording status unreadable, flag UNKNOWN - %s",
                       region, status_resp["__error__"])
    channels = safe_call(client.describe_delivery_channels)
    if "__error__" in channels:
        logger.warning("Config delivery channels in %s unreadable, bucket not shown - %s",
                       region, channels["__error__"])
    buckets = [] if "__error__" in channels else [
        c["s3BucketName"] for c in channels.get("DeliveryChannels") or [] if c.get("s3BucketName")]
    rows = []
    for recorder in recorders:
        name = recorder.get("name", "default")
        status = statuses.get(name)
        flag, hint = _recorder_flag(status)
        started = (status or {}).get("lastStartTime")
        notes = ("Bills per configuration item recorded while recording is on; a stopped "
                 "recorder is free. No usage signal: the recording state is the evidence. ")
        if started:
            notes += f"Recording last started {started:%Y-%m-%d}. "
        if "__error__" in status_resp:
            notes += f"Recording status could not be read: {status_resp['__error__']} "
        row = new_row(
            "ConfigRecorder", region, name, name, None, None, None, True,
            flag, notes, description=join_nonempty([
                _scope(recorder), (recorder.get("recordingMode") or {}).get("recordingFrequency", ""),
                recorder.get("servicePrincipal", ""),
                f"delivers to s3://{buckets[0]}" if buckets else ""], ", "),
            cost_hint=hint, arn=recorder.get("arn", ""))
        raw_capture.record("ConfigRecorder", region, name, recorder)
        rows.append(row)
    return rows


def _rule_flag(rule):
    state = rule.get("ConfigRuleState", "")
    if state.startswith("DELETING"):
        return "STALE (being deleted)", None
    return "ACTIVE (bills per evaluation while the recorder runs)", BILLS_WHILE_ENABLED


def collect_config_rules(session, region):
    client = _client(session, region)
    rules, page_error = paged(client, "describe_config_rules", "ConfigRules", service="ConfigRule")
    if page_error and not rules:
        return [error_row("ConfigRule", region, "ERROR", page_error)]
    rows = []
    for rule in rules:
        name = rule["ConfigRuleName"]
        flag, hint = _rule_flag(rule)
        created_by = rule.get("CreatedBy", "")
        notes = ("Each evaluation is billed under AWS Config; the region's Config bill is shared "
                 "evenly across its recorder and rules, as an estimate. ")
        if created_by:
            notes += f"Created by {created_by}: remove it there, not here. "
        source = rule.get("Source") or {}
        row = new_row(
            "ConfigRule", region, name, name, None, None, None, True, flag, notes,
            description=join_nonempty([rule.get("Description", ""), source.get("Owner", ""),
                                       source.get("SourceIdentifier", "")], ", "),
            cost_hint=hint, arn=rule.get("ConfigRuleArn", ""))
        raw_capture.record("ConfigRule", region, name, rule)
        rows.append(row)
    return rows
