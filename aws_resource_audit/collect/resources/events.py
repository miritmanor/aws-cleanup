"""EventBridge rules and Scheduler schedules: what makes something run when nobody calls
it. A schedule states expected cadence, never evidence that anything ran."""

import logging


from ..arns import arn_scope, probable_resource_id_from_arn
from ..calls import paged, safe_call
from ... import coverage
from ...collect import raw_capture
from ...config import RETRY_CONFIG
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago
from ... import activity
from ...text import join_nonempty

logger = logging.getLogger(__name__)


def _target_scope(arn):
    """(id, service, account, region) for a target ARN, or None if not inventoried.
    Returns instead of adding the edge so each conn_type stays a literal (tested via ast)."""
    target_id, target_service = probable_resource_id_from_arn(arn)
    if not target_id:
        return None
    account, region = arn_scope(arn)
    return target_id, target_service, account, region


def collect_eventbridge_rules(session, region):
    """Every rule on every event bus; custom buses hold the app-specific ones."""
    events = session.client("events", region_name=region, config=RETRY_CONFIG)
    rows = []

    buses, error = paged(events, "list_event_buses", "EventBuses",
                         service="EventBridgeRule")
    if error and not buses:
        return [error_row("EventBridgeRule", region, "ERROR", error)]

    for bus in buses:
        bus_name = bus.get("Name") or "default"
        if bus_name != "default":
            # The default bus exists in every region and nobody created it; a custom one is a choice.
            rows.append(new_row(
                "EventBridgeBus", region, bus_name, bus_name, bus.get("CreationTime"), "", None, True,
                "UNKNOWN", "Events published to a custom bus bill per million; an idle bus costs "
                "nothing. Whether it is used shows on its rules' targets.",
                description=(bus.get("Description") or "")[:70], arn=bus.get("Arn", "")))
            raw_capture.record("EventBridgeBus", region, bus_name, bus)
        rules, rule_error = paged(events, "list_rules", "Rules",
                                  service="EventBridgeRule",
                                  EventBusName=bus_name)
        if rule_error and not rules:
            rows.append(error_row("EventBridgeRule", region, bus_name, rule_error))
            continue

        for rule in rules:
            name = rule.get("Name")
            if not name:
                continue
            state = rule.get("State") or "UNKNOWN"
            schedule = rule.get("ScheduleExpression") or ""
            pattern = bool(rule.get("EventPattern"))
            description = join_nonempty([
                f"bus {bus_name}",
                schedule or ("event pattern" if pattern else ""),
                state,
            ])
            # A rule publishes no usage metric of its own. Saying so beats
            # implying anything from its age.
            evidence = activity.unavailable(
                "EventBridge publishes no per-rule usage signal; whether the "
                "rule fired is only visible on what it targets.")
            rows.append(new_row(
                "EventBridgeRule", region, name, name,
                None, "", None, True,
                # DISABLED is a fact about the rule: neither unused nor unknown.
                "STALE (DISABLED)" if state == "DISABLED" else "UNKNOWN",
                "A rule states when something SHOULD run. It is not evidence "
                "that anything ran - see the target's own activity."
                + (f" Schedule: {schedule}." if schedule else ""),
                description=description, activity=evidence,
                arn=rule.get("Arn", ""),
            ))
            raw_capture.record("EventBridgeRule", region, name, rule)
            if bus_name != "default":
                add_edge(rows[-1], bus_name, "listens on bus", "rule EventBusName",
                         conn_type="eventbridgerule.eventbridgebus.membership",
                         target_service="EventBridgeBus")

            targets, target_error = paged(
                events, "list_targets_by_rule", "Targets",
                capability=coverage.DEPENDENCIES, service="EventBridgeRule",
                Rule=name, EventBusName=bus_name)
            if target_error and not targets:
                continue
            for target in targets:
                scope = _target_scope(target.get("Arn") or "")
                if scope:
                    target_id, target_service, account, target_region = scope
                    add_edge(rows[-1], target_id, "triggers",
                             f"EventBridge rule target on bus {bus_name}"
                             + (f" ({schedule})" if schedule else ""),
                             conn_type="eventbridgerule.any.target",
                             target_service=target_service,
                             target_arn=target.get("Arn"),
                             target_account=account, target_region=target_region)
                role_arn = target.get("RoleArn")
                if role_arn:
                    role_id, _svc = probable_resource_id_from_arn(role_arn)
                    if role_id:
                        add_edge(rows[-1], role_id, "runs as",
                                 "EventBridge rule target RoleArn",
                                 conn_type="eventbridgerule.iamrole.target-role",
                                 target_service="IAMRole")
    return rows


