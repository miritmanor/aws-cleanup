"""CloudTrail, CloudWatch logs and alarms, X-Ray."""

from datetime import datetime, timezone

from botocore.exceptions import BotoCoreError, ClientError

from ..arns import probable_resource_id_from_arn
from ..cloudwatch import cw_note
from ..uri_refs import infer_log_group_source
from ...config import RETRY_CONFIG
from ...staleness import days_ago, flag_stale
from ...collect.calls import safe_call
from ...text import join_nonempty
from ...rows import add_edge, error_row, new_row
from ...collect import raw_capture


def collect_cloudtrail_trails(session, region):
    ct = session.client("cloudtrail", region_name=region, config=RETRY_CONFIG)
    resp = safe_call(ct.describe_trails, includeShadowTrails=False)
    if "__error__" in resp:
        return [error_row("CloudTrailTrail", region, "ERROR", resp["__error__"])]
    rows = []
    for trail in resp.get("trailList", []):
        home_region = trail.get("HomeRegion", "")
        if home_region and home_region != region:
            continue  # multi-region trails show up in every region's list; only report once, from their home region
        name = trail["Name"]
        status = safe_call(ct.get_trail_status, Name=trail["TrailARN"])
        is_logging = status.get("IsLogging") if "__error__" not in status else None
        last_delivery = status.get("LatestDeliveryTime") if "__error__" not in status else None
        ref_days = days_ago(last_delivery)
        # Set only when the trail also delivers to CloudWatch Logs.
        log_group_arn = trail.get("CloudWatchLogsLogGroupArn")
        log_group_id, log_group_service = probable_resource_id_from_arn(log_group_arn) if log_group_arn else (None, None)
        row = new_row(
            "CloudTrailTrail", region, trail.get("TrailARN", name), name,
            "", last_delivery, ref_days, last_delivery is None,
            "STALE (NOT LOGGING - review or delete)" if is_logging is False else flag_stale(ref_days, None, last_delivery is None),
            f"multi-region={trail.get('IsMultiRegionTrail', False)}; LatestDeliveryTime (last log file delivered to S3) used as activity signal",
            arn=trail.get("TrailARN", ""),
        )
        add_edge(row, log_group_id, "delivers logs to",
                 "CloudTrail DescribeTrails CloudWatchLogsLogGroupArn",
                 conn_type="cloudtrail.loggroup.arn", target_service=log_group_service)
        rows.append(row)
        raw_capture.record("CloudTrailTrail", region,
                           trail.get("TrailARN", name), trail)
    return rows


def collect_cloudwatch_log_groups(session, region):
    """Log groups: last event, source inferred from AWS naming conventions, and
    subscription-filter destinations. No extra IAM permissions needed."""
    logs = session.client("logs", region_name=region, config=RETRY_CONFIG)
    rows = []
    try:
        paginator = logs.get_paginator("describe_log_groups")
        for page in paginator.paginate():
            for lg in page["logGroups"]:
                name = lg["logGroupName"]
                created = datetime.fromtimestamp(lg["creationTime"] / 1000, tz=timezone.utc) if lg.get("creationTime") else None
                stored_bytes = lg.get("storedBytes", 0)
                streams = safe_call(
                    logs.describe_log_streams, logGroupName=name,
                    orderBy="LastEventTime", descending=True, limit=1,
                )
                last_event = None
                cw_error = streams.get("__error__")
                if not cw_error and streams.get("logStreams"):
                    ts = streams["logStreams"][0].get("lastEventTimestamp")
                    if ts:
                        last_event = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                ref_days = days_ago(last_event) if last_event else days_ago(created)
                retention = lg.get("retentionInDays", "never expires")

                source_label, embedded_id, group_match_id, group_match_service = infer_log_group_source(name)

                sub_resp = safe_call(logs.describe_subscription_filters, logGroupName=name)
                destination_arns = (
                    [f["destinationArn"] for f in sub_resp.get("subscriptionFilters", []) if f.get("destinationArn")]
                    if "__error__" not in sub_resp else []
                )

                connections_parts = []
                if source_label and not group_match_id:
                    # Recognized but not AWS-enforced naming (or an unscanned
                    # service): show it, but never turn it into a grouping edge.
                    connections_parts.append(
                        f"likely source: {source_label}" + (f" ({embedded_id})" if embedded_id else ""))

                notes = cw_note(
                    "Last log event timestamp on the most recent stream used as activity signal; "
                    "log groups incur ongoing storage cost", cw_error)
                notes += (f" Source inferred from AWS's log group naming convention: {source_label}."
                          if source_label else
                          " Log group name doesn't match any recognized AWS service naming convention - "
                          "likely custom-created by application code (logs:CreateLogGroup); check its most "
                          "recent log stream's content directly in the console to see what's writing to it.")

                row = new_row(
                    "CloudWatchLogGroup", region, name, name,
                    created, last_event, ref_days, last_event is None,
                    flag_stale(ref_days if last_event else None, days_ago(created), last_event is None),
                    notes,
                    description=f"{stored_bytes} bytes stored",
                    connections=join_nonempty(connections_parts),
                    arn=lg.get("arn", ""),
                )
                raw_capture.record("CloudWatchLogGroup", region, name, lg)
                # Only AWS-enforced names become edges; log groups outlive their source,
                # so a missing one is not reported as DANGLING.
                add_edge(row, group_match_id, "collects logs from",
                         f"AWS-enforced log group naming ({source_label})",
                         conn_type="loggroup.any.name-convention", assert_exists=False,
                         target_service=group_match_service,
                         target_region=region)
                for arn in destination_arns:
                    dest_id, dest_service = probable_resource_id_from_arn(arn)
                    add_edge(row, dest_id, "forwards logs to",
                             "CloudWatch Logs subscription filter destinationArn",
                             conn_type="loggroup.any.subscription-filter", target_service=dest_service)
                rows.append(row)
    except (ClientError, BotoCoreError) as e:
        rows.append(error_row("CloudWatchLogGroup", region, "ERROR", str(e)))
    return rows