def collect_scheduler_schedules(session, region):
    """EventBridge Scheduler across every schedule group; separate API from rules."""
    scheduler = session.client("scheduler", region_name=region, config=RETRY_CONFIG)
    rows = []

    schedules, error = paged(scheduler, "list_schedules", "Schedules",
                             service="EventBridgeSchedule")
    if error and not schedules:
        # "Not available here" and "denied" look alike; the ledger tells them apart.
        return [error_row("EventBridgeSchedule", region, "ERROR", error)]

    for summary in schedules:
        name = summary.get("Name")
        if not name:
            continue
        group = summary.get("GroupName") or "default"
        detail = safe_call(scheduler.get_schedule,
                           capability=coverage.CONFIGURATION,
                           Name=name, GroupName=group)
        if "__error__" in detail:
            rows.append(error_row("EventBridgeSchedule", region, name,
                                  detail["__error__"]))
            continue

        state = detail.get("State") or "UNKNOWN"
        expression = detail.get("ScheduleExpression") or ""
        timezone = detail.get("ScheduleExpressionTimezone") or ""
        created = detail.get("CreationDate")
        target = detail.get("Target") or {}
        retry = (target.get("RetryPolicy") or {})
        dead_letter = (target.get("DeadLetterConfig") or {}).get("Arn")

        evidence = activity.unavailable(
            "EventBridge Scheduler publishes no per-schedule usage signal; "
            "whether it fired is only visible on what it targets.")
        rows.append(new_row(
            "EventBridgeSchedule", region, name, name,
            created, "", days_ago(created), True,
            "STALE (DISABLED)" if state == "DISABLED" else "UNKNOWN",
            join_nonempty([
                "A schedule states expected cadence, not that anything ran.",
                f"Retries: {retry.get('MaximumRetryAttempts')}."
                if retry.get("MaximumRetryAttempts") is not None else "",
                "Has a dead-letter queue." if dead_letter else "",
            ]),
            description=join_nonempty([
                f"group {group}", expression, timezone, state]),
            activity=evidence, arn=detail.get("Arn", ""),
        ))
        raw_capture.record("EventBridgeSchedule", region, name, detail)

        scope = _target_scope(target.get("Arn") or "")
        if scope:
            target_id, target_service, account, target_region = scope
            add_edge(rows[-1], target_id, "triggers",
                     f"EventBridge schedule target ({expression or 'no expression'})",
                     conn_type="eventbridgeschedule.any.target",
                     target_service=target_service, target_arn=target.get("Arn"),
                     target_account=account, target_region=target_region)
        dlq_scope = _target_scope(dead_letter) if dead_letter else None
        if dlq_scope:
            target_id, target_service, account, target_region = dlq_scope
            add_edge(rows[-1], target_id, "dead-letters to",
                     "EventBridge schedule DeadLetterConfig",
                     conn_type="eventbridgeschedule.any.target",
                     target_service=target_service, target_arn=dead_letter,
                     target_account=account, target_region=target_region)
        role_arn = target.get("RoleArn")
        if role_arn:
            role_id, _svc = probable_resource_id_from_arn(role_arn)
            if role_id:
                add_edge(rows[-1], role_id, "runs as",
                         "EventBridge schedule target RoleArn",
                         conn_type="eventbridgeschedule.iamrole.target-role",
                         target_service="IAMRole")
    return rows