def collect_cloudwatch_alarms(session, region):
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    try:
        paginator = cw.get_paginator("describe_alarms")
        for page in paginator.paginate():
            for alarm in page.get("MetricAlarms", []) + page.get("CompositeAlarms", []):
                name = alarm["AlarmName"]
                state = alarm.get("StateValue", "")
                updated = alarm.get("StateUpdatedTimestamp")
                ref_days = days_ago(updated)
                rows.append(new_row(
                    "CloudWatchAlarm", region, alarm.get("AlarmArn", name), name,
                    "", updated, ref_days, True,
                    "STALE (INSUFFICIENT_DATA - target resource may be gone)" if state == "INSUFFICIENT_DATA" else flag_stale(ref_days, None, True),
                    "StateUpdatedTimestamp used as activity proxy; alarms don't cost money but INSUFFICIENT_DATA often means the monitored resource no longer exists",
                    description=alarm.get("AlarmDescription", "") or "",
                    arn=alarm.get("AlarmArn", ""),
                ))
                raw_capture.record("CloudWatchAlarm", region,
                                   alarm.get("AlarmArn", name), alarm)
    except (ClientError, BotoCoreError) as e:
        rows.append(error_row("CloudWatchAlarm", region, "ERROR", str(e)))
    return rows


def collect_xray_config(session, region):
    xray = session.client("xray", region_name=region, config=RETRY_CONFIG)
    rows = []

    groups_resp = safe_call(xray.get_groups)
    if "__error__" in groups_resp:
        rows.append(error_row("XRayGroup", region, "ERROR", groups_resp["__error__"]))
    else:
        for g in groups_resp.get("Groups", []):
            # AWS creates a "Default" group in every region; it is free and
            # cannot be deleted, like the Default sampling rule below.
            if g.get("GroupName") == "Default":
                continue
            rows.append(new_row(
                "XRayGroup", region, g.get("GroupARN", g["GroupName"]), g["GroupName"],
                "", "", None, True, "UNKNOWN",
                "X-Ray exposes no last-used signal for groups; traces themselves auto-expire "
                "after 30 days regardless of this config - check if the filter's target service still exists",
                arn=g.get("GroupARN", ""),
            ))
            raw_capture.record("XRayGroup", region,
                               g.get("GroupARN", g["GroupName"]), g)

    rules_resp = safe_call(xray.get_sampling_rules)
    if "__error__" in rules_resp:
        rows.append(error_row("XRaySamplingRule", region, "ERROR", rules_resp["__error__"]))
    else:
        for r in rules_resp.get("SamplingRuleRecords", []):
            rule = r.get("SamplingRule", {})
            if rule.get("RuleName") == "Default":
                continue
            modified = r.get("ModifiedAt")
            rows.append(new_row(
                "XRaySamplingRule", region, rule.get("RuleARN", rule.get("RuleName", "")),
                rule.get("RuleName", ""),
                r.get("CreatedAt"), modified, days_ago(modified), True, "UNKNOWN",
                "X-Ray exposes no true last-used signal for sampling rules (only last-modified); "
                "check whether the associated service still exists",
            ))
            raw_capture.record("XRaySamplingRule", region,
                               rule.get("RuleARN", rule.get("RuleName", "")), r)
    return rows
